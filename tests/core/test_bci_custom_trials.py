"""Custom whole-trial selections remain separate from the pinned file-level reference."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from dmri_studio.cli.main import main
from dmri_studio.core.bci import FEATURES
from dmri_studio.core.bci_ridge import (
    CUSTOM_REPORT_SCHEMA,
    CUSTOM_SPEC_SCHEMA,
    _custom_trial_selections,
    _load_role,
    fitting_selection,
    reference_filter,
    run_h1_custom_trial_experiment,
)

h5py = pytest.importorskip("h5py")
pytest.importorskip("sklearn")


def _make_nwb(path, *, day, partition, length, signal_offset):
    t = np.arange(length, dtype=float) + signal_offset
    velocity = np.column_stack([np.sin(t / (5 + column)) + t / 300 for column in range(7)])
    with h5py.File(path, "w") as root:
        root.attrs["nwb_version"] = "2.6.0"
        root.create_dataset("identifier", data=f"HumanPitt-{partition}_{day}_set1".encode())
        root.create_dataset("session_start_time", data=f"1925-01-{day}T00:00:00".encode())
        subject = root.create_group("general").create_group("subject")
        subject.create_dataset("subject_id", data=f"HumanPitt-{partition}".encode())
        acquisition = root.create_group("acquisition")
        values = {
            "OpenLoopKinematics": velocity,
            "OpenLoopKinematicsVelocity": velocity,
            "TrialNum": np.arange(length) // 40 + 1,
            "eval_mask": np.array([index % 17 != 0 for index in range(length)], dtype=bool),
        }
        for name, array in values.items():
            group = acquisition.create_group(name)
            if name.startswith("OpenLoop"):
                group.attrs["description"] = ",".join(FEATURES)
            data = group.create_dataset("data", data=array)
            if name.startswith("OpenLoop"):
                data.attrs["unit"] = "arbitrary"
            clock = group.create_dataset("starting_time", data=0.0)
            clock.attrs["rate"] = 0.02
            clock.attrs["unit"] = "seconds"
        units = root.create_group("units")
        units.create_dataset("id", data=[4, 5])
        first = np.arange(0, length, 3) * 0.02
        second = np.arange(1, length, 5) * 0.02
        units.create_dataset("spike_times", data=np.r_[first, second])
        units.create_dataset("spike_times_index", data=[len(first), len(first) + len(second)])


def _fixture(tmp_path):
    train = tmp_path / "train.nwb"
    calibration = tmp_path / "calibration.nwb"
    evaluation = tmp_path / "evaluation.nwb"
    _make_nwb(train, day="01", partition="held_in_calib", length=200, signal_offset=0)
    _make_nwb(calibration, day="02", partition="held_in_calib", length=200, signal_offset=400)
    _make_nwb(evaluation, day="02", partition="held_in_minival", length=80, signal_offset=400)

    def role(path, partition, day, length):
        return {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "dataset_version": "synthetic-custom-v1",
            "asset_id": path.stem,
            "participant": "HumanPitt",
            "session_day": f"1925-01-{day}",
            "recording_set": "set1",
            "benchmark_partition": partition,
            "identity_evidence": "synthetic NWB construction",
            "partition_evidence": "synthetic fixture construction",
            "observation_span": {
                "start": 0,
                "stop": length,
                "evidence": "synthetic source sample coordinates",
            },
            "unit_ids": [4, 5],
        }

    spec = {
        "schema": CUSTOM_SPEC_SCHEMA,
        "upstream_commit": "770c9b9597b8f58bf67b5a9ec6401ef705a2cb0f",
        "feature_order": list(FEATURES),
        "preprocessing": {
            "bin_period_s": 0.02,
            "tau_ms": 240.0,
            "kernel_extent": 1,
            "history_bins": 0,
            "still_threshold": 0.001,
            "train_fraction": 0.8,
        },
        "model_selection": {"alpha_logspace": [-5, 5, 20], "cv_folds": 5},
        "roles": {
            "training": role(train, "held_in_calib", "01", 200),
            "calibration": role(calibration, "held_in_calib", "02", 200),
            "evaluation": role(evaluation, "held_in_minival", "02", 80),
        },
        "selections": {
            "training": {
                "start": 40,
                "stop": 160,
                "trial_numbers": [2, 3, 4],
                "evidence": "synthetic TrialNum boundaries",
            },
            "calibration": {
                "start": 120,
                "stop": 160,
                "trial_numbers": [4],
                "evidence": "synthetic TrialNum boundaries",
            },
            "evaluation": {
                "start": 0,
                "stop": 40,
                "trial_numbers": [1],
                "evidence": "synthetic TrialNum boundaries",
            },
        },
    }
    spec_path = tmp_path / "custom.json"
    spec_path.write_text(json.dumps(spec))
    return spec_path, spec


def _save(path, spec):
    for row in spec["roles"].values():
        source = row["path"]
        row["sha256"] = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    path.write_text(json.dumps(spec))


def test_custom_complete_trials_cli_report_and_source_preservation(tmp_path):
    path, spec = _fixture(tmp_path)
    source_bytes = {role: Path(row["path"]).read_bytes() for role, row in spec["roles"].items()}
    report = run_h1_custom_trial_experiment(path)
    assert report["schema"] == CUSTOM_REPORT_SCHEMA
    assert [report["sources"][role]["selected"]["samples"] for role in spec["roles"]] == [
        120,
        40,
        40,
    ]
    assert [report["sources"][role]["selected"]["trial_count"] for role in spec["roles"]] == [
        3,
        1,
        1,
    ]
    assert (
        report["temporal_isolation"]["unused_source_bins_between_calibration_and_evaluation"] == 80
    )
    assert report["temporal_isolation"]["unanalyzed_gap_s_between_selected_windows"] == 1.6
    assert report["temporal_isolation"]["causal_filter_support_bins"] == 12
    assert report["sources"]["evaluation"]["selected"]["last_bin_window_end_s"] == 0.78
    assert report["sources"]["calibration"]["selected"]["first_bin_window_start_s"] == 2.38
    assert report["evaluation"]["included_samples"] == 37
    output = tmp_path / "custom-report.json"
    assert (
        main(["bci", "ridge-h1-custom-trials", "--spec", str(path), "--output", str(output)]) == 0
    )
    assert json.loads(output.read_text()) == report
    assert {
        role: Path(row["path"]).read_bytes() for role, row in spec["roles"].items()
    } == source_bytes


def test_custom_rejects_partial_trials_wrong_ids_and_overlapping_roles(tmp_path):
    path, spec = _fixture(tmp_path)
    spec["selections"]["calibration"]["start"] = 121
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="complete TrialNum"):
        run_h1_custom_trial_experiment(path)
    spec["selections"]["calibration"]["start"] = 120
    spec["selections"]["calibration"]["trial_numbers"] = [3]
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="trial IDs"):
        run_h1_custom_trial_experiment(path)
    spec["selections"]["calibration"] = {
        "start": 0,
        "stop": 40,
        "trial_numbers": [1],
        "evidence": "synthetic overlap",
    }
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="overlap"):
        run_h1_custom_trial_experiment(path)
    spec["selections"]["calibration"] = {
        "start": 40,
        "stop": 80,
        "trial_numbers": [2],
        "evidence": "synthetic adjacent trial boundary",
    }
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="share a bin boundary"):
        run_h1_custom_trial_experiment(path)


def test_custom_requires_source_index_mapping_and_role_provenance(tmp_path):
    path, spec = _fixture(tmp_path)
    with h5py.File(spec["roles"]["calibration"]["path"], "r+") as root:
        root["acquisition/OpenLoopKinematicsVelocity/data"][0, 0] += 1
    _save(path, spec)
    with pytest.raises(ValueError, match="exact prefix"):
        run_h1_custom_trial_experiment(path)
    with h5py.File(spec["roles"]["calibration"]["path"], "r+") as root:
        root["acquisition/OpenLoopKinematicsVelocity/data"][0, 0] -= 1
    spec["roles"]["calibration"]["session_day"] = "1925-01-03"
    _save(path, spec)
    with pytest.raises(ValueError, match="contradicts NWB metadata"):
        run_h1_custom_trial_experiment(path)


def test_custom_requires_raw_spike_prefix_not_only_matching_bin_counts(tmp_path):
    path, spec = _fixture(tmp_path)
    with h5py.File(spec["roles"]["evaluation"]["path"], "r+") as root:
        # Both timestamps remain in the same analyzed 20 ms bin.
        root["units/spike_times"][1] += 0.001
    _save(path, spec)
    with pytest.raises(ValueError, match="spike-time prefix"):
        run_h1_custom_trial_experiment(path)


def test_custom_requires_nonvelocity_kinematic_prefix(tmp_path):
    path, spec = _fixture(tmp_path)
    with h5py.File(spec["roles"]["evaluation"]["path"], "r+") as root:
        root["acquisition/OpenLoopKinematics/data"][10, 0] += 1
    _save(path, spec)
    with pytest.raises(ValueError, match="kinematic-observation prefix"):
        run_h1_custom_trial_experiment(path)


def test_custom_resets_filter_and_excludes_unselected_context(tmp_path):
    path, spec = _fixture(tmp_path)
    first = run_h1_custom_trial_experiment(path)
    full = {role: _load_role(row, role, tmp_path) for role, row in spec["roles"].items()}
    selected, _ = _custom_trial_selections(full, spec["selections"])
    filtered = fitting_selection(
        selected["training"]["counts"],
        selected["training"]["targets"],
        selected["training"]["raw_mask"],
        0,
    )["filtered"]
    assert filtered[0, 0] != pytest.approx(reference_filter(full["training"]["counts"])[40, 0])
    with h5py.File(spec["roles"]["training"]["path"], "r+") as root:
        root["acquisition/OpenLoopKinematicsVelocity/data"][0, 0] += 20
    with h5py.File(spec["roles"]["calibration"]["path"], "r+") as root:
        root["acquisition/OpenLoopKinematicsVelocity/data"][90, 0] += 20
    _save(path, spec)
    second = run_h1_custom_trial_experiment(path)
    assert first["fitting"]["selected_alpha"] == second["fitting"]["selected_alpha"]
    assert first["evaluation"]["predictions"] == second["evaluation"]["predictions"]


def test_custom_evaluation_labels_do_not_enter_fitting_or_calibration(tmp_path):
    path, spec = _fixture(tmp_path)
    first = run_h1_custom_trial_experiment(path)
    for role in ("calibration", "evaluation"):
        with h5py.File(spec["roles"][role]["path"], "r+") as root:
            root["acquisition/OpenLoopKinematicsVelocity/data"][10, 0] += 3
    _save(path, spec)
    second = run_h1_custom_trial_experiment(path)
    assert first["fitting"]["selected_alpha"] == second["fitting"]["selected_alpha"]
    assert (
        first["evaluation"]["calibration_raw_mean"] == second["evaluation"]["calibration_raw_mean"]
    )
    assert first["evaluation"]["predictions"] == second["evaluation"]["predictions"]
    assert first["evaluation"]["metrics"] != second["evaluation"]["metrics"]
