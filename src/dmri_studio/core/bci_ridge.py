"""Explicit, read-only FALCON H1 ridge reference experiment.

The scientific implementation is intentionally narrow. Optional NWB, SciPy,
and scikit-learn packages are imported only when an experiment is requested.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from dmri_studio.core.bci import (
    FEATURES,
    PERIOD_S,
    UPSTREAM_LOADER_COMMIT,
    bin_h1_spikes,
    inspect_falcon_h1,
)
from dmri_studio.core.bci_inventory import _nwb_metadata
from dmri_studio.core.dataset import SourceFingerprint

SPEC_SCHEMA = "falcon-h1-ridge-reference-spec-v1"
REPORT_SCHEMA = "falcon-h1-ridge-reference-report-v1"
CUSTOM_SPEC_SCHEMA = "falcon-h1-ridge-custom-trials-spec-v1"
CUSTOM_REPORT_SCHEMA = "falcon-h1-ridge-custom-trials-report-v1"
KERNEL_TAU_MS = 240.0
KERNEL_LENGTH = 12
ALPHAS = np.logspace(-5, 5, 20)
ROLE_PARTITIONS = {
    "training": {"held_in_calib"},
    "calibration": {"held_in_calib", "held_out_calib"},
    "evaluation": {"held_in_minival", "held_out_minival"},
}


def reference_filter(counts: np.ndarray) -> np.ndarray:
    """Pinned demo's 12-tap causal, normalized exponential filter."""
    try:
        from scipy.signal import convolve
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install dmri-studio[bci-reference] for H1 ridge experiments") from exc

    x = np.asarray(counts)
    if x.ndim != 2 or not np.isfinite(x).all():
        raise ValueError("spike counts must be a finite time × units matrix")
    kernel = np.exp(-np.arange(0, KERNEL_TAU_MS, 20) / KERNEL_TAU_MS)
    kernel /= kernel.sum()
    return np.array(
        [convolve(x[:, ch], kernel, mode="full")[: len(x)] for ch in range(x.shape[1])]
    ).T


def lagged_features(features: np.ndarray, history: int, *, zero_pad: bool) -> np.ndarray:
    """Current observation first, then preceding observations in time order."""
    if not isinstance(history, int) or history < 0 or history > 59:
        raise ValueError("history must be an integer from 0 through 59 bins")
    x = np.asarray(features)
    if x.ndim != 2:
        raise ValueError("features must be time × units")
    if not zero_pad and len(x) <= history:
        raise ValueError("history leaves no fitting observations")
    result = np.zeros((len(x) if zero_pad else len(x) - history, x.shape[1] * (history + 1)))
    for lag in range(history + 1):
        if zero_pad:
            if lag < len(x):
                result[lag:, lag * x.shape[1] : (lag + 1) * x.shape[1]] = x[: len(x) - lag]
        else:
            result[:, lag * x.shape[1] : (lag + 1) * x.shape[1]] = x[history - lag : len(x) - lag]
    return result


def fitting_selection(
    counts: np.ndarray, targets: np.ndarray, raw_mask: np.ndarray, history: int
) -> dict[str, Any]:
    """Reproduce upstream H1 selection while accounting for exclusions separately."""
    x = np.asarray(counts)
    y = np.asarray(targets, dtype=float)
    mask = np.asarray(raw_mask)
    if x.ndim != 2 or y.shape != (len(x), len(FEATURES)) or mask.shape != (len(x),):
        raise ValueError("counts, seven targets, and raw mask must share the sample count")
    if not np.logical_or(mask == 0, mask == 1).all():
        raise ValueError("raw eval_mask must contain only 0/1")
    if not np.isfinite(x).all():
        raise ValueError("nonfinite spike features are unsupported")
    filtered = reference_filter(x)
    split = int(0.8 * len(x))
    if split <= history or len(x) - split <= history:
        raise ValueError("recording too short for the 80/20 reference split and history")
    mean = np.nanmean(filtered[:split], axis=0)
    std = np.nanstd(filtered[:split], axis=0)
    std[std == 0] = 1
    normalized = (filtered - mean) / std
    still = np.all(np.abs(y) < 0.001, axis=1)
    nonfinite = ~np.isfinite(y).all(axis=1)
    raw_excluded = ~mask.astype(bool)
    selected = ~(raw_excluded | still | nonfinite)
    train_x = lagged_features(normalized[:split], history, zero_pad=False)
    test_x = lagged_features(normalized[split:], history, zero_pad=False)
    train_selection = selected[history:split]
    test_selection = selected[split + history :]
    return {
        "train_x": train_x[train_selection],
        "train_y": y[history:split][train_selection],
        "test_x": test_x[test_selection],
        "test_y": y[split + history :][test_selection],
        "filtered": filtered,
        "normalization_mean": mean,
        "normalization_std": std,
        "split_index": split,
        "exclusions": {
            "raw_mask_false": int(raw_excluded.sum()),
            "still_time": int(still.sum()),
            "nonfinite_target": int(nonfinite.sum()),
            "exclusive_reasons": {
                "raw_mask_false": int(raw_excluded.sum()),
                "still_after_raw_mask": int((~raw_excluded & still).sum()),
                "nonfinite_after_raw_mask_and_still": int(
                    (~raw_excluded & ~still & nonfinite).sum()
                ),
            },
            "excluded_union": int((~selected).sum()),
            "history_trim_train": history,
            "history_trim_test": history,
            "train_included": int(train_selection.sum()),
            "test_included": int(test_selection.sum()),
        },
    }


