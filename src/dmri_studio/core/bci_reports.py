"""Passive validation and bounded display preparation for saved H1 reports.

Only the explicitly selected JSON file is read. Embedded paths are never opened,
executed, returned for display, or used as scientific verification evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from dmri_studio.core.bci import FEATURES, PERIOD_S, UPSTREAM_LOADER_COMMIT
from dmri_studio.core.bci_chronological import REPORT_SCHEMA, _validate_configuration
from dmri_studio.core.dataset import SourceFingerprint

CONDITIONS = (
    "constant_training_mean",
    "fixed_ridge",
    "normalization_recalibrated_ridge",
)
MAX_REPORT_BYTES = 32 * 1024 * 1024
MAX_PLOT_SEGMENTS = 128


def _fail(message: str) -> None:
    raise ValueError(f"Chronological report: {_display_text(message)}")


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _fail(f"{name} must be finite")
    return float(value)


def _count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(f"{name} must be a nonnegative integer")
    return value


def _sha(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        _fail(f"{name} must be a SHA-256 hex digest")
    return value


def _mapping(value: Any, name: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _fail(f"{name} fields differ from the supported v1 schema")
    return value


def _display_text(value: str) -> str:
    """Keep incidental absolute local paths out of passive summaries."""
    return re.sub(r"(?<![\w])(?:/[^\s]+|[A-Za-z]:\\[^\s]+)", "[local path]", value)


def _sanitize_tree(value: Any) -> Any:
    if isinstance(value, str):
        return _display_text(value)
    if isinstance(value, list):
        return [_sanitize_tree(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_tree(item) for key, item in value.items()}
    return value


def _selection(value: Any, name: str) -> dict[str, Any]:
    item = _mapping(
        value,
        name,
        {
            "start",
            "stop",
            "samples",
            "trial_numbers",
            "trial_count",
            "exposure_s",
            "raw_eval_mask_true",
            "raw_eval_mask_false",
            "first_window_start_s",
            "last_window_end_s",
        }
        | ({"reason"} if name.startswith("unused_") else set()),
    )
    if "reason" in item and (not isinstance(item["reason"], str) or not item["reason"].strip()):
        _fail(f"{name} needs a nonempty reason")
    start, stop = _count(item["start"], f"{name}.start"), _count(item["stop"], f"{name}.stop")
    n = _count(item["samples"], f"{name}.samples")
    if stop <= start or stop - start != n:
        _fail(f"{name} source-index span and samples differ")
    ids = item["trial_numbers"]
    if (
        not isinstance(ids, list)
        or not ids
        or any(not isinstance(x, int) or isinstance(x, bool) for x in ids)
    ):
        _fail(f"{name} TrialNum IDs are invalid")
    if _count(item["trial_count"], f"{name}.trial_count") != len(ids):
        _fail(f"{name} trial count differs from IDs")
    true_count = _count(item["raw_eval_mask_true"], f"{name}.raw_mask_true")
    false_count = _count(item["raw_eval_mask_false"], f"{name}.raw_mask_false")
    if true_count + false_count != n:
        _fail(f"{name} raw mask counts differ from samples")
    if not math.isclose(
        _finite(item["exposure_s"], f"{name}.exposure_s"), n * PERIOD_S, abs_tol=1e-7
    ):
        _fail(f"{name} exposure differs from represented bins")
    first = _finite(item["first_window_start_s"], f"{name}.first_window_start_s")
    last = _finite(item["last_window_end_s"], f"{name}.last_window_end_s")
    if last <= first or not math.isclose(last - first, n * PERIOD_S, abs_tol=1e-7):
        _fail(f"{name} timing does not match contiguous 20 ms windows")
    return item


def _metric(value: Any, name: str) -> dict[str, Any]:
    row = _mapping(
        value,
        name,
        {
            "included_samples",
            "per_output",
            "uniform_average_r2",
            "variance_weighted_r2",
            "aggregate_reason",
        },
    )
    n = _count(row["included_samples"], f"{name}.included_samples")
    outputs = row["per_output"]
    # The report writer sorts JSON object keys. Dict insertion order is not
    # scientific feature order; prediction columns follow the v1 schema.
    if not isinstance(outputs, dict) or set(outputs) != set(FEATURES):
        _fail(f"{name} feature labels differ from {list(FEATURES)}")
    r2_values = []
    for feature in FEATURES:
        item = _mapping(outputs[feature], f"{name}.{feature}", {"r2", "r2_reason", "mae", "rmse"})
        r2 = item["r2"]
        reason = item["r2_reason"]
        if r2 is None:
            if not isinstance(reason, str) or not reason.strip():
                _fail(f"{name}.{feature} undefined R² needs a reason")
        else:
            r2_values.append(_finite(r2, f"{name}.{feature}.r2"))
            if reason is not None:
                _fail(f"{name}.{feature} defined R² cannot have an undefined reason")
        if n == 0:
            if item["mae"] is not None or item["rmse"] is not None:
                _fail(f"{name}.{feature} errors require included observations")
        else:
            mae = _finite(item["mae"], f"{name}.{feature}.mae")
            rmse = _finite(item["rmse"], f"{name}.{feature}.rmse")
            if mae < 0 or rmse < 0 or rmse + 1e-10 < mae:
                _fail(f"{name}.{feature} error magnitudes are inconsistent")
    uniform, weighted = row["uniform_average_r2"], row["variance_weighted_r2"]
    if len(r2_values) == len(FEATURES):
        mean = sum(r2_values) / len(r2_values)
        if not math.isclose(
            _finite(uniform, f"{name}.uniform_average_r2"), mean, rel_tol=1e-8, abs_tol=1e-8
        ):
            _fail(f"{name} uniform-average R² disagrees with per-output R²")
        weight = _finite(weighted, f"{name}.variance_weighted_r2")
        if weight < min(r2_values) - 1e-8 or weight > max(r2_values) + 1e-8:
            _fail(f"{name} variance-weighted R² is outside per-output range")
        if row["aggregate_reason"] is not None:
            _fail(f"{name} defined aggregates cannot have an undefined reason")
    elif uniform is not None or weighted is not None or not row["aggregate_reason"]:
        _fail(f"{name} undefined per-output R² requires undefined aggregates and a reason")
    return row


def read_h1_chronological_report(path: str | Path) -> dict[str, Any]:
    """Read one explicit report, validate available claims, return a path-free view."""
    source = Path(path).expanduser().resolve(strict=True)
    before = SourceFingerprint.capture(source)
    if before.size > MAX_REPORT_BYTES:
        _fail(f"file exceeds the {MAX_REPORT_BYTES // (1024 * 1024)} MiB read limit")
    data = source.read_bytes()
    if len(data) != before.size:
        _fail("file changed while reading")
    try:
        report = json.loads(
            data,
            object_pairs_hook=_object_pairs,
            parse_constant=lambda token: _fail(f"nonfinite JSON constant {token}"),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail(
            f"invalid JSON: {exc.msg if isinstance(exc, json.JSONDecodeError) else 'text encoding'}"
        )
    if not before.unchanged(source):
        _fail("file changed during inspection")
    if not isinstance(report, dict):
        _fail("top level must be a JSON object")
    if report.get("schema") != REPORT_SCHEMA:
        _fail(f"unsupported schema version {report.get('schema')!r}; expected {REPORT_SCHEMA}")
    _mapping(
        report,
        "report",
        {
            "schema",
            "specification",
            "sources",
            "upstream_commit",
            "chronology_evidence",
            "selection_rule",
            "selections",
            "configuration",
            "training",
            "calibration",
            "evaluation",
            "temporal_isolation",
            "software",
            "limitations",
        },
    )
    if report["upstream_commit"] != UPSTREAM_LOADER_COMMIT:
        _fail("pinned upstream revision differs")
    specification = _mapping(report["specification"], "specification", {"path", "sha256"})
    _sha(specification["sha256"], "specification.sha256")
    sources = _mapping(report["sources"], "sources", {"recording"})
    recording = _mapping(
        sources["recording"],
        "recording",
        {
            "path",
            "sha256",
            "dataset_version",
            "asset_id",
            "partition",
            "nwb_identity",
        },
    )
    _sha(recording["sha256"], "recording.sha256")
    if not isinstance(specification["path"], str) or not isinstance(recording["path"], str):
        _fail("embedded source path declarations must be strings")
    for name in ("dataset_version", "asset_id", "partition", "nwb_identity"):
        if not isinstance(recording[name], str) or not recording[name].strip():
            _fail(f"recording.{name} is missing")
    if report["chronology_evidence"] != "single_nwb_clock_and_trialnum":
        _fail("unsupported chronology evidence")
    if not isinstance(report["selection_rule"], str) or not report["selection_rule"].strip():
        _fail("selection rule is missing")
    try:
        _validate_configuration(report["configuration"])
    except ValueError as exc:
        _fail(str(exc))
    selections = _mapping(
        report["selections"],
        "selections",
        {
            "training",
            "calibration",
            "evaluation",
            "unused_gap",
            "unused_trailing",
        },
    )
    for name in ("training", "calibration", "evaluation", "unused_gap", "unused_trailing"):
        _selection(selections[name], name)
    train, cal, ev, gap, tail = (
        selections[name]
        for name in ("training", "calibration", "evaluation", "unused_gap", "unused_trailing")
    )
    if not (
        train["start"] == 0
        and train["stop"] == cal["start"]
        and cal["stop"] == gap["start"]
        and gap["stop"] == ev["start"]
        and ev["stop"] == tail["start"]
        and cal["last_window_end_s"] < ev["first_window_start_s"]
    ):
        _fail("reported source selections do not establish chronological separation")
    training = _mapping(
        report["training"],
        "training",
        {
            "selected_samples",
            "raw_mask_false",
            "nonfinite_target",
            "still_time",
            "excluded_union",
            "target_mean",
            "feature_mean",
            "feature_std",
            "ridge_alpha",
            "ridge_coefficients",
            "ridge_intercept",
        },
    )
    for name in (
        "selected_samples",
        "raw_mask_false",
        "nonfinite_target",
        "still_time",
        "excluded_union",
    ):
        _count(training[name], f"training.{name}")
    if training["selected_samples"] + training["excluded_union"] != train["samples"]:
        _fail("training selection/exclusion denominator differs from source span")
    if training["raw_mask_false"] != train["raw_eval_mask_false"]:
        _fail("training raw mask count differs from selection")
    if not isinstance(training["target_mean"], list) or len(training["target_mean"]) != len(
        FEATURES
    ):
        _fail("training target mean must have seven ordered outputs")
    for value in training["target_mean"]:
        _finite(value, "training.target_mean")
    units = len(training["feature_mean"]) if isinstance(training["feature_mean"], list) else 0
    if not 1 <= units <= 5000:
        _fail("training feature dimension is unsupported")
    for name in ("feature_mean", "feature_std"):
        if not isinstance(training[name], list) or len(training[name]) != units:
            _fail(f"training.{name} has inconsistent unit dimension")
        for value in training[name]:
            _finite(value, f"training.{name}")
    if any(value <= 0 for value in training["feature_std"]):
        _fail("training feature standard deviations must be positive")
    coefficients = training["ridge_coefficients"]
    if not isinstance(coefficients, list) or len(coefficients) != len(FEATURES):
        _fail("ridge coefficient output dimension differs")
    for row in coefficients:
        if not isinstance(row, list) or len(row) != units:
            _fail("ridge coefficient unit dimension differs")
        for value in row:
            _finite(value, "ridge coefficient")
    if not isinstance(training["ridge_intercept"], list) or len(training["ridge_intercept"]) != len(
        FEATURES
    ):
        _fail("ridge intercept output dimension differs")
    for value in training["ridge_intercept"]:
        _finite(value, "ridge intercept")
    if _finite(training["ridge_alpha"], "training.ridge_alpha") != float(
        report["configuration"]["ridge_alpha"]
    ):
        _fail("fitted alpha differs from configuration")
    calibration = _mapping(
        report["calibration"],
        "calibration",
        {
            "selected_samples",
            "raw_mask_false",
            "target_labels_used",
            "feature_mean",
            "feature_std",
        },
    )
    if calibration["target_labels_used"] is not False:
        _fail("normalization recalibration must not use calibration target labels")
    if (
        _count(calibration["selected_samples"], "calibration.selected_samples")
        + _count(calibration["raw_mask_false"], "calibration.raw_mask_false")
        != cal["samples"]
    ):
        _fail("calibration counts differ from source span")
    for name in ("feature_mean", "feature_std"):
        values = calibration[name]
        if not isinstance(values, list) or len(values) != units:
            _fail(f"calibration.{name} has inconsistent unit dimension")
        for value in values:
            _finite(value, f"calibration.{name}")
    if any(value <= 0 for value in calibration["feature_std"]):
        _fail("calibration feature standard deviations must be positive")
    temporal = _mapping(
        report["temporal_isolation"],
        "temporal_isolation",
        {
            "filter_support_bins",
            "history_bins",
            "reset_at_each_role_start",
            "carry_across_trial_transitions_within_role",
            "calibration_history_carried_into_evaluation",
            "unselected_bins_between_calibration_and_evaluation",
        },
    )
    if (
        temporal["filter_support_bins"] != 12
        or temporal["history_bins"] != 0
        or temporal["reset_at_each_role_start"] is not True
        or temporal["carry_across_trial_transitions_within_role"] is not True
        or temporal["calibration_history_carried_into_evaluation"] is not False
        or temporal["unselected_bins_between_calibration_and_evaluation"] != gap["samples"]
    ):
        _fail("temporal isolation declarations differ from supported v1 behavior")
    evaluation = _mapping(
        report["evaluation"],
        "evaluation",
        {
            "samples",
            "raw_mask_false",
            "additional_nonfinite_target_exclusions",
            "included_samples",
            "selected_original_indices",
            "conditions",
            "metric_scope",
        },
    )
    n = _count(evaluation["samples"], "evaluation.samples")
    missing = _count(evaluation["raw_mask_false"], "evaluation.raw_mask_false")
    nonfinite = _count(
        evaluation["additional_nonfinite_target_exclusions"], "evaluation.additional_nonfinite"
    )
    included = _count(evaluation["included_samples"], "evaluation.included_samples")
    if (
        n != ev["samples"]
        or missing != ev["raw_eval_mask_false"]
        or included + missing + nonfinite != n
    ):
        _fail("evaluation sample, raw-mask and additional-exclusion counts disagree")
    indices = evaluation["selected_original_indices"]
    if (
        not isinstance(indices, list)
        or len(indices) != included
        or any(
            not isinstance(x, int) or isinstance(x, bool) or not ev["start"] <= x < ev["stop"]
            for x in indices
        )
        or any(b <= a for a, b in zip(indices, indices[1:], strict=False))
    ):
        _fail("evaluation original observation indices are invalid")
    conditions = evaluation["conditions"]
    if not isinstance(conditions, dict) or set(conditions) != set(CONDITIONS):
        _fail("condition identities differ from chronological v1")
    per_trial_reference = None
    for condition in CONDITIONS:
        item = _mapping(conditions[condition], condition, {"pooled", "per_trial", "predictions"})
        prediction_rows = item["predictions"]
        if not isinstance(prediction_rows, list) or len(prediction_rows) != n:
            _fail(f"{condition} prediction sample dimension differs")
        for row in prediction_rows:
            if not isinstance(row, list) or len(row) != len(FEATURES):
                _fail(f"{condition} prediction feature dimension differs")
            for value in row:
                _finite(value, f"{condition} prediction")
        if condition == "constant_training_mean" and any(
            any(
                not math.isclose(value, expected, rel_tol=1e-10, abs_tol=1e-10)
                for value, expected in zip(row, training["target_mean"], strict=True)
            )
            for row in prediction_rows
        ):
            _fail("constant predictions differ from the reported training-target mean")
        pooled = _metric(item["pooled"], f"{condition}.pooled")
        if pooled["included_samples"] != included:
            _fail(f"{condition} pooled denominator differs from shared evaluation selection")
        per_trial = item["per_trial"]
        if not isinstance(per_trial, list) or len(per_trial) != ev["trial_count"]:
            _fail(f"{condition} per-trial results differ from evaluation trials")
        metadata = []
        for trial in per_trial:
            row = _mapping(
                trial,
                f"{condition}.trial",
                {
                    "trial_number",
                    "original_start",
                    "original_stop",
                    "raw_mask_true",
                    "additional_nonfinite_exclusions",
                    "metrics",
                },
            )
            start, stop = (
                _count(row["original_start"], "trial.start"),
                _count(row["original_stop"], "trial.stop"),
            )
            raw_true = _count(row["raw_mask_true"], "trial.raw_mask_true")
            excluded = _count(row["additional_nonfinite_exclusions"], "trial.nonfinite")
            metrics = _metric(row["metrics"], f"{condition}.trial.metrics")
            if (
                not ev["start"] <= start < stop <= ev["stop"]
                or raw_true > stop - start
                or excluded > raw_true
                or metrics["included_samples"] != raw_true - excluded
            ):
                _fail(f"{condition} trial bounds or denominator differ")
            metadata.append(
                (row["trial_number"], start, stop, raw_true, excluded, metrics["included_samples"])
            )
        if (
            [row[0] for row in metadata] != ev["trial_numbers"]
            or metadata[0][1] != ev["start"]
            or metadata[-1][2] != ev["stop"]
            or any(a[2] != b[1] for a, b in zip(metadata, metadata[1:], strict=False))
        ):
            _fail(f"{condition} trial boundaries differ from selected evaluation span")
        if (
            sum(row[5] for row in metadata) != included
            or sum(row[3] for row in metadata) != ev["raw_eval_mask_true"]
        ):
            _fail(f"{condition} trial counts differ from pooled evaluation")
        if per_trial_reference is None:
            per_trial_reference = metadata
        elif metadata != per_trial_reference:
            _fail("conditions disagree on trial alignment or evaluation observations")
    limitations = report["limitations"]
    if (
        not isinstance(limitations, list)
        or not limitations
        or any(not isinstance(x, str) or not x.strip() for x in limitations)
    ):
        _fail("limitations are missing")
    software = _mapping(report["software"], "software", {"numpy", "scipy", "scikit_learn"})
    if any(
        not isinstance(software[key], str) or not software[key]
        for key in ("numpy", "scipy", "scikit_learn")
    ):
        _fail("software version provenance is missing")
    safe_selections = {
        name: {
            key: _display_text(value) if key == "reason" else value for key, value in item.items()
        }
        for name, item in selections.items()
    }
    view = {
        "schema": REPORT_SCHEMA,
        "report_sha256": hashlib.sha256(data).hexdigest(),
        "structure": "valid_supported_schema",
        "reported_consistency": "checked_available_fields",
        "independent_source_verification": "not_performed",
        "scientific_acceptance": "unassigned",
        "metric_recomputation": "unavailable_observed_targets_not_embedded",
        "observed_trace": "unavailable_observed_targets_not_embedded",
        "feature_labels": list(FEATURES),
        "feature_order_evidence": "defined_by_v1_schema_not_independently_recoverable_from_predictions",
        "source": {
            key: _display_text(recording[key]) if key != "sha256" else recording[key]
            for key in ("sha256", "dataset_version", "asset_id", "partition", "nwb_identity")
        },
        "specification_sha256": specification["sha256"],
        "upstream_commit": report["upstream_commit"],
        "software": {key: software[key] for key in ("numpy", "scipy", "scikit_learn")},
        "selection_rule": _display_text(report["selection_rule"]),
        "selections": safe_selections,
        "configuration": report["configuration"],
        "training": training,
        "calibration": calibration,
        "evaluation": evaluation,
        "temporal_isolation": temporal,
        "limitations": [_display_text(value) for value in limitations],
        "error_units": "unavailable: output units are not embedded in the v1 report",
    }
    return _sanitize_tree(view)


def prepare_h1_prediction_traces(
    view: dict[str, Any], feature: str, *, max_points: int = 3200
) -> dict[str, Any]:
    """Preserve trial/gap edges, bucket extrema and endpoints for display only."""
    if feature not in FEATURES or max_points < 64:
        raise ValueError("unsupported feature or plot point limit")
    evaluation = view["evaluation"]
    indices = evaluation["selected_original_indices"]
    if not indices:
        return {
            "available": False,
            "reason": "No included evaluation observations are recorded for a prediction trace.",
        }
    starts = {
        item["original_start"] for item in evaluation["conditions"][CONDITIONS[0]]["per_trial"]
    }
    runs: list[list[int]] = []
    for original in indices:
        if not runs or original != runs[-1][-1] + 1 or original in starts:
            runs.append([])
        runs[-1].append(original)
    if len(runs) > MAX_PLOT_SEGMENTS:
        return {
            "available": False,
            "reason": "Too many separated intervals to plot without hiding gaps or trial boundaries.",
        }
    column = FEATURES.index(feature)
    step = max(1, math.ceil(4 * len(indices) / max_points))
    series = {}
    offset = view["selections"]["evaluation"]["start"]
    for condition in CONDITIONS:
        predictions = evaluation["conditions"][condition]["predictions"]
        segments = []
        for run in runs:
            points = [(index, predictions[index - offset][column]) for index in run]
            chosen = {0, len(points) - 1}
            if step > 1:
                for begin in range(0, len(points), step):
                    bucket = points[begin : begin + step]
                    values = [item[1] for item in bucket]
                    chosen.update(
                        {
                            begin,
                            begin + len(bucket) - 1,
                            begin + values.index(min(values)),
                            begin + values.index(max(values)),
                        }
                    )
            else:
                chosen.update(range(len(points)))
            segments.append([points[index] for index in sorted(chosen)])
        series[condition] = segments
    return {
        "available": True,
        "x_label": "Original source bin index (nominal 20 ms)",
        "observed": "unavailable: observed targets are not embedded in the v1 report",
        "trial_boundaries": sorted(starts),
        "downsampled": step > 1,
        "series": series,
    }


def summarize_h1_chronological_report(view: dict[str, Any]) -> dict[str, Any]:
    """Return a small path-free summary without predictions or model weights."""
    return {
        "schema": view["schema"],
        "report_sha256": view["report_sha256"],
        "structure": view["structure"],
        "reported_consistency": view["reported_consistency"],
        "independent_source_verification": view["independent_source_verification"],
        "scientific_acceptance": view["scientific_acceptance"],
        "metric_recomputation": view["metric_recomputation"],
        "source": view["source"],
        "specification_sha256": view["specification_sha256"],
        "selections": view["selections"],
        "evaluation": {
            "samples": view["evaluation"]["samples"],
            "included_samples": view["evaluation"]["included_samples"],
            "conditions": {
                name: {
                    "pooled": view["evaluation"]["conditions"][name]["pooled"],
                    "per_trial": view["evaluation"]["conditions"][name]["per_trial"],
                }
                for name in CONDITIONS
            },
        },
        "limitations": view["limitations"],
    }
