"""Synthetic desktop fixtures; GUI tests never depend on a real HCP subject.

Use the native Cocoa plugin on macOS for VTK tests. Linux CI uses Xvfb;
offscreen Qt is suitable only for non-VTK checks on macOS. Native screenshots
remain a separate visual acceptance exercise.
"""

from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
from PySide6.QtCore import QSettings

from dmri_studio.gui.main_window import MainWindow
from dmri_studio.gui.settings import Settings
from dmri_studio.gui.theme import Theme


@pytest.fixture
def dataset_folder(tmp_path: Path) -> Path:
    """Generate four ordered volumes: first b0 and three weighted samples."""
    folder = tmp_path / "subject"
    folder.mkdir()
    data = np.arange(8 * 10 * 12 * 4, dtype=np.float32).reshape(8, 10, 12, 4) + 1
    image = nib.Nifti1Image(data, np.diag([1.2, 1.5, 2.0, 1]))
    image.header.set_xyzt_units("mm")
    nib.save(image, folder / "data.nii")
    (folder / "bvals").write_text("0 1000 1000 2000")
    (folder / "bvecs").write_text("0 1 0 0\n0 0 1 0\n0 0 0 1")
    return folder


@pytest.fixture
def window(qapp: object, qtbot: object, tmp_path: Path) -> MainWindow:
    """Give each test isolated preferences and deterministic thread cleanup."""
    theme = Theme(qapp)
    settings = Settings(QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat))
    instance = MainWindow(theme, settings)
    # Scientific workflow tests begin in their explicitly selected import route.
    # The redesigned production startup is tested separately as the Start page.
    instance.focus_load()
    # Own this window's teardown explicitly. pytest-qt's automatic widget
    # deletion does not wait when closeEvent defers closure for an active read.
    # Three serialized planes make that race much easier to encounter natively.
    instance.show()
    # Qt shortcuts use active-window focus, unlike direct keyPressEvent tests.
    # Closing a previous processing window can leave native focus elsewhere;
    # establish the real keyboard context instead of relying on show timing.
    instance.raise_()
    instance.activateWindow()
    qtbot.waitExposed(instance, timeout=10000)
    qtbot.wait(30)

    def activate_native_window():
        # Cocoa may deliver the prior welcome/tool window's deactivation after
        # show(). Request activation only after exposure, and retry the actual
        # native request while retaining the real active-window assertion.
        if not instance.isActiveWindow():
            instance.raise_()
            instance.activateWindow()
            instance.windowHandle().requestActivate()
        return instance.isActiveWindow()

    qtbot.waitUntil(activate_native_window, timeout=10000)
    yield instance
    instance.close()
    # closeEvent refuses to destroy the window while *any* GUI worker is active,
    # including scientific, batch, QC and viewer reads. Wait for that accepted
    # close instead of checking only the loader and preview threads: otherwise
    # deleteLater can retire a previous test's QObjects during the next test's
    # background slice read.
    qtbot.waitUntil(lambda: not instance.isVisible(), timeout=30000)
    instance.deleteLater()
    qtbot.wait(10)
