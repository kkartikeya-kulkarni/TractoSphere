# Installation

PUBLIC ALPHA · RESEARCH USE ONLY · NOT FOR CLINICAL USE

## Verified installation scope

Python 3.12 on macOS Apple Silicon is the local verification target. A fresh venv
must have include-system-site-packages=false; an already populated research
conda environment does not count as a clean install. The alpha validation record
lists the actual tested environment and commands.

Ubuntu 24.04 ARM64 source/wheel installation was tested in a clean Linux VM
with X11/Mesa software rendering. Intel macOS, Linux x86-64 and Windows remain
experimental. No physical Linux GPU or Windows desktop was tested. See
[platform evidence](platforms.md). External FSL/MRtrix/ANTs
availability is platform-dependent. CUDA is not assumed on Apple Silicon.

## Source installation

Install Python 3.12+ from a trusted distributor such as python.org or Miniforge.
In the downloaded/extracted source folder:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install '.[gui]'
python -m pip check
dmri-studio gui
```

On Windows PowerShell use `py -3.12 -m venv .venv`, then
`.venv\Scripts\Activate.ps1` and the same `python -m pip` commands. If shell
activation is restricted, use `.venv\Scripts\python.exe -m pip install ".[gui]"`
and `.venv\Scripts\dmri-studio.exe gui` without changing global execution policy.
These commands are provided for experimental testing, not claimed as Windows
installation verification. Use a local directory with ordinary write permissions.
For headless loading/planning, `python -m pip install .` installs NumPy/NiBabel
without desktop libraries. Atlas numerical import additionally needs SciPy,
available through the GUI extra. CLI usage: `dmri-studio --help`.

## Wheel installation

Use the versioned wheel and checksums from the [GitHub prerelease](https://github.com/kkartikeya-kulkarni/TractoSphere/releases/tag/v0.1.0-alpha). From a fresh
Python 3.12+ venv, substitute the actual wheel path:

```sh
python -m pip install './dmri_studio-0.1.0a0-py3-none-any.whl[gui]'
python -m pip check
dmri-studio gui
```

Dependencies are downloaded from their upstream distributions; the wheel is the
TractoSphere application, not a self-contained macOS executable.

## macOS helper

The release includes Install-TractoSphere.command alongside the wheel. Place both and SHA256SUMS
in one folder and run the helper in Terminal. It checks Python, creates a new
user-local venv, installs that exact wheel with GUI dependencies, verifies pip
requirements and prints the launch command. It does not install scientific tools
or modify existing environments. If macOS blocks an unsigned downloaded command,
review the script and run `bash Install-TractoSphere.command` in Terminal.

No notarized standalone .app/.dmg is provided in this alpha. The tested source/
wheel path avoids presenting an unverified frozen application as supported and
avoids silently bundling restricted scientific executables. No paid signing or
cloud service is required.

## External preprocessing tools

Install only the tools needed for your selected operations, using their official
instructions and licenses:

- MRtrix3: conversion, denoising, Gibbs, dwi2mask and bias wrapper.
- FSL: TOPUP/EDDY and supported structural preparation/alignment; CPU EDDY can
  require substantial time/RAM. CUDA requires a compatible separately installed
  GPU environment.
- ANTs: N4BiasFieldCorrection for the standard MRtrix ants bias-correction path.
- Optional AP/PA combination: verified full-diffusion pairing, supported FSL
  eddy_combine and the exact pinned HCP gradient helper required by the adapter.

On first launch, the scientific dependency checker scans installed tools and
reports exact executable paths, versions, FSL component builds and operation
requirements. **Continue with viewer only** works without any of these tools.
Reopen setup in Preferences or the command palette; `dmri-studio doctor` provides
the same read-only inventory without Qt. Use Browse → Check installed tools →
Use checked tool locations to save app-owned defaults explicitly.

The current MRtrix adapters require 3.0.5, CPU EDDY requires component 2401.2 and
ANTs N4 requires 2.6.5. FSL structural tools require installed component ownership
records; the FSL suite version alone is insufficient. The standard N4 path also
requires the MRtrix wrapper sources at its expected installation layout. Newer
upstream downloads are not automatically validated; leave dependent steps pending
or use a separately installed compatible environment. The checker never installs,
upgrades, downgrades or modifies scientific software.

See the [dependency setup guide](dependency_setup.md) for official installation
links, exact per-operation requirements, directory selection and JSON reports.
Configure executable directories in setup/the preprocessing workspace or provide
PATH as documented by each tool. Installing Python dependencies alone does not
make these algorithms available. The GUI presents tool versions/commands before
execution. FSL's noncommercial terms are independent of TractoSphere's MIT license.

## Troubleshooting

- `No module named PySide6/vtk`: activate the correct venv and install the GUI extra.
- No compatible Python/wheel: use supported Python 3.12+ and a currently supported
  OS/architecture; do not mix Intel/ARM environments on a Mac.
- Blank/failed 3D rendering: check graphics-driver support and provide the sanitized
  Qt/VTK versions and error through the installation issue template.
- Missing scientific tool: install/configure that tool; viewer operation does not
  depend on FSL/MRtrix/ANTs.
- Missing metadata: supply acquisition evidence; do not copy parameters from a
  similar protocol.
- Streaming/cloud paths: restore required files to local processing storage first.

Use `python -m pip show dmri-studio` and `dmri-studio --version` to report versions.
Never include credentials, patient identifiers or restricted data in public issues.

## Guided scientific software installation

Preferences → Software Dependencies → Install / Setup selects pinned official
platform guidance. Browser downloads require consent; archive verification is
available when an official digest exists. Locate the complete extracted folder
and use Test Installation to verify/configure app-owned paths. No scientific
installer or global PATH change is automated. FSL needs its official Terminal/IT
workflow. See [guided setup](dependency_setup.md) and [platform scope](platforms.md).

## Ubuntu 24.04 desktop libraries (tested ARM64 VM)

A minimal Ubuntu server install needs Qt X11 and OpenGL libraries in addition to
pip packages. On Ubuntu 24.04 (not every Linux distribution):

```sh
sudo apt-get update
sudo apt-get install -y libxcb-cursor0 libxkbcommon-x11-0 libegl1 libgl1-mesa-dri libopengl0 libxcursor1 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libxcb-xinerama0 libxcb-randr0 libxcb-render-util0
```

Run the GUI from a desktop session. For bounded automated software-rendered tests,
install `xvfb xauth` and run `LIBGL_ALWAYS_SOFTWARE=1 xvfb-run -a dmri-studio gui`.
This does not test native GPU drivers. Use the appropriate upstream Qt/distro
instructions on other Linux systems. Native Wayland and Windows/WSL processing
remain unverified/unsupported as specified in [platform compatibility](platforms.md).

Application settings use Qt's per-user platform store, not machine-specific source
paths. Workspace paths refer to actual local files; moving a workspace between
computers may require restoring/reselecting the referenced datasets. Saving a
workspace is not an archive of all referenced MRI files. No dataset is bundled.
