"""Native MRtrix3 connectomics over the existing scientific artifact executor.

Python/CLI/GUI call prepare_connectome(), review exact commands, then explicitly
approve with execute_plan(). Sources are immutable. Algebra below independently
checks native matrices; it never replaces tck2connectome as the scientific engine.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import shlex
import shutil
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

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
from ..tract_index import IndexedStreamlines
from ..validation import json_compatible
from .connectome_registry import METRICS
from .contracts import Artifact, ImportRequest, artifact_id
from .csd import _help
from .dti import _fresh, _revision, image_data
from .planning import Task, build_workflow
from .records import save_selection
from .tracking import TrackingStep, _association

ALGORITHMS = frozenset(("tck2connectome", "connectome2tck"))
LIMITS = [
    "Execution, numerical verification, automated QC, scientific review and scientific acceptance are separate; acceptance remains unassigned.",
    "Small validation tractograms are test-scale, not reproducible quantitative whole-brain connectivity. Counts are not axon counts and mean FA/length are not connection strength.",
    "Atlas registration and endpoint assignment parameters affect results. Matching physical grids and declarations do not independently establish biological correspondence.",
    "The connectome operation does not generate or alter tracks. Optional exact-source SIFT2 factors are applied once by native MRtrix; no additional mu scaling, implicit registration/resampling, missing-parcel synthesis or FA clipping is performed.",
    "Empty-edge means are undefined, although MRtrix writes zero. The separate occupancy mask distinguishes them from measured zero values.",
]


def read_matrix(
    path: Path | str, *, allow_invalid: bool = False, allow_negative: bool = False
) -> np.ndarray:
    """Bound CSV memory and preserve original double precision, including zeros."""
    path = Path(path)
    if path.stat().st_size > 128 * 1024**2:
        raise ValueError("Matrix CSV exceeds 128 MiB bounded import")
    matrix = np.loadtxt(path, delimiter=",", comments="#", ndmin=2)
    if matrix.shape[0] != matrix.shape[1] or not 1 <= len(matrix) <= 2048:
        raise ValueError("Connectome requires a square matrix of at most 2048 original node IDs")
    if not allow_invalid and (
        not np.isfinite(matrix).all() or (not allow_negative and np.any(matrix < 0))
    ):
        raise ValueError("Nonfinite or negative matrix values; scientific values were not repaired")
    return matrix


def node_table(atlas: Artifact | dict[str, Any]) -> list[dict[str, Any]]:
    """Keep original label ID == matrix index+1, including every missing parcel."""
    a = atlas.to_dict() if isinstance(atlas, Artifact) else atlas
    ids = a["verification"]["label_ids"]
    if not ids or max(ids) > 2048:
        raise ValueError("Atlas requires 1..2048 positive node IDs; no silent renumbering")
    names = a.get("metadata", {}).get("region_names", {})
    counts = a["verification"]["label_voxel_counts"]
    return [
        dict(
            matrix_index=i - 1,
            parcel_id=i,
            label=names.get(str(i), f"Node {i}"),
            voxel_count=counts.get(str(i), 0),
            nonempty=i in ids,
            atlas_id=a["id"],
        )
        for i in range(1, max(ids) + 1)
    ]


def assignments(
    path: Path | str, count: int, ids: set[int], cancelled: Callable[[], bool] = lambda: False
) -> np.ndarray:
    """Read exactly one endpoint pair per source streamline, preserving ordering."""
    if not 0 <= count <= 1_000_000:
        raise ValueError(
            "Stage 5 assignment verification is bounded to one million streamlines; no implicit subset"
        )
    pairs = np.empty((count, 2), dtype=np.int32)
    rows = 0
    with Path(path).open() as f:
        for line in f:
            check_cancel(cancelled)
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                row = [int(x) for x in line.split()]
            except ValueError as exc:
                raise ValueError("Invalid integer assignment record") from exc
            if len(row) != 2 or any(i not in ids and i != 0 for i in row):
                raise ValueError("Invalid endpoint pair or unknown parcel ID in assignments")
            if rows >= count:
                raise ValueError("Assignment file has more rows than the exact source tractogram")
            pairs[rows] = row
            rows += 1
    if rows != count:
        raise ValueError("Assignment count/order contract differs from source tractogram")
    return pairs


def aggregate(
    pairs: np.ndarray,
    values: np.ndarray,
    size: int,
    symmetric: bool,
    zero_diagonal: bool,
    mean: bool = False,
    weights: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Independent verification of native upper-triangle/symmetric edge semantics."""
    totals = np.zeros((size, size))
    occupancy = np.zeros((size, size), dtype=np.int64)
    if len(pairs) != len(values) or weights is not None and len(weights) != len(pairs):
        raise ValueError("Aggregation evidence differs from exact streamline count/order")
    # Bounded vector algebra is independent verification, never the scientific
    # calculator. Native tck2connectome supplies every published metric.
    canonical = np.sort(pairs, axis=1)
    a, b = canonical.T
    valid = (a > 0) & (b > 0)
    if zero_diagonal:
        valid &= a != b
    where = (a[valid] - 1, b[valid] - 1)
    factors = np.ones(len(values)) if weights is None else np.asarray(weights)
    np.add.at(occupancy, where, 1)
    np.add.at(totals, where, np.asarray(values)[valid] * factors[valid])
    if mean:
        denominator = np.zeros_like(totals)
        np.add.at(denominator, where, factors[valid])
        np.divide(totals, denominator, out=totals, where=denominator > 0)
    if symmetric:
        totals += np.triu(totals, 1).T
        occupancy += np.triu(occupancy, 1).T
    return totals, occupancy


