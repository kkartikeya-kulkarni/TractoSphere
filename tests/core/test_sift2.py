"""Native SIFT2 integration and controlled weighted-edge arithmetic, no subject fixtures."""

import json
import subprocess
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
from test_act import tissue as analytic_tissue
from test_connectome import inputs as analytic_connectome_inputs

from dmri_studio.core.execution import execute_plan, hash_file
from dmri_studio.core.scientific import ALGORITHMS, ImportRequest, Task, import_artifact
from dmri_studio.core.scientific.connectome import (
    aggregate,
    load_bundle,
    prepare_connectome,
    read_matrix,
)
from dmri_studio.core.scientific.sift2 import (
    convergence,
    prepare_sift2,
    read_weights,
    verify_weight_binding,
    weight_statistics,
)


@pytest.fixture
def tissue(tmp_path):
    """Reuse the explicit analytic tissue definition without private data."""
    return analytic_tissue.__wrapped__(tmp_path)


@pytest.fixture
def inputs(tmp_path):
    """Reuse known parcel/endpoint geometry for native weighted comparisons."""
    return analytic_connectome_inputs.__wrapped__(tmp_path)


BIN = Path("/usr/local/mrtrix3/bin")
native = pytest.mark.skipif(not (BIN / "tcksift2").is_file(), reason="Native MRtrix3 required")


def bound_weights(path, tract, factors, **extra):
    """Explicit trusted analytic vector provenance, never an invented SIFT2 result."""
    np.savetxt(path, factors, fmt="%.17g")
    record = dict(
        schema="tractosphere.streamline-weights/1",
        representation="weight_factors",
        ordering="exact source file streamline order",
        weights=str(path),
        weights_sha256=hash_file(path),
        tractogram=tract.payload["path"],
        tractogram_sha256=hash_file(Path(tract.payload["path"])),
        streamlines=len(factors),
        origin="Controlled analytic vector; not SIFT2 estimation",
        **extra,
    )
    side = path.with_suffix(".json")
    side.write_text(json.dumps(record))
    return import_artifact(
        ImportRequest(
            "streamline_weights",
            path,
            declarations={
                **tract.to_dict()["declarations"],
                "tractogram_id": tract.id,
                "weight_record": str(side),
            },
        )
    )


@pytest.fixture
def sift_inputs(tissue, tmp_path):
    five, ref, fod, seed = tissue
    path = Path(fod.payload["path"]).parent / "known-tracks.tck"
    lines = [
        np.array([[x, y, 0] for x in np.linspace(-9, 9, 30)], "f4") for y in np.linspace(-3, 3, 40)
    ]
    nib.streamlines.save(nib.streamlines.Tractogram(lines, affine_to_rasmm=np.eye(4)), path)
    tract = import_artifact(
        ImportRequest(
            "tractogram",
            path,
            declarations={
                **ref.to_dict()["declarations"],
                "sift2_fod_sha256": hash_file(Path(fod.payload["path"])),
            },
        )
    )
    return dict(tracts=tract, fod=fod, reference=ref, five_tt=five), seed


def selection(items, **settings):
    return Task(
        "sift",
        "tcksift2",
        {k: a.id for k, a in items.items()},
        dict(act="on" if "five_tt" in items else "off", fd_scale_gm="off", **settings),
    )


def test_act_sift2_requires_exact_accepted_tissue_review(sift_inputs, tmp_path):
    items, _ = sift_inputs
    source = items["five_tt"]
    declarations = dict(source.payload["declarations"])
    declarations.pop("tissue_review")
    items["five_tt"] = import_artifact(
        ImportRequest("5tt", Path(source.payload["path"]), declarations=declarations)
    )
    with pytest.raises(ValueError, match="accepted, hash-bound 5TT"):
        prepare_sift2(
            tuple(items.values()),
            (selection(items),),
            tmp_path / "unreviewed-sift2",
            tool_directory=BIN,
        )


def test_registry_and_native_defaults():
    a = ALGORITHMS["tcksift2"]
    assert a.implementation == "implemented" and a.validated_versions == ("3.0.5",)
    assert a.outputs["weights"] == "streamline_weights"
    assert a.parameters["reg_tv"]["default"] is None
    assert "linear" not in a.parameters and "out_coeffs" not in a.parameters


