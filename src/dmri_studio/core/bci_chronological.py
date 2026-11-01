"""Read-only, within-recording H1 constant/ridge chronology comparison.

This is a custom research analysis, not the pinned FALCON benchmark protocol.
Optional NWB and ML packages load only when the experiment runs.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from dmri_studio.core.bci import FEATURES, PERIOD_S, UPSTREAM_LOADER_COMMIT
from dmri_studio.core.bci_ridge import KERNEL_LENGTH, KERNEL_TAU_MS, _load_role, reference_filter
from dmri_studio.core.dataset import SourceFingerprint

SPEC_SCHEMA = "falcon-h1-ridge-chronological-spec-v1"
REPORT_SCHEMA = "falcon-h1-ridge-chronological-report-v1"
_POLICIES = {
    "training_selection": "raw_mask_true_finite_nonstill",
    "calibration_selection": "raw_mask_true_all_targets_ignored",
    "evaluation_selection": "raw_mask_true_finite_targets",
    "filter_reset": "each_role_start; causal state carries across TrialNum within role",
    "model_selection": "none; fixed predeclared alpha",
}


def _validate_configuration(value: Any) -> float:
    expected = {
        "bin_period_s": PERIOD_S,
        "tau_ms": KERNEL_TAU_MS,
        "kernel_extent": 1,
        "history_bins": 0,
        "still_threshold": 0.001,
        **_POLICIES,
    }
    if not isinstance(value, dict) or set(value) != set(expected) | {"ridge_alpha"}:
        raise ValueError("chronological configuration is incomplete")
    if any(value[key] != setting for key, setting in expected.items()):
        raise ValueError("chronological preprocessing or selection policy differs from v1")
    alpha = value["ridge_alpha"]
    if (
        isinstance(alpha, bool)
        or not isinstance(alpha, (int, float))
        or not math.isfinite(alpha)
        or alpha <= 0
    ):
        raise ValueError("ridge_alpha must be explicitly positive and finite")
    return float(alpha)


def _validate_selections(source: dict[str, Any], declarations: Any) -> dict[str, dict[str, Any]]:
    import h5py

    if not isinstance(declarations, dict) or set(declarations) != {
        "training",
        "calibration",
        "evaluation",
        "unused",
    }:
        raise ValueError(
            "chronological selections require training, calibration, evaluation and unused intervals"
        )
    with h5py.File(source["source"], "r") as root:
        trial_numbers = np.asarray(root["acquisition/TrialNum/data"][:], dtype=np.int64)
    n = len(trial_numbers)
    boundaries = set(np.r_[0, np.flatnonzero(np.diff(trial_numbers) > 0) + 1, n].tolist())

    def check(
        name: str, item: Any, *, allow_file_end: bool = False, unselected: bool = False
    ) -> dict[str, Any]:
        required = {"start", "stop", "trial_numbers"} | ({"reason"} if unselected else set())
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError(f"{name} needs original indices and exact TrialNum IDs")
        if unselected and (not isinstance(item["reason"], str) or not item["reason"].strip()):
            raise ValueError(f"{name} requires a reason for not using these observations")
        start, stop, ids = item["start"], item["stop"], item["trial_numbers"]
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(stop, int)
            or isinstance(stop, bool)
            or start < 0
            or stop <= start
            or stop > n
            or (stop == n and not allow_file_end)
            or start not in boundaries
            or stop not in boundaries
            or not isinstance(ids, list)
        ):
            raise ValueError(f"{name} must use complete, source-indexed TrialNum segments")
        starts = np.r_[0, np.flatnonzero(np.diff(trial_numbers) > 0) + 1]
        actual = trial_numbers[starts[(starts >= start) & (starts < stop)]].tolist()
        if ids != actual:
            raise ValueError(f"{name} trial IDs contradict source TrialNum")
        mask = source["raw_mask"][start:stop]
        return {
            **item,
            "samples": stop - start,
            "trial_count": len(actual),
            "exposure_s": round((stop - start) * PERIOD_S, 9),
            "raw_eval_mask_true": int(np.count_nonzero(mask)),
            "raw_eval_mask_false": int(len(mask) - np.count_nonzero(mask)),
            "first_window_start_s": round(
                source["inspection"]["time"]["start_s"] + (start - 1) * PERIOD_S, 9
            ),
            "last_window_end_s": round(
                source["inspection"]["time"]["start_s"] + (stop - 1) * PERIOD_S, 9
            ),
        }

    selections = {
        role: check(role, declarations[role]) for role in ("training", "calibration", "evaluation")
    }
    unused = declarations["unused"]
    if not isinstance(unused, list) or len(unused) != 2:
        raise ValueError(
            "chronological v1 requires an explicit inter-role gap and trailing unselected segment"
        )
    gap = check("unused gap", unused[0], unselected=True)
    trailing = check("trailing unselected", unused[1], allow_file_end=True, unselected=True)
    train, cal, evaluation = (selections[key] for key in ("training", "calibration", "evaluation"))
    if not (
        train["start"] == 0
        and train["stop"] == cal["start"]
        and cal["stop"] == gap["start"]
        and gap["stop"] == evaluation["start"]
        and evaluation["stop"] == trailing["start"]
        and trailing["stop"] == n
    ):
        raise ValueError(
            "training and calibration must precede evaluation without shared observations"
        )
    if cal["last_window_end_s"] >= evaluation["first_window_start_s"]:
        raise ValueError("future calibration or overlapping analyzed windows are unsupported")
    selections["unused_gap"] = {**gap, "reason": unused[0].get("reason", "unselected")}
    selections["unused_trailing"] = {**trailing, "reason": unused[1].get("reason", "unselected")}
    return selections


def _metrics(targets: np.ndarray, predictions: np.ndarray) -> dict[str, Any]:
    """Explicit per-output errors and R²; undefined constants remain visible."""
    y = np.asarray(targets, dtype=float)
    p = np.asarray(predictions, dtype=float)
    if y.shape != p.shape or y.ndim != 2 or y.shape[1] != len(FEATURES):
        raise ValueError("target/prediction dimensions differ")
    outputs = {}
    for col, label in enumerate(FEATURES):
        if not len(y):
            outputs[label] = {
                "r2": None,
                "r2_reason": "no included observations",
                "mae": None,
                "rmse": None,
            }
            continue
        error = y[:, col] - p[:, col]
        sse = float(np.sum(error * error))
        sst = float(np.sum((y[:, col] - y[:, col].mean()) ** 2))
        defined = len(y) >= 2 and sst > 0
        outputs[label] = {
            "r2": float(1 - sse / sst) if defined else None,
            "r2_reason": None if defined else "fewer than two observations or constant target",
            "mae": float(np.mean(np.abs(error))),
            "rmse": float(np.sqrt(np.mean(error * error))),
        }
    if not len(y):
        return {
            "included_samples": 0,
            "per_output": outputs,
            "uniform_average_r2": None,
            "variance_weighted_r2": None,
            "aggregate_reason": "no included observations",
        }
    values = [outputs[label]["r2"] for label in FEATURES]
    defined = all(value is not None for value in values)
    variances = np.var(y, axis=0)
    return {
        "included_samples": len(y),
        "per_output": outputs,
        "uniform_average_r2": float(np.mean(values)) if defined else None,
        "variance_weighted_r2": float(np.average(values, weights=variances)) if defined else None,
        "aggregate_reason": None if defined else "at least one output has undefined R²",
    }


def run_h1_chronological_experiment(spec_path: str | Path) -> dict[str, Any]:
    """Compare three frozen conditions on one source's later, complete trials."""
    try:
        import scipy
        import sklearn
        from sklearn.linear_model import Ridge
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install dmri-studio[bci-reference] for H1 ridge experiments") from exc

    path = Path(spec_path).expanduser().resolve(strict=True)
    spec_fingerprint = SourceFingerprint.capture(path)
    spec_bytes = path.read_bytes()
    spec = json.loads(spec_bytes)
    if not isinstance(spec, dict) or set(spec) != {
        "schema",
        "upstream_commit",
        "source",
        "feature_order",
        "chronology_evidence",
        "selection_rule",
        "selections",
        "configuration",
    }:
        raise ValueError("unsupported or incomplete chronological experiment specification")
    if spec["schema"] != SPEC_SCHEMA or spec["upstream_commit"] != UPSTREAM_LOADER_COMMIT:
        raise ValueError("chronological schema or pinned upstream revision differs")
    if spec["feature_order"] != list(FEATURES):
        raise ValueError("chronological feature order differs from H1")
    if spec["chronology_evidence"] != "single_nwb_clock_and_trialnum":
        raise ValueError(
            "unsupported temporal ordering evidence; cross-file dates and names are insufficient"
        )
    if not isinstance(spec["selection_rule"], str) or not spec["selection_rule"].strip():
        raise ValueError("a predeclared selection rule is required")
    alpha = _validate_configuration(spec["configuration"])
    if not isinstance(spec["source"], dict) or not isinstance(spec["source"].get("path"), str):
        raise ValueError("chronological source requires an explicit path and identity")
    source_path = (path.parent / spec["source"]["path"]).expanduser().resolve(strict=True)
    source_fingerprint = SourceFingerprint.capture(source_path)
    source = _load_role(spec["source"], "training", path.parent)
    if (
        source["inspection"]["time"]["gap_count"]
        or source["inspection"]["time"]["short_interval_count"]
    ):
        raise ValueError("chronological v1 requires one regular represented acquisition clock")
    selections = _validate_selections(source, spec["selections"])
    counts, targets, mask = source["counts"], source["targets"], source["raw_mask"]
    segments = {}
    for role in ("training", "calibration", "evaluation"):
        item = selections[role]
        sl = slice(item["start"], item["stop"])
        segments[role] = {
            "features": reference_filter(counts[sl]),
            "targets": targets[sl],
            "raw_mask": mask[sl],
        }
    train, cal, evaluation = (segments[key] for key in ("training", "calibration", "evaluation"))
    train_nonfinite = ~np.isfinite(train["targets"]).all(axis=1)
    train_still = np.all(np.abs(train["targets"]) < 0.001, axis=1)
    train_selected = train["raw_mask"] & ~train_nonfinite & ~train_still
    cal_selected = cal["raw_mask"]
    eval_nonfinite = ~np.isfinite(evaluation["targets"]).all(axis=1)
    eval_selected = evaluation["raw_mask"] & ~eval_nonfinite
    if np.count_nonzero(train_selected) < 2 or not np.count_nonzero(cal_selected):
        raise ValueError("insufficient selected training or calibration observations")
    train_y = train["targets"][train_selected]
    train_x = train["features"][train_selected]
    train_mean, train_std = train_x.mean(axis=0), train_x.std(axis=0)
    train_std[train_std == 0] = 1
    cal_x = cal["features"][cal_selected]
    cal_mean, cal_std = cal_x.mean(axis=0), cal_x.std(axis=0)
    cal_std[cal_std == 0] = 1
    model = Ridge(alpha=alpha)
    model.fit((train_x - train_mean) / train_std, train_y)
    eval_features = evaluation["features"]
    predicted = {
        "constant_training_mean": np.broadcast_to(
            train_y.mean(axis=0), (len(eval_features), len(FEATURES))
        ).copy(),
        "fixed_ridge": model.predict((eval_features - train_mean) / train_std),
        "normalization_recalibrated_ridge": model.predict((eval_features - cal_mean) / cal_std),
    }
    conditions = {}
    for name, values in predicted.items():
        if not np.isfinite(values).all():
            raise ValueError(f"{name} produced nonfinite predictions")
        per_trial = []
        for trial in source["inspection"]["trials"]["segments"]:
            start, stop = trial["start_index"], trial["stop_index_exclusive"]
            ev = selections["evaluation"]
            if start < ev["start"] or stop > ev["stop"]:
                continue
            local = slice(start - ev["start"], stop - ev["start"])
            selected = eval_selected[local]
            per_trial.append(
                {
                    "trial_number": trial["trial_number"],
                    "original_start": start,
                    "original_stop": stop,
                    "raw_mask_true": int(np.count_nonzero(evaluation["raw_mask"][local])),
                    "additional_nonfinite_exclusions": int(
                        np.count_nonzero(evaluation["raw_mask"][local] & eval_nonfinite[local])
                    ),
                    "metrics": _metrics(
                        evaluation["targets"][local][selected], values[local][selected]
                    ),
                }
            )
        conditions[name] = {
            "pooled": _metrics(evaluation["targets"][eval_selected], values[eval_selected]),
            "per_trial": per_trial,
            "predictions": values.tolist(),
        }
    if not spec_fingerprint.unchanged(path) or not source_fingerprint.unchanged(source["source"]):
        raise ValueError("specification or source changed during chronological experiment")
    return {
        "schema": REPORT_SCHEMA,
        "specification": {"path": str(path), "sha256": hashlib.sha256(spec_bytes).hexdigest()},
        "upstream_commit": UPSTREAM_LOADER_COMMIT,
        "sources": {
            "recording": {
                "path": str(source["source"]),
                "sha256": source["inspection"]["source"]["sha256"],
                "dataset_version": source["declaration"]["dataset_version"],
                "asset_id": source["declaration"]["asset_id"],
                "partition": source["declaration"]["benchmark_partition"],
                "nwb_identity": source["declaration"]["identity_evidence"],
            }
        },
        "chronology_evidence": spec["chronology_evidence"],
        "selection_rule": spec["selection_rule"],
        "selections": selections,
        "configuration": spec["configuration"],
        "software": {
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "scikit_learn": sklearn.__version__,
        },
        "training": {
            "selected_samples": int(np.count_nonzero(train_selected)),
            "raw_mask_false": int(np.count_nonzero(~train["raw_mask"])),
            "nonfinite_target": int(np.count_nonzero(train_nonfinite)),
            "still_time": int(np.count_nonzero(train_still)),
            "excluded_union": int(len(train_selected) - np.count_nonzero(train_selected)),
            "target_mean": train_y.mean(axis=0).tolist(),
            "feature_mean": train_mean.tolist(),
            "feature_std": train_std.tolist(),
            "ridge_alpha": alpha,
            "ridge_coefficients": model.coef_.tolist(),
            "ridge_intercept": model.intercept_.tolist(),
        },
        "calibration": {
            "selected_samples": int(np.count_nonzero(cal_selected)),
            "raw_mask_false": int(np.count_nonzero(~cal["raw_mask"])),
            "target_labels_used": False,
            "feature_mean": cal_mean.tolist(),
            "feature_std": cal_std.tolist(),
        },
        "evaluation": {
            "samples": len(eval_selected),
            "raw_mask_false": int(np.count_nonzero(~evaluation["raw_mask"])),
            "additional_nonfinite_target_exclusions": int(
                np.count_nonzero(evaluation["raw_mask"] & eval_nonfinite)
            ),
            "included_samples": int(np.count_nonzero(eval_selected)),
            "selected_original_indices": (
                np.flatnonzero(eval_selected) + selections["evaluation"]["start"]
            ).tolist(),
            "conditions": conditions,
            "metric_scope": "same selected complete trials; custom held-in calibration-file holdout, not FALCON benchmark",
        },
        "temporal_isolation": {
            "filter_support_bins": KERNEL_LENGTH,
            "history_bins": 0,
            "reset_at_each_role_start": True,
            "carry_across_trial_transitions_within_role": True,
            "calibration_history_carried_into_evaluation": False,
            "unselected_bins_between_calibration_and_evaluation": selections["unused_gap"][
                "samples"
            ],
        },
        "limitations": [
            "One held-in recording's later complete trials, not independent benchmark minival data.",
            "Fixed alpha 1.0 was declared before scoring; no hyperparameter selection was performed.",
            "Individual 20 ms bins and a few trials are not independent subjects or a significance test.",
            "Unit IDs do not establish biological stability across recordings.",
        ],
    }
