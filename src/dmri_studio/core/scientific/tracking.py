"""Version-bound MRtrix tractography, filtering and density adapters.

Call prepare_tracking(imported_artifacts, typed_tasks, NEW_directory), review
its commands, then execute_plan(None, plan, approval=plan.digest). This module
compiles and verifies only; the existing artifact engine runs every command.
No solver, upstream reconstruction, implicit registration or source mutation.
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import shlex
import shutil
from pathlib import Path
from types import SimpleNamespace

import nibabel as nib
import numpy as np

from ..atlas import intersects_mask
from ..execution import (
    ExecutionPlan,
    MRtrixStep,
    _environment_contract,
    _publish_json,
    detect_mrtrix,
    execute_artifact_task,
    hash_file,
)
from ..process import check_cancel
from ..storage import require_local_processing
from ..tract_index import IndexedStreamlines
from .contracts import Artifact
from .csd import _help
from .dti import _aligned, _fresh, _revision, _text_table, _world_scheme, image_data
from .planning import OutputRef, Task, _order, build_workflow
from .records import save_selection

METHODS = {
    "tckgen.ifod2": "iFOD2",
    "tckgen.sdstream": "SD_Stream",
    "tckgen.ifod1": "iFOD1",
    "tckgen.tensor_det": "Tensor_Det",
    "tckgen.tensor_prob": "Tensor_Prob",
    "tckgen.act_ifod2": "iFOD2",
    "tckgen.act_ifod1": "iFOD1",
    "tckgen.act_sdstream": "SD_Stream",
}
ALGORITHMS = frozenset((*METHODS, "tckedit", "tckmap"))
LIMITS = [
    "Execution, numerical verification, automated QC, expert review and scientific acceptance are separate states.",
    "Streamlines are model-dependent trajectories, not individual axons. Counts and density are not axon counts or anatomical connection strength.",
    "ACT is applied only by explicitly selected ACT variants with verified five-tissue inputs. No SIFT2, registration, gradient repair or implicit resampling is performed.",
    "Same physical grid and declared subject/space are required; declarations do not independently authenticate anatomical correspondence.",
    "Probabilistic algorithms and random seed placement do not guarantee identical paths across runs; no unsupported random-seed flag is supplied.",
    "FOD amplitude cutoffs depend on response and normalization. Prior negative sampled FOD amplitudes and reconstruction warnings remain scientific review findings.",
    "tckedit -mask cropping is deliberately not exposed as whole-streamline filtering. Inclusion/exclusion and endpoint-only tests are explicitly distinct.",
    "Preview counts are for inspection, not a quantitative whole-brain research protocol. Seed caps can yield fewer accepted streamlines than requested.",
]


def _association(bound):
    """Reject conflicting or unknown associations, never infer registration."""
    for key in ("space_id", "subject_id"):
        values = {b.get("declarations", {}).get(key) for b in bound.values()}
        if len(values) != 1 or None in values or "" in values:
            raise ValueError(
                f"Tracking needs one explicitly reviewed {key} for every bound image/tract/ROI; unknown or conflicting association is blocked"
            )
    sessions = {b.get("declarations", {}).get("session_id") for b in bound.values()} - {None, ""}
    if len(sessions) > 1:
        raise ValueError("Conflicting acquisition/session identities")


def _constraints(bound):
    """Stable numerical slot order is the documented ordered-waypoint order."""
    return {
        role: [
            bound[key]
            for key in sorted(
                (key for key in bound if re.fullmatch(role + r"_\d+", key)),
                key=lambda key: int(key.rsplit("_", 1)[1]),
            )
        ]
        for role in ("include", "exclude", "ordered")
    }


def _validate_parameters(task, bound):
    """Algorithm-specific rejection rather than silently ignoring controls."""
    p = dict(task.parameters)
    if p.get("max_length_mm", 1) < p.get("min_length_mm", 0):
        raise ValueError("Maximum streamline length is below minimum")
    if task.algorithm in METHODS:
        tensor = task.algorithm.startswith("tckgen.tensor")
        method = p["seed_method"]
        if method == "dynamic" and tensor:
            raise ValueError("Dynamic FOD seeding requires FOD-based tracking")
        expected = (
            {"seed_mask"}
            if method in ("image", "random_per_voxel", "grid_per_voxel")
            else {"seed_roi"}
            if method == "roi"
            else set()
        )
        actual = set(bound) & {"seed_mask", "seed_roi"}
        if method == "gmwmi":
            expected = {"gmwmi"}
        actual = set(bound) & {"seed_mask", "seed_roi", "gmwmi"}
        if actual != expected:
            raise ValueError(
                "Choose exactly the seed image/ROI required by the selected seeding method; dynamic/sphere cannot combine with image seeding"
            )
        if ("per_voxel" in p) != (method in ("random_per_voxel", "grid_per_voxel")):
            raise ValueError("per_voxel is required only for random/grid-per-voxel seeding")
        if ("seed_sphere" in p) != (method == "sphere"):
            raise ValueError("Sphere coordinates are required only for spherical seeding")
        if method == "sphere":
            sphere = np.asarray(p["seed_sphere"], float)
            if sphere.shape != (4,) or sphere[3] <= 0:
                raise ValueError("Seed sphere must be world RAS x,y,z,positive radius in mm")
            geo = bound["dwi" if tensor else "fod"]["geometry"]
            center = nib.affines.apply_affine(np.linalg.inv(geo["affine_mm"]), sphere[:3])
            if np.any(center < -0.5) or np.any(center > np.array(geo["shape"][:3]) - 0.5):
                raise ValueError("Seed sphere centre lies outside the source physical grid")
        if "seed_direction" in p:
            d = np.asarray(p["seed_direction"])
            if d.shape != (3,) or not np.isclose(np.linalg.norm(d), 1, atol=1e-6):
                raise ValueError(
                    "Seed direction must be an explicit unit world-RAS vector; no normalization was applied"
                )
        if p.get("stop_after_includes") == "on" and not any(
            k.startswith(("include_", "ordered_")) for k in bound
        ):
            raise ValueError("Stop after inclusions requires an inclusion/ordered waypoint")
        if p["seeds"] < p["select"]:
            raise ValueError("Seed cap cannot be less than requested accepted streamlines")
        if not tensor:
            normalization = bound["fod"]["declarations"].get("normalization")
            if normalization not in ("not_done", "completed"):
                raise ValueError(
                    "Declare FOD normalization as not_done or completed after reviewing provenance; unknown amplitude calibration is blocked"
                )
        if task.algorithm.startswith("tckgen.act_"):
            if p["backtrack"] == "on" and task.algorithm != "tckgen.act_ifod2":
                raise ValueError(
                    "Backtracking is currently validated only with ACT iFOD2; choose off for this algorithm"
                )
            if p["backtrack"] == "on" and p.get("stop_after_includes") == "on":
                raise ValueError("MRtrix forbids stop-after-inclusions with ACT backtracking")
            if method == "gmwmi":
                expected_hash = next(
                    s["sha256"]
                    for s in bound["five_tt"]["sources"]
                    if s["path"] == bound["five_tt"]["path"]
                )
                if bound["gmwmi"]["declarations"].get("five_tt_sha256") != expected_hash:
                    raise ValueError(
                        "GMWMI seed image must identify the exact selected 5TT source hash"
                    )
        elif method == "gmwmi":
            raise ValueError("GM-WM interface seeding requires ACT")
    if task.algorithm == "tckedit" and p["ends_only"] == "on" and _constraints(bound)["ordered"]:
        raise ValueError(
            "Ordered waypoints and endpoint-only filtering are distinct; combine them in separately reviewed tasks"
        )


def prepare_tracking(
    artifacts,
    tasks,
    output_directory,
    *,
    nthreads=2,
    tool_directory=None,
    cancelled=lambda: False,
    progress=lambda _: None,
):
    """Compile a finite, immutable tracking graph using the existing import DAG."""
    if not tasks or any(t.algorithm not in ALGORITHMS for t in tasks):
        raise ValueError("Select only implemented Stage 4 tracking/filter/density tasks")
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
        raise ValueError("Choose a NEW attempt directory with an existing parent")
    folder = folder.resolve()
    sources = {s["path"]: s["sha256"] for a in artifacts for s in a.payload["sources"]}
    if any(folder.is_relative_to(Path(s).resolve().parent) for s in sources):
        raise ValueError("Output must be outside original scientific input directories")
    require_local_processing([folder, *sources])
    names = {"tckinfo", "tckstats", *(t.algorithm.split(".")[0] for t in tasks)}
    names.add("mrconvert")  # verified reference/density exports for existing viewer
    if any(t.algorithm.startswith("tckgen.act_") for t in tasks):
        names.add("5ttcheck")
    tools = detect_mrtrix(
        names=tuple(sorted(names)), tool_directory=tool_directory, cancelled=cancelled
    )
    if any(t["version"] != "3.0.5" for t in tools.values()):
        raise ValueError("Stage 4 adapter accepts only validated MRtrix3 3.0.5")
    helptext = {name: _help(t["path"]) for name, t in tools.items()}
    environment = _environment_contract(cancelled)
    environment["unset"] = ["MRTRIX_QUIET", "MRTRIX_OVERWRITE"]
    environment["overrides"]["PATH"] = (
        str(Path(next(iter(tools.values()))["path"]).parent)
        + os.pathsep
        + os.environ.get("PATH", "")
    )
    inventory = {a.id: a.to_dict() for a in artifacts}
    products = {}
    commands = []
    bindings = []
    generated = {}
    outputs = []
    proposals = {r["id"]: r for r in proposal["tasks"]}
    estimate = 0

    def command(task, step, exe, args, inputs, files, kind, geometry=None):
        argv = [tools[exe]["path"], *map(str, args), "-nthreads", str(nthreads)]
        for arg in argv[1:]:
            if (
                arg.startswith("-")
                and not re.fullmatch(r"-[0-9.].*", arg)
                and arg not in helptext[exe]
            ):
                raise ValueError(f"Installed {exe} does not advertise required option {arg}")
        commands.append(
            dict(
                task=task.id,
                step=step,
                argv=argv,
                display=shlex.join(argv),
                inputs=list(map(str, inputs)),
                outputs=files,
                kind=kind,
                geometry=geometry,
            )
        )
        outputs.extend(files)

    for task in _order(tuple(tasks)):
        check_cancel(cancelled)
        bound = {
            k: products[(v.task_id, v.output)] if isinstance(v, OutputRef) else inventory[v]
            for k, v in task.inputs.items()
        }
        if "original_atlas" in bound:
            from ..registration_review import require_registration

            eligibility = require_registration(
                bound["original_atlas"], bound["reference"], cancelled=cancelled
            )
        else:
            eligibility = None
        _association(bound)
        _validate_parameters(task, bound)
        if task.algorithm.startswith("tckgen.act_"):
            from .anatomy import accepted_tissue_review, alignment_review, tissue_statistics

            _, evidence = alignment_review(bound["five_tt"], bound["reference"], cancelled)
            sources.update(evidence)
            _, tissue_evidence = accepted_tissue_review(bound["five_tt"], cancelled)
            sources.update(tissue_evidence)
            tissue_statistics(bound["five_tt"]["path"], cancelled)
        p = dict(task.parameters)
        spec = task.algorithm
        base = bound.get("dwi", bound.get("fod", bound.get("reference")))
        geo = base["geometry"]
        constraints = _constraints(bound)
        # Integer atlas labels must first be explicitly selected/exported using
        # existing ROI tools. Do not treat a multi-label atlas as a binary ROI.
        for _role, b in bound.items():
            if b["kind"] == "roi" and b.get("verification", {}).get("label_ids") not in (
                None,
                [],
                [1],
            ):
                raise ValueError(
                    "Select/export one binary ROI derivative before tracking; multi-label masks are ambiguous"
                )
        row = dict(
            task=task.id,
            algorithm=spec,
            parameters=p,
            source=base,
            input_bindings=bound,
            registration_eligibility=eligibility,
            outputs={},
            constraints=constraints,
        )
        common = []
        for role, flag in (
            ("include", "-include"),
            ("exclude", "-exclude"),
            ("ordered", "-include_ordered"),
        ):
            for roi in constraints[role]:
                common += [flag, roi["path"]]
        if spec in METHODS:
            source = base["path"]
            tensor = "dwi" in bound
            if tensor:
                sampling = proposals[task.id]["sampling"]
                indices = sampling["selected_original_volume_indices"]
                selected = np.asarray(_world_scheme(base))[indices]
                weighted = selected[:, 3] > 50
                design = np.column_stack(
                    [
                        np.ones(len(selected)),
                        selected[:, 3, None]
                        * np.c_[
                            selected[:, 0] ** 2,
                            selected[:, 1] ** 2,
                            selected[:, 2] ** 2,
                            2 * selected[:, 0] * selected[:, 1],
                            2 * selected[:, 0] * selected[:, 2],
                            2 * selected[:, 1] * selected[:, 2],
                        ],
                    ]
                )
                if (
                    not (~weighted).any()
                    or np.linalg.matrix_rank(design) < 7
                    or len(
                        {
                            round(float(g["b_median"]))
                            for g in sampling["selected_shells"]
                            if not g["is_b0"]
                        }
                    )
                    != 1
                ):
                    raise ValueError(
                        "Tensor tracking requires explicit b0 plus one suitable weighted shell with tensor design rank 7"
                    )
                row["sampling"] = sampling
                table = task.id + "-selected.grad"
                generated[table] = _text_table(selected)
                if indices != list(range(geo["shape"][3])):
                    name = task.id + "-selected-dwi.mif"
                    source = str(folder / name)
                    command(
                        task,
                        task.id + "-select",
                        "mrconvert",
                        [
                            base["path"],
                            source,
                            "-coord",
                            "3",
                            ",".join(map(str, indices)),
                            "-datatype",
                            "float32",
                            "-bvalue_scaling",
                            "no",
                        ]
                        + (
                            ["-fslgrad", base["request"]["bvec"], base["request"]["bval"]]
                            if base["gradients"]["convention"] == "fsl_image_axes"
                            else []
                        ),
                        [base["path"]],
                        [name],
                        "selection",
                        geo,
                    )
                    estimate += int(np.prod(geo["shape"][:3])) * len(indices) * 4
                common += ["-grad", folder / table, "-config", "BValueScaling", "false"]
            if "mask" in bound:
                common += ["-mask", bound["mask"]["path"]]
            if spec.startswith("tckgen.act_"):
                five = bound["five_tt"]["path"]
                command(task, task.id + "-5ttcheck", "5ttcheck", [five], [five], [], "act_check")
                common += ["-act", five]
                for key in ("backtrack", "crop_at_gmwmi"):
                    if p[key] == "on":
                        common.append("-" + key)
            seed = p["seed_method"]
            if seed == "dynamic":
                common += ["-seed_dynamic", source]
            elif seed == "sphere":
                common += ["-seed_sphere", ",".join(map(str, p["seed_sphere"]))]
            elif seed == "gmwmi":
                common += ["-seed_gmwmi", bound["gmwmi"]["path"]]
            else:
                seed_image = bound["seed_roi" if seed == "roi" else "seed_mask"]["path"]
                common += [
                    "-seed_image" if seed in ("image", "roi") else "-seed_" + seed,
                    seed_image,
                ]
                if seed in ("random_per_voxel", "grid_per_voxel"):
                    common += [str(p["per_voxel"])]
            flags = {
                "select": "-select",
                "seeds": "-seeds",
                "step_mm": "-step",
                "angle_deg": "-angle",
                "cutoff": "-cutoff",
                "seed_cutoff": "-seed_cutoff",
                "min_length_mm": "-minlength",
                "max_length_mm": "-maxlength",
                "max_attempts_per_seed": "-max_attempts_per_seed",
                "samples": "-samples",
                "trials": "-trials",
                "power": "-power",
            }
            for key, flag in flags.items():
                if key in p:
                    common += [flag, p[key]]
            for key, flag in [
                ("rk4", "-rk4"),
                ("unidirectional", "-seed_unidirectional"),
                ("stop_after_includes", "-stop"),
            ]:
                if p.get(key) == "on":
                    common += [flag]
            if "seed_direction" in p:
                common += ["-seed_direction", ",".join(map(str, p["seed_direction"]))]
            name = task.id + "-tracks.tck"
            seeds = task.id + "-seeds.csv"
            command(
                task,
                task.id + "-track",
                "tckgen",
                [
                    source,
                    folder / name,
                    "-algorithm",
                    METHODS[spec],
                    *common,
                    "-output_seeds",
                    folder / seeds,
                    "-info",
                ],
                [
                    source,
                    *[
                        b["path"]
                        for b in bound.values()
                        if b["kind"] in ("mask", "roi", "5tt", "gmwmi")
                    ],
                    *([folder / table] if tensor else []),
                ],
                [name, seeds],
                "tracts",
                geo,
            )
            row.update(selected_source=source, seed_output=seeds)
            estimate += (
                int(p["select"]) * (math.ceil(p["max_length_mm"] / p["step_mm"]) * 4 + 4) * 12
            )
        elif spec == "tckedit":
            name = task.id + "-filtered.tck"
            source = bound["tracts"]["path"]
            if Path(source).suffix.lower() != ".tck":
                raise ValueError(
                    "MRtrix processing currently accepts TCK only; use the existing geometry-verified TRK export first"
                )
            args = [
                source,
                folder / name,
                *common,
                "-minlength",
                p["min_length_mm"],
                "-maxlength",
                p["max_length_mm"],
            ]
            for key, flag in [("number", "-number"), ("skip", "-skip")]:
                # Native -skip requires a positive integer. Explicit zero means
                # skip none, implemented by retaining MRtrix's native default.
                if key in p and not (key == "skip" and p[key] == 0):
                    args += [flag, p[key]]
            if p["ends_only"] == "on":
                args += ["-ends_only"]
            command(
                task,
                task.id + "-filter",
                "tckedit",
                args,
                [source, *[r["path"] for group in constraints.values() for r in group]],
                [name],
                "tracts",
                geo,
            )
            estimate += (
                Path(source).stat().st_size if not bound["tracts"].get("expected") else 64 * 1024**2
            )
        else:
            name = task.id + "-density.mif"
            source = bound["tracts"]["path"]
            args = [
                source,
                folder / name,
                "-template",
                base["path"],
                "-datatype",
                "float32",
                "-contrast",
                "tdi",
                "-stat_vox",
                "sum",
            ]
            if p["directional"] == "on":
                args += ["-dec"]
            command(
                task,
                task.id + "-density",
                "tckmap",
                args,
                [source, base["path"]],
                [name],
                "density",
                geo,
            )
            estimate += int(np.prod(geo["shape"][:3])) * 4 * (3 if p["directional"] == "on" else 1)
        role = "density" if spec == "tckmap" else "tracts"
        row["outputs"][role] = name
        if spec == "tckmap":
            exported = task.id + "-density.nii.gz"
            command(
                task,
                task.id + "-export",
                "mrconvert",
                [folder / name, folder / exported, "-datatype", "float32"],
                [folder / name],
                [exported],
                "density_export",
                geo,
            )
            row["outputs"]["density_nifti"] = exported
        product = dict(
            path=str(folder / name),
            kind="density" if role == "density" else "tractogram",
            geometry=geo if role == "density" else None,
            declarations=dict(base["declarations"]),
            metadata={},
            expected=True,
        )
        products[(task.id, role)] = product
        if role == "tracts":
            if "five_tt" in bound:
                endpoint_name = task.id + "-endpoints.nii.gz"
                row["outputs"]["endpoint_density"] = endpoint_name
                outputs.append(endpoint_name)
            background = bound.get("reference", bound.get("mask"))
            reference_name = task.id + "-reference.nii.gz"
            command(
                task,
                task.id + "-reference",
                "mrconvert",
                [background["path"], folder / reference_name, "-datatype", "float32"],
                [background["path"]],
                [reference_name],
                "reference_export",
                geo,
            )
            row["outputs"]["reference"] = reference_name
            row["background"] = background
            estimate += int(np.prod(geo["shape"][:3])) * 8
            command(
                task,
                task.id + "-info",
                "tckinfo",
                [folder / name, "-count"],
                [folder / name],
                [],
                "info",
            )
            command(
                task,
                task.id + "-stats",
                "tckstats",
                [folder / name, "-dump", folder / (task.id + "-lengths.txt")],
                [folder / name],
                [task.id + "-lengths.txt"],
                "lengths",
            )
        bindings.append(row)
    outputs += [
        *generated,
        "scientific-selection.json",
        "qc-metrics.json",
        "qc-report.html",
        "qc-catalog.json",
    ]
    if shutil.disk_usage(folder.parent).free < estimate + 5 * 1024**3:
        raise ValueError(
            "Insufficient storage for conservative tracking/selected-DWI estimate plus 5 GiB safety margin"
        )
    implementation = [
        Path(__file__),
        Path(__file__).with_name("tracking_registry.py"),
        Path(__file__).with_name("anatomy.py"),
        Path(__file__).with_name("act_qc.py"),
        Path(__file__).with_name("registry.py"),
        Path(__file__).with_name("planning.py"),
        Path(__file__).with_name("imports.py"),
        Path(__file__).with_name("dti.py"),
        Path(__file__).parents[1] / "execution.py",
        Path(__file__).parents[1] / "registration_review.py",
        Path(__file__).parents[1] / "process.py",
        Path(__file__).parents[1] / "tract_index.py",
        Path(__file__).parents[1] / "atlas.py",
    ]
    return ExecutionPlan(
        dict(
            kind="mrtrix_tracking_execution_plan",
            schema_version=1,
            artifacts=[a.to_dict() for a in artifacts],
            tasks=[t.to_dict() for t in tasks],
            output_directory=str(folder),
            tools=tools,
            environment=environment,
            commands=commands,
            bindings=bindings,
            outputs=list(dict.fromkeys(outputs)),
            generated_text=generated,
            extra_input_hashes=sources,
            parameters=dict(
                nthreads=nthreads,
                estimated_working_bytes=estimate,
                storage_margin_bytes=5 * 1024**3,
            ),
            scientific_limits=LIMITS,
            issues=proposal["issues"],
            build=_revision(),
            compiler="tractosphere.tracking/1",
            implementation_hashes={
                str(f.relative_to(Path(__file__).parents[2])): hash_file(f, cancelled)
                for f in implementation
            },
        )
    )


def _mask_samples(points, array, inverse):
    """Nearest physical voxel cells, with explicit outside-field handling."""
    vox = np.floor(nib.affines.apply_affine(inverse, points) + 0.5).astype(int)
    valid = ((vox >= 0) & (vox < np.array(array.shape))).all(axis=1)
    hits = np.zeros(len(vox), bool)
    hits[valid] = array[tuple(vox[valid].T)] > 0
    return hits


def tract_measurements(path, row, cancelled=lambda: False):
    """Reuse indexed native RAS TCK and exact volumetric ROI intersection."""
    tracts = IndexedStreamlines(path, cancelled=cancelled)
    try:
        if len(tracts) == 0:
            raise ValueError("Tractography output is empty; no validated result is published")
        bound = row["input_bindings"]
        roi_data = {}
        for group in row["constraints"].values():
            for b in group:
                with image_data(Path(b["path"])) as (affine, a):
                    roi_data[b["path"]] = (np.asarray(a) > 0, np.linalg.inv(affine))
        mask = None
        if "mask" in bound:
            with image_data(Path(bound["mask"]["path"])) as (affine, a):
                mask = (np.asarray(a) > 0, np.linalg.inv(affine))
        outside = 0
        endpoints_outside = 0
        violations = {"include": 0, "exclude": 0, "ordered": 0}
        vertex_violations = {"include": 0, "exclude": 0}
        vertices = 0
        endpoint_low = np.full(3, np.inf)
        endpoint_high = -endpoint_low
        for index in range(len(tracts)):
            check_cancel(cancelled)
            line = tracts[index]
            vertices += len(line)
            endpoint_low = np.minimum(endpoint_low, line[[0, -1]].min(axis=0))
            endpoint_high = np.maximum(endpoint_high, line[[0, -1]].max(axis=0))
            if mask:
                inside = _mask_samples(line, *mask)
                outside += int(not inside.all())
                endpoints_outside += int(not inside[[0, -1]].all())
            for role in ("include", "exclude"):
                for b in row["constraints"][role]:
                    data, inverse = roi_data[b["path"]]
                    if row["parameters"].get("ends_only") == "on":
                        hit = _mask_samples(line[[0, -1]], data, inverse).any()
                    else:
                        hit = intersects_mask(
                            line,
                            data,
                            SimpleNamespace(
                                voxels=lambda p, inv=inverse: nib.affines.apply_affine(inv, p)
                            ),
                        )
                    violations[role] += int((not hit) if role == "include" else hit)
                    vertices_to_test = (
                        line[[0, -1]] if row["parameters"].get("ends_only") == "on" else line
                    )
                    vertex_hit = _mask_samples(vertices_to_test, data, inverse).any()
                    vertex_violations[role] += int(
                        (not vertex_hit) if role == "include" else vertex_hit
                    )
            ordered = row["constraints"]["ordered"]
            if ordered:
                positions = []
                for b in ordered:
                    data, inverse = roi_data[b["path"]]
                    hit = np.flatnonzero(_mask_samples(line, data, inverse))
                    positions.append(int(hit[0]) if len(hit) else -1)
                # Native saved-vertex order check is descriptive; exact segment
                # intersections are independently checked for unordered ROIs.
                if -1 in positions or not (
                    np.all(np.diff(positions) >= 0)
                    or (row.get("algorithm") != "tckedit" and np.all(np.diff(positions) <= 0))
                ):
                    violations["ordered"] += 1
        lengths = np.array(tracts.lengths)
        return dict(
            streamlines=len(tracts),
            vertices=vertices,
            bounds_min=tracts.lower.tolist(),
            bounds_max=tracts.upper.tolist(),
            endpoint_bounds_min=endpoint_low.tolist(),
            endpoint_bounds_max=endpoint_high.tolist(),
            length_mm=dict(
                min=float(lengths.min()),
                max=float(lengths.max()),
                mean=float(lengths.mean()),
                median=float(np.median(lengths)),
                percentiles=np.percentile(lengths, [1, 5, 50, 95, 99]).tolist(),
            ),
            tracking_mask_assessed=mask is not None,
            mask_streamlines_with_any_outside_saved_vertex=outside if mask is not None else None,
            mask_streamlines_with_outside_endpoint=endpoints_outside if mask is not None else None,
            roi_violation_observations=violations,
            roi_saved_vertex_violation_observations=vertex_violations,
            roi_observation_semantics=(
                "Native tckedit 3.0.5 checks saved vertices in nearest ROI voxels. "
                "Segment-intersection observations can flag between-vertex crossings that native filtering does not exclude; "
                "ordered observations use first saved visits and are descriptive, not a replacement for native loop rules."
            ),
            finite_coordinates=True,
            coordinate_convention="scanner/world RAS+ millimetres; no display-axis flips",
            lengths=lengths,
        )
    finally:
        tracts.close()


def verify_step(folder, plan, command, cancelled):
    """Verify scientific products before the DAG can consume them."""
    row = next(r for r in plan["bindings"] if r["task"] == command["task"])
    if command["kind"] == "act_check":
        from .anatomy import tissue_statistics

        text = (folder / "logs" / (command["step"] + ".log")).read_text(errors="replace")
        if "[WARNING]" in text or "[ERROR]" in text:
            raise ValueError("Native 5ttcheck findings block ACT; inspect saved log")
        return tissue_statistics(row["input_bindings"]["five_tt"]["path"], cancelled)
    if command["kind"] == "reference_export":
        original = _aligned(Path(row["background"]["path"]), command["geometry"])
        exported = _aligned(folder / command["outputs"][0], command["geometry"])
        if not np.array_equal(original, exported):
            raise ValueError("Tracking reference export changed scientific values")
        return dict(
            exact_values_preserved=True,
            geometry_verified=True,
            role="anatomical reference"
            if "reference" in row["input_bindings"]
            else "tracking mask background, not structural anatomy",
        )
    if command["kind"] == "tracts":
        m = tract_measurements(folder / command["outputs"][0], row, cancelled)
        m.pop("lengths")
        return m
    if command["kind"] == "density":
        array = _aligned(folder / command["outputs"][0], command["geometry"])
        expected = 3 if row["parameters"]["directional"] == "on" else 1
        if (
            not np.isfinite(array).all()
            or (array < 0).any()
            or not array.any()
            or (expected == 3 and (array.ndim != 4 or array.shape[3] != 3))
            or (expected == 1 and array.ndim != 3)
        ):
            raise ValueError(
                "Invalid density dimensions, finite values or nonnegative contributions"
            )
        return dict(
            shape=list(array.shape),
            maximum=float(array.max()),
            minimum=float(array.min()),
            units="streamline contributions per template voxel; DEC weighted by absolute world direction"
            if expected == 3
            else "streamline contributions per template voxel",
            geometry_verified=True,
        )
    if command["kind"] == "density_export":
        original = folder / row["outputs"]["density"]
        exported = folder / command["outputs"][0]
        a = _aligned(original, command["geometry"])
        b = _aligned(exported, command["geometry"])
        if not np.array_equal(a, b):
            raise ValueError("Density NIfTI export changed scientific values")
        return dict(
            exact_values_preserved=True,
            geometry_verified=True,
            channel_meaning="LR, AP, SI" if a.ndim == 4 else "streamline contributions",
        )
    if command["kind"] == "selection":
        from .._mif_checks import produced_mif, voxel_mapping

        indices = row["sampling"]["selected_original_volume_indices"]
        with (
            image_data(Path(row["source"]["path"])) as (source_aff, source),
            produced_mif(folder / command["outputs"][0]) as (fields, aff, output),
        ):
            axes, flips = voxel_mapping(source_aff, source.shape, aff, output.shape)
            from .._mif_checks import rearrange

            if output.shape[3] != len(indices):
                raise ValueError("Selected DWI volume count changed")
            gradients = np.array(
                [[float(x) for x in r.split(",")] for r in fields.get("dw_scheme", [])]
            )
            expected = _world_scheme(row["source"])[indices]
            if gradients.shape != expected.shape or not np.allclose(
                gradients, expected, atol=1e-7, rtol=1e-7
            ):
                raise ValueError(
                    "Selected DWI embedded gradients differ from the reviewed original measurement mapping"
                )
            for j, i in enumerate(indices):
                check_cancel(cancelled)
                if not np.array_equal(
                    rearrange(np.asarray(source[..., i], dtype="f4"), axes, flips), output[..., j]
                ):
                    raise ValueError(
                        "Shell-selection changed source values or spatial correspondence"
                    )
        return dict(
            selected_volumes=len(indices), all_selected_values_verified=True, resampling=False
        )
    return dict(status="command_completed; cross-checks recorded in final QC")


def verify_tracking(folder, plan, cancelled=lambda: False, progress=lambda _: None):
    """Independent measurement/report layer; never scientific acceptance."""
    for name, text in plan["generated_text"].items():
        if (folder / name).read_text() != text:
            raise ValueError(
                "Reviewed tracking gradient derivative changed; no verified tractogram published"
            )
    findings = list(LIMITS)
    for binding in plan["bindings"]:
        review = binding.get("registration_eligibility")
        if review and review["usage"] == "DIAGNOSTIC":
            findings.append(
                "DIAGNOSTIC registration eligibility; human scientific acceptance remains unassigned. Evidence: "
                + review["diagnostic_evidence_identity"]
            )
    rows = []
    for row in plan["bindings"]:
        check_cancel(cancelled)
        if row["algorithm"] == "tckmap":
            c = next(
                c for c in plan["commands"] if c["task"] == row["task"] and c["kind"] == "density"
            )
            rows.append(dict(task=row["task"], density=verify_step(folder, plan, c, cancelled)))
            continue
        name = row["outputs"]["tracts"]
        m = tract_measurements(folder / name, row, cancelled)
        if "five_tt" in row["input_bindings"]:
            from .act_qc import endpoint_qc

            m["act"] = endpoint_qc(
                folder / name,
                row["input_bindings"]["five_tt"]["path"],
                folder / (row["task"] + "-endpoints.nii.gz"),
                cancelled=cancelled,
            )
            findings.append(
                f"{row['task']}: descriptive dominant endpoint tissues {m['act']['dominant_tissue_counts']}. Cropped GM-WM interface endpoints may have nearly tied fractions; these labels are not automatic anatomical acceptance."
            )
        lengths = m.pop("lengths")
        dumped = np.atleast_1d(np.loadtxt(folder / (row["task"] + "-lengths.txt")))
        if dumped.shape != lengths.shape:
            raise ValueError("MRtrix length count disagrees with full TCK indexing")
        m["mrtrix_vs_vertex_length_max_absolute_mm"] = float(np.max(np.abs(dumped - lengths)))
        m["mrtrix_length_summary_mm"] = {
            key: float(fn(dumped))
            for key, fn in [
                ("min", np.min),
                ("max", np.max),
                ("mean", np.mean),
                ("median", np.median),
            ]
        }
        info = (folder / "logs" / (row["task"] + "-info.log")).read_text(errors="replace")
        m["mrtrix_tckinfo"] = info
        explicit_count = re.search(r"actual count in file:\s*(\d+)", info)
        if not explicit_count or int(explicit_count.group(1)) != m["streamlines"]:
            raise ValueError("MRtrix tckinfo count disagrees with full-file indexing")
        if row["algorithm"] in METHODS:
            method = re.search(r"^\s*method:\s*(\S+)", info, re.MULTILINE)
            if (
                not method
                or method.group(1).replace("_", "").lower()
                != METHODS[row["algorithm"]].replace("_", "").lower()
            ):
                raise ValueError("TCK records a different tracking algorithm than approved")
            if "five_tt" in row["input_bindings"]:
                act = re.search(r"^\s*act:\s*(.+)$", info, re.MULTILINE)
                if not act or Path(act.group(1).strip()) != Path(
                    row["input_bindings"]["five_tt"]["path"]
                ):
                    raise ValueError("TCK does not record the approved ACT tissue image")
                for option in ("backtrack", "crop_at_gmwmi"):
                    match = re.search(r"^\s*" + option + r":\s*(\d+)", info, re.MULTILINE)
                    recorded = bool(match and int(match.group(1)))
                    if recorded != (row["parameters"][option] == "on"):
                        raise ValueError("TCK ACT option differs from approved " + option)
            p = row["parameters"]
            tol = 1e-4
            m["length_constraint_roundoff_tolerance_mm"] = tol
            m["below_minimum_length_count"] = int((lengths < p["min_length_mm"] - tol).sum())
            m["above_requested_maximum_length_count"] = int(
                (lengths > p["max_length_mm"] + tol).sum()
            )
            if m["below_minimum_length_count"] or m["above_requested_maximum_length_count"]:
                findings.append(
                    f"{row['task']}: actual vertex lengths include {m['below_minimum_length_count']} below minimum and {m['above_requested_maximum_length_count']} above requested maximum; preserve MRtrix output and review integration/stopping overshoot"
                )
        if m["mask_streamlines_with_any_outside_saved_vertex"]:
            findings.append(
                f"{row['task']}: {m['mask_streamlines_with_any_outside_saved_vertex']} streamlines have saved vertices outside nearest-neighbour mask cells; boundary interpolation/stopping requires review"
            )
        if any(m["roi_violation_observations"].values()):
            findings.append(
                f"{row['task']}: independent ROI observations require review: {m['roi_violation_observations']}"
            )
        if "select" in row["parameters"] and m["streamlines"] < row["parameters"]["select"]:
            findings.append(
                f"{row['task']}: requested {row['parameters']['select']}, accepted {m['streamlines']}; explicit seed/ROI limits reached"
            )
        if row.get("seed_output"):
            seed_text = (folder / row["seed_output"]).read_text()
            m["successful_seed_rows"] = len(
                [s for s in seed_text.splitlines() if s.strip() and not s.lstrip().startswith("#")]
            )
            m["seed_file"] = row["seed_output"]
        tracklog = (
            folder
            / "logs"
            / (row["task"] + ("-track.log" if row["algorithm"] in METHODS else "-filter.log"))
        )
        text = tracklog.read_text(errors="replace")
        m["native_tracking_diagnostics"] = native_tracking_diagnostics(text)
        matches = re.findall(r"(\d+)\s+seeds,\s+(\d+)\s+streamlines,\s+(\d+)\s+selected", text)
        m["attempts_summary"] = (
            dict(
                zip(
                    ("attempted_seeds", "generated_streamlines", "accepted_streamlines"),
                    map(int, matches[-1]),
                    strict=True,
                )
            )
            if matches
            else None
        )
        m["tracking_warnings"] = [
            s for s in text.splitlines() if "[WARNING]" in s or "[ERROR]" in s
        ]
        findings.extend(f"{row['task']}: {s}" for s in m["tracking_warnings"])
        rows.append(dict(task=row["task"], algorithm=row["algorithm"], **m))
    from .contracts import ImportRequest
    from .imports import import_artifact

    products = []
    for row in plan["bindings"]:
        for role, name in row["outputs"].items():
            if role == "density_nifti":
                continue
            kind = (
                "tractogram"
                if role == "tracts"
                else "anatomy"
                if role == "reference"
                else "density"
            )
            product = import_artifact(
                ImportRequest(
                    kind,
                    folder / name,
                    declarations=row["source"]["declarations"],
                    provenance="TractoSphere MRtrix derivative; execution and numerical verification completed, scientific review and acceptance remain pending.",
                ),
                cancelled=cancelled,
                progress=progress,
            )
            products.append(dict(task=row["task"], role=role, artifact=product.to_dict()))
    result = dict(
        execution="completed",
        numerical_verification="passed",
        automated_qc="completed_with_findings",
        scientific_review="required",
        scientific_acceptance="unassigned",
        findings=findings,
        measurements=rows,
        products=products,
    )
    _publish_json(folder / "qc-metrics.json", result)
    report = '<!doctype html><meta charset="utf-8"><title>TractoSphere tractography QC</title><style>body{font:15px/1.5 system-ui;max-width:1100px;margin:40px auto;padding:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere}table{border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #ccc}</style><h1>Tractography scientific evidence</h1><p>Execution completed · Numerical verification passed · QC requires review · Scientific acceptance unassigned</p>'
    for r in rows:
        report += "<h2>" + html.escape(r["task"]) + "</h2><table>"
        values = [
            ("Algorithm", r.get("algorithm", "Tract density")),
            ("Accepted streamlines", r.get("streamlines", "Not applicable")),
        ]
        values += [("Length " + k + " (mm)", v) for k, v in r.get("length_mm", {}).items()]
        values += [
            ("Reported seed attempts", r.get("attempts_summary")),
            (
                "Tracks with a saved vertex outside mask",
                r.get("mask_streamlines_with_any_outside_saved_vertex")
                if r.get("tracking_mask_assessed")
                else "Not assessed — no binary tracking mask bound",
            ),
            ("ROI compliance observations", r.get("roi_violation_observations")),
        ]
        report += "".join(
            "<tr><th>" + html.escape(str(k)) + "</th><td>" + html.escape(str(v)) + "</td></tr>"
            for k, v in values
        )
        report += (
            "</table><details><summary>Complete numerical and command metadata</summary><pre>"
            + html.escape(json.dumps(r, indent=2))
            + "</pre></details>"
        )
    report += (
        "<h2>Limitations and findings</h2><ul>"
        + "".join("<li>" + html.escape(f) + "</li>" for f in findings)
        + "</ul><h2>Commands</h2><pre>"
        + html.escape("\n\n".join(c["display"] for c in plan["commands"]))
        + "</pre>"
    )
    with (folder / "qc-report.html").open("x") as f:
        f.write(report)
    catalog = dict(
        schema_version=1,
        kind="dmri_qc_catalog",
        producer="TractoSphere MRtrix Stage 4",
        identity=dict(subject=plan["bindings"][0]["source"]["declarations"]["subject_id"]),
        stages=[
            dict(
                id="tractography",
                title="Tractography execution and independent checks",
                execution="completed",
                technical="passed",
                automated_qc="pending",
                scientific_review="required",
                findings=findings,
                artifacts=[
                    dict(
                        kind="report",
                        label="Scientific QC report",
                        path="qc-report.html",
                        sha256=hash_file(folder / "qc-report.html"),
                    ),
                    dict(
                        kind="metrics",
                        label="Numerical measurements",
                        path="qc-metrics.json",
                        sha256=hash_file(folder / "qc-metrics.json"),
                    ),
                ],
            )
        ],
    )
    _publish_json(folder / "qc-catalog.json", catalog)
    return result


def native_tracking_diagnostics(text):
    """Preserve native candidate/termination statistics, never invent rejections."""
    result = dict(
        termination_probabilities_percent={},
        rejection_counts={},
        scope="Native generated-candidate diagnostics, not a reclassification of only the saved selected streamlines",
    )
    section = None
    for line in text.splitlines():
        if "Termination reason probabilities:" in line:
            section = "termination_probabilities_percent"
        elif "Track rejection counts:" in line:
            section = "rejection_counts"
        elif section:
            match = re.search(r"\[INFO\]\s{2,}(.+?):\s*([0-9.]+)(%)?\s*$", line)
            if match and (bool(match.group(3)) == (section == "termination_probabilities_percent")):
                result[section][match.group(1)] = (
                    float(match.group(2)) if match.group(3) else int(match.group(2))
                )
    return result


class TrackingAdapter:
    """Shared artifact-executor callbacks, also usable without a GUI."""

    def compile(self, task, inputs, output_directory):
        return prepare_tracking(tuple(inputs.values()), (task,), output_directory)

    def validate(self, task, inputs):
        return tuple(build_workflow(tuple(inputs.values()), (task,)).to_dict()["issues"])

    def qc(self, manifest):
        return manifest["validation"]

    def run(self, plan, *, approval, cancelled=lambda: False, progress=lambda _: None):
        data = plan.to_dict()
        folder = Path(data["output_directory"])
        artifacts = tuple(Artifact(a) for a in data["artifacts"])
        tasks = tuple(Task.from_dict(t) for t in data["tasks"])

        def setup(target):
            save_selection(target / "scientific-selection.json", artifacts, tasks)
            for name, text in data["generated_text"].items():
                with (target / name).open("x") as f:
                    f.write(text)

        def sources():
            for a in artifacts:
                a.assert_current()
            for t in data["tools"].values():
                if hash_file(Path(t["path"])) != t["sha256"]:
                    raise ValueError("Tracking executable changed during execution")
            for p, expected in data["extra_input_hashes"].items():
                if hash_file(Path(p)) != expected:
                    raise ValueError("Tracking input/anatomical review changed during execution")

        return execute_artifact_task(
            plan,
            approval=approval,
            recompile=lambda: prepare_tracking(
                artifacts,
                tasks,
                folder,
                nthreads=data["parameters"]["nthreads"],
                tool_directory=Path(next(iter(data["tools"].values()))["path"]).parent,
                cancelled=cancelled,
                progress=progress,
            ),
            setup=setup,
            step_factory=lambda c: TrackingStep(
                c["step"],
                tuple(c["argv"]),
                tuple(map(Path, c["inputs"])),
                tuple(folder / n for n in c["outputs"]),
                {
                    **data["parameters"],
                    "generated_text": {
                        str(folder / n): text for n, text in data["generated_text"].items()
                    },
                },
            ),
            verify_step=lambda c: verify_step(folder, data, c, cancelled),
            verify=lambda _: verify_tracking(folder, data, cancelled, progress),
            verify_sources=sources,
            cancelled=cancelled,
            progress=progress,
        )


class TrackingStep(MRtrixStep):
    """No-overwrite step using existing process groups, cancellation and logs."""

    def validate(self):
        if any(not p.is_file() for p in self.inputs):
            raise ValueError("Missing tracking input")
        if any(p.exists() or p.is_symlink() for p in self.outputs):
            raise ValueError("Tracking derivative exists; overwriting is refused")
        for path, text in self.parameters.get("generated_text", {}).items():
            if Path(path) in self.inputs and Path(path).read_text() != text:
                raise ValueError("Reviewed gradient table changed before tracking")