def streamline_sample_mean(points: np.ndarray, samples: np.ndarray) -> float:
    """Independently verify MRtrix3's arc-length-weighted trilinear mean.

    Integrate the linear interpolant between adjacent saved vertex samples,
    then divide by total polyline length. Endpoints have half-segment weights.
    This is not the arithmetic mean of vertices (uneven spacing changes that).
    Native tcksample remains the calculator used for the scientific result.
    """
    lengths = np.linalg.norm(np.diff(np.asarray(points, dtype="f8"), axis=0), axis=1)
    total = float(lengths.sum())
    return float(np.dot(lengths, (samples[:-1] + samples[1:]) * 0.5) / total) if total else 0.0


def load_bundle(
    path: Path | str, *, cancelled: Callable[[], bool] = lambda: False, verify: bool = True
) -> dict[str, Any]:
    """Reopen only a hash-bound native result; raw historical CSV has no assignments."""
    path = Path(path)
    if path.stat().st_size > 16 * 1024**2:
        raise ValueError("Oversized connectome bundle")
    from .records import read_json

    data = read_json(path.read_text())
    required = {
        "hashes",
        "nodes",
        "matrices",
        "occupancy",
        "source",
        "atlas",
        "reference",
        "assignments",
        "assignment_parameters",
        "qc",
        "declarations",
    }
    if not required.issubset(data):
        raise ValueError("Incomplete connectome bundle; source/assignments/node mapping required")
    if data.get("schema") != "tractosphere.connectome/1":
        raise ValueError("Unsupported connectome bundle schema")
    if verify:
        for name, digest in data["hashes"].items():
            check_cancel(cancelled)
            if hash_file(Path(name), cancelled) != digest:
                raise ValueError("Connectome source/result identity changed: " + Path(name).name)
    count = len(data["nodes"])
    if [r["parcel_id"] for r in data["nodes"]] != list(range(1, count + 1)):
        raise ValueError("Invalid node-to-matrix index mapping")
    for metric, entry in data["matrices"].items():
        if read_matrix(
            entry["path"], allow_negative=metric in {"mean_fa", "sift2_mean_fa"}
        ).shape != (count, count):
            raise ValueError("Matrix dimensions differ from node mapping")
    return data


def require_bundle_registration(bundle, declarations=None, *, cancelled=lambda: False):
    """Edge extraction is analysis too; old matrices remain openable for inspection."""
    from ..registration_review import require_registration

    context = bundle.get("registration_context")
    if not context and (declarations or {}).get("registration_qc"):
        from ..registration_qc import verify_bundle

        qc_path = declarations["registration_qc"]
        evidence, _ = verify_bundle(qc_path, cancelled)
        if hash_file(Path(bundle["atlas"]), cancelled) != hash_file(
            Path(evidence["atlas"]["path"]), cancelled
        ):
            raise ValueError("Connectome and native registration QC bind different atlases")
        context = dict(atlas=evidence["atlas"], reference=evidence["reference"])
        context["atlas"]["declarations"]["registration_qc"] = qc_path
    if not context:
        raise ValueError(
            "Historical connectome lacks current registration context; rebind reviewed atlas/reference before new scientific extraction"
        )
    return require_registration(context["atlas"], context["reference"], cancelled=cancelled)


def import_bundle(
    request: ImportRequest,
    *,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[str], None] = lambda _: None,
) -> Artifact:
    """Expose verified connectomes to the existing typed artifact inventory."""
    data = load_bundle(request.path, cancelled=cancelled)
    declarations = json_compatible(request.declarations)
    for key in ("subject_id", "space_id"):
        if declarations.get(key, data["declarations"].get(key)) != data["declarations"].get(key):
            raise ValueError("Connectome association declaration conflicts with its source")
    # Preserve caller declarations so reimport has exactly the same identity.
    sources = [
        dict(
            path=str(request.path),
            size_bytes=request.path.stat().st_size,
            sha256=hash_file(request.path, cancelled),
        )
    ]
    sources += [
        dict(path=p, size_bytes=Path(p).stat().st_size, sha256=h)
        for p, h in sorted(data["hashes"].items())
    ]
    return Artifact(
        dict(
            id=artifact_id(request.kind, sources, declarations, request.provenance),
            kind=request.kind,
            path=str(request.path),
            declarations=declarations,
            provenance=dict(statement=request.provenance, scientific_acceptance="unknown"),
            request=dict(
                kind=request.kind,
                path=str(request.path),
                declarations=declarations,
                provenance=request.provenance,
            ),
            sources=sources,
            geometry=None,
            gradients=None,
            metadata=dict(nodes=len(data["nodes"]), metrics=list(data["matrices"])),
            verification=dict(coverage="All bundle/source/matrix hashes and node mapping verified"),
        )
    )


