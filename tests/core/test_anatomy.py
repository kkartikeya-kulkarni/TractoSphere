"""Bound anatomical provenance, native geometry and review prerequisites."""

import json
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.anatomy import (
    image3d,
    posterior_support,
    prepare_anatomy,
    reviewed_anatomy,
    save3d,
    verified_manifest,
)
from dmri_studio.core.dataset import DatasetPaths
from dmri_studio.core.loading import DatasetLoader
from dmri_studio.core.qc import artifact, load_catalog, record_review
from dmri_studio.core.workflow import ProcessingHistory


def test_fast_zero_input_exclusions_are_recorded_without_filling():
    """Actual zeros remain FAST background, with their exclusions recorded."""
    brain = np.array([[[1.0, 0.0, 0.0]]])
    mask = np.array([[[1, 1, 0]]])
    maps = np.zeros((3, *brain.shape))
    maps[2, 0, 0, 0] = 1
    result = posterior_support(maps, brain, mask)
    assert result["zero_input_voxels_inside_extraction"] == 1
    assert np.array_equal(mask, [[[1, 1, 0]]])
    maps[2, 0, 0, 1] = 1
    with pytest.raises(ValueError, match="actual nonzero"):
        posterior_support(maps, brain, mask)


def fixture(root):
    """Three synthetic tissues with background; not subject or accepted anatomy."""
    source = root / "source"
    source.mkdir()
    shape = (32, 34, 36)
    grid = np.indices(shape)
    radius = sum(((grid[i] - (shape[i] - 1) / 2) / (shape[i] * 0.39)) ** 2 for i in range(3))
    rng = np.random.default_rng(31)
    t1 = np.where(
        radius < 0.3, 900, np.where(radius < 0.72, 550, np.where(radius < 1, 200, 0))
    ).astype(np.float32)
    t1 += (rng.normal(0, 12, shape) * (radius < 1)).astype(np.float32)
    affine = np.diag([-2.0, 2.0, 2.0, 1.0])
    affine[0, 3] = 62
    image = nib.Nifti1Image(t1, affine)
    image.header.set_xyzt_units("mm")
    nib.save(image, source / "t1.nii.gz")
    dwi = nib.Nifti1Image(np.stack([t1 * k for k in [1, 0.6, 0.7, 0.5]], -1), affine)
    dwi.header.set_xyzt_units("mm")
    nib.save(dwi, source / "dwi.nii")
    (source / "dwi.bval").write_text("5 1000 1000 1000\n")
    (source / "dwi.bvec").write_text("0 1 0 0\n0 0 1 0\n0 0 0 1\n")
    return DatasetPaths(
        source / "dwi.nii", source / "dwi.bval", source / "dwi.bvec"
    ), source / "t1.nii.gz"


def test_save_mean_keeps_grid_without_changing_four_dimensional_source(tmp_path):
    """A 3D registration mean keeps source forms; 4D file bytes remain intact."""
    paths, _ = fixture(tmp_path)
    before = paths.dwi.read_bytes()
    reference = nib.load(paths.dwi)
    values = np.asarray(reference.dataobj[..., 0])
    target = tmp_path / "mean.nii.gz"
    save3d(values, reference, target)
    result, actual = image3d(target)
    assert result.shape == reference.shape[:3]
    assert np.array_equal(result.affine, reference.affine)
    assert np.array_equal(values, actual)
    assert before == paths.dwi.read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        save3d(values, reference, target)


def test_plan_requires_explicit_history_and_matched_inv2(tmp_path):
    """Missing processing history and mismatched anatomical grids cannot run."""
    paths, t1 = fixture(tmp_path)
    loaded = DatasetLoader().load(paths)
    with loaded.dataset as dataset:
        history = ProcessingHistory((), True)
        kwargs = dict(
            fsl_directory=Path.home() / "fsl/bin", tool_directory=Path("/usr/local/mrtrix3/bin")
        )
        if not kwargs["tool_directory"].exists():
            pytest.skip("Local MRtrix installation unavailable for planning check")
        with pytest.raises(ValueError, match="Declare"):
            prepare_anatomy(dataset, history, ("anatomy",), tmp_path / "run", **kwargs)
        bad = nib.load(t1)
        shifted = bad.affine.copy()
        shifted[0, 3] += 2
        image = nib.Nifti1Image(np.asarray(bad.dataobj), shifted)
        image.header.set_xyzt_units("mm")
        nib.save(image, tmp_path / "inv2.nii.gz")
        with pytest.raises(ValueError, match="native grid"):
            prepare_anatomy(
                dataset,
                history,
                ("anatomy",),
                tmp_path / "run",
                anatomy_options={
                    "mode": "mp2rage_inv2",
                    "t1": str(t1),
                    "inv2": str(tmp_path / "inv2.nii.gz"),
                    "evidence": "Synthetic mismatched test",
                },
                **kwargs,
            )
        assert not (tmp_path / "run").exists()


def test_completed_record_cannot_hide_changed_output(tmp_path):
    """Producer success is insufficient if an output or path association changed."""
    result = tmp_path / "brain.txt"
    result.write_text("changed")
    record = tmp_path / "manifest.json"
    record.write_text(
        json.dumps(
            {
                "status": "validated_pending_qc",
                "inputs_unchanged": True,
                "plan": {"kind": "fsl_anatomy_plan", "output_directory": str(tmp_path)},
                "outputs": {"brain.txt": {"sha256": "0" * 64}},
            }
        )
    )
    with pytest.raises(ValueError, match="changed"):
        verified_manifest(record, "fsl_anatomy_plan", lambda: False)


def test_alignment_requires_accepted_review_of_exact_anatomy(tmp_path):
    """Needs-review and a decision for another manifest cannot unlock BBR."""
    source = tmp_path / "manifest.json"
    source.write_text("{}")
    entry = artifact(source, "history", "Synthetic anatomical record")
    path = tmp_path / "qc-catalog.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "dmri_qc_catalog",
                "identity": {"subject": "synthetic"},
                "stages": [
                    {
                        "id": "anatomy",
                        "title": "Synthetic",
                        "execution": "completed",
                        "technical": "passed",
                        "automated_qc": "pending",
                        "scientific_review": "required",
                        "findings": [],
                        "artifacts": [entry],
                    }
                ],
            }
        )
    )
    catalog = load_catalog(path)
    pending = record_review(
        catalog, "anatomy", "needs_review", "Test fixture", "Unresolved boundaries", tmp_path
    )
    with pytest.raises(ValueError, match="acceptance"):
        reviewed_anatomy(pending, entry["sha256"], lambda: False)
    accepted = record_review(
        catalog,
        "anatomy",
        "accepted",
        "Test fixture",
        "Synthetic decision, no participant acceptance",
        tmp_path,
    )
    assert reviewed_anatomy(accepted, entry["sha256"], lambda: False)
    with pytest.raises(ValueError, match="acceptance"):
        reviewed_anatomy(accepted, "0" * 64, lambda: False)
