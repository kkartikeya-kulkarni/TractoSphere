"""Authoritative registration eligibility shared by import, GUI, CLI and batch.

Native decisions are append-only version2 records over an exact QC evidence set.
Legacy positive declarations are historical assertions, never approval. Known
rejection, stale evidence or identity conflicts cannot be overridden. Missing or
unresolved review permits only an explicitly issued, hash-bound DIAGNOSTIC record.
No operation here registers, resamples or modifies an original research file.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

from .execution import _publish_json, hash_file
from .scientific.records import read_json

SCHEMA = "tractosphere.registration-review/2"
IDENTITY = ("subject_id", "session_id", "acquisition_id", "space_id")


def review_directory():
    """Local discovery index only; review decisions remain in immutable QC bundles."""
    return Path(
        os.environ.get(
            "TRACTOSPHERE_REVIEW_DIRECTORY",
            str(Path.home() / ".tractosphere" / "registration-reviews"),
        )
    )


def known_bundles(atlas):
    """Find prior reviews even when a legacy importer omits the QC path."""
    directory = review_directory()
    result = set()
    path = str(Path(atlas["path"]).resolve())
    checksum = hash_file(Path(path))
    for key in (_digest(path), checksum):
        for entry in (directory / key).glob("*.json"):
            item = read_json(entry.read_text())
            result.add(item["qc_bundle"])
    return sorted(result)


def remember_bundle(path, evidence):
    path = str(Path(path).resolve())
    for key in (_digest(str(Path(evidence["atlas_path"]).resolve())), evidence["atlas_sha256"]):
        directory = review_directory() / key
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (_digest(path) + ".json")
        if not target.exists():
            _publish_json(target, dict(qc_bundle=path, identity=evidence["identity"]))


def _dict(artifact):
    return artifact.to_dict() if hasattr(artifact, "to_dict") else copy.deepcopy(artifact)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def bundle_evidence(path, cancelled=lambda: False):
    """Bind every native evidence hash and explicit acquisition/image identity."""
    from .registration_qc import verify_bundle

    record, hashes = verify_bundle(path, cancelled)
    # A transform sidecar is not sufficient by itself: validate its referenced
    # matrix/warp and exact moving/fixed images as well. Never interpret or
    # convert conventions here; the established spatial loader owns that check.
    for name in list(hashes):
        source = Path(name)
        if source.suffix == ".json" and source.stat().st_size <= 8 * 1024**2:
            nested = read_json(source.read_text())
            if (
                isinstance(nested, dict)
                and nested.get("schema") == "tractosphere.spatial-transform/1"
            ):
                from .spatial import load_transform

                transform = load_transform(source)
                hashes[transform["file"]] = transform["sha256"]
                for key in ("source", "target"):
                    hashes[transform[key]["image"]] = transform[key]["sha256"]
    atlas, reference = record["atlas"], record["reference"]
    identity = {k: atlas["declarations"].get(k, "") for k in IDENTITY}
    for k in IDENTITY:
        if identity[k] != reference["declarations"].get(k, ""):
            raise ValueError("QC reference identity conflict: " + k)
    for k in ("subject_id", "session_id", "acquisition_id"):
        if identity[k] != record.get(k, ""):
            raise ValueError("QC bundle identity conflict: " + k)
    return dict(
        identity=identity,
        atlas_path=atlas["path"],
        atlas_sha256=hash_file(Path(atlas["path"]), cancelled),
        reference_path=reference["path"],
        reference_sha256=hash_file(Path(reference["path"]), cancelled),
        registration=record["registration"],
        evidence_hashes=hashes,
    )


def bundle_review(path, cancelled=lambda: False):
    """Evaluate current review without promoting old schemas or erasing history."""
    result = dict(
        registration_execution_status="COMPLETED",
        technical_validity_status="VALIDATED",
        human_review_decision="NEEDS_REVIEW",
        review_applicability="MISSING",
        eligible=False,
    )
    try:
        evidence = bundle_evidence(path, cancelled)
        result.update(evidence_identity=_digest(evidence), evidence=evidence)
        decisions = sorted((Path(path).parent / "reviews").glob("*.json"))
        if not decisions:
            return result
        decision_path = decisions[-1]
        decision = read_json(decision_path.read_text())
        result.update(
            decision_path=str(decision_path), decision_sha256=hash_file(decision_path, cancelled)
        )
        if decision.get("evidence_hashes") != evidence["evidence_hashes"]:
            raise ValueError("Review evidence changed")
        if decision.get("schema") != SCHEMA:
            result.update(
                human_review_decision="REJECTED"
                if decision.get("decision") == "REJECTED"
                else "NEEDS_REVIEW",
                review_applicability="LEGACY_REVIEW_REQUIRED",
            )
            return result
        if (
            decision.get("evidence_identity") != _digest(evidence)
            or decision.get("identity") != evidence["identity"]
        ):
            raise ValueError("Review identity changed")
        if not all(
            isinstance(decision.get(k), str) and decision[k].strip()
            for k in ("reviewer", "comments")
        ) or not isinstance(decision.get("timestamp"), (int, float)):
            raise ValueError("Incomplete authoritative review record")
        state = decision.get("decision")
        if state not in {"APPROVED", "REJECTED", "NEEDS_REVIEW"}:
            raise ValueError("Invalid review decision")
        result.update(
            human_review_decision=state,
            review_applicability="APPLICABLE",
            eligible=state == "APPROVED",
        )
    except (ValueError, OSError, KeyError, TypeError) as error:
        result.update(
            technical_validity_status="STALE_EVIDENCE",
            review_applicability="STALE_EVIDENCE",
            eligible=False,
            reason=str(error),
        )
    return result


def _snapshot(atlas, reference, state, cancelled):
    """Exact diagnostic scope, including imported evidence and declared identities."""
    records = []
    for a in (atlas, reference):
        if a is None:
            continue
        d = dict(a.get("declarations", {}))
        d.pop("registration_diagnostic", None)
        # The snapshot binds declarations, not a mutable import metadata cache.
        hashes = {s["path"]: hash_file(Path(s["path"]), cancelled) for s in a.get("sources", [])}
        hashes[a["path"]] = hash_file(Path(a["path"]), cancelled)
        records.append(dict(path=a["path"], declarations=d, hashes=hashes))
    return dict(
        images=records,
        native_evidence_identity=state.get("evidence_identity"),
        native_decision_sha256=state.get("decision_sha256"),
    )


def registration_eligibility(atlas, reference=None, *, cancelled=lambda: False):
    """Return independent status axes; never trust a cached import decision."""
    atlas = _dict(atlas)
    reference = _dict(reference) if reference is not None else None
    d = atlas.get("declarations", {})
    state = dict(
        registration_execution_status="UNKNOWN",
        technical_validity_status="UNESTABLISHED",
        human_review_decision="NEEDS_REVIEW",
        review_applicability="MISSING",
        eligible=False,
        usage="BLOCKED",
    )
    candidates = known_bundles(atlas)
    qc = d.get("registration_qc")
    if qc:
        qc = str(Path(qc).resolve())
        candidates = sorted(set(candidates + [qc]))
    if len(candidates) > 1:
        # Independent bundles must not silently supersede a rejected/stale review.
        states = [bundle_review(p, cancelled) for p in candidates]
        if any(not item["eligible"] for item in states):
            state.update(
                review_applicability="CONFLICT",
                reason="Multiple native QC bundles require reconciliation",
                related_bundles=candidates,
            )
            return state
    if not qc and candidates:
        qc = candidates[-1]
    if qc:
        state.update(bundle_review(qc, cancelled))
        evidence = state.get("evidence")
        if evidence:
            if (
                any(d.get(k, "") != evidence["identity"][k] for k in IDENTITY)
                or hash_file(Path(atlas["path"]), cancelled) != evidence["atlas_sha256"]
            ):
                state.update(review_applicability="IDENTITY_CONFLICT", eligible=False)
            if reference is not None and (
                any(
                    reference.get("declarations", {}).get(k, "") != evidence["identity"][k]
                    for k in IDENTITY
                )
                or hash_file(Path(reference["path"]), cancelled) != evidence["reference_sha256"]
            ):
                state.update(review_applicability="IDENTITY_CONFLICT", eligible=False)
    if reference is not None and any(
        d.get(k, "") != reference.get("declarations", {}).get(k, "") for k in IDENTITY
    ):
        state.update(review_applicability="IDENTITY_CONFLICT", eligible=False)
    legacy = atlas.get("metadata", {}).get("registration_review", {})
    if (
        reference is not None
        and legacy.get("reference_sha256")
        and legacy["reference_sha256"] != hash_file(Path(reference["path"]), cancelled)
    ):
        state.update(
            review_applicability="IDENTITY_CONFLICT",
            eligible=False,
            reason="Registration target differs from exact selected reference",
        )
    for item in (atlas, reference):
        if item is not None:
            for source in item.get("sources", []):
                if hash_file(Path(source["path"]), cancelled) != source["sha256"]:
                    state.update(
                        review_applicability="STALE_EVIDENCE",
                        eligible=False,
                        reason="Imported source evidence changed",
                    )

    def text(value):
        return value.strip().casefold() if isinstance(value, str) else ""

    positive_legacy = (
        d.get("registration_reviewed") is True
        or text(d.get("registration_reviewed")) in {"true", "reviewed", "approved", "accepted"}
        or text(legacy.get("spatial_review")) == "reviewed"
        or text(legacy.get("decision")) == "approved"
    )
    declared_reject = any(
        text(value) == "rejected"
        for value in (
            d.get("registration_status"),
            d.get("registration_reviewed"),
            legacy.get("decision"),
            legacy.get("spatial_review"),
        )
    )
    if declared_reject or state["human_review_decision"] == "REJECTED":
        state.update(human_review_decision="REJECTED", eligible=False)
        if positive_legacy or state.get("review_applicability") == "APPLICABLE" and declared_reject:
            state["review_applicability"] = "CONFLICT"
    blocked = state["human_review_decision"] == "REJECTED" or state["review_applicability"] in {
        "STALE_EVIDENCE",
        "IDENTITY_CONFLICT",
        "CONFLICT",
    }
    if blocked:
        state.update(eligible=False, usage="BLOCKED")
        return state
    if state["eligible"]:
        state["usage"] = "REVIEWED"
        return state
    if positive_legacy or legacy:
        state["legacy_evidence"] = (
            "Preserved historical assertion; new review or explicit diagnostic record required"
        )
    if reference is None:
        state["reason"] = "Select exact reference to evaluate diagnostic eligibility"
        return state
    snapshot = _snapshot(atlas, reference, state, cancelled)
    state["diagnostic_evidence_identity"] = _digest(snapshot)
    override = d.get("registration_diagnostic")
    if override:
        if (
            not isinstance(override, dict)
            or override.get("schema") != "tractosphere.registration-diagnostic/1"
            or not override.get("reason", "").strip()
            or not override.get("reviewer", "").strip()
            or not override.get("timestamp")
        ):
            state.update(review_applicability="CONFLICT", reason="Invalid diagnostic override")
        elif override.get("evidence_identity") != _digest(snapshot):
            state.update(
                review_applicability="STALE_EVIDENCE", reason="Diagnostic evidence changed"
            )
        else:
            state.update(eligible=True, usage="DIAGNOSTIC", diagnostic_override=override)
    return state


def diagnostic_override(atlas, reference, *, reviewer, reason, cancelled=lambda: False):
    """Explicit API action; caller must save returned record in atlas declarations."""
    from .validation import utc_now

    if not reviewer.strip() or not reason.strip():
        raise ValueError("Diagnostic operation requires reviewer and substantive reason")
    a = _dict(atlas)
    a["declarations"].pop("registration_diagnostic", None)
    state = registration_eligibility(a, reference, cancelled=cancelled)
    if state["human_review_decision"] == "REJECTED" or state["review_applicability"] in {
        "STALE_EVIDENCE",
        "IDENTITY_CONFLICT",
        "CONFLICT",
    }:
        raise ValueError("Rejected, stale or conflicting registration cannot be overridden")
    if not a["declarations"].get("subject_id") or not a["declarations"].get("space_id"):
        raise ValueError("Diagnostic registration requires subject and physical space identity")
    return dict(
        schema="tractosphere.registration-diagnostic/1",
        reviewer=reviewer.strip(),
        reason=reason.strip(),
        timestamp=utc_now(),
        evidence_identity=state.get("diagnostic_evidence_identity"),
        scientific_acceptance="UNASSIGNED",
    )


def require_registration(atlas, reference, *, cancelled=lambda: False):
    """Shared scientific gate used before any downstream process or mask export."""
    result = registration_eligibility(atlas, reference, cancelled=cancelled)
    if not result["eligible"]:
        raise ValueError(
            "Atlas alignment unresolved: registration review "
            + result["human_review_decision"]
            + "/"
            + result["review_applicability"]
            + "; "
            + result.get("reason", "")
            + "; Reviewed native evidence or explicit diagnostic evidence required"
        )
    return result


def record_review(path, decision, reviewer, comments):
    """Append one v2 human decision; original images and historical reviews stay intact."""
    import time
    import uuid

    if (
        decision not in {"APPROVED", "REJECTED", "NEEDS_REVIEW"}
        or not reviewer.strip()
        or not comments.strip()
    ):
        raise ValueError("Select an explicit decision, reviewer and substantive comments")
    evidence = bundle_evidence(path)
    directory = Path(path).parent / "reviews"
    directory.mkdir(exist_ok=True)
    target = directory / (f"{time.time_ns()}-{uuid.uuid4().hex}.json")
    _publish_json(
        target,
        dict(
            schema=SCHEMA,
            decision=decision,
            reviewer=reviewer.strip(),
            comments=comments.strip(),
            timestamp=time.time(),
            identity=evidence["identity"],
            evidence_identity=_digest(evidence),
            evidence_hashes=evidence["evidence_hashes"],
            registration_execution_status="COMPLETED",
            technical_validity_status="VALIDATED",
        ),
    )
    remember_bundle(path, evidence)
    return target
