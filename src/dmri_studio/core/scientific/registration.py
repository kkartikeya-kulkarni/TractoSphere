"""Registration adapter for the shared scientific executor (no new optimizer)."""

from __future__ import annotations

import html
import json
import shlex
from pathlib import Path

import nibabel as nib
import numpy as np

from ..anatomy import anatomical_tools
from ..atlas import geometry_record
from ..execution import (
    ExecutionPlan,
    _environment_contract,
    _publish_json,
    detect_mrtrix,
    execute_artifact_task,
    hash_file,
)
from ..process import check_cancel
from ..scene import ImageVolume
from ..spatial import ImageSpace, chain, matrix, transform_record, validate_transform_file
from ..storage import require_local_processing
from .contracts import Artifact, ImportRequest
from .dti import _fresh, _revision
from .imports import import_artifact
from .planning import Task, build_workflow
from .records import read_json, save_selection
from .tracking import TrackingStep

LIMITS = [
    "Processing completion and numerical integrity do not establish anatomical alignment. QC_REVIEW_REQUIRED; acceptance unassigned.",
    "FLIRT matrices are image-dependent FSL scaled-voxel mm, not generic world affines. Warp inversion uses native invwarp, never matrix inversion.",
    "Atlas/ROI nearest-neighbor resampling preserves IDs but can lose small parcels; compare anatomy in all three planes.",
    "No DWI time series, gradients, tensors, SH FODs or tractograms are transformed. Partial-volume resampling is blocked pending fraction validation.",
    "EPI registration does not correct susceptibility distortion; epi_reg supplies no fieldmap inputs and requires supplied WM segmentation.",
    "ANTs binary/composite and nonlinear imports are unsupported; only validated single ITK text affines can be converted by MRtrix.",
]


def space(artifact):
    d = artifact["declarations"]
    return ImageSpace.create(
        artifact["path"],
        d.get("space_kind"),
        d.get("space_id"),
        d.get("subject_id"),
        d.get("acquisition_id"),
        d.get("session_id", ""),
    )


def associated(image, frame, hashes):
    """Require exact reference or explicit hash-bound same-space association evidence."""
    if image["kind"] == "atlas_probability":
        d = image["declarations"]
        current = ImageSpace(
            d.get("space_kind"),
            d.get("space_id"),
            d.get("subject_id"),
            d.get("acquisition_id"),
            image["path"],
            hash_file(Path(image["path"])),
            d.get("session_id", ""),
        )
    else:
        current = space(image)
    if current == ImageSpace(**frame):
        return
    if any(
        getattr(current, key) != frame[key]
        for key in ("kind", "id", "subject", "acquisition", "session")
    ):
        raise ValueError(
            "Moving image does not belong to transform source space; possible double registration"
        )
    evidence = Path(image["declarations"].get("space_review", ""))
    if not evidence.is_file() or evidence.stat().st_size > 1024**2:
        raise ValueError(
            "Different image in transform frame needs a hash-bound space_review; matching geometry is insufficient"
        )
    r = read_json(evidence.read_text())
    if (
        r.get("schema") != "tractosphere.space-association/1"
        or r.get("image_sha256") != current.sha256
        or r.get("reference_sha256") != frame["sha256"]
        or not r.get("review_notes")
        or r.get("status") != "reviewed"
    ):
        raise ValueError("Invalid same-space image association evidence")
    hashes[str(evidence.resolve())] = hash_file(evidence)


