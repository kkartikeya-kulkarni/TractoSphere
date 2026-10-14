"""Reusable shell controls and direct presentation of core validation evidence.

Cards and disclosures use native layouts, selectable text and accessible labels.
No status, threshold, or scientific interpretation is inferred by these widgets.
"""

import json
from collections import deque
from datetime import datetime

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.validation import ValidationReport, json_compatible

from .theme import CARD_PADDING, COMPACT, GAP, MOTION, Theme, font


def label(text: str, style: str = "body") -> QLabel:
    """Make wrapping, selectable plain text, safe for arbitrary filenames."""
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setFont(font(style))
    widget.setProperty("textStyle", style)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


class ElidedLabel(QLabel):
    """Keep complete identity text accessible while painting a bounded header."""

    def __init__(self, text: str, style: str = "caption") -> None:
        """Use normal theme typography; ellipsis never changes the stored value."""
        super().__init__(text)
        self.setFont(font(style))
        self.setProperty("textStyle", style)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setToolTip(text)

    def setText(self, text: str) -> None:
        """Expose the full dataset/project identity through the tooltip and API."""
        super().setText(text)
        self.setToolTip(text)
        self.setAccessibleName(text)

    def paintEvent(self, event: object) -> None:
        """Draw a single elided line instead of clipped fragments or wrapped paths."""
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().windowText().color())
        text = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideMiddle, self.width()
        )
        painter.drawText(
            self.rect(), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text
        )


def button(
    text: str, callback: object = None, *, quiet: bool = False, primary: bool = False
) -> QPushButton:
    """Create a keyboard-accessible action with a consistent target size."""
    widget = QPushButton(text)
    widget.setFont(font("control"))
    widget.setProperty("quiet", quiet)
    widget.setProperty("iconOnly", not bool(text))
    widget.setProperty("primary", primary)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if callback is not None:
        widget.clicked.connect(callback)
    theme = getattr(QApplication.instance(), "tractosphere_theme", None)
    # One pinned Lucide family for ordinary actions, never decorative raster art.
    prefixes = {
        "open": "folder-open",
        "browse": "folder-open",
        "choose": "file-input",
        "load": "upload",
        "import": "upload",
        "export": "download",
        "save": "download",
        "copy": "copy",
        "new": "plus",
        "clear": "x",
        "cancel": "square",
        "stop": "square",
        "reset": "rotate-ccw",
        "refresh": "rotate-ccw",
        "confirm": "shield-check",
        "prepare": "list-checks",
        "inspect": "scan",
        "continue": "arrow-right",
        "review": "list-checks",
        "enter": "arrow-right",
        "approve": "play",
        "record": "file-chart-column",
        "models": "sliders-vertical",
        "auto": "sliders-horizontal",
    }
    if theme and (word := text.split(" ", 1)[0].lower()) in prefixes:
        theme.set_icon(widget, prefixes[word], "accent.on" if primary else "text.secondary")
    return widget


class Card(QFrame):
    """A quiet rounded surface with one shared padding rule."""

    def __init__(self, theme: Theme, title: str = "") -> None:
        """Create a vertical card, optionally with a section heading."""
        super().__init__()
        self.setProperty("card", True)
        theme.bind(self)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*([CARD_PADDING] * 4))
        self.body.setSpacing(12)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        if title:
            self.body.addWidget(label(title, "section"))


class Disclosure(QWidget):
    """Expand evidence in place without opening a modal or losing context."""

    def __init__(self, title: str, content: QWidget, expanded: bool = False) -> None:
        """Keep a readable title and native keyboard toggle."""
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.toggle = button(title, quiet=True)
        self.theme = getattr(QApplication.instance(), "tractosphere_theme", None)
        if self.theme:
            self.theme.set_icon(self.toggle, "chevron-down" if expanded else "chevron-right")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setAccessibleName(title)
        self.toggle.setProperty("leftAligned", True)
        self.content, self.title = content, title
        layout.addWidget(self.toggle)
        layout.addWidget(content)
        content.setVisible(expanded)
        self.toggle.toggled.connect(self.set_expanded)

    def set_expanded(self, expanded: bool) -> None:
        """Preserve child state and expose expanded state to accessibility APIs."""
        self.content.setVisible(expanded)
        self.toggle.setText(self.title)
        if self.theme:
            self.theme.set_icon(self.toggle, "chevron-down" if expanded else "chevron-right")


