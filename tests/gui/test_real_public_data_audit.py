"""Opt-in native GUI walkthrough using an independently downloaded public dataset.

Set TRACTOSPHERE_REAL_DATA_ROOT to a BIDS ds007739 checkout containing sub-02.
The test never writes within the dataset directory.
"""

import os
from pathlib import Path

import pytest

from dmri_studio.core.execution import hash_file
from dmri_studio.core.scientific import ImportRequest


@pytest.mark.skipif(
    not os.environ.get("TRACTOSPHERE_REAL_DATA_ROOT")
    or not os.environ.get("TRACTOSPHERE_REAL_MASK")
    or os.environ.get("QT_QPA_PLATFORM") != "cocoa",
    reason="Requires explicitly supplied public dataset and native Cocoa GUI",
)
def test_real_dwi_dti_maps_gui(window, qtbot, tmp_path):
    root = Path(os.environ["TRACTOSPHERE_REAL_DATA_ROOT"])
    stem = root / "sub-02/dwi/sub-02_run-03_dwi"
    source = Path(str(stem) + ".nii.gz")
    mask = Path(os.environ["TRACTOSPHERE_REAL_MASK"])
    before = hash_file(source)

    window.focus_models()
    page = window.scientific
    page.tool_directory = Path("/usr/local/mrtrix3/bin")
    page.import_request(
        ImportRequest(
            "dwi",
            source,
            Path(str(stem) + ".bval"),
            Path(str(stem) + ".bvec"),
            Path(str(stem) + ".json"),
            {
                "processing_state": "raw",
                "subject_id": "sub-02",
                "space_id": "sub-02_dwi_run-03_native",
            },
            "OpenNeuro ds007739 v1.0.0 raw DWI; scientific acceptance unknown.",
        )
    )
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert len(page.artifacts) == 1, page.status.text()
    page.import_request(
        ImportRequest(
            "mask",
            mask,
            declarations={"subject_id": "sub-02", "space_id": "sub-02_dwi_run-03_native"},
            provenance="MRtrix3 dwi2mask derivative from this exact raw DWI; QC pending.",
        )
    )
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert len(page.artifacts) == 2, page.status.text()

    panel = page.dti
    page.tabs.setCurrentWidget(panel)
    panel.source.setCurrentIndex(panel.source.findData(page.artifacts[0].id))
    panel.mask.setCurrentIndex(panel.mask.findData(page.artifacts[1].id))
    panel.shells.setText("0,950")
    panel.fit.setCurrentText("WLS")
    panel.output.setText(str(tmp_path / "real-dti-attempt"))
    panel.guided.click()
    assert len(page.tasks) == 2, page.status.text()
    panel.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=120000)
    assert panel.plan is not None, page.status.text()
    assert not panel.run_button.isEnabled()
    panel.approval.setChecked(True)
    panel.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=180000)
    assert panel.manifest["status"] == "validated_pending_qc", panel.manifest.get("error")
    assert panel.maps.count() >= 5
    panel.open_qc()
    qtbot.waitUntil(lambda: window.qc.thread is None, timeout=20000)
    assert window.qc.catalog is not None
    assert hash_file(source) == before


@pytest.mark.skipif(
    not os.environ.get("TRACTOSPHERE_REAL_DATA_ROOT")
    or os.environ.get("QT_QPA_PLATFORM") != "cocoa",
    reason="Requires explicitly supplied public dataset and native Cocoa GUI",
)
def test_real_raw_dwi_denoise_and_gibbs_review_gui(window, qtbot, tmp_path):
    root = Path(os.environ["TRACTOSPHERE_REAL_DATA_ROOT"])
    stem = root / "sub-02/dwi/sub-02_run-03_dwi"
    source = Path(str(stem) + ".nii.gz")
    before = hash_file(source)
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
    qtbot.waitUntil(lambda: window.load_thread is None, timeout=30000)
    assert window.dataset is not None

    window.focus_workflow()
    workflow = window.workflow
    workflow.template.setCurrentIndex(2)
    workflow.source.setText("OpenNeuro ds007739 v1.0.0 raw BIDS acquisition, sub-02 run-03")
    workflow.confirm.click()
    workflow.selections["denoise"].setChecked(True)
    workflow.selections["gibbs"].setChecked(True)
    gibbs = next(node for node in workflow.plan.to_dict()["nodes"] if node["operation"] == "gibbs")
    assert gibbs["status"] == "uncertain"
    assert any("Slice axis is undocumented" in reason for reason in gibbs["reasons"])
    workflow.selections["gibbs"].setChecked(False)
    assert workflow.plan.to_dict()["status"] == "eligible"
    execution = workflow.execution
    execution.tool_directory.setText("/usr/local/mrtrix3/bin")
    execution.output.setText(str(tmp_path / "real-denoise"))
    execution.prepare.click()
    qtbot.waitUntil(lambda: execution.thread is None, timeout=120000)
    assert execution.plan is not None, execution.status.text()
    assert "dwidenoise" in execution.commands.toPlainText()
    assert not any(
        "mrdegibbs" in command["argv"][0] for command in execution.plan.to_dict()["commands"]
    )
    execution.run.click()
    qtbot.waitUntil(lambda: execution.thread is None, timeout=300000)
    assert execution.manifest["status"] == "validated_pending_qc", execution.status.text()
    assert hash_file(source) == before


