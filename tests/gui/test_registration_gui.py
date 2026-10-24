"""Native registration controls and image-only overlays; no tractogram fixtures."""

import importlib.util
import os
from pathlib import Path

import pytest


def core_fixture(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "reg_fixture", Path(__file__).parents[1] / "test_registration.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, mod.fixture.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("appearance", ["light", "dark"])
def test_native_registration_preview_approval_alignment(
    window, qtbot, tmp_path, monkeypatch, appearance
):
    from dmri_studio.core.scientific import Task

    mod, f = core_fixture(tmp_path, monkeypatch)
    window.theme.apply(appearance)
    window.focus_registration()
    page = window.scientific
    page.artifacts = [f["a"], f["b"], f["transform"]]
    page.tool_directory = mod.MR
    page.refresh()
    panel = page.registration
    panel.method.setCurrentIndex(panel.method.findData("registration.apply.scalar"))
    panel.task = Task(
        "reg",
        "registration.apply.scalar",
        dict(moving=f["a"].id, reference=f["b"].id, transform=f["transform"].id),
        {},
    )
    panel.output.setText(str(f["out"] / "native-gui"))
    panel.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert panel.plan is not None, page.status.text()
    assert "-applyxfm" in panel.log.toPlainText()
    assert not panel.execute.isEnabled()
    panel.approval.setChecked(True)
    panel.execute.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert panel.manifest["status"] == "validated_pending_qc", panel.manifest.get("error")
    panel.open.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    qtbot.waitUntil(
        lambda: window.scientific_viewer is not None and not window.scientific_viewer.tasks,
        timeout=20000,
    )
    v = window.scientific_viewer
    assert v.registration_layer is not None, (
        v.status.text(),
        v.reference,
        v.pending_registration,
        page.status.text(),
    )
    assert v.groups == []
    assert v.registration_layer["registration"]
    for mode in ["Checkerboard", "Outlines", "Registered only", "Reference only", "Overlay"]:
        v.registration_mode.setCurrentText(mode)
        qtbot.wait(20)
        assert all(view.canvas.image is not None for view in v.views.values())
    v.registration_blink.setChecked(True)
    qtbot.wait(750)
    v.registration_blink.setChecked(False)
    assert not page.operation_state.busy


@pytest.mark.parametrize("appearance", ["light", "dark"])
def test_native_atlas_qc_regions_navigation_and_reopen(
    window, qtbot, tmp_path, monkeypatch, appearance
):
    from PySide6.QtCore import Qt

    from dmri_studio.core.execution import hash_file

    mod, f = core_fixture(tmp_path, monkeypatch)
    # Build actual MRtrix low-b reference and QC; retain valid evidence before
    # the independent core corruption test deliberately alters it.
    mod.test_gradient_selected_reference_not_volume_zero(f)
    import nibabel as nib
    import numpy as np

    from dmri_studio.core.execution import _publish_json
    from dmri_studio.core.scientific import ImportRequest, import_artifact

    ref = f["derived_reference"]
    labels = np.zeros((20, 20, 20), dtype="i2")
    labels[3:8, 4:10, 5:13] = 7
    labels[12:15, 8:12, 8:15] = 41
    atlas = mod.image(f["src"] / "atlas-qc.nii.gz", labels, nib.load(f["t"].image).affine)
    relation = f["src"] / "relation.json"
    _publish_json(
        relation,
        dict(
            schema="tractosphere.registered-atlas-qc-association/1",
            sha256=hash_file(atlas),
            reference_sha256=hash_file(Path(ref.payload["path"])),
            registration_reference=f["t"].image,
            registration_reference_sha256=f["t"].sha256,
            status="reviewed",
            review_notes="Known controlled synthetic geometry",
            evidence_hashes={
                str(f["src"] / "transform.json"): hash_file(f["src"] / "transform.json")
            },
        ),
    )
    declarations = dict(ref.to_dict()["declarations"])
    declarations.pop("reference_record")
    declarations["qc_association"] = str(relation)
    artifact = import_artifact(ImportRequest("atlas_labels", atlas, declarations=declarations))
    folder, result = mod.run(f, "qc", dict(atlas=artifact, reference=ref))
    product = result["validation"]["products"][0]
    window.theme.apply(appearance)
    window.open_scientific_product(product)
    qtbot.waitUntil(
        lambda: window.scientific_viewer is not None and not window.scientific_viewer.tasks,
        timeout=20000,
    )
    viewer = window.scientific_viewer
    assert not viewer.layer_low.isEnabled() and not viewer.layer_cmap.isEnabled()
    assert viewer.registration_regions.count() == 2
    assert all(control.isEnabled() for control in viewer.registration_review_buttons)
    assert viewer.registration_report_button.isEnabled()
    assert not viewer.groups
    before = hash_file(atlas)
    for position in ([0, 0, 0], [19, 19, 19], [5, 6, 8]):
        viewer.navigate(position)
        assert tuple(viewer.position) == tuple(position)
        assert all(v.canvas.image is not None for v in viewer.views.values())
    viewer.registration_regions.item(1).setCheckState(Qt.CheckState.Unchecked)
    viewer.registration_layer["opacity"] = 0.2
    viewer.registration_mode.setCurrentText("Outlines")
    viewer.refresh_slices()
    assert viewer.registration_visible_ids() == [7]
    viewer.registration_blink.setChecked(True)
    qtbot.wait(700)
    viewer.registration_blink.setChecked(False)
    assert viewer.registration_layer["visible"]
    viewer.registration_layer["visible"] = True
    workspace = tmp_path / "review-workspace.json"
    viewer.save_workspace(workspace)
    viewer.open_workspace(workspace)
    qtbot.waitUntil(lambda: not viewer.tasks, timeout=20000)
    assert viewer.registration_visible_ids() == [7]
    assert viewer.registration_mode.currentText() == "Outlines"
    assert viewer.registration_layer["opacity"] == 0.2
    assert tuple(viewer.position) == (5, 6, 8)
    assert hash_file(atlas) == before
    assert not viewer.groups
    # Native review actions must govern downstream eligibility, including old
    # projects that omit the QC pointer. Dialog answers are controlled test input.
    from PySide6.QtWidgets import QInputDialog

    from dmri_studio.core.registration_review import registration_eligibility

    monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("synthetic reviewer", True))
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        lambda *a: ("Known fixture; no human-data acceptance", True),
    )
    for decision, eligible in [("APPROVED", True), ("REJECTED", False), ("NEEDS_REVIEW", False)]:
        viewer.review_registration(decision)
        qtbot.waitUntil(lambda: not viewer.tasks, timeout=15000)
        state = registration_eligibility(artifact, ref)
        assert state["eligible"] is eligible
        assert viewer.registration_review_state == state["human_review_decision"]
        evidence_dir = os.environ.get("TRACTOSPHERE_STAGE_A_EVIDENCE")
        if evidence_dir:
            destination = Path(evidence_dir) / f"native-review-{decision.lower()}-{appearance}.png"
            assert viewer.grab().save(str(destination))


