"""Presentation does not change scientific status, provenance or tool gates."""

from PySide6.QtWidgets import QPushButton


def test_secondary_empty_states_do_not_show_idle_evidence(window):
    assert not window.qc.empty.isHidden()
    assert window.qc.content.isHidden()
    assert not window.results.empty.isHidden()
    assert window.results.steps.isHidden()
    assert window.projects.table.isHidden()


def test_recorded_measurements_and_raw_values_remain_identical(window):
    qc = window.qc
    source = '{"cnr": {"invalid_voxels": 6}, "scientific_acceptance": "pending"}'
    qc.present_measurements(source)
    assert qc.text.toPlainText() == source
    assert qc.measurements.topLevelItem(0).text(0) == "cnr"
    assert qc.measurements.topLevelItem(0).child(0).text(1) == "6"
    assert qc.measurements.topLevelItem(1).text(1) == '"pending"'
    assert qc.review_tabs.currentWidget() is qc.measurements
    assert qc.catalog is None
    qc.present_measurements("plain execution log")
    assert qc.review_tabs.currentWidget() is qc.text


def test_setup_summaries_keep_one_scan_and_advanced_diagnostics(window):
    manager = window.preferences.dependencies
    assert set(manager.family_labels) == {"MRtrix3", "FSL", "ANTs"}
    scans = [b for b in manager.findChildren(QPushButton) if b.text() == "Scan Again"]
    assert len(scans) == 1
    assert manager.advanced.content.isHidden()
    for family in manager.fields:
        assert manager.family_details[family].content.isHidden()
    assert window.dependencies.thread is None  # No automatic probes or installation.
