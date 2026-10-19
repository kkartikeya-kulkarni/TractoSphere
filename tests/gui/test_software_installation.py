"""Actual Qt controls + synthetic external-install folders; never run an installer."""

import hashlib
import os
import re
import sys
import time

import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtWidgets import QFileDialog, QScrollArea

from dmri_studio.core.dependencies import inspect_dependencies
from dmri_studio.core.software_setup import installation_guide
from dmri_studio.gui import dependency_setup, software_installation
from dmri_studio.gui.settings import Settings


def simulate_external_install(prefix, version="2.6.5"):
    """Fake only a version probe, not an MRI algorithm or real upstream installer."""
    binary = prefix / "bin/N4BiasFieldCorrection"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(
        f'#!{sys.executable}\nimport sys\nassert sys.argv[1:]==["--version"]\nprint("ANTs Version: {version}")\n'
    )
    binary.chmod(0o755)
    return binary


@pytest.fixture
def wizard(window, qtbot, monkeypatch):
    # These controls test the guided macOS workflow on every host. Native
    # Windows remains unsupported; this is a synthetic workflow, not an
    # installation claim. Windows cannot execute a POSIX shebang fixture.
    monkeypatch.setattr(
        software_installation,
        "installation_guide",
        lambda family: installation_guide(
            family, host=dict(system="Darwin", architecture="arm64", macos="26.3", distribution={})
        ),
    )
    if os.name == "nt":
        from dmri_studio.core import dependencies

        def fixture_query(path, argument, timeout):
            assert argument == "--version"
            version = re.search(r"ANTs Version: ([\d.]+)", path.read_text())[1]
            return f"ANTs Version: {version}", None

        monkeypatch.setattr(dependencies, "_query", fixture_query)
    window.preferences.dependencies.common.setChecked(False)
    monkeypatch.setattr(
        dependency_setup,
        "inspect_dependencies",
        lambda **opts: inspect_dependencies(
            directories=opts["directories"],
            environ={"PATH": ""},
            search_common=False,
            cancelled=opts["cancelled"],
            system="Darwin",
        ),
    )
    manager = window.preferences.dependencies
    manager.open_guide("ANTs")
    dialog = manager.guides["ANTs"]
    qtbot.addWidget(dialog)
    dialog.raise_()
    dialog.activateWindow()
    qtbot.waitExposed(dialog)
    yield dialog
    dialog.reject()
    qtbot.waitUntil(lambda: window.dependencies.thread is None, timeout=10000)


def finish(wizard, qtbot):
    qtbot.waitUntil(lambda: wizard.controller.thread is None, timeout=10000)


def test_guided_external_success_rescan_configures_only_selected_family(
    wizard, window, qtbot, monkeypatch, tmp_path
):
    calls = []
    monkeypatch.setattr(
        software_installation.QDesktopServices,
        "openUrl",
        lambda url: calls.append(url.toString()) or True,
    )
    # Download cannot occur until explicit checkbox consent, even by calling handler.
    wizard.open_download()
    assert not calls
    wizard.findChild(QScrollArea).ensureWidgetVisible(wizard.consent)
    qtbot.mouseClick(
        wizard.consent, Qt.MouseButton.LeftButton, pos=QPoint(8, wizard.consent.height() // 2)
    )
    assert wizard.consent.isChecked()
    qtbot.mouseClick(wizard.download, Qt.MouseButton.LeftButton)
    assert calls and calls[0].startswith("https://github.com/ANTsX/ANTs/releases/download/v2.6.5/")
    assert "not monitored" in wizard.status.text()
    prefix = tmp_path / "isolated ANTs"
    binary = simulate_external_install(prefix)
    before = binary.read_bytes()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_: str(prefix))
    qtbot.mouseClick(wizard.locate, Qt.MouseButton.LeftButton)
    window.workflow.execution.correction_inputs["fsl_directory"].setText(
        "unrelated manual FSL path"
    )
    qtbot.mouseClick(wizard.test, Qt.MouseButton.LeftButton)
    finish(wizard, qtbot)
    assert "verified and configured" in wizard.status.text()
    expected = str(prefix / "bin")
    assert window.settings.get("dependencies/ANTs") == expected
    assert wizard.manager.fields["ANTs"].text() == expected
    assert window.workflow.execution.bias_inputs["ants_directory"].text() == expected
    assert (
        window.workflow.execution.correction_inputs["fsl_directory"].text()
        == "unrelated manual FSL path"
    )
    fresh = Settings(QSettings(window.settings.store.fileName(), QSettings.IniFormat))
    assert fresh.get("dependencies/ANTs") == expected and binary.read_bytes() == before


def test_incorrect_directory_and_incompatible_install_leave_settings(wizard, qtbot, tmp_path):
    wizard.destination.setText(str(tmp_path / "missing"))
    qtbot.mouseClick(wizard.test, Qt.MouseButton.LeftButton)
    assert "Incorrect/missing directory" in wizard.status.text()
    prefix = tmp_path / "incompatible"
    simulate_external_install(prefix, "9.9.9")
    wizard.destination.setText(str(prefix))
    qtbot.mouseClick(wizard.test, Qt.MouseButton.LeftButton)
    finish(wizard, qtbot)
    assert "incompatible" in wizard.state.text().lower()
    assert wizard.controller.saved_directories()["ANTs"] == ""
    wizard.report_interrupted()
    assert "failed/interrupted by user" in wizard.status.text()
    assert wizard.controller.saved_directories()["ANTs"] == ""


