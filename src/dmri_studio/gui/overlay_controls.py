"""Single-overlay controls and bounded plane cache; Preview owns all worker I/O.

The queue exposes immutable task snapshots, so replacement/removal/volume and
slice changes cannot publish stale pixels or revive an overlay after unload.
"""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QLocale, QRectF, Qt
from PySide6.QtGui import QDoubleValidator, QImage, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.overlay import OverlayDataset
from dmri_studio.core.viewer_display import colors, overlay_rgba, robust_range

from .components import button, label
from .theme import Theme, font

LOGGER = logging.getLogger(__name__)


class DisplayNumber(QDoubleSpinBox):
    """Compact significant-digit presentation with scientific-notation entry."""

    def textFromValue(self, value: float) -> str:
        """Avoid six trailing zeros on every intensity/threshold endpoint."""
        return f"{value:.8g}"

    def valueFromText(self, text: str) -> float:
        """Parse the same locale-independent scalar notation shown in readouts."""
        try:
            return float(text)
        except ValueError:
            return self.value()

    def validate(self, text: str, position: int) -> tuple:
        """Allow partial exponents while editing without publishing invalid values."""
        validator = QDoubleValidator(-1e30, 1e30, 12)
        validator.setLocale(QLocale.c())
        validator.setNotation(QDoubleValidator.Notation.ScientificNotation)
        return validator.validate(text, position)


def number_field(name: str) -> QDoubleSpinBox:
    """Offer keyboard-editable scientific values without rounding small ranges away."""
    field = DisplayNumber()
    field.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.PlusMinus)
    field.setRange(-1e30, 1e30)
    field.setDecimals(12)
    field.setKeyboardTracking(False)
    field.setMinimumWidth(116)
    field.setMaximumWidth(160)
    field.setAccessibleName(name)
    field.setToolTip(name)
    return field


class ColorBar(QWidget):
    """Paint the same color lookup and numeric endpoints used by overlay pixels."""

    def __init__(self, theme: Theme) -> None:
        """Keep the compact legend readable in both themes."""
        super().__init__()
        self.theme = theme
        self.low, self.high, self.colormap = 0.0, 1.0, "viridis"
        self.setMinimumSize(160, 40)
        theme.changed.connect(self.update)

    def paintEvent(self, event: object) -> None:
        """Render a dense one-row lookup above its threshold values."""
        rgb = np.ascontiguousarray(
            colors(
                (np.linspace(0, 1, 256) if self.high > self.low else np.zeros(256))[None, :],
                self.colormap,
            )
        )
        image = QImage(rgb.data, 256, 1, rgb.strides[0], QImage.Format.Format_RGB888)
        painter = QPainter(self)
        painter.drawImage(QRectF(0, 0, self.width(), 12), image)
        painter.setFont(font("caption"))
        painter.setPen(self.theme.color("text.secondary"))
        painter.drawText(
            QRectF(0, 16, self.width(), 24), Qt.AlignmentFlag.AlignLeft, f"{self.low:.5g}"
        )
        painter.drawText(
            QRectF(0, 16, self.width(), 24), Qt.AlignmentFlag.AlignRight, f"{self.high:.5g}"
        )


