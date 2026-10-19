"""Image-only registered-atlas evidence, full-range montages and immutable reviews.

The same voxel-to-display mapping and label compositing feed native viewer and
headless reports. This module neither registers images nor loads tractograms.
"""

from __future__ import annotations

import colorsys
import html
import json
import shutil
from pathlib import Path

import nibabel as nib
import numpy as np
from PIL import Image, ImageDraw

from .execution import ExecutionPlan, _environment_contract, _publish_json, hash_file
from .preview_geometry import ORIENTATIONS, plane_geometry
from .process import check_cancel
from .scene import ImageVolume
from .scientific.dti import _fresh, _revision
from .scientific.planning import build_workflow
from .scientific.records import read_json
from .storage import require_local_processing


def label_rgba(labels, opacity=0.35, visible=None, outline=False):
    """Exact label IDs; boundaries are display-only 2D transitions, never mask edits."""
    ids = np.unique(labels)
    enabled = set(ids) if visible is None else set(visible)
    rgba = np.zeros((*labels.shape, 4), dtype="u1")
    boundary = np.zeros(labels.shape, dtype=bool)
    if outline:
        boundary[1:, :] |= labels[1:, :] != labels[:-1, :]
        boundary[:-1, :] |= labels[:-1, :] != labels[1:, :]
        boundary[:, 1:] |= labels[:, 1:] != labels[:, :-1]
        boundary[:, :-1] |= labels[:, :-1] != labels[:, 1:]
        boundary[[0, -1], :] = True
        boundary[:, [0, -1]] = True
    for value in ids:
        if value == 0 or value not in enabled:
            continue
        mask = labels == value
        if outline:
            mask &= boundary
        rgba[mask, :3] = (
            np.array(colorsys.hsv_to_rgb((int(value) * 0.61803398875) % 1, 0.65, 0.95)) * 255
        )
        rgba[mask, 3] = round(255 * opacity)
    return rgba


