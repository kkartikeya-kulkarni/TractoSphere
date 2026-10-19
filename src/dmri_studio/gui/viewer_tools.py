"""Scientific viewer interaction tools built on the existing scene services.

No file mutation, registration, tract reconstruction or preprocessing occurs.
Each edit remains a derivative; cross-grid sampling requires an explicit user
statement that the images already occupy the same physical space.
"""

import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QInputDialog,
    QListWidget,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.scene import ImageVolume, TractGroup
from dmri_studio.core.scene_operations import (
    polygon_roi,
    projected_selection,
    read_mesh,
    resample_overlay,
)

from .components import label


class ViewerTools:
    """Optional tools composed into the four-view controller, not another viewer."""

    def setup_tools(self):
        self.layers = []
        self.polygon_vertices = []
        self.polygon_plane = None
        self.last_selection_plane = None
        self.highlight_selected = QCheckBox("Highlight selected fibers")
        self.highlight_selected.setChecked(True)
        self.highlight_selected.toggled.connect(self.refresh_tracts)
        self.tracts_layout.insertWidget(0, self.highlight_selected)
        self.selection_mode = QComboBox()
        self.selection_mode.addItems(["Camera", "Select drag plane", "Select rectangle"])
        self.selection_mode.currentTextChanged.connect(
            lambda mode: setattr(self.scene, "selection_mode", mode)
        )
        self.scene.gesture_callback = lambda a, b, mode: self.safe(
            lambda: self.gesture_select(a, b, mode)
        )
        self.tracts_layout.insertWidget(0, self.selection_mode)
        self.angle = self.spin(0, 90, 90, 5)
        self.angle.setToolTip(
            "Maximum angle from the selection-plane normal; 90 accepts all crossing directions"
        )
        self.tracts_layout.insertWidget(1, label("Selection angle limit (degrees)"))
        self.tracts_layout.insertWidget(2, self.angle)
        self.cut_mode = QComboBox()
        self.cut_mode.addItems(["Keep beginning", "Keep end", "Keep longer part"])
        self.tracts_layout.insertWidget(3, self.cut_mode)
        self.tracts_layout.insertWidget(
            4, self.action("Cut selected at drag plane", lambda: self.cut_drag())
        )
        self.tracts_layout.insertWidget(5, self.action("Rename group", self.rename_group))
        self.tracts_layout.insertWidget(
            6, self.action("Remove group from scene", self.remove_group)
        )
        self.tracts_layout.insertWidget(
            7, self.action("Export bundle statistics", self.export_statistics)
        )
        self.volume_render_check = QCheckBox("Render anatomical volume (ray casting)")
        self.volume_render_check.toggled.connect(lambda _v: self.safe(self.update_volume_render))
        self.display_layout.insertWidget(self.display_layout.count() - 1, self.volume_render_check)
        self.volume_alpha = self.spin(0.01, 1, 0.15, 0.05)
        self.volume_alpha.valueChanged.connect(lambda _v: self.safe(self.update_volume_render))
        self.display_layout.insertWidget(
            self.display_layout.count() - 1, label("Volume rendering opacity")
        )
        self.display_layout.insertWidget(self.display_layout.count() - 1, self.volume_alpha)
        self.surfaces_layout.insertWidget(
            1, self.action("Load GIFTI / FreeSurfer surface", self.choose_mesh)
        )
        self.surfaces_layout.insertWidget(
            self.surfaces_layout.count() - 1,
            self.action("Remove selected surface", self.remove_surface),
        )
        self.brush.addItems(["Draw 3D", "Erase 3D", "Polygon"])
        self.rois_layout.insertWidget(4, self.action("Finish polygon", self.finish_polygon))
        self.rois_layout.insertWidget(5, self.action("Cancel polygon", self.cancel_polygon))
        self.roi_operation = QComboBox()
        self.roi_operation.addItems(
            [
                "Copy",
                "Rename",
                "Remove",
                "Jump to centroid",
                "Threshold image",
                "Fill holes",
                "Largest component",
                "Smooth",
                "Invert",
                "Shift +X",
                "Shift -X",
                "Shift +Y",
                "Shift -Y",
                "Shift +Z",
                "Shift -Z",
                "Flip X",
                "Flip Y",
                "Flip Z",
            ]
        )
        self.rois_layout.insertWidget(self.rois_layout.count() - 1, self.roi_operation)
        self.rois_layout.insertWidget(
            self.rois_layout.count() - 1,
            self.action("Apply ROI operation", self.apply_roi_operation),
        )
        self.rois_layout.insertWidget(
            self.rois_layout.count() - 1,
            self.action("Export ROI statistics", self.export_roi_statistics),
        )
        self.roi_same_space = QCheckBox("ROI already aligned in world space")
        self.roi_same_space.setToolTip(
            "Allows nearest-neighbor resampling across grids. Does not estimate registration or assume subject identity."
        )
        self.rois_layout.insertWidget(1, self.roi_same_space)
        self.layers_panel = QWidget()
        layout = QVBoxLayout(self.layers_panel)
        self.layer_list = QListWidget()
        self.layer_list.currentRowChanged.connect(self.layer_info)
        layout.addWidget(self.layer_list)
        self.layer_same_space = QCheckBox("Overlay already aligned in world space")
        self.layer_same_space.setToolTip(
            "Explicit same-space declaration permits display-only resampling. No registration is inferred."
        )
        layout.addWidget(self.layer_same_space)
        layout.addWidget(self.action("Add scalar overlay", self.choose_layer))
        self.layer_visible = QCheckBox("Selected layer visible")
        self.layer_visible.toggled.connect(self.layer_style)
        layout.addWidget(self.layer_visible)
        self.layer_alpha = self.spin(0, 1, 0.4, 0.05)
        self.layer_alpha.valueChanged.connect(self.layer_style)
        layout.addWidget(label("Layer opacity"))
        layout.addWidget(self.layer_alpha)
        self.layer_low = self.spin(-1e12, 1e12, 0, 0.01)
        self.layer_high = self.spin(-1e12, 1e12, 1, 0.01)
        self.layer_low.valueChanged.connect(self.layer_style)
        self.layer_high.valueChanged.connect(self.layer_style)
        layout.addWidget(label("Layer minimum"))
        layout.addWidget(self.layer_low)
        layout.addWidget(label("Layer maximum"))
        layout.addWidget(self.layer_high)
        self.layer_cmap = QComboBox()
        self.layer_cmap.addItems(["hot", "viridis", "gray", "blue-white-red"])
        self.layer_cmap.currentTextChanged.connect(self.layer_style)
        layout.addWidget(self.layer_cmap)
        layout.addWidget(self.action("Move layer up", lambda: self.move_layer(-1)))
        layout.addWidget(self.action("Move layer down", lambda: self.move_layer(1)))
        layout.addWidget(self.action("Remove selected layer", self.remove_layer))
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.layers_panel)
        self.tabs.addTab(scroll, "Layers")

    def update_volume_render(self):
        if self.data is None:
            return
        if self.volume_render_check.isChecked():
            self.scene.volume_render(
                self.data,
                self.reference,
                self.low.value(),
                self.high.value(),
                self.volume_alpha.value(),
            )
        else:
            self.scene.remove(("volume", 0))
            self.scene.draw()
        self.render_settings()

    def choose_mesh(self):
        if self.reference is None:
            raise ValueError("Open the corresponding image first")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load mesh in matching world space",
            "",
            "Surfaces (*.gii *.white *.pial *.inflated);;All files (*)",
        )
        if path:
            self.load_mesh(path)

    def load_mesh(self, path, state=None):
        ref = self.reference
        if not ref.physical_units_known:
            raise ValueError("Base image requires documented spatial units")

        def ready(result):
            if ref is not self.reference:
                return
            vertices, faces = result
            record = state or {
                "kind": "mesh",
                "path": str(Path(path).resolve()),
                "color": (0.72, 0.78, 0.85),
                "opacity": 0.4,
                "visible": True,
            }
            i = len(self.surfaces)
            self.surfaces.append(record)
            self.surface_list.addItem(Path(path).name + " · imported mesh")
            self.surface_list.setCurrentRow(i)
            self.scene.mesh(("surface", i), vertices, faces, record["color"], record["opacity"])
            self.scene.actors[("surface", i)].SetVisibility(record["visible"])
            self.render_settings()

        self.background(lambda: read_mesh(path), ready)

    def remove_surface(self):
        i = self.surface_list.currentRow()
        if i < 0:
            raise ValueError("Select a surface first")
        self.scene.remove(("surface", i))
        self.surfaces.pop(i)
        self.surface_list.takeItem(i)
        for j in range(i, len(self.surfaces)):
            if ("surface", j + 1) in self.scene.actors:
                self.scene.actors[("surface", j)] = self.scene.actors.pop(("surface", j + 1))
        self.surface_selection(self.surface_list.currentRow())
        self.scene.draw()

    def rename_group(self):
        group = self.current_group()
        if group is None:
            raise ValueError("Select a tract group first")
        text, ok = QInputDialog.getText(
            self, "Group name", "Name", text=self.tract_list.currentItem().text()
        )
        if ok and text.strip():
            self.tract_list.currentItem().setText(text.strip())
            group.name = text.strip()

    def remove_group(self):
        i = self.tract_list.currentRow()
        if i < 0:
            raise ValueError("Select a tract group first")
        self.groups.pop(i)
        self.tract_list.takeItem(i)
        for key in list(self.scene.actors):
            if key[0] == "tract":
                self.scene.remove(key)
        self.refresh_tracts()
        self.status.setText("Group removed from scene; source file unchanged")

    def export_statistics(self):
        group = self.current_group()
        if group is None:
            raise ValueError("Select a group first")
        self.save_json("Bundle statistics", {"source": str(group.path), **group.statistics()})

    def save_json(self, title, value):
        path, _ = QFileDialog.getSaveFileName(self, title, "", "JSON (*.json)")
        if path:
            if not path.endswith(".json"):
                path += ".json"
            with open(path, "x") as f:
                json.dump(value, f, indent=2)
            self.status.setText("Saved " + path)

    def gesture_select(self, start, end, mode):
        group = self.current_group()
        if group is None:
            raise ValueError("Select a tract group first")
        if (end - start).manhattanLength() < 4:
            raise ValueError("Drag across fibers to define a selection")
        r = self.scene.renderer
        camera = r.GetActiveCamera()
        m = camera.GetCompositeProjectionTransformMatrix(r.GetTiledAspectRatio(), -1, 1)
        projection = np.array([[m.GetElement(i, j) for j in range(4)] for i in range(4)])
        # Qt logical pixels -> normalized device coordinates, independent of Retina scale.
        a = np.array(
            [2 * start.x() / self.scene.width() - 1, 1 - 2 * start.y() / self.scene.height()]
        )
        b = np.array([2 * end.x() / self.scene.width() - 1, 1 - 2 * end.y() / self.scene.height()])
        inv = np.linalg.inv(projection)

        def world(xy, z):
            p = inv @ np.r_[xy, z, 1]
            return p[:3] / p[3]

        origin = world(a, -1)
        normal = np.cross(world(b, -1) - origin, world(a, 1) - origin)
        normal /= np.linalg.norm(normal)
        self.last_selection_plane = (origin, normal)
        snapshot = TractGroup(group.path, group.streamlines, group.source_ids, group.total)
        snapshot.deleted = set(group.deleted)
        snapshot.hidden = set(group.hidden)
        snapshot.cuts = dict(group.cuts)
        ref = self.reference
        angle = self.angle.value()

        def ready(selected):
            if self.reference is ref and any(g is group for g in self.groups):
                group.selected = selected - group.deleted
                self.refresh_tracts()
                self.status.setText(f"Selected {len(group.selected):,} fibers in {mode.lower()}")

        self.status.setText("Selecting across indexed fibers…")
        self.background(
            lambda: projected_selection(
                snapshot, projection, a, b, mode == "Select rectangle", angle, normal
            ),
            ready,
        )

    def cut_drag(self):
        if self.last_selection_plane is None:
            raise ValueError("Drag a selection plane in the 3D view first")
        group = self.current_group()
        if group is None:
            raise ValueError("Select a tract group first")
        group.edit("cut", *self.last_selection_plane, keep=self.cut_mode.currentText())
        self.refresh_tracts()

    def cancel_polygon(self):
        self.polygon_vertices = []
        self.polygon_plane = None
        self.status.setText("Polygon cleared")

    def finish_polygon(self):
        i = self.roi_list.currentRow()
        if i < 0 or self.polygon_plane is None:
            raise ValueError("Choose Polygon, then click at least three points in one slice")
        self.roi_checkpoint(i)
        polygon_roi(self.rois[i]["data"], self.polygon_vertices, *self.polygon_plane)
        self.rois[i]["edited"] = True
        self.cancel_polygon()
        self.refresh_slices()
        self.roi_info(i)
        self.schedule_roi_surface(i)

    def apply_roi_operation(self):
        from scipy import ndimage

        operation = self.roi_operation.currentText()
        i = self.roi_list.currentRow()
        if operation == "Threshold image":
            if self.data is None:
                raise ValueError("Open an image first")
            low, ok = QInputDialog.getDouble(
                self, "Threshold ROI", "Lower threshold", self.low.value(), -1e12, 1e12, 4
            )
            if not ok:
                return
            high, ok = QInputDialog.getDouble(
                self, "Threshold ROI", "Upper threshold", self.high.value(), low, 1e12, 4
            )
            if ok:
                self.add_roi(
                    np.isfinite(self.data) & (self.data >= low) & (self.data <= high),
                    "Threshold ROI",
                )
            return
        if i < 0:
            raise ValueError("Select an ROI first")
        roi = self.rois[i]
        if operation == "Copy":
            self.add_roi(roi["data"].copy(), roi["name"] + " copy")
            return
        if operation == "Rename":
            text, ok = QInputDialog.getText(self, "ROI name", "Name", text=roi["name"])
            if ok and text.strip():
                roi["name"] = text.strip()
                self.roi_list.currentItem().setText(text.strip())
            return
        if operation == "Remove":
            self.scene.remove(("roi", i))
            self.rois.pop(i)
            self.roi_list.takeItem(i)
            for j in range(i, len(self.rois)):
                if ("roi", j + 1) in self.scene.actors:
                    self.scene.actors[("roi", j)] = self.scene.actors.pop(("roi", j + 1))
            self.roi_undo.clear()
            self.roi_redo.clear()
            if hasattr(self, "_roi_dirty"):
                self._roi_dirty.clear()
            self.refresh_slices()
            self.scene.draw()
            return
        if operation == "Jump to centroid":
            points = np.argwhere(roi["data"])
            if not len(points):
                raise ValueError("ROI is empty")
            self.navigate(np.rint(points.mean(axis=0)).astype(int))
            return
        self.roi_checkpoint(i)
        data = roi["data"]
        if operation == "Fill holes":
            data = ndimage.binary_fill_holes(data)
        elif operation == "Largest component":
            labels, n = ndimage.label(data)
            if n:
                counts = np.bincount(labels.ravel())
                counts[0] = 0
                data = labels == np.argmax(counts)
        elif operation == "Smooth":
            data = ndimage.gaussian_filter(data.astype(float), 1) >= 0.5
        elif operation == "Invert":
            data = ~data.astype(bool)
        elif operation.startswith("Shift"):
            axis = "XYZ".index(operation[-1])
            shift = [0, 0, 0]
            shift[axis] = 1 if "+" in operation else -1
            data = ndimage.shift(data, shift, order=0, mode="constant", cval=0, prefilter=False)
        elif operation.startswith("Flip"):
            data = np.flip(data, axis="XYZ".index(operation[-1]))
        roi["data"] = np.asarray(data, np.uint8)
        roi["edited"] = True
        self.refresh_slices()
        self.roi_info(i)
        self.schedule_roi_surface(i)

    def export_roi_statistics(self):
        if not self.rois:
            raise ValueError("Load or create ROIs first")
        records = []
        for roi in self.rois:
            mask = roi["data"] > 0
            values = self.data[mask]
            finite = values[np.isfinite(values)]
            records.append(
                {
                    "name": roi["name"],
                    "voxels": int(mask.sum()),
                    "volume_mm3": float(
                        mask.sum() * abs(np.linalg.det(self.reference.affine[:3, :3]))
                    )
                    if self.reference.physical_units_known
                    else None,
                    "mean": float(finite.mean()) if len(finite) else None,
                    "std": float(finite.std()) if len(finite) else None,
                    "image": str(self.reference.path),
                    "volume_index": self.volume.value(),
                }
            )
        self.save_json("ROI statistics", records)

    def choose_layer(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Add aligned scalar overlay", "", "NIfTI (*.nii *.nii.gz)"
        )
        if path:
            self.load_layer(path, allow_resample=self.layer_same_space.isChecked())

    def load_layer(self, path, allow_resample=False, state=None):
        ref = self.reference
        if ref is None:
            raise ValueError("Open a base image first")

        def read():
            image = ImageVolume(Path(path))
            same = image.shape[:3] == ref.shape[:3] and np.allclose(
                image.image.affine, ref.image.affine
            )
            lossless = None
            if (
                not same
                and image.physical_units_known
                and ref.physical_units_known
                and image.affine_source != "fallback"
                and ref.affine_source != "fallback"
            ):
                from dmri_studio.core._mif_checks import rearrange, voxel_mapping

                try:
                    axes, flips = voxel_mapping(image.affine, image.shape, ref.affine, ref.shape)
                except ValueError:
                    pass
                else:
                    # MRtrix exports may have a lossless voxel-axis flip relative
                    # to the NIfTI background. Reindex the display view, never
                    # interpolate fractions or ask for an identity registration.
                    if image.count != 1:
                        raise ValueError("Choose a 3D overlay")
                    lossless = dict(axes=list(axes), flips=list(flips))
                    return rearrange(image.volume(), axes, flips), lossless
            if not same and not allow_resample:
                raise ValueError(
                    "Different grid: explicitly confirm same world space before display resampling"
                )
            return resample_overlay(image, ref, order=1), lossless

        def ready(result):
            from dmri_studio.core.viewer_display import robust_range

            if ref is not self.reference:
                return
            data, lossless = result
            lo, hi = robust_range(data)
            item = state or {
                "path": str(Path(path).resolve()),
                "visible": True,
                "opacity": 0.4,
                "low": lo,
                "high": hi,
                "cmap": "hot",
                "resampled_same_space": allow_resample and lossless is None,
                "lossless_axis_reindexing": lossless,
            }
            self.layers.append({**item, "data": data})
            self.layer_list.addItem(Path(path).name)
            self.layer_list.setCurrentRow(len(self.layers) - 1)
            self.refresh_slices()

        self.background(read, ready)

    def layer_info(self, index):
        if 0 <= index < len(self.layers):
            layer = self.layers[index]
            for control, key in [
                (self.layer_alpha, "opacity"),
                (self.layer_low, "low"),
                (self.layer_high, "high"),
            ]:
                with QSignalBlocker(control):
                    control.setValue(layer[key])
            with QSignalBlocker(self.layer_visible):
                self.layer_visible.setChecked(layer["visible"])
            with QSignalBlocker(self.layer_cmap):
                self.layer_cmap.setCurrentText(layer["cmap"])

    def layer_style(self, *_):
        i = self.layer_list.currentRow()
        if 0 <= i < len(self.layers):
            self.layers[i].update(
                opacity=self.layer_alpha.value(),
                low=self.layer_low.value(),
                high=self.layer_high.value(),
                visible=self.layer_visible.isChecked(),
                cmap=self.layer_cmap.currentText(),
            )
            self.refresh_slices()

    def move_layer(self, delta):
        i = self.layer_list.currentRow()
        j = i + delta
        if not 0 <= j < len(self.layers):
            return
        self.layers[i], self.layers[j] = self.layers[j], self.layers[i]
        item = self.layer_list.takeItem(i)
        self.layer_list.insertItem(j, item)
        self.layer_list.setCurrentRow(j)
        self.refresh_slices()

    def remove_layer(self):
        i = self.layer_list.currentRow()
        if i < 0:
            raise ValueError("Select a layer first")
        self.layers.pop(i)
        self.layer_list.takeItem(i)
        self.refresh_slices()
