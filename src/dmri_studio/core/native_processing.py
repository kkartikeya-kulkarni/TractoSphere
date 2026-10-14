"""General native MP-PCA/Gibbs adapter extracted from verified study workflows.

Uses installed MRtrix CLI tools, the existing conversion engine and physical-grid
checks. No copied denoising/Gibbs algorithm. Plans bind exact commands and inspected
MRtrix axes before execution. Each changed image retains a before/after mapping;
statistical differences are descriptive, never automated scientific acceptance.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import nibabel as nib
import numpy as np

from . import execution
from ._mif_checks import produced_mif, rearrange, voxel_mapping
from .acquisition import gibbs_plane
from .dataset import SourceFingerprint
from .execution import (
    ExecutionPlan,
    _now,
    _publish_json,
    _validate_mask,
    execute_plan,
    hash_file,
    prepare_execution,
)
from .process import ProcessingCancelled, check_cancel
from .validation import json_compatible
from .workflow import ProcessingHistory, build_plan


def prepare_native(
    dataset,
    history,
    operations,
    output,
    *,
    clean_scale=2,
    nthreads=4,
    tool_directory=None,
    cancelled=lambda: False,
    progress=lambda _: None,
    extent=5,
    nshifts=20,
    minW=1,
    maxW=3,
    native_record=None,
) -> ExecutionPlan:
    """Freeze scientific eligibility, sources, binaries, axes and explicit settings."""
    proposal = build_plan(dataset, history, operations).to_dict()
    if proposal["status"] != "eligible":
        reasons = proposal["reasons"] + [
            r for node in proposal["nodes"] if node["status"] != "eligible" for r in node["reasons"]
        ]
        raise ValueError("Proposal requires review: " + "; ".join(reasons))
    for name, value, lo, hi in [
        ("extent", extent, 3, 15),
        ("nshifts", nshifts, 1, 100),
        ("minW", minW, 1, 10),
        ("maxW", maxW, 1, 10),
    ]:
        if type(value) is not int or not lo <= value <= hi:
            raise ValueError(f"{name} must be an integer between {lo} and {hi}")
    if extent % 2 == 0 or minW > maxW:
        raise ValueError("Denoising extent must be odd; Gibbs minW must not exceed maxW")
    if "denoise" in operations and any(n < extent for n in dataset.shape[:3]):
        raise ValueError("Denoising extent exceeds an image dimension")
    base = prepare_execution(
        dataset,
        history,
        ("convert",),
        output,
        clean_scale=clean_scale,
        nthreads=nthreads,
        tool_directory=tool_directory,
        cancelled=cancelled,
        progress=progress,
    )
    payload = base.to_dict()
    if native_record is not None:
        from .correction import _acquisition_source

        acquisition, checks = _acquisition_source(dataset, Path(native_record), cancelled)
        payload["native_input_record"] = str(native_record)
        payload["original_acquisition"] = str(acquisition)
        payload["extra_input_hashes"] = checks

    tools = execution.detect_mrtrix(
        tool_directory=tool_directory,
        cancelled=cancelled,
        names=tuple(
            ["mrconvert", "dwi2mask"]
            + (["dwidenoise"] if "denoise" in operations else [])
            + (["mrdegibbs", "mrinfo"] if "gibbs" in operations else [])
        ),
    )
    output = Path(payload["output_directory"])
    parameters = payload["parameters"] | {
        "extent": extent,
        "estimator": "Exp2",
        "nshifts": nshifts,
        "minW": minW,
        "maxW": maxW,
    }
    if "gibbs" in operations:
        plane = gibbs_plane(nib.load(dataset.paths.dwi), json_compatible(dataset.sidecar) or {})
        command = [
            tools["mrinfo"]["path"],
            str(dataset.paths.dwi),
            "-size",
            "-spacing",
            "-transform",
        ]
        text = subprocess.run(
            command, capture_output=True, text=True, check=True, timeout=30
        ).stdout
        lines = text.strip().splitlines()
        shape = tuple(map(int, lines[0].split()))
        spacing = np.array(list(map(float, lines[1].split())))
        transform = np.array([list(map(float, line.split())) for line in lines[2:]])
        if transform.shape != (4, 4):
            raise ValueError("Unexpected MRtrix header inspection format")
        transform[:3, :3] *= spacing[:3]
        axes, flips = voxel_mapping(dataset.affine, dataset.shape, transform, shape)
        parameters["gibbs"] = plane | {
            "mif_axes": [axes.index(a) for a in plane["axes"]],
            "expected_mif_shape": shape,
            "expected_mif_affine": transform.tolist(),
            "native_axes_in_mif_order": axes,
            "flips": flips,
            "header_inspection": {"argv": command, "stdout": text},
        }
    common = ["-nthreads", str(nthreads)]
    commands = list(payload["commands"])
    current = output / "dwi.mif"
    outputs = list(payload["outputs"])
    for operation in ["denoise", "gibbs"]:
        if operation not in operations:
            continue
        target = output / f"{operation}.mif"
        if operation == "denoise":
            argv = [
                tools["dwidenoise"]["path"],
                str(current),
                str(target),
                "-noise",
                str(output / "noise.mif"),
                "-extent",
                ",".join([str(extent)] * 3),
                "-estimator",
                "Exp2",
                "-datatype",
                "float32",
                *common,
            ]
            outputs += ["noise.mif", "noise.nii"]
        else:
            argv = [
                tools["mrdegibbs"]["path"],
                str(current),
                str(target),
                "-axes",
                ",".join(map(str, parameters["gibbs"]["mif_axes"])),
                "-nshifts",
                str(nshifts),
                "-minW",
                str(minW),
                "-maxW",
                str(maxW),
                "-datatype",
                "float32le",
                *common,
            ]
        commands.append(
            {
                "step": operation,
                "argv": argv,
                "display": shlex.join(argv),
                "input_mif": str(current),
                "output_mif": str(target),
            }
        )
        outputs += [f"{operation}.mif", f"{operation}.nii", f"{operation}-residual.nii"]
        current = target
    if "mask" in operations:
        argv = [
            tools["dwi2mask"]["path"],
            str(current),
            str(output / "mask.mif"),
            "-clean_scale",
            str(parameters["clean_scale"]),
            "-config",
            "BZeroThreshold",
            str(parameters["b0_max"]),
            *common,
        ]
        commands.append({"step": "mask", "argv": argv, "display": shlex.join(argv)})
        argv = [
            tools["mrconvert"]["path"],
            str(output / "mask.mif"),
            str(output / "mask-export.nii"),
            "-datatype",
            "uint8",
            "-clear_property",
            "dw_scheme",
            *common,
        ]
        commands.append({"step": "export_mask", "argv": argv, "display": shlex.join(argv)})
        outputs += ["mask.mif", "mask-export.nii", "mask.nii.gz"]
    outputs += ["native.bval", "native.bvec"]
    # Conservative peak includes all retained MIF/NIfTI/residual outputs and
    # two transient full-size buffers. It is a capacity estimate, not an ETA.
    raw_bytes = int(np.prod(dataset.shape)) * 4
    retained = (1 + 3 * len(set(operations) & {"denoise", "gibbs"})) * raw_bytes
    payload["implementation_hashes"] = {
        str(Path(__file__).resolve()): hash_file(Path(__file__)),
        str(Path(execution.__file__).resolve()): hash_file(Path(execution.__file__)),
        str(Path(__file__).with_name("checkpoints.py")): hash_file(
            Path(__file__).with_name("checkpoints.py")
        ),
    }
    payload["resources"] = {
        "estimated_peak_additional_bytes": retained + 2 * raw_bytes + 256 * 1024**2,
        "basis": "float32 native derivatives plus transient export/residual buffers; excludes upstream source files",
    }
    payload.update(
        kind="mrtrix_native_execution_plan",
        conversion_plan=base.to_dict(),
        parameters=parameters,
        tools=tools,
        commands=commands,
        outputs=outputs,
        operations=list(operations),
    )
    payload["internal_actions"] += [
        "Verify every native result and gradient; save before-minus-after residuals, noise map and explicit viewer correspondences without interpolation.",
        "Mask the last selected native result when masking is selected.",
    ]
    payload["scientific_limits"] += [
        "MP-PCA precedes Gibbs and interpolation. Partial Fourier/filtering limitations remain; standard mrdegibbs is not PF-specific correction.",
        "Execution and finite output checks do not establish signal preservation; inspect residual anatomy and all relevant boundaries.",
    ]
    return ExecutionPlan(payload)


def _save_native(dataset, values, affine, destination, cancelled) -> dict:
    """Export reindexed planes to a memory-mapped NIfTI, never interpolate."""
    if destination.exists():
        raise ValueError("Native export destination exists")
    shape = (*dataset.shape[:3], values.shape[3]) if values.ndim == 4 else dataset.shape[:3]
    axes, flips = voxel_mapping(affine, values.shape, dataset.affine, dataset.shape)
    # A new scratch array bounds memory independently of DWI volume count.
    scratch = destination.with_suffix(".export-array")
    with scratch.open("xb") as handle:
        handle.truncate(int(np.prod(shape)) * 4)
    array = np.memmap(scratch, mode="r+", dtype=np.float32, shape=shape, order="F")
    try:
        for v in range(shape[3] if len(shape) == 4 else 1):
            check_cancel(cancelled)
            plane = rearrange(values[..., v] if values.ndim == 4 else values, axes, flips)
            if not np.isfinite(plane).all():
                raise ValueError("Nonfinite native output")
            if len(shape) == 4:
                array[..., v] = plane
            else:
                array[:] = plane
        original = nib.load(dataset.paths.dwi)
        header = original.header.copy()
        header.set_data_dtype(np.float32)
        image = original.__class__(array, dataset.affine, header)
        image.set_qform(original.get_qform(), int(original.header["qform_code"]))
        image.set_sform(original.get_sform(), int(original.header["sform_code"]))
        nib.save(image, destination)
        saved = nib.load(destination, mmap="r")
        if saved.shape != shape or not np.allclose(saved.affine, dataset.affine, atol=1e-5, rtol=0):
            raise ValueError("Native export geometry changed")
        for v in range(shape[3] if len(shape) == 4 else 1):
            a = np.asarray(saved.dataobj[..., v] if len(shape) == 4 else saved.dataobj)
            b = array[..., v] if len(shape) == 4 else array
            if not np.array_equal(a, b):
                raise ValueError("Native NIfTI export changed values")
        return {"shape": shape, "native_grid": True, "finite": True, "exact_export_verified": True}
    finally:
        array.flush()
        array._mmap.close()
        # This is our own transient export buffer, never an input or derivative.
        scratch.unlink()


def _verify_native(dataset, command, folder, cancelled) -> dict:
    """Require preserved gradients/grid, then save exact outputs and residuals."""
    with (
        produced_mif(Path(command["input_mif"])) as (source_fields, source_affine, source),
        produced_mif(Path(command["output_mif"])) as (fields, affine, values),
    ):
        if values.shape != source.shape or not np.allclose(
            affine, source_affine, atol=1e-7, rtol=0
        ):
            raise ValueError("Native operation changed MIF geometry or volume count")
        if not fields.get("dw_scheme") or fields["dw_scheme"] != source_fields.get("dw_scheme"):
            raise ValueError("Native operation lost or changed embedded gradients")
        step = command["step"]
        image_check = _save_native(dataset, values, affine, folder / f"{step}.nii", cancelled)
        scratch = folder / f"{step}-residual.array"
        with scratch.open("xb") as handle:
            handle.truncate(values.size * 4)
        residual = np.memmap(scratch, mode="r+", dtype=np.float32, shape=values.shape)
        stats = []
        try:
            for v in range(values.shape[3]):
                check_cancel(cancelled)
                residual[..., v] = source[..., v] - values[..., v]
                a = residual[..., v].astype(np.float64)
                stats.append(
                    {
                        "volume": v,
                        "b_value": float(dataset.gradients.bvals[v]),
                        "mean_difference": float(a.mean()),
                        "rms_difference": float(np.sqrt(np.mean(a * a))),
                    }
                )
            _save_native(dataset, residual, affine, folder / f"{step}-residual.nii", cancelled)
        finally:
            residual._mmap.close()
            scratch.unlink()
        check = {
            "status": "passed",
            "image": image_check,
            "embedded_gradient_rows": dataset.n_volumes,
            "per_volume": stats,
            "scientific_review": "required",
        }
    if command["step"] == "denoise":
        with produced_mif(folder / "noise.mif") as (_, affine, values):
            if (
                values.ndim != 3
                or not np.isfinite(values).all()
                or np.min(values) < 0
                or np.max(values) <= 0
            ):
                raise ValueError("Invalid MP-PCA noise estimates; do not force positive values")
            check["noise"] = _save_native(dataset, values, affine, folder / "noise.nii", cancelled)
    return check


class NativeStep(execution.MRtrixStep):
    """Native operations use the existing validate/run/qc adapter interface."""

    def validate(self) -> None:
        """Require immutable declared inputs and exclusively new output paths."""
        if self.name not in {"denoise", "gibbs", "mask", "export_mask"}:
            raise ValueError("Unsupported native step")
        if any(not path.is_file() for path in self.inputs):
            raise ValueError(f"Missing {self.name} input")
        if any(path.exists() or path.is_symlink() for path in self.outputs):
            raise ValueError(f"A {self.name} output exists; overwrite refused")

    def qc(self, dataset, folder, cancelled, progress) -> dict:
        """Retain validation and scientific-review outcomes as separate facts."""
        if self.name in {"denoise", "gibbs"}:
            return _verify_native(
                dataset,
                {
                    "step": self.name,
                    "input_mif": str(self.inputs[0]),
                    "output_mif": str(self.outputs[0]),
                },
                folder,
                cancelled,
            )
        if self.name == "export_mask":
            return _validate_mask(dataset, folder, cancelled)
        return {"status": "pending_export_validation"}


def native_adapter(command: dict, folder: Path, parameters: dict) -> NativeStep:
    """Bind a recompiled command to the shared adapter contract."""
    name = command["step"]
    # Every supported command has an explicit input/output positional pair.
    inputs = (Path(command["argv"][1]),)
    outputs = [Path(command["argv"][2])]
    if name == "denoise":
        outputs += [folder / "noise.mif", folder / "noise.nii"]
    if name in {"denoise", "gibbs"}:
        outputs += [folder / f"{name}.nii", folder / f"{name}-residual.nii"]
    if name == "export_mask":
        outputs += [folder / "mask.nii.gz"]
    return NativeStep(name, tuple(command["argv"]), inputs, tuple(outputs), parameters)


def execute_native(
    dataset, plan, *, approval, cancelled=lambda: False, progress=lambda _: None
) -> dict:
    """Reuse verified conversion, then execute native steps in the same exclusive run."""
    if approval != plan.digest:
        raise ValueError("Approve the exact displayed plan")
    p = plan.to_dict()
    params = p["parameters"]
    current = prepare_native(
        dataset,
        ProcessingHistory.from_dict(p["history"]),
        tuple(p["operations"]),
        Path(p["output_directory"]),
        clean_scale=params["clean_scale"],
        nthreads=params["nthreads"],
        tool_directory=Path(p["tools"]["mrconvert"]["path"]).parent,
        cancelled=cancelled,
        progress=progress,
        **{k: params[k] for k in ["extent", "nshifts", "minW", "maxW"]},
        native_record=p.get("native_input_record"),
    )
    if current.digest != plan.digest:
        raise ValueError("Native plan evidence changed; prepare again")
    if (
        shutil.disk_usage(Path(p["output_directory"]).parent).free
        < p["resources"]["estimated_peak_additional_bytes"]
    ):
        raise ValueError(
            "Insufficient free disk for the reviewed native outputs and temporary buffers"
        )
    base = ExecutionPlan(p["conversion_plan"])
    result = execute_plan(
        dataset, base, approval=base.digest, cancelled=cancelled, progress=progress
    )
    folder = Path(p["output_directory"])
    manifest = {
        "schema_version": 1,
        "plan": p,
        "approval": {"digest": approval},
        "started_at": _now(),
        "status": "running",
        "steps": list(result["steps"]),
        "validation": dict(result["validation"]),
        "human_qc": "pending",
        "outputs": {},
    }
    path = folder / "native-manifest.json"

    def checkpoint():
        """Atomic current state plus immutable conversion manifest remain distinct."""
        temporary = folder / ".native-manifest.tmp"
        with temporary.open("w") as stream:
            json.dump(manifest, stream, indent=2, allow_nan=False)
        os.replace(temporary, path)

    try:
        if result["status"] == "cancelled":
            raise ProcessingCancelled(result.get("error", "Conversion cancelled"))
        if result["status"] != "validated":
            raise ValueError(
                "Conversion prerequisite failed: " + result.get("error", result["status"])
            )
        if "gibbs" in p["operations"]:
            with produced_mif(folder / "dwi.mif") as (_, affine, values):
                plane = params["gibbs"]
                if list(values.shape) != list(plane["expected_mif_shape"]) or not np.allclose(
                    affine, plane["expected_mif_affine"], atol=1e-6, rtol=0
                ):
                    raise ValueError("Prepared Gibbs axes do not match actual converted geometry")
        environment = dict(os.environ) | p["environment"]["overrides"]
        for command in p["commands"][1:]:
            dataset.assert_current()
            step = {"step": command["step"], "status": "running", "started_at": _now()}
            manifest["steps"].append(step)
            checkpoint()
            progress(f"Running {command['step']}\n{command['display']}")
            adapter = native_adapter(command, folder, params)
            try:
                step.update(
                    adapter.run(
                        folder=folder,
                        environment=environment,
                        cancelled=cancelled,
                        progress=progress,
                    )
                )
            except Exception as exc:
                if hasattr(exc, "record"):
                    step.update(exc.record)
                raise
            step["status"] = "command_completed"
            checkpoint()
            if command["step"] in {"denoise", "gibbs"}:
                manifest["validation"][command["step"]] = adapter.qc(
                    dataset, folder, cancelled, progress
                )
                step["status"] = "validated"
                from .checkpoints import record_native_checkpoint

                record_native_checkpoint(dataset, folder, manifest, command["step"])
            elif command["step"] == "export_mask":
                manifest["validation"]["mask"] = adapter.qc(dataset, folder, cancelled, progress)
                step["status"] = "validated"
                next(s for s in manifest["steps"] if s["step"] == "mask")["status"] = "validated"
            step["finished_at"] = _now()
            checkpoint()
        with (folder / "native.bval").open("x") as stream:
            np.savetxt(stream, dataset.gradients.bvals[None], fmt="%.17g")
        with (folder / "native.bvec").open("x") as stream:
            np.savetxt(stream, dataset.gradients.bvecs.T, fmt="%.17g")
        if not np.array_equal(
            np.loadtxt(folder / "native.bval"), dataset.gradients.bvals
        ) or not np.array_equal(np.loadtxt(folder / "native.bvec"), dataset.gradients.bvecs.T):
            raise ValueError("Native exported gradients changed values or volume association")
        for name in p["outputs"]:
            manifest["outputs"][name] = {
                "sha256": hash_file(folder / name, cancelled),
                "bytes": (folder / name).stat().st_size,
            }
        manifest["status"] = "validated_pending_qc"
    except (ProcessingCancelled, KeyboardInterrupt) as exc:
        manifest.update(status="cancelled", error=str(exc))
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            manifest["inputs_unchanged"] = all(
                hash_file(Path(p["input_paths"][role])) == row["sha256"]
                and SourceFingerprint(**row["fingerprint"]).unchanged(p["input_paths"][role])
                for role, row in p["inputs"].items()
            )
            if not manifest["inputs_unchanged"]:
                manifest.update(status="failed", error="Original input content changed")
        except Exception as exc:
            manifest.update(
                status="failed", error=f"Preservation check failed: {exc}", inputs_unchanged=False
            )
        for step in manifest["steps"]:
            if step["status"] in {"running", "command_completed"}:
                step["status"] = (
                    manifest["status"]
                    if manifest["status"] in {"failed", "cancelled"}
                    else step["status"]
                )
        manifest["finished_at"] = _now()
        checkpoint()
    if manifest["status"] == "validated_pending_qc":
        try:
            export_native_catalog(dataset, folder, manifest)
        except Exception as exc:
            # Verified processing is distinct from report export. Preserve the
            # failure and enable a metadata-only repair; never rerun MRI for it.
            record = {
                "status": "failed",
                "step": "qc_export",
                "error": repr(exc),
                "processing_manifest": str(path),
                "scientific_review": "required",
            }
            _publish_json(folder / "qc-export-failure.json", record)
            manifest["qc_export_error"] = record["error"]
            progress(f"Processing verified; QC export needs repair: {exc}")
    return manifest


def export_native_catalog(dataset, folder: Path, manifest: dict) -> Path:
    """Index generated checks/comparisons for the existing QC workspace, no rerun."""
    from .qc import artifact, load_catalog

    stages = []
    previous = {
        "dwi": str(dataset.paths.dwi),
        "bval": str(dataset.paths.bval),
        "bvec": str(dataset.paths.bvec),
    }
    for command in manifest["plan"]["commands"]:
        name = command["step"]
        if name not in {"denoise", "gibbs"}:
            continue
        metrics = folder / f"{name}-verification.json"
        _publish_json(metrics, manifest["validation"][name])
        current = {
            "dwi": str(folder / f"{name}.nii"),
            "bval": str(folder / "native.bval"),
            "bvec": str(folder / "native.bvec"),
        }
        sources = {"before": dict(previous), "after": dict(current)}
        for source in sources.values():
            source["sha256"] = {
                key: hash_file(Path(source[key])) for key in ["dwi", "bval", "bvec"]
            }
        comparison = folder / f"{name}-comparison.json"
        _publish_json(
            comparison,
            {
                "notice": "Native before/after; inspect for anatomical signal leakage, ringing and contrast changes. Scientific review required.",
                "sources": sources,
                "modes": [
                    {
                        "label": f"Before / after {name}",
                        "left": "before",
                        "right": "after",
                        "pairs": [[v, v] for v in range(dataset.n_volumes)],
                        "description": "Same native grid and unchanged diffusion encodings; no resampling",
                    }
                ],
            },
        )
        stages.append(
            {
                "id": name,
                "title": name,
                "execution": "completed",
                "technical": "passed",
                "automated_qc": "pending",
                "scientific_review": "required",
                "findings": [
                    "Native geometry, finite values, gradients and exact NIfTI export verified. Inspect residuals before scientific acceptance.",
                    "Unknown scanner filtering and partial-Fourier limitations remain applicable.",
                ],
                "artifacts": [
                    artifact(
                        comparison,
                        "comparison",
                        "Native before/after with residual difference view",
                    ),
                    artifact(
                        metrics,
                        "metrics",
                        "Saved full-output verification and per-volume differences",
                    ),
                    artifact(folder / "logs" / f"{name}.log", "log", "Exact local MRtrix log"),
                    artifact(
                        folder / "native-manifest.json",
                        "history",
                        "Frozen commands, source hashes and processing history",
                    ),
                ],
            }
        )
        previous = current
    if "mask" in manifest["validation"]:
        stages.append(
            {
                "id": "mask",
                "title": "Brain mask",
                "execution": "completed",
                "technical": "passed",
                "automated_qc": "pending",
                "scientific_review": "required",
                "findings": [
                    "Inspect mask.nii.gz using the existing viewer overlay; coverage is not established by binary validation."
                ],
                "artifacts": [
                    artifact(
                        folder / "native-manifest.json",
                        "metrics",
                        "Mask structural verification and history",
                    )
                ],
            }
        )
    destination = folder / "qc-catalog.json"
    _publish_json(
        destination,
        {
            "schema_version": 1,
            "kind": "dmri_qc_catalog",
            "identity": {
                "subject": "User-loaded dataset; subject identity not inferred",
                "input": str(dataset.paths.dwi),
            },
            "stages": stages,
        },
    )
    load_catalog(destination)
    return destination
