"""Reviewed MRtrix conversion/masking plans and source-preserving execution.

Python usage: prepare_execution(dataset, history, ('mask',), new_folder), inspect
its JSON, then execute_plan(dataset, plan, approval=plan.digest). The explicit
digest is an approval receipt, not an authentication signature. GUI/CLI require
a separate review action. No imported arbitrary command is ever executed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np

from dmri_studio import __version__

from ._mif_checks import produced_mif, rearrange, voxel_mapping
from .dataset import DwiDataset, SourceFingerprint
from .gradients import GradientTable, mrtrix_world_scheme
from .process import ProcessingCancelled, check_cancel, run_command
from .tool_versions import MRTRIX_TESTED_RELEASES
from .validation import freeze_json, json_compatible
from .workflow import ProcessingHistory, build_plan

# Only this installed release has been exercised locally for these exact commands.
# Adding a release requires adapter acceptance, not a guessed version range. The
# 3.0.5 FSL export path is excluded; raw gradients are inspected in the MIF header.
TESTED_RELEASES = MRTRIX_TESTED_RELEASES
SOURCE_COMMIT = "e919e656a72c0b0eee9723f2ed7dbe571e5a8ac2"


def _now() -> str:
    """Timestamp actual processing events in UTC."""
    return datetime.now(UTC).isoformat()


def hash_file(path: Path, cancelled: Callable[[], bool] = lambda: False) -> str:
    """Hash complete bytes in bounded chunks, checking identity before and after."""
    before = SourceFingerprint.capture(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            check_cancel(cancelled)
            digest.update(chunk)
    check_cancel(cancelled)
    if not before.unchanged(path):
        raise ValueError(f"File changed during hashing: {path}")
    return digest.hexdigest()


def detect_mrtrix(
    *,
    cancelled: Callable[[], bool] = lambda: False,
    tool_directory: Path | None = None,
    names: tuple[str, ...] = ("mrconvert", "dwi2mask"),
) -> dict:
    """Locate required executables and bind versions to complete binary hashes.

    Tool discovery executes only the bounded -version query (10 second timeout).
    Unknown/mixed releases remain unusable, with no automatic installation.
    """
    tools = {}
    for name in names:
        check_cancel(cancelled)
        candidate = str(tool_directory / name) if tool_directory else shutil.which(name)
        if not candidate or not Path(candidate).is_file():
            raise ValueError(
                f"MRtrix {name} was not found. Install MRtrix3 or select its bin directory."
            )
        path = Path(candidate).resolve(strict=True)
        text = subprocess.run(
            [str(path), "-version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
        match = re.search(rf"==\s*{name}\s+(\d+\.\d+\.\d+)\s*==", text)
        if not match or match[1] not in TESTED_RELEASES:
            raise ValueError(
                f"{name} reports an unvalidated release. This adapter is tested with {', '.join(sorted(TESTED_RELEASES))}; found: {text.splitlines()[0] if text else 'no version'}"
            )
        tools[name] = {
            "path": str(path),
            "version": match[1],
            "version_output": text,
            "fingerprint": SourceFingerprint.capture(path).to_dict(),
            "sha256": hash_file(path, cancelled),
        }
    if len({item["version"] for item in tools.values()}) != 1:
        raise ValueError("The MRtrix executables report different releases.")
    return tools


def _environment_contract(cancelled: Callable[[], bool]) -> dict:
    """Bind inherited MRtrix configuration without exposing unrelated secrets."""
    config_files = {}
    for path in (Path("/etc/mrtrix.conf"), Path.home() / ".mrtrix.conf"):
        if path.exists():
            config_files[str(path)] = hash_file(path, cancelled)
        else:
            config_files[str(path)] = None
    return {
        "mrtrix_variables": {
            k: v for k, v in sorted(os.environ.items()) if k.startswith("MRTRIX_")
        },
        "config_files": config_files,
        "overrides": {"MRTRIX_QUIET": "", "MRTRIX_OVERWRITE": ""},
    }


def _destination(dataset: DwiDataset, folder: Path) -> Path:
    """Require a NEW run directory outside every original input directory."""
    lexical = folder.expanduser().absolute()
    if lexical.exists() or lexical.is_symlink():
        raise ValueError(
            "Run folder already exists. Choose a new folder; nothing will be overwritten."
        )
    if not lexical.parent.is_dir():
        raise ValueError("The run folder's parent directory must already exist.")
    resolved = lexical.resolve()
    for path in dataset.paths.supplied().values():
        parent = path.resolve().parent
        if resolved == parent or parent in resolved.parents:
            raise ValueError("Choose a run folder outside the original input directories.")
    return resolved


@dataclass(frozen=True)
class ExecutionPlan:
    """Deeply immutable command/validation contract approved by its digest."""

    payload: Any

    def __post_init__(self) -> None:
        """Freeze all nested input, parameter, command and evidence fields."""
        object.__setattr__(self, "payload", freeze_json(self.payload))

    def to_dict(self) -> dict:
        """Detach data before rendering/export; clients cannot change approval."""
        return json_compatible(self.payload)

    def to_json(self) -> str:
        """Produce strict, human-readable JSON."""
        return json.dumps(self.to_dict(), indent=2, allow_nan=False)

    @property
    def digest(self) -> str:
        """Bind approval to every field, including binary hashes and outputs."""
        raw = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode()).hexdigest()


def prepare_execution(
    dataset: DwiDataset,
    history: ProcessingHistory,
    operations: tuple[str, ...],
    output: Path,
    *,
    clean_scale: float = 2.0,
    nthreads: int = 4,
    tool_directory: Path | None = None,
    extent: int = 5,
    nshifts: int = 20,
    minW: int = 1,
    maxW: int = 3,
    fitting_mask: Path | None = None,
    correction_record: Path | None = None,
    correction_gate: Path | None = None,
    ants_directory: Path | None = None,
    references: tuple[Path, ...] = (),
    bids_root: Path | None = None,
    native_record: Path | None = None,
    fsl_directory: Path | None = None,
    anatomy_options: dict | None = None,
    combination_options: dict | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[str], None] = lambda _: None,
) -> ExecutionPlan:
    """Research-approved adapter preview; creates no output directory or images.

    This phase supports conversion and independent masking only. Native coded,
    orthogonal geometry in millimetres is required; unsupported geometry is
    refused without changing the working generic viewer. Float32 conversion and
    MRtrix vector normalization are explicit; b-value rescaling is disabled.
    """
    from .storage import require_local_processing

    require_local_processing(
        [output, *dataset.paths.supplied().values(), *references, fitting_mask]
    )
    for options in (anatomy_options or {}, combination_options or {}):
        require_local_processing(
            value
            for key, value in options.items()
            if key
            in {
                "t1",
                "inv2",
                "anatomy_manifest",
                "anatomy_review",
                "diffusion_manifest",
                "parent_manifest",
                "pairing_spec",
                "hcp_helper",
            }
            and isinstance(value, (str, Path))
        )
    if "combine" in operations:
        from .combination import prepare_combination

        return prepare_combination(
            dataset,
            history,
            operations,
            output,
            combination_options=combination_options,
            fsl_directory=fsl_directory,
            tool_directory=tool_directory,
            nthreads=nthreads,
            cancelled=cancelled,
            progress=progress,
        )
    if {"anatomy", "alignment"} & set(operations):
        from .anatomy import prepare_anatomy

        return prepare_anatomy(
            dataset,
            history,
            operations,
            output,
            anatomy_options=anatomy_options,
            fsl_directory=fsl_directory,
            tool_directory=tool_directory,
            nthreads=nthreads,
            cancelled=cancelled,
            progress=progress,
        )
    if "correction" in operations:
        from .correction import prepare_correction

        return prepare_correction(
            dataset,
            history,
            operations,
            output,
            references=references,
            bids_root=bids_root,
            native_record=native_record,
            fsl_directory=fsl_directory,
            tool_directory=tool_directory,
            nthreads=nthreads,
            cancelled=cancelled,
            progress=progress,
        )
    if "bias" in operations:
        from .bias_correction import prepare_bias

        return prepare_bias(
            dataset,
            history,
            operations,
            output,
            fitting_mask=fitting_mask,
            correction_record=correction_record,
            correction_gate=correction_gate,
            ants_directory=ants_directory,
            tool_directory=tool_directory,
            nthreads=nthreads,
            cancelled=cancelled,
            progress=progress,
        )
    if {"denoise", "gibbs"} & set(operations):
        from .native_processing import prepare_native

        return prepare_native(
            dataset,
            history,
            operations,
            output,
            clean_scale=clean_scale,
            nthreads=nthreads,
            tool_directory=tool_directory,
            cancelled=cancelled,
            progress=progress,
            extent=extent,
            nshifts=nshifts,
            minW=minW,
            maxW=maxW,
            native_record=native_record,
        )
    proposal = build_plan(dataset, history, operations).to_dict()
    if proposal["status"] != "eligible":
        raise ValueError("Only an eligible, confirmed proposal can be prepared for execution.")
    if set(operations) - {"convert", "mask"}:
        raise ValueError("Execution currently supports conversion and independent masking only.")
    if (
        isinstance(clean_scale, bool)
        or not np.isfinite(clean_scale)
        or clean_scale < 0
        or clean_scale > 10
        or int(clean_scale) != clean_scale
    ):
        raise ValueError("Mask clean scale must be an integer from 0 to 10 voxels.")
    if type(nthreads) is not int or not 1 <= nthreads <= 64:
        raise ValueError("Thread count must be an integer from 1 to 64.")
    if dataset.spatial_units != "mm" or dataset.affine_source == "fallback":
        raise ValueError("This execution adapter requires coded geometry with explicit mm units.")
    rotation = dataset.affine[:3, :3] / np.linalg.norm(dataset.affine[:3, :3], axis=0)
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5, rtol=0):
        raise ValueError(
            "Sheared geometry requires a separate MRtrix conversion review; no resampling is permitted here."
        )
    destination = _destination(dataset, output)
    progress("Checking MRtrix executables and versions…")
    tools = detect_mrtrix(cancelled=cancelled, tool_directory=tool_directory)
    inputs = {}
    for role, path in dataset.paths.supplied().items():
        progress(f"Hashing original {role}: {path.name}…")
        inputs[role] = {
            "fingerprint": dataset.fingerprints[role].to_dict(),
            "sha256": hash_file(path, cancelled),
        }
    dataset.assert_current()
    # The loaded metadata is a cheap snapshot. A content change with restored
    # mtime must not bind new bytes to an old interpretation of the gradients.
    gradients = GradientTable.from_files(
        dataset.paths.bval, dataset.paths.bvec, layout=dataset.gradients.source_layout
    )
    if not np.array_equal(gradients.bvals, dataset.gradients.bvals) or not np.array_equal(
        gradients.bvecs, dataset.gradients.bvecs
    ):
        raise ValueError(
            "Gradient content changed since loading. Reload the input before planning."
        )
    parameters = {
        "clean_scale": int(clean_scale),
        "nthreads": nthreads,
        "b0_max": dataset.validation.policy.b0_max,
        "datatype": "float32le",
        "strides": "-1,2,3,4",
        "bvalue_scaling": "no",
    }
    commands = _commands(dataset, tools, destination, parameters, "mask" in operations)
    outputs = ["dwi.mif"] + (
        ["mask.mif", "mask-export.nii", "mask.nii.gz"] if "mask" in operations else []
    )
    return ExecutionPlan(
        {
            "schema_version": 1,
            "kind": "mrtrix_execution_plan",
            "execution_available": True,
            "approved_for_execution": False,
            "runtime": {
                "dmri_studio": __version__,
                "python": sys.version,
                "numpy": np.__version__,
                "nibabel": nib.__version__,
                "platform": sys.platform,
            },
            "inputs": inputs,
            "input_paths": {role: str(path) for role, path in dataset.paths.supplied().items()},
            "input_geometry": {
                "shape": list(dataset.shape),
                "affine": dataset.affine.tolist(),
                "affine_source": dataset.affine_source,
                "units": dataset.spatial_units,
                "coordinate_space": "Not established from filenames or form codes",
            },
            "history": history.to_dict(),
            "operations": list(operations),
            "parameters": parameters,
            "tools": tools,
            "environment": _environment_contract(cancelled),
            "output_directory": str(destination),
            "outputs": outputs,
            "commands": commands,
            "gradient_staging": {
                "bvals": dataset.gradients.bvals.tolist(),
                "bvecs_fsl_3xN": dataset.gradients.bvecs.T.tolist(),
                "source_layout": dataset.gradients.source_layout,
            },
            "internal_actions": [
                "Write lossless 17-digit FSL gradient text in the new run directory using the explicitly validated layout; preserve originals.",
                "Validate all converted values volume by volume, physical voxel mapping, finite values and embedded world-space gradients before masking.",
            ]
            + (
                [
                    "Validate the full binary MIF and exported mask; reindex by permutation/flip to the original native grid, with no interpolation; write uint8 mask.nii.gz.",
                    "Require human anatomical QC acceptance/rejection separately from structural checks.",
                ]
                if "mask" in operations
                else []
            ),
            "scientific_limits": [
                "Source history remains declared/inferred; command success and structural checks do not establish scientific benefit or anatomical validity.",
                "MRtrix normalizes directions and converts FSL axes into world coordinates. Original b-values are retained; no FSL gradient export is used.",
                "Float32 storage is explicit; higher-precision source intensities may be rounded. Every output is compared against the declared float32 interpretation.",
            ],
            "implementation_source": f"https://github.com/MRtrix3/mrtrix3/tree/{SOURCE_COMMIT}",
        }
    )


def _commands(dataset: DwiDataset, tools: dict, output: Path, parameters: dict, mask: bool) -> list:
    """Compile known commands, with absolute paths and explicit scientific options."""
    common = [
        "-nthreads",
        str(parameters["nthreads"]),
        "-config",
        "BZeroThreshold",
        str(parameters["b0_max"]),
    ]
    convert = [
        tools["mrconvert"]["path"],
        str(dataset.paths.dwi),
        str(output / "dwi.mif"),
        "-fslgrad",
        str(output / "inputs.bvecs"),
        str(output / "inputs.bvals"),
        "-datatype",
        parameters["datatype"],
        "-strides",
        parameters["strides"],
        "-bvalue_scaling",
        parameters["bvalue_scaling"],
        *common,
    ]
    result = [{"step": "convert", "argv": convert, "display": shlex.join(convert)}]
    if mask:
        argv = [
            tools["dwi2mask"]["path"],
            str(output / "dwi.mif"),
            str(output / "mask.mif"),
            "-clean_scale",
            str(parameters["clean_scale"]),
            *common,
        ]
        export = [
            tools["mrconvert"]["path"],
            str(output / "mask.mif"),
            str(output / "mask-export.nii"),
            "-datatype",
            "uint8",
            "-clear_property",
            "dw_scheme",
            *common,
        ]
        result.extend(
            [
                {"step": "mask", "argv": argv, "display": shlex.join(argv)},
                {"step": "export_mask", "argv": export, "display": shlex.join(export)},
            ]
        )
    return result


def save_execution_plan(plan: ExecutionPlan, path: Path) -> Path:
    """Publish a NEW plan JSON atomically; never overwrite an existing file."""
    path = path.expanduser().absolute()
    _publish_json(path, {"plan": plan.to_dict(), "approval_digest": plan.digest})
    return path


def _publish_json(path: Path, payload: dict) -> None:
    """Atomic exclusive publication for approval and QC documents."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _write_manifest(folder: Path, manifest: dict) -> None:
    """Replace only this run's own manifest after a complete JSON write."""
    temporary = folder / "manifest.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(folder / "manifest.json")


