"""Journal presentation should not rebuild unchanged or inactive batch pages."""

import json

from dmri_studio.gui import batch as batch_module


def _row(index, status="PENDING"):
    return {
        "key": f"subject-{index}/session-01/diffusion/metrics",
        "acquisition": {
            "subject_id": f"subject-{index}",
            "session_id": "01",
            "acquisition_id": "diffusion",
        },
        "node": {"id": "metrics"},
        "status": status,
        "qc_status": "NOT_ASSESSED",
        "issues": [],
    }


def test_unchanged_journal_skips_read_and_render(window, tmp_path, monkeypatch):
    window.focus_batch()
    page = window.batch
    folder = tmp_path / "batch"
    folder.mkdir()
    journal = folder / "journal.json"
    rows = {str(i): _row(i) for i in range(100)}
    journal.write_text(json.dumps({"state": "READY", "nodes": rows}))
    page.folder = folder

    counts = {"read": 0, "render": 0}
    original_read = batch_module.read_journal
    original_render = page.render

    def read(*args, **kwargs):
        counts["read"] += 1
        return original_read(*args, **kwargs)

    def render():
        counts["render"] += 1
        original_render()

    monkeypatch.setattr(batch_module, "read_journal", read)
    monkeypatch.setattr(page, "render", render)
    page.refresh()
    assert counts == {"read": 1, "render": 1}
    page.table.selectRow(5)
    selected = page.selected_keys()
    for _ in range(3):
        page.refresh()
    assert counts == {"read": 1, "render": 1}
    assert page.selected_keys() == selected

    rows["5"]["status"] = "COMPLETED"
    journal.write_text(json.dumps({"state": "COMPLETED", "nodes": rows}))
    page.refresh()
    assert counts == {"read": 2, "render": 2}
    assert page.selected_keys() == selected
    assert "COMPLETED" in page.summary.text()


def test_idle_batch_polling_only_when_visible_and_open(window, tmp_path):
    window.focus_batch()
    page = window.batch
    assert not page.timer.isActive()
    page.folder = tmp_path
    page._update_polling()
    assert page.timer.isActive()
    window.focus_start()
    assert not page.timer.isActive()
    window.focus_batch()
    assert page.timer.isActive()

    page.thread = object()
    try:
        window.focus_start()
        assert page.timer.isActive()
    finally:
        page.thread = None
        page._update_polling()
    assert not page.timer.isActive()
