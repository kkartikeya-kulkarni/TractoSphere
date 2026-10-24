"""CSD controls over the existing inventory, owned worker and scientific engine.

No renderer or solver lives here. Imported FODs open typed metadata; explicitly
labelled C00/compartment exports may be inspected in the existing scalar viewer.
"""

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
from dmri_studio.core.scientific import OutputRef, Task
from dmri_studio.core.scientific.csd import ALGORITHMS, prepare_csd

from .components import Disclosure, button, label


class CSDExecutionPanel(QScrollArea):
    """Explicit responses, shells and optional normalization with digest approval."""

    view_requested = Signal(object)
    qc_requested = Signal(object)
    run_requested = Signal(object)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner, self.plan, self.manifest = owner, None, None
        self.setWidgetResizable(True)
        content = QWidget()
        self.setWidget(content)
        root = QVBoxLayout(content)
        root.addWidget(label("Estimate fiber orientation distributions", "section"))
        root.addWidget(
            label(
                "Select eligible preprocessed DWI, choose a supported single- or multi-tissue method, and supply or explicitly estimate response functions. The planner checks shells and tissue associations; no preprocessing is repeated.",
                "caption",
            )
        )
        config = QWidget()
        setup = QVBoxLayout(config)
        setup.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.source, self.mask, self.method, self.response_mode = (QComboBox() for _ in range(4))
        self.method.addItem("Three-tissue MSMT-CSD", "dwi2fod.msmt_csd")
        self.method.addItem("Single-tissue CSD", "dwi2fod.csd")
        for text, data in [
            ("Import existing responses", "import"),
            ("Estimate · Dhollander", "dhollander"),
            ("Estimate · Tournier (WM only)", "tournier"),
            ("Estimate · Tax (WM only)", "tax"),
        ]:
            self.response_mode.addItem(text, data)
        self.responses = {t: QComboBox() for t in ("wm", "gm", "csf")}
        self.shells = QLineEdit()
        self.shells.setPlaceholderText("Explicit b-values after acquisition review")
        self.lmax = QSpinBox()
        self.lmax.setRange(2, 30)
        self.lmax.setSingleStep(2)
        self.lmax.setValue(8)
        self.normalize = QCheckBox(
            "Add separate multi-tissue normalization (not applied by default)"
        )
        self.output = QLineEdit()
        self.output.setPlaceholderText("New immutable attempt directory")
        self.threads = QSpinBox()
        self.threads.setRange(1, 64)
        self.threads.setValue(4)
        self.acquisition = label("Import DWI to review its shells and gradient frame.", "caption")
        imports = QHBoxLayout()
        for title, kind in (
            ("Import DWI…", "dwi"),
            ("Import mask…", "mask"),
            ("Import response…", "response"),
        ):
            imports.addWidget(
                button(title, lambda _=False, selected=kind: self.owner.browse_import(selected))
            )
        imports.addStretch()
        setup.addLayout(imports)
        for title, field in [
            ("DWI", self.source),
            ("Binary mask", self.mask),
            ("Reconstruction", self.method),
            ("Responses", self.response_mode),
            *[(t.upper() + " response", c) for t, c in self.responses.items()],
            ("Shells (s/mm²)", self.shells),
        ]:
            form.addRow(title, field)
            field.setAccessibleName("CSD " + title)
        out = QHBoxLayout()
        out.addWidget(self.output, 1)
        out.addWidget(button("Choose parent…", self.choose_parent))
        form.addRow("New output directory", out)
        setup.addLayout(form)
        setup.addWidget(self.acquisition)
        self.method_info = label("", "caption")
        setup.addWidget(self.method_info)
        advanced = QWidget()
        advanced_form = QFormLayout(advanced)
        advanced_form.addRow("WM lmax", self.lmax)
        advanced_form.addRow("Threads", self.threads)
        advanced_form.addRow(self.normalize)
        self.advanced = Disclosure("Advanced reconstruction settings", advanced)
        setup.addWidget(self.advanced)
        actions = QHBoxLayout()
        self.guided = button("Configure reconstruction tasks", self.add_guided)
        self.preview = button("Validate and review commands", self.prepare)
        actions.addWidget(self.guided)
        actions.addWidget(self.preview)
        actions.addStretch()
        setup.addLayout(actions)
        setup.addWidget(
            label(
                "Advanced parameters and independent response/normalization tasks are available in Algorithms. Every scientific choice remains visible in the command preview.",
                "caption",
            )
        )
        self.setup = Disclosure("Select inputs and reconstruction", config, expanded=True)
        root.addWidget(self.setup)
        self.approval = QCheckBox(
            "I reviewed the exact commands, responses, shells, provenance and scientific limitations"
        )
        root.addWidget(self.approval)
        self.run_button = button("Run approved CSD", self.run)
        root.addWidget(self.run_button)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        self.log.setMaximumBlockCount(3000)
        self.log.setAccessibleName("CSD commands and live logs")
        root.addWidget(self.log, 1)
        self.result_label = label("No CSD attempt completed in this workspace.", "caption")
        root.addWidget(self.result_label)
        row = QHBoxLayout()
        self.maps = QComboBox()
        self.maps.setAccessibleName("Verified CSD products")
        self.open_button = button("Inspect result", self.open_result)
        self.qc_button = button("Review QC", self.open_qc)
        self.history_button = button("Run details", self.open_run)
        for w in (self.maps, self.open_button, self.qc_button, self.history_button):
            row.addWidget(w)
        root.addLayout(row)
        root.removeWidget(self.result_label)
        root.insertWidget(2, self.result_label)
        root.removeItem(row)
        root.insertLayout(3, row)
        root.removeWidget(self.log)
        root.insertWidget(5, self.log, 1)
        self.approval.toggled.connect(self.update_actions)
        self.source.currentIndexChanged.connect(self.source_info)
        self.response_mode.currentIndexChanged.connect(self.update_actions)
        self.method.currentIndexChanged.connect(self.update_actions)
        self.method.currentIndexChanged.connect(self.explain_method)
        self.response_mode.currentIndexChanged.connect(self.explain_method)
        self.output.textChanged.connect(self.invalidate)
        self.threads.valueChanged.connect(self.invalidate)
        for combo in (
            self.source,
            self.mask,
            self.method,
            self.response_mode,
            *self.responses.values(),
        ):
            combo.currentIndexChanged.connect(self.invalidate)
        self.shells.textChanged.connect(self.invalidate)
        self.lmax.valueChanged.connect(self.invalidate)
        self.normalize.toggled.connect(self.invalidate)
        self.refresh()
        self.explain_method()

    def explain_method(self, *_):
        """Describe real adapter requirements without claiming shell eligibility."""
        multi = self.method.currentData() == "dwi2fod.msmt_csd"
        self.method_info.setText(
            (
                "Three-tissue MSMT-CSD needs compatible shell information and WM, GM and CSF responses. "
                if multi
                else "Single-tissue CSD needs a compatible WM response. "
            )
            + (
                "Response estimation will be a separately validated task. "
                if self.response_mode.currentData() != "import"
                else "Select the corresponding imported response input. "
            )
            + "The exact plan decides eligibility; this choice alone does not."
        )

    def invalidate(self, *_):
        """A changed execution configuration always needs fresh approval."""
        self.plan = None
        self.approval.setChecked(False)
        self.update_actions()

    def refresh(self):
        """Keep scientific files shared with DTI; refresh only cheap UI inventory."""
        self.invalidate()
        for combo, kind, tissue in [
            (self.source, "dwi", None),
            (self.mask, "mask", None),
            *[(c, "response", t) for t, c in self.responses.items()],
        ]:
            selected = combo.currentData()
            combo.clear()
            combo.addItem(
                "Select imported " + (tissue.upper() + " " if tissue else "") + kind + "…", None
            )
            for a in self.owner.artifacts:
                if a.kind == kind and (
                    tissue is None or a.payload["declarations"].get("tissue") == tissue
                ):
                    combo.addItem(Path(a.payload["path"]).name, a.id)
            if selected is not None:
                combo.setCurrentIndex(max(0, combo.findData(selected)))
            if combo.count() == 1:
                combo.setItemText(
                    0,
                    "No imported "
                    + (tissue.upper() + " " if tissue else "")
                    + kind
                    + " — use Import above",
                )
        self.update_actions()

    def source_info(self, *_):
        a = next((a for a in self.owner.artifacts if a.id == self.source.currentData()), None)
        if a:
            g = a.payload["gradients"]
            self.acquisition.setText(
                "Available shells: "
                + ", ".join(f"{s['b_median']:g} ({s['count']} volumes)" for s in g["shells"])
                + f" · {g['convention']}. Select shells explicitly; imported preprocessing remains subject to review."
            )

    def choose_parent(self):
        p = QFileDialog.getExistingDirectory(self, "CSD output parent")
        if p:
            self.output.setText(str(Path(p) / "csd-attempt001"))

    def add_guided(self):
        """Build typed tasks only; never infer missing tissue responses or shells."""
        try:
            source, mask = self.source.currentData(), self.mask.currentData()
            if not source or not mask:
                raise ValueError("Choose imported DWI and a compatible binary mask")
            shells = [float(v.strip()) for v in self.shells.text().split(",")]
            if self.lmax.value() % 2:
                raise ValueError("WM lmax must be even")
            method = self.method.currentData()
            response = self.response_mode.currentData()
            if method.endswith("msmt_csd") and response in ("tournier", "tax"):
                raise ValueError(
                    "Tournier and Tax estimate WM only; select single-tissue CSD or import three compatible tissue responses"
                )
            number = 1
            while any(
                t.id in {f"response{number}", f"csd{number}", f"normalise{number}"}
                for t in self.owner.tasks
            ):
                number += 1
            if method == "dwi2fod.csd" and response == "dhollander":
                raise ValueError(
                    "Estimate Dhollander separately with b0 and diffusion shells in Algorithms, then import its WM response for a single weighted-shell CSD fit; or choose Tournier/Tax."
                )
            rows = []
            inputs = dict(dwi=source, mask=mask)
            tissues = ["wm", "gm", "csf"] if method.endswith("msmt_csd") else ["wm"]
            if response != "import":
                rows.append(
                    Task(
                        f"response{number}",
                        "dwi2response." + response,
                        dict(dwi=source, mask=mask),
                        dict(shells=shells, lmax=self.lmax.value()),
                    )
                )
                inputs.update(
                    {
                        t + "_response": OutputRef(f"response{number}", t + "_response")
                        for t in tissues
                    }
                )
            else:
                for t in tissues:
                    selected = self.responses[t].currentData()
                    if not selected:
                        raise ValueError(
                            "Choose imported "
                            + t.upper()
                            + " response; original values will not be replaced"
                        )
                    inputs[t + "_response"] = selected
            rows.append(
                Task(f"csd{number}", method, inputs, dict(shells=shells, lmax=self.lmax.value()))
            )
            if self.normalize.isChecked():
                if len(tissues) != 3:
                    raise ValueError("Multi-tissue normalization requires WM, GM and CSF")
                rows.append(
                    Task(
                        f"normalise{number}",
                        "mtnormalise",
                        {
                            "wm_fod": OutputRef(f"csd{number}", "wm_fod"),
                            "gm": OutputRef(f"csd{number}", "gm"),
                            "csf": OutputRef(f"csd{number}", "csf"),
                            "mask": mask,
                        },
                        dict(order=3, balanced="off"),
                    )
                )
            self.owner.tasks.extend(rows)
            self.owner.refresh()
            self.owner.status.setText(
                "CSD tasks added. Preview and review exact commands before approval."
            )
        except (ValueError, TypeError) as exc:
            self.owner.status.setText(str(exc))

    def prepare(self):
        self.invalidate()
        if not self.output.text().strip():
            self.owner.status.setText("Choose a new output directory first")
            return
        art = tuple(self.owner.artifacts)
        tasks = tuple(t for t in self.owner.tasks if t.algorithm in ALGORITHMS)
        output, threads, directory = (
            Path(self.output.text()),
            self.threads.value(),
            self.owner.tool_directory,
        )
        self.owner.start(
            "csd-preview",
            lambda cancel, progress: prepare_csd(
                art,
                tasks,
                output,
                nthreads=threads,
                tool_directory=directory,
                cancelled=cancel,
                progress=progress,
            ),
        )

    def run(self):
        if not self.plan or not self.approval.isChecked():
            return
        if not self.owner.processing_available():
            self.owner.status.setText("Wait for existing scientific processing to finish")
            return
        plan = self.plan
        self.approval.setChecked(False)
        self.manifest = None
        self.maps.clear()
        self.result_label.setText("Running · " + plan.to_dict()["output_directory"])
        self.owner.start(
            "csd-run",
            lambda cancel, progress: execute_plan(
                None, plan, approval=plan.digest, cancelled=cancel, progress=progress
            ),
        )

    def result(self, kind, result):
        if kind == "csd-preview":
            self.setup.toggle.setChecked(False)
            self.plan = result
            data = result.to_dict()
            self.log.setPlainText(
                "\n\n".join(
                    [
                        "Approval digest: " + result.digest,
                        *data["scientific_limits"],
                        *data.get("internal_actions", []),
                        *[i["message"] for i in data["issues"]],
                        *[c["display"] for c in data["commands"]],
                    ]
                )
            )
            self.owner.status.setText(
                "Commands prepared. Review before approving; scientific acceptance is not assessed."
            )
        else:
            self.setup.toggle.setChecked(False)
            self.manifest = result
            self.maps.clear()
            self.plan = None
            status = result["status"]
            good = status == "validated_pending_qc" and result.get("inputs_unchanged")
            message = (
                "Processing completed · QC requires review"
                if good
                else status.replace("_", " ").capitalize()
            )
            self.result_label.setText(message + " · " + result["plan"]["output_directory"])
            if not self.log.toPlainText().strip():
                self.log.setPlainText("\n\n".join(c["display"] for c in result["plan"]["commands"]))
            self.log.appendPlainText(result.get("error") or message)
            findings = result.get("validation", {}).get("findings", [])
            if findings:
                self.log.appendPlainText("QC findings:\n" + "\n".join(findings))
            if good:
                folder = Path(result["plan"]["output_directory"])
                # Use the same typed product contract as Runs; no large source
                # rehash on the GUI thread. Bytes are checked on opening below.
                for row in result["plan"]["bindings"]:
                    for role, name in row["outputs"].items():
                        self.maps.addItem(
                            name,
                            dict(
                                kind="fod"
                                if role == "wm_fod"
                                else "response"
                                if role.endswith("_response")
                                else "tissue_scalar",
                                path=str(folder / name),
                                sha256=result["outputs"][name]["sha256"],
                                tissue="wm" if role == "wm_fod" else role.removesuffix("_response"),
                                normalization="completed"
                                if row["algorithm"] == "mtnormalise"
                                else "not_done",
                            ),
                        )
                for e in result["plan"]["exports"]:
                    name = e["destination"]
                    self.maps.addItem(
                        name
                        + (
                            " · C00 coefficient, not glyph"
                            if e["kind"] == "fod_c00"
                            else " · compartment amplitude"
                        ),
                        dict(
                            kind="scalar",
                            path=str(folder / name),
                            sha256=result["outputs"][name]["sha256"],
                        ),
                    )
            self.owner.status.setText(
                message
                + (
                    ". " + result["error"]
                    if result.get("error")
                    else ". Scientific acceptance remains pending."
                )
            )
        self.update_actions()

    def update_actions(self, *_):
        if not hasattr(self, "run_button"):
            return
        idle = self.owner.thread is None
        for w in (
            self.source,
            self.mask,
            self.method,
            self.response_mode,
            self.shells,
            self.lmax,
            self.normalize,
            self.output,
            self.threads,
            self.guided,
            self.preview,
        ):
            w.setEnabled(idle)
        for t, c in self.responses.items():
            c.setEnabled(
                idle
                and self.response_mode.currentData() == "import"
                and (t == "wm" or self.method.currentData() == "dwi2fod.msmt_csd")
            )
        self.approval.setEnabled(idle and self.plan is not None)
        self.run_button.setEnabled(idle and self.plan is not None and self.approval.isChecked())
        self.open_button.setEnabled(idle and self.maps.count() > 0)
        self.qc_button.setEnabled(idle and self.manifest is not None)
        self.history_button.setEnabled(idle and self.manifest is not None)

    def open_result(self):
        product = self.maps.currentData()
        if not product:
            return

        def verify(cancel, progress):
            if hash_file(Path(product["path"]), cancel) != product["sha256"]:
                raise ValueError("Result changed; reload saved run evidence")
            return product

        self.owner.start("csd-view", verify)

    def open_qc(self):
        if self.manifest:
            folder = Path(self.manifest["plan"]["output_directory"])
            name = (
                "failure-qc-catalog.json"
                if self.manifest.get("failure_evidence")
                else "qc-catalog.json"
            )
            self.qc_requested.emit(folder / name)

    def open_run(self):
        if self.manifest:
            self.run_requested.emit(
                Path(self.manifest["plan"]["output_directory"]) / "manifest.json"
            )
