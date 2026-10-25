"""Controlled analytic 5TT/straight-fiber fixtures, never subject tissue data."""

import json
import os
from pathlib import Path
from threading import Event

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.execution import execute_plan, hash_file
from dmri_studio.core.qc import load_catalog, record_review
from dmri_studio.core.scientific import ImportRequest, Task, import_artifact
from dmri_studio.core.scientific.act_qc import sample_fractions
from dmri_studio.core.scientific.anatomy import (
    CHANNEL_DECLARATION,
    accepted_tissue_review,
    alignment_review,
    prepare_anatomy,
    tissue_statistics,
)
from dmri_studio.core.scientific.csd import sh_design
from dmri_studio.core.scientific.tracking import prepare_tracking

BIN = Path("/usr/local/mrtrix3/bin")
native = pytest.mark.skipif(not (BIN / "5ttcheck").is_file(), reason="Native MRtrix3 required")


def save(path, data, affine):
    """Write explicit test values with physical units and a nonidentity affine."""
    im = nib.Nifti1Image(np.asarray(data, "f4"), affine)
    im.header.set_xyzt_units("mm")
    im.set_sform(affine, 1)
    im.set_qform(affine, 1)
    nib.save(im, path)
    return path


@pytest.fixture
def tissue(tmp_path):
    from dmri_studio.core.fod import sphere_mesh

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    affine = np.array([[-2.0, 0, 0, 20], [0, 2, 0, -20], [0, 0, 2, -20], [0, 0, 0, 1]])
    data = np.zeros((20, 20, 20, 5), "f4")
    data[4:16, 2:18, 2:18, 2] = 1
    data[2:4, 2:18, 2:18, 0] = 1
    data[16:18, 2:18, 2:18, 0] = 1
    data[1, 2:18, 2:18, 3] = 1
    data[18, 2:18, 2:18, 3] = 1
    refpath = save(inputs / "reference.nii", np.ones(data.shape[:3]), affine)
    evidence = inputs / "evidence.txt"
    evidence.write_text("Analytic fixture; no real anatomical registration or subject claims")
    review = inputs / "review.json"
    record = dict(
        schema="tractosphere.anatomical-alignment-review/1",
        subject_id="analytic",
        space_id="analytic-ras",
        anatomy=dict(path=str(refpath), sha256=hash_file(refpath)),
        reference=dict(path=str(refpath), sha256=hash_file(refpath)),
        evidence=[dict(path=str(evidence), sha256=hash_file(evidence))],
        planes_reviewed=["axial", "sagittal", "coronal"],
        review_notes="Analytic planes defined explicitly in test coordinates; not biological validation",
    )
    review.write_text(json.dumps(record))
    decl = dict(
        subject_id="analytic",
        space_id="analytic-ras",
        channels=CHANNEL_DECLARATION,
        anatomy_sha256=hash_file(refpath),
        anatomical_review=str(review),
    )
    five = import_artifact(
        ImportRequest("5tt", save(inputs / "five.nii", data, affine), declarations=decl)
    )
    metrics = inputs / "five-qc-metrics.json"
    metrics.write_text(json.dumps({"products": [{"artifact": five.to_dict()}]}))
    catalog_path = inputs / "five-qc-catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "dmri_qc_catalog",
                "identity": {"subject": "analytic"},
                "stages": [
                    {
                        "id": "anatomy",
                        "title": "Analytic five-tissue fixture",
                        "execution": "completed",
                        "technical": "passed",
                        "automated_qc": "pending",
                        "scientific_review": "required",
                        "findings": ["Analytic fixture only; no participant anatomy is accepted"],
                        "artifacts": [
                            {
                                "kind": "metrics",
                                "label": "Analytic values",
                                "path": metrics.name,
                                "sha256": hash_file(metrics),
                            }
                        ],
                    }
                ],
            }
        )
    )
    tissue_review = record_review(
        load_catalog(catalog_path),
        "anatomy",
        "accepted",
        "Analytic fixture",
        "The analytic channel boundaries are defined by this test array; no human anatomy claim",
        inputs,
    )
    five = import_artifact(
        ImportRequest(
            "5tt",
            inputs / "five.nii",
            declarations={**decl, "tissue_review": str(tissue_review)},
        )
    )
    reference = import_artifact(ImportRequest("anatomy", refpath, declarations=decl))
    dirs = sphere_mesh(1)[0]
    coef = np.linalg.lstsq(sh_design(dirs, 8), dirs[:, 0] ** 8, rcond=None)[0]
    fod = import_artifact(
        ImportRequest(
            "fod",
            save(inputs / "fod.nii", np.broadcast_to(coef, (20, 20, 20, 45)).copy(), affine),
            declarations={
                **decl,
                "normalization": "not_done",
                "sh_basis": "mrtrix3_real_symmetric",
                "sh_frame": "world_ras",
                "tissue": "wm",
            },
        )
    )
    seed = import_artifact(
        ImportRequest("mask", save(inputs / "seed.nii", data[..., 2], affine), declarations=decl)
    )
    return five, reference, fod, seed


