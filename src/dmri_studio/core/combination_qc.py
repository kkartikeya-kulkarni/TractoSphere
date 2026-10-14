"""Saved-evidence QC for optional HCP-compatible matched AP/PA derivatives."""

import base64
import html
import json
from pathlib import Path

import nibabel as nib
import numpy as np

from .execution import hash_file
from .qc import artifact, load_catalog
from .qc_figures import native_montages


def export_combination_qc(dataset, folder: Path, manifest: dict) -> Path:
    """Reuse the native viewer and explicit maps; do not repeat upstream QC."""
    success = manifest["status"] == "validated_pending_qc"
    artifacts, figures = [], []
    if success:
        parent = nib.load(dataset.paths.dwi, mmap="r", keep_file_open=True)
        output = nib.load(folder / "combined.nii.gz", mmap="r", keep_file_open=True)
        rows = manifest["validation"]["volumes"]
        # Representative images complement the complete interactive volume map.
        selected = {0, max(range(len(rows)), key=lambda i: rows[i]["A_to_combined"]["RMSE"])}
        weighted = [
            i for i, row in enumerate(rows) if row["nominal_b"] > dataset.validation.policy.b0_max
        ]
        if weighted:
            selected.add(weighted[0])
        for i in sorted(selected):
            a, b = manifest["plan"]["pairing"]["pairs"][i]
            va, vb, vc = (
                np.asarray(parent.dataobj[..., a], np.float32),
                np.asarray(parent.dataobj[..., b], np.float32),
                np.asarray(output.dataobj[..., i], np.float32),
            )
            window = [
                float(min(va.min(), vb.min(), vc.min())),
                float(np.percentile(np.r_[va.ravel(), vb.ravel(), vc.ravel()], 99.5)),
            ]
            window[1] = max(window[1], window[0] + 1e-6)
            span = max(float(np.percentile(abs(vc - va), 99)), 1e-6)
            panels = [
                {"array": va, "title": f"Parent A volume {a}", "range": window},
                {"array": vb, "title": f"Parent B volume {b}", "range": window},
                {"array": vc, "title": f"Combined volume {i}", "range": window},
                {
                    "array": vc - va,
                    "title": "Combined minus parent A",
                    "range": [-span, span],
                    "colormap": "blue-white-red",
                },
            ]
            figures += native_montages(dataset, panels, folder / "qc-figures", f"pair-{i:04d}")
        sources = {
            "parent": {
                "dwi": str(dataset.paths.dwi),
                "bval": str(dataset.paths.bval),
                "bvec": str(dataset.paths.bvec),
            },
            "combined": {
                "dwi": str(folder / "combined.nii.gz"),
                "bval": str(folder / "combined.bval"),
                "bvec": str(folder / "combined.bvec"),
            },
        }
        for source in sources.values():
            source["sha256"] = {k: hash_file(Path(v)) for k, v in source.items()}
        modes = [
            {
                "label": f"Parent {label} / combined",
                "left": "parent",
                "right": "combined",
                "pairs": [
                    [pair[side], i] for i, pair in enumerate(manifest["plan"]["pairing"]["pairs"])
                ],
                "description": "Explicit original-to-derivative correspondence. Effective rotated encoding differs; no repeated correction. Parent is retained.",
            }
            for side, label in enumerate(("A", "B"))
        ]
        comparison = folder / "combination-comparison.json"
        comparison.write_text(
            json.dumps(
                {
                    "notice": "HCP-compatible matched measurements. Original parent retained; combined images and effective encodings require scientific review.",
                    "sources": sources,
                    "modes": modes,
                },
                indent=2,
            )
        )
        artifacts.append(artifact(comparison, "comparison", "Inspect every parent/combined volume"))
    report = folder / "combination-QC.html"
    sections = [
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Matched AP/PA combination QC</title><style>body{background:#101821;color:#e7edf5;font:16px system-ui;margin:24px}img{width:100%;max-width:1600px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body><h1>Optional matched AP/PA combination</h1>",
        f"<p>Status: {html.escape(manifest['status'])}. Scientific review pending. The parent dataset is unchanged.</p>",
    ]
    for line in manifest["plan"]["scientific_limits"]:
        sections.append("<p>" + html.escape(line) + "</p>")
    for figure in figures:
        uri = "data:image/svg+xml;base64," + base64.b64encode(figure.read_bytes()).decode()
        sections.append(
            f"<figure><img alt='{html.escape(figure.stem)}' src='{uri}'><figcaption>{html.escape(figure.stem)}</figcaption></figure>"
        )
    sections.append(
        "<details><summary>All commands, software versions, mappings, replacement locations and numerical results</summary><pre>"
        + html.escape(json.dumps(manifest, indent=2))
        + "</pre></details>"
    )
    logs = sorted((folder / "logs").glob("*.log"))
    for log in logs:
        sections.append(
            "<details><summary>"
            + html.escape(log.name)
            + "</summary><pre>"
            + html.escape(log.read_text(errors="replace"))
            + "</pre></details>"
        )
    with report.open("x") as stream:
        stream.write("\n".join(sections) + "</body></html>")
    artifacts = (
        [
            artifact(report, "report", "Portable combination QC"),
            artifact(
                folder / "combination-manifest.json",
                "history",
                "Exact combination execution and verification",
            ),
        ]
        + artifacts
        + [artifact(p, "figure", p.stem) for p in figures]
        + [artifact(p, "log", p.name) for p in logs]
    )
    spec = manifest["plan"]["pairing"]["specification"]
    catalog = {
        "schema_version": 1,
        "kind": "dmri_qc_catalog",
        "identity": {"subject": spec["subject"], "session": spec["session"]},
        "stages": [
            {
                "id": "combination",
                "title": "Optional matched AP/PA derivative",
                "execution": "completed" if success else "failed",
                "technical": "passed" if success else "failed",
                "automated_qc": "pending",
                "scientific_review": "required",
                "findings": manifest["plan"]["scientific_limits"]
                + ([manifest["error"]] if "error" in manifest else []),
                "artifacts": artifacts,
            }
        ],
    }
    path = folder / "qc-catalog.json"
    with path.open("x") as stream:
        json.dump(catalog, stream, indent=2)
    load_catalog(path)
    return path
