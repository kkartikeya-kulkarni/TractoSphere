"""Read-only scientific-tool inventory; never install software or run MRI commands.

FSL versions come from component ownership records, never `eddy --version` or
TOPUP without inputs. Execution still independently binds hashes and all required
sources. Setup version matching is not scientific acceptance or plan approval.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from .tool_versions import ANTS_N4_TESTED_RELEASE, EDDY_TESTED_RELEASE, MRTRIX_TESTED_RELEASES

INSTALL_LINKS = {
    "MRtrix3": "https://www.mrtrix.org/download/",
    "FSL": "https://fsl.fmrib.ox.ac.uk/fsl/docs/install/index.html",
    "ANTs": "https://github.com/ANTsX/ANTs/wiki/Installing-ANTs-release-binaries",
}
TOOLS = {
    "MRtrix3": (
        "mrconvert",
        "dwi2mask",
        "dwidenoise",
        "mrdegibbs",
        "mrinfo",
        "dwibiascorrect",
        "dwiextract",
        "mrmath",
        "mrcalc",
        "mrdump",
    ),
    "FSL": (
        "topup",
        "eddy_cpu",
        "bet2",
        "bet",
        "fast",
        "flirt",
        "fslmaths",
        "eddy_combine",
        "fslstats",
        "fslval",
        "fslhd",
        "remove_ext",
        "imtest",
    ),
    "ANTs": ("N4BiasFieldCorrection",),
}


def fsl_root(directory: str | Path | None = None, environ: Mapping | None = None) -> Path | None:
    """Resolve an explicit FSL root/bin without ever interpreting empty as CWD."""
    environment = os.environ if environ is None else environ
    selected = str(directory) if directory else str(environment.get("FSLDIR", ""))
    if not selected.strip():
        return None
    path = Path(selected).expanduser()
    return path.parent if path.name == "bin" else path


FEATURES = {
    "convert_mask": ("Conversion / brain masking", {"MRtrix3": ("mrconvert", "dwi2mask")}),
    "denoise": ("MP-PCA denoising", {"MRtrix3": ("mrconvert", "dwi2mask", "dwidenoise")}),
    "gibbs": ("Gibbs correction", {"MRtrix3": ("mrconvert", "dwi2mask", "mrdegibbs", "mrinfo")}),
    "correction": (
        "TOPUP / EDDY",
        {"MRtrix3": ("mrconvert", "dwi2mask"), "FSL": ("topup", "eddy_cpu", "bet2")},
    ),
    "bias": (
        "N4 bias correction",
        {
            "MRtrix3": (
                "mrconvert",
                "dwi2mask",
                "dwibiascorrect",
                "dwiextract",
                "mrmath",
                "mrcalc",
                "mrdump",
                "mrinfo",
            ),
            "ANTs": ("N4BiasFieldCorrection",),
        },
    ),
    "anatomy": ("Prepared-T1 / MP2RAGE tissue preparation", {"FSL": ("fast",)}),
    "anatomy_raw": (
        "Raw T1 extraction / tissue preparation",
        {"FSL": ("fast", "bet", "bet2", "fslstats", "fslval", "fslhd", "remove_ext", "imtest")},
    ),
    "alignment": ("Rigid / BBR alignment", {"FSL": ("flirt", "fast")}),
    "combination": (
        "Optional matched AP/PA combination",
        {"MRtrix3": ("mrconvert", "mrinfo"), "FSL": ("eddy_combine", "fslmaths")},
    ),
}


def _directory(value: str | Path, name: str, system: str | None = None) -> Path:
    """Accept either installation root or bin directory without modifying either."""
    folder = Path(value).expanduser().absolute()
    return folder / "bin" if _candidate(folder / "bin", name, system) else folder


def _candidate(folder: Path, name: str, system: str | None) -> Path | None:
    """Recognize native .exe files without treating batch files as executables.

    Extensionless paths remain discoverable (e.g. a WSL folder), but the scan
    explicitly refuses to query WSL/FSL Linux tools as native Windows programs.
    """
    names = (name + ".exe", name) if (system or platform.system()) == "Windows" else (name,)
    return next((folder / value for value in names if (folder / value).is_file()), None)


def _locate(
    name: str,
    family: str,
    directories: Mapping,
    environ: Mapping,
    common: bool,
    home: Path,
    system: str,
) -> tuple[Path | None, str]:
    """Explicit selection is authoritative; do not silently fall back if incomplete."""
    if directories.get(family):
        return _candidate(
            _directory(directories[family], name, system), name, system
        ), "selected folder"
    candidates = []
    if family == "FSL" and environ.get("FSLDIR"):
        candidates.append((_candidate(Path(environ["FSLDIR"]) / "bin", name, system), "FSLDIR"))
    if family == "ANTs" and environ.get("ANTSPATH"):
        candidates.append(
            (_candidate(_directory(environ["ANTSPATH"], name, system), name, system), "ANTSPATH")
        )
    located = shutil.which(name, path=environ.get("PATH", ""))
    if located:
        candidates.append((Path(located), "PATH"))
    if common:
        roots = [] if system == "Windows" else [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
        roots += (
            {
                "MRtrix3": [home / "mrtrix3/bin", Path("/usr/local/mrtrix3/bin")],
                "FSL": [home / "fsl/bin", Path("/usr/local/fsl/bin")],
                "ANTs": [home / "ants/bin"],
            }[family]
            if system != "Windows"
            else [home / family / "bin"]
        )
        # Bounded version-specific user-owned setup locations also work when
        # Finder starts the app without the user's Terminal PATH.
        names = {"MRtrix3": "mrtrix3-3.0.5", "FSL": "fsl-eddy-2401.2", "ANTs": "ants-2.6.5"}
        folder = home / "TractoSphereScientificTools" / names[family]
        roots += [folder / "bin"]
        if family in {"MRtrix3", "ANTs"}:
            roots += [folder / ("mrtrix3" if family == "MRtrix3" else names[family]) / "bin"]
        candidates.extend((_candidate(root, name, system), "common installation") for root in roots)
    for path, source in candidates:
        if path is not None and path.is_file():
            return path, source
    return None, "not found"


def _fsl_binary(path: Path, name: str) -> Path:
    """Inspect the actual FSL binary behind its share/fsl/bin launcher, without running it."""
    resolved = path.resolve()
    for parent in resolved.parents:
        if (parent / "conda-meta").is_dir() and (parent / "bin" / name).is_file():
            return parent / "bin" / name
    return resolved


def _query(path: Path, argument: str, timeout: float) -> tuple[str, str | None]:
    """Execute only the fixed, bounded version query, no shell or MRI inputs."""
    try:
        result = subprocess.run(
            [str(path), argument],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        text = result.stdout
        if result.returncode:
            return (
                text,
                f"Version query exited {result.returncode}: {(result.stderr or text)[:1000]}",
            )
        return text, None
    except subprocess.TimeoutExpired:
        return "", f"Version query exceeded {timeout:g} seconds; tool not verified"
    except (OSError, UnicodeError) as exc:
        return "", f"Could not query executable: {exc}"


def inspect_dependencies(
    *,
    directories: Mapping | None = None,
    environ: Mapping | None = None,
    search_common: bool = True,
    home: Path | None = None,
    timeout: float = 3,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[str], None] = lambda _: None,
    system: str | None = None,
) -> dict:
    """Return exact discovered paths/version evidence and per-operation setup needs.

    A cancelled scan is incomplete, never a positive compatibility decision.
    `search_common=False` permits controlled PATH-only deployments and clean-profile
    tests. No broad disk scan, downloads, configuration writes or MRI commands.
    """
    if not 0 < timeout <= 10:
        raise ValueError("Version query timeout must be >0 and ≤10 seconds")
    directories, environ = dict(directories or {}), dict(os.environ if environ is None else environ)
    home = Path.home() if home is None else home
    system = system or platform.system()
    records, fsl_records, suite_versions = [], {}, {}
    stopped = False
    for family, names in TOOLS.items():
        for name in names:
            if cancelled():
                stopped = True
                break
            progress(f"Inspecting {family} · {name}")
            path, source = _locate(name, family, directories, environ, search_common, home, system)
            item = dict(
                family=family,
                name=name,
                path=None,
                discovered_path=str(path) if path else None,
                discovery=source,
                version=None,
                build=None,
                platform=None,
                version_source=None,
                state="missing",
                detail=f"{name} not found. Install {family} or select its bin folder.",
                installation_url=INSTALL_LINKS[family],
            )
            if path is not None:
                path = _fsl_binary(path, name) if family == "FSL" else path.resolve()
                item["path"] = str(path)
                item["state"] = "unverified"
                wsl = (
                    str(path)
                    .lower()
                    .replace("/", "\\")
                    .startswith(("\\\\wsl$\\", "\\\\wsl.localhost\\"))
                )
                if system == "Windows" and (family == "FSL" or wsl):
                    item["detail"] = (
                        "Windows-to-WSL execution is unavailable; Linux/WSL executables are not native Windows tools."
                    )
                elif system == "Windows" and path.suffix.lower() != ".exe":
                    item["detail"] = (
                        "Not a native Windows .exe. MSYS/WSL scripts and batch launchers are not queried as native scientific executables."
                    )
                elif not os.access(path, os.X_OK):
                    item["detail"] = "File exists but is not executable. Review its installation."
                elif family == "FSL":
                    prefix = path.parent.parent
                    if prefix not in fsl_records:
                        rows = []
                        for p in sorted((prefix / "conda-meta").glob("fsl*.json")):
                            try:
                                record = json.loads(p.read_text())
                                if isinstance(record, dict):
                                    rows.append((p, record))
                            except (OSError, ValueError):
                                continue
                        fsl_records[prefix] = rows
                        version_file = prefix / "etc/fslversion"
                        try:
                            suite_versions[str(prefix)] = (
                                version_file.read_text().strip() if version_file.is_file() else None
                            )
                        except OSError:
                            suite_versions[str(prefix)] = None
                    owners = [
                        (p, r)
                        for p, r in fsl_records[prefix]
                        if f"bin/{name}" in r.get("files", [])
                    ]
                    if len(owners) == 1 and isinstance(owners[0][1].get("version"), str):
                        p, r = owners[0]
                        item.update(
                            version=r["version"],
                            build=r.get("build"),
                            platform=r.get("subdir"),
                            version_source=str(p),
                            state="version_match",
                            detail="Installed component/package ownership recorded; execution preview binds binary and metadata hashes.",
                        )
                        if name == "eddy_cpu" and r["version"] != EDDY_TESTED_RELEASE:
                            item.update(
                                state="unsupported",
                                detail=f"EDDY {r['version']} is unvalidated; correction adapter requires {EDDY_TESTED_RELEASE}. Do not substitute the FSL suite version.",
                            )
                    else:
                        item["detail"] = (
                            "Missing or ambiguous FSL component ownership/version record. Suite version alone cannot validate this adapter."
                        )
                else:
                    argument = "-version" if family == "MRtrix3" else "--version"
                    output, error = _query(path, argument, timeout)
                    item.update(version_output=output[:8192], version_source=f"{path} {argument}")
                    pattern = (
                        rf"==\s*{name}\s+(\d+\.\d+\.\d+)\s*=="
                        if family == "MRtrix3"
                        else r"ANTs Version:\s*(\S+)"
                    )
                    match = re.search(pattern, output)
                    if match:
                        item["version"] = match[1]
                    if error:
                        item["detail"] = error
                    elif not match:
                        item["detail"] = (
                            "Version output was not recognized by the validated adapter's parser."
                        )
                    elif match[1] not in (
                        MRTRIX_TESTED_RELEASES if family == "MRtrix3" else {ANTS_N4_TESTED_RELEASE}
                    ):
                        expected = (
                            ", ".join(sorted(MRTRIX_TESTED_RELEASES))
                            if family == "MRtrix3"
                            else ANTS_N4_TESTED_RELEASE
                        )
                        item.update(
                            state="unsupported",
                            detail=f"Found {match[1]}; this adapter requires {expected}. Another version needs validation, not an automatic downgrade.",
                        )
                    else:
                        item.update(
                            state="version_match",
                            detail="Matches the adapter version gate; execution preview still checks binary hashes and required sources.",
                        )
            records.append(item)
        if stopped:
            break
    by_name = {r["name"]: r for r in records}
    features = {
        "viewer": dict(
            title="MRI / tract / atlas viewer and saved QC",
            state="available",
            reasons=["External scientific executables are optional for this workspace."],
        )
    }
    for key, (title, requirements) in FEATURES.items():
        reasons = []
        if system not in {"Darwin", "Linux"}:
            reasons.append(
                "Scientific adapter execution is unsupported on this OS; Windows-to-WSL bridging is not implemented."
            )
        for family, names in requirements.items():
            entries = [by_name.get(n) for n in names]
            reasons.extend(
                f"{n}: {(r['state'] if r else 'not checked')}"
                for n, r in zip(names, entries, strict=True)
                if not r or r["state"] != "version_match"
            )
            locations = {str(Path(r["path"]).parent) for r in entries if r and r["path"]}
            if len(locations) > 1:
                reasons.append(
                    f"{family} required tools come from multiple bin folders; choose a consistent installation."
                )
        if key == "bias" and by_name.get("dwibiascorrect", {}).get("path"):
            library = (
                Path(by_name["dwibiascorrect"]["path"]).parent.parent
                / "lib/mrtrix3/dwibiascorrect/ants.py"
            )
            if not library.is_file():
                reasons.append(
                    "MRtrix ants wrapper source unavailable at the adapter-required installation layout."
                )
        features[key] = dict(
            title=title,
            state="needs_setup" if reasons or stopped else "version_requirements_met",
            reasons=reasons
            or [
                "Setup version checks passed; acquisition/history/evidence and binary/source checks remain required at command preview."
            ],
        )
        if key == "combination":
            features[key]["additional_requirement"] = (
                "Separate verified full AP/PA pairing and the exact pinned HCP helper; calibration b0 images alone are insufficient."
            )
    return dict(
        schema="tractosphere.dependencies/1",
        checked_at=datetime.now(UTC).isoformat(),
        cancelled=stopped,
        tools=records,
        features=features,
        fsl_suite_versions=suite_versions,
        installation_links=INSTALL_LINKS,
        platform_system=system,
        automatic_installation=False,
        compatibility_scope="Version/provenance inventory only; not scientific acceptance or processing approval.",
    )
