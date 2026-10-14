# Scientific dependency setup

The MRI, atlas, ROI/tract editor, comparisons and saved-QC browser run with the
Python GUI dependencies alone. MRtrix3, FSL and ANTs are optional **local
command-line installations**, not bundled tools, imported libraries, containers
or remote APIs. TractoSphere never installs, upgrades or downgrades them.

## First launch

After entering the studio, the setup dialog inventories installed scientific
tools in a background worker. Continue with viewer only remains usable during
that check. Continuing records your choice; subsequent launches do not force
another setup dialog. Reopen it through the command palette's **Check scientific
dependencies** action or the Scientific software setup section in Preferences.

The table reports every required executable's actual path and exact version,
with FSL component build/platform and evidence available by selecting a row.
Missing, unrecognized and unvalidated tools remain distinct. The FSL suite label
is recorded in the exported report separately from executable component versions.

Auto-detection checks FSLDIR, ANTSPATH, PATH and, optionally, a small set of common
installation folders. It does not search your MRI directories. Use Browse to
choose an existing installation root or bin directory, then Check installed
tools. An explicit folder is authoritative; an incomplete installation does not
silently fall back to another copy. Use checked tool locations explicitly saves
application preferences and fills the existing preprocessing tool fields. It does
not alter system PATH, shell configuration or scientific algorithm settings.
Changing a folder requires rechecking. Active preprocessing prevents applying
new locations. Each new command preview still validates the actual inputs,
executables, hashes and provenance independently.

## Adapter compatibility

| Operation | Required local software | Existing adapter requirements |
| --- | --- | --- |
| Native conversion / brain masking | MRtrix3 | Required executables report 3.0.5 |
| MP-PCA denoising / Gibbs correction | MRtrix3 | Required executables report 3.0.5; acquisition/history checks still apply |
| TOPUP / EDDY | MRtrix3 + FSL | MRtrix3 3.0.5; eddy_cpu component 2401.2; installed FSL component ownership records |
| Standard MRtrix/ANTs N4 | MRtrix3 + ANTs | MRtrix3 3.0.5, adapter-required wrapper source layout, N4BiasFieldCorrection 2.6.5 |
| Supported T1 preparation / rigid / BBR | FSL | Required component ownership/version records; no new numeric version pin is introduced |
| Optional matched AP/PA combination | MRtrix3 + FSL + pinned HCP helper | Required tools, verified full-diffusion pairing and separate producer/helper contracts |

These are the current validation gates, not a promise that every newer release
is interchangeable. Official installers may offer different versions. Do not
replace a working research installation merely to suppress a setup warning.
Choose a separately installed compatible toolchain or leave the dependent step
pending; another version requires adapter validation. No version checks have
been relaxed. FSL installations without unique conda component ownership records
cannot satisfy the current adapters, even if their suite label looks familiar.
CUDA EDDY discovery/execution is not added by this setup checker.

A matched setup version is **not** processing approval, successful correction or
scientific acceptance. Acquisition metadata, input processing history, masks,
calibrations, source layouts and scientifically reviewed QC remain necessary.
Automatic joint-acquisition assembly and interrupted EDDY recovery remain future
features.

## Official installation guidance

