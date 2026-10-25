"""First-run continuation and shared setup UI use real Qt signals and workers."""

import platform
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QPushButton

from dmri_studio.core.dependencies import inspect_dependencies
from dmri_studio.gui import dependency_setup


def absent():
    return inspect_dependencies(environ={"PATH": ""}, search_common=False)


def test_official_installation_links_are_interactive_rich_text(window):
    from dmri_studio.core.dependencies import INSTALL_LINKS

    links = [
        w
        for w in window.preferences.dependencies.findChildren(QLabel)
        if "installation instructions</a>" in w.text()
    ]
    assert len(links) == len(INSTALL_LINKS)
    for link, url in zip(links, INSTALL_LINKS.values(), strict=True):
        assert link.textFormat() == Qt.TextFormat.RichText
        assert link.textInteractionFlags() & Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        assert link.openExternalLinks() and f'href="{url}"' in link.text()


def settled(window, qtbot):
    qtbot.waitUntil(lambda: window.dependencies.thread is None, timeout=10000)


def test_first_run_viewer_only_during_scan_and_repeat_suppressed(window, qtbot, monkeypatch):
    def check(**_):
        time.sleep(0.1)
        return absent()

    monkeypatch.setattr(dependency_setup, "inspect_dependencies", check)
    window.open_dependency_setup(first_run=True)
    dialog = window.dependency_dialog
    assert dialog.isVisible() and window.dependencies.thread is not None
    button = next(
        b for b in dialog.findChildren(QPushButton) if b.text() == "Continue with viewer only"
    )
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert not dialog.isVisible() and window.settings.get("dependencies/acknowledged", False)
    window.focus_scientific_viewer()
    assert window.scientific_viewer is not None
    settled(window, qtbot)
    assert window.dependencies.report["features"]["viewer"]["state"] == "available"
    window.open_dependency_setup(first_run=True)
    assert not dialog.isVisible()


def test_shared_report_and_explicit_default_paths(window, qtbot, monkeypatch, tmp_path):
    report = absent()
    paths = {f: str(tmp_path / f / "bin") for f in ("MRtrix3", "FSL", "ANTs")}
    for tool in report["tools"]:
        tool["path"] = str(Path(paths[tool["family"]]) / tool["name"])
        tool["state"] = "version_match"
        tool["version"] = "synthetic compatible version"
    monkeypatch.setattr(dependency_setup, "inspect_dependencies", lambda **_: report)
    widget = window.preferences.dependencies
    widget.scan()
    settled(window, qtbot)
    assert widget.tree.topLevelItemCount() == 3
    if platform.system() == "Windows":
        # Scientific command execution is deliberately unsupported here even
        # if a synthetic inventory row reports a matching version.
        assert not widget.save.isEnabled()
        return
    assert widget.save.isEnabled()
    window.workflow.execution.plan = object()
    widget.apply()
    panel = window.workflow.execution
    assert panel.tool_directory.text() == paths["MRtrix3"]
    assert window.batch.mrtrix_directory.text() == paths["MRtrix3"]
    assert window.batch.tool_directory == Path(paths["MRtrix3"])
    assert panel.correction_inputs["fsl_directory"].text() == paths["FSL"]
    assert panel.anatomy_inputs.fields["fsl_directory"].text() == paths["FSL"]
    assert panel.bias_inputs["ants_directory"].text() == paths["ANTs"]
    assert panel.plan is None
    assert window.dependencies.saved_directories() == paths
    widget.fields["ANTs"].setText("changed after check")
    assert not widget.save.isEnabled()
    widget.apply()
    assert "Recheck" in widget.status.text()
    assert window.dependencies.saved_directories() == paths


def test_busy_processing_not_reconfigured(window, qtbot, monkeypatch):
    monkeypatch.setattr(dependency_setup, "inspect_dependencies", lambda **_: absent())
    widget = window.preferences.dependencies
    widget.scan()
    settled(window, qtbot)
    window.workflow.execution.thread = object()
    try:
        widget.apply()
        assert "Finish the active" in widget.status.text()
        assert window.dependencies.saved_directories() == {"MRtrix3": "", "FSL": "", "ANTs": ""}
    finally:
        window.workflow.execution.thread = None


def test_scan_is_responsive_and_close_waits_safely(window, qtbot, monkeypatch):
    def check(**options):
        until = time.monotonic() + 0.2
        while time.monotonic() < until and not options["cancelled"]():
            time.sleep(0.01)
        return inspect_dependencies(
            environ={"PATH": ""}, search_common=False, cancelled=options["cancelled"]
        )

    monkeypatch.setattr(dependency_setup, "inspect_dependencies", check)
    ticks = []
    timer = QTimer(window)
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    window.preferences.dependencies.scan()
    qtbot.wait(30)
    assert ticks and window.dependencies.thread is not None
    window.close()
    settled(window, qtbot)
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=10000)
    assert window.dependencies.report["cancelled"]


def test_real_app_entry_opens_studio_without_setup_modal(window, qtbot, monkeypatch, qapp):
    from dmri_studio.gui import app

    monkeypatch.setattr(dependency_setup, "inspect_dependencies", lambda **_: absent())
    monkeypatch.setattr(app, "QApplication", lambda _: qapp)
    monkeypatch.setattr(app, "Settings", lambda: window.settings)
    monkeypatch.setattr(app, "Theme", lambda *_: window.theme)
    monkeypatch.setattr(app, "MainWindow", lambda *_: window)
    monkeypatch.setattr(qapp, "exec", lambda: 0)
    window.settings.set("show_welcome", False)
    assert app.main() == 0
    assert window.dependency_dialog is None
    assert window.isVisible()
    assert window.dependencies.thread is None
    window.focus_dependencies()
    assert window.workspace.currentWidget() is window.dependency_host


def test_batch_reads_saved_mrtrix_location_without_asking_again(window, tmp_path):
    """A new batch workspace reuses the application-level installation path."""
    from dmri_studio.gui.batch import BatchPage

    location = str(tmp_path / "verified" / "mrtrix3" / "bin")
    window.settings.set("dependencies/MRtrix3", location)
    page = BatchPage(window.theme, settings=window.settings)
    try:
        assert page.mrtrix_directory.text() == location
    finally:
        page.timer.stop()
        page.deleteLater()
