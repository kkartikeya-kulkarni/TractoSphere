"""Matched AP/PA safety gates and real local combination on a tiny phantom.

Producer records below are explicitly synthetic contract fixtures. No EDDY/N4
is run or claimed; only combination tools execute in the integration test.
"""

import json
import shutil
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.dataset import DatasetPaths
from dmri_studio.core.execution import execute_plan, hash_file, prepare_execution
from dmri_studio.core.loading import DatasetLoader
from dmri_studio.core.pairing import validate_pairing
from dmri_studio.core.qc import load_catalog
from dmri_studio.core.workflow import HistoryClaim, ProcessingHistory

PROJECT = Path(__file__).resolve().parents[3]
HELPER = (
    PROJECT
    / "data/hcd-reference/evidence/ap-pa-combination-audit-001/average_bvecs-pinned-reference.py"
)
FSL = Path.home() / "fsl/bin"


def make_fixture(root):
    """Generate nine volumes: extra reference and four explicit repeated pairs."""
    source = root / "source"
    source.mkdir()
    shape = (12, 14, 16)
    xyz = np.indices(shape)
    radius = sum(((xyz[i] - (shape[i] - 1) / 2) / (shape[i] * 0.42)) ** 2 for i in range(3))
    tissue = np.where(radius < 0.5, 800, np.where(radius < 1, 500, 0)).astype(np.float32)
    rng = np.random.default_rng(58)
    data = np.stack(
        [
            np.where(
                radius < 1, tissue * (1 if v in (0, 1, 5) else 0.65) + rng.normal(0, 2, shape), 0
            )
            for v in range(9)
        ],
        axis=3,
    ).astype(np.float32)
    # Signed input and a missing FOV voxel exercise the exact HCP clipping recipe.
    data[5, 5, 5, 2] = -6
    data[5, 5, 5, 6] = -2
    data[4, 4, 4, :] = 0
    affine = np.diag([-2.0, 2.0, 2.5, 1.0])
    affine[:3, 3] = [12, -14, -20]
    image = nib.Nifti1Image(data, affine)
    image.header.set_xyzt_units("mm")
    image.set_qform(affine, 1)
    image.set_sform(affine, 1)
    dwi = source / "bias-corrected.nii"
    nib.save(image, dwi)
    bval = source / "corrected.bval"
    np.savetxt(bval, [[5, 5, 1000, 1000, 1000, 5, 1000, 1000, 1000]], fmt="%.17g")
    nominal = np.array(
        [[1, 1, 1, 0, 0, 1, 1, 0, 0], [0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 0, 0, 0, 1, 0, 0, 0, 1]],
        float,
    )
    angle = np.deg2rad(2)
    rotation = np.array(
        [[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]]
    )
    rotated = nominal.copy()
    rotated[:, 5:] = rotation @ rotated[:, 5:]
    bvec = source / "corrected.bvec"
    np.savetxt(bvec, rotated, fmt="%.17g")
    nominal_path = source / "nominal.bvec"
    np.savetxt(nominal_path, nominal, fmt="%.17g")
    outlier = source / "eddy.eddy_outlier_map"
    flags = np.zeros((9, shape[2]), int)
    flags[2, 8] = 1
    np.savetxt(outlier, flags, fmt="%d", header="SYNTHETIC contract; no EDDY run")
    upstream = source / "synthetic-eddy-record.json"
    upstream.write_text(
        json.dumps(
            {
                "fixture": "SYNTHETIC CONTRACT; EDDY NOT EXECUTED",
                "argv": ["eddy_cpu", "--out=" + str(source / "eddy")],
                "outputs": {str(outlier): {"sha256": hash_file(outlier)}},
            }
        )
    )
    manifest = source / "synthetic-N4-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "fixture": "SYNTHETIC CONTRACT; N4 NOT EXECUTED",
                "status": "validated_pending_qc",
                "inputs_unchanged": True,
                "plan": {
                    "kind": "mrtrix_ants_bias_plan",
                    "output_directory": str(source),
                    "handoff": {"record": str(upstream), "record_sha256": hash_file(upstream)},
                },
                "outputs": {p.name: {"sha256": hash_file(p)} for p in [dwi, bval, bvec]},
            }
        )
    )
    metadata = []
    for name, pe in [("A", "j-"), ("B", "j")]:
        p = source / (name + ".json")
        p.write_text(
            json.dumps(
                {
                    "PhaseEncodingDirection": pe,
                    "EchoTime": 0.09,
                    "RepetitionTime": 4.0,
                    "TotalReadoutTime": 0.05,
                    "Fixture": "SYNTHETIC protocol",
                }
            )
        )
        metadata.append(p)
    spec = {
        "schema_version": 1,
        "kind": "dmri_matched_AP_PA",
        "subject": "synthetic-combination",
        "session": "fixture",
        "rationale": "Generated repeated encodings for software validation only; no participant inference",
        "parent_sha256": {
            k: hash_file(v) for k, v in [("dwi", dwi), ("bval", bval), ("bvec", bvec)]
        },
        "pairs": [[1, 5], [2, 6], [3, 7], [4, 8]],
        "unpaired": [{"index": 0, "reason": "Synthetic extra reference retained in parent"}],
        "series_lengths": [2, 2],
        "nominal_gradients": {
            k: {"path": str(p), "sha256": hash_file(p)}
            for k, p in [("bval", bval), ("bvec", nominal_path)]
        },
        "evidence": [
            {"path": str(p), "sha256": hash_file(p), "description": "Generated protocol fixture"}
            for p in metadata
        ],
        "acquisition": {
            "basis": "documented_protocol",
            "A_json": str(metadata[0]),
            "B_json": str(metadata[1]),
            "same_geometry_and_reconstruction_evidence": "Synthetic array pairs generated together with identical grid; no reconstruction",
            "between_run_scaling_evidence": "Generated with identical tissue and scaling; no intensity normalization",
        },
    }
    specification = source / "pairing.json"
    specification.write_text(json.dumps(spec))
    dataset = DatasetLoader().load(DatasetPaths(dwi, bval, bvec)).dataset
    assert dataset is not None
    history = ProcessingHistory(
        tuple(
            HistoryClaim(s, True, "user_declared", "SYNTHETIC contract only", "high", "2026-10-13")
            for s in ["eddy", "motion", "bias"]
        ),
        True,
    )
    options = {
        "pairing_spec": str(specification),
        "parent_manifest": str(manifest),
        "hcp_helper": str(HELPER),
        "fsl_directory": str(FSL),
    }
    return dataset, history, options


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("reuse", "only one"),
        ("omit", "every parent"),
        ("direction", "same encoding"),
        ("TE", "EchoTime"),
        ("tamper", "missing or changed"),
        ("calibration", "calibration-only"),
    ],
)
def test_reject_unsafe_pairing(tmp_path, mutation, match):
    dataset, history, options = make_fixture(tmp_path)
    path = Path(options["pairing_spec"])
    spec = json.loads(path.read_text())
    try:
        if mutation == "reuse":
            spec["pairs"][1] = spec["pairs"][0]
        if mutation == "omit":
            spec["unpaired"] = []
        if mutation == "direction":
            p = Path(spec["nominal_gradients"]["bvec"]["path"])
            values = np.loadtxt(p)
            values[:, 6] = [0, 1, 0]
            np.savetxt(p, values)
            spec["nominal_gradients"]["bvec"]["sha256"] = hash_file(p)
        if mutation in ("TE", "tamper"):
            p = Path(spec["acquisition"]["B_json"])
            meta = json.loads(p.read_text())
            meta["EchoTime"] = 0.11
            p.write_text(json.dumps(meta))
            if mutation == "TE":
                spec["evidence"][1]["sha256"] = hash_file(p)
        if mutation == "calibration":
            spec["pairs"] = [[0, 1], [2, 6]]
            spec["unpaired"] = [{"index": v, "reason": "fixture"} for v in [3, 4, 5, 7, 8]]
            spec["series_lengths"] = [2]
            # A weighted pair with unequal b-values also fails full-series eligibility.
            spec["pairs"] = [[0, 2], [1, 6]]
            spec["unpaired"] = [{"index": v, "reason": "fixture"} for v in [3, 4, 5, 7, 8]]
        path.write_text(json.dumps(spec))
        with pytest.raises(ValueError, match=match):
            validate_pairing(dataset, path)
    finally:
        dataset.close()