@pytest.mark.parametrize(
    "values,count,error",
    [
        ([1, -1], 2, "Negative"),
        ([1, np.nan], 2, "nonfinite"),
        ([1, 2], 3, "count"),
        ([[1, 2], [3, 4]], 2, "column"),
        ([], 0, "column"),
    ],
)
def test_invalid_weights(tmp_path, values, count, error):
    p = tmp_path / "weights.txt"
    np.savetxt(p, values)
    with pytest.raises(ValueError, match=error):
        read_weights(p, count)


def test_zero_extreme_and_known_weighted_means(tmp_path):
    p = tmp_path / "weights.txt"
    np.savetxt(p, [0, 0.2, 1.8, 10000])
    a = read_weights(p, 4)
    s = weight_statistics(a)
    assert s["zero_percent"] == 25 and s["extreme_count"] == 1
    pairs = np.array([[1, 2], [2, 1], [2, 3], [2, 3], [0, 3], [1, 1]])
    w = np.array([0.2, 1.8, 0, 0, 5, 6])
    lengths = np.array([50, 150, 20, 30, 80, 90])
    out, occ = aggregate(pairs, lengths, 3, True, True, mean=True, weights=w)
    assert out[0, 1] == 140 and out[1, 0] == 140
    assert out[1, 2] == 0 and occ[1, 2] == 2  # Undefined weighted mean despite occupied edge.
    fa, _ = aggregate(
        pairs, np.array([0.2, 0.8, 0.5, 0.5, 0.1, 0.4]), 3, True, True, mean=True, weights=w
    )
    assert fa[0, 1] == pytest.approx(0.74)
    total, _ = aggregate(pairs, np.ones(6), 3, True, True, weights=w)
    assert total[0, 1] == 2 and total[1, 2] == 0 and total[0, 0] == 0


@pytest.mark.parametrize(
    "settings",
    [
        dict(min_factor=0.1, min_coeff=-1),
        dict(max_factor=2, max_coeff=2),
        dict(min_iters=20, max_iters=10),
        dict(fd_scale_gm="on"),
    ],
)
def test_invalid_parameter_combinations(sift_inputs, tmp_path, settings):
    items, _ = sift_inputs
    p = dict(act="on", fd_scale_gm="off")
    p.update(settings)
    t = Task("sift", "tcksift2", {k: a.id for k, a in items.items()}, p)
    with pytest.raises(ValueError):
        prepare_sift2(tuple(items.values()), (t,), tmp_path / "out", tool_directory=BIN)


@native
@pytest.mark.parametrize("mask_mode", ["act", "explicit", "none"])
def test_actual_sift2_and_independent_reference(sift_inputs, tmp_path, mask_mode):
    items, mask = sift_inputs
    if mask_mode != "act":
        items.pop("five_tt")
    if mask_mode == "explicit":
        items["proc_mask"] = mask
    before = {a.payload["path"]: hash_file(Path(a.payload["path"])) for a in items.values()}
    t = selection(items)
    plan = prepare_sift2(tuple(items.values()), (t,), tmp_path / "run", tool_directory=BIN)
    with pytest.raises(ValueError, match="Approve"):
        execute_plan(None, plan, approval="wrong")
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    record = result["validation"]["measurements"]
    a = read_weights(tmp_path / "run/sift-weights.txt", 40)
    assert record["statistics"]["count"] == 40 and record["mu"] > 0
    assert (
        record["scientific_acceptance"] == "unassigned" and not record["mu_applied_by_application"]
    )
    weight = result["validation"]["products"][0]["artifact"]
    verify_weight_binding(weight, items["tracts"])
    assert all(hash_file(Path(p)) == h for p, h in before.items())
    independent = tmp_path / "independent"
    independent.mkdir()
    command = plan.to_dict()["commands"][-1]
    argv = [v.replace(str(tmp_path / "run"), str(independent)) for v in command["argv"]]
    subprocess.run(argv, check=True, capture_output=True)
    np.testing.assert_allclose(
        read_weights(independent / "sift-weights.txt", 40), a, atol=2e-5, rtol=2e-5
    )
    assert float((independent / "sift-mu.txt").read_text()) == record["mu"]
    assert (
        convergence(independent / "sift-convergence.csv", {})["iterations"]
        == record["convergence"]["iterations"]
    )
    from dmri_studio.core.runs import inspect_run

    assert (
        inspect_run(tmp_path / "run/manifest.json")["products"][0]["kind"] == "streamline_weights"
    )


