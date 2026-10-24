"""Task-first tracking controls edit the existing typed scientific task."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget

from dmri_studio.core.scientific import Task


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
    assert guided.count.maximum() == 10_000_000

    guided.task_buttons["filter"].click()
    assert panel.operation.currentData() == "filter"
    assert guided.pages.currentIndex() == 1
    assert guided.filter_tracts.isVisibleTo(guided)
    assert guided.filter_count.maximum() == 10_000_000
    assert not guided.source.isVisibleTo(guided)
    assert guided.ends_only.text().startswith("Test endpoints only")

    guided.task_buttons["density"].click()
    assert panel.operation.currentData() == "density"
    assert guided.pages.currentIndex() == 2
    assert guided.density_tracts.isVisibleTo(guided)
    assert not guided.filter_tracts.isVisibleTo(guided)
    assert "not an axon count" in guided.task_help.text()


def test_guided_review_remains_reachable_at_minimum_window_size(window):
    window.resize(960, 640)
    window.focus_tracking_plan()
    panel = window.scientific.tracking
    guided = panel.guided
    panel.ensureWidgetVisible(guided.run_button)
    assert panel.verticalScrollBar().maximum() > 0
    assert guided.run_button.mapTo(panel.viewport(), guided.run_button.rect().topLeft()).y() < 640


def test_original_atlas_filter_entry_explains_missing_viewer_inputs(window):
    window.focus_tracking_plan()
    guided = window.scientific.tracking.guided
    guided.select_task("filter")
    guided.atlas_filter_button.click()
    assert "Open the source tractogram, reference image and original label atlas" in (
        window.scientific.status.text()
    )


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
    guided.select_task("generate")
    assert panel.plan is not None and guided.approval.isChecked()
    guided.task_buttons["generate"].click()
    assert guided.task_buttons["generate"].isChecked()
    assert panel.plan is not None and guided.approval.isChecked()
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


def test_guided_inputs_cannot_change_while_scientific_worker_is_active(window):
    window.focus_tracking_plan()
    page = window.scientific
    guided = page.tracking.guided
    page.thread = object()
    try:
        guided.update_actions()
        assert not guided.pages.isEnabled()
        assert not guided.output.isEnabled()
        assert not guided.task_buttons["filter"].isEnabled()
    finally:
        page.thread = None
        guided.update_actions()
    assert guided.pages.isEnabled()


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


def test_filter_has_no_implicit_generation_count_or_length_cutoff(window):
    window.focus_tracking_plan()
    panel = window.scientific.tracking
    panel.owner.artifacts = [_artifact("tractogram", "tracks"), _artifact("anatomy", "grid")]
    panel.refresh()
    panel.count.setValue(17)
    panel.minimum.setValue(4)
    panel.maximum.setValue(25)
    panel.guided.select_task("filter")
    _select(panel.guided.filter_tracts, "tracks")
    _select(panel.guided.filter_reference, "grid")
    task = panel.selected_task()
    assert "number" not in task.parameters
    assert task.parameters["min_length_mm"] == 0
    assert task.parameters["max_length_mm"] == 1000000
    assert not panel.guided.filter_count.isEnabled()
    panel.guided.filter_limit.setChecked(True)
    panel.guided.filter_count.setValue(9)
    task = panel.selected_task()
    assert task.parameters["number"] == 9
    panel.guided.filter_limit.setChecked(False)
    assert "number" not in panel.selected_task().parameters


def test_legacy_filter_count_edit_remains_explicit(window):
    window.focus_tracking_plan()
    panel = window.scientific.tracking
    panel.owner.artifacts = [_artifact("tractogram", "tracks"), _artifact("anatomy", "grid")]
    panel.refresh()
    panel.guided.select_task("filter")
    _select(panel.tracts, "tracks")
    _select(panel.reference, "grid")
    panel.count.setValue(5)
    panel.maximum.setValue(30)
    task = panel.selected_task()
    assert task.parameters["number"] == 5
    assert task.parameters["max_length_mm"] == 30


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


def test_guided_act_uses_exact_supported_variant(window):
    window.focus_tracking_plan()
    panel = window.scientific.tracking
    panel.owner.artifacts = [
        _artifact("fod", "fod"),
        _artifact("5tt", "tissue"),
        _artifact("anatomy", "grid"),
    ]
    panel.refresh()
    guided = panel.guided
    _select(guided.method, "tckgen.ifod2")
    _select(guided.mode, "act")
    _select(guided.source, "fod")
    _select(guided.five_tt, "tissue")
    _select(guided.reference_generate, "grid")
    _select(guided.seed_method, "dynamic")
    assert guided.five_tt.isVisibleTo(guided)
    assert guided.backtrack.isVisibleTo(guided)
    assert guided.backtrack.isChecked()
    task = panel.selected_task()
    assert task.algorithm == "tckgen.act_ifod2"
    assert task.inputs["five_tt"] == "tissue"
    assert task.inputs["reference"] == "grid"
    assert task.parameters["backtrack"] == "on"
    assert task.parameters["crop_at_gmwmi"] == "on"


def test_guided_tensor_exposes_required_shell_selection(window):
    window.focus_tracking_plan()
    panel = window.scientific.tracking
    panel.owner.artifacts = [_artifact("dwi", "dwi"), _artifact("mask", "mask")]
    panel.refresh()
    guided = panel.guided
    _select(guided.method, "tckgen.tensor_det")
    _select(guided.source, "dwi")
    _select(guided.mask, "mask")
    _select(guided.seed_method, "image")
    _select(guided.seed, "mask")
    assert guided.tensor_shells.isVisibleTo(guided)
    guided.tensor_shells.setText("0, 1000")
    assert panel.selected_task().parameters["shells"] == (0.0, 1000.0)
    _select(guided.method, "tckgen.ifod2")
    assert not guided.tensor_shells.isVisibleTo(guided)


def test_guided_task_choice_exits_saved_task_precedence(window):
    window.focus_tracking_plan()
    panel = window.scientific.tracking
    panel.owner.tasks = [Task("saved", "tckedit", {}, {})]
    panel.refresh()
    panel.task_source.setCurrentIndex(panel.task_source.findData("saved"))
    assert panel.guided.pages.currentIndex() == 1
    assert "take precedence" in panel.guided.saved_task_notice.text()
    panel.guided.select_task("generate")
    assert panel.task_source.currentData() is None
    assert panel.operation.currentData() == "generate"
    assert panel.guided.saved_task_notice.text() == ""


@pytest.mark.skipif(
    not Path("/usr/local/mrtrix3/bin/tckgen").is_file(), reason="Actual MRtrix required"
)
def test_guided_preview_summarizes_real_approved_plan(window, qtbot, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "tracking_fixture", Path(__file__).parents[1] / "core/test_tracking.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    fod, mask, _ = fixture.source.__wrapped__(tmp_path)
    window.focus_tracking_plan()
    page = window.scientific
    page.artifacts = [fod, mask]
    page.tool_directory = Path("/usr/local/mrtrix3/bin")
    page.refresh()
    guided = page.tracking.guided
    _select(guided.method, "tckgen.sdstream")
    _select(guided.source, fod.id)
    _select(guided.mask, mask.id)
    _select(guided.seed_method, "image")
    _select(guided.seed, mask.id)
    guided.count.setValue(10)
    page.tracking.minimum.setValue(3)
    page.tracking.maximum.setValue(20)
    page.tracking.cutoff.setValue(0.01)
    page.tracking.seed_cutoff.setValue(0.02)
    page.tracking.attempts.setValue(10)
    guided.output.setText(str(tmp_path / "attempt"))
    guided.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert page.tracking.plan is not None, page.status.text()
    assert "tckgen.sdstream" in guided.summary.text()
    assert "Requested output streamlines: 10" in guided.summary.text()
    assert page.tracking.plan.digest in guided.summary.text()
    assert "SD_Stream" in guided.commands.toPlainText()
    assert not guided.run_button.isEnabled()
    guided.approval.setChecked(True)
    assert guided.run_button.isEnabled()
    guided.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert page.tracking.manifest is not None, page.status.text()
    assert page.tracking.manifest["status"] == "validated_pending_qc"
    assert page.tracking.maps.count() > 0
    assert guided.open_button.isEnabled()
    assert "QC" in guided.result.text()


@pytest.mark.skipif(
    not Path("/usr/local/mrtrix3/bin/tckmap").is_file(), reason="Actual MRtrix required"
)
def test_guided_density_and_unlimited_filter_prepare_real_commands(window, qtbot, tmp_path):
    from dmri_studio.core.execution import hash_file
    from dmri_studio.core.scientific import ImportRequest, import_artifact

    spec = importlib.util.spec_from_file_location(
        "roi_fixture", Path(__file__).parents[1] / "core/test_roi_filtering.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    source, declarations = fixture.roi_case.__wrapped__(tmp_path)
    tracts = import_artifact(
        ImportRequest("tractogram", source / "tracks.tck", declarations=declarations)
    )
    reference = import_artifact(
        ImportRequest("anatomy", source / "reference.nii.gz", declarations=declarations)
    )
    window.focus_tracking_plan()
    page = window.scientific
    page.artifacts = [tracts, reference]
    page.tool_directory = Path("/usr/local/mrtrix3/bin")
    page.refresh()
    guided = page.tracking.guided
    guided.select_task("filter")
    _select(guided.filter_tracts, tracts.id)
    _select(guided.filter_reference, reference.id)
    guided.output.setText(str(tmp_path / "filter"))
    guided.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert page.tracking.plan is not None, page.status.text()
    commands = page.tracking.plan.to_dict()["commands"]
    assert not any("-number" in c["argv"] for c in commands)
    assert "full pathway" in guided.summary.text()
    assert "Maximum output streamlines" not in guided.summary.text()
    original_hash = hash_file(source / "tracks.tck")
    guided.approval.setChecked(True)
    guided.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert page.tracking.manifest["status"] == "validated_pending_qc", page.status.text()
    assert hash_file(source / "tracks.tck") == original_hash

    guided.select_task("density")
    _select(guided.density_tracts, tracts.id)
    _select(guided.density_reference, reference.id)
    guided.output.setText(str(tmp_path / "density"))
    guided.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert page.tracking.plan is not None, page.status.text()
    assert page.tracking.plan.to_dict()["bindings"][0]["algorithm"] == "tckmap"
    assert "Reference grid: reference.nii.gz" in guided.summary.text()
    assert "tckmap" in guided.commands.toPlainText()
    guided.approval.setChecked(True)
    guided.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert page.tracking.manifest["status"] == "validated_pending_qc", page.status.text()
    assert page.tracking.maps.currentData()["kind"] == "scalar"
    assert guided.open_button.isEnabled()
    import nibabel as nib
    import numpy as np

    density_image = nib.load(page.tracking.maps.currentData()["path"])
    reference_image = nib.load(source / "reference.nii.gz")
    assert density_image.shape[:3] == reference_image.shape[:3]
    np.testing.assert_allclose(density_image.affine, reference_image.affine)
    assert hash_file(source / "tracks.tck") == original_hash


def test_verified_density_product_is_available_to_open_in_viewer(window, tmp_path):
    window.focus_tracking_plan()
    panel = window.scientific.tracking
    panel.guided.select_task("density")
    folder = tmp_path / "completed"
    record = {
        "status": "validated_pending_qc",
        "inputs_unchanged": True,
        "plan": {
            "output_directory": str(folder),
            "bindings": [
                {
                    "outputs": {
                        "density": "density.mif",
                        "density_nifti": "density.nii.gz",
                        "reference": "reference.nii.gz",
                    },
                    "source": {"declarations": {"subject_id": "synthetic", "space_id": "grid"}},
                }
            ],
        },
        "outputs": {
            "density.mif": {"sha256": "native-density-hash"},
            "density.nii.gz": {"sha256": "density-hash"},
            "reference.nii.gz": {"sha256": "reference-hash"},
        },
        "validation": {"products": []},
    }
    panel.result("tracking-run", record)
    assert panel.maps.count() == 1
    assert panel.maps.currentData()["kind"] == "scalar"
    assert panel.maps.currentData()["path"] == str(folder / "density.nii.gz")
    assert panel.guided.open_button.isEnabled()
    assert "QC" in panel.guided.result.text()
