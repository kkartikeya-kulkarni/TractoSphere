"""Native SIFT2 over the shared artifact executor, never a custom optimizer.

Python/CLI/GUI compile prepare_sift2(), review exact argv and approve its digest
with execute_plan(). A weight sidecar binds the exact ordered tractogram bytes.
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
import shlex
import shutil
from pathlib import Path

import numpy as np
import psutil

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
from .contracts import Artifact, ImportRequest
from .csd import _help
from .dti import _fresh, _revision
from .planning import Task, build_workflow
from .records import read_json, save_selection
from .tracking import TrackingStep, _association

LIMITS = [
    "Processing completion, numerical validity, optimizer stopping, scientific QC and scientific acceptance are distinct; acceptance remains unassigned.",
    "SIFT2 is sensitive to tractogram coverage, FOD quality/normalization and segmentation. Small tractograms are diagnostic only; even 1M tracks do not establish research-grade quantitative reliability.",
    "Weight factors are neither axon counts nor optimizer coefficients. Mu is preserved separately; no additional mu scaling is applied to weights or connectomes.",
    "Weighting does not repair streamline geometry, registration, atlas coverage or missing endpoint assignments. Cross-subject comparison requires additional scientific controls.",
    "Source coordinates, FOD coefficients, tissue fractions and tractogram ordering remain unchanged. Filtered/reordered/regenerated tracks require their own exact weight association.",
]


def read_weights(path, count=None):
    """Bound complete vector parsing; zeros valid, no clipping/padding/reordering."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 32 * 1024**2:
        raise ValueError("Weight vector missing or exceeds the 32 MiB import budget")
    if path.stat().st_size == 0:
        raise ValueError("Expected one weight row or column; empty vector")
    values = np.loadtxt(path, ndmin=2)
    if min(values.shape) != 1 or not values.size or values.size > 1_000_000:
        raise ValueError("Expected one weight row or column and at most one million factors")
    # MRtrix save_vector writes a single whitespace-separated row. Vertical
    # vectors are also valid. Flatten only an authenticated 1-D shape, in order.
    values = values.reshape(-1)
    if count is not None and len(values) != count:
        raise ValueError(
            "Weight count differs from the exact source tractogram; no padding or truncation"
        )
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Negative or nonfinite weight factors; scientific output was not repaired")
    return values


def weight_statistics(values):
    """Measured statistics with a descriptive, explicitly non-acceptance outlier flag."""
    a = np.asarray(values, dtype="f8")
    positive = a[a > 0]
    median_positive = float(np.median(positive)) if len(positive) else 0.0
    return dict(
        count=len(a),
        minimum=float(a.min()),
        maximum=float(a.max()),
        mean=float(a.mean()),
        median=float(np.median(a)),
        sum=float(a.sum()),
        percentiles=dict(
            zip(
                ("p0", "p1", "p5", "p25", "p50", "p75", "p95", "p99", "p100"),
                np.percentile(a, [0, 1, 5, 25, 50, 75, 95, 99, 100]).tolist(),
                strict=True,
            )
        ),
        zero_count=int((a == 0).sum()),
        zero_percent=100 * float((a == 0).mean()),
        invalid_count=0,
        extreme_count=int((a > 1000 * median_positive).sum()) if median_positive else 0,
        extreme_definition="Factor >1000 times the positive-weight median; descriptive review flag only",
    )


