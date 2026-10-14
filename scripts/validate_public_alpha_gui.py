"""Native alpha smoke test on permitted real MRI/TCK/TRK, no preprocessing runs.

Usage: python scripts/validate_public_alpha_gui.py --reference SUBJECT_DIR --output NEW_DIR
The supplied disjoint WM/GM/CSF masks form an explicitly derived label fixture.
Screenshots show actual native Qt/VTK, with no private paths in the status line.
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import nibabel as nib
import numpy as np
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from dmri_studio import __version__
from dmri_studio.gui.capture import capture_widget
from dmri_studio.gui.main_window import MainWindow
from dmri_studio.gui.settings import Settings
from dmri_studio.gui.theme import Theme


def hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(1024**2), b""):
            digest.update(part)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--timeout",
        type=float,
        default=120,
        help="Worker wait limit in seconds (slow VMs may need longer)",
    )
    args = parser.parse_args()
    if not 0 < args.timeout <= 600:
        parser.error("--timeout must be >0 and <=600 seconds")
    base, out = args.reference.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sources = [
        base / "anat/sub-1006__T1w.nii.gz",
        base / "dwi/sub-1006__dwi.nii.gz",
        base / "tractography/sub-1006__AF_L.tck",
        base / "tractography/sub-1006__PYT_L.trk",
    ]
    sources += [base / f"mask/sub-1006__mask_{t}.nii.gz" for t in ("wm", "gm", "csf")]
    before = {str(p): hash_file(p) for p in sources}
    app = QApplication([])
    theme = Theme(app)
    window = MainWindow(theme, Settings(QSettings(str(out / "settings.ini"), QSettings.IniFormat)))
    window.resize(1440, 960)
    window.show()
    window.activateWindow()
    window.focus_scientific_viewer()
    viewer = window.scientific_viewer
    failures = []

    def record_error(typ, value, tb):
        failures.append(str(value))
        sys.__excepthook__(typ, value, tb)

    sys.excepthook = record_error

    def wait():
        until = time.monotonic() + args.timeout
        while viewer.tasks and time.monotonic() < until:
            QTest.qWait(10)
            time.sleep(0.005)
        QTest.qWait(100)
        assert not viewer.tasks and not failures, (viewer.status.text(), failures)
        assert not viewer.status.text().startswith(
            ("Operation failed", "Cannot complete", "Read failed")
        ), viewer.status.text()

    def capture(name):
        QTest.qWait(100)
        assert capture_widget(window).save(str(out / f"{name}.png"))

    try:
        assert __version__ == "0.1.0a0"
        assert not hasattr(window, "assistant")
        assert "Public Alpha" in window.windowTitle()
        viewer.load_image(sources[1])
        wait()
        assert viewer.volume.maximum() == 64
        viewer.volume.setValue(34)
        wait()
        viewer.navigate([50, 60, 40])
        assert np.array_equal(viewer.position, [50, 60, 40])
        viewer.status.setText(
            "Real public 65-volume diffusion MRI · linked native-space navigation"
        )
        capture("diffusion-dark")
        viewer.load_image(sources[0])
        wait()
        viewer.load_tracts(sources[2])
        wait()
        viewer.load_tracts(sources[3])
        wait()
        assert len(viewer.groups) == 2 and all(g.total == len(g.streamlines) for g in viewer.groups)
        viewer.density.setValue(1500)
        viewer.highlight_selected.setChecked(False)
        viewer.slice_opacity.setValue(0.15)
        viewer.scene.camera("Left")
        viewer.status.setText(
            "TractoInferno CC0 · sub-1006 · real TCK and TRK in matching anatomical space"
        )
        capture("viewer-dark")
        images = [nib.load(p) for p in sources[4:]]
        masks = [np.asarray(i.dataobj) > 0 for i in images]
        assert not (np.sum(masks, axis=0) > 1).any()
        labels = np.zeros(masks[0].shape, np.uint8)
        for identifier, mask in enumerate(masks, 1):
            labels[mask] = identifier
        atlas = out / "sub-1006_tissue_dseg.nii.gz"
        nib.save(nib.Nifti1Image(labels, images[0].affine, images[0].header), atlas)
        atlas.with_name("sub-1006_tissue_dseg.tsv").write_text(
            "index\tname\tcolor\n1\tWhite matter (provided mask)\t#5599ee\n2\tGray matter (provided mask)\t#dd9955\n3\tCSF (provided mask)\t#55ccaa\n"
        )
        viewer.load_atlas(atlas, same_space=True)
        wait()
        assert len(viewer.rois) == 3
        viewer.roi_list.entries[1].setSelected(True)
        wait()
        for i in (0, 1):
            viewer.rois[i]["opacity"] = 0.12
            viewer.scene.actors[("roi", i)].GetProperty().SetOpacity(0.12)
        viewer.tabs.setCurrentIndex(2)
        viewer.tabs.widget(2).verticalScrollBar().setValue(0)
        viewer.status.setText(
            "Native tissue regions derived from supplied masks · real aligned tractography"
        )
        capture("atlas-dark")
        viewer.roi_list.setCurrentRow(0)
        original = viewer.rois[0]["data"].copy()
        viewer.morph_roi("Dilate")
        assert viewer.rois[0]["data"].sum() > original.sum()
        viewer.undo_roi(False)
        assert np.array_equal(viewer.rois[0]["data"], original)
        viewer.roi_list.setCurrentRow(0)
        viewer.rois[0]["role"] = "Include"
        viewer.rois[2]["role"] = "Exclude"
        viewer.tract_space_confirmed.setChecked(True)
        viewer.filter_roi_roles()
        wait()
        assert viewer.groups[viewer.tract_list.currentRow()].selected
        viewer.status.setText(
            "Actual volumetric ROI filtering · selected real fibers · source files preserved"
        )
        capture("roi-filter-dark")
        theme.apply("light")
        viewer.status.setText("Public alpha · research use only · real CC0 example")
        capture("viewer-light")
        record = {
            "version": __version__,
            "checks": [
                "startup without AI",
                "native 65-volume MRI navigation",
                "linked position",
                "real TCK and TRK indexing/rendering",
                "named atlas tree",
                "multiple actual surfaces",
                "morphology and undo",
                "volumetric include/exclude filtering",
                "dark/light native screenshots",
            ],
            "unchanged": {str(p): hash_file(p) == before[str(p)] for p in sources},
            "source_sha256": before,
            "tract_counts": {g.path.name: g.total for g in viewer.groups},
            "scientific_acceptance": "not inferred from software smoke tests",
        }
        assert all(record["unchanged"].values())
        (out / "validation.json").write_text(json.dumps(record, indent=2))
        print(json.dumps(record, indent=2))
    finally:
        wait()
        window.close()
        QTest.qWait(100)


if __name__ == "__main__":
    main()
