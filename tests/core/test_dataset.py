"""Exercise standalone proxy reads with synthetic sources in pytest temp folders.

No real data is copied. A recording proxy fails if a method asks for the entire
array, making lazy-access behavior testable independently of timing and RAM.
"""

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.dataset import (
    DatasetClosedError,
    DatasetPaths,
    DatasetReadError,
    DwiDataset,
    SourceChangedError,
    SourceFingerprint,
)
from dmri_studio.core.gradients import GradientTable
from dmri_studio.core.validation import CHECK_CODES, CheckResult, ValidationReport


class RecordingProxy:
    """Delegate indexing but forbid implicit conversion of the complete proxy."""

    def __init__(self, proxy: Any) -> None:
        """Keep the real scaled proxy and a record of exact index requests."""
        self.proxy = proxy
        self.shape = proxy.shape
        self.keys: list[tuple[Any, ...]] = []

    def __getitem__(self, key: tuple[Any, ...]) -> np.ndarray:
        """Record only the selected region before reading its real synthetic data."""
        self.keys.append(key)
        return self.proxy[key]

    def __array__(self, *args: Any, **kwargs: Any) -> None:
        """Fail if any implementation tries to materialize the entire image."""
        raise AssertionError("Whole image conversion is forbidden")


@pytest.fixture
def dataset_case(tmp_path: Path) -> tuple[DwiDataset, np.ndarray, RecordingProxy]:
    """Create a scaled four-volume NIfTI and a metadata-complete dataset handle."""
    values = np.arange(6 * 7 * 8 * 4, dtype=np.int16).reshape((6, 7, 8, 4))
    image = nib.Nifti1Image(values, np.diag([1.25, 1.25, 1.25, 1]))
    image.header.set_xyzt_units("mm")
    image.header.set_slope_inter(2, -7)
    path = tmp_path / "source.nii"
    nib.save(image, path)
    bval, bvec = tmp_path / "bvals", tmp_path / "bvecs"
    bval.write_text("1000 5 1000 0")
    bvec.write_text("1 0 0 0\n0 0 1 0\n0 0 0 0\n")
    paths = DatasetPaths(path, bval, bvec)
    loaded = nib.load(path, mmap="r", keep_file_open=False)
    recorder = RecordingProxy(loaded.dataobj)
    loaded._dataobj = recorder
    report = ValidationReport(tuple(CheckResult(code, "pass", "Checked") for code in CHECK_CODES))
    dataset = DwiDataset(
        id="synthetic",
        paths=paths,
        image=loaded,
        gradients=GradientTable(
            np.array([1000, 5, 1000, 0]), np.array([[1, 0, 0], [0, 0, 0], [0, 1, 0], [0, 0, 0]])
        ),
        validation=report,
        voxel_sizes_native=(1.25, 1.25, 1.25),
        spatial_units="mm",
        affine=loaded.affine,
        affine_source="sform",
        axis_codes=("R", "A", "S"),
        fingerprints={role: SourceFingerprint.capture(p) for role, p in paths.supplied().items()},
        metadata={"nifti_version": 1},
        sidecar={"Unknown": [1, 2]},
    )
    return dataset, values.astype(np.float64) * 2 - 7, recorder


@pytest.mark.parametrize("axis", [0, 1, 2])
@pytest.mark.parametrize("dtype", ["float32", "float64"])
def test_slice_reads_only_requested_plane(dataset_case: tuple, axis: int, dtype: str) -> None:
    """Scaling and native axis order agree, without calling read_volume()."""
    dataset, expected, recorder = dataset_case
    plane = dataset.read_slice(1, axis=axis, index=2, dtype=dtype)
    key = [slice(None), slice(None), slice(None), 1]
    key[axis] = 2
    np.testing.assert_array_equal(plane, expected[tuple(key)])
    assert plane.dtype == np.dtype(dtype)
    assert plane.flags.owndata
    assert recorder.keys == [tuple(key)]
    assert sum(isinstance(k, int) for k in recorder.keys[0]) == 2