def prepare_registration(
    artifacts,
    tasks,
    output_directory,
    *,
    nthreads=2,
    tool_directory=None,
    fsl_directory=None,
    cancelled=lambda: False,
    progress=lambda _: None,
):
    """Compile a reviewed single registration node; batch supplies verified dependencies."""
    if len(tasks) != 1 or not tasks[0].algorithm.startswith("registration."):
        raise ValueError("Select one registration operation per immutable attempt")
    if isinstance(nthreads, bool) or not isinstance(nthreads, int) or not 1 <= nthreads <= 4:
        raise ValueError("Registration requires 1..4 threads")
    task = tasks[0]
    if any(not isinstance(v, str) for v in task.inputs.values()):
        raise ValueError(
            "Import verified producer outputs before registration; batch resolves dependencies"
        )
    if task.algorithm == "registration.reference":
        from .registration_reference import prepare_reference

        return prepare_reference(
            artifacts,
            tasks,
            output_directory,
            nthreads=nthreads,
            tool_directory=tool_directory,
            cancelled=cancelled,
            progress=progress,
        )
    if task.algorithm == "registration.qc":
        from ..registration_qc import prepare_qc

        return prepare_qc(
            artifacts,
            tasks,
            output_directory,
            nthreads=nthreads,
            tool_directory=tool_directory,
            cancelled=cancelled,
            progress=progress,
        )
    artifacts = _fresh(
        tuple(a for a in artifacts if a.id in task.inputs.values()), cancelled, progress
    )
    proposal = build_workflow(artifacts, tasks, verify_sources=False).to_dict()
    errors = [r["message"] for r in proposal["issues"] if r["severity"] == "error"]
    if errors:
        raise ValueError("; ".join(errors))
    inventory = {a.id: a.to_dict() for a in artifacts}
    bound = {k: inventory[v] for k, v in task.inputs.items()}
    hashes = {s["path"]: s["sha256"] for a in artifacts for s in a.payload["sources"]}
    folder = Path(output_directory).expanduser().absolute()
    if folder.exists() or folder.is_symlink() or not folder.parent.is_dir():
        raise ValueError("Choose a new registration attempt under an existing output parent")
    if any(folder.is_relative_to(Path(p).resolve().parent) for p in hashes):
        raise ValueError("Place derivatives outside original input directories")
    method = task.algorithm.removeprefix("registration.")
    p = dict(task.parameters)
    commands, outputs = [], []
    specs = {}
    fsl_names, mr_names = set(), set()
    from ..dependencies import fsl_root

    root = fsl_root(fsl_directory)
    env = _environment_contract(cancelled)
    env["overrides"].update(
        OMP_NUM_THREADS=str(nthreads),
        OPENBLAS_NUM_THREADS="1",
        FSLPARALLEL="0",
        FSLOUTPUTTYPE="NIFTI_GZ",
    )
    env["unset"] = ["MRTRIX_OVERWRITE", "MRTRIX_QUIET"]

    def command(exe, args, files, kind):
        check_cancel(cancelled)
        (
            mr_names if exe in {"mrtransform", "transformconvert", "transformcalc"} else fsl_names
        ).add(exe)
        commands.append(
            dict(
                task=task.id,
                step=f"{task.id}-{len(commands) + 1}-{exe}",
                executable=exe,
                args=list(map(str, args)),
                inputs=list(hashes),
                outputs=files,
                kind=kind,
            )
        )
        outputs.extend(files)

    def native_transform(t):
        if t["format"] == "itk_affine_text":
            name = f"converted-{len(commands)}.txt"
            command(
                "transformconvert",
                [t["file"], "itk_import", folder / name],
                [name],
                "mrtrix_linear",
            )
            return {**t, "format": "mrtrix_linear", "file": str(folder / name)}
        return t

    registered = "registered.nii.gz"
    transform_name = "transform.mat"
    result_transform = None
    if method in {"flirt", "epi_reg", "fnirt"}:
        src, dst = space(bound["moving"]), space(bound["reference"])
        if src.image == dst.image:
            raise ValueError("Moving and reference images must be distinct source data")
        if src.id == dst.id:
            raise ValueError("Registration source and target spaces must differ")
        if src.subject != dst.subject and src.kind != "MNI_TEMPLATE" and dst.kind != "MNI_TEMPLATE":
            raise ValueError("Registration cannot associate different participants")
        for item in (bound["moving"], bound["reference"]):
            if item["declarations"].get("anatomical_modality") not in {
                "T1w",
                "T2w",
                "b0",
                "mean_b0",
                "FA",
                "MD",
            }:
                raise ValueError(
                    "Declare actual anatomical contrast; unknown contrast is not eligible"
                )
            if item["declarations"].get("space_kind") == "DWI_NATIVE" and not item[
                "declarations"
            ].get("reference_history"):
                raise ValueError(
                    "DWI reference needs recorded correction/history evidence; no preprocessing is inferred"
                )
        initial = bound.get("initial", {}).get("metadata")
        if initial and (
            initial["source"] != src.to_dict()
            or initial["target"] != dst.to_dict()
            or initial["format"] != "fsl_flirt"
        ):
            raise ValueError(
                "Initial FLIRT transform does not bind these exact source/reference images"
            )
        if method == "flirt":
            args = [
                "-in",
                src.image,
                "-ref",
                dst.image,
                "-dof",
                6 if p["model"] == "rigid" else 12,
                "-cost",
                p["cost"],
                "-interp",
                "trilinear",
                "-omat",
                folder / transform_name,
                "-out",
                folder / registered,
            ]
            if initial:
                args += ["-init", initial["file"]]
            command("flirt", args, [transform_name, registered], "registration")
        elif method == "epi_reg":
            if (
                src.kind != "DWI_NATIVE"
                or dst.kind != "T1_NATIVE"
                or bound["reference"]["declarations"].get("anatomical_modality") != "T1w"
            ):
                raise ValueError("epi_reg is EPI/b0 to T1; inverse is a separate operation")
            if bound["t1_brain"]["path"] == bound["reference"]["path"]:
                raise ValueError(
                    "Brain-extracted T1 must be a distinct prepared image from the reference T1"
                )
            if bound["t1_brain"]["declarations"].get("anatomical_preparation") != "brain_extracted":
                raise ValueError(
                    "Declare and verify brain-extracted T1 preparation; a whole-head or unknown T1 is not a substitute"
                )
            if bound["wmseg"]["declarations"].get("mask_role") != "white_matter_segmentation":
                raise ValueError(
                    "Declare the binary mask as a white-matter segmentation; an arbitrary brain mask is not suitable for BBR"
                )
            for key in ("t1_brain", "wmseg"):
                associated(bound[key], dst.to_dict(), hashes)
                if bound[key]["geometry"] != bound["reference"]["geometry"]:
                    # Compare physical grid only; descriptions can legitimately differ.
                    a, b = bound[key]["geometry"], bound["reference"]["geometry"]
                    if a["shape"] != b["shape"] or not np.allclose(
                        a["affine_mm"], b["affine_mm"], atol=1e-5
                    ):
                        raise ValueError("BBR masks must match the native T1 grid")
            transform_name = "registered.mat"
            command(
                "epi_reg",
                [
                    f"--epi={src.image}",
                    f"--t1={dst.image}",
                    f"--t1brain={bound['t1_brain']['path']}",
                    f"--wmseg={bound['wmseg']['path']}",
                    f"--out={folder / 'registered'}",
                ],
                [transform_name, registered],
                "registration",
            )
            fsl_names.update(
                ("flirt", "fslmaths", "fslval", "imtest", "imcp", "remove_ext", "fslorient")
            )
        else:
            if (
                bound["moving"]["declarations"].get("anatomical_modality") != "T1w"
                or bound["reference"]["declarations"].get("anatomical_modality") != "T1w"
                or not initial
            ):
                raise ValueError(
                    "FNIRT requires structural T1/T1 inputs and their verified affine initialization"
                )
            if not all(
                bound[k]["declarations"].get("whole_head") is True for k in ("moving", "reference")
            ):
                raise ValueError(
                    "FNIRT requires explicit whole-head coverage; no implicit brain-extracted input"
                )
            transform_name = "transform-coefficients.nii.gz"
            command(
                "fnirt",
                [
                    f"--in={src.image}",
                    f"--ref={dst.image}",
                    f"--aff={initial['file']}",
                    f"--cout={folder / transform_name}",
                    f"--iout={folder / registered}",
                    f"--jout={folder / 'jacobian.nii.gz'}",
                    f"--logout={folder / 'fnirt.log'}",
                    "--miter=" + ",".join([str(p["iterations"])] * 4),
                    "--warpres=" + ",".join([str(p["warp_resolution"])] * 3),
                ],
                [transform_name, registered, "jacobian.nii.gz", "fnirt.log"],
                "nonlinear_registration",
            )
        result_transform = dict(
            source=src.to_dict(),
            target=dst.to_dict(),
            format="fsl_coefficients" if method == "fnirt" else "fsl_flirt",
            file=transform_name,
        )
        specs["registered"] = bound["moving"]["kind"]
    elif method.startswith("apply."):
        t = native_transform(bound["transform"]["metadata"])
        src, dst = ImageSpace(**t["source"]), ImageSpace(**t["target"])
        associated(bound["moving"], t["source"], hashes)
        if space(bound["reference"]) != dst:
            raise ValueError("Selected reference is not the transform's exact target image")
        moving = bound["moving"]["path"]
        categorical = method in {"apply.labels", "apply.roi"}
        if method == "apply.probability":
            if t["format"] == "fsl_flirt":
                converted = "probability-transform.txt"
                command(
                    "transformconvert",
                    [t["file"], src.image, dst.image, "flirt_import", folder / converted],
                    [converted],
                    "mrtrix_linear",
                )
                t = {**t, "format": "mrtrix_linear", "file": str(folder / converted)}
            if t["format"] != "mrtrix_linear":
                raise ValueError(
                    "Probability-channel nonlinear resampling is not yet verified; use a verified linear transform"
                )
        if t["format"] == "fsl_flirt":
            # FLIRT matrices are tied to the original moving grid; do not reuse
            # them with a differently sampled atlas even in the same frame.
            a, b = nib.load(moving), nib.load(src.image)
            if a.shape != b.shape or not np.allclose(a.affine, b.affine, atol=1e-5):
                raise ValueError(
                    "Different moving grid: convert the FLIRT transform to MRtrix first; no incorrect scaled-voxel application"
                )
            command(
                "flirt",
                [
                    "-in",
                    moving,
                    "-ref",
                    dst.image,
                    "-applyxfm",
                    "-init",
                    t["file"],
                    "-interp",
                    "nearestneighbour" if categorical else "trilinear",
                    "-out",
                    folder / registered,
                ],
                [registered],
                "resample",
            )
        elif t["format"] == "mrtrix_linear":
            command(
                "mrtransform",
                [
                    moving,
                    folder / registered,
                    "-linear",
                    t["file"],
                    "-template",
                    dst.image,
                    "-interp",
                    "nearest" if categorical else "linear",
                    "-nthreads",
                    nthreads,
                ],
                [registered],
                "resample",
            )
        elif t["format"] in {"fsl_coefficients", "fsl_relative"}:
            args = [
                f"--in={moving}",
                f"--ref={dst.image}",
                f"--warp={t['file']}",
                f"--out={folder / registered}",
                "--interp=" + ("nn" if categorical else "trilinear"),
            ]
            if t["format"] == "fsl_relative":
                args.append("--rel")
            # FSL sampling geometry still needs the original source grid.
            a, b = nib.load(moving), nib.load(src.image)
            if a.shape != b.shape or not np.allclose(a.affine, b.affine, atol=1e-5):
                raise ValueError(
                    "FSL warp source grid differs; this import needs a separately validated grid conversion"
                )
            command("applywarp", args, [registered], "resample")
        else:
            raise ValueError("Unsupported resampling transform")
        specs["registered"] = bound["moving"]["kind"]
    elif method in {"invert", "convert", "compose"}:
        t = bound.get("transform", bound.get("first"))["metadata"]
        src, dst = ImageSpace(**t["source"]), ImageSpace(**t["target"])
        fmt = t["format"]
        if method == "convert":
            transform_name = "transform-mrtrix.txt"
            if fmt not in {"fsl_flirt", "itk_affine_text"}:
                raise ValueError("Only FSL FLIRT or ITK text affines may use transformconvert")
            args = (
                [t["file"], src.image, dst.image, "flirt_import"]
                if fmt == "fsl_flirt"
                else [t["file"], "itk_import"]
            )
            command(
                "transformconvert",
                [*args, folder / transform_name],
                [transform_name],
                "mrtrix_linear",
            )
            fmt = "mrtrix_linear"
        elif method == "invert":
            if fmt == "fsl_flirt":
                command(
                    "convert_xfm",
                    ["-omat", folder / transform_name, "-inverse", t["file"]],
                    [transform_name],
                    "fsl_flirt",
                )
            elif fmt == "mrtrix_linear":
                command(
                    "transformcalc",
                    [t["file"], "invert", folder / transform_name],
                    [transform_name],
                    "mrtrix_linear",
                )
            elif fmt in {"fsl_coefficients", "fsl_relative"}:
                transform_name = "transform-warp.nii.gz"
                args = [
                    f"--warp={t['file']}",
                    f"--ref={src.image}",
                    f"--out={folder / transform_name}",
                ]
                if fmt == "fsl_relative":
                    args += ["--rel"]
                command("invwarp", args, [transform_name], "fsl_relative")
                fmt = "fsl_relative"
            else:
                raise ValueError("Convert ITK text affine before inversion")
            src, dst = dst, src
        else:
            u = bound["second"]["metadata"]
            source, target = chain([t, u])
            src, dst = ImageSpace(**source), ImageSpace(**target)
            if fmt == u["format"] == "fsl_flirt":
                command(
                    "convert_xfm",
                    ["-omat", folder / transform_name, "-concat", u["file"], t["file"]],
                    [transform_name],
                    "fsl_flirt",
                )
            elif fmt in {"fsl_flirt", "fsl_coefficients", "fsl_relative"} and u["format"] in {
                "fsl_flirt",
                "fsl_coefficients",
                "fsl_relative",
            }:
                transform_name = "transform-warp.nii.gz"
                args = [f"--ref={dst.image}", f"--out={folder / transform_name}", "--relout"]
                args += [("--premat=" if fmt == "fsl_flirt" else "--warp1=") + t["file"]]
                args += [("--postmat=" if u["format"] == "fsl_flirt" else "--warp2=") + u["file"]]
                if "fsl_relative" in {fmt, u["format"]}:
                    args.append("--rel")
                command("convertwarp", args, [transform_name], "fsl_relative")
                fmt = "fsl_relative"
            else:
                raise ValueError(
                    "Composition supports native FSL chains; mixed conventions are blocked"
                )
        result_transform = dict(
            source=src.to_dict(), target=dst.to_dict(), format=fmt, file=transform_name
        )
    else:
        raise ValueError("Unsupported registration operation")
    tools, software = {}, {}
    if fsl_names:
        if (
            root is None
            or not root.is_absolute()
            or not (root / "etc/fslversion").is_file()
            or (root / "etc/fslversion").read_text().strip() != "6.0.7.18"
        ):
            raise ValueError("Select verified FSL 6.0.7.18 with FSLDIR")
        tools, _ = anatomical_tools(root / "bin", sorted(fsl_names), cancelled)
        software[str(root / "etc/fslversion")] = hash_file(root / "etc/fslversion", cancelled)
        for path in (root / "etc/flirtsch").glob("*.sch"):
            software[str(path)] = hash_file(path, cancelled)
        env["overrides"].update(FSLDIR=str(root))
    if mr_names:
        tools.update(
            detect_mrtrix(
                names=tuple(sorted(mr_names)), tool_directory=tool_directory, cancelled=cancelled
            )
        )
        if any(tools[n]["version"] != "3.0.5" for n in mr_names):
            raise ValueError("MRtrix transform adapter is verified for 3.0.5")
    for c in commands:
        c["argv"] = [tools[c.pop("executable")]["path"], *c.pop("args")]
        c["display"] = shlex.join(c["argv"])
    for t in tools.values():
        if t.get("version_record"):
            software[t["version_record"]] = t["version_record_sha256"]
    require_local_processing([folder, *hashes])
    from ..registration_resources import registration_resources, require_registration_resources

    reservation = registration_resources([task], artifacts)
    require_registration_resources(reservation, folder)
    estimate = reservation["memory_bytes"]
    outputs += ["scientific-selection.json", "qc-metrics.json", "qc-report.html", "qc-catalog.json"]
    if result_transform:
        outputs.append("transform.json")
    implementation = [
        Path(__file__),
        Path(__file__).with_name("registration_registry.py"),
        Path(__file__).parents[1] / "spatial.py",
        Path(__file__).parents[1] / "registration_resources.py",
        Path(__file__).parents[1] / "execution.py",
    ]
    return ExecutionPlan(
        dict(
            kind="registration_execution_plan",
            schema_version=1,
            artifacts=[a.to_dict() for a in artifacts],
            tasks=[task.to_dict()],
            output_directory=str(folder),
            tools=tools,
            environment=env,
            commands=commands,
            outputs=outputs,
            extra_input_hashes=hashes,
            software_hashes=software,
            source=src.to_dict(),
            reference=dst.to_dict(),
            bound=bound,
            transform=result_transform,
            product_specs=specs,
            parameters=dict(
                nthreads=nthreads,
                estimated_working_bytes=estimate,
                resource_reservation=reservation,
                storage_margin_bytes=5 * 1024**3,
            ),
            issues=proposal["issues"],
            scientific_limits=LIMITS,
            build=_revision(),
            compiler="tractosphere.registration/1",
            implementation_hashes={
                str(path): hash_file(path, cancelled) for path in implementation
            },
            mrtrix_directory=str(tool_directory) if tool_directory else None,
        )
    )


