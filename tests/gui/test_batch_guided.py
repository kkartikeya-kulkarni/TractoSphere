"""Guided Batch uses stable acquisition IDs and the existing plan/approval path."""

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt

from dmri_studio.core.batch.discovery import Acquisition, Study
from dmri_studio.core.execution import ExecutionPlan
from dmri_studio.gui.batch import BatchPage
from dmri_studio.gui.settings import Settings
from dmri_studio.gui.theme import Theme


def _page(qapp, qtbot):
    page = BatchPage(Theme(qapp))
    qtbot.addWidget(page)
    page.show()
    return page


def _study(tmp_path):
    root = tmp_path / "sources"
    root.mkdir()
    return Study(
        "guided",
        str(root),
        (
            Acquisition("sub-01", "ses-01", "dwi-1", {"tensor": "missing-one.nii"}),
            Acquisition("sub-02", "ses-02", "dwi-2", {"tensor": "missing-two.nii"}),
            Acquisition(
                "sub-03",
                "ses-02",
                "dwi-3",
                {"tensor": "missing-three.nii"},
                findings=("Missing required metadata",),
            ),
        ),
    )


def test_subject_selection_survives_sort_filter_and_navigation(qapp, qtbot, tmp_path):
    page = _page(qapp, qtbot)
    page.kind = "discover"
    page.result(0, _study(tmp_path), "")
    guided = page.guided
    assert page.display_stack.currentIndex() == 0
    assert guided.pages.currentIndex() == 1
    assert set(guided._selected) == {a.key for a in page.study.acquisitions[:2]}
    guided.subject_table.sortItems(1, Qt.SortOrder.DescendingOrder)
    guided.subject_search.setText("sub-02")
    assert sum(not guided.subject_table.isRowHidden(i) for i in range(3)) == 1
    guided.select_visible()
    assert len(guided._selected) == 2
    guided.subject_search.clear()
    for row in range(3):
        item = guided.subject_table.item(row, 0)
        if item.data(Qt.ItemDataRole.UserRole) == page.study.acquisitions[0].key:
            item.setCheckState(Qt.CheckState.Unchecked)
    assert page.guided_selection == (page.study.acquisitions[1].key,)
    guided.advance()
    assert guided.pages.currentIndex() == 2
    guided.previous()
    assert guided.pages.currentIndex() == 1
    assert page.guided_selection == (page.study.acquisitions[1].key,)
    assert "1 of 3 selected" in guided.selected_count.text()


def test_guided_dry_run_records_subset_and_requires_approval(qapp, qtbot, tmp_path):
    page = _page(qapp, qtbot)
    page.kind = "discover"
    page.result(0, _study(tmp_path), "")
    guided = page.guided
    wanted = page.study.acquisitions[0].key
    for row in range(guided.subject_table.rowCount()):
        item = guided.subject_table.item(row, 0)
        if item.data(Qt.ItemDataRole.UserRole) != wanted:
            item.setCheckState(Qt.CheckState.Unchecked)
    guided.advance()
    assert guided.workflow_choice.currentData() == "tensor"
    assert json.loads(page.workflow.toPlainText())["nodes"][0]["adapter"] == "dti"
    guided.advance()
    guided.output.setText(str(tmp_path / "new-batch"))
    assert page.output.text() == str(tmp_path / "new-batch")
    guided.validate_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert page.plan is not None, page.status.text()
    assert page.plan.to_dict()["selected_acquisition_keys"] == [wanted]
    assert len(page.plan.to_dict()["rows"]) == 1
    assert page.plan.to_dict()["rows"][0]["status"] == "BLOCKED"
    assert guided.pages.currentIndex() == 4
    assert not guided.run.isEnabled()
    assert page.folder is None
    guided.output.setText(str(tmp_path / "changed-batch"))
    assert page.plan is None and not guided.run.isEnabled()


def test_conversion_requires_explicit_history_and_advanced_is_same_workflow(qapp, qtbot, tmp_path):
    page = _page(qapp, qtbot)
    root = tmp_path / "raw"
    root.mkdir()
    study = Study("raw", str(root), (Acquisition("sub-01", paths={"dwi": "dwi.nii"}),))
    page.kind = "discover"
    page.result(0, study, "")
    guided = page.guided
    guided.advance()
    assert guided.workflow_choice.currentData() == "convert"
    assert (
        json.loads(page.workflow.toPlainText())["nodes"][0]["preprocessing"]["history"]["confirmed"]
        is False
    )
    guided.advance()
    assert guided.pages.currentIndex() == 2
    guided.history_confirm.setChecked(True)
    assert (
        json.loads(page.workflow.toPlainText())["nodes"][0]["preprocessing"]["history"]["confirmed"]
        is True
    )
    guided.advance()
    assert guided.pages.currentIndex() == 3
    page.toggle_mode()
    assert page.display_stack.currentIndex() == 1
    page.workflow.setPlainText('{"nodes": []}')
    page.toggle_mode()
    assert guided.workflow_choice.currentData() == "saved"
    assert json.loads(page.workflow.toPlainText()) == {"nodes": []}


