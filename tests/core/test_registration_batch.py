"""Actual native multi-subject registration→atlas→QC and verified recovery."""

import json
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.batch.discovery import Acquisition, Study
from dmri_studio.core.batch.engine import BatchRunner, control, create_batch
from dmri_studio.core.batch.planning import build_batch_plan
from dmri_studio.core.registration_qc import batch_qc_rows, export_batch_qc, record_decision

pytestmark = pytest.mark.skipif(
    not all((Path("/usr/local/mrtrix3/bin") / name).is_file() for name in ("mrconvert", "mrmath"))
    or not all((Path.home() / "fsl/share/fsl/bin" / name).is_file() for name in ("flirt", "fast")),
    reason="Native MRtrix3 and FSL are required for registration batch integration tests",
)


def fixture(tmp_path):
    root = tmp_path / "inputs"
    root.mkdir()
    acquisitions = []
    for i in range(2):
        directory = root / f"synthetic-{i}"
        directory.mkdir()
        labels = np.zeros((16, 18, 20), dtype="i2")
        labels[3:8, 3:7, 4:12] = 7
        labels[8:12, 9:14, 8:13] = 23
        aff = np.diag([-2.0, 2.0, 2.0, 1.0])
        aff[:3, 3] = [32, -18, -20]
        for name, data in [
            ("atlas.nii.gz", labels),
            ("b0.nii.gz", (labels + 1).astype("f4")),
            (
                "dwi.nii.gz",
                np.stack([labels + 1, labels + 2, labels + 3, labels + 4], axis=-1).astype("f4"),
            ),
        ]:
            im = nib.Nifti1Image(data, aff)
            im.header.set_xyzt_units("mm")
            im.set_qform(aff, 1)
            im.set_sform(aff, 1)
            nib.save(im, directory / name)
        (directory / "bval").write_text("1000 0 1000 0")
        (directory / "bvec").write_text("1 0 0 0\n0 0 1 0\n0 0 0 0")
        acquisitions.append(
            Acquisition(
                f"synthetic-{i}",
                "01",
                "diffusion",
                dict(
                    dwi=str(directory / "dwi.nii.gz"),
                    bval=str(directory / "bval"),
                    bvec=str(directory / "bvec"),
                    t1=str(directory / "atlas.nii.gz"),
                    atlas=str(directory / "atlas.nii.gz"),
                    reference=str(directory / "b0.nii.gz"),
                ),
                dict(synthetic=True),
                dict(
                    space_id="dwi",
                    processing_state="preprocessed",
                    reference_history="Controlled synthetic processed fixture",
                ),
            )
        )

    def task(method, inputs, params=None):
        return dict(
            id="reg",
            algorithm="registration." + method,
            inputs={k: {"artifact_id": v} for k, v in inputs.items()},
            parameters=params or {},
        )

    reference = dict(node="reference", output="mean-b0.nii.gz", kind="anatomy")
    moving = dict(
        role="t1",
        kind="anatomy",
        declarations=dict(space_id="t1", space_kind="T1_NATIVE", anatomical_modality="T1w"),
    )
    atlas = dict(
        role="atlas", kind="atlas_labels", declarations=dict(space_id="t1", space_kind="T1_NATIVE")
    )
    nodes = [
        dict(
            id="reference",
            adapter="registration",
            imports=dict(d=dict(role="dwi", kind="dwi", bval_role="bval", bvec_role="bvec")),
            tasks=[task("reference", dict(dwi="d"), dict(b0_threshold=50, selection="mean_low_b"))],
        ),
        dict(
            id="estimate",
            adapter="registration",
            depends_on=["reference"],
            imports=dict(a=moving, b=reference),
            tasks=[
                task("flirt", dict(moving="a", reference="b"), dict(model="rigid", cost="normmi"))
            ],
        ),
        dict(
            id="atlas",
            adapter="registration",
            depends_on=["reference", "estimate"],
            imports=dict(
                a=atlas,
                b=reference,
                t=dict(node="estimate", output="transform.json", kind="spatial_transform"),
            ),
            tasks=[task("apply.labels", dict(moving="a", reference="b", transform="t"))],
        ),
        dict(
            id="qc",
            adapter="registration",
            depends_on=["atlas", "reference"],
            imports=dict(
                a=dict(node="atlas", output="registered.nii.gz", kind="atlas_labels"), b=reference
            ),
            tasks=[task("qc", dict(atlas="a", reference="b"))],
        ),
    ]
    return Study("synthetic-registration", str(root), tuple(acquisitions)), dict(nodes=nodes)


