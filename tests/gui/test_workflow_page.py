"""Interactive history review must match the headless planning rules."""

import json

from PySide6.QtWidgets import QFileDialog

from dmri_studio.core.workflow import build_plan


def activate(window, qtbot, folder):
    """Load through the normal worker and navigate to the real planning screen."""
    window.page.fill_folder(folder)
    window.start_load()
    qtbot.waitUntil(lambda: window.load_thread is None and window.dataset is not None)
    window.focus_workflow()
    return window.workflow


def test_hcp_mask_plan_matches_core(window, qtbot, dataset_folder):
    """HCP evidence is explicit and masking remains independent."""
    page = activate(window, qtbot, dataset_folder)
    page.template.setCurrentIndex(1)
    assert not page.history.confirmed
    page.confirm.click()
    page.selections["mask"].setChecked(True)
    assert "Blocked" in page.method_statuses["denoise"].text()
    assert page.history.resolve("interpolation") == "done"
    assert next(c for c in page.history.claims if c.stage == "interpolation").evidence == "inferred"
    expected = build_plan(window.dataset, page.history, ("mask",)).to_dict()
    actual = page.plan.to_dict()
    assert actual["nodes"] == expected["nodes"]
    assert actual["status"] == "eligible"
    assert actual["approved_for_execution"] is False


def test_history_edit_invalidates_confirmation_and_export(window, qtbot, dataset_folder):
    """An old eligible plan never survives an evidence edit."""
    page = activate(window, qtbot, dataset_folder)
    page.confirm.click()
    page.selections["mask"].setChecked(True)
    assert page.export.isEnabled()
    page.states["motion"].setCurrentIndex(2)
    assert not page.history.confirmed and page.plan is None
    assert not page.export.isEnabled()
    page.confirm.click()
    assert not page.history.confirmed  # Conclusive claims need a source.
    page.source.setText("User processing record")
    page.confirm.click()
    assert page.history.confirmed


def test_theme_navigation_unload_keep_viewer_contract(window, qtbot, dataset_folder):
    """Planning navigation does not alter viewer position or persisted evidence."""
    page = activate(window, qtbot, dataset_folder)
    page.confirm.click()
    page.selections["mask"].setChecked(True)
    position = window.page.preview.position
    history = page.history
    window.toggle_theme()
    assert page.history == history
    assert page.selections["mask"].text().startswith("Selected")
    assert page.selections["mask"].property("selected") is True
    window.focus_load()
    assert window.workspace.currentWidget() is window.scroll
    assert window.page.preview.position == position
    window.focus_workflow()
    assert window.workspace.currentWidget() is window.workflow_scroll
    window.unload()
    assert page.dataset is None and page.plan is None
    navigation = {name: control for control, name in window.nav_buttons}
    assert navigation["Viewer"].isEnabled()  # Standalone image inspection needs no DWI plan.
    assert navigation["Preprocessing"].isEnabled()  # Empty state guides input selection.
    assert not navigation["Data Check"].isEnabled()


def test_export_is_new_proposal_and_does_not_overwrite(
    window, qtbot, dataset_folder, tmp_path, monkeypatch
):
    """The native save action calls the same protected report exporter."""
    page = activate(window, qtbot, dataset_folder)
    page.confirm.click()
    page.selections["mask"].setChecked(True)
    destination = tmp_path / "proposal.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(destination), ""))
    page.export.click()
    payload = json.loads(destination.read_text())
    assert payload["execution_available"] is False
    original = destination.read_bytes()
    page.export.click()
    assert destination.read_bytes() == original


def test_replacement_discards_history_but_failure_retains_it(window, qtbot, dataset_folder):
    """History belongs to the active input, not a candidate that failed loading."""
    page = activate(window, qtbot, dataset_folder)
    page.confirm.click()
    page.selections["mask"].setChecked(True)
    original = page.history
    window.focus_load()
    window.page.show_form()
    window.page.fields["bval"].setText(str(dataset_folder / "missing"))
    # Trigger a real loader failure after the form path precheck was satisfied.
    window.page.fields["bval"].setText(str(dataset_folder / "bvals"))
    (dataset_folder / "bvals").write_text("0 1000")
    window.start_load()
    qtbot.waitUntil(lambda: window.load_thread is None)
    assert page.history == original
    (dataset_folder / "bvals").write_text("0 1000 1000 2000")
    window.start_load()
    qtbot.waitUntil(lambda: window.load_thread is None)
    assert not page.history.confirmed and page.plan is None


def test_raw_history_has_eligible_denoising_explanation(window, qtbot, dataset_folder):
    """All-not-done history must not index an empty scientific-reasons tuple."""
    page = activate(window, qtbot, dataset_folder)
    page.template.setCurrentIndex(2)
    page.source.setText("Synthetic data with no previous preprocessing")
    page.confirm.click()
    page.selections["denoise"].setChecked(True)
    assert page.plan.to_dict()["status"] == "eligible"
    assert "Eligible" in page.method_statuses["denoise"].text()
    assert "MP-PCA" in page.method_statuses["denoise"].text()
