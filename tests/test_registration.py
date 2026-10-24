"""Native registration numerics and strict coordinate contracts."""

import os
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.execution import execute_plan
from dmri_studio.core.scientific import ImportRequest, Task, import_artifact
from dmri_studio.core.scientific.registration import prepare_registration
from dmri_studio.core.spatial import (
    ImageSpace,
    chain,
    load_transform,
    save_transform,
    transform_record,
)

FSL = Path(os.environ.get("FSLDIR", "/usr/local/fsl"))
MR = Path("/usr/local/mrtrix3/bin")


def image(path, values, affine):
    im = nib.Nifti1Image(values, affine)
    im.header.set_xyzt_units("mm")
    im.set_qform(affine, 1)
    im.set_sform(affine, 1)
    nib.save(im, path)
    return path


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("FSLDIR", str(FSL))
    src = tmp_path / "inputs"
    src.mkdir()
    out = tmp_path / "outputs"
    out.mkdir()
    affine = np.diag([-2.0, 2.0, 2.0, 1.0])
    affine[:3, 3] = [30, -20, -20]
    data = np.zeros((20, 20, 20), dtype="f4")
    data[4:9, 5:11, 6:12] = 1
    data[12:15, 12:15, 8:11] = 3
    a = image(src / "a.nii.gz", data, affine)
    b = image(src / "b.nii.gz", data, affine)
    s = ImageSpace.create(a, "T1_NATIVE", "native-t1", "synthetic", "acq1")
    t = ImageSpace.create(b, "DWI_NATIVE", "native-dwi", "synthetic", "acq1")
    mat = np.eye(4)
    mat[0, 3] = 4
    np.savetxt(src / "a-b.mat", mat)
    record = transform_record(
        src / "a-b.mat",
        s,
        t,
        "fsl_flirt",
        software="controlled fixture",
        version="1",
        evidence="Known FSL scaled-mm positive x translation, not a real participant",
    )
    save_transform(src / "transform.json", record)

    def artifact(path, kind, frame):
        return import_artifact(
            ImportRequest(
                kind,
                path,
                declarations=dict(
                    space_kind=frame.kind,
                    space_id=frame.id,
                    subject_id=frame.subject,
                    acquisition_id=frame.acquisition,
                    anatomical_modality="T1w",
                    reference_history="Controlled synthetic reference",
                    whole_head=True,
                ),
            )
        )

    return dict(
        src=src,
        out=out,
        a=artifact(a, "anatomy", s),
        b=artifact(b, "anatomy", t),
        s=s,
        t=t,
        transform=import_artifact(ImportRequest("spatial_transform", src / "transform.json")),
    )


def run(f, method, ports, params=None, name=None):
    task = Task("reg", "registration." + method, {k: v.id for k, v in ports.items()}, params or {})
    plan = prepare_registration(
        tuple({a.id: a for a in ports.values()}.values()),
        (task,),
        f["out"] / (name or method),
        tool_directory=MR,
    )
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    assert result["inputs_unchanged"]
    return Path(plan.payload["output_directory"]), result


def test_native_flirt_apply_convert_and_inverse(fixture):
    f = fixture
    folder, _ = run(
        f, "apply.scalar", {"moving": f["a"], "reference": f["b"], "transform": f["transform"]}
    )
    values = np.asanyarray(nib.load(folder / "registered.nii.gz").dataobj)
    original = np.asanyarray(nib.load(f["s"].image).dataobj)
    expected = np.zeros_like(original)
    expected[2:] = original[:-2]
    np.testing.assert_array_equal(values, expected)
    converted, _ = run(f, "convert", {"transform": f["transform"]})
    tr = import_artifact(ImportRequest("spatial_transform", converted / "transform.json"))
    output, _ = run(
        f, "apply.scalar", {"moving": f["a"], "reference": f["b"], "transform": tr}, name="mr-apply"
    )
    np.testing.assert_array_equal(nib.load(output / "registered.nii.gz").get_fdata(), values)
    inverse, _ = run(f, "invert", {"transform": f["transform"]})
    rec = load_transform(inverse / "transform.json")
    assert rec["source"] == f["t"].to_dict()
    np.testing.assert_allclose(
        np.loadtxt(rec["file"]), np.linalg.inv(np.loadtxt(f["src"] / "a-b.mat"))
    )


