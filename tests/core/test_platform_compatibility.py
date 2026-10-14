"""Portable filesystem/process contracts; simulated Windows is not OS validation."""

import json
import os
import sys
from types import SimpleNamespace

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core import dependencies, resources
from dmri_studio.core.process import resource_snapshot, run_command
from dmri_studio.core.scene import ImageVolume, TractGroup


@pytest.mark.parametrize(
    "family,name", [("MRtrix3", "mrconvert"), ("ANTs", "N4BiasFieldCorrection")]
)
@pytest.mark.parametrize("selected_root", [True, False])
def test_windows_explicit_executable_discovery(tmp_path, monkeypatch, family, name, selected_root):
    prefix = tmp_path / "Scientific tools β" / family
    binary = prefix / "bin" / (name + ".exe")
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"Not a real executable; fixed mocked version response")
    binary.chmod(0o755)
    calls = []

    def query(path, argument, timeout):
        calls.append((path, argument))
        return (f"== {name} 3.0.5 ==" if family == "MRtrix3" else "ANTs Version: 2.6.5"), None

    monkeypatch.setattr(dependencies, "_query", query)
    report = dependencies.inspect_dependencies(
        directories={family: prefix if selected_root else binary.parent},
        environ={"PATH": ""},
        search_common=False,
        system="Windows",
    )
    row = next(r for r in report["tools"] if r["name"] == name)
    assert row["path"] == str(binary) and row["state"] == "version_match"
    assert calls == [(binary, "-version" if family == "MRtrix3" else "--version")]
    assert all(r["state"] == "needs_setup" for k, r in report["features"].items() if k != "viewer")


def test_windows_environment_discovery_and_explicit_missing_no_fallback(tmp_path, monkeypatch):
    prefix = tmp_path / "ANTs"
    prefix.mkdir()
    binary = prefix / "N4BiasFieldCorrection.exe"
    binary.write_bytes(b"mock")
    monkeypatch.setattr(dependencies, "_query", lambda *_: ("ANTs Version: 2.6.5", None))
    opts = dict(
        environ={"PATH": "", "ANTSPATH": str(prefix)}, search_common=False, system="Windows"
    )
    r = dependencies.inspect_dependencies(**opts)
    assert next(x for x in r["tools"] if x["family"] == "ANTs")["discovery"] == "ANTSPATH"
    r = dependencies.inspect_dependencies(directories={"ANTs": tmp_path / "missing"}, **opts)
    assert next(x for x in r["tools"] if x["family"] == "ANTs")["state"] == "missing"


def test_windows_fsl_never_queried_even_exe(tmp_path, monkeypatch):
    binary = tmp_path / "topup.exe"
    binary.write_bytes(b"not a native supported FSL")
    monkeypatch.setattr(dependencies, "_query", lambda *_: pytest.fail("FSL must not be invoked"))
    r = dependencies.inspect_dependencies(
        directories={"FSL": tmp_path}, environ={"PATH": ""}, search_common=False, system="Windows"
    )
    row = next(x for x in r["tools"] if x["name"] == "topup")
    assert row["state"] == "unverified" and "Windows-to-WSL" in row["detail"]


def test_windows_lifetime_cpu_not_uninitialized_zero(monkeypatch):
    class Process:
        def __init__(self, pid):
            assert pid == 123

        def oneshot(self):
            from contextlib import nullcontext

            return nullcontext()

        def cpu_times(self):
            return SimpleNamespace(user=2, system=1)

        def create_time(self):
            return 98

        def memory_info(self):
            return SimpleNamespace(rss=1234567)

    monkeypatch.setattr(resources.psutil, "Process", Process)
    monkeypatch.setattr("time.time", lambda: 100)
    r = resources.windows_process_snapshot(123)
    assert r["cpu_percent"] == 150 and r["rss_bytes"] == 1234567
    assert "Lifetime-average" in r["cpu_semantics"]


def test_real_process_resources_and_missing_process():
    r = resource_snapshot(os.getpid())
    assert r is not None and r["pid"] == os.getpid() and r["rss_bytes"] > 0
    assert resources.peak_self_rss_bytes() > 0
    assert resources.windows_process_snapshot(99999999) is None


