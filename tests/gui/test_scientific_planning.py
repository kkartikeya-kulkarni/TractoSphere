"""Native workstation contracts: independent inputs, honest planning, safe workers."""

import nibabel as nib
import numpy as np
from PySide6.QtWidgets import QDialog

from dmri_studio.core.execution import hash_file
from dmri_studio.core.scientific import ALGORITHMS, ImportRequest, Task
from dmri_studio.core.scientific.records import save_selection
from dmri_studio.gui.scientific_planning import ImportDialog, TaskDialog


def scalar(tmp_path, name="anatomy.nii", shape=(4, 5, 6)):
    image = nib.Nifti1Image(np.ones(shape, dtype=np.float32), np.eye(4))
    image.header.set_xyzt_units("mm")
    path = tmp_path / name
    nib.save(image, path)
    return path


def test_scientific_routes_independent_and_preserve_scene(window, qtbot, tmp_path):
    assert window.dataset is None
    for name, callback, stage in (
        ("Reconstruction", window.focus_models, "models"),
        ("Connectivity", window.focus_connectivity, "connectivity"),
        ("Tractography", window.focus_tracking_plan, "tractography"),
    ):
        callback()
        assert window.workspace.currentWidget() is window.scientific
        assert window.scientific.stage == stage
        assert [n for b, n in window.nav_buttons if b.property("selected")] == [name]
    page = window.scientific
    window.focus_models()
    path = scalar(tmp_path)
    before = hash_file(path)
    page.import_request(ImportRequest("anatomy", path))
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert len(page.artifacts) == 1 and page.inputs.count() == 1
    page.inputs.setCurrentRow(0)
    assert str(path) in page.input_details.text()
    assert "scientific" in page.input_details.text().lower()
    page.inspect.click()
    qtbot.waitUntil(
        lambda: window.scientific_viewer is not None and not window.scientific_viewer.tasks,
        timeout=10000,
    )
    assert window.scientific_viewer.reference.path == path
    ref = window.scientific_viewer.reference
    window.focus_tracking_plan()
    window.focus_viewer()
    assert window.scientific_viewer.reference is ref
    assert page.artifacts[0].payload["path"] == str(path) and hash_file(path) == before


def test_actual_import_dialog_declarations_no_inference(window, qtbot, tmp_path):
    dialog = ImportDialog(window)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.kind.setCurrentIndex(dialog.kind.findData("tensor"))
    dialog.paths["path"].setText(str(scalar(tmp_path, shape=(4, 5, 6, 6))))
    request = dialog.request()
    assert "tensor_layout" not in request.declarations
    for key, value in dict(
        tensor_layout="mrtrix_D11_D22_D33_D12_D13_D23",
        tensor_frame="world_ras",
        tensor_units="mm2/s",
    ).items():
        field = dialog.declarations[key]
        field.setCurrentIndex(field.findData(value))
    request = dialog.request()
    dialog.accept_checked()
    assert dialog.result() == QDialog.DialogCode.Accepted
    window.scientific.import_request(request)
    qtbot.waitUntil(lambda: window.scientific.thread is None, timeout=10000)
    assert window.scientific.artifacts[0].kind == "tensor"
    assert window.dataset is None


def test_task_form_validation_export_and_selection_restore(window, qtbot, tmp_path, monkeypatch):
    page = window.scientific
    window.focus_models()
    request = ImportRequest(
        "tensor",
        scalar(tmp_path, shape=(4, 5, 6, 6)),
        declarations=dict(
            tensor_layout="mrtrix_D11_D22_D33_D12_D13_D23",
            tensor_frame="world_ras",
            tensor_units="mm2/s",
        ),
    )
    page.import_request(request)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    dialog = TaskDialog(ALGORITHMS["tensor2metric"], page.artifacts, [], window)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.bindings["tensor"].currentData() is None
    assert not dialog.fields["metrics"].text()
    dialog.bindings["tensor"].setCurrentIndex(1)
    dialog.fields["metrics"].setText("FA, MD")
    task = dialog.task()
    assert task.inputs["tensor"] == page.artifacts[0].id
    page.tasks.append(task)
    page.refresh()
    page.tabs.setCurrentWidget(page.graph_page)
    page.validate_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert page.plan.payload["status"] == "review_required"
    assert "not produced" in page.findings.toPlainText()
    assert "implemented adapter" in page.findings.toPlainText()
    assert page.export_button.isEnabled()
    from PySide6.QtWidgets import QFileDialog

    destination = tmp_path / "proposal.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(destination), "JSON"))
    page.export_button.click()
    assert destination.exists()
    selection = tmp_path / "selection.json"
    save_selection(selection, tuple(page.artifacts), tuple(page.tasks))
    page.tasks.clear()
    page.artifacts.clear()
    page.refresh()
    page.restore(selection)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert len(page.artifacts) == len(page.tasks) == 1
    assert page.plan is None and not page.export_button.isEnabled()
    assert window.workflow.execution.thread is None


