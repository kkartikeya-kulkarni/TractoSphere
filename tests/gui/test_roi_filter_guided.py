"""Original-atlas filter selection uses actual label IDs and one core plan route."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QComboBox, QFileDialog, QListWidget, QScrollArea, QWidget

from dmri_studio.gui.roi_filter_dialog import RoiFilterDialog


def viewer_fixture(qtbot, tmp_path, *, edited=False):
    viewer = QWidget()
    qtbot.addWidget(viewer)
    viewer.reference = SimpleNamespace(path=tmp_path / "reference.nii.gz")
    viewer.roi_list = QListWidget()
    viewer.roi_filter_mode = QComboBox()
    viewer.roi_filter_mode.addItems(["All", "Any"])
    viewer.mrtrix_directory = "/usr/local/mrtrix3/bin"
    viewer.current_group = lambda: SimpleNamespace(path=tmp_path / "tracks.tck", total=4)
    viewer.rois = [
        dict(
            atlas_id="original-atlas",
            atlas_source=str(tmp_path / "atlas.nii.gz"),
            atlas_kind="labels",
            label_id=label_id,
            name=name,
            name_available=True,
            role="Display",
            color=color,
            edited=edited,
        )
        for label_id, name, color in (
            (3, "Internal Capsule", (0.9, 0.2, 0.1)),
            (20, "Ventricle", (0.2, 0.4, 0.8)),
        )
    ]
    for roi in viewer.rois:
        viewer.roi_list.addItem(roi["name"])
    viewer.roi_list.setCurrentRow(0)
    return viewer


def test_filter_dialog_selects_original_regions_without_preassigned_roles(qtbot, tmp_path):
    viewer = viewer_fixture(qtbot, tmp_path)
    dialog = RoiFilterDialog(viewer)
    qtbot.addWidget(dialog)
    assert dialog.region_list.count() == 2
    assert dialog.includes == [] and dialog.excludes == []
    assert dialog.mode.currentText() == "AND"
    dialog.region_search.setText("capsule")
    assert not dialog.region_list.item(0).isHidden()
    assert dialog.region_list.item(1).isHidden()
    dialog.region_list.setCurrentRow(0)
    dialog.assign_regions("include")
    assert dialog.includes == [3]
    dialog.region_search.clear()
    dialog.region_list.setCurrentRow(1)
    dialog.show_selected_region()
    assert viewer.roi_list.currentRow() == 1
    dialog.assign_regions("exclude")
    assert dialog.excludes == [20]
    assert "Internal Capsule" in dialog.summary.text()
    assert "Ventricle" in dialog.summary.text()
    assert dialog.plan is None and not dialog.run_button.isEnabled()
    dialog.mode.setCurrentText("OR")
    assert "Include ANY" in dialog.summary.text()
    dialog.ends.setChecked(True)
    assert "Endpoints only" in dialog.summary.text()


def test_filter_dialog_review_controls_reachable_at_compact_height(qtbot, tmp_path):
    dialog = RoiFilterDialog(viewer_fixture(qtbot, tmp_path))
    qtbot.addWidget(dialog)
    dialog.resize(850, 640)
    dialog.show()
    scroll = dialog.findChild(QScrollArea)
    assert scroll is not None
    scroll.ensureWidgetVisible(dialog.run_button)
    assert scroll.verticalScrollBar().maximum() > 0
    assert dialog.run_button.mapTo(dialog, dialog.run_button.rect().topLeft()).y() < 640


def test_filter_dialog_waypoint_order_and_role_reassignment(qtbot, tmp_path):
    viewer = viewer_fixture(qtbot, tmp_path)
    dialog = RoiFilterDialog(viewer)
    qtbot.addWidget(dialog)
    dialog.region_list.item(0).setSelected(True)
    dialog.region_list.item(1).setSelected(True)
    dialog.assign_regions("include")
    assert dialog.includes == [3, 20]
    dialog.mode.setCurrentText("ORDERED")
    dialog.included_list.setCurrentRow(1)
    dialog.move_include(-1)
    assert dialog.includes == [20, 3]
    assert "saved streamline vertex direction" in dialog.summary.text()
    dialog.region_list.clearSelection()
    dialog.region_list.setCurrentRow(0)
    dialog.assign_regions("exclude")
    assert dialog.includes == [20] and dialog.excludes == [3]
    dialog.excluded_list.setCurrentRow(0)
    dialog.remove_region("exclude")
    assert dialog.excludes == []


def test_filter_dialog_rejects_edited_atlas_masks(qtbot, tmp_path):
    viewer = viewer_fixture(qtbot, tmp_path, edited=True)
    with pytest.raises(ValueError, match="original discrete atlas labels"):
        RoiFilterDialog(viewer)


def test_filter_dialog_rejects_mixed_original_atlases(qtbot, tmp_path):
    viewer = viewer_fixture(qtbot, tmp_path)
    viewer.rois[0]["role"] = "Include"
    viewer.rois[1]["role"] = "Include"
    viewer.rois[1]["atlas_id"] = "different-atlas"
    with pytest.raises(ValueError, match="one original discrete atlas"):
        RoiFilterDialog(viewer)


def test_filter_rule_save_restore_keeps_order_but_not_approval(qtbot, tmp_path, monkeypatch):
    viewer = viewer_fixture(qtbot, tmp_path)
    dialog = RoiFilterDialog(viewer)
    qtbot.addWidget(dialog)
    dialog.includes = [20, 3]
    dialog.excludes = []
    dialog.mode.setCurrentText("ORDERED")
    dialog.subject.setText("synthetic-subject")
    dialog.space.setText("synthetic-space")
    dialog.evidence.setText("controlled identity fixture")
    dialog._refresh_selected_regions()
    configuration = tmp_path / "rules.json"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(configuration), "JSON")
    )
    dialog.save_configuration()
    assert configuration.is_file()
    restored = RoiFilterDialog(viewer)
    qtbot.addWidget(restored)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *a, **k: (str(configuration), "JSON")
    )
    restored.load_configuration()
    assert restored.includes == [20, 3]
    assert restored.mode.currentText() == "ORDERED"
    assert not restored.approve.isChecked() and restored.plan is None


def test_saved_filter_rules_reject_stale_atlas_labels(qtbot, tmp_path, monkeypatch):
    dialog = RoiFilterDialog(viewer_fixture(qtbot, tmp_path))
    qtbot.addWidget(dialog)
    path = tmp_path / "stale-rules.json"
    path.write_text(
        json.dumps(
            dict(
                schema="tractosphere.roi-rule/1",
                source=str(dialog.source.path),
                atlas=str(dialog.atlas),
                reference=str(dialog.reference),
                include=[999],
                exclude=[],
                mode="AND",
                ends_only=False,
                declarations={},
            )
        )
    )
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), "JSON"))
    dialog.load_configuration()
    assert "invalid for the current atlas" in dialog.status.text()
    assert dialog.includes == []
    assert dialog.plan is None and not dialog.run_button.isEnabled()


@pytest.mark.skipif(
    not Path("/usr/local/mrtrix3/bin/tckedit").is_file(), reason="Actual MRtrix required"
)
def test_guided_atlas_selection_prepares_exact_union_plan(qtbot, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "roi_fixture", Path(__file__).parents[1] / "core/test_roi_filtering.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    source, declarations = fixture.roi_case.__wrapped__(tmp_path)
    viewer = viewer_fixture(qtbot, tmp_path)
    viewer.reference.path = source / "reference.nii.gz"
    viewer.current_group = lambda: SimpleNamespace(path=source / "tracks.tck", total=4)
    for roi in viewer.rois:
        roi["atlas_source"] = str(source / "atlas.nii.gz")
    dialog = RoiFilterDialog(viewer)
    qtbot.addWidget(dialog)
    dialog.subject.setText(declarations["subject_id"])
    dialog.space.setText(declarations["space_id"])
    dialog.evidence.setText(declarations["alignment_evidence"])
    dialog.reviewed.setChecked(True)
    dialog.review_fields["registration_diagnostic"].setText(
        json.dumps(declarations["registration_diagnostic"])
    )
    dialog.parent_dir.setText(str(tmp_path))
    dialog.region_list.item(0).setSelected(True)
    dialog.region_list.item(1).setSelected(True)
    dialog.assign_regions("include")
    dialog.mode.setCurrentText("OR")
    dialog.preview_button.click()
    qtbot.waitUntil(lambda: dialog.thread is None, timeout=15000)
    assert dialog.plan is not None, dialog.status.text()
    commands = dialog.plan.to_dict()["commands"]
    tract_command = next(c for c in commands if c["kind"] == "tracts")
    assert tract_command["argv"].count("-include") == 1
    assert dialog.plan.to_dict()["bindings"][0]["constraints"]["include"][0]["verification"][
        "label_ids"
    ] in (None, [1])
    assert not dialog.run_button.isEnabled()
    dialog.approve.setChecked(True)
    assert dialog.run_button.isEnabled()