def prepare_connectome(
    artifacts: Sequence[Artifact],
    tasks: Sequence[Task],
    output_directory: Path | str,
    *,
    nthreads: int = 2,
    tool_directory: Path | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[str], None] = lambda _: None,
) -> ExecutionPlan:
    """Compile native count/length/FA or edge extraction; never repeat upstream work."""
    if not tasks or any(t.algorithm not in ALGORITHMS for t in tasks):
        raise ValueError("Select implemented connectome or edge extraction tasks")
    if isinstance(nthreads, bool) or not isinstance(nthreads, int) or not 1 <= nthreads <= 64:
        raise ValueError("Threads must be an integer in 1..64")
    if any(not isinstance(v, str) for t in tasks for v in t.inputs.values()):
        raise ValueError(
            "Import a verified completed connectome before extracting edges; no unverified assignment reuse"
        )
    used = {v for t in tasks for v in t.inputs.values()}
    for artifact in artifacts:
        if artifact.id in used:
            artifact.assert_current(cancelled)
    artifacts = _fresh(tuple(a for a in artifacts if a.id in used), cancelled, progress)
    proposed = build_workflow(
        artifacts, tuple(tasks), verify_sources=False, cancelled=cancelled, progress=progress
    ).to_dict()
    errors = [i["message"] for i in proposed["issues"] if i["severity"] == "error"]
    if errors:
        raise ValueError("; ".join(errors))
    folder = Path(output_directory).expanduser().absolute()
    if folder.exists() or folder.is_symlink() or not folder.parent.is_dir():
        raise ValueError("Choose a NEW attempt directory with an existing parent")
    folder = folder.resolve()
    sources = {s["path"]: s["sha256"] for a in artifacts for s in a.payload["sources"]}
    if any(folder.is_relative_to(Path(p).resolve().parent) for p in sources):
        raise ValueError("Output must be outside original scientific source directories")
    require_local_processing([folder, *sources])
    names = {"mrconvert", *(t.algorithm for t in tasks)}
    if any(set(t.parameters.get("metrics", ())) & {"mean_fa", "sift2_mean_fa"} for t in tasks):
        names.add("tcksample")
    tools = detect_mrtrix(
        names=tuple(sorted(names)), tool_directory=tool_directory, cancelled=cancelled
    )
    if any(t["version"] != "3.0.5" for t in tools.values()):
        raise ValueError("Stage 5 accepts only verified MRtrix3 3.0.5")
    helps = {n: _help(t["path"]) for n, t in tools.items()}
    environment = _environment_contract(cancelled)
    environment["unset"] = ["MRTRIX_QUIET", "MRTRIX_OVERWRITE"]
    environment["overrides"]["PATH"] = (
        str(Path(next(iter(tools.values()))["path"]).parent)
        + os.pathsep
        + os.environ.get("PATH", "")
    )
    inventory = {a.id: a.to_dict() for a in artifacts}
    commands = []
    rows = []
    outputs = []
    estimate = 0

    def cmd(step, exe, args, inputs, out, kind):
        for arg in args:
            if str(arg).startswith("-") and str(arg) not in helps[exe]:
                raise ValueError(f"Installed {exe} does not advertise {arg}")
        argv = [tools[exe]["path"], *map(str, args), "-nthreads", str(nthreads)]
        commands.append(
            dict(
                step=step,
                argv=argv,
                display=shlex.join(argv),
                inputs=list(map(str, inputs)),
                outputs=out,
                kind=kind,
            )
        )
        outputs.extend(out)

    for task in tasks:
        check_cancel(cancelled)
        p = dict(task.parameters)
        bound = {k: inventory[v] for k, v in task.inputs.items()}
        row = dict(task=task.id, algorithm=task.algorithm, parameters=p, inputs=bound, outputs={})
        if task.algorithm == "connectome2tck":
            bundle = load_bundle(bound["connectome"]["path"], cancelled=cancelled)
            row["registration_eligibility"] = require_bundle_registration(
                bundle, bound["connectome"]["declarations"], cancelled=cancelled
            )
            a, b = p["node_a"], p["node_b"]
            size = len(bundle["nodes"])
            if max(a, b) > size:
                raise ValueError("Selected node outside original parcel mapping")
            occ = read_matrix(bundle["occupancy"])
            if occ[min(a, b) - 1, max(a, b) - 1] == 0:
                raise ValueError(
                    "Selected edge has no contributing streamlines under the recorded diagonal policy"
                )
            source = bundle["source"]
            ref = bundle["reference"]
            # Bundle hash verification binds original tract ordering and assignment bytes.
            args = [
                source,
                bundle["assignments"],
                folder / (task.id + "-tracks.tck"),
                "-nodes",
                str(a) if a == b else f"{a},{b}",
                "-exclusive",
                "-files",
                "single",
            ]
            if a == b:
                args += ["-keep_self"]
            row.update(bundle=bundle, selected_pair=[a, b])
            for node in sorted({a, b}):
                name = task.id + "-node-" + str(node) + ".nii.gz"
                row["outputs"]["roi_" + str(node)] = name
                outputs.append(name)
            name = task.id + "-tracks.tck"
            row["outputs"]["tracts"] = name
            extraction_outputs = [name]
            if "weights" in bundle:
                row["outputs"]["weights"] = task.id + "-weights.csv"
                row["outputs"]["weight_record"] = task.id + "-weights.json"
                args += [
                    "-tck_weights_in",
                    bundle["weights"],
                    "-prefix_tck_weights_out",
                    folder / (task.id + "-weights"),
                ]
                extraction_outputs.append(row["outputs"]["weights"])
                outputs += [row["outputs"]["weight_record"], task.id + "-source-indices.txt"]
            cmd(
                task.id + "-extract",
                "connectome2tck",
                args,
                [source, bundle["assignments"]],
                extraction_outputs,
                "extract",
            )
        else:
            _association(bound)
            atlas = bound["nodes"]
            from ..registration_review import require_registration

            row["registration_eligibility"] = require_registration(
                atlas, bound["reference"], cancelled=cancelled
            )
            atlas_codes = {atlas["geometry"].get(k) for k in ("qform_code", "sform_code")} - {
                None,
                0,
            }
            reference_codes = {
                bound["reference"]["geometry"].get(k) for k in ("qform_code", "sform_code")
            } - {None, 0}
            if atlas_codes & {3, 4} and reference_codes and not atlas_codes & reference_codes:
                raise ValueError(
                    "Atlas coded standard space conflicts with the exact reference header; resolve registration/header provenance before execution"
                )
            nodes = node_table(atlas)
            geo = bound["reference"]["geometry"]
            for k in ("nodes", "fa"):
                if k in bound:
                    voxel_mapping(
                        np.asarray(geo["affine_mm"]),
                        geo["shape"],
                        np.asarray(bound[k]["geometry"]["affine_mm"]),
                        bound[k]["geometry"]["shape"],
                    )
            method = p["assignment"]
            args = ["-assignment_" + method]
            if method == "radial_search":
                if p.get("search_mm") is not None:
                    raise ValueError("search_mm is not a radial parameter")
                args += [p["radial_search_mm"]]
            elif method in {"forward_search", "reverse_search"}:
                if p.get("radial_search_mm") is not None or p.get("search_mm") is None:
                    raise ValueError("Choose only the explicit forward/reverse search distance")
                if method == "forward_search" and p["search_mm"] <= 0:
                    raise ValueError("Forward search requires positive distance")
                args += [p["search_mm"]]
            elif p.get("search_mm") is not None or p.get("radial_search_mm") is not None:
                raise ValueError("Endpoint voxel lookup takes no search distance")
            if p["symmetric"] == "yes":
                args += ["-symmetric"]
            if p["zero_diagonal"] == "yes":
                args += ["-zero_diagonal"]
            count = bound["tracts"]["metadata"]["streamlines"]
            if count > 1_000_000:
                raise ValueError(
                    "Stage 5 supports up to one million input streamlines; select an explicit verified subset for this bounded adapter"
                )
            row.update(
                nodes=nodes, source=bound["tracts"]["path"], atlas=atlas["path"], streamlines=count
            )
            weighted = any(METRICS[m].get("weighted") for m in p["metrics"])
            if weighted != ("weights" in bound):
                raise ValueError(
                    "Weighted metrics require exact-source weight factors; unused weights must not be silently ignored"
                )
            if weighted:
                from .sift2 import verify_weight_binding

                row["weight_record"] = verify_weight_binding(
                    bound["weights"], bound["tracts"], cancelled
                )
            if set(p["metrics"]) & {"mean_fa", "sift2_mean_fa"}:
                if "fa" not in bound or bound["fa"]["declarations"].get("metric") != "FA":
                    raise ValueError(
                        "Mean FA requires an explicitly identified compatible FA image"
                    )
                name = task.id + "-fa-per-streamline.txt"
                raw = task.id + "-fa-vertices.txt"
                row["fa_values"] = name
                row["fa_vertices"] = raw
                cmd(
                    task.id + "-sample",
                    "tcksample",
                    [row["source"], bound["fa"]["path"], folder / name, "-stat_tck", "mean"],
                    [row["source"], bound["fa"]["path"]],
                    [name],
                    "fa_sample",
                )
                cmd(
                    task.id + "-sample-qc",
                    "tcksample",
                    [row["source"], bound["fa"]["path"], folder / raw],
                    [row["source"], bound["fa"]["path"]],
                    [raw],
                    "fa_raw",
                )
                # Bound retained per-vertex ASCII evidence from actual source
                # payload size instead of an arbitrary 4096 bytes per track.
                # Native tcksample writes Float32 scalars: even a signed
                # 17-significant-digit scientific value and separator fit in
                # 24 bytes. TCK/TRK coordinates occupy at least 12 bytes/vertex.
                # Headers, separators and TRK metadata make twice the source
                # size conservative. Separate count*256 budgeting below covers
                # per-streamline vectors and assignment records; keep 64 MiB
                # additional scratch allowance and the full 5 GiB safety margin.
                estimate += Path(row["source"]).stat().st_size * 2 + 64 * 1024**2
            elif "fa" in bound:
                raise ValueError("FA input supplied but no FA metric requested")
            requested = list(p["metrics"])
            metrics = list(dict.fromkeys(["count", *requested]))
            row["matrices"] = {}
            row["assignment_outputs"] = {}
            for metric in metrics:
                filename = task.id + "-" + metric + ".csv"
                assignment = task.id + "-" + metric + "-assignments.txt"
                flags = list(METRICS[metric]["flags"])
                extra = []
                if METRICS[metric].get("base_metric", metric) == "mean_fa":
                    flags += ["-scale_file", folder / row["fa_values"]]
                    extra = [folder / row["fa_values"]]
                if METRICS[metric].get("weighted"):
                    flags += ["-tck_weights_in", bound["weights"]["path"]]
                    extra += [bound["weights"]["path"]]
                cmd(
                    task.id + "-" + metric,
                    "tck2connectome",
                    [
                        row["source"],
                        row["atlas"],
                        folder / filename,
                        *args,
                        *flags,
                        "-stat_edge",
                        METRICS[metric]["statistic"],
                        "-out_assignments",
                        folder / assignment,
                    ],
                    [row["source"], row["atlas"], *extra],
                    [filename, assignment],
                    "matrix",
                )
                row["matrices"][metric] = filename
                row["assignment_outputs"][metric] = assignment
            row["outputs"]["connectome"] = task.id + "-connectome.json"
            outputs += [
                row["outputs"]["connectome"],
                task.id + "-nodes.json",
                task.id + "-occupancy.csv",
            ]
            if weighted:
                outputs += [task.id + "-weight-sum.csv", task.id + "-weight-validity.csv"]
            ref = bound["reference"]["path"]
            estimate += len(nodes) ** 2 * 64 + count * 256
        reference_name = task.id + "-reference.nii.gz"
        row["outputs"]["reference"] = reference_name
        cmd(
            task.id + "-reference",
            "mrconvert",
            [ref, folder / reference_name, "-datatype", "float32"],
            [ref],
            [reference_name],
            "reference",
        )
        estimate += 32 * 1024**2
        rows.append(row)
    if shutil.disk_usage(folder.parent).free < estimate + 5 * 1024**3:
        raise ValueError("Insufficient storage for connectome estimate plus 5 GiB safety margin")
    outputs += ["scientific-selection.json", "qc-metrics.json", "qc-report.html", "qc-catalog.json"]
    files = [
        Path(__file__),
        Path(__file__).with_name("connectome_registry.py"),
        Path(__file__).with_name("registry.py"),
        Path(__file__).with_name("planning.py"),
        Path(__file__).with_name("imports.py"),
        Path(__file__).parents[1] / "execution.py",
        Path(__file__).parents[1] / "registration_review.py",
    ]
    return ExecutionPlan(
        dict(
            kind="mrtrix_connectome_execution_plan",
            schema_version=1,
            artifacts=[a.to_dict() for a in artifacts],
            tasks=[t.to_dict() for t in tasks],
            output_directory=str(folder),
            tools=tools,
            environment=environment,
            commands=commands,
            bindings=rows,
            outputs=list(dict.fromkeys(outputs)),
            extra_input_hashes=sources,
            parameters=dict(
                nthreads=nthreads,
                estimated_working_bytes=estimate,
                storage_margin_bytes=5 * 1024**3,
            ),
            scientific_limits=LIMITS,
            issues=proposed["issues"],
            build=_revision(),
            implementation_hashes={str(f): hash_file(f, cancelled) for f in files},
        )
    )


