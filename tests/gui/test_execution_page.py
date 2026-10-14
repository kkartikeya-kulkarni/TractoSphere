"""Approval invalidation, responsiveness and reuse of the working native viewer."""

import json
import time

import nibabel as nib
import numpy as np

from dmri_studio.core.execution import ExecutionPlan
from dmri_studio.core.process import ProcessingCancelled
from dmri_studio.gui import execution


def activate(window, qtbot, folder):
    """Use the normal loader before opening a separate execution panel."""
    window.page.fill_folder(folder)
    window.start_load()
    qtbot.waitUntil(lambda: window.dataset is not None and window.load_thread is None)
    window.focus_workflow()
    page = window.workflow
    page.confirm.click()
    page.selections["mask"].setChecked(True)
    return page.execution


def fake_plan(output):
    """Isolate UI state tests from the real MRtrix adapters tested in core."""
    return ExecutionPlan(
        {
            "output_directory": str(output),
            "parameters": {"clean_scale": 2},
            "tools": {},
            "commands": [{"display": "synthetic command preview"}],
            "internal_actions": [],
            "scientific_limits": [],
        }
    )


def test_anatomy_controls_are_contextual_and_edits_invalidate_plan(
    window, qtbot, dataset_folder, tmp_path, monkeypatch
):
    """The worker receives explicit anatomy evidence; edits require fresh review."""
    panel = activate(window, qtbot, dataset_folder)
    window.workflow.selections["mask"].setChecked(False)
    window.workflow.selections["anatomy"].setChecked(True)
    panel.anatomy_inputs.mode.setCurrentIndex(1)
    panel.anatomy_inputs.fields["t1"].setText("/synthetic/prepared-t1.nii")
    panel.anatomy_inputs.fields["evidence"].setText("Synthetic fixture declaration")
    panel.output.setText(str(tmp_path / "run"))
    received = []

    def prepare(*args, **kwargs):
        received.append(kwargs["anatomy_options"])
        return fake_plan(tmp_path / "run")

    monkeypatch.setattr(execution, "prepare_execution", prepare)
    panel.prepare.click()
    qtbot.waitUntil(lambda: panel.thread is None)
    assert received[0]["mode"] == "prepared_t1"
    assert received[0]["t1"] == "/synthetic/prepared-t1.nii"
    assert panel.plan is not None
    panel.anatomy_inputs.fields["evidence"].setText("Changed declaration")
    assert panel.plan is None and not panel.run.isEnabled()
    window.workflow.selections["anatomy"].setChecked(False)
    window.workflow.selections["alignment"].setChecked(True)
    assert panel.anatomy_inputs.mode.isHidden()
    assert "anatomy_review" in panel.anatomy_inputs.options()


def test_separate_review_and_approval_invalidated_on_edits(
    window, qtbot, dataset_folder, tmp_path, monkeypatch
):
    """Preparing does not run; all edits void the previously reviewed command set."""
    panel = activate(window, qtbot, dataset_folder)
    folder = tmp_path / "run"
    monkeypatch.setattr(execution, "prepare_execution", lambda *args, **kwargs: fake_plan(folder))
    calls = []
    monkeypatch.setattr(execution, "execute_plan", lambda *args, **kwargs: calls.append(kwargs))
    panel.output.setText(str(folder))
    panel.prepare.click()
    qtbot.waitUntil(lambda: panel.thread is None)
    assert panel.run.isEnabled() and not calls and not folder.exists()
    assert "synthetic command preview" in panel.commands.toPlainText()
    panel.clean_scale.setValue(3)
    assert panel.plan is None and not panel.run.isEnabled()
    panel.prepare.click()
    qtbot.waitUntil(lambda: panel.thread is None)
    window.workflow.source.setText("Edited evidence")
    assert panel.plan is None and not panel.run.isEnabled()


