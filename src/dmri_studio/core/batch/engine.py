"""Persistent resource scheduler. Scientific work delegates exclusively to execute_plan.

One journal owner (OS advisory lock), atomic snapshots, immutable attempts and
append-only events. Pause drains active jobs; cancellation uses the existing
process-group cancellation contract. No partial derivative is reused.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import csv
import html
import json
import math
import os
import shutil
import statistics
import threading
import time
import uuid
from collections import Counter
from pathlib import Path

import psutil

from ..execution import ExecutionPlan, execute_plan, hash_file
from ..process import ProcessingCancelled
from ..scientific.records import read_json
from .discovery import Acquisition, associated_path
from .planning import compile_node, digest, requests, resources, verify_reuse

GOOD = {"COMPLETED", "SKIPPED_VERIFIED"}
TERMINAL = GOOD | {"FAILED", "CANCELLED", "BLOCKED", "REQUIRES_REVIEW"}


def atomic_json(path, value):
    """Durable atomic metadata update, never a scientific source or derivative."""
    path = Path(path)
    temp = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    with temp.open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_journal(folder):
    return read_json((Path(folder) / "journal.json").read_text())


def control(folder, action, keys=()):
    """CLI/GUI mailbox. Commands are exclusive files, never executable text."""
    if action not in {"pause", "resume", "cancel", "retry"}:
        raise ValueError("Unsupported batch control")
    folder = Path(folder)
    journal = read_journal(folder)
    if set(keys) - set(journal["nodes"]):
        raise ValueError("Control target is not in this batch")
    path = folder / "controls" / (f"{time.time_ns()}-{uuid.uuid4().hex}.json")
    with path.open("x") as stream:
        json.dump(dict(action=action, keys=list(keys), timestamp=time.time()), stream)
    return path


@contextlib.contextmanager
def ownership(folder):
    """An expired PID is not a lock. Kernel ownership survives stale lock-file contents."""
    import fcntl

    with (Path(folder) / "scheduler.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another scheduler owns this batch") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def normalized_plan(plan):
    """Relocate command destinations only; exact inputs/settings/tool hashes stay bound."""
    data = json.loads(json.dumps(plan))
    folder = data["output_directory"]
    data.pop("build", None)  # Source modules, not unrelated Git commits, establish code identity.

    def walk(value):
        if isinstance(value, str):
            return value.replace(folder, "${ATTEMPT}")
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            return {walk(k): walk(v) for k, v in value.items()}
        return value

    return walk(data)


def create_batch(plan: ExecutionPlan, approval: str):
    if plan.payload.get("kind") != "tractosphere_batch_plan" or approval != plan.digest:
        raise ValueError("Approve the exact reviewed batch-plan digest")
    data = plan.to_dict()
    folder = Path(data["output_directory"])
    folder.mkdir(exist_ok=False)
    for name in ("controls", "logs", "subjects", "reports"):
        (folder / name).mkdir()
    atomic_json(folder / "approved-plan.json", {"digest": approval, "plan": data})
    nodes = {}
    for row in data["rows"]:
        nodes[row["key"]] = {
            **row,
            "status": "PENDING"
            if row["status"] in {"ELIGIBLE", "WAITING_DEPENDENCY", "SKIPPED_VERIFIED"}
            else row["status"],
            "attempts": [],
            "qc_status": "NOT_ASSESSED",
            "scientific_acceptance": "unassigned",
        }
    journal = dict(
        schema="tractosphere.batch-journal/1",
        plan_digest=approval,
        study_id=data["study"]["study_id"],
        created_at=time.time(),
        updated_at=time.time(),
        state="READY",
        nodes=nodes,
        applied_controls=[],
        owner=None,
        scientific_acceptance="unassigned",
    )
    atomic_json(folder / "journal.json", journal)
    return folder


class BatchRunner:
    """Qt-free queue owner; use run() in a GUI worker or headless process."""

    def __init__(self, folder, *, progress=lambda _: None, cancelled=lambda: False):
        self.folder = Path(folder).resolve(strict=True)
        self.progress = progress
        self.cancelled = cancelled
        self.events = {}
        self.paused = False
        self.mutex = threading.Lock()

    def save(self):
        self.journal["updated_at"] = time.time()
        atomic_json(self.folder / "journal.json", self.journal)

    def log(self, key, message):
        with self.mutex:
            with (self.folder / "logs" / (digest(key)[:20] + ".log")).open("a") as stream:
                stream.write(f"{time.time():.6f} {message}\n")
            self.progress(f"{key}: {message}")

    def event(self, key, state, **extra):
        row = self.journal["nodes"][key]
        row.update(status=state, **extra)
        with (self.folder / "events.jsonl").open("a") as stream:
            stream.write(
                json.dumps(dict(time=time.time(), key=key, status=state, **extra), allow_nan=False)
                + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        self.save()

    def controls(self):
        for path in sorted((self.folder / "controls").glob("*.json")):
            if path.name in self.journal["applied_controls"]:
                continue
            command = read_json(path.read_text())
            action, keys = command["action"], command["keys"] or list(self.journal["nodes"])
            if action in {"pause", "resume"}:
                self.paused = action == "pause"
                self.journal["state"] = "PAUSED" if self.paused else "RUNNING"
            for key in keys:
                row = self.journal["nodes"][key]
                reviewed = next(item for item in self.plan["rows"] if item["key"] == key)
                if reviewed["status"] == "BLOCKED":
                    # Cancellation/retry cannot promote an ineligible unapproved
                    # stage. Corrected inputs need a new reviewed plan.
                    continue
                if action == "cancel":
                    if key in self.events:
                        self.events[key].set()
                    elif row["status"] not in GOOD:
                        self.event(key, "CANCELLED", error="Cancelled before scheduling")
                elif action == "retry" and row["status"] in {
                    "FAILED",
                    "CANCELLED",
                    "REQUIRES_REVIEW",
                }:
                    self.event(key, "PENDING", error=None)
                # BLOCKED input/configuration plans need a new reviewed plan.
            self.journal["applied_controls"].append(path.name)
            self.save()

    def orphan_check(self):
        """Never duplicate an interrupted job that may still be writing an attempt."""
        active_paths = [
            a["directory"]
            for r in self.journal["nodes"].values()
            if r["status"] == "RUNNING"
            for a in r["attempts"][-1:]
        ]
        for process in psutil.process_iter(["pid", "cmdline", "cwd"]):
            if process.pid == os.getpid():
                continue
            command = " ".join(process.info.get("cmdline") or [])
            cwd = process.info.get("cwd") or ""
            if any(p in command or cwd == p or cwd.startswith(p + os.sep) for p in active_paths):
                raise ValueError(
                    f"Interrupted attempt still has a possible writer PID {process.pid}; no duplicate execution"
                )

    def verify_completed(self, key, row):
        node = row["node"]
        acquisition = Acquisition(**row["acquisition"])
        completed = self.dependencies(row)
        artifacts, tasks = requests(
            acquisition,
            node,
            Path(self.plan["study"]["root"]),
            completed,
            cancelled=self.cancelled,
            progress=lambda s: self.log(key, s),
        )
        if row.get("input_ids") and row["input_ids"] != [a.id for a in artifacts]:
            raise ValueError("Approved input identity changed; create a new reviewed plan")
        manifest = row.get("manifest")
        if not manifest and node.get("reuse_role"):
            manifest = associated_path(acquisition, node["reuse_role"], "manifest")
        if row.get("manifest_sha256") and hash_file(Path(manifest)) != row["manifest_sha256"]:
            raise ValueError("Completed producer manifest changed")
        if node["adapter"] == "preprocessing":
            from ..runs import inspect_run

            result = inspect_run(Path(manifest), cancelled=self.cancelled)
            if result["status"] not in {"validated", "validated_pending_qc"} or not result[
                "record"
            ].get("inputs_unchanged"):
                raise ValueError("Preprocessing output is incomplete")
            if not row.get("preview") or normalized_plan(
                result["record"]["plan"]
            ) != normalized_plan(row["preview"]):
                raise ValueError("Preprocessing recipe differs from approved plan")
            for tool in result["record"]["plan"]["tools"].values():
                if hash_file(Path(tool["path"])) != tool["sha256"]:
                    raise ValueError("Preprocessing tool identity changed")
        else:
            result = verify_reuse(
                manifest,
                artifacts,
                tasks,
                cancelled=self.cancelled,
                progress=lambda s: self.log(key, s),
            )
        return result

    def dependencies(self, row):
        prefix = row["key"].rsplit("/", 1)[0]
        return {
            name: self.journal["nodes"][prefix + "/" + name]["manifest"]
            for name in row["node"].get("depends_on", [])
            if self.journal["nodes"][prefix + "/" + name]["status"] in GOOD
        }

    def execute(self, key, row, attempt, cancel):
        start = time.monotonic()
        try:
            acquisition = Acquisition(**row["acquisition"])
            artifacts, tasks = requests(
                acquisition,
                row["node"],
                Path(self.plan["study"]["root"]),
                self.dependencies(row),
                cancelled=cancel.is_set,
                progress=lambda s: self.log(key, s),
            )
            if row.get("input_ids") and row["input_ids"] != [a.id for a in artifacts]:
                raise ValueError("Source identity changed after approval")
            reserve = resources(row["node"], artifacts, tasks)
            if (
                reserve["memory_bytes"] > row["resources"]["memory_bytes"]
                or reserve["disk_bytes"] > row["resources"]["disk_bytes"]
            ):
                raise ValueError(
                    "Materialized dependency exceeds its approved reservation; review a larger resource request"
                )
            plan = compile_node(
                acquisition,
                row["node"],
                artifacts,
                tasks,
                attempt,
                nthreads=row["resources"]["threads"],
                tool_directory=Path(self.plan["tool_directory"])
                if self.plan["tool_directory"]
                else None,
                cancelled=cancel.is_set,
                progress=lambda s: self.log(key, s),
            )
            required = plan.to_dict().get("parameters", {}).get("resource_reservation")
            if required and any(
                required[k] > row["resources"][k] for k in ("memory_bytes", "disk_bytes")
            ):
                raise ValueError("Compiled registration exceeds approved resource reservation")
            if row.get("preview") and normalized_plan(plan.to_dict()) != normalized_plan(
                row["preview"]
            ):
                raise ValueError(
                    "Compiled scientific plan changed since review; new approval required"
                )
            # Exact runtime plan is derived from the approved recipe. Existing engine
            # still receives its own exact digest and revalidates every source/tool.
            dataset = None
            if row["node"]["adapter"] == "preprocessing":
                from ..dataset import DatasetPaths
                from ..loading import DatasetLoader

                request = next(a for a in artifacts if a.kind == "dwi").to_dict()["request"]
                dataset = (
                    DatasetLoader()
                    .load(
                        DatasetPaths(
                            request["path"],
                            request.get("bval"),
                            request.get("bvec"),
                            request.get("sidecar"),
                        )
                    )
                    .dataset
                )
            try:
                result = execute_plan(
                    dataset,
                    plan,
                    approval=plan.digest,
                    cancelled=cancel.is_set,
                    progress=lambda s: self.log(key, s),
                )
            finally:
                if dataset is not None:
                    dataset.close()
            status = {
                "validated": "COMPLETED",
                "validated_pending_qc": "COMPLETED",
                "cancelled": "CANCELLED",
            }.get(result["status"], "FAILED")
            from ..runs import MANIFESTS

            manifest_path = attempt / MANIFESTS[plan.payload["kind"]]
            return dict(
                status=status,
                manifest=str(manifest_path),
                manifest_sha256=hash_file(manifest_path),
                elapsed_seconds=time.monotonic() - start,
                qc_status="REQUIRES_REVIEW" if status == "COMPLETED" else "NOT_ASSESSED",
                validation=result.get("validation"),
                qc_metrics=read_metrics(manifest_path),
                error=result.get("error"),
                input_ids=[a.id for a in artifacts],
            )
        except ProcessingCancelled as exc:
            return dict(
                status="CANCELLED", error=str(exc), elapsed_seconds=time.monotonic() - start
            )
        except Exception as exc:
            self.log(key, f"{type(exc).__name__}: {exc}")
            return dict(
                status="FAILED",
                error=f"{type(exc).__name__}: {exc}",
                elapsed_seconds=time.monotonic() - start,
            )

    def run(self):
        with ownership(self.folder):
            approved = read_json((self.folder / "approved-plan.json").read_text())
            self.plan = approved["plan"]
            if ExecutionPlan(self.plan).digest != approved["digest"]:
                raise ValueError("Approved plan was modified")
            for name, expected in self.plan["orchestration_hashes"].items():
                if hash_file(Path(__file__).with_name(name)) != expected:
                    raise ValueError("Batch implementation changed; create a newly reviewed plan")
            for relative, expected in self.plan.get("scientific_implementation_hashes", {}).items():
                if hash_file(Path(__file__).parents[1] / relative) != expected:
                    raise ValueError("Scientific implementation changed; review a new plan")
            for tool in self.plan.get("deferred_tools", {}).values():
                if hash_file(Path(tool["path"])) != tool["sha256"]:
                    raise ValueError("Deferred tool identity changed; review a new plan")
            self.journal = read_journal(self.folder)
            if self.journal["plan_digest"] != approved["digest"]:
                raise ValueError("Journal belongs to another plan")
            reviewed = {r["key"]: r for r in self.plan["rows"]}
            if set(reviewed) != set(self.journal["nodes"]):
                raise ValueError("Journal node identities changed")
            for key, row in self.journal["nodes"].items():
                for field in (
                    "key",
                    "node",
                    "acquisition",
                    "resources",
                    "preview",
                    "preview_digest",
                ):
                    if row.get(field) != reviewed[key].get(field):
                        raise ValueError("Journal configuration differs from the approved plan")
            self.orphan_check()
            self.paused = self.journal["state"] == "PAUSED"
            self.journal.update(
                owner={"pid": os.getpid(), "created": psutil.Process().create_time()},
                state="PAUSED" if self.paused else "RUNNING",
            )
            self.save()
            self.controls()
            # Revalidate in dependency order. Failed verification is review, not rerun.
            for key, row in self.journal["nodes"].items():
                if (
                    row["status"] in GOOD
                    or row["status"] == "RUNNING"
                    or row["node"].get("reuse_role")
                    and row["status"] == "PENDING"
                ):
                    try:
                        if row["status"] == "RUNNING":
                            attempt = Path(row["attempts"][-1]["directory"])
                            from ..runs import MANIFESTS

                            candidates = [
                                attempt / name
                                for name in set(MANIFESTS.values())
                                if (attempt / name).is_file()
                            ]
                            if len(candidates) != 1:
                                raise ValueError(
                                    "Interrupted attempt lacks one unambiguous producer manifest"
                                )
                            row["manifest"] = str(candidates[0])
                        result = self.verify_completed(key, row)
                        self.event(
                            key,
                            "SKIPPED_VERIFIED",
                            manifest=result["manifest"],
                            manifest_sha256=result["manifest_sha256"],
                            validation=result["record"].get("validation"),
                            qc_metrics=read_metrics(Path(result["manifest"])),
                            qc_status="REQUIRES_REVIEW",
                            error=None,
                        )
                    except Exception as exc:
                        self.event(key, "REQUIRES_REVIEW", error=str(exc))
            active = {}
            limits = self.plan["limits"]
            try:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=limits["max_processes"]
                ) as pool:
                    while True:
                        self.controls()
                        if self.cancelled():
                            for key, row in self.journal["nodes"].items():
                                if key in self.events:
                                    self.events[key].set()
                                elif row["status"] not in TERMINAL:
                                    self.event(key, "CANCELLED", error="Batch cancellation")
                        for future in list(active):
                            if future.done():
                                key = active.pop(future)
                                result = future.result()
                                self.events.pop(key, None)
                                self.event(key, result.pop("status"), **result)
                        scheduled = False
                        for key, row in self.journal["nodes"].items():
                            if (
                                self.paused
                                or self.cancelled()
                                or row["status"] not in {"PENDING", "RESOURCE_BLOCKED"}
                            ):
                                continue
                            prefix = key.rsplit("/", 1)[0]
                            dependencies = [
                                self.journal["nodes"][prefix + "/" + n]
                                for n in row["node"].get("depends_on", [])
                            ]
                            if any(d["status"] not in GOOD for d in dependencies):
                                continue
                            active_rows = [self.journal["nodes"][v] for v in active.values()]
                            subjects = {r["acquisition"]["subject_id"] for r in active_rows}
                            if len(active) >= limits["max_processes"] or (
                                row["acquisition"]["subject_id"] not in subjects
                                and len(subjects) >= limits["max_subjects"]
                            ):
                                continue
                            memory = sum(r["resources"]["memory_bytes"] for r in active_rows)
                            disk = sum(r["resources"]["disk_bytes"] for r in active_rows)
                            threads = sum(r["resources"]["threads"] for r in active_rows)
                            r = row["resources"]
                            if (
                                memory + r["memory_bytes"] > limits["memory_bytes"]
                                or threads + r["threads"] > limits["cpu_threads"]
                            ):
                                continue
                            headroom = limits["disk_headroom_bytes"]
                            if row["node"]["adapter"] == "registration" or any(
                                a["node"]["adapter"] == "registration" for a in active_rows
                            ):
                                from ..registration_resources import DISK_HEADROOM_BYTES

                                headroom = max(headroom, DISK_HEADROOM_BYTES)
                            if (
                                psutil.virtual_memory().available
                                < memory + r["memory_bytes"] + 512 * 1024**2
                                or shutil.disk_usage(self.folder).free
                                < disk + r["disk_bytes"] + headroom
                            ):
                                self.event(
                                    key,
                                    "RESOURCE_BLOCKED",
                                    error="Insufficient available RAM or disk headroom; no new process started",
                                )
                                continue
                            parent = self.folder / "subjects" / prefix
                            parent.mkdir(parents=True, exist_ok=True)
                            attempt = parent / (
                                row["node"]["id"] + "-attempt-" + uuid.uuid4().hex[:12]
                            )
                            row["attempts"].append(
                                {"directory": str(attempt), "started_at": time.time()}
                            )
                            cancel = threading.Event()
                            self.events[key] = cancel
                            self.event(key, "RUNNING", error=None)
                            future = pool.submit(self.execute, key, row.copy(), attempt, cancel)
                            active[future] = key
                            scheduled = True
                        if not active and not scheduled:
                            # Pause returns safely; a new owner can later resume. Resource blocks
                            # do not spin or consume CPU while waiting for user/system changes.
                            break
                        time.sleep(0.05)
            finally:
                self.journal["owner"] = None
                statuses = {r["status"] for r in self.journal["nodes"].values()}
                self.journal["state"] = (
                    "PAUSED"
                    if self.paused
                    else "COMPLETED"
                    if statuses <= GOOD
                    else "ATTENTION_REQUIRED"
                )
                self.save()
            return self.journal


def study_qc(folder):
    """Available measurements only. No incompatible matrix averaging or auto acceptance."""
    journal = read_journal(folder)
    rows = list(journal["nodes"].values())
    acquisitions = {}
    for row in rows:
        acquisitions.setdefault(row["key"].rsplit("/", 1)[0], []).append(row)
    reviews = reviews_for(folder, journal)
    accepted = {
        key
        for key, values in acquisitions.items()
        if all(reviews.get(r["key"], {}).get("decision") == "accepted" for r in values)
    }
    subjects = {r["acquisition"]["subject_id"] for r in rows}
    accepted_subjects = {
        subject
        for subject in subjects
        if all(key in accepted for key in acquisitions if key.split("/")[0] == subject)
    }
    counts = Counter(r["status"] for r in rows)
    groups = {}
    for row in rows:
        protocol = row.get("comparison_configuration", {"unverified_group": row["key"]})
        group = digest(protocol)
        groups.setdefault(group, {"configuration": protocol, "nodes": []})["nodes"].append(
            row["key"]
        )
    measurements = {}
    for group, configuration in groups.items():
        records = [r for r in rows if r["key"] in configuration["nodes"] and r["status"] in GOOD]
        entries = {}
        for row in records:
            for metric, value in numeric_leaves(row.get("qc_metrics", {})):
                entries.setdefault(metric, []).append((row["key"], value))
        summary = {}
        for metric, values in entries.items():
            numbers = [v for _, v in values]
            median = statistics.median(numbers)
            mad = statistics.median(abs(v - median) for v in numbers)
            summary[metric] = dict(
                n=len(numbers),
                minimum=min(numbers),
                maximum=max(numbers),
                median=median,
                review_outliers=[
                    key
                    for key, value in values
                    if len(values) >= 4 and mad > 0 and abs(value - median) > 3 * 1.4826 * mad
                ],
                policy="Exploratory median absolute deviation flag only; never exclusion or acceptance",
            )
        measurements[group] = summary
    return dict(
        study_id=journal["study_id"],
        subjects=len({r["acquisition"]["subject_id"] for r in rows}),
        acquisitions=len(acquisitions),
        stages=dict(counts),
        completed_acquisitions=sum(
            all(r["status"] in GOOD for r in records) for records in acquisitions.values()
        ),
        scientific_acceptance="unassigned",
        accepted_subjects=len(accepted_subjects),
        explicit_reviews=reviews,
        comparison_groups=groups,
        available_measurements=measurements,
        findings=[
            dict(
                key=r["key"],
                status=r["status"],
                qc=r["qc_status"],
                error=r.get("error"),
                validation=r.get("validation"),
            )
            for r in rows
        ],
        notice="Completion is not scientific acceptance. Unexecuted QC is not applicable; no matrix aggregation is performed.",
    )


def export_report(folder, destination):
    """New self-contained HTML/JSON/CSV snapshot, preserving all original records."""
    destination = Path(destination)
    destination.mkdir(exist_ok=False)
    journal, qc = read_journal(folder), study_qc(folder)
    atomic_json(destination / "journal.json", journal)
    atomic_json(destination / "study-qc.json", qc)
    shutil.copyfile(Path(folder) / "approved-plan.json", destination / "approved-plan.json")
    columns = [
        "key",
        "status",
        "qc_status",
        "scientific_acceptance",
        "elapsed_seconds",
        "manifest",
        "error",
    ]
    with (destination / "subjects.csv").open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(journal["nodes"].values())
    body = "".join(
        "<tr>"
        + "".join("<td>" + html.escape(str(row.get(c, ""))) + "</td>" for c in columns)
        + "</tr>"
        for row in journal["nodes"].values()
    )
    (destination / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>TractoSphere batch QC</title><style>body{font:15px system-ui;margin:36px}td,th{padding:8px;border-bottom:1px solid #bbb;text-align:left}pre{white-space:pre-wrap}</style><h1>'
        + html.escape(qc["study_id"])
        + "</h1><p>"
        + html.escape(qc["notice"])
        + "</p><table><tr>"
        + "".join("<th>" + c + "</th>" for c in columns)
        + "</tr>"
        + body
        + "</table><h2>Available QC</h2><pre>"
        + html.escape(json.dumps(qc, indent=2))
        + "</pre>"
    )
    return destination / "index.html"


def read_metrics(manifest):
    """Read only producer-hashed QC, retaining absence instead of inventing a pass."""
    record = read_json(Path(manifest).read_text())
    name = "qc-metrics.json"
    if name not in record.get("outputs", {}):
        return {}
    path = Path(manifest).parent / name
    if path.stat().st_size > 16 * 1024**2 or hash_file(path) != record["outputs"][name]["sha256"]:
        raise ValueError("QC metrics changed or exceed the metadata limit")
    return read_json(path.read_text())


def numeric_leaves(value, prefix=""):
    """Keep full native field names; arrays/distributions are not flattened as subjects."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield from numeric_leaves(child, prefix + "/" + key)
    elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        yield prefix, value


