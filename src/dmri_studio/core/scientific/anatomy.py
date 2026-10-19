"""MRtrix five-tissue operations, never a second process executor.

prepare_anatomy(artifacts, (Task(...),), NEW_directory) compiles one independently
reusable operation. Review its commands, then execute_plan(..., approval=digest).
Validated outputs can be reimported into subsequent tasks, GUI or future batch
work. No implicit registration, resampling, tissue repair or scientific acceptance.
"""

from __future__ import annotations

import html
import json
import os
import shlex
import shutil
import sys
from pathlib import Path

import numpy as np

from .._mif_checks import voxel_mapping
from ..execution import (
    ExecutionPlan,
    _environment_contract,
    _publish_json,
    detect_mrtrix,
    execute_artifact_task,
    hash_file,
)
from ..process import check_cancel
from ..storage import require_local_processing
from .contracts import Artifact, ImportRequest
from .csd import _help
from .dti import _fresh, _revision, image_data
from .imports import import_artifact
from .planning import Task, build_workflow
from .records import read_json, save_selection
from .tracking import TrackingStep, _association

ALGORITHMS = frozenset(("5ttgen.fsl", "5ttcheck", "5tt2vis", "5tt2gmwmi"))
CHANNELS = ("cortical_gm", "subcortical_gm", "wm", "csf", "pathology")
CHANNEL_DECLARATION = ",".join(CHANNELS)
LIMITS = [
    "Execution, numerical verification, automated QC, anatomical review and scientific acceptance are separate states.",
    "5TT channels are continuous tissue fractions, not integer atlas labels; their declared channel order cannot be authenticated by values alone.",
    "No registration, affine rewrite, tissue clipping or fraction normalization is performed by these operations.",
    "The current conservative adapter requires an already reviewed common physical voxel grid. Native-space segmentation on a different grid needs a separately verified transform workflow.",
    "FSL FAST/FIRST segmentation and prior anatomical resampling can affect ACT. Segmentation requires visual scientific review even after 5ttcheck passes.",
    "FSL 5ttgen may internally upsample coarse T1 input for FIRST and performs model registration; this is part of the native segmentation algorithm, not a new T1-to-DWI registration.",
    "This anatomical operation does not execute SIFT2 or batch scheduling.",
]


def alignment_review(item, reference, cancelled=lambda: False):
    """Verify immutable evidence, never treat matching dimensions as registration.

    A review is explicit scientific evidence, not an automatically inferred label.
    It binds source anatomy and target reference bytes, supplied transform/log
    evidence and multi-plane review notes. It does not assign biological acceptance.
    """
    declaration = item["declarations"]
    path = Path(declaration.get("anatomical_review", ""))
    if not path.is_file() or path.stat().st_size > 1024**2:
        raise ValueError(
            "ACT/anatomy needs a hash-bound anatomical_review record, not a filename or identity transform"
        )
    record = read_json(path.read_text())
    if record.get("schema") != "tractosphere.anatomical-alignment-review/1":
        raise ValueError("Unsupported anatomical alignment review schema")
    for key in ("subject_id", "space_id"):
        if (
            not record.get(key)
            or record[key] != declaration.get(key)
            or record[key] != reference["declarations"].get(key)
        ):
            raise ValueError("Anatomical review subject/space differs from the selected inputs")
    expected_anatomy = declaration.get("anatomy_sha256")
    if item["kind"] == "anatomy":
        expected_anatomy = next(s["sha256"] for s in item["sources"] if s["path"] == item["path"])
    if expected_anatomy != record.get("anatomy", {}).get("sha256"):
        raise ValueError("Anatomical review does not identify this anatomy/segmentation source")
    if str(Path(record.get("reference", {}).get("path", "")).resolve()) != str(
        Path(reference["path"]).resolve()
    ):
        raise ValueError("Anatomical review identifies a different target reference")
    if set(record.get("planes_reviewed", [])) != {"axial", "sagittal", "coronal"} or not record.get(
        "review_notes"
    ):
        raise ValueError(
            "Record multi-plane anatomical observations before ACT; matching grids are insufficient"
        )
    evidence = [record.get("anatomy", {}), record.get("reference", {}), *record.get("evidence", [])]
    if len(evidence) < 3:
        raise ValueError(
            "Registration review needs source/reference and original transform or processing evidence"
        )
    hashes = {str(path.resolve()): hash_file(path, cancelled)}
    for row in evidence:
        check_cancel(cancelled)
        p = Path(row.get("path", ""))
        if not p.is_file() or hash_file(p, cancelled) != row.get("sha256"):
            raise ValueError("Anatomical registration evidence changed or is missing")
        hashes[str(p.resolve())] = row["sha256"]
    ref_hash = next(s["sha256"] for s in reference["sources"] if s["path"] == reference["path"])
    if ref_hash != record["reference"]["sha256"]:
        raise ValueError("Reference image bytes differ from the anatomical review")
    return record, hashes


