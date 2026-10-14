"""Fail closed on private data, unapproved binary assets and unsafe release archives.

Run: python scripts/audit_release_contents.py --source . --artifacts dist
Uses tracked source files and reads archives in memory with strict limits; never
extracts them or prints matched text. This is a distribution guard, not a substitute
for whole-history Gitleaks, manual image review or GitHub retained-object checks.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

MAX_MEMBER = 32 * 1024**2
MAX_TOTAL = 256 * 1024**2
MAX_FILES = 10000
MAX_DEPTH = 4
SCIENCE = (
    ".nii",
    ".nii.gz",
    ".mif",
    ".mif.gz",
    ".mih",
    ".mgh",
    ".mgz",
    ".tck",
    ".trk",
    ".trx",
    ".tt.gz",
    ".src",
    ".sz",
    ".fib",
    ".fz",
    ".pam5",
    ".h5",
    ".npz",
    ".npy",
    ".dcm",
    ".dicom",
    ".hdr",
    ".img",
    ".bval",
    ".bvec",
    ".tsf",
)
BINARY = (".png", ".jpg", ".jpeg", ".ttf", ".otf", ".woff", ".woff2", ".ico", ".icns", ".svg")
PRIVATE_DIRS = {
    ".git",
    ".venv",
    "venv",
    ".conda",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "data",
    "outputs",
    "results",
    "exports",
    "workspaces",
    "release-private",
    "research-private",
    "review-bundles",
    ".ssh",
    ".aws",
    ".azure",
    ".gcloud",
}
RULES = {
    "machine_home_path": re.compile(r"/(?:Users|home)/[A-Za-z][A-Za-z0-9._-]{1,}/"),
    "restricted_research_identity": re.compile(
        r"\b(?:HCD\d{5,}|HCP\d{5,}|LEMON"
        r"_sub|sub-01000[2-7]|[0-9]{2}KK[0-9]{5})\b"
    ),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "github_credential": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"
    ),
    "provider_credential": re.compile(
        r"\b(?:sk-(?:proj-|svcacct-)[A-Za-z0-9_-]{24,}|ya29\.[A-Za-z0-9_-]{30,}|AKIA[A-Z0-9]{16})\b"
    ),
}


class Audit:
    """Bounded, read-only audit; findings contain codes and locations, never values."""

    def __init__(self, approved: dict):
        self.approved = approved
        self.findings: list[dict] = []
        self.files = 0
        self.bytes = 0
        self.manifest: list[dict] = []

    def flag(self, location: str, code: str) -> None:
        self.findings.append({"location": location, "code": code})

    @staticmethod
    def canonical(name: str) -> str:
        """Map a source-distribution prefix or wheel package path to source paths."""
        parts = list(PurePosixPath(name).parts)
        if len(parts) > 1 and re.fullmatch(r"dmri[_-]studio-\d[^/]*", parts[0]):
            parts = parts[1:]
        if parts and parts[0] == "dmri_studio":
            parts.insert(0, "src")
        return "/".join(parts)

    def member(
        self, name: str, data: bytes, *, location: str | None = None, depth: int = 0
    ) -> None:
        """Inspect contents without filesystem extraction or implicit external access."""
        location = location or name
        self.files += 1
        self.bytes += len(data)
        if (
            self.files > MAX_FILES
            or self.bytes > MAX_TOTAL
            or len(data) > MAX_MEMBER
            or depth > MAX_DEPTH
        ):
            self.flag(location, "audit_limit_exceeded")
            return
        if (
            "\\" in name
            or PurePosixPath(name).is_absolute()
            or ".." in PurePosixPath(name).parts
            or re.match(r"^[A-Za-z]:", name)
        ):
            self.flag(location, "unsafe_archive_path")
            return
        normalized = self.canonical(name)
        parts = PurePosixPath(normalized).parts
        lower = normalized.lower()
        basename = parts[-1] if parts else ""
        digest = hashlib.sha256(data).hexdigest()
        self.manifest.append(dict(location=location, bytes=len(data), sha256=digest))
        if any(p.lower() in PRIVATE_DIRS for p in parts):
            self.flag(location, "private_or_generated_directory")
        if lower.endswith(SCIENCE) or basename.lower() in {
            "bvals",
            "bvecs",
            "dataset_description.json",
            "participants.tsv",
        }:
            self.flag(location, "scientific_data_file")
        if lower.endswith((".html", ".pdf", ".log", ".sqlite", ".db")):
            self.flag(location, "unreviewed_report_or_runtime_state")
        if (
            basename.startswith(".env")
            or basename in {".netrc", ".pypirc", ".npmrc", "id_rsa", "id_ed25519"}
            or re.match(r"(?:credentials|tokens).*\.json$", basename, re.I)
        ):
            self.flag(location, "credential_or_private_configuration")
        if data.startswith(
            (b"\x7fELF", b"MZ", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe")
        ):
            self.flag(location, "bundled_executable")
        # NIfTI bytes are detected even when renamed to an innocuous extension.
        if len(data) >= 348 and (
            data[344:348] in (b"n+1\0", b"ni1\0")
            or data[4:12] in (b"n+2\0\r\n\x1a\n", b"ni2\0\r\n\x1a\n")
        ):
            self.flag(location, "nifti_content")
        if data.startswith(b"TRACK\0") or data.startswith(b"mrtrix tracks\n"):
            self.flag(location, "tractography_content")
        try:
            if zipfile.is_zipfile(io.BytesIO(data)):
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    if len(archive.infolist()) > MAX_FILES:
                        self.flag(location, "audit_limit_exceeded")
                        return
                    seen = set()
                    for info in archive.infolist():
                        if info.is_dir():
                            continue
                        if info.filename in seen:
                            self.flag(location, "duplicate_archive_member")
                        seen.add(info.filename)
                        if (info.external_attr >> 16) & 0o170000 == 0o120000:
                            self.flag(location + "!" + info.filename, "archive_link")
                            continue
                        if info.file_size > MAX_MEMBER or self.bytes + info.file_size > MAX_TOTAL:
                            self.flag(location, "audit_limit_exceeded")
                            break
                        with archive.open(info) as stream:
                            child = stream.read(MAX_MEMBER + 1)
                        self.member(
                            # ZipInfo.filename normalizes backslashes on Windows.
                            # Inspect the original archive spelling so malformed
                            # paths cannot become apparently safe approved assets.
                            info.orig_filename,
                            child,
                            location=location + "!" + info.orig_filename,
                            depth=depth + 1,
                        )
                return
            if lower.endswith((".tar.gz", ".tgz", ".tar")):
                with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
                    seen = set()
                    for info in archive:
                        if self.files >= MAX_FILES:
                            self.flag(location, "audit_limit_exceeded")
                            break
                        if info.isdir():
                            continue
                        if info.name in seen:
                            self.flag(location, "duplicate_archive_member")
                        seen.add(info.name)
                        if not info.isfile():
                            self.flag(location + "!" + info.name, "archive_link_or_special_file")
                            continue
                        if info.size > MAX_MEMBER or self.bytes + info.size > MAX_TOTAL:
                            self.flag(location, "audit_limit_exceeded")
                            break
                        self.member(
                            info.name,
                            archive.extractfile(info).read(MAX_MEMBER + 1),
                            location=location + "!" + info.name,
                            depth=depth + 1,
                        )
                return
            if data.startswith(b"\x1f\x8b"):
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                    child = stream.read(MAX_MEMBER + 1)
                self.member(
                    name.removesuffix(".gz"), child, location=location + "!gzip", depth=depth + 1
                )
                return
        except (OSError, ValueError, EOFError, zipfile.BadZipFile, tarfile.TarError):
            self.flag(location, "unreadable_archive")
            return
        if lower.endswith(BINARY):
            entry = self.approved.get(normalized)
            if (
                not entry
                or entry.get("sha256") != digest
                or not entry.get("license")
                or not entry.get("source")
            ):
                self.flag(location, "unapproved_binary_asset")
            return
        if b"\0" in data[:8192]:
            self.flag(location, "unreviewed_binary")
            return
        text = data.decode("utf-8", errors="replace")
        for rule, regex in RULES.items():
            if regex.search(text):
                self.flag(location, rule)
        if text.startswith("version https://git-lfs.github.com/spec/v1"):
            self.flag(location, "lfs_pointer_requires_review")

    def result(self) -> dict:
        return dict(
            status="failed" if self.findings else "passed",
            files=self.files,
            bytes_inspected=self.bytes,
            findings=self.findings,
            manifest=self.manifest,
            scope="Distribution guard only; whole-history/remote/manual review remains required",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    approved = json.loads((source / "release-assets.json").read_text(encoding="utf-8"))["assets"]
    audit = Audit(approved)
    names = (
        subprocess.check_output(["git", "-C", str(source), "ls-files", "-z"])
        .decode("utf-8")
        .split("\0")
    )
    for name in filter(None, names):
        path = source / name
        if path.is_symlink():
            audit.flag(name, "source_symlink")
        elif not path.is_file():
            audit.flag(name, "tracked_file_missing")
        elif path.stat().st_size > MAX_MEMBER:
            audit.flag(name, "audit_limit_exceeded")
        else:
            audit.member(name, path.read_bytes())
    if args.artifacts:
        for path in sorted(args.artifacts.iterdir()):
            if path.is_symlink() or not path.is_file():
                audit.flag(path.name, "unexpected_artifact_entry")
            elif path.stat().st_size > MAX_MEMBER:
                audit.flag(path.name, "audit_limit_exceeded")
            else:
                audit.member(path.name, path.read_bytes())
    result = audit.result()
    if args.report:
        with args.report.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != "manifest"}, indent=2))
    return 1 if audit.findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
