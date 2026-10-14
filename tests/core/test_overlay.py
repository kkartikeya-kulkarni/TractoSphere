"""Same-space validation and lazy 3D/4D overlays; all files are temporary."""

from dataclasses import replace
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
from test_dataset import RecordingProxy, dataset_case  # noqa: F401

from dmri_studio.core.overlay import OverlayDataset, validate_overlay_space


def test_matching_and_physical_units(dataset_case: tuple) -> None:  # noqa: F811
    """Convert both affines to mm, including translations; accept <=0.01 mm."""
    dwi, _, _ = dataset_case
    assert validate_overlay_space(dwi.shape, dwi.affine, "mm", dwi) == 0
    meters = dwi.affine.copy()
    meters[:3] *= 0.001
    assert validate_overlay_space(dwi.shape[:3], meters, "meter", dwi) == pytest.approx(0)
    shifted = dwi.affine.copy()
    shifted[0, 3] += 0.0099
    assert validate_overlay_space(dwi.shape[:3], shifted, "mm", dwi) == pytest.approx(0.0099)


@pytest.mark.parametrize(
    "kind", ["shape", "translation", "scale", "shear", "unknown", "fallback", "singular", "nan"]
)
def test_refuse_incompatible_space(dataset_case: tuple, kind: str) -> None:  # noqa: F811
    """Corner checks catch small rotations/scales/shears that center checks miss."""
    dwi, _, _ = dataset_case
    shape, affine, units = dwi.shape[:3], dwi.affine.copy(), "mm"
    if kind == "shape":
        shape = (5, 7, 8)
    elif kind == "translation":
        affine[0, 3] = 0.02
    elif kind == "scale":
        affine[0, 0] += 0.003
    elif kind == "shear":
        affine[0, 1] = 0.003
    elif kind == "unknown":
        units = "unknown"
    elif kind == "fallback":
        dwi = replace(dwi, image=dwi._image, affine_source="fallback")
    elif kind == "singular":
        affine[0] = 0
    else:
        affine[0, 3] = np.nan
    with pytest.raises(ValueError):
        validate_overlay_space(shape, affine, units, dwi)


@pytest.mark.parametrize("four_d", [False, True])
def test_lazy_overlay_volume_and_source_change(
    dataset_case: tuple,  # noqa: F811
    tmp_path: Path,
    four_d: bool,  # noqa: F811
) -> None:  # noqa: F811
    """Read selected planes only; preserve NIfTI scaling and strict volume bounds."""
    dwi, _, _ = dataset_case
    shape = dwi.shape[:3] + ((2,) if four_d else ())
    data = np.arange(np.prod(shape), dtype=np.int16).reshape(shape)
    image = nib.Nifti1Image(data, dwi.affine)
    image.header.set_xyzt_units("mm")
    image.header.set_slope_inter(2, -1)
    path = tmp_path / "overlay.nii"
    nib.save(image, path)
    overlay = OverlayDataset.load(path, dwi)
    proxy = RecordingProxy(overlay.image.dataobj)
    overlay.image._dataobj = proxy
    assert overlay.n_volumes == (2 if four_d else 1)
    volume = 1 if four_d else 0
    expected = data[2, :, :, volume] if four_d else data[2]
    np.testing.assert_array_equal(overlay.read_slice(volume, axis=0, index=2), expected * 2 - 1)
    assert len(proxy.keys) == 1
    with pytest.raises(IndexError):
        overlay.read_slice(overlay.n_volumes)
    path.unlink()
    with pytest.raises(ValueError, match="source changed"):
        overlay.read_slice(0)