def test_import_failure_retains_inventory_and_unknown_history(window, qtbot, tmp_path):
    page = window.scientific
    page.import_request(ImportRequest("anatomy", tmp_path / "missing.nii"))
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert not page.artifacts and page.status.text()
    assert page.import_button.isEnabled()
    page.import_active()
    assert "Load a DWI" in page.status.text()
    assert page.context() is None


def test_explicit_loaded_dwi_no_preprocessing_requirement(window, qtbot, dataset_folder):
    window.page.fill_folder(dataset_folder)
    window.start_load()
    qtbot.waitUntil(
        lambda: window.dataset is not None and window.load_thread is None, timeout=10000
    )
    page = window.scientific
    window.focus_models()
    page.active_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert page.artifacts[0].kind == "dwi"
    assert page.artifacts[0].payload["declarations"]["processing_state"] == "unknown"
    assert not window.workflow.history.confirmed
    assert window.workflow.execution.thread is None


def test_edit_task_invalidates_proposal_and_preserves_id(window, qtbot, tmp_path, monkeypatch):
    page = window.scientific
    request = ImportRequest(
        "tensor",
        scalar(tmp_path, shape=(4, 5, 6, 6)),
        declarations=dict(
            tensor_layout="mrtrix_D11_D22_D33_D12_D13_D23",
            tensor_frame="world_ras",
            tensor_units="mm2/s",
        ),
    )
    page.import_request(request)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    page.tasks = [
        Task("stable_id", "tensor2metric", {"tensor": page.artifacts[0].id}, {"metrics": ["FA"]})
    ]
    page.refresh()
    page.validate()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert page.plan is not None

    def edit(dialog):
        assert dialog.identifier.isReadOnly() and dialog.identifier.text() == "stable_id"
        assert dialog.fields["metrics"].text() == "FA"
        dialog.fields["metrics"].setText("FA, MD")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(TaskDialog, "exec", edit)
    page.task_list.setCurrentRow(0)
    page.edit_task_button.click()
    assert page.tasks[0].id == "stable_id" and page.tasks[0].parameters["metrics"] == ("FA", "MD")
    assert page.plan is None and not page.export_button.isEnabled()


def test_cancel_and_close_drain_scientific_inspection(window, qtbot, tmp_path, monkeypatch):
    """New workspace shutdown must join its reader without owning a new engine."""
    import time
    from threading import Event

    from dmri_studio.core.process import check_cancel
    from dmri_studio.gui import scientific_planning

    entered = Event()

    def slow(request, *, cancelled, progress):
        entered.set()
        while not cancelled():
            time.sleep(0.005)
        check_cancel(cancelled)

    monkeypatch.setattr(scientific_planning, "import_artifact", slow)
    path = scalar(tmp_path)
    before = hash_file(path)
    page = window.scientific
    page.import_request(ImportRequest("anatomy", path))
    qtbot.waitUntil(entered.is_set, timeout=10000)
    page.cancel_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert not page.artifacts and page.import_button.isEnabled()
    entered.clear()
    page.import_request(ImportRequest("anatomy", path))
    qtbot.waitUntil(entered.is_set, timeout=10000)
    window.close()
    qtbot.waitUntil(lambda: page.thread is None and not window.isVisible(), timeout=10000)
    assert hash_file(path) == before and not page.artifacts
