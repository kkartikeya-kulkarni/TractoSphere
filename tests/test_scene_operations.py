"""Independent numerical contracts for indexing, attributes, geometry and edits."""

from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.scene import ImageVolume, TractGroup
from dmri_studio.core.scene_operations import (
    paint_roi,
    polygon_roi,
    projected_selection,
    read_mesh,
    resample_overlay,
)


def ref(path, shape=(9, 10, 11), affine=None):
    image = nib.Nifti1Image(np.ones(shape, np.float32), np.eye(4) if affine is None else affine)
    image.header.set_xyzt_units("mm")
    nib.save(image, path)
    return ImageVolume(path)


@pytest.mark.parametrize("extension", ["tck", "trk"])
def test_index_matches_independent_nibabel(tmp_path, extension):
    lines = [np.array([[0, 1, 2], [1, 2, 3], [3, 3, 7]], np.float32) + i for i in range(41)]
    p = tmp_path / ("input." + extension)
    tract = nib.streamlines.Tractogram(lines, affine_to_rasmm=np.eye(4))
    if extension == "trk":
        hdr = nib.streamlines.TrkFile.create_empty_header()
        hdr["voxel_to_rasmm"] = np.array(
            [[-2, 0, 0, 25], [0, 3, 0, -10], [0, 0, 4, 8], [0, 0, 0, 1]], np.float32
        )
        hdr["dimensions"] = [20, 30, 40]
        hdr["voxel_sizes"] = [2, 3, 4]
        hdr["voxel_order"] = "LAS"
        tract = nib.streamlines.TrkFile(tract, hdr)
    nib.streamlines.save(tract, p)
    independent = nib.streamlines.load(p).tractogram.streamlines
    indexed = TractGroup.load(p)
    assert indexed.total == len(lines)
    for a, b in zip(indexed.streamlines, independent, strict=True):
        assert np.allclose(a, b, atol=1e-6)
    expected = [np.linalg.norm(np.diff(x, axis=0), axis=1).sum() for x in independent]
    assert np.allclose(indexed._lengths, expected)
    indexed.hidden = {0, 3, 4, 9}
    indexed.deleted = {10}
    ids = indexed.display_ids(100)
    assert ids == [i for i in range(41) if i not in indexed.hidden | indexed.deleted]
    assert len(indexed.display_ids(7)) == 7


def test_index_trk_metadata_export_and_cut(tmp_path):
    p = tmp_path / "attributes.trk"
    line = np.array([[0, 0, -2], [0, 0, 0], [0, 0, 2], [0, 0, 4]], np.float32)
    t = nib.streamlines.Tractogram(
        [line],
        data_per_point={"FA": [np.array([[0.1], [0.3], [0.5], [0.7]], np.float32)]},
        data_per_streamline={"weight": np.array([[2.5]], np.float32)},
        affine_to_rasmm=np.eye(4),
    )
    nib.streamlines.save(t, p)
    g = TractGroup.load(p)
    reference = ref(tmp_path / "ref.nii")
    g.export(tmp_path / "roundtrip.trk", reference)
    check = nib.streamlines.load(tmp_path / "roundtrip.trk").tractogram
    assert np.allclose(check.data_per_point["FA"][0], t.data_per_point["FA"][0])
    assert np.allclose(check.data_per_streamline["weight"], [[2.5]])
    g.selected = {0}
    g.edit("cut", np.array([0, 0, 1]), np.array([0, 0, 1]), keep="Keep end")
    g.export(tmp_path / "cut.trk", reference)
    cut = nib.streamlines.load(tmp_path / "cut.trk").tractogram
    assert np.allclose(cut.streamlines[0], [[0, 0, 1], [0, 0, 2], [0, 0, 4]])
    assert np.allclose(cut.data_per_point["FA"][0], [[0.4], [0.5], [0.7]])
    with pytest.raises(ValueError, match="metadata"):
        g.export(tmp_path / "loss.tck", reference)
    assert not (tmp_path / "loss.tck").exists()


def test_source_change_and_bad_records(tmp_path):
    p = tmp_path / "valid.tck"
    nib.streamlines.save(
        nib.streamlines.Tractogram([np.array([[0, 0, 0], [1, 1, 1]])], affine_to_rasmm=np.eye(4)), p
    )
    g = TractGroup.load(p)
    with p.open("ab") as f:
        f.write(b"bad")
    with pytest.raises(ValueError, match="changed"):
        g.streamlines.verify_source()
    with pytest.raises(ValueError, match="Truncated|buffer size"):
        TractGroup.load(p)


