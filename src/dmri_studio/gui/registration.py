"""Registration controls in the existing scientific worker and native viewer."""

import json
import time
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.execution import execute_plan, hash_file
from dmri_studio.core.scientific import Artifact, ImportRequest, import_artifact
from dmri_studio.core.scientific.registration import prepare_registration, space
from dmri_studio.core.scientific.registry import ALGORITHMS
from dmri_studio.core.spatial import FORMATS, save_transform, transform_record

from .components import Disclosure, button, label


class TransformImportDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("Import existing transform — explicit direction")
        root = QVBoxLayout(self)
        form = QFormLayout()
        root.addLayout(form)
        self.source = QComboBox()
        self.target = QComboBox()
        missing = []
        for combo in (self.source, self.target):
            combo.addItem("Select image and declared space…", None)
            for a in owner.artifacts:
                if a.kind == "anatomy":
                    reason = self._image_blocker(a)
                    name = Path(a.payload["path"]).name
                    combo.addItem(
                        name if not reason else f"Imported · {name} · needs review",
                        a.id if not reason else None,
                    )
                    if reason:
                        combo.model().item(combo.count() - 1).setEnabled(False)
                        missing.append(f"{name}: {reason}")
        self.format = QComboBox()
        for key, meaning in FORMATS.items():
            self.format.addItem(key + " · " + meaning, key)
        self.fields = {}
        for key, title in [
            ("file", "Original transform file"),
            ("software", "Original software"),
            ("version", "Original version (or documented unknown)"),
            ("evidence", "Direction and image-association evidence"),
            ("output", "New transform record JSON"),
        ]:
            field = QLineEdit()
            self.fields[key] = field
            form.addRow(title, field)
            if key in {"file", "output"}:
                form.addRow(button("Browse " + key, lambda _, k=key: self.browse(k)))
        form.addRow("Moving/source image", self.source)
        form.addRow("Fixed/target image", self.target)
        form.addRow("Native coordinate format", self.format)
        root.addWidget(
            label(
                "Choose source and target images with established subject, acquisition and spatial identity. "
                "If an imported image needs review, close this tool and use Registration → "
                "Imported registration inputs → Review input evidence; then reopen transform import.",
                "caption",
            )
        )
        if missing:
            details = QPlainTextEdit()
            details.setReadOnly(True)
            details.setMaximumHeight(90)
            details.setAccessibleName("Imported images needing transform-space evidence")
            details.setPlainText(
                "Imported images needing evidence:\n• " + "\n• ".join(dict.fromkeys(missing))
            )
            root.addWidget(details)
        root.addWidget(
            label(
                "Import records provenance; it does not assert anatomical accuracy. No matrix is applied or affine rewritten.",
                "caption",
            )
        )
        self.error = label("", "caption")
        root.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _image_blocker(artifact):
        d = artifact.payload.get("declarations", {})
        missing = [
            title
            for key, title in (
                ("subject_id", "subject identity"),
                ("acquisition_id", "acquisition identity"),
                ("space_kind", "coordinate-space kind"),
                ("space_id", "specific spatial identity"),
            )
            if not d.get(key)
        ]
        geometry = artifact.payload.get("geometry") or {}
        if len(geometry.get("shape", [])) != 3:
            missing.append("3D reference image")
        if geometry.get("units") != "mm":
            missing.append("millimeter spatial units")
        return ", ".join(missing)

    def browse(self, key):
        path, _ = (
            QFileDialog.getSaveFileName(self, "New immutable transform record", "", "JSON (*.json)")
            if key == "output"
            else QFileDialog.getOpenFileName(self, "Original transform")
        )
        if path:
            self.fields[key].setText(path)

    def selection(self):
        artifacts = {a.id: a.to_dict() for a in self.owner.artifacts}
        if not self.source.currentData() or not self.target.currentData():
            raise ValueError("Select exact source and target images")
        return dict(
            path=Path(self.fields["file"].text()),
            source=space(artifacts[self.source.currentData()]),
            target=space(artifacts[self.target.currentData()]),
            fmt=self.format.currentData(),
            software=self.fields["software"].text(),
            version=self.fields["version"].text(),
            evidence=self.fields["evidence"].text(),
        ), Path(self.fields["output"].text())