def test_native_atlas_labels_and_loss(fixture):
    f = fixture
    from dmri_studio.core.runs import inspect_run

    source = nib.load(f["s"].image)
    labels = np.asarray(source.dataobj, dtype="i2") * 7
    labels[19, 0, 0] = 99
    atlas = image(f["src"] / "atlas.nii.gz", labels, source.affine)
    from dmri_studio.core.execution import _publish_json, hash_file

    review = f["src"] / "space.json"
    _publish_json(
        review,
        dict(
            schema="tractosphere.space-association/1",
            image_sha256=hash_file(atlas),
            reference_sha256=f["s"].sha256,
            status="reviewed",
            review_notes="Controlled exact synthetic grid",
        ),
    )
    d = dict(f["a"].to_dict()["declarations"], space_review=str(review))
    art = import_artifact(ImportRequest("atlas_labels", atlas, declarations=d))
    folder, result = run(
        f, "apply.labels", {"moving": art, "reference": f["b"], "transform": f["transform"]}
    )
    stats = result["validation"]["checks"]["registered.nii.gz"]
    assert stats["lost_labels"] == [99]
    assert set(np.unique(nib.load(folder / "registered.nii.gz").get_fdata())) == {0, 7, 21}
    historical = inspect_run(folder / "manifest.json")
    assert historical["products"][0]["categorical"] is True
    assert historical["products"][0]["sha256"] == result["outputs"]["registered.nii.gz"]["sha256"]
    assert historical["products"][0]["reference_sha256"] == result["plan"]["reference"]["sha256"]


def test_wrong_direction_and_missing_space_review(fixture):
    f = fixture
    with pytest.raises(ValueError, match="source space|reference"):
        prepare_registration(
            (f["a"], f["b"], f["transform"]),
            (
                Task(
                    "reg",
                    "registration.apply.scalar",
                    dict(moving=f["b"].id, reference=f["a"].id, transform=f["transform"].id),
                    {},
                ),
            ),
            f["out"] / "bad",
        )
    record = f["transform"].to_dict()["metadata"]
    with pytest.raises(ValueError, match="intermediate"):
        chain([record, record])
    (f["src"] / "a-b.mat").write_text("0 0 0 0\n" * 4)
    with pytest.raises(ValueError, match="changed"):
        load_transform(f["src"] / "transform.json")


def test_actual_rigid_estimation(fixture):
    f = fixture
    _, result = run(
        f, "flirt", {"moving": f["a"], "reference": f["b"]}, {"model": "rigid", "cost": "normcorr"}
    )
    mat = np.asarray(result["validation"]["checks"]["transform.mat"]["matrix"])
    np.testing.assert_allclose(mat[:3, :3].T @ mat[:3, :3], np.eye(3), atol=1e-5)
    assert result["validation"]["scientific_acceptance"] == "unassigned"


def test_native_warp_composition_inverse(fixture):
    f = fixture
    # Compose a known field with a second affine, with exact middle-image identity.
    import subprocess

    field = f["src"] / "field.nii.gz"
    subprocess.run(
        [
            str(FSL / "bin/convertwarp"),
            f"--ref={f['t'].image}",
            f"--premat={f['src'] / 'a-b.mat'}",
            f"--out={field}",
            "--relout",
        ],
        check=True,
        capture_output=True,
    )
    save_transform(
        f["src"] / "warp.json",
        transform_record(
            field,
            f["s"],
            f["t"],
            "fsl_relative",
            software="FSL",
            version="6.0.7.18",
            evidence="Known synthetic affine converted to field",
        ),
    )
    warp = import_artifact(ImportRequest("spatial_transform", f["src"] / "warp.json"))
    output, _ = run(
        f, "apply.scalar", dict(moving=f["a"], reference=f["b"], transform=warp), name="warp-apply"
    )
    direct, _ = run(
        f,
        "apply.scalar",
        dict(moving=f["a"], reference=f["b"], transform=f["transform"]),
        name="linear-apply",
    )
    np.testing.assert_allclose(
        nib.load(output / "registered.nii.gz").get_fdata(),
        nib.load(direct / "registered.nii.gz").get_fdata(),
        atol=1e-5,
    )
    inv, _ = run(f, "invert", dict(transform=warp), name="warp-inverse")
    assert load_transform(inv / "transform.json")["format"] == "fsl_relative"


