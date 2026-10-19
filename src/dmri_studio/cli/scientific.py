"""Qt-free scientific imports, workflow review and explicitly approved DTI/CSD/tracking execution."""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
from pathlib import Path
from threading import Event, current_thread, main_thread
from typing import Any

from dmri_studio.core.execution import detect_mrtrix, save_execution_plan
from dmri_studio.core.process import ProcessingCancelled
from dmri_studio.core.scientific import (
    ALGORITHMS,
    ImportRequest,
    Task,
    build_workflow,
    import_artifact,
)
from dmri_studio.core.scientific.contracts import KINDS
from dmri_studio.core.scientific.records import read_json, restore_selection, save_selection


def add_command(commands: Any) -> None:
    """Expose independently callable contracts without changing preprocessing CLI."""
    parser = commands.add_parser(
        "scientific",
        help="import inputs, review tasks and execute approved reconstruction, tracking, registration and connectomics",
    )
    sub = parser.add_subparsers(dest="scientific_action", required=True)
    sub.add_parser(
        "registry", help="describe implemented/planned algorithms, inputs, outputs and QC"
    )
    imp = sub.add_parser("import", help="inspect one input independently of preprocessing")
    imp.add_argument("--kind", choices=KINDS, required=True)
    imp.add_argument("--file", type=Path, required=True)
    for name in ("bval", "bvec", "sidecar"):
        imp.add_argument("--" + name, type=Path)
    imp.add_argument(
        "--declarations",
        default="{}",
        help="explicit JSON scientific conventions; no filename guessing",
    )
    imp.add_argument(
        "--from-selection",
        type=Path,
        help="reinspect and extend an existing selection into a NEW record",
    )
    imp.add_argument(
        "--provenance",
        default="External input; processing history and scientific acceptance unknown.",
    )
    imp.add_argument(
        "--selection", type=Path, required=True, help="new metadata record; no MRI copying"
    )
    plan = sub.add_parser(
        "plan", help="revalidate a selection and export a non-executable proposal"
    )
    plan.add_argument("--selection", type=Path, required=True)
    plan.add_argument("--tasks", type=Path, help="optional JSON list of Task.to_dict() selections")
    plan.add_argument("--proposal", type=Path, required=True)
    plan.add_argument(
        "--probe-tools", action="store_true", help="explicit local version/hash probes only"
    )
    plan.add_argument("--mrtrix-directory", type=Path)
    dti = sub.add_parser("dti", help="preview or explicitly approve implemented DTI tasks")
    dti.add_argument("--selection", type=Path, required=True)
    dti.add_argument("--output", type=Path, required=True, help="new immutable attempt directory")
    dti.add_argument("--mrtrix-directory", type=Path)
    dti.add_argument("--threads", type=int, default=4)
    dti.add_argument("--plan", type=Path, help="save exact command plan to a new file")
    dti.add_argument(
        "--approve-digest", help="run only when matching the displayed command-plan digest"
    )
    csd = sub.add_parser(
        "csd", help="preview/approve implemented response, CSD and optional normalization tasks"
    )
    csd.add_argument("--selection", type=Path, required=True)
    csd.add_argument("--output", type=Path, required=True, help="new immutable attempt directory")
    csd.add_argument("--mrtrix-directory", type=Path)
    csd.add_argument("--threads", type=int, default=4)
    csd.add_argument("--plan", type=Path, help="save exact command plan to a new file")
    csd.add_argument("--approve-digest", help="run only the matching reviewed command-plan digest")

    sift2 = sub.add_parser("sift2", help="preview/approve native SIFT2 weight factors and QC")
    sift2.add_argument("--selection", type=Path, required=True)
    sift2.add_argument("--output", type=Path, required=True)
    sift2.add_argument("--mrtrix-directory", type=Path)
    sift2.add_argument("--threads", type=int, default=2)
    sift2.add_argument("--plan", type=Path)
    sift2.add_argument("--approve-digest")
    registration = sub.add_parser(
        "registration", help="preview/approve native image registration and transform tasks"
    )
    registration.add_argument("--selection", type=Path, required=True)
    registration.add_argument("--output", type=Path, required=True)
    registration.add_argument("--mrtrix-directory", type=Path)
    registration.add_argument("--threads", type=int, default=2)
    registration.add_argument("--plan", type=Path)
    registration.add_argument("--approve-digest")
    anatomy = sub.add_parser("anatomy", help="preview/approve native 5TT and GM-WM interface tasks")
    anatomy.add_argument("--selection", type=Path, required=True)
    anatomy.add_argument("--output", type=Path, required=True)
    anatomy.add_argument("--mrtrix-directory", type=Path)
    anatomy.add_argument("--threads", type=int, default=2)
    anatomy.add_argument("--plan", type=Path)
    anatomy.add_argument("--approve-digest")
    tracking = sub.add_parser(
        "tracking", help="preview/approve MRtrix tracking, filtering and density tasks"
    )
    tracking.add_argument("--selection", type=Path, required=True)
    tracking.add_argument("--output", type=Path, required=True)
    tracking.add_argument("--mrtrix-directory", type=Path)
    tracking.add_argument("--threads", type=int, default=2)
    tracking.add_argument("--plan", type=Path)
    tracking.add_argument("--approve-digest")
    connectome = sub.add_parser(
        "connectome", help="preview/approve native connectomes or selected edge extraction"
    )
    for key in ("selection", "output"):
        connectome.add_argument("--" + key, type=Path, required=True)
    connectome.add_argument("--mrtrix-directory", type=Path)
    connectome.add_argument("--threads", type=int, default=2)
    connectome.add_argument("--plan", type=Path)
    connectome.add_argument("--approve-digest")


