"""Native ACT/5TT controls over analytic fixtures, not subject screenshots."""

import importlib.util
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.gui.operation_state import State

native_mrtrix = pytest.mark.skipif(
    not all(
        (Path("/usr/local/mrtrix3/bin") / name).is_file()
        for name in ("mrconvert", "5ttcheck", "5tt2vis", "tckgen")
    ),
    reason="Native MRtrix3 is required for ACT and 5TT execution",
)


def configure(window, tmp_path):
    """Reuse explicit analytic tissue/fiber definitions from the core tests."""
    spec = importlib.util.spec_from_file_location(
        "act_fixture", Path(__file__).parents[1] / "core/test_act.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    art = fixture.tissue.__wrapped__(tmp_path)
    window.focus_tracking_plan()
    page = window.scientific
    page.artifacts = list(art)
    page.refresh()
    page.tool_directory = Path("/usr/local/mrtrix3/bin")
    p = page.tracking
    p.mode.setCurrentIndex(p.mode.findData("act"))
    p.seed_method.setCurrentIndex(p.seed_method.findData("image"))
    for combo, a in (
        (p.source, art[2]),
        (p.five_tt, art[0]),
        (p.reference, art[1]),
        (p.seed, art[3]),
    ):
        combo.setCurrentIndex(combo.findData(a.id))
    p.count.setValue(10)
    p.seeds.setValue(500)
    p.minimum.setValue(3)
    p.maximum.setValue(38)
    p.step.setValue(0.5)
    p.angle.setValue(30)
    p.cutoff.setValue(0.01)
    p.seed_cutoff.setValue(0.02)
    p.output.setText(str(tmp_path / "act-run"))
    return page, p, art


@pytest.mark.parametrize("appearance", ["light", "dark"])
def test_act_controls_typed_task_and_mode_switch(window, qtbot, tmp_path, appearance):
    page, p, art = configure(window, tmp_path)
    window.theme.apply(appearance)
    t = p.selected_task()
    assert t.algorithm == "tckgen.act_ifod2" and "mask" not in t.inputs
    assert t.inputs["five_tt"] == art[0].id and t.parameters["backtrack"] == "on"
    assert not p.run_button.isEnabled()
    assert not p.method.model().item(p.method.findData("tckgen.tensor_det")).isEnabled()
    p.method.setCurrentIndex(p.method.findData("tckgen.sdstream"))
    assert not p.backtrack.isEnabled() and not p.backtrack.isChecked()
    assert p.selected_task().parameters["backtrack"] == "off"
    p.mode.setCurrentIndex(p.mode.findData("standard"))
    assert p.method.model().item(p.method.findData("tckgen.tensor_det")).isEnabled()
    p.mask.setCurrentIndex(p.mask.findData(art[3].id))
    assert p.selected_task().algorithm == "tckgen.sdstream"
    assert not page.operation_state.busy and not window.toast.isVisible()


@native_mrtrix
def test_native_act_approval_run_qc_viewer(window, qtbot, tmp_path):
    page, p, _ = configure(window, tmp_path)
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert p.plan is not None, page.status.text()
    assert "-act" in p.log.toPlainText() and "-backtrack" in p.log.toPlainText()
    p.approval.setChecked(True)
    p.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert p.manifest["status"] == "validated_pending_qc", p.manifest.get("error")
    assert p.manifest["validation"]["measurements"][0]["act"]["endpoints"] == 20
    assert page.operation_state.state == State.WARNING and not page.operation_state.busy
    p.maps.setCurrentIndex(
        next(i for i in range(p.maps.count()) if p.maps.itemData(i)["kind"] == "tractogram")
    )
    p.open_result()
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    qtbot.waitUntil(
        lambda: window.scientific_viewer is not None and not window.scientific_viewer.tasks,
        timeout=15000,
    )
    assert window.scientific_viewer.groups[0].total == 10
    assert not window.toast.isVisible()


@native_mrtrix
def test_native_tissue_panel_and_compartment_handoff(window, qtbot, tmp_path):
    page, _, art = configure(window, tmp_path)
    p = page.anatomy
    page.tabs.setCurrentWidget(p)
    p.operation.setCurrentIndex(p.operation.findData("5tt2vis"))
    p.source.setCurrentIndex(p.source.findData(art[0].id))
    p.reference.setCurrentIndex(p.reference.findData(art[1].id))
    p.output.setText(str(tmp_path / "tissues"))
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert p.plan is not None, page.status.text()
    p.approval.setChecked(True)
    p.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert p.manifest["status"] == "validated_pending_qc", p.manifest.get("error")
    assert p.maps.count() == 6
    p.open_result()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    qtbot.waitUntil(
        lambda: window.scientific_viewer is not None and not window.scientific_viewer.tasks,
        timeout=15000,
    )
    assert window.scientific_viewer.workspace_mode == "image"
    assert not page.operation_state.busy


@native_mrtrix
def test_anatomy_failure_recovery_clears_active_state(window, qtbot, tmp_path):
    page, p, art = configure(window, tmp_path)
    from dmri_studio.core.scientific import ImportRequest, import_artifact

    five = import_artifact(
        ImportRequest(
            "5tt",
            Path(art[0].payload["path"]),
            declarations={
                "channels": art[0].payload["declarations"]["channels"],
                "subject_id": "analytic",
                "space_id": "analytic-ras",
            },
        )
    )
    page.artifacts.append(five)
    page.refresh()
    p.five_tt.setCurrentIndex(p.five_tt.findData(five.id))
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert p.plan is None and page.operation_state.state == State.FAILED
    p.five_tt.setCurrentIndex(p.five_tt.findData(art[0].id))
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert p.plan is not None, page.status.text()
    assert page.operation_state.state == State.COMPLETED and not p.run_button.isEnabled()


def test_lossless_tissue_overlay_axis_flip(window, qtbot, tmp_path):
    _, _, art = configure(window, tmp_path)
    ref = nib.load(art[1].payload["path"])
    data = np.asarray(nib.load(art[0].payload["path"]).dataobj)[..., 2]
    affine = ref.affine.copy()
    affine[:3, 3] += affine[:3, 0] * (data.shape[0] - 1)
    affine[:3, 0] *= -1
    out = tmp_path / "flipped-tissue.nii"
    im = nib.Nifti1Image(data[::-1].copy(), affine)
    im.header.set_xyzt_units("mm")
    nib.save(im, out)
    window.focus_viewer()
    v = window.scientific_viewer
    v.load_image(Path(art[1].payload["path"]))
    qtbot.waitUntil(lambda: not v.tasks, timeout=15000)
    v.load_layer(out)
    qtbot.waitUntil(lambda: not v.tasks, timeout=15000)
    assert len(v.layers) == 1, v.status.text()
    np.testing.assert_array_equal(v.layers[0]["data"], data)
    assert v.layers[0]["lossless_axis_reindexing"]["flips"] == [0]
    assert not v.layers[0]["resampled_same_space"]
