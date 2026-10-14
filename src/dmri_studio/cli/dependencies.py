"""Headless read-only dependency checker using the GUI's shared inventory."""

import json
import sys
from pathlib import Path

from dmri_studio.core.dependencies import FEATURES, inspect_dependencies


def add_command(commands):
    """Expose dependency inspection without Qt or software installation."""
    parser = commands.add_parser(
        "doctor", help="inspect optional scientific tools; no installation"
    )
    for family in ("mrtrix", "fsl", "ants"):
        parser.add_argument(f"--{family}-bin", type=Path, help="existing root/bin folder")
    parser.add_argument("--path-only", action="store_true", help="disable common-location search")
    parser.add_argument("--require", choices=tuple(FEATURES), action="append", default=[])
    parser.add_argument("--report-json", type=Path, help="write a NEW local JSON inventory")
    parser.add_argument(
        "--setup",
        choices=("MRtrix3", "FSL", "ANTs"),
        help="show platform-specific guided manual installation",
    )
    parser.add_argument(
        "--destination", type=Path, help="read-only space/permission check with --setup"
    )


def run(args):
    """Optional missing tools return 0; explicitly required unmet features return 1."""
    try:
        if args.report_json and (args.report_json.exists() or args.report_json.is_symlink()):
            raise FileExistsError("Setup report destination exists; choose a new file")
        report = inspect_dependencies(
            directories={"MRtrix3": args.mrtrix_bin, "FSL": args.fsl_bin, "ANTs": args.ants_bin},
            search_common=not args.path_only,
        )
        if args.destination and not args.setup:
            raise ValueError("--destination requires --setup")
        if args.setup:
            from dmri_studio.core.software_setup import destination_check, installation_guide

            guide = installation_guide(args.setup)
            guide["storage"] = destination_check(args.destination or guide["destination"], guide)
            report["installation_guidance"] = guide
            print(json.dumps(guide, indent=2))
        for tool in report["tools"]:
            build = f" / build {tool['build']}" if tool["build"] else ""
            platform = f" / {tool['platform']}" if tool["platform"] else ""
            print(
                f"{tool['family']} / {tool['name']}: {tool['state']} · version {tool['version'] or 'unknown'}{build}{platform} "
                f"· {tool['path'] or 'not found'}"
            )
            print("  " + tool["detail"])
        print("Viewer, atlas/tract editing and saved QC remain available without external tools.")
        print(
            "Exact component versions/build records are not the FSL suite version. Scientific approval remains separate."
        )
        for feature in report["features"].values():
            print(f"{feature['title']}: {feature['state']}")
            for reason in feature["reasons"]:
                print("  " + reason)
            if feature.get("additional_requirement"):
                print("  " + feature["additional_requirement"])
        for family, url in report["installation_links"].items():
            print(f"Official {family} installation: {url}")
        if args.report_json:
            with args.report_json.open("x") as f:
                json.dump(report, f, indent=2)
        return int(any(report["features"][key]["state"] == "needs_setup" for key in args.require))
    except (OSError, ValueError) as exc:
        print(f"Setup inspection failed: {exc}", file=sys.stderr)
        return 2