@dataclass(frozen=True)
class MRtrixStep:
    """External-step adapter with inputs, outputs, parameters, validate/run/qc.

    Commands come from the recompiled plan, not free-form user text. This common
    interface can join a backend registry as more methods are authorized.
    """

    name: str
    argv: tuple[str, ...]
    inputs: tuple[Path, ...]
    outputs: tuple[Path, ...]
    parameters: Any

    def validate(self) -> None:
        """Require every input and refuse pre-existing derivatives."""
        if self.name not in {"convert", "mask", "export_mask"}:
            raise ValueError("Unsupported MRtrix adapter step.")
        if any(not path.is_file() for path in self.inputs):
            raise ValueError(f"Missing {self.name} step input.")
        if any(path.exists() or path.is_symlink() for path in self.outputs):
            raise ValueError(f"A {self.name} output already exists; overwrite is refused.")

    def run(
        self, *, folder: Path, environment: dict, cancelled: Callable, progress: Callable
    ) -> dict:
        """Delegate exact argv and cancellation to the shared shell-free runner."""
        self.validate()
        return run_command(
            self.argv,
            cwd=folder,
            log=folder / "logs" / f"{self.name}.log",
            environment=environment,
            cancelled=cancelled,
            progress=progress,
        )

    def qc(
        self, dataset: DwiDataset, folder: Path, cancelled: Callable, progress: Callable
    ) -> dict:
        """Check the output contract separately from the executable's exit status."""
        if self.name == "convert":
            return _validate_conversion(dataset, folder / "dwi.mif", cancelled, progress)
        if self.name == "export_mask":
            return _validate_mask(dataset, folder, cancelled)
        return {"status": "pending_export_validation"}


