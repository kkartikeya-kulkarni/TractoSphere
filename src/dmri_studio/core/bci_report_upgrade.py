"""Explicit, hash-bound v1 to v2 H1 report enrichment without model fitting."""

from __future__ import annotations

import json
from pathlib import Path

from dmri_studio.core.bci import FEATURES, inspect_falcon_h1
from dmri_studio.core.bci_reports import (
    METRIC_DEFINITIONS,
    REPORT_SCHEMA_V2,
    _validate_v2,
    read_h1_chronological_report,
)
from dmri_studio.core.bci_ridge import write_h1_ridge_report
from dmri_studio.core.dataset import SourceFingerprint


def upgrade_h1_chronological_report(
    report_path: str | Path, source_path: str | Path, output_path: str | Path
) -> Path:
    """Add exact observed evaluation evidence from an explicitly named source.

    The v1 report's embedded paths are never followed. The source is inspected
    read-only and must match the v1 recording SHA-256 before any output exists.
    """
    import h5py
    import numpy as np

    report_path = Path(report_path).expanduser().resolve(strict=True)
    source_path = Path(source_path).expanduser().resolve(strict=True)
    output_path = Path(output_path).expanduser().resolve(strict=False)
    if output_path in {report_path, source_path}:
        raise ValueError("v2 output must not replace the v1 report or recording")
    report_before = SourceFingerprint.capture(report_path)
    source_before = SourceFingerprint.capture(source_path)
    view = read_h1_chronological_report(report_path)
    if view["schema"] != "falcon-h1-ridge-chronological-report-v1":
        raise ValueError("only an original v1 chronological report can be enriched")
    inspection = inspect_falcon_h1(source_path)
    if inspection["source"]["sha256"] != view["source"]["sha256"]:
        raise ValueError("explicit source SHA-256 differs from the frozen v1 report")
    if inspection["features"]["labels"] != list(FEATURES):
        raise ValueError("explicit source output labels differ from the report")
    ev = view["selections"]["evaluation"]
    with h5py.File(source_path, "r") as root:
        targets = np.asarray(
            root["acquisition/OpenLoopKinematicsVelocity/data"][ev["start"] : ev["stop"]],
            dtype=float,
        )
        raw = np.asarray(root["acquisition/eval_mask/data"][ev["start"] : ev["stop"]], dtype=bool)
        trials = np.asarray(
            root["acquisition/TrialNum/data"][ev["start"] : ev["stop"]], dtype=np.int64
        )
    if (
        targets.shape != (ev["samples"], len(FEATURES))
        or raw.shape != (ev["samples"],)
        or trials.shape != (ev["samples"],)
    ):
        raise ValueError("explicit source evaluation dimensions differ from the report")
    finite = np.isfinite(targets).all(axis=1)
    selected_local = np.flatnonzero(raw & finite)
    indices = (selected_local + ev["start"]).tolist()
    if indices != view["evaluation"]["selected_original_indices"]:
        raise ValueError("explicit source selection differs from the frozen v1 report")
    if not report_before.unchanged(report_path) or not source_before.unchanged(source_path):
        raise ValueError("v1 report or explicit source changed while reading")
    unit = inspection["features"]["storage_units"]["OpenLoopKinematicsVelocity"]
    established = bool(unit and unit != "unknown")
    observations = {
        "storage": "included_only_in_original_index_order",
        "prediction_storage": "all_evaluation_candidates_in_source_index_order",
        "original_indices": indices,
        "trial_numbers": trials[selected_local].tolist(),
        "targets": targets[selected_local].tolist(),
        "candidate_raw_eval_mask": raw.tolist(),
        "candidate_target_finite": finite.tolist(),
        "output_labels": list(FEATURES),
        "output_units": [
            {
                "label": feature,
                "unit": unit if established else None,
                "provenance": "source_nwb_velocity_storage_unit" if established else "unknown",
            }
            for feature in FEATURES
        ],
        "metric_definitions": dict(METRIC_DEFINITIONS),
    }
    # Validate every recorded pooled and per-trial metric against the source's
    # selected targets and the untouched v1 prediction arrays before writing.
    _validate_v2(view, observations)
    original = json.loads(report_path.read_bytes())
    if not report_before.unchanged(report_path):
        raise ValueError("v1 report changed while preparing the output")
    original["schema"] = REPORT_SCHEMA_V2
    original["original_v1_report_sha256"] = view["report_sha256"]
    original["enrichment"] = {
        "method": "read_only_exact_selected_nwb_targets",
        "explicit_source_path": str(source_path),
        "source_sha256": inspection["source"]["sha256"],
    }
    original["observations"] = observations
    return write_h1_ridge_report(original, output_path)
