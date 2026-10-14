"""Display-only preferences; never edit processing or authentication settings."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QComboBox, QVBoxLayout, QWidget

from .components import Card, label
from .dependency_setup import DependencyWidget
from .theme import PAGE_INSET

if TYPE_CHECKING:
    from .main_window import MainWindow


class PreferencesPage(QWidget):
    """Small, functional settings workspace with explicit presentation boundaries."""

    def __init__(self, window: "MainWindow") -> None:
        """Expose existing theme and privacy controls without duplicating services."""
        super().__init__()
        theme, settings = window.theme, window.settings
        self.settings = settings
        root = QVBoxLayout(self)
        root.setContentsMargins(*([PAGE_INSET] * 4))
        root.setSpacing(16)
        root.addWidget(label("Preferences", "title"))
        root.addWidget(
            label(
                "Personalize the workstation. Scientific parameters stay with each processing plan.",
                "caption",
            )
        )
        display = Card(theme, "Appearance & accessibility")
        self.scheme = QComboBox()
        self.scheme.addItems(["Dark workstation", "Light workstation"])
        self.scheme.setCurrentIndex(theme.name == "light")
        self.scheme.setAccessibleName("Application color theme")

        def change(index: int) -> None:
            """Synchronize the central palette and persistent display preference."""
            theme.apply("light" if index else "dark")
            settings.set("theme", theme.name)
            window.update_theme_action()

        self.scheme.currentIndexChanged.connect(change)
        theme.changed.connect(
            lambda: (
                self.scheme.setCurrentIndex(theme.name == "light")
                if self.scheme.currentIndex() != (theme.name == "light")
                else None
            )
        )
        display.body.addWidget(self.scheme)
        self.reduce = QCheckBox("Reduce motion and disable welcome animation")
        self.reduce.setChecked(settings.get("reduced_motion", False))

        def change_motion(value: bool) -> None:
            """Apply presentation-only motion preference to future UI animations."""
            settings.set("reduced_motion", value)
            theme.reduced_motion = value

        self.reduce.toggled.connect(change_motion)
        display.body.addWidget(self.reduce)
        self.welcome = QCheckBox("Show the welcome screen at startup")
        self.welcome.setChecked(settings.get("show_welcome", True))
        self.welcome.toggled.connect(lambda value: settings.set("show_welcome", value))
        display.body.addWidget(self.welcome)
        root.addWidget(display)
        about = Card(theme, "TractoSphere")
        about.body.addWidget(label("Diffusion MRI Research Studio\nFrom Diffusion to Discovery"))
        about.body.addWidget(
            label(
                "Internal project: dMRI Studio 2.0\nModeling, tractography generation and connectivity are not yet implemented. The 3D viewer supports imported tractography. Scientific QC requires human review.",
                "caption",
            )
        )
        root.addWidget(about)
        self.dependencies = DependencyWidget(
            window.dependencies, lambda: window.workflow.execution.thread is None
        )
        root.addWidget(self.dependencies)
        root.addStretch()

    def refresh(self) -> None:
        """Reflect choices made in the welcome screen without emitting edits."""
        for control, key, default in (
            (self.reduce, "reduced_motion", False),
            (self.welcome, "show_welcome", True),
        ):
            control.blockSignals(True)
            control.setChecked(self.settings.get(key, default))
            control.blockSignals(False)