def image_qc(path, reference, source=None, categorical=False, probability=False):
    image = nib.load(path)
    ref = nib.load(reference)
    geometry = geometry_record(ImageVolume(path))
    if image.shape[:3] != ref.shape[:3] or not np.allclose(
        image.affine, ref.affine, atol=1e-4, rtol=0
    ):
        raise ValueError("Registered output grid differs from approved reference")
    values = np.asanyarray(image.dataobj)
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite registered output")
    result = dict(
        geometry=geometry,
        nonzero_voxels=int(np.count_nonzero(values)),
        empty=not bool(np.any(values)),
        scientific_review="QC_REVIEW_REQUIRED",
    )
    if probability:
        original = nib.load(source)
        if (
            image.ndim != 4
            or image.shape[3] != original.shape[3]
            or (values < 0).any()
            or (values > 1).any()
        ):
            raise ValueError(
                "Probability resampling changed channel count or produced invalid fractions"
            )
        result.update(
            channels=image.shape[3],
            probability_minimum=float(values.min()),
            probability_maximum=float(values.max()),
            thresholding="not performed",
            interpolation="linear",
        )
    elif image.ndim != 3:
        raise ValueError("Scalar/label registration requires a 3D output")
    if categorical:
        original = np.asanyarray(nib.load(source).dataobj)
        labels, counts = np.unique(values, return_counts=True)
        old, oldcounts = np.unique(original, return_counts=True)
        if (
            (values < 0).any()
            or not np.array_equal(values, np.rint(values))
            or not set(labels) <= set(old) | {0}
        ):
            raise ValueError("Categorical resampling introduced fractional or unknown labels")
        result.update(
            source_labels={str(int(k)): int(v) for k, v in zip(old, oldcounts, strict=True)},
            output_labels={str(int(k)): int(v) for k, v in zip(labels, counts, strict=True)},
            lost_labels=sorted(int(x) for x in set(old) - set(labels) - {0}),
            labels_preserved=True,
        )
    return result


