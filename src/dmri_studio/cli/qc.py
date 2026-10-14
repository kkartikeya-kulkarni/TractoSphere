"""CLI access to the same saved-evidence QC API used by the desktop."""

import argparse
import json
from pathlib import Path

from dmri_studio.core.qc import export_report, load_catalog, record_review


def main(argv=None) -> int:
    """Inspect, export or record review without running any scientific processing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--export-report", type=Path)
    parser.add_argument("--stage")
    parser.add_argument("--artifact", type=int, default=0)
    parser.add_argument("--decision", choices=["accepted", "rejected", "needs_review"])
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--review-directory", type=Path)
    args = parser.parse_args(argv)
    try:
        catalog = load_catalog(args.catalog)
        if args.decision:
            if not args.review_directory:
                raise ValueError("--review-directory is required")
            print(
                record_review(
                    catalog,
                    args.stage,
                    args.decision,
                    args.reviewer,
                    args.reason,
                    args.review_directory,
                )
            )
        elif args.export_report:
            stage = next(s for s in catalog.to_dict()["stages"] if s["id"] == args.stage)
            print(export_report(catalog, stage["artifacts"][args.artifact], args.export_report))
        else:
            print(json.dumps(catalog.to_dict(), indent=2))
        return 0
    except (OSError, ValueError, StopIteration, IndexError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
