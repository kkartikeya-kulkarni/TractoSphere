"""Connectivity input recovery uses the shared, scientifically checked importer."""

import nibabel as nib
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QFileDialog, QPushButton

from dmri_studio.gui.components import Disclosure
from dmri_studio.gui.scientific_planning import ImportDialog


def test_empty_connectivity_inputs_have_adjacent_scoped_recovery(window, qtbot, monkeypatch):
    window.focus_connectivity()
    page = window.scientific
    panel = page.connectome
    routes = {
        "tracts": "tractogram",
        "atlas": "atlas_labels",
        "reference": "anatomy",
        "fa": "anatomy",
        "weights": "streamline_weights",
    }
    for key, kind in routes.items():
        combo = getattr(panel, key)
        assert combo.currentData() is None
        assert "use Import beside this field" in combo.itemText(0)
        assert panel.import_buttons[key].parentWidget() is panel.input_rows[key]

        def cancel_dialog(expected_kind=kind):
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, ImportDialog)
            assert dialog.kind.currentData() == expected_kind
            qtbot.mouseClick(
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel),
                Qt.MouseButton.LeftButton,
            )

        if key == "weights":
            panel.metric.setFocus()
            qtbot.keyClick(panel.metric, Qt.Key.Key_Down)
            assert panel.metric.currentData() == "all_sift2"
        QTimer.singleShot(0, cancel_dialog)
        qtbot.mouseClick(panel.import_buttons[key], Qt.MouseButton.LeftButton)
    assert "identified FA sampling image" in panel.input_help.text()
    assert "exact-source SIFT2 weight factors" in panel.input_help.text()
    assert not page.artifacts and panel.plan is None


def test_imported_explicit_fa_reaches_only_eligible_selector(window, qtbot, tmp_path, monkeypatch):
    image = nib.Nifti1Image(np.full((5, 6, 7), 0.5, dtype="f4"), np.eye(4))
    image.header.set_xyzt_units("mm")
    fa = tmp_path / "FA.nii.gz"
    other = tmp_path / "structural.nii.gz"
    nib.save(image, fa)
    nib.save(image, other)
    window.focus_connectivity()
    page = window.scientific
    panel = page.connectome

    def click_import(path, *, declare_fa):
        monkeypatch.setattr(
            QFileDialog, "getOpenFileName", lambda _parent, title, *_: (str(path), "")
        )

        def complete_dialog():
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, ImportDialog)
            assert dialog.kind.currentData() == "anatomy"
            browse = next(
                control
                for control in dialog.findChildren(QPushButton)
                if control.accessibleName() == "Browse Input file"
            )
            qtbot.mouseClick(browse, Qt.MouseButton.LeftButton)
            assert dialog.paths["path"].text() == str(path)
            if declare_fa:
                disclosure = next(
                    item
                    for item in dialog.findChildren(Disclosure)
                    if item.title == "Scientific identity and format declarations"
                )
                qtbot.mouseClick(disclosure.toggle, Qt.MouseButton.LeftButton)
                dialog.declarations["metric"].setFocus()
                qtbot.keyClick(dialog.declarations["metric"], Qt.Key.Key_Down)
                assert dialog.declarations["metric"].currentData() == "FA"
            qtbot.mouseClick(
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok),
                Qt.MouseButton.LeftButton,
            )

        QTimer.singleShot(0, complete_dialog)
        qtbot.mouseClick(
            panel.import_buttons["fa" if declare_fa else "reference"],
            Qt.MouseButton.LeftButton,
        )
        qtbot.waitUntil(lambda: page.thread is None, timeout=10000)

    click_import(other, declare_fa=False)
    assert panel.reference.count() == 2
    assert panel.fa.count() == 1
    click_import(fa, declare_fa=True)
    assert panel.reference.count() == 3
    assert panel.fa.count() == 2
    assert panel.fa.itemText(1).startswith("FA.nii.gz")
    assert panel.plan is None and not panel.approve.isChecked()

    panel.metric.setFocus()
    for _ in range(panel.metric.count() - 1):
        qtbot.keyClick(panel.metric, Qt.Key.Key_Down)
    assert panel.metric.currentData() == "mean_fa"
    assert panel.form.isRowVisible(panel.input_rows["fa"])
    assert not panel.form.isRowVisible(panel.input_rows["weights"])
    panel.metric.setFocus()
    qtbot.keyClick(panel.metric, Qt.Key.Key_Up)
    assert panel.metric.currentData() == "mean_length"
    assert not panel.form.isRowVisible(panel.input_rows["fa"])
    assert not panel.form.isRowVisible(panel.input_rows["weights"])
    assert panel.plan is None
