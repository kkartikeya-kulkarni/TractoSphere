"""Compact 5TT preparation in the existing scientific workspace/worker lifecycle."""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
from dmri_studio.core.scientific import Artifact, Task
from dmri_studio.core.scientific.anatomy import prepare_anatomy

from .components import Disclosure, button, label


class AnatomyPanel(QScrollArea):
    """Generate/import-check tissue derivatives separately from ACT tracking."""

    view_requested = Signal(dict)
    qc_requested = Signal(object)
    run_requested = Signal(object)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner, self.plan, self.manifest = owner, None, None
        self.setWidgetResizable(True)
        content = QWidget()
        self.setWidget(content)
        root = QVBoxLayout(content)
        root.addWidget(
            label(
                "Five-tissue anatomy · verify registration and segmentation before ACT. Scientific acceptance remains separate.",
                "caption",
            )
        )
        form = QFormLayout()
        self.operation = QComboBox()
        for title, key in (
            ("Generate 5TT with FSL", "5ttgen.fsl"),
            ("Validate imported 5TT", "5ttcheck"),
            ("Inspect tissue compartments", "5tt2vis"),
            ("Generate GM–WM interface seeds", "5tt2gmwmi"),
        ):
            self.operation.addItem(title, key)
        self.source = QComboBox()
        self.reference = QComboBox()
        self.mask = QComboBox()
        self.extraction = QComboBox()
        for title, key in (
            ("Use selected brain mask", "mask"),
            ("Already brain extracted", "premasked"),
            ("Native automatic extraction", "automatic"),
        ):
            self.extraction.addItem(title, key)
        self.amygdala = QCheckBox("Represent amygdalae/hippocampi as subcortical GM")
        self.threads = QSpinBox()
        self.threads.setRange(1, 8)
        self.threads.setValue(2)
        self.output = QLineEdit()
        self.output.setPlaceholderText("New immutable derivative directory")
        out = QHBoxLayout()
        out.addWidget(self.output)
        out.addWidget(button("Choose parent…", self.choose_parent))
        for title, w in (
            ("Operation", self.operation),
            ("Anatomy / five-tissue input", self.source),
            ("Reviewed diffusion reference", self.reference),
            ("Brain extraction", self.extraction),
            ("Brain mask", self.mask),
            ("Threads", self.threads),
        ):
            form.addRow(title, w)
            w.setAccessibleName(title)
        form.addRow(self.amygdala)
        form.addRow("Output directory", out)
        self.form = form
        root.addLayout(form)
        root.addWidget(
            label(
                "Import existing 5TT as type 5tt with the exact channel order. Anatomy/5TT must include a hash-bound anatomical_review JSON identifying the source, diffusion reference, registration evidence and axial/sagittal/coronal review. Matching filenames or grids are insufficient. The current adapter requires an already aligned common grid; it never applies registration.",
                "caption",
            )
        )
        self.preview = button("Preview commands", self.prepare)
        root.addWidget(self.preview)
        self.approval = QCheckBox(
            "I reviewed the anatomy, tissue order, alignment evidence, commands and limitations"
        )
        root.addWidget(self.approval)
        self.run_button = button("Run approved tissue operation", self.run)
        root.addWidget(self.run_button)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setMaximumHeight(200)
        self.log.setAccessibleName("Anatomy command preview and live log")
        root.addWidget(Disclosure("Commands and processing logs", self.log, expanded=True))
        self.summary = label("No tissue operation completed.", "caption")
        root.addWidget(self.summary)
        tools = QHBoxLayout()
        self.maps = QComboBox()
        self.open_button = button("Open compartment", self.open_result)
        self.qc_button = button("Review QC", self.open_qc)
        tools.addWidget(self.maps, 1)
        tools.addWidget(self.open_button)
        tools.addWidget(self.qc_button)
        root.addLayout(tools)
        root.addStretch()
        self.operation.currentIndexChanged.connect(self.refresh)
        for w in (self.source, self.reference, self.mask, self.extraction):
            w.currentIndexChanged.connect(self.invalidate)
        self.amygdala.toggled.connect(self.invalidate)
        self.output.textChanged.connect(self.invalidate)
        self.threads.valueChanged.connect(self.invalidate)
        self.approval.toggled.connect(self.update_actions)
        self.refresh()

    def invalidate(self, *_):
        """Changed scientific choices invalidate exact-plan approval."""
        self.plan = None
        self.approval.setChecked(False)
        self.update_actions()

    def refresh(self, *_):
        """Only compatible typed inventory entries are offered."""
        self.invalidate()
        generate = self.operation.currentData() == "5ttgen.fsl"
        for combo, kinds in (
            (self.source, {"anatomy"} if generate else {"5tt"}),
            (self.reference, {"anatomy"}),
            (self.mask, {"mask"}),
        ):
            old = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Choose…", None)
            for a in self.owner.artifacts:
                if a.kind in kinds:
                    combo.addItem(Path(a.payload["path"]).name, a.id)
            combo.setCurrentIndex(max(0, combo.findData(old)))
            combo.blockSignals(False)
        for w in (self.extraction, self.mask, self.amygdala):
            self.form.setRowVisible(w, generate)
        self.update_actions()

    def choose_parent(self):
        """Choose only a parent; exclusive attempt creation occurs in the engine."""
        p = QFileDialog.getExistingDirectory(self, "Anatomical derivative parent")
        if p:
            self.output.setText(str(Path(p) / "anatomy-attempt001"))

    def selected_task(self):
        """Return the same typed task used by CLI and Python."""
        method = self.operation.currentData()
        generate = method == "5ttgen.fsl"
        inputs = {
            "anatomy" if generate else "five_tt": self.source.currentData(),
            "reference": self.reference.currentData(),
        }
        p = {}
        if generate:
            p = dict(
                brain_extraction=self.extraction.currentData(),
                amygdala_hippocampus_sgm="on" if self.amygdala.isChecked() else "off",
            )
            if self.extraction.currentData() == "mask":
                inputs["mask"] = self.mask.currentData()
        if any(v is None for v in inputs.values()):
            raise ValueError("Select all required anatomical inputs")
        return Task("anatomy", method, inputs, p)

    def prepare(self):
        """Background compilation performs real validation and dependency checks."""
        self.invalidate()
        try:
            task = self.selected_task()
            if not self.output.text().strip():
                raise ValueError("Choose a new anatomical derivative directory")
        except ValueError as e:
            self.owner.status.setText(str(e))
            return
        self.owner.tasks = [t for t in self.owner.tasks if t.id != task.id] + [task]
        artifacts = tuple(self.owner.artifacts)
        output = Path(self.output.text())
        threads = self.threads.value()
        self.owner.start(
            "anatomy-preview",
            lambda cancel, progress: prepare_anatomy(
                artifacts,
                (task,),
                output,
                nthreads=threads,
                tool_directory=self.owner.tool_directory,
                fsl_directory=self.owner.fsl_directory,
                cancelled=cancel,
                progress=progress,
            ),
        )

    def run(self):
        """Execution requires exact preview and explicit application approval."""
        if not self.plan or not self.approval.isChecked() or not self.owner.processing_available():
            return
        plan = self.plan
        self.approval.setChecked(False)
        self.maps.clear()
        self.manifest = None
        self.summary.setText("Processing anatomy…")
        self.owner.start(
            "anatomy-run",
            lambda cancel, progress: execute_plan(
                None, plan, approval=plan.digest, cancelled=cancel, progress=progress
            ),
        )

    def result(self, kind, result):
        """Only verified derivatives become reusable or visible products."""
        if kind == "anatomy-preview":
            self.plan = result
            self.log.setPlainText(
                "Approval digest: "
                + result.digest
                + "\n\n"
                + "\n".join(result.payload["scientific_limits"])
                + "\n\n"
                + "\n\n".join(c["display"] for c in result.payload["commands"])
            )
            self.owner.status.setText(
                "Anatomical plan prepared. Review evidence and commands before approval."
            )
        elif kind == "anatomy-run":
            self.plan = None
            self.manifest = result
            self.maps.clear()
            good = result["status"] == "validated_pending_qc" and result.get("inputs_unchanged")
            text = (
                "Processing completed · anatomical QC requires review"
                if good
                else result["status"].capitalize()
            )
            self.summary.setText(text)
            self.owner.status.setText(
                text + (" · " + result["error"] if result.get("error") else "")
            )
            if good:
                for p in result["validation"]["products"]:
                    a = Artifact(p["artifact"])
                    if all(old.id != a.id for old in self.owner.artifacts):
                        self.owner.artifacts.append(a)
                    if a.kind in {"tissue_scalar", "gmwmi"}:
                        path = Path(a.payload["path"])
                        self.maps.addItem(
                            p["role"],
                            dict(
                                path=str(path),
                                kind="scalar",
                                sha256=result["outputs"][path.name]["sha256"],
                            ),
                        )
                self.owner.refresh()
        self.update_actions()

    def failed(self, message, cancelled=False):
        """Clear active loading/approval while preserving historical logs."""
        self.plan = None
        self.approval.setChecked(False)
        self.summary.setText(
            "Cancelled · no verified product" if cancelled else "Operation failed · " + message
        )

    def open_result(self):
        """Hash verification runs on the owned worker before viewer handoff."""
        p = self.maps.currentData()
        if not p:
            return

        def verify(cancel, progress):
            if hash_file(Path(p["path"]), cancel) != p["sha256"]:
                raise ValueError("Anatomical product changed")
            return p

        self.owner.start("anatomy-view", verify)

    def open_qc(self):
        """Use the existing central QC workspace."""
        if self.manifest:
            self.qc_requested.emit(
                Path(self.manifest["plan"]["output_directory"]) / "qc-catalog.json"
            )

    def update_actions(self, *_):
        """Busy state is scoped to the existing scientific worker."""
        idle = self.owner.thread is None
        for w in (
            self.operation,
            self.source,
            self.reference,
            self.mask,
            self.extraction,
            self.output,
            self.threads,
            self.amygdala,
            self.preview,
        ):
            w.setEnabled(idle)
        self.approval.setEnabled(idle and self.plan is not None)
        self.run_button.setEnabled(idle and self.plan is not None and self.approval.isChecked())
        self.open_button.setEnabled(idle and self.maps.count() > 0)
        self.qc_button.setEnabled(
            idle
            and self.manifest is not None
            and self.manifest.get("status") == "validated_pending_qc"
        )
