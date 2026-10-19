"""Version-bound MRtrix CSD tasks over TractoSphere's existing artifact executor.

Python: prepare_csd(artifacts, tasks, NEW_directory), inspect its exact commands
and digest, then execute_plan(None, plan, approval=plan.digest). No solver,
preprocessing, tract generation, source edits or automatic acceptance occurs.
"""

from __future__ import annotations

import math
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

from ..execution import (
    ExecutionPlan,
    MRtrixStep,
    _environment_contract,
    detect_mrtrix,
    execute_artifact_task,
    hash_file,
)
from ..process import check_cancel
from ..storage import require_local_processing
from .contracts import Artifact
from .dti import _aligned, _fresh, _revision, _text_table, _world_scheme, image_data
from .planning import OutputRef, Task, _order, build_workflow
from .responses import response_rows

ALGORITHMS = frozenset(
    {
        "dwi2response.dhollander",
        "dwi2response.tournier",
        "dwi2response.tax",
        "dwi2fod.csd",
        "dwi2fod.msmt_csd",
        "mtnormalise",
    }
)
LIMITS = [
    "Execution, numerical verification, automated QC and scientific acceptance are independent; expert review remains required.",
    "External preprocessing, tissue identity and subject/space correspondence retain declared or unknown provenance; no preprocessing is repeated.",
    "FODs are MRtrix real symmetric SH coefficients in world RAS, not scalar time series or diffusion tensors. Negative higher-order SH coefficients are expected and are not FA violations.",
    "Response shell association and any exact row extraction are explicit in bindings/generated text. Original response values and files are unchanged.",
    "WM/GM/CSF amplitudes are response-dependent compartments, not automatically calibrated probabilities or histological tissue fractions.",
    "Normalization is a separate explicit operation. It changes amplitudes; normalized and unnormalized outputs are not expected to be equal.",
    "No glyph rendering, tractography generation, connectomics or batch scheduling is provided by this adapter.",
]
RESPONSE_LAYOUT_THRESHOLD = 256 * 1024**2

HELPERS = {
    "mrconvert",
    "mrinfo",
    "mrstats",
    "dwiextract",
    "mrcalc",
    "mrmath",
    "maskfilter",
    "mrthreshold",
    "mrcat",
    "dwi2tensor",
    "tensor2metric",
    "dwi2fod",
    "amp2response",
    "sh2peaks",
    "peaks2amp",
    "fod2fixel",
    "fixel2voxel",
    "fixel2peaks",
}


def sh_design(directions, lmax):
    """Real orthonormal even-degree SH design, used for rank/QC, never fitting.

    MRtrix m<0 sine / m>0 cosine real basis; phases do not affect angular rank.
    Actual amplitude evaluation is validated against MRtrix sh2amp in tests.
    """
    g = np.asarray(directions, dtype=float)
    g = g / np.linalg.norm(g, axis=1)[:, None]
    azimuth = np.arctan2(g[:, 1], g[:, 0])
    z = np.clip(g[:, 2], -1, 1)
    columns = []
    for degree in range(0, lmax + 1, 2):
        for order in range(-degree, degree + 1):
            m = abs(order)
            norm = np.sqrt(
                (2 * degree + 1)
                / (4 * np.pi)
                * np.exp(math.lgamma(degree - m + 1) - math.lgamma(degree + m + 1))
            )
            # Associated Legendre polynomial in NumPy's stable Legendre basis;
            # this is SH evaluation/QC, never a deconvolution solver.
            associated = (
                (-1) ** m
                * (1 - z * z) ** (m / 2)
                * np.polynomial.legendre.Legendre.basis(degree).deriv(m)(z)
            )
            term = norm * associated
            columns.append(
                term
                if not m
                else np.sqrt(2) * term * (np.sin(m * azimuth) if order < 0 else np.cos(m * azimuth))
            )
    return np.column_stack(columns)


def _help(path, *arguments):
    """Inspect the installed command, not assumed current upstream options."""
    text = subprocess.run(
        [str(path), *arguments, "-help"], capture_output=True, text=True, check=True, timeout=15
    )
    clean = text.stdout + text.stderr
    while "\b" in clean:
        clean = re.sub(".\b", "", clean)
    return clean


