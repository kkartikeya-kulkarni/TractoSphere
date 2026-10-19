"""Native MRtrix extraction of verified low-b measurements, without preprocessing."""

import shlex
from pathlib import Path

import numpy as np

from .._mif_checks import rearrange, voxel_mapping
from ..execution import (
    ExecutionPlan,
    _environment_contract,
    _publish_json,
    detect_mrtrix,
    hash_file,
)
from ..process import check_cancel
from ..storage import require_local_processing
from .contracts import ImportRequest
from .dti import _fresh, _revision, image_data
from .imports import import_artifact
from .planning import build_workflow


def prepare_reference(
    artifacts,
    tasks,
    output_directory,
    *,
    nthreads=2,
    tool_directory=None,
    cancelled=lambda: False,
    progress=lambda _: None,
):
    from .registration import LIMITS

    task = tasks[0]
    artifacts = _fresh(
        tuple(a for a in artifacts if a.id in task.inputs.values()), cancelled, progress
    )
    proposal = build_workflow(artifacts, tasks, verify_sources=False).to_dict()
    errors = [i["message"] for i in proposal["issues"] if i["severity"] == "error"]
    if errors:
        raise ValueError("; ".join(errors))
    dwi = next(a.to_dict() for a in artifacts if a.id == task.inputs["dwi"])
    d = dwi["declarations"]
    if (
        d.get("processing_state") != "preprocessed"
        or not d.get("reference_history")
        or not all(d.get(k) for k in ("subject_id", "acquisition_id", "space_id"))
    ):
        raise ValueError(
            "Reference extraction requires identified preprocessed DWI and explicit processing history; raw data are not relabeled"
        )
    threshold = task.parameters["b0_threshold"]
    b = np.asarray(dwi["gradients"]["bvals"])
    indices = np.flatnonzero(b <= threshold).tolist()
    if not indices:
        raise ValueError("No measurements meet the selected low-b threshold")
    if task.parameters["selection"] == "first_low_b":
        indices = indices[:1]
    folder = Path(output_directory).absolute()
    if folder.exists() or not folder.parent.is_dir():
        raise ValueError("Choose a new reference derivative directory")
    hashes = {s["path"]: s["sha256"] for a in artifacts for s in a.payload["sources"]}
    if any(folder.is_relative_to(Path(p).resolve().parent) for p in hashes):
        raise ValueError("Reference outputs must be separate from source directories")
    require_local_processing([folder, *hashes])
    from ..registration_resources import registration_resources, require_registration_resources

    reservation = registration_resources(tasks, artifacts)
    require_registration_resources(reservation, folder)
    estimate = reservation["memory_bytes"]
    tools = detect_mrtrix(
        names=("mrconvert", "mrmath"), tool_directory=tool_directory, cancelled=cancelled
    )
    if any(t["version"] != "3.0.5" for t in tools.values()):
        raise ValueError("Reference extraction validated for MRtrix3 3.0.5")
    commands = []
    for exe, args, outputs in [
        (
            "mrconvert",
            [
                dwi["path"],
                folder / "low-b.mif",
                "-coord",
                "3",
                ",".join(map(str, indices)),
                "-datatype",
                "float32",
            ],
            ["low-b.mif"],
        ),
        (
            "mrmath",
            [folder / "low-b.mif", "mean", folder / "mean-b0.nii.gz", "-axis", "3"],
            ["mean-b0.nii.gz"],
        ),
    ]:
        argv = [tools[exe]["path"], *map(str, args), "-nthreads", str(nthreads)]
        commands.append(
            dict(
                task=task.id,
                step=exe,
                argv=argv,
                display=shlex.join(argv),
                inputs=list(hashes),
                outputs=outputs,
                kind="reference",
            )
        )
    env = _environment_contract(cancelled)
    env["overrides"].update(OMP_NUM_THREADS=str(nthreads), OPENBLAS_NUM_THREADS="1")
    env["unset"] = ["MRTRIX_OVERWRITE", "MRTRIX_QUIET"]
    return ExecutionPlan(
        dict(
            kind="registration_execution_plan",
            operation="reference",
            artifacts=[a.to_dict() for a in artifacts],
            tasks=[task.to_dict()],
            output_directory=str(folder),
            tools=tools,
            environment=env,
            commands=commands,
            outputs=[
                "low-b.mif",
                "mean-b0.nii.gz",
                "reference.json",
                "scientific-selection.json",
                "qc-metrics.json",
                "qc-report.html",
                "qc-catalog.json",
            ],
            extra_input_hashes=hashes,
            software_hashes={},
            parameters=dict(
                nthreads=nthreads,
                estimated_working_bytes=estimate,
                resource_reservation=reservation,
                storage_margin_bytes=5 * 1024**3,
            ),
            dwi=dwi,
            indices=indices,
            threshold=threshold,
            scientific_limits=LIMITS,
            build=_revision(),
            compiler="tractosphere.registration-reference/1",
            implementation_hashes={
                str(p): hash_file(p)
                for p in (
                    Path(__file__),
                    Path(__file__).parents[1] / "registration_resources.py",
                    Path(__file__).parents[1] / "registration_review.py",
                )
            },
            mrtrix_directory=str(tool_directory) if tool_directory else None,
        )
    )