def _adapter(command: dict, folder: Path, dataset: DwiDataset, parameters: dict) -> MRtrixStep:
    """Describe only the inputs/outputs of the three compiled step types."""
    inputs, outputs = {
        "convert": (
            (dataset.paths.dwi, folder / "inputs.bvecs", folder / "inputs.bvals"),
            (folder / "dwi.mif",),
        ),
        "mask": ((folder / "dwi.mif",), (folder / "mask.mif",)),
        "export_mask": (
            (folder / "mask.mif",),
            (folder / "mask-export.nii", folder / "mask.nii.gz"),
        ),
    }[command["step"]]
    return MRtrixStep(
        command["step"], tuple(command["argv"]), inputs, outputs, freeze_json(parameters)
    )


def execute_plan(
    dataset: DwiDataset,
    plan: ExecutionPlan,
    *,
    approval: str,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[str], None] = lambda _: None,
) -> dict:
    """Recompile/revalidate the reviewed plan, run adapters and record all states.

    No command is run before exact approval, content/binary/config revalidation
    and atomic new-directory creation. Failure/cancellation retains diagnostic
    files and an incomplete manifest; there is no existence-based resume.
    """
    if plan.payload.get("kind") == "mrtrix_sift2_execution_plan":
        from .scientific.sift2 import SIFT2Adapter

        return SIFT2Adapter().run(plan, approval=approval, cancelled=cancelled, progress=progress)
    if plan.payload.get("kind") == "registration_execution_plan":
        from .scientific.registration import RegistrationAdapter

        return RegistrationAdapter().run(plan, approval=approval, cancelled=cancelled, progress=progress)
    if plan.payload.get("kind") == "mrtrix_anatomy_execution_plan":
        from .scientific.anatomy import AnatomyAdapter

        return AnatomyAdapter().run(plan, approval=approval, cancelled=cancelled, progress=progress)
    if plan.payload.get("kind") == "mrtrix_dti_execution_plan":
        from .scientific.dti import DTIAdapter

        return DTIAdapter().run(plan, approval=approval, cancelled=cancelled, progress=progress)
    if plan.payload.get("kind") == "mrtrix_csd_execution_plan":
        from .scientific.csd import CSDAdapter

        return CSDAdapter().run(plan, approval=approval, cancelled=cancelled, progress=progress)
    if plan.payload.get("kind") == "mrtrix_connectome_execution_plan":
        from .scientific.connectome import ConnectomeAdapter

        return ConnectomeAdapter().run(
            plan, approval=approval, cancelled=cancelled, progress=progress
        )
    if plan.payload.get("kind") == "mrtrix_tracking_execution_plan":
        from .scientific.tracking import TrackingAdapter

        return TrackingAdapter().run(
            plan, approval=approval, cancelled=cancelled, progress=progress
        )
    if plan.payload.get("kind") == "scientific_workflow_proposal":
        raise ValueError(
            "Scientific workflow is a review proposal; no accepted execution adapters are available"
        )
    if plan.payload.get("kind") == "hcp_matched_combination_plan":
        from .combination import execute_combination

        return execute_combination(
            dataset, plan, approval=approval, cancelled=cancelled, progress=progress
        )
    if plan.payload.get("kind") == "fsl_anatomy_plan":
        from .anatomy import execute_anatomy

        return execute_anatomy(
            dataset, plan, approval=approval, cancelled=cancelled, progress=progress
        )
    if plan.payload.get("kind") == "fsl_correction_plan":
        from .correction import execute_correction

        return execute_correction(
            dataset, plan, approval=approval, cancelled=cancelled, progress=progress
        )
    if plan.payload.get("kind") == "mrtrix_ants_bias_plan":
        from .bias_correction import execute_bias

        return execute_bias(
            dataset, plan, approval=approval, cancelled=cancelled, progress=progress
        )
    if plan.payload.get("kind") == "mrtrix_native_execution_plan":
        from .native_processing import execute_native

        return execute_native(
            dataset, plan, approval=approval, cancelled=cancelled, progress=progress
        )
    if approval != plan.digest:
        raise ValueError("Approve the exact displayed plan digest before running.")
    payload = plan.to_dict()
    history = ProcessingHistory.from_dict(payload["history"])
    parameters = payload["parameters"]
    current = prepare_execution(
        dataset,
        history,
        tuple(payload["operations"]),
        Path(payload["output_directory"]),
        clean_scale=parameters["clean_scale"],
        nthreads=parameters["nthreads"],
        tool_directory=Path(payload["tools"]["mrconvert"]["path"]).parent,
        cancelled=cancelled,
        progress=progress,
    )
    if current.digest != plan.digest:
        raise ValueError(
            "Inputs, evidence, binaries, configuration or parameters changed. Review a fresh plan."
        )
    folder = Path(payload["output_directory"])
    check_cancel(cancelled)
    folder.mkdir(mode=0o700, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "status": "running",
        "started_at": _now(),
        "plan": payload,
        "approval": {"digest": approval, "recorded_at": _now()},
        "steps": [],
        "validation": {},
        "human_qc": "pending",
        "inputs_unchanged": None,
    }
    _write_manifest(folder, manifest)
    (folder / "logs").mkdir()
    try:
        with (folder / "inputs.bvals").open("x") as handle:
            np.savetxt(handle, dataset.gradients.bvals[None, :], fmt="%.17g")
        with (folder / "inputs.bvecs").open("x") as handle:
            np.savetxt(handle, dataset.gradients.bvecs.T, fmt="%.17g")
        environment = dict(os.environ)
        environment.update(payload["environment"]["overrides"])
        for command in payload["commands"]:
            dataset.assert_current()
            adapter = _adapter(command, folder, dataset, parameters)
            step = {
                "step": command["step"],
                "argv": command["argv"],
                "started_at": _now(),
                "status": "running",
                "log": str(folder / "logs" / f"{command['step']}.log"),
            }
            manifest["steps"].append(step)
            _write_manifest(folder, manifest)
            progress(f"Running {command['step']}\n{command['display']}")
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
            step.update(status="command_completed", finished_at=_now())
            if command["step"] == "convert":
                progress("Checking every converted voxel, geometry and embedded gradient…")
                manifest["validation"]["conversion"] = adapter.qc(
                    dataset, folder, cancelled, progress
                )
                step["status"] = "validated"
            if command["step"] == "export_mask":
                progress("Checking mask structure and aligning it to the original native grid…")
                manifest["validation"]["mask"] = adapter.qc(dataset, folder, cancelled, progress)
                step["status"] = "validated"
                next(record for record in manifest["steps"] if record["step"] == "mask")[
                    "status"
                ] = "validated"
            _write_manifest(folder, manifest)
        manifest["outputs"] = {}
        for name in payload["outputs"]:
            progress(f"Hashing output {name}…")
            manifest["outputs"][name] = {
                "sha256": hash_file(folder / name, cancelled),
                "bytes": (folder / name).stat().st_size,
            }
        manifest["status"] = (
            "validated_pending_qc" if "mask" in payload["operations"] else "validated"
        )
    except (ProcessingCancelled, KeyboardInterrupt) as exc:
        manifest.update(status="cancelled", error=str(exc))
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        # Preserve a truthful identity result even after a failed/cancelled tool.
        # Cancellation no longer aborts this final source check; the app shows its
        # stage and waits safely, without approving any partial output.
        try:
            progress("Verifying original input hashes after the run…")
            unchanged = all(
                hash_file(Path(payload["input_paths"][role])) == data["sha256"]
                and SourceFingerprint(**data["fingerprint"]).unchanged(payload["input_paths"][role])
                for role, data in payload["inputs"].items()
            )
            manifest["inputs_unchanged"] = unchanged
            if not unchanged:
                manifest.update(
                    status="failed",
                    error="Original input identity changed during execution; reload and investigate.",
                )
        except Exception as exc:
            manifest.update(
                status="failed",
                error=f"Could not verify original inputs: {exc}",
                inputs_unchanged=False,
            )
        for step in manifest["steps"]:
            if step["status"] == "running":
                step.update(status=manifest["status"], finished_at=_now())
        manifest["finished_at"] = _now()
        _write_summary(folder, manifest)
        _write_manifest(folder, manifest)
    return manifest


