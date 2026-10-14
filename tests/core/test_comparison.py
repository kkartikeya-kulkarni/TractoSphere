"""Meaningful signed-difference, coverage and immutable display regressions."""

import numpy as np
import pytest

from dmri_studio.core.comparison import comparison_rgb, difference_metrics


def test_metrics_sign_mask_and_nonfinite():
    a = np.array([1.0, 2.0, np.nan, 999.0])
    b = np.array([2.0, 4.0, 3.0, -999.0])
    report = difference_metrics(a, b, np.array([1, 1, 1, 0]))
    assert report["nonfinite_pairs"] == 1
    assert report["finite_pairs"] == 2
    assert report["mean_difference"] == 1.5
    assert report["relative_L2"] == 1
    assert report["correlation"] == pytest.approx(1)


def test_difference_colors_and_sources_unchanged():
    a = np.zeros((1, 4))
    b = np.array([[-2.0, 0.0, 2.0, np.nan]])
    saved = b.copy()
    result = comparison_rgb(a, b, "Difference", (0, 2), difference_limit=2)
    assert result.tolist() == [[[0, 0, 255], [255, 255, 255], [255, 0, 0], [255, 0, 255]]]
    assert np.array_equal(b, saved, equal_nan=True)


def test_overlay_endpoints_and_invalid_geometry():
    a, b = np.zeros((2, 2)), np.ones((2, 2))
    assert (comparison_rgb(a, b, "Overlay", (0, 1), opacity=0) == 0).all()
    assert (comparison_rgb(a, b, "Overlay", (0, 1), opacity=1) == 255).all()
    with pytest.raises(ValueError):
        difference_metrics(a, b[:1])
    with pytest.raises(ValueError):
        comparison_rgb(a, b, "Difference", (0, 1), difference_limit=0)


def test_empty_and_constant_metrics_are_not_fake_correlations():
    assert difference_metrics(np.zeros(2), np.ones(2))["correlation"] is None
    assert difference_metrics(np.zeros(2), np.ones(2))["relative_L2"] is None
    assert difference_metrics(np.array([np.nan]), np.ones(1))["RMSE"] is None
