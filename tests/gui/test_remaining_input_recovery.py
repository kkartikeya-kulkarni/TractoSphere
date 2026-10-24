"""Missing scientific inputs have an explicit route into the shared importer."""

import nibabel as nib
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QFileDialog, QPushButton

from dmri_studio.core.scientific import ImportRequest
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


def test_dti_optional_mask_and_csd_input_rows_have_scoped_import(window, qtbot):
    window.focus_models()
    page = window.scientific

    def cancel(kind):
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ImportDialog)
        assert dialog.kind.currentData() == kind
        qtbot.mouseClick(
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel),
            Qt.MouseButton.LeftButton,
        )

    dti = page.dti
    page.tabs.setCurrentWidget(dti)
    QTimer.singleShot(0, lambda: cancel("mask"))
    qtbot.mouseClick(dti.import_mask, Qt.MouseButton.LeftButton)
    assert dti.mask.itemData(0) is None

    csd = page.csd
    page.tabs.setCurrentWidget(csd)
    for title, kind in (
        ("DWI", "dwi"),
        ("Binary mask", "mask"),
        ("WM response", "response"),
        ("GM response", "response"),
        ("CSF response", "response"),
    ):
        QTimer.singleShot(0, lambda selected=kind: cancel(selected))
        qtbot.mouseClick(csd.import_buttons[title], Qt.MouseButton.LeftButton)
    csd.response_mode.setCurrentIndex(csd.response_mode.findData("dhollander"))
    assert not csd.form.isRowVisible(csd.input_rows["WM response"])
    assert not csd.form.isRowVisible(csd.input_rows["GM response"])
    csd.response_mode.setCurrentIndex(csd.response_mode.findData("import"))
    csd.method.setCurrentIndex(csd.method.findData("dwi2fod.csd"))
    assert csd.form.isRowVisible(csd.input_rows["WM response"])
    assert not csd.form.isRowVisible(csd.input_rows["GM response"])
    assert "Import beside" in csd.source.itemText(0)
    assert csd.plan is None and not csd.approval.isChecked()


def test_guided_tracking_imports_follow_task_and_algorithm(window, qtbot):
    window.focus_tracking_plan()
    panel = window.scientific.tracking
    guided = panel.guided

    def cancel(kind):
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ImportDialog)
        assert dialog.kind.currentData() == kind
        qtbot.mouseClick(
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel),
            Qt.MouseButton.LeftButton,
        )

    def import_cancel(title, kind):
        QTimer.singleShot(0, lambda: cancel(kind))
        qtbot.mouseClick(guided.import_buttons[title], Qt.MouseButton.LeftButton)

    import_cancel("Tracking input", "fod")
    panel.method.setCurrentIndex(panel.method.findData("tckgen.tensor_det"))
    import_cancel("Tracking input", "dwi")
    import_cancel("Where may tracks travel? Tracking mask", "mask")
    guided.seed_method.setCurrentIndex(guided.seed_method.findData("roi"))
    import_cancel("Seed region or mask", "roi")
    guided.mode.setCurrentIndex(guided.mode.findData("act"))
    assert guided.generate_form.isRowVisible(guided.generate_input_rows["Five-tissue anatomy"])
    import_cancel("Five-tissue anatomy", "5tt")
    import_cancel("Reference image", "anatomy")
    guided.select_task("filter")
    import_cancel("Filter existing tractogram", "tractogram")
    import_cancel("Filter reference image", "anatomy")
    guided.select_task("density")
    import_cancel("Density existing tractogram", "tractogram")
    import_cancel("Density reference image / grid", "anatomy")
    assert panel.plan is None and not panel.approval.isChecked()


def test_preprocessing_can_open_input_import_without_replacing_existing_state(window, qtbot):
    window.focus_workflow()
    workflow = window.workflow
    assert workflow.empty.isVisibleTo(workflow)
    qtbot.mouseClick(workflow.change_input, Qt.MouseButton.LeftButton)
    assert window._selected_navigation == "Project"
    assert workflow.dataset is None


def test_inspected_tractogram_reaches_tracking_sift2_and_connectivity(window, qtbot, tmp_path):
    source = tmp_path / "synthetic.tck"
    tracks = nib.streamlines.Tractogram(
        [np.array([[1, 1, 1], [2, 2, 2]], dtype="f4")],
        affine_to_rasmm=np.eye(4),
    )
    nib.streamlines.save(tracks, source)
    window.focus_tracking_plan()
    page = window.scientific
    page.import_request(ImportRequest("tractogram", source, declarations={"space_id": "synthetic"}))
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert len(page.artifacts) == 1, page.status.text()
    imported = page.artifacts[0]
    assert imported.kind == "tractogram"
    guided = page.tracking.guided
    assert guided.filter_tracts.findData(imported.id) > 0
    assert guided.density_tracts.findData(imported.id) > 0
    assert page.sift2.tracts.findData(imported.id) > 0
    assert page.connectome.tracts.findData(imported.id) > 0
    assert page.sift2.plan is None and page.connectome.plan is None
