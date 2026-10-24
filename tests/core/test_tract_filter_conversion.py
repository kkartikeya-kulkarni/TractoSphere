"""TRK filtering needs an explicit, complete, non-lossy TCK derivative."""

import importlib.util
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.execution import hash_file
from dmri_studio.core.scientific import ImportRequest, import_artifact
from dmri_studio.core.tract_filter_conversion import convert_verified_trk


def _case(tmp_path, *, attributes=False):
    spec = importlib.util.spec_from_file_location(
        "roi_fixture", Path(__file__).parents[1] / "core/test_roi_filtering.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    source, declarations = fixture.roi_case.__wrapped__(tmp_path)
    image = nib.load(source / "reference.nii.gz")
    lines = list(nib.streamlines.load(source / "tracks.tck").streamlines)
    payload = dict(affine_to_rasmm=np.eye(4))
    if attributes:
        payload["data_per_streamline"] = {"weight": np.ones((len(lines), 1), "f4")}
    tractogram = nib.streamlines.Tractogram(lines, **payload)
    header = nib.streamlines.TrkFile.create_empty_header()
    header["voxel_to_rasmm"] = image.affine
    header["dimensions"] = image.shape
    header["voxel_sizes"] = nib.affines.voxel_sizes(image.affine)
    header["voxel_order"] = "".join(nib.aff2axcodes(image.affine))
    trk = source / "tracks.trk"
    nib.streamlines.save(nib.streamlines.TrkFile(tractogram, header), trk)
    record = import_artifact(ImportRequest("tractogram", trk, declarations=declarations))
    reference = import_artifact(
        ImportRequest("anatomy", source / "reference.nii.gz", declarations=declarations)
    )
    output = tmp_path / "derivatives"
    output.mkdir()
    return trk, record, reference, output / "converted.tck"


def test_complete_trk_conversion_preserves_coordinates_and_hashes(tmp_path):
    trk, source, reference, output = _case(tmp_path)
    before = hash_file(trk)
    artifact = convert_verified_trk(source, reference, output)
    assert artifact.kind == "tractogram"
    assert artifact.payload["path"] == str(output)
    assert output.is_file() and output.with_suffix(".conversion.json").is_file()
    assert hash_file(trk) == before
    original = list(nib.streamlines.load(trk).streamlines)
    converted = list(nib.streamlines.load(output).streamlines)
    assert len(original) == len(converted) == 4
    for left, right in zip(original, converted, strict=True):
        np.testing.assert_allclose(left, right, atol=1e-4, rtol=0)
    assert len(artifact.payload["sources"]) == 2  # output plus conversion record
    artifact.assert_current()
    output.with_suffix(".conversion.json").write_text("tampered")
    with pytest.raises(ValueError, match="Imported source changed"):
        artifact.assert_current()


def test_trk_conversion_blocks_geometry_mismatch_and_attribute_loss(tmp_path):
    trk, source, reference, output = _case(tmp_path)
    wrong = dict(reference.payload)
    wrong["declarations"] = dict(wrong["declarations"], space_id="different-space")
    from dmri_studio.core.scientific import Artifact

    with pytest.raises(ValueError, match="matching space_id"):
        convert_verified_trk(source, Artifact(wrong), output)
    assert not output.exists()

    changed_header = nib.streamlines.load(trk)
    changed_header.header["dimensions"] = (9, 9, 9)
    nib.streamlines.save(changed_header, trk)
    changed = import_artifact(
        ImportRequest("tractogram", trk, declarations=dict(source.payload["declarations"]))
    )
    with pytest.raises(ValueError, match="header grid differs"):
        convert_verified_trk(changed, reference, output)
    assert not output.exists()


def test_trk_conversion_rejects_attributes_and_cancellation(tmp_path):
    trk, source, reference, output = _case(tmp_path, attributes=True)
    with pytest.raises(ValueError, match="attributes"):
        convert_verified_trk(source, reference, output)
    assert not output.exists()
    assert not output.with_suffix(".conversion.json").exists()
    from dmri_studio.core.process import ProcessingCancelled

    with pytest.raises(ProcessingCancelled):
        convert_verified_trk(source, reference, output, cancelled=lambda: True)
    assert trk.is_file() and not output.exists()

    checks = 0

    def cancel_during_work():
        nonlocal checks
        checks += 1
        return checks >= 9

    with pytest.raises(ProcessingCancelled):
        convert_verified_trk(source, reference, output, cancelled=cancel_during_work)
    assert checks >= 9
    assert trk.is_file() and not output.exists()
    assert not output.with_suffix(".conversion.json").exists()


def test_trk_conversion_never_removes_existing_output_or_symlink(tmp_path):
    _, source, reference, output = _case(tmp_path)
    output.write_text("keep")
    with pytest.raises(ValueError, match="Choose a new TCK"):
        convert_verified_trk(source, reference, output)
    assert output.read_text() == "keep"
    output.unlink()
    output.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="Choose a new TCK"):
        convert_verified_trk(source, reference, output)
    assert output.is_symlink()
    linked_parent = tmp_path / "linked-originals"
    linked_parent.symlink_to(Path(source.payload["path"]).parent, target_is_directory=True)
    with pytest.raises(ValueError, match="outside original scientific input directories"):
        convert_verified_trk(source, reference, linked_parent / "new.tck")
