"""Pure display math: synthetic arrays only; run python -m pytest tests/core."""

import numpy as np
import pytest

from dmri_studio.core.viewer_display import (
    colors,
    drag_range,
    normalize,
    overlay_rgba,
    robust_range,
)


def test_robust_range_and_normalization() -> None:
    """Pool finite planes, reject outliers, preserve negatives and input arrays."""
    plane = np.arange(100.0).reshape(10, 10)
    original = plane.copy()
    assert robust_range([plane, np.array([np.nan, np.inf])]) == pytest.approx((1.98, 97.02))
    assert robust_range([]) == (0, 1)
    assert robust_range([np.zeros((2, 2))]) == (0, 1)
    np.testing.assert_array_equal(normalize(np.array([-2, 0, 2, np.nan]), -1, 1), [0, 0.5, 1, 0])
    normalize(plane, 2, 98)
    np.testing.assert_array_equal(plane, original)
    with pytest.raises(ValueError):
        normalize(plane, 1, 1)


def test_window_level_drag() -> None:
    """Width remains positive; horizontal and vertical motions act independently."""
    assert drag_range((0, 100), 0, 100) == (50, 150)
    low, high = drag_range((0, 100), 200, 0)
    assert (low + high) / 2 == pytest.approx(50)
    assert high - low == pytest.approx(100 * np.e)
    assert drag_range((0, 100), -10000, 0)[1] > drag_range((0, 100), -10000, 0)[0]


@pytest.mark.parametrize("colormap", ["gray", "hot", "red-yellow", "viridis"])
def test_colormap_thresholds_and_alpha(colormap: str) -> None:
    """Inclusive endpoints and scalar colors agree with the legend lookup."""
    plane = np.array([[-1, 0, 0.5, 1, 2, np.nan]])
    rgba = overlay_rgba(plane, mode="Colormap", colormap=colormap, low=0, high=1, opacity=0.5)
    np.testing.assert_array_equal(rgba[0, :, 3], [0, 128, 128, 128, 0, 0])
    np.testing.assert_array_equal(rgba[0, 1:4, :3], colors(np.array([0, 0.5, 1]), colormap))
    assert not np.array_equal(rgba[0, 1, :3], rgba[0, 3, :3])


def test_mask_nonzero_outline_and_equal_threshold() -> None:
    """Mask interiors disappear in outline mode; equal thresholds select a label."""
    plane = np.ones((5, 5))
    full = overlay_rgba(plane, low=1, high=1)
    edge = overlay_rgba(plane, low=1, high=1, outline=True)
    assert np.count_nonzero(full[..., 3]) == 25
    assert np.count_nonzero(edge[..., 3]) == 16
    assert edge[2, 2, 3] == 0
    plane[0, 0] = 0
    assert overlay_rgba(plane)[0, 0, 3] == 0
    with pytest.raises(ValueError):
        overlay_rgba(plane, low=2, high=1)
