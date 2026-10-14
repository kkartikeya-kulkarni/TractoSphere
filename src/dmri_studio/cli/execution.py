"""Headless prepare → review → approve/run → anatomical QC commands.

Plans are recompiled from validated inputs before execution, never treated as a
shell script. See README for a complete invocation using a separate run folder.
"""

import argparse
import json
import sys
from pathlib import Path

from dmri_studio.core.dataset import DatasetPaths
from dmri_studio.core.execution import (
    ExecutionPlan,
    execute_plan,
    prepare_execution,
    record_qc,
    save_execution_plan,
)
from dmri_studio.core.loading import DatasetLoader
from dmri_studio.core.process import ProcessingCancelled
from dmri_studio.core.validation import ValidationPolicy
from dmri_studio.core.workflow import ProcessingHistory


def add_commands(commands: object) -> None:
    """Add only the bounded executable capabilities and explicit review receipts."""
    project = commands.add_parser(
        "project", help="persistent separate-acquisition project snapshots"
    )
    project.add_argument("action", choices=("new", "inspect", "scan", "context", "add", "attach"))
    project.add_argument("--name")
    project.add_argument("--project", type=Path)
    project.add_argument("--output", type=Path)
    project.add_argument("--root", type=Path)
    project.add_argument(
        "--acquisition",
        type=Path,
        help="JSON containing paths, identity, history and optional evidence",
    )
    project.add_argument("--acquisition-id")
    project.add_argument("--manifest", type=Path)
    review = commands.add_parser(
        "inspect-review", help="verify a local human review against unchanged QC evidence"
    )
    review.add_argument("--catalog", type=Path, required=True)
    review.add_argument("--review", type=Path, required=True)
    inspect = commands.add_parser(
        "inspect-run", help="verify saved run evidence and list outputs/history without processing"
    )
    inspect.add_argument("--manifest", type=Path, required=True)
    prepare = commands.add_parser(
        "prepare", help="preview conversion/masking commands; hash inputs, but do not process them"
    )
    for role in ("dwi", "bval", "bvec", "history", "output", "execution-plan"):
        prepare.add_argument("--" + role, type=Path, required=True)
    prepare.add_argument("--json", type=Path)
    prepare.add_argument("--bvec-layout", choices=("auto", "3xN", "Nx3"), default="auto")
    prepare.add_argument(
        "--operation",
        choices=(
            "convert",
            "denoise",
            "gibbs",
            "mask",
            "bias",
            "correction",
            "anatomy",
            "alignment",
            "combine",
        ),
        action="append",
        required=True,
    )
    prepare.add_argument("--clean-scale", type=float, default=2.0)
    prepare.add_argument("--nthreads", type=int, default=4)
    prepare.add_argument("--tool-directory", type=Path)
    prepare.add_argument(
        "--anatomy-options",
        type=Path,
        help="JSON with explicit anatomical sources/history or producer and review records",
    )
    prepare.add_argument(
        "--combination-options",
        type=Path,
        help="JSON with verified pairing, final N4 producer and pinned HCP helper paths",
    )
    prepare.add_argument("--reference", type=Path, action="append", default=[])
    for option in ("bids-root", "native-record", "fsl-directory"):
        prepare.add_argument("--" + option, type=Path)
    for option in ("fitting-mask", "correction-record", "correction-gate", "ants-directory"):
        prepare.add_argument("--" + option, type=Path)
    prepare.add_argument("--extent", type=int, default=5)
    prepare.add_argument("--nshifts", type=int, default=20)
    prepare.add_argument("--minW", type=int, default=1)
    prepare.add_argument("--maxW", type=int, default=3)
    run = commands.add_parser(
        "execute", help="run the exact reviewed plan with its approval digest"
    )
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument(
        "--approve",
        required=True,
        help="paste the SHA256 digest shown after inspecting prepare's commands and parameters",
    )
    qc = commands.add_parser(
        "qc", help="record human anatomical acceptance/rejection for a validated mask run"
    )
    qc.add_argument("--run", type=Path, required=True)
    qc.add_argument("--decision", choices=("accepted", "rejected"), required=True)
    qc.add_argument("--reason", required=True)
    qc.add_argument("--reviewer", required=True)


def read_json(path: Path) -> dict:
    """Share strict duplicate/constant rejection with the existing history CLI."""
    from .main import _history_pairs, _reject_history_constant

    if path.stat().st_size > 2_097_152:
        raise ValueError("Plan/history JSON exceeds 2 MiB.")
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_history_pairs,
        parse_constant=_reject_history_constant,
    )


