"""History confirmation and individual-operation planning for an active DWI.

The existing Project page remains the sole importer and image viewer. This
workspace edits evidence; its separate execution panel uses the shared core API.
"""

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.dataset import DatasetReadError, DwiDataset
from dmri_studio.core.workflow import (
    OPERATIONS,
    SOURCES,
    STAGES,
    HistoryClaim,
    ProcessingHistory,
    WorkflowPlan,
    assess_operations,
    build_plan,
    export_plan,
)

from .components import Card, Disclosure, button, label
from .execution import ExecutionPanel
from .theme import GAP, PAGE_INSET, Theme
from .workstation import EmptyState

HCP_INTERPOLATION_SOURCE = "https://github.com/Washington-University/HCPpipelines/blob/6d695803a342f51660a381f703d0e22b2ffe4779/DiffusionPreprocessing/scripts/DiffusionToStructural.sh"

OPERATION_GUIDANCE = {
    "convert": "Prepare a copy in MRtrix format with the declared gradients; source files stay unchanged.",
    "denoise": "Estimate and reduce noise with the supported MP-PCA method. Suitability and its position before EDDY remain separate scientific review questions.",
    "gibbs": "Reduce ringing associated with finite k-space sampling where the input history permits it.",
    "mask": "Estimate a brain mask. Inspect its coverage before accepting it for later operations.",
    "correction": "Use associated calibration and acquisition metadata for TOPUP/EDDY motion and distortion correction; inspect diagnostics and replacements.",
    "bias": "Apply N4 only to a verified direct EDDY derivative with the required fitting mask and evidence.",
    "anatomy": "Prepare structural anatomy and a white-matter candidate for separate anatomical review.",
    "alignment": "Estimate low-b to reviewed T1 alignment; successful execution does not establish anatomical correctness.",
    "combine": "Create a combined AP/PA derivative only from an explicitly matched, verified pair.",
}

assert set(OPERATION_GUIDANCE) == set(OPERATIONS)


