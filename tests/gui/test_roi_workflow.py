"""Actual native ROI controls, source-file filtering and workspace restoration."""

import importlib.util
from pathlib import Path

import pytest

from dmri_studio.core.execution import hash_file


def fixture(window, qtbot, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "roi_fixture", Path(__file__).parents[1] / "core/test_roi_filtering.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    source, decl = m.roi_case.__wrapped__(tmp_path)
    window.focus_scientific_viewer()
    v = window.scientific_viewer
    v.set_workspace_mode("tractography")
    v.load_image(source / "reference.nii.gz")
    qtbot.waitUntil(lambda: not v.tasks, timeout=15000)
    v.load_atlas(source / "atlas.nii.gz", same_space=True)
    qtbot.waitUntil(lambda: not v.tasks, timeout=15000)
    v.load_tracts(source / "tracks.tck")
    qtbot.waitUntil(lambda: not v.tasks, timeout=15000)
    return v, source, decl


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_bulk_visibility_display_and_persistence(window, qtbot, tmp_path, theme):
    v, source, decl = fixture(window, qtbot, tmp_path)
    window.theme.apply(theme)
    assert len(v.rois) == 2 and not any(r["visible"] for r in v.rois)
    hashes = {p: hash_file(p) for p in source.iterdir()}
    v.bulk_selection("all")
    assert len(v.roi_list.selected_rows()) == 2
    assert not any(r["visible"] for r in v.rois) and all(r["role"] == "Display" for r in v.rois)
    v.bulk_selection("none")
    assert not v.roi_list.selected_rows()
    v.bulk_selection("invert")
    assert len(v.roi_list.selected_rows()) == 2
    v.roi_list.setCurrentRow(0)
    rows = v.selection_preview("20,3,20")
    v.select_roi_rows(rows)
    assert [v.rois[i]["label_id"] for i in v.roi_selection_order] == [20, 3]
    with pytest.raises(ValueError, match="unavailable"):
        v.selection_preview("frontal", True)
    v.show_regions(True)
    qtbot.waitUntil(
        lambda: not v.tasks and not v.roi_surface_queue and not v.roi_surface_busy, timeout=20000
    )
    assert len([k for k in v.scene.actors if k[0] == "roi"]) == 2
    v.roi_global_opacity.setValue(0.25)
    assert all(r["opacity"] == 0.25 for r in v.rois)
    v.roi_list.setCurrentRow(0)
    v.roi_opacity.setValue(0.65)
    assert v.rois[0]["opacity"] == 0.65 and v.rois[1]["opacity"] == 0.25
    v.roi_display_mode.setCurrentText("Boundary only")
    v.apply_roi_display()
    qtbot.waitUntil(
        lambda: not v.tasks and not v.roi_surface_queue and not v.roi_surface_busy, timeout=15000
    )
    v.roi_role.setCurrentText("Exclude")
    v.assign_roi_role()
    assert v.rois[0]["role"] == "Exclude" and v.rois[0]["visible"]
    path = tmp_path / "workspace.json"
    v.save_workspace(path)
    v.open_workspace(path)
    qtbot.waitUntil(lambda: not v.tasks, timeout=20000)
    assert v.rois[0]["surface_mode"] == "Boundary only" and v.rois[0]["role"] == "Exclude"
    assert v.rois[0]["opacity"] == 0.65
    assert all(hash_file(p) == h for p, h in hashes.items())


def test_native_dialog_filter_full_source_and_return(window, qtbot, tmp_path):
    v, source, decl = fixture(window, qtbot, tmp_path)
    v.bulk_selection("all")
    v.roi_role.setCurrentText("Include")
    v.assign_roi_role()
    v.open_native_roi_filter()
    d = v.native_roi_dialog
    d.subject.setText(decl["subject_id"])
    d.space.setText(decl["space_id"])
    d.evidence.setText(decl["alignment_evidence"])
    d.reviewed.setChecked(True)
    import json

    d.review_fields["registration_diagnostic"].setText(json.dumps(decl["registration_diagnostic"]))
    d.parent_dir.setText(str(tmp_path))
    d.mode.setCurrentText("OR")
    d.preview_button.click()
    qtbot.waitUntil(lambda: d.thread is None, timeout=20000)
    assert d.plan is not None, d.status.text()
    assert not d.run_button.isEnabled()
    d.approve.setChecked(True)
    d.run_button.click()
    qtbot.waitUntil(lambda: d.thread is None, timeout=25000)
    assert d.result_record and d.result_record["status"] == "validated_pending_qc", d.status.text()
    assert d.result_record["validation"]["measurements"][0]["streamlines"] == 3
    d.open_result()
    qtbot.waitUntil(lambda: not v.tasks, timeout=15000)
    assert len(v.groups) == 2 and v.current_group().total == 3
    d.return_full()
    assert v.current_group().total == 4 and v.current_group().visible
    v.load_image(source / "reference.nii.gz")
    qtbot.waitUntil(lambda: not v.tasks, timeout=15000)
    d.return_full()
    assert "workspace was closed" in d.status.text()


def test_named_selection_order_and_workspace(window, qtbot, tmp_path):
    v, source, decl = fixture(window, qtbot, tmp_path)
    # Clearly synthetic anatomical label table; real absent names remain absent.
    for roi, name in zip(v.rois, ["Synthetic Left Parcel", "Synthetic Right Parcel"], strict=True):
        roi.update(name=name, name_available=True)
    v.select_roi_rows(v.selection_preview("RIGHT, left", True))
    assert len(v.roi_selection_order) == 2
    v.select_roi_rows(v.selection_preview("20,3"))
    v.roi_role.setCurrentText("Include")
    v.assign_roi_role()
    assert [
        v.rois[i]["label_id"] for i in sorted(range(2), key=lambda i: v.rois[i]["role_rank"])
    ] == [20, 3]
    v.roi_global_opacity.setValue(0.35)
    path = tmp_path / "ordered-workspace.json"
    v.save_workspace(path)
    v.open_workspace(path)
    qtbot.waitUntil(lambda: not v.tasks, timeout=20000)
    assert [v.rois[i]["label_id"] for i in v.roi_selection_order] == [20, 3]
    assert v.roi_global_opacity.value() == 0.35
    assert not any(r["visible"] for r in v.rois)


def test_filter_dialog_cancels_actual_process_and_reports_failure(window, qtbot, tmp_path):
    import os
    import sys

    import psutil

    from dmri_studio.core.process import run_command

    v, source, decl = fixture(window, qtbot, tmp_path)
    v.bulk_selection("all")
    v.roi_role.setCurrentText("Include")
    v.assign_roi_role()
    v.open_native_roi_filter()
    d = v.native_roi_dialog
    pidfile = tmp_path / "child.pid"

    # Real cancellable process plumbing, not claimed as scientific execution.
    def operation():
        return run_command(
            [
                sys.executable,
                "-c",
                "import os,time,pathlib;pathlib.Path("
                + repr(str(pidfile))
                + ").write_text(str(os.getpid()));time.sleep(30)",
            ],
            cwd=tmp_path,
            log=tmp_path / "cancel.log",
            environment=os.environ,
            cancelled=d.cancel_event.is_set,
            progress=d.worker.progress.emit,
        )

    d.start("execute", operation)
    qtbot.waitUntil(pidfile.exists, timeout=15000)
    pid = int(pidfile.read_text())
    assert psutil.pid_exists(pid)
    d.cancel_button.click()
    qtbot.waitUntil(lambda: d.thread is None, timeout=15000)
    assert not psutil.pid_exists(pid) and d.result_record is None
    assert "cancelled" in d.status.text().lower()
    d.start("execute", lambda: (_ for _ in ()).throw(ValueError("Controlled failure evidence")))
    qtbot.waitUntil(lambda: d.thread is None, timeout=15000)
    assert "Controlled failure evidence" in d.status.text()
    assert not d.open_button.isEnabled()


def test_smoothing_and_opacity_do_not_change_native_filter(window, qtbot, tmp_path):
    import nibabel as nib
    import numpy as np

    from dmri_studio.core.execution import execute_plan
    from dmri_studio.core.roi_filtering import prepare_roi_filter

    v, source, decl = fixture(window, qtbot, tmp_path)
    v.bulk_selection("all")
    v.show_regions(True)
    qtbot.waitUntil(
        lambda: not v.tasks and not v.roi_surface_busy and not v.roi_surface_queue, timeout=20000
    )
    outputs = []
    masks = []
    for number, quality, opacity in [(1, "Voxel boundary", 1.0), (2, "Detailed", 0.25)]:
        v.roi_quality.setCurrentText(quality)
        v.roi_global_opacity.setValue(opacity)
        v.apply_roi_display()
        qtbot.waitUntil(
            lambda: not v.tasks and not v.roi_surface_busy and not v.roi_surface_queue,
            timeout=20000,
        )
        plan = prepare_roi_filter(
            source / "tracks.tck",
            source / "atlas.nii.gz",
            source / "reference.nii.gz",
            tmp_path / f"appearance-{number}",
            include=[3, 20],
            mode="AND",
            declarations=decl,
            tool_directory=Path("/usr/local/mrtrix3/bin"),
        )
        result = execute_plan(None, plan, approval=plan.digest)
        assert result["status"] == "validated_pending_qc"
        out = tmp_path / f"appearance-{number}/processing/roi-filter-filtered.tck"
        outputs.append(list(nib.streamlines.load(out).streamlines))
        masks.append(
            np.asanyarray(
                nib.load(tmp_path / f"appearance-{number}/inputs/include-1.nii.gz").dataobj
            )
        )
    np.testing.assert_array_equal(masks[0], masks[1])
    assert len(outputs[0]) == len(outputs[1]) == 1
    np.testing.assert_array_equal(outputs[0][0], outputs[1][0])


def test_reference_switch_retires_pending_roi_surfaces(window, qtbot, tmp_path):
    v, source, decl = fixture(window, qtbot, tmp_path)
    v.bulk_selection("all")
    v.show_regions(True)
    v.load_image(source / "reference.nii.gz")
    qtbot.waitUntil(
        lambda: not v.tasks and not v.roi_surface_busy and not v.roi_surface_queue, timeout=20000
    )
    assert not v.rois
    assert not [key for key in v.scene.actors if key[0] == "roi"]
