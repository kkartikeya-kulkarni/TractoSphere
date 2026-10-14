"""Persistent acquisition ledger with immutable snapshots and explicit provenance.

One row is an acquisition, not a participant. Shared calibration/anatomy stays
associated with its original paths. No concatenation, normalization, processing
history inference or MRI execution happens during discovery or project import.
"""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import nibabel as nib
import numpy as np

from .acquisition import validate_epi_reference
from .acquisition_metadata import entities, inherited_metadata
from .execution import _now, hash_file
from .process import check_cancel
from .qc import _json
from .runs import inspect_run
from .workflow import ProcessingHistory


def discover_acquisitions(
    root: Path, *, cancelled=lambda: False, progress=lambda _: None
) -> list[dict]:
    """Inventory BIDS magnitude DWIs and verify explicit calibration associations.

    Reads headers/gradients/metadata only. Missing evidence stays in each row's
    issues, allowing other acquisitions to be loaded independently. Anatomical
    images are candidates with unknown processing history, never assumed raw.
    """
    root = root.expanduser().absolute()
    rows = []
    for image in sorted(root.rglob("*_dwi.nii*")):
        check_cancel(cancelled)
        if (
            not image.name.endswith((".nii", ".nii.gz"))
            or "derivatives" in image.relative_to(root).parts
        ):
            continue
        fields, _ = entities(image)
        if fields.get("part") == "phase":
            continue
        progress("Inspecting acquisition " + str(image))
        stem = str(image)[:-7] if image.name.endswith(".nii.gz") else str(image)[:-4]
        paths = {
            role: stem + ext
            for role, ext in [
                ("dwi", ".nii.gz" if image.name.endswith(".nii.gz") else ".nii"),
                ("bval", ".bval"),
                ("bvec", ".bvec"),
            ]
        }
        if Path(stem + ".json").is_file():
            paths["json"] = stem + ".json"
        row = {
            "identity": {k: fields.get(k, "") for k in ("sub", "ses", "acq", "run", "dir")},
            "paths": paths,
            "bids_root": str(root),
            "issues": [],
            "references": [],
            "reference_issues": [],
            "anatomy_candidates": [],
        }
        try:
            header = nib.load(image)
            if len(header.shape) != 4:
                raise ValueError("DWI must have four dimensions")
            bvals = np.loadtxt(paths["bval"]).reshape(-1)
            bvecs = np.loadtxt(paths["bvec"], ndmin=2)
            if (
                bvals.size != header.shape[3]
                or bvecs.shape not in {(3, len(bvals)), (len(bvals), 3)}
                or not np.isfinite(bvals).all()
                or not np.isfinite(bvecs).all()
            ):
                raise ValueError("Gradient counts or finite values do not match DWI")
            row["header"] = {
                "shape": list(header.shape),
                "zooms": list(map(float, header.header.get_zooms())),
                "affine": header.affine.tolist(),
                "bvalues": np.unique(bvals).tolist(),
                "units": list(header.header.get_xyzt_units()),
            }
            row["acquisition"] = inherited_metadata(image, root=root)
        except (ValueError, OSError) as exc:
            row["issues"].append(str(exc))
        subject = next((p for p in image.parents if p.name.startswith("sub-")), None)
        session = next((p for p in image.parents if p.name.startswith("ses-")), subject)
        if subject and session:
            for ref in sorted(subject.rglob("*_epi.nii*")):
                if not ref.name.endswith((".nii", ".nii.gz")):
                    continue
                try:
                    ref_fields, _ = entities(ref)
                    if ref_fields.get("part") == "phase":
                        continue
                    evidence = inherited_metadata(ref, root=root)
                    association = validate_epi_reference(
                        image,
                        ref,
                        row.get("acquisition", {}).get("metadata", {}),
                        evidence["metadata"],
                    )
                    row["references"].append(
                        {"path": str(ref), "evidence": evidence, "association": association}
                    )
                except (ValueError, OSError) as exc:
                    row["reference_issues"].append({"path": str(ref), "reason": str(exc)})
            row["anatomy_candidates"] = [
                str(p)
                for p in sorted(session.glob("anat/*"))
                if p.name.endswith(
                    (
                        "_T1w.nii",
                        "_T1w.nii.gz",
                        "_UNIT1.nii",
                        "_UNIT1.nii.gz",
                        "_inv-2_MP2RAGE.nii.gz",
                    )
                )
            ]
        rows.append(row)
    return rows