def execute_artifact_task(
    plan: ExecutionPlan,
    *,
    approval: str,
    recompile: Callable,
    setup: Callable,
    step_factory: Callable,
    verify_step: Callable,
    verify: Callable,
    verify_sources: Callable,
    cancelled: Callable = lambda: False,
    progress: Callable = lambda _: None,
) -> dict:
    """Shared task executor for independently imported scientific artifacts.

    Extend the same approval, MRtrixStep/run_command, resource, cancellation and
    manifest infrastructure to non-DwiDataset inputs. Scientific adapters supply
    contracts and checks, never a second subprocess runner. Recompilation must
    derive commands from typed selections, not trust imported argv.
    """
    if approval != plan.digest:
        raise ValueError("Approve the exact displayed plan digest before running.")
    if recompile().digest != plan.digest:
        raise ValueError(
            "Inputs, binaries, configuration or parameters changed; review a fresh plan."
        )
    data = plan.to_dict()
    folder = Path(data["output_directory"])
    check_cancel(cancelled)
    folder.mkdir(mode=0o700, exist_ok=False)
    (folder / "logs").mkdir()
    manifest = dict(
        schema_version=1,
        status="running",
        started_at=_now(),
        plan=data,
        approval=dict(digest=approval, recorded_at=_now()),
        steps=[],
        validation={},
        human_qc="pending",
        inputs_unchanged=None,
    )
    _write_manifest(folder, manifest)
    try:
        setup(folder)
        environment = dict(os.environ)
        environment.update(data["environment"]["overrides"])
        for key in data["environment"].get("unset", ()):
            environment.pop(key, None)
        for command in data["commands"]:
            check_cancel(cancelled)
            adapter = step_factory(command)
            step = dict(
                step=command["step"],
                argv=command["argv"],
                started_at=_now(),
                status="running",
                log=str(folder / "logs" / f"{command['step']}.log"),
            )
            manifest["steps"].append(step)
            _write_manifest(folder, manifest)
            progress("Running " + command["display"])
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
            step.update(status="command_completed", finished_at=_now())
            try:
                step["verification"] = verify_step(command)
            except ProcessingCancelled:
                step["status"] = "verification_cancelled"
                raise
            except Exception as exc:
                step.update(
                    status="verification_failed",
                    verification={"technical": "failed", "error": str(exc)},
                )
                raise
            step["status"] = "validated"
            _write_manifest(folder, manifest)
        manifest["validation"] = verify(folder)
        manifest["outputs"] = {
            name: dict(
                sha256=hash_file(folder / name, cancelled), bytes=(folder / name).stat().st_size
            )
            for name in data["outputs"]
        }
        manifest["status"] = "validated_pending_qc"
    except (ProcessingCancelled, KeyboardInterrupt) as exc:
        manifest.update(status="cancelled", error=str(exc))
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            progress("Verifying original input hashes after the run…")
            verify_sources()
            manifest["inputs_unchanged"] = True
        except Exception as exc:
            manifest.update(
                status="failed", error=f"Original inputs changed: {exc}", inputs_unchanged=False
            )
        for step in manifest["steps"]:
            if step["status"] == "running":
                step.update(status=manifest["status"], finished_at=_now())
        manifest["finished_at"] = _now()
        _write_summary(folder, manifest)
        _write_manifest(folder, manifest)
    return manifest


