"""Dataset association, containment and inheritance tests; no synthetic human claims."""

import csv
import json

import pytest

from dmri_studio.core.batch.discovery import (
    Acquisition,
    Study,
    discover_bids,
    discover_custom,
    import_manifest,
    manifest_template,
)


def bids(tmp_path):
    (tmp_path / "dataset_description.json").write_text(
        json.dumps({"Name": "Synthetic", "BIDSVersion": "1.10.0"})
    )
    (tmp_path / "dwi.json").write_text('{"TotalReadoutTime":0.04,"PhaseEncodingDirection":"j"}')
    for subject in ("synth01", "synth02"):
        for session in ("01", "02"):
            folder = tmp_path / f"sub-{subject}" / f"ses-{session}" / "dwi"
            folder.mkdir(parents=True)
            for run in (1, 2):
                stem = f"sub-{subject}_ses-{session}_acq-ms_dir-AP_run-{run}_dwi"
                for ext in (".nii.gz", ".bval", ".bvec"):
                    (folder / (stem + ext)).write_text("discovery fixture only")
                (folder / (stem + ".json")).write_text('{"PhaseEncodingDirection":"j-"}')
    return tmp_path


def test_bids_sessions_runs_and_inheritance(tmp_path):
    study = discover_bids(bids(tmp_path), "synthetic-study")
    assert len(study.acquisitions) == 8
    assert len({a.key for a in study.acquisitions}) == 8
    for a in study.acquisitions:
        assert not a.findings
        assert a.metadata["inherited_metadata"] == {
            "PhaseEncodingDirection": "j-",
            "TotalReadoutTime": 0.04,
        }
        assert len(a.metadata["metadata_sources"]) == 2
        assert "json" not in a.paths  # Never substitute only the child for merged metadata.
    assert Study.from_dict(study.to_dict()) == study


def test_derivatives_not_raw(tmp_path):
    bids(tmp_path)
    (tmp_path / "dataset_description.json").write_text('{"DatasetType":"derivative"}')
    with pytest.raises(ValueError, match="Derivative"):
        discover_bids(tmp_path, "study")


def test_mismatched_identity_and_missing_gradient(tmp_path):
    bids(tmp_path)
    image = sorted(tmp_path.glob("sub-*/ses-*/dwi/*.nii.gz"))[0]
    image.with_name(image.name.replace(".nii.gz", ".bvec")).unlink()
    image.rename(image.with_name(image.name.replace("sub-synth01", "sub-other")))
    result = discover_bids(tmp_path, "study")
    assert any("disagrees" in f for a in result.acquisitions for f in a.findings)


def test_custom_ambiguity_and_sessions(tmp_path):
    for ses in ("a", "b"):
        folder = tmp_path / "synthetic01" / ses
        folder.mkdir(parents=True)
        (folder / "one.nii").write_bytes(b"one")
        (folder / "two.nii").write_bytes(b"two")
    result = discover_custom(
        tmp_path,
        "study",
        {"subject_pattern": "synthetic*", "session_pattern": "*", "files": {"dwi": "*.nii"}},
    )
    assert len(result.acquisitions) == 2
    assert all("2 candidates" in a.findings[0] and not a.paths for a in result.acquisitions)
    with pytest.raises(ValueError):
        discover_custom(tmp_path, "study", {"files": {"dwi": "../outside"}})


@pytest.mark.parametrize("suffix,delimiter", [(".csv", ","), (".tsv", "\t")])
def test_manifest_optional_roles_containment_and_duplicates(tmp_path, suffix, delimiter):
    (tmp_path / "tracks.tck").write_text("fixture")
    path = tmp_path / ("input" + suffix)
    with path.open("w", newline="") as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(["subject_id", "session_id", "acquisition_id", "tractogram_path"])
        writer.writerow(["synthetic", "one", "a", "tracks.tck"])
        writer.writerow(["synthetic", "one", "b", "../outside"])
    study = import_manifest(path, tmp_path, "test")
    assert "dwi" not in study.acquisitions[0].paths
    assert study.acquisitions[1].findings
    with pytest.raises(ValueError, match="Duplicate"):
        Study("test", str(tmp_path), (study.acquisitions[0],) * 2)
    manifest_template(tmp_path / ("template" + suffix))


def test_symlink_escape_and_identifiers(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside").write_text("private")
    (root / "link").symlink_to(tmp_path / "outside")
    path = root / "manifest.csv"
    path.write_text("subject_id,dwi_path\nsynthetic,link\n")
    assert import_manifest(path, root, "test").acquisitions[0].findings
    with pytest.raises(ValueError):
        Acquisition("../subject")


def test_duplicate_bids_entity_scopes_are_not_silently_overwritten(tmp_path):
    root = bids(tmp_path)
    folder = root / "sub-synth01" / "ses-01" / "dwi"
    (folder / "acq-ms_dir-AP_dwi.json").write_text('{"TotalReadoutTime":0.1}')
    (folder / "dir-AP_acq-ms_dwi.json").write_text('{"TotalReadoutTime":0.2}')
    study = discover_bids(root, "study")
    assert any("Duplicate BIDS" in finding for a in study.acquisitions for finding in a.findings)


def test_same_file_not_presented_as_multiple_research_subjects(tmp_path):
    path = tmp_path / "tensor.nii"
    path.write_bytes(b"fixture")
    with pytest.raises(ValueError, match="different subjects"):
        Study(
            "study",
            str(tmp_path),
            (
                Acquisition("a", paths={"tensor": str(path)}),
                Acquisition("b", paths={"tensor": str(path)}),
            ),
        )


def test_derivatives_remain_processing_experiments(tmp_path):
    from dmri_studio.core.batch.discovery import Derivative

    a = Acquisition(
        "synthetic",
        derivatives={"first": Derivative("first", "dti", "manifest.json", "selection.json")},
    )
    study = Study("study", str(tmp_path), (a,))
    restored = Study.from_dict(json.loads(json.dumps(study.to_dict())))
    assert restored == study
    assert len(restored.acquisitions) == 1
    assert restored.acquisitions[0].derivatives["first"].kind == "dti"