def tissue_statistics(path, cancelled=lambda: False):
    """Scan true fractions without repair; bound memory to one spatial slab."""
    totals = np.zeros(5, dtype="f8")
    nonzero = np.zeros(5, dtype=np.int64)
    dominant = np.zeros(5, dtype=np.int64)
    included = 0
    maximum_sum_error = 0.0
    with image_data(Path(path)) as (affine, data):
        if data.ndim != 4 or data.shape[3] != 5:
            raise ValueError("5TT must have exactly five channels in the declared MRtrix order")
        for z in range(data.shape[2]):
            check_cancel(cancelled)
            a = np.asarray(data[:, :, z, :], dtype="f8")
            if not np.isfinite(a).all() or np.any(a < 0) or np.any(a > 1):
                raise ValueError("Invalid/nonfinite 5TT fractions; values were not repaired")
            sums = a.sum(axis=-1)
            brain = sums > 0.5
            errors = np.minimum(np.abs(sums), np.abs(sums - 1))
            maximum_sum_error = max(maximum_sum_error, float(errors.max()))
            if (errors > 1e-3).any():
                raise ValueError(
                    "5TT fractions do not sum to zero or one within the existing 0.001 contract"
                )
            totals += a.sum(axis=(0, 1))
            nonzero += (a > 0).sum(axis=(0, 1))
            dominant += np.bincount(a[brain].argmax(axis=-1), minlength=5)
            included += int(brain.sum())
        if included == 0 or totals[2] == 0 or totals[:2].sum() == 0:
            raise ValueError("5TT has no brain, white matter or gray matter compartment")
        return dict(
            shape=list(data.shape),
            affine_mm=affine.tolist(),
            brain_voxels=included,
            fraction_sum_tolerance=1e-3,
            maximum_sum_error=maximum_sum_error,
            channel_order=list(CHANNELS),
            nonzero_voxels=nonzero.tolist(),
            dominant_voxels=dominant.tolist(),
            fraction_sums=totals.tolist(),
            volume_mm3=(totals * abs(np.linalg.det(affine[:3, :3]))).tolist(),
            fractions_unmodified=True,
        )


