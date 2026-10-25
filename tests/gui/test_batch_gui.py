"""Actual Qt batch interaction and native scientific execution; no fabricated results."""

import importlib.util
import json
from pathlib import Path

import pytest

from dmri_studio.core.batch.engine import control, read_journal

pytestmark = pytest.mark.skipif(
    not all(
        (Path("/usr/local/mrtrix3/bin") / name).is_file()
        for name in ("mrconvert", "tensor2metric")
    ),
    reason="Native MRtrix3 is required for batch plan and execution tests",
)


def configure(window, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "batch_fixtures", Path(__file__).parents[1] / "core/test_batch_engine.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    study, workflow = module.fixture(tmp_path)
    window.focus_batch()
    page = window.batch
    page.tool_directory = module.BIN
    page.kind = "discover"
    page.result(0, study, "")
    page.output.setText(str(tmp_path / "batch"))
    page.workflow.setPlainText(json.dumps(workflow))
    return page


@pytest.mark.parametrize("appearance", ["light", "dark"])
def test_batch_native_execution_reopen_qc_and_theme(window, qtbot, tmp_path, appearance):
    page = configure(window, tmp_path)
    window.theme.apply(appearance)
    assert page.table.rowCount() == 2
    assert not page.run_button.isEnabled()
    page.plan_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert page.plan is not None, page.status.text()
    assert "tensor2metric" in page.preview.toPlainText()
    assert not page.run_button.isEnabled()
    page.approval.setChecked(True)
    page.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert read_journal(page.folder)["state"] == "COMPLETED", page.status.text()
    page.table.selectRow(0)
    page.inspect_selected()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert page.products
    page.show_qc()
    assert "unassigned" in page.details.toPlainText()
    page.resume_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert all(
        r["status"] == "SKIPPED_VERIFIED" for r in read_journal(page.folder)["nodes"].values()
    )
    page.search.setText("synthetic-0")
    assert sum(not page.table.isRowHidden(i) for i in range(2)) == 1
    assert window.workspace.currentWidget() is page


def test_batch_controls_and_configuration_invalidation(window, qtbot, tmp_path):
    page = configure(window, tmp_path)
    page.plan_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    page.approval.setChecked(True)
    page.workflow.appendPlainText(" ")
    assert page.plan is None and not page.run_button.isEnabled()
    page.plan_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    from dmri_studio.core.batch.engine import create_batch

    page.folder = create_batch(page.plan, page.plan.digest)
    control(page.folder, "pause")
    page.start(
        "run",
        lambda cancel, progress: (
            __import__("dmri_studio.core.batch.engine", fromlist=["BatchRunner"])
            .BatchRunner(page.folder)
            .run()
        ),
    )
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert read_journal(page.folder)["state"] == "PAUSED"
    page.table.selectRow(0)
    page.send_control("cancel", selected=True)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert any(r["status"] == "CANCELLED" for r in read_journal(page.folder)["nodes"].values())


def test_batch_history_and_visible_acquisition_table(window, qtbot, tmp_path):
    page = configure(window, tmp_path)
    qtbot.wait(60)
    assert page.table.viewport().height() >= 160
    page.plan_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    page.approval.setChecked(True)
    page.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert str(page.folder) in window.settings.get("batch/history", [])
    page.folder = None
    page.study_name.setText("different-study")
    page.workflow.setPlainText("{}")
    page.open_recent(1)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert page.folder is not None and page.table.rowCount() == 2
    assert page.plan is None
    assert page.study_name.text() == page.study.study_id
    assert json.loads(page.workflow.toPlainText())["nodes"]
    assert "tensor2metric" in page.preview.toPlainText()
