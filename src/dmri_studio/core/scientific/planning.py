"""Typed DAG proposals for independent scientific entry points; never execution.

A downstream task can bind an imported artifact ID or an explicit OutputRef.
Outputs are contracts, not files or measured results. All planned-adapter graphs
remain non-executable even if input compatibility checks pass.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .._mif_checks import voxel_mapping
from ..execution import ExecutionPlan
from ..process import check_cancel
from ..validation import freeze_json, json_compatible
from .contracts import Artifact, validate_declarations
from .registry import ALGORITHMS, Algorithm


@dataclass(frozen=True)
class OutputRef:
    """Reference one named output of one task, not a guessed filesystem path."""

    task_id: str
    output: str

    def __post_init__(self) -> None:
        """Output references use identifiers, never arbitrary paths or argv."""
        for value in (self.task_id, self.output):
            if not isinstance(value, str) or not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_-]{0,63}", value
            ):
                raise ValueError("Output references require safe task and product identifiers")


@dataclass(frozen=True)
class Task:
    """Serializable reusable operation selection; no GUI, process or dataset owner."""

    id: str
    algorithm: str
    inputs: Any
    parameters: Any = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Freeze the contract and reject ambiguous or unsupported input meaning."""
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", self.id):
            raise ValueError("Task ID must be a safe identifier, not a path")
        if self.algorithm not in ALGORITHMS:
            raise ValueError(f"Unknown algorithm: {self.algorithm}")
        if not isinstance(self.inputs, dict) or not isinstance(self.parameters, dict):
            raise ValueError("Task bindings and parameters must be objects")
        validate_declarations(self.parameters)
        for binding in self.inputs.values():
            if not isinstance(binding, (str, OutputRef)):
                raise ValueError("Bind an artifact ID or explicit OutputRef")
        object.__setattr__(self, "inputs", freeze_json(self.inputs))
        object.__setattr__(self, "parameters", freeze_json(self.parameters))

    def to_dict(self) -> dict:
        """Return detached JSON-compatible contract evidence for review or persistence."""
        return dict(
            id=self.id,
            algorithm=self.algorithm,
            inputs={
                k: dict(task_id=v.task_id, output=v.output)
                if isinstance(v, OutputRef)
                else dict(artifact_id=v)
                for k, v in self.inputs.items()
            },
            parameters=json_compatible(self.parameters),
        )

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        """Round-trip exported task selection; no arbitrary command import."""
        if set(data) != {"id", "algorithm", "inputs", "parameters"}:
            raise ValueError("Unexpected task fields")
        bindings = {}
        for name, ref in data["inputs"].items():
            if set(ref) == {"artifact_id"}:
                bindings[name] = ref["artifact_id"]
            elif set(ref) == {"task_id", "output"}:
                bindings[name] = OutputRef(ref["task_id"], ref["output"])
            else:
                raise ValueError("Invalid typed input reference")
        return cls(data["id"], data["algorithm"], bindings, data["parameters"])


