"""Scientific planning and accepted DTI/CSD execution in the existing workstation.

Imports/hash checks and DTI/CSD use the established owned-worker lifecycle.
Tracking and connectome algorithms remain non-executable until accepted.
The registry, declarations and proposed outputs never impersonate measured QC.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.dataset import DwiDataset
from dmri_studio.core.execution import detect_mrtrix, save_execution_plan
from dmri_studio.core.scientific import (
    ALGORITHMS,
    ImportRequest,
    OutputRef,
    Task,
    build_workflow,
    import_artifact,
)
from dmri_studio.core.scientific.contracts import KINDS, Artifact
from dmri_studio.core.scientific.records import restore_selection, save_selection
from dmri_studio.core.scientific.registry import Algorithm
from dmri_studio.core.workflow import ProcessingHistory

from .components import Disclosure, button, label
from .execution import ProcessingWorker
from .operation_state import OperationState, State
from .scientific_guidance import IMPORT_EVIDENCE, input_description, input_label
from .theme import Theme
from .workstation import PageHeader


def choice(values: Sequence[str]) -> QComboBox:
    """Start unset: choosing an algorithm or convention is an explicit act."""
    widget = QComboBox()
    widget.addItem("Choose…", None)
    for value in values:
        widget.addItem(value, value)
    return widget


class ImportDialog(QDialog):
    """Explicit input meaning, not a guess based on component count or filename."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        initial_kind: str | None = None,
        initial_artifact: Artifact | None = None,
    ) -> None:
        """Build native controls over the shared scientific contracts; start no processing."""
        super().__init__(parent)
        self.setWindowTitle("Import scientific input")
        self.resize(620, 600)
        root = QVBoxLayout(self)
        root.addWidget(label("Inspect an existing input", "section"))
        root.addWidget(
            label(
                "Original files remain unchanged. External processing history and scientific acceptance remain unverified.",
                "caption",
            )
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        content_layout.addLayout(form)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        self.kind = QComboBox()
        self.kind.addItem("Choose the kind of data…", None)
        for kind in KINDS:
            self.kind.addItem(input_label(kind), kind)
        if initial_kind in KINDS:
            self.kind.setCurrentIndex(self.kind.findData(initial_kind))
        form.addRow("Input type", self.kind)
        self.purpose = label(
            "Choose a scientific input type to see what evidence it needs.", "caption"
        )
        content_layout.addWidget(self.purpose)
        self.evidence = label("", "caption")
        content_layout.addWidget(self.evidence)
        self.paths = {}
        self.path_rows = {}
        for name, title in (
            ("path", "Input file"),
            ("bval", "b-values"),
            ("bvec", "b-vectors"),
            ("sidecar", "Acquisition / provenance JSON"),
        ):
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            field = QLineEdit()
            field.setAccessibleName(title)
            self.paths[name] = field
            layout.addWidget(field, 1)
            browse = button("Browse…", lambda _=False, n=name: self.browse(n))
            browse.setAccessibleName(f"Browse {title}")
            layout.addWidget(browse)
            form.addRow(title, row)
            self.path_rows[name] = (row, form.labelForField(row))
        self.state = choice(("raw", "preprocessed", "unknown"))
        self.state.setCurrentIndex(3)
        self.subject = QLineEdit()
        self.subject.setPlaceholderText("Explicit subject identity for tracking/ROI association")
        self.space = QLineEdit()
        self.space.setPlaceholderText(
            "Optional subject/session + registered space identity; not a transform"
        )
        self.provenance = QLineEdit(
            "External input; processing history and scientific acceptance unknown."
        )
        form.addRow("Processing state", self.state)
        self.state_label = form.labelForField(self.state)
        form.addRow("Subject identity", self.subject)
        form.addRow("Space association", self.space)
        form.addRow("Provenance statement", self.provenance)
        conventions = QWidget()
        convention_form = QFormLayout(conventions)
        convention_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        content_layout.addWidget(
            Disclosure("Scientific identity and format declarations", conventions)
        )
        self.declarations = {}
        self.declaration_rows = {}
        specs = (
            (
                "space_kind",
                "Coordinate-space kind",
                (
                    "DWI_NATIVE",
                    "T1_NATIVE",
                    "T2_NATIVE",
                    "MNI_TEMPLATE",
                    "ATLAS_NATIVE",
                    "OTHER_REGISTERED_SPACE",
                ),
                {"anatomy", "atlas_labels", "roi", "mask", "atlas_probability"},
            ),
            (
                "session_id",
                "Session identity",
                None,
                {"anatomy", "atlas_labels", "roi", "mask", "dwi", "spatial_transform"},
            ),
            (
                "acquisition_id",
                "Acquisition identity",
                None,
                {"anatomy", "atlas_labels", "roi", "mask", "dwi", "spatial_transform"},
            ),
            (
                "reference_history",
                "DWI correction / processing history evidence",
                None,
                {"anatomy", "dwi"},
            ),
            ("reference_record", "Verified low-b reference record JSON", None, {"anatomy"}),
            (
                "space_review",
                "Same-space image association JSON",
                None,
                {"anatomy", "atlas_labels", "roi", "mask"},
            ),
            (
                "qc_association",
                "Registered atlas to diffusion QC association JSON",
                None,
                {"atlas_labels"},
            ),
            ("whole_head", "Whole-head structural coverage", ("yes", "no"), {"anatomy"}),
            (
                "tensor_layout",
                "Tensor component order",
                ("mrtrix_D11_D22_D33_D12_D13_D23", "fsl_Dxx_Dxy_Dxz_Dyy_Dyz_Dzz"),
                {"tensor"},
            ),
            (
                "tensor_frame",
                "Tensor coordinate frame",
                ("world_ras", "fsl_image_axes"),
                {"tensor"},
            ),
            ("tensor_units", "Tensor units", ("mm2/s",), {"tensor"}),
            ("sh_basis", "SH basis", ("mrtrix3_real_symmetric",), {"fod"}),
            ("sh_frame", "SH coordinate frame", ("world_ras",), {"fod"}),
            (
                "tissue",
                "Tissue compartment",
                ("wm", "gm", "csf"),
                {"fod", "response", "tissue_scalar"},
            ),
            (
                "normalization",
                "Normalization history",
                ("unknown", "not_done", "completed"),
                {"fod", "tissue_scalar"},
            ),
            (
                "channels",
                "5TT channel order",
                ("cortical_gm,subcortical_gm,wm,csf,pathology",),
                {"5tt"},
            ),
            ("metric", "Scalar metric identity", ("FA", "other", "unknown"), {"anatomy"}),
            (
                "anatomical_modality",
                "Image contrast or scalar metric",
                ("T1w", "T2w", "b0", "mean_b0", "FA", "MD", "other", "unknown"),
                {"anatomy"},
            ),
            (
                "anatomical_review",
                "Hash-bound anatomical alignment review JSON",
                None,
                {"anatomy", "5tt", "gmwmi"},
            ),
            ("anatomy_sha256", "Original segmentation anatomy SHA-256", None, {"5tt"}),
            ("five_tt_sha256", "Source 5TT SHA-256", None, {"gmwmi"}),
            ("alignment_evidence", "Atlas alignment evidence / review", None, {"atlas_labels"}),
            (
                "registration_record",
                "Hash-bound registration review JSON (optional)",
                None,
                {"atlas_labels"},
            ),
            (
                "registration_qc",
                "Native atlas QC bundle JSON",
                None,
                {"atlas_labels", "connectome_matrix"},
            ),
            (
                "registration_diagnostic",
                "Explicit diagnostic record JSON (optional)",
                None,
                {"atlas_labels"},
            ),
            ("threshold", "Probability threshold (0–1)", None, {"atlas_probability"}),
            ("shells", "Response row b-values (comma-separated)", None, {"response"}),
            ("tractogram_id", "Associated tractogram content ID", None, {"streamline_weights"}),
            (
                "weight_record",
                "Hash-bound streamline-weight record JSON",
                None,
                {"streamline_weights"},
            ),
            ("sift2_fod_sha256", "Reviewed tracking-source FOD SHA-256", None, {"tractogram"}),
        )
        for key, title, values, kinds in specs:
            field = choice(values) if values else QLineEdit()
            field.setAccessibleName(title)
            self.declarations[key] = field
            convention_form.addRow(title, field)
            self.declaration_rows[key] = (field, convention_form.labelForField(field), kinds)
        self.registration_reviewed = QCheckBox(
            "Historical registration declaration (does not confer approval)"
        )
        convention_form.addRow(self.registration_reviewed)
        self.kind.currentIndexChanged.connect(self.update_fields)
        self.update_fields()
        self.error = label("", "caption")
        root.addWidget(self.error)
        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        actions.button(QDialogButtonBox.StandardButton.Ok).setText("Inspect input")
        actions.accepted.connect(self.accept_checked)
        actions.rejected.connect(self.reject)
        root.addWidget(actions)
        if initial_artifact is not None:
            self._populate_imported_input(initial_artifact, actions)

    def _populate_imported_input(self, artifact: Artifact, actions: QDialogButtonBox) -> None:
        """Review declarations on one original source without choosing its file again."""
        self.setWindowTitle("Review imported input declarations")
        self.kind.setCurrentIndex(self.kind.findData(artifact.kind))
        self.kind.setEnabled(False)
        original = artifact.payload["request"]
        for name, field in self.paths.items():
            field.setText(original.get(name) or "")
            field.setReadOnly(True)
        for action in self.findChildren(QPushButton):
            if action.accessibleName().startswith("Browse "):
                action.setEnabled(False)
        declarations = artifact.payload.get("declarations", {})
        self.subject.setText(declarations.get("subject_id", ""))
        self.space.setText(declarations.get("space_id", ""))
        self.provenance.setText(artifact.payload["provenance"]["statement"])
        if artifact.kind == "dwi":
            self.state.setCurrentIndex(
                self.state.findData(declarations.get("processing_state", "unknown"))
            )
        if artifact.kind == "atlas_labels":
            self.registration_reviewed.setChecked(
                bool(declarations.get("registration_reviewed", False))
            )
        for key, (field, _, kinds) in self.declaration_rows.items():
            if artifact.kind not in kinds or key not in declarations:
                continue
            value = declarations[key]
            if isinstance(field, QComboBox):
                field.setCurrentIndex(
                    field.findData("yes" if value is True else "no" if value is False else value)
                )
            else:
                field.setText(str(value))
        self.purpose.setText(
            "Review source documentation before changing declarations. The original input is "
            "unchanged; reinspection checks its current bytes. A filename alone cannot establish identity."
        )
        if artifact.kind == "anatomy":
            self.evidence.setText(
                "T1_NATIVE means the subject's T1 structural space; an explicitly documented "
                "ACPC-aligned HCP structural image may belong there. It does not mean raw scanner "
                "coordinates or MNI template space. Confirm the specific source before selecting it."
            )
        actions.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Reinspect and save declarations"
        )
        self._original_declarations = dict(declarations)

    def browse(self, name: str) -> None:
        """Use a conventional chooser; no automatic sidecar association."""
        title = {
            "path": "input file",
            "bval": "b-values",
            "bvec": "b-vectors",
            "sidecar": "acquisition or provenance JSON",
        }[name]
        path, _ = QFileDialog.getOpenFileName(self, f"Choose {title}")
        if path:
            self.paths[name].setText(path)

    def update_fields(self) -> None:
        """Show only declarations relevant to the selected contract."""
        kind = self.kind.currentData()
        for name, (row, title) in self.path_rows.items():
            visible = name == "path" or kind == "dwi"
            row.setVisible(visible)
            title.setVisible(visible)
        for field, title, kinds in self.declaration_rows.values():
            field.setVisible(kind in kinds)
            title.setVisible(kind in kinds)
        self.state.setVisible(kind == "dwi")
        self.state_label.setVisible(kind == "dwi")
        self.registration_reviewed.setVisible(kind == "atlas_labels")
        self.purpose.setText(
            input_description(kind)
            + " Technical inspection does not establish scientific acceptance. Open scientific declarations when the selected type requires identity or convention evidence."
            if kind
            else "Choose a scientific input type to see what evidence it needs."
        )
        self.evidence.setText(
            "Before inspection: " + IMPORT_EVIDENCE[kind]
            if kind
            else "Select a task-specific input type. Leave identity declarations blank when the evidence is unavailable; later scientific planning may remain blocked."
        )

    def request(self) -> ImportRequest:
        """Return scientific declarations separately from verified measurements."""
        kind = self.kind.currentData()
        if not kind:
            raise ValueError("Choose the input type")
        if not self.paths["path"].text().strip():
            raise ValueError("Choose an input file")
        declarations = dict(getattr(self, "_original_declarations", {}))
        if self.subject.text().strip():
            declarations["subject_id"] = self.subject.text().strip()
        else:
            declarations.pop("subject_id", None)
        if self.space.text().strip():
            declarations["space_id"] = self.space.text().strip()
        else:
            declarations.pop("space_id", None)
        if kind == "atlas_labels":
            declarations["registration_reviewed"] = self.registration_reviewed.isChecked()
        if kind == "dwi":
            declarations["processing_state"] = self.state.currentData()
        for key, (field, _, kinds) in self.declaration_rows.items():
            if kind not in kinds:
                continue
            value = field.currentData() if isinstance(field, QComboBox) else field.text().strip()
            if value is None or value == "":
                declarations.pop(key, None)
                continue
            if key == "whole_head":
                value = value == "yes"
            if key == "threshold":
                value = float(value)
            if key == "shells":
                value = [float(v.strip()) for v in value.split(",")]
            if key == "registration_diagnostic":
                value = json.loads(Path(value).read_text())
            declarations[key] = value
        paths = {
            name: Path(field.text().strip()) if field.text().strip() else None
            for name, field in self.paths.items()
            if name == "path" or kind == "dwi"
        }
        return ImportRequest(
            kind, **paths, declarations=declarations, provenance=self.provenance.text()
        )

    def accept_checked(self) -> None:
        """Keep input mistakes in the dialog; inspection failures stay on the page."""
        try:
            self.request()
        except (ValueError, TypeError) as exc:
            self.error.setText(str(exc))
            return
        self.accept()


