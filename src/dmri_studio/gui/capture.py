"""Capture actual Qt controls and native VTK pixels in one workspace image."""

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QPainter

from .scene_renderer import SceneRenderer


def capture_widget(widget):
    """Composite native OpenGL children that QWidget.grab cannot reliably read.

    On X11, an on-screen VTK child renders correctly but QWidget.grab can return
    black/uninitialized pixels for its native window. Read VTK's actual back
    buffer and place it at the child's logical Qt coordinates; pixmap DPR handles
    high-DPI displays. No image data, camera or scientific geometry is changed.
    """
    pixmap = widget.grab()
    scenes = ([widget] if isinstance(widget, SceneRenderer) else []) + widget.findChildren(
        SceneRenderer
    )
    painter = QPainter(pixmap)
    try:
        for scene in scenes:
            if not scene.isVisibleTo(widget):
                continue
            scene.draw()
            scene.capture_frame()
            if scene.frame is None:
                raise RuntimeError("No rendered 3D framebuffer is available for screenshot export")
            position = QPoint(0, 0) if scene is widget else scene.mapTo(widget, QPoint(0, 0))
            painter.drawImage(QRect(position, scene.size()), scene.frame)
    finally:
        painter.end()
    return pixmap
