"""Native Stage 4 approval, real execution, verified handoff and state recovery."""

import importlib.util
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from dmri_studio.gui.operation_state import State


def configure(window, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "tracking_fixture", Path(__file__).parents[1] / "core/test_tracking.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    f, m, r = fixture.source.__wrapped__(tmp_path)
    page = window.scientific
    window.focus_tracking_plan()
    page.artifacts = [f, m, r]
    page.refresh()
    page.tool_directory = Path("/usr/local/mrtrix3/bin")
    p = page.tracking
    p.method.setCurrentIndex(p.method.findData("tckgen.sdstream"))
    p.seed_method.setCurrentIndex(p.seed_method.findData("image"))
    for combo, value in [(p.source, f.id), (p.mask, m.id), (p.seed, m.id)]:
        combo.setCurrentIndex(combo.findData(value))
    p.count.setValue(10)
    p.seeds.setValue(1000)
    p.minimum.setValue(3)
    p.maximum.setValue(20)
    p.cutoff.setValue(0.01)
    p.seed_cutoff.setValue(0.02)
    p.attempts.setValue(10)
    p.output.setText(str(tmp_path / "attempt"))
    return page, p, r


@pytest.mark.skipif(
    not Path("/usr/local/mrtrix3/bin/tckgen").is_file(), reason="Actual MRtrix required"
)
def test_tracking_gui_execution_and_result(window, qtbot, tmp_path):
    page, p, r = configure(window, tmp_path)
    assert page.tabs.currentWidget() is p
    assert not p.run_button.isEnabled()
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert p.plan is not None, page.status.text()
    assert "SD_Stream" in p.log.toPlainText() and not (tmp_path / "attempt").exists()
    p.approval.setChecked(True)
    p.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert p.manifest["status"] == "validated_pending_qc", p.manifest.get("error")
    assert not page.operation_state.busy and page.operation_state.state == State.WARNING
    # The normal viewer owns geometry and bounded indexed rendering.
    assert window.scientific_viewer is None
    p.open_result()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    qtbot.waitUntil(lambda: window.scientific_viewer is not None, timeout=10000)
    viewer = window.scientific_viewer
    qtbot.waitUntil(lambda: len(viewer.groups) == 1 and not viewer.tasks, timeout=10000)
    assert viewer.groups[0].total == 10 and viewer.workspace_mode == "tractography"
    generated = next(a for a in page.artifacts if a.kind == "tractogram")
    reference = next(a for a in page.artifacts if a.kind == "anatomy")
    window.focus_tracking_plan()
    p.operation.setCurrentIndex(p.operation.findData("filter"))
    p.tracts.setCurrentIndex(p.tracts.findData(generated.id))
    p.reference.setCurrentIndex(p.reference.findData(reference.id))
    p.count.setValue(5)
    # The native deterministic method can exceed its requested stopping length
    # by a few integration steps. Choose an explicit enclosing filter range.
    p.maximum.setValue(30)
    p.output.setText(str(tmp_path / "filter"))
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert p.plan is not None, page.status.text()
    assert not any("-skip" in c["argv"] for c in p.plan.payload["commands"])
    p.approval.setChecked(True)
    p.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert p.manifest["status"] == "validated_pending_qc", p.manifest.get("error")
    assert p.manifest["validation"]["measurements"][0]["streamlines"] == 5
    filtered = next(
        a for a in page.artifacts if a.kind == "tractogram" and "filtered" in a.payload["path"]
    )
    p.operation.setCurrentIndex(p.operation.findData("density"))
    p.tracts.setCurrentIndex(p.tracts.findData(filtered.id))
    p.reference.setCurrentIndex(p.reference.findData(reference.id))
    p.output.setText(str(tmp_path / "density"))
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert p.plan is not None, page.status.text()
    p.approval.setChecked(True)
    p.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert p.manifest["status"] == "validated_pending_qc", p.manifest.get("error")
    assert any(a.kind == "density" for a in page.artifacts)
    p.open_qc()
    qtbot.waitUntil(lambda: window.qc.thread is None, timeout=10000)
    assert window.qc.catalog is not None
    p.open_run()
    qtbot.waitUntil(lambda: window.results.thread is None, timeout=10000)
    assert window.results.snapshot["products"][0]["kind"] == "scalar"
    assert not window.toast.isVisible()


@pytest.mark.parametrize("appearance", ["light", "dark"])
def test_tracking_controls_and_approval_invalidation(window, qtbot, tmp_path, appearance):
    page, p, _ = configure(window, tmp_path)
    window.theme.apply(appearance)
    assert p.method.count() == 5 and p.source.accessibleName()
    p.method.setCurrentIndex(p.method.findData("tckgen.tensor_det"))
    assert p.shells.isEnabled() and not p.samples.isEnabled() and p.rk4.isEnabled()
    assert not p.seed_method.model().item(p.seed_method.findData("dynamic")).isEnabled()
    p.operation.setCurrentIndex(p.operation.findData("filter"))
    assert p.ends.isEnabled() and not p.seed_method.isEnabled()
    p.operation.setCurrentIndex(p.operation.findData("density"))
    assert p.directional.isEnabled() and not p.ends.isEnabled()
    assert not page.operation_state.busy


def test_tracking_invalid_input_recovery(window, qtbot, tmp_path):
    page, p, _ = configure(window, tmp_path)
    p.source.setCurrentIndex(0)
    p.preview.click()
    assert "Select every required" in page.status.text() and page.thread is None
    assert not p.approval.isEnabled() and not p.run_button.isEnabled()
    p.seed_method.setCurrentIndex(p.seed_method.findData("sphere"))
    p.sphere.setText("not coordinates")
    p.preview.click()
    assert page.thread is None and not page.operation_state.busy


def test_ordered_roi_choices_survive_inventory_refresh(window, qtbot, tmp_path):
    from dmri_studio.core.scientific import ImportRequest, import_artifact

    page, p, _ = configure(window, tmp_path)
    mask = next(a for a in page.artifacts if a.kind == "mask")
    regions = [
        import_artifact(
            ImportRequest(
                "roi",
                Path(mask.payload["path"]),
                declarations=dict(mask.payload["declarations"]),
                provenance=name,
            )
        )
        for name in ("first test ROI", "second test ROI")
    ]
    page.artifacts.extend(regions)
    page.refresh()
    items = p.constraints["ordered"]
    second = items.takeItem(1)
    items.insertItem(0, second)
    items.item(0).setCheckState(Qt.CheckState.Checked)
    order = [items.item(i).data(Qt.ItemDataRole.UserRole) for i in range(items.count())]
    page.refresh()
    assert [items.item(i).data(Qt.ItemDataRole.UserRole) for i in range(items.count())] == order
    assert items.item(0).checkState() == Qt.CheckState.Checked


def test_selected_existing_viewer_roi_snapshot(window, qtbot, tmp_path, monkeypatch):
    import numpy as np
    from PySide6.QtWidgets import QFileDialog

    page, p, ref = configure(window, tmp_path)
    window.show_scene("tractography", auto_image=False)
    viewer = window.scientific_viewer
    viewer.load_image(Path(ref.payload["path"]))
    qtbot.waitUntil(lambda: not viewer.tasks, timeout=10000)
    data = np.zeros(viewer.data.shape, "u1")
    data[8:12, 8:12, 8:12] = 1
    viewer.add_roi(data, "Existing selected ROI")
    viewer.roi_list.setCurrentRow(0)
    original = viewer.rois[0]["data"].copy()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    window.import_tracking_rois()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    imported = [a for a in page.artifacts if a.kind == "roi"]
    assert len(imported) == 1
    assert imported[0].payload["declarations"]["subject_id"] == "analytic"
    np.testing.assert_array_equal(viewer.rois[0]["data"], original)
    assert len(list(tmp_path.glob("tracking-rois-*/roi-snapshots.json"))) == 1


@pytest.mark.skipif(
    not Path("/usr/local/mrtrix3/bin/tckgen").is_file(), reason="Actual MRtrix required"
)
def test_gui_cancellation_does_not_publish_partial_tracks(window, qtbot, tmp_path):
    page, p, _ = configure(window, tmp_path)
    p.count.setValue(100000)
    p.seeds.setValue(1000000)
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert p.plan is not None, page.status.text()
    p.approval.setChecked(True)
    p.run_button.click()
    qtbot.waitUntil(
        lambda: "Running " in p.log.toPlainText() and "tckgen" in p.log.toPlainText(), timeout=15000
    )
    page.cancel_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert page.operation_state.state == State.CANCELLED and not page.operation_state.busy
    if p.manifest is not None:
        assert p.manifest["status"] == "cancelled" and not p.manifest.get("outputs")
    assert p.maps.count() == 0 and not p.open_button.isEnabled()
    assert not p.result_label.text().startswith("Running")


@pytest.mark.skipif(
    not Path("/usr/local/mrtrix3/bin/tckgen").is_file(), reason="Actual MRtrix required"
)
def test_preview_to_run_directory_collision_is_terminal(window, qtbot, tmp_path):
    page, p, _ = configure(window, tmp_path)
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert p.plan is not None
    # A new directory appeared after approval preview. It must never be reused.
    destination = Path(p.output.text())
    destination.mkdir()
    (destination / "retained.txt").write_text("do not overwrite")
    p.approval.setChecked(True)
    p.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert page.operation_state.state == State.FAILED and not page.operation_state.busy
    assert not p.result_label.text().startswith("Running") and p.maps.count() == 0
    assert (destination / "retained.txt").read_text() == "do not overwrite"
