"""Atlas ID, probability, coordinate and volumetric-intersection contracts."""

import json
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.atlas import Atlas, filter_streamlines, intersects_mask, label_table, sha256
from dmri_studio.core.scene import ImageVolume, TractGroup


def image(path, data, affine=None, code=1):
    obj = nib.Nifti1Image(np.asarray(data), np.eye(4) if affine is None else affine)
    obj.set_qform(obj.affine, code)
    obj.set_sform(obj.affine, code)
    obj.header.set_xyzt_units("mm")
    nib.save(obj, path)
    return ImageVolume(path)


def test_discrete_names_ids_volume_lazy(tmp_path):
    data = np.zeros((7, 8, 9), np.int16)
    data[1:3, 2:4, 3:5] = 2
    data[4:6, 2:4, 3:5] = 99
    ref = image(tmp_path / "atlas.nii.gz", data, np.diag([2, 3, 4, 1]))
    (tmp_path / "atlas.tsv").write_text("index\tname\tcolor\n2\tLeft Example\t#ff8800\n")
    atlas = Atlas(ref.path, ref)
    assert [r["label_id"] for r in atlas.records] == [2, 99]
    assert atlas.records[0]["volume_mm3"] == pytest.approx(8 * 24)
    assert atlas.records[1]["name"] == "Region 99"
    a, b = list(atlas.regions())
    assert "data" not in a and not a["data"].flags.writeable
    a["data"] = a["data"].copy()
    a["data"][0, 0, 0] = 1
    assert not atlas.mask(2)[0, 0, 0] and b["data"].sum() == 8


def test_probability_threshold_and_fsl_index(tmp_path):
    data = np.zeros((5, 6, 7, 3), np.float32)
    data[1:3, 1:3, 1:3, 0] = 80
    data[3:4, 1:3, 1:3, 1] = 40
    ref = image(tmp_path / "prob.nii", data)
    xml = tmp_path / "labels.xml"
    xml.write_text(
        '<atlas><header><type>Probabilistic</type><name>Example</name></header><data><label index="0">First</label><label index="1">Second</label></data></atlas>'
    )
    a = Atlas(ref.path, ref, table=xml, threshold=0.5)
    assert [r["label_id"] for r in a.records] == [0] and a.omitted == [1, 2]
    assert a.records[0]["name"] == "First"
    names, _, _ = label_table(xml, False)
    assert names[1] == "First"
    a = Atlas(ref.path, ref, table=xml, threshold=0.25)
    assert len(a.records) == 2
    with pytest.raises(ValueError, match="invalid values"):
        Atlas(ref.path, ref, probability_scale=1)


def test_reject_continuous_empty_invalid_and_spaces(tmp_path):
    ref = image(tmp_path / "ref.nii", np.ones((4, 5, 6), np.float32))
    continuous = image(tmp_path / "p.nii", np.full((4, 5, 6), 0.4, np.float32))
    with pytest.raises(ValueError, match="Continuous"):
        Atlas(continuous.path, ref, same_space=True)
    assert len(Atlas(continuous.path, ref, kind="probability", same_space=True).records) == 1
    empty = image(tmp_path / "empty.nii", np.zeros((4, 5, 6), np.int16))
    with pytest.raises(ValueError, match="no nonempty"):
        Atlas(empty.path, ref, same_space=True)
    mni = image(tmp_path / "mni.nii", np.ones((4, 5, 6), np.int16), code=4)
    with pytest.raises(ValueError, match="space mismatch"):
        Atlas(mni.path, ref, same_space=True)
    with pytest.raises(ValueError, match="Confirm"):
        Atlas(continuous.path, ref, kind="probability")


def test_verified_affine_resampling(tmp_path):
    data = np.zeros((5, 5, 5), np.int16)
    data[1, 1, 1] = 7
    source = image(tmp_path / "mni.nii", data, code=4)
    ref = image(tmp_path / "native.nii", np.zeros((5, 5, 5), np.int16))
    matrix = np.eye(4)
    matrix[0, 3] = 1
    record = dict(
        schema="tractosphere.verified-affine/1",
        verified=True,
        evidence="Known unit-test coordinate translation, not subject registration",
        source_sha256=sha256(source.path),
        reference_sha256=sha256(ref.path),
        matrix_rasmm=matrix.tolist(),
    )
    transform = tmp_path / "transform.json"
    transform.write_text(json.dumps(record))
    atlas = Atlas(source.path, ref, transform=transform)
    assert atlas.mask(7)[2, 1, 1] and atlas.mask(7).sum() == 1
    record["reference_sha256"] = "wrong"
    transform.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="SHA-256"):
        Atlas(source.path, ref, transform=transform)


