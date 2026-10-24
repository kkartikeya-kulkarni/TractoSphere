"""Task-first presentation over one approved tractography configuration.

TrackingExecutionPanel owns every input, typed Task, plan and worker. These
controls edit that same state; no second command builder or executor exists.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .components import Disclosure, button, label

TASKS = (
    (
        "generate",
        "Generate New Streamlines",
        "Create a new tractogram from a verified FOD or suitable DWI tensor input.",
    ),
    (
        "filter",
        "Select or Filter Existing Streamlines",
        "Keep tracks that satisfy reviewed spatial rules; the original tractogram stays unchanged.",
    ),
    (
        "density",
        "Create a Tract-Density Map",
        "Map streamline-derived density onto a compatible reference grid. This is not an axon count.",
    ),
)

METHOD_HELP = {
    "tckgen.ifod2": "Probabilistic iFOD2 samples directions from an FOD; different runs can differ.",
    "tckgen.ifod1": "Probabilistic iFOD1 samples directions from an FOD; different runs can differ.",
    "tckgen.sdstream": "Deterministic SD_Stream follows FOD directions under the selected tracking rules.",
    "tckgen.tensor_det": "Deterministic tensor tracking requires a compatible diffusion series and explicit shell selection.",
    "tckgen.tensor_prob": "Probabilistic tensor tracking requires a compatible diffusion series and explicit shell selection.",
}


class GuidedTracking(QWidget):
    """A small, contextual view of the legacy panel's authoritative controls."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._syncing = False
        root = QVBoxLayout(self)
        root.addWidget(label("What would you like to do?", "title"))
        root.addWidget(
            label(
                "Choose one task, select verified inputs, then review the exact scientific plan before running.",
                "caption",
            )
        )
        choices = QHBoxLayout()
        self.task_buttons = {}
        for key, title, explanation in TASKS:
            choice = button(title, lambda _=False, task=key: self.select_task(task))
            choice.setCheckable(True)
            choice.setAccessibleName(title)
            choice.setToolTip(explanation)
            choices.addWidget(choice)
            self.task_buttons[key] = choice
        root.addLayout(choices)
        self.task_help = label("", "caption")
        root.addWidget(self.task_help)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._generate_page())
        self.pages.addWidget(self._filter_page())
        self.pages.addWidget(self._density_page())
        root.addWidget(self.pages)

        output = QFormLayout()
        self.output = QLineEdit()
        self.output.setPlaceholderText("Choose a new derivative directory")
        self.output.setAccessibleName("New tractography output directory")
        output_row = QHBoxLayout()
        output_row.addWidget(self.output, 1)
        output_row.addWidget(button("Choose parent…", self.choose_parent))
        output.addRow("New output directory", output_row)
        root.addLayout(output)

        root.addWidget(label("Review the plan", "section"))
        self.summary = label(
            "No plan has been validated. Nothing will run until you approve it.", "caption"
        )
        root.addWidget(self.summary)
        self.preview = button("Validate and Preview Plan", owner.prepare, primary=True)
        root.addWidget(self.preview)
        self.commands = QPlainTextEdit()
        self.commands.setReadOnly(True)
        self.commands.setAccessibleName("Exact tractography commands and scientific limitations")
        self.command_details = Disclosure("Exact commands and limitations", self.commands)
        root.addWidget(self.command_details)
        self.approval = QCheckBox(
            "I reviewed this exact plan, source association and scientific limitations"
        )
        root.addWidget(self.approval)
        self.run_button = button("Run Approved Plan", owner.run, primary=True)
        root.addWidget(self.run_button)
        self.result = label("", "caption")
        root.addWidget(self.result)
        result_actions = QHBoxLayout()
        self.open_button = button("Open Result in Viewer", owner.open_result)
        self.qc_button = button("Review QC", owner.open_qc)
        self.details_button = button("View Run Details", owner.open_run)
        for action in (self.open_button, self.qc_button, self.details_button):
            result_actions.addWidget(action)
        root.addLayout(result_actions)
        root.addStretch()

        self.output.textChanged.connect(owner.output.setText)
        owner.output.textChanged.connect(self._output_from_advanced)
        self.approval.toggled.connect(owner.approval.setChecked)
        owner.approval.toggled.connect(self._approval_from_advanced)
        owner.operation.currentIndexChanged.connect(self._operation_from_advanced)
        owner.method.currentIndexChanged.connect(self._method_from_advanced)
        owner.count.valueChanged.connect(self._count_from_advanced)
        for source in (
            owner.source,
            owner.seed_method,
            owner.seed,
            owner.mask,
            owner.tracts,
            owner.reference,
            owner.five_tt,
            owner.mode,
        ):
            source.currentIndexChanged.connect(self.refresh_inventory)
        self.refresh_inventory()
        self._operation_from_advanced()
        self._output_from_advanced(owner.output.text())
        self._approval_from_advanced(owner.approval.isChecked())
        self.update_actions()

    def _form_page(self, title, explanation):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label(title, "section"))
        layout.addWidget(label(explanation, "caption"))
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.addLayout(form)
        return page, layout, form

    def _linked_combo(self, legacy, name):
        combo = QComboBox()
        combo.setAccessibleName(name)
        combo.currentIndexChanged.connect(
            lambda _index, source=combo, target=legacy: self._set_advanced_combo(source, target)
        )
        return combo

    def _generate_page(self):
        page, layout, form = self._form_page(
            "Generate New Streamlines",
            "A computational seed starts tracking; it is not necessarily an anatomical endpoint.",
        )
        self.source = self._linked_combo(self.owner.source, "Tracking input")
        self.method = self._linked_combo(self.owner.method, "Tracking method")
        self.mode = self._linked_combo(self.owner.mode, "Tracking mode")
        self.seed_method = self._linked_combo(self.owner.seed_method, "Seed method")
        self.seed = self._linked_combo(self.owner.seed, "Seed region or mask")
        self.mask = self._linked_combo(self.owner.mask, "Tracking mask")
        self.five_tt = self._linked_combo(self.owner.five_tt, "Five-tissue anatomy")
        self.reference_generate = self._linked_combo(self.owner.reference, "Tracking reference")
        self.count = QSpinBox()
        self.count.setRange(1, 1000000)
        self.count.setAccessibleName("Requested output streamline count")
        self.count.valueChanged.connect(self.owner.count.setValue)
        for title, widget in (
            ("Tracking input", self.source),
            ("Method", self.method),
            ("Tracking mode", self.mode),
            ("Where should streamlines be seeded?", self.seed_method),
            ("Seed region or mask", self.seed),
            ("Where may tracks travel? Tracking mask", self.mask),
            ("Five-tissue anatomy", self.five_tt),
            ("Reference image", self.reference_generate),
            ("Requested output streamlines", self.count),
        ):
            form.addRow(title, widget)
        self.generate_form = form
        self.method_help = label("", "caption")
        layout.addWidget(self.method_help)
        layout.addWidget(
            label(
                "A tracking mask limits propagation; a required ROI is a pathway condition. Configure pathway ROIs below or in Advanced Settings.",
                "caption",
            )
        )
        layout.addStretch()
        return page

    def _filter_page(self):
        page, layout, form = self._form_page(
            "Select or Filter Existing Streamlines",
            "Choose an existing tractogram and reference. Include rules test regions a track must cross; exclude rules reject intersections.",
        )
        self.filter_tracts = self._linked_combo(self.owner.tracts, "Source tractogram")
        self.filter_reference = self._linked_combo(
            self.owner.reference, "Tractogram reference image"
        )
        self.ends_only = QCheckBox("Test endpoints only, instead of the full pathway")
        self.ends_only.toggled.connect(self.owner.ends.setChecked)
        self.owner.ends.toggled.connect(self._ends_from_advanced)
        form.addRow("Existing tractogram", self.filter_tracts)
        form.addRow("Reference image", self.filter_reference)
        form.addRow(self.ends_only)
        layout.addWidget(
            label(
                "Ordered waypoints use the saved streamline vertex direction and cannot be combined with endpoint-only filtering. ROI constraints require explicit, compatible source and space identity.",
                "caption",
            )
        )
        layout.addStretch()
        return page

    def _density_page(self):
        page, layout, form = self._form_page(
            "Create a Tract-Density Map",
            "Use a verified tractogram and compatible reference image. The source tracks remain unchanged.",
        )
        self.density_tracts = self._linked_combo(self.owner.tracts, "Density source tractogram")
        self.density_reference = self._linked_combo(self.owner.reference, "Density reference grid")
        form.addRow("Existing tractogram", self.density_tracts)
        form.addRow("Reference image / grid", self.density_reference)
        layout.addWidget(
            label(
                "Density is streamline-derived spatial information, not a direct measure of axon counts. Directional channels are in Advanced Settings.",
                "caption",
            )
        )
        layout.addStretch()
        return page

    def select_task(self, task):
        index = self.owner.operation.findData(task)
        if index >= 0:
            self.owner.operation.setCurrentIndex(index)
        self._operation_from_advanced()

    def _operation_from_advanced(self, *_):
        current = self.owner.operation.currentData()
        for index, (key, _title, explanation) in enumerate(TASKS):
            self.task_buttons[key].setChecked(key == current)
            if key == current:
                self.pages.setCurrentIndex(index)
                self.task_help.setText(explanation)
        self.clear_plan()
        self.update_actions()

    def _set_advanced_combo(self, source, target):
        if self._syncing:
            return
        index = target.findData(source.currentData())
        if index >= 0:
            target.setCurrentIndex(index)

    def _copy_combo(self, target, source):
        target.blockSignals(True)
        target.clear()
        for index in range(source.count()):
            target.addItem(source.itemText(index), source.itemData(index))
            target.setItemData(
                index,
                source.itemData(index, Qt.ItemDataRole.ToolTipRole),
                Qt.ItemDataRole.ToolTipRole,
            )
            model_item = source.model().item(index)
            if model_item is not None and target.model().item(index) is not None:
                target.model().item(index).setEnabled(model_item.isEnabled())
        target.setCurrentIndex(source.currentIndex())
        target.blockSignals(False)

    def refresh_inventory(self):
        self._syncing = True
        for target, source in (
            (self.source, self.owner.source),
            (self.method, self.owner.method),
            (self.mode, self.owner.mode),
            (self.seed_method, self.owner.seed_method),
            (self.seed, self.owner.seed),
            (self.mask, self.owner.mask),
            (self.five_tt, self.owner.five_tt),
            (self.reference_generate, self.owner.reference),
            (self.filter_reference, self.owner.reference),
            (self.density_reference, self.owner.reference),
            (self.filter_tracts, self.owner.tracts),
            (self.density_tracts, self.owner.tracts),
        ):
            self._copy_combo(target, source)
        self._syncing = False
        self._count_from_advanced(self.owner.count.value())
        self._ends_from_advanced(self.owner.ends.isChecked())
        self._method_from_advanced()
        self.update_actions()

    def _method_from_advanced(self, *_):
        self.method_help.setText(
            METHOD_HELP.get(self.owner.method.currentData(), "Choose a supported method.")
        )
        self.generate_form.setRowVisible(self.five_tt, self.owner.mode.currentData() == "act")
        self.generate_form.setRowVisible(
            self.reference_generate, self.owner.mode.currentData() == "act"
        )
        self.generate_form.setRowVisible(
            self.seed,
            self.owner.seed_method.currentData()
            in {"image", "roi", "random_per_voxel", "grid_per_voxel", "gmwmi"},
        )

    def _count_from_advanced(self, value):
        self.count.blockSignals(True)
        self.count.setValue(value)
        self.count.blockSignals(False)

    def _ends_from_advanced(self, checked):
        self.ends_only.blockSignals(True)
        self.ends_only.setChecked(checked)
        self.ends_only.blockSignals(False)

    def _output_from_advanced(self, value):
        if self.output.text() != value:
            self.output.blockSignals(True)
            self.output.setText(value)
            self.output.blockSignals(False)

    def _approval_from_advanced(self, checked):
        self.approval.blockSignals(True)
        self.approval.setChecked(checked)
        self.approval.blockSignals(False)
        self.update_actions()

    def choose_parent(self):
        path = QFileDialog.getExistingDirectory(self, "Tractography output parent")
        if path:
            self.output.setText(str(Path(path) / "tracking-attempt001"))

    def clear_plan(self):
        self.summary.setText("Configuration changed. Validate the exact plan before approval.")
        self.commands.clear()
        self.update_actions()

    def plan_ready(self, plan):
        data = plan.to_dict()
        self.summary.setText(
            f"{self.task_help.text()}\nOutput: {data['output_directory']}\n"
            f"Commands: {len(data['commands'])}. Approval digest: {plan.digest}. "
            "Scientific QC remains separate from execution."
        )
        self.commands.setPlainText(self.owner.log.toPlainText())
        self.update_actions()

    def result_ready(self, record):
        status = record.get("status", "unknown").replace("_", " ")
        self.result.setText(
            f"{status.capitalize()} · scientific QC and review remain separate. "
            + str(record.get("plan", {}).get("output_directory", ""))
        )
        self.update_actions()

    def update_actions(self):
        idle = self.owner.owner.thread is None
        self.preview.setEnabled(idle)
        self.approval.setEnabled(idle and self.owner.plan is not None)
        self.run_button.setEnabled(
            idle and self.owner.plan is not None and self.owner.approval.isChecked()
        )
        self.open_button.setEnabled(idle and self.owner.maps.count() > 0)
        self.qc_button.setEnabled(idle and self.owner.qc_button.isEnabled())
        self.details_button.setEnabled(idle and self.owner.manifest is not None)