def _load_role(entry: dict[str, Any], role: str, spec_dir: Path) -> dict[str, Any]:
    import h5py

    required = {
        "path",
        "sha256",
        "dataset_version",
        "asset_id",
        "participant",
        "session_day",
        "recording_set",
        "benchmark_partition",
        "identity_evidence",
        "partition_evidence",
        "observation_span",
        "unit_ids",
    }
    if not isinstance(entry, dict) or set(entry) != required:
        raise ValueError(
            f"{role} requires explicit path, source, identity, partition, and unit ordering"
        )
    if entry["benchmark_partition"] not in ROLE_PARTITIONS[role]:
        raise ValueError(f"{role} partition contradicts its declared role")
    for field in required - {"unit_ids", "observation_span"}:
        if not isinstance(entry[field], str) or not entry[field].strip():
            raise ValueError(f"{role} {field} must be a nonempty documented declaration")
    path = (spec_dir / entry["path"]).expanduser().resolve(strict=True)
    before = SourceFingerprint.capture(path)
    inspected = inspect_falcon_h1(path)
    if inspected["source"]["sha256"] != entry["sha256"]:
        raise ValueError(f"{role} source SHA-256 differs from the experiment specification")
    sample_count = inspected["time"]["sample_count"]
    observed = _nwb_metadata(path)
    if observed["session_day"] is not None and entry["session_day"] != observed["session_day"]:
        raise ValueError(f"{role} session_day contradicts NWB metadata")
    if observed["participant"] is not None and (
        observed["participant"] != entry["participant"]
        and not observed["participant"].startswith(entry["participant"] + "-")
    ):
        raise ValueError(f"{role} participant contradicts NWB metadata")
    if (
        observed["recording_set"] is not None
        and entry["recording_set"] not in observed["recording_set"]
    ):
        raise ValueError(f"{role} recording_set contradicts NWB identifier")
    span = entry["observation_span"]
    if (
        not isinstance(span, dict)
        or set(span) != {"start", "stop", "evidence"}
        or not isinstance(span["start"], int)
        or not isinstance(span["stop"], int)
        or span["start"] < 0
        or span["stop"] - span["start"] != sample_count
        or not isinstance(span["evidence"], str)
        or not span["evidence"].strip()
    ):
        raise ValueError(f"{role} requires a documented observation span matching its sample count")
    unit_count = inspected["units"]["count"]
    if sample_count * unit_count > 5_000_000:
        raise ValueError(f"{role} exceeds the bounded 5-million-count reference experiment")
    with h5py.File(path, "r") as root:
        ids = np.asarray(root["units/id"][:]).tolist()
        if ids != entry["unit_ids"]:
            raise ValueError(f"{role} ordered unit IDs differ from the specification")
        clock = root["acquisition/OpenLoopKinematics/starting_time"]
        ends = float(clock[()]) + np.arange(sample_count) * float(clock.attrs["rate"])
        offsets = np.asarray(root["units/spike_times_index"][:], dtype=int)
        spikes = root["units/spike_times"]
        counts = np.zeros((sample_count, unit_count), dtype=np.uint64)
        previous = 0
        for column, stop in enumerate(offsets):
            counts[:, column] = bin_h1_spikes(np.asarray(spikes[previous:stop]), ends)
            previous = int(stop)
        targets = np.asarray(root["acquisition/OpenLoopKinematicsVelocity/data"][:], dtype=float)
        mask = np.asarray(root["acquisition/eval_mask/data"][:], dtype=bool)
    if not before.unchanged(path):
        raise ValueError(f"{role} source changed while reading")
    if counts.size and int(counts.max()) > 255:
        raise ValueError(f"{role} has a bin count above 255; pinned upstream uint8 would overflow")
    return {
        "declaration": entry,
        "source": path,
        "inspection": inspected,
        "counts": counts,
        "targets": targets,
        "raw_mask": mask,
        "unit_ids": ids,
    }


