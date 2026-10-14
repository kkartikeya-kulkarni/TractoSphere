"""Interrupted native processing resumes only verified stages in new attempts."""

import json
import shutil

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.dataset import DatasetPaths
from dmri_studio.core.execution import execute_plan, prepare_execution
from dmri_studio.core.loading import DatasetLoader
from dmri_studio.core.runs import inspect_run, load_verified_product
from dmri_studio.core.validation import ValidationPolicy
from dmri_studio.core.workflow import STAGES, HistoryClaim, ProcessingHistory


def test_failed_gibbs_recovers_denoise_without_repeating_it(tmp_path, monkeypatch):
    """Actual tiny MRtrix fixture; inject an error BEFORE Gibbs, then continue it."""
    if not all(shutil.which(x) for x in ["mrconvert", "dwidenoise", "mrdegibbs", "mrinfo"]):
        pytest.skip("Local MRtrix unavailable")
    from dmri_studio.core.correction import _acquisition_source
    from dmri_studio.core.native_processing import NativeStep
    from dmri_studio.core.projects import add_acquisition, attach_attempt, new_project

    raw = tmp_path / "raw"
    raw.mkdir()
    rng = np.random.default_rng(312)
    image = nib.Nifti1Image(
        (500 + rng.normal(0, 8, (8, 10, 12, 20))).astype("float32"), np.diag([-2, 2, 2, 1])
    )
    image.header.set_xyzt_units("mm")
    image.header.set_dim_info(slice=2)
    nib.save(image, raw / "data.nii")
    np.savetxt(raw / "bvals", np.array([0] + [1000] * 19)[None])
    v = rng.normal(size=(3, 20))
    v /= np.linalg.norm(v, axis=0)
    v[:, 0] = 0
    np.savetxt(raw / "bvecs", v)
    ds = DatasetLoader().load(DatasetPaths(raw / "data.nii", raw / "bvals", raw / "bvecs")).dataset
    history = ProcessingHistory(
        tuple(
            HistoryClaim(
                s, False, "user_declared", "Synthetic generated input", "high", "2026-10-13"
            )
            for s in STAGES
        ),
        True,
    )
    original_run = NativeStep.run
    project = add_acquisition(
        new_project("Recovery"),
        {k: str(p) for k, p in ds.paths.supplied().items()},
        {"sub": "synthetic", "run": "01"},
        history.to_dict(),
    )
    identifier = project["acquisitions"][0]["id"]

    def fail_gibbs(self, **kwargs):
        if self.name == "gibbs":
            raise RuntimeError("Injected pre-launch failure for recovery test")
        return original_run(self, **kwargs)

    next_ds = None
    try:
        plan = prepare_execution(ds, history, ("denoise", "gibbs"), tmp_path / "first", nthreads=1)
        monkeypatch.setattr(NativeStep, "run", fail_gibbs)
        result = execute_plan(ds, plan, approval=plan.digest)
        assert result["status"] == "failed"
        path = tmp_path / "first/native-manifest.json"
        snapshot = inspect_run(path)
        assert not snapshot["products"] and snapshot["recovery"]
        assert snapshot["verified_hashes"] >= 9
        project = attach_attempt(project, identifier, path)
        recovery = snapshot["recovery"]
        assert recovery["suggested_operations"] == ["gibbs"]
        next_ds = load_verified_product(
            recovery["product"], DatasetLoader(), policy=ValidationPolicy()
        ).dataset
        h = ProcessingHistory.from_dict(recovery["product"]["history"])
        assert h.resolve("denoise") == "done" and h.resolve("gibbs") == "not_done"
        assert _acquisition_source(next_ds, path, lambda: False)[0] == raw / "data.nii"
        monkeypatch.setattr(NativeStep, "run", original_run)
        plan2 = prepare_execution(
            next_ds, h, ("gibbs",), tmp_path / "continued", native_record=path, nthreads=1
        )
        assert all(c["step"] != "denoise" for c in plan2.to_dict()["commands"])
        final = execute_plan(next_ds, plan2, approval=plan2.digest)
        assert final["status"] == "validated_pending_qc", final.get("error")
        assert final["plan"]["original_acquisition"] == str(raw / "data.nii")
        assert json.loads(path.read_text())["status"] == "failed"
        project = attach_attempt(project, identifier, tmp_path / "continued/native-manifest.json")
        assert [a["status"] for a in project["acquisitions"][0]["attempts"]] == [
            "failed",
            "validated_pending_qc",
        ]
        # Changed recovered bytes invalidate the earlier completion evidence.
        with (tmp_path / "first/denoise.nii").open("ab") as stream:
            stream.write(b"changed")
        with pytest.raises(ValueError, match="changed"):
            inspect_run(path)
    finally:
        if next_ds:
            next_ds.close()
        ds.close()
