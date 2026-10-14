"""Native installed-package first-run test with fresh preferences, no MRI processing.

Run --profile missing with PATH limited to the new venv and system /usr/bin:/bin,
FSLDIR/ANTSPATH unset. This is a clean application profile on an existing macOS
host, not a claim that the OS was reinstalled. --profile installed inventories the
host read-only. The real entry point, welcome and first-run dialog are exercised.
"""

import argparse
import hashlib
import json
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from dmri_studio.gui import app as entry
from dmri_studio.gui.settings import Settings
from dmri_studio.gui.welcome import WelcomeWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("missing", "installed"), required=True)
    parser.add_argument("--theme", choices=("dark", "light"), default="dark")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    settings = Settings(QSettings(str(out / "preferences.ini"), QSettings.IniFormat))
    settings.set("reduced_motion", True)
    settings.set("dependencies/search_common", args.profile == "installed")
    settings.set("show_welcome", True)
    settings.set("theme", args.theme)
    entry.Settings = lambda: settings
    original = entry.MainWindow
    state = dict(stage=0, window=None, error=None, report=None)
    started = time.monotonic()
    ticks = []
    source = args.reference / "anat/sub-1006__T1w.nii.gz"
    tract = args.reference / "tractography/sub-1006__AF_L.tck"
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, tract)}

    def construct(theme, preferences):
        window = original(theme, preferences)
        state["window"] = window
        window.resize(1440, 960)
        timer = QTimer(window)
        timer.setInterval(25)
        state["timer"] = timer
        timer.timeout.connect(check)
        timer.start()
        return window

    def check():
        ticks.append(time.monotonic())
        window = state["window"]
        try:
            assert time.monotonic() - started < 90, "Native first-run timeout"
            if state["stage"] == 0:
                welcome = next(
                    (
                        w
                        for w in QApplication.topLevelWidgets()
                        if isinstance(w, WelcomeWindow) and w.isVisible()
                    ),
                    None,
                )
                if welcome:
                    QTest.mouseClick(welcome.enter_button, Qt.MouseButton.LeftButton)
                    state["stage"] = 1
            elif state["stage"] == 1:
                dialog = window.dependency_dialog
                if dialog is None or window.dependencies.thread is not None:
                    return
                assert dialog.isVisible()
                report = window.dependencies.report
                assert report and not report["cancelled"]
                if args.profile == "missing":
                    assert all(t["state"] == "missing" for t in report["tools"])
                state["report"] = report
                assert dialog.grab().save(str(out / "first-run-setup.png"))
                control = next(
                    b
                    for b in dialog.findChildren(QPushButton)
                    if b.text() == "Continue with viewer only"
                )
                QTest.mouseClick(control, Qt.MouseButton.LeftButton)
                assert settings.get("dependencies/acknowledged", False)
                window.focus_scientific_viewer()
                window.scientific_viewer.load_image(source)
                state["stage"] = 2
            elif state["stage"] == 2:
                viewer = window.scientific_viewer
                if viewer.tasks:
                    return
                viewer.load_tracts(tract)
                state["stage"] = 3
            elif state["stage"] == 3:
                viewer = window.scientific_viewer
                if viewer.tasks:
                    return
                assert len(viewer.groups) == 1 and viewer.groups[0].total == 18375
                assert not viewer.status.text().startswith("Operation failed"), viewer.status.text()
                viewer.density.setValue(1000)
                viewer.scene.camera("Left")
                viewer.status.setText(
                    "Viewer-only operation · real permitted MRI / tractography · no scientific tool required"
                )
                assert window.grab().save(str(out / "viewer-only.png"))
                # A new window reads the same saved user decision and does not
                # reopen the first-run dialog automatically.
                next_window = original(window.theme, settings)
                next_window.open_dependency_setup(first_run=True)
                assert next_window.dependency_dialog is None
                next_window.close()
                assert all(
                    hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in hashes.items()
                )
                (out / "validation.json").write_text(
                    json.dumps(
                        dict(
                            profile=args.profile,
                            theme=args.theme,
                            elapsed_seconds=time.monotonic() - started,
                            real_entry_point=True,
                            welcome_enter_clicked=True,
                            continue_viewer_only=True,
                            repeat_setup_suppressed=True,
                            original_hashes_unchanged=True,
                            tick_count=len(ticks),
                            dependency_inventory=state["report"],
                            environment_scope="fresh preferences + isolated Python environment on existing macOS; no fresh OS claim",
                        ),
                        indent=2,
                    )
                )
                state["stage"] = 4
                state["timer"].stop()
                window.close()
        except Exception as exc:
            state["error"] = str(exc)
            traceback.print_exc()
            state["timer"].stop()
            window.close()
            QApplication.quit()

    entry.MainWindow = construct
    result = entry.main()
    assert state["error"] is None, state["error"]
    assert state["stage"] == 4
    print("Native first-run setup and real viewer-only checks passed:", out)
    return result


if __name__ == "__main__":
    main()
