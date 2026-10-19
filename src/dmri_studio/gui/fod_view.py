"""FOD tools extending the existing slice/VTK viewer, never a separate viewer.

Workers own read-only mappings and paint independent QImages; Qt widgets and VTK
actors are touched only in terminal GUI callbacks. Generation checks reject old
loads, cancelled work and old crosshair/parameter requests.
"""

import time
from pathlib import Path

import nibabel as nib
import numpy as np
from PySide6.QtCore import QPointF, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.fod import FODImage, glyphs, project_glyphs, region_positions, sphere_mesh
from dmri_studio.core.preview_geometry import plane_geometry
from dmri_studio.core.scene import ImageVolume

from .components import label
from .operation_state import OperationState, State
from .workstation import inspector_section


class CoefficientBackground(ImageVolume):
    """C00 display proxy on original geometry, explicitly not anatomical intensity."""

    def __init__(self, model, data):
        self.path = model.path
        self.image = nib.Nifti1Image(data, model.affine)
        self.image.header.set_xyzt_units("mm")
        self.image.set_sform(model.affine, 1)
        self.image.set_qform(model.affine, 1)
        self.affine_source = "FOD geometry; C00 coefficient proxy"


def slice_overlay(mesh, reference, geometry, indices, opacity):
    """Orthographically project and depth-sort real radial triangles into a QImage.

    Native pixel geometry and zoom/pan are inherited from SliceCanvas. This is
    display rasterization only, with no interpolation/resampling of MRI data.
    """
    dimensions = np.array([geometry.shape[geometry.horizontal], geometry.shape[geometry.vertical]])
    size = tuple(np.maximum(1, np.rint(dimensions * min(4.0, 1024 / max(dimensions)))).astype(int))
    image = QImage(*size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    if not len(indices):
        return image
    projected = project_glyphs(mesh, reference, geometry)[indices]
    faces = mesh.faces
    polygons = projected[:, faces, :].reshape(-1, 3, 3)
    colors = mesh.colors[faces].mean(axis=1)
    colors = np.tile(colors, (len(indices), 1))
    # Lighting follows actual world-space surface normals, not theme colors.
    world = mesh.vertices[indices][:, faces, :].reshape(-1, 3, 3)
    normals = np.cross(world[:, 1] - world[:, 0], world[:, 2] - world[:, 0])
    norms = np.linalg.norm(normals, axis=1)
    lighting = 0.48 + 0.52 * np.abs(normals @ np.array([0.3, -0.4, 0.866])) / np.maximum(
        norms, 1e-20
    )
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setOpacity(opacity)
    for i in np.argsort(polygons[:, :, 2].mean(axis=1)):
        rgb = np.clip(colors[i] * lighting[i], 0, 255).astype(int)
        painter.setBrush(QColor(*map(int, rgb)))
        painter.drawPolygon(
            QPolygonF([QPointF(float(p[0] * size[0]), float(p[1] * size[1])) for p in polygons[i]])
        )
    painter.end()
    return image


class FODTools:
    """Mix-in for ScientificViewer, sharing its pool, navigation, scene and status."""

    def setup_fod(self):
        self.fod = None
        self.fod_generation = 0
        self.fod_request = 0
        self.fod_state = OperationState(self)
        self.fod_metrics = {}
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        index = self.tabs.addTab(scroll, "FODs")
        self.fod_tab = index
        self.inspector_choice.addItem("FOD visualization", index)
        self.fod_summary = label("No FOD open", "caption")
        self.fod_summary.setWordWrap(True)
        self.fod_info = label("No FOD open · MRtrix3 real-even SH required", "caption")
        self.fod_info.setWordWrap(True)
        self.fod_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.fod_status = label("", "caption")
        self.fod_status.setWordWrap(True)
        self.fod_state.bind(self.fod_status)
        self.fod_show = QCheckBox("Show FODs")
        self.fod_show.setEnabled(False)
        self.fod_show.setToolTip("Open a supported FOD image before enabling its glyphs")
        self.fod_mode = QComboBox()
        self.fod_mode.addItems(["Selected voxel", "Slice region", "Selected ROI region"])
        self.fod_color = QComboBox()
        self.fod_color.addItems(["Direction RGB", "Neutral"])
        self.fod_scaling = QComboBox()
        self.fod_scaling.addItems(["Relative shape", "Shared region", "Amplitude (mm/unit)"])
        self.fod_scale = self.spin(0.001, 100, 3.0, 0.25)
        self.fod_spacing = QSpinBox()
        self.fod_spacing.setRange(1, 32)
        self.fod_spacing.setValue(6)
        self.fod_limit = QSpinBox()
        self.fod_limit.setRange(1, 512)
        self.fod_limit.setValue(90)
        self.fod_radius = QSpinBox()
        self.fod_radius.setRange(1, 100)
        self.fod_radius.setValue(24)
        self.fod_quality = QComboBox()
        self.fod_quality.addItems(
            [
                "Interactive · 66 directions",
                "Balanced · 258 directions",
                "Detailed · 1026 directions",
            ]
        )
        self.fod_quality.setCurrentIndex(1)
        self.fod_opacity = self.spin(0.05, 1, 1, 0.05)
        self.fod_isolate = QCheckBox("Hide MRI planes in 3D")
        self.fod_isolate.setChecked(True)
        self.fod_selected = label("Select a voxel in any linked slice.", "caption")
        self.fod_selected.setWordWrap(True)
        self.fod_timer = QTimer(self)
        self.fod_timer.setSingleShot(True)
        self.fod_timer.setInterval(100)
        self.fod_timer.timeout.connect(self.render_fod)
        layout.addWidget(
            inspector_section(
                "FOD image",
                widgets=[
                    self.action("Open FOD…", self.choose_fod),
                    self.fod_summary,
                    self.fod_show,
                    self.fod_status,
                ],
            )
        )
        self.fod_metadata_section = inspector_section(
            "FOD metadata", widgets=[self.fod_info], advanced=True
        )
        layout.addWidget(self.fod_metadata_section)
        self.fod_cancel = self.action("Cancel FOD operation", self.cancel_fod)
        self.fod_cancel.hide()
        layout.addWidget(self.fod_cancel)
        self.fod_state.changed.connect(lambda: self.fod_cancel.setVisible(self.fod_state.busy))
        layout.addWidget(
            inspector_section(
                "Display",
                rows=[
                    ("Region", self.fod_mode),
                    ("Color", self.fod_color),
                    ("Scale mode", self.fod_scaling),
                    ("Glyph scale", self.fod_scale),
                    ("Spacing · voxels", self.fod_spacing),
                ],
                widgets=[
                    self.fod_isolate,
                    self.action("Inspect selected in 3D", self.focus_fod),
                    self.fod_selected,
                ],
            )
        )
        note = label(
            "R: left–right · G: anterior–posterior · B: superior–inferior (world RAS).\nRadius uses max(sampled amplitude, 0) for display only. Raw amplitudes and SH coefficients are unchanged. Relative shape normalizes each glyph; shared region preserves relative peaks within the displayed region. Finite angular sampling is not a positivity proof.",
            "caption",
        )
        note.setWordWrap(True)
        layout.addWidget(
            inspector_section(
                "Advanced",
                rows=[
                    ("Angular sampling", self.fod_quality),
                    ("Maximum glyphs", self.fod_limit),
                    ("Region radius · voxels", self.fod_radius),
                    ("Opacity", self.fod_opacity),
                ],
                widgets=[note],
                advanced=True,
            )
        )
        layout.addWidget(self.action("Open associated Stage 3 QC…", self.choose_fod_qc))
        layout.addStretch()
        for name in ("mode", "color", "scaling", "quality"):
            getattr(self, "fod_" + name).currentIndexChanged.connect(self.schedule_fod)
        for name in ("scale", "spacing", "limit", "radius", "opacity"):
            getattr(self, "fod_" + name).valueChanged.connect(self.schedule_fod)
        self.roi_list.currentRowChanged.connect(
            lambda *_: self.schedule_fod() if self.fod_mode.currentIndex() == 2 else None
        )
        self.editing_changed.connect(
            lambda: self.schedule_fod() if self.fod_mode.currentIndex() == 2 else None
        )
        self.fod_scale.setSuffix(" mm")
        self.fod_scale.setToolTip(
            "Relative/shared: peak radius in mm. Amplitude mode: millimetres per sampled amplitude unit. Source values are unchanged."
        )
        self.fod_scaling.currentTextChanged.connect(
            lambda text: self.fod_scale.setSuffix(
                " mm/unit" if text == "Amplitude (mm/unit)" else " mm"
            )
        )
        self.fod_show.toggled.connect(self.schedule_fod)
        self.fod_isolate.toggled.connect(self.schedule_fod)
        self.fod_state.changed.connect(
            lambda: self.fod_status.setToolTip(
                "\n".join(h["message"] for h in self.fod_state.history[-8:])
            )
        )
        # Top-aligned labels avoid clipped scientific names in a narrow inspector.
        for form in content.findChildren(QFormLayout):
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.fod_first_fit = False
        self.fod_camera_center = None
        self.fod_qc_callback = None

    def choose_fod(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open declared MRtrix3 real-even world-RAS FOD",
            "",
            "SH coefficient images (*.mif *.nii *.nii.gz)",
        )
        if path:
            self.load_fod(path)

    def choose_fod_qc(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open associated Stage 3 QC evidence", "", "QC reports (*.json)"
        )
        if path and self.fod_qc_callback:
            self.fod_qc_callback(Path(path))
        elif path:
            self.fod_status.setText("Use Quality Control → Open Report for this evidence file")

    def clear_fod_display(self):
        self.fod_request += 1
        self.fod_timer.stop()
        for view in self.views.values():
            view.canvas.fod_image = None
            view.canvas.update()
        self.scene.remove(("fod", 0))
        for actor, check in zip(self.scene.planes, self.plane_checks, strict=False):
            actor.SetVisibility(check.isChecked())
        if self.scene.planes:
            self.scene.draw()

    def cancel_fod(self):
        """Cancel this presentation without leaving an Opening label or live glyph."""
        self.fod_generation += 1
        self.clear_fod_display()
        if self.fod is None:
            self.fod_summary.setText("No FOD active · cancelled")
            self.fod_info.setText("FOD import cancelled · source unchanged")
        self.status.setText("FOD request cancelled · source unchanged")
        self.fod_state.update(
            self.fod_state.token, State.CANCELLED, "FOD display cancelled · source unchanged"
        )

    def load_fod(
        self,
        path,
        *,
        expected_hash=None,
        basis="mrtrix3_real_symmetric",
        frame="world_ras",
        settings=None,
    ):
        if self.image_loading:
            self.volume_generation += 1
            self.image_loading = False
            self.image_state.update(
                self.image_state.token, State.CANCELLED, "Image request superseded by FOD import"
            )
        self.fod_generation += 1
        generation = self.fod_generation
        self.clear_fod_display()
        self.fod = None
        self.fod_show.setEnabled(False)
        self.fod_camera_center = None
        self.fod_summary.setText("Opening " + Path(path).name)
        self.fod_info.setText("Opening " + Path(path).name)
        self.fod_selected.setText("No voxel sampled from this input yet.")
        self.tabs.setCurrentIndex(self.fod_tab)
        self.status.setText("Opening FOD · " + Path(path).name)
        token = self.fod_state.begin(State.LOADING, "Reading FOD header and source hash…")
        started = time.perf_counter()
        reference = self.reference

        def read():
            model = FODImage(
                path,
                basis=basis,
                frame=frame,
                expected_hash=expected_hash,
                cancelled=lambda: generation != self.fod_generation,
            )
            if reference is not None and not isinstance(reference, CoefficientBackground):
                from dmri_studio.core.atlas import geometry_record

                geometry_record(reference)
                model.reference_indices(reference, [[0, 0, 0]])
                return model, None
            return model, model.c00()

        def failed(error):
            self.pending_restore = None
            self.fod = None
            self.fod_summary.setText("No FOD active · import failed")
            self.fod_info.setText("No FOD active · import failed")
            self.status.setText(
                "Operation failed: FOD import · details in the FOD inspector"
                if self.reference is not None
                else "Operation failed: FOD import · " + error
            )
            self.fod_state.update(token, State.FAILED, "FOD import failed: " + error)

        def ready(value):
            model, c00 = value
            self.fod = model
            self.fod_show.setEnabled(True)
            self.status.setText(
                model.path.name + " · SH coefficients imported · scientific acceptance unchanged"
            )
            self.fod_metrics["load_seconds"] = time.perf_counter() - started
            if c00 is not None:
                self.groups.clear()
                self.rois.clear()
                self.surfaces.clear()
                self.layers.clear()
                self.layer_list.clear()
                self.tract_list.clear()
                self.roi_list.clear()
                self.surface_list.clear()
                for key in list(self.scene.actors):
                    self.scene.remove(key)
                self.reference = CoefficientBackground(model, c00)
                self.data = c00
                self.dti_direction = None
                self.dti_direction_path = None
                self.position = tuple(n // 2 for n in c00.shape)
                self.volume.setRange(0, 0)
                self.auto_contrast()
                self.refresh_volume()
            self.fod_summary.setText(
                f"{model.path.name} · lmax {model.degree} · {model.shape[3]} coefficients"
            )
            self.fod_info.setText(
                f"{model.path.name}\n{model.shape} · {model.shape[3]} coefficients · lmax {model.degree}\nMRtrix3 orthonormal real-even SH\nWorld RAS directions · coefficient order l even, m −l…+l\nBackground: {self.reference.path.name}"
                + (
                    " · C00 coefficient proxy"
                    if isinstance(self.reference, CoefficientBackground)
                    else " · exact physical grid verified; subject/registration provenance still required"
                )
            )
            self.fod_info.setToolTip(str(model.path) + "\nSHA-256 " + model.sha256)
            if settings:
                self.restore_fod_settings(settings)
            self.fod_first_fit = not bool(settings)
            if not settings:
                self.fod_show.setChecked(True)
            self.tabs.setCurrentIndex(self.fod_tab)
            if not settings:
                self.layout_choice.setCurrentText("Four views")
            self.fod_state.update(
                token, State.READY, "FOD parsed · scientific acceptance unchanged"
            )
            self.schedule_fod()
            self.update_content_state()
            self.image_loaded.emit()
            if self.pending_restore:
                record, self.pending_restore = self.pending_restore, None
                self.restore_objects(record)

        self.background(
            read, ready, current=lambda: generation == self.fod_generation, on_error=failed
        )

    def fod_reference_changed(self):
        """Retain FOD only across verified same-grid scalar background replacement."""
        if self.fod is None:
            return
        try:
            from dmri_studio.core.atlas import geometry_record

            geometry_record(self.reference)
            self.fod.reference_indices(self.reference, [[0, 0, 0]])
            self.fod_info.setText(
                self.fod_info.text().split("\nBackground:")[0]
                + "\nBackground: "
                + self.reference.path.name
                + " · physical grid verified"
            )
            self.schedule_fod()
        except ValueError as exc:
            self.fod = None
            self.clear_fod_display()
            self.fod_summary.setText("FOD detached · background geometry differs")
            self.fod_info.setText(
                "FOD detached: background geometry changed. Reopen a compatible FOD."
            )
            token = self.fod_state.begin(State.VALIDATING, "Checking FOD/background geometry")
            self.fod_state.update(
                token, State.WARNING, str(exc) + " No registration or resampling was performed."
            )

    def schedule_fod(self, *_):
        if not hasattr(self, "fod"):
            return
        self.clear_fod_display()
        if self.fod is not None and self.fod_show.isChecked() and not self.image_loading:
            self.fod_timer.start()
        else:
            self.fod_state.update(
                self.fod_state.token,
                State.CANCELLED,
                "FOD overlay disabled or superseded · source unchanged",
            )

    def render_fod(self):
        if self.fod is None or not self.fod_show.isChecked():
            return
        request = self.fod_request
        model, reference, position = self.fod, self.reference, self.position
        geometries = [plane_geometry(reference, o) for o in self.views]
        settings = dict(
            quality=self.fod_quality.currentIndex(),
            scale=self.fod_scale.value(),
            scaling=self.fod_scaling.currentText(),
            color=self.fod_color.currentText(),
        )
        roi = None
        if self.fod_mode.currentIndex() == 2:
            i = self.roi_list.currentRow()
            if i < 0:
                token = self.fod_state.begin(State.VALIDATING, "Checking ROI selection")
                self.fod_state.update(
                    token,
                    State.WARNING,
                    "Select a compatible ROI in Atlas & ROIs before regional FOD display",
                )
                return
            roi = self.rois[i]["data"]
        regional = self.fod_mode.currentIndex() != 0
        spacing, radius = self.fod_spacing.value(), self.fod_radius.value()
        maximum = min(self.fod_limit.value(), 262144 // len(sphere_mesh(settings["quality"])[1]))
        opacity = self.fod_opacity.value()
        started = time.perf_counter()
        token = self.fod_state.begin(State.PROCESSING, "Sampling FODs and preparing display…")

        def build():
            if request != self.fod_request:
                return None
            positions = (
                region_positions(
                    reference,
                    position,
                    geometries,
                    spacing=spacing,
                    maximum=maximum,
                    radius=radius,
                    roi=roi,
                )
                if regional
                else np.array([position])
            )
            if not len(positions):
                return "empty"
            selected = model.reference_indices(reference, [position])
            indices = model.reference_indices(reference, positions)
            mesh = glyphs(model, indices, **settings)
            single = glyphs(model, selected, shared_peak=mesh.peak, **settings)
            overlays = {}
            for o, g in zip(self.views, geometries, strict=True):
                if request != self.fod_request:
                    return None
                ids = np.flatnonzero(positions[:, g.axis] == position[g.axis])
                overlays[o] = slice_overlay(mesh, reference, g, ids, opacity)
            return single, overlays, len(positions), time.perf_counter() - started

        def ready(value):
            if value is None:
                return
            if isinstance(value, str) and value == "empty":
                self.fod_state.update(
                    token, State.WARNING, "No ROI voxels in the selected slice region"
                )
                return
            single, overlays, count, elapsed = value
            for o, image in overlays.items():
                self.views[o].canvas.fod_image = image
                self.views[o].canvas.update()
            camera = self.scene.renderer.GetActiveCamera()
            if getattr(self, "fod_camera_center", None) is not None and not self.fod_first_fit:
                # Follow crosshair translation without losing user rotation/zoom.
                delta = single.centers[0] - self.fod_camera_center
                camera.SetPosition(*(np.array(camera.GetPosition()) + delta))
                camera.SetFocalPoint(*(np.array(camera.GetFocalPoint()) + delta))
            self.fod_camera_center = single.centers[0].copy()
            self.scene.fod_mesh(single, opacity)
            self.scene.renderer.ResetCameraClippingRange()
            self.apply_fod_planes()
            if self.fod_first_fit:
                self.fod_first_fit = False
                self.focus_fod()
            values = single.amplitudes
            self.fod_metrics.update(
                render_seconds=elapsed, glyph_count=count, mesh_vertices=len(single.colors)
            )
            minimum, maximum = float(values.min()), float(values.max())
            negative = int(
                np.count_nonzero(values < -1e-7 * max(float(np.max(np.abs(values))), 1e-20))
            )
            world = reference.world(position)
            self.fod_selected.setText(
                f"Voxel {position}\nRAS mm {tuple(float(round(w, 3)) for w in world)}\nSampled amplitude {minimum:.6g} … {maximum:.6g}\nNegative samples beyond relative 1e−7 review floor: {negative}/{values.size}\nUntruncated samples retained; radius max(a,0).\n{count} glyphs · {elapsed:.3f} s · {settings['scaling']}"
            )
            sampling_warning = len(single.colors) < 2 * model.shape[3]
            zero = maximum <= 0
            self.fod_state.update(
                token,
                State.WARNING if negative or sampling_warning or zero else State.READY,
                "Display ready · sampled negative amplitudes require review"
                if negative
                else (
                    "Zero positive sampled amplitude at this voxel"
                    if zero
                    else "Sampling is coarse for this SH degree; choose Detailed quality"
                    if sampling_warning
                    else "Display ready · not scientific acceptance"
                ),
            )

        def failed(error):
            self.clear_fod_display()
            self.fod_state.update(token, State.FAILED, "FOD display failed: " + error)

        self.background(
            build,
            ready,
            current=lambda: request == self.fod_request and model is self.fod,
            on_error=failed,
        )

    def apply_fod_planes(self):
        if (
            hasattr(self, "fod_isolate")
            and self.fod is not None
            and self.fod_show.isChecked()
            and self.fod_isolate.isChecked()
        ):
            for actor in self.scene.planes:
                actor.SetVisibility(False)
            self.scene.draw()

    def focus_fod(self):
        """Fit the selected physical voxel's glyph, retaining standard scene controls."""
        if self.fod is None or self.reference is None:
            return
        self.layout_choice.setCurrentText("Four views")
        center = self.reference.world(self.position)
        radius = max(self.fod_scale.value(), 0.1)
        camera = self.scene.renderer.GetActiveCamera()
        direction = np.array([1.0, -1.0, 0.65])
        direction /= np.linalg.norm(direction)
        camera.SetFocalPoint(*center)
        camera.SetPosition(*(center + direction * radius * 5))
        camera.SetViewUp(0, 0, 1)
        camera.SetParallelScale(radius * 1.4)
        self.scene.renderer.ResetCameraClippingRange()
        self.scene.draw()

    def fod_settings(self):
        if self.fod is None:
            return None
        return dict(
            path=str(self.fod.path),
            sha256=self.fod.sha256,
            show=self.fod_show.isChecked(),
            mode=self.fod_mode.currentText(),
            color=self.fod_color.currentText(),
            scaling=self.fod_scaling.currentText(),
            quality=self.fod_quality.currentIndex(),
            scale=self.fod_scale.value(),
            spacing=self.fod_spacing.value(),
            limit=self.fod_limit.value(),
            radius=self.fod_radius.value(),
            opacity=self.fod_opacity.value(),
            isolate=self.fod_isolate.isChecked(),
        )

    def restore_fod_settings(self, record):
        for name in ("mode", "color", "scaling"):
            control = getattr(self, "fod_" + name)
            if control.findText(record[name]) < 0:
                raise ValueError("Unsupported saved FOD display setting")
            control.setCurrentText(record[name])
        self.fod_quality.setCurrentIndex(record["quality"])
        for name in ("scale", "spacing", "limit", "radius", "opacity"):
            getattr(self, "fod_" + name).setValue(record[name])
        self.fod_isolate.setChecked(record["isolate"])
        with QSignalBlocker(self.fod_show):
            self.fod_show.setChecked(record["show"])