def _tools(tasks, directory, cancelled):
    """Bind compiled tools and full response-script dependency/runtime identity."""
    names = {
        "mrconvert",
        *(
            "dwi2response"
            if t.algorithm.startswith("dwi2response")
            else "dwi2fod"
            if t.algorithm.startswith("dwi2fod")
            else "mtnormalise"
            for t in tasks
        ),
    }
    response = any(t.algorithm.startswith("dwi2response") for t in tasks)
    if response:
        if os.name == "nt":
            raise ValueError(
                "Native Windows response-script child-process cancellation is not validated; use a verified POSIX environment. No WSL execution is implied."
            )
        names |= HELPERS
    tools = detect_mrtrix(names=tuple(sorted(names)), tool_directory=directory, cancelled=cancelled)
    if any(t["version"] != "3.0.5" for t in tools.values()):
        raise ValueError("CSD adapter accepts only its validated MRtrix3 3.0.5 release")
    scripts = {}
    if response:
        lib = Path(tools["dwi2response"]["path"]).parent.parent / "lib/mrtrix3"
        for method in {
            t.algorithm.split(".")[1] for t in tasks if t.algorithm.startswith("dwi2response")
        }:
            if not (lib / "dwi2response" / (method + ".py")).is_file():
                raise ValueError("Missing installed response algorithm module: " + method)
        for p in sorted(lib.rglob("*.py")):
            scripts[str(p)] = hash_file(p, cancelled)
        if not scripts:
            raise ValueError("Response Python library source unavailable for provenance")
        runtime = Path(sys.executable).resolve()
        scripts[str(runtime)] = hash_file(runtime, cancelled)
    for task in tasks:
        name = (
            "dwi2response"
            if task.algorithm.startswith("dwi2response")
            else "dwi2fod"
            if task.algorithm.startswith("dwi2fod")
            else "mtnormalise"
        )
        sub = (task.algorithm.split(".")[1],) if name == "dwi2response" else ()
        helptext = _help(tools[name]["path"], *sub)
        flags = ["-mask", "-nthreads"]
        flags += (
            ["-voxels", "-shells", "-lmax", "-scratch"]
            if name == "dwi2response"
            else ["-lmax", "-grad"]
            if name == "dwi2fod"
            else ["-order", "-balanced", "-check_norm", "-check_mask", "-check_factors"]
        )
        option_names = {
            "max_iters": "-max_iters",
            "number": "-number",
            "erode": "-erode",
            "peak_ratio": "-peak_ratio",
            "convergence": "-convergence",
            "niter": "-niter",
            "neg_lambda": "-neg_lambda",
            "norm_lambda": "-norm_lambda",
            "threshold": "-threshold",
            "predict_signal": "-predicted_signal",
            "iterations": "-niter",
        }
        flags += [flag for key, flag in option_names.items() if key in task.parameters]
        if any(f not in helptext for f in flags):
            raise ValueError(name + " required installed command options are unavailable")
    return tools, scripts


def _signal(base, mask, indices, cancelled, progress):
    """Full bounded input scan; CSD is not a log-tensor fit and uses its own QC."""
    domain = _aligned(Path(mask["path"]), base["geometry"]).astype(bool)
    maximum = np.full(domain.shape, -np.inf, "f4")
    zero = np.zeros(domain.shape, "u2")
    negative = 0
    selected = set(indices)
    checked = 0
    with image_data(Path(base["path"])) as (_, values):
        for volume in range(values.shape[3]):
            check_cancel(cancelled)
            data = np.asarray(values[..., volume], dtype="f4")
            if not np.isfinite(data).all():
                raise ValueError("Nonfinite DWI; source was not repaired")
            checked += data.size
            if volume in selected:
                maximum = np.maximum(maximum, data)
                zero += (data <= 0).astype("u2")
                negative += int((data[domain] < 0).sum())
            progress(f"Inspecting CSD input volume {volume + 1}/{values.shape[3]}")
    if not domain.any():
        raise ValueError("CSD mask is empty")
    return dict(
        values_checked=checked,
        fitting_voxels=int(domain.sum()),
        no_positive_signal_voxels=int((domain & (maximum <= 0)).sum()),
        any_nonpositive_signal_voxels=int((domain & (zero > 0)).sum()),
        negative_selected_observations=negative,
    )