class RegistrationPanel(QScrollArea):
    view_requested = Signal(dict)
    qc_requested = Signal(object)
    history_requested = Signal()
    activate_dwi_requested = Signal(object)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.plan = None
        self.task = None
        self._guided_task = None
        self.manifest = None
        self.setWidgetResizable(True)
        content = QWidget()
        self.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(12, 16, 12, 12)
        root.setSpacing(12)
        advanced_content = QWidget()
        advanced = QVBoxLayout(advanced_content)
        advanced.addWidget(
            label(
                "Advanced Registration Tools",
                "section",
            )
        )
        advanced.addWidget(
            label(
                "Exact typed tasks, nonlinear registration, transform inversion/composition/conversion and import remain available here. Unsupported spatial conventions still block.",
                "caption",
            )
        )
        self.method = QComboBox()
        for key, spec in ALGORITHMS.items():
            if spec.stage == "registration":
                self.method.addItem(spec.name, key)
        advanced.addWidget(self.method)
        row = QHBoxLayout()
        self.configure = button("Select inputs and parameters…", self.configure_task)
        row.addWidget(self.configure)
        self.import_transform = button("Import existing transform…", self.import_existing)
        row.addWidget(self.import_transform)
        advanced.addLayout(row)
        self.selection = QPlainTextEdit()
        self.selection.setReadOnly(True)
        self.selection.setMaximumHeight(160)
        advanced.addWidget(self.selection)
        form = QFormLayout()
        self.output = QLineEdit()
        self.output.setPlaceholderText("New immutable output directory")
        self.threads = QSpinBox()
        self.threads.setRange(1, 4)
        self.threads.setValue(2)
        form.addRow("Output", self.output)
        form.addRow("Threads", self.threads)
        advanced.addLayout(form)
        advanced.addWidget(button("Choose output parent…", self.choose_parent))
        self.preview = button("Validate inputs and preview commands", self.prepare)
        advanced.addWidget(self.preview)
        self.approval = QCheckBox(
            "I reviewed the exact inputs, transform direction, interpolation, resources and commands"
        )
        advanced.addWidget(self.approval)
        self.execute = button("Run approved operation", self.run)
        advanced.addWidget(self.execute)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setMinimumHeight(160)
        advanced.addWidget(self.log)
        self.summary = label("Scientific acceptance remains separate from processing.", "caption")
        advanced.addWidget(self.summary)
        row = QHBoxLayout()
        self.open = button("Inspect alignment / atlas QC", self.open_result)
        row.addWidget(self.open)
        row.addWidget(button("Open saved atlas QC…", self.open_saved))
        self.qc = button("Technical QC", self.open_qc)
        row.addWidget(self.qc)
        advanced.addLayout(row)
        advanced.addStretch()
        self.method.currentIndexChanged.connect(self.method_changed)
        self.output.textChanged.connect(self.invalidate)
        self.threads.valueChanged.connect(self.invalidate)
        self.approval.toggled.connect(self.update_actions)
        from .registration_guided import GuidedRegistration

        self.guided = GuidedRegistration(self)
        root.addWidget(self.guided)
        root.addWidget(Disclosure("Advanced Registration Tools", advanced_content))
        root.addStretch()
        self.update_actions()

    def method_changed(self, *_):
        self.task = None
        self.selection.clear()
        if hasattr(self, "guided"):
            self.guided.advanced_selected()
        else:
            self.invalidate()

    def invalidate(self, *_):
        self.plan = None
        self.approval.setChecked(False)
        if hasattr(self, "guided"):
            self.guided.clear_plan()
        self.update_actions()

    def refresh(self):
        if hasattr(self, "guided"):
            self.guided.refresh_inventory()
        self.invalidate()

    def choose_parent(self):
        path = QFileDialog.getExistingDirectory(self, "Registration derivative parent")
        if path:
            self.output.setText(
                str(Path(path) / ("registration-" + time.strftime("%Y%m%d-%H%M%S")))
            )

    def configure_task(self):
        from .scientific_planning import TaskDialog

        dialog = TaskDialog(ALGORITHMS[self.method.currentData()], self.owner.artifacts, [], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.task = dialog.task()
            self.selection.setPlainText(json.dumps(self.task.to_dict(), indent=2))
            self.guided.advanced_selected()
            self.invalidate()

    def import_existing(self):
        dialog = TransformImportDialog(self.owner)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            options, path = dialog.selection()
        except (ValueError, OSError) as e:
            self.summary.setText(str(e))
            return

        def work(cancel, progress):
            record = transform_record(**options)
            save_transform(path, record)
            subject = (
                record["target"]["subject"]
                if record["source"]["kind"] == "MNI_TEMPLATE"
                else record["source"]["subject"]
            )
            return import_artifact(
                ImportRequest(
                    "spatial_transform",
                    path,
                    declarations=dict(
                        subject_id=subject, acquisition_id=record["target"]["acquisition"]
                    ),
                ),
                cancelled=cancel,
                progress=progress,
            )

        self.owner.start("registration-import", work)

    def prepare(self):
        self.invalidate()
        # Existing automation and specialist callers may assign a typed task
        # directly. A new guided task is built only for an untouched draft or
        # for a task previously created by this form.
        if self.guided.active and (self.task is None or self.task is self._guided_task):
            try:
                self.task = self.guided.build_task()
                self._guided_task = self.task
            except ValueError as exc:
                self.summary.setText(str(exc))
                self.guided.summary.setText(str(exc))
                return
            self.selection.setPlainText(json.dumps(self.task.to_dict(), indent=2))
        if not self.task or not self.output.text().strip():
            self.summary.setText("Select inputs/parameters and a new output directory.")
            self.guided.summary.setText(self.summary.text())
            return
        task = self.task
        artifacts = tuple(self.owner.artifacts)
        output = Path(self.output.text())
        threads = self.threads.value()
        directory = self.owner.tool_directory
        fsl_directory = self.owner.fsl_directory
        self.owner.tasks = [t for t in self.owner.tasks if t.id != task.id] + [task]
        self.owner.start(
            "registration-preview",
            lambda cancel, progress: prepare_registration(
                artifacts,
                (task,),
                output,
                nthreads=threads,
                tool_directory=directory,
                fsl_directory=fsl_directory,
                cancelled=cancel,
                progress=progress,
            ),
        )

    def run(self):
        if not self.plan or not self.approval.isChecked() or not self.owner.processing_available():
            return
        plan = self.plan
        self.approval.setChecked(False)
        self.manifest = None
        self.owner.start(
            "registration-run",
            lambda cancel, progress: execute_plan(
                None, plan, approval=plan.digest, cancelled=cancel, progress=progress
            ),
        )

    def result(self, kind, result):
        if kind == "registration-import":
            self.owner.artifacts.append(result)
            self.owner.refresh()
            self.summary.setText("Transform record imported; anatomical review required.")
            return
        if kind == "registration-preview":
            self.plan = result
            self.guided.plan_ready(result)
            self.log.setPlainText(
                "Approval digest: "
                + result.digest
                + "\n\n"
                + "\n".join(c["display"] for c in result.payload["commands"])
                + "\n\n"
                + "\n".join(result.payload["scientific_limits"])
            )
            self.summary.setText(
                "Inputs verified for the selected operation. Review before execution."
            )
            return
        if kind == "registration-view":
            self.owner.product_handoff = result
            return
        self.manifest = result
        self.plan = None
        good = result["status"] == "validated_pending_qc" and result.get("inputs_unchanged")
        self.summary.setText(
            "Processing completed · QC review required"
            if good
            else result["status"] + " · " + result.get("error", "")
        )
        if good:
            for row in result["validation"]["products"]:
                if "artifact" in row:
                    a = Artifact(row["artifact"])
                    if all(old.id != a.id for old in self.owner.artifacts):
                        self.owner.artifacts.append(a)
            self.owner.refresh()
        # Refresh must first invalidate the old runnable plan. Then present the
        # completed, read-only plan from its execution record in the guided UI.
        self.guided.result_ready(result, verified=good)

    def failed(self, message, cancelled=False):
        self.invalidate()
        self.summary.setText("Cancelled; partial evidence retained" if cancelled else message)
        self.guided.summary.setText(self.summary.text())

    def open_result(self):
        if (
            not self.manifest
            or self.manifest["status"] != "validated_pending_qc"
            or not self.manifest.get("inputs_unchanged")
        ):
            return
        p = self.manifest["plan"]
        folder = Path(p["output_directory"])
        if p.get("operation") == "atlas_qc":
            product = dict(
                kind="registration_qc",
                path=str(folder / "atlas-qc.json"),
                sha256=self.manifest["outputs"]["atlas-qc.json"]["sha256"],
            )
        elif "registered.nii.gz" in self.manifest["outputs"]:
            product = dict(
                kind="registration_alignment",
                categorical=p.get("bound", {}).get("moving", {}).get("kind")
                in {"atlas_labels", "roi"},
                reference=p["reference"]["image"],
                reference_sha256=p["reference"]["sha256"],
                path=str(folder / "registered.nii.gz"),
                sha256=self.manifest["outputs"]["registered.nii.gz"]["sha256"],
            )
        elif "mean-b0.nii.gz" in self.manifest["outputs"]:
            product = dict(
                kind="scalar",
                path=str(folder / "mean-b0.nii.gz"),
                sha256=self.manifest["outputs"]["mean-b0.nii.gz"]["sha256"],
            )
        else:
            self.summary.setText(
                "Use this verified transform/reference in the next registration or atlas QC operation."
            )
            return
        self.verify_view(product)

    def verify_view(self, product):
        def verify(cancel, progress):
            if hash_file(Path(product["path"]), cancel) != product["sha256"]:
                raise ValueError("Result changed")
            if product["kind"] == "registration_qc":
                from dmri_studio.core.registration_qc import verify_bundle

                verify_bundle(product["path"], cancel)
            return product

        self.owner.start("registration-view", verify)

    def open_saved(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open registered-atlas QC bundle", "", "JSON (*.json)"
        )
        if path:
            self.verify_view(dict(kind="registration_qc", path=path, sha256=hash_file(Path(path))))

    def open_qc(self):
        if self.manifest:
            self.qc_requested.emit(
                Path(self.manifest["plan"]["output_directory"]) / "qc-catalog.json"
            )

    def update_actions(self, *_):
        idle = self.owner.thread is None
        for w in (
            self.method,
            self.configure,
            self.import_transform,
            self.output,
            self.threads,
            self.preview,
        ):
            w.setEnabled(idle)
        self.approval.setEnabled(idle and self.plan is not None)
        self.execute.setEnabled(idle and self.plan is not None and self.approval.isChecked())
        verified_result = (
            self.manifest is not None
            and self.manifest.get("status") == "validated_pending_qc"
            and self.manifest.get("inputs_unchanged") is True
        )
        self.open.setEnabled(idle and verified_result)
        self.qc.setEnabled(idle and verified_result)
        if hasattr(self, "guided"):
            self.guided.update_actions()
