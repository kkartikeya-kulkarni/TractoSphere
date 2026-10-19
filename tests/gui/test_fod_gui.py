"""Native linked FOD controls, true VTK surfaces, recovery and saved state."""

import nibabel as nib
import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from dmri_studio.gui.operation_state import State


def fod_file(tmp_path, name="fod", nonfinite=False, offset=0):
    values = np.zeros((12, 14, 16, 15), dtype="f4")
    values[..., 0] = 1
    values[..., 3] = 0.7
    if nonfinite:
        values[6, 7, 8, 0] = np.nan
    affine = np.diag([1.2, 1.5, 2.0, 1.0])
    affine[0, 3] = offset
    image = nib.Nifti1Image(values, affine)
    image.header.set_xyzt_units("mm")
    image.set_sform(affine, 1)
    image.set_qform(affine, 1)
    path = tmp_path / (name + ".nii")
    nib.save(image, path)
    return path


def wait(qtbot, v):
    qtbot.waitUntil(lambda: not v.tasks and not v.fod_timer.isActive(), timeout=15000)


def viewer(window):
    window.show_scene("image", auto_image=False)
    return window.scientific_viewer


def test_linked_planes_modes_color_and_scale(window, qtbot, tmp_path):
    v = viewer(window)
    path = fod_file(tmp_path)
    v.load_fod(path)
    wait(qtbot, v)
    assert v.fod is not None and v.fod.degree == 4
    assert ("fod", 0) in v.scene.actors
    assert v.workspace_mode == "image"
    assert all(s.canvas.fod_image is not None for s in v.views.values())
    actor = v.scene.actors[("fod", 0)]
    poly = actor.GetMapper().GetInput()
    assert poly.GetNumberOfPoints() == 258
    assert poly.GetPointData().GetScalars().GetNumberOfTuples() == 258
    for _o, s in v.views.items():
        g = s.geometry
        pos = list(v.position)
        pos[g.horizontal] = 3
        pos[g.vertical] = 4
        x, y = g.display_position(pos)
        r = s.canvas.image_rect()
        QTest.mouseClick(
            s.canvas,
            Qt.MouseButton.LeftButton,
            pos=QPointF(r.left() + x * r.width(), r.top() + y * r.height()).toPoint(),
        )
        wait(qtbot, v)
        assert v.position[g.horizontal] == 3 and v.position[g.vertical] == 4
        assert str(v.position) in v.fod_selected.text()
    v.fod_mode.setCurrentText("Slice region")
    v.fod_limit.setValue(24)
    v.fod_spacing.setValue(2)
    wait(qtbot, v)
    assert 1 < v.fod_metrics["glyph_count"] <= 24
    v.fod_color.setCurrentText("Neutral")
    v.fod_scaling.setCurrentText("Shared region")
    wait(qtbot, v)
    rgb = v.scene.actors[("fod", 0)].GetMapper().GetInput().GetPointData().GetScalars()
    assert rgb.GetTuple3(0)[0] == rgb.GetTuple3(0)[1] == rgb.GetTuple3(0)[2]
    for theme in ["light", "dark"]:
        window.theme.apply(theme)
        assert v.fod is not None
    v.fod_show.setChecked(False)
    wait(qtbot, v)
    assert ("fod", 0) not in v.scene.actors
    assert all(s.canvas.fod_image is None for s in v.views.values())
    assert not window.toast.isVisible()


def test_failure_retry_cancel_and_newest_input(window, qtbot, tmp_path):
    v = viewer(window)
    path = fod_file(tmp_path)
    v.load_fod(tmp_path / "missing.mif")
    wait(qtbot, v)
    assert v.fod_state.state == State.FAILED
    v.load_fod(path)
    wait(qtbot, v)
    assert v.fod_state.state in {State.READY, State.WARNING}
    assert any(h["state"] == "failed" for h in v.fod_state.history)
    v.load_fod(path)
    v.cancel_fod()
    wait(qtbot, v)
    assert v.fod_state.state == State.CANCELLED and ("fod", 0) not in v.scene.actors
    assert "Opening" not in v.fod_summary.text()
    v.load_fod(tmp_path / "old-missing.mif")
    v.load_fod(path)
    wait(qtbot, v)
    assert v.fod.path == path and v.fod_state.state != State.FAILED
    assert not v.fod_state.busy and not v.image_loading
    v.navigate((4, 4, 4))
    v.navigate((5, 6, 7))
    wait(qtbot, v)
    assert "(5, 6, 7)" in v.fod_selected.text()
    v.load_fod(fod_file(tmp_path, "nan", True))
    wait(qtbot, v)
    assert v.fod_state.state == State.FAILED and ("fod", 0) not in v.scene.actors