def test_roi_brush_polygon_resampling(tmp_path):
    data = np.zeros((9, 10, 11), np.uint8)
    paint_roi(data, (4, 4, 4), 2, 2, 1, True)
    assert data[:, :, 3].any() and data[:, :, 5].any()
    paint_roi(data, (4, 4, 4), 2, 2, 0, False)
    assert not data[:, :, 4].any() and data[:, :, 3].any()
    polygon_roi(data, [(1, 1, 4), (5, 1, 4), (5, 5, 4), (1, 5, 4)], 2, 4)
    assert data[2, 2, 4] and not data[7, 7, 4]
    a = ref(tmp_path / "a.nii")
    b = ref(tmp_path / "b.nii", (5, 5, 6), np.diag([2, 2, 2, 1]))
    sampled = resample_overlay(b, a, 0)
    assert sampled.shape == a.shape and set(np.unique(sampled)) <= {0, 1}
    b.image.header.set_xyzt_units("unknown")
    with pytest.raises(ValueError, match="documented"):
        resample_overlay(b, a, 0)


def test_projected_selection_crossing_and_angle():
    lines = [
        np.array([[-0.8, 0, 0], [0.8, 0, 0]]),
        np.array([[0, -0.8, 0], [0, 0.8, 0]]),
        np.array([[0.8, -0.8, 0], [0.8, 0.8, 0]]),
    ]
    g = TractGroup(Path("test.tck"), lines, np.arange(3), 3)
    # Identity test projection is an orthographic clip matrix, with positive w.
    assert projected_selection(g, np.eye(4), [0, -0.5], [0, 0.5]) == {0}
    assert projected_selection(g, np.eye(4), [-0.1, -0.1], [0.1, 0.1], True) == {0, 1}
    assert projected_selection(
        g, np.eye(4), [-0.1, -0.1], [0.1, 0.1], True, 10, np.array([1, 0, 0])
    ) == {0}


def test_mesh_coordinate_transform(tmp_path):
    from nibabel.gifti import GiftiCoordSystem, GiftiDataArray, GiftiImage

    coords = GiftiCoordSystem(dataspace=1, xformspace=1, xform=np.diag([2, 2, 2, 1]))
    image = GiftiImage(
        darrays=[
            GiftiDataArray(
                np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32),
                intent="NIFTI_INTENT_POINTSET",
                coordsys=coords,
            ),
            GiftiDataArray(np.array([[0, 1, 2]], np.int32), intent="NIFTI_INTENT_TRIANGLE"),
        ]
    )
    p = tmp_path / "mesh.surf.gii"
    nib.save(image, p)
    vertices, faces = read_mesh(p)
    assert np.allclose(vertices, [[0, 0, 0], [2, 0, 0], [0, 2, 0]])
    assert faces.tolist() == [[0, 1, 2]]


def test_freesurfer_surface_to_scanner_space(tmp_path):
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32)
    faces = np.array([[0, 1, 2]], np.int32)
    volume = [10, 12, 14]
    zoom = [2, 3, 4]
    info = dict(
        head=np.array([2, 0, 20]),
        valid="1",
        filename="reference.mgz",
        volume=volume,
        voxelsize=zoom,
        xras=[1, 0, 0],
        yras=[0, 1, 0],
        zras=[0, 0, 1],
        cras=[11, 12, 13],
    )
    p = tmp_path / "lh.white"
    nib.freesurfer.write_geometry(p, vertices, faces, volume_info=info)
    actual, triangles = read_mesh(p)
    # With identity direction cosines, tkregister RAS rotates axes then adds CRAS.
    expected = vertices @ np.array([[-1, 0, 0], [0, 0, 1], [0, -1, 0]]) + [11, 12, 13]
    assert np.allclose(actual, expected)
    assert np.array_equal(triangles, faces)


def test_cut_keeps_correct_side_and_selected_only(tmp_path):
    lines = [np.array([[0, 0, -2], [0, 0, 0], [0, 0, 4]], np.float32)] * 2
    g = TractGroup(Path("example.tck"), lines, range(2), 2)
    g.selected = {0}
    g.edit("cut", np.zeros(3), np.array([0, 0, 1]), keep="Keep longer part")
    assert set(g.cuts) == {0}
    assert np.allclose(g.line(0), [[0, 0, 0], [0, 0, 4]])
    assert np.array_equal(g.line(1), lines[1])
    g.undo()
    assert np.array_equal(g.line(0), lines[0])
    g.undo(True)
    assert np.allclose(g.line(0), [[0, 0, 0], [0, 0, 4]])
