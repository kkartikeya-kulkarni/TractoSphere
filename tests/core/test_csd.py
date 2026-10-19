"""Actual MRtrix CSD/response/normalization contracts with bounded phantoms.

No preprocessing or tracking. Scientific production is always MRtrix; the
synthetic DWI has known tissue mixtures, not fake output files. External tool
absence is an explicit optional-environment skip, never simulated success.
"""

import os
import shutil
import subprocess
from pathlib import Path
from threading import Event

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core._mif_checks import produced_mif
from dmri_studio.core.execution import execute_plan, hash_file
from dmri_studio.core.qc import load_catalog
from dmri_studio.core.scientific import ImportRequest, OutputRef, Task, import_artifact
from dmri_studio.core.scientific.csd import prepare_csd, sh_design
from dmri_studio.core.scientific.dti import _aligned
from dmri_studio.core.scientific.responses import read_response, response_rows

BIN = Path("/usr/local/mrtrix3/bin")
if not (BIN / "dwi2fod").is_file():
    found = shutil.which("dwi2fod")
    BIN = Path(found).parent if found else None
requires_mrtrix = pytest.mark.skipif(
    BIN is None, reason="Actual version-validated MRtrix required; no simulated scientific results"
)


def save(path, values):
    """Coded LAS millimetre fixture; never overwrite production research data."""
    im = nib.Nifti1Image(
        np.asarray(values, "f4"),
        np.array([[-2, 0, 0, 20], [0, 2, 0, -20], [0, 0, 2, -20], [0, 0, 0, 1.0]]),
    )
    im.set_qform(im.affine, 1)
    im.set_sform(im.affine, 1)
    im.header.set_xyzt_units("mm")
    nib.save(im, path)
    return path


def call(args):
    """Direct independent MRtrix command, not TractoSphere compilation."""
    env = dict(os.environ)
    env["PATH"] = str(BIN) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        list(map(str, args)), capture_output=True, text=True, env=env, timeout=120
    )
    assert result.returncode == 0, result.stderr


def mif(path):
    """Detach small fixture outputs while the validated reader context is open."""
    with produced_mif(path) as (_, aff, values):
        return aff.copy(), np.array(values)


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    """Three-shell tissue compartments plus crossing WM, with exact gradients."""
    root = tmp_path_factory.mktemp("CSD-inputs")
    n = 48
    i = np.arange(n)
    z = 1 - 2 * (i + 0.5) / n
    phi = i * np.pi * (3 - np.sqrt(5))
    dirs = np.c_[np.sqrt(1 - z * z) * np.cos(phi), np.sqrt(1 - z * z) * np.sin(phi), z]
    b = np.r_[0, 0, np.repeat([1000.0, 2000.0, 3000.0], n)]
    g = np.vstack((np.zeros((2, 3)), np.tile(dirs, (3, 1))))
    wm_x = 1000 * np.exp(-b * (0.0003 + 0.0014 * g[:, 0] ** 2))
    wm_y = 1000 * np.exp(-b * (0.0003 + 0.0014 * g[:, 1] ** 2))
    gm = 1000 * np.exp(-b * 0.0008)
    csf = 1000 * np.exp(-b * 0.003)
    a = np.zeros((20, 20, 20, len(b)), "f4")
    a[:7] = 0.94 * wm_x + 0.03 * gm + 0.03 * csf
    a[7:13] = 0.03 * wm_x + 0.94 * gm + 0.03 * csf
    a[13:] = 0.03 * wm_x + 0.03 * gm + 0.94 * csf
    a[:7, :, 10:] = 0.47 * wm_x + 0.47 * wm_y + 0.03 * gm + 0.03 * csf
    # Distribution-based response estimation needs nondegenerate within-tissue
    # variation. Pure constant tissue blocks give zero MAD/empty robust classes;
    # add bounded, seeded measurement variation rather than weaken validation.
    a *= np.linspace(0.97, 1.03, 20, dtype="f4")[None, :, None, None]
    a += np.random.default_rng(731).normal(0, 0.3, a.shape).astype("f4")
    path = save(root / "dwi.nii", a)
    np.savetxt(root / "bvals", b[None, :])
    np.savetxt(root / "bvecs", g.T)
    source = import_artifact(
        ImportRequest(
            "dwi",
            path,
            root / "bvals",
            root / "bvecs",
            declarations={"processing_state": "preprocessed", "space_id": "synthetic-CSD"},
            provenance="Analytic tensor-mixture DWI test fixture, no acquisition/preprocessing certification.",
        )
    )
    mask = import_artifact(
        ImportRequest(
            "mask",
            save(root / "mask.nii", np.ones(a.shape[:3])),
            declarations={"space_id": "synthetic-CSD"},
        )
    )
    return root, source, mask


