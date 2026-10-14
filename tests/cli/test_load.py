"""Exercise CLI behavior using tiny temporary images, never the real HCP subject.

Run ``python -m pytest tests/cli/test_load.py`` after installing the package in
editable mode. The subprocess test runs from an unrelated folder to verify the
installed entry point rather than accidentally relying on the repository path.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.cli.main import main
from dmri_studio.core.dataset import DatasetPaths
from dmri_studio.core.loading import DatasetLoader
from dmri_studio.core.validation import ValidationReport

cli_module = importlib.import_module("dmri_studio.cli.main")


@pytest.fixture
def cli_inputs(tmp_path: Path) -> dict[str, Path]:
    """Create a valid four-volume image with matching b0/weighted gradients."""
    image = nib.Nifti1Image(np.ones((6, 7, 8, 4), dtype=np.float32), np.eye(4))
    image.header.set_xyzt_units("mm")
    image.set_qform(np.eye(4), code=1)
    image.set_sform(np.eye(4), code=1)
    paths = {"dwi": tmp_path / "tiny.nii", "bval": tmp_path / "bvals", "bvec": tmp_path / "bvecs"}
    nib.save(image, paths["dwi"])
    paths["bval"].write_text("0 1000 1000 2000\n", encoding="utf-8")
    paths["bvec"].write_text("0 1 0 0\n0 0 1 0\n0 0 0 1\n", encoding="utf-8")
    return paths


def load_args(paths: dict[str, Path], *extra: str) -> list[str]:
    """Build explicit file arguments; no folder-layout guessing belongs here."""
    return [
        "load",
        "--dwi",
        str(paths["dwi"]),
        "--bval",
        str(paths["bval"]),
        "--bvec",
        str(paths["bvec"]),
        *extra,
    ]


def test_success_prints_summary_all_checks_and_preserves_inputs(
    cli_inputs: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A completed CLI load prints its bounded coverage and leaves files intact."""
    before = {name: path.read_bytes() for name, path in cli_inputs.items()}
    assert main(load_args(cli_inputs)) == 0
    output = capsys.readouterr().out
    assert "Dataset loaded" in output
    assert "6 × 7 × 8 × 4" in output
    assert "b0-like: 1" in output
    assert "diffusion-weighted: 3" in output
    assert "full voxel scan not performed" in output
    for code in ("F01", "F05", "N01", "A01", "G01", "J01", "V03", "C03"):
        assert f"] {code}:" in output
    assert {name: path.read_bytes() for name, path in cli_inputs.items()} == before


