"""EDDY 2401.2 diagnostic interpretation, separate from scientific acceptance.

Extracted from the existing validation adapters. Shell indexing follows the
installed EddyUtils::get_groups/Isb0 rules (2401.2 EddyUtils.cpp:48–110 and
EddyUtils.h:294–297), not the viewer's descriptive bins. No diagnostic values
are repaired, thresholded to zero or interpreted as independent ground truth.
"""

from __future__ import annotations

import re

import numpy as np


def finite_statistics(values: np.ndarray) -> dict:
    """Count invalid/zero values without suppressing them in summaries."""
    values = np.asarray(values)
    finite = np.isfinite(values)
    return {
        "count": int(values.size),
        "nonfinite": int((~finite).sum()),
        "nan": int(np.isnan(values).sum()),
        "positive_infinity": int(np.isposinf(values).sum()),
        "negative_infinity": int(np.isneginf(values).sum()),
        "exact_zero": int((values == 0).sum()),
        "finite_percentiles_0_1_50_99_100": np.percentile(
            values[finite], [0, 1, 50, 99, 100]
        ).tolist()
        if finite.any()
        else None,
    }


def eddy_shells(bvalues: np.ndarray, *, b_range: float = 100.0) -> list[dict]:
    """Verify unique volume assignment using the pinned executable's grouping.

    Only interprets outputs of EDDY 2401.2. Does not bypass EDDY's own shelled-data
    check or change any acquired b-values. Ambiguous shell membership is refused.
    """
    b = np.asarray(bvalues, dtype=float)
    if b.ndim != 1 or not b.size or not np.isfinite(b).all() or (b < 0).any():
        raise ValueError("Finite nonnegative 1D b-values are required")
    if not np.isfinite(b_range) or b_range <= 0:
        raise ValueError("Positive finite EDDY b_range required")
    templates = [0]
    for i in range(1, len(b)):
        if not any(abs(b[t] - b[i]) < b_range for t in templates):
            templates.append(i)
    means = sorted(float(b[np.abs(b - b[t]) < b_range].mean()) for t in templates)
    indices = [np.flatnonzero(np.abs(b - mean) <= b_range) for mean in means]
    if sorted(np.concatenate(indices).tolist()) != list(range(len(b))):
        raise ValueError("Ambiguous or missing EDDY shell membership")
    result = []
    for mean, group in zip(means, indices, strict=True):
        if not len(group) or np.any((b[group] <= b_range) != (mean <= b_range)):
            raise ValueError("Shell crosses EDDY's low-b classification boundary")
        result.append({"mean_bvalue": mean, "indices": group.tolist(), "is_b0": mean <= b_range})
    if not result[0]["is_b0"] or len(result) < 2:
        raise ValueError("EDDY correction requires low-b and diffusion-weighted measurements")
    return result


def hyperparameter_blocks(text: str, shells: int) -> list[dict]:
    """Decode logged NewSphericalKMatrix parameters, retaining invalid failures.

    EddyCommandLineOptions.cpp binds spheri to NewSpherical; KMatrix.h:273–332
    and KMatrix.cpp:392–476 define the exponential parameterization. A malformed
    or nonfinite block must not disappear just because a later block is valid.
    """
    lines = text.splitlines()
    rows = []
    expected = 3 if shells == 1 else 3 + shells
    number = re.compile(
        r"^[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|nan|inf(?:inity)?)$", re.I
    )
    for i, line in enumerate(lines):
        if not line.strip().startswith("Estimated hyperparameters:"):
            continue
        first = line.strip().removeprefix("Estimated hyperparameters:").strip()
        if first and not number.fullmatch(first):
            raise ValueError("Malformed first logged GP hyperparameter")
        values = [float(first)] if first else []
        for following in lines[i + 1 :]:
            value = following.strip()
            if not value and not values:
                continue
            if not number.fullmatch(value):
                break
            values.append(float(value))
        vector = np.asarray(values)
        if len(values) != expected or not np.isfinite(vector).all():
            raise ValueError("Unexpected or nonfinite installed spherical GP schema")
        try:
            with np.errstate(over="raise", under="raise", invalid="raise", divide="raise"):
                transformed = np.exp(vector)
                noise = transformed[2:3] if shells == 1 else transformed[3:]
                ratios = noise / transformed[0]
        except FloatingPointError as exc:
            raise ValueError(
                "GP variance transform underflow/overflow; diagnostics invalid"
            ) from exc
        rows.append(
            {
                "logged_log_parameters": values,
                "signal_variance": float(transformed[0]),
                "angular_length_scale_radians": float(transformed[1]),
                "log_b_length_scale": float(transformed[2]) if shells > 1 else None,
                "error_variances_in_shell_order": noise.tolist(),
                "noise_to_signal_variance_ratios": ratios.tolist(),
            }
        )
    if not rows:
        raise ValueError("No logged GP hyperparameters available")
    return rows
