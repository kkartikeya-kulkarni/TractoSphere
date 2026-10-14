"""Spatial/data preservation and selected-derivative editing contracts."""

from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.scene import ImageVolume, TractGroup, save_new_image


@pytest.fixture
def image(tmp_path):
    affine = np.array([[-2, 0, 0, 12], [0, 3, 0.2, -4], [0, 0, 4, 7], [0, 0, 0, 1.0]])
    p = tmp_path / "reference.nii.gz"
    nib.save(
        nib.Nifti1Image(np.arange(6 * 7 * 8 * 3, dtype=np.float32).reshape(6, 7, 8, 3), affine), p
    )
    return ImageVolume(p)


def test_image_native_roundtrip_and_volume(image):
    p = np.array([[1, 2, 3], [4, 5, 6]])
    assert np.allclose(image.voxels(image.world(p)), p)
    assert image.count == 3
    assert image.volume(1).shape == (6, 7, 8)
    assert image.volume(2)[0, 0, 0] == 2
    with pytest.raises(IndexError):
        image.volume(3)


@pytest.mark.parametrize("extension", [".tck", ".trk"])
def test_export_roundtrip_selected_edits(image, tmp_path, extension):
    lines = [
        np.array([[0, 0, -1], [0, 0, 1], [0, 0, 3]], np.float32),
        np.array([[1, 1, 1], [3, 3, 3]], np.float32),
    ]
    group = TractGroup(Path("original.tck"), lines, np.arange(2), 2)
    group.selected = {0}
    group.edit("cut", np.zeros(3), np.array([0, 0, 1]))
    assert np.allclose(group.line(0), [[0, 0, -1], [0, 0, 0]])
    assert np.array_equal(group.line(1), lines[1])
    group.undo()
    assert np.array_equal(group.line(0), lines[0])
    group.undo(True)
    assert len(group.line(0)) == 2
    group.edit("delete")
    assert group.deleted == {0}
    out = tmp_path / ("edit" + extension)
    group.export(out, image)
    result = TractGroup.load(out)
    assert len(result.streamlines) == 1
    assert np.allclose(result.streamlines[0], lines[1], atol=1e-5)
    with pytest.raises(FileExistsError):
        group.export(out, image)
    assert len(lines[0]) == 3


def test_lazy_limit_deterministic_and_reject_invalid(tmp_path):
    lines = [np.array([[i, 0, 0], [i, 1, 1]], np.float32) for i in range(120)]
    p = tmp_path / "many.tck"
    nib.streamlines.save(nib.streamlines.Tractogram(lines, affine_to_rasmm=np.eye(4)), p)
    a = TractGroup.load(p, limit=17)
    b = TractGroup.load(p, limit=17)
    assert a.total == 120 and len(a.streamlines) == 17
    assert np.array_equal(a.source_ids, b.source_ids)
    assert a.statistics()["sampled"]
    with pytest.raises(ValueError):
        a.edit("delete")


def test_roi_segment_intersection_not_only_vertices(image):
    line = image.world(np.array([[0, 2, 3], [5, 2, 3]]))
    group = TractGroup(Path("original.tck"), [line], np.array([0]), 1)
    roi = np.zeros(image.shape[:3], np.uint8)
    roi[2, 2, 3] = 1
    group.select_roi(roi, image)
    assert group.selected == {0}
    group.edit("hide")
    assert group.hidden == {0}
    assert not group.deleted


def test_roi_save_geometry_no_source_overwrite(image, tmp_path):
    p = tmp_path / "mask.nii.gz"
    data = np.zeros(image.shape[:3], np.uint8)
    data[1, 2, 3] = 1
    save_new_image(p, data, image)
    loaded = nib.load(p)
    assert np.allclose(loaded.affine, image.affine)
    assert loaded.shape == image.shape[:3]
    assert loaded.get_data_dtype() == np.uint8
    with pytest.raises(FileExistsError):
        save_new_image(image.path, data, image)


def test_view_cli_requires_explicit_input(capsys):
    from dmri_studio.cli.main import main

    assert main(["view"]) == 2
    assert main(["view", "image.nii", "--workspace", "view.json"]) == 2
    assert "Supply an image" in capsys.readouterr().err


def test_ai_command_is_removed(capsys):
    from dmri_studio.cli.main import main

    assert main(["ai"]) == 2
    assert "invalid choice" in capsys.readouterr().err


def test_physical_units_conversion_and_source_geometry(tmp_path):
    p = tmp_path / "metres.nii.gz"
    image = nib.Nifti1Image(np.ones((3, 4, 5), dtype=np.float32), np.diag([0.002, 0.003, 0.004, 1]))
    image.header.set_xyzt_units("meter")
    nib.save(image, p)
    ref = ImageVolume(p)
    assert np.allclose(ref.world([1, 1, 1]), [2, 3, 4])
    assert ref.physical_units_known
    save_new_image(tmp_path / "mask.nii.gz", np.ones((3, 4, 5), np.uint8), ref)
    saved = ImageVolume(tmp_path / "mask.nii.gz")
    assert np.allclose(saved.affine, ref.affine)
    assert saved.image.header.get_xyzt_units()[0] == "meter"
