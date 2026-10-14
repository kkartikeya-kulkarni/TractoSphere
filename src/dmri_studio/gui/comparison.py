"""Synchronized comparison composed from the existing native Preview widgets.

Launch with ``python -m dmri_studio.gui.comparison manifest.json``. The manifest
contains source paths and explicit paired indices, never commands. All reads
use the existing lazy dataset/worker architecture; sources are never rewritten.
"""

import json
import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QMainWindow,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.comparison import comparison_rgb, difference_metrics, validate_comparison
from dmri_studio.core.dataset import DatasetPaths, DwiDataset
from dmri_studio.core.loading import DatasetLoader

from .components import button, label
from .preview import Preview
from .theme import Theme


class ComparisonWidget(QWidget):
    """Explicit volume mapping, synchronized native coordinates and display modes."""

    def __init__(self, theme: Theme, datasets: dict[str, DwiDataset], manifest: dict) -> None:
        """Receive already validated sources; reuse Preview ownership and workers."""
        super().__init__()
        self.theme, self.datasets, self.manifest = theme, datasets, manifest
        self._syncing = False
        self.pairs: list[tuple[int, int]] = []
        self.current_mode: dict = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(label("Matched diffusion image comparison", "title"))
        layout.addWidget(
            label(
                manifest.get(
                    "notice",
                    "Scientific review pending; difference does not establish superiority.",
                ),
                "caption",
            )
        )
        controls = QHBoxLayout()
        self.mode = QComboBox()
        for mode in manifest["modes"]:
            self.mode.addItem(mode["label"])
        self.orientation = QComboBox()
        self.orientation.addItems(["Axial", "Coronal", "Sagittal", "Three views"])
        self.display = QComboBox()
        self.display.addItems(["Side by side", "Overlay", "Difference"])
        self.opacity = QDoubleSpinBox()
        self.opacity.setRange(0, 1)
        self.opacity.setSingleStep(0.1)
        self.opacity.setValue(0.5)
        self.opacity.setAccessibleName("Overlay fraction of right image")
        self.span = QDoubleSpinBox()
        self.span.setRange(0.00001, 1e9)
        self.span.setDecimals(5)
        self.span.setValue(1000)
        self.span.setAccessibleName("Signed difference color limit")
        layout.addWidget(self.mode)
        for w in [
            self.orientation,
            self.display,
            label("Overlay fraction", "caption"),
            self.opacity,
            label("± Difference", "caption"),
            self.span,
        ]:
            controls.addWidget(w)
        theme_button = button("Switch theme")
        theme_button.clicked.connect(
            lambda: theme.apply("light" if theme.name == "dark" else "dark")
        )
        controls.addWidget(theme_button)
        layout.addLayout(controls)
        self.description = label("", "caption")
        layout.addWidget(self.description)
        row = QHBoxLayout()
        self.left, self.right = Preview(theme), Preview(theme)
        for preview in [self.left, self.right]:
            row.addWidget(preview, 1)
            preview.slider.hide()
            preview.auto_volume.setChecked(False)
            preview.planes_changed.connect(lambda p=preview: self.refresh_from(p))
            preview.idle.connect(self.repaint_comparison)
        layout.addLayout(row, 1)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setAccessibleName("Matched volume correspondence")
        layout.addWidget(self.slider)
        self.metrics = label("", "mono")
        self.metrics.setAccessibleName("AI comparison evidence")
        layout.addWidget(self.metrics)
        self.left.navigation_changed.connect(lambda p, v: self.navigate(self.left, p, v))
        self.right.navigation_changed.connect(lambda p, v: self.navigate(self.right, p, v))
        self.mode.currentIndexChanged.connect(self.select_mode)
        self.orientation.currentIndexChanged.connect(self.select_orientation)
        self.slider.valueChanged.connect(self.select_pair)
        for control in [self.display, self.opacity, self.span]:
            signal = (
                control.currentIndexChanged
                if isinstance(control, QComboBox)
                else control.valueChanged
            )
            signal.connect(self.repaint_comparison)
        self.left.convention.toggled.connect(self.right.convention.setChecked)
        self.right.convention.toggled.connect(self.left.convention.setChecked)
        self.select_mode(0)

    def select_mode(self, index: int) -> None:
        """Validate each mapping before permitting synchronized native differences."""
        mode = self.manifest["modes"][index]
        pairs = [tuple(pair) for pair in mode["pairs"]]
        left, right = self.datasets[mode["left"]], self.datasets[mode["right"]]
        validate_comparison(left, right, pairs)
        self._syncing = True
        self.current_mode, self.pairs = mode, pairs
        self.left.set_dataset(left)
        self.right.set_dataset(right)
        self.left.title.setText(mode.get("left_label", mode["left"]))
        self.right.title.setText(mode.get("right_label", mode["right"]))
        self.slider.setRange(0, len(pairs) - 1)
        self.slider.setValue(0)
        self._syncing = False
        self.select_pair(0)
        self.select_orientation()

    def select_orientation(self, *_: object) -> None:
        """Expand the same existing view on both sides; no new renderer."""
        orientation = self.orientation.currentText().lower()
        for preview in [self.left, self.right]:
            preview.set_expanded(None if orientation == "three views" else orientation)
        self.repaint_comparison()

    def select_pair(self, index: int) -> None:
        """Update both original volume indices from the audited mapping."""
        if self._syncing or not self.pairs:
            return
        self._syncing = True
        a, b = self.pairs[index]
        self.left.slider.setValue(a)
        self.right.slider.setValue(b)
        self.right.set_position(self.left.position)
        self._syncing = False
        self.description.setText(
            f"Match {index + 1}/{len(self.pairs)} · source volumes (zero-based) {a} ↔ {b} · "
            + self.current_mode.get(
                "description", "Verified same grid; residual misalignment may remain."
            )
        )
        self.repaint_comparison()

    def navigate(self, sender: Preview, position: tuple, volume: int) -> None:
        """Propagate either crosshair/keyboard path without recursive updates."""
        if self._syncing or not self.pairs:
            return
        side = 0 if sender is self.left else 1
        mapped = next((i for i, pair in enumerate(self.pairs) if pair[side] == volume), None)
        if mapped is not None and mapped != self.slider.value():
            self.slider.setValue(mapped)
        elif mapped is None:
            self.select_pair(self.slider.value())
        other = self.right if sender is self.left else self.left
        self._syncing = True
        other.set_position(position)
        self._syncing = False
        self.repaint_comparison()

    def repaint_comparison(self, *_: object) -> None:
        """Use matching cached planes only; stale differences are never displayed."""
        if self._syncing:
            return
        mode = self.display.currentText()
        self.opacity.setEnabled(mode == "Overlay")
        self.span.setEnabled(mode == "Difference")
        metrics = []
        for name in self.left.views:
            left, right = self.left.views[name], self.right.views[name]
            if (
                left.plane is None
                or right.plane is None
                or left.completed_key != left.desired_key
                or right.completed_key != right.desired_key
            ):
                continue
            # Use one left-hand intensity window for comparable grayscale display.
            right.canvas.display_range = left.canvas.display_range
            if mode == "Side by side":
                right.canvas.set_plane(right.plane)
            else:
                rgb = comparison_rgb(
                    left.plane,
                    right.plane,
                    mode,
                    left.canvas.display_range,
                    opacity=self.opacity.value(),
                    difference_limit=self.span.value(),
                )
                pixels = np.ascontiguousarray(
                    np.stack(
                        [right.geometry.display_plane(rgb[..., channel]) for channel in range(3)],
                        axis=-1,
                    )
                )
                right.canvas.image = QImage(
                    pixels.data,
                    pixels.shape[1],
                    pixels.shape[0],
                    pixels.strides[0],
                    QImage.Format.Format_RGB888,
                ).copy()
                right.canvas.message = ""
                right.canvas.overlay_image = None
                right.canvas.update()
            if name == self.orientation.currentText().lower():
                metrics.append(difference_metrics(left.plane, right.plane))
        saved = self.current_mode.get("metrics", [])
        selected = saved[self.slider.value()] if saved else (metrics[0] if metrics else {})
        values = []
        for key, title in [
            ("shell", "Shell"),
            ("RMSE", "RMSE"),
            ("relative_L2", "Relative L2"),
            ("correlation", "Correlation"),
            ("nonfinite_pairs", "Nonfinite pairs"),
        ]:
            value = selected.get(key)
            if value is not None:
                values.append(
                    f"{title}: {value:.5g}" if isinstance(value, float) else f"{title}: {value}"
                )
        self.metrics.setText(
            ("Saved whole-volume metrics · " if saved else "Displayed-plane metrics · ")
            + " · ".join(values)
            + "\nDifference = right − left; red positive, blue negative, white zero, magenta nonfinite. Scientific acceptance pending."
        )
        self.metrics.setToolTip(json.dumps(selected, indent=2, ensure_ascii=False))

    def refresh_from(self, sender: Preview) -> None:
        """Synchronize contrast controls/readouts as well as displayed pixels."""
        if self._syncing:
            return
        other = self.right if sender is self.left else self.left
        if other.display_range != sender.display_range:
            self._syncing = True
            try:
                other.set_display_range(*sender.display_range)
            finally:
                self._syncing = False
        self.repaint_comparison()


