"""Approved batch recipes around existing scientific compilers and typed tasks.

Cross-adapter outputs are imported only after the producing engine manifest has
passed its existing validation and hashes. A deferred node is never reported as
scientifically eligible before those real outputs exist.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..execution import ExecutionPlan, hash_file
from ..runs import inspect_run
from ..scientific.contracts import ImportRequest
from ..scientific.imports import import_artifact
from ..scientific.planning import Task
from ..scientific.records import read_json, restore_selection
from .discovery import Study, associated_path, contained, identity

GIB = 1024**3
ADAPTERS = (
    "registration",
    "dti",
    "csd",
    "tracking",
    "anatomy",
    "sift2",
    "connectome",
    "preprocessing",
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def compiler(name):
    if name not in ADAPTERS:
        raise ValueError(f"No accepted batch adapter: {name}")
    from importlib import import_module

    return getattr(import_module("dmri_studio.core.scientific." + name), "prepare_" + name)


def workflow_order(workflow):
    if set(workflow) - {"schema", "nodes", "overrides", "description"}:
        raise ValueError("Unsupported workflow fields")
    nodes = workflow.get("nodes", [])
    if not nodes or len(nodes) > 128:
        raise ValueError("Workflow requires 1..128 nodes")
    ids = [identity(n["id"]) for n in nodes]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate workflow node ID")
    result, done = [], set()
    for node in nodes:
        if node.get("adapter") not in ADAPTERS:
            raise ValueError("Select an implemented scientific adapter")
        if set(node) - {
            "id",
            "adapter",
            "depends_on",
            "imports",
            "tasks",
            "selection_role",
            "reuse_role",
            "resources",
            "preprocessing",
        }:
            raise ValueError("Unsupported node fields; commands cannot be supplied by workflows")
        deps = node.get("depends_on", [])
        if not set(deps) <= set(ids):
            raise ValueError("Missing workflow dependency")
        for binding in node.get("imports", {}).values():
            allowed = {
                "role",
                "node",
                "output",
                "kind",
                "declarations",
                "declaration_roles",
                "provenance",
                "bval_role",
                "bvec_role",
                "sidecar_role",
            }
            if set(binding) - allowed or ("role" in binding) == ("node" in binding):
                raise ValueError(
                    "Import binding requires exactly one role or producer node and supported fields"
                )
            if "node" in binding and binding["node"] not in deps:
                raise ValueError("Output imports require an explicit dependency")
    while len(result) < len(nodes):
        ready = [n for n in nodes if n["id"] not in done and set(n.get("depends_on", [])) <= done]
        if not ready:
            raise ValueError("Workflow contains a dependency cycle")
        result.extend(ready)
        done.update(n["id"] for n in ready)
    return result


def requests(
    acquisition, node, root, completed=None, *, cancelled=lambda: False, progress=lambda _: None
):
    """Import real sources, retaining their exact scientific declarations."""
    completed = completed or {}
    if "selection_role" in node:
        path = contained(root, associated_path(acquisition, node["selection_role"], "selection"))
        saved = read_json(path.read_text())
        for artifact in saved["artifacts"]:
            for source in artifact["sources"]:
                contained(root, source["path"])
        artifacts, tasks = restore_selection(path, cancelled=cancelled, progress=progress)
    else:
        artifacts, aliases = [], {}
        for alias, binding in node.get("imports", {}).items():
            inherited = {}
            if "node" in binding:
                record = inspect_run(
                    Path(completed[binding["node"]]), cancelled=cancelled, progress=progress
                )
                if record["status"] != "validated_pending_qc" or not record["record"].get(
                    "inputs_unchanged"
                ):
                    raise ValueError("Dependency is not a completed verified scientific derivative")
                name = binding["output"]
                if name not in record["record"]["outputs"]:
                    raise ValueError("Requested dependency product is absent from verified outputs")
                path = Path(record["record"]["plan"]["output_directory"]) / name
                for product in record["record"].get("validation", {}).get("products", []):
                    a = product.get("artifact", {})
                    if a.get("path") == str(path) and a.get("kind") == binding["kind"]:
                        inherited = a.get("declarations", {})
            else:
                role = binding["role"]
                if role not in acquisition.paths:
                    raise ValueError(f"Missing {role} input for {node['id']}")
                path = contained(root, acquisition.paths[role])
            declarations = {
                **acquisition.declarations,
                **inherited,
                **binding.get("declarations", {}),
            }
            for declaration, role in binding.get("declaration_roles", {}).items():
                if declaration not in {
                    "reference_record",
                    "qc_association",
                    "space_review",
                    "registration_manifest",
                    "registration_qc",
                }:
                    raise ValueError("Unsupported spatial evidence declaration role")
                declarations[declaration] = str(
                    contained(root, associated_path(acquisition, role, declaration))
                )
            for key, value in (
                ("subject_id", acquisition.subject_id),
                ("session_id", acquisition.session_id),
            ):
                if declarations.get(key, value) != value:
                    raise ValueError("Declared subject/session conflicts with acquisition identity")
                declarations[key] = value
            if node["adapter"] in {"registration", "connectome", "tracking"}:
                if (
                    declarations.get("acquisition_id", acquisition.acquisition_id)
                    != acquisition.acquisition_id
                ):
                    raise ValueError("Registration acquisition identity conflicts with batch row")
                declarations["acquisition_id"] = acquisition.acquisition_id
            kwargs = {
                key: contained(root, acquisition.paths[binding[key + "_role"]])
                for key in ("bval", "bvec", "sidecar")
                if key + "_role" in binding
            }
            artifact = import_artifact(
                ImportRequest(
                    binding["kind"],
                    path,
                    declarations=declarations,
                    provenance=binding.get(
                        "provenance", "Explicit batch mapping; scientific acceptance unassigned."
                    ),
                    **kwargs,
                ),
                cancelled=cancelled,
                progress=progress,
            )
            artifacts.append(artifact)
            aliases[alias] = artifact.id
        tasks = []
        for task in node.get("tasks", []):
            task = read_json(json.dumps(task))
            for binding in task["inputs"].values():
                if "artifact_id" in binding:
                    binding["artifact_id"] = aliases[binding["artifact_id"]]
            tasks.append(Task.from_dict(task))
        artifacts, tasks = tuple(artifacts), tuple(tasks)
    for artifact in artifacts:
        dec = artifact.to_dict().get("declarations", {})
        if dec.get("subject_id") != acquisition.subject_id:
            raise ValueError("Scientific selection requires matching explicit subject_id")
        if dec.get("session_id", "") != acquisition.session_id:
            raise ValueError("Scientific selection session differs from acquisition")
    if not tasks and node["adapter"] != "preprocessing":
        raise ValueError("No scientific tasks selected")
    return artifacts, tasks


def verify_reuse(
    path, artifacts, tasks, *, threads=None, cancelled=lambda: False, progress=lambda _: None
):
    """Verify producer, original bytes, output bytes, task parameters and installed code/tools."""
    result = inspect_run(Path(path), cancelled=cancelled, progress=progress)
    record, plan = result["record"], result["record"]["plan"]
    if record["status"] != "validated_pending_qc" or record.get("inputs_unchanged") is not True:
        raise ValueError("Reuse requires completed, validated outputs and preserved originals")
    if [t.to_dict() for t in tasks] != plan.get("tasks"):
        raise ValueError("Reuse task/parameter/input binding mismatch")
    if {a.id for a in artifacts} != {a["id"] for a in plan.get("artifacts", [])}:
        raise ValueError("Reuse source/acquisition/declaration identity mismatch")
    from ..registration_review import require_registration

    inventory = {a.id: a.to_dict() for a in artifacts}
    for task in tasks:
        if task.algorithm == "connectome2tck":
            from ..scientific.connectome import load_bundle, require_bundle_registration

            require_bundle_registration(
                load_bundle(inventory[task.inputs["connectome"]]["path"], cancelled=cancelled),
                inventory[task.inputs["connectome"]]["declarations"],
                cancelled=cancelled,
            )
        for port in ("nodes", "original_atlas"):
            if port in task.inputs and "reference" in task.inputs:
                require_registration(
                    inventory[task.inputs[port]],
                    inventory[task.inputs["reference"]],
                    cancelled=cancelled,
                )
    if threads is not None and plan["parameters"]["nthreads"] != threads:
        raise ValueError("Reuse thread configuration differs from approved workflow")
    from ..execution import _environment_contract

    current_environment = _environment_contract(cancelled)
    saved_environment = plan.get("environment", {})
    if any(
        saved_environment.get(key) != current_environment[key]
        for key in ("mrtrix_variables", "config_files")
    ) or any(
        saved_environment.get("overrides", {}).get(key) != value
        for key, value in current_environment["overrides"].items()
    ):
        raise ValueError("Reuse MRtrix environment/configuration changed")
    # Compilers add their own PATH/unset overrides. They are retained as producer
    # provenance, not compared to the un-enriched base environment. Actual native
    # binaries and implementation/helper hashes are checked independently below.
    for name, tool in plan["tools"].items():
        if hash_file(Path(tool["path"]), cancelled) != tool["sha256"]:
            raise ValueError(f"Reuse software identity changed: {name}")
    package = Path(__file__).parents[2]
    if not plan.get("implementation_hashes"):
        raise ValueError("Producer lacks implementation identity for verified reuse")
    for relative, expected in plan["implementation_hashes"].items():
        if hash_file(package / relative, cancelled) != expected:
            raise ValueError("Reuse implementation changed: " + relative)
    return result


def resources(node, artifacts=(), tasks=None):
    """Conservative reservations, not measured peak predictions; overrides cannot lower minima."""
    size = sum(
        s["bytes"] if "bytes" in s else Path(s["path"]).stat().st_size
        for a in artifacts
        for s in a.to_dict()["sources"]
    )
    baseline = {
        "dti": 512 * 1024**2,
        "csd": 2 * GIB,
        "tracking": GIB,
        "anatomy": 3 * GIB,
        "registration": 512 * 1024**2,
        "sift2": 2 * GIB,
        "connectome": 512 * 1024**2,
        "preprocessing": 3 * GIB,
    }[node["adapter"]]
    if node["adapter"] == "preprocessing" and set(
        node.get("preprocessing", {}).get("operations", [])
    ) <= {"convert", "mask"}:
        baseline = 512 * 1024**2
    values = dict(
        memory_bytes=max(baseline, 3 * size), disk_bytes=max(128 * 1024**2, 4 * size), threads=1
    )
    if node["adapter"] == "registration":
        from ..registration_resources import registration_resources

        selected = tasks if tasks is not None else node.get("tasks", [])
        if tasks is None and node.get("selection_role") and not selected:
            selected = [dict(algorithm="registration.fnirt")]  # unresolved selection: conservative
        model = registration_resources(selected, artifacts)
        values = {k: model[k] for k in ("memory_bytes", "disk_bytes", "threads")}
    for key, value in node.get("resources", {}).items():
        if key not in values or isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(
                "Resource requests require positive integer memory_bytes/disk_bytes/threads"
            )
        values[key] = max(values[key], value)
    return values


def build_batch_plan(
    study: Study,
    workflow: dict,
    output: Path,
    *,
    tool_directory=None,
    limits=None,
    cancelled=lambda: False,
    progress=lambda _: None,
):
    """Read-only dry run: version/help probes only, no scientific commands or output mkdir."""
    if not study.acquisitions:
        raise ValueError("No acquisitions discovered; an empty batch cannot execute")
    ordered = workflow_order(workflow)
    if set(workflow.get("overrides", {})) - {a.key for a in study.acquisitions}:
        raise ValueError("Override references an unknown acquisition")
    for overrides in workflow.get("overrides", {}).values():
        if set(overrides) - {node["id"] for node in ordered}:
            raise ValueError("Override references an unknown workflow node")
    output = Path(output).expanduser().absolute()
    if output.exists() or not output.parent.is_dir():
        raise ValueError("Choose a new batch directory with an existing parent")
    root = Path(study.root).resolve(strict=True)
    configured = dict(
        max_subjects=1,
        max_processes=1,
        cpu_threads=2,
        memory_bytes=4 * GIB,
        disk_headroom_bytes=5 * GIB,
    )
    configured.update(limits or {})
    if set(configured) != {
        "max_subjects",
        "max_processes",
        "cpu_threads",
        "memory_bytes",
        "disk_headroom_bytes",
    } or any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in configured.values()):
        raise ValueError("Invalid resource limits")
    rows = []
    for acquisition in study.acquisitions:
        progress("Reviewing " + acquisition.key)
        for original in ordered:
            node = read_json(json.dumps(original))
            override = workflow.get("overrides", {}).get(acquisition.key, {}).get(node["id"], {})
            if set(override) - {"tasks", "resources"}:
                raise ValueError("Overrides may change only explicit tasks/resources")
            node.update(override)
            row = dict(
                key=acquisition.key + "/" + node["id"],
                acquisition=acquisition.to_dict(),
                node=node,
                status="ELIGIBLE",
                issues=[],
                approval_scope="Exact recipe and dependency hashes; deferred inputs must pass native adapter validation",
                resources=resources(node),
            )
            try:
                row["issues"].extend(acquisition.findings)
                if any("disagrees" in finding for finding in acquisition.findings):
                    raise ValueError("Conflicting acquisition identity")
                for binding in node.get("imports", {}).values():
                    if "role" in binding:
                        contained(
                            root, associated_path(acquisition, binding["role"], binding["role"])
                        )
                    for declaration, role in binding.get("declaration_roles", {}).items():
                        contained(root, associated_path(acquisition, role, declaration))
                if node.get("depends_on"):
                    row["status"] = "WAITING_DEPENDENCY"
                else:
                    artifacts, tasks = requests(
                        acquisition, node, root, cancelled=cancelled, progress=progress
                    )
                    row["input_ids"] = [a.id for a in artifacts]
                    row["comparison_configuration"] = comparison_configuration(
                        acquisition, artifacts, tasks
                    )
                    row["resources"] = resources(node, artifacts, tasks)
                    if node.get("reuse_role"):
                        path = contained(
                            root, associated_path(acquisition, node["reuse_role"], "manifest")
                        )
                        reused = verify_reuse(
                            path, artifacts, tasks, cancelled=cancelled, progress=progress
                        )
                        if (
                            "threads" in node.get("resources", {})
                            and node["resources"]["threads"]
                            != reused["record"]["plan"]["parameters"]["nthreads"]
                        ):
                            raise ValueError(
                                "Requested thread override differs from reusable producer"
                            )
                        row["resources"]["threads"] = reused["record"]["plan"]["parameters"][
                            "nthreads"
                        ]
                        row.update(
                            status="SKIPPED_VERIFIED",
                            manifest=str(path),
                            manifest_sha256=reused["manifest_sha256"],
                        )
                    else:
                        # Existing compilers require an existing output parent; preview an unused
                        # sibling path. Actual execution recompiles into the immutable batch attempt.
                        preview = output.parent / (
                            output.name + "-preview-" + digest(row["key"])[:16]
                        )
                        plan = compile_node(
                            acquisition,
                            node,
                            artifacts,
                            tasks,
                            preview,
                            nthreads=row["resources"]["threads"],
                            tool_directory=tool_directory,
                            cancelled=cancelled,
                            progress=progress,
                        )
                        row["preview"] = plan.to_dict()
                        row["preview_digest"] = plan.digest
                if (
                    row["resources"]["memory_bytes"] > configured["memory_bytes"]
                    or row["resources"]["threads"] > configured["cpu_threads"]
                ):
                    if row["status"] != "SKIPPED_VERIFIED":
                        row.update(
                            status="BLOCKED",
                            issues=["Stage reservation exceeds configured memory or thread budget"],
                        )
            except Exception as exc:
                from ..process import ProcessingCancelled

                if isinstance(exc, ProcessingCancelled):
                    raise
                row.update(status="BLOCKED", issues=[f"{type(exc).__name__}: {exc}"])
            rows.append(row)
    # Deferred nodes cannot bind output bytes yet, but all implementation source
    # and selected installed tool identities are frozen before umbrella approval.
    from ..execution import detect_mrtrix
    from ..scientific.registry import ALGORITHMS

    names = set()
    registration_methods = set()
    for node in ordered:
        if node.get("depends_on"):
            for task in node.get("tasks", []):
                if task["algorithm"].startswith("registration."):
                    registration_methods.add(task["algorithm"].removeprefix("registration."))
                else:
                    names.update(ALGORITHMS[task["algorithm"]].to_dict()["dependencies"])
    deferred_tools = (
        detect_mrtrix(
            names=tuple(sorted(names)), tool_directory=tool_directory, cancelled=cancelled
        )
        if names
        else {}
    )
    if registration_methods:
        import os

        from ..anatomy import anatomical_tools

        fsl_names, mr_names = set(), set()
        for method in registration_methods:
            if method == "qc":
                continue  # image-only report generation has no external command
            if method == "reference":
                mr_names.update(("mrconvert", "mrmath"))
            elif method in {"flirt", "fnirt", "epi_reg"}:
                fsl_names.add(method)
                if method == "epi_reg":
                    fsl_names.update(("flirt", "fslmaths"))
            elif method == "convert":
                mr_names.add("transformconvert")
            else:
                # Transform-dependent branches cannot know their producer format
                # yet. Bind the available candidates; compilation still requires
                # and verifies the exact selected tool before execution.
                fsl_names.update(("flirt", "applywarp", "invwarp", "convertwarp", "convert_xfm"))
                mr_names.update(("mrtransform", "transformconvert", "transformcalc"))
        root = Path(os.environ.get("FSLDIR", ""))
        if fsl_names:
            fsl, _ = anatomical_tools(root / "bin", tuple(sorted(fsl_names)), cancelled)
            deferred_tools.update(fsl)
            for path in [root / "etc/fslversion", *(root / "etc/flirtsch").glob("*.sch")]:
                deferred_tools[str(path)] = dict(
                    path=str(path), sha256=hash_file(path), version="FSL configuration"
                )
        if mr_names:
            deferred_tools.update(
                detect_mrtrix(
                    names=tuple(sorted(mr_names)),
                    tool_directory=tool_directory,
                    cancelled=cancelled,
                )
            )
    return ExecutionPlan(
        dict(
            kind="tractosphere_batch_plan",
            schema=1,
            study=study.to_dict(),
            workflow=workflow,
            output_directory=str(output),
            tool_directory=str(tool_directory) if tool_directory else None,
            limits=configured,
            rows=rows,
            scientific_acceptance="unassigned",
            deferred_tools=deferred_tools,
            scientific_implementation_hashes={
                str(p.relative_to(Path(__file__).parents[1])): hash_file(p)
                for p in Path(__file__).parents[1].glob("**/*.py")
                if "batch" not in p.parts
            },
            orchestration_hashes={p.name: hash_file(p) for p in Path(__file__).parent.glob("*.py")},
        )
    )


def compile_node(acquisition, node, artifacts, tasks, output, **kwargs):
    """Adapt preprocessing's existing dataset interface; no new scientific executor."""
    if node["adapter"] != "preprocessing":
        return compiler(node["adapter"])(artifacts, tasks, output, **kwargs)
    from ..dataset import DatasetPaths
    from ..execution import prepare_execution
    from ..loading import DatasetLoader
    from ..workflow import ProcessingHistory

    config = node.get("preprocessing", {})
    if set(config) - {"operations", "history", "options"}:
        raise ValueError("Unsupported preprocessing configuration")
    dwi = next((a for a in artifacts if a.kind == "dwi"), None)
    if dwi is None:
        raise ValueError("Preprocessing requires an explicitly bound DWI and gradients")
    request = dwi.to_dict()["request"]
    loaded = DatasetLoader().load(
        DatasetPaths(
            request["path"], request.get("bval"), request.get("bvec"), request.get("sidecar")
        ),
        cancelled=kwargs.get("cancelled", lambda: False),
    )
    if loaded.dataset is None:
        raise ValueError("Preprocessing dataset failed the existing gradient/image validation")
    try:
        history = ProcessingHistory.from_dict(config["history"])
        options = dict(config.get("options", {}))
        # Paths retain source identity; the existing adapter owns scientific eligibility.
        for key in (
            "fitting_mask",
            "correction_record",
            "correction_gate",
            "ants_directory",
            "bids_root",
            "native_record",
            "fsl_directory",
        ):
            if options.get(key):
                options[key] = Path(options[key])
        if "references" in options:
            options["references"] = tuple(Path(p) for p in options["references"])
        return prepare_execution(
            loaded.dataset, history, tuple(config["operations"]), output, **kwargs, **options
        )
    finally:
        loaded.dataset.close()


def comparison_configuration(acquisition, artifacts, tasks):
    """Keep acquisitions separate unless a reviewed study protocol group is declared."""
    return dict(
        protocol_group=acquisition.metadata.get("comparison_group", acquisition.key),
        tasks=[{"algorithm": t.algorithm, "parameters": t.to_dict()["parameters"]} for t in tasks],
        inputs=[
            dict(
                kind=a.kind,
                shape=(a.to_dict().get("geometry") or {}).get("shape"),
                spacing=(a.to_dict().get("geometry") or {}).get("voxel_sizes_mm"),
                normalization=a.to_dict().get("declarations", {}).get("normalization"),
            )
            for a in artifacts
        ],
    )
