"""Acquisition/diagnostic regression checks without participant processing."""

import json
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.acquisition import validate_epi_reference
from dmri_studio.core.acquisition_metadata import inherited_metadata, slice_groups
from dmri_studio.core.correction import CorrectionStep, correction_gate
from dmri_studio.core.correction_diagnostics import eddy_shells, hyperparameter_blocks
from dmri_studio.core.dataset import DatasetPaths
from dmri_studio.core.execution import prepare_execution
from dmri_studio.core.loading import DatasetLoader
from dmri_studio.core.workflow import STAGES, HistoryClaim, ProcessingHistory


def fixture(root: Path):
    """Create a magnitude BIDS phantom with nonzero first low-b volume index."""
    raw = root / "raw"
    dwi = raw / "sub-test/ses-01/dwi/sub-test_ses-01_dwi.nii"
    ref = raw / "sub-test/ses-01/fmap/sub-test_ses-01_dir-PA_epi.nii"
    for p in (dwi, ref):
        p.parent.mkdir(parents=True, exist_ok=True)
    (raw / "dataset_description.json").write_text(
        '{"Name":"SYNTHETIC TEST ONLY","BIDSVersion":"1.11.2"}'
    )
    grid = np.indices((32, 34, 36), dtype=float)
    radius = sum(((grid[k] - (grid.shape[k + 1] - 1) / 2) / [12, 13, 14][k]) ** 2 for k in range(3))
    brain = radius < 1
    rng = np.random.default_rng(101)
    vectors = rng.normal(size=(3, 18))
    vectors /= np.linalg.norm(vectors, axis=0)
    b = np.full(18, 1000.0)
    b[[1, 9, 17]] = 5
    data = np.stack(
        [
            (900 - 150 * radius)
            * brain
            * (1 if b[v] < 50 else np.exp(-0.5 - vectors[0, v] ** 2 * 0.5))
            + rng.normal(0, 5, brain.shape)
            for v in range(18)
        ],
        axis=3,
    ).astype(np.float32)
    affine = np.diag([-2, 2, 2, 1])
    affine[0, 3] = 62
    for path, values in [(dwi, data), (ref, data[..., [1, 9]])]:
        image = nib.Nifti1Image(values, affine)
        image.header.set_xyzt_units("mm")
        image.header.set_dim_info(freq=0, phase=1, slice=2)
        nib.save(image, path)
    bval, bvec = dwi.with_suffix(".bval"), dwi.with_suffix(".bvec")
    np.savetxt(bval, [b], fmt="%.17g")
    np.savetxt(bvec, vectors, fmt="%.17g")
    metadata = {
        "PhaseEncodingDirection": "j-",
        "TotalReadoutTime": 0.05,
        "EchoTime": 0.08,
        "RepetitionTime": 4,
        "FlipAngle": 90,
        "SliceEncodingDirection": "k",
        "PartialFourier": 0.875,
        "SliceTiming": (np.arange(36) * 0.1).tolist(),
    }
    dwi.with_suffix(".json").write_text(json.dumps(metadata))
    ref.with_suffix(".json").write_text(
        json.dumps(
            metadata
            | {
                "PhaseEncodingDirection": "j",
                "IntendedFor": "bids::sub-test/ses-01/dwi/" + dwi.name,
            }
        )
    )
    dataset = DatasetLoader().load(DatasetPaths(dwi, bval, bvec, dwi.with_suffix(".json"))).dataset
    assert dataset is not None
    history = ProcessingHistory(
        tuple(
            HistoryClaim(s, False, "user_declared", "Synthetic fixture", "high", "2026-10-13")
            for s in STAGES
        ),
        True,
    )
    return dataset, history, raw, ref


