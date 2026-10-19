"""Stage A: native QC bundles, authoritative review gates and resource reservations.

All images are controlled synthetic data; no participant files are modified.
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import nibabel as nib
import numpy as np
import pytest
from test_registration import fixture as fixture
from test_registration import image, run
from test_registration import test_gradient_selected_reference_not_volume_zero as make_reference

from dmri_studio.core.execution import hash_file
from dmri_studio.core.registration_qc import record_decision, review_status, verify_bundle
from dmri_studio.core.registration_resources import (
    registration_resources,
    require_registration_resources,
)
from dmri_studio.core.registration_review import (
    diagnostic_override,
    registration_eligibility,
    require_registration,
)
from dmri_studio.core.scientific import ImportRequest, Task, import_artifact


@pytest.fixture
def reviewed(fixture):
    f = fixture
    make_reference(f)
    ref = f["derived_reference"]
    labels = np.zeros((20, 20, 20), dtype="i2")
    labels[4:8, 4:9, 4:9] = 7
    labels[12:15, 8:12, 10:15] = 41
    source = image(f["src"] / "source-atlas.nii.gz", labels, nib.load(f["s"].image).affine)
    space_review = f["src"] / "source-space.json"
    space_review.write_text(
        json.dumps(
            dict(
                schema="tractosphere.space-association/1",
                image_sha256=hash_file(source),
                reference_sha256=f["s"].sha256,
                status="reviewed",
                review_notes="Known synthetic atlas source grid",
            )
        )
    )
    moving = import_artifact(
        ImportRequest(
            "atlas_labels",
            source,
            declarations={**f["a"].to_dict()["declarations"], "space_review": str(space_review)},
        )
    )
    registered, _ = run(
        f, "apply.labels", dict(moving=moving, reference=f["b"], transform=f["transform"])
    )
    output = registered / "registered.nii.gz"
    association = f["src"] / "association.json"
    evidence = [source, f["src"] / "transform.json"]
    association.write_text(
        json.dumps(
            dict(
                schema="tractosphere.registered-atlas-qc-association/1",
                sha256=hash_file(output),
                reference_sha256=hash_file(Path(ref.payload["path"])),
                registration_reference=f["t"].image,
                registration_reference_sha256=f["t"].sha256,
                status="reviewed",
                review_notes="Controlled known FSL translation; diagnostic fixture, not human anatomy",
                evidence_hashes={str(p): hash_file(p) for p in evidence},
            )
        )
    )
    declarations = ref.to_dict()["declarations"]
    declarations.pop("reference_record")
    declarations["qc_association"] = str(association)
    atlas = import_artifact(ImportRequest("atlas_labels", output, declarations=declarations))
    directory, _ = run(f, "qc", dict(atlas=atlas, reference=ref))
    qc = directory / "atlas-qc.json"
    declarations["registration_qc"] = str(qc)
    atlas = import_artifact(ImportRequest("atlas_labels", output, declarations=declarations))
    return f, atlas, ref, qc, evidence


def decide(qc, decision):
    return record_decision(
        qc, decision, "synthetic reviewer", "Controlled contract test; no real-subject acceptance"
    )


@pytest.mark.parametrize(
    "decision,eligible", [("APPROVED", True), ("REJECTED", False), ("NEEDS_REVIEW", False)]
)
def test_native_review_import_reopen_and_missing_pointer(reviewed, decision, eligible):
    _, atlas, ref, qc, _ = reviewed
    assert not registration_eligibility(atlas, ref)["eligible"]
    record = decide(qc, decision)
    assert json.loads(record.read_text())["schema"] == "tractosphere.registration-review/2"
    assert registration_eligibility(atlas, ref)["eligible"] is eligible
    assert review_status(qc) == decision
    req = atlas.to_dict()["request"]
    req["declarations"].pop("registration_qc")
    reopened = import_artifact(ImportRequest(**req))
    assert registration_eligibility(reopened, ref)["eligible"] is eligible
    assert record.is_file()  # Reopening never erases decisions or reruns registration.


@pytest.mark.parametrize(
    "changed",
    [
        "atlas",
        "reference",
        "moving",
        "transform",
        "source_atlas",
        "qc_image",
        "qc_report",
        "qc_bundle",
    ],
)
def test_changed_evidence_cannot_inherit_approval(reviewed, changed):
    f, atlas, ref, qc, evidence = reviewed
    history = decide(qc, "APPROVED")
    record, _ = verify_bundle(qc)
    target = dict(
        atlas=Path(atlas.payload["path"]),
        reference=Path(ref.payload["path"]),
        moving=Path(f["s"].image),
        transform=f["src"] / "a-b.mat",
        source_atlas=evidence[0],
        qc_image=qc.parent / record["images"][0]["path"],
        qc_report=qc.parent / "qc-report.html",
        qc_bundle=qc,
    )[changed]
    target.write_bytes(target.read_bytes() + b"\ncontrolled changed evidence")
    state = registration_eligibility(atlas, ref)
    assert not state["eligible"] and state["review_applicability"] == "STALE_EVIDENCE"
    assert history.exists()
    with pytest.raises(ValueError, match="cannot be overridden"):
        diagnostic_override(atlas, ref, reviewer="test", reason="Attempt invalid bypass")


@pytest.mark.parametrize("key", ["subject_id", "session_id", "acquisition_id", "space_id"])
def test_identity_changes_invalidate_review(reviewed, key):
    _, atlas, ref, qc, _ = reviewed
    decide(qc, "APPROVED")
    changed = atlas.to_dict()
    changed["declarations"][key] = "different"
    assert registration_eligibility(changed, ref)["review_applicability"] == "IDENTITY_CONFLICT"
    with pytest.raises(ValueError):
        require_registration(changed, ref)


def test_diagnostic_is_explicit_not_acceptance_and_rejection_wins(reviewed):
    _, atlas, ref, qc, _ = reviewed
    a = atlas.to_dict()
    before = copy.deepcopy(a)
    override = diagnostic_override(
        a, ref, reviewer="analyst", reason="Inspect known synthetic aggregation before review"
    )
    assert a == before
    a["declarations"]["registration_diagnostic"] = override
    state = require_registration(a, ref)
    assert state["usage"] == "DIAGNOSTIC" and state["human_review_decision"] == "NEEDS_REVIEW"
    assert override["scientific_acceptance"] == "UNASSIGNED"
    decide(qc, "REJECTED")
    a["declarations"]["registration_reviewed"] = True
    assert registration_eligibility(a, ref)["review_applicability"] == "CONFLICT"
    with pytest.raises(ValueError):
        require_registration(a, ref)


def test_legacy_positive_never_promoted_and_history_preserved(reviewed):
    _, atlas, ref, qc, _ = reviewed
    path = decide(qc, "APPROVED")
    old = json.loads(path.read_text())
    old["schema"] = "tractosphere.registration-review/1"
    path.write_text(json.dumps(old))
    assert not registration_eligibility(atlas, ref)["eligible"]
    assert review_status(qc) == "NEEDS_REVIEW"
    second = decide(qc, "REJECTED")
    assert path.exists() and second != path
    assert review_status(qc) == "REJECTED"


def test_rejected_gates_python_cli_batch_and_roi_without_processing(reviewed, monkeypatch, capsys):
    f, atlas, ref, qc, _ = reviewed
    from dmri_studio.cli.main import main
    from dmri_studio.core.batch.planning import compiler
    from dmri_studio.core.roi_filtering import prepare_roi_filter
    from dmri_studio.core.scientific.connectome import prepare_connectome
    from dmri_studio.core.scientific.records import save_selection

    path = f["src"] / "tracks.tck"
    nib.streamlines.save(
        nib.streamlines.Tractogram(
            [np.array([[20, 0, 0], [0, 0, 0]], "f4")], affine_to_rasmm=np.eye(4)
        ),
        path,
    )
    tracts = import_artifact(
        ImportRequest(
            "tractogram",
            path,
            declarations={
                k: v for k, v in ref.to_dict()["declarations"].items() if k != "reference_record"
            },
        )
    )
    task = Task(
        "matrix",
        "tck2connectome",
        dict(tracts=tracts.id, nodes=atlas.id, reference=ref.id),
        dict(metrics=["count"], assignment="end_voxels", symmetric="yes", zero_diagonal="yes"),
    )
    selection = f["src"] / "selection.json"
    save_selection(selection, (tracts, atlas, ref), (task,))
    decide(qc, "REJECTED")

    # Actual scientific execution is forbidden; version probes in preparation are separate.
    def forbidden(*args, **kwargs):
        pytest.fail("A rejected registration launched scientific processing")

    monkeypatch.setattr("dmri_studio.core.execution.execute_plan", forbidden)
    for prepare in [prepare_connectome, compiler("connectome")]:
        with pytest.raises(ValueError, match="REJECTED"):
            prepare(
                (tracts, atlas, ref),
                (task,),
                f["out"] / "blocked",
                tool_directory=Path("/usr/local/mrtrix3/bin"),
            )
    assert (
        main(
            [
                "scientific",
                "connectome",
                "--selection",
                str(selection),
                "--output",
                str(f["out"] / "cli"),
                "--mrtrix-directory",
                "/usr/local/mrtrix3/bin",
            ]
        )
        != 0
    )
    with pytest.raises(ValueError, match="REJECTED"):
        prepare_roi_filter(
            path,
            Path(atlas.payload["path"]),
            Path(ref.payload["path"]),
            f["out"] / "roi",
            include=[7],
            declarations=atlas.to_dict()["declarations"],
        )
    assert not (f["out"] / "roi").exists() and not (f["out"] / "blocked").exists()


@pytest.mark.parametrize("source,target", [(20, 20), (160, 160), (200, 100), (80, 192)])
def test_geometry_resource_contract_matches_batch(source, target):
    from dmri_studio.core.batch.planning import resources

    tasks = [dict(algorithm="registration.fnirt")]
    artifacts = [
        SimpleNamespace(
            to_dict=lambda n=n: dict(
                kind="anatomy", geometry=dict(shape=[n] * 3), path=str(n), sources=[]
            )
        )
        for n in [source, target]
    ]
    contract = registration_resources(tasks, artifacts)
    budget = resources(
        dict(adapter="registration", tasks=tasks, resources=dict(memory_bytes=1)), artifacts
    )
    assert budget["memory_bytes"] == contract["memory_bytes"] >= max(source, target) ** 3 * 1500
    assert budget["disk_bytes"] == contract["disk_bytes"]
    assert contract["estimate_not_measurement"]


def test_intermediate_geometry_and_16gb_admission(tmp_path, monkeypatch):
    intermediate = image(tmp_path / "intermediate.nii", np.zeros((200, 4, 4), "f4"), np.eye(4))
    transform = dict(
        kind="spatial_transform",
        metadata=dict(source=dict(image=str(intermediate), sha256=hash_file(intermediate))),
    )
    model = registration_resources([dict(algorithm="registration.fnirt")], [transform])
    assert model["largest_grid_voxels"] == 3200
    large = registration_resources(
        [dict(algorithm="registration.fnirt")],
        [dict(kind="anatomy", path="grid", geometry=dict(shape=[160] * 3))],
    )
    assert large["memory_bytes"] >= 6_144_000_000
    monkeypatch.setattr(
        "dmri_studio.core.registration_resources.psutil.virtual_memory",
        lambda: SimpleNamespace(total=16 * 1024**3, available=3 * 1024**3),
    )
    with pytest.raises(ValueError, match="RAM"):
        require_registration_resources(large, tmp_path / "out")
    monkeypatch.setattr(
        "dmri_studio.core.registration_resources.psutil.virtual_memory",
        lambda: SimpleNamespace(total=16 * 1024**3, available=12 * 1024**3),
    )
    monkeypatch.setattr(
        "dmri_studio.core.registration_resources.shutil.disk_usage",
        lambda _: SimpleNamespace(free=20 * 1024**3),
    )
    require_registration_resources(large, tmp_path / "out")
    monkeypatch.setattr(
        "dmri_studio.core.registration_resources.shutil.disk_usage",
        lambda _: SimpleNamespace(free=5 * 1024**3),
    )
    with pytest.raises(ValueError, match="scratch"):
        require_registration_resources(large, tmp_path / "out")


def test_historical_matrix_explicit_rebinding_and_rejection(reviewed):
    from dmri_studio.core.scientific.connectome import require_bundle_registration

    _, atlas, _, qc, _ = reviewed
    historical = dict(atlas=atlas.payload["path"])
    with pytest.raises(ValueError, match="Historical connectome"):
        require_bundle_registration(historical)
    decide(qc, "APPROVED")
    assert (
        require_bundle_registration(historical, dict(registration_qc=str(qc)))["usage"]
        == "REVIEWED"
    )
    decide(qc, "REJECTED")
    with pytest.raises(ValueError, match="REJECTED"):
        require_bundle_registration(historical, dict(registration_qc=str(qc)))


def test_unknown_geometry_is_conservative_not_zero():
    reserve = registration_resources([dict(algorithm="registration.fnirt")])
    assert reserve["unresolved_geometry"] and reserve["memory_bytes"] == 8 * 1024**3
    with pytest.raises(ValueError, match="positive image dimensions"):
        registration_resources(
            [dict(algorithm="registration.fnirt")], [dict(geometry=dict(shape=[160, 0, 160]))]
        )


def test_native_review_rechecked_at_execution_and_completed_reuse(reviewed, tmp_path):
    from dmri_studio.core.batch.discovery import Acquisition, Study
    from dmri_studio.core.batch.planning import build_batch_plan, verify_reuse
    from dmri_studio.core.execution import execute_plan
    from dmri_studio.core.scientific.connectome import prepare_connectome
    from dmri_studio.core.scientific.records import save_selection

    f, atlas, ref, qc, _ = reviewed
    path = f["src"] / "verified-tracks.tck"
    nib.streamlines.save(
        nib.streamlines.Tractogram(
            [np.array([[20, -8, -8], [2, 0, 0]], "f4")], affine_to_rasmm=np.eye(4)
        ),
        path,
    )
    dec = {k: v for k, v in ref.to_dict()["declarations"].items() if k != "reference_record"}
    tracts = import_artifact(ImportRequest("tractogram", path, declarations=dec))
    task = Task(
        "matrix",
        "tck2connectome",
        dict(tracts=tracts.id, nodes=atlas.id, reference=ref.id),
        dict(metrics=["count"], assignment="end_voxels", symmetric="yes", zero_diagonal="yes"),
    )
    artifacts = (tracts, atlas, ref)
    decide(qc, "APPROVED")
    selection = f["src"] / "selection.json"
    save_selection(selection, artifacts, (task,))
    study = Study(
        "synthetic-reviewed",
        str(tmp_path),
        (Acquisition("synthetic", "01", "acq1", {"selection": str(selection)}, {}, {}),),
    )
    workflow = {"nodes": [dict(id="matrix", adapter="connectome", selection_role="selection")]}
    plan = prepare_connectome(
        artifacts, (task,), f["out"] / "matrix", tool_directory=Path("/usr/local/mrtrix3/bin")
    )
    result = execute_plan(None, plan, approval=plan.digest)
    assert result["status"] == "validated_pending_qc", result.get("error")
    manifest = f["out"] / "matrix/manifest.json"
    verify_reuse(manifest, artifacts, (task,))
    pending = prepare_connectome(
        artifacts, (task,), f["out"] / "pending", tool_directory=Path("/usr/local/mrtrix3/bin")
    )
    decide(qc, "REJECTED")
    with pytest.raises(ValueError, match="REJECTED"):
        execute_plan(None, pending, approval=pending.digest)
    assert not (f["out"] / "pending").exists()
    with pytest.raises(ValueError, match="REJECTED"):
        verify_reuse(manifest, artifacts, (task,))
    batch = build_batch_plan(
        study, workflow, f["out"] / "batch", tool_directory=Path("/usr/local/mrtrix3/bin")
    ).to_dict()
    assert all(r["status"] == "BLOCKED" and "REJECTED" in str(r["issues"]) for r in batch["rows"])


@pytest.mark.parametrize("legacy", [True, "true", "reviewed", "APPROVED"])
def test_positive_legacy_text_conflicts_with_native_rejection(reviewed, legacy):
    _, atlas, ref, qc, _ = reviewed
    a = atlas.to_dict()
    a["declarations"]["registration_reviewed"] = legacy
    assert not registration_eligibility(a, ref)["eligible"]
    decide(qc, "REJECTED")
    assert registration_eligibility(a, ref)["review_applicability"] == "CONFLICT"


def test_legacy_rejected_text_blocks_diagnostic(reviewed):
    _, atlas, ref, _, _ = reviewed
    a = atlas.to_dict()
    a["declarations"]["registration_reviewed"] = "rejected"
    assert registration_eligibility(a, ref)["human_review_decision"] == "REJECTED"
    with pytest.raises(ValueError, match="cannot be overridden"):
        diagnostic_override(a, ref, reviewer="test", reason="Invalid negative legacy bypass")


def test_saved_selection_fnirt_budget_uses_resolved_tasks():
    from dmri_studio.core.batch.planning import resources

    image = SimpleNamespace(
        to_dict=lambda: dict(
            kind="anatomy", path="source", geometry=dict(shape=[160] * 3), sources=[]
        )
    )
    tasks = [dict(algorithm="registration.fnirt")]
    explicit = resources(dict(adapter="registration", tasks=tasks), [image])
    selected = resources(dict(adapter="registration", selection_role="selection"), [image], tasks)
    assert selected == explicit
    assert selected["memory_bytes"] >= 6_144_000_000
    assert (
        resources(dict(adapter="registration", selection_role="selection"))["memory_bytes"]
        == 8 * 1024**3
    )
