"""Native tissue-task walkthrough with actual MRtrix output and Viewer handoff.

The analytic tissue fixture is not evidence of anatomical or ACT validity.
"""

import importlib.util
import os
from pathlib import Path

import pytest

from dmri_studio.core.execution import hash_file


@pytest.mark.skipif(
    os.environ.get("QT_QPA_PLATFORM") != "cocoa"
    or not Path("/usr/local/mrtrix3/bin/5tt2vis").is_file(),
    reason="Native Cocoa/VTK and compatible MRtrix3 are required",
)
def test_native_five_tissue_visualization_qc_and_source_integrity(window, qtbot, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "act_fixture", Path(__file__).parents[1] / "core/test_act.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    five, reference, _, _ = module.tissue.__wrapped__(tmp_path)
    original = hash_file(Path(five.payload["path"]))

    window.focus_tracking_plan()
    page = window.scientific
    page.artifacts = [five, reference]
    page.tool_directory = Path("/usr/local/mrtrix3/bin")
    page.refresh()
    panel = page.anatomy
    page.tabs.setCurrentWidget(panel)
    panel.operation.setCurrentIndex(panel.operation.findData("5tt2vis"))
    panel.source.setCurrentIndex(panel.source.findData(five.id))
    panel.reference.setCurrentIndex(panel.reference.findData(reference.id))
    panel.output.setText(str(tmp_path / "tissue-attempt"))
    assert "existing 5TT" in panel.task_help.text()
    assert "Inputs selected" in panel.input_help.text()
    panel.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert panel.plan is not None, page.status.text()
    assert panel.plan.payload["commands"]
    panel.approval.setChecked(True)
    panel.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert panel.manifest["status"] == "validated_pending_qc", page.status.text()
    assert panel.manifest["validation"]["scientific_acceptance"] == "unassigned"
    assert panel.maps.count() > 0
    assert hash_file(Path(five.payload["path"])) == original

    product = panel.maps.currentData()
    panel.open_button.click()
    qtbot.waitUntil(
        lambda: (
            window.scientific_viewer is not None
            and window.scientific_viewer.reference is not None
            and window.scientific_viewer.reference.path == Path(product["path"])
        ),
        timeout=20000,
    )
    window.focus_tracking_plan()
    panel.qc_button.click()
    qtbot.waitUntil(lambda: window.qc.thread is None, timeout=15000)
    assert "review" in window.qc.status.text().lower()
