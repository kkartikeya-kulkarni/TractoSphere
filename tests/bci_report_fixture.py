"""Small path-bearing synthetic H1 report for passive-reader and GUI tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from dmri_studio.core.bci import FEATURES, UPSTREAM_LOADER_COMMIT
from dmri_studio.core.bci_chronological import REPORT_SCHEMA
from dmri_studio.core.bci_reports import (
    METRIC_DEFINITIONS,
    REPORT_SCHEMA_V2,
    compute_embedded_metrics,
)


def synthetic_chronological_report(path: Path) -> dict:
    def selection(start, stop, trials, true_count):
        return {
            "start": start,
            "stop": stop,
            "samples": stop - start,
            "trial_numbers": trials,
            "trial_count": len(trials),
            "exposure_s": round((stop - start) * 0.02, 9),
            "raw_eval_mask_true": true_count,
            "raw_eval_mask_false": stop - start - true_count,
            "first_window_start_s": round((start - 1) * 0.02, 9),
            "last_window_end_s": round((stop - 1) * 0.02, 9),
        }

    def metric(count):
        return {
            "included_samples": count,
            "per_output": {
                key: {"r2": -1.0, "r2_reason": None, "mae": 1.0, "rmse": 1.0} for key in FEATURES
            },
            "uniform_average_r2": -1.0,
            "variance_weighted_r2": -1.0,
            "aggregate_reason": None,
        }

    def condition(multiplier):
        return {
            "pooled": metric(4),
            "per_trial": [
                {
                    "trial_number": 5,
                    "original_start": 8,
                    "original_stop": 11,
                    "raw_mask_true": 2,
                    "additional_nonfinite_exclusions": 0,
                    "metrics": metric(2),
                },
                {
                    "trial_number": 6,
                    "original_start": 11,
                    "original_stop": 14,
                    "raw_mask_true": 2,
                    "additional_nonfinite_exclusions": 0,
                    "metrics": metric(2),
                },
            ],
            "predictions": [[float(multiplier * index)] * 7 for index in range(6)],
        }

    report = {
        "schema": REPORT_SCHEMA,
        "specification": {"path": "/private/absent/spec.json", "sha256": "b" * 64},
        "sources": {
            "recording": {
                "path": "/private/absent/source.nwb",
                "sha256": "a" * 64,
                "dataset_version": "synthetic",
                "asset_id": "synthetic-asset",
                "partition": "held_in_calib",
                "nwb_identity": "synthetic NWB evidence",
            }
        },
        "upstream_commit": UPSTREAM_LOADER_COMMIT,
        "chronology_evidence": "single_nwb_clock_and_trialnum",
        "selection_rule": "synthetic earlier training/calibration and later evaluation",
        "selections": {
            "training": selection(0, 4, [1, 2], 4),
            "calibration": selection(4, 6, [3], 2),
            "unused_gap": {**selection(6, 8, [4], 2), "reason": "declared separation"},
            "evaluation": selection(8, 14, [5, 6], 4),
            "unused_trailing": {**selection(14, 16, [7], 2), "reason": "last trial not selected"},
        },
        "configuration": {
            "bin_period_s": 0.02,
            "tau_ms": 240.0,
            "kernel_extent": 1,
            "history_bins": 0,
            "still_threshold": 0.001,
            "ridge_alpha": 1.0,
            "training_selection": "raw_mask_true_finite_nonstill",
            "calibration_selection": "raw_mask_true_all_targets_ignored",
            "evaluation_selection": "raw_mask_true_finite_targets",
            "filter_reset": "each_role_start; causal state carries across TrialNum within role",
            "model_selection": "none; fixed predeclared alpha",
        },
        "training": {
            "selected_samples": 3,
            "raw_mask_false": 0,
            "nonfinite_target": 0,
            "still_time": 1,
            "excluded_union": 1,
            "target_mean": [0.0] * 7,
            "feature_mean": [0.0, 0.0],
            "feature_std": [1.0, 1.0],
            "ridge_alpha": 1.0,
            "ridge_coefficients": [[0.0, 0.0] for _ in FEATURES],
            "ridge_intercept": [0.0] * 7,
        },
        "calibration": {
            "selected_samples": 2,
            "raw_mask_false": 0,
            "target_labels_used": False,
            "feature_mean": [0.0, 0.0],
            "feature_std": [1.0, 1.0],
        },
        "evaluation": {
            "samples": 6,
            "raw_mask_false": 2,
            "additional_nonfinite_target_exclusions": 0,
            "included_samples": 4,
            "selected_original_indices": [8, 10, 11, 13],
            "metric_scope": "synthetic custom report",
            "conditions": {
                "constant_training_mean": condition(0),
                "fixed_ridge": condition(1),
                "normalization_recalibrated_ridge": condition(2),
            },
        },
        "temporal_isolation": {
            "filter_support_bins": 12,
            "history_bins": 0,
            "reset_at_each_role_start": True,
            "carry_across_trial_transitions_within_role": True,
            "calibration_history_carried_into_evaluation": False,
            "unselected_bins_between_calibration_and_evaluation": 2,
        },
        "software": {"numpy": "synthetic", "scipy": "synthetic", "scikit_learn": "synthetic"},
        "limitations": ["Synthetic fixture; no scientific acceptance."],
    }
    path.write_text(json.dumps(report, sort_keys=True))
    return report


def synthetic_chronological_v2_report(path: Path) -> dict:
    """Self-contained, numerically consistent six-candidate/two-trial fixture."""
    report = synthetic_chronological_report(path)
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    indices = report["evaluation"]["selected_original_indices"]
    targets = [
        [float((position + 1) * (feature + 1)) for feature in range(len(FEATURES))]
        for position in range(len(indices))
    ]
    for condition in report["evaluation"]["conditions"].values():
        predictions = [condition["predictions"][index - 8] for index in indices]
        condition["pooled"] = compute_embedded_metrics(targets, predictions)
        for trial in condition["per_trial"]:
            positions = [
                position
                for position, index in enumerate(indices)
                if trial["original_start"] <= index < trial["original_stop"]
            ]
            trial["metrics"] = compute_embedded_metrics(
                [targets[position] for position in positions],
                [predictions[position] for position in positions],
            )
    report["schema"] = REPORT_SCHEMA_V2
    report["original_v1_report_sha256"] = original_hash
    report["enrichment"] = {
        "method": "read_only_exact_selected_nwb_targets",
        "explicit_source_path": "/private/absent/explicit-source.nwb",
        "source_sha256": "a" * 64,
    }
    report["observations"] = {
        "storage": "included_only_in_original_index_order",
        "prediction_storage": "all_evaluation_candidates_in_source_index_order",
        "original_indices": indices,
        "trial_numbers": [5, 5, 6, 6],
        "targets": targets,
        "candidate_raw_eval_mask": [True, False, True, True, False, True],
        "candidate_target_finite": [True] * 6,
        "output_labels": list(FEATURES),
        "output_units": [
            {
                "label": feature,
                "unit": "arbitrary",
                "provenance": "source_nwb_velocity_storage_unit",
            }
            for feature in FEATURES
        ],
        "metric_definitions": dict(METRIC_DEFINITIONS),
    }
    path.write_text(json.dumps(report, sort_keys=True))
    return report