def test_registry_batch_and_cli():
    from dmri_studio.core.batch.planning import compiler
    from dmri_studio.core.scientific.registry import ALGORITHMS

    assert compiler("registration") is prepare_registration
    from types import SimpleNamespace

    from dmri_studio.core.batch.planning import resources

    metadata = SimpleNamespace(
        to_dict=lambda: dict(kind="anatomy", geometry=dict(shape=[160, 160, 160]), sources=[])
    )
    budget = resources(
        dict(adapter="registration", tasks=[dict(algorithm="registration.fnirt")]), [metadata]
    )
    assert budget["memory_bytes"] >= 160**3 * 1500
    assert len([k for k in ALGORITHMS if k.startswith("registration.")]) == 12


def test_gradient_selected_reference_not_volume_zero(fixture):
    f = fixture
    source = nib.load(f["s"].image)
    # Volume zero is diffusion weighted; low-b measurements are at indices 1 and 3.
    base = np.asarray(source.dataobj) + 10
    data = np.stack([base * 2, base * 3, base * 5, base * 7], axis=-1).astype("f4")
    dwi = image(f["src"] / "dwi.nii.gz", data, source.affine)
    (f["src"] / "bval").write_text("1000 0 1000 20")
    (f["src"] / "bvec").write_text("1 0 0 0\n0 0 1 0\n0 0 0 0")
    a = import_artifact(
        ImportRequest(
            "dwi",
            dwi,
            bval=f["src"] / "bval",
            bvec=f["src"] / "bvec",
            declarations=dict(
                subject_id="synthetic",
                session_id="01",
                acquisition_id="acq1",
                space_id="native-dwi",
                processing_state="preprocessed",
                reference_history="Controlled processed fixture; no correction inferred",
            ),
        )
    )
    folder, result = run(
        f, "reference", {"dwi": a}, {"b0_threshold": 50, "selection": "mean_low_b"}
    )
    assert result["validation"]["checks"]["selected_indices"] == [1, 3]
    np.testing.assert_allclose(nib.load(folder / "mean-b0.nii.gz").get_fdata(), base * 5)
    from dmri_studio.core.scientific.contracts import Artifact

    reference = Artifact(result["validation"]["products"][0]["artifact"])
    assert reference.to_dict()["metadata"]["reference_record"]["dwi"]["id"] == a.id
    f["derived_reference"] = reference


