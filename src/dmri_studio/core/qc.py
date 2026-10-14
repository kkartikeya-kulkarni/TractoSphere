"""Read existing QC evidence without recomputing scientific analyses.

A catalog explicitly associates steps, statuses and artifacts. Hash verification
confirms catalog bytes, not scientific correctness. Human decisions are immutable
new records bound to that snapshot; imported claims never authenticate a reviewer.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .execution import hash_file
from .process import check_cancel
from .validation import freeze_json, json_compatible

EXECUTION = {"completed", "running", "failed", "skipped", "pending", "unknown"}
CHECKS = {"passed", "failed", "pending", "unavailable", "not_applicable"}
KINDS = {"report", "figure", "metrics", "log", "history", "comparison"}


def _json(path: Path) -> dict:
    """Bound JSON reads and reject duplicate keys/non-standard numeric constants."""
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("QC JSON exceeds the 32 MiB limit")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate QC key: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"Invalid JSON constant: {value}")

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError("QC document must be an object")
    return value


@dataclass(frozen=True)
class QCCatalog:
    """Frozen locally checked evidence; no external commands or automatic acceptance."""

    path: Path
    payload: object
    digest: str

    def to_dict(self) -> dict:
        """Return detached data for the UI/CLI."""
        return json_compatible(self.payload)

    def artifact_path(self, artifact: dict) -> Path:
        """Resolve relative locations against this catalog, independent of cwd."""
        return (self.path.parent / artifact["path"]).resolve(strict=True)


def load_catalog(
    path: Path,
    *,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[str], None] = lambda _: None,
) -> QCCatalog:
    """Validate every declared artifact hash; never infer completion from filenames."""
    path = path.expanduser().resolve(strict=True)
    before = hash_file(path, cancelled)
    payload = _json(path)
    if payload.get("schema_version") != 1 or payload.get("kind") != "dmri_qc_catalog":
        raise ValueError("Unsupported QC catalog")
    if not isinstance(payload.get("identity"), dict) or not payload["identity"].get("subject"):
        raise ValueError("QC catalog requires explicit subject identity")
    stages = payload.get("stages")
    if not isinstance(stages, list) or not stages:
        raise ValueError("QC catalog has no stages")
    seen = set()
    for stage in stages:
        if stage.get("id") in seen or not isinstance(stage.get("id"), str):
            raise ValueError("QC stage identifiers must be unique strings")
        seen.add(stage["id"])
        if (
            stage.get("execution") not in EXECUTION
            or stage.get("technical") not in CHECKS
            or stage.get("automated_qc") not in CHECKS
        ):
            raise ValueError(f"Invalid independent status fields for {stage['id']}")
        if stage.get("scientific_review") != "required":
            raise ValueError("Catalog imports cannot confer scientific acceptance")
        if not isinstance(stage.get("findings"), list) or not all(
            isinstance(x, str) for x in stage["findings"]
        ):
            raise ValueError("Findings must be explicit text")
        if not isinstance(stage.get("artifacts"), list):
            raise ValueError("Artifacts must be a list")
        if (stage["technical"] == "passed" or stage["automated_qc"] == "passed") and not stage[
            "artifacts"
        ]:
            raise ValueError("Passed checks require source evidence")
        for artifact in stage["artifacts"]:
            check_cancel(cancelled)
            if artifact.get("kind") not in KINDS or not isinstance(artifact.get("label"), str):
                raise ValueError("Invalid QC artifact kind/label")
            location = (path.parent / artifact["path"]).resolve(strict=True)
            progress(f"Verifying {stage['id']}: {location.name}")
            if hash_file(location, cancelled) != artifact.get("sha256"):
                raise ValueError(f"QC evidence changed: {location}")
    if hash_file(path, cancelled) != before:
        raise ValueError("QC catalog changed while being loaded")
    return QCCatalog(path, freeze_json(payload), before)


def read_artifact(catalog: QCCatalog, artifact: dict, *, limit: int = 4 * 1024 * 1024) -> str:
    """Read bounded selectable text after currency check; disclose truncation."""
    path = catalog.artifact_path(artifact)
    if hash_file(path) != artifact["sha256"]:
        raise ValueError("Artifact changed; reload the catalog")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    text = data[:limit].decode("utf-8", errors="replace")
    if len(data) > limit:
        text += "\n[Display truncated; full source remains available.]"
    return text


def export_report(catalog: QCCatalog, artifact: dict, destination: Path) -> Path:
    """Copy the existing report byte-for-byte to a new file, without rerunning QC."""
    if artifact["kind"] != "report":
        raise ValueError("Select an existing report")
    source = catalog.artifact_path(artifact)
    if hash_file(source) != artifact["sha256"]:
        raise ValueError("Report changed; reload the catalog")
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing)
    if hash_file(destination) != artifact["sha256"]:
        raise ValueError("Export did not match source; incomplete export retained")
    return destination


def record_review(
    catalog: QCCatalog, stage_id: str, decision: str, reviewer: str, reason: str, directory: Path
) -> Path:
    """Append a human decision bound to verified evidence; never mutate old status."""
    if (
        decision not in {"accepted", "rejected", "needs_review"}
        or not reviewer.strip()
        or not reason.strip()
    ):
        raise ValueError("Decision, reviewer and a scientific explanation are required")
    current = load_catalog(catalog.path)
    if current.digest != catalog.digest:
        raise ValueError("QC snapshot changed; reload before recording review")
    stage = next((s for s in catalog.to_dict()["stages"] if s["id"] == stage_id), None)
    if stage is None:
        raise ValueError("Unknown QC stage")
    if decision == "accepted" and (
        stage["execution"] != "completed" or stage["technical"] != "passed"
    ):
        raise ValueError("An incomplete or technically failed stage cannot be accepted")
    if not directory.is_dir():
        raise ValueError("Choose an existing review-record directory")
    record = {
        "schema_version": 1,
        "kind": "dmri_human_qc_review",
        "catalog": str(catalog.path),
        "catalog_sha256": catalog.digest,
        "identity": catalog.to_dict()["identity"],
        "stage": stage_id,
        "decision": decision,
        "reviewer": reviewer.strip(),
        "reason": reason.strip(),
        "recorded_utc": datetime.now(UTC).isoformat(),
        "evidence": stage["artifacts"],
        "authentication": "local user declaration",
    }
    target = directory / f"review-{uuid4().hex}.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2, allow_nan=False)
    return target


def artifact(path: Path, kind: str, label: str) -> dict:
    """Create an evidence descriptor for a producer without interpreting results."""
    return {
        "path": str(path.resolve(strict=True)),
        "kind": kind,
        "label": label,
        "sha256": hash_file(path),
    }


def load_review(catalog: QCCatalog, path: Path, *, cancelled=lambda: False) -> dict:
    """Read a local review declaration bound to this exact unchanged evidence.

    This is not reviewer authentication and never changes producer statuses.
    Contradictory reviews are retained as separate files for the user to assess.
    """
    path = path.resolve(strict=True)
    before = hash_file(path, cancelled)
    record = _json(path)
    current = load_catalog(catalog.path, cancelled=cancelled)
    if current.digest != catalog.digest or record.get("catalog_sha256") != catalog.digest:
        raise ValueError("Review belongs to a different or changed QC snapshot")
    if record.get("kind") != "dmri_human_qc_review" or record.get("schema_version") != 1:
        raise ValueError("Unsupported human-review record")
    stage = next((s for s in catalog.to_dict()["stages"] if s["id"] == record.get("stage")), None)
    if (
        stage is None
        or record.get("evidence") != stage["artifacts"]
        or record.get("identity") != catalog.to_dict()["identity"]
    ):
        raise ValueError("Review stage, subject or evidence does not match this catalog")
    if (
        record.get("decision") not in {"accepted", "rejected", "needs_review"}
        or not str(record.get("reviewer", "")).strip()
        or not str(record.get("reason", "")).strip()
    ):
        raise ValueError("Review needs a decision, reviewer and explanation")
    if record["decision"] == "accepted" and (
        stage["execution"] != "completed" or stage["technical"] != "passed"
    ):
        raise ValueError("An incomplete or technically failed stage cannot be accepted")
    if hash_file(path, cancelled) != before:
        raise ValueError("Review changed while reading")
    return {
        **record,
        "review_path": str(path),
        "review_sha256": before,
        "authentication": "local user declaration; identity not authenticated",
    }
