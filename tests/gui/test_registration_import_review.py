"""Registration keeps inspected HCP-style anatomy visible until declarations are reviewed."""

import nibabel as nib
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QFileDialog, QPushButton

from dmri_studio.core.scientific import ImportRequest, Task
from dmri_studio.gui.scientific_planning import ImportDialog


def _image(path):
    image = nib.Nifti1Image(np.ones((6, 7, 8), dtype="f4"), np.eye(4))
    image.header.set_xyzt_units("mm")
    nib.save(image, path)


def _accept(qtbot):
    dialog = QApplication.activeModalWidget()
    assert isinstance(dialog, ImportDialog)
    qtbot.mouseClick(
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok),
        Qt.MouseButton.LeftButton,
    )


def test_hcp_style_anatomy_import_review_and_reference_selection(
    window, qtbot, tmp_path, monkeypatch
):
    path = tmp_path / "T1w_acpc_dc_restore_1.25.nii.gz"
    _image(path)
    window.resize(960, 640)
    window.focus_registration()
    page = window.scientific
    guided = page.registration.guided
    guided.select_goal("epi_t1")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda _parent, title, *_: (str(path), ""))

    def import_without_declarations():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ImportDialog)
        assert dialog.kind.currentData() == "anatomy"
        browse = next(
            item
            for item in dialog.findChildren(QPushButton)
            if item.accessibleName() == "Browse Input file"
        )
        qtbot.mouseClick(browse, Qt.MouseButton.LeftButton)
        assert dialog.paths["path"].text() == str(path)
        _accept(qtbot)

    import_button = next(
        item
        for item in guided.findChildren(QPushButton)
        if item.accessibleName() == "Import Reference T1 image"
    )
    QTimer.singleShot(0, import_without_declarations)
    qtbot.mouseClick(import_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert len(page.artifacts) == 1, page.status.text()
    original_id = page.artifacts[0].id
    assert guided.imported_anatomy.findData(original_id) > 0
    assert guided.epi_reference.count() == 1
    assert "T1w anatomical contrast declaration" in guided.imported_details.text()
    assert "subject T1 structural-space declaration" in guided.imported_details.text()
    assert (
        "subject identity, acquisition identity, specific spatial identity"
        in guided.imported_details.text()
    )
    assert "\n• " in guided.inventory_notice.text()
    assert guided.inventory_notice.minimumWidth() == 0
    QApplication.processEvents()
    assert guided.inventory_notice.width() <= page.registration.viewport().width()

    def review_as_mni():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ImportDialog)
        assert dialog.paths["path"].text() == str(path)
        assert dialog.paths["path"].isReadOnly()
        assert not next(
            item
            for item in dialog.findChildren(QPushButton)
            if item.accessibleName() == "Browse Input file"
        ).isEnabled()
        dialog.subject.setText("synthetic-subject")
        dialog.space.setText("reviewed-template-space")
        dialog.declarations["acquisition_id"].setText("structural-01")
        dialog.declarations["anatomical_modality"].setCurrentIndex(
            dialog.declarations["anatomical_modality"].findData("T1w")
        )
        dialog.declarations["space_kind"].setCurrentIndex(
            dialog.declarations["space_kind"].findData("MNI_TEMPLATE")
        )
        _accept(qtbot)

    QTimer.singleShot(0, review_as_mni)
    qtbot.mouseClick(guided.review_declarations, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert len(page.artifacts) == 1
    assert guided.epi_reference.count() == 1
    assert "rather than declared MNI_TEMPLATE" in guided.imported_details.text()

    page.registration.plan = object()
    page.registration.approval.blockSignals(True)
    page.registration.approval.setChecked(True)
    page.registration.approval.blockSignals(False)

    def review_as_subject_t1():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ImportDialog)
        assert dialog.subject.text() == "synthetic-subject"
        assert dialog.space.text() == "reviewed-template-space"
        assert dialog.declarations["anatomical_modality"].currentData() == "T1w"
        dialog.space.setText("subject-t1-acpc")
        dialog.declarations["space_kind"].setCurrentIndex(
            dialog.declarations["space_kind"].findData("T1_NATIVE")
        )
        assert "ACPC-aligned HCP" in dialog.evidence.text()
        _accept(qtbot)

    QTimer.singleShot(0, review_as_subject_t1)
    qtbot.mouseClick(guided.review_declarations, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert len(page.artifacts) == 1
    assert page.artifacts[0].id != original_id
    assert page.artifacts[0].payload["path"] == str(path)
    assert page.artifacts[0].payload["declarations"]["space_kind"] == "T1_NATIVE"
    assert guided.epi_reference.findData(page.artifacts[0].id) > 0
    guided.epi_reference.setCurrentIndex(guided.epi_reference.findData(page.artifacts[0].id))
    assert guided.epi_reference.currentData() == page.artifacts[0].id
    assert page.registration.plan is None and not page.registration.approval.isChecked()


def test_anatomy_declaration_review_preserves_previous_record_if_source_changed(
    window, qtbot, tmp_path
):
    path = tmp_path / "T1w_acpc_dc_restore_1.25.nii.gz"
    _image(path)
    window.focus_registration()
    page = window.scientific
    page.import_request(ImportRequest("anatomy", path))
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    original = page.artifacts[0]
    page.tasks = [Task("held", "registration.flirt", {"moving": original.id}, {})]
    qtbot.mouseClick(page.registration.guided.review_declarations, Qt.MouseButton.LeftButton)
    assert "bound to a draft task" in page.status.text()
    assert [artifact.id for artifact in page.artifacts] == [original.id]
    page.tasks.clear()
    _image(path)
    with path.open("ab") as handle:
        handle.write(b"changed")

    QTimer.singleShot(0, lambda: _accept(qtbot))
    qtbot.mouseClick(page.registration.guided.review_declarations, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert [artifact.id for artifact in page.artifacts] == [original.id]
    assert "previous imported record unchanged" in page.status.text()
    assert "Imported source changed" in page.status.text()


def test_fa_image_can_be_declared_for_supported_registration_role(window, qtbot, tmp_path):
    path = tmp_path / "fa.nii.gz"
    _image(path)
    window.focus_registration()
    page = window.scientific
    guided = page.registration.guided
    guided.select_goal("align_two")
    dialog = ImportDialog(window, initial_kind="anatomy")
    assert dialog.declarations["anatomical_modality"].findData("FA") >= 0
    assert dialog.declarations["anatomical_modality"].findData("MD") >= 0
    assert dialog.declarations["anatomical_modality"].findData("mean_b0") >= 0
    dialog.paths["path"].setText(str(path))
    dialog.subject.setText("documented-subject")
    dialog.space.setText("documented-dwi-space")
    dialog.declarations["acquisition_id"].setText("documented-dwi-acquisition")
    dialog.declarations["space_kind"].setCurrentIndex(
        dialog.declarations["space_kind"].findData("DWI_NATIVE")
    )
    dialog.declarations["anatomical_modality"].setCurrentIndex(
        dialog.declarations["anatomical_modality"].findData("FA")
    )
    dialog.declarations["reference_history"].setText("Documented external reference history")
    page.import_request(dialog.request())
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    artifact = page.artifacts[0]
    assert artifact.payload["declarations"]["anatomical_modality"] == "FA"
    assert guided.moving.findData(artifact.id) > 0
    assert page.connectome.fa.findData(artifact.id) < 0  # metric identity remains separate


def test_registration_imported_image_without_spatial_identity_is_not_eligible(
    window, qtbot, tmp_path
):
    path = tmp_path / "fa.nii.gz"
    _image(path)
    window.focus_registration()
    page = window.scientific
    guided = page.registration.guided
    guided.select_goal("align_two")
    page.import_request(ImportRequest("anatomy", path, declarations={"anatomical_modality": "FA"}))
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    artifact = page.artifacts[0]
    assert guided.imported_anatomy.findData(artifact.id) > 0
    assert guided.moving.findData(artifact.id) == -1
    assert "subject identity" in guided.imported_details.text()
    assert "coordinate-space kind" in guided.imported_details.text()
