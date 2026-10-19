"""Actual MRtrix ROI semantics with known paths; original arrays/tracts immutable."""

from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.execution import execute_plan, hash_file
from dmri_studio.core.roi_filtering import prepare_roi_filter
from dmri_studio.core.tract_index import IndexedStreamlines

BIN = Path("/usr/local/mrtrix3/bin")


@pytest.fixture
def roi_case(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    aff = np.array([[-1.2, 0, 0, 20], [0, 1.6, 0, -8], [0, 0, 2, 3], [0, 0, 0, 1.0]])
    angle = np.deg2rad(23)
    rotation = np.array(
        [[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]]
    )
    aff[:3, :3] = rotation @ aff[:3, :3]
    labels = np.zeros((12, 12, 12), "u1")
    labels[3:5, 2:5, 2:5] = 3
    labels[7:9, 2:5, 2:5] = 20
    for name, data in [("atlas", labels), ("reference", np.ones(labels.shape))]:
        im = nib.Nifti1Image(data, aff)
        im.set_qform(aff, 1)
        im.set_sform(aff, 1)
        im.header.set_xyzt_units("mm")
        nib.save(im, source / (name + ".nii.gz"))
    lines = [
        np.array([[1, 3, 3], [3, 3, 3], [5, 3, 3]]),
        np.array([[6, 3, 3], [8, 3, 3], [10, 3, 3]]),
        np.array([[1, 3, 3], [3, 3, 3], [5, 3, 3], [8, 3, 3], [10, 3, 3]]),
        np.array([[1, 8, 3], [5, 8, 3], [10, 8, 3]]),
    ]
    nib.streamlines.save(
        nib.streamlines.Tractogram(
            [nib.affines.apply_affine(aff, x).astype("f4") for x in lines],
            affine_to_rasmm=np.eye(4),
        ),
        source / "tracks.tck",
    )
    decl = {
        "subject_id": "synthetic-roi",
        "space_id": "known-oblique-world",
        "registration_reviewed": True,
        "alignment_evidence": "Controlled fixture generated on exact declared grid",
    }

    from dmri_studio.core.registration_review import diagnostic_override
    from dmri_studio.core.scientific import ImportRequest, import_artifact

    atlas = import_artifact(
        ImportRequest("atlas_labels", source / "atlas.nii.gz", declarations=decl)
    )
    reference = import_artifact(
        ImportRequest("anatomy", source / "reference.nii.gz", declarations=decl)
    )
    decl["registration_diagnostic"] = diagnostic_override(
        atlas,
        reference,
        reviewer="controlled fixture",
        reason="Known oblique grid and analytic paths; diagnostic filtering",
    )
    return source, decl


@pytest.mark.parametrize(
    "mode,include,exclude,ends,expected",
    [
        ("OR", [3, 20], [], False, 3),
        ("AND", [3, 20], [], False, 1),
        ("ORDERED", [3, 20], [], False, 1),
        ("OR", [3, 20], [20], False, 1),
        ("OR", [], [20], False, 2),
        ("OR", [3], [], True, 1),
    ],
)
def test_actual_filter_modes(tmp_path, roi_case, mode, include, exclude, ends, expected):
    source, decl = roi_case
    before = {str(p): hash_file(p) for p in source.iterdir()}
    # Endpoint case has an actual endpoint in parcel 3; the other fixture
    # paths only pass through it, demonstrating distinct semantics.
    if ends:
        tracks = nib.streamlines.load(source / "tracks.tck")
        lines = list(tracks.streamlines)
        lines[0] = lines[0][:2]
        nib.streamlines.save(
            nib.streamlines.Tractogram(lines, affine_to_rasmm=np.eye(4)), source / "tracks.tck"
        )
        before[str(source / "tracks.tck")] = hash_file(source / "tracks.tck")
    plan = prepare_roi_filter(
        source / "tracks.tck",
        source / "atlas.nii.gz",
        source / "reference.nii.gz",
        tmp_path / "run",
        include=include,
        exclude=exclude,
        mode=mode,
        ends_only=ends,
        declarations=decl,
        tool_directory=BIN,
    )
    argv = next(c["argv"] for c in plan.to_dict()["commands"] if c["kind"] == "tracts")
    assert "-number" not in argv
    if mode == "OR" and include:
        assert argv.count("-include") == 1
    if mode == "AND":
        assert argv.count("-include") == 2
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    index = IndexedStreamlines(tmp_path / "run/processing/roi-filter-filtered.tck")
    assert len(index) == expected
    index.close()
    assert all(hash_file(Path(p)) == h for p, h in before.items())
    if mode == "AND":
        from dmri_studio.core.roi_filtering import export_roi_result

        output = export_roi_result(result, tmp_path / "export.tck")
        assert hash_file(output) == hash_file(tmp_path / "run/processing/roi-filter-filtered.tck")
        assert output.with_suffix(".roi-filter.json").exists()
        with pytest.raises(FileExistsError):
            export_roi_result(result, output)


def test_spatial_unknown_and_bad_ids_block(tmp_path, roi_case):
    source, decl = roi_case
    args = [
        source / "tracks.tck",
        source / "atlas.nii.gz",
        source / "reference.nii.gz",
        tmp_path / "bad",
    ]
    with pytest.raises(ValueError, match="Reviewed"):
        prepare_roi_filter(*args, include=[3])
    with pytest.raises(ValueError, match="absent"):
        prepare_roi_filter(*args, include=[99], declarations=decl)
    assert not args[-1].exists()
    with pytest.raises(ValueError, match="distinct"):
        prepare_roi_filter(
            *args, include=[3, 20], mode="ORDERED", ends_only=True, declarations=decl
        )


def test_changed_original_atlas_blocks_approved_plan(tmp_path, roi_case):
    source, decl = roi_case
    plan = prepare_roi_filter(
        source / "tracks.tck",
        source / "atlas.nii.gz",
        source / "reference.nii.gz",
        tmp_path / "changed-run",
        include=[3],
        declarations=decl,
        tool_directory=BIN,
    )
    # Only this synthetic test copy is changed after approval, never research data.
    image = nib.load(source / "atlas.nii.gz")
    data = np.asanyarray(image.dataobj).copy()
    data[0, 0, 0] = 3
    nib.save(nib.Nifti1Image(data, image.affine, image.header), source / "atlas.nii.gz")
    with pytest.raises(ValueError, match="changed|identity|hash|stale"):
        execute_plan(None, plan, approval=plan.digest)


def test_ordered_direction_and_empty_output_not_published(tmp_path, roi_case):
    source, decl = roi_case
    plan = prepare_roi_filter(
        source / "tracks.tck",
        source / "atlas.nii.gz",
        source / "reference.nii.gz",
        tmp_path / "reverse-order",
        include=[20, 3],
        mode="ORDERED",
        declarations=decl,
        tool_directory=BIN,
    )
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "failed"
    assert "empty" in result["error"].lower()
    index = IndexedStreamlines(tmp_path / "reverse-order/processing/roi-filter-filtered.tck")
    assert len(index) == 0  # Native 3.0.5 respects saved direction, not either direction.
    index.close()
    assert not result.get("validation", {}).get("products")
