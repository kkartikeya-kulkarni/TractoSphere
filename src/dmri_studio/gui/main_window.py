"""Native shell and transactional ownership of the active dataset.

Launch through ``dmri-studio gui``. Project ledgers are explicit immutable
snapshots; shell preferences stay separate. Safe close drains blocking reads.
"""

import logging
import time
from dataclasses import replace
from pathlib import Path
from threading import Event

from PySide6.QtCore import QSize, Qt, QThread, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence
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
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.dataset import DwiDataset
from dmri_studio.core.loading import DatasetLoader, LoadResult
from dmri_studio.core.validation import ValidationPolicy

from .components import ElidedLabel, LogDrawer, Toast, button, label
from .dependency_setup import DependencyController, DependencyDialog
from .load_data import LoadDataPage
from .operation_state import OperationState, State
from .preferences import PreferencesPage
from .projects import ProjectPage
from .qc import QCPage
from .results import ResultsPage
from .scientific_planning import ScientificPlanningPage
from .settings import Settings
from .theme import ASSETS, COMPACT, RAIL, SIDEBAR, Theme
from .validation_page import DataValidationPage
from .workers import LoadWorker, LogBridge, QtLogHandler
from .workflow import WorkflowPage
from .workstation import StartPage


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
        theme.reduced_motion = True
        self.loader = loader or DatasetLoader()
        self.dataset: DwiDataset | None = None
        self.retired: list[DwiDataset] = []
        self.pending_result = None
        self.project_binding = None
        self.running_project_binding = None
        self.request = 0
        self.load_state = OperationState(self)
        self.load_thread: QThread | None = None
        self.load_worker: LoadWorker | None = None
        self.cancel_event = Event()
        self.closing = False
        self.sidebar_collapsed = bool(self.settings.get("sidebar_collapsed", False))
        self.drawer_height = int(self.settings.get("drawer_height", 220))
        self.setWindowTitle("TractoSphere — Diffusion MRI Research Studio")
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
        self.wordmark = label("TractoSphere", "section")
        sidebar.addWidget(self.wordmark)
        self.collapse = button("", self.toggle_sidebar, quiet=True)
        self.collapse.setAccessibleName("Toggle sidebar")
        theme.set_icon(self.collapse, "panel-left-close")
        sidebar.addWidget(self.collapse)
        sidebar.addSpacing(8)
        self.nav_buttons = []
        self.nav_headers = []
        names = [
            ("Project", "file-input"),
            ("Acquisitions", "database"),
            ("Data Check", "clipboard-check"),
            ("Viewer", "scan"),
            ("Tractography", "route"),
            ("Preprocessing", "sliders-horizontal"),
            ("Reconstruction", "brain"),
            ("Registration", "scan"),
            ("Batch Processing", "list-checks"),
            ("Connectivity", "network"),
            ("QC", "list-checks"),
            ("Results", "files"),
            ("Software Dependencies", "sliders-horizontal"),
            ("Settings", "settings"),
        ]
        for name, icon in names:
            group = {
                "Project": "Data",
                "Viewer": "View",
                "Preprocessing": "Process",
                "QC": "Review",
                "Software Dependencies": "System",
            }.get(name)
            if group:
                if name == "Software Dependencies":
                    sidebar.addStretch()
                heading = label(group, "caption")
                heading.setContentsMargins(8, 8, 0, 0)
                sidebar.addWidget(heading)
                self.nav_headers.append(heading)
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
                else self.focus_dependencies
                if name == "Software Dependencies"
                else self.focus_preferences
                if name == "Settings"
                else self.focus_data_check
                if name == "Data Check"
                else self.focus_workflow
                if name == "Preprocessing"
                else self.focus_registration
                if name == "Registration"
                else self.focus_batch
                if name == "Batch Processing"
                else self.focus_models
                if name == "Reconstruction"
                else self.focus_connectivity
                if name == "Connectivity"
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
                    "Reconstruction": "Import independent inputs and plan diffusion models; execution adapters are not implemented",
                    "Registration": "Register images and review atlases on preprocessed diffusion",
                    "Batch Processing": "Import studies, review workflows, execute and recover batches",
                    "Connectivity": "Build and inspect connectomes from existing tracts and registered nodes",
                    "Data Check": "Validate loaded inputs",
                    "QC": "Inspect existing quality-control results",
                    "Results": "View processing outputs and reports",
                    "Settings": "Application preferences",
                    "Software Dependencies": "Detect, locate and set up scientific software",
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
                    "Software Dependencies",
                    "Reconstruction",
                    "Connectivity",
                    "Batch Processing",
                    "Registration",
                }
            )
            theme.set_icon(nav, icon, "accent.text" if name == "Project" else "text.disabled")
            if name == "Project":
                nav.setProperty("selected", True)
                theme.bind(nav, "accent.subtle", "accent.text")
            nav.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            nav.setMinimumHeight(30)
            sidebar.addWidget(nav)
            self.nav_buttons.append((nav, name))
        self._selected_navigation = "Project"
        self.sidebar_note = label("Research alpha", "caption")
        sidebar.addWidget(self.sidebar_note)
        shell.addWidget(self.sidebar)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        self.toolbar = QToolBar("Workspace", self)
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.toolbar.setMinimumHeight(44)
        self.toolbar.setIconSize(QSize(18, 18))
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toolbar.addWidget(self.collapse)
        self.workspace_title = label("Start", "section")
        self.workspace_title.setMinimumWidth(125)
        self.toolbar.addWidget(self.workspace_title)
        self.toolbar.addSeparator()
        self.open_image_action = self.toolbar.addAction(
            theme.icon("folder-open"), "Open Image…", self.open_image
        )
        self.open_tract_action = self.toolbar.addAction(
            theme.icon("route"), "Add Tracts…", self.open_tractography
        )
        theme.set_icon(self.open_image_action, "folder-open")
        theme.set_icon(self.open_tract_action, "route")
        self.plan_tracking_action = self.toolbar.addAction(
            "Generate / filter tracks…", self.focus_tracking_plan
        )
        self.plan_tracking_action.setToolTip(
            "Generate MRtrix tracks, filter streamlines or create tract-density maps after command review and approval"
        )
        self.toolbar.addSeparator()
        self.inspector_action = self.toolbar.addAction(
            theme.icon("sliders-horizontal"), "Inspector", self.toggle_inspector
        )
        self.inspector_action.setCheckable(True)
        self.inspector_action.setChecked(True)
        theme.set_icon(self.inspector_action, "sliders-horizontal")
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer)
        self.commands_button = button("Commands", self.commands, quiet=True)
        theme.set_icon(self.commands_button, "search")
        self.toolbar.addWidget(self.commands_button)
        self.theme_button = button("", self.toggle_theme, quiet=True)
        self.toolbar.addWidget(self.theme_button)
        right_layout.addWidget(self.toolbar)
        self.project_label = ElidedLabel("No project open", "caption")
        self.dataset_label = ElidedLabel("No image open", "caption")
        status = QStatusBar(self)
        status.setSizeGripEnabled(True)
        status.addWidget(self.project_label, 1)
        status.addPermanentWidget(self.dataset_label, 2)
        self.setStatusBar(status)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.page = LoadDataPage(theme, self.settings)
        self.scroll.setWidget(self.page)
        self.workspace = QStackedWidget()
        self.workspace.addWidget(self.scroll)
        self.validation_page = DataValidationPage(theme, self.focus_load)
        self.validation_host = QScrollArea()
        self.validation_host.setWidgetResizable(True)
        self.validation_host.setWidget(self.validation_page)
        self.workspace.addWidget(self.validation_host)
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
        # Scientific and batch panels are substantial optional workspaces. Keep
        # their public attributes as cached properties, creating each on first use.
        self._scientific = None
        self._batch = None
        self._dependency_defaults = self.dependencies.saved_directories()
        self.results = ResultsPage(self.settings)
        self.results.scientific_requested.connect(self.open_scientific_selection)
        self.workspace.addWidget(self.results)
        self.preferences = PreferencesPage(self)
        self.dependency_host = QScrollArea()
        self.dependency_host.setWidgetResizable(True)
        self.dependency_host.setWidget(self.preferences.dependencies)
        self.workspace.addWidget(self.dependency_host)
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
        self.apply_dependency_locations(self._dependency_defaults)
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
        self.page.open_image_requested.connect(self.open_image)
        self.page.open_tracts_requested.connect(self.open_tractography)
        self.page.open_project_requested.connect(self.browse_project)
        self.page.open_results_requested.connect(self.browse_run)
        self.page.open_scientific_requested.connect(self.focus_models)
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
        self.setup_menus()
        self.workspace.currentChanged.connect(self.update_edit_actions)
        self.update_edit_actions()
        self.start_page = StartPage(self)
        self.workspace.addWidget(self.start_page)
        self.projects.project_changed.connect(self.remember_project)
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
        self.theme.apply(self.theme.preference)
        self.focus_start()

    @property
    def scientific(self) -> ScientificPlanningPage:
        """Create and retain scientific panels only when a caller needs them."""
        if self._scientific is None:
            page = ScientificPlanningPage(
                self.theme, lambda: self.dataset, lambda: self.workflow.history
            )
            self._scientific = page
            self.workspace.addWidget(page)
            page.notification.connect(lambda message, tone: self.toast.show_message(message, tone))
            page.execution_busy.connect(self.dti_processing_busy)
            page.inspect_requested.connect(self.inspect_scientific_input)
            page.qc_requested.connect(self.focus_qc)
            page.dti.view_requested.connect(self.open_dti_product)
            page.csd.view_requested.connect(self.open_scientific_product)
            page.tracking.view_requested.connect(self.open_scientific_product)
            page.registration.view_requested.connect(self.open_scientific_product)
            page.registration.qc_requested.connect(self.open_generated_qc)
            page.anatomy.view_requested.connect(self.open_scientific_product)
            page.anatomy.qc_requested.connect(self.open_generated_qc)
            page.anatomy.run_requested.connect(self.open_saved_run)
            page.sift2.view_requested.connect(self.open_scientific_product)
            page.sift2.qc_requested.connect(self.open_generated_qc)
            page.sift2.run_requested.connect(self.open_saved_run)
            page.connectome.view_requested.connect(self.open_scientific_product)
            page.connectome.qc_requested.connect(self.open_generated_qc)
            page.connectome.run_requested.connect(self.open_saved_run)
            page.tracking.qc_requested.connect(self.open_generated_qc)
            page.tracking.run_requested.connect(self.open_saved_run)
            page.roi_import_requested.connect(self.import_tracking_rois)
            page.csd.qc_requested.connect(self.open_generated_qc)
            page.csd.run_requested.connect(self.open_saved_run)
            page.dti.qc_requested.connect(self.open_generated_qc)
            page.dti.run_requested.connect(self.open_saved_run)
            page.processing_available = lambda: (
                self.workflow.execution.thread is None
                and (self._batch is None or self._batch.thread is None)
            )
            mrtrix = self._dependency_defaults.get("MRtrix3")
            fsl = self._dependency_defaults.get("FSL")
            page.tool_directory = Path(mrtrix) if mrtrix else None
            page.fsl_directory = Path(fsl) if fsl else None
            page.refresh()
        return self._scientific

    @property
    def batch(self):
        """Create and retain the study workspace on first use."""
        if self._batch is None:
            from .batch import BatchPage

            page = BatchPage(self.theme, settings=self.settings)
            self._batch = page
            self.workspace.addWidget(page)
            page.product_requested.connect(
                lambda product: (
                    self.open_dti_product(product)
                    if product["kind"] == "dti_vector"
                    else self.open_scientific_product(product)
                )
            )
            page.busy_changed.connect(self.dti_processing_busy)
            page.processing_available = lambda: (
                self.workflow.execution.thread is None
                and (self._scientific is None or self._scientific.thread is None)
            )
            mrtrix = self._dependency_defaults.get("MRtrix3")
            fsl = self._dependency_defaults.get("FSL")
            ants = self._dependency_defaults.get("ANTs")
            page.tool_directory = Path(mrtrix) if mrtrix else None
            page.fsl_directory = Path(fsl) if fsl else None
            page.ants_directory = Path(ants) if ants else None
            page.mrtrix_directory.setText(mrtrix or "")
        return self._batch

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
        self.toast.resolve("dataset-load")
        self.load_token = self.load_state.begin(State.VALIDATING, "Checking dataset inputs…")
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
            self.load_state.update(self.load_token, State.LOADING, progress.message)
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
            if not self.pending_result and self.page.import_intent == "preprocessed":
                self.workflow.source.setText(
                    "User imported externally preprocessed DWI. Operations, parameters and scientific validation remain unknown until supporting records are reviewed."
                )
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
            self.load_state.update(
                self.load_token,
                State.WARNING if self.page.state == "warning" else State.READY,
                self.dataset.paths.dwi.name
                + (
                    " · input warnings require review"
                    if self.page.state == "warning"
                    else " · ready"
                ),
            )
            self.record("Dataset activated. " + result.report.coverage_message())
        else:
            self.page.present_report(result.report, cancelled)
            self.load_state.update(
                self.load_token,
                State.CANCELLED if cancelled else State.FAILED,
                "Loading cancelled"
                if cancelled
                else "Could not load dataset; see candidate validation",
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
        self.load_state.update(
            self.load_token, State.FAILED, "Unexpected loader error; details in Logs"
        )

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
        self.setWindowTitle("TractoSphere — Diffusion MRI Research Studio")
        self.setWindowIcon(QIcon(str(ASSETS / "branding/tractosphere-app.svg")))
        self.record("Dataset unloaded; source files unchanged.")

    def toggle_theme(self) -> None:
        """Change only palette/icon state; all selection and work remain intact."""
        self.theme.apply("light" if self.theme.name == "dark" else "dark")
        self.settings.set("theme", self.theme.preference)
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
        for heading in self.nav_headers:
            heading.setVisible(not self.sidebar_collapsed)
        self.sidebar_note.setVisible(not self.sidebar_collapsed)
        for widget, name in self.nav_buttons:
            display = {
                "Project": "Import data",
                "Acquisitions": "Projects",
                "Viewer": "Image Viewer",
                "Data Check": "Data validation",
                "QC": "Quality Control",
                "Results": "Runs && Reports",
                "Reconstruction": "Diffusion models",
                "Settings": "Preferences",
                "Software Dependencies": "Software setup",
            }.get(name, name)
            widget.setText("" if self.sidebar_collapsed else display)
            widget.setToolTip(
                name
                + (
                    " · Planning only; execution adapters are not implemented"
                    if name in {"Reconstruction", "Connectivity"}
                    else " · Inspect existing tractograms and ROIs"
                    if name == "Tractography"
                    else " · Detect and configure scientific software"
                    if name == "Software Dependencies"
                    else " · Load Data"
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
        titles = {
            "Project": "Import data",
            "Acquisitions": "Projects",
            "Viewer": "Image Viewer",
            "QC": "Quality Control",
            "Results": "Runs & Reports",
            "Settings": "Preferences",
            "Software Dependencies": "Software setup",
        }
        self.workspace_title.setText(titles.get(selected, selected) or "Start")
        if hasattr(self, "undo_action"):
            self.update_edit_actions()
        self.open_image_action.setVisible(selected in {"Viewer", "Tractography", "Project", ""})
        viewing = selected in {"Viewer", "Tractography"}
        self.open_tract_action.setVisible(selected == "Tractography")
        self.plan_tracking_action.setVisible(selected == "Tractography")
        self.inspector_action.setVisible(viewing)
        if self.scientific_viewer:
            self.inspector_action.setChecked(not self.scientific_viewer.inspector.isHidden())
        if viewing:
            self.update_scene_identity()
        elif self.dataset:
            self.dataset_label.setText("Processing input · " + self.dataset.paths.dwi.name)
        else:
            self.dataset_label.setText("No processing input loaded")
        previous = self._selected_navigation
        self._selected_navigation = selected
        for nav, name in self.nav_buttons:
            if name not in {previous, selected}:
                continue
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

    def browse_project(self):
        self.focus_projects()
        self.projects.browse()

    def browse_run(self):
        self.focus_results()
        self.results.browse()

    def focus_projects(self):
        """Open the acquisition ledger without replacing the active image."""
        self.workspace.setCurrentWidget(self.projects)
        self.select_navigation("Acquisitions")

    def focus_registration(self):
        self.scientific.set_stage("registration")
        self.workspace.setCurrentWidget(self.scientific)
        self.select_navigation("Registration")

    def focus_batch(self):
        """Open study orchestration without changing existing viewers."""
        directory = self._scientific.tool_directory if self._scientific is not None else None
        if (
            directory
            and (Path(directory) / "mrconvert").is_file()
            and not self.batch.historical_toolchain
        ):
            self.batch.tool_directory = directory
        self.workspace.setCurrentWidget(self.batch)
        self.select_navigation("Batch Processing")

    def focus_models(self):
        """Review model contracts without requiring a preprocessed DWI or executing."""
        self.scientific.set_stage("models")
        self.workspace.setCurrentWidget(self.scientific)
        self.select_navigation("Reconstruction")

    def focus_connectivity(self):
        """Plan from existing tracts/nodes; no reconstruction prerequisite invented."""
        self.scientific.set_stage("connectivity")
        self.workspace.setCurrentWidget(self.scientific)
        self.select_navigation("Connectivity")

    def focus_tracking_plan(self):
        """Keep generation planning distinct from working tract visualization/editing."""
        self.scientific.set_stage("tractography")
        self.workspace.setCurrentWidget(self.scientific)
        self.select_navigation("Tractography")
        self.workspace_title.setText("Tractography")

    def import_tracking_rois(self):
        """Snapshot selected existing ROI masks into separate immutable inputs.

        Never edits an atlas/ROI source. The scientist explicitly chooses the
        export parent and associates the current source; equal physical grids
        are checked, while subject association remains a declared claim.
        """
        import json
        from uuid import uuid4

        import numpy as np
        from PySide6.QtWidgets import QFileDialog

        from dmri_studio.core._mif_checks import voxel_mapping
        from dmri_studio.core.scene import save_new_image
        from dmri_studio.core.scientific import ImportRequest, import_artifact

        page = self.scientific
        viewer = self.scientific_viewer
        if page.thread is not None:
            return
        source = next(
            (a for a in page.artifacts if a.id == page.tracking.source.currentData()), None
        )
        if viewer is None or viewer.reference is None or not viewer.roi_list.selected_rows():
            page.status.setText("Select one or more regions in the existing viewer ROI list first.")
            return
        if source is None or not all(
            source.payload["declarations"].get(k) for k in ("subject_id", "space_id")
        ):
            page.status.setText(
                "Select a tracking source with explicit subject and space declarations before associating viewer ROIs."
            )
            return
        try:
            voxel_mapping(
                viewer.reference.affine,
                viewer.reference.shape,
                np.asarray(source.payload["geometry"]["affine_mm"]),
                source.payload["geometry"]["shape"],
            )
            indices = viewer.roi_list.selected_rows()
            if len(indices) > 8:
                raise ValueError("Select at most eight ROI snapshots per import to bound memory.")
            snapshots = [
                (
                    viewer.rois[i]["name"],
                    np.asarray(viewer.rois[i]["data"] > 0, dtype=np.uint8),
                    viewer.rois[i].get("role", "Display"),
                )
                for i in indices
            ]
            if any(not data.any() for _, data, _ in snapshots):
                raise ValueError("Empty ROIs cannot seed or constrain tractography.")
        except ValueError as exc:
            page.status.setText(str(exc))
            return
        parent = QFileDialog.getExistingDirectory(
            self, "Choose local parent for new tracking ROI snapshots"
        )
        if not parent:
            return
        folder = Path(parent) / ("tracking-rois-" + uuid4().hex[:12])
        reference = viewer.reference
        declarations = dict(source.payload["declarations"])

        def export(cancel, progress):
            from dmri_studio.core.process import check_cancel
            from dmri_studio.core.storage import require_local_processing

            require_local_processing([folder])
            folder.mkdir(mode=0o700, exist_ok=False)
            imported = []
            provenance = []
            for i, (name, data, role) in enumerate(snapshots):
                check_cancel(cancel)
                path = folder / f"roi-{i + 1:03}.nii.gz"
                save_new_image(path, data, reference)
                imported.append(
                    import_artifact(
                        ImportRequest(
                            "roi",
                            path,
                            declarations=declarations,
                            provenance=f"Separate selected viewer ROI snapshot: {name}. Source/space association reviewed by the user; no registration or resampling. Original ROI unchanged.",
                        ),
                        cancelled=cancel,
                        progress=progress,
                    )
                )
                provenance.append(
                    dict(
                        name=name,
                        path=str(path),
                        tracking_role=role,
                        source_reference=str(reference.path),
                        scientific_acceptance="unassigned",
                    )
                )
            with (folder / "roi-snapshots.json").open("x") as stream:
                json.dump(provenance, stream, indent=2)
            return imported

        page.start("import-tracking-rois", export)

    def open_scientific_selection(self):
        """Opening a saved selection rechecks inputs and never restarts a run."""
        self.focus_models()
        self.scientific.browse_restore()

    def open_dti_product(self, product):
        """Typed verified scalar/vector handoff to the existing shared renderer."""
        self.focus_viewer()
        viewer = self.scientific_viewer
        if viewer is not None:
            if product["kind"] == "dti_vector":
                viewer.safe(
                    lambda: viewer.load_image(
                        Path(product["fa"]),
                        direction_path=Path(product["path"]),
                        expected_hashes={
                            product["path"]: product["sha256"],
                            product["fa"]: product["fa_sha256"],
                        },
                    )
                )
            else:
                viewer.safe(
                    lambda: viewer.load_image(
                        Path(product["path"]), expected_hashes={product["path"]: product["sha256"]}
                    )
                )

    def open_scientific_product(self, product):
        """Keep FOD coefficients and responses typed; never fake scalar glyphs."""
        if product["kind"] in {"registration_qc", "registration_alignment"}:
            self.show_scene("image", auto_image=False)
            self.scientific_viewer.load_registration_product(product)
            return
        if product["kind"] == "streamline_weights":
            self.focus_tracking_plan()
            self.scientific.tabs.setCurrentWidget(self.scientific.sift2)

            def read(cancel, progress):
                from dmri_studio.core.execution import hash_file

                if hash_file(Path(product["path"]), cancel) != product["sha256"]:
                    raise ValueError("Saved SIFT2 identity record changed")
                return self.scientific.sift2.read_record(product["path"], cancel)

            self.scientific.start("sift2-open", read)
            return
        if product["kind"] == "connectome":
            self.focus_connectivity()
            self.scientific.connectome.open_bundle(Path(product["path"]))
            return
        if product["kind"] == "tractogram":
            self.show_scene("tractography", auto_image=False)
            self.scientific_viewer.safe(
                lambda: self.scientific_viewer.load_tracking_product(product)
            )
            return
        if product["kind"] == "scalar":
            self.open_dti_product(product)
            return
        if product["kind"] == "fod":
            self.show_scene("image", auto_image=False)
            self.scientific_viewer.load_fod(
                product["path"],
                expected_hash=product["sha256"],
                basis=product.get("sh_basis", "mrtrix3_real_symmetric"),
                frame=product.get("sh_frame", "world_ras"),
            )
            return
        from dmri_studio.core.scientific import ImportRequest

        decl = {
            k: product[k]
            for k in ("tissue", "normalization", "sh_basis", "sh_frame")
            if k in product
        }
        if product["kind"] == "fod":
            decl.update(tissue="wm", sh_basis="mrtrix3_real_symmetric", sh_frame="world_ras")
        self.focus_models()
        from dmri_studio.core.execution import hash_file
        from dmri_studio.core.scientific import import_artifact

        def inspect(cancel, progress):
            path = Path(product["path"])
            if hash_file(path, cancel) != product["sha256"]:
                raise ValueError("Saved result changed; reopen run evidence")
            artifact = import_artifact(
                ImportRequest(
                    product["kind"],
                    path,
                    declarations=decl,
                    provenance="Hash-verified scientific output; numerical checks and scientific acceptance are separate.",
                ),
                cancelled=cancel,
                progress=progress,
            )
            if hash_file(path, cancel) != product["sha256"]:
                raise ValueError("Saved result changed during inspection")
            return artifact

        self.scientific.start("import-product", inspect)

    def inspect_scientific_input(self, artifact):
        """Use the existing renderer. Spatial correspondence is never inferred."""
        path = Path(artifact.payload["path"])
        if artifact.kind == "fod":
            self.show_scene("image", auto_image=False)
            self.scientific_viewer.load_fod(path)
            return
        if artifact.kind in {"response", "tensor"} or str(path).endswith(".mif"):
            self.focus_models()
            self.scientific.tabs.setCurrentWidget(self.scientific.input_page)
            index = next(
                (i for i, a in enumerate(self.scientific.artifacts) if a.id == artifact.id), -1
            )
            self.scientific.inputs.setCurrentRow(index)
            self.scientific.show_input(index)
            self.scientific.status.setText(
                "Typed scientific metadata; coefficients are not scalar volumes."
            )
            return
        if artifact.kind in {"tractogram", "roi", "atlas_labels", "atlas_probability"}:
            self.focus_scientific_viewer()
            viewer = self.scientific_viewer
            if viewer is not None and viewer.reference is not None:
                if artifact.kind == "tractogram":
                    viewer.safe(lambda: viewer.load_tracts(path))
                elif artifact.kind == "roi":
                    viewer.safe(lambda: viewer.load_roi(path))
                else:
                    # Reuse the existing explicit atlas space/threshold dialog.
                    # A successful contract import never supplies registration.
                    viewer.safe(lambda: viewer.choose_atlas(path))
            else:
                self.toast.show_message(
                    "Open the associated anatomical reference first; spatial alignment is unverified.",
                    "warning",
                )
        else:
            self.focus_viewer()
            if getattr(self, "scientific_viewer", None) is not None:
                self.scientific_viewer.safe(lambda: self.scientific_viewer.load_image(path))

    def focus_preferences(self):
        """Open display/privacy preferences, never processing defaults."""
        self.preferences.refresh()
        self.workspace.setCurrentWidget(self.preferences_scroll)
        self.select_navigation("Settings")

    def focus_data_check(self):
        """Navigate to the actual existing input validation evidence."""
        report = (
            self.page.failure.report
            if self.page.failure is not None
            else self.dataset.validation
            if self.dataset
            else None
        )
        self.validation_page.set_report(report)
        self.workspace.setCurrentWidget(self.validation_host)
        self.select_navigation("Data Check")

    def focus_viewer(self):
        """Open image tools over the same scene used by tractography."""
        self.show_scene("image")

    def focus_scientific_viewer(self):
        """Open tract/ROI tools without replacing or rereading the shared image."""
        self.show_scene("tractography")

    def show_scene(self, mode, *, auto_image=True):
        if self.scientific_viewer is None:
            try:
                from .scientific_viewer import ScientificViewer

                self.scientific_viewer = ScientificViewer(self.theme)
                self.scientific_viewer.mrtrix_directory = self.dependencies.saved_directories()[
                    "MRtrix3"
                ]
            except ImportError as exc:
                self.toast.show_message(f"Install dmri-studio[gui] for viewing: {exc}", "error")
                return
            self.scientific_viewer.image_loaded.connect(self.update_scene_identity)
            self.scientific_viewer.editing_changed.connect(self.update_edit_actions)
            self.scientific_viewer.tabs.currentChanged.connect(self.update_edit_actions)
            self.scientific_viewer.tract_list.currentRowChanged.connect(self.update_edit_actions)
            self.scientific_viewer.workspace_mode_changed.connect(self.scene_mode_changed)
            self.scientific_viewer.inspector_visibility_changed.connect(
                self.inspector_action.setChecked
            )
            self.scientific_viewer.fod_qc_callback = lambda path: (
                self.focus_qc(),
                self.qc.load(path),
            )
            self.scientific_viewer.scene_toolbar.hide()
            self.workspace.addWidget(self.scientific_viewer)
        viewer = self.scientific_viewer
        self.workspace.setCurrentWidget(viewer)
        viewer.set_workspace_mode(mode)
        if (
            auto_image
            and viewer.reference is None
            and self.dataset is not None
            and not viewer.tasks
        ):
            viewer.safe(lambda: viewer.load_image(self.dataset.paths.dwi))
        self.select_navigation("Viewer" if mode == "image" else "Tractography")

    def scene_mode_changed(self, mode):
        if self.scientific_viewer and self.workspace.currentWidget() is self.scientific_viewer:
            self.select_navigation("Viewer" if mode == "image" else "Tractography")

    def open_image(self):
        self.focus_viewer()
        if self.scientific_viewer:
            self.scientific_viewer.safe(self.scientific_viewer.choose_image)

    def open_tractography(self):
        self.focus_scientific_viewer()
        viewer = self.scientific_viewer
        if viewer is None:
            return
        if viewer.reference is None:
            viewer.status.setText(
                "Open the anatomical reference first, then Add Tracts from the same coordinate space."
            )
            viewer.safe(viewer.choose_image)
        else:
            viewer.safe(viewer.choose_tracts)

    def toggle_inspector(self):
        if self.scientific_viewer and self.workspace.currentWidget() is self.scientific_viewer:
            self.scientific_viewer.set_inspector_visible(self.inspector_action.isChecked())

    def focus_start(self):
        self.start_page.refresh()
        self.workspace.setCurrentWidget(self.start_page)
        self.select_navigation("")
        self.workspace_title.setText("Start")

    def focus_dependencies(self):
        self.workspace.setCurrentWidget(self.dependency_host)
        self.select_navigation("Software Dependencies")

    def remember_project(self, name):
        import json

        if not self.projects.path:
            return
        path = str(self.projects.path)
        try:
            records = json.loads(self.settings.get("recent_projects_json", "[]"))
        except (ValueError, TypeError):
            records = []
        records = [{"name": name, "path": path}] + [r for r in records if r["path"] != path]
        self.settings.set("recent_projects_json", json.dumps(records[:6]))

    def setup_menus(self):
        """Reuse validated services through standard desktop actions and shortcuts."""
        menus = {
            name: self.menuBar().addMenu(name)
            for name in ("File", "Edit", "View", "Tools", "Window", "Help")
        }

        def add(menu, title, callback, shortcut=None, role=None):
            action = QAction(title, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            if role:
                action.setMenuRole(role)
            action.triggered.connect(callback)
            menus[menu].addAction(action)
            self.actions.append(action)
            return action

        add("File", "Open Image…", self.open_image, QKeySequence.StandardKey.Open)
        add("File", "Open Project…", self.browse_project, "Ctrl+Shift+O")
        add("File", "Import Diffusion MRI…", self.focus_load)
        add("File", "Open Tractography…", self.open_tractography)
        add("File", "Open FOD Coefficients…", lambda: self.scene_action("choose_fod"))
        add("File", "Open Viewer Workspace…", lambda: self.scene_action("open_workspace"))
        add(
            "File",
            "Save Viewer Workspace…",
            lambda: self.scene_action("save_workspace"),
            QKeySequence.StandardKey.Save,
        )
        add("File", "Export View…", lambda: self.scene_action("save_screenshot"))
        self.undo_action = add(
            "Edit", "Undo", lambda: self.edit_scene(False), QKeySequence.StandardKey.Undo
        )
        self.redo_action = add(
            "Edit", "Redo", lambda: self.edit_scene(True), QKeySequence.StandardKey.Redo
        )
        add(
            "Edit",
            "Preferences…",
            self.focus_preferences,
            QKeySequence.StandardKey.Preferences,
            QAction.MenuRole.PreferencesRole,
        )
        add("View", "Start", self.focus_start)
        add("View", "Image Viewer", self.focus_viewer)
        add("View", "Tractography", self.focus_scientific_viewer)
        add("View", "Toggle Sidebar", self.toggle_sidebar, "Ctrl+B")
        add("View", "Toggle Inspector", lambda: self.inspector_action.trigger(), "Ctrl+I")
        add("View", "Processing Logs", self.toggle_logs, "Ctrl+J")
        add("Tools", "Commands…", self.commands, "Ctrl+K")
        add("Tools", "Software Dependencies…", self.focus_dependencies)
        add("Tools", "Quality Control", self.focus_qc)
        add("Tools", "Diffusion Models", self.focus_models)
        add("Tools", "Generate / filter tracks…", self.focus_tracking_plan)
        add("Tools", "Connectivity", self.focus_connectivity)
        add(
            "Window",
            "Full Screen",
            lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen(),
            "Ctrl+Shift+F",
        )
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        add(
            "Help",
            "TractoSphere Documentation",
            lambda: QDesktopServices.openUrl(
                QUrl("https://github.com/kkartikeya-kulkarni/TractoSphere#readme")
            ),
        )
        add(
            "Help",
            "Report an Issue…",
            lambda: QDesktopServices.openUrl(
                QUrl("https://github.com/kkartikeya-kulkarni/TractoSphere/issues")
            ),
        )

    def scene_action(self, method):
        if self.scientific_viewer is None:
            self.focus_viewer()
        if self.scientific_viewer:
            self.scientific_viewer.safe(getattr(self.scientific_viewer, method))

    def update_edit_actions(self, *_):
        """Only the active ROI/group undo stack can receive standard Edit commands."""
        viewer = self.scientific_viewer
        undo = redo = False
        if viewer and self.workspace.currentWidget() is viewer:
            if viewer.tabs.currentIndex() == 2:
                undo, redo = bool(viewer.roi_undo), bool(viewer.roi_redo)
            elif viewer.tabs.currentIndex() == 1 and (group := viewer.current_group()):
                undo, redo = bool(group.undo_stack), bool(group.redo_stack)
        self.undo_action.setEnabled(undo)
        self.redo_action.setEnabled(redo)
        self.undo_action.setToolTip("Undo an edit in the selected tract or ROI context.")
        self.redo_action.setToolTip("Redo an edit in the selected tract or ROI context.")

    def edit_scene(self, redo):
        viewer = self.scientific_viewer
        if viewer and self.workspace.currentWidget() is viewer:
            viewer.safe(
                lambda: (
                    viewer.undo_roi(redo)
                    if viewer.tabs.currentIndex() == 2
                    else viewer.tract_undo(redo)
                )
            )

    def update_scene_identity(self):
        if (
            self.scientific_viewer
            and self.scientific_viewer.reference
            and self.workspace.currentWidget() is self.scientific_viewer
        ):
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
        if context.get("product", {}).get("kind") in {
            "fod",
            "response",
            "tissue_scalar",
            "tractogram",
            "connectome",
        }:
            self.open_scientific_product(context["product"])
            return
        if context.get("product", {}).get("kind") in {"scalar", "dti_vector"}:
            self.open_dti_product(context["product"])
            return
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
        if not self.can_change_dependencies():
            return
        self._dependency_defaults.update(locations)
        panel = self.workflow.execution
        if "MRtrix3" in locations:
            directory = str(locations["MRtrix3"] or "")
            if self._scientific is not None:
                self._scientific.tool_directory = Path(directory) if directory else None
            panel.tool_directory.setText(directory)
            # Batch Processing must share the same saved installation instead of
            # prompting for a second MRtrix3 directory on every new batch.
            if self._batch is not None:
                self._batch.tool_directory = Path(directory) if directory else None
                if not self._batch.historical_toolchain:
                    self._batch.mrtrix_directory.setText(directory)
            if getattr(self, "scientific_viewer", None) is not None:
                self.scientific_viewer.mrtrix_directory = directory
        if "ANTs" in locations:
            directory = str(locations["ANTs"] or "")
            panel.bias_inputs["ants_directory"].setText(directory)
            if self._batch is not None:
                self._batch.ants_directory = Path(directory) if directory else None
        if "FSL" in locations:
            directory = str(locations["FSL"] or "")
            if self._scientific is not None:
                self._scientific.fsl_directory = Path(directory) if directory else None
            if self._batch is not None:
                self._batch.fsl_directory = Path(directory) if directory else None
            panel.correction_inputs["fsl_directory"].setText(directory)
            panel.anatomy_inputs.fields["fsl_directory"].setText(directory)
            panel.combination_inputs.fields["fsl_directory"].setText(directory)
        if self._scientific is not None:
            self._scientific.refresh()

    def can_change_dependencies(self):
        viewer = getattr(self, "scientific_viewer", None)
        roi_dialog = getattr(viewer, "native_roi_dialog", None)
        idle = (
            self.workflow.execution.thread is None
            and (self._scientific is None or self._scientific.thread is None)
            and (self._batch is None or self._batch.thread is None)
            and (roi_dialog is None or roi_dialog.thread is None)
        )
        if not idle or (self._batch is not None and self._batch.approval.isChecked()):
            return False
        return self._scientific is None or (
            not any(
                panel.approval.isChecked()
                for panel in (
                    self._scientific.dti,
                    self._scientific.csd,
                    self._scientific.tracking,
                    self._scientific.registration,
                    self._scientific.anatomy,
                    self._scientific.sift2,
                )
            )
            and not self._scientific.connectome.approve.isChecked()
        )

    def closeEvent(self, event: object) -> None:
        """Close cooperatively, leaving the event loop responsive during I/O."""
        # A debounce can still be pending with no live worker. Cancel its future
        # read before closing the dataset, as well as invalidating active reads.
        self.page.preview.set_dataset(None)
        dependencies_idle = self.dependencies.shutdown()
        qc_idle = self.qc.shutdown()
        results_idle = self.results.shutdown()
        projects_idle = self.projects.shutdown()
        scientific_idle = self._scientific is None or self._scientific.shutdown()
        batch_idle = self._batch is None or self._batch.shutdown()
        scene_idle = self.scientific_viewer is None or self.scientific_viewer.shutdown()
        if (
            self.load_thread
            or self.page.preview.thread
            or self.workflow.execution.thread
            or not qc_idle
            or not dependencies_idle
            or not results_idle
            or not projects_idle
            or not scientific_idle
            or not batch_idle
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
        self.settings.set("theme", self.theme.preference)
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

    def dti_processing_busy(self, busy):
        """Keep heavy GUI jobs sequential while preserving navigation and viewing."""
        self.workflow.execution.setEnabled(not busy)
        self.processing_busy(busy)

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