def processing_main(args: argparse.Namespace) -> int:
    """Use the same reviewed executor as Python/GUI without importing Qt."""
    dataset = None
    try:
        if args.command == "project":
            from dmri_studio.core.execution import hash_file
            from dmri_studio.core.projects import (
                acquisition_context,
                add_acquisition,
                attach_attempt,
                discover_acquisitions,
                new_project,
                read_project,
                save_project,
            )

            if args.action == "scan":
                if not args.root:
                    raise ValueError("scan requires --root")
                print(json.dumps(discover_acquisitions(args.root), indent=2))
                return 0
            if args.action == "new":
                record = new_project(args.name or "Untitled research project")
            else:
                if not args.project:
                    raise ValueError("This action requires --project")
                previous_digest = hash_file(args.project)
                record = read_project(args.project)
                if args.action == "context":
                    if not args.acquisition_id:
                        raise ValueError("context requires --acquisition-id")
                    print(
                        json.dumps(acquisition_context(args.project, args.acquisition_id), indent=2)
                    )
                    return 0
                if args.action == "inspect":
                    print(json.dumps(record, indent=2))
                    return 0
                if args.action == "add":
                    if not args.acquisition:
                        raise ValueError("add requires --acquisition")
                    spec = read_json(args.acquisition)
                    record = add_acquisition(
                        record,
                        spec["paths"],
                        spec["identity"],
                        spec["history"],
                        evidence=spec.get("evidence", []),
                        discovery=spec.get("discovery"),
                    )
                else:
                    if not args.acquisition_id or not args.manifest:
                        raise ValueError("attach requires --acquisition-id and --manifest")
                    record = attach_attempt(record, args.acquisition_id, args.manifest)
                if hash_file(args.project) != previous_digest:
                    raise ValueError("Project snapshot changed during edit; reopen it")

                record["previous_snapshot"] = {
                    "path": str(args.project.absolute()),
                    "sha256": previous_digest,
                }
            if not args.output:
                raise ValueError("Writing a project requires a new --output snapshot path")
            print(save_project(record, args.output))
            return 0
        if args.command == "inspect-review":
            from dmri_studio.core.qc import load_catalog, load_review

            print(json.dumps(load_review(load_catalog(args.catalog), args.review), indent=2))
            return 0
        if args.command == "inspect-run":
            from dmri_studio.core.runs import inspect_run

            print(json.dumps(inspect_run(args.manifest), indent=2))
            return 0
        if args.command == "qc":
            saved = record_qc(args.run, args.decision, args.reason, reviewer=args.reviewer)
            print(f"Human QC recorded: {saved}")
            return 0
        if args.command == "prepare":
            history_before = args.history.read_bytes()
            history = ProcessingHistory.from_dict(read_json(args.history))
            paths = DatasetPaths(args.dwi, args.bval, args.bvec, args.json)
            policy = ValidationPolicy(bvec_layout=args.bvec_layout)
        else:
            document = read_json(args.plan)
            if not isinstance(document, dict) or set(document) != {"plan", "approval_digest"}:
                raise ValueError("Execution file must contain plan and approval_digest only.")
            plan = ExecutionPlan(document["plan"])
            if document["approval_digest"] != plan.digest or args.approve != plan.digest:
                raise ValueError("Approval does not match the exact reviewed plan.")
            payload = plan.to_dict()
            paths = DatasetPaths(**payload["input_paths"])
            policy = ValidationPolicy(
                bvec_layout=payload["gradient_staging"]["source_layout"],
                b0_max=payload["parameters"]["b0_max"],
            )
        loaded = DatasetLoader().load(paths, policy=policy)
        if loaded.dataset is None:
            from .main import _print_report

            _print_report(loaded.report)
            return 1
        dataset = loaded.dataset
        if args.command == "prepare":
            plan = prepare_execution(
                dataset,
                history,
                tuple(args.operation),
                args.output,
                clean_scale=args.clean_scale,
                nthreads=args.nthreads,
                tool_directory=args.tool_directory,
                progress=print,
                extent=args.extent,
                nshifts=args.nshifts,
                minW=args.minW,
                maxW=args.maxW,
                fitting_mask=args.fitting_mask,
                correction_record=args.correction_record,
                correction_gate=args.correction_gate,
                ants_directory=args.ants_directory,
                references=tuple(args.reference),
                bids_root=args.bids_root,
                native_record=args.native_record,
                fsl_directory=args.fsl_directory,
                anatomy_options=read_json(args.anatomy_options) if args.anatomy_options else None,
                combination_options=read_json(args.combination_options)
                if args.combination_options
                else None,
            )
            if args.history.read_bytes() != history_before:
                raise ValueError("History file changed; review it and prepare again.")
            save_execution_plan(plan, args.execution_plan)
            payload = plan.to_dict()
            print(f"Output folder: {payload['output_directory']}")
            print(f"Parameters: {json.dumps(payload['parameters'])}")
            for name, tool in payload["tools"].items():
                print(f"{name}: {tool['path']} (version {tool['version']})")
            for command in payload["commands"]:
                print(command["display"])
            for action in payload["internal_actions"] + payload["scientific_limits"]:
                print(action)
            print(f"Execution plan: {args.execution_plan}")
            print(f"Review the plan, then approve this exact digest: {plan.digest}")
            return 0
        result = execute_plan(dataset, plan, approval=args.approve, progress=print)
        print(f"Run status: {result['status']}; outputs/logs: {plan.payload['output_directory']}")
        if "error" in result:
            print(result["error"], file=sys.stderr)
        return (
            130
            if result["status"] == "cancelled"
            else 0
            if result["status"] in {"validated", "validated_pending_qc"}
            else 1
        )
    except (ProcessingCancelled, KeyboardInterrupt):
        print("Processing cancelled.", file=sys.stderr)
        return 130
    except (ValueError, TypeError, KeyError) as exc:
        print(f"dmri-studio: processing request error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"dmri-studio: processing error: {exc}", file=sys.stderr)
        return 1
    finally:
        if dataset:
            dataset.close()