@pytest.mark.skipif(
    not os.environ.get("TRACTOSPHERE_REAL_DATA_ROOT")
    or os.environ.get("QT_QPA_PLATFORM") != "cocoa",
    reason="Requires explicitly supplied public dataset and native Cocoa GUI",
)
def test_real_raw_dwi_mask_gui(window, qtbot, tmp_path):
    root = Path(os.environ["TRACTOSPHERE_REAL_DATA_ROOT"])
    stem = root / "sub-02/dwi/sub-02_run-03_dwi"
    source = Path(str(stem) + ".nii.gz")
    before = hash_file(source)
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
    qtbot.waitUntil(lambda: window.load_thread is None, timeout=30000)
    assert window.dataset is not None
    window.focus_workflow()
    workflow = window.workflow
    workflow.template.setCurrentIndex(2)
    workflow.source.setText("OpenNeuro ds007739 v1.0.0 raw BIDS acquisition, sub-02 run-03")
    workflow.confirm.click()
    workflow.selections["mask"].setChecked(True)
    assert workflow.plan.to_dict()["status"] == "eligible"
    execution = workflow.execution
    execution.tool_directory.setText("/usr/local/mrtrix3/bin")
    execution.output.setText(str(tmp_path / "real-mask"))
    execution.prepare.click()
    qtbot.waitUntil(lambda: execution.thread is None, timeout=60000)
    assert execution.plan is not None, execution.status.text()
    execution.run.click()
    qtbot.waitUntil(lambda: execution.thread is None, timeout=120000)
    assert execution.manifest["status"] == "validated_pending_qc", execution.status.text()
    assert hash_file(source) == before


@pytest.mark.skipif(
    not os.environ.get("TRACTOSPHERE_REAL_DENOISED")
    or not os.environ.get("TRACTOSPHERE_REAL_MASK")
    or os.environ.get("QT_QPA_PLATFORM") != "cocoa",
    reason="Requires GUI-produced denoised public DWI and native Cocoa GUI",
)
def test_real_denoised_dwi_csd_gui(window, qtbot, tmp_path):
    denoised = Path(os.environ["TRACTOSPHERE_REAL_DENOISED"])
    mask = Path(os.environ["TRACTOSPHERE_REAL_MASK"])
    before = hash_file(denoised)
    window.focus_models()
    page = window.scientific
    page.tool_directory = Path("/usr/local/mrtrix3/bin")
    page.import_request(
        ImportRequest(
            "dwi",
            denoised,
            declarations={
                "processing_state": "unknown",
                "subject_id": "sub-02",
                "space_id": "sub-02_dwi_run-03_native",
            },
            provenance="MRtrix3 denoising from ds007739 sub-02 run-03; correction and scientific QC pending.",
        )
    )
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert len(page.artifacts) == 1, page.status.text()
    page.import_request(
        ImportRequest(
            "mask",
            mask,
            declarations={"subject_id": "sub-02", "space_id": "sub-02_dwi_run-03_native"},
            provenance="MRtrix3 dwi2mask derivative from exact raw DWI; QC pending.",
        )
    )
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert len(page.artifacts) == 2, page.status.text()
    panel = page.csd
    page.tabs.setCurrentWidget(panel)
    panel.source.setCurrentIndex(panel.source.findData(page.artifacts[0].id))
    panel.mask.setCurrentIndex(panel.mask.findData(page.artifacts[1].id))
    panel.response_mode.setCurrentIndex(panel.response_mode.findData("dhollander"))
    panel.shells.setText("0,200,950,2300")
    panel.lmax.setValue(6)
    panel.output.setText(str(tmp_path / "real-csd-attempt"))
    panel.guided.click()
    assert len(page.tasks) == 2, page.status.text()
    panel.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=120000)
    assert panel.plan is not None, page.status.text()
    panel.approval.setChecked(True)
    panel.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=300000)
    assert panel.manifest["status"] == "validated_pending_qc", page.status.text()
    assert hash_file(denoised) == before