- [MRtrix3 downloads](https://www.mrtrix.org/download/)
- [FSL installation on macOS](https://fsl.fmrib.ox.ac.uk/fsl/docs/install/macos.html)
- [ANTs release binaries](https://github.com/ANTsX/ANTs/wiki/Installing-ANTs-release-binaries)

Links open your browser only when clicked. Review upstream licenses and supported
platforms independently; TractoSphere's MIT license does not cover these tools.

## Read-only command-line check

```sh
dmri-studio doctor
dmri-studio doctor --path-only
dmri-studio doctor --mrtrix-bin /path/to/mrtrix3/bin --fsl-bin /path/to/fsl/bin --ants-bin /path/to/ants/bin
dmri-studio doctor --require correction --report-json setup-report.json
```

`--path-only` disables common-folder searches, while still respecting explicit
folders, FSLDIR and ANTSPATH. Without --require, missing optional tools do not
cause an error exit; viewer-only use is valid. An unmet explicitly required
feature exits 1. Invalid options or an existing export destination exit 2.
Reports are written exclusively to new files; review local paths before sharing.

Inventory executes only bounded `-version` MRtrix and `--version` ANTs probes.
FSL is inspected through installed package ownership records; TOPUP and EDDY are
never executed just to obtain a version. Scanning is asynchronous; shutdown
cancels remaining probes and waits safely for the current bounded query.

See [installation](installation.md) and [validation](validation.md) for the exact
tested environment and remaining platform limitations.

## Guided Install / Setup

The existing Software Dependencies section has per-package **Install / Setup**,
**Official Installation Guide**, **Locate Existing Installation**, **Scan Again**
and **Test Installation**. The guided detail sheet uses the same scanner; it is
not another dependency manager. It detects OS/process architecture and, on Linux,
distribution metadata. It shows the current adapter version, installed status,
method, user-owned destination, available space, known download size, licenses
and expected permissions. Instructions remain readable offline.

For macOS MRtrix3 3.0.5, the official release's universal Intel/ARM archive was
inspected: bin/mrconvert contains both architectures and the complete lib wrapper
layout is present. Download it using the browser, extract with Finder, preserve
the whole mrtrix3 folder, then locate/test it. The known archive is 65,793,237 bytes
and contains 179,551,781 regular-file bytes; overhead/additional prerequisites are
not included. No publisher asset digest was provided, so this app makes no
checksum-authentication claim and does not automatically install it.

ANTs 2.6.5 has curated platform-specific official release archives. A local
**Verify Downloaded Archive** action reads the file in a cancellable background
worker and compares size/SHA-256 against the official release API's asset digest.
It never extracts/executes the archive. A match is distribution integrity evidence,
not a signature, successful installation or scientific acceptance. Expanded size
is unknown; free space is not treated as a guarantee. Preserve bin/lib when
extracting. Unsigned macOS binaries may require upstream security approval/IT
help; TractoSphere never removes quarantine or collects passwords.

FSL uses its official platform installer/conda guidance. It currently needs
Terminal or IT assistance; no integrated FSL one-click installer is offered.
Confirm eddy_cpu component 2401.2 and preserve conda-meta ownership/build records;
the default/latest suite is not automatically recommended as compatible. Windows
gets FSL's WSL instructions and an explicit warning that the application has no
Windows-to-WSL execution bridge.

**Automatic installation methods: none in this alpha.** MRtrix lacks a publisher
asset checksum; FSL resolves dependencies and can change shell configuration;
ANTs unsigned binaries/runtime prerequisites require platform approval not yet
verified on an independent Mac. The browser download is an explicit consented
handoff, not an app-controlled download/install. No live remote script, package
manager, sudo, installer or shell profile modification is executed by this GUI.
This decision does not delay release to implement unsafe automatic installation.

## Verify and configure after external installation

1. Select the completed extracted root/bin folder, or use Scan Again for common
   locations. The guide offers a unique actually detected folder automatically.
2. Test Installation reuses the scanner on that exact selected directory.
3. With **After a successful test, save verified paths** selected, a complete,
   compatible family is saved to app-owned settings only after its matching test
   completes. Unrelated scientific tool paths are retained. Unchecking the option
   performs verification only. A plain rescan does not save settings.
4. Missing/unsupported/incomplete versions, an interrupted test, changed choices,
   closed dialog, active preprocessing or a preferences write error prevent
   automatic configuration. Do not use partially downloaded/extracted files.
5. The existing per-operation availability summary and preprocessing tool fields
   refresh. Future processing still independently validates versions, binaries,
   source hashes, acquisition inputs and scientific provenance.

For an incomplete family, some operations can remain usable through the existing
explicit command preview, but the guide does not label/configure the whole family
as ready. Installation Failed / Interrupted records a **user-reported** external
failure; the application does not monitor or invent an external installer's state.
Browser-launch failure is reported with recovery guidance; opening a browser is
not evidence of working internet, successful download or completed installation.

Headless/API parity:

```sh
dmri-studio doctor --path-only --setup ANTs --destination /new/user-owned/tools
```

Python: `dmri_studio.core.software_setup.installation_guide`, `destination_check`
and `verify_archive` reuse existing inventory records without importing Qt.
See [platform support](platforms.md) and [validation scope](validation.md).

Research sources inspected 2026-10-14:
[MRtrix 3.0.5 release](https://github.com/MRtrix3/mrtrix3/releases/tag/3.0.5),
[ANTs 2.6.5 release](https://github.com/ANTsX/ANTs/releases/tag/v2.6.5),
[ANTs binary/security guidance](https://github.com/ANTsX/ANTs/wiki/Installing-ANTs-release-binaries),
[FSL macOS installation](https://fsl.fmrib.ox.ac.uk/fsl/docs/install/macos.html),
[FSL conda guidance](https://fsl.fmrib.ox.ac.uk/fsl/docs/install/conda.html),
[FSL WSL instructions](https://fsl.fmrib.ox.ac.uk/fsl/docs/install/windows.html).

## Phase 3 platform checks

The same dependency manager is retained. Native Windows `.exe` detection now
handles selected installation roots/bin directories and ANTSPATH. Non-native
shell/batch launchers and WSL/FSL executables are not queried as Windows tools.
A native version match does not enable unsupported Windows scientific adapters.
On Linux, clean missing-tool scans, version-fixture tests, configuration persistence
and actual guided dialogs were exercised in Ubuntu 24.04 ARM64/X11. Scientific
software was not installed or validated end-to-end in that VM. Exact version gates
and binary-hash provenance remain unchanged. See [platform evidence](platforms.md).
