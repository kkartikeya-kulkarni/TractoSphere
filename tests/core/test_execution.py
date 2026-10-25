"""Meaningful execution safeguards and real MRtrix acceptance on tiny phantoms.

The real adapter checks skip only when the tested MRtrix release is unavailable.
No HCP data, previous result or input-side write is used by these tests.
"""

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core import execution
from dmri_studio.core._mif_checks import rearrange, voxel_mapping
from dmri_studio.core.dataset import DatasetPaths, SourceChangedError, SourceFingerprint
from dmri_studio.core.execution import (
    ExecutionPlan,
    detect_mrtrix,
    execute_plan,
    hash_file,
    prepare_execution,
    record_qc,
    save_execution_plan,
)
from dmri_studio.core.loading import DatasetLoader
from dmri_studio.core.process import ProcessingCancelled, run_command
from dmri_studio.core.workflow import HistoryClaim, ProcessingHistory


@pytest.fixture
def phantom(tmp_path):
    """A reproducible brain-shaped signal with background, not an anatomical truth."""
    source = tmp_path / "source"
    source.mkdir()
    xyz = np.indices((32, 34, 36), dtype=np.float32)
    radius = sum(((xyz[i] - (n - 1) / 2) / 10) ** 2 for i, n in enumerate(xyz.shape[1:]))
    brain = radius < 1
    data = np.stack(
        [np.where(brain, 1000 / (i + 1) + radius * 30, 0) for i in range(13)], axis=3
    ).astype(np.float32)
    affine = np.diag([-1.25, 1.5, 2, 1])
    affine[0, 3] = 40
    image = nib.Nifti1Image(data, affine)
    image.header.set_xyzt_units("mm")
    nib.save(image, source / "data.nii")
    np.savetxt(source / "bvals", np.array([[5] + [1000] * 6 + [2000] * 6]), fmt="%g")
    np.savetxt(source / "bvecs", np.column_stack(([1, 0, 0], np.tile(np.eye(3), (1, 4)))), fmt="%g")
    loaded = DatasetLoader().load(
        DatasetPaths(source / "data.nii", source / "bvals", source / "bvecs")
    )
    assert loaded.dataset is not None
    with loaded.dataset as dataset:
        yield dataset


@pytest.fixture
def history():
    """Explicit post-interpolation history permits independent masking only."""
    return ProcessingHistory(
        (
            HistoryClaim(
                "interpolation",
                True,
                "user_declared",
                "Synthetic test record",
                "high",
                "2026-10-10",
            ),
        ),
        True,
    )


@pytest.fixture
def mrtrix():
    """Real executable/version checks are optional outside this local acceptance."""
    if not shutil.which("mrconvert") or not shutil.which("dwi2mask"):
        pytest.skip("MRtrix unavailable")
    try:
        return detect_mrtrix()
    except ValueError:
        pytest.skip("Tested MRtrix release unavailable")


@pytest.fixture
def fake_tools(monkeypatch):
    """Safety tests need not launch a real scientific command."""
    path = Path(sys.executable).resolve()
    tools = {
        name: {
            "path": str(path),
            "version": "3.0.5",
            "version_output": "synthetic",
            "fingerprint": SourceFingerprint.capture(path).to_dict(),
            "sha256": hash_file(path),
        }
        for name in ("mrconvert", "dwi2mask")
    }
    monkeypatch.setattr(execution, "detect_mrtrix", lambda **_: tools)
    return tools


def test_preview_is_exact_immutable_and_does_not_run(phantom, history, fake_tools, tmp_path):
    """No derivative folder until approval; all scientific options are visible."""
    output = tmp_path / "new run"
    plan = prepare_execution(phantom, history, ("mask",), output)
    assert not output.exists()
    payload = plan.to_dict()
    assert [c["step"] for c in payload["commands"]] == ["convert", "mask", "export_mask"]
    assert "dwidenoise" not in plan.to_json() and "mrdegibbs" not in plan.to_json()
    assert all("-force" not in c["argv"] for c in payload["commands"])
    assert payload["gradient_staging"]["bvals"][0] == 5
    payload["commands"][0]["argv"].append("-force")
    assert "-force" not in plan.to_json()
    with pytest.raises(TypeError):
        plan.payload["approved_for_execution"] = True
    saved = save_execution_plan(plan, tmp_path / "preview.json")
    assert json.loads(saved.read_text())["approval_digest"] == plan.digest
    with pytest.raises(FileExistsError):
        save_execution_plan(plan, saved)


