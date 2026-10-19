"""Workstation redesign contracts: navigation, startup and evidence separation."""

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy

from dmri_studio.gui.welcome import BrainScene, WelcomeWindow


def test_welcome_static_and_enter(window, qtbot):
    """Reduced motion renders a static illustration and Enter opens the same studio."""
    window.settings.set("reduced_motion", True)
    welcome = WelcomeWindow(window.theme, window.settings)
    qtbot.addWidget(welcome)
    welcome.show()
    assert isinstance(welcome.scene, BrainScene)
    assert not welcome.scene.timer.isActive()
    assert not welcome.grab().isNull()
    emitted = QSignalSpy(welcome.entered)
    welcome.enter_button.click()
    assert emitted.count() == 1
    assert not welcome.isVisible()
    assert not welcome.scene.timer.isActive()


def test_welcome_animation_is_bounded(window, qtbot):
    """Illustration stops on hide and after its bounded startup sequence."""
    scene = BrainScene(window.theme, True)
    qtbot.addWidget(scene)
    scene.show()
    assert scene.timer.isActive()
    scene.started -= 19
    scene.advance()
    assert not scene.timer.isActive()
    scene.set_motion(True)
    scene.hide()
    assert not scene.timer.isActive()
    scene.set_motion(False)
    assert scene.elapsed == 9


def test_dedicated_viewer_reuses_reader_and_restores_imports(window, qtbot, dataset_folder):
    """Switching workspaces never duplicates data or changes voxel/volume state."""
    window.page.fill_folder(dataset_folder)
    window.start_load()
    qtbot.waitUntil(lambda: window.load_thread is None and window.dataset is not None)
    preview = window.page.preview
    qtbot.waitUntil(lambda: not preview.pending and preview.thread is None)
    source = window.dataset
    window.focus_viewer()
    viewer = window.scientific_viewer
    qtbot.waitUntil(lambda: not viewer.tasks)
    assert window.workspace.currentWidget() is viewer
    assert viewer.workspace_mode == "image"
    assert viewer.reference.path == source.paths.dwi
    reference = viewer.reference
    viewer.expand("coronal")
    assert viewer.expanded == "coronal"
    viewer.expand("coronal")
    position = viewer.position
    window.focus_load()
    assert preview.parentWidget() is window.page
    assert preview.dataset is source
    window.focus_viewer()
    assert viewer.reference is reference and viewer.position == position
    window.focus_preferences()
    assert window.workspace.currentWidget() is window.preferences_scroll
    assert window.dataset is source


def test_workflow_tabs_do_not_approve_science(window, qtbot, dataset_folder):
    """All existing editors remain reachable; tab selection carries no approval."""
    window.page.fill_folder(dataset_folder)
    window.start_load()
    qtbot.waitUntil(lambda: window.load_thread is None and window.dataset is not None)
    window.focus_workflow()
    for index in range(4):
        window.workflow.steps.setCurrentIndex(index)
        assert not window.workflow.history.confirmed
        assert window.workflow.execution.plan is None
        assert window.workflow.execution.thread is None
    assert not window.workflow.execution.run.isEnabled()
    disabled = {name for nav, name in window.nav_buttons if not nav.isEnabled()}
    assert not ({"Reconstruction", "Connectivity"} & disabled)
    window.focus_models()
    assert window.workspace.currentWidget() is window.scientific
    assert not window.scientific.validate_button.isEnabled()
    assert window.scientific.plan is None
    assert all(
        a.implementation
        == (
            "implemented"
            if a.id
            in {
                "dwi2tensor",
                "tensor2metric",
                "dwi2response.dhollander",
                "dwi2response.tournier",
                "dwi2response.tax",
                "dwi2fod.csd",
                "dwi2fod.msmt_csd",
                "mtnormalise",
            }
            else "planned"
        )
        for a in window.scientific.visible_algorithms
    )
    assert window.scientific.dti.plan is None
    assert not window.scientific.dti.approval.isChecked()
    assert not window.scientific.dti.run_button.isEnabled()
    assert window.scientific.csd.plan is None
    assert not window.scientific.csd.approval.isChecked()
    assert not window.scientific.csd.run_button.isEnabled()