def new_project(name: str) -> dict:
    """Create an empty ledger, without creating folders or importing data."""
    if not name.strip():
        raise ValueError("Project name is required")
    return {
        "kind": "dmri_acquisition_project",
        "schema_version": 1,
        "id": str(uuid.uuid4()),
        "name": name.strip(),
        "created_at": _now(),
        "acquisitions": [],
        "previous_snapshot": None,
    }


def add_acquisition(
    project: dict,
    paths: dict,
    identity: dict,
    history: dict,
    *,
    evidence=(),
    discovery=None,
    cancelled=lambda: False,
    progress=lambda _: None,
) -> dict:
    """Bind explicit input/history to bytes; documents do not authenticate claims.

    Works for original, partially processed and external/historical derivatives.
    Independent source documents are recorded, not interpreted as engine gates.
    """
    result = copy.deepcopy(project)
    ProcessingHistory.from_dict(history)
    if not identity.get("sub", "").strip():
        raise ValueError("A subject identity is required, including for custom layouts")
    paths = {k: str(Path(v).expanduser().absolute()) for k, v in paths.items() if v}
    if not {"dwi", "bval", "bvec"} <= set(paths) or set(paths) - {"dwi", "bval", "bvec", "json"}:
        raise ValueError("Supply DWI and matching bval/bvec paths")
    if any(
        Path(a["paths"]["dwi"]).resolve() == Path(paths["dwi"]).resolve()
        for a in result["acquisitions"]
    ):
        raise ValueError("This acquisition is already in the project; use its existing row")
    hashes = {}
    for role, value in paths.items():
        check_cancel(cancelled)
        progress("Binding " + role + ": " + value)
        hashes[role] = hash_file(Path(value), cancelled)
    documents = {
        str(Path(p).expanduser().absolute()): hash_file(Path(p), cancelled) for p in evidence
    }
    if discovery:
        # Bind inherited metadata too: a snapshot must never advertise an old
        # calibration association after the source JSON was edited. Correction
        # still revalidates the actual images and metadata during preparation.
        metadata_records = [discovery.get("acquisition", {})] + [
            ref["evidence"] for ref in discovery.get("references", [])
        ]
        for metadata in metadata_records:
            for source in metadata.get("sources", []):
                if hash_file(Path(source["path"]), cancelled) != source["sha256"]:
                    raise ValueError("Acquisition metadata changed since discovery")
                documents[source["path"]] = source["sha256"]
    row = {
        "id": str(uuid.uuid4()),
        "identity": dict(identity),
        "paths": paths,
        "sha256": hashes,
        "history": history,
        "evidence": documents,
        "discovery": discovery,
        "attempts": [],
        "history_notice": "Declared/inferred provenance. Attached documents and hashes do not establish scientific acceptance.",
    }
    if discovery and discovery["paths"] != paths:
        raise ValueError("Discovery record does not identify these input paths")
    result["acquisitions"].append(row)
    return result