def run(args: argparse.Namespace) -> int:
    """Use the same import, validation and exclusive-export services as the GUI."""
    try:
        if args.scientific_action == "registry":
            print(json.dumps([a.to_dict() for a in ALGORITHMS.values()], indent=2))
            return 0
        if args.scientific_action == "import":
            artifact = import_artifact(
                ImportRequest(
                    args.kind,
                    args.file,
                    args.bval,
                    args.bvec,
                    args.sidecar,
                    read_json(args.declarations),
                    args.provenance,
                )
            )
            artifacts, tasks = (
                restore_selection(args.from_selection) if args.from_selection else ((), ())
            )
            if any(a.id == artifact.id for a in artifacts):
                raise ValueError("This exact input is already in the selection")
            save_selection(args.selection, (*artifacts, artifact), tasks)
            print(f"Imported {artifact.kind}: {artifact.id}; scientific acceptance unknown")
            return 0
        artifacts, tasks = restore_selection(args.selection)
        if args.scientific_action in {
            "dti",
            "csd",
            "tracking",
            "connectome",
            "anatomy",
            "sift2",
            "registration",
        }:
            from dmri_studio.core.execution import execute_plan
            from dmri_studio.core.scientific.anatomy import prepare_anatomy
            from dmri_studio.core.scientific.connectome import prepare_connectome
            from dmri_studio.core.scientific.csd import prepare_csd
            from dmri_studio.core.scientific.dti import prepare_dti
            from dmri_studio.core.scientific.registration import prepare_registration
            from dmri_studio.core.scientific.sift2 import prepare_sift2
            from dmri_studio.core.scientific.tracking import prepare_tracking

            compiler = {
                "registration": prepare_registration,
                "sift2": prepare_sift2,
                "anatomy": prepare_anatomy,
                "dti": prepare_dti,
                "csd": prepare_csd,
                "tracking": prepare_tracking,
                "connectome": prepare_connectome,
            }[args.scientific_action]
            plan = compiler(
                artifacts,
                tasks,
                args.output,
                nthreads=args.threads,
                tool_directory=args.mrtrix_directory,
            )
            if args.plan:
                save_execution_plan(plan, args.plan)
            print(plan.to_json())
            print("Approval digest: " + plan.digest)
            if args.approve_digest:
                # Convert CLI Ctrl-C into the same cooperative cancellation used
                # by the GUI: terminate/reap children and write a terminal record,
                # rather than abandon a manifest marked "running".
                cancel = Event()
                installed = current_thread() is main_thread()
                previous = signal.getsignal(signal.SIGINT)
                if installed:
                    signal.signal(signal.SIGINT, lambda *_: cancel.set())
                try:
                    result = execute_plan(
                        None,
                        plan,
                        approval=args.approve_digest,
                        progress=print,
                        cancelled=cancel.is_set,
                    )
                finally:
                    if installed:
                        signal.signal(signal.SIGINT, previous)
                print(result["status"])
                return (
                    130
                    if result["status"] == "cancelled"
                    else 0
                    if result["status"] == "validated_pending_qc"
                    else 1
                )
            return 0
        if args.tasks:
            tasks = tuple(Task.from_dict(t) for t in read_json(args.tasks.read_text()))
        evidence = None
        if args.probe_tools:
            evidence = detect_mrtrix(
                names=tuple(sorted({ALGORITHMS[t.algorithm].command for t in tasks})),
                tool_directory=args.mrtrix_directory,
            )
        proposal = build_workflow(artifacts, tasks, dependency_evidence=evidence)
        save_execution_plan(proposal, args.proposal)
        print(f"{proposal.payload['status']}; execution unavailable; proposal {args.proposal}")
        return 1 if proposal.payload["status"] == "blocked" else 0
    except (KeyboardInterrupt, ProcessingCancelled):
        print(
            "Scientific operation interrupted; inspect any saved attempt before retrying. Originals are not overwritten",
            file=sys.stderr,
        )
        return 130
    except (ValueError, OSError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        print(f"Scientific input/selection error: {exc}", file=sys.stderr)
        return 2
