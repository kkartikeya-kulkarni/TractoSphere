"""Linked native-plane preview with bounded, debounced background reads.

One shared voxel position drives all canvases and sliders. At most one plane
read runs at a time (DwiDataset serializes proxy access); each view keeps only
its last plane and request key. No full volume or 4D image is materialized.
"""

import math

import numpy as np
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPainter, QPen, QPolygonF, QShortcut
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QSlider, QTabBar, QVBoxLayout, QWidget

from dmri_studio.core.dataset import DwiDataset
from dmri_studio.core.preview_geometry import (
    ORIENTATIONS,
    Orientation,
    PlaneGeometry,
    plane_geometry,
    world_position,
)
from dmri_studio.core.viewer_display import robust_range

from .components import Card, Disclosure, button, label
from .overlay_controls import OverlayControls, number_field
from .settings import Settings
from .slice_canvas import SliceCanvas
from .theme import Theme, font
from .workers import SliceWorker, ViewerTaskWorker


class GradientSphere(QWidget):
    """Static orthographic projection of supplied weighted directions only."""

    MARKERS = ("●", "■", "▲", "◆", "+", "×")

    def __init__(self, theme: Theme) -> None:
        """Use the card surface and the approved categorical group colors."""
        super().__init__()
        self.theme, self.dataset = theme, None
        self.groups: list[object] = []
        self.setMinimumHeight(176)
        self.setAccessibleName("Gradient directions: supplied FSL x, y, z components")
        theme.changed.connect(self.update)

    @staticmethod
    def project(points: np.ndarray) -> np.ndarray:
        """Use a fixed orthographic camera; this is not a world-coordinate transform."""
        horizontal = np.array([1 / math.sqrt(2), -1 / math.sqrt(2), 0])
        vertical = np.array([1 / math.sqrt(6), 1 / math.sqrt(6), -2 / math.sqrt(6)])
        return np.asarray(points) @ np.stack([horizontal, vertical]).T

    def set_dataset(self, dataset: DwiDataset) -> None:
        """Ask core for group membership, preserving vectors and original b-values."""
        self.dataset = dataset
        policy = dataset.validation.policy
        self.groups = [
            g
            for g in dataset.gradients.shells(b0_max=policy.b0_max, max_span=policy.shell_span)
            if not g.is_b0
        ]
        self.update()

    def paintEvent(self, event: object) -> None:
        """Draw a wire sphere, gradient-component axes and distinct group markers."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        radius = max(0, min(self.width() - 48, self.height() - 48)) * 0.36

        def point(value: np.ndarray) -> QPointF:
            """Convert projected unit coordinates to widget coordinates."""
            return center + QPointF(float(value[0] * radius), float(value[1] * radius))

        painter.setPen(QPen(self.theme.color("border.subtle"), 1))
        painter.drawEllipse(center, radius, radius)
        angles = np.linspace(0, 2 * np.pi, 100)
        for axis in range(3):
            circle = np.zeros((100, 3))
            circle[:, (axis + 1) % 3] = np.cos(angles)
            circle[:, (axis + 2) % 3] = np.sin(angles)
            painter.drawPolyline(QPolygonF([point(v) for v in self.project(circle)]))
        painter.setFont(font("caption"))
        for name, tip in zip("xyz", self.project(np.eye(3) * 1.2), strict=True):
            painter.setPen(QPen(self.theme.color("text.muted"), 1))
            painter.drawLine(center, point(tip))
            painter.drawText(point(tip) + QPointF(4, -4), name)
        if self.dataset is None:
            return
        for index, group in enumerate(self.groups):
            vectors = self.dataset.gradients.bvecs[list(group.volume_indices)]
            vectors = vectors / np.linalg.norm(vectors, axis=1)[:, None]
            color = self.theme.color(f"gradient.group.{index % 6 + 1}")
            painter.setPen(QPen(color, 1.5))
            painter.setBrush(color)
            for projected in self.project(vectors):
                p = point(projected)
                shape = index % 6
                if shape == 0:
                    painter.drawEllipse(p, 2.5, 2.5)
                elif shape == 1:
                    painter.drawRect(QRectF(p.x() - 2.5, p.y() - 2.5, 5, 5))
                elif shape in (2, 3):
                    offsets = (
                        [(0, -3), (3, 3), (-3, 3)]
                        if shape == 2
                        else [(0, -3), (3, 0), (0, 3), (-3, 0)]
                    )
                    painter.drawPolygon(QPolygonF([p + QPointF(x, y) for x, y in offsets]))
                else:
                    painter.drawText(p, self.MARKERS[shape])
        if not self.groups:
            painter.setPen(self.theme.color("text.muted"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "No diffusion-weighted directions"
            )


class SliceView(QWidget):
    """One titled canvas, slice slider and bounded plane cache."""

    escape_requested = Signal()

    def __init__(self, theme: Theme, orientation: Orientation) -> None:
        """Build independent view controls; the parent owns shared position state."""
        super().__init__()
        self.orientation = orientation
        self.geometry: PlaneGeometry | None = None
        self.plane: np.ndarray | None = None
        self.completed_key: tuple[int, int, int] | None = None
        self.desired_key: tuple[int, int, int] | None = None
        self.error = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        row = QHBoxLayout()
        self.title = label(orientation.title(), "control")
        row.addWidget(self.title, 1)
        self.reset = button("", quiet=True)
        self.reset.setAccessibleName(f"Reset {orientation} view")
        theme.set_icon(self.reset, "rotate-ccw")
        self.reset.setFixedWidth(30)
        self.reset.setToolTip("Restore fit-to-view for this canvas")
        row.addWidget(self.reset)
        self.expand = button("", quiet=True)
        self.expand.setFixedWidth(30)
        self.expand.setToolTip(f"Expand {orientation} view to fill the workspace")
        self.expand.setAccessibleName(f"Expand {orientation} view")
        theme.set_icon(self.expand, "maximize")
        row.addWidget(self.expand)
        layout.addLayout(row)
        self.geometry_note = label("", "caption")
        layout.addWidget(self.geometry_note)
        self.geometry_note.hide()
        self.canvas = SliceCanvas(theme)
        self.reset.clicked.connect(self.canvas.reset_view)
        layout.addWidget(self.canvas, 1)
        self.slice_label = label("", "caption")
        layout.addWidget(self.slice_label)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setAccessibleName(f"{orientation.title()} native slice")
        self.slider.setToolTip("Select a native slice; the linked crosshair moves with it.")
        layout.addWidget(self.slider)
        self.range_label = label("", "caption")
        # Detailed contrast information is available in the canvas tooltip,
        # avoiding three extra wrapping text rows under small images.
        self.range_label.hide()
        layout.addWidget(self.range_label)
        self.slider.installEventFilter(self)
        self.expand.installEventFilter(self)
        self.setFocusProxy(self.canvas)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Keep view arrow-key semantics even when its slider/button has focus."""
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            # A native focus transition immediately after reparenting can send
            # the key to a control before Qt updates the shortcut focus chain.
            # Handle the delivered key locally as well as the normal shortcut.
            self.escape_requested.emit()
            return True
        if event.type() == QEvent.Type.KeyPress and event.key() in (
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
        ):
            self.canvas.keyPressEvent(event)
            return True
        return super().eventFilter(watched, event)