def convergence(path, parameters):
    """Inspect native 3.0.5 CSV without converting a stopping event into acceptance."""
    if Path(path).stat().st_size > 32 * 1024**2:
        raise ValueError("Oversized SIFT2 convergence evidence")
    with Path(path).open() as f:
        reader = csv.DictReader(f)
        required = {
            "Iteration",
            "Cost_data",
            "Cost_reg",
            "Cost_total",
            "Streamlines",
            "Fixels_excluded",
        }
        if not required <= set(reader.fieldnames or []):
            raise ValueError("Missing native SIFT2 convergence columns")
        rows = [{k: float(row[k]) for k in required} for row in reader]
    if not rows or len(rows) > 10002 or not all(np.isfinite(list(r.values())).all() for r in rows):
        raise ValueError("Empty/nonfinite/oversized convergence records")
    its = [int(r["Iteration"]) for r in rows]
    if its != list(range(len(rows))) or any(r["Cost_data"] < 0 or r["Cost_reg"] < 0 for r in rows):
        raise ValueError("Invalid iteration ordering or cost evidence")
    cap = parameters.get("max_iters", 1000)
    reached = its[-1] >= cap
    changes = np.diff([r["Cost_total"] for r in rows])
    return dict(
        iterations=its[-1],
        native_max_iterations=cap,
        iteration_cap_reached=reached,
        stopping_status="iteration cap reached; convergence not established"
        if reached
        else "native optimizer stopped before cap; inspect cost history",
        convergence="requires_review",
        initial=rows[0],
        final=rows[-1],
        total_cost_increase_iterations=int((changes > 0).sum()),
        relative_total_cost_reduction=(rows[0]["Cost_total"] - rows[-1]["Cost_total"])
        / rows[0]["Cost_total"]
        if rows[0]["Cost_total"]
        else None,
        native_csv=str(path),
        scientific_acceptance="unassigned",
    )


def verify_weight_binding(weight, tract, cancelled=lambda: False):
    """Authenticate the immutable sidecar and exact source/order, not just filename/count."""
    w = weight.to_dict() if isinstance(weight, Artifact) else weight
    t = tract.to_dict() if isinstance(tract, Artifact) else tract
    decl = w["declarations"]
    sidecar = Path(decl.get("weight_record", ""))
    if not sidecar.is_file() or sidecar.stat().st_size > 1024**2:
        raise ValueError(
            "Weights require a hash-bound weight_record for the exact tractogram/order"
        )
    record = read_json(sidecar.read_text())
    th = next(s["sha256"] for s in t["sources"] if s["path"] == t["path"])
    wh = next(s["sha256"] for s in w["sources"] if s["path"] == w["path"])
    if (
        record.get("schema") != "tractosphere.streamline-weights/1"
        or record.get("tractogram_sha256") != th
        or record.get("weights_sha256") != wh
        or record.get("streamlines") != t["metadata"]["streamlines"]
        or record.get("ordering") != "exact source file streamline order"
        or record.get("representation") != "weight_factors"
        or decl.get("tractogram_id") != t["id"]
    ):
        raise ValueError("Weight factors do not identify this exact source tractogram and ordering")
    if (
        hash_file(Path(record["tractogram"]), cancelled) != th
        or hash_file(Path(record["weights"]), cancelled) != wh
    ):
        raise ValueError("Weight sidecar source/output changed")
    read_weights(w["path"], t["metadata"]["streamlines"])
    return record


