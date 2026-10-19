"""Controlled contract fixtures, not evidence of reconstruction scientific validity."""

import json

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.cli.main import main
from dmri_studio.core.execution import execute_plan, hash_file, save_execution_plan
from dmri_studio.core.scientific import (
    ALGORITHMS,
    ImportRequest,
    OutputRef,
    Task,
    build_workflow,
    import_artifact,
)
from dmri_studio.core.scientific.records import restore_selection, save_selection


def image(tmp_path, name="image.nii", shape=(4, 5, 6), values=None, affine=None):
    """Preserve coded mm geometry in independently imported toy inputs."""
    affine = np.diag([2.0, 2.0, 2.0, 1.0]) if affine is None else affine
    obj = nib.Nifti1Image(
        np.ones(shape, dtype=np.float32)
        if values is None
        else np.asarray(values, dtype=np.float32),
        affine,
    )
    obj.set_qform(affine, 1)
    obj.set_sform(affine, 1)
    obj.header.set_xyzt_units("mm")
    path = tmp_path / name
    nib.save(obj, path)
    return path


def dwi(tmp_path, rank=True, state="preprocessed"):
    """Seven independently spanning tensor design measurements including b0."""
    vec = np.array(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 0], [1, 0, 1], [0, 1, 1]], dtype=float
    )
    vec[4:] /= np.sqrt(2)
    if not rank:
        vec[1:] = [1, 0, 0]
    bval, bvec = tmp_path / "bval", tmp_path / "bvec"
    np.savetxt(bval, [[0] + [1000] * 6])
    np.savetxt(bvec, vec.T)
    return import_artifact(
        ImportRequest(
            "dwi",
            image(tmp_path, shape=(4, 5, 6, 7)),
            bval,
            bvec,
            declarations={"processing_state": state},
        )
    )


def tensor(tmp_path, layout="mrtrix_D11_D22_D33_D12_D13_D23"):
    return import_artifact(
        ImportRequest(
            "tensor",
            image(tmp_path, shape=(4, 5, 6, 6)),
            declarations=dict(tensor_layout=layout, tensor_frame="world_ras", tensor_units="mm2/s"),
        )
    )


def messages(plan, severity=None):
    return "\n".join(
        i["message"]
        for i in plan.to_dict()["issues"]
        if severity is None or i["severity"] == severity
    )


def test_independent_tensor_and_immutable_sources(tmp_path):
    path = image(tmp_path, shape=(4, 5, 6, 6))
    before = hash_file(path)
    with pytest.raises(ValueError, match="component order"):
        import_artifact(ImportRequest("tensor", path))
    a = tensor(tmp_path)
    proposal = build_workflow(
        (a,), (Task("metrics", "tensor2metric", {"tensor": a.id}, {"metrics": ["FA", "MD"]}),)
    )
    assert proposal.payload["status"] == "review_required"
    assert not proposal.payload["execution_ready"] and not proposal.payload["commands"]
    assert hash_file(path) == before
    with pytest.raises(TypeError):
        a.payload["kind"] = "dwi"
    with pytest.raises(ValueError, match="review proposal"):
        execute_plan(None, proposal, approval=proposal.digest)


def test_dwi_gradient_validation_rank_shell_order(tmp_path):
    a = dwi(tmp_path)
    p = build_workflow(
        (a,), (Task("tensor", "dwi2tensor", {"dwi": a.id}, {"shells": [0, 1000], "fit": "OLS"}),)
    )
    assert p.payload["tasks"][0]["sampling"]["tensor_design_rank"] == 7
    assert tuple(p.payload["tasks"][0]["sampling"]["selected_original_volume_indices"]) == tuple(
        range(7)
    )
    assert p.payload["status"] == "review_required"
    np.savetxt(tmp_path / "bvec", np.zeros((3, 7)))
    with pytest.raises(ValueError, match="changed"):
        a.assert_current()
    with pytest.raises(ValueError, match="validation failed|Invalid gradients"):
        import_artifact(
            ImportRequest("dwi", tmp_path / "image.nii", tmp_path / "bval", tmp_path / "bvec")
        )


