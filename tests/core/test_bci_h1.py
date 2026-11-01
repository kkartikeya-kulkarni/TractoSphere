"""Synthetic modern H1 structures exercise the read-only scientific contract."""

import hashlib
import json

import numpy as np
import pytest

from dmri_studio.cli.main import main
from dmri_studio.core.bci import (
    bin_h1_spikes,
    describe_h1_timing,
    h1_bin_windows,
    inspect_falcon_h1,
)

h5py = pytest.importorskip("h5py")


@pytest.fixture
def recording(tmp_path):
    path = tmp_path / "example.nwb"
    with h5py.File(path, "w") as root:
        root.attrs["nwb_version"] = "2.6.0"
        acquisition = root.create_group("acquisition")
        values = {
            "OpenLoopKinematics": np.arange(42, dtype=float).reshape(6, 7),
            "OpenLoopKinematicsVelocity": np.ones((6, 7), dtype=np.float32),
            "TrialNum": np.array([3, 3, 4, 4, 4, 4], dtype=float),
            "eval_mask": np.array([1, 0, 1, 1, 0, 1], dtype=bool),
        }
        for name, array in values.items():
            group = acquisition.create_group(name)
            if name.startswith("OpenLoop"):
                group.attrs["description"] = "tx,ty,tz,rx,g1,g2,g3"
            data = group.create_dataset("data", data=array)
            if name.startswith("OpenLoop"):
                data.attrs["unit"] = "arbitrary"
            clock = group.create_dataset("starting_time", data=0.0)
            clock.attrs["rate"] = 0.02
            clock.attrs["unit"] = "seconds"
        units = root.create_group("units")
        units.create_dataset("id", data=[4, 5])
        units.create_dataset("spike_times", data=[0.01, 0.03])
        units.create_dataset("spike_times_index", data=[1, 2])
    return path