def test_actual_batch_registration_qc_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("FSLDIR", str(Path.home() / "fsl"))
    study, workflow = fixture(tmp_path)
    plan = build_batch_plan(
        study,
        workflow,
        tmp_path / "batch",
        tool_directory=Path("/usr/local/mrtrix3/bin"),
        limits={"disk_headroom_bytes": 1024**2},
    )
    folder = create_batch(plan, plan.digest)
    result = BatchRunner(folder).run()
    assert result["state"] == "COMPLETED", [
        (k, r["status"], r.get("error")) for k, r in result["nodes"].items()
    ]
    assert len(result["nodes"]) == 8
    rows = batch_qc_rows(folder)
    qc = [r for r in rows if r["qc_bundle"]]
    assert len(qc) == 2 and all(r["review"] == "NEEDS_REVIEW" for r in qc)
    record_decision(
        qc[0]["qc_bundle"], "REJECTED", "Controlled test reviewer", "Synthetic review behavior only"
    )
    assert any(r["review"] == "REJECTED" for r in batch_qc_rows(folder))
    report = export_batch_qc(folder, tmp_path / "portable")
    assert report.is_file()
    assert len(list((tmp_path / "portable").glob("case-*/images/*.png"))) > 0
    resumed = BatchRunner(folder).run()
    assert all(
        r["status"] == "SKIPPED_VERIFIED" and len(r["attempts"]) == 1
        for r in resumed["nodes"].values()
    ), [(r["status"], r.get("error")) for r in resumed["nodes"].values()]


def test_registration_missing_input_isolation_and_cross_subject_transform(tmp_path, monkeypatch):
    import pytest

    from dmri_studio.core.batch.planning import requests
    from dmri_studio.core.spatial import ImageSpace, save_transform, transform_record

    monkeypatch.setenv("FSLDIR", str(Path.home() / "fsl"))
    study, workflow = fixture(tmp_path)
    Path(study.acquisitions[0].paths["bvec"]).unlink()
    plan = build_batch_plan(
        study, workflow, tmp_path / "missing-batch", tool_directory=Path("/usr/local/mrtrix3/bin")
    )
    rows = plan.to_dict()["rows"]
    assert any(
        r["status"] == "BLOCKED" and r["acquisition"]["subject_id"] == "synthetic-0" for r in rows
    )
    assert any(
        r["status"] == "ELIGIBLE" and r["acquisition"]["subject_id"] == "synthetic-1" for r in rows
    )
    good = study.acquisitions[1]
    image = Path(good.paths["t1"])
    matrix = image.parent / "known.mat"
    np.savetxt(matrix, np.eye(4))
    source = ImageSpace.create(image, "T1_NATIVE", "t1", "synthetic-1", "diffusion", "01")
    target = ImageSpace.create(
        image.parent / "b0.nii.gz", "DWI_NATIVE", "dwi", "synthetic-1", "diffusion", "01"
    )
    path = image.parent / "transform.json"
    save_transform(
        path,
        transform_record(
            matrix,
            source,
            target,
            "fsl_flirt",
            software="Synthetic test",
            version="1",
            evidence="Known identity for controlled test ONLY",
        ),
    )
    bad_acquisition = Acquisition(
        "synthetic-0", "01", "diffusion", dict(transform=str(path)), {}, {}
    )
    node = dict(
        id="invert",
        adapter="registration",
        imports=dict(t=dict(role="transform", kind="spatial_transform")),
        tasks=[
            dict(
                id="invert",
                algorithm="registration.invert",
                inputs=dict(transform=dict(artifact_id="t")),
                parameters={},
            )
        ],
    )
    with pytest.raises(ValueError, match="subject|participant"):
        requests(bad_acquisition, node, Path(study.root))
    for session, acquisition_id, message in (
        ("02", "diffusion", "session"),
        ("01", "other-acquisition", "acquisition"),
    ):
        wrong = Acquisition("synthetic-1", session, acquisition_id, dict(transform=str(path)))
        with pytest.raises(ValueError, match=message):
            requests(wrong, node, Path(study.root))


