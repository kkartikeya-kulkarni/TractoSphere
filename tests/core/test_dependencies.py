"""Tool discovery must not relax scientific gates or run FSL imaging commands."""

import json
import os
import sys

import pytest

from dmri_studio.core.dependencies import TOOLS, inspect_dependencies

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="POSIX executable fixtures; Windows remains experimental"
)


def executable(path, output):
    """A clearly synthetic version-only executable, never participant evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\nprint({output!r})\n")
    path.chmod(0o755)


@pytest.fixture
def installation(tmp_path):
    paths = {family: tmp_path / family / "bin" for family in TOOLS}
    for family, names in TOOLS.items():
        for name in names:
            text = f"== {name} 3.0.5 ==" if family == "MRtrix3" else "ANTs Version: 2.6.5"
            executable(paths[family] / name, text)
            if family == "FSL":
                # A FSL command must NEVER be invoked by the setup scanner.
                (paths[family] / name).write_text(
                    f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(tmp_path / 'FSL-WAS-RUN')!r}).write_text('bad')\n"
                )
                metadata = paths[family].parent / "conda-meta"
                metadata.mkdir(exist_ok=True)
                (metadata / f"fsl-{name}.json").write_text(
                    json.dumps(
                        dict(
                            version="2401.2" if name == "eddy_cpu" else "example-component-version",
                            build="fixture-build",
                            subdir="fixture-platform",
                            files=[f"bin/{name}"],
                        )
                    )
                )
    wrapper = paths["MRtrix3"].parent / "lib/mrtrix3/dwibiascorrect/ants.py"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("# Synthetic wrapper-layout fixture\n")
    version = paths["FSL"].parent / "etc/fslversion"
    version.parent.mkdir()
    version.write_text("6.0.7.18\n")
    return paths


def scan(paths):
    return inspect_dependencies(directories=paths, environ={"PATH": ""}, search_common=False)


def test_missing_is_optional_and_viewer_ready(tmp_path):
    report = inspect_dependencies(environ={"PATH": ""}, search_common=False, home=tmp_path)
    assert all(r["state"] == "missing" for r in report["tools"])
    assert report["features"]["viewer"]["state"] == "available"
    assert report["features"]["bias"]["state"] == "needs_setup"
    assert report["automatic_installation"] is False


def test_exact_versions_component_builds_and_wrapper_no_FSL_run(installation):
    report = scan(installation)
    assert all(t["state"] == "version_match" for t in report["tools"])
    assert all(f["state"] != "needs_setup" for f in report["features"].values())
    eddy = next(t for t in report["tools"] if t["name"] == "eddy_cpu")
    assert eddy["version"] == "2401.2" and eddy["build"] == "fixture-build"
    assert eddy["version_source"].endswith("fsl-eddy_cpu.json")
    assert not (installation["FSL"].parents[1] / "FSL-WAS-RUN").exists()
    assert report["fsl_suite_versions"][str(installation["FSL"].parent)] == "6.0.7.18"


def test_version_mismatch_keeps_backend_rejections(installation):
    from dmri_studio.core.bias_correction import _tools
    from dmri_studio.core.correction import _fsl_tools
    from dmri_studio.core.execution import detect_mrtrix

    executable(installation["MRtrix3"] / "mrconvert", "== mrconvert 3.0.7 ==")
    executable(installation["ANTs"] / "N4BiasFieldCorrection", "ANTs Version: 2.6.6")
    meta = installation["FSL"].parent / "conda-meta/fsl-eddy_cpu.json"
    record = json.loads(meta.read_text())
    record["version"] = "2401.3"
    meta.write_text(json.dumps(record))
    report = scan(installation)
    assert {t["name"] for t in report["tools"] if t["state"] == "unsupported"} == {
        "mrconvert",
        "eddy_cpu",
        "N4BiasFieldCorrection",
    }
    with pytest.raises(ValueError, match="unvalidated"):
        detect_mrtrix(tool_directory=installation["MRtrix3"])
    with pytest.raises(ValueError, match="2401.2"):
        _fsl_tools(installation["FSL"], lambda: False)
    executable(installation["MRtrix3"] / "mrconvert", "== mrconvert 3.0.5 ==")
    with pytest.raises(ValueError, match="2.6.5"):
        _tools(installation["MRtrix3"], installation["ANTs"], lambda: False)


def test_fsl_suite_version_cannot_replace_component_records(installation):
    (installation["FSL"].parent / "conda-meta/fsl-eddy_cpu.json").unlink()
    report = scan(installation)
    eddy = next(t for t in report["tools"] if t["name"] == "eddy_cpu")
    assert eddy["version"] is None and eddy["state"] == "unverified"
    assert report["features"]["correction"]["state"] == "needs_setup"


def test_fsl_launcher_and_explicit_missing_folder(installation, tmp_path):
    launchers = installation["FSL"].parent / "share/fsl/bin"
    launchers.mkdir(parents=True)
    executable(launchers / "eddy_cpu", "Not a package version")
    paths = {**installation, "FSL": None}
    report = inspect_dependencies(
        directories=paths, environ={"PATH": str(launchers)}, search_common=False
    )
    eddy = next(t for t in report["tools"] if t["name"] == "eddy_cpu")
    assert eddy["path"] == str(installation["FSL"] / "eddy_cpu")
    paths["MRtrix3"] = tmp_path / "missing-selected-folder"
    report = inspect_dependencies(
        directories=paths, environ={"PATH": str(installation["MRtrix3"])}, search_common=False
    )
    assert all(t["state"] == "missing" for t in report["tools"] if t["family"] == "MRtrix3")


def test_root_directories_wrapper_layout_and_cancellation(installation):
    report = scan({k: p.parent for k, p in installation.items()})
    assert report["features"]["bias"]["state"] == "version_requirements_met"
    (installation["MRtrix3"].parent / "lib/mrtrix3/dwibiascorrect/ants.py").unlink()
    assert scan(installation)["features"]["bias"]["state"] == "needs_setup"
    cancelled = inspect_dependencies(cancelled=lambda: True)
    assert cancelled["cancelled"] and not cancelled["tools"]
    assert cancelled["features"]["viewer"]["state"] == "available"


def test_timeout_failure_and_unknown_output(installation):
    path = installation["ANTs"] / "N4BiasFieldCorrection"
    path.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(2)\n")
    report = inspect_dependencies(directories=installation, timeout=0.05, search_common=False)
    ants = next(t for t in report["tools"] if t["family"] == "ANTs")
    assert ants["state"] == "unverified" and "exceeded" in ants["detail"]
    executable(path, "Unrecognized output")
    assert (
        next(t for t in scan(installation)["tools"] if t["family"] == "ANTs")["state"]
        == "unverified"
    )


def test_cli_doctor_optional_vs_required_and_no_overwrite(tmp_path, monkeypatch, capsys):
    from dmri_studio.cli.main import main

    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("FSLDIR", raising=False)
    monkeypatch.delenv("ANTSPATH", raising=False)
    destination = tmp_path / "setup.json"
    assert main(["doctor", "--path-only", "--report-json", str(destination)]) == 0
    assert json.loads(destination.read_text())["features"]["viewer"]["state"] == "available"
    assert main(["doctor", "--path-only", "--require", "bias"]) == 1
    assert main(["doctor", "--path-only", "--report-json", str(destination)]) == 2
    assert "Official MRtrix3" in capsys.readouterr().out
