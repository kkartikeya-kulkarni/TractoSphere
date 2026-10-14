"""Curated upstream installation guidance, using the existing tool inventory.

No downloads, installers, shell commands, PATH edits or MRI execution. Browser
handoff is a GUI action. Version recommendations come from unchanged adapter gates.
Release metadata was inspected 2026-10-14; upstream assets are not bundled.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
from collections.abc import Callable
from pathlib import Path

from .dependencies import TOOLS
from .tool_versions import ANTS_N4_TESTED_RELEASE, EDDY_TESTED_RELEASE, MRTRIX_TESTED_RELEASES

ANTs_RELEASE = "https://github.com/ANTsX/ANTs/releases/tag/v2.6.5"
MRTRIX_RELEASE = "https://github.com/MRtrix3/mrtrix3/releases/tag/3.0.5"
ANTs_GUIDE = "https://github.com/ANTsX/ANTs/wiki/Installing-ANTs-release-binaries"
MRTRIX_GUIDE = "https://mrtrix.readthedocs.io/en/3.0.5/installation/build_from_source.html"
FSL_GUIDE = "https://fsl.fmrib.ox.ac.uk/fsl/docs/install/"
# Exact official-release asset sizes and GitHub-published SHA-256 digests. These
# are distribution integrity evidence, not signatures or scientific validation.
ANTS_ASSETS = {
    "mac-arm": (
        "ants-2.6.5-macos-14-ARM64-clang.zip",
        601442289,
        "e9fe0a360076f500aeecafa5a61fb2990419fd1d92a023b4aa6c104ec09af37f",
    ),
    "mac-intel": (
        "ants-2.6.5-macos-15-intel-X64-clang.zip",
        675586264,
        "743906ff48424bf30c4952b3a627ec54a9addef137e1866f04e19a7e5bed2c26",
    ),
    "ubuntu-22.04": (
        "ants-2.6.5-ubuntu-22.04-X64-gcc.zip",
        754731237,
        "504b6ab94dc09fa414874fdb847f6fced03f143763501e60cd51d247344641cd",
    ),
    "ubuntu-24.04": (
        "ants-2.6.5-ubuntu-24.04-X64-gcc.zip",
        786423846,
        "9f6a2c8881d6c94ea17171c0502e55cb898ad9afd50e4e23fde9a8c0997aa04b",
    ),
    "almalinux-8": (
        "ants-2.6.5-almalinux8-X64-gcc.zip",
        689178077,
        "cea0617ddbdfc4d926f1ce5dc171df5b2627b125d50aa32d0c6ad402b2ec911c",
    ),
    "almalinux-9": (
        "ants-2.6.5-almalinux9-X64-gcc.zip",
        709198473,
        "e7410139964dc493b41be7cef720cc6231ea7456b937505bcf84a424011c3002",
    ),
}


def host_platform() -> dict:
    """Report the running OS/architecture; do not infer WSL execution support."""
    result = dict(
        system=platform.system(),
        architecture=platform.machine(),
        release=platform.release(),
        macos=platform.mac_ver()[0],
        distribution={},
    )
    if result["system"] == "Linux":
        try:
            result["distribution"] = platform.freedesktop_os_release()
        except OSError:
            pass
    return result


def family_state(report: dict | None, family: str, host: dict | None = None) -> dict:
    """Summarize existing scanner results without repeating its version detection."""
    if family not in TOOLS:
        raise ValueError("Unsupported scientific package")
    host = host or host_platform()
    records = [r for r in (report or {}).get("tools", []) if r["family"] == family]
    state = "not_checked"
    if host["system"] not in {"Darwin", "Linux"} or host["architecture"].lower() not in {
        "arm64",
        "aarch64",
        "x86_64",
        "amd64",
    }:
        state = "unsupported_os"
    elif report and report.get("cancelled"):
        state = "interrupted"
    elif records:
        states = {r["state"] for r in records}
        if states == {"missing"}:
            state = "missing"
        elif "unsupported" in states:
            state = "incompatible"
        elif (
            len(records) == len(TOOLS[family])
            and states == {"version_match"}
            and len({str(Path(r["path"]).parent) for r in records}) == 1
        ):
            state = "ready"
        else:
            state = "incomplete"
    return dict(
        state=state,
        missing=[r["name"] for r in records if r["state"] == "missing"],
        problems=[
            f"{r['name']}: {r['detail']}"
            for r in records
            if r["state"] not in {"missing", "version_match"}
        ],
        records=records,
    )


def installation_guide(family: str, *, host: dict | None = None) -> dict:
    """Offline-readable, version-specific manual setup; never recommend latest blindly."""
    if family not in TOOLS:
        raise ValueError("Unsupported scientific package")
    host = host or host_platform()
    system, arch = host["system"], host["architecture"].lower()
    supported = system in {"Darwin", "Linux"} and arch in {"arm64", "aarch64", "x86_64", "amd64"}
    version = {
        "MRtrix3": ", ".join(sorted(MRTRIX_TESTED_RELEASES)),
        "ANTs": ANTS_N4_TESTED_RELEASE,
        "FSL": f"eddy_cpu component {EDDY_TESTED_RELEASE}; other required component records",
    }[family]
    prefix = {"MRtrix3": "mrtrix3-3.0.5", "ANTs": "ants-2.6.5", "FSL": "fsl-eddy-2401.2"}[family]
    result = dict(
        family=family,
        host=host,
        required_version=version,
        destination="~/TractoSphereScientificTools/" + prefix,
        execution_supported=supported,
        automatic_installation=False,
        download_url=None,
        download_bytes=None,
        sha256=None,
        source_date="2026-10-14",
        expanded_bytes=None,
        permissions="User-owned folder: no administrator rights expected. External prerequisite installers may need OS permission; TractoSphere never requests passwords.",
    )
    if family == "MRtrix3":
        result.update(
            guide_url=MRTRIX_GUIDE,
            release_url=MRTRIX_RELEASE,
            license="MRtrix3 MPL-2.0; review upstream notices.",
            method="Pinned official release / separate source or package installation",
            automatic_reason="The 3.0.5 archive has no publisher checksum/signature supplied in its release asset metadata; automatic installation is not enabled.",
        )
        if system == "Darwin" and supported:
            result.update(
                download_url="https://github.com/MRtrix3/mrtrix3/releases/download/3.0.5/mrtrix3-macos-3.0.5.tar.gz",
                download_bytes=65793237,
                expanded_bytes=179551781,
                method="Official 3.0.5 universal macOS archive (Intel + Apple Silicon)",
                steps=[
                    "Download the pinned 3.0.5 archive below through your browser.",
                    "In Finder, double-click the archive. Keep the whole mrtrix3 folder, including bin, lib and share; do not copy just the executables.",
                    "Move that folder into a NEW user-owned destination. Do not replace an existing research installation.",
                    "Choose Locate Existing Installation and select the extracted mrtrix3 folder. Test Installation, then verify and configure.",
                    "If macOS blocks a binary, review its origin and follow upstream security guidance or ask IT. This app never removes quarantine or changes security policy.",
                ],
            )
        else:
            result["steps"] = [
                "Open the pinned 3.0.5 release and platform-specific upstream guide.",
                "Linux: use an isolated version-pinned official package/source installation; distribution packages may supply a different version. Ask IT if a compiler/package manager is needed.",
                "Windows: upstream uses MSYS2. TractoSphere's Windows scientific adapters are not verified; native/WSL bridge execution is unavailable.",
                "Keep the complete install tree. After supported local installation, select its root/bin folder and test it. N4 additionally needs the wrapper sources at the adapter-required layout.",
            ]
    elif family == "ANTs":
        result.update(
            guide_url=ANTs_GUIDE,
            release_url=ANTs_RELEASE,
            license="ANTs Apache-2.0; review upstream notices.",
            method="Pinned 2.6.5 release archive / upstream source build",
            automatic_reason="Archives are unsigned on macOS and can require OS security approval/runtime prerequisites. No unattended installation or quarantine bypass is enabled.",
        )
        key = None
        # Conservative binary recommendation for the OS used by the upstream
        # build; do not infer older deployment targets from the asset filename.
        mac = str(host.get("macos", "")).split(".")[0]
        if system == "Darwin" and mac.isdigit():
            if arch in {"arm64", "aarch64"} and int(mac) >= 14:
                key = "mac-arm"
            if arch in {"x86_64", "amd64"} and int(mac) >= 15:
                key = "mac-intel"
        if system == "Linux" and arch in {"x86_64", "amd64"}:
            distro = host.get("distribution", {})
            key = distro.get("ID", "") + "-" + distro.get("VERSION_ID", "")
        if key in ANTS_ASSETS:
            name, size, checksum = ANTS_ASSETS[key]
            result.update(
                download_url="https://github.com/ANTsX/ANTs/releases/download/v2.6.5/" + name,
                download_bytes=size,
                sha256=checksum,
                method="Official 2.6.5 archive for this upstream build platform",
            )
        result["steps"] = [
            "Use the matching pinned 2.6.5 archive when offered. Other OS releases/architectures need upstream build guidance; no binary compatibility is inferred.",
            "Download in your browser. Verify the downloaded archive here against the official release's SHA-256 before extraction when offered.",
            "Extract the complete ants-2.6.5 folder into a NEW user-owned location, preserving bin and lib. Choose that extracted folder under Locate Existing Installation.",
            "Test Installation, then verify and configure; no global PATH edits are needed in TractoSphere.",
            "macOS: unsigned binaries may be blocked. Follow the upstream security section or ask IT after verifying the source. This app never removes quarantine. Windows binaries also need runtime/interpreter prerequisites; Windows preprocessing remains unavailable here.",
        ]
    else:
        page = {"Darwin": "macos.html", "Linux": "linux.html", "Windows": "windows.html"}.get(
            system, "index.html"
        )
        result.update(
            guide_url=FSL_GUIDE + page,
            release_url=FSL_GUIDE + page,
            license="FSL noncommercial terms apply separately; review the official license and institutional requirements.",
            method="Official FSL installer / separately version-pinned FSL conda environment",
            automatic_reason="The installer resolves a multi-package environment and may change shell configuration. An unreviewed live remote script is not executed by TractoSphere.",
            steps=[
                f"The correction adapter requires eddy_cpu {EDDY_TESTED_RELEASE}, not just FSL 6.x. Confirm the component offered before proceeding; the default/latest installer is not guaranteed compatible.",
                "Use the official platform guide. FSL installation currently requires Terminal or IT assistance; TractoSphere does not automate its installer. For custom versions consult the official installer options or FSL conda guide.",
                "Choose a NEW user-owned location; do not reuse/overwrite your working FSL directory. Keep conda-meta component ownership records.",
                "Return here: Locate Existing Installation → select the FSL folder → Test Installation → verify and configure.",
                "Windows: FSL belongs inside WSL. Installing WSL may need OS administrator permission. This application has no Windows-to-WSL execution/path/output bridge; do not select WSL Linux executables as native tools.",
            ],
        )
    result["platform_note"] = (
        "macOS Apple Silicon setup verified locally; independent Mac pending."
        if system == "Darwin" and arch in {"arm64", "aarch64"}
        else "Experimental platform guidance; native scientific execution not independently verified."
    )
    if not supported:
        result["platform_note"] = (
            "Unsupported scientific execution on this host. Viewer remains available. Windows-to-WSL execution is not implemented."
            if system == "Windows"
            else "Unsupported OS/architecture for the current scientific adapters. Viewer-only use remains available."
        )
        result["download_url"] = None
    return result


def destination_check(destination: str | Path, guide: dict) -> dict:
    """Check real filesystem space/permissions without creating or changing folders."""
    path = Path(destination).expanduser().absolute()
    parent = path
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    result = dict(
        destination=str(path),
        existing=path.exists(),
        free_bytes=None,
        writable=False,
        sufficient=None,
        detail="",
    )
    try:
        if not parent.is_dir():
            raise OSError("Selected parent is not a directory")
        result["free_bytes"] = shutil.disk_usage(parent).free
        result["writable"] = os.access(parent, os.W_OK)
        lower = guide.get("download_bytes")
        if lower is not None:
            lower += guide.get("expanded_bytes") or 0
            result["sufficient"] = result["free_bytes"] >= lower
        result["detail"] = (
            "Existing location: locate/test it, or choose a NEW destination; nothing will be overwritten."
            if result["existing"]
            else "Suggested destination only; TractoSphere does not create it."
        )
        if not result["writable"]:
            result["detail"] += " Parent is not writable; choose a user-owned location."
        if result["sufficient"] is False:
            result["detail"] += " Insufficient free space for known download/extraction bytes."
        if guide.get("expanded_bytes") is None:
            result["detail"] += (
                " Expanded/install size is unknown; reported free space is not a guarantee of sufficient space."
            )
    except OSError as exc:
        result["detail"] = "Could not inspect destination: " + str(exc)
    return result


def verify_archive(
    path: Path, guide: dict, *, cancelled: Callable[[], bool] = lambda: False
) -> dict:
    """Read/hash a selected archive only; no extraction or installer execution."""
    expected = guide.get("sha256")
    if not expected:
        raise ValueError(
            "No official release checksum is available for this selection; no authenticity claim can be made."
        )
    if path.is_symlink() or not path.is_file():
        raise ValueError("Choose a regular downloaded archive, not a symlink or folder.")
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            if cancelled():
                raise InterruptedError("Archive verification interrupted; no settings changed.")
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Archive changed during verification; download again and recheck.")
    if digest.hexdigest() != expected or before.st_size != guide["download_bytes"]:
        raise ValueError(
            "Official archive size/SHA-256 mismatch. Do not extract or execute it; download the exact pinned asset again."
        )
    return dict(
        path=str(path),
        sha256=digest.hexdigest(),
        bytes=before.st_size,
        source=guide["download_url"],
        status="release_digest_matched",
        scientific_acceptance=False,
    )
