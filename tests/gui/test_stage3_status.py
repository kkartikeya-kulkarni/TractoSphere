"""Native asynchronous state regressions, using actual scalar/DWI planes.
No fake progress or model inference; all input files remain unchanged.
"""

import time
from pathlib import Path

import nibabel as nib
import numpy as np

from dmri_studio.gui.operation_state import OperationState, State


def test_scoped_terminal_history_and_late_events(qapp):
    state = OperationState()
    old = state.begin(State.LOADING, "old")
    new = state.begin(State.LOADING, "new")
    assert not state.update(old, State.FAILED, "old error")
    assert state.update(new, State.READY, "new ready")
    assert not state.busy and not state.update(new, State.LOADING, "late progress")
    assert not state.update(old, State.FAILED, "old error delivered after new completion")
    assert state.state == State.READY and state.message == "new ready"
    retry = state.begin(State.PROCESSING, "run")
    assert state.update(retry, State.WARNING, "Completed; scientific QC needs review")
    assert len(state.history) == 3 and state.state == State.WARNING
    assert state.history[0]["state"] == "cancelled"
    token = state.begin(State.PROCESSING, "second")
    assert state.update(token, State.CANCELLED, "Cancelled")
    assert not state.update(token, State.COMPLETED, "wrong success")


def test_viewer_old_failure_cannot_overwrite_new_success(window, qtbot, tmp_path, monkeypatch):
    import dmri_studio.gui.scientific_viewer as module

    data = np.arange(7 * 8 * 9 * 3, dtype="f4").reshape(7, 8, 9, 3)
    p = tmp_path / "valid.nii"
    image = nib.Nifti1Image(data, np.diag([2, 2, 2, 1]))
    image.header.set_xyzt_units("mm")
    nib.save(image, p)
    real = module.ImageVolume

    def read(path):
        if Path(path).name == "old-missing.nii":
            time.sleep(0.05)
            raise ValueError("old failure that must stay inactive")
        return real(path)

    monkeypatch.setattr(module, "ImageVolume", read)
    window.focus_viewer()
    v = window.scientific_viewer
    v.load_image(tmp_path / "old-missing.nii")
    v.load_image(p)
    assert v.image_state.busy and v.image_loading
    qtbot.waitUntil(lambda: not v.tasks, timeout=10000)
    assert v.image_state.state == State.READY and not v.image_loading
    assert v.reference.path == p and "old failure" not in v.status.text()
    assert all(
        view.canvas.image is not None and "Reading native" not in view.canvas.message
        for view in v.views.values()
    )
    for index in [2, 1, 0, 2]:
        v.volume.setValue(index)
    qtbot.waitUntil(lambda: not v.tasks, timeout=10000)
    np.testing.assert_array_equal(v.data, data[..., 2])
    assert v.image_state.state == State.READY
    for theme in ["light", "dark"]:
        window.theme.apply(theme)
        v.load_image(p)
        qtbot.waitUntil(lambda: not v.tasks, timeout=10000)
        assert v.image_state.state == State.READY
    corrupted = tmp_path / "corrupt.nii"
    corrupted.write_bytes(b"not a NIfTI")
    v.load_image(corrupted)
    qtbot.waitUntil(lambda: not v.tasks, timeout=10000)
    assert v.image_state.state == State.FAILED and not v.image_state.busy
    assert v.reference.path == p and "Previous image retained" in v.status.text()
    v.load_image(p)
    qtbot.waitUntil(lambda: not v.tasks, timeout=10000)
    assert v.image_state.state == State.READY and "Operation failed" not in v.status.text()
    assert any(h["state"] == "failed" for h in v.image_state.history)


def test_load_feedback_no_success_banner_or_duplicate_toast(window, qtbot, dataset_folder):
    page = window.page
    page.fields["dwi"].setText(str(dataset_folder / "data.nii"))
    page.fields["bval"].setText(str(dataset_folder / "bvals"))
    page.fields["bvec"].setText(str(dataset_folder / "bvecs"))
    window.start_load()
    qtbot.waitUntil(
        lambda: (
            window.load_thread is None and page.preview.thread is None and not page.preview.pending
        ),
        timeout=15000,
    )
    assert window.dataset is not None
    assert not window.toast.isVisible()
    assert not page.busy and page.progress.isHidden()
    assert not page.banner.isVisible() or page.state == "warning"
    assert window.load_state.state in {State.READY, State.WARNING}
    for view in page.preview.views.values():
        assert view.canvas.image is not None and "Reading native" not in view.canvas.message


def test_neutral_toast_deduplication_and_scope(window):
    toast = window.toast
    toast.show_message("Useful confirmation", "success", scope="save")
    assert toast.isVisible() and toast.timer.isActive()
    toast.show_message("Useful confirmation", "success", scope="save")
    toast.resolve("other")
    assert toast.isVisible()
    toast.resolve("save")
    assert toast.isHidden()
    toast.show_message("Real error", "error", scope="load")
    assert not toast.timer.isActive()
    toast.resolve("load")
    assert toast.isHidden()
