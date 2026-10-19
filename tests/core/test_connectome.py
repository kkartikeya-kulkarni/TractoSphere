"""Known physical endpoint/length/scalar native MRtrix tests, no upstream jobs."""

import subprocess
from pathlib import Path
from threading import Event

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.execution import execute_plan, hash_file
from dmri_studio.core.runs import inspect_run
from dmri_studio.core.scientific import ImportRequest, Task, import_artifact
from dmri_studio.core.scientific.connectome import (
    load_bundle,
    node_table,
    prepare_connectome,
    read_matrix,
)

BIN = Path("/usr/local/mrtrix3/bin")
requires = pytest.mark.skipif(
    not (BIN / "tck2connectome").is_file(), reason="Actual accepted MRtrix3 required"
)


def save(path, data, affine=None):
    im = nib.Nifti1Image(np.asarray(data, "f4"), np.eye(4) if affine is None else affine)
    im.header.set_xyzt_units("mm")
    im.set_qform(im.affine, 1)
    im.set_sform(im.affine, 1)
    nib.save(im, path)
    return path


@pytest.fixture
def inputs(tmp_path):
    p = tmp_path / "inputs"
    p.mkdir()
    decl = dict(
        subject_id="analytic",
        space_id="known-physical",
        registration_reviewed=True,
        alignment_evidence="Analytic same-space fixture with exact known endpoints",
        metric="FA",
    )
    a = np.zeros((12, 5, 5))
    a[1:3] = 1
    a[8:10] = 3
    tracks = [
        np.array([[2, 2, 2], [5, 2, 2], [8, 2, 2]], "f4"),
        np.array([[8, 2, 2], [2, 2, 2]], "f4"),
        np.array([[1, 2, 2], [2, 2, 2]], "f4"),
        np.array([[0, 2, 2], [8, 2, 2]], "f4"),
    ]
    nib.streamlines.save(
        nib.streamlines.Tractogram(tracks, affine_to_rasmm=np.eye(4)), p / "tracks.tck"
    )
    paths = {
        "tracts": ("tractogram", p / "tracks.tck"),
        "nodes": ("atlas_labels", save(p / "nodes.nii", a)),
        "reference": ("anatomy", save(p / "reference.nii", np.ones(a.shape))),
        "fa": ("anatomy", save(p / "fa.nii", np.full(a.shape, 0.5))),
    }
    imported = {
        k: import_artifact(ImportRequest(kind, path, declarations=decl))
        for k, (kind, path) in paths.items()
    }

    from dmri_studio.core.registration_review import diagnostic_override

    diagnostic = diagnostic_override(
        imported["nodes"],
        imported["reference"],
        reviewer="controlled fixture",
        reason="Known analytic geometry; test numerical aggregation only",
    )
    imported["nodes"] = import_artifact(
        ImportRequest(
            "atlas_labels",
            paths["nodes"][1],
            declarations={**decl, "registration_diagnostic": diagnostic},
        )
    )
    return imported


def renew_diagnostic(items):
    """Explicitly reauthorize newly constructed analytic data, never production data."""
    from dmri_studio.core.registration_review import diagnostic_override

    req = items["nodes"].to_dict()["request"]
    req["declarations"]["registration_diagnostic"] = diagnostic_override(
        items["nodes"],
        items["reference"],
        reviewer="controlled fixture",
        reason="New known analytic geometry; verify unchanged numerical expectations",
    )
    items["nodes"] = import_artifact(ImportRequest(**req))


def task(inputs, **kw):
    params = dict(
        metrics=["count", "mean_length", "mean_fa"],
        assignment="end_voxels",
        symmetric="yes",
        zero_diagonal="no",
    )
    params.update(kw)
    return Task("matrix", "tck2connectome", {k: a.id for k, a in inputs.items()}, params)


def run(inputs, out, **params):
    plan = prepare_connectome(
        tuple(inputs.values()), (task(inputs, **params),), out, tool_directory=BIN
    )
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    return plan, result, load_bundle(out / "matrix-connectome.json")


