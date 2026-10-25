"""Saved scientific software is an application default, not a command approval."""

import importlib.util
import platform
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings

from dmri_studio.core.dependencies import inspect_dependencies
from dmri_studio.gui import dependency_setup
from dmri_studio.gui.settings import ScientificSoftwareSettings, Settings


def test_settings_survive_restart_and_empty_never_means_cwd(tmp_path):
    file = tmp_path / "preferences.ini"
    first = ScientificSoftwareSettings(Settings(QSettings(str(file), QSettings.IniFormat)))
    assert all(first.location(family) is None for family in first.locations())
    for family in first.locations():
        folder = tmp_path / family / "bin"
        folder.mkdir(parents=True)
        first.replace(family, str(folder))
    fresh = ScientificSoftwareSettings(Settings(QSettings(str(file), QSettings.IniFormat)))
    assert {family: str(fresh.location(family)) for family in fresh.locations()} == {
        family: str(tmp_path / family / "bin") for family in first.locations()
    }
    expected_state = "unsupported" if platform.system() == "Windows" else "incomplete"
    assert all(fresh.state(family) == expected_state for family in fresh.locations())
    first.replace("ANTs", None)
    assert (
        ScientificSoftwareSettings(Settings(QSettings(str(file), QSettings.IniFormat))).location(
            "ANTs"
        )
        is None
    )


def test_failed_settings_write_reports_failure(tmp_path):
    target = tmp_path / "directory-not-an-ini-file"
    target.mkdir()
    software = ScientificSoftwareSettings(Settings(QSettings(str(target), QSettings.IniFormat)))
    with pytest.raises(OSError, match="Could not save application settings"):
        software.replace("MRtrix3", str(tmp_path / "candidate"))
    assert software.location("MRtrix3") is None


def test_legacy_saved_paths_reach_new_workspaces(window, tmp_path):
    locations = {family: str(tmp_path / family / "bin") for family in ("MRtrix3", "FSL", "ANTs")}
    for family, path in locations.items():
        window.settings.set(f"dependencies/{family}", path)
    window.settings.store.sync()
    window.apply_dependency_locations(window.dependencies.saved_directories())
    assert window.scientific.tool_directory == Path(locations["MRtrix3"])
    assert window.scientific.fsl_directory == Path(locations["FSL"])
    assert window.workflow.execution.tool_directory.text() == locations["MRtrix3"]
    assert window.workflow.execution.bias_inputs["ants_directory"].text() == locations["ANTs"]
    assert window.workflow.execution.correction_inputs["fsl_directory"].text() == locations["FSL"]
    assert window.batch.mrtrix_directory.text() == locations["MRtrix3"]
    assert window.batch.fsl_directory == Path(locations["FSL"])
    assert window.batch.ants_directory == Path(locations["ANTs"])


def test_new_batch_resolver_uses_current_saved_paths(window, monkeypatch, tmp_path):
    page = window.batch
    paths = {family: str(tmp_path / family / "bin") for family in ("MRtrix3", "FSL", "ANTs")}
    for family, path in paths.items():
        window.settings.set(f"dependencies/{family}", path)
    window.apply_dependency_locations(paths)
    page.study = object()
    page.output.setText(str(tmp_path / "new-batch"))
    captured = {}

    def build(*args, **kwargs):
        captured.update(kwargs)

    from dmri_studio.gui import batch

    monkeypatch.setattr(batch, "build_batch_plan", build)
    monkeypatch.setattr(page, "start", lambda _kind, task: task(lambda: False, lambda _: None))
    page.dry_run()
    assert captured["tool_directory"] == Path(paths["MRtrix3"])
    assert captured["fsl_directory"] == Path(paths["FSL"])
    assert captured["ants_directory"] == Path(paths["ANTs"])