def verify_step(folder, plan, c, cancelled=lambda: False):
    if plan.get("operation") == "reference":
        from .registration_reference import verify_reference_step

        return verify_reference_step(folder, plan, c, cancelled)
    result = {}
    for name in c["outputs"]:
        path = folder / name
        if not path.is_file() or not path.stat().st_size:
            raise ValueError("Incomplete registration output: " + name)
        if name == "registered.nii.gz":
            moving = plan["bound"]["moving"]
            result[name] = image_qc(
                path,
                plan["reference"]["image"],
                moving["path"],
                moving["kind"] in {"roi", "atlas_labels"},
                moving["kind"] == "atlas_probability",
            )
        elif name == "jacobian.nii.gz":
            a = np.asanyarray(nib.load(path).dataobj)
            if not np.isfinite(a).all():
                raise ValueError("Invalid nonlinear Jacobian")
            result[name] = dict(
                minimum=float(a.min()),
                maximum=float(a.max()),
                nonpositive=int(np.count_nonzero(a <= 0)),
                review_required=True,
            )
        elif name.endswith((".mat", ".txt")):
            result[name] = dict(matrix=matrix(path).tolist())
        elif "warp" in name or "coefficients" in name:
            result[name] = validate_transform_file(
                path, "fsl_coefficients" if "coefficients" in name else "fsl_relative"
            )
    return result