def test_pairing_preserves_extra_reference_and_rotation_evidence(tmp_path):
    dataset, _, options = make_fixture(tmp_path)
    try:
        result = validate_pairing(dataset, Path(options["pairing_spec"]))
        assert result["unpaired_parent_volumes_retained"][0]["index"] == 0
        assert np.allclose(result["weighted_pair_angles_deg"], [2, 2, 0], atol=1e-5)
        assert result["scientific_acceptance"] == "pending"
    finally:
        dataset.close()


def test_real_combination_preserves_parent_and_verifies_all_pairs(tmp_path):
    if (
        not shutil.which("mrconvert")
        or not (FSL / "eddy_combine").is_file()
        or not HELPER.is_file()
    ):
        pytest.skip("Local verified FSL/MRtrix/pinned helper unavailable")
    dataset, history, options = make_fixture(tmp_path)
    try:
        plan = prepare_execution(
            dataset,
            history,
            ("combine",),
            tmp_path / "run",
            combination_options=options,
            nthreads=2,
        )
        assert not (tmp_path / "run").exists()
        result = execute_plan(dataset, plan, approval=plan.digest)
        assert result["status"] == "validated_pending_qc", result.get("error")
        assert result["inputs_unchanged"] and "qc_export_error" not in result, result
        assert len(result["validation"]["volumes"]) == 4
        assert all(v["exact_image_recipe"] for v in result["validation"]["volumes"])
        assert result["staging"]["A"]["source_to_staged_values_verified"]
        assert np.loadtxt(tmp_path / "run/exported.bval")[0] == 5
        assert nib.load(dataset.paths.dwi).shape[-1] == 9
        load_catalog(tmp_path / "run/qc-catalog.json")
    finally:
        dataset.close()
