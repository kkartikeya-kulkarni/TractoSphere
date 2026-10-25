"""Scientific eligibility, evidence conflicts and source-preserving proposals.

All imaging fixtures are tiny synthetic files in pytest's temporary directory.
No scientific command runs in these tests or in this planning phase.
"""

import json
import os
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.dataset import DatasetClosedError, DatasetPaths, SourceChangedError
from dmri_studio.core.loading import DatasetLoader
from dmri_studio.core.workflow import (
    STAGES,
    HistoryClaim,
    ProcessingHistory,
    assess_operations,
    build_plan,
    export_plan,
)


@pytest.fixture
def dataset(tmp_path: Path):
    """Provide b0 and weighted data without assuming an acquisition history."""
    image = nib.Nifti1Image(np.ones((5, 6, 7, 4), np.float32), np.eye(4))
    image.header.set_xyzt_units("mm")
    nib.save(image, tmp_path / "data.nii")
    (tmp_path / "bvals").write_text("5 1000 2000 3000")
    (tmp_path / "bvecs").write_text("0 1 0 0\n0 0 1 0\n0 0 0 1")
    result = DatasetLoader().load(
        DatasetPaths(tmp_path / "data.nii", tmp_path / "bvals", tmp_path / "bvecs")
    )
    assert result.dataset is not None
    with result.dataset as value:
        yield value


def claim(stage: str, done: bool, evidence: str = "user_declared") -> HistoryClaim:
    """Keep test facts traceable rather than promote them to verified history."""
    return HistoryClaim(stage, done, evidence, "Synthetic test declaration", "medium", "2026-10-10")


def raw_history() -> ProcessingHistory:
    """The caller explicitly declares these steps absent; no filename heuristic."""
    return ProcessingHistory(tuple(claim(stage, False) for stage in STAGES), True)


def statuses(dataset, history):
    """Index the shared engine's decisions for concise scientific assertions."""
    return {d.operation: d.status for d in assess_operations(dataset, history)}


def test_hcp_mask_does_not_force_ineligible_steps(dataset):
    """Previously corrected HCP can select an independent mask."""
    history = ProcessingHistory(
        tuple(
            claim(
                stage,
                stage not in {"denoise", "gibbs"},
                "inferred" if stage == "interpolation" else "user_declared",
            )
            for stage in STAGES
        ),
        True,
    )
    assert statuses(dataset, history) == {
        "convert": "eligible",
        "denoise": "blocked",
        "gibbs": "blocked",
        "mask": "eligible",
        "bias": "blocked",
        "correction": "blocked",
        "anatomy": "eligible",
        "alignment": "eligible",
        "combine": "eligible",
    }
    payload = build_plan(dataset, history, ("mask",)).to_dict()
    assert [node["operation"] for node in payload["nodes"]] == ["convert", "mask"]
    assert payload["nodes"][1]["depends_on"] == ["convert"]
    assert payload["status"] == "eligible"
    assert payload["approved_for_execution"] is False
    assert payload["execution_available"] is False
    assert payload["history"]["claims"][0]["evidence"] == "user_declared"


def test_unknown_history_never_becomes_raw_by_filename(dataset):
    """The filename data.nii establishes no previous processing history."""
    history = ProcessingHistory(confirmed=True)
    assert statuses(dataset, history)["denoise"] == "uncertain"
    assert statuses(dataset, history)["mask"] == "eligible"
    assert build_plan(dataset, history, ("denoise",)).to_dict()["status"] == "uncertain"


def test_declared_raw_denoise_order_and_unreviewed_gibbs(dataset):
    """Allow the denoising proposal; do not invent raw slice/Fourier metadata."""
    results = statuses(dataset, raw_history())
    assert results["denoise"] == "eligible"
    assert results["gibbs"] == "uncertain"
    plan = build_plan(dataset, raw_history(), ("mask", "denoise"))
    assert [n["operation"] for n in plan.to_dict()["nodes"]] == ["convert", "denoise", "mask"]


def test_raw_gibbs_verified_plane_partial_fourier_unknown_filtering(dataset, tmp_path):
    """Metadata-confirmed slice planes qualify without scanner filter settings."""
    path = tmp_path / "verified-plane.nii"
    image = nib.Nifti1Image(np.ones(dataset.shape, np.float32), dataset.affine)
    image.header.set_xyzt_units("mm")
    image.header.set_dim_info(freq=0, phase=1, slice=2)
    nib.save(image, path)
    sidecar = tmp_path / "verified-plane.json"
    sidecar.write_text(json.dumps({"PartialFourier": "7/8"}))
    loaded = DatasetLoader().load(
        DatasetPaths(path, dataset.paths.bval, dataset.paths.bvec, sidecar)
    )
    assert loaded.dataset is not None
    with loaded.dataset as value:
        decisions = assess_operations(value, raw_history())
        gibbs = next(d for d in decisions if d.operation == "gibbs")
        assert gibbs.status == "eligible"
        assert any("does not by itself block" in r for r in gibbs.reasons)
        assert any("7/8" in r for r in gibbs.reasons)


