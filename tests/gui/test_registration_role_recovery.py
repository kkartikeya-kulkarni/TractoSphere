"""Registration keeps inspected role inputs visible while planner gates remain strict."""

import nibabel as nib
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog

from dmri_studio.core.scientific import ImportRequest
from dmri_studio.gui.scientific_planning import ImportDialog


def _image(path, shape=(7, 8, 9)):
    data = np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
    image = nib.Nifti1Image(data, np.eye(4))
    image.header.set_xyzt_units("mm")
    nib.save(image, path)


def test_dwi_picker_suggests_hcp_companions_but_does_not_claim_history(
    window, qtbot, tmp_path, monkeypatch
):
    image = tmp_path / "data.nii.gz"
    _image(image, (7, 8, 9, 2))
    (tmp_path / "bvals").write_text("0 1000\n")
    (tmp_path / "bvecs").write_text("0 1\n0 0\n0 0\n")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(image), ""))
    dialog = ImportDialog(window, initial_kind="dwi")
    dialog.browse("path")
    assert dialog.paths["bval"].text() == str(tmp_path / "bvals")
    assert dialog.paths["bvec"].text() == str(tmp_path / "bvecs")
    assert "validation still run" in dialog.companion_notice.text()
    second = tmp_path / "other_dwi.nii.gz"
    _image(second, (7, 8, 9, 2))
    dialog.paths["path"].setText(str(second))
    dialog.suggest_companions()
    assert not dialog.paths["bval"].text() and not dialog.paths["bvec"].text()
    dialog.paths["path"].setText(str(image))
    dialog.suggest_companions()
    request = dialog.request()
    assert request.declarations["processing_state"] == "unknown"
    window.focus_registration()
    page = window.scientific
    page.import_request(request)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert len(page.artifacts) == 1, page.status.text()
    guided = page.registration.guided
    guided.select_goal("reference")
    assert guided.imported_anatomy.findData(page.artifacts[0].id) > 0
    assert guided.dwi.findData(page.artifacts[0].id) == -1
    assert "declared preprocessed state" in guided.imported_details.text()
    assert "correction history" in guided.imported_details.text()
    guided.imported_anatomy.setCurrentIndex(guided.imported_anatomy.findData(page.artifacts[0].id))
    assert guided.use_dwi.isEnabled()
    qtbot.mouseClick(guided.use_dwi, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.load_thread is None, timeout=10000)
    assert window.dataset is not None
    assert window.dataset.paths.dwi == image
    assert window.page.paths().bval == tmp_path / "bvals"
    assert "preprocessed" not in page.artifacts[0].payload["declarations"].get(
        "processing_state", "unknown"
    )


def test_atlas_role_import_type_and_review_are_available(window, qtbot, tmp_path):
    window.focus_registration()
    page = window.scientific
    guided = page.registration.guided
    guided.select_goal("move")
    assert [guided.derivative_type.itemData(i) for i in range(guided.derivative_type.count())] == [
        "atlas_labels",
        "roi",
        "anatomy",
        "atlas_probability",
    ]
    source = tmp_path / "roi.nii.gz"
    _image(source)
    # Binary data with a coded affine is necessary; no association is inferred.
    roi = nib.Nifti1Image(np.ones((7, 8, 9), dtype=np.uint8), np.eye(4))
    roi.header.set_xyzt_units("mm")
    roi.set_sform(np.eye(4), code=1)
    nib.save(roi, source)
    page.import_request(ImportRequest("roi", source))
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert page.artifacts, page.status.text()
    artifact = page.artifacts[0]
    assert guided.imported_anatomy.findData(artifact.id) > 0
    assert guided.imported_anatomy.currentData() == artifact.id
    assert guided.imported_anatomy.itemData(
        guided.imported_anatomy.currentIndex(), Qt.ItemDataRole.ToolTipRole
    ) == str(source)
    guided.imported_anatomy.setCurrentIndex(guided.imported_anatomy.findData(artifact.id))
    assert "specific spatial identity" in guided.imported_details.text()
    assert guided.review_declarations.isEnabled()
    assert guided.derivative.findData(artifact.id) == -1


def test_generic_alignment_rejects_same_selected_image(window):
    window.focus_registration()
    guided = window.scientific.registration.guided
    a = type(
        "Imported",
        (),
        {
            "kind": "anatomy",
            "id": "same",
            "payload": {
                "path": "/synthetic/a.nii.gz",
                "declarations": {
                    "subject_id": "s",
                    "acquisition_id": "a",
                    "space_kind": "T1_NATIVE",
                    "space_id": "t1",
                    "anatomical_modality": "T1w",
                },
            },
        },
    )()
    window.scientific.artifacts.append(a)
    guided.refresh_inventory()
    guided.select_goal("align_two")
    guided.moving.setCurrentIndex(guided.moving.findData("same"))
    guided.reference.setCurrentIndex(guided.reference.findData("same"))
    import pytest

    with pytest.raises(ValueError, match="must be different"):
        guided.build_task()


def test_cancel_request_is_single_and_discards_unpublished_input(window, qtbot):
    import time

    window.focus_registration()
    page = window.scientific

    def inspect(cancel, progress):
        progress("Reading a bounded image block")
        while not cancel():
            time.sleep(0.01)
        raise ValueError("Inspection cancelled at safe checkpoint")

    page.start("import", inspect)
    qtbot.waitUntil(lambda: page.thread is not None, timeout=3000)
    page.cancel_inspection()
    assert not page.cancel_button.isEnabled()
    first_message = page.status.text()
    assert "safe file-read checkpoint" in first_message
    page.cancel_inspection()
    assert page.status.text() == first_message
    qtbot.waitUntil(lambda: page.thread is None, timeout=3000)
    assert not page.artifacts


def test_previous_registration_uses_verified_results_reader(window, qtbot, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton

    window.focus_registration()
    guided = window.scientific.registration.guided
    guided.select_goal("inspect")
    calls = []
    monkeypatch.setattr(window.results, "browse", lambda: calls.append("manifest-picker"))
    action = next(
        item
        for item in guided.findChildren(QPushButton)
        if item.text() == "Open Previous Registration Run…"
    )
    qtbot.mouseClick(action, Qt.MouseButton.LeftButton)
    assert calls == ["manifest-picker"]
    assert window.workspace.currentWidget() is window.results


def test_advanced_transform_import_shows_undeclared_image_block(window, qtbot, tmp_path):
    from PySide6.QtWidgets import QPlainTextEdit

    from dmri_studio.gui.registration import TransformImportDialog

    path = tmp_path / "T1.nii.gz"
    _image(path)
    window.focus_registration()
    page = window.scientific
    page.import_request(ImportRequest("anatomy", path))
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert page.artifacts, page.status.text()
    dialog = TransformImportDialog(page)
    assert dialog.source.count() == 2
    assert not dialog.source.model().item(1).isEnabled()
    assert "needs review" in dialog.source.itemText(1)
    details = next(
        item
        for item in dialog.findChildren(QPlainTextEdit)
        if item.accessibleName() == "Imported images needing transform-space evidence"
    )
    assert "subject identity" in details.toPlainText()