@pytest.mark.skipif(
    not os.environ.get("TRACTOSPHERE_REAL_FOD")
    or not os.environ.get("TRACTOSPHERE_REAL_FA")
    or not os.environ.get("TRACTOSPHERE_REAL_MASK")
    or os.environ.get("QT_QPA_PLATFORM") != "cocoa",
    reason="Requires GUI-produced public FOD/FA and native Cocoa GUI",
)
def test_real_fod_tracking_filter_density_gui(window, qtbot, tmp_path):
    fod_path = Path(os.environ["TRACTOSPHERE_REAL_FOD"])
    fa_path = Path(os.environ["TRACTOSPHERE_REAL_FA"])
    mask_path = Path(os.environ["TRACTOSPHERE_REAL_MASK"])
    before = {p: hash_file(p) for p in (fod_path, fa_path, mask_path)}
    window.focus_tracking_plan()
    page = window.scientific
    page.tool_directory = Path("/usr/local/mrtrix3/bin")
    common = {"subject_id": "sub-02", "space_id": "sub-02_dwi_run-03_native"}
    for request in (
        ImportRequest(
            "fod",
            fod_path,
            declarations={
                **common,
                "normalization": "not_done",
                "sh_basis": "mrtrix3_real_symmetric",
                "sh_frame": "world_ras",
                "tissue": "wm",
            },
            provenance="GUI-produced MSMT-CSD WM FOD from ds007739 sub-02 run-03; scientific QC pending.",
        ),
        ImportRequest(
            "mask",
            mask_path,
            declarations=common,
            provenance="MRtrix3 dwi2mask from exact raw DWI; QC pending.",
        ),
        ImportRequest(
            "anatomy",
            fa_path,
            declarations={**common, "space_kind": "DWI_NATIVE"},
            provenance="GUI-produced DTI FA in native DWI grid; QC pending.",
        ),
    ):
        page.import_request(request)
        qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert len(page.artifacts) == 3, page.status.text()
    fod, mask, reference = page.artifacts
    panel = page.tracking
    page.tabs.setCurrentWidget(panel)
    panel.method.setCurrentIndex(panel.method.findData("tckgen.sdstream"))
    panel.source.setCurrentIndex(panel.source.findData(fod.id))
    panel.mask.setCurrentIndex(panel.mask.findData(mask.id))
    panel.seed_method.setCurrentIndex(panel.seed_method.findData("image"))
    panel.seed.setCurrentIndex(panel.seed.findData(mask.id))
    panel.count.setValue(100)
    panel.seeds.setValue(100000)
    panel.minimum.setValue(10)
    panel.maximum.setValue(150)
    panel.cutoff.setValue(0.05)
    panel.seed_cutoff.setValue(0.05)
    panel.attempts.setValue(50)
    panel.output.setText(str(tmp_path / "real-tracking"))
    panel.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=120000)
    assert panel.plan is not None, page.status.text()
    panel.approval.setChecked(True)
    panel.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=180000)
    assert panel.manifest["status"] == "validated_pending_qc", page.status.text()
    generated = next(a for a in page.artifacts if a.kind == "tractogram")
    panel.open_result()
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    qtbot.waitUntil(
        lambda: window.scientific_viewer is not None and not window.scientific_viewer.tasks,
        timeout=30000,
    )
    assert window.scientific_viewer.groups[0].total == 100

    window.focus_tracking_plan()
    panel.operation.setCurrentIndex(panel.operation.findData("filter"))
    panel.tracts.setCurrentIndex(panel.tracts.findData(generated.id))
    panel.reference.setCurrentIndex(panel.reference.findData(reference.id))
    panel.filter_limit.setChecked(True)
    panel.filter_count.setValue(50)
    panel.filter_minimum.setValue(0)
    panel.filter_maximum.setValue(1000)
    panel.output.setText(str(tmp_path / "real-filter"))
    panel.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=60000)
    assert panel.plan is not None, page.status.text()
    panel.approval.setChecked(True)
    panel.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=120000)
    assert panel.manifest["status"] == "validated_pending_qc", page.status.text()
    filtered = next(
        a for a in page.artifacts if a.kind == "tractogram" and "filtered" in a.payload["path"]
    )

    panel.operation.setCurrentIndex(panel.operation.findData("density"))
    panel.tracts.setCurrentIndex(panel.tracts.findData(filtered.id))
    panel.reference.setCurrentIndex(panel.reference.findData(reference.id))
    panel.output.setText(str(tmp_path / "real-density"))
    panel.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=60000)
    assert panel.plan is not None, page.status.text()
    panel.approval.setChecked(True)
    panel.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=120000)
    assert panel.manifest["status"] == "validated_pending_qc", page.status.text()
    assert any(a.kind == "density" for a in page.artifacts)
    assert all(hash_file(p) == digest for p, digest in before.items())


