"""Tracking controls in the existing scientific workspace, not another viewer.

Only owned ScientificPlanningPage workers call the shared scientific engine.
Every changed option invalidates exact-plan approval. Source/ROI editing stays
in the existing viewer; this panel consumes explicitly imported derivatives.
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.execution import execute_plan, hash_file
from dmri_studio.core.scientific import Task
from dmri_studio.core.scientific.tracking import METHODS, prepare_tracking

from .components import Disclosure, button, label


def number(low, high, value, decimals=3):
    field = QDoubleSpinBox()
    field.setRange(low, high)
    field.setDecimals(decimals)
    field.setValue(value)
    return field


class TrackingExecutionPanel(QScrollArea):
    """Generate, filter and map tracks with compact setup and explicit approval."""

    view_requested = Signal(dict)
    qc_requested = Signal(object)
    run_requested = Signal(object)
    viewer_source_requested = Signal()
    viewer_reference_requested = Signal()
    viewer_regions_requested = Signal()

    def __init__(self, owner):
        super().__init__(owner)
        self.setWidgetResizable(True)
        content = QWidget()
        self.setWidget(content)
        self.owner = owner
        self.plan = None
        self.manifest = None
        self.viewer_filter_context = None
        root = QVBoxLayout(content)
        from .selected_task import SelectedTask

        self.task_source = SelectedTask(
            self, lambda a: a.startswith("tckgen.") or a in {"tckedit", "tckmap"}
        )
        root.addWidget(self.task_source)
        root.addWidget(
            label(
                "Preview-scale tracking · review FOD amplitude, anatomy, geometry and scientific QC. ACT is explicit; SIFT2 weighting is available in its dedicated workspace.",
                "caption",
            )
        )
        self.result_label = label("No tracking attempt completed in this workspace.", "caption")
        root.addWidget(self.result_label)
        resultrow = QHBoxLayout()
        self.maps = QComboBox()
        self.maps.setAccessibleName("Verified tractography products")
        self.open_button = button("Open result", self.open_result)
        self.qc_button = button("Review QC", self.open_qc)
        self.history_button = button("Run details", self.open_run)
        for w in (self.maps, self.open_button, self.qc_button, self.history_button):
            resultrow.addWidget(w)
        self.result_tools = QWidget()
        self.result_tools.setLayout(resultrow)
        root.addWidget(self.result_tools)
        self.result_tools.hide()
        self.result_label.hide()
        config = QWidget()
        setup = QVBoxLayout(config)
        setup.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.form = form
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.operation = QComboBox()
        for name, key in [
            ("Generate tracks", "generate"),
            ("Filter existing tracks", "filter"),
            ("Tract density", "density"),
        ]:
            self.operation.addItem(name, key)
        self.method = QComboBox()
        for key, text in METHODS.items():
            if not key.startswith("tckgen.act_"):
                self.method.addItem(text, key)
        self.mode = QComboBox()
        self.mode.addItem("Standard", "standard")
        self.mode.addItem("Anatomically constrained (ACT)", "act")
        self.five_tt = QComboBox()
        self.backtrack = QCheckBox("Retry poor structural terminations (iFOD2 ACT)")
        self.backtrack.setChecked(True)
        self.crop = QCheckBox("Crop endpoints at GM–WM interface")
        self.crop.setChecked(True)
        self.source = QComboBox()
        self.mask = QComboBox()
        self.seed = QComboBox()
        self.tracts = QComboBox()
        self.reference = QComboBox()
        self.seed_method = QComboBox()
        self.seed_method.setPlaceholderText("Choose a supported seeding method…")
        for text, key in [
            ("Dynamic FOD", "dynamic"),
            ("Mask image", "image"),
            ("Selected ROI", "roi"),
            ("World-space sphere", "sphere"),
            ("Random per voxel", "random_per_voxel"),
            ("Grid per voxel", "grid_per_voxel"),
            ("GM–WM interface (ACT)", "gmwmi"),
        ]:
            self.seed_method.addItem(text, key)
        self.count = QSpinBox()
        self.count.setRange(1, 1000000)
        self.count.setValue(1000)
        self.output = QLineEdit()
        self.output.setPlaceholderText("New immutable attempt directory")
        out = QHBoxLayout()
        out.addWidget(self.output, 1)
        out.addWidget(button("Choose parent…", self.choose_parent))
        for title, w in [
            ("Operation", self.operation),
            ("Algorithm", self.method),
            ("Tracking mode", self.mode),
            ("Five-tissue anatomy", self.five_tt),
            ("FOD / DWI source", self.source),
            ("Tracking mask", self.mask),
            ("Seeding", self.seed_method),
            ("Seed image / ROI", self.seed),
            ("Existing TCK", self.tracts),
            ("Reference image", self.reference),
            ("Requested streamlines", self.count),
        ]:
            form.addRow(title, w)
            w.setAccessibleName("Tracking " + title)
        form.addRow("New output directory", out)
        form.addRow(self.backtrack)
        form.addRow(self.crop)
        setup.addLayout(form)
        self.description = label(
            "Select verified inputs. Source/ROI subject and space declarations must agree; geometry alone cannot establish registration.",
            "caption",
        )
        setup.addWidget(self.description)
        advanced = QWidget()
        a = QFormLayout(advanced)
        self.advanced_form = a
        self.step = number(0.01, 10, 0.625)
        self.angle = number(0.1, 90, 45, 1)
        self.cutoff = number(0.000001, 100, 0.1, 6)
        self.seed_cutoff = number(0.000001, 100, 0.1, 6)
        self.minimum = number(0.01, 1000, 10, 1)
        self.maximum = number(0.01, 2000, 120, 1)
        self.seeds = QSpinBox()
        self.seeds.setRange(1, 100000000)
        self.seeds.setValue(100000)
        self.attempts = QSpinBox()
        self.attempts.setRange(1, 10000)
        self.attempts.setValue(50)
        self.per_voxel = QSpinBox()
        self.per_voxel.setRange(1, 100)
        self.per_voxel.setValue(1)
        self.sphere = QLineEdit()
        self.sphere.setPlaceholderText("World RAS x,y,z,radius (mm)")
        self.shells = QLineEdit()
        self.shells.setPlaceholderText("Tensor tracking: explicit b0 and one weighted shell")
        self.threads = QSpinBox()
        self.threads.setRange(1, 64)
        self.threads.setValue(2)
        self.ends = QCheckBox("Test only endpoints against include/exclude ROIs (filtering only)")
        self.directional = QCheckBox("Also use directional RGB density channels (LR, AP, SI)")
        self.rk4 = QCheckBox("Fourth-order integration (deterministic methods only)")
        self.samples = QSpinBox()
        self.samples.setRange(2, 100)
        self.samples.setValue(4)
        self.trials = QSpinBox()
        self.trials.setRange(1, 10000)
        self.trials.setValue(1000)
        self.power = QLineEdit()
        self.power.setPlaceholderText("Native default; iFOD methods only")
        self.seed_direction = QLineEdit()
        self.seed_direction.setPlaceholderText("World RAS x,y,z; optional unit vector")
        self.unidirectional = QCheckBox("Track unidirectionally from the seed")
        self.stop_after_includes = QCheckBox(
            "Stop after inclusion ROIs (requires includes; incompatible with ACT backtracking)"
        )
        self.power.textChanged.connect(self.invalidate)
        self.seed_direction.textChanged.connect(self.invalidate)
        self.unidirectional.toggled.connect(self.invalidate)
        self.stop_after_includes.toggled.connect(self.invalidate)
        self.skip = QSpinBox()
        self.skip.setRange(0, 100000000)
        self.skip.setValue(0)
        self.filter_limit = QCheckBox("Limit the number of retained streamlines")
        self.filter_count = QSpinBox()
        self.filter_count.setRange(1, 1000000)
        self.filter_count.setValue(1000)
        self.filter_minimum = number(0, 1000000, 0, 1)
        self.filter_maximum = number(0.01, 1000000, 1000000, 1)
        for title, w in [
            ("FOD amplitude power", self.power),
            ("Initial seed direction", self.seed_direction),
            ("Step (mm)", self.step),
            ("Maximum angle (degrees)", self.angle),
            ("FOD / FA cutoff", self.cutoff),
            ("Seed cutoff", self.seed_cutoff),
            ("Minimum length (mm)", self.minimum),
            ("Maximum length (mm)", self.maximum),
            ("Seed-attempt cap", self.seeds),
            ("Attempts per seed", self.attempts),
            ("Seeds / grid size per voxel", self.per_voxel),
            ("Seed sphere", self.sphere),
            ("Tensor shells (s/mm²)", self.shells),
            ("iFOD2 arc samples", self.samples),
            ("iFOD sampling trials", self.trials),
            ("Skip selected input tracks", self.skip),
            ("Filter: maximum retained tracks", self.filter_count),
            ("Filter: minimum length (mm)", self.filter_minimum),
            ("Filter: maximum length (mm)", self.filter_maximum),
            ("Threads", self.threads),
        ]:
            a.addRow(title, w)
            w.setAccessibleName("Tracking " + title)
        for w in (
            self.rk4,
            self.ends,
            self.directional,
            self.unidirectional,
            self.stop_after_includes,
            self.filter_limit,
        ):
            a.addRow(w)
        setup.addWidget(Disclosure("Advanced tracking and filtering", advanced, expanded=False))
        roi_widget = QWidget()
        r = QHBoxLayout(roi_widget)
        self.constraints = {}
        for role, title in [
            ("include", "Include all"),
            ("exclude", "Exclude any"),
            ("ordered", "Ordered waypoints · drag to reorder"),
        ]:
            col = QVBoxLayout()
            col.addWidget(label(title, "caption"))
            w = QListWidget()
            w.setMaximumHeight(140)
            w.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
            w.setAccessibleName(title)
            col.addWidget(w)
            r.addLayout(col)
            self.constraints[role] = w
            w.itemChanged.connect(self.invalidate)
            w.model().rowsMoved.connect(self.invalidate)
        setup.addWidget(Disclosure("ROI constraints", roi_widget, expanded=False))
        setup.addWidget(button("Use selected viewer ROIs…", self.owner.roi_import_requested.emit))
        setup.addWidget(
            label(
                "Use saved binary ROIs from the existing ROI editor. Passing through a waypoint differs from ending inside it. Original masks and tracts remain unchanged.",
                "caption",
            )
        )
        row = QHBoxLayout()
        self.preview = button("Preview commands", self.prepare)
        row.addWidget(self.preview)
        row.addStretch()
        setup.addLayout(row)
        self.setup = Disclosure("Tracking setup", config, expanded=True)
        self.approval = QCheckBox(
            "I reviewed the source, coordinates, seeding, commands and scientific limitations"
        )
        self.run_button = button("Run approved tracking", self.run)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setMinimumHeight(110)
        self.log.setMaximumHeight(190)
        self.log.setAccessibleName("Tracking commands and live log")
        self.log_section = Disclosure("Command preview and live logs", self.log, expanded=False)
        from .tracking_guided import GuidedTracking

        self.guided = GuidedTracking(self)
        for combo in (self.tracts, self.reference):
            combo.currentIndexChanged.connect(self._update_viewer_filter_notice)
        for inventory in self.constraints.values():
            inventory.itemChanged.connect(lambda *_: self.guided.refresh_constraints())
            inventory.model().rowsMoved.connect(lambda *_: self.guided.refresh_constraints())
        root.addWidget(self.guided)
        advanced = QWidget()
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.addWidget(self.setup)
        advanced_layout.addWidget(self.approval)
        advanced_layout.addWidget(self.run_button)
        advanced_layout.addWidget(self.log_section)
        root.addWidget(Disclosure("Advanced tracking settings and exact controls", advanced))
        root.addStretch()
        for combo in (
            self.operation,
            self.method,
            self.source,
            self.mask,
            self.seed,
            self.tracts,
            self.reference,
            self.seed_method,
            self.mode,
            self.five_tt,
        ):
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            combo.setMinimumContentsLength(24)
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
        self.approval.toggled.connect(self.update_actions)
        for w in (self.operation, self.method, self.seed_method, self.mode):
            w.currentIndexChanged.connect(self.refresh)
        for w in (self.source, self.mask, self.seed, self.tracts, self.reference, self.five_tt):
            w.currentIndexChanged.connect(self.invalidate)
        for w in (self.output, self.sphere, self.shells):
            w.textChanged.connect(self.invalidate)
        for w in (
            self.step,
            self.angle,
            self.cutoff,
            self.seed_cutoff,
            self.minimum,
            self.maximum,
            self.seeds,
            self.attempts,
            self.per_voxel,
            self.threads,
            self.count,
            self.samples,
            self.trials,
            self.skip,
            self.filter_count,
            self.filter_minimum,
            self.filter_maximum,
        ):
            w.valueChanged.connect(self.invalidate)
        self.filter_limit.toggled.connect(self.invalidate)
        self.count.valueChanged.connect(self._legacy_filter_count_changed)
        self.minimum.valueChanged.connect(self._legacy_filter_minimum_changed)
        self.maximum.valueChanged.connect(self._legacy_filter_maximum_changed)
        for w in (self.ends, self.directional, self.rk4, self.backtrack, self.crop):
            w.toggled.connect(self.invalidate)
        self.refresh()

    def invalidate(self, *_):
        self.plan = None
        self.approval.setChecked(False)
        if hasattr(self, "guided"):
            self.guided.clear_plan()
        self.update_actions()

    def refresh(self, *_):
        self.task_source.refresh_tasks()
        self.invalidate()
        tensor = self.method.currentData() in ("tckgen.tensor_det", "tckgen.tensor_prob")
        act = self.mode.currentData() == "act"
        for key in ("tckgen.tensor_det", "tckgen.tensor_prob"):
            self.method.model().item(self.method.findData(key)).setEnabled(not act)
        interface = self.seed_method.model().item(self.seed_method.findData("gmwmi"))
        interface.setEnabled(act)
        if not act and self.seed_method.currentData() == "gmwmi":
            self.seed_method.blockSignals(True)
            self.seed_method.setCurrentIndex(-1)
            self.seed_method.blockSignals(False)
        dynamic = self.seed_method.model().item(self.seed_method.findData("dynamic"))
        dynamic.setEnabled(not tensor)
        dynamic.setToolTip(
            "Dynamic FOD seeding requires an FOD tracking source; tensor tracking requires DWI and another seeding mechanism."
        )
        if tensor and self.seed_method.currentData() == "dynamic":
            self.seed_method.blockSignals(True)
            self.seed_method.setCurrentIndex(-1)
            self.seed_method.blockSignals(False)
        seed_kind = (
            "roi"
            if self.seed_method.currentData() == "roi"
            else "gmwmi"
            if self.seed_method.currentData() == "gmwmi"
            else "mask"
        )
        for combo, kinds in [
            (self.source, {"dwi" if tensor else "fod"}),
            (self.mask, {"mask"}),
            (self.seed, {seed_kind}),
            (self.tracts, {"tractogram"}),
            (self.reference, {"anatomy"}),
            (self.five_tt, {"5tt"}),
        ]:
            old = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            name = next(iter(kinds))
            combo.addItem(f"Select imported {name}…", None)
            for item in self.owner.artifacts:
                if item.kind in kinds:
                    combo.addItem(Path(item.payload["path"]).name, item.id)
                    combo.setItemData(
                        combo.count() - 1, str(item.payload["path"]), Qt.ItemDataRole.ToolTipRole
                    )
            if combo.count() == 1:
                combo.setItemText(0, f"No imported {name} — use Import in the guided task")
            combo.setCurrentIndex(max(0, combo.findData(old)))
            combo.blockSignals(False)
        for w in self.constraints.values():
            existing = {
                w.item(i).data(Qt.ItemDataRole.UserRole): w.item(i).checkState()
                for i in range(w.count())
            }
            w.blockSignals(True)
            w.clear()
            ranks = {identity: i for i, identity in enumerate(existing)}
            for item in sorted(self.owner.artifacts, key=lambda a: ranks.get(a.id, len(ranks))):
                if item.kind == "roi":
                    row = QListWidgetItem(Path(item.payload["path"]).name)
                    row.setData(Qt.ItemDataRole.UserRole, item.id)
                    row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    row.setCheckState(existing.get(item.id, Qt.CheckState.Unchecked))
                    w.addItem(row)
            w.blockSignals(False)
        if hasattr(self, "guided"):
            self.guided.refresh_inventory()
        self.update_actions()

    def use_viewer_filter_source(
        self,
        source,
        reference,
        *,
        total,
        displayed,
        edited,
        expected_hash=None,
        source_signature=None,
    ):
        """Select only unambiguous recorded inputs; planning rechecks their bytes."""
        self.viewer_filter_context = dict(
            source=Path(source).resolve(),
            reference=Path(reference).resolve(),
            total=total,
            displayed=displayed,
            edited=edited,
            expected_hash=expected_hash,
            source_signature=source_signature,
        )
        self.guided.select_task("filter")
        self.refresh()
        source_count = self._select_viewer_artifact(
            "tractogram",
            self.viewer_filter_context["source"],
            self.tracts,
            expected_hash=expected_hash,
        )
        reference_count = self._select_viewer_artifact(
            "anatomy", self.viewer_filter_context["reference"], self.reference
        )
        self.guided.show_viewer_source(self.viewer_filter_context)
        return source_count, reference_count

    def _select_viewer_artifact(self, kind, path, combo, *, expected_hash=None):
        matches = [
            a
            for a in self.owner.artifacts
            if a.kind == kind
            and Path(a.payload["path"]).resolve() == path
            and (
                expected_hash is None
                or any(
                    Path(s["path"]).resolve() == path and s["sha256"] == expected_hash
                    for s in a.payload.get("sources", ())
                )
            )
        ]
        if len(matches) == 1:
            index = combo.findData(matches[0].id)
            if index >= 0:
                combo.setCurrentIndex(index)
        else:
            combo.setCurrentIndex(0)
        return len(matches)

    def select_viewer_import(self, artifact):
        context = self.viewer_filter_context
        if context is None or artifact.kind not in {"tractogram", "anatomy"}:
            return
        path = Path(artifact.payload["path"]).resolve()
        role = "source" if artifact.kind == "tractogram" else "reference"
        if path != context[role]:
            return
        if role == "source" and context["source_signature"] is not None:
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != tuple(context["source_signature"]):
                self.owner.status.setText(
                    "The Viewer track source changed after it was loaded. Reopen the tractogram "
                    "in the Viewer before filtering."
                )
                return False
        if (
            role == "source"
            and context["expected_hash"]
            and not any(
                Path(s["path"]).resolve() == path and s["sha256"] == context["expected_hash"]
                for s in artifact.payload.get("sources", ())
            )
        ):
            self.owner.status.setText(
                "The Viewer source changed since it was opened. Reload its verified evidence before filtering."
            )
            return
        combo = self.tracts if role == "source" else self.reference
        index = combo.findData(artifact.id)
        if index >= 0:
            combo.setCurrentIndex(index)
        self.guided.show_viewer_source(context)
        return True

    def _update_viewer_filter_notice(self, *_):
        if self.viewer_filter_context is not None:
            self.guided.show_viewer_source(self.viewer_filter_context)

    def choose_parent(self):
        path = QFileDialog.getExistingDirectory(self, "Tractography output parent")
        if path:
            self.output.setText(str(Path(path) / "tracking-attempt001"))

    def convert_trk(self):
        """Create an explicit full-source TCK derivative through the Qt-free verifier."""
        if self.owner.thread is not None:
            return
        source = next((a for a in self.owner.artifacts if a.id == self.tracts.currentData()), None)
        reference = next(
            (a for a in self.owner.artifacts if a.id == self.reference.currentData()), None
        )
        if source is None or reference is None:
            self.owner.status.setText(
                "Import and select the TRK source and its documented NIfTI reference first."
            )
            return
        if Path(source.payload["path"]).suffix.lower() != ".trk":
            self.owner.status.setText(
                "The selected tractogram is already TCK; no conversion is needed."
            )
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "Save new full-source TCK derivative", "", "MRtrix tractogram (*.tck)"
        )
        if not target:
            return
        destination = Path(target)
        if destination.suffix.lower() != ".tck":
            destination = destination.with_suffix(".tck")
        from dmri_studio.core.tract_filter_conversion import convert_verified_trk

        self.owner.start(
            "tracking-convert-trk",
            lambda cancel, progress: convert_verified_trk(
                source, reference, destination, cancelled=cancel, progress=progress
            ),
        )

    def _legacy_filter_count_changed(self, value):
        """Retain old form callers without imposing a silent filter cap."""
        if self.operation.currentData() == "filter":
            self.filter_limit.setChecked(True)
            self.filter_count.setValue(value)

    def _legacy_filter_minimum_changed(self, value):
        if self.operation.currentData() == "filter":
            self.filter_minimum.setValue(value)

    def _legacy_filter_maximum_changed(self, value):
        if self.operation.currentData() == "filter":
            self.filter_maximum.setValue(value)

    def selected_task(self):
        selected = self.task_source.task()
        if selected is not None:
            return selected
        operation = self.operation.currentData()
        inputs = {}
        p = {}
        if operation == "generate":
            spec = self.method.currentData()
            if not spec:
                raise ValueError("Choose a compatible tracking algorithm")
            tensor = spec.startswith("tckgen.tensor")
            act = self.mode.currentData() == "act"
            if act and tensor:
                raise ValueError("ACT tensor tracking is not validated; select an FOD algorithm")
            inputs = {"dwi" if tensor else "fod": self.source.currentData()}
            if self.mask.currentData() or not act:
                inputs["mask"] = self.mask.currentData()
            if act:
                inputs.update(
                    five_tt=self.five_tt.currentData(), reference=self.reference.currentData()
                )
            method = self.seed_method.currentData()
            if method is None:
                raise ValueError(
                    "Choose a supported seeding method; dynamic FOD seeding is unavailable for tensor DWI tracking"
                )
            if self.reference.currentData():
                inputs["reference"] = self.reference.currentData()
            if method in ("image", "roi", "random_per_voxel", "grid_per_voxel"):
                inputs["seed_roi" if method == "roi" else "seed_mask"] = self.seed.currentData()
            if method == "gmwmi":
                inputs["gmwmi"] = self.seed.currentData()
            p = dict(
                select=self.count.value(),
                seeds=self.seeds.value(),
                seed_method=method,
                step_mm=self.step.value(),
                angle_deg=self.angle.value(),
                cutoff=self.cutoff.value(),
                seed_cutoff=self.seed_cutoff.value(),
                min_length_mm=self.minimum.value(),
                max_length_mm=self.maximum.value(),
                max_attempts_per_seed=self.attempts.value(),
            )
            if self.power.text().strip():
                if not self.power.isEnabled():
                    raise ValueError("Clear FOD power before choosing an unsupported algorithm")
                p["power"] = float(self.power.text())
            if self.seed_direction.text().strip():
                p["seed_direction"] = [
                    float(v.strip()) for v in self.seed_direction.text().split(",")
                ]
            p["unidirectional"] = "on" if self.unidirectional.isChecked() else "off"
            p["stop_after_includes"] = "on" if self.stop_after_includes.isChecked() else "off"
            if method in ("random_per_voxel", "grid_per_voxel"):
                p["per_voxel"] = self.per_voxel.value()
            if method == "sphere":
                p["seed_sphere"] = [float(x.strip()) for x in self.sphere.text().split(",")]
            if tensor:
                p["shells"] = [float(x.strip()) for x in self.shells.text().split(",")]
            if spec in ("tckgen.sdstream", "tckgen.tensor_det"):
                p["rk4"] = "on" if self.rk4.isChecked() else "off"
            if spec in ("tckgen.ifod1", "tckgen.ifod2"):
                p["trials"] = self.trials.value()
            if spec == "tckgen.ifod2":
                p["samples"] = self.samples.value()
            if act:
                p.update(
                    backtrack="on" if self.backtrack.isChecked() else "off",
                    crop_at_gmwmi="on" if self.crop.isChecked() else "off",
                )
                spec = spec.replace("tckgen.", "tckgen.act_")
        else:
            spec = "tckedit" if operation == "filter" else "tckmap"
            inputs = dict(tracts=self.tracts.currentData(), reference=self.reference.currentData())
            p = (
                dict(
                    min_length_mm=self.filter_minimum.value(),
                    max_length_mm=self.filter_maximum.value(),
                    skip=self.skip.value(),
                    ends_only="on" if self.ends.isChecked() else "off",
                )
                if operation == "filter"
                else dict(directional="on" if self.directional.isChecked() else "off")
            )
            if operation == "filter" and self.filter_limit.isChecked():
                p["number"] = self.filter_count.value()
        if operation != "density":
            for role, w in self.constraints.items():
                selected = [
                    w.item(i).data(Qt.ItemDataRole.UserRole)
                    for i in range(w.count())
                    if w.item(i).checkState() == Qt.CheckState.Checked
                ]
                if len(selected) > 8:
                    raise ValueError(
                        "At most eight explicitly ordered masks per constraint role are supported"
                    )
                inputs.update({f"{role}_{i + 1}": v for i, v in enumerate(selected)})
        if any(v is None for v in inputs.values()):
            raise ValueError("Select every required scientific input before preview")
        return Task("tracking", spec, inputs, p)

    def prepare(self):
        self.invalidate()
        try:
            if not self.output.text().strip():
                raise ValueError("Choose a new derivative directory")
            task = self.selected_task()
            context = self.viewer_filter_context
            if context is not None and task.algorithm == "tckedit":
                selected = next(
                    (a for a in self.owner.artifacts if a.id == task.inputs.get("tracts")), None
                )
                if (
                    selected is not None
                    and Path(selected.payload["path"]).resolve() == context["source"]
                ):
                    signature = context["source_signature"]
                    stat = context["source"].stat()
                    if signature is not None and (stat.st_size, stat.st_mtime_ns) != tuple(
                        signature
                    ):
                        raise ValueError(
                            "The Viewer track source changed after loading. Reopen it before planning."
                        )
            self.owner.tasks = [t for t in self.owner.tasks if t.id != task.id] + [task]
            art = tuple(self.owner.artifacts)
            output = Path(self.output.text())
            threads = self.threads.value()
            directory = self.owner.tool_directory
        except (ValueError, TypeError) as e:
            self.owner.status.setText(str(e))
            return
        self.owner.start(
            "tracking-preview",
            lambda cancel, progress: prepare_tracking(
                art,
                (task,),
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
        self.result_tools.hide()
        self.result_label.setText("Running · " + plan.payload["output_directory"])
        self.result_label.show()
        self.log_section.toggle.setChecked(True)
        self.owner.start(
            "tracking-run",
            lambda cancel, progress: execute_plan(
                None, plan, approval=plan.digest, cancelled=cancel, progress=progress
            ),
        )

    def result(self, kind, result):
        if kind == "tracking-preview":
            self.plan = result
            data = result.to_dict()
            self.log.setPlainText(
                "Approval digest: "
                + result.digest
                + "\n\n"
                + "\n".join(data["scientific_limits"])
                + "\n\n"
                + "\n\n".join(c["display"] for c in data["commands"])
            )
            self.owner.status.setText("Commands prepared. Review the exact plan before approval.")
            self.setup.toggle.setChecked(False)
            self.log_section.toggle.setChecked(True)
            self.guided.plan_ready(result)
        else:
            self.manifest = result
            self.plan = None
            self.maps.clear()
            good = result["status"] == "validated_pending_qc" and result.get("inputs_unchanged")
            text = (
                "Processing completed · QC requires review"
                if good
                else result["status"].replace("_", " ").capitalize()
            )
            self.result_label.setText(text + " · " + result["plan"]["output_directory"])
            self.result_label.show()
            self.result_tools.show()
            self.log_section.toggle.setChecked(False)
            self.owner.status.setText(
                text
                + (
                    (". " + result["error"])
                    if result.get("error")
                    else ". Scientific acceptance remains pending."
                )
            )
            self.log.appendPlainText(self.owner.status.text())
            if good:
                from dmri_studio.core.scientific import Artifact

                for product in result["validation"].get("products", []):
                    artifact = Artifact(product["artifact"])
                    if all(a.id != artifact.id for a in self.owner.artifacts):
                        self.owner.artifacts.append(artifact)
                self.owner.refresh()
                folder = Path(result["plan"]["output_directory"])
                for row in result["plan"]["bindings"]:
                    for role, name in row["outputs"].items():
                        # MRtrix's native density MIF is a verified scientific
                        # product, but the Viewer accepts its separately verified
                        # NIfTI export. Keep the MIF in the manifest, not this list.
                        if role in {"reference", "density"}:
                            continue
                        self.maps.addItem(
                            name,
                            dict(
                                path=str(folder / name),
                                kind="tractogram" if role == "tracts" else "scalar",
                                sha256=result["outputs"][name]["sha256"],
                                reference=str(folder / row["outputs"]["reference"])
                                if role == "tracts"
                                else None,
                                reference_sha256=result["outputs"][row["outputs"]["reference"]][
                                    "sha256"
                                ]
                                if role == "tracts"
                                else None,
                                subject_id=row["source"]["declarations"].get("subject_id"),
                                space_id=row["source"]["declarations"].get("space_id"),
                            ),
                        )
                self.setup.toggle.setChecked(False)
            self.guided.result_ready(result)
        self.update_actions()

    def failed(self, message, cancelled=False, running=False):
        """Terminal pre-execution errors must not leave a running result label."""
        self.plan = None
        self.approval.setChecked(False)
        if running:
            self.manifest = None
            self.maps.clear()
            self.result_tools.hide()
            self.result_label.setText(
                "Cancelled · no verified tractogram published"
                if cancelled
                else "Execution failed · no verified tractogram published"
            )
            self.result_label.show()
        self.log.appendPlainText(("Cancelled: " if cancelled else "Operation failed: ") + message)
        self.log_section.toggle.setChecked(True)
        self.guided.clear_plan()
        self.update_actions()

    def update_actions(self, *_):
        if not hasattr(self, "run_button"):
            return
        idle = self.owner.thread is None
        op = self.operation.currentData()
        generate = op == "generate"
        for field in (self.method, self.mode, self.source, self.mask, self.seed_method, self.seed):
            self.form.setRowVisible(field, generate)
        act = self.mode.currentData() == "act"
        for field in (self.five_tt, self.backtrack, self.crop):
            self.form.setRowVisible(field, generate and act)
        self.five_tt.setEnabled(idle and generate and act)
        self.mode.setEnabled(idle and generate)
        self.crop.setEnabled(idle and generate and act)
        allow_backtrack = self.method.currentData() == "tckgen.ifod2"
        self.backtrack.setEnabled(idle and generate and act and allow_backtrack)
        if act and not allow_backtrack:
            self.backtrack.blockSignals(True)
            self.backtrack.setChecked(False)
            self.backtrack.blockSignals(False)
        self.form.setRowVisible(self.tracts, not generate)
        self.form.setRowVisible(self.count, generate)
        for field in (
            self.step,
            self.angle,
            self.cutoff,
            self.seed_cutoff,
            self.seeds,
            self.attempts,
            self.per_voxel,
            self.sphere,
            self.shells,
            self.samples,
            self.trials,
            self.rk4,
        ):
            self.advanced_form.setRowVisible(field, generate)
        for field in (self.minimum, self.maximum):
            self.advanced_form.setRowVisible(field, generate)
        for field in (
            self.filter_limit,
            self.filter_count,
            self.filter_minimum,
            self.filter_maximum,
        ):
            self.advanced_form.setRowVisible(field, op == "filter")
        self.advanced_form.setRowVisible(self.ends, op == "filter")
        self.advanced_form.setRowVisible(self.skip, op == "filter")
        self.advanced_form.setRowVisible(self.directional, op == "density")
        method = self.method.currentData()
        for w in (self.operation, self.output, self.threads, self.preview):
            w.setEnabled(idle)
        for w in (
            self.source,
            self.mask,
            self.method,
            self.seed_method,
            self.step,
            self.angle,
            self.cutoff,
            self.seed_cutoff,
            self.seeds,
            self.attempts,
        ):
            w.setEnabled(idle and generate)
        self.tracts.setEnabled(idle and not generate)
        self.reference.setEnabled(idle)
        for w in (self.count, self.minimum, self.maximum):
            w.setEnabled(idle and generate)
        self.filter_limit.setEnabled(idle and op == "filter")
        self.filter_count.setEnabled(idle and op == "filter" and self.filter_limit.isChecked())
        self.filter_minimum.setEnabled(idle and op == "filter")
        self.filter_maximum.setEnabled(idle and op == "filter")
        self.seed.setEnabled(
            idle
            and generate
            and self.seed_method.currentData()
            in ("image", "roi", "random_per_voxel", "grid_per_voxel", "gmwmi")
        )
        self.sphere.setEnabled(idle and generate and self.seed_method.currentData() == "sphere")
        self.per_voxel.setEnabled(
            idle
            and generate
            and self.seed_method.currentData() in ("random_per_voxel", "grid_per_voxel")
        )
        self.shells.setEnabled(idle and generate and method.startswith("tckgen.tensor"))
        self.rk4.setEnabled(
            idle and generate and method in ("tckgen.sdstream", "tckgen.tensor_det")
        )
        self.samples.setEnabled(idle and generate and method == "tckgen.ifod2")
        self.trials.setEnabled(idle and generate and method in ("tckgen.ifod1", "tckgen.ifod2"))
        self.ends.setEnabled(idle and op == "filter")
        self.skip.setEnabled(idle and op == "filter")
        self.directional.setEnabled(idle and op == "density")
        for w in self.constraints.values():
            w.setEnabled(idle and op != "density")
        self.approval.setEnabled(idle and self.plan is not None)
        self.run_button.setEnabled(idle and self.plan is not None and self.approval.isChecked())
        self.open_button.setEnabled(idle and self.maps.count() > 0)
        self.qc_button.setEnabled(
            idle
            and self.manifest is not None
            and self.manifest.get("status") == "validated_pending_qc"
        )
        self.history_button.setEnabled(idle and self.manifest is not None)
        self.power.setEnabled(idle and generate and method in {"tckgen.ifod1", "tckgen.ifod2"})
        self.power.setToolTip(
            "Only supported for probabilistic FOD methods; blank preserves the native default"
        )
        self.seed_direction.setEnabled(idle and generate)
        self.unidirectional.setEnabled(idle and generate)
        self.stop_after_includes.setEnabled(
            idle and generate and not (act and self.backtrack.isChecked())
        )
        self.task_source.apply_enabled(idle)
        if hasattr(self, "guided"):
            self.guided.update_actions()

    def open_result(self):
        product = self.maps.currentData()
        if not product:
            return

        def verify(cancel, progress):
            if hash_file(Path(product["path"]), cancel) != product["sha256"]:
                raise ValueError("Tractography result changed; re-open verified run evidence")
            return product

        self.owner.start("tracking-view", verify)

    def open_original(self):
        """Reopen the exact approved filter source against its verified output grid."""
        record = self.manifest
        if (
            not record
            or record.get("status") != "validated_pending_qc"
            or not record.get("inputs_unchanged")
        ):
            return
        plan = record["plan"]
        row = next(
            (item for item in plan.get("bindings", ()) if item.get("algorithm") == "tckedit"),
            None,
        )
        if row is None:
            return
        # The planner's primary source is the reference image for tckedit.
        # The full tractogram has its own explicit input binding.
        source = row.get("input_bindings", {}).get("tracts")
        if source is None:
            self.owner.status.setText(
                "This historical filter record has no bound tractogram; the original cannot be verified."
            )
            return
        digest = next(
            (
                item["sha256"]
                for item in source["sources"]
                if Path(item["path"]).resolve() == Path(source["path"]).resolve()
            ),
            None,
        )
        reference_name = row["outputs"]["reference"]
        reference = Path(plan["output_directory"]) / reference_name
        reference_digest = record["outputs"][reference_name]["sha256"]
        if digest is None:
            self.owner.status.setText("Original source hash is missing from this run record.")
            return
        product = dict(
            kind="tractogram",
            path=source["path"],
            sha256=digest,
            reference=str(reference),
            reference_sha256=reference_digest,
            subject_id=source["declarations"].get("subject_id"),
            space_id=source["declarations"].get("space_id"),
        )

        def verify(cancel, progress):
            if hash_file(Path(product["path"]), cancel) != digest:
                raise ValueError("Original tractogram changed; reopen historical run evidence")
            if hash_file(reference, cancel) != reference_digest:
                raise ValueError("Verified reference changed; reopen historical run evidence")
            return product

        self.owner.start("tracking-original-view", verify)

    def open_qc(self):
        if self.manifest:
            self.qc_requested.emit(
                Path(self.manifest["plan"]["output_directory"]) / "qc-catalog.json"
            )

    def open_run(self):
        if self.manifest:
            self.run_requested.emit(
                Path(self.manifest["plan"]["output_directory"]) / "manifest.json"
            )
