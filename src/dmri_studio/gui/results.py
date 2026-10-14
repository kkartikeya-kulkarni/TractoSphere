"""Saved engine attempts, explicit output handoff and existing QC access."""

import json
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.runs import inspect_run

from .components import Disclosure, button, label
from .execution import ProcessingWorker


class ResultsPage(QWidget):
    """Recover evidence without rerunning analyses or implicitly restarting jobs."""

    load_requested = Signal(object)
    qc_requested = Signal(object)

    def __init__(self, settings):
        """Keep evidence hashing outside the UI thread and preserve source attempts."""
        super().__init__()
        self.settings = settings
        self.thread = self.worker = None
        self.cancel = Event()
        self.snapshot = None
        self.path = None
        self.processing_busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(label("Saved preprocessing runs", "title"))
        layout.addWidget(
            label(
                "Reopen outputs, processing history and QC. Opening a run never restarts a correction. Continue from verified outputs in a new attempt.",
                "caption",
            )
        )
        actions = QHBoxLayout()
        self.open = button("Open run manifest", self.browse)
        self.refresh = button("Refresh saved status", self.reload)
        actions.addWidget(self.open)
        actions.addWidget(self.refresh)
        actions.addStretch()
        layout.addLayout(actions)
        self.status = label(
            "Select an engine run manifest. Existing study reports can be opened in QC.", "caption"
        )
        layout.addWidget(self.status)
        self.steps = QListWidget()
        self.steps.setAccessibleName("Saved processing step states")
        self.steps.setMinimumHeight(160)
        layout.addWidget(self.steps, 1)
        self.products = QComboBox()
        self.products.setAccessibleName("Verified diffusion outputs")
        layout.addWidget(self.products)
        self.recovery_summary = label("", "caption")
        layout.addWidget(self.recovery_summary)
        row = QGridLayout()
        self.load_output = button("Load output && history", self.choose_output)
        self.open_qc = button("Open existing QC", self.choose_qc)
        self.recover = button("Continue from checkpoint", self.choose_recovery)
        row.addWidget(self.load_output, 0, 0)
        row.addWidget(self.open_qc, 0, 1)
        row.addWidget(self.recover, 1, 0, 1, 2)
        layout.addLayout(row)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Saved commands, results and provenance")
        self.details.setMaximumHeight(300)
        layout.addWidget(Disclosure("Exact commands and provenance", self.details))

        self.update_actions()

    def browse(self):
        """Use a remembered evidence folder without automatically loading data."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open processing manifest",
            str(self.settings.get("last_run_manifest", "")),
            "JSON (*.json)",
        )
        if path:
            self.load(Path(path))

    def reload(self):
        """Read the latest published snapshot; no process control is exposed."""
        if self.path:
            self.load(self.path)

    def load(self, path):
        """Verify immutable completed artifacts in a cancellable read-only worker."""
        if self.thread:
            return
        self.path = Path(path)
        self.snapshot = None
        self.products.clear()
        self.steps.clear()
        self.details.clear()
        self.recovery_summary.clear()
        self.cancel = Event()
        self.thread = QThread(self)
        worker = ProcessingWorker(0, lambda: None)
        self.worker = worker
        cancel = self.cancel
        source = self.path
        worker.task = lambda: inspect_run(
            source, cancelled=cancel.is_set, progress=worker.progress.emit
        )
        worker.moveToThread(self.thread)
        self.thread.started.connect(worker.run)
        worker.progress.connect(self.status.setText)
        worker.result.connect(self.result)
        worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.thread.finished.connect(worker.deleteLater)
        self.thread.finished.connect(self.finished)
        self.status.setText("Reading saved evidence…")
        self.update_actions()
        self.thread.start()

    def result(self, _, result, error):
        """Display execution and evidence verification without scientific acceptance."""
        if error:
            self.status.setText(error)
            return
        self.snapshot = result
        self.settings.set("last_run_manifest", str(self.path))
        self.status.setText(
            f"Recorded status: {result['status']} · {result['verified_hashes']} hashes verified · Scientific review required\n{result['notice']}"
        )
        for row in result["steps"]:
            timing = "" if row["elapsed_seconds"] is None else f" · {row['elapsed_seconds']:.1f}s"
            code = "" if row["return_code"] is None else f" · exit {row['return_code']}"
            self.steps.addItem(f"{row['step']} · {row['status']}{timing}{code}")
        for product in result["products"]:
            self.products.addItem(product["label"])
        recovery = result.get("recovery")
        self.recovery_summary.setText(
            "Verified checkpoint: "
            + recovery["product"]["paths"]["dwi"]
            + "\nRemaining selected operations: "
            + ", ".join(recovery["suggested_operations"])
            + ". Review fresh commands before running in a new directory."
            if recovery
            else result["record"].get("error", "")
        )
        self.details.setPlainText(json.dumps(result, indent=2))

    def finished(self):
        """Re-enable actions only when evidence worker ownership has ended."""
        self.thread.wait()
        self.thread.deleteLater()
        self.thread = self.worker = None
        self.update_actions()

    def update_actions(self):
        """No output replacement while processing or metadata inspection is active."""
        idle = self.thread is None
        self.products.setVisible(bool(self.snapshot and self.snapshot["products"]))
        self.open.setEnabled(idle)
        self.refresh.setEnabled(idle and self.path is not None)
        self.load_output.setEnabled(
            idle and not self.processing_busy and bool(self.snapshot and self.snapshot["products"])
        )
        self.recover.setEnabled(
            idle
            and not self.processing_busy
            and bool(self.snapshot and self.snapshot.get("recovery"))
        )
        self.open_qc.setEnabled(idle and bool(self.snapshot and self.snapshot["qc_catalog"]))

    def choose_output(self):
        """Ask the existing loader to activate a specific verified derivative."""
        if self.snapshot and self.products.currentIndex() >= 0 and not self.processing_busy:
            self.load_requested.emit(
                {
                    "product": self.snapshot["products"][self.products.currentIndex()],
                    "handoff": self.snapshot["handoff"],
                }
            )

    def choose_recovery(self):
        """Load only a saved verified prefix; fresh command approval remains required."""
        if self.snapshot and self.snapshot.get("recovery") and not self.processing_busy:
            self.load_requested.emit(self.snapshot["recovery"])

    def choose_qc(self):
        """QC performs its own saved-artifact verification, with no new analyses."""
        if self.snapshot and self.snapshot["qc_catalog"]:
            self.qc_requested.emit(Path(self.snapshot["qc_catalog"]))

    def shutdown(self):
        """Cancel only this page's read task, never any MRI/background process."""
        self.cancel.set()
        return self.thread is None
