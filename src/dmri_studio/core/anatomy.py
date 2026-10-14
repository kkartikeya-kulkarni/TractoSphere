"""Local anatomical preparation and separately reviewed 3D DWI–T1 alignment.

Adapts the existing LEMON INV2/UNI and prepared-HCP-T1 procedures. No historical
image is used to tune parameters. Every source is copied to a new attempt;
neither anatomical preparation nor registration changes the loaded 4D DWI.
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
from .execution import ExecutionPlan, _now, hash_file
from .process import ProcessingCancelled, check_cancel, run_command
from .qc import _json, load_catalog
from .workflow import ProcessingHistory, build_plan


def image3d(path: Path, reference=None) -> tuple[object, np.ndarray]:
    """Require readable finite coded geometry; never guess units or resample."""
    image = nib.load(path, mmap="r")
    if image.ndim != 3 or image.header.get_xyzt_units()[0] != "mm":
        raise ValueError("Anatomical images must be 3D NIfTI with explicit mm units")
    if not (int(image.header["sform_code"]) or int(image.header["qform_code"])):
        raise ValueError("Anatomical orientation requires a coded qform or sform")
    if not np.isfinite(image.affine).all() or abs(np.linalg.det(image.affine[:3, :3])) < 1e-8:
        raise ValueError("Invalid anatomical affine")
    if reference is not None and (
        image.shape != reference.shape
        or not np.allclose(image.affine, reference.affine, atol=1e-5, rtol=0)
    ):
        raise ValueError("Anatomical images do not share the required native grid")
    values = np.asarray(image.dataobj, dtype=np.float32)
    if not np.isfinite(values).all() or not np.any(values != 0):
        raise ValueError("Anatomical image has nonfinite values or empty support")
    return image, values


def save3d(values, reference, path: Path) -> None:
    """Write a new derived image while preserving reference forms and geometry."""
    if path.exists() or path.is_symlink():
        raise ValueError("Anatomical output exists; overwrite refused")
    header = reference.header.copy()
    header.set_data_dtype(values.dtype)
    result = type(reference)(values, reference.affine, header)
    for form in ("qform", "sform"):
        matrix, code = getattr(reference, "get_" + form)(coded=True)
        getattr(result, "set_" + form)(matrix, int(code))
    nib.save(result, path)


def anatomical_tools(directory: Path | None, names, cancelled: Callable) -> tuple[dict, Path]:
    """Record actual local package/build ownership and binary/script hashes."""
    candidate = str(directory / "fast") if directory else shutil.which("fast")
    if not candidate:
        raise ValueError("Select an installed FSL bin directory containing FAST and FLIRT")
    bindir = Path(candidate).expanduser().resolve(strict=True).parent
    records = [(p, _json(p)) for p in sorted((bindir.parent / "conda-meta").glob("fsl*.json"))]
    tools = {}
    for name in names:
        path = bindir / name
        owners = [(p, r) for p, r in records if f"bin/{name}" in r.get("files", [])]
        if not path.is_file() or len(owners) != 1:
            raise ValueError(f"Cannot verify installed FSL executable/build: {name}")
        record_path, record = owners[0]
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


def verified_manifest(path: Path, expected_kind: str, cancelled: Callable) -> tuple[dict, dict]:
    """Bind a completed engine manifest and its exact outputs, not filenames."""
    digest = hash_file(path, cancelled)
    record = _json(path)
    if (
        record.get("status") != "validated_pending_qc"
        or record.get("inputs_unchanged") is not True
        or record.get("plan", {}).get("kind") != expected_kind
    ):
        raise ValueError("Prerequisite manifest is not a validated, source-preserved run")
    folder = Path(record["plan"]["output_directory"]).resolve(strict=True)
    hashes = {str(path): digest}
    for name, entry in record.get("outputs", {}).items():
        output = (folder / name).resolve(strict=True)
        if not output.is_relative_to(folder) or hash_file(output, cancelled) != entry["sha256"]:
            raise ValueError(f"Prerequisite output changed or escaped attempt: {name}")
        hashes[str(output)] = entry["sha256"]
    if len(hashes) == 1 or hash_file(path, cancelled) != digest:
        raise ValueError("Prerequisite outputs absent or manifest changed")
    return record, hashes


def prepare_anatomy(
    dataset,
    history,
    operations,
    output,
    *,
    anatomy_options=None,
    fsl_directory=None,
    tool_directory=None,
    nthreads=4,
    cancelled=lambda: False,
    progress=lambda _: None,
) -> ExecutionPlan:
    """Freeze one anatomical stage, declared provenance and applicable review gate."""
    selected = set(operations)
    if len(selected) != 1 or not selected <= {"anatomy", "alignment"}:
        raise ValueError("Prepare anatomy and review it before selecting alignment separately")
    operation = next(iter(selected))
    if build_plan(dataset, history, operations).to_dict()["status"] != "eligible":
        raise ValueError("Confirm the input history and eligible anatomical operation")
    opts = dict(anatomy_options or {})
    allowed = {
        "mode",
        "t1",
        "inv2",
        "evidence",
        "anatomy_manifest",
        "anatomy_review",
        "diffusion_manifest",
        "fsl_directory",
    }
    if set(opts) - allowed:
        raise ValueError("Unknown anatomical configuration fields")
    fsl_directory = Path(opts["fsl_directory"]) if opts.get("fsl_directory") else fsl_directory
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
    p, extra = base.to_dict(), {}
    folder = Path(p["output_directory"])
    if operation == "anatomy":
        mode = opts.get("mode")
        if (
            mode not in {"prepared_t1", "mp2rage_inv2", "raw_t1"}
            or not str(opts.get("evidence", "")).strip()
        ):
            raise ValueError(
                "Declare prepared T1, MP2RAGE UNI/INV2, or confirmed unprocessed T1 history and its source/subject association; unknown history is unsupported"
            )
        if not opts.get("t1"):
            raise ValueError("Supply the associated T1w NIfTI")
        t1 = Path(opts["t1"]).expanduser().resolve(strict=True)
        reference, _ = image3d(t1)
        opts["t1"] = str(t1)
        extra[str(t1)] = hash_file(t1, cancelled)
        names = ["fast"]
        if mode == "mp2rage_inv2":
            if not opts.get("inv2"):
                raise ValueError("MP2RAGE extraction requires the associated same-grid INV2")
            inv2 = Path(opts["inv2"]).expanduser().resolve(strict=True)
            image3d(inv2, reference)
            opts["inv2"] = str(inv2)
            extra[str(inv2)] = hash_file(inv2, cancelled)
        if mode in {"mp2rage_inv2", "raw_t1"}:
            names += ["bet", "bet2", "fslstats", "fslval", "fslhd", "remove_ext", "imtest"]
    else:
        for key in ("anatomy_manifest", "anatomy_review", "diffusion_manifest"):
            if not opts.get(key):
                raise ValueError(
                    "Alignment requires anatomical producer, human review and final N4 producer records"
                )
            opts[key] = str(Path(opts[key]).expanduser().resolve(strict=True))
        anatomical, hashes = verified_manifest(
            Path(opts["anatomy_manifest"]), "fsl_anatomy_plan", cancelled
        )
        if anatomical["plan"]["anatomical_operation"] != "anatomy":
            raise ValueError("Anatomical producer does not describe tissue preparation")
        extra.update(hashes)
        # The operator's declaration associates anatomy to this diffusion lineage.
        diffusion, hashes = verified_manifest(
            Path(opts["diffusion_manifest"]), "mrtrix_ants_bias_plan", cancelled
        )
        extra.update(hashes)
        final = Path(diffusion["plan"]["output_directory"]) / "bias-corrected.nii"
        if dataset.paths.dwi.resolve() != final.resolve():
            raise ValueError("Load the final N4 image named by its verified producer")
        bvals = np.loadtxt(final.parent / "corrected.bval").ravel()
        bvecs = np.loadtxt(final.parent / "corrected.bvec", ndmin=2).T
        if not np.array_equal(bvals, dataset.gradients.bvals) or not np.array_equal(
            bvecs, dataset.gradients.bvecs
        ):
            raise ValueError("Final diffusion gradients do not match the validated producer")
        anatomical_dwi = anatomical["plan"]["input_paths"]["dwi"]
        lineage = {
            str(dataset.paths.dwi.resolve()),
            str(Path(diffusion["plan"]["input_paths"]["dwi"]).resolve()),
        }
        if str(Path(anatomical_dwi).resolve()) not in lineage:
            raise ValueError(
                "Anatomical preparation was associated with a different diffusion input; review its explicit association first"
            )
        review_path = Path(opts["anatomy_review"])
        extra.update(reviewed_anatomy(review_path, extra[opts["anatomy_manifest"]], cancelled))
        opts["anatomy_directory"] = anatomical["plan"]["output_directory"]
        reference_name = "registration-reference.nii.gz"
        if reference_name not in anatomical["outputs"]:
            if anatomical["plan"]["anatomy_options"].get("mode") not in {
                "prepared_t1",
                "mp2rage_inv2",
            }:
                raise ValueError(
                    "Anatomical producer does not identify its restored registration reference"
                )
            reference_name = "t1w-brain.nii.gz"
        opts["registration_reference"] = str(Path(opts["anatomy_directory"]) / reference_name)
        names = ["flirt", "fast"]
    for path in extra:
        if folder.is_relative_to(Path(path).parent):
            raise ValueError(
                "Choose a new output folder outside all anatomical/evidence source directories"
            )
    tools, fsldir = anatomical_tools(fsl_directory, names, cancelled)
    commands = []
    if operation == "anatomy":
        if opts["mode"] in {"mp2rage_inv2", "raw_t1"}:
            commands.append(
                (
                    "anatomical_extraction",
                    [
                        tools["bet"]["path"],
                        str(
                            folder
                            / (
                                "input-inv2.nii.gz"
                                if opts["mode"] == "mp2rage_inv2"
                                else "input-t1.nii.gz"
                            )
                        ),
                        str(folder / "extraction-brain.nii.gz"),
                        "-R",
                        "-m",
                        "-f",
                        "0.5",
                    ],
                )
            )
        commands.append(
            (
                "anatomical_tissues",
                [
                    tools["fast"]["path"],
                    "-t",
                    "1",
                    "-n",
                    "3",
                    *(["-b", "-B"] if opts["mode"] == "raw_t1" else ["-N"]),
                    "-o",
                    str(folder / "tissue"),
                    str(folder / "t1w-brain.nii.gz"),
                ],
            )
        )
    else:
        schedule = fsldir / "etc/flirtsch/bbr.sch"
        extra[str(schedule)] = hash_file(schedule, cancelled)
        for step, cost in (("rigid", "corratio"), ("bbr", "bbr")):
            argv = [
                tools["flirt"]["path"],
                "-in",
                str(folder / "mean-b0.nii.gz"),
                "-ref",
                str(folder / "t1w-brain.nii.gz"),
                "-omat",
                str(folder / f"dwi-to-t1-{step}.mat"),
                "-out",
                str(folder / f"mean-b0-t1-{step}.nii.gz"),
                "-dof",
                "6",
                "-cost",
                cost,
                "-interp",
                "trilinear",
            ]
            if step == "bbr":
                argv += [
                    "-init",
                    str(folder / "dwi-to-t1-rigid.mat"),
                    "-wmseg",
                    str(folder / "wm-mask.nii.gz"),
                    "-schedule",
                    str(folder / "bbr.sch"),
                ]
            commands.append((step, argv))
        opts["schedule"] = str(schedule)
    # Reuse input hashing/approval schema but do not convert/copy the entire DWI.
    p.update(
        kind="fsl_anatomy_plan",
        anatomical_operation=operation,
        anatomy_options=opts,
        extra_input_hashes=extra,
        operations=list(operations),
        commands=[
            {"step": name, "argv": argv, "display": shlex.join(argv)} for name, argv in commands
        ],
        tools=tools,
        outputs=(
            [
                "t1w-brain.nii.gz",
                "brain-mask.nii.gz",
                "wm-mask.nii.gz",
                "registration-reference.nii.gz",
            ]
            + [f"tissue_pve_{i}.nii.gz" for i in range(3)]
            + (
                ["tissue_restore.nii.gz", "tissue_bias.nii.gz"]
                if opts.get("mode") == "raw_t1"
                else []
            )
            if operation == "anatomy"
            else [
                "mean-b0.nii.gz",
                "dwi-to-t1-rigid.mat",
                "dwi-to-t1-bbr.mat",
                "mean-b0-t1-rigid.nii.gz",
                "mean-b0-t1-bbr.nii.gz",
            ]
        ),
        implementation_hashes={str(Path(__file__)): hash_file(Path(__file__))},
    )
    p["parameters"] = {
        "nthreads": nthreads,
        "b0_max": dataset.validation.policy.b0_max,
        "anatomical_FAST_bias_fit": opts.get("mode") == "raw_t1",
        "wm_threshold": 0.5,
        "registration_dof": 6 if operation == "alignment" else None,
    }
    p["environment"]["overrides"].update(
        FSLDIR=str(fsldir),
        FSLOUTPUTTYPE="NIFTI_GZ",
        OMP_NUM_THREADS=str(nthreads),
        TMPDIR=str(folder / "scratch"),
        TMP=str(folder / "scratch"),
        TEMP=str(folder / "scratch"),
    )
    p["internal_actions"] = [
        "Copy verified anatomical inputs to this exclusive attempt; preserve all source files.",
        "Confirmed unprocessed T1 uses BET -R 0.5 and standard FAST -b -B joint tissue/bias estimation. Prepared T1 and MP2RAGE use FAST -N without another anatomical bias fit. WM threshold 0.5 requires review.",
        "Alignment uses the actual low-b mean; only its 3D QC images are resampled. The 4D DWI and rotated vectors remain unchanged.",
    ]
    p["scientific_limits"] = [
        "Anatomical history and association are explicit user declarations, not inferred from filenames.",
        "Unknown anatomical processing history is not an unprocessed T1 declaration. Document scanner reconstruction and any offline corrections before selecting the branch.",
        "Brain extraction, white-matter boundaries and BBR alignment require human scientific review. Technical success is not acceptance.",
    ]
    return ExecutionPlan(p)


def posterior_support(maps, brain, mask) -> dict:
    """Reuse the verified FAST support contract, preserving zero-input exclusions."""
    support = (mask > 0) & (brain != 0)
    excluded = (mask > 0) & (brain == 0)
    sums = maps.sum(axis=0)
    if (
        not np.isfinite(maps).all()
        or ((maps < 0) | (maps > 1)).any()
        or not np.allclose(sums[support], 1, atol=1e-4, rtol=0)
        or np.any(sums[excluded] != 0)
        or np.any(sums[mask == 0] != 0)
    ):
        raise ValueError("Invalid FAST tissue probabilities on actual nonzero input support")
    return {
        "posterior_sum_nonzero_input_verified": True,
        "zero_input_voxels_inside_extraction": int(excluded.sum()),
        "zero_input_exclusions_unchanged": True,
        "anatomical_exclusions_require_review": True,
    }


def reviewed_anatomy(review_path: Path, manifest_hash: str, cancelled: Callable) -> dict:
    """Bind the user's saved decision to the exact completed anatomy stage."""
    review_hash = hash_file(review_path, cancelled)
    review = _json(review_path)
    catalog = load_catalog(Path(review.get("catalog", "")), cancelled=cancelled)
    stage = next((s for s in catalog.to_dict()["stages"] if s["id"] == "anatomy"), {})
    if (
        review.get("kind") != "dmri_human_qc_review"
        or review.get("decision") != "accepted"
        or review.get("stage") != "anatomy"
        or not review.get("reviewer")
        or not review.get("reason")
        or review.get("catalog_sha256") != catalog.digest
        or stage.get("execution") != "completed"
        or stage.get("technical") != "passed"
        or not any(
            a["sha256"] == manifest_hash
            for a in stage.get("artifacts", [])
            if a["kind"] == "history"
        )
        or hash_file(review_path, cancelled) != review_hash
    ):
        raise ValueError(
            "Record acceptance of this exact anatomy QC catalog before BBR; file labels alone do not establish review"
        )
    return {str(review_path): review_hash, str(catalog.path): catalog.digest}


