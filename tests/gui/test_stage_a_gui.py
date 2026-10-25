"""Native parameter editing, exact-task reopening and executed provenance."""

import os
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from test_sift2_gui import configure as configure_sift2
from test_tracking_gui import configure as configure_tracking

from dmri_studio.core.scientific import ImportRequest, import_artifact
from dmri_studio.core.scientific.records import restore_selection, save_selection

pytestmark = pytest.mark.skipif(
    not all(
        (Path("/usr/local/mrtrix3/bin") / name).is_file()
        for name in ("mrconvert", "tckgen", "tcksift2")
    ),
    reason="Native MRtrix3 is required for executed parameter and provenance tests",
)


def capture(window, name):
    target = os.environ.get("TRACTOSPHERE_STAGE_A_EVIDENCE")
    if target:
        folder = Path(target)
        folder.mkdir(parents=True, exist_ok=True)
        assert window.grab().save(str(folder / (name + ".png")))


@pytest.mark.parametrize("appearance", ["light", "dark"])
def test_tracking_parameters_preview_execute_reopen(window, qtbot, tmp_path, appearance):
    page, p, _ = configure_tracking(window, tmp_path)
    window.theme.apply(appearance)
    p.method.setCurrentIndex(p.method.findData("tckgen.ifod2"))
    p.power.setText("0.5")
    p.seed_direction.setText("1, 0, 0")
    p.unidirectional.setChecked(True)
    expected = dict(
        power=0.5, seed_direction=[1.0, 0.0, 0.0], unidirectional="on", stop_after_includes="off"
    )
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert p.plan is not None, page.status.text()
    argv = next(c["argv"] for c in p.plan.payload["commands"] if c["kind"] == "tracts")
    assert argv[argv.index("-power") + 1] == "0.5"
    assert argv[argv.index("-seed_direction") + 1] == "1.0,0.0,0.0"
    assert "-seed_unidirectional" in argv
    p.approval.setChecked(True)
    p.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert p.manifest["status"] == "validated_pending_qc", p.manifest.get("error")
    actual = p.manifest["plan"]["bindings"][0]["parameters"]
    assert all(actual[k] == v for k, v in expected.items())
    record = tmp_path / "selection.json"
    save_selection(record, tuple(page.artifacts), tuple(page.tasks))
    artifacts, tasks = restore_selection(record)
    page.artifacts, page.tasks = list(artifacts), list(tasks)
    page.refresh()
    p.task_source.setCurrentIndex(p.task_source.findData("tracking"))
    assert p.selected_task().to_dict() == tasks[0].to_dict()
    assert not p.power.isEnabled() and p.output.isEnabled()
    capture(window, "tracking-" + appearance)
    p.task_source.setCurrentIndex(0)
    mask = next(a for a in page.artifacts if a.kind == "mask")
    roi = import_artifact(
        ImportRequest(
            "roi", Path(mask.payload["path"]), declarations=mask.to_dict()["declarations"]
        )
    )
    page.artifacts.append(roi)
    page.refresh()
    p.constraints["include"].item(0).setCheckState(Qt.CheckState.Checked)
    p.stop_after_includes.setChecked(True)
    p.output.setText(str(tmp_path / "stop-preview"))
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert p.plan is not None, page.status.text()
    assert "-stop" in next(c["argv"] for c in p.plan.payload["commands"] if c["kind"] == "tracts")
    p.method.setCurrentIndex(p.method.findData("tckgen.sdstream"))
    assert not p.power.isEnabled()
    with pytest.raises(ValueError, match="power"):
        p.selected_task()


def test_sift2_coefficients_execute_and_reopen(window, qtbot, tmp_path):
    page, p, _ = configure_sift2(window, tmp_path)
    values = dict(min_coeff=-2.0, max_coeff=2.0, max_coeff_step=0.1)
    for key, value in values.items():
        p.settings[key].setText(str(value))
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert p.plan is not None, page.status.text()
    argv = next(c["argv"] for c in p.plan.payload["commands"] if "-out_mu" in c["argv"])
    for key, value in values.items():
        assert float(argv[argv.index("-" + key) + 1]) == value
    p.approval.setChecked(True)
    p.run_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=30000)
    assert p.manifest["status"] == "validated_pending_qc", p.manifest.get("error")
    record = tmp_path / "selection.json"
    save_selection(record, tuple(page.artifacts), tuple(page.tasks))
    artifacts, tasks = restore_selection(record)
    page.artifacts, page.tasks = list(artifacts), list(tasks)
    page.refresh()
    p.task_source.setCurrentIndex(p.task_source.findData(tasks[0].id))
    assert p.selected_task().to_dict() == tasks[0].to_dict()
    assert all(p.selected_task().parameters[k] == v for k, v in values.items())
    assert not p.settings["min_coeff"].isEnabled()
    for theme in ("light", "dark"):
        window.theme.apply(theme)
        qtbot.wait(50)
        capture(window, "sift2-" + theme)
    p.task_source.setCurrentIndex(0)
    p.settings["min_coeff"].setText("1")
    p.output.setText(str(tmp_path / "invalid-coefficient"))
    p.preview.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=20000)
    assert p.plan is None and not p.run_button.isEnabled()
    assert "min_coeff" in page.status.text()