def response_task(source, mask):
    """Explicit response recipe; no automatic shell selection."""
    return Task(
        "response",
        "dwi2response.dhollander",
        {"dwi": source.id, "mask": mask.id},
        {"shells": [0, 1000, 2000, 3000], "lmax": 4, "erode": 0},
    )


def run(artifacts, tasks, path):
    """Run only the exact explicitly approved fixture plan."""
    plan = prepare_csd(artifacts, tasks, path, tool_directory=BIN, nthreads=2)
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    assert result["inputs_unchanged"] and result["human_qc"] == "pending"
    return plan, result


def test_response_headers_tissues_rows_and_integrity(tmp_path):
    """Header metadata is authoritative; missing metadata is never guessed."""
    p = tmp_path / "response.txt"
    p.write_text("# Shells: 5,1000,2000\n100 0 0\n60 -20 4\n40 -18 6\n")
    before = hash_file(p)
    meta = read_response(p, "wm")
    assert meta["lmax"] == 4 and meta["shells"] == [5, 1000, 2000]
    assert response_rows(meta, [{"b_median": 1000, "is_b0": False}], "wm", 4, single=True) == [1]
    a = import_artifact(ImportRequest("response", p, declarations={"tissue": "wm"}))
    assert a.payload["metadata"]["columns"] == 3
    for tissue, shells in [("gm", None), ("wm", [0, 1000, 2000])]:
        with pytest.raises(ValueError):
            read_response(p, tissue, shells)
    with pytest.raises(ValueError, match="insufficient"):
        response_rows(meta, [{"b_median": 1000, "is_b0": False}], "wm", 6, single=True)
    assert hash_file(p) == before
    for text in ["1 nan\n", "0 -1\n", "1 2\n2\n", "# Shells: 0,0\n1\n2\n", "1 .2\n"]:
        q = tmp_path / "invalid.txt"
        q.write_text(text)
        with pytest.raises(ValueError):
            read_response(q, "wm")


def test_singleton_compartment_import(tmp_path):
    """Real MRtrix isotropic 4D/singleton storage is not a scalar time series."""
    path = save(tmp_path / "gm.nii", np.ones((3, 4, 5, 1)))
    a = import_artifact(
        ImportRequest(
            "tissue_scalar", path, declarations={"tissue": "gm", "normalization": "not_done"}
        )
    )
    assert a.payload["geometry"]["shape"] == (3, 4, 5, 1)
    with pytest.raises(ValueError, match="singleton"):
        import_artifact(
            ImportRequest("tissue_scalar", save(tmp_path / "invalid.nii", np.ones((3, 4, 5, 2))))
        )


@requires_mrtrix
def test_real_sh_basis_against_mrtrix_sh2amp(tmp_path):
    """QC sampling must use the real MRtrix SH convention, not decorative lobes."""
    g = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 2, 3], [-2, 3, 1], [2, -1, 3]], float)
    g /= np.linalg.norm(g, axis=1)[:, None]
    coef = np.eye(45, dtype="f4")[:, None, None, :]
    path = save(tmp_path / "basis.nii", coef)
    np.savetxt(tmp_path / "dirs", g)
    call([BIN / "sh2amp", path, tmp_path / "dirs", tmp_path / "amp.mif", "-nthreads", "2"])
    image = nib.load(path)
    actual = _aligned(
        tmp_path / "amp.mif", {"shape": list(image.shape), "affine_mm": image.affine.tolist()}
    )
    np.testing.assert_allclose(actual[:, 0, 0, :].T, sh_design(g, 8), atol=2e-6, rtol=2e-6)