def test_running_protects_dataset_and_is_cancellable(
    window, qtbot, dataset_folder, tmp_path, monkeypatch
):
    """The GUI remains responsive, while input replacement/unload is refused."""
    panel = activate(window, qtbot, dataset_folder)
    folder = tmp_path / "run"
    panel.plan = fake_plan(folder)
    dataset = window.dataset

    def running(*args, cancelled, progress, **kwargs):
        """A silent data-only worker waits for actual cancellation, never touches UI."""
        progress("Synthetic cancellable job")
        end = time.monotonic() + 5
        while time.monotonic() < end:
            if cancelled():
                return {"status": "cancelled", "plan": {"output_directory": str(folder)}}
            time.sleep(0.01)
        raise ProcessingCancelled("timed out")

    monkeypatch.setattr(execution, "execute_plan", running)
    panel.refresh_actions()
    panel.run.click()
    qtbot.waitUntil(lambda: panel.thread is not None)
    window.unload()
    assert window.dataset is dataset
    assert not window.page.unload.isEnabled()
    assert not window.workflow.history_card.isEnabled()
    window.toggle_theme()
    assert window.theme.name == "light"
    panel.cancel.click()
    qtbot.waitUntil(lambda: panel.thread is None)
    assert panel.manifest["status"] == "cancelled"
    assert not panel.accept.isEnabled()
    assert window.workflow.history_card.isEnabled()


def test_mask_inspection_requires_actual_overlay_load_and_explicit_qc(
    window, qtbot, dataset_folder, tmp_path, monkeypatch
):
    """A structurally validated state alone cannot auto-approve anatomical coverage."""
    panel = activate(window, qtbot, dataset_folder)
    folder = tmp_path / "synthetic UI run"
    folder.mkdir()
    mask = np.zeros(window.dataset.shape[:3], np.uint8)
    mask[2:6, 3:7, 4:8] = 1
    image = nib.Nifti1Image(mask, window.dataset.affine)
    image.header.set_xyzt_units("mm")
    nib.save(image, folder / "mask.nii.gz")
    panel.manifest = {
        "status": "validated_pending_qc",
        "plan": {"output_directory": str(folder)},
        "outputs": {"mask.nii.gz": {}},
    }
    panel.refresh_actions()
    assert panel.inspect.isEnabled() and not panel.accept.isEnabled()
    panel.inspect.click()
    qtbot.waitUntil(lambda: panel.review_ready, timeout=10000)
    assert window.workspace.currentWidget() is window.scroll
    assert window.page.preview.overlay.dataset.path == folder / "mask.nii.gz"
    assert panel.accept.isEnabled()
    calls = []

    def save(run, decision, reason, *, reviewer, cancelled):
        """Record UI arguments only; real output/hash safeguards live in core tests."""
        calls.append((run, decision, reason, reviewer))
        path = run / "qc-ui.json"
        path.write_text(json.dumps({"decision": decision}))
        return path

    monkeypatch.setattr(execution, "record_qc", save)
    panel.accept.click()
    assert not calls  # No reviewer/explanation entered.
    panel.reviewer.setText("Test reviewer")
    panel.reason.setText("Synthetic UI test; not scientific approval")
    panel.reject.click()
    qtbot.waitUntil(lambda: panel.thread is None)
    assert calls == [
        (folder, "rejected", "Synthetic UI test; not scientific approval", "Test reviewer")
    ]
    assert not panel.accept.isEnabled()


def test_close_cancels_processing_and_waits_safely(
    window, qtbot, dataset_folder, tmp_path, monkeypatch
):
    """Closing never deletes the live worker, subprocess context or active input."""
    panel = activate(window, qtbot, dataset_folder)
    panel.plan = fake_plan(tmp_path / "run")

    def work(*args, cancelled, **kwargs):
        """Simulate a bounded background job ending when close requests cancellation."""
        while not cancelled():
            time.sleep(0.01)
        return {"status": "cancelled", "plan": {"output_directory": str(tmp_path / "run")}}

    monkeypatch.setattr(execution, "execute_plan", work)
    panel.refresh_actions()
    panel.run.click()
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible() and panel.thread is None, timeout=10000)


