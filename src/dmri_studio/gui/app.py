"""Desktop entry point: ``dmri-studio gui`` or ``python -m dmri_studio.gui.app``.

Preferences are local QSettings; imaging inputs can live anywhere. Fonts/icons
are package assets, so starting the application never requires a network request.
"""

import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .settings import Settings
from .theme import ASSETS, Theme
from .welcome import WelcomeWindow


def main(scene=None) -> int:
    """Construct the optional Qt adapter only when explicitly launched."""
    app = QApplication(sys.argv[:1])
    app.setApplicationName("dMRI Studio")
    app.setOrganizationName("dMRI Studio")
    app.setApplicationDisplayName("TractoSphere")
    app.setWindowIcon(QIcon(str(ASSETS / "branding/tractosphere-app.svg")))
    settings = Settings()
    theme = Theme(app, str(settings.get("theme", "dark")))
    window = MainWindow(theme, settings)

    def enter_studio():
        """First run offers optional scientific setup after the welcome screen."""
        window.show()
        QTimer.singleShot(0, lambda: window.open_dependency_setup(first_run=True))

    if scene is not None:
        enter_studio()
        window.focus_scientific_viewer()
        viewer = window.scientific_viewer
        if viewer is None:
            return 2
        if scene.workspace:
            viewer.safe(lambda: viewer.open_workspace(scene.workspace))
        else:

            def attach():
                viewer.image_loaded.disconnect(attach)
                for path in scene.tract:
                    viewer.safe(lambda p=path: viewer.load_tracts(p))

            viewer.image_loaded.connect(attach)
            viewer.safe(lambda: viewer.load_image(scene.image))
    elif settings.get("show_welcome", True):
        welcome = WelcomeWindow(theme, settings)
        welcome.entered.connect(enter_studio)
        welcome.show()
    else:
        enter_studio()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