class ComparisonWindow(QMainWindow):
    """Hold source lifetimes until existing viewer workers have retired."""

    def __init__(self, theme: Theme, datasets: dict, manifest: dict) -> None:
        """Wrap the reusable comparison panel without changing the main shell."""
        super().__init__()
        self.setWindowTitle("TractoSphere · Image comparison · review pending")
        self.panel = ComparisonWidget(theme, datasets, manifest)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.panel)
        self.setCentralWidget(scroll)
        self.resize(1500, 960)
        self._closing = False

    def closeEvent(self, event: object) -> None:
        """Drain finite lazy reads; never destroy a running QThread or source."""
        if not self._closing:
            self._closing = True
            self.panel.left.set_dataset(None)
            self.panel.right.set_dataset(None)
        if any(p.thread is not None for p in [self.panel.left, self.panel.right]):
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
        for dataset in self.panel.datasets.values():
            dataset.close()
        event.accept()


def main() -> int:
    """Load metadata before showing the UI; plane reads remain asynchronous."""
    manifest = json.loads(Path(sys.argv[1]).read_text())
    datasets = {}
    for key, item in manifest["sources"].items():
        result = DatasetLoader().load(
            DatasetPaths(Path(item["dwi"]), Path(item["bval"]), Path(item["bvec"]))
        )
        if result.dataset is None:
            raise ValueError(
                f"Comparison input validation failed: {key}: {result.report.to_dict()}"
            )
        datasets[key] = result.dataset
    app = QApplication(sys.argv[:1])
    theme = Theme(app, "dark")
    window = ComparisonWindow(theme, datasets, manifest)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