def test_h1_inspection_and_cli_share_exact_read_only_summary(recording, capsys):
    original = recording.read_bytes()
    report = inspect_falcon_h1(recording)
    assert report["source"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert report["time"]["sample_count"] == 6
    assert report["time"]["last_sample_s"] == 0.1
    assert report["time"]["nominal_duration_s"] == pytest.approx(0.12)
    assert report["time"]["analyzed_exposure_s"] == pytest.approx(0.12)
    assert report["trials"]["segments"][0]["start_index"] == 0
    assert report["trials"]["segments"][0]["stop_index_exclusive"] == 2
    assert report["trials"]["segments"][1]["start_index"] == 2
    assert report["trials"]["segments"][1]["stop_index_exclusive"] == 6
    assert report["evaluation_mask"]["true_count"] == 4
    assert report["evaluation_mask"]["false_count"] == 2
    assert report["units"]["count"] == 2
    assert report["units"]["spike_count"] == 2
    assert report["units"]["empty_unit_count"] == 0
    assert [row["observed_spikes"] for row in report["units"]["per_unit"]] == [1, 1]
    assert report["units"]["per_unit"][0]["rate_hz"] == pytest.approx(1 / 0.12)
    assert report["scientific_acceptance"] == "unassigned"
    assert main(["bci", "inspect-h1", "--nwb", str(recording)]) == 0
    assert json.loads(capsys.readouterr().out) == report
    assert recording.read_bytes() == original


def test_bin_edges_match_upstream_histogram_on_ordinary_fixture():
    ends = np.array([0.0, 0.02, 0.04, 0.06])
    spikes = np.array([-0.02, -0.01, 0.0, 0.019, 0.02, 0.04, 0.06])
    actual = bin_h1_spikes(spikes, ends)
    upstream_counts, _ = np.histogram(spikes, bins=np.r_[-0.02, ends])
    assert actual.dtype == np.uint64
    np.testing.assert_array_equal(actual, upstream_counts)
    np.testing.assert_array_equal(actual, [2, 2, 1, 2])


def test_empty_duplicate_unsorted_nonfinite_and_overflow_contract():
    np.testing.assert_array_equal(bin_h1_spikes([], []), np.array([], dtype=np.uint64))
    np.testing.assert_array_equal(bin_h1_spikes([], [0.0]), [0])
    np.testing.assert_array_equal(bin_h1_spikes([0.0, 0.0, 0.02], [0.0, 0.02]), [0, 3])
    assert bin_h1_spikes(np.zeros(300), [0.0, 0.02])[-1] == 300
    with pytest.raises(ValueError, match="sorted"):
        bin_h1_spikes([0.01, 0.0], [0.0, 0.02])
    with pytest.raises(ValueError, match="finite"):
        bin_h1_spikes([np.nan], [0.0])
    with pytest.raises(ValueError, match="increase strictly"):
        h1_bin_windows([0.0, 0.0])


def test_gap_and_short_interval_use_actual_exposure_and_omit_gap_spikes():
    ends = np.array([0.0, 0.05, 0.06])
    starts, _ = h1_bin_windows(ends)
    np.testing.assert_allclose(starts, [-0.02, 0.03, 0.05])
    assert np.sum(ends - starts) == pytest.approx(0.05)
    timing = describe_h1_timing(ends)
    assert timing["nominal_duration_s"] == pytest.approx(0.06)
    assert timing["analyzed_exposure_s"] == pytest.approx(0.05)
    assert timing["gap_count"] == 1
    assert timing["gap_duration_s"] == pytest.approx(0.03)
    assert timing["short_interval_count"] == 1
    np.testing.assert_array_equal(
        bin_h1_spikes([-0.01, 0.01, 0.029, 0.03, 0.05, 0.06], ends), [1, 1, 2]
    )


def test_descriptive_mask_nonfinite_and_trial_exposure_are_separate(recording):
    with h5py.File(recording, "r+") as root:
        root["acquisition/OpenLoopKinematics/data"][1, 0] = np.nan  # raw mask false
        root["acquisition/OpenLoopKinematics/data"][2, 0] = np.inf  # raw mask true
        root["units/spike_times"][:] = [0.02, 0.04]
    report = inspect_falcon_h1(recording)
    assert report["kinematics"]["position_nonfinite_by_feature"][0] == 2
    assert report["kinematics"]["position_nonfinite_by_raw_mask"]["true"][0] == 1
    assert report["kinematics"]["position_nonfinite_by_raw_mask"]["false"][0] == 1
    assert report["evaluation_mask"]["false_count"] == 2
    assert report["trials"]["segments"][0]["analyzed_exposure_s"] == pytest.approx(0.04)
    assert report["trials"]["segments"][1]["analyzed_exposure_s"] == pytest.approx(0.08)
    assert report["trials"]["segments"][0]["observed_spikes_all_units"] == 0
    assert report["trials"]["segments"][1]["observed_spikes_all_units"] == 2
    with h5py.File(recording, "r") as root:
        assert np.isnan(root["acquisition/OpenLoopKinematics/data"][1, 0])
        assert np.isinf(root["acquisition/OpenLoopKinematics/data"][2, 0])


def test_empty_unit_and_outside_window_spikes_are_reported(recording):
    with h5py.File(recording, "r+") as root:
        root["units/spike_times_index"][:] = [0, 2]
        root["units/spike_times"][:] = [-0.03, 0.2]
    report = inspect_falcon_h1(recording)
    assert report["units"]["empty_unit_count"] == 1
    assert report["units"]["zero_observed_unit_count"] == 2
    assert report["units"]["spikes_outside_analyzed_windows"] == {
        "before": 1,
        "gap": 0,
        "after": 1,
    }
    assert all(row["rate_hz"] == 0 for row in report["units"]["per_unit"])


def test_unsorted_source_spikes_are_rejected_without_repair(recording):
    with h5py.File(recording, "r+") as root:
        root["units/spike_times_index"][:] = [2, 2]
        root["units/spike_times"][:] = [0.03, 0.01]
    with pytest.raises(ValueError, match="sorted"):
        inspect_falcon_h1(recording)


def test_inspector_descriptive_count_does_not_wrap_above_255(recording):
    with h5py.File(recording, "r+") as root:
        del root["units/spike_times"]
        root["units"].create_dataset("spike_times", data=np.zeros(300))
        root["units/spike_times_index"][:] = [300, 300]
    report = inspect_falcon_h1(recording)
    assert report["units"]["per_unit"][0]["observed_spikes"] == 300
    assert report["units"]["per_unit"][0]["duplicate_timestamps"] == 299
    assert report["trials"]["segments"][0]["observed_spikes_all_units"] == 300


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("rate", 0.0, "invalid timing"),
        ("rate", 0.03, "20 ms"),
        ("starting_time", float("nan"), "invalid timing"),
        ("different_start", 0.02, "timing differs"),
    ],
)
def test_malformed_timing_is_rejected(recording, field, value, message):
    with h5py.File(recording, "r+") as root:
        clock = root["acquisition/TrialNum/starting_time"]
        if field == "rate":
            clock.attrs["rate"] = value
        elif field == "different_start":
            clock[()] = value
        else:
            clock[()] = value
    with pytest.raises(ValueError, match=message):
        inspect_falcon_h1(recording)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("OpenLoopKinematicsVelocity", np.ones((6, 6)), "time × 7"),
        ("OpenLoopKinematicsVelocity", np.ones((5, 7)), "sample count"),
        ("TrialNum", np.ones(5), "sample count"),
        ("eval_mask", np.ones(5), "sample count"),
    ],
)
def test_inconsistent_dimensions_are_rejected(recording, field, replacement, message):
    with h5py.File(recording, "r+") as root:
        group = root[f"acquisition/{field}"]
        del group["data"]
        group.create_dataset("data", data=replacement)
    with pytest.raises(ValueError, match=message):
        inspect_falcon_h1(recording)


