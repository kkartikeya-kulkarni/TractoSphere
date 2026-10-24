"""Presentation preferences, separate from scientific executable configuration."""

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QCheckBox, QComboBox, QVBoxLayout, QWidget

from .components import button, label
from .dependency_setup import DependencyWidget
from .workstation import PageHeader, inspector_section


class PreferencesPage(QWidget):
    """Use system appearance by default; never change scientific parameters."""

    def __init__(self, window):
        super().__init__()
        self.theme = window.theme
        self.settings = window.settings
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)
        root.addWidget(PageHeader("Preferences", "Application appearance and accessibility."))
        self.scheme = QComboBox()
        self.scheme.addItem("Follow system appearance", "system")
        self.scheme.addItem("Light", "light")
        self.scheme.addItem("Dark", "dark")
        self.scheme.setCurrentIndex(max(0, self.scheme.findData(window.theme.preference)))
        self.scheme.setAccessibleName("Application appearance")

        def change(index):
            preference = self.scheme.itemData(index)
            window.theme.apply(preference)
            self.settings.set("theme", preference)
            window.update_theme_action()

        self.scheme.currentIndexChanged.connect(change)
        window.theme.changed.connect(self.refresh)
        root.addWidget(
            inspector_section(
                "Appearance",
                [("Color scheme", self.scheme)],
                [
                    label(
                        "Interface colors follow your system accent where Qt exposes it. Scientific image and tract colors stay independent.",
                        "caption",
                    )
                ],
            )
        )
        self.reduce = QCheckBox("Suppress nonessential interface motion")
        self.reduce.setChecked(True)
        self.reduce.setEnabled(False)
        self.reduce.setToolTip("The redesigned interface contains no decorative animation.")
        root.addWidget(
            inspector_section(
                "Accessibility",
                widgets=[
                    self.reduce,
                    label(
                        "Native keyboard controls, visible focus and system fonts are used throughout. Image navigation retains its scientific keyboard shortcuts.",
                        "caption",
                    ),
                ],
                advanced=True,
            )
        )
        root.addWidget(
            inspector_section(
                "Scientific software",
                widgets=[
                    label(
                        "Viewing, atlas and ROI tools do not require external processing software."
                    ),
                    button("Open Software Dependencies…", window.focus_dependencies),
                ],
            )
        )
        root.addWidget(
            inspector_section(
                "About",
                widgets=[
                    label("TractoSphere · Diffusion MRI Research Studio"),
                    label(
                        "Public alpha · Research use only · Not for clinical use. Scientific validation ongoing. Diffusion modelling and connectivity execution are not implemented; imported tractography can be inspected and edited.",
                        "caption",
                    ),
                ],
                advanced=True,
            )
        )
        # One existing controller and widget, hosted by the separate setup route.
        self.dependencies = DependencyWidget(
            window.dependencies,
            lambda: (
                window.workflow.execution.thread is None
                and window.scientific.thread is None
                and window.batch.thread is None
            ),
        )
        root.addStretch()

    def refresh(self):
        """No account services, scientific defaults, or image files are touched."""
        with QSignalBlocker(self.scheme):
            self.scheme.setCurrentIndex(max(0, self.scheme.findData(self.theme.preference)))
