"""Strict headless preview/approval and executable CLI parity on synthetic inputs."""

import json
import shutil

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.cli.main import main
from dmri_studio.core.execution import detect_mrtrix
from dmri_studio.core.workflow import ProcessingHistory


@pytest.fixture
def args(tmp_path):
    """Prepare a tiny DWI and explicitly confirmed unknown history."""
    source = tmp_path / "source"
    source.mkdir()
    image = nib.Nifti1Image(
        np.arange(5 * 6 * 7 * 4, dtype=np.float32).reshape(5, 6, 7, 4), np.eye(4)
    )
    image.header.set_xyzt_units("mm")
    nib.save(image, source / "data.nii")
    (source / "bvals").write_text("5 1000 1000 1000")
    (source / "bvecs").write_text("1 1 0 0\n0 0 1 0\n0 0 0 1")
    history = tmp_path / "history.json"
    history.write_text(json.dumps(ProcessingHistory(confirmed=True).to_dict()))
    return [
        "prepare",
        "--dwi",
        str(source / "data.nii"),
        "--bval",
        str(source / "bvals"),
        "--bvec",
        str(source / "bvecs"),
        "--history",
        str(history),
        "--operation",
        "convert",
        "--output",
        str(tmp_path / "run"),
        "--execution-plan",
        str(tmp_path / "execution.json"),
    ]


def test_cli_prepare_then_separate_approval_and_execute(args, tmp_path, capsys):
    """A real CLI execute validates the same physical conversion as the Python API."""
    if not shutil.which("mrconvert"):
        pytest.skip("MRtrix unavailable")
    try:
        detect_mrtrix()
    except ValueError:
        pytest.skip("Tested release unavailable")
    assert main(args) == 0
    assert not (tmp_path / "run").exists()
    document = json.loads((tmp_path / "execution.json").read_text())
    assert document["plan"]["approved_for_execution"] is False
    assert main(["execute", "--plan", str(tmp_path / "execution.json"), "--approve", "wrong"]) == 2
    assert not (tmp_path / "run").exists()
    assert (
        main(
            [
                "execute",
                "--plan",
                str(tmp_path / "execution.json"),
                "--approve",
                document["approval_digest"],
            ]
        )
        == 0
    )
    result = json.loads((tmp_path / "run" / "manifest.json").read_text())
    assert result["status"] == "validated" and result["inputs_unchanged"] is True
    assert "Review the plan" in capsys.readouterr().out


def test_cli_requires_approval_and_strict_plan_json(tmp_path):
    """No implicit yes, duplicate keys, fabricated receipt or NaN plan."""
    assert main(["execute", "--plan", str(tmp_path / "missing.json")]) == 2
    path = tmp_path / "bad.json"
    path.write_text('{"plan":{},"plan":{},"approval_digest":"x"}')
    assert main(["execute", "--plan", str(path), "--approve", "x"]) == 2
    path.write_text('{"plan":NaN,"approval_digest":"x"}')
    assert main(["execute", "--plan", str(path), "--approve", "x"]) == 2


def test_project_cli_round_trip_and_context(args, tmp_path, capsys):
    """CLI snapshots and loading context preserve explicit identity and unknown history."""
    first, second = tmp_path / "project.json", tmp_path / "project-002.json"
    assert main(["project", "new", "--name", "Research", "--output", str(first)]) == 0
    spec = tmp_path / "acquisition.json"
    spec.write_text(
        json.dumps(
            {
                "paths": {
                    role: args[args.index("--" + role) + 1] for role in ("dwi", "bval", "bvec")
                },
                "identity": {"sub": "synthetic", "ses": "01", "run": "02"},
                "history": ProcessingHistory().to_dict(),
            }
        )
    )
    assert (
        main(
            [
                "project",
                "add",
                "--project",
                str(first),
                "--acquisition",
                str(spec),
                "--output",
                str(second),
            ]
        )
        == 0
    )
    project = json.loads(second.read_text())
    identifier = project["acquisitions"][0]["id"]
    assert not json.loads(first.read_text())["acquisitions"]
    capsys.readouterr()
    assert (
        main(["project", "context", "--project", str(second), "--acquisition-id", identifier]) == 0
    )
    context = json.loads(capsys.readouterr().out)
    assert context["project_binding"]["acquisition_id"] == identifier
    assert not context["product"]["history"]["claims"]