@native
def test_mutually_exclusive_masks_and_unknown_fod(sift_inputs, tmp_path):
    items, mask = sift_inputs
    items["proc_mask"] = mask
    with pytest.raises(ValueError, match="either ACT"):
        prepare_sift2(
            tuple(items.values()), (selection(items),), tmp_path / "out", tool_directory=BIN
        )
    items.pop("proc_mask")
    tract = items["tracts"]
    data = tract.to_dict()["request"]
    data["declarations"].pop("sift2_fod_sha256")
    items["tracts"] = import_artifact(ImportRequest(**data))
    with pytest.raises(ValueError, match="FOD association"):
        prepare_sift2(
            tuple(items.values()), (selection(items),), tmp_path / "out", tool_directory=BIN
        )


@native
@pytest.mark.parametrize("zero_edge", [False, True])
def test_native_weighted_connectomes(inputs, tmp_path, zero_edge):
    items = inputs
    w = bound_weights(
        Path(items["tracts"].payload["path"]).parent / "factors.txt",
        items["tracts"],
        [0, 0, 1, 1] if zero_edge else [0.2, 1.8, 0, 1],
    )
    items["weights"] = w
    metrics = [
        "count",
        "mean_length",
        "mean_fa",
        "sift2_weighted",
        "sift2_mean_length",
        "sift2_mean_fa",
    ]
    t = Task(
        "matrix",
        "tck2connectome",
        {k: a.id for k, a in items.items()},
        dict(metrics=metrics, assignment="end_voxels", symmetric="yes", zero_diagonal="no"),
    )
    plan = prepare_connectome(tuple(items.values()), (t,), tmp_path / "run", tool_directory=BIN)
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    b = load_bundle(tmp_path / "run/matrix-connectome.json")
    assert len(b["matrices"]) == 6
    assert read_matrix(b["matrices"]["sift2_weighted"]["path"])[0, 2] == (
        0 if zero_edge else pytest.approx(2, abs=1e-7, rel=0)
    )
    assert read_matrix(b["matrices"]["sift2_mean_fa"]["path"])[0, 2] == (
        0 if zero_edge else pytest.approx(0.5)
    )
    assert read_matrix(b["weight_validity"])[0, 2] == (0 if zero_edge else 1)
    assert read_matrix(b["occupancy"])[0, 2] == 2
    assert b["qc"]["sift2"]["application_mu_scaling"] is False
    for command in plan.to_dict()["commands"]:
        if command["kind"] != "matrix":
            continue
        target = tmp_path / "independent"
        target.mkdir(exist_ok=True)
        argv = [v.replace(str(tmp_path / "run"), str(target)) for v in command["argv"]]
        if "-scale_file" in argv:
            argv[argv.index("-scale_file") + 1] = str(tmp_path / "run/matrix-fa-per-streamline.txt")
        subprocess.run(argv, check=True, capture_output=True)
        np.testing.assert_allclose(
            read_matrix(argv[3]), read_matrix(command["argv"][3]), atol=0, rtol=0
        )