@requires
@pytest.mark.parametrize("symmetric", ["yes", "no"])
@pytest.mark.parametrize("zero_diagonal", ["yes", "no"])
def test_native_metrics_exact_endpoint_semantics(tmp_path, inputs, symmetric, zero_diagonal):
    before = [hash_file(Path(a.payload["path"])) for a in inputs.values()]
    plan, r, b = run(inputs, tmp_path / "run", symmetric=symmetric, zero_diagonal=zero_diagonal)
    counts = read_matrix(b["matrices"]["count"]["path"])
    length = read_matrix(b["matrices"]["mean_length"]["path"])
    fa = read_matrix(b["matrices"]["mean_fa"]["path"])
    assert (
        counts.shape == (3, 3)
        and counts[0, 2] == 2
        and counts[0, 0] == (1 if zero_diagonal == "no" else 0)
    )
    assert counts[2, 0] == (2 if symmetric == "yes" else 0)
    assert np.all(counts[1] == 0) and np.all(counts[:, 1] == 0)
    assert length[0, 2] == pytest.approx(6) and fa[0, 2] == pytest.approx(0.5)
    assert b["qc"]["assigned_streamlines"] == 3 and b["qc"]["unassigned_streamlines"] == 1
    assert b["qc"]["missing_node_ids"] == [2] and b["nodes"][2]["parcel_id"] == 3
    assert b["qc"]["self_connections_before_diagonal_policy"] == 1
    assert [hash_file(Path(a.payload["path"])) for a in inputs.values()] == before
    assert inspect_run(tmp_path / "run/manifest.json")["products"][0]["kind"] == "connectome"
    from dmri_studio.core.qc import load_catalog

    catalog = load_catalog(tmp_path / "run/qc-catalog.json").to_dict()
    assert catalog["stages"][0]["automated_qc"] == "completed_with_findings"
    # Independent actual commands on identical sources and explicit policies.
    for command in plan.to_dict()["commands"]:
        if command["kind"] != "matrix":
            continue
        args = [
            str(x).replace(str(tmp_path / "run"), str(tmp_path / "independent"))
            for x in command["argv"]
        ]
        (tmp_path / "independent").mkdir(exist_ok=True)
        # Mean FA reference intentionally reuses the exact verified scalar vector.
        if "-scale_file" in args:
            args[args.index("-scale_file") + 1] = str(tmp_path / "run/matrix-fa-per-streamline.txt")
        subprocess.run(args, check=True, capture_output=True)
        np.testing.assert_allclose(
            read_matrix(args[3]), read_matrix(command["argv"][3]), rtol=0, atol=0
        )


@requires
@pytest.mark.parametrize(
    "method,extra",
    [
        ("radial_search", {"radial_search_mm": 4}),
        ("reverse_search", {"search_mm": 0}),
        ("forward_search", {"search_mm": 2}),
    ],
)
def test_actual_assignment_variants(tmp_path, inputs, method, extra):
    _, _, b = run(inputs, tmp_path / "run", assignment=method, **extra)
    assert b["qc"]["assignment"] == method
    assert b["qc"]["assigned_streamlines"] + b["qc"]["unassigned_streamlines"] == 4


@requires
def test_edge_extract_exact_original_coordinates(tmp_path, inputs):
    _, _, b = run(inputs, tmp_path / "run")
    artifact = import_artifact(
        ImportRequest("connectome_matrix", tmp_path / "run/matrix-connectome.json")
    )
    for a, c, count in [(1, 3, 2), (1, 1, 1)]:
        t = Task("edge", "connectome2tck", {"connectome": artifact.id}, {"node_a": a, "node_b": c})
        p = prepare_connectome((artifact,), (t,), tmp_path / f"edge{a}-{c}", tool_directory=BIN)
        r = execute_plan(None, p, approval=p.digest)
        assert r["status"] == "validated_pending_qc", r.get("error")
        assert r["validation"]["measurements"][0]["streamlines"] == count
    with pytest.raises(ValueError, match="no contributing"):
        prepare_connectome(
            (artifact,),
            (
                Task(
                    "edge",
                    "connectome2tck",
                    {"connectome": artifact.id},
                    {"node_a": 1, "node_b": 2},
                ),
            ),
            tmp_path / "empty",
            tool_directory=BIN,
        )
    Path(b["assignments"]).write_text("1 3\n")
    with pytest.raises(ValueError, match="identity changed"):
        load_bundle(tmp_path / "run/matrix-connectome.json")


