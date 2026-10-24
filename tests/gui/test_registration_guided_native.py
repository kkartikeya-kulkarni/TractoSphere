"""Native Cocoa walkthroughs for exact guided registration and Viewer handoff."""

import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("QT_QPA_PLATFORM") != "cocoa"
    or not Path("/usr/local/mrtrix3/bin/mrconvert").is_file()
    or not (Path(os.environ.get("FSLDIR", str(Path.home() / "fsl"))) / "bin/flirt").is_file(),
    reason="Native Cocoa/VTK, actual MRtrix3 and FSL installations are required",
)


def _fixture(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "registration_fixture", Path(__file__).parents[1] / "test_registration.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.fixture.__wrapped__(tmp_path, monkeypatch)


def _select(combo, value):
    index = combo.findData(value)
    assert index >= 0
    combo.setCurrentIndex(index)


def _run_guided(page, qtbot):
    panel = page.registration
    guided = panel.guided
    guided.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert panel.plan is not None, page.status.text()
    assert not guided.run_button.isEnabled()
    guided.approval.setChecked(True)
    guided.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=60000)
    assert panel.manifest["status"] == "validated_pending_qc", page.status.text()
    assert panel.manifest["inputs_unchanged"]
    return guided


def test_native_guided_rigid_alignment_inspection(window, qtbot, tmp_path, monkeypatch):
    from dmri_studio.core.execution import hash_file

    mod, f = _fixture(tmp_path, monkeypatch)
    original = hash_file(Path(f["a"].payload["path"]))
    window.focus_registration()
    page = window.scientific
    page.artifacts = [f["a"], f["b"]]
    page.tool_directory = mod.MR
    page.refresh()
    guided = page.registration.guided
    _select(guided.moving, f["a"].id)
    _select(guided.reference, f["b"].id)
    _select(guided.alignment_method, "rigid")
    _select(guided.cost, "mutualinfo")
    guided.output.setText(str(f["out"] / "guided-rigid"))
    _run_guided(page, qtbot)
    assert hash_file(Path(f["a"].payload["path"])) == original
    guided.open_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    qtbot.waitUntil(
        lambda: (
            window.scientific_viewer is not None
            and window.scientific_viewer.registration_layer is not None
            and not window.scientific_viewer.tasks
        ),
        timeout=20000,
    )
    viewer = window.scientific_viewer
    assert not any(control.isEnabled() for control in viewer.registration_review_buttons)
    for mode in ("Overlay", "Outlines", "Checkerboard", "Registered only", "Reference only"):
        viewer.registration_mode.setCurrentText(mode)
        qtbot.wait(20)
        assert viewer.registration_mode_info.text()
        assert all(view.canvas.image is not None for view in viewer.views.values())
    viewer.registration_blink.setChecked(True)
    qtbot.wait(700)
    viewer.registration_blink.setChecked(False)


def test_native_guided_atlas_resampling_preserves_labels(window, qtbot, tmp_path, monkeypatch):
    import nibabel as nib
    import numpy as np

    from dmri_studio.core.execution import _publish_json, hash_file
    from dmri_studio.core.scientific import ImportRequest, import_artifact

    mod, f = _fixture(tmp_path, monkeypatch)
    source = nib.load(f["s"].image)
    labels = np.asarray(source.dataobj, dtype="i2") * 7
    atlas = mod.image(f["src"] / "atlas.nii.gz", labels, source.affine)
    evidence = f["src"] / "space.json"
    _publish_json(
        evidence,
        dict(
            schema="tractosphere.space-association/1",
            image_sha256=hash_file(atlas),
            reference_sha256=f["s"].sha256,
            status="reviewed",
            review_notes="Known synthetic source grid",
        ),
    )
    declaration = dict(f["a"].to_dict()["declarations"], space_review=str(evidence))
    imported = import_artifact(ImportRequest("atlas_labels", atlas, declarations=declaration))
    original = hash_file(atlas)
    window.focus_registration()
    page = window.scientific
    page.artifacts = [imported, f["b"], f["transform"]]
    page.tool_directory = mod.MR
    page.refresh()
    guided = page.registration.guided
    guided.select_goal("move")
    _select(guided.derivative, imported.id)
    _select(guided.target, f["b"].id)
    _select(guided.transform, f["transform"].id)
    guided.output.setText(str(f["out"] / "guided-atlas"))
    _run_guided(page, qtbot)
    assert hash_file(atlas) == original
    registered = Path(page.registration.manifest["plan"]["output_directory"]) / "registered.nii.gz"
    values = nib.load(registered).get_fdata()
    assert set(np.unique(values)) <= {0, 7, 21}
    guided.open_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    qtbot.waitUntil(
        lambda: (
            window.scientific_viewer is not None
            and window.scientific_viewer.registration_layer is not None
            and not window.scientific_viewer.tasks
        ),
        timeout=20000,
    )
    viewer = window.scientific_viewer
    assert viewer.registration_layer["categorical"]
    assert viewer.registration_regions.count() == 2
    assert not viewer.registration_review_buttons[0].isEnabled()