@pytest.mark.skipif(
    not os.environ.get("TRACTOSPHERE_REAL_FOD")
    or not os.environ.get("TRACTOSPHERE_REAL_FA")
    or not os.environ.get("TRACTOSPHERE_REAL_TRACKS")
    or os.environ.get("QT_QPA_PLATFORM") != "cocoa",
    reason="Requires GUI-produced public tractogram/FOD/FA and native Cocoa GUI",
)
def test_real_tractogram_sift2_gui(window, qtbot, tmp_path):
    tracks = Path(os.environ["TRACTOSPHERE_REAL_TRACKS"])
    fod = Path(os.environ["TRACTOSPHERE_REAL_FOD"])
    reference = Path(os.environ["TRACTOSPHERE_REAL_FA"])
    before = {p: hash_file(p) for p in (tracks, fod, reference)}
    common = {"subject_id": "sub-02", "space_id": "sub-02_dwi_run-03_native"}
    window.focus_tracking_plan()
    page = window.scientific
    page.tool_directory = Path("/usr/local/mrtrix3/bin")
    for request in (
        ImportRequest(
            "tractogram",
            tracks,
            declarations={**common, "sift2_fod_sha256": before[fod]},
            provenance="GUI-produced tractogram from exact WM FOD; QC pending.",
        ),
        ImportRequest(
            "fod",
            fod,
            declarations={
                **common,
                "normalization": "not_done",
                "sh_basis": "mrtrix3_real_symmetric",
                "sh_frame": "world_ras",
                "tissue": "wm",
            },
            provenance="GUI-produced MSMT-CSD WM FOD; QC pending.",
        ),
        ImportRequest(
            "anatomy",
            reference,
            declarations={**common, "space_kind": "DWI_NATIVE"},
            provenance="GUI-produced DTI FA native-grid reference; QC pending.",
        ),
    ):
        page.import_request(request)
        qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert len(page.artifacts) == 3, page.status.text()
    t, f, r = page.artifacts
    panel = page.sift2
    page.tabs.setCurrentWidget(panel)
    panel.tracts.setCurrentIndex(panel.tracts.findData(t.id))
    panel.fod.setCurrentIndex(panel.fod.findData(f.id))
    panel.reference.setCurrentIndex(panel.reference.findData(r.id))
    panel.policy.setCurrentIndex(panel.policy.findData("none"))
    panel.output.setText(str(tmp_path / "real-sift2"))
    panel.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=60000)
    assert panel.plan is not None, page.status.text()
    panel.approval.setChecked(True)
    panel.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=180000)
    assert panel.manifest["status"] == "validated_pending_qc", page.status.text()
    assert len(panel.values) == 100
    assert all(hash_file(p) == digest for p, digest in before.items())


@pytest.mark.skipif(
    not os.environ.get("TRACTOSPHERE_REAL_DATA_ROOT")
    or os.environ.get("QT_QPA_PLATFORM") != "cocoa",
    reason="Requires explicitly supplied public dataset and native Cocoa GUI",
)
def test_real_topup_eddy_gui_evidence_review(window, qtbot, tmp_path):
    root = Path(os.environ["TRACTOSPHERE_REAL_DATA_ROOT"])
    stem = root / "sub-02/dwi/sub-02_run-03_dwi"
    calibration = root / "sub-02/dwi/sub-02_run-02_dwi.nii.gz"
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
    qtbot.waitUntil(lambda: window.load_thread is None, timeout=30000)
    assert window.dataset is not None
    window.focus_workflow()
    workflow = window.workflow
    workflow.template.setCurrentIndex(2)
    workflow.source.setText("OpenNeuro ds007739 v1.0.0 raw BIDS acquisition, sub-02 run-03")
    workflow.confirm.click()
    workflow.selections["correction"].setChecked(True)
    execution = workflow.execution
    execution.references.setPlainText(str(calibration))
    execution.correction_inputs["bids_root"].setText(str(root))
    execution.correction_inputs["fsl_directory"].setText(str(Path(os.environ["FSLDIR"]) / "bin"))
    execution.tool_directory.setText("/usr/local/mrtrix3/bin")
    execution.output.setText(str(tmp_path / "real-topup-eddy"))
    execution.prepare.click()
    qtbot.waitUntil(lambda: execution.thread is None, timeout=120000)
    assert execution.plan is None
    assert "documented native k slice axis" in execution.status.text()


