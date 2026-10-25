"""A dataset document must preserve real producer findings and evidence boundaries."""

import json

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.combined_qc import build_combined_report_from_folder
from dmri_studio.core.execution import hash_file
from dmri_studio.core.qc import artifact, load_catalog, record_review


def _catalog(folder, subject, stage, findings, artifacts):
    folder.mkdir(exist_ok=True)
    path = folder / "qc-catalog.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "dmri_qc_catalog",
                "identity": {"subject": subject},
                "stages": [
                    {
                        "id": stage,
                        "execution": "completed",
                        "technical": "passed",
                        "automated_qc": "completed_with_findings",
                        "scientific_review": "required",
                        "findings": findings,
                        "artifacts": artifacts,
                    }
                ],
            }
        )
    )
    return path


def test_combined_report_contains_only_saved_steps_and_preserves_pending_review(tmp_path):
    denoise = tmp_path / "denoise-attempt"
    denoise.mkdir()
    before = denoise / "before.nii.gz"
    after = denoise / "after.nii.gz"
    values = np.arange(8 * 8 * 8, dtype=np.float32).reshape(8, 8, 8, 1)
    nib.save(nib.Nifti1Image(values, np.eye(4)), before)
    nib.save(nib.Nifti1Image(values + 10, np.eye(4)), after)
    comparison = denoise / "comparison.json"
    comparison.write_text(
        json.dumps(
            {
                "sources": {
                    name: {"dwi": str(path), "sha256": {"dwi": hash_file(path)}}
                    for name, path in (("before", before), ("after", after))
                },
                "modes": [
                    {
                        "label": "Before / after denoise",
                        "left": "before",
                        "right": "after",
                        "pairs": [[0, 0]],
                    }
                ],
            }
        )
    )
    denoise_catalog = _catalog(
        denoise,
        "sub-01",
        "denoise",
        ["Residual needs human inspection"],
        [artifact(comparison, "comparison", "Native before/after")],
    )
    review_folder = tmp_path / "reviews"
    review_folder.mkdir()
    record_review(
        load_catalog(denoise_catalog),
        "denoise",
        "needs_review",
        "Fixture Reviewer",
        "Inspect residual image",
        review_folder,
    )
    figure_folder = tmp_path / "eddy-attempt"
    figure_folder.mkdir()
    figure = figure_folder / "motion.svg"
    figure.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    _catalog(
        figure_folder,
        "sub-01",
        "eddy",
        ["Movement warning"],
        [artifact(figure, "figure", "diagnostic-motion")],
    )
    output = tmp_path / "dataset-QC.html"
    build_combined_report_from_folder(tmp_path, output)
    text = output.read_text()
    assert "Before / after denoise" in text
    assert "native voxel axis-2 slice" in text
    assert "data:image/png;base64," in text
    assert "data:image/svg+xml;base64," in text
    assert "Residual needs human inspection" in text
    assert "Movement warning" in text
    assert "Scientific review" in text and "required" in text
    assert "local declarations: needs_review" in text
    assert "Inspect residual image" in text
    assert "tractography" not in text.lower()
    with pytest.raises(FileExistsError):
        build_combined_report_from_folder(tmp_path, output)


def test_mixed_explicit_subjects_and_changed_evidence_are_refused(tmp_path):
    paths = []
    for subject in ("sub-01", "sub-02"):
        folder = tmp_path / subject
        folder.mkdir()
        report = folder / "report.html"
        report.write_text("<html>Pending review</html>")
        paths.append(_catalog(folder, subject, "dti", [], [artifact(report, "report", "QC")]))
    with pytest.raises(ValueError, match="different subjects"):
        build_combined_report_from_folder(tmp_path, tmp_path / "mixed.html")
    paths[1].unlink()
    (tmp_path / "sub-01" / "report.html").write_text("changed")
    with pytest.raises(ValueError, match="evidence changed"):
        build_combined_report_from_folder(tmp_path, tmp_path / "changed.html")


def test_documented_cli_uses_same_exporter(tmp_path):
    from dmri_studio.cli.main import main

    attempt = tmp_path / "dti"
    attempt.mkdir()
    report = attempt / "report.html"
    report.write_text("<html>Real producer output placeholder</html>")
    _catalog(attempt, "sub-01", "dti", ["Review map"], [artifact(report, "report", "QC")])
    destination = tmp_path / "full-QC.html"
    assert main(["combined-qc", "--folder", str(tmp_path), "--output", str(destination)]) == 0
    assert "Review map" in destination.read_text()


def test_failure_catalog_is_included_in_dataset_document(tmp_path):
    attempt = tmp_path / "failed-dti"
    attempt.mkdir()
    report = attempt / "failure-report.html"
    report.write_text("<html>Failed attempt</html>")
    catalog = _catalog(
        attempt,
        "sub-01",
        "dti",
        ["DTI processing failed; inspect the attempt log"],
        [artifact(report, "report", "Failure QC")],
    )
    catalog.rename(attempt / "failure-qc-catalog.json")
    destination = tmp_path / "dataset-QC.html"
    build_combined_report_from_folder(tmp_path, destination)
    assert "DTI processing failed; inspect the attempt log" in destination.read_text()