def act_task(five, ref, fod, seed, method="tckgen.act_ifod2", **updates):
    p = dict(
        select=10,
        seeds=500,
        seed_method="image",
        step_mm=0.5,
        angle_deg=30,
        cutoff=0.01,
        seed_cutoff=0.02,
        min_length_mm=3,
        max_length_mm=38,
        max_attempts_per_seed=10,
        backtrack="on" if method.endswith("ifod2") else "off",
        crop_at_gmwmi="on",
    )
    p.update(updates)
    return Task(
        "track", method, dict(fod=fod.id, five_tt=five.id, reference=ref.id, seed_mask=seed.id), p
    )


def test_tissue_structure_and_unchanged(tissue):
    five, ref, _, _ = tissue
    before = hash_file(Path(five.payload["path"]))
    stats = tissue_statistics(five.payload["path"])
    assert stats["brain_voxels"] == 18 * 16 * 16
    assert stats["maximum_sum_error"] == 0
    # det(diag(-2,2,2)) is -7.999999999999998 in binary64 on this BLAS.
    assert stats["volume_mm3"][2] == pytest.approx(12 * 16 * 16 * 8, rel=0, abs=1e-10)
    assert hash_file(Path(five.payload["path"])) == before
    assert alignment_review(five.to_dict(), ref.to_dict())[0]["subject_id"] == "analytic"


def test_tissue_review_blocks_seeds_and_act_when_missing_or_pending(tissue, tmp_path):
    five, ref, fod, seed = tissue
    original = five.to_dict()
    declarations = dict(original["declarations"])
    review_path = Path(declarations.pop("tissue_review"))
    unreviewed = import_artifact(
        ImportRequest("5tt", Path(five.payload["path"]), declarations=declarations)
    )
    gmwmi = Task("seeds", "5tt2gmwmi", dict(five_tt=unreviewed.id, reference=ref.id))
    with pytest.raises(ValueError, match="accepted, hash-bound 5TT"):
        prepare_anatomy((unreviewed, ref), (gmwmi,), tmp_path / "seeds")
    with pytest.raises(ValueError, match="accepted, hash-bound 5TT"):
        prepare_tracking(
            (unreviewed, ref, fod, seed),
            (act_task(unreviewed, ref, fod, seed),),
            tmp_path / "act",
        )

    catalog = load_catalog(Path(json.loads(review_path.read_text())["catalog"]))
    pending_path = record_review(
        catalog,
        "anatomy",
        "needs_review",
        "Analytic fixture",
        "GM-WM boundary remains unresolved",
        tmp_path,
    )
    pending = import_artifact(
        ImportRequest(
            "5tt",
            Path(five.payload["path"]),
            declarations={**declarations, "tissue_review": str(pending_path)},
        )
    )
    with pytest.raises(ValueError, match="not accepted"):
        prepare_anatomy(
            (pending, ref),
            (Task("seeds", "5tt2gmwmi", dict(five_tt=pending.id, reference=ref.id)),),
            tmp_path / "pending-seeds",
        )


