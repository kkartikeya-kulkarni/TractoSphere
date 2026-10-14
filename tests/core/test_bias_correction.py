"""N4 evidence gates and actual local wrapper on an explicit synthetic fixture.

Fixture producer records emulate the on-disk contract; EDDY is NOT run and these
are never used as participant evidence. The actual MRtrix/ANTs N4 wrapper is run.
"""

import json
import os
import shutil
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.bias_correction import verify_correction_handoff
from dmri_studio.core.dataset import DatasetPaths
from dmri_studio.core.execution import execute_plan, hash_file, prepare_execution
from dmri_studio.core.loading import DatasetLoader
from dmri_studio.core.qc import load_catalog
from dmri_studio.core.workflow import HistoryClaim, ProcessingHistory, assess_operations

ANTS = Path(os.environ.get("TRACTOSPHERE_ANTS_BIN", ".missing-optional-ants"))


def make_fixture(root):
    """Create smooth biased tissue, a binary mask and clearly synthetic records."""
    source = root / "input"
    source.mkdir()
    xyz = np.indices((24, 26, 28), dtype=float)
    radius = sum(((xyz[i] - (xyz.shape[i + 1] - 1) / 2) / [10, 11, 12][i]) ** 2 for i in range(3))
    mask = radius < 1
    tissue = np.where(radius < 0.5, 800, 600) * np.exp(0.3 * xyz[0] / 24)
    rng = np.random.default_rng(126)
    data = np.stack(
        [(tissue * (1 if v < 2 else 0.65) + rng.normal(0, 2, mask.shape)) * mask for v in range(8)],
        axis=3,
    ).astype(np.float32)
    affine = np.array([[0, -2, 0, 30], [2, 0, 0, -20], [0, 0, 2.5, -30], [0, 0, 0, 1]])
    dwi = source / "eddy.nii"
    image = nib.Nifti1Image(data, affine)
    image.header.set_xyzt_units("mm")
    nib.save(image, dwi)
    fitting = source / "mask.nii"
    image = nib.Nifti1Image(mask.astype(np.uint8), affine)
    image.header.set_xyzt_units("mm")
    nib.save(image, fitting)
    bval = source / "bval"
    np.savetxt(bval, [[5, 5, 1000, 1000, 1000, 1000, 1000, 1000]], fmt="%.17g")
    bvec = source / "eddy.eddy_rotated_bvecs"
    vectors = rng.normal(size=(3, 8))
    vectors /= np.linalg.norm(vectors, axis=0)
    np.savetxt(bvec, vectors, fmt="%.17g")
    producer = source / "synthetic-producer.json"
    producer.write_text(
        json.dumps(
            {
                "fixture": "SYNTHETIC CONTRACT ONLY; no EDDY executed",
                "status": "validated_pending_visual_qc",
                "return_code": 0,
                "argv": ["eddy_cpu", "--out=" + str(source / "eddy"), "--bvals=" + str(bval)],
                "inputs": {str(bval): hash_file(bval)},
                "outputs": {str(p): {"sha256": hash_file(p)} for p in [dwi, bvec]},
            }
        )
    )
    gate = source / "synthetic-gate.json"
    gate.write_text(
        json.dumps(
            {
                "fixture": "SYNTHETIC CONTRACT ONLY",
                "technical_checks_passed": True,
                "corrected_input_sha256": hash_file(dwi),
                "flags": ["Synthetic phantom, not anatomical validation"],
            }
        )
    )
    dataset = DatasetLoader().load(DatasetPaths(dwi, bval, bvec)).dataset
    assert dataset is not None
    history = ProcessingHistory(
        tuple(
            HistoryClaim(s, done, "user_declared", "Synthetic fixture", "high", "2026-10-13")
            for s, done in [("bias", False), ("eddy", True), ("motion", True)]
        ),
        True,
    )
    return (
        dataset,
        history,
        {"fitting_mask": fitting, "correction_record": producer, "correction_gate": gate},
    )