def test_count_mismatch_exits_one_with_actionable_failure(
    cli_inputs: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI preserves core validation failures rather than raising tracebacks."""
    cli_inputs["bval"].write_text("0 1000\n", encoding="utf-8")
    assert main(load_args(cli_inputs)) == 1
    result = capsys.readouterr()
    assert "Could not load dataset" in result.out
    assert "[FAIL] G02" in result.out
    assert "Traceback" not in result.err


def test_missing_image_does_not_claim_sample_coverage(
    cli_inputs: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Even a fully evaluated failure must distinguish unperformed voxel reads."""
    cli_inputs["dwi"] = cli_inputs["dwi"].with_name("missing.nii")
    assert main(load_args(cli_inputs)) == 1
    output = capsys.readouterr().out
    assert "voxel samples were not read" in output
    assert "Metadata and sampled voxels checked" not in output


def test_warning_is_loadable_and_printed(
    cli_inputs: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing spatial units deserve attention without blocking inspection."""
    image = nib.load(cli_inputs["dwi"])
    data = np.asarray(image.dataobj).copy()
    changed = nib.Nifti1Image(data, image.affine)
    changed.header.set_xyzt_units("unknown")
    nib.save(changed, cli_inputs["dwi"])
    assert main(load_args(cli_inputs)) == 0
    output = capsys.readouterr().out
    assert "Dataset loaded with warnings" in output
    assert "[WARN] N04" in output
    assert "millimetres unavailable" in output


def test_negative_samples_and_gradient_deviation_are_info(
    cli_inputs: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ordinary preprocessed negatives and unused grad_dev do not become warnings."""
    image = nib.load(cli_inputs["dwi"])
    changed = nib.Nifti1Image(-np.ones(image.shape, dtype=np.float32), image.affine)
    changed.header.set_xyzt_units("mm")
    changed.set_qform(np.eye(4), code=1)
    changed.set_sform(np.eye(4), code=1)
    nib.save(changed, cli_inputs["dwi"])
    # Deliberately not a NIfTI: presence checks must not attempt to parse it.
    (cli_inputs["dwi"].parent / "grad_dev.nii").write_text("presence only", encoding="utf-8")
    assert main(load_args(cli_inputs)) == 0
    output = capsys.readouterr().out
    assert "[INFO] V03" in output
    assert "[INFO] F05" in output
    assert "[WARN] V03" not in output
    assert "grad_dev.nii" in output


def test_optional_malformed_json_fails_without_traceback(
    cli_inputs: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Selected malformed JSON is an explicit user-fixable input error."""
    sidecar = tmp_path / "invalid.json"
    sidecar.write_text('{"EchoTime":', encoding="utf-8")
    assert main(load_args(cli_inputs, "--json", str(sidecar))) == 1
    output = capsys.readouterr()
    assert "[FAIL] J01" in output.out
    assert "Traceback" not in output.err


def test_json_report_preserves_core_check_decisions(
    cli_inputs: dict[str, Path],
    tmp_path: Path,
) -> None:
    """Human output and exported evidence use the same core report policy."""
    report_path = tmp_path / "report.json"
    assert main(load_args(cli_inputs, "--report-json", str(report_path))) == 0
    exported = json.loads(report_path.read_text(encoding="utf-8"))
    result = DatasetLoader().load(DatasetPaths(**cli_inputs))
    try:
        expected = result.report.to_dict()
        assert exported["checks"] == expected["checks"]
        assert exported["policy"] == expected["policy"]
        assert exported["coverage"] == "metadata_and_samples"
        assert exported["can_load"] is True
    finally:
        if result.dataset is not None:
            result.dataset.close()


@pytest.mark.parametrize("role", ("dwi", "bval", "bvec"))
def test_report_cannot_overwrite_input(
    cli_inputs: dict[str, Path],
    role: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report export is never allowed to replace any imaging/gradient source."""
    target = cli_inputs[role]
    before = target.read_bytes()
    assert main(load_args(cli_inputs, "--report-json", str(target))) == 2
    assert target.read_bytes() == before
    assert "input file" in capsys.readouterr().err


def test_report_cannot_overwrite_selected_sidecar(
    cli_inputs: dict[str, Path],
    tmp_path: Path,
) -> None:
    """Optional metadata receives the same protection as required sources."""
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text("{}\n", encoding="utf-8")
    assert main(load_args(cli_inputs, "--json", str(sidecar), "--report-json", str(sidecar))) == 2
    assert sidecar.read_text(encoding="utf-8") == "{}\n"


@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_report_rejects_aliases_to_inputs(
    cli_inputs: dict[str, Path],
    tmp_path: Path,
    link_kind: str,
) -> None:
    """Resolve both pathname aliases and shared-inode aliases before export."""
    alias = tmp_path / "alias.json"
    try:
        if link_kind == "symlink":
            alias.symlink_to(cli_inputs["bval"])
        else:
            os.link(cli_inputs["bval"], alias)
    except OSError as exc:
        pytest.skip(f"This filesystem cannot create {link_kind}: {exc}")
    before = cli_inputs["bval"].read_bytes()
    assert main(load_args(cli_inputs, "--report-json", str(alias))) == 2
    assert cli_inputs["bval"].read_bytes() == before


def test_existing_report_is_not_silently_replaced(
    cli_inputs: dict[str, Path],
    tmp_path: Path,
) -> None:
    """An unrelated existing report also requires a new destination name."""
    report = tmp_path / "existing.json"
    report.write_text("retain me", encoding="utf-8")
    assert main(load_args(cli_inputs, "--report-json", str(report))) == 2
    assert report.read_text(encoding="utf-8") == "retain me"


def test_report_destination_race_does_not_overwrite(
    cli_inputs: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Atomic no-replacement publication still protects files created mid-load."""
    destination = tmp_path / "race.json"
    original_link = cli_module.os.link

    def competing_link(source: Any, target: Any, **kwargs: Any) -> None:
        """Simulate a second process publishing the destination just before us."""
        Path(target).write_text("other process", encoding="utf-8")
        original_link(source, target, **kwargs)

    monkeypatch.setattr(cli_module.os, "link", competing_link)
    assert main(load_args(cli_inputs, "--report-json", str(destination))) == 1
    assert destination.read_text(encoding="utf-8") == "other process"
    assert not list(tmp_path.glob(".race.json.*.tmp"))


def test_ctrl_c_returns_130_without_traceback(
    cli_inputs: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keyboard cancellation is distinguishable from a malformed dataset."""

    def interrupt(*args: Any, **kwargs: Any) -> Any:
        """Stand in for Ctrl-C during a loader read."""
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module.DatasetLoader, "load", interrupt)
    assert main(load_args(cli_inputs)) == 130
    error = capsys.readouterr().err
    assert "cancelled" in error
    assert "Traceback" not in error


def test_cancelled_core_result_returns_130(
    cli_inputs: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cooperative core cancellation is not accidentally reported as success."""
    result = SimpleNamespace(
        dataset=None, report=ValidationReport(checks=(), complete=False, cancelled=True)
    )
    monkeypatch.setattr(cli_module.DatasetLoader, "load", lambda *args, **kwargs: result)
    assert main(load_args(cli_inputs)) == 130


def test_unexpected_exception_logs_diagnostic(
    cli_inputs: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unexpected implementation errors retain technical diagnostic information."""

    def broken(*args: Any, **kwargs: Any) -> Any:
        """Produce an unexpected technical failure rather than bad input."""
        raise RuntimeError("test-only diagnostic")

    monkeypatch.setattr(cli_module.DatasetLoader, "load", broken)
    assert main(load_args(cli_inputs)) == 1
    assert "Unexpected loader error" in caplog.text
    assert "test-only diagnostic" in caplog.text


def test_usage_version_and_gui_help(capsys: pytest.CaptureFixture[str]) -> None:
    """Only implemented commands are advertised; ordinary usage exits are stable."""
    assert main(["--version"]) == 0
    assert "0.1.0" in capsys.readouterr().out
    assert main(["load"]) == 2
    assert "required" in capsys.readouterr().err
    assert main(["gui", "--help"]) == 0
    assert "gui" in capsys.readouterr().out


def test_installed_module_runs_from_unrelated_directory(
    cli_inputs: dict[str, Path],
    tmp_path: Path,
) -> None:
    """Exercise real argument parsing/process exit without a PYTHONPATH workaround."""
    completed = subprocess.run(
        [sys.executable, "-m", "dmri_studio", *load_args(cli_inputs)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Dataset loaded" in completed.stdout
    assert "full voxel scan not performed" in completed.stdout


def test_explicit_gradient_layout_is_forwarded_to_core(
    cli_inputs: dict[str, Path],
    tmp_path: Path,
) -> None:
    """The layout switch is an actual core policy override, not just a UI label."""
    cli_inputs["bvec"].write_text("0 0 0\n1 0 0\n0 1 0\n0 0 1\n", encoding="utf-8")
    destination = tmp_path / "layout-report.json"
    assert (
        main(load_args(cli_inputs, "--bvec-layout", "Nx3", "--report-json", str(destination))) == 0
    )
    report = json.loads(destination.read_text(encoding="utf-8"))
    assert report["policy"]["bvec_layout"] == "Nx3"
    assert not any(check["status"] == "fail" for check in report["checks"])


def test_failed_validation_can_export_its_report(
    cli_inputs: dict[str, Path],
    tmp_path: Path,
) -> None:
    """Invalid datasets still produce evidence that a user can inspect later."""
    cli_inputs["bval"].write_text("0 1000\n", encoding="utf-8")
    destination = tmp_path / "failed-report.json"
    assert main(load_args(cli_inputs, "--report-json", str(destination))) == 1
    report = json.loads(destination.read_text(encoding="utf-8"))
    assert report["can_load"] is False
    assert any(check["code"] == "G02" and check["status"] == "fail" for check in report["checks"])


def test_gui_command_dispatches_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only explicit gui dispatch enters the optional desktop entry point."""
    monkeypatch.setitem(sys.modules, "dmri_studio.gui.app", SimpleNamespace(main=lambda: 7))
    assert main(["gui"]) == 7
