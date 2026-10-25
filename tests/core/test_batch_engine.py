"""Actual native tensor metrics through batch scheduling, plus controlled recovery faults."""

import json
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.batch.discovery import Acquisition, Study
from dmri_studio.core.batch.engine import (
    BatchRunner,
    control,
    create_batch,
    export_report,
    read_journal,
)
from dmri_studio.core.batch.planning import GIB, build_batch_plan
from dmri_studio.core.scientific.dti import TENSOR_DECLARATIONS

BIN = Path("/usr/local/mrtrix3/bin")
pytestmark = pytest.mark.skipif(
    not all((BIN / name).is_file() for name in ("mrconvert", "tensor2metric")),
    reason="Native MRtrix3 is required for batch integration tests",
)


def fixture(tmp_path, count=2):
    root = tmp_path / "sources"
    root.mkdir()
    acquisitions = []
    for i in range(count):
        path = root / f"synthetic-{i}.nii"
        data = np.zeros((3, 4, 5, 6), dtype="f4")
        data[..., :3] = [0.0015, 0.0005, 0.0003]
        image = nib.Nifti1Image(data, np.diag([2.0, 2.0, 2.0, 1.0]))
        image.set_qform(image.affine, 1)
        image.set_sform(image.affine, 1)
        image.header.set_xyzt_units("mm")
        nib.save(image, path)
        acquisitions.append(
            Acquisition(
                f"synthetic-{i}",
                "01",
                "tensor",
                {"tensor": str(path)},
                {"synthetic": True},
                dict(TENSOR_DECLARATIONS),
            )
        )
    study = Study("synthetic-study", str(root), tuple(acquisitions))
    workflow = {
        "nodes": [
            dict(
                id="metrics",
                adapter="dti",
                imports={"t": {"role": "tensor", "kind": "tensor"}},
                tasks=[
                    dict(
                        id="metrics",
                        algorithm="tensor2metric",
                        inputs={"tensor": {"artifact_id": "t"}},
                        parameters={"metrics": ["FA", "MD"]},
                    )
                ],
            )
        ]
    }
    return study, workflow


def plan_for(tmp_path, count=2):
    study, workflow = fixture(tmp_path, count)
    plan = build_batch_plan(
        study,
        workflow,
        tmp_path / "batch",
        tool_directory=BIN,
        limits={"disk_headroom_bytes": 1024**2},
    )
    assert all(r["status"] == "ELIGIBLE" for r in plan.to_dict()["rows"]), plan.to_json()
    return plan


def test_actual_execution_and_resume_no_duplicate(tmp_path):
    plan = plan_for(tmp_path)
    folder = create_batch(plan, plan.digest)
    result = BatchRunner(folder).run()
    assert result["state"] == "COMPLETED", result
    assert all(r["status"] == "COMPLETED" for r in result["nodes"].values())
    for row in result["nodes"].values():
        record = json.loads(Path(row["manifest"]).read_text())
        assert record["inputs_unchanged"]
        assert record["validation"]
        assert len(row["attempts"]) == 1
    resumed = BatchRunner(folder).run()
    assert all(
        r["status"] == "SKIPPED_VERIFIED" and len(r["attempts"]) == 1
        for r in resumed["nodes"].values()
    ), resumed
    assert export_report(folder, tmp_path / "report").is_file()


def test_pause_cancel_retry(tmp_path):
    plan = plan_for(tmp_path)
    folder = create_batch(plan, plan.digest)
    control(folder, "pause")
    paused = BatchRunner(folder).run()
    assert paused["state"] == "PAUSED"
    assert not any(r["attempts"] for r in paused["nodes"].values())
    key = next(iter(paused["nodes"]))
    control(folder, "cancel", [key])
    control(folder, "resume")
    result = BatchRunner(folder).run()
    assert result["nodes"][key]["status"] == "CANCELLED"
    other = next(k for k in result["nodes"] if k != key)
    assert result["nodes"][other]["status"] == "COMPLETED"
    control(folder, "retry", [key])
    result = BatchRunner(folder).run()
    assert result["nodes"][key]["status"] == "COMPLETED"
    assert result["nodes"][other]["status"] == "SKIPPED_VERIFIED"
    assert len(result["nodes"][other]["attempts"]) == 1


