"""Native atlas acceptance with installed public FSL data and real TractoInferno.

Usage: .venv/bin/python scripts/validate_atlas_viewer.py NEW_OUTPUT_DIR
Does not estimate registration, generate fibers, or alter input images.
A three-tissue native label fixture is derived transparently from the public
subject's supplied disjoint WM/GM/CSF masks for the real-tract filtering check.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path
from unittest.mock import patch

import nibabel as nib
import numpy as np
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QPushButton,
    QScrollArea,
)

from dmri_studio.core.atlas import sha256
from dmri_studio.gui.main_window import MainWindow
from dmri_studio.gui.settings import Settings
from dmri_studio.gui.theme import Theme


def main():
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=False)
    project = Path(__file__).resolve().parents[2]
    base = Path(
        os.environ.get(
            "TRACTOSPHERE_REFERENCE_DIR",
            str(
                project
                / "data/viewer-reference/tractoinferno-sub-1006/derivatives/testset/sub-1006"
            ),
        )
    )
    fsl = Path.home() / "fsl/data/atlases"
    discrete = fsl / "JHU/JHU-ICBM-labels-2mm.nii.gz"
    probability = fsl / "JHU/JHU-ICBM-tracts-prob-2mm.nii.gz"
    anatomy = fsl / "JHU/JHU-ICBM-FA-2mm.nii.gz"
    source_paths = [
        discrete,
        probability,
        anatomy,
        fsl / "JHU-labels.xml",
        fsl / "JHU-tracts.xml",
        base / "anat/sub-1006__T1w.nii.gz",
        base / "tractography/sub-1006__AF_L.tck",
    ]
    source_paths += [base / f"mask/sub-1006__mask_{t}.nii.gz" for t in ("wm", "gm", "csf")]
    hashes = {str(p): sha256(p) for p in source_paths}
    app = QApplication([])
    theme = Theme(app)
    window = MainWindow(theme, Settings(QSettings(str(out / "settings.ini"), QSettings.IniFormat)))
    window.resize(1440, 960)
    window.show()
    window.activateWindow()
    window.focus_scientific_viewer()
    v = window.scientific_viewer
    errors = []

    def exception(*args):
        errors.append(str(args[1]))
        traceback.print_exception(*args)

    sys.excepthook = exception

    def wait(allow_error=False):
        end = time.monotonic() + 240
        while v.tasks and time.monotonic() < end:
            QTest.qWait(5)
            time.sleep(0.01)
        QTest.qWait(100)
        assert not v.tasks and not errors, errors
        if not allow_error:
            assert not v.status.text().startswith(("Operation failed", "Cannot complete")), (
                v.status.text()
            )

    def click(text):
        button = next(b for b in v.findChildren(QPushButton) if b.text() == text)
        for i in range(v.tabs.count()):
            page = v.tabs.widget(i)
            if page.isAncestorOf(button):
                v.tabs.setCurrentIndex(i)
                if isinstance(page, QScrollArea):
                    page.ensureWidgetVisible(button)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        wait()
        print("Clicked", text, flush=True)

    def capture(name):
        v.tabs.setCurrentIndex(2)
        v.tabs.widget(2).verticalScrollBar().setValue(0)
        QTest.qWait(80)
        assert window.grab().save(str(out / (name + ".png")))

    result = {}
    try:
        v.load_image(anatomy)
        wait()

        def accept_import():
            dialog = app.activeModalWidget()
            assert isinstance(dialog, QDialog)
            next(c for c in dialog.findChildren(QCheckBox) if "verified" in c.text()).setChecked(
                True
            )
            buttons = dialog.findChild(QDialogButtonBox)
            QTest.mouseClick(
                buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton
            )

        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(discrete), "")):
            QTimer.singleShot(150, accept_import)
            click("Import anatomical atlas")
        expected_labels = np.unique(np.asarray(nib.load(discrete).dataobj))
        expected_labels = expected_labels[expected_labels > 0]
        assert len(v.rois) == len(expected_labels) and v.roi_list.topLevelItemCount() == 1
        assert v.rois[0]["label_id"] == 1 and v.rois[0]["name"] == "Middle cerebellar peduncle"
        assert v.roi_list.topLevelItem(0).childCount() == len(expected_labels)
        assert v.scene.actors[("roi", 0)].GetMapper().GetInput().GetNumberOfPoints() > 0
        v.scene.camera("Isometric")
        v.slice_opacity.setValue(0.2)
        capture("01-jhu-labels-populated")
        # Native tree selection, including multi-selection and immediate 3D generation.
        tree = v.roi_list
        tree.scrollToItem(tree.entries[2])
        QTest.qWait(40)
        QTest.mouseClick(
            tree.viewport(),
            Qt.MouseButton.LeftButton,
            pos=tree.visualItemRect(tree.entries[2]).center(),
        )
        wait()
        assert ("roi", 2) in v.scene.actors
        v.atlas_visibility(0, False)
        for check in v.plane_checks:
            check.setChecked(False)
        v.scene.camera("Left")
        capture("02-individual-anatomical-region")
        tree.entries[3].setSelected(True)
        tree.entries[4].setSelected(True)
        wait()
        assert all(v.scene.actors[("roi", i)].GetVisibility() for i in (2, 3, 4))
        v.roi_opacity.setValue(1.0)
        assert v.scene.actors[("roi", 2)].GetProperty().GetOpacity() == 1.0
        v.roi_opacity.setValue(0.35)
        with patch.object(QColorDialog, "getColor", return_value=QColor("#55bb99")):
            click("ROI color")
        assert np.allclose(v.rois[2]["color"], QColor("#55bb99").getRgbF()[:3])
        tree.entries[4].setCheckState(0, Qt.CheckState.Unchecked)
        wait()
        assert not v.scene.actors[("roi", 4)].GetVisibility()
        tree.entries[4].setCheckState(0, Qt.CheckState.Checked)
        wait()
        v.scene.camera("Isometric")
        capture("03-multiple-regions")
        for check in v.plane_checks:
            check.setChecked(True)
        result["discrete"] = {
            "regions": len(v.rois),
            "metadata": str(fsl / "JHU-labels.xml"),
            "first_region": v.rois[0]["name"],
        }
        # Save one atlas-derived ROI with its identity/role and reload it.
        v.roi_role.setCurrentText("Include")
        click("Assign role to selected ROIs")
        with patch.object(
            QFileDialog, "getSaveFileName", return_value=(str(out / "saved-roi.nii.gz"), "")
        ):
            click("Save selected ROI as new NIfTI")
        v.load_roi(out / "saved-roi.nii.gz")
        wait()
        assert v.rois[-1]["label_id"] == 3 and v.rois[-1]["role"] == "Include"
        # Probability threshold is configurable and stored per atlas group.
        v.load_atlas(probability, same_space=True, threshold=0.25)
        wait()
        p25 = [r for r in v.rois if r.get("atlas_kind") == "probability"]
        assert len(p25) == 20 and p25[0]["name"] == "Anterior thalamic radiation L"
        counts25 = {r["label_id"]: r["voxel_count"] for r in p25}
        v.load_atlas(probability, same_space=True, threshold=0.5)
        wait()
        p50 = [r for r in v.rois if r.get("probability_threshold") == 0.5]
        assert p50 and all(r["voxel_count"] <= counts25[r["label_id"]] for r in p50)
        capture("04-probabilistic-atlas")
        result["probability"] = {
            "threshold_025_count": len(p25),
            "threshold_050_count": len(p50),
            "threshold_counts_monotonic": True,
        }
        with patch.object(
            QFileDialog, "getSaveFileName", return_value=(str(out / "atlas-workspace.json"), "")
        ):
            click("Save")
        count = len(v.rois)
        saved_visibility = [r["visible"] for r in v.rois]
        v.open_workspace(out / "atlas-workspace.json")
        wait()
        assert len(v.rois) == count and v.rois[0]["label_id"] == 1
        assert [r["visible"] for r in v.rois] == saved_visibility
        result["workspace_restored_regions"] = count
        result["workspace_visibility_preserved"] = True
        # MNI atlas must not silently overlay this native subject.
        v.load_image(base / "anat/sub-1006__T1w.nii.gz")
        wait()
        v.load_atlas(discrete, same_space=True)
        wait(True)
        assert "space mismatch" in v.status.text() and not v.rois
        result["mismatch_rejection"] = v.status.text()
        capture("05-mni-native-mismatch")
        # Derive a non-overlapping native tissue label map from the supplied masks.
        mask_images = [
            nib.load(base / f"mask/sub-1006__mask_{t}.nii.gz") for t in ("wm", "gm", "csf")
        ]
        masks = [np.asarray(i.dataobj) > 0 for i in mask_images]
        overlap = np.sum(masks, axis=0) > 1
        assert not overlap.any(), "Tissue masks overlap; do not invent label priority"
        data = np.zeros(masks[0].shape, np.uint8)
        for k, mask in enumerate(masks, 1):
            data[mask] = k
        native = out / "sub-1006_tissues_dseg.nii.gz"
        img = nib.Nifti1Image(data, mask_images[0].affine, mask_images[0].header)
        nib.save(img, native)
        native.with_name("sub-1006_tissues_dseg.tsv").write_text(
            "index\tname\tcolor\n1\tWhite matter (provided mask)\t#5599ee\n2\tGray matter (provided mask)\t#dd9955\n3\tCSF (provided mask)\t#55ccaa\n"
        )
        v.load_atlas(native, same_space=True)
        wait()
        assert len(v.rois) == 3
        v.load_tracts(base / "tractography/sub-1006__AF_L.tck")
        wait()
        v.tract_space_confirmed.setChecked(True)
        v.density.setValue(1500)
        v.roi_list.setCurrentRow(0)
        v.roi_list.entries[1].setSelected(True)
        wait()
        for i in (0, 1):
            v.rois[i]["opacity"] = 0.12
            v.scene.actors[("roi", i)].GetProperty().SetOpacity(0.12)
        v.scene.camera("Left")
        capture("06-native-tissue-regions-real-tracts")
        v.roi_list.clearSelection()
        v.roi_list.setCurrentRow(0)
        v.roi_role.setCurrentText("Include")
        click("Assign role to selected ROIs")
        click("Filter tracts with ROI roles")
        included = set(v.groups[0].selected)
        assert included
        v.roi_list.setCurrentRow(2)
        v.roi_role.setCurrentText("Exclude")
        click("Assign role to selected ROIs")
        click("Filter tracts with ROI roles")
        retained = set(v.groups[0].selected)
        assert retained and retained <= included
        result["native_filter"] = {
            "total": v.groups[0].total,
            "wm_include": len(included),
            "wm_include_csf_exclude": len(retained),
            "native_fixture": "Three provided subject masks; no MNI registration or region naming inferred",
        }
        v.atlas_visibility(2, False)
        capture("07-roi-filtered-streamlines")
        with patch.object(
            QFileDialog,
            "getSaveFileName",
            return_value=(str(out / "selected.tck"), "MRtrix (*.tck)"),
        ):
            click("Save selected tract group")
        saved = nib.streamlines.load(out / "selected.tck").tractogram.streamlines
        assert len(saved) == len(retained)
        for actual, index in zip(saved, sorted(retained), strict=True):
            assert np.allclose(actual, v.groups[0].line(index), atol=1e-5)
        theme.apply("light")
        capture("08-light-theme")
        result["source_sha256"] = hashes
        result["source_sha256_unchanged"] = {
            str(p): sha256(p) == hashes[str(p)] for p in source_paths
        }
        assert all(result["source_sha256_unchanged"].values())
        result["sources"] = {
            "FSL_atlases": "https://fsl.fmrib.ox.ac.uk/fsl/docs/other/datasets.html",
            "FSL_XML": "https://fsl.fmrib.ox.ac.uk/fsl/docs/other/atlas_xml.html",
            "license": "https://fsl.fmrib.ox.ac.uk/fsl/docs/license.html",
            "TractoInferno": "https://openneuro.org/datasets/ds003900",
        }
        (out / "validation.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        wait(True)
        window.close()
        QTest.qWait(100)


if __name__ == "__main__":
    main()
