"""SIFT2 workflow and weight inspection in the existing scientific workstation."""

from pathlib import Path

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QEvent, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
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
from dmri_studio.core.scientific import Artifact, ImportRequest, Task, import_artifact
from dmri_studio.core.scientific.records import read_json
from dmri_studio.core.scientific.sift2 import prepare_sift2, read_weights, verify_weight_binding

from .components import Disclosure, button, label


class SIFT2Panel(QScrollArea):
    """One owned worker/approval lifecycle; optimizer completion never means acceptance."""

    view_requested = Signal(dict)
    qc_requested = Signal(object)
    run_requested = Signal(object)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner, self.plan, self.manifest, self.record = owner, None, None, None
        self.values = None
        self.setWidgetResizable(True)
        content = QWidget()
        self.setWidget(content)
        root = QVBoxLayout(content)
        from .selected_task import SelectedTask

        self.task_source = SelectedTask(self, lambda a: a == "tcksift2")
        root.addWidget(self.task_source)
        root.addWidget(
            label(
                "Calculate SIFT2 weights for existing streamlines using a compatible WM FOD. Weights are model-derived factors, not literal axon counts. Geometry stays unchanged and scientific review remains separate.",
                "caption",
            )
        )
        form = QFormLayout()
        self.form = form
        root.addLayout(form)
        self.tracts = QComboBox()
        self.fod = QComboBox()
        self.reference = QComboBox()
        self.five_tt = QComboBox()
        self.proc_mask = QComboBox()
        self.policy = QComboBox()
        for name, value in [
            ("ACT-derived processing mask", "act"),
            ("Explicit processing mask", "mask"),
            ("Native whole-FOD model", "none"),
        ]:
            self.policy.addItem(name, value)
        for text, w in [
            ("Source tractogram", self.tracts),
            ("Compatible WM FOD", self.fod),
            ("Reviewed reference", self.reference),
            ("Processing mask policy", self.policy),
            ("ACT five-tissue image", self.five_tt),
            ("Processing mask", self.proc_mask),
        ]:
            form.addRow(text, w)
            w.setAccessibleName(text)
        self.threads = QSpinBox()
        self.threads.setRange(1, 8)
        self.threads.setValue(2)
        self.output = QLineEdit()
        self.output.setPlaceholderText("New immutable SIFT2 derivative directory")
        out = QHBoxLayout()
        out.addWidget(self.output)
        out.addWidget(button("Choose parent…", self.choose_parent))
        form.addRow("Output", out)
        self.input_help = label(
            "Select a source tractogram, WM FOD and reviewed reference.", "caption"
        )
        root.addWidget(self.input_help)
        advanced = QWidget()
        af = QFormLayout(advanced)
        af.addRow("Threads", self.threads)
        self.settings = {}
        for key, title in [
            ("reg_tikhonov", "Tikhonov coefficient"),
            ("reg_tv", "Total variation coefficient"),
            ("min_iters", "Minimum iterations"),
            ("max_iters", "Maximum iterations"),
            ("min_td_frac", "Minimum tract-density fraction"),
            ("fd_thresh", "Fibre-density threshold"),
            ("min_coeff", "Minimum optimizer coefficient"),
            ("max_coeff", "Maximum optimizer coefficient"),
            ("max_coeff_step", "Maximum optimizer coefficient step"),
            ("min_factor", "Minimum weight factor"),
            ("max_factor", "Maximum weight factor"),
            ("min_cf_decrease", "Minimum relative cost decrease"),
        ]:
            edit = QLineEdit()
            edit.setPlaceholderText("Native default (unchanged)")
            edit.setAccessibleName(title)
            self.settings[key] = edit
            af.addRow(title, edit)
            edit.textChanged.connect(self.invalidate)
        self.gm_scale = QCheckBox(
            "Heuristic GM scaling · requires established single-tissue CSD and ACT"
        )
        af.addRow(self.gm_scale)
        self.gm_scale.toggled.connect(self.invalidate)
        root.addWidget(Disclosure("Advanced optimization controls", advanced))
        self.preview = button("Preview commands", self.prepare)
        root.addWidget(self.preview)
        self.approval = QCheckBox(
            "I reviewed exact inputs, mask policy, commands, resource limits and scientific limitations"
        )
        root.addWidget(self.approval)
        self.run_button = button("Run approved SIFT2", self.run)
        root.addWidget(self.run_button)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setMaximumHeight(180)
        self.log_disclosure = Disclosure("Commands and processing log", self.log)
        root.addWidget(self.log_disclosure)
        self.summary = label("No SIFT2 result open.", "caption")
        root.addWidget(self.summary)
        actions = QHBoxLayout()
        self.reopen = button("Open weight result…", self.choose_record)
        actions.addWidget(self.reopen)
        self.qc = button("Scientific QC", self.open_qc)
        actions.addWidget(self.qc)
        self.open_tracks = button("Inspect weighted streamlines", self.open_result)
        actions.addWidget(self.open_tracks)
        root.addLayout(actions)
        self.figure = Figure(figsize=(6, 2.3))
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(220)
        self.canvas.setAccessibleName("SIFT2 weight factor histogram, linear factor scale")
        self.canvas.hide()
        root.addWidget(self.canvas)
        selection = QHBoxLayout()
        self.index = QSpinBox()
        self.index.setRange(0, 0)
        self.index.setAccessibleName("Original source streamline index, zero based")
        self.weight_label = label("Select a source index after opening valid weights.", "caption")
        selection.addWidget(label("Source index (0 based)"))
        selection.addWidget(self.index)
        selection.addWidget(self.weight_label, 1)
        root.addLayout(selection)
        for w in (self.tracts, self.fod, self.reference, self.five_tt, self.proc_mask, self.policy):
            w.currentIndexChanged.connect(self.invalidate)
            w.currentIndexChanged.connect(self.update_policy_fields)
        self.output.textChanged.connect(self.invalidate)
        self.threads.valueChanged.connect(self.invalidate)
        self.approval.toggled.connect(self.update_actions)
        self.policy.currentIndexChanged.connect(self.update_policy_fields)
        self.index.valueChanged.connect(self.inspect_index)
        self.refresh()
        self.update_policy_fields()

    def update_policy_fields(self, *_):
        """Only request the tissue or mask evidence used by the selected policy."""
        policy = self.policy.currentData()
        self.form.setRowVisible(self.five_tt, policy == "act")
        self.form.setRowVisible(self.proc_mask, policy == "mask")
        missing = [
            name
            for name, control in (
                ("tractogram", self.tracts),
                ("WM FOD", self.fod),
                ("reviewed reference", self.reference),
                ("five-tissue image", self.five_tt if policy == "act" else None),
                ("processing mask", self.proc_mask if policy == "mask" else None),
            )
            if control is not None and control.currentData() is None
        ]
        self.input_help.setText(
            "Select "
            + ", ".join(missing)
            + ". Exact spatial and provenance checks follow during plan validation."
            if missing
            else "Inputs selected. Validate exact tractogram/FOD association, mask policy and commands before approval."
        )

    def invalidate(self, *_):
        """Configuration changes revoke approval and stale result association."""
        self.plan = None
        self.approval.setChecked(False)
        if self.sender() in (self.tracts, self.fod):
            self.record = None
            self.values = None
            self.figure.clear()
            self.canvas.hide()
            self.canvas.draw_idle()
            self.summary.setText("Input changed. Open or generate weights for this exact source.")
            self.weight_label.setText("No weight result associated with the selected input.")
        self.update_actions()

    def refresh(self, *_):
        self.task_source.refresh_tasks()
        """Keep valid inventory selection without copying images or weight vectors."""
        for combo, kind in (
            (self.tracts, "tractogram"),
            (self.fod, "fod"),
            (self.reference, "anatomy"),
            (self.five_tt, "5tt"),
            (self.proc_mask, "mask"),
        ):
            old = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Choose…", None)
            for a in self.owner.artifacts:
                if a.kind == kind:
                    combo.addItem(Path(a.payload["path"]).name + " · " + a.id[:8], a.id)
            combo.setCurrentIndex(max(0, combo.findData(old)))
            combo.blockSignals(False)
        self.invalidate()
        self.update_policy_fields()

    def selected_task(self):
        """Use one exact typed task for editing, preview, execution and persistence."""
        selected = self.task_source.task()
        if selected is not None:
            return selected
        data = dict(
            tracts=self.tracts.currentData(),
            fod=self.fod.currentData(),
            reference=self.reference.currentData(),
        )
        policy = self.policy.currentData()
        if policy == "act":
            data["five_tt"] = self.five_tt.currentData()
        elif policy == "mask":
            data["proc_mask"] = self.proc_mask.currentData()
        if any(v is None for v in data.values()):
            raise ValueError("Select all required compatible SIFT2 inputs")
        p = dict(
            act="on" if policy == "act" else "off",
            fd_scale_gm="on" if self.gm_scale.isChecked() else "off",
        )
        for key, edit in self.settings.items():
            if edit.text().strip():
                p[key] = int(edit.text()) if key.endswith("iters") else float(edit.text())
        return Task("sift2", "tcksift2", data, p)

    def choose_parent(self):
        """Exclusive attempt creation belongs to the shared executor."""
        p = QFileDialog.getExistingDirectory(self, "SIFT2 derivative parent")
        if p:
            self.output.setText(str(Path(p) / "sift2-attempt001"))

    def prepare(self):
        """Background compiler validates exact geometry/provenance before approval."""
        self.invalidate()
        try:
            task = self.selected_task()
            if not self.output.text().strip():
                raise ValueError("Choose a new derivative directory")
        except (ValueError, TypeError) as e:
            self.owner.status.setText(str(e))
            return
        self.owner.tasks = [t for t in self.owner.tasks if t.id != task.id] + [task]
        a = tuple(self.owner.artifacts)
        out = Path(self.output.text())
        threads = self.threads.value()
        self.owner.start(
            "sift2-preview",
            lambda cancel, progress: prepare_sift2(
                a,
                (task,),
                out,
                nthreads=threads,
                tool_directory=self.owner.tool_directory,
                cancelled=cancel,
                progress=progress,
            ),
        )

    def run(self):
        """No processing without exact displayed-plan approval."""
        if not self.plan or not self.approval.isChecked() or not self.owner.processing_available():
            return
        plan = self.plan
        self.approval.setChecked(False)
        self.record = None
        self.values = None
        self.figure.clear()
        self.canvas.draw_idle()
        self.summary.setText("SIFT2 processing…")

        def execute(cancel, progress):
            result = execute_plan(
                None, plan, approval=plan.digest, cancelled=cancel, progress=progress
            )
            if result["status"] == "validated_pending_qc":
                record = result["validation"]["measurements"]
                result["_display_weights"] = read_weights(record["weights"], record["streamlines"])
            return result

        self.owner.start("sift2-run", execute)

    @staticmethod
    def read_record(path, cancelled=lambda: False):
        """Reopen authentic factors, exact tract order and immutable reference."""
        path = Path(path)
        if path.stat().st_size > 1024**2:
            raise ValueError("Oversized weight result")
        r = read_json(path.read_text())
        r["record_path"] = str(path)
        tract = import_artifact(ImportRequest(**r["tractogram_request"]), cancelled=cancelled)
        w = import_artifact(
            ImportRequest(
                "streamline_weights",
                Path(r["weights"]),
                declarations={
                    **r["declarations"],
                    "tractogram_id": tract.id,
                    "weight_record": str(path),
                },
            ),
            cancelled=cancelled,
        )
        verify_weight_binding(w, tract, cancelled)
        if hash_file(Path(r["reference"]), cancelled) != r["reference_sha256"]:
            raise ValueError("SIFT2 reference changed")
        return r, read_weights(r["weights"], r["streamlines"]), w

    def choose_record(self):
        """Open a preserved scientific result without repeating optimization."""
        p, _ = QFileDialog.getOpenFileName(
            self, "SIFT2 weight identity record", "", "Weight record (*.json)"
        )
        if p:
            self.owner.start("sift2-open", lambda cancel, progress: self.read_record(p, cancel))

    def result(self, kind, result):
        """Only verified factors are reusable; all optimizer findings stay accessible."""
        if kind == "sift2-preview":
            self.plan = result
            self.log_disclosure.toggle.setChecked(True)
            self.log.setPlainText(
                "Approval digest: "
                + result.digest
                + "\n\n"
                + "\n".join(result.payload["scientific_limits"])
                + "\n\n"
                + "\n\n".join(c["display"] for c in result.payload["commands"])
            )
            self.owner.status.setText(
                "SIFT2 plan prepared. Review exact commands and compatibility before approval."
            )
        elif kind == "sift2-run":
            self.plan = None
            self.manifest = result
            if result["status"] == "validated_pending_qc" and result.get("inputs_unchanged"):
                product = result["validation"]["products"][0]
                a = Artifact(product["artifact"])
                if all(x.id != a.id for x in self.owner.artifacts):
                    self.owner.artifacts.append(a)
                r = result["validation"]["measurements"]
                self.show_record(r, result.pop("_display_weights"))
                self.owner.refresh()
            else:
                self.failed(
                    result.get("error", result["status"]), cancelled=result["status"] == "cancelled"
                )
        elif kind == "sift2-open":
            r, values, a = result
            self.log.setPlainText(
                f"Reopened verified factors: {r['weights']}\n"
                f"Source tractogram SHA-256: {r['tractogram_sha256']}\n"
                f"Source FOD SHA-256: {r['fod_sha256']}\n"
                "Exact commands, execution logs and convergence evidence remain in the saved run."
            )
            self.log_disclosure.toggle.setChecked(False)
            if all(x.id != a.id for x in self.owner.artifacts):
                self.owner.artifacts.append(a)
            self.show_record(r, values)
            self.owner.refresh()
        self.update_actions()

    def show_record(self, record, values):
        """A real histogram plus exact numeric statistics; no display normalization of stored factors."""
        self.record, self.values = record, values
        self.index.setRange(0, len(values) - 1)
        self.inspect_index()
        s = record["statistics"]
        c = record["convergence"]
        percentiles = " · ".join(f"{key}: {value:.5g}" for key, value in s["percentiles"].items())
        self.summary.setText(
            f"Processing completed · Scientific QC requires review\n{len(values):,} factors · mu {record['mu']:.8g} · zero {s['zero_percent']:.3g}% · mean {s['mean']:.6g} · median {s['median']:.6g}\n"
            f"Range {s['minimum']:.6g}–{s['maximum']:.6g} · {c['iterations']} iterations · {c['stopping_status']}\n"
            f"Source: {Path(record['tractogram']).name} · FOD normalization: {record['normalization']} · No added mu scaling\n"
            f"Percentiles (display rounded): {percentiles}"
        )
        self.figure.clear()
        self.ax = self.figure.add_subplot(111)
        self.ax.hist(values, bins=60, color="#668b9b")
        self.ax.set_xlabel("Native SIFT2 factor (linear)")
        self.ax.set_ylabel("Streamlines")
        self.figure.tight_layout()
        self.canvas.show()
        self.appearance()
        self.owner.status.setText("Processing completed. Scientific QC requires review.")

    def inspect_index(self, *_):
        """Original 0-based source ordering, independent of rendered subset."""
        if self.values is not None:
            self.weight_label.setText(
                f"Factor {self.values[self.index.value()]:.17g} · original source streamline"
            )

    def open_qc(self):
        """Use the existing scientific QC workspace."""
        if self.record:
            self.qc_requested.emit(Path(self.record["weights"]).parent / "qc-catalog.json")

    def open_result(self):
        """Use the existing tractography viewer with exact factor/source association."""
        if not self.record:
            return
        r = self.record
        product = dict(
            kind="tractogram",
            path=r["tractogram"],
            sha256=r["tractogram_sha256"],
            reference=r["reference"],
            reference_sha256=r["reference_sha256"],
            weight_record=r["record_path"],
            **r["declarations"],
        )
        # Native record and weight file intentionally use the same stem.
        self.view_requested.emit(product)

    def failed(self, message, cancelled=False):
        """Terminal states clear stale data and loading without erasing log history."""
        self.plan = None
        self.record = None
        self.values = None
        self.approval.setChecked(False)
        self.figure.clear()
        self.canvas.hide()
        self.canvas.draw_idle()
        self.weight_label.setText("No verified weight selection.")
        self.summary.setText(
            "Cancelled · no verified product" if cancelled else "Operation failed · " + message
        )

    def appearance(self):
        """Theme changes plot furniture, never numerical weight meaning."""
        # A styled QScrollArea has a transparent Window brush on macOS. Its
        # RGB name is black even in light mode; use the opaque application
        # palette for Matplotlib's raster surface and text instead.
        palette = QApplication.palette()
        bg = palette.color(QPalette.ColorRole.Window).name()
        fg = palette.color(QPalette.ColorRole.WindowText).name()
        self.figure.set_facecolor(bg)
        for ax in self.figure.axes:
            ax.set_facecolor(bg)
            ax.tick_params(colors=fg)
            ax.xaxis.label.set_color(fg)
            ax.yaxis.label.set_color(fg)
            for spine in ax.spines.values():
                spine.set_color(fg)
        self.canvas.draw_idle()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "figure"):
            self.appearance()

    def update_actions(self, *_):
        """Cooperate with the owning single-operation state machine."""
        idle = self.owner.thread is None
        for w in (
            self.tracts,
            self.fod,
            self.reference,
            self.policy,
            self.output,
            self.threads,
            self.gm_scale,
            self.preview,
            self.reopen,
            *self.settings.values(),
        ):
            w.setEnabled(idle)
        self.five_tt.setEnabled(idle and self.policy.currentData() == "act")
        self.proc_mask.setEnabled(idle and self.policy.currentData() == "mask")
        self.approval.setEnabled(idle and self.plan is not None)
        self.run_button.setEnabled(idle and self.plan is not None and self.approval.isChecked())
        self.open_tracks.setEnabled(idle and self.record is not None)
        self.qc.setEnabled(idle and self.record is not None)
        self.task_source.apply_enabled(idle)