def test_corrupt_output_not_reused(tmp_path):
    plan = plan_for(tmp_path, 1)
    folder = create_batch(plan, plan.digest)
    result = BatchRunner(folder).run()
    row = next(iter(result["nodes"].values()))
    output = Path(row["manifest"]).parent / "metrics-FA.mif"
    output.write_bytes(b"controlled corruption")
    result = BatchRunner(folder).run()
    assert next(iter(result["nodes"].values()))["status"] == "REQUIRES_REVIEW"
    assert len(next(iter(result["nodes"].values()))["attempts"]) == 1


def test_changed_input_after_approval_fails_without_native_outputs(tmp_path):
    plan = plan_for(tmp_path, 1)
    source = Path(plan.to_dict()["study"]["acquisitions"][0]["paths"]["tensor"])
    image = nib.load(source)
    data = image.get_fdata().astype("f4")
    data[..., 0] *= 2
    nib.save(nib.Nifti1Image(data, image.affine, image.header), source)
    folder = create_batch(plan, plan.digest)
    result = BatchRunner(folder).run()
    row = next(iter(result["nodes"].values()))
    assert row["status"] == "FAILED"
    assert "identity changed" in row["error"]
    assert not Path(row["attempts"][0]["directory"]).exists()


def test_plan_approval_and_resource_gate(tmp_path, monkeypatch):
    plan = plan_for(tmp_path, 1)
    with pytest.raises(ValueError, match="digest"):
        create_batch(plan, "wrong")
    folder = create_batch(plan, plan.digest)
    from collections import namedtuple

    import dmri_studio.core.batch.engine as engine

    monkeypatch.setattr(
        engine.shutil,
        "disk_usage",
        lambda _: namedtuple("Disk", "total used free")(10 * GIB, 10 * GIB, 0),
    )
    result = BatchRunner(folder).run()
    assert next(iter(result["nodes"].values()))["status"] == "RESOURCE_BLOCKED"
    assert not next(iter(result["nodes"].values()))["attempts"]


def test_missing_inputs_isolated_dry_run(tmp_path):
    study, workflow = fixture(tmp_path)
    bad = Acquisition("synthetic-missing", paths={})
    study = Study(study.study_id, study.root, (*study.acquisitions, bad))
    output = tmp_path / "batch"
    plan = build_batch_plan(study, workflow, output, tool_directory=BIN)
    assert not output.exists()
    assert [r["status"] for r in plan.to_dict()["rows"]] == ["ELIGIBLE", "ELIGIBLE", "BLOCKED"]


def test_changed_plan_and_lock(tmp_path):
    from dmri_studio.core.batch.engine import ownership

    plan = plan_for(tmp_path, 1)
    folder = create_batch(plan, plan.digest)
    with ownership(folder), pytest.raises(ValueError, match="owns"):
        BatchRunner(folder).run()
    saved = json.loads((folder / "approved-plan.json").read_text())
    saved["plan"]["limits"]["cpu_threads"] = 64
    (folder / "approved-plan.json").write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="modified"):
        BatchRunner(folder).run()


def test_interrupted_completed_attempt_recovered(tmp_path):
    from dmri_studio.core.batch.engine import atomic_json

    plan = plan_for(tmp_path, 1)
    folder = create_batch(plan, plan.digest)
    result = BatchRunner(folder).run()
    row = next(iter(result["nodes"].values()))
    assert row["status"] == "COMPLETED"
    row["status"] = "RUNNING"
    row.pop("manifest")
    row.pop("manifest_sha256")
    atomic_json(folder / "journal.json", result)
    result = BatchRunner(folder).run()
    assert next(iter(result["nodes"].values()))["status"] == "SKIPPED_VERIFIED"


