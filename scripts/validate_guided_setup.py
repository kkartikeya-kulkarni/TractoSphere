"""Native guided setup audit: real scanner, fresh app preferences, no installer/MRI runs.

Run with --output NEW_DIRECTORY. Uses local installed tools read-only; --ants-bin
may name an isolated existing research installation. Public screenshots are taken
before real installation paths are selected. Official browser links are not opened.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from dmri_studio.gui.main_window import MainWindow
from dmri_studio.gui.settings import Settings
from dmri_studio.gui.theme import Theme


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ants-bin", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    app = QApplication([])
    theme = Theme(app, "dark")
    settings = Settings(QSettings(str(output / "preferences.ini"), QSettings.IniFormat))
    window = MainWindow(theme, settings)
    window.resize(1400, 900)
    window.show()
    window.focus_preferences()
    manager = window.preferences.dependencies
    manager.common.setChecked(False)
    for field in manager.fields.values():
        field.setText("")
    manager.scan()
    start = time.monotonic()
    state = dict(stage=0, records=[], error=None, hashes={})
    timer = QTimer(window)
    timer.setInterval(50)

    def tick():
        try:
            assert time.monotonic() - start < 80, "Native guided setup timeout"
            if window.dependencies.thread is not None:
                return
            if state["stage"] == 0:
                assert all(t["state"] == "missing" for t in window.dependencies.report["tools"])
                assert window.grab().save(str(output / "software-dependencies-dark.png"))
                state["stage"] = 1
            elif state["stage"] in (1, 2, 3):
                family = ("MRtrix3", "ANTs", "FSL")[state["stage"] - 1]
                manager.open_guide(family)
                dialog = manager.guides[family]
                # Actual scrollable dialog and action availability, no browser download.
                assert dialog.isVisible() and not dialog.download.isEnabled()
                assert dialog.grab().save(str(output / (family.lower() + "-guide-dark.png")))
                theme.apply("light")
                app.processEvents()
                assert dialog.grab().save(str(output / (family.lower() + "-guide-light.png")))
                theme.apply("dark")
                dialog.accept()
                state["stage"] += 1
            elif state["stage"] == 4:
                if args.ants_bin is None:
                    state["stage"] = 6
                    return
                binary = args.ants_bin / "N4BiasFieldCorrection"
                state["hashes"][str(binary)] = hashlib.sha256(binary.read_bytes()).hexdigest()
                manager.open_guide("ANTs")
                dialog = manager.guides["ANTs"]
                dialog.destination.setText(str(args.ants_bin))
                QTest.mouseClick(dialog.test, Qt.MouseButton.LeftButton)
                state["stage"] = 5
            elif state["stage"] == 5:
                dialog = manager.guides["ANTs"]
                assert "verified and configured" in dialog.status.text(), dialog.status.text()
                assert settings.get("dependencies/ANTs") == str(args.ants_bin)
                state["records"] = window.dependencies.report
                assert dialog.grab().save(
                    str(output / "actual-existing-ants-verification-private.png")
                )
                dialog.accept()
                state["stage"] = 6
            elif state["stage"] == 6:
                assert all(
                    hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
                    for p, h in state["hashes"].items()
                )
                (output / "validation.json").write_text(
                    json.dumps(
                        dict(
                            passed=True,
                            elapsed_seconds=time.monotonic() - start,
                            versions=state["records"],
                            source_hashes_unchanged=True,
                            images_are_actual_GUI=True,
                            automatic_installer_executed=False,
                            new_settings_only=True,
                            OS_scope="existing macOS host; independent Mac remains pending",
                        ),
                        indent=2,
                    )
                )
                timer.stop()
                window.close()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            state["error"] = str(exc)
            timer.stop()
            window.close()
            app.quit()

    timer.timeout.connect(tick)
    timer.start()
    app.exec()
    assert state["error"] is None, state["error"]
    assert state["stage"] == 6
    print("Native guided setup and read-only installed ANTs configuration passed:", output)


if __name__ == "__main__":
    main()