def _fsl_environment(environment, cancelled):
    """Use the existing dependency manager to locate FSL; bind native helpers/data.

    No installer is invoked. FIRST subprocesses remain local (FSLPARALLEL=0), in
    the shared runner's process group so cancellation reaches all descendants.
    """
    from ..dependencies import inspect_dependencies

    inventory = inspect_dependencies(cancelled=cancelled)
    # FSLDIR is verified against the discovered FAST executable, never inferred
    # from a subject directory or an arbitrary executable basename.
    root = Path(os.environ.get("FSLDIR", ""))
    if not root.is_absolute() or not (root / "etc/fslversion").is_file():
        raise ValueError("Set FSLDIR to the installed FSL root; segmentation is unavailable")
    if os.name == "nt":
        raise ValueError(
            "Native Windows FSL segmentation is not supported; import a verified 5TT instead"
        )
    version = (root / "etc/fslversion").read_text().strip()
    if version != "6.0.7.18":
        raise ValueError(
            "FSL 5TT generation is currently validated only for FSL 6.0.7.18; importing a verified 5TT remains available"
        )
    fast = next((r for r in inventory["tools"] if r["family"] == "FSL" and r["name"] == "fast"), {})
    if Path(fast.get("path", "")).resolve() != (root / "bin/fast").resolve():
        raise ValueError("Dependency manager FAST and FSLDIR identify different installations")
    hashes = {}
    # Full executable and model identity is recorded, including helper scripts
    # that FIRST launches. This is bounded software data, never a research scan.
    for name in (
        "fast",
        "bet",
        "flirt",
        "run_first_all",
        "first",
        "first_flirt",
        "first_boundary_corr",
        "fslmaths",
        "fslstats",
        "fslval",
        "standard_space_roi",
        "fsl_sub",
        "convert_xfm",
        "imtest",
        "remove_ext",
    ):
        p = root / "bin" / name
        if not p.is_file() or not os.access(p, os.X_OK):
            raise ValueError("Missing FSL segmentation dependency: " + name)
        hashes[str(p)] = hash_file(p, cancelled)
    models = root / "data/first/models_336_bin"
    if not models.is_dir():
        raise ValueError("Missing FIRST models; no placeholder segmentation will be generated")
    for p in sorted(models.rglob("*")):
        if p.is_file():
            hashes[str(p)] = hash_file(p, cancelled)
    for p in (
        root / "etc/fslversion",
        root / "data/standard/MNI152_T1_1mm.nii.gz",
        root / "data/standard/MNI152_T1_1mm_brain.nii.gz",
    ):
        if not p.is_file():
            raise ValueError("Missing FSL registration model: " + str(p))
        hashes[str(p)] = hash_file(p, cancelled)
    environment["overrides"].update(FSLDIR=str(root), FSLOUTPUTTYPE="NIFTI_GZ", FSLPARALLEL="0")
    # Modern fsl_sub: zero means all cores, so bind one local worker explicitly.
    configs = sorted(
        {
            p.resolve()
            for p in (root / "lib").glob("python*/site-packages/fsl_sub/plugins/fsl_sub_shell.yml")
        }
    )
    if len(configs) != 1:
        raise ValueError("Cannot bind a unique local fsl_sub shell configuration")
    environment["overrides"].update(FSLSUB_PARALLEL="1", FSLSUB_CONF=str(configs[0]))
    hashes[str(configs[0])] = hash_file(configs[0], cancelled)
    # Bind scheduler behavior and the FIRST image-weighting templates as well as
    # the entry scripts. A library update must invalidate a reviewed preview.
    for p in sorted(configs[0].parents[1].rglob("*.py")):
        hashes[str(p)] = hash_file(p, cancelled)
    for p in sorted((root / "data/standard").glob("MNI152_T1_1mm*")):
        if p.is_file():
            hashes[str(p)] = hash_file(p, cancelled)
    runtime = root / "bin/python3.12"
    if not runtime.is_file():
        raise ValueError("The validated FSL Python runtime is unavailable")
    hashes[str(runtime.resolve())] = hash_file(runtime.resolve(), cancelled)
    environment["overrides"]["PATH"] += (
        os.pathsep + str(root / "share/fsl/bin") + os.pathsep + str(root / "bin")
    )
    return dict(
        version=version,
        root=str(root),
        discovery={k: fast.get(k) for k in ("path", "version", "version_source", "state")},
    ), hashes