@pytest.fixture(scope="module")
def estimated(sample, tmp_path_factory):
    """Actual Dhollander responses, reused across downstream fixture operations."""
    if BIN is None:
        pytest.skip("Actual MRtrix required")
    root, source, mask = sample
    dest = tmp_path_factory.mktemp("CSD-results") / "dhollander"
    plan, result = run((source, mask), (response_task(source, mask),), dest)
    artifacts = [
        import_artifact(
            ImportRequest(
                "response", dest / ("response-" + t + "-response.txt"), declarations={"tissue": t}
            )
        )
        for t in ["wm", "gm", "csf"]
    ]
    return dest, plan, result, tuple(artifacts)


@requires_mrtrix
def test_dhollander_estimation_and_independent_reference(sample, estimated, tmp_path):
    """Real script/helper closure, selected voxels, response values and hashes."""
    root, source, mask = sample
    dest, plan, result, responses = estimated
    assert plan.payload["script_hashes"] and "peaks2amp" in plan.payload["tools"]
    assert all(len(a.payload["metadata"]["shells"]) == 4 for a in responses)
    assert load_catalog(dest / "qc-catalog.json").to_dict()["stages"][0]["technical"] == "passed"
    outputs = [tmp_path / (t + ".txt") for t in ["wm", "gm", "csf"]]
    call(
        [
            plan.payload["script_python"],
            BIN / "dwi2response",
            "dhollander",
            source.payload["path"],
            *outputs,
            "-grad",
            dest / "response-selected.grad",
            "-mask",
            mask.payload["path"],
            "-lmax",
            "0,4,4,4",
            "-erode",
            "0",
            "-voxels",
            tmp_path / "voxels.mif",
            "-scratch",
            tmp_path,
            "-nthreads",
            "2",
            "-config",
            "BValueScaling",
            "false",
        ]
    )
    for t, p in zip(["wm", "gm", "csf"], outputs, strict=True):
        # Parallel Float64 spatial reductions plus 15-digit text serialization
        # need not be bitwise identical. This predeclared bound is far below
        # Float32 signal precision; compare selected masks exactly as well.
        np.testing.assert_allclose(
            np.loadtxt(dest / ("response-" + t + "-response.txt")),
            np.loadtxt(p),
            atol=1e-9,
            rtol=1e-12,
        )
    np.testing.assert_array_equal(
        mif(dest / "response-response-voxels.mif")[1], mif(tmp_path / "voxels.mif")[1]
    )


@requires_mrtrix
def test_single_tissue_real_csd_and_response_row_derivative(sample, estimated, tmp_path):
    """Exact selected-response values and true SH output, independent of preprocessing."""
    _, source, mask = sample
    _, _, _, responses = estimated
    wm = responses[0]
    task = Task(
        "csd",
        "dwi2fod.csd",
        {"dwi": source.id, "mask": mask.id, "wm_response": wm.id},
        {"shells": [3000], "lmax": 4},
    )
    before = hash_file(Path(wm.payload["path"]))
    dest = tmp_path / "single"
    plan, result = run((source, mask, wm), (task,), dest)
    assert result["validation"]["measurements"][0]["wm_fod"]["coefficients"] == 15
    selected = dest / "csd-wm-selected-response.txt"
    np.testing.assert_array_equal(
        np.loadtxt(selected), np.asarray(wm.payload["metadata"]["coefficients"])[3]
    )
    call(
        [
            BIN / "dwi2fod",
            "csd",
            dest / "csd-selected.mif",
            selected,
            tmp_path / "independent.mif",
            "-mask",
            mask.payload["path"],
            "-grad",
            dest / "csd-selected.grad",
            "-lmax",
            "4",
            "-nthreads",
            "2",
            "-config",
            "BValueScaling",
            "false",
        ]
    )
    np.testing.assert_array_equal(
        mif(dest / "csd-wm-fod.mif")[1], mif(tmp_path / "independent.mif")[1]
    )
    assert hash_file(Path(wm.payload["path"])) == before


