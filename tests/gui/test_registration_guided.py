"""The guided registration route must produce the existing typed scientific tasks."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint

from dmri_studio.core.scientific import Task


def _artifact(kind, name):
    space_kind = "DWI_NATIVE" if name in {"epi", "dwi"} else "T1_NATIVE"
    modality = "b0" if name == "epi" else "T1w"
    declarations = {
        "subject_id": "synthetic-subject",
        "acquisition_id": name,
        "space_id": f"synthetic-{space_kind.lower()}-{name}",
        "space_kind": space_kind,
        "anatomical_modality": modality,
        "processing_state": "preprocessed" if name == "dwi" else "unknown",
        "reference_history": "synthetic correction history" if name in {"epi", "dwi"} else "",
        "anatomical_preparation": "brain_extracted" if name == "brain" else "unknown",
        "mask_role": "white_matter_segmentation" if name == "wm" else "unknown",
    }
    return SimpleNamespace(
        id=name,
        kind=kind,
        payload={
            "path": f"/synthetic/{name}.nii.gz",
            "declarations": declarations,
            "gradients": {"bvals": [0, 1000]} if name == "dwi" else None,
        },
    )


def _select(combo, value):
    index = combo.findData(value)
    assert index >= 0
    combo.setCurrentIndex(index)


def test_guided_registration_goals_are_separate_and_reused(window):
    window.focus_registration()
    panel = window.scientific.registration
    guided = panel.guided
    assert (
        "transform direction"
        in window.scientific.heading.findChildren(type(guided.summary))[1].text()
    )
    assert guided.goal == "align_two"
    assert guided.pages.currentIndex() == 2
    assert guided.moving.isVisibleTo(guided)
    assert not guided.dwi.isVisibleTo(guided)
    assert "Import two identified" in guided.inventory_notice.text()
    guided.select_goal("reference")
    assert guided.pages.currentIndex() == 0
    assert guided.dwi.isVisibleTo(guided)
    assert not guided.moving.isVisibleTo(guided)
    assert "does not align it with anatomy" in guided.explanation.text()
    assert "documented preprocessed DWI" in guided.inventory_notice.text()
    guided.select_goal("inspect")
    assert guided.pages.currentIndex() == 5
    assert not guided.preview.isVisibleTo(guided)
    window.focus_tracking_plan()
    window.focus_registration()
    assert window.scientific.registration is panel
    assert panel.guided is guided
    assert guided.goal == "inspect"


@pytest.mark.parametrize("appearance", ["light", "dark"])
def test_guided_controls_remain_scroll_accessible_at_minimum_size(window, qapp, appearance):
    window.theme.apply(appearance)
    window.resize(960, 640)
    window.focus_registration()
    qapp.processEvents()
    panel = window.scientific.registration
    guided = panel.guided
    assert panel.horizontalScrollBar().maximum() == 0
    assert guided.preview.accessibleName() or guided.preview.text()
    assert all(button.minimumSizeHint().height() > 0 for button in guided.goal_buttons.values())
    panel.ensureWidgetVisible(guided.run_button)
    qapp.processEvents()
    button_rect = guided.run_button.geometry()
    visible_rect = panel.viewport().rect()
    mapped = guided.run_button.mapTo(panel.viewport(), QPoint(0, 0))
    assert mapped.y() < visible_rect.bottom()
    assert mapped.y() + button_rect.height() > visible_rect.top()


def test_guided_tasks_map_to_supported_registry_operations(window):
    window.focus_registration()
    panel = window.scientific.registration
    panel.owner.artifacts = [
        _artifact("dwi", "dwi"),
        _artifact("anatomy", "epi"),
        _artifact("anatomy", "t1"),
        _artifact("anatomy", "brain"),
        _artifact("mask", "wm"),
        _artifact("atlas_labels", "atlas"),
        _artifact("roi", "roi"),
        _artifact("atlas_probability", "probability"),
        _artifact("spatial_transform", "transform"),
    ]
    panel.refresh()
    guided = panel.guided

    guided.select_goal("reference")
    _select(guided.dwi, "dwi")
    _select(guided.low_b_selection, "first_low_b")
    guided.threshold.setValue(80)
    task = guided.build_task()
    assert task.algorithm == "registration.reference"
    assert task.inputs == {"dwi": "dwi"}
    assert task.parameters == {"selection": "first_low_b", "b0_threshold": 80}

    guided.select_goal("epi_t1")
    for combo, value in (
        (guided.epi_moving, "epi"),
        (guided.epi_reference, "t1"),
        (guided.t1_brain, "brain"),
        (guided.wmseg, "wm"),
    ):
        _select(combo, value)
    task = guided.build_task()
    assert task.algorithm == "registration.epi_reg"
    assert task.inputs == {"moving": "epi", "reference": "t1", "t1_brain": "brain", "wmseg": "wm"}
    assert "epi.nii.gz" in guided.direction.text()
    assert "t1.nii.gz" in guided.direction.text()

    guided.select_goal("align_two")
    _select(guided.moving, "t1")
    _select(guided.reference, "epi")
    _select(guided.alignment_method, "rigid")
    _select(guided.cost, "mutualinfo")
    task = guided.build_task()
    assert task.algorithm == "registration.flirt"
    assert task.inputs == {"moving": "t1", "reference": "epi"}
    assert task.parameters == {"model": "rigid", "cost": "mutualinfo"}

    guided.select_goal("move")
    _select(guided.target, "epi")
    _select(guided.transform, "transform")
    for kind, expected in (
        ("atlas", "labels"),
        ("roi", "roi"),
        ("probability", "probability"),
        ("t1", "scalar"),
    ):
        _select(guided.derivative, kind)
        task = guided.build_task()
        assert task.algorithm == "registration.apply." + expected
        assert task.inputs == {"moving": kind, "reference": "epi", "transform": "transform"}

    guided.select_goal("existing_atlas_qc")
    _select(guided.imported_anatomy, "atlas")
    assert "needs a hash-bound QC association" in guided.imported_details.text()
    _select(guided.imported_anatomy, "epi")
    assert "needs an exact generated low-b reference record" in guided.imported_details.text()
    _select(guided.existing_atlas, "atlas")
    _select(guided.existing_reference, "epi")
    task = guided.build_task()
    assert task.algorithm == "registration.qc"
    assert task.inputs == {"atlas": "atlas", "reference": "epi"}
    assert "No transform is estimated or applied" in guided.direction.text()


def test_guided_hides_clearly_ineligible_dwi_and_epi_candidates(window):
    window.focus_registration()
    panel = window.scientific.registration
    raw = _artifact("dwi", "raw")
    wrong = _artifact("anatomy", "wrong")
    valid = _artifact("anatomy", "epi")
    panel.owner.artifacts = [raw, wrong, valid]
    panel.refresh()
    assert panel.guided.dwi.findData("raw") == -1
    assert panel.guided.epi_moving.findData("wrong") == -1
    assert panel.guided.epi_moving.findData("epi") >= 0


def test_guided_edit_invalidates_approval_and_advanced_task_precedence(window):
    window.focus_registration()
    panel = window.scientific.registration
    guided = panel.guided
    panel.plan = SimpleNamespace(digest="synthetic")
    guided.approval.setChecked(True)
    assert panel.approval.isChecked()
    guided.select_goal("align_two")
    assert panel.plan is not None
    _select(guided.alignment_method, "affine")
    assert panel.plan is None
    assert not panel.approval.isChecked()
    assert not guided.run_button.isEnabled()
    panel.method.setCurrentIndex(panel.method.findData("registration.invert"))
    panel.task = Task("advanced", "registration.invert", {"transform": "id"}, {})
    assert not guided.active
    assert "Advanced typed task" in guided.advanced_notice.text()
    guided.select_goal("move")
    assert guided.active
    assert panel.task is None


def test_guided_missing_input_does_not_start_a_worker(window):
    window.focus_registration()
    panel = window.scientific.registration
    guided = panel.guided
    guided.select_goal("align_two")
    guided.output.setText("/synthetic/new-registration")
    guided.preview.click()
    assert panel.owner.thread is None
    assert panel.plan is None
    assert "Choose the moving image" in guided.summary.text()


def test_guided_real_rigid_preview_uses_exact_approved_plan(window, qtbot, tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "reg_fixture", Path(__file__).parents[1] / "test_registration.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    f = fixture.fixture.__wrapped__(tmp_path, monkeypatch)
    window.focus_registration()
    page = window.scientific
    page.artifacts = [f["a"], f["b"]]
    page.tool_directory = fixture.MR
    page.refresh()
    panel = page.registration
    guided = panel.guided
    _select(guided.moving, f["a"].id)
    _select(guided.reference, f["b"].id)
    _select(guided.alignment_method, "rigid")
    _select(guided.cost, "mutualinfo")
    guided.output.setText(str(f["out"] / "guided-rigid"))
    guided.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert panel.plan is not None, page.status.text()
    assert panel.plan.payload["tasks"][0]["algorithm"] == "registration.flirt"
    assert "-dof 6" in guided.commands.toPlainText()
    assert panel.plan.digest in guided.summary.text()
    assert "a.nii.gz" in guided.summary.text() and "b.nii.gz" in guided.summary.text()
    assert not guided.run_button.isEnabled()
    guided.approval.setChecked(True)
    assert guided.run_button.isEnabled()
    guided.cost.setCurrentIndex(guided.cost.findData("normmi"))
    assert panel.plan is None and not guided.run_button.isEnabled()


def test_guided_low_b_preview_survives_verified_product_refresh(
    window, qtbot, tmp_path, monkeypatch
):
    import nibabel as nib
    import numpy as np

    from dmri_studio.core.scientific import ImportRequest, import_artifact

    spec = importlib.util.spec_from_file_location(
        "reg_fixture", Path(__file__).parents[1] / "test_registration.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    f = fixture.fixture.__wrapped__(tmp_path, monkeypatch)
    source = nib.load(f["s"].image)
    base = np.asarray(source.dataobj) + 10
    values = np.stack([base * 2, base * 3, base * 5, base * 7], axis=-1).astype("f4")
    dwi = fixture.image(f["src"] / "dwi.nii.gz", values, source.affine)
    bval = f["src"] / "bval"
    bvec = f["src"] / "bvec"
    bval.write_text("1000 0 1000 20")
    bvec.write_text("1 0 0 0\n0 0 1 0\n0 0 0 0")
    imported = import_artifact(
        ImportRequest(
            "dwi",
            dwi,
            bval=bval,
            bvec=bvec,
            declarations=dict(
                subject_id="synthetic",
                session_id="01",
                acquisition_id="acq1",
                space_id="native-dwi",
                processing_state="preprocessed",
                reference_history="Controlled processed fixture",
            ),
        )
    )
    window.focus_registration()
    page = window.scientific
    page.artifacts = [imported]
    page.tool_directory = fixture.MR
    page.refresh()
    panel = page.registration
    guided = panel.guided
    guided.select_goal("reference")
    _select(guided.dwi, imported.id)
    _select(guided.low_b_selection, "mean_low_b")
    guided.threshold.setValue(50)
    guided.output.setText(str(f["out"] / "guided-low-b"))
    guided.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert panel.plan is not None, page.status.text()
    exact_plan = panel.plan.to_dict()
    assert exact_plan["indices"] == [1, 3]
    assert exact_plan["threshold"] == 50
    argv = exact_plan["commands"][0]["argv"]
    assert argv[argv.index("-coord") + 1 : argv.index("-coord") + 3] == ["3", "1,3"]
    assert "selected volume indices (2): 1, 3" in guided.summary.text()
    assert panel.plan.digest in guided.summary.text()
    guided.approval.setChecked(True)
    guided.threshold.setValue(60)
    assert panel.plan is None
    assert not guided.approval.isChecked()
    assert not guided.run_button.isEnabled()
    guided.threshold.setValue(50)
    guided.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert panel.plan is not None, page.status.text()
    assert panel.plan.to_dict()["indices"] == [1, 3]
    guided.approval.setChecked(True)
    guided.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=60000)
    assert panel.manifest["status"] == "validated_pending_qc", page.status.text()
    assert panel.manifest["plan"]["indices"] == [1, 3]
    assert panel.manifest["validation"]["checks"]["selected_indices"] == [1, 3]
    assert panel.plan is None
    assert not guided.run_button.isEnabled()
    assert "selected volume indices (2): 1, 3" in guided.summary.text()
    assert panel.manifest["approval"]["digest"] in guided.summary.text()
    assert "1,3" in guided.commands.toPlainText()
    guided.threshold.setValue(60)
    assert "No approved plan" in guided.summary.text()
    assert not guided.run_button.isEnabled()


def test_guided_epi_t1_preview_binds_structural_evidence(window, qtbot, tmp_path, monkeypatch):
    import nibabel as nib

    from dmri_studio.core.execution import _publish_json, hash_file
    from dmri_studio.core.scientific import ImportRequest, import_artifact

    spec = importlib.util.spec_from_file_location(
        "reg_fixture", Path(__file__).parents[1] / "test_registration.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    f = fixture.fixture.__wrapped__(tmp_path, monkeypatch)
    d = dict(f["b"].to_dict()["declarations"], anatomical_modality="b0")
    moving = import_artifact(ImportRequest("anatomy", Path(f["t"].image), declarations=d))
    image = nib.load(f["s"].image)
    brain = fixture.image(f["src"] / "t1_brain.nii.gz", image.get_fdata(), image.affine)
    brain_evidence = f["src"] / "t1-brain-space.json"
    _publish_json(
        brain_evidence,
        dict(
            schema="tractosphere.space-association/1",
            image_sha256=hash_file(brain),
            reference_sha256=f["s"].sha256,
            status="reviewed",
            review_notes="Controlled synthetic brain extraction for guided planning",
        ),
    )
    brain_artifact = import_artifact(
        ImportRequest(
            "anatomy",
            brain,
            declarations={
                **f["a"].to_dict()["declarations"],
                "space_review": str(brain_evidence),
                "anatomical_preparation": "brain_extracted",
            },
        )
    )
    mask = fixture.image(
        f["src"] / "wmseg.nii.gz", (image.get_fdata() > 0).astype("u1"), image.affine
    )
    evidence = f["src"] / "wm-space.json"
    _publish_json(
        evidence,
        dict(
            schema="tractosphere.space-association/1",
            image_sha256=hash_file(mask),
            reference_sha256=f["s"].sha256,
            status="reviewed",
            review_notes="Controlled synthetic segmentation, not anatomical acceptance",
        ),
    )
    wm = import_artifact(
        ImportRequest(
            "mask",
            mask,
            declarations={
                **f["a"].to_dict()["declarations"],
                "space_review": str(evidence),
                "mask_role": "white_matter_segmentation",
            },
        )
    )
    window.focus_registration()
    page = window.scientific
    page.artifacts = [moving, f["a"], brain_artifact, wm]
    page.tool_directory = fixture.MR
    page.refresh()
    guided = page.registration.guided
    guided.select_goal("epi_t1")
    _select(guided.epi_moving, moving.id)
    _select(guided.epi_reference, f["a"].id)
    _select(guided.t1_brain, brain_artifact.id)
    _select(guided.wmseg, wm.id)
    guided.output.setText(str(f["out"] / "guided-epi"))
    guided.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    plan = page.registration.plan
    assert plan is not None, page.status.text()
    assert plan.payload["tasks"][0]["algorithm"] == "registration.epi_reg"
    assert "--epi=" in guided.commands.toPlainText()
    assert "--wmseg=" in guided.commands.toPlainText()
    assert plan.payload["source"]["kind"] == "DWI_NATIVE"
    assert plan.payload["reference"]["kind"] == "T1_NATIVE"
    assert not guided.run_button.isEnabled()


def test_atlas_result_handoff_keeps_categorical_semantics(window, tmp_path):
    window.focus_registration()
    panel = window.scientific.registration
    output = tmp_path / "run"
    panel.manifest = {
        "status": "validated_pending_qc",
        "inputs_unchanged": True,
        "plan": {
            "output_directory": str(output),
            "operation": "apply.labels",
            "bound": {"moving": {"kind": "atlas_labels"}},
            "reference": {"image": "/synthetic/reference.nii.gz", "sha256": "refhash"},
        },
        "outputs": {"registered.nii.gz": {"sha256": "producthash"}},
    }
    products = []
    panel.verify_view = products.append
    panel.open_result()
    assert products == [
        {
            "kind": "registration_alignment",
            "categorical": True,
            "reference": "/synthetic/reference.nii.gz",
            "reference_sha256": "refhash",
            "path": str(output / "registered.nii.gz"),
            "sha256": "producthash",
        }
    ]
    panel.manifest["inputs_unchanged"] = False
    panel.open_result()
    assert len(products) == 1
