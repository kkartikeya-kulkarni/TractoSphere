"""Native Cocoa/VTK walkthrough of the guided scientific result routes."""

import importlib.util
import json
import os
from pathlib import Path

import pytest


@pytest.mark.skipif(
    os.environ.get("QT_QPA_PLATFORM") != "cocoa"
    or not Path("/usr/local/mrtrix3/bin/tckgen").is_file(),
    reason="Native Cocoa/VTK and an actual MRtrix installation are required",
)
def test_native_guided_generate_and_density_open_verified_results(window, qtbot, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "tracking_fixture", Path(__file__).parents[1] / "core/test_tracking.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    fod, mask, reference = fixture.source.__wrapped__(tmp_path)
    window.focus_tracking_plan()
    page = window.scientific
    page.artifacts = [fod, mask, reference]
    page.tool_directory = Path("/usr/local/mrtrix3/bin")
    page.refresh()
    panel = page.tracking
    guided = panel.guided
    guided.method.setCurrentIndex(guided.method.findData("tckgen.sdstream"))
    guided.source.setCurrentIndex(guided.source.findData(fod.id))
    guided.mask.setCurrentIndex(guided.mask.findData(mask.id))
    guided.seed_method.setCurrentIndex(guided.seed_method.findData("image"))
    guided.seed.setCurrentIndex(guided.seed.findData(mask.id))
    guided.count.setValue(10)
    panel.minimum.setValue(3)
    panel.maximum.setValue(20)
    panel.cutoff.setValue(0.01)
    panel.seed_cutoff.setValue(0.02)
    panel.attempts.setValue(10)
    guided.output.setText(str(tmp_path / "generate"))
    guided.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert panel.plan is not None, page.status.text()
    guided.approval.setChecked(True)
    guided.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert panel.manifest["status"] == "validated_pending_qc", page.status.text()
    guided.open_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    qtbot.waitUntil(
        lambda: (
            window.scientific_viewer is not None
            and len(window.scientific_viewer.groups) == 1
            and not window.scientific_viewer.tasks
        ),
        timeout=20000,
    )
    assert window.scientific_viewer.groups[0].total == 10

    generated = next(a for a in page.artifacts if a.kind == "tractogram")
    window.focus_tracking_plan()
    guided.select_task("density")
    guided.density_tracts.setCurrentIndex(guided.density_tracts.findData(generated.id))
    guided.density_reference.setCurrentIndex(guided.density_reference.findData(reference.id))
    guided.output.setText(str(tmp_path / "density"))
    guided.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert panel.plan is not None, page.status.text()
    guided.approval.setChecked(True)
    guided.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert panel.manifest["status"] == "validated_pending_qc", page.status.text()
    product = panel.maps.currentData()
    assert product["kind"] == "scalar" and guided.open_button.isEnabled()
    guided.open_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    qtbot.waitUntil(
        lambda: (
            window.scientific_viewer.reference is not None
            and window.scientific_viewer.reference.path == Path(product["path"])
            and not window.scientific_viewer.tasks
        ),
        timeout=20000,
    )


@pytest.mark.skipif(
    os.environ.get("QT_QPA_PLATFORM") != "cocoa"
    or not Path("/usr/local/mrtrix3/bin/tckedit").is_file(),
    reason="Native Cocoa/VTK and an actual MRtrix installation are required",
)
def test_native_guided_original_atlas_filter_without_preassigned_roles(window, qtbot, tmp_path):
    from dmri_studio.core.execution import hash_file

    spec = importlib.util.spec_from_file_location(
        "roi_fixture", Path(__file__).parents[1] / "core/test_roi_filtering.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    source, declarations = fixture.roi_case.__wrapped__(tmp_path)
    original_hash = hash_file(source / "tracks.tck")
    window.focus_scientific_viewer()
    viewer = window.scientific_viewer
    viewer.set_workspace_mode("tractography")
    viewer.load_image(source / "reference.nii.gz")
    qtbot.waitUntil(lambda: not viewer.tasks, timeout=15000)
    viewer.load_atlas(source / "atlas.nii.gz", same_space=True)
    qtbot.waitUntil(lambda: not viewer.tasks, timeout=15000)
    viewer.load_tracts(source / "tracks.tck")
    qtbot.waitUntil(lambda: not viewer.tasks, timeout=15000)
    assert all(roi["role"] == "Display" for roi in viewer.rois)

    window.focus_tracking_plan()
    guided = window.scientific.tracking.guided
    guided.select_task("filter")
    guided.atlas_filter_button.click()
    dialog = viewer.native_roi_dialog
    assert dialog.includes == []
    dialog.region_list.item(0).setSelected(True)
    dialog.region_list.item(1).setSelected(True)
    dialog.assign_regions("include")
    dialog.mode.setCurrentText("OR")
    dialog.subject.setText(declarations["subject_id"])
    dialog.space.setText(declarations["space_id"])
    dialog.evidence.setText(declarations["alignment_evidence"])
    dialog.reviewed.setChecked(True)
    dialog.review_fields["registration_diagnostic"].setText(
        json.dumps(declarations["registration_diagnostic"])
    )
    dialog.parent_dir.setText(str(tmp_path))
    dialog.preview_button.click()
    qtbot.waitUntil(lambda: dialog.thread is None, timeout=20000)
    assert dialog.plan is not None, dialog.status.text()
    assert not dialog.run_button.isEnabled()
    dialog.approve.setChecked(True)
    dialog.run_button.click()
    qtbot.waitUntil(lambda: dialog.thread is None, timeout=30000)
    assert dialog.result_record["status"] == "validated_pending_qc", dialog.status.text()
    assert dialog.result_record["validation"]["measurements"][0]["streamlines"] == 3
    assert hash_file(source / "tracks.tck") == original_hash
    dialog.open_result()
    assert dialog.source.visible  # The asynchronous output hash check has not hidden it.
    qtbot.waitUntil(lambda: len(viewer.groups) == 2 and not viewer.tasks, timeout=20000)
    assert viewer.current_group().total == 3
    dialog.return_full()
    assert viewer.current_group().total == 4 and viewer.current_group().visible
