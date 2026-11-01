"""Real H1 reads through GUI workers; source bytes remain unchanged."""

import hashlib

from core.test_bci_inventory import _entry, _manifest, _recording

from dmri_studio.core.bci import inspect_falcon_h1
from dmri_studio.gui.bci_recordings import BciRecordingsPage
from dmri_studio.gui.theme import Theme


def page(qapp, qtbot, *, inventory=False):
    widget = BciRecordingsPage(Theme(qapp), inventory=inventory)
    qtbot.addWidget(widget)
    return widget


def wait(widget, qtbot):
    qtbot.waitUntil(lambda: widget.thread is None, timeout=10000)


def test_actual_source_matches_core_and_recovers_from_error(qapp, qtbot, tmp_path):
    source = _recording(tmp_path / "recording.nwb")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    widget = page(qapp, qtbot)
    widget.load(source)
    assert not widget.shutdown()
    assert not widget.open_button.isEnabled()
    wait(widget, qtbot)
    assert widget.report == inspect_falcon_h1(source)
    assert "6 samples" in widget.summary.text()
    assert "denominator: 6 samples" in widget.summary.text()
    units = next(
        widget.details.topLevelItem(i)
        for i in range(widget.details.topLevelItemCount())
        if widget.details.topLevelItem(i).text(0) == "units"
    )
    units.setExpanded(True)
    assert any(units.child(i).text(0) == "per_unit" for i in range(units.childCount()))
    widget.load(tmp_path / "sensitive name" / "missing.nwb")
    wait(widget, qtbot)
    assert widget.report is None
    assert widget.details.topLevelItemCount() == 0
    assert str(tmp_path) not in widget.status.text()
    assert widget.open_button.isEnabled()
    widget.load(source)
    wait(widget, qtbot)
    assert widget.report is not None
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_inventory_reports_every_failure_and_retains_success(qapp, qtbot, tmp_path):
    source = _recording(tmp_path / "recording.nwb")
    entry = _entry(source, "one")
    missing = {**entry, "entry_id": "missing", "path": str(tmp_path / "private name/missing.nwb")}
    manifest = _manifest(tmp_path, [entry, missing])
    widget = page(qapp, qtbot, inventory=True)
    widget.load(manifest)
    wait(widget, qtbot)
    assert "incomplete" in widget.status.text()
    assert "1 of 2 entries succeeded" in widget.summary.text()
    assert len(widget.report["recordings"]) == 2
    assert widget.report["recordings"][1]["status"] == "error"
    assert not widget.report["comparability"]["descriptive_eligible"]
    finding = widget.recordings.item(1, 7).text()
    assert "Listed recording unavailable" in finding
    assert str(tmp_path) not in finding
    widget.details.expandAll()
    from PySide6.QtWidgets import QTreeWidgetItemIterator

    it = QTreeWidgetItemIterator(widget.details)
    while it.value():
        assert str(tmp_path) not in it.value().text(1)
        it += 1


def test_path_with_spaces_is_not_partially_disclosed():
    from dmri_studio.gui.bci_recordings import _finding, _text

    private = "/Users/researcher/Private study/participant 01.nwb"
    assert _text(private) == "[text omitted: local path]"
    assert _text(f"Source: {private}") == "[text omitted: local path]"
    finding = _finding([f"[Errno 2] No such file or directory: '{private}'"])
    assert finding == "Listed recording unavailable; check its manifest path."


def test_picker_cancel_and_navigation(window, qtbot, monkeypatch):
    window.focus_bci_inspection()
    assert window.workspace.currentWidget() is window._bci_inspection
    monkeypatch.setattr(
        "dmri_studio.gui.bci_recordings.QFileDialog.getOpenFileName", lambda *_args: ("", "")
    )
    window._bci_inspection.open_button.click()
    assert window._bci_inspection.thread is None
    window.focus_bci_inventory()
    assert window.workspace.currentWidget() is window._bci_inventory
    window.focus_bci_results()
    assert window.workspace.currentWidget() is window._bci_results


def test_close_waits_for_active_recording_inspection(window, qtbot, monkeypatch, tmp_path):
    from threading import Event

    import dmri_studio.gui.bci_recordings as recordings_gui

    source = _recording(tmp_path / "recording.nwb")
    started, release = Event(), Event()
    original = recordings_gui.inspect_falcon_h1

    def slow_inspection(path):
        started.set()
        assert release.wait(10)
        return original(path)

    monkeypatch.setattr(recordings_gui, "inspect_falcon_h1", slow_inspection)
    window.focus_bci_inspection()
    window._bci_inspection.load(source)
    qtbot.waitUntil(started.is_set, timeout=10000)
    window.close()
    assert window.isVisible()
    assert not window._bci_inspection.shutdown()
    release.set()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=15000)