class TaskDialog(QDialog):
    """Choose typed sources and parameters; there is no arbitrary argv field."""

    def __init__(
        self,
        spec: Algorithm,
        artifacts: Sequence[Artifact],
        tasks: Sequence[Task],
        parent: QWidget | None = None,
    ) -> None:
        """Build native controls over the shared scientific contracts; start no processing."""
        super().__init__(parent)
        self.spec = spec
        self.setWindowTitle(spec.name + " — " + spec.implementation)
        self.resize(650, 600)
        root = QVBoxLayout(self)
        root.addWidget(label(spec.name, "section"))
        root.addWidget(
            label(
                "Choose scientific policies explicitly. Implemented reconstruction, tracking, ACT, tissue and connectome tasks require command preview and approval. Planned algorithms remain unavailable.",
                "caption",
            )
        )
        root.addWidget(
            label(
                "Missing a required input? Close this dialog, use Import required input in Advanced algorithms, then reopen Configure task. Optional inputs may remain unbound.",
                "caption",
            )
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        next_id = 1
        while any(t.id == f"task{next_id}" for t in tasks):
            next_id += 1
        self.identifier = QLineEdit(f"task{next_id}")
        form.addRow("Task ID", self.identifier)
        self.bindings = {}
        for role, kind in {**spec.required_inputs, **spec.optional_inputs}.items():
            combo = QComboBox()
            combo.setAccessibleName(role)
            combo.addItem(
                f"Select imported {kind}…"
                if role in spec.required_inputs
                else "Not bound (optional)",
                None,
            )
            for artifact in artifacts:
                if artifact.kind == kind:
                    combo.addItem(
                        f"{Path(artifact.payload['path']).name} · {artifact.id[:8]}", artifact.id
                    )
            for task in tasks:
                for name, out_kind in ALGORITHMS[task.algorithm].outputs.items():
                    if out_kind == kind:
                        combo.addItem(f"Expected: {task.id} / {name}", OutputRef(task.id, name))
            self.bindings[role] = combo
            form.addRow(role.replace("_", " ") + f" ({kind})", combo)
        self.fields = {}
        for key, rule in spec.parameters.items():
            if rule["type"] == "choice":
                field = choice(rule["choices"])
            else:
                field = QLineEdit()
                field.setPlaceholderText(
                    "Comma-separated values" if rule["type"].endswith("list") else "Explicit value"
                )
                if rule["type"] == "choice_list":
                    field.setPlaceholderText(", ".join(rule["choices"]))
            field.setToolTip(rule["policy"])
            field.setAccessibleName(key)
            self.fields[key] = field
            form.addRow(
                key.replace("_", " ") + (" *" if rule["required"] else " (optional)"), field
            )
        self.error = label("", "caption")
        root.addWidget(self.error)
        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        actions.button(QDialogButtonBox.StandardButton.Ok).setText("Add to proposal")
        actions.accepted.connect(self.accept_checked)
        actions.rejected.connect(self.reject)
        root.addWidget(actions)

    def task(self) -> Task:
        """Parse only declared parameter types; authoritative validation is core-owned."""
        params = {}
        for key, field in self.fields.items():
            value = field.currentData() if isinstance(field, QComboBox) else field.text().strip()
            if value is None or value == "":
                continue
            kind = self.spec.parameters[key]["type"]
            if kind == "int":
                value = int(value)
            elif kind == "number":
                value = float(value)
            elif kind in {"number_list", "vector"}:
                value = [float(v.strip()) for v in value.split(",")]
            elif kind == "choice_list":
                value = [v.strip() for v in value.split(",")]
            params[key] = value
        return Task(
            self.identifier.text().strip(),
            self.spec.id,
            {
                key: field.currentData()
                for key, field in self.bindings.items()
                if field.currentData() is not None
            },
            params,
        )

    def accept_checked(self) -> None:
        """Keep parsing errors actionable; compatibility is reviewed in the proposal."""
        try:
            self.task()
        except (ValueError, TypeError) as exc:
            self.error.setText(str(exc))
            return
        self.accept()


class ScientificPlanningPage(QWidget):
    """One inventory/task graph shared by models, tracking and connectivity modes."""

    notification = Signal(str, str)
    execution_busy = Signal(bool)
    inspect_requested = Signal(object)
    roi_import_requested = Signal()
    roi_imported = Signal(object)
    roi_filter_requested = Signal()
    qc_requested = Signal()

    def __init__(
        self,
        theme: Theme,
        context: Callable[[], DwiDataset | None] = lambda: None,
        history_context: Callable[[], ProcessingHistory | None] = lambda: None,
    ) -> None:
        """Build native controls over the shared scientific contracts; start no processing."""
        super().__init__()
        self.theme = theme
        self.context = context
        self.history_context = history_context
        self.artifacts = []
        self.tasks = []
        self.plan = None
        self.thread = self.worker = None
        self._retired_workers = []
        self.cancel = Event()
        self.pending = None
        self.pending_revision_id = None
        self.product_handoff = None
        self.stage = "models"
        self.tool_directory = None
        self.fsl_directory = None
        self.processing_available = lambda: True
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)
        self.heading = PageHeader(
            "Diffusion Models",
            "Independent scientific inputs. DTI and CSD reconstruction are available; tracking generation is available; connectomics is available with verified registered parcellations.",
        )
        root.addWidget(self.heading)
        actions = QHBoxLayout()
        self.import_button = button("Import input…", self.browse_import)
        self.active_button = button("Use loaded DWI", self.import_active)
        self.restore_button = button("Open selection…", self.browse_restore)
        self.save_button = button("Save selection…", self.browse_save)
        for b in (self.import_button, self.active_button, self.restore_button, self.save_button):
            actions.addWidget(b)
        actions.addStretch()
        root.addLayout(actions)
        self.task_intro = label(
            "Choose a supported task below; imported inputs are shared across scientific workspaces.",
            "caption",
        )
        root.addWidget(self.task_intro)
        task_actions = QHBoxLayout()
        self.model_dti = button(
            "Fit diffusion tensor", lambda: self.tabs.setCurrentWidget(self.dti)
        )
        self.model_csd = button("Estimate FOD", lambda: self.tabs.setCurrentWidget(self.csd))
        self.model_inputs = button(
            "Review imported inputs", lambda: self.tabs.setCurrentWidget(self.input_page)
        )
        for action in (self.model_dti, self.model_csd, self.model_inputs):
            task_actions.addWidget(action)
        task_actions.addStretch()
        root.addLayout(task_actions)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.inputs = QListWidget()
        self.inputs.setAccessibleName("Scientific input inventory")
        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("Search imported data by file, type or subject…")
        self.input_search.setAccessibleName("Search scientific input inventory")
        self.input_search.textChanged.connect(self.filter_inputs)
        self.input_details = label(
            "Import DWI, tensor, FOD or tractogram independently. Scientific conventions must be declared explicitly."
        )
        self.input_details.setMinimumWidth(0)
        self.input_page = QWidget()
        layout = QVBoxLayout(self.input_page)
        layout.addWidget(self.input_search)
        split = QSplitter()
        split.addWidget(self.inputs)
        details = QWidget()
        detail_layout = QVBoxLayout(details)
        detail_layout.addWidget(self.input_details)
        detail_layout.addStretch()
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setWidget(details)
        split.addWidget(detail_scroll)
        split.setSizes([360, 480])
        layout.addWidget(split, 1)
        row = QHBoxLayout()
        self.remove_input = button("Remove inventory reference", self.remove_artifact)
        self.inspect = button("Inspect selected input", self.inspect_input)
        self.review_input = button("Review declarations…", self.review_selected_input)
        row.addWidget(self.remove_input)
        row.addWidget(self.inspect)
        row.addWidget(self.review_input)
        row.addStretch()
        layout.addLayout(row)
        self.tabs.addTab(self.input_page, "Inputs")
        self.inputs.currentRowChanged.connect(self.show_input)
        self.algorithm_page = QWidget()
        layout = QVBoxLayout(self.algorithm_page)
        self.algorithm_summary = label(
            "Choose a task to see its required scientific inputs. Import eligible data before planning.",
            "caption",
        )
        layout.addWidget(self.algorithm_summary)
        split = QSplitter()
        self.algorithms = QListWidget()
        self.algorithms.setAccessibleName("Scientific algorithm registry")
        split.addWidget(self.algorithms)
        self.algorithm_details = label("")
        details = QScrollArea()
        details.setWidgetResizable(True)
        details_content = QWidget()
        details_layout = QVBoxLayout(details_content)
        details_layout.addWidget(
            Disclosure("Technical adapter roles, versions and QC contract", self.algorithm_details)
        )
        details_layout.addStretch()
        details.setWidget(details_content)
        split.addWidget(details)
        split.setSizes([350, 490])
        layout.addWidget(split, 1)
        self.add_button = button("Configure task…", self.add_task)
        self.algorithm_import = button("Import required input…", self.import_algorithm_input)
        layout.addWidget(self.algorithm_import)
        layout.addWidget(self.add_button)
        self.algorithms.currentRowChanged.connect(self.show_algorithm)
        self.tabs.addTab(self.algorithm_page, "Advanced algorithms")
        self.graph_page = QWidget()
        layout = QVBoxLayout(self.graph_page)
        self.task_list = QListWidget()
        self.task_list.setAccessibleName("Workflow task graph")
        layout.addWidget(self.task_list)
        row = QHBoxLayout()
        self.edit_task_button = button("Edit task…", self.edit_task)
        self.remove_task_button = button("Remove task", self.remove_task)
        self.validate_button = button("Validate proposal", self.validate)
        self.probe_button = button("Check command versions", self.probe)
        self.export_button = button("Export proposal…", self.export)
        for b in (
            self.edit_task_button,
            self.remove_task_button,
            self.validate_button,
            self.probe_button,
            self.export_button,
        ):
            row.addWidget(b)
        row.addStretch()
        layout.addLayout(row)
        self.findings = QPlainTextEdit()
        self.findings.setReadOnly(True)
        self.findings.setAccessibleName("Workflow compatibility and QC requirements")
        layout.addWidget(self.findings, 2)
        layout.addWidget(
            label(
                "DTI execution is available in the DTI execution tab after command preview and explicit approval. CSD execution is available in its own tab. A proposal is not scientific acceptance.",
                "caption",
            )
        )
        qc = button("Open existing QC", self.qc_requested.emit, quiet=True)
        layout.addWidget(qc)
        self.tabs.addTab(self.graph_page, "Workflow review")
        self.status = label("No scientific processing is started by this workspace.", "caption")
        root.addWidget(self.status)
        self.operation_state = OperationState(self)
        self.operation_state.bind(self.status)
        from .dti import DTIExecutionPanel

        self.dti = DTIExecutionPanel(self)
        self.tabs.addTab(self.dti, "DTI execution")
        from .csd import CSDExecutionPanel

        self.csd = CSDExecutionPanel(self)
        self.tabs.addTab(self.csd, "CSD / FOD")
        from .tracking import TrackingExecutionPanel

        self.tracking = TrackingExecutionPanel(self)
        self.tabs.addTab(self.tracking, "Tractography tasks")
        from .five_tissue import AnatomyPanel

        self.anatomy = AnatomyPanel(self)
        self.tabs.addTab(self.anatomy, "Anatomy / 5TT")
        from .sift2 import SIFT2Panel

        self.sift2 = SIFT2Panel(self)
        self.tabs.addTab(self.sift2, "SIFT2 weighting")
        from .connectome import ConnectomePanel

        self.connectome = ConnectomePanel(self)
        self.tabs.addTab(self.connectome, "Connectivity")
        from .registration import RegistrationPanel

        self.registration = RegistrationPanel(self)
        self.tabs.addTab(self.registration, "Registration / atlas QC")
        self.cancel_button = button("Cancel current operation", self.cancel_inspection)
        root.addWidget(self.cancel_button)
        self.task_list.currentRowChanged.connect(self.update_actions)
        self.set_stage("models")
        self.refresh()

    def set_stage(self, stage: str) -> None:
        """Change the registry filter, retaining every input and selected task."""
        self.stage = stage
        self.algorithms.clear()
        self.visible_algorithms = [a for a in ALGORITHMS.values() if a.stage == stage]
        for spec in self.visible_algorithms:
            self.algorithms.addItem(spec.name + " · " + spec.implementation.capitalize())
        heading_labels = self.heading.findChildren(type(self.input_details))
        heading_labels[0].setText(
            {
                "registration": "Registration and Spatial Transforms",
                "models": "Diffusion Models",
                "tractography": "Tractography",
                "connectivity": "Connectivity",
            }[stage]
        )
        heading_labels[1].setText(
            "Choose an alignment or spatial task, then review the exact inputs and transform direction."
            if stage == "registration"
            else "Independent scientific inputs. DTI and CSD reconstruction are available; tracking generation is available; connectomics is available with verified registered parcellations."
        )
        self.tabs.setTabVisible(self.tabs.indexOf(self.dti), stage == "models")
        self.tabs.setTabVisible(self.tabs.indexOf(self.csd), stage == "models")
        self.tabs.setTabVisible(self.tabs.indexOf(self.tracking), stage == "tractography")
        self.tabs.setTabVisible(self.tabs.indexOf(self.anatomy), stage == "tractography")
        self.tabs.setTabVisible(self.tabs.indexOf(self.sift2), stage == "tractography")
        self.tabs.setTabVisible(self.tabs.indexOf(self.connectome), stage == "connectivity")
        self.tabs.setTabVisible(self.tabs.indexOf(self.registration), stage == "registration")
        self.model_dti.setVisible(stage == "models")
        self.model_csd.setVisible(stage == "models")
        self.model_inputs.setVisible(stage in {"models", "connectivity"})
        self.task_intro.setText(
            {
                "models": "Choose Fit diffusion tensor or Estimate FOD. Import or reuse verified inputs; exact commands and scientific approval remain in each task.",
                "connectivity": "Build a matrix from a compatible tractogram and registered atlas. Review atlas nodes and exact commands before execution.",
                "tractography": "Choose Generate, Filter or Density in the guided Tractography task. Imported ROIs and streamlines remain shared.",
                "registration": "Choose a guided alignment, transformation or inspection task. Moving and reference image roles remain explicit.",
            }[stage]
        )
        if stage == "registration":
            self.tabs.setCurrentWidget(self.registration)
        if stage == "connectivity":
            self.tabs.setCurrentWidget(self.connectome)
        if stage == "tractography":
            self.tabs.setCurrentWidget(self.tracking)
        self.algorithm_details.setText(
            "Choose an algorithm to inspect its requirements and implementation status."
        )
        self.update_actions()

    def refresh(self) -> None:
        """Present declared and inspected states separately; invalidate stale proposals."""
        current_item = self.inputs.currentItem()
        selected_id = current_item.data(Qt.ItemDataRole.UserRole) if current_item else None
        self.inputs.clear()
        for index, a in enumerate(self.artifacts):
            declarations = a.payload.get("declarations", {})
            identity = declarations.get("subject_id") or "Subject not declared"
            self.inputs.addItem(
                f"{Path(a.payload['path']).name}\n{input_label(a.kind)} · {identity} · technically inspected, scientific review required"
            )
            self.inputs.item(index).setData(Qt.ItemDataRole.UserRole, a.id)
        if selected_id:
            self.inputs.setCurrentRow(
                next((i for i, a in enumerate(self.artifacts) if a.id == selected_id), -1)
            )
        self.filter_inputs(self.input_search.text())
        self.task_list.clear()
        for task in self.tasks:
            self.task_list.addItem(
                f"{task.id} · {ALGORITHMS[task.algorithm].name} · {ALGORITHMS[task.algorithm].implementation} adapter"
            )
        self.plan = None
        if hasattr(self, "dti"):
            self.dti.refresh()
        if hasattr(self, "csd"):
            self.csd.refresh()
        if hasattr(self, "tracking"):
            self.tracking.refresh()
        if hasattr(self, "registration"):
            self.registration.refresh()
        if hasattr(self, "anatomy"):
            self.anatomy.refresh()
        if hasattr(self, "sift2"):
            self.sift2.refresh()
        if hasattr(self, "connectome"):
            self.connectome.refresh()
        self.findings.setPlainText(
            "Choose tasks and validate their explicit input bindings. No workflow will execute."
        )
        self.update_actions()

    def filter_inputs(self, query: str) -> None:
        """Filter presentation only; artifact IDs and task bindings never change."""
        needle = query.casefold().strip()
        for index in range(self.inputs.count()):
            item = self.inputs.item(index)
            item.setHidden(needle not in item.text().casefold())

    def show_input(self, row: int) -> None:
        """Readable summary; complete hashes remain in the saved scientific selection."""
        if not 0 <= row < len(self.artifacts):
            self.update_actions()
            return
        data = self.artifacts[row].to_dict()
        g = data.get("geometry") or {}
        meta = data["metadata"]
        grad = data.get("gradients") or {}
        # Import/preview workers perform full evidence hashing. Selecting a row must
        # not synchronously rehash a potentially multi-GB DWI on the GUI thread.
        text = [
            Path(data["path"]).name,
            f"Type: {input_label(data['kind'])} ({data['kind']})",
            f"Content ID: {data['id']}",
            "Last inspected registration eligibility (rechecked during preview): "
            + json.dumps(meta.get("registration_eligibility", "not applicable")),
            f"Source: {data['path']}",
            f"Shape: {g.get('shape', meta.get('shape', 'not an image'))}",
            f"Orientation: {g.get('orientation', 'RAS+ mm tract coordinates' if data['kind'] == 'tractogram' else 'not applicable')}",
        ]
        if data["kind"] == "fod":
            text.extend(
                [
                    f"Even real symmetric SH · lmax {meta.get('lmax')} · {g.get('shape', ['?'])[-1]} coefficients",
                    "FOD coefficients are not scalar volumes. Open in Image Viewer for verified MRtrix3 real-even SH glyphs; C00 is a coefficient proxy only.",
                ]
            )
        elif data["kind"] == "response":
            text.extend(
                [
                    f"Tissue: {data['declarations'].get('tissue')}",
                    f"Response shells: {meta.get('shells')}",
                    f"Zonal coefficients per shell: {meta.get('columns')} · lmax {meta.get('lmax')}",
                ]
            )
        if grad:
            text.extend(
                [
                    f"Gradient frame: {grad['convention']}",
                    "Descriptive shells: "
                    + ", ".join(
                        f"{s['b_median']:g} ({s['count']} volumes)" for s in grad["shells"]
                    ),
                ]
            )
        text.extend(
            [
                "Declarations: "
                + (
                    "; ".join(
                        f"{key.replace('_', ' ')}: "
                        + (
                            f"{len(value.get('claims', []))} reported claims; evidence retained in selection"
                            if key == "inherited_processing_history"
                            else str(value)
                        )
                        for key, value in data["declarations"].items()
                    )
                    or "none supplied"
                ),
                f"Provenance: {data['provenance']['statement']}",
                "Technical inspection does not establish scientific acceptability.",
            ]
        )
        self.input_details.setText("\n\n".join(text))
        self.update_actions()

    def show_algorithm(self, row: int) -> None:
        """Present the selected algorithm contract, version status and required QC."""
        if not 0 <= row < len(self.visible_algorithms):
            self.update_actions()
            return
        spec = self.visible_algorithms[row]
        requirements = [
            input_label(kind) if kind in KINDS else kind.replace("_", " ")
            for kind in spec.required_inputs.values()
        ]
        available = {artifact.kind for artifact in self.artifacts}
        missing = [
            kind
            for kind in spec.required_inputs.values()
            if kind in KINDS and kind not in available
        ]
        self.algorithm_summary.setText(
            f"{spec.name}. Required: {', '.join(requirements) or 'none'}. "
            + (
                "Not imported yet: " + ", ".join(input_label(kind) for kind in missing) + ". "
                if missing
                else "Select exact imported inputs in Configure task. "
            )
            + "Optional inputs and scientific eligibility are checked during planning."
        )
        self.algorithm_details.setText(
            "\n\n".join(
                [
                    spec.name,
                    (
                        "Implemented DTI adapter · preview/approve in DTI execution"
                        if spec.id in {"dwi2tensor", "tensor2metric"}
                        else "Implemented tissue adapter · preview/approve in Anatomy / 5TT"
                        if spec.id in {"5ttgen.fsl", "5ttcheck", "5tt2vis", "5tt2gmwmi"}
                        else "Implemented tracking adapter · preview/approve in Generate / filter tracks"
                        if spec.stage == "tractography"
                        else "Implemented CSD adapter · preview/approve in CSD / FOD"
                    )
                    if spec.implementation == "implemented"
                    else "Planned adapter · execution unavailable",
                    f"Backend: {spec.backend} / {spec.command}",
                    "Required: " + ", ".join(f"{k}: {v}" for k, v in spec.required_inputs.items()),
                    "Optional: "
                    + (", ".join(f"{k}: {v}" for k, v in spec.optional_inputs.items()) or "none"),
                    "Acquisitions: " + ", ".join(spec.acquisition_types),
                    "Outputs: " + ", ".join(f"{k}: {v}" for k, v in spec.outputs.items()),
                    "Accepted algorithm versions: "
                    + (", ".join(spec.validated_versions) or "none — planning only")
                    + ". Existing preprocessing version gates remain unchanged.",
                    "QC required:\n" + "\n".join("• " + q for q in spec.qc),
                ]
            )
        )
        self.update_actions()

    def import_algorithm_input(self) -> None:
        """Open the same typed import dialog for the selected task's first missing kind."""
        row = self.algorithms.currentRow()
        if not 0 <= row < len(self.visible_algorithms):
            self.status.setText("Select a scientific task to identify its required inputs.")
            return
        spec = self.visible_algorithms[row]
        imported = {artifact.kind for artifact in self.artifacts}
        kind = next(
            (
                value
                for value in spec.required_inputs.values()
                if value in KINDS and value not in imported
            ),
            None,
        )
        self.browse_import(kind)

    def browse_import(self, kind: str | None = None) -> None:
        """Collect explicit input meaning before beginning read-only inspection."""
        dialog = ImportDialog(self, initial_kind=kind if isinstance(kind, str) else None)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.import_request(dialog.request())

    def review_selected_input(self) -> None:
        item = self.inputs.currentItem()
        if item is not None:
            self.review_input_declarations(item.data(Qt.ItemDataRole.UserRole))

    def review_anatomy_declarations(self, artifact_id: str) -> None:
        """Preserve the Registration entry point for reviewing an anatomical image."""
        self.review_input_declarations(artifact_id)

    def review_input_declarations(self, artifact_id: str) -> None:
        """Revise one draft inventory record after reinspection of its original source."""
        artifact = next((a for a in self.artifacts if a.id == artifact_id), None)
        if self.thread is not None or artifact is None:
            return
        if any(artifact_id in task.inputs.values() for task in self.tasks):
            self.status.setText(
                "This input is bound to a draft task. Remove that task before revising its declarations; approved or running plans are never changed."
            )
            return
        dialog = ImportDialog(self, initial_artifact=artifact)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        reviewed = dialog.request()
        original_request = artifact.payload["request"]
        request = ImportRequest(
            artifact.kind,
            Path(original_request["path"]),
            bval=Path(original_request["bval"]) if original_request.get("bval") else None,
            bvec=Path(original_request["bvec"]) if original_request.get("bvec") else None,
            sidecar=Path(original_request["sidecar"]) if original_request.get("sidecar") else None,
            declarations=reviewed.declarations,
            provenance=reviewed.provenance,
        )
        self.pending_revision_id = artifact_id

        def reinspect(cancel, progress):
            progress("Confirming the original imported sources are unchanged…")
            artifact.assert_current(cancel)
            return import_artifact(request, cancelled=cancel, progress=progress)

        self.start(
            "revise-input",
            reinspect,
        )

    def import_request(self, request: ImportRequest) -> None:
        """Public UI adapter over the same Qt-free reader used by CLI/Python."""
        self.start(
            "import",
            lambda cancel, progress: import_artifact(request, cancelled=cancel, progress=progress),
        )

    def import_active(self) -> None:
        """Bind only an actually loaded DWI; never infer its processing history."""
        dataset = self.context()
        if dataset is None:
            self.status.setText("Load a DWI with matching gradients first, or use Import input.")
            return
        paths = dataset.paths
        declarations = {"processing_state": "unknown"}
        history = self.history_context()
        if history is not None:
            # Preserve evidence levels verbatim as reported context, not fresh
            # authentication or a scientific acceptance claim.
            declarations["inherited_processing_history"] = history.to_dict()
        self.import_request(
            ImportRequest(
                "dwi",
                paths.dwi,
                paths.bval,
                paths.bvec,
                paths.json,
                declarations=declarations,
            )
        )

    def add_task(self) -> None:
        """Add reviewed input bindings and parameters to the unexecuted task graph."""
        row = self.algorithms.currentRow()
        if not 0 <= row < len(self.visible_algorithms):
            return
        dialog = TaskDialog(self.visible_algorithms[row], self.artifacts, self.tasks, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            task = dialog.task()
            if any(t.id == task.id for t in self.tasks):
                self.status.setText("Task ID already exists; choose a unique identifier.")
                return
            self.tasks.append(task)
            self.refresh()
            self.tabs.setCurrentWidget(self.graph_page)

    def edit_task(self) -> None:
        """Edit explicit bindings/policies; every change invalidates the prior proposal."""
        row = self.task_list.currentRow()
        if not 0 <= row < len(self.tasks):
            return
        original = self.tasks[row]
        dialog = TaskDialog(
            ALGORITHMS[original.algorithm],
            self.artifacts,
            [t for t in self.tasks if t is not original],
            self,
        )
        # Keep producer identity stable for existing output references. Replacing
        # the algorithm itself requires a new task and explicit downstream binds.
        dialog.identifier.setText(original.id)
        dialog.identifier.setReadOnly(True)
        for role, ref in original.inputs.items():
            combo = dialog.bindings[role]
            for index in range(combo.count()):
                if combo.itemData(index) == ref:
                    combo.setCurrentIndex(index)
                    break
        for key, value in original.parameters.items():
            field = dialog.fields[key]
            if isinstance(field, QComboBox):
                field.setCurrentIndex(field.findData(value))
            else:
                field.setText(
                    ", ".join(str(v) for v in value) if isinstance(value, tuple) else str(value)
                )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.tasks[row] = dialog.task()
            self.refresh()
            self.task_list.setCurrentRow(row)

    def remove_artifact(self) -> None:
        """Remove only an unbound inventory entry; never delete a source file."""
        row = self.inputs.currentRow()
        if 0 <= row < len(self.artifacts):
            if any(self.artifacts[row].id in t.inputs.values() for t in self.tasks):
                self.status.setText(
                    "Remove tasks bound to this input before removing its inventory entry."
                )
                return
            self.artifacts.pop(row)
            self.refresh()

    def remove_task(self) -> None:
        """Remove a draft task only when no other task depends on its outputs."""
        row = self.task_list.currentRow()
        if 0 <= row < len(self.tasks):
            if any(
                isinstance(ref, OutputRef) and ref.task_id == self.tasks[row].id
                for t in self.tasks
                for ref in t.inputs.values()
            ):
                self.status.setText("Remove dependent tasks before removing this producer.")
                return
            self.tasks.pop(row)
            self.refresh()

    def inspect_input(self) -> None:
        """Rehash the selected input before delegating to the existing viewer."""
        row = self.inputs.currentRow()
        if 0 <= row < len(self.artifacts):
            artifact = self.artifacts[row]

            def verify(cancel: Callable[[], bool], progress: Callable[[str], None]) -> Artifact:
                """Recheck every bound source before handing its original path to the viewer."""
                progress("Rechecking selected source hashes before viewing…")
                artifact.assert_current(cancel)
                return artifact

            self.start("inspect", verify)

    def validate(self) -> None:
        """Validate a frozen inventory/task snapshot on the existing worker lifecycle."""
        artifacts, tasks = tuple(self.artifacts), tuple(self.tasks)
        self.start(
            "plan",
            lambda cancel, progress: build_workflow(
                artifacts, tasks, cancelled=cancel, progress=progress
            ),
        )

    def probe(self) -> None:
        """Only explicit local -version queries; this cannot activate an adapter."""
        names = tuple(sorted({ALGORITHMS[t.algorithm].command for t in self.tasks}))
        directory = self.tool_directory
        artifacts, tasks = tuple(self.artifacts), tuple(self.tasks)
        self.start(
            "plan",
            lambda cancel, progress: build_workflow(
                artifacts,
                tasks,
                dependency_evidence=detect_mrtrix(
                    names=names, tool_directory=directory, cancelled=cancel
                ),
                cancelled=cancel,
                progress=progress,
            ),
        )

    def browse_save(self) -> None:
        """Save a new selection record; do not overwrite an existing run or source."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save scientific selection (new file)",
            "scientific-selection.json",
            "JSON (*.json)",
        )
        if path:
            try:
                save_selection(Path(path), tuple(self.artifacts), tuple(self.tasks))
                self.status.setText(
                    "Selection saved. Reopening will reinspect the original sources."
                )
            except (ValueError, OSError) as exc:
                self.status.setText(str(exc))

    def browse_restore(self) -> None:
        """Select a saved record for complete source reinspection."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open scientific selection", "", "JSON (*.json)"
        )
        if path:
            self.restore(Path(path))

    def restore(self, path: Path) -> None:
        """Reinspect a saved selection off-thread and replace state only on success."""
        source = Path(path)
        self.start(
            "restore",
            lambda cancel, progress: restore_selection(source, cancelled=cancel, progress=progress),
        )

    def export(self) -> None:
        """Export the current proposal without approving or executing it."""
        if self.plan is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export non-executable proposal (new file)",
            "scientific-proposal.json",
            "JSON (*.json)",
        )
        if path:
            try:
                save_execution_plan(self.plan, Path(path))
                self.status.setText(
                    "Proposal exported. No execution approval or scientific acceptance is implied."
                )
            except (ValueError, OSError) as exc:
                self.status.setText(str(exc))

    def start(
        self, kind: str, operation: Callable[[Callable[[], bool], Callable[[str], None]], Any]
    ) -> None:
        """Reuse owner-thread retirement and cooperative cancellation, not a new engine."""
        if self.thread:
            return
        self.cancel = Event()
        cancel = self.cancel
        self.pending = kind
        token = self.operation_state.begin(
            State.PROCESSING if kind.endswith("-run") else State.VALIDATING,
            "Preparing scientific execution…"
            if kind.endswith("-run")
            else "Inspecting original inputs…",
        )
        self.operation_token = token
        if kind in {
            "dti-run",
            "csd-run",
            "tracking-run",
            "connectome-run",
            "anatomy-run",
            "registration-run",
            "sift2-run",
        }:
            self.execution_busy.emit(True)
        self.thread = QThread(self)
        worker = ProcessingWorker(0, lambda: None)
        self.worker = worker
        worker.task = lambda: operation(cancel.is_set, worker.progress.emit)
        worker.moveToThread(self.thread)
        self.thread.started.connect(worker.run)
        worker.progress.connect(
            lambda text: self.operation_state.update(
                token, State.PROCESSING if kind.endswith("-run") else State.LOADING, text
            )
        )
        if kind.startswith("dti-"):
            worker.progress.connect(self.dti.log.appendPlainText)
        elif kind.startswith("csd-"):
            worker.progress.connect(self.csd.log.appendPlainText)
        elif kind.startswith("tracking-"):
            worker.progress.connect(self.tracking.log.appendPlainText)
        elif kind.startswith("sift2-"):
            worker.progress.connect(self.sift2.log.appendPlainText)
        elif kind.startswith("registration-"):
            worker.progress.connect(self.registration.log.appendPlainText)
        elif kind.startswith("anatomy-"):
            worker.progress.connect(self.anatomy.log.appendPlainText)
        elif kind.startswith("connectome-"):
            worker.progress.connect(self.connectome.log.appendPlainText)
        worker.result.connect(self.result)
        worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.thread.finished.connect(worker.deleteLater)
        self.thread.finished.connect(self.finished)
        self.status.setText("Inspecting original inputs…")
        self.update_actions()
        self.thread.start()

    def result(self, _: int, result: Any, error: str) -> None:
        """Display actual inspection/proposal findings without inferring scientific acceptance."""
        if error:
            if self.pending == "revise-input":
                error = "Declaration review failed; previous imported record unchanged. " + error
                self.pending_revision_id = None
            if self.pending.startswith("sift2-"):
                self.sift2.failed(error, cancelled=self.cancel.is_set())
            if self.pending.startswith("registration-"):
                self.registration.failed(error, cancelled=self.cancel.is_set())
            if self.pending.startswith("anatomy-"):
                self.anatomy.failed(error, cancelled=self.cancel.is_set())
            if self.pending.startswith("connectome-"):
                self.connectome.failed(error, cancelled=self.cancel.is_set())
            if self.pending in {"tracking-preview", "tracking-run"}:
                self.tracking.failed(
                    error, cancelled=self.cancel.is_set(), running=self.pending == "tracking-run"
                )
            self.operation_state.update(
                self.operation_token,
                State.CANCELLED if self.cancel.is_set() else State.FAILED,
                "Operation cancelled; originals unchanged" if self.cancel.is_set() else error,
            )
            return
        if self.cancel.is_set() and not self.pending.endswith("-run"):
            if self.pending == "revise-input":
                self.pending_revision_id = None
            self.operation_state.update(
                self.operation_token, State.CANCELLED, "Operation cancelled; originals unchanged"
            )
            return
        if self.pending in {"dti-preview", "dti-run"}:
            self.dti.result(self.pending, result)
        elif self.pending in {"csd-preview", "csd-run"}:
            self.csd.result(self.pending, result)
        elif self.pending.startswith("sift2-"):
            try:
                self.sift2.result(self.pending, result)
            except (ValueError, OSError, KeyError) as exc:
                self.sift2.failed(str(exc))
                self.operation_state.update(self.operation_token, State.FAILED, str(exc))
        elif self.pending.startswith("registration-"):
            self.registration.result(self.pending, result)
        elif self.pending in {"anatomy-preview", "anatomy-run"}:
            self.anatomy.result(self.pending, result)
        elif self.pending == "anatomy-view":
            self.status.setText("Verified anatomical product; opening the existing Image Viewer.")
            self.product_handoff = result
        elif self.pending.startswith("connectome-"):
            try:
                self.connectome.result(self.pending, result)
            except (ValueError, OSError) as exc:
                self.connectome.failed(str(exc))
                self.operation_state.update(self.operation_token, State.FAILED, str(exc))
                return
        elif self.pending in {"tracking-preview", "tracking-run"}:
            self.tracking.result(self.pending, result)
        elif self.pending == "tracking-view":
            self.status.setText("Result hash verified; opening tractography product.")
            self.product_handoff = result
        elif self.pending == "csd-view":
            self.status.setText("Result hashes verified; opening the selected scientific product.")
            self.product_handoff = result
        elif self.pending == "dti-view":
            self.status.setText("Result hashes verified; opening the selected image.")
            self.dti.view_requested.emit(result)
        elif self.pending in {"import", "import-product"}:
            if all(a.id != result.id for a in self.artifacts):
                self.artifacts.append(result)
            self.refresh()
            if result.kind == "anatomy":
                selector = self.registration.guided.imported_anatomy
                selector.setCurrentIndex(max(0, selector.findData(result.id)))
            self.status.setText(
                "Input inspected and source hashes recorded. Scientific review remains required."
            )
        elif self.pending == "revise-input":
            old_id = self.pending_revision_id
            self.pending_revision_id = None
            index = next((i for i, a in enumerate(self.artifacts) if a.id == old_id), None)
            if index is None:
                self.status.setText(
                    "Imported record changed during review; no replacement was made."
                )
                return
            self.artifacts[index] = result
            self.refresh()
            if result.kind == "anatomy":
                selector = self.registration.guided.imported_anatomy
                selector.setCurrentIndex(max(0, selector.findData(result.id)))
            self.status.setText(
                "Input declarations updated after source reinspection. Workflow eligibility and exact plan validation remain separate."
            )
        elif self.pending == "import-tracking-rois":
            imported = result["artifacts"] if isinstance(result, dict) else result
            metadata = result.get("metadata", {}) if isinstance(result, dict) else {}
            for artifact in imported:
                if all(a.id != artifact.id for a in self.artifacts):
                    self.artifacts.append(artifact)
            self.refresh()
            self.tracking.guided.set_imported_roi_metadata(metadata)
            self.roi_imported.emit(metadata)
            self.status.setText(
                "Selected viewer ROI snapshots imported. Review their roles and coordinate association before tracking."
            )
        elif self.pending == "inspect":
            self.status.setText("Selected sources unchanged; opening the existing viewer.")
            self.inspect_requested.emit(result)
        elif self.pending == "restore":
            self.artifacts, self.tasks = map(list, result)
            self.refresh()
            self.status.setText(
                "Selection restored after source reinspection; no processing started."
            )
        else:
            self.plan = result
            data = result.to_dict()
            lines = [
                f"Compatibility: {data['status']} · proposal only; use the supported execution workspace for command approval · scientific acceptance not assessed"
            ]
            lines.extend(
                f"{i['task']} · {i['severity'].upper()} · {i['message']}" for i in data["issues"]
            )
            for task in data["tasks"]:
                lines.extend(
                    [
                        "",
                        f"{task['id']} outputs (not produced): "
                        + ", ".join(task["expected_outputs"]),
                        "QC required: " + "; ".join(task["qc_requirements"]),
                    ]
                )
            self.findings.setPlainText("\n".join(lines))
            self.status.setText("Proposal checked; review findings. No command has executed.")
        terminal = State.COMPLETED
        if self.pending.endswith("-run"):
            status = result.get("status")
            terminal = (
                State.CANCELLED
                if status == "cancelled"
                else State.FAILED
                if status == "failed"
                else State.WARNING
                if result.get("validation", {}).get("findings")
                else State.COMPLETED
            )
        self.operation_state.update(self.operation_token, terminal, self.status.text())
        self.update_actions()

    def finished(self) -> None:
        """Retire the owned worker/thread and restore available controls."""
        thread, worker = self.thread, self.worker
        self.thread = self.worker = None
        if worker is not None:
            # Match the established batch retirement contract. The task closure
            # captures worker.progress and otherwise keeps a self-cycle alive
            # after the controller drops its reference, competing with deferred
            # Qt destruction during later Python garbage collection.
            worker.task = lambda: None
            self._retired_workers.append((thread, worker))
        if self.pending in {
            "dti-run",
            "csd-run",
            "tracking-run",
            "connectome-run",
            "anatomy-run",
            "registration-run",
            "sift2-run",
        }:
            self.execution_busy.emit(False)
        if thread:
            thread.deleteLater()
        product, self.product_handoff = self.product_handoff, None
        if product is not None:
            if self.pending == "tracking-view":
                self.tracking.view_requested.emit(product)
            elif self.pending == "registration-view":
                self.registration.view_requested.emit(product)
            elif self.pending == "anatomy-view":
                self.anatomy.view_requested.emit(product)
            else:
                self.csd.view_requested.emit(product)
        self.update_actions()

    def update_actions(self, *_: Any) -> None:
        """Expose only meaningful actions for the current selection and busy state."""
        from shiboken6 import isValid

        self._retired_workers = [
            (t, w) for t, w in self._retired_workers if isValid(t) or isValid(w)
        ]
        busy = self.thread is not None
        if hasattr(self, "registration"):
            self.registration.guided._show_imported_details()
        for b in (self.import_button, self.active_button, self.restore_button, self.save_button):
            b.setEnabled(not busy)
        self.add_button.setEnabled(not busy and self.algorithms.currentRow() >= 0)
        self.remove_input.setEnabled(not busy and self.inputs.currentRow() >= 0)
        row = self.inputs.currentRow()
        self.review_input.setEnabled(not busy and 0 <= row < len(self.artifacts))
        self.inspect.setEnabled(
            not busy
            and 0 <= row < len(self.artifacts)
            and self.artifacts[row].kind
            in {
                "anatomy",
                "dwi",
                "mask",
                "roi",
                "atlas_labels",
                "atlas_probability",
                "tissue_scalar",
                "tractogram",
                "fod",
                "response",
            }
            and (
                self.artifacts[row].kind in {"fod", "response"}
                or not str(self.artifacts[row].payload["path"]).lower().endswith(".mif")
            )
        )
        self.edit_task_button.setEnabled(not busy and self.task_list.currentRow() >= 0)
        self.remove_task_button.setEnabled(not busy and self.task_list.currentRow() >= 0)
        self.validate_button.setEnabled(not busy and bool(self.tasks))
        self.probe_button.setEnabled(not busy and bool(self.tasks))
        self.export_button.setEnabled(not busy and self.plan is not None)
        self.cancel_button.setVisible(busy)
        if hasattr(self, "dti"):
            self.dti.update_actions()
        if hasattr(self, "csd"):
            self.csd.update_actions()
        if hasattr(self, "tracking"):
            self.tracking.update_actions()
        if hasattr(self, "registration"):
            self.registration.update_actions()
        if hasattr(self, "anatomy"):
            self.anatomy.update_actions()
        if hasattr(self, "sift2"):
            self.sift2.update_actions()
        if hasattr(self, "connectome"):
            self.connectome.update_actions()

    def cancel_inspection(self) -> None:
        """Cancel the current request, not the Event from widget construction."""
        self.cancel.set()
        self.status.setText(
            "Cancelling current operation; incomplete attempts retained, original files unchanged…"
        )

    def shutdown(self) -> bool:
        """Drain read-only inspections on safe application close."""
        self.cancel.set()
        return self.thread is None