@pytest.mark.parametrize("field", ["eval_mask", "TrialNum", "OpenLoopKinematicsVelocity"])
def test_missing_required_fields_are_rejected(recording, field):
    with h5py.File(recording, "r+") as root:
        del root[f"acquisition/{field}"]
    with pytest.raises(ValueError, match="Missing.*H1 field"):
        inspect_falcon_h1(recording)


def test_evaluation_mask_must_be_binary_and_is_not_inverted(recording, capsys):
    with h5py.File(recording, "r+") as root:
        group = root["acquisition/eval_mask"]
        del group["data"]
        group.create_dataset("data", data=[0, 0, 0, 1, 1, 1])
    report = inspect_falcon_h1(recording)
    assert report["evaluation_mask"] == {
        "source": "eval_mask",
        "true_count": 3,
        "false_count": 3,
    }
    with h5py.File(recording, "r+") as root:
        root["acquisition/eval_mask/data"][0] = 2
    with pytest.raises(ValueError, match="0/1"):
        inspect_falcon_h1(recording)
    assert main(["bci", "inspect-h1", "--nwb", str(recording)]) == 1
    assert "0/1" in capsys.readouterr().err


@pytest.mark.parametrize(
    "values", [[3, 2, 3, 3, 4, 4], [3, 3.5, 4, 4, 4, 4], [3, 3, float("nan"), 4, 4, 4]]
)
def test_invalid_trial_numbers_are_rejected(recording, values):
    with h5py.File(recording, "r+") as root:
        root["acquisition/TrialNum/data"][:] = values
    with pytest.raises(ValueError, match="TrialNum"):
        inspect_falcon_h1(recording)


def test_nonfinite_covariate_is_counted_without_repair(recording):
    with h5py.File(recording, "r+") as root:
        root["acquisition/OpenLoopKinematicsVelocity/data"][2, 4] = np.nan
    report = inspect_falcon_h1(recording)
    assert report["kinematics"]["velocity_nonfinite_by_feature"] == [0, 0, 0, 0, 1, 0, 0]
    with h5py.File(recording, "r") as root:
        assert np.isnan(root["acquisition/OpenLoopKinematicsVelocity/data"][2, 4])


def test_ragged_spike_index_and_nonfinite_times_are_rejected(recording):
    with h5py.File(recording, "r+") as root:
        root["units/spike_times_index"][1] = 3
    with pytest.raises(ValueError, match="inconsistent"):
        inspect_falcon_h1(recording)
    with h5py.File(recording, "r+") as root:
        root["units/spike_times_index"][1] = 2
        root["units/spike_times"][0] = np.inf
    with pytest.raises(ValueError, match="nonfinite"):
        inspect_falcon_h1(recording)


def test_source_change_during_inspection_is_rejected(recording, monkeypatch):
    import dmri_studio.core.bci as bci

    original_hash = bci.hash_file

    def changed_source(path):
        digest = original_hash(path)
        with h5py.File(path, "r+") as root:
            root["acquisition/eval_mask/data"][0] = False
        return digest

    monkeypatch.setattr(bci, "hash_file", changed_source)
    with pytest.raises(ValueError, match="changed during inspection"):
        inspect_falcon_h1(recording)
