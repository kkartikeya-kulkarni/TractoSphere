# TractoSphere v0.1.1-alpha — Cross-platform fixes

PUBLIC ALPHA · RESEARCH USE ONLY · NOT FOR CLINICAL USE · SCIENTIFIC VALIDATION ONGOING

This update packages the fixes already verified on main. The v0.1.0-alpha tag and
its downloads remain unchanged. No scientific preprocessing algorithms or tool
compatibility requirements changed.

## Changes

- Prevent a native indexed_gzip crash on Windows with Unicode image filenames by
  selecting NiBabel's standard gzip reader on Windows. Arrays and geometry are
  preserved; compressed random access may be slower. macOS/Linux retain acceleration.
- Compare BIDS calibration associations using portable URI separators on Windows.
- Preserve approved SVG/font/icon bytes at checkout; retain exact-hash security
  auditing, including ZIP original-filename and disguised-executable checks.
- Correct GUI tests for actual available desktop/frame geometry and consistent
  mocked installation platforms, retaining expansion and exact restoration checks.
- Provide pinned, checksum-verified Mesa OpenGL only inside Windows hosted CI.
  No Mesa driver, MRI data, FSL, MRtrix3 or ANTs executable is included in downloads.
- Clarify Mac installation, reopening, and scientific executable folder selection.

## Install or upgrade

Use Python 3.12+ and the [installation guide](installation.md). GUI dependencies
are installed separately by pip; external scientific software is optional for
MRI, tractography, atlas and ROI viewing.

For macOS, download **TractoSphere-v0.1.1-alpha-macos-source-install.zip**, extract
it, then run `bash Install-TractoSphere.command` in its folder. The helper verifies
the companion wheel hash, creates a new `alpha-0.1.1` environment and launches the
application. It does not overwrite an earlier alpha installation or install
scientific tools. Reopen with:

```sh
"$HOME/Library/Application Support/TractoSphere/alpha-0.1.1/bin/dmri-studio" gui
```

For a manual install in a new virtual environment:

```sh
python -m pip install './dmri_studio-0.1.1a0-py3-none-any.whl[gui]'
dmri-studio gui
```

Verify downloaded files against **SHA256SUMS**. This is a Python environment
installation, not a standalone notarized .app/.dmg. Keep existing scientific
installations intact; Settings → Software Dependencies provides read-only
checks and guided setup. Exact scientific version gates are unchanged.

## Verification and platform scope

The corrected Windows revision passed [the complete hosted job](https://github.com/kkartikeya-kulkarni/TractoSphere/actions/runs/38645371566):
661 core tests, 103 GUI tests, and native Qt/VTK smoke. There were 23 core and
1 GUI pre-existing optional/platform skips. Four non-Windows jobs passed in
[the earlier matrix](https://github.com/kkartikeya-kulkarni/TractoSphere/actions/runs/38638990797),
including macOS ARM64/Intel and Ubuntu x86-64 Python 3.12/3.13. These runs are
at different commits; they are not a single all-platform green matrix.

- macOS Apple Silicon: locally verified; release wheel installation and GUI
  smoke are checked separately before publication.
- macOS Intel and Ubuntu x86-64: hosted CI tested; independent physical hardware
  and Linux GPU/Wayland acceptance remain pending.
- Ubuntu 24.04 ARM64: prior VM/X11 software-rendering verification.
- Windows Server 2022: hosted software-rendering verification. Physical Windows
  10/11 and hardware GPUs remain unverified. Scientific preprocessing and WSL
  execution remain unsupported on Windows.
- Independent physical Mac installation: pending.

See [platforms](platforms.md) and [limitations](limitations.md). Automated success
is not scientific acceptance. No new diffusion reconstruction, tractography
generation, joint-acquisition assembly or interrupted EDDY recovery is introduced.

## Feedback

Use [Issues](https://github.com/kkartikeya-kulkarni/TractoSphere/issues/new/choose)
for bugs and [Discussions](https://github.com/kkartikeya-kulkarni/TractoSphere/discussions)
for questions. Include versions, platform and sanitized logs. Do not upload
identifiable images, restricted research data or credentials.