def test_degenerate_tensor_sampling_blocked(tmp_path):
    a = dwi(tmp_path, rank=False, state="raw")
    p = build_workflow(
        (a,), (Task("tensor", "dwi2tensor", {"dwi": a.id}, {"shells": [0, 1000], "fit": "OLS"}),)
    )
    assert "rank <7" in messages(p, "error")
    assert "Raw DWI" in messages(p, "review")


def test_task_graph_roundtrip_missing_cycles_outputs(tmp_path):
    a = tensor(tmp_path)
    first = Task("fit", "dwi2tensor", {}, {"shells": [0, 1000], "fit": "OLS"})
    second = Task(
        "metrics", "tensor2metric", {"tensor": OutputRef("fit", "tensor")}, {"metrics": ["FA"]}
    )
    p = build_workflow((a,), (second, first))
    assert [t["id"] for t in p.payload["tasks"]] == ["fit", "metrics"]
    assert "Missing required dwi" in messages(p, "error")
    assert Task.from_dict(second.to_dict()) == second
    with pytest.raises(ValueError, match="cycle"):
        build_workflow(
            (a,),
            (
                Task(
                    "x", "tensor2metric", {"tensor": OutputRef("x", "metrics")}, {"metrics": ["FA"]}
                ),
            ),
        )
    with pytest.raises(ValueError, match="missing producer"):
        build_workflow((a,), (second,))
    with pytest.raises(ValueError, match="Unknown producer output"):
        build_workflow(
            (a,),
            (
                first,
                Task(
                    "bad",
                    "tensor2metric",
                    {"tensor": OutputRef("fit", "fake")},
                    {"metrics": ["FA"]},
                ),
            ),
        )


def test_wrong_types_parameters_version_dependency(tmp_path):
    a = tensor(tmp_path, "fsl_Dxx_Dxy_Dxz_Dyy_Dyz_Dzz")
    p = build_workflow(
        (a,),
        (
            Task(
                "metrics",
                "tensor2metric",
                {"tensor": a.id},
                {"metrics": ["FA"], "shell_argument": "evil"},
            ),
        ),
        dependency_evidence={},
    )
    assert "no FSL conversion" in messages(p, "error")
    assert "Missing inspected dependency" in messages(p, "error")
    assert "Unsupported parameter" in messages(p, "error")
    accepted = {
        "registration.reference",
        "registration.qc",
        "registration.flirt",
        "registration.epi_reg",
        "registration.fnirt",
        "registration.invert",
        "registration.compose",
        "registration.convert",
        "registration.apply.scalar",
        "registration.apply.labels",
        "registration.apply.roi",
        "registration.apply.probability",
        "tcksift2",
        "dwi2tensor",
        "tensor2metric",
        "dwi2response.dhollander",
        "dwi2response.tournier",
        "dwi2response.tax",
        "dwi2fod.csd",
        "dwi2fod.msmt_csd",
        "mtnormalise",
        "tckgen.ifod2",
        "tckgen.sdstream",
        "tckgen.ifod1",
        "tckgen.tensor_det",
        "tckgen.tensor_prob",
        "tckedit",
        "tckmap",
        "tck2connectome",
        "connectome2tck",
        "5ttgen.fsl",
        "5ttcheck",
        "5tt2vis",
        "5tt2gmwmi",
        "tckgen.act_ifod2",
        "tckgen.act_ifod1",
        "tckgen.act_sdstream",
    }
    assert {
        spec.id for spec in ALGORITHMS.values() if spec.implementation == "implemented"
    } == accepted
    from dmri_studio.core.scientific.adapters import ACCEPTED_ADAPTERS

    assert set(ACCEPTED_ADAPTERS) == accepted
    assert all(
        spec.validated_versions
        == (("FSL 6.0.7.18", "MRtrix3 3.0.5") if spec.stage == "registration" else ("3.0.5",))
        and spec.adapter
        for spec in ALGORITHMS.values()
        if spec.id in accepted
    )
    assert all(
        spec.implementation == "planned" and not spec.validated_versions and spec.adapter is None
        for spec in ALGORITHMS.values()
        if spec.id not in accepted
    )
    assert all(
        ALGORITHMS[name].implementation == "implemented"
        and ALGORITHMS[name].validated_versions == ("3.0.5",)
        for name in ("dwi2tensor", "tensor2metric")
    )