def test_atlas_qc_full_range_review_stale(fixture):
    from dmri_studio.core.execution import _publish_json, hash_file
    from dmri_studio.core.registration_qc import (
        label_rgba,
        record_decision,
        review_status,
        verify_bundle,
    )

    test_gradient_selected_reference_not_volume_zero(fixture)
    f = fixture
    reference = f["derived_reference"]
    labels = np.zeros((20, 20, 20), dtype="i2")
    labels[2:5, 3:8, 4:9] = 7
    labels[12:15, 8:12, 10:15] = 41
    path = image(f["src"] / "registered-atlas.nii.gz", labels, nib.load(f["t"].image).affine)
    association = f["src"] / "atlas-qc-association.json"
    _publish_json(
        association,
        dict(
            schema="tractosphere.registered-atlas-qc-association/1",
            sha256=hash_file(path),
            reference_sha256=hash_file(Path(reference.payload["path"])),
            registration_reference=f["t"].image,
            registration_reference_sha256=f["t"].sha256,
            status="reviewed",
            review_notes="Controlled atlas and processed reference occupy known synthetic world geometry",
            evidence_hashes={
                str(f["src"] / "transform.json"): hash_file(f["src"] / "transform.json")
            },
        ),
    )
    dec = dict(reference.to_dict()["declarations"])
    dec.pop("reference_record")
    dec["qc_association"] = str(association)
    atlas = import_artifact(ImportRequest("atlas_labels", path, declarations=dec))
    folder, result = run(f, "qc", dict(atlas=atlas, reference=reference))
    bundle, hashes = verify_bundle(folder / "atlas-qc.json")
    assert len(bundle["slices"]) == 60
    assert set(bundle["technical"]["label_distribution"]) == {"0", "7", "41"}
    assert review_status(folder / "atlas-qc.json") == "NEEDS_REVIEW"
    record_decision(
        folder / "atlas-qc.json",
        "APPROVED",
        "synthetic test reviewer",
        "Only controlled synthetic geometry accepted for this test",
    )
    assert review_status(folder / "atlas-qc.json") == "APPROVED"
    pixels = label_rgba(labels[:, :, 5], 0.3, [7], True)
    assert pixels[..., 3].max() == round(0.3 * 255)
    assert np.unique(labels).tolist() == [0, 7, 41]
    (folder / bundle["images"][0]["path"]).write_bytes(b"controlled corruption")
    assert review_status(folder / "atlas-qc.json") == "STALE_EVIDENCE"


def test_actual_fnirt_and_itk_text_import(fixture):
    f = fixture
    folder, result = run(
        f,
        "fnirt",
        dict(moving=f["a"], reference=f["b"], initial=f["transform"]),
        dict(iterations=1, warp_resolution=10),
    )
    rec = load_transform(folder / "transform.json")
    assert rec["format"] == "fsl_coefficients"
    assert "jacobian.nii.gz" in result["validation"]["checks"]
    text = f["src"] / "itk.txt"
    text.write_text(
        "#Insight Transform File V1.0\n#Transform 0\nTransform: AffineTransform_double_3_3\nParameters: 1 0 0 0 1 0 0 0 1 4 0 0\nFixedParameters: 3 2 1\n"
    )
    save_transform(
        f["src"] / "itk.json",
        transform_record(
            text,
            f["s"],
            f["t"],
            "itk_affine_text",
            software="ITK fixture",
            version="1",
            evidence="Known sampling LPS affine",
        ),
    )
    art = import_artifact(ImportRequest("spatial_transform", f["src"] / "itk.json"))
    out, _ = run(f, "convert", dict(transform=art), name="itk-convert")
    m = np.asarray(load_transform(out / "transform.json")["inspection"]["matrix"])
    assert abs(m[0, 3]) == 4


def test_probability_channels_preserved_without_thresholding(fixture):
    from dmri_studio.core.execution import _publish_json, hash_file

    f = fixture
    scalar = nib.load(f["s"].image).get_fdata().astype("f4") / 3
    path = image(
        f["src"] / "probability.nii.gz",
        np.stack([scalar * 0.3, scalar * 0.7], axis=-1),
        nib.load(f["s"].image).affine,
    )
    review = f["src"] / "probability-space.json"
    _publish_json(
        review,
        dict(
            schema="tractosphere.space-association/1",
            image_sha256=hash_file(path),
            reference_sha256=f["s"].sha256,
            status="reviewed",
            review_notes="Known synthetic probability image in the original T1 grid",
        ),
    )
    art = import_artifact(
        ImportRequest(
            "atlas_probability",
            path,
            declarations={
                **f["a"].to_dict()["declarations"],
                "threshold": 0.4,
                "space_review": str(review),
            },
        )
    )
    folder, result = run(
        f, "apply.probability", dict(moving=art, reference=f["b"], transform=f["transform"])
    )
    actual = nib.load(folder / "registered.nii.gz").get_fdata()
    expected = np.zeros((*scalar.shape, 2))
    expected[2:] = np.stack([scalar * 0.3, scalar * 0.7], axis=-1)[:-2]
    np.testing.assert_allclose(actual, expected, atol=1e-6)
    assert result["validation"]["checks"]["registered.nii.gz"]["thresholding"] == "not performed"


