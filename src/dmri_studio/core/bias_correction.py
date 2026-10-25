"""Reviewed MRtrix/ANTs bias correction of evidence-bound pre-N4 EDDY data.

The installed wrapper is executed locally; its algorithm is not copied. Existing
producer records and a passing correction gate are required. This adapter checks
byte associations, not the truth of a scientific acceptance claim. Every result
remains pending review. No participant-specific paths or correction settings.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np

from . import execution
from ._mif_checks import produced_mif, rearrange, voxel_mapping
from .dataset import DwiDataset
from .execution import ExecutionPlan, MRtrixStep, _now, _publish_json, hash_file
from .native_processing import _save_native
from .process import ProcessingCancelled, check_cancel
from .qc import _json, artifact, load_catalog
from .tool_versions import ANTS_N4_TESTED_RELEASE
from .workflow import ProcessingHistory, build_plan

MR_COMMANDS = (
    "mrconvert",
    "dwi2mask",
    "dwibiascorrect",
    "dwiextract",
    "mrmath",
    "mrcalc",
    "mrdump",
    "mrinfo",
)


def _mask(dataset: DwiDataset, path: Path) -> tuple[np.ndarray, dict]:
    """Require a binary, nonempty explicit mask with physical voxel correspondence."""
    image = nib.load(path, mmap="r")
    if image.ndim != 3 or image.header.get_xyzt_units()[0] != "mm":
        raise ValueError("N4 fitting mask must be a 3D NIfTI with explicit mm units")
    axes, flips = voxel_mapping(image.affine, image.shape, dataset.affine, dataset.shape)
    values = np.asarray(image.dataobj)
    if not np.array_equal(np.unique(values), [0, 1]):
        raise ValueError("Fitting mask must contain only 0 and 1, with foreground and background")
    return np.asarray(rearrange(values, axes, flips), dtype=np.float32), {
        "voxels": int(np.count_nonzero(values)),
        "resampling": False,
        "scope": "Binary and physical-grid validation only; anatomical review remains required",
    }


def verify_correction_handoff(
    dataset: DwiDataset, record_path: Path, gate_path: Path, cancelled: Callable = lambda: False
) -> dict:
    """Bind a passing saved gate to the EDDY output and its actual rotated vectors.

    Supports the recorded producer contract used by the existing validation
    engine. It does not infer a passed gate from exit code or file existence, and
    does not apply this importer to arbitrary external pipeline record formats.
    """
    record_hash, gate_hash = hash_file(record_path, cancelled), hash_file(gate_path, cancelled)
    record, gate = _json(record_path), _json(gate_path)
    if record.get("status") != "validated_pending_visual_qc" or record.get("return_code") != 0:
        raise ValueError("N4 requires a numerically validated EDDY producer record")
    if gate.get("technical_checks_passed") is not True:
        raise ValueError("Correction diagnostics have not passed; N4 must remain pending")
    argv = record.get("scientific_argv") or record.get("argv", [])
    output_args = [x.removeprefix("--out=") for x in argv if x.startswith("--out=")]
    bval_args = [x.removeprefix("--bvals=") for x in argv if x.startswith("--bvals=")]
    if len(output_args) != 1 or len(bval_args) != 1:
        raise ValueError("Producer lacks unambiguous EDDY output/b-value arguments")
    prefix = output_args[0]
    if dataset.paths.dwi.resolve() not in {Path(prefix + s).resolve() for s in (".nii", ".nii.gz")}:
        raise ValueError("Select the direct pre-N4 EDDY image named by the producer command")
    dwi_hash = hash_file(dataset.paths.dwi, cancelled)
    if gate.get("corrected_input_sha256") != dwi_hash:
        raise ValueError("Correction gate does not refer to this exact pre-N4 image")
    outputs = record.get("outputs", {})
    hashes = {}
    for name, entry in outputs.items():
        path = Path(name)
        if not path.is_absolute() or not isinstance(entry, dict):
            raise ValueError("Unsupported producer output record")
        actual = hash_file(path, cancelled)
        if actual != entry.get("sha256"):
            raise ValueError(f"Validated producer output changed: {path}")
        hashes[str(path.resolve())] = actual
    if hashes.get(str(dataset.paths.dwi.resolve())) != dwi_hash:
        raise ValueError("The corrected DWI is missing from the verified producer outputs")
    rotated = Path(prefix + ".eddy_rotated_bvecs").resolve()
    if str(rotated) not in hashes:
        raise ValueError("Producer lacks verified EDDY-rotated b-vectors")
    vectors = np.loadtxt(rotated, ndmin=2)
    if vectors.shape != (3, dataset.n_volumes) or not np.array_equal(
        vectors.T, dataset.gradients.bvecs
    ):
        raise ValueError("Loaded vectors do not match EDDY-rotated vectors in volume order")
    bval_source = Path(bval_args[0]).resolve()
    recorded_bval = record.get("inputs", {}).get(str(bval_source))
    if isinstance(recorded_bval, dict):
        recorded_bval = recorded_bval.get("sha256")
    if recorded_bval is None or hash_file(bval_source, cancelled) != recorded_bval:
        raise ValueError("EDDY input b-values lack unchanged provenance")
    if not np.array_equal(np.loadtxt(bval_source).ravel(), dataset.gradients.bvals):
        raise ValueError("Loaded b-values differ from EDDY acquisition order")
    if (
        hash_file(record_path, cancelled) != record_hash
        or hash_file(gate_path, cancelled) != gate_hash
    ):
        raise ValueError("Correction evidence changed during validation")
    return {
        "record": str(record_path),
        "gate": str(gate_path),
        "record_sha256": record_hash,
        "gate_sha256": gate_hash,
        "producer_output_hashes": hashes,
        "bval_source": str(bval_source),
        "bval_source_sha256": recorded_bval,
        "rotated_vectors": str(rotated),
        "gate_flags": gate.get("flags", []) + gate.get("scientific_concerns", []),
        "scientific_acceptance": "pending",
        "scope": "Saved correction gate reused, not rerun or converted to scientific acceptance",
    }


def _tools(
    directory: Path | None, ants_directory: Path | None, cancelled: Callable
) -> tuple[dict, dict]:
    """Freeze required local executables and installed Python wrapper sources."""
    tools = execution.detect_mrtrix(
        tool_directory=directory, cancelled=cancelled, names=MR_COMMANDS
    )
    candidate = (
        str(ants_directory / "N4BiasFieldCorrection")
        if ants_directory
        else shutil.which("N4BiasFieldCorrection")
    )
    if not candidate:
        raise ValueError(
            "ANTs N4BiasFieldCorrection is unavailable; select its installed bin directory"
        )
    binary = Path(candidate).resolve(strict=True)
    text = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, check=True, timeout=10
    ).stdout
    match = re.search(r"ANTs Version:\s*(\S+)", text)
    if not match or match[1] != ANTS_N4_TESTED_RELEASE:
        raise ValueError(
            "This N4 adapter has been exercised with ANTs 2.6.5; another release requires validation"
        )
    tools["N4BiasFieldCorrection"] = {
        "path": str(binary),
        "version": match[1],
        "version_output": text,
        "sha256": hash_file(binary, cancelled),
    }
    library = Path(tools["dwibiascorrect"]["path"]).parent.parent / "lib/mrtrix3"
    if not (library / "dwibiascorrect/ants.py").is_file():
        raise ValueError("Cannot locate installed MRtrix wrapper source to bind its implementation")
    sources = {str(p): hash_file(p, cancelled) for p in sorted(library.rglob("*.py"))}
    return tools, sources


def prepare_bias(
    dataset: DwiDataset,
    history: ProcessingHistory,
    operations: tuple[str, ...],
    output: Path,
    *,
    fitting_mask: Path | None,
    correction_record: Path | None,
    correction_gate: Path | None,
    ants_directory: Path | None = None,
    tool_directory: Path | None = None,
    nthreads: int = 4,
    cancelled: Callable = lambda: False,
    progress: Callable = lambda _: None,
) -> ExecutionPlan:
    """Freeze once-only N4 settings, explicit mask, upstream gate and all sources."""
    if set(operations) - {"convert", "bias"}:
        raise ValueError(
            "Run post-EDDY N4 separately with an explicit saved fitting mask; do not repeat native corrections"
        )
    proposal = build_plan(dataset, history, operations).to_dict()
    if proposal["status"] != "eligible":
        raise ValueError(
            "Confirm completed EDDY/motion and explicitly unperformed bias correction before N4"
        )
    if any(p is None for p in (fitting_mask, correction_record, correction_gate)):
        raise ValueError(
            "N4 requires a fitting mask, validated EDDY record and passing correction gate"
        )
    mask_path, record_path, gate_path = (
        Path(p).expanduser().resolve(strict=True)
        for p in (fitting_mask, correction_record, correction_gate)
    )
    _, mask_check = _mask(dataset, mask_path)
    progress("Verifying saved correction diagnostics, producer hashes and rotated gradients…")
    handoff = verify_correction_handoff(dataset, record_path, gate_path, cancelled)
    base = execution.prepare_execution(
        dataset,
        history,
        ("convert",),
        output,
        nthreads=nthreads,
        tool_directory=tool_directory,
        cancelled=cancelled,
        progress=progress,
    )
    p = base.to_dict()
    folder = Path(p["output_directory"])
    for source in (mask_path, record_path, gate_path):
        if source.parent == folder or source.parent in folder.parents:
            raise ValueError("N4 run folder must be outside mask and evidence input directories")
    tools, wrapper_sources = _tools(tool_directory, ants_directory, cancelled)
    paths = {
        "fitting_mask": str(mask_path),
        "correction_record": str(record_path),
        "correction_gate": str(gate_path),
    }
    argv = [
        tools["dwibiascorrect"]["path"],
        "ants",
        str(folder / "dwi.mif"),
        str(folder / "bias-corrected.mif"),
        "-mask",
        str(folder / "fitting-mask.mif"),
        "-bias",
        str(folder / "bias-field.mif"),
        "-ants.b",
        "[100,3]",
        "-ants.c",
        "[1000,0.0]",
        "-ants.s",
        "4",
        "-config",
        "BZeroThreshold",
        str(p["parameters"]["b0_max"]),
        "-nthreads",
        str(nthreads),
        "-scratch",
        str(folder / "scratch"),
        "-nocleanup",
        "-debug",
    ]
    # Ensure the script resolves exactly the binaries we inspected. Its other
    # dependencies are the installed MRtrix Python source tree bound above.
    overrides = p["environment"]["overrides"] | {
        "PATH": os.pathsep.join(
            [
                str(Path(tools["mrconvert"]["path"]).parent),
                str(Path(tools["N4BiasFieldCorrection"]["path"]).parent),
                os.environ.get("PATH", ""),
            ]
        ),
        "ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS": str(nthreads),
        "OMP_NUM_THREADS": str(nthreads),
        "TMPDIR": str(folder / "scratch"),
    }
    p.update(
        kind="mrtrix_ants_bias_plan",
        conversion_plan=base.to_dict(),
        operations=list(operations),
        tools=tools,
        bias_inputs=paths,
        bias_input_hashes={k: hash_file(Path(v), cancelled) for k, v in paths.items()},
        handoff=handoff,
        mask_check=mask_check,
        wrapper_sources=wrapper_sources,
        implementation_hashes={
            str(Path(__file__).resolve()): hash_file(Path(__file__)),
            str(Path(execution.__file__).resolve()): hash_file(Path(execution.__file__)),
        },
        resources={
            "estimated_peak_additional_bytes": 7 * int(np.prod(dataset.shape)) * 4 + 256 * 1024**2
        },
        outputs=[
            "dwi.mif",
            "fitting-mask.mif",
            "fitting-mask.nii",
            "bias-corrected.mif",
            "bias-field.mif",
            "bias-corrected.nii",
            "bias-field.nii",
            "corrected.bval",
            "corrected.bvec",
        ],
        commands=p["commands"] + [{"step": "bias", "argv": argv, "display": shlex.join(argv)}],
    )
    p["environment"]["overrides"] = overrides
    p["parameters"].update(
        spline_spacing_mm=100,
        spline_order=3,
        iterations=1000,
        convergence_threshold=0.0,
        shrink_factor=4,
    )
    p["internal_actions"] += [
        "Reindex the explicit binary mask into the verified DWI MIF grid without interpolation or boundary changes.",
        "Run the installed MRtrix/ANTs wrapper once; retain scratch, mean low-b input, log and global normalization.",
        "Verify input divided by the saved field across every volume, native geometry, embedded gradients and exact sidecars.",
    ]
    p["scientific_limits"] += [
        "A passing upstream technical gate is not scientific acceptance. Its flags remain pending.",
        "N4 uses the mask as weights. Neither a smooth field nor completing the fixed iteration level establishes anatomical correctness.",
        "The standard wrapper normalizes the field by the ratio of masked corrected/input mean-b0 integrals; this is retained, not reversed.",
    ]
    return ExecutionPlan(p)


def _stage_mask(dataset: DwiDataset, folder: Path, mask_path: Path, cancelled: Callable) -> dict:
    """Write an exact reindexed scalar MIF, avoiding a rounded NIfTI/ITK mismatch."""
    native, check = _mask(dataset, mask_path)
    with produced_mif(folder / "dwi.mif") as (fields, affine, dwi):
        axes, flips = voxel_mapping(dataset.affine, dataset.shape, affine, dwi.shape)
        values = rearrange(native, axes, flips)
        lines = [
            "mrtrix image",
            "dim: " + ",".join(map(str, values.shape)),
            "vox: " + ",".join(fields["vox"][0].split(",")[:3]),
            "layout: +0,+1,+2",
            "datatype: Float32LE",
        ]
        lines += ["transform: " + row for row in fields["transform"]]
        offset = 4096
        header = ("\n".join(lines + [f"file: . {offset}", "END", ""])).encode()
        if len(header) > offset:
            raise ValueError("Scalar mask header exceeds allocated offset")
        with (folder / "fitting-mask.mif").open("xb") as stream:
            stream.write(header.ljust(offset, b"\0"))
            stream.write(np.asarray(values, dtype="<f4").tobytes(order="F"))
    with produced_mif(folder / "fitting-mask.mif") as (fields, affine, values):
        axes, flips = voxel_mapping(affine, values.shape, dataset.affine, dataset.shape)
        if "dw_scheme" in fields or not np.array_equal(rearrange(values, axes, flips), native):
            raise ValueError("Scalar fitting mask staging changed its values")
        _save_native(dataset, values, affine, folder / "fitting-mask.nii", cancelled)
    return check | {
        "boundary_changes": False,
        "header": "Exact converted DWI grid; no diffusion scheme on scalar fitting mask",
    }


class BiasStep(MRtrixStep):
    """Local wrapper adapter using the same validated-step/runner interface."""

    def validate(self) -> None:
        """Require staged gradient-bearing input/mask and exclusively new outputs."""
        if self.name != "bias" or any(not p.is_file() for p in self.inputs):
            raise ValueError("Missing N4 staged inputs")
        if any(p.exists() or p.is_symlink() for p in self.outputs):
            raise ValueError("Bias output exists; overwrite refused")

    def qc(
        self, dataset: DwiDataset, folder: Path, cancelled: Callable, progress: Callable
    ) -> dict:
        """Check field, gradients, division and normalization independently of exit code."""
        return verify_bias_outputs(dataset, folder, cancelled, progress)


def _grid_deviation_mm(reference: np.ndarray, candidate: np.ndarray, shape: tuple[int, ...]) -> float:
    """Largest physical displacement across voxel-centre corners of one grid."""
    if not np.isfinite(reference).all() or not np.isfinite(candidate).all():
        return float("inf")
    corners = np.array(
        [
            [x, y, z]
            for x in (0, shape[0] - 1)
            for y in (0, shape[1] - 1)
            for z in (0, shape[2] - 1)
        ],
        dtype=float,
    )
    return float(
        np.max(
            np.linalg.norm(
                nib.affines.apply_affine(reference, corners)
                - nib.affines.apply_affine(candidate, corners),
                axis=1,
            )
        )
    )


def verify_bias_outputs(
    dataset: DwiDataset,
    folder: Path,
    cancelled: Callable = lambda: False,
    progress: Callable = lambda _: None,
) -> dict:
    """Exhaustive float32 division and native export, bounded to a volume in RAM."""
    with (
        produced_mif(folder / "dwi.mif") as (sf, sa, source),
        produced_mif(folder / "bias-corrected.mif") as (rf, ra, result),
        produced_mif(folder / "bias-field.mif") as (_, fa, field),
    ):
        field_grid_deviation = _grid_deviation_mm(sa, fa, source.shape[:3])
        if (
            source.shape != result.shape
            or field.shape != source.shape[:3]
            or not np.allclose(sa, ra, atol=1e-7, rtol=0)
            # ANTs/MRtrix may round the scalar field's transform through
            # float32 headers. Permit only sub-micron corner displacement;
            # a changed voxel grid or interpolation still fails.
            or field_grid_deviation > 1e-4
        ):
            raise ValueError("N4 changed geometry or volume order/shape")
        expected = np.array([[float(v) for v in row.split(",")] for row in sf.get("dw_scheme", [])])
        actual = np.array([[float(v) for v in row.split(",")] for row in rf.get("dw_scheme", [])])
        if (
            actual.shape != (dataset.n_volumes, 4)
            or expected.shape != actual.shape
            or not np.isfinite(actual).all()
            or not np.array_equal(actual[:, 3], expected[:, 3])
            or not np.allclose(actual[:, :3], expected[:, :3], atol=2e-10, rtol=0)
        ):
            raise ValueError("N4 lost or changed embedded diffusion gradients")
        if not np.isfinite(field).all() or np.min(field) <= 0:
            raise ValueError("N4 field is not finite and strictly positive")
        stats, count, maximum = [], 0, 0.0
        for v in range(dataset.n_volumes):
            check_cancel(cancelled)
            before, after = source[..., v], result[..., v]
            divided = before / field
            if not np.isfinite(after).all() or not np.isfinite(divided).all():
                raise ValueError(f"Nonfinite N4 image/division at volume {v}")
            error = float(np.max(np.abs(after - divided)))
            if not np.allclose(after, divided, atol=1e-6, rtol=2e-7):
                raise ValueError(f"N4 division mismatch at volume {v}: {error}")
            count += before.size
            maximum = max(maximum, error)
            stats.append(
                {
                    "volume": v,
                    "b_value": float(dataset.gradients.bvals[v]),
                    "mean_before": float(np.mean(before, dtype=np.float64)),
                    "mean_after": float(np.mean(after, dtype=np.float64)),
                    "division_max_absolute_error": error,
                }
            )
            progress(f"Verified N4 division volume {v + 1}/{dataset.n_volumes}")
        image_check = _save_native(dataset, result, ra, folder / "bias-corrected.nii", cancelled)
        field_check = _save_native(dataset, field, fa, folder / "bias-field.nii", cancelled)
        field_range = [float(np.min(field)), float(np.max(field))]
    log = (folder / "logs/bias.log").read_text(errors="replace")
    multipliers = re.findall(r"resulting multiplier = ([0-9.eE+-]+)", log)
    if (
        len(multipliers) != 1
        or not np.isfinite(float(multipliers[0]))
        or float(multipliers[0]) <= 0
    ):
        raise ValueError("Wrapper did not record a unique positive global field normalization")
    normalization = _verify_normalization(dataset, folder, float(multipliers[0]), cancelled)
    for name, array in [
        ("corrected.bval", dataset.gradients.bvals[None, :]),
        ("corrected.bvec", dataset.gradients.bvecs.T),
    ]:
        with (folder / name).open("x") as stream:
            np.savetxt(stream, array, fmt="%.17g")
        if not np.array_equal(np.loadtxt(folder / name).ravel(), array.ravel()):
            raise ValueError("Exported gradient sidecar changed numeric values")
    return {
        "status": "passed",
        "values_compared": count,
        "division_max_absolute_error": maximum,
        "division_tolerance": {"atol": 1e-6, "rtol": 2e-7},
        "field_range": field_range,
        "field_grid_max_corner_displacement_mm": field_grid_deviation,
        "field_grid_tolerance_mm": 1e-4,
        "wrapper_global_field_multiplier": float(multipliers[0]),
        "normalization": normalization,
        "image": image_check,
        "field": field_check,
        "per_volume": stats,
        "embedded_gradient_rows": dataset.n_volumes,
        "rotated_sidecars_preserved": True,
        "native_diffusion_space": True,
        "spatial_interpolation": False,
        "scientific_review": "required",
    }


def _verify_normalization(
    dataset: DwiDataset, folder: Path, multiplier: float, cancelled: Callable
) -> dict:
    """Check actual low-b selection and wrapper scaling against retained fit intermediates."""
    candidates = list((folder / "scratch").glob("*/init_bias.nii"))
    if len(candidates) != 1:
        raise ValueError("Cannot identify the unique retained ANTs fit")
    scratch = candidates[0].parent
    raw_field_image = nib.load(candidates[0])
    corrected_image = nib.load(scratch / "corrected.nii")
    indices = np.flatnonzero(dataset.gradients.b0_mask(b0_max=dataset.validation.policy.b0_max))
    with (
        produced_mif(folder / "dwi.mif") as (_, affine, dwi),
        produced_mif(scratch / "mean_bzero.mif") as (_, ma, mean),
        produced_mif(folder / "bias-field.mif") as (_, fa, field),
        produced_mif(folder / "fitting-mask.mif") as (_, _, mask),
    ):
        if mean.shape != dwi.shape[:3] or not np.allclose(ma, affine, atol=1e-7, rtol=0):
            raise ValueError("Wrapper mean low-b geometry changed")
        expected = np.zeros(mean.shape, np.float64)
        for index in indices:
            check_cancel(cancelled)
            expected += dwi[..., index]
        expected /= len(indices)
        if not np.allclose(mean, expected, atol=1e-5, rtol=2e-7):
            raise ValueError("Wrapper mean does not match the documented low-b indices")
        axes, flips = voxel_mapping(raw_field_image.affine, raw_field_image.shape, fa, field.shape)
        raw_field = rearrange(np.asarray(raw_field_image.dataobj), axes, flips)
        scaled = raw_field * multiplier
        if not np.allclose(field, scaled, atol=1e-6, rtol=2e-7):
            raise ValueError("Saved field does not match the documented global normalization")
        axes, flips = voxel_mapping(
            corrected_image.affine, corrected_image.shape, affine, mean.shape
        )
        corrected = rearrange(np.asarray(corrected_image.dataobj), axes, flips)
        input_sum = float(np.sum(mean * mask, dtype=np.float64))
        corrected_sum = float(np.sum(corrected * mask, dtype=np.float64))
        ratio = corrected_sum / input_sum
        if not np.isfinite(ratio) or not np.isclose(ratio, multiplier, atol=1e-7, rtol=5e-6):
            raise ValueError(
                "Recorded normalization disagrees with masked input/corrected integrals"
            )
        _save_native(dataset, mean, affine, folder / "mean-low-b-before.nii", cancelled)
        _save_native(dataset, mean / field, affine, folder / "mean-low-b-after.nii", cancelled)
        return {
            "low_b_indices_zero_based": indices.tolist(),
            "b0_max": dataset.validation.policy.b0_max,
            "mean_max_error": float(np.max(np.abs(mean - expected))),
            "scaled_field_max_error": float(np.max(np.abs(field - scaled))),
            "independent_float64_integral_ratio": ratio,
            "ratio_tolerance": "atol 1e-7, rtol 5e-6 accommodates wrapper summation precision",
            "field_scaling": "initial ANTs field multiplied by corrected/input masked mean-b0 integral ratio",
        }


def execute_bias(
    dataset: DwiDataset,
    plan: ExecutionPlan,
    *,
    approval: str,
    cancelled: Callable = lambda: False,
    progress: Callable = lambda _: None,
) -> dict:
    """Revalidate approval, execute once in a new folder, and retain failures honestly."""
    if approval != plan.digest:
        raise ValueError("Approve this exact N4 plan before execution")
    p = plan.to_dict()
    current = prepare_bias(
        dataset,
        ProcessingHistory.from_dict(p["history"]),
        tuple(p["operations"]),
        Path(p["output_directory"]),
        **{k: Path(v) for k, v in p["bias_inputs"].items()},
        tool_directory=Path(p["tools"]["mrconvert"]["path"]).parent,
        ants_directory=Path(p["tools"]["N4BiasFieldCorrection"]["path"]).parent,
        nthreads=p["parameters"]["nthreads"],
        cancelled=cancelled,
        progress=progress,
    )
    if current.digest != plan.digest:
        raise ValueError("N4 sources, evidence or implementation changed; prepare a fresh plan")
    folder = Path(p["output_directory"])
    if shutil.disk_usage(folder.parent).free < p["resources"]["estimated_peak_additional_bytes"]:
        raise ValueError("Insufficient free disk for retained N4 outputs, scratch and exports")
    base = ExecutionPlan(p["conversion_plan"])
    converted = execution.execute_plan(
        dataset, base, approval=base.digest, cancelled=cancelled, progress=progress
    )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "plan": p,
        "approval": {"digest": approval},
        "status": "running",
        "started_at": _now(),
        "steps": list(converted["steps"]),
        "validation": dict(converted["validation"]),
        "human_qc": "pending",
        "outputs": {},
    }

    def checkpoint() -> None:
        """Replace only this new attempt's own progress record."""
        temporary = folder / ".bias-manifest.tmp"
        temporary.write_text(json.dumps(manifest, indent=2, allow_nan=False))
        temporary.replace(folder / "bias-manifest.json")

    try:
        if converted["status"] == "cancelled":
            raise ProcessingCancelled(converted.get("error", "Conversion cancelled"))
        if converted["status"] != "validated":
            raise ValueError(
                "N4 conversion prerequisite failed: " + converted.get("error", converted["status"])
            )
        manifest["validation"]["fitting_mask"] = _stage_mask(
            dataset, folder, Path(p["bias_inputs"]["fitting_mask"]), cancelled
        )
        (folder / "scratch").mkdir()
        command = p["commands"][-1]
        adapter = BiasStep(
            "bias",
            tuple(command["argv"]),
            (folder / "dwi.mif", folder / "fitting-mask.mif"),
            (folder / "bias-corrected.mif", folder / "bias-field.mif"),
            p["parameters"],
        )
        step = {"step": "bias", "status": "running", "started_at": _now(), "argv": command["argv"]}
        manifest["steps"].append(step)
        checkpoint()
        try:
            step.update(
                adapter.run(
                    folder=folder,
                    environment=dict(os.environ) | p["environment"]["overrides"],
                    cancelled=cancelled,
                    progress=progress,
                )
            )
        except Exception as exc:
            if hasattr(exc, "record"):
                step.update(exc.record)
            raise
        step.update(status="command_completed", finished_at=_now())
        checkpoint()
        manifest["validation"]["bias"] = adapter.qc(dataset, folder, cancelled, progress)
        step["status"] = "validated"
        # The wrapper's -nocleanup retains commands, mean b0 and fit intermediates.
        # Hash every regular output including scratch/logs, excluding live manifests.
        manifest["outputs"] = {
            str(path.relative_to(folder)): {
                "sha256": hash_file(path, cancelled),
                "bytes": path.stat().st_size,
            }
            for path in sorted(folder.rglob("*"))
            if path.is_file() and path.name not in {"bias-manifest.json", ".bias-manifest.tmp"}
        }
        manifest["status"] = "validated_pending_qc"
    except (ProcessingCancelled, KeyboardInterrupt) as exc:
        manifest.update(status="cancelled", error=str(exc))
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            dataset.assert_current()
            checks = {p["input_paths"][k]: v["sha256"] for k, v in p["inputs"].items()}
            checks.update({p["bias_inputs"][k]: v for k, v in p["bias_input_hashes"].items()})
            checks.update(p["handoff"]["producer_output_hashes"])
            checks[p["handoff"]["bval_source"]] = p["handoff"]["bval_source_sha256"]
            manifest["inputs_unchanged"] = all(
                hash_file(Path(path)) == digest for path, digest in checks.items()
            )
            if not manifest["inputs_unchanged"]:
                raise ValueError("N4 input or evidence changed during execution")
        except Exception as exc:
            manifest.update(status="failed", error=str(exc), inputs_unchanged=False)
        for step in manifest["steps"]:
            if step["status"] == "running":
                step.update(status=manifest["status"], finished_at=_now())
        manifest["finished_at"] = _now()
        checkpoint()
    if manifest["status"] == "validated_pending_qc":
        try:
            export_bias_catalog(dataset, folder, manifest)
        except Exception as exc:
            manifest["qc_export_error"] = str(exc)
            # Do not mutate a manifest already bound by a partly written catalog.
            _publish_json(
                folder / "qc-export-failure.json",
                {"error": str(exc), "processing_status": manifest["status"]},
            )
    return manifest


