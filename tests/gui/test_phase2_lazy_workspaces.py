"""Optional scientific workspaces appear on demand and retain Phase 1 defaults."""

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from dmri_studio.gui.main_window import MainWindow
from dmri_studio.gui.settings import Settings
from dmri_studio.gui.theme import Theme


def test_scientific_workspace_created_once_and_keeps_state(window):
    assert window._scientific is None
    assert window._batch is None
    assert window.can_change_dependencies()
    assert window._scientific is None
    assert window._batch is None

    window.focus_models()
    page = window.scientific
    assert window.workspace.currentWidget() is page
    page.tracking.output.setText("/tmp/unsubmitted-tracking-output")
    window.focus_registration()
    window.focus_tracking_plan()
    window.focus_models()
    assert window.scientific is page
    assert window.workspace.count() == len(
        set(window.workspace.widget(i) for i in range(window.workspace.count()))
    )
    assert page.tracking.output.text() == "/tmp/unsubmitted-tracking-output"


def test_saved_defaults_reach_pages_constructed_after_navigation(window, tmp_path):
    locations = {family: str(tmp_path / family / "bin") for family in ("MRtrix3", "FSL", "ANTs")}
    for family, path in locations.items():
        window.settings.set(f"dependencies/{family}", path)
    window.settings.store.sync()
    window.apply_dependency_locations(window.dependencies.saved_directories())
    assert window._scientific is None and window._batch is None

    window.focus_batch()
    batch = window.batch
    assert window._scientific is None
    assert batch.tool_directory == Path(locations["MRtrix3"])
    assert batch.mrtrix_directory.text() == locations["MRtrix3"]
    assert batch.fsl_directory == Path(locations["FSL"])
    assert batch.ants_directory == Path(locations["ANTs"])

    window.focus_registration()
    scientific = window.scientific
    assert scientific.tool_directory == Path(locations["MRtrix3"])
    assert scientific.fsl_directory == Path(locations["FSL"])
    window.focus_batch()
    assert window.batch is batch
    assert window.scientific is scientific


def test_configuration_changes_do_not_create_optional_pages(window, tmp_path):
    initial = str(tmp_path / "first" / "bin")
    replacement = str(tmp_path / "second" / "bin")
    window.apply_dependency_locations({"MRtrix3": initial})
    window.apply_dependency_locations({"MRtrix3": replacement})
    assert window._scientific is None and window._batch is None
    assert window.workflow.execution.tool_directory.text() == replacement
    assert window.scientific.tool_directory == Path(replacement)
    assert window.batch.tool_directory == Path(replacement)


def test_close_without_opening_optional_pages_does_not_construct_them(window):
    assert window._scientific is None and window._batch is None
    window.close()
    assert window._scientific is None and window._batch is None


def test_navigation_restyles_only_changed_buttons(window, monkeypatch):
    nav_widgets = {widget for widget, _ in window.nav_buttons}
    changed = []
    original = window.theme.set_icon

    def set_icon(widget, name, token="text.secondary"):
        if widget in nav_widgets:
            changed.append(widget.accessibleName())
        return original(widget, name, token)

    monkeypatch.setattr(window.theme, "set_icon", set_icon)
    window.focus_start()
    assert changed == ["Project"]
    changed.clear()
    window.focus_batch()
    assert changed == ["Batch Processing"]
    changed.clear()
    window.focus_preferences()
    assert changed == ["Batch Processing", "Settings"]
    assert [name for widget, name in window.nav_buttons if widget.property("selected")] == [
        "Settings"
    ]


def test_restart_restores_defaults_before_lazy_pages_exist(qapp, tmp_path):
    settings_file = tmp_path / "preferences.ini"
    saved = Settings(QSettings(str(settings_file), QSettings.IniFormat))
    paths = {family: str(tmp_path / family / "bin") for family in ("MRtrix3", "FSL", "ANTs")}
    for family, path in paths.items():
        saved.set(f"dependencies/{family}", path)
    saved.store.sync()
    assert saved.store.status() == QSettings.Status.NoError

    first = MainWindow(Theme(qapp), Settings(QSettings(str(settings_file), QSettings.IniFormat)))
    assert first._scientific is None and first._batch is None
    assert first.batch.mrtrix_directory.text() == paths["MRtrix3"]
    first.close()
    qapp.processEvents()

    reopened = MainWindow(Theme(qapp), Settings(QSettings(str(settings_file), QSettings.IniFormat)))
    assert reopened._scientific is None and reopened._batch is None
    assert reopened.workflow.execution.bias_inputs["ants_directory"].text() == paths["ANTs"]
    assert reopened.scientific.fsl_directory == Path(paths["FSL"])
    assert reopened.batch.fsl_directory == Path(paths["FSL"])
    reopened.close()


def test_repeated_navigation_does_not_create_more_widgets(window):
    window.focus_models()
    window.focus_batch()
    scientific, batch = window.scientific, window.batch
    count = len(QApplication.allWidgets())
    for _ in range(20):
        window.focus_registration()
        window.focus_tracking_plan()
        window.focus_batch()
    assert window.scientific is scientific and window.batch is batch
    assert len(QApplication.allWidgets()) == count
