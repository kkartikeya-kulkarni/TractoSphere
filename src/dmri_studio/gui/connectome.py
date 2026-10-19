"""Connectomics controls and scientific heatmap in the existing workstation.

All file validation/native execution is dispatched through the owning scientific
page worker. This module only presents verified matrices, never computes them.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.colors import ListedColormap, LogNorm
from matplotlib.figure import Figure
from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.execution import execute_plan, hash_file
from dmri_studio.core.scientific import ImportRequest, Task, import_artifact
from dmri_studio.core.scientific.connectome import (
    load_bundle,
    node_table,
    prepare_connectome,
    read_matrix,
)
from dmri_studio.core.scientific.connectome_registry import METRICS

from .components import Disclosure, button, label


class MatrixView(QWidget):
    """Precision-preserving matrix, bounded labels, native pan/zoom and edge selection."""

    selected = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.title = label("No connectome open", "section")
        root.addWidget(self.title)
        self.figure = Figure(figsize=(7, 5))
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.canvas.setAccessibleName(
            "Connectivity matrix. Use arrow keys to move selected node pair; pan and zoom toolbar available."
        )
        toolbar = NavigationToolbar2QT(self.canvas, self)
        toolbar.setIconSize(QSize(16, 16))
        toolbar.setStyleSheet("QToolButton { padding: 3px; min-width: 20px; min-height: 20px; }")
        root.addWidget(toolbar)
        root.addWidget(self.canvas, 1)
        self.details = label(
            "Open a verified result, or associate an existing CSV with its original atlas and row mapping."
        )
        root.addWidget(self.details)
        self.matrix = None
        self.occupancy = None
        self.nodes = []
        self.cell = (0, 0)
        self.metric = "unknown"
        self.artists = []
        self.bundle = None
        self.log_scale = False
        self.canvas.mpl_connect("button_press_event", self.click)
        self.canvas.mpl_connect("motion_notify_event", self.hover)
        self.canvas.mpl_connect("key_press_event", self.key)
        self.canvas.mpl_connect("resize_event", lambda _: self.layout_plot())
        self.heatmap_load_seconds = None

    def display(
        self,
        matrix,
        nodes,
        metric,
        occupancy=None,
        *,
        title="",
        bundle=None,
        logarithmic=False,
        difference=False,
    ):
        start = time.perf_counter()
        self.matrix = np.asarray(matrix, dtype="f8")
        self.nodes = nodes
        self.metric = metric
        self.occupancy = occupancy
        self.bundle = bundle
        self.log_scale = logarithmic
        self.difference = difference
        self.cell = (0, 0)
        self.figure.clear()
        self.ax = self.figure.add_axes([0.15, 0.2, 0.65, 0.7])
        self.color_axis = self.figure.add_axes([0.84, 0.2, 0.025, 0.7])
        missing = np.zeros(self.matrix.shape, bool) if occupancy is None else occupancy == 0
        validity = None if not bundle else bundle.get("_weight_validity_display")
        if validity is not None and metric in {"sift2_mean_length", "sift2_mean_fa"}:
            missing |= ~np.asarray(validity, dtype=bool)
        invalid = ~np.isfinite(self.matrix)
        masked = np.ma.masked_where(missing | invalid, self.matrix)
        cmap = __import__("matplotlib").colormaps["coolwarm" if difference else "viridis"].copy()
        cmap = cmap.with_extremes(bad="#d4d6d6")
        norm = None
        if logarithmic:
            positive = self.matrix[(self.matrix > 0) & ~missing & ~invalid]
            if len(positive):
                norm = LogNorm(
                    vmin=float(positive.min()),
                    vmax=max(float(positive.max()), float(positive.min()) * 1.001),
                )
            masked = np.ma.masked_where(missing | invalid | (self.matrix <= 0), self.matrix)
        image = self.ax.imshow(
            masked, origin="upper", interpolation="nearest", cmap=cmap, norm=norm
        )
        if invalid.any():
            self.ax.imshow(
                np.ma.masked_where(~invalid, np.ones(self.matrix.shape)),
                cmap=ListedColormap(["#ae5670"]),
                vmin=0,
                vmax=1,
            )
        self.figure.colorbar(
            image,
            cax=self.color_axis,
            label=METRICS.get(metric, {}).get("units", "unknown metric units"),
        )
        self.ax.set_xlabel("Target node ID")
        self.ax.set_ylabel("Source node ID")
        self.artists = [
            self.ax.axhline(0, color="#e7b05b", lw=0.7),
            self.ax.axvline(0, color="#e7b05b", lw=0.7),
        ]
        self.title.setText(
            f"{title} · {len(nodes)} × {len(nodes)} · {METRICS.get(metric, {}).get('name', metric)}"
            + (" · display log scale" if logarithmic else " · linear display")
            + (" · difference" if difference else "")
        )
        self.layout_plot()
        self.canvas.draw()
        self.select_cell(0, 0)
        self.heatmap_load_seconds = time.perf_counter() - start

    def layout_plot(self):
        """Use the available canvas height; avoid tiny/overlapping laptop labels."""
        if not hasattr(self, "ax") or not self.nodes:
            return
        width, height = self.figure.get_size_inches() * self.figure.dpi
        side = max(40, min(height - 65, width - 165))
        left = max(60, (width - side - 70) / 2)
        bottom = 45
        self.ax.set_position([left / width, bottom / height, side / width, side / height])
        self.color_axis.set_position(
            [(left + side + 18) / width, bottom / height, 12 / width, side / height]
        )
        ticks = np.unique(
            np.linspace(
                0, len(self.nodes) - 1, max(2, min(12, int(side / 32), len(self.nodes)))
            ).astype(int)
        )
        names = [str(self.nodes[i]["parcel_id"]) for i in ticks]
        self.ax.set_xticks(ticks, names)
        self.ax.set_yticks(ticks, names)
        self.apply_appearance()
        self.canvas.draw_idle()

    def apply_appearance(self):
        """Qt palette applies to plot furniture, never to scientific color meaning."""
        background = self.palette().color(QPalette.ColorRole.Window).name()
        foreground = self.palette().color(QPalette.ColorRole.WindowText).name()
        self.figure.set_facecolor(background)
        for axis in (self.ax, self.color_axis):
            axis.set_facecolor(background)
            axis.tick_params(colors=foreground, labelsize=9)
            axis.xaxis.label.set_color(foreground)
            axis.yaxis.label.set_color(foreground)
            for spine in axis.spines.values():
                spine.set_color(foreground)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "ax"):
            self.apply_appearance()
            self.canvas.draw_idle()

    def select_cell(self, row, column):
        if self.matrix is None:
            return
        row = max(0, min(len(self.nodes) - 1, row))
        column = max(0, min(len(self.nodes) - 1, column))
        self.cell = (row, column)
        a, b = self.nodes[row], self.nodes[column]
        v = self.matrix[row, column]
        count = None if self.occupancy is None else int(self.occupancy[row, column])
        state = (
            "Invalid measurement"
            if not np.isfinite(v)
            else "No assigned streamlines / undefined mean"
            if count == 0 and self.metric not in {"count", "sift2_weighted"}
            else "No assigned streamlines"
            if count == 0
            else "Occupancy unavailable; zero is not authenticated absence"
            if count is None
            else f"{count} contributing streamlines"
        )
        assignment = (
            "" if not self.bundle else " · " + self.bundle["assignment_parameters"]["assignment"]
        )
        if getattr(self, "difference", False):
            state = (
                "Display difference; source matrices unchanged"
                if count != 0
                else "Undefined difference: one or both source means lack valid contributions"
            )
        if (
            self.metric in {"mean_fa", "sift2_mean_fa"}
            and not getattr(self, "difference", False)
            and np.isfinite(v)
            and not 0 <= v <= 1
            and count != 0
        ):
            state += " · nonphysical FA value requires review (unchanged)"
        if self.bundle and self.metric.startswith("sift2_"):
            weights = self.bundle.get("_weight_sum_display")
            if weights is not None:
                state += f" · edge factor sum {weights[row, column]:.17g}"
                if self.metric != "sift2_weighted" and weights[row, column] == 0:
                    state += " · zero total weight: weighted mean undefined (native stored zero)"
            state += " · no added mu scaling"
        self.details.setText(
            f"{a['parcel_id']} · {a['label']} → {b['parcel_id']} · {b['label']}\n{v:.17g} {METRICS.get(self.metric, {}).get('units', '(unknown units)')} · {state}{assignment}"
        )
        self.artists[0].set_ydata([row, row])
        self.artists[1].set_xdata([column, column])
        self.canvas.draw_idle()
        self.selected.emit(row, column)

    def click(self, event):
        if (
            event.inaxes is getattr(self, "ax", None)
            and event.xdata is not None
            and event.ydata is not None
        ):
            self.select_cell(int(np.floor(event.ydata + 0.5)), int(np.floor(event.xdata + 0.5)))

    def hover(self, event):
        if self.matrix is not None and event.inaxes is self.ax and event.xdata is not None:
            r, c = int(np.floor(event.ydata + 0.5)), int(np.floor(event.xdata + 0.5))
            if 0 <= r < len(self.nodes) and 0 <= c < len(self.nodes):
                self.canvas.setToolTip(
                    f"Node {self.nodes[r]['parcel_id']} → {self.nodes[c]['parcel_id']}: {self.matrix[r, c]:.17g}"
                )

    def key(self, event):
        if event.key in ("left", "right", "up", "down"):
            r, c = self.cell
            self.select_cell(
                r + ({"up": -1, "down": 1}.get(event.key, 0)),
                c + ({"left": -1, "right": 1}.get(event.key, 0)),
            )


class ConnectomePanel(QWidget):
    """Guided configuration, full native approval and matrix-to-track navigation."""

    view_requested = Signal(dict)
    qc_requested = Signal(object)
    run_requested = Signal(object)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.plan = None
        self.manifest = None
        self.bundle = None
        self.bundle_path = None
        self.matrix_paths = {}
        self.matrix_cache = {}
        self.matrix_hashes = {}
        self.occupancy_cache = None
        self.comparing = False
        self.nodes = []
        self.last_product = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        setup = QVBoxLayout(content)
        setup.addWidget(
            label(
                "Existing tractography + registered discrete atlas. Preview-scale non-ACT tracks require scientific review. Optional exact-source SIFT2 factors remain distinct from raw counts.",
                "caption",
            )
        )
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.tracts = QComboBox()
        self.atlas = QComboBox()
        self.reference = QComboBox()
        self.fa = QComboBox()
        self.weights = QComboBox()
        for text, w in [
            ("Tractogram", self.tracts),
            ("Registered parcellation", self.atlas),
            ("Anatomical / FA reference", self.reference),
            ("FA sampling image", self.fa),
            ("SIFT2 weight factors", self.weights),
        ]:
            form.addRow(text, w)
            w.setAccessibleName(text)
        self.metric = QComboBox()
        for key, text in [
            ("all", "Count, mean length and mean FA"),
            ("all_sift2", "All six metrics · weighted and unweighted"),
            ("sift2_weighted", "SIFT2-weighted contribution"),
            ("sift2_mean_length", "SIFT2-weighted mean length"),
            ("sift2_mean_fa", "SIFT2-weighted mean FA"),
            ("count", "Streamline count"),
            ("mean_length", "Mean length"),
            ("mean_fa", "Mean FA"),
        ]:
            self.metric.addItem(text, key)
        form.addRow("Metrics", self.metric)
        self.node_summary = label(
            "Select a parcellation to inspect its original node IDs.", "caption"
        )
        form.addRow(self.node_summary)
        self.node_list = QTableWidget(0, 4)
        self.node_list.setHorizontalHeaderLabels(["Parcel ID", "Label", "Voxels", "Present"])
        self.node_list.setAccessibleName("Validated original atlas nodes including missing parcels")
        self.node_list.setMaximumHeight(210)
        self.node_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        form.addRow(self.node_list)
        self.assignment = QComboBox()
        for text, key in [
            ("Radial endpoint search", "radial_search"),
            ("Endpoint voxel lookup", "end_voxels"),
            ("Reverse inward search", "reverse_search"),
            ("Forward outward search", "forward_search"),
        ]:
            self.assignment.addItem(text, key)
        form.addRow("Assignment strategy", self.assignment)
        self.radius = QDoubleSpinBox()
        self.radius.setRange(0.01, 100)
        self.radius.setValue(4)
        self.radius.setSuffix(" mm")
        form.addRow("Radial search radius", self.radius)
        self.search = QDoubleSpinBox()
        self.search.setRange(0, 200)
        self.search.setValue(4)
        self.search.setSuffix(" mm")
        form.addRow("Forward / reverse limit", self.search)
        self.symmetric = QCheckBox("Mirror off-diagonal edges on output")
        self.symmetric.setChecked(True)
        self.diagonal = QCheckBox("Zero diagonal on output (self assignments remain in QC)")
        self.diagonal.setChecked(True)
        advanced = QWidget()
        af = QFormLayout(advanced)
        af.addRow(self.symmetric)
        af.addRow(self.diagonal)
        self.threads = QSpinBox()
        self.threads.setRange(1, 64)
        self.threads.setValue(2)
        af.addRow("Threads", self.threads)
        self.advanced = Disclosure("Advanced matrix policies", advanced)
        self.output = QLineEdit()
        self.output.setPlaceholderText("New immutable connectome attempt directory")
        out = QWidget()
        ol = QHBoxLayout(out)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.addWidget(self.output, 1)
        ol.addWidget(button("Choose…", self.choose_output))
        form.addRow("Output", out)
        setup.addLayout(form)
        setup.addWidget(self.advanced)
        self.preview = button("Preview commands", self.prepare)
        self.approve = QCheckBox(
            "I reviewed these exact commands, source geometry and scientific limitations"
        )
        self.run_button = button("Build connectome", self.run)
        setup.addWidget(self.preview)
        setup.addWidget(self.approve)
        setup.addWidget(self.run_button)
        self.commands = QPlainTextEdit()
        self.commands.setReadOnly(True)
        self.command_details = Disclosure("Exact MRtrix commands", self.commands)
        setup.addWidget(self.command_details)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.logs = Disclosure("Execution log", self.log)
        setup.addWidget(self.logs)
        setup.addStretch()
        self.tabs.addTab(scroll, "Build connectome")
        inspect = QWidget()
        v = QVBoxLayout(inspect)
        bar = QHBoxLayout()
        self.open_button = button("Open result / CSV…", self.open_file)
        bar.addWidget(self.open_button)
        self.display_metric = QComboBox()
        bar.addWidget(self.display_metric)
        self.logscale = QCheckBox("Log display (counts or weight sums)")
        bar.addWidget(self.logscale)
        self.export_button = button("Export CSV…", self.export)
        bar.addWidget(self.export_button)
        self.compare_button = button("Compare…", self.compare)
        bar.addWidget(self.compare_button)
        v.addLayout(bar)
        self.matrix = MatrixView()
        v.addWidget(self.matrix, 1)
        action = QHBoxLayout()
        self.extract = button("Extract selected connection…", self.extract_selected)
        action.addWidget(self.extract)
        self.open_tracks = button("Open extracted tracks", self.open_extracted)
        action.addWidget(self.open_tracks)
        self.qc = button("Scientific QC", self.open_qc)
        action.addWidget(self.qc)
        self.runs = button("Run details", self.open_run)
        action.addWidget(self.runs)
        v.addLayout(action)
        self.tabs.addTab(inspect, "Inspect matrix")
        self.summary = label("No connectome has been generated.", "caption")
        root.addWidget(self.summary)
        for w in (
            self.tracts,
            self.atlas,
            self.reference,
            self.fa,
            self.weights,
            self.metric,
            self.assignment,
        ):
            w.currentIndexChanged.connect(self.invalidate)
        self.atlas.currentIndexChanged.connect(self.show_nodes)
        for w in (self.radius, self.search, self.threads):
            w.valueChanged.connect(self.invalidate)
        for w in (self.symmetric, self.diagonal):
            w.toggled.connect(self.invalidate)
        self.output.textChanged.connect(self.invalidate)
        self.approve.toggled.connect(self.update_actions)
        self.display_metric.currentIndexChanged.connect(self.show_matrix)
        self.logscale.toggled.connect(self.show_matrix)
        self.matrix.selected.connect(self.update_actions)
        self.update_actions()

    def refresh(self):
        for combo, kind in [
            (self.tracts, "tractogram"),
            (self.atlas, "atlas_labels"),
            (self.reference, "anatomy"),
            (self.fa, "anatomy"),
            (self.weights, "streamline_weights"),
        ]:
            selected = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Choose…", None)
            for a in self.owner.artifacts:
                if a.kind == kind:
                    combo.addItem(Path(a.payload["path"]).name + " · " + a.id[:8], a.id)
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.blockSignals(False)
        self.show_nodes()
        self.invalidate()

    def invalidate(self, *_):
        self.plan = None
        self.approve.setChecked(False)
        self.commands.clear()
        self.update_actions()

    def show_nodes(self, *_):
        a = next((a for a in self.owner.artifacts if a.id == self.atlas.currentData()), None)
        try:
            nodes = node_table(a) if a else []
        except ValueError as e:
            self.owner.status.setText(str(e))
            nodes = []
        self.node_list.setRowCount(len(nodes))
        for i, n in enumerate(nodes):
            for j, key in enumerate(("parcel_id", "label", "voxel_count", "nonempty")):
                self.node_list.setItem(i, j, QTableWidgetItem(str(n[key])))
        self.node_list.resizeColumnsToContents()
        self.node_list.horizontalHeader().setStretchLastSection(True)
        self.node_summary.setText(
            f"{sum(n['nonempty'] for n in nodes)} nonempty parcels · {len(nodes)} matrix rows · original IDs preserved"
            if nodes
            else "Select a valid registered parcellation."
        )

    def selected_task(self):
        metric = self.metric.currentData()
        metrics = (
            ["count", "mean_length", "mean_fa"]
            if metric == "all"
            else list(METRICS)
            if metric == "all_sift2"
            else [metric]
        )
        inputs = dict(
            tracts=self.tracts.currentData(),
            nodes=self.atlas.currentData(),
            reference=self.reference.currentData(),
        )
        if set(metrics) & {"mean_fa", "sift2_mean_fa"}:
            inputs["fa"] = self.fa.currentData()
        if any(METRICS[m].get("weighted") for m in metrics):
            inputs["weights"] = self.weights.currentData()
        if any(x is None for x in inputs.values()):
            raise ValueError(
                "Select all required scientific inputs; mean FA needs its identified compatible FA map"
            )
        p = dict(
            metrics=metrics,
            assignment=self.assignment.currentData(),
            symmetric="yes" if self.symmetric.isChecked() else "no",
            zero_diagonal="yes" if self.diagonal.isChecked() else "no",
        )
        if p["assignment"] == "radial_search":
            p["radial_search_mm"] = self.radius.value()
        elif p["assignment"] in ("forward_search", "reverse_search"):
            p["search_mm"] = self.search.value()
        return Task("connectome", "tck2connectome", inputs, p)

    def choose_output(self):
        parent = QFileDialog.getExistingDirectory(self, "Choose local parent for NEW attempt")
        if parent:
            self.output.setText(str(Path(parent) / "connectome-run-001"))

    def prepare(self):
        self.invalidate()
        try:
            if not self.output.text().strip():
                raise ValueError("Choose a new derivative directory")
            task = self.selected_task()
            self.owner.tasks = [t for t in self.owner.tasks if t.id != task.id] + [task]
            art = tuple(self.owner.artifacts)
            out = Path(self.output.text())
            threads = self.threads.value()
            directory = self.owner.tool_directory
        except (ValueError, TypeError) as e:
            self.owner.status.setText(str(e))
            return
        self.owner.start(
            "connectome-preview",
            lambda cancel, progress: prepare_connectome(
                art,
                (task,),
                out,
                nthreads=threads,
                tool_directory=directory,
                cancelled=cancel,
                progress=progress,
            ),
        )

    @staticmethod
    def presentation(path, cancelled=lambda: False):
        """Hash verification and potentially large CSV reads stay off the Qt thread."""
        bundle = load_bundle(path, cancelled=cancelled)
        if "weight_validity" in bundle:
            bundle["_weight_validity_display"] = read_matrix(bundle["weight_validity"])
            bundle["_weight_sum_display"] = read_matrix(bundle["weight_sum"])
        matrices = {
            m: read_matrix(e["path"], allow_invalid=True) for m, e in bundle["matrices"].items()
        }
        return Path(path), bundle, matrices, read_matrix(bundle["occupancy"])

    def accept_presentation(self, presentation):
        """Replace source identity, matrices and extraction state together."""
        self.bundle_path, self.bundle, self.matrix_cache, self.occupancy_cache = presentation
        self.matrix_paths = {m: e["path"] for m, e in self.bundle["matrices"].items()}
        self.matrix_hashes = {m: self.bundle["hashes"][p] for m, p in self.matrix_paths.items()}
        self.last_product = None
        self.nodes = self.bundle["nodes"]
        self.populate_metrics()
        self.tabs.setCurrentIndex(1)

    def run(self):
        if self.plan is None or not self.approve.isChecked():
            return
        plan = self.plan
        self.log.clear()
        self.summary.setText("Connectome processing in progress; scientific checks pending.")

        def execute(cancel, progress):
            result = execute_plan(
                None, plan, approval=plan.digest, cancelled=cancel, progress=progress
            )
            # Ephemeral display arrays are consumed by the GUI, not persisted in provenance.
            if result["status"] == "validated_pending_qc":
                for product in result["validation"]["products"]:
                    if product["kind"] == "connectome":
                        result["_presentation"] = self.presentation(Path(product["path"]), cancel)
            return result

        self.owner.start("connectome-run", execute)

    def failed(self, error, cancelled=False):
        self.summary.setText(
            "Cancelled; no valid result published." if cancelled else "Operation failed: " + error
        )
        self.approve.setChecked(False)

    def result(self, kind, result):
        if kind == "connectome-preview":
            self.plan = result
            is_edge = any(
                c["argv"][0].endswith("connectome2tck") for c in result.to_dict()["commands"]
            )
            self.run_button.setText("Extract connection" if is_edge else "Build connectome")
            self.commands.setPlainText(
                "\n\n".join(c["display"] for c in result.to_dict()["commands"])
            )
            reviews = [
                r.get("registration_eligibility")
                for r in result.to_dict().get("bindings", [])
                if r.get("registration_eligibility")
            ]
            self.commands.appendPlainText(
                "\nRegistration eligibility: " + json.dumps(reviews, indent=2)
            )
            self.owner.status.setText(
                "Commands ready for explicit review and approval. "
                + ", ".join(r["usage"] for r in reviews)
            )
            return
        if kind == "connectome-run":
            presentation = result.pop("_presentation", None)
            self.manifest = result
            self.plan = None
            self.approve.setChecked(False)
            if result["status"] != "validated_pending_qc":
                self.failed(result.get("error", result["status"]), result["status"] == "cancelled")
                return
            self.summary.setText(
                "Processing completed · numerical checks passed · QC findings require review"
            )
            self.owner.status.setText(
                "Open the matrix and review assignments, occupancy and scientific findings."
            )
            for product in result["validation"]["products"]:
                if product["kind"] == "connectome":
                    if presentation is not None:
                        self.accept_presentation(presentation)
                elif product["kind"] == "tractogram":
                    self.last_product = product
                    self.tabs.setCurrentIndex(1)
        elif kind == "connectome-open":
            self.accept_presentation(result)
            self.owner.status.setText("Connectome opened; scientific QC requires review.")
            self.summary.setText(
                f"{self.bundle['qc']['assigned_streamlines']}/{self.bundle['qc']['total_streamlines']} assigned · scientific review required"
            )
        elif kind == "connectome-csv":
            path, matrix, nodes, metric, digest = result
            self.bundle = None
            self.bundle_path = None
            self.nodes = nodes
            self.matrix_paths = {metric: str(path)}
            self.matrix_cache = {metric: matrix}
            self.matrix_hashes = {metric: digest}
            self.occupancy_cache = None
            self.last_product = None
            self.populate_metrics()
            self.tabs.setCurrentIndex(1)
            self.summary.setText(
                "External CSV: metric/node mapping declared by user; assignments and occupancy unavailable. Extraction disabled."
            )
        elif kind == "connectome-compare":
            other = result
            self.show_comparison(other)
        self.update_actions()

    def populate_metrics(self):
        self.display_metric.blockSignals(True)
        self.display_metric.clear()
        for m in self.matrix_paths:
            self.display_metric.addItem(METRICS.get(m, {}).get("name", m), m)
        self.display_metric.blockSignals(False)
        self.show_matrix()

    def show_matrix(self, *_):
        m = self.display_metric.currentData()
        if not m or m not in self.matrix_paths:
            return
        try:
            array = self.matrix_cache[m]
            occupancy = self.occupancy_cache
            self.comparing = False
            self.matrix.display(
                array,
                self.nodes,
                m,
                occupancy,
                title=Path(self.matrix_paths[m]).name,
                bundle=self.bundle,
                logarithmic=self.logscale.isChecked() and m in {"count", "sift2_weighted"},
            )
        except (ValueError, OSError) as e:
            self.owner.status.setText(str(e))
        self.update_actions()

    def open_bundle(self, path):
        self.owner.start(
            "connectome-open", lambda cancel, progress: self.presentation(path, cancel)
        )

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open connectome bundle or CSV", "", "Connectomes (*.json *.csv)"
        )
        if not path:
            return
        path = Path(path)
        if path.suffix == ".json":
            self.open_bundle(path)
            return
        a = next((a for a in self.owner.artifacts if a.id == self.atlas.currentData()), None)
        if not a:
            self.owner.status.setText("Select the associated atlas before opening an external CSV.")
            return
        # The explicit mapping dialog records a declaration, not historical proof.
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle("Associate CSV rows with atlas")
        layout = QVBoxLayout(dialog)
        metric = QComboBox()
        metric.addItem("Unknown metric", "unknown")
        for key, e in METRICS.items():
            metric.addItem(e["name"], key)
        layout.addWidget(metric)
        review = QCheckBox(
            "I reviewed that row/column i corresponds to original parcel ID i+1 in this atlas"
        )
        layout.addWidget(review)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        layout.addWidget(buttons)
        buttons.button(QDialogButtonBox.StandardButton.Open).setEnabled(False)
        review.toggled.connect(buttons.button(QDialogButtonBox.StandardButton.Open).setEnabled)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        m = metric.currentData()

        def read(cancel, progress):
            a.assert_current(cancel)
            nodes = node_table(a)
            digest = hash_file(path, cancel)
            array = read_matrix(path, allow_invalid=True)
            if hash_file(path, cancel) != digest:
                raise ValueError("CSV changed while being read; reopen the file")
            if array.shape != (len(nodes), len(nodes)):
                raise ValueError(
                    "CSV dimensions do not match maximum original parcel ID; no nodes renumbered"
                )
            return path, array, nodes, m, digest

        self.owner.start("connectome-csv", read)

    def extract_selected(self):
        if self.comparing or not self.bundle or not self.bundle_path:
            return
        r, c = self.matrix.cell
        if self.occupancy_cache[r, c] <= 0:
            return
        parent = QFileDialog.getExistingDirectory(
            self, "Choose parent for selected edge derivative"
        )
        if not parent:
            return
        from uuid import uuid4

        path = self.bundle_path
        pair = (r + 1, c + 1)
        out = Path(parent) / ("edge-" + uuid4().hex[:10])
        directory = self.owner.tool_directory

        def compile_edge(cancel, progress):
            artifact = import_artifact(
                ImportRequest("connectome_matrix", path), cancelled=cancel, progress=progress
            )
            return prepare_connectome(
                (artifact,),
                (
                    Task(
                        "edge",
                        "connectome2tck",
                        {"connectome": artifact.id},
                        {"node_a": pair[0], "node_b": pair[1]},
                    ),
                ),
                out,
                nthreads=2,
                tool_directory=directory,
                cancelled=cancel,
                progress=progress,
            )

        self.owner.start("connectome-preview", compile_edge)
        self.tabs.setCurrentIndex(0)

    def open_extracted(self):
        if self.last_product:
            self.view_requested.emit(self.last_product)

    def open_qc(self):
        if self.manifest and self.manifest["status"] == "validated_pending_qc":
            self.qc_requested.emit(
                Path(self.manifest["plan"]["output_directory"]) / "qc-catalog.json"
            )
        elif self.bundle_path and (self.bundle_path.parent / "qc-catalog.json").exists():
            self.qc_requested.emit(self.bundle_path.parent / "qc-catalog.json")

    def open_run(self):
        if self.manifest:
            self.run_requested.emit(
                Path(self.manifest["plan"]["output_directory"]) / "manifest.json"
            )

    def export(self):
        m = self.display_metric.currentData()
        if m not in self.matrix_paths:
            return
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Export unchanged scientific CSV (new file)",
            "connectome-" + m + ".csv",
            "CSV (*.csv)",
        )
        if not dest:
            return
        try:
            p = Path(dest)
            if p.exists() or Path(str(p) + ".json").exists():
                raise ValueError("Export exists; choose a new filename")
            source = Path(self.matrix_paths[m])
            digest = hash_file(source)
            if digest != self.matrix_hashes.get(m):
                raise ValueError("Displayed matrix source changed; reopen and verify before export")
            with p.open("xb") as f:
                with source.open("rb") as src:
                    shutil.copyfileobj(src, f)
            if hash_file(p) != digest:
                raise ValueError("Export hash verification failed")
            with Path(str(p) + ".json").open("x") as f:
                json.dump(
                    dict(
                        metric=m,
                        nodes=self.nodes,
                        source_sha256=digest,
                        source=str(source),
                        bundle=str(self.bundle_path) if self.bundle_path else None,
                        scientific_acceptance="unassigned",
                    ),
                    f,
                    indent=2,
                )
            self.owner.status.setText("Unchanged matrix CSV exported with node mapping sidecar.")
        except (ValueError, OSError) as e:
            self.owner.status.setText(str(e))

    def compare(self):
        if not self.bundle:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Compare compatible verified bundle", "", "Connectome (*.json)"
        )
        if path:
            self.owner.start(
                "connectome-compare",
                lambda cancel, progress: load_bundle(Path(path), cancelled=cancel),
            )

    def show_comparison(self, other):
        m = self.display_metric.currentData()
        if not self.bundle or m not in other["matrices"]:
            raise ValueError("Comparison requires the same known metric")
        if (
            self.bundle["nodes"] != other["nodes"]
            or self.bundle["assignment_parameters"] != other["assignment_parameters"]
        ):
            raise ValueError(
                "Atlas/node mapping or assignment/output policies differ; direct comparison blocked"
            )
        current = self.matrix_cache[m]
        comparison = read_matrix(other["matrices"][m]["path"], allow_invalid=True)
        self.comparing = True
        # Empty native means are undefined, even though their CSV value is zero.
        # Only edges occupied in both analyses have a defined mean difference.
        occupancy = None
        display_bundle = self.bundle
        if m != "count":
            occupancy = ((self.occupancy_cache > 0) & (read_matrix(other["occupancy"]) > 0)).astype(
                int
            )
        if m in {"sift2_mean_length", "sift2_mean_fa"}:
            # Both means must have positive total weight. Track occupancy alone
            # cannot establish that a weighted mean exists in either analysis.
            valid = (read_matrix(self.bundle["weight_validity"]) > 0) & (
                read_matrix(other["weight_validity"]) > 0
            )
            occupancy = ((occupancy > 0) & valid).astype(int)
            display_bundle = {**self.bundle, "_weight_validity_display": valid}
        self.matrix.display(
            current - comparison,
            self.nodes,
            m,
            occupancy,
            title="Current − comparison (display only)",
            bundle=display_bundle,
            difference=True,
        )
        self.extract.setEnabled(False)

    def update_actions(self, *_):
        idle = self.owner.thread is None
        for w in (
            self.tracts,
            self.atlas,
            self.reference,
            self.metric,
            self.output,
            self.assignment,
            self.symmetric,
            self.diagonal,
            self.threads,
            self.open_button,
        ):
            w.setEnabled(idle)
        self.preview.setEnabled(idle)
        self.approve.setEnabled(idle and self.plan is not None)
        self.run_button.setEnabled(idle and self.plan is not None and self.approve.isChecked())
        method = self.assignment.currentData()
        self.radius.setEnabled(idle and method == "radial_search")
        self.search.setEnabled(idle and method in ("forward_search", "reverse_search"))
        self.fa.setEnabled(
            idle and self.metric.currentData() in ("all", "all_sift2", "mean_fa", "sift2_mean_fa")
        )
        self.weights.setEnabled(
            idle
            and self.metric.currentData()
            in ("all_sift2", "sift2_weighted", "sift2_mean_length", "sift2_mean_fa")
        )
        self.extract.setToolTip(
            "Requires a verified source tractogram, its exact assignments, and a nonempty selected edge"
        )
        self.extract.setEnabled(
            idle
            and self.bundle is not None
            and not self.comparing
            and self.matrix.occupancy is not None
            and bool(self.matrix.occupancy[self.matrix.cell] > 0)
        )
        self.open_tracks.setEnabled(idle and self.last_product is not None)
        self.export_button.setEnabled(
            idle and self.matrix.matrix is not None and not self.comparing
        )
        self.compare_button.setEnabled(idle and self.bundle is not None)
        self.logscale.setEnabled(self.display_metric.currentData() in {"count", "sift2_weighted"})