@pytest.mark.parametrize("value", [-1, np.nan, 0.2])
def test_invalid_atlas_rejected(tmp_path, value):
    a = np.ones((3, 3, 3))
    a[0, 0, 0] = value
    with pytest.raises(ValueError):
        import_artifact(ImportRequest("atlas_labels", save(tmp_path / "bad.nii", a)))


@requires
@pytest.mark.parametrize(
    "policy",
    [{"radial_search_mm": 4}, {"search_mm": 1}, {"weights": "sift2"}, {"metrics": ["unknown"]}],
)
def test_invalid_parameter_combination(tmp_path, inputs, policy):
    with pytest.raises(ValueError):
        prepare_connectome(
            tuple(inputs.values()), (task(inputs, **policy),), tmp_path / "run", tool_directory=BIN
        )


@requires
def test_missing_fa_alignment_space_and_storage(tmp_path, inputs, monkeypatch):
    without = {k: a for k, a in inputs.items() if k != "fa"}
    with pytest.raises(ValueError, match="FA"):
        prepare_connectome(
            tuple(without.values()), (task(without),), tmp_path / "no-fa", tool_directory=BIN
        )
    d = inputs["nodes"].to_dict()
    d["request"]["declarations"]["registration_reviewed"] = False
    d["request"]["declarations"].pop("registration_diagnostic", None)
    bad = import_artifact(ImportRequest(**d["request"]))
    items = {**inputs, "nodes": bad}
    with pytest.raises(ValueError, match="alignment unresolved"):
        prepare_connectome(
            tuple(items.values()), (task(items),), tmp_path / "unreviewed", tool_directory=BIN
        )
    monkeypatch.setattr(
        "dmri_studio.core.scientific.connectome.shutil.disk_usage",
        lambda _: type("D", (), {"free": 1})(),
    )
    with pytest.raises(ValueError, match="storage"):
        prepare_connectome(
            tuple(inputs.values()), (task(inputs),), tmp_path / "storage", tool_directory=BIN
        )


@requires
def test_source_mutation_failure_and_cancel(tmp_path, inputs):
    p = prepare_connectome(
        tuple(inputs.values()), (task(inputs),), tmp_path / "run", tool_directory=BIN
    )
    with pytest.raises(ValueError, match="Approve"):
        execute_plan(None, p, approval="wrong")
    stop = Event()

    def progress(text):
        if text.startswith("Running"):
            stop.set()

    r = execute_plan(None, p, approval=p.digest, cancelled=stop.is_set, progress=progress)
    assert r["status"] == "cancelled" and not r.get("outputs")
    Path(inputs["tracts"].payload["path"]).write_bytes(b"corrupted")
    with pytest.raises((ValueError, OSError)):
        prepare_connectome(
            tuple(inputs.values()), (task(inputs),), tmp_path / "changed", tool_directory=BIN
        )


@requires
def test_integer_mif_and_nonidentity_affine(tmp_path, inputs):
    source = Path(inputs["nodes"].payload["path"])
    target = source.with_suffix(".mif")
    subprocess.run(
        [str(BIN / "mrconvert"), str(source), str(target), "-datatype", "int32"],
        capture_output=True,
        check=True,
    )
    a = import_artifact(
        ImportRequest(
            "atlas_labels", target, declarations=inputs["nodes"].to_dict()["declarations"]
        )
    )
    assert node_table(a)[1]["voxel_count"] == 0
    converted = {**inputs, "nodes": a}
    renew_diagnostic(converted)
    _, _, b = run(converted, tmp_path / "run")
    assert b["qc"]["assigned_streamlines"] == 3


