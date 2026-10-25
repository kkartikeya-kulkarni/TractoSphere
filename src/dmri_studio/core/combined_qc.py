"""Build one portable overview from verified, existing QC evidence.

This is an evidence index and visual review aid, never a scientific decision.
The complete producer catalogs and reports remain the authoritative records.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import struct
import zlib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from .process import check_cancel
from .qc import QCCatalog, load_catalog, load_review

MAX_CATALOGS = 64
MAX_FIGURES = 72
MAX_EMBEDDED_BYTES = 64 * 1024 * 1024
_SLICE = re.compile(r"^(.*?)-(axial|coronal|sagittal)-(\d+)$", re.I)
_INLINE_IMAGE = re.compile(r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+")
_REVIEW_NAME = re.compile(r"^review-[0-9a-f]{32}\.json$")


class PreviewUnavailable(ValueError):
    """Evidence is valid, but a bounded visual preview cannot be produced."""


def _explicit_subject(value: object) -> str | None:
    """Exclude producer placeholders and image basenames from identity matching."""
    subject = str(value).strip()
    if not subject or subject.lower().startswith(("unspecified", "unknown", "user-loaded")):
        return None
    if subject.lower().endswith((".nii", ".nii.gz", ".mif", ".mif.gz")):
        return None
    return subject


def discover_catalogs(folder: Path, *, cancelled: Callable[[], bool] = lambda: False) -> list[Path]:
    """Find bounded QC catalogs under an explicitly selected local folder."""
    folder = folder.expanduser().resolve(strict=True)
    if not folder.is_dir():
        raise ValueError("Choose a folder containing saved QC attempts")
    found: list[Path] = []
    visited = 0
    for current, directories, files in os.walk(folder, followlinks=False):
        check_cancel(cancelled)
        visited += 1
        if visited > 10_000:
            raise ValueError("Folder scan exceeded 10,000 directories; choose a smaller run folder")
        depth = len(Path(current).relative_to(folder).parts)
        directories[:] = sorted(d for d in directories if not d.startswith(".") and depth < 8)
        if "qc-catalog.json" in files:
            found.append(Path(current) / "qc-catalog.json")
            if len(found) > MAX_CATALOGS:
                raise ValueError(
                    f"More than {MAX_CATALOGS} QC catalogs found; choose a smaller folder"
                )
    if not found:
        raise ValueError("No qc-catalog.json files found under this folder")
    return sorted(found)


def discover_review_records(
    folder: Path, *, cancelled: Callable[[], bool] = lambda: False
) -> list[Path]:
    """Find optional human review records under the selected dataset folder."""
    found: list[Path] = []
    visited = 0
    for current, directories, files in os.walk(folder, followlinks=False):
        check_cancel(cancelled)
        visited += 1
        if visited > 10_000:
            raise ValueError("Review scan exceeded 10,000 directories; choose a smaller folder")
        depth = len(Path(current).relative_to(folder).parts)
        directories[:] = sorted(d for d in directories if not d.startswith(".") and depth < 8)
        found.extend(Path(current) / name for name in files if _REVIEW_NAME.fullmatch(name))
        if len(found) > 256:
            raise ValueError("More than 256 review records found; choose a smaller dataset folder")
    return sorted(found)


def _read_verified(catalog: QCCatalog, artifact: dict, limit: int) -> bytes:
    source = catalog.artifact_path(artifact)
    if source.stat().st_size > limit:
        raise ValueError(f"Evidence exceeds the export limit: {source.name}")
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != artifact["sha256"]:
        raise ValueError(f"QC evidence changed while exporting: {source}")
    return data


def _representative_figures(artifacts: list[dict], limit: int = 6) -> list[dict]:
    """Choose a diagnostic plot and central slices across saved figure groups."""
    figures = [item for item in artifacts if item["kind"] == "figure"]
    groups: dict[str, list[tuple[int, dict]]] = {}
    for item in figures:
        label = item["label"]
        match = _SLICE.match(label)
        key = f"{match[1]}-{match[2]}" if match else label
        groups.setdefault(key, []).append((int(match[3]) if match else -1, item))
    chosen: list[dict] = []
    for key, entries in sorted(
        groups.items(),
        key=lambda pair: (
            0 if any(word in pair[0].lower() for word in ("motion", "outlier", "residual")) else 1,
            pair[0],
        ),
    ):
        del key
        entries.sort(key=lambda entry: entry[0])
        chosen.append(entries[len(entries) // 2][1])
        if len(chosen) >= limit:
            break
    return chosen


def _png_gray(pixels) -> bytes:
    """Encode a small grayscale preview without a GUI or plotting backend."""
    height, width = pixels.shape
    rows = b"".join(b"\0" + pixels[row].tobytes() for row in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 6))
        + chunk(b"IEND", b"")
    )


def _comparison_preview(catalog: QCCatalog, artifact: dict, cancelled) -> tuple[bytes, str]:
    """Render one verified matched pair on its native grid for visual review."""
    import nibabel as nib
    import numpy as np

    from .execution import hash_file

    manifest = json.loads(_read_verified(catalog, artifact, 32 * 1024 * 1024))
    mode = manifest["modes"][0]
    left_index, right_index = mode["pairs"][0]
    images = []
    for role in (mode["left"], mode["right"]):
        source = manifest["sources"][role]
        path = Path(source["dwi"]).resolve(strict=True)
        if path.stat().st_size > 2 * 1024 * 1024 * 1024:
            raise PreviewUnavailable("Comparison image exceeds the 2 GiB preview limit")
        if hash_file(path, cancelled) != source["sha256"]["dwi"]:
            raise ValueError(f"Comparison image changed since QC: {path}")
        images.append(nib.load(str(path)))
    before, after = images
    if before.shape[:3] != after.shape[:3] or not np.allclose(
        before.affine, after.affine, rtol=0, atol=1e-3
    ):
        raise PreviewUnavailable("Comparison grid differs; no paired preview was rendered")
    if before.shape[0] * before.shape[1] > 4_000_000:
        raise PreviewUnavailable("Comparison slice exceeds the bounded preview size")
    center = before.shape[2] // 2
    panels = []
    for image, volume in ((before, left_index), (after, right_index)):
        if len(image.shape) != 4 or not 0 <= volume < image.shape[3]:
            raise ValueError("Comparison volume index is outside the source image")
        panel = np.asarray(image.dataobj[:, :, center, volume], dtype=np.float32)
        panels.append(np.rot90(panel))
    finite = np.concatenate([p[np.isfinite(p)] for p in panels])
    if finite.size == 0:
        raise PreviewUnavailable("Comparison slice contains no finite values")
    nonfinite = sum(int(np.count_nonzero(~np.isfinite(p))) for p in panels)
    low, high = np.percentile(finite, [1, 99])
    if high <= low:
        high = low + 1
    scaled = [
        np.asarray(
            np.clip((np.where(np.isfinite(p), p, low) - low) * 255 / (high - low), 0, 255),
            dtype=np.uint8,
        )
        for p in panels
    ]
    gap = np.zeros((scaled[0].shape[0], 8), dtype=np.uint8)
    pixels = np.concatenate([scaled[0], gap, scaled[1]], axis=1)
    scale = max(1, min(4, 800 // pixels.shape[1]))
    if scale > 1:
        pixels = np.repeat(np.repeat(pixels, scale, axis=0), scale, axis=1)
    preview = _png_gray(pixels)
    caption = (
        f"{mode['label']} · left/right volumes {left_index}/{right_index} · "
        f"native voxel axis-2 slice {center}; shared display scale, {nonfinite} nonfinite "
        "pixels shown black, no resampling or inference"
    )
    return preview, caption


def build_combined_report(
    catalogs: Sequence[Path],
    destination: Path,
    *,
    review_paths: Sequence[Path] = (),
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[str], None] = lambda _: None,
) -> Path:
    """Export a new self-contained HTML overview without changing source evidence."""
    if not catalogs or len(catalogs) > MAX_CATALOGS:
        raise ValueError(f"Choose between 1 and {MAX_CATALOGS} QC catalogs")
    destination = destination.expanduser().absolute()
    if destination.exists():
        raise FileExistsError(f"Report already exists: {destination}")
    if not destination.parent.is_dir():
        raise ValueError("Choose an existing output folder")
    snapshots: list[QCCatalog] = []
    explicit_subjects: set[str] = set()
    for path in catalogs:
        check_cancel(cancelled)
        progress(f"Verifying {path.parent.name}…")
        snapshot = load_catalog(path, cancelled=cancelled, progress=progress)
        snapshots.append(snapshot)
        subject = _explicit_subject(snapshot.to_dict()["identity"]["subject"])
        if subject:
            explicit_subjects.add(subject)
    if len(explicit_subjects) > 1:
        raise ValueError(
            "QC catalogs name different subjects; choose one participant's attempt folder"
        )
    subject = next(iter(explicit_subjects), "Subject unspecified in source catalogs")
    by_digest: dict[str, list[QCCatalog]] = {}
    for catalog in snapshots:
        by_digest.setdefault(catalog.digest, []).append(catalog)
    reviews_by_stage: dict[tuple[str, str], list[dict]] = {}
    for review_path in review_paths:
        check_cancel(cancelled)
        if review_path.stat().st_size > 1024 * 1024:
            raise ValueError(f"Review record exceeds 1 MiB: {review_path}")
        metadata = json.loads(review_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError(f"Review record must be a JSON object: {review_path}")
        if metadata.get("kind") != "dmri_human_qc_review":
            continue
        candidates = by_digest.get(metadata.get("catalog_sha256"), [])
        if not candidates:
            continue
        source = str(Path(metadata.get("catalog", "")).expanduser().absolute())
        exact = [candidate for candidate in candidates if str(candidate.path) == source]
        if not exact and len(candidates) != 1:
            raise ValueError(f"Review matches multiple copied catalogs: {review_path}")
        catalog = (exact or candidates)[0]
        record = load_review(catalog, review_path, cancelled=cancelled)
        reviews_by_stage.setdefault((catalog.digest, record["stage"]), []).append(record)
    body: list[str] = []
    summary: list[str] = []
    not_run: list[str] = []
    embedded_bytes = 0
    embedded_figures = 0
    omitted_figures = 0
    seen_inline_reports: set[Path] = set()
    for catalog in snapshots:
        check_cancel(cancelled)
        data = catalog.to_dict()
        identity = ", ".join(f"{key}: {value}" for key, value in data["identity"].items())
        for stage in data["stages"]:
            title = stage.get("title") or stage.get("name") or stage["id"]
            if stage["execution"] in {"pending", "skipped", "unknown"}:
                not_run.append(
                    f"<li>{html.escape(catalog.path.parent.name)} · {html.escape(title)} "
                    f"({html.escape(stage['execution'])} in the saved attempt)</li>"
                )
                continue
            anchor = f"step-{len(body) + 1}"
            stage_reviews = reviews_by_stage.get((catalog.digest, stage["id"]), [])
            review_status = stage["scientific_review"]
            if stage_reviews:
                review_status += " · local declarations: " + ", ".join(
                    sorted({record["decision"] for record in stage_reviews})
                )
            summary.append(
                f"<tr><td>{html.escape(catalog.path.parent.name)}</td>"
                f'<td><a href="#{anchor}">{html.escape(title)}</a></td>'
                + "".join(
                    f"<td>{html.escape(str(stage[key]))}</td>"
                    for key in ("execution", "technical", "automated_qc")
                )
                + f"<td>{html.escape(review_status)}</td>"
                + "</tr>"
            )
            findings = "".join(f"<li>{html.escape(str(item))}</li>" for item in stage["findings"])
            review_section = ""
            if stage_reviews:
                records = "".join(
                    f"<li>{html.escape(record['decision'])} · reviewer declaration: "
                    f"{html.escape(record['reviewer'])} · {html.escape(record['recorded_utc'])} "
                    f"· {html.escape(record['reason'])} · record SHA-256 "
                    f"{record['review_sha256']}</li>"
                    for record in stage_reviews
                )
                review_section = (
                    "<h3>Saved local human review declarations</h3>"
                    "<p>These declarations match the exact catalog evidence. The toolbox does not "
                    f"authenticate reviewer identity or resolve conflicting decisions.</p><ul>{records}</ul>"
                )
            figures: list[str] = []
            selected = _representative_figures(stage["artifacts"])
            total_figures = sum(item["kind"] == "figure" for item in stage["artifacts"])
            omitted_figures += total_figures - len(selected)
            for entry in selected:
                check_cancel(cancelled)
                source = catalog.artifact_path(entry)
                size = source.stat().st_size
                if embedded_figures >= MAX_FIGURES or embedded_bytes + size > MAX_EMBEDDED_BYTES:
                    omitted_figures += 1
                    continue
                raw = _read_verified(catalog, entry, MAX_EMBEDDED_BYTES)
                mime = {
                    ".svg": "image/svg+xml",
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                }.get(source.suffix.lower())
                if mime is None:
                    omitted_figures += 1
                    continue
                encoded = base64.b64encode(raw).decode("ascii")
                figures.append(
                    f'<figure><img loading="lazy" src="data:{mime};base64,{encoded}" '
                    f'alt="{html.escape(entry["label"], quote=True)}"><figcaption>'
                    f"{html.escape(entry['label'])} · SHA-256 {entry['sha256']}</figcaption></figure>"
                )
                embedded_bytes += len(raw)
                embedded_figures += 1
            # Some producers embed their maps only in their portable HTML report.
            if not figures:
                for entry in stage["artifacts"]:
                    if entry["kind"] != "report":
                        continue
                    source = catalog.artifact_path(entry)
                    if source in seen_inline_reports or source.stat().st_size > 5 * 1024 * 1024:
                        continue
                    seen_inline_reports.add(source)
                    report_text = _read_verified(catalog, entry, 5 * 1024 * 1024).decode(
                        "utf-8", errors="replace"
                    )
                    for match in list(_INLINE_IMAGE.finditer(report_text))[:4]:
                        uri = match.group()
                        size = len(uri) * 3 // 4
                        if (
                            embedded_figures >= MAX_FIGURES
                            or embedded_bytes + size > MAX_EMBEDDED_BYTES
                        ):
                            omitted_figures += 1
                            continue
                        figures.append(
                            f'<figure><img loading="lazy" src="{uri}" alt="Producer report image">'
                            f"<figcaption>Image embedded in verified producer report · SHA-256 "
                            f"{entry['sha256']}</figcaption></figure>"
                        )
                        embedded_bytes += size
                        embedded_figures += 1
                    break
            if not figures:
                for entry in stage["artifacts"]:
                    if entry["kind"] != "comparison":
                        continue
                    check_cancel(cancelled)
                    try:
                        preview, caption = _comparison_preview(catalog, entry, cancelled)
                    except PreviewUnavailable as exc:
                        figures.append(
                            f"<p>Native comparison preview unavailable: {html.escape(str(exc))}. "
                            "Inspect the original verified comparison in Quality Control.</p>"
                        )
                        break
                    if (
                        embedded_figures >= MAX_FIGURES
                        or embedded_bytes + len(preview) > MAX_EMBEDDED_BYTES
                    ):
                        omitted_figures += 1
                        break
                    encoded = base64.b64encode(preview).decode("ascii")
                    figures.append(
                        f'<figure><img loading="lazy" src="data:image/png;base64,{encoded}" '
                        f'alt="Verified native before and after comparison"><figcaption>'
                        f"{html.escape(caption)} · comparison SHA-256 {entry['sha256']}"
                        "</figcaption></figure>"
                    )
                    embedded_bytes += len(preview)
                    embedded_figures += 1
                    break
            evidence = []
            for entry in stage["artifacts"]:
                if entry["kind"] in {"metrics", "history", "report", "comparison"}:
                    evidence.append(
                        f"<li>{html.escape(entry['kind'])}: {html.escape(entry['label'])} "
                        f"· SHA-256 {html.escape(entry['sha256'])} "
                        f"· {html.escape(str(catalog.artifact_path(entry)))}</li>"
                    )
            recorded_blocks = []
            for entry in stage["artifacts"]:
                if entry["kind"] not in {"metrics", "history", "log"}:
                    continue
                source = catalog.artifact_path(entry)
                limit = 512 * 1024 if entry["kind"] != "log" else 128 * 1024
                if source.stat().st_size > limit:
                    recorded_blocks.append(
                        f"<p>{html.escape(entry['label'])} is larger than the document limit; "
                        "inspect the unchanged source evidence listed below.</p>"
                    )
                    continue
                recorded = _read_verified(catalog, entry, limit).decode("utf-8", errors="replace")
                if entry["kind"] != "log":
                    try:
                        recorded = json.dumps(json.loads(recorded), indent=2, ensure_ascii=False)
                    except ValueError:
                        pass
                recorded_blocks.append(
                    f"<details><summary>{html.escape(entry['label'])} "
                    f"({html.escape(entry['kind'])})</summary>"
                    f"<pre>{html.escape(recorded)}</pre></details>"
                )
            body.append(
                f'<section id="{anchor}"><h2>{html.escape(title)}</h2>'
                f"<p><strong>Attempt:</strong> {html.escape(catalog.path.parent.name)} · "
                f"<strong>Identity as recorded:</strong> {html.escape(identity)}</p>"
                f"<p>Execution: {html.escape(stage['execution'])} · Technical: "
                f"{html.escape(stage['technical'])} · Automated QC: "
                f"{html.escape(stage['automated_qc'])} · Scientific review: "
                f"{html.escape(stage['scientific_review'])}</p>"
                f"<h3>Recorded findings</h3><ul>{findings}</ul>"
                f"{review_section}"
                f"<h3>Representative saved images and graphs</h3>"
                f"{''.join(figures) or '<p>No embeddable figure was recorded for this stage.</p>'}"
                f"{''.join(recorded_blocks)}"
                f"<details><summary>Complete source evidence locations and hashes</summary>"
                f"<p>Catalog: {html.escape(str(catalog.path))} · SHA-256 {catalog.digest}</p>"
                f"<ul>{''.join(evidence)}</ul></details></section>"
            )
    warning = (
        "Some catalogs have unspecified or file-derived subject identity; verify their exact input association "
        "in the original run manifests before interpreting this collection."
        if any(_explicit_subject(c.to_dict()["identity"]["subject"]) is None for c in snapshots)
        else "The reported subject identity is copied from producer catalogs, not independently authenticated."
    )
    not_run_section = (
        "<section><h2>Saved plan stages not run in those attempts</h2><p>These entries "
        "were recorded as pending, skipped or unknown, and are not included as performed "
        f"processing steps above.</p><ul>{''.join(not_run)}</ul></section>"
        if not_run
        else ""
    )
    document = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>TractoSphere combined QC · {html.escape(subject)}</title>"
        "<style>body{font:16px system-ui,sans-serif;background:#f4f6f9;color:#152238;margin:0}"
        "main{max-width:1180px;margin:auto;padding:24px}header,section{background:#fff;border:1px solid #d8e0ea;"
        "border-radius:12px;padding:22px;margin:18px 0}h1{font-size:2rem}h2{border-bottom:1px solid #d8e0ea;"
        "padding-bottom:8px}table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #dde3eb;"
        "padding:8px;text-align:left;vertical-align:top}.scroll{overflow:auto}figure{margin:18px 0}"
        "img{max-width:100%;height:auto;border:1px solid #d8e0ea}figcaption{font-size:.85rem;color:#48576d}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.85rem;background:#f4f6f9;padding:12px}"
        "details{margin:12px 0}summary{cursor:pointer;font-weight:600}.warning{color:#8b3f00}"
        "a{color:#155d9f}a:hover{text-decoration:underline}"
        "@media print{body{background:white}section,header{break-inside:avoid}}</style>"
        f"<main><header><h1>Combined quality-control review · {html.escape(subject)}</h1>"
        f"<p>Generated {datetime.now(UTC).isoformat()} from {len(snapshots)} verified QC catalogs. "
        "This report compiles recorded evidence; it does not rerun processing or grant scientific approval.</p>"
        f"<p>{sum(map(len, reviews_by_stage.values()))} exact-bound local human review declarations "
        "included; reviewer identity is not authenticated.</p>"
        f'<p class="warning">{html.escape(warning)}</p>'
        f"<p>{embedded_figures} representative images/graphs embedded. "
        f"{omitted_figures} additional saved figures omitted from this overview; "
        "inspect their unchanged producer catalogs for complete slice coverage. "
        "Original step reports, logs and manifests remain canonical.</p></header>"
        '<section><h2>Processing and QC status</h2><div class="scroll"><table><thead><tr>'
        "<th>Attempt</th><th>Stage</th><th>Execution</th><th>Technical</th>"
        "<th>Automated QC</th><th>Scientific review</th></tr></thead>"
        f"<tbody>{''.join(summary)}</tbody></table></div></section>"
        f"{not_run_section}{''.join(body)}</main></html>"
    )
    check_cancel(cancelled)
    with destination.open("xb") as stream:
        try:
            stream.write(document.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            destination.unlink(missing_ok=True)
            raise
    progress(f"Combined QC report saved: {destination}")
    return destination


def build_combined_report_from_folder(
    folder: Path,
    destination: Path,
    *,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[str], None] = lambda _: None,
) -> Path:
    """Discover one participant's attempt catalogs, then export their evidence."""
    folder = folder.expanduser().resolve(strict=True)
    return build_combined_report(
        discover_catalogs(folder, cancelled=cancelled),
        destination,
        review_paths=discover_review_records(folder, cancelled=cancelled),
        cancelled=cancelled,
        progress=progress,
    )
