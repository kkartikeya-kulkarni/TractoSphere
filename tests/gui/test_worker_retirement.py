"""I/O remains off-thread; wrapped QObject destruction is serialized on the GUI."""

from PySide6.QtCore import Qt, QThread

from dmri_studio.gui.workers import ViewerTaskWorker


def test_worker_is_retired_on_owner_after_off_thread_task(qapp, qtbot):
    """Exercise thread handoff and actual deferred destruction, not just flags."""
    thread = QThread()
    called_on, destroyed_on, results = [], [], []

    def task():
        """Capture the thread that executes the scientific I/O stand-in."""
        called_on.append(QThread.currentThread())
        return "completed"

    worker = ViewerTaskWorker(1, task)
    worker.result.connect(lambda request, result, error: results.append((request, result, error)))
    worker.destroyed.connect(lambda: destroyed_on.append(QThread.currentThread()))
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
    thread.finished.connect(worker.deleteLater)
    thread.start()
    qtbot.waitUntil(lambda: len(destroyed_on) == 1 and thread.isFinished(), timeout=10000)
    assert called_on == [thread]
    assert destroyed_on == [qapp.thread()]
    assert results == [(1, "completed", "")]
    thread.wait()
    thread.deleteLater()


def test_native_scene_finalizes_before_parent_destruction(window, qtbot, dataset_folder):
    """QVTK callbacks and pending display timers cannot outlive their Qt owner."""
    window.focus_scientific_viewer()
    viewer = window.scientific_viewer
    assert viewer.scene.parentWidget() is not None
    viewer.load_image(dataset_folder / "data.nii")
    qtbot.waitUntil(lambda: not viewer.tasks, timeout=10000)
    viewer.scene.draw()
    viewer.fod_timer.start(1000)
    window.close()
    qtbot.waitUntil(lambda: viewer.scene._finalized, timeout=10000)
    assert not viewer.fod_timer.isActive() and not viewer.scene._Timer.isActive()
    assert not viewer.scene.GetRenderWindow().HasObserver("EndEvent")
    assert not viewer.scene.GetRenderWindow().HasObserver("CursorChangedEvent")
    viewer.scene.Finalize()
    viewer.scene.draw()  # safe, idempotent terminal calls


def test_scientific_tasks_release_callback_cycles_before_qt_retirement(window, qtbot):
    """Repeated success/failure retains wrappers until real deferred deletion."""
    import gc

    from shiboken6 import isValid

    page = window.scientific
    for iteration in range(30):

        def operation(cancelled, progress, fail=iteration % 3 == 0):
            if fail:
                raise ValueError("Controlled failure before any scientific process")
            return ([], [])

        page.start("restore", operation)
        worker, thread = page.worker, page.thread
        original_task = worker.task
        qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
        assert worker.task is not original_task
        del original_task
        gc.collect()
        qtbot.waitUntil(lambda w=worker, t=thread: not isValid(w) and not isValid(t), timeout=10000)
        page.update_actions()
        assert not page._retired_workers