class OverlayControls(QWidget):
    """Manage one overlay; scientific validation lives entirely in core.overlay."""

    def __init__(self, preview: Any, theme: Theme) -> None:
        """Build labelled controls without opening a file on the GUI thread."""
        super().__init__()
        self.preview, self.theme = preview, theme
        self.dataset: OverlayDataset | None = None
        self.generation = 0
        self.load_path: Path | None = None
        self.cache: dict[str, tuple[tuple, np.ndarray | None]] = {}
        self.initial_thresholds = False
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        row = QHBoxLayout()
        self.add = button("Add overlay")
        self.add.clicked.connect(self.choose)
        row.addWidget(self.add)
        self.name = label("One NIfTI on the exact DWI grid", "caption")
        row.addWidget(self.name, 1)
        self.remove = button("Remove overlay", quiet=True)
        self.remove.clicked.connect(self.clear)
        row.addWidget(self.remove)
        self.body.addLayout(row)
        self.status = label("", "caption")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setMargin(8)
        self.status.hide()
        self.body.addWidget(self.status)
        self.controls = QWidget()
        layout = QVBoxLayout(self.controls)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.visible = button("Visible")
        self.visible.setCheckable(True)
        self.visible.setChecked(True)
        self.mode = QComboBox()
        self.mode.addItems(["Mask", "Colormap"])
        self.mode.setAccessibleName("Overlay display mode")
        self.colormap = QComboBox()
        self.colormap.addItems(["gray", "hot", "red-yellow", "viridis"])
        self.colormap.setCurrentText("viridis")
        self.colormap.setAccessibleName("Overlay colormap")
        self.outline = button("Outline: off")
        self.outline.setCheckable(True)
        self.volume = QSpinBox()
        self.volume.setButtonSymbols(QSpinBox.ButtonSymbols.PlusMinus)
        self.volume.setMinimum(1)
        self.volume.setPrefix("Volume ")
        self.volume.setAccessibleName("Overlay volume (one-based)")
        for control in (self.visible, self.mode, self.colormap, self.outline, self.volume):
            row.addWidget(control)
        row.addStretch()
        layout.addLayout(row)
        row = QHBoxLayout()
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(45)
        self.opacity.setMaximumWidth(120)
        self.opacity.setAccessibleName("Overlay opacity")
        self.opacity_label = label("Opacity 45%", "caption")
        row.addWidget(self.opacity_label)
        row.addWidget(self.opacity)
        self.low, self.high = (
            number_field("Overlay threshold minimum"),
            number_field("Overlay threshold maximum"),
        )
        row.addWidget(label("Threshold", "caption"))
        row.addWidget(self.low)
        row.addWidget(label("to", "caption"))
        row.addWidget(self.high)
        self.bar = ColorBar(theme)
        self.bar.setMaximumWidth(240)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(self.bar)
        self.body.addWidget(self.controls)
        for control in (self.visible, self.outline):
            control.toggled.connect(self.repaint_planes)
        for control in (self.mode, self.colormap):
            control.currentTextChanged.connect(self.repaint_planes)
        for control in (self.low, self.high, self.opacity):
            control.valueChanged.connect(self.repaint_planes)
        self.volume.valueChanged.connect(self.request_volume)
        for control, tip in (
            (self.visible, "Show or hide overlay pixels; source values remain available."),
            (self.mode, "Mask: nonzero values in amber. Colormap: scalar values mapped to colors."),
            (self.colormap, "Color map for values between the inclusive thresholds."),
            (self.outline, "Show only the inner four-neighbor boundary on each displayed plane."),
            (self.opacity, "Blend overlay pixels over the DWI."),
            (self.volume, "Choose a volume from a 4D overlay independently of the DWI volume."),
            (
                self.add,
                "Load a 3D or 4D NIfTI in a worker; require matching shape and corner positions within 0.01 mm.",
            ),
        ):
            control.setToolTip(tip)
        self.clear()

    def choose(self) -> None:
        """Use a native file chooser, then enqueue loading and core validation."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Add overlay", str(Path.home()), "NIfTI (*.nii *.nii.gz)"
        )
        if path:
            self.load(path)

    def load(self, path: str | Path) -> None:
        """Replace the current overlay explicitly; rejected candidates stay absent."""
        if self.preview.dataset is None:
            return
        self.clear()
        self.load_path = Path(path)
        self.name.setText("Reading overlay…")
        self.preview.timer.start()

    def clear(self) -> None:
        """Invalidate queued work without blocking on a worker's local proxy."""
        self.generation += 1
        self.load_path = None
        self.dataset = None
        self.cache.clear()
        self.initial_thresholds = False
        self.controls.hide()
        self.remove.hide()
        self.status.hide()
        self.name.setText("One NIfTI on the exact DWI grid")
        for view in self.preview.views.values():
            view.canvas.overlay_image = None
            view.canvas.update()
        self.preview.update_position_label()

    def key(self, name: str) -> tuple:
        """Identify a plane independently of the DWI's diffusion volume."""
        view = self.preview.views[name]
        return (self.generation, self.volume.value() - 1, self.preview.position[view.geometry.axis])

    @property
    def pending(self) -> bool:
        """Report work to Preview's single serialized reader queue."""
        return self.load_path is not None or (
            self.dataset is not None
            and any(
                self.cache.get(name, (None, None))[0] != self.key(name)
                for name in self.preview.views
            )
        )

    def task(self) -> tuple[tuple, Callable]:
        """Snapshot immutable task arguments; no widget is accessed by the worker."""
        if self.load_path is not None:
            path, dwi = self.load_path, self.preview.dataset
            self.load_path = None
            return ("load", self.generation), lambda: OverlayDataset.load(path, dwi)
        name = next(
            n for n in self.preview.views if self.cache.get(n, (None, None))[0] != self.key(n)
        )
        key, dataset = self.key(name), self.dataset
        axis = self.preview.views[name].geometry.axis
        return (name, key), lambda: dataset.read_slice(key[1], axis=axis, index=key[2])

    def accept(self, token: tuple, value: object, error: str) -> None:
        """Reject obsolete generations or slices before publishing pixels/readout."""
        name, key = token
        if name == "load":
            if key != self.generation:
                return
            if not error:
                self.dataset = value
                self.name.setText(value.path.name)
                self.name.setToolTip(str(value.path))
                self.volume.blockSignals(True)
                self.volume.setRange(1, value.n_volumes)
                self.volume.setValue(1)
                self.volume.setSuffix(f" of {value.n_volumes}")
                self.volume.blockSignals(False)
                self.volume.setVisible(value.n_volumes > 1)
                self.controls.show()
                self.remove.show()
                self.initial_thresholds = True
        else:
            if self.dataset is None or key != self.key(name):
                return
            self.cache[name] = (key, value)
        if error:
            self.status.setText(
                f"Overlay refused: {error}" if name == "load" else f"Overlay read failed: {error}"
            )
            self.theme.bind(self.status, "error.bg", "error.fg")
            self.status.show()
            LOGGER.warning("%s", self.status.text())
            if name == "load":
                self.name.setText("No overlay loaded")
        if self.dataset is not None and not self.pending and self.initial_thresholds:
            planes = [p for _, p in self.cache.values() if p is not None]
            # Full finite range of the three planes, not a whole-image scan.
            finite = [p[np.isfinite(p)] for p in planes if np.isfinite(p).any()]
            low, high = (
                (min(float(p.min()) for p in finite), max(float(p.max()) for p in finite))
                if finite
                else robust_range([])
            )
            for control, number in ((self.low, low), (self.high, high)):
                control.blockSignals(True)
                control.setValue(number)
                control.blockSignals(False)
            self.initial_thresholds = False
        self.repaint_planes()

    def request_volume(self, value: int) -> None:
        """Select overlay volume independently and coalesce its three reads."""
        self.repaint_planes()
        self.preview.timer.start()

    def repaint_planes(self, *_: object) -> None:
        """Apply current geometry and display settings to valid cached native planes."""
        mask = self.mode.currentText() == "Mask"
        self.outline.setVisible(mask)
        self.colormap.setVisible(not mask)
        self.bar.setVisible(not mask)
        self.visible.setText("Visible" if self.visible.isChecked() else "Hidden")
        self.outline.setText(f"Outline: {'on' if self.outline.isChecked() else 'off'}")
        self.opacity_label.setText(f"Opacity {self.opacity.value()}%")
        low, high = self.low.value(), self.high.value()
        self.bar.low, self.bar.high, self.bar.colormap = low, high, self.colormap.currentText()
        self.bar.setAccessibleName(f"{self.colormap.currentText()} color bar: {low:g} to {high:g}")
        self.bar.update()
        for name, view in self.preview.views.items():
            view.canvas.overlay_image = None
            key, plane = self.cache.get(name, (None, None))
            if (
                self.dataset is not None
                and key == self.key(name)
                and plane is not None
                and self.visible.isChecked()
                and high >= low
            ):
                rgba = np.ascontiguousarray(
                    overlay_rgba(
                        view.geometry.display_plane(plane),
                        mode=self.mode.currentText(),
                        colormap=self.colormap.currentText(),
                        low=low,
                        high=high,
                        opacity=self.opacity.value() / 100,
                        outline=self.outline.isChecked(),
                        color=self.theme.color("viewer.overlay").getRgb()[:3],
                    )
                )
                view.canvas.overlay_image = QImage(
                    rgba.data,
                    rgba.shape[1],
                    rgba.shape[0],
                    rgba.strides[0],
                    QImage.Format.Format_RGBA8888,
                ).copy()
            view.canvas.update()
        if high < low and self.dataset is not None:
            self.status.setText(
                "Threshold maximum must be at least minimum. Overlay is hidden until corrected."
            )
            self.status.show()
        elif self.status.text().startswith("Threshold"):
            self.status.hide()
        self.preview.update_position_label()

    def readout(self) -> str:
        """Return the unthresholded, uncolored source value at the linked voxel."""
        if self.dataset is None:
            return ""
        for name, view in self.preview.views.items():
            key, plane = self.cache.get(name, (None, None))
            if key == self.key(name) and plane is not None:
                axes = [i for i in range(3) if i != view.geometry.axis]
                value = plane[tuple(self.preview.position[i] for i in axes)]
                return f"   ·   Overlay  {value:.7g}"
        return "   ·   Overlay  reading…" if self.pending else "   ·   Overlay  unavailable"