@pytest.mark.parametrize("data", [np.ones((2, 3)), np.array([[np.nan]]), np.array([[-1]])])
def test_invalid_matrix_csv(tmp_path, data):
    np.savetxt(tmp_path / "bad.csv", data, delimiter=",")
    with pytest.raises(ValueError):
        read_matrix(tmp_path / "bad.csv")


@requires
@pytest.mark.parametrize("value", [1.2, -0.1])
def test_nonphysical_fa_preserved_and_flagged(tmp_path, inputs, value):
    p = tmp_path / "inputs" / "nonphysical-fa.nii"
    save(p, np.full((12, 5, 5), value))
    fa = import_artifact(
        ImportRequest("anatomy", p, declarations=inputs["fa"].to_dict()["declarations"])
    )
    _, _, b = run({**inputs, "fa": fa}, tmp_path / "run")
    matrix = read_matrix(b["matrices"]["mean_fa"]["path"], allow_negative=True)
    assert matrix[0, 2] == pytest.approx(value, abs=1e-6)
    assert b["qc"]["fa_sampling"]["nonphysical_vertex_samples"] > 0
    assert len(b["qc"]["fa_sampling"]["affected_streamline_indices"]) == 4


@requires
@pytest.mark.parametrize("kind", ["flipped", "permuted", "oblique"])
def test_known_physical_coordinate_transforms(tmp_path, inputs, kind):
    angle = 0.4
    affine = np.array(
        [
            [np.cos(angle), -np.sin(angle), 0, 20],
            [np.sin(angle), np.cos(angle), 0, -8],
            [0, 0, 1.5, 3],
            [0, 0, 0, 1.0],
        ]
    )
    if kind == "flipped":
        affine = np.array([[-2, 0, 0, 24], [0, 1, 0, 7], [0, 0, 3, -12], [0, 0, 0, 1.0]])
    if kind == "permuted":
        affine = np.array([[0, 0, 2, 2], [1, 0, 0, 5], [0, 3, 0, -10], [0, 0, 0, 1.0]])
    d = tmp_path / "transformed"
    d.mkdir()
    decl = inputs["nodes"].to_dict()["declarations"]
    new = {}
    for key in ["nodes", "reference", "fa"]:
        image = nib.load(inputs[key].payload["path"])
        p = save(d / (key + ".nii"), np.asanyarray(image.dataobj), affine)
        new[key] = import_artifact(
            ImportRequest("atlas_labels" if key == "nodes" else "anatomy", p, declarations=decl)
        )
    old = nib.streamlines.load(inputs["tracts"].payload["path"])
    lines = [nib.affines.apply_affine(affine, x).astype("f4") for x in old.streamlines]
    nib.streamlines.save(
        nib.streamlines.Tractogram(lines, affine_to_rasmm=np.eye(4)), d / "tracts.tck"
    )
    new["tracts"] = import_artifact(
        ImportRequest("tractogram", d / "tracts.tck", declarations=decl)
    )
    renew_diagnostic(new)
    _, _, b = run(new, tmp_path / "run")
    assert b["qc"]["assigned_streamlines"] == 3
    assert read_matrix(b["matrices"]["count"]["path"])[0, 2] == 2
    assert read_matrix(b["matrices"]["mean_length"]["path"])[0, 2] == pytest.approx(
        6 * np.linalg.norm(affine[:3, 0]), abs=1e-4
    )


@requires
def test_coded_mni_conflict_is_not_bypassed(tmp_path, inputs):
    path = tmp_path / "inputs" / "coded-mni.nii"
    a = np.zeros((12, 5, 5))
    a[1:3] = 1
    a[8:10] = 3
    image = nib.Nifti1Image(a.astype("i4"), np.eye(4))
    image.header.set_xyzt_units("mm")
    image.set_qform(image.affine, 4)
    image.set_sform(image.affine, 4)
    nib.save(image, path)
    atlas = import_artifact(
        ImportRequest("atlas_labels", path, declarations=inputs["nodes"].to_dict()["declarations"])
    )
    d = {**inputs, "nodes": atlas}
    renew_diagnostic(d)
    with pytest.raises(ValueError, match="coded standard space"):
        prepare_connectome(tuple(d.values()), (task(d),), tmp_path / "blocked", tool_directory=BIN)