def verify_registration(folder, plan, cancelled=lambda: False, progress=lambda _: None):
    if plan.get("operation") == "reference":
        from .registration_reference import verify_reference

        return verify_reference(folder, plan, cancelled)
    if plan.get("operation") == "atlas_qc":
        from ..registration_qc import generate_qc

        return generate_qc(folder, plan, cancelled, progress)
    products, checks = [], {}
    for c in plan["commands"]:
        checks.update(verify_step(folder, plan, c, cancelled))
    if plan["transform"]:
        t = plan["transform"]
        record = transform_record(
            folder / t["file"],
            ImageSpace(**t["source"]),
            ImageSpace(**t["target"]),
            t["format"],
            software="FSL / MRtrix3",
            version="FSL 6.0.7.18 / MRtrix3 3.0.5",
            evidence="Native commands and exact images in execution-manifest.json; anatomical review still required",
            parameters=plan["tasks"][0]["parameters"],
        )
        _publish_json(folder / "transform.json", record)
        artifact = import_artifact(ImportRequest("spatial_transform", folder / "transform.json"))
        products.append(dict(role="transform", artifact=artifact.to_dict()))
    if plan["product_specs"]:
        d = plan["reference"]
        declarations = dict(
            subject_id=d["subject"],
            acquisition_id=d["acquisition"],
            session_id=d.get("session", ""),
            space_id=d["id"],
            space_kind=d["kind"],
            registration_status="QC_REVIEW_REQUIRED",
            registration_manifest=str(folder / "manifest.json"),
        )
        if plan["product_specs"]["registered"] == "atlas_probability":
            declarations["threshold"] = plan["bound"]["moving"]["declarations"]["threshold"]
        if plan["product_specs"]["registered"] == "anatomy":
            declarations["anatomical_modality"] = plan["bound"]["moving"]["declarations"].get(
                "anatomical_modality"
            )
        artifact = import_artifact(
            ImportRequest(
                plan["product_specs"]["registered"],
                folder / "registered.nii.gz",
                declarations=declarations,
                provenance="Native resampling; anatomical alignment and expert acceptance pending",
            ),
            cancelled=cancelled,
        )
        products.append(dict(role="registered", artifact=artifact.to_dict()))
    result = dict(
        execution="completed",
        numerical_verification="passed",
        scientific_review="QC_REVIEW_REQUIRED",
        scientific_acceptance="unassigned",
        checks=checks,
        products=products,
        findings=LIMITS,
    )
    publish_qc(folder, plan, result, plan["reference"]["subject"])
    return result


