"""Thread adapters around the shared loader and single-plane read API.

Workers own no widgets. Cancellation uses threading.Event because queued slots
cannot run while a worker is blocked in disk I/O. Results carry request IDs.
"""

import logging
import traceback
from collections.abc import Callable
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from dmri_studio.core.dataset import DatasetPaths, DwiDataset
from dmri_studio.core.loading import DatasetLoader
from dmri_studio.core.validation import ValidationPolicy


class LogBridge(QObject):
    """Marshal Python logging to the GUI thread."""

    record = Signal(object)


class QtLogHandler(logging.Handler):
    """Forward actual core logs, including unexpected exception details."""

    def __init__(self, bridge: LogBridge) -> None:
        """Retain the signal bridge for the lifetime of this handler."""
        super().__init__()
        self.bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        """Format exceptions before crossing the thread boundary."""
        self.bridge.record.emit(
            (record.created, record.levelname, record.name, self.format(record))
        )


class GuiOwnedWorker(QObject):
    """Do I/O off-thread, but retire Python-wrapped QObjects on their owner thread.

    Native PySide6 teardown can otherwise invert the GIL and Qt connection-lock
    order: the GUI updates logs while a worker destructor calls disconnectNotify
    and waits for the GIL. Move back from the worker's own thread before quitting;
    the existing thread.finished → deleteLater connection then retires it on the
    GUI thread. No UI operation occurs on the I/O thread.
    """

    finished = Signal()

    def __init__(self) -> None:
        """Remember the creating GUI thread before moveToThread starts the job."""
        super().__init__()
        self._owner_thread = self.thread()

    def finish(self) -> None:
        """Hand ownership back while this method still runs on the worker thread."""
        self.moveToThread(self._owner_thread)
        self.finished.emit()


class LoadWorker(GuiOwnedWorker):
    """Execute one candidate load and publish its immutable report."""

    progress = Signal(int, object)
    result = Signal(int, object)
    error = Signal(int, str)

    def __init__(
        self,
        request: int,
        paths: DatasetPaths,
        policy: ValidationPolicy,
        cancel: Event,
        loader: DatasetLoader,
        expected_product: dict | None = None,
    ) -> None:
        """Capture this transaction's inputs without touching the UI."""
        super().__init__()
        self.request, self.paths, self.policy = request, paths, policy
        self.cancel, self.loader = cancel, loader
        self.expected_product = expected_product

    @Slot()
    def run(self) -> None:
        """Let the core make every scientific acceptance decision."""
        try:
            options = dict(
                policy=self.policy,
                progress=lambda p: self.progress.emit(self.request, p),
                cancelled=self.cancel.is_set,
            )
            if self.expected_product is None:
                result = self.loader.load(self.paths, **options)
            else:
                from dmri_studio.core.runs import load_verified_product

                result = load_verified_product(self.expected_product, self.loader, **options)
            self.result.emit(self.request, result)
        except Exception:
            self.error.emit(self.request, traceback.format_exc())
        finally:
            self.finish()


class SliceWorker(GuiOwnedWorker):
    """Read one requested native plane; never materialize a whole volume."""

    result = Signal(int, object, str)

    def __init__(
        self, request: int, dataset: DwiDataset, volume: int, axis: int, index: int | None = None
    ) -> None:
        """Retain the dataset until this read finishes."""
        super().__init__()
        self.request, self.dataset, self.volume, self.axis = request, dataset, volume, axis
        self.index = index

    @Slot()
    def run(self) -> None:
        """Deliver either a standalone plane or an actionable core error."""
        try:
            self.result.emit(
                self.request,
                self.dataset.read_slice(self.volume, axis=self.axis, index=self.index),
                "",
            )
        except Exception as exc:
            self.result.emit(self.request, None, str(exc))
        finally:
            self.finish()


class ViewerTaskWorker(GuiOwnedWorker):
    """Execute an overlay metadata/plane task in the existing serialized queue."""

    result = Signal(int, object, str)

    def __init__(self, request: int, task: Callable[[], object]) -> None:
        """Capture a callable containing data arguments only, never widgets."""
        super().__init__()
        self.request, self.task = request, task

    @Slot()
    def run(self) -> None:
        """Deliver failures as readable messages and always release the thread."""
        try:
            self.result.emit(self.request, self.task(), "")
        except Exception as exc:
            self.result.emit(self.request, None, str(exc))
        finally:
            self.finish()
