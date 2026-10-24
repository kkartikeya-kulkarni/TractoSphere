"""Progressive batch controls over the existing approved batch services.

The BatchPage owns the study, workflow text, plan and runner. This widget only
collects draft choices and presents their already validated results.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.batch.discovery import Study
from dmri_studio.core.scientific.records import read_json
from dmri_studio.core.workflow import ProcessingHistory

from .components import button, label

STAGES = (
    "Select Data",
    "Review Subjects",
    "Choose Workflow",
    "Configure",
    "Review Plan",
    "Run & Monitor",
    "Results & QC",
)


def explain_issue(issue: str) -> str:
    """Add a next action while preserving the exact scientific diagnostic."""
    lower = issue.casefold()
    if "mrtrix" in lower or "mrconvert" in lower or "tensor2metric" in lower:
        return (
            "Check the saved MRtrix3 installation in Scientific Software settings. Detail: " + issue
        )
    if "fsl" in lower or "flirt" in lower or "eddy" in lower:
        return "Check the saved FSL installation in Scientific Software settings. Detail: " + issue
    if "ants" in lower or "n4" in lower:
        return "Check the saved ANTs installation in Scientific Software settings. Detail: " + issue
    if "missing" in lower or "not a readable file" in lower:
        return "Review the selected acquisition's source files and metadata. Detail: " + issue
    return issue


class GuidedBatch(QWidget):
    """Seven visible steps backed by one BatchPage state and approval digest."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._study_identity = None
        self._selected = set()
        self._syncing = False
        self._data_dirty = False
        self._data_generation = 0
        self._saved_workflow_ready = False
        root = QVBoxLayout(self)
        root.addWidget(label("Process Multiple Subjects", "title"))
        root.addWidget(
            label(
                "Select a dataset, choose the subjects and processing workflow, then review the plan before running.",
                "caption",
            )
        )
        self.progress = label("1 of 7 · Select Data", "caption")
        root.addWidget(self.progress)
        self.pages = QStackedWidget()
        for factory in (
            self._data_page,
            self._subjects_page,
            self._workflow_page,
            self._configure_page,
            self._plan_page,
            self._monitor_page,
            self._results_page,
        ):
            content = factory()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            self.pages.addWidget(scroll)
        root.addWidget(self.pages, 1)
        nav = QHBoxLayout()
        self.back = button("Back", self.previous)
        self.next = button("Continue", self.advance)
        nav.addWidget(self.back)
        nav.addStretch()
        nav.addWidget(self.next)
        root.addLayout(nav)
        self.notice = label("Select a dataset to begin. No processing has started.", "caption")
        root.addWidget(self.notice)
        self.owner.approval.toggled.connect(self._approval_from_advanced)
        self.owner.workflow.textChanged.connect(self._workflow_from_advanced)
        self.owner.output.textChanged.connect(self._output_from_advanced)
        self.set_stage(0)

    def _page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        return page, layout

    def _data_page(self):
        page, layout = self._page()
        layout.addWidget(label("Select Data", "section"))
        self.source_kind = QComboBox()
        self.source_kind.addItem("Open a BIDS dataset", "bids")
        self.source_kind.addItem("Use another supported dataset structure", "custom")
        self.source_kind.addItem("Import a CSV/TSV manifest", "manifest")
        self.source_kind.addItem("Open a saved study or batch", "saved")
        self.source_kind.setAccessibleName("Dataset source type")
        layout.addWidget(self.source_kind)
        form = QFormLayout()
        self.data_form = form
        self.dataset_root = QLineEdit()
        self.dataset_root.setPlaceholderText("Choose the authorized dataset folder")
        self.dataset_root.setAccessibleName("Dataset folder")
        self.study_id = QLineEdit("study-001")
        self.study_id.setAccessibleName("Study name")
        self.source_file = QLineEdit()
        self.source_file.setAccessibleName("Mapping or manifest file")
        form.addRow("Dataset folder", self._picker(self.dataset_root, file=False))
        form.addRow("Study name", self.study_id)
        self.file_row = self._picker(self.source_file, file=True)
        form.addRow("Mapping / manifest file", self.file_row)
        layout.addLayout(form)
        self.source_help = label("Choose a BIDS root containing diffusion acquisitions.", "caption")
        layout.addWidget(self.source_help)
        self.discover_button = button("Discover Acquisitions", self.discover)
        layout.addWidget(self.discover_button)
        self.open_study_button = button("Open Saved Study…", self.owner.open_study)
        self.open_batch_button = button("Inspect / Resume Previous Batch…", self.owner.open_batch)
        layout.addWidget(self.open_study_button)
        self.recent_studies = QComboBox()
        self.recent_studies.setAccessibleName("Recent saved studies")
        self.recent_studies.activated.connect(self._open_recent_study)
        layout.addWidget(self.recent_studies)
        self.refresh_recent_studies()
        layout.addWidget(self.open_batch_button)
        self.recent = QComboBox()
        self.recent.activated.connect(self._open_recent)
        layout.addWidget(self.recent)
        self.refresh_recent_batches()
        self.source_kind.currentIndexChanged.connect(self._source_mode)
        self.source_kind.currentIndexChanged.connect(self._mark_data_dirty)
        for field in (self.dataset_root, self.study_id, self.source_file):
            field.textChanged.connect(self._mark_data_dirty)
        self._source_mode()
        layout.addStretch()
        return page

    def _picker(self, field, *, file):
        row = QWidget()
        box = QHBoxLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(field)

        def choose():
            if file:
                path, _ = QFileDialog.getOpenFileName(self, "Choose mapping or manifest")
            else:
                path = QFileDialog.getExistingDirectory(self, "Choose dataset folder")
            if path:
                field.setText(path)

        box.addWidget(button("Browse…", choose))
        return row

    def _source_mode(self):
        mode = self.source_kind.currentData()
        self.data_form.setRowVisible(self.file_row, mode in {"custom", "manifest"})
        self.dataset_root.setEnabled(mode != "saved")
        self.study_id.setEnabled(mode != "saved")
        self.discover_button.setVisible(mode != "saved")
        self.open_study_button.setVisible(mode == "saved")
        self.recent_studies.setVisible(mode == "saved")
        self.open_batch_button.setVisible(mode == "saved")
        self.recent.setVisible(mode == "saved")
        self.source_help.setText(
            {
                "bids": "Choose a BIDS root. Discovery reports missing inputs; it does not certify scientific eligibility.",
                "custom": "Choose the dataset root and an explicit supported JSON mapping. Ambiguous files are not guessed.",
                "manifest": "Choose the dataset root and a CSV/TSV manifest with explicit acquisition paths.",
                "saved": "Open a study to make a new plan, or inspect and resume a previously approved batch.",
            }[mode]
        )

    def _mark_data_dirty(self):
        self._data_dirty = True
        self._data_generation += 1

    def _open_recent(self, index):
        path = self.recent.itemData(index)
        if path:
            self.owner.load_batch(Path(path))

    def refresh_recent_batches(self):
        self.recent.clear()
        self.recent.addItem("Recent approved batches…", None)
        for i in range(1, self.owner.history.count()):
            self.recent.addItem(self.owner.history.itemText(i), self.owner.history.itemData(i))

    def refresh_recent_studies(self):
        self.recent_studies.clear()
        self.recent_studies.addItem("Recent saved studies…", None)
        if not self.owner.settings:
            return
        try:
            metadata = read_json(self.owner.settings.get("batch/recent_study_metadata", "{}"))
            if not isinstance(metadata, dict):
                metadata = {}
        except ValueError:
            metadata = {}
        for path in self.owner.settings.get("batch/recent_studies", []):
            if not Path(path).is_file():
                continue
            info = metadata.get(path, {})
            self.recent_studies.addItem(
                f"{info.get('study_id') or Path(path).stem} · "
                f"{info.get('acquisitions', '?')} acquisitions · "
                f"{info.get('root', 'open to inspect dataset')}",
                path,
            )

    def _open_recent_study(self, index):
        path = self.recent_studies.itemData(index)
        if path:
            self.owner.open_study_path(Path(path))

    def discover(self):
        if self.owner.thread is not None:
            self.notice.setText("Wait for the current discovery or plan to finish.")
            return
        mode = self.source_kind.currentData()
        root = Path(self.dataset_root.text().strip()).expanduser()
        if not root.is_dir():
            self.notice.setText("Choose an existing dataset folder before discovery.")
            return
        if mode in {"custom", "manifest"} and not Path(self.source_file.text()).is_file():
            self.notice.setText("Choose an existing mapping or manifest file.")
            return
        self.owner.method.setCurrentIndex({"bids": 0, "custom": 1, "manifest": 2}[mode])
        self.owner.source_root.setText(str(root))
        self.owner.study_name.setText(self.study_id.text().strip())
        self.owner.import_file.setText(self.source_file.text().strip())
        self.owner._pending_discovery_generation = self._data_generation
        self.notice.setText("Discovering acquisitions and reading metadata…")
        self.owner.discover()

    def _subjects_page(self):
        page, layout = self._page()
        layout.addWidget(label("Review Subjects", "section"))
        layout.addWidget(
            label(
                "Discovery findings are separate from scientific workflow eligibility.", "caption"
            )
        )
        toolbar = QHBoxLayout()
        self.subject_search = QLineEdit()
        self.subject_search.setPlaceholderText("Search subject, session or acquisition")
        self.subject_search.setAccessibleName("Search discovered acquisitions")
        self.subject_search.textChanged.connect(self._filter_subjects)
        toolbar.addWidget(self.subject_search)
        self.subject_filter = QComboBox()
        self.subject_filter.addItems(["All discovery states", "Discovered", "Needs input review"])
        self.subject_filter.currentIndexChanged.connect(self._filter_subjects)
        toolbar.addWidget(self.subject_filter)
        layout.addLayout(toolbar)
        controls = QHBoxLayout()
        controls.addWidget(button("Select visible discovered", self.select_visible))
        controls.addWidget(button("Deselect all", self.deselect_all))
        self.selected_count = label("No acquisitions selected", "caption")
        controls.addWidget(self.selected_count)
        layout.addLayout(controls)
        self.subject_table = QTableWidget(0, 5)
        self.subject_table.setHorizontalHeaderLabels(
            ["Select", "Subject", "Session", "Acquisition", "Discovery status"]
        )
        self.subject_table.setAccessibleName("Discovered acquisitions")
        self.subject_table.setSortingEnabled(True)
        self.subject_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.subject_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.subject_table.horizontalHeader().setStretchLastSection(True)
        self.subject_table.itemChanged.connect(self._selection_changed)
        self.subject_table.itemSelectionChanged.connect(self._subject_details)
        layout.addWidget(self.subject_table, 1)
        self.subject_details = QPlainTextEdit()
        self.subject_details.setReadOnly(True)
        self.subject_details.setPlaceholderText(
            "Select an acquisition to inspect its discovery findings."
        )
        self.subject_details.setMaximumHeight(100)
        self.subject_details.setAccessibleName("Acquisition discovery findings")
        layout.addWidget(self.subject_details)
        return page

    def study_loaded(self, study):
        for field, value in ((self.dataset_root, study.root), (self.study_id, study.study_id)):
            field.blockSignals(True)
            field.setText(value)
            field.blockSignals(False)
        self._data_dirty = False
        identity = (study.study_id, study.root, tuple(a.key for a in study.acquisitions))
        if identity != self._study_identity:
            self._selected = {a.key for a in study.acquisitions if not a.findings}
            self._saved_workflow_ready = False
        self._study_identity = identity
        self._syncing = True
        self.subject_table.setSortingEnabled(False)
        self.subject_table.setRowCount(len(study.acquisitions))
        for row, acquisition in enumerate(study.acquisitions):
            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            check.setData(Qt.ItemDataRole.UserRole, acquisition.key)
            check.setCheckState(
                Qt.CheckState.Checked
                if acquisition.key in self._selected
                else Qt.CheckState.Unchecked
            )
            values = [
                check,
                QTableWidgetItem(acquisition.subject_id),
                QTableWidgetItem(acquisition.session_id or "—"),
                QTableWidgetItem(acquisition.acquisition_id),
                QTableWidgetItem("Needs input review" if acquisition.findings else "Discovered"),
            ]
            for column, item in enumerate(values):
                self.subject_table.setItem(row, column, item)
        self.subject_table.setSortingEnabled(True)
        self._syncing = False
        self._selection_state()
        self._filter_subjects()
        self.notice.setText(
            f"{len(study.acquisitions)} acquisitions found. Review individual inputs before planning."
        )
        self.set_stage(1)

    def _selection_changed(self, item):
        if self._syncing or item.column() != 0:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._selected.add(key)
        else:
            self._selected.discard(key)
        self._selection_state()

    def _selection_state(self):
        self.owner.guided_selection = tuple(sorted(self._selected))
        self.selected_count.setText(
            f"{len(self._selected)} of {self.subject_table.rowCount()} selected"
        )
        self.owner.invalidate()
        if self.owner.study:
            self._refresh_workflow_choices()

    def _filter_subjects(self):
        query = self.subject_search.text().casefold()
        status = self.subject_filter.currentText()
        for row in range(self.subject_table.rowCount()):
            text = " ".join(
                self.subject_table.item(row, col).text() for col in range(1, 4)
            ).casefold()
            state = self.subject_table.item(row, 4).text()
            self.subject_table.setRowHidden(
                row,
                query not in text or (status != "All discovery states" and state != status),
            )

    def select_visible(self):
        self._syncing = True
        for row in range(self.subject_table.rowCount()):
            if (
                not self.subject_table.isRowHidden(row)
                and self.subject_table.item(row, 4).text() == "Discovered"
            ):
                item = self.subject_table.item(row, 0)
                item.setCheckState(Qt.CheckState.Checked)
                self._selected.add(item.data(Qt.ItemDataRole.UserRole))
        self._syncing = False
        self._selection_state()

    def deselect_all(self):
        self._syncing = True
        for row in range(self.subject_table.rowCount()):
            self.subject_table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        self._selected.clear()
        self._syncing = False
        self._selection_state()

    def _subject_details(self):
        rows = self.subject_table.selectionModel().selectedRows()
        if not rows or not self.owner.study:
            return
        key = self.subject_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        acquisition = next((a for a in self.owner.study.acquisitions if a.key == key), None)
        if acquisition:
            self.subject_details.setPlainText(
                f"{key}\n"
                + (
                    "\n".join(acquisition.findings)
                    if acquisition.findings
                    else "No discovery findings. Scientific eligibility is checked when planning."
                )
            )

    def _workflow_page(self):
        page, layout = self._page()
        layout.addWidget(label("What would you like to process?", "section"))
        self.workflow_choice = QComboBox()
        self.workflow_choice.setAccessibleName("Batch scientific workflow")
        self.workflow_choice.currentIndexChanged.connect(self._choose_workflow)
        layout.addWidget(self.workflow_choice)
        self.workflow_description = label(
            "Choose a supported operation for these inputs.", "caption"
        )
        self.workflow_description.setWordWrap(True)
        layout.addWidget(self.workflow_description)
        self.history_confirm = QCheckBox(
            "I reviewed the source DWI processing history; no completed corrections are being claimed here"
        )
        self.history_confirm.toggled.connect(self._choose_workflow)
        layout.addWidget(self.history_confirm)
        layout.addWidget(button("Load Saved Workflow…", self._load_workflow))
        layout.addWidget(button("Inspect / Edit Advanced Workflow", self.owner.toggle_mode))
        layout.addStretch()
        return page

    def _refresh_workflow_choices(self):
        if not self.owner.study:
            return
        roles = {
            role
            for a in self.owner.study.acquisitions
            if a.key in self._selected
            for role in a.paths
        }
        current = self.workflow_choice.currentData()
        self.workflow_choice.blockSignals(True)
        self.workflow_choice.clear()
        if "dwi" in roles:
            self.workflow_choice.addItem("Convert validated DWI to MRtrix", "convert")
        if "tensor" in roles:
            self.workflow_choice.addItem(
                "Calculate diffusion tensor metrics (FA, MD, AD, RD)", "tensor"
            )
        self.workflow_choice.addItem("Use a saved or advanced workflow", "saved")
        index = self.workflow_choice.findData(current)
        self.workflow_choice.setCurrentIndex(max(0, index))
        self.workflow_choice.blockSignals(False)
        self._choose_workflow()

    def _choose_workflow(self):
        if self._syncing:
            return
        choice = self.workflow_choice.currentData()
        self.history_confirm.setVisible(choice == "convert")
        explanations = {
            "convert": "Requires a validated 4D DWI and matching gradients. Confirm the source history before planning; the scientific adapter checks geometry, metadata and MRtrix3.",
            "tensor": "Requires a compatible tensor image for each acquisition. Produces FA, MD, AD and RD maps after the DTI adapter validates inputs and MRtrix3.",
            "saved": "Load a previously reviewed workflow or configure one under Advanced Settings. The same planner validates its exact definition.",
        }
        self.workflow_description.setText(
            explanations.get(choice, "No supported workflow is available for these inputs.")
        )
        if choice in {"tensor", "convert"} and self.owner.study:
            required = ("tensor",) if choice == "tensor" else ("dwi", "bval", "bvec")
            missing = [
                a.key
                for a in self.owner.study.acquisitions
                if a.key in self._selected and not all(role in a.paths for role in required)
            ]
            if missing:
                self.workflow_description.setText(
                    self.workflow_description.text()
                    + f" {len(missing)} selected acquisition(s) lack required discovered inputs and will be blocked during planning."
                )
        if choice == "tensor":
            self._syncing = True
            self.owner.tensor_workflow()
        elif choice == "convert":
            self._syncing = True
            self.owner.workflow.setPlainText(
                json.dumps(
                    {
                        "nodes": [
                            {
                                "id": "convert",
                                "adapter": "preprocessing",
                                "imports": {
                                    "dwi": {
                                        "role": "dwi",
                                        "kind": "dwi",
                                        "bval_role": "bval",
                                        "bvec_role": "bvec",
                                    }
                                },
                                "preprocessing": {
                                    "operations": ["convert"],
                                    "history": ProcessingHistory(
                                        confirmed=self.history_confirm.isChecked()
                                    ).to_dict(),
                                },
                            }
                        ]
                    },
                    indent=2,
                )
            )
        self._syncing = False

    def _load_workflow(self):
        if self.owner.load_workflow():
            self._saved_workflow_ready = True
            index = self.workflow_choice.findData("saved")
            self.workflow_choice.setCurrentIndex(index)

    def _workflow_from_advanced(self):
        if not self._syncing and self.workflow_choice.currentData() != "saved":
            # The advanced editor remains authoritative. Do not replace its edits
            # when the user returns to the guided review.
            self._syncing = True
            self.workflow_choice.setCurrentIndex(self.workflow_choice.findData("saved"))
            self._syncing = False
            self._saved_workflow_ready = True
        elif not self._syncing:
            self._saved_workflow_ready = True

    def _configure_page(self):
        page, layout = self._page()
        layout.addWidget(label("Configure", "section"))
        self.configuration_summary = label("Choose subjects and a workflow first.", "caption")
        layout.addWidget(self.configuration_summary)
        form = QFormLayout()
        self.output = QLineEdit()
        self.output.setAccessibleName("New batch output folder")
        self.output.setPlaceholderText("Choose a new derivatives folder")
        self.output.textChanged.connect(self._output_to_advanced)
        form.addRow("New output folder", self._output_picker())
        layout.addLayout(form)
        self.software = label(
            "Scientific software is restored from application settings.", "caption"
        )
        self.software.setWordWrap(True)
        layout.addWidget(self.software)
        self.advanced_resources = QCheckBox("Advanced resource limits")
        self.advanced_resources.toggled.connect(self._resource_visibility)
        layout.addWidget(self.advanced_resources)
        self.resource_form = QWidget()
        resources = QFormLayout(self.resource_form)
        for name, widget in (
            ("Concurrent subjects", self.owner.max_subjects),
            ("Concurrent processes", self.owner.max_processes),
            ("CPU thread budget", self.owner.threads),
            ("Memory budget (GiB)", self.owner.memory),
        ):
            # Keep one actual widget instance; the legacy form owns the controls,
            # so guided adjustments use proxy spin boxes below.
            from PySide6.QtWidgets import QSpinBox

            proxy = QSpinBox()
            proxy.setRange(widget.minimum(), widget.maximum())
            proxy.setValue(widget.value())
            proxy.setAccessibleName(name)
            proxy.valueChanged.connect(widget.setValue)
            widget.valueChanged.connect(proxy.setValue)
            resources.addRow(name, proxy)
        self.resource_form.hide()
        layout.addWidget(self.resource_form)
        self.validate_button = button("Validate Processing Plan", self._validate)
        layout.addWidget(self.validate_button)
        layout.addStretch()
        return page

    def _resource_visibility(self, checked):
        self.resource_form.setVisible(checked)

    def _output_picker(self):
        row = QWidget()
        box = QHBoxLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.output)

        def choose():
            self.owner.choose_path(self.owner.output)

        box.addWidget(button("Browse…", choose))
        return row

    def _output_to_advanced(self, value):
        if self.owner.output.text() != value:
            self.owner.output.setText(value)

    def _output_from_advanced(self, value):
        if self.output.text() != value:
            self.output.setText(value)

    def _validate(self):
        if not self._selected:
            self.notice.setText("Select at least one acquisition before validating.")
            return
        if not self.output.text().strip():
            self.notice.setText("Choose a new batch output folder before validating.")
            return
        if self.workflow_choice.currentData() == "convert" and not self.history_confirm.isChecked():
            self.notice.setText("Review and confirm source processing history before conversion.")
            return
        if self.workflow_choice.currentData() == "saved" and not self._saved_workflow_ready:
            self.notice.setText(
                "Load a saved workflow or configure one in Advanced Settings first."
            )
            return
        self.notice.setText("Validating exact inputs, software, resources and commands…")
        self.owner.dry_run()

    def _plan_page(self):
        page, layout = self._page()
        layout.addWidget(label("Review Plan", "section"))
        self.plan_summary = label("Validate the plan to see eligibility and exact work.", "caption")
        self.plan_summary.setWordWrap(True)
        layout.addWidget(self.plan_summary)
        self.blockers = QPlainTextEdit()
        self.blockers.setReadOnly(True)
        self.blockers.setAccessibleName("Blocked or deferred acquisition explanations")
        layout.addWidget(self.blockers, 1)
        self.approve = QCheckBox("I reviewed and approve this exact validated plan")
        self.approve.setAccessibleName("Approve exact batch plan")
        self.approve.toggled.connect(self.owner.approval.setChecked)
        layout.addWidget(self.approve)
        layout.addWidget(button("Inspect Exact Commands and Provenance", self._show_advanced_plan))
        self.run = button("Run Approved Batch", self._run)
        layout.addWidget(self.run)
        return page

    def _show_advanced_plan(self):
        self.owner.toggle_mode()
        self.owner.tabs.setCurrentIndex(1)

    def _approval_from_advanced(self, checked):
        if self.approve.isChecked() != checked:
            self.approve.setChecked(checked)
        self.run.setEnabled(bool(self.owner.plan and checked and self.owner.thread is None))

    def sync_plan_state(self):
        self.approve.setEnabled(self.owner.plan is not None and self.owner.thread is None)
        self.run.setEnabled(
            self.owner.plan is not None
            and self.owner.approval.isChecked()
            and self.owner.thread is None
        )
        if self.owner.plan is None:
            self.plan_summary.setText(
                "Configuration changed. Validate a new exact plan before approval."
            )

    def plan_loaded(self, plan):
        data = plan.to_dict()
        counts = Counter(row["status"] for row in data["rows"])
        selected = data.get("selected_acquisition_keys")
        self.plan_summary.setText(
            f"Study: {data['study']['study_id']}\n"
            f"Selected acquisitions: {len(selected) if selected is not None else len(data['study']['acquisitions'])}\n"
            f"Workflow stages: {len(data['rows'])}\n"
            f"Eligible: {counts['ELIGIBLE']} · Deferred: {counts['WAITING_DEPENDENCY']} · Blocked: {counts['BLOCKED']}\n"
            f"Output: {data['output_directory']}\nScientific acceptance remains unassigned."
        )
        findings = []
        stages = []
        for row in data["rows"]:
            node = row["node"]
            readable = (
                "Convert diffusion data to MRtrix"
                if node.get("adapter") == "preprocessing"
                and node.get("preprocessing", {}).get("operations") == ["convert"]
                else "Calculate diffusion tensor metrics"
                if any(task.get("algorithm") == "tensor2metric" for task in node.get("tasks", []))
                else node["id"].replace("_", " ")
            )
            if readable not in stages:
                stages.append(readable)
            if row["status"] in {"BLOCKED", "WAITING_DEPENDENCY"}:
                findings.append(
                    f"{row['key']} — {row['status']}\n  "
                    + "; ".join(explain_issue(issue) for issue in row.get("issues", []))
                )
        self.plan_summary.setText(self.plan_summary.text() + "\nStages: " + ", ".join(stages))
        self.blockers.setPlainText(
            "\n".join(findings) if findings else "No blocked or deferred stages in this plan."
        )
        self.sync_plan_state()
        self.set_stage(4)

    def _run(self):
        self.owner.execute()
        if self.owner.folder:
            self.set_stage(5)

    def _monitor_page(self):
        page, layout = self._page()
        layout.addWidget(label("Run & Monitor", "section"))
        self.monitor_summary = label(
            "No batch is running. Validated plans require explicit approval.", "caption"
        )
        layout.addWidget(self.monitor_summary)
        self.monitor_table = QTableWidget(0, 6)
        self.monitor_table.setHorizontalHeaderLabels(
            ["Subject", "Session", "Acquisition", "Stage", "Execution", "QC"]
        )
        self.monitor_table.setAccessibleName("Batch execution status by acquisition")
        self.monitor_table.setSortingEnabled(True)
        self.monitor_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.monitor_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.monitor_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.monitor_table, 1)
        controls = QHBoxLayout()
        controls.addWidget(button("Pause Scheduling", lambda: self.owner.send_control("pause")))
        controls.addWidget(button("Resume", self.owner.resume))
        controls.addWidget(
            button("Cancel Selected", lambda: self.owner.send_control("cancel", selected=True))
        )
        controls.addWidget(button("Cancel Batch", lambda: self.owner.send_control("cancel")))
        controls.addWidget(
            button("Retry Selected", lambda: self.owner.send_control("retry", selected=True))
        )
        controls.addWidget(button("Show Logs", self._show_logs))
        layout.addLayout(controls)
        self.monitor_details = QPlainTextEdit()
        self.monitor_details.setReadOnly(True)
        self.monitor_details.setMaximumHeight(100)
        self.monitor_details.setAccessibleName("Selected stage details and errors")
        layout.addWidget(self.monitor_details)
        self.monitor_table.itemSelectionChanged.connect(self._monitor_details)
        return page

    def _monitor_details(self):
        keys = self.selected_stage_keys()
        if keys:
            self.monitor_details.setPlainText(
                json.dumps([row for row in self.owner.rows if row["key"] in keys], indent=2)
            )

    def _show_logs(self):
        self.monitor_details.setPlainText(self.owner.logs.toPlainText())

    def journal_loaded(self, journal):
        rows = list(journal["nodes"].values())
        chosen = set(self.selected_stage_keys())
        counts = Counter(row["status"] for row in rows)
        self.monitor_summary.setText(
            journal["state"]
            + " · "
            + " · ".join(f"{state}: {count}" for state, count in sorted(counts.items()))
            + " · Completion does not imply scientific approval."
        )
        self.monitor_table.blockSignals(True)
        self.monitor_table.setSortingEnabled(False)
        self.monitor_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            a = row["acquisition"]
            values = [
                a["subject_id"],
                a["session_id"] or "—",
                a["acquisition_id"],
                row["node"]["id"],
                row["status"],
                row.get("qc_status", "NOT_ASSESSED"),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row["key"])
                self.monitor_table.setItem(index, column, item)
        self.monitor_table.setSortingEnabled(True)
        self.monitor_table.clearSelection()
        for row in range(self.monitor_table.rowCount()):
            if self.monitor_table.item(row, 0).data(Qt.ItemDataRole.UserRole) in chosen:
                self.monitor_table.selectRow(row)
        self.monitor_table.blockSignals(False)
        self.results_summary.setText(self.monitor_summary.text())
        self.result_stage.blockSignals(True)
        previous = self.result_stage.currentData()
        self.result_stage.clear()
        for row in rows:
            self.result_stage.addItem(f"{row['key']} · {row['status']}", row["key"])
        if previous:
            self.result_stage.setCurrentIndex(max(0, self.result_stage.findData(previous)))
        self.result_stage.blockSignals(False)
        self._result_detail()

    def selected_stage_keys(self):
        if self.pages.currentIndex() == 6 and self.result_stage.currentData():
            return [self.result_stage.currentData()]
        return [
            self.monitor_table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
            for index in self.monitor_table.selectionModel().selectedRows()
        ]

    def _results_page(self):
        page, layout = self._page()
        layout.addWidget(label("Results & QC", "section"))
        self.results_summary = label(
            "No results yet. Scientific review follows execution.", "caption"
        )
        layout.addWidget(self.results_summary)
        self.result_stage = QComboBox()
        self.result_stage.setAccessibleName("Completed or failed batch stage")
        self.result_stage.currentIndexChanged.connect(self._result_detail)
        layout.addWidget(self.result_stage)
        actions = QHBoxLayout()
        actions.addWidget(button("Inspect Verified Output", self.owner.inspect_selected))
        actions.addWidget(button("Study QC", self._show_qc))
        actions.addWidget(button("Export Report", self.owner.export))
        actions.addWidget(button("Record Scientific Review", self.owner.review_selected))
        layout.addLayout(actions)
        history_actions = QHBoxLayout()
        history_actions.addWidget(button("Resume Interrupted Batch", self.owner.resume))
        history_actions.addWidget(button("Registration QC", self.owner.registration_qc))
        history_actions.addWidget(
            button("Export Registration QC", self.owner.export_registration_qc)
        )
        layout.addLayout(history_actions)
        self.results_detail = QPlainTextEdit()
        self.results_detail.setReadOnly(True)
        self.results_detail.setAccessibleName("Result integrity and scientific QC")
        layout.addWidget(self.results_detail, 1)
        self.products = QListWidget()
        self.products.setAccessibleName("Verified batch products")
        layout.addWidget(self.products)
        layout.addWidget(button("Open Selected Product", self._open_product))
        layout.addWidget(button("Open Another Saved Batch…", self.owner.open_batch))
        layout.addWidget(button("New Batch With Current Software", self.owner.new_batch))
        return page

    def _result_detail(self):
        key = self.result_stage.currentData()
        row = next((r for r in self.owner.rows if r["key"] == key), None)
        if row is None:
            self.results_detail.setPlainText(
                "No stage selected. Execution and scientific review are separate."
            )
            return
        lines = [
            f"Stage: {row['key']}",
            f"Execution: {row['status']}",
            f"QC: {row.get('qc_status', 'NOT_ASSESSED')}",
            f"Verified manifest: {row.get('manifest') or 'not available'}",
        ]
        lines.extend(f"Finding: {issue}" for issue in row.get("issues", []))
        if row.get("error"):
            lines.append(f"Error: {row['error']}")
        self.results_detail.setPlainText("\n".join(lines))

    def _show_qc(self):
        if self.owner.folder:
            self.owner.show_qc()
            self.results_detail.setPlainText(self.owner.details.toPlainText())

    def inspected(self):
        self.results_detail.setPlainText(self.owner.details.toPlainText())
        self.products.clear()
        self.products.addItems([p.get("label", Path(p["path"]).name) for p in self.owner.products])

    def _open_product(self):
        self.owner.products_list.setCurrentRow(self.products.currentRow())
        self.owner.open_product()

    def history_loaded(self):
        self._study_identity = None
        self._selected.clear()
        self.owner.guided_selection = None
        self.notice.setText(
            "Opened historical batch. Its approved software paths and hashes are pinned."
        )
        self.set_stage(6)

    def execution_loaded(self):
        if self.owner.folder:
            self.set_stage(5)

    def new_batch(self):
        self.notice.setText(
            "New batch uses current saved software. Validate a new plan before running."
        )
        if isinstance(self.owner.study, Study):
            self.study_loaded(self.owner.study)
            # A separate new batch may start from the reopened workflow text,
            # but it must obtain a fresh plan and approval with current tools.
            try:
                self._saved_workflow_ready = bool(
                    read_json(self.owner.workflow.toPlainText()).get("nodes")
                )
            except (ValueError, AttributeError, TypeError):
                self._saved_workflow_ready = False
        else:
            self.set_stage(0)

    def operation_error(self, error):
        self.notice.setText(error)

    def set_stage(self, index):
        self.pages.setCurrentIndex(index)
        self.progress.setText(f"{index + 1} of 7 · {STAGES[index]}")
        self.back.setEnabled(index > 0)
        self.next.setVisible(index not in (4, 6))
        self.next.setText("View Results & QC" if index == 5 else "Continue")
        if index == 3:
            self.configuration_summary.setText(
                f"Study: {self.owner.study.study_id if self.owner.study else 'none'} · "
                f"Selected acquisitions: {len(self._selected)} · "
                f"Workflow: {self.workflow_choice.currentText()}"
            )
            self.software.setText(
                "Saved software: MRtrix3 "
                + str(self.owner.tool_directory or "not configured")
                + " · FSL "
                + str(self.owner.fsl_directory or "not configured")
                + " · ANTs "
                + str(self.owner.ants_directory or "not configured")
                + ". Exact tools are verified during planning and execution."
            )

    def previous(self):
        if self.pages.currentIndex() > 0:
            self.set_stage(self.pages.currentIndex() - 1)

    def advance(self):
        stage = self.pages.currentIndex()
        if stage == 0 and (not self.owner.study or self._data_dirty):
            self.notice.setText("Discover or open a study first.")
            return
        if stage == 1 and not self._selected:
            self.notice.setText("Select at least one acquisition to process.")
            return
        if (
            stage == 2
            and self.workflow_choice.currentData() == "convert"
            and not self.history_confirm.isChecked()
        ):
            self.notice.setText("Review and confirm processing history before choosing conversion.")
            return
        if (
            stage == 2
            and self.workflow_choice.currentData() == "saved"
            and not self._saved_workflow_ready
        ):
            self.notice.setText(
                "Load a saved workflow or configure one in Advanced Settings first."
            )
            return
        if stage == 3:
            if self.owner.plan is not None:
                self.set_stage(4)
            else:
                self._validate()
            return
        if stage == 5 and not self.owner.folder:
            self.notice.setText("Run or reopen an approved batch before viewing results.")
            return
        self.set_stage(min(stage + 1, 6))
