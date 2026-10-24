"""Project reopening uses the normal transactional loader and existing viewer."""

import json
import shutil

import pytest

from dmri_studio.core.projects import add_acquisition, new_project, save_project
from dmri_studio.core.workflow import ProcessingHistory


def test_project_load_preserves_unknown_history_and_processing_lock(
    window, qtbot, dataset_folder, tmp_path
):
    paths = {
        k: str(dataset_folder / v)
        for k, v in [("dwi", "data.nii"), ("bval", "bvals"), ("bvec", "bvecs")]
    }
    project = add_acquisition(
        new_project("Research"),
        paths,
        {"sub": "S1", "ses": "V1", "run": "01"},
        ProcessingHistory().to_dict(),
    )
    save_project(project, tmp_path / "project.json")
    window.projects.open_path(tmp_path / "project.json")
    qtbot.waitUntil(lambda: window.projects.thread is None, timeout=10000)
    assert window.projects.table.rowCount() == 1
    window.projects.table.selectRow(0)
    window.processing_busy(True)
    assert not window.projects.load_input.isEnabled()
    window.processing_busy(False)
    window.projects.load_input.click()
    qtbot.waitUntil(
        lambda: (
            window.projects.thread is None
            and window.load_thread is None
            and window.dataset is not None
        ),
        timeout=10000,
    )
    assert window.dataset.paths.dwi == dataset_folder / "data.nii"
    assert window.workflow.history.resolve("eddy") == "unknown"


def test_real_conversion_attaches_to_loaded_acquisition_not_selected_row(
    window, qtbot, dataset_folder, tmp_path
):
    """Exercise actual bounded GUI conversion, project persistence and output handoff."""
    if not all(shutil.which(name) for name in ("mrconvert", "mrinfo")):
        pytest.skip("Local MRtrix unavailable")
    paths = {
        k: str(dataset_folder / v)
        for k, v in [("dwi", "data.nii"), ("bval", "bvals"), ("bvec", "bvecs")]
    }
    other = tmp_path / "other-input"
    shutil.copytree(dataset_folder, other)
    project = new_project("Separate acquisitions")
    for run, inputs in [
        ("01", paths),
        ("02", {k: str(other / v.rsplit("/", 1)[-1]) for k, v in paths.items()}),
    ]:
        project = add_acquisition(
            project,
            inputs,
            {"sub": "S1", "ses": "V1", "run": run},
            ProcessingHistory().to_dict(),
        )
    original = save_project(project, tmp_path / "project.json")
    window.projects.open_path(original)
    qtbot.waitUntil(lambda: window.projects.thread is None)
    window.projects.table.selectRow(0)
    window.projects.load_input.click()
    qtbot.waitUntil(lambda: window.dataset is not None and window.load_thread is None)
    assert window.project_binding["acquisition_id"] == project["acquisitions"][0]["id"]
    window.projects.table.selectRow(1)
    window.focus_workflow()
    workflow = window.workflow
    workflow.confirm.click()  # Unknown upstream history remains unknown.
    workflow.selections["convert"].setChecked(True)
    panel = workflow.execution
    panel.output.setText(str(tmp_path / "converted"))
    panel.prepare.click()
    qtbot.waitUntil(lambda: panel.thread is None, timeout=20000)
    assert panel.plan is not None, panel.status.text()
    panel.run.click()
    qtbot.waitUntil(lambda: panel.thread is None and window.projects.thread is None, timeout=30000)
    assert panel.manifest["status"] == "validated"
    rows = window.projects.project["acquisitions"]
    assert len(rows[0]["attempts"]) == 1 and not rows[1]["attempts"]
    assert not json.loads(original.read_text())["acquisitions"][0]["attempts"]
    assert window.projects.path != original
    assert rows[0]["attempts"][0]["scientific_review"] == "pending"
    window.projects.table.selectRow(0)
    window.projects.inspect.click()
    qtbot.waitUntil(lambda: window.results.thread is None)
    assert window.results.snapshot["status"] == "validated"
    window.results.load_output.click()
    qtbot.waitUntil(lambda: window.load_thread is None)
    assert window.project_binding["acquisition_id"] == rows[0]["id"]
    assert window.workflow.history.resolve("eddy") == "unknown"


def test_external_project_edit_does_not_create_silent_revision(window, qtbot, tmp_path):
    """The displayed project bytes must still be the parent of a new snapshot."""
    path = save_project(new_project("Original"), tmp_path / "project.json")
    window.projects.open_path(path)
    qtbot.waitUntil(lambda: window.projects.thread is None)
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="changed externally"):
        window.projects.persist(window.projects.project)


def test_recent_project_is_explicit_and_rechecked(window, qtbot, tmp_path):
    """Remember the snapshot path without loading private data at startup."""
    from dmri_studio.gui.projects import ProjectPage

    path = save_project(new_project("Recent project"), tmp_path / "project.json")
    window.projects.open_path(path)
    qtbot.waitUntil(lambda: window.projects.thread is None)
    assert window.settings.get("last_project_snapshot") == str(path)

    reopened = ProjectPage(lambda: (None, None), window.settings)
    qtbot.addWidget(reopened)
    assert reopened.project is None and reopened.thread is None
    reopened.open_recent()
    qtbot.waitUntil(lambda: reopened.thread is None)
    assert reopened.project["name"] == "Recent project"
    assert window.dataset is None

    path.unlink()
    reopened.open_recent()
    assert "unavailable" in reopened.status.text()
