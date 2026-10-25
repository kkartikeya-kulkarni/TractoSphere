"""Actual Qt/VTK smoke on small generated geometry, not biological validation.

Run on a desktop or Xvfb: python scripts/validate_platform_smoke.py --output NEW
Real public MRI/tract testing is a separate validation_public_alpha_gui run.
No scientific executables, downloads, default settings or expensive jobs touched.
"""

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

import nibabel as nib
import numpy as np
import vtk
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from vtk.util.numpy_support import vtk_to_numpy

from dmri_studio.gui.capture import capture_widget
from dmri_studio.gui.main_window import MainWindow
from dmri_studio.gui.settings import Settings
from dmri_studio.gui.theme import Theme
from dmri_studio.gui.welcome import WelcomeWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    ijk = np.indices((16, 18, 20))
    radius = sum(((ijk[i] - center) / 6) ** 2 for i, center in enumerate((8, 9, 10)))
    image = np.stack([np.where(radius < 1, 100 + ijk[0] + t * 10, 0) for t in range(4)], -1)
    affine = np.diag([-2.0, 2.5, 3.0, 1.0])
    affine[:3, 3] = [30, -20, -30]
    source = out / "MRI β with spaces.nii.gz"
    nii = nib.Nifti1Image(image.astype(np.float32), affine)
    nii.header.set_xyzt_units("mm")
    nib.save(nii, source)
    labels = np.zeros(image.shape[:3], np.uint8)
    labels[(radius < 0.6) & (ijk[0] < 8)] = 1
    labels[(radius < 0.6) & (ijk[0] >= 8)] = 2
    atlas = out / "regions β.nii.gz"
    nib.save(nib.Nifti1Image(labels, affine, nii.header), atlas)
    atlas.with_suffix("").with_suffix(".tsv").write_text(
        "index\tname\tcolor\n1\tFixture left\t#5599ee\n2\tFixture right\t#dd9955\n",
        encoding="utf-8",
    )
    voxels = np.array([[4, 9, 10], [8, 9, 10], [12, 9, 10]], np.float32)
    lines = [nib.affines.apply_affine(affine, voxels + [0, offset, 0]) for offset in (0, 1)]
    sources = [source, atlas]
    for extension in ("tck", "trk"):
        p = out / ("tracts β." + extension)
        nib.streamlines.save(nib.streamlines.Tractogram(lines, affine_to_rasmm=np.eye(4)), p)
        sources.append(p)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    app = QApplication([])
    theme = Theme(app)
    settings = Settings(QSettings(str(out / "settings.ini"), QSettings.IniFormat))
    settings.set("reduced_motion", True)
    window = MainWindow(theme, settings)
    window.focus_scientific_viewer()
    viewer = window.scientific_viewer
    failures = []

    def record_error(typ, value, tb):
        failures.append(str(value))
        sys.__excepthook__(typ, value, tb)

    sys.excepthook = record_error

    def wait():
        deadline = time.monotonic() + 60
        while viewer.tasks and time.monotonic() < deadline:
            QTest.qWait(20)
        QTest.qWait(150)
        assert not viewer.tasks and not failures, failures
        assert not viewer.status.text().startswith(
            ("Read failed", "Cannot complete", "Operation failed")
        ), viewer.status.text()

    def capture(name):
        QTest.qWait(150)
        assert capture_widget(window).save(str(out / (name + ".png")))
        if sys.platform.startswith("linux"):
            assert (
                app.primaryScreen()
                .grabWindow(int(window.winId()))
                .save(str(out / (name + "-screen.png")))
            )

    window.resize(1280, 850)
    window.show()
    try:
        welcome = WelcomeWindow(theme, settings)
        welcome.show()
        QTest.qWait(100)
        assert welcome.grab().save(str(out / "welcome.png"))
        welcome.close()
        assert not hasattr(window, "assistant")
        window.focus_scientific_viewer()
        viewer.load_image(source)
        wait()
        assert viewer.volume.maximum() == 3
        viewer.volume.setValue(3)
        viewer.navigate([8, 9, 10])
        assert np.array_equal(viewer.position, [8, 9, 10])
        for p in sources[2:]:
            viewer.load_tracts(p)
            wait()
        assert len(viewer.groups) == 2
        for group in viewer.groups:
            assert np.allclose(group.line(0), lines[0])
        viewer.load_atlas(atlas, same_space=True)
        wait()
        assert len(viewer.rois) == 2
        build_surface = next(
            button
            for button in viewer.findChildren(QPushButton)
            if button.text() == "Build / update ROI in 3D"
        )
        for index in range(2):
            viewer.roi_list.setCurrentRow(index)
            build_surface.click()
        viewer.roi_list.entries[1].setSelected(True)
        wait()
        assert all(("roi", i) in viewer.scene.actors for i in range(2)), (
            viewer.status.text(),
            list(viewer.scene.actors),
            [roi.get("surface_built") for roi in viewer.rois],
            viewer.roi_surface_queue,
            viewer.roi_surface_busy,
        )
        viewer.roi_list.setCurrentRow(1)
        viewer.roi_opacity.setValue(0.25)
        assert viewer.scene.actors[("roi", 1)].GetProperty().GetOpacity() == 0.25
        viewer.scene.camera("Left")
        viewer.scene.draw()
        capture("four-view-dark")
        grab = vtk.vtkWindowToImageFilter()
        grab.SetInput(viewer.scene.GetRenderWindow())
        grab.ReadFrontBufferOff()
        grab.Update()
        pixels = vtk_to_numpy(grab.GetOutput().GetPointData().GetScalars())
        assert pixels.std() > 2, "Renderer must produce actual nonuniform pixels"
        viewer.scene.screenshot(str(out / "rendered-scene.png"))
        viewer.morph_roi("Dilate")
        viewer.undo_roi(False)
        workspace = out / "workspace β.json"
        viewer.save_workspace(workspace)
        viewer.open_workspace(workspace)
        wait()
        assert len(viewer.rois) == 2 and len(viewer.groups) == 2
        assert viewer.volume.value() == 3 and np.array_equal(viewer.position, [8, 9, 10])
        theme.apply("light")
        window.resize(1000, 720)
        capture("four-view-light-small")
        window.focus_preferences()
        capture("dependencies-light")
        window.preferences.dependencies.open_guide("MRtrix3")
        guide = window.preferences.dependencies.guides["MRtrix3"]
        QTest.qWait(100)
        assert capture_widget(guide).save(str(out / "guided-setup.png"))
        guide.reject()
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == before[p.name] for p in sources)
        (out / "validation.json").write_text(
            json.dumps(
                dict(
                    system=platform.system(),
                    architecture=platform.machine(),
                    python=platform.python_version(),
                    qt=__import__("PySide6").__version__,
                    vtk=vtk.vtkVersion.GetVTKVersion(),
                    render_window=viewer.scene.GetRenderWindow().GetClassName(),
                    renderer_capabilities=viewer.scene.GetRenderWindow().ReportCapabilities(),
                    source_sha256=before,
                    all_sources_unchanged=True,
                    vtk_pixel_std=float(pixels.std()),
                    checks=[
                        "welcome",
                        "no AI",
                        "4D navigation",
                        "linked coordinates",
                        "TCK/TRK coordinates",
                        "named atlas regions",
                        "multiple real mask surfaces",
                        "ROI opacity/edit/undo",
                        "workspace roundtrip",
                        "dark/light and small window",
                        "dependency panel",
                        "3D pixels",
                    ],
                    evidence_scope="Generated geometry integration smoke; not biological or physical-GPU validation",
                ),
                indent=2,
            ),
            encoding="utf-8",
        )
        print("Platform GUI smoke passed")
    finally:
        wait()
        window.close()
        QTest.qWait(100)


if __name__ == "__main__":
    main()
