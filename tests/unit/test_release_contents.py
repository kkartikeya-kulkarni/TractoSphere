"""Adversarial distribution tests use synthetic bytes, never research arrays."""

import gzip
import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "release_audit", Path(__file__).resolve().parents[2] / "scripts/audit_release_contents.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def codes(audit):
    return {item["code"] for item in audit.findings}


@pytest.mark.parametrize("name", ["data.nii.gz", "tracks.tck", "directions.bvec"])
def test_scientific_files_rejected(name):
    audit = MODULE.Audit({})
    audit.member(name, b"example")
    assert "scientific_data_file" in codes(audit)


def test_renamed_compressed_nifti_rejected():
    data = bytearray(348)
    data[344:348] = b"n+1\0"
    audit = MODULE.Audit({})
    audit.member("innocent.txt", gzip.compress(data))
    assert "nifti_content" in codes(audit)


@pytest.mark.parametrize("name", ["../outside.txt", "/absolute.txt", "C:/outside.txt", "a\\b"])
def test_archive_paths_rejected(name):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(name, "example")
    audit = MODULE.Audit({})
    audit.member("bundle.zip", stream.getvalue())
    assert "unsafe_archive_path" in codes(audit)


def test_tar_link_never_followed():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/outside"
        archive.addfile(info)
    audit = MODULE.Audit({})
    audit.member("bundle.tar", stream.getvalue())
    assert "archive_link_or_special_file" in codes(audit)


def test_asset_requires_approved_digest_and_provenance():
    data = b"synthetic image bytes"
    approved = {
        "figure.png": {
            "sha256": hashlib.sha256(data).hexdigest(),
            "license": "CC0",
            "source": "test fixture",
        }
    }
    audit = MODULE.Audit(approved)
    audit.member("figure.png", data)
    assert not audit.findings
    audit.member("figure.png", data + b"changed")
    assert "unapproved_binary_asset" in codes(audit)


def test_secret_value_never_in_findings():
    token = "gh" + "p_" + "A" * 36
    audit = MODULE.Audit({})
    audit.member("settings.txt", token.encode())
    assert "github_credential" in codes(audit)
    assert token not in json.dumps(audit.result())


def test_bounded_gzip_expansion(monkeypatch):
    monkeypatch.setattr(MODULE, "MAX_MEMBER", 100)
    audit = MODULE.Audit({})
    audit.member("small.gz", gzip.compress(b"x" * 10000))
    assert "audit_limit_exceeded" in codes(audit)


def test_source_and_wheel_paths_share_asset_approval():
    assert MODULE.Audit.canonical("dmri_studio/assets/a.png") == "src/dmri_studio/assets/a.png"
    assert MODULE.Audit.canonical("dmri_studio-0.1.0a0/docs/a.png") == "docs/a.png"


def test_plain_source_allowed():
    audit = MODULE.Audit({})
    audit.member("src/example.py", b"def example():\n    return 42\n")
    assert not audit.findings


def test_named_sdist_is_inspected_as_tar():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        info = tarfile.TarInfo("dmri_studio-0.1.0a0/README.md")
        data = b"public source"
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    audit = MODULE.Audit({})
    audit.member("dmri_studio-0.1.0a0.tar.gz", stream.getvalue())
    assert not audit.findings
    assert audit.files == 2
