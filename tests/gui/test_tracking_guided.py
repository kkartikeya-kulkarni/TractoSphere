"""Task-first tracking controls edit the existing typed scientific task."""

from types import SimpleNamespace


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
