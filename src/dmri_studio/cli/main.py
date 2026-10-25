"""Inspect one external diffusion dataset without importing a GUI.

Run ``dmri-studio load --dwi data.nii --bval bvals --bvec bvecs`` from
any directory. Images remain at their source locations. This adapter presents
the core's metadata-and-samples report; it performs no extra scientific checks.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dmri_studio import __version__
from dmri_studio.core.dataset import DatasetPaths, DwiDataset, SourceFingerprint
from dmri_studio.core.loading import DatasetLoader
from dmri_studio.core.validation import ValidationPolicy, ValidationReport
from dmri_studio.core.workflow import OPERATIONS, ProcessingHistory, build_plan, export_plan

_LOG = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    """Describe the currently implemented CLI; GUI dispatch remains optional and lazily imported."""
    parser = argparse.ArgumentParser(
        prog="dmri-studio",
        description="Inspect and process diffusion MRI with explicit scientific review.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("gui", help="launch the optional desktop application")
    viewer = commands.add_parser(
        "view", help="open the scientific 3D/4D MRI and tractography viewer"
    )
    viewer.add_argument("image", type=Path, nargs="?", help="3D/4D scalar NIfTI")
    viewer.add_argument(
        "--tract",
        type=Path,
        action="append",
        default=[],
        help="matching-space .tck/.trk; repeat for groups",
    )
    viewer.add_argument("--workspace", type=Path, help="saved viewer workspace JSON")
    load = commands.add_parser("load", help="validate and summarize one diffusion dataset")
    load.add_argument("--dwi", type=Path, required=True, help="4D .nii or .nii.gz image")
    load.add_argument("--bval", type=Path, required=True, help="matching b-value text file")
    load.add_argument("--bvec", type=Path, required=True, help="matching b-vector text file")
    load.add_argument("--json", type=Path, help="optional acquisition JSON sidecar")
    load.add_argument(
        "--bvec-layout",
        choices=("auto", "3xN", "Nx3"),
        default="auto",
        help="gradient layout; auto interprets ambiguous 3x3 tables as FSL rows",
    )
    load.add_argument(
        "--report-json",
        type=Path,
        help="write the complete report to a NEW JSON file; existing files are never overwritten",
    )
    plan = commands.add_parser(
        "plan", help="inspect inputs and propose operations; no processing commands run"
    )
    plan.add_argument("--dwi", type=Path, required=True)
    plan.add_argument("--bval", type=Path, required=True)
    plan.add_argument("--bvec", type=Path, required=True)
    plan.add_argument("--json", type=Path)
    plan.add_argument("--bvec-layout", choices=("auto", "3xN", "Nx3"), default="auto")
    plan.add_argument(
        "--history", type=Path, help="explicit history JSON; missing history remains unconfirmed"
    )
    plan.add_argument(
        "--operation",
        action="append",
        choices=tuple(OPERATIONS),
        required=True,
        help="repeat to select multiple operations",
    )
    plan.add_argument("--plan-json", type=Path, help="export the proposal to a NEW JSON file")
    from .execution import add_commands

    add_commands(commands)
    from .atlas import add_command

    add_command(commands)
    from .dependencies import add_command as add_dependency_command

    add_dependency_command(commands)
    from .scientific import add_command as add_scientific_command

    add_scientific_command(commands)
    from .batch import add_command as add_batch_command

    add_batch_command(commands)
    from .roi_filter import add_command as add_roi_filter_command

    add_roi_filter_command(commands)
    return parser


def _history_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON keys rather than silently discard history evidence."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate history key: {key}")
        result[key] = value
    return result


def _reject_history_constant(value: str) -> None:
    """History is strict JSON, never NaN or Infinity."""
    raise ValueError(f"Invalid history JSON constant: {value}")


def _plan_main(args: argparse.Namespace) -> int:
    """Use the existing loader and core planner, with no Qt or tool execution.

    Exit 0 means scientifically eligible as a proposal, not approved to run.
    Exit 1 means invalid inputs or blocked/uncertain proposal, 2 invalid request.
    """
    dataset = None
    try:
        history = ProcessingHistory()
        history_fingerprint = None
        if args.history is not None:
            history_fingerprint = SourceFingerprint.capture(args.history)
            if history_fingerprint.size > 1_048_576:
                raise ValueError("History JSON exceeds the 1 MiB limit.")
            payload = json.loads(
                args.history.read_text(encoding="utf-8"),
                object_pairs_hook=_history_pairs,
                parse_constant=_reject_history_constant,
            )
            history = ProcessingHistory.from_dict(payload)
        if len(args.operation) != len(set(args.operation)):
            raise ValueError("Select each operation at most once.")
        result = DatasetLoader().load(
            DatasetPaths(args.dwi, args.bval, args.bvec, args.json),
            policy=ValidationPolicy(bvec_layout=args.bvec_layout),
        )
        dataset = result.dataset
        _print_report(result.report)
        if result.report.cancelled:
            return 130
        if dataset is None:
            return 1
        if history_fingerprint is not None and not history_fingerprint.unchanged(args.history):
            raise ValueError("History file changed during import. Review it and retry.")
        plan = build_plan(dataset, history, tuple(args.operation))
        payload = plan.to_dict()
        print(f"Workflow proposal: {payload['status']}. Planning only; no commands executed.")
        for reason in payload["reasons"]:
            print(f"  {reason}")
        for node in payload["nodes"]:
            print(f"  {node['title']}: {node['status']}")
            for reason in node["reasons"]:
                print(f"    {reason}")
        if args.plan_json is not None:
            saved = export_plan(
                plan, args.plan_json, extra_sources=(args.history,) if args.history else ()
            )
            print(f"Proposal JSON: {saved}")
        return 0 if payload["status"] == "eligible" else 1
    except (ValueError, TypeError) as exc:
        print(f"dmri-studio: planning request error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"dmri-studio: planning I/O error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Planning cancelled.", file=sys.stderr)
        return 130
    except Exception:
        _LOG.exception("Unexpected planning error. No processing commands were executed.")
        return 1
    finally:
        if dataset is not None:
            dataset.close()


def _report_destination(destination: Path, paths: DatasetPaths) -> Path:
    """Reject input aliases and existing destinations before any report write."""
    destination = destination.expanduser().absolute()
    resolved = destination.resolve()
    for source in (paths.dwi, paths.bval, paths.bvec, paths.json):
        if source is None:
            continue
        source = Path(source).expanduser()
        if resolved == source.resolve():
            raise ValueError("Report destination must not overwrite an input file or its symlink.")
        # samefile catches hardlinks whose resolved path names are different.
        try:
            same_file = destination.samefile(source)
        except (FileNotFoundError, OSError):
            same_file = False
        if same_file:
            raise ValueError("Report destination must not overwrite a hardlink to an input file.")
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"Report destination already exists; choose a new file: {destination}")
    if not destination.parent.is_dir():
        raise ValueError(f"Report parent directory does not exist: {destination.parent}")
    return destination


def _write_report(destination: Path, report: ValidationReport) -> None:
    """Publish a complete report without replacing any concurrently created file.

    A temporary file in the destination directory keeps publication on one
    filesystem. Linking its complete contents is atomic and fails if the target
    appeared after our earlier check. Imaging sources are never opened for write.
    """
    payload = report.to_json(indent=2) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _volume_ranges(indices: Sequence[int]) -> str:
    """Render zero-based core indices as compact, explicitly one-based ranges."""
    values = sorted(set(int(index) + 1 for index in indices))
    if not values:
        return ""
    ranges: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    visible = ", ".join(ranges[:12])
    if len(ranges) > 12:
        visible += f", … ({len(values)} affected volumes; full list in JSON report)"
    return visible


def _number(value: Any) -> str:
    """Format scalar metadata compactly without rounding stored values."""
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _print_dataset(dataset: DwiDataset, report: ValidationReport) -> None:
    """Present meaningful metadata while keeping the full report machine-readable."""
    summary = dataset.summary()
    shape = " × ".join(str(length) for length in dataset.shape)
    print(f"Shape: {shape} (X × Y × Z × volumes)")
    print(f"Storage datatype: {dataset.storage_dtype}")
    sizes_mm = dataset.voxel_sizes_mm()
    if sizes_mm is None:
        print(
            f"Voxel spacing: {' × '.join(map(_number, dataset.voxel_sizes_native))} "
            f"(native spatial units: {dataset.spatial_units}; millimetres unavailable)"
        )
    else:
        print(f"Voxel spacing: {' × '.join(map(_number, sizes_mm))} mm")
    print(
        f"Affine: {dataset.affine_source}; file-axis codes: {' / '.join(map(str, dataset.axis_codes))}"
    )
    if dataset.affine_source == "fallback":
        print("Orientation: anatomical orientation is unverified (fallback affine).")
    # Summary keys may add new metadata, but output intentionally excludes large
    # affine/gradient arrays: all evidence remains in the optional JSON report.
    metadata = summary.get("metadata", {})
    for key, label in (
        ("nifti_version", "NIfTI version"),
        ("effective_scaling", "Effective scaling"),
        ("scaling", "Scaling"),
    ):
        if key in metadata:
            value = metadata[key]
            print(
                f"{label}: {json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value}"
            )
    b0_count = int(dataset.gradients.b0_mask(b0_max=report.policy.b0_max).sum())
    print(
        f"Volumes: {dataset.n_volumes}; b0-like: {b0_count}; "
        f"diffusion-weighted: {dataset.n_volumes - b0_count} "
        f"(b0-like means b ≤ {_number(report.policy.b0_max)} s/mm²)"
    )
    print("Descriptive b-value groups (s/mm²; counts are volumes, not unique directions):")
    # Use the validated table's group definitions, never independently cluster
    # the b-values in the presentation layer.
    weighted_group_index = 0
    for group in dataset.gradients.shells(
        b0_max=report.policy.b0_max,
        max_span=report.policy.shell_span,
    ):
        if not group.is_b0:
            weighted_group_index += 1
        label = "b0-like" if group.is_b0 else f"Weighted group {weighted_group_index}"
        print(
            f"  {label}: median {_number(group.b_median)}, "
            f"range {_number(group.b_min)}–{_number(group.b_max)}, "
            f"{group.count} volumes"
        )
    print(
        f"Default preview volume: {dataset.default_preview_volume() + 1} (one-based; preview GUI is later)"
    )
    if "sample_minimum" in metadata and "sample_maximum" in metadata:
        print(
            f"Sampled scaled intensity range: {_number(metadata['sample_minimum'])} "
            f"to {_number(metadata['sample_maximum'])}"
        )


def _print_report(report: ValidationReport) -> None:
    """Print every check and remedy; severity stays exactly as the core decided."""
    counts = report.counts()
    print(
        "Validation: "
        + ", ".join(
            f"{counts.get(status, 0)} {status.upper()}"
            for status in ("pass", "info", "warn", "fail", "skipped")
        )
    )
    for check in report.checks:
        print(f"[{check.status.upper()}] {check.code}: {check.message}")
        if check.code == "F05" and check.observed:
            for source_path in check.observed:
                print(f"  Present, unused: {source_path}")
        if check.volume_indices:
            print(f"  Affected volumes (one-based): {_volume_ranges(check.volume_indices)}")
        if check.remediation:
            print(f"  Action: {check.remediation}")
    if report.status() == "incomplete":
        print(
            "Coverage: validation incomplete; the check rows above show what was examined. "
            "Full voxel scan not performed."
        )
        print("Completion: incomplete; the required load checks did not all finish.")
    else:
        print(f"Coverage: {report.coverage_message()}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run CLI loading, returning 0/1/2/130 for success/data/usage/cancellation."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # Returning rather than leaking SystemExit keeps the adapter scriptable;
        # the console entry point and __main__ turn the result into process status.
        return int(exc.code or 0)
    if args.command == "roi-filter":
        from .roi_filter import run

        return run(args)
    if args.command == "batch":
        from .batch import run

        return run(args)
    if args.command == "scientific":
        from .scientific import run

        return run(args)
    if args.command == "doctor":
        from .dependencies import run

        return run(args)
    if args.command == "atlas":
        from .atlas import run

        return run(args)
    if args.command == "view":
        if bool(args.image) == bool(args.workspace) or (args.workspace and args.tract):
            print("Supply an image with optional --tract files, OR --workspace", file=sys.stderr)
            return 2
        try:
            from dmri_studio.gui.app import main as gui_main
        except ModuleNotFoundError:
            print("Install dmri-studio[gui] for the viewer", file=sys.stderr)
            return 2
        return gui_main(args)
    if args.command == "gui":
        try:
            from dmri_studio.gui.app import main as gui_main
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.startswith("PySide6"):
                print(
                    "Install desktop support with: python -m pip install 'dmri-studio[gui]'",
                    file=sys.stderr,
                )
                return 2
            raise
        return gui_main()
    if args.command == "plan":
        return _plan_main(args)
    if args.command in {
        "prepare",
        "execute",
        "qc",
        "combined-qc",
        "inspect-run",
        "inspect-review",
        "project",
    }:
        from .execution import processing_main

        return processing_main(args)
    paths = DatasetPaths(dwi=args.dwi, bval=args.bval, bvec=args.bvec, json=args.json)
    destination: Path | None = None
    if args.report_json is not None:
        try:
            destination = _report_destination(args.report_json, paths)
        except (ValueError, OSError, RuntimeError) as exc:
            print(f"dmri-studio: report destination error: {exc}", file=sys.stderr)
            return 2

    dataset: DwiDataset | None = None
    try:
        result = DatasetLoader().load(paths, policy=ValidationPolicy(bvec_layout=args.bvec_layout))
        dataset = result.dataset
        report = result.report
        if report.cancelled:
            print("Load cancelled.", file=sys.stderr)
        elif report.can_load():
            print(
                "Dataset loaded with warnings." if report.status() == "warn" else "Dataset loaded."
            )
        else:
            print("Could not load dataset.")
        for label, path in (
            ("DWI", paths.dwi),
            ("b-values", paths.bval),
            ("b-vectors", paths.bvec),
            ("JSON sidecar", paths.json),
        ):
            print(
                f"{label}: {Path(path).expanduser().absolute() if path is not None else 'not provided'}"
            )
        if dataset is not None:
            _print_dataset(dataset, report)
        _print_report(report)
        if destination is not None:
            _write_report(destination, report)
            print(f"JSON report: {destination}")
        if report.cancelled:
            return 130
        return 0 if report.can_load() else 1
    except KeyboardInterrupt:
        print("Load cancelled (Ctrl-C).", file=sys.stderr)
        return 130
    except OSError as exc:
        print(f"dmri-studio: I/O error: {exc}", file=sys.stderr)
        return 1
    except Exception:
        _LOG.exception(
            "Unexpected loader error. No input files were modified; inspect the details below."
        )
        return 1
    finally:
        if dataset is not None:
            dataset.close()
