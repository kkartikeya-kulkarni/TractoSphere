"""Headless, read-only command for the selected FALCON H1 NWB structure."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dmri_studio.core.bci import inspect_falcon_h1
from dmri_studio.core.bci_inventory import inspect_h1_inventory


def add_command(commands: argparse._SubParsersAction) -> None:
    bci = commands.add_parser("bci", help="inspect supported BCI research recordings")
    tasks = bci.add_subparsers(dest="bci_command", required=True)
    inspect = tasks.add_parser("inspect-h1", help="read-only FALCON H1 NWB structure summary")
    inspect.add_argument("--nwb", type=Path, required=True, help="selected modern H1 .nwb file")
    inventory = tasks.add_parser("inventory-h1", help="compare explicitly listed H1 recordings")
    inventory.add_argument("--manifest", type=Path, required=True, help="versioned JSON manifest")
    inventory.add_argument(
        "--output", type=Path, help="create a new JSON report outside source data"
    )


def run(args: argparse.Namespace) -> int:
    try:
        if args.bci_command == "inspect-h1":
            report = inspect_falcon_h1(args.nwb)
        else:
            report = inspect_h1_inventory(args.manifest)
            if args.output is not None:
                output = args.output.expanduser().resolve(strict=False)
                protected = {Path(report["manifest"]["path"])}
                protected.update(
                    Path(row["source"]["path"]) for row in report["recordings"] if "source" in row
                )
                if output in protected:
                    raise ValueError(
                        "inventory report output must not replace a manifest or recording"
                    )
                with output.open("x", encoding="utf-8") as handle:
                    json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
                    handle.write("\n")
                return 0 if report["complete"] else 1
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"dmri-studio: H1 inspection failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0 if args.bci_command == "inspect-h1" or report["complete"] else 1