def publish_qc(folder, plan, result, subject):
    _publish_json(folder / "qc-metrics.json", result)
    with (folder / "qc-report.html").open("x") as f:
        f.write(
            '<!doctype html><meta charset="utf-8"><title>Registration QC</title><style>body{font:15px system-ui;margin:36px;max-width:1100px}pre{white-space:pre-wrap}</style><h1>Registration QC — review required</h1><p>Inspect axial, sagittal and coronal anatomy. Numerical integrity is not scientific acceptance.</p><pre>'
            + html.escape(json.dumps(result, indent=2))
            + "</pre><h2>Commands</h2><pre>"
            + html.escape("\n".join(c["display"] for c in plan["commands"]))
            + "</pre>"
        )
    _publish_json(
        folder / "qc-catalog.json",
        dict(
            schema_version=1,
            kind="dmri_qc_catalog",
            producer="TractoSphere registration adapter",
            identity=dict(subject=subject),
            stages=[
                dict(
                    id="registration",
                    title="Registration spatial QC",
                    execution="completed",
                    technical="passed",
                    automated_qc="pending",
                    scientific_review="required",
                    findings=LIMITS,
                    artifacts=[
                        dict(
                            kind="report",
                            label="Registration QC",
                            path="qc-report.html",
                            sha256=hash_file(folder / "qc-report.html"),
                        )
                    ],
                )
            ],
        ),
    )
    return result


