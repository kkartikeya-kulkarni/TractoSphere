"""Linked preview integration: real proxy reads, mouse events and worker races.

Run ``QT_QPA_PLATFORM=offscreen python -m pytest tests/gui``. Synthetic ramps
encode voxel position and volume; no user imaging data is needed here.
"""

import threading
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QBoxLayout
from test_load_data import load

from dmri_studio.core.dataset import DwiDataset
from dmri_studio.core.preview_geometry import ORIENTATIONS
from dmri_studio.gui.preview import Preview


def settled(preview: Preview, qtbot: object) -> None:
    """Wait until every current plane is complete and its worker has joined."""
    qtbot.waitUntil(lambda: preview.thread is None and not preview.pending, timeout=10000)


def pixel(canvas: object, voxel: tuple[int, int, int]) -> QPoint:
    """Return the center of a requested voxel in the current fitted viewport."""
    x, y = canvas.geometry.display_position(voxel)
    rect = canvas.image_rect()
    return QPoint(round(rect.x() + rect.width() * x), round(rect.y() + rect.height() * y))


@pytest.mark.parametrize("orientation", ORIENTATIONS)
@pytest.mark.parametrize("radiological", (False, True))
def test_click_drag_and_linked_sliders(
    window: object, qtbot: object, dataset_folder: Path, orientation: str, radiological: bool
) -> None:
    """Actual clicks in all planes move the other slices and survive display flips."""
    load(window, qtbot, dataset_folder)
    preview = window.page.preview
    preview.convention.setChecked(radiological)
    view = preview.views[orientation]
    preview.set_expanded(orientation)
    qtbot.wait(30)
    goal = list((1, 2, 3))
    goal[view.geometry.axis] = preview.position[view.geometry.axis]
    goal = tuple(goal)
    qtbot.mouseClick(view.canvas, Qt.MouseButton.LeftButton, pos=pixel(view.canvas, goal))
    assert preview.position == goal
    for linked in preview.views.values():
        assert linked.canvas.position == goal
        assert linked.slider.value() == goal[linked.geometry.axis]
    # Outside-image clicks and drag moves must not clamp into edge voxels.
    qtbot.mouseClick(view.canvas, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    assert preview.position == goal
    moved = list(goal)
    moved[view.geometry.horizontal] += 1
    point = QPointF(pixel(view.canvas, tuple(moved)))
    event = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        point,
        point,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(view.canvas, event)
    assert preview.position == tuple(moved)
    view.slider.setValue(0)
    assert preview.position[view.geometry.axis] == 0
    assert all(v.canvas.position == preview.position for v in preview.views.values())
    settled(preview, qtbot)
    assert all(v.completed_key == v.desired_key for v in preview.views.values())


def test_readout_and_only_requested_planes(
    window: object, qtbot: object, dataset_folder: Path, monkeypatch: object
) -> None:
    """Coordinates and intensities match the asymmetric source; reads stay two-dimensional."""
    reads = []
    original = DwiDataset.read_slice

    def observed(
        self: object, volume: int, *, axis: int = 2, index: int | None = None, **kwargs: object
    ) -> np.ndarray:
        """Record the exact lazy request, retaining production proxy behavior."""
        reads.append((volume, axis, index))
        return original(self, volume, axis=axis, index=index, **kwargs)

    def forbidden(*args: object, **kwargs: object) -> None:
        """Full volume allocation is forbidden in any preview path."""
        raise AssertionError("Preview requested a whole volume")

    monkeypatch.setattr(DwiDataset, "read_slice", observed)
    monkeypatch.setattr(DwiDataset, "read_volume", forbidden)
    load(window, qtbot, dataset_folder)
    preview = window.page.preview
    assert preview.position == (4, 5, 6)
    assert reads == [(0, 2, 6), (0, 1, 5), (0, 0, 4)]
    assert "(4.80, 7.50, 12.00)" in preview.position_label.text()
    expected = ((4 * 10 + 5) * 12 + 6) * 4 + 1
    assert f"Intensity  {expected}" in preview.position_label.text()
    reads.clear()
    preview.views["axial"].slider.setValue(3)
    settled(preview, qtbot)
    assert reads == [(0, 2, 3)]  # other planes already contain this voxel
    preview.slider.setValue(2)
    assert "reading" in preview.position_label.text()
    settled(preview, qtbot)
    expected = ((4 * 10 + 5) * 12 + 3) * 4 + 3
    assert f"Intensity  {expected}" in preview.position_label.text()
    assert "Volume 3 / 4 · b = 1000" in preview.volume_label.text()
    assert reads[-3:] == [(2, 2, 3), (2, 1, 5), (2, 0, 4)]


@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_keyboard_expand_collapse_and_escape(
    window: object, qtbot: object, dataset_folder: Path, orientation: str
) -> None:
    """Expanded views occupy the workspace; keyboard navigation and Escape preserve state."""
    load(window, qtbot, dataset_folder)
    preview = window.page.preview
    view = preview.views[orientation]
    qtbot.mouseClick(view.expand, Qt.MouseButton.LeftButton)
    qtbot.wait(30)
    assert preview.expanded == orientation
    assert window.workspace.currentWidget() is window.expanded_host
    assert view.canvas.height() >= 300
    assert not preview.sphere_side.isVisible()
    assert sum(v.isVisible() for v in preview.views.values()) == 1
    before = preview.position[view.geometry.axis]
    qtbot.keyClick(view.canvas, Qt.Key.Key_Up)
    assert view.slider.value() == before + 1
    qtbot.keyClick(view.canvas, Qt.Key.Key_Down)
    assert view.slider.value() == before
    qtbot.keyClick(view.canvas, Qt.Key.Key_Right)
    assert preview.slider.value() == 1
    qtbot.keyClick(view.canvas, Qt.Key.Key_Left)
    assert preview.slider.value() == 0
    qtbot.keyClick(view.canvas, Qt.Key.Key_Left)
    assert preview.slider.value() == 0
    selected = preview.position
    # Escape also works when a control rather than the image has focus.
    view.slider.setFocus()
    qtbot.keyClick(view.slider, Qt.Key.Key_Escape)
    assert preview.expanded is None
    assert window.workspace.currentWidget() is window.scroll
    assert preview.position == selected
    assert all(v.isVisible() for v in preview.views.values())
    qtbot.mouseClick(view.expand, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(view.expand, Qt.MouseButton.LeftButton)
    assert preview.expanded is None
    settled(preview, qtbot)


def test_convention_persistence_and_cached_repaint(
    window: object, qtbot: object, dataset_folder: Path, monkeypatch: object
) -> None:
    """Convention changes update edges and pixel order without new disk requests."""
    dataset = load(window, qtbot, dataset_folder)
    preview = window.page.preview
    before = preview.views["axial"].canvas.image.copy()
    position = preview.position

    def forbidden(*args: object, **kwargs: object) -> None:
        """Display flips have no reason to read the proxy again."""
        raise AssertionError("Unexpected disk read")

    monkeypatch.setattr(DwiDataset, "read_slice", forbidden)
    preview.convention.setChecked(True)
    assert preview.position == position
    assert window.settings.get("preview/radiological") is True
    assert preview.views["axial"].geometry.edges == ("R", "L", "A", "P")
    assert preview.views["coronal"].geometry.edges == ("R", "L", "S", "I")
    assert preview.views["sagittal"].geometry.edges == ("A", "P", "S", "I")
    after = preview.views["axial"].canvas.image
    assert after.pixelColor(0, 0) == before.pixelColor(before.width() - 1, 0)
    second = Preview(window.theme, window.settings)
    qtbot.addWidget(second)
    assert second.convention.isChecked()
    assert "Radiological" in second.convention_label.text()
    assert window.dataset is dataset


@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_stale_slice_results_per_view(
    window: object, qtbot: object, dataset_folder: Path, monkeypatch: object, orientation: str
) -> None:
    """A blocked plane for each orientation cannot overwrite a newer slice request."""
    load(window, qtbot, dataset_folder)
    preview = window.page.preview
    view = preview.views[orientation]
    entered, release = threading.Event(), threading.Event()
    original = DwiDataset.read_slice
    reads = []

    def slow(
        self: object, volume: int, *, axis: int = 2, index: int | None = None, **kwargs: object
    ) -> np.ndarray:
        """Hold an obsolete index while the UI selects two newer positions."""
        reads.append((axis, index))
        if axis == view.geometry.axis and index == 1:
            entered.set()
            release.wait(5)
        return original(self, volume, axis=axis, index=index, **kwargs)

    monkeypatch.setattr(DwiDataset, "read_slice", slow)
    view.slider.setValue(1)
    qtbot.waitUntil(entered.is_set)
    view.slider.setValue(2)
    view.slider.setValue(3)
    window.toggle_theme()
    release.set()
    settled(preview, qtbot)
    assert reads == [(view.geometry.axis, 1), (view.geometry.axis, 3)]
    np.testing.assert_array_equal(
        view.plane, original(window.dataset, 0, axis=view.geometry.axis, index=3)
    )
    assert view.slider.value() == preview.position[view.geometry.axis] == 3


def test_narrow_stacking_and_resize_mapping(
    window: object, qtbot: object, dataset_folder: Path
) -> None:
    """Responsive layout and hit mapping use the resized physical image rectangle."""
    load(window, qtbot, dataset_folder)
    preview = window.page.preview
    window.resize(960, 720)
    qtbot.wait(40)
    # Compact workstation keeps all three planes visible at laptop width.
    assert preview.columns.direction() == QBoxLayout.Direction.LeftToRight
    for view in preview.views.values():
        rect = view.canvas.image_rect()
        assert rect.width() / rect.height() == pytest.approx(view.geometry.aspect)
        assert view.canvas.hit(QPointF(pixel(view.canvas, preview.position))) == preview.position
    window.resize(1400, 900)
    qtbot.wait(40)
    assert preview.columns.direction() == QBoxLayout.Direction.LeftToRight


def test_fallback_and_unknown_units_readout(
    window: object, qtbot: object, dataset_folder: Path
) -> None:
    """Unverified geometry and unknown units cannot masquerade as anatomical millimeters."""
    dataset = load(window, qtbot, dataset_folder)
    altered = replace(
        dataset, image=dataset._image, affine_source="fallback", spatial_units="unknown"
    )
    preview = window.page.preview
    preview.set_dataset(altered)
    settled(preview, qtbot)
    assert "units unknown" in preview.position_label.text()
    assert "unverified fallback" in preview.position_label.text()
    assert all(v.geometry.edges == ("?",) * 4 for v in preview.views.values())


def test_unload_during_preview_read(
    window: object, qtbot: object, dataset_folder: Path, monkeypatch: object
) -> None:
    """Retire safely while a plane is blocked; no stale image survives unload."""
    original_dataset = load(window, qtbot, dataset_folder)
    preview = window.page.preview
    original = DwiDataset.read_slice
    entered, release = threading.Event(), threading.Event()

    def slow(self: object, volume: int, **kwargs: object) -> np.ndarray:
        """Simulate blocking disk I/O without freezing the GUI event loop."""
        entered.set()
        release.wait(5)
        return original(self, volume, **kwargs)

    monkeypatch.setattr(DwiDataset, "read_slice", slow)
    preview.slider.setValue(1)
    qtbot.waitUntil(entered.is_set)
    preview.set_expanded("axial")
    window.unload()
    assert not original_dataset._closed
    assert preview.expanded is None
    release.set()
    settled(preview, qtbot)
    assert original_dataset._closed
    assert all(v.canvas.image is None for v in preview.views.values())
    assert preview.position_label.text() == ""


def test_close_cancels_debounced_read(
    window: object, qtbot: object, dataset_folder: Path, monkeypatch: object
) -> None:
    """Closing between a slider event and timer expiry must never start a worker."""
    dataset = load(window, qtbot, dataset_folder)
    preview = window.page.preview
    preview.slider.setValue(1)
    assert preview.timer.isActive() and preview.thread is None
    reads = []

    def unexpected(*args: object, **kwargs: object) -> None:
        """Record a read after close as a regression."""
        reads.append(args)

    monkeypatch.setattr(DwiDataset, "read_slice", unexpected)
    window.close()
    qtbot.wait(120)
    assert dataset._closed
    assert preview.thread is None and not preview.timer.isActive()
    assert not reads


def test_slice_controls_use_view_keyboard_semantics(
    window: object, qtbot: object, dataset_folder: Path
) -> None:
    """Focus on a slice slider still uses left/right for shared volume selection."""
    load(window, qtbot, dataset_folder)
    preview = window.page.preview
    view = preview.views["coronal"]
    before = preview.position
    view.slider.setFocus()
    qtbot.keyClick(view.slider, Qt.Key.Key_Right)
    assert preview.slider.value() == 1
    assert preview.position == before
    qtbot.keyClick(view.slider, Qt.Key.Key_Up)
    assert preview.position[view.geometry.axis] == before[view.geometry.axis] + 1
    settled(preview, qtbot)


def test_oblique_caption_and_replacement_geometry(
    window: object, qtbot: object, dataset_folder: Path
) -> None:
    """A replacement updates plane axes without slider signals using half-configured views."""
    dataset = load(window, qtbot, dataset_folder)
    preview = window.page.preview
    preview.set_position((7, 9, 11))
    settled(preview, qtbot)
    affine = np.array([[0, 0, 1, 0], [0.2, 1, 0, 0], [1, 0, 0, 0], [0, 0, 0, 1]], dtype=float)
    changed = replace(dataset, image=dataset._image, affine=affine)
    preview.set_dataset(changed)
    settled(preview, qtbot)
    assert preview.views["axial"].geometry.axis == 0
    assert "no resampling" in preview.views["axial"].geometry_note.text()
    assert preview.views["axial"].geometry_note.isVisible()
    assert preview.position == (4, 5, 6)
