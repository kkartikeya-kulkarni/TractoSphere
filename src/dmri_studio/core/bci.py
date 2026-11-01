"""Read-only inspection of the selected modern FALCON H1 NWB structure.

This module deliberately does not load a decoder, bin spikes, or infer anatomy.
The optional h5py dependency is imported only when this API is called.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from dmri_studio import __version__
from dmri_studio.core.dataset import SourceFingerprint
from dmri_studio.core.execution import hash_file

SCHEMA = "falcon-h1-modern-nwb-v1"
UPSTREAM_LOADER_COMMIT = "770c9b9597b8f58bf67b5a9ec6401ef705a2cb0f"
PERIOD_S = 0.02
FEATURES = ("tx", "ty", "tz", "rx", "g1", "g2", "g3")
SERIES = (
    "OpenLoopKinematics",
    "OpenLoopKinematicsVelocity",
    "TrialNum",
    "eval_mask",
)


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _dataset(root: Any, name: str, h5py: Any) -> Any:
    """Require an in-file dataset, not an external or soft HDF5 link."""
    current = root
    for part in name.split("/"):
        if not isinstance(current, h5py.Group):
            raise ValueError(f"Missing required H1 field: {name}")
        if not isinstance(current.get(part, getlink=True), h5py.HardLink):
            raise ValueError(f"Missing or linked required H1 field: {name}")
        current = current[part]
    if not isinstance(current, h5py.Dataset):
        raise ValueError(f"Required H1 field is not a dataset: {name}")
    return current


def _series(root: Any, name: str, h5py: Any) -> tuple[Any, float, float]:
    data = _dataset(root, f"acquisition/{name}/data", h5py)
    clock = _dataset(root, f"acquisition/{name}/starting_time", h5py)
    if clock.shape != ():
        raise ValueError(f"{name} starting_time must be a scalar")
    try:
        start = float(clock[()])
        period = float(clock.attrs["rate"])
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError(f"{name} requires numeric starting_time and rate") from exc
    if not math.isfinite(start) or not math.isfinite(period) or period <= 0:
        raise ValueError(f"{name} has invalid timing")
    if _text(clock.attrs.get("unit", "")) != "seconds":
        raise ValueError(f"{name} starting_time unit must be seconds")
    if not math.isclose(period, PERIOD_S, rel_tol=1e-6, abs_tol=1e-9):
        raise ValueError(f"{name} must use the upstream H1 20 ms time step")
    return data, start, period


def _missing_by_column(data: Any, label: str) -> list[int]:
    if len(data.shape) != 2 or data.shape[1] != len(FEATURES) or data.shape[0] == 0:
        raise ValueError(f"{label} must have nonempty time × 7 dimensions")
    if data.dtype.kind not in "iuf":
        raise ValueError(f"{label} must be numeric")
    missing = np.zeros(len(FEATURES), dtype=np.int64)
    for offset in range(0, data.shape[0], 8192):
        block = data[offset : offset + 8192]
        missing += np.count_nonzero(~np.isfinite(block), axis=0)
    return [int(value) for value in missing]


def _one_dimensional(data: Any, size: int, label: str) -> None:
    if data.shape != (size,):
        raise ValueError(f"{label} length must equal the kinematic sample count")
    if data.dtype.kind not in "biuf":
        raise ValueError(f"{label} must be numeric or boolean")


def _trial_segments(data: Any, size: int) -> list[dict[str, int]]:
    _one_dimensional(data, size, "TrialNum")
    values = np.asarray(data[:])
    if not np.isfinite(values).all() or not np.equal(values, np.floor(values)).all():
        raise ValueError("TrialNum must contain finite integer-valued numbers")
    if np.any(np.diff(values) < 0):
        raise ValueError("TrialNum must not decrease; upstream H1 uses increases as boundaries")
    starts = np.r_[0, np.flatnonzero(np.diff(values) > 0) + 1]
    stops = np.r_[starts[1:], size]
    return [
        dict(
            start_index=int(start), stop_index_exclusive=int(stop), trial_number=int(values[start])
        )
        for start, stop in zip(starts, stops, strict=True)
    ]


def _mask_counts(data: Any, size: int) -> dict[str, int | str]:
    _one_dimensional(data, size, "eval_mask")
    if data.dtype.kind not in "biu":
        raise ValueError("eval_mask must contain boolean or binary integer values")
    true_count = 0
    for offset in range(0, size, 8192):
        block = np.asarray(data[offset : offset + 8192])
        if not np.logical_or(block == 0, block == 1).all():
            raise ValueError("eval_mask must contain only 0/1 values")
        true_count += int(np.count_nonzero(block))
    return dict(source="eval_mask", true_count=true_count, false_count=size - true_count)


def _unit_summary(root: Any, h5py: Any) -> dict[str, int]:
    ids = _dataset(root, "units/id", h5py)
    spikes = _dataset(root, "units/spike_times", h5py)
    indexes = _dataset(root, "units/spike_times_index", h5py)
    if len(ids.shape) != 1 or ids.shape[0] == 0 or ids.dtype.kind not in "iu":
        raise ValueError("units/id must contain integer unit identifiers")
    if len(spikes.shape) != 1 or spikes.dtype.kind not in "iuf":
        raise ValueError("units/spike_times must be a numeric vector")
    if indexes.shape != ids.shape or indexes.dtype.kind not in "iu":
        raise ValueError("units/spike_times_index must match units/id")
    unit_ids = np.asarray(ids[:])
    if np.unique(unit_ids).size != unit_ids.size:
        raise ValueError("units/id contains duplicate identifiers")
    ends = np.asarray(indexes[:], dtype=np.int64)
    if np.any(ends < 0) or np.any(np.diff(ends) < 0) or ends[-1] != spikes.shape[0]:
        raise ValueError("units/spike_times_index is inconsistent with spike_times")
    for offset in range(0, spikes.shape[0], 131072):
        if not np.isfinite(spikes[offset : offset + 131072]).all():
            raise ValueError("units/spike_times contains nonfinite values")
    lengths = np.diff(np.r_[0, ends])
    return dict(
        count=int(ids.shape[0]),
        spike_count=int(spikes.shape[0]),
        empty_unit_count=int(np.count_nonzero(lengths == 0)),
    )


def inspect_falcon_h1(path: str | Path) -> dict[str, Any]:
    """Validate and summarize one modern H1 NWB source without changing it.

    The result contains no neural samples, covariate arrays, or subject metadata.
    A full-file SHA-256 and pre/post identity check bind the observations to the
    exact source bytes. Other FALCON tasks and older Blacklist-only H1 files are
    intentionally outside this first contract.
    """
    try:
        import h5py
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install dmri-studio[bci] to inspect FALCON H1 NWB files") from exc

    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != ".nwb":
        raise ValueError("Select an existing .nwb file")
    before = SourceFingerprint.capture(source)
    with h5py.File(source, "r") as root:
        version = _text(root.attrs.get("nwb_version", ""))
        if not version.startswith("2."):
            raise ValueError("Expected an NWB 2.x HDF5 recording")
        parsed = {name: _series(root, name, h5py) for name in SERIES}
        position, start, period = parsed["OpenLoopKinematics"]
        velocity = parsed["OpenLoopKinematicsVelocity"][0]
        size = position.shape[0] if len(position.shape) == 2 else 0
        for name, (data, other_start, other_period) in parsed.items():
            if len(data.shape) == 0 or data.shape[0] != size:
                raise ValueError(f"{name} sample count differs from OpenLoopKinematics")
            if not math.isclose(other_start, start, abs_tol=1e-8) or not math.isclose(
                other_period, period, rel_tol=1e-6, abs_tol=1e-9
            ):
                raise ValueError(f"{name} timing differs from OpenLoopKinematics")
        for name in ("OpenLoopKinematics", "OpenLoopKinematicsVelocity"):
            group = root[f"acquisition/{name}"]
            labels = tuple(
                part.strip() for part in _text(group.attrs.get("description", "")).split(",")
            )
            if labels != FEATURES:
                raise ValueError(f"{name} requires the documented seven H1 feature labels")
        position_missing = _missing_by_column(position, "OpenLoopKinematics")
        velocity_missing = _missing_by_column(velocity, "OpenLoopKinematicsVelocity")
        trials = _trial_segments(parsed["TrialNum"][0], size)
        mask = _mask_counts(parsed["eval_mask"][0], size)
        units = _unit_summary(root, h5py)
        storage_units = {
            name: _text(parsed[name][0].attrs.get("unit", "unknown"))
            for name in ("OpenLoopKinematics", "OpenLoopKinematicsVelocity")
        }
    digest = hash_file(source)
    if not before.unchanged(source):
        raise ValueError("NWB source changed during inspection")
    return {
        "schema": SCHEMA,
        "inspector_version": __version__,
        "upstream_loader_commit": UPSTREAM_LOADER_COMMIT,
        "source": {"path": str(source), "sha256": digest, "bytes": before.size},
        "nwb_version": version,
        "time": {
            "sample_count": size,
            "start_s": start,
            "period_s": period,
            "last_sample_s": start + (size - 1) * period,
        },
        "features": {"labels": list(FEATURES), "storage_units": storage_units},
        "kinematics": {
            "position_nonfinite_by_feature": position_missing,
            "velocity_nonfinite_by_feature": velocity_missing,
        },
        "units": units,
        "trials": {"count": len(trials), "segments": trials},
        "evaluation_mask": mask,
        "scientific_acceptance": "unassigned",
    }