def _write_summary(folder: Path, manifest: dict) -> None:
    """Write the readable counterpart of completed, failed or cancelled provenance."""
    lines = [
        "dMRI Studio — MRtrix execution summary",
        f"Status: {manifest['status']}",
        f"Started: {manifest['started_at']} · Finished: {manifest['finished_at']}",
        f"Output directory: {folder}",
        f"Original inputs unchanged: {manifest['inputs_unchanged']}",
        f"Approval digest: {manifest['approval']['digest']}",
        "Anatomical QC is separate: see qc-decision.json when a reviewer creates it.",
    ]
    if "error" in manifest:
        lines.append(f"Failure/cancellation: {manifest['error']}")
        lines.append(
            "Review diagnostic logs. Reload changed inputs or correct the reported problem, then prepare a NEW run folder; incomplete files cannot be resumed/reused in this phase."
        )
    lines += [f"Parameters: {manifest['plan']['parameters']}"]
    lines += [
        f"{name}: {tool['path']} · version {tool['version']}"
        for name, tool in manifest["plan"]["tools"].items()
    ]
    lines += [
        f"{step['step']}: {step['status']} · exit {step.get('return_code', 'not available')}\n{shlex.join(step['argv'])}\nLog: {step['log']}"
        for step in manifest["steps"]
    ]
    lines += [f"Validation: {json.dumps(manifest['validation'], allow_nan=False)}"]
    lines += manifest["plan"]["scientific_limits"]
    with (folder / "summary.txt").open("x", encoding="utf-8") as handle:
        handle.write("\n\n".join(lines) + "\n")
    manifest["summary_file"] = "summary.txt"