def test_transform_record_cross_subject_session_and_units(fixture):
    import json

    f = fixture
    record = json.loads((f["src"] / "transform.json").read_text())
    record["target"]["subject"] = "other-participant"
    bad = f["src"] / "cross-subject.json"
    bad.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="subject"):
        load_transform(bad)
    original = nib.load(f["s"].image)
    original.header.set_xyzt_units("meter")
    path = f["src"] / "meters.nii.gz"
    nib.save(original, path)
    with pytest.raises(ValueError, match="millimeter"):
        ImageSpace.create(path, "T1_NATIVE", "meters", "synthetic", "acq1")


def test_oblique_affine_application_and_composition(fixture):
    f = fixture
    angle = np.deg2rad(23)
    rotation = np.array(
        [[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]]
    )
    aff = np.eye(4)
    aff[:3, :3] = rotation @ np.diag([-1.2, 2.0, 2.7])
    aff[:3, 3] = [15, -22, 8]
    values = np.zeros((20, 20, 20), dtype="f4")
    values[4:7, 8:12, 5:9] = 17
    a = image(f["src"] / "oblique-a.nii.gz", values, aff)
    b = image(f["src"] / "oblique-b.nii.gz", values, aff)
    c = image(f["src"] / "oblique-c.nii.gz", values, aff)
    spaces = [
        ImageSpace.create(p, "OTHER_REGISTERED_SPACE", f"oblique-{i}", "synthetic", "acq1")
        for i, p in enumerate((a, b, c))
    ]
    m1 = np.eye(4)
    m1[0, 3] = 2.4
    m2 = np.eye(4)
    m2[1, 3] = 2
    transforms = []
    for i, m in enumerate((m1, m2)):
        path = f["src"] / f"oblique-{i}.mat"
        np.savetxt(path, m)
        record = f["src"] / f"oblique-{i}.json"
        save_transform(
            record,
            transform_record(
                path,
                spaces[i],
                spaces[i + 1],
                "fsl_flirt",
                software="Controlled FSL fixture",
                version="1",
                evidence="Known scaled-voxel translations on an oblique anisotropic grid",
            ),
        )
        transforms.append(import_artifact(ImportRequest("spatial_transform", record)))
    folder, _ = run(
        f, "compose", dict(first=transforms[0], second=transforms[1]), name="oblique-compose"
    )
    record = load_transform(folder / "transform.json")
    np.testing.assert_allclose(np.loadtxt(record["file"]), m2 @ m1, atol=1e-6)
    artifacts = []
    for path, space in ((a, spaces[0]), (c, spaces[2])):
        artifacts.append(
            import_artifact(
                ImportRequest(
                    "anatomy",
                    path,
                    declarations=dict(
                        space_kind=space.kind,
                        space_id=space.id,
                        subject_id=space.subject,
                        acquisition_id=space.acquisition,
                    ),
                )
            )
        )
    transform = import_artifact(ImportRequest("spatial_transform", folder / "transform.json"))
    out, _ = run(
        f,
        "apply.scalar",
        dict(moving=artifacts[0], reference=artifacts[1], transform=transform),
        name="oblique-result",
    )
    expected = np.zeros_like(values)
    expected[2:, 1:] = values[:-2, :-1]
    np.testing.assert_allclose(nib.load(out / "registered.nii.gz").get_fdata(), expected, atol=1e-4)


