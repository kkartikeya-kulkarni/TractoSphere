"""Task-focused presentation around unchanged preprocessing and model planners."""

from dmri_studio.core.workflow import OPERATIONS
from dmri_studio.gui.workflow import OPERATION_GUIDANCE


def test_preprocessing_stage_review_retains_proposal(window, qtbot, dataset_folder):
    window.page.fill_folder(dataset_folder)
    window.start_load()
    qtbot.waitUntil(lambda: window.load_thread is None and window.dataset is not None)
    window.focus_workflow()
    page = window.workflow
    assert set(OPERATION_GUIDANCE) == set(OPERATIONS)
    page.template.setCurrentIndex(2)
    page.source.setText("Synthetic raw acquisition")
    page.confirm.click()
    page.selections["denoise"].setChecked(True)
    assert page.plan.to_dict()["status"] == "eligible"
    plan = page.plan
    assert not page.execution.advanced_processing.toggle.isChecked()
    page.execution.advanced_processing.toggle.click()
    page.steps.setCurrentIndex(3)
    page.steps.setCurrentIndex(1)
    assert page.plan is plan
    assert not page.plan.to_dict()["approved_for_execution"]
    assert "MP-PCA" in OPERATION_GUIDANCE["denoise"]


def test_model_advanced_controls_are_available_without_obscuring_task(window):
    window.focus_models()
    page = window.scientific
    dti, csd = page.dti, page.csd
    assert not dti.advanced.toggle.isChecked()
    assert not csd.advanced.toggle.isChecked()
    assert dti.fit.currentText() == "IWLS"
    assert dti.iterations.value() == 2
    dti.advanced.toggle.click()
    assert dti.advanced.toggle.isChecked()
    assert dti.iterations.isEnabled()
    csd.advanced.toggle.click()
    assert csd.advanced.toggle.isChecked()
    assert csd.lmax.value() == 8
    assert "Three-tissue" in csd.method_info.text()
    csd.method.setCurrentIndex(csd.method.findData("dwi2fod.csd"))
    assert "Single-tissue" in csd.method_info.text()
