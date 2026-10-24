"""Missing scientific inputs have an explicit route into the shared importer."""

import nibabel as nib
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QFileDialog, QPushButton

from dmri_studio.gui.scientific_planning import ImportDialog


def test_anatomy_role_import_routes_follow_the_selected_operation(
    window, qtbot, tmp_path, monkeypatch
):
    window.focus_models()
    page = window.scientific
    panel = page.anatomy
    page.tabs.setCurrentWidget(panel)
    roles = {"source": "anatomy", "reference": "anatomy", "mask": "mask"}

    def cancel(kind):
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ImportDialog)
        assert dialog.kind.currentData() == kind
        qtbot.mouseClick(
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel),
            Qt.MouseButton.LeftButton,
        )

    for key, kind in roles.items():
        combo = getattr(panel, key)
        assert "use Import beside this field" in combo.itemText(0)
        QTimer.singleShot(0, lambda selected=kind: cancel(selected))
        qtbot.mouseClick(panel.import_buttons[key], Qt.MouseButton.LeftButton)
    panel.operation.setFocus()
    qtbot.keyClick(panel.operation, Qt.Key.Key_Down)
    assert panel.operation.currentData() == "5ttcheck"
    assert "five-tissue image" in panel.source.itemText(0)
    assert not panel.form.isRowVisible(panel.input_rows["mask"])
    QTimer.singleShot(0, lambda: cancel("5tt"))
    qtbot.mouseClick(panel.import_buttons["source"], Qt.MouseButton.LeftButton)

    image = nib.Nifti1Image(np.ones((5, 6, 7), dtype="f4"), np.eye(4))
    image.header.set_xyzt_units("mm")
    anatomy = tmp_path / "T1.nii.gz"
    nib.save(image, anatomy)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda _parent, title, *_: (str(anatomy), "")
    )

    def complete_import():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ImportDialog)
        browse = next(
            action
            for action in dialog.findChildren(QPushButton)
            if action.accessibleName() == "Browse Input file"
        )
        qtbot.mouseClick(browse, Qt.MouseButton.LeftButton)
        qtbot.mouseClick(
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok),
            Qt.MouseButton.LeftButton,
        )

    panel.operation.setFocus()
    qtbot.keyClick(panel.operation, Qt.Key.Key_Home)
    assert panel.operation.currentData() == "5ttgen.fsl"
    QTimer.singleShot(0, complete_import)
    qtbot.mouseClick(panel.import_buttons["source"], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert panel.source.count() == 2, page.status.text()
    assert panel.source.itemText(1) == anatomy.name
    assert panel.plan is None and not panel.approval.isChecked()


def test_sift2_required_and_policy_specific_inputs_have_scoped_import(window, qtbot):
    window.focus_models()
    panel = window.scientific.sift2
    window.scientific.tabs.setCurrentWidget(panel)

    def cancel(kind):
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ImportDialog)
        assert dialog.kind.currentData() == kind
        qtbot.mouseClick(
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel),
            Qt.MouseButton.LeftButton,
        )

    for key, kind in (
        ("tracts", "tractogram"),
        ("fod", "fod"),
        ("reference", "anatomy"),
    ):
        assert "Import beside this field" in getattr(panel, key).itemText(0)
        QTimer.singleShot(0, lambda selected=kind: cancel(selected))
        qtbot.mouseClick(panel.import_buttons[key], Qt.MouseButton.LeftButton)
    panel.policy.setCurrentIndex(panel.policy.findData("act"))
    assert panel.form.isRowVisible(panel.input_rows["five_tt"])
    QTimer.singleShot(0, lambda: cancel("5tt"))
    qtbot.mouseClick(panel.import_buttons["five_tt"], Qt.MouseButton.LeftButton)
    panel.policy.setCurrentIndex(panel.policy.findData("mask"))
    assert panel.form.isRowVisible(panel.input_rows["proc_mask"])
    QTimer.singleShot(0, lambda: cancel("mask"))
    qtbot.mouseClick(panel.import_buttons["proc_mask"], Qt.MouseButton.LeftButton)
    assert panel.plan is None and not panel.approval.isChecked()