def test_native_guided_epi_to_t1_keeps_direction_and_structural_evidence(
    window, qtbot, tmp_path, monkeypatch
):
    import nibabel as nib

    from dmri_studio.core.execution import _publish_json, hash_file
    from dmri_studio.core.scientific import ImportRequest, import_artifact
    from dmri_studio.core.spatial import load_transform

    mod, f = _fixture(tmp_path, monkeypatch)
    moving_declarations = dict(f["b"].to_dict()["declarations"], anatomical_modality="b0")
    moving = import_artifact(
        ImportRequest("anatomy", Path(f["t"].image), declarations=moving_declarations)
    )
    image = nib.load(f["s"].image)
    brain = mod.image(f["src"] / "t1_brain.nii.gz", image.get_fdata(), image.affine)
    brain_evidence = f["src"] / "t1-brain-space.json"
    _publish_json(
        brain_evidence,
        dict(
            schema="tractosphere.space-association/1",
            image_sha256=hash_file(brain),
            reference_sha256=f["s"].sha256,
            status="reviewed",
            review_notes="Controlled synthetic brain-extracted input; no anatomical acceptance",
        ),
    )
    t1_brain = import_artifact(
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
    mask = mod.image(f["src"] / "wmseg.nii.gz", (image.get_fdata() > 0).astype("u1"), image.affine)
    evidence = f["src"] / "wm-space.json"
    _publish_json(
        evidence,
        dict(
            schema="tractosphere.space-association/1",
            image_sha256=hash_file(mask),
            reference_sha256=f["s"].sha256,
            status="reviewed",
            review_notes="Controlled synthetic mask; no human anatomical acceptance",
        ),
    )
    wmseg = import_artifact(
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
    page.artifacts = [moving, f["a"], t1_brain, wmseg]
    page.tool_directory = mod.MR
    page.refresh()
    guided = page.registration.guided
    guided.select_goal("epi_t1")
    for combo, artifact in (
        (guided.epi_moving, moving),
        (guided.epi_reference, f["a"]),
        (guided.t1_brain, t1_brain),
        (guided.wmseg, wmseg),
    ):
        _select(combo, artifact.id)
    guided.output.setText(str(f["out"] / "guided-epi"))
    _run_guided(page, qtbot)
    folder = Path(page.registration.manifest["plan"]["output_directory"])
    transform = load_transform(folder / "transform.json")
    assert transform["source"]["kind"] == "DWI_NATIVE"
    assert transform["target"]["kind"] == "T1_NATIVE"
    assert page.registration.manifest["validation"]["scientific_acceptance"] == "unassigned"
    guided.open_button.click()
    qtbot.waitUntil(
        lambda: (
            window.scientific_viewer is not None
            and window.scientific_viewer.registration_layer is not None
            and not window.scientific_viewer.tasks
        ),
        timeout=20000,
    )


def test_native_guided_low_b_reference_uses_gradients(window, qtbot, tmp_path, monkeypatch):
    import nibabel as nib
    import numpy as np

    from dmri_studio.core.scientific import ImportRequest, import_artifact

    mod, f = _fixture(tmp_path, monkeypatch)
    source = nib.load(f["s"].image)
    base = np.asarray(source.dataobj) + 10
    values = np.stack([base * 2, base * 3, base * 5, base * 7], axis=-1).astype("f4")
    dwi = mod.image(f["src"] / "dwi.nii.gz", values, source.affine)
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
    page.tool_directory = mod.MR
    page.refresh()
    guided = page.registration.guided
    guided.select_goal("reference")
    _select(guided.dwi, imported.id)
    _select(guided.low_b_selection, "mean_low_b")
    guided.threshold.setValue(50)
    guided.output.setText(str(f["out"] / "guided-low-b"))
    _run_guided(page, qtbot)
    assert page.registration.manifest["validation"]["checks"]["selected_indices"] == [1, 3]
    assert "selected volume indices (2): 1, 3" in guided.summary.text()
    result = Path(page.registration.manifest["plan"]["output_directory"]) / "mean-b0.nii.gz"
    np.testing.assert_allclose(nib.load(result).get_fdata(), base * 5)
    reference_artifact = next(a for a in page.artifacts if a.payload["path"] == str(result))
    guided.select_goal("epi_t1")
    assert guided.epi_moving.findData(reference_artifact.id) > 0
    guided.select_goal("reference")
    guided.open_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    qtbot.waitUntil(
        lambda: (
            window.scientific_viewer is not None
            and window.scientific_viewer.reference is not None
            and window.scientific_viewer.reference.path == result
            and not window.scientific_viewer.tasks
        ),
        timeout=20000,
    )


def test_native_batch_registration_product_uses_verified_historical_handoff(
    window, qtbot, tmp_path, monkeypatch
):
    from dmri_studio.core.execution import hash_file
    from dmri_studio.core.runs import inspect_run

    mod, f = _fixture(tmp_path, monkeypatch)
    original = hash_file(Path(f["a"].payload["path"]))
    folder, record = mod.run(
        f,
        "apply.scalar",
        {"moving": f["a"], "reference": f["b"], "transform": f["transform"]},
        name="saved-batch-registration",
    )
    history = inspect_run(folder / "manifest.json")
    product = history["products"][0]
    assert product["sha256"] == record["outputs"]["registered.nii.gz"]["sha256"]
    assert product["reference_sha256"] == record["plan"]["reference"]["sha256"]
    window.focus_batch()
    window.batch.kind = "inspect"
    window.batch.result("inspect", history, None)
    assert window.batch.guided.products.count() == 1
    window.batch.guided.products.setCurrentRow(0)
    window.batch.guided._open_product()
    qtbot.waitUntil(
        lambda: (
            window.scientific_viewer is not None
            and window.scientific_viewer.registration_layer is not None
            and not window.scientific_viewer.tasks
        ),
        timeout=20000,
    )
    assert hash_file(Path(f["a"].payload["path"])) == original
    assert hash_file(Path(product["path"])) == product["sha256"]
    assert not any(
        control.isEnabled() for control in window.scientific_viewer.registration_review_buttons
    )
