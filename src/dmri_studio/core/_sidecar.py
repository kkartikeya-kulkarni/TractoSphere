"""Bounded metadata interpretation for an optional diffusion JSON sidecar.

This module does not implement BIDS discovery or inheritance. Unknown keys and
invalid optional acquisition values are retained unchanged; the checks describe
which values the application can safely interpret. Call through DatasetLoader.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .validation import CheckResult


class SidecarParseError(ValueError):
    """Indicate a malformed JSON document without discarding its file context."""


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate keys at every object depth rather than silently replace."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SidecarParseError(f"Duplicate JSON key: {key!r}.")
        result[key] = value
    return result


def _constant(value: str) -> None:
    """Reject JavaScript-style NaN/Infinity values, which are not JSON numbers."""
    raise SidecarParseError(f"Nonstandard JSON numeric literal: {value}.")


def _finite_tree(value: Any) -> None:
    """Catch finite-looking JSON exponents that overflow Python's float parser."""
    if isinstance(value, float) and not math.isfinite(value):
        raise SidecarParseError("A JSON number overflows the supported finite range.")
    if isinstance(value, dict):
        for child in value.values():
            _finite_tree(child)
    elif isinstance(value, list):
        for child in value:
            _finite_tree(child)


def _positive_scalar(value: Any) -> bool:
    """Distinguish positive scalar timing values from bools and arrays."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return value > 0 and math.isfinite(value)
    except OverflowError:
        return False


def inspect_sidecar(path: Path | None) -> tuple[dict[str, Any], tuple[CheckResult, ...]]:
    """Parse one optional JSON object and run J01–J05 without rewriting values."""
    checks: list[CheckResult] = []
    source = str(path) if path is not None else None
    if path is None:
        checks.append(CheckResult("J01", "info", "Optional JSON sidecar not provided."))
        checks.extend(
            CheckResult(code, "skipped", "No JSON sidecar was selected.")
            for code in ("J02", "J03", "J04", "J05")
        )
        return {}, tuple(checks)
    try:
        # utf-8-sig accepts an optional BOM without dropping any document fields.
        with path.open("r", encoding="utf-8-sig") as stream:
            values = json.load(stream, object_pairs_hook=_object_pairs, parse_constant=_constant)
        if not isinstance(values, dict):
            raise SidecarParseError("The top-level JSON value must be an object.")
        _finite_tree(values)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        checks.append(
            CheckResult(
                "J01",
                "fail",
                f"Cannot parse JSON sidecar: {exc}",
                source=source,
                remediation="Correct the JSON document or remove the optional selection.",
            )
        )
        checks.extend(
            CheckResult(code, "skipped", "JSON parsing failed.", source=source)
            for code in ("J02", "J03", "J04", "J05")
        )
        return {}, tuple(checks)
    checks.append(
        CheckResult("J01", "pass", "JSON object parsed; all fields retained.", source=source)
    )

    if "PhaseEncodingDirection" not in values:
        checks.append(
            CheckResult("J02", "info", "PhaseEncodingDirection not provided.", source=source)
        )
    else:
        value = values["PhaseEncodingDirection"]
        valid = isinstance(value, str) and value in {"i", "i-", "j", "j-", "k", "k-"}
        checks.append(
            CheckResult(
                "J02",
                "pass" if valid else "warn",
                "PhaseEncodingDirection is a valid voxel-axis label."
                if valid
                else "PhaseEncodingDirection is invalid and will remain uninterpreted.",
                source=source,
                observed=value,
                expected=["i", "i-", "j", "j-", "k", "k-"],
            )
        )

    valid_times: dict[str, float] = {}
    for name in ("TotalReadoutTime", "EffectiveEchoSpacing", "EchoTime", "RepetitionTime"):
        if name not in values:
            checks.append(CheckResult("J03", "info", f"{name} not provided.", source=source))
            continue
        value = values[name]
        valid = _positive_scalar(value)
        if valid:
            valid_times[name] = float(value)
        checks.append(
            CheckResult(
                "J03",
                "pass" if valid else "warn",
                f"{name} is a positive scalar in seconds."
                if valid
                else f"{name} is not a positive finite scalar; retained but uninterpreted.",
                source=source,
                observed=value,
                expected="finite scalar > 0 seconds",
            )
        )

    matrix = values.get("ReconMatrixPE")
    valid_matrix = isinstance(matrix, int) and not isinstance(matrix, bool) and matrix > 1
    if "ReconMatrixPE" not in values:
        checks.append(
            CheckResult(
                "J04",
                "info",
                "ReconMatrixPE not provided; readout relation not checked.",
                source=source,
            )
        )
    elif not valid_matrix:
        checks.append(
            CheckResult(
                "J04",
                "warn",
                "ReconMatrixPE must be an integer greater than one.",
                source=source,
                observed=matrix,
                expected="integer > 1, excluding bool",
            )
        )
    elif {"EffectiveEchoSpacing", "TotalReadoutTime"} <= valid_times.keys():
        try:
            expected = valid_times["EffectiveEchoSpacing"] * (matrix - 1)
            consistent = math.isfinite(expected) and abs(
                valid_times["TotalReadoutTime"] - expected
            ) <= max(1e-6, 0.01 * expected)
        except OverflowError:
            expected, consistent = None, False
        checks.append(
            CheckResult(
                "J04",
                "pass" if consistent else "warn",
                "Readout timing relation agrees within tolerance."
                if consistent
                else "TotalReadoutTime disagrees with EffectiveEchoSpacing × (ReconMatrixPE − 1).",
                source=source,
                observed=valid_times["TotalReadoutTime"],
                expected=expected
                if expected is None or math.isfinite(expected)
                else "finite expected timing",
            )
        )
    else:
        checks.append(
            CheckResult(
                "J04",
                "info",
                "ReconMatrixPE is valid; valid scalar readout times are unavailable.",
                source=source,
            )
        )

    if {"EchoTime", "RepetitionTime"} <= valid_times.keys():
        plausible = valid_times["EchoTime"] < valid_times["RepetitionTime"]
        checks.append(
            CheckResult(
                "J05",
                "pass" if plausible else "warn",
                "EchoTime is less than RepetitionTime."
                if plausible
                else "EchoTime is not less than RepetitionTime; verify this acquisition metadata.",
                source=source,
            )
        )
    else:
        checks.append(
            CheckResult(
                "J05",
                "info",
                "TE/TR plausibility cannot be checked without valid scalar values.",
                source=source,
            )
        )
    checks.append(
        CheckResult(
            "J05",
            "info",
            "Only the documented scalar metadata fields were interpreted; other JSON fields are unvalidated.",
            source=source,
            observed={
                "correction_metadata_missing": [
                    name
                    for name in ("PhaseEncodingDirection", "TotalReadoutTime")
                    if name not in values
                ]
            },
        )
    )
    return values, tuple(checks)