def prepare_csd(
    artifacts,
    tasks,
    output_directory,
    *,
    nthreads=4,
    tool_directory=None,
    cancelled=lambda: False,
    progress=lambda _: None,
):
    """Compile typed CSD/response/normalization tasks; preview performs no fitting."""
    if not tasks or any(t.algorithm not in ALGORITHMS for t in tasks):
        raise ValueError(
            "Only implemented CSD/response/normalization tasks may execute; tracking/connectomics remain planned"
        )
    if isinstance(nthreads, bool) or not isinstance(nthreads, int) or not 1 <= nthreads <= 64:
        raise ValueError("Threads must be an integer in 1..64")
    used = {v for t in tasks for v in t.inputs.values() if isinstance(v, str)}
    artifacts = _fresh(tuple(a for a in artifacts if a.id in used), cancelled, progress)
    proposal = build_workflow(
        artifacts, tuple(tasks), verify_sources=False, cancelled=cancelled, progress=progress
    ).to_dict()
    errors = [i["message"] for i in proposal["issues"] if i["severity"] == "error"]
    if errors:
        raise ValueError("; ".join(errors))
    folder = Path(output_directory).expanduser().absolute()
    if folder.exists() or folder.is_symlink() or not folder.parent.is_dir():
        raise ValueError("Choose a NEW CSD attempt directory with an existing parent")
    folder = folder.resolve()
    sources = {s["path"]: s["sha256"] for a in artifacts for s in a.to_dict()["sources"]}
    if any(folder.is_relative_to(Path(p).resolve().parent) for p in sources):
        raise ValueError("Output must be outside original input directories")
    require_local_processing([folder, *sources])
    tools, scripts = _tools(tasks, tool_directory, cancelled)
    environment = _environment_contract(cancelled)
    # MRtrix's Python scripts test variable *presence*, unlike its binaries.
    # An empty inherited QUIET/OVERWRITE value must not suppress logs or grant
    # overwrite permission in response estimation. Scope removals to this run.
    environment["unset"] = ["MRTRIX_QUIET", "MRTRIX_OVERWRITE"]
    binpath = str(Path(tools["mrconvert"]["path"]).parent)
    environment["overrides"].update(
        PATH=os.pathsep.join([binpath, *os.environ.get("PATH", "").split(os.pathsep)]),
        PYTHONPATH="",
    )
    inventory = {a.id: a.to_dict() for a in artifacts}
    rows = {r["id"]: r for r in proposal["tasks"]}
    products, commands, generated, outputs, bindings, exports = {}, [], {}, [], [], []
    layout_copies = []

    def command(step, exe, args, inputs, names, kind, geometry=None, components=None):
        argv = [
            tools[exe]["path"],
            *map(str, args),
            "-nthreads",
            str(nthreads),
            "-config",
            "BValueScaling",
            "false",
        ]
        if exe == "dwi2response":
            argv = [str(Path(sys.executable).resolve()), *argv]
        commands.append(
            dict(
                task=task.id,
                step=step,
                argv=argv,
                display=shlex.join(argv),
                inputs=list(map(str, inputs)),
                outputs=names,
                kind=kind,
                geometry=geometry,
                components=components,
            )
        )
        outputs.extend(names)

    for task in _order(tuple(tasks)):
        check_cancel(cancelled)
        spec = task.algorithm
        p = dict(task.parameters)
        bound = {
            role: products[(ref.task_id, ref.output)]
            if isinstance(ref, OutputRef)
            else inventory[ref]
            for role, ref in task.inputs.items()
        }
        mask = bound["mask"]
        base = bound.get("dwi", bound.get("wm_fod"))
        geo = base["geometry"]
        identity = {
            k: v
            for k, v in base.get("declarations", {}).items()
            if k in {"space_id", "subject_id", "session_id"}
        }
        row = dict(
            task=task.id,
            algorithm=spec,
            source=base,
            mask=mask,
            parameters=p,
            outputs={},
            input_bindings=bound,
        )
        if "dwi" in bound:
            sampling = rows[task.id]["sampling"]
            indices = sampling["selected_original_volume_indices"]
            groups = sampling["selected_shells"]
            weighted = [g for g in groups if not g["is_b0"]]
            single = spec in {"dwi2fod.csd", "dwi2response.tax", "dwi2response.tournier"}
            if single and any(g["is_b0"] for g in groups):
                raise ValueError(
                    "Single-shell CSD/Tournier/Tax uses one weighted shell only; explicitly omit b0 from this task's shell selection"
                )
            if spec == "dwi2response.dhollander" and not any(g["is_b0"] for g in groups):
                raise ValueError("Dhollander requires explicit b0 plus weighted measurements")
            scheme = _world_scheme(base)
            lmax = p["lmax"]
            angular = []
            if spec.startswith("dwi2response"):
                # Upstream WM selection has an internal fixed-order CSD before
                # final response estimation. A low requested response order
                # must not conceal an insufficient internal angular design.
                internal_order = 6 if spec.endswith("dhollander") else 4
                all_weighted = [i for i in indices if base["gradients"]["bvals"][i] > 50]
                internal_design = sh_design(scheme[all_weighted, :3], internal_order)
                internal_rank = int(np.linalg.matrix_rank(internal_design))
                if internal_rank < internal_design.shape[1]:
                    raise ValueError(
                        f"Response voxel-selection internal lmax {internal_order} needs angular rank {internal_design.shape[1]}; actual {internal_rank}"
                    )
                row["response_selection_design"] = dict(
                    lmax=internal_order, rank=internal_rank, coefficients=internal_design.shape[1]
                )
            for group in weighted:
                gindices = [
                    i
                    for i in indices
                    if abs(base["gradients"]["bvals"][i] - group["b_median"]) <= 100
                    and base["gradients"]["bvals"][i] > 50
                ]
                design = sh_design(scheme[gindices, :3], lmax)
                rank = int(np.linalg.matrix_rank(design))
                if rank != design.shape[1]:
                    raise ValueError(
                        f"Shell {group['b_median']}: angular rank {rank} below {design.shape[1]} SH coefficients; choose supported lmax"
                    )
                angular.append(
                    dict(
                        b_median=group["b_median"],
                        directions=len(gindices),
                        rank=rank,
                        coefficients=design.shape[1],
                        condition=float(np.linalg.cond(design)),
                    )
                )
            row.update(
                sampling=sampling,
                angular_design=angular,
                signal_inspection=_signal(base, mask, indices, cancelled, progress),
            )
            table = task.id + "-selected.grad"
            generated[table] = _text_table(scheme[indices])
            selected = base["path"]
            if indices != list(range(geo["shape"][3])):
                selected = str(folder / (task.id + "-selected.mif"))
                args = [
                    base["path"],
                    selected,
                    "-coord",
                    "3",
                    ",".join(map(str, indices)),
                    "-datatype",
                    "float32",
                    "-bvalue_scaling",
                    "no",
                ]
                if base["gradients"]["convention"] == "fsl_image_axes":
                    args += ["-fslgrad", base["request"]["bvec"], base["request"]["bval"]]
                command(
                    task.id + "-select",
                    "mrconvert",
                    args,
                    [base["path"]],
                    [Path(selected).name],
                    "selection",
                    geo,
                    len(indices),
                )
            row.update(selected=selected, gradient_table=table)
            common = ["-mask", mask["path"], "-grad", folder / table]
            if spec.startswith("dwi2response"):
                if selected == base["path"] and str(selected).endswith(".mif"):
                    from .._mif_checks import produced_mif

                    with produced_mif(Path(selected)) as (_, _, input_values):
                        volume_major = abs(input_values.strides[3]) >= max(
                            abs(s) for s in input_values.strides[:3]
                        )
                        prepare_layout = (
                            volume_major and input_values.nbytes > RESPONSE_LAYOUT_THRESHOLD
                        )
                    if prepare_layout:
                        name = task.id + "-response-layout.mif"
                        layout_copies.append(dict(source=str(selected), destination=name))
                        outputs.append(name)
                        selected = str(folder / name)
                        row["selected"] = selected
                method = spec.split(".")[1]
                tissues = ["wm", "gm", "csf"] if method == "dhollander" else ["wm"]
                names = [task.id + "-" + t + "-response.txt" for t in tissues]
                voxels = task.id + "-response-voxels.mif"
                response_lmax = (
                    ",".join(
                        "0" if g["is_b0"] else str(lmax)
                        for g in sorted(groups, key=lambda g: g["b_median"])
                    )
                    if method == "dhollander"
                    else str(lmax)
                )
                args = [
                    method,
                    selected,
                    *[folder / n for n in names],
                    *common,
                    "-lmax",
                    response_lmax,
                    "-voxels",
                    folder / voxels,
                    "-scratch",
                    folder,
                    "-info",
                ]
                defaults = (
                    {
                        "erode": 3,
                        "fa_threshold": 0.2,
                        "wm_percent": 0.5,
                        "gm_percent": 2.0,
                        "csf_percent": 10.0,
                    }
                    if method == "dhollander"
                    else {"max_iters": 10, "number": 300, "iter_voxels": 0, "dilate": 1}
                    if method == "tournier"
                    else {"max_iters": 20, "peak_ratio": 0.1, "convergence": 0.5}
                )
                for key in ("erode", "max_iters", "number", "peak_ratio", "convergence"):
                    if key in p:
                        args += ["-" + key, str(p[key])]
                        defaults[key] = p[key]
                row["algorithm_defaults"] = defaults
                command(
                    task.id + "-response",
                    "dwi2response",
                    args,
                    [selected, mask["path"], folder / table],
                    names + [voxels],
                    "response",
                    geo,
                )
                for tissue, name in zip(tissues, names, strict=True):
                    metadata = dict(
                        tissue=tissue,
                        shells=sorted(g["b_median"] for g in groups),
                        lmax=lmax if tissue == "wm" else 0,
                    )
                    products[(task.id, tissue + "_response")] = dict(
                        kind="response",
                        path=str(folder / name),
                        metadata=metadata,
                        declarations={**identity, "tissue": tissue},
                        geometry=None,
                        expected=True,
                    )
                    row["outputs"][tissue + "_response"] = name
                row["voxels"] = voxels
            else:
                tissues = ["wm"] if spec == "dwi2fod.csd" else ["wm", "gm", "csf"]
                names = []
                response_mapping = {}
                for tissue in tissues:
                    response = bound[tissue + "_response"]
                    metadata = response["metadata"]
                    if response.get("expected"):
                        expected = sorted(g["b_median"] for g in groups)
                        if len(metadata["shells"]) != len(expected) or any(
                            abs(a - b) > 100
                            for a, b in zip(metadata["shells"], expected, strict=False)
                        ):
                            raise ValueError(
                                "Estimated response sampling differs from reconstruction sampling"
                            )
                        if tissue == "wm" and metadata["lmax"] < lmax:
                            raise ValueError("Estimated response lmax below reconstruction lmax")
                        path = response["path"]
                        selected_rows = None
                    else:
                        selected_rows = response_rows(
                            metadata, groups, tissue, lmax if tissue == "wm" else 0, single=single
                        )
                        path = response["path"]
                        if selected_rows != list(range(metadata["rows"])):
                            name = task.id + "-" + tissue + "-selected-response.txt"
                            generated[name] = (
                                "# Shells: "
                                + ",".join(str(metadata["shells"][i]) for i in selected_rows)
                                + "\n"
                                + _text_table(np.asarray(metadata["coefficients"])[selected_rows])
                            )
                            path = str(folder / name)
                    name = task.id + ("-wm-fod.mif" if tissue == "wm" else "-" + tissue + ".mif")
                    names.append(name)
                    response_mapping[tissue] = dict(
                        original=response["path"],
                        used=path,
                        original_row_indices=selected_rows,
                        tissue=tissue,
                    )
                    row["outputs"]["wm_fod" if tissue == "wm" else tissue] = name
                    products[(task.id, "wm_fod" if tissue == "wm" else tissue)] = dict(
                        kind="fod" if tissue == "wm" else "tissue_scalar",
                        path=str(folder / name),
                        geometry=geo,
                        declarations={
                            **identity,
                            "tissue": tissue,
                            "normalization": "not_done",
                            "sh_basis": "mrtrix3_real_symmetric",
                            "sh_frame": "world_ras",
                        },
                        metadata=dict(lmax=lmax if tissue == "wm" else 0),
                        expected=True,
                    )
                args = [spec.split(".")[1], selected]
                for tissue, name in zip(tissues, names, strict=True):
                    args += [response_mapping[tissue]["used"], folder / name]
                args += common + ["-lmax", str(lmax) if single else f"{lmax},0,0"]
                defaults = (
                    {
                        "niter": 50,
                        "neg_lambda": 1.0,
                        "norm_lambda": 1.0,
                        "threshold": 0.0,
                        "filter": [1, 1, 1, 0, 0],
                    }
                    if single
                    else {"neg_lambda": 1e-10, "norm_lambda": 1e-10}
                )
                for key in ("niter", "neg_lambda", "norm_lambda", "threshold"):
                    if key in p:
                        args += ["-" + key, str(p[key])]
                        defaults[key] = p[key]
                if not single and p.get("predict_signal", "off") == "on":
                    predicted = task.id + "-predicted.mif"
                    args += ["-predicted_signal", folder / predicted]
                    names.append(predicted)
                    row["predicted"] = predicted
                row.update(response_mapping=response_mapping, algorithm_defaults=defaults)
                command(
                    task.id + "-fod",
                    "dwi2fod",
                    args,
                    [
                        selected,
                        mask["path"],
                        folder / table,
                        *[r["used"] for r in response_mapping.values()],
                    ],
                    names,
                    "fod",
                    geo,
                )
        else:
            for role in ("wm_fod", "gm", "csf"):
                product = bound[role]
                decl = product.get("declarations", {})
                history = (
                    product.get("metadata", {})
                    .get("embedded_header", {})
                    .get("command_history", [])
                )
                if decl.get("normalization") != "not_done" or any(
                    "mtnormalise" in h for h in history
                ):
                    raise ValueError(
                        "Normalization history must be explicitly not_done with no conflicting mtnormalise history; repeated/unknown normalization is blocked"
                    )
            names = []
            args = []
            for role in ("wm_fod", "gm", "csf"):
                name = task.id + "-" + role + "-normalized.mif"
                args += [bound[role]["path"], folder / name]
                names.append(name)
                row["outputs"][role] = name
                products[(task.id, role)] = {
                    **bound[role],
                    "path": str(folder / name),
                    "declarations": {**bound[role]["declarations"], "normalization": "completed"},
                    "expected": True,
                }
            checks = [
                task.id + "-normalization-field.mif",
                task.id + "-normalization-inliers.mif",
                task.id + "-normalization-factors.txt",
            ]
            args += [
                "-mask",
                mask["path"],
                "-order",
                p["order"],
                "-check_norm",
                folder / checks[0],
                "-check_mask",
                folder / checks[1],
                "-check_factors",
                folder / checks[2],
            ]
            if p["balanced"] == "on":
                args += ["-balanced"]
            if "iterations" in p:
                args += ["-niter", p["iterations"]]
            row["algorithm_defaults"] = dict(
                order=p["order"],
                outer_iterations=p.get("iterations", 15),
                inner_iterations=7,
                reference=0.28209479177387814,
                balanced=p["balanced"],
            )
            row["normalization_checks"] = checks
            command(
                task.id + "-normalise",
                "mtnormalise",
                args,
                [bound[k]["path"] for k in ("wm_fod", "gm", "csf", "mask")],
                names + checks,
                "normalization",
                geo,
            )
        bindings.append(row)
        for role, name in row["outputs"].items():
            if name.endswith(".mif") and role in {"wm_fod", "gm", "csf"}:
                # A C00 proxy is explicitly a scalar coefficient, never a FOD
                # glyph or a mislabeled scalar representation of the full FOD.
                target = name.removesuffix(".mif") + (
                    "-c00.nii.gz" if role == "wm_fod" else ".nii.gz"
                )
                exports.append(
                    dict(
                        source=name,
                        destination=target,
                        kind="fod_c00" if role == "wm_fod" else "tissue_scalar",
                        tissue="wm" if role == "wm_fod" else role,
                        geometry=geo,
                    )
                )
                outputs.append(target)
    outputs += ["qc-metrics.json", "qc-report.html", "qc-catalog.json", "scientific-selection.json"]
    estimate = sum(
        int(np.prod(c["geometry"]["shape"][:3])) * 4 * c["components"]
        for c in commands
        if c["kind"] == "selection"
    )
    for row in bindings:
        voxels = int(np.prod(row["source"]["geometry"]["shape"][:3]))
        if row["algorithm"].startswith("dwi2response"):
            estimate += voxels * 4 * 3
        else:
            lmax = row["parameters"].get("lmax", row["source"]["metadata"].get("lmax"))
            components = (lmax + 1) * (lmax + 2) // 2
            components += 2 if len(row["outputs"]) == 3 else 0
            components += (
                len(row["sampling"]["selected_original_volume_indices"])
                if row.get("predicted")
                else 0
            )
            components += 2 if row.get("normalization_checks") else 0
            components += len(row["outputs"])  # scalar exports
            estimate += voxels * 4 * components
    if any(c["kind"] == "response" for c in commands):
        estimate += (
            max(
                (
                    int(np.prod(a.payload["geometry"]["shape"])) * 4
                    for a in artifacts
                    if a.kind == "dwi"
                ),
                default=0,
            )
            * 3
        )
    estimate += sum(Path(item["source"]).stat().st_size for item in layout_copies)
    if layout_copies:
        outputs.append("layout-preparation.json")
    if shutil.disk_usage(folder.parent).free < estimate + 2 * 1024**3:
        raise ValueError(
            "Insufficient free storage for bounded CSD outputs/scratch plus 2 GiB margin"
        )
    return ExecutionPlan(
        dict(
            kind="mrtrix_csd_execution_plan",
            schema_version=1,
            artifacts=[a.to_dict() for a in artifacts],
            tasks=[t.to_dict() for t in tasks],
            parameters=dict(nthreads=nthreads, estimated_working_bytes=estimate),
            tools=tools,
            script_hashes=scripts,
            script_python=str(Path(sys.executable).resolve()) if scripts else None,
            environment=environment,
            output_directory=str(folder),
            commands=commands,
            generated_text=generated,
            layout_copies=layout_copies,
            internal_actions=[
                "Before response estimation: verified bounded lossless MIF storage-layout copy "
                + item["source"]
                + " → "
                + item["destination"]
                + "; values, gradients and geometry unchanged"
                for item in layout_copies
            ],
            bindings=bindings,
            exports=exports,
            outputs=list(dict.fromkeys(outputs)),
            extra_input_hashes=sources,
            scientific_limits=LIMITS,
            issues=proposal["issues"],
            compiler="tractosphere.csd/1",
            build=_revision(),
            implementation_hashes={
                str(p.relative_to(Path(__file__).parents[2])): hash_file(p, cancelled)
                for p in [
                    Path(__file__),
                    Path(__file__).with_name("responses.py"),
                    Path(__file__).with_name("mif_layout.py"),
                    Path(__file__).with_name("csd_qc.py"),
                    Path(__file__).with_name("registry.py"),
                    Path(__file__).with_name("planning.py"),
                    Path(__file__).with_name("imports.py"),
                    Path(__file__).with_name("dti.py"),
                    Path(__file__).with_name("contracts.py"),
                    Path(__file__).with_name("records.py"),
                    Path(__file__).parents[1] / "_mif_checks.py",
                    Path(__file__).parents[1] / "gradients.py",
                    Path(__file__).parents[1] / "execution.py",
                    Path(__file__).parents[1] / "process.py",
                ]
            },
        )
    )