@native
def test_selected_edge_preserves_native_weight_order(inputs, tmp_path):
    items = inputs
    items.pop("fa")
    items["weights"] = bound_weights(
        Path(items["tracts"].payload["path"]).parent / "factors.txt",
        items["tracts"],
        [0.2, 1.8, 0, 1],
    )
    t = Task(
        "matrix",
        "tck2connectome",
        {k: a.id for k, a in items.items()},
        dict(
            metrics=["sift2_weighted"],
            assignment="end_voxels",
            symmetric="yes",
            zero_diagonal="yes",
        ),
    )
    plan = prepare_connectome(tuple(items.values()), (t,), tmp_path / "matrix", tool_directory=BIN)
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    bundle = import_artifact(
        ImportRequest(
            "connectome_matrix",
            tmp_path / "matrix/matrix-connectome.json",
            declarations=items["tracts"].to_dict()["declarations"],
        )
    )
    task = Task("edge", "connectome2tck", dict(connectome=bundle.id), dict(node_a=1, node_b=3))
    plan = prepare_connectome((bundle,), (task,), tmp_path / "edge", tool_directory=BIN)
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    p = result["validation"]["products"][0]
    record = json.loads(Path(p["weight_record"]).read_text())
    np.testing.assert_allclose(read_weights(record["weights"], 2), [0.2, 1.8], atol=1e-7, rtol=0)
    np.testing.assert_array_equal(np.loadtxt(record["subset_source_indices"]), [0, 1])
    assert record["tractogram_sha256"] == hash_file(Path(p["path"]))
    assert record["optimization_scope"].startswith("Weights estimated on original full")


def test_weight_source_order_and_tampering(inputs, tmp_path):
    tract = inputs["tracts"]
    w = bound_weights(tmp_path / "weights.txt", tract, [1, 2, 3, 4])
    assert verify_weight_binding(w, tract)["streamlines"] == 4
    path = Path(tract.payload["path"])
    old = path.read_bytes()
    lines = list(nib.streamlines.load(path).streamlines)
    lines.reverse()
    nib.streamlines.save(nib.streamlines.Tractogram(lines, affine_to_rasmm=np.eye(4)), path)
    other = import_artifact(
        ImportRequest("tractogram", path, declarations=tract.to_dict()["declarations"])
    )
    with pytest.raises(ValueError, match="exact source"):
        verify_weight_binding(w, other)
    path.write_bytes(old)
    side = Path(w.payload["declarations"]["weight_record"])
    record = json.loads(side.read_text())
    record["representation"] = "optimization_coefficients"
    side.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="identity record"):
        import_artifact(ImportRequest(**w.to_dict()["request"]))


def test_cancelled_preflight_and_iteration_cap(sift_inputs, tmp_path):
    items, _ = sift_inputs
    from dmri_studio.core.process import ProcessingCancelled

    with pytest.raises(ProcessingCancelled):
        prepare_sift2(
            tuple(items.values()),
            (selection(items),),
            tmp_path / "out",
            tool_directory=BIN,
            cancelled=lambda: True,
        )
    p = tmp_path / "csv"
    p.write_text(
        "Iteration,Cost_data,Cost_reg,Cost_total,Streamlines,Fixels_excluded,\n0,10,0,10,2,0,\n1,8,1,9,2,0,\n"
    )
    q = convergence(p, {"max_iters": 1})
    assert q["iteration_cap_reached"] and q["convergence"] == "requires_review"


@native
def test_actual_weighted_mean_denominator_and_distinct_lengths(inputs, tmp_path):
    items = inputs
    path = Path(items["tracts"].payload["path"])
    lines = list(nib.streamlines.load(path).streamlines)
    lines[1] = np.array([[8, 2, 2], [5, 4, 2], [2, 2, 2]], "f4")
    nib.streamlines.save(nib.streamlines.Tractogram(lines, affine_to_rasmm=np.eye(4)), path)
    items["tracts"] = import_artifact(ImportRequest(**items["tracts"].to_dict()["request"]))
    items.pop("fa")
    items["weights"] = bound_weights(path.parent / "factors.txt", items["tracts"], [1, 3, 0, 0])
    task = Task(
        "matrix",
        "tck2connectome",
        {k: a.id for k, a in items.items()},
        dict(
            metrics=["sift2_mean_length"],
            assignment="end_voxels",
            symmetric="yes",
            zero_diagonal="yes",
        ),
    )
    plan = prepare_connectome(
        tuple(items.values()), (task,), tmp_path / "matrix", tool_directory=BIN
    )
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    a = read_matrix(tmp_path / "matrix/matrix-sift2_mean_length.csv")
    expected = (6 + 3 * 2 * np.sqrt(13)) / 4
    assert a[0, 2] == pytest.approx(expected, abs=2e-6, rel=0)
    assert not np.isclose(a[0, 2], (6 + 3 * 2 * np.sqrt(13)) / 2)


