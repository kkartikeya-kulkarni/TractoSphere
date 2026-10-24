"""Focused UI behavior from the 2026-10-24 usability audit, using synthetic inputs."""

import json

import nibabel as nib
import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QFileDialog, QLabel, QPushButton

from dmri_studio.core.batch.discovery import discover_custom
from dmri_studio.core.scientific import ImportRequest
from dmri_studio.gui.batch import BatchPage
from dmri_studio.gui.batch_guided import bids_root_problem
from dmri_studio.gui.scientific_planning import ImportDialog
from dmri_studio.gui.theme import Theme


def test_fresh_preprocessing_route_and_reconstruction_copy(window):
    controls = dict((name, control) for control, name in window.nav_buttons)
    assert controls["Preprocessing"].isEnabled()
    assert not controls["Data Check"].isEnabled()
    controls["Preprocessing"].click()
    assert window.workspace.currentWidget() is window.workflow_scroll
    assert window.workflow.dataset is None
    assert window.workflow.empty.actions[0].hasFocus()
    assert (
        "Fit tensors or estimate fiber orientation distributions"
        in controls["Reconstruction"].toolTip()
    )
    assert "not implemented" not in controls["Reconstruction"].toolTip()
    assert (
        controls["Reconstruction"].accessibleDescription() == controls["Reconstruction"].toolTip()
    )


@pytest.mark.parametrize(
    "kind", ["dwi", "anatomy", "atlas_labels", "tractogram", "response", "streamline_weights"]
)
def test_import_identity_is_single_and_reaches_request(window, qtbot, tmp_path, kind):
    dialog = ImportDialog(window, initial_kind=kind)
    qtbot.addWidget(dialog)
    assert dialog.kind.currentData() == kind
    assert dialog.state.isHidden() == (kind != "dwi")
    for key, value in (("session_id", "ses-02"), ("acquisition_id", "dwi-03")):
        dialog.declarations[key].setText(value)
    # Count the actual form labels; the first duplicate widget used to be silently ignored.
    assert (
        len([item for item in dialog.findChildren(QLabel) if item.text() == "Session identity"])
        == 1
    )
    assert (
        len([item for item in dialog.findChildren(QLabel) if item.text() == "Acquisition identity"])
        == 1
    )
    path = tmp_path / "synthetic.nii.gz"
    path.write_bytes(b"synthetic path only; request construction does not read the file")
    dialog.paths["path"].setText(str(path))
    request = dialog.request()
    if kind in {"dwi", "anatomy", "atlas_labels"}:
        assert request.declarations["session_id"] == "ses-02"
        assert request.declarations["acquisition_id"] == "dwi-03"
    else:
        assert "session_id" not in request.declarations
        assert "acquisition_id" not in request.declarations
    assert "Before inspection:" in dialog.evidence.text()


def test_registration_explains_filtered_import_and_opens_scoped_dialog(
    window, qtbot, tmp_path, monkeypatch
):
    window.focus_registration()
    page = window.scientific
    guided = page.registration.guided
    assert guided.moving.count() == 1
    assert "No eligible import" in guided.moving.itemText(0)
    opened = []
    monkeypatch.setattr(page, "browse_import", opened.append)
    action = next(
        control
        for control in guided.findChildren(QPushButton)
        if control.accessibleName() == "Import Moving image"
    )
    action.click()
    assert opened == ["anatomy"]

    image = tmp_path / "synthetic-T1.nii.gz"
    synthetic = nib.Nifti1Image(np.ones((4, 4, 4), dtype=np.float32), np.eye(4))
    synthetic.header.set_xyzt_units("mm")
    nib.save(synthetic, image)
    page.import_request(ImportRequest("anatomy", image, declarations={"subject_id": "sub-test"}))
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert len(page.artifacts) == 1, page.status.text()
    assert guided.moving.count() == 1
    assert "supported image contrast" in guided.imported_details.text()
    image2 = tmp_path / "synthetic-T1-eligible.nii.gz"
    nib.save(synthetic, image2)
    page.import_request(
        ImportRequest(
            "anatomy",
            image2,
            declarations={
                "subject_id": "sub-test",
                "acquisition_id": "structural-01",
                "space_id": "subject-t1-space",
                "space_kind": "T1_NATIVE",
                "anatomical_modality": "T1w",
            },
        )
    )
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert len(page.artifacts) == 2, page.status.text()
    assert guided.moving.count() == 2
    guided.moving.setFocus()
    qtbot.keyClick(guided.moving, Qt.Key.Key_Down)
    assert guided.moving.currentData() is not None
    assert not guided.approval.isChecked()


def test_reconstruction_prerequisites_keep_technical_contract(window, monkeypatch):
    window.focus_models()
    page = window.scientific
    row = next(
        i
        for i, spec in enumerate(page.visible_algorithms)
        if "dwi" in spec.required_inputs.values()
    )
    page.algorithms.setCurrentRow(row)
    assert "Diffusion MRI image" in page.algorithm_summary.text()
    assert "Not imported yet" in page.algorithm_summary.text()
    assert "Backend:" in page.algorithm_details.text()
    assert "Accepted algorithm versions:" in page.algorithm_details.text()
    opened = []
    monkeypatch.setattr(page, "browse_import", opened.append)
    page.algorithm_import.click()
    assert opened == ["dwi"]