@pytest.mark.parametrize(
    "parameter,value",
    [("clean_scale", -1), ("clean_scale", np.nan), ("nthreads", 0), ("nthreads", True)],
)
def test_invalid_parameters_refused(phantom, history, tmp_path, parameter, value):
    """Invalid scientific/execution parameters never reach tool detection."""
    with pytest.raises(ValueError):
        prepare_execution(phantom, history, ("mask",), tmp_path / "run", **{parameter: value})


def test_output_and_history_constraints(phantom, history, fake_tools, tmp_path):
    """Preserve input directory and refuse repeats/unknown or ineligible proposals."""
    with pytest.raises(ValueError, match="outside"):
        prepare_execution(phantom, history, ("mask",), phantom.paths.dwi.parent / "run")
    with pytest.raises(ValueError, match="already exists"):
        prepare_execution(phantom, history, ("mask",), tmp_path)
    with pytest.raises(ValueError):
        prepare_execution(phantom, ProcessingHistory(), ("mask",), tmp_path / "run")
    with pytest.raises(ValueError):
        prepare_execution(phantom, history, ("denoise",), tmp_path / "run")


def test_approval_and_command_tampering_refused(phantom, history, fake_tools, tmp_path):
    """A JSON file cannot authorize arbitrary argv, even with a recalculated digest."""
    plan = prepare_execution(phantom, history, ("convert",), tmp_path / "run")
    with pytest.raises(ValueError, match="Approve"):
        execute_plan(phantom, plan, approval="wrong")
    forged = plan.to_dict()
    forged["commands"][0]["argv"] = [sys.executable, "-c", "raise SystemExit(0)"]
    altered = ExecutionPlan(forged)
    with pytest.raises(ValueError, match="changed"):
        execute_plan(phantom, altered, approval=altered.digest)
    assert not (tmp_path / "run").exists()


def test_content_change_with_restored_mtime_invalidates_approval(
    phantom, history, fake_tools, tmp_path
):
    """A same-size companion edit is blocked before native execution."""
    plan = prepare_execution(phantom, history, ("convert",), tmp_path / "run")
    path = phantom.paths.bval
    before = path.stat()
    path.write_text(path.read_text().replace("1000", "1001", 1))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(SourceChangedError, match="changed"):
        execute_plan(phantom, plan, approval=plan.digest)
    assert not (tmp_path / "run").exists()


def test_binary_change_invalidates_approval(phantom, history, fake_tools, tmp_path, monkeypatch):
    """The approved scientific implementation is the exact binary, not a name."""
    plan = prepare_execution(phantom, history, ("convert",), tmp_path / "run")
    tools = json.loads(json.dumps(fake_tools))
    tools["mrconvert"]["sha256"] = "changed"
    monkeypatch.setattr(execution, "detect_mrtrix", lambda **_: tools)
    with pytest.raises(ValueError, match="changed"):
        execute_plan(phantom, plan, approval=plan.digest)


def test_failed_and_cancelled_runs_keep_incomplete_provenance(
    phantom, history, fake_tools, tmp_path, monkeypatch
):
    """Success is never inferred from an existing or partially written output."""

    def fail(*args, **kwargs):
        """Simulate a tool error after run creation without imaging writes."""
        raise RuntimeError("synthetic tool failure")

    monkeypatch.setattr(execution, "run_command", fail)
    plan = prepare_execution(phantom, history, ("mask",), tmp_path / "failed")
    result = execute_plan(phantom, plan, approval=plan.digest)
    assert result["status"] == "failed" and result["inputs_unchanged"] is True
    assert result["steps"][0]["status"] == "failed"
    with pytest.raises(ValueError):
        record_qc(tmp_path / "failed", "accepted", "reason", reviewer="tester")

    def cancel(*args, **kwargs):
        """Simulate cooperative cancellation inside the command runner."""
        raise ProcessingCancelled("synthetic cancellation")

    monkeypatch.setattr(execution, "run_command", cancel)
    plan = prepare_execution(phantom, history, ("mask",), tmp_path / "cancelled")
    result = execute_plan(phantom, plan, approval=plan.digest)
    assert result["status"] == "cancelled" and result["inputs_unchanged"] is True