def anatomical_input_qc(source, mask=None):
    """Require declared T1 contrast and measure signal without changing values.

    FA and b0 are also 3D: dimensionality cannot authorize tissue segmentation.
    Intensity statistics describe input quality, not clinical acceptance cutoffs.
    """
    if (
        source["declarations"].get("anatomical_modality") != "T1w"
        or source["declarations"].get("metric") == "FA"
    ):
        raise ValueError(
            "5ttgen fsl requires explicit anatomical_modality T1w; FA, b0 and unknown contrast are blocked"
        )
    with image_data(Path(source["path"])) as (_, values):
        if len(values.shape) != 3 or np.prod(values.shape) * 4 > 256 * 1024**2:
            raise ValueError(
                "T1 inspection requires a 3D image within the 256 MiB intensity budget"
            )
        a = np.asarray(values, dtype="f4")
        if not np.isfinite(a).all() or not np.any(a):
            raise ValueError("T1 has empty or nonfinite signal; no tissue maps will be fabricated")
        if mask is not None:
            from .dti import _aligned

            a = a[_aligned(Path(mask["path"]), source["geometry"]) > 0]
        if not a.size or np.ptp(a) == 0:
            raise ValueError("T1 has constant/empty signal in the selected region")
        return dict(
            modality="T1w",
            intensity_units="source arbitrary intensity units; no scaling change",
            evaluated_values=int(a.size),
            nonpositive_values=int((a <= 0).sum()),
            negative_values=int((a < 0).sum()),
            percentiles=np.percentile(a, [0, 1, 5, 50, 95, 99, 100]).tolist(),
            voxel_sizes_mm=source["geometry"]["voxel_sizes_mm"],
            limitation="Declared contrast and intensity variation do not establish segmentation accuracy; inspect brain coverage and native FAST/FIRST output",
        )


