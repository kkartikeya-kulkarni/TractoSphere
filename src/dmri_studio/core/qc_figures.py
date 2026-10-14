"""Reusable display-only native QC montages, using the existing viewer geometry."""

import base64
import html
import textwrap
from pathlib import Path

import numpy as np

from .bias_qc import _png
from .preview_geometry import ORIENTATIONS, plane_geometry
from .viewer_display import colors, normalize, overlay_rgba


def native_montages(dataset, panels: list[dict], directory: Path, prefix: str) -> list[Path]:
    """Render all slices in all planes with fixed labelled windows and exact indices.

    Each panel supplies a 3D array, title, range, optional colormap and optional
    mask outline. Nonfinite pixels are explicitly magenta; source arrays remain
    unchanged. No anatomical acceptance or image correction occurs here.
    """
    if not 1 <= len(panels) <= 4:
        raise ValueError("Choose one to four comparable montage panels")
    for panel in panels:
        if panel["array"].shape != dataset.shape[:3]:
            raise ValueError("Montage array is not on the declared native grid")
    directory.mkdir(exist_ok=True)
    paths = []
    for orientation in ORIENTATIONS:
        geometry = plane_geometry(dataset, orientation)
        count = dataset.shape[geometry.axis]
        panel_w, cell_w = 188, 196 * len(panels)
        panel_h = min(250, max(100, panel_w / geometry.aspect))
        title_lines = [textwrap.wrap(panel["title"], width=26) or [""] for panel in panels]
        image_y = 40 + 14 * max(map(len, title_lines))
        row_h = panel_h + image_y + 15
        for start in range(0, count, 6):
            width, height = cell_w * 2, 75 + 3 * row_h
            pieces = [
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
                f'<rect width="{width}" height="{height}" fill="#101821"/>',
                '<g font-family="sans-serif" font-size="14" fill="#e7edf5">',
                f'<text x="10" y="24">{html.escape(prefix)} · {geometry.title} · file axis {geometry.axis} · zero-based indices</text>',
                f'<text x="10" y="47">Left/right/top/bottom: {" / ".join(geometry.edges)}. Nonfinite pixels: magenta.</text>',
            ]
            for item, index in enumerate(range(start, min(start + 6, count))):
                x, y = item % 2 * cell_w, 75 + item // 2 * row_h
                pieces.append(f'<text x="{x + 5}" y="{y + 15}">Slice {index}</text>')
                for column, panel in enumerate(panels):
                    values = geometry.display_plane(
                        np.take(panel["array"], index, axis=geometry.axis)
                    )
                    finite = np.isfinite(values)
                    # Only display pixels use a finite stand-in; diagnostic
                    # invalid counts and source arrays are never repaired.
                    display = np.where(finite, values, panel["range"][0])
                    rgb = colors(normalize(display, *panel["range"]), panel.get("colormap", "gray"))
                    if "mask" in panel:
                        mask = geometry.display_plane(
                            np.take(panel["mask"], index, axis=geometry.axis)
                        )
                        overlay = overlay_rgba(mask, outline=True, opacity=0.9)
                        alpha = overlay[..., 3:4] / 255
                        rgb = (rgb * (1 - alpha) + overlay[..., :3] * alpha).astype(np.uint8)
                    rgb[~finite] = [255, 0, 255]
                    px = x + column * 196 + 4
                    low, high = panel["range"]
                    for line_index, line in enumerate(title_lines[column]):
                        pieces.append(
                            f'<text x="{px}" y="{y + 32 + line_index * 14}" font-size="12">{html.escape(line)}</text>'
                        )
                    pieces.append(
                        f'<text x="{px}" y="{y + image_y - 5}" font-size="12">Range [{low:.3g}, {high:.3g}]</text>'
                    )
                    uri = "data:image/png;base64," + base64.b64encode(_png(rgb)).decode()
                    shown_w = min(panel_w, panel_h * geometry.aspect)
                    pieces.append(
                        f'<image x="{px}" y="{y + image_y}" width="{shown_w}" height="{shown_w / geometry.aspect}" preserveAspectRatio="none" href="{uri}"/>'
                    )
            pieces.append("</g></svg>")
            path = directory / f"{prefix}-{orientation}-{start:04d}.svg"
            with path.open("x") as stream:
                stream.write("\n".join(pieces))
            paths.append(path)
    return paths