@requires_mrtrix
@pytest.mark.parametrize("balanced", ["off", "on"])
def test_msmt_real_output_reference_optional_normalization(sample, estimated, tmp_path, balanced):
    """Imported responses, three real compartments, explicit separate normalizer."""
    _, source, mask = sample
    _, _, _, responses = estimated
    inputs = {
        "dwi": source.id,
        "mask": mask.id,
        **{t + "_response": a.id for t, a in zip(["wm", "gm", "csf"], responses, strict=True)},
    }
    task = Task(
        "msmt",
        "dwi2fod.msmt_csd",
        inputs,
        {"shells": [0, 1000, 2000, 3000], "lmax": 4, "predict_signal": "on"},
    )
    dest = tmp_path / "multi"
    plan, result = run((source, mask, *responses), (task,), dest)
    assert result["validation"]["measurements"][0]["residuals"]["normalized_RMSE"] < 0.2
    reference = [tmp_path / (t + ".mif") for t in ["wm", "gm", "csf"]]
    args = [BIN / "dwi2fod", "msmt_csd", source.payload["path"]]
    for a, path in zip(responses, reference, strict=True):
        args += [a.payload["path"], path]
    call(
        args
        + [
            "-mask",
            mask.payload["path"],
            "-grad",
            dest / "msmt-selected.grad",
            "-lmax",
            "4,0,0",
            "-nthreads",
            "2",
            "-config",
            "BValueScaling",
            "false",
        ]
    )
    for name, ref in zip(
        ["msmt-wm-fod.mif", "msmt-gm.mif", "msmt-csf.mif"], reference, strict=True
    ):
        np.testing.assert_array_equal(mif(dest / name)[1], mif(ref)[1])
    art = []
    for tissue, name in zip(
        ["wm", "gm", "csf"], ["msmt-wm-fod.mif", "msmt-gm.mif", "msmt-csf.mif"], strict=True
    ):
        decl = {"tissue": tissue, "normalization": "not_done", "space_id": "synthetic-CSD"}
        if tissue == "wm":
            decl.update(sh_basis="mrtrix3_real_symmetric", sh_frame="world_ras")
        art.append(
            import_artifact(
                ImportRequest(
                    "fod" if tissue == "wm" else "tissue_scalar", dest / name, declarations=decl
                )
            )
        )
    normal = Task(
        "norm",
        "mtnormalise",
        {"wm_fod": art[0].id, "gm": art[1].id, "csf": art[2].id, "mask": mask.id},
        {"order": 0, "balanced": balanced},
    )
    ndest = tmp_path / "normalized"
    _, normalized = run((*art, mask), (normal,), ndest)
    assert normalized["validation"]["measurements"][0]["normalization"]["inlier_voxels"] > 0
    nref = [tmp_path / ("norm-" + t + ".mif") for t in ["wm", "gm", "csf"]]
    args = [BIN / "mtnormalise"]
    for a, p in zip(art, nref, strict=True):
        args += [a.payload["path"], p]
    call(
        args
        + [
            "-mask",
            mask.payload["path"],
            *(["-balanced"] if balanced == "on" else []),
            "-order",
            "0",
            "-nthreads",
            "2",
            "-config",
            "BValueScaling",
            "false",
        ]
    )
    for role, p in zip(["wm_fod", "gm", "csf"], nref, strict=True):
        np.testing.assert_array_equal(
            mif(ndest / ("norm-" + role + "-normalized.mif"))[1], mif(p)[1]
        )
    repeat = Task(
        "again",
        "mtnormalise",
        {
            "wm_fod": OutputRef("norm", "wm_fod"),
            "gm": OutputRef("norm", "gm"),
            "csf": OutputRef("norm", "csf"),
            "mask": mask.id,
        },
        {"order": 0, "balanced": balanced},
    )
    with pytest.raises(ValueError, match="repeated normalization"):
        prepare_csd((*art, mask), (normal, repeat), tmp_path / "repeat", tool_directory=BIN)


