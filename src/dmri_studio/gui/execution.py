"""Thin, replaceable UI around the Qt-free reviewed execution API.

All hashing, external commands and QC writes run in an owned worker. This screen
uses the current design tokens; a later GUI redesign does not alter the engine.
"""

import logging
from collections.abc import Callable
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.execution import (
    execute_plan,
    prepare_execution,
    record_qc,
    save_execution_plan,
)

from .anatomy import AnatomyInputs
from .combination import CombinationInputs
from .components import Card, Disclosure, button, label
from .theme import Theme, font
from .workers import ViewerTaskWorker

LOGGER = logging.getLogger(__name__)


class ProcessingWorker(ViewerTaskWorker):
    """Add text progress to the proven owner-thread retirement lifecycle."""

    progress = Signal(str)


class ExecutionPanel(Card):
    """Review exact commands, explicitly approve, inspect, then record human QC."""

    busy_changed = Signal(bool)
    notification = Signal(str, str)
    inspect_requested = Signal(object)
    qc_requested = Signal(object)
    results_requested = Signal(object)
    attempt_finished = Signal(object)

    def __init__(self, theme: Theme, context: Callable) -> None:
        """Gather choices only; workers receive immutable data, never widgets."""
        super().__init__(theme, "4 · Review commands and run")
        self.context = context
        self.plan = None
        self.manifest = None
        self.thread = self.worker = None
        self.cancel_event = Event()
        self.task_kind = ""
        self.review_ready = False
        self.body.addWidget(
            label(
                "Choose a new output folder, review the applicable operations and exact commands, then approve this plan. Scientific review remains separate from processing completion.",
                "caption",
            )
        )
        row = QHBoxLayout()
        self.output = QLineEdit()
        self.output.setPlaceholderText("New run folder, outside your original input folder")
        self.output.setAccessibleName("New processing run folder")
        self.browse = button("Choose parent", self.choose_parent)
        row.addWidget(self.output, 1)
        row.addWidget(self.browse)
        self.body.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(label("MRtrix bin directory", "control"))
        self.tool_directory = QLineEdit()
        self.tool_directory.setPlaceholderText("Optional: use installed tools on PATH when blank")
        self.tool_directory.setAccessibleName("Installed MRtrix bin directory")
        self.tool_directory.textChanged.connect(self.invalidate)
        self.tool_browse = button("Browse", self.choose_tools)
        row.addWidget(self.tool_directory, 1)
        row.addWidget(self.tool_browse)
        self.body.addLayout(row)
        row = QHBoxLayout()
        self.clean_scale_label = label("Mask clean scale (voxels)", "control")
        row.addWidget(self.clean_scale_label)
        self.clean_scale = QSpinBox()
        self.clean_scale.setAccessibleName(
            "Mask bridge-cleaning scale in voxels; 0 disables cleaning"
        )
        self.clean_scale.setRange(0, 10)
        self.clean_scale.setValue(2)
        row.addWidget(self.clean_scale)
        row.addWidget(label("Threads", "control"))
        self.nthreads = QSpinBox()
        self.nthreads.setRange(1, 64)
        self.nthreads.setValue(4)
        self.nthreads.setAccessibleName("MRtrix processing threads")
        row.addWidget(self.nthreads)
        row.addStretch()
        self.body.addLayout(row)
        row = QGridLayout()
        self.native_row = row
        self.native_parameters = {}
        self.native_parameter_labels = {}
        for key, title, value, maximum in [
            ("extent", "MP-PCA patch width (odd)", 5, 15),
            ("nshifts", "Gibbs subvoxel shifts", 20, 100),
            ("minW", "Gibbs min window", 1, 10),
            ("maxW", "Gibbs max window", 3, 10),
        ]:
            control = QSpinBox()
            control.setRange(3 if key == "extent" else 1, maximum)
            control.setValue(value)
            control.setAccessibleName(title)
            self.native_parameters[key] = control
            index = len(self.native_parameters) - 1
            parameter_label = label(title, "caption")
            self.native_parameter_labels[key] = parameter_label
            row.addWidget(parameter_label, index // 2, (index % 2) * 2)
            row.addWidget(control, index // 2, (index % 2) * 2 + 1)
            control.valueChanged.connect(self.invalidate)
        self.body.addLayout(row)
        bias_inputs = QWidget()
        bias_layout = QVBoxLayout(bias_inputs)
        bias_layout.setContentsMargins(0, 0, 0, 0)
        bias_layout.addWidget(
            label(
                "For N4 only: load the direct EDDY output with rotated gradients. Supply its passing correction gate, producer record and an explicit fitting mask. Unknown or completed bias-correction history prevents a second correction. Baseline: 100 mm cubic spline, 1000 iterations, threshold 0, shrink 4.",
                "caption",
            )
        )
        self.bias_inputs = {}
        self.bias_browse = []
        for key, title in [
            ("fitting_mask", "Fitting mask NIfTI"),
            ("correction_record", "Validated EDDY record JSON"),
            ("correction_gate", "Passing correction gate JSON"),
            ("ants_directory", "ANTs bin directory (optional if on PATH)"),
        ]:
            row = QHBoxLayout()
            field_label = label(title, "caption")
            field_label.setFixedWidth(170)
            row.addWidget(field_label)
            field = QLineEdit()
            field.setPlaceholderText(title)
            field.setAccessibleName(title)
            field.textChanged.connect(self.invalidate)
            self.bias_inputs[key] = field
            row.addWidget(field, 1)
            choose = button("Browse", lambda _, k=key: self.choose_bias_input(k))
            self.bias_browse.append(choose)
            row.addWidget(choose)
            bias_layout.addLayout(row)
        self.bias_disclosure = Disclosure("N4 fitting mask and upstream evidence", bias_inputs)
        self.body.addWidget(self.bias_disclosure)
        correction_inputs = QWidget()
        correction_layout = QVBoxLayout(correction_inputs)
        correction_layout.setContentsMargins(0, 0, 0, 0)
        correction_layout.addWidget(
            label(
                "Single-acquisition CPU baseline: TOPUP, BET fitting mask, EDDY with replacement and diagnostics, then dwi2mask clean_scale=2. Confirmed metadata and reverse-PE b0 association are required. Run selected denoising/Gibbs first; supply their producer record when loading that output. Failed diagnostics keep N4/alignment pending.",
                "caption",
            )
        )
        self.references = QPlainTextEdit()
        self.references.setPlaceholderText("Associated magnitude EPI b0 NIfTI paths, one per line")
        self.references.setAccessibleName("Associated reverse phase calibration images")
        self.references.setMaximumHeight(85)
        self.references.textChanged.connect(self.invalidate)
        correction_layout.addWidget(self.references)
        self.reference_browse = button("Choose calibration images", self.choose_references)
        correction_layout.addWidget(self.reference_browse)
        self.correction_inputs, self.correction_browse = {}, []
        for key, title in [
            ("bids_root", "BIDS root (optional if detected)"),
            ("native_record", "Native producer JSON (if derived)"),
            ("fsl_directory", "FSL bin directory"),
        ]:
            row = QHBoxLayout()
            field_label = label(title, "caption")
            field_label.setFixedWidth(190)
            row.addWidget(field_label)
            field = QLineEdit()
            field.setAccessibleName(title)
            field.setPlaceholderText(title)
            field.textChanged.connect(self.invalidate)
            self.correction_inputs[key] = field
            row.addWidget(field, 1)
            choose = button("Browse", lambda _, k=key: self.choose_correction_input(k))
            self.correction_browse.append(choose)
            row.addWidget(choose)
            correction_layout.addLayout(row)
        self.correction_disclosure = Disclosure(
            "TOPUP/EDDY acquisition and calibration evidence", correction_inputs
        )
        self.body.addWidget(self.correction_disclosure)
        self.anatomy_inputs = AnatomyInputs()
        self.anatomy_inputs.changed.connect(self.invalidate)
        self.anatomy_disclosure = Disclosure(
            "Anatomical inputs, history and review", self.anatomy_inputs
        )
        self.body.addWidget(self.anatomy_disclosure)
        self.combination_inputs = CombinationInputs()
        self.combination_inputs.changed.connect(self.invalidate)
        self.combination_disclosure = Disclosure(
            "Matched AP/PA combination evidence", self.combination_inputs
        )
        self.body.addWidget(self.combination_disclosure)
        self.status = label("Select operations in a confirmed, eligible proposal.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.body.addWidget(self.status)
        self.commands = QPlainTextEdit()
        self.commands.setReadOnly(True)
        self.commands.setPlaceholderText(
            "Exact commands and input constraints appear here after preparing a preview. Review them before approving execution."
        )
        self.commands.setAccessibleName("Exact processing commands, paths and constraints")
        self.commands.setFont(font("mono"))
        self.commands.setMinimumHeight(150)
        self.commands.setMaximumHeight(280)
        actions = QGridLayout()
        self.prepare = button("Prepare command preview", self.prepare_plan)
        self.export = button("Export execution plan", self.export_plan)
        self.run = button("Approve displayed plan and run", self.run_plan, primary=True)
        self.cancel = button("Cancel", self.cancel_work)
        for index, control in enumerate((self.prepare, self.export, self.run, self.cancel)):
            actions.addWidget(control, index // 2, index % 2)
        self.body.addLayout(actions)
        self.body.addWidget(self.commands)
        self.progress = label("", "caption")
        self.progress.setTextFormat(Qt.TextFormat.PlainText)
        self.body.addWidget(self.progress)
        self.open_qc = button("Open generated step QC", self.inspect_qc)
        self.body.addWidget(self.open_qc)
        self.open_results = button("Open saved run and outputs", self.inspect_results)
        self.body.addWidget(self.open_results)
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self.update_elapsed)
        self.elapsed = 0
        self.mask_review_heading = label("5 · Inspect mask and record anatomical QC", "section")
        self.body.addWidget(self.mask_review_heading)
        self.qc_status = label(
            "After structural validation, inspect the mask across slices. A successful command does not approve brain coverage.",
            "caption",
        )
        self.body.addWidget(self.qc_status)
        self.inspect = button("Inspect mask on original DWI", self.inspect_mask)
        self.body.addWidget(self.inspect)
        self.reviewer = QLineEdit()
        self.reviewer.setPlaceholderText("Reviewer name")
        self.reviewer.setAccessibleName("Anatomical QC reviewer")
        self.body.addWidget(self.reviewer)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText(
            "Coverage, missing brain, excess tissue, or reason for rejection"
        )
        self.reason.setAccessibleName("Anatomical QC review explanation")
        self.body.addWidget(self.reason)
        row = QHBoxLayout()
        self.accept = button("Accept mask", lambda: self.write_qc("accepted"))
        self.reject = button("Reject mask", lambda: self.write_qc("rejected"))
        row.addWidget(self.accept)
        row.addWidget(self.reject)
        row.addStretch()
        self.body.addLayout(row)
        self.output.textChanged.connect(self.invalidate)
        self.clean_scale.valueChanged.connect(self.invalidate)
        self.nthreads.valueChanged.connect(self.invalidate)
        self.reset()

    def reset(self) -> None:
        """A successful input replacement discards stale approval and QC context."""
        self.plan = self.manifest = None
        self.review_ready = False
        self.commands.clear()
        self.reviewer.clear()
        self.reason.clear()
        self.qc_status.setText(
            "After structural validation, inspect the mask across slices. Human anatomical QC is pending."
        )
        self.invalidate()

    def invalidate(self, *_: object) -> None:
        """Changed inputs, history, selection, destination or parameters void approval."""
        if self.thread is None:
            self.plan = None
            self.commands.clear()
            self.status.setText("Prepare a fresh command preview before approving a run.")
        self.refresh_actions()

    def refresh_actions(self) -> None:
        """Keep actions consistent with actual review, worker and validation state."""
        busy = self.thread is not None
        _, _, operations = self.context()
        bias_selected = "bias" in operations
        correction_selected = "correction" in operations
        combination_selected = "combine" in operations
        self.combination_disclosure.setVisible(combination_selected)
        anatomy_selected = bool({"anatomy", "alignment"} & set(operations))
        self.anatomy_inputs.set_operation("alignment" if "alignment" in operations else "anatomy")
        self.anatomy_disclosure.setVisible(anatomy_selected)
        self.clean_scale_label.setVisible("mask" in operations)
        self.clean_scale.setVisible("mask" in operations)
        for key, control in self.native_parameters.items():
            applicable = "denoise" in operations if key == "extent" else "gibbs" in operations
            # Visibility follows the already-selected operation; values/defaults
            # and the backend's eligibility checks are unchanged.
            control.setVisible(applicable)
            self.native_parameter_labels[key].setVisible(applicable)
        self.bias_disclosure.setVisible(bias_selected)
        self.correction_disclosure.setVisible(correction_selected)
        self.prepare.setEnabled(not busy)
        self.export.setEnabled(not busy and self.plan is not None)
        self.run.setEnabled(not busy and self.plan is not None)
        self.cancel.setEnabled(busy and not self.cancel_event.is_set())
        for control in (
            self.output,
            self.browse,
            self.clean_scale,
            self.nthreads,
            self.tool_directory,
            self.tool_browse,
            *self.native_parameters.values(),
            *self.bias_inputs.values(),
            *self.bias_browse,
            self.references,
            self.reference_browse,
            *self.correction_inputs.values(),
            *self.correction_browse,
            self.anatomy_inputs,
            self.combination_inputs,
        ):
            control.setEnabled(not busy)
        self.open_results.setEnabled(not busy and self.manifest is not None)
        validated_mask = bool(
            self.manifest
            and self.manifest["status"] == "validated_pending_qc"
            and "mask.nii.gz" in self.manifest.get("outputs", {})
        )
        for widget in (
            self.mask_review_heading,
            self.qc_status,
            self.inspect,
            self.reviewer,
            self.reason,
            self.accept,
            self.reject,
        ):
            widget.setVisible("mask" in operations or validated_mask)
        self.inspect.setEnabled(not busy and validated_mask)
        catalog = (
            Path(self.manifest["plan"]["output_directory"]) / "qc-catalog.json"
            if self.manifest
            else None
        )
        self.open_qc.setEnabled(not busy and catalog is not None and catalog.is_file())
        for control in (self.reviewer, self.reason, self.accept, self.reject):
            control.setEnabled(not busy and validated_mask and self.review_ready)

    def choose_tools(self):
        """Select existing local binaries; detection binds versions/hashes during preview."""
        path = QFileDialog.getExistingDirectory(self, "Installed MRtrix bin directory")
        if path:
            self.tool_directory.setText(path)

    def choose_parent(self) -> None:
        """Select a parent; the named run subdirectory must remain nonexistent."""
        directory = QFileDialog.getExistingDirectory(self, "Choose output parent folder")
        if directory:
            from datetime import datetime

            self.output.setText(str(Path(directory) / f"dmri-run-{datetime.now():%Y%m%d-%H%M%S}"))

    def choose_bias_input(self, key: str) -> None:
        """Choose explicit evidence; filenames never imply a validated handoff."""
        if key == "ants_directory":
            path = QFileDialog.getExistingDirectory(self, "Select installed ANTs bin directory")
        else:
            path, _ = QFileDialog.getOpenFileName(self, self.bias_inputs[key].placeholderText())
        if path:
            self.bias_inputs[key].setText(path)

    def choose_references(self) -> None:
        """Select explicit calibration files; metadata validation decides eligibility."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Associated magnitude EPI b0 images", "", "NIfTI (*.nii *.nii.gz)"
        )
        if paths:
            self.references.setPlainText("\n".join(paths))

    def choose_correction_input(self, key: str) -> None:
        """Keep native producer evidence separate from anatomical or N4 history."""
        if key == "native_record":
            path, _ = QFileDialog.getOpenFileName(
                self, "Native processing producer JSON", "", "JSON (*.json)"
            )
        else:
            path = QFileDialog.getExistingDirectory(
                self, self.correction_inputs[key].placeholderText()
            )
        if path:
            self.correction_inputs[key].setText(path)

    def prepare_plan(self) -> None:
        """Snapshot choices, then detect tools and hash files off the event thread."""
        if self.thread is not None:
            return
        dataset, history, operations = self.context()
        if dataset is None or not self.output.text().strip():
            self.status.setText("Load a dataset and enter a new output folder first.")
            return
        output = Path(self.output.text().strip())
        clean_scale, nthreads = self.clean_scale.value(), self.nthreads.value()
        native_parameters = {k: v.value() for k, v in self.native_parameters.items()}
        bias_inputs = {
            k: Path(v.text().strip()) if v.text().strip() else None
            for k, v in self.bias_inputs.items()
        }
        correction_inputs = {
            k: Path(v.text().strip()) if v.text().strip() else None
            for k, v in self.correction_inputs.items()
        }
        references = tuple(
            Path(line.strip())
            for line in self.references.toPlainText().splitlines()
            if line.strip()
        )
        tool_directory = (
            Path(self.tool_directory.text().strip()) if self.tool_directory.text().strip() else None
        )
        anatomy_options = self.anatomy_inputs.options()
        combination_options = self.combination_inputs.options()
        self.start_task(
            "prepare",
            lambda progress, cancelled: prepare_execution(
                dataset,
                history,
                operations,
                output,
                clean_scale=clean_scale,
                nthreads=nthreads,
                cancelled=cancelled,
                progress=progress,
                **native_parameters,
                **bias_inputs,
                **correction_inputs,
                references=references,
                tool_directory=tool_directory,
                anatomy_options=anatomy_options,
                combination_options=combination_options,
            ),
        )

    def run_plan(self) -> None:
        """This separate button approves only the exact immutable displayed plan."""
        if self.plan is None or self.thread is not None:
            return
        dataset, _, _ = self.context()
        plan = self.plan
        self.manifest = None
        self.review_ready = False
        self.start_task(
            "run",
            lambda progress, cancelled: execute_plan(
                dataset, plan, approval=plan.digest, cancelled=cancelled, progress=progress
            ),
        )

    def start_task(self, kind: str, task: Callable) -> None:
        """Use the existing worker ownership rule and thread-safe cancellation."""
        self.cancel_event = Event()
        self.task_kind = kind
        self.elapsed = 0
        self.thread = QThread(self)
        # The task is data-only; emitting a Qt signal is safe across threads.
        worker = ProcessingWorker(0, lambda: None)
        cancel = self.cancel_event
        worker.task = lambda: task(worker.progress.emit, cancel.is_set)
        self.worker = worker
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.show_progress)
        self.worker.result.connect(self.task_result)
        self.worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.task_finished)
        self.status.setText(
            "Preparing reviewed commands…"
            if kind == "prepare"
            else "Running approved plan…"
            if kind == "run"
            else "Saving human QC decision…"
        )
        self.refresh_actions()
        self.busy_changed.emit(True)
        self.clock.start()
        self.thread.start()

    def show_progress(self, text: str) -> None:
        """Expose actual stages/tool output and full logs without fake percentages."""
        self.progress.setText(f"Elapsed {self.elapsed}s · " + text[-1200:])
        LOGGER.info("%s", text)

    def update_elapsed(self) -> None:
        """Keep the user informed even while an external command is silent."""
        self.elapsed += 1

    def task_result(self, _: int, result: object, error: str) -> None:
        """Distinguish prepared commands, structural validation and human approval."""
        if error:
            self.status.setText(error)
            self.notification.emit(error, "error")
            if self.task_kind == "run":
                self.plan = None
            return
        if self.task_kind == "prepare":
            self.plan = result
            payload = result.to_dict()
            lines = [
                f"Output folder: {payload['output_directory']}",
                "Parameters: "
                + ", ".join(
                    f"{k}={v}"
                    for k, v in payload["parameters"].items()
                    if not isinstance(v, (dict, list))
                ),
            ]
            lines += [command["display"] for command in payload["commands"]]
            lines += [
                f"{name}: {tool['path']} · version {tool['version']}"
                for name, tool in payload["tools"].items()
            ]
            if "gibbs" in payload["parameters"]:
                axes = payload["parameters"]["gibbs"]
                lines += [
                    f"Gibbs slice axis: native {axes['slice_axis']} ({axes['slice_axis_source']}); MRtrix correction axes {axes['mif_axes']}. Partial Fourier: {axes['partial_fourier_original'] or 'undocumented'}. Exact header inspection is retained in the exported plan."
                ]
            if "resources" in payload:
                lines += [
                    f"Estimated peak additional disk: {payload['resources']['estimated_peak_additional_bytes'] / 1024**3:.2f} GiB. This is a conservative capacity estimate, not a runtime estimate."
                ]
            lines += payload["internal_actions"] + payload["scientific_limits"]
            lines += [f"Approval digest: {result.digest}"]
            self.commands.setPlainText("\n\n".join(lines))
            self.status.setText(
                "Ready for command review. Nothing has run; this folder will be created only after approval."
            )
        elif self.task_kind == "run":
            self.manifest = result
            self.plan = None  # New output directory exists; a plan is single use.
            output = result["plan"]["output_directory"]
            title = {
                "validated_pending_qc": "Technical checks passed · Scientific QC pending",
                "validated": "Conversion and structural checks complete",
                "cancelled": "Processing cancelled · Incomplete outputs retained",
                "failed": "Processing failed · Diagnostic logs retained",
            }.get(result["status"], "Processing incomplete")
            self.status.setText(
                f"{title}\nOutputs saved in {output}"
                + (f"\n{result['error']}" if "error" in result else "")
                + (
                    f"\nQC export failed: {result['qc_export_error']}. Preserve the verified outputs; repair the report export without repeating preprocessing."
                    if "qc_export_error" in result
                    else ""
                )
            )
            self.qc_status.setText(
                "Structural checks passed. Open the mask on the original DWI and review coverage across slices before accepting/rejecting."
                if result["status"] == "validated_pending_qc"
                and "mask.nii.gz" in result.get("outputs", {})
                else "Inspect the generated step QC. This run has no mask eligible for anatomical review."
            )
            self.notification.emit(
                self.status.text(),
                "success"
                if result["status"] in {"validated", "validated_pending_qc"}
                else "warning"
                if result["status"] == "cancelled"
                else "error",
            )
        else:
            self.review_ready = False
            self.qc_status.setText(f"Human QC recorded: {result}")
            self.status.setText("Anatomical QC decision saved. Processing records are retained.")
            self.manifest = None
            self.notification.emit(self.qc_status.text(), "success")

    def task_finished(self) -> None:
        """Re-enable editing only after subprocess, integrity checks and worker exit."""
        self.thread.wait()
        self.thread.deleteLater()
        self.thread = self.worker = None
        self.clock.stop()
        self.progress.setText(
            f"Finished after {self.elapsed}s · "
            + (
                "Command preview prepared; no scientific processing has run."
                if self.task_kind == "prepare" and self.plan
                else "See the result above and the saved run logs."
            )
        )
        LOGGER.info("%s", self.status.text())
        self.refresh_actions()
        self.busy_changed.emit(False)
        if self.task_kind == "run" and self.manifest:
            from dmri_studio.core.runs import MANIFESTS

            plan = self.manifest["plan"]
            if plan.get("kind") in MANIFESTS:
                self.attempt_finished.emit(Path(plan["output_directory"]) / MANIFESTS[plan["kind"]])

    def cancel_work(self) -> None:
        """Terminate the scientific child, then safely verify source preservation."""
        self.cancel_event.set()
        self.cancel.setEnabled(False)
        self.status.setText("Cancelling safely; final input verification may take a moment.")

    def export_plan(self) -> None:
        """Export the displayed commands and digest to a new JSON destination."""
        if self.plan is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export execution plan", "execution-plan.json", "JSON (*.json)"
        )
        if path:
            try:
                saved = save_execution_plan(self.plan, Path(path))
                self.notification.emit(f"Execution plan exported: {saved}", "success")
            except (OSError, ValueError) as exc:
                self.notification.emit(str(exc), "error")

    def inspect_results(self):
        """Open the exact existing manifest without executing any operation."""
        if self.manifest is None:
            return
        from dmri_studio.core.runs import MANIFESTS

        plan = self.manifest["plan"]
        path = Path(plan["output_directory"]) / MANIFESTS[plan["kind"]]
        self.results_requested.emit(path)

    def inspect_qc(self) -> None:
        """Open saved step checks/comparisons without recomputing their analyses."""
        if self.manifest:
            self.qc_requested.emit(
                Path(self.manifest["plan"]["output_directory"]) / "qc-catalog.json"
            )

    def inspect_mask(self) -> None:
        """Open only the validated run's native-grid mask in the working viewer."""
        if self.manifest:
            self.inspect_requested.emit(
                Path(self.manifest["plan"]["output_directory"]) / "mask.nii.gz"
            )

    def mark_inspected(self, path: Path) -> None:
        """Enable human decisions after the viewer actually loaded the expected mask."""
        if (
            self.manifest
            and path == Path(self.manifest["plan"]["output_directory"]) / "mask.nii.gz"
        ):
            self.review_ready = True
            self.qc_status.setText(
                "Mask loaded on the original DWI. Review across slices, then enter your name and assessment before accepting/rejecting."
            )
            self.refresh_actions()

    def write_qc(self, decision: str) -> None:
        """Bind the human assessment to checked output bytes in a worker."""
        if not self.manifest or not self.review_ready or self.thread:
            return
        reviewer, reason = self.reviewer.text(), self.reason.text()
        if not reviewer.strip() or not reason.strip():
            self.qc_status.setText(
                "Enter your reviewer name and anatomical review explanation first."
            )
            return
        folder = Path(self.manifest["plan"]["output_directory"])
        self.start_task(
            "qc",
            lambda _, cancelled: record_qc(
                folder, decision, reason, reviewer=reviewer, cancelled=cancelled
            ),
        )