def test_registration_preview_receives_saved_fsl_and_mrtrix(window, monkeypatch, tmp_path):
    from dmri_studio.gui import registration

    mrtrix = str(tmp_path / "mrtrix" / "bin")
    fsl = str(tmp_path / "fsl" / "bin")
    window.apply_dependency_locations({"MRtrix3": mrtrix, "FSL": fsl})
    panel = window.scientific.registration
    panel.task = SimpleNamespace(id="registration")
    panel.output.setText(str(tmp_path / "new-registration"))
    captured = {}
    monkeypatch.setattr(
        registration, "prepare_registration", lambda *args, **kwargs: captured.update(kwargs)
    )
    monkeypatch.setattr(
        window.scientific, "start", lambda _kind, task: task(lambda: False, lambda _: None)
    )
    panel.prepare()
    assert captured["tool_directory"] == Path(mrtrix)
    assert captured["fsl_directory"] == Path(fsl)


def test_unverified_path_and_interrupted_report_never_save(window, tmp_path):
    controller = window.dependencies
    controller.inputs["ANTs"] = str(tmp_path / "unverified")
    with pytest.raises(ValueError):
        controller.apply_verified_family("ANTs")
    controller.report = {"cancelled": True, "tools": []}
    with pytest.raises(ValueError):
        controller.apply()
    assert controller.saved_directories()["ANTs"] == ""


@pytest.mark.parametrize("state, expected_saved", [("version_match", True), ("unsupported", False)])
def test_verify_and_save_requires_compatible_family(
    window, qtbot, monkeypatch, tmp_path, state, expected_saved
):
    directory = tmp_path / "ANTs" / "bin"
    directory.mkdir(parents=True)
    (directory / "N4BiasFieldCorrection").touch()
    report = inspect_dependencies(environ={"PATH": ""}, search_common=False)
    record = next(row for row in report["tools"] if row["family"] == "ANTs")
    record.update(path=str(directory / "N4BiasFieldCorrection"), state=state, version="synthetic")
    monkeypatch.setattr(dependency_setup, "inspect_dependencies", lambda **_: report)
    widget = window.preferences.dependencies
    widget.fields["ANTs"].setText(str(directory))
    widget.verify_and_save("ANTs")
    qtbot.waitUntil(lambda: window.dependencies.thread is None, timeout=10000)
    qtbot.waitUntil(lambda: widget.pending_family is None, timeout=10000)
    saved = window.dependencies.saved_directories()["ANTs"]
    can_save = expected_saved and platform.system() != "Windows"
    assert (saved == str(directory)) is can_save
    if can_save:
        assert window.workflow.execution.bias_inputs["ants_directory"].text() == saved
        directory.rename(tmp_path / "moved")
        assert window.dependencies.software.state("ANTs") == "missing"
        widget.populate(None)
        assert "Missing installation" in widget.family_labels["ANTs"].text()
        replacement = tmp_path / "replacement" / "bin"
        replacement.mkdir(parents=True)
        (replacement / "N4BiasFieldCorrection").touch()
        record["path"] = str(replacement / "N4BiasFieldCorrection")
        widget.fields["ANTs"].setText(str(replacement))
        widget.verify_and_save("ANTs")
        qtbot.waitUntil(lambda: window.dependencies.thread is None, timeout=10000)
        qtbot.waitUntil(lambda: widget.pending_family is None, timeout=10000)
        assert window.dependencies.saved_directories()["ANTs"] == str(replacement)
        assert window.workflow.execution.bias_inputs["ants_directory"].text() == str(replacement)
    else:
        assert (
            "unsupported" if platform.system() == "Windows" else "incompatible"
        ) in widget.status.text()


