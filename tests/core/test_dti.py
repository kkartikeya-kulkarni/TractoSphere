"""Real bounded MRtrix phantoms and defensive DTI contract tests.

Scientific fit/metric production is always external MRtrix, while expected
phantom tensors and independent eigen algebra establish known-answer checks.
No preprocessing, restricted data or expensive jobs are used.
"""

import shutil
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.execution import execute_plan, hash_file
from dmri_studio.core.process import ProcessingCancelled
from dmri_studio.core.qc import load_catalog
from dmri_studio.core.runs import inspect_run
from dmri_studio.core.scientific import ImportRequest, OutputRef, Task, import_artifact
from dmri_studio.core.scientific.dti import TENSOR_DECLARATIONS, prepare_dti

BIN = Path("/usr/local/mrtrix3/bin")
if not (BIN / "dwi2tensor").is_file():
    executable = shutil.which("dwi2tensor")
    BIN = Path(executable).parent if executable else None
requires_mrtrix = pytest.mark.skipif(
    BIN is None,
    reason="Actual validated MRtrix installation required; no simulated scientific success",
)


def save(path, array, affine):
    """Write coded mm fixtures; production adapter never changes their bytes."""
    image = nib.Nifti1Image(np.asarray(array, dtype="f4"), affine)
    image.set_qform(affine, 1)
    image.set_sform(affine, 1)
    image.header.set_xyzt_units("mm")
    nib.save(image, path)
    return path


def phantom(tmp_path, *, noise=0, multishell=False, affine=None, negative=False):
    """Rotated anisotropic tensor and FSL-axis gradients with exact analytic signal."""
    tmp_path.mkdir(exist_ok=True)
    rng = np.random.default_rng(802)
    vectors = rng.normal(size=(30, 3))
    vectors /= np.linalg.norm(vectors, axis=1)[:, None]
    g = np.vstack(([0, 0, 0], vectors))
    b = np.array([0] + [1000] * 30)
    if multishell:
        g = np.vstack((g, vectors))
        b = np.r_[b, np.full(30, 2500)]
    if affine is None:
        angle = 0.23
        affine = np.array(
            [
                [2 * np.cos(angle), -2 * np.sin(angle), 0, 12],
                [2 * np.sin(angle), 2 * np.cos(angle), 0, -8],
                [0, 0, 2, 6],
                [0, 0, 0, 1.0],
            ]
        )
    rotation = affine[:3, :3] / np.linalg.norm(affine[:3, :3], axis=0)
    fsl = g.copy()
    if np.linalg.det(rotation) > 0:
        fsl[:, 0] *= -1
    world = fsl @ rotation.T
    tensor = np.array(
        [
            [0.0014, 0.0002, 0.0001],
            [0.0002, 0.0005, 0.00005],
            [0.0001, 0.00005, -0.0008 if negative else 0.0003],
        ]
    )
    signal = 1000 * np.exp(-b * np.einsum("ni,ij,nj->n", world, tensor, world))
    values = np.broadcast_to(signal, (4, 5, 6, len(b))).copy()
    values += rng.normal(0, noise, values.shape)
    path = save(tmp_path / "dwi.nii", values, affine)
    np.savetxt(tmp_path / "bval", b[None, :])
    np.savetxt(tmp_path / "bvec", g.T)
    a = import_artifact(
        ImportRequest(
            "dwi",
            path,
            tmp_path / "bval",
            tmp_path / "bvec",
            declarations={"processing_state": "preprocessed"},
        )
    )
    return a, tensor


def tasks(a, fit="OLS", iterations=None, mask=None, shells=None):
    """One reusable two-task DAG, equally callable from CLI, GUI and scheduling."""
    params = dict(shells=[0, 1000] if shells is None else shells, fit=fit)
    if iterations is not None:
        params["iterations"] = iterations
    inputs = {"dwi": a.id}
    metric_inputs = {"tensor": OutputRef("fit", "tensor")}
    if mask:
        inputs["mask"] = mask.id
        metric_inputs["mask"] = mask.id
    return (
        Task("fit", "dwi2tensor", inputs, params),
        Task(
            "metrics",
            "tensor2metric",
            metric_inputs,
            {"metrics": ["FA", "MD", "AD", "RD", "V1", "CL", "CP", "CS"]},
        ),
    )