def test_geometry_space_and_probability_distinction(tmp_path):
    values = np.zeros((4, 5, 6))
    values[:2] = 3
    values[2:] = 7
    a = import_artifact(ImportRequest("atlas_labels", image(tmp_path, values=values)))
    assert a.payload["metadata"]["region_names"]["3"] == "Region 3"
    assert a.payload["metadata"]["region_volumes_mm3"]["3"] == pytest.approx(480)
    with pytest.raises(ValueError, match="integer"):
        import_artifact(
            ImportRequest("atlas_labels", image(tmp_path, "continuous.nii", values=values / 10))
        )
    path = image(tmp_path, "prob.nii", shape=(4, 5, 6, 2), values=np.ones((4, 5, 6, 2)) * 0.4)
    with pytest.raises(ValueError, match="threshold"):
        import_artifact(ImportRequest("atlas_probability", path))
    prob = import_artifact(
        ImportRequest("atlas_probability", path, declarations={"threshold": 0.5})
    )
    assert not prob.payload["verification"]["label_ids"]
    t = tensor(tmp_path)
    bad = import_artifact(ImportRequest("mask", image(tmp_path, "mask.nii", affine=np.eye(4))))
    p = build_workflow(
        (t, bad),
        (Task("metrics", "tensor2metric", {"tensor": t.id, "mask": bad.id}, {"metrics": ["FA"]}),),
    )
    assert "reference grid" in messages(p, "error")


def test_fod_five_tt_and_integrity(tmp_path):
    path = image(tmp_path, shape=(4, 5, 6, 6))
    decl = dict(sh_basis="mrtrix3_real_symmetric", sh_frame="world_ras", tissue="wm")
    a = import_artifact(ImportRequest("fod", path, declarations=decl))
    assert a.payload["metadata"]["lmax"] == 2
    mask = import_artifact(ImportRequest("mask", image(tmp_path, "mask.nii")))
    p = build_workflow(
        (a, mask),
        (
            Task(
                "track",
                "tckgen.ifod2",
                {"fod": a.id, "seed_mask": mask.id, "mask": mask.id},
                {
                    "select": 100,
                    "seeds": 1000,
                    "seed_method": "image",
                    "step_mm": 0.5,
                    "cutoff": 0.1,
                    "angle_deg": 45,
                    "seed_cutoff": 0.1,
                    "min_length_mm": 5,
                    "max_length_mm": 100,
                    "max_attempts_per_seed": 20,
                },
            ),
        ),
    )
    assert p.payload["status"] == "review_required"  # No DWI/preprocessing dependency.
    five = np.ones((4, 5, 6, 5), dtype=np.float32) * 0.2
    five_path = image(tmp_path, "5tt.nii", values=five)
    import_artifact(
        ImportRequest(
            "5tt",
            five_path,
            declarations={"channels": "cortical_gm,subcortical_gm,wm,csf,pathology"},
        )
    )
    five[:] = 0.4
    image(tmp_path, "5tt.nii", values=five)
    with pytest.raises(ValueError, match="sum to 1"):
        import_artifact(
            ImportRequest(
                "5tt",
                five_path,
                declarations={"channels": "cortical_gm,subcortical_gm,wm,csf,pathology"},
            )
        )
    values = np.ones((4, 5, 6))
    values[0, 0, 0] = np.nan
    with pytest.raises(ValueError, match="Nonfinite"):
        import_artifact(ImportRequest("anatomy", image(tmp_path, "nan.nii", values=values)))


@pytest.mark.parametrize("extension", ["tck", "trk"])
def test_independent_tract_import_connectome_and_weights(tmp_path, extension):
    path = tmp_path / f"fiber.{extension}"
    tracts = nib.streamlines.Tractogram(
        [np.array([[0, 0, 0], [2, 2, 2]], dtype=np.float32)], affine_to_rasmm=np.eye(4)
    )
    nib.streamlines.save(tracts, path)
    a = import_artifact(ImportRequest("tractogram", path, declarations={"space_id": "subjectA"}))
    anatomy = import_artifact(
        ImportRequest("anatomy", image(tmp_path), declarations={"space_id": "subjectA"})
    )
    nodes = import_artifact(
        ImportRequest(
            "atlas_labels", image(tmp_path, "nodes.nii"), declarations={"space_id": "MNI"}
        )
    )
    weights_path = tmp_path / "weights.txt"
    weights_path.write_text("1\n")
    weights = import_artifact(ImportRequest("streamline_weights", weights_path))
    p = build_workflow(
        (a, anatomy, nodes, weights),
        (
            Task(
                "connect",
                "tck2connectome",
                {"tracts": a.id, "reference": anatomy.id, "nodes": nodes.id, "weights": weights.id},
                {"assignment": "end_voxels", "symmetric": "yes", "zero_diagonal": "yes"},
            ),
        ),
    )
    assert a.payload["metadata"]["streamlines"] == 1
    assert "coordinate-space identities disagree" in messages(p, "error")
    assert "Weights require exact tractogram identity/order association" in messages(p, "error")