def _validate_conversion(
    dataset: DwiDataset, path: Path, cancelled: Callable, progress: Callable
) -> dict:
    """Full signal comparison after the explicitly declared float32 conversion."""
    with produced_mif(path) as (fields, affine, values):
        if len(values.shape) != 4 or values.shape[3] != dataset.n_volumes:
            raise ValueError("Converted DWI volume count differs from the input.")
        axes, flips = voxel_mapping(dataset.affine, dataset.shape, affine, values.shape)
        gradients = np.array(
            [[float(v) for v in row.split(",")] for row in fields.get("dw_scheme", [])]
        )
        expected = mrtrix_world_scheme(
            dataset.gradients.bvals,
            dataset.gradients.bvecs,
            convention="fsl_image_axes",
            affine=dataset.affine,
        )
        if (
            gradients.shape != expected.shape
            or not np.isfinite(gradients).all()
            or not np.allclose(gradients, expected, atol=1e-8, rtol=1e-8)
        ):
            raise ValueError(
                "Embedded gradients do not match the explicit FSL-to-world conversion."
            )
        checked = 0
        for volume in range(dataset.n_volumes):
            check_cancel(cancelled)
            original = rearrange(dataset.read_volume(volume), axes, flips)
            converted = values[..., volume]
            if not np.isfinite(converted).all() or not np.array_equal(original, converted):
                raise ValueError(
                    f"Converted values are nonfinite or differ from the declared float32 input at volume {volume + 1}."
                )
            checked += converted.size
            if volume % 16 == 0:
                progress(f"Validated volume {volume + 1} / {dataset.n_volumes}")
        return {
            "status": "passed",
            "values_compared": checked,
            "unequal_count": 0,
            "nonfinite_count": 0,
            "embedded_gradient_rows": len(gradients),
            "bvalues_preserved": True,
            "input_axes_in_output_order": list(axes),
            "flipped_output_axes": list(flips),
            "output_affine": affine.tolist(),
            "comparison": "Complete physical-voxel correspondence against float32 interpreted source; no interpolation",
        }