class WorkflowPage(QWidget):
    """Keep unconfirmed edits separate from the evidence used by the planner."""

    notification = Signal(str, str)
    import_requested = Signal()

    def __init__(self, theme: Theme) -> None:
        """Create an explicit, session-only history editor with no automatic claims."""
        super().__init__()
        self.theme = theme
        self.dataset: DwiDataset | None = None
        self.history = ProcessingHistory()
        self.plan: WorkflowPlan | None = None
        self.inferred_stages: set[str] = set()
        root = QVBoxLayout(self)
        root.setContentsMargins(*([PAGE_INSET] * 4))
        root.setSpacing(GAP)
        root.addWidget(label("Preprocessing plan", "title"))
        root.addWidget(
            label(
                "Review evidence, configure supported operations, approve exact commands, then inspect results.",
                "caption",
            )
        )
        self.summary = label("Import diffusion data to plan supported preprocessing operations.")
        root.addWidget(self.summary)
        self.columns = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.columns.setSpacing(16)
        root.addLayout(self.columns)
        history_card = Card(theme, "1 · Confirm processing history")
        self.history_card = history_card
        self.columns.addWidget(history_card, 1)
        history_card.body.addWidget(
            label(
                "File names and header descriptions do not verify completed preprocessing. Record only what applies to this input.",
                "caption",
            )
        )
        self.template = QComboBox()
        self.template.setAccessibleName("History starting point")
        self.template.addItems(
            [
                "Custom / unknown history",
                "HCP reference history — review",
                "Raw acquisition — review",
            ]
        )
        history_card.body.addWidget(self.template)
        self.template.currentIndexChanged.connect(self.apply_template)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.states = {}
        for row, (stage, title) in enumerate(STAGES.items()):
            state = QComboBox()
            state.setAccessibleName(f"{title} history")
            state.addItem("Unknown", None)
            state.addItem("Not done", False)
            state.addItem("Completed", True)
            state.addItem("Conflicting evidence", "conflict")
            grid.addWidget(label(title, "control"), row, 0)
            grid.addWidget(state, row, 1)
            state.currentIndexChanged.connect(lambda _, s=stage: self.stage_edited(s))
            self.states[stage] = state
        history_card.body.addLayout(grid)
        history_card.body.addWidget(label("History source / explanation", "control"))
        self.source = QLineEdit()
        self.source.setAccessibleName("Processing history source or explanation")
        self.source.setPlaceholderText(
            "Download description, upstream log, or your processing record"
        )
        self.source.textChanged.connect(self.invalidate)
        history_card.body.addWidget(self.source)
        history_card.body.addWidget(label("Confidence in these claims", "control"))
        self.confidence = QComboBox()
        self.confidence.setAccessibleName("History confidence")
        self.confidence.addItems(["low", "medium", "high"])
        self.confidence.setCurrentText("medium")
        self.confidence.currentIndexChanged.connect(self.invalidate)
        history_card.body.addWidget(self.confidence)
        self.confirm = button("Confirm history", self.confirm_history, primary=True)
        history_card.body.addWidget(self.confirm)
        self.history_status = label("History has not been confirmed.", "caption")
        history_card.body.addWidget(self.history_status)

        operation_card = Card(theme, "2 · Select individual operations")
        self.operation_card = operation_card
        self.columns.addWidget(operation_card, 1)
        operation_card.body.addWidget(
            label(
                "Choose the outcome you need. Conversion is added once when another operation needs MRtrix input.",
                "caption",
            )
        )
        self.operation_list = QListWidget()
        self.operation_list.setAccessibleName("Available preprocessing operations")
        self.operation_list.setWordWrap(True)
        self.operation_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.operation_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.operation_pages = QStackedWidget()
        self.operation_purpose = label("", "caption")
        operation_card.body.addWidget(self.operation_purpose)
        browser = QSplitter()
        browser.addWidget(self.operation_list)
        browser.addWidget(self.operation_pages)
        browser.setStretchFactor(0, 2)
        browser.setStretchFactor(1, 3)
        operation_card.body.addWidget(browser, 1)
        self.operation_list.currentRowChanged.connect(self.operation_pages.setCurrentIndex)
        self.operation_list.currentRowChanged.connect(self.show_operation_purpose)
        self.selections, self.method_statuses, self.method_details = {}, {}, {}
        for operation, title in OPERATIONS.items():
            selection = button("Select · " + title)
            selection.setCheckable(True)
            selection.setProperty("leftAligned", True)
            selection.setAccessibleName(title)
            selection.toggled.connect(
                lambda checked, op=operation: self.selection_changed(op, checked)
            )
            self.selections[operation] = selection
            self.operation_list.addItem(title)
            self.operation_list.item(self.operation_list.count() - 1).setToolTip(title)
            detail_page = QWidget()
            operation_layout = QVBoxLayout(detail_page)
            operation_layout.setContentsMargins(16, 4, 4, 4)
            operation_layout.addWidget(label(title, "section"))
            operation_layout.addWidget(selection)
            self.operation_pages.addWidget(detail_page)
            status = label("Confirm history to assess eligibility.", "caption")
            operation_layout.addWidget(status)
            self.method_statuses[operation] = status
            details = label("")
            disclosure = Disclosure("Requirements and method guidance", details, expanded=True)
            self.method_details[operation] = details
            operation_layout.addWidget(disclosure)
            operation_layout.addStretch()
        self.operation_list.setCurrentRow(0)
        operation_card.body.addWidget(
            label(
                "TOPUP/EDDY requires associated calibration and acquisition metadata. N4 requires a passing pre-N4 EDDY handoff and an explicit fitting mask. Anatomy requires explicit input history; alignment requires reviewed anatomy. Optional AP/PA combination requires verified full-diffusion pairing. Unknown history stays unknown.",
                "caption",
            )
        )

        self.review = Card(theme, "3 · Review proposal")
        root.addWidget(self.review)
        self.plan_status = label("Confirm history and select an operation.")
        self.review.body.addWidget(self.plan_status)
        self.plan_detail = label("")
        self.review.body.addWidget(self.plan_detail)
        actions = QHBoxLayout()
        self.export = button("Export proposal JSON", self.export_proposal)
        self.export.setEnabled(False)
        actions.addWidget(self.export)
        actions.addStretch()
        self.review.body.addLayout(actions)
        self.review.body.addWidget(
            label(
                "Proposal export remains planning only. Prepare exact commands in Execute & monitor; approval is a separate action.",
                "caption",
            )
        )
        self.execution = ExecutionPanel(theme, self.execution_context)
        root.addWidget(self.execution)
        self.execution.notification.connect(self.notification)
        self.execution.busy_changed.connect(self.execution_busy)
        # Reparent the existing editors: the planner and execution services are unchanged.
        self.steps = QTabWidget()
        self.steps.setAccessibleName("Preprocessing workflow stages")
        for title, content in (
            ("1  Input history", history_card),
            ("2  Operations", operation_card),
            ("3  Review proposal", self.review),
            ("4  Execute && monitor", self.execution),
        ):
            self.columns.removeWidget(content)
            root.removeWidget(content)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            self.steps.addTab(scroll, title)
        root.addWidget(self.steps, 1)
        self.empty = EmptyState(
            "No diffusion input loaded",
            "Import raw or externally preprocessed DWI, then review its history before planning corrections.",
            [("Select diffusion data", self.import_requested.emit)],
        )
        root.addWidget(self.empty, 1)
        self.stage_navigation = QWidget()
        navigation = QHBoxLayout(self.stage_navigation)
        navigation.setContentsMargins(0, 0, 0, 0)
        self.previous_stage = button(
            "Previous", lambda: self.steps.setCurrentIndex(self.steps.currentIndex() - 1)
        )
        self.next_stage = button(
            "Next stage", lambda: self.steps.setCurrentIndex(self.steps.currentIndex() + 1)
        )
        navigation.addWidget(self.previous_stage)
        navigation.addStretch()
        navigation.addWidget(self.next_stage)
        self.steps.currentChanged.connect(self.update_stage_navigation)
        root.addWidget(self.stage_navigation)
        self.setMinimumHeight(560)
        self.set_dataset(None)

    def update_stage_navigation(self, *_):
        """Stage navigation never confirms history or authorizes execution."""
        self.previous_stage.setEnabled(self.steps.currentIndex() > 0)
        self.next_stage.setEnabled(self.steps.currentIndex() < self.steps.count() - 1)

    def show_operation_purpose(self, row: int) -> None:
        """Reuse one explanation widget when researchers inspect different stages."""
        operation = tuple(OPERATIONS)[row] if 0 <= row < len(OPERATIONS) else None
        self.operation_purpose.setText(
            OPERATION_GUIDANCE[operation] if operation else "Choose a supported operation."
        )

    def resizeEvent(self, event: object) -> None:
        """Stack cards at narrow widths, without squeezing labels or controls."""
        super().resizeEvent(event)
        self.columns.setDirection(
            QBoxLayout.Direction.LeftToRight
            if self.width() >= 1000
            else QBoxLayout.Direction.TopToBottom
        )

    def selection_changed(self, operation: str, checked: bool) -> None:
        """Make selection legible in both themes using shared button tokens."""
        control = self.selections[operation]
        control.setText(("Selected · " if checked else "Select · ") + OPERATIONS[operation])
        control.setProperty("selected", checked)
        self.theme.bind(
            control,
            "accent.subtle" if checked else "bg.surface",
            "accent.text" if checked else "text.primary",
        )
        self.update_plan()
        if hasattr(self, "execution"):
            self.execution.invalidate()

    def set_dataset(self, dataset: DwiDataset | None) -> None:
        """Discard evidence and selections only after a successful input replacement."""
        self.dataset = dataset
        self.empty.setVisible(dataset is None)
        self.steps.setVisible(dataset is not None)
        self.stage_navigation.setVisible(dataset is not None)
        self.update_stage_navigation()
        self.template.blockSignals(True)
        self.template.setCurrentIndex(0)
        self.template.blockSignals(False)
        for state in self.states.values():
            state.setCurrentIndex(0)
        self.inferred_stages.clear()
        self.source.clear()
        for selection in self.selections.values():
            selection.setChecked(False)
            selection.setEnabled(dataset is not None)
        self.confirm.setEnabled(dataset is not None)
        self.summary.setText(
            "Import diffusion data to plan supported preprocessing operations."
            if dataset is None
            else f"{dataset.paths.dwi.name} · {' × '.join(map(str, dataset.shape))}\n{dataset.validation.coverage_message()}"
        )
        self.invalidate()
        if hasattr(self, "execution"):
            self.execution.reset()

    def import_verified_history(self, document):
        """Carry exact saved claims forward after a verified output load."""
        history = ProcessingHistory.from_dict(document)
        for stage, control in self.states.items():
            control.blockSignals(True)
            control.setCurrentIndex(
                {"unknown": 0, "not_done": 1, "done": 2, "conflict": 3}[history.resolve(stage)]
            )
            control.blockSignals(False)
        self.source.setText(
            "Imported saved processing evidence. New edits become user declarations; see Results for original sources."
        )
        self.history = history
        self.history_status.setText(
            "Saved output history imported from checked producer evidence; completion claims remain inferred from those records. Unknown upstream stages remain unknown. Scientific acceptance is pending."
        )
        self.update_plan()

    def stage_edited(self, stage: str) -> None:
        """A direct completion edit becomes a user claim rather than a template inference."""
        self.inferred_stages.discard(stage)
        self.invalidate()

    def apply_template(self, index: int) -> None:
        """Propose editable claims only after an explicit template selection."""
        for stage, state in self.states.items():
            state.setCurrentIndex(
                0
                if index == 0 or index == 1 and stage in {"denoise", "gibbs", "bias"}
                else 1
                if index == 2
                else 2
            )
        self.inferred_stages = {"interpolation"} if index == 1 else set()
        self.source.setText(
            "User-reported HCP minimally preprocessed download. Interpolation is inferred from the HCP pipeline; producing logs not supplied."
            if index == 1
            else ""
        )
        self.source.setCursorPosition(0)
        self.invalidate()

    def invalidate(self, *_: object) -> None:
        """Any evidence edit invalidates confirmation and the displayed proposal."""
        self.history = ProcessingHistory()
        self.plan = None
        self.export.setEnabled(False)
        self.history_status.setText(
            "Review these claims and confirm them for this imported dataset. Confirmation does not verify upstream logs."
        )
        for status in self.method_statuses.values():
            status.setText("Confirm history to assess eligibility.")
        for index, title in enumerate(OPERATIONS.values()):
            self.operation_list.item(index).setText(title)
        for details in self.method_details.values():
            details.clear()
        self.plan_status.setText("Confirm history and select an operation.")
        self.plan_detail.clear()
        if hasattr(self, "execution"):
            self.execution.invalidate()

    def execution_context(self) -> tuple:
        """Give the replaceable execution view the current core objects/choices."""
        return (
            self.dataset,
            self.history,
            tuple(op for op, control in self.selections.items() if control.isChecked()),
        )

    def execution_busy(self, busy: bool) -> None:
        """Keep the approved context fixed while hashing, running or saving QC."""
        self.history_card.setEnabled(not busy)
        self.operation_card.setEnabled(not busy)
        self.review.setEnabled(not busy)

    def confirm_history(self) -> None:
        """Label entered facts honestly and preserve unknown/conflicting entries."""
        if self.dataset is None:
            return
        try:
            claims = []
            for stage, state in self.states.items():
                value = state.currentData()
                evidence = (
                    "conflict"
                    if value == "conflict"
                    else "unknown"
                    if value is None
                    else "inferred"
                    if stage in self.inferred_stages
                    else "user_declared"
                )
                claims.append(
                    HistoryClaim(
                        stage,
                        None if value == "conflict" else value,
                        evidence,
                        self.source.text().strip()
                        + (
                            " Expected interpolation basis: "
                            + HCP_INTERPOLATION_SOURCE
                            + "; actual producing version remains unknown."
                            if stage in self.inferred_stages
                            else ""
                        ),
                        "unknown" if value is None else self.confidence.currentText(),
                        date.today().isoformat(),
                    )
                )
            self.history = ProcessingHistory(tuple(claims), confirmed=True)
            self.history_status.setText(
                "History confirmed for this session. Claims remain user-declared or unknown."
                + (" HCP interpolation remains inferred." if self.inferred_stages else "")
            )
            self.update_plan()
        except (ValueError, DatasetReadError) as exc:
            self.history_status.setText(str(exc))
            self.notification.emit(str(exc), "error")

    def update_plan(self, *_: object) -> None:
        """Use the same method decisions and proposal builder as the CLI/API."""
        if self.dataset is None or not self.history.confirmed:
            return
        try:
            decisions = assess_operations(self.dataset, self.history)
            for decision in decisions:
                index = list(OPERATIONS).index(decision.operation)
                checked = self.selections[decision.operation].isChecked()
                self.operation_list.item(index).setText(
                    ("Selected · " if checked else "")
                    + OPERATIONS[decision.operation]
                    + "\n"
                    + decision.status.capitalize()
                )
                self.operation_list.item(index).setToolTip(
                    OPERATIONS[decision.operation] + "\n" + "; ".join(decision.reasons)
                )
                self.method_statuses[decision.operation].setText(
                    decision.status.capitalize()
                    + " · "
                    + (
                        decision.reasons[0]
                        if decision.reasons
                        else "Review the operation details below."
                    )
                )
                self.method_details[decision.operation].setText(
                    "Required: "
                    + "; ".join(decision.required_inputs)
                    + "\nOptional: "
                    + "; ".join(decision.optional_inputs)
                    + "\nOutputs: "
                    + ", ".join(decision.expected_outputs)
                    + "\n"
                    + "\n".join(decision.reasons)
                    + "\n"
                    + SOURCES[decision.operation]
                )
            selected = tuple(op for op, checkbox in self.selections.items() if checkbox.isChecked())
            self.plan = build_plan(self.dataset, self.history, selected)
            payload = self.plan.to_dict()
            self.plan_status.setText(
                payload["status"].capitalize()
                + " proposal · "
                + (
                    " → ".join(OPERATIONS[node["operation"]] for node in payload["nodes"])
                    or "No operations selected"
                )
            )
            self.plan_detail.setText(
                "\n".join(
                    payload["reasons"]
                    + [
                        f"{node['title']}: {node['status']} · " + " ".join(node["reasons"])
                        for node in payload["nodes"]
                    ]
                )
            )
            self.export.setEnabled(bool(selected))
        except DatasetReadError as exc:
            self.plan = None
            self.export.setEnabled(False)
            self.plan_status.setText(str(exc))
            self.plan_detail.clear()

    def export_proposal(self) -> None:
        """Recheck current input evidence and use exclusive report publication."""
        self.update_plan()
        if self.plan is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export workflow proposal", "workflow-proposal.json", "JSON (*.json)"
        )
        if not path:
            return
        try:
            # Dialog interaction can outlive an input change: recheck again.
            self.update_plan()
            if self.plan is None:
                return
            saved = export_plan(self.plan, Path(path))
            self.notification.emit(f"Proposal exported: {saved}", "success")
        except (ValueError, OSError, RuntimeError) as exc:
            self.notification.emit(str(exc), "error")