def test_actual_epi_reg_without_fieldmap(fixture):
    from dmri_studio.core.execution import _publish_json, hash_file

    f = fixture
    d = dict(f["b"].to_dict()["declarations"], anatomical_modality="b0")
    moving = import_artifact(ImportRequest("anatomy", Path(f["t"].image), declarations=d))
    reference = f["a"]
    im = nib.load(f["s"].image)
    brain_path = image(f["src"] / "t1_brain.nii.gz", im.get_fdata(), im.affine)
    brain_evidence = f["src"] / "t1-brain-space.json"
    _publish_json(
        brain_evidence,
        dict(
            schema="tractosphere.space-association/1",
            image_sha256=hash_file(brain_path),
            reference_sha256=f["s"].sha256,
            status="reviewed",
            review_notes="Controlled synthetic brain-extracted input for adapter contract",
        ),
    )
    t1_brain = import_artifact(
        ImportRequest(
            "anatomy",
            brain_path,
            declarations={
                **reference.to_dict()["declarations"],
                "space_review": str(brain_evidence),
                "anatomical_preparation": "brain_extracted",
            },
        )
    )
    mask = image(f["src"] / "wmseg.nii.gz", (im.get_fdata() > 0).astype("u1"), im.affine)
    evidence = f["src"] / "wm-space.json"
    _publish_json(
        evidence,
        dict(
            schema="tractosphere.space-association/1",
            image_sha256=hash_file(mask),
            reference_sha256=f["s"].sha256,
            status="reviewed",
            review_notes="Controlled synthetic white-matter mask, not a human segmentation",
        ),
    )
    wm = import_artifact(
        ImportRequest(
            "mask",
            mask,
            declarations={
                **reference.to_dict()["declarations"],
                "space_review": str(evidence),
                "mask_role": "white_matter_segmentation",
            },
        )
    )
    unreviewed_wm = import_artifact(
        ImportRequest(
            "mask",
            mask,
            declarations={**reference.to_dict()["declarations"], "space_review": str(evidence)},
        )
    )
    with pytest.raises(ValueError, match="white-matter segmentation"):
        prepare_registration(
            (moving, reference, t1_brain, unreviewed_wm),
            (
                Task(
                    "reg",
                    "registration.epi_reg",
                    dict(
                        moving=moving.id,
                        reference=reference.id,
                        t1_brain=t1_brain.id,
                        wmseg=unreviewed_wm.id,
                    ),
                    {},
                ),
            ),
            f["out"] / "unreviewed-wm",
            tool_directory=MR,
        )
    folder, result = run(
        f, "epi_reg", dict(moving=moving, reference=reference, t1_brain=t1_brain, wmseg=wm)
    )
    assert load_transform(folder / "transform.json")["source"]["kind"] == "DWI_NATIVE"
    assert result["validation"]["scientific_acceptance"] == "unassigned"
    assert all("--nofmap" not in c["argv"] for c in result["plan"]["commands"])


def test_new_registration_rejects_same_source_file_with_different_declarations(fixture):
    f = fixture
    original = f["a"]
    duplicate = import_artifact(
        ImportRequest(
            "anatomy",
            Path(original.payload["path"]),
            declarations={
                **original.to_dict()["declarations"],
                "space_id": "incorrectly-redeclared-other-space",
            },
        )
    )
    with pytest.raises(ValueError, match="distinct source data"):
        prepare_registration(
            (original, duplicate),
            (
                Task(
                    "same-source",
                    "registration.flirt",
                    {"moving": original.id, "reference": duplicate.id},
                    {"model": "rigid", "cost": "mutualinfo"},
                ),
            ),
            f["out"] / "same-source",
            tool_directory=MR,
        )


def test_transform_import_rejects_same_image_with_redeclared_space(fixture):
    f = fixture
    duplicate = ImageSpace.create(
        f["s"].image, "DWI_NATIVE", "misdeclared-other-space", "synthetic", "acq1"
    )
    with pytest.raises(ValueError, match="distinct images"):
        transform_record(
            f["src"] / "a-b.mat",
            f["s"],
            duplicate,
            "fsl_flirt",
            software="controlled fixture",
            version="1",
            evidence="Invalid same-image direction",
        )