class ValidationPanel(Card):
    """Render report findings, with problems first and all original evidence."""

    def __init__(self, theme: Theme, report: ValidationReport, title: str = "Validation") -> None:
        """Build status totals and expandable rows from the immutable core report."""
        super().__init__(theme, title)
        self.report = report
        counts = QHBoxLayout()
        tones = {
            "pass": "success",
            "info": "info",
            "warn": "warning",
            "fail": "error",
            "skipped": None,
        }
        for status, number in report.counts().items():
            badge = label(f"{number} {status.upper()}", "caption")
            badge.setContentsMargins(8, 4, 8, 4)
            tone = tones[status]
            theme.bind(
                badge,
                f"{tone}.bg" if tone else "bg.sidebar",
                f"{tone}.fg" if tone else "text.muted",
            )
            counts.addWidget(badge)
        counts.addStretch()
        self.body.addLayout(counts)
        self.body.addWidget(label(report.coverage_message(), "caption"))
        order = {"fail": 0, "warn": 1, "info": 2, "skipped": 3, "pass": 4}
        ordinary = QWidget()
        ordinary_layout = QVBoxLayout(ordinary)
        ordinary_layout.setContentsMargins(0, 0, 0, 0)
        self.rows: list[Disclosure] = []
        for check in sorted(report.checks, key=lambda c: order[c.status]):
            evidence = []
            for key, value in [
                ("Observed", check.observed),
                ("Expected", check.expected),
                ("Suggested action", check.remediation),
                ("Source", check.source),
            ]:
                if value is not None:
                    encoded = (
                        value
                        if isinstance(value, str)
                        else json.dumps(json_compatible(value), ensure_ascii=False, indent=2)
                    )
                    evidence.append(f"{key}: {encoded}")
            if check.volume_indices:
                evidence.append(
                    "Volumes (one-based): " + ", ".join(str(i + 1) for i in check.volume_indices)
                )
            detail = label("\n\n".join(evidence) or "No additional evidence recorded.", "caption")
            row = Disclosure(f"{check.status.upper()} · {check.code}  {check.message}", detail)
            # A wrapping plain-text label prevents long findings from setting the
            # whole page's minimum width, while the toggle stays compact.
            row.toggle.setText(f"▸  {check.status.upper()} · {check.code}   Show details")
            row.title = f"{check.status.upper()} · {check.code}   Show details"
            row.layout().insertWidget(0, label(check.message))
            if check.status in ("warn", "fail") and check.remediation:
                row.layout().insertWidget(1, label(check.remediation, "caption"))
            tone = tones[check.status]
            if tone:
                theme.bind(row.toggle, "bg.surface", f"{tone}.fg")
            self.rows.append(row)
            if check.status in ("fail", "warn"):
                self.body.addWidget(row)
            else:
                ordinary_layout.addWidget(row)
        self.body.addWidget(Disclosure("All other checks", ordinary))
        technical = QPlainTextEdit(report.to_json())
        technical.setReadOnly(True)
        technical.setFont(font("mono"))
        technical.setMinimumHeight(180)
        technical.setAccessibleName("Complete validation evidence, selectable and copyable")
        self.body.addWidget(Disclosure("Advanced details", technical))