def test_preferences_and_sharing_remain_explicit(window):
    """Display preferences do not opt into AI or change scientific defaults."""
    window.focus_preferences()
    window.preferences.scheme.setCurrentIndex(window.preferences.scheme.findData("system"))
    assert window.theme.preference == "system"
    assert window.settings.get("theme") == "system"
    assert window.preferences.reduce.isChecked()
    assert not window.preferences.reduce.isEnabled()  # No decorative motion exists.
    assert window.workflow.execution.clean_scale.value() == 2
    assert not hasattr(window, "assistant")


def test_keyboard_viewer_controls_have_accessible_names(window):
    """Compact icon controls retain a textual purpose for keyboard/accessibility."""
    for orientation, view in window.page.preview.views.items():
        assert orientation in view.reset.accessibleName()
        assert orientation in view.expand.accessibleName()
        assert view.reset.focusPolicy() != Qt.FocusPolicy.NoFocus


def test_execution_parameters_follow_selected_operation(window, qtbot, dataset_folder):
    """Reduce visual clutter without changing parameter values or backend rules."""
    window.page.fill_folder(dataset_folder)
    window.start_load()
    qtbot.waitUntil(lambda: window.load_thread is None and window.dataset is not None)
    window.focus_workflow()
    page = window.workflow
    page.steps.setCurrentIndex(3)
    assert page.execution.native_parameters["extent"].isHidden()
    page.selections["denoise"].setChecked(True)
    assert not page.execution.native_parameters["extent"].isHidden()
    assert page.execution.native_parameters["nshifts"].isHidden()
    page.selections["gibbs"].setChecked(True)
    assert not page.execution.native_parameters["nshifts"].isHidden()
    assert page.execution.native_parameters["extent"].value() == 5
    assert page.execution.native_parameters["nshifts"].value() == 20
    assert not page.history.confirmed
    assert not page.execution.run.isEnabled()


def test_narrow_viewer_keeps_one_usable_linked_plane(window, qtbot, dataset_folder):
    """A single-plane layout keeps the scientific image usable at laptop size."""
    window.page.fill_folder(dataset_folder)
    window.start_load()
    qtbot.waitUntil(lambda: window.load_thread is None and window.dataset is not None)
    window.focus_viewer()
    viewer = window.scientific_viewer
    qtbot.waitUntil(lambda: not viewer.tasks)
    window.resize(1024, 700)
    viewer.layout_choice.setCurrentText("Axial")
    qtbot.wait(80)
    assert window.width() <= window.screen().availableGeometry().width()
    visible = [view for view in viewer.views.values() if view.isVisible()]
    assert len(visible) == 1 and visible[0] is viewer.views["axial"]
    assert visible[0].canvas.height() >= 180
    position = viewer.position
    viewer.layout_choice.setCurrentText("Sagittal")
    assert viewer.views["sagittal"].isVisible()
    assert viewer.position == position
    assert window.dataset_label.text() == window.dataset_label.toolTip()


def test_viewer_controls_and_logs_do_not_overlap_slices(window, qtbot, dataset_folder):
    """Visible controls remain outside the MRI canvas with logs open."""
    window.page.fill_folder(dataset_folder)
    window.start_load()
    qtbot.waitUntil(lambda: window.load_thread is None and window.dataset is not None)
    window.focus_viewer()
    viewer = window.scientific_viewer
    qtbot.waitUntil(lambda: not viewer.tasks)
    window.resize(1024, 700)
    viewer.layout_choice.setCurrentText("Axial")
    viewer.set_inspector_visible(False)
    window.logs.toggle()
    qtbot.wait(100)
    view = viewer.views["axial"]
    assert view.canvas.height() >= 180
    assert view.canvas.y() + view.canvas.height() <= view.slice_label.y()
    assert view.slice_label.geometry().bottom() < view.slider.geometry().top()
    assert not viewer.scene_host.isVisible()
    assert window.logs.isVisible()
