"""Saved-run navigation uses existing dataset ownership and exact history."""

import importlib.util
from pathlib import Path


def fixture_record(tmp_path):
    """Reuse the explicit synthetic producer contract from core tests."""
    spec = importlib.util.spec_from_file_location(
        "test_run_fixture", Path(__file__).parents[1] / "core/test_runs.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.make_record(tmp_path)


def test_open_saved_result_and_restore_history(window, qtbot, tmp_path):
    path = fixture_record(tmp_path)
    window.open_saved_run(path)
    page = window.results
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert page.snapshot and page.load_output.isEnabled()
    assert "validated_pending_qc" in page.status.text()
    page.load_output.click()
    qtbot.waitUntil(
        lambda: window.load_thread is None and window.dataset is not None, timeout=10000
    )
    assert window.dataset.paths.dwi == path.parent / "denoise.nii"
    assert window.workflow.history.resolve("denoise") == "done"
    assert window.workflow.history.resolve("eddy") == "unknown"
    assert window.workflow.history.claims[0].evidence == "inferred"
    assert "Scientific" in window.workflow.history_status.text()


def test_processing_disables_output_replacement(window, qtbot, tmp_path):
    path = fixture_record(tmp_path)
    window.open_saved_run(path)
    qtbot.waitUntil(lambda: window.results.thread is None)
    window.processing_busy(True)
    assert not window.results.load_output.isEnabled()
    window.processing_busy(False)
    assert window.results.load_output.isEnabled()
