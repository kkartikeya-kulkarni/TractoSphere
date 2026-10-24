"""Temporary pre-change TractoSphere GUI profiler. No scientific processing."""

import json
import os
import platform
import resource
import sys
import tempfile
import time
from pathlib import Path

origin = time.perf_counter()
from PySide6.QtCore import QEventLoop, QSettings, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

qt_import = time.perf_counter()
from dmri_studio.gui.theme import Theme  # noqa: E402

theme_import = time.perf_counter()
import dmri_studio.gui.main_window as main_module  # noqa: E402

main_import = time.perf_counter()
from dmri_studio.gui.batch import BatchPage  # noqa: E402
from dmri_studio.gui.settings import Settings  # noqa: E402

constructors = {}


def wrap_constructor(cls):
    original = cls.__init__

    def measured(self, *args, **kwargs):
        begin = time.perf_counter()
        original(self, *args, **kwargs)
        constructors[cls.__name__] = round(
            constructors.get(cls.__name__, 0.0) + (time.perf_counter() - begin) * 1000, 3
        )

    cls.__init__ = measured


for cls in (
    main_module.LoadDataPage,
    main_module.DataValidationPage,
    main_module.ProjectPage,
    main_module.WorkflowPage,
    main_module.QCPage,
    main_module.ScientificPlanningPage,
    BatchPage,
    main_module.ResultsPage,
    main_module.PreferencesPage,
    main_module.StartPage,
):
    wrap_constructor(cls)


def ms(operation):
    start = time.perf_counter()
    operation()
    app.processEvents()
    return round((time.perf_counter() - start) * 1000, 3)


def row(i):
    return {
        "key": f"subject-{i}/session-01/diffusion/metrics",
        "acquisition": {
            "subject_id": f"subject-{i}",
            "session_id": "01",
            "acquisition_id": "diffusion",
        },
        "node": {"id": "metrics"},
        "status": "PENDING",
        "qc_status": "NOT_ASSESSED",
        "issues": [],
    }


with tempfile.TemporaryDirectory() as tmp:
    app_start = time.perf_counter()
    app = QApplication([])
    app_ready = time.perf_counter()
    theme = Theme(app)
    theme_ready = time.perf_counter()
    settings = Settings(QSettings(str(Path(tmp) / "prefs.ini"), QSettings.IniFormat))
    window_start = time.perf_counter()
    window = main_module.MainWindow(theme, settings)
    window_ready = time.perf_counter()
    window.show()
    app.processEvents()
    shown = time.perf_counter()
    loop = QEventLoop()
    QTimer.singleShot(0, loop.quit)
    loop.exec()
    interactive = time.perf_counter()

    loaded_ms = None
    if os.environ.get("BENCH_LOADED"):
        import nibabel as nib
        import numpy as np

        folder = Path(tmp) / "subject"
        folder.mkdir()
        data = np.arange(8 * 10 * 12 * 4, dtype=np.float32).reshape(8, 10, 12, 4) + 1
        image = nib.Nifti1Image(data, np.diag([1.2, 1.5, 2.0, 1]))
        image.header.set_xyzt_units("mm")
        nib.save(image, folder / "data.nii")
        (folder / "bvals").write_text("0 1000 1000 2000")
        (folder / "bvecs").write_text("0 1 0 0\n0 0 1 0\n0 0 0 1")
        window.focus_load()
        window.page.fill_folder(folder)
        began = time.perf_counter()
        window.start_load()
        deadline = time.perf_counter() + 20
        while time.perf_counter() < deadline and (
            window.load_thread is not None or window.page.preview.thread is not None
        ):
            app.processEvents()
            time.sleep(0.005)
        assert window.dataset is not None, window.page.banner_detail.text()
        loaded_ms = round((time.perf_counter() - began) * 1000, 3)

    navigation = {}
    targets = {
        "Start": window.focus_start,
        "Project": window.focus_load,
        "Acquisitions": window.focus_projects,
        "Reconstruction": window.focus_models,
        "Tractography plan": window.focus_tracking_plan,
        "Registration": window.focus_registration,
        "Connectivity": window.focus_connectivity,
        "Batch": window.focus_batch,
        "QC": window.focus_qc,
        "Results": window.focus_results,
        "Settings": window.focus_preferences,
        "Dependencies": window.focus_dependencies,
    }
    for name, action in targets.items():
        navigation[name] = {"first_ms": ms(action)}
    for name, action in targets.items():
        navigation[name]["return_ms"] = ms(action)

    batch = window.batch
    table = {}
    for count in (10, 100, 1000):
        batch.rows = [row(i) for i in range(count)]
        table[str(count)] = {"initial_ms": ms(batch.render)}
        batch.table.selectRow(count // 2)
        table[str(count)]["selection_ms"] = ms(
            lambda selected=count // 2: batch.table.selectRow(selected)
        )
        table[str(count)]["rerender_ms"] = ms(batch.render)
        batch.search.setText("subject-1")
        table[str(count)]["filter_ms"] = ms(batch.filter_rows)
        batch.search.clear()
    journal_dir = Path(tmp) / "batch"
    journal_dir.mkdir()
    (journal_dir / "journal.json").write_text(
        json.dumps({"state": "READY", "nodes": {str(i): row(i) for i in range(1000)}})
    )
    batch.folder = journal_dir
    table["1000"]["journal_refresh_ms"] = [ms(batch.refresh) for _ in range(3)]

    timer_ticks = [0]
    batch.timer.timeout.connect(lambda: timer_ticks.__setitem__(0, timer_ticks[0] + 1))
    before_cpu = time.process_time()
    idle = QEventLoop()
    QTimer.singleShot(1800, idle.quit)
    idle.exec()
    idle_cpu_ms = round((time.process_time() - before_cpu) * 1000, 3)

    window.close()
    app.processEvents()
    result = {
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "python": sys.version.split()[0],
        "qt_platform": os.environ.get("QT_QPA_PLATFORM", "default"),
        "phase_ms": {
            "qt_import": round((qt_import - origin) * 1000, 3),
            "theme_import": round((theme_import - qt_import) * 1000, 3),
            "main_import": round((main_import - theme_import) * 1000, 3),
            "qapplication": round((app_ready - app_start) * 1000, 3),
            "theme": round((theme_ready - app_ready) * 1000, 3),
            "main_window": round((window_ready - window_start) * 1000, 3),
            "show_events": round((shown - window_ready) * 1000, 3),
            "first_event": round((interactive - shown) * 1000, 3),
            "script_to_interactive": round((interactive - origin) * 1000, 3),
        },
        "constructors_ms": constructors,
        "navigation_ms": navigation,
        "batch_table_ms": table,
        "idle_with_journal_cpu_ms_per_1800_ms": idle_cpu_ms,
        "batch_timer_ticks_per_1800_ms": timer_ticks[0],
        "max_rss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "vtkmodules_loaded": any(name.startswith("vtkmodules") for name in sys.modules),
        "loaded_ms": loaded_ms,
    }
    print(json.dumps(result, sort_keys=True))
