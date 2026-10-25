"""Opt-in native Cocoa walkthrough on an independently downloaded public subject.

The app is driven through its own controls. Public source files stay outside the
repository, and this test records technical outcomes without scientific approval.
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from dmri_studio.core.execution import hash_file


@pytest.mark.skipif(
    not os.environ.get("TRACTOSPHERE_DS000221_ROOT")
    or not os.environ.get("TRACTOSPHERE_DS000221_OUTPUT")
    or os.environ.get("QT_QPA_PLATFORM") != "cocoa",
    reason="Requires selected public data, an isolated output folder and native Cocoa GUI",
)
def test_ds000221_raw_denoise_gibbs_native_gui(window, qtbot):
    root = Path(os.environ["TRACTOSPHERE_DS000221_ROOT"])
    output_root = Path(os.environ["TRACTOSPHERE_DS000221_OUTPUT"])
    output_root.mkdir(parents=True, exist_ok=True)
    stem = root / "sub-010002/ses-01/dwi/sub-010002_ses-01_dwi"
    source = Path(str(stem) + ".nii.gz")
    source_hash = hash_file(source)
    evidence = {"dataset": "OpenNeuro ds000221 v1.0.0 sub-010002 ses-01"}

    page = window.page
    page.show_form()
    for role, suffix in (
        ("dwi", ".nii.gz"),
        ("bval", ".bval"),
        ("bvec", ".bvec"),
        ("json", ".json"),
    ):
        page.fields[role].setText(str(stem) + suffix)
    window.start_load()
    qtbot.waitUntil(lambda: window.load_thread is None, timeout=60000)
    evidence["import"] = {
        "loaded": window.dataset is not None,
        "status": page.state,
    }
    assert window.dataset is not None, evidence["import"]
    window.grab().save(str(output_root / "01-import.png"))

    window.focus_workflow()
    workflow = window.workflow
    workflow.template.setCurrentIndex(2)
    workflow.source.setText("OpenNeuro ds000221 v1.0.0 raw BIDS acquisition, sub-010002 ses-01")
    workflow.confirm.click()
    workflow.selections["denoise"].setChecked(True)
    workflow.selections["gibbs"].setChecked(True)
    evidence["eligibility"] = workflow.plan.to_dict()
    assert evidence["eligibility"]["status"] == "eligible", evidence["eligibility"]
    window.grab().save(str(output_root / "02-eligibility.png"))

    execution = workflow.execution
    execution.tool_directory.setText("/usr/local/mrtrix3/bin")
    execution.output.setText(str(output_root / "denoise-gibbs"))
    execution.prepare.click()
    qtbot.waitUntil(lambda: execution.thread is None, timeout=180000)
    evidence["preview"] = {
        "status": execution.status.text(),
        "commands": execution.commands.toPlainText(),
        "plan": execution.plan.to_dict() if execution.plan else None,
    }
    assert execution.plan is not None, evidence["preview"]
    assert "dwidenoise" in evidence["preview"]["commands"]
    assert "mrdegibbs" in evidence["preview"]["commands"]
    window.grab().save(str(output_root / "03-preview.png"))

    execution.run.click()
    qtbot.waitUntil(lambda: execution.thread is None, timeout=900000)
    evidence["result"] = execution.manifest
    evidence["source_unchanged"] = hash_file(source) == source_hash
    (output_root / "native-gui-evidence.json").write_text(
        json.dumps(evidence, indent=2, default=str) + "\n"
    )
    window.grab().save(str(output_root / "04-result.png"))
    assert evidence["source_unchanged"]
    assert execution.manifest["status"] == "validated_pending_qc", execution.status.text()


@pytest.mark.skipif(
    not os.environ.get("TRACTOSPHERE_DS000221_ROOT")
    or not os.environ.get("TRACTOSPHERE_DS000221_OUTPUT")
    or not os.environ.get("TRACTOSPHERE_FSL_BIN")
    or os.environ.get("QT_QPA_PLATFORM") != "cocoa",
    reason="Requires selected public data, GUI native output and native Cocoa GUI",
)
def test_ds000221_gibbs_to_topup_eddy_native_gui(window, qtbot):
    root = Path(os.environ["TRACTOSPHERE_DS000221_ROOT"])
    output_root = Path(os.environ["TRACTOSPHERE_DS000221_OUTPUT"])
    parent = output_root / "denoise-gibbs"
    assert (parent / "native-manifest.json").exists(), "Run raw GUI preprocessing first"
    source = parent / "gibbs.nii"
    source_hash = hash_file(source)
    original_json = (
        root / "sub-010002/ses-01/dwi/sub-010002_ses-01_dwi.json"
    )
    reverse = (
        root
        / "sub-010002/ses-01/fmap/sub-010002_ses-01_acq-SEfmapDWI_dir-PA_epi.nii.gz"
    )
    evidence = {"source": str(source), "source_sha256": source_hash}

    page = window.page
    page.show_form()
    for role, path in (
        ("dwi", source),
        ("bval", parent / "inputs.bvals"),
        ("bvec", parent / "inputs.bvecs"),
        ("json", original_json),
    ):
        page.fields[role].setText(str(path))
    window.start_load()
    qtbot.waitUntil(lambda: window.load_thread is None, timeout=60000)
    evidence["import"] = {"loaded": window.dataset is not None, "state": page.state}
    assert window.dataset is not None, evidence["import"]

    window.focus_workflow()
    workflow = window.workflow
    workflow.template.setCurrentIndex(2)
    for stage in ("denoise", "gibbs"):
        workflow.states[stage].setCurrentIndex(2)
    workflow.source.setText(str(parent / "native-manifest.json"))
    workflow.confirm.click()
    workflow.selections["correction"].setChecked(True)
    evidence["eligibility"] = workflow.plan.to_dict()
    assert evidence["eligibility"]["status"] == "eligible", evidence["eligibility"]
    window.grab().save(str(output_root / "05-correction-eligibility.png"))

    execution = workflow.execution
    execution.tool_directory.setText("/usr/local/mrtrix3/bin")
    execution.references.setPlainText(str(reverse))
    execution.correction_inputs["bids_root"].setText(str(root))
    execution.correction_inputs["native_record"].setText(
        str(parent / "native-manifest.json")
    )
    execution.correction_inputs["fsl_directory"].setText(
        os.environ["TRACTOSPHERE_FSL_BIN"]
    )
    execution.output.setText(str(output_root / "topup-eddy"))
    execution.prepare.click()
    qtbot.waitUntil(lambda: execution.thread is None, timeout=180000)
    evidence["preview"] = {
        "status": execution.status.text(),
        "commands": execution.commands.toPlainText(),
        "plan": execution.plan.to_dict() if execution.plan else None,
    }
    (output_root / "correction-gui-evidence.json").write_text(
        json.dumps(evidence, indent=2, default=str) + "\n"
    )
    assert execution.plan is not None, evidence["preview"]
    assert "topup" in evidence["preview"]["commands"]
    assert "eddy_cpu" in evidence["preview"]["commands"]
    estimated = evidence["preview"]["plan"].get("resources", {}).get(
        "estimated_peak_additional_bytes", 0
    )
    free = shutil.disk_usage(output_root).free
    assert free > estimated + 3 * 1024**3, (free, estimated)
    window.grab().save(str(output_root / "06-correction-preview.png"))

    execution.run.click()
    qtbot.waitUntil(lambda: execution.thread is None, timeout=7200000)
    evidence["result"] = execution.manifest
    evidence["status_after_run"] = execution.status.text()
    evidence["source_unchanged"] = hash_file(source) == source_hash
    (output_root / "correction-gui-evidence.json").write_text(
        json.dumps(evidence, indent=2, default=str) + "\n"
    )
    window.grab().save(str(output_root / "07-correction-result.png"))
    assert evidence["source_unchanged"]
    assert execution.manifest is not None, execution.status.text()
    assert execution.manifest["status"] == "validated_pending_qc", execution.status.text()
