"""Selection-oriented presentation of existing scientific viewer tools.

Controls are reparented, not duplicated: each original callback and scientific
value remains attached to its original widget. Hidden advanced sections retain
state, and every supported editing action stays available.
"""

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHeaderView,
    QLineEdit,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from .components import label
from .workstation import inspector_section


def compact_object_list(control):
    """Size short lists to real rows; large lists retain a bounded scroll area."""

    def size(*_):
        # The model can reset during QObject teardown, after its view is gone.
        if not isValid(control):
            return
        rows = control.count()
        height = max(64, min(180, rows * (control.fontMetrics().height() + 12) + 10))
        control.setFixedHeight(height)

    control.model().rowsInserted.connect(size)
    control.model().rowsRemoved.connect(size)
    control.model().modelReset.connect(size)
    size()


def organize_inspector(viewer):
    """Put object selection first and appearance/editing in separate sections."""
    v = viewer
    titles = ["Image", "Tracts", "ROIs", "Surfaces", "Layers"]
    for index, title in enumerate(titles):
        v.tabs.setTabText(index, title)
    v.inspector_sections = {}
    for index, attribute in enumerate(
        ["display_layout", "tracts_layout", "rois_layout", "surfaces_layout", "layers_layout"]
    ):
        scroll = v.tabs.widget(index)
        old = scroll.widget()
        actions = {b.text(): b for b in old.findChildren(QPushButton)}
        used = set()
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        setattr(v, attribute, layout)

        def buttons(*names, actions=actions, used=used):
            controls = []
            for name in names:
                control = actions[name]
                used.add(name)
                controls.append(control)
            return controls

        def section(title, rows=(), widgets=(), advanced=False, _index=index, _layout=layout):
            item = inspector_section(title, rows, widgets, advanced=advanced)
            v.inspector_sections[(_index, title)] = item
            _layout.addWidget(item)

        if index == 0:
            section(
                "Image display",
                [("Minimum", v.low), ("Maximum", v.high), ("Color map", v.colormap)],
                buttons("Auto contrast"),
            )
            v.interpolation = QComboBox()
            v.interpolation.addItems(["Linear display", "Nearest display"])
            v.interpolation.setToolTip(
                "Display scaling only. Source voxels and label overlays are unchanged."
            )
            v.interpolation.currentIndexChanged.connect(lambda i: set_interpolation(v, i))
            section("Display sampling", [("Interpolation", v.interpolation)], advanced=True)
            section(
                "Camera",
                [("View", v.camera)],
                buttons("Fit / reset camera") + [v.parallel],
                advanced=True,
            )
            section(
                "Anatomical planes", [("Opacity", v.slice_opacity)], v.plane_checks, advanced=True
            )
            section(
                "3D rendering",
                [("Ambient light", v.light)],
                [v.clip, v.quality, v.aa] + buttons("Background color"),
                advanced=True,
            )
            section(
                "Volume rendering",
                [("Opacity", v.volume_alpha)],
                [v.volume_render_check],
                advanced=True,
            )
            v.image_metadata = label("No image open", "caption")
            section("Image information", widgets=[v.image_metadata], advanced=True)
            layout.addWidget(
                label(
                    "Slices: drag crosshair · right-drag contrast\n3D: rotate · middle-drag pan · wheel zoom",
                    "caption",
                )
            )
        elif index == 1:
            compact_object_list(v.tract_list)
            layout.addWidget(v.tract_list)
            layout.addWidget(v.tract_visible)
            layout.addWidget(v.stats)
            section(
                "Appearance",
                [
                    ("Geometry", v.mode),
                    ("Color", v.coloring),
                    ("Opacity", v.opacity),
                    ("Width / diameter", v.width),
                    ("Display limit", v.density),
                ],
                buttons("Uniform group color"),
            )
            section(
                "Scalar coloring",
                widgets=buttons("Load same-grid scalar color map", "Use base-image scalar colors")
                + [v.scalar_label],
                advanced=True,
            )
            section(
                "SIFT2 weight factors",
                widgets=buttons("Load exact-source SIFT2 weight record") + [v.weight_legend],
                advanced=True,
            )
            section(
                "Select streamlines",
                [("Interaction", v.selection_mode), ("Plane angle limit", v.angle)],
                [v.highlight_selected]
                + buttons(
                    "Select tracts intersecting selected ROI",
                    "Select all loaded in group",
                    "Clear selection",
                ),
                advanced=True,
            )
            section(
                "Edit selection",
                [("Keep after cut", v.cut_mode)],
                buttons(
                    "Hide selected",
                    "Show hidden",
                    "Delete selected",
                    "Cut selected at axial plane",
                    "Cut selected at drag plane",
                    "Undo",
                    "Redo",
                ),
                advanced=True,
            )
            section(
                "Manage group",
                widgets=buttons(
                    "Rename group",
                    "Remove group from scene",
                    "Export retained loaded tracts",
                    "Export bundle statistics",
                ),
                advanced=True,
            )
            object_menu(
                v,
                v.tract_list,
                [
                    ("Color…", v.group_color),
                    ("Rename…", v.rename_group),
                    ("Export tracts…", v.export_tracts),
                    ("Export statistics…", v.export_statistics),
                ],
            )
        elif index == 2:
            layout.addWidget(buttons("Import anatomical atlas")[0])
            layout.addWidget(v.atlas_status)
            v.region_search = QLineEdit()
            v.region_search.setPlaceholderText("Find a region or label…")
            v.region_search.setAccessibleName("Search atlas regions")
            v.region_search.setToolTip("Filters the list only; anatomical visibility is unchanged.")
            v.region_search.textChanged.connect(v.roi_list.filter_regions)
            layout.addWidget(v.region_search)
            layout.addWidget(v.roi_bulk_panel)
            used.update(b.text() for b in v.roi_bulk_panel.findChildren(QPushButton))
            v.roi_list.setMinimumHeight(150)
            v.roi_list.setMaximumHeight(260)
            v.roi_list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            # Full region identity has priority over a three-column squeeze.
            # Numeric values/role remain visible for the selected region below.
            v.roi_list.setColumnHidden(1, True)
            v.roi_list.setColumnHidden(2, False)
            layout.addWidget(v.roi_list)
            v.selected_region_name = label("Select a region", "control")
            v.selected_region_role = label("", "caption")

            def selected_region(*_):
                if not isValid(v.roi_list) or not isValid(v.selected_region_name):
                    return
                i = v.roi_list.currentRow()
                if 0 <= i < len(v.rois):
                    roi = v.rois[i]
                    prefix = f"{roi['label_id']} · " if "label_id" in roi else ""
                    v.selected_region_name.setText(
                        prefix
                        + (
                            roi["name"]
                            if roi.get("name_available", "label_id" not in roi)
                            else "anatomical name unavailable"
                        )
                    )
                    v.selected_region_role.setText("Role: " + roi.get("role", "Display"))

            v.roi_list.currentRowChanged.connect(selected_region)
            v.roi_list.model().dataChanged.connect(selected_region)
            layout.addWidget(v.selected_region_name)
            layout.addWidget(v.selected_region_role)
            layout.addWidget(v.roi_stats)
            section(
                "Selected region",
                [("Opacity", v.roi_opacity)],
                [v.roi_visible] + buttons("ROI color", "Build / update ROI in 3D"),
            )
            section(
                "Existing tract filtering",
                [("Region role", v.roi_role), ("Inclusion rule", v.roi_filter_mode)],
                [v.tract_space_confirmed]
                + buttons(
                    "Assign role to selected ROIs",
                    "Filter full source with MRtrix…",
                    "Save selected tract group",
                ),
                advanced=True,
            )
            section(
                "Create / import",
                widgets=buttons("Load ROI NIfTI", "New empty ROI in image grid")
                + [v.roi_same_space],
                advanced=True,
            )
            section(
                "Edit mask",
                [("Tool", v.brush), ("Brush radius (voxels)", v.radius)],
                buttons(
                    "Finish polygon",
                    "Cancel polygon",
                    "Dilate",
                    "Erode",
                    "Merge visible into new ROI",
                    "Undo",
                    "Redo",
                ),
                advanced=True,
            )
            section(
                "Other region operations",
                [("Operation", v.roi_operation)],
                buttons(
                    "Apply ROI operation",
                    "Save selected ROI as new NIfTI",
                    "Export ROI statistics",
                    "Reimport atlas with new threshold",
                ),
                advanced=True,
            )
            object_menu(
                v,
                v.roi_list,
                [
                    ("Region color…", v.roi_color),
                    ("Jump to centroid", lambda: roi_operation(v, "Jump to centroid")),
                    ("Show in 3D", v.roi_surface),
                    ("Save region…", v.save_roi),
                ],
            )
        elif index == 3:
            compact_object_list(v.surface_list)
            layout.addWidget(v.surface_list)
            section(
                "Selected surface",
                [("Opacity", v.surface_opacity)],
                [v.surface_visible] + buttons("Surface color"),
            )
            section(
                "Generate from image",
                [("Isovalue", v.surface_threshold), ("Label ID", v.surface_label)],
                buttons("Add surface from NIfTI")
                + [
                    label(
                        "Mask/segmentation isosurfaces are not cortical reconstructions.", "caption"
                    )
                ],
                advanced=True,
            )
            section(
                "Mesh files",
                widgets=buttons("Load GIFTI / FreeSurfer surface", "Remove selected surface"),
                advanced=True,
            )
            object_menu(
                v,
                v.surface_list,
                [("Color…", v.surface_color), ("Remove from scene", v.remove_surface)],
            )
        else:
            v.layers_panel = content
            compact_object_list(v.layer_list)
            layout.addWidget(v.layer_list)
            layout.addWidget(buttons("Add scalar overlay")[0])
            section(
                "Selected overlay",
                [
                    ("Opacity", v.layer_alpha),
                    ("Minimum", v.layer_low),
                    ("Maximum", v.layer_high),
                    ("Color map", v.layer_cmap),
                ],
                [v.layer_visible],
            )
            section(
                "Geometry and order",
                widgets=[v.layer_same_space]
                + buttons("Move layer up", "Move layer down", "Remove selected layer"),
                advanced=True,
            )
            object_menu(
                v,
                v.layer_list,
                [
                    ("Move up", lambda: v.move_layer(-1)),
                    ("Move down", lambda: v.move_layer(1)),
                    ("Remove from scene", v.remove_layer),
                ],
            )
        remaining = [control for name, control in actions.items() if name not in used]
        if remaining:
            section("Additional tools", widgets=remaining, advanced=True)
        layout.addStretch()
        scroll.takeWidget()
        scroll.setWidget(content)
        old.deleteLater()


def set_interpolation(viewer, index):
    """Display transform only; label overlays continue to use nearest sampling."""
    for view in viewer.views.values():
        view.canvas.smooth_image = index == 0
        view.canvas.update()


def object_menu(viewer, control, actions):
    """Right-click selects the clicked object before validated existing actions."""
    control.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def show(position):
        item = control.itemAt(position)
        if item is None:
            return
        control.setCurrentItem(item)
        menu = QMenu(control)
        for title, callback in actions:
            menu.addAction(title, lambda _=False, cb=callback: viewer.safe(cb))
        menu.exec(control.viewport().mapToGlobal(position))

    control.customContextMenuRequested.connect(show)


def roi_operation(viewer, operation):
    """Use the existing explicit operation service and restore the editor choice."""
    old = viewer.roi_operation.currentText()
    with QSignalBlocker(viewer.roi_operation):
        viewer.roi_operation.setCurrentText(operation)
    try:
        viewer.apply_roi_operation()
    finally:
        with QSignalBlocker(viewer.roi_operation):
            viewer.roi_operation.setCurrentText(old)