def test_inheritance_records_field_sources_and_refuses_ambiguity(tmp_path):
    dataset, _, root, ref = fixture(tmp_path)
    try:
        (root / "dwi.json").write_text('{"TotalReadoutTime":0.04,"Manufacturer":"Fixture"}')
        subject = root / "sub-test"
        (subject / "sub-test_dwi.json").write_text('{"EchoTime":0.075}')
        result = inherited_metadata(dataset.paths.dwi)
        assert result["metadata"]["TotalReadoutTime"] == 0.05
        assert result["metadata"]["Manufacturer"] == "Fixture"
        assert result["field_sources"]["Manufacturer"] == str(root / "dwi.json")
        assert len(result["sources"]) == 3
        reference = inherited_metadata(ref)
        assert validate_epi_reference(
            dataset.paths.dwi, ref, result["metadata"], reference["metadata"]
        )["intended_for_verified"]
        (root / "sub-test_dwi.json").write_text('{"Manufacturer":"Fixture"}')
        with pytest.raises(ValueError, match="Multiple applicable"):
            inherited_metadata(dataset.paths.dwi)
    finally:
        dataset.close()


def test_slice_groups_reverse_axis_and_conflicts():
    image = nib.Nifti1Image(np.zeros((4, 4, 6)), np.eye(4))
    image.header.set_dim_info(slice=2)
    meta = {
        "SliceEncodingDirection": "k-",
        "SliceTiming": [0, 0.2, 0.1, 0, 0.2, 0.1],
        "MultibandAccelerationFactor": 2,
    }
    groups = slice_groups(image, meta)
    assert groups["groups"] == [[2, 5], [0, 3], [1, 4]]
    with pytest.raises(ValueError, match="conflicts"):
        slice_groups(image, meta | {"MultibandAccelerationFactor": 3})
    assert slice_groups(image, {})["status"] == "unavailable"
    with pytest.raises(ValueError, match="Unequal"):
        slice_groups(image, meta | {"SliceTiming": [0, 0.1, 0.2, 0.3, 0.3, 0.4]})


def test_eddy_shell_indexing_and_invalid_gp_not_hidden():
    result = eddy_shells(np.array([1000, 5, 2000, 990, 2010, 5]))
    assert [s["indices"] for s in result] == [[1, 5], [0, 3], [2, 4]]
    with pytest.raises(ValueError, match="Ambiguous"):
        eddy_shells(np.array([5, 1000, 1090, 1180]))
    valid = "Estimated hyperparameters:\n 5\n 0\n -2\nMore output"
    assert hyperparameter_blocks(valid, 1)[0]["error_variances_in_shell_order"] == [np.exp(-2)]
    assert hyperparameter_blocks(valid.replace(":\n 5", ": 5"), 1) == hyperparameter_blocks(
        valid, 1
    )
    with pytest.raises(ValueError, match="nonfinite"):
        hyperparameter_blocks("Estimated hyperparameters:\nnan\n0\n-2\nOther\n" + valid, 1)
    with pytest.raises(ValueError, match="underflow"):
        hyperparameter_blocks("Estimated hyperparameters:\n5\n0\n-900\n", 1)


def test_correction_plan_freezes_associations_and_logout(tmp_path):
    """Read installed tools only; prepare never starts TOPUP/EDDY or creates output."""
    fsl = Path.home() / "fsl/bin"
    mr = Path("/usr/local/mrtrix3/bin")
    if not (fsl / "eddy_cpu").is_file() or not (mr / "mrconvert").is_file():
        pytest.skip("Local validated tool installation unavailable")
    dataset, history, root, ref = fixture(tmp_path)
    try:
        output = tmp_path / "run"
        plan = prepare_execution(
            dataset,
            history,
            ("correction",),
            output,
            references=(ref,),
            fsl_directory=fsl,
            tool_directory=mr,
        )
        p = plan.to_dict()
        assert not output.exists()
        assert p["shells"][0]["indices"] == [1, 9, 17]
        assert p["tools"]["eddy_cpu"]["version"] == "2401.2"
        topup = next(c["argv"] for c in p["commands"] if c["step"] == "topup")
        assert f"--logout={output / 'correction/topup-explicit.log'}" in topup
        assert all(
            str(output) in arg.split("=", 1)[1]
            for arg in topup
            if arg.startswith(
                ("--imain=", "--datain=", "--config=", "--out=", "--fout=", "--iout=", "--logout=")
            )
        )
        assert p["slice_grouping"]["multiband_from_timing"] == 1
        meta = json.loads(ref.with_suffix(".json").read_text())
        ref.with_suffix(".json").write_text(
            json.dumps(meta | {"IntendedFor": "ses-02/dwi/wrong.nii"})
        )
        with pytest.raises(ValueError, match="IntendedFor"):
            prepare_execution(
                dataset,
                history,
                ("correction",),
                output,
                references=(ref,),
                fsl_directory=fsl,
                tool_directory=mr,
            )
    finally:
        dataset.close()