def prepare_anatomy(
    artifacts,
    tasks,
    output_directory,
    *,
    nthreads=2,
    tool_directory=None,
    cancelled=lambda: False,
    progress=lambda _: None,
):
    """Compile one independent anatomical task through the existing typed graph."""
    if len(tasks) != 1 or tasks[0].algorithm not in ALGORITHMS:
        raise ValueError("Select one implemented 5TT operation; verify it before the next stage")
    if isinstance(nthreads, bool) or not isinstance(nthreads, int) or not 1 <= nthreads <= 8:
        raise ValueError("Anatomical tasks require 1..8 bounded threads")
    task = tasks[0]
    if any(not isinstance(v, str) for v in task.inputs.values()):
        raise ValueError(
            "Anatomical tasks require imported verified inputs, not unverified expected products"
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
    _association(bound)
    source = bound.get("anatomy", bound.get("five_tt"))
    review, extra = alignment_review(source, bound["reference"], cancelled)
    folder = Path(output_directory).expanduser().absolute()
    if folder.exists() or folder.is_symlink() or not folder.parent.is_dir():
        raise ValueError("Choose a NEW anatomy attempt directory with an existing parent")
    sources = {s["path"]: s["sha256"] for a in artifacts for s in a.payload["sources"]}
    sources.update(extra)
    if any(folder.is_relative_to(Path(p).resolve().parent) for p in sources):
        raise ValueError("Outputs must be outside original anatomical/evidence directories")
    require_local_processing([folder, *sources])
    method = task.algorithm
    generation = method == "5ttgen.fsl"
    input_qc = anatomical_input_qc(source, bound.get("mask")) if generation else None
    names = {"mrconvert", "5ttcheck", method.split(".")[0]}
    if generation:
        names |= {
            "mrcalc",
            "mrmath",
            "mrstats",
            "mrcat",
            "mrthreshold",
            "mrgrid",
            "mrtransform",
            "maskfilter",
            "meshconvert",
            "mesh2voxel",
        }
    tools = detect_mrtrix(
        names=tuple(sorted(names)), tool_directory=tool_directory, cancelled=cancelled
    )
    if any(t["version"] != "3.0.5" for t in tools.values()):
        raise ValueError("5TT adapter is validated only for MRtrix3 3.0.5")
    environment = _environment_contract(cancelled)
    environment["unset"] = ["MRTRIX_QUIET", "MRTRIX_OVERWRITE"]
    environment["overrides"].update(OMP_NUM_THREADS=str(nthreads), OPENBLAS_NUM_THREADS="1")
    environment["overrides"]["PATH"] = (
        str(Path(tools["mrconvert"]["path"]).parent) + os.pathsep + os.environ.get("PATH", "")
    )
    fsl = None
    software_hashes = {}
    if generation:
        fsl, software_hashes = _fsl_environment(environment, cancelled)
        lib = Path(tools["5ttgen"]["path"]).parent.parent / "lib/mrtrix3"
        if not (lib / "_5ttgen/fsl.py").is_file():
            raise ValueError("Installed FSL 5ttgen implementation is unavailable")
        for p in sorted(lib.rglob("*.py")):
            software_hashes[str(p)] = hash_file(p, cancelled)
        runtime = Path(sys.executable).resolve()
        software_hashes[str(runtime)] = hash_file(runtime, cancelled)
    commands, outputs, product_specs = [], [], []

    def command(step, exe, args, inputs, files, kind):
        """Only verified native argv; no shell or user-provided command fragments."""
        text = _help(tools[exe]["path"], *(("fsl",) if exe == "5ttgen" else ()))
        for flag in [str(a) for a in args if str(a).startswith("-")]:
            if flag not in text:
                raise ValueError(f"Installed {exe} does not support {flag}")
        argv = [tools[exe]["path"], *map(str, args), "-nthreads", str(nthreads)]
        if exe == "5ttgen":
            argv.insert(0, str(Path(sys.executable).resolve()))
        commands.append(
            dict(
                task=task.id,
                step=step,
                argv=argv,
                display=shlex.join(argv),
                inputs=list(map(str, inputs)),
                outputs=files,
                kind=kind,
            )
        )
        outputs.extend(files)

    five = source["path"]
    if generation:
        masking = task.parameters["brain_extraction"]
        if ("mask" in bound) != (masking == "mask"):
            raise ValueError("Choose exactly the mask required by the explicit extraction policy")
        five = str(folder / (task.id + "-5tt.mif"))
        args = [
            "fsl",
            source["path"],
            five,
            "-nocrop",
            "-nocleanup",
            "-scratch",
            folder / "scratch",
            "-info",
        ]
        if masking == "mask":
            args += ["-mask", bound["mask"]["path"]]
        if masking == "premasked":
            args += ["-premasked"]
        if task.parameters["amygdala_hippocampus_sgm"] == "on":
            args += ["-sgm_amyg_hipp"]
        command(
            task.id + "-segment",
            "5ttgen",
            args,
            [b["path"] for b in bound.values()],
            [Path(five).name],
            "five_tt",
        )
        product_specs.append(dict(role="five_tt", kind="5tt", path=Path(five).name))
    command(task.id + "-check", "5ttcheck", [five], [five], [], "check")
    if method == "5tt2gmwmi":
        name = task.id + "-gmwmi.nii.gz"
        command(task.id + "-interface", "5tt2gmwmi", [five, folder / name], [five], [name], "gmwmi")
        product_specs.append(dict(role="gmwmi", kind="gmwmi", path=name))
    if method == "5tt2vis":
        name = task.id + "-visualization.nii.gz"
        command(
            task.id + "-visualization",
            "5tt2vis",
            [five, folder / name],
            [five],
            [name],
            "visualization",
        )
        product_specs.append(dict(role="visualization", kind="tissue_scalar", path=name))
        for i, channel in enumerate(CHANNELS):
            name = task.id + "-" + channel + ".nii.gz"
            command(
                task.id + "-" + channel,
                "mrconvert",
                [
                    five,
                    folder / name,
                    "-coord",
                    "3",
                    str(i),
                    "-axes",
                    "0,1,2",
                    "-datatype",
                    "float32",
                ],
                [five],
                [name],
                "compartment",
            )
            product_specs.append(dict(role=channel, kind="tissue_scalar", path=name, channel=i))
    nvox = int(np.prod(source["geometry"]["shape"][:3]))
    estimate = nvox * (1000 if generation else 64)  # scratch incl. FIRST model resampling
    if shutil.disk_usage(folder.parent).free < estimate + 5 * 1024**3:
        raise ValueError("Insufficient anatomy scratch estimate plus 5 GiB safety margin")
    outputs += ["scientific-selection.json", "qc-metrics.json", "qc-report.html", "qc-catalog.json"]
    implementation = [
        Path(__file__),
        Path(__file__).with_name("anatomy_registry.py"),
        Path(__file__).with_name("imports.py"),
        Path(__file__).with_name("planning.py"),
        Path(__file__).parents[1] / "execution.py",
    ]
    return ExecutionPlan(
        dict(
            kind="mrtrix_anatomy_execution_plan",
            schema_version=1,
            artifacts=[a.to_dict() for a in artifacts],
            tasks=[task.to_dict()],
            output_directory=str(folder),
            tools=tools,
            environment=environment,
            commands=commands,
            outputs=outputs,
            extra_input_hashes=sources,
            software_hashes=software_hashes,
            fsl=fsl,
            anatomical_input_qc=input_qc,
            source=source,
            reference=bound["reference"],
            review=review,
            five_tt=five,
            product_specs=product_specs,
            issues=proposal["issues"],
            parameters=dict(
                nthreads=nthreads,
                estimated_working_bytes=estimate,
                storage_margin_bytes=5 * 1024**3,
            ),
            scientific_limits=LIMITS,
            build=_revision(),
            compiler="tractosphere.anatomy/1",
            implementation_hashes={str(p): hash_file(p, cancelled) for p in implementation},
        )
    )


def verify_anatomy_step(folder, plan, command, cancelled=lambda: False):
    """Verify fractions, native check warnings and actual compartment geometry."""
    if command["kind"] in {"five_tt", "check"}:
        stats = tissue_statistics(plan["five_tt"], cancelled)
        geo = plan["reference"]["geometry"]
        voxel_mapping(
            np.asarray(stats["affine_mm"]),
            stats["shape"],
            np.asarray(geo["affine_mm"]),
            geo["shape"],
        )
        if command["kind"] == "check":
            text = (folder / "logs" / (command["step"] + ".log")).read_text(errors="replace")
            if "[WARNING]" in text or "[ERROR]" in text:
                raise ValueError("Native 5ttcheck reported invalid tissue data; inspect saved log")
            stats["native_5ttcheck"] = text
        return stats
    path = folder / command["outputs"][0]
    with image_data(path) as (affine, array):
        geo = plan["reference"]["geometry"]
        voxel_mapping(affine, array.shape, np.asarray(geo["affine_mm"]), geo["shape"])
        a = np.asarray(array)
        if a.ndim != 3 or not np.isfinite(a).all():
            raise ValueError("Nonfinite or wrong-dimensional tissue visualization/interface")
        if command["kind"] == "gmwmi" and (not a.any() or (a < 0).any() or (a > 1).any()):
            raise ValueError("GMWMI must be a nonempty finite seed-probability image in [0,1]")
        if command["kind"] == "compartment":
            channel = next(p["channel"] for p in plan["product_specs"] if p["path"] == path.name)
            from .dti import _aligned

            original = _aligned(Path(plan["five_tt"]), geo)[..., channel]
            export = _aligned(path, geo)
            if not np.array_equal(original, export):
                raise ValueError("Tissue compartment export changed scientific fractions")
        return dict(
            shape=list(a.shape),
            nonzero_voxels=int(np.count_nonzero(a)),
            minimum=float(a.min()),
            maximum=float(a.max()),
            geometry_verified=True,
        )


def verify_anatomy(folder, plan, cancelled=lambda: False, progress=lambda _: None):
    """Publish verified typed results and existing QC catalog/report conventions."""
    stats = tissue_statistics(plan["five_tt"], cancelled)
    products = []
    declarations = dict(plan["source"]["declarations"])
    declarations.update(
        channels=CHANNEL_DECLARATION, anatomy_sha256=plan["review"]["anatomy"]["sha256"]
    )
    declarations["five_tt_sha256"] = hash_file(Path(plan["five_tt"]), cancelled)
    for spec in plan["product_specs"]:
        artifact = import_artifact(
            ImportRequest(
                spec["kind"],
                folder / spec["path"],
                declarations={**declarations, "tissue": spec["role"]},
                provenance="Native MRtrix5TT derivative. Numerical checks completed; scientific review and acceptance pending.",
            ),
            cancelled=cancelled,
            progress=progress,
        )
        products.append(dict(role=spec["role"], artifact=artifact.to_dict()))
    result = dict(
        execution="completed",
        numerical_verification="passed",
        automated_qc="completed",
        scientific_review="required",
        scientific_acceptance="unassigned",
        tissue_statistics=stats,
        findings=LIMITS,
        products=products,
    )
    _publish_json(folder / "qc-metrics.json", result)
    with (folder / "qc-report.html").open("x") as f:
        f.write(
            '<!doctype html><meta charset="utf-8"><title>5TT scientific QC</title><style>body{font:15px system-ui;max-width:1100px;margin:40px auto}pre{white-space:pre-wrap}</style><h1>Five-tissue anatomical evidence</h1><p>Numerical verification completed · Scientific review required · Acceptance unassigned</p><pre>'
            + html.escape(json.dumps(result, indent=2))
            + "</pre><h2>Actual commands</h2><pre>"
            + html.escape("\n".join(c["display"] for c in plan["commands"]))
            + "</pre>"
        )
    _publish_json(
        folder / "qc-catalog.json",
        dict(
            schema_version=1,
            kind="dmri_qc_catalog",
            producer="TractoSphere anatomical MRtrix adapter",
            identity=dict(subject=declarations["subject_id"]),
            stages=[
                dict(
                    id="anatomy",
                    title="Five-tissue numerical and native validation",
                    execution="completed",
                    technical="passed",
                    automated_qc="pending",
                    scientific_review="required",
                    findings=LIMITS,
                    artifacts=[
                        dict(kind=kind, label=label, path=name, sha256=hash_file(folder / name))
                        for kind, label, name in (
                            ("report", "5TT QC", "qc-report.html"),
                            ("metrics", "Tissue measurements", "qc-metrics.json"),
                        )
                    ],
                )
            ],
        ),
    )
    return result


class AnatomyAdapter:
    """Callbacks into execute_artifact_task; process ownership stays shared."""

    def compile(self, task, inputs, output_directory):
        """Compile a reusable operation independently of the GUI."""
        return prepare_anatomy(tuple(inputs.values()), (task,), output_directory)

    def validate(self, task, inputs):
        """Return shared typed graph findings."""
        return tuple(build_workflow(tuple(inputs.values()), (task,)).to_dict()["issues"])

    def qc(self, manifest):
        """Return recorded checks without assigning acceptance."""
        return manifest["validation"]

    def run(self, plan, *, approval, cancelled=lambda: False, progress=lambda _: None):
        """Recompile exact approval, reuse process groups/logs/cancellation/records."""
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
                    raise ValueError("Anatomical input/tool/evidence changed: " + str(p))

        return execute_artifact_task(
            plan,
            approval=approval,
            recompile=lambda: prepare_anatomy(
                artifacts,
                tasks,
                folder,
                nthreads=data["parameters"]["nthreads"],
                tool_directory=Path(data["tools"]["mrconvert"]["path"]).parent,
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
            verify_step=lambda c: verify_anatomy_step(folder, data, c, cancelled),
            verify=lambda _: verify_anatomy(folder, data, cancelled, progress),
            verify_sources=sources,
            cancelled=cancelled,
            progress=progress,
        )
