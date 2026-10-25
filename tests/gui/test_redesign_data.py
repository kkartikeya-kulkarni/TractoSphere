"""Task-oriented import and hierarchical project presentation contracts."""

import shutil
from pathlib import Path

from PySide6.QtCore import Qt

from dmri_studio.core.projects import add_acquisition, new_project, save_project
from dmri_studio.core.workflow import ProcessingHistory


def test_import_choices_and_external_history_remain_unknown(window, qtbot, dataset_folder):
    page = window.page
    assert not page.goals.isHidden() and page.form.isHidden()
    page.choose_intent("preprocessed")
    assert page.goals.isHidden() and not page.form.isHidden()
    page.fill_folder(dataset_folder)
    qtbot.mouseClick(page.load_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.load_thread is None)
    assert window.dataset is not None
    assert "externally preprocessed" in window.workflow.source.text()
    assert window.workflow.history.resolve("eddy") == "unknown"
    assert not window.workflow.history.confirmed
    assert window.workflow.template.currentIndex() == 0
    window.focus_data_check()
    assert window.workspace.currentWidget() is window.validation_host
    assert window.validation_page.report is window.dataset.validation
    assert not window.validation_page.empty.isVisible()


def test_project_tree_identity_and_search(window, qtbot, dataset_folder, tmp_path):
    paths = {
        k: str(dataset_folder / v)
        for k, v in [("dwi", "data.nii"), ("bval", "bvals"), ("bvec", "bvecs")]
    }
    project = new_project("Separate acquisitions")
    for subject, session, run in [("S1", "V1", "01"), ("S1", "V2", "02"), ("S2", "V1", "03")]:
        folder = tmp_path / f"input-{subject}-{session}"
        shutil.copytree(dataset_folder, folder)
        separate_paths = {k: str(folder / Path(v).name) for k, v in paths.items()}
        project = add_acquisition(
            project,
            separate_paths,
            {"sub": subject, "ses": session, "run": run},
            ProcessingHistory().to_dict(),
        )
    path = save_project(project, tmp_path / "project.json")
    window.projects.open_path(path)
    qtbot.waitUntil(lambda: window.projects.thread is None)
    tree = window.projects.table
    assert tree.topLevelItemCount() == 2 and tree.rowCount() == 3
    tree.selectRow(1)
    assert window.projects.selected()["identity"]["ses"] == "V2"
    tree.setCurrentItem(tree.topLevelItem(0))
    assert window.projects.selected() is None
    assert not window.projects.load_input.isEnabled()
    window.projects.search.setText("S2")
    assert [leaf.isHidden() for leaf in tree.leaves] == [True, True, False]
    assert not window.projects.empty.isVisible()