def test_argv_unicode_space_directory_and_no_overwrite(tmp_path):
    folder = tmp_path / "study β with spaces"
    folder.mkdir()
    log = folder / "tool log.txt"
    argument = "literal $HOME; & filename β"
    r = run_command(
        [
            sys.executable,
            "-c",
            "import sys, time; sys.stdout.buffer.write(sys.argv[1].encode('utf-8')); "
            "sys.stdout.buffer.flush(); time.sleep(0.25)",
            argument,
        ],
        cwd=folder,
        log=log,
        environment=os.environ,
        cancelled=lambda: False,
        progress=lambda _: None,
    )
    assert log.read_text(encoding="utf-8").strip() == argument
    assert r["return_code"] == 0 and r["argv"][-1] == argument
    assert (
        json.loads(log.with_suffix(".resources.jsonl").read_text().splitlines()[0])["rss_bytes"] > 0
    )
    with pytest.raises(FileExistsError):
        run_command(
            [sys.executable, "-c", "raise SystemExit(99)"],
            cwd=folder,
            log=log,
            environment=os.environ,
            cancelled=lambda: False,
            progress=lambda _: None,
        )


@pytest.mark.parametrize("extension", ["tck", "trk"])
def test_native_paths_image_tract_export_roundtrip(tmp_path, extension):
    folder = tmp_path / "MRI β with spaces"
    folder.mkdir()
    image = folder / "reference.nii.gz"
    affine = np.diag([-2, 3, 4, 1])
    affine[0, 3] = 12
    nii = nib.Nifti1Image(np.ones((8, 9, 10, 3), np.float32), affine)
    nii.header.set_xyzt_units("mm")
    nib.save(nii, image)
    reference = ImageVolume(image)
    tract = folder / ("source." + extension)
    expected = np.array([[0, 3, 4], [2, 6, 8]], np.float32)
    nib.streamlines.save(nib.streamlines.Tractogram([expected], affine_to_rasmm=np.eye(4)), tract)
    group = TractGroup.load(tract)
    target = folder / ("export β." + extension)
    group.export(target, reference)
    assert np.allclose(nib.streamlines.load(target).tractogram.streamlines[0], expected)
    with pytest.raises(FileExistsError):
        group.export(target, reference)
    assert np.allclose(nib.load(image).affine, affine)


@pytest.mark.parametrize("extension", ["tsv", "json", "txt"])
def test_utf8_atlas_region_names_with_non_utf8_locale(tmp_path, monkeypatch, extension):
    """Require an explicit Unicode decoder rather than inheriting Windows ACP."""
    from pathlib import Path

    from dmri_studio.core.atlas import label_table

    name = "Région β"
    path = tmp_path / ("atlas." + extension)
    payload = {
        "tsv": f"index\tname\n7\t{name}\n",
        "json": json.dumps({"Labels": {"7": name}}, ensure_ascii=False),
        "txt": "7 Région_β 255 100 50\n",
    }[extension]
    path.write_text(payload, encoding="utf-8")
    original = Path.open

    def open_with_cp1252_default(
        self, mode="r", buffering=-1, encoding=None, errors=None, newline=None
    ):
        if "b" not in mode and encoding is None:
            encoding = "cp1252"
        return original(self, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", open_with_cp1252_default)
    names, _, _ = label_table(path)
    assert names[7] == ("Région_β" if extension == "txt" else name)


def test_windows_non_native_launcher_not_executed(tmp_path, monkeypatch):
    binary = tmp_path / "mrconvert"
    binary.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr(dependencies, "_query", lambda *_: pytest.fail("Not a native exe"))
    report = dependencies.inspect_dependencies(
        directories={"MRtrix3": tmp_path},
        environ={"PATH": ""},
        search_common=False,
        system="Windows",
    )
    row = next(r for r in report["tools"] if r["name"] == "mrconvert")
    assert row["state"] == "unverified" and "Not a native" in row["detail"]