@pytest.mark.parametrize("source", ["bids", "custom", "manifest"])
def test_guided_discovery_uses_existing_importers(qapp, qtbot, tmp_path, source):
    page = _page(qapp, qtbot)
    guided = page.guided
    if source == "bids":
        import importlib.util

        fixture = Path(__file__).parents[1] / "core" / "test_batch_discovery.py"
        spec = importlib.util.spec_from_file_location("batch_discovery_fixture", fixture)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.bids(tmp_path)
        expected = 8
    elif source == "custom":
        (tmp_path / "subject-a").mkdir()
        (tmp_path / "subject-a" / "dwi.nii").write_bytes(b"fixture")
        mapping = tmp_path / "mapping.json"
        mapping.write_text(
            json.dumps({"subject_pattern": "subject-*", "files": {"dwi": "dwi.nii"}})
        )
        guided.source_file.setText(str(mapping))
        expected = 1
    else:
        (tmp_path / "dwi.nii").write_bytes(b"fixture")
        manifest = tmp_path / "subjects.csv"
        manifest.write_text("subject_id,session_id,acquisition_id,dwi_path\na,01,dwi,dwi.nii\n")
        guided.source_file.setText(str(manifest))
        expected = 1
    guided.source_kind.setCurrentIndex(guided.source_kind.findData(source))
    guided.dataset_root.setText(str(tmp_path))
    guided.discover_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert page.study is not None, page.status.text()
    assert len(page.study.acquisitions) == expected
    assert guided.subject_table.rowCount() == expected
    assert guided.pages.currentIndex() == 1


def test_stale_async_plan_is_discarded_and_historical_toolchain_stays_pinned(qapp, qtbot, tmp_path):
    page = _page(qapp, qtbot)
    study = _study(tmp_path)
    page.kind = "discover"
    page.result(0, study, "")
    payload = {
        "study": study.to_dict(),
        "workflow": json.loads(page.workflow.toPlainText()),
        "tool_directory": "/historical/mrtrix/bin",
        "output_directory": str(tmp_path / "old"),
        "limits": {
            "max_subjects": 1,
            "max_processes": 1,
            "cpu_threads": 2,
            "memory_bytes": 4 * 1024**3,
        },
        "rows": [],
    }
    old = ExecutionPlan(payload)
    page.kind = "plan"
    page._pending_plan_generation = page._plan_generation
    page.output.setText(str(tmp_path / "changed"))
    page.result(0, old, "")
    assert page.plan is None
    assert "changed while planning" in page.status.text()
    page.kind = "history"
    page.result(0, (tmp_path / "old", old), "")
    assert page.historical_plan.digest == old.digest
    assert page.mrtrix_directory.text() == "/historical/mrtrix/bin"
    assert page.mrtrix_directory.isReadOnly()
    assert page.guided.pages.currentIndex() == 6
    page.new_batch()
    assert old.to_dict()["tool_directory"] == "/historical/mrtrix/bin"
    assert page.historical_plan is None
    assert page.guided._saved_workflow_ready


def test_recent_saved_study_reopens_after_settings_restart(qapp, qtbot, tmp_path):
    path = tmp_path / "saved-study.json"
    study = _study(tmp_path)
    path.write_text(json.dumps(study.to_dict()))
    settings_path = tmp_path / "settings.ini"
    first = Settings(QSettings(str(settings_path), QSettings.Format.IniFormat))
    page = BatchPage(Theme(qapp), settings=first)
    qtbot.addWidget(page)
    page.study = study
    page.remember_study(path)
    second = Settings(QSettings(str(settings_path), QSettings.Format.IniFormat))
    restored = BatchPage(Theme(qapp), settings=second)
    qtbot.addWidget(restored)
    restored.guided.source_kind.setCurrentIndex(3)
    assert restored.guided.recent_studies.count() == 2
    assert "3 acquisitions" in restored.guided.recent_studies.itemText(1)
    restored.guided.recent_studies.activated.emit(1)
    qtbot.waitUntil(lambda: restored.thread is None, timeout=10000)
    assert restored.study == study
    assert restored.guided.pages.currentIndex() == 1


def test_unavailable_preset_does_not_reuse_hidden_default_json(qapp, qtbot, tmp_path):
    page = _page(qapp, qtbot)
    root = tmp_path / "empty"
    root.mkdir()
    page.kind = "discover"
    page.result(0, Study("empty", str(root), (Acquisition("a"),)), "")
    guided = page.guided
    guided.advance()
    assert guided.workflow_choice.currentData() == "saved"
    guided.advance()
    assert guided.pages.currentIndex() == 2
    assert "Load a saved workflow" in guided.notice.text()
    page.workflow.setPlainText('{"nodes": [{"id": "x", "adapter": "dti"}]}')
    guided.advance()
    assert guided.pages.currentIndex() == 3


def test_discovery_result_cannot_replace_a_new_dataset_choice(qapp, qtbot, tmp_path):
    page = _page(qapp, qtbot)
    root = tmp_path / "old"
    root.mkdir()
    study = Study("old", str(root), (Acquisition("a"),))
    guided = page.guided
    guided.dataset_root.setText(str(root))
    page._pending_discovery_generation = guided._data_generation
    guided.dataset_root.setText(str(tmp_path / "new"))
    page.kind = "discover"
    page.result(0, study, "")
    assert page.study is None
    assert "choice changed" in page.status.text()


@pytest.mark.parametrize("appearance", ["light", "dark"])
def test_guided_batch_fits_minimum_window_and_themes(qapp, qtbot, appearance):
    theme = Theme(qapp)
    theme.apply(appearance)
    page = BatchPage(theme)
    qtbot.addWidget(page)
    page.resize(960, 640)
    page.show()
    qapp.processEvents()
    assert page.width() == 960 and page.height() == 640
    assert page.guided.pages.width() > 800
    assert page.guided.progress.text() == "1 of 7 · Select Data"
