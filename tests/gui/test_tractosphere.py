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
    assert window.workspace.currentWidget() is window.expanded_host
    preview.set_expanded("coronal")
    preview.set_expanded(None)
    assert window.workspace.currentWidget() is window.expanded_host
    assert preview.dataset is source
    position = preview.position
    window.focus_load()
    assert preview.parentWidget() is window.page
    assert preview.position == position
    window.focus_viewer()
    assert preview.dataset is source
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
    assert {"Reconstruction", "Connectivity"} <= disabled


def test_preferences_and_sharing_remain_explicit(window):
    """Display preferences do not opt into AI or change scientific defaults."""
    window.focus_preferences()
    window.preferences.reduce.setChecked(True)
    window.preferences.welcome.setChecked(False)
    assert window.settings.get("reduced_motion") is True
    assert window.settings.get("show_welcome") is False
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
    """Small docked layouts must not squeeze three canvases into unreadable strips."""
    window.page.fill_folder(dataset_folder)
    window.start_load()
    qtbot.waitUntil(lambda: window.load_thread is None and window.dataset is not None)
    preview = window.page.preview
    qtbot.waitUntil(lambda: not preview.pending and preview.thread is None)
    window.focus_viewer()
    preview.setFixedWidth(580)
    window.resize(1024, 700)
    qtbot.wait(80)
    assert window.width() == 1024
    assert preview.compact_tabs.isVisible()
    visible = [view for view in preview.views.values() if view.isVisible()]
    assert len(visible) == 1
    assert visible[0].canvas.height() >= 180
    position = preview.position
    preview.compact_tabs.setCurrentIndex(2)
    assert preview.views["sagittal"].isVisible()
    assert preview.position == position
    assert window.dataset_label.text() == window.dataset_label.toolTip()


def test_viewer_controls_and_logs_do_not_overlap_slices(window, qtbot, dataset_folder):
    """Short workspaces scroll rather than placing slice controls over the canvas."""
    window.page.fill_folder(dataset_folder)
    window.start_load()
    qtbot.waitUntil(lambda: window.load_thread is None and window.dataset is not None)
    preview = window.page.preview
    qtbot.waitUntil(lambda: not preview.pending and preview.thread is None)
    window.focus_viewer()
    window.resize(1024, 700)
    preview.tools_toggle.setChecked(True)
    window.logs.toggle()
    qtbot.wait(100)
    for view in preview.views.values():
        if view.isVisible():
            assert view.canvas.height() >= 200
            assert view.canvas.y() + view.canvas.height() <= view.slice_label.y()
            assert view.slice_label.geometry().bottom() < view.slider.geometry().top()
    assert window.expanded_host.verticalScrollBar().maximum() > 0
