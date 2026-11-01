"""Headless, read-only command for the selected FALCON H1 NWB structure."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dmri_studio.core.bci import inspect_falcon_h1


def add_command(commands: argparse._SubParsersAction) -> None:
    bci = commands.add_parser("bci", help="inspect supported BCI research recordings")
    tasks = bci.add_subparsers(dest="bci_command", required=True)
    inspect = tasks.add_parser("inspect-h1", help="read-only FALCON H1 NWB structure summary")
    inspect.add_argument("--nwb", type=Path, required=True, help="selected modern H1 .nwb file")


def run(args: argparse.Namespace) -> int:
    try:
        report = inspect_falcon_h1(args.nwb)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"dmri-studio: H1 inspection failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0