def export_bias_catalog(dataset: DwiDataset, folder: Path, manifest: dict) -> Path:
    """Expose saved metrics/logs and all-volume before/after in the existing QC GUI."""
    from .bias_qc import export_bias_report

    report, figures = export_bias_report(dataset, folder, manifest)
    before = {k: str(getattr(dataset.paths, k)) for k in ("dwi", "bval", "bvec")}
    after = {
        "dwi": str(folder / "bias-corrected.nii"),
        "bval": str(folder / "corrected.bval"),
        "bvec": str(folder / "corrected.bvec"),
    }
    for source in (before, after):
        source["sha256"] = {k: hash_file(Path(source[k])) for k in ("dwi", "bval", "bvec")}
    comparison = folder / "bias-comparison.json"
    _publish_json(
        comparison,
        {
            "notice": "N4 field applied once to pre-N4 data. Check tissue contrast and field/anatomical correspondence; scientific review required.",
            "sources": {"before": before, "after": after},
            "modes": [
                {
                    "label": "Before / after N4",
                    "left": "before",
                    "right": "after",
                    "pairs": [[v, v] for v in range(dataset.n_volumes)],
                    "description": "Identical native grid, volume order and rotated gradients; no spatial interpolation",
                }
            ],
        },
    )
    metrics = folder / "bias-verification.json"
    _publish_json(metrics, manifest["validation"])
    destination = folder / "qc-catalog.json"
    _publish_json(
        destination,
        {
            "schema_version": 1,
            "kind": "dmri_qc_catalog",
            "identity": {
                "subject": "User-loaded dataset; identity retained in source records",
                "input": str(dataset.paths.dwi),
            },
            "stages": [
                {
                    "id": "bias",
                    "title": "MRtrix / ANTs N4 bias correction",
                    "execution": "completed",
                    "technical": "passed",
                    "automated_qc": "pending",
                    "scientific_review": "required",
                    "findings": manifest["plan"]["scientific_limits"],
                    "artifacts": [
                        artifact(comparison, "comparison", "All-volume native before/after"),
                        artifact(metrics, "metrics", "Division, field and gradient verification"),
                        artifact(
                            folder / "bias-manifest.json",
                            "history",
                            "Exact configuration and processing history",
                        ),
                        artifact(
                            folder / "logs/bias.log",
                            "log",
                            "Wrapper / ANTs execution and normalization",
                        ),
                        artifact(
                            Path(manifest["plan"]["bias_inputs"]["correction_gate"]),
                            "metrics",
                            "Upstream correction gate and unresolved concerns",
                        ),
                        artifact(report, "report", "Portable all-slice field and mask QC"),
                        *[artifact(path, "figure", path.stem) for path in figures],
                    ],
                }
            ],
        },
    )
    load_catalog(destination)
    return destination
