"""Explicit same-grid image comparisons; no registration or acceptance inference.

Call validate_comparison before numerical differences. A common voxel grid does
not prove anatomical alignment or matched diffusion encoding: callers must supply
and disclose acquisition/volume correspondence evidence separately.
"""

import numpy as np

from .dataset import DwiDataset
from .overlay import validate_overlay_space
from .viewer_display import normalize


def validate_comparison(left: DwiDataset, right: DwiDataset, pairs: list[tuple[int, int]]) -> float:
    """Verify source currency, physical grids and explicit paired volume indices."""
    left.assert_current()
    right.assert_current()
    if right.affine_source == "fallback":
        raise ValueError("Comparison requires a verified affine for both images")
    distance = validate_overlay_space(right.shape, right.affine, right.spatial_units, left)
    if not pairs:
        raise ValueError("Supply explicit volume correspondences")
    for a, b in pairs:
        if not isinstance(a, int) or not isinstance(b, int):
            raise ValueError("Volume correspondence indices must be integers")
        if not 0 <= a < left.n_volumes or not 0 <= b < right.n_volumes:
            raise ValueError("Volume correspondence is outside its source image")
    return distance


def difference_metrics(a: np.ndarray, b: np.ndarray, mask: np.ndarray | None = None) -> dict:
    """Descriptive b-minus-a metrics with explicit nonfinite coverage, no grading."""
    if a.shape != b.shape or (mask is not None and mask.shape != a.shape):
        raise ValueError("Differences require identical array and mask shapes")
    selected = np.ones(a.shape, bool) if mask is None else np.asarray(mask, bool)
    finite = selected & np.isfinite(a) & np.isfinite(b)
    x, y = np.asarray(a[finite], float), np.asarray(b[finite], float)
    result = {
        "selected_voxels": int(selected.sum()),
        "finite_pairs": int(finite.sum()),
        "nonfinite_pairs": int((selected & ~finite).sum()),
    }
    if not x.size:
        return result | {
            "mean_difference": None,
            "MAE": None,
            "RMSE": None,
            "relative_L2": None,
            "correlation": None,
        }
    delta = y - x
    denominator = float(np.dot(x, x))
    correlation = float(np.corrcoef(x, y)[0, 1]) if x.std() > 0 and y.std() > 0 else None
    return result | {
        "mean_difference": float(delta.mean()),
        "MAE": float(abs(delta).mean()),
        "RMSE": float(np.sqrt(np.mean(delta**2))),
        "relative_L2": float(np.sqrt(np.dot(delta, delta) / denominator)) if denominator else None,
        "correlation": correlation,
        "left_mean": float(x.mean()),
        "right_mean": float(y.mean()),
    }


def comparison_rgb(
    left: np.ndarray,
    right: np.ndarray,
    mode: str,
    limits: tuple[float, float],
    *,
    opacity: float = 0.5,
    difference_limit: float = 1,
) -> np.ndarray:
    """Display-only grayscale blend or signed red/blue right-minus-left map."""
    if left.shape != right.shape:
        raise ValueError("Comparison planes have different shapes")
    if not 0 <= opacity <= 1 or not np.isfinite(difference_limit) or difference_limit <= 0:
        raise ValueError("Invalid comparison display limits")
    if mode == "Overlay":
        gray = (1 - opacity) * normalize(left, *limits) + opacity * normalize(right, *limits)
        return np.repeat((gray[..., None] * 255).astype(np.uint8), 3, axis=-1)
    if mode != "Difference":
        raise ValueError("Choose Overlay or Difference")
    delta = np.clip((right.astype(float) - left.astype(float)) / difference_limit, -1, 1)
    rgb = np.ones((*delta.shape, 3), float)
    rgb[..., 0] -= np.maximum(-delta, 0)
    rgb[..., 1] -= abs(delta)
    rgb[..., 2] -= np.maximum(delta, 0)
    rgb[~np.isfinite(left) | ~np.isfinite(right)] = [1, 0, 1]
    return (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
