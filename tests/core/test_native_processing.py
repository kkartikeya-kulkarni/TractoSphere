"""Real native adapters on a small synthetic signal; no participant data replay."""

import shutil

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.dataset import DatasetPaths
from dmri_studio.core.execution import execute_plan, hash_file, prepare_execution
from dmri_studio.core.loading import DatasetLoader
from dmri_studio.core.workflow import STAGES, HistoryClaim, ProcessingHistory


def test_native_sequence_permuted_grid_gradients_and_residuals(tmp_path):
    """Read actual MIF axes rather than mistaking file k for the converted k."""
    if not all(shutil.which(x) for x in ["mrconvert", "mrinfo", "dwidenoise", "mrdegibbs"]):
        pytest.skip("Local MRtrix unavailable")
    source = tmp_path / "input"
    source.mkdir()
    rng = np.random.default_rng(18)
    values = (rng.normal(0, 8, (12, 14, 16, 20)) + 500).astype(np.float32)
    # Input slice axis0 becomes a different MRtrix axis after reorientation.
    affine = np.array([[0, 0, -2, 30], [1.5, 0, 0, -15], [0, 2.5, 0, -10], [0, 0, 0, 1]])
    image = nib.Nifti1Image(values, affine)
    image.header.set_xyzt_units("mm")
    image.header.set_dim_info(slice=0)
    nib.save(image, source / "dwi.nii")
    bvals = np.array([5, *([1000] * 19)])
    vectors = rng.normal(size=(3, 20))
    vectors /= np.linalg.norm(vectors, axis=0)
    np.savetxt(source / "bvals", bvals[None])
    np.savetxt(source / "bvecs", vectors)
    dataset = (
        DatasetLoader()
        .load(DatasetPaths(source / "dwi.nii", source / "bvals", source / "bvecs"))
        .dataset
    )
    assert dataset is not None
    history = ProcessingHistory(
        tuple(
            HistoryClaim(s, False, "user_declared", "Synthetic generation", "high", "2026-10-13")
            for s in STAGES
        ),
        True,
    )
    hashes = {p: hash_file(p) for p in source.iterdir()}
    try:
        plan = prepare_execution(
            dataset, history, ("denoise", "gibbs"), tmp_path / "run", nthreads=2
        )
        assert plan.payload["parameters"]["gibbs"]["mif_axes"] != (1, 2)
        mask_plan = prepare_execution(
            dataset,
            history,
            ("denoise", "gibbs", "mask"),
            tmp_path / "mask-preview",
            clean_scale=2.0,
            nthreads=2,
        )
        mask_command = next(
            c["argv"] for c in mask_plan.to_dict()["commands"] if c["step"] == "mask"
        )
        assert mask_command[mask_command.index("-clean_scale") + 1] == "2"
        assert mask_command[1].endswith("gibbs.mif")
        result = execute_plan(dataset, plan, approval=plan.digest)
        assert result["status"] == "validated_pending_qc", result.get("error")
        assert result["inputs_unchanged"]
        assert np.array_equal(np.loadtxt(tmp_path / "run/native.bval"), bvals)
        previous = values
        for stage in ["denoise", "gibbs"]:
            output = nib.load(tmp_path / f"run/{stage}.nii")
            residual = nib.load(tmp_path / f"run/{stage}-residual.nii")
            assert output.shape == values.shape
            assert np.array_equal(output.affine, affine)
            actual = np.asarray(output.dataobj)
            assert np.allclose(np.asarray(residual.dataobj), previous - actual, atol=1e-5, rtol=0)
            previous = actual
        assert all(hash_file(p) == h for p, h in hashes.items())
    finally:
        dataset.close()
