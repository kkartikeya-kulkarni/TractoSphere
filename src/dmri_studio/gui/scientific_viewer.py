"""Four-view scientific workspace reusing the application's native slice canvases.

The scene is independent of preprocessing ownership. Readers run in a worker;
VTK actors and Qt controls remain on the GUI thread. No processing is started.
"""

import json
import time
from pathlib import Path

import nibabel as nib
import numpy as np
from PySide6.QtCore import (
    QEvent,
    QObject,
    QRunnable,
    QSignalBlocker,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QListWidget,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.preview_geometry import plane_geometry
from dmri_studio.core.scene import ImageVolume, TractGroup, save_new_image
from dmri_studio.core.viewer_display import robust_range

from .atlas_tools import AtlasTools, RoiTree
from .components import button, label
from .fod_view import CoefficientBackground, FODTools
from .operation_state import OperationState, State
from .preview import SliceView
from .registration_view import RegistrationView
from .scene_renderer import SceneRenderer
from .viewer_tools import ViewerTools
from .workstation import EmptyState


class Result(QObject):
    done = Signal(object, object)


class ReadTask(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.result = Result()

    def run(self):
        try:
            value, error = self.function(), None
        except Exception as exc:
            value, error = None, str(exc)
        self.result.done.emit(value, error)


class ScientificViewer(RegistrationView, FODTools, AtlasTools, ViewerTools, QWidget):
    """Editable visualization derivatives, accurate affine navigation, no source writes."""

    image_loaded = Signal()
    editing_changed = Signal()
    workspace_mode_changed = Signal(str)
    inspector_visibility_changed = Signal(bool)
    filter_tracks_requested = Signal(object)

    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.workspace_mode = "image"
        self.mode_tabs = {"image": 0, "tractography": 1}
        self.mode_layouts = {"image": "Three orthogonal", "tractography": "Four views"}
        self.reference = None
        self.data = None
        self.scalar_map = None
        self.position = (0, 0, 0)
        self.groups = []
        self.rois = []
        self.surfaces = []
        self.roi_stroke = False
        self.roi_undo = []
        self.roi_redo = []
        self.tasks = set()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.volume_generation = 0
        self.scene_generation = 0
        self.image_state = OperationState(self)
        self.image_loading = False
        self.pending_tracking_product = None
        self.tracking_identity = None
        self.image_loaded.connect(self._tracking_background_ready)
        self.pending_restore = None
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        self.scene_toolbar = QWidget()
        toolbar = QHBoxLayout(self.scene_toolbar)
        toolbar.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.scene_toolbar)
        self.header_title = label("Image Viewer", "section")
        toolbar.addWidget(self.header_title)
        for title, callback in [
            ("Open MRI", self.choose_image),
            ("Add tracts", self.choose_tracts),
            ("Restore", self.open_workspace),
            ("Save", self.save_workspace),
            ("Export PNG", self.save_screenshot),
        ]:
            toolbar.addWidget(
                self.action(title, lambda _=False, f=callback: self.safe(f), quiet=True)
            )
        toolbar.addStretch()
        self.status = label(
            "Open a 3D/4D NIfTI image. TCK/TRK coordinates use RAS+ mm; matching space is required.",
            "caption",
        )
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.status)
        self.content_stack = QStackedWidget()
        root.addWidget(self.content_stack, 1)
        split = QSplitter()
        self.scene_split = split
        self.content_stack.addWidget(split)
        self.empty_state = EmptyState(
            "No image open", "Open a NIfTI image to begin.", [("Open Image…", self.choose_image)]
        )
        self.content_stack.addWidget(self.empty_state)
        self.layout_bar = QWidget()
        layout_row = QHBoxLayout(self.layout_bar)
        layout_row.setContentsMargins(0, 0, 0, 0)
        self.layout_choice = QComboBox()
        self.layout_choice.setAccessibleName("Viewport layout")
        self.layout_choice.addItems(
            ["Three orthogonal", "Four views", "Axial", "Sagittal", "Coronal", "3D scene"]
        )
        self.layout_choice.currentTextChanged.connect(self.apply_layout)
        layout_row.addWidget(self.layout_choice)
        self.camera_button = button("Camera")
        self.camera_button.setAccessibleName("3D camera controls")
        self.camera_menu = QMenu(self.camera_button)
        for preset in (
            "Isometric",
            "Anterior",
            "Posterior",
            "Left",
            "Right",
            "Superior",
            "Inferior",
        ):
            self.camera_menu.addAction(
                preset, lambda _=False, name=preset: self.safe(lambda: self.set_camera_preset(name))
            )
        self.camera_menu.addSeparator()
        self.camera_menu.addAction(
            "Fit scene",
            lambda: self.safe(lambda: self.set_camera_preset(self.camera.currentText())),
        )
        self.camera_menu.addAction("Toggle orthographic projection", lambda: self.parallel.toggle())
        self.camera_button.setMenu(self.camera_menu)
        layout_row.addWidget(self.camera_button)
        layout_row.addStretch()
        root.insertWidget(root.indexOf(self.content_stack), self.layout_bar)
        self.grid = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.grid)
        self.rows = [QSplitter(), QSplitter()]
        for row in self.rows:
            self.grid.addWidget(row)
            row.setChildrenCollapsible(False)
        self.views = {}
        for orientation, row in [("axial", 0), ("sagittal", 0), ("coronal", 1)]:
            view = SliceView(theme, orientation)
            self.views[orientation] = view
            self.rows[row].addWidget(view)
            view.slider.valueChanged.connect(
                lambda value, o=orientation: self.slice_changed(o, value)
            )
            view.canvas.installEventFilter(self)
            view.canvas.position_changed.connect(
                lambda pos, o=orientation: self.safe(lambda: self.navigate(pos, o))
            )
            view.canvas.stepped.connect(lambda s, v, o=orientation: self.step(o, s, v))
            view.canvas.range_changed.connect(self.range_changed)
            view.expand.clicked.connect(lambda _=False, o=orientation: self.expand(o))
        self.scene_host = QWidget()
        scene_layout = QVBoxLayout(self.scene_host)
        scene_layout.setContentsMargins(0, 0, 0, 0)
        heading = QHBoxLayout()
        self.scene_title = label("3D · RAS+ mm", "control")
        heading.addWidget(self.scene_title)
        heading.addStretch()
        heading.addWidget(self.action("Expand", lambda: self.expand("3d"), quiet=True))
        scene_layout.addLayout(heading)
        # QVTK installs its parent-destruction cleanup in its constructor;
        # adding an initially parentless widget to a layout is too late.
        self.scene = SceneRenderer(self)
        self.scene.pick_callback = self.select_tract
        self.scene.position_callback = lambda world: (
            self.navigate(np.rint(self.reference.voxels(world)).astype(int))
            if self.reference
            else None
        )
        scene_layout.addWidget(self.scene, 1)
        self.rows[1].addWidget(self.scene_host)
        self.expanded = None
        self.escape = QShortcut(QKeySequence("Escape"), self)
        self.escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape.activated.connect(lambda: self.expand(self.expanded) if self.expanded else None)
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(270)
        self.tabs.setMaximumWidth(460)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.setUsesScrollButtons(True)
        self.inspector = QWidget()
        inspector_layout = QVBoxLayout(self.inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(8)
        self.inspector_choice = QComboBox()
        self.inspector_choice.setAccessibleName("Inspector contents")
        for title, index in [
            ("Image display", 0),
            ("Tract groups", 1),
            ("Atlas & ROIs", 2),
            ("Anatomical surfaces", 3),
            ("Image overlays", 4),
        ]:
            self.inspector_choice.addItem(title, index)
        self.inspector_choice.currentIndexChanged.connect(
            lambda i: self.tabs.setCurrentIndex(self.inspector_choice.itemData(i))
        )
        self.tabs.currentChanged.connect(self.sync_inspector_choice)
        self.tabs.tabBar().hide()
        inspector_layout.addWidget(self.inspector_choice)
        inspector_layout.addWidget(self.tabs, 1)
        split.addWidget(self.inspector)
        split.setStretchFactor(0, 1)
        split.setSizes([900, 310])
        split.setChildrenCollapsible(False)
        for name in ["Display", "Tracts", "ROIs", "Surfaces"]:
            content = QWidget()
            content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            layout = QVBoxLayout(content)
            layout.setSpacing(8)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(content)
            self.tabs.addTab(scroll, name)
            setattr(self, name.lower() + "_layout", layout)
        self.setup_display()
        self.setup_tracts()
        self.setup_rois()
        self.setup_surfaces()
        self.footer = QWidget()
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.footer)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 0)
        self.volume.setTracking(False)
        self.volume.setAccessibleName("Diffusion volume")
        self.volume.valueChanged.connect(self.request_volume)
        footer.addWidget(label("Volume"))
        footer.addWidget(self.volume, 1)
        self.volume_info = label("No image", "caption")
        footer.addWidget(self.volume_info)
        self.coordinates = label("", "caption")
        root.addWidget(self.coordinates)
        self.setup_tools()
        self.setup_atlas_tools()
        from .viewer_inspector import organize_inspector

        organize_inspector(self)
        self.setup_registration_view()
        self.setup_fod()
        self.set_workspace_mode("image")
        self.update_content_state()

    def set_workspace_mode(self, mode):
        """Change presentation only; retain every loaded image/tract/ROI instance."""
        if mode not in {"image", "tractography"}:
            raise ValueError("Unknown scientific workspace mode")
        if hasattr(self, "tabs"):
            self.mode_tabs[self.workspace_mode] = self.tabs.currentIndex()
            self.mode_layouts[self.workspace_mode] = self.layout_choice.currentText()
        changed = mode != self.workspace_mode
        self.workspace_mode = mode
        self.header_title.setText("Image Viewer" if mode == "image" else "Tractography")
        self.tabs.setTabVisible(1, mode == "tractography")
        model = self.inspector_choice.model()
        model.item(1).setEnabled(mode == "tractography")
        selected = self.mode_tabs[mode]
        self.tabs.setCurrentIndex(selected if self.tabs.isTabVisible(selected) else 0)
        if changed:
            self.layout_choice.setCurrentText(self.mode_layouts[mode])
        self.apply_layout()
        if (
            changed
            and not self.tasks
            and not self.status.text().startswith(("Cannot complete action:", "Operation failed:"))
        ):
            if self.reference is not None:
                self.status.setText(
                    f"{self.reference.path.name} · volume {self.volume.value() + 1}/{self.reference.count} · source unchanged"
                    if mode == "image"
                    else f"{len(self.groups)} tract groups · {len(self.rois)} regions · Coordinate matching requires verified source provenance."
                )
        self.update_content_state()
        self.workspace_mode_changed.emit(mode)

    def set_inspector_visible(self, visible):
        self.inspector.setVisible(visible)
        self.inspector_visibility_changed.emit(visible)

    def sync_inspector_choice(self, index):
        self.tabs.setMinimumWidth(380 if index == 2 else 270)
        with QSignalBlocker(self.inspector_choice):
            self.inspector_choice.setCurrentIndex(self.inspector_choice.findData(index))

    def update_content_state(self):
        loaded = self.reference is not None and not self.image_loading
        self.status.setVisible(
            loaded
            or bool(self.tasks)
            or self.status.text().startswith(("Cannot complete action:", "Operation failed:"))
        )
        self.content_stack.setCurrentWidget(self.scene_split if loaded else self.empty_state)
        self.layout_bar.setVisible(loaded)
        self.footer.setVisible(loaded and self.reference.count > 1)
        self.coordinates.setVisible(loaded)
        if loaded and getattr(self, "_metadata_reference", None) is not self.reference:
            self._metadata_reference = self.reference
            header = self.reference.image.header
            affine_text = "\n".join(
                "  ".join(f"{x:.4g}" for x in row) for row in self.reference.affine
            )
            self.image_metadata.setText(
                f"{self.reference.path.name}\nDimensions: {' × '.join(map(str, self.reference.shape))}\nDatatype: {header.get_data_dtype()}\nSpatial units: {header.get_xyzt_units()[0]}\nAffine source: {self.reference.affine_source}\nqform code: {int(header['qform_code'])} · sform code: {int(header['sform_code'])}\n\nWorld affine\n{affine_text}"
            )
            self.image_metadata.setToolTip(str(self.reference.path))
        if not loaded and not self.tasks and self.image_state.state != State.FAILED:
            self.empty_state.title.setText("No image open")
            self.empty_state.description.setText("Open a NIfTI image to begin.")

    def apply_layout(self, *_):
        """Rearrange the same Qt viewports, never scientific image coordinates."""
        if not hasattr(self, "views") or not hasattr(self, "scene_host"):
            return
        self.expanded = None
        self.rows[0].insertWidget(0, self.views["axial"])
        self.rows[0].insertWidget(1, self.views["sagittal"])
        self.rows[1].insertWidget(0, self.views["coronal"])
        self.rows[1].insertWidget(1, self.scene_host)
        layout = self.layout_choice.currentText()
        self.grid.setOrientation(Qt.Orientation.Vertical)
        for row in self.rows:
            row.setOrientation(Qt.Orientation.Horizontal)
        if layout == "Three orthogonal":
            self.grid.setOrientation(Qt.Orientation.Horizontal)
            for row in self.rows:
                row.setOrientation(Qt.Orientation.Vertical)
            self.rows[1].insertWidget(0, self.views["sagittal"])
            self.rows[1].insertWidget(1, self.views["coronal"])
        for key, view in self.views.items():
            view.setVisible(layout in {"Three orthogonal", "Four views"} or layout.lower() == key)
        self.scene_host.setVisible(layout in {"Four views", "3D scene"})
        self.camera_button.setVisible(layout in {"Four views", "3D scene"})
        self.update_row_visibility()

    def set_camera_preset(self, preset):
        """The compact menu and inspector share the existing camera service/state."""
        with QSignalBlocker(self.camera):
            self.camera.setCurrentText(preset)
        self.scene.camera(preset)

    def update_row_visibility(self):
        for row in self.rows:
            row.setVisible(any(not row.widget(i).isHidden() for i in range(row.count())))

    def action(self, text, callback=None, **kwargs):
        """Every action checks prerequisites and reports failure in the visible status."""
        group_actions = {
            "Uniform group color",
            "Use base-image scalar colors",
            "Select all loaded in group",
            "Clear selection",
            "Hide selected",
            "Delete selected",
            "Cut selected at axial plane",
            "Show hidden",
            "Export retained loaded tracts",
            "Rename group",
            "Remove group from scene",
            "Export bundle statistics",
            "Cut selected at drag plane",
        }
        roi_actions = {
            "ROI color",
            "Build / update ROI in 3D",
            "Dilate",
            "Erode",
            "Save selected ROI as new NIfTI",
            "Finish polygon",
            "Export ROI statistics",
        }

        def invoke():
            if (
                text not in {"Open MRI", "Restore", "Expand", "Background color", "Cancel polygon"}
                and self.reference is None
            ):
                raise ValueError("Open MRI first, then add matching tracts or create/load an ROI")
            if text in group_actions and self.current_group() is None:
                raise ValueError("Add tracts and select a group in the Tracts list first")
            if text in roi_actions and self.roi_list.currentRow() < 0:
                raise ValueError(
                    "Load ROI NIfTI or create a New empty ROI, then select it in the list"
                )
            if callback is not None:
                callback()

        control = button(text, lambda _checked=False: self.safe(invoke), **kwargs)
        control.setAccessibleName(text or control.accessibleName())
        return control

    def safe(self, function):
        try:
            if self.status.text().startswith(("Cannot complete action:", "Operation failed:")):
                self.status.setText("Ready")
            if function is not None:
                function()
        except Exception as exc:
            self.status.setText(f"Cannot complete action: {exc}")

    def background(self, function, callback, *, current=None, on_error=None):
        generation = self.scene_generation
        if current is None:

            def current():
                """Ignore reader results belonging to a replaced scene."""
                return generation == self.scene_generation

        task = ReadTask(function)
        self.tasks.add(task)

        def done(value, error):
            self.tasks.discard(task)
            if not current():
                self.update_content_state()
                return
            if error:
                if on_error is not None:
                    on_error(error)
                    self.update_content_state()
                    return
                self.pending_restore = None
                self.status.setText("Operation failed: " + error)
            else:
                if on_error is not None:
                    try:
                        callback(value)
                    except Exception as exc:
                        on_error(str(exc))
                else:
                    self.safe(lambda: callback(value))
            self.update_content_state()

        task.result.done.connect(done)
        self.pool.start(task)

    def setup_display(self):
        layout = self.display_layout
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.addLayout(form)
        self.low = QDoubleSpinBox()
        self.high = QDoubleSpinBox()
        for box in (self.low, self.high):
            box.setKeyboardTracking(False)
            box.setRange(-1e12, 1e12)
            box.setDecimals(3)
            box.valueChanged.connect(self.update_contrast)
        form.addRow("Minimum", self.low)
        form.addRow("Maximum", self.high)
        layout.addWidget(self.action("Auto contrast", self.auto_contrast))
        self.colormap = QComboBox()
        self.colormap.addItems(["Gray", "Hot", "Viridis"])
        self.colormap.currentIndexChanged.connect(self.refresh_slices)
        form.addRow("2D colormap", self.colormap)
        self.camera = QComboBox()
        self.camera.addItems(
            ["Isometric", "Anterior", "Posterior", "Left", "Right", "Superior", "Inferior"]
        )
        self.camera.currentTextChanged.connect(self.scene.camera)
        form.addRow("Camera", self.camera)
        layout.addWidget(
            self.action("Fit / reset camera", lambda: self.scene.camera(self.camera.currentText()))
        )
        self.parallel = QCheckBox("Orthographic projection")
        self.parallel.toggled.connect(self.projection)
        layout.addWidget(self.parallel)
        self.slice_opacity = self.spin(0, 1, 1, 0.05)
        self.slice_opacity.valueChanged.connect(self.render_settings)
        form.addRow("Slice opacity", self.slice_opacity)
        self.plane_checks = []
        for _axis, title in enumerate(
            ["File-axis 0 plane", "File-axis 1 plane", "File-axis 2 plane"]
        ):
            check = QCheckBox(title)
            check.setChecked(True)
            check.toggled.connect(self.render_settings)
            self.plane_checks.append(check)
            layout.addWidget(check)
        self.light = self.spin(0, 1, 0.2, 0.05)
        self.light.valueChanged.connect(self.render_settings)
        form.addRow("Surface ambient light", self.light)
        self.clip = QCheckBox("Clip objects at current axial plane")
        self.clip.toggled.connect(self.render_settings)
        layout.addWidget(self.clip)
        self.quality = QComboBox()
        self.quality.addItems(["Interactive transparency", "High-quality transparency"])
        self.quality.currentIndexChanged.connect(self.render_settings)
        layout.addWidget(self.quality)
        self.aa = QCheckBox("FXAA edge smoothing")
        self.aa.toggled.connect(self.render_settings)
        layout.addWidget(self.aa)
        layout.addWidget(self.action("Background color", self.background_color))
        layout.addWidget(
            label(
                "Slices: left drag crosshair · right drag contrast\n3D: left rotate · middle pan · wheel zoom\nCmd/Ctrl-click a tract to select it.",
                "caption",
            )
        )
        layout.addStretch()

    @staticmethod
    def spin(low, high, value, step):
        box = QDoubleSpinBox()
        box.setRange(low, high)
        box.setKeyboardTracking(False)
        box.setValue(value)
        box.setSingleStep(step)
        return box

    def setup_tracts(self):
        layout = self.tracts_layout
        self.tract_list = QListWidget()
        self.tract_list.currentRowChanged.connect(self.tract_info)
        self.tract_list.setMaximumHeight(150)
        layout.addWidget(self.tract_list)
        self.tract_visible = QCheckBox("Selected group visible")
        self.tract_visible.setChecked(True)
        self.tract_visible.toggled.connect(self.group_visibility)
        layout.addWidget(self.tract_visible)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.addLayout(form)
        self.mode = QComboBox()
        self.mode.addItems(["Lines", "Tubes"])
        self.coloring = QComboBox()
        self.coloring.addItems(
            ["Direction RGB", "Uniform", "Length", "Image scalar", "SIFT2 weight"]
        )
        self.width = self.spin(0.1, 8, 1, 0.1)
        self.opacity = self.spin(0, 1, 1, 0.05)
        self.density = QSpinBox()
        self.density.setRange(1, 1000000)
        self.density.setKeyboardTracking(False)
        self.density.setValue(10000)
        self.density.setToolTip(
            "Number of fibers drawn per group, not a file or processing limit. "
            "The complete source remains indexed for filtering and export. "
            "Drawing more fibers can make the 3D view slow or exhaust graphics memory."
        )
        for name, control in [
            ("Render", self.mode),
            ("Color", self.coloring),
            ("Width px / tube mm", self.width),
            ("Opacity", self.opacity),
            ("Display cap / group", self.density),
        ]:
            form.addRow(name, control)
            (
                control.currentIndexChanged
                if isinstance(control, QComboBox)
                else control.valueChanged
            ).connect(self.refresh_tracts)
        layout.addWidget(self.action("Uniform group color", self.group_color))
        layout.addWidget(
            self.action("Load same-grid scalar color map", lambda: self.safe(self.choose_scalar))
        )
        layout.addWidget(self.action("Use base-image scalar colors", self.clear_scalar))
        self.scalar_label = label("Scalar source: displayed image", "caption")
        self.scalar_label.setWordWrap(True)
        layout.addWidget(self.scalar_label)
        layout.addWidget(
            self.action(
                "Load exact-source SIFT2 weight record", lambda: self.safe(self.choose_weights)
            )
        )
        self.weight_legend = label("SIFT2 factors: none attached", "caption")
        self.weight_legend.setAccessibleName(
            "SIFT2 factor color legend and selected source streamline weight"
        )
        self.weight_legend.setWordWrap(True)
        layout.addWidget(self.weight_legend)
        layout.addWidget(
            self.action(
                "Select tracts intersecting selected ROI", lambda: self.safe(self.select_by_roi)
            )
        )
        layout.addWidget(self.action("Select all loaded in group", lambda: self.select_all(True)))
        layout.addWidget(self.action("Clear selection", lambda: self.select_all(False)))
        self.filter_tracks_action = self.action("Filter These Tracks…", self.request_filter_tracks)
        self.filter_tracks_action.setAccessibleName(
            "Filter the selected Viewer tract group using its complete source file"
        )
        layout.addWidget(self.filter_tracks_action)
        for name, operation in [
            ("Hide selected", "hide"),
            ("Delete selected", "delete"),
            ("Cut selected at axial plane", "cut"),
        ]:
            layout.addWidget(
                self.action(
                    name, lambda _=False, op=operation: self.safe(lambda: self.edit_tracts(op))
                )
            )
        layout.addWidget(self.action("Show hidden", self.show_hidden))
        row = QHBoxLayout()
        layout.addLayout(row)
        row.addWidget(self.action("Undo", lambda: self.tract_undo(False)))
        row.addWidget(self.action("Redo", lambda: self.tract_undo(True)))
        layout.addWidget(
            self.action("Export retained loaded tracts", lambda: self.safe(self.export_tracts))
        )
        self.stats = label("", "caption")
        self.stats.setWordWrap(True)
        layout.addWidget(self.stats)
        layout.addWidget(
            label(
                "The whole source file is indexed without loading all coordinates into RAM. "
                "Display cap controls only how many fibers are drawn; full-source filtering "
                "and export keep the remaining fibers. Originals are unchanged.",
                "caption",
            )
        )
        layout.addStretch()

    def setup_rois(self):
        layout = self.rois_layout
        self.roi_list = RoiTree(self.rois)
        self.roi_list.setMinimumHeight(190)
        self.roi_list.setMaximumHeight(290)
        self.roi_list.currentRowChanged.connect(self.roi_info)
        layout.addWidget(self.roi_list)
        layout.addWidget(self.action("Load ROI NIfTI", lambda: self.safe(self.choose_roi)))
        layout.addWidget(
            self.action("New empty ROI in image grid", lambda: self.safe(self.new_roi))
        )
        self.roi_visible = QCheckBox("Selected ROI visible")
        self.roi_visible.setChecked(True)
        self.roi_visible.toggled.connect(self.roi_visibility)
        layout.addWidget(self.roi_visible)
        self.brush = QComboBox()
        self.brush.addItems(["Navigate", "Draw", "Erase"])
        layout.addWidget(self.brush)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.addLayout(form)
        self.radius = QSpinBox()
        self.radius.setRange(1, 20)
        self.radius.setValue(2)
        form.addRow("Brush radius (voxels)", self.radius)
        self.roi_opacity = self.spin(0, 1, 0.3, 0.05)
        form.addRow("ROI opacity", self.roi_opacity)
        self.roi_opacity.valueChanged.connect(self.update_roi_style)
        layout.addWidget(self.action("ROI color", self.roi_color))
        layout.addWidget(
            self.action("Build / update ROI in 3D", lambda: self.safe(self.roi_surface))
        )
        for name in ["Dilate", "Erode", "Merge visible into new ROI"]:
            layout.addWidget(
                self.action(name, lambda _=False, n=name: self.safe(lambda: self.morph_roi(n)))
            )
        row = QHBoxLayout()
        layout.addLayout(row)
        row.addWidget(self.action("Undo", lambda: self.undo_roi(False)))
        row.addWidget(self.action("Redo", lambda: self.undo_roi(True)))
        layout.addWidget(
            self.action("Save selected ROI as new NIfTI", lambda: self.safe(self.save_roi))
        )
        self.roi_stats = label("", "caption")
        self.roi_stats.setWordWrap(True)
        layout.addWidget(self.roi_stats)
        layout.addWidget(
            label(
                "ROI overlays use the image grid. Different grids require explicit same-space confirmation. Edits affect derivatives only.",
                "caption",
            )
        )
        layout.addStretch()

    def setup_surfaces(self):
        layout = self.surfaces_layout
        layout.addWidget(
            label(
                "Generate an isosurface from a matching-space mask, segmentation label or scalar image. These are not cortical reconstructions.",
                "caption",
            )
        )
        self.surface_threshold = self.spin(-1e6, 1e6, 0.5, 0.5)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.addLayout(form)
        form.addRow("Isovalue", self.surface_threshold)
        self.surface_label = QSpinBox()
        self.surface_label.setRange(0, 100000)
        form.addRow("Label ID (0 = threshold)", self.surface_label)
        layout.addWidget(
            self.action("Add surface from NIfTI", lambda: self.safe(self.choose_surface))
        )
        self.surface_list = QListWidget()
        self.surface_list.currentRowChanged.connect(self.surface_selection)
        layout.addWidget(self.surface_list)
        self.surface_opacity = self.spin(0, 1, 0.2, 0.05)
        form.addRow("Opacity", self.surface_opacity)
        self.surface_opacity.valueChanged.connect(self.surface_style)
        self.surface_visible = QCheckBox("Selected surface visible")
        self.surface_visible.setChecked(True)
        self.surface_visible.toggled.connect(self.surface_style)
        layout.addWidget(self.surface_visible)
        layout.addWidget(self.action("Surface color", self.surface_color))
        layout.addStretch()

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open scalar 3D/4D image", "", "NIfTI (*.nii *.nii.gz)"
        )
        if path:
            self.load_image(path)

    def load_image(self, path, *, direction_path=None, expected_hashes=None):
        self.roi_surface_queue.clear()
        self.pending_tracking_product = None
        self.tracking_identity = None
        self.cancel_fod()
        self.scene_generation += 1
        token = self.image_state.begin(State.LOADING, "Opening image · " + Path(path).name)
        self.image_loading = True
        self.status.setText(self.image_state.message)
        self.update_content_state()
        if self.image_loading:
            self.empty_state.title.setText("Opening image…")
            self.empty_state.description.setText(
                "Reading the requested image. The source file is unchanged."
            )
        self.volume_generation += 1
        generation = self.volume_generation

        def read():
            from dmri_studio.core.execution import hash_file

            allowed = {str(Path(p).resolve()) for p in (path, direction_path) if p}
            checks = {
                str(Path(p).resolve()): digest for p, digest in (expected_hashes or {}).items()
            }
            if set(checks) - allowed:
                raise ValueError(
                    "Result verification must reference only the selected image/vector"
                )
            for name, digest in checks.items():
                if hash_file(Path(name)) != digest:
                    raise ValueError(
                        "Verified DTI result changed; reload run evidence before viewing"
                    )
            ref = ImageVolume(Path(path))
            if ref.image.header.get_intent()[0] == "vector":
                raise ValueError(
                    "This is a vector image, not a scalar volume series. Open V1 with its verified FA map from Diffusion Models."
                )
            direction = None
            if direction_path is not None:
                vector = nib.load(Path(direction_path), mmap=True)
                units = vector.header.get_xyzt_units()[0]
                factor = {"mm": 1, "meter": 1000, "micron": 0.001}.get(units)
                if factor is None:
                    raise ValueError("V1 requires documented physical units")
                affine = vector.affine.copy()
                affine[:3] *= factor
                if vector.shape not in {
                    (*ref.shape[:3], 1, 3),
                    (*ref.shape[:3], 3),
                } or not np.allclose(affine, ref.affine, atol=1e-5, rtol=0):
                    raise ValueError("V1/FA geometry mismatch; no resampling is performed")
                # Canonical NIfTI vectors use the fifth axis. The explicit typed
                # path also reads preserved early development 4D exports; neither
                # representation is ever treated as a scalar time series.
                direction = np.asarray(
                    vector.dataobj[:, :, :, 0, :] if len(vector.shape) == 5 else vector.dataobj,
                    dtype=np.float32,
                )
                if not np.isfinite(direction).all():
                    raise ValueError("V1 contains nonfinite components")
            volume = ref.volume(0)
            for name, digest in checks.items():
                if hash_file(Path(name)) != digest:
                    raise ValueError("DTI result changed during viewing preparation")
            return ref, volume, direction

        def ready(result):
            if generation != self.volume_generation:
                return
            self.reference, self.data, self.dti_direction = result
            self.image_loading = False
            self.dti_direction_path = str(direction_path) if direction_path else None
            self.dti_source_hashes = expected_hashes or {}
            self.registration_layer = None
            self.registration_bundle = None
            self.registration_timer.stop()
            self.registration_blink.setChecked(False)
            self.registration_regions.clear()
            self.layers.clear()
            self.layer_list.clear()
            self.cancel_polygon()
            if hasattr(self, "_roi_dirty"):
                self._roi_dirty.clear()
                self._roi_surface_timer.stop()
            self.scalar_map = None
            self.scalar_label.setText("Scalar source: displayed image")
            self.scene_title.setText(
                "3D · RAS+ mm"
                if self.reference.physical_units_known
                else "3D · spatial units unspecified"
            )
            # A new space clears all scene objects instead of silently reusing them.
            self.groups.clear()
            self.rois.clear()
            self.surfaces.clear()
            self.roi_undo.clear()
            self.roi_redo.clear()
            for key in list(self.scene.actors):
                self.scene.remove(key)
            self.tract_list.clear()
            self.roi_list.clear()
            self.surface_list.clear()
            self.position = tuple(n // 2 for n in self.reference.shape[:3])
            self.volume.blockSignals(True)
            self.volume.setRange(0, self.reference.count - 1)
            self.volume.setValue(0)
            self.volume.blockSignals(False)
            for check in self.plane_checks:
                check.setChecked(True)
            self.auto_contrast()
            self.refresh_volume()
            self.scene.camera("Isometric")
            self.fod_reference_changed()
            self.status.setText(
                f"{self.reference.path.name} · {self.reference.shape} · {self.reference.affine_source} · source unchanged"
            )
            if direction_path is not None:
                self.status.setText(
                    "V1 world-RAS RGB × FA brightness · 2D direction display; 3D planes show FA · sources unchanged"
                )
            if self.pending_restore:
                restore = self.pending_restore
                self.pending_restore = None
                self.restore_objects(restore)
            else:
                from dmri_studio.core.atlas import find_table, stem

                metadata = find_table(path)
                name = stem(path).lower()
                recognized = any(word in name for word in ("atlas", "dseg", "labels", "maxprob"))
                if metadata and metadata.suffix.lower() == ".xml":
                    recognized = True
                if recognized:
                    self.load_atlas(path, same_space=True)
            self.image_state.update(token, State.READY, self.status.text())
            self.image_loaded.emit()

        def failed(error):
            self.image_loading = False
            self.pending_restore = None
            retained = (
                f" Previous image retained: {self.reference.path.name}." if self.reference else ""
            )
            self.status.setText("Operation failed: " + error + retained)
            self.image_state.update(token, State.FAILED, self.status.text())
            self.empty_state.title.setText("Could not open image")
            self.empty_state.description.setText(error + " Choose another image or check the file.")

        self.background(
            read, ready, current=lambda: generation == self.volume_generation, on_error=failed
        )

    def request_volume(self, index):
        if self.reference is None or self.image_loading:
            return
        self.volume_generation += 1
        generation = self.volume_generation
        ref = self.reference
        token = self.image_state.begin(State.LOADING, f"Reading volume {index + 1} / {ref.count}…")
        self.status.setText(self.image_state.message)

        def ready(data):
            if generation != self.volume_generation:
                return
            self.data = data
            self.refresh_volume()
            self.status.setText(
                f"{ref.path.name} · volume {index + 1}/{ref.count} · source unchanged"
            )

            self.image_state.update(token, State.READY, self.status.text())

        def failed(error):
            self.status.setText(
                f"Operation failed: volume {index + 1} · {error}. Previously rendered volume retained."
            )
            self.image_state.update(token, State.FAILED, self.status.text())

        self.background(
            lambda: ref.volume(index),
            ready,
            current=lambda: generation == self.volume_generation,
            on_error=failed,
        )

    def refresh_volume(self):
        self.scene.set_volume(
            self.data, self.reference, self.position, self.low.value(), self.high.value()
        )
        self.refresh_slices()
        self.render_settings()
        self.update_volume_render()
        self.refresh_tracts()
        self.volume_info.setText(f"{self.volume.value() + 1} / {self.reference.count}")
        # Display gradients only when matching sidecars are actually present.
        base = str(self.reference.path).removesuffix(".gz").removesuffix(".nii")
        try:
            b = np.loadtxt(base + ".bval").reshape(-1)
            v = np.loadtxt(base + ".bvec")
            if v.shape == (3, len(b)):
                v = v.T
            if len(b) == self.reference.count and v.shape == (len(b), 3):
                i = self.volume.value()
                self.volume_info.setText(
                    f"{i + 1}/{len(b)} · b={b[i]:g} · g=({v[i, 0]:.2f}, {v[i, 1]:.2f}, {v[i, 2]:.2f})"
                )
        except (OSError, ValueError):
            pass

    def auto_contrast(self):
        if self.data is not None:
            lo, hi = robust_range(self.data)
            self.range_changed(lo, hi)

    def range_changed(self, low, high):
        """Preserve diffusivity-scale display limits instead of rounding to 0.001.

        Only display controls change precision/step; image values, interpolation,
        affine and scientific defaults remain unchanged.
        """
        self.low.blockSignals(True)
        self.high.blockSignals(True)
        decimals = 8 if max(abs(low), abs(high)) < 0.1 else 3
        span = high - low
        step = max(10.0 ** (np.floor(np.log10(span)) - 2), 10.0**-decimals) if span > 0 else 1
        for box in (self.low, self.high):
            box.setDecimals(decimals)
            box.setSingleStep(step)
        self.low.setValue(low)
        self.high.setValue(high)
        self.low.blockSignals(False)
        self.high.blockSignals(False)
        self.update_contrast()

    def update_contrast(self):
        if self.high.value() <= self.low.value():
            return
        self.refresh_slices()
        for actor in self.scene.planes:
            actor.GetProperty().SetColorWindow(self.high.value() - self.low.value())
            actor.GetProperty().SetColorLevel((self.high.value() + self.low.value()) / 2)
        self.scene.draw()
        if hasattr(self, "volume_render_check"):
            self.update_volume_render()

    def refresh_slices(self, *_):
        if self.data is None:
            return
        for orientation, view in self.views.items():
            g = plane_geometry(self.reference, orientation)
            view.geometry = g
            view.canvas.configure(g, self.position)
            view.canvas.display_range = (self.low.value(), self.high.value())
            plane = np.take(self.data, self.position[g.axis], axis=g.axis)
            view.canvas.set_plane(plane)
            if self.colormap.currentIndex():
                from dmri_studio.core.viewer_display import colors

                values = np.clip(
                    (g.display_plane(plane) - self.low.value())
                    / max(self.high.value() - self.low.value(), 1e-9),
                    0,
                    1,
                )
                rgb = colors(values, "hot" if self.colormap.currentIndex() == 1 else "viridis")
                rgba = np.ascontiguousarray(
                    np.dstack((rgb, np.full(values.shape, 255, dtype=np.uint8)))
                )
                view.canvas.image = QImage(
                    rgba.data,
                    rgba.shape[1],
                    rgba.shape[0],
                    rgba.strides[0],
                    QImage.Format.Format_RGBA8888,
                ).copy()
            if getattr(self, "dti_direction", None) is not None:
                # Display-only DEC: world RAS directions map to red/green/blue;
                # FA modulates brightness. Scientific vector and FA files remain unchanged.
                directions = np.take(self.dti_direction, self.position[g.axis], axis=g.axis)
                components = np.stack(
                    [g.display_plane(directions[..., i]) for i in range(3)], axis=-1
                )
                brightness = np.clip(g.display_plane(plane), 0, 1)
                rgb = (np.clip(np.abs(components) * brightness[..., None], 0, 1) * 255).astype(
                    np.uint8
                )
                dec = np.ascontiguousarray(
                    np.dstack((rgb, np.full(brightness.shape, 255, dtype=np.uint8)))
                )
                view.canvas.image = QImage(
                    dec.data,
                    dec.shape[1],
                    dec.shape[0],
                    dec.strides[0],
                    QImage.Format.Format_RGBA8888,
                ).copy()
            rgba = np.zeros((*g.display_plane(plane).shape, 4), dtype=np.uint8)

            def composite(rgb, alpha, rgba=rgba):
                old = rgba[..., 3:4].astype(float) / 255
                alpha = np.broadcast_to(np.asarray(alpha)[..., None], old.shape)
                out = alpha + old * (1 - alpha)
                color = (rgb * alpha + rgba[..., :3] * old * (1 - alpha)) / np.maximum(out, 1e-12)
                rgba[..., :3] = np.clip(color, 0, 255).astype(np.uint8)
                rgba[..., 3] = np.clip(out[..., 0] * 255, 0, 255).astype(np.uint8)

            for layer in self.layers:
                if not layer["visible"]:
                    continue
                from dmri_studio.core.viewer_display import colors

                values = g.display_plane(np.take(layer["data"], self.position[g.axis], axis=g.axis))
                scaled = np.clip(
                    (values - layer["low"]) / max(layer["high"] - layer["low"], 1e-12), 0, 1
                )
                rgb = colors(np.nan_to_num(scaled), layer["cmap"])
                alpha = np.where(np.isfinite(values) & (values != 0), layer["opacity"], 0)
                if layer.get("registration"):
                    mode = self.registration_mode.currentText()
                    if mode == "Reference only":
                        continue
                    if layer.get("categorical"):
                        from dmri_studio.core.registration_qc import label_rgba

                        atlas_pixels = label_rgba(
                            values,
                            layer["opacity"],
                            self.registration_visible_ids(),
                            mode == "Outlines",
                        )
                        rgb, alpha = atlas_pixels[..., :3], atlas_pixels[..., 3] / 255.0
                        current_id = int(layer["data"][self.position])
                        self.registration_info.setText(
                            f"Registered atlas · {self.registration_region_name(current_id)} at crosshair · review: {self.registration_review_state}"
                        )
                    elif mode == "Outlines":
                        edges = np.hypot(*np.gradient(scaled))
                        alpha = np.where(edges > 0.08, layer["opacity"], 0)
                    if mode == "Checkerboard":
                        yy, xx = np.indices(values.shape)
                        alpha = np.where(((xx // 12 + yy // 12) % 2) == 0, alpha, 0)
                    if mode == "Registered only":
                        alpha = np.ones(values.shape)
                composite(rgb, alpha)
            for roi in self.rois:
                if not roi["visible"]:
                    continue
                mask = g.display_plane(np.take(roi["data"], self.position[g.axis], axis=g.axis)) > 0
                composite(np.array(roi["color"]) * 255, np.where(mask, roi["opacity"], 0))
            view.canvas.overlay_image = QImage(
                rgba.data,
                rgba.shape[1],
                rgba.shape[0],
                rgba.strides[0],
                QImage.Format.Format_RGBA8888,
            ).copy()
            view.canvas.update()
            view.slider.blockSignals(True)
            view.slider.setRange(0, self.data.shape[g.axis] - 1)
            view.slider.setValue(self.position[g.axis])
            view.slider.blockSignals(False)
            view.slice_label.setText(
                f"{self.position[g.axis] + 1} / {self.data.shape[g.axis]} · axis {g.axis}"
            )
        world = self.reference.world(self.position)
        unit = "mm" if self.reference.physical_units_known else "units unspecified"
        self.coordinates.setText(
            f"Voxel {self.position}  ·  RAS+ {unit} ({world[0]:.2f}, {world[1]:.2f}, {world[2]:.2f})  ·  Intensity {self.data[self.position]:.5g}"
        )

    def navigate(self, position, orientation=None):
        if self.reference is None:
            return
        proposed = tuple(
            int(np.clip(p, 0, s - 1)) for p, s in zip(position, self.data.shape, strict=False)
        )
        if orientation and self.brush.currentText() != "Navigate":
            if self.roi_list.currentRow() < 0:
                raise ValueError(
                    "Create or load an ROI before drawing; choose Navigate to move the cursor"
                )
            if self.brush.currentText() == "Polygon" and self.polygon_plane is not None:
                axis = self.views[orientation].geometry.axis
                if (axis, proposed[axis]) != self.polygon_plane:
                    raise ValueError("Finish or cancel the polygon before drawing in another plane")
        self.position = proposed
        if (
            orientation
            and self.brush.currentText() != "Navigate"
            and self.roi_list.currentRow() >= 0
        ):
            i = self.roi_list.currentRow()
            roi = self.rois[i]
            if not self.roi_stroke:
                self.roi_checkpoint(i)
            axis = self.views[orientation].geometry.axis
            if self.brush.currentText() == "Polygon":
                plane = (axis, self.position[axis])
                if self.polygon_plane is not None and plane != self.polygon_plane:
                    raise ValueError("Finish or cancel the polygon before changing planes")
                self.polygon_plane = plane
                if not self.polygon_vertices or self.polygon_vertices[-1] != self.position:
                    self.polygon_vertices.append(self.position)
                self.status.setText(
                    f"Polygon: {len(self.polygon_vertices)} vertices; click Finish polygon to fill"
                )
            else:
                from dmri_studio.core.scene_operations import paint_roi

                paint_roi(
                    roi["data"],
                    self.position,
                    axis,
                    self.radius.value(),
                    self.brush.currentText().startswith("Draw"),
                    "3D" in self.brush.currentText(),
                )
            roi["edited"] = True
            self.roi_info(i)
            self.schedule_roi_surface(i)
        self.refresh_slices()
        self.scene.move_planes(self.position)
        self.render_settings()
        self.schedule_fod()

    def eventFilter(self, obj, event):
        if obj in [v.canvas for v in self.views.values()]:
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                i = self.roi_list.currentRow()
                if self.brush.currentText() != "Navigate" and i >= 0:
                    self.roi_checkpoint(i)
                    self.roi_stroke = True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self.roi_stroke = False
        return super().eventFilter(obj, event)

    def schedule_roi_surface(self, index):
        """Debounce surface rebuilding; a 2D edit must not leave stale 3D anatomy."""
        if not hasattr(self, "_roi_surface_timer"):
            self._roi_surface_timer = QTimer(self)
            self._roi_surface_timer.setSingleShot(True)
            self._roi_dirty = set()
            self._roi_surface_timer.timeout.connect(self.rebuild_dirty_rois)
        if self.rois[index].get("surface_built"):
            self._roi_dirty.add(index)
            self._roi_surface_timer.start(200)

    def rebuild_dirty_rois(self):
        for i in self._roi_dirty:
            if i < len(self.rois):
                roi = self.rois[i]
                self.scene.surface(
                    ("roi", i),
                    roi["data"],
                    self.reference.affine,
                    0.5,
                    roi["color"],
                    roi["opacity"],
                )
                self.scene.actors[("roi", i)].SetVisibility(roi["visible"])
        self._roi_dirty.clear()
        self.render_settings()

    def slice_changed(self, orientation, index):
        if self.reference is None:
            return
        p = list(self.position)
        p[self.views[orientation].geometry.axis] = index
        self.navigate(p)

    def step(self, orientation, slices, volumes):
        if self.reference is None:
            return
        self.views[orientation].slider.setValue(self.views[orientation].slider.value() + slices)
        self.volume.setValue(self.volume.value() + volumes)

    def expand(self, name):
        self.expanded = None if self.expanded == name else name
        if self.expanded is None:
            self.apply_layout()
            return
        for key, view in self.views.items():
            view.setVisible(key == self.expanded)
        self.scene_host.setVisible(self.expanded == "3d")
        self.update_row_visibility()

    def choose_tracts(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add matching-space tract groups", "", "Tractography (*.tck *.trk)"
        )
        for path in paths:
            self.load_tracts(path)

    def load_tracking_product(self, product):
        """Open a verified generated reference and native RAS tractogram together."""
        reference = product.get("reference")
        identity = (product.get("subject_id"), product.get("space_id"))
        if (
            reference
            and self.reference is not None
            and all(identity)
            and identity == self.tracking_identity
        ):
            current_reference = self.reference

            def check():
                from dmri_studio.core._mif_checks import voxel_mapping
                from dmri_studio.core.execution import hash_file

                if hash_file(Path(reference)) != product["reference_sha256"]:
                    raise ValueError("Tracking reference changed; reload verified evidence")
                candidate = ImageVolume(Path(reference))
                voxel_mapping(
                    candidate.affine,
                    candidate.shape,
                    current_reference.affine,
                    current_reference.shape,
                )
                return product

            self.background(check, self._load_tracking_scene)
            return
        if reference:
            self.load_image(
                Path(reference), expected_hashes={reference: product["reference_sha256"]}
            )
            self.pending_tracking_product = (self.volume_generation, dict(product))
        elif self.reference is not None:
            self._load_tracking_scene(product)
        else:
            raise ValueError("Open the matching reference image before this historical tractogram")

    def _tracking_background_ready(self):
        pending, self.pending_tracking_product = self.pending_tracking_product, None
        if pending is not None and pending[0] == self.volume_generation:
            product = pending[1]
            self.tracking_identity = (product.get("subject_id"), product.get("space_id"))
            self.safe(lambda: self._load_tracking_scene(product))

    def _load_tracking_scene(self, product):
        """Existing tracks/ROI tools, with immutable selected-parcel derivatives."""
        self.load_tracts(
            Path(product["path"]),
            expected_hash=product["sha256"],
            weight_record=product.get("weight_record"),
        )
        regions = product.get("selected_regions", ())
        if not regions:
            return
        reference = self.reference

        def read():
            from dmri_studio.core.atlas import geometry_record
            from dmri_studio.core.execution import hash_file
            from dmri_studio.core.scientific.dti import _aligned

            result = []
            for region in regions:
                path = Path(region["path"])
                if hash_file(path) != region["sha256"]:
                    raise ValueError("Selected connection ROI changed; reopen verified evidence")
                result.append((region, _aligned(path, geometry_record(reference))))
            return result

        def ready(result):
            if self.reference is not reference:
                return
            for region, array in result:
                self.add_roi(
                    array != 0, f"Node {region['parcel_id']} · {region['label']}", region["path"]
                )

        self.background(read, ready)

    def load_tracts(self, path, state=None, *, expected_hash=None, weight_record=None):
        if self.reference is None:
            raise ValueError("Open the matching image first")
        if str(path).lower().endswith(".tt.gz"):
            raise ValueError(
                "TinyTrack conversion is not enabled: the installed DSI converter failed spatial round-trip validation. Export a verified matching-space TCK/TRK instead."
            )
        ref = self.reference
        if not ref.physical_units_known or ref.affine_source == "fallback":
            raise ValueError(
                "Tract alignment requires documented physical units and a valid qform/sform"
            )
        self.status.setText(
            "Loading tractography in background (all fibers indexed, bounded display)…"
        )

        def ready(group):
            if ref is not self.reference:
                return
            group.expected_hash = expected_hash
            if state:
                group.hidden = set(state.get("hidden", []))
                group.selected = set(state.get("selected", []))
                group.visible = state["visible"]
                group.color = tuple(state["color"])
            self.groups.append(group)
            group.name = state.get("name", group.path.name) if state else group.path.name
            self.tract_list.addItem(group.name)
            self.tract_list.setCurrentRow(len(self.groups) - 1)
            self.refresh_tracts()
            sample_ids = group.display_ids(1000)
            points = (
                np.concatenate([group.line(i) for i in sample_ids])
                if sample_ids
                else np.empty((0, 3))
            )
            vox = ref.voxels(points)
            inside = np.all((vox >= -0.5) & (vox < np.array(ref.shape[:3]) - 0.5), axis=1)
            displayed = ScientificViewer.displayed_tract_count(group, self.density.value())
            self.status.setText(
                f"{group.path.name}: {group.total:,} source fibers indexed · "
                f"{displayed:,} drawn (cap {self.density.value():,}) · "
                f"{inside.mean() * 100 if len(inside) else 0:.2f}% sampled points inside image FOV. "
                "Bounds do not prove anatomical correspondence."
            )

        def read():
            from dmri_studio.core.execution import hash_file

            if expected_hash and hash_file(Path(path)) != expected_hash:
                raise ValueError("Generated tractogram changed; reload verified run evidence")
            group = TractGroup.load(path)
            record_path = weight_record or (state or {}).get("weight_record")
            if record_path:
                self.read_group_weights(group, record_path)
            if expected_hash and hash_file(Path(path)) != expected_hash:
                group.streamlines.close()
                raise ValueError("Generated tractogram changed during indexing")
            return group

        self.background(read, ready)

    @staticmethod
    def read_group_weights(group, path):
        """Bind full immutable factors to exact source bytes before subset rendering."""
        from dmri_studio.core.execution import hash_file
        from dmri_studio.core.scientific.records import read_json
        from dmri_studio.core.scientific.sift2 import read_weights

        path = Path(path)
        if path.stat().st_size > 1024**2:
            raise ValueError("Oversized weight identity record")
        record = read_json(path.read_text())
        if (
            record.get("schema") != "tractosphere.streamline-weights/1"
            or record.get("representation") != "weight_factors"
            or record.get("ordering") != "exact source file streamline order"
            or record.get("streamlines") != group.total
            or hash_file(group.path) != record.get("tractogram_sha256")
            or group.cuts
        ):
            raise ValueError("Weights do not match this exact unedited source tractogram/order")
        weights = read_weights(record["weights"], group.total)
        if hash_file(Path(record["weights"])) != record["weights_sha256"]:
            raise ValueError("SIFT2 factors changed; association rejected")
        record["record_path"] = str(path)
        group.weights, group.weight_record = weights, record

    def choose_weights(self):
        """Attach factors to a selected existing group, never modify track geometry."""
        group = self.current_group()
        if group is None:
            raise ValueError("Open and select the exact source tractogram first")
        path, _ = QFileDialog.getOpenFileName(
            self, "Exact-source SIFT2 weight record", "", "Weight record (*.json)"
        )
        if path:

            def read():
                self.read_group_weights(group, path)
                return group

            def ready(value):
                if any(value is g for g in self.groups):
                    self.coloring.setCurrentText("SIFT2 weight")
                    self.refresh_tracts()

            self.background(read, ready)

    def choose_scalar(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Exact-grid scalar map (FA, MD, etc.)", "", "NIfTI (*.nii *.nii.gz)"
        )
        if path:
            self.load_scalar(path)

    def load_scalar(self, path, activate=True):
        if self.reference is None:
            raise ValueError("Open the matching anatomical image first")
        ref = self.reference

        def read():
            image = ImageVolume(Path(path))
            if (
                image.count != 1
                or image.shape[:3] != ref.shape[:3]
                or not np.allclose(image.image.affine, ref.image.affine, atol=1e-5)
            ):
                raise ValueError(
                    "Scalar map must share the exact native image grid; no registration is inferred"
                )
            if image.physical_units_known and not np.allclose(image.affine, ref.affine, atol=1e-5):
                raise ValueError("Scalar map physical units disagree with the base image")
            return {"path": str(image.path), "data": image.volume()}

        def ready(value):
            if ref is self.reference:
                self.scalar_map = value
                self.scalar_label.setText(
                    "Scalar source: " + Path(value["path"]).name + " · exact base-image voxel grid"
                )
                if activate:
                    self.coloring.setCurrentText("Image scalar")
                self.refresh_tracts()

        self.background(read, ready)

    def clear_scalar(self):
        self.scalar_map = None
        self.scalar_label.setText("Scalar source: displayed image")
        self.refresh_tracts()

    def current_group(self):
        i = self.tract_list.currentRow()
        return self.groups[i] if i >= 0 else None

    def request_filter_tracks(self):
        """Handoff the selected group's full source, never its rendered subset."""
        group = self.current_group()
        if group is None or self.reference is None:
            self.status.setText("Select a loaded tract group and its reference image first.")
            return
        try:
            group.streamlines.verify_source()
        except (OSError, ValueError) as exc:
            self.status.setText(f"Track source changed after loading; reopen it first. {exc}")
            return
        if not self.reference.source_fingerprint.unchanged(self.reference.path):
            self.status.setText(
                "Reference image changed after loading; reopen it before filtering."
            )
            return
        displayed = ScientificViewer.displayed_tract_count(group, self.density.value())
        self.filter_tracks_requested.emit(
            dict(
                source=str(group.path),
                reference=str(self.reference.path),
                total=group.total,
                displayed=displayed,
                edited=bool(group.selected or group.hidden or group.deleted or group.cuts),
                expected_hash=getattr(group, "expected_hash", None),
                source_signature=group.streamlines.signature,
            )
        )

    @staticmethod
    def displayed_tract_count(group, cap):
        """Count rendered fibers separately from indexed source fibers."""
        return (
            min(cap, max(0, len(group.streamlines) - len(group.hidden | group.deleted)))
            if group.visible
            else 0
        )

    def refresh_tracts(self, *_):
        started = time.monotonic()
        if self.sender() is self.opacity:
            for key, actor in self.scene.actors.items():
                if key[0] == "tract":
                    actor.GetProperty().SetOpacity(self.opacity.value())
            self.scene.draw()
            self.last_render_seconds = time.monotonic() - started
            self.editing_changed.emit()
            return
        if self.sender() is self.width and self.mode.currentText() == "Lines":
            for key, actor in self.scene.actors.items():
                if key[0] == "tract":
                    actor.GetProperty().SetLineWidth(self.width.value())
            self.scene.draw()
            self.last_render_seconds = time.monotonic() - started
            self.editing_changed.emit()
            return
        if not hasattr(self, "density"):
            return
        for i, group in enumerate(self.groups):
            self.scene.tracts(
                ("tract", i),
                group,
                self.density.value(),
                self.mode.currentText(),
                self.coloring.currentText(),
                self.width.value(),
                self.opacity.value(),
                self.scalar_map["data"] if self.scalar_map else self.data,
                self.reference,
                highlight=self.highlight_selected.isChecked(),
            )
        self.tract_info(self.tract_list.currentRow())
        self.render_settings()
        self.last_render_seconds = time.monotonic() - started
        self.editing_changed.emit()

    def tract_info(self, index):
        if index < 0 or index >= len(self.groups):
            return
        group = self.groups[index]
        if group.weights is None:
            self.weight_legend.setText(
                "SIFT2 factors: none attached · unweighted group shown in its normal color"
            )
        elif group.cuts:
            self.weight_legend.setText(
                "Geometry edited in viewer: source weights are unavailable for these edited paths; no reweighting was performed"
            )
        else:
            s = group.weight_record["statistics"]
            selected = sorted(group.selected - group.deleted)
            text = (
                f"SIFT2 factors · viridis, linear: {s['minimum']:.6g} (dark) → {s['maximum']:.6g} (light)\n"
                f"{group.total:,} source factors · displayed subset is separate · no added mu scaling"
            )
            if selected:
                j = selected[0]
                source_id = group.source_ids[j]
                text += f"\nSelected source index {source_id:,} (0 based): factor {group.weights[source_id]:.17g}"
                if len(selected) > 1:
                    text += f" · {len(selected):,} selected; first shown"
            self.weight_legend.setText(text)
        stats = group.statistics()
        mean = stats["mean_length_mm"]
        displayed = ScientificViewer.displayed_tract_count(group, self.density.value())
        self.stats.setText(
            f"{stats['source_count']:,} source fibers · {stats['retained_loaded_count']:,} retained\n"
            f"{displayed:,} drawn in 3D (cap {self.density.value():,}); full source remains available\n"
            f"{len(group.selected - group.deleted):,} selected · {len(group.hidden - group.deleted):,} hidden\n"
            + (
                f"Length: {stats['min_length_mm']:.1f}–{stats['max_length_mm']:.1f} mm · mean {mean:.1f} mm"
                if mean is not None
                else "No retained fibers"
            )
        )
        self.tract_visible.blockSignals(True)
        self.tract_visible.setChecked(group.visible)
        self.tract_visible.blockSignals(False)

    def group_visibility(self, value):
        if group := self.current_group():
            group.visible = value
            self.refresh_tracts()

    def group_color(self):
        color = QColorDialog.getColor(parent=self)
        if color.isValid() and (group := self.current_group()):
            group.color = color.getRgbF()[:3]
            self.coloring.setCurrentText("Uniform")
            self.refresh_tracts()

    def select_tract(self, key, index):
        group = self.groups[key[1]]
        self.tract_list.setCurrentRow(key[1])
        group.selected.symmetric_difference_update({index})
        self.refresh_tracts()

    def select_all(self, value):
        if group := self.current_group():
            group.selected = set(range(len(group.streamlines))) - group.deleted if value else set()
            self.refresh_tracts()

    def select_by_roi(self):
        group = self.current_group()
        i = self.roi_list.currentRow()
        if group is None or i < 0:
            raise ValueError("Select a tract group and ROI first")
        ref = self.reference
        roi = self.rois[i]["data"].copy()
        # Snapshot editable state; worker never mutates a live rendered group.
        snapshot = TractGroup(group.path, group.streamlines, group.source_ids, group.total)
        snapshot.deleted = set(group.deleted)
        snapshot.cuts = dict(group.cuts)

        def find():
            snapshot.select_roi(roi, ref)
            return snapshot.selected

        def apply(selected):
            if ref is self.reference and any(g is group for g in self.groups):
                group.selected = selected - group.deleted
                self.refresh_tracts()
                self.status.setText(f"Selected {len(group.selected):,} intersecting fibers")

        self.status.setText("Selecting fibers intersecting ROI…")
        self.background(find, apply)

    def edit_tracts(self, operation):
        if group := self.current_group():
            axis = self.views["axial"].geometry.axis
            normal = np.linalg.inv(self.reference.affine[:3, :3])[axis]
            group.edit(
                operation,
                self.reference.world(self.position),
                normal,
                keep=self.cut_mode.currentText(),
            )
            self.refresh_tracts()

    def show_hidden(self):
        if group := self.current_group():
            group.checkpoint()
            group.hidden.clear()
            self.refresh_tracts()

    def tract_undo(self, redo):
        if group := self.current_group():
            group.undo(redo)
            self.refresh_tracts()

    def export_tracts(self):
        group = self.current_group()
        if group is None:
            return
        path, selected_format = QFileDialog.getSaveFileName(
            self,
            "Save undeleted fibers (source unchanged)",
            "",
            "MRtrix (*.tck);;TrackVis (*.trk)",
        )
        if path:
            if Path(path).suffix.lower() not in (".tck", ".trk"):
                path += ".trk" if "TrackVis" in selected_format else ".tck"
            if Path(path + ".json").exists():
                raise FileExistsError(path + ".json")
            snapshot = TractGroup(group.path, group.streamlines, group.source_ids, group.total)
            snapshot.deleted = set(group.deleted)
            snapshot.hidden = set(group.hidden)
            snapshot.cuts = dict(group.cuts)
            snapshot._lengths = group._lengths
            ref = self.reference
            self.status.setText("Exporting tract derivative in background…")

            def write():
                snapshot.export(path, ref)
                record = {
                    "source": str(snapshot.path),
                    "loaded_source_ids": list(snapshot.source_ids)
                    if not isinstance(snapshot.source_ids, range)
                    else {"start": 0, "count": snapshot.total},
                    "deleted_loaded_ids": sorted(snapshot.deleted),
                    "cut_loaded_ids": sorted(snapshot.cuts),
                    "statistics": snapshot.statistics(),
                    "space": "RAS+ mm",
                    "point_metadata_at_cuts": "linear interpolation along original arc length",
                }
                with open(path + ".json", "x", encoding="utf-8") as f:
                    json.dump(record, f, indent=2)
                return path

            self.background(
                write, lambda result: self.status.setText("Saved tract derivative: " + result)
            )

    def choose_roi(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load same-grid ROI", "", "NIfTI (*.nii *.nii.gz)"
        )
        if path:
            self.load_roi(path)

    def load_roi(self, path):
        if self.reference is None:
            raise ValueError("Open an image first")
        roi = ImageVolume(Path(path))
        values = roi.volume()
        unique = np.unique(values)
        sidecar = (
            Path(str(path)[:-7] + ".json")
            if str(path).endswith(".nii.gz")
            else Path(path).with_suffix(".json")
        )
        saved = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
        if saved.get("schema") != "tractosphere.roi/1" and (
            roi.count > 1 or not np.isin(unique, [0, 1, 255]).all()
        ):
            self.choose_atlas(path)
            return
        same = roi.shape[:3] == self.reference.shape[:3] and np.allclose(
            roi.image.affine, self.reference.image.affine, atol=1e-5
        )
        if not same and not self.roi_same_space.isChecked():
            raise ValueError(
                "Different ROI grid: explicitly confirm same world space to resample labels"
            )
        from dmri_studio.core.scene_operations import resample_overlay

        self.add_roi(
            resample_overlay(roi, self.reference, order=0) != 0,
            Path(path).name,
            str(Path(path).resolve()),
        )
        if saved.get("schema") == "tractosphere.roi/1":
            self.rois[-1].update(saved["roi"])
            self.rois[-1]["path"] = str(Path(path).resolve())
            self.roi_list.rebuild()
            self.roi_list.setCurrentRow(len(self.rois) - 1)

    def add_roi(self, data, name, path=None):
        self.rois.append(
            {
                "data": np.asarray(data, dtype=np.uint8),
                "name": name,
                "path": path,
                "visible": True,
                "color": (0.95, 0.5, 0.2),
                "opacity": 0.3,
                "edited": False,
                "surface_built": False,
                "role": "Display",
                "voxel_count": int(np.count_nonzero(data)),
                "volume_mm3": float(
                    np.count_nonzero(data) * abs(np.linalg.det(self.reference.affine[:3, :3]))
                ),
            }
        )
        self.roi_list.addItem(name)
        self.roi_list.setCurrentRow(len(self.rois) - 1)
        self.refresh_slices()

    def new_roi(self):
        if self.data is None:
            raise ValueError("Open an image first")
        self.add_roi(np.zeros(self.data.shape, dtype=np.uint8), f"ROI {len(self.rois) + 1}")

    def roi_info(self, i):
        if not 0 <= i < len(self.rois):
            self.roi_stats.setText("Create or load an ROI to begin")
            return
        roi = self.rois[i]
        n = int(np.count_nonzero(roi["data"]))
        roi["voxel_count"] = n
        roi["volume_mm3"] = (
            float(n * abs(np.linalg.det(self.reference.affine[:3, :3])))
            if self.reference.physical_units_known
            else None
        )
        self.roi_list.update_region(i)
        units = "mm³" if self.reference.physical_units_known else "affine units³ (unverified)"
        self.roi_stats.setText(
            f"{n:,} voxels · {n * abs(np.linalg.det(self.reference.affine[:3, :3])):.2f} {units}\nMean image intensity: {float(self.data[roi['data'] > 0].mean()):.4g}"
            if n
            else "Empty ROI"
        )
        self.roi_visible.blockSignals(True)
        self.roi_visible.setChecked(roi["visible"])
        self.roi_visible.blockSignals(False)
        self.roi_opacity.blockSignals(True)
        self.roi_opacity.setValue(roi["opacity"])
        self.roi_opacity.blockSignals(False)

    def roi_surface(self):
        i = self.roi_list.currentRow()
        if i < 0:
            raise ValueError("Select an ROI first")
        roi = self.rois[i]
        from .roi_surfaces import roi_mesh

        self.scene.roi_polydata(
            ("roi", i),
            roi_mesh(roi["data"], self.reference.affine, roi.get("surface_quality", "Smooth")),
            roi["color"],
            roi["opacity"],
            roi.get("surface_mode", "Surface"),
        )
        self.scene.actors[("roi", i)].SetVisibility(roi["visible"])
        roi["surface_built"] = True

    def roi_visibility(self, value):
        i = self.roi_list.currentRow()
        if i >= 0:
            self.atlas_visibility(i, value)

    def update_roi_style(self):
        i = self.roi_list.currentRow()
        if i >= 0:
            self.rois[i]["opacity"] = self.roi_opacity.value()
            if ("roi", i) in self.scene.actors:
                self.scene.actors[("roi", i)].GetProperty().SetOpacity(self.roi_opacity.value())
                self.scene.draw()
            self.refresh_slices()

    def roi_color(self):
        color = QColorDialog.getColor(parent=self)
        i = self.roi_list.currentRow()
        if i >= 0 and color.isValid():
            self.rois[i]["color"] = color.getRgbF()[:3]
            self.roi_list.update_region(i)
            if ("roi", i) in self.scene.actors:
                self.scene.actors[("roi", i)].GetProperty().SetColor(*self.rois[i]["color"])
                self.scene.draw()
            self.refresh_slices()

    def roi_checkpoint(self, i):
        if "data" not in self.rois[i]:
            self.rois[i]["data"] = self.rois[i]["data"].copy()
        # Editing a hidden reference otherwise appears to do nothing. Reveal
        # the derivative being edited; source voxel data remain untouched.
        if not self.rois[i]["visible"]:
            self.rois[i]["visible"] = True
            if self.roi_list.currentRow() == i:
                self.roi_visible.setChecked(True)
            if ("roi", i) in self.scene.actors:
                self.scene.actors[("roi", i)].SetVisibility(True)
        self.roi_undo.append((i, self.rois[i]["data"].copy()))
        self.roi_undo = self.roi_undo[-5:]
        self.roi_redo.clear()
        self.editing_changed.emit()

    def undo_roi(self, redo):
        source, target = (self.roi_redo, self.roi_undo) if redo else (self.roi_undo, self.roi_redo)
        if source:
            i, data = source.pop()
            target.append((i, self.rois[i]["data"].copy()))
            self.rois[i]["data"] = data
            self.rois[i]["edited"] = True
            self.roi_list.setCurrentRow(i)
            self.schedule_roi_surface(i)
            self.refresh_slices()
            self.roi_info(i)
        else:
            self.status.setText("No ROI edit to " + ("redo" if redo else "undo"))
        self.editing_changed.emit()

    def morph_roi(self, operation):
        from scipy.ndimage import binary_dilation, binary_erosion

        i = self.roi_list.currentRow()
        if i < 0:
            raise ValueError("Select an ROI first")
        if operation.startswith("Merge"):
            if not any(r["visible"] for r in self.rois):
                raise ValueError("No visible ROI to merge")
            self.add_roi(
                np.logical_or.reduce([r["data"] for r in self.rois if r["visible"]]), "Merged ROI"
            )
        else:
            self.roi_checkpoint(i)
            self.rois[i]["data"] = (binary_dilation if operation == "Dilate" else binary_erosion)(
                self.rois[i]["data"]
            ).astype(np.uint8)
            self.rois[i]["edited"] = True
            self.schedule_roi_surface(i)
        self.refresh_slices()
        self.roi_info(self.roi_list.currentRow())

    def save_roi(self):
        i = self.roi_list.currentRow()
        if i < 0:
            raise ValueError("Select or create an ROI first")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save new ROI derivative", "", "NIfTI (*.nii.gz *.nii)"
        )
        if path:
            if not path.endswith((".nii", ".nii.gz")):
                path += ".nii.gz"
            sidecar = (
                Path(path[:-7] + ".json")
                if path.endswith(".nii.gz")
                else Path(path).with_suffix(".json")
            )
            if sidecar.exists():
                raise FileExistsError(sidecar)
            save_new_image(path, self.rois[i]["data"], self.reference)
            with sidecar.open("x", encoding="utf-8") as f:
                json.dump(
                    {
                        "schema": "tractosphere.roi/1",
                        "roi": {k: v for k, v in self.rois[i].items() if k != "data"},
                    },
                    f,
                    indent=2,
                )
            self.status.setText("Saved ROI and region metadata: " + path)

    def choose_surface(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Matching-world-space image for isosurface", "", "NIfTI (*.nii *.nii.gz)"
        )
        if path:
            self.load_surface(path, self.surface_threshold.value(), self.surface_label.value())

    def load_surface(self, path, threshold, label_id=0, state=None):
        ref = self.reference
        if ref is None:
            raise ValueError("Open an image first")

        def read():
            image = ImageVolume(Path(path))
            data = image.volume()
            return image, (data == label_id).astype(np.float32) if label_id else data

        def ready(result):
            if ref is not self.reference:
                return
            image, data = result
            if not image.physical_units_known or not ref.physical_units_known:
                raise ValueError("Surface alignment requires documented physical units")
            key = ("surface", len(self.surfaces))
            record = state or {
                "path": str(image.path),
                "threshold": threshold,
                "label": label_id,
                "opacity": 0.2,
                "color": (0.72, 0.78, 0.85),
                "visible": True,
            }
            self.surfaces.append(record)
            self.surface_list.addItem(
                image.path.name + (" · label " + str(label_id) if label_id else " · isosurface")
            )
            self.surface_list.setCurrentRow(len(self.surfaces) - 1)
            self.scene.surface(
                key,
                data,
                image.affine,
                0.5 if label_id else threshold,
                record["color"],
                record["opacity"],
            )
            self.scene.actors[key].SetVisibility(record["visible"])
            self.render_settings()

        self.background(read, ready)

    def surface_selection(self, index):
        if 0 <= index < len(self.surfaces) and hasattr(self, "surface_opacity"):
            record = self.surfaces[index]
            self.surface_opacity.blockSignals(True)
            self.surface_visible.blockSignals(True)
            self.surface_opacity.setValue(record["opacity"])
            self.surface_visible.setChecked(record["visible"])
            self.surface_opacity.blockSignals(False)
            self.surface_visible.blockSignals(False)

    def surface_style(self):
        i = self.surface_list.currentRow()
        key = ("surface", i)
        if key in self.scene.actors:
            self.scene.actors[key].GetProperty().SetOpacity(self.surface_opacity.value())
            self.scene.actors[key].SetVisibility(self.surface_visible.isChecked())
            self.surfaces[i].update(
                opacity=self.surface_opacity.value(), visible=self.surface_visible.isChecked()
            )
            self.scene.draw()

    def surface_color(self):
        color = QColorDialog.getColor(parent=self)
        key = ("surface", self.surface_list.currentRow())
        if color.isValid() and key in self.scene.actors:
            self.scene.actors[key].GetProperty().SetColor(*color.getRgbF()[:3])
            self.surfaces[key[1]]["color"] = color.getRgbF()[:3]
            self.scene.draw()

    def projection(self, value):
        self.scene.renderer.GetActiveCamera().SetParallelProjection(value)
        self.scene.draw()

    def background_color(self):
        color = QColorDialog.getColor(parent=self)
        if color.isValid():
            self.scene.renderer.SetBackground(*color.getRgbF()[:3])
            self.scene.draw()

    def render_settings(self, *_):
        if not hasattr(self, "clip"):
            return
        from .scene_renderer import vtk

        for axis, actor in enumerate(self.scene.planes):
            actor.GetProperty().SetOpacity(self.slice_opacity.value())
            actor.SetVisibility(self.plane_checks[axis].isChecked())
        for actor in self.scene.actors.values():
            actor.GetProperty().SetAmbient(self.light.value())
            actor.GetMapper().RemoveAllClippingPlanes()
            if self.clip.isChecked() and self.reference:
                plane = vtk.vtkPlane()
                axis = plane_geometry(self.reference, "axial").axis
                plane.SetNormal(*np.linalg.inv(self.reference.affine[:3, :3])[axis])
                plane.SetOrigin(*self.reference.world(self.position))
                actor.GetMapper().AddClippingPlane(plane)
        self.scene.renderer.SetMaximumNumberOfPeels(32 if self.quality.currentIndex() else 8)
        self.scene.renderer.SetOcclusionRatio(0.01 if self.quality.currentIndex() else 0.1)
        self.scene.renderer.SetUseFXAA(self.aa.isChecked())
        self.apply_fod_planes()
        self.scene.draw()

    def save_screenshot(self):
        path, selection = QFileDialog.getSaveFileName(
            self, "Save rendered image", "", "3D view PNG (*.png);;Workspace PNG (*.png)"
        )
        if path:
            if Path(path).suffix.lower() != ".png":
                path += ".png"
            if Path(path).exists():
                raise FileExistsError(path)
            if selection.startswith("Workspace"):
                from .capture import capture_widget

                if not capture_widget(self).save(path):
                    raise OSError("Could not save workspace screenshot")
            else:
                self.scene.screenshot(path)

    def save_workspace(self, path=None):
        if self.reference is None:
            raise ValueError("Open an image before saving a workspace")
        if self.tasks:
            raise ValueError("Wait for current image/tract reads before saving")
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Save new workspace", "", "JSON (*.json)")
        if not path:
            return
        path = Path(path)
        if path.suffix.lower() != ".json":
            path = Path(str(path) + ".json")
        if path.exists():
            raise FileExistsError(path)
        assets = path.with_suffix(".assets")
        assets.mkdir(exist_ok=False)
        groups = []
        for i, group in enumerate(self.groups):
            has_attributes = hasattr(group.streamlines, "scalar_count") and (
                group.streamlines.scalar_count or group.streamlines.property_count
            )
            target = assets / f"tracts-{i}{chr(46)}{'trk' if has_attributes else 'tck'}"
            if group.deleted or group.cuts or not isinstance(group.source_ids, range):
                group.export(target, self.reference)
            else:
                target = group.path
            groups.append(
                {
                    "path": str(target.resolve()),
                    "source": str(group.path),
                    "name": getattr(group, "name", group.path.name),
                    "source_ids": list(group.source_ids)
                    if not isinstance(group.source_ids, range)
                    else {"start": 0, "count": group.total},
                    "hidden": [
                        j
                        for j, k in enumerate(
                            i for i in range(len(group.streamlines)) if i not in group.deleted
                        )
                        if k in group.hidden
                    ],
                    "selected": [
                        j
                        for j, k in enumerate(
                            i for i in range(len(group.streamlines)) if i not in group.deleted
                        )
                        if k in group.selected
                    ],
                    "visible": group.visible,
                    "color": group.color,
                    "weight_record": group.weight_record.get("record_path")
                    if group.weight_record and target == group.path
                    else None,
                }
            )
        rois = []
        for i, roi in enumerate(self.rois):
            target = assets / f"roi-{i}.nii.gz"
            save_new_image(target, roi["data"], self.reference)
            rois.append(
                {**{k: v for k, v in roi.items() if k != "data"}, "path": str(target.resolve())}
            )
        camera = self.scene.renderer.GetActiveCamera()
        record = {
            "schema": "tractosphere.viewer/1",
            "ui": {
                "mode": self.workspace_mode,
                "layout": self.layout_choice.currentText(),
                "mode_layouts": {
                    **self.mode_layouts,
                    self.workspace_mode: self.layout_choice.currentText(),
                },
                "mode_tabs": {**self.mode_tabs, self.workspace_mode: self.tabs.currentIndex()},
                "inspector_visible": not self.inspector.isHidden(),
                "split_sizes": self.scene_split.sizes(),
            },
            "image": str(self.reference.path),
            "fod_background": isinstance(self.reference, CoefficientBackground),
            "fod": self.fod_settings(),
            "dti_direction": getattr(self, "dti_direction_path", None),
            "dti_source_hashes": getattr(self, "dti_source_hashes", {}),
            "scalar_map": self.scalar_map["path"] if self.scalar_map else None,
            "registration": self.registration_workspace(),
            "layers": [
                {k: v for k, v in layer.items() if k != "data"}
                for layer in self.layers
                if not layer.get("registration")
            ],
            "position": self.position,
            "volume": self.volume.value(),
            "range": [self.low.value(), self.high.value()],
            "groups": groups,
            "rois": rois,
            "surfaces": self.surfaces,
            "camera": {
                "position": camera.GetPosition(),
                "focal": camera.GetFocalPoint(),
                "up": camera.GetViewUp(),
                "scale": camera.GetParallelScale(),
                "parallel": bool(camera.GetParallelProjection()),
            },
            "render": {
                "roi_global_opacity": self.roi_global_opacity.value(),
                "mode": self.mode.currentText(),
                "coloring": self.coloring.currentText(),
                "width": self.width.value(),
                "opacity": self.opacity.value(),
                "density": self.density.value(),
                "slice_opacity": self.slice_opacity.value(),
                "planes": [c.isChecked() for c in self.plane_checks],
                "colormap": self.colormap.currentText(),
                "background": self.scene.renderer.GetBackground(),
                "lighting": self.light.value(),
                "clip": self.clip.isChecked(),
                "fxaa": self.aa.isChecked(),
                "quality": self.quality.currentIndex(),
                "highlight_selected": self.highlight_selected.isChecked(),
                "volume_render": self.volume_render_check.isChecked(),
                "volume_opacity": self.volume_alpha.value(),
                "image_interpolation": self.interpolation.currentText(),
            },
        }
        with path.open("x", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
        self.status.setText("Saved workspace and separate edited derivatives: " + str(path))

    def open_workspace(self, path=None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Restore workspace", "", "JSON (*.json)")
        if not path:
            return
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        if record.get("schema") != "tractosphere.viewer/1":
            raise ValueError("Unsupported workspace schema")
        self.pending_restore = record
        if record.get("fod_background"):
            f = record["fod"]
            self.load_fod(f["path"], expected_hash=f["sha256"], settings=f)
            return
        self.load_image(
            record["image"],
            direction_path=record.get("dti_direction"),
            expected_hashes=record.get("dti_source_hashes"),
        )

    def restore_objects(self, record):
        if record.get("fod") and not record.get("fod_background"):
            f = record["fod"]
            self.load_fod(f["path"], expected_hash=f["sha256"], settings=f)
        ui = record.get("ui", {})
        self.set_workspace_mode(ui.get("mode", self.workspace_mode))
        for mode in ("image", "tractography"):
            layout = ui.get("mode_layouts", {}).get(mode)
            layout = "3D scene" if layout == "3D anatomy" else layout
            if layout is not None and self.layout_choice.findText(layout) >= 0:
                self.mode_layouts[mode] = layout
            tab = ui.get("mode_tabs", {}).get(mode)
            if (
                isinstance(tab, int)
                and 0 <= tab < self.tabs.count()
                and (mode == "tractography" or tab != 1)
            ):
                self.mode_tabs[mode] = tab
        self.tabs.setCurrentIndex(self.mode_tabs[self.workspace_mode])
        saved_layout = ui.get("layout", "Four views")
        self.layout_choice.setCurrentText(
            "3D scene" if saved_layout == "3D anatomy" else saved_layout
        )
        self.set_inspector_visible(ui.get("inspector_visible", True))
        if isinstance(ui.get("split_sizes"), list) and len(ui["split_sizes"]) == 2:
            self.scene_split.setSizes(ui["split_sizes"])
        if record.get("scalar_map"):
            self.load_scalar(record["scalar_map"], activate=False)
        self.range_changed(*record["range"])
        self.navigate(record["position"])
        self.volume.setValue(record["volume"])
        r = record["render"]
        with QSignalBlocker(self.roi_global_opacity):
            self.roi_global_opacity.setValue(r.get("roi_global_opacity", 0.3))
        self.mode.setCurrentText(r["mode"])
        self.coloring.setCurrentText(r["coloring"])
        self.width.setValue(r["width"])
        self.opacity.setValue(r["opacity"])
        self.density.setValue(r["density"])
        self.slice_opacity.setValue(r["slice_opacity"])
        self.colormap.setCurrentText(r["colormap"])
        self.interpolation.setCurrentText(r.get("image_interpolation", "Linear display"))
        self.light.setValue(r["lighting"])
        self.clip.setChecked(r["clip"])
        self.aa.setChecked(r["fxaa"])
        self.quality.setCurrentIndex(r.get("quality", 0))
        self.highlight_selected.setChecked(r.get("highlight_selected", True))
        self.scene.renderer.SetBackground(*r["background"])
        for check, visible in zip(self.plane_checks, r["planes"], strict=False):
            check.setChecked(visible)
        for group in record["groups"]:
            self.load_tracts(group["path"], group)
        for roi in record["rois"]:
            self.load_roi(roi["path"])
            self.rois[-1].update({k: v for k, v in roi.items() if k != "path"})
            if roi.get("surface_built"):
                self.roi_surface()
        # Restoration must not turn hidden atlas regions on merely because
        # the tree establishes a current row. Explicit user selection reveals.
        with QSignalBlocker(self.roi_list):
            for i, saved in enumerate(record["rois"]):
                self.rois[i]["selected"] = saved.get("selected", False)
                self.rois[i]["selection_rank"] = saved.get("selection_rank")
            self.roi_list.rebuild()
            if self.rois:
                self.roi_list.setCurrentRow(0)
            for i, item in enumerate(self.roi_list.entries):
                item.setSelected(self.rois[i].get("selected", False))
        self.roi_selection_order = sorted(
            [i for i, r in enumerate(self.rois) if r.get("selected")],
            key=lambda i: self.rois[i].get("selection_rank") or 0,
        )
        if self.rois:
            self.roi_info(0)
        for surface in record["surfaces"]:
            if surface.get("kind") == "mesh":
                self.load_mesh(surface["path"], surface)
            else:
                self.load_surface(surface["path"], surface["threshold"], surface["label"], surface)
        for layer in record.get("layers", []):
            self.load_layer(layer["path"], layer.get("resampled_same_space", False), layer)
        if record.get("registration"):
            self.registration_restore = {
                **record["registration"],
                "position": record["position"],
                "range": record["range"],
            }
            self.load_registration_product(record["registration"]["product"])
        self.volume_alpha.setValue(r.get("volume_opacity", 0.15))
        self.volume_render_check.setChecked(r.get("volume_render", False))
        camera = self.scene.renderer.GetActiveCamera()
        c = record["camera"]
        camera.SetPosition(*c["position"])
        camera.SetFocalPoint(*c["focal"])
        camera.SetViewUp(*c["up"])
        camera.SetParallelScale(c["scale"])
        self.parallel.setChecked(c["parallel"])
        self.refresh_slices()
        self.scene.draw()

    def shutdown(self):
        """Do not destroy live readers. Main window retries cooperatively."""
        dialog = getattr(self, "native_roi_dialog", None)
        if dialog is not None and dialog.thread:
            dialog.cancel_event.set()
            return False
        self.fod_generation += 1
        self.roi_surface_queue.clear()
        self.fod_request += 1
        self.fod_timer.stop()
        self.scene_generation += 1
        self.volume_generation += 1
        if hasattr(self, "_roi_surface_timer"):
            self._roi_surface_timer.stop()
            self._roi_dirty.clear()
        if self.tasks or self.pool.activeThreadCount():
            return False
        # Release callbacks/context while the native Qt owner is still alive,
        # rather than depend on cyclic Python/VTK garbage-collection order.
        self.scene.Finalize()
        return True
