"""Chronological H1 comparisons use only source-indexed synthetic NWB fixtures."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from dmri_studio.cli.main import main
from dmri_studio.core.bci import FEATURES
from dmri_studio.core.bci_chronological import (
    REPORT_SCHEMA,
    SPEC_SCHEMA,
    _metrics,
    run_h1_chronological_experiment,
)
from dmri_studio.core.bci_report_upgrade import upgrade_h1_chronological_report
from dmri_studio.core.bci_reports import read_h1_chronological_report
from dmri_studio.core.bci_ridge import _load_role, reference_filter, write_h1_ridge_report

h5py = pytest.importorskip("h5py")
pytest.importorskip("sklearn")


def _fixture(tmp_path):
    source = tmp_path / "one-recording.nwb"
    n = 280
    t = np.arange(n, dtype=float)
    targets = np.column_stack([np.sin(t / (6 + col)) + t / (150 + 20 * col) for col in range(7)])
    mask = np.ones(n, dtype=bool)
    mask[::19] = False
    with h5py.File(source, "w") as root:
        root.attrs["nwb_version"] = "2.6.0"
        root.create_dataset("identifier", data=b"HumanPitt-held_in-calib_01-01_set1")
        root.create_dataset("session_start_time", data=b"1925-01-01T00:00:00")
        subject = root.create_group("general").create_group("subject")
        subject.create_dataset("subject_id", data=b"HumanPitt-held_in-calib")
        acq = root.create_group("acquisition")
        arrays = {
            "OpenLoopKinematics": targets,
            "OpenLoopKinematicsVelocity": targets,
            "TrialNum": np.arange(n) // 40 + 1,
            "eval_mask": mask,
        }
        for name, values in arrays.items():
            group = acq.create_group(name)
            if name.startswith("OpenLoop"):
                group.attrs["description"] = ",".join(FEATURES)
            data = group.create_dataset("data", data=values)
            if name.startswith("OpenLoop"):
                data.attrs["unit"] = "arbitrary"
            clock = group.create_dataset("starting_time", data=0.0)
            clock.attrs["rate"] = 0.02
            clock.attrs["unit"] = "seconds"
        units = root.create_group("units")
        units.create_dataset("id", data=[4, 5])
        first = np.arange(0, n, 3) * 0.02
        second = np.arange(1, n, 5) * 0.02
        units.create_dataset("spike_times", data=np.r_[first, second])
        units.create_dataset("spike_times_index", data=[len(first), len(first) + len(second)])
    spec = {
        "schema": SPEC_SCHEMA,
        "upstream_commit": "770c9b9597b8f58bf67b5a9ec6401ef705a2cb0f",
        "source": {
            "path": str(source),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "dataset_version": "synthetic-v1",
            "asset_id": "one-recording",
            "participant": "HumanPitt",
            "session_day": "1925-01-01",
            "recording_set": "set1",
            "benchmark_partition": "held_in_calib",
            "identity_evidence": "synthetic NWB metadata",
            "partition_evidence": "synthetic fixture",
            "observation_span": {"start": 0, "stop": n, "evidence": "synthetic indices"},
            "unit_ids": [4, 5],
        },
        "feature_order": list(FEATURES),
        "chronology_evidence": "single_nwb_clock_and_trialnum",
        "selection_rule": "first two trials train, third calibrate, fourth gap, fifth/sixth evaluate",
        "selections": {
            "training": {"start": 0, "stop": 80, "trial_numbers": [1, 2]},
            "calibration": {"start": 80, "stop": 120, "trial_numbers": [3]},
            "evaluation": {"start": 160, "stop": 240, "trial_numbers": [5, 6]},
            "unused": [
                {"start": 120, "stop": 160, "trial_numbers": [4], "reason": "gap"},
                {
                    "start": 240,
                    "stop": 280,
                    "trial_numbers": [7],
                    "reason": "final trial unconfirmed",
                },
            ],
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
    }
    path = tmp_path / "chronological.json"
    path.write_text(json.dumps(spec))
    return path, spec


def _save(path, spec):
    source = Path(spec["source"]["path"])
    spec["source"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    path.write_text(json.dumps(spec))


def test_chronological_spec_rejects_ambiguous_duplicate_selections(tmp_path):
    path, _ = _fixture(tmp_path)
    original = path.read_text()
    path.write_text('{"selections":{},' + original[1:])
    with pytest.raises(ValueError, match="duplicate JSON key"):
        run_h1_chronological_experiment(path)


def test_chronological_cli_same_evaluation_samples_metrics_and_source_integrity(tmp_path):
    path, spec = _fixture(tmp_path)
    original = Path(spec["source"]["path"]).read_bytes()
    report = run_h1_chronological_experiment(path)
    assert report["schema"] == REPORT_SCHEMA
    assert report["selections"]["evaluation"]["trial_count"] == 2
    assert report["temporal_isolation"]["calibration_history_carried_into_evaluation"] is False
    conditions = report["evaluation"]["conditions"]
    assert set(conditions) == {
        "constant_training_mean",
        "fixed_ridge",
        "normalization_recalibrated_ridge",
    }
    counts = {name: item["pooled"]["included_samples"] for name, item in conditions.items()}
    assert len(set(counts.values())) == 1
    assert all(len(item["per_trial"]) == 2 for item in conditions.values())
    expected = report["training"]["target_mean"]
    assert all(row == expected for row in conditions["constant_training_mean"]["predictions"])
    output = tmp_path / "private-report.json"
    assert (
        main(["bci", "ridge-h1-chronological", "--spec", str(path), "--output", str(output)]) == 0
    )
    assert json.loads(output.read_text()) == report
    assert Path(spec["source"]["path"]).read_bytes() == original


def test_explicit_v2_enrichment_preserves_frozen_predictions_and_sources(tmp_path):
    path, spec = _fixture(tmp_path)
    source = Path(spec["source"]["path"])
    original_source = source.read_bytes()
    v1_path = tmp_path / "frozen-v1.json"
    original = run_h1_chronological_experiment(path)
    write_h1_ridge_report(original, v1_path)
    original_v1 = v1_path.read_bytes()
    v2_path = tmp_path / "new-v2.json"
    upgrade_h1_chronological_report(v1_path, source, v2_path)
    cli_v2 = tmp_path / "cli-v2.json"
    assert (
        main(
            [
                "bci",
                "enrich-h1-chronological-report",
                "--report",
                str(v1_path),
                "--source",
                str(source),
                "--output",
                str(cli_v2),
            ]
        )
        == 0
    )
    assert json.loads(cli_v2.read_text()) == json.loads(v2_path.read_text())
    view = read_h1_chronological_report(v2_path)
    assert (
        view["metric_recomputation"]
        == "all_pooled_and_per_trial_metrics_agree_with_embedded_targets"
    )
    assert (
        view["evaluation"]["selected_original_indices"]
        == original["evaluation"]["selected_original_indices"]
    )
    for name in original["evaluation"]["conditions"]:
        assert (
            view["evaluation"]["conditions"][name]["predictions"]
            == original["evaluation"]["conditions"][name]["predictions"]
        )
    assert view["observations"]["output_units"][0]["unit"] == "arbitrary"
    assert source.read_bytes() == original_source
    assert v1_path.read_bytes() == original_v1
    altered = tmp_path / "altered-source.nwb"
    altered.write_bytes(original_source)
    with h5py.File(altered, "r+") as root:
        root["acquisition/OpenLoopKinematicsVelocity/data"][180, 0] += 0.25
    with pytest.raises(ValueError, match="source SHA-256 differs"):
        upgrade_h1_chronological_report(v1_path, altered, tmp_path / "unwritten-v2.json")
    assert not (tmp_path / "unwritten-v2.json").exists()
    with pytest.raises(ValueError, match="must not replace"):
        upgrade_h1_chronological_report(v1_path, source, v1_path)
    with pytest.raises(FileExistsError):
        upgrade_h1_chronological_report(v1_path, source, v2_path)


def test_chronological_rejects_future_calibration_and_unsupported_ordering(tmp_path):
    path, spec = _fixture(tmp_path)
    spec["chronology_evidence"] = "deidentified_dates_sorted_as_calendar"
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="unsupported temporal ordering"):
        run_h1_chronological_experiment(path)
    spec["chronology_evidence"] = "single_nwb_clock_and_trialnum"
    spec["selections"]["calibration"] = {"start": 200, "stop": 240, "trial_numbers": [6]}
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="precede evaluation"):
        run_h1_chronological_experiment(path)
    spec["selections"]["calibration"] = {"start": 80, "stop": 120, "trial_numbers": [3]}
    spec["selections"]["evaluation"]["stop"] = 230
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="complete, source-indexed"):
        run_h1_chronological_experiment(path)


def test_chronological_requires_exact_source_identity(tmp_path):
    path, spec = _fixture(tmp_path)
    spec["source"]["sha256"] = "0" * 64
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="SHA-256"):
        run_h1_chronological_experiment(path)


def test_chronological_calibration_scope_and_evaluation_label_isolation(tmp_path):
    path, spec = _fixture(tmp_path)
    first = run_h1_chronological_experiment(path)
    row = _load_role(spec["source"], "training", tmp_path)
    train = slice(0, 80)
    train_targets = row["targets"][train]
    train_selected = (
        row["raw_mask"][train]
        & np.isfinite(train_targets).all(axis=1)
        & ~np.all(np.abs(train_targets) < 0.001, axis=1)
    )
    expected_train_mean = reference_filter(row["counts"][train])[train_selected].mean(axis=0)
    expected_cal_mean = reference_filter(row["counts"][80:120])[row["raw_mask"][80:120]].mean(
        axis=0
    )
    np.testing.assert_allclose(first["training"]["feature_mean"], expected_train_mean)
    np.testing.assert_allclose(first["calibration"]["feature_mean"], expected_cal_mean)
    source = Path(spec["source"]["path"])
    with h5py.File(source, "r+") as root:
        root["acquisition/OpenLoopKinematicsVelocity/data"][200, 0] += 5
    _save(path, spec)
    second = run_h1_chronological_experiment(path)
    assert first["training"] == second["training"]
    assert first["calibration"] == second["calibration"]
    for name in first["evaluation"]["conditions"]:
        assert (
            first["evaluation"]["conditions"][name]["predictions"]
            == second["evaluation"]["conditions"][name]["predictions"]
        )
    assert (
        first["evaluation"]["conditions"]["fixed_ridge"]["pooled"]
        != second["evaluation"]["conditions"]["fixed_ridge"]["pooled"]
    )
    with h5py.File(source, "r+") as root:
        root["acquisition/OpenLoopKinematicsVelocity/data"][90, 0] += 5
    _save(path, spec)
    label_changed = run_h1_chronological_experiment(path)
    assert label_changed["calibration"] == second["calibration"]
    for name in second["evaluation"]["conditions"]:
        assert (
            label_changed["evaluation"]["conditions"][name]["predictions"]
            == second["evaluation"]["conditions"][name]["predictions"]
        )
    with h5py.File(source, "r+") as root:
        root["units/spike_times"][30] += 0.02  # Earlier calibration bin, same unit.
    _save(path, spec)
    third = run_h1_chronological_experiment(path)
    assert label_changed["training"] == third["training"]
    assert (
        second["evaluation"]["conditions"]["fixed_ridge"]["predictions"]
        == third["evaluation"]["conditions"]["fixed_ridge"]["predictions"]
    )
    assert second["calibration"]["feature_mean"] != third["calibration"]["feature_mean"]
    assert (
        second["evaluation"]["conditions"]["normalization_recalibrated_ridge"]["predictions"]
        != third["evaluation"]["conditions"]["normalization_recalibrated_ridge"]["predictions"]
    )


def test_future_neural_input_cannot_change_earlier_predictions(tmp_path):
    path, spec = _fixture(tmp_path)
    first = run_h1_chronological_experiment(path)
    source = Path(spec["source"]["path"])
    with h5py.File(source, "r+") as root:
        root["units/spike_times"][73] += 0.04  # Shift a late spike to a later bin.
    _save(path, spec)
    second = run_h1_chronological_experiment(path)
    for name in first["evaluation"]["conditions"]:
        before = first["evaluation"]["conditions"][name]["predictions"]
        after = second["evaluation"]["conditions"][name]["predictions"]
        assert before[:59] == after[:59]
    assert (
        first["evaluation"]["conditions"]["fixed_ridge"]["predictions"][59:]
        != second["evaluation"]["conditions"]["fixed_ridge"]["predictions"][59:]
    )


def test_constant_predictor_and_metric_definitions(tmp_path):
    path, _ = _fixture(tmp_path)
    report = run_h1_chronological_experiment(path)
    source = Path(report["sources"]["recording"]["path"])
    with h5py.File(source, "r") as root:
        y = root["acquisition/OpenLoopKinematicsVelocity/data"][:]
    selected = report["evaluation"]["selected_original_indices"]
    actual = y[selected, 0]
    predicted = np.array(
        report["evaluation"]["conditions"]["constant_training_mean"]["predictions"]
    )
    chosen = np.array(selected) - report["selections"]["evaluation"]["start"]
    error = actual - predicted[chosen, 0]
    measured = report["evaluation"]["conditions"]["constant_training_mean"]["pooled"]["per_output"][
        "tx"
    ]
    assert measured["mae"] == pytest.approx(np.mean(np.abs(error)))
    assert measured["rmse"] == pytest.approx(np.sqrt(np.mean(error**2)))
    assert measured["r2"] == pytest.approx(
        1 - np.sum(error**2) / np.sum((actual - actual.mean()) ** 2)
    )
    constant = _metrics(np.ones((4, 7)), np.zeros((4, 7)))
    assert constant["uniform_average_r2"] is None
    assert (
        constant["per_output"]["tx"]["r2_reason"]
        == "fewer than two observations or constant target"
    )


def test_chronological_import_isolation():
    code = "import sys; import dmri_studio.core.bci_chronological; assert not any(x in sys.modules for x in ('h5py','sklearn','PySide6','vtk'))"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    subprocess.run([sys.executable, "-c", code], env=env, check=True)
