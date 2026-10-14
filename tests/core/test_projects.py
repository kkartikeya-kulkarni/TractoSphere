"""Project acquisition identity, provenance and immutable snapshot boundaries."""

import json
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.loading import DatasetLoader
from dmri_studio.core.projects import (
    acquisition_context,
    add_acquisition,
    discover_acquisitions,
    new_project,
    read_project,
    save_project,
)
from dmri_studio.core.runs import load_verified_product
from dmri_studio.core.validation import ValidationPolicy
from dmri_studio.core.workflow import ProcessingHistory


def fixture(root):
    raw = root / "raw"
    dwi = raw / "sub-01/ses-A/dwi"
    dwi.mkdir(parents=True)
    fmap = dwi.parent / "fmap"
    fmap.mkdir()
    (raw / "dataset_description.json").write_text("{}")
    (raw / "dwi.json").write_text(
        json.dumps(
            {
                "PhaseEncodingDirection": "j-",
                "TotalReadoutTime": 0.05,
                "EchoTime": 0.09,
                "RepetitionTime": 4,
                "FlipAngle": 90,
                "SliceEncodingDirection": "k",
            }
        )
    )
    paths = []
    for run in ("01", "02"):
        stem = dwi / f"sub-01_ses-A_run-{run}_dwi"
        image = nib.Nifti1Image(np.ones((4, 5, 6, 4), np.float32), np.diag([-2, 2, 2, 1]))
        image.header.set_xyzt_units("mm")
        nib.save(image, str(stem) + ".nii.gz")
        Path(str(stem) + ".bval").write_text("0 1000 1000 1000")
        Path(str(stem) + ".bvec").write_text("0 1 0 0\n0 0 1 0\n0 0 0 1")
        paths.append(str(stem) + ".nii.gz")
    ref = fmap / "sub-01_ses-A_dir-PA_epi"
    nib.save(
        nib.Nifti1Image(np.ones((4, 5, 6), np.float32), np.diag([-2, 2, 2, 1])),
        str(ref) + ".nii.gz",
    )
    Path(str(ref) + ".json").write_text(
        json.dumps(
            {
                "PhaseEncodingDirection": "j",
                "TotalReadoutTime": 0.05,
                "EchoTime": 0.09,
                "RepetitionTime": 4,
                "FlipAngle": 90,
                "IntendedFor": [str(Path(p).relative_to(raw / "sub-01")) for p in paths],
            }
        )
    )
    return raw


def test_two_runs_one_subject_and_shared_calibration(tmp_path):
    rows = discover_acquisitions(fixture(tmp_path))
    assert len(rows) == 2 and not any(r["issues"] for r in rows)
    assert rows[0]["identity"]["sub"] == rows[1]["identity"]["sub"] == "01"
    assert rows[0]["identity"]["run"] != rows[1]["identity"]["run"]
    assert rows[0]["references"][0]["path"] == rows[1]["references"][0]["path"]
    assert rows[0]["acquisition"]["field_sources"]["EchoTime"].endswith("/dwi.json")
    project = new_project("Separate runs")
    for r in rows:
        project = add_acquisition(
            project, r["paths"], r["identity"], ProcessingHistory().to_dict(), discovery=r
        )
    save_project(project, tmp_path / "project.json")
    assert read_project(tmp_path / "project.json") == project
    with pytest.raises(FileExistsError):
        save_project(project, tmp_path / "project.json")
    context = acquisition_context(tmp_path / "project.json", project["acquisitions"][0]["id"])
    loaded = load_verified_product(context["product"], DatasetLoader(), policy=ValidationPolicy())
    assert loaded.dataset is not None
    loaded.dataset.close()
    assert context["product"]["history"]["claims"] == []
    assert len(context["handoff"]["references"]) == 1


def test_changed_external_provenance_rejected_and_sources_protected(tmp_path):
    row = discover_acquisitions(fixture(tmp_path))[0]
    evidence = tmp_path / "input.log"
    evidence.write_text("History is a declaration, not a numerical gate")
    p = add_acquisition(
        new_project("external"),
        row["paths"],
        row["identity"],
        ProcessingHistory().to_dict(),
        evidence=[evidence],
    )
    saved = tmp_path / "projects"
    saved.mkdir()
    save_project(p, saved / "p.json")
    evidence.write_text("changed")
    with pytest.raises(ValueError, match="provenance changed"):
        acquisition_context(saved / "p.json", p["acquisitions"][0]["id"])
    with pytest.raises(ValueError, match="outside source"):
        save_project(p, Path(row["paths"]["dwi"]).parent / "project.json")
    with pytest.raises(ValueError, match="already"):
        add_acquisition(p, row["paths"], row["identity"], ProcessingHistory().to_dict())


def test_missing_gradient_does_not_hide_other_acquisition(tmp_path):
    root = fixture(tmp_path)
    next(root.rglob("*run-01*.bvec")).unlink()
    rows = discover_acquisitions(root)
    assert rows[0]["issues"] and not rows[1]["issues"]


def test_inherited_metadata_change_invalidates_project_context(tmp_path):
    """Snapshot calibration labels cannot be reused after inherited JSON edits."""
    root = fixture(tmp_path)
    row = discover_acquisitions(root)[0]
    project = add_acquisition(
        new_project("Acquisition evidence"),
        row["paths"],
        row["identity"],
        ProcessingHistory().to_dict(),
        discovery=row,
    )
    path = save_project(project, tmp_path / "project.json")
    (root / "dwi.json").write_text('{"EchoTime": 0.12}')
    with pytest.raises(ValueError, match="provenance changed"):
        acquisition_context(path, project["acquisitions"][0]["id"])
    with pytest.raises(ValueError, match="metadata changed"):
        add_acquisition(
            new_project("Stale discovery"),
            row["paths"],
            row["identity"],
            ProcessingHistory().to_dict(),
            discovery=row,
        )