class CSDStep(MRtrixStep):
    """Validated argv; existing process runner owns logs, resources and cancellation."""

    def validate(self):
        """No input can disappear or derivative overwrite an existing file."""
        if any(not p.is_file() for p in self.inputs):
            raise ValueError("Missing CSD step input")
        if any(p.exists() or p.is_symlink() for p in self.outputs):
            raise ValueError("CSD output exists; overwriting is refused")


class CSDAdapter:
    """Adapter callbacks only; shared core.execution remains the sole executor."""

    def compile(self, task, inputs, output_directory):
        """Compile an independently reusable GUI-free task."""
        return prepare_csd(tuple(inputs.values()), (task,), output_directory)

    def validate(self, task, inputs):
        """Return shared typed-workflow compatibility findings."""
        return tuple(build_workflow(tuple(inputs.values()), (task,)).to_dict()["issues"])

    def qc(self, manifest):
        """Read recorded QC without refitting or assigning scientific acceptance."""
        return manifest["validation"]

    def run(self, plan, *, approval, cancelled=lambda: False, progress=lambda _: None):
        """Recompile approval and delegate every process to the shared executor."""
        from ..execution import _write_manifest
        from .csd_qc import failure_report, verify_csd, verify_step
        from .records import save_selection

        data = plan.to_dict()
        folder = Path(data["output_directory"])
        artifacts = tuple(Artifact(a) for a in data["artifacts"])
        tasks = tuple(Task.from_dict(t) for t in data["tasks"])

        def recompile():
            return prepare_csd(
                artifacts,
                tasks,
                folder,
                nthreads=data["parameters"]["nthreads"],
                tool_directory=Path(data["tools"]["mrconvert"]["path"]).parent,
                cancelled=cancelled,
                progress=progress,
            )

        def setup(target):
            for name, text in data["generated_text"].items():
                with (target / name).open("x", encoding="utf-8") as stream:
                    stream.write(text)
            save_selection(target / "scientific-selection.json", artifacts, tasks)
            if data.get("layout_copies"):
                from ..execution import _publish_json
                from .mif_layout import prepare_response_layout

                records = [
                    prepare_response_layout(
                        Path(item["source"]),
                        target / item["destination"],
                        cancelled=cancelled,
                        progress=progress,
                    )
                    for item in data["layout_copies"]
                ]
                _publish_json(
                    target / "layout-preparation.json",
                    dict(copies=records, scientific_data_changed=False),
                )

        def step(command):
            return CSDStep(
                command["step"],
                tuple(command["argv"]),
                tuple(map(Path, command["inputs"])),
                tuple(folder / n for n in command["outputs"]),
                data["parameters"],
            )

        def sources():
            for a in artifacts:
                a.assert_current()
            for p, digest in data["script_hashes"].items():
                if hash_file(Path(p)) != digest:
                    raise ValueError("Response script/runtime changed during execution")
            for item in data["tools"].values():
                if hash_file(Path(item["path"])) != item["sha256"]:
                    raise ValueError("Scientific executable changed during execution")

        result = execute_artifact_task(
            plan,
            approval=approval,
            recompile=recompile,
            setup=setup,
            step_factory=step,
            verify_step=lambda c: verify_step(folder, data, c, cancelled),
            verify=lambda target: verify_csd(target, data, cancelled, progress),
            verify_sources=sources,
            cancelled=cancelled,
            progress=progress,
        )
        if result["status"] in {"failed", "cancelled"}:
            try:
                result["failure_evidence"] = failure_report(folder, result)
            except Exception as exc:
                result["failure_report_error"] = str(exc)
            _write_manifest(folder, result)
        return result