@dataclass(frozen=True)
class AnatomicalStep:
    """Shared plugin-style contract for isolated local anatomy commands."""

    name: str
    argv: tuple[str, ...]
    inputs: tuple[Path, ...]
    outputs: tuple[Path, ...]
    parameters: dict

    def validate(self):
        """Never overwrite inputs or an earlier attempt's files."""
        if any(not p.is_file() for p in self.inputs) or any(
            p.exists() or p.is_symlink() for p in self.outputs
        ):
            raise ValueError("Missing anatomical input or pre-existing output")

    def run(self, folder, environment, cancelled, progress):
        """Run installed tools with resource/log monitoring outside the UI thread."""
        self.validate()
        return run_command(
            self.argv,
            cwd=folder,
            log=folder / "logs" / f"{self.name}.log",
            environment=environment,
            cancelled=cancelled,
            progress=progress,
        )

    def qc(self, reference):
        """Check finite native/T1-grid images before downstream use."""
        return [image3d(p, reference)[0].shape for p in self.outputs if p.name.endswith(".nii.gz")]


def execute_anatomy(dataset, plan, *, approval, cancelled=lambda: False, progress=lambda _: None):
    """Execute one frozen stage and save failures without changing original data."""
    if approval != plan.digest:
        raise ValueError("Approve the exact displayed anatomical plan")
    p = plan.to_dict()
    options = {
        k: v
        for k, v in p["anatomy_options"].items()
        if k not in {"anatomy_directory", "schedule", "registration_reference"}
    }
    current = prepare_anatomy(
        dataset,
        ProcessingHistory.from_dict(p["history"]),
        tuple(p["operations"]),
        Path(p["output_directory"]),
        anatomy_options=options,
        fsl_directory=Path(p["tools"]["fast"]["path"]).parent,
        nthreads=p["parameters"]["nthreads"],
        cancelled=cancelled,
        progress=progress,
    )
    if current.digest != plan.digest:
        raise ValueError("Anatomical inputs, software or configuration changed; prepare again")
    folder = Path(p["output_directory"])
    folder.mkdir(exist_ok=False)
    (folder / "logs").mkdir()
    (folder / "scratch").mkdir()
    manifest = {
        "plan": p,
        "approval": approval,
        "started_at": _now(),
        "status": "running",
        "steps": [],
        "validation": {},
        "outputs": {},
        "scientific_acceptance": "pending",
    }
    destination = folder / "anatomy-manifest.json"

    def checkpoint():
        """Atomically publish current status only inside this attempt."""
        temporary = folder / ".anatomy-manifest.tmp"
        temporary.write_text(json.dumps(manifest, indent=2, allow_nan=False))
        temporary.replace(destination)

    environment = dict(os.environ, **p["environment"]["overrides"])
    opts = p["anatomy_options"]
    try:
        if p["anatomical_operation"] == "anatomy":
            reference, values = image3d(Path(opts["t1"]))
            save3d(values, reference, folder / "input-t1.nii.gz")
            if opts["mode"] == "prepared_t1":
                save3d(values, reference, folder / "t1w-brain.nii.gz")
                save3d((values != 0).astype(np.uint8), reference, folder / "brain-mask.nii.gz")
            elif opts["mode"] == "mp2rage_inv2":
                inv, iv = image3d(Path(opts["inv2"]), reference)
                save3d(iv, inv, folder / "input-inv2.nii.gz")
        else:
            shutil.copyfile(opts["registration_reference"], folder / "t1w-brain.nii.gz")
            shutil.copyfile(
                Path(opts["anatomy_directory"]) / "wm-mask.nii.gz", folder / "wm-mask.nii.gz"
            )
            shutil.copyfile(opts["schedule"], folder / "bbr.sch")
            reference, _ = image3d(folder / "t1w-brain.nii.gz")
            source = nib.load(dataset.paths.dwi, mmap="r")
            indices = np.flatnonzero(
                dataset.gradients.b0_mask(b0_max=dataset.validation.policy.b0_max)
            )
            if not len(indices):
                raise ValueError("Alignment requires actual documented low-b volumes")
            total = np.zeros(dataset.shape[:3], np.float64)
            for index in indices:
                check_cancel(cancelled)
                v = np.asarray(source.dataobj[..., int(index)], dtype=np.float64)
                if not np.isfinite(v).all():
                    raise ValueError("Nonfinite final diffusion low-b input")
                total += v
            save3d((total / len(indices)).astype(np.float32), source, folder / "mean-b0.nii.gz")
            manifest["validation"]["mean_b0_indices_zero_based"] = indices.tolist()
        for command in p["commands"]:
            check_cancel(cancelled)
            name = command["step"]
            if name == "anatomical_extraction":
                inputs = [
                    folder
                    / ("input-inv2.nii.gz" if opts["mode"] == "mp2rage_inv2" else "input-t1.nii.gz")
                ]
                outputs = [
                    folder / "extraction-brain.nii.gz",
                    folder / "extraction-brain_mask.nii.gz",
                ]
            elif name == "anatomical_tissues":
                inputs = [folder / "t1w-brain.nii.gz"]
                outputs = [folder / f"tissue_pve_{i}.nii.gz" for i in range(3)]
                if opts["mode"] == "raw_t1":
                    outputs += [folder / "tissue_restore.nii.gz", folder / "tissue_bias.nii.gz"]
            else:
                inputs = [folder / "mean-b0.nii.gz", folder / "t1w-brain.nii.gz"]
                if name == "bbr":
                    inputs += [
                        folder / "dwi-to-t1-rigid.mat",
                        folder / "wm-mask.nii.gz",
                        folder / "bbr.sch",
                    ]
                outputs = [folder / f"dwi-to-t1-{name}.mat", folder / f"mean-b0-t1-{name}.nii.gz"]
            adapter = AnatomicalStep(
                name, tuple(command["argv"]), tuple(inputs), tuple(outputs), p["parameters"]
            )
            step = {"step": name, "status": "running", "started_at": _now()}
            manifest["steps"].append(step)
            checkpoint()
            try:
                step.update(adapter.run(folder, environment, cancelled, progress))
            except BaseException as exc:
                if hasattr(exc, "record"):
                    step.update(exc.record)
                raise
            adapter.qc(reference)
            if name == "anatomical_extraction":
                _, mask = image3d(outputs[1], reference)
                if not np.array_equal(np.unique(mask), [0, 1]):
                    raise ValueError("BET mask must contain binary foreground and background")
                save3d(mask.astype(np.uint8), reference, folder / "brain-mask.nii.gz")
                save3d(values * mask, reference, folder / "t1w-brain.nii.gz")
            elif name == "anatomical_tissues":
                maps = np.stack([image3d(path, reference)[1] for path in outputs[:3]])
                _, support = image3d(folder / "brain-mask.nii.gz", reference)
                _, brain = image3d(folder / "t1w-brain.nii.gz", reference)
                posterior_checks = posterior_support(maps, brain, support)
                registration_reference = folder / "t1w-brain.nii.gz"
                if opts["mode"] == "raw_t1":
                    _, field = image3d(folder / "tissue_bias.nii.gz", reference)
                    if np.any(field[support > 0] <= 0):
                        raise ValueError("Nonpositive anatomical FAST field inside extraction")
                    registration_reference = folder / "tissue_restore.nii.gz"
                    manifest["validation"]["FAST_bias"] = {
                        "positive_inside_extraction": True,
                        "finite": True,
                        "range_inside_extraction": [
                            float(field[support > 0].min()),
                            float(field[support > 0].max()),
                        ],
                        "scientific_acceptance": "pending",
                    }
                shutil.copyfile(registration_reference, folder / "registration-reference.nii.gz")
                wm = (maps[2] >= 0.5).astype(np.uint8)
                if not np.array_equal(np.unique(wm), [0, 1]):
                    raise ValueError("White-matter candidate is empty or full")
                save3d(wm, reference, folder / "wm-mask.nii.gz")
                manifest["validation"]["tissues"] = {
                    "finite": True,
                    "posterior_sum_verified": True,
                    "classes": ["CSF", "GM", "WM"],
                    "WM_threshold": 0.5,
                    "mask_voxels": int(np.count_nonzero(support)),
                    "WM_voxels": int(wm.sum()),
                    "scientific_acceptance": "pending",
                    "actual_support": posterior_checks,
                }
            else:
                matrix = np.loadtxt(outputs[0])
                if (
                    matrix.shape != (4, 4)
                    or not np.isfinite(matrix).all()
                    or not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-8)
                    or not np.allclose(
                        matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-3, rtol=0
                    )
                    or not np.isclose(np.linalg.det(matrix[:3, :3]), 1, atol=1e-3)
                ):
                    raise ValueError(
                        "FLIRT did not produce a finite rigid proper-rotation transform"
                    )
                manifest["validation"][name] = {
                    "matrix": matrix.tolist(),
                    "source": str(folder / "mean-b0.nii.gz"),
                    "reference": str(folder / "t1w-brain.nii.gz"),
                    "convention": "FSL scaled-mm; source and reference NIfTI required",
                    "four_dimensional_DWI_resampled": False,
                    "scientific_acceptance": "pending",
                }
            step.update(status="validated", finished_at=_now())
            checkpoint()
        manifest["status"] = "validated_pending_qc"
    except (ProcessingCancelled, KeyboardInterrupt) as exc:
        manifest.update(status="cancelled", error=str(exc))
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        checks = {p["input_paths"][key]: value["sha256"] for key, value in p["inputs"].items()}
        checks.update(p["extra_input_hashes"])
        try:
            dataset.assert_current()
            manifest["inputs_unchanged"] = all(
                hash_file(Path(path)) == digest for path, digest in checks.items()
            )
            if not manifest["inputs_unchanged"]:
                raise ValueError("Source/evidence preservation failed")
        except Exception as exc:
            manifest.update(status="failed", error=str(exc), inputs_unchanged=False)
        for step in manifest["steps"]:
            if step["status"] == "running":
                step.update(status=manifest["status"], finished_at=_now())
        manifest["outputs"] = {
            str(path.relative_to(folder)): {"sha256": hash_file(path), "bytes": path.stat().st_size}
            for path in sorted(folder.rglob("*"))
            if path.is_file() and path.name not in {destination.name, ".anatomy-manifest.tmp"}
        }
        manifest["finished_at"] = _now()
        checkpoint()
    try:
        from .anatomy_qc import export_anatomical_qc

        export_anatomical_qc(dataset, folder, manifest)
    except Exception as exc:
        manifest["qc_export_error"] = str(exc)
        (folder / "qc-export-failure.json").write_text(json.dumps({"error": str(exc)}))
    return manifest
