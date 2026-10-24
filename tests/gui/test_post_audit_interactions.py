"""User-action routes after the screenshot audit; picker responses are synthetic offscreen."""

import json

import nibabel as nib
import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QPushButton,
)

from dmri_studio.core.batch.discovery import discover_custom
from dmri_studio.gui.batch import BatchPage
from dmri_studio.gui.batch_mapping import CustomMappingDialog
from dmri_studio.gui.components import Disclosure
from dmri_studio.gui.scientific_planning import ImportDialog
from dmri_studio.gui.theme import Theme


def test_import_browse_buttons_update_only_the_requested_field(
    window, qtbot, tmp_path, monkeypatch
):
    files = {
        "Choose input file": tmp_path / "dwi.nii.gz",
        "Choose b-values": tmp_path / "reviewed.bval",
        "Choose b-vectors": tmp_path / "reviewed.bvec",
        "Choose acquisition or provenance JSON": tmp_path / "reviewed.json",
    }
    for file in files.values():
        file.write_text("synthetic picker return")
    companions = {
        "bval": tmp_path / "dwi.bval",
        "bvec": tmp_path / "dwi.bvec",
        "sidecar": tmp_path / "dwi.json",
    }
    for file in companions.values():
        file.write_text("synthetic companion")
    called = []

    def pick(_parent, title):
        called.append(title)
        return str(files[title]), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", pick)
    dialog = ImportDialog(window, initial_kind="dwi")
    qtbot.addWidget(dialog)
    dialog.show()
    expected_paths = {name: "" for name in dialog.paths}
    for name, title in (
        ("path", "Input file"),
        ("bval", "b-values"),
        ("bvec", "b-vectors"),
        ("sidecar", "Acquisition / provenance JSON"),
    ):
        browse = next(
            action
            for action in dialog.findChildren(QPushButton)
            if action.accessibleName() == f"Browse {title}"
        )
        qtbot.mouseClick(browse, Qt.MouseButton.LeftButton)
        expected_paths[name] = str(files[called[-1]])
        if name == "path":
            expected_paths.update({key: str(value) for key, value in companions.items()})
        assert {key: field.text() for key, field in dialog.paths.items()} == expected_paths
    assert called == list(files)