def test_bias_inputs_are_contextual_and_invalidate_approval(
    window, qtbot, dataset_folder, tmp_path, monkeypatch
):
    """A changed fitting mask/evidence cannot reuse a previous reviewed plan."""
    panel = activate(window, qtbot, dataset_folder)
    page = window.workflow
    page.selections["mask"].setChecked(False)
    page.selections["bias"].setChecked(True)
    assert not panel.bias_disclosure.isHidden()
    assert panel.clean_scale.isHidden()
    monkeypatch.setattr(
        execution, "prepare_execution", lambda *args, **kwargs: fake_plan(tmp_path / "bias")
    )
    panel.output.setText(str(tmp_path / "bias"))
    panel.prepare.click()
    qtbot.waitUntil(lambda: panel.thread is None)
    assert panel.plan is not None
    panel.bias_inputs["fitting_mask"].setText(str(tmp_path / "changed-mask.nii"))
    assert panel.plan is None and not panel.run.isEnabled()
    page.selections["bias"].setChecked(False)
    assert panel.bias_disclosure.isHidden()


def test_correction_calibration_changes_invalidate_approval(
    window, qtbot, dataset_folder, tmp_path, monkeypatch
):
    """Changing acquisition associations cannot retain approval from older inputs."""
    panel = activate(window, qtbot, dataset_folder)
    page = window.workflow
    page.selections["mask"].setChecked(False)
    page.selections["correction"].setChecked(True)
    assert not panel.correction_disclosure.isHidden()
    assert panel.bias_disclosure.isHidden() and panel.clean_scale.isHidden()
    calls = []

    def prepare(*args, **kwargs):
        calls.append(kwargs)
        return fake_plan(tmp_path / "correction")

    monkeypatch.setattr(execution, "prepare_execution", prepare)
    panel.references.setPlainText(str(tmp_path / "PA.nii.gz"))
    panel.output.setText(str(tmp_path / "correction"))
    panel.prepare.click()
    qtbot.waitUntil(lambda: panel.thread is None)
    assert panel.plan is not None
    assert calls[0]["references"] == (tmp_path / "PA.nii.gz",)
    panel.references.setPlainText(str(tmp_path / "changed-PA.nii.gz"))
    assert panel.plan is None and not panel.run.isEnabled()
    page.selections["correction"].setChecked(False)
    assert panel.correction_disclosure.isHidden()


def test_combination_evidence_changes_invalidate_plan(
    window, qtbot, dataset_folder, tmp_path, monkeypatch
):
    """Pairing edits cannot reuse approval, and workers get the selected evidence."""
    panel = activate(window, qtbot, dataset_folder)
    window.workflow.selections["mask"].setChecked(False)
    window.workflow.selections["combine"].setChecked(True)
    assert not panel.combination_disclosure.isHidden()
    assert panel.clean_scale.isHidden() and panel.anatomy_disclosure.isHidden()
    panel.combination_inputs.fields["pairing_spec"].setText("/synthetic/pairs.json")
    received = []

    def prepare(*args, **kwargs):
        received.append(kwargs["combination_options"])
        return fake_plan(tmp_path / "run")

    monkeypatch.setattr(execution, "prepare_execution", prepare)
    panel.output.setText(str(tmp_path / "run"))
    panel.prepare.click()
    qtbot.waitUntil(lambda: panel.thread is None)
    assert received[0]["pairing_spec"] == "/synthetic/pairs.json"
    assert panel.run.isEnabled()
    panel.combination_inputs.fields["pairing_spec"].setText("/synthetic/new-pairs.json")
    assert panel.plan is None and not panel.run.isEnabled()


def test_gui_explicit_tool_directory_is_bound_to_preview(
    window, qtbot, dataset_folder, tmp_path, monkeypatch
):
    """Desktop launch without shell PATH can select actual tools; edits void approval."""
    panel = activate(window, qtbot, dataset_folder)
    panel.output.setText(str(tmp_path / "run"))
    panel.tool_directory.setText(str(tmp_path / "tools"))
    received = []

    def prepare(*args, **kwargs):
        received.append(kwargs["tool_directory"])
        return fake_plan(tmp_path / "run")

    monkeypatch.setattr(execution, "prepare_execution", prepare)
    panel.prepare.click()
    qtbot.waitUntil(lambda: panel.thread is None)
    assert received == [tmp_path / "tools"] and panel.run.isEnabled()
    panel.tool_directory.clear()
    assert panel.plan is None and not panel.run.isEnabled()
