"""Native matrix/approval/edge workflow; real MRtrix fixtures, no private data."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtWidgets import QFileDialog

from dmri_studio.core.scientific.records import restore_selection, save_selection
from dmri_studio.gui.operation_state import State

native_connectome = pytest.mark.skipif(
    not all(
        (Path("/usr/local/mrtrix3/bin") / name).is_file()
        for name in ("mrconvert", "tck2connectome")
    ),
    reason="Native MRtrix3 is required for connectome plan and execution tests",
)


def configure(window, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "connectome_fixture", Path(__file__).parents[1] / "core/test_connectome.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    artifacts = fixture.inputs.__wrapped__(tmp_path)
    page = window.scientific
    page.artifacts = list(artifacts.values())
    page.tool_directory = Path("/usr/local/mrtrix3/bin")
    page.refresh()
    window.focus_connectivity()
    panel = page.connectome
    for key, w in [
        ("tracts", panel.tracts),
        ("nodes", panel.atlas),
        ("reference", panel.reference),
        ("fa", panel.fa),
    ]:
        w.setCurrentIndex(w.findData(artifacts[key].id))
    panel.assignment.setCurrentIndex(panel.assignment.findData("end_voxels"))
    panel.diagonal.setChecked(False)
    panel.output.setText(str(tmp_path / "run"))
    return page, panel, artifacts


@pytest.mark.skipif(
    not Path("/usr/local/mrtrix3/bin/tck2connectome").is_file(),
    reason="Actual accepted MRtrix required",
)
def test_actual_gui_matrix_metrics_extraction_and_reopen(window, qtbot, tmp_path, monkeypatch):
    page, p, a = configure(window, tmp_path)
    assert page.tabs.currentWidget() is p and p.node_list.rowCount() == 3
    assert not p.run_button.isEnabled()
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert p.plan is not None, page.status.text()
    assert "tcksample" in p.commands.toPlainText()
    p.approve.setChecked(True)
    p.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert p.manifest["status"] == "validated_pending_qc", p.manifest.get("error")
    assert p.matrix.matrix.shape == (3, 3) and p.display_metric.count() == 3
    assert not page.cancel_button.isVisible() and page.operation_state.state == State.WARNING
    p.matrix.select_cell(0, 2)
    assert "2 contributing" in p.matrix.details.text()
    assert p.extract.isEnabled()
    p.matrix.key(SimpleNamespace(key="left"))
    assert p.matrix.cell == (0, 1)
    assert not p.extract.isEnabled()
    p.matrix.select_cell(0, 2)
    exported = tmp_path / "export.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *x, **kw: (str(exported), "CSV"))
    p.export()
    np.testing.assert_array_equal(np.loadtxt(exported, delimiter=","), p.matrix.matrix)
    assert Path(str(exported) + ".json").exists()
    for key, expected in [("mean_length", 6), ("mean_fa", 0.5)]:
        p.display_metric.setCurrentIndex(p.display_metric.findData(key))
        assert p.matrix.matrix[0, 2] == pytest.approx(expected)
    p.display_metric.setCurrentIndex(p.display_metric.findData("count"))
    p.logscale.setChecked(True)
    assert p.matrix.log_scale
    p.matrix.select_cell(0, 2)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *x, **kw: str(tmp_path))
    p.extract.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert p.plan is not None, page.status.text()
    assert "connectome2tck" in p.commands.toPlainText()
    p.approve.setChecked(True)
    p.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert p.last_product and p.manifest["status"] == "validated_pending_qc", p.summary.text()
    p.open_tracks.click()
    qtbot.waitUntil(
        lambda: window.scientific_viewer is not None and not window.scientific_viewer.tasks,
        timeout=15000,
    )
    assert window.scientific_viewer.groups[0].total == 2
    qtbot.waitUntil(lambda: len(window.scientific_viewer.rois) == 2, timeout=10000)
    window.focus_connectivity()
    assert p.bundle is not None
    source = tmp_path / "run/connectome-connectome.json"
    p.open_bundle(source)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert p.bundle_path == source and p.matrix.matrix.shape == (3, 3)
    assert page.status.text() == "Connectome opened; scientific QC requires review."
    save_selection(tmp_path / "selection.json", tuple(a.values()), tuple(page.tasks))
    art, tasks = restore_selection(tmp_path / "selection.json")
    assert len(art) == 4


@pytest.mark.parametrize("appearance", ["light", "dark"])
def test_matrix_display_selection_and_nonfinite_states(window, qtbot, tmp_path, appearance):
    page, p, _ = configure(window, tmp_path)
    window.theme.set_mode(appearance) if hasattr(window.theme, "set_mode") else window.theme.apply(
        appearance
    )
    matrix = np.array([[0, np.nan, 1.1], [0, 0, 0], [0, 0, 0.0]])
    nodes = [dict(parcel_id=i + 1, label=f"Node {i + 1}") for i in range(3)]
    p.matrix.display(matrix, nodes, "mean_fa", np.array([[1, 1, 1], [0, 0, 0], [0, 0, 0]]))
    p.matrix.select_cell(0, 1)
    assert "Invalid measurement" in p.matrix.details.text()
    p.matrix.select_cell(0, 0)
    assert "1 contributing" in p.matrix.details.text()
    p.matrix.select_cell(1, 1)
    assert "undefined mean" in p.matrix.details.text()
    p.matrix.select_cell(0, 2)
    assert "1.1000000000000001" in p.matrix.details.text()
    p.matrix.display(np.zeros((1, 1)), nodes[:1], "count")
    assert p.matrix.cell == (0, 0)
    assert not p.extract.isEnabled()


@native_connectome
def test_connectome_invalid_input_recovery_and_approval(window, qtbot, tmp_path):
    page, p, a = configure(window, tmp_path)
    p.fa.setCurrentIndex(0)
    p.preview.click()
    assert "Select all" in page.status.text()
    assert page.thread is None
    p.fa.setCurrentIndex(p.fa.findData(a["fa"].id))
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert p.plan is not None
    p.approve.setChecked(True)
    p.diagonal.setChecked(True)
    assert p.plan is None and not p.run_button.isEnabled()


def test_matrix_comparison_rejects_different_nodes(window, qtbot, tmp_path):
    page, p, a = configure(window, tmp_path)
    p.display_metric.addItem("Count", "count")
    p.bundle = {"nodes": [{"parcel_id": 1}], "matrices": {"count": {}}, "assignment_parameters": {}}
    with pytest.raises(ValueError, match="Atlas/node mapping"):
        p.show_comparison(
            {"nodes": [{"parcel_id": 2}], "matrices": {"count": {}}, "assignment_parameters": {}}
        )


def test_export_blocks_source_changed_after_display(window, qtbot, tmp_path, monkeypatch):
    """Export must refer to the exact displayed matrix snapshot, including raw CSV."""
    from dmri_studio.core.execution import hash_file

    page, p, _ = configure(window, tmp_path)
    source = tmp_path / "original.csv"
    source.write_text("0,1,2\n1,0,3\n2,3,0\n")
    nodes = [dict(parcel_id=i + 1, label=f"Node {i + 1}") for i in range(3)]
    p.result(
        "connectome-csv",
        (source, np.loadtxt(source, delimiter=","), nodes, "count", hash_file(source)),
    )
    before = p.matrix.matrix.copy()
    source.write_text("0,4,5\n4,0,6\n5,6,0\n")
    dest = tmp_path / "unsafe-export.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(dest), "CSV"))
    p.export()
    assert "source changed" in page.status.text()
    assert not dest.exists()
    np.testing.assert_array_equal(before, p.matrix.matrix)
    assert p.last_product is None and not p.extract.isEnabled()


def test_mean_comparison_keeps_undefined_edges_and_cannot_extract(window, qtbot, tmp_path):
    page, p, _ = configure(window, tmp_path)
    nodes = [dict(parcel_id=i + 1, label=f"Node {i + 1}") for i in range(3)]
    a = np.eye(3)
    b = np.eye(3)
    occ = np.eye(3)
    other_path = tmp_path / "other.csv"
    np.savetxt(other_path, b, delimiter=",")
    other_occ = tmp_path / "other-occupancy.csv"
    np.savetxt(other_occ, occ, delimiter=",")
    p.bundle = {
        "nodes": nodes,
        "matrices": {"mean_fa": {}},
        "assignment_parameters": {"assignment": "end_voxels"},
    }
    p.nodes = nodes
    p.matrix_paths = {"mean_fa": "current.csv"}
    p.matrix_cache = {"mean_fa": a}
    p.occupancy_cache = occ
    p.display_metric.blockSignals(True)
    p.display_metric.addItem("Mean FA", "mean_fa")
    p.display_metric.blockSignals(False)
    p.show_comparison(
        {
            "nodes": nodes,
            "matrices": {"mean_fa": {"path": str(other_path)}},
            "occupancy": str(other_occ),
            "assignment_parameters": p.bundle["assignment_parameters"],
        }
    )
    p.matrix.select_cell(0, 1)
    p.update_actions()
    assert "Undefined difference" in p.matrix.details.text()
    assert not p.extract.isEnabled() and not p.export_button.isEnabled()
    p.matrix.select_cell(0, 0)
    p.update_actions()
    assert "Display difference" in p.matrix.details.text() and not p.extract.isEnabled()
    # Negative FA differences are legitimate differences, not nonphysical FA.
    a[0, 0] = 0.5
    other = {
        "nodes": nodes,
        "matrices": {"mean_fa": {"path": str(other_path)}},
        "occupancy": str(other_occ),
        "assignment_parameters": p.bundle["assignment_parameters"],
    }
    p.show_comparison(other)
    p.matrix.select_cell(0, 0)
    assert p.matrix.matrix[0, 0] == -0.5
    assert "nonphysical FA" not in p.matrix.details.text()
    # Positive track occupancy cannot make a zero-total-weight mean valid.
    current_valid = tmp_path / "current-validity.csv"
    comparison_valid = tmp_path / "comparison-validity.csv"
    np.savetxt(current_valid, np.ones((3, 3)), delimiter=",")
    valid = np.ones((3, 3))
    valid[0, 0] = 0
    np.savetxt(comparison_valid, valid, delimiter=",")
    p.bundle["weight_validity"] = str(current_valid)
    other["weight_validity"] = str(comparison_valid)
    other["matrices"]["sift2_mean_fa"] = {"path": str(other_path)}
    p.matrix_cache["sift2_mean_fa"] = a
    p.display_metric.blockSignals(True)
    p.display_metric.addItem("Weighted mean FA", "sift2_mean_fa")
    p.display_metric.setCurrentIndex(p.display_metric.findData("sift2_mean_fa"))
    p.display_metric.blockSignals(False)
    p.show_comparison(other)
    p.matrix.select_cell(0, 0)
    assert "Undefined difference" in p.matrix.details.text()
    assert p.matrix.ax.images[0].get_array().mask[0, 0]
    assert p.matrix.occupancy[1, 1] == 1


@native_connectome
def test_connectome_cancel_is_terminal_and_no_success(window, qtbot, tmp_path):
    page, p, _ = configure(window, tmp_path)
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    p.approve.setChecked(True)
    p.run_button.click()
    page.cancel_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert page.operation_state.state == State.CANCELLED
    assert p.manifest is None or p.manifest["status"] == "cancelled"
    assert p.bundle is None and p.last_product is None and not page.cancel_button.isVisible()