def save_project(project: dict, path: Path) -> Path:
    """Save a new snapshot; existing snapshots and imaging paths are immutable."""
    path = path.expanduser().absolute()
    for row in project["acquisitions"]:
        for source in row["paths"].values():
            parent = Path(source).resolve().parent
            if path.resolve().parent == parent or parent in path.resolve().parents:
                raise ValueError("Save project snapshots outside source imaging directories")
    # Exclusive create prevents two windows replacing each other's history.
    with path.open("x", encoding="utf8") as stream:
        json.dump(project, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return path


def read_project(path: Path) -> dict:
    """Open a saved ledger without loading arrays or asserting current hashes."""
    record = _json(path)
    if record.get("kind") != "dmri_acquisition_project" or record.get("schema_version") != 1:
        raise ValueError("Unsupported project schema")
    ids = set()
    for row in record["acquisitions"]:
        if row["id"] in ids:
            raise ValueError("Duplicate acquisition identity")
        ids.add(row["id"])
        ProcessingHistory.from_dict(row["history"])
        if set(row["paths"]) != set(row["sha256"]):
            raise ValueError("Acquisition paths and hashes differ")
    return record


def acquisition_context(
    project_path: Path, acquisition_id: str, *, cancelled=lambda: False
) -> dict:
    """Produce the same hash-bound input transaction used by saved-run loading."""
    digest = hash_file(project_path, cancelled)
    record = read_project(project_path)
    row = next(r for r in record["acquisitions"] if r["id"] == acquisition_id)
    for path, expected in row["evidence"].items():
        if hash_file(Path(path), cancelled) != expected:
            raise ValueError("External provenance changed: " + path)
    product = {k: row[k] for k in ("paths", "sha256", "history")}
    product.update(
        label="Project acquisition",
        source_manifest=str(project_path),
        source_sha256=digest,
        extra_hashes=row["evidence"],
    )
    handoff = {}
    if row.get("discovery"):
        handoff["bids_root"] = row["discovery"]["bids_root"]
        handoff["references"] = [r["path"] for r in row["discovery"]["references"]]
    if hash_file(project_path, cancelled) != digest:
        raise ValueError("Project changed during inspection")
    return {
        "product": product,
        "handoff": handoff,
        "project_binding": {"project_id": record["id"], "acquisition_id": acquisition_id},
    }


def attach_attempt(
    project: dict,
    acquisition_id: str,
    manifest: Path,
    *,
    cancelled=lambda: False,
    progress=lambda _: None,
) -> dict:
    """Associate a completed/failed attempt only through exact input lineage.

    Validation failures remain failures. Previously completed outputs may be
    inputs of a next stage; no subject-name or basename-only matching is used.
    """
    result = copy.deepcopy(project)
    row = next(r for r in result["acquisitions"] if r["id"] == acquisition_id)
    snapshot = inspect_run(manifest, cancelled=cancelled, progress=progress)
    if snapshot["status"] not in {"validated", "validated_pending_qc", "failed", "cancelled"}:
        raise ValueError("An active/incomplete attempt cannot be imported as finished")
    plan = snapshot["record"]["plan"]
    candidates = [
        {str(Path(row["paths"][role]).resolve()): digest for role, digest in row["sha256"].items()}
    ]
    for previous in row["attempts"]:
        if hash_file(Path(previous["manifest"]), cancelled) != previous["sha256"]:
            raise ValueError("A previously attached attempt changed; review project lineage")
        old = inspect_run(Path(previous["manifest"]), cancelled=cancelled, progress=progress)
        products = list(old["products"])
        if old.get("recovery"):
            products.append(old["recovery"]["product"])
        candidates.extend(
            {str(Path(p["paths"][role]).resolve()): digest for role, digest in p["sha256"].items()}
            for p in products
        )
    supplied = {
        str(Path(plan["input_paths"][role]).resolve()): entry["sha256"]
        for role, entry in plan["inputs"].items()
        if role in {"dwi", "bval", "bvec"}
    }
    if len(supplied) != 3 or not any(
        all(c.get(path) == digest for path, digest in supplied.items()) for c in candidates
    ):
        raise ValueError(
            "Attempt input lineage does not match this acquisition or its verified outputs"
        )
    if any(a["manifest"] == str(manifest.resolve()) for a in row["attempts"]):
        raise ValueError("Attempt already recorded")
    row["attempts"].append(
        {
            "manifest": str(manifest.resolve()),
            "sha256": snapshot["manifest_sha256"],
            "status": snapshot["status"],
            "operations": plan["operations"],
            "scientific_review": "pending",
        }
    )
    return result