def _track_values(path: Path | str, cancelled: Callable[[], bool]) -> np.ndarray:
    """Bounded indexed access to native physical tracks, no complete tract copy."""
    tracks = IndexedStreamlines(Path(path), cancelled=cancelled)
    try:
        lengths = np.empty(len(tracks))
        for i in range(len(tracks)):
            check_cancel(cancelled)
            line = tracks[i]
            lengths[i] = np.linalg.norm(np.diff(line.astype("f8"), axis=0), axis=1).sum()
        return lengths
    finally:
        tracks.close()


def verify_connectome(
    folder: Path,
    plan: dict[str, Any],
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[str], None] = lambda _: None,
) -> dict[str, Any]:
    """Verify native results independently; retain all nonphysical scientific values."""
    findings = list(LIMITS)
    measurements = []
    products = []
    for row in plan["bindings"]:
        check_cancel(cancelled)
        p = row["parameters"]
        ref = folder / row["outputs"]["reference"]
        from ..atlas import geometry_record
        from ..scene import ImageVolume
        from .dti import _aligned

        original_reference = (
            row["bundle"]["reference"]
            if row["algorithm"] == "connectome2tck"
            else row["inputs"]["reference"]["path"]
        )
        reference_geometry = geometry_record(ImageVolume(ref))
        expected_reference = _aligned(Path(original_reference), reference_geometry)
        with image_data(ref) as (aff, data):
            if not np.isfinite(np.asarray(data)).all() or not np.array_equal(
                expected_reference, np.asarray(data)
            ):
                raise ValueError(
                    "Exported reference values or physical geometry differ from source"
                )
        if row["algorithm"] == "connectome2tck":
            bundle = row["bundle"]
            a, b = sorted(row["selected_pair"])
            pairs = assignments(
                bundle["assignments"],
                bundle["qc"]["total_streamlines"],
                {n["parcel_id"] for n in bundle["nodes"] if n["nonempty"]},
                cancelled,
            )
            indices = np.flatnonzero(np.all(np.sort(pairs, axis=1) == [a, b], axis=1))
            original = IndexedStreamlines(Path(bundle["source"]), cancelled=cancelled)
            out = IndexedStreamlines(folder / row["outputs"]["tracts"], cancelled=cancelled)
            try:

                def signature(line):
                    return hashlib.sha256(np.asarray(line, dtype="<f4").tobytes()).hexdigest()

                if len(out) != len(indices) or not len(out):
                    raise ValueError("Extracted count differs from exact assignment records")
                if Counter(signature(original[int(i)]) for i in indices) != Counter(
                    signature(out[i]) for i in range(len(out))
                ):
                    raise ValueError(
                        "Extracted coordinates differ from original selected streamlines"
                    )
                measurement = dict(
                    task=row["task"],
                    selected_pair=[a, b],
                    streamlines=len(out),
                    coordinate_identity="exact Float32 multiset, duplicate multiplicity retained",
                )
                if "weights" in row["outputs"]:
                    from .sift2 import read_weights

                    factors = read_weights(bundle["weights"], len(original))
                    subset = read_weights(folder / row["outputs"]["weights"], len(out))
                    # Native extraction emits source order. Verify every exact
                    # coordinate and associated factor, including duplicate paths
                    # carrying different weights; a geometry multiset alone is insufficient.
                    if not all(
                        np.array_equal(original[int(i)], out[j]) for j, i in enumerate(indices)
                    ):
                        raise ValueError(
                            "Native subset ordering differs from exact source assignment indices"
                        )
                    if not np.allclose(subset, factors[indices].astype("f4"), atol=2e-6, rtol=2e-5):
                        raise ValueError(
                            "Extracted weight factors do not correspond to extracted streamlines"
                        )
                    np.savetxt(folder / (row["task"] + "-source-indices.txt"), indices, fmt="%d")
                    measurement.update(
                        weight_count=len(subset),
                        weight_sum=float(subset.sum()),
                        weight_order="Native source order; each coordinate/factor pair independently checked",
                    )
            finally:
                original.close()
                out.close()
            from ..atlas import geometry_record
            from ..scene import ImageVolume, save_new_image
            from .dti import _aligned

            reference_image = ImageVolume(ref)
            labels = _aligned(Path(bundle["atlas"]), geometry_record(reference_image))
            regions = []
            for node in sorted({a, b}):
                target = folder / row["outputs"]["roi_" + str(node)]
                save_new_image(target, (labels == node).astype(np.uint8), reference_image)
                regions.append(
                    dict(
                        path=str(target),
                        sha256=hash_file(target),
                        parcel_id=node,
                        label=bundle["nodes"][node - 1]["label"],
                    )
                )
            path = folder / row["outputs"]["tracts"]
            products.append(
                dict(
                    kind="tractogram",
                    path=str(path),
                    label=path.name,
                    sha256=hash_file(path),
                    reference=str(ref),
                    reference_sha256=hash_file(ref),
                    selected_regions=regions,
                    **bundle["declarations"],
                )
            )
            if "weights" in row["outputs"]:
                from .imports import import_artifact
                from .sift2 import weight_statistics

                subset_tract = import_artifact(
                    ImportRequest("tractogram", path, declarations=bundle["declarations"]),
                    cancelled=cancelled,
                )
                record = dict(bundle["weight_record"])
                record.update(
                    tractogram=str(path),
                    tractogram_sha256=hash_file(path),
                    tractogram_id=subset_tract.id,
                    tractogram_request=subset_tract.to_dict()["request"],
                    streamlines=len(subset),
                    weights=str(folder / row["outputs"]["weights"]),
                    weights_sha256=hash_file(folder / row["outputs"]["weights"]),
                    statistics=weight_statistics(subset),
                    reference=str(ref),
                    reference_sha256=hash_file(ref),
                    source_weight_record=bundle["weight_record"],
                    subset_source_indices=str(folder / (row["task"] + "-source-indices.txt")),
                    optimization_scope="Weights estimated on original full tractogram; this subset was not reoptimized",
                )
                sidecar = folder / row["outputs"]["weight_record"]
                record["record_path"] = str(sidecar)
                _publish_json(sidecar, record)
                products[-1]["weight_record"] = str(sidecar)
        else:
            size = len(row["nodes"])
            valid_ids = {n["parcel_id"] for n in row["nodes"] if n["nonempty"]}
            n = row["streamlines"]
            allpairs = {
                m: assignments(folder / name, n, valid_ids, cancelled)
                for m, name in row["assignment_outputs"].items()
            }
            pairs = allpairs["count"]
            if not all(np.array_equal(pairs, a) for a in allpairs.values()):
                raise ValueError("Assignment ordering differs between metrics")
            values = {"count": np.ones(n), "mean_length": _track_values(row["source"], cancelled)}
            factors = None
            if "weight_record" in row:
                from .sift2 import read_weights, verify_weight_binding

                verify_weight_binding(row["inputs"]["weights"], row["inputs"]["tracts"], cancelled)
                factors = read_weights(row["inputs"]["weights"]["path"], n)
            fa_qc = {}
            if "fa_values" in row:
                values["mean_fa"] = np.loadtxt(folder / row["fa_values"], ndmin=1)
                if values["mean_fa"].shape != (n,) or not np.isfinite(values["mean_fa"]).all():
                    raise ValueError(
                        "FA sample count/nonfinite mismatch; no streamlines silently discarded"
                    )
                index = IndexedStreamlines(Path(row["source"]), cancelled=cancelled)
                expected_vertex_counts = list(index.counts)
                raw_means = []
                invalid = nonphysical = vertex_count = 0
                affected = []
                try:
                    with (folder / row["fa_vertices"]).open() as f:
                        for line in f:
                            check_cancel(cancelled)
                            if not line.strip() or line.lstrip().startswith("#"):
                                continue
                            arr = np.fromstring(line, sep=" ")
                            if (
                                len(raw_means) >= n
                                or len(arr) != expected_vertex_counts[len(raw_means)]
                            ):
                                raise ValueError(
                                    "FA sample vertex counts differ from exact source streamline ordering"
                                )
                            vertex_count += len(arr)
                            invalid += int((~np.isfinite(arr)).sum())
                            bad = int(((arr < 0) | (arr > 1)).sum())
                            nonphysical += bad
                            raw_means.append(streamline_sample_mean(index[len(raw_means)], arr))
                            if bad:
                                affected.append(len(raw_means) - 1)
                finally:
                    index.close()
                if (
                    len(raw_means) != n
                    or invalid
                    or not np.allclose(raw_means, values["mean_fa"], atol=2e-6, rtol=2e-5)
                ):
                    raise ValueError(
                        "Per-vertex FA evidence does not verify the native streamline means"
                    )
                fa_qc = dict(
                    vertices_sampled=vertex_count,
                    nonfinite_samples=invalid,
                    nonphysical_vertex_samples=nonphysical,
                    affected_streamline_indices=affected,
                    nonphysical_streamline_means=int(
                        ((values["mean_fa"] < 0) | (values["mean_fa"] > 1)).sum()
                    ),
                    interpolation="MRtrix native trilinear at saved vertices",
                    per_streamline_statistic="Arc-length-weighted trapezoidal mean (native tcksample -stat_tck mean)",
                    independent_sample_mean_max_abs_difference=float(
                        np.max(np.abs(np.asarray(raw_means) - values["mean_fa"]))
                    ),
                    sample_mean_tolerance=dict(
                        absolute=2e-6,
                        relative=2e-5,
                        reason="Native Float32 accumulation and six-significant-digit ASCII vertex evidence",
                    ),
                    exclusion="none; values unchanged",
                )
                if nonphysical:
                    findings.append(
                        f"{nonphysical} sampled FA values are outside [0,1]; preserved without clipping or exclusions"
                    )
            symmetric = p["symmetric"] == "yes"
            zero = p["zero_diagonal"] == "yes"
            matrices = {}
            deltas = {}
            for metric, name in row["matrices"].items():
                base = METRICS[metric].get("base_metric", metric)
                weighted = METRICS[metric].get("weighted", False)
                matrix = read_matrix(folder / name, allow_negative=base == "mean_fa")
                if matrix.shape != (size, size):
                    raise ValueError("Matrix dimension differs from maximum original node ID")
                expected, occupancy = aggregate(
                    pairs,
                    values[base],
                    size,
                    symmetric,
                    zero,
                    mean=base != "count",
                    weights=factors if weighted else None,
                )
                delta = float(np.max(np.abs(expected - matrix)))
                atol = 0 if metric == "count" else 1e-3 if base == "mean_length" else 2e-6
                rtol = 0 if metric == "count" else 2e-5
                if not np.allclose(matrix, expected, atol=atol, rtol=rtol):
                    raise ValueError(
                        f"Independent assignment aggregation mismatch for {metric}: {delta}"
                    )
                deltas[metric] = dict(
                    maximum_absolute_difference=delta,
                    absolute_tolerance=atol,
                    relative_tolerance=rtol,
                )
                matrices[metric] = dict(path=str(folder / name), **METRICS[metric])
            weight_sum = None
            if factors is not None:
                weight_sum, _ = aggregate(pairs, np.ones(n), size, symmetric, zero, weights=factors)
                sum_path = folder / (row["task"] + "-weight-sum.csv")
                valid_path = folder / (row["task"] + "-weight-validity.csv")
                np.savetxt(sum_path, weight_sum, delimiter=",", fmt="%.17g")
                np.savetxt(valid_path, weight_sum > 0, delimiter=",", fmt="%d")
                for metric, entry in matrices.items():
                    if METRICS[metric].get("weighted"):
                        entry.update(
                            weight_sum=str(sum_path),
                            validity=str(valid_path),
                            empty_edge_policy="Native zero retained; weighted mean undefined when validity=0; occupancy counts remain distinct",
                        )
            canonical = np.sort(pairs, axis=1)
            both = (canonical > 0).all(axis=1)
            selfcount = int(((pairs[:, 0] == pairs[:, 1]) & both).sum())
            off = np.triu(occupancy, 1) > 0
            connected = (off | off.T).any(axis=0)
            nonempty = np.array([x["nonempty"] for x in row["nodes"]])
            measurement = dict(
                task=row["task"],
                total_streamlines=n,
                assigned_streamlines=int(both.sum()),
                unassigned_streamlines=int((~both).sum()),
                partially_assigned_streamlines=int(((pairs > 0).sum(axis=1) == 1).sum()),
                assignment_success_percent=100 * float(both.mean()),
                invalid_assignments=0,
                matrix_contributing_streamlines=int(np.triu(occupancy).sum()),
                assigned_but_diagonal_excluded=selfcount if zero else 0,
                node_count=size,
                atlas_identity=row["inputs"]["nodes"]["id"],
                duplicate_display_labels=[
                    name for name, c in Counter(n["label"] for n in row["nodes"]).items() if c > 1
                ],
                nonempty_parcels=int(nonempty.sum()),
                missing_node_ids=[x["parcel_id"] for x in row["nodes"] if not x["nonempty"]],
                connected_nodes=int(connected.sum()),
                connected_node_definition="At least one off-diagonal occupied edge",
                isolated_nonempty_nodes=int((nonempty & ~connected).sum()),
                represented_edges=int(off.sum()),
                edge_density=float(off.sum() / (size * (size - 1) / 2)) if size > 1 else 0,
                self_connections_before_diagonal_policy=selfcount,
                symmetric_policy=symmetric,
                zero_diagonal_policy=zero,
                matrix_shape=[size, size],
                matrix_distributions={
                    m: dict(
                        minimum=float(read_matrix(v["path"], allow_invalid=True).min()),
                        maximum=float(read_matrix(v["path"], allow_invalid=True).max()),
                        nonzero_values=int(
                            np.count_nonzero(read_matrix(v["path"], allow_invalid=True))
                        ),
                        units=v["units"],
                    )
                    for m, v in matrices.items()
                },
                aggregation_verification=deltas,
                fa_sampling=fa_qc,
                assignment=p["assignment"],
                assignment_parameters={
                    k: v for k, v in p.items() if k in ("radial_search_mm", "search_mm")
                },
                endpoint_correspondence="Native assignment records; two positive endpoints required; no unassigned endpoint counted as complete",
                registration_eligibility=row.get("registration_eligibility"),
                scientific_review="required",
            )
            if factors is not None:
                measurement["sift2"] = dict(
                    weights=row["inputs"]["weights"]["path"],
                    source_tractogram_sha256=row["weight_record"]["tractogram_sha256"],
                    mu=row["weight_record"].get("mu"),
                    application_mu_scaling=False,
                    total_input_weight=float(factors.sum()),
                    contributing_edge_weight=float(np.triu(weight_sum).sum()),
                    positive_weight_edges=int(np.count_nonzero(np.triu(weight_sum, 1))),
                    zero_total_weight_occupied_edges=int(
                        ((np.triu(occupancy) > 0) & (weight_sum == 0)).sum()
                    ),
                    assignment_policy_unchanged=True,
                )
            if int(np.triu(occupancy).sum()) != int(both.sum()) - (selfcount if zero else 0):
                raise ValueError("Assigned/contributing count accounting mismatch")
            if fa_qc:
                bad = np.zeros(n)
                bad[fa_qc["affected_streamline_indices"]] = 1
                affected_edges, _ = aggregate(pairs, bad, size, False, zero)
                fa_qc["affected_edges"] = [
                    dict(
                        source_node=int(a + 1),
                        target_node=int(b + 1),
                        affected_streamlines=int(affected_edges[a, b]),
                    )
                    for a, b in np.argwhere(affected_edges > 0)
                ]
                source_fa = row["inputs"]["fa"]
                fa_qc["input_FA_minimum"] = source_fa["verification"]["minimum"]
                fa_qc["input_FA_maximum"] = source_fa["verification"]["maximum"]
                if (
                    source_fa["verification"]["minimum"] < 0
                    or source_fa["verification"]["maximum"] > 1
                ):
                    findings.append(
                        "Source FA includes nonphysical values; unchanged. Vertex-level and affected-edge influence are reported separately."
                    )
            if not both.all():
                findings.append(
                    f"{row['task']}: {int((~both).sum())}/{n} streamlines lack a valid node pair; no radius inflation or synthetic edges"
                )
            _publish_json(folder / (row["task"] + "-nodes.json"), row["nodes"])
            occpath = folder / (row["task"] + "-occupancy.csv")
            np.savetxt(occpath, occupancy, fmt="%d", delimiter=",")
            bound = row["inputs"]
            deps = {**plan["extra_input_hashes"]}
            # Bundle binds matrices/occupancy/assignments and exact source ordering.
            for name in [
                *row["matrices"].values(),
                *row["assignment_outputs"].values(),
                row["outputs"]["reference"],
                row["task"] + "-nodes.json",
                occpath.name,
                *([row["fa_values"], row["fa_vertices"]] if "fa_values" in row else []),
                *(
                    [row["task"] + "-weight-sum.csv", row["task"] + "-weight-validity.csv"]
                    if factors is not None
                    else []
                ),
            ]:
                deps[str(folder / name)] = hash_file(folder / name, cancelled)
            bundle = dict(
                schema="tractosphere.connectome/1",
                declarations=dict(bound["tracts"]["declarations"]),
                source=row["source"],
                atlas=row["atlas"],
                reference=str(ref),
                nodes=row["nodes"],
                matrices=matrices,
                occupancy=str(occpath),
                assignments=str(folder / row["assignment_outputs"]["count"]),
                registration_context=dict(atlas=bound["nodes"], reference=bound["reference"]),
                registration_eligibility=row.get("registration_eligibility"),
                assignment_parameters=p,
                qc=measurement,
                hashes=deps,
            )
            if factors is not None:
                bundle.update(
                    weights=row["inputs"]["weights"]["path"],
                    weight_record=row["weight_record"],
                    weight_sum=str(sum_path),
                    weight_validity=str(valid_path),
                )
            bundlepath = folder / row["outputs"]["connectome"]
            _publish_json(bundlepath, bundle)
            products.append(
                dict(
                    kind="connectome",
                    path=str(bundlepath),
                    label=bundlepath.name,
                    sha256=hash_file(bundlepath),
                    **bundle["declarations"],
                )
            )
        measurements.append(measurement)
    result = dict(
        execution="completed",
        numerical_verification="passed",
        automated_qc="completed_with_findings",
        scientific_review="required",
        scientific_acceptance="unassigned",
        findings=findings,
        measurements=measurements,
        products=products,
    )
    _publish_json(folder / "qc-metrics.json", result)
    report = '<!doctype html><meta charset="utf-8"><title>Connectome scientific QC</title><style>body{font:15px/1.5 system-ui;margin:40px;max-width:1100px}pre{white-space:pre-wrap}td,th{padding:6px;text-align:left}</style><h1>Connectome QC</h1><p>Execution completed · Numerical checks passed · Scientific review required · Acceptance unassigned</p>'
    for m in measurements:
        report += (
            "<h2>"
            + html.escape(m["task"])
            + "</h2><table>"
            + "".join(
                "<tr><th>"
                + html.escape(k.replace("_", " "))
                + "</th><td>"
                + html.escape(str(v))
                + "</td></tr>"
                for k, v in m.items()
                if not isinstance(v, (dict, list))
            )
            + "</table><details><summary>Numerical details</summary><pre>"
            + html.escape(json.dumps(m, indent=2))
            + "</pre></details>"
        )
    report += (
        "<h2>Scientific limitations</h2><ul>"
        + "".join("<li>" + html.escape(x) + "</li>" for x in findings)
        + "</ul><details><summary>Exact commands</summary><pre>"
        + html.escape("\n".join(c["display"] for c in plan["commands"]))
        + "</pre></details>"
    )
    with (folder / "qc-report.html").open("x") as f:
        f.write(report)
    _publish_json(
        folder / "qc-catalog.json",
        dict(
            schema_version=1,
            kind="dmri_qc_catalog",
            producer="TractoSphere Stage 5",
            identity=dict(subject=products[0].get("subject_id", "unknown")),
            stages=[
                dict(
                    id="connectomics",
                    title="Structural connectivity",
                    execution="completed",
                    technical="passed",
                    automated_qc="completed_with_findings",
                    registration_eligibility=row.get("registration_eligibility"),
                    scientific_review="required",
                    findings=findings,
                    artifacts=[
                        dict(
                            kind="report",
                            label="Connectome scientific QC",
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
        ),
    )
    return result


class ConnectomeAdapter:
    """Use existing immutable artifact execution, process monitoring and cancellation."""

    def compile(
        self, task: Task, inputs: dict[str, Artifact], output_directory: Path
    ) -> ExecutionPlan:
        """Compile the same independent task contract used by CLI and GUI."""
        return prepare_connectome(tuple(inputs.values()), (task,), output_directory)

    def validate(self, task: Task, inputs: dict[str, Artifact]) -> tuple[dict[str, Any], ...]:
        """Inspect typed compatibility without launching a scientific command."""
        return tuple(build_workflow(tuple(inputs.values()), (task,)).to_dict()["issues"])

    def qc(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """Return preserved verification and independent scientific-review findings."""
        return manifest["validation"]

    def run(
        self,
        plan: ExecutionPlan,
        *,
        approval: str,
        cancelled: Callable[[], bool] = lambda: False,
        progress: Callable[[str], None] = lambda _: None,
    ) -> dict[str, Any]:
        data = plan.to_dict()
        folder = Path(data["output_directory"])
        artifacts = tuple(Artifact(a) for a in data["artifacts"])
        tasks = tuple(Task.from_dict(t) for t in data["tasks"])

        def sources():
            for a in artifacts:
                a.assert_current()
            for t in data["tools"].values():
                if hash_file(Path(t["path"])) != t["sha256"]:
                    raise ValueError("Connectome executable changed")

        def stepcheck(c):
            for name in c["outputs"]:
                path = folder / name
                if not path.is_file() or not path.stat().st_size:
                    raise ValueError("Missing/incomplete connectome output: " + name)
            return dict(status="produced; full scientific checks required before publication")

        return execute_artifact_task(
            plan,
            approval=approval,
            recompile=lambda: prepare_connectome(
                artifacts,
                tasks,
                folder,
                nthreads=data["parameters"]["nthreads"],
                tool_directory=Path(next(iter(data["tools"].values()))["path"]).parent,
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
            verify_step=stepcheck,
            verify=lambda _: verify_connectome(folder, data, cancelled, progress),
            verify_sources=sources,
            cancelled=cancelled,
            progress=progress,
        )