class Preview(Card):
    """Share voxel/volume state and reject stale reads independently for every view."""

    idle = Signal()
    expanded_changed = Signal(bool)
    navigation_changed = Signal(object, int)
    planes_changed = Signal()

    def __init__(self, theme: Theme, settings: Settings | None = None) -> None:
        """Compose three views, shared controls and the unchanged gradient sphere."""
        super().__init__(theme)
        self.theme, self.settings = theme, settings or Settings()
        self.dataset: DwiDataset | None = None
        self.position = (0, 0, 0)
        self.generation = 0
        self.request = 0
        self.thread: QThread | None = None
        self.worker: SliceWorker | None = None
        self.active_view: SliceView | None = None
        self.active_key: tuple[int, int, int] | None = None
        self.expanded: Orientation | None = None
        self.active_overlay = None
        self.display_range = (0.0, 1.0)
        self.initial_range = None
        self.auto_requested = True
        self.body.setAlignment(Qt.AlignmentFlag(0))
        self.body.setSpacing(8)
        header = QHBoxLayout()
        self.title = label("Native image workspace", "section")
        header.addWidget(self.title, 1)
        self.convention = button("Neurological")
        self.convention.setCheckable(True)
        self.convention.setChecked(bool(self.settings.get("preview/radiological", False)))
        self.convention.setToolTip(
            "Patient right on screen left in axial/coronal views. Sagittal keeps anterior left. Display only."
        )
        self.convention.toggled.connect(self.change_convention)
        self.tools_toggle = button("Display && overlays")
        self.tools_toggle.setCheckable(True)
        self.tools_toggle.setChecked(False)
        self.tools_toggle.setToolTip("Show or hide contrast and overlay controls")
        header.addWidget(self.tools_toggle)
        header.addWidget(self.convention)
        self.body.addLayout(header)
        self.convention_label = label("", "caption")
        self.body.addWidget(self.convention_label)
        self.compact_tabs = QTabBar()
        self.compact_tabs.setAccessibleName("Linked anatomical plane in compact viewer")
        for name in ORIENTATIONS:
            self.compact_tabs.addTab(name.title())
        self.compact_tabs.setToolTip(
            "Compact layout: switch linked planes without changing coordinates"
        )
        self.compact_tabs.hide()
        self.body.addWidget(self.compact_tabs)
        self.columns = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.columns.setSpacing(16)
        self.views = {name: SliceView(theme, name) for name in ORIENTATIONS}
        for name, view in self.views.items():
            view.escape_requested.connect(lambda: self.set_expanded(None))
            view.canvas.position_changed.connect(self.set_position)
            view.canvas.range_changed.connect(self.set_display_range)
            view.slider.valueChanged.connect(lambda value, n=name: self.set_slice(n, value))
            view.canvas.stepped.connect(
                lambda slices, volumes, n=name: self.step(n, slices, volumes)
            )
            view.expand.clicked.connect(
                lambda checked=False, n=name: self.set_expanded(None if self.expanded else n)
            )
            self.columns.addWidget(view, 1)
        self.compact_tabs.currentChanged.connect(self.update_view_layout)
        self.tools_panel = QWidget()
        tools_layout = QVBoxLayout(self.tools_panel)
        tools_layout.setContentsMargins(0, 0, 0, 0)
        tools_layout.setSpacing(8)
        self.tools_toggle.toggled.connect(self.tools_panel.setVisible)
        contrast = QHBoxLayout()
        contrast.addWidget(label("Display range", "control"))
        self.display_min = number_field("DWI display minimum")
        self.display_max = number_field("DWI display maximum")
        self.display_max.setValue(1)
        for text, field in (("Min", self.display_min), ("Max", self.display_max)):
            contrast.addWidget(label(text, "caption"))
            contrast.addWidget(field)
            field.valueChanged.connect(self.edit_display_range)
        self.auto_button = button("Auto")
        self.auto_button.setToolTip(
            "2nd–98th percentiles of finite values in the three current planes"
        )
        self.auto_button.clicked.connect(self.auto_range)
        contrast.addWidget(self.auto_button)
        self.reset_range_button = button("Reset")
        self.reset_range_button.setToolTip("Restore the initial dataset display range")
        self.reset_range_button.clicked.connect(self.reset_range)
        contrast.addWidget(self.reset_range_button)
        contrast.addStretch()
        tools_layout.addLayout(contrast)
        row = QHBoxLayout()
        self.auto_volume = button("Auto-adjust per volume: on")
        self.auto_volume.setCheckable(True)
        self.auto_volume.setChecked(True)
        self.auto_volume.setToolTip(
            "Recompute robust display range whenever the diffusion volume changes"
        )
        self.auto_volume.toggled.connect(self.toggle_auto_volume)
        row.addWidget(self.auto_volume)
        self.display_status = label("Display only · source values unchanged", "caption")
        row.addWidget(self.display_status, 1)
        tools_layout.addLayout(row)
        self.overlay = OverlayControls(self, theme)
        tools_layout.addWidget(self.overlay)
        self.body.addWidget(self.tools_panel)
        self.tools_panel.hide()
        self.body.addLayout(self.columns, 1)
        self.volume_label = label("", "control")
        self.body.addWidget(self.volume_label)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setAccessibleName("Preview volume")
        self.slider.setToolTip("Select one diffusion volume for all three views.")
        self.slider.valueChanged.connect(self.request_volume)
        self.body.addWidget(self.slider)
        self.position_label = label("", "mono")
        self.position_label.setAccessibleName("Voxel, world coordinates and intensity")
        self.body.addWidget(self.position_label)
        self.help_label = label(
            "Left drag: voxel · Right drag: contrast · Wheel / pinch: zoom · Middle / Space drag: pan · ↑ ↓ slice · ← → volume",
            "caption",
        )
        self.body.addWidget(self.help_label)
        self.sphere_side = QWidget()
        sphere_layout = QHBoxLayout(self.sphere_side)
        sphere_layout.setContentsMargins(0, 8, 0, 0)
        self.sphere = GradientSphere(theme)
        self.sphere.setFixedSize(200, 176)
        sphere_layout.addWidget(self.sphere)
        sphere_text = QVBoxLayout()
        sphere_text.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        sphere_text.setSpacing(8)
        sphere_text.addWidget(label("Gradient directions", "control"))
        self.legend = label("", "caption")
        sphere_text.addWidget(self.legend)
        sphere_text.addWidget(
            label(
                "Supplied FSL x / y / z components; anatomical correspondence not verified.",
                "caption",
            )
        )
        sphere_layout.addLayout(sphere_text, 1)
        self.sphere_details = Disclosure(
            "Gradient directions · supplied FSL vectors", self.sphere_side
        )
        self.body.addWidget(self.sphere_details)
        self.geometry_label = label("", "caption")
        self.geometry_details = Disclosure(
            "Native planes · geometry and display details", self.geometry_label
        )
        self.body.addWidget(self.geometry_details)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.start_read)
        self.escape = QShortcut(QKeySequence("Escape"), self)
        self.escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape.activated.connect(lambda: self.set_expanded(None))
        theme.changed.connect(self.update_legend)
        self.change_convention(self.convention.isChecked())
        # Keep the earlier axial accessors used by shell/capture integrations.
        self.canvas = self.views["axial"].canvas
        self.range_label = self.views["axial"].range_label

    @property
    def pending(self) -> bool:
        """Expose aggregate queue state for safe shell shutdown and tests."""
        return self.dataset is not None and (self.dwi_pending or self.overlay.pending)

    @property
    def dwi_pending(self) -> bool:
        """Keep DWI auto-ranging independent of pending overlay work."""
        return self.dataset is not None and any(
            view.desired_key != view.completed_key for view in self.views.values()
        )

    def update_view_layout(self, *_: object) -> None:
        """Use one linked plane at narrow widths instead of compressing canvases."""
        compact = self.width() < 640 and self.expanded is None
        self.compact_tabs.setVisible(compact)
        selected = ORIENTATIONS[self.compact_tabs.currentIndex()] if compact else self.expanded
        for name, view in self.views.items():
            view.setVisible(selected is None or name == selected)
        self.columns.setDirection(QBoxLayout.Direction.LeftToRight)

    def resizeEvent(self, event: object) -> None:
        """Preserve a useful image area beside a dock on laptop-sized windows."""
        self.update_view_layout()
        super().resizeEvent(event)

    def set_dataset(self, dataset: DwiDataset | None) -> None:
        """Invalidate queued results on replacement/unload, preserving safe worker ownership."""
        self.set_expanded(None)
        self.generation += 1
        self.request += 1
        self.dataset = dataset
        self.overlay.clear()
        self.initial_range = None
        self.auto_requested = True
        self.display_range = (0.0, 1.0)
        self.timer.stop()
        for view in self.views.values():
            view.completed_key = view.desired_key = None
            view.plane = None
            view.error = ""
            view.canvas.image = None
            view.canvas.reset_view()
            view.canvas.geometry = None
            view.canvas.message = "Reading native slice…"
            view.canvas.update()
        if dataset is None:
            self.position_label.clear()
            return
        self.position = tuple(n // 2 for n in dataset.shape[:3])
        self.configure_geometry()
        self.sphere.set_dataset(dataset)
        self.update_legend()
        self.slider.blockSignals(True)
        self.slider.setRange(0, dataset.n_volumes - 1)
        self.slider.setValue(dataset.default_preview_volume())
        self.slider.setEnabled(dataset.n_volumes > 1)
        self.slider.blockSignals(False)
        self.request_volume(self.slider.value())

    def configure_geometry(self) -> None:
        """Change display transforms/labels without discarding or rereading native planes."""
        if self.dataset is None:
            return
        convention = "Radiological" if self.convention.isChecked() else "Neurological"
        self.convention_label.setText(
            f"{convention} requested · anatomical orientation unverified"
            if self.dataset.affine_source == "fallback"
            else f"{convention} · patient {'right' if self.convention.isChecked() else 'left'} on screen left · sagittal anterior left"
        )
        details = []
        for name, view in self.views.items():
            geometry = plane_geometry(self.dataset, name, self.convention.isChecked())
            view.geometry = geometry
            view.canvas.configure(geometry, self.position)
            view.title.setText(
                name.title()
                + (
                    " · native"
                    if "no resampling" in geometry.title
                    else " · ?"
                    if "unverified" in geometry.title
                    else ""
                )
            )
            view.title.setToolTip(geometry.title)
            uncertain = "unverified" in geometry.title
            oblique = "no resampling" in geometry.title
            view.geometry_note.setText(
                f"File axis {geometry.axis} · anatomical orientation unverified"
                if uncertain
                else f"Closest native plane · {geometry.angle:.2f}° from {name} · no resampling"
            )
            view.geometry_note.setVisible(uncertain or oblique)
            view.slider.blockSignals(True)
            view.slider.setRange(0, self.dataset.shape[geometry.axis] - 1)
            view.slider.blockSignals(False)
            view.slider.setEnabled(self.dataset.shape[geometry.axis] > 1)
            if view.plane is not None and view.completed_key == view.desired_key:
                view.canvas.set_plane(view.plane)
            angle = (
                f" · normal {geometry.angle:.2f}° from {name}"
                if np.isfinite(geometry.angle)
                else ""
            )
            details.append(
                f"{geometry.title}: file axis {geometry.axis}{angle}. Screen right = {'+' if geometry.right_sign > 0 else '−'}axis {geometry.horizontal}; up = {'+' if geometry.up_sign > 0 else '−'}axis {geometry.vertical}."
            )
        self.geometry_label.setText(
            "\n".join(details)
            + f"\n{self.dataset.affine_source} affine. Display-only transpose/flips; no resampling. Pixel aspect uses affine column lengths; shear is not rectified. Oblique edge labels list RAS components ≥20%, largest first."
        )

    def change_convention(self, radiological: bool) -> None:
        """Persist display convention and repaint cached planes without changing voxels."""
        self.settings.set("preview/radiological", radiological)
        self.convention.setText("Radiological" if radiological else "Neurological")
        self.convention.setProperty("selected", radiological)
        self.theme.bind(self.convention)
        self.convention_label.setText(
            "Radiological · patient right on screen left · sagittal anterior left"
            if radiological
            else "Neurological · patient left on screen left · sagittal anterior left"
        )
        self.configure_geometry()
        if hasattr(self, "overlay"):
            self.overlay.repaint_planes()
        self.planes_changed.emit()

    def set_position(self, position: tuple[int, int, int]) -> None:
        """Clamp a shared native voxel and synchronously update all crosshairs/sliders."""
        if self.dataset is None:
            return
        self.position = tuple(
            max(0, min(int(value), size - 1))
            for value, size in zip(position, self.dataset.shape[:3], strict=True)
        )
        self.schedule()
        self.navigation_changed.emit(self.position, self.slider.value())

    def set_slice(self, orientation: Orientation, index: int) -> None:
        """Move only this view's native slice coordinate."""
        geometry = self.views[orientation].geometry
        if geometry is not None:
            position = list(self.position)
            position[geometry.axis] = index
            self.set_position(tuple(position))

    def step(self, orientation: Orientation, slices: int, volumes: int) -> None:
        """Delegate bounded keyboard steps to the same synchronized slider paths."""
        view = self.views[orientation]
        if slices:
            view.slider.setValue(view.slider.value() + slices)
        if volumes:
            self.slider.setValue(self.slider.value() + volumes)

    def request_volume(self, volume: int) -> None:
        """Label the selected b-value immediately, then debounce three requested planes."""
        if self.dataset is None:
            return
        text = f"Volume {volume + 1} / {self.dataset.n_volumes} · b = {self.dataset.gradients.bvals[volume]:g} s/mm²"
        if self.auto_volume.isChecked():
            self.auto_requested = True
        self.volume_label.setText(text)
        self.slider.setAccessibleDescription(text)
        self.schedule()
        self.navigation_changed.emit(self.position, volume)

    def schedule(self) -> None:
        """Reuse unchanged planes; coalesce changed slice/volume keys per view."""
        for view in self.views.values():
            axis = view.geometry.axis
            index = self.position[axis]
            view.slider.blockSignals(True)
            view.slider.setValue(index)
            view.slider.blockSignals(False)
            view.slice_label.setText(
                f"Slice {index + 1} of {self.dataset.shape[axis]} · file axis {axis}"
            )
            view.slider.setAccessibleDescription(view.slice_label.text())
            view.canvas.position = self.position
            key = (self.generation, self.slider.value(), index)
            view.desired_key = key
            if key != view.completed_key:
                view.canvas.image = None
                view.canvas.message = "Reading native slice…"
                view.error = ""
            elif view.plane is not None and view.canvas.image is None:
                view.canvas.set_plane(view.plane)
            view.canvas.update()
        self.overlay.repaint_planes()
        self.update_position_label()
        if self.pending:
            self.timer.start()

    def update_position_label(self) -> None:
        """Read intensity from a matching lazy plane, never from the full image."""
        if self.dataset is None:
            return
        world, units = world_position(self.dataset, self.position)
        qualifier = " · unverified fallback" if self.dataset.affine_source == "fallback" else ""
        intensity = "reading…"
        for view in self.views.values():
            if view.completed_key == view.desired_key and view.plane is not None:
                axes = [i for i in range(3) if i != view.geometry.axis]
                value = view.plane[tuple(self.position[i] for i in axes)]
                intensity = f"{float(value):.7g}" if np.isfinite(value) else f"{value} (nonfinite)"
                break
        else:
            if not self.dwi_pending:
                intensity = "unavailable · reload dataset"
        self.position_label.setText(
            f"Voxel (i, j, k; zero-based)  {self.position}   ·   Range {self.display_range[0]:.5g} to {self.display_range[1]:.5g}\nWorld RAS ({units}{qualifier})  ({world[0]:.2f}, {world[1]:.2f}, {world[2]:.2f})   ·   Intensity  {intensity}"
            + (self.overlay.readout() if hasattr(self, "overlay") else "")
        )

    def start_read(self) -> None:
        """Serialize proxy reads, respecting the latest debounce even after completion."""
        if self.thread is not None or not self.pending or self.timer.isActive():
            return
        self.request += 1
        self.thread = QThread(self)
        self.active_overlay = None
        if self.dwi_pending:
            view = next(v for v in self.views.values() if v.completed_key != v.desired_key)
            self.active_view, self.active_key = view, view.desired_key
            self.worker = SliceWorker(
                self.request,
                self.dataset,
                self.slider.value(),
                view.geometry.axis,
                self.position[view.geometry.axis],
            )
        else:
            self.active_overlay, task = self.overlay.task()
            self.worker = ViewerTaskWorker(self.request, task)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.result.connect(self.received)
        self.worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.finished)
        self.thread.start()

    def received(self, request: int, plane: object, error: str) -> None:
        """Only the matching dataset/volume/slice may update that view or intensity."""
        if self.active_overlay is not None:
            if request == self.request and self.dataset is not None:
                self.overlay.accept(self.active_overlay, plane, error)
            return
        view = self.active_view
        if request != self.request or self.dataset is None or self.active_key != view.desired_key:
            return
        view.completed_key = self.active_key
        view.plane = plane
        view.error = error
        if error:
            view.canvas.image = None
            view.canvas.message = "Preview unavailable · reload the dataset"
            view.canvas.update()
            view.range_label.setText(error)
        else:
            view.range_label.setText(view.canvas.set_plane(plane))
        view.canvas.setToolTip(
            view.range_label.text()
            + "\nLeft drag: voxel · Right drag: contrast · Wheel/pinch: zoom · Middle/Space drag: pan · ↑/↓ slice · ←/→ volume"
        )
        if not self.dwi_pending:
            planes = [v.plane for v in self.views.values() if v.plane is not None]
            if len(planes) == 3:
                automatic = robust_range(planes)
                if self.initial_range is None:
                    self.initial_range = automatic
                if self.auto_requested:
                    self.set_display_range(*automatic)
            self.auto_requested = False
        self.update_position_label()

        self.planes_changed.emit()

    def set_display_range(self, low: float, high: float) -> None:
        """Update all cached images together; intensity readout remains source data."""
        if not np.isfinite([low, high]).all() or high <= low:
            self.display_status.setText("Maximum must be greater than minimum")
            return
        self.auto_requested = False
        self.display_range = (low, high)
        for field, value in ((self.display_min, low), (self.display_max, high)):
            field.blockSignals(True)
            field.setValue(value)
            field.blockSignals(False)
        self.display_status.setText(f"Range {low:.6g} to {high:.6g} · display only")
        for view in self.views.values():
            view.canvas.display_range = self.display_range
            if view.plane is not None and view.completed_key == view.desired_key:
                view.canvas.set_plane(view.plane)

        self.update_position_label()

        self.planes_changed.emit()

    def edit_display_range(self, _: float) -> None:
        """Validate ordered endpoints before applying a manually entered range."""
        self.set_display_range(self.display_min.value(), self.display_max.value())

    def auto_range(self) -> None:
        """Wait for matching planes if necessary; never mix diffusion volumes."""
        self.auto_requested = True
        if not self.dwi_pending and self.dataset is not None:
            self.set_display_range(
                *robust_range(v.plane for v in self.views.values() if v.plane is not None)
            )

    def reset_range(self) -> None:
        """Return to the robust range established on initial dataset display."""
        if self.initial_range is not None:
            self.set_display_range(*self.initial_range)

    def toggle_auto_volume(self, enabled: bool) -> None:
        """Enabling recomputes now; disabling keeps the current range fixed."""
        self.auto_volume.setText(f"Auto-adjust per volume: {'on' if enabled else 'off'}")
        self.auto_requested = enabled
        if enabled:
            self.auto_range()

    def finished(self) -> None:
        """Release a completed worker before servicing the latest queued plane."""
        self.thread.wait()
        self.thread.deleteLater()
        self.thread, self.worker = None, None
        self.idle.emit()
        self.start_read()

    def set_expanded(self, orientation: Orientation | None) -> None:
        """Reuse the live controls in a workspace-sized host, retaining position and focus."""
        if orientation == self.expanded:
            return
        old = self.expanded
        self.expanded = orientation
        # Expanded mode prioritizes the image; the same controls remain one
        # click away. Their state and each canvas transform remain untouched.
        if orientation is not None:
            self.tools_toggle.setChecked(False)
        for name, view in self.views.items():
            view.setVisible(orientation is None or name == orientation)
            expanded = name == orientation
            view.expand.setText("")
            view.expand.setAccessibleName(f"{'Collapse' if expanded else 'Expand'} {name} view")
            view.expand.setToolTip(
                "Return to three views · Escape"
                if expanded
                else f"Expand {name} view to fill the workspace"
            )
            self.theme.set_icon(view.expand, "minimize" if expanded else "maximize")
        self.sphere_details.setVisible(orientation is None)
        self.geometry_details.setVisible(orientation is None)
        self.expanded_changed.emit(orientation is not None)
        self.update_view_layout()
        self.views[orientation or old or "axial"].canvas.setFocus()

    def update_legend(self) -> None:
        """Match each group to its token color, marker and unmodified b-value range."""
        if self.dataset is None:
            return
        lines = []
        for i, group in enumerate(self.sphere.groups):
            color = self.sphere.theme.color(f"gradient.group.{i % 6 + 1}").name()
            lines.append(
                f'<span style="color:{color}">{GradientSphere.MARKERS[i % 6]} Group {i + 1}</span> · {group.b_min:g}–{group.b_max:g} · {group.count} volumes'
            )
        count = self.dataset.summary()["b0_count"]
        lines.append(f"{count} b0-like volumes omitted from directions")
        if not count:
            lines.append("No b0-like volume; preview starts at volume 1.")
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        self.legend.setText("<br>".join(lines))
