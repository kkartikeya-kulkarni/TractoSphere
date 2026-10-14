"""Display-only math for scripts and the viewer; arrays are never modified.

Use robust_range(planes) for pooled finite 2nd–98th percentiles. Overlay RGBA
uses inclusive thresholds; mask outlines are the inner four-neighbor 2D edge.
"""

import math
from collections.abc import Iterable

import numpy as np

from ._viridis import VIRIDIS


def robust_range(planes: Iterable[np.ndarray]) -> tuple[float, float]:
    """Pool displayed native planes, ignoring nonfinite samples, with a safe span."""
    values = [p[np.isfinite(p)] for p in planes]
    values = np.concatenate(values) if values else np.array([])
    if not values.size:
        return (0.0, 1.0)
    low, high = map(float, np.percentile(values, [2, 98]))
    return (low, high) if high > low else (low, low + max(1.0, abs(low) * 1e-6))


def normalize(plane: np.ndarray, low: float, high: float) -> np.ndarray:
    """Map a finite ordered range into [0,1] without altering source intensities."""
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        raise ValueError("Display maximum must be finite and greater than minimum")
    return np.clip(
        np.nan_to_num((plane.astype(float) - low) / (high - low), nan=0, posinf=1, neginf=0), 0, 1
    )


def drag_range(start: tuple[float, float], dx: float, dy: float) -> tuple[float, float]:
    """Horizontal drag scales width exponentially; downward drag raises level."""
    low, high = start
    width = (high - low) * math.exp(max(-10, min(10, dx / 200)))
    level = (low + high) / 2 + dy * (high - low) / 200
    return level - width / 2, level + width / 2


def colors(values: np.ndarray, name: str) -> np.ndarray:
    """Use the standard viridis lookup or piecewise-linear gray/heat ramps."""
    if name == "viridis":
        table = np.asarray(VIRIDIS)
        indices = np.minimum((np.clip(values, 0, 1) * 256).astype(int), 255)
        return (table[indices] * 255).astype(np.uint8)
    anchors = {
        "blue-white-red": [(0, 70, 190), (255, 255, 255), (190, 30, 0)],
        "gray": [(0, 0, 0), (255, 255, 255)],
        "hot": [(0, 0, 0), (255, 0, 0), (255, 255, 0), (255, 255, 255)],
        "red-yellow": [(255, 0, 0), (255, 255, 0)],
    }[name]
    table = np.array(anchors)
    return np.stack(
        [np.interp(values, np.linspace(0, 1, len(table)), table[:, i]) for i in range(3)], axis=-1
    ).astype(np.uint8)


def overlay_rgba(
    plane: np.ndarray,
    *,
    mode: str = "Mask",
    colormap: str = "viridis",
    low: float = 0,
    high: float = 1,
    opacity: float = 0.5,
    outline: bool = False,
    color: tuple[int, int, int] = (255, 196, 80),
) -> np.ndarray:
    """Render one plane with transparent nonfinite/out-of-threshold samples."""
    if not np.isfinite([low, high]).all() or high < low:
        raise ValueError("Threshold maximum must be at least minimum")
    visible = np.isfinite(plane) & (plane >= low) & (plane <= high)
    if mode == "Mask":
        visible &= plane != 0
        if outline:
            padded = np.pad(visible, 1)
            interior = padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
            visible &= ~interior
        rgb = np.broadcast_to(np.array(color, dtype=np.uint8), (*plane.shape, 3))
    elif mode == "Colormap":
        rgb = colors(
            normalize(plane, low, high if high > low else low + max(1, abs(low) * 1e-6)), colormap
        )
    else:
        raise ValueError("Overlay mode must be Mask or Colormap")
    return np.dstack((rgb, visible.astype(np.uint8) * round(np.clip(opacity, 0, 1) * 255)))