@requires_mrtrix
@pytest.mark.parametrize("method", ["tournier", "tax"])
def test_other_real_response_methods(sample, tmp_path, method):
    """Single-tissue methods are real upstream algorithms, not three-tissue stubs."""
    _, source, mask = sample
    task = Task(
        "response",
        "dwi2response." + method,
        {"dwi": source.id, "mask": mask.id},
        {"shells": [3000], "lmax": 4, "max_iters": 3},
    )
    _, result = run((source, mask), (task,), tmp_path / method)
    assert set(result["validation"]["measurements"][0]["responses"]) == {"wm_response"}


@requires_mrtrix
def test_csd_approval_inputs_cancellation_and_partial_results(sample, estimated, tmp_path):
    """No arbitrary commands, source changes, overwrite or cancelled-success state."""
    _, source, mask = sample
    _, _, _, responses = estimated
    task = Task(
        "csd",
        "dwi2fod.csd",
        {"dwi": source.id, "mask": mask.id, "wm_response": responses[0].id},
        {"shells": [3000], "lmax": 4},
    )
    dest = tmp_path / "cancel"
    plan = prepare_csd((source, mask, *responses), (task,), dest, tool_directory=BIN, nthreads=2)
    with pytest.raises(ValueError, match="Approve"):
        execute_plan(None, plan, approval="incorrect")
    assert not dest.exists()
    cancel = Event()
    result = execute_plan(
        None,
        plan,
        approval=plan.digest,
        cancelled=cancel.is_set,
        progress=lambda s: cancel.set() if s.startswith("Running") else None,
    )
    assert (
        result["status"] == "cancelled" and not result.get("outputs") and result["inputs_unchanged"]
    )
    assert result["failure_evidence"] and load_catalog(dest / "failure-qc-catalog.json")
    with pytest.raises(ValueError, match="NEW"):
        prepare_csd((source, mask, *responses), (task,), dest, tool_directory=BIN)


@requires_mrtrix
def test_csd_invalid_parameters_masks_and_shells(sample, estimated, tmp_path):
    """Reject unsuitable angular design, responses and unavailable prerequisites."""
    _, source, mask = sample
    _, _, _, responses = estimated
    base = {"dwi": source.id, "mask": mask.id, "wm_response": responses[0].id}
    for params, match in [
        ({"shells": [1000, 2000], "lmax": 4}, "single-shell"),
        ({"shells": [0, 3000], "lmax": 4}, "omit b0"),
        ({"shells": [3000], "lmax": 5}, "even"),
        ({"shells": [3000], "lmax": 12}, "insufficient"),
    ]:
        with pytest.raises(ValueError, match=match):
            prepare_csd(
                (source, mask, *responses),
                (Task("bad", "dwi2fod.csd", base, params),),
                tmp_path / "bad",
                tool_directory=BIN,
            )
    task = Task("bad", "dwi2fod.msmt_csd", base, {"shells": [0, 1000, 2000, 3000], "lmax": 4})
    with pytest.raises(ValueError, match="gm_response"):
        prepare_csd((source, mask, *responses), (task,), tmp_path / "bad", tool_directory=BIN)
    # Isolate angular rank from response-order failure: an estimation task has
    # no existing RF prerequisite, but still cannot invent missing directions.
    angular = Task(
        "rank",
        "dwi2response.tournier",
        {"dwi": source.id, "mask": mask.id},
        {"shells": [3000], "lmax": 12},
    )
    with pytest.raises(ValueError, match="angular rank"):
        prepare_csd((source, mask), (angular,), tmp_path / "rank", tool_directory=BIN)


