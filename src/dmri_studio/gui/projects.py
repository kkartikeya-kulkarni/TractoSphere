"""Persistent acquisition workspace reusing the existing loader, executor and QC."""

from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.execution import hash_file
from dmri_studio.core.projects import (
    acquisition_context,
    add_acquisition,
    attach_attempt,
    discover_acquisitions,
    new_project,
    read_project,
    save_project,
)
from dmri_studio.core.workflow import ProcessingHistory

from .components import Disclosure, button, label
from .execution import ProcessingWorker

EXECUTION_LABELS = {
    "validated_pending_qc": "Technical checks passed · Review pending",
    "validated": "Structural checks passed · Review separate",
    "failed": "Failed · Evidence retained",
    "cancelled": "Cancelled · Incomplete outputs retained",
}


class ProjectPage(QWidget):
    """Track separate acquisitions and reusable assets, never implicit concatenation."""

    load_requested = Signal(object)
    run_requested = Signal(object)
    project_changed = Signal(str)
    notification = Signal(str, str)

    def __init__(self, current_context):
        super().__init__()
        self.current_context = current_context
        self.project = None
        self.path = None
        self.snapshot_sha256 = None
        self.thread = self.worker = None
        self.cancel = Event()
        self.busy = False
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)
        root.addWidget(label("Project acquisitions", "title"))
        root.addWidget(
            label(
                "Keep subjects, sessions and acquisitions distinct. Shared calibration remains explicitly associated. Open one acquisition in the existing viewer and preprocessing workspace.",
                "caption",
            )
        )
        row = QHBoxLayout()
        self.new = button("New project", self.create)
        self.open = button("Open project", self.browse)
        self.scan = button("Import BIDS acquisitions", self.scan_bids)
        self.add = button("Add loaded input", self.add_current)
        for control in (self.new, self.open, self.scan, self.add):
            row.addWidget(control)
        root.addLayout(row)
        self.status = label(
            "Create a project snapshot outside your source-data folders.", "caption"
        )
        root.addWidget(self.status)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Subject", "Session", "Acquisition / run / direction", "Attempts", "Latest execution"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.selection)
        self.table.setMinimumHeight(180)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        root.addWidget(self.table, 1)
        self.acquisition_summary = label(
            "Select an acquisition to inspect its metadata and associations.", "caption"
        )
        root.addWidget(self.acquisition_summary)
        self.attempt_choice = QComboBox()
        self.attempt_choice.setAccessibleName("Recorded acquisition attempts")
        root.addWidget(self.attempt_choice)
        actions = QHBoxLayout()
        self.load_input = button("Load selected acquisition", self.activate)
        self.attach = button("Attach attempt", self.attach_run)
        self.inspect = button("Open attempt && QC", self.inspect_run)
        for control in (self.load_input, self.attach, self.inspect):
            actions.addWidget(control)
        root.addLayout(actions)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(210)
        root.addWidget(Disclosure("Detailed paths and provenance", self.details))

        self.actions()

    def create(self):
        name, ok = QInputDialog.getText(self, "New acquisition project", "Project name")
        if not ok or not name.strip():
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save first immutable project snapshot", "", "JSON (*.json)"
        )
        if filename:
            self.task(lambda: self.persist(new_project(name), Path(filename)))

    def browse(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open project snapshot", "", "JSON (*.json)"
        )
        if filename:
            self.open_path(Path(filename))

    def open_path(self, path):
        def read():
            digest = hash_file(path, self.cancel.is_set)
            record = read_project(path)
            if hash_file(path, self.cancel.is_set) != digest:
                raise ValueError("Project snapshot changed while opening")
            return record, path, digest

        self.task(read)

    def persist(self, record, path=None):
        """Every edit creates a new snapshot; previous snapshots stay readable."""
        if path is None:
            import uuid

            path = self.path.parent / f"project-{uuid.uuid4().hex[:12]}.json"
            if hash_file(self.path, self.cancel.is_set) != self.snapshot_sha256:
                raise ValueError("Project snapshot changed externally; reopen it before editing")
            record["previous_snapshot"] = {"path": str(self.path), "sha256": self.snapshot_sha256}
        save_project(record, path)
        return record, path, hash_file(path, self.cancel.is_set)

    def scan_bids(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select BIDS dataset root (metadata/header inspection only)"
        )
        if folder:
            self.task(
                lambda: discover_acquisitions(Path(folder), cancelled=self.cancel.is_set),
                kind="discovery",
            )

    def import_discovery(self, rows):
        """Offer explicit selection; never import an entire cohort by surprise."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QListWidget, QListWidgetItem

        dialog = QDialog(self)
        dialog.setWindowTitle("Select acquisitions to keep separate")
        dialog.resize(860, 460)
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            label(
                "Header/gradient checks only. History remains unknown. Calibration issues remain visible and cannot authorize correction.",
                "caption",
            )
        )
        listing = QListWidget()
        layout.addWidget(listing)
        for row in rows:
            item = QListWidgetItem(
                str(Path(row["paths"]["dwi"]).relative_to(row["bids_root"]))
                + (" · NEEDS REVIEW: " + "; ".join(row["issues"]) if row["issues"] else "")
            )
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            listing.addItem(item)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = [
            r for i, r in enumerate(rows) if listing.item(i).checkState() == Qt.CheckState.Checked
        ]
        if selected:
            self.import_rows(selected)

    def import_rows(self, rows):
        """Hash selected inputs off-thread; one failed import never mutates a snapshot."""

        def work():
            record = self.project
            for row in rows:
                record = add_acquisition(
                    record,
                    row["paths"],
                    row["identity"],
                    ProcessingHistory().to_dict(),
                    discovery=row,
                    cancelled=self.cancel.is_set,
                )
            return self.persist(record)

        self.task(work)

    def add_current(self):
        dataset, history = self.current_context()
        if dataset is None:
            self.status.setText(
                "Load the input and review its history first. Unknown stages may remain unknown."
            )
            return
        subject, ok = QInputDialog.getText(
            self, "Acquisition identity", "Subject label (shared across sessions and runs)"
        )
        if not ok or not subject.strip():
            return
        session, ok = QInputDialog.getText(
            self, "Acquisition identity", "Session label (leave empty if unavailable)"
        )
        if not ok:
            return
        run, ok = QInputDialog.getText(self, "Acquisition identity", "Acquisition / run label")
        if not ok:
            return
        evidence, _ = QFileDialog.getOpenFileNames(
            self, "Optional original processing records (Cancel keeps history declarations only)"
        )
        paths = {k: str(v) for k, v in dataset.paths.supplied().items()}
        saved_history = history.to_dict()
        self.task(
            lambda: self.persist(
                add_acquisition(
                    self.project,
                    paths,
                    {"sub": subject, "ses": session, "run": run},
                    saved_history,
                    evidence=evidence,
                    cancelled=self.cancel.is_set,
                )
            )
        )

    def selected(self):
        row = self.table.currentRow()
        return (
            self.project["acquisitions"][row]
            if self.project and 0 <= row < len(self.project["acquisitions"])
            else None
        )

    def selection(self):
        import json

        self.details.setPlainText(json.dumps(self.selected(), indent=2) if self.selected() else "")
        row = self.selected()
        self.attempt_choice.clear()
        if row:
            for attempt in row["attempts"]:
                self.attempt_choice.addItem(
                    " / ".join(attempt["operations"])
                    + " · "
                    + EXECUTION_LABELS.get(attempt["status"], attempt["status"])
                    + " · "
                    + str(Path(attempt["manifest"]).parent.name),
                    attempt["manifest"],
                )
            self.attempt_choice.setCurrentIndex(len(row["attempts"]) - 1)
            discovery = row.get("discovery") or {}
            metadata = discovery.get("acquisition", {}).get("metadata", {})
            header = discovery.get("header", {})
            self.acquisition_summary.setText(
                "Input: "
                + row["paths"]["dwi"]
                + "\n"
                + "Shape: "
                + str(header.get("shape", "available after input validation"))
                + " · TE: "
                + str(metadata.get("EchoTime", "undocumented"))
                + " · PE: "
                + str(metadata.get("PhaseEncodingDirection", "undocumented"))
                + " · Readout (s): "
                + str(metadata.get("TotalReadoutTime", "undocumented"))
                + "\nVerified calibration associations: "
                + str(len(discovery.get("references", [])))
                + " · Anatomical candidates: "
                + str(len(discovery.get("anatomy_candidates", [])))
                + "\nHistory: "
                + (
                    "reviewed declarations/inferences"
                    if row["history"]["confirmed"]
                    else "unknown / unconfirmed"
                )
                + ". Scientific review remains separate."
                + (
                    "\nInput issues: " + "; ".join(discovery["issues"])
                    if discovery.get("issues")
                    else ""
                )
                + (
                    "\nSome candidate references were rejected; see detailed provenance before correction."
                    if discovery.get("reference_issues")
                    else ""
                )
            )
        self.actions()

    def activate(self):
        if self.selected() and not self.busy:
            identifier = self.selected()["id"]
            self.task(
                lambda: acquisition_context(self.path, identifier, cancelled=self.cancel.is_set),
                kind="load",
            )

    def attach_run(self):
        if not self.selected():
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "Associate a finished engine attempt by exact input lineage", "", "JSON (*.json)"
        )
        if filename:
            identifier = self.selected()["id"]
            self.task(
                lambda: self.persist(
                    attach_attempt(
                        self.project, identifier, Path(filename), cancelled=self.cancel.is_set
                    )
                )
            )

    def inspect_run(self):
        path = self.attempt_choice.currentData()
        if path:
            self.run_requested.emit(Path(path))

    def binding_for_product(self, product):
        """Keep acquisition identity only for a recorded producer in this project."""
        if self.project:
            source = str(Path(product["source_manifest"]).resolve())
            for row in self.project["acquisitions"]:
                if any(a["manifest"] == source for a in row["attempts"]):
                    return {"project_id": self.project["id"], "acquisition_id": row["id"]}
        return None

    def record_finished_attempt(self, path, binding):
        """Save an immutable project revision after exact input lineage validation.

        Identity comes from the loaded acquisition, never the selected table row.
        If another project is open or the ledger is busy, preserve the run and
        explain how to attach it later; never silently attach to a different row.
        """
        if not binding:
            return
        if (
            not self.project
            or self.project["id"] != binding["project_id"]
            or self.thread
            or self.busy
        ):
            self.notification.emit(
                f"Run saved at {path}. Open its original project and attach the attempt to record it.",
                "warning",
            )
            return
        identifier = binding["acquisition_id"]
        self.task(
            lambda: self.persist(
                attach_attempt(self.project, identifier, Path(path), cancelled=self.cancel.is_set)
            ),
            kind="record_attempt",
        )

    def task(self, callback, kind="project"):
        if self.thread or self.busy:
            return
        self.cancel = Event()
        self.kind = kind
        self.thread = QThread(self)
        self.worker = ProcessingWorker(0, callback)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.result.connect(self.result)
        self.worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.finished)
        self.status.setText("Reading and binding acquisition evidence…")
        self.actions()
        self.thread.start()

    def result(self, _, result, error):
        self.pending_discovery = self.pending_load = None
        if error:
            self.status.setText(error)
            self.notification.emit(error, "error")
        elif self.kind == "discovery":
            self.pending_discovery = result
        elif self.kind == "load":
            self.pending_load = result
        else:
            selected_id = self.selected()["id"] if self.selected() else None
            self.project, self.path, self.snapshot_sha256 = result
            self.project_changed.emit(self.project["name"])
            self.table.setRowCount(len(self.project["acquisitions"]))
            for i, row in enumerate(self.project["acquisitions"]):
                identity = row["identity"]
                values = [
                    identity.get("sub", ""),
                    identity.get("ses", ""),
                    " / ".join(identity.get(k, "") for k in ("acq", "run", "dir")),
                    str(len(row["attempts"])),
                    EXECUTION_LABELS.get(
                        row["attempts"][-1]["status"], row["attempts"][-1]["status"]
                    )
                    if row["attempts"]
                    else "Not processed here",
                ]
                for j, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    self.table.setItem(i, j, item)
                if row["id"] == selected_id:
                    self.table.selectRow(i)
            self.table.resizeColumnsToContents()
            for column, width in ((0, 110), (1, 90), (3, 90)):
                self.table.setColumnWidth(column, max(width, self.table.columnWidth(column)))
            subjects = len({r["identity"]["sub"] for r in self.project["acquisitions"]})
            self.status.setText(
                f"{self.project['name']} · {subjects} subject(s) · {len(self.project['acquisitions'])} acquisitions · Scientific review separate\nSnapshot: {self.path}"
            )
            self.selection()
            if self.kind == "record_attempt":
                self.notification.emit(
                    "Run and provenance saved in a new project snapshot.", "success"
                )

    def finished(self):
        self.thread.wait()
        self.thread.deleteLater()
        self.thread = self.worker = None
        self.actions()
        if getattr(self, "pending_load", None):
            self.load_requested.emit(self.pending_load)
            self.pending_load = None
        if getattr(self, "pending_discovery", None):
            rows = self.pending_discovery
            self.pending_discovery = None
            self.import_discovery(rows)

    def actions(self):
        idle = not self.thread and not self.busy
        self.new.setEnabled(idle)
        self.open.setEnabled(idle)
        for control in (self.scan, self.add):
            control.setEnabled(idle and self.project is not None)
        for control in (self.load_input, self.attach):
            control.setEnabled(idle and self.selected() is not None)
        self.inspect.setEnabled(idle and bool(self.selected() and self.selected()["attempts"]))
        self.attempt_choice.setEnabled(idle and self.attempt_choice.count() > 0)

    def shutdown(self):
        self.cancel.set()
        return self.thread is None
