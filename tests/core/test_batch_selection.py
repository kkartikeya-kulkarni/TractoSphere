"""Selected acquisitions are an explicit part of new approved batch plans."""

import json

import pytest

from dmri_studio.core.batch.discovery import Acquisition, Study
from dmri_studio.core.batch.planning import build_batch_plan


def test_subset_is_bound_to_plan_digest_without_changing_study(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    study = Study(
        "study",
        str(root),
        (Acquisition("sub-01"), Acquisition("sub-02", "ses-1")),
    )
    workflow = {
        "nodes": [
            {
                "id": "metrics",
                "adapter": "dti",
                "imports": {"t": {"role": "tensor", "kind": "tensor"}},
            }
        ]
    }
    first, second = (a.key for a in study.acquisitions)
    one = build_batch_plan(study, workflow, tmp_path / "out", selected_keys=[second])
    all_plan = build_batch_plan(study, workflow, tmp_path / "out")
    payload = one.to_dict()
    assert payload["selected_acquisition_keys"] == [second]
    assert len(payload["study"]["acquisitions"]) == 2
    assert [row["acquisition"]["subject_id"] for row in payload["rows"]] == ["sub-02"]
    assert one.digest != all_plan.digest
    assert "selected_acquisition_keys" not in all_plan.to_dict()
    assert json.dumps(payload)  # Detached, serializable approval evidence.
    for invalid in ([], [first, first], ["unknown/no-session/default"]):
        with pytest.raises(ValueError, match="Select one or more unique"):
            build_batch_plan(study, workflow, tmp_path / "out", selected_keys=invalid)


def test_cli_plans_exact_selected_subset(tmp_path):
    from dmri_studio.cli.main import main

    root = tmp_path / "source"
    root.mkdir()
    study = Study("cli", str(root), (Acquisition("a"), Acquisition("b")))
    workflow = {
        "nodes": [
            {
                "id": "metrics",
                "adapter": "dti",
                "imports": {"t": {"role": "tensor", "kind": "tensor"}},
            }
        ]
    }
    study_path = tmp_path / "study.json"
    workflow_path = tmp_path / "workflow.json"
    plan_path = tmp_path / "plan.json"
    study_path.write_text(json.dumps(study.to_dict()))
    workflow_path.write_text(json.dumps(workflow))
    # Missing tensor inputs are honestly blocked, but the requested subset is
    # still bound to the exported dry-run plan for GUI/CLI parity.
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
                "--acquisition-key",
                study.acquisitions[1].key,
            ]
        )
        == 1
    )
    plan = json.loads(plan_path.read_text())
    assert plan["selected_acquisition_keys"] == [study.acquisitions[1].key]
    assert len(plan["rows"]) == 1
