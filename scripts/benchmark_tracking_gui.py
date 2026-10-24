"""Measure task-first Tractography and synthetic ROI interactions without VTK.

Run with ``PYTHONPATH=src`` and an explicit ``QT_QPA_PLATFORM``. This measures
GUI handling only; it neither reads research data nor executes tractography.
"""

import json
import os
import platform
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from dmri_studio.gui.main_window import MainWindow
from dmri_studio.gui.settings import Settings
from dmri_studio.gui.theme import Theme


def measure(app, callback):
    started = time.perf_counter()
    callback()
    app.processEvents()
    return round((time.perf_counter() - started) * 1000, 3)


def roi(index):
    name = f"synthetic-region-{index:04d}"
    return SimpleNamespace(id=name, kind="roi", payload={"path": f"/synthetic/{name}.nii.gz"})


with tempfile.TemporaryDirectory() as directory:
    app = QApplication([])
    theme = Theme(app)
    settings = Settings(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
    window = MainWindow(theme, settings)
    window.show()
    app.processEvents()
    first = measure(app, window.focus_tracking_plan)
    page = window.scientific
    panel = page.tracking
    guided = panel.guided
    measure(app, window.focus_start)
    repeated = measure(app, window.focus_tracking_plan)

    inventory = {}
    for count in (10, 100, 1000):
        page.artifacts = [roi(i) for i in range(count)]
        population = measure(app, panel.refresh)
        assert guided.roi_inventory.count() == count
        search = measure(app, lambda: guided.roi_search.setText("region-0009"))
        clear_search = measure(app, guided.roi_search.clear)
        guided.roi_inventory.setCurrentRow(0)
        assign = measure(app, lambda: guided.assign_roi("include"))
        guided.roi_inventory.setCurrentRow(1)
        measure(app, lambda: guided.assign_roi("ordered"))
        guided.roi_inventory.setCurrentRow(2)
        measure(app, lambda: guided.assign_roi("ordered"))
        guided.roi_groups["ordered"].setCurrentRow(1)
        reorder = measure(app, lambda: guided.move_waypoint(-1))
        inventory[str(count)] = {
            "populate_ms": population,
            "search_ms": search,
            "clear_search_ms": clear_search,
            "assign_ms": assign,
            "reorder_ms": reorder,
        }

    window.close()
    app.processEvents()
    print(
        json.dumps(
            {
                "platform": platform.platform(),
                "qt_platform": os.environ.get("QT_QPA_PLATFORM", "default"),
                "first_tracking_ms": first,
                "return_navigation_ms": repeated,
                "roi_inventory": inventory,
            },
            sort_keys=True,
        )
    )