@requires_mrtrix
def test_csd_cli_and_typed_reopening(sample, estimated, tmp_path, capsys):
    """Preview cannot execute; approval and reopened products bind real bytes."""
    from dmri_studio.cli.main import main
    from dmri_studio.core.runs import inspect_run
    from dmri_studio.core.scientific.records import save_selection

    _, source, mask = sample
    _, _, _, responses = estimated
    task = Task(
        "ss",
        "dwi2fod.csd",
        {"dwi": source.id, "mask": mask.id, "wm_response": responses[0].id},
        {"shells": [3000], "lmax": 4},
    )
    selection = tmp_path / "selection.json"
    save_selection(selection, (source, mask, *responses), (task,))
    cmd = [
        "scientific",
        "csd",
        "--selection",
        str(selection),
        "--output",
        str(tmp_path / "attempt"),
        "--mrtrix-directory",
        str(BIN),
        "--threads",
        "2",
    ]
    assert main(cmd) == 0
    digest = capsys.readouterr().out.rsplit("Approval digest: ", 1)[1].strip()
    assert not (tmp_path / "attempt").exists()
    assert main([*cmd, "--approve-digest", digest]) == 0
    run = inspect_run(tmp_path / "attempt" / "manifest.json")
    assert run["scientific_acceptance"] == "pending"
    fod = next(p for p in run["products"] if p["kind"] == "fod")
    assert fod["normalization"] == "not_done"
    assert any(
        p["kind"] == "scalar" and "coefficient" in p["scalar_meaning"] for p in run["products"]
    )
    with Path(fod["path"]).open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="evidence changed"):
        inspect_run(tmp_path / "attempt" / "manifest.json")


def test_compartment_roundoff_is_reported_without_repair():
    """Tiny solver negatives are retained; material negatives remain findings."""
    from dmri_studio.core.scientific.csd_qc import compartment_statistics

    values = np.array([-0.25, -1e-12, 0, 0.5], dtype="f4")
    before = values.copy()
    result = compartment_statistics(values)
    assert result["negative_voxels"] == 2
    assert result["negative_beyond_roundoff_voxels"] == 1
    np.testing.assert_array_equal(values, before)


@requires_mrtrix
def test_response_upstream_internal_requirements_checked_before_execution(sample, tmp_path):
    """Tournier's two-iteration minimum is upstream semantics, not a fit default."""
    _, source, mask = sample
    bad = Task(
        "rf",
        "dwi2response.tournier",
        {"dwi": source.id, "mask": mask.id},
        {"shells": [3000], "lmax": 4, "max_iters": 1},
    )
    with pytest.raises(ValueError, match="max_iters"):
        prepare_csd((source, mask), (bad,), tmp_path / "invalid", tool_directory=BIN)
    assert not (tmp_path / "invalid").exists()


