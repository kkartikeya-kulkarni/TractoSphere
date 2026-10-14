"""Native shell and transactional ownership of the active dataset.

Launch through ``dmri-studio gui``. Project ledgers are explicit immutable
snapshots; shell preferences stay separate. Safe close drains blocking reads.
"""

import logging
import sys
import time
from dataclasses import replace
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.dataset import DwiDataset
from dmri_studio.core.loading import DatasetLoader, LoadResult
from dmri_studio.core.validation import ValidationPolicy

from .components import ElidedLabel, LogDrawer, Toast, button, label
from .dependency_setup import DependencyController, DependencyDialog
from .load_data import LoadDataPage
from .preferences import PreferencesPage
from .projects import ProjectPage
from .qc import QCPage
from .results import ResultsPage
from .settings import Settings
from .theme import ASSETS, COMPACT, RAIL, SIDEBAR, Theme
from .welcome import reduced_motion
from .workers import LoadWorker, LogBridge, QtLogHandler
from .workflow import WorkflowPage


class MainWindow(QMainWindow):
    """Keep candidate transactions and active-dataset ownership separate."""

    def __init__(
        self, theme: Theme, settings: Settings | None = None, loader: DatasetLoader | None = None
    ) -> None:
        """Restore shell preferences, but never automatically open imaging data."""
        super().__init__()
        self.theme = theme
        self.settings = settings or Settings()
        self.dependencies = DependencyController(self.settings, self)
        self.dependency_dialog = None
        theme.reduced_motion = reduced_motion(self.settings)
        self.loader = loader or DatasetLoader()
        self.dataset: DwiDataset | None = None
        self.retired: list[DwiDataset] = []
        self.pending_result = None
        self.project_binding = None
        self.running_project_binding = None
        self.request = 0
        self.load_thread: QThread | None = None
        self.load_worker: LoadWorker | None = None
        self.cancel_event = Event()
        self.closing = False
        self.sidebar_collapsed = bool(self.settings.get("sidebar_collapsed", False))
        self.drawer_height = int(self.settings.get("drawer_height", 220))
        self.setWindowTitle("TractoSphere — Diffusion MRI Research Studio — Public Alpha")
        self.setWindowIcon(QIcon(str(ASSETS / "branding/tractosphere-app.svg")))
        self.setMinimumSize(960, 640)
        self.resize(1280, 840)
        container = QWidget()
        self.setCentralWidget(container)
        shell = QHBoxLayout(container)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        self.sidebar = QWidget()
        theme.bind(self.sidebar, "bg.sidebar")
        sidebar = QVBoxLayout(self.sidebar)
        sidebar.setContentsMargins(12, 16, 12, 16)
        sidebar.setSpacing(8)
        self.wordmark = label("WORKSPACES", "caption")
        sidebar.addWidget(self.wordmark)
        self.collapse = button("", self.toggle_sidebar, quiet=True)
        self.collapse.setAccessibleName("Toggle sidebar")
        theme.set_icon(self.collapse, "panel-left-close")
        sidebar.addWidget(self.collapse)
        sidebar.addSpacing(8)
        self.nav_buttons = []
        names = [
            ("Project", "file-input"),
            ("Acquisitions", "database"),
            ("Viewer", "scan"),
            ("Data Check", "clipboard-check"),
            ("Preprocessing", "sliders-horizontal"),
            ("Reconstruction", "brain"),
            ("Tractography", "route"),
            ("Connectivity", "network"),
            ("QC", "list-checks"),
            ("Results", "files"),
            ("Settings", "settings"),
        ]
        for name, icon in names:
            if name == "Settings":
                sidebar.addStretch()
            nav = button(
                name,
                self.focus_load
                if name == "Project"
                else self.focus_projects
                if name == "Acquisitions"
                else self.focus_viewer
                if name == "Viewer"
                else self.focus_scientific_viewer
                if name == "Tractography"
                else self.focus_preferences
                if name == "Settings"
                else self.focus_data_check
                if name == "Data Check"
                else self.focus_workflow
                if name == "Preprocessing"
                else self.focus_qc
                if name == "QC"
                else self.focus_results
                if name == "Results"
                else None,
                quiet=True,
            )
            nav.setProperty("nav", True)
            nav.setAccessibleName(name)
            nav.setProperty("iconName", icon)
            nav.setToolTip(
                {
                    "Project": "Project · Load Data",
                    "Viewer": "Open and inspect MRI images",
                    "Tractography": "3D viewer · inspect and edit existing tractograms",
                    "Acquisitions": "Review acquisition associations",
                    "Preprocessing": "Configure supported preprocessing operations",
                    "Data Check": "Validate loaded inputs",
                    "QC": "Inspect existing quality-control results",
                    "Results": "View processing outputs and reports",
                    "Settings": "Application preferences",
                }.get(name, "Available in a later phase")
            )
            nav.setAccessibleDescription(nav.toolTip())
            nav.setEnabled(
                name
                in {
                    "Project",
                    "Acquisitions",
                    "QC",
                    "Results",
                    "Settings",
                    "Tractography",
                    "Viewer",
                }
            )
            theme.set_icon(nav, icon, "accent.text" if name == "Project" else "text.disabled")
            if name == "Project":
                nav.setProperty("selected", True)
                theme.bind(nav, "accent.subtle", "accent.text")
            sidebar.addWidget(nav)
            self.nav_buttons.append((nav, name))
        self.sidebar_note = label("Single-dataset workspace\nImport, inspect and plan.", "caption")
        sidebar.addWidget(self.sidebar_note)
        shell.addWidget(self.sidebar)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        top = QWidget()
        theme.bind(top, "bg.surface")
        top.setMinimumHeight(64)
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(16, 8, 16, 8)
        logo = QSvgWidget(str(ASSETS / "branding/tractosphere.svg"))
        logo.setFixedSize(36, 36)
        logo.setAccessibleName("TractoSphere logo")
        top_layout.addWidget(logo)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        brand.addWidget(label("TractoSphere", "section"))
        brand.addWidget(label("Diffusion MRI Research Studio", "caption"))
        alpha_notice = label("PUBLIC ALPHA · RESEARCH USE ONLY", "caption")
        alpha_notice.setWordWrap(False)
        alpha_notice.setToolTip("NOT FOR CLINICAL USE · SCIENTIFIC VALIDATION ONGOING")
        brand.addWidget(alpha_notice)
        top_layout.addLayout(brand)
        top_layout.addSpacing(24)
        identity = QVBoxLayout()
        identity.setSpacing(0)
        self.project_label = ElidedLabel("Untitled project", "control")
        identity.addWidget(self.project_label)
        self.dataset_label = ElidedLabel("No dataset loaded", "caption")
        identity.addWidget(self.dataset_label)
        top_layout.addLayout(identity, 1)
        self.commands_button = button(
            "⌘K" if sys.platform == "darwin" else "Ctrl+K",
            self.commands,
            quiet=True,
        )
        theme.set_icon(self.commands_button, "search")
        top_layout.addWidget(self.commands_button)
        top_layout.addWidget(button("3D Viewer", self.focus_scientific_viewer, quiet=True))
        self.theme_button = button("", self.toggle_theme, quiet=True)
        top_layout.addWidget(self.theme_button)
        logs_button = button("", self.toggle_logs, quiet=True)
        logs_button.setAccessibleName("Toggle log drawer")
        logs_button.setToolTip(
            "Toggle logs · ⌘J" if sys.platform == "darwin" else "Toggle logs · Ctrl+J"
        )
        theme.set_icon(logs_button, "terminal")
        top_layout.addWidget(logs_button)
        right_layout.addWidget(top)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.page = LoadDataPage(theme, self.settings)
        self.scroll.setWidget(self.page)
        self.workspace = QStackedWidget()
        self.workspace.addWidget(self.scroll)
        self.projects = ProjectPage(lambda: (self.dataset, self.workflow.history))
        self.workspace.addWidget(self.projects)
        self.projects.load_requested.connect(self.load_saved_result)
        self.projects.run_requested.connect(self.open_saved_run)
        self.projects.project_changed.connect(self.project_label.setText)
        self.projects.notification.connect(
            lambda message, tone: self.toast.show_message(message, tone)
        )
        self.projects.project_changed.connect(
            lambda _: self.sidebar_note.setText(
                "Project acquisitions\nInspect and process separately."
            )
        )
        self.expanded_host = QScrollArea()
        self.expanded_host.setWidgetResizable(True)
        self.expanded_content = QWidget()
        # Preserve usable canvas heights when overlay controls or logs are open.
        # Narrow widths still reach Preview's linked-plane tab layout.
        self.expanded_content.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.expanded_host.setWidget(self.expanded_content)
        self.expanded_layout = QVBoxLayout(self.expanded_content)
        self.expanded_layout.setContentsMargins(16, 16, 16, 16)
        self.workspace.addWidget(self.expanded_host)
        self.workflow_scroll = QScrollArea()
        self.workflow_scroll.setWidgetResizable(True)
        self.workflow = WorkflowPage(theme)
        self.workflow_scroll.setWidget(self.workflow)
        self.workspace.addWidget(self.workflow_scroll)
        self.qc = QCPage(theme)
        self.workspace.addWidget(self.qc)
        self.results = ResultsPage(self.settings)
        self.workspace.addWidget(self.results)
        self.preferences = PreferencesPage(self)
        self.preferences_scroll = QScrollArea()
        self.preferences_scroll.setWidgetResizable(True)
        self.preferences_scroll.setWidget(self.preferences)
        self.workspace.addWidget(self.preferences_scroll)
        self.results.qc_requested.connect(self.open_generated_qc)
        self.results.load_requested.connect(self.load_saved_result)
        self.qc.notification.connect(lambda message, tone: self.toast.show_message(message, tone))
        self.workflow.notification.connect(
            lambda message, tone: self.toast.show_message(message, tone)
        )
        self.dependencies.configuration_applied.connect(self.apply_dependency_locations)
        self.apply_dependency_locations(self.dependencies.saved_directories())
        self.workflow.execution.busy_changed.connect(self.processing_busy)
        self.workflow.execution.inspect_requested.connect(self.inspect_processed_mask)
        self.workflow.execution.qc_requested.connect(self.open_generated_qc)
        self.workflow.execution.results_requested.connect(self.open_saved_run)
        self.workflow.execution.attempt_finished.connect(self.record_project_attempt)
        self.splitter.addWidget(self.workspace)
        self.preview_index = self.page.root.indexOf(self.page.preview)
        self.preview_scroll = 0
        self.viewer_workspace_active = False
        self.scientific_viewer = None
        self.page.preview.expanded_changed.connect(self.expand_preview)
        self.logs = LogDrawer(theme)
        self.splitter.addWidget(self.logs)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.logs.toggled.connect(self.drawer_toggled)
        self.splitter.splitterMoved.connect(self.drawer_resized)
        right_layout.addWidget(self.splitter, 1)
        shell.addWidget(right, 1)
        self.toast = Toast(right, theme)
        self.page.load_requested.connect(self.start_load)
        self.page.cancel_requested.connect(self.cancel_load)
        self.page.unload_requested.connect(self.unload)
        self.page.notification.connect(self.toast.show_message)
        self.page.preview.idle.connect(self.release_retired)
        self.page.preview.idle.connect(self.mask_preview_ready)
        self.bridge = LogBridge(self)
        self.bridge.record.connect(self.logs.append)
        self.handler = QtLogHandler(self.bridge)
        self.logger = logging.getLogger("dmri_studio")
        self.previous_log_level = self.logger.level
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(self.handler)
        self.actions = []
        for name, shortcut, callback in [
            ("Choose DWI image", "Ctrl+O", lambda: self.page.browse("dwi")),
            ("Command list", "Ctrl+K", self.commands),
            ("Toggle sidebar", "Ctrl+B", self.toggle_sidebar),
            ("Toggle log drawer", "Ctrl+J", self.toggle_logs),
        ]:
            action = QAction(name, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            self.addAction(action)
            self.actions.append(action)
        menu = self.menuBar().addMenu("File")
        menu.addAction(self.actions[0])
        view = self.menuBar().addMenu("View")
        for action in self.actions[1:]:
            view.addAction(action)
        self.apply_sidebar()
        self.update_theme_action()
        if geometry := self.settings.get("geometry"):
            self.restoreGeometry(geometry)
        # Clamp both size and position to the current monitor's usable desktop.
        area = self.screen().availableGeometry()
        self.resize(min(self.width(), area.width()), min(self.height(), area.height()))
        self.move(
            max(area.left(), min(self.x(), area.right() - self.width())),
            max(area.top(), min(self.y(), area.bottom() - self.height())),
        )
        restore_drawer = self.settings.get("drawer_open", False)
        self.drawer_toggled(False)
        if restore_drawer:
            self.logs.toggle()
        self.close_timer = QTimer(self)
        self.close_timer.setInterval(50)
        self.close_timer.timeout.connect(self.finish_close)
        self.theme.apply(self.theme.name)

    def record(self, message: str, level: str = "INFO") -> None:
        """Record actual GUI transaction events separately from core stages."""
        self.logs.append((time.time(), level, "gui.loader", message))

    def start_load(self) -> None:
        """Start one candidate; the prior dataset remains the active object."""
        if (
            self.load_thread is not None
            or self.workflow.execution.thread is not None
            or not self.page.load_button.isEnabled()
        ):
            return
        self.request += 1
        self.cancel_event = Event()
        self.page.remove_failure()
        self.page.set_busy(True)
        self.workflow.execution.setEnabled(False)
        self.theme.apply(self.theme.name)
        paths = self.page.paths()
        for role, path in paths.supplied().items():
            self.record(f"Selected {role}: {path}")
            self.settings.set(f"folders/{role}", str(path.parent))
        policy = ValidationPolicy(bvec_layout=self.page.layout_choice.currentData())
        self.load_thread = QThread(self)
        self.load_worker = LoadWorker(
            self.request,
            paths,
            policy,
            self.cancel_event,
            self.loader,
            expected_product=self.pending_result["product"] if self.pending_result else None,
        )
        self.load_worker.moveToThread(self.load_thread)
        self.load_thread.started.connect(self.load_worker.run)
        self.load_worker.progress.connect(self.load_progress)
        self.load_worker.result.connect(self.load_result)
        self.load_worker.error.connect(self.load_error)
        self.load_worker.finished.connect(self.load_thread.quit, Qt.ConnectionType.DirectConnection)
        self.load_thread.finished.connect(self.load_worker.deleteLater)
        self.load_thread.finished.connect(self.load_finished)
        self.load_thread.start()

    def load_progress(self, request: int, progress: object) -> None:
        """Ignore progress from a superseded transaction."""
        if request == self.request and not self.cancel_event.is_set():
            self.page.set_progress(progress)

    def cancel_load(self) -> None:
        """Cancellation stays thread-safe even during a blocking compressed read."""
        self.cancel_event.set()
        self.page.cancel_button.setEnabled(False)
        self.page.stage_message = "Cancelling… waiting for the current read to finish safely."
        self.page.update_elapsed()
        self.record("Cancellation requested; active dataset retained.")

    def load_result(self, request: int, result: LoadResult) -> None:
        """Atomically accept success; discard stale or cancelled candidates."""
        if request != self.request or self.closing:
            if result.dataset:
                result.dataset.close()
            return
        cancelled = result.report.cancelled or self.cancel_event.is_set()
        if cancelled and not result.report.cancelled:
            result = replace(result, report=replace(result.report, complete=False, cancelled=True))
        if cancelled and result.dataset:
            result.dataset.close()
        for check in result.report.checks:
            self.record(
                f"{check.status.upper()} {check.code}: {check.message}",
                {"warn": "WARNING", "fail": "ERROR"}.get(check.status, "INFO"),
            )
        self.page.set_busy(False)
        if result.dataset is not None and not cancelled:
            if self.dataset:
                self.retired.append(self.dataset)
            self.dataset = result.dataset
            self.page.set_dataset(self.dataset)
            self.workflow.set_dataset(self.dataset)
            self.project_binding = (
                self.pending_result.get("project_binding") if self.pending_result else None
            )
            if self.pending_result and self.pending_result["product"]["paths"]["dwi"] == str(
                self.dataset.paths.dwi
            ):
                self.workflow.import_verified_history(self.pending_result["product"]["history"])
                panel = self.workflow.execution
                for key, value in self.pending_result["handoff"].items():
                    if key == "references":
                        panel.references.setPlainText("\n".join(value))
                        continue
                    if key in panel.bias_inputs:
                        panel.bias_inputs[key].setText(value)
                    if key in panel.correction_inputs:
                        panel.correction_inputs[key].setText(value)
                    if key in panel.anatomy_inputs.fields:
                        panel.anatomy_inputs.fields[key].setText(value)
                    if key in panel.combination_inputs.fields:
                        panel.combination_inputs.fields[key].setText(value)
                for op in self.pending_result.get("suggested_operations", []):
                    if op in self.workflow.selections:
                        self.workflow.selections[op].setChecked(True)
                for key, value in self.pending_result.get("native_parameters", {}).items():
                    control = panel.native_parameters.get(key) or getattr(panel, key, None)
                    if control is not None:
                        control.setValue(value)
                self.pending_result = None
            self.set_workflow_available(True)
            self.dataset_label.setText(
                f"{self.dataset.paths.dwi.parent.name} / {self.dataset.paths.dwi.name}"
            )
            self.setWindowTitle(f"{self.dataset.paths.dwi.name} — TractoSphere")
            self.release_retired()
            self.toast.show_message(
                self.page.banner_title.text(),
                "warning" if self.page.state == "warning" else "success",
            )
            self.record("Dataset activated. " + result.report.coverage_message())
        else:
            self.page.present_report(result.report, cancelled)
            if not cancelled:
                self.toast.show_message(
                    "Could not load dataset. See candidate validation.", "error"
                )
        self.theme.apply(self.theme.name)
        self.scroll.verticalScrollBar().setValue(0)

    def load_error(self, request: int, traceback: str) -> None:
        """Retain active data and expose unexpected failures in copyable logs."""
        if request != self.request:
            return
        self.page.set_busy(False)
        self.page.state = "failed"
        self.page.show_banner(
            "Could not load dataset",
            "Unexpected loader error. Open Logs for the full traceback and Copy all details. The active dataset is unchanged.",
            "error",
        )
        self.record(traceback, "ERROR")
        if not self.logs.open:
            self.logs.toggle()
        self.toast.show_message("Unexpected loader error. Details are in Logs.", "error")

    def load_finished(self) -> None:
        """Release QThread only after it has stopped; then enable another load."""
        self.load_thread.wait()  # Join only after finished, never during disk I/O.
        self.load_thread.deleteLater()
        self.load_thread, self.load_worker = None, None
        self.pending_result = None
        if not self.closing:
            self.page.set_busy(False)
            self.workflow.execution.setEnabled(True)

    def release_retired(self) -> None:
        """Never close a dataset on the UI thread while its read lock is held."""
        if self.page.preview.thread is None:
            for dataset in self.retired:
                dataset.close()
            self.retired.clear()

    def unload(self) -> None:
        """Release the current dataset safely; no input file is deleted."""
        if self.load_thread or self.workflow.execution.thread:
            return
        if self.dataset:
            self.retired.append(self.dataset)
        self.dataset = None
        self.project_binding = None
        self.page.set_dataset(None)
        self.workflow.set_dataset(None)
        self.set_workflow_available(False)
        self.focus_load()
        self.release_retired()
        self.dataset_label.setText("No dataset loaded")
        self.setWindowTitle("TractoSphere — Diffusion MRI Research Studio — Public Alpha")
        self.setWindowIcon(QIcon(str(ASSETS / "branding/tractosphere-app.svg")))
        self.record("Dataset unloaded; source files unchanged.")

    def toggle_theme(self) -> None:
        """Change only palette/icon state; all selection and work remain intact."""
        self.theme.apply("light" if self.theme.name == "dark" else "dark")
        self.settings.set("theme", self.theme.name)
        self.update_theme_action()

    def update_theme_action(self) -> None:
        """Name the action rather than exposing an unexplained icon."""
        other = "light" if self.theme.name == "dark" else "dark"
        self.theme_button.setAccessibleName(f"Switch to {other} theme")
        self.theme_button.setToolTip(self.theme_button.accessibleName())
        self.theme.set_icon(self.theme_button, "sun" if other == "light" else "moon")

    def toggle_sidebar(self) -> None:
        """Persist the compact rail without changing the active page."""
        self.sidebar_collapsed = not self.sidebar_collapsed
        self.apply_sidebar()
        self.settings.set("sidebar_collapsed", self.sidebar_collapsed)

    def apply_sidebar(self) -> None:
        """Keep accessible names and tooltips in the icon-only rail."""
        self.sidebar.setFixedWidth(RAIL if self.sidebar_collapsed else SIDEBAR)
        margin = 6 if self.sidebar_collapsed else 12
        self.sidebar.layout().setContentsMargins(margin, 16, margin, 16)
        self.wordmark.setVisible(not self.sidebar_collapsed)
        self.sidebar_note.setVisible(not self.sidebar_collapsed)
        for widget, name in self.nav_buttons:
            display = {
                "Project": "Data && imports",
                "Acquisitions": "Acquisitions",
                "Viewer": "Image viewer",
                "Data Check": "Data validation",
                "QC": "Quality control",
                "Results": "Runs && reports",
                "Reconstruction": "Diffusion models",
                "Settings": "Preferences",
            }.get(name, name)
            widget.setText("" if self.sidebar_collapsed else display)
            widget.setToolTip(
                name
                + (
                    " · Load Data"
                    if name == "Project"
                    else " · History and operation planning"
                    if name == "Preprocessing" and self.dataset is not None
                    else " · Load a dataset first"
                    if name == "Preprocessing"
                    else " · Inspect saved processing evidence"
                    if name == "QC"
                    else " · Load a dataset first"
                    if name in {"Viewer", "Data Check"} and self.dataset is None
                    else ""
                    if name in {"Acquisitions", "Viewer", "Data Check", "Results", "Settings"}
                    else " · Not implemented"
                )
            )
            widget.setAccessibleDescription(widget.toolTip())
        self.theme.set_icon(
            self.collapse, "panel-left-open" if self.sidebar_collapsed else "panel-left-close"
        )

    def toggle_logs(self) -> None:
        """Expose the drawer action to both toolbar and command list."""
        self.logs.toggle()

    def drawer_toggled(self, opened: bool) -> None:
        """Constrain an expanded drawer to at most half the content height."""
        self.logs.setMinimumHeight(120 if opened else COMPACT)
        self.logs.setMaximumHeight(max(120, self.splitter.height() // 2) if opened else COMPACT)
        height = (
            min(max(120, self.drawer_height), max(120, self.splitter.height() // 2))
            if opened
            else COMPACT
        )
        self.splitter.setSizes([max(1, self.splitter.height() - height), height])
        self.settings.set("drawer_open", opened)

    def drawer_resized(self, *_: object) -> None:
        """Remember the user's expanded height independently of collapse."""
        if self.logs.open:
            self.drawer_height = self.splitter.sizes()[1]
            self.settings.set("drawer_height", self.drawer_height)

    def resizeEvent(self, event: object) -> None:
        """Keep drawer and toast within the resized native window."""
        super().resizeEvent(event)
        if hasattr(self, "logs") and self.logs.open:
            self.logs.setMaximumHeight(max(120, self.splitter.height() // 2))
        if hasattr(self, "toast"):
            self.toast.move(max(0, self.toast.parentWidget().width() - self.toast.width() - 24), 80)

    def focus_load(self) -> None:
        """Navigate to the existing Project / Load Data screen."""
        self.viewer_workspace_active = False
        self.page.preview.set_expanded(None)
        if self.page.preview.parentWidget() == self.expanded_content:
            self.expand_preview(False)
        self.workspace.setCurrentWidget(self.scroll)
        self.select_navigation("Project")
        self.scroll.verticalScrollBar().setValue(0)
        if self.page.form.isVisible():
            self.page.fields["dwi"].setFocus()
        else:
            self.page.another.setFocus()

    def set_workflow_available(self, available: bool) -> None:
        """Enable only the implemented planning workspace for an active dataset."""
        for nav, name in self.nav_buttons:
            if name in {"Preprocessing", "Data Check"}:
                nav.setEnabled(available)
                self.theme.set_icon(
                    nav,
                    nav.property("iconName"),
                    "text.secondary" if available else "text.disabled",
                )
        self.apply_sidebar()

    def select_navigation(self, selected: str) -> None:
        """Reflect the active workspace using the existing semantic theme."""
        for nav, name in self.nav_buttons:
            active = name == selected
            nav.setProperty("selected", active)
            self.theme.bind(
                nav,
                "accent.subtle" if active else "bg.sidebar",
                "accent.text"
                if active
                else "text.secondary"
                if nav.isEnabled()
                else "text.disabled",
            )
            self.theme.set_icon(
                nav,
                nav.property("iconName"),
                "accent.text"
                if active
                else "text.secondary"
                if nav.isEnabled()
                else "text.disabled",
            )

    def focus_projects(self):
        """Open the acquisition ledger without replacing the active image."""
        self.workspace.setCurrentWidget(self.projects)
        self.select_navigation("Acquisitions")

    def focus_preferences(self):
        """Open display/privacy preferences, never processing defaults."""
        self.preferences.refresh()
        self.workspace.setCurrentWidget(self.preferences_scroll)
        self.select_navigation("Settings")

    def focus_data_check(self):
        """Navigate to the actual existing input validation evidence."""
        self.focus_load()
        self.page.jump_validation()
        self.select_navigation("Data Check")

    def focus_viewer(self):
        """Move the existing native reader/canvases into a dedicated workspace."""
        if self.dataset is None:
            self.focus_scientific_viewer()
            return
        self.viewer_workspace_active = True
        self.expand_preview(True)
        self.select_navigation("Viewer")

    def focus_scientific_viewer(self):
        """Open the accelerated four-view workspace without changing processing inputs."""
        if self.scientific_viewer is None:
            try:
                from .scientific_viewer import ScientificViewer

                self.scientific_viewer = ScientificViewer(self.theme)
            except ImportError as exc:
                self.toast.show_message(f"Install dmri-studio[gui] for 3D support: {exc}", "error")
                return
            self.scientific_viewer.image_loaded.connect(self.update_scene_identity)
            self.workspace.addWidget(self.scientific_viewer)
        self.workspace.setCurrentWidget(self.scientific_viewer)
        if (
            self.scientific_viewer.reference is None
            and self.dataset is not None
            and not self.scientific_viewer.tasks
        ):
            self.scientific_viewer.safe(
                lambda: self.scientific_viewer.load_image(self.dataset.paths.dwi)
            )
        self.select_navigation("Tractography")

    def update_scene_identity(self):
        if self.scientific_viewer and self.scientific_viewer.reference and self.dataset is None:
            self.dataset_label.setText("Viewer · " + self.scientific_viewer.reference.path.name)

    def focus_results(self) -> None:
        """Open saved attempts independently of the current dataset."""
        self.page.preview.set_expanded(None)
        self.workspace.setCurrentWidget(self.results)
        self.select_navigation("Results")

    def open_saved_run(self, path: Path) -> None:
        """Read an existing attempt without changing or restarting processing."""
        self.focus_results()
        self.results.load(path)

    def load_saved_result(self, context: dict) -> None:
        """Use the normal transactional loader; carry verified history on success only."""
        if self.load_thread is not None or self.workflow.execution.thread is not None:
            self.toast.show_message(
                "Wait for the active load or processing operation before replacing inputs.",
                "warning",
            )
            return
        self.pending_result = dict(context)
        if "project_binding" not in self.pending_result:
            self.pending_result["project_binding"] = self.projects.binding_for_product(
                context["product"]
            )
        for role, field in self.page.fields.items():
            field.setText(context["product"]["paths"].get(role, ""))
        self.focus_load()
        self.start_load()

    def open_generated_qc(self, path: Path) -> None:
        """Reuse newly generated evidence without launching a second analysis."""
        self.focus_qc()
        self.qc.load(path)

    def focus_qc(self) -> None:
        """Open existing evidence without requiring a loaded raw dataset."""
        self.page.preview.set_expanded(None)
        self.workspace.setCurrentWidget(self.qc)
        self.select_navigation("QC")

    def focus_workflow(self) -> None:
        """Open the history/planning screen without reloading or changing viewer state."""
        if self.dataset is None:
            return
        self.page.preview.set_expanded(None)
        self.workspace.setCurrentWidget(self.workflow_scroll)
        self.select_navigation("Preprocessing")
        self.workflow.confirm.setFocus()

    def expand_preview(self, expanded: bool) -> None:
        """Move the same live preview into a full-content host and restore it on return."""
        if expanded or self.viewer_workspace_active:
            self.preview_scroll = self.scroll.verticalScrollBar().value()
            self.page.root.removeWidget(self.page.preview)
            self.expanded_layout.addWidget(self.page.preview)
            self.workspace.setCurrentWidget(self.expanded_host)
        else:
            self.expanded_layout.removeWidget(self.page.preview)
            self.page.root.insertWidget(self.preview_index, self.page.preview)
            self.workspace.setCurrentWidget(self.scroll)
            QTimer.singleShot(
                0, lambda: self.scroll.verticalScrollBar().setValue(self.preview_scroll)
            )
        self.page.preview.show()

    def commands(self) -> None:
        """Offer searchable existing actions only; Escape restores prior focus."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Commands")
        dialog.resize(480, 320)
        layout = QVBoxLayout(dialog)
        query = QLineEdit()
        query.setPlaceholderText("Search commands…")
        query.setAccessibleName("Search commands")
        listing = QListWidget()
        choices = [
            ("Choose DWI image", lambda: self.page.browse("dwi")),
            ("Focus Load Data", self.focus_load),
            ("Open saved preprocessing QC", self.focus_qc),
            ("Open saved preprocessing runs", self.focus_results),
            ("Check scientific dependencies", self.open_dependency_setup),
            ("Toggle theme", self.toggle_theme),
            ("Collapse / expand sidebar", self.toggle_sidebar),
            ("Toggle logs", self.toggle_logs),
        ]
        if self.dataset is not None:
            choices.append(("Open preprocessing plan", self.focus_workflow))
        layout.addWidget(query)
        layout.addWidget(listing)

        def populate(text: str) -> None:
            """Filter the known action list; never invent processing commands."""
            listing.clear()
            listing.addItems([name for name, _ in choices if text.lower() in name.lower()])
            listing.setCurrentRow(0)

        def activate() -> None:
            """Close the popup before dispatching its selected shell action."""
            item = listing.currentItem()
            if item:
                callback = dict(choices)[item.text()]
                dialog.accept()
                callback()

        query.textChanged.connect(populate)
        query.returnPressed.connect(activate)
        listing.itemActivated.connect(activate)
        populate("")
        query.setFocus()
        dialog.exec()

    def open_dependency_setup(self, *, first_run=False):
        """Open the shared setup interface; viewer-only continuation stays available."""
        if first_run and self.settings.get("dependencies/acknowledged", False):
            return
        if self.dependency_dialog is None:
            self.dependency_dialog = DependencyDialog(self)
        self.dependency_dialog.open()

    def apply_dependency_locations(self, locations):
        """Apply only explicit/saved tool defaults, invalidating command previews."""
        if self.workflow.execution.thread is not None:
            return
        panel = self.workflow.execution
        if "MRtrix3" in locations:
            panel.tool_directory.setText(locations["MRtrix3"])
        if "ANTs" in locations:
            panel.bias_inputs["ants_directory"].setText(locations["ANTs"])
        if "FSL" in locations:
            panel.correction_inputs["fsl_directory"].setText(locations["FSL"])
            panel.anatomy_inputs.fields["fsl_directory"].setText(locations["FSL"])
            panel.combination_inputs.fields["fsl_directory"].setText(locations["FSL"])

    def closeEvent(self, event: object) -> None:
        """Close cooperatively, leaving the event loop responsive during I/O."""
        # A debounce can still be pending with no live worker. Cancel its future
        # read before closing the dataset, as well as invalidating active reads.
        self.page.preview.set_dataset(None)
        dependencies_idle = self.dependencies.shutdown()
        qc_idle = self.qc.shutdown()
        results_idle = self.results.shutdown()
        projects_idle = self.projects.shutdown()
        scene_idle = self.scientific_viewer is None or self.scientific_viewer.shutdown()
        if (
            self.load_thread
            or self.page.preview.thread
            or self.workflow.execution.thread
            or not qc_idle
            or not dependencies_idle
            or not results_idle
            or not projects_idle
            or not scene_idle
        ):
            event.ignore()
            self.closing = True
            self.cancel_event.set()
            if self.workflow.execution.thread:
                self.workflow.execution.cancel_work()
            self.page.show_banner(
                "Closing safely", "Waiting for the current read to finish.", "info"
            )
            self.close_timer.start()
            return
        self.close_timer.stop()
        self.settings.set("geometry", self.saveGeometry())
        self.settings.set("theme", self.theme.name)
        self.settings.store.sync()
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(self.previous_log_level)
        self.release_retired()
        if self.dataset:
            self.dataset.close()
            self.dataset = None
        event.accept()

    def finish_close(self) -> None:
        """Retry closing after both worker threads have completed normally."""
        if (
            self.load_thread is None
            and self.page.preview.thread is None
            and self.workflow.execution.thread is None
            and self.projects.thread is None
        ):
            self.close()

    def processing_busy(self, busy: bool) -> None:
        """Protect the active input without freezing navigation, viewing or theme changes."""
        if busy and self.workflow.execution.task_kind == "run":
            self.running_project_binding = self.project_binding
        self.projects.busy = busy
        self.projects.actions()
        self.results.processing_busy = busy
        self.results.update_actions()
        # Import controls remain visible, but cannot replace/close the dataset
        # owned by the running engine. Guards above also protect keyboard calls.
        self.page.load_button.setEnabled(not busy)
        self.page.unload.setEnabled(not busy)
        self.page.form.setEnabled(not busy)
        self.page.another.setEnabled(not busy)
        if not busy:
            self.page.update_enabled()

    def record_project_attempt(self, path: Path) -> None:
        """Record the finished run independently of current table selection."""
        binding, self.running_project_binding = self.running_project_binding, None
        if not self.closing:
            self.projects.record_finished_attempt(path, binding)

    def inspect_processed_mask(self, path: object) -> None:
        """Reuse the original exact-grid overlay viewer instead of creating a second viewer."""
        self.focus_load()
        preview = self.page.preview
        preview.tools_toggle.setChecked(True)
        preview.overlay.mode.setCurrentText("Mask")
        preview.overlay.load(path)
        self.scroll.ensureWidgetVisible(preview)

    def mask_preview_ready(self) -> None:
        """Only allow QC after the expected mask planes were actually read."""
        overlay = self.page.preview.overlay
        if (
            overlay.dataset
            and not overlay.pending
            and all(value is not None for _, value in overlay.cache.values())
            and len(overlay.cache) == 3
        ):
            self.workflow.execution.mark_inspected(overlay.dataset.path)
