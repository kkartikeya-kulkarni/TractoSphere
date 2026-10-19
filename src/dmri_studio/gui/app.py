"""Desktop entry point: ``dmri-studio gui`` or ``python -m dmri_studio.gui.app``.

Preferences are local QSettings; imaging inputs can live anywhere. Fonts/icons
are package assets, so starting the application never requires a network request.
"""

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .settings import Settings
from .theme import ASSETS, Theme


def main(scene=None) -> int:
    """Construct the optional Qt adapter only when explicitly launched."""
    app = QApplication(sys.argv[:1])
    app.setApplicationName("dMRI Studio")
    app.setOrganizationName("dMRI Studio")
    app.setApplicationDisplayName("TractoSphere")
    app.setWindowIcon(QIcon(str(ASSETS / "branding/tractosphere-app.svg")))
    settings = Settings()
    theme = Theme(app, str(settings.get("theme", "system")))
    window = MainWindow(theme, settings)

    def enter_studio():
        """Open directly; software setup is available without a modal interruption."""
        window.show()

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
    else:
        window.focus_start()
        enter_studio()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
