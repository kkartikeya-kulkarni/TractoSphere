"""Self-contained H1 chronology report alignment and recomputation checks."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from bci_report_fixture import synthetic_chronological_v2_report

from dmri_studio.core.bci_reports import (
    compute_embedded_metrics,
    prepare_h1_prediction_traces,
    read_h1_chronological_report,
    summarize_h1_chronological_report,
)


def _save(path, report):
    path.write_text(json.dumps(report, sort_keys=True))


def test_v2_targets_metrics_units_and_traces(tmp_path):
    path = tmp_path / "v2.json"
    synthetic_chronological_v2_report(path)
    before = path.read_bytes()
    view = read_h1_chronological_report(path)
    assert path.read_bytes() == before
    assert (
        view["metric_recomputation"]
        == "all_pooled_and_per_trial_metrics_agree_with_embedded_targets"
    )
    assert view["independent_source_verification"] == "not_performed"
    assert view["scientific_acceptance"] == "unassigned"
    assert view["observations"]["original_indices"] == [8, 10, 11, 13]
    assert view["observations"]["trial_numbers"] == [5, 5, 6, 6]
    assert view["observations"]["output_units"][0]["unit"] == "arbitrary"
    assert "/private/absent/" not in json.dumps(view)
    trace = prepare_h1_prediction_traces(view, "tx")
    assert [[point[0] for point in segment] for segment in trace["series"]["observed"]] == [
        [8],
        [10],
        [11],
        [13],
    ]
    assert trace["trial_boundaries"] == [8, 11]
    assert "available" in trace["observed"]


@pytest.mark.parametrize(
    "alter, message",
    [
        (lambda r: r["observations"]["targets"].pop(), "target sample dimension"),
        (lambda r: r["observations"]["targets"][0].pop(), "target feature dimension"),
        (
            lambda r: r["observations"]["targets"][0].__setitem__(0, 500.0),
            "disagrees with embedded observations",
        ),
        (
            lambda r: r["evaluation"]["conditions"]["fixed_ridge"]["pooled"]["per_output"][
                "tx"
            ].__setitem__(
                "rmse",
                r["evaluation"]["conditions"]["fixed_ridge"]["pooled"]["per_output"]["tx"]["rmse"]
                + 0.1,
            ),
            "disagrees with embedded observations",
        ),
        (
            lambda r: r["evaluation"]["conditions"]["fixed_ridge"]["predictions"][0].__setitem__(
                0, 500.0
            ),
            "disagrees with embedded observations",
        ),
        (lambda r: r["observations"]["original_indices"].__setitem__(0, 9), "selected indices"),
        (lambda r: r["observations"]["trial_numbers"].__setitem__(0, 6), "trial membership"),
        (
            lambda r: r["observations"]["candidate_raw_eval_mask"].__setitem__(0, False),
            "selected indices",
        ),
        (lambda r: r["observations"]["output_labels"].reverse(), "feature order"),
        (lambda r: r["observations"]["output_units"][0].update(label="ty"), "unit or provenance"),
        (
            lambda r: r["observations"]["metric_definitions"].update(mae="different"),
            "metric definitions",
        ),
        (
            lambda r: r["enrichment"].update(source_sha256="0" * 64),
            "source extraction provenance",
        ),
    ],
)
def test_v2_rejects_misalignment_and_tampering(tmp_path, alter, message):
    path = tmp_path / "v2.json"
    report = synthetic_chronological_v2_report(path)
    alter(report)
    _save(path, report)
    with pytest.raises(ValueError, match=message):
        read_h1_chronological_report(path)


def test_v2_accepts_unknown_units_without_inference(tmp_path):
    path = tmp_path / "v2.json"
    report = synthetic_chronological_v2_report(path)
    for row in report["observations"]["output_units"]:
        row.update(unit=None, provenance="unknown")
    _save(path, report)
    view = read_h1_chronological_report(path)
    assert all(row["unit"] is None for row in view["observations"]["output_units"])


def test_constant_and_empty_target_metric_definitions():
    empty = compute_embedded_metrics([], [])
    assert empty["uniform_average_r2"] is None
    assert empty["per_output"]["tx"]["mae"] is None
    constant = compute_embedded_metrics([[1.0] * 7, [1.0] * 7], [[0.0] * 7, [0.0] * 7])
    assert constant["per_output"]["tx"]["r2"] is None
    assert (
        constant["per_output"]["tx"]["r2_reason"]
        == "fewer than two observations or constant target"
    )
    assert constant["uniform_average_r2"] is None


def test_v2_cli_api_agreement_and_import_isolation(tmp_path):
    path = tmp_path / "v2.json"
    synthetic_chronological_v2_report(path)
    expected = summarize_h1_chronological_report(read_h1_chronological_report(path))
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dmri_studio",
            "bci",
            "inspect-h1-chronological-report",
            "--report",
            str(path),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
    assert '"targets":' not in result.stdout
    isolated = subprocess.run(
        [
            sys.executable,
            "-c",
            "import dmri_studio.core.bci_report_upgrade; import sys; print(any(x in sys.modules for x in ('PySide6', 'vtk', 'h5py', 'sklearn')))",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert isolated.stdout.strip() == "False"
