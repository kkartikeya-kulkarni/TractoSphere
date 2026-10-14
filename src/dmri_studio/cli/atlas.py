"""Headless atlas inspection, region export and filtering of existing tractograms."""

import json
import sys
from pathlib import Path


def add_command(commands):
    """Expose the same core atlas implementation without importing Qt or VTK."""
    parser = commands.add_parser("atlas", help="inspect/import an anatomical atlas")
    parser.add_argument("image", type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--label-table", type=Path)
    parser.add_argument("--kind", choices=("auto", "labels", "probability"), default="auto")
    parser.add_argument("--threshold", type=float, default=0.25)
    parser.add_argument("--probability-scale", type=float, choices=(1, 100), default=1)
    parser.add_argument(
        "--same-space",
        action="store_true",
        help="explicitly confirm shared anatomical space; does not bypass known mismatches",
    )
    parser.add_argument(
        "--transform", type=Path, help="verified source-world to reference-world affine JSON"
    )
    parser.add_argument("--report-json", type=Path)
    parser.add_argument(
        "--export-dir", type=Path, help="NEW directory for region masks and metadata"
    )
    parser.add_argument("--tract", type=Path)
    parser.add_argument(
        "--tract-same-space",
        action="store_true",
        help="confirm tract subject and reference space from provenance",
    )
    parser.add_argument("--include", type=int, action="append", default=[])
    parser.add_argument("--exclude", type=int, action="append", default=[])
    parser.add_argument("--any", action="store_true", help="any inclusion ROI; default all")
    parser.add_argument("--tract-output", type=Path, help="NEW .tck/.trk selected group")


def run(args):
    """Validate inputs before exporting; never overwrite an existing output."""
    try:
        from dmri_studio.core.atlas import Atlas, filter_streamlines
        from dmri_studio.core.scene import ImageVolume, TractGroup, save_new_image

        if bool(args.tract) != bool(args.tract_output):
            raise ValueError("--tract and --tract-output must be supplied together")
        if args.include or args.exclude:
            if not args.tract:
                raise ValueError("ROI filtering requires --tract and --tract-output")
        destinations = [args.report_json, args.export_dir, args.tract_output]
        if args.tract_output:
            destinations.append(Path(str(args.tract_output) + ".json"))
        for destination in filter(None, destinations):
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(f"Choose a new destination: {destination}")
        ref = ImageVolume(args.reference)
        atlas = Atlas(
            args.image,
            ref,
            table=args.label_table,
            kind=args.kind,
            threshold=args.threshold,
            probability_scale=args.probability_scale,
            same_space=args.same_space,
            transform=args.transform,
        )
        regions = list(atlas.regions())
        by_id = {r["label_id"]: r for r in regions}
        if set(args.include) & set(args.exclude):
            raise ValueError("A region cannot be both Include and Exclude")
        for role, identifiers in (("Include", args.include), ("Exclude", args.exclude)):
            for identifier in identifiers:
                if identifier not in by_id:
                    raise ValueError(f"Region {identifier} is absent/empty at this threshold")
                by_id[identifier]["role"] = role
        payload = dict(
            schema="tractosphere.atlas/1",
            regions=[dict(r) for r in regions],
            warnings=atlas.warnings,
            omitted_labels=atlas.omitted,
        )
        if args.tract:
            if not args.tract_same_space:
                raise ValueError(
                    "Verify tract subject/reference space and supply --tract-same-space"
                )
            group = TractGroup.load(args.tract)
            selected = filter_streamlines(group, regions, ref, not args.any)
            if not selected:
                raise ValueError("No streamlines meet the ROI roles; no tract file exported")
            group.deleted = set(range(len(group.streamlines))) - selected
            group.export(args.tract_output, ref)
            payload["tract_filter"] = dict(
                source=str(args.tract),
                output=str(args.tract_output),
                source_ids=sorted(selected),
                require_all=not args.any,
            )
            with open(str(args.tract_output) + ".json", "x") as f:
                json.dump(payload["tract_filter"], f, indent=2)
        if args.export_dir:
            args.export_dir.mkdir(parents=True, exist_ok=False)
            for region in regions:
                path = args.export_dir / f"region-{region['label_id']}.nii.gz"
                save_new_image(path, region["data"], ref)
                with (args.export_dir / f"region-{region['label_id']}.json").open("x") as f:
                    json.dump(dict(schema="tractosphere.roi/1", roi=dict(region)), f, indent=2)
        if args.report_json:
            with args.report_json.open("x") as f:
                json.dump(payload, f, indent=2)
        print(json.dumps(payload, indent=2))
        return 0
    except (ValueError, OSError, ImportError, KeyError) as exc:
        print(f"Atlas import failed: {exc}", file=sys.stderr)
        return 2
