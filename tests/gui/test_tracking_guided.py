"""Task-first tracking controls edit the existing typed scientific task."""

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget


def _artifact(kind, name):
    return SimpleNamespace(
        id=name,
        kind=kind,
        payload={"path": f"/synthetic/{name}.nii.gz"},
    )


def _select(combo, key):
    index = combo.findData(key)
    assert index >= 0
    combo.setCurrentIndex(index)


def test_task_first_tracking_shows_separate_operations(window):
    window.focus_tracking_plan()
    panel = window.scientific.tracking
    guided = panel.guided
    assert guided.task_buttons["generate"].isChecked()
    assert guided.pages.currentIndex() == 0
    assert guided.source.isVisibleTo(guided)
    assert not guided.filter_tracts.isVisibleTo(guided)

    guided.task_buttons["filter"].click()
    assert panel.operation.currentData() == "filter"
    assert guided.pages.currentIndex() == 1
    assert guided.filter_tracts.isVisibleTo(guided)
    assert not guided.source.isVisibleTo(guided)
    assert guided.ends_only.text().startswith("Test endpoints only")

    guided.task_buttons["density"].click()
    assert panel.operation.currentData() == "density"
    assert guided.pages.currentIndex() == 2
    assert guided.density_tracts.isVisibleTo(guided)
    assert not guided.filter_tracts.isVisibleTo(guided)
    assert "not an axon count" in guided.task_help.text()


def test_guided_generation_uses_existing_typed_task(window):
    window.focus_tracking_plan()
    panel = window.scientific.tracking
    panel.owner.artifacts = [
        _artifact("fod", "fod"),
        _artifact("mask", "mask"),
    ]
    panel.refresh()
    guided = panel.guided
    _select(guided.method, "tckgen.sdstream")
    _select(guided.source, "fod")
    _select(guided.mask, "mask")
    _select(guided.seed_method, "image")
    _select(guided.seed, "mask")
    guided.count.setValue(27)
    task = panel.selected_task()
    assert task.algorithm == "tckgen.sdstream"
    assert task.inputs["fod"] == "fod"
    assert task.inputs["mask"] == "mask"
    assert task.inputs["seed_mask"] == "mask"
    assert task.parameters["select"] == 27
    assert panel.count.value() == 27


def test_task_change_invalidates_approval_but_return_reuses_panel(window):
    window.focus_tracking_plan()
    panel = window.scientific.tracking
    guided = panel.guided
    panel.plan = SimpleNamespace(digest="approved")
    panel.update_actions()
    guided.approval.setChecked(True)
    assert panel.approval.isChecked() and guided.run_button.isEnabled()
    guided.select_task("filter")
    assert panel.plan is None
    assert not panel.approval.isChecked()
    assert not guided.approval.isChecked()
    assert not guided.run_button.isEnabled()

    window.focus_models()
    window.focus_tracking_plan()
    assert window.scientific.tracking is panel
    assert panel.guided is guided
    assert panel.operation.currentData() == "filter"


def test_advanced_selection_updates_guided_state(window):
    window.focus_tracking_plan()
    panel = window.scientific.tracking
    panel.owner.artifacts = [_artifact("tractogram", "tracks"), _artifact("anatomy", "grid")]
    panel.refresh()
    panel.operation.setCurrentIndex(panel.operation.findData("filter"))
    _select(panel.tracts, "tracks")
    _select(panel.reference, "grid")
    panel.ends.setChecked(True)
    assert panel.guided.filter_tracts.currentData() == "tracks"
    assert panel.guided.filter_reference.currentData() == "grid"
    assert panel.guided.ends_only.isChecked()
    task = panel.selected_task()
    assert task.algorithm == "tckedit"
    assert task.parameters["ends_only"] == "on"