def test_background_alignment_and_scalar_switch(window, qtbot, tmp_path):
    v = viewer(window)
    fod = fod_file(tmp_path)
    image = nib.load(fod)
    scalar = nib.Nifti1Image(np.ones(image.shape[:3], dtype="f4"), image.affine, image.header)
    scalar_path = tmp_path / "scalar.nii"
    nib.save(scalar, scalar_path)
    v.load_image(scalar_path)
    wait(qtbot, v)
    v.load_fod(fod)
    wait(qtbot, v)
    assert v.fod is not None
    v.load_image(scalar_path)
    wait(qtbot, v)
    assert v.fod is not None and ("fod", 0) in v.scene.actors
    other = fod_file(tmp_path, "other", offset=100)
    v.load_image(other)
    wait(qtbot, v)
    assert v.fod is None and ("fod", 0) not in v.scene.actors
    v.load_fod(fod)
    wait(qtbot, v)
    assert v.fod_state.state == State.FAILED
    assert all(s.canvas.fod_image is None for s in v.views.values())


@pytest.mark.parametrize("show", [True, False])
def test_workspace_roundtrip(window, qtbot, tmp_path, show):
    v = viewer(window)
    path = fod_file(tmp_path)
    v.load_fod(path)
    wait(qtbot, v)
    v.navigate((3, 4, 5))
    v.fod_color.setCurrentText("Neutral")
    v.fod_scale.setValue(2.5)
    v.fod_show.setChecked(show)
    wait(qtbot, v)
    workspace = tmp_path / "view.json"
    v.save_workspace(workspace)
    v.fod_color.setCurrentText("Direction RGB")
    v.navigate((1, 1, 1))
    wait(qtbot, v)
    v.open_workspace(workspace)
    wait(qtbot, v)
    assert v.position == (3, 4, 5)
    assert v.fod_color.currentText() == "Neutral"
    assert v.fod_scale.value() == 2.5
    assert v.fod_show.isChecked() == show
    assert (("fod", 0) in v.scene.actors) == show


def test_roi_bound_and_loading_supersession(window, qtbot, tmp_path):
    """Regional display obeys actual mask selection and never revives old loads."""
    v = viewer(window)
    path = fod_file(tmp_path)
    v.load_fod(path)
    wait(qtbot, v)
    roi = np.zeros(v.reference.shape[:3], dtype="u1")
    roi[4:9, 5:10, 6:11] = 1
    v.add_roi(roi, "Restricted")
    v.roi_list.setCurrentRow(0)
    v.fod_mode.setCurrentText("Selected ROI region")
    v.fod_spacing.setValue(1)
    v.fod_limit.setValue(12)
    wait(qtbot, v)
    assert 1 <= v.fod_metrics["glyph_count"] <= 12
    v.fod_mode.setCurrentText("Slice region")
    v.fod_show.setChecked(False)
    wait(qtbot, v)
    assert not v.fod_state.busy
    v.load_fod(tmp_path / "late-missing.mif")
    # Opening a newer scalar dataset cancels the FOD operation's presentation.
    image = nib.Nifti1Image(np.ones((8, 8, 8), dtype="f4"), np.eye(4))
    image.header.set_xyzt_units("mm")
    image.set_sform(image.affine, 1)
    nib.save(image, tmp_path / "new.nii")
    v.load_image(tmp_path / "new.nii")
    wait(qtbot, v)
    assert v.reference.path.name == "new.nii"
    assert not v.fod_state.busy and v.image_state.state == State.READY
    assert ("fod", 0) not in v.scene.actors


def test_scalar_background_workspace_and_camera(window, qtbot, tmp_path):
    v = viewer(window)
    path = fod_file(tmp_path)
    image = nib.load(path)
    scalar = tmp_path / "background.nii"
    nib.save(
        nib.Nifti1Image(np.ones(image.shape[:3], dtype="f4"), image.affine, image.header), scalar
    )
    v.load_image(scalar)
    wait(qtbot, v)
    v.load_fod(path)
    wait(qtbot, v)
    v.fod_mode.setCurrentText("Slice region")
    v.navigate((4, 5, 6))
    wait(qtbot, v)
    v.layout_choice.setCurrentText("Axial")
    camera = v.scene.renderer.GetActiveCamera()
    camera.SetPosition(20, 30, 40)
    v.save_workspace(tmp_path / "scalar-view.json")
    v.open_workspace(tmp_path / "scalar-view.json")
    wait(qtbot, v)
    assert v.layout_choice.currentText() == "Axial"
    assert v.position == (4, 5, 6)
    assert v.reference.path == scalar
    np.testing.assert_allclose(camera.GetPosition(), [20, 30, 40])
    assert v.fod is not None and ("fod", 0) in v.scene.actors
