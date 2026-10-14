"""Viewer integration through real Qt events and synthetic NIfTI workers."""

import threading
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication
from test_linked_preview import pixel, settled
from test_load_data import load

from dmri_studio.core.overlay import OverlayDataset
from dmri_studio.core.viewer_display import robust_range


def overlay_file(
    folder: Path, dataset: object, *, four_d: bool = False, mismatch: bool = False
) -> Path:
    """Write a distinctive scalar ramp on the synthetic DWI grid in pytest temp."""
    data = np.arange(np.prod(dataset.shape[:3]), dtype=np.float32).reshape(dataset.shape[:3])
    if four_d:
        data = np.stack([data, data + 2000], axis=-1)
    affine = dataset.affine.copy()
    if mismatch:
        affine[0, 3] += 1
    image = nib.Nifti1Image(data, affine)
    image.header.set_xyzt_units("mm")
    path = folder / "overlay.nii"
    nib.save(image, path)
    return path


def move(canvas: object, origin: QPointF, delta: QPointF, button: Qt.MouseButton) -> None:
    """Send an explicit held-button move; offscreen platforms need no cursor warp."""
    point = origin + delta
    QApplication.sendEvent(
        canvas,
        QMouseEvent(
            QMouseEvent.Type.MouseMove,
            point,
            point,
            Qt.MouseButton.NoButton,
            button,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def test_shared_range_auto_volume_and_readout(
    window: object, qtbot: object, dataset_folder: Path
) -> None:
    """Auto runs only with matching planes; fixed/manual ranges survive navigation."""
    load(window, qtbot, dataset_folder)
    p = window.page.preview
    initial = p.display_range
    assert initial == robust_range(v.plane for v in p.views.values())
    readout = p.position_label.text()
    p.set_display_range(100, 900)
    assert all(v.canvas.display_range == (100, 900) for v in p.views.values())
    assert p.position_label.text().split("Intensity")[1] == readout.split("Intensity")[1]
    p.auto_volume.setChecked(False)
    p.slider.setValue(2)
    settled(p, qtbot)
    assert p.display_range == (100, 900)
    p.auto_volume.setChecked(True)
    assert p.display_range == robust_range(v.plane for v in p.views.values())
    p.slider.setValue(3)
    settled(p, qtbot)
    assert p.display_range == robust_range(v.plane for v in p.views.values())
    p.views["axial"].slider.setValue(1)
    before = p.display_range
    settled(p, qtbot)
    assert p.display_range == before
    p.auto_range()
    assert p.display_range == robust_range(v.plane for v in p.views.values())
    p.reset_range()
    assert p.display_range == initial
    assert "zero-based" in p.position_label.text()
    assert p.views["axial"].slice_label.text().startswith("Slice 2 of 12")
    p.set_display_range(2, 1)
    assert p.display_range == initial
    assert "Maximum" in p.display_status.text()


@pytest.mark.parametrize("orientation", ["axial", "coronal", "sagittal"])
@pytest.mark.parametrize("radio", [False, True])
def test_zoom_pan_mapping_and_expansion(
    window: object, qtbot: object, dataset_folder: Path, orientation: str, radio: bool
) -> None:
    """Anchored zoom, native flips, both pan gestures, reset and expansion agree."""
    load(window, qtbot, dataset_folder)
    p = window.page.preview
    p.convention.setChecked(radio)
    p.set_expanded(orientation)
    qtbot.wait(30)
    canvas = p.views[orientation].canvas
    point = QPointF(pixel(canvas, p.position))
    before = canvas.hit(point)
    wheel = QWheelEvent(
        point,
        point,
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(canvas, wheel)
    assert canvas.zoom > 1
    assert canvas.hit(point) == before
    zoom = canvas.zoom
    for space in (False, True):
        origin = QPointF(pixel(canvas, p.position))
        if space:
            qtbot.keyPress(canvas, Qt.Key.Key_Space)
        button = Qt.MouseButton.LeftButton if space else Qt.MouseButton.MiddleButton
        qtbot.mousePress(canvas, button, pos=origin.toPoint())
        old_pan = QPointF(canvas.pan)
        delta = QPointF(25, -17)
        move(canvas, origin, delta, button)
        qtbot.mouseRelease(canvas, button, pos=(origin + delta).toPoint())
        if space:
            qtbot.keyRelease(canvas, Qt.Key.Key_Space)
        assert canvas.pan == old_pan + delta
        assert canvas.hit(origin + delta) == before
        assert p.position == before
    p.set_expanded(None)
    assert canvas.zoom == zoom
    assert all(v.canvas.zoom == 1 for n, v in p.views.items() if n != orientation)
    p.set_expanded(orientation)
    qtbot.wait(30)
    goal = list(p.position)
    goal[canvas.geometry.horizontal] += 1
    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=pixel(canvas, tuple(goal)))
    assert p.position == tuple(goal)
    qtbot.mouseClick(p.views[orientation].reset, Qt.MouseButton.LeftButton)
    assert canvas.zoom == 1 and canvas.pan == QPointF()
    settled(p, qtbot)


def test_right_drag_window_level(window: object, qtbot: object, dataset_folder: Path) -> None:
    """Right drag changes every view's display while leaving voxel selection fixed."""
    load(window, qtbot, dataset_folder)
    p = window.page.preview
    canvas = p.canvas
    origin = canvas.rect().center()
    position, initial = p.position, p.display_range
    qtbot.mousePress(canvas, Qt.MouseButton.RightButton, pos=origin)
    move(canvas, QPointF(origin), QPointF(80, 30), Qt.MouseButton.RightButton)
    qtbot.mouseRelease(canvas, Qt.MouseButton.RightButton)
    assert p.display_range != initial
    assert all(v.canvas.display_range == p.display_range for v in p.views.values())
    assert p.position == position


def test_overlay_controls_volume_thresholds_readout_and_removal(
    window: object, qtbot: object, dataset_folder: Path, tmp_path: Path
) -> None:
    """4D overlay has independent volume, styling, raw readout and removal lifecycle."""
    dataset = load(window, qtbot, dataset_folder)
    p = window.page.preview
    overlay = p.overlay
    overlay.load(overlay_file(tmp_path, dataset, four_d=True))
    settled(p, qtbot)
    assert overlay.dataset.n_volumes == 2
    assert all(v.canvas.overlay_image is not None for v in p.views.values())
    assert "Overlay  546" in p.position_label.text()
    overlay.volume.setValue(2)
    settled(p, qtbot)
    assert p.slider.value() == 0
    assert "Overlay  2546" in p.position_label.text()
    overlay.low.setValue(2000)
    overlay.high.setValue(3000)
    overlay.mode.setCurrentText("Colormap")
    assert not overlay.bar.isHidden()
    assert overlay.bar.low == 2000
    overlay.visible.setChecked(False)
    assert all(v.canvas.overlay_image is None for v in p.views.values())
    assert "Overlay  2546" in p.position_label.text()
    overlay.visible.setChecked(True)
    p.convention.setChecked(True)
    assert all(v.canvas.overlay_image is not None for v in p.views.values())
    overlay.mode.setCurrentText("Mask")
    overlay.outline.setChecked(True)
    assert overlay.bar.isHidden()
    overlay.low.setValue(4000)
    assert all(v.canvas.overlay_image is None for v in p.views.values())
    assert "Threshold" in overlay.status.text()
    overlay.clear()
    assert "Overlay" not in p.position_label.text()
    assert all(v.canvas.overlay_image is None for v in p.views.values())
    overlay.load(overlay_file(tmp_path, dataset, mismatch=True))
    settled(p, qtbot)
    assert overlay.dataset is None
    assert "1 mm" in overlay.status.text() and "no resampling" in overlay.status.text()


def test_overlay_stale_load_unload(
    window: object, qtbot: object, dataset_folder: Path, tmp_path: Path, monkeypatch: object
) -> None:
    """A worker cannot resurrect an overlay after DWI unload; I/O is off-thread."""
    dataset = load(window, qtbot, dataset_folder)
    p = window.page.preview
    path = overlay_file(tmp_path, dataset)
    entered, release = threading.Event(), threading.Event()
    original = OverlayDataset.load

    def slow(path: Path, dwi: object) -> OverlayDataset:
        """Hold the load while asserting that GUI and I/O use different threads."""
        assert threading.current_thread() is not threading.main_thread()
        entered.set()
        release.wait(5)
        return original(path, dwi)

    monkeypatch.setattr(OverlayDataset, "load", slow)
    p.overlay.load(path)
    qtbot.waitUntil(entered.is_set)
    window.unload()
    release.set()
    settled(p, qtbot)
    assert p.overlay.dataset is None and p.overlay.status.isHidden()
    assert all(v.canvas.overlay_image is None for v in p.views.values())


def test_pinch_and_numeric_fields(window: object, qtbot: object, dataset_folder: Path) -> None:
    """Native pinch anchors pixels; editable numbers remain visible after themes."""
    from PySide6.QtGui import QNativeGestureEvent, QPointingDevice

    load(window, qtbot, dataset_folder)
    p = window.page.preview
    p.set_expanded("axial")
    qtbot.wait(30)
    canvas = p.canvas
    point = canvas.image_rect().center()
    before = canvas.hit(point)
    event = QNativeGestureEvent(
        Qt.NativeGestureType.ZoomNativeGesture,
        QPointingDevice.primaryPointingDevice(),
        2,
        point,
        point,
        point,
        0.5,
        QPointF(),
    )
    QApplication.sendEvent(canvas, event)
    assert canvas.zoom == pytest.approx(1.5)
    assert canvas.hit(point) == before
    assert canvas.hit(QPointF(5, 5)) is None  # annotations never select hidden pixels
    p.tools_toggle.setChecked(True)
    for mode in ("dark", "light", "dark"):
        window.theme.apply(mode)
        qtbot.wait(20)
        assert p.display_max.lineEdit().width() > 70
        assert p.display_max.text()
    p.display_max.setFocus()
    p.display_max.selectAll()
    qtbot.keyClicks(p.display_max, "2e3")
    qtbot.keyClick(p.display_max, Qt.Key.Key_Return)
    assert p.display_range[1] == 2000
    settled(p, qtbot)


def test_overlay_stale_volume_read_and_dwi_replacement(
    window: object, qtbot: object, dataset_folder: Path, tmp_path: Path, monkeypatch: object
) -> None:
    """Old overlay volume/slice results cannot overwrite newer state or replacement."""
    dataset = load(window, qtbot, dataset_folder)
    p = window.page.preview
    p.overlay.load(overlay_file(tmp_path, dataset, four_d=True))
    settled(p, qtbot)
    entered, release = threading.Event(), threading.Event()
    original = OverlayDataset.read_slice

    def slow(self: object, volume: int, **kwargs: object) -> np.ndarray:
        """Hold volume two while volume one is reselected on the GUI thread."""
        if volume == 1:
            entered.set()
            release.wait(5)
        return original(self, volume, **kwargs)

    monkeypatch.setattr(OverlayDataset, "read_slice", slow)
    p.overlay.volume.setValue(2)
    qtbot.waitUntil(entered.is_set)
    p.overlay.volume.setValue(1)
    p.set_position((1, 2, 3))
    release.set()
    settled(p, qtbot)
    assert "Overlay  147" in p.position_label.text()
    assert all(key[1] == 0 for key, _ in p.overlay.cache.values())
    p.set_dataset(dataset)
    settled(p, qtbot)
    assert p.overlay.dataset is None
    assert "Overlay" not in p.position_label.text()