def test_selection_and_proposal_exports_exclusive_revalidate(tmp_path):
    a = tensor(tmp_path)
    task = Task("metrics", "tensor2metric", {"tensor": a.id}, {"metrics": ["MD"]})
    path = tmp_path / "selection.json"
    save_selection(path, (a,), (task,))
    restored, tasks = restore_selection(path)
    assert restored[0].id == a.id and tasks == (task,)
    with pytest.raises(FileExistsError):
        save_selection(path, (a,), (task,))
    proposal = build_workflow(restored, tasks)
    dest = tmp_path / "proposal.json"
    save_execution_plan(proposal, dest)
    assert json.loads(dest.read_text())["approval_digest"] == proposal.digest
    with pytest.raises(FileExistsError):
        save_execution_plan(proposal, dest)
    image(tmp_path, values=np.ones((4, 5, 6, 6)) * 2)
    with pytest.raises(ValueError, match="changed"):
        restore_selection(path)


def test_cli_registry_and_independent_import(tmp_path, capsys):
    assert main(["scientific", "registry"]) == 0
    registry = json.loads(capsys.readouterr().out)
    assert len(registry) == 37 and all(a["implementation"] == "implemented" for a in registry)
    assert next(a for a in registry if a["id"] == "tcksift2")["validated_versions"] == ["3.0.5"]
    path = image(tmp_path)
    selection = tmp_path / "selection.json"
    assert (
        main(
            [
                "scientific",
                "import",
                "--kind",
                "anatomy",
                "--file",
                str(path),
                "--selection",
                str(selection),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "scientific",
                "plan",
                "--selection",
                str(selection),
                "--proposal",
                str(tmp_path / "proposal.json"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "proposal.json").exists()


def mif(tmp_path, scheme=True, truncated=False, transform=True):
    """Write raw Float32 MIF with known scanner RAS vectors; no MRtrix call."""
    shape = (4, 5, 6, 4)
    rows = [
        "mrtrix image",
        "dim: 4,5,6,4",
        "vox: 2,2,2,1",
        "layout: +0,+1,+2,+3",
        "datatype: Float32LE",
    ]
    if transform:
        rows += ["transform: 1,0,0,0", "transform: 0,1,0,0", "transform: 0,0,1,0"]
    if scheme:
        rows += [
            "dw_scheme: 0,0,0,0",
            "dw_scheme: 1,0,0,1000",
            "dw_scheme: 0,1,0,1000",
            "dw_scheme: 0,0,1,2000",
        ]
    header = ("\n".join(rows + ["file: . 1024", "END", ""])).encode()
    path = tmp_path / "raw.mif"
    payload = np.arange(np.prod(shape), dtype="<f4").tobytes()
    path.write_bytes(
        header + b"\0" * (1024 - len(header)) + payload[:-4]
        if truncated
        else header + b"\0" * (1024 - len(header)) + payload
    )
    return path


def test_embedded_mif_gradient_frame_order_and_invalid_payload(tmp_path):
    path = mif(tmp_path)
    before = hash_file(path)
    a = import_artifact(ImportRequest("dwi", path))
    assert a.payload["gradients"]["convention"] == "mrtrix_world_ras"
    assert tuple(a.payload["gradients"]["bvals"]) == (0.0, 1000.0, 1000.0, 2000.0)
    assert a.payload["geometry"]["affine_mm"][0][0] == 2
    assert a.payload["verification"]["maximum"] == 479
    assert hash_file(path) == before
    mif(tmp_path, scheme=False)
    with pytest.raises(ValueError, match="dw_scheme"):
        import_artifact(ImportRequest("dwi", path))
    mif(tmp_path, truncated=True)
    with pytest.raises(ValueError, match="payload size"):
        import_artifact(ImportRequest("dwi", path))


def test_response_rows_and_single_vs_multi_shell_contract(tmp_path):
    a = dwi(tmp_path)
    mask = import_artifact(ImportRequest("mask", image(tmp_path, "mask.nii")))
    response_path = tmp_path / "response.txt"
    response_path.write_text("1 .2\n")
    response = import_artifact(
        ImportRequest("response", response_path, declarations={"tissue": "wm", "shells": [1000]})
    )
    params = {"shells": [0, 1000], "lmax": 2}
    task = Task(
        "csd", "dwi2fod.csd", {"dwi": a.id, "mask": mask.id, "wm_response": response.id}, params
    )
    p = build_workflow((a, mask, response), (task,))
    assert not [m for m in p.payload["issues"] if m["severity"] == "error"]
    multi = Task(
        "msmt",
        "dwi2fod.msmt_csd",
        {"dwi": a.id, "mask": mask.id, "wm_response": response.id},
        params,
    )
    p = build_workflow((a, mask, response), (multi,))
    assert "at least two weighted groups" in messages(p, "error")
    assert "Missing required gm_response" in messages(p, "error")
    with pytest.raises(ValueError, match="finite, nonnegative"):
        import_artifact(
            ImportRequest("response", response_path, declarations={"tissue": "wm", "shells": [-1]})
        )


def test_cancel_and_invalid_numeric_parameters(tmp_path):
    from dmri_studio.core.process import ProcessingCancelled

    path = image(tmp_path)
    with pytest.raises(ProcessingCancelled):
        import_artifact(ImportRequest("anatomy", path), cancelled=lambda: True)
    a = dwi(tmp_path)
    p = build_workflow(
        (a,), (Task("bad", "dwi2tensor", {"dwi": a.id}, {"shells": 1000, "fit": "OLS"}),)
    )
    assert p.payload["status"] == "blocked"
    p = build_workflow(
        (a,), (Task("bad", "dwi2tensor", {"dwi": a.id}, {"shells": [1000], "fit": "invalid"}),)
    )
    assert "Unsupported fit choice" in messages(p, "error")


def test_fod_repeat_normalization_missing_five_tt(tmp_path):
    path = image(tmp_path, shape=(4, 5, 6, 6))
    a = import_artifact(
        ImportRequest(
            "fod",
            path,
            declarations=dict(
                sh_basis="mrtrix3_real_symmetric",
                sh_frame="world_ras",
                tissue="wm",
                normalization="completed",
            ),
        )
    )
    p = build_workflow(
        (a,), (Task("norm", "mtnormalise", {"wm_fod": a.id}, {"order": 3, "balanced": "off"}),)
    )
    assert "repeated normalization" in messages(p, "error")
    p = build_workflow(
        (a,),
        (
            Task(
                "act",
                "tckgen.act_ifod2",
                {"fod": a.id},
                {"select": 2, "step_mm": 1, "cutoff": 0.1, "angle_deg": 30},
            ),
        ),
    )
    assert "Missing required five_tt" in messages(p, "error")


def test_qform_conflict_unknown_units_and_changed_label_table(tmp_path):
    path = image(tmp_path)
    loaded = nib.load(path)
    loaded.set_qform(np.diag([3.0, 3.0, 3.0, 1.0]), 1)
    nib.save(loaded, path)
    with pytest.raises(ValueError, match="Conflicting qform"):
        import_artifact(ImportRequest("anatomy", path))
    path = image(tmp_path)
    loaded = nib.load(path)
    loaded.header.set_xyzt_units("unknown")
    nib.save(loaded, path)
    with pytest.raises(ValueError, match="documented spatial units"):
        import_artifact(ImportRequest("anatomy", path))
    path = image(tmp_path)
    table = tmp_path / "image.tsv"
    table.write_text("index\tname\n1\tA real table name\n")
    a = import_artifact(ImportRequest("atlas_labels", path))
    assert a.payload["metadata"]["region_names"]["1"] == "A real table name"
    table.write_text("index\tname\n1\tChanged name\n")
    with pytest.raises(ValueError, match="changed"):
        a.assert_current()


def test_nonfinite_duplicate_configuration_and_explicit_fit_policy(tmp_path):
    from dmri_studio.core.scientific.records import read_json

    for text in ('{"fit":"OLS","fit":"WLS_IWLS"}', '{"step":NaN}'):
        with pytest.raises(ValueError):
            read_json(text)
    with pytest.raises(ValueError, match="finite"):
        Task("bad", "dwi2tensor", {}, {"shells": [float("nan")]})
    with pytest.raises(ValueError, match="finite"):
        ImportRequest("anatomy", tmp_path / "x", declarations={"a": float("inf")})
    a = dwi(tmp_path)
    p = build_workflow(
        (a,), (Task("fit", "dwi2tensor", {"dwi": a.id}, {"shells": [0, 1000], "fit": "WLS_IWLS"}),)
    )
    assert "explicit iteration count" in messages(p, "error")
    p = build_workflow(
        (a,),
        (
            Task(
                "fit",
                "dwi2tensor",
                {"dwi": a.id},
                {"shells": [0, 1000], "fit": "WLS_IWLS", "iterations": 2},
            ),
        ),
    )
    assert not [i for i in p.payload["issues"] if i["severity"] == "error"]


def test_cli_task_proposal_and_shared_version_probe(tmp_path, monkeypatch, capsys):
    a = tensor(tmp_path)
    task = Task("metrics", "tensor2metric", {"tensor": a.id}, {"metrics": ["FA"]})
    selection = tmp_path / "selection.json"
    save_selection(selection, (a,), (task,))
    from dmri_studio.cli import scientific

    calls = []

    def probe(**kwargs):
        calls.append(kwargs)
        return {
            "tensor2metric": {
                "path": "/mock/bin/tensor2metric",
                "version": "3.0.5",
                "sha256": "mock-test-evidence",
            }
        }

    monkeypatch.setattr(scientific, "detect_mrtrix", probe)
    destination = tmp_path / "proposal.json"
    assert (
        main(
            [
                "scientific",
                "plan",
                "--selection",
                str(selection),
                "--proposal",
                str(destination),
                "--probe-tools",
                "--mrtrix-directory",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert calls[0]["names"] == ("tensor2metric",) and calls[0]["tool_directory"] == tmp_path
    payload = json.loads(destination.read_text())["plan"]
    assert not payload["execution_ready"] and not payload["commands"]
    assert "execution unavailable" in capsys.readouterr().out
    anatomy = image(tmp_path, "anatomy.nii")
    appended = tmp_path / "appended.json"
    assert (
        main(
            [
                "scientific",
                "import",
                "--kind",
                "anatomy",
                "--file",
                str(anatomy),
                "--from-selection",
                str(selection),
                "--selection",
                str(appended),
            ]
        )
        == 0
    )
    artifacts, tasks = restore_selection(appended)
    assert len(artifacts) == 2 and tasks == (task,)


def test_core_has_no_gui_or_optional_scipy_dependency(tmp_path):
    import os
    import subprocess
    import sys

    code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'scipy','PySide6','vtk','vtkmodules'}:
        raise ImportError('Optional dependency must not be required for scientific planning: '+name)
    return original(name,*args,**kwargs)
builtins.__import__ = guarded
from dmri_studio.core.scientific import ALGORITHMS, Task
assert len(ALGORITHMS) == 37
from dmri_studio.core.scientific.anatomy import prepare_anatomy
from dmri_studio.core.scientific.registration import prepare_registration
from dmri_studio.core.scientific.act_qc import endpoint_qc
from dmri_studio.core.scientific.tracking import prepare_tracking
from dmri_studio.core.scientific.csd import prepare_csd, sh_design
assert sh_design([[1,0,0]],2).shape == (1,6)
Task('metric','tensor2metric',{}, {'metrics':['FA']})
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_three_tissue_csf_response_and_typed_expected_outputs(tmp_path):
    # A controlled two-shell sampling layout, not an actual reconstructed FOD.
    first = dwi(tmp_path)
    values = np.array(first.payload["gradients"]["bvecs"])
    vectors = np.vstack([values, values[1:]])
    bval, bvec = tmp_path / "multi.bval", tmp_path / "multi.bvec"
    np.savetxt(bval, [[0] + [1000] * 6 + [2000] * 6])
    np.savetxt(bvec, vectors.T)
    multi = import_artifact(
        ImportRequest("dwi", image(tmp_path, "multi.nii", shape=(4, 5, 6, 13)), bval, bvec)
    )
    mask = import_artifact(ImportRequest("mask", image(tmp_path, "mask.nii")))
    responses = {}
    for tissue in ("wm", "gm", "csf"):
        path = tmp_path / f"{tissue}.txt"
        path.write_text("1 .2\n.8 .1\n.5 .1\n" if tissue == "wm" else "1\n.8\n.5\n")
        responses[tissue] = import_artifact(
            ImportRequest(
                "response", path, declarations={"tissue": tissue, "shells": [0, 1000, 2000]}
            )
        )
    bindings = {
        "dwi": multi.id,
        "mask": mask.id,
        **{name + "_response": value.id for name, value in responses.items()},
    }
    p = build_workflow(
        (multi, mask, *responses.values()),
        (Task("msmt", "dwi2fod.msmt_csd", bindings, {"shells": [0, 1000, 2000], "lmax": 2}),),
    )
    assert p.payload["status"] == "review_required" and not [
        i for i in p.payload["issues"] if i["severity"] == "error"
    ]
    generator = Task(
        "responses",
        "dwi2response.dhollander",
        {"dwi": multi.id, "mask": mask.id},
        {"shells": [0, 1000, 2000], "lmax": 2},
    )
    p = build_workflow((multi, mask), (generator,))
    output = p.payload["tasks"][0]["expected_outputs"]["csf_response"]
    assert output["declarations"]["tissue"] == "csf" and output["spatial_grid"] is None
    tensor = Task("tensor", "dwi2tensor", {"dwi": multi.id}, {"shells": [0, 1000], "fit": "OLS"})
    p = build_workflow((multi,), (tensor,))
    assert tuple(p.payload["tasks"][0]["expected_outputs"]["tensor"]["spatial_grid"]["shape"]) == (
        4,
        5,
        6,
    )


def test_expected_normalized_tissues_do_not_inherit_wm_identity(tmp_path):
    path = image(tmp_path, shape=(4, 5, 6, 6))
    wm = import_artifact(
        ImportRequest(
            "fod",
            path,
            declarations=dict(
                sh_basis="mrtrix3_real_symmetric",
                sh_frame="world_ras",
                tissue="wm",
                normalization="not_done",
            ),
        )
    )
    gm = import_artifact(ImportRequest("tissue_scalar", image(tmp_path, "gm.nii")))
    csf = import_artifact(ImportRequest("tissue_scalar", image(tmp_path, "csf.nii")))
    mask = import_artifact(ImportRequest("mask", image(tmp_path, "mask.nii")))
    p = build_workflow(
        (wm, gm, csf, mask),
        (
            Task(
                "normalize",
                "mtnormalise",
                {"wm_fod": wm.id, "gm": gm.id, "csf": csf.id, "mask": mask.id},
                {"order": 3, "balanced": "off"},
            ),
        ),
    )
    outputs = p.payload["tasks"][0]["expected_outputs"]
    for key, tissue in (("wm_fod", "wm"), ("gm", "gm"), ("csf", "csf")):
        assert outputs[key]["declarations"]["tissue"] == tissue
        assert outputs[key]["declarations"]["normalization"] == "completed"
        assert "tensor_layout" not in outputs[key]["declarations"]


def test_cli_failed_version_probe_is_actionable_and_does_not_export(tmp_path, monkeypatch, capsys):
    import subprocess

    from dmri_studio.cli import scientific

    a = tensor(tmp_path)
    selection = tmp_path / "selection.json"
    save_selection(
        selection, (a,), (Task("metric", "tensor2metric", {"tensor": a.id}, {"metrics": ["FA"]}),)
    )

    def failed(**kwargs):
        raise subprocess.TimeoutExpired(["tensor2metric", "-version"], 10)

    monkeypatch.setattr(scientific, "detect_mrtrix", failed)
    output = tmp_path / "not-produced.json"
    args = [
        "scientific",
        "plan",
        "--selection",
        str(selection),
        "--proposal",
        str(output),
        "--probe-tools",
    ]
    assert main(args) == 2 and not output.exists()
    assert "timed out" in capsys.readouterr().err

    def interrupted(**kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(scientific, "detect_mrtrix", interrupted)
    assert main(args) == 130 and not output.exists()