def test_remove_updates_defaults_and_keeps_active_plan(window, tmp_path):
    location = str(tmp_path / "mrtrix" / "bin")
    window.settings.set("dependencies/MRtrix3", location)
    window.apply_dependency_locations({"MRtrix3": location})
    plan = object()
    window.scientific.tracking.plan = plan
    window.scientific.tracking.approval.setChecked(True)
    assert not window.can_change_dependencies()
    window.preferences.dependencies.remove_location("MRtrix3")
    assert window.dependencies.saved_directories()["MRtrix3"] == location
    assert window.scientific.tracking.plan is plan
    window.scientific.tracking.approval.setChecked(False)
    window.preferences.dependencies.remove_location("MRtrix3")
    assert window.dependencies.saved_directories()["MRtrix3"] == ""
    assert window.scientific.tool_directory is None
    assert window.batch.mrtrix_directory.text() == ""


@pytest.mark.parametrize("worker", ["scientific", "batch", "preprocessing"])
def test_active_worker_blocks_configuration_change(window, tmp_path, worker):
    location = str(tmp_path / "mrtrix" / "bin")
    window.settings.set("dependencies/MRtrix3", location)
    target = {
        "scientific": window.scientific,
        "batch": window.batch,
        "preprocessing": window.workflow.execution,
    }[worker]
    target.thread = object()
    try:
        window.preferences.dependencies.remove_location("MRtrix3")
        assert window.dependencies.saved_directories()["MRtrix3"] == location
        assert "Finish the active" in window.preferences.dependencies.status.text()
    finally:
        target.thread = None


def test_historical_batch_is_not_replaced_by_new_global_default(window, tmp_path):
    page = window.batch
    historical = str(tmp_path / "old" / "bin")
    page.historical_toolchain = True
    page.mrtrix_directory.setText(historical)
    current = str(tmp_path / "new" / "bin")
    window.settings.set("dependencies/MRtrix3", current)
    window.apply_dependency_locations({"MRtrix3": current})
    assert page.mrtrix_directory.text() == historical
    assert page.tool_directory == Path(current)
    page.study = object()
    page.dry_run()
    assert "retains its approved toolchain" in page.status.text()
    page.new_batch()
    assert page.mrtrix_directory.text() == current


@pytest.mark.skipif(
    not all(
        (Path("/usr/local/mrtrix3/bin") / name).is_file()
        for name in ("mrconvert", "tensor2metric")
    ),
    reason="Native MRtrix3 is required to create the approved batch fixture",
)
def test_reopened_approved_batch_keeps_recorded_toolchain(window, qtbot, tmp_path):
    from dmri_studio.core.batch.engine import create_batch
    from dmri_studio.core.batch.planning import GIB, build_batch_plan
    from dmri_studio.core.scientific.records import read_json

    module_path = Path(__file__).parents[1] / "core/test_batch_engine.py"
    spec = importlib.util.spec_from_file_location("phase1_batch_fixture", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    study, workflow = module.fixture(tmp_path, count=1)
    old_fsl = tmp_path / "historical-fsl" / "bin"
    old_ants = tmp_path / "historical-ants" / "bin"
    plan = build_batch_plan(
        study,
        workflow,
        tmp_path / "approved-batch",
        tool_directory=module.BIN,
        fsl_directory=old_fsl,
        ants_directory=old_ants,
        limits={"disk_headroom_bytes": GIB},
    )
    folder = create_batch(plan, plan.digest)
    approved_path = folder / "approved-plan.json"
    approved_before = read_json(approved_path.read_text())
    recorded_tools = approved_before["plan"]["rows"][0]["preview"]["tools"]
    assert recorded_tools
    assert all(
        tool["sha256"] and tool["path"].startswith(str(module.BIN))
        for tool in recorded_tools.values()
    )
    page = window.batch
    page.load_batch(folder)
    qtbot.waitUntil(lambda: page.thread is None, timeout=10000)
    assert page.historical_toolchain
    assert page.historical_plan.digest == plan.digest
    assert page.mrtrix_directory.text() == str(module.BIN)
    assert "historical-fsl" in page.preview.toPlainText()
    window.apply_dependency_locations({"MRtrix3": str(tmp_path / "new-mrtrix")})
    assert page.mrtrix_directory.text() == str(module.BIN)
    assert read_json(approved_path.read_text()) == approved_before
