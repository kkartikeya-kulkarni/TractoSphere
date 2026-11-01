"""Small synthetic H1 files verify manifest identity and comparison boundaries."""

import hashlib
import json
import shutil

import numpy as np
import pytest

from dmri_studio.cli.main import main
from dmri_studio.core.bci import describe_h1_timing, inspect_falcon_h1
from dmri_studio.core.bci_inventory import (
    SUPPORTED_BINNING,
    SUPPORTED_MEASUREMENT,
    inspect_h1_inventory,
)

h5py = pytest.importorskip("h5py")


def _recording(path, *, session="1925-01-01", recording="set-a", samples=6):
    with h5py.File(path, "w") as root:
        root.attrs["nwb_version"] = "2.6.0"
        root.create_dataset("identifier", data=recording.encode())
        root.create_dataset("session_start_time", data=f"{session}T11:00:00-05:00".encode())
        acquisition = root.create_group("acquisition")
        arrays = {
            "OpenLoopKinematics": np.zeros((samples, 7)),
            "OpenLoopKinematicsVelocity": np.ones((samples, 7)),
            "TrialNum": np.r_[np.ones(samples // 2), np.full(samples - samples // 2, 2)],
            "eval_mask": np.array([i % 2 == 0 for i in range(samples)]),
        }
        for name, values in arrays.items():
            group = acquisition.create_group(name)
            if name.startswith("OpenLoop"):
                group.attrs["description"] = "tx,ty,tz,rx,g1,g2,g3"
            data = group.create_dataset("data", data=values)
            if name.startswith("OpenLoop"):
                data.attrs["unit"] = "arbitrary"
            clock = group.create_dataset("starting_time", data=0.0)
            clock.attrs["rate"] = 0.02
            clock.attrs["unit"] = "seconds"
        units = root.create_group("units")
        units.create_dataset("id", data=[1, 2])
        units.create_dataset("spike_times", data=[0.01, 0.03, 0.03])
        units.create_dataset("spike_times_index", data=[1, 3])
    return path


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entry(path, entry_id, *, identity=None):
    return {
        "entry_id": entry_id,
        "path": str(path),
        "sha256": _digest(path),
        "identity": identity
        or {
            "participant": None,
            "session_day": None,
            "recording_set": None,
            "benchmark_partition": None,
        },
    }


def _manifest(tmp_path, entries):
    path = tmp_path / "inventory.json"
    path.write_text(
        json.dumps(
            {
                "schema": "falcon-h1-inventory-manifest-v1",
                "binning": SUPPORTED_BINNING,
                "measurement": SUPPORTED_MEASUREMENT,
                "recordings": entries,
            }
        )
    )
    return path


def test_manifest_rejects_ambiguous_duplicate_declarations(tmp_path):
    source = _recording(tmp_path / "source.nwb")
    manifest = _manifest(tmp_path, [_entry(source, "one")])
    original = manifest.read_text()
    manifest.write_text('{"schema":"unsupported",' + original[1:])
    with pytest.raises(ValueError, match="duplicate JSON key"):
        inspect_h1_inventory(manifest)


def test_same_session_different_recordings_are_distinct_and_match_single_api(tmp_path, capsys):
    first = _recording(tmp_path / "first.nwb", recording="set-a")
    second = _recording(tmp_path / "second.nwb", recording="set-b", samples=4)
    before = (_digest(first), _digest(second))
    manifest = _manifest(tmp_path, [_entry(first, "a"), _entry(second, "b")])
    report = inspect_h1_inventory(manifest)
    assert report == inspect_h1_inventory(manifest)
    assert report["complete"] is True
    assert [row["entry_id"] for row in report["recordings"]] == ["a", "b"]
    assert report["comparability"]["descriptive_eligible"] is True
    assert report["comparability"]["future_decoder_training_suitability"] == "not assessed"
    assert [row["identity"]["session_day"]["value"] for row in report["recordings"]] == [
        "1925-01-01"
    ] * 2
    assert [row["identity"]["recording_set"]["value"] for row in report["recordings"]] == [
        "set-a",
        "set-b",
    ]
    assert all(row["identity"]["participant"] is None for row in report["recordings"])
    assert report["recordings"][0]["inspection"] == inspect_falcon_h1(first)
    assert report["recordings"][1]["measurements"]["analyzed_exposure_s"] == 0.08
    assert report["recordings"][0]["measurements"]["observed_spikes_in_windows"] == 3
    assert (
        report["recordings"][0]["measurements"]["raw_eval_mask"]["false_fraction_of_samples"] == 0.5
    )
    assert main(["bci", "inventory-h1", "--manifest", str(manifest)]) == 0
    assert json.loads(capsys.readouterr().out) == report
    assert (_digest(first), _digest(second)) == before


def test_repeated_path_and_duplicate_content_are_errors_not_extra_observations(tmp_path):
    first = _recording(tmp_path / "first.nwb")
    copy = tmp_path / "copy.nwb"
    shutil.copyfile(first, copy)
    manifest = _manifest(tmp_path, [_entry(first, "a"), _entry(first, "b"), _entry(copy, "c")])
    report = inspect_h1_inventory(manifest)
    assert report["complete"] is False
    assert all(row["status"] == "error" for row in report["recordings"])
    assert any("repeated_path" in error for error in report["recordings"][0]["errors"])
    assert all(
        any("duplicate_content" in error for error in row["errors"]) for row in report["recordings"]
    )
    assert report["comparability"]["descriptive_eligible"] is False


def test_identity_collision_and_explicit_conflict_with_nwb_are_reported(tmp_path):
    first = _recording(tmp_path / "first.nwb")
    second = _recording(tmp_path / "second.nwb")
    with h5py.File(second, "r+") as root:
        root["acquisition/eval_mask/data"][0] = False
    manifest = _manifest(tmp_path, [_entry(first, "a"), _entry(second, "b")])
    report = inspect_h1_inventory(manifest)
    assert all(
        any("identity_collision" in error for error in row["errors"])
        for row in report["recordings"]
    )
    identity = _entry(first, "a")["identity"]
    identity["session_day"] = {
        "value": "1925-01-08",
        "provenance": "explicit_user_declaration",
        "evidence": "synthetic research note",
    }
    manifest = _manifest(tmp_path, [_entry(first, "a", identity=identity)])
    report = inspect_h1_inventory(manifest)
    assert report["recordings"][0]["status"] == "error"
    assert "contradicts NWB metadata" in report["recordings"][0]["errors"][0]
    assert report["recordings"][0]["declared_identity"]["session_day"]["value"] == "1925-01-08"
    assert report["recordings"][0]["nwb_identity_observations"]["session_day"] == "1925-01-01"


def test_reordered_unit_ids_do_not_enable_neuron_matching(tmp_path):
    first = _recording(tmp_path / "first.nwb", recording="set-a")
    second = _recording(tmp_path / "second.nwb", recording="set-b")
    with h5py.File(second, "r+") as root:
        root["units/id"][:] = [2, 1]
    report = inspect_h1_inventory(_manifest(tmp_path, [_entry(first, "a"), _entry(second, "b")]))
    assert report["complete"] is True
    assert report["comparability"]["descriptive_eligible"] is True
    assert "not performed" in report["comparability"]["unit_matching"]
    assert [u["unit_id"] for u in report["recordings"][1]["inspection"]["units"]["per_unit"]] == [
        2,
        1,
    ]


def test_labels_units_and_binning_configuration_limits(tmp_path):
    first = _recording(tmp_path / "first.nwb", recording="set-a")
    second = _recording(tmp_path / "second.nwb", recording="set-b")
    with h5py.File(second, "r+") as root:
        root["acquisition/OpenLoopKinematics"].attrs["description"] = "ty,tx,tz,rx,g1,g2,g3"
    report = inspect_h1_inventory(_manifest(tmp_path, [_entry(first, "a"), _entry(second, "b")]))
    assert report["complete"] is False
    assert "seven H1 feature labels" in report["recordings"][1]["errors"][0]
    with h5py.File(second, "r+") as root:
        root["acquisition/OpenLoopKinematics"].attrs["description"] = "tx,ty,tz,rx,g1,g2,g3"
        root["acquisition/OpenLoopKinematics/data"].attrs["unit"] = "different"
    report = inspect_h1_inventory(_manifest(tmp_path, [_entry(first, "a"), _entry(second, "b")]))
    assert report["complete"] is True
    assert report["comparability"]["descriptive_eligible"] is False
    assert "feature labels/order or storage units differ" in report["comparability"]["reasons"]
    changed = _entry(second, "b")
    changed["binning"] = {**SUPPORTED_BINNING, "period_s": 0.01}
    report = inspect_h1_inventory(_manifest(tmp_path, [_entry(first, "a"), changed]))
    assert report["complete"] is False
    assert "20 ms" in report["recordings"][1]["errors"][0]
    changed = _entry(second, "b")
    changed["measurement"] = {**SUPPORTED_MEASUREMENT, "unit_rate_denominator": "mask_true_only"}
    report = inspect_h1_inventory(_manifest(tmp_path, [_entry(first, "a"), changed]))
    assert report["complete"] is False
    assert "measurement configuration" in report["recordings"][1]["errors"][0]


def test_partial_failure_zero_exposure_and_safe_cli_output(tmp_path, capsys):
    first = _recording(tmp_path / "first.nwb")
    missing = tmp_path / "missing.nwb"
    bad = {**_entry(first, "b"), "path": str(missing)}
    manifest = _manifest(tmp_path, [_entry(first, "a"), bad])
    report = inspect_h1_inventory(manifest)
    assert report["complete"] is False
    assert report["recordings"][0]["status"] == "ok"
    assert report["recordings"][1]["status"] == "error"
    assert main(["bci", "inventory-h1", "--manifest", str(manifest)]) == 1
    assert json.loads(capsys.readouterr().out) == report
    assert describe_h1_timing([])["analyzed_exposure_s"] == 0
    empty = _recording(tmp_path / "empty.nwb", samples=0)
    manifest = _manifest(tmp_path, [_entry(first, "a"), _entry(empty, "b")])
    report = inspect_h1_inventory(manifest)
    assert report["complete"] is False
    assert "nonempty" in report["recordings"][1]["errors"][0]
    assert main(["bci", "inventory-h1", "--manifest", str(manifest), "--output", str(first)]) == 1
    assert "must not replace" in capsys.readouterr().err
    assert (
        main(["bci", "inventory-h1", "--manifest", str(manifest), "--output", str(manifest)]) == 1
    )
    assert "must not replace" in capsys.readouterr().err
    output = tmp_path / "private-report.json"
    assert main(["bci", "inventory-h1", "--manifest", str(manifest), "--output", str(output)]) == 1
    assert json.loads(output.read_text())["complete"] is False
    assert main(["bci", "inventory-h1", "--manifest", str(manifest), "--output", str(output)]) == 1
    assert _digest(first) == _entry(first, "a")["sha256"]


def test_missing_nwb_identity_remains_unknown_and_provenance_is_preserved(tmp_path):
    first = _recording(tmp_path / "first.nwb")
    with h5py.File(first, "r+") as root:
        del root["identifier"]
        del root["session_start_time"]
    identity = _entry(first, "a")["identity"]
    identity["benchmark_partition"] = {
        "value": "held-in-minival",
        "provenance": "documented_upstream_mapping",
        "evidence": "https://example.test/pinned-asset",
    }
    report = inspect_h1_inventory(_manifest(tmp_path, [_entry(first, "a", identity=identity)]))
    assert report["complete"] is True
    chosen = report["recordings"][0]["identity"]
    assert chosen["participant"] is None
    assert chosen["session_day"] is None
    assert chosen["recording_set"] is None
    assert chosen["benchmark_partition"]["provenance"] == "documented_upstream_mapping"
    assert report["comparability"]["identity_unknown_by_entry"]["a"] == [
        "participant",
        "session_day",
        "recording_set",
    ]


def test_nonfinite_fractions_keep_mask_denominators_explicit(tmp_path):
    first = _recording(tmp_path / "first.nwb")
    with h5py.File(first, "r+") as root:
        data = root["acquisition/OpenLoopKinematics/data"]
        data[0, 0] = np.nan  # raw mask true
        data[1, 0] = np.inf  # raw mask false
    report = inspect_h1_inventory(_manifest(tmp_path, [_entry(first, "a")]))
    tx = report["recordings"][0]["measurements"]["nonfinite_covariates"]["position"]["tx"]
    assert tx == {
        "count": 2,
        "fraction_of_all_samples": pytest.approx(2 / 6),
        "raw_mask_true_count": 1,
        "fraction_of_raw_mask_true_samples": pytest.approx(1 / 3),
        "raw_mask_false_count": 1,
        "fraction_of_raw_mask_false_samples": pytest.approx(1 / 3),
    }
    with h5py.File(first, "r") as root:
        assert np.isnan(root["acquisition/OpenLoopKinematics/data"][0, 0])
        assert np.isinf(root["acquisition/OpenLoopKinematics/data"][1, 0])


def test_absent_mask_status_has_null_fraction_not_zero(tmp_path):
    first = _recording(tmp_path / "first.nwb")
    with h5py.File(first, "r+") as root:
        root["acquisition/eval_mask/data"][:] = True
        root["acquisition/OpenLoopKinematics/data"][0, 0] = np.nan
    row = inspect_h1_inventory(_manifest(tmp_path, [_entry(first, "a")]))["recordings"][0]
    tx = row["measurements"]["nonfinite_covariates"]["position"]["tx"]
    assert tx["fraction_of_raw_mask_true_samples"] == pytest.approx(1 / 6)
    assert tx["fraction_of_raw_mask_false_samples"] is None
    assert row["measurements"]["raw_eval_mask"]["false_fraction_of_samples"] == 0


def test_import_isolation(tmp_path):
    import subprocess
    import sys

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import dmri_studio.core.bci_inventory; import dmri_studio.cli.bci; "
            "print(any(x.startswith(('PySide6', 'vtkmodules')) for x in sys.modules), 'h5py' in sys.modules)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert proc.stdout.strip() == "False False"