def test_actual_empty_atlas_failure_isolated(tmp_path, monkeypatch):
    """Actual FLIRT produces an empty out-of-FOV atlas; it cannot publish success."""
    from dmri_studio.core.spatial import ImageSpace, save_transform, transform_record

    monkeypatch.setenv("FSLDIR", str(Path.home() / "fsl"))
    study, _ = fixture(tmp_path)
    acquisitions = []
    for i, a in enumerate(study.acquisitions):
        path = Path(a.paths["atlas"])
        source = ImageSpace.create(
            path, "T1_NATIVE", "t1", a.subject_id, a.acquisition_id, a.session_id
        )
        target = ImageSpace.create(
            path.parent / "b0.nii.gz",
            "DWI_NATIVE",
            "dwi",
            a.subject_id,
            a.acquisition_id,
            a.session_id,
        )
        m = np.eye(4)
        m[0, 3] = 1000 if i == 0 else 2
        matrix = path.parent / "transform.mat"
        np.savetxt(matrix, m)
        record = path.parent / "transform.json"
        save_transform(
            record,
            transform_record(
                matrix,
                source,
                target,
                "fsl_flirt",
                software="Controlled fixture",
                version="1",
                evidence="Known out-of-field translation tests technical failure isolation",
            ),
        )
        acquisitions.append(
            Acquisition(
                a.subject_id,
                a.session_id,
                a.acquisition_id,
                {**a.paths, "transform": str(record)},
                a.metadata,
                a.declarations,
            )
        )
    study = Study(study.study_id, study.root, tuple(acquisitions))
    workflow = dict(
        nodes=[
            dict(
                id="atlas",
                adapter="registration",
                imports=dict(
                    a=dict(
                        role="atlas",
                        kind="atlas_labels",
                        declarations=dict(space_kind="T1_NATIVE", space_id="t1"),
                    ),
                    b=dict(
                        role="reference",
                        kind="anatomy",
                        declarations=dict(space_kind="DWI_NATIVE", space_id="dwi"),
                    ),
                    t=dict(role="transform", kind="spatial_transform"),
                ),
                tasks=[
                    dict(
                        id="reg",
                        algorithm="registration.apply.labels",
                        inputs={
                            k: dict(artifact_id=v)
                            for k, v in dict(moving="a", reference="b", transform="t").items()
                        },
                        parameters={},
                    )
                ],
            )
        ]
    )
    plan = build_batch_plan(
        study, workflow, tmp_path / "fail-batch", tool_directory=Path("/usr/local/mrtrix3/bin")
    )
    folder = create_batch(plan, plan.digest)
    result = BatchRunner(folder).run()
    statuses = {r["acquisition"]["subject_id"]: r for r in result["nodes"].values()}
    assert statuses["synthetic-0"]["status"] == "FAILED", statuses
    assert statuses["synthetic-1"]["status"] == "COMPLETED", statuses
    failed = statuses["synthetic-0"]
    manifest = json.loads(Path(failed["manifest"]).read_text())
    assert manifest["steps"][0]["return_code"] == 0
    assert (Path(failed["manifest"]).parent / "registered.nii.gz").is_file()
    assert not np.any(nib.load(Path(failed["manifest"]).parent / "registered.nii.gz").get_fdata())
    # Retry never overwrites the retained failed attempt or reruns the other subject.
    key = next(
        k for k, r in result["nodes"].items() if r["acquisition"]["subject_id"] == "synthetic-0"
    )
    control(folder, "retry", [key])
    retried = BatchRunner(folder).run()
    assert len(retried["nodes"][key]["attempts"]) == 2
    assert [r["status"] for r in retried["nodes"].values()].count("SKIPPED_VERIFIED") == 1
