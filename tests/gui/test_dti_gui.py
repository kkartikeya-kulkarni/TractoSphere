"""Native DTI command approval, actual fitting, scalar/DEC/QC/result handoffs."""

from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.execution import hash_file
from dmri_studio.core.scientific import ImportRequest

BIN = Path("/usr/local/mrtrix3/bin")


@pytest.mark.skipif(
    not (BIN / "dwi2tensor").is_file(), reason="Real validated MRtrix needed, not simulated science"
)
def test_actual_guided_dti_approval_maps_vector_qc_history(window, qtbot, tmp_path):
    """Drive existing controls all the way through actual MRtrix and existing viewer."""
    source = tmp_path / "inputs"
    source.mkdir()
    rng = np.random.default_rng(19)
    g = rng.normal(size=(20, 3))
    g /= np.linalg.norm(g, axis=1)[:, None]
    g = np.vstack(([0, 0, 0], g))
    b = np.r_[0, np.full(20, 1000.0)]
    signal = 1000 * np.exp(-b * np.sum(g * g * np.array([0.0015, 0.0005, 0.0003]), axis=1))
    image = nib.Nifti1Image(
        np.broadcast_to(signal, (8, 9, 10, 21)).astype("f4"), np.diag([-2, 2, 2, 1])
    )
    image.set_qform(image.affine, 1)
    image.set_sform(image.affine, 1)
    image.header.set_xyzt_units("mm")
    path = source / "dwi.nii"
    nib.save(image, path)
    np.savetxt(source / "bval", b[None, :])
    np.savetxt(source / "bvec", g.T)
    before = hash_file(path)
    window.focus_models()
    page = window.scientific
    page.import_request(
        ImportRequest(
            "dwi",
            path,
            source / "bval",
            source / "bvec",
            declarations={"processing_state": "preprocessed"},
        )
    )
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    panel = page.dti
    page.tabs.setCurrentWidget(panel)
    page.tool_directory = BIN
    panel.shells.setText("0,1000")
    panel.output.setText(str(tmp_path / "run"))
    panel.guided.click()
    assert len(page.tasks) == 2
    assert page.tasks[0].parameters["fit"] == "IWLS" and page.tasks[0].parameters["iterations"] == 2
    assert not panel.run_button.isEnabled()
    panel.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert panel.plan is not None, page.status.text()
    assert not (tmp_path / "run").exists()
    assert "-iter 2" in panel.log.toPlainText()
    panel.approval.setChecked(True)
    assert panel.run_button.isEnabled()
    panel.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert panel.manifest["status"] == "validated_pending_qc", panel.manifest.get("error")
    assert panel.maps.count() > 5 and not panel.run_button.isEnabled()
    for suffix in ("FA", "MD", "AD", "RD", "V1"):
        index = next(
            i
            for i in range(panel.maps.count())
            if panel.maps.itemData(i).endswith("-" + suffix + ".nii.gz")
        )
        panel.maps.setCurrentIndex(index)
        panel.open_result()
        qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
        qtbot.waitUntil(
            lambda: window.scientific_viewer is not None and not window.scientific_viewer.tasks,
            timeout=15000,
        )
        viewer = window.scientific_viewer
        assert viewer.reference.count == 1
        assert (viewer.dti_direction is not None) == (suffix == "V1")
        assert all(view.canvas.image is not None for view in viewer.views.values())
        if suffix in {"MD", "AD", "RD"}:
            # Diffusivity values are ~1e-3 mm²/s; millesimal rounding loses
            # scientifically relevant window/level settings, even with correct data.
            viewer.range_changed(-0.00025, 0.0035)
            assert viewer.low.value() == pytest.approx(-0.00025, abs=1e-9)
            assert viewer.high.value() == pytest.approx(0.0035, abs=1e-9)
            assert viewer.high.singleStep() < 0.001
    panel.open_qc()
    qtbot.waitUntil(lambda: window.qc.thread is None, timeout=10000)
    assert window.qc.catalog is not None
    panel.open_run()
    qtbot.waitUntil(lambda: window.results.thread is None, timeout=10000)
    assert window.results.snapshot["status"] == "validated_pending_qc"
    assert hash_file(path) == before


def test_dti_empty_invalid_missing_dependency_controls(window, qtbot, tmp_path):
    """Absent inputs are actionable errors, not empty success or runnable buttons."""
    window.focus_models()
    page = window.scientific
    panel = page.dti
    page.tabs.setCurrentWidget(panel)
    panel.guided.click()
    assert "Import DWI" in page.status.text() and not page.tasks
    panel.output.setText(str(tmp_path / "run"))
    panel.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert panel.plan is None and not panel.run_button.isEnabled()
    assert "Only implemented DTI" in page.status.text()