def test_mapping_editor_uses_explicit_role_files_and_preserves_ambiguity(
    qapp, qtbot, tmp_path, monkeypatch
):
    root = tmp_path / "non-bids"
    example = root / "person-01" / "scan-01"
    second = root / "person-02" / "scan-02"
    for folder in (example, second):
        folder.mkdir(parents=True)
        (folder / "dwi.nii.gz").write_bytes(b"discovery placeholder")
        (folder / "dwi.bval").write_text("0 1000")
        (folder / "dwi.bvec").write_text("0 1")
    (second / "dwi-copy.nii.gz").write_bytes(b"ambiguous discovery placeholder")
    page = BatchPage(Theme(qapp))
    qtbot.addWidget(page)
    page.show()
    guided = page.guided
    guided.source_kind.setFocus()
    qtbot.keyClick(guided.source_kind, Qt.Key.Key_Down)
    assert guided.source_kind.currentData() == "custom"
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda _parent, title, *_: str(root) if title == "Choose dataset folder" else str(example),
    )
    root_browse = next(
        action
        for action in guided.findChildren(QPushButton)
        if action.accessibleName() == "Browse Dataset folder"
    )
    qtbot.mouseClick(root_browse, Qt.MouseButton.LeftButton)
    assert guided.dataset_root.text() == str(root)

    dialog = CustomMappingDialog(root, guided)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.keyClicks(dialog.subject_pattern, "person-*")
    qtbot.keyClicks(dialog.acquisition_pattern, "scan-*")
    qtbot.mouseClick(dialog.example_browse, Qt.MouseButton.LeftButton)
    assert dialog.example_folder.text() == str(example)
    files = {
        role: example / name
        for role, name in (("dwi", "dwi.nii.gz"), ("bval", "dwi.bval"), ("bvec", "dwi.bvec"))
    }
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda _parent, title, *_: (str(files[title.split()[2]]), ""),
    )
    for role in files:
        qtbot.mouseClick(dialog.role_buttons[role], Qt.MouseButton.LeftButton)
        assert dialog.role_patterns[role].text() == files[role].name
    # The researcher explicitly broadens the DWI pattern; ambiguity is reported.
    dialog.role_patterns["dwi"].setFocus()
    qtbot.keyClick(dialog.role_patterns["dwi"], Qt.Key.Key_End)
    for _ in range(len(dialog.role_patterns["dwi"].text())):
        qtbot.keyClick(dialog.role_patterns["dwi"], Qt.Key.Key_Backspace)
    qtbot.keyClicks(dialog.role_patterns["dwi"], "dwi*.nii.gz")
    mapping_path = tmp_path / "mapping.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(mapping_path), ""))
    save = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save)
    qtbot.mouseClick(save, Qt.MouseButton.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Accepted
    mapping = json.loads(mapping_path.read_text())
    assert mapping["files"] == {"dwi": "dwi*.nii.gz", "bval": "dwi.bval", "bvec": "dwi.bvec"}
    study = discover_custom(root, "synthetic", mapping)
    assert len(study.acquisitions) == 2
    assert not study.acquisitions[0].findings
    assert "2 candidates" in study.acquisitions[1].findings[0]


def test_mapping_editor_does_not_infer_or_overwrite(qapp, qtbot, tmp_path, monkeypatch):
    root = tmp_path / "non-bids"
    root.mkdir()
    dialog = CustomMappingDialog(root)
    qtbot.addWidget(dialog)
    dialog.show()
    save = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save)
    qtbot.mouseClick(save, Qt.MouseButton.LeftButton)
    assert "subject folder pattern" in dialog.error.text()
    qtbot.keyClicks(dialog.subject_pattern, "person-*")
    qtbot.mouseClick(save, Qt.MouseButton.LeftButton)
    assert "at least one scientific file role" in dialog.error.text()
    qtbot.keyClicks(dialog.role_patterns["dwi"], "../outside.nii.gz")
    qtbot.mouseClick(save, Qt.MouseButton.LeftButton)
    assert "bounded, relative" in dialog.error.text()
    dialog.role_patterns["dwi"].selectAll()
    qtbot.keyClicks(dialog.role_patterns["dwi"], "dwi.nii.gz")
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text("existing")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(mapping_path), ""))
    qtbot.mouseClick(save, Qt.MouseButton.LeftButton)
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert mapping_path.read_text() == "existing"
    assert "no file was overwritten" in dialog.error.text()


