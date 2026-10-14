"""Workspace export must contain the actual VTK framebuffer, not native-window garbage."""

from PySide6.QtGui import QColor, QPixmap

from dmri_studio.gui.capture import capture_widget


def test_workspace_capture_composites_native_vtk_frame(window, qtbot, dataset_folder, monkeypatch):
    window.focus_scientific_viewer()
    viewer = window.scientific_viewer
    viewer.load_image(dataset_folder / "data.nii")
    qtbot.waitUntil(lambda: not viewer.tasks, timeout=15000)
    viewer.scene.draw()
    qtbot.wait(50)
    # Reproduce QWidget.grab's broken native child region deterministically.
    # The scientific framebuffer still comes from the actual VTK renderer.
    ratio = window.devicePixelRatioF()
    blank = QPixmap(round(window.width() * ratio), round(window.height() * ratio))
    blank.setDevicePixelRatio(ratio)
    blank.fill(QColor("magenta"))
    monkeypatch.setattr(window, "grab", lambda: blank.copy())
    result = capture_widget(window).toImage()
    scene = viewer.scene
    frame = scene.frame
    center = scene.mapTo(window, scene.rect().center())
    actual = result.pixelColor(round(center.x() * ratio), round(center.y() * ratio))
    expected = frame.pixelColor(frame.width() // 2, frame.height() // 2)
    assert actual != QColor("magenta")
    assert all(
        abs(a - b) <= 8 for a, b in zip(actual.getRgb()[:3], expected.getRgb()[:3], strict=True)
    )
    assert result.pixelColor(0, 0) == QColor("magenta")
