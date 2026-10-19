"""Bounded full-value inspection must respect large MIF storage traversal."""

import numpy as np
import pytest

from dmri_studio.core.scientific.imports import _scan


class RecordedArray:
    """Record requested blocks without altering the scientific array."""

    def __init__(self, values: np.ndarray):
        """Expose shape/strides exactly as the read-only MIF view does."""
        self.values = values
        self.shape = values.shape
        self.strides = values.strides
        self.requests = []
        self.sizes = []
        self.visits = np.zeros(values.shape, dtype=np.uint8)

    def __getitem__(self, key):
        """Return real values and record the bounded requested footprint."""
        self.requests.append(key)
        self.visits[key] += 1
        block = self.values[key]
        self.sizes.append(block.size)
        return block


@pytest.mark.parametrize("kind", ["dwi", "output"])
def test_volume_contiguous_inspection_finishes_each_volume(kind):
    """Negative spatial strides preserve exact statistics and full coverage."""
    values = np.arange(12 * 13 * 14 * 8, dtype=np.float32).reshape((12, 13, 14, 8), order="F")
    array = RecordedArray(values[::-1])
    before = values.copy()
    record = _scan(array, kind, lambda: False, lambda _: None)
    assert [key[3] for key in array.requests] == list(range(8))
    assert sum(array.sizes) == values.size
    assert max(array.sizes) <= 1_000_000
    assert (array.visits == 1).all()
    assert record["minimum"] == values.min() and record["maximum"] == values.max()
    assert record["nonzero_values"] == np.count_nonzero(values)
    assert record == _scan(np.array(values, order="C"), kind, lambda: False, lambda _: None)
    np.testing.assert_array_equal(values, before)


def test_wide_planes_remain_bounded_and_late_nonfinite_values_are_detected():
    """A plane larger than the memory bound is tiled; no sample-only shortcut."""
    values = np.zeros((1001, 1001, 1, 2), dtype=np.uint8, order="F")
    values[-1, -1, 0, -1] = 7
    array = RecordedArray(values)
    result = _scan(array, "dwi", lambda: False, lambda _: None)
    assert max(array.sizes) <= 1_000_000 and sum(array.sizes) == values.size
    assert (array.visits == 1).all()
    assert result["nonzero_values"] == 1 and result["maximum"] == 7
    broken = np.zeros((3, 4, 5, 7), dtype=np.float32, order="F")
    broken[-1, -1, -1, -1] = np.nan
    with pytest.raises(ValueError, match="Nonfinite"):
        _scan(RecordedArray(broken), "dwi", lambda: False, lambda _: None)


def test_interleaved_storage_retains_coupled_spatial_tiles():
    """Component-fast storage and probability checks must not use volume mode."""
    array = RecordedArray(np.ones((3, 4, 5, 8), dtype=np.float32, order="C"))
    result = _scan(array, "dwi", lambda: False, lambda _: None)
    assert all(key[-1] is Ellipsis for key in array.requests)
    assert sum(array.sizes) == np.prod(array.shape)
    assert result["nonzero_values"] == np.prod(array.shape)
    probability = np.full((3, 4, 5, 5), 0.2, dtype=np.float32, order="F")
    assert (
        _scan(probability, "5tt", lambda: False, lambda _: None)["nonzero_values"]
        == probability.size
    )
    probability[..., -1] = 0.8
    with pytest.raises(ValueError, match="sum to 1"):
        _scan(probability, "5tt", lambda: False, lambda _: None)
