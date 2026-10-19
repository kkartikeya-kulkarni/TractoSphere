"""Descriptive tensor-validity counts; never repair data or confer acceptance."""

from __future__ import annotations

import numpy as np


def eigenvalue_findings(eigenvalues: np.ndarray, fa: np.ndarray | None = None) -> dict:
    """Separate negative components, affected voxels and roundoff-scale findings.

    Inputs are independently audited eigenvalues in mm²/s, one row per evaluated
    voxel. The 32 Float32-epsilon relative allowance is a conservative reporting
    scale for Float32 coefficient/eigensolver roundoff, with a 1e-10 mm²/s floor
    near zero. It is not a biological acceptance threshold. Raw violations remain
    recorded and retain the engine's existing review/flagging policy.
    """
    values = np.asarray(eigenvalues, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 3:
        raise ValueError("Expected one three-eigenvalue row per evaluated voxel")
    finite = np.isfinite(values)
    valid = finite.all(axis=1)
    scale = np.max(np.where(finite, np.abs(values), 0), axis=1)
    tolerance = np.maximum(1e-10, 32 * np.finfo(np.float32).eps * scale)
    negative = finite & (values < 0)
    meaningful = finite & (values < -tolerance[:, None])
    result = dict(
        evaluated_voxels=len(values),
        nonfinite_eigenvalues=int((~finite).sum()),
        voxels_with_nonfinite_eigenvalues=int((~valid).sum()),
        negative_eigenvalues_raw=int(negative.sum()),
        negative_eigenvalue_voxels_raw=int(negative.any(axis=1).sum()),
        negative_eigenvalues_beyond_roundoff=int(meaningful.sum()),
        negative_eigenvalue_voxels_beyond_roundoff=int(meaningful.any(axis=1).sum()),
        percent_voxels_negative_beyond_roundoff=100 * float(meaningful.any(axis=1).mean())
        if len(values)
        else 0.0,
        roundoff_policy=dict(
            relative_float32_epsilons=32,
            eigenvalue_absolute_floor_mm2_s=1e-10,
            eigenvalue_formula="max(1e-10, 32*Float32_epsilon*max(abs(lambda))) per voxel",
            fa_absolute_tolerance=float(32 * np.finfo(np.float32).eps),
            scientific_acceptance=False,
        ),
    )
    if fa is not None:
        anisotropy = np.asarray(fa, dtype=np.float64)
        if anisotropy.shape != (len(values),):
            raise ValueError("FA must contain one value per evaluated voxel")
        beyond = np.isfinite(anisotropy) & (anisotropy > 1 + 32 * np.finfo(np.float32).eps)
        result.update(
            fa_nonfinite=int((~np.isfinite(anisotropy)).sum()),
            fa_above_one_raw=int((anisotropy > 1).sum()),
            fa_above_one_beyond_roundoff=int(beyond.sum()),
            percent_fa_above_one_beyond_roundoff=100 * float(beyond.mean())
            if len(values)
            else 0.0,
        )
    return result