class LogDrawer(QWidget):
    """Bounded local execution log with filtering, copying and scroll control."""

    toggled = Signal(bool)

    def __init__(self, theme: Theme) -> None:
        """Store real records; an empty log starts genuinely empty."""
        super().__init__()
        self.records: deque[tuple[float, str, str, str]] = deque(maxlen=10000)
        self.discarded = 0
        self.open = False
        self._rendering = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = button("Logs · 0 entries", self.toggle, quiet=True)
        theme.set_icon(self.header, "terminal")
        self.header.setMaximumHeight(COMPACT)
        self.header.setMinimumHeight(0)
        layout.addWidget(self.header)
        self.body = QWidget()
        theme.bind(self.body, "bg.log")
        body = QVBoxLayout(self.body)
        body.setContentsMargins(16, 8, 16, 8)
        controls = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItems(["All levels", "INFO", "WARNING", "ERROR", "DEBUG"])
        self.filter.setAccessibleName("Log severity filter")
        self.filter.currentTextChanged.connect(self.render)
        controls.addWidget(self.filter)
        controls.addWidget(button("Copy selected", self.copy_selected, quiet=True))
        controls.addWidget(button("Copy all", self.copy_all, quiet=True))
        controls.addWidget(button("Clear visible log", self.clear, quiet=True))
        controls.addStretch()
        self.auto = QCheckBox("Auto-scroll")
        self.auto.setChecked(True)
        controls.addWidget(self.auto)
        body.addLayout(controls)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(font("mono"))
        self.text.setAccessibleName("Execution log")
        self.text.document().setMaximumBlockCount(20000)
        self.text.verticalScrollBar().valueChanged.connect(self.scrolled)
        body.addWidget(self.text)
        layout.addWidget(self.body)
        self.body.hide()

    def toggle(self) -> None:
        """Request an expanded or collapsed splitter allocation."""
        self.open = not self.open
        self.body.setVisible(self.open)
        self.toggled.emit(self.open)

    def append(self, record: tuple[float, str, str, str]) -> None:
        """Bound retained records and disclose eviction in the log header."""
        if len(self.records) == self.records.maxlen:
            self.discarded += 1
        self.records.append(record)
        self.render()

    def render(self, *_: object) -> None:
        """Render the selected level while preserving deliberate scroll position."""
        self._rendering = True
        scrollbar = self.text.verticalScrollBar()
        old = scrollbar.value()
        lines = [
            self.format_record(r)
            for r in self.records
            if self.filter.currentText() in ("All levels", r[1])
        ]
        self.text.setPlainText("\n".join(lines))
        scrollbar.setValue(scrollbar.maximum() if self.auto.isChecked() else old)
        suffix = f" · {self.discarded} older entries discarded" if self.discarded else ""
        # Tools legitimately emit empty lines. Keep the full record while giving
        # the compact header a safe label instead of indexing an empty list.
        message_lines = self.records[-1][3].splitlines() if self.records else []
        activity = (
            message_lines[0] if message_lines else ("Blank log line" if self.records else "Ready")
        )
        self.header.setText(f"Logs · {len(self.records)} entries{suffix}   ·   {activity[:90]}")
        self._rendering = False

    @staticmethod
    def format_record(record: tuple[float, str, str, str]) -> str:
        """Show local time with explicit severity and originating component."""
        timestamp, severity, component, message = record
        return f"{datetime.fromtimestamp(timestamp):%H:%M:%S}  {severity:7}  {component}  {message}"

    def scrolled(self, value: int) -> None:
        """Pause automatic scrolling when the user moves above the last row."""
        if not self._rendering and value < self.text.verticalScrollBar().maximum():
            self.auto.setChecked(False)

    def copy_selected(self) -> None:
        """Copy only the user's highlighted log text."""
        QApplication.clipboard().setText(
            self.text.textCursor().selectedText().replace("\u2029", "\n")
        )

    def copy_all(self) -> None:
        """Copy all retained records, including levels hidden by the filter."""
        QApplication.clipboard().setText("\n".join(self.format_record(r) for r in self.records))

    def clear(self) -> None:
        """Clear the local display without altering reports or input sources."""
        self.records.clear()
        self.discarded = 0
        self.render()


class Toast(QFrame):
    """Dismissible local status overlay; persistent banners hold durable errors."""

    def __init__(self, parent: QWidget, theme: Theme) -> None:
        """Use a single bounded toast so bursts cannot obscure the workspace."""
        super().__init__(parent)
        self.theme = theme
        self.setProperty("card", True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 8, 12)
        self.message = label("")
        layout.addWidget(self.message, 1)
        self.dismiss = button("", self.hide, quiet=True)
        self.dismiss.setAccessibleName("Dismiss notification")
        theme.set_icon(self.dismiss, "x")
        layout.addWidget(self.dismiss)
        self.opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity)
        self.fade = QPropertyAnimation(self.opacity, b"opacity", self)
        self.fade.setDuration(MOTION)
        self.fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.hide()

    def show_message(self, text: str, tone: str = "success") -> None:
        """Auto-dismiss success; retain warnings/errors until explicitly dismissed."""
        self.timer.stop()
        self.message.setText(text)
        self.setAccessibleName(text)
        self.theme.bind(self, f"{tone}.bg", f"{tone}.fg")
        self.setFixedWidth(min(420, self.parentWidget().width() - 48))
        self.adjustSize()
        self.move(self.parentWidget().width() - self.width() - GAP, 80)
        self.fade.stop()
        self.fade.setStartValue(0.0)
        self.fade.setEndValue(1.0)
        self.show()
        self.raise_()
        if QApplication.isEffectEnabled(Qt.UIEffect.UI_FadeTooltip) and not getattr(
            self.theme, "reduced_motion", False
        ):
            self.fade.start()
        else:
            self.opacity.setOpacity(1.0)
        if tone == "success":
            self.timer.start(4000)