def test_tool_contract_refuses_source_side_paths_and_existing_outputs(tmp_path):
    """Prevent recurrence of TOPUP's historical source-side log overwrite."""
    folder = tmp_path / "attempt"
    folder.mkdir()
    source = tmp_path / "historical.log"
    source.write_text("preserve me")
    staged = folder / "staged.nii"
    staged.write_text("test contract only")
    step = CorrectionStep("topup", ("never-executed",), (staged,), (source,), {})
    with pytest.raises(ValueError, match="inside"):
        step.validate(folder)
    step = CorrectionStep("topup", ("never-executed",), (staged,), (staged,), {})
    with pytest.raises(ValueError, match="overwrite"):
        step.validate(folder)
    assert source.read_text() == "preserve me"


def test_diagnostic_gate_preserves_nonfinite_and_collapse_failures(tmp_path):
    """Controlled saved arrays test the gate, not EDDY's scientific performance."""
    dataset, _, _, _ = fixture(tmp_path)
    folder = tmp_path / "diagnostic-fixture"
    for name in ("correction", "evaluation", "logs"):
        (folder / name).mkdir(parents=True)
    c = folder / "correction"

    def save(path, values):
        image = nib.Nifti1Image(values, dataset.affine)
        image.header.set_xyzt_units("mm")
        nib.save(image, path)

    try:
        mask = np.zeros(dataset.shape[:3], np.uint8)
        mask[4:-4, 4:-4, 4:-4] = 1
        save(folder / "evaluation/mask.nii.gz", mask)
        save(c / "fitting_mask.nii.gz", mask)
        residual = np.ones(dataset.shape, np.float32)
        cnr = np.ones((*dataset.shape[:3], 2), np.float32)
        save(c / "eddy.eddy_residuals.nii.gz", residual)
        save(c / "eddy.eddy_cnr_maps.nii.gz", cnr)
        save(c / "eddy.nii.gz", np.ones(dataset.shape, np.float32))
        np.savetxt(c / "eddy.eddy_movement_rms", np.zeros((dataset.n_volumes, 2)))
        np.savetxt(c / "eddy.eddy_parameters", np.zeros((dataset.n_volumes, 16)))
        np.savetxt(
            c / "eddy.eddy_outlier_map",
            np.zeros((dataset.n_volumes, dataset.shape[2])),
            header="synthetic outlier map",
        )
        shells = eddy_shells(dataset.gradients.bvals)
        (c / "eddy.eddy_shell_indicies.json").write_text(
            json.dumps({"Shells": [{"Index": s["indices"]} for s in shells]})
        )
        (folder / "logs/eddy.log").write_text(
            "SYNTHETIC DIAGNOSTIC FIXTURE\nEstimated hyperparameters: 5\n0\n-2\n"
        )
        gate = correction_gate(dataset, folder, {"shells": shells}, lambda: False)
        assert gate["technical_checks_passed"] and gate["scientific_acceptance"] == "pending"
        cnr[0, 0, 0, 1] = np.nan  # Background invalid values remain reported too.
        save(c / "eddy.eddy_cnr_maps.nii.gz", cnr)
        gate = correction_gate(dataset, folder, {"shells": shells}, lambda: False)
        assert not gate["technical_checks_passed"]
        assert gate["shells"][1]["CNR_entire_grid"]["nonfinite"] == 1
        assert gate["shells"][1]["CNR_inside_brain"]["nonfinite"] == 0
        residual[..., dataset.gradients.bvals > 100] = 0
        save(c / "eddy.eddy_residuals.nii.gz", residual)
        gate = correction_gate(dataset, folder, {"shells": shells}, lambda: False)
        assert any("collapse" in flag for flag in gate["flags"])
        (c / "eddy.eddy_shell_indicies.json").write_text('{"Shells":[]}')
        with pytest.raises(ValueError, match="shell indices"):
            correction_gate(dataset, folder, {"shells": shells}, lambda: False)
    finally:
        dataset.close()