def _window_digests(row: dict[str, Any], width: int = 32) -> set[bytes]:
    """Flag identical numeric windows; identity alone cannot prove shared acquisition."""
    counts = row["counts"]
    targets = row["targets"]
    if len(counts) < width:
        width = len(counts)
    if not width:
        return set()
    combined = np.ascontiguousarray(np.column_stack((counts.astype(float), targets)))
    return {
        hashlib.blake2b(combined[i : i + width].tobytes(), digest_size=16).digest()
        for i in range(len(combined) - width + 1)
    }


def _validate_separation(rows: dict[str, dict[str, Any]]) -> None:
    roles = tuple(rows)
    for i, left_role in enumerate(roles):
        left = rows[left_role]
        for right_role in roles[i + 1 :]:
            right = rows[right_role]
            if (
                left["source"] == right["source"]
                or left["inspection"]["source"]["sha256"] == right["inspection"]["source"]["sha256"]
            ):
                raise ValueError(f"{left_role} and {right_role} repeat the same recording content")
            same_identity = all(
                left["declaration"][key] == right["declaration"][key]
                for key in ("participant", "session_day", "recording_set")
            )
            if same_identity:
                a = left["declaration"]["observation_span"]
                b = right["declaration"]["observation_span"]
                if max(a["start"], b["start"]) < min(a["stop"], b["stop"]):
                    raise ValueError(
                        f"{left_role} and {right_role} declare overlapping observation spans"
                    )
                if _window_digests(left) & _window_digests(right):
                    raise ValueError(
                        f"{left_role} and {right_role} have identical numeric windows; independence is unresolved"
                    )
            if _window_digests(left) & _window_digests(right):
                raise ValueError(
                    f"{left_role} and {right_role} have identical numeric windows despite different declared identities; independence is unresolved"
                )


