"""Evidence-bound, isolated TOPUP/EDDY CPU execution for a single PE acquisition.

Uses installed FSL CLI programs and the existing MRtrix conversion/masking engine.
This adapter deliberately does not concatenate acquisitions, infer calibrations,
repeat upstream corrections or substitute a study-specific shell count. See the
frozen plan for all applicability limits and the precise execution mechanism.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import nibabel as nib
import numpy as np

from . import execution
from .acquisition import phase_encoding_row, validate_epi_reference
from .acquisition_metadata import entities, inherited_metadata, slice_groups
from .correction_diagnostics import eddy_shells, finite_statistics, hyperparameter_blocks
from .dataset import DatasetPaths
from .execution import ExecutionPlan, _now, _publish_json, hash_file
from .loading import DatasetLoader
from .native_processing import _save_native
from .process import ProcessingCancelled, check_cancel, run_command
from .qc import _json
from .tool_versions import EDDY_TESTED_RELEASE
from .workflow import ProcessingHistory, build_plan


@dataclass(frozen=True)
class CorrectionStep:
    """Shared external-step interface; numerical checks remain explicit below."""

    name: str
    argv: tuple[str, ...]
    inputs: tuple[Path, ...]
    outputs: tuple[Path, ...]
    parameters: dict

    def validate(self, folder: Path) -> None:
        """Only staged inputs and new outputs are legal for these local tools."""
        for path in (*self.inputs, *self.outputs):
            if not path.resolve().is_relative_to(folder.resolve()):
                raise ValueError("Correction tool files must stay inside the new attempt")
        if any(not path.is_file() for path in self.inputs):
            raise ValueError(f"Missing {self.name} input")
        if any(path.exists() or path.is_symlink() for path in self.outputs):
            raise ValueError(f"Refusing to overwrite a {self.name} output")

    def run(
        self, *, folder: Path, environment: dict, cancelled: Callable, progress: Callable
    ) -> dict:
        """Run argv through the owned, cancellable process/resource monitor."""
        self.validate(folder)
        return run_command(
            self.argv,
            cwd=folder / "correction",
            log=folder / "logs" / f"{self.name}.log",
            environment=environment,
            cancelled=cancelled,
            progress=progress,
        )

    def qc(self) -> dict:
        """Check declared artifacts before the separate full numerical/scientific gates."""
        if any(not path.is_file() or path.stat().st_size == 0 for path in self.outputs):
            raise ValueError(f"Missing/empty {self.name} output despite command exit")
        return {
            "declared_outputs_present": True,
            "scope": "Presence only; numerical and scientific checks are separate",
        }


def _step(command: dict, folder: Path, parameters: dict) -> CorrectionStep:
    """Associate known command roles with fixed, isolated file contracts."""
    c, e = folder / "correction", folder / "evaluation"
    contracts = {
        "topup": (
            (c / "b0-pairs.nii", c / "acqparams.txt", c / "topup.cnf"),
            tuple(
                c / name
                for name in (
                    "field.nii.gz",
                    "unwarped.nii.gz",
                    "topup_fieldcoef.nii.gz",
                    "topup_movpar.txt",
                    "topup-explicit.log",
                )
            ),
        ),
        "fitting_mask": (
            (c / "mean-unwarped.nii",),
            (c / "fitting.nii.gz", c / "fitting_mask.nii.gz"),
        ),
        "eddy": (
            tuple(
                c / name
                for name in (
                    "input.nii",
                    "fitting_mask.nii.gz",
                    "acqparams.txt",
                    "index.txt",
                    "topup_fieldcoef.nii.gz",
                    "topup_movpar.txt",
                )
            )
            + (folder / "inputs.bvals", folder / "inputs.bvecs"),
            tuple(
                c / name
                for name in (
                    "eddy.nii.gz",
                    "eddy.eddy_rotated_bvecs",
                    "eddy.eddy_residuals.nii.gz",
                    "eddy.eddy_cnr_maps.nii.gz",
                    "eddy.eddy_outlier_free_data.nii.gz",
                    "eddy.eddy_shell_indicies.json",
                )
            ),
        ),
        "corrected_conversion": (
            (c / "eddy.nii.gz", c / "eddy.eddy_rotated_bvecs", folder / "inputs.bvals"),
            (e / "dwi.mif",),
        ),
        "final_mask": ((e / "dwi.mif",), (e / "mask.mif",)),
        "export_final_mask": ((e / "mask.mif",), (e / "mask-export.nii",)),
    }
    inputs, outputs = contracts[command["step"]]
    return CorrectionStep(command["step"], tuple(command["argv"]), inputs, outputs, parameters)


def _fsl_tools(directory: Path | None, cancelled: Callable) -> tuple[dict, Path]:
    """Bind actual executables to installed package/build records, not suite label.

    EDDY 2401.2 has no --version option. The conda installation record identifies
    its build; binary hashes are recorded independently. Unsupported installations
    require an explicit version contract rather than pretending --help is version.
    """
    candidate = str(directory / "eddy_cpu") if directory else shutil.which("eddy_cpu")
    if not candidate:
        raise ValueError("Select the installed FSL bin directory containing eddy_cpu")
    bindir = Path(candidate).expanduser().resolve(strict=True).parent
    records = []
    for path in sorted((bindir.parent / "conda-meta").glob("fsl-*.json")):
        record = _json(path)
        records.append((path, record))
    tools = {}
    for name in ("topup", "eddy_cpu", "bet2"):
        path = bindir / name
        if not path.is_file():
            raise ValueError(f"Missing local FSL executable: {path}")
        matching = [(p, r) for p, r in records if f"bin/{name}" in r.get("files", [])]
        if len(matching) != 1:
            raise ValueError(f"Cannot establish installed package/build for {name}")
        record_path, record = matching[0]
        if name == "eddy_cpu" and record.get("version") != EDDY_TESTED_RELEASE:
            raise ValueError("Diagnostic contract currently supports EDDY 2401.2 only")
        tools[name] = {
            "path": str(path),
            "sha256": hash_file(path, cancelled),
            "version": record["version"],
            "build": record.get("build"),
            "platform": record.get("subdir"),
            "mechanism": "local CLI",
            "version_record": str(record_path),
            "version_record_sha256": hash_file(record_path, cancelled),
        }
    return tools, bindir.parent


def _acquisition_source(
    dataset, native_record: Path | None, cancelled: Callable
) -> tuple[Path, dict]:
    """Associate a native denoise/Gibbs derivative with its unchanged BIDS source."""
    if native_record is None:
        return dataset.paths.dwi, {}
    record = _json(native_record)
    recovery = None
    if record.get("status") in {"failed", "cancelled"}:
        from .checkpoints import native_recovery

        recovery = native_recovery(native_record, record, cancelled=cancelled)
        if (
            recovery is None
            or dataset.paths.dwi.resolve() != Path(recovery["product"]["paths"]["dwi"]).resolve()
        ):
            raise ValueError("Failed native attempt has no matching verified checkpoint")
    if recovery is None and (
        record.get("status") != "validated_pending_qc" or record.get("inputs_unchanged") is not True
    ):
        raise ValueError("Native producer must have validated outputs and preserved inputs")
    p = record.get("plan", {})
    if p.get("kind") != "mrtrix_native_execution_plan":
        raise ValueError("Unsupported native producer; no acquisition history is inferred")
    source = Path(p.get("original_acquisition", p["input_paths"]["dwi"]))
    output = Path(p["output_directory"])
    name = (
        Path(recovery["product"]["paths"]["dwi"]).name
        if recovery
        else ("gibbs.nii" if "gibbs" in p["operations"] else "denoise.nii")
    )
    if dataset.paths.dwi.resolve() != (output / name).resolve():
        raise ValueError("Load the native producer's last corrected image")
    checks = dict(p.get("extra_input_hashes", {})) | {
        str(native_record): hash_file(native_record, cancelled)
    }
    checks.update(
        recovery["product"]["extra_hashes"]
        if recovery
        else {str(output / k): v["sha256"] for k, v in record["outputs"].items()}
    )
    checks.update({p["input_paths"][k]: v["sha256"] for k, v in p["inputs"].items()})
    if str(output / name) not in checks:
        raise ValueError("Native image lacks a producer output hash")
    for path, digest in checks.items():
        if hash_file(Path(path), cancelled) != digest:
            raise ValueError(f"Native producer evidence changed: {path}")
    if str(source) not in checks:
        raise ValueError("Original acquisition lacks bound lineage evidence")
    b = np.loadtxt(p["input_paths"]["bval"]).ravel()
    # Numeric orientation/layout must follow the producer's original contract.
    from .gradients import GradientTable

    gradients = GradientTable.from_files(
        Path(p["input_paths"]["bval"]),
        Path(p["input_paths"]["bvec"]),
        layout=p["gradient_staging"]["source_layout"],
    )
    if not np.array_equal(b, dataset.gradients.bvals) or not np.array_equal(
        gradients.bvecs, dataset.gradients.bvecs
    ):
        raise ValueError("Native derivative gradients do not match its acquisition source")
    original = nib.load(source)
    if original.shape != dataset.shape or not np.allclose(
        original.affine, dataset.affine, atol=1e-5, rtol=0
    ):
        raise ValueError("Native producer geometry differs from loaded image")
    return source, checks


def prepare_correction(
    dataset,
    history,
    operations,
    output,
    *,
    references: tuple[Path, ...],
    bids_root: Path | None = None,
    native_record: Path | None = None,
    fsl_directory: Path | None = None,
    tool_directory: Path | None = None,
    nthreads: int = 4,
    cancelled: Callable = lambda: False,
    progress: Callable = lambda _: None,
) -> ExecutionPlan:
    """Freeze acquisition evidence, isolated outputs and baseline commands, no run."""
    if set(operations) - {"convert", "correction"}:
        raise ValueError("Run correction separately after any selected denoising/Gibbs steps")
    if build_plan(dataset, history, operations).to_dict()["status"] != "eligible":
        raise ValueError("Correction requires confirmed eligible processing history")
    if not references:
        raise ValueError("Supply explicitly associated reverse-PE b0 calibration NIfTI images")
    references = tuple(Path(x).expanduser().absolute() for x in references)
    if len({p.resolve() for p in references}) != len(references):
        raise ValueError("Duplicate calibration references are not permitted")
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
    source, handoff = _acquisition_source(dataset, native_record, cancelled)
    acquisition = inherited_metadata(source, root=bids_root)
    meta = acquisition["metadata"]
    row = phase_encoding_row(meta)
    image = nib.load(source)
    groups = slice_groups(image, meta)
    if groups["slice_axis"] != 2:
        raise ValueError(
            "This EDDY adapter requires a documented native k slice axis; do not guess or transpose it"
        )
    if np.linalg.det(dataset.affine[:3, :3]) >= 0:
        raise ValueError(
            "Positive-determinant FSL PE convention requires a separately verified orientation adapter"
        )
    shells = eddy_shells(dataset.gradients.bvals)
    if not np.array_equal(
        dataset.gradients.bvals <= 100, dataset.gradients.bvals <= dataset.validation.policy.b0_max
    ):
        raise ValueError("Loaded low-b classification differs from installed EDDY b_range=100")
    weighted = dataset.gradients.bvals > 100
    if not np.allclose(dataset.gradients.norms()[weighted], 1, atol=1e-4, rtol=0):
        raise ValueError(
            "EDDY requires unit weighted gradients; no automatic gradient normalization"
        )
    refs, extra_hashes = [], dict(handoff)
    for reference in references:
        check_cancel(cancelled)
        evidence = inherited_metadata(reference, root=Path(acquisition["root"]))
        fields, suffix = entities(reference)
        if suffix != "epi" or fields.get("part") == "phase":
            raise ValueError(
                "Select magnitude EPI b0 fieldmap images, not phase or full reverse-DWI series"
            )
        association = validate_epi_reference(source, reference, meta, evidence["metadata"])
        cal = nib.load(reference)
        if len(cal.shape) not in (3, 4) or cal.header.get_xyzt_units()[0] != "mm":
            raise ValueError("Calibration must be a 3D/4D native-mm magnitude EPI")
        refs.append(
            {
                **association,
                "evidence": evidence,
                "volumes": cal.shape[3] if len(cal.shape) == 4 else 1,
            }
        )
        extra_hashes[str(reference)] = hash_file(reference, cancelled)
    if not any(np.dot(ref["acqparams"][:3], row[:3]) == -1 for ref in refs):
        raise ValueError("No associated opposite-polarity calibration was supplied")
    for document in [acquisition, *(r["evidence"] for r in refs)]:
        extra_hashes.update({s["path"]: s["sha256"] for s in document["sources"]})
    for path in extra_hashes:
        parent = Path(path).resolve().parent
        if folder == parent or parent in folder.parents:
            raise ValueError("Choose output outside all calibration/evidence source directories")
    progress("Checking installed FSL package/build evidence…")
    fsl, fsldir = _fsl_tools(fsl_directory, cancelled)
    # Official installed schedules, never modify a sampling schedule to get past
    # a dimension failure. Odd grids use the distributed no-subsampling schedule.
    config = (
        fsldir
        / "etc/flirtsch"
        / ("b02b0_2.cnf" if all(n % 2 == 0 for n in dataset.shape[:3]) else "b02b0_1.cnf")
    )
    extra_hashes[str(config)] = hash_file(config, cancelled)
    config_text = config.read_text()
    if any(
        token in config_text
        for token in ("--out=", "--iout=", "--fout=", "--logout=", "--imain=", "--datain=")
    ):
        raise ValueError("TOPUP config contains file paths; output isolation cannot be guaranteed")
    p["tools"].update(fsl)
    p.update(
        kind="fsl_correction_plan",
        operations=list(operations),
        conversion_plan=base.to_dict(),
        correction_inputs={
            "references": [str(x) for x in references],
            "bids_root": acquisition["root"],
            "native_record": str(native_record) if native_record else None,
        },
        acquisition=acquisition,
        calibration=refs,
        extra_input_hashes=extra_hashes,
        topup_config_source=str(config),
        slice_grouping=groups,
        shells=shells,
    )
    p["parameters"].update(
        b_range=100,
        niter=5,
        flm="quadratic",
        slm="none",
        resamp="jac",
        repol=True,
        ol_type="sw",
        initrand=1,
        fitting_bet_fraction=0.3,
        final_mask_clean_scale=2,
        mporder=0,
    )
    correction = folder / "correction"

    def t(name):
        """Select an already bound local executable."""
        return p["tools"][name]["path"]

    commands = [
        (
            "topup",
            [
                t("topup"),
                f"--imain={correction / 'b0-pairs.nii'}",
                f"--datain={correction / 'acqparams.txt'}",
                f"--config={correction / 'topup.cnf'}",
                f"--out={correction / 'topup'}",
                f"--fout={correction / 'field.nii.gz'}",
                f"--iout={correction / 'unwarped.nii.gz'}",
                f"--logout={correction / 'topup-explicit.log'}",
                f"--nthr={nthreads}",
                "--verbose",
            ],
        ),
        (
            "fitting_mask",
            [
                t("bet2"),
                str(correction / "mean-unwarped.nii"),
                str(correction / "fitting"),
                "-m",
                "-f",
                "0.3",
            ],
        ),
        (
            "eddy",
            [
                t("eddy_cpu"),
                f"--imain={correction / 'input.nii'}",
                f"--mask={correction / 'fitting_mask.nii.gz'}",
                f"--acqp={correction / 'acqparams.txt'}",
                f"--index={correction / 'index.txt'}",
                f"--bvals={folder / 'inputs.bvals'}",
                f"--bvecs={folder / 'inputs.bvecs'}",
                f"--topup={correction / 'topup'}",
                f"--out={correction / 'eddy'}",
                "--repol",
                "--cnr_maps",
                "--residuals",
                "--niter=5",
                "--flm=quadratic",
                "--slm=none",
                "--resamp=jac",
                "--ol_type=sw",
                "--initrand=1",
                "--b_range=100",
                f"--nthr={nthreads}",
                "--verbose",
            ],
        ),
    ]
    p["commands"] += [
        {"step": name, "argv": argv, "display": shlex.join(argv)} for name, argv in commands
    ]
    evaluation = folder / "evaluation"
    commands = [
        (
            "corrected_conversion",
            [
                t("mrconvert"),
                str(correction / "eddy.nii.gz"),
                str(evaluation / "dwi.mif"),
                "-fslgrad",
                str(correction / "eddy.eddy_rotated_bvecs"),
                str(folder / "inputs.bvals"),
                "-bvalue_scaling",
                "no",
                "-datatype",
                "float32le",
                "-strides",
                "-1,2,3,4",
                "-nthreads",
                str(nthreads),
            ],
        ),
        (
            "final_mask",
            [
                t("dwi2mask"),
                str(evaluation / "dwi.mif"),
                str(evaluation / "mask.mif"),
                "-clean_scale",
                "2",
                "-config",
                "BZeroThreshold",
                str(p["parameters"]["b0_max"]),
                "-nthreads",
                str(nthreads),
            ],
        ),
        (
            "export_final_mask",
            [
                t("mrconvert"),
                str(evaluation / "mask.mif"),
                str(evaluation / "mask-export.nii"),
                "-datatype",
                "uint8",
                "-nthreads",
                str(nthreads),
            ],
        ),
    ]
    p["commands"] += [
        {"step": name, "argv": argv, "display": shlex.join(argv)} for name, argv in commands
    ]
    p["environment"]["overrides"].update(
        FSLDIR=str(fsldir),
        FSLOUTPUTTYPE="NIFTI_GZ",
        OMP_NUM_THREADS=str(nthreads),
        TMPDIR=str(folder / "scratch"),
    )
    p["implementation_hashes"] = {
        str(path): hash_file(path)
        for path in (
            Path(__file__),
            Path(__file__).with_name("correction_diagnostics.py"),
            Path(__file__).with_name("acquisition_metadata.py"),
            Path(__file__).with_name("acquisition.py"),
        )
    }
    p["resources"] = {
        "estimated_peak_additional_bytes": int(np.prod(dataset.shape)) * 4 * 9 + 256 * 1024**2
    }
    p["internal_actions"] += [
        "Stage DWI and b0 calibrations inside this exclusive attempt. Select the actual first documented low-b index, preserving all DWI volumes.",
        "Save acqparams/index, exact calibration-volume mapping and documented slice groups. Never use PA b0 as a full reverse diffusion series.",
        "Use BET2 fitting mask on TOPUP mean; generate a separate dwi2mask clean_scale=2 evaluation/final mask after EDDY integrity checks.",
        "Check CNR, residuals and fitted GP variances before releasing the N4 handoff. Keep failed outputs and scientific review pending.",
    ]
    p["scientific_limits"] += [
        "Single-acquisition/single-PE CPU adapter only. No acquisition concatenation, slice-to-volume or susceptibility-by-movement correction, gradient-nonlinearity correction, N4 or T1 registration is implied.",
        "BET2 is the direct executable called by the established bet -m -f 0.3 wrapper path. Both masks require anatomical review.",
        "Slice groups are recorded from metadata, not invented or used to imply slice-to-volume processing. Slicewise outlier replacement remains the baseline.",
        "Inclusive GP residuals/CNR are fitted diagnostics, not independent ground truth. Sparse acquisitions may still fail the existing diagnostic gate.",
        "Corrected 4D output stays on the native diffusion grid. Registration changes intensities by spatial resampling within that grid; no T1-space resampling is performed.",
    ]
    return ExecutionPlan(p)


def _image(
    path: Path, shape: tuple, affine: np.ndarray, cancelled: Callable
) -> nib.spatialimages.SpatialImage:
    """Check every output volume and the explicit native grid before downstream use."""
    image = nib.load(path, mmap="r")
    if image.shape != shape or not np.allclose(image.affine, affine, atol=1e-5, rtol=0):
        raise ValueError(f"Incorrect geometry/volume count: {path}")
    for v in range(shape[3] if len(shape) == 4 else 1):
        check_cancel(cancelled)
        values = np.asarray(image.dataobj[..., v] if len(shape) == 4 else image.dataobj)
        if not np.isfinite(values).all():
            raise ValueError(f"Nonfinite image output: {path}, volume {v}")
    return image


def _stage(dataset, p: dict, folder: Path, cancelled: Callable) -> dict:
    """Create all generated inputs inside the attempt, preserving volume mapping."""
    from ._mif_checks import produced_mif

    target = folder / "correction"
    target.mkdir()
    (folder / "scratch").mkdir()
    with produced_mif(folder / "dwi.mif") as (_, affine, values):
        _save_native(dataset, values, affine, target / "input.nii", cancelled)
    source = nib.load(target / "input.nii", mmap="r")
    first = p["shells"][0]["indices"][0]
    rows = [list(phase_encoding_row(p["acquisition"]["metadata"]))]
    values = [np.asarray(source.dataobj[..., first], np.float32)]
    mapping = [{"source": str(dataset.paths.dwi), "volume": first, "role": "DWI low-b reference"}]
    for reference in p["calibration"]:
        image = nib.load(reference["path"], mmap="r")
        for v in range(reference["volumes"]):
            check_cancel(cancelled)
            values.append(
                np.asarray(
                    image.dataobj[..., v] if len(image.shape) == 4 else image.dataobj, np.float32
                )
            )
            rows.append(reference["acqparams"])
            mapping.append(
                {"source": reference["path"], "volume": v, "role": "Associated b0 calibration"}
            )
    # Calibration stacks are small; the 4D DWI export above remains disk-backed.
    _save_native(
        dataset, np.stack(values, axis=3), dataset.affine, target / "b0-pairs.nii", cancelled
    )
    np.savetxt(target / "acqparams.txt", rows, fmt="%.17g")
    np.savetxt(target / "index.txt", np.ones((1, dataset.n_volumes), int), fmt="%d")
    shutil.copyfile(p["topup_config_source"], target / "topup.cnf")
    _publish_json(
        target / "calibration-association.json",
        {
            "volumes": mapping,
            "rows": rows,
            "DWI_volume_order": list(range(dataset.n_volumes)),
            "index": "All DWI volumes map to row 1; one verified PE acquisition",
        },
    )
    _publish_json(target / "acquisition-evidence.json", p["acquisition"])
    _publish_json(target / "slice-grouping.json", p["slice_grouping"])
    if p["slice_grouping"]["groups"] is not None:
        np.savetxt(target / "slspec.txt", p["slice_grouping"]["groups"], fmt="%d")
    return {
        "calibration_volumes": len(values),
        "low_b_indices": p["shells"][0]["indices"],
        "first_reference_index": first,
    }


def correction_gate(dataset, folder: Path, p: dict, cancelled: Callable) -> dict:
    """Evaluate shell CNR/residuals in both masks; failed diagnostics stay failed."""
    correction = folder / "correction"
    prefix = correction / "eddy"
    mask = np.asarray(nib.load(folder / "evaluation/mask.nii.gz").dataobj) > 0
    fitting = np.asarray(nib.load(correction / "fitting_mask.nii.gz").dataobj) > 0
    cnr = nib.load(str(prefix) + ".eddy_cnr_maps.nii.gz")
    residual = nib.load(str(prefix) + ".eddy_residuals.nii.gz")
    shells = p["shells"]
    published = _json(Path(str(prefix) + ".eddy_shell_indicies.json")).get("Shells")
    if (
        not isinstance(published, list)
        or len(published) != len(shells)
        or any(
            row.get("Index") != shell["indices"]
            for row, shell in zip(published, shells, strict=True)
        )
    ):
        raise ValueError("Published EDDY shell indices disagree with acquired volume mapping")
    for image, shape in ((cnr, (*dataset.shape[:3], len(shells))), (residual, dataset.shape)):
        if image.shape != shape or not np.allclose(image.affine, dataset.affine, atol=1e-5, rtol=0):
            raise ValueError(
                "EDDY diagnostics have an incompatible grid, channel count or volume order"
            )
    flags, concerns, rows, per_volume = [], [], [], []
    # This accumulates one 3D MSR image per shell without loading all residuals.
    for channel, shell in enumerate(shells):
        total = np.zeros(dataset.shape[:3], np.float64)
        for v in shell["indices"]:
            check_cancel(cancelled)
            values = np.asarray(residual.dataobj[..., v], dtype=np.float64)
            stats = finite_statistics(values)
            if stats["nonfinite"]:
                flags.append(f"Nonfinite residual values at volume {v}")
            with np.errstate(over="ignore", invalid="ignore"):
                squared = values * values
                total += squared / len(shell["indices"])
            per_volume.append(
                {
                    "volume": v,
                    "residual": stats,
                    "squared_residual_inside_brain": finite_statistics(squared[mask]),
                }
            )
        values = np.asarray(cnr.dataobj[..., channel])
        row = {
            **shell,
            "cnr_channel": channel,
            "CNR_inside_brain": finite_statistics(values[mask]),
            "CNR_inside_fitting_mask": finite_statistics(values[fitting]),
            "CNR_entire_grid": finite_statistics(values),
            "inclusive_MSR_inside_brain": finite_statistics(total[mask]),
        }
        if row["CNR_entire_grid"]["nonfinite"]:
            flags.append(f"Nonfinite CNR channel {channel}; counts in both masks recorded")
        if row["inclusive_MSR_inside_brain"]["nonfinite"]:
            flags.append(f"Nonfinite squared prediction residual channel {channel}")
        percentiles = row["inclusive_MSR_inside_brain"]["finite_percentiles_0_1_50_99_100"]
        if channel and (percentiles is None or percentiles[2] == 0):
            flags.append(f"Weighted inclusive prediction residual collapse channel {channel}")
        if channel and row["CNR_inside_brain"]["exact_zero"]:
            concerns.append(
                f"Channel {channel}: {row['CNR_inside_brain']['exact_zero']} exact-zero brain CNR voxels require focal review"
            )
        rows.append(row)
    try:
        gp = hyperparameter_blocks(
            (folder / "logs/eddy.log").read_text(errors="replace"), len(shells) - 1
        )
    except ValueError as exc:
        gp = []
        flags.append(str(exc))
    movement = np.loadtxt(str(prefix) + ".eddy_movement_rms", ndmin=2)
    outliers = np.loadtxt(str(prefix) + ".eddy_outlier_map", skiprows=1, ndmin=2)
    parameters = np.loadtxt(str(prefix) + ".eddy_parameters", ndmin=2)
    if (
        movement.shape != (dataset.n_volumes, 2)
        or outliers.shape != (dataset.n_volumes, dataset.shape[2])
        or parameters.shape[0] != dataset.n_volumes
        or parameters.shape[1] < 6
        or not all(np.isfinite(x).all() for x in (movement, outliers, parameters))
        or not np.isin(outliers, [0, 1]).all()
    ):
        raise ValueError("Invalid EDDY motion, parameter or outlier volume/slice association")
    return {
        "technical_checks_passed": not flags,
        "flags": flags,
        "scientific_concerns": concerns,
        "corrected_input_sha256": hash_file(Path(str(prefix) + ".nii.gz"), cancelled),
        "shells": rows,
        "published_EDDY_shell_indices": published,
        "per_volume": sorted(per_volume, key=lambda x: x["volume"]),
        "GP_hyperparameters": gp,
        "detected_pairs": np.argwhere(outliers > 0).tolist(),
        "detections_per_slice": outliers.sum(axis=0).astype(int).tolist(),
        "movement_rms": movement.tolist(),
        "eddy_parameters": parameters.tolist(),
        "volume_count_retained": dataset.n_volumes,
        "scientific_acceptance": "pending",
        "target_excluded_predictions": "Not available; inclusive fitted predictions are not independent ground truth",
        "geometry": "Native diffusion grid; no T1 registration or AP/PA combination performed",
    }


def execute_correction(
    dataset, plan, *, approval, cancelled=lambda: False, progress=lambda _: None
) -> dict:
    """Run reviewed local commands once; failures never launch dependent N4/alignment."""
    if approval != plan.digest:
        raise ValueError("Approve the exact correction plan")
    p = plan.to_dict()
    options = p["correction_inputs"]
    current = prepare_correction(
        dataset,
        ProcessingHistory.from_dict(p["history"]),
        tuple(p["operations"]),
        Path(p["output_directory"]),
        references=tuple(Path(x) for x in options["references"]),
        bids_root=Path(options["bids_root"]),
        native_record=Path(options["native_record"]) if options["native_record"] else None,
        fsl_directory=Path(p["tools"]["eddy_cpu"]["path"]).parent,
        tool_directory=Path(p["tools"]["mrconvert"]["path"]).parent,
        nthreads=p["parameters"]["nthreads"],
        cancelled=cancelled,
        progress=progress,
    )
    if current.digest != plan.digest:
        raise ValueError(
            "Acquisition, calibration, software or configuration changed; prepare again"
        )
    folder = Path(p["output_directory"])
    if shutil.disk_usage(folder.parent).free < p["resources"]["estimated_peak_additional_bytes"]:
        raise ValueError("Insufficient disk for preserved correction outputs and diagnostics")
    base = ExecutionPlan(p["conversion_plan"])
    converted = execution.execute_plan(
        dataset, base, approval=base.digest, cancelled=cancelled, progress=progress
    )
    manifest = {
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
    corrected_dataset = None
    producer = gate = None

    def checkpoint():
        temporary = folder / ".correction-manifest.tmp"
        temporary.write_text(json.dumps(manifest, indent=2, allow_nan=False))
        temporary.replace(folder / "correction-manifest.json")

    try:
        if converted["status"] == "cancelled":
            raise ProcessingCancelled("Conversion cancelled")
        if converted["status"] != "validated":
            raise ValueError(
                "Conversion prerequisite failed: " + converted.get("error", converted["status"])
            )
        manifest["validation"]["staging"] = _stage(dataset, p, folder, cancelled)
        correction = folder / "correction"
        evaluation = folder / "evaluation"
        evaluation.mkdir()
        environment = dict(os.environ) | p["environment"]["overrides"]
        for command in p["commands"][1:]:
            check_cancel(cancelled)
            name = command["step"]
            step = {
                "step": name,
                "status": "running",
                "started_at": _now(),
                "argv": command["argv"],
            }
            manifest["steps"].append(step)
            checkpoint()
            progress(f"Running {name}\n{command['display']}")
            adapter = _step(command, folder, p["parameters"])
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
            step["artifact_contract"] = adapter.qc()
            checkpoint()
            if name == "topup":
                n = manifest["validation"]["staging"]["calibration_volumes"]
                unwarped = _image(
                    correction / "unwarped.nii.gz",
                    (*dataset.shape[:3], n),
                    dataset.affine,
                    cancelled,
                )
                _image(correction / "field.nii.gz", dataset.shape[:3], dataset.affine, cancelled)
                coeff = np.asarray(nib.load(correction / "topup_fieldcoef.nii.gz").dataobj)
                motion = np.loadtxt(correction / "topup_movpar.txt", ndmin=2)
                if (
                    not np.isfinite(coeff).all()
                    or motion.shape != (n, 6)
                    or not np.isfinite(motion).all()
                ):
                    raise ValueError("Invalid TOPUP coefficients or calibration motion parameters")
                mean = sum(np.asarray(unwarped.dataobj[..., v], np.float64) / n for v in range(n))
                _save_native(
                    dataset, mean, dataset.affine, correction / "mean-unwarped.nii", cancelled
                )
                manifest["validation"]["topup"] = {
                    "finite": True,
                    "native_grid": True,
                    "calibration_volumes": n,
                }
            elif name == "fitting_mask":
                image = _image(
                    correction / "fitting_mask.nii.gz", dataset.shape[:3], dataset.affine, cancelled
                )
                if not np.array_equal(np.unique(np.asarray(image.dataobj)), [0, 1]):
                    raise ValueError("EDDY fitting mask must contain both zero and one")
                manifest["validation"]["fitting_mask"] = {
                    "binary": True,
                    "native_grid": True,
                    "anatomical_review": "pending",
                }
            elif name == "eddy":
                _image(correction / "eddy.nii.gz", dataset.shape, dataset.affine, cancelled)
                _image(
                    correction / "eddy.eddy_outlier_free_data.nii.gz",
                    dataset.shape,
                    dataset.affine,
                    cancelled,
                )
                rotated = np.loadtxt(correction / "eddy.eddy_rotated_bvecs", ndmin=2)
                if (
                    rotated.shape != (3, dataset.n_volumes)
                    or not np.isfinite(rotated).all()
                    or not np.allclose(
                        np.linalg.norm(rotated[:, dataset.gradients.bvals > 100], axis=0),
                        1,
                        atol=1e-4,
                        rtol=0,
                    )
                ):
                    raise ValueError("Invalid EDDY-rotated gradient counts or norms")
                shutil.copyfile(folder / "inputs.bvals", correction / "corrected.bval")
                shutil.copyfile(
                    correction / "eddy.eddy_rotated_bvecs", correction / "corrected.bvec"
                )
                loaded = DatasetLoader().load(
                    DatasetPaths(
                        correction / "eddy.nii.gz",
                        correction / "corrected.bval",
                        correction / "corrected.bvec",
                    )
                )
                corrected_dataset = loaded.dataset
                if corrected_dataset is None:
                    raise ValueError("Corrected DWI failed the shared input loader")
                manifest["validation"]["eddy_integrity"] = {
                    "finite": True,
                    "native_grid": True,
                    "volumes": dataset.n_volumes,
                    "bvalues_preserved": np.array_equal(
                        corrected_dataset.gradients.bvals, dataset.gradients.bvals
                    ),
                }
                if not manifest["validation"]["eddy_integrity"]["bvalues_preserved"]:
                    raise ValueError("Corrected b-values changed")
            elif name == "corrected_conversion":
                manifest["validation"]["corrected_gradients"] = execution._validate_conversion(
                    corrected_dataset, evaluation / "dwi.mif", cancelled, progress
                )
            elif name == "export_final_mask":
                manifest["validation"]["mask"] = execution._validate_mask(
                    corrected_dataset, evaluation, cancelled
                )
            step["status"] = "validated" if name != "final_mask" else "command_completed"
            checkpoint()
        gate = correction_gate(corrected_dataset, folder, p, cancelled)
        manifest["validation"]["correction_gate"] = gate
        manifest["status"] = "validated_pending_qc" if gate["technical_checks_passed"] else "failed"
        if not gate["technical_checks_passed"]:
            manifest["error"] = "Correction diagnostics failed; N4/alignment pending: " + "; ".join(
                gate["flags"]
            )
        # Bind the producer's exact input bvals and output vectors to the N4
        # handoff contract; no absent/failed diagnostic is promoted to passed.
        eddy_step = next(s for s in manifest["steps"] if s["step"] == "eddy")
        producer = {
            **eddy_step,
            "status": "validated_pending_visual_qc"
            if gate["technical_checks_passed"]
            else "diagnostics_failed",
            "inputs": {str(folder / "inputs.bvals"): hash_file(folder / "inputs.bvals")},
            "outputs": {
                str(path): {"sha256": hash_file(path, cancelled), "bytes": path.stat().st_size}
                for path in sorted(correction.glob("eddy*"))
                if path.is_file()
            },
        }
    except (ProcessingCancelled, KeyboardInterrupt) as exc:
        manifest.update(status="cancelled", error=str(exc))
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        if corrected_dataset is not None:
            corrected_dataset.close()
        try:
            dataset.assert_current()
            hashes = {p["input_paths"][k]: v["sha256"] for k, v in p["inputs"].items()} | p[
                "extra_input_hashes"
            ]
            manifest["inputs_unchanged"] = all(
                hash_file(Path(path)) == digest for path, digest in hashes.items()
            )
            if not manifest["inputs_unchanged"]:
                raise ValueError(
                    "Original acquisition/calibration/evidence changed during processing"
                )
        except Exception as exc:
            manifest.update(status="failed", error=str(exc), inputs_unchanged=False)
        for step in manifest["steps"]:
            if step["status"] == "running":
                step.update(status=manifest["status"], finished_at=_now())
        manifest["finished_at"] = _now()
        if gate is not None:
            if manifest["status"] != "validated_pending_qc":
                gate["technical_checks_passed"] = False
                if manifest.get("error") and manifest["error"] not in gate["flags"]:
                    gate["flags"].append(manifest["error"])
            _publish_json(folder / "correction-gate.json", gate)
        if producer is not None:
            if manifest["status"] != "validated_pending_qc":
                producer["status"] = "diagnostics_failed"
            _publish_json(folder / "eddy-producer.json", producer)
        # Hash completed and failed artifacts alike. Do not pretend that a hash
        # establishes numerical integrity or scientific validity.
        manifest["outputs"] = {
            str(path.relative_to(folder)): {"sha256": hash_file(path), "bytes": path.stat().st_size}
            for path in sorted(folder.rglob("*"))
            if path.is_file()
            and path.name not in {"correction-manifest.json", ".correction-manifest.tmp"}
        }
        checkpoint()
    try:
        from .correction_qc import export_correction_catalog

        export_correction_catalog(dataset, folder, manifest)
    except Exception as exc:
        manifest["qc_export_error"] = str(exc)
        _publish_json(
            folder / "qc-export-failure.json",
            {"error": str(exc), "processing_status": manifest["status"]},
        )
    return manifest
