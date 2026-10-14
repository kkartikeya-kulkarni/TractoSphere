"""Native Qt/VTK validation using the immutable public TractoInferno reference.

Run with a NEW output directory. No inference, processing or source edits.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import nibabel as nib
import numpy as np
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from dmri_studio.core.resources import peak_self_rss_bytes
from dmri_studio.gui.main_window import MainWindow
from dmri_studio.gui.settings import Settings
from dmri_studio.gui.theme import Theme

PROJECT = Path(__file__).resolve().parents[2]
BASE = Path(
    os.environ.get(
        "TRACTOSPHERE_REFERENCE_DIR",
        str(PROJECT / "data/viewer-reference/tractoinferno-sub-1006/derivatives/testset/sub-1006"),
    )
)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=False)
    app = QApplication([])
    theme = Theme(app)
    settings = Settings(QSettings(str(output / "settings.ini"), QSettings.IniFormat))
    window = MainWindow(theme, settings)
    window.show()
    window.resize(1440, 960)
    window.focus_scientific_viewer()
    v = window.scientific_viewer
    pulses = []
    heartbeat = QTimer()
    heartbeat.setInterval(20)
    heartbeat.timeout.connect(lambda: pulses.append(time.monotonic()))
    heartbeat.start()
    evidence = {}
    started = time.monotonic()
    paths = [
        BASE / "anat/sub-1006__T1w.nii.gz",
        BASE / "dwi/sub-1006__dwi.nii.gz",
        BASE / "tractography/sub-1006__AF_L.tck",
        BASE / "tractography/sub-1006__CC_Pr_Po.tck",
        BASE / "tractography/sub-1006__PYT_L.trk",
        BASE / "mask/sub-1006__mask_wm.nii.gz",
    ]
    before = {str(p): sha(p) for p in paths}

    def wait():
        end = time.monotonic() + 120
        while v.tasks and time.monotonic() < end:
            QTest.qWait(30)
            time.sleep(0.005)  # Yield Python GIL to export/index workers under QtTest.
        assert not v.tasks, "Background read timeout"
        QTest.qWait(80)
        assert not v.status.text().startswith(("Cannot complete", "Read failed")), v.status.text()

    def capture(name):
        QTest.qWait(80)
        assert window.grab().save(str(output / (name + ".png")))

    def frame():
        return v.scene.frame.copy()

    try:
        assert not hasattr(window, "assistant")
        v.load_image(paths[0])
        wait()
        assert v.data.shape == (142, 174, 120)
        v.load_tracts(paths[2])
        v.load_tracts(paths[3])
        v.load_tracts(paths[4])
        wait()
        assert len(v.groups) == 3
        evidence["indexed_source_counts"] = {
            g.path.name: {"source": g.total, "indexed": len(g.streamlines)} for g in v.groups
        }
        assert all(g.total == len(g.streamlines) for g in v.groups)
        assert max(g.total for g in v.groups) == 239937
        v.refresh_tracts()
        evidence["three_group_line_rebuild_seconds"] = v.last_render_seconds
        QTest.qWait(30)
        v.scene.camera("Left")
        for check in v.plane_checks:
            check.setChecked(False)
        capture("01-four-view-real-tractography")
        v.tabs.setCurrentIndex(1)
        v.expand("3d")
        v.scene.camera("Isometric")
        capture("02-direction-rgb")
        original = frame()
        v.mode.setCurrentText("Tubes")
        evidence["three_group_tube_rebuild_seconds"] = v.last_render_seconds
        QTest.qWait(30)
        v.width.setValue(0.7)
        QTest.qWait(50)
        assert frame() != original
        capture("03-tubes")
        original = frame()
        v.opacity.setValue(0.35)
        evidence["three_group_opacity_update_seconds"] = v.last_render_seconds
        QTest.qWait(50)
        assert frame() != original
        v.opacity.setValue(1)
        v.mode.setCurrentText("Lines")
        v.width.setValue(1)
        v.scene.camera("Left")
        cam = v.scene.renderer.GetActiveCamera()
        position = cam.GetPosition()
        interactor = v.scene.GetRenderWindow().GetInteractor()
        interactor.SetEventInformation(150, 150, 0, 0)
        interactor.LeftButtonPressEvent()
        interactor.SetEventInformation(200, 180, 0, 0)
        interactor.MouseMoveEvent()
        interactor.LeftButtonReleaseEvent()
        assert cam.GetPosition() != position
        position = cam.GetPosition()
        interactor.MouseWheelForwardEvent()
        assert cam.GetPosition() != position
        focal = cam.GetFocalPoint()
        interactor.SetEventInformation(150, 150, 0, 0)
        interactor.MiddleButtonPressEvent()
        interactor.SetEventInformation(170, 165, 0, 0)
        interactor.MouseMoveEvent()
        interactor.MiddleButtonReleaseEvent()
        assert cam.GetFocalPoint() != focal
        # Pick a real streamline via its projected world point, not by calling
        # the editing callback directly. Display sampling keeps the test bounded.
        v.density.setValue(100)
        v.scene.camera("Left")
        point = v.groups[0].line(0)[len(v.groups[0].line(0)) // 2]
        renderer = v.scene.renderer
        renderer.SetWorldPoint(*point, 1)
        renderer.WorldToDisplay()
        x, y, _ = renderer.GetDisplayPoint()
        interactor.SetEventInformation(int(x), int(y), 1, 0)
        interactor.LeftButtonPressEvent()
        interactor.LeftButtonReleaseEvent()
        assert any(g.selected for g in v.groups), "Ctrl-picking did not select a real tract"
        for g in v.groups:
            g.selected.clear()
        interactor.SetEventInformation(int(x), int(y), 0, 0)
        v.density.setValue(10000)
        evidence["native_ctrl_tract_picking"] = True
        evidence["camera_mouse_rotate_zoom_pan"] = True
        for name in ["Anterior", "Posterior", "Left", "Right", "Superior", "Inferior", "Isometric"]:
            v.scene.camera(name)
            assert np.isfinite(cam.GetPosition()).all()
        v.parallel.setChecked(True)
        assert cam.GetParallelProjection() == 1
        v.parallel.setChecked(False)
        v.load_surface(paths[5], 0.5)
        wait()
        assert ("surface", 0) in v.scene.actors
        v.surface_opacity.setValue(0.25)
        v.scene.camera("Left")
        capture("04-white-matter-mask-surface-and-tracts")
        v.load_roi(paths[5])
        assert v.rois[0]["data"].any()
        v.roi_opacity.setValue(0.2)
        v.roi_surface()
        v.expand("3d")
        v.tabs.setCurrentIndex(2)
        p = list(v.position)
        p[2] += 5
        v.navigate(p)
        assert all(view.canvas.position == tuple(p) for view in v.views.values())
        assert v.scene.planes[2].GetDisplayExtent()[4] == p[2]
        capture("05-roi-overlay")
        v.new_roi()
        v.brush.setCurrentText("Draw")
        v.navigate(v.position, "axial")
        n = int(v.rois[-1]["data"].sum())
        assert n > 0
        v.undo_roi(False)
        assert v.rois[-1]["data"].sum() == 0
        v.undo_roi(True)
        assert v.rois[-1]["data"].sum() == n
        v.morph_roi("Dilate")
        assert v.rois[-1]["data"].sum() > n
        v.morph_roi("Erode")
        v.brush.setCurrentText("Navigate")
        v.tract_list.setCurrentRow(0)
        group = v.groups[0]
        group.selected = {0, 1}
        v.edit_tracts("hide")
        assert group.hidden == {0, 1}
        v.tract_undo(False)
        assert not group.hidden
        v.edit_tracts("delete")
        assert group.deleted == {0, 1}
        v.tract_undo(False)
        assert not group.deleted
        group.selected = {0}
        group.edit("cut", group.line(0).mean(axis=0), np.array([0, 0, 1]))
        group.undo()
        v.refresh_tracts()
        export = output / "edited-tracts.tck"
        group.export(export, v.reference)
        restored = nib.streamlines.load(export)
        assert len(restored.tractogram.streamlines) == len(group.streamlines)
        group.selected = {0}
        v.edit_tracts("hide")
        camera_before = list(cam.GetPosition())
        voxel_before = v.position
        workspace = output / "workspace.json"
        v.save_workspace(workspace)
        v.open_workspace(workspace)
        wait()
        assert len(v.groups) == 3 and len(v.rois) == 2 and len(v.surfaces) == 1
        assert v.groups[0].hidden == {0}, v.groups[0].hidden
        assert v.position == voxel_before
        assert np.allclose(cam.GetPosition(), camera_before)
        v.scene.screenshot(output / "06-render-export.png")
        assert (output / "06-render-export.png").stat().st_size > 10000
        v.tabs.setCurrentIndex(0)
        window.resize(1024, 768)
        capture("07-laptop-controls")
        assert window.width() <= 1024
        theme.apply("light")
        capture("08-light-theme")
        theme.apply("dark")
        window.resize(1440, 960)
        v.load_image(paths[1])
        wait()
        assert v.reference.count == 65
        first = v.data.copy()
        v.volume.setValue(50)
        wait()
        assert not np.array_equal(first, v.data)
        assert v.volume.value() == 50
        v.auto_contrast()
        capture("09-real-diffusion-volume-51")
        evidence["volume_information"] = v.volume_info.text()
        evidence["checks"] = [
            "AI absent",
            "real T1 / 65-volume DWI",
            "3 real tract groups",
            "RGB, line, tube, opacity",
            "mouse rotate/pan/zoom",
            "7 anatomical cameras",
            "projection",
            "native slice synchronization",
            "mask-derived surface",
            "ROI overlay/draw/undo/redo/dilate/erode",
            "selected hide/delete/undo",
            "tract export",
            "workspace derivatives / references / camera / hidden selection",
            "VTK screenshot",
            "1024x768 and light/dark themes",
        ]
        evidence["source_sha256_unchanged"] = {str(p): sha(p) == before[str(p)] for p in paths}
        assert all(evidence["source_sha256_unchanged"].values())
        evidence["max_event_loop_gap_seconds_including_screenshot_and_exports"] = (
            max(np.diff(pulses)) if len(pulses) > 1 else None
        )
        evidence["elapsed_seconds"] = time.monotonic() - started
        evidence["peak_rss_bytes"] = peak_self_rss_bytes()
        evidence["render_window"] = v.scene.GetRenderWindow().GetClassName()
        evidence["vtk_version"] = __import__(
            "vtkmodules.vtkCommonCore", fromlist=["vtkVersion"]
        ).vtkVersion.GetVTKVersion()
        (output / "validation.json").write_text(json.dumps(evidence, indent=2))
        print(json.dumps(evidence, indent=2), flush=True)
    finally:
        wait()
        window.close()
        QTest.qWait(100)


if __name__ == "__main__":
    main()
