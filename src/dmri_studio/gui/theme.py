"""Single design-system entry point: fonts, metrics, palettes and Lucide tinting.

The fixed stylesheet is installed once; switching themes updates palettes and
icons in place. All measurements are logical pixels except the font point sizes.
"""

import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QLabel,
    QPushButton,
    QWidget,
)

from .tokens import COLORS

ASSETS = Path(__file__).parent / "assets"
SPACE = (0, 4, 8, 12, 16, 24, 32, 48, 64)
PAGE_INSET = 20
CARD_PADDING = 16
GAP = 16
CONTROL = 32
COMPACT = 28
SIDEBAR = 204
RAIL = 52
MOTION = 0
ICON_SIZE = QSize(18, 18)


def font(style: str = "body") -> QFont:
    """Return a readable platform system font; never bundle Apple font files."""
    system = QFontDatabase.systemFont(
        QFontDatabase.SystemFont.FixedFont
        if style == "mono"
        else QFontDatabase.SystemFont.GeneralFont
    )
    base = max(12.0, QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).pointSizeF())
    size, weight = {
        "title": (base + 8, 600),
        "section": (base, 500),
        "body": (base, 400),
        "control": (base, 400),
        "caption": (max(11, base - 2), 400),
        "metric": (base + 8, 500),
        "mono": (max(11, base - 2), 400),
    }[style]
    result = QFont(system)
    result.setPointSizeF(size)
    result.setWeight(QFont.Weight(weight))
    return result