def _validate_mask(dataset: DwiDataset, folder: Path, cancelled: Callable) -> dict:
    """Full binary/payload/grid check and native-grid NIfTI for the existing viewer."""
    check_cancel(cancelled)
    image = nib.load(folder / "mask-export.nii", mmap="r")
    exported = np.asanyarray(image.dataobj)
    with produced_mif(folder / "mask.mif") as (fields, affine, values):
        if fields["datatype"] != ["Bit"] or values.ndim != 3 or exported.ndim != 3:
            raise ValueError("Expected a 3D binary MRtrix mask.")
        axes, flips = voxel_mapping(image.affine, image.shape, affine, values.shape)
        if not np.array_equal(rearrange(exported, axes, flips), values):
            raise ValueError("Mask export changed binary values.")
        unique = np.unique(values)
        if not np.array_equal(unique, [0, 1]):
            raise ValueError("Mask must contain both background (0) and brain candidates (1).")
        voxel_mapping(dataset.affine, dataset.shape, affine, values.shape)
        count = int(np.count_nonzero(values))
    axes, flips = voxel_mapping(image.affine, image.shape, dataset.affine, dataset.shape)
    native = np.ascontiguousarray(rearrange(exported, axes, flips), dtype=np.uint8)
    mask = nib.Nifti1Image(native, dataset.affine)
    mask.header.set_xyzt_units("mm")
    mask.set_sform(dataset.affine, code=1)
    mask.set_qform(dataset.affine, code=1)
    mask.header["descrip"] = b"dMRI Studio MRtrix mask; anatomical QC pending"
    check_cancel(cancelled)
    path = folder / "mask.nii.gz"
    if path.exists() or path.is_symlink():
        raise ValueError("Native mask destination appeared during execution.")
    nib.save(mask, path)
    # Verify actual saved file against the exact-grid viewer acceptance contract.
    from .overlay import OverlayDataset

    overlay = OverlayDataset.load(path, dataset)
    if tuple(overlay.image.shape) != dataset.shape[:3]:
        raise ValueError("Saved mask does not match the native grid.")
    return {
        "status": "passed",
        "nonzero_voxels": count,
        "volume_mm3": count * abs(float(np.linalg.det(dataset.affine[:3, :3]))),
        "unique_values": [0, 1],
        "native_grid": True,
        "resampling": False,
        "human_anatomical_qc": "pending",
    }