@requires_mrtrix
@pytest.mark.parametrize("fit,iterations", [("OLS", None), ("WLS", None), ("IWLS", 2)])
def test_actual_phantom_known_metrics_and_provenance(tmp_path, fit, iterations):
    a, tensor = phantom(tmp_path / "source")
    before = {s["path"]: s["sha256"] for s in a.to_dict()["sources"]}
    plan = prepare_dti((a,), tasks(a, fit, iterations), tmp_path / "run", tool_directory=BIN)
    command = next(c["argv"] for c in plan.to_dict()["commands"] if c["step"] == "fit-fit")
    assert ("-ols" in command) == (fit == "OLS")
    assert command[command.index("-iter") + 1] == str(iterations or 0)
    with pytest.raises(ValueError, match="exact displayed"):
        execute_plan(None, plan, approval="not-approved")
    assert not (tmp_path / "run").exists()
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    assert result["inputs_unchanged"] and result["human_qc"] == "pending"
    actual = nib.load(tmp_path / "run" / "fit-tensor.nii.gz")
    np.testing.assert_allclose(actual.affine, a.payload["geometry"]["affine_mm"], atol=1e-5)
    wanted = tensor[(0, 1, 2, 0, 0, 1), (0, 1, 2, 1, 2, 2)]
    np.testing.assert_allclose(actual.get_fdata()[0, 0, 0], wanted, atol=2e-9, rtol=1e-5)
    eigen = np.linalg.eigvalsh(tensor)
    md = eigen.mean()
    fa = np.sqrt(1.5 * np.sum((eigen - md) ** 2) / np.sum(eigen**2))
    for metric, want in dict(FA=fa, MD=md, AD=eigen[2], RD=eigen[:2].mean()).items():
        value = nib.load(tmp_path / "run" / f"metrics-{metric}.nii.gz").get_fdata()
        np.testing.assert_allclose(value, want, atol=2e-7 if metric == "FA" else 2e-9, rtol=1e-5)
    vector_image = nib.load(tmp_path / "run" / "metrics-V1.nii.gz")
    assert vector_image.shape == (*actual.shape[:3], 1, 3)
    assert vector_image.header.get_intent()[0] == "vector"
    catalog = load_catalog(tmp_path / "run" / "qc-catalog.json")
    assert catalog.payload["stages"][0]["scientific_review"] == "required"
    assert inspect_run(tmp_path / "run" / "manifest.json")["dti_products"]
    assert all(hash_file(Path(p)) == h for p, h in before.items())
    assert all("sha256" in s for s in result["outputs"].values())
    with pytest.raises(ValueError, match="NEW"):
        prepare_dti((a,), tasks(a), tmp_path / "run", tool_directory=BIN)


@requires_mrtrix
def test_noisy_negative_eigenvalues_not_repaired(tmp_path):
    a, _ = phantom(tmp_path / "source", noise=1, negative=True)
    plan = prepare_dti((a,), tasks(a), tmp_path / "run", tool_directory=BIN)
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    assert result["validation"]["measurements"][0]["negative_eigenvalues"] > 0
    assert any("negative eigenvalues" in s for s in result["validation"]["findings"])
    assert nib.load(tmp_path / "run" / "metrics-FA.nii.gz").get_fdata().max() > 1


@requires_mrtrix
def test_multishell_explicit_mapping_mif_and_tensor_entry(tmp_path):
    a, _ = phantom(tmp_path / "source", multishell=True, noise=1)
    plan = prepare_dti((a,), tasks(a, "IWLS", 2), tmp_path / "first", tool_directory=BIN)
    assert plan.payload["bindings"][0]["sampling"]["selected_original_volume_indices"] == tuple(
        range(31)
    )
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    mif = import_artifact(ImportRequest("dwi", tmp_path / "first" / "fit-selected.mif"))
    other = prepare_dti((mif,), tasks(mif, "WLS"), tmp_path / "second", tool_directory=BIN)
    assert execute_plan(None, other, approval=other.digest)["status"] == "validated_pending_qc"
    tensor = import_artifact(
        ImportRequest(
            "tensor", tmp_path / "first" / "fit-tensor.nii.gz", declarations=TENSOR_DECLARATIONS
        )
    )
    metric = (Task("metrics", "tensor2metric", {"tensor": tensor.id}, {"metrics": ["FA", "MD"]}),)
    third = prepare_dti((tensor,), metric, tmp_path / "third", tool_directory=BIN)
    assert not any("dwi2tensor" in c["argv"][0] for c in third.to_dict()["commands"])
    assert execute_plan(None, third, approval=third.digest)["status"] == "validated_pending_qc"