def prepare_qc(
    artifacts,
    tasks,
    output_directory,
    *,
    nthreads=2,
    tool_directory=None,
    cancelled=lambda: False,
    progress=lambda _: None,
):
    from .scientific.registration import LIMITS, image_qc

    task = tasks[0]
    artifacts = _fresh(
        tuple(a for a in artifacts if a.id in task.inputs.values()), cancelled, progress
    )
    proposal = build_workflow(artifacts, tasks, verify_sources=False).to_dict()
    errors = [i["message"] for i in proposal["issues"] if i["severity"] == "error"]
    if errors:
        raise ValueError("; ".join(errors))
    inventory = {a.id: a.to_dict() for a in artifacts}
    atlas, reference = inventory[task.inputs["atlas"]], inventory[task.inputs["reference"]]
    dec, refdec = atlas["declarations"], reference["declarations"]
    if any(
        dec.get(key) != refdec.get(key)
        for key in ("subject_id", "session_id", "acquisition_id", "space_id")
    ) or not dec.get("subject_id"):
        raise ValueError(
            "Atlas and diffusion reference subject/session/acquisition/space must match"
        )
    refrecord = reference["metadata"].get("reference_record")
    if not refrecord or refrecord["dwi"]["declarations"].get("processing_state") != "preprocessed":
        raise ValueError(
            "QC background must be a verified low-b reference from the selected preprocessed DWI"
        )
    hashes = {s["path"]: s["sha256"] for a in artifacts for s in a.payload["sources"]}
    refhash = hash_file(Path(reference["path"]), cancelled)
    provenance = {}
    producer_checks = {}
    if dec.get("registration_manifest"):
        from .runs import inspect_run

        path = Path(dec["registration_manifest"])
        run = inspect_run(path, cancelled=cancelled, progress=progress)
        p = run["record"]["plan"]
        if (
            run["status"] != "validated_pending_qc"
            or p["kind"] != "registration_execution_plan"
            or p.get("operation")
        ):
            raise ValueError(
                "Atlas registration producer is not a verified completed spatial operation"
            )
        if run["record"]["outputs"].get(Path(atlas["path"]).name, {}).get("sha256") != hash_file(
            Path(atlas["path"])
        ):
            raise ValueError("Atlas not identified by registration producer")
        provenance = p
        producer_checks = run["record"].get("validation", {}).get("checks", {})
        hashes[str(path)] = hash_file(path)
        hashes.update(p["extra_input_hashes"])
        if p["reference"]["sha256"] != refhash:
            provenance = {}  # requires explicit relation below, not matching shape alone
    if not provenance:
        relation = atlas["metadata"].get("qc_association")
        if (
            not relation
            or relation.get("reference_sha256") != refhash
            or not relation.get("review_notes")
            or not relation.get("evidence_hashes")
            or relation.get("status") != "reviewed"
        ):
            raise ValueError(
                "Registration used another reference or imported atlas lacks provenance: provide hash-bound qc_association establishing its relation to this preprocessed reference"
            )
        registration_reference = Path(relation["registration_reference"])
        if hash_file(registration_reference) != relation.get("registration_reference_sha256"):
            raise ValueError("Original registration reference changed")
        a, b = ImageVolume(registration_reference), ImageVolume(Path(reference["path"]))
        if a.shape != b.shape or not np.allclose(a.affine, b.affine, atol=1e-4):
            raise ValueError(
                "Different registration/QC grids need explicit resampling; no identity mapping is applied"
            )
        provenance = relation
        hashes[str(registration_reference)] = hash_file(registration_reference)
    stats = image_qc(atlas["path"], reference["path"], atlas["path"], True)
    stats["registration_output_checks"] = producer_checks
    stats["coverage_caveat"] = (
        "Absent/lost labels can reflect source coverage, field of view or sampling. Anatomy must be reviewed; labels are never manufactured."
    )
    folder = Path(output_directory).absolute()
    if folder.exists() or not folder.parent.is_dir():
        raise ValueError("QC uses a new immutable output directory")
    require_local_processing([folder, *hashes])
    from .registration_resources import registration_resources, require_registration_resources

    reservation = registration_resources(tasks, artifacts)
    require_registration_resources(reservation, folder)
    estimate = reservation["memory_bytes"]
    ref_image = ImageVolume(Path(reference["path"]))
    image_outputs = []
    for orientation in ORIENTATIONS:
        axis = plane_geometry(ref_image, orientation).axis
        image_outputs.extend(
            f"images/{orientation}-{start:04d}.png" for start in range(0, ref_image.shape[axis], 16)
        )
        image_outputs.append(f"images/{orientation}-outline.png")
    return ExecutionPlan(
        dict(
            kind="registration_execution_plan",
            operation="atlas_qc",
            artifacts=[a.to_dict() for a in artifacts],
            tasks=[task.to_dict()],
            output_directory=str(folder),
            tools={},
            environment=_environment_contract(cancelled),
            commands=[],
            outputs=[
                "scientific-selection.json",
                "qc-metrics.json",
                "qc-report.html",
                "qc-catalog.json",
                "atlas-qc.json",
                *image_outputs,
            ],
            extra_input_hashes=hashes,
            software_hashes={},
            parameters=dict(
                nthreads=nthreads,
                estimated_working_bytes=estimate,
                resource_reservation=reservation,
                storage_margin_bytes=5 * 1024**3,
            ),
            atlas=atlas,
            reference=reference,
            registration_provenance=provenance,
            technical=stats,
            scientific_limits=LIMITS,
            build=_revision(),
            compiler="tractosphere.registered-atlas-qc/1",
            implementation_hashes={
                str(p): hash_file(p)
                for p in (
                    Path(__file__),
                    Path(__file__).parent / "registration_resources.py",
                    Path(__file__).parent / "registration_review.py",
                )
            },
            mrtrix_directory=str(tool_directory) if tool_directory else None,
        )
    )


