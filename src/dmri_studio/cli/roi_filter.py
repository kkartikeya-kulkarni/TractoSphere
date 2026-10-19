"""Headless exact-label preparation and approved native filtering."""

import json
import signal
from pathlib import Path
from threading import Event

from dmri_studio.core.execution import ExecutionPlan, execute_plan
from dmri_studio.core.roi_filtering import prepare_roi_filter


def add_command(commands):
    parser = commands.add_parser(
        "roi-filter", help="prepare or execute full-source atlas ROI filtering"
    )
    sub = parser.add_subparsers(dest="roi_action", required=True)
    prepare = sub.add_parser("prepare")
    for name in ("tractogram", "atlas", "reference", "output"):
        prepare.add_argument("--" + name, type=Path, required=True)
    prepare.add_argument("--include", default="[]", help="JSON original IDs in waypoint order")
    prepare.add_argument("--exclude", default="[]")
    prepare.add_argument("--mode", choices=["OR", "AND", "ORDERED"], default="OR")
    prepare.add_argument("--ends-only", action="store_true")
    prepare.add_argument(
        "--declarations", required=True, help="JSON reviewed subject/space/alignment evidence"
    )
    prepare.add_argument("--mrtrix-directory", type=Path)
    execute = sub.add_parser("execute")
    execute.add_argument("--plan", required=True, type=Path)
    execute.add_argument("--approve-digest", required=True)


def run(args):
    cancel = Event()
    previous = signal.signal(signal.SIGINT, lambda *_: cancel.set())
    try:
        if args.roi_action == "prepare":
            plan = prepare_roi_filter(
                args.tractogram,
                args.atlas,
                args.reference,
                args.output,
                include=json.loads(args.include),
                exclude=json.loads(args.exclude),
                mode=args.mode,
                ends_only=args.ends_only,
                declarations=json.loads(args.declarations),
                tool_directory=args.mrtrix_directory,
                cancelled=cancel.is_set,
            )
            print(
                json.dumps(
                    {
                        "plan": str(args.output / "review-plan.json"),
                        "approval_digest": plan.digest,
                        "commands": [c["display"] for c in plan.to_dict()["commands"]],
                    },
                    indent=2,
                )
            )
            return 0
        plan = ExecutionPlan(json.loads(args.plan.read_text()))
        result = execute_plan(None, plan, approval=args.approve_digest, cancelled=cancel.is_set)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "validated_pending_qc" else 1
    except (ValueError, OSError, KeyError) as error:
        print(str(error))
        return 1

    finally:
        signal.signal(signal.SIGINT, previous)