def test_native_batch_registration_approval_qc_table(window, qtbot, tmp_path, monkeypatch):
    import json

    spec = importlib.util.spec_from_file_location(
        "batch_reg", Path(__file__).parents[1] / "core/test_registration_batch.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("FSLDIR", str(Path.home() / "fsl"))
    study, workflow = mod.fixture(tmp_path)
    window.focus_batch()
    page = window.batch
    page.study = study
    page.workflow.setPlainText(json.dumps(workflow))
    page.output.setText(str(tmp_path / "gui-batch"))
    page.mrtrix_directory.setText("/usr/local/mrtrix3/bin")
    page.plan_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert page.plan is not None, page.status.text()
    assert not page.run_button.isEnabled()
    page.approval.setChecked(True)
    page.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert all(r["status"] == "COMPLETED" for r in page.rows), page.status.text()
    page.registration_qc()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    page.qc_filter.setCurrentText("NEEDS_REVIEW")
    qtbot.wait(1200)
    assert any(not page.table.isRowHidden(i) for i in range(page.table.rowCount()))
    assert all(r["qc_status"] == "NEEDS_REVIEW" for r in page.rows)
    # Reopen journal/QC without processing; verify all previous attempt counts.
    folder = page.folder
    page.load_batch(folder)
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert all(len(r["attempts"]) == 1 for r in page.rows)
