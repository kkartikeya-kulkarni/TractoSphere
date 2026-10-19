"""Shared-scene desktop routing and state contracts, without MRI processing."""

import hashlib
import json

import nibabel as nib
import numpy as np


def selected(window):
    return [name for control, name in window.nav_buttons if control.property("selected")]


def wait_scene(qtbot, viewer):
    qtbot.waitUntil(lambda: not viewer.tasks, timeout=15000)
    assert not viewer.status.text().startswith(("Operation failed:", "Cannot complete action:"))


def test_start_page_is_direct_and_setup_is_optional(window):
    window.focus_start()
    assert window.workspace.currentWidget() is window.start_page
    assert not selected(window)
    assert window.dependency_dialog is None
    assert window.workspace_title.text() == "Start"
    assert not hasattr(window, "assistant")


def test_image_route_is_not_tractography_and_has_honest_empty_state(window):
    window.focus_viewer()
    viewer = window.scientific_viewer
    assert selected(window) == ["Viewer"]
    assert viewer.workspace_mode == "image"
    assert window.workspace.currentWidget() is viewer
    assert viewer.content_stack.currentWidget() is viewer.empty_state
    assert viewer.empty_state.title.text() == "No image open"
    assert not viewer.footer.isVisible()
    assert not viewer.tabs.isTabVisible(1)
    assert all(view.canvas.message == "No image open" for view in viewer.views.values())
    window.focus_scientific_viewer()
    assert selected(window) == ["Tractography"]
    assert window.scientific_viewer is viewer
    assert viewer.tabs.isTabVisible(1)


def test_loaded_scene_survives_mode_switch_and_saved_workspace(
    window, qtbot, dataset_folder, tmp_path
):
    image = dataset_folder / "data.nii"
    original_sha = hashlib.sha256(image.read_bytes()).hexdigest()
    window.focus_viewer()
    v = window.scientific_viewer
    v.load_image(image)
    wait_scene(qtbot, v)
    reference = v.reference
    v.navigate((2, 3, 4))
    v.volume.setValue(2)
    wait_scene(qtbot, v)
    # A deterministic geometry fixture tests IO/identity only, not anatomy.
    points = nib.affines.apply_affine(
        reference.affine, np.array([[2, 3, 4], [3, 3, 4], [4, 3, 4]], float)
    )
    tract = tmp_path / "fixture.tck"
    nib.streamlines.save(nib.streamlines.Tractogram([points], affine_to_rasmm=np.eye(4)), tract)
    window.focus_scientific_viewer()
    assert v.reference is reference and v.volume.value() == 2
    assert v.position == (2, 3, 4)
    v.load_tracts(tract)
    wait_scene(qtbot, v)
    mask = np.zeros(reference.shape[:3], np.uint8)
    mask[2:5, 2:5, 3:6] = 1
    v.add_roi(mask, "Test region")
    groups, rois = v.groups[0], v.rois[0]
    window.focus_viewer()
    assert selected(window) == ["Viewer"]
    assert v.reference is reference and v.groups[0] is groups and v.rois[0] is rois
    assert v.volume.value() == 2
    v.layout_choice.setCurrentText("Axial")
    assert v.views["axial"].isVisible() and not v.views["coronal"].isVisible()
    v.set_inspector_visible(False)
    saved = tmp_path / "scene.json"
    v.save_workspace(saved)
    record = json.loads(saved.read_text())
    assert record["ui"]["mode"] == "image" and record["ui"]["layout"] == "Axial"
    assert not record["ui"]["inspector_visible"]
    assert record["ui"]["mode_layouts"]["image"] == "Axial"
    assert record["ui"]["mode_layouts"]["tractography"] == "Four views"
    v.open_workspace(saved)
    wait_scene(qtbot, v)
    assert selected(window) == ["Viewer"]
    assert v.layout_choice.currentText() == "Axial" and v.inspector.isHidden()
    window.focus_scientific_viewer()
    assert v.layout_choice.currentText() == "Four views"
    window.focus_viewer()
    assert v.layout_choice.currentText() == "Axial"
    assert len(v.groups) == len(v.rois) == 1
    assert v.volume.value() == 2
    assert hashlib.sha256(image.read_bytes()).hexdigest() == original_sha


def test_inspector_toggle_and_menu_actions_are_contextual(window):
    window.focus_viewer()
    window.inspector_action.trigger()
    assert window.scientific_viewer.inspector.isHidden()
    window.inspector_action.trigger()
    assert not window.scientific_viewer.inspector.isHidden()
    names = [action.text() for action in window.actions]
    assert "Open Image…" in names and "Save Viewer Workspace…" in names
    assert "Preferences…" in names and "Undo" in names and "Redo" in names
    window.focus_load()
    assert not window.open_tract_action.isVisible()
    assert not window.inspector_action.isVisible()


def test_system_theme_preference_is_not_lost_in_preferences(window):
    window.theme.apply("system")
    window.focus_preferences()
    assert window.preferences.scheme.currentData() == "system"
    assert window.theme.preference == "system"
    window.preferences.scheme.setCurrentIndex(window.preferences.scheme.findData("light"))
    assert window.theme.name == "light" and window.settings.get("theme") == "light"


def test_compact_camera_menu_uses_shared_scientific_scene(window, qtbot, dataset_folder):
    window.focus_scientific_viewer()
    viewer = window.scientific_viewer
    viewer.load_image(dataset_folder / "data.nii")
    wait_scene(qtbot, viewer)
    viewer.layout_choice.setCurrentText("3D scene")
    assert viewer.scene_host.isVisible()
    assert all(view.isHidden() for view in viewer.views.values())
    assert viewer.camera_button.isVisible()
    right = next(a for a in viewer.camera_menu.actions() if a.text() == "Right")
    right.trigger()
    assert viewer.camera.currentText() == "Right"
    camera = viewer.scene.renderer.GetActiveCamera()
    assert camera.GetPosition()[0] > camera.GetFocalPoint()[0]
    projection = viewer.parallel.isChecked()
    viewer.camera_menu.actions()[-1].trigger()
    assert viewer.parallel.isChecked() is not projection
    viewer.layout_choice.setCurrentText("Axial")
    assert viewer.camera_button.isHidden()


def test_edit_menu_tracks_roi_undo_and_never_edits_from_another_workspace(
    window, qtbot, dataset_folder
):
    assert not window.undo_action.isEnabled()
    window.focus_viewer()
    v = window.scientific_viewer
    v.load_image(dataset_folder / "data.nii")
    wait_scene(qtbot, v)
    mask = np.zeros(v.reference.shape[:3], np.uint8)
    mask[2:5, 2:5, 3:6] = 1
    v.add_roi(mask, "Editable derivative")
    v.tabs.setCurrentIndex(2)
    v.morph_roi("Dilate")
    assert window.undo_action.isEnabled() and not window.redo_action.isEnabled()
    window.undo_action.trigger()
    np.testing.assert_array_equal(v.rois[0]["data"], mask)
    assert not window.undo_action.isEnabled() and window.redo_action.isEnabled()
    window.focus_load()
    assert not window.redo_action.isEnabled()
    window.focus_viewer()
    assert window.redo_action.isEnabled()
    window.redo_action.trigger()
    assert np.count_nonzero(v.rois[0]["data"]) > np.count_nonzero(mask)
