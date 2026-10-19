"""Headless study discovery, approval, scheduling and recovery over shared services."""

from __future__ import annotations

import json
import signal
import sys
from pathlib import Path
from threading import Event

from dmri_studio.core.batch.discovery import (
    Study,
    discover_bids,
    discover_custom,
    import_manifest,
    manifest_template,
)
from dmri_studio.core.batch.engine import (
    BatchRunner,
    control,
    create_batch,
    export_report,
    read_journal,
    record_review,
    study_qc,
)
from dmri_studio.core.batch.planning import build_batch_plan
from dmri_studio.core.execution import ExecutionPlan, _publish_json
from dmri_studio.core.scientific.records import read_json


def add_command(commands):
    parser = commands.add_parser(
        "batch", help="study discovery, dry run, approved scheduling and recovery"
    )
    sub = parser.add_subparsers(dest="batch_action", required=True)
    discover = sub.add_parser("discover")
    discover.add_argument("--method", choices=("bids", "custom", "manifest"), required=True)
    discover.add_argument("--root", type=Path, required=True)
    discover.add_argument("--study-id", required=True)
    discover.add_argument("--input", type=Path, help="CSV/TSV manifest or custom mapping JSON")
    discover.add_argument("--output", type=Path, required=True)
    template = sub.add_parser("manifest-template")
    template.add_argument("--output", type=Path, required=True)
    plan = sub.add_parser("plan", help="dry run only; no scientific commands")
    plan.add_argument("--study", type=Path, required=True)
    plan.add_argument("--workflow", type=Path, required=True)
    plan.add_argument("--output-directory", type=Path, required=True)
    plan.add_argument("--plan", type=Path, required=True)
    plan.add_argument("--mrtrix-directory", type=Path)
    plan.add_argument("--limits", default="{}", help="JSON resource budgets")
    execute = sub.add_parser("execute")
    execute.add_argument("--plan", type=Path, required=True)
    execute.add_argument("--approve-digest", required=True)
    for name in (
        "status",
        "qc",
        "resume",
        "pause",
        "cancel",
        "retry",
        "export",
        "registration-qc",
        "export-registration-qc",
    ):
        command = sub.add_parser(name)
        command.add_argument("--batch", type=Path, required=True)
        if name in ("cancel", "retry"):
            command.add_argument(
                "--key", action="append", default=[], help="exact node key; repeat to select jobs"
            )
        if name in {"export", "export-registration-qc"}:
            command.add_argument("--output", type=Path, required=True)

    review = sub.add_parser("review", help="record an explicit human scientific assessment")
    review.add_argument("--batch", type=Path, required=True)
    review.add_argument("--key", required=True)
    review.add_argument(
        "--decision", choices=("accepted", "rejected", "requires_review"), required=True
    )
    review.add_argument("--reviewer", required=True)
    review.add_argument("--note", required=True)


def run(args):
    try:
        action = args.batch_action
        if action == "registration-qc":
            from dmri_studio.core.registration_qc import batch_qc_rows

            print(json.dumps(batch_qc_rows(args.batch), indent=2))
            return 0
        if action == "export-registration-qc":
            from dmri_studio.core.registration_qc import export_batch_qc

            print(export_batch_qc(args.batch, args.output))
            return 0
        if action == "review":
            print(
                json.dumps(
                    record_review(args.batch, args.key, args.decision, args.reviewer, args.note),
                    indent=2,
                )
            )
            return 0
        if action == "manifest-template":
            manifest_template(args.output)
            return 0
        if action == "discover":
            if args.method == "bids":
                study = discover_bids(args.root, args.study_id)
            elif args.method == "custom":
                study = discover_custom(args.root, args.study_id, read_json(args.input.read_text()))
            else:
                study = import_manifest(args.input, args.root, args.study_id)
            _publish_json(args.output, study.to_dict())
            print(json.dumps(study.to_dict(), indent=2))
            return 0
        if action == "plan":
            plan = build_batch_plan(
                Study.from_dict(read_json(args.study.read_text())),
                read_json(args.workflow.read_text()),
                args.output_directory,
                tool_directory=args.mrtrix_directory,
                limits=read_json(args.limits),
                progress=lambda s: print(s, file=sys.stderr),
            )
            _publish_json(args.plan, plan.to_dict())
            print(plan.to_json())
            print("Approval digest: " + plan.digest)
            return 1 if any(r["status"] == "BLOCKED" for r in plan.to_dict()["rows"]) else 0
        if action in ("status", "qc"):
            print(
                json.dumps(
                    read_journal(args.batch) if action == "status" else study_qc(args.batch),
                    indent=2,
                )
            )
            return 0
        if action == "export":
            print(export_report(args.batch, args.output))
            return 0
        if action in ("pause", "cancel", "retry"):
            print(control(args.batch, action, getattr(args, "key", ())))
            return 0
        if action == "execute":
            plan = ExecutionPlan(read_json(args.plan.read_text()))
            folder = create_batch(plan, args.approve_digest)
        else:
            folder = args.batch
            control(folder, "resume")
        cancel = Event()
        previous = signal.signal(signal.SIGINT, lambda *_: cancel.set())
        try:
            result = BatchRunner(
                folder, progress=lambda s: print(s, file=sys.stderr), cancelled=cancel.is_set
            ).run()
        finally:
            signal.signal(signal.SIGINT, previous)
        print(json.dumps(result, indent=2))
        return 0 if result["state"] in {"COMPLETED", "PAUSED"} else 1
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        print("Batch error: " + str(exc), file=sys.stderr)
        return 2
