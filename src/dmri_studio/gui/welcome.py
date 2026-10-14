"""TractoSphere identity and bounded software-rendered startup illustration.

No subject data, external files, GPU context or tractography reconstruction.
Precomputed 3D curves suggest commissural, association and projection pathways;
they are explicitly illustrative, not anatomical evidence or measured tracts.
"""

import math
import os
import sys
import time

import numpy as np
from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QVBoxLayout, QWidget

from .components import button, label
from .settings import Settings
from .theme import ASSETS, Theme, font


def reduced_motion(settings: Settings) -> bool:
    """Honor the explicit preference/environment and macOS accessibility setting."""
    if (
        settings.get("reduced_motion", False)
        or os.environ.get("TRACTOSPHERE_REDUCED_MOTION") == "1"
    ):
        return True
    if sys.platform == "darwin":
        import subprocess

        try:
            return (
                subprocess.run(
                    ["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
                    capture_output=True,
                    text=True,
                    timeout=0.3,
                    check=False,
                ).stdout.strip()
                == "1"
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    return False


class BrainScene(QWidget):
    """Small deterministic 3D scene, projected with QPainter at at most 20 fps."""

    def __init__(self, theme: Theme, animate: bool = True) -> None:
        """Precompute the bounded scene once; do not start a hidden animation."""
        super().__init__()
        self.theme, self.animate = theme, animate
        self.setMinimumSize(280, 260)
        self.setAccessibleName("Illustrative brain with progressively revealed pathways")
        self.started = time.monotonic()
        self.elapsed = 9.0 if not animate else 0.0
        self.lines = self.geometry()
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.advance)
        theme.changed.connect(self.update)

    @staticmethod
    def geometry() -> list[tuple[np.ndarray, str, float]]:
        """Build bounded parametric hemispheres and smooth synthetic fiber bundles."""
        lines = []

        # Bilateral lobulated envelopes, rather than disconnected ellipsoids.
        # These are deliberately illustrative surfaces, never subject anatomy.
        def envelope(hemi: int, lat: np.ndarray | float, lon: np.ndarray | float) -> np.ndarray:
            """Evaluate a lobulated hemisphere surface in illustration coordinates."""
            lat, lon = np.broadcast_arrays(lat, lon)
            fold = 1 + 0.045 * np.sin(9 * lon + 7 * lat) * np.cos(5 * lat - 2 * lon)
            width = np.cos(lat) ** 0.65
            x = hemi * (0.025 + 0.78 * width * (0.5 + 0.5 * np.cos(lon)))
            y = 0.98 * np.sin(lon) * np.cos(lat) * fold
            z = 0.79 * np.sin(lat) * fold + 0.07 * np.sin(lon)
            return np.column_stack((x, y, z))

        for hemi in (-1, 1):
            for lat in np.linspace(-1.47, 1.47, 20):
                lon = np.linspace(0, 2 * np.pi, 64)
                gyri = lat + 0.036 * np.sin(11 * lon + lat * 5)
                lines.append((envelope(hemi, gyri, lon), "surface", 0))
            for lon in np.linspace(0, 2 * np.pi, 12, endpoint=False):
                lat = np.linspace(-1.47, 1.47, 48)
                gyri = lon + 0.035 * np.sin(16 * lat + lon * 4)
                lines.append((envelope(hemi, lat, gyri), "surface", 0))
        t = np.linspace(0, 1, 40)
        # Separate curved strands spread in 3D, with varied endpoints and depth.
        # RGB-inspired direction families recall diffusion DEC convention.
        for i in range(40):
            q = (i + 0.5) / 40
            y = -0.65 + 1.3 * q
            width = 0.54 + 0.15 * np.sin(np.pi * q)
            xyz = np.column_stack(
                (
                    width * np.cos(np.pi * t),
                    y + 0.07 * np.sin(2 * np.pi * t + i),
                    -0.08 + 0.42 * np.sin(np.pi * t) + 0.12 * np.cos(q * 4),
                )
            )
            lines.append((xyz, "commissural", i / 180))
        for side in (-1, 1):
            for i in range(30):
                q = (i + 0.5) / 30
                a = -0.83 + 1.66 * t
                xyz = np.column_stack(
                    (
                        side * (0.21 + 0.39 * q + 0.07 * np.sin(np.pi * t + i * 0.3)),
                        a * (0.72 + 0.23 * np.sin(q * np.pi)),
                        -0.24 + 0.36 * np.sin(np.pi * t) + 0.26 * np.sin(q * 3),
                    )
                )
                lines.append((xyz, "association", 0.18 + i / 170))
            for i in range(30):
                q = (i + 0.5) / 30
                xyz = np.column_stack(
                    (
                        side * (0.055 + (0.15 + 0.43 * q) * np.sin(t * np.pi / 2)),
                        -0.13 + (q - 0.5) * 1.2 * t**1.7,
                        -0.68 + 1.31 * t - 0.15 * q * t,
                    )
                )
                lines.append((xyz, "projection", 0.42 + i / 180))
        return lines

    def advance(self) -> None:
        """Pause hidden/minimized windows; stop startup animation after 18 seconds."""
        if not self.isVisible() or self.window().isMinimized():
            return
        self.elapsed = time.monotonic() - self.started
        if self.elapsed >= 18:
            self.timer.stop()
        self.update()

    def showEvent(self, event: object) -> None:
        """Only visible startup scenes schedule frames."""
        super().showEvent(event)
        if self.animate and self.elapsed < 18:
            self.timer.start()

    def hideEvent(self, event: object) -> None:
        """Release animation activity immediately when the window disappears."""
        self.timer.stop()
        super().hideEvent(event)

    def set_motion(self, enabled: bool) -> None:
        """Switch to a completed static frame when reduced motion is requested."""
        self.animate = enabled
        if enabled:
            self.started = time.monotonic()
            self.elapsed = 0
            self.timer.start()
        else:
            self.timer.stop()
            self.elapsed = 9
        self.update()

    def paintEvent(self, event: object) -> None:
        """Project precomputed curves without a GPU context or scientific calculation."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        angle = 0.46 + (0.13 * math.sin(self.elapsed * 0.18) if self.animate else 0)
        ca, sa = math.cos(angle), math.sin(angle)
        tilt = -0.28
        ct, st = math.cos(tilt), math.sin(tilt)
        rotation = np.array([[ca, -sa, 0], [sa * ct, ca * ct, -st], [sa * st, ca * st, ct]])
        scale = min(self.width() / 2.5, self.height() / 2.2)
        center = QPointF(self.width() / 2, self.height() / 2)
        colors = {
            "surface": self.theme.color("text.muted"),
            "commissural": QColor("#D29A92"),
            "association": QColor("#91C2A8"),
            "projection": QColor("#9EABDA"),
        }
        draw = []
        for points, kind, delay in self.lines:
            points = points @ rotation.T
            draw.append((float(points[:, 1].mean()), points, kind, delay))
        for depth, points, kind, delay in sorted(draw, key=lambda v: v[0]):
            fraction = min(1, max(0, self.elapsed / 6 - delay)) if self.animate else 1
            count = len(points) if kind == "surface" else int(len(points) * fraction)
            if count < 2:
                continue
            color = QColor(colors[kind])
            color.setAlphaF(
                (0.08 + 0.10 * (depth + 1) / 2)
                if kind == "surface"
                else 0.28 + 0.28 * (depth + 1) / 2
            )
            painter.setPen(QPen(color, 0.65 if kind == "surface" else 0.8))
            path = QPainterPath()
            for j, (x, _, z) in enumerate(points[:count]):
                p = center + QPointF(float(x * scale), float(-z * scale))
                path.moveTo(p) if j == 0 else path.lineTo(p)
            painter.drawPath(path)


class WelcomeWindow(QWidget):
    """Skippable branding; Enter opens the existing workstation without loading data."""

    entered = Signal()

    def __init__(self, theme: Theme, settings: Settings) -> None:
        """Provide immediate entry and persistent presentation-only preferences."""
        super().__init__()
        self.theme, self.settings = theme, settings
        self.setWindowTitle("TractoSphere — Diffusion MRI Research Studio")
        self.setWindowIcon(QIcon(str(ASSETS / "branding/tractosphere-app.svg")))
        self.resize(1080, 720)
        self.setMinimumSize(640, 520)
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 20, 36, 24)
        top = QHBoxLayout()
        eyebrow = label("TRACTOSPHERE  /  RESEARCH WORKSTATION", "caption")
        eyebrow.setWordWrap(False)
        top.addWidget(eyebrow)
        top.addStretch()
        top.addWidget(button("Skip welcome", self.enter, quiet=True))
        root.addLayout(top)
        try:
            self.scene = BrainScene(theme, not reduced_motion(settings))
        except Exception:
            # Static vector fallback contains no renderer/GPU requirement.
            self.scene = QSvgWidget(str(ASSETS / "branding/tractosphere.svg"))
        root.addWidget(self.scene, 1)
        title = label("TractoSphere", "title")
        title_font = font("title")
        title_font.setPointSizeF(30)
        title.setFont(title_font)
        for text in (
            title,
            label("Diffusion MRI Research Studio", "section"),
            label("From Diffusion to Discovery", "caption"),
        ):
            text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            root.addWidget(text)
        row = QHBoxLayout()
        row.addStretch()
        self.enter_button = button("ENTER STUDIO", self.enter, primary=True)
        self.enter_button.setMinimumWidth(210)
        self.enter_button.setDefault(True)
        self.enter_shortcut = QShortcut(QKeySequence("Return"), self)
        self.enter_shortcut.activated.connect(self.enter)
        row.addWidget(self.enter_button)
        row.addStretch()
        root.addLayout(row)
        options = QHBoxLayout()
        options.addStretch()
        self.reduce = QCheckBox("Reduced motion")
        self.reduce.setChecked(reduced_motion(settings))
        self.reduce.toggled.connect(self.motion_changed)
        self.show_next = QCheckBox("Show welcome at startup")
        self.show_next.setChecked(settings.get("show_welcome", True))
        self.show_next.toggled.connect(lambda v: settings.set("show_welcome", v))
        options.addWidget(self.reduce)
        options.addWidget(self.show_next)
        options.addStretch()
        root.addLayout(options)
        note = label(
            "Illustrative pathways · No reconstruction or dataset loading at startup", "caption"
        )
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(note)
        theme.apply(theme.name)

    def motion_changed(self, reduced: bool) -> None:
        """Persist the user choice without changing any processing settings."""
        self.settings.set("reduced_motion", reduced)
        self.theme.reduced_motion = reduced
        if isinstance(self.scene, BrainScene):
            self.scene.set_motion(not reduced)

    def enter(self) -> None:
        """Hand off to the already constructed workstation and stop drawing."""
        self.entered.emit()
        self.close()
