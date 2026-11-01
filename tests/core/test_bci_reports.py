"""Passive report validation, display alignment, and CLI parity."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from bci_report_fixture import synthetic_chronological_report

from dmri_studio.core.bci_reports import (
    CONDITIONS,
    prepare_h1_prediction_traces,
    read_h1_chronological_report,
    summarize_h1_chronological_report,
)


def _write(path, report):
    path.write_text(json.dumps(report, sort_keys=True))


def test_report_is_read_only_path_free_and_separates_evidence_states(tmp_path):
    path = tmp_path / "report.json"
    synthetic_chronological_report(path)
    before = path.read_bytes()
    view = read_h1_chronological_report(path)
    assert path.read_bytes() == before
    assert view["structure"] == "valid_supported_schema"
    assert view["reported_consistency"] == "checked_available_fields"
    assert view["independent_source_verification"] == "not_performed"
    assert view["scientific_acceptance"] == "unassigned"
    assert view["metric_recomputation"] == "unavailable_observed_targets_not_embedded"
    assert "/private/absent/" not in json.dumps(view)
    assert view["evaluation"]["conditions"]["fixed_ridge"]["pooled"]["uniform_average_r2"] == -1


def test_embedded_paths_in_freeform_fields_are_redacted(tmp_path):
    path = tmp_path / "report.json"
    report = synthetic_chronological_report(path)
    report["limitations"].append("Review /private/participant name/analysis/report.json before use")
    report["selections"]["unused_gap"]["reason"] = "Separate /example/private/file.nwb"
    report["evaluation"]["metric_scope"] = "see /Volumes/research/hidden.json"
    _write(path, report)
    view = read_h1_chronological_report(path)
    assert "/private/participant" not in json.dumps(view)
    assert "participant name/analysis" not in json.dumps(view)
    assert "/example/private" not in json.dumps(view)
    assert "/Volumes/research" not in json.dumps(view)
    assert "[text omitted: local path]" in json.dumps(view)


def test_external_citation_remains_visible_but_file_uri_is_omitted(tmp_path):
    path = tmp_path / "report.json"
    report = synthetic_chronological_report(path)
    report["limitations"] += [
        "Method: https://example.org/research/paper",
        "Private source: file:///example/Private Participant/recording.nwb",
    ]
    _write(path, report)
    view = read_h1_chronological_report(path)
    assert "Method: https://example.org/research/paper" in view["limitations"]
    assert "Participant/recording.nwb" not in json.dumps(view)


@pytest.mark.parametrize(
    "change,message",
    [
        (lambda r: r.update(schema="future-version"), "unsupported schema version"),
        (lambda r: r["evaluation"]["conditions"].pop("fixed_ridge"), "condition identities"),
        (
            lambda r: r["evaluation"]["conditions"]["fixed_ridge"]["predictions"][0].pop(),
            "prediction feature dimension",
        ),
        (
            lambda r: r["evaluation"]["conditions"]["fixed_ridge"]["pooled"]["per_output"].pop(
                "tx"
            ),
            "feature labels",
        ),
        (
            lambda r: r["evaluation"]["selected_original_indices"].append(12),
            "original observation indices",
        ),
        (
            lambda r: r["calibration"].update(target_labels_used=True),
            "must not use calibration target labels",
        ),
    ],
)
def test_rejects_inconsistent_report_claims(tmp_path, change, message):
    path = tmp_path / "report.json"
    report = synthetic_chronological_report(path)
    change(report)
    _write(path, report)
    with pytest.raises(ValueError, match=message):
        read_h1_chronological_report(path)


def test_rejects_malformed_and_duplicate_json(tmp_path):
    path = tmp_path / "bad.json"
    for content, message in (
        ("{", "invalid JSON"),
        ('{"schema": 1, "schema": 2}', "duplicate JSON key"),
        ('{"schema": NaN}', "nonfinite JSON"),
    ):
        path.write_text(content)
        with pytest.raises(ValueError, match=message):
            read_h1_chronological_report(path)


def test_undefined_metrics_are_labeled_and_negative_values_preserved(tmp_path):
    path = tmp_path / "report.json"
    report = synthetic_chronological_report(path)
    for condition in CONDITIONS:
        rows = [report["evaluation"]["conditions"][condition]["pooled"]]
        rows.extend(
            row["metrics"] for row in report["evaluation"]["conditions"][condition]["per_trial"]
        )
        for row in rows:
            row["per_output"]["tx"]["r2"] = None
            row["per_output"]["tx"]["r2_reason"] = "constant target"
            row["uniform_average_r2"] = None
            row["variance_weighted_r2"] = None
            row["aggregate_reason"] = "one output undefined"
    _write(path, report)
    view = read_h1_chronological_report(path)
    pooled = view["evaluation"]["conditions"]["fixed_ridge"]["pooled"]
    assert pooled["per_output"]["tx"]["r2"] is None
    assert pooled["per_output"]["ty"]["r2"] == -1


def test_prediction_segments_preserve_gaps_trials_and_extrema(tmp_path):
    path = tmp_path / "report.json"
    synthetic_chronological_report(path)
    view = read_h1_chronological_report(path)
    trace = prepare_h1_prediction_traces(view, "tx")
    assert trace["trial_boundaries"] == [8, 11]
    assert [[point[0] for point in segment] for segment in trace["series"]["fixed_ridge"]] == [
        [8],
        [10],
        [11],
        [13],
    ]
    assert trace["observed"].startswith("unavailable")
    assert trace["x_label"].startswith("Original source bin index")
    # Display decimation must retain extrema and endpoints in each interval.
    view2 = copy.deepcopy(view)
    eval_row = view2["evaluation"]
    eval_row["selected_original_indices"] = list(range(8, 11)) + list(range(11, 14))
    eval_row["included_samples"] = 6
    eval_row["conditions"]["fixed_ridge"]["predictions"][1][0] = 100
    sampled = prepare_h1_prediction_traces(view2, "tx", max_points=64)
    assert any(value == 100 for segment in sampled["series"]["fixed_ridge"] for _, value in segment)
    view2["evaluation"]["selected_original_indices"] = []
    assert not prepare_h1_prediction_traces(view2, "tx")["available"]


def test_cli_api_agreement_and_import_isolation(tmp_path):
    path = tmp_path / "report.json"
    synthetic_chronological_report(path)
    expected = summarize_h1_chronological_report(read_h1_chronological_report(path))
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    command = [
        sys.executable,
        "-m",
        "dmri_studio",
        "bci",
        "inspect-h1-chronological-report",
        "--report",
        str(path),
    ]
    result = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
    assert path.read_bytes()
    import_check = subprocess.run(
        [
            sys.executable,
            "-c",
            "import dmri_studio.core.bci_reports; import sys; print(any(x in sys.modules for x in ('PySide6', 'vtk', 'h5py', 'sklearn')))",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert import_check.stdout.strip() == "False"
