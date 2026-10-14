"""Optional matched AP/PA derivative using the verified HCP/FSL procedure.

The parent is always retained. This bounded adapter starts from an engine-verified
final N4 image, whose one shared field has already been applied. It executes local
eddy_combine, the unchanged pinned HCP rotated-gradient helper and the documented
common-FOV/zero-threshold recipe; it never reruns EDDY, N4 or spatial interpolation.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import nibabel as nib
import numpy as np

from . import execution
from .anatomy import anatomical_tools, verified_manifest
from .comparison import difference_metrics
from .dataset import DatasetPaths
from .execution import ExecutionPlan, _now, hash_file
from .loading import DatasetLoader
from .pairing import HCP_COMMIT, HCP_HELPER_SHA256, validate_pairing
from .process import ProcessingCancelled, check_cancel, run_command
from .qc import _json
from .workflow import ProcessingHistory, build_plan


def prepare_combination(
    dataset,
    history,
    operations,
    output,
    *,
    combination_options=None,
    fsl_directory=None,
    tool_directory=None,
    nthreads=4,
    cancelled=lambda: False,
    progress=lambda _: None,
):
    """Freeze explicit pairing, final-image lineage and actual installed tools."""
    if (
        set(operations) != {"combine"}
        or build_plan(dataset, history, operations).to_dict()["status"] != "eligible"
    ):
        raise ValueError("Select optional combination alone after verified correction and N4")
    options = dict(combination_options or {})
    required = {"pairing_spec", "parent_manifest", "hcp_helper"}
    if set(options) - (required | {"fsl_directory"}) or any(not options.get(k) for k in required):
        raise ValueError(
            "Combination requires a pairing specification, final N4 producer and pinned HCP helper"
        )
    for key in required:
        options[key] = str(Path(options[key]).expanduser().resolve(strict=True))
    if hash_file(Path(options["hcp_helper"]), cancelled) != HCP_HELPER_SHA256:
        raise ValueError("HCP helper differs from the verified pinned implementation")
    producer, checks = verified_manifest(
        Path(options["parent_manifest"]), "mrtrix_ants_bias_plan", cancelled
    )
    parent_folder = Path(producer["plan"]["output_directory"])
    if not {"bias-corrected.nii", "corrected.bval", "corrected.bvec"} <= set(
        producer.get("outputs", {})
    ):
        raise ValueError(
            "Parent producer must hash its final DWI and both rotated-gradient sidecars"
        )
    if dataset.paths.dwi.resolve() != (parent_folder / "bias-corrected.nii").resolve():
        raise ValueError("Select the final native N4 image named by this producer")
    if not np.array_equal(
        np.loadtxt(parent_folder / "corrected.bval").ravel(), dataset.gradients.bvals
    ) or not np.array_equal(
        np.loadtxt(parent_folder / "corrected.bvec", ndmin=2).T, dataset.gradients.bvecs
    ):
        raise ValueError("Parent gradients do not match the verified rotated-gradient output")
    handoff = producer["plan"].get("handoff", {})
    upstream_path = Path(handoff.get("record", ""))
    if hash_file(upstream_path, cancelled) != handoff.get("record_sha256"):
        raise ValueError("Parent EDDY producer evidence changed")
    upstream = _json(upstream_path)
    argv = upstream.get("scientific_argv") or upstream.get("argv", [])
    prefixes = [x.removeprefix("--out=") for x in argv if x.startswith("--out=")]
    if len(prefixes) != 1:
        raise ValueError("Cannot associate the original EDDY replacement map")
    outlier = Path(prefixes[0] + ".eddy_outlier_map").resolve(strict=True)
    entry = upstream.get("outputs", {}).get(str(outlier), {})
    if hash_file(outlier, cancelled) != entry.get("sha256"):
        raise ValueError("EDDY replacement map lacks unchanged producer provenance")
    flags = np.loadtxt(outlier, skiprows=1, ndmin=2)
    if (
        flags.shape != (dataset.n_volumes, dataset.shape[2])
        or not np.isfinite(flags).all()
        or not np.all((flags == 0) | (flags == 1))
    ):
        raise ValueError("EDDY outlier map does not match parent volume/slice order")
    progress("Verifying original measurement pairing and acquisition evidence…")
    pairing = validate_pairing(dataset, Path(options["pairing_spec"]), cancelled=cancelled)
    checks.update(pairing["source_hashes"])
    checks.update(
        {
            str(upstream_path): handoff["record_sha256"],
            str(outlier): entry["sha256"],
            options["hcp_helper"]: HCP_HELPER_SHA256,
        }
    )
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
    for path in checks:
        if folder.is_relative_to(Path(path).parent):
            raise ValueError("Choose output outside every input/evidence source directory")
    bindir = Path(options["fsl_directory"]) if options.get("fsl_directory") else fsl_directory
    fsl, fsldir = anatomical_tools(bindir, ("eddy_combine", "fslmaths"), cancelled)
    tools = execution.detect_mrtrix(
        tool_directory=tool_directory, names=("mrconvert", "mrinfo"), cancelled=cancelled
    )
    tools.update(fsl)
    tools["python"] = {
        "path": sys.executable,
        "version": sys.version,
        "numpy_version": np.__version__,
        "sha256": hash_file(Path(sys.executable)),
        "mechanism": "local CLI running unmodified pinned HCP source; imported local NumPy",
    }
    data = folder / "inputs"
    output_prefix = folder / "combined"
    commands = [
        (
            "image_combination",
            [
                tools["eddy_combine"]["path"],
                str(data / "A.nii"),
                str(data / "A.bval"),
                str(data / "A-nominal.bvec"),
                str(data / "series.txt"),
                str(data / "B.nii"),
                str(data / "B.bval"),
                str(data / "B-nominal.bvec"),
                str(data / "series.txt"),
                str(folder / "average"),
                "1",
            ],
        ),
        (
            "rotated_gradients",
            [
                sys.executable,
                str(folder / "average_bvecs.py"),
                str(data / "A.bval"),
                str(data / "A-rotated.bvec"),
                str(data / "B.bval"),
                str(data / "B-rotated.bvec"),
                str(output_prefix),
                "1",
                str(data / "series.txt"),
                str(data / "series.txt"),
            ],
        ),
        (
            "common_FOV",
            [
                tools["fslmaths"]["path"],
                str(folder / "average/data.nii.gz"),
                "-abs",
                "-Tmin",
                "-bin",
                "-fillh",
                str(folder / "fov-mask.nii.gz"),
            ],
        ),
        (
            "FOV_threshold",
            [
                tools["fslmaths"]["path"],
                str(folder / "average/data.nii.gz"),
                "-mas",
                str(folder / "fov-mask.nii.gz"),
                "-thr",
                "0",
                str(output_prefix) + ".nii.gz",
            ],
        ),
        (
            "combined_MIF",
            [
                tools["mrconvert"]["path"],
                str(output_prefix) + ".nii.gz",
                str(folder / "dwi.mif"),
                "-fslgrad",
                str(output_prefix) + ".bvec",
                str(output_prefix) + ".bval",
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
            "exact_gradient_export",
            [
                tools["mrinfo"]["path"],
                str(folder / "dwi.mif"),
                "-config",
                "BZeroThreshold",
                "0",
                "-bvalue_scaling",
                "false",
                "-export_grad_fsl",
                str(folder / "exported.bvec"),
                str(folder / "exported.bval"),
            ],
        ),
    ]
    mappings = [
        {
            "combined": i,
            "A_parent": a,
            "B_parent": b,
            "original_b": float(dataset.gradients.bvals[a]),
            "A_original_replaced_slices": np.flatnonzero(flags[a]).tolist(),
            "B_original_replaced_slices": np.flatnonzero(flags[b]).tolist(),
        }
        for i, (a, b) in enumerate(pairing["pairs"])
    ]
    p.update(
        kind="hcp_matched_combination_plan",
        operations=list(operations),
        combination_options=options,
        pairing=pairing,
        volume_mapping=mappings,
        extra_input_hashes=checks,
        tools=tools,
        HCP_commit=HCP_COMMIT,
        HCP_helper_sha256=HCP_HELPER_SHA256,
        commands=[
            {"step": name, "argv": argv, "display": shlex.join(argv)} for name, argv in commands
        ],
        implementation_hashes={
            str(Path(__file__)): hash_file(Path(__file__)),
            str(Path(__file__).with_name("pairing.py")): hash_file(
                Path(__file__).with_name("pairing.py")
            ),
        },
        outputs=[
            "combined.nii.gz",
            "combined.bval",
            "combined.bvec",
            "combined.idxs",
            "dwi.mif",
            "exported.bval",
            "exported.bvec",
            "fov-mask.nii.gz",
        ],
        resources={
            "estimated_peak_additional_bytes": int(np.prod(dataset.shape[:3]))
            * len(pairing["pairs"])
            * 4
            * 7,
            "estimated_tool_arrays_bytes": int(np.prod(dataset.shape[:3]))
            * len(pairing["pairs"])
            * 4
            * 3,
        },
    )
    p["parameters"] = {
        "nthreads": nthreads,
        "b0_max": dataset.validation.policy.b0_max,
        "combine_data_flag": 1,
        "N4_repeated": False,
        "spatial_interpolation": False,
        "parent_retained": True,
    }
    p["environment"]["overrides"].update(
        FSLDIR=str(fsldir),
        FSLOUTPUTTYPE="NIFTI_GZ",
        TMPDIR=str(folder / "scratch"),
        TMP=str(folder / "scratch"),
        TEMP=str(folder / "scratch"),
    )
    p["internal_actions"] = [
        "Stage explicit matched volumes and original/rotated gradients; retain all unpaired volumes in the unchanged parent.",
        "Execute FSL eddy_combine flag1 and the pinned HCP gradient helper. Record common-FOV masking and zero threshold separately from anatomical brain masking.",
        "Numerically verify each image pair, HCP effective encoding, MIF gradients and exact low-b sidecar export.",
    ]
    p["scientific_limits"] = pairing["scientific_limits"] + [
        "Matched measurements have motion-rotated approximate encodings; averaging does not create independent observations or prove improvement.",
        "Replaced slices may contain correlated model predictions. The original-volume/acquired-slice outlier map is carried forward; these are not resampled output slice coordinates. Replacement is not repeated.",
        "The parent was divided once by one shared N4 field; combination inherits that scaling. No N4 or spatial correction is repeated.",
        "This adapter currently requires the engine's final N4 producer; existing historical study derivatives remain available through their saved QC/comparison catalogs.",
    ]
    return ExecutionPlan(p)


def _staged_image(dataset, indices, target: Path, scratch: Path, cancelled):
    """Copy selected physical volumes via a disk buffer; never interpolate."""
    source = nib.load(dataset.paths.dwi, mmap="r", keep_file_open=True)
    shape = (*dataset.shape[:3], len(indices))
    values = np.memmap(scratch, mode="w+", dtype=np.float32, shape=shape, order="F")
    hashes = []
    try:
        for column, index in enumerate(indices):
            check_cancel(cancelled)
            volume = np.asarray(source.dataobj[..., index], dtype=np.float32)
            if not np.isfinite(volume).all():
                raise ValueError("Nonfinite parent diffusion volume")
            hashes.append(hashlib.sha256(volume.tobytes(order="C")).hexdigest())
            values[..., column] = volume
        values.flush()
        header = source.header.copy()
        header.set_data_dtype(np.float32)
        image = type(source)(values, source.affine, header)
        for form in ("qform", "sform"):
            matrix, code = getattr(source, "get_" + form)(coded=True)
            getattr(image, "set_" + form)(matrix, int(code))
        nib.save(image, target)
        staged = nib.load(target, mmap="r")
        for column, digest in enumerate(hashes):
            check_cancel(cancelled)
            actual = np.asarray(staged.dataobj[..., column], np.float32)
            if hashlib.sha256(actual.tobytes(order="C")).hexdigest() != digest:
                raise ValueError("Staging changed a parent volume's values or order")
        return {
            "parent_indices": indices,
            "float32_volume_sha256": hashes,
            "source_to_staged_values_verified": True,
        }
    finally:
        del values
        scratch.unlink(missing_ok=True)


@dataclass(frozen=True)
class CombinationStep:
    """Isolated external-tool contract; image verification remains separate."""

    name: str
    argv: tuple[str, ...]
    inputs: tuple[Path, ...]
    outputs: tuple[Path, ...]
    parameters: dict

    def validate(self):
        """Refuse missing staged inputs and output overwrites."""
        if any(not p.is_file() for p in self.inputs) or any(
            p.exists() or p.is_symlink() for p in self.outputs
        ):
            raise ValueError("Combination input missing or output already exists")

    def run(self, folder, environment, cancelled, progress):
        """Run local tools with exact command, resource and log records."""
        self.validate()
        return run_command(
            self.argv,
            cwd=folder,
            log=folder / "logs" / (self.name + ".log"),
            environment=environment,
            cancelled=cancelled,
            progress=progress,
        )

    def qc(self):
        """Do not equate command output existence with numerical verification."""
        if any(not p.is_file() or not p.stat().st_size for p in self.outputs):
            raise ValueError("Combination tool did not produce its declared output")
        return {"declared_outputs_present": True, "numerical_verification": "pending"}


def verify_combined(dataset, folder: Path, pairs: list, cancelled, progress) -> dict:
    """Check every output voxel and independent effective-encoding calculation."""
    images = {
        k: nib.load(folder / name, mmap="r", keep_file_open=True)
        for k, name in {
            "A": "inputs/A.nii",
            "B": "inputs/B.nii",
            "average": "average/data.nii.gz",
            "final": "combined.nii.gz",
        }.items()
    }
    for image in images.values():
        if image.shape != (*dataset.shape[:3], len(pairs)) or not np.allclose(
            image.affine, dataset.affine, atol=1e-5, rtol=0
        ):
            raise ValueError("Combination changed the native geometry or pair count")
    fov_image = nib.load(folder / "fov-mask.nii.gz")
    support = np.asarray(fov_image.dataobj)
    if (
        fov_image.shape != dataset.shape[:3]
        or not np.allclose(fov_image.affine, dataset.affine, atol=1e-5, rtol=0)
        or not np.all(np.isin(support, [0, 1]))
        or not support.any()
    ):
        raise ValueError("Invalid common-FOV support; no automatic repair")
    bval = np.loadtxt(folder / "combined.bval").ravel()
    bvec = np.loadtxt(folder / "combined.bvec", ndmin=2)
    if bval.shape != (len(pairs),) or bvec.shape != (3, len(pairs)) or not np.isfinite(bvec).all():
        raise ValueError("Combined gradients have invalid shape/values")
    if not np.array_equal(
        np.loadtxt(folder / "combined.idxs", ndmin=2), np.tile(np.arange(1, len(pairs) + 1), (2, 1))
    ):
        raise ValueError("HCP helper changed the explicit staged pairing")
    rows = []
    for index, (a, b) in enumerate(pairs):
        check_cancel(cancelled)
        progress(f"Verifying combined volume {index + 1}/{len(pairs)}")
        A, B, average, final = [
            np.asarray(images[key].dataobj[..., index], np.float32)
            for key in ("A", "B", "average", "final")
        ]
        expected = (A + B) * np.float32(0.5)
        if (
            not np.array_equal(average, expected)
            or not np.array_equal(final, np.maximum(expected * support, 0))
            or not np.isfinite(final).all()
        ):
            raise ValueError(
                f"Combined image does not match HCP mean/FOV/threshold recipe at pair {index}"
            )
        ga, gb = dataset.gradients.bvecs[[a, b]] * dataset.gradients.bvals[[a, b], None]
        eigenvalues, eigenvectors = np.linalg.eigh((np.outer(ga, ga) + np.outer(gb, gb)) * 0.5)
        expected_b = np.rint(np.sqrt(max(float(eigenvalues[-1]), 0)))
        if bval[index] != expected_b:
            raise ValueError("Combined effective b-value does not follow the pinned HCP recipe")
        if expected_b > dataset.validation.policy.b0_max and not np.allclose(
            np.outer(bvec[:, index], bvec[:, index]),
            np.outer(eigenvectors[:, -1], eigenvectors[:, -1]),
            atol=1e-8,
            rtol=0,
        ):
            raise ValueError("Combined principal encoding axis does not follow the HCP recipe")
        rows.append(
            {
                "combined": index,
                "A_parent": a,
                "B_parent": b,
                "exact_image_recipe": True,
                "nominal_b": float(dataset.gradients.bvals[a]),
                "effective_b": float(bval[index]),
                "negative_before_threshold": int((expected < 0).sum()),
                "nonzero_outside_FOV": int(np.count_nonzero(expected[support == 0])),
                "A_to_combined": difference_metrics(A, final),
                "B_to_combined": difference_metrics(B, final),
            }
        )
    if not np.array_equal(np.loadtxt(folder / "exported.bval").ravel(), bval) or not np.allclose(
        np.loadtxt(folder / "exported.bvec", ndmin=2), bvec, atol=1e-8, rtol=0
    ):
        raise ValueError("MIF gradient export changed effective b-values or vectors")
    loaded = DatasetLoader().load(
        DatasetPaths(folder / "combined.nii.gz", folder / "combined.bval", folder / "combined.bvec")
    )
    if loaded.dataset is None:
        raise ValueError("Combined output failed shared dataset loading")
    with loaded.dataset as combined:
        mif = execution._validate_conversion(combined, folder / "dwi.mif", cancelled, progress)
    return {
        "volumes": rows,
        "MIF": mif,
        "FOV_voxels": int(support.sum()),
        "N4_repeated": False,
        "spatial_interpolation": False,
        "parent_preserved": True,
        "scientific_acceptance": "pending",
    }


def execute_combination(
    dataset, plan, *, approval, cancelled=lambda: False, progress=lambda _: None
):
    """Run the reviewed procedure once in a new exclusive directory."""
    if approval != plan.digest:
        raise ValueError("Approve the exact combination plan")
    p = plan.to_dict()
    current = prepare_combination(
        dataset,
        ProcessingHistory.from_dict(p["history"]),
        tuple(p["operations"]),
        Path(p["output_directory"]),
        combination_options=p["combination_options"],
        fsl_directory=Path(p["tools"]["eddy_combine"]["path"]).parent,
        tool_directory=Path(p["tools"]["mrconvert"]["path"]).parent,
        nthreads=p["parameters"]["nthreads"],
        cancelled=cancelled,
        progress=progress,
    )
    if current.digest != plan.digest:
        raise ValueError("Combination evidence or settings changed; prepare again")
    folder = Path(p["output_directory"])
    if (
        shutil.disk_usage(folder.parent).free
        < p["resources"]["estimated_peak_additional_bytes"] + 256 * 1024**2
    ):
        raise ValueError("Insufficient disk for isolated combined images and retained evidence")
    folder.mkdir(exist_ok=False)
    for name in ("inputs", "scratch", "average", "logs"):
        (folder / name).mkdir()
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

    def checkpoint():
        """Publish attempt status atomically without touching earlier records."""
        temp = folder / ".combination-manifest.tmp"
        temp.write_text(json.dumps(manifest, indent=2, allow_nan=False))
        temp.replace(folder / "combination-manifest.json")

    environment = dict(os.environ, **p["environment"]["overrides"])
    try:
        pairs = p["pairing"]["pairs"]
        spec = p["pairing"]["specification"]
        nominal_path = (
            Path(p["pairing"]["source"]).parent / spec["nominal_gradients"]["bvec"]["path"]
        )
        nominal = np.loadtxt(nominal_path, ndmin=2)
        for side, label in enumerate(("A", "B")):
            indices = [pair[side] for pair in pairs]
            manifest.setdefault("staging", {})[label] = _staged_image(
                dataset,
                indices,
                folder / f"inputs/{label}.nii",
                folder / f"scratch/{label}.dat",
                cancelled,
            )
            np.savetxt(
                folder / f"inputs/{label}.bval", dataset.gradients.bvals[indices][None], fmt="%.17g"
            )
            np.savetxt(folder / f"inputs/{label}-nominal.bvec", nominal[:, indices], fmt="%.17g")
            np.savetxt(
                folder / f"inputs/{label}-rotated.bvec",
                dataset.gradients.bvecs[indices].T,
                fmt="%.17g",
            )
        lengths = spec["series_lengths"]
        np.savetxt(folder / "inputs/series.txt", np.column_stack([lengths, lengths]), fmt="%d")
        shutil.copyfile(p["combination_options"]["hcp_helper"], folder / "average_bvecs.py")
        (folder / "volume-mapping.json").write_text(json.dumps(p["volume_mapping"], indent=2))
        for command in p["commands"]:
            name = command["step"]
            contracts = {
                "image_combination": (
                    [
                        "inputs/A.nii",
                        "inputs/B.nii",
                        "inputs/A.bval",
                        "inputs/A-nominal.bvec",
                        "inputs/B.bval",
                        "inputs/B-nominal.bvec",
                        "inputs/series.txt",
                    ],
                    ["average/data.nii.gz", "average/bvals", "average/bvecs"],
                ),
                "rotated_gradients": (
                    [
                        "average_bvecs.py",
                        "inputs/A-rotated.bvec",
                        "inputs/B-rotated.bvec",
                        "inputs/A.bval",
                        "inputs/B.bval",
                        "inputs/series.txt",
                    ],
                    ["combined.bval", "combined.bvec", "combined.idxs"],
                ),
                "common_FOV": (["average/data.nii.gz"], ["fov-mask.nii.gz"]),
                "FOV_threshold": (["average/data.nii.gz", "fov-mask.nii.gz"], ["combined.nii.gz"]),
                "combined_MIF": (
                    ["combined.nii.gz", "combined.bval", "combined.bvec"],
                    ["dwi.mif"],
                ),
                "exact_gradient_export": (["dwi.mif"], ["exported.bval", "exported.bvec"]),
            }
            inputs, outputs = contracts[name]
            step = {"step": name, "status": "running", "started_at": _now()}
            manifest["steps"].append(step)
            checkpoint()
            adapter = CombinationStep(
                name,
                tuple(command["argv"]),
                tuple(folder / x for x in inputs),
                tuple(folder / x for x in outputs),
                p["parameters"],
            )
            try:
                step.update(adapter.run(folder, environment, cancelled, progress))
                step["output_check"] = adapter.qc()
            except BaseException as exc:
                if hasattr(exc, "record"):
                    step.update(exc.record)
                raise
            step.update(status="command_completed", finished_at=_now())
            checkpoint()
        manifest["validation"] = verify_combined(dataset, folder, pairs, cancelled, progress)
        manifest["status"] = "validated_pending_qc"
        for step in manifest["steps"]:
            step["status"] = "validated"
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
                raise ValueError("Combination source preservation failed")
        except Exception as exc:
            manifest.update(status="failed", error=str(exc), inputs_unchanged=False)
        for step in manifest["steps"]:
            if step["status"] == "running":
                step.update(status=manifest["status"], finished_at=_now())
        manifest["outputs"] = {
            str(path.relative_to(folder)): {"sha256": hash_file(path), "bytes": path.stat().st_size}
            for path in sorted(folder.rglob("*"))
            if path.is_file()
            and path.name not in {"combination-manifest.json", ".combination-manifest.tmp"}
        }
        manifest["finished_at"] = _now()
        checkpoint()
    try:
        from .combination_qc import export_combination_qc

        export_combination_qc(dataset, folder, manifest)
    except Exception as exc:
        manifest["qc_export_error"] = str(exc)
        checkpoint()
        (folder / "qc-export-failure.json").write_text(json.dumps({"error": str(exc)}))
    return manifest