@pytest.mark.skipif(
    not os.environ.get("TRACTOSPHERE_REAL_DATA_ROOT")
    or not os.environ.get("TRACTOSPHERE_ANTS_BIN")
    or os.environ.get("QT_QPA_PLATFORM") != "cocoa",
    reason="Requires explicitly supplied public dataset and native Cocoa GUI",
)
def test_real_n4_gui_upstream_gate(window, qtbot, tmp_path):
    root = Path(os.environ["TRACTOSPHERE_REAL_DATA_ROOT"])
    stem = root / "sub-02/dwi/sub-02_run-03_dwi"
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
    qtbot.waitUntil(lambda: window.load_thread is None, timeout=30000)
    assert window.dataset is not None
    window.focus_workflow()
    workflow = window.workflow
    workflow.template.setCurrentIndex(2)
    workflow.source.setText("OpenNeuro ds007739 v1.0.0 raw BIDS acquisition, sub-02 run-03")
    workflow.confirm.click()
    workflow.selections["bias"].setChecked(True)
    assert workflow.plan.to_dict()["status"] != "eligible"
    execution = workflow.execution
    execution.bias_inputs["ants_directory"].setText(os.environ["TRACTOSPHERE_ANTS_BIN"])
    execution.output.setText(str(tmp_path / "real-n4"))
    execution.prepare.click()
    qtbot.waitUntil(lambda: execution.thread is None, timeout=30000)
    assert execution.plan is None
    assert (
        "eddy" in execution.status.text().lower() or "correction" in execution.status.text().lower()
    )


@pytest.mark.skipif(
    not os.environ.get("TRACTOSPHERE_REAL_DATA_ROOT")
    or os.environ.get("QT_QPA_PLATFORM") != "cocoa",
    reason="Requires explicitly supplied public dataset and native Cocoa GUI",
)
def test_real_bids_batch_conversion_gui(qapp, qtbot, tmp_path):
    from PySide6.QtCore import QSettings, Qt

    from dmri_studio.core.batch.engine import read_journal
    from dmri_studio.gui.batch import BatchPage
    from dmri_studio.gui.settings import Settings
    from dmri_studio.gui.theme import Theme

    root = Path(os.environ["TRACTOSPHERE_REAL_DATA_ROOT"])
    settings = Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    settings.set("dependencies/MRtrix3", "/usr/local/mrtrix3/bin")
    page = BatchPage(Theme(qapp), settings=settings)
    qtbot.addWidget(page)
    page.show()
    guided = page.guided
    guided.dataset_root.setText(str(root))
    guided.discover_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert page.study is not None, page.status.text()
    wanted = next(
        a.key
        for a in page.study.acquisitions
        if str(a.paths.get("dwi", "")).endswith("run-03_dwi.nii.gz")
    )
    for row in range(guided.subject_table.rowCount()):
        item = guided.subject_table.item(row, 0)
        if item.data(Qt.ItemDataRole.UserRole) != wanted:
            item.setCheckState(Qt.CheckState.Unchecked)
    guided.advance()
    assert guided.workflow_choice.currentData() == "convert"
    guided.history_confirm.setChecked(True)
    guided.advance()
    guided.output.setText(str(tmp_path / "real-batch-conversion"))
    guided.validate_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=60000)
    assert page.plan is not None, page.status.text()
    assert page.plan.to_dict()["rows"][0]["status"] == "ELIGIBLE", page.plan.to_json()
    digest = page.plan.digest
    guided.approve.setChecked(True)
    guided.run.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=180000)
    assert read_journal(page.folder)["state"] == "COMPLETED", page.status.text()
    page.load_batch(page.folder)
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert page.historical_plan.digest == digest


