"""Interactive native slice painting; display transforms never change source data.

Geometry is supplied by core.preview_geometry. Mouse hits and crosshairs use the
same fitted rectangle and invertible mapping, including anisotropy and flips.
"""

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QImage,
    QKeyEvent,
    QMouseEvent,
    QNativeGestureEvent,
    QPainter,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from dmri_studio.core.preview_geometry import PlaneGeometry
from dmri_studio.core.viewer_display import drag_range, normalize

from .theme import Theme, font


class SliceCanvas(QWidget):
    """Paint a native plane, orientation edges and a contrasting linked crosshair."""

    position_changed = Signal(object)
    stepped = Signal(int, int)
    range_changed = Signal(float, float)

    def __init__(self, theme: Theme) -> None:
        """Keep the scan surface dark in both themes and accept keyboard focus."""
        super().__init__()
        self.theme = theme
        self.geometry: PlaneGeometry | None = None
        self.position = (0, 0, 0)
        self.image: QImage | None = None
        self.aspect = 1.0
        self.display_range = (0.0, 1.0)
        self.fod_image: QImage | None = None
        self.overlay_image: QImage | None = None
        self.zoom = 1.0
        self.pan = QPointF()
        self.space_held = False
        self.drag_mode = ""
        self.drag_origin = QPointF()
        self.drag_pan = QPointF()
        self.drag_display = self.display_range
        self.message = "No image open"
        self.smooth_image = True
        self.setMinimumSize(160, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip(
            "Left drag: voxel · Right drag: window/level · Wheel/pinch: zoom · Middle/Space drag: pan · ↑/↓ slice · ←/→ volume"
        )
        theme.changed.connect(self.update)

    def sizeHint(self) -> QSize:
        """Give normal views useful image area while allowing expanded growth."""
        return QSize(320, 300)

    def configure(self, geometry: PlaneGeometry, position: tuple[int, int, int]) -> None:
        """Set the display mapping without reading or resampling voxels."""
        self.geometry, self.position = geometry, position
        self.aspect = geometry.aspect
        self.setAccessibleName(
            f"{geometry.orientation.title()} slice; arrow keys step slice and volume"
        )
        self.update()

    def set_plane(self, plane: np.ndarray, spacing: tuple[float, float] | None = None) -> str:
        """Scale finite plane min/max for display only; constant planes remain clickable."""
        finite = plane[np.isfinite(plane)]
        low, high = self.display_range
        normalized = normalize(plane, low, high)
        pixels = (
            self.geometry.display_plane(normalized)
            if self.geometry is not None
            else np.flipud(normalized.T)
        )
        pixels = np.ascontiguousarray(np.clip(pixels * 255, 0, 255).astype(np.uint8))
        self.image = QImage(
            pixels.data,
            pixels.shape[1],
            pixels.shape[0],
            pixels.strides[0],
            QImage.Format.Format_Grayscale8,
        ).copy()
        if self.geometry is None and spacing is not None:
            self.aspect = (plane.shape[0] * spacing[0]) / (plane.shape[1] * spacing[1])
        self.message = (
            ""
            if finite.size and finite.max() > finite.min()
            else (
                "Constant plane · no intensity contrast"
                if finite.size
                else "No finite values in this plane"
            )
        )
        self.update()
        return self.message or f"Shared display min/max {low:.6g}–{high:.6g}"

    def fit_rect(self) -> QRectF:
        """Fit physical pixel sizes inside a reserved gutter for edge annotations."""
        height = max(0.0, min(self.height() - 48, (self.width() - 48) / self.aspect))
        width = height * self.aspect
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def image_rect(self) -> QRectF:
        """Use one transformed rectangle for pixels, overlays, crosshair and hits."""
        fit = self.fit_rect()
        size = fit.size() * self.zoom
        return QRectF(fit.center() + self.pan - QPointF(size.width() / 2, size.height() / 2), size)

    def reset_view(self) -> None:
        """Restore this view's independent fit and pan without changing slices."""
        self.zoom, self.pan = 1.0, QPointF()
        self.update()

    def zoom_at(self, point: QPointF, factor: float) -> None:
        """Anchor the exact image fraction under the cursor while changing scale."""
        rect = self.image_rect()
        if rect.isEmpty():
            return
        new_zoom = max(0.25, min(32.0, self.zoom * factor))
        ratio = new_zoom / self.zoom
        center = point + (rect.center() - point) * ratio
        self.zoom = new_zoom
        self.pan = center - self.fit_rect().center()
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Consume high-resolution wheel motion so the surrounding page stays put."""
        delta = event.pixelDelta().y() or event.angleDelta().y() / 8
        self.zoom_at(event.position(), float(np.exp(np.clip(delta / 120, -2, 2))))
        event.accept()

    def event(self, event: object) -> bool:
        """Support native trackpad pinch (including macOS native zoom gestures)."""
        if (
            isinstance(event, QNativeGestureEvent)
            and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture
        ):
            self.zoom_at(event.position(), max(0.1, 1 + event.value()))
            event.accept()
            return True
        return super().event(event)

    def hit(self, point: QPointF) -> tuple[int, int, int] | None:
        """Reject letterboxing; keep mouse mapping available during a pending read."""
        rect = self.image_rect()
        if (
            self.geometry is None
            or rect.isEmpty()
            or not QRectF(self.rect().adjusted(24, 24, -24, -24)).contains(point)
        ):
            return None
        return self.geometry.voxel_position(
            (point.x() - rect.left()) / rect.width(),
            (point.y() - rect.top()) / rect.height(),
            self.position,
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Keep left selection; reserve middle/Space for pan and right for contrast."""
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.drag_origin = event.position()
        self.drag_pan = QPointF(self.pan)
        self.drag_display = self.display_range
        self.drag_mode = (
            "pan"
            if event.button() == Qt.MouseButton.MiddleButton
            or (self.space_held and event.button() == Qt.MouseButton.LeftButton)
            else "range"
            if event.button() == Qt.MouseButton.RightButton
            else ""
        )
        if not self.drag_mode and event.button() == Qt.MouseButton.LeftButton:
            position = self.hit(event.position())
            if position is not None:
                self.position_changed.emit(position)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Adjust display state from the original drag, avoiding cumulative drift."""
        delta = event.position() - self.drag_origin
        if self.drag_mode == "pan":
            self.pan = self.drag_pan + delta
            self.update()
        elif self.drag_mode == "range":
            self.range_changed.emit(*drag_range(self.drag_display, delta.x(), delta.y()))
        elif event.buttons() & Qt.MouseButton.LeftButton:
            position = self.hit(event.position())
            if position is not None:
                self.position_changed.emit(position)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """End a drag even if its modifier key was released first."""
        self.drag_mode = ""
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        """Space affects pointer drags only, never voxel coordinates."""
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.space_held = False
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event: object) -> None:
        """Avoid a stuck Space modifier after switching applications or controls."""
        self.space_held = False
        self.drag_mode = ""
        super().focusOutEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Up/down step native slice index; left/right step the shared volume."""
        if event.key() == Qt.Key.Key_Space:
            self.space_held = True
            event.accept()
            return
        steps = {
            Qt.Key.Key_Up: (1, 0),
            Qt.Key.Key_Down: (-1, 0),
            Qt.Key.Key_Right: (0, 1),
            Qt.Key.Key_Left: (0, -1),
        }
        if event.key() in steps:
            self.stepped.emit(*steps[event.key()])
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event: object) -> None:
        """Smoothly fit image pixels; draw dual-stroke crosshairs above any intensity."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.smooth_image)
        painter.setBrush(self.theme.color("viewer.canvas"))
        painter.setPen(
            QPen(self.theme.color("focus.ring"), 2) if self.hasFocus() else Qt.PenStyle.NoPen
        )
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)
        rect = self.image_rect()
        painter.save()
        painter.setClipRect(self.rect().adjusted(24, 24, -24, -24))
        if self.image is not None:
            painter.drawImage(rect, self.image)
            if self.overlay_image is not None:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                painter.drawImage(rect, self.overlay_image)
            if self.fod_image is not None:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.drawImage(rect, self.fod_image)
            if self.geometry is not None:
                x, y = self.geometry.display_position(self.position)
                center = QPointF(rect.left() + x * rect.width(), rect.top() + y * rect.height())
                # A dark halo and bright cyan core preserve visibility on both
                # white tissue and dark background without obscuring a voxel.
                for token, width in (("viewer.crosshair.halo", 3), ("viewer.crosshair", 1)):
                    painter.setPen(QPen(self.theme.color(token), width))
                    painter.drawLine(
                        QPointF(rect.left(), center.y()), QPointF(rect.right(), center.y())
                    )
                    painter.drawLine(
                        QPointF(center.x(), rect.top()), QPointF(center.x(), rect.bottom())
                    )
        painter.restore()
        painter.setFont(font("caption"))
        painter.setPen(self.theme.color("viewer.text"))
        if self.geometry is not None:
            left, right, top, bottom = self.geometry.edges
            for text, box in (
                (left, QRectF(0, self.height() / 2 - 12, 24, 24)),
                (right, QRectF(self.width() - 24, self.height() / 2 - 12, 24, 24)),
                (top, QRectF(0, 0, self.width(), 24)),
                (bottom, QRectF(0, self.height() - 24, self.width(), 24)),
            ):
                painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        if self.message and self.image is None:
            painter.fillRect(rect, self.theme.color("viewer.canvas"))
            painter.drawText(
                rect.adjusted(8, 8, -8, -8),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self.message,
            )