def record_qc(
    folder: Path,
    decision: str,
    reason: str,
    *,
    reviewer: str,
    cancelled: Callable[[], bool] = lambda: False,
) -> Path:
    """Append one human decision to a validated mask run, preserving its manifest.

    Evidence must remain byte-identical to the structurally validated outputs.
    A new exclusive QC record prevents overwriting a previous human assessment.
    """
    if decision not in {"accepted", "rejected"} or not reason.strip() or not reviewer.strip():
        raise ValueError("QC requires accept/reject, a review explanation and reviewer name.")
    manifest_path = folder / (
        "native-manifest.json" if (folder / "native-manifest.json").exists() else "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "validated_pending_qc" or "mask.nii.gz" not in manifest.get(
        "outputs", {}
    ):
        raise ValueError("Only a structurally validated mask run can receive anatomical QC.")
    for name, output in manifest["outputs"].items():
        if hash_file(folder / name, cancelled) != output["sha256"]:
            raise ValueError(
                f"Validated output changed: {name}. Repeat structural validation before QC."
            )
    destination = folder / "qc-decision.json"
    manifest_hash = hash_file(manifest_path, cancelled)
    check_cancel(cancelled)
    _publish_json(
        destination,
        {
            "schema_version": 1,
            "decision": decision,
            "reason": reason.strip(),
            "reviewer": reviewer.strip(),
            "reviewed_at": _now(),
            "manifest_sha256": manifest_hash,
            "approval_digest": manifest["approval"]["digest"],
            "scope": "Human anatomical review; does not certify upstream history or scientific benefit",
        },
    )
    return destination