def prepare_sift2(
    artifacts,
    tasks,
    output_directory,
    *,
    nthreads=2,
    tool_directory=None,
    cancelled=lambda: False,
    progress=lambda _: None,
):
    """Compile one reusable SIFT2 task with explicit geometry/provenance/mask policy."""
    if len(tasks) != 1 or tasks[0].algorithm != "tcksift2":
        raise ValueError("Select exactly one native SIFT2 task per immutable attempt")
    if isinstance(nthreads, bool) or not isinstance(nthreads, int) or not 1 <= nthreads <= 64:
        raise ValueError("Threads must be an integer in 1..64")
    task = tasks[0]
    if any(not isinstance(v, str) for v in task.inputs.values()):
        raise ValueError(
            "Import completed verified sources before SIFT2; no implicit upstream execution"
        )
    artifacts = _fresh(
        tuple(a for a in artifacts if a.id in task.inputs.values()), cancelled, progress
    )
    proposal = build_workflow(artifacts, tasks, verify_sources=False).to_dict()
    errors = [i["message"] for i in proposal["issues"] if i["severity"] == "error"]
    if errors:
        raise ValueError("; ".join(errors))
    inventory = {a.id: a.to_dict() for a in artifacts}
    bound = {k: inventory[v] for k, v in task.inputs.items()}
    _association(bound)
    p = dict(task.parameters)
    if (p["act"] == "on") != ("five_tt" in bound):
        raise ValueError("ACT mask policy must explicitly match the selected 5TT")
    if p["act"] == "on" and "proc_mask" in bound:
        raise ValueError(
            "Select either ACT-derived processing mask or explicit proc_mask, not both"
        )
    if p["fd_scale_gm"] == "on" and (
        p["act"] != "on"
        or bound["fod"]["declarations"].get("reconstruction") != "single_tissue_csd"
    ):
        raise ValueError(
            "fd_scale_gm requires ACT and an explicitly established single-tissue CSD history; never automatic for MSMT"
        )
    for low, high in (("min_factor", "min_coeff"), ("max_factor", "max_coeff")):
        if low in p and high in p:
            raise ValueError(f"{low} and {high} are mutually exclusive")
    if p.get("min_iters", 10) > p.get("max_iters", 1000):
        raise ValueError("Minimum iterations exceed maximum iterations")
    fod = bound["fod"]
    decl = fod["declarations"]
    if decl.get("normalization") not in {"completed", "not_done"} or decl.get("tissue") != "wm":
        raise ValueError("Explicit WM FOD and normalization history required")
    geo = fod["geometry"]
    for role in ("reference", "five_tt", "proc_mask"):
        if role in bound:
            g = bound[role]["geometry"]
            voxel_mapping(
                np.asarray(geo["affine_mm"]), geo["shape"], np.asarray(g["affine_mm"]), g["shape"]
            )
    count = bound["tracts"]["metadata"]["streamlines"]
    if not 1 <= count <= 1_000_000 or not bound["tracts"]["path"].lower().endswith(".tck"):
        raise ValueError("Native TCK with 1..1,000,000 fully validated streamlines required")
    index = IndexedStreamlines(bound["tracts"]["path"], cancelled=cancelled)
    try:
        source = str(index.header.get("source", ""))
        expected = next(s["sha256"] for s in fod["sources"] if s["path"] == fod["path"])
        declared = bound["tracts"]["declarations"].get("sift2_fod_sha256")
        if (
            not (
                source and Path(source).is_file() and hash_file(Path(source), cancelled) == expected
            )
            and declared != expected
        ):
            raise ValueError(
                "Tractogram source FOD association unresolved: inspect native source history or declare verified sift2_fod_sha256"
            )
        corners = np.array(
            list(__import__("itertools").product(*zip(index.lower, index.upper, strict=True)))
        )
        inverse = np.linalg.inv(np.asarray(geo["affine_mm"]))
        vox = corners @ inverse[:3, :3].T + inverse[:3, 3]
        if (vox.max(axis=0) < -0.5).any() or (
            vox.min(axis=0) >= np.asarray(geo["shape"][:3]) - 0.5
        ).any():
            raise ValueError("Tractogram physical extent does not overlap the selected FOD")
    finally:
        index.close()
    sources = {s["path"]: s["sha256"] for a in artifacts for s in a.payload["sources"]}
    if "five_tt" in bound:
        from .anatomy import alignment_review, tissue_statistics

        _, evidence = alignment_review(bound["five_tt"], bound["reference"], cancelled)
        sources.update(evidence)
        tissue_statistics(bound["five_tt"]["path"], cancelled)
    folder = Path(output_directory).expanduser().absolute()
    if folder.exists() or folder.is_symlink() or not folder.parent.is_dir():
        raise ValueError("Choose a NEW SIFT2 attempt directory with an existing parent")
    folder = folder.resolve()
    if any(folder.is_relative_to(Path(s).resolve().parent) for s in sources):
        raise ValueError("Output must be separate from original scientific input directories")
    require_local_processing([folder, *sources])
    estimate = count * 128 + 64 * 1024**2
    if shutil.disk_usage(folder.parent).free < estimate + 5 * 1024**3:
        raise ValueError("Insufficient storage for SIFT2 output plus 5 GiB margin")
    # Engineering preflight, not a promise about native peak allocation. Fixel
    # mappings depend on tract length/coverage; monitor actual RSS during runs.
    memory_estimate = (
        2 * Path(fod["path"]).stat().st_size
        + Path(bound["tracts"]["path"]).stat().st_size // 2
        + count * 192
        + 256 * 1024**2
    )
    memory_margin = 512 * 1024**2
    if psutil.virtual_memory().available < memory_estimate + memory_margin:
        raise ValueError(
            f"Insufficient available memory: estimated SIFT2 working set {memory_estimate} bytes "
            f"plus {memory_margin} bytes reserve; close other workloads before retrying"
        )
    tools = detect_mrtrix(
        names=("tcksift2", "tckinfo", *(("5ttcheck",) if "five_tt" in bound else ())),
        tool_directory=tool_directory,
        cancelled=cancelled,
    )
    if any(t["version"] != "3.0.5" for t in tools.values()):
        raise ValueError("SIFT2 adapter requires verified MRtrix3 3.0.5")
    env = _environment_contract(cancelled)
    env["unset"] = ["MRTRIX_QUIET", "MRTRIX_OVERWRITE"]
    env["overrides"]["PATH"] = (
        str(Path(tools["tcksift2"]["path"]).parent) + os.pathsep + os.environ.get("PATH", "")
    )
    names = dict(
        weights=task.id + "-weights.txt",
        mu=task.id + "-mu.txt",
        csv=task.id + "-convergence.csv",
        record=task.id + "-weights.json",
    )
    commands = []

    def command(step, tool, args, outputs):
        helptext = _help(tools[tool]["path"])
        for arg in map(str, args):
            if arg.startswith("-") and not re.fullmatch(r"-[0-9.].*", arg) and arg not in helptext:
                raise ValueError(f"Installed {tool} does not support {arg}")
        argv = [tools[tool]["path"], *map(str, args), "-nthreads", str(nthreads)]
        commands.append(
            dict(
                step=step,
                argv=argv,
                display=shlex.join(argv),
                inputs=list(sources),
                outputs=outputs,
            )
        )

    command(task.id + "-count", "tckinfo", [bound["tracts"]["path"], "-count"], [])
    if "five_tt" in bound:
        command(task.id + "-5ttcheck", "5ttcheck", [bound["five_tt"]["path"]], [])
    args = [
        bound["tracts"]["path"],
        fod["path"],
        folder / names["weights"],
        "-out_mu",
        folder / names["mu"],
        "-csv",
        folder / names["csv"],
        "-info",
    ]
    for role, flag in (("five_tt", "-act"), ("proc_mask", "-proc_mask")):
        if role in bound:
            args += [flag, bound[role]["path"]]
    if p["fd_scale_gm"] == "on":
        args += ["-fd_scale_gm"]
    for key, value in p.items():
        if key not in {"act", "fd_scale_gm"}:
            args += ["-" + key, value]
    command(task.id, "tcksift2", args, [names[k] for k in ("weights", "mu", "csv")])
    files = [
        Path(__file__),
        Path(__file__).with_name("sift2_registry.py"),
        Path(__file__).parents[1] / "execution.py",
    ]
    return ExecutionPlan(
        dict(
            kind="mrtrix_sift2_execution_plan",
            schema_version=1,
            artifacts=[a.to_dict() for a in artifacts],
            tasks=[task.to_dict()],
            output_directory=str(folder),
            tools=tools,
            environment=env,
            commands=commands,
            binding=bound,
            files=names,
            outputs=[
                *names.values(),
                "scientific-selection.json",
                "qc-metrics.json",
                "qc-report.html",
                "qc-catalog.json",
            ],
            parameters=dict(
                nthreads=nthreads,
                settings=p,
                estimated_output_bytes=estimate,
                storage_margin_bytes=5 * 1024**3,
                estimated_working_memory_bytes=memory_estimate,
                memory_margin_bytes=memory_margin,
                memory_estimate_method="2× FOD file + 0.5× TCK file + 192 bytes/track + 256 MiB; engineering estimate, monitor actual native RSS",
            ),
            scientific_limits=LIMITS,
            extra_input_hashes=sources,
            build=_revision(),
            implementation_hashes={str(f): hash_file(f, cancelled) for f in files},
        )
    )