def _order(tasks: tuple[Task, ...]) -> list[Task]:
    """Stable topological order; reject cycles and missing producers explicitly."""
    by_id = {t.id: t for t in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("Duplicate task IDs")
    dependencies = {
        t.id: {v.task_id for v in t.inputs.values() if isinstance(v, OutputRef)} for t in tasks
    }
    for targets in dependencies.values():
        if not targets <= by_id.keys():
            raise ValueError("Output reference names a missing producer")
    result, done = [], set()
    while len(result) < len(tasks):
        ready = [t for t in tasks if t.id not in done and dependencies[t.id] <= done]
        if not ready:
            raise ValueError("Workflow contains a dependency cycle")
        result.extend(ready)
        done.update(t.id for t in ready)
    return result


def _parameters(spec: Algorithm, params: dict, add: Callable[[str, str], None]) -> None:
    """Check only the registry-declared typed parameters and explicit policies."""
    for key in set(params) - set(spec.parameters):
        add("error", f"Unsupported parameter {key}; arbitrary command arguments are not permitted")
    for key, rule in spec.parameters.items():
        value = params.get(key)
        if value is None:
            if rule["required"]:
                add("error", f"Choose {key} explicitly; no scientific default selected")
            continue
        kind = rule["type"]
        if kind in {"choice_list", "number_list", "vector"}:
            if (
                not isinstance(value, list)
                or not value
                or (kind != "vector" and len({str(v) for v in value}) != len(value))
            ):
                add("error", f"{key} requires a nonempty, nonduplicated list")
                continue
            values = value
        else:
            values = [value]
        for v in values:
            if kind.startswith("choice"):
                if v not in rule["choices"]:
                    add("error", f"Unsupported {key} choice: {v}")
            elif (
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not math.isfinite(v)
                or (kind == "int" and not isinstance(v, int))
            ):
                add("error", f"{key} must be a finite {kind}")
            elif (rule["minimum"] is not None and v < rule["minimum"]) or (
                rule["maximum"] is not None and v > rule["maximum"]
            ):
                add("error", f"{key} is outside its declared range")
        if key == "lmax" and isinstance(value, int) and value % 2:
            add("error", "lmax must be even for symmetric SH")


def _same_geometry(inputs: dict, add: Callable[[str, str], None]) -> None:
    """Conservative no-resampling Stage 1 contract, plus explicit space identity."""
    images = [(key, v) for key, v in inputs.items() if v.get("geometry")]
    if not images:
        if any(v["kind"] == "tractogram" for v in inputs.values()):
            add(
                "review",
                "No anatomical reference bound; tract coordinates alone do not establish alignment",
            )
        return
    _, base = images[0]
    for key, value in images[1:]:
        a, b = base["geometry"], value["geometry"]
        try:
            # MRtrix canonical storage can permute/flip NIfTI voxel indices.
            # Accept only the existing proven one-to-one physical grid map;
            # never equate overlapping bounds with a registration transform.
            axes, flips = voxel_mapping(
                np.asarray(a["affine_mm"]), a["shape"], np.asarray(b["affine_mm"]), b["shape"]
            )
        except ValueError:
            add(
                "error",
                f"{key} differs from the reference grid; verified registration/resampling is not implemented in this planner",
            )
        else:
            if tuple(axes) != (0, 1, 2) or any(flips):
                add(
                    "review",
                    f"{key} has a verified lossless voxel-axis permutation/flip on the same physical grid. Original files remain unchanged; no resampling or spatial registration is applied.",
                )
    # Bounds can disprove gross alignment, but overlap never proves registration.
    corners = np.array(
        [
            [x, y, z, 1]
            for x in (-0.5, base["geometry"]["shape"][0] - 0.5)
            for y in (-0.5, base["geometry"]["shape"][1] - 0.5)
            for z in (-0.5, base["geometry"]["shape"][2] - 0.5)
        ]
    )
    world = corners @ np.asarray(base["geometry"]["affine_mm"]).T
    for value in inputs.values():
        bounds = value.get("metadata", {})
        if value["kind"] == "tractogram" and "bounds_min" in bounds:
            if np.any(np.asarray(bounds["bounds_max"]) < world[:, :3].min(axis=0)) or np.any(
                np.asarray(bounds["bounds_min"]) > world[:, :3].max(axis=0)
            ):
                add(
                    "error",
                    "Tract RAS+ bounds do not overlap the bound image; no coordinate correction is implicit",
                )
    spaces = {v.get("declarations", {}).get("space_id") for v in inputs.values()}
    known = spaces - {None, ""}
    if len(known) > 1:
        add(
            "error",
            "Declared coordinate-space identities disagree; a matching affine is insufficient",
        )
    if None in spaces or "" in spaces or not known:
        add(
            "review",
            "Anatomical space association is unverified; equal geometry does not prove subject or registration correspondence",
        )
    else:
        add(
            "review",
            "Space identity is user-declared; anatomical alignment requires evidence and visual review",
        )


def _sampling(task: Task, dwi: dict, add: Callable[[str, str], None]) -> dict:
    """Preserve original indices; classify declared shells and test DTI rank only."""
    gradient = dwi.get("gradients")
    if not gradient:
        add(
            "review",
            "Upstream DWI gradients require verification before consuming the expected product",
        )
        return {}
    for finding in gradient["checks"]:
        if finding["status"] == "warn":
            add("review", "Gradient inspection warning: " + finding["message"])
    groups = gradient["shells"]
    shells = task.parameters.get("shells", ())
    if not isinstance(shells, (tuple, list)):
        add("error", "Shell selection must be an explicit list")
        return {}
    selected = []
    for target in shells:
        if (
            isinstance(target, bool)
            or not isinstance(target, (int, float))
            or not math.isfinite(target)
        ):
            continue
        matches = [
            g
            for g in groups
            if abs(g["b_median"] - target) <= gradient["policy"]["shell_span"]
            and (g["is_b0"] == (target <= gradient["policy"]["b0_max"]))
        ]
        if len(matches) != 1:
            add(
                "error",
                f"Selected shell {target} does not identify exactly one descriptive b-value group",
            )
        elif matches[0] not in selected:
            selected.append(matches[0])
    weighted = [g for g in selected if not g["is_b0"]]
    if not weighted:
        add("error", "No diffusion-weighted group explicitly selected")
    if (
        task.algorithm in {"dwi2fod.csd", "dwi2response.tournier", "dwi2response.tax"}
        and len(weighted) != 1
    ):
        add(
            "error",
            "This single-shell contract requires exactly one weighted group; no shell was silently selected",
        )
    if task.algorithm == "dwi2fod.msmt_csd" and (
        len(weighted) < 2 or not any(g["is_b0"] for g in selected)
    ):
        add("error", "Three-tissue MSMT contract requires b0 and at least two weighted groups")
    indices = sorted(i for g in selected for i in g["volume_indices"])
    result = dict(
        selected_shells=[dict(b_median=g["b_median"], is_b0=g["is_b0"]) for g in selected],
        selected_original_volume_indices=indices,
        selected_bvalues=[gradient["bvals"][i] for i in indices],
        gradient_convention=gradient["convention"],
    )
    if task.algorithm == "dwi2tensor" and indices:
        b = np.asarray(gradient["bvals"])[indices]
        x, y, z = np.asarray(gradient["bvecs"])[indices].T
        design = np.column_stack(
            [
                np.ones(len(b)),
                b * x * x,
                b * y * y,
                b * z * z,
                2 * b * x * y,
                2 * b * x * z,
                2 * b * y * z,
            ]
        )
        rank = int(np.linalg.matrix_rank(design))
        result["tensor_design_rank"] = rank
        if rank < 7:
            add(
                "error",
                "Selected DTI measurements have rank <7 including intercept; fitting is underdetermined",
            )
    if "lmax" in task.parameters and isinstance(task.parameters["lmax"], int):
        coefficients = (task.parameters["lmax"] + 1) * (task.parameters["lmax"] + 2) // 2
        if sum(g["count"] for g in weighted) < coefficients:
            add(
                "review",
                "Directional count below SH coefficient count; angular rank/regularization need algorithm-specific validation",
            )
    add(
        "review",
        "Descriptive shell groups do not verify common TE, gradient anatomy, filtering history or scientific acquisition compatibility",
    )
    if dwi.get("declarations", {}).get("processing_state") == "raw":
        add(
            "review",
            "Raw DWI: review appropriate preprocessing using the existing engine; no corrections are assumed or automatically added",
        )
    return result


def build_workflow(
    artifacts: tuple[Artifact, ...],
    tasks: tuple[Task, ...],
    *,
    dependency_evidence: dict | None = None,
    cancelled=lambda: False,
    progress=lambda _: None,
    verify_sources: bool = True,
) -> ExecutionPlan:
    """Build a digest-bound review proposal. No directory, command or process created."""
    inventory = {a.id: a for a in artifacts}
    if len(inventory) != len(artifacts):
        raise ValueError("Duplicate imported artifact IDs")
    if not tasks:
        raise ValueError("Select at least one scientific task")
    if verify_sources:
        for artifact in artifacts:
            check_cancel(cancelled)
            progress(f"Revalidating source hashes: {Path(artifact.payload['path']).name}")
            artifact.assert_current(cancelled)
    products, rows, issues = {}, [], []
    ordered = _order(tasks)
    for task in ordered:
        check_cancel(cancelled)
        spec = ALGORITHMS[task.algorithm]

        def add(severity: str, message: str, task_id: str = task.id) -> None:
            """Associate each finding with the task that produced it."""
            issues.append(dict(task=task_id, severity=severity, message=message))

        params = json_compatible(task.parameters)
        _parameters(spec, params, add)
        if task.algorithm == "dwi2tensor":
            if params.get("fit") in {"WLS_IWLS", "IWLS"} and params.get("iterations") is None:
                add(
                    "error",
                    "WLS_IWLS requires an explicit iteration count; no upstream fitting default is assumed",
                )
            if params.get("fit") == "OLS" and params.get("iterations") is not None:
                add("error", "IWLS iteration count is incompatible with OLS")
        inputs = {}
        slots = {**spec.required_inputs, **spec.optional_inputs}
        for role in set(task.inputs) - set(slots):
            add("error", f"Unsupported input slot {role}")
        for role, kind in slots.items():
            binding = task.inputs.get(role)
            if binding is None:
                if role in spec.required_inputs:
                    add("error", f"Missing required {role} ({kind})")
                continue
            if isinstance(binding, OutputRef):
                if (binding.task_id, binding.output) not in products:
                    raise ValueError(f"Unknown producer output {binding.task_id}/{binding.output}")
                value = products[(binding.task_id, binding.output)]
                add(
                    "review", f"{role} is an expected upstream output; verification remains pending"
                )
            else:
                if binding not in inventory:
                    raise ValueError(f"Unknown artifact ID for {role}")
                value = inventory[binding].to_dict()
            if value["kind"] != kind:
                add("error", f"{role} needs {kind}, received {value['kind']}")
            inputs[role] = value
        if spec.stage != "registration":
            _same_geometry(inputs, add)
        else:
            add(
                "review",
                "Registration deliberately crosses grids; the registration adapter validates exact source/target contracts",
            )
        # Proposal, CLI validation and execution preparations share the same
        # registration decision. Deferred outputs still require materialization.
        for role in ("nodes", "original_atlas"):
            if (
                role in inputs
                and "reference" in inputs
                and not any(inputs[k].get("expected") for k in (role, "reference"))
            ):
                from ..registration_review import require_registration

                try:
                    review = require_registration(
                        inputs[role], inputs["reference"], cancelled=cancelled
                    )
                    add(
                        "review",
                        "Registration eligibility: "
                        + review["human_review_decision"]
                        + "/"
                        + review["review_applicability"]
                        + "; usage="
                        + review["usage"],
                    )
                except (ValueError, OSError) as exc:
                    add("error", str(exc))
        if (
            task.algorithm == "connectome2tck"
            and "connectome" in inputs
            and not inputs["connectome"].get("expected")
        ):
            from .connectome import load_bundle, require_bundle_registration

            try:
                item = inputs["connectome"]
                require_bundle_registration(
                    load_bundle(item["path"], cancelled=cancelled),
                    item["declarations"],
                    cancelled=cancelled,
                )
            except (ValueError, OSError) as exc:
                add("error", str(exc))
        sampling = (
            _sampling(task, inputs["dwi"], add)
            if "dwi" in inputs and inputs["dwi"]["kind"] == "dwi" and spec.stage != "registration"
            else {}
        )
        if "tensor" in inputs and task.algorithm == "tensor2metric":
            decl = inputs["tensor"].get("declarations", {})
            if (
                decl.get("tensor_layout") != "mrtrix_D11_D22_D33_D12_D13_D23"
                or decl.get("tensor_frame") != "world_ras"
            ):
                add(
                    "error",
                    "tensor2metric contract requires MRtrix component order and world frame; no FSL conversion is implicit",
                )
        for role in ("wm_response", "gm_response", "csf_response"):
            if role in inputs and not inputs[role].get("expected"):
                response = inputs[role]
                if response.get("declarations", {}).get("tissue") != role.removesuffix("_response"):
                    add("error", f"{role} tissue declaration does not match its input slot")
                selected = sampling.get("selected_shells", [])
                if selected:
                    from .responses import response_rows

                    try:
                        response_rows(
                            response["metadata"],
                            selected,
                            role.removesuffix("_response"),
                            params.get("lmax", 2) if role == "wm_response" else 0,
                            single=task.algorithm == "dwi2fod.csd",
                        )
                    except ValueError as exc:
                        add("error", f"{role} response shell/coefficient compatibility: {exc}")
        if (
            task.algorithm == "mtnormalise"
            and inputs.get("wm_fod", {}).get("declarations", {}).get("normalization") == "completed"
        ):
            add(
                "error",
                "FOD normalization declared completed; repeated normalization is not allowed",
            )
        if task.algorithm == "mtnormalise":
            for role, tissue in (("wm_fod", "wm"), ("gm", "gm"), ("csf", "csf")):
                item = inputs.get(role, {})
                if item and item.get("declarations", {}).get("tissue") != tissue:
                    add("error", f"{role} must explicitly identify the {tissue} compartment")
        if (
            task.algorithm == "tckedit"
            and isinstance(params.get("min_length_mm"), (int, float))
            and isinstance(params.get("max_length_mm"), (int, float))
            and params["max_length_mm"] < params["min_length_mm"]
        ):
            add("error", "Maximum tract length is below minimum")
        if task.algorithm == "tcksift2" and params.get("act") == "on" and "five_tt" not in inputs:
            add("error", "ACT weighting requires explicitly associated 5TT")
        if task.algorithm == "tck2connectome":
            if (
                params.get("assignment") == "radial_search"
                and params.get("radial_search_mm") is None
            ):
                add(
                    "error",
                    "Radial assignment requires an explicit distance; no upstream default chosen",
                )
            if (
                params.get("assignment") == "end_voxels"
                and params.get("radial_search_mm") is not None
            ):
                add(
                    "error", "Radial-search distance is incompatible with endpoint-voxel assignment"
                )
            if "weights" in inputs and not inputs["weights"].get("expected"):
                weights, tracts = inputs["weights"], inputs.get("tracts", {})
                if weights.get("metadata", {}).get("rows") != tracts.get("metadata", {}).get(
                    "streamlines"
                ) or weights.get("declarations", {}).get("tractogram_id") != tracts.get("id"):
                    add(
                        "error",
                        "Weights require exact tractogram identity/order association and one row per streamline",
                    )
            add(
                "review",
                "Node-label ordering and endpoint assignment must be verified; streamline weights/counts are not axon counts",
            )
        dependency = (dependency_evidence or {}).get(spec.command)
        if dependency_evidence is not None and (not dependency or not dependency.get("path")):
            add("error", f"Missing inspected dependency: {spec.command}")
        elif dependency is None:
            add("review", f"Dependency {spec.command} not probed; install/version state unknown")
        if dependency is not None:
            add(
                "review",
                "Dependency evidence recorded; execution still requires fresh exact-command adapter validation"
                if spec.implementation == "implemented"
                else "Dependency evidence recorded; no algorithm-specific version has passed adapter acceptance",
            )
            if not dependency.get("sha256") or not dependency.get("version"):
                add(
                    "review",
                    "Dependency evidence lacks an exact version or binary hash; execution would require fresh inspection",
                )
        if spec.implementation == "planned":
            add(
                "adapter",
                f"{spec.id} is planned; no tested execution adapter or validated algorithm-specific versions",
            )
        else:
            add(
                "review",
                f"{spec.id} has an implemented adapter; prepare a separate exact-command execution plan before approval",
            )
        base = next((v for v in inputs.values() if v.get("geometry")), {})
        expected_outputs = {}
        for name, kind in spec.outputs.items():
            # Inherit association, not input-specific tensor/tissue semantics.
            decl = {
                key: value
                for key, value in base.get("declarations", {}).items()
                if key in {"space_id", "subject_id", "session_id", "acquisition_id"}
            }
            if kind == "tensor":
                decl.update(
                    tensor_layout="mrtrix_D11_D22_D33_D12_D13_D23",
                    tensor_frame="world_ras",
                    tensor_units="mm2/s",
                )
            if kind == "fod":
                decl.update(
                    sh_basis="mrtrix3_real_symmetric",
                    sh_frame="world_ras",
                    tissue="wm",
                    normalization="completed" if spec.id == "mtnormalise" else "not_done",
                )
            if kind == "tissue_scalar" and name in {"gm", "csf"}:
                decl["tissue"] = name
                decl["normalization"] = "completed" if spec.id == "mtnormalise" else "not_done"
            if kind == "response":
                decl["tissue"] = name.removesuffix("_response")
            # A response/weight/tract file is not an image just because its
            # producer consumed an image. Only image products inherit a grid.
            spatial_grid = (
                dict(base["geometry"])
                if base.get("geometry") and kind in {"tensor", "fod", "tissue_scalar"}
                else None
            )
            if spatial_grid:
                spatial_grid["shape"] = list(spatial_grid["shape"][:3])
            value = dict(
                kind=kind,
                expected=True,
                geometry=spatial_grid,
                declarations=decl,
                metadata={},
                id=f"{task.id}:{name}",
            )
            products[(task.id, name)] = value
            expected_outputs[name] = dict(
                kind=kind,
                spatial_grid=spatial_grid,
                declarations=decl,
                verification="not produced; output contract pending adapter QC",
            )
        rows.append(
            dict(
                **task.to_dict(),
                sampling=sampling,
                expected_outputs=expected_outputs,
                dependency=dependency,
                adapter_status=spec.implementation,
                registry_contract=spec.to_dict(),
                qc_requirements=list(spec.qc),
                execution="not_started",
                technical_verification="proposal only",
                scientific_review="required",
            )
        )
    errors = [i for i in issues if i["severity"] == "error"]
    return ExecutionPlan(
        dict(
            schema_version=1,
            kind="scientific_workflow_proposal",
            source_validation="rehash complete"
            if verify_sources
            else "preview only; source hashes not rechecked",
            status="blocked" if errors else "review_required",
            execution_ready=False,
            commands=[],
            approval_policy="Execution requires a separate exact compiled plan and approval after adapter acceptance",
            artifacts=[a.to_dict() for a in artifacts],
            tasks=rows,
            issues=issues,
            scientific_acceptance="not assessed",
            batch_scope="single immutable task graph; no scheduler or interrupted-process recovery",
        )
    )