def test_default_plane_volume_and_summary_do_not_prefetch(dataset_case: tuple) -> None:
    """First b0-like volume is index 1; summary needs no voxel reads."""
    dataset, expected, recorder = dataset_case
    assert dataset.default_preview_volume() == 1
    summary = dataset.summary()
    assert summary["b0_count"] == 2
    assert summary["weighted_count"] == 2
    assert recorder.keys == []
    np.testing.assert_array_equal(dataset.read_slice(1), expected[:, :, 4, 1])


def test_explicit_block_volume_and_lifecycle(dataset_case: tuple) -> None:
    """Explicit materialization is scoped; returned copies outlive close()."""
    dataset, expected, _ = dataset_case
    region = (slice(1, 3), slice(2, 5), slice(3, 6))
    with dataset:
        block = dataset.read_block(2, region, dtype="float64")
        np.testing.assert_array_equal(block, expected[(*region, 2)])
        np.testing.assert_array_equal(dataset.read_volume(0), expected[..., 0])
    block[0, 0, 0] = -99
    dataset.close()
    with pytest.raises(DatasetClosedError):
        dataset.read_slice(0)
    with pytest.raises(DatasetClosedError):
        dataset.__enter__()


@pytest.mark.parametrize(
    "kwargs,exception",
    [
        ({"volume_index": -1}, IndexError),
        ({"volume_index": 4}, IndexError),
        ({"volume_index": True}, TypeError),
        ({"volume_index": 0, "axis": 3}, IndexError),
        ({"volume_index": 0, "index": 8}, IndexError),
        ({"volume_index": 0, "index": 1.5}, TypeError),
        ({"volume_index": 0, "dtype": "int16"}, ValueError),
    ],
)
def test_invalid_slice_selection_never_reads(
    dataset_case: tuple, kwargs: dict, exception: type
) -> None:
    """Bad selection does not wrap around or accidentally expand a read."""
    dataset, _, recorder = dataset_case
    with pytest.raises(exception):
        dataset.read_slice(**kwargs)
    assert not recorder.keys


def test_source_change_including_gradients_invalidates_read(dataset_case: tuple) -> None:
    """An old report cannot be paired with an edited gradient file."""
    dataset, _, recorder = dataset_case
    dataset.paths.bval.write_text("0 0 0 0\n")
    with pytest.raises(SourceChangedError, match="bval"):
        dataset.read_slice(0)
    assert not recorder.keys


def test_symlink_retarget_is_detected(tmp_path: Path) -> None:
    """Source identity includes the lexical path and its resolved target."""
    first, second, link = (tmp_path / name for name in ("first", "second", "link"))
    first.write_text("one")
    second.write_text("two")
    link.symlink_to(first)
    fingerprint = SourceFingerprint.capture(link)
    assert fingerprint.unchanged(link)
    link.unlink()
    link.symlink_to(second)
    assert not fingerprint.unchanged(link)


def test_metadata_cannot_be_mutated(dataset_case: tuple) -> None:
    """Metadata and gradients remain stable across UI/script consumers."""
    dataset, _, _ = dataset_case
    with pytest.raises(ValueError):
        dataset.affine.setflags(write=True)
    with pytest.raises(FrozenInstanceError):
        dataset.spatial_units = "meter"
    with pytest.raises(TypeError):
        dataset.sidecar["Unknown"][0] = 9
    summary = dataset.summary()
    summary["shape"][0] = 999
    assert dataset.shape[0] == 6


def test_proxy_read_error_is_typed(dataset_case: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    """A delayed filesystem failure has a meaningful core exception."""
    dataset, _, _ = dataset_case

    def fail_read(self: Any, key: Any) -> None:
        """Simulate a source/device read error after initial validation."""
        raise OSError("device read failed")

    monkeypatch.setattr(RecordingProxy, "__getitem__", fail_read)
    with pytest.raises(DatasetReadError, match="device read failed"):
        dataset.read_slice(0)


def test_blank_paths_remain_missing_and_paths_resolve(tmp_path: Path) -> None:
    """Empty strings must not accidentally mean the current working directory."""
    assert DatasetPaths("", None, "").supplied() == {}
    paths = DatasetPaths(tmp_path / "a", tmp_path / "b", tmp_path / "c")
    assert paths.resolved().dwi.is_absolute()