def verify_sift2(folder, plan, cancelled=lambda: False):
    """Inspect real native factors, mu, iteration evidence and preserved source ordering."""
    bound, names = plan["binding"], plan["files"]
    n = bound["tracts"]["metadata"]["streamlines"]
    values = read_weights(folder / names["weights"], n)
    mu = np.loadtxt(folder / names["mu"], ndmin=1)
    if mu.shape != (1,) or not np.isfinite(mu).all() or mu[0] <= 0:
        raise ValueError("SIFT2 mu must be one finite positive proportionality coefficient")
    conv = convergence(folder / names["csv"], plan["parameters"]["settings"])
    log = (folder / "logs" / (plan["tasks"][0]["id"] + ".log")).read_text(errors="replace")
    counts = (folder / "logs" / (plan["tasks"][0]["id"] + "-count.log")).read_text()
    if f"actual count in file: {n}" not in counts:
        raise ValueError("Native tckinfo does not confirm the exact streamline count")
    statistics = weight_statistics(values)
    findings = [*LIMITS, *[x for x in log.splitlines() if "[WARNING]" in x]]
    for pattern in (
        r".*fixels have no attributed streamlines.*",
        r".*fixels were tracked, but.*",
        r".*contribute .*initial cost function.*",
    ):
        findings.extend(re.findall(pattern, log))
    if n < 100000:
        findings.append(
            "DIAGNOSTIC / TEST-SCALE tractogram; quantitative SIFT2 reliability not established"
        )
    if statistics["sum"] == 0:
        raise ValueError("All factors are zero; no valid SIFT2 contribution estimate")
    if (
        conv["iteration_cap_reached"]
        or conv["total_cost_increase_iterations"]
        or statistics["extreme_count"]
    ):
        findings.append(
            "Optimizer/weight-distribution findings require scientific review; command completion is not convergence acceptance"
        )
    record = dict(
        schema="tractosphere.streamline-weights/1",
        representation="weight_factors",
        record_path=str(folder / names["record"]),
        ordering="exact source file streamline order",
        streamlines=n,
        tractogram_id=bound["tracts"]["id"],
        tractogram_request=bound["tracts"]["request"],
        declarations=bound["tracts"]["declarations"],
        reference=bound["reference"]["path"],
        reference_sha256=hash_file(Path(bound["reference"]["path"]), cancelled),
        settings=plan["parameters"]["settings"],
        tractogram=bound["tracts"]["path"],
        tractogram_sha256=hash_file(Path(bound["tracts"]["path"]), cancelled),
        weights=str(folder / names["weights"]),
        weights_sha256=hash_file(folder / names["weights"], cancelled),
        fod=bound["fod"]["path"],
        fod_sha256=hash_file(Path(bound["fod"]["path"]), cancelled),
        normalization=bound["fod"]["declarations"]["normalization"],
        mu=float(mu[0]),
        mu_applied_by_application=False,
        statistics=statistics,
        convergence=conv,
        findings=findings,
        scientific_acceptance="unassigned",
    )
    _publish_json(folder / names["record"], record)
    from .imports import import_artifact

    artifact = import_artifact(
        ImportRequest(
            "streamline_weights",
            folder / names["weights"],
            declarations={
                **bound["tracts"]["declarations"],
                "tractogram_id": bound["tracts"]["id"],
                "weight_record": str(folder / names["record"]),
            },
        ),
        cancelled=cancelled,
    )
    verify_weight_binding(artifact, bound["tracts"], cancelled)
    result = dict(
        execution="completed",
        numerical_verification="passed",
        automated_qc="completed_with_findings",
        scientific_review="required",
        scientific_acceptance="unassigned",
        measurements=record,
        findings=findings,
        products=[
            dict(
                kind="streamline_weights",
                path=str(folder / names["record"]),
                label="SIFT2 weight factors and QC",
                sha256=hash_file(folder / names["record"]),
                artifact=artifact.to_dict(),
            )
        ],
    )
    _publish_json(folder / "qc-metrics.json", result)
    report = (
        '<!doctype html><meta charset="utf-8"><title>SIFT2 scientific QC</title><style>body{font:15px/1.5 system-ui;margin:40px}pre{white-space:pre-wrap}</style><h1>SIFT2 QC</h1><p>Processing completed. Scientific QC requires review. Acceptance unassigned.</p><pre>'
        + html.escape(json.dumps(record, indent=2))
        + "</pre>"
    )
    (folder / "qc-report.html").write_text(report)
    _publish_json(
        folder / "qc-catalog.json",
        dict(
            schema_version=1,
            kind="dmri_qc_catalog",
            producer="TractoSphere Stage 6B",
            identity=dict(subject=bound["tracts"]["declarations"].get("subject_id")),
            stages=[
                dict(
                    id="sift2",
                    title="SIFT2 weighting",
                    execution="completed",
                    technical="passed",
                    automated_qc="completed_with_findings",
                    scientific_review="required",
                    findings=findings,
                    artifacts=[
                        dict(
                            kind="report",
                            label="SIFT2 scientific QC",
                            path="qc-report.html",
                            sha256=hash_file(folder / "qc-report.html"),
                        ),
                        dict(
                            kind="metrics",
                            label="Weight and optimization measurements",
                            path="qc-metrics.json",
                            sha256=hash_file(folder / "qc-metrics.json"),
                        ),
                    ],
                )
            ],
        ),
    )
    return result


