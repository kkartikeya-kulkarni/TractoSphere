"""Portable anatomical QC from saved outputs; no segmentation or registration rerun."""

import base64
import html
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from .anatomy import image3d
from .qc import artifact, load_catalog
from .qc_figures import native_montages


def export_anatomical_qc(
    dataset, folder: Path, manifest: dict, *, report_directory: Path | None = None
) -> Path:
    """Show all native slices and exact evidence with scientific review pending."""
    prepared = manifest["plan"]["anatomical_operation"] == "anatomy"
    destination = report_directory or folder
    if report_directory is not None:
        destination.mkdir(exist_ok=False)
    stage = "anatomy" if prepared else "alignment"
    title = (
        "Anatomical extraction and white-matter candidate"
        if prepared
        else "Separate 3D DWI–T1 rigid and BBR alignment"
    )
    figures = []
    if manifest["status"] == "validated_pending_qc":
        reference, values = image3d(folder / "t1w-brain.nii.gz")
        geometry = SimpleNamespace(
            shape=reference.shape,
            affine=reference.affine,
            affine_source="sform" if int(reference.header["sform_code"]) else "qform",
        )
        positive = values[values > 0]
        window = (
            [0, float(np.percentile(positive, 99.5))]
            if positive.size
            else [float(values.min()), float(values.max())]
        )
        _, wm = image3d(folder / "wm-mask.nii.gz", reference)
        if prepared:
            _, original = image3d(folder / "input-t1.nii.gz", reference)
            _, mask = image3d(folder / "brain-mask.nii.gz", reference)
            panels = [
                {
                    "array": original,
                    "title": "Input T1 + extraction outline",
                    "range": window,
                    "mask": mask,
                },
                {
                    "array": values,
                    "title": "Extracted T1 + WM outline",
                    "range": window,
                    "mask": wm,
                },
                {
                    "array": image3d(folder / "tissue_pve_2.nii.gz", reference)[1],
                    "title": "FAST WM partial volume",
                    "range": [0, 1],
                    "colormap": "gray",
                },
            ]
            if manifest["plan"]["anatomy_options"].get("mode") == "raw_t1":
                _, restored = image3d(folder / "registration-reference.nii.gz", reference)
                panels[1]["array"] = restored
                panels[1]["title"] = "Restored T1 + WM outline"
                _, field = image3d(folder / "tissue_bias.nii.gz", reference)
                panels.append(
                    {
                        "array": field,
                        "title": "FAST anatomical bias field",
                        "range": [float(field.min()), float(field.max())],
                        "colormap": "viridis",
                    }
                )
        else:
            _, rigid = image3d(folder / "mean-b0-t1-rigid.nii.gz", reference)
            _, bbr = image3d(folder / "mean-b0-t1-bbr.nii.gz", reference)
            dwi_window = [
                0,
                float(np.percentile(np.concatenate((rigid[rigid > 0], bbr[bbr > 0])), 99.5)),
            ]
            panels = [
                {
                    "array": values,
                    "title": "T1 reference + WM outline",
                    "range": window,
                    "mask": wm,
                },
                {
                    "array": rigid,
                    "title": "Rigid mean low-b + WM outline",
                    "range": dwi_window,
                    "mask": wm,
                },
                {
                    "array": bbr,
                    "title": "BBR mean low-b + WM outline",
                    "range": dwi_window,
                    "mask": wm,
                },
            ]
        figures = native_montages(geometry, panels, destination / "qc-figures", stage)
    report = destination / "anatomy-QC.html"
    evidence = folder / "anatomy-manifest.json"
    logs = sorted((folder / "logs").glob("*.log"))
    body = [
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>"
        + html.escape(title)
        + "</title><style>body{background:#101821;color:#e7edf5;font:16px system-ui;margin:24px}img{width:100%;max-width:1300px}pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer;margin:16px 0}</style></head><body>",
        f"<h1>{title}</h1>",
        f"<p>Execution/technical status: {html.escape(manifest['status'])}. Scientific review required.</p>",
        "<p>Native 4D diffusion image and gradients are unchanged. Registration images, when present, are separate resampled 3D QC derivatives. Mask sizes and tissue probabilities do not establish anatomical correctness.</p>",
    ]
    if "error" in manifest:
        body.append("<p>" + html.escape(manifest["error"]) + "</p>")
    for figure in figures:
        uri = "data:image/svg+xml;base64," + base64.b64encode(figure.read_bytes()).decode()
        body.append(
            f"<figure><img alt='{html.escape(figure.stem)}' src='{uri}'><figcaption>{html.escape(figure.stem)}</figcaption></figure>"
        )
    body.append(
        "<details><summary>Exact commands, versions, hashes, history and numerical checks</summary><pre>"
        + html.escape(json.dumps(manifest, indent=2))
        + "</pre></details>"
    )
    for log in logs:
        body.append(
            "<details><summary>"
            + html.escape(log.name)
            + "</summary><pre>"
            + html.escape(log.read_text(errors="replace"))
            + "</pre></details>"
        )
    with report.open("x") as stream:
        stream.write("\n".join(body) + "</body></html>")
    success = manifest["status"] == "validated_pending_qc"
    catalog = {
        "schema_version": 1,
        "kind": "dmri_qc_catalog",
        "identity": {
            "subject": dataset.paths.dwi.name,
            "session": "Explicit source association in processing history",
        },
        "stages": [
            {
                "id": stage,
                "title": title,
                "execution": "completed" if success else "failed",
                "technical": "passed" if success else "failed",
                "automated_qc": "pending",
                "scientific_review": "required",
                "findings": [
                    "Inspect extraction boundaries, inferior extent and white-matter segmentation. No automatic anatomical acceptance.",
                    "Only 3D registration QC images are resampled; native diffusion volumes and rotated gradients are preserved.",
                ]
                + ([manifest["error"]] if "error" in manifest else []),
                "artifacts": [
                    artifact(report, "report", title),
                    artifact(evidence, "history", "Exact processing and validation record"),
                ]
                + [artifact(p, "figure", p.stem) for p in figures]
                + [artifact(p, "log", p.name) for p in logs],
            }
        ],
    }
    target = destination / "qc-catalog.json"
    with target.open("x") as stream:
        json.dump(catalog, stream, indent=2)
    load_catalog(target)
    return target