@requires
def test_registration_evidence_binds_atlas_and_exact_target(tmp_path, inputs):
    """An atlas review cannot be transplanted to another atlas or reference file."""
    import json

    evidence = tmp_path / "inputs/registration.log"
    evidence.write_text("Analytic identity registration; source and target explicitly known")
    review_path = tmp_path / "inputs/review.json"
    record = dict(
        schema="tractosphere.registered-atlas-review/1",
        atlas_sha256=hash_file(Path(inputs["nodes"].payload["path"])),
        reference_sha256=hash_file(Path(inputs["reference"].payload["path"])),
        spatial_review="reviewed",
        evidence_hashes={str(evidence): hash_file(evidence)},
    )
    review_path.write_text(json.dumps(record))
    req = dict(inputs["nodes"].payload["request"])
    req["declarations"] = {**req["declarations"], "registration_record": str(review_path)}
    reviewed = import_artifact(ImportRequest(**req))
    from dmri_studio.core.registration_review import diagnostic_override

    req["declarations"]["registration_diagnostic"] = diagnostic_override(
        reviewed,
        inputs["reference"],
        reviewer="controlled fixture",
        reason="Exact legacy reference binding under test, no scientific approval",
    )
    reviewed = import_artifact(ImportRequest(**req))
    bound = {**inputs, "nodes": reviewed}
    prepare_connectome(
        tuple(bound.values()), (task(bound),), tmp_path / "valid", tool_directory=BIN
    )
    # Same geometry, different source bytes is not the reviewed target.
    altered_ref = save(tmp_path / "inputs/different_reference.nii", np.full((12, 5, 5), 2.0))
    ref = import_artifact(
        ImportRequest(
            "anatomy", altered_ref, declarations=inputs["reference"].payload["declarations"]
        )
    )
    different = {**bound, "reference": ref}
    with pytest.raises(ValueError, match="exact selected reference"):
        prepare_connectome(
            tuple(different.values()), (task(different),), tmp_path / "wrong", tool_directory=BIN
        )
    evidence.write_text("Changed registration evidence")
    with pytest.raises(ValueError, match="Registration evidence changed"):
        import_artifact(ImportRequest(**req))
    del record["reference_sha256"]
    review_path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="exact target reference SHA-256"):
        import_artifact(ImportRequest(**req))


@requires
def test_native_fa_mean_respects_uneven_vertex_spacing(tmp_path, inputs):
    """Real tcksample mean downweights endpoints and accounts for segment length."""
    points = np.array([[1, 2, 2], [2, 2, 2], [8, 2, 2]], dtype="f4")
    path = tmp_path / "inputs/uneven.tck"
    nib.streamlines.save(nib.streamlines.Tractogram([points], affine_to_rasmm=np.eye(4)), path)
    decl = inputs["fa"].payload["declarations"]
    fa_path = save(
        tmp_path / "inputs/linear-fa.nii",
        np.broadcast_to(np.arange(12)[:, None, None] / 11, (12, 5, 5)),
    )
    a = {
        **inputs,
        "tracts": import_artifact(ImportRequest("tractogram", path, declarations=decl)),
        "fa": import_artifact(ImportRequest("anatomy", fa_path, declarations=decl)),
    }
    _, _, b = run(a, tmp_path / "run")
    mean = read_matrix(b["matrices"]["mean_fa"]["path"])[0, 2]
    assert mean == pytest.approx(4.5 / 11, abs=2e-6)
    assert abs(mean - (1 + 2 + 8) / 33) > 0.05
    assert b["qc"]["fa_sampling"]["independent_sample_mean_max_abs_difference"] < 2e-6