def test_tissue_review_cannot_unlock_another_image(tissue, tmp_path):
    five = tissue[0]
    alternate = tmp_path / "copied-five.nii"
    alternate.write_bytes(Path(five.payload["path"]).read_bytes())
    imported = import_artifact(
        ImportRequest("5tt", alternate, declarations=five.payload["declarations"])
    )
    with pytest.raises(ValueError, match="does not identify this exact 5TT"):
        accepted_tissue_review(imported.to_dict())


@pytest.mark.parametrize("fault", ["negative", "nonfinite", "sum", "channels", "empty"])
def test_invalid_5tt_rejected(tissue, tmp_path, fault):
    five, _, _, _ = tissue
    im = nib.load(five.payload["path"])
    a = np.asanyarray(im.dataobj).copy()
    if fault == "negative":
        a[5, 5, 5, 2] = -0.1
    if fault == "nonfinite":
        a[5, 5, 5, 2] = np.nan
    if fault == "sum":
        a[5, 5, 5, 2] = 0.5
    if fault == "channels":
        a = a[..., :4]
    if fault == "empty":
        a[:] = 0
    p = save(tmp_path / "bad.nii", a, im.affine)
    with pytest.raises(ValueError):
        import_artifact(ImportRequest("5tt", p, declarations=five.payload["declarations"]))


@pytest.mark.parametrize(
    "field", ["anatomy", "reference", "planes_reviewed", "subject_id", "evidence"]
)
def test_review_not_just_geometry(tissue, field):
    five, ref, _, _ = tissue
    p = Path(five.payload["declarations"]["anatomical_review"])
    data = json.loads(p.read_text())
    data[field] = (
        []
        if field in ("evidence", "planes_reviewed")
        else {}
        if field in ("anatomy", "reference")
        else "different"
    )
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        alignment_review(five.to_dict(), ref.to_dict())


@pytest.mark.parametrize(
    "affine",
    [
        np.diag([2.0, 3.0, 4.0, 1.0]),
        np.array([[0, -2, 0, 30], [3, 0, 0, -20], [0, 0, 4, 10], [0, 0, 0, 1.0]]),
    ],
)
def test_continuous_tissue_sampling_physical_coordinates(affine):
    data = np.zeros((4, 5, 6, 5))
    data[..., 2] = 1
    points = nib.affines.apply_affine(affine, [[1.25, 2.5, 3.25], [2, 3, 4]])
    a, inside, vox = sample_fractions(points, data, affine)
    np.testing.assert_allclose(a[:, 2], 1)
    np.testing.assert_allclose(vox, [[1.25, 2.5, 3.25], [2, 3, 4]])
    assert inside.all()


@native
@pytest.mark.parametrize("operation", ["5ttcheck", "5tt2vis", "5tt2gmwmi"])
def test_actual_tissue_operations(tissue, tmp_path, operation):
    five, ref, _, _ = tissue
    task = Task("tissue", operation, dict(five_tt=five.id, reference=ref.id))
    plan = prepare_anatomy(tissue[:2], (task,), tmp_path / "attempt", tool_directory=BIN)
    assert (
        plan.digest
        == prepare_anatomy(tissue[:2], (task,), tmp_path / "attempt", tool_directory=BIN).digest
    )
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    assert result["inputs_unchanged"]
    assert result["validation"]["scientific_acceptance"] == "unassigned"
    if operation == "5tt2vis":
        assert len(result["validation"]["products"]) == 6
    if operation == "5tt2gmwmi":
        a = result["validation"]["products"][0]["artifact"]
        assert a["kind"] == "gmwmi" and a["verification"]["maximum"] <= 1


@native
@pytest.mark.parametrize("method", ["tckgen.act_ifod2", "tckgen.act_ifod1", "tckgen.act_sdstream"])
def test_actual_act_generation(tissue, tmp_path, method):
    five, ref, fod, seed = tissue
    t = act_task(*tissue, method=method)
    plan = prepare_tracking(tissue, (t,), tmp_path / "act", tool_directory=BIN)
    argv = next(c["argv"] for c in plan.payload["commands"] if c["kind"] == "tracts")
    assert "-act" in argv and "-mask" not in argv and "-crop_at_gmwmi" in argv
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    m = result["validation"]["measurements"][0]
    assert m["streamlines"] == 10 and m["act"]["endpoints"] == 20
    assert m["act"]["outside_field_endpoints"] == 0
    assert m["tracking_mask_assessed"] is False
    assert m["mask_streamlines_with_any_outside_saved_vertex"] is None
    assert m["mask_streamlines_with_outside_endpoint"] is None
    assert (tmp_path / "act/track-endpoints.nii.gz").is_file()
    assert result["inputs_unchanged"]


