"""Shared desktop presentation components; no scientific state or calculations."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from .components import Disclosure, button, label


class PageHeader(QWidget):
    """A readable heading and optional concise description, without a card frame."""

    def __init__(self, title, description="", actions=()):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(label(title, "title"))
        if description:
            text.addWidget(label(description, "caption"))
        row.addLayout(text, 1)
        for action in actions:
            row.addWidget(action)


class EmptyState(QWidget):
    """Useful, keyboard-accessible empty state instead of idle scientific controls."""

    def __init__(self, title, description, actions=()):
        super().__init__()
        self.setAccessibleName(title)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(2)
        content = QWidget()
        content.setMinimumWidth(360)
        content.setMaximumWidth(560)
        layout = QVBoxLayout(content)
        layout.setSpacing(12)
        self.title = label(title, "title")
        self.description = label(description)
        layout.addWidget(self.title)
        layout.addWidget(self.description)
        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 0)
        self.actions = []
        for index, (text, callback) in enumerate(actions):
            control = button(text, callback, primary=index == 0)
            row.addWidget(control)
            self.actions.append(control)
        row.addStretch()
        layout.addLayout(row)
        outer.addWidget(content, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(3)


def inspector_section(title, rows=(), widgets=(), *, advanced=False):
    """Group existing controls without replacing their callbacks or field values."""
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 4, 0, 12)
    layout.setSpacing(8)
    if rows:
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        for name, control in rows:
            field_label = label(name, "control")
            field_label.setBuddy(control)
            control.setAccessibleName(control.accessibleName() or name)
            form.addRow(field_label, control)
        layout.addLayout(form)
    for widget in widgets:
        layout.addWidget(widget)
    section = Disclosure(title, content, expanded=not advanced)
    section.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    return section


class StartPage(QWidget):
    """Direct-start task launcher; no animation, implicit loading or setup modal."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 32)
        root.addWidget(PageHeader("TractoSphere", "Diffusion MRI Research Studio"))
        root.addSpacing(28)
        content = QWidget()
        content.setMaximumWidth(700)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(label("Start with your research data", "section"))
        for title, detail, callback in [
            (
                "Open Project…",
                "Resume an acquisition project and its recorded attempts.",
                window.projects.browse,
            ),
            (
                "Open Image…",
                "Inspect a 3D or 4D NIfTI without preparing a processing run.",
                window.open_image,
            ),
            (
                "Import Diffusion MRI…",
                "Review raw or externally preprocessed diffusion inputs.",
                window.focus_load,
            ),
            (
                "Open Tractography…",
                "Open a matching MRI and inspect existing streamlines.",
                window.open_tractography,
            ),
        ]:
            row = QHBoxLayout()
            action = button(title, callback, quiet=True)
            action.setMinimumWidth(205)
            action.setProperty("leftAligned", True)
            row.addWidget(action)
            row.addWidget(label(detail, "caption"), 1)
            layout.addLayout(row)
        layout.addSpacing(24)
        layout.addWidget(label("Recent projects", "section"))
        self.recent = QVBoxLayout()
        layout.addLayout(self.recent)
        root.addWidget(content)
        root.addStretch(1)
        root.addWidget(
            label(
                "Public alpha · Research use only · Scientific review remains essential.", "caption"
            )
        )
        self.refresh()

    def refresh(self):
        """Show recorded project paths; missing files require explicit relocation."""
        import json
        from pathlib import Path

        while self.recent.count():
            item = self.recent.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        try:
            records = json.loads(self.window.settings.get("recent_projects_json", "[]"))
        except (ValueError, TypeError):
            records = []
        if not records:
            self.recent.addWidget(label("Projects you open will appear here.", "caption"))
        for record in records[:6]:
            path = Path(record["path"])
            control = button(
                record.get("name", path.stem),
                lambda _=False, p=path: self.open_recent(p),
                quiet=True,
            )
            control.setToolTip(str(path))
            control.setProperty("leftAligned", True)
            self.recent.addWidget(control)

    def open_recent(self, path):
        """Never substitute another dataset when a remembered path is missing."""
        if not path.is_file():
            self.window.toast.show_message(
                "Project file is missing. Use Open Project to locate its saved snapshot.", "warning"
            )
            return
        self.window.focus_projects()
        self.window.projects.open_path(path)
