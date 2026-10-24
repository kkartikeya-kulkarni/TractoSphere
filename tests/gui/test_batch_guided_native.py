"""Native scientific walkthrough; run in a permitted Cocoa session on macOS."""

import json
from pathlib import Path

import nibabel as nib
import numpy as np
from PySide6.QtCore import QSettings, Qt

from dmri_studio.core.batch.engine import read_journal
from dmri_studio.gui.batch import BatchPage
from dmri_studio.gui.settings import Settings
from dmri_studio.gui.theme import Theme

BIN = Path("/usr/local/mrtrix3/bin")


def bids_fixture(tmp_path):
    root = tmp_path / "synthetic-bids"
    root.mkdir()
    (root / "dataset_description.json").write_text(
        json.dumps({"Name": "Synthetic", "BIDSVersion": "1.10.0"})
    )
    for subject in ("01", "02"):
        folder = root / f"sub-{subject}" / "dwi"
        folder.mkdir(parents=True)
        stem = folder / f"sub-{subject}_dwi"
        data = np.ones((8, 10, 12, 4), dtype="f4")
        image = nib.Nifti1Image(data, np.diag([2.0, 2.0, 2.0, 1.0]))
        image.set_qform(image.affine, 1)
        image.set_sform(image.affine, 1)
        image.header.set_xyzt_units("mm")
        nib.save(image, str(stem) + ".nii.gz")
        (folder / (stem.name + ".bval")).write_text("0 1000 1000 2000\n")
        (folder / (stem.name + ".bvec")).write_text("0 1 0 0\n0 0 1 0\n0 0 0 1\n")
        (folder / (stem.name + ".json")).write_text(
            json.dumps({"PhaseEncodingDirection": "j", "TotalReadoutTime": 0.04})
        )
    return root


def setup_page(qapp, qtbot, tmp_path):
    settings_path = tmp_path / "settings.ini"
    settings = Settings(QSettings(str(settings_path), QSettings.Format.IniFormat))
    settings.set("dependencies/MRtrix3", str(BIN))
    settings.store.sync()
    restored = Settings(QSettings(str(settings_path), QSettings.Format.IniFormat))
    page = BatchPage(Theme(qapp), settings=restored)
    qtbot.addWidget(page)
    page.show()
    return page


def prepare_guided_plan(page, qtbot, tmp_path):
    guided = page.guided
    guided.dataset_root.setText(str(bids_fixture(tmp_path)))
    guided.discover_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert page.study is not None, page.status.text()
    assert guided.subject_table.rowCount() == 2
    assert len(guided._selected) == 2
    selected = page.study.acquisitions[0].key
    for row in range(guided.subject_table.rowCount()):
        item = guided.subject_table.item(row, 0)
        if item.data(Qt.ItemDataRole.UserRole) != selected:
            item.setCheckState(Qt.CheckState.Unchecked)
    guided.advance()
    assert guided.workflow_choice.currentData() == "convert"
    guided.history_confirm.setChecked(True)
    guided.advance()
    guided.output.setText(str(tmp_path / "new-batch"))
    guided.validate_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert page.plan is not None, page.status.text()
    assert page.plan.to_dict()["selected_acquisition_keys"] == [selected]
    assert page.plan.to_dict()["rows"][0]["status"] == "ELIGIBLE", page.plan.to_json()
    assert guided.pages.currentIndex() == 4
    return selected


def test_guided_bids_subset_reaches_exact_eligible_plan(qapp, qtbot, tmp_path):
    page = setup_page(qapp, qtbot, tmp_path)
    prepare_guided_plan(page, qtbot, tmp_path)
    assert page.folder is None
    assert not page.run_button.isEnabled()
    page.guided.approve.setChecked(True)
    assert page.run_button.isEnabled()
    original = page.plan.digest
    page.guided.previous()
    page.guided.advance()
    assert page.plan.digest == original
    assert page.guided.approve.isChecked()
    page.guided.output.setText(str(tmp_path / "different-batch"))
    assert page.plan is None
    assert not page.guided.approve.isChecked()
    assert not page.run_button.isEnabled()


def test_native_guided_execution_results_qc_and_history(qapp, qtbot, tmp_path):
    page = setup_page(qapp, qtbot, tmp_path)
    prepare_guided_plan(page, qtbot, tmp_path)
    approved = page.plan
    page.guided.approve.setChecked(True)
    page.guided.run.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=45000)
    assert read_journal(page.folder)["state"] == "COMPLETED", page.status.text()
    assert page.guided.monitor_table.rowCount() == 1
    page.guided.advance()
    assert page.guided.pages.currentIndex() == 6
    assert "COMPLETED" in page.guided.results_summary.text()
    page.guided._show_qc()
    assert "unassigned" in page.guided.results_detail.toPlainText()
    historical_folder = page.folder
    page.load_batch(historical_folder)
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert page.historical_plan.digest == approved.digest
    assert page.historical_plan.to_dict()["tool_directory"] == str(BIN)
    assert page.historical_plan.to_dict()["selected_acquisition_keys"] == [
        page.study.acquisitions[0].key
    ]
    assert page.guided.pages.currentIndex() == 6