def test_roi_assignment_search_removal_and_waypoint_order(window):
    window.focus_tracking_plan()
    panel = window.scientific.tracking
    panel.owner.artifacts = [
        _artifact("tractogram", "tracks"),
        _artifact("anatomy", "grid"),
        _artifact("roi", "region-a"),
        _artifact("roi", "region-b"),
    ]
    panel.refresh()
    panel.guided.select_task("filter")
    guided = panel.guided
    _select(guided.filter_tracts, "tracks")
    _select(guided.filter_reference, "grid")
    guided.set_imported_roi_metadata(
        {
            "region-a": {"name": "Left Internal Capsule", "color": (0.8, 0.2, 0.1)},
            "region-b": {"name": "Ventricle", "color": (0.2, 0.4, 0.9)},
        }
    )
    original_item = guided.roi_inventory.item(0)
    guided.roi_search.setText("capsule")
    assert not guided.roi_inventory.item(0).isHidden()
    assert guided.roi_inventory.item(1).isHidden()
    guided.roi_inventory.setCurrentRow(0)
    guided.assign_roi("include")
    guided.assign_roi("ordered")
    guided.roi_search.clear()
    guided.roi_inventory.setCurrentRow(1)
    guided.assign_roi("exclude")
    guided.assign_roi("ordered")
    assert guided.roi_groups["include"].item(0).text() == "Left Internal Capsule"
    assert guided.roi_groups["exclude"].item(0).text() == "Ventricle"
    assert [
        guided.roi_groups["ordered"].item(i).data(Qt.ItemDataRole.UserRole) for i in range(2)
    ] == ["region-a", "region-b"]
    guided.roi_groups["ordered"].setCurrentRow(1)
    guided.move_waypoint(-1)
    task = panel.selected_task()
    assert task.inputs["include_1"] == "region-a"
    assert task.inputs["exclude_1"] == "region-b"
    assert task.inputs["ordered_1"] == "region-b"
    assert task.inputs["ordered_2"] == "region-a"
    panel.refresh()
    assert guided.roi_inventory.item(0) is original_item  # unchanged inventory is reused
    guided.roi_groups["exclude"].setCurrentRow(0)
    guided.remove_roi("exclude")
    assert guided.roi_groups["exclude"].count() == 0
    assert "exclude_1" not in panel.selected_task().inputs


def test_density_hides_roi_constraints_without_discarding_them(window):
    window.focus_tracking_plan()
    panel = window.scientific.tracking
    panel.owner.artifacts = [_artifact("roi", "region-a")]
    panel.refresh()
    panel.guided.roi_inventory.setCurrentRow(0)
    panel.guided.assign_roi("include")
    assert panel.guided.roi_groups["include"].count() == 1
    panel.guided.select_task("density")
    assert not panel.guided.roi_section.isVisibleTo(panel.guided)
    panel.guided.select_task("generate")
    assert panel.guided.roi_section.isVisibleTo(panel.guided)
    assert panel.guided.roi_groups["include"].count() == 1


def test_viewer_highlight_requires_the_original_roi_object(window, monkeypatch):
    window.focus_tracking_plan()
    reference = object()
    region = {"name": "Selected ROI"}
    roi_list = QListWidget()
    roi_list.addItem("Selected ROI")
    viewer = SimpleNamespace(reference=reference, rois=[region], roi_list=roi_list)
    monkeypatch.setattr(window, "scientific_viewer", viewer)
    window._tracking_roi_pending = {0: (reference, region)}
    window._tracking_rois_imported({"roi-id": {"viewer_index": 0}})
    focused = []
    monkeypatch.setattr(window, "focus_viewer", lambda: focused.append(True))
    window.highlight_tracking_roi("roi-id")
    assert roi_list.currentRow() == 0 and focused == [True]
    viewer.rois = [{"name": "Selected ROI"}]  # same label, different source object
    window.highlight_tracking_roi("roi-id")
    assert focused == [True]
    assert "changed or was removed" in window.scientific.status.text()
    window.scientific_viewer = None