def record_review(folder, key, decision, reviewer, note):
    """Explicit human assessment bound to exact immutable result identity."""
    if (
        decision not in {"accepted", "rejected", "requires_review"}
        or not reviewer.strip()
        or not note.strip()
    ):
        raise ValueError("Review requires a decision, named reviewer and scientific rationale")
    journal = read_journal(folder)
    row = journal["nodes"][key]
    if row["status"] not in GOOD or not row.get("manifest"):
        raise ValueError("Only verified completed results can receive a scientific review")
    from ..runs import inspect_run

    result = inspect_run(Path(row["manifest"]))
    if result["manifest_sha256"] != row["manifest_sha256"]:
        raise ValueError("Result changed since batch verification")
    if result["record"]["plan"].get("operation") == "atlas_qc":
        from ..registration_qc import record_decision

        record_decision(
            Path(row["manifest"]).parent / "atlas-qc.json",
            {"accepted": "APPROVED", "rejected": "REJECTED", "requires_review": "NEEDS_REVIEW"}[
                decision
            ],
            reviewer,
            note,
        )
    target = Path(folder) / "reviews"
    target.mkdir(exist_ok=True)
    record = dict(
        key=key,
        decision=decision,
        reviewer=reviewer,
        note=note,
        timestamp=time.time(),
        plan_digest=journal["plan_digest"],
        manifest_sha256=row["manifest_sha256"],
    )
    path = target / (str(time.time_ns()) + "-" + uuid.uuid4().hex + ".json")
    with path.open("x") as stream:
        json.dump(record, stream, indent=2)
    return record