@native
def test_cancel_actual_sift2_process_and_keep_diagnostics(sift_inputs, tmp_path):
    import os
    import re
    from threading import Event

    items, _ = sift_inputs
    task = selection(items, min_iters=10000, max_iters=10000)
    plan = prepare_sift2(tuple(items.values()), (task,), tmp_path / "cancel", tool_directory=BIN)
    cancel = Event()
    pid = []
    running = False

    def progress(s):
        nonlocal running
        if s.startswith("Running "):
            running = "/tcksift2 " in s
        if running and "Resource sample" in s:
            pid.append(int(re.search(r"PID (\d+)", s)[1]))
            cancel.set()

    result = execute_plan(
        None, plan, approval=plan.digest, cancelled=cancel.is_set, progress=progress
    )
    assert result["status"] == "cancelled" and result["inputs_unchanged"] is True
    assert pid and not result.get("outputs")
    for child in pid:
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)
    assert (tmp_path / "cancel/logs/sift.log").is_file()


@native
def test_invalid_native_optimization_keeps_failure(sift_inputs, tmp_path):
    items, _ = sift_inputs
    p = Path(items["fod"].payload["path"])
    im = nib.load(p)
    # Explicit invalid analytic negative distribution, not fabricated subject tissue.
    bad = np.zeros(im.shape, "f4")
    bad[..., 0] = -1
    out = p.with_name("negative-fod.nii")
    n = nib.Nifti1Image(bad, im.affine)
    n.header.set_xyzt_units("mm")
    n.set_qform(im.affine, 1)
    n.set_sform(im.affine, 1)
    nib.save(n, out)
    request = items["fod"].to_dict()["request"]
    request["path"] = str(out)
    items["fod"] = import_artifact(ImportRequest(**request))
    request = items["tracts"].to_dict()["request"]
    request["declarations"]["sift2_fod_sha256"] = hash_file(out)
    items["tracts"] = import_artifact(ImportRequest(**request))
    plan = prepare_sift2(
        tuple(items.values()), (selection(items),), tmp_path / "failed", tool_directory=BIN
    )
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "failed" and result["inputs_unchanged"] is True
    assert not result.get("outputs") and (tmp_path / "failed/logs/sift.log").is_file()


@native
def test_memory_preflight_blocks_large_working_set(sift_inputs, tmp_path, monkeypatch):
    """An available-memory check must fail before any output/process is created."""
    from types import SimpleNamespace

    import dmri_studio.core.scientific.sift2 as module

    items, _ = sift_inputs
    monkeypatch.setattr(module.psutil, "virtual_memory", lambda: SimpleNamespace(available=1024))
    output = tmp_path / "memory-blocked"
    with pytest.raises(ValueError, match="Insufficient available memory"):
        prepare_sift2(tuple(items.values()), (selection(items),), output, tool_directory=BIN)
    assert not output.exists()


@native
def test_fa_evidence_storage_budget_preserves_margin(inputs, tmp_path, monkeypatch):
    """Use actual native evidence size and reject even a one-byte headroom deficit."""
    from types import SimpleNamespace

    import dmri_studio.core.scientific.connectome as module

    items = inputs
    task = Task(
        "matrix",
        "tck2connectome",
        {k: a.id for k, a in items.items()},
        dict(metrics=["mean_fa"], assignment="end_voxels", symmetric="yes", zero_diagonal="yes"),
    )
    plan = prepare_connectome(
        tuple(items.values()), (task,), tmp_path / "budget", tool_directory=BIN
    )
    params = plan.payload["parameters"]
    assert params["storage_margin_bytes"] == 5 * 1024**3
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    actual_bytes = sum(p.stat().st_size for p in (tmp_path / "budget").rglob("*") if p.is_file())
    assert actual_bytes < params["estimated_working_bytes"]
    monkeypatch.setattr(
        module.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(
            free=params["estimated_working_bytes"] + params["storage_margin_bytes"] - 1
        ),
    )
    with pytest.raises(ValueError, match="5 GiB safety margin"):
        prepare_connectome(tuple(items.values()), (task,), tmp_path / "blocked", tool_directory=BIN)
    assert not (tmp_path / "blocked").exists()
