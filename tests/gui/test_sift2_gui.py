"""Real native SIFT2/weighted matrix controls over controlled analytic scientific files."""

import importlib.util
import json
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
from matplotlib.colors import to_rgba
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QFileDialog

from dmri_studio.core.execution import hash_file
from dmri_studio.core.scientific import ImportRequest, import_artifact
from dmri_studio.gui.operation_state import State


def configure(window, tmp_path):
    """Known fibers/tissue; no real subject data embedded in automated tests."""
    spec = importlib.util.spec_from_file_location(
        "sift_tissue", Path(__file__).parents[1] / "core/test_act.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    five, ref, fod, mask = m.tissue.__wrapped__(tmp_path)
    path = Path(fod.payload["path"]).parent / "tracks.tck"
    lines = [
        np.array([[x, y, 0] for x in np.linspace(-9, 9, 30)], "f4") for y in np.linspace(-3, 3, 40)
    ]
    nib.streamlines.save(nib.streamlines.Tractogram(lines, affine_to_rasmm=np.eye(4)), path)
    tracts = import_artifact(
        ImportRequest(
            "tractogram",
            path,
            declarations={
                **ref.to_dict()["declarations"],
                "sift2_fod_sha256": hash_file(Path(fod.payload["path"])),
            },
        )
    )
    window.focus_tracking_plan()
    page = window.scientific
    page.tool_directory = Path("/usr/local/mrtrix3/bin")
    page.artifacts = [five, ref, fod, mask, tracts]
    page.refresh()
    p = page.sift2
    page.tabs.setCurrentWidget(p)
    for combo, a in [(p.tracts, tracts), (p.fod, fod), (p.reference, ref), (p.five_tt, five)]:
        combo.setCurrentIndex(combo.findData(a.id))
    p.output.setText(str(tmp_path / "sift2"))
    return page, p, tracts


@pytest.mark.parametrize("appearance", ["light", "dark"])
def test_sift2_controls_native_defaults_and_state(window, qtbot, tmp_path, appearance):
    page, p, _ = configure(window, tmp_path)
    window.theme.apply(appearance)
    task = p.selected_task()
    assert task.algorithm == "tcksift2" and task.parameters == {"act": "on", "fd_scale_gm": "off"}
    assert "five_tt" in task.inputs and not p.run_button.isEnabled()
    p.settings["reg_tv"].setText("0.2")
    assert p.selected_task().parameters["reg_tv"] == 0.2
    p.policy.setCurrentIndex(p.policy.findData("none"))
    assert "five_tt" not in p.selected_task().inputs
    assert not page.operation_state.busy and not window.toast.isVisible()
    assert p.canvas.isHidden()


def test_native_gui_sift2_approval_histogram_reopen_and_viewer(window, qtbot, tmp_path):
    page, p, tract = configure(window, tmp_path)
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert p.plan is not None, page.status.text()
    assert "-out_mu" in p.log.toPlainText() and "-csv" in p.log.toPlainText()
    assert not p.run_button.isEnabled()
    p.approval.setChecked(True)
    p.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert p.manifest["status"] == "validated_pending_qc", p.manifest.get("error")
    assert p.record["streamlines"] == 40 and len(p.values) == 40
    assert len(p.figure.axes) == 1 and p.figure.axes[0].patches
    for appearance in ("light", "dark"):
        window.theme.apply(appearance)
        qtbot.wait(20)
        palette = QApplication.palette()
        background = palette.color(QPalette.ColorRole.Window)
        foreground = palette.color(QPalette.ColorRole.WindowText)
        assert p.figure.get_facecolor() == to_rgba(background.name())
        assert p.ax.xaxis.label.get_color() == foreground.name()
        assert abs(background.lightness() - foreground.lightness()) > 100
        assert not p.canvas.isHidden()
    assert page.operation_state.state == State.WARNING and not page.operation_state.busy
    assert not page.cancel_button.isVisible() and not window.toast.isVisible()
    p.index.setValue(17)
    assert format(p.values[17], ".17g") in p.weight_label.text()
    assert any(a.kind == "streamline_weights" for a in page.artifacts)
    p.open_result()
    qtbot.waitUntil(
        lambda: window.scientific_viewer is not None and not window.scientific_viewer.tasks,
        timeout=20000,
    )
    v = window.scientific_viewer
    assert v.groups[0].total == 40 and len(v.groups[0].weights) == 40
    v.coloring.setCurrentText("SIFT2 weight")
    v.select_tract(("tract", 0), 17)
    assert (
        "source index 17" in v.weight_legend.text()
        and format(v.groups[0].weights[17], ".17g") in v.weight_legend.text()
    )
    assert ("tract", 0) in v.scene.actors
    assert p.read_record(p.record["record_path"])[0]["mu"] == p.record["mu"]
    # Actual missing source association must block, rather than color fake factors.
    broken = dict(p.record)
    broken["tractogram_sha256"] = "0" * 64
    bad = tmp_path / "wrong.json"
    bad.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="exact unedited"):
        v.read_group_weights(v.groups[0], bad)
    window.focus_tracking_plan()
    page.tabs.setCurrentWidget(p)
    p.settings["reg_tv"].setText("0.3")
    assert p.plan is None and not p.approval.isChecked()
    p.failed("invalid FOD")
    assert p.values is None and p.record is None and "failed" in p.summary.text()
    assert p.canvas.isHidden()
    p.result("sift2-open", p.read_record(tmp_path / "sift2/sift2-weights.json"))
    assert "Processing completed" in p.summary.text() and p.values is not None
    p.tracts.setCurrentIndex(0)
    assert p.record is None and p.values is None and not p.figure.axes
    assert p.canvas.isHidden()
    assert "Input changed" in p.summary.text() and not p.open_tracks.isEnabled()


