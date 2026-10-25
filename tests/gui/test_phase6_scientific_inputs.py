"""Task-first input presentation over the existing immutable import contracts."""

import nibabel as nib
import numpy as np
from PySide6.QtCore import Qt

from dmri_studio.core.scientific import ImportRequest
from dmri_studio.core.scientific.contracts import KINDS
from dmri_studio.gui.scientific_guidance import INPUT_GUIDANCE
from dmri_studio.gui.scientific_planning import ImportDialog


def test_supported_input_choices_keep_exact_core_kind(window, qtbot):
    dialog = ImportDialog(window)
    qtbot.addWidget(dialog)
    assert set(INPUT_GUIDANCE) == set(KINDS)
    for kind in KINDS:
        index = dialog.kind.findData(kind)
        assert index > 0
        dialog.kind.setCurrentIndex(index)
        assert INPUT_GUIDANCE[kind][0] in dialog.kind.currentText()
        assert INPUT_GUIDANCE[kind][1] in dialog.purpose.text()
    dialog.kind.setCurrentIndex(dialog.kind.findData("tensor"))
    assert not dialog.declarations["tensor_layout"].currentData()


def test_five_tissue_import_exposes_distinct_tissue_review(window, qtbot):
    dialog = ImportDialog(window, initial_kind="5tt")
    qtbot.addWidget(dialog)
    assert not dialog.declarations["anatomical_review"].isHidden()
    assert not dialog.declarations["tissue_review"].isHidden()
    dialog.kind.setCurrentIndex(dialog.kind.findData("anatomy"))
    assert dialog.declarations["tissue_review"].isHidden()


def test_scientific_task_entry_and_inventory_search_preserve_artifact(window, qtbot, tmp_path):
    window.focus_models()
    page = window.scientific
    assert page.model_dti.isVisible() and page.model_csd.isVisible()
    page.model_dti.click()
    assert page.tabs.currentWidget() is page.dti
    page.model_csd.click()
    assert page.tabs.currentWidget() is page.csd

    path = tmp_path / "structural.nii.gz"
    image = nib.Nifti1Image(np.ones((4, 5, 6), dtype=np.float32), np.eye(4))
    image.header.set_xyzt_units("mm")
    nib.save(image, path)
    page.import_request(ImportRequest("anatomy", path, declarations={"subject_id": "sub-test"}))
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert page.inputs.count() == 1
    assert "Anatomical MRI" in page.inputs.item(0).text()
    assert "sub-test" in page.inputs.item(0).text()
    content_id = page.artifacts[0].id
    page.inputs.setCurrentRow(0)
    page.input_search.setText("different subject")
    assert page.inputs.item(0).isHidden()
    page.input_search.setText("sub-test")
    assert not page.inputs.item(0).isHidden()
    page.refresh()
    assert page.inputs.currentItem().data(Qt.ItemDataRole.UserRole) == content_id
    page.remove_input.click()
    assert page.inputs.count() == 0 and path.exists()