@pytest.mark.skipif(
    not os.environ.get("TRACTOSPHERE_REAL_DATA_ROOT")
    or not os.environ.get("TRACTOSPHERE_REAL_FA")
    or os.environ.get("QT_QPA_PLATFORM") != "cocoa",
    reason="Requires public T1 and GUI-produced FA on native Cocoa",
)
def test_real_t1_fa_registration_eligibility_gui(window, qtbot, tmp_path):
    root = Path(os.environ["TRACTOSPHERE_REAL_DATA_ROOT"])
    t1 = root / "sub-02/anat/sub-02_T1w.nii.gz"
    fa = Path(os.environ["TRACTOSPHERE_REAL_FA"])
    window.focus_registration()
    page = window.scientific
    page.tool_directory = Path("/usr/local/mrtrix3/bin")
    page.import_request(
        ImportRequest(
            "anatomy",
            t1,
            sidecar=t1.with_name("sub-02_T1w.json"),
            declarations={
                "subject_id": "sub-02",
                "acquisition_id": "ds007739_sub-02_T1w",
                "space_kind": "T1_NATIVE",
                "space_id": "sub-02_T1w_native",
                "anatomical_modality": "T1w",
                "processing_state": "raw",
            },
            provenance="OpenNeuro ds007739 raw defaced T1w; anatomical QC pending.",
        )
    )
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    page.import_request(
        ImportRequest(
            "anatomy",
            fa,
            declarations={
                "subject_id": "sub-02",
                "acquisition_id": "ds007739_sub-02_run-03",
                "space_kind": "DWI_NATIVE",
                "space_id": "sub-02_dwi_run-03_native",
                "anatomical_modality": "FA",
                "processing_state": "unknown",
            },
            provenance="GUI-produced FA from raw public DWI; correction history not established.",
        )
    )
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert len(page.artifacts) == 2, page.status.text()
    guided = page.registration.guided
    guided.select_goal("align_two")
    assert guided.moving.findData(page.artifacts[1].id) < 0
    guided.imported_anatomy.setCurrentIndex(guided.imported_anatomy.findData(page.artifacts[1].id))
    assert "correction/reference history" in guided.imported_details.text().lower()
    guided.reference.setCurrentIndex(guided.reference.findData(page.artifacts[0].id))
    guided.output.setText(str(tmp_path / "real-registration"))
    guided.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert page.registration.plan is None


@pytest.mark.skipif(
    not os.environ.get("TRACTOSPHERE_REAL_DATA_ROOT")
    or not os.environ.get("TRACTOSPHERE_REAL_FA")
    or os.environ.get("QT_QPA_PLATFORM") != "cocoa",
    reason="Requires public T1 and GUI-produced FA on native Cocoa",
)
def test_real_t1_five_tissue_review_gate_gui(window, qtbot, tmp_path):
    root = Path(os.environ["TRACTOSPHERE_REAL_DATA_ROOT"])
    t1 = root / "sub-02/anat/sub-02_T1w.nii.gz"
    fa = Path(os.environ["TRACTOSPHERE_REAL_FA"])
    window.focus_tracking_plan()
    page = window.scientific
    page.tool_directory = Path("/usr/local/mrtrix3/bin")
    common = {"subject_id": "sub-02", "space_id": "sub-02_T1w_native"}
    for request in (
        ImportRequest(
            "anatomy",
            t1,
            sidecar=t1.with_name("sub-02_T1w.json"),
            declarations={
                **common,
                "space_kind": "T1_NATIVE",
                "anatomical_modality": "T1w",
                "acquisition_id": "ds007739_sub-02_T1w",
            },
            provenance="OpenNeuro ds007739 raw defaced T1w; anatomical review pending.",
        ),
        ImportRequest(
            "anatomy",
            fa,
            declarations={
                "subject_id": "sub-02",
                "space_id": "sub-02_dwi_run-03_native",
                "space_kind": "DWI_NATIVE",
                "anatomical_modality": "FA",
                "acquisition_id": "ds007739_sub-02_run-03",
            },
            provenance="GUI-produced DTI FA; anatomical review pending.",
        ),
    ):
        page.import_request(request)
        qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert len(page.artifacts) == 2, page.status.text()
    panel = page.anatomy
    page.tabs.setCurrentWidget(panel)
    panel.operation.setCurrentIndex(panel.operation.findData("5ttgen.fsl"))
    panel.source.setCurrentIndex(panel.source.findData(page.artifacts[0].id))
    panel.reference.setCurrentIndex(panel.reference.findData(page.artifacts[1].id))
    panel.extraction.setCurrentIndex(panel.extraction.findData("automatic"))
    panel.output.setText(str(tmp_path / "real-five-tissue"))
    panel.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert panel.plan is None
    assert "review" in page.status.text().lower() or "space" in page.status.text().lower()