@native
def test_approval_cancellation_and_existing_output(tissue, tmp_path):
    t = Task("check", "5ttcheck", dict(five_tt=tissue[0].id, reference=tissue[1].id))
    plan = prepare_anatomy(tissue[:2], (t,), tmp_path / "attempt", tool_directory=BIN)
    with pytest.raises(ValueError, match="Approve"):
        execute_plan(None, plan, approval="wrong")
    event = Event()
    event.set()
    from dmri_studio.core.process import ProcessingCancelled

    with pytest.raises(ProcessingCancelled):
        execute_plan(None, plan, approval=plan.digest, cancelled=event.is_set)
    assert not (tmp_path / "attempt").exists()
    (tmp_path / "attempt").mkdir()
    with pytest.raises(ValueError, match="NEW"):
        prepare_anatomy(tissue[:2], (t,), tmp_path / "attempt", tool_directory=BIN)


@native
def test_act_backtracking_and_seed_constraints(tissue, tmp_path):
    from dataclasses import replace

    t = act_task(*tissue, method="tckgen.act_sdstream", backtrack="on")
    with pytest.raises(ValueError, match="Backtracking"):
        prepare_tracking(tissue, (t,), tmp_path / "bad", tool_directory=BIN)
    t = act_task(*tissue)
    t = replace(
        t,
        parameters={**t.parameters, "stop_after_includes": "on"},
        inputs={**t.inputs, "include_1": tissue[3].id},
    )
    with pytest.raises(ValueError):
        prepare_tracking(tissue, (t,), tmp_path / "bad2", tool_directory=BIN)


def test_gmwmi_is_probability_not_binary_mask(tissue, tmp_path):
    image = nib.load(tissue[1].payload["path"])
    a = np.zeros(image.shape, "f4")
    a[4, 5, 6] = 0.25
    p = save(tmp_path / "gmwmi.nii", a, image.affine)
    result = import_artifact(
        ImportRequest("gmwmi", p, declarations=tissue[0].payload["declarations"])
    )
    assert result.payload["verification"]["maximum"] == 0.25
    with pytest.raises(ValueError, match="continuous probabilities"):
        import_artifact(ImportRequest("mask", p))


@pytest.mark.parametrize("contrast", [None, "b0", "other", "T1w"])
def test_anatomical_contrast_not_inferred(tissue, contrast):
    from dmri_studio.core.scientific.anatomy import anatomical_input_qc

    source = tissue[1].to_dict()
    if contrast:
        source["declarations"]["anatomical_modality"] = contrast
    # Even declared T1w is rejected for this constant analytic reference.
    with pytest.raises(ValueError, match="constant|T1w"):
        anatomical_input_qc(source)


def test_declared_t1_quality_and_fa_rejection(tissue, tmp_path):
    from dmri_studio.core.scientific.anatomy import anatomical_input_qc

    ref = tissue[1].to_dict()
    im = nib.load(ref["path"])
    p = save(tmp_path / "t1.nii", np.arange(np.prod(im.shape)).reshape(im.shape) - 5, im.affine)
    a = import_artifact(ImportRequest("anatomy", p, declarations={"anatomical_modality": "T1w"}))
    q = anatomical_input_qc(a.to_dict())
    assert q["negative_values"] == 5 and q["evaluated_values"] == 8000
    bad = a.to_dict()
    bad["declarations"]["metric"] = "FA"
    with pytest.raises(ValueError, match="FA"):
        anatomical_input_qc(bad)