def reviews_for(folder, journal):
    result = {}
    for path in sorted((Path(folder) / "reviews").glob("*.json")):
        record = read_json(path.read_text())
        row = journal["nodes"].get(record["key"])
        if (
            row
            and row["status"] in GOOD
            and record["plan_digest"] == journal["plan_digest"]
            and record["manifest_sha256"] == row.get("manifest_sha256")
        ):
            if row["node"]["adapter"] == "registration" and row.get("manifest"):
                bundle = Path(row["manifest"]).parent / "atlas-qc.json"
                if bundle.is_file():
                    from ..registration_qc import review_status

                    if review_status(bundle) == "STALE_EVIDENCE":
                        continue
            result[record["key"]] = record
    # Native-viewer reviews and batch reviews share the same hash-bound evidence.
    # A later rejection in the viewer must supersede an earlier batch acceptance.
    from ..registration_qc import review_status

    for key, row in journal["nodes"].items():
        if (
            row["node"]["adapter"] != "registration"
            or row["status"] not in GOOD
            or not row.get("manifest")
        ):
            continue
        bundle = Path(row["manifest"]).parent / "atlas-qc.json"
        if not bundle.is_file():
            continue
        status = review_status(bundle)
        result.pop(key, None)
        if status in {"APPROVED", "REJECTED", "NEEDS_REVIEW"}:
            records = sorted((bundle.parent / "reviews").glob("*.json"))
            if records:
                review = read_json(records[-1].read_text())
                result[key] = dict(
                    key=key,
                    decision={
                        "APPROVED": "accepted",
                        "REJECTED": "rejected",
                        "NEEDS_REVIEW": "requires_review",
                    }[status],
                    reviewer=review["reviewer"],
                    note=review["comments"],
                    timestamp=review["timestamp_utc"],
                    evidence_hashes=review["evidence_hashes"],
                )
    return result
