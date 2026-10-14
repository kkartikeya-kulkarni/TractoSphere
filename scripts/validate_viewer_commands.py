"""Click-level native regression on real MRI; no scientific processing or source edits.

Run: .venv/bin/python scripts/validate_viewer_commands.py NEW_OUTPUT_DIRECTORY
File dialogs receive deterministic destinations; buttons, mouse gestures and
signals run through Qt normally. Tracebacks are captured as test failures.
"""

import hashlib
import json
import os
import sys
import time
import traceback
from pathlib import Path
from unittest.mock import patch

import nibabel as nib
import numpy as np
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QFileDialog,
    QInputDialog,
    QPushButton,
    QScrollArea,
)

from dmri_studio.gui.main_window import MainWindow
from dmri_studio.gui.settings import Settings
from dmri_studio.gui.theme import Theme

BASE = Path(
    os.environ.get(
        "TRACTOSPHERE_REFERENCE_DIR",
        str(
            Path(__file__).resolve().parents[2]
            / "data/viewer-reference/tractoinferno-sub-1006/derivatives/testset/sub-1006"
        ),
    )
)


def main():
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=False)
    app = QApplication([])
    theme = Theme(app)
    window = MainWindow(theme, Settings(QSettings(str(out / "settings.ini"), QSettings.IniFormat)))
    window.show()
    window.resize(1440, 960)
    window.activateWindow()
    exceptions = []

    def record_exception(*args):
        exceptions.append(str(args[1]))
        traceback.print_exception(*args)

    sys.excepthook = record_exception
    nav = next(b for b, n in window.nav_buttons if n == "Viewer")
    assert nav.isEnabled(), "Image viewer must work without loading a preprocessing dataset"
    QTest.mouseClick(nav, Qt.MouseButton.LeftButton)
    QTest.qWait(50)
    v = window.scientific_viewer
    assert v is not None
    evidence = {"clicked": [], "checks": []}
    started = time.monotonic()

    def wait():
        deadline = time.monotonic() + 180
        while v.tasks and time.monotonic() < deadline:
            QTest.qWait(5)
            time.sleep(0.01)  # Yield Python GIL to export/index workers under QtTest.
        assert not v.tasks, "Worker timeout"
        QTest.qWait(250)
        assert not exceptions, exceptions

    def click(text):
        controls = [b for b in v.findChildren(QPushButton) if b.text() == text]
        assert len(controls) == 1, (text, len(controls))
        control = controls[0]
        for i in range(v.tabs.count()):
            page = v.tabs.widget(i)
            if page.isAncestorOf(control):
                v.tabs.setCurrentIndex(i)
                if isinstance(page, QScrollArea):
                    page.ensureWidgetVisible(control)
        QTest.qWait(20)
        assert control.isVisible() and control.isEnabled(), text
        QTest.mouseClick(control, Qt.MouseButton.LeftButton)
        wait()
        assert not v.status.text().startswith(
            ("Cannot complete", "Operation failed", "Read failed")
        ), (text, v.status.text())
        evidence["clicked"].append(text)
        print("Clicked:", text, flush=True)

    def history(tab, text):
        v.tabs.setCurrentIndex(tab)
        control = next(b for b in v.tabs.widget(tab).findChildren(QPushButton) if b.text() == text)
        v.tabs.widget(tab).ensureWidgetVisible(control)
        QTest.mouseClick(control, Qt.MouseButton.LeftButton)
        wait()
        evidence["clicked"].append(v.tabs.tabText(tab) + " " + text)

    def capture(name):
        QTest.qWait(60)
        assert window.grab().save(str(out / (name + ".png")))

    sources = [
        BASE / "anat/sub-1006__T1w.nii.gz",
        BASE / "tractography/sub-1006__AF_L.tck",
        BASE / "mask/sub-1006__mask_wm.nii.gz",
        BASE / "dti/sub-1006__fa.nii.gz",
    ]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    try:
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(sources[0]), "")):
            click("Open MRI")
        with patch.object(QFileDialog, "getOpenFileNames", return_value=([str(sources[1])], "")):
            click("Add tracts")
        assert v.groups and len(v.groups[0].streamlines) == 18375
        v.density.setValue(500)
        for check in v.plane_checks:
            check.setChecked(False)
        v.scene.camera("Left")
        # Test actual Qt mouse events, rather than calling VTK event methods.
        cam = v.scene.renderer.GetActiveCamera()
        position = cam.GetPosition()
        center = v.scene.rect().center()
        QTest.mousePress(v.scene, Qt.MouseButton.LeftButton, pos=center)
        QTest.mouseMove(v.scene, center + QPoint(45, 25), delay=30)
        QTest.mouseRelease(v.scene, Qt.MouseButton.LeftButton, pos=center + QPoint(45, 25))
        assert cam.GetPosition() != position, "Qt mouse rotation did not reach VTK"
        v.scene.camera("Left")
        v.selection_mode.setCurrentText("Select rectangle")
        QTest.mousePress(v.scene, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
        QTest.mouseMove(v.scene, QPoint(v.scene.width() - 2, v.scene.height() - 2), delay=20)
        QTest.mouseRelease(
            v.scene,
            Qt.MouseButton.LeftButton,
            pos=QPoint(v.scene.width() - 2, v.scene.height() - 2),
        )
        wait()
        assert len(v.groups[0].selected) == v.groups[0].total
        evidence["checks"].append("Native Qt camera rotation and whole-view rectangle selection")
        v.selection_mode.setCurrentText("Camera")
        click("Hide selected")
        assert len(v.groups[0].hidden) == 18375
        click("Show hidden")
        assert not v.groups[0].hidden
        click("Clear selection")
        assert not v.groups[0].selected
        click("Select all loaded in group")
        click("Delete selected")
        assert len(v.groups[0].deleted) == 18375
        # Resolve duplicate Undo by directly targeting its containing tab's button.
        v.tabs.setCurrentIndex(1)
        undo = next(b for b in v.tabs.widget(1).findChildren(QPushButton) if b.text() == "Undo")
        v.tabs.widget(1).ensureWidgetVisible(undo)
        QTest.mouseClick(undo, Qt.MouseButton.LeftButton)
        wait()
        assert not v.groups[0].deleted
        history(1, "Redo")
        assert len(v.groups[0].deleted) == 18375
        history(1, "Undo")
        click("Clear selection")
        with patch.object(QColorDialog, "getColor", return_value=QColor("#44bb88")):
            click("Uniform group color")
        assert np.allclose(v.groups[0].color, QColor("#44bb88").getRgbF()[:3])
        with patch.object(QInputDialog, "getText", return_value=("Arcuate left", True)):
            click("Rename group")
        with patch.object(
            QFileDialog, "getSaveFileName", return_value=(str(out / "bundle-stats"), "")
        ):
            click("Export bundle statistics")
        with patch.object(
            QFileDialog, "getSaveFileName", return_value=(str(out / "fibers"), "MRtrix (*.tck)")
        ):
            click("Export retained loaded tracts")
        assert (out / "fibers.tck").exists()
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(sources[3]), "")):
            click("Load same-grid scalar color map")
        assert v.scalar_map is not None
        click("Use base-image scalar colors")
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(sources[2]), "")):
            click("Load ROI NIfTI")
        click("Build / update ROI in 3D")
        for check in v.plane_checks:
            check.setChecked(True)
        v.scene.camera("Left")
        # Real Qt modifier picks with anatomical planes and a surface present.
        renderer = v.scene.renderer
        for modifier in (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.MetaModifier):
            v.groups[0].selected.clear()
            v.refresh_tracts()
            for index in v.groups[0].display_ids(30):
                points = v.groups[0].line(index)
                renderer.SetWorldPoint(*points[len(points) // 2], 1)
                renderer.WorldToDisplay()
                x, y, _ = renderer.GetDisplayPoint()
                scale = v.scene.devicePixelRatioF()
                pixel = QPoint(round(x / scale), round(v.scene.height() - y / scale - 1))
                QTest.mouseClick(v.scene, Qt.MouseButton.LeftButton, modifier, pixel)
                if v.groups[0].selected:
                    break
            assert v.groups[0].selected, "Actual Qt modifier click did not select a tract"
        v.highlight_selected.setChecked(False)
        v.highlight_selected.setChecked(True)
        click("Clear selection")
        click("Select tracts intersecting selected ROI")
        assert v.groups[0].selected
        evidence["checks"].append(
            "Cmd/Ctrl picking through surfaces and ROI intersection selection"
        )
        click("Clear selection")
        for mode in ("Tubes", "Lines"):
            v.mode.setCurrentText(mode)
        for color in ("Length", "Image scalar", "Direction RGB", "Uniform"):
            v.coloring.setCurrentText(color)
        v.width.setValue(1.5)
        v.opacity.setValue(0.65)
        v.tract_visible.setChecked(False)
        assert not v.groups[0].visible
        v.tract_visible.setChecked(True)
        v.roi_visible.setChecked(False)
        assert not v.scene.actors[("roi", 0)].GetVisibility()
        v.roi_visible.setChecked(True)
        v.roi_opacity.setValue(0.25)
        assert v.scene.actors[("roi", 0)].GetProperty().GetOpacity() == 0.25
        with patch.object(QColorDialog, "getColor", return_value=QColor("#44aaff")):
            click("ROI color")
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(out / "roi"), "")):
            click("Save selected ROI as new NIfTI")
        assert (out / "roi.nii.gz").exists()
        with patch.object(
            QFileDialog, "getSaveFileName", return_value=(str(out / "roi-stats"), "")
        ):
            click("Export ROI statistics")
        click("New empty ROI in image grid")
        v.brush.setCurrentText("Draw 3D")
        canvas = v.views["axial"].canvas
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=canvas.rect().center())
        wait()
        assert v.rois[-1]["data"].sum() > 1
        z = v.position[v.views["axial"].geometry.axis]
        assert np.take(v.rois[-1]["data"], max(0, z - 1), axis=v.views["axial"].geometry.axis).any()
        click("Build / update ROI in 3D")
        before = v.scene.actors[("roi", 1)].GetMapper().GetInput().GetNumberOfPoints()
        v.roi_visible.setChecked(False)
        click("Dilate")
        assert v.rois[-1]["visible"] and v.roi_visible.isChecked(), "Edited ROI remained hidden"
        after = v.scene.actors[("roi", 1)].GetMapper().GetInput().GetNumberOfPoints()
        assert before != after, "3D ROI remained stale"
        click("Erode")
        saved = v.rois[-1]["data"].copy()
        v.brush.setCurrentText("Erase 3D")
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=canvas.rect().center())
        wait()
        assert not np.array_equal(saved, v.rois[-1]["data"])
        history(2, "Undo")
        assert np.array_equal(saved, v.rois[-1]["data"])
        history(2, "Redo")
        assert not np.array_equal(saved, v.rois[-1]["data"])
        history(2, "Undo")
        for brush_mode in ("Draw", "Erase"):
            v.brush.setCurrentText(brush_mode)
            QTest.mouseClick(
                canvas, Qt.MouseButton.LeftButton, pos=canvas.rect().center() + QPoint(40, 0)
            )
            wait()
        v.brush.setCurrentText("Polygon")
        v.cancel_polygon()
        for delta in [QPoint(-25, -25), QPoint(25, -25), QPoint(25, 25), QPoint(-25, 25)]:
            QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=canvas.rect().center() + delta)
        click("Finish polygon")
        assert v.rois[-1]["data"].sum() > 30
        v.brush.setCurrentText("Navigate")
        # Axial and dragged cuts affect only explicitly selected fibers.
        group = v.groups[0]
        group.selected = {0}
        midpoint = group.line(0)[len(group.line(0)) // 2]
        v.navigate(np.rint(v.reference.voxels(midpoint)).astype(int))
        click("Cut selected at axial plane")
        assert set(group.cuts) == {0}
        history(1, "Undo")
        assert not group.cuts
        v.scene.camera("Left")
        v.selection_mode.setCurrentText("Select drag plane")
        center = v.scene.rect().center()
        QTest.mousePress(v.scene, Qt.MouseButton.LeftButton, pos=QPoint(center.x(), 1))
        QTest.mouseMove(v.scene, QPoint(center.x(), v.scene.height() - 2), delay=30)
        QTest.mouseRelease(
            v.scene, Qt.MouseButton.LeftButton, pos=QPoint(center.x(), v.scene.height() - 2)
        )
        wait()
        assert group.selected and v.last_selection_plane is not None
        v.cut_mode.setCurrentText("Keep longer part")
        click("Cut selected at drag plane")
        assert group.cuts
        history(1, "Undo")
        click("Clear selection")
        v.selection_mode.setCurrentText("Camera")
        click("Merge visible into new ROI")
        for operation in [
            "Copy",
            "Jump to centroid",
            "Fill holes",
            "Largest component",
            "Smooth",
            "Shift +X",
            "Shift -X",
            "Shift +Y",
            "Shift -Y",
            "Shift +Z",
            "Shift -Z",
            "Flip X",
            "Flip X",
            "Flip Y",
            "Flip Y",
            "Flip Z",
            "Flip Z",
            "Invert",
            "Invert",
        ]:
            v.roi_operation.setCurrentText(operation)
            click("Apply ROI operation")
        with patch.object(QInputDialog, "getText", return_value=("Review ROI", True)):
            v.roi_operation.setCurrentText("Rename")
            click("Apply ROI operation")
        with patch.object(QInputDialog, "getDouble", side_effect=[(100.0, True), (200.0, True)]):
            v.roi_operation.setCurrentText("Threshold image")
            click("Apply ROI operation")
        assert np.array_equal(v.rois[-1]["data"], (v.data >= 100) & (v.data <= 200))
        v.roi_operation.setCurrentText("Remove")
        click("Apply ROI operation")
        # Real mask decimated only as a controlled same-space grid test fixture.
        source_roi = nib.load(sources[2])
        coarse = nib.Nifti1Image(
            np.asarray(source_roi.dataobj)[::2, ::2, ::2], source_roi.affine @ np.diag([2, 2, 2, 1])
        )
        coarse.header.set_xyzt_units("mm")
        nib.save(coarse, out / "coarse-roi.nii.gz")
        v.roi_same_space.setChecked(True)
        with patch.object(
            QFileDialog, "getOpenFileName", return_value=(str(out / "coarse-roi.nii.gz"), "")
        ):
            click("Load ROI NIfTI")
        assert v.rois[-1]["data"].shape == v.data.shape
        v.roi_same_space.setChecked(False)
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(sources[3]), "")):
            click("Add scalar overlay")
        assert len(v.layers) == 1
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(sources[0]), "")):
            click("Add scalar overlay")
        second = v.layers[1]
        click("Move layer up")
        assert v.layers[0] is second
        click("Move layer down")
        assert v.layers[1] is second
        v.layer_visible.setChecked(False)
        assert not second["visible"]
        click("Remove selected layer")
        for cmap in ["hot", "viridis", "gray", "blue-white-red"]:
            v.layer_cmap.setCurrentText(cmap)
        v.layer_alpha.setValue(0.3)
        capture("01-overlays-roi-editing")
        v.volume_render_check.setChecked(True)
        wait()
        assert ("volume", 0) in v.scene.actors
        v.tabs.setCurrentIndex(0)
        capture("02-anatomical-volume-rendering")
        v.volume_render_check.setChecked(False)
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(sources[2]), "")):
            click("Add surface from NIfTI")
        assert len(v.surfaces) == 1
        # Export the actual mask-derived surface only to validate mesh import.
        # This is explicitly not a cortical reconstruction.
        from nibabel.gifti import GiftiCoordSystem, GiftiDataArray, GiftiImage
        from vtk.util.numpy_support import vtk_to_numpy

        poly = v.scene.actors[("surface", 0)].GetMapper().GetInput()
        vertices = vtk_to_numpy(poly.GetPoints().GetData())
        faces = vtk_to_numpy(poly.GetPolys().GetConnectivityArray()).reshape(-1, 3)
        mesh = GiftiImage(
            darrays=[
                GiftiDataArray(
                    np.asarray(vertices, np.float32),
                    intent="NIFTI_INTENT_POINTSET",
                    coordsys=GiftiCoordSystem(dataspace=1, xformspace=1, xform=v.reference.affine),
                ),
                GiftiDataArray(np.asarray(faces, np.int32), intent="NIFTI_INTENT_TRIANGLE"),
            ]
        )
        nib.save(mesh, out / "mask-derived.surf.gii")
        click("Remove selected surface")
        assert not v.surfaces
        with patch.object(
            QFileDialog, "getOpenFileName", return_value=(str(out / "mask-derived.surf.gii"), "")
        ):
            click("Load GIFTI / FreeSurfer surface")
        assert len(v.surfaces) == 1
        click("Remove selected surface")
        with patch.object(
            QFileDialog, "getSaveFileName", return_value=(str(out / "saved-workspace"), "")
        ):
            click("Save")
        assert (out / "saved-workspace.json").exists()
        with patch.object(
            QFileDialog, "getOpenFileName", return_value=(str(out / "saved-workspace.json"), "")
        ):
            click("Restore")
        assert len(v.layers) == 1 and v.tract_list.item(0).text() == "Arcuate left"
        with patch.object(
            QFileDialog,
            "getSaveFileName",
            return_value=(str(out / "render"), "3D view PNG (*.png)"),
        ):
            click("Export PNG")
        assert (out / "render.png").stat().st_size > 1000
        v.tabs.setCurrentIndex(1)
        capture("03-tract-controls")
        theme.apply("light")
        capture("04-light-theme")
        theme.apply("dark")
        window.resize(1180, 780)
        v.tabs.setCurrentIndex(2)
        capture("05-laptop-roi-controls")
        click("Remove selected layer")
        assert not v.layers
        click("Remove group from scene")
        assert not v.groups
        # No-selection commands must show actionable errors rather than throwing.
        button = next(
            b for b in v.findChildren(QPushButton) if b.text() == "Cut selected at drag plane"
        )
        button.click()
        assert v.status.text().startswith("Cannot complete action:")
        assert not exceptions, exceptions
        evidence["source_sha256_unchanged"] = {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest() == hashes[str(p)] for p in sources
        }
        assert all(evidence["source_sha256_unchanged"].values())
        evidence["elapsed_seconds"] = time.monotonic() - started
        (out / "commands.json").write_text(json.dumps(evidence, indent=2))
        print(json.dumps(evidence, indent=2))
    finally:
        wait()
        window.close()
        QTest.qWait(50)


if __name__ == "__main__":
    main()
