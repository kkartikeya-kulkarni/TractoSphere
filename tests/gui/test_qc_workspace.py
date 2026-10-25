"""QC navigation imports existing evidence without a dataset or processing job."""

import json

from dmri_studio.core.qc import artifact


def test_saved_metrics_and_missing_evidence(window, qtbot, tmp_path):
    metrics = tmp_path / "metrics.json"
    metrics.write_text('{"focal_anomalies": 6}')
    catalog = tmp_path / "qc.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "dmri_qc_catalog",
                "identity": {"subject": "synthetic"},
                "stages": [
                    {
                        "id": "eddy",
                        "title": "EDDY",
                        "execution": "completed",
                        "technical": "passed",
                        "automated_qc": "pending",
                        "scientific_review": "required",
                        "findings": ["Six focal anomalies require review"],
                        "artifacts": [artifact(metrics, "metrics", "CNR diagnostics")],
                    }
                ],
            }
        )
    )
    window.focus_qc()
    assert window.workspace.currentWidget() is window.qc
    assert window.dataset is None
    window.qc.load(catalog)
    qtbot.waitUntil(
        lambda: window.qc.thread is None and "focal_anomalies" in window.qc.text.toPlainText(),
        timeout=10000,
    )
    assert "Scientific review: required" in window.qc.stages.item(0).text()
    assert "Six focal" in window.qc.findings.text()
    window.qc.review_tabs.setCurrentIndex(3)
    assert not window.qc.text.isVisibleTo(window.qc.review_tabs)
    assert window.qc.reviewer.isVisibleTo(window.qc.review_tabs)
    assert not window.qc.open_report.isEnabled()
    assert not window.qc.compare.isEnabled()
    window.qc.open_comparison()
    assert window.qc.thread is None
    assert "Select an available" in window.qc.status.text()
    from dmri_studio.core.qc import record_review

    review = record_review(
        window.qc.catalog,
        "eddy",
        "rejected",
        "SYNTHETIC TEST HARNESS",
        "Fixture review only",
        tmp_path,
    )
    window.qc.open_review(review)
    qtbot.waitUntil(lambda: window.qc.thread is None)
    assert "Scientific review: rejected (local user record)" in window.qc.stages.item(0).text()
    assert window.qc.catalog.to_dict()["stages"][0]["technical"] == "passed"
    metrics.write_text("changed")
    window.qc.load(catalog)
    qtbot.waitUntil(lambda: window.qc.thread is None, timeout=10000)
    assert "changed" in window.qc.status.text()
