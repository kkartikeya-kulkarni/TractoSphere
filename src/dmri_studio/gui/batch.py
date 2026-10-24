"""Study workspace using the headless batch services and existing result viewers."""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.batch.discovery import Study, discover_bids, discover_custom, import_manifest
from dmri_studio.core.batch.engine import (
    BatchRunner,
    control,
    create_batch,
    export_report,
    read_journal,
    study_qc,
)
from dmri_studio.core.batch.planning import ADAPTERS, GIB, build_batch_plan
from dmri_studio.core.execution import ExecutionPlan, _publish_json
from dmri_studio.core.runs import inspect_run
from dmri_studio.core.scientific.records import read_json

from .components import button, label
from .execution import ProcessingWorker
from .settings import ScientificSoftwareSettings


class BatchPage(QWidget):
    """Compact acquisition table, explicit approval, journal controls and QC."""

    product_requested = Signal(object)
    run_requested = Signal(object)
    qc_requested = Signal(object)
    busy_changed = Signal(bool)

    def __init__(self, theme, parent=None, settings=None):
        super().__init__(parent)
        self.theme = theme
        self.settings = settings
        self.study = self.plan = self.folder = None
        self.thread = self.worker = None
        self._retired_workers = []
        self.cancel = Event()
        self.tool_directory = None
        self.fsl_directory = None
        self.ants_directory = None
        self.historical_toolchain = False
        self.historical_plan = None
        self._plan_generation = 0
        self._pending_plan_generation = None
        self._pending_discovery_generation = None
        self.processing_available = lambda: True
        self.kind = ""
        self.rows = []
        self.products = []
        self.inspected_handoff = {}
        self.registration_reviews = {}
        self._journal_signature = None
        self.guided_selection = None
        advanced = QWidget(self)
        root = QVBoxLayout(advanced)
        root.addWidget(label("Batch Processing", "title"))
        root.addWidget(
            label(
                "Review each acquisition, approve the workflow, and recover verified results. Scientific acceptance remains separate.",
                "caption",
            )
        )
        self.tabs = QTabWidget()
        self.tabs.setMinimumHeight(220)
        self.tabs.setMaximumHeight(310)
        root.addWidget(self.tabs)
        setup = QWidget()
        form = QFormLayout(setup)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.method = QComboBox()
        self.method.addItems(["BIDS", "Custom mapping", "CSV / TSV manifest"])
        self.source_root = QLineEdit()
        self.study_name = QLineEdit("study-001")
        self.import_file = QLineEdit()
        self.output = QLineEdit()
        self.mrtrix_directory = QLineEdit()
        # Reuse the installation configured once in Software Dependencies.
        # Saved batch histories may still explicitly pin an earlier toolchain.
        if self.settings:
            software = ScientificSoftwareSettings(self.settings)
            self.tool_directory = software.location("MRtrix3")
            self.fsl_directory = software.location("FSL")
            self.ants_directory = software.location("ANTs")
            self.mrtrix_directory.setText(str(self.tool_directory or ""))
        self.mrtrix_directory.setPlaceholderText(
            "Uses saved MRtrix3 location; configure it in Software Dependencies"
        )
        for title, widget in [
            ("Study ID", self.study_name),
            ("Input method", self.method),
            ("Authorized dataset root", self.source_root),
            ("Mapping / manifest file", self.import_file),
            ("New batch output directory", self.output),
            ("MRtrix bin directory", self.mrtrix_directory),
        ]:
            widget.setAccessibleName(title)
            if isinstance(widget, QLineEdit) and widget not in (self.study_name,):
                row = QHBoxLayout()
                row.addWidget(widget)
                row.addWidget(button("Choose", lambda checked=False, w=widget: self.choose_path(w)))
                form.addRow(title, row)
            else:
                form.addRow(title, widget)
        import_row = QHBoxLayout()
        import_row.addWidget(button("Discover inputs", self.discover))
        import_row.addWidget(button("Save study", self.save_study))
        import_row.addWidget(button("Open study", self.open_study))
        form.addRow(import_row)
        self.adapter = QComboBox()
        self.adapter.addItems(ADAPTERS)
        self.adapter.setAccessibleName("Scientific adapter")
        workflow_row = QHBoxLayout()
        workflow_row.addWidget(self.adapter)
        workflow_row.addWidget(button("Use saved scientific selections", self.selection_workflow))
        workflow_row.addWidget(button("Tensor metric workflow", self.tensor_workflow))
        workflow_row.addWidget(button("Registration workflow…", self.registration_workflow))
        workflow_row.addWidget(button("Load workflow", self.load_workflow))
        workflow_row.addWidget(button("Save workflow", self.save_workflow))
        form.addRow("Workflow", workflow_row)
        self.workflow = QPlainTextEdit()
        self.workflow.setAccessibleName("Typed workflow configuration")
        self.workflow.setMinimumHeight(110)
        self.workflow.setMaximumHeight(170)
        self.tensor_workflow()
        form.addRow(self.workflow)
        self.max_subjects, self.max_processes, self.threads, self.memory = (
            QSpinBox() for _ in range(4)
        )
        resource_row = QHBoxLayout()
        for name, widget, maximum, value in [
            ("Subjects", self.max_subjects, 16, 1),
            ("Processes", self.max_processes, 16, 1),
            ("CPU budget", self.threads, 64, 2),
            ("RAM GiB", self.memory, 128, 4),
        ]:
            widget.setRange(1, maximum)
            widget.setValue(value)
            widget.setAccessibleName(name)
            resource_row.addWidget(label(name, "caption"))
            resource_row.addWidget(widget)
        form.addRow("Resource limits", resource_row)
        setup_scroll = QScrollArea()
        setup_scroll.setWidgetResizable(True)
        setup_scroll.setWidget(setup)
        self.tabs.addTab(setup_scroll, "Inputs")
        review = QWidget()
        review_layout = QVBoxLayout(review)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("Execution plan and exact commands")
        review_layout.addWidget(self.preview)
        self.approval = QCheckBox(
            "I reviewed this plan and approve eligible work, including validated dependency continuations"
        )
        self.approval.setAccessibleName("Approve batch plan")
        review_layout.addWidget(self.approval)
        review_layout.addWidget(button("Export exact plan", self.save_plan))
        self.tabs.addTab(review, "Plan")
        controls = QHBoxLayout()
        self.plan_button = button("Validate / Dry run", self.dry_run)
        self.new_button = button("New batch with current software", self.new_batch)
        self.run_button = button("Run approved batch", self.execute)
        self.pause_button = button("Pause scheduling", lambda: self.send_control("pause"))
        self.resume_button = button("Resume", self.resume)
        self.cancel_button = button(
            "Cancel selected", lambda: self.send_control("cancel", selected=True)
        )
        self.cancel_all_button = button("Cancel batch", lambda: self.send_control("cancel"))
        self.retry_button = button(
            "Retry selected", lambda: self.send_control("retry", selected=True)
        )
        for widget in (
            self.new_button,
            self.plan_button,
            self.run_button,
            self.pause_button,
            self.resume_button,
            self.cancel_button,
            self.cancel_all_button,
            self.retry_button,
        ):
            controls.addWidget(widget)
        root.addLayout(controls)
        filter_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search subject, acquisition or status")
        self.search.setAccessibleName("Search batch subjects")
        self.search.textChanged.connect(self.filter_rows)
        filter_row.addWidget(self.search)
        self.history = QComboBox()
        self.history.setAccessibleName("Recent batch history")
        self.history.addItem("Recent batches…", None)
        if self.settings:
            for path in self.settings.get("batch/history", []):
                self.history.addItem(Path(path).name, path)
        self.history.activated.connect(self.open_recent)
        filter_row.addWidget(self.history)
        filter_row.addWidget(button("Open batch…", self.open_batch))
        filter_row.addWidget(button("Export report", self.export))
        filter_row.addWidget(button("Registration QC", self.registration_qc))
        filter_row.addWidget(button("Export atlas QC", self.export_registration_qc))
        self.qc_filter = QComboBox()
        self.qc_filter.addItems(
            [
                "All review states",
                "NEEDS_REVIEW",
                "APPROVED",
                "REJECTED",
                "STALE_EVIDENCE",
                "FAILED",
            ]
        )
        self.qc_filter.setAccessibleName("Batch registration review filter")
        self.qc_filter.currentIndexChanged.connect(self.filter_rows)
        filter_row.addWidget(self.qc_filter)
        root.addLayout(filter_row)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                "Subject",
                "Session",
                "Acquisition",
                "Stage",
                "Execution",
                "QC",
                "Runtime s",
                "Output / finding",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setAccessibleName("Batch subject and acquisition table")
        self.table.itemSelectionChanged.connect(self.show_selected)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Acquisition metadata and scientific QC")
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.table)
        split.addWidget(self.details)
        split.setSizes([950, 350])
        split.setMinimumHeight(240)
        root.addWidget(split, 2)
        result_row = QHBoxLayout()
        result_row.addWidget(button("Verify selected results", self.inspect_selected))
        self.products_list = QListWidget()
        self.products_list.setMaximumHeight(75)
        self.products_list.setAccessibleName("Verified scientific products")
        result_row.addWidget(self.products_list, 1)
        result_row.addWidget(button("Open product", self.open_product))
        result_row.addWidget(button("Study QC", self.show_qc))
        result_row.addWidget(button("Record review", self.review_selected))
        root.addLayout(result_row)
        self.summary = label("No batch execution", "caption")
        root.addWidget(self.summary)
        self.status = label("Import a study to begin. No processing has started.", "caption")
        root.addWidget(self.status)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(1000)
        self.logs.setMaximumHeight(85)
        self.logs.setAccessibleName("Batch execution log")
        root.addWidget(self.logs)
        self.timer = QTimer(self)
        self.timer.setInterval(600)
        self.timer.timeout.connect(self.refresh)
        self.approval.toggled.connect(self.update_actions)
        for edit in (
            self.workflow,
            self.output,
            self.source_root,
            self.study_name,
            self.import_file,
            self.mrtrix_directory,
        ):
            edit.textChanged.connect(self.invalidate)
        for spin in (self.max_subjects, self.max_processes, self.threads, self.memory):
            spin.valueChanged.connect(self.invalidate)
        self.method.currentIndexChanged.connect(self.invalidate)
        from .batch_guided import GuidedBatch

        display = QVBoxLayout(self)
        mode = QHBoxLayout()
        self.mode_label = label("Guided batch setup", "caption")
        mode.addWidget(self.mode_label)
        mode.addStretch()
        self.mode_button = button("Advanced Settings", self.toggle_mode)
        mode.addWidget(self.mode_button)
        display.addLayout(mode)
        self.display_stack = QStackedWidget()
        self.display_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.guided = GuidedBatch(self)
        self.display_stack.addWidget(self.guided)
        advanced_scroll = QScrollArea()
        advanced_scroll.setWidgetResizable(True)
        advanced_scroll.setWidget(advanced)
        self.display_stack.addWidget(advanced_scroll)
        display.addWidget(self.display_stack)
        self.update_actions()

    def toggle_mode(self):
        advanced = self.display_stack.currentIndex() == 0
        self.display_stack.setCurrentIndex(1 if advanced else 0)
        self.mode_button.setText("Guided Setup" if advanced else "Advanced Settings")
        self.mode_label.setText("Advanced batch controls" if advanced else "Guided batch setup")

    def _update_polling(self):
        """Poll only a visible saved batch or a live operation."""
        should_poll = self.thread is not None or (self.isVisible() and self.folder is not None)
        if should_poll and not self.timer.isActive():
            self.timer.start()
        elif not should_poll and self.timer.isActive():
            self.timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "timer"):
            self.refresh()
            self._update_polling()

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, "timer"):
            self._update_polling()

    def invalidate(self):
        self._plan_generation += 1
        self.plan = None
        self.approval.setChecked(False)
        self.update_actions()
        if hasattr(self, "guided"):
            self.guided.sync_plan_state()

    def choose_path(self, widget):
        if widget is self.import_file:
            name, _ = QFileDialog.getOpenFileName(self, "Select manifest or custom mapping")
        else:
            name = QFileDialog.getExistingDirectory(self, "Select directory")
            if widget is self.output and name:
                name = str(Path(name) / ("batch-" + time.strftime("%Y%m%d-%H%M%S")))
        if name:
            widget.setText(name)

    def tensor_workflow(self):
        self.workflow_text = {
            "nodes": [
                dict(
                    id="metrics",
                    adapter="dti",
                    imports={"tensor": {"role": "tensor", "kind": "tensor"}},
                    tasks=[
                        dict(
                            id="metrics",
                            algorithm="tensor2metric",
                            inputs={"tensor": {"artifact_id": "tensor"}},
                            parameters={"metrics": ["FA", "MD", "AD", "RD"]},
                        )
                    ],
                )
            ]
        }
        if hasattr(self, "workflow"):
            self.workflow.setPlainText(json.dumps(self.workflow_text, indent=2))

    def selection_workflow(self):
        self.workflow.setPlainText(
            json.dumps(
                {
                    "nodes": [
                        dict(
                            id="analysis",
                            adapter=self.adapter.currentText(),
                            selection_role="selection",
                        )
                    ]
                },
                indent=2,
            )
        )

    def discover(self):
        if not self.source_root.text().strip():
            self.status.setText("Choose an existing dataset folder before discovery.")
            return
        if self.method.currentIndex() in (1, 2) and not self.import_file.text().strip():
            self.status.setText("Choose a mapping or manifest file before discovery.")
            return
        root, name, source, method = (
            Path(self.source_root.text()),
            self.study_name.text(),
            Path(self.import_file.text()),
            self.method.currentIndex(),
        )

        def operation(cancel, progress):
            if method == 0:
                return discover_bids(root, name)
            if method == 1:
                return discover_custom(root, name, read_json(source.read_text()))
            return import_manifest(source, root, name)

        self.start("discover", operation)

    def dry_run(self):
        if self.historical_toolchain:
            self.status.setText(
                "This batch retains its approved toolchain. Choose New batch with current software to plan a separate batch."
            )
            return
        if not self.study:
            self.status.setText("Discover or open a study first.")
            return
        try:
            workflow = read_json(self.workflow.toPlainText())
            output = Path(self.output.text())
            limits = dict(
                max_subjects=self.max_subjects.value(),
                max_processes=self.max_processes.value(),
                cpu_threads=self.threads.value(),
                memory_bytes=self.memory.value() * GIB,
            )
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        study = self.study
        selected_keys = self.guided_selection
        fsl_directory = self.fsl_directory
        ants_directory = self.ants_directory
        self._pending_plan_generation = self._plan_generation
        directory = (
            Path(self.mrtrix_directory.text())
            if self.mrtrix_directory.text().strip()
            else self.tool_directory
        )
        self.start(
            "plan",
            lambda cancel, progress: build_batch_plan(
                study,
                workflow,
                output,
                limits=limits,
                selected_keys=selected_keys,
                tool_directory=directory,
                fsl_directory=fsl_directory,
                ants_directory=ants_directory,
                cancelled=cancel,
                progress=progress,
            ),
        )

    def execute(self):
        if not self.plan or not self.approval.isChecked() or not self.processing_available():
            self.status.setText(
                "Review and approve the current plan; other scientific processing must be idle."
            )
            return
        try:
            self.folder = create_batch(self.plan, self.plan.digest)
            self.remember_batch()
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
            return
        self.start(
            "run",
            lambda cancel, progress: BatchRunner(
                self.folder, cancelled=cancel, progress=progress
            ).run(),
        )

    def resume(self):
        if not self.folder or self.thread or not self.processing_available():
            return
        control(self.folder, "resume")
        self.start(
            "run",
            lambda cancel, progress: BatchRunner(
                self.folder, cancelled=cancel, progress=progress
            ).run(),
        )

    def selected_keys(self):
        if hasattr(self, "guided") and self.display_stack.currentIndex() == 0:
            guided = self.guided.selected_stage_keys()
            if guided:
                return guided
        return [
            self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
            for index in self.table.selectionModel().selectedRows()
        ]

    def send_control(self, action, selected=False):
        if not self.folder:
            return
        keys = self.selected_keys() if selected else []
        if selected and not keys:
            self.status.setText("Select one or more acquisition stages first.")
            return
        try:
            if action == "cancel" and not selected and self.thread and self.kind == "run":
                self.cancel.set()
            control(self.folder, action, keys)
            self.status.setText(
                f"{action.capitalize()} requested; active process status is recorded in the journal."
            )
            if not self.thread and action in {"retry", "cancel", "pause"}:
                self.start(
                    "run",
                    lambda cancel, progress: BatchRunner(
                        self.folder, cancelled=cancel, progress=progress
                    ).run(),
                )
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def start(self, kind, operation):
        if self.thread:
            return
        self.kind = kind
        self.cancel = Event()
        self.thread = QThread(self)
        worker = ProcessingWorker(0, lambda: None)
        self.worker = worker
        worker.task = lambda: operation(self.cancel.is_set, worker.progress.emit)
        worker.moveToThread(self.thread)
        self.thread.started.connect(worker.run)
        worker.progress.connect(self.logs.appendPlainText)
        worker.result.connect(self.result)
        worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.thread.finished.connect(worker.deleteLater)
        self.thread.finished.connect(self.finished)
        self.status.setText(
            "Running approved batch…" if kind == "run" else "Inspecting inputs and metadata…"
        )
        self.busy_changed.emit(True)
        self.update_actions()
        self._update_polling()
        self.thread.start()

    def result(self, _, result, error):
        if error:
            if self.kind == "discover":
                self.logs.appendPlainText("Discovery detail: " + error)
            message = (
                "This folder is not a BIDS root. Choose the folder containing "
                "dataset_description.json, or use Custom folder mapping (JSON)."
                if self.kind == "discover"
                and self.method.currentIndex() == 0
                and "dataset_description.json" in error
                and "No such file or directory" in error
                else error
            )
            self.status.setText(message)
            if hasattr(self, "guided"):
                self.guided.operation_error(message)
            return
        if self.kind in {"discover", "study"}:
            if (
                self.kind == "discover"
                and self._pending_discovery_generation is not None
                and self._pending_discovery_generation != self.guided._data_generation
            ):
                self.status.setText(
                    "Dataset choice changed during discovery. Discover the current folder again."
                )
                self.guided.operation_error(self.status.text())
                self._pending_discovery_generation = None
                return
            self._pending_discovery_generation = None
            saved_study_path = None
            if self.kind == "study" and isinstance(result, tuple):
                result, saved_study_path = result
            self.historical_toolchain = False
            self.historical_plan = None
            self.mrtrix_directory.setReadOnly(False)
            self.study, self.plan, self.folder = result, None, None
            self.details.clear()
            self.products = []
            self.inspected_handoff = {}
            self.products_list.clear()
            self.preview.clear()
            self.approval.setChecked(False)
            self.rows = [
                dict(
                    key=a.key,
                    acquisition=a.to_dict(),
                    node={"id": "not planned"},
                    status="REQUIRES_REVIEW" if a.findings else "DISCOVERED",
                    qc_status="NOT_ASSESSED",
                    issues=a.findings,
                )
                for a in result.acquisitions
            ]
            self.render()
            if hasattr(self, "guided"):
                self.guided.study_loaded(result)
            if saved_study_path is not None:
                self.remember_study(saved_study_path)
            self.status.setText(
                f"{len(result.acquisitions)} acquisitions discovered. Scientific eligibility has not yet been assessed."
            )
        elif self.kind == "history":
            folder, saved = result
            self.historical_toolchain = True
            self.historical_plan = saved
            data = saved.to_dict()
            self.study = Study.from_dict(data["study"])
            self.study_name.setText(self.study.study_id)
            self.source_root.setText(self.study.root)
            self.output.setText(str(folder))
            self.workflow.setPlainText(json.dumps(data["workflow"], indent=2))
            self.mrtrix_directory.setText(data.get("tool_directory") or "")
            self.mrtrix_directory.setReadOnly(True)
            for widget, name in (
                (self.max_subjects, "max_subjects"),
                (self.max_processes, "max_processes"),
                (self.threads, "cpu_threads"),
            ):
                widget.setValue(data["limits"][name])
            self.memory.setValue(max(1, data["limits"]["memory_bytes"] // GIB))
            self.folder, self.plan = folder, None
            self.products = []
            self.inspected_handoff = {}
            self.products_list.clear()
            self.details.clear()
            self.approval.setChecked(False)
            self.preview.setPlainText(self.describe_plan(saved))
            self.tabs.setCurrentIndex(1)
            self.remember_batch()
            self.refresh()
            self.status.setText(
                "Saved batch reopened with its pinned approved toolchain. Resume verifies identities before reuse."
            )
            if hasattr(self, "guided"):
                self.guided.history_loaded()

        elif self.kind == "plan":
            if self._pending_plan_generation != self._plan_generation:
                self.status.setText(
                    "Configuration changed while planning. Validate the current choices again."
                )
                if hasattr(self, "guided"):
                    self.guided.operation_error(self.status.text())
                return
            self.plan = result
            self.folder = None
            self.products = []
            self.inspected_handoff = {}
            self.products_list.clear()
            self.rows = result.to_dict()["rows"]
            self.preview.setPlainText(self.describe_plan(result))
            self.approval.setChecked(False)
            self.tabs.setCurrentIndex(1)
            self.render()
            self.status.setText(
                "Dry run finished. Review eligible, blocked and deferred stages before approval."
            )
            if hasattr(self, "guided"):
                self.guided.plan_loaded(result)
        elif self.kind == "registration-qc":
            states = {r["key"]: r for r in result}
            for row in self.rows:
                if row["key"] in states:
                    row["qc_status"] = states[row["key"]]["review"]
                    self.registration_reviews[row["key"]] = (
                        row.get("manifest_sha256"),
                        row["qc_status"],
                    )
            self.render()
            self.details.setPlainText(json.dumps(result, indent=2))
            self.status.setText(
                "Registration evidence reverified. Human review remains independent of execution."
            )
        elif self.kind == "inspect":
            self.products = result["products"]
            self.inspected_handoff = result.get("handoff", {})
            self.products_list.clear()
            self.products_list.addItems([self.product_label(p) for p in self.products])
            self.details.setPlainText(json.dumps(result, indent=2))
            self.status.setText("Result hashes verified; scientific acceptance remains unassigned.")
            if hasattr(self, "guided"):
                self.guided.inspected()
        elif self.kind == "export":
            self.status.setText("Report saved: " + str(result))
        elif self.kind == "review":
            self.refresh()
            self.status.setText("Scientific review recorded separately from execution completion.")
            if hasattr(self, "guided"):
                self.guided.set_stage(6)
        else:
            self.refresh()
            self.status.setText(
                "Batch state: " + result.get("state", "loaded") + ". Scientific QC requires review."
            )
            if hasattr(self, "guided"):
                self.guided.execution_loaded()

    def new_batch(self):
        """Leave the historical approval intact and start a distinct configuration."""
        if self.thread is not None:
            return
        self.historical_toolchain = False
        self.historical_plan = None
        self.folder = None
        self.products = []
        self.inspected_handoff = {}
        self.products_list.clear()
        self._journal_signature = None
        self._update_polling()
        self.output.clear()
        self.mrtrix_directory.setReadOnly(False)
        if self.settings:
            software = ScientificSoftwareSettings(self.settings)
            self.tool_directory = software.location("MRtrix3")
            self.fsl_directory = software.location("FSL")
            self.ants_directory = software.location("ANTs")
        self.mrtrix_directory.setText(str(self.tool_directory or ""))
        self.invalidate()
        self.status.setText("New batch uses current saved software; review and approve a new plan.")
        if hasattr(self, "guided"):
            self.guided.new_batch()

    def finished(self):
        thread, worker = self.thread, self.worker
        self.thread = self.worker = None
        if worker is not None:
            # Keep Python wrappers alive until Qt has delivered deferred deletion.
            # Clearing the task breaks its callback cycle and releases plan data.
            worker.task = lambda: None
            self._retired_workers.append((thread, worker))
        if thread:
            thread.deleteLater()
        self.busy_changed.emit(False)
        self.update_actions()
        self._update_polling()

    def update_actions(self):
        if not hasattr(self, "plan_button"):
            return
        idle = self.thread is None
        self.plan_button.setEnabled(
            idle and self.study is not None and not self.historical_toolchain
        )
        self.run_button.setEnabled(idle and self.plan is not None and self.approval.isChecked())
        self.resume_button.setEnabled(idle and self.folder is not None)
        self.approval.setEnabled(idle and self.plan is not None)

    def refresh(self):
        import shiboken6

        self._retired_workers = [
            (t, w) for t, w in self._retired_workers if shiboken6.isValid(t) or shiboken6.isValid(w)
        ]
        if not self.folder:
            return
        try:
            journal_path = self.folder / "journal.json"
            info = journal_path.stat()
            signature = (
                journal_path,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
            if signature == self._journal_signature:
                return
            journal = read_journal(self.folder)
            self.rows = list(journal["nodes"].values())
            from collections import Counter

            counts = Counter(r["status"] for r in self.rows)
            self.summary.setText(
                journal["state"]
                + " · "
                + " · ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
            )
            self.render()
            self._journal_signature = signature
            if hasattr(self, "guided"):
                self.guided.journal_loaded(journal)
        except (ValueError, OSError):
            return

    def render(self):
        selected = set(self.selected_keys())
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            a = row["acquisition"]
            reviewed = self.registration_reviews.get(row["key"])
            if reviewed and reviewed[0] == row.get("manifest_sha256"):
                row["qc_status"] = reviewed[1]
            values = [
                a["subject_id"],
                a["session_id"] or "—",
                a["acquisition_id"],
                row["node"]["id"],
                row["status"],
                row.get("qc_status", "NOT_ASSESSED"),
                f"{row['elapsed_seconds']:.2f}" if "elapsed_seconds" in row else "—",
                row.get("manifest") or row.get("error") or "; ".join(row.get("issues", [])),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row["key"])
                self.table.setItem(index, column, item)
        self.table.setSortingEnabled(True)
        # Reused row indices can still carry selection after sorting or a
        # journal update. Restore only the selected acquisition identities.
        self.table.clearSelection()
        for index in range(self.table.rowCount()):
            if self.table.item(index, 0).data(Qt.ItemDataRole.UserRole) in selected:
                for col in range(self.table.columnCount()):
                    self.table.item(index, col).setSelected(True)
        self.table.resizeColumnsToContents()
        for column in range(7):
            self.table.setColumnWidth(column, min(180, max(85, self.table.columnWidth(column))))
        self.table.setColumnWidth(7, 320)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.blockSignals(False)
        self.filter_rows()
        self.update_actions()

    def registration_workflow(self):
        from dmri_studio.core.registration_qc import registration_workflow

        choice, ok = QInputDialog.getItem(
            self,
            "Registration batch workflow",
            "Use subject-specific inputs and spatial evidence",
            ["Register T1 and atlas, then QC", "QC existing verified outputs only"],
            0,
            False,
        )
        if ok:
            self.adapter.setCurrentText("registration")
            self.workflow.setPlainText(
                json.dumps(registration_workflow(choice.startswith("QC")), indent=2)
            )
            self.status.setText(
                "Review input associations and per-acquisition spatial evidence before dry run. QC-only requires a verified preprocessed reference_record."
            )

    def registration_qc(self):
        if self.folder:
            from dmri_studio.core.registration_qc import batch_qc_rows

            self.start(
                "registration-qc", lambda cancel, progress: batch_qc_rows(self.folder, cancel)
            )

    def export_registration_qc(self):
        if not self.folder:
            return
        parent = QFileDialog.getExistingDirectory(
            self, "Portable batch registration QC report parent"
        )
        if parent:
            from dmri_studio.core.registration_qc import export_batch_qc

            destination = Path(parent) / ("registration-qc-" + time.strftime("%Y%m%d-%H%M%S"))
            self.start(
                "export", lambda cancel, progress: export_batch_qc(self.folder, destination, cancel)
            )

    def filter_rows(self):
        text = self.search.text().casefold()
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(
                row,
                text
                not in " ".join(
                    self.table.item(row, c).text() for c in range(self.table.columnCount())
                ).casefold()
                or (
                    self.qc_filter.currentIndex() > 0
                    and self.qc_filter.currentText()
                    not in (self.table.item(row, 4).text(), self.table.item(row, 5).text())
                ),
            )

    def show_selected(self):
        keys = set(self.selected_keys())
        self.details.setPlainText(json.dumps([r for r in self.rows if r["key"] in keys], indent=2))

    def inspect_selected(self):
        keys = self.selected_keys()
        if not keys:
            return
        row = next(r for r in self.rows if r["key"] == keys[0])
        if not row.get("manifest"):
            self.status.setText("This stage has no completed result to inspect.")
            return
        path = Path(row["manifest"])
        self.start(
            "inspect",
            lambda cancel, progress: inspect_run(path, cancelled=cancel, progress=progress),
        )

    def open_product(self):
        index = self.products_list.currentRow()
        if 0 <= index < len(self.products):
            product = self.products[index]
            if "paths" not in product and not {"kind", "path"} <= set(product):
                self.status.setText("This verified output has no supported opening route.")
                return
            self.product_requested.emit(product)

    @staticmethod
    def product_label(product):
        if product.get("label"):
            return str(product["label"])
        if product.get("path"):
            return Path(product["path"]).name
        if product.get("paths", {}).get("dwi"):
            return Path(product["paths"]["dwi"]).name
        return "Verified output record"

    def show_qc(self):
        if self.folder:
            self.details.setPlainText(json.dumps(study_qc(self.folder), indent=2))

    def open_batch(self):
        name = QFileDialog.getExistingDirectory(self, "Open saved batch directory")
        if name:
            self.load_batch(Path(name))

    def load_batch(self, folder):
        def operation(cancel, progress):
            approved = read_json((folder / "approved-plan.json").read_text())
            saved = ExecutionPlan(approved["plan"])
            if saved.digest != approved["digest"]:
                raise ValueError("Saved batch approval digest does not match its plan.")
            read_journal(folder)
            return folder, saved

        self.start("history", operation)

    def export(self):
        if not self.folder:
            return
        name = QFileDialog.getExistingDirectory(self, "Select report parent")
        if name:
            destination = Path(name) / ("batch-report-" + time.strftime("%Y%m%d-%H%M%S"))
            self.start("export", lambda cancel, progress: export_report(self.folder, destination))

    def save_study(self):
        if self.study:
            name, _ = QFileDialog.getSaveFileName(self, "Save study", "", "JSON (*.json)")
            if name:

                def save():
                    _publish_json(Path(name), self.study.to_dict())
                    self.remember_study(Path(name))

                self.safe(save)

    def open_study(self):
        name, _ = QFileDialog.getOpenFileName(self, "Open study", "", "JSON (*.json)")
        if name:
            self.open_study_path(Path(name))

    def open_study_path(self, path):
        self.start(
            "study",
            lambda cancel, progress: (
                Study.from_dict(read_json(Path(path).read_text())),
                Path(path),
            ),
        )

    def remember_study(self, path):
        if not self.settings:
            return
        paths = [str(path)] + [
            old for old in self.settings.get("batch/recent_studies", []) if old != str(path)
        ]
        self.settings.set("batch/recent_studies", paths[:30])
        try:
            metadata = read_json(self.settings.get("batch/recent_study_metadata", "{}"))
            if not isinstance(metadata, dict):
                metadata = {}
        except ValueError:
            metadata = {}
        if self.study:
            metadata[str(path)] = dict(
                study_id=self.study.study_id,
                root=self.study.root,
                acquisitions=len(self.study.acquisitions),
            )
        self.settings.set(
            "batch/recent_study_metadata",
            json.dumps({p: metadata.get(p, {}) for p in paths[:30]}),
        )
        self.settings.store.sync()
        if hasattr(self, "guided"):
            self.guided.refresh_recent_studies()

    def load_workflow(self):
        name, _ = QFileDialog.getOpenFileName(self, "Open workflow", "", "JSON (*.json)")
        if name:
            loaded = []

            def apply():
                self.workflow.setPlainText(json.dumps(read_json(Path(name).read_text()), indent=2))
                loaded.append(True)

            self.safe(apply)
            return bool(loaded)
        return False

    def save_workflow(self):
        name, _ = QFileDialog.getSaveFileName(self, "Save workflow", "", "JSON (*.json)")
        if name:
            self.safe(lambda: _publish_json(Path(name), read_json(self.workflow.toPlainText())))

    def safe(self, callback):
        try:
            callback()
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))

    def shutdown(self):
        if self.thread:
            self.cancel.set()
            return False
        return True

    def review_selected(self):
        from dmri_studio.core.batch.engine import record_review

        keys = self.selected_keys()
        if not self.folder or len(keys) != 1:
            self.status.setText(
                "Select one completed stage to record an explicit scientific review."
            )
            return
        decision, ok = QInputDialog.getItem(
            self,
            "Scientific review",
            "Decision (separate from processing completion)",
            ["requires_review", "rejected", "accepted"],
            0,
            False,
        )
        if not ok:
            return
        reviewer, ok = QInputDialog.getText(self, "Scientific review", "Reviewer name")
        if not ok:
            return
        note, ok = QInputDialog.getMultiLineText(
            self, "Scientific review", "Scientific rationale and limitations"
        )
        if ok:
            folder, key = self.folder, keys[0]
            self.start(
                "review",
                lambda cancel, progress: record_review(folder, key, decision, reviewer, note),
            )

    def remember_batch(self):
        if self.folder is None:
            return
        paths = [str(self.folder)] + [
            self.history.itemData(i)
            for i in range(1, self.history.count())
            if self.history.itemData(i) != str(self.folder)
        ]
        self.history.clear()
        self.history.addItem("Recent batches…", None)
        for path in paths[:30]:
            self.history.addItem(Path(path).name, path)
        if self.settings:
            self.settings.set("batch/history", paths[:30])
            self.settings.store.sync()
        if hasattr(self, "guided"):
            self.guided.refresh_recent_batches()

    def open_recent(self, index):
        path = self.history.itemData(index)
        if path and not self.thread:
            self.load_batch(Path(path))

    def save_plan(self):
        if self.plan is not None:
            name, _ = QFileDialog.getSaveFileName(
                self, "Export exact reviewed batch plan", "", "JSON (*.json)"
            )
            if name:
                self.safe(lambda: _publish_json(Path(name), self.plan.to_dict()))

    @staticmethod
    def describe_plan(plan):
        data = plan.to_dict()
        lines = [
            f"Study: {data['study']['study_id']}",
            f"Selected acquisitions: {len(data.get('selected_acquisition_keys', data['study']['acquisitions']))}",
            f"Output: {data['output_directory']}",
            "MRtrix3: " + str(data.get("tool_directory") or "not selected"),
            "FSL: "
            + (
                str(data.get("fsl_directory") or "not selected")
                if "fsl_directory" in data
                else "legacy plan; inspect recorded tool hashes"
            ),
            "ANTs: "
            + (
                str(data.get("ants_directory") or "not selected")
                if "ants_directory" in data
                else "legacy plan; inspect recorded tool hashes"
            ),
            "Resources: " + json.dumps(data["limits"]),
            "Scientific acceptance: unassigned",
            "",
        ]
        for row in data["rows"]:
            lines.append(row["key"] + " — " + row["status"])
            lines.extend("  " + issue for issue in row["issues"])
            lines.append("  Reservation: " + json.dumps(row["resources"]))
            for task in row["node"].get("tasks", []):
                lines.append("  " + task["algorithm"] + " " + json.dumps(task["parameters"]))
            if row.get("manifest"):
                lines.append("  Verified reuse: " + row["manifest"])
            for artifact in row.get("preview", {}).get("artifacts", []):
                lines.append("  Input " + artifact["kind"] + ": " + artifact["path"])
            for command in row.get("preview", {}).get("commands", []):
                lines.append("  " + command.get("display", str(command.get("argv", ""))))
            if row["status"] == "WAITING_DEPENDENCY":
                lines.append(
                    "  Deferred: real producer outputs must pass typed import and scientific validation before execution."
                )
            lines.append("")
        lines.extend(
            [
                "Approval digest: " + plan.digest,
                "Export exact plan for all source hashes, tool versions, declarations and command records.",
            ]
        )
        return "\n".join(lines)
