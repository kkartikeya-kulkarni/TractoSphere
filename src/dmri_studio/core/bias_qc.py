"""Portable N4 QC from already verified outputs, with no extra MRI processing.

Uses the existing native-plane geometry and colormaps. PNG encoding is lossless
stdlib code; no Qt/matplotlib dependency in the processing engine. Display-only
SVG sizing preserves physical pixel aspect; saved MRI arrays are untouched.
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

from .dataset import DwiDataset
from .preview_geometry import ORIENTATIONS, plane_geometry
from .viewer_display import colors, normalize, overlay_rgba


def _png(rgb: np.ndarray) -> bytes:
    """Encode RGB pixels without a plotting/runtime dependency."""
    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    height, width, channels = rgb.shape
    if channels != 3:
        raise ValueError("Expected RGB display pixels")

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    payload = b"".join(b"\0" + rgb[y].tobytes() for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(payload))
        + chunk(b"IEND", b"")
    )


def export_bias_report(
    dataset: DwiDataset, folder: Path, manifest: dict, *, report_directory: Path | None = None
) -> tuple[Path, list[Path]]:
    """Create all-slice before/after, field and explicit mask panels with fixed windows."""
    arrays = [
        np.asarray(nib.load(folder / name).dataobj)
        for name in [
            "mean-low-b-before.nii",
            "mean-low-b-after.nii",
            "bias-field.nii",
            "fitting-mask.nii",
        ]
    ]
    before, after, field, mask = arrays
    selected = mask != 0
    # One common display window for all before/after slices. Field window is
    # independently labelled; neither uses per-slice auto-contrast.
    low, high = np.percentile(before[selected], [0.5, 99.5]).tolist()
    if high <= low:
        high = low + 1
    f_low, f_high = float(field.min()), float(field.max())
    if f_high <= f_low:
        f_high = f_low + 1e-6
    report_directory = report_directory or folder
    output = report_directory / "qc-figures"
    output.mkdir(exist_ok=False)
    pages, sections = [], []
    for orientation in ORIENTATIONS:
        geometry = plane_geometry(dataset, orientation)
        slices = dataset.shape[geometry.axis]
        aspect = geometry.aspect
        cell_width, panel_width = 784, 188
        panel_height = min(250, max(100, panel_width / aspect))
        row_height = panel_height + 60
        for start in range(0, slices, 6):
            width, height = cell_width * 2, 64 + row_height * 3
            parts = [
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
                f'<rect width="{width}" height="{height}" fill="#101821"/>',
                '<g fill="#e7edf5" font-family="sans-serif" font-size="16">',
                f'<text x="12" y="25">{html.escape(geometry.title)} · file axis {geometry.axis} · zero-based slices {start}–{min(start + 5, slices - 1)}</text>',
                f'<text x="12" y="48">DWI window [{low:.4g}, {high:.4g}] · field [{f_low:.4g}, {f_high:.4g}] · left/right/top/bottom: {" / ".join(geometry.edges)}</text>',
            ]
            for item, index in enumerate(range(start, min(start + 6, slices))):
                x, y = (item % 2) * cell_width, 64 + (item // 2) * row_height
                planes = [
                    geometry.display_plane(np.take(a, index, axis=geometry.axis)) for a in arrays
                ]
                grayscale = [colors(normalize(a, low, high), "gray") for a in planes[:2]]
                field_rgb = colors(normalize(planes[2], f_low, f_high), "viridis")
                rgba = overlay_rgba(planes[3], outline=True, opacity=0.9)
                alpha = rgba[..., 3:4] / 255
                mask_rgb = (grayscale[0] * (1 - alpha) + rgba[..., :3] * alpha).astype(np.uint8)
                parts.append(f'<text x="{x + 8}" y="{y + 17}">Slice {index}</text>')
                for panel, (title, rgb) in enumerate(
                    zip(
                        ["Before", "After (same scale)", "Bias field", "Fitting mask outline"],
                        [*grayscale, field_rgb, mask_rgb],
                        strict=True,
                    )
                ):
                    px = x + panel * 196 + 4
                    parts.append(f'<text x="{px}" y="{y + 37}" font-size="13">{title}</text>')
                    uri = "data:image/png;base64," + base64.b64encode(_png(rgb)).decode()
                    # Compute both dimensions ourselves; preserveAspectRatio=none
                    # refers only to display pixels, already corrected for spacing.
                    shown_width = min(panel_width, panel_height * aspect)
                    shown_height = shown_width / aspect
                    parts.append(
                        f'<image x="{px}" y="{y + 43}" width="{shown_width}" height="{shown_height}" preserveAspectRatio="none" href="{uri}"/>'
                    )
            parts.append("</g></svg>")
            svg = "\n".join(parts)
            path = output / f"{orientation}-{start:04d}.svg"
            with path.open("x") as stream:
                stream.write(svg)
            pages.append(path)
            sections.append(
                f'<figure><img alt="{orientation} slices {start} to {min(start + 5, slices - 1)}" src="data:image/svg+xml;base64,{base64.b64encode(svg.encode()).decode()}"/></figure>'
            )
    commands = "\n\n".join(c["display"] for c in manifest["plan"]["commands"])
    limits = "".join(
        "<li>" + html.escape(s) + "</li>" for s in manifest["plan"]["scientific_limits"]
    )
    report = report_directory / "N4_visual_QC.html"
    text = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>N4 bias-correction QC</title><style>body{{font:16px system-ui;background:#101821;color:#e7edf5;margin:28px;line-height:1.5}}img{{width:100%;height:auto}}figure{{margin:20px 0}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;padding:18px;background:#1a2532}}summary{{cursor:pointer}}h1{{font-size:30px}}</style>
<h1>N4 bias-correction QC</h1><p>Execution completed. Numerical checks passed. <strong>Scientific review required.</strong></p>
<p>Input: {html.escape(str(dataset.paths.dwi))}. All {dataset.n_volumes} volumes retained in native diffusion space. No spatial interpolation. No claims of field plausibility or mask acceptance are inferred from numerical checks.</p>
<ul>{limits}</ul><p>All native slices are shown in three orientations. The fixed before/after window is derived from the input inside the fitting mask. The field has its own explicitly labelled range. These display choices do not change image data. Inspect tissue contrast, cortical and inferior mask extent, non-brain inclusion, and whether the field tracks tissue boundaries.</p>
<details><summary>Exact local commands and versions</summary><pre>{html.escape(commands)}</pre><pre>{html.escape(json.dumps(manifest["plan"]["tools"], indent=2))}</pre></details>
<details><summary>Numerical verification, field scaling and low-b indices</summary><pre>{html.escape(json.dumps(manifest["validation"], indent=2))}</pre></details>
<details><summary>Upstream evidence and pending findings</summary><pre>{html.escape(json.dumps(manifest["plan"]["handoff"], indent=2))}</pre></details>
{"".join(sections)}<details><summary>Full N4 execution log</summary><pre>{html.escape((folder / "logs/bias.log").read_text(errors="replace"))}</pre></details></html>"""
    with report.open("x") as stream:
        stream.write(text)
    # All visual payloads are embedded; absolute provenance strings are text,
    # never external image/script dependencies.
    if "<script" in text or 'src="http' in text or 'href="file:' in text:
        raise ValueError("Unexpected external dependency in portable N4 QC")
    return report, pages