def test_dependency_cycle_rejected(tmp_path):
    study, workflow = fixture(tmp_path, 1)
    workflow["nodes"][0]["depends_on"] = ["metrics"]
    with pytest.raises(ValueError, match="cycle"):
        build_batch_plan(study, workflow, tmp_path / "batch")


def test_dependency_actual_tensor_fit_then_metrics(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "dti_phantom", Path(__file__).with_name("test_dti.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    a, _ = module.phantom(tmp_path / "source")
    request = a.to_dict()["request"]
    acquisition = Acquisition(
        "synthetic-dwi",
        "",
        "run1",
        {"dwi": request["path"], "bval": request["bval"], "bvec": request["bvec"]},
        {"synthetic": True},
        {"processing_state": "preprocessed"},
    )
    study = Study("dependency", str(tmp_path / "source"), (acquisition,))
    workflow = {
        "nodes": [
            dict(
                id="fit",
                adapter="dti",
                imports={"dwi": dict(role="dwi", kind="dwi", bval_role="bval", bvec_role="bvec")},
                tasks=[
                    dict(
                        id="fit",
                        algorithm="dwi2tensor",
                        inputs={"dwi": {"artifact_id": "dwi"}},
                        parameters={"fit": "OLS", "shells": [0, 1000]},
                    )
                ],
            ),
            dict(
                id="metric",
                adapter="dti",
                depends_on=["fit"],
                imports={
                    "tensor": dict(
                        node="fit",
                        output="fit-tensor.mif",
                        kind="tensor",
                        declarations=dict(TENSOR_DECLARATIONS),
                    )
                },
                tasks=[
                    dict(
                        id="metric",
                        algorithm="tensor2metric",
                        inputs={"tensor": {"artifact_id": "tensor"}},
                        parameters={"metrics": ["FA", "MD"]},
                    )
                ],
            ),
        ]
    }
    plan = build_batch_plan(study, workflow, tmp_path / "batch", tool_directory=BIN)
    assert [r["status"] for r in plan.to_dict()["rows"]] == ["ELIGIBLE", "WAITING_DEPENDENCY"], (
        plan.to_json()
    )
    folder = create_batch(plan, plan.digest)
    result = BatchRunner(folder).run()
    assert result["state"] == "COMPLETED", result
    result = BatchRunner(folder).run()
    assert all(
        r["status"] == "SKIPPED_VERIFIED" and len(r["attempts"]) == 1
        for r in result["nodes"].values()
    ), result


def test_journal_recipe_tamper_rejected(tmp_path):
    from dmri_studio.core.batch.engine import atomic_json

    plan = plan_for(tmp_path, 1)
    folder = create_batch(plan, plan.digest)
    journal = read_journal(folder)
    next(iter(journal["nodes"].values()))["node"]["tasks"][0]["parameters"]["metrics"] = ["AD"]
    atomic_json(folder / "journal.json", journal)
    with pytest.raises(ValueError, match="configuration"):
        BatchRunner(folder).run()


def test_concurrent_subjects_actual_native(tmp_path):
    study, workflow = fixture(tmp_path, 3)
    plan = build_batch_plan(
        study,
        workflow,
        tmp_path / "batch",
        tool_directory=BIN,
        limits={"max_subjects": 2, "max_processes": 2},
    )
    folder = create_batch(plan, plan.digest)
    result = BatchRunner(folder).run()
    assert result["state"] == "COMPLETED", result
    events = [json.loads(s) for s in (folder / "events.jsonl").read_text().splitlines()]
    active, maximum = set(), 0
    for event in events:
        if event["status"] == "RUNNING":
            active.add(event["key"])
        else:
            active.discard(event["key"])
        maximum = max(maximum, len(active))
    assert maximum == 2


def test_failed_subject_isolation_and_selected_retry(tmp_path, monkeypatch):
    import dmri_studio.core.batch.engine as engine

    plan = plan_for(tmp_path)
    folder = create_batch(plan, plan.digest)
    original = engine.execute_plan

    def failure(dataset, native_plan, **kwargs):
        if "synthetic-0" in native_plan.payload["output_directory"]:
            raise RuntimeError("Controlled scheduler fault before native execution")
        return original(dataset, native_plan, **kwargs)

    monkeypatch.setattr(engine, "execute_plan", failure)
    result = BatchRunner(folder).run()
    assert sorted(r["status"] for r in result["nodes"].values()) == ["COMPLETED", "FAILED"]
    key = next(k for k, r in result["nodes"].items() if r["status"] == "FAILED")
    monkeypatch.setattr(engine, "execute_plan", original)
    control(folder, "retry", [key])
    result = BatchRunner(folder).run()
    assert result["state"] == "COMPLETED"
    assert len(result["nodes"][key]["attempts"]) == 2
    assert all(len(r["attempts"]) == 1 for k, r in result["nodes"].items() if k != key)


def test_active_cancellation_and_pause_drains(tmp_path):
    plan = plan_for(tmp_path)
    folder = create_batch(plan, plan.digest)
    sent = []

    def progress(message):
        if not sent and "Verifying imported" in message:
            control(folder, "pause")
            sent.append(True)

    # An explicit pause before scheduling is covered separately; here publish
    # during the running stage, which must complete rather than be cancelled.
    runner = BatchRunner(folder, progress=progress)
    original = runner.execute

    def execute(*args):
        control(folder, "pause")
        return original(*args)

    runner.execute = execute
    result = runner.run()
    assert result["state"] == "PAUSED"
    assert sum(r["status"] == "COMPLETED" for r in result["nodes"].values()) == 1
    assert sum(r["status"] == "PENDING" for r in result["nodes"].values()) == 1


def test_actual_preprocessing_conversion_and_resume(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "native_fixture", Path(__file__).with_name("test_execution.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture_generator = module.phantom.__wrapped__(tmp_path)
    dataset = next(fixture_generator)
    try:
        paths = {k: str(v) for k, v in dataset.paths.supplied().items()}
        a = Acquisition("synthetic-conversion", paths=paths, metadata={"synthetic": True})
        study = Study("preprocess", str(tmp_path / "source"), (a,))
        workflow = {
            "nodes": [
                dict(
                    id="convert",
                    adapter="preprocessing",
                    imports={
                        "dwi": dict(role="dwi", kind="dwi", bval_role="bval", bvec_role="bvec")
                    },
                    preprocessing=dict(
                        operations=["convert"], history=module.history.__wrapped__().to_dict()
                    ),
                )
            ]
        }
        plan = build_batch_plan(study, workflow, tmp_path / "batch", tool_directory=BIN)
        assert plan.to_dict()["rows"][0]["status"] == "ELIGIBLE", plan.to_json()
        folder = create_batch(plan, plan.digest)
        result = BatchRunner(folder).run()
        assert result["state"] == "COMPLETED", result
        result = BatchRunner(folder).run()
        assert next(iter(result["nodes"].values()))["status"] == "SKIPPED_VERIFIED", result
    finally:
        fixture_generator.close()


def test_actual_process_restart_interrupted_attempt_and_retry(tmp_path):
    import os
    import subprocess
    import sys
    import time

    plan = plan_for(tmp_path)
    folder = create_batch(plan, plan.digest)
    script = tmp_path / "interrupt_scheduler.py"
    script.write_text(
        """import sys,time\nfrom pathlib import Path\nfrom dmri_studio.core.batch.engine import BatchRunner\nr=BatchRunner(Path(sys.argv[1]))\ndef interrupted(key,row,attempt,cancel):\n attempt.mkdir()\n (attempt/'partial-diagnostic.txt').write_text('Deliberately interrupted synthetic test; not a scientific output')\n time.sleep(120)\nr.execute=interrupted\nr.run()\n"""
    )
    child = subprocess.Popen(
        [sys.executable, str(script), str(folder)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")},
    )
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline and not list(
            (folder / "subjects").glob("**/partial-diagnostic.txt")
        ):
            time.sleep(0.03)
        assert list((folder / "subjects").glob("**/partial-diagnostic.txt"))
    finally:
        child.kill()
        child.wait(timeout=10)
    recovered = BatchRunner(folder).run()
    interrupted = [k for k, r in recovered["nodes"].items() if r["status"] == "REQUIRES_REVIEW"]
    assert len(interrupted) == 1
    assert any(r["status"] == "COMPLETED" for r in recovered["nodes"].values())
    control(folder, "retry", interrupted)
    result = BatchRunner(folder).run()
    assert result["state"] == "COMPLETED"
    assert list((folder / "subjects").glob("**/partial-diagnostic.txt"))  # diagnostic retained


def test_cli_discovery_plan_execution_and_resume(tmp_path, capsys):
    from dmri_studio.cli.main import main

    study, workflow = fixture(tmp_path, 1)
    study_path, workflow_path, plan_path = (
        tmp_path / n for n in ("study.json", "workflow.json", "plan.json")
    )
    study_path.write_text(json.dumps(study.to_dict()))
    workflow_path.write_text(json.dumps(workflow))
    assert (
        main(
            [
                "batch",
                "plan",
                "--study",
                str(study_path),
                "--workflow",
                str(workflow_path),
                "--output-directory",
                str(tmp_path / "batch"),
                "--plan",
                str(plan_path),
                "--mrtrix-directory",
                str(BIN),
            ]
        )
        == 0
    )
    from dmri_studio.core.execution import ExecutionPlan

    plan = ExecutionPlan(json.loads(plan_path.read_text()))
    assert (
        main(["batch", "execute", "--plan", str(plan_path), "--approve-digest", plan.digest]) == 0
    )
    assert main(["batch", "resume", "--batch", str(tmp_path / "batch")]) == 0
    assert "SKIPPED_VERIFIED" in capsys.readouterr().out


def test_tool_change_blocks_reuse_without_rerun(tmp_path, monkeypatch):
    import dmri_studio.core.batch.planning as planning

    plan = plan_for(tmp_path, 1)
    folder = create_batch(plan, plan.digest)
    assert BatchRunner(folder).run()["state"] == "COMPLETED"
    original = planning.hash_file

    def changed(path, *args):
        return "0" * 64 if Path(path).name == "tensor2metric" else original(path, *args)

    monkeypatch.setattr(planning, "hash_file", changed)
    result = BatchRunner(folder).run()
    row = next(iter(result["nodes"].values()))
    assert row["status"] == "REQUIRES_REVIEW"
    assert "software identity" in row["error"]
    assert len(row["attempts"]) == 1


def test_comparison_configuration_accepts_world_coordinate_tractogram():
    from dmri_studio.core.batch.planning import comparison_configuration
    from dmri_studio.core.scientific.contracts import Artifact

    artifact = Artifact(dict(id="example", kind="tractogram", geometry=None, declarations={}))
    result = comparison_configuration(Acquisition("synthetic"), [artifact], [])
    assert result["inputs"][0]["shape"] is None


def test_explicit_review_does_not_follow_execution_automatically(tmp_path):
    from dmri_studio.core.batch.engine import record_review, study_qc

    plan = plan_for(tmp_path, 1)
    folder = create_batch(plan, plan.digest)
    result = BatchRunner(folder).run()
    assert study_qc(folder)["accepted_subjects"] == 0
    key = next(iter(result["nodes"]))
    record_review(
        folder,
        key,
        "requires_review",
        "Synthetic test reviewer",
        "Software fixture only, no human scientific acceptance",
    )
    assert study_qc(folder)["accepted_subjects"] == 0
    assert study_qc(folder)["explicit_reviews"][key]["decision"] == "requires_review"


def test_blocked_job_cannot_be_promoted_by_cancel_retry(tmp_path):
    study, workflow = fixture(tmp_path, 1)
    missing = Acquisition("synthetic-missing")
    study = Study("study", study.root, (missing,))
    plan = build_batch_plan(study, workflow, tmp_path / "batch", tool_directory=BIN)
    assert plan.to_dict()["rows"][0]["status"] == "BLOCKED"
    folder = create_batch(plan, plan.digest)
    key = plan.to_dict()["rows"][0]["key"]
    control(folder, "cancel", [key])
    control(folder, "retry", [key])
    result = BatchRunner(folder).run()
    assert result["nodes"][key]["status"] == "BLOCKED"
    assert result["nodes"][key]["attempts"] == []


def test_empty_batch_is_not_success(tmp_path):
    with pytest.raises(ValueError, match="empty batch"):
        build_batch_plan(Study("empty", str(tmp_path), ()), {"nodes": []}, tmp_path / "batch")


def test_cancel_active_child_through_existing_process_runner(tmp_path, monkeypatch):
    """Controlled non-MRI child proves signal propagation; no scientific success is simulated."""
    import os
    import sys
    import threading
    import time

    import psutil

    import dmri_studio.core.batch.engine as engine
    from dmri_studio.core.process import run_command

    plan = plan_for(tmp_path, 1)
    folder = create_batch(plan, plan.digest)
    child_pid = tmp_path / "controlled-child.pid"

    def diagnostic_child(dataset, native_plan, *, approval, cancelled, progress):
        attempt = Path(native_plan.payload["output_directory"])
        attempt.mkdir()
        program = (
            "import os,time,pathlib;pathlib.Path("
            + repr(str(child_pid))
            + ").write_text(str(os.getpid()));time.sleep(30)"
        )
        run_command(
            [sys.executable, "-c", program],
            cwd=attempt,
            log=attempt / "cancellation-diagnostic.log",
            environment=os.environ.copy(),
            cancelled=cancelled,
            progress=progress,
        )
        raise AssertionError("The controlled child should have been cancelled")

    monkeypatch.setattr(engine, "execute_plan", diagnostic_child)
    results = []
    runner = threading.Thread(target=lambda: results.append(BatchRunner(folder).run()))
    runner.start()
    deadline = time.monotonic() + 10
    while not child_pid.exists() and time.monotonic() < deadline:
        time.sleep(0.03)
    assert child_pid.exists()
    pid = int(child_pid.read_text())
    control(folder, "cancel", [next(iter(read_journal(folder)["nodes"]))])
    runner.join(15)
    assert not runner.is_alive()
    assert next(iter(results[0]["nodes"].values()))["status"] == "CANCELLED"
    assert not psutil.pid_exists(pid)


def test_registration_sized_reservations_serialize_on_16gb_and_recover(tmp_path, monkeypatch):
    """Use FNIRT budgets with tiny native metrics, never simultaneous FNIRT jobs."""
    from types import SimpleNamespace

    from dmri_studio.core.registration_resources import registration_resources

    reservation = registration_resources(
        [dict(algorithm="registration.fnirt")],
        [dict(kind="anatomy", path="synthetic-grid", geometry=dict(shape=[160] * 3))],
    )
    study, workflow = fixture(tmp_path, 2)
    workflow["nodes"][0]["resources"] = {k: reservation[k] for k in ("memory_bytes", "disk_bytes")}
    monkeypatch.setattr(
        "dmri_studio.core.batch.engine.psutil.virtual_memory",
        lambda: SimpleNamespace(total=16 * GIB, available=12 * GIB),
    )
    plan = build_batch_plan(
        study,
        workflow,
        tmp_path / "reserved-batch",
        tool_directory=BIN,
        limits=dict(max_subjects=2, max_processes=2, memory_bytes=8 * GIB),
    )
    folder = create_batch(plan, plan.digest)
    result = BatchRunner(folder).run()
    assert result["state"] == "COMPLETED"
    active = set()
    for event in [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()]:
        if event["status"] == "RUNNING":
            active.add(event["key"])
        else:
            active.discard(event["key"])
        assert len(active) <= 1
    attempts = {key: len(row["attempts"]) for key, row in read_journal(folder)["nodes"].items()}
    BatchRunner(folder).run()
    assert {
        key: len(row["attempts"]) for key, row in read_journal(folder)["nodes"].items()
    } == attempts
