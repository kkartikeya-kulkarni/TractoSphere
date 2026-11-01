"""Pinned H1 ridge reference tests use only synthetic, public-safe recordings."""

import hashlib
import json

import numpy as np
import pytest

from dmri_studio.cli.main import main
from dmri_studio.core.bci import FEATURES
from dmri_studio.core.bci_ridge import (
    SPEC_SCHEMA,
    fitting_selection,
    lagged_features,
    reference_filter,
    run_h1_ridge_experiment,
    write_h1_ridge_report,
)

h5py = pytest.importorskip("h5py")
pytest.importorskip("sklearn")


def _recording(path, *, day, partition, samples=100, offset=0, mask_false=()):
    t = np.arange(offset, offset + samples, dtype=float)
    signal = np.column_stack([np.sin(t / 7 + i / 3) + t / 200 for i in range(7)])
    with h5py.File(path, "w") as root:
        root.attrs["nwb_version"] = "2.6.0"
        root.create_dataset("identifier", data=f"HumanPitt-{partition}_{day}_set1".encode())
        root.create_dataset("session_start_time", data=f"1925-01-{day}T00:00:00".encode())
        general = root.create_group("general")
        subject = general.create_group("subject")
        subject.create_dataset("subject_id", data=f"HumanPitt-{partition}".encode())
        acq = root.create_group("acquisition")
        arrays = {
            "OpenLoopKinematics": signal,
            "OpenLoopKinematicsVelocity": signal,
            "TrialNum": np.r_[np.zeros(samples // 2), np.ones(samples - samples // 2)],
            "eval_mask": np.array([i not in mask_false for i in range(samples)], dtype=bool),
        }
        for key, values in arrays.items():
            series = acq.create_group(key)
            if key.startswith("OpenLoop"):
                series.attrs["description"] = ",".join(FEATURES)
            data = series.create_dataset("data", data=values)
            if key.startswith("OpenLoop"):
                data.attrs["unit"] = "arbitrary"
            clock = series.create_dataset("starting_time", data=0.0)
            clock.attrs["rate"] = 0.02
            clock.attrs["unit"] = "seconds"
        units = root.create_group("units")
        units.create_dataset("id", data=[4, 5])
        first = np.arange(0, samples, 3) * 0.02
        second = np.arange(1, samples, 5) * 0.02
        units.create_dataset("spike_times", data=np.r_[first, second])
        units.create_dataset("spike_times_index", data=[len(first), len(first) + len(second)])
    return path


def _spec(tmp_path, *, overlap=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    train = _recording(tmp_path / "train.nwb", day="01", partition="held_in_calib")
    calib = _recording(
        tmp_path / "calib.nwb",
        day="03" if not overlap else "02",
        partition="held_out_calib",
        offset=100,
    )
    evaluation = _recording(
        tmp_path / "eval.nwb",
        day="02" if overlap else "03",
        partition="held_out_minival",
        offset=100 if overlap else 200,
        mask_false=(0, 2, 4),
    )

    def entry(path, partition, day, offset):
        return {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "dataset_version": "synthetic-fixture-v1",
            "asset_id": path.stem,
            "participant": "HumanPitt",
            "session_day": f"1925-01-{day}",
            "recording_set": "set1",
            "benchmark_partition": partition,
            "identity_evidence": "synthetic NWB fixture construction",
            "partition_evidence": "synthetic fixture construction",
            "observation_span": {
                "start": offset,
                "stop": offset + 100,
                "evidence": "synthetic fixture sample indices",
            },
            "unit_ids": [4, 5],
        }

    specification = {
        "schema": SPEC_SCHEMA,
        "upstream_commit": "770c9b9597b8f58bf67b5a9ec6401ef705a2cb0f",
        "feature_order": list(FEATURES),
        "preprocessing": {
            "bin_period_s": 0.02,
            "tau_ms": 240.0,
            "kernel_extent": 1,
            "history_bins": 1,
            "still_threshold": 0.001,
            "train_fraction": 0.8,
        },
        "model_selection": {"alpha_logspace": [-5, 5, 20], "cv_folds": 5},
        "roles": {
            "training": entry(train, "held_in_calib", "01", 0),
            "calibration": entry(calib, "held_out_calib", "02" if overlap else "03", 100),
            "evaluation": entry(
                evaluation, "held_out_minival", "02" if overlap else "03", 100 if overlap else 200
            ),
        },
    }
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(specification))
    return path, specification


def test_filter_is_causal_and_lag_order_is_current_then_past():
    counts = np.array([[1, 0], [0, 2], [0, 0], [4, 0]], dtype=float)
    filtered = reference_filter(counts)
    assert filtered[0, 1] == 0
    assert filtered[1, 0] > 0
    assert filtered[2, 0] > 0
    np.testing.assert_allclose(reference_filter(counts[:2]), filtered[:2])
    np.testing.assert_array_equal(
        lagged_features(np.array([[1, 2], [3, 4], [5, 6]]), 1, zero_pad=False),
        [[3, 4, 1, 2], [5, 6, 3, 4]],
    )
    np.testing.assert_array_equal(
        lagged_features(np.array([[1, 2], [3, 4]]), 1, zero_pad=True),
        [[1, 2, 0, 0], [3, 4, 1, 2]],
    )


def test_fitting_normalization_uses_only_first_80_percent_and_preserves_mask():
    counts = np.arange(40, dtype=float).reshape(20, 2)
    targets = np.tile(np.arange(7, dtype=float), (20, 1)) + np.arange(20)[:, None]
    mask = np.ones(20, dtype=bool)
    mask[3] = False
    targets[5, 0] = np.nan
    copy = mask.copy()
    result = fitting_selection(counts, targets, mask, 1)
    np.testing.assert_allclose(result["normalization_mean"], result["filtered"][:16].mean(axis=0))
    assert result["exclusions"]["raw_mask_false"] == 1
    assert result["exclusions"]["nonfinite_target"] == 1
    assert (
        sum(result["exclusions"]["exclusive_reasons"].values())
        == result["exclusions"]["excluded_union"]
    )
    np.testing.assert_array_equal(mask, copy)


def test_experiment_cli_api_determinism_source_integrity_and_exclusive_output(tmp_path, capsys):
    spec_path, spec = _spec(tmp_path)
    source_bytes = {
        role: (tmp_path / spec["roles"][role]["path"]).read_bytes() for role in spec["roles"]
    }
    first = run_h1_ridge_experiment(spec_path)
    second = run_h1_ridge_experiment(spec_path)
    assert first == second
    assert first["evaluation"]["raw_eval_mask_false"] == 3
    assert first["evaluation"]["included_samples"] == 97
    assert first["fitting"]["selected_alpha"] in np.logspace(-5, 5, 20)
    output = tmp_path / "report.json"
    assert main(["bci", "ridge-h1", "--spec", str(spec_path), "--output", str(output)]) == 0
    assert json.loads(output.read_text()) == first
    assert "report" in capsys.readouterr().out
    with pytest.raises(FileExistsError):
        write_h1_ridge_report(first, output)
    with pytest.raises(ValueError, match="must not replace"):
        write_h1_ridge_report(first, spec_path)
    with pytest.raises(ValueError, match="outside the repository"):
        write_h1_ridge_report(first, __file__ + ".json")
    for role in spec["roles"]:
        assert (tmp_path / spec["roles"][role]["path"]).read_bytes() == source_bytes[role]


def test_reference_spec_rejects_ambiguous_duplicate_roles(tmp_path):
    path, _ = _spec(tmp_path)
    original = path.read_text()
    path.write_text('{"roles":{},' + original[1:])
    with pytest.raises(ValueError, match="duplicate JSON key"):
        run_h1_ridge_experiment(path)


def test_pinned_reference_fixture_numerical_conformance(tmp_path):
    """Expected values were generated independently by the pinned upstream modules."""
    spec_path, _ = _spec(tmp_path)
    report = run_h1_ridge_experiment(spec_path)
    assert report["fitting"]["selected_alpha"] == pytest.approx(69.51927961775606)
    assert report["fitting"]["internal_holdout"]["uniform_average_r2"] == pytest.approx(
        -7.8441752317972995, abs=1e-10
    )
    assert report["evaluation"]["metrics"]["variance_weighted_r2"] == pytest.approx(
        -2.1863902789318086, abs=1e-10
    )
    np.testing.assert_allclose(
        report["evaluation"]["predictions"][:2],
        [
            [
                0.27786124635766796,
                0.26886329057834407,
                0.250550743251378,
                0.224939561428033,
                0.19484918042040034,
                0.16359213314290647,
                0.1346093862582269,
            ],
            [
                0.29862358639204994,
                0.3099273111787121,
                0.3058464517342075,
                0.2868302539895073,
                0.2549721371313141,
                0.2137792372327017,
                0.167786320294812,
            ],
        ],
        rtol=0,
        atol=1e-10,
    )


def test_partition_conflict_overlap_feature_order_and_hash_are_rejected(tmp_path):
    spec_path, spec = _spec(tmp_path)
    spec["roles"]["evaluation"]["benchmark_partition"] = "held_in_calib"
    spec_path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="partition contradicts"):
        run_h1_ridge_experiment(spec_path)
    spec["roles"]["evaluation"]["benchmark_partition"] = "held_out_minival"
    spec["feature_order"] = list(reversed(FEATURES))
    spec_path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="feature order"):
        run_h1_ridge_experiment(spec_path)
    spec["feature_order"] = list(FEATURES)
    spec["roles"]["evaluation"]["sha256"] = "0" * 64
    spec_path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="SHA-256"):
        run_h1_ridge_experiment(spec_path)
    overlap_path, _ = _spec(tmp_path / "overlap", overlap=True)
    with pytest.raises(ValueError, match="overlapping"):
        run_h1_ridge_experiment(overlap_path)


