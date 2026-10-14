"""Immutable validation evidence shared by scripts, CLI, and future GUI.

Run the focused tests with ``python -m pytest tests/core/test_validation.py``.
No filesystem layout is assumed here: paths are evidence in a report, not
instructions to open or change a source. Policy thresholds classify supplied
values; they never authorize normalization or data repair.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from numbers import Integral, Real
from types import MappingProxyType
from typing import Any, Literal
from uuid import uuid4

type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
type CheckStatus = Literal["pass", "info", "warn", "fail", "skipped"]

CHECK_CODES = tuple(
    f"{prefix}{number:02d}"
    for prefix, count in (("F", 5), ("N", 7), ("A", 4), ("G", 10), ("J", 5), ("V", 3), ("C", 3))
    for number in range(1, count + 1)
)
COVERAGE_MESSAGE = "Metadata and sampled voxels checked; full voxel scan not performed."
MANDATORY_CHECK_CODES = frozenset(CHECK_CODES) - {
    "F05",
    "A03",
    "J01",
    "J02",
    "J03",
    "J04",
    "J05",
}
UNPERFORMED_CHECKS = (
    "Exhaustive voxel finiteness",
    "Complete gzip stream integrity / CRC verification",
    "Anatomical gradient correspondence",
    "Motion and artifact quality control",
    "Readiness for a particular processing method",
)


def utc_now() -> str:
    """Return an unambiguous UTC timestamp for provenance, independent of UI locale."""
    return datetime.now(UTC).isoformat()


def describe_coverage(
    checks: tuple[CheckResult, ...] | list[CheckResult],
    *,
    complete: bool,
    cancelled: bool,
) -> str:
    """Describe observed coverage rather than treating an attempted read as success."""
    if not complete or cancelled:
        return "Validation incomplete; check rows show what was examined. Full voxel scan not performed."
    reads = [check for check in checks if check.code == "V01"]
    if not any(check.status == "pass" for check in reads):
        return (
            "Metadata checks attempted; voxel samples were not read. Full voxel scan not performed."
        )
    if any(check.status in ("fail", "skipped") for check in reads):
        return "Metadata and available voxel samples checked; sampling incomplete. Full voxel scan not performed."
    return COVERAGE_MESSAGE


def json_compatible(value: Any) -> JSONValue:
    """Return detached JSON values, retaining nonfinite diagnostics as strings.

    JSON does not support NaN/Infinity. Invalid inputs must still be reportable,
    so their diagnostic values become explicit text instead of invalid JSON.
    """
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        if math.isfinite(number):
            return number
        return "NaN" if math.isnan(number) else ("Infinity" if number > 0 else "-Infinity")
    if isinstance(value, Mapping):
        return {str(key): json_compatible(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_compatible(item) for item in value]
    if hasattr(value, "tolist"):
        return json_compatible(value.tolist())
    return str(value)


def freeze_json(value: Any) -> Any:
    """Defensively freeze nested evidence so a consumer cannot rewrite a report."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    if hasattr(value, "tolist"):
        return freeze_json(value.tolist())
    return value


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    """Explicit, serializable loader policy; all b-values use s/mm²."""

    bvec_layout: Literal["auto", "3xN", "Nx3"] = "auto"
    b0_max: float = 50.0
    unit_norm_atol: float = 0.01
    zero_norm_atol: float = 1e-6
    shell_span: float = 100.0
    sample_edge: int = 16

    def __post_init__(self) -> None:
        """Reject invalid policy before any source file is opened."""
        if self.bvec_layout not in ("auto", "3xN", "Nx3"):
            raise ValueError("bvec_layout must be auto, 3xN, or Nx3")
        for name in ("b0_max", "unit_norm_atol", "zero_norm_atol", "shell_span"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(f"{name} must be a finite nonnegative number")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite nonnegative number")
        if isinstance(self.sample_edge, bool) or not isinstance(self.sample_edge, Integral):
            raise ValueError("sample_edge must be a positive integer")
        if self.sample_edge < 1:
            raise ValueError("sample_edge must be a positive integer")


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One check and its evidence; volume indices are zero-based internally."""

    code: str
    status: CheckStatus
    message: str
    source: str | None = None
    volume_indices: tuple[int, ...] = ()
    observed: Any = None
    expected: Any = None
    remediation: str | None = None

    def __post_init__(self) -> None:
        """Freeze diagnostic payloads without discarding unusual source values."""
        if self.status not in ("pass", "info", "warn", "fail", "skipped"):
            raise ValueError(f"Unknown check status: {self.status}")
        object.__setattr__(self, "volume_indices", tuple(self.volume_indices))
        object.__setattr__(self, "observed", freeze_json(self.observed))
        object.__setattr__(self, "expected", freeze_json(self.expected))

    def to_dict(self) -> dict[str, JSONValue]:
        """Serialize without relying on NumPy or a GUI-specific encoder."""
        return {
            "code": self.code,
            "status": self.status,
            "message": self.message,
            "source": self.source,
            "volume_indices": list(self.volume_indices),
            "observed": json_compatible(self.observed),
            "expected": json_compatible(self.expected),
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """A complete evidence snapshot with explicit coverage and cancellation state."""

    checks: tuple[CheckResult, ...]
    policy: ValidationPolicy = field(default_factory=ValidationPolicy)
    complete: bool = True
    cancelled: bool = False
    run_id: str = field(default_factory=lambda: str(uuid4()))
    started_at: str = field(default_factory=utc_now)
    finished_at: str = field(default_factory=utc_now)
    sources: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    sampled_regions: tuple[Any, ...] = ()
    software_versions: Mapping[str, str] = field(default_factory=dict)
    coverage: Literal["metadata_and_samples"] = "metadata_and_samples"
    unperformed_checks: tuple[str, ...] = UNPERFORMED_CHECKS
    schema_version: int = 1

    def __post_init__(self) -> None:
        """Detach nested metadata from the loader's mutable construction buffers."""
        object.__setattr__(self, "checks", tuple(self.checks))
        for name in ("sources", "metadata", "sampled_regions", "software_versions"):
            object.__setattr__(self, name, freeze_json(getattr(self, name)))

    def status(self) -> Literal["pass", "warn", "fail", "incomplete"]:
        """Return overall state; incomplete or cancelled work is never a pass."""
        if not self.complete or self.cancelled:
            return "incomplete"
        if any(check.status == "fail" for check in self.checks):
            return "fail"
        if set(CHECK_CODES) - {check.code for check in self.checks}:
            return "incomplete"
        performed = {check.code for check in self.checks if check.status != "skipped"}
        if MANDATORY_CHECK_CODES - performed:
            return "incomplete"
        return "warn" if any(check.status == "warn" for check in self.checks) else "pass"

    def coverage_message(self) -> str:
        """Explain actual metadata/sample coverage even for rejected input."""
        return describe_coverage(self.checks, complete=self.complete, cancelled=self.cancelled)

    def can_load(self) -> bool:
        """Permit completed loadable data, retaining every warning and limitation."""
        return self.status() in ("pass", "warn")

    def counts(self) -> dict[str, int]:
        """Count result rows (a check can emit more than one finding)."""
        return {
            state: sum(c.status == state for c in self.checks)
            for state in ("pass", "info", "warn", "fail", "skipped")
        }

    def by_status(self, status: str) -> tuple[CheckResult, ...]:
        """Select findings without modifying their report order."""
        return tuple(check for check in self.checks if check.status == status)

    def to_dict(self) -> dict[str, JSONValue]:
        """Return a detached machine-readable report with policy and provenance."""
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "status": self.status(),
            "can_load": self.can_load(),
            "complete": self.complete,
            "cancelled": self.cancelled,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "coverage": self.coverage,
            "coverage_message": self.coverage_message(),
            "policy": json_compatible(asdict(self.policy)),
            "counts": self.counts(),
            "sources": json_compatible(self.sources),
            "metadata": json_compatible(self.metadata),
            "sampled_regions": json_compatible(self.sampled_regions),
            "software_versions": json_compatible(self.software_versions),
            "unperformed_checks": list(self.unperformed_checks),
            "checks": [check.to_dict() for check in self.checks],
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Encode strict JSON; unsupported NaN/Infinity literals are never emitted."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, allow_nan=False)
