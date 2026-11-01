"""Read-only H1 report workflow through the actual application navigation."""

from bci_report_fixture import synthetic_chronological_report


def test_open_report_from_navigation_with_file_action(window, qtbot, tmp_path, monkeypatch):
    path = tmp_path / "synthetic.json"
    synthetic_chronological_report(path)
    window.focus_bci_results()
    page = window._bci_results
    assert window.workspace.currentWidget() is page
    assert [name for button, name in window.nav_buttons if button.property("selected")] == [
        "BCI Results"
    ]
    monkeypatch.setattr(
        "dmri_studio.gui.bci_results.QFileDialog.getOpenFileName",
        lambda *_args: (str(path), "JSON reports (*.json)"),
    )
    page.open_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert page.view["evaluation"]["included_samples"] == 4
    assert "not independently verified" in page.status.text()
    assert "scientific acceptance: unassigned" in page.provenance.text()
    assert "not performed" in page.provenance.text()
    assert "Calibration selected: 2" in page.selections.text()
    assert "target labels used: no" in page.selections.text()
    assert page.conditions.item(1, 2).text() == "-1"
    assert "units unavailable" in page.feature_metrics.text()
    assert "Observed target trace unavailable" in page.plot_note.text()
    assert page.plot.trace["trial_boundaries"] == [8, 11]
    assert "/private/absent" not in page.provenance.text()
    page.feature.setCurrentText("g3")
    assert page.feature_metrics.text().startswith("g3")
    assert page.plot.trace["series"]["fixed_ridge"][0][0][0] == 8


def test_invalid_report_has_readable_error_and_no_stale_result(window, qtbot, tmp_path):
    valid = tmp_path / "valid.json"
    synthetic_chronological_report(valid)
    window.focus_bci_results()
    page = window._bci_results
    page.load(valid)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema":"future"}')
    page.load(bad)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert "unsupported schema version" in page.status.text()
    assert page.view is None
    assert page.open_button.isEnabled()
    assert "synthetic-asset" not in page.provenance.text()


def test_missing_report_does_not_display_local_path(window, qtbot, tmp_path):
    window.focus_bci_results()
    page = window._bci_results
    missing = tmp_path / "private-participant" / "missing.json"
    page.load(missing)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert "unavailable or unreadable" in page.status.text()
    assert str(tmp_path) not in page.status.text()