def test_click_driven_scientific_imports_reach_registration_dti_and_csd(
    window, qtbot, tmp_path, monkeypatch
):
    """Use actual page actions and modal controls; only OS picker responses are replaced."""
    anatomy = tmp_path / "T1.nii.gz"
    image = nib.Nifti1Image(np.ones((5, 6, 7), dtype="f4"), np.eye(4))
    image.header.set_xyzt_units("mm")
    nib.save(image, anatomy)
    dwi = tmp_path / "dwi.nii.gz"
    diffusion = nib.Nifti1Image(np.ones((5, 6, 7, 4), dtype="f4"), np.eye(4))
    diffusion.header.set_xyzt_units("mm")
    nib.save(diffusion, dwi)
    bval = tmp_path / "dwi.bval"
    bvec = tmp_path / "dwi.bvec"
    bval.write_text("0 1000 1000 1000\n")
    bvec.write_text("0 1 0 0\n0 0 1 0\n0 0 0 1\n")
    response = tmp_path / "wm.txt"
    response.write_text("# Shells: 1000\n1.0 0.2\n")
    files = {
        "Choose input file": anatomy,
        "Choose b-values": bval,
        "Choose b-vectors": bvec,
    }
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda _parent, title, *_: (str(files[title]), "")
    )

    def import_by_click(action, *, kind, declarations=()):
        def drive_modal():
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, ImportDialog) and dialog.isVisible()
            assert dialog.kind.currentData() == kind
            browse = next(
                control
                for control in dialog.findChildren(QPushButton)
                if control.accessibleName() == "Browse Input file"
            )
            qtbot.mouseClick(browse, Qt.MouseButton.LeftButton)
            assert dialog.paths["path"].text() == str(files["Choose input file"])
            if kind == "dwi":
                for name, accessible in (("bval", "Browse b-values"), ("bvec", "Browse b-vectors")):
                    control = next(
                        button
                        for button in dialog.findChildren(QPushButton)
                        if button.accessibleName() == accessible
                    )
                    qtbot.mouseClick(control, Qt.MouseButton.LeftButton)
                    assert dialog.paths[name].text() == str(
                        files[f"Choose {'b-values' if name == 'bval' else 'b-vectors'}"]
                    )
            if declarations:
                disclosure = next(
                    item
                    for item in dialog.findChildren(Disclosure)
                    if item.title == "Scientific identity and format declarations"
                )
                qtbot.mouseClick(disclosure.toggle, Qt.MouseButton.LeftButton)
                for name, value in declarations:
                    control = {
                        "subject_id": dialog.subject,
                        "space_id": dialog.space,
                    }.get(name) or dialog.declarations[name]
                    control.setFocus()
                    if name in {"space_kind", "anatomical_modality", "tissue"}:
                        for _ in range(value):
                            qtbot.keyClick(control, Qt.Key.Key_Down)
                    else:
                        qtbot.keyClicks(control, value)
            qtbot.mouseClick(
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok),
                Qt.MouseButton.LeftButton,
            )

        QTimer.singleShot(0, drive_modal)
        qtbot.mouseClick(action, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: window.scientific.thread is None, timeout=10000)

    window.focus_registration()
    page = window.scientific
    guided = page.registration.guided
    assert guided.moving.count() == 1
    import_anatomy = next(
        action
        for action in guided.findChildren(QPushButton)
        if action.accessibleName() == "Import Moving image"
    )
    import_by_click(
        import_anatomy,
        kind="anatomy",
        declarations=(
            ("subject_id", "synthetic"),
            ("acquisition_id", "acq1"),
            ("space_id", "subject-t1"),
            ("space_kind", 2),
            ("anatomical_modality", 1),
        ),
    )
    assert len(page.artifacts) == 1, page.status.text()
    assert guided.moving.count() == 2
    assert guided.moving.itemText(1).startswith(anatomy.name + " · ")

    window.focus_models()
    qtbot.mouseClick(page.model_dti, Qt.MouseButton.LeftButton)
    files["Choose input file"] = dwi
    import_by_click(page.dti.import_dwi, kind="dwi")
    assert len(page.artifacts) == 2, page.status.text()
    assert page.dti.source.currentData() == page.artifacts[-1].id
    qtbot.mouseClick(page.model_csd, Qt.MouseButton.LeftButton)
    assert page.csd.source.count() == 2
    assert "Missing imported response functions" in page.csd.response_help.text()
    assert "Dhollander" in page.csd.response_help.text()

    files["Choose input file"] = response
    import_response = page.csd.import_buttons["WM response"]
    import_by_click(import_response, kind="response", declarations=(("tissue", 1),))
    assert len(page.artifacts) == 3, page.status.text()
    assert page.csd.responses["wm"].count() == 2
    assert page.csd.responses["gm"].count() == 1
    assert page.csd.responses["csf"].count() == 1
    assert page.plan is None and not page.tasks


