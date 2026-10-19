"""Read-only MRtrix zonal-SH response contracts and explicit shell association.

These are response coefficients (m=0 for each even degree), not full FOD SH
images or diffusion tensors. No value normalization, clipping or replacement is
performed. Missing shell metadata requires an explicit ordered declaration.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np


def read_response(path: Path, tissue: str, shells=None) -> dict:
    """Validate a complete finite rectangular response, retaining original values."""
    if tissue not in {"wm", "gm", "csf"}:
        raise ValueError("Declare response tissue explicitly: wm, gm or csf")
    if path.stat().st_size > 32 * 1024**2:
        raise ValueError("Response text exceeds the 32 MiB import bound")
    rows, header_shells, history = [], None, []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            match = re.match(r"#\s*Shells\s*:(.*)", line, re.I)
            if match:
                if header_shells is not None:
                    raise ValueError("Response has duplicate shell metadata")
                header_shells = [float(v) for v in re.split(r"[,\s]+", match[1].strip()) if v]
            if "command_history:" in line:
                history.append(line.removeprefix("#").strip())
            continue
        try:
            rows.append([float(v) for v in line.split()])
        except ValueError as exc:
            raise ValueError("Response contains nonnumeric coefficients") from exc
    if not rows or not rows[0] or len({len(row) for row in rows}) != 1:
        raise ValueError("Response requires nonempty, rectangular coefficient rows")
    values = np.asarray(rows)
    if not np.isfinite(values).all() or np.any(values[:, 0] <= 0):
        raise ValueError("Response must be finite with positive zeroth-order signal; no repair")
    if values.shape[1] > 16:
        raise ValueError("Response order exceeds the supported lmax 30 contract")
    if tissue in {"gm", "csf"} and values.shape[1] != 1:
        raise ValueError("Isotropic GM/CSF responses require one coefficient per shell")
    if shells is not None:
        if not isinstance(shells, (list, tuple)):
            raise ValueError("Response shells must be an ordered list")
        if header_shells is not None and not np.array_equal(shells, header_shells):
            raise ValueError("Declared response shells disagree with original file metadata")
    chosen = header_shells if header_shells is not None else shells
    if chosen is None or len(chosen) != len(rows):
        raise ValueError(
            "Declare ordered response shell b-values matching its rows (or retain # Shells metadata)"
        )
    if any(isinstance(b, bool) or not isinstance(b, (int, float)) for b in chosen):
        raise ValueError("Response shell values must be finite nonnegative numbers")
    bvalues = np.asarray(chosen, dtype=float)
    if not np.isfinite(bvalues).all() or np.any(bvalues < 0) or np.any(np.diff(bvalues) <= 0):
        raise ValueError("Response shells must be finite, nonnegative, unique and ordered")
    return dict(
        rows=len(rows),
        columns=values.shape[1],
        tissue=tissue,
        shells=bvalues.tolist(),
        coefficients=rows,
        lmax=2 * (values.shape[1] - 1),
        shell_metadata_source="file header"
        if header_shells is not None
        else "explicit declaration",
        coefficient_convention="MRtrix zonal real SH: m=0 for degrees 0,2,...; not full FOD coefficients",
        command_history=history,
    )


def response_rows(
    metadata: dict, selected: list[dict], tissue: str, lmax: int, *, single=False
) -> list[int]:
    """Associate each selected shell once; never guess rows from their count."""
    if metadata.get("tissue") != tissue:
        raise ValueError(f"{tissue} response tissue identity disagrees with its workflow slot")
    groups = sorted(
        (g for g in selected if not single or not g["is_b0"]), key=lambda g: g["b_median"]
    )
    available = metadata["shells"]
    rows = []
    for group in groups:
        # Mean/median shell summaries can differ slightly; this is association
        # within the existing descriptive shell tolerance, never b-value editing.
        matches = [
            i
            for i, b in enumerate(available)
            if abs(b - group["b_median"]) <= 100 and (b <= 50) == group["is_b0"]
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{tissue} response has no unique row for selected shell {group['b_median']}"
            )
        rows.append(matches[0])
    if not rows or len(set(rows)) != len(rows):
        raise ValueError("Response row association is empty or ambiguous")
    if tissue == "wm" and metadata["lmax"] < lmax:
        raise ValueError("WM response has insufficient coefficients for requested FOD lmax")
    return rows
