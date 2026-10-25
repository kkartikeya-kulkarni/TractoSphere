"""Run one deterministic, disjoint shard of the installed-wheel GUI test suite."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--junitxml", default="gui-tests.xml")
    parser.add_argument("--list", action="store_true", help="Show selected files without running")
    args = parser.parse_args()
    if args.count < 1 or not 0 <= args.index < args.count:
        parser.error("Require 0 <= index < count")

    root = Path(__file__).resolve().parents[1]
    files = sorted((root / "tests/gui").glob("test_*.py"))
    selected = [path for number, path in enumerate(files) if number % args.count == args.index]
    if not selected:
        parser.error("Shard has no GUI test files")
    print(f"GUI shard {args.index + 1}/{args.count}: {len(selected)} files", flush=True)
    if args.list:
        for path in selected:
            print(path.relative_to(root).as_posix())
        return 0
    command = [sys.executable, "-m", "pytest", *map(str, selected), f"--junitxml={args.junitxml}"]
    return subprocess.call(command, cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())