@pytest.mark.parametrize("appearance", ["light", "dark"])
@pytest.mark.parametrize("size", [(960, 640), (1280, 840)])
def test_registration_heading_starts_inside_scroll_viewport(window, qapp, appearance, size):
    window.theme.apply(appearance)
    window.resize(*size)
    window.focus_registration()
    qapp.processEvents()
    panel = window.scientific.registration
    guided = panel.guided
    heading = guided.layout().itemAt(0).widget()
    top = heading.mapTo(panel.viewport(), QPoint(0, 0)).y()
    assert top >= 0
    assert top + heading.height() <= panel.viewport().height()
    assert panel.horizontalScrollBar().maximum() == 0
    guided.select_goal("move")
    qapp.processEvents()
    assert heading.mapTo(panel.viewport(), QPoint(0, 0)).y() >= 0


@pytest.mark.parametrize(
    "description,expected",
    [
        (None, "not a BIDS root"),
        ("{not json", "valid JSON"),
        (
            json.dumps({"Name": "x", "BIDSVersion": "1.8", "DatasetType": "derivative"}),
            "derivative BIDS",
        ),
        (json.dumps({"Name": "x", "BIDSVersion": "1.8"}), None),
    ],
)
def test_bids_root_guidance_before_discovery(tmp_path, description, expected):
    if description is not None:
        (tmp_path / "dataset_description.json").write_text(description)
    issue = bids_root_problem(tmp_path)
    assert issue is None if expected is None else expected in issue


def test_guided_batch_wrong_bids_root_and_mapping_template(qapp, qtbot, tmp_path, monkeypatch):
    page = BatchPage(Theme(qapp))
    qtbot.addWidget(page)
    page.show()
    guided = page.guided
    guided.dataset_root.setText(str(tmp_path))
    guided.discover_button.click()
    assert "not a BIDS root" in guided.notice.text()
    assert page.thread is None
    guided.source_kind.setCurrentIndex(guided.source_kind.findData("custom"))
    assert guided.mapping_help.isVisible()
    assert "subject_pattern" in guided.mapping_example.toPlainText()
    assert guided.data_form.labelForField(guided.file_row).text() == "Custom mapping JSON"
    template = tmp_path / "mapping.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(template), ""))
    guided.save_example.click()
    assert json.loads(template.read_text())["files"]["dwi"] == "dwi.nii.gz"
    original = template.read_bytes()
    guided.save_example.click()
    assert template.read_bytes() == original
    assert "not overwritten" in guided.notice.text()
    guided.source_kind.setCurrentIndex(guided.source_kind.findData("manifest"))
    assert not guided.mapping_help.isVisible()
    assert guided.data_form.labelForField(guided.file_row).text() == "Acquisition table CSV/TSV"


def test_guided_batch_mapping_errors_and_matched_file_preview(qapp, qtbot, tmp_path):
    page = BatchPage(Theme(qapp))
    qtbot.addWidget(page)
    page.show()
    guided = page.guided
    guided.source_kind.setCurrentIndex(guided.source_kind.findData("custom"))
    guided.dataset_root.setText(str(tmp_path))
    mapping = tmp_path / "mapping.json"
    mapping.write_text("{bad json")
    guided.source_file.setText(str(mapping))
    guided.discover_button.click()
    assert "readable JSON" in guided.notice.text()
    assert page.thread is None
    mapping.write_text(json.dumps({"subject_pattern": "sub-*", "files": {"dwi": "dwi.nii.gz"}}))
    source = tmp_path / "sub-01"
    source.mkdir()
    (source / "dwi.nii.gz").write_bytes(b"synthetic discovery only")
    guided.discover_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert guided.pages.currentIndex() == 1
    assert "Matched files" in guided.subject_details.toPlainText()
    assert "dwi.nii.gz" in guided.subject_details.toPlainText()


@pytest.mark.parametrize("number,findings", [(1, 0), (2, 1), (0, 1)])
def test_custom_mapping_discovery_reports_exact_matches(tmp_path, number, findings):
    acquisition = tmp_path / "sub-01" / "scan-01"
    acquisition.mkdir(parents=True)
    for i in range(number):
        (acquisition / f"dwi-{i}.nii.gz").write_bytes(b"synthetic discovery only")
    study = discover_custom(
        tmp_path,
        "test",
        {
            "subject_pattern": "sub-*",
            "acquisition_pattern": "scan-*",
            "files": {"dwi": "dwi-*.nii.gz"},
        },
    )
    assert len(study.acquisitions) == 1
    assert bool(study.acquisitions[0].findings) == bool(findings)
    assert ("dwi" in study.acquisitions[0].paths) == (number == 1)


def test_custom_mapping_preserves_two_acquisition_identities(tmp_path):
    for name in ("scan-01", "scan-02"):
        folder = tmp_path / "sub-01" / name
        folder.mkdir(parents=True)
        (folder / "dwi.nii.gz").write_bytes(b"synthetic discovery only")
    study = discover_custom(
        tmp_path,
        "test",
        {
            "subject_pattern": "sub-*",
            "acquisition_pattern": "scan-*",
            "files": {"dwi": "dwi.nii.gz"},
        },
    )
    assert {a.acquisition_id for a in study.acquisitions} == {"scan-01", "scan-02"}
    assert all(not a.findings for a in study.acquisitions)