def test_click_driven_custom_mapping_and_discovery(qapp, qtbot, tmp_path, monkeypatch):
    """Reach the mapping editor from Batch and review real discovery findings."""
    root = tmp_path / "non-bids"
    example = root / "person-01" / "scan-01"
    other = root / "person-02" / "scan-02"
    for folder in (example, other):
        folder.mkdir(parents=True)
        (folder / "dwi.nii.gz").write_bytes(b"placeholder discovery source")
        (folder / "dwi.bval").write_text("0 1000\n")
        (folder / "dwi.bvec").write_text("0 1\n")
    page = BatchPage(Theme(qapp))
    qtbot.addWidget(page)
    page.show()
    guided = page.guided
    guided.source_kind.setFocus()
    qtbot.keyClick(guided.source_kind, Qt.Key.Key_Down)
    assert guided.source_kind.currentData() == "custom"
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda _parent, title, *_: str(root) if title == "Choose dataset folder" else str(example),
    )
    root_browse = next(
        control
        for control in guided.findChildren(QPushButton)
        if control.accessibleName() == "Browse Dataset folder"
    )
    qtbot.mouseClick(root_browse, Qt.MouseButton.LeftButton)
    mapping_path = tmp_path / "custom-mapping.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(mapping_path), ""))
    files = {
        role: example / name
        for role, name in (("dwi", "dwi.nii.gz"), ("bval", "dwi.bval"), ("bvec", "dwi.bvec"))
    }
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda _parent, title, *_: (str(files[title.split()[2]]), ""),
    )

    def complete_mapping():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, CustomMappingDialog)
        qtbot.keyClicks(dialog.subject_pattern, "person-*")
        qtbot.keyClicks(dialog.acquisition_pattern, "scan-*")
        qtbot.mouseClick(dialog.example_browse, Qt.MouseButton.LeftButton)
        for role in files:
            qtbot.mouseClick(dialog.role_buttons[role], Qt.MouseButton.LeftButton)
        qtbot.mouseClick(
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save),
            Qt.MouseButton.LeftButton,
        )

    QTimer.singleShot(0, complete_mapping)
    qtbot.mouseClick(guided.create_mapping_button, Qt.MouseButton.LeftButton)
    assert guided.source_file.text() == str(mapping_path)
    assert json.loads(mapping_path.read_text())["files"] == {
        role: path.name for role, path in files.items()
    }
    qtbot.mouseClick(guided.discover_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert guided.pages.currentIndex() == 1, guided.notice.text()
    assert guided.subject_table.rowCount() == 2
    assert "2 of 2 selected" in guided.selected_count.text()
    assert page.plan is None and not page.approval.isChecked()
    # A researcher chooses a subset through the table, then reviews a real
    # backend proposal. Discovery alone never authorizes execution.
    first_cell = guided.subject_table.item(0, 0)
    first_key = first_cell.data(Qt.ItemDataRole.UserRole)
    qtbot.mouseClick(
        guided.subject_table.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(
            guided.subject_table.visualItemRect(first_cell).left() + 12,
            guided.subject_table.visualItemRect(first_cell).center().y(),
        ),
    )
    assert "1 of 2 selected" in guided.selected_count.text()
    qtbot.mouseClick(guided.next, Qt.MouseButton.LeftButton)
    assert guided.pages.currentIndex() == 2
    assert guided.workflow_choice.currentData() == "convert"
    qtbot.mouseClick(guided.history_confirm, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(guided.next, Qt.MouseButton.LeftButton)
    assert guided.pages.currentIndex() == 3
    qtbot.keyClicks(guided.output, str(tmp_path / "new-batch"))
    qtbot.mouseClick(guided.validate_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert page.plan is not None, page.status.text()
    selected_keys = page.plan.to_dict()["selected_acquisition_keys"]
    assert len(selected_keys) == 1 and first_key not in selected_keys
    assert guided.pages.currentIndex() == 4
    assert not guided.run.isEnabled()


@pytest.mark.parametrize("appearance", ["light", "dark"])
def test_mapping_dialog_keyboard_and_small_window_layout(qapp, qtbot, tmp_path, appearance):
    root = tmp_path / "non-bids"
    root.mkdir()
    Theme(qapp).apply(appearance)
    dialog = CustomMappingDialog(root)
    qtbot.addWidget(dialog)
    dialog.resize(620, 560)
    dialog.show()
    qapp.processEvents()
    save = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save)
    assert save.isVisible() and save.isEnabled()
    assert dialog.findChild(QDialogButtonBox).geometry().bottom() <= dialog.height()
    assert dialog.subject_pattern.isVisible()
    dialog.subject_pattern.setFocus()
    qtbot.keyClicks(dialog.subject_pattern, "person-*")
    qtbot.keyClick(dialog.subject_pattern, Qt.Key.Key_Tab)
    assert dialog.session_pattern.hasFocus()
    assert dialog.subject_pattern.text() == "person-*"


@pytest.mark.parametrize("appearance", ["light", "dark"])
def test_small_window_navigation_scroll_keeps_headings_and_selection_visible(
    window, qapp, appearance
):
    window.theme.apply(appearance)
    window.resize(960, 640)
    window.focus_registration()
    qapp.processEvents()
    assert window.navigation_scroll.verticalScrollBar().maximum() > 0
    assert all(heading.height() >= 25 for heading in window.nav_headers)
    window.focus_preferences()
    qapp.processEvents()
    settings = next(control for control, name in window.nav_buttons if name == "Settings")
    nav_view = window.navigation_scroll.viewport()
    top = settings.mapTo(nav_view, QPoint(0, 0)).y()
    assert 0 <= top and top + settings.height() <= nav_view.height()
