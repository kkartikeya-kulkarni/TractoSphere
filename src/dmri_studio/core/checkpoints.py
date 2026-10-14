"""Completed native-stage recovery without promoting a failed attempt.

Only checkpoints saved after full native value/gradient/residual verification are
eligible. Running manifests and unverified/legacy partial artifacts are refused.
Continuation uses a new attempt, never an in-place tool restart or fake success.
"""

from pathlib import Path

from .execution import hash_file
from .workflow import HistoryClaim, ProcessingHistory


def record_native_checkpoint(dataset, folder, manifest, stage):
    """Freeze each verified prefix immediately, before the next tool starts."""
    completed = [
        s
        for s in ("denoise", "gibbs")
        if manifest.get("validation", {}).get(s, {}).get("status") == "passed"
    ]
    if stage not in completed:
        raise ValueError("Cannot checkpoint an unverified native stage")
    names = ["dwi.mif"]
    for name in completed:
        names.extend([name + ".mif", name + ".nii", name + "-residual.nii"])
        if name == "denoise":
            names.extend(["noise.mif", "noise.nii"])
    outputs = {
        name: {"sha256": hash_file(folder / name), "bytes": (folder / name).stat().st_size}
        for name in names
    }
    manifest.setdefault("recovery_checkpoints", []).append(
        {
            "stage": stage,
            "completed_operations": completed,
            "outputs": outputs,
            "verification": "Full native image, embedded gradients and residual verification passed; scientific review pending",
        }
    )


def native_recovery(path: Path, record: dict, *, cancelled=lambda: False) -> dict | None:
    """Expose only the last immutable verified prefix of a terminal failed run."""
    if (
        record.get("status") not in {"failed", "cancelled"}
        or record.get("inputs_unchanged") is not True
        or not record.get("recovery_checkpoints")
    ):
        return None
    p = record["plan"]
    if p.get("kind") != "mrtrix_native_execution_plan":
        return None
    folder = Path(p["output_directory"]).resolve()
    if path.resolve().parent != folder:
        raise ValueError("Checkpoint manifest is outside its attempt")
    cp = record["recovery_checkpoints"][-1]
    completed = cp["completed_operations"]
    if (
        not completed
        or any(op not in {"denoise", "gibbs"} for op in completed)
        or cp["stage"] != completed[-1]
    ):
        raise ValueError("Invalid native checkpoint prefix")
    planned = [op for op in ("denoise", "gibbs") if op in p["operations"]]
    if completed != planned[: len(completed)]:
        raise ValueError("Checkpoint is not a prefix of the selected native operations")
    required = {"dwi.mif"}
    for op in completed:
        required.update({op + ".mif", op + ".nii", op + "-residual.nii"})
        if op == "denoise":
            required.update({"noise.mif", "noise.nii"})
    if not required <= set(cp["outputs"]):
        raise ValueError("Checkpoint lacks required verified artifacts")
    for op in completed:
        if record.get("validation", {}).get(op, {}).get("status") != "passed" or not any(
            s["step"] == op and s["status"] == "validated" for s in record["steps"]
        ):
            raise ValueError("Checkpoint lacks executed numerical validation")
    checks = {p["input_paths"][k]: v["sha256"] for k, v in p["inputs"].items()}
    checks.update(p.get("extra_input_hashes", {}))
    for name, v in cp["outputs"].items():
        file = (folder / name).resolve()
        if not file.is_relative_to(folder):
            raise ValueError("Checkpoint artifact escapes attempt")
        checks[str(file)] = v["sha256"]
    for file, digest in checks.items():
        if hash_file(Path(file), cancelled) != digest:
            raise ValueError("Checkpoint evidence changed: " + file)
    previous = ProcessingHistory.from_dict(p["history"])
    history = ProcessingHistory(
        tuple(c for c in previous.claims if c.stage not in completed)
        + tuple(
            HistoryClaim(
                op,
                True,
                "inferred",
                f"Numerically verified checkpoint in failed attempt {path}",
                "high",
                record["finished_at"][:10],
            )
            for op in completed
        ),
        True,
    )
    paths = {
        "dwi": str(folder / (cp["stage"] + ".nii")),
        "bval": p["input_paths"]["bval"],
        "bvec": p["input_paths"]["bvec"],
    }
    if p["input_paths"].get("json"):
        paths["json"] = p["input_paths"]["json"]
    product = {
        "label": "Verified " + cp["stage"] + " checkpoint; enclosing attempt failed",
        "paths": paths,
        "sha256": {
            k: checks[str(Path(v).resolve())] if str(Path(v).resolve()) in checks else checks[v]
            for k, v in paths.items()
        },
        "history": history.to_dict(),
        "source_manifest": str(path),
        "source_sha256": hash_file(path, cancelled),
        "extra_hashes": checks,
    }
    remaining = [op for op in p["operations"] if op not in completed and op != "convert"]
    return {
        "product": product,
        "handoff": {"native_record": str(path)},
        "suggested_operations": remaining,
        "native_parameters": {
            k: v
            for k, v in p["parameters"].items()
            if k in {"extent", "nshifts", "minW", "maxW", "nthreads", "clean_scale"}
        },
        "notice": "Continue only remaining selected operations in a new directory. Failed attempt and scientific uncertainty remain preserved.",
    }