def test_exact_sparse_segment_and_multi_roi_logic(tmp_path):
    ref = image(tmp_path / "ref.nii", np.zeros((10, 10, 10), np.uint8))
    a = np.zeros(ref.shape, np.uint8)
    a[4, 4, 4] = 1
    b = np.zeros(ref.shape, np.uint8)
    b[7, 4, 4] = 1
    lines = [
        np.array([[0, 4, 4], [9, 4, 4]]),
        np.array([[0, 4, 4], [5, 4, 4]]),
        np.array([[0, 0, 0], [9, 0, 0]]),
    ]
    assert intersects_mask(lines[0], a, ref)  # Neither supplied vertex is in the ROI.
    g = TractGroup(Path("test.tck"), lines, range(3), 3)
    rois = [dict(data=a, role="Include"), dict(data=b, role="Include")]
    assert filter_streamlines(g, rois, ref) == {0}
    assert filter_streamlines(g, rois, ref, False) == {0, 1}
    rois[1]["role"] = "Exclude"
    assert filter_streamlines(g, rois, ref) == {1}


def test_cli_region_export_and_readonly(tmp_path, capsys):
    from dmri_studio.cli.main import main

    data = np.zeros((5, 5, 5), np.int16)
    data[1:3, 1:3, 1:3] = 7
    ref = image(tmp_path / "atlas.nii.gz", data)
    before = sha256(ref.path)
    report = tmp_path / "report.json"
    output = tmp_path / "regions"
    args = [
        "atlas",
        str(ref.path),
        "--reference",
        str(ref.path),
        "--report-json",
        str(report),
        "--export-dir",
        str(output),
    ]
    assert main(args) == 0
    payload = json.loads(report.read_text())
    assert payload["regions"][0]["label_id"] == 7
    saved = json.loads((output / "region-7.json").read_text())
    assert saved["roi"]["name"] == "Region 7"
    assert np.count_nonzero(nib.load(output / "region-7.nii.gz").dataobj) == 8
    assert main(args) == 2
    assert "new destination" in capsys.readouterr().err
    assert sha256(ref.path) == before


def test_qform_sform_conflict_and_xml_paths(tmp_path):
    from dmri_studio.core.atlas import find_table, xml_images

    ref = image(tmp_path / "atlas.nii.gz", np.ones((3, 4, 5), np.int16))
    xml = tmp_path / "other.xml"
    xml.write_text(
        "<atlas><header><images><imagefile>/atlas</imagefile></images>"
        '<type>Label</type></header><data><label index="1">One</label></data></atlas>'
    )
    assert xml_images(xml) == [ref.path] and find_table(ref.path) == xml
    obj = nib.load(ref.path)
    shifted = obj.affine.copy()
    shifted[0, 3] += 5
    obj.set_qform(shifted, 1)
    nib.save(obj, ref.path)
    with pytest.raises(ValueError, match="Conflicting qform"):
        Atlas(ref.path, ImageVolume(ref.path))


def test_cli_tract_roles_and_space_confirmation(tmp_path):
    from nibabel.streamlines import TckFile, Tractogram

    from dmri_studio.cli.main import main

    data = np.zeros((5, 5, 5), np.int16)
    data[2, 2, 2] = 4
    ref = image(tmp_path / "atlas.nii", data)
    tract = tmp_path / "source.tck"
    lines = [
        np.array([[0, 2, 2], [4, 2, 2]], dtype=np.float32),
        np.array([[0, 0, 0], [4, 0, 0]], dtype=np.float32),
    ]
    TckFile(Tractogram(lines, affine_to_rasmm=np.eye(4))).save(tract)
    dest = tmp_path / "selected.tck"
    args = [
        "atlas",
        str(ref.path),
        "--reference",
        str(ref.path),
        "--tract",
        str(tract),
        "--tract-output",
        str(dest),
        "--include",
        "4",
    ]
    assert main(args) == 2 and not dest.exists()
    assert main(args + ["--tract-same-space"]) == 0
    saved = nib.streamlines.load(dest).tractogram.streamlines
    assert len(saved) == 1 and np.array_equal(saved[0], lines[0])
    assert json.loads(Path(str(dest) + ".json").read_text())["source_ids"] == [0]