@pytest.mark.parametrize("stage", ["interpolation", "denoise", "gibbs"])
def test_noise_model_changing_or_repeated_denoise_is_blocked(dataset, stage):
    """Do not allow an override of known method contraindications."""
    claims = tuple(claim(s, s == stage) for s in STAGES)
    assert statuses(dataset, ProcessingHistory(claims, True))["denoise"] == "blocked"


def test_gradient_calibration_does_not_prove_image_interpolation(dataset):
    """A named correction is not itself proof of voxel resampling."""
    history = ProcessingHistory(tuple(claim(s, s == "gradient_nonlinearity") for s in STAGES), True)
    assert statuses(dataset, history)["denoise"] == "uncertain"


def test_conflicting_evidence_is_not_resolved_by_confidence(dataset):
    """Preserve opposing claims; masks can still be considered independently."""
    history = ProcessingHistory(
        (claim("interpolation", True, "inferred"), claim("interpolation", False)), True
    )
    assert history.resolve("interpolation") == "conflict"
    assert statuses(dataset, history)["denoise"] == "uncertain"
    assert len(history.to_dict()["claims"]) == 2


def test_confirmation_and_selection_are_both_required(dataset):
    """Eligible methods alone cannot turn an unreviewed import into a proposal."""
    assert build_plan(dataset, ProcessingHistory(), ("mask",)).to_dict()["status"] == "uncertain"
    assert build_plan(dataset, raw_history(), ()).to_dict()["nodes"] == []
    with pytest.raises(ValueError):
        build_plan(dataset, raw_history(), ("mask", "mask"))
    with pytest.raises(ValueError):
        build_plan(dataset, raw_history(), ("eddy",))


def test_changed_and_closed_inputs_require_reload(dataset):
    """Reject stale decisions even if the original load passed."""
    original = dataset.fingerprints["bval"]
    atime_ns = dataset.paths.bval.stat().st_atime_ns
    dataset.paths.bval.write_text("0 1000 1000 3000")
    os.utime(dataset.paths.bval, ns=(atime_ns, original.mtime_ns))
    with pytest.raises(SourceChangedError):
        build_plan(dataset, raw_history(), ("mask",))
    dataset.close()
    with pytest.raises(DatasetClosedError):
        build_plan(dataset, raw_history(), ("mask",))


def test_history_roundtrip_and_nested_plan_immutability(dataset):
    """Clients cannot relabel evidence or approval by modifying returned data."""
    history = raw_history()
    assert ProcessingHistory.from_dict(history.to_dict()) == history
    plan = build_plan(dataset, history, ("mask",))
    detached = plan.to_dict()
    detached["history"]["confirmed"] = False
    assert plan.to_dict()["history"]["confirmed"] is True
    with pytest.raises(TypeError):
        plan.payload["approved_for_execution"] = True


def test_json_labels_cannot_verify_upstream_evidence():
    """A JSON field is not proof that a source log has actually been checked."""
    payload = ProcessingHistory((claim("motion", True, "verified"),), True).to_dict()
    with pytest.raises(ValueError, match="cannot verify"):
        ProcessingHistory.from_dict(payload)


@pytest.mark.parametrize(
    "change",
    [
        {"completed": 1},
        {"completed": None},
        {"source": ""},
        {"recorded_on": "2026-02-31"},
        {"evidence": "automatic"},
        {"evidence": "unknown"},
        {"confidence": "certain"},
    ],
)
def test_invalid_history_is_rejected(change):
    """Reject malformed completion states and fabricated evidence labels."""
    fields = {
        "stage": "motion",
        "completed": True,
        "evidence": "user_declared",
        "source": "record",
        "recorded_on": "2026-10-10",
    }
    fields.update(change)
    with pytest.raises(ValueError):
        HistoryClaim(**fields)


def test_plan_export_protects_sources_and_existing_files(dataset, tmp_path):
    """A report can be reviewed without mutating images or replacing evidence."""
    before = {p: p.read_bytes() for p in dataset.paths.supplied().values()}
    plan = build_plan(dataset, raw_history(), ("mask",))
    saved = export_plan(plan, tmp_path / "proposal.json")
    assert json.loads(saved.read_text())["kind"] == "workflow_proposal"
    with pytest.raises(ValueError):
        export_plan(plan, saved)
    for path in before:
        with pytest.raises(ValueError):
            export_plan(plan, path)
    alias = tmp_path / "alias.json"
    os.link(dataset.paths.bval, alias)
    with pytest.raises(ValueError):
        export_plan(plan, alias)
    assert {p: p.read_bytes() for p in before} == before


def test_export_race_preserves_competing_file(dataset, tmp_path, monkeypatch):
    """No-overwrite protection also holds after the destination precheck."""
    original = os.link
    destination = tmp_path / "race.json"

    def competing(source, target):
        """Publish another file immediately before the final atomic link."""
        Path(target).write_text("Other process")
        original(source, target)

    monkeypatch.setattr(os, "link", competing)
    with pytest.raises(FileExistsError):
        export_plan(build_plan(dataset, raw_history(), ("mask",)), destination)
    assert destination.read_text() == "Other process"
    assert not list(tmp_path.glob(".race.json.*.tmp"))