def slice_snapshot(
    reference,
    data,
    labels,
    orientation,
    index,
    window,
    *,
    opacity=0.35,
    outline=False,
    visible=None,
):
    g = plane_geometry(reference, orientation)
    values = g.display_plane(np.take(data, index, axis=g.axis))
    parcels = g.display_plane(np.take(labels, index, axis=g.axis))
    gray = (np.clip((values - window[0]) / max(window[1] - window[0], 1e-12), 0, 1) * 255).astype(
        "u1"
    )
    rgba = label_rgba(parcels, opacity, visible, outline)
    rgb = np.repeat(gray[..., None], 3, axis=-1).astype(float)
    alpha = rgba[..., 3:4] / 255.0
    rgb = (rgb * (1 - alpha) + rgba[..., :3] * alpha).astype("u1")
    width = 320
    height = max(100, round(width / g.aspect))
    rendered = Image.fromarray(rgb).resize((width, height), Image.Resampling.NEAREST)
    canvas = Image.new("RGB", (width + 36, height + 70), (20, 23, 28))
    canvas.paste(rendered, (18, 42))
    draw = ImageDraw.Draw(canvas)
    pos = np.array([(n - 1) / 2 for n in reference.shape[:3]])
    pos[g.axis] = index
    world = reference.world(pos)
    draw.text(
        (5, 3),
        f"{orientation} | slice {index + 1}/{reference.shape[g.axis]} | native axis {g.axis}",
        fill="white",
    )
    draw.text((5, 18), "RAS mm: " + ", ".join(f"{v:.1f}" for v in world), fill="white")
    draw.text((width // 2, 28), g.edges[2], fill="white")
    draw.text((width // 2, height + 43), g.edges[3], fill="white")
    draw.text((2, height // 2 + 40), g.edges[0], fill="white")
    draw.text((width + 20, height // 2 + 40), g.edges[1], fill="white")
    draw.text(
        (5, height + 57),
        f"Window {window[0]:.3g}..{window[1]:.3g} | opacity {opacity:.2f} | {'outline' if outline else 'labels'}",
        fill="white",
    )
    return canvas, dict(
        orientation=orientation,
        index_zero_based=index,
        native_axis=g.axis,
        center_ras_mm=world.tolist(),
        edges=list(g.edges),
        angle_from_cardinal_degrees=g.angle,
        window=list(window),
        opacity=opacity,
        outline=outline,
    )


def generate_qc(folder, plan, cancelled=lambda: False, progress=lambda _: None):
    from .scientific.registration import publish_qc
    from .viewer_display import robust_range

    reference = ImageVolume(Path(plan["reference"]["path"]))
    data = reference.volume()
    labels = np.asanyarray(nib.load(plan["atlas"]["path"]).dataobj)
    window = robust_range(data)
    images = folder / "images"
    images.mkdir()
    pages = []
    slice_records = []
    for orientation in ORIENTATIONS:
        axis = plane_geometry(reference, orientation).axis
        # Every slice is captured, including empty inferior/superior boundaries.
        for start in range(0, reference.shape[axis], 16):
            tiles = []
            for index in range(start, min(start + 16, reference.shape[axis])):
                check_cancel(cancelled)
                tile, meta = slice_snapshot(reference, data, labels, orientation, index, window)
                tiles.append(tile)
                slice_records.append(meta)
            w, h = tiles[0].size
            montage = Image.new("RGB", (w * 4, h * 4), (20, 23, 28))
            for j, tile in enumerate(tiles):
                montage.paste(tile, ((j % 4) * w, (j // 4) * h))
            path = images / f"{orientation}-{start:04d}.png"
            montage.save(path)
            pages.append(
                dict(
                    path=str(path.relative_to(folder)),
                    sha256=hash_file(path),
                    orientation=orientation,
                    first_slice=start,
                    last_slice=start + len(tiles) - 1,
                )
            )
            progress(
                f"QC {orientation}: exported slices {start + 1}–{start + len(tiles)} / {reference.shape[axis]}"
            )
        tile, meta = slice_snapshot(
            reference,
            data,
            labels,
            orientation,
            reference.shape[axis] // 2,
            window,
            outline=True,
            opacity=0.85,
        )
        path = images / f"{orientation}-outline.png"
        tile.save(path)
        pages.append(
            dict(
                path=str(path.relative_to(folder)),
                sha256=hash_file(path),
                orientation=orientation,
                outline=True,
            )
        )
    present, counts = np.unique(labels, return_counts=True)
    stats = dict(
        plan["technical"],
        label_distribution={str(int(k)): int(v) for k, v in zip(present, counts, strict=True)},
        nonempty_regions=int(np.count_nonzero(present)),
        all_native_slices_captured=True,
    )
    bundle = dict(
        schema="tractosphere.registered-atlas-qc/1",
        subject_id=plan["atlas"]["declarations"]["subject_id"],
        session_id=plan["atlas"]["declarations"].get("session_id", ""),
        acquisition_id=plan["atlas"]["declarations"]["acquisition_id"],
        reference=plan["reference"],
        atlas=plan["atlas"],
        registration=plan["registration_provenance"],
        technical=stats,
        images=pages,
        slices=slice_records,
        window=list(window),
        opacity=0.35,
        scientific_acceptance="unassigned",
        evidence_hashes=plan["extra_input_hashes"],
        limitations=plan["scientific_limits"],
    )
    _publish_json(folder / "atlas-qc.json", bundle)
    product = dict(
        kind="registration_qc",
        path=str(folder / "atlas-qc.json"),
        sha256=hash_file(folder / "atlas-qc.json"),
        label="Registered atlas on preprocessed mean-b0",
    )
    result = dict(
        execution="completed",
        numerical_verification="passed",
        scientific_review="QC_REVIEW_REQUIRED",
        scientific_acceptance="unassigned",
        checks=stats,
        products=[product],
        findings=plan["scientific_limits"],
    )
    publish_qc(folder, plan, result, bundle["subject_id"])
    # The report is new, not an original image. Append gallery before manifest hashes publish.
    with (folder / "qc-report.html").open("a") as stream:
        stream.write(
            "<h2>Actual atlas overlays — full native slice range</h2><p>Native-plane orientations; no display resampling of scientific arrays. Every slice is included. Montages are scientific exports, not application screenshots.</p>"
        )
        stream.write(
            '<p><a href="atlas-qc.json">Complete machine-readable identities, transforms and evidence hashes</a></p><details><summary>Registration provenance and diffusion reference</summary><pre>'
            + html.escape(
                json.dumps(
                    dict(
                        subject=bundle["subject_id"],
                        session=bundle["session_id"],
                        acquisition=bundle["acquisition_id"],
                        reference=bundle["reference"],
                        atlas=bundle["atlas"],
                        registration=bundle["registration"],
                        evidence_hashes=bundle["evidence_hashes"],
                    ),
                    indent=2,
                )
            )
            + "</pre></details>"
        )
        for page in pages:
            stream.write(
                '<figure><a href="'
                + html.escape(page["path"])
                + '"><img width="100%" src="'
                + html.escape(page["path"])
                + '"></a><figcaption>'
                + html.escape(page["path"])
                + "</figcaption></figure>"
            )
    # Catalog report hash follows the final report; write was exclusive, now update only unpublished catalog.
    catalog = read_json((folder / "qc-catalog.json").read_text())
    catalog["stages"][0]["artifacts"][0]["sha256"] = hash_file(folder / "qc-report.html")
    (folder / "qc-catalog.json").write_text(json.dumps(catalog, indent=2))
    return result


def verify_bundle(path, cancelled=lambda: False):
    path = Path(path).resolve(strict=True)
    record = read_json(path.read_text())
    if record.get("schema") != "tractosphere.registered-atlas-qc/1":
        raise ValueError("Not a registered-atlas QC bundle")
    hashes = {str(path): hash_file(path), **record["evidence_hashes"]}
    # Human decisions also bind the visible report text and numerical summary,
    # not only its underlying atlas and montage pixels.
    for name in ("qc-report.html", "qc-metrics.json"):
        evidence = path.parent / name
        hashes[str(evidence)] = hash_file(evidence, cancelled)
    for item in record["images"]:
        target = (path.parent / item["path"]).resolve(strict=True)
        if not target.is_relative_to(path.parent):
            raise ValueError("QC image escaped its bundle")
        hashes[str(target)] = item["sha256"]
    for p, digest in hashes.items():
        check_cancel(cancelled)
        if hash_file(Path(p), cancelled) != digest:
            raise ValueError("QC evidence changed: " + p)
    return record, hashes


def review_status(path):
    """Native viewer and downstream preparation use the same authoritative state."""
    from .registration_review import bundle_review, registration_eligibility

    state = bundle_review(path)
    if state.get("evidence"):
        record, _ = verify_bundle(path)
        atlas = record["atlas"]
        atlas["declarations"]["registration_qc"] = str(Path(path).resolve())
        state = registration_eligibility(atlas, record["reference"])
    return (
        state["review_applicability"]
        if state["review_applicability"] in {"STALE_EVIDENCE", "CONFLICT", "IDENTITY_CONFLICT"}
        else state["human_review_decision"]
    )


def record_decision(path, decision, reviewer, comments):
    """Append an exact-evidence v2 review without altering historical records."""
    from .registration_review import record_review

    return record_review(path, decision, reviewer, comments)


def batch_qc_rows(folder, cancelled=lambda: False):
    """Reverify review applicability on request, never rerun a scientific command."""
    from .batch.engine import read_journal

    rows = []
    for key, node in read_journal(folder)["nodes"].items():
        check_cancel(cancelled)
        if node["node"]["adapter"] != "registration":
            continue
        row = dict(
            key=key,
            subject_id=node["acquisition"]["subject_id"],
            session_id=node["acquisition"]["session_id"],
            acquisition_id=node["acquisition"]["acquisition_id"],
            execution=node["status"],
            review="NOT_APPLICABLE",
            manifest=node.get("manifest"),
            findings=node.get("error", ""),
            qc_bundle=None,
        )
        if node.get("manifest"):
            path = Path(node["manifest"]).parent / "atlas-qc.json"
            if path.is_file() and node["status"] in {"COMPLETED", "SKIPPED_VERIFIED"}:
                row["qc_bundle"] = str(path)
                try:
                    bundle, _ = verify_bundle(path, cancelled)
                    row["review"] = review_status(path)
                    row["findings"] = bundle["technical"]
                except (ValueError, OSError) as exc:
                    row["review"] = "STALE_EVIDENCE"
                    row["findings"] = str(exc)
            elif node["status"] in {"COMPLETED", "SKIPPED_VERIFIED"}:
                row["review"] = "NEEDS_REVIEW"
        rows.append(row)
    return rows


def export_batch_qc(folder, destination, cancelled=lambda: False):
    """Portable image-only report copies QC evidence, never MRI/tractography datasets."""
    import csv

    destination = Path(destination)
    destination.mkdir(exist_ok=False)
    rows = batch_qc_rows(folder, cancelled)
    links = []
    for i, row in enumerate(rows):
        check_cancel(cancelled)
        bundle = row["qc_bundle"]
        if bundle and row["review"] != "STALE_EVIDENCE":
            source = Path(bundle).parent
            target = destination / f"case-{i:04d}"
            target.mkdir()
            shutil.copytree(source / "images", target / "images")
            for name in ("atlas-qc.json", "qc-metrics.json", "qc-report.html"):
                shutil.copyfile(source / name, target / name)
            if (source / "reviews").is_dir():
                shutil.copytree(source / "reviews", target / "reviews")
            row["portable_report"] = str((target / "qc-report.html").relative_to(destination))
            links.append(
                '<li><a href="'
                + html.escape(row["portable_report"])
                + '">'
                + html.escape(row["key"])
                + "</a> · "
                + html.escape(row["review"])
                + "</li>"
            )
    _publish_json(destination / "registration-summary.json", rows)
    columns = (
        "key",
        "subject_id",
        "session_id",
        "acquisition_id",
        "execution",
        "review",
        "qc_bundle",
        "portable_report",
        "findings",
    )
    with (destination / "registration-summary.csv").open("x", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    (destination / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Batch registration QC</title><h1>Batch registration and atlas review</h1><p>Execution, technical validation and human review are independent. Review applicability was reverified at export. No registration reran.</p><ul>'
        + "".join(links)
        + "</ul><pre>"
        + html.escape(json.dumps(rows, indent=2))
        + "</pre>"
    )
    return destination / "index.html"


def registration_workflow(existing_qc=False):
    """Reusable GUI/CLI workflow; per-acquisition declarations supply spatial evidence.

    The QC-only route requires an existing verified reference_record and
    qc_association (or registration_manifest). No registration is re-executed.
    The estimation route requires T1-space atlas association evidence, never
    an identity transform inferred from matching image dimensions.
    """

    def node(name, method, imports, inputs, parameters=None, dependencies=()):
        return dict(
            id=name,
            adapter="registration",
            depends_on=list(dependencies),
            imports=imports,
            tasks=[
                dict(
                    id="reg",
                    algorithm="registration." + method,
                    inputs={k: dict(artifact_id=v) for k, v in inputs.items()},
                    parameters=parameters or {},
                )
            ],
        )

    reference = dict(node="reference", output="mean-b0.nii.gz", kind="anatomy")
    if existing_qc:
        return dict(
            description="QC existing verified atlas on existing preprocessed-DWI reference; no registration commands",
            nodes=[
                node(
                    "qc",
                    "qc",
                    dict(
                        a=dict(
                            role="atlas",
                            kind="atlas_labels",
                            declaration_roles=dict(qc_association="qc_association"),
                        ),
                        b=dict(
                            role="registration_reference",
                            kind="anatomy",
                            declaration_roles=dict(reference_record="reference_record"),
                        ),
                    ),
                    dict(atlas="a", reference="b"),
                )
            ],
        )
    moving = dict(
        role="t1",
        kind="anatomy",
        declarations=dict(space_id="subject-t1", space_kind="T1_NATIVE", anatomical_modality="T1w"),
    )
    return dict(
        description="Preprocessed low-b reference → rigid T1 alignment → categorical atlas → independent QC review",
        nodes=[
            node(
                "reference",
                "reference",
                dict(d=dict(role="dwi", kind="dwi", bval_role="bval", bvec_role="bvec")),
                dict(dwi="d"),
                dict(b0_threshold=50, selection="mean_low_b"),
            ),
            node(
                "estimate",
                "flirt",
                dict(a=moving, b=reference),
                dict(moving="a", reference="b"),
                dict(model="rigid", cost="normmi"),
                ["reference"],
            ),
            node(
                "atlas",
                "apply.labels",
                dict(
                    a=dict(
                        role="atlas",
                        kind="atlas_labels",
                        declarations=dict(space_id="subject-t1", space_kind="T1_NATIVE"),
                        declaration_roles=dict(space_review="space_review"),
                    ),
                    b=reference,
                    t=dict(node="estimate", output="transform.json", kind="spatial_transform"),
                ),
                dict(moving="a", reference="b", transform="t"),
                dependencies=["reference", "estimate"],
            ),
            node(
                "qc",
                "qc",
                dict(
                    a=dict(node="atlas", output="registered.nii.gz", kind="atlas_labels"),
                    b=reference,
                ),
                dict(atlas="a", reference="b"),
                dependencies=["atlas", "reference"],
            ),
        ],
    )