def test_native_rejection_diagnostics_not_inferred():
    from dmri_studio.core.scientific.tracking import native_tracking_diagnostics

    text = """tckgen: [INFO] Termination reason probabilities:
tckgen: [INFO]   Entered CSF: 0.0307%
tckgen: [INFO] Track rejection counts:
tckgen: [INFO]   Poor structural termination: 546
tckgen: [INFO]   Failed to traverse white matter: 10
"""
    q = native_tracking_diagnostics(text)
    assert q["termination_probabilities_percent"] == {"Entered CSF": 0.0307}
    assert q["rejection_counts"] == {
        "Poor structural termination": 546,
        "Failed to traverse white matter": 10,
    }


@native
def test_changed_alignment_evidence_invalidates_approval(tissue, tmp_path):
    t = act_task(*tissue)
    p = prepare_tracking(tissue, (t,), tmp_path / "run", tool_directory=BIN)
    path = Path(tissue[0].payload["declarations"]["anatomical_review"])
    data = json.loads(path.read_text())
    data["review_notes"] += " Updated review."
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="changed"):
        execute_plan(None, p, approval=p.digest)
    assert not (tmp_path / "run").exists()


@native
def test_real_act_child_cancellation_does_not_publish(tissue, tmp_path):
    import os
    import re

    t = act_task(*tissue, select=100000, seeds=1000000)
    p = prepare_tracking(tissue, (t,), tmp_path / "cancelled", tool_directory=BIN)
    event = Event()
    pids = []
    tracking_started = False

    def progress(text):
        nonlocal tracking_started
        if text.startswith("Running "):
            tracking_started = "/tckgen " in text
        if tracking_started and "Resource sample" in text:
            pids.append(int(re.search(r"PID (\d+)", text)[1]))
            event.set()

    result = execute_plan(None, p, approval=p.digest, cancelled=event.is_set, progress=progress)
    assert result["status"] == "cancelled" and result["inputs_unchanged"]
    assert not result.get("outputs") and not result["validation"]
    assert pids
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


@native
def test_flirt_is_image_aware_not_generic_world_matrix(tissue, tmp_path):
    import subprocess

    from dmri_studio.core._mif_checks import rearrange, voxel_mapping

    ref = nib.load(tissue[1].payload["path"])
    values = np.arange(np.prod(ref.shape), dtype="f4").reshape(ref.shape) + 1
    src = save(tmp_path / "source.nii", values, ref.affine)
    matrix = np.eye(4)
    matrix[0, 3] = 2  # +1 FSL scaled-mm voxel, physical -2mm RAS x
    mat = tmp_path / "flirt.mat"
    np.savetxt(mat, matrix)
    converted = tmp_path / "mrtrix-reverse.txt"
    mr = tmp_path / "mr.nii"
    fsl = tmp_path / "fsl.nii.gz"
    flirt = Path(os.environ.get("FSLDIR", "/usr/local/fsl")) / "bin/flirt"
    if not flirt.is_file():
        pytest.skip("FSL FLIRT unavailable; no invented convention comparison")
    commands = [
        [BIN / "transformconvert", mat, src, src, "flirt_import", converted],
        [
            BIN / "mrtransform",
            src,
            mr,
            "-linear",
            converted,
            "-template",
            src,
            "-interp",
            "linear",
            "-nthreads",
            "1",
        ],
        [
            flirt,
            "-in",
            src,
            "-ref",
            src,
            "-applyxfm",
            "-init",
            mat,
            "-interp",
            "trilinear",
            "-out",
            fsl,
        ],
    ]
    for args in commands:
        r = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=15)
        assert r.returncode == 0, r.stderr
    a = nib.load(mr)
    b = nib.load(fsl)
    axes, flips = voxel_mapping(a.affine, a.shape, b.affine, b.shape)
    x = rearrange(np.asarray(a.dataobj), axes, flips)
    y = np.asarray(b.dataobj)
    np.testing.assert_allclose(x[2:-2, 2:-2, 2:-2], y[2:-2, 2:-2, 2:-2], rtol=0, atol=0.02)
    # FSL's +2 scaled-mm moves toward negative RAS x on this LAS image.
    np.testing.assert_allclose(y[2:-2, 2:-2, 2:-2], values[1:-3, 2:-2, 2:-2], rtol=0, atol=0.02)