class Theme(QObject):
    """Apply semantic palettes without rebuilding widgets or application QSS."""

    changed = Signal()

    def __init__(self, app: QApplication, name: str = "dark") -> None:
        """Resolve the native palette and install the structural stylesheet once."""
        super().__init__(app)
        self.app = app
        previous = getattr(app, "tractosphere_theme", None)
        if previous is not None:
            # A test/application can replace the presentation controller. Old
            # platform listeners must not recolor windows that have been closed.
            app.styleHints().colorSchemeChanged.disconnect(previous.system_appearance_changed)
            app.paletteChanged.disconnect(previous.native_palette_changed)
        self.reduced_motion = True
        self.native_accent = QColor(app.palette().color(QPalette.ColorRole.Accent))
        self.preference = name
        self._applying = False
        app.tractosphere_theme = self
        self.name = self.resolve(name)
        self.surfaces: list[tuple[QWidget, str, str]] = []
        self.icons: list[tuple[QWidget, str, str]] = []
        # Keep native macOS widgets; Windows/Linux retain the portable Qt style.
        if sys.platform != "darwin":
            app.setStyle("Fusion")
        app.setFont(font())
        app.setStyleSheet((ASSETS / "shell.qss").read_text())
        self.apply(name)
        app.styleHints().colorSchemeChanged.connect(self.system_appearance_changed)
        app.paletteChanged.connect(self.native_palette_changed)

    def resolve(self, preference):
        """Resolve system appearance without inventing a per-platform theme."""
        if preference in {"light", "dark"}:
            return preference
        return "dark" if self.app.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"

    def system_appearance_changed(self, *_):
        if self.preference == "system" and not self._applying:
            self.apply("system")

    def native_palette_changed(self, palette):
        if not self._applying:
            self.native_accent = QColor(palette.color(QPalette.ColorRole.Accent))
            self.apply(self.preference)

    def color(self, token: str) -> QColor:
        """Resolve one named token for the active theme."""
        if token in {"accent.primary", "focus.ring"} and self.native_accent.isValid():
            return QColor(self.native_accent)
        return QColor(COLORS[token][self.name == "light"])

    def palette(self, background: str = "bg.canvas", foreground: str = "text.primary") -> QPalette:
        """Build all relevant palette roles including disabled and focus states."""
        p = QPalette()
        roles = {
            "Window": background,
            "WindowText": foreground,
            "Base": "bg.surface",
            "AlternateBase": "bg.sidebar",
            "Text": foreground,
            "Button": "bg.surface",
            "ButtonText": foreground,
            "Highlight": "accent.primary",
            "HighlightedText": "accent.on",
            "Mid": "border.control",
            "Midlight": "border.subtle",
            "Light": "bg.hover",
            "Dark": "bg.pressed",
            "Link": "accent.text",
            "ToolTipBase": "bg.raised",
            "ToolTipText": "text.primary",
            "PlaceholderText": "text.muted",
            "Accent": "accent.primary",
        }
        for role, token in roles.items():
            p.setColor(getattr(QPalette.ColorRole, role), self.color(token))
        for role in (
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
            QPalette.ColorRole.WindowText,
        ):
            p.setColor(QPalette.ColorGroup.Disabled, role, self.color("text.disabled"))
        p.setColor(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, self.color("bg.disabled")
        )
        return p

    def bind(
        self, widget: QWidget, background: str = "bg.surface", foreground: str = "text.primary"
    ) -> None:
        """Register a semantic surface, or replace its status after a load."""
        self.surfaces = [(w, b, f) for w, b, f in self.surfaces if w is not widget]
        self.surfaces.append((widget, background, foreground))
        widget.setAutoFillBackground(True)
        widget.setPalette(self.palette(background, foreground))
        self.style_widget(widget, background, foreground, True)

    def icon(self, name: str, token: str = "text.secondary") -> QIcon:
        """Render the bundled SVG at Retina resolution with explicit stroke tint."""
        # Qt resolves SF Symbols from the OS. No Apple symbol assets are stored.
        symbols = {
            "file-input": "square.and.arrow.down",
            "folder-open": "folder",
            "database": "square.stack.3d.up",
            "scan": "photo",
            "clipboard-check": "checklist",
            "sliders-horizontal": "slider.horizontal.3",
            "brain": "waveform.path",
            "route": "point.3.connected.trianglepath.dotted",
            "network": "point.3.filled.connected.trianglepath.dotted",
            "list-checks": "checkmark.seal",
            "files": "clock.arrow.circlepath",
            "settings": "gearshape",
            "panel-left-close": "sidebar.left",
            "panel-left-open": "sidebar.left",
            "search": "magnifyingglass",
            "sun": "sun.max",
            "moon": "moon",
            "terminal": "terminal",
            "chevron-right": "chevron.right",
            "chevron-down": "chevron.down",
            "rotate-ccw": "arrow.counterclockwise",
            "maximize": "arrow.up.left.and.arrow.down.right",
            "minimize": "arrow.down.right.and.arrow.up.left",
            "layers": "square.3.layers.3d",
            "plus": "plus",
            "x": "xmark",
            "copy": "doc.on.doc",
            "download": "square.and.arrow.down",
            "upload": "square.and.arrow.up",
            "play": "play",
            "square": "stop",
            "info": "info.circle",
            "shield-check": "checkmark.shield",
            "arrow-right": "arrow.right",
            "file-chart-column": "doc.text",
            "book-open": "book",
        }
        native = QIcon.fromTheme(symbols.get(name, name)) if sys.platform == "darwin" else QIcon()
        if not native.isNull():
            pixmap = native.pixmap(QSize(36, 36))
            painter = QPainter(pixmap)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), self.color(token))
            painter.end()
            pixmap.setDevicePixelRatio(2)
            return QIcon(pixmap)
        data = (
            (ASSETS / "icons" / f"{name}.svg")
            .read_text()
            .replace("currentColor", self.color(token).name())
        )
        pixmap = QPixmap(48, 48)
        pixmap.fill(QColor("transparent"))
        painter = QPainter(pixmap)
        QSvgRenderer(QByteArray(data.encode())).render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(2)
        return QIcon(pixmap)

    def set_icon(self, widget: QWidget, name: str, token: str = "text.secondary") -> None:
        """Keep button icon tint synchronized with theme changes."""
        self.icons = [(w, n, t) for w, n, t in self.icons if w is not widget]
        self.icons.append((widget, name, token))
        widget.setIcon(self.icon(name, token))
        if hasattr(widget, "setIconSize"):
            widget.setIconSize(ICON_SIZE)

    def style_widget(self, widget: QWidget, background: str, foreground: str, bound: bool) -> None:
        """Refresh explicit local brushes where Qt caches stylesheet palette roles.

        Geometry stays in shell.qss. Only this theme layer emits color rules;
        the application stylesheet itself is never replaced during switching.
        """
        name = f"theme_{id(widget)}"
        widget.setObjectName(name)
        selector = f"#{name}"

        def color(token: str) -> str:
            """Resolve a CSS color exclusively through the shared token map."""
            return self.color(token).name()

        rules = []
        if bound:
            rules.append(
                f"{selector} {{ background-color: {color(background)}; color: {color(foreground)}; }}"
            )
        if isinstance(widget, QAbstractItemView):
            rules.append(
                f"{selector}::item:selected {{ background-color: {color('accent.subtle')}; color: {color('accent.text')}; }}"
            )
        if isinstance(widget, QComboBox):
            arrow = (ASSETS / "icons" / f"control-chevron-{self.name}.svg").as_posix()
            rules.append(
                f'{selector}::down-arrow {{ image: url("{arrow}"); width: 14px; height: 14px; }}'
            )
        if isinstance(widget, QLabel):
            token = foreground
            if foreground == "text.primary" and widget.property("textStyle") == "caption":
                token = "text.muted"
            rules.append(f"{selector} {{ color: {color(token)}; }}")
        if isinstance(widget, QPushButton):
            primary = widget.property("primary")
            quiet = widget.property("quiet")
            selected = widget.property("selected")
            fill = "accent.primary" if primary else "accent.subtle" if selected else "bg.surface"
            text = "accent.on" if primary else "accent.text" if selected else foreground
            bg = color(fill) if primary or selected or not quiet else "transparent"
            rules.append(f"{selector} {{ background-color: {bg}; color: {color(text)}; }}")
            hover = "accent.hover" if primary else "bg.hover"
            pressed = "accent.pressed" if primary else "bg.pressed"
            rules.append(f"{selector}:hover {{ background-color: {color(hover)}; }}")
            rules.append(f"{selector}:pressed {{ background-color: {color(pressed)}; }}")
            disabled_bg = "transparent" if quiet else color("bg.disabled")
            rules.append(
                f"{selector}:disabled {{ color: {color('text.disabled')}; background-color: {disabled_bg}; border-color: transparent; }}"
            )
            if widget.property("leftAligned"):
                rules.append(f"{selector} {{ text-align: left; }}")
        widget.setStyleSheet("\n".join(rules))

    def apply(self, name: str) -> None:
        """Switch colors immediately while retaining all application state."""
        self.preference = name
        self.name = self.resolve(name)
        self._applying = True
        self.app.setPalette(self.palette())
        # Deleted transient disclosures may still be in the palette registry.
        from shiboken6 import isValid

        self.surfaces = [(w, b, f) for w, b, f in self.surfaces if isValid(w)]
        self.icons = [(w, n, t) for w, n, t in self.icons if isValid(w)]
        # Qt stylesheet palette() brushes are cached per widget. Re-polish
        # every existing widget after assigning its nearest semantic surface;
        # do not replace the application stylesheet or recreate the window.
        bindings = {id(w): (b, f) for w, b, f in self.surfaces}
        for widget in self.app.allWidgets():
            ancestor = widget
            while ancestor is not None and id(ancestor) not in bindings:
                ancestor = ancestor.parentWidget()
            background, foreground = bindings.get(id(ancestor), ("bg.canvas", "text.primary"))
            widget.style().unpolish(widget)
            widget.setPalette(self.palette(background, foreground))
            widget.style().polish(widget)
            self.style_widget(widget, background, foreground, id(widget) in bindings)
            widget.update()
        for widget, icon, token in self.icons:
            widget.setIcon(self.icon(icon, token))
        self._applying = False
        self.changed.emit()