class RegistrationAdapter:
    """Same immutable engine/process groups, approval and journal as previous stages."""

    def run(self, plan, *, approval, cancelled=lambda: False, progress=lambda _: None):
        data = plan.to_dict()
        folder = Path(data["output_directory"])
        artifacts = tuple(Artifact(a) for a in data["artifacts"])
        tasks = tuple(Task.from_dict(t) for t in data["tasks"])

        def sources():
            for p, expected in {
                **data["extra_input_hashes"],
                **data["software_hashes"],
                **{t["path"]: t["sha256"] for t in data["tools"].values()},
            }.items():
                if hash_file(Path(p)) != expected:
                    raise ValueError("Registration source/tool changed: " + p)

        return execute_artifact_task(
            plan,
            approval=approval,
            recompile=lambda: prepare_registration(
                artifacts,
                tasks,
                folder,
                nthreads=data["parameters"]["nthreads"],
                tool_directory=Path(data["mrtrix_directory"]) if data["mrtrix_directory"] else None,
                cancelled=cancelled,
                progress=progress,
            ),
            setup=lambda target: save_selection(
                target / "scientific-selection.json", artifacts, tasks
            ),
            step_factory=lambda c: TrackingStep(
                c["step"],
                tuple(c["argv"]),
                tuple(map(Path, c["inputs"])),
                tuple(folder / n for n in c["outputs"]),
                data["parameters"],
            ),
            verify_step=lambda c: verify_step(folder, data, c, cancelled),
            verify=lambda _: verify_registration(folder, data, cancelled, progress),
            verify_sources=sources,
            cancelled=cancelled,
            progress=progress,
        )
