"""Actual CSD execution/approval and typed products inside the native GUI."""

import importlib.util
from pathlib import Path

import pytest

from dmri_studio.gui.operation_state import State


@pytest.mark.skipif(
    not Path("/usr/local/mrtrix3/bin/dwi2fod").is_file(), reason="Actual validated MRtrix required"
)
def test_csd_gui_real_execution_import_preview_products(window, qtbot, tmp_path, tmp_path_factory):
    # Reuse one scientifically documented controlled phantom definition, not
    # another scientific implementation or hand-authored fake response output.
    spec = importlib.util.spec_from_file_location(
        "csd_fixture", Path(__file__).parents[1] / "core/test_csd.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    _, source, mask = fixture.sample.__wrapped__(tmp_path_factory)
    page = window.scientific
    window.focus_models()
    page.artifacts = [source, mask]
    page.refresh()
    page.tool_directory = Path("/usr/local/mrtrix3/bin")
    panel = page.csd
    page.tabs.setCurrentWidget(panel)
    panel.source.setCurrentIndex(panel.source.findData(source.id))
    panel.mask.setCurrentIndex(panel.mask.findData(mask.id))
    panel.response_mode.setCurrentIndex(panel.response_mode.findData("dhollander"))
    panel.shells.setText("0,1000,2000,3000")
    panel.lmax.setValue(4)
    panel.output.setText(str(tmp_path / "attempt"))
    # Default erosion is inappropriate for this 20-cube fixture; retain guided
    # production defaults and explicitly edit the real advanced task below.
    panel.guided.click()
    assert len(page.tasks) == 2
    from dmri_studio.core.scientific import Task

    old = page.tasks[0]
    page.tasks[0] = Task(old.id, old.algorithm, dict(old.inputs), {**old.parameters, "erode": 0})
    page.refresh()
    panel.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert panel.plan is not None, page.status.text()
    assert not panel.run_button.isEnabled() and not (tmp_path / "attempt").exists()
    assert (
        "dwi2response dhollander" in panel.log.toPlainText()
        and "dwi2fod msmt_csd" in panel.log.toPlainText()
    )
    panel.approval.setChecked(True)
    panel.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert panel.manifest["status"] == "validated_pending_qc", panel.manifest.get("error")
    assert not page.operation_state.busy and page.operation_state.state in {
        State.COMPLETED,
        State.WARNING,
    }
    index = next(i for i in range(panel.maps.count()) if panel.maps.itemData(i)["kind"] == "fod")
    panel.maps.setCurrentIndex(index)
    panel.open_result()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    # Stage 3.5 opens typed coefficients in the shared image workspace, not as
    # scalar volumes or a disconnected metadata-only panel.
    qtbot.waitUntil(lambda: window.scientific_viewer is not None, timeout=10000)
    viewer = window.scientific_viewer
    qtbot.waitUntil(
        lambda: viewer.fod is not None and not viewer.tasks and not viewer.fod_timer.isActive(),
        timeout=10000,
    )
    assert viewer.workspace_mode == "image"
    assert viewer.fod.sha256 == panel.maps.currentData()["sha256"]
    assert ("fod", 0) in viewer.scene.actors
    panel.open_qc()
    qtbot.waitUntil(lambda: window.qc.thread is None, timeout=10000)
    assert window.qc.catalog is not None
    panel.open_run()
    qtbot.waitUntil(lambda: window.results.thread is None, timeout=10000)
    assert any(p["kind"] == "fod" for p in window.results.snapshot["products"])
    assert not window.toast.isVisible()