class SIFT2Adapter:
    """Delegate all process ownership, approval, cancellation and publication to the existing engine."""

    def compile(self, task, inputs, output_directory):
        """Compile the same Python/CLI/GUI contract."""
        return prepare_sift2(tuple(inputs.values()), (task,), output_directory)

    def validate(self, task, inputs):
        """Inspect the typed DAG without executing an optimizer."""
        return tuple(build_workflow(tuple(inputs.values()), (task,)).to_dict()["issues"])

    def qc(self, manifest):
        """Return preserved scientific QC, never imply acceptance."""
        return manifest["validation"]

    def run(self, plan, *, approval, cancelled=lambda: False, progress=lambda _: None):
        """Execute immutable native steps using the shared cancellable runner."""
        data = plan.to_dict()
        folder = Path(data["output_directory"])
        artifacts = tuple(Artifact(a) for a in data["artifacts"])
        tasks = tuple(Task.from_dict(t) for t in data["tasks"])

        def sources():
            for path, digest in data["extra_input_hashes"].items():
                if hash_file(Path(path)) != digest:
                    raise ValueError("SIFT2 input or registration evidence changed")
            for tool in data["tools"].values():
                if hash_file(Path(tool["path"])) != tool["sha256"]:
                    raise ValueError("SIFT2 executable changed")

        def stepcheck(c):
            check_cancel(cancelled)
            for name in c["outputs"]:
                if not (folder / name).is_file() or not (folder / name).stat().st_size:
                    raise ValueError("Missing/incomplete SIFT2 output: " + name)
            return dict(status="command completed; final numerical verification still required")

        return execute_artifact_task(
            plan,
            approval=approval,
            recompile=lambda: prepare_sift2(
                artifacts,
                tasks,
                folder,
                nthreads=data["parameters"]["nthreads"],
                tool_directory=Path(data["tools"]["tcksift2"]["path"]).parent,
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
            verify=lambda _: verify_sift2(folder, data, cancelled),
            verify_sources=sources,
            cancelled=cancelled,
            progress=progress,
        )
