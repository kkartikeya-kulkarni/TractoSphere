"""Portable DTI evidence presentation; consume recorded measurements, no fitting.

PNG encoding uses the standard library. No optional plotting dependency or MRI
arrays leave the workstation. Only representative scalar slices are embedded.
Scientific numbers remain unchanged; display ranges are explicitly labelled.
"""

from __future__ import annotations

import base64
import html
import json
import struct
import zlib
from pathlib import Path

import nibabel as nib
import numpy as np


def _png(array):
    """Encode an 8-bit grayscale display slice, never a scientific derivative."""
    array = np.ascontiguousarray(array, dtype=np.uint8)
    height, width = array.shape

    def chunk(kind, payload):
        """Emit PNG chunks with their standard CRC integrity field."""
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload))
        )

    scan = b"".join(b"\x00" + row.tobytes() for row in array)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scan))
        + chunk(b"IEND", b"")
    )


def write_report(folder: Path, result: dict, plan: dict):
    """Write an exclusive self-contained report with findings, plots and map slices."""

    def esc(value):
        """Escape filenames, provenance statements and all imported report text."""
        return html.escape(str(value))

    sections = [
        '<!doctype html><meta charset="utf-8"><title>TractoSphere DTI evidence</title>',
        "<style>body{font:16px system-ui;line-height:1.55;max-width:1100px;margin:40px auto;padding:0 24px;color:#222}h2{margin-top:40px}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:8px;border-bottom:1px solid #ddd}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}figure{display:inline-block;margin:12px;vertical-align:top}img{width:200px;image-rendering:pixelated;background:#000}figcaption{max-width:240px}svg{max-width:100%;background:#f5f5f5}details{margin:18px 0}</style>",
        "<h1>DTI reconstruction evidence</h1><p>Execution completed · Numerical verification passed · Automated QC findings recorded · <strong>Scientific review required</strong></p>",
        "<p>These are independent status levels. No automatic scientific acceptance is conferred.</p>",
        '<h2 id="inputs">Inputs and scientific scope</h2>',
    ]
    for a in plan["artifacts"]:
        sections.append(
            f"<p><strong>{esc(a['kind'])}</strong>: {esc(Path(a['path']).name)}<br>{esc(a['provenance']['statement'])}<br>Geometry: {esc(a['geometry']['shape'])}; {esc(a['geometry']['orientation'])}; mm.</p>"
        )
    sections += [
        '<h2 id="findings">Findings and unresolved limitations</h2><ul>',
        *[f"<li>{esc(s)}</li>" for s in result["findings"]],
        "</ul>",
        '<h2 id="measurements">Numerical evidence</h2>',
    ]
    for row in result["measurements"]:
        sections.append(
            f"<h3>{esc(row['task'])}</h3><table><tr><th>Measurement</th><th>Recorded value</th></tr>"
        )
        for name, value in row.items():
            if name in {"task", "per_volume", "sampling"}:
                continue
            sections.append(f"<tr><td>{esc(name)}</td><td>{esc(value)}</td></tr>")
        sections.append("</table>")
        if "sampling" in row:
            sections.append(
                "<p>Selected original-volume indices: "
                + esc(row["sampling"]["selected_original_volume_indices"])
                + ". No volume reordering or automatic rejection.</p>"
            )
        if row.get("per_volume"):
            values = np.array([v["rmse"] for v in row["per_volume"]])
            high = float(values.max()) or 1
            points = " ".join(
                f"{40 + i * 700 / max(len(values) - 1, 1):.2f},{180 - 140 * y / high:.2f}"
                for i, y in enumerate(values)
            )
            sections.append(
                f'<figure><svg width="780" height="225" viewBox="0 0 780 225" role="img" aria-label="Per selected volume residual RMSE"><polyline points="{points}" fill="none" stroke="#3366a0" stroke-width="2"/><text x="40" y="20">Residual RMSE (input signal units), maximum {high:.6g}</text><text x="40" y="210">Selected volume ordinal (original indices retained in table)</text></svg><figcaption>Predicted minus observed signal. Descriptive fit evidence; no residual acceptance threshold applied.</figcaption></figure>'
            )
            sections.append(
                "<details><summary>Per-volume residual measurements</summary><table><tr><th>Original index</th><th>b (s/mm²)</th><th>RMSE</th><th>Mean residual</th></tr>"
            )
            for v in row["per_volume"]:
                sections.append(
                    "<tr>"
                    + "".join(
                        f"<td>{esc(v[k])}</td>"
                        for k in ["original_volume_index", "b_s_mm2", "rmse", "mean_residual"]
                    )
                    + "</tr>"
                )
            sections.append("</table></details>")
    sections.append(
        '<h2 id="maps">Representative scalar maps</h2><p>Native voxel-axis-2 middle slice, with original orientation labels and affine preserved in exported files. These are display previews, not anatomical registration evidence. Robust display windows do not modify scientific values.</p>'
    )
    for export in plan["exports"]:
        name = export["destination"]
        if not any(name.endswith("-" + m + ".nii.gz") for m in ("FA", "MD", "AD", "RD")):
            continue
        image = nib.load(folder / name)
        plane = np.asarray(image.dataobj[:, :, image.shape[2] // 2], dtype=float).T[::-1]
        finite = plane[np.isfinite(plane) & (plane != 0)]
        low, high = (
            (0.0, 1.0)
            if "-FA." in name
            else (np.percentile(finite, [1, 99]) if finite.size else (0.0, 1.0))
        )
        display = (np.clip((plane - low) / max(float(high - low), 1e-12), 0, 1) * 255).astype(
            np.uint8
        )
        png = base64.b64encode(_png(display)).decode()
        units = "dimensionless" if "-FA." in name else "mm²/s"
        sections.append(
            f'<figure><img alt="{esc(name)} representative native slice" src="data:image/png;base64,{png}"><figcaption>{esc(name)}<br>Window {low:.6g}…{high:.6g} {units}; axis-2 index {image.shape[2] // 2}.</figcaption></figure>'
        )
    sections += [
        '<h2 id="reproducibility">Reproducibility</h2><p>Complete input/binary/config hashes, exact commands, selected gradients, volume mapping, timestamps, resource samples and output hashes are in manifest.json and logs/. The original scientific inputs were preserved.</p>',
        "<details><summary>Exact commands and complete numerical record</summary><pre>",
        esc("\n".join(c["display"] for c in plan["commands"])),
        "\n\n",
        esc(json.dumps(result, indent=2, allow_nan=False)),
        "</pre></details>",
        "<p>Methods: MRtrix 3.0.5 dwi2tensor and tensor2metric. See pinned adapter documentation for OLS/WLS/IWLS, coefficient ordering, negative eigenvalues and vector conventions.</p>",
    ]
    with (folder / "qc-report.html").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(sections))


def write_failure_report(folder: Path, manifest: dict):
    """Keep failed/cancelled evidence portable without promoting partial outputs."""
    from ..execution import _publish_json, hash_file

    name = "failure-report.html"
    message = html.escape(manifest.get("error", manifest["status"]))
    commands = html.escape("\n".join(c["display"] for c in manifest["plan"]["commands"]))
    with (folder / name).open("x", encoding="utf-8") as stream:
        stream.write(
            '<!doctype html><meta charset="utf-8"><title>DTI incomplete attempt</title><style>body{font:16px system-ui;max-width:1000px;margin:40px auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>DTI attempt '
            + html.escape(manifest["status"])
            + "</h1><p>Partial derivatives are not verified results and must not be consumed downstream. Source identity: "
            + str(manifest["inputs_unchanged"])
            + "</p><h2>Stopping point</h2><p>"
            + message
            + "</p><h2>Scientific status</h2><p>Numerical verification did not complete. Automated scientific QC unavailable. Scientific review required. No automatic repair, overwrite or retry occurred.</p><h2>Exact planned commands</h2><pre>"
            + commands
            + "</pre><p>See manifest.json, summary.txt and logs/ for actual completed steps, process return codes, timestamps and resource records.</p>"
        )
    evidence = [
        dict(
            kind="report",
            label="Failed/cancelled attempt evidence",
            path=name,
            sha256=hash_file(folder / name),
        )
    ]
    _publish_json(
        folder / "failure-qc-catalog.json",
        dict(
            schema_version=1,
            kind="dmri_qc_catalog",
            identity=dict(subject="Unspecified — source identity in run manifest"),
            stages=[
                dict(
                    id="dti",
                    title="Incomplete DTI attempt",
                    execution="failed",
                    technical="failed" if manifest["status"] == "failed" else "pending",
                    automated_qc="unavailable",
                    scientific_review="required",
                    findings=[manifest.get("error", manifest["status"])],
                    artifacts=evidence,
                )
            ],
        ),
    )
    return dict(
        report=name,
        report_sha256=evidence[0]["sha256"],
        catalog="failure-qc-catalog.json",
        catalog_sha256=hash_file(folder / "failure-qc-catalog.json"),
    )