def verify_reference_step(folder, plan, c, cancelled=lambda: False):
    path = folder / c["outputs"][0]
    with (
        image_data(path) as (affine, values),
        image_data(Path(plan["dwi"]["path"])) as (source_affine, source),
    ):
        axes, flips = voxel_mapping(source_affine, source.shape, affine, values.shape)
        if c["step"] == "mrconvert":
            for j, i in enumerate(plan["indices"]):
                check_cancel(cancelled)
                if not np.array_equal(
                    rearrange(np.asarray(source[..., i], dtype="f4"), axes, flips),
                    np.asarray(values[..., j]),
                ):
                    raise ValueError("Low-b extraction changed selected measurements")
            return dict(
                selected_indices=plan["indices"],
                threshold_s_mm2=plan["threshold"],
                all_selected_values_verified=True,
            )
        expected = np.zeros(source.shape[:3], dtype="f8")
        for i in plan["indices"]:
            check_cancel(cancelled)
            expected += np.asarray(source[..., i], dtype="f8")
        expected /= len(plan["indices"])
        expected = rearrange(expected, axes, flips)
        actual = np.asarray(values, dtype="f8")
        if not np.isfinite(actual).all() or not np.allclose(actual, expected, rtol=2e-6, atol=1e-5):
            raise ValueError(
                "Native mean-b0 differs from independently averaged selected measurements"
            )
        return dict(
            max_absolute_difference=float(np.abs(actual - expected).max()),
            mean_absolute_difference=float(np.abs(actual - expected).mean()),
            selected_indices=plan["indices"],
            threshold_s_mm2=plan["threshold"],
        )


def verify_reference(folder, plan, cancelled=lambda: False):
    from .registration import publish_qc

    output = folder / "mean-b0.nii.gz"
    checks = verify_reference_step(folder, plan, plan["commands"][-1], cancelled)
    record = dict(
        schema="tractosphere.diffusion-reference/1",
        path=str(output),
        sha256=hash_file(output),
        dwi=plan["dwi"],
        selected_indices=plan["indices"],
        b0_threshold=plan["threshold"],
        selection=plan["tasks"][0]["parameters"]["selection"],
        evidence_hashes=plan["extra_input_hashes"],
        commands=plan["commands"],
        tools=plan["tools"],
        independent_check=checks,
    )
    _publish_json(folder / "reference.json", record)
    d = dict(
        plan["dwi"]["declarations"],
        space_kind="DWI_NATIVE",
        anatomical_modality="b0",
        reference_record=str(folder / "reference.json"),
    )
    artifact = import_artifact(
        ImportRequest(
            "anatomy",
            output,
            declarations=d,
            provenance="Mean of recorded low-b measurements from the exact declared preprocessed DWI. No correction performed.",
        )
    )
    result = dict(
        execution="completed",
        numerical_verification="passed",
        scientific_review="QC_REVIEW_REQUIRED",
        scientific_acceptance="unassigned",
        checks=checks,
        products=[dict(role="reference", artifact=artifact.to_dict())],
        findings=plan["scientific_limits"],
    )
    publish_qc(folder, plan, result, plan["dwi"]["declarations"]["subject_id"])
    return result