def test_handoff_failed_gate_and_changed_vectors(tmp_path):
    dataset, history, inputs = make_fixture(tmp_path)
    try:
        assert (
            verify_correction_handoff(
                dataset, inputs["correction_record"], inputs["correction_gate"]
            )["scientific_acceptance"]
            == "pending"
        )
        gate = json.loads(inputs["correction_gate"].read_text())
        gate["technical_checks_passed"] = False
        inputs["correction_gate"].write_text(json.dumps(gate))
        with pytest.raises(ValueError, match="diagnostics have not passed"):
            verify_correction_handoff(
                dataset, inputs["correction_record"], inputs["correction_gate"]
            )
        gate["technical_checks_passed"] = True
        inputs["correction_gate"].write_text(json.dumps(gate))
        dataset.paths.bvec.write_text("0 0 0")
        with pytest.raises(ValueError, match="output changed"):
            verify_correction_handoff(
                dataset, inputs["correction_record"], inputs["correction_gate"]
            )
    finally:
        dataset.close()


def test_repeat_or_unknown_bias_history_blocks(tmp_path):
    dataset, history, inputs = make_fixture(tmp_path)
    try:
        assert (
            next(d for d in assess_operations(dataset, history) if d.operation == "bias").status
            == "eligible"
        )
        for claims, expected in [
            (history.claims[1:], "uncertain"),
            (
                (
                    HistoryClaim(
                        "bias", True, "user_declared", "Already corrected", "high", "2026-10-13"
                    ),
                    *history.claims[1:],
                ),
                "blocked",
            ),
        ]:
            h = ProcessingHistory(claims, True)
            assert (
                next(d for d in assess_operations(dataset, h) if d.operation == "bias").status
                == expected
            )
    finally:
        dataset.close()


def test_real_n4_division_native_grid_rotated_gradients_and_provenance(tmp_path):
    if not shutil.which("dwibiascorrect") or not (ANTS / "N4BiasFieldCorrection").is_file():
        pytest.skip("Local exercised MRtrix / ANTs installation unavailable")
    dataset, history, inputs = make_fixture(tmp_path)
    hashes = {p: hash_file(p) for p in (tmp_path / "input").iterdir()}
    try:
        plan = prepare_execution(
            dataset, history, ("bias",), tmp_path / "run", nthreads=2, ants_directory=ANTS, **inputs
        )
        assert not (tmp_path / "run").exists()
        argv = plan.to_dict()["commands"][-1]["argv"]
        assert argv[argv.index("-ants.c") + 1] == "[1000,0.0]"
        result = execute_plan(dataset, plan, approval=plan.digest)
        assert result["status"] == "validated_pending_qc", result.get("error")
        assert result["inputs_unchanged"]
        check = result["validation"]["bias"]
        assert check["values_compared"] == np.prod(dataset.shape)
        assert check["rotated_sidecars_preserved"] and not check["spatial_interpolation"]
        field = np.asarray(nib.load(tmp_path / "run/bias-field.nii").dataobj)
        final = nib.load(tmp_path / "run/bias-corrected.nii")
        assert final.shape == dataset.shape and np.array_equal(final.affine, dataset.affine)
        for v in range(dataset.n_volumes):
            assert np.allclose(
                np.asarray(final.dataobj[..., v]),
                dataset.read_volume(v) / field,
                atol=1e-6,
                rtol=2e-7,
            )
        assert np.array_equal(np.loadtxt(tmp_path / "run/corrected.bval"), dataset.gradients.bvals)
        assert np.array_equal(
            np.loadtxt(tmp_path / "run/corrected.bvec").T, dataset.gradients.bvecs
        )
        assert (
            load_catalog(tmp_path / "run/qc-catalog.json").to_dict()["stages"][0][
                "scientific_review"
            ]
            == "required"
        )
        assert all(hash_file(p) == h for p, h in hashes.items())
    finally:
        dataset.close()
