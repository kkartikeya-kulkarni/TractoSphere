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
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.runs import inspect_run

from .components import Disclosure, button, label
from .execution import ProcessingWorker
from .workstation import EmptyState

RUN_NAMES = {
    "mrtrix_execution_plan": "Diffusion preprocessing",
    "mrtrix_native_execution_plan": "Native diffusion preprocessing",
    "fsl_correction_plan": "TOPUP / EDDY correction",
    "mrtrix_ants_bias_plan": "N4 bias correction",
    "mrtrix_dti_execution_plan": "Diffusion tensor",
    "mrtrix_csd_execution_plan": "FOD reconstruction",
    "mrtrix_anatomy_execution_plan": "Anatomy / five-tissue",
    "mrtrix_sift2_execution_plan": "SIFT2 weights",
    "mrtrix_connectome_execution_plan": "Structural connectivity",
    "mrtrix_tracking_execution_plan": "Tractography",
    "registration_execution_plan": "Registration",
}


class ResultsPage(QWidget):
    """Recover evidence without rerunning analyses or implicitly restarting jobs."""

    load_requested = Signal(object)
    qc_requested = Signal(object)
    scientific_requested = Signal()

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
        layout.addWidget(label("Runs & Reports", "title"))
        layout.addWidget(
            label(
                "Reopen outputs, processing history and QC. Opening a run never restarts a correction. Continue from verified outputs in a new attempt.",
                "caption",
            )
        )
        actions = QHBoxLayout()
        self.open = button("Open run manifest", self.browse)
        self.recent = button("Open last inspected run", self.open_recent)
        self.refresh = button("Refresh saved status", self.reload)
        actions.addWidget(self.open)
        actions.addWidget(self.recent)
        actions.addWidget(self.refresh)
        actions.addWidget(button("Open scientific selection…", self.scientific_requested.emit))
        actions.addStretch()
        layout.addLayout(actions)
        self.status = label(
            "Select an engine run manifest. Existing study reports can be opened in QC.", "caption"
        )
        layout.addWidget(self.status)
        self.empty = EmptyState(
            "No processing run open",
            "Open a saved engine manifest to inspect its steps, outputs and QC. This never restarts processing.",
            [("Open run…", self.browse)],
        )
        layout.addWidget(self.empty, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a step or recorded status…")
        self.search.setAccessibleName("Search saved processing steps")
        self.search.textChanged.connect(self.filter_steps)
        layout.addWidget(self.search)
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
        self.advanced = Disclosure("Exact commands and provenance", self.details)
        layout.addWidget(self.advanced)

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

    def open_recent(self):
        """Load only after a user action; never auto-open private run evidence."""
        saved = str(self.settings.get("last_run_manifest", "") or "")
        if not saved or not Path(saved).is_file():
            self.status.setText(
                "The last inspected run is unavailable. Choose an existing manifest."
            )
            return
        self.load(Path(saved))

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
            f"{RUN_NAMES.get(result['kind'], 'Scientific processing')} · Recorded status: {result['status']}"
            f" · {result['verified_hashes']} hashes verified"
            f" · {len(result['products'])} verified output(s)"
            " · Scientific review required\n" + result["notice"]
        )
        for row in result["steps"]:
            timing = "" if row["elapsed_seconds"] is None else f" · {row['elapsed_seconds']:.1f}s"
            code = "" if row["return_code"] is None else f" · exit {row['return_code']}"
            self.steps.addItem(f"{row['step']} · {row['status']}{timing}{code}")
        for product in result["products"]:
            source = product.get("path") or product.get("artifact", {}).get("path")
            self.products.addItem(
                product.get("label") or (Path(source).name if source else "Verified output")
            )
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
        self.filter_steps(self.search.text())

    def finished(self):
        """Re-enable actions only when evidence worker ownership has ended."""
        self.thread.wait()
        self.thread.deleteLater()
        self.thread = self.worker = None
        self.update_actions()

    def update_actions(self):
        """No output replacement while processing or metadata inspection is active."""
        idle = self.thread is None
        self.empty.setVisible(self.snapshot is None)
        for widget in (
            self.search,
            self.steps,
            self.recovery_summary,
            self.load_output,
            self.open_qc,
            self.recover,
            self.advanced,
        ):
            widget.setVisible(self.snapshot is not None)
        self.products.setVisible(bool(self.snapshot and self.snapshot["products"]))
        self.open.setVisible(self.snapshot is not None)
        self.recent.setVisible(bool(self.settings.get("last_run_manifest", "")))
        self.refresh.setVisible(self.path is not None)
        for action in self.empty.actions:
            action.setEnabled(idle)
        self.open.setEnabled(idle)
        self.recent.setEnabled(idle)
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

    def filter_steps(self, text):
        for index in range(self.steps.count()):
            item = self.steps.item(index)
            item.setHidden(text.casefold() not in item.text().casefold())

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
