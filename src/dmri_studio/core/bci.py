"""Read-only inspection of the selected modern FALCON H1 NWB structure.

This module deliberately does not load a decoder or infer anatomy.
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

SCHEMA = "falcon-h1-modern-nwb-v2"
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


def _missing_by_column(data: Any, mask: Any, label: str) -> tuple[list[int], dict[str, list[int]]]:
    if len(data.shape) != 2 or data.shape[1] != len(FEATURES) or data.shape[0] == 0:
        raise ValueError(f"{label} must have nonempty time × 7 dimensions")
    if data.dtype.kind not in "iuf":
        raise ValueError(f"{label} must be numeric")
    missing = np.zeros(len(FEATURES), dtype=np.int64)
    by_mask = {
        "true": np.zeros(len(FEATURES), dtype=np.int64),
        "false": np.zeros(len(FEATURES), dtype=np.int64),
    }
    for offset in range(0, data.shape[0], 8192):
        block = data[offset : offset + 8192]
        raw_mask = np.asarray(mask[offset : offset + block.shape[0]], dtype=bool)
        invalid = ~np.isfinite(block)
        missing += np.count_nonzero(invalid, axis=0)
        for status, selected in (("true", raw_mask), ("false", ~raw_mask)):
            by_mask[status] += np.count_nonzero(invalid[selected], axis=0)
    return (
        [int(value) for value in missing],
        {key: value.tolist() for key, value in by_mask.items()},
    )


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


def h1_bin_windows(
    bin_end_s: np.ndarray, period_s: float = PERIOD_S
) -> tuple[np.ndarray, np.ndarray]:
    """Return analyzed [start, end) windows; the final end is inclusive.

    H1 timestamps denote bin ends in seconds. A long interval contributes only
    its proximal final ``period_s``; a short interval contributes its actual
    width. No spikes in omitted gaps are assigned to neighboring bins.
    """
    ends = np.asarray(bin_end_s, dtype=np.float64)
    if ends.ndim != 1 or not np.isfinite(ends).all():
        raise ValueError("H1 bin ends must be a finite one-dimensional vector")
    if not math.isfinite(period_s) or period_s <= 0:
        raise ValueError("H1 bin period must be positive and finite")
    if ends.size and np.any(np.diff(ends) <= 0):
        raise ValueError("H1 bin ends must increase strictly")
    starts = ends - period_s
    if ends.size > 1:
        starts[1:] = np.maximum(starts[1:], ends[:-1])
    return starts, ends


def describe_h1_timing(bin_end_s: np.ndarray, period_s: float = PERIOD_S) -> dict[str, Any]:
    """Describe represented gaps and actual analyzed exposure, without spikes."""
    starts, ends = h1_bin_windows(bin_end_s, period_s)
    intervals = np.diff(ends)
    gaps = intervals > period_s + 1e-9
    short = intervals < period_s - 1e-9
    return {
        "sample_count": int(ends.size),
        "nominal_duration_s": round(ends.size * period_s, 9),
        "analyzed_exposure_s": round(math.fsum(float(x) for x in ends - starts), 9),
        "gap_count": int(np.count_nonzero(gaps)),
        "gap_duration_s": round(math.fsum(float(x) for x in intervals[gaps] - period_s), 9),
        "short_interval_count": int(np.count_nonzero(short)),
    }


def _spike_bin_indices(
    spikes: np.ndarray, starts: np.ndarray, ends: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    # np.histogram uses left-inclusive edges, except its final right edge.
    indices = np.searchsorted(ends, spikes, side="right")
    if ends.size:
        indices[spikes == ends[-1]] = ends.size - 1
    in_range = indices < ends.size
    valid = np.zeros(spikes.size, dtype=bool)
    # Tolerate only floating arithmetic at a constructed lower edge (e.g.
    # 0.05 - 0.02). This does not widen a window at measurement precision.
    valid[in_range] = spikes[in_range] >= starts[indices[in_range]] - 1e-12
    return indices, valid


def bin_h1_spikes(
    spike_times_s: np.ndarray, bin_end_s: np.ndarray, period_s: float = PERIOD_S
) -> np.ndarray:
    """Bin one sorted H1 unit with uint64 counts; never wrap at 255.

    Duplicate finite spike times are counted independently. Unsorted or
    nonfinite timestamps are rejected. Empty windows return an empty vector.
    """
    starts, ends = h1_bin_windows(bin_end_s, period_s)
    spikes = np.asarray(spike_times_s, dtype=np.float64)
    if spikes.ndim != 1 or not np.isfinite(spikes).all():
        raise ValueError("H1 spike times must be a finite one-dimensional vector")
    if np.any(np.diff(spikes) < 0):
        raise ValueError("H1 spike times must be sorted within each unit")
    if not ends.size:
        return np.zeros(0, dtype=np.uint64)
    indices, valid = _spike_bin_indices(spikes, starts, ends)
    # bincount uses platform integers; cast after asserting representability.
    counts = np.bincount(indices[valid], minlength=ends.size)
    if np.any(counts < 0):
        raise OverflowError("H1 spike-bin count exceeded platform integer range")
    return counts.astype(np.uint64, copy=False)


def _unit_summary(
    root: Any, h5py: Any, starts: np.ndarray, ends: np.ndarray, trials: list[dict[str, int]]
) -> tuple[dict[str, Any], list[int]]:
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
    ragged_ends = np.asarray(indexes[:], dtype=np.int64)
    if (
        np.any(ragged_ends < 0)
        or np.any(np.diff(ragged_ends) < 0)
        or ragged_ends[-1] != spikes.shape[0]
    ):
        raise ValueError("units/spike_times_index is inconsistent with spike_times")
    lengths = np.diff(np.r_[0, ragged_ends])
    # Ragged units are read in bounded chunks. Only counts and source identifiers
    # leave this function; a full time x unit matrix is never constructed.
    per_unit = []
    per_trial = [0] * len(trials)
    stops = np.asarray([trial["stop_index_exclusive"] for trial in trials], dtype=np.int64)
    outside = {"before": 0, "gap": 0, "after": 0}
    prior = 0
    exposure_s = round(math.fsum(float(x) for x in ends - starts), 9)
    for unit_id, stop in zip(unit_ids, ragged_ends, strict=True):
        observed = 0
        last_spike = None
        duplicates = 0
        for offset in range(int(prior), int(stop), 131072):
            block = np.asarray(spikes[offset : min(offset + 131072, int(stop))], dtype=np.float64)
            if not np.isfinite(block).all():
                raise ValueError("units/spike_times contains nonfinite values")
            if (last_spike is not None and block.size and block[0] < last_spike) or np.any(
                np.diff(block) < 0
            ):
                raise ValueError("units/spike_times must be sorted within each unit")
            duplicates += int(np.count_nonzero(np.diff(block) == 0))
            if last_spike is not None and block.size and block[0] == last_spike:
                duplicates += 1
            if block.size:
                last_spike = float(block[-1])
            indices, valid = _spike_bin_indices(block, starts, ends)
            observed += int(np.count_nonzero(valid))
            if np.any(valid):
                trial_indices = np.searchsorted(stops, indices[valid], side="right")
                for trial_index, count in enumerate(
                    np.bincount(trial_indices, minlength=len(trials))
                ):
                    per_trial[trial_index] += int(count)
            outside["before"] += int(np.count_nonzero(~valid & (block < starts[0])))
            outside["after"] += int(np.count_nonzero(~valid & (block > ends[-1])))
            outside["gap"] += int(
                np.count_nonzero(~valid & (block >= starts[0]) & (block <= ends[-1]))
            )
        per_unit.append(
            {
                "unit_id": int(unit_id),
                "observed_spikes": observed,
                "rate_hz": observed / exposure_s,
                "duplicate_timestamps": duplicates,
            }
        )
        prior = int(stop)
    return dict(
        count=int(ids.shape[0]),
        spike_count=int(spikes.shape[0]),
        empty_unit_count=int(np.count_nonzero(lengths == 0)),
        zero_observed_unit_count=sum(row["observed_spikes"] == 0 for row in per_unit),
        per_unit=per_unit,
        spikes_outside_analyzed_windows=outside,
    ), per_trial


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
        if size == 0:
            raise ValueError("OpenLoopKinematics must have nonempty time × 7 dimensions")
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
        trials = _trial_segments(parsed["TrialNum"][0], size)
        raw_mask = parsed["eval_mask"][0]
        mask = _mask_counts(raw_mask, size)
        position_missing, position_by_mask = _missing_by_column(
            position, raw_mask, "OpenLoopKinematics"
        )
        velocity_missing, velocity_by_mask = _missing_by_column(
            velocity, raw_mask, "OpenLoopKinematicsVelocity"
        )
        bin_ends = start + np.arange(size, dtype=np.float64) * period
        bin_starts, bin_ends = h1_bin_windows(bin_ends, period)
        widths = bin_ends - bin_starts
        timing = describe_h1_timing(bin_ends, period)
        units, trial_spikes = _unit_summary(root, h5py, bin_starts, bin_ends, trials)
        trial_summaries = []
        for trial, spike_count in zip(trials, trial_spikes, strict=True):
            first, stop = trial["start_index"], trial["stop_index_exclusive"]
            selected_count = 0
            for offset in range(first, stop, 8192):
                selected_count += int(np.count_nonzero(raw_mask[offset : min(offset + 8192, stop)]))
            trial_summaries.append(
                {
                    **trial,
                    "analyzed_exposure_s": round(
                        math.fsum(float(x) for x in widths[first:stop]), 9
                    ),
                    "observed_spikes_all_units": spike_count,
                    "raw_eval_mask_true_count": selected_count,
                    "raw_eval_mask_false_count": stop - first - selected_count,
                }
            )
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
            "start_s": start,
            "period_s": period,
            "last_sample_s": start + (size - 1) * period,
            **timing,
        },
        "features": {"labels": list(FEATURES), "storage_units": storage_units},
        "kinematics": {
            "position_nonfinite_by_feature": position_missing,
            "velocity_nonfinite_by_feature": velocity_missing,
            "position_nonfinite_by_raw_mask": position_by_mask,
            "velocity_nonfinite_by_raw_mask": velocity_by_mask,
        },
        "units": units,
        "trials": {"count": len(trials), "segments": trial_summaries},
        "evaluation_mask": mask,
        "measurement_kind": "descriptive; no quality score or scientific acceptance",
        "scientific_acceptance": "unassigned",
    }
