"""Saved-run navigation uses existing dataset ownership and exact history."""

import importlib.util
import json
from pathlib import Path

import pytest


def fixture_record(tmp_path):
    """Reuse the explicit synthetic producer contract from core tests."""
    spec = importlib.util.spec_from_file_location(
        "test_run_fixture", Path(__file__).parents[1] / "core/test_runs.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.make_record(tmp_path)


def act_fixture(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "test_act_fixture", Path(__file__).parents[1] / "core/test_act.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.tissue.__wrapped__(tmp_path)


@pytest.mark.skipif(
    not Path("/usr/local/mrtrix3/bin/5tt2gmwmi").is_file(),
    reason="Native MRtrix3 5tt2gmwmi is required",
)
def test_saved_gmwmi_run_offers_exact_verified_viewer_product(window, qtbot, tmp_path):
    from dmri_studio.core.execution import execute_plan
    from dmri_studio.core.scientific import Task
    from dmri_studio.core.scientific.anatomy import prepare_anatomy

    five, reference, _, _ = act_fixture(tmp_path)
    task = Task("interface", "5tt2gmwmi", {"five_tt": five.id, "reference": reference.id})
    plan = prepare_anatomy(
        (five, reference),
        (task,),
        tmp_path / "attempt",
        tool_directory=Path("/usr/local/mrtrix3/bin"),
    )
    record = execute_plan(None, plan, approval=plan.digest)
    assert record["status"] == "validated_pending_qc"

    window.open_saved_run(tmp_path / "attempt/manifest.json")
    page = window.results
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert page.snapshot is not None, page.status.text()
    assert page.products.count() == 1
    product = page.snapshot["products"][0]
    assert product["kind"] == "scalar"
    assert product["artifact"]["kind"] == "gmwmi"
    assert product["path"] == str(tmp_path / "attempt/interface-gmwmi.nii.gz")
    assert page.products.itemText(0).startswith("Gmwmi ·")
    assert page.open_qc.isEnabled()

    page.load_requested.disconnect(window.load_saved_result)
    selected = []
    page.load_requested.connect(selected.append)
    page.load_output.click()
    assert selected[0]["product"] == product

    from dmri_studio.core.runs import inspect_run

    manifest_path = tmp_path / "attempt/manifest.json"
    tampered = json.loads(manifest_path.read_text())
    tampered["validation"]["products"][0]["artifact"]["sources"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(tampered))
    with pytest.raises(ValueError, match="product identity"):
        inspect_run(manifest_path)


def test_saved_five_tissue_product_restores_exact_typed_input(window, qtbot, tmp_path):
    from dmri_studio.core.execution import hash_file

    five, _, _, _ = act_fixture(tmp_path)
    product = {
        "kind": "5tt",
        "path": five.payload["path"],
        "sha256": hash_file(Path(five.payload["path"])),
        "artifact": five.to_dict(),
    }
    window.load_saved_result({"product": product, "handoff": {}})
    qtbot.waitUntil(lambda: window.scientific.thread is None, timeout=15000)
    assert any(a.id == five.id for a in window.scientific.artifacts)
    assert window.scientific.tabs.currentWidget() is window.scientific.anatomy


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


def test_recent_run_requires_explicit_action_and_rechecks_evidence(window, qtbot, tmp_path):
    """A remembered path is navigation, not an automatic or pre-approved load."""
    from dmri_studio.gui.results import ResultsPage

    path = fixture_record(tmp_path)
    window.settings.set("last_run_manifest", str(path))
    page = ResultsPage(window.settings)
    qtbot.addWidget(page)
    assert page.snapshot is None and page.thread is None
    page.recent.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert page.snapshot and page.snapshot["manifest"] == str(path)
    assert "Scientific review required" in page.status.text()
    assert "verified output" in page.status.text()

    page.snapshot = None
    path.unlink()
    page.recent.click()
    assert page.snapshot is None
    assert "unavailable" in page.status.text()