def _custom_trial_selections(
    rows: dict[str, dict[str, Any]], declarations: Any
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Select whole source-indexed trials with isolated filter state."""
    import h5py

    if not isinstance(declarations, dict) or set(declarations) != set(ROLE_PARTITIONS):
        raise ValueError("custom trial selections require training, calibration and evaluation")
    selected_rows = {}
    summaries = {}
    for role, row in rows.items():
        item = declarations[role]
        if not isinstance(item, dict) or set(item) != {
            "start",
            "stop",
            "trial_numbers",
            "evidence",
        }:
            raise ValueError(f"{role} requires explicit source indices, trial IDs and evidence")
        start, stop = item["start"], item["stop"]
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(stop, int)
            or isinstance(stop, bool)
            or start < 0
            or stop <= start
            or stop >= len(row["counts"])
            or not isinstance(item["evidence"], str)
            or not item["evidence"].strip()
            or not isinstance(item["trial_numbers"], list)
        ):
            raise ValueError(f"{role} selection must contain complete in-file trials")
        with h5py.File(row["source"], "r") as root:
            trials = np.asarray(root["acquisition/TrialNum/data"][:], dtype=np.int64)
        boundaries = np.r_[0, np.flatnonzero(np.diff(trials) > 0) + 1]
        if start not in boundaries or stop not in boundaries:
            raise ValueError(f"{role} selection is not bounded by complete TrialNum segments")
        trial_ids = trials[boundaries[(boundaries >= start) & (boundaries < stop)]].tolist()
        if item["trial_numbers"] != trial_ids:
            raise ValueError(f"{role} selected trial IDs contradict source TrialNum")
        selected_rows[role] = {
            **row,
            "counts": row["counts"][start:stop].copy(),
            "targets": row["targets"][start:stop].copy(),
            "raw_mask": row["raw_mask"][start:stop].copy(),
        }
        summaries[role] = {
            **item,
            "samples": stop - start,
            "trial_count": len(trial_ids),
            "first_bin_window_start_s": round(
                row["inspection"]["time"]["start_s"] + (start - 1) * PERIOD_S, 9
            ),
            "last_bin_window_end_s": round(
                row["inspection"]["time"]["start_s"] + (stop - 1) * PERIOD_S, 9
            ),
            "raw_eval_mask_true": int(np.count_nonzero(row["raw_mask"][start:stop])),
            "raw_eval_mask_false": int(
                stop - start - np.count_nonzero(row["raw_mask"][start:stop])
            ),
            "analyzed_exposure_s": round((stop - start) * PERIOD_S, 9),
            "filter_reset_at_selection_start": role != "calibration",
        }

    training, calibration, evaluation = (rows[name] for name in ROLE_PARTITIONS)
    if any(
        calibration["declaration"][key] != evaluation["declaration"][key]
        for key in ("participant", "session_day", "recording_set")
    ):
        raise ValueError(
            "custom calibration and evaluation must belong to the same documented recording"
        )
    if (
        training["declaration"]["participant"],
        training["declaration"]["session_day"],
        training["declaration"]["recording_set"],
    ) == (
        evaluation["declaration"]["participant"],
        evaluation["declaration"]["session_day"],
        evaluation["declaration"]["recording_set"],
    ):
        raise ValueError("custom training must use a different documented session or set")
    n = len(evaluation["counts"])
    if len(calibration["counts"]) < n or not all(
        np.array_equal(calibration[key][:n], evaluation[key], equal_nan=True)
        for key in ("counts", "targets", "raw_mask")
    ):
        raise ValueError(
            "calibration/evaluation source-index mapping is not established by an exact prefix"
        )
    if calibration["unit_ids"] != evaluation["unit_ids"]:
        raise ValueError("calibration/evaluation unit-ID order differs within the same session")
    if calibration["inspection"]["time"]["start_s"] != evaluation["inspection"]["time"]["start_s"]:
        raise ValueError("calibration/evaluation source clocks differ")
    with (
        h5py.File(calibration["source"], "r") as left,
        h5py.File(evaluation["source"], "r") as right,
    ):
        if not np.array_equal(
            left["acquisition/OpenLoopKinematics/data"][:n],
            right["acquisition/OpenLoopKinematics/data"][:],
            equal_nan=True,
        ):
            raise ValueError("calibration/evaluation kinematic-observation prefix differs")
        if not np.array_equal(
            left["acquisition/TrialNum/data"][:n], right["acquisition/TrialNum/data"][:]
        ):
            raise ValueError("calibration/evaluation trial-number prefix differs")
        end_s = evaluation["inspection"]["time"]["last_sample_s"]
        left_spikes, right_spikes = left["units/spike_times"], right["units/spike_times"]
        left_stops = np.asarray(left["units/spike_times_index"][:], dtype=int)
        right_stops = np.asarray(right["units/spike_times_index"][:], dtype=int)
        a = b = 0
        for left_stop, right_stop in zip(left_stops, right_stops, strict=True):
            x = np.asarray(left_spikes[a:left_stop])
            y = np.asarray(right_spikes[b:right_stop])
            if np.any(y > end_s):
                raise ValueError("evaluation source has spikes beyond its final bin end")
            if not np.array_equal(x[x <= end_s], y[y <= end_s]):
                raise ValueError("calibration/evaluation spike-time prefix differs")
            a, b = int(left_stop), int(right_stop)
    cal = summaries["calibration"]
    ev = summaries["evaluation"]
    if cal["start"] >= ev["stop"]:
        unused_bins = cal["start"] - ev["stop"]
    elif ev["start"] >= cal["stop"]:
        unused_bins = ev["start"] - cal["stop"]
    else:
        raise ValueError("custom calibration and evaluation selections overlap in source indices")
    # The minival file includes its last bin's right boundary. An adjacent
    # calibration selection could therefore reuse a spike on that boundary,
    # even though the source-index ranges are disjoint. Require at least one
    # complete unused bin between roles; filtering is reset at evaluation.
    if unused_bins == 0:
        raise ValueError("custom calibration and evaluation share a bin boundary")
    first, second = (ev, cal) if ev["stop"] < cal["start"] else (cal, ev)
    if second["first_bin_window_start_s"] <= first["last_bin_window_end_s"]:
        raise ValueError("custom calibration and evaluation analyzed windows overlap")
    # Every selected role is checked, including against coincident numerical
    # observations under different declared session identities.
    for i, left_role in enumerate(ROLE_PARTITIONS):
        for right_role in tuple(ROLE_PARTITIONS)[i + 1 :]:
            if _window_digests(selected_rows[left_role]) & _window_digests(
                selected_rows[right_role]
            ):
                raise ValueError(
                    f"custom {left_role} and {right_role} selections have identical numeric windows; independence is unresolved"
                )
    return selected_rows, summaries


def _metrics(y: np.ndarray, predictions: np.ndarray) -> dict[str, Any]:
    from sklearn.metrics import r2_score

    if len(y) < 2:
        return {"defined": False, "reason": "fewer than two selected observations"}
    variances = np.var(y, axis=0)
    if np.any(variances == 0):
        return {
            "defined": False,
            "reason": "one or more constant target features",
            "constant_features": [FEATURES[i] for i in np.flatnonzero(variances == 0)],
        }
    return {
        "defined": True,
        "per_output_r2": dict(
            zip(
                FEATURES,
                map(float, r2_score(y, predictions, multioutput="raw_values")),
                strict=True,
            )
        ),
        "uniform_average_r2": float(r2_score(y, predictions, multioutput="uniform_average")),
        "variance_weighted_r2": float(r2_score(y, predictions, multioutput="variance_weighted")),
    }


def _array_identity(array: np.ndarray) -> dict[str, Any]:
    contiguous = np.ascontiguousarray(array)
    return {
        "shape": list(contiguous.shape),
        "dtype": str(contiguous.dtype),
        "sha256": hashlib.sha256(contiguous.tobytes()).hexdigest(),
    }


def _run_h1_ridge_experiment(spec_path: str | Path, *, custom: bool) -> dict[str, Any]:
    """Shared numerical path for the exact reference and explicit custom trials."""
    try:
        import scipy
        from sklearn import __version__ as sklearn_version
        from sklearn.linear_model import Ridge
        from sklearn.model_selection import GridSearchCV
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install dmri-studio[bci-reference] for H1 ridge experiments") from exc

    path = Path(spec_path).expanduser().resolve(strict=True)
    if path.stat().st_size > 1_000_000:
        raise ValueError("experiment specification exceeds the 1 MB read limit")
    spec_fingerprint = SourceFingerprint.capture(path)
    spec_bytes = path.read_bytes()
    spec = json.loads(spec_bytes)
    required = {
        "schema",
        "upstream_commit",
        "feature_order",
        "preprocessing",
        "model_selection",
        "roles",
    }
    if custom:
        required.add("selections")
    if not isinstance(spec, dict) or set(spec) != required:
        raise ValueError("unsupported or incomplete FALCON H1 ridge experiment specification")
    expected_schema = CUSTOM_SPEC_SCHEMA if custom else SPEC_SCHEMA
    if spec["schema"] != expected_schema or spec["upstream_commit"] != UPSTREAM_LOADER_COMMIT:
        raise ValueError("experiment schema or pinned upstream revision differs")
    if spec["feature_order"] != list(FEATURES):
        raise ValueError("H1 target feature order differs from the pinned reference")
    settings = spec["preprocessing"]
    if not isinstance(settings, dict) or set(settings) != {
        "bin_period_s",
        "tau_ms",
        "kernel_extent",
        "history_bins",
        "still_threshold",
        "train_fraction",
    }:
        raise ValueError("incomplete preprocessing declaration")
    if any(
        settings[key] != expected
        for key, expected in {
            "bin_period_s": PERIOD_S,
            "tau_ms": KERNEL_TAU_MS,
            "kernel_extent": 1,
            "still_threshold": 0.001,
            "train_fraction": 0.8,
        }.items()
    ):
        raise ValueError("preprocessing differs from the pinned H1 ridge reference")
    history = settings["history_bins"]
    if not isinstance(history, int) or history < 0 or history > 59:
        raise ValueError("history_bins must be an integer from 0 through 59")
    if custom and history != 0:
        raise ValueError("custom complete-trial experiment currently requires zero lag history")
    if spec["model_selection"] != {"alpha_logspace": [-5, 5, 20], "cv_folds": 5}:
        raise ValueError("model selection differs from the pinned GridSearchCV reference")
    if not isinstance(spec["roles"], dict) or set(spec["roles"]) != set(ROLE_PARTITIONS):
        raise ValueError("training, calibration and evaluation roles are all required")
    rows = {role: _load_role(spec["roles"][role], role, path.parent) for role in ROLE_PARTITIONS}
    calibration_identity = rows["calibration"]["declaration"]
    evaluation_identity = rows["evaluation"]["declaration"]
    if any(
        calibration_identity[key] != evaluation_identity[key]
        for key in ("participant", "session_day", "recording_set")
    ):
        raise ValueError(
            "calibration must be explicitly bound to the evaluation participant, session and set"
        )
    if (
        calibration_identity["benchmark_partition"].split("_", 1)[0]
        != evaluation_identity["benchmark_partition"].split("_", 1)[0]
    ):
        raise ValueError("calibration and evaluation held-in/held-out partitions conflict")
    selections = None
    if custom:
        rows, selections = _custom_trial_selections(rows, spec["selections"])
    else:
        _validate_separation(rows)
    widths = {len(row["unit_ids"]) for row in rows.values()}
    if len(widths) != 1:
        raise ValueError(
            "H1 reference requires the same channel count; IDs do not imply neuron identity"
        )
    training = rows["training"]
    fit = fitting_selection(training["counts"], training["targets"], training["raw_mask"], history)
    if len(fit["train_x"]) < 5 or len(fit["test_x"]) < 2:
        raise ValueError(
            "too few selected observations for five-fold reference fitting and holdout"
        )
    if np.any(np.var(fit["train_y"], axis=0) == 0):
        raise ValueError("constant training target makes reference R² model selection undefined")
    model = GridSearchCV(Ridge(), {"alpha": ALPHAS})
    model.fit(fit["train_x"], fit["train_y"])
    fitting_predictions = model.predict(fit["test_x"])
    calibration = rows["calibration"]["counts"].astype(float)
    calibration_mean = calibration.mean(axis=0)
    calibration_std = calibration.std(axis=0)
    calibration_std[calibration_std == 0] = 1
    evaluation = rows["evaluation"]
    # Upstream decoder resets at each file, zero-pads history, and does not
    # reset on TrialNum transitions. The 60-bin raw buffer exceeds the 12-tap kernel.
    eval_filtered = reference_filter(evaluation["counts"])
    eval_normalized = (eval_filtered - calibration_mean) / calibration_std
    eval_features = lagged_features(eval_normalized, history, zero_pad=True)
    predictions = model.predict(eval_features)
    eval_nonfinite = ~np.isfinite(evaluation["targets"]).all(axis=1)
    selected = evaluation["raw_mask"] & ~eval_nonfinite
    eval_metrics = _metrics(evaluation["targets"][selected], predictions[selected])
    holdout_metrics = _metrics(fit["test_y"], fitting_predictions)
    source_reports = {
        role: {
            "path": str(row["source"]),
            "sha256": row["inspection"]["source"]["sha256"],
            "dataset_version": row["declaration"]["dataset_version"],
            "asset_id": row["declaration"]["asset_id"],
            "participant": row["declaration"]["participant"],
            "session_day": row["declaration"]["session_day"],
            "recording_set": row["declaration"]["recording_set"],
            "partition": row["declaration"]["benchmark_partition"],
            "identity_evidence": row["declaration"]["identity_evidence"],
            "partition_evidence": row["declaration"]["partition_evidence"],
            "observation_span": row["declaration"]["observation_span"],
            "unit_ids": row["unit_ids"],
            "samples": row["inspection"]["time"]["sample_count"],
            **({"selected": selections[role]} if selections is not None else {}),
        }
        for role, row in rows.items()
    }
    report = {
        "schema": CUSTOM_REPORT_SCHEMA if custom else REPORT_SCHEMA,
        "specification": {"path": str(path), "sha256": hashlib.sha256(spec_bytes).hexdigest()},
        "upstream_commit": UPSTREAM_LOADER_COMMIT,
        "sources": source_reports,
        "configuration": {
            "preprocessing": settings,
            "model_selection": spec["model_selection"],
            **({"selections": selections} if selections is not None else {}),
        },
        "software": {
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "scikit_learn": sklearn_version,
        },
        "fitting": {
            "selected_alpha": float(model.best_params_["alpha"]),
            "cross_validation_score_uniform_average_r2": float(model.best_score_),
            "internal_holdout": holdout_metrics,
            "split_index": fit["split_index"],
            "exclusions": fit["exclusions"],
            "normalization_mean": fit["normalization_mean"].tolist(),
            "normalization_std": fit["normalization_std"].tolist(),
            "intermediate_identities": {
                "binned_counts": _array_identity(training["counts"]),
                "filtered_features": _array_identity(fit["filtered"]),
                "selected_lagged_train": _array_identity(fit["train_x"]),
                "selected_lagged_holdout": _array_identity(fit["test_x"]),
                "holdout_predictions": _array_identity(fitting_predictions),
            },
        },
        "evaluation": {
            "sample_count": len(predictions),
            "raw_eval_mask_true": int(evaluation["raw_mask"].sum()),
            "raw_eval_mask_false": int((~evaluation["raw_mask"]).sum()),
            "additional_nonfinite_target_exclusions": int(
                (evaluation["raw_mask"] & eval_nonfinite).sum()
            ),
            "included_samples": int(selected.sum()),
            "calibration_raw_mean": calibration_mean.tolist(),
            "calibration_raw_std": calibration_std.tolist(),
            "predictions": predictions.tolist(),
            "metrics": eval_metrics,
            "intermediate_identities": {
                "binned_counts": _array_identity(evaluation["counts"]),
                "filtered_features": _array_identity(eval_filtered),
                "prediction_features": _array_identity(eval_features),
                "raw_eval_mask": _array_identity(evaluation["raw_mask"]),
                "predictions": _array_identity(predictions),
            },
            "metric_scope": (
                "custom disjoint-trial real-data result; not the FALCON benchmark score"
                if custom
                else "local single-recording descriptive result; not the official FALCON benchmark score"
            ),
        },
        "limitations": [
            "Unit IDs and matching channel counts do not establish cross-session biological identity.",
            "Exact repeated-observation detection is conservative but cannot prove independent acquisition.",
            "The pinned reference fits on filtered training data but deploys per-recording raw-count normalization.",
            "No trial-boundary reset; the causal filter carries state across TrialNum changes.",
        ],
    }
    if not all(math.isfinite(value) for row in predictions for value in row):
        raise ValueError("nonfinite reference prediction")
    if not spec_fingerprint.unchanged(path):
        raise ValueError("experiment specification changed during execution")
    if custom:
        assert selections is not None
        cal, ev = selections["calibration"], selections["evaluation"]
        gap_bins = (
            cal["start"] - ev["stop"] if cal["start"] >= ev["stop"] else ev["start"] - cal["stop"]
        )
        report["temporal_isolation"] = {
            "unused_source_bins_between_calibration_and_evaluation": gap_bins,
            "unanalyzed_gap_s_between_selected_windows": round(gap_bins * PERIOD_S, 9),
            "causal_filter_support_bins": KERNEL_LENGTH,
            "history_bins": history,
            "filter_reset_at_training_and_evaluation_start": True,
            "calibration_uses_no_filter": True,
            "calibration_parameters_from_selected_raw_counts_only": True,
        }
        report["limitations"].append(
            "Custom source-indexed complete trials and filter resets differ from the unchanged upstream file-level pipeline."
        )
    return report


def run_h1_ridge_experiment(spec_path: str | Path) -> dict[str, Any]:
    """Run one hash-bound pinned reference experiment without source writes."""
    return _run_h1_ridge_experiment(spec_path, custom=False)


def run_h1_custom_trial_experiment(spec_path: str | Path) -> dict[str, Any]:
    """Run a custom nonoverlapping whole-trial experiment on the shared math path."""
    return _run_h1_ridge_experiment(spec_path, custom=True)


def write_h1_ridge_report(report: dict[str, Any], output: str | Path) -> Path:
    """Create an exclusive path-bearing report; never replace sources or spec."""
    destination = Path(output).expanduser().resolve(strict=False)
    repository = Path(__file__).resolve().parents[3]
    if destination.is_relative_to(repository):
        raise ValueError("path-bearing experiment reports must be outside the repository")
    protected = {Path(report["specification"]["path"])}
    protected.update(Path(row["path"]) for row in report["sources"].values())
    if destination in protected:
        raise ValueError("experiment report must not replace a source or specification")
    with destination.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    return destination
