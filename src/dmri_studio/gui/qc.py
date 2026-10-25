"""Existing-evidence QC workspace; imports, reviews and exports without analysis."""

import json
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.combined_qc import build_combined_report_from_folder
from dmri_studio.core.comparison import validate_comparison
from dmri_studio.core.dataset import DatasetPaths
from dmri_studio.core.execution import hash_file
from dmri_studio.core.loading import DatasetLoader
from dmri_studio.core.qc import (
    export_report,
    load_catalog,
    load_review,
    read_artifact,
    record_review,
)

from .comparison import ComparisonWindow
from .components import button, label
from .execution import ProcessingWorker
from .theme import Theme
from .workstation import EmptyState


class QCPage(QWidget):
    """Inspect producer evidence with independent status dimensions and human review."""

    notification = Signal(str, str)

    def __init__(self, theme: Theme) -> None:
        """Keep heavy file hashing and image loading outside the event thread."""
        super().__init__()
        self.theme = theme
        self.reviews = {}
        self.catalog = None
        self.thread = self.worker = None
        self.cancel = Event()
        self.windows = []
        self.closing = False
        self.current_artifacts = []
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)
        root.addWidget(label("Quality Control", "title"))
        root.addWidget(
            label(
                "Review saved evidence from each operation. Opening QC does not rerun processing or accept results.",
                "caption",
            )
        )
        top = QHBoxLayout()
        self.open_button = button("Open Report…", self.browse)
        top.addWidget(self.open_button)
        self.combined_button = button("Create dataset QC document…", self.create_combined)
        self.combined_button.setToolTip(
            "Collect verified evidence for the steps run in one dataset folder into a single HTML document."
        )
        top.addWidget(self.combined_button)
        self.identity = label("No QC catalog loaded", "control")
        top.addWidget(self.identity, 1)
        root.addLayout(top)
        self.status = label("Choose the catalog produced for a processing attempt.", "caption")
        root.addWidget(self.status)
        self.empty = EmptyState(
            "No QC report open",
            "Open a saved QC report to review processing evidence.",
            [
                ("Open Report…", self.browse),
                ("Create dataset QC document…", self.create_combined),
            ],
        )
        root.addWidget(self.empty, 1)
        split = QSplitter()
        self.content = split
        self.stages = QListWidget()
        self.stages.setWordWrap(True)
        self.stages.setAccessibleName("Processing steps and independent QC status")
        split.addWidget(self.stages)
        body = QWidget()
        layout = QVBoxLayout(body)
        self.check_labels = {}
        checks = QGridLayout()
        for index, (key, title) in enumerate(
            (
                ("execution", "Execution"),
                ("technical", "Technical checks"),
                ("automated_qc", "Automated QC"),
                ("scientific_review", "Scientific review"),
            )
        ):
            value = label(title + " · No evidence", "control")
            self.check_labels[key] = value
            checks.addWidget(value, index // 2, index % 2)
        layout.addLayout(checks)
        self.findings = label("", "body")
        layout.addWidget(self.findings)
        self.artifacts = QComboBox()
        self.artifacts.setAccessibleName("Available step evidence")
        layout.addWidget(self.artifacts)
        actions = QHBoxLayout()
        self.open_report = button("Open report", self.view_report)
        self.open_report.setToolTip("Select a report artifact to open its verified HTML.")
        self.export = button("Export report", self.save_report)
        self.export.setToolTip("Select a report artifact to export its unchanged bytes.")
        self.compare = button("Compare images", self.open_comparison)
        self.compare.setToolTip(
            "Requires a saved matched-image comparison artifact with verified gradients and volume mapping."
        )
        for widget in (self.open_report, self.export, self.compare):
            actions.addWidget(widget)
        layout.addLayout(actions)
        self.figure_zoom = QComboBox()
        self.figure_zoom.addItems(["Fit figure", "100% pixels", "200% pixels"])
        self.figure_zoom.setAccessibleName("QC figure display scale")
        self.figure_zoom.currentIndexChanged.connect(self.scale_figure)
        layout.addWidget(self.figure_zoom)
        self.original_figure = None
        self.figure = QLabel()
        self.figure.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.figure.setAccessibleName("Saved QC figure")
        self.figure_scroll = QScrollArea()
        self.figure_scroll.setWidgetResizable(True)
        self.figure_scroll.setWidget(self.figure)
        layout.addWidget(self.figure_scroll, 1)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setAccessibleName("Saved metrics, provenance and logs")
        layout.addWidget(self.text, 1)
        tabs = QTabWidget()
        tabs.setAccessibleName("QC evidence and scientific review")
        evidence = QWidget()
        evidence_layout = QVBoxLayout(evidence)
        evidence_layout.setContentsMargins(0, 8, 0, 0)
        for widget in (self.figure_zoom, self.figure_scroll):
            layout.removeWidget(widget)
            evidence_layout.addWidget(widget, 0 if widget is self.figure_zoom else 1)
        tabs.addTab(evidence, "Figures")
        self.measurements = QTreeWidget()
        self.measurements.setHeaderLabels(["Recorded measurement", "Value"])
        self.measurements.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.measurements.setAccessibleName("Producer measurements; no recalculation")
        tabs.addTab(self.measurements, "Measurements")
        layout.removeWidget(self.text)
        tabs.addTab(self.text, "Source details")
        review_page = QWidget()
        review_layout = QVBoxLayout(review_page)
        review_layout.addWidget(label("Scientific review", "section"))
        review_layout.addWidget(
            label(
                "Execution and technical verification do not establish scientific acceptance. Record your assessment of the selected stage with supporting evidence.",
                "caption",
            )
        )
        tabs.addTab(review_page, "Scientific review")
        layout.addWidget(tabs, 1)
        self.review_tabs = tabs
        review = QGridLayout()
        self.reviewer = QLineEdit()
        self.reviewer.setPlaceholderText("Reviewer name")
        self.reviewer.setAccessibleName("Scientific reviewer")
        self.decision = QComboBox()
        self.decision.addItems(["needs_review", "accepted", "rejected"])
        review.addWidget(self.reviewer, 0, 0)
        review.addWidget(self.decision, 0, 1)
        self.record = button("Record review…", self.save_review)
        review.addWidget(self.record, 1, 0)
        self.import_review = button("Open saved review…", self.browse_review)
        review.addWidget(self.import_review, 1, 1)
        review_layout.addLayout(review)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Describe coverage, artifacts and remaining uncertainties")
        self.reason.setAccessibleName("Scientific review reason")
        review_layout.addWidget(self.reason)
        review_layout.addStretch()
        split.addWidget(body)
        split.setStretchFactor(1, 1)
        split.setSizes([240, 900])
        root.addWidget(split, 1)
        self.stages.currentRowChanged.connect(self.select_stage)
        self.artifacts.currentIndexChanged.connect(self.select_artifact)
        self.update_actions()

    def scale_figure(self, *_: object) -> None:
        """Fit saved figures initially, with explicit full-resolution inspection."""
        if self.original_figure is None:
            return
        image = self.original_figure
        if self.figure_zoom.currentIndex() == 0:
            size = self.figure_scroll.viewport().size()
            image = image.scaled(
                size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        elif self.figure_zoom.currentIndex() == 2:
            image = image.scaled(
                image.size() * 2,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        self.figure.setPixmap(QPixmap.fromImage(image))
        self.figure.setMinimumSize(image.size())

    def resizeEvent(self, event: object) -> None:
        """Keep fit mode legible as the workspace changes size."""
        super().resizeEvent(event)
        self.scale_figure()

    def browse(self) -> None:
        """Choose a producer catalog rather than guess statuses from a folder name."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open saved QC report or catalog", "", "QC evidence (*.json *.html *.htm)"
        )
        if path:
            if Path(path).suffix.lower() in {".html", ".htm"}:
                opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))
                self.status.setText(
                    "Opened saved HTML in your browser; no catalog statuses were inferred."
                    if opened
                    else "The browser could not open this report. Choose another browser or open the file directly."
                )
            else:
                self.load(Path(path))

    def load(self, path: Path) -> None:
        """Verify catalog/evidence hashes in a worker, retaining prior evidence on error."""
        self.start(
            "catalog",
            lambda progress: load_catalog(path, cancelled=self.cancel.is_set, progress=progress),
        )

    def start(self, kind: str, task) -> None:
        """Own one finite read/write worker; no subprocess is launched here."""
        if self.thread is not None:
            return
        self.kind = kind
        self.cancel = Event()
        self.thread = QThread(self)
        self.worker = ProcessingWorker(0, lambda: None)
        worker = self.worker
        worker.task = lambda: task(worker.progress.emit)
        worker.moveToThread(self.thread)
        self.thread.started.connect(worker.run)
        worker.progress.connect(self.status.setText)
        worker.result.connect(self.result)
        worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.thread.finished.connect(worker.deleteLater)
        self.thread.finished.connect(self.finished)
        self.update_actions()
        self.status.setText(
            "Reading existing evidence…"
            if kind in {"catalog", "artifact", "comparison"}
            else "Building dataset QC document…"
            if kind == "combined"
            else "Saving review/export…"
        )
        self.thread.start()

    def result(self, _: int, result: object, error: str) -> None:
        """Display actual source evidence without converting warnings to acceptance."""
        if error:
            if self.kind == "catalog":
                self.catalog = None
                self.current_artifacts = []
                self.stages.clear()
                self.artifacts.clear()
            self.status.setText(error)
            self.notification.emit(error, "error")
            return
        if self.closing:
            if self.kind == "comparison":
                for dataset in result[0].values():
                    dataset.close()
            return
        if self.kind == "catalog":
            self.catalog = result
            self.reviews = {}
            data = result.to_dict()
            self.identity.setText(" · ".join(f"{k}: {v}" for k, v in data["identity"].items()))
            self.stages.blockSignals(True)
            self.stages.clear()
            for stage in data["stages"]:
                self.stages.addItem(
                    f"{stage.get('title', stage['id'])}\nExecution: {stage['execution']}\nScientific review: required"
                )
            self.stages.blockSignals(False)
            self.status.setText("Evidence hashes verified. Scientific review remains required.")
        elif self.kind == "artifact":
            if isinstance(result, QImage):
                self.review_tabs.setCurrentIndex(0)
                self.original_figure = result
                self.scale_figure()
                self.figure_zoom.show()
                self.figure_scroll.show()
                QTimer.singleShot(0, self.scale_figure)
            else:
                self.figure_zoom.hide()
                self.present_measurements(result)
                self.figure_scroll.hide()
            self.status.setText("Showing existing evidence; no analysis was rerun.")
        elif self.kind == "comparison":
            datasets, manifest = result
            try:
                window = ComparisonWindow(self.theme, datasets, manifest)
            except Exception:
                for dataset in datasets.values():
                    dataset.close()
                raise
            self.windows.append(window)
            window.show()
            self.status.setText("Matched images opened with explicit volume correspondence.")
        elif self.kind in {"review", "load_review"}:
            self.reviews[result["stage"]] = result
            index = next(
                i
                for i, stage in enumerate(self.catalog.to_dict()["stages"])
                if stage["id"] == result["stage"]
            )
            self.check_labels["scientific_review"].setText(
                f"Scientific review · {result['decision']} (local record)"
            )
            self.theme.bind(
                self.check_labels["scientific_review"],
                "bg.canvas",
                "success.fg"
                if result["decision"] == "accepted"
                else "error.fg"
                if result["decision"] == "rejected"
                else "warning.fg",
            )
            item = self.stages.item(index)
            item.setText(
                item.text().split("Scientific review:")[0]
                + f"Scientific review: {result['decision']} (local user record)"
            )
            self.status.setText(
                f"Review: {result['decision']} · {result['reviewer']} · {result['reason']} · {result['review_path']} · Identity not authenticated; producer checks unchanged."
            )
        elif self.kind == "browser":
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(result)))
            self.status.setText("Opened verified existing report in your browser.")
        elif self.kind == "combined":
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(result)))
            self.status.setText(
                f"Dataset QC document saved: {result}. "
                + ("Opened in your browser." if opened else "Open the file in your browser.")
            )
            self.notification.emit(self.status.text(), "success")
        else:
            self.status.setText(f"Saved: {result}")
            self.notification.emit(self.status.text(), "success")

    def finished(self) -> None:
        """Retire workers before starting selected-artifact reads."""
        self.thread.wait()
        self.thread.deleteLater()
        self.thread = self.worker = None
        self.update_actions()
        if self.kind == "catalog" and self.catalog and not self.closing:
            self.stages.setCurrentRow(0)
            self.select_stage(0)

    def select_stage(self, index: int) -> None:
        """Show findings and source lists exactly as the producer associated them."""
        if not self.catalog or index < 0 or self.thread:
            return
        stage = self.catalog.to_dict()["stages"][index]
        for key, title in (
            ("execution", "Execution"),
            ("technical", "Technical checks"),
            ("automated_qc", "Automated QC"),
            ("scientific_review", "Scientific review"),
        ):
            value = (
                self.reviews.get(stage["id"], {}).get("decision", "required")
                if key == "scientific_review"
                else stage[key]
            )
            self.check_labels[key].setText(f"{title} · {value.replace('_', ' ')}")
            # Color only the producer's recorded state; no acceptance inference.
            tone = (
                "error.fg"
                if value in {"failed", "rejected"}
                else "warning.fg"
                if value in {"pending", "required", "needs_review", "completed_with_findings"}
                else "success.fg"
                if value in {"passed", "accepted"}
                else "text.secondary"
            )
            self.theme.bind(self.check_labels[key], "bg.canvas", tone)
        self.findings.setText(
            "\n".join(stage["findings"]) or "No scientific interpretation was supplied."
        )
        self.reviewer.clear()
        self.reason.clear()
        self.decision.setCurrentIndex(0)
        self.current_artifacts = stage["artifacts"]
        self.artifacts.blockSignals(True)
        self.artifacts.clear()
        for entry in self.current_artifacts:
            self.artifacts.addItem(f"{entry['kind']} · {entry['label']}")
        self.artifacts.blockSignals(False)
        self.text.clear()
        self.figure.clear()
        self.select_artifact(0)
        self.update_actions()

    def selected(self) -> dict | None:
        """Return the selected explicit artifact descriptor."""
        index = self.artifacts.currentIndex()
        return self.current_artifacts[index] if 0 <= index < len(self.current_artifacts) else None

    def select_artifact(self, index: int) -> None:
        """Load images/text asynchronously; HTML runs only in the external browser."""
        entry = self.selected()
        self.update_actions()
        if not entry or self.thread:
            return
        if entry["kind"] in {"report", "comparison"}:
            self.figure_zoom.hide()
            self.text.setPlainText(
                f"{entry['label']}\n{self.catalog.artifact_path(entry)}\nSHA256: {entry['sha256']}\nUse the action above to open this artifact."
            )
            self.figure_scroll.hide()
            self.review_tabs.setCurrentWidget(self.text)
            return
        catalog = self.catalog

        def read(progress):
            if entry["kind"] == "figure":
                path = catalog.artifact_path(entry)
                if hash_file(path) != entry["sha256"]:
                    raise ValueError("Figure changed; reload QC")
                image = QImage(str(path))
                if image.isNull():
                    raise ValueError("Figure could not be decoded")
                return image
            return read_artifact(catalog, entry)

        self.start("artifact", read)

    def present_measurements(self, text):
        """Format recorded JSON values without changing their values or QC meaning."""
        self.text.setPlainText(text)
        self.measurements.clear()
        try:
            record = json.loads(text)
        except (ValueError, TypeError):
            self.review_tabs.setCurrentWidget(self.text)
            return

        remaining = [2000]

        def append(parent, key, value):
            if remaining[0] <= 0:
                return
            remaining[0] -= 1
            compound = isinstance(value, (dict, list))
            item = QTreeWidgetItem(
                parent, [str(key), "" if compound else json.dumps(value, ensure_ascii=False)]
            )
            item.setToolTip(0, str(key))
            item.setToolTip(1, item.text(1))
            if compound:
                entries = value.items() if isinstance(value, dict) else enumerate(value)
                for child, content in entries:
                    append(item, child, content)

        if isinstance(record, dict):
            for key, value in record.items():
                append(self.measurements, key, value)
        else:
            append(self.measurements, "Recorded value", record)
        if remaining[0] == 0:
            QTreeWidgetItem(
                self.measurements,
                [
                    "Display limited to 2,000 entries",
                    "Complete unchanged values are in Source details",
                ],
            )
        self.measurements.expandToDepth(0)
        self.review_tabs.setCurrentWidget(self.measurements)

    def update_actions(self) -> None:
        """Enable only implemented actions supported by the selected evidence."""
        idle = self.thread is None
        self.empty.setVisible(self.catalog is None)
        self.content.setVisible(self.catalog is not None)
        self.identity.setVisible(self.catalog is not None)
        entry = self.selected()
        report = bool(entry and entry["kind"] == "report")
        self.open_button.setVisible(self.catalog is not None)
        for action in self.empty.actions:
            action.setEnabled(idle)
        self.open_button.setEnabled(idle)
        self.combined_button.setEnabled(idle)
        self.stages.setEnabled(idle)
        self.artifacts.setEnabled(idle)
        self.open_report.setEnabled(idle and report)
        self.export.setEnabled(idle and report)
        self.compare.setEnabled(idle and bool(entry and entry["kind"] == "comparison"))
        self.record.setEnabled(idle and self.catalog is not None and self.stages.currentRow() >= 0)
        self.import_review.setEnabled(idle and self.catalog is not None)

    def view_report(self) -> None:
        """Reverify report bytes immediately before opening it."""
        catalog, entry = self.catalog, self.selected()

        def check(progress):
            path = catalog.artifact_path(entry)
            if hash_file(path) != entry["sha256"]:
                raise ValueError("Report changed; reload QC")
            return path

        self.start("browser", check)

    def save_report(self) -> None:
        """Export exact report bytes; existing destinations are refused."""
        entry, catalog = self.selected(), self.catalog
        path, _ = QFileDialog.getSaveFileName(
            self, "Export existing report", "QC-report.html", "HTML (*.html)"
        )
        if path:
            self.start("export", lambda _: export_report(catalog, entry, Path(path)))

    def create_combined(self) -> None:
        """Build one report for the steps present under a chosen dataset run folder."""
        starting_folder = str(self.catalog.path.parent.parent) if self.catalog is not None else ""
        folder = QFileDialog.getExistingDirectory(
            self, "Choose one dataset's saved processing folder", starting_folder
        )
        if not folder:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Save dataset QC document",
            str(Path(folder) / "dataset-QC.html"),
            "HTML (*.html)",
        )
        if destination:
            self.start(
                "combined",
                lambda progress: build_combined_report_from_folder(
                    Path(folder),
                    Path(destination),
                    cancelled=self.cancel.is_set,
                    progress=progress,
                ),
            )

    def save_review(self) -> None:
        """Record a user's explicit assessment separately from producer statuses."""
        if not self.reviewer.text().strip() or not self.reason.text().strip():
            self.status.setText("Enter your name and a scientific review explanation first.")
            return
        directory = QFileDialog.getExistingDirectory(self, "Choose review-record directory")
        if directory:
            catalog = self.catalog
            stage = catalog.to_dict()["stages"][self.stages.currentRow()]["id"]
            decision, reviewer, reason = (
                self.decision.currentText(),
                self.reviewer.text(),
                self.reason.text(),
            )
            self.start(
                "review",
                lambda _: load_review(
                    catalog,
                    record_review(catalog, stage, decision, reviewer, reason, Path(directory)),
                    cancelled=self.cancel.is_set,
                ),
            )

    def browse_review(self):
        """Reopen a human declaration without changing the producer catalog."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open saved scientific review", "", "JSON (*.json)"
        )
        if path:
            self.open_review(Path(path))

    def open_review(self, path):
        """Verify selected review against the currently loaded catalog."""
        if self.catalog is None or self.thread is not None:
            return
        catalog = self.catalog
        self.start(
            "load_review", lambda _: load_review(catalog, path, cancelled=self.cancel.is_set)
        )

    def open_comparison(self) -> None:
        """Load mapped local image sources in a worker; reuse the existing viewer."""
        catalog, entry = self.catalog, self.selected()
        if self.thread or catalog is None or entry is None or entry["kind"] != "comparison":
            self.status.setText("Select an available matched-image comparison first.")
            return

        def read(progress):
            manifest = json.loads(read_artifact(catalog, entry, limit=32 * 1024 * 1024))
            datasets = {}
            try:
                for key, source in manifest["sources"].items():
                    progress(f"Loading comparison source {key}")
                    expected = source.get("sha256")
                    if not isinstance(expected, dict) or set(expected) != {"dwi", "bval", "bvec"}:
                        raise ValueError(
                            "Comparison sources need recorded image/gradient hashes; export an updated catalog"
                        )
                    for role, digest in expected.items():
                        if hash_file(Path(source[role]), self.cancel.is_set) != digest:
                            raise ValueError(f"Comparison {key}/{role} changed since verification")
                    paths = DatasetPaths(*[Path(source[k]) for k in ["dwi", "bval", "bvec"]])
                    result = DatasetLoader().load(paths, cancelled=self.cancel.is_set)
                    if result.dataset is None:
                        raise ValueError(
                            f"Comparison input failed: {key}: {result.report.to_json()}"
                        )
                    datasets[key] = result.dataset
                for mode in manifest["modes"]:
                    validate_comparison(
                        datasets[mode["left"]],
                        datasets[mode["right"]],
                        [tuple(p) for p in mode["pairs"]],
                    )
                return datasets, manifest
            except Exception:
                for dataset in datasets.values():
                    dataset.close()
                raise

        self.start("comparison", read)

    def shutdown(self) -> bool:
        """Drain only this workspace's readers; never signal an MRI process."""
        self.closing = True
        self.cancel.set()
        for window in self.windows:
            window.close()
        return self.thread is None and all(not w.isVisible() for w in self.windows)