def test_nonfinite_constant_and_overflow_findings(tmp_path):
    path, spec = _spec(tmp_path)
    evaluation = tmp_path / "eval.nwb"
    with h5py.File(evaluation, "r+") as root:
        root["acquisition/OpenLoopKinematicsVelocity/data"][1, 0] = np.nan
    spec["roles"]["evaluation"]["sha256"] = hashlib.sha256(evaluation.read_bytes()).hexdigest()
    path.write_text(json.dumps(spec))
    report = run_h1_ridge_experiment(path)
    assert report["evaluation"]["additional_nonfinite_target_exclusions"] == 1
    assert report["evaluation"]["included_samples"] == 96
    with h5py.File(evaluation, "r+") as root:
        root["acquisition/OpenLoopKinematicsVelocity/data"][:] = 2
    spec["roles"]["evaluation"]["sha256"] = hashlib.sha256(evaluation.read_bytes()).hexdigest()
    path.write_text(json.dumps(spec))
    assert run_h1_ridge_experiment(path)["evaluation"]["metrics"]["defined"] is False
    with h5py.File(evaluation, "r+") as root:
        root["units/spike_times_index"][:] = [300, 320]
        del root["units/spike_times"]
        root["units"].create_dataset("spike_times", data=np.r_[np.zeros(300), np.arange(20) * 0.02])
    spec["roles"]["evaluation"]["sha256"] = hashlib.sha256(evaluation.read_bytes()).hexdigest()
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="above 255"):
        run_h1_ridge_experiment(path)