def test_scan_only_and_no_configuration_consent(wizard, qtbot, tmp_path):
    prefix = tmp_path / "compatible"
    simulate_external_install(prefix)
    wizard.destination.setText(str(prefix))
    wizard.configure.setChecked(False)
    wizard.test_installation()
    finish(wizard, qtbot)
    assert "not selected" in wizard.status.text()
    assert wizard.controller.saved_directories()["ANTs"] == ""
    qtbot.mouseClick(wizard.scan, Qt.MouseButton.LeftButton)
    finish(wizard, qtbot)
    assert wizard.controller.saved_directories()["ANTs"] == ""


def test_offline_insufficient_space_and_permission_recovery(wizard, qtbot, monkeypatch):
    monkeypatch.setattr(software_installation.QDesktopServices, "openUrl", lambda _: False)
    wizard.open_browser(wizard.guide["guide_url"])
    assert "Could not open" in wizard.status.text() and "Offline" in wizard.status.text()
    monkeypatch.setattr(
        software_installation,
        "destination_check",
        lambda *_: dict(
            free_bytes=1,
            writable=False,
            sufficient=False,
            detail="Insufficient storage; permission denied",
            existing=False,
        ),
    )
    wizard.check_destination()
    wizard.consent.setChecked(True)
    assert not wizard.download.isEnabled() and "permission" in wizard.space.text()
    assert wizard.controller.saved_directories()["ANTs"] == ""


def test_cancelled_test_and_closed_dialog_never_configure(wizard, qtbot, tmp_path, monkeypatch):
    prefix = tmp_path / "candidate"
    simulate_external_install(prefix)

    def slow(**opts):
        until = time.monotonic() + 0.15
        while time.monotonic() < until and not opts["cancelled"]():
            time.sleep(0.005)
        return inspect_dependencies(
            directories=opts["directories"],
            environ={"PATH": ""},
            search_common=False,
            cancelled=opts["cancelled"],
        )

    monkeypatch.setattr(dependency_setup, "inspect_dependencies", slow)
    wizard.destination.setText(str(prefix))
    wizard.test_installation()
    wizard.cancel_check()
    finish(wizard, qtbot)
    assert (
        wizard.controller.report["cancelled"]
        and wizard.controller.saved_directories()["ANTs"] == ""
    )
    wizard.test_installation()
    wizard.reject()
    finish(wizard, qtbot)
    assert wizard.controller.saved_directories()["ANTs"] == ""


def test_archive_gui_hash_integrity_success_and_failure(wizard, qtbot, tmp_path, monkeypatch):
    path = tmp_path / "fixture.zip"
    path.write_bytes(b"clearly synthetic installer")
    wizard.guide.update(
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(), download_bytes=path.stat().st_size
    )
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(path), "zip"))
    qtbot.mouseClick(wizard.archive, Qt.MouseButton.LeftButton)
    finish(wizard, qtbot)
    assert "SHA-256 and size matched" in wizard.status.text()
    path.write_bytes(b"wrong bytes")
    qtbot.mouseClick(wizard.archive, Qt.MouseButton.LeftButton)
    finish(wizard, qtbot)
    assert "mismatch" in wizard.status.text()
    assert wizard.controller.saved_directories()["ANTs"] == ""


def test_unsupported_Windows_guidance_blocks_test(window, qtbot, monkeypatch):
    guide = installation_guide(
        "FSL", host=dict(system="Windows", architecture="AMD64", macos="", distribution={})
    )
    monkeypatch.setattr(software_installation, "installation_guide", lambda _: guide)
    window.preferences.dependencies.open_guide("FSL")
    dialog = window.preferences.dependencies.guides["FSL"]
    qtbot.addWidget(dialog)
    assert not dialog.test.isEnabled() and not dialog.download.isEnabled()
    assert "WSL" in dialog.guide["platform_note"] and "windows.html" in dialog.guide["guide_url"]
    dialog.test_installation()
    assert window.dependencies.thread is None
    dialog.reject()


def test_verified_settings_write_failure_preserves_configuration(
    wizard, qtbot, tmp_path, monkeypatch
):
    prefix = tmp_path / "valid"
    simulate_external_install(prefix)
    old = wizard.controller.saved_directories()
    monkeypatch.setattr(
        wizard.controller.settings.store, "status", lambda: QSettings.Status.AccessError
    )
    wizard.destination.setText(str(prefix))
    wizard.test_installation()
    finish(wizard, qtbot)
    assert "Could not save" in wizard.status.text()
    assert wizard.controller.saved_directories() == old


def test_running_preprocessing_prevents_configuration(wizard, window, qtbot, tmp_path):
    prefix = tmp_path / "valid"
    simulate_external_install(prefix)
    wizard.destination.setText(str(prefix))
    window.workflow.execution.thread = object()
    try:
        wizard.test_installation()
        finish(wizard, qtbot)
        assert "Finish the active" in wizard.status.text()
        assert wizard.controller.saved_directories()["ANTs"] == ""
    finally:
        window.workflow.execution.thread = None