@requires_mrtrix
def test_invalid_inputs_masks_params_and_cancellation(tmp_path):
    a, _ = phantom(tmp_path / "source")
    with pytest.raises(ValueError, match="b0|rank"):
        prepare_dti((a,), tasks(a, shells=[1000]), tmp_path / "no-b0", tool_directory=BIN)
    with pytest.raises(ValueError, match="range"):
        prepare_dti((a,), tasks(a, "IWLS", 11), tmp_path / "bad-iter", tool_directory=BIN)
    bad = save(tmp_path / "source" / "mask.nii", np.ones((4, 5, 6)), np.diag([3, 3, 3, 1]))
    mask = import_artifact(ImportRequest("mask", bad))
    with pytest.raises(ValueError, match="grid"):
        prepare_dti((a, mask), tasks(a, mask=mask), tmp_path / "bad-mask", tool_directory=BIN)
    with pytest.raises(ProcessingCancelled):
        prepare_dti((a,), tasks(a), tmp_path / "cancel", tool_directory=BIN, cancelled=lambda: True)
    plan = prepare_dti((a,), tasks(a), tmp_path / "run", tool_directory=BIN)
    calls = []

    def progress(text):
        calls.append(text)

    # Cancel after engine creation/first tool; manifests and source identity retained.
    result = execute_plan(
        None,
        plan,
        approval=plan.digest,
        cancelled=lambda: any(t.startswith("Running ") for t in calls),
        progress=progress,
    )
    assert result["status"] == "cancelled" and result["inputs_unchanged"]
    assert "outputs" not in result
    assert (tmp_path / "run" / "manifest.json").is_file()


@requires_mrtrix
def test_full_finite_scan_not_sample_only(tmp_path):
    a, _ = phantom(tmp_path / "source")
    obj = nib.load(a.payload["path"])
    data = obj.get_fdata().astype("f4")
    data[1, 1, 1, 20] = np.nan
    save(Path(a.payload["path"]), data, obj.affine)
    with pytest.raises(ValueError, match="Nonfinite|validation failed"):
        changed = import_artifact(ImportRequest(**a.to_dict()["request"]))
        prepare_dti((changed,), tasks(changed), tmp_path / "run", tool_directory=BIN)


@requires_mrtrix
def test_zero_signal_preflight_no_mask_repair_or_output(tmp_path):
    """The real subject's padded-mask failure now stops before tensor estimation."""
    a, _ = phantom(tmp_path / "source")
    image = nib.load(a.payload["path"])
    values = image.get_fdata().astype("f4")
    values[1, 1, 1, :] = 0
    save(Path(a.payload["path"]), values, image.affine)
    changed = import_artifact(ImportRequest(**a.to_dict()["request"]))
    with pytest.raises(ValueError, match="1 fitting-domain voxels have no positive"):
        prepare_dti((changed,), tasks(changed), tmp_path / "run", tool_directory=BIN)
    assert not (tmp_path / "run").exists()
    np.testing.assert_array_equal(
        nib.load(changed.payload["path"]).get_fdata()[1, 1, 1], np.zeros(31)
    )


@requires_mrtrix
def test_stale_plan_arbitrary_argv_and_missing_tools_refused(tmp_path):
    """Exact approval cannot authorize tampered commands, changed sources or missing tools."""
    from dmri_studio.core.execution import ExecutionPlan

    a, _ = phantom(tmp_path / "source")
    with pytest.raises(ValueError, match="not found"):
        prepare_dti((a,), tasks(a), tmp_path / "missing", tool_directory=tmp_path / "no-tools")
    plan = prepare_dti((a,), tasks(a), tmp_path / "run", tool_directory=BIN)
    data = plan.to_dict()
    data["commands"][0]["argv"] = ["arbitrary-executable"]
    tampered = ExecutionPlan(data)
    with pytest.raises(ValueError, match="changed"):
        execute_plan(None, tampered, approval=tampered.digest)
    assert not (tmp_path / "run").exists()
    with Path(a.payload["request"]["bval"]).open("a") as stream:
        stream.write(" 1000")
    with pytest.raises(ValueError):
        execute_plan(None, plan, approval=plan.digest)
    assert not (tmp_path / "run").exists()