def test_evaluation_labels_cannot_change_fitted_model_or_predictions(tmp_path):
    path, spec = _spec(tmp_path)
    first = run_h1_ridge_experiment(path)
    evaluation = tmp_path / "eval.nwb"
    with h5py.File(evaluation, "r+") as root:
        root["acquisition/OpenLoopKinematicsVelocity/data"][:] *= 2
    spec["roles"]["evaluation"]["sha256"] = hashlib.sha256(evaluation.read_bytes()).hexdigest()
    path.write_text(json.dumps(spec))
    second = run_h1_ridge_experiment(path)
    assert first["fitting"]["selected_alpha"] == second["fitting"]["selected_alpha"]
    assert first["evaluation"]["predictions"] == second["evaluation"]["predictions"]
    assert first["evaluation"]["metrics"] != second["evaluation"]["metrics"]


def test_calibration_binding_and_raw_mask_zero_selection(tmp_path):
    path, spec = _spec(tmp_path)
    spec["roles"]["calibration"]["session_day"] = "1925-01-04"
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="contradicts NWB metadata"):
        run_h1_ridge_experiment(path)
    spec["roles"]["calibration"]["session_day"] = "1925-01-03"
    evaluation = tmp_path / "eval.nwb"
    with h5py.File(evaluation, "r+") as root:
        root["acquisition/eval_mask/data"][:] = False
    spec["roles"]["evaluation"]["sha256"] = hashlib.sha256(evaluation.read_bytes()).hexdigest()
    path.write_text(json.dumps(spec))
    report = run_h1_ridge_experiment(path)
    assert report["evaluation"]["included_samples"] == 0
    assert report["evaluation"]["raw_eval_mask_false"] == 100
    assert report["evaluation"]["metrics"]["defined"] is False