def test_weighted_connectome_gui_controls(window, qtbot, tmp_path):
    page, p, _ = configure(window, tmp_path)
    c = page.connectome
    c.metric.setCurrentIndex(c.metric.findData("all_sift2"))
    assert c.weights.isEnabled() and c.fa.isEnabled()
    with pytest.raises(ValueError, match="required"):
        c.selected_task()
    c.metric.setCurrentIndex(c.metric.findData("all"))
    assert not c.weights.isEnabled()
    assert c.metric.count() == 8


def test_native_weighted_gui_six_metrics_and_subset_weights(window, qtbot, tmp_path, monkeypatch):
    from test_connectome_gui import configure as configure_matrix

    from dmri_studio.core.scientific.sift2 import weight_statistics

    page, p, arts = configure_matrix(window, tmp_path)
    source = Path(arts["tracts"].payload["path"])
    path = source.with_name("factors.txt")
    weights = np.array([0.2, 1.8, 0, 1])
    np.savetxt(path, weights, fmt="%.17g")
    record = dict(
        schema="tractosphere.streamline-weights/1",
        representation="weight_factors",
        ordering="exact source file streamline order",
        weights=str(path),
        weights_sha256=hash_file(path),
        tractogram=str(source),
        tractogram_sha256=hash_file(source),
        streamlines=4,
        statistics=weight_statistics(weights),
        origin="Known analytic test vector, not real SIFT2 output",
    )
    side = path.with_suffix(".json")
    side.write_text(json.dumps(record))
    w = import_artifact(
        ImportRequest(
            "streamline_weights",
            path,
            declarations={
                **arts["tracts"].to_dict()["declarations"],
                "tractogram_id": arts["tracts"].id,
                "weight_record": str(side),
            },
        )
    )
    page.artifacts.append(w)
    page.refresh()
    p.metric.setCurrentIndex(p.metric.findData("all_sift2"))
    p.weights.setCurrentIndex(p.weights.findData(w.id))
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert p.plan is not None, page.status.text()
    assert p.commands.toPlainText().count("-tck_weights_in") == 3
    p.approve.setChecked(True)
    p.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert p.manifest["status"] == "validated_pending_qc", p.manifest.get("error")
    assert p.display_metric.count() == 6
    for key in ["sift2_weighted", "sift2_mean_length", "sift2_mean_fa"]:
        p.display_metric.setCurrentIndex(p.display_metric.findData(key))
        p.matrix.select_cell(0, 2)
        assert (
            "2 contributing streamlines" in p.matrix.details.text()
            and "edge factor sum" in p.matrix.details.text()
        )
    p.matrix.select_cell(0, 2)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: str(tmp_path))
    p.extract.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert "-prefix_tck_weights_out" in p.commands.toPlainText()
    p.approve.setChecked(True)
    p.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert p.manifest["status"] == "validated_pending_qc", p.manifest.get("error")
    assert p.last_product["weight_record"]
    p.open_tracks.click()
    qtbot.waitUntil(
        lambda: window.scientific_viewer is not None and not window.scientific_viewer.tasks,
        timeout=20000,
    )
    group = window.scientific_viewer.groups[0]
    assert group.total == 2
    np.testing.assert_allclose(group.weights, [0.2, 1.8], atol=1e-7, rtol=0)


def test_gui_sift2_cancellation_is_neutral_and_partial_not_reusable(window, qtbot, tmp_path):
    page, p, _ = configure(window, tmp_path)
    p.settings["min_iters"].setText("10000")
    p.settings["max_iters"].setText("10000")
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert p.plan is not None, page.status.text()
    p.approval.setChecked(True)
    p.run_button.click()
    qtbot.waitUntil(
        lambda: "Resource sample" in p.log.toPlainText() and "/tcksift2 " in p.log.toPlainText(),
        timeout=15000,
    )
    page.cancel.set()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert p.manifest["status"] == "cancelled" and page.operation_state.state == State.CANCELLED
    assert p.record is None and not p.open_tracks.isEnabled() and not p.run_button.isEnabled()
    assert not page.cancel_button.isVisible() and not window.toast.isVisible()