@requires_mrtrix
def test_cli_plan_approve_and_output_integrity(tmp_path, capsys):
    """CLI uses the same saved independent inputs and exact plan digest as the GUI."""
    from dmri_studio.cli.main import main
    from dmri_studio.core.scientific.records import save_selection

    a, _ = phantom(tmp_path / "source")
    selection = tmp_path / "selection.json"
    save_selection(selection, (a,), tasks(a))
    command = [
        "scientific",
        "dti",
        "--selection",
        str(selection),
        "--output",
        str(tmp_path / "run"),
        "--mrtrix-directory",
        str(BIN),
    ]
    assert main([*command, "--plan", str(tmp_path / "plan.json")]) == 0
    output = capsys.readouterr().out
    digest = output.rsplit("Approval digest: ", 1)[1].strip()
    assert not (tmp_path / "run").exists()
    assert main([*command, "--approve-digest", digest]) == 0
    assert inspect_run(tmp_path / "run" / "manifest.json")["status"] == "validated_pending_qc"
    with (tmp_path / "run" / "metrics-FA.nii.gz").open("ab") as stream:
        stream.write(b"tampering")
    with pytest.raises(ValueError, match="evidence changed"):
        inspect_run(tmp_path / "run" / "manifest.json")


@requires_mrtrix
def test_corrupted_tensor_geometry_blocks_downstream_metrics(tmp_path, monkeypatch):
    """Fault-inject a generated tensor header: never alter scientific source data."""
    import re

    from dmri_studio.core.scientific.dti import DTIStep

    a, _ = phantom(tmp_path / "source")
    plan = prepare_dti((a,), tasks(a), tmp_path / "run", tool_directory=BIN)
    original = DTIStep.run

    def corrupt(self, **kwargs):
        """Run actual MRtrix, then simulate an output-integrity failure."""
        record = original(self, **kwargs)
        if self.name == "fit-fit":
            path = self.outputs[0]
            with path.open("r+b") as stream:
                header = stream.read(4096)
                found = re.search(rb"transform:[^\n]+,\s*([-+0-9.eE]+)", header)
                assert found is not None
                token = found.group(1)
                # Same-length change preserves valid file structure/payload but
                # moves the tensor to a different physical grid.
                index = next(i for i, c in enumerate(token) if 48 <= c <= 57)
                altered = (
                    token[:index]
                    + (b"9" if token[index : index + 1] != b"9" else b"8")
                    + token[index + 1 :]
                )
                stream.seek(found.start(1))
                stream.write(altered)
        return record

    monkeypatch.setattr(DTIStep, "run", corrupt)
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "failed" and result["inputs_unchanged"]
    assert result["steps"][-1]["step"] == "fit-fit"
    assert result["steps"][-1]["status"] == "verification_failed"
    assert not any("metrics" in s["step"] for s in result["steps"])
    assert "outputs" not in result
    assert (
        load_catalog(tmp_path / "run" / "failure-qc-catalog.json").payload["stages"][0]["technical"]
        == "failed"
    )


@requires_mrtrix
@pytest.mark.parametrize("product", ["predicted", "b0"])
def test_overflowed_prediction_or_baseline_withholds_derivatives(tmp_path, monkeypatch, product):
    """HCD overflow case: finite tensors alone must not promote a failed DWI fit."""
    import re

    from dmri_studio.core.scientific.dti import DTIStep

    a, _ = phantom(tmp_path / "source")
    plan = prepare_dti((a,), tasks(a), tmp_path / "run", tool_directory=BIN)
    original = DTIStep.run

    def overflow(self, **kwargs):
        """Actual MRtrix plus controlled output-overflow injection, no input edits."""
        record = original(self, **kwargs)
        if self.name == "fit-fit":
            path = next(p for p in self.outputs if p.name == f"fit-{product}.mif")
            with path.open("rb") as stream:
                header = stream.read(1_048_576)
            match = re.search(rb"file:\s+\.\s+(\d+)", header)
            assert match is not None
            values = np.memmap(path, mode="r+", offset=int(match[1]), dtype="<f4")
            values[0] = np.inf
            values.flush()
            del values
        return record

    monkeypatch.setattr(DTIStep, "run", overflow)
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "failed" and result["inputs_unchanged"]
    assert f"Nonfinite generated output fit-{product}.mif" in result["error"]
    assert result["steps"][-1]["status"] == "verification_failed"
    assert not any("metrics" in row["step"] for row in result["steps"])
    assert "outputs" not in result
    assert (tmp_path / "run" / "failure-qc-catalog.json").exists()
