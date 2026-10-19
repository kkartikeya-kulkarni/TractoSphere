"""Parse and inspect supplied FSL-convention gradients without changing them.

Use ``GradientTable.from_files(path_to_bvals, path_to_bvecs)`` from a script,
or the shared dataset loader used by the CLI. Files may live anywhere outside
the repository; no particular participant folder structure is required here.
Run the synthetic checks with ``python -m pytest tests/core/test_gradients.py``.

Only storage layout changes: FSL's three rows become an N-by-three array.
No normalization, b-value adjustment, anatomical rotation, or axis flip occurs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .validation import CheckResult, ValidationPolicy

GradientLayout = Literal["auto", "3xN", "Nx3"]
_NUMBER = re.compile(
    r"[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|inf(?:inity)?|nan)",
    re.IGNORECASE | re.ASCII,
)
_CONVENTION_MESSAGE = (
    "FSL image-axis convention assumed; anatomical gradient correspondence not verified."
)


def _readonly(values: ArrayLike) -> NDArray[np.float64]:
    """Copy numeric metadata onto an immutable buffer, including its base."""
    array = np.asarray(values, dtype=np.float64)
    # A bytes-backed array cannot have its writeable flag re-enabled by callers.
    return np.frombuffer(array.tobytes(order="C"), dtype=np.float64).reshape(array.shape)


def _indices(mask: NDArray[np.bool_]) -> tuple[int, ...]:
    """Return ordinary Python indices for JSON-compatible validation evidence."""
    return tuple(int(index) for index in np.flatnonzero(mask))


def _numbers(values: NDArray[np.float64]) -> list[float | str]:
    """Keep invalid values visible without emitting nonstandard JSON numbers."""
    return [float(value) if np.isfinite(value) else str(float(value)) for value in values]


def _threshold(value: float, name: str) -> float:
    """Reject nonsensical direct helper arguments just as the policy does."""
    if isinstance(value, (bool, np.bool_)) or not np.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative number.")
    return float(value)


class GradientParseError(ValueError):
    """Expected input error carrying findings from both gradient files."""

    def __init__(self, checks: tuple[CheckResult, ...]) -> None:
        """Retain structured checks so adapters do not parse exception text."""
        self.checks = checks
        failures = [check for check in checks if check.status == "fail"]
        self.code = failures[0].code if failures else "G01"
        super().__init__("; ".join(check.message for check in failures))


@dataclass(frozen=True, slots=True)
class ShellSummary:
    """One descriptive b-value bin, not a claim of fitted acquisition shells."""

    b_min: float
    b_max: float
    b_median: float
    count: int
    volume_indices: tuple[int, ...]
    is_b0: bool

    def to_dict(self) -> dict[str, object]:
        """Export ordinary JSON values, preserving original volume order."""
        return {
            "b_min": self.b_min,
            "b_max": self.b_max,
            "b_median": self.b_median,
            "count": self.count,
            "volume_indices": list(self.volume_indices),
            "is_b0": self.is_b0,
        }


def _read_rows(path: Path) -> tuple[list[list[float]] | None, CheckResult]:
    """Read strict whitespace-separated numbers with actionable locations."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        return None, CheckResult(
            "G01",
            "fail",
            f"Cannot read gradient text: {error}",
            source=str(path),
            remediation="Choose a readable UTF-8 gradient file belonging to this image.",
        )
    rows: list[list[float]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        tokens = line.split()
        if not tokens:
            continue
        row: list[float] = []
        for token_number, token in enumerate(tokens, start=1):
            # float() also accepts underscores; the on-disk format does not.
            if _NUMBER.fullmatch(token) is None:
                return None, CheckResult(
                    "G01",
                    "fail",
                    f"Invalid gradient token {token!r} at line {line_number}, token {token_number}.",
                    source=str(path),
                    observed={"line": line_number, "token": token_number, "value": token},
                    remediation="Use numbers separated by whitespace, without headers, comments, or commas.",
                )
            # NaN/Inf remain parseable so G04 can identify their volume indices.
            row.append(float(token))
        rows.append(row)
    return rows, CheckResult("G01", "pass", "Gradient text syntax is valid.", source=str(path))


@dataclass(frozen=True, slots=True, eq=False)
class GradientTable:
    """Preserved numeric gradients with an explicit storage/convention contract.

    Direct construction always expects internal bvecs shaped (N, 3), even when
    ``source_layout`` records that a file originally used three rows. Mismatched
    bval and bvec counts are retained for structured validation, never broadcast.
    """

    bvals: NDArray[np.float64]
    bvecs: NDArray[np.float64]
    source_layout: Literal["3xN", "Nx3"] = "Nx3"
    convention: Literal["fsl_image_axes", "mrtrix_world_ras"] = "fsl_image_axes"
    _parse_checks: tuple[CheckResult, ...] = field(default=(), repr=False)
    _ambiguous: bool = field(default=False, repr=False)
    _bval_source: str | None = field(default=None, repr=False)
    _bvec_source: str | None = field(default=None, repr=False)
    _bval_column: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        """Detach arrays from caller storage and verify the internal shape."""
        bvals, bvecs = _readonly(self.bvals), _readonly(self.bvecs)
        if bvals.ndim != 1:
            raise ValueError("Internal bvals must be a one-dimensional array.")
        if bvecs.ndim != 2 or bvecs.shape[1] != 3:
            raise ValueError("Internal bvecs must have shape (N, 3).")
        if self.source_layout not in ("3xN", "Nx3"):
            raise ValueError("source_layout must be '3xN' or 'Nx3'.")
        if self.convention not in {"fsl_image_axes", "mrtrix_world_ras"}:
            raise ValueError(
                "Supported frames are supplied FSL image axes or embedded MRtrix world RAS; no automatic conversion."
            )
        object.__setattr__(self, "bvals", bvals)
        object.__setattr__(self, "bvecs", bvecs)
        object.__setattr__(self, "_parse_checks", tuple(self._parse_checks))

    @classmethod
    def from_files(
        cls,
        bval_path: Path,
        bvec_path: Path,
        *,
        layout: GradientLayout = "auto",
    ) -> GradientTable:
        """Parse both files independently; report malformed syntax or layout."""
        if layout not in ("auto", "3xN", "Nx3"):
            raise ValueError("layout must be 'auto', '3xN', or 'Nx3'.")
        bval_path, bvec_path = Path(bval_path), Path(bvec_path)
        value_rows, value_syntax = _read_rows(bval_path)
        vector_rows, vector_syntax = _read_rows(bvec_path)
        checks = [value_syntax, vector_syntax]
        bvals = bvecs = None
        column = ambiguous = False
        chosen = "3xN"
        if value_rows is None:
            checks.append(
                CheckResult(
                    "G02",
                    "skipped",
                    "b-value shape unavailable after parse failure.",
                    source=str(bval_path),
                )
            )
        elif not value_rows or not (
            len(value_rows) == 1 or all(len(row) == 1 for row in value_rows)
        ):
            checks.append(
                CheckResult(
                    "G02",
                    "fail",
                    "b-values must contain one nonempty numeric row or column.",
                    source=str(bval_path),
                    remediation="Select the matching one-row or one-column b-value file.",
                )
            )
        else:
            column = len(value_rows) > 1
            bvals = np.asarray([value for row in value_rows for value in row], dtype=np.float64)
            checks.append(
                CheckResult(
                    "G02", "pass", "b-value row/column shape is valid.", source=str(bval_path)
                )
            )
        if vector_rows is None:
            checks.append(
                CheckResult(
                    "G03",
                    "skipped",
                    "b-vector shape unavailable after parse failure.",
                    source=str(bvec_path),
                )
            )
        elif not vector_rows or len({len(row) for row in vector_rows}) != 1:
            code = "G01" if vector_rows else "G03"
            checks.append(
                CheckResult(
                    code,
                    "fail",
                    "b-vectors must be nonempty and have equally sized rows.",
                    source=str(bvec_path),
                    remediation="Supply a rectangular FSL 3×N or N×3 b-vector table.",
                )
            )
        else:
            matrix = np.asarray(vector_rows, dtype=np.float64)
            rows, columns = matrix.shape
            # Three-by-three cannot be resolved from norms: use the explicit
            # FSL default and retain an ambiguity warning until overridden.
            chosen = ("3xN" if rows == 3 else "Nx3") if layout == "auto" else layout
            if (chosen == "3xN" and rows != 3) or (chosen == "Nx3" and columns != 3):
                checks.append(
                    CheckResult(
                        "G03",
                        "fail",
                        f"b-vector shape {rows}×{columns} does not match layout {layout}.",
                        source=str(bvec_path),
                        observed=[rows, columns],
                        remediation="Choose the matching layout or supply a 3×N/N×3 gradient table.",
                    )
                )
            else:
                bvecs = matrix.T if chosen == "3xN" else matrix
                ambiguous = rows == columns == 3 and layout == "auto"
                checks.append(
                    CheckResult(
                        "G03", "pass", "b-vector table layout is valid.", source=str(bvec_path)
                    )
                )
        if bvals is None or bvecs is None:
            # Independent syntax/shape checks above are preserved; dependent
            # scientific checks cannot certify a partially parsed table.
            present = {check.code for check in checks}
            for number in range(1, 10):
                code = f"G{number:02d}"
                if code not in present:
                    checks.append(
                        CheckResult(
                            code,
                            "skipped",
                            "Complete gradient table unavailable after parse failure.",
                        )
                    )
            checks.append(CheckResult("G10", "info", _CONVENTION_MESSAGE))
            raise GradientParseError(tuple(checks))
        return cls(
            bvals,
            bvecs,
            source_layout=chosen,
            _parse_checks=tuple(checks),
            _ambiguous=ambiguous,
            _bval_source=str(bval_path),
            _bvec_source=str(bvec_path),
            _bval_column=column,
        )

    @property
    def n_volumes(self) -> int:
        """Return b-value count; validation separately checks vector count."""
        return int(self.bvals.size)

    def b0_mask(self, *, b0_max: float = 50.0) -> NDArray[np.bool_]:
        """Classify values at or below the recorded inclusive threshold."""
        return self.bvals <= _threshold(b0_max, "b0_max")

    def norms(self) -> NDArray[np.float64]:
        """Compute stable vector lengths without normalizing source vectors."""
        with np.errstate(over="ignore", invalid="ignore"):
            return np.hypot.reduce(self.bvecs, axis=1)

    def shells(self, *, b0_max: float = 50.0, max_span: float = 100.0) -> tuple[ShellSummary, ...]:
        """Group by distance from each bin's minimum, preventing chaining."""
        b0_max, max_span = _threshold(b0_max, "b0_max"), _threshold(max_span, "max_span")
        if not np.all(np.isfinite(self.bvals)) or np.any(self.bvals < 0):
            raise ValueError("Shell summaries require finite nonnegative b-values.")
        groups: list[ShellSummary] = []

        def append_group(indices: NDArray[np.intp], is_b0: bool) -> None:
            """Summarize preserved values and retain their original indices."""
            values = self.bvals[indices]
            ordered = tuple(sorted(int(index) for index in indices))
            sorted_values = np.sort(values)
            middle = sorted_values.size // 2
            # Avoid overflowing the sum of two large but finite b-values.
            median = sorted_values[middle]
            if sorted_values.size % 2 == 0:
                lower = sorted_values[middle - 1]
                median = lower + (median - lower) / 2
            groups.append(
                ShellSummary(
                    float(np.min(values)),
                    float(np.max(values)),
                    float(median),
                    len(ordered),
                    ordered,
                    is_b0,
                )
            )

        b0_indices = np.flatnonzero(self.bvals <= b0_max)
        if b0_indices.size:
            append_group(b0_indices, True)
        weighted = np.flatnonzero(self.bvals > b0_max)
        weighted = weighted[np.argsort(self.bvals[weighted], kind="stable")]
        start = 0
        while start < weighted.size:
            stop = start + 1
            minimum = self.bvals[weighted[start]]
            while stop < weighted.size and self.bvals[weighted[stop]] - minimum <= max_span:
                stop += 1
            append_group(weighted[start:stop], False)
            start = stop
        return tuple(groups)

    def validate(
        self,
        *,
        expected_volumes: int | None,
        policy: ValidationPolicy,
    ) -> tuple[CheckResult, ...]:
        """Run G01–G10 without modifying data; image count may be unavailable."""
        if expected_volumes is not None and (
            isinstance(expected_volumes, (bool, np.bool_))
            or not isinstance(expected_volumes, (int, np.integer))
            or expected_volumes < 1
        ):
            raise ValueError("expected_volumes must be a positive integer or None.")
        # Successful parsing contributes syntax evidence; counts/layout below
        # supersede the parser's provisional G02/G03 shape-only messages.
        checks = [check for check in self._parse_checks if check.code == "G01"]
        if not checks:
            checks.append(
                CheckResult(
                    "G01", "info", "Gradient arrays supplied directly; text parsing not applicable."
                )
            )
        nv, ng = self.n_volumes, int(self.bvecs.shape[0])
        count_ok = nv > 0 and (expected_volumes is None or nv == expected_volumes)
        checks.append(
            CheckResult(
                "G02",
                "pass" if count_ok else "fail",
                f"b-values contain {nv} entries"
                + (
                    f"; image has {expected_volumes} volumes."
                    if expected_volumes is not None
                    else "; image volume count unavailable."
                ),
                source=self._bval_source,
                observed=nv,
                expected=expected_volumes,
                remediation=None
                if count_ok
                else "Select the b-value file matching every image volume.",
            )
        )
        if self._bval_column:
            checks.append(
                CheckResult(
                    "G02",
                    "info",
                    "b-value column interpreted as a vector; source values preserved.",
                    source=self._bval_source,
                )
            )
        if expected_volumes is None:
            checks.append(
                CheckResult("G02", "skipped", "Comparison with image volume count is unavailable.")
            )
        vector_count_ok = (
            ng > 0 and ng == nv and (expected_volumes is None or ng == expected_volumes)
        )
        checks.append(
            CheckResult(
                "G03",
                "pass" if vector_count_ok else "fail",
                f"b-vector table contains {ng} vectors in source layout {self.source_layout}.",
                source=self._bvec_source,
                observed={"vectors": ng, "bvalues": nv, "layout": self.source_layout},
                expected=expected_volumes,
                remediation=None
                if vector_count_ok
                else "Select the vector file matching the image and b-values.",
            )
        )
        if self.source_layout == "Nx3":
            checks.append(
                CheckResult(
                    "G03",
                    "info",
                    "N×3 source layout retained; no anatomical rotation applied.",
                    source=self._bvec_source,
                )
            )
        else:
            checks.append(
                CheckResult(
                    "G03",
                    "info",
                    "FSL 3×N storage transposed to N×3; vector components unchanged.",
                    source=self._bvec_source,
                )
            )
        if self._ambiguous:
            checks.append(
                CheckResult(
                    "G03",
                    "warn",
                    "Ambiguous 3×3 table interpreted as FSL 3×N.",
                    source=self._bvec_source,
                    remediation="Verify the layout and explicitly choose 3xN or Nx3.",
                )
            )
        if expected_volumes is None:
            checks.append(
                CheckResult(
                    "G03",
                    "skipped",
                    "Comparison with image volume count is unavailable; b-value/vector mutual counts were checked.",
                )
            )
        finite_b = np.isfinite(self.bvals)
        finite_g = np.all(np.isfinite(self.bvecs), axis=1)
        bad_b, bad_g = _indices(~finite_b), _indices(~finite_g)
        checks.append(
            CheckResult(
                "G04",
                "fail" if bad_b or bad_g else "pass",
                "Gradient values must all be finite.",
                volume_indices=tuple(sorted(set(bad_b + bad_g))),
                observed={
                    "nonfinite_bval_indices": list(bad_b),
                    "nonfinite_bvec_indices": list(bad_g),
                },
                remediation="Supply finite gradients; no values were replaced."
                if bad_b or bad_g
                else None,
            )
        )
        negative = _indices(finite_b & (self.bvals < 0))
        high = _indices(finite_b & (self.bvals > 20_000))
        checks.append(
            CheckResult(
                "G05",
                "fail" if negative else ("warn" if high else "pass"),
                "b-values are interpreted in s/mm²; negative values are invalid and values above 20,000 require unit verification.",
                source=self._bval_source,
                volume_indices=negative or high,
                observed={
                    "negative_indices": list(negative),
                    "high_b_indices": list(high),
                    "units_assumed": "s/mm²",
                },
                remediation="Supply nonnegative b-values; no clamping applied."
                if negative
                else ("Verify b-value units and acquisition settings." if high else None),
            )
        )
        valid_b = bool(nv and np.all(finite_b) and not negative)
        if valid_b:
            b0 = self.b0_mask(b0_max=policy.b0_max)
            b0_count = int(np.count_nonzero(b0))
            message = (
                "No b0-like volume found."
                if b0_count == 0
                else "No diffusion-weighted volumes remain under the configured b0 threshold."
                if b0_count == nv
                else "Both b0-like and diffusion-weighted volumes are present."
            )
            checks.append(
                CheckResult(
                    "G06",
                    "warn" if b0_count in (0, nv) else "pass",
                    message,
                    observed={
                        "b0_count": b0_count,
                        "weighted_count": nv - b0_count,
                        "b0_max": policy.b0_max,
                    },
                    remediation="Inspection is allowed; later methods must check their own sampling requirements."
                    if b0_count in (0, nv)
                    else None,
                )
            )
        else:
            checks.append(
                CheckResult(
                    "G06",
                    "skipped",
                    "b0 classification requires nonempty, finite, nonnegative b-values.",
                )
            )
        # Mismatched counts must never produce an accidental NumPy broadcast.
        if not valid_b or ng != nv or not np.all(finite_g):
            for code in ("G07", "G08"):
                checks.append(
                    CheckResult(
                        code,
                        "skipped",
                        "Norm checks require finite vectors paired with valid b-values.",
                    )
                )
        else:
            norms = self.norms()
            unit = (norms >= 1.0 - policy.unit_norm_atol) & (norms <= 1.0 + policy.unit_norm_atol)
            invalid_weighted = _indices(~b0 & (~unit | (norms == 0)))
            checks.append(
                CheckResult(
                    "G07",
                    "fail" if invalid_weighted else "pass",
                    "Diffusion-weighted vectors must have unit norm within the configured tolerance.",
                    source=self._bvec_source,
                    volume_indices=invalid_weighted,
                    observed={"norms": _numbers(norms[list(invalid_weighted)])},
                    expected={"unit_norm_atol": policy.unit_norm_atol},
                    remediation="Supply matching unit vectors; no normalization or b-value scaling was applied."
                    if invalid_weighted
                    else None,
                )
            )
            invalid_b0 = _indices(b0 & ~((norms <= policy.zero_norm_atol) | unit))
            nonzero_b0 = _indices(b0 & (norms > 0))
            checks.append(
                CheckResult(
                    "G08",
                    "warn" if invalid_b0 else "pass",
                    "b0-like directions are retained; zero or unit-length vectors are accepted.",
                    source=self._bvec_source,
                    volume_indices=invalid_b0,
                    observed={
                        "nonzero_b0_indices": list(nonzero_b0),
                        "unusual_norms": _numbers(norms[list(invalid_b0)]),
                    },
                    remediation="Verify unusual b0-like vectors; they are not used for directional interpretation."
                    if invalid_b0
                    else None,
                )
            )
        if valid_b:
            summaries = self.shells(b0_max=policy.b0_max, max_span=policy.shell_span)
            total = sum(shell.count for shell in summaries)
            checks.append(
                CheckResult(
                    "G09",
                    "pass" if total == nv else "fail",
                    "Descriptive b-value groups cover the supplied b-values.",
                    observed={"groups": [shell.to_dict() for shell in summaries], "total": total},
                    expected=nv,
                    remediation=None
                    if total == nv
                    else "Report an internal shell-summary inconsistency.",
                )
            )
        else:
            checks.append(
                CheckResult(
                    "G09",
                    "skipped",
                    "Shell grouping requires nonempty, finite, nonnegative b-values.",
                )
            )
        checks.append(
            CheckResult(
                "G10",
                "info",
                _CONVENTION_MESSAGE
                if self.convention == "fsl_image_axes"
                else "Embedded MRtrix scanner/world RAS gradients retained in volume order; no FSL conversion, rotation, normalization or b-value rescaling applied. Anatomical correspondence is unverified.",
            )
        )
        return tuple(checks)


def mrtrix_world_scheme(
    bvals: ArrayLike,
    bvecs: ArrayLike,
    *,
    convention: str,
    affine: ArrayLike | None = None,
) -> NDArray[np.float64]:
    """Detached expected scheme for an explicitly reviewed MRtrix gradient import.

    Parsing/GradientTable never change source directions. This function mirrors
    MRtrix's declared unit normalization and FSL storage-handedness mapping for
    conversion verification only; it is not an empirical anatomical flip or a
    gradient-correction algorithm. B-values are retained exactly, with scaling
    disabled in the corresponding command. Embedded world-RAS inputs need no
    voxel-axis rotation. Source arrays remain unchanged.
    """
    values = np.asarray(bvals, dtype=np.float64)
    vectors = np.asarray(bvecs, dtype=np.float64).copy()
    if (
        values.ndim != 1
        or vectors.shape != (len(values), 3)
        or not np.isfinite(values).all()
        or not np.isfinite(vectors).all()
    ):
        raise ValueError("Expected finite matching gradient rows before coordinate conversion")
    norms = np.linalg.norm(vectors, axis=1)
    if convention == "fsl_image_axes":
        matrix = np.asarray(affine, dtype=np.float64)
        if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
            raise ValueError("FSL import verification requires a finite affine")
        rotation = matrix[:3, :3] / np.linalg.norm(matrix[:3, :3], axis=0)
        if np.linalg.det(rotation) > 0:
            vectors[:, 0] *= -1
        vectors[norms > 0] /= norms[norms > 0, None]
        vectors = (rotation @ vectors.T).T
    elif convention == "mrtrix_world_ras":
        vectors[norms > 0] /= norms[norms > 0, None]
    else:
        raise ValueError("Unsupported gradient coordinate convention")
    return np.column_stack((vectors, values))
