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
