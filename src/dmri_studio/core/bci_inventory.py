"""Read-only, sequential inventory of explicitly listed FALCON H1 recordings."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np

from dmri_studio.core.bci import FEATURES, PERIOD_S, inspect_falcon_h1
from dmri_studio.core.dataset import SourceFingerprint

MANIFEST_SCHEMA = "falcon-h1-inventory-manifest-v1"
REPORT_SCHEMA = "falcon-h1-inventory-report-v1"
IDENTITY_FIELDS = ("participant", "session_day", "recording_set", "benchmark_partition")
SUPPORTED_BINNING = {
    "period_s": PERIOD_S,
    "timestamp_role": "bin_end",
    "analysis_windows": "all_samples",
    "eval_mask": "preserve_raw",
}
SUPPORTED_MEASUREMENT = {
    "unit_rate_denominator": "all_analyzed_exposure_s",
    "rate_quantiles": [0.0, 0.25, 0.5, 0.75, 1.0],
    "nonfinite_covariates": "count_by_feature_and_raw_mask_status",
    "trial_boundaries": "TrialNum_increase",
}
PROVENANCES = {"nwb_metadata", "documented_upstream_mapping", "explicit_user_declaration"}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _nwb_metadata(path: Path) -> dict[str, str | None]:
    """Read only scalar identity evidence; never derive a real calendar date."""
    import h5py

    found: dict[str, str | None] = {}
    with h5py.File(path, "r") as root:
        for key, dataset_path in (
            ("participant", "general/subject/subject_id"),
            ("recording_set", "identifier"),
            ("session_start_time", "session_start_time"),
        ):
            current: Any = root
            for part in dataset_path.split("/"):
                if not isinstance(current, h5py.Group) or part not in current:
                    current = None
                    break
                if not isinstance(current.get(part, getlink=True), h5py.HardLink):
                    raise ValueError(f"NWB identity field uses a linked object: {dataset_path}")
                current = current[part]
            if current is None:
                found[key] = None
            elif not isinstance(current, h5py.Dataset) or current.shape != ():
                raise ValueError(f"NWB identity field must be scalar: {dataset_path}")
            else:
                found[key] = _text(current[()]).strip() or None
    start = found.pop("session_start_time")
    if start is not None and not re.match(r"^\d{4}-\d{2}-\d{2}T", start):
        raise ValueError("NWB session_start_time is not an ISO-like timestamp")
    found["session_day"] = start[:10] if start else None
    found["benchmark_partition"] = None
    return found


def _identity(declared: Any, observed: dict[str, str | None]) -> dict[str, Any]:
    if not isinstance(declared, dict) or set(declared) != set(IDENTITY_FIELDS):
        raise ValueError(f"identity must explicitly contain {', '.join(IDENTITY_FIELDS)}")
    result = {}
    for field in IDENTITY_FIELDS:
        item = declared[field]
        from_nwb = observed[field]
        evidence_field = {
            "participant": "general/subject/subject_id",
            "session_day": "session_start_time date label (deidentified)",
            "recording_set": "identifier",
            "benchmark_partition": None,
        }[field]
        if item is None:
            result[field] = (
                {"value": from_nwb, "provenance": "nwb_metadata", "evidence": evidence_field}
                if from_nwb is not None
                else None
            )
            continue
        if not isinstance(item, dict) or set(item) != {"value", "provenance", "evidence"}:
            raise ValueError(f"{field} requires value, provenance and evidence, or null")
        value, provenance, evidence = item["value"], item["provenance"], item["evidence"]
        if not isinstance(value, str) or not value.strip() or provenance not in PROVENANCES:
            raise ValueError(f"{field} has an invalid value or provenance")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError(f"{field} requires a nonempty evidence reference")
        if provenance == "nwb_metadata" and (from_nwb is None or value != from_nwb):
            raise ValueError(f"{field} contradicts or is absent from NWB metadata")
        if from_nwb is not None and value != from_nwb:
            raise ValueError(f"{field} contradicts NWB metadata ({from_nwb})")
        if field == "benchmark_partition" and provenance == "nwb_metadata":
            raise ValueError("benchmark_partition is not established by this NWB metadata")
        result[field] = dict(value=value, provenance=provenance, evidence=evidence)
    return result


def _binning(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(SUPPORTED_BINNING):
        raise ValueError(
            "binning must explicitly declare period_s, timestamp_role, analysis_windows and eval_mask"
        )
    if not isinstance(value["period_s"], (int, float)) or not math.isclose(
        value["period_s"], PERIOD_S, rel_tol=0, abs_tol=1e-12
    ):
        raise ValueError("only the validated 20 ms H1 binning configuration is supported")
    for key in ("timestamp_role", "analysis_windows", "eval_mask"):
        if value[key] != SUPPORTED_BINNING[key]:
            raise ValueError(f"unsupported H1 binning configuration: {key}")
    return dict(SUPPORTED_BINNING)


def _measurement(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value != SUPPORTED_MEASUREMENT:
        raise ValueError(
            "only the documented descriptive H1 measurement configuration is supported"
        )
    return {
        **SUPPORTED_MEASUREMENT,
        "rate_quantiles": list(SUPPORTED_MEASUREMENT["rate_quantiles"]),
    }


def _fraction(count: int, denominator: int) -> float | None:
    return count / denominator if denominator else None


def _measurements(report: dict[str, Any]) -> dict[str, Any]:
    samples = report["time"]["sample_count"]
    mask = report["evaluation_mask"]
    rates = np.asarray([unit["rate_hz"] for unit in report["units"]["per_unit"]], dtype=float)
    missing = {}
    for kind in ("position", "velocity"):
        total = report["kinematics"][f"{kind}_nonfinite_by_feature"]
        by_mask = report["kinematics"][f"{kind}_nonfinite_by_raw_mask"]
        missing[kind] = {
            feature: {
                "count": total[index],
                "fraction_of_all_samples": _fraction(total[index], samples),
                "raw_mask_true_count": by_mask["true"][index],
                "fraction_of_raw_mask_true_samples": _fraction(
                    by_mask["true"][index], mask["true_count"]
                ),
                "raw_mask_false_count": by_mask["false"][index],
                "fraction_of_raw_mask_false_samples": _fraction(
                    by_mask["false"][index], mask["false_count"]
                ),
            }
            for index, feature in enumerate(FEATURES)
        }
    return {
        "samples": samples,
        "trial_segment_count": report["trials"]["count"],
        "analyzed_exposure_s": report["time"]["analyzed_exposure_s"],
        "recorded_units": report["units"]["count"],
        "observed_spikes_in_windows": sum(
            unit["observed_spikes"] for unit in report["units"]["per_unit"]
        ),
        "zero_observed_spike_units": report["units"]["zero_observed_unit_count"],
        "unit_activity_rate_hz": {
            "denominator": "analyzed exposure seconds, all raw-mask statuses",
            "min": float(np.min(rates)),
            "q25": float(np.quantile(rates, 0.25)),
            "median": float(np.median(rates)),
            "q75": float(np.quantile(rates, 0.75)),
            "max": float(np.max(rates)),
        },
        "represented_timing": {
            key: report["time"][key]
            for key in ("gap_count", "gap_duration_s", "short_interval_count")
        },
        "raw_eval_mask": {
            "true_count": mask["true_count"],
            "false_count": mask["false_count"],
            "true_fraction_of_samples": _fraction(mask["true_count"], samples),
            "false_fraction_of_samples": _fraction(mask["false_count"], samples),
        },
        "nonfinite_covariates": missing,
    }


def inspect_h1_inventory(manifest_path: str | Path) -> dict[str, Any]:
    """Inspect each listed source sequentially; report partial failures explicitly."""
    path = Path(manifest_path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("Select an existing inventory manifest JSON file")
    before = SourceFingerprint.capture(path)
    if before.size > 4_000_000:
        raise ValueError("inventory manifest exceeds the 4 MB read limit")
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"Expected {MANIFEST_SCHEMA}")
    config = _binning(manifest.get("binning"))
    measurement = _measurement(manifest.get("measurement"))
    entries = manifest.get("recordings")
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest recordings must be a nonempty list")
    if len(entries) > 512:
        raise ValueError("inventory supports at most 512 recordings per run")
    ids = [item.get("entry_id") if isinstance(item, dict) else None for item in entries]
    if any(not isinstance(item, str) or not item.strip() for item in ids) or len(set(ids)) != len(
        ids
    ):
        raise ValueError("each recording requires a unique nonempty entry_id")
    if not before.unchanged(path):
        raise ValueError("inventory manifest changed while reading")

    records = []
    for item in entries:
        row: dict[str, Any] = {"entry_id": item["entry_id"], "status": "ok", "errors": []}
        records.append(row)
        row["declared_identity"] = item.get("identity")
        try:
            source_text = item["path"]
            expected = item["sha256"]
            if not isinstance(source_text, str) or not source_text.strip():
                raise ValueError("recording path must be a nonempty string")
            if not isinstance(expected, str) or not _DIGEST.fullmatch(expected):
                raise ValueError("recording sha256 must be 64 lowercase hex characters")
            candidate = (path.parent / source_text).expanduser().resolve(strict=False)
            row["source"] = {"path": str(candidate), "expected_sha256": expected}
            source = candidate.resolve(strict=True)
            row["source"] = {"path": str(source), "expected_sha256": expected}
            row["binning"] = _binning(item.get("binning", config))
            row["measurement_configuration"] = _measurement(item.get("measurement", measurement))
            source_before = SourceFingerprint.capture(source)
            inspection = inspect_falcon_h1(source)
            row["inspection"] = inspection
            if inspection["source"]["sha256"] != expected:
                raise ValueError("recording SHA-256 differs from manifest")
            observed = _nwb_metadata(source)
            if not source_before.unchanged(source):
                raise ValueError("NWB source changed while reading identity metadata")
            row["nwb_identity_observations"] = observed
            row["identity"] = _identity(item.get("identity"), observed)
            row["dataset"] = item.get("dataset")
            row["measurements"] = _measurements(inspection)
        except (KeyError, OSError, ValueError, RuntimeError, TypeError) as exc:
            row["status"] = "error"
            row["errors"].append(str(exc))

    # Content and path checks are separate from logical session identity.
    for key, label in (("path", "repeated_path"), ("sha256", "duplicate_content")):
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in records:
            source = row.get("inspection", {}).get("source", row.get("source", {}))
            value = source.get(key)
            if value is not None:
                groups.setdefault(value, []).append(row)
        for group in groups.values():
            if len(group) > 1:
                for row in group:
                    row["errors"].append(
                        f"{label}: {', '.join(other['entry_id'] for other in group if other is not row)}"
                    )
                    row["status"] = "error"
    identities: dict[tuple[str | None, ...], list[dict[str, Any]]] = {}
    for row in records:
        identity = row.get("identity")
        if identity is None:
            continue
        values = tuple(
            identity[field]["value"] if identity[field] else None for field in IDENTITY_FIELDS
        )
        if values[1] and values[2]:
            identities.setdefault(values, []).append(row)
    for group in identities.values():
        digests = {row.get("inspection", {}).get("source", {}).get("sha256") for row in group}
        if len(group) > 1 and len(digests) > 1:
            for row in group:
                qualifier = (
                    "identity_collision"
                    if all(row["identity"][field] is not None for field in IDENTITY_FIELDS)
                    else "possible_identity_collision"
                )
                row["errors"].append(
                    f"{qualifier}: different content has the same known recording identity"
                )
                row["status"] = "error"

    successful = [row for row in records if row["status"] == "ok"]
    comparability = _comparability(successful, len(records) == len(successful))
    return {
        "schema": REPORT_SCHEMA,
        "task": "FALCON H1 modern NWB",
        "manifest": {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()},
        "binning": config,
        "measurement_configuration": measurement,
        "complete": len(successful) == len(records),
        "recordings": records,
        "comparability": comparability,
        "scientific_acceptance": "unassigned",
    }


def _comparability(records: list[dict[str, Any]], complete: bool) -> dict[str, Any]:
    reasons = []
    if not complete:
        reasons.append("one or more recording inspections failed")
    if len(records) < 2:
        reasons.append("at least two distinct successful recordings are required")
    if records:
        first = records[0]
        for row in records[1:]:
            if row["inspection"]["features"] != first["inspection"]["features"]:
                reasons.append("feature labels/order or storage units differ")
            if (
                row["binning"] != first["binning"]
                or row["measurement_configuration"] != first["measurement_configuration"]
                or row["inspection"]["time"]["period_s"] != first["inspection"]["time"]["period_s"]
            ):
                reasons.append("binning, measurement configuration or timing differs")
    return {
        "descriptive_eligible": not reasons,
        "reasons": sorted(set(reasons)),
        "task": "FALCON H1 modern NWB",
        "exposure_s_by_entry": {
            row["entry_id"]: row["measurements"]["analyzed_exposure_s"] for row in records
        },
        "raw_mask_true_fraction_by_entry": {
            row["entry_id"]: row["measurements"]["raw_eval_mask"]["true_fraction_of_samples"]
            for row in records
        },
        "identity_unknown_by_entry": {
            row["entry_id"]: [field for field in IDENTITY_FIELDS if row["identity"][field] is None]
            for row in records
        },
        "feature_profile_by_entry": {
            row["entry_id"]: row["inspection"]["features"] for row in records
        },
        "timing_profile_by_entry": {
            row["entry_id"]: {
                "period_s": row["inspection"]["time"]["period_s"],
                "binning": row["binning"],
                "measurement_configuration": row["measurement_configuration"],
            }
            for row in records
        },
        "unit_matching": "not performed; IDs/dimensions do not establish biological identity",
        "future_decoder_training_suitability": "not assessed",
    }