def test_quiet_subprocess_cancellation_is_prompt(tmp_path):
    """Cancellation does not depend on a blocked readline or a tool progress line."""
    cancel = threading.Event()
    timer = threading.Timer(0.15, cancel.set)
    timer.start()
    started = time.monotonic()
    with pytest.raises(ProcessingCancelled):
        run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            log=tmp_path / "cancel.log",
            environment=os.environ,
            cancelled=cancel.is_set,
            progress=lambda _: None,
        )
    timer.join()
    assert time.monotonic() - started < 5


def test_process_error_and_verbose_output(tmp_path):
    """Disk logs retain complete output even when the UI receives bounded updates."""
    log = tmp_path / "error.log"
    with pytest.raises(RuntimeError, match="exited 7"):
        run_command(
            [sys.executable, "-c", "print('x' * 100000); raise SystemExit(7)"],
            cwd=tmp_path,
            log=log,
            environment=os.environ,
            cancelled=lambda: False,
            progress=lambda _: None,
        )
    assert log.stat().st_size >= 100000


def test_geometry_mapping_permutations_and_translation():
    """Equal dimensions alone must never hide a shifted or resampled mask."""
    affine = np.eye(4)
    target = np.array([[0, -1, 0, 4], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    assert voxel_mapping(affine, (5, 6, 7), target, (6, 5, 7)) == ((1, 0, 2), (1,))
    target[0, 3] = 3
    with pytest.raises(ValueError):
        voxel_mapping(affine, (5, 6, 7), target, (6, 5, 7))


def test_native_reindexing_preserves_all_4d_volume_values():
    """Spatial permutation/flips must not omit or reorder the temporal axis.

    Independent coordinate labels encode native i/j/k and volume identity.
    This guards the case-export error where only three transpose axes were
    supplied to a four-dimensional corrected diffusion image.
    """
    native_shape = (2, 3, 4, 5)
    i, j, k, volume = np.indices(native_shape)
    expected = 1000 * volume + 100 * i + 10 * j + k
    # Source x is native j, source y is reversed native i, z/volume unchanged.
    x, y, z, t = np.indices((3, 2, 4, 5))
    stored = 1000 * t + 100 * (1 - y) + 10 * x + z
    source_affine = np.array([[0, -1, 0, 1], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    axes, flips = voxel_mapping(source_affine, stored.shape, np.eye(4), native_shape)
    result = rearrange(stored, axes + (3,), flips)
    assert np.array_equal(result, expected)
    assert result.shape == native_shape
    assert np.array_equal(result[1, 2, 3, :], [123, 1123, 2123, 3123, 4123])


def test_real_mrtrix_conversion_mask_and_qc(phantom, history, mrtrix, tmp_path):
    """Test the actual pinned adapter, low-b retention, voxel checks and human receipt."""
    before = {role: hash_file(path) for role, path in phantom.paths.supplied().items()}
    plan = prepare_execution(phantom, history, ("mask",), tmp_path / "real run", nthreads=1)
    result = execute_plan(phantom, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    assert result["validation"]["conversion"]["values_compared"] == np.prod(phantom.shape)
    assert result["validation"]["conversion"]["bvalues_preserved"] is True
    mask = nib.load(tmp_path / "real run" / "mask.nii.gz")
    assert mask.shape == phantom.shape[:3]
    assert np.allclose(mask.affine, phantom.affine)
    assert np.array_equal(np.unique(mask.dataobj), [0, 1])
    assert result["inputs_unchanged"] is True
    assert before == {role: hash_file(path) for role, path in phantom.paths.supplied().items()}
    saved = record_qc(
        tmp_path / "real run",
        "rejected",
        "Synthetic fixture; not an anatomical validation",
        reviewer="Automated acceptance test",
    )
    assert json.loads(saved.read_text())["decision"] == "rejected"
    with pytest.raises(FileExistsError):
        record_qc(tmp_path / "real run", "accepted", "second decision", reviewer="tester")
    with pytest.raises(ValueError):
        execute_plan(phantom, plan, approval=plan.digest)


def test_real_conversion_only_has_no_mask(phantom, history, mrtrix, tmp_path):
    """The executor must not add an unselected scientific operation."""
    plan = prepare_execution(phantom, history, ("convert",), tmp_path / "convert only", nthreads=1)
    result = execute_plan(phantom, plan, approval=plan.digest)
    assert result["status"] == "validated", result.get("error")
    assert set(result["outputs"]) == {"dwi.mif"}


@pytest.mark.parametrize("case", ["oblique", "permuted", "scaled_integer", "three_Nx3"])
def test_real_conversion_general_geometry_scaling_and_layout(
    phantom, history, mrtrix, tmp_path, case
):
    """Exercise independent fragile format/convention cases, not just the HCP axes."""
    image = nib.load(phantom.paths.dwi)
    data = np.asarray(image.dataobj).copy()
    affine = image.affine.copy()
    layout = "auto"
    if case == "oblique":
        angle = 0.25
        rotation = np.array(
            [[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]]
        )
        affine[:3, :3] = rotation @ affine[:3, :3]
    elif case == "permuted":
        affine[:3, :3] = affine[:3, (1, 2, 0)]
    elif case == "scaled_integer":
        data = data.astype(np.int16)
    else:
        data = data[..., :3]
        phantom.paths.bval.write_text("5 1000 1000")
        np.savetxt(phantom.paths.bvec, [[1, 0, 0], [0, 0, 1], [1, 0, 0]])
        layout = "Nx3"  # 3x3 is deliberately ambiguous; MRtrix alone chooses rows.
    changed = nib.Nifti1Image(data, affine)
    changed.header.set_xyzt_units("mm")
    if case == "scaled_integer":
        changed.header.set_slope_inter(0.5, -2)
    nib.save(changed, phantom.paths.dwi)
    from dmri_studio.core.validation import ValidationPolicy

    loaded = DatasetLoader().load(phantom.paths, policy=ValidationPolicy(bvec_layout=layout))
    assert loaded.dataset is not None
    with loaded.dataset as dataset:
        plan = prepare_execution(dataset, history, ("convert",), tmp_path / case, nthreads=1)
        result = execute_plan(dataset, plan, approval=plan.digest)
        assert result["status"] == "validated", result.get("error")
        assert result["validation"]["conversion"]["unequal_count"] == 0


def test_corrupt_conversion_stops_before_masking(phantom, history, mrtrix, tmp_path, monkeypatch):
    """Exit zero cannot let a changed signal enter the next scientific operation."""
    from dmri_studio.core.process import run_command as real_run

    calls = []

    def corrupt(argv, **kwargs):
        """Run a real conversion, then introduce one simulated disk-output fault."""
        calls.append(Path(argv[0]).name)
        result = real_run(argv, **kwargs)
        path = tmp_path / "corrupt" / "dwi.mif"
        with path.open("r+b") as handle:
            handle.seek(-4, 2)
            handle.write(np.array([np.nan], dtype="<f4").tobytes())
        return result

    monkeypatch.setattr(execution, "run_command", corrupt)
    plan = prepare_execution(phantom, history, ("mask",), tmp_path / "corrupt", nthreads=1)
    result = execute_plan(phantom, plan, approval=plan.digest)
    assert result["status"] == "failed"
    assert calls == ["mrconvert"]
    assert not (tmp_path / "corrupt" / "mask.mif").exists()
    assert result["steps"][0]["return_code"] == 0
    assert result["inputs_unchanged"] is True
