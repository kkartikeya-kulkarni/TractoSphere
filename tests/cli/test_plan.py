"""CLI history input, inspection parity and non-executing proposal export."""

import json
import subprocess

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.cli.main import main
from dmri_studio.core.workflow import STAGES, HistoryClaim, ProcessingHistory


@pytest.fixture
def inputs(tmp_path):
    """Create a tiny inspectable DWI and an explicit declared raw history."""
    image = nib.Nifti1Image(np.ones((5, 6, 7, 4), np.float32), np.eye(4))
    image.header.set_xyzt_units("mm")
    nib.save(image, tmp_path / "data.nii")
    (tmp_path / "bvals").write_text("0 1000 2000 3000")
    (tmp_path / "bvecs").write_text("0 1 0 0\n0 0 1 0\n0 0 0 1")
    history = ProcessingHistory(
        tuple(
            HistoryClaim(
                stage, False, "user_declared", "Test acquisition record", "medium", "2026-10-10"
            )
            for stage in STAGES
        ),
        True,
    )
    (tmp_path / "history.json").write_text(json.dumps(history.to_dict()))
    return tmp_path


def arguments(folder, *extra):
    """Build explicit file arguments without fixture-specific loader heuristics."""
    return [
        "plan",
        "--dwi",
        str(folder / "data.nii"),
        "--bval",
        str(folder / "bvals"),
        "--bvec",
        str(folder / "bvecs"),
        *extra,
    ]


def test_mask_proposal_and_raw_denoise_no_external_execution(inputs, monkeypatch, capsys):
    """Eligibility is a proposal, with no subprocess or imaging derivative."""

    def forbidden(*args, **kwargs):
        """Any scientific subprocess invocation is a test failure."""
        raise AssertionError("Planning must not run commands")

    monkeypatch.setattr(subprocess, "run", forbidden)
    target = inputs / "proposal.json"
    assert (
        main(
            arguments(
                inputs,
                "--history",
                str(inputs / "history.json"),
                "--operation",
                "mask",
                "--plan-json",
                str(target),
            )
        )
        == 0
    )
    result = json.loads(target.read_text())
    assert [n["operation"] for n in result["nodes"]] == ["convert", "mask"]
    assert result["execution_available"] is False
    assert "no commands executed" in capsys.readouterr().out
    assert not list(inputs.glob("*.mif"))


def test_hcp_constraint_exports_blocked_explanation(inputs):
    """A blocked selection remains inspectable without permitting execution."""
    path = inputs / "history.json"
    payload = json.loads(path.read_text())
    next(c for c in payload["claims"] if c["stage"] == "interpolation")["completed"] = True
    path.write_text(json.dumps(payload))
    output = inputs / "blocked.json"
    assert (
        main(
            arguments(
                inputs, "--history", str(path), "--operation", "denoise", "--plan-json", str(output)
            )
        )
        == 1
    )
    plan = json.loads(output.read_text())
    assert plan["status"] == "blocked"
    assert "interpolation" in " ".join(plan["nodes"][1]["reasons"])


def test_missing_history_stays_uncertain_and_invalid_gradients_stop(inputs):
    """No filename-based history inference or bypass of existing loader checks."""
    assert main(arguments(inputs, "--operation", "mask")) == 1
    (inputs / "bvals").write_text("0 1000")
    output = inputs / "invalid.json"
    assert main(arguments(inputs, "--operation", "mask", "--plan-json", str(output))) == 1
    assert not output.exists()


@pytest.mark.parametrize(
    "content",
    [
        '{"schema_version":1,"confirmed":true,"confirmed":false,"claims":[]}',
        '{"schema_version":1,"confirmed":NaN,"claims":[]}',
        '{"schema_version":true,"confirmed":true,"claims":[]}',
        '{"schema_version":1,"confirmed":true,"claims":[],"automatic":true}',
    ],
)
def test_invalid_history_document_is_actionable(inputs, content, capsys):
    """Reject discarded/ambiguous evidence rather than proceed with guesses."""
    path = inputs / "history.json"
    path.write_text(content)
    assert main(arguments(inputs, "--history", str(path), "--operation", "mask")) == 2
    assert "planning request error" in capsys.readouterr().err


def test_history_file_and_inputs_cannot_be_overwritten(inputs):
    """Plan exports protect evidence sources as well as imaging inputs."""
    for name in ["history.json", "data.nii", "bvals"]:
        destination = inputs / name
        before = destination.read_bytes()
        assert (
            main(
                arguments(
                    inputs,
                    "--history",
                    str(inputs / "history.json"),
                    "--operation",
                    "mask",
                    "--plan-json",
                    str(destination),
                )
            )
            == 2
        )
        assert destination.read_bytes() == before