@requires_mrtrix
def test_response_script_cancellation_retires_helper_process_group(sample, tmp_path):
    """Cancel a real response Python script and its real MRtrix helper children."""
    from threading import Timer

    import psutil

    _, source, mask = sample
    target = tmp_path / "response-cancel"
    plan = prepare_csd(
        (source, mask), (response_task(source, mask),), target, tool_directory=BIN, nthreads=2
    )
    cancel = Event()
    timers = []

    def progress(text):
        if text.startswith("Resource sample") and not timers:
            timer = Timer(0.15, cancel.set)
            timers.append(timer)
            timer.start()

    result = execute_plan(
        None, plan, approval=plan.digest, cancelled=cancel.is_set, progress=progress
    )
    for timer in timers:
        timer.join()
    assert timers and result["status"] == "cancelled"
    assert result["inputs_unchanged"] and not result.get("outputs")
    assert (target / "failure-qc-catalog.json").is_file()
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            assert str(target) not in " ".join(process.info["cmdline"] or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


@requires_mrtrix
def test_lossless_response_layout_and_real_estimation(sample, tmp_path, monkeypatch):
    """Storage preparation preserves all source voxels/gradients, not a solver."""
    import dmri_studio.core.scientific.csd as csd
    from dmri_studio.core.scientific.mif_layout import prepare_response_layout

    _, source, mask = sample
    (tmp_path / "source").mkdir()
    path = tmp_path / "source" / "volume-major.mif"
    call(
        [
            BIN / "mrconvert",
            source.payload["path"],
            path,
            "-fslgrad",
            source.payload["request"]["bvec"],
            source.payload["request"]["bval"],
            "-strides",
            "1,2,3,4",
        ]
    )
    original = hash_file(path)
    copy = tmp_path / "channel-fast.mif"
    record = prepare_response_layout(path, copy)
    assert record["complete_payload_verified"] and record["maximum_buffer_bytes"] <= 128 * 1024**2
    with produced_mif(path) as (f, a, x), produced_mif(copy) as (g, b, y):
        assert f["dw_scheme"] == g["dw_scheme"]
        np.testing.assert_array_equal(a, b)
        np.testing.assert_array_equal(x, y)
        assert abs(y.strides[3]) == 4
    with pytest.raises(FileExistsError):
        prepare_response_layout(path, copy)
    art = import_artifact(
        ImportRequest(
            "dwi",
            path,
            declarations={"processing_state": "preprocessed", "space_id": "synthetic-CSD"},
        )
    )
    monkeypatch.setattr(csd, "RESPONSE_LAYOUT_THRESHOLD", 1)
    dest = tmp_path / "run-layout"
    plan, result = run((art, mask), (response_task(art, mask),), dest)
    assert plan.to_dict()["layout_copies"] and result["outputs"]["layout-preparation.json"]
    assert hash_file(path) == original
    # Independent command over the original storage; same scientific data.
    ref = tmp_path / "reference-layout"
    ref.mkdir()
    call(
        [
            BIN / "dwi2response",
            "dhollander",
            path,
            ref / "wm.txt",
            ref / "gm.txt",
            ref / "csf.txt",
            "-mask",
            mask.payload["path"],
            "-grad",
            dest / "response-selected.grad",
            "-config",
            "BValueScaling",
            "false",
            "-erode",
            "0",
            "-lmax",
            "0,4,4,4",
            "-nthreads",
            "2",
        ]
    )
    for tissue in ["wm", "gm", "csf"]:
        np.testing.assert_allclose(
            np.loadtxt(dest / ("response-" + tissue + "-response.txt")),
            np.loadtxt(ref / (tissue + ".txt")),
            atol=1e-9,
            rtol=1e-12,
        )


@requires_mrtrix
@pytest.mark.parametrize("before_creation", [False, True])
def test_cli_ctrl_c_uses_shared_cancellation_and_restores_signal(
    sample, tmp_path, capsys, monkeypatch, before_creation
):
    """A CLI interrupt is cancellation, never an abandoned running/success state."""
    import signal

    import dmri_studio.core.execution as execution
    from dmri_studio.cli.main import main
    from dmri_studio.core.scientific.records import save_selection

    _, source, mask = sample
    selection = tmp_path / "selection.json"
    save_selection(selection, (source, mask), (response_task(source, mask),))
    cmd = [
        "scientific",
        "csd",
        "--selection",
        str(selection),
        "--output",
        str(tmp_path / "attempt"),
        "--mrtrix-directory",
        str(BIN),
    ]
    assert main(cmd) == 0
    digest = capsys.readouterr().out.rsplit("Approval digest: ", 1)[1].strip()
    previous = signal.getsignal(signal.SIGINT)

    def approved_execution(dataset, plan, *, approval, progress, cancelled):
        assert approval == plan.digest and not cancelled()
        signal.raise_signal(signal.SIGINT)
        assert cancelled()
        if before_creation:
            from dmri_studio.core.process import ProcessingCancelled

            raise ProcessingCancelled("Cancelled during input revalidation")
        return {"status": "cancelled"}

    monkeypatch.setattr(execution, "execute_plan", approved_execution)
    assert main([*cmd, "--approve-digest", digest]) == 130
    assert signal.getsignal(signal.SIGINT) == previous
    assert not (tmp_path / "attempt").exists()
