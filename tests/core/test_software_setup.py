"""Guidance policy, filesystem and archive checks never install external software."""

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dmri_studio.core import software_setup as setup
from dmri_studio.core.dependencies import inspect_dependencies


def host(system="Darwin", arch="arm64", macos="26.0", distro=None):
    return dict(system=system, architecture=arch, macos=macos, distribution=distro or {})


@pytest.mark.parametrize(
    "target,key",
    [
        (host(), "mac-arm"),
        (host(arch="x86_64", macos="15.0"), "mac-intel"),
        (host("Linux", "x86_64", distro={"ID": "ubuntu", "VERSION_ID": "22.04"}), "ubuntu-22.04"),
        (host("Linux", "x86_64", distro={"ID": "almalinux", "VERSION_ID": "9"}), "almalinux-9"),
    ],
)
def test_exact_ANTs_asset_for_verified_platform_selection(target, key):
    guide = setup.installation_guide("ANTs", host=target)
    asset, size, digest = setup.ANTS_ASSETS[key]
    assert guide["download_url"].endswith(asset)
    assert guide["download_bytes"] == size and guide["sha256"] == digest
    assert guide["required_version"] == "2.6.5" and not guide["automatic_installation"]


@pytest.mark.parametrize(
    "target", [host("Windows", "AMD64"), host("FreeBSD", "x86_64"), host("Darwin", "riscv64")]
)
def test_unsupported_execution_has_no_automatic_download(target):
    for family in ("MRtrix3", "FSL", "ANTs"):
        guide = setup.installation_guide(family, host=target)
        assert not guide["execution_supported"] and guide["download_url"] is None
        assert setup.family_state(None, family, target)["state"] == "unsupported_os"


def test_unknown_linux_and_older_mac_do_not_guess_binary():
    for target in (
        host("Linux", "aarch64", distro={"ID": "ubuntu", "VERSION_ID": "24.04"}),
        host("Linux", "x86_64", distro={"ID": "debian", "VERSION_ID": "12"}),
        host(macos="13.0"),
    ):
        g = setup.installation_guide("ANTs", host=target)
        assert g["execution_supported"] and g["download_url"] is None
        assert "upstream" in g["steps"][0]
    g = setup.installation_guide("MRtrix3", host=host())
    assert "3.0.5" in g["download_url"] and "universal" in g["method"] and g["sha256"] is None
    fsl = setup.installation_guide("FSL", host=host("Windows", "AMD64"))
    assert fsl["guide_url"].endswith("windows.html") and "2401.2" in fsl["required_version"]
    assert fsl["download_bytes"] is None


def test_destination_space_permissions_and_no_folder_creation(tmp_path, monkeypatch):
    guide = setup.installation_guide("ANTs", host=host())
    destination = tmp_path / "new-tools"
    monkeypatch.setattr(setup.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    monkeypatch.setattr(setup.os, "access", lambda *_: False)
    r = setup.destination_check(destination, guide)
    assert r["sufficient"] is False and r["writable"] is False and not destination.exists()
    assert "Insufficient" in r["detail"] and "Expanded/install size is unknown" in r["detail"]
    monkeypatch.setattr(
        setup.shutil, "disk_usage", lambda _: (_ for _ in ()).throw(PermissionError("denied"))
    )
    assert "Could not inspect" in setup.destination_check(destination, guide)["detail"]


def test_archive_checksum_bad_interrupted_and_not_official(tmp_path):
    payload = b"Synthetic installer archive, never executed."
    path = tmp_path / "fixture.zip"
    path.write_bytes(payload)
    guide = dict(
        sha256=hashlib.sha256(payload).hexdigest(),
        download_bytes=len(payload),
        download_url="https://github.com/ANTsX/ANTs/releases/fixture",
    )
    before = path.read_bytes()
    assert setup.verify_archive(path, guide)["status"] == "release_digest_matched"
    with pytest.raises(InterruptedError):
        setup.verify_archive(path, guide, cancelled=lambda: True)
    with pytest.raises(ValueError, match="mismatch"):
        setup.verify_archive(path, {**guide, "sha256": "0" * 64})
    with pytest.raises(ValueError, match="No official"):
        setup.verify_archive(path, {**guide, "sha256": None})
    assert path.read_bytes() == before


def test_windows_FSL_not_queried_or_ready(tmp_path, monkeypatch):
    binary = tmp_path / "bin/topup"
    binary.parent.mkdir()
    binary.write_text("unexecuted Linux/WSL file")
    binary.chmod(0o755)
    report = inspect_dependencies(
        directories={"FSL": binary.parent},
        environ={"PATH": ""},
        search_common=False,
        system="Windows",
    )
    row = next(r for r in report["tools"] if r["name"] == "topup")
    assert "Windows-to-WSL" in row["detail"] and row["state"] == "unverified"
    assert report["features"]["viewer"]["state"] == "available"
    assert report["features"]["correction"]["state"] == "needs_setup"


def test_cli_guidance_uses_same_inventory(tmp_path, monkeypatch, capsys):
    from dmri_studio.cli.main import main

    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("FSLDIR", raising=False)
    monkeypatch.delenv("ANTSPATH", raising=False)
    report = tmp_path / "setup.json"
    assert (
        main(
            [
                "doctor",
                "--path-only",
                "--setup",
                "FSL",
                "--destination",
                str(tmp_path / "new"),
                "--report-json",
                str(report),
            ]
        )
        == 0
    )
    r = json.loads(report.read_text())
    assert r["installation_guidance"]["automatic_installation"] is False
    assert "2401.2" in r["installation_guidance"]["required_version"]
    assert not (tmp_path / "new").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX Finder version-executable fixture")
def test_Finder_discovery_of_versioned_user_installation(tmp_path, monkeypatch):
    import sys

    from dmri_studio.core import dependencies

    folder = tmp_path / "TractoSphereScientificTools/ants-2.6.5/ants-2.6.5/bin"
    folder.mkdir(parents=True)
    binary = folder / "N4BiasFieldCorrection"
    binary.write_text(f'#!{sys.executable}\nprint("ANTs Version: 2.6.5")\n')
    binary.chmod(0o755)
    original = Path.is_file
    monkeypatch.setattr(
        Path, "is_file", lambda p: original(p) if str(p).startswith(str(tmp_path)) else False
    )
    r = dependencies.inspect_dependencies(environ={"PATH": ""}, home=tmp_path, search_common=True)
    row = next(t for t in r["tools"] if t["family"] == "ANTs")
    assert (
        row["path"] == str(binary)
        and row["version"] == "2.6.5"
        and row["discovery"] == "common installation"
    )
