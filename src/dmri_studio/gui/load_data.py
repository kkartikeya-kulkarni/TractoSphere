"""Single-dataset form and states, presenting the core report without revalidation.

Browse/drop suggestions are local, nonrecursive and reviewable. File existence
is only an input affordance; the worker performs all authoritative checks.
"""

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.dataset import DatasetPaths, DwiDataset
from dmri_studio.core.validation import ValidationReport

from .components import Card, Disclosure, ValidationPanel, button, label
from .discovery import detect_folder, role_for_file
from .preview import Preview
from .settings import Settings
from .theme import GAP, PAGE_INSET, Theme


class DropZone(Card):
    """Accessible folder picker with equivalent native URL drag/drop handling."""

    dropped = Signal(object)

    def __init__(self, theme: Theme, browse: object) -> None:
        """Use a modest surface, not an illustration competing with the form."""
        super().__init__(theme)
        self.body.setContentsMargins(16, 12, 16, 12)
        theme.bind(self, "bg.sidebar")
        self.setAcceptDrops(True)
        row = QHBoxLayout()
        row.addWidget(label("Drop a dataset folder or choose files"), 1)
        choose = button("Browse folder", browse)
        theme.set_icon(choose, "folder-open")
        row.addWidget(choose)
        self.body.addLayout(row)

    def dragEnterEvent(self, event: object) -> None:
        """Accept local paths only; remote URLs are not downloaded."""
        if event.mimeData().hasUrls() and all(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: object) -> None:
        """Hand local paths to the same suggestion route as Browse folder."""
        self.dropped.emit(
            [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        )
        event.acceptProposedAction()


class LoadDataPage(QWidget):
    """Load form, durable status, active preview, summary and report disclosures."""

    load_requested = Signal()
    cancel_requested = Signal()
    unload_requested = Signal()
    notification = Signal(str, str)
    open_image_requested = Signal()
    open_tracts_requested = Signal()
    open_project_requested = Signal()
    open_results_requested = Signal()
    open_scientific_requested = Signal()

    def __init__(self, theme: Theme, settings: Settings) -> None:
        """Start empty; no previously used image is opened automatically."""
        super().__init__()
        self.theme, self.settings = theme, settings
        self.dataset: DwiDataset | None = None
        self.busy = False
        self.state = "empty"
        self.import_intent = "unknown"
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(*([PAGE_INSET] * 4))
        self.root.setSpacing(GAP)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label("Data & imports", "title"))
        self.subtitle = label("Open a 4D diffusion image with its b-values and b-vectors.")
        titles.addWidget(self.subtitle)
        heading.addLayout(titles, 1)
        self.another = button("Load another dataset", self.show_form)
        self.unload = button("Unload", self.unload_requested.emit, quiet=True)
        heading.addWidget(self.another)
        heading.addWidget(self.unload)
        self.root.addLayout(heading)
        self.goals = QWidget()
        goals = QVBoxLayout(self.goals)
        goals.setContentsMargins(0, 16, 0, 16)
        goals.setSpacing(12)
        goals.addWidget(label("What would you like to open?", "section"))
        for title, description, callback in [
            (
                "Open image…",
                "Inspect anatomical or diffusion NIfTI images.",
                self.open_image_requested.emit,
            ),
            (
                "Import raw DWI",
                "Review matching diffusion gradients and acquisition metadata.",
                lambda: self.choose_intent("raw"),
            ),
            (
                "Import preprocessed DWI",
                "Keep external processing history unknown until evidence is reviewed.",
                lambda: self.choose_intent("preprocessed"),
            ),
            (
                "Open project…",
                "Resume subjects, acquisitions and recorded processing attempts.",
                self.open_project_requested.emit,
            ),
            (
                "Open tractography…",
                "Inspect existing TCK/TRK with a compatible anatomical reference.",
                self.open_tracts_requested.emit,
            ),
            (
                "Import scientific inputs…",
                "Inspect MIF, tensors, FODs, masks or 5TT independently; plan future algorithms.",
                self.open_scientific_requested.emit,
            ),
            (
                "Import existing results…",
                "Inspect an existing engine manifest without restarting processing.",
                self.open_results_requested.emit,
            ),
        ]:
            row = QHBoxLayout()
            control = button(title, callback, quiet=True)
            control.setMinimumWidth(220)
            control.setProperty("leftAligned", True)
            row.addWidget(control)
            row.addWidget(label(description, "caption"), 1)
            goals.addLayout(row)
        self.root.addWidget(self.goals)
        self.stepper = label(
            "01  Select files     /     02  Validate inputs     /     03  Explore dataset",
            "caption",
        )
        self.root.addWidget(self.stepper)
        self.banner = Card(theme)
        self.banner.body.setContentsMargins(8, 6, 8, 6)
        self.banner_title = label("", "caption")
        self.banner_detail = label("", "caption")
        banner_row = QHBoxLayout()
        banner_row.addWidget(self.banner_title, 1)
        self.validation_jump = button("View validation", self.jump_validation, quiet=True)
        banner_row.addWidget(self.validation_jump)
        self.banner.body.addLayout(banner_row)
        self.banner.body.addWidget(self.banner_detail)
        self.root.addWidget(self.banner)
        self.banner.hide()
        self.form = Card(theme, "Input files")
        self.intent_label = label("Diffusion inputs · processing history unknown", "control")
        self.form.body.addWidget(self.intent_label)
        self.form.body.addWidget(button("Choose another import type", self.show_goals, quiet=True))
        self.drop_zone = DropZone(theme, self.browse_folder)
        self.drop_zone.dropped.connect(self.drop_paths)
        self.form.body.addWidget(self.drop_zone)
        self.fields: dict[str, QLineEdit] = {}
        self.errors: dict[str, object] = {}
        self.browse_buttons = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        path_fields = QWidget()
        path_form = QFormLayout(path_fields)
        path_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        rows = [
            ("dwi", "DWI image — required", "4D NIfTI · .nii or .nii.gz"),
            ("bval", "b-values — required", "Whitespace text · one value per volume · s/mm²"),
            (
                "bvec",
                "b-vectors — required",
                "FSL 3 × N rows or N × 3 columns; matching image and processing stage",
            ),
            ("json", "JSON sidecar — optional", "Acquisition metadata · Not provided is valid"),
        ]
        for row, (role, title, help_text) in enumerate(rows):
            field = QLineEdit()
            field.setAccessibleName(title)
            field.setPlaceholderText(help_text)
            field.setToolTip(help_text)
            field.editingFinished.connect(lambda r=role: self.check_field(r))
            field.textChanged.connect(self.update_enabled)
            self.fields[role] = field
            field_label = label(title, "control")
            field_label.setBuddy(field)
            browse = button("Browse", lambda checked=False, r=role: self.browse(r))
            browse.setAccessibleName(f"Browse {title}")
            self.browse_buttons[role] = browse
            error = label("", "caption")
            theme.bind(error, "bg.surface", "error.fg")
            error.hide()
            self.errors[role] = error
            file_name = label("No file selected", "control")
            file_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            field.textChanged.connect(
                lambda text, target=file_name: self.show_file_name(target, text)
            )
            grid.addWidget(field_label, row * 2, 0)
            grid.addWidget(browse, row * 2, 1, 2, 1)
            grid.addWidget(file_name, row * 2 + 1, 0)
            path_form.addRow(title, field)
            path_form.addRow(error)
        grid.setColumnStretch(0, 1)
        self.form.body.addLayout(grid)
        self.path_disclosure = Disclosure("Full paths and manual entry", path_fields)
        self.form.body.addWidget(self.path_disclosure)
        self.layout_choice = QComboBox()
        self.layout_choice.addItem("Auto — FSL rows", "auto")
        self.layout_choice.addItem("3 rows (x, y, z)", "3xN")
        self.layout_choice.addItem("3 columns (x, y, z)", "Nx3")
        self.layout_choice.setAccessibleName("b-vector layout for ambiguous three-volume tables")
        self.layout_help = label(
            "For three volumes, both layouts are 3 × 3. Choose the layout supplied with this image.",
            "caption",
        )
        self.form.body.addWidget(self.layout_help)
        self.form.body.addWidget(self.layout_choice)
        self.layout_help.hide()
        self.layout_choice.hide()
        actions = QHBoxLayout()
        self.load_button = button("Load dataset", self.load_requested.emit, primary=True)
        self.clear_button = button("Clear selection", self.clear, quiet=True)
        actions.addWidget(self.load_button)
        actions.addWidget(self.clear_button)
        actions.addStretch()
        self.cancel_button = button("Cancel", self.cancel_requested.emit)
        actions.addWidget(self.cancel_button)
        self.cancel_button.hide()
        self.form.body.addLayout(actions)
        self.stage = label("", "caption")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.form.body.addWidget(self.stage)
        self.form.body.addWidget(self.progress)
        self.stage.hide()
        self.progress.hide()
        self.stage_message = "Starting loader…"
        self.started_at = 0.0
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self.update_elapsed)
        self.form.body.addWidget(
            label("Files are read in place. Your source data will not be modified.", "caption")
        )
        self.root.addWidget(self.form)
        self.failure: QWidget | None = None
        self.preview = Preview(theme, settings)
        self.root.addWidget(self.preview)
        self.preview.hide()
        self.details = QWidget()
        self.details_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.details)
        self.details_layout.setContentsMargins(0, 0, 0, 0)
        self.details_layout.setSpacing(GAP)
        self.details_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.root.addWidget(self.details)
        self.details.hide()
        self.sources: QWidget | None = None
        self.empty = Card(theme)
        self.empty.body.addWidget(label("No dataset loaded", "section"))
        self.empty.body.addWidget(
            label(
                "Choose your image and matching gradients. Dimensions, b-value groups and validation findings will appear here."
            )
        )
        theme.bind(self.empty, "bg.canvas", "text.muted")
        self.root.addWidget(self.empty)
        self.root.addStretch()
        self.another.hide()
        self.unload.hide()
        self.form.hide()
        self.empty.hide()
        self.stepper.hide()
        self.update_enabled()

    def show_file_name(self, target, text):
        """Readable identity with the complete editable path available on disclosure."""
        target.setText(Path(text.strip().strip("\"'")).name if text else "No file selected")
        target.setToolTip(text)

    def choose_intent(self, intent):
        """A user-declared entry point never asserts completed corrections."""
        self.import_intent = intent
        self.intent_label.setText(
            "Externally preprocessed DWI · history requires evidence"
            if intent == "preprocessed"
            else "Raw DWI · review acquisition metadata and history"
        )
        self.show_form()

    def show_goals(self):
        """Retain candidate paths and the active dataset while changing import type."""
        if not self.busy:
            self.form.hide()
            self.goals.show()

    def resizeEvent(self, event: object) -> None:
        """Reflow metadata cards below the approved content-width breakpoint."""
        self.details_layout.setDirection(
            QBoxLayout.Direction.TopToBottom
            if self.width() - 64 < 880
            else QBoxLayout.Direction.LeftToRight
        )
        inset = 24 if self.width() < 900 else PAGE_INSET
        self.root.setContentsMargins(inset, PAGE_INSET, inset, PAGE_INSET)
        super().resizeEvent(event)

    def path_text(self, role: str) -> str:
        """Allow pasted quoted paths deliberately, without ever invoking a shell."""
        text = self.fields[role].text().strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
            text = text[1:-1]
        return text

    def paths(self) -> DatasetPaths:
        """Snapshot form paths for the worker; JSON remains genuinely optional."""
        return DatasetPaths(
            **{r: Path(t).expanduser() if (t := self.path_text(r)) else None for r in self.fields}
        )

    def update_enabled(self, *_: object) -> None:
        """Gate the button on basic existing paths, without scientific parsing."""
        valid = all(
            self.path_text(r) and Path(self.path_text(r)).expanduser().is_file()
            for r in ("dwi", "bval", "bvec")
        )
        if self.path_text("json"):
            valid = valid and Path(self.path_text("json")).expanduser().is_file()
        valid = valid and self.path_text("dwi").lower().endswith((".nii", ".nii.gz"))
        self.load_button.setEnabled(bool(valid and not self.busy))

    def check_field(self, role: str) -> None:
        """Show field problems only after an edit is completed."""
        text = self.path_text(role)
        message = ""
        if text and not Path(text).expanduser().is_file():
            message = "File not found. Choose the matching file for this image."
        elif role == "dwi" and text and not text.lower().endswith((".nii", ".nii.gz")):
            message = "Choose a .nii or .nii.gz diffusion image."
        self.errors[role].setText(message)
        self.errors[role].setVisible(bool(message))
        if message:
            self.path_disclosure.toggle.setChecked(True)
        self.update_enabled()

    def browse(self, role: str) -> None:
        """Use native platform dialogs and remember role-specific folders."""
        if self.busy:
            return
        filters = {"dwi": "NIfTI images (*.nii *.nii.gz)", "json": "JSON metadata (*.json)"}
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Choose {self.fields[role].accessibleName()}",
            self.settings.folder(role),
            filters.get(role, "Gradient text (*)"),
        )
        if path:
            self.fields[role].setText(path)
            self.settings.set(f"folders/{role}", str(Path(path).parent))
            self.check_field(role)

    def browse_folder(self) -> None:
        """The keyboard alternative to dropping a local dataset folder."""
        path = QFileDialog.getExistingDirectory(
            self, "Choose dataset folder", self.settings.folder("dataset")
        )
        if path:
            self.fill_folder(Path(path))

    def choose_candidate(self, role: str, candidates: list[Path]) -> Path | None:
        """Require an explicit choice when multiple plausible files remain."""
        choice, ok = QInputDialog.getItem(
            self,
            "Choose which file to use",
            f"Multiple {role} candidates. Choose the file belonging to this image:",
            [p.name for p in candidates],
            0,
            False,
        )
        return next((p for p in candidates if p.name == choice), None) if ok else None

    def fill_folder(self, folder: Path) -> None:
        """Suggest same-folder files, never start loading or recurse participants."""
        if self.busy:
            return
        try:
            candidates = detect_folder(folder)
            self.clear()
            self.settings.set("folders/dataset", str(folder))
            images = candidates["dwi"]
            image = (
                images[0]
                if len(images) == 1
                else self.choose_candidate("DWI image", images)
                if images
                else None
            )
            if image:
                candidates = detect_folder(folder, image)
                candidates["dwi"] = [image]
            for role, choices in candidates.items():
                if role == "dwi" and image is None:
                    chosen = None
                else:
                    chosen = (
                        choices[0]
                        if len(choices) == 1
                        else self.choose_candidate(role, choices)
                        if choices
                        else None
                    )
                if chosen:
                    self.fields[role].setText(str(chosen))
                    self.settings.set(f"folders/{role}", str(folder))
                elif choices:
                    self.errors[role].setText("Choose which file to use.")
                    self.errors[role].show()
            self.show_form()
        except OSError as exc:
            self.notification.emit(f"Could not inspect folder: {exc}", "error")

    def drop_paths(self, paths: list[Path]) -> None:
        """Handle local folder or compatible individual file drops."""
        if self.busy:
            return
        if len(paths) == 1 and paths[0].is_dir():
            self.fill_folder(paths[0])
            return
        groups: dict[str, list[Path]] = {}
        for path in paths:
            if path.is_file() and (role := role_for_file(path)):
                groups.setdefault(role, []).append(path)
        for role, choices in groups.items():
            chosen = choices[0] if len(choices) == 1 else self.choose_candidate(role, choices)
            self.fields[role].setText(str(chosen) if chosen else "")
            self.check_field(role)
        self.show_form()

    def clear(self) -> None:
        """Clear only the candidate form, preserving any active dataset."""
        if self.busy:
            return
        for field in self.fields.values():
            field.clear()
        for error in self.errors.values():
            error.hide()
        self.layout_choice.setCurrentIndex(0)
        self.layout_choice.hide()
        self.layout_help.hide()

    def show_form(self) -> None:
        """Open candidate inputs while keeping the active dataset visible."""
        self.form.show()
        self.goals.hide()
        self.stepper.show()
        self.empty.hide()
        self.fields["dwi"].setFocus()
        if self.dataset is not None and not self.busy:
            self.show_banner(
                "Load another dataset",
                f"{self.dataset.paths.dwi.name} remains active until a new dataset loads successfully.",
                "info",
            )

    def show_banner(self, title: str, detail: str, tone: str) -> None:
        """Keep status persistent and readable beyond the transient toast."""
        self.banner_title.setText(title)
        self.banner_detail.setText(detail)
        self.theme.bind(self.banner, f"{tone}.bg", f"{tone}.fg")
        self.validation_jump.setVisible(tone in ("success", "warning", "error"))
        self.banner.show()

    def jump_validation(self) -> None:
        """Scroll to candidate failures first, otherwise the active report."""
        target = self.failure if self.failure is not None else self.details
        scroll = self.parentWidget()
        while scroll is not None and not hasattr(scroll, "ensureWidgetVisible"):
            scroll = scroll.parentWidget()
        if scroll is not None:
            scroll.ensureWidgetVisible(target, 0, 16)

    def update_elapsed(self) -> None:
        """Show elapsed wall time without implying a completion-time estimate."""
        self.stage.setText(
            f"{self.stage_message} · {int(time.monotonic() - self.started_at)} s elapsed"
        )

    def set_busy(self, busy: bool) -> None:
        """Disable duplicate transactions while keeping Cancel and shell controls live."""
        self.busy = busy
        if busy:
            self.started_at = time.monotonic()
            self.elapsed_timer.start()
        else:
            self.elapsed_timer.stop()
        for widget in [
            *self.fields.values(),
            *self.browse_buttons.values(),
            self.drop_zone,
            self.layout_choice,
            self.clear_button,
            self.another,
            self.unload,
        ]:
            widget.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy)
        self.progress.setVisible(busy)
        self.stage.setVisible(busy)
        self.load_button.setText("Loading…" if busy else "Load dataset")
        if busy:
            self.state = "loading"
            self.form.show()
            self.goals.hide()
            self.stepper.setText("✓ Select files     →     ② Validate · running     →     ③ Loaded")
            self.stage.setText("Starting loader…")
            self.banner.hide()
            self.stage_message = "Checking input metadata and bounded voxel samples"
            self.update_elapsed()
        self.update_enabled()

    def set_progress(self, progress: object) -> None:
        """Show the actual core stage, not a fabricated duration or ETA."""
        self.stage_message = f"{progress.stage.replace('_', ' ').title()} · {progress.message}"
        self.update_elapsed()

    def remove_failure(self) -> None:
        """Remove only the previous candidate report, not the active report."""
        if self.failure is not None:
            self.root.removeWidget(self.failure)
            self.failure.hide()
            self.failure.deleteLater()
            self.failure = None

    def present_report(self, report: ValidationReport, cancelled: bool = False) -> None:
        """Keep rejected-candidate evidence separate from active-dataset evidence."""
        self.remove_failure()
        self.state = "cancelled" if cancelled else "failed"
        self.stepper.setText(
            "① Select files     →     ② Validate · cancelled"
            if cancelled
            else "① Select files     →     ② Validate · failed"
        )
        retained = (
            f" Active dataset retained: {self.dataset.paths.dwi.name}." if self.dataset else ""
        )
        self.show_banner(
            "Loading cancelled" if cancelled else "Could not load dataset",
            report.coverage_message() + retained,
            "info" if cancelled else "error",
        )
        self.failure = ValidationPanel(
            self.theme, report, "Cancelled candidate" if cancelled else "Candidate validation"
        )
        self.root.insertWidget(self.root.indexOf(self.form), self.failure)
        self.form.show()
        # Core G03 evidence is the sole trigger for the ambiguity control.
        ambiguous = any(c.code == "G03" and "3×3" in c.message for c in report.checks)
        self.layout_choice.setVisible(ambiguous)
        self.layout_help.setVisible(ambiguous)
        if not cancelled and self.failure.rows:
            self.failure.rows[0].toggle.setFocus()

    def set_dataset(self, dataset: DwiDataset | None) -> None:
        """Present the accepted dataset; never infer a validation status locally."""
        self.dataset = dataset
        self.remove_failure()
        while self.details_layout.count():
            item = self.details_layout.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        if self.sources:
            self.root.removeWidget(self.sources)
            self.sources.hide()
            self.sources.deleteLater()
            self.sources = None
        self.preview.set_dataset(dataset)
        loaded = dataset is not None
        for widget in (self.preview, self.details, self.another, self.unload):
            widget.setVisible(loaded)
        self.empty.hide()
        self.form.hide()
        self.goals.setVisible(not loaded)
        self.stepper.setVisible(loaded)
        self.subtitle.setVisible(not loaded)
        if dataset is None:
            self.state = "empty"
            self.banner.hide()
            self.stepper.setText(
                "01  Select files     /     02  Validate inputs     /     03  Explore dataset"
            )
            self.clear()
            return
        summary = dataset.summary()
        ambiguous = any(c.code == "G03" and "3×3" in c.message for c in dataset.validation.checks)
        self.layout_choice.setVisible(ambiguous)
        self.layout_help.setVisible(ambiguous)
        warnings = dataset.validation.counts()["warn"]
        self.state = "warning" if warnings else "loaded"
        self.stepper.setText("✓ Select files     →     ✓ Validate     →     ✓ Loaded")
        self.show_banner(
            "Dataset loaded with warnings" if warnings else "Dataset loaded",
            (f"{warnings} warning(s). " if warnings else "")
            + dataset.validation.coverage_message(),
            "warning" if warnings else "success",
        )
        if not warnings:
            self.banner.hide()
        else:
            self.theme.bind(self.banner, "bg.surface", "text.primary")
            self.banner_title.setText("Input checks · review required")
        card = Card(self.theme, "Dataset summary")
        card.body.addWidget(label(dataset.paths.dwi.name, "section"))
        source = label(str(dataset.paths.dwi.parent), "caption")
        source.setToolTip(str(dataset.paths.dwi))
        card.body.addWidget(source)
        counts = QHBoxLayout()
        for value, text in [
            (dataset.n_volumes, "volumes"),
            (summary["b0_count"], "b0-like"),
            (summary["weighted_count"], "diffusion-weighted"),
        ]:
            stack = QVBoxLayout()
            stack.addWidget(label(str(value), "metric"))
            stack.addWidget(label(text, "caption"))
            counts.addLayout(stack)
        card.body.addLayout(counts)
        spacing = summary["voxel_sizes_mm"] or summary["voxel_sizes_native"]
        unit = "mm" if summary["voxel_sizes_mm"] else "units unknown"
        metadata = [
            ("Dimensions", " × ".join(map(str, dataset.shape))),
            ("Voxel size", " × ".join(f"{v:g}" for v in spacing) + f" {unit}"),
            (
                "Storage",
                f"{dataset.storage_dtype} · NIfTI-{dataset.metadata.get('nifti_version', 'unknown')}",
            ),
            (
                "Orientation",
                " / ".join(str(v) for v in dataset.axis_codes) + f" · {dataset.affine_source}"
                if dataset.affine_source != "fallback"
                else "Unverified · fallback affine",
            ),
        ]
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        for i, (name, value) in enumerate(metadata):
            grid.addWidget(label(name, "caption"), i, 0)
            grid.addWidget(label(value), i, 1)
        card.body.addLayout(grid)
        card.body.addWidget(label("b-value groups · s/mm²", "control"))
        table = QGridLayout()
        table.setHorizontalSpacing(12)
        table.setVerticalSpacing(8)
        for col, text in enumerate(("Group", "Median", "Range", "Volumes")):
            table.addWidget(label(text, "caption"), 0, col)
        for i, group in enumerate(summary["shells"], 1):
            values = (
                "b0-like" if group["is_b0"] else f"Group {i - bool(summary['b0_count'])}",
                f"{group['b_median']:g}",
                f"{group['b_min']:g}–{group['b_max']:g}",
                str(group["count"]),
            )
            for col, text in enumerate(values):
                table.addWidget(label(text, "caption"), i, col)
        card.body.addLayout(table)
        card.body.addWidget(label("Counts describe volumes, not unique directions.", "caption"))
        acquisition = []
        for key in ("PhaseEncodingDirection", "TotalReadoutTime", "EffectiveEchoSpacing"):
            check_code = "J02" if key == "PhaseEncodingDirection" else "J03"
            invalid = any(
                c.code == check_code
                and c.status == "warn"
                and (key in c.message or (isinstance(c.observed, dict) and key in c.observed))
                for c in dataset.validation.checks
            )
            value = (
                "Unusable metadata" if invalid else str(dataset.sidecar.get(key, "Not provided"))
            )
            acquisition.append(f"{key}: {value}")
        card.body.addWidget(
            Disclosure("Acquisition metadata", label("\n".join(acquisition), "caption"))
        )
        self.details_layout.addWidget(card, 55)
        panel = ValidationPanel(self.theme, dataset.validation)
        self.details_layout.addWidget(panel, 45)
        self.sources = Disclosure(
            "Input files · review source paths",
            label("\n".join(f"{r}: {p}" for r, p in dataset.paths.supplied().items()), "caption"),
        )
        self.root.insertWidget(self.root.count() - 1, self.sources)
