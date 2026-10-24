"""Compact DTI review/execution controls in the existing scientific workspace.

This widget owns no processes or scientific state. The parent owns its existing
ProcessingWorker lifecycle, task inventory, dependency paths and cancellation.
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLayout,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.execution import execute_plan, hash_file
from dmri_studio.core.scientific import OutputRef, Task
from dmri_studio.core.scientific.dti import prepare_dti

from .components import Disclosure, button, label


class DTIExecutionPanel(QScrollArea):
    """Explicit shell selection, reviewed commands and exact-digest run approval."""

    view_requested = Signal(object)
    qc_requested = Signal(object)
    run_requested = Signal(object)

    def __init__(self, owner):
        """Reuse compact native controls and the established task/worker architecture."""
        super().__init__(owner)
        self.owner = owner
        self.plan = None
        self.manifest = None
        self.setWidgetResizable(True)
        content = QWidget()
        self.setWidget(content)
        root = QVBoxLayout(content)
        root.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        root.addWidget(label("Diffusion tensor processing", "section"))
        root.addWidget(
            label(
                "Fit a tensor from compatible DWI, or calculate maps from an existing six-component MRtrix tensor. FA, MD, AD and RD are derived measurements, not diagnoses. No preprocessing is repeated.",
                "caption",
            )
        )
        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.form = form
        self.task_mode = QComboBox()
        self.task_mode.setAccessibleName("DTI task")
        self.task_mode.addItem("Fit a tensor from diffusion MRI", "fit")
        self.task_mode.addItem("Calculate maps from an existing tensor", "metrics")
        form.addRow("What would you like to do?", self.task_mode)
        self.source = QComboBox()
        self.source.setAccessibleName("DTI DWI input")
        self.source.setPlaceholderText("No imported DWI — use Import DWI")
        self.acquisition = label(
            "Import DWI to inspect its acquisition and gradient summary.", "caption"
        )
        self.source.currentIndexChanged.connect(self.source_info)
        self.shells = QLineEdit()
        self.shells.setPlaceholderText("Explicit selection, including b0: e.g. 0, 1000")
        self.shells.setAccessibleName("DTI selected shells")
        self.fit = QComboBox()
        self.fit.addItems(["IWLS", "WLS", "OLS"])
        self.iterations = QSpinBox()
        self.iterations.setRange(1, 10)
        self.iterations.setValue(2)
        self.iterations.setToolTip(
            "IWLS: initial empirical-signal WLS, then this many predicted-signal reweightings. Guided recommendation follows MRtrix 3.0.5 default, subject to acquisition suitability."
        )
        self.fit.currentTextChanged.connect(lambda text: self.iterations.setEnabled(text == "IWLS"))
        self.mask = QComboBox()
        self.output = QLineEdit()
        self.output.setPlaceholderText("New attempt directory, outside original input folders")
        self.output.setAccessibleName("DTI output directory")
        out = QHBoxLayout()
        out.addWidget(self.output, 1)
        out.addWidget(button("Choose parent…", self.choose_parent))
        self.threads = QSpinBox()
        self.threads.setRange(1, 64)
        self.threads.setValue(4)
        source_row = QWidget()
        source_layout = QHBoxLayout(source_row)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.addWidget(self.source, 1)
        self.import_dwi = button("Import DWI…", lambda: self.owner.browse_import("dwi"))
        self.import_dwi.setAccessibleName("Import DWI for tensor fitting")
        source_layout.addWidget(self.import_dwi)
        form.addRow("DWI", source_row)
        self.source_row = source_row
        self.tensor = QComboBox()
        self.tensor.setAccessibleName("Existing DTI tensor input")
        self.tensor.currentIndexChanged.connect(self.tensor_info)
        tensor_row = QWidget()
        tensor_layout = QHBoxLayout(tensor_row)
        tensor_layout.setContentsMargins(0, 0, 0, 0)
        tensor_layout.addWidget(self.tensor, 1)
        self.import_tensor = button("Import tensor…", lambda: self.owner.browse_import("tensor"))
        self.import_tensor.setAccessibleName("Import existing six-component tensor")
        tensor_layout.addWidget(self.import_tensor)
        form.addRow("Existing tensor", tensor_row)
        self.tensor_row = tensor_row
        for title, field in [
            ("Shells (s/mm²)", self.shells),
            ("Fit", self.fit),
        ]:
            field.setMinimumHeight(max(28, field.fontMetrics().height() + 10))
            form.addRow(title, field)
        form.addRow("New output directory", out)
        root.addLayout(form)
        root.addWidget(self.acquisition)
        advanced = QWidget()
        advanced_form = QFormLayout(advanced)
        self.advanced_form = advanced_form
        advanced_form.addRow("IWLS reweightings", self.iterations)
        mask_row = QWidget()
        mask_line = QHBoxLayout(mask_row)
        mask_line.setContentsMargins(0, 0, 0, 0)
        mask_line.addWidget(self.mask, 1)
        self.import_mask = button("Import mask…", lambda: self.owner.browse_import("mask"))
        self.import_mask.setAccessibleName("Import optional DTI binary mask")
        mask_line.addWidget(self.import_mask)
        advanced_form.addRow("Optional binary mask", mask_row)
        advanced_form.addRow("Threads", self.threads)
        self.advanced = Disclosure("Advanced tensor fitting settings", advanced)
        root.addWidget(self.advanced)
        self.guided_note = label(
            "Guided recommendation: IWLS with two reweightings, following MRtrix 3.0.5. Select shells explicitly after acquisition review. Advanced task parameters remain in Algorithms / Configure task.",
            "caption",
        )
        root.addWidget(self.guided_note)
        actions = QHBoxLayout()
        self.guided = button("Configure tensor and metric tasks", self.add_guided)
        self.preview = button("Validate and review commands", self.prepare)
        actions.addWidget(self.guided)
        actions.addWidget(self.preview)
        actions.addStretch()
        root.addLayout(actions)
        self.approval = QCheckBox(
            "I reviewed the exact commands, input provenance, shell selection and scientific limitations"
        )
        self.approval.toggled.connect(self.update_actions)
        root.addWidget(self.approval)
        self.run_button = button("Run approved DTI", self.run)
        run_row = QHBoxLayout()
        run_row.addWidget(self.run_button)
        run_row.addStretch()
        root.addLayout(run_row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(110)
        self.log.setMaximumBlockCount(3000)
        self.log.setAccessibleName("DTI execution commands and live logs")
        root.addWidget(self.log, 1)
        self.result_label = label("No DTI attempt completed in this workspace.", "caption")
        root.addWidget(self.result_label)
        results = QHBoxLayout()
        self.maps = QComboBox()
        self.maps.setAccessibleName("Verified DTI results")
        self.open_button = button("Open result", self.open_result)
        self.qc_button = button("Review QC", self.open_qc)
        self.history_button = button("Run details", self.open_run)
        for item in (self.maps, self.open_button, self.qc_button, self.history_button):
            results.addWidget(item)
        root.addLayout(results)
        self.output.textChanged.connect(self.invalidate)
        self.threads.valueChanged.connect(self.invalidate)
        for combo in (self.task_mode, self.source, self.tensor, self.mask, self.fit):
            combo.currentIndexChanged.connect(self.invalidate)
        self.task_mode.currentIndexChanged.connect(self.show_task_mode)
        self.shells.textChanged.connect(self.invalidate)
        self.iterations.valueChanged.connect(self.invalidate)
        self.refresh()

    def show_task_mode(self, *_):
        """Expose only the input and parameters used by the selected operation."""
        fitting = self.task_mode.currentData() == "fit"
        for widget in (self.source_row, self.shells, self.fit):
            self.form.setRowVisible(widget, fitting)
        self.form.setRowVisible(self.tensor_row, not fitting)
        self.advanced_form.setRowVisible(self.iterations, fitting)
        self.guided_note.setVisible(fitting)
        if fitting:
            self.source_info()
        else:
            self.tensor_info()
        self.guided.setText(
            "Configure tensor and metric tasks"
            if fitting
            else "Configure metrics from existing tensor"
        )
        self.update_actions()

    def invalidate(self, *_):
        """Any configuration change requires a new command preview and approval."""
        self.plan = None
        self.approval.setChecked(False)
        self.update_actions()

    def refresh(self):
        """Inventory changes invalidate approval but never change source scientific files."""
        self.invalidate()
        selected_source = self.source.currentData()
        selected_tensor = self.tensor.currentData()
        selected_mask = self.mask.currentData()
        self.source.clear()
        self.tensor.clear()
        self.tensor.addItem("No imported tensor — use Import tensor", None)
        self.mask.clear()
        self.mask.addItem("No mask — all voxels must be valid", None)
        for artifact in self.owner.artifacts:
            if artifact.kind == "dwi":
                self.source.addItem(Path(artifact.payload["path"]).name, artifact.id)
            elif artifact.kind == "tensor":
                self.tensor.addItem(Path(artifact.payload["path"]).name, artifact.id)
            elif artifact.kind == "mask":
                self.mask.addItem(Path(artifact.payload["path"]).name, artifact.id)
        if self.source.count():
            restored = self.source.findData(selected_source) if selected_source else -1
            self.source.setCurrentIndex(restored if restored >= 0 else 0)
        self.tensor.setCurrentIndex(max(0, self.tensor.findData(selected_tensor)))
        self.mask.setCurrentIndex(max(0, self.mask.findData(selected_mask)))
        self.show_task_mode()
        self.update_actions()

    def source_info(self, *_):
        """Show actual imported shell/frame evidence; never infer gradient anatomy."""
        if self.task_mode.currentData() == "metrics":
            return
        current = self.source.currentData()
        artifact = next((a for a in self.owner.artifacts if a.id == current), None)
        if artifact is None:
            self.acquisition.setText("Import DWI to inspect its acquisition and gradient summary.")
            return
        g = artifact.to_dict()["gradients"]
        groups = ", ".join(f"{s['b_median']:g}: {s['count']} volumes" for s in g["shells"])
        self.acquisition.setText(
            f"Available b-value groups (s/mm²): {groups}. Frame: {g['convention']}. Corrected-gradient correspondence and external scientific provenance require review."
        )

    def tensor_info(self, *_):
        """Keep imported tensors visible while explaining metric eligibility."""
        if self.task_mode.currentData() != "metrics":
            return
        artifact = next(
            (a for a in self.owner.artifacts if a.id == self.tensor.currentData()), None
        )
        if artifact is None:
            self.acquisition.setText(
                "Import a six-component tensor. Declare its component order, coordinate frame and mm²/s units from source documentation."
            )
            return
        declarations = artifact.payload["declarations"]
        if self._metric_tensor_eligible(artifact):
            self.acquisition.setText(
                "Tensor format is eligible for metric planning. Confirm its source identity and scientific processing history during review."
            )
        else:
            self.acquisition.setText(
                "Imported tensor is not eligible for MRtrix tensor metrics: this operation requires MRtrix D11/D22/D33/D12/D13/D23 order, world-RAS frame and mm²/s units. Review its declarations against source documentation, or use a separately validated conversion; no conversion is inferred. "
                f"Current declarations: {declarations.get('tensor_layout', 'unknown')}, {declarations.get('tensor_frame', 'unknown')}, {declarations.get('tensor_units', 'unknown')}."
            )

    @staticmethod
    def _metric_tensor_eligible(artifact):
        declarations = artifact.payload["declarations"]
        return (
            declarations.get("tensor_layout") == "mrtrix_D11_D22_D33_D12_D13_D23"
            and declarations.get("tensor_frame") == "world_ras"
            and declarations.get("tensor_units") == "mm2/s"
        )

    def choose_parent(self):
        """Select a parent; users choose a new immutable attempt name in the field."""
        path = QFileDialog.getExistingDirectory(self, "DTI output parent")
        if path:
            self.output.setText(str(Path(path) / "dti-attempt001"))

    def add_guided(self):
        """Suggest documented IWLS without implicitly choosing acquisition shells."""
        try:
            if self.task_mode.currentData() == "metrics":
                self.add_existing_tensor_metrics()
                return
            source = self.source.currentData()
            if source is None:
                raise ValueError("Import DWI with matching gradients first")
            if any(
                t.algorithm == "dwi2tensor" and t.inputs.get("dwi") == source
                for t in self.owner.tasks
            ):
                raise ValueError(
                    "A DTI fit for this input already exists. Edit it in Workflow review, or explicitly configure a separate comparison task in Algorithms."
                )
            shells = [float(v.strip()) for v in self.shells.text().split(",")]
            used = {t.id for t in self.owner.tasks}
            number = 1
            while f"dti{number}" in used or f"metrics{number}" in used:
                number += 1
            fit = self.fit.currentText()
            params = dict(shells=shells, fit=fit)
            if fit == "IWLS":
                params["iterations"] = self.iterations.value()
            inputs = dict(dwi=source)
            mask = self.mask.currentData()
            if mask:
                inputs["mask"] = mask
            metric_inputs = dict(tensor=OutputRef(f"dti{number}", "tensor"))
            if mask:
                metric_inputs["mask"] = mask
            self.owner.tasks.extend(
                (
                    Task(f"dti{number}", "dwi2tensor", inputs, params),
                    Task(
                        f"metrics{number}",
                        "tensor2metric",
                        metric_inputs,
                        dict(metrics=["FA", "MD", "AD", "RD", "V1"]),
                    ),
                )
            )
            self.owner.refresh()
            self.owner.status.setText(
                "DTI tasks added. Inspect acquisition suitability and preview exact commands before approval."
            )
        except (ValueError, TypeError) as exc:
            self.owner.status.setText(str(exc))

    def add_existing_tensor_metrics(self):
        """Use the shared tensor2metric contract, without refitting diffusion data."""
        tensor = self.tensor.currentData()
        if tensor is None:
            raise ValueError("Import an existing six-component tensor first")
        artifact = next((a for a in self.owner.artifacts if a.id == tensor), None)
        if artifact is None or not self._metric_tensor_eligible(artifact):
            raise ValueError(
                "This tensor's declared component order, frame or units are incompatible with MRtrix tensor metrics. Review declarations or use a validated conversion."
            )
        if any(
            task.algorithm == "tensor2metric" and task.inputs.get("tensor") == tensor
            for task in self.owner.tasks
        ):
            raise ValueError("Metrics for this tensor are already configured in Workflow review")
        number = 1
        while any(task.id == f"metrics{number}" for task in self.owner.tasks):
            number += 1
        inputs = {"tensor": tensor}
        if self.mask.currentData():
            inputs["mask"] = self.mask.currentData()
        self.owner.tasks.append(
            Task(
                f"metrics{number}",
                "tensor2metric",
                inputs,
                {"metrics": ["FA", "MD", "AD", "RD", "V1"]},
            )
        )
        self.owner.refresh()
        self.owner.status.setText(
            "Tensor metric task added. Confirm component order, frame, units and source history in the exact plan before approval."
        )

    def prepare(self):
        """Compile full input/binary checks off-thread; do not start MRI commands."""
        self.invalidate()
        if not self.output.text().strip():
            self.owner.status.setText("Choose a new output directory first")
            return
        artifacts = tuple(self.owner.artifacts)
        tasks = tuple(t for t in self.owner.tasks if t.algorithm in {"dwi2tensor", "tensor2metric"})
        output, threads, directory = (
            Path(self.output.text()),
            self.threads.value(),
            self.owner.tool_directory,
        )
        self.owner.start(
            "dti-preview",
            lambda cancel, progress: prepare_dti(
                artifacts,
                tasks,
                output,
                nthreads=threads,
                tool_directory=directory,
                cancelled=cancel,
                progress=progress,
            ),
        )

    def run(self):
        """Only this explicit user action authorizes the currently displayed digest."""
        if not self.plan or not self.approval.isChecked():
            return
        if not self.owner.processing_available():
            self.owner.status.setText(
                "Wait for existing preprocessing to finish; no concurrent scientific run was started."
            )
            return
        plan = self.plan
        self.approval.setChecked(False)
        self.manifest = None
        self.maps.clear()
        self.result_label.setText("Running new attempt: " + plan.to_dict()["output_directory"])
        self.owner.start(
            "dti-run",
            lambda cancel, progress: execute_plan(
                None, plan, approval=plan.digest, cancelled=cancel, progress=progress
            ),
        )

    def result(self, kind, result):
        """Never expose model outputs from failed or incompletely verified attempts."""
        if kind == "dti-preview":
            self.plan = result
            import json

            lines = [f"Approval digest: {result.digest}"]
            data = result.to_dict()
            lines += [i["message"] for i in data["issues"] if i["severity"] != "adapter"]
            lines += data["scientific_limits"]
            lines += [c["display"] for c in data["commands"]]
            lines += [
                "Parameters and selected original-volume mapping:",
                json.dumps(data["bindings"], indent=2),
            ]
            self.log.setPlainText("\n\n".join(lines))
            self.owner.status.setText(
                "Commands prepared. Review before approving; scientific acceptance is not assessed."
            )
        else:
            self.manifest = result
            self.result_label.setText(
                "Last attempt: "
                + result["plan"]["output_directory"]
                + " · "
                + result["status"].replace("_", " ")
            )
            primary = next(
                (b for b in result["plan"]["bindings"] if b["algorithm"] == "dwi2tensor"), None
            )
            if primary:
                self.source.setCurrentIndex(self.source.findData(primary["source"]["id"]))
                self.shells.setText(
                    ", ".join(str(s["b_median"]) for s in primary["sampling"]["selected_shells"])
                )
                self.fit.setCurrentText(primary["fit"] if primary["fit"] != "WLS_IWLS" else "IWLS")
                if primary["iterations"]:
                    self.iterations.setValue(primary["iterations"])
                self.mask.setCurrentIndex(
                    self.mask.findData(primary["mask"]["id"] if primary["mask"] else None)
                )
            self.log.appendPlainText(
                "\nStatus: "
                + result["status"]
                + "\n"
                + result.get("error", "Numerical verification passed; scientific review required.")
            )
            if result["status"] == "validated_pending_qc" and result["inputs_unchanged"]:
                for export in result["plan"]["exports"]:
                    name = export["destination"]
                    if name.endswith(("-tensor.nii.gz", "-EIGENVALUES.nii.gz")):
                        continue
                    self.maps.addItem(name, name)
            self.owner.status.setText(
                "DTI "
                + result["status"].replace("_", " ")
                + ". "
                + result.get("error", "Review saved QC and results.")
            )
            self.plan = None
        self.update_actions()

    def update_actions(self, *_):
        """Keep scientific edits disabled while the owned worker is active."""
        idle = self.owner.thread is None
        for control in (
            self.source,
            self.tensor,
            self.task_mode,
            self.shells,
            self.fit,
            self.mask,
            self.output,
            self.threads,
            self.guided,
            self.import_dwi,
            self.import_tensor,
            self.import_mask,
            self.preview,
        ):
            control.setEnabled(idle)
        self.iterations.setEnabled(idle and self.fit.currentText() == "IWLS")
        self.approval.setEnabled(idle and self.plan is not None)
        self.run_button.setEnabled(idle and self.plan is not None and self.approval.isChecked())
        self.open_button.setEnabled(idle and self.maps.count() > 0)
        self.qc_button.setEnabled(
            idle
            and self.manifest is not None
            and (self.maps.count() > 0 or bool(self.manifest.get("failure_evidence")))
        )
        self.history_button.setEnabled(idle and self.manifest is not None)

    def open_result(self):
        """Reverify saved hashes in a worker before handing a typed product to the viewer."""
        if not self.manifest or self.maps.currentData() is None:
            return
        manifest, name = self.manifest, self.maps.currentData()

        def verify(cancel, progress):
            """Open verified scalar images; V1 remains a typed world-RAS vector."""
            folder = Path(manifest["plan"]["output_directory"])
            path = folder / name
            if hash_file(path, cancel) != manifest["outputs"][name]["sha256"]:
                raise ValueError("Result changed; reload run evidence before viewing")
            fa = name.replace("-V1.nii.gz", "-FA.nii.gz")
            if name.endswith("-V1.nii.gz"):
                if (
                    fa not in manifest["outputs"]
                    or hash_file(folder / fa, cancel) != manifest["outputs"][fa]["sha256"]
                ):
                    raise ValueError(
                        "V1 display requires its verified FA map; export unit vectors remains available"
                    )
                return dict(
                    kind="dti_vector",
                    path=str(path),
                    fa=str(folder / fa),
                    sha256=manifest["outputs"][name]["sha256"],
                    fa_sha256=manifest["outputs"][fa]["sha256"],
                )
            if name.endswith(("-tensor.nii.gz", "-EIGENVALUES.nii.gz")):
                raise ValueError(
                    "Multi-component tensors/eigenvalues are scientific exports, not scalar image sequences. Choose FA/MD/AD/RD/V1."
                )
            return dict(kind="scalar", path=str(path), sha256=manifest["outputs"][name]["sha256"])

        self.owner.start("dti-view", verify)

    def open_qc(self):
        """Existing QC UI verifies the catalog; no analyses are rerun."""
        if self.manifest and (self.maps.count() or self.manifest.get("failure_evidence")):
            name = (
                "failure-qc-catalog.json"
                if self.manifest.get("failure_evidence")
                else "qc-catalog.json"
            )
            self.qc_requested.emit(Path(self.manifest["plan"]["output_directory"]) / name)

    def open_run(self):
        """Open completed or failed attempt evidence without rerunning processing."""
        if self.manifest:
            self.run_requested.emit(
                Path(self.manifest["plan"]["output_directory"]) / "manifest.json"
            )
