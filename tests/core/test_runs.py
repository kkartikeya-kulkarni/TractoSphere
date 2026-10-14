"""Saved-attempt recovery must preserve unknown history and refuse changed bytes."""

import json
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.execution import hash_file
from dmri_studio.core.loading import DatasetLoader
from dmri_studio.core.runs import inspect_run, load_verified_product
from dmri_studio.core.validation import ValidationPolicy
from dmri_studio.core.workflow import ProcessingHistory


def make_record(root):
    """Create explicitly synthetic completed-producer contract; run no MRI commands."""
    folder = root / "run"
    folder.mkdir()
    image = nib.Nifti1Image(np.ones((4, 5, 6, 4), np.float32), np.diag([2.0, 2.0, 2.0, 1.0]))
    image.header.set_xyzt_units("mm")
    nib.save(image, folder / "denoise.nii")
    (folder / "native.bval").write_text("0 1000 1000 1000")
    (folder / "native.bvec").write_text("0 1 0 0\n0 0 1 0\n0 0 0 1")
    record = {
        "fixture": "SYNTHETIC CONTRACT ONLY; no MRI execution",
        "status": "validated_pending_qc",
        "inputs_unchanged": True,
        "finished_at": "2026-10-13T12:00:00Z",
        "plan": {
            "kind": "mrtrix_native_execution_plan",
            "output_directory": str(folder),
            "operations": ["denoise"],
            "history": ProcessingHistory(confirmed=True).to_dict(),
            "inputs": {},
            "input_paths": {},
            "commands": [{"step": "denoise", "display": "fixture command"}],
        },
        "steps": [
            {"step": "denoise", "status": "validated", "return_code": 0, "elapsed_seconds": 1}
        ],
        "outputs": {p.name: {"sha256": hash_file(p)} for p in folder.iterdir()},
    }
    path = folder / "native-manifest.json"
    path.write_text(json.dumps(record))
    return path


def test_recovered_history_only_marks_executed_stage(tmp_path):
    result = inspect_run(make_record(tmp_path))
    history = ProcessingHistory.from_dict(result["products"][0]["history"])
    assert history.resolve("denoise") == "done"
    assert history.resolve("eddy") == "unknown" and history.resolve("bias") == "unknown"
    assert history.claims[0].evidence == "inferred"
    assert result["scientific_acceptance"] == "pending"
    loaded = load_verified_product(
        result["products"][0], DatasetLoader(), policy=ValidationPolicy()
    )
    assert loaded.dataset is not None
    loaded.dataset.close()


@pytest.mark.parametrize("target", ["output", "manifest"])
def test_changed_evidence_cannot_inherit_history(tmp_path, target):
    path = make_record(tmp_path)
    product = inspect_run(path)["products"][0]
    if target == "output":
        Path(product["paths"]["bval"]).write_text("0 1500 1500 1500")
    else:
        path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="changed"):
        load_verified_product(product, DatasetLoader(), policy=ValidationPolicy())


def test_failed_and_running_records_do_not_authorize_downstream(tmp_path):
    path = make_record(tmp_path)
    record = json.loads(path.read_text())
    for status in ("failed", "running"):
        record["status"] = status
        path.write_text(json.dumps(record))
        result = inspect_run(path)
        assert not result["products"] and not result["handoff"]
        if status == "running":
            assert result["verified_hashes"] == 0 and "liveness" in result["notice"]


def test_changed_completed_output_rejected(tmp_path):
    path = make_record(tmp_path)
    (path.parent / "native.bval").write_text("0 2000 2000 2000")
    with pytest.raises(ValueError, match="changed"):
        inspect_run(path)
