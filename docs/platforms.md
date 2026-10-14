# Platform compatibility

PUBLIC ALPHA · RESEARCH USE ONLY · SCIENTIFIC VALIDATION ONGOING

## Actual verification scope (Phase 3, 2026-10-14)

| Platform | Installation and GUI evidence | Scientific tools | Alpha support |
| --- | --- | --- | --- |
| macOS Apple Silicon | Source and noneditable wheel, Python 3.12, native Qt/VTK on Apple M5 / 16 GB / macOS 26.3; real T1, 65-volume DWI, TCK/TRK, atlas ROIs, edits, workspace, dark/light | Existing local adapters retained; exact MRtrix 3.0.5 / EDDY 2401.2 / N4 2.6.5 gates | Primary locally verified platform; independent clean Mac still pending |
| macOS Intel | Matching Python 3.12 dependency wheels identified; Intel macOS 15 CI prepared, not executed | Official universal MRtrix and matching Intel ANTs guidance | Experimental; no Intel machine tested |
| Ubuntu 24.04 ARM64 | Clean VM source and wheel installs; full tests; X11/Xvfb + Mesa software rendering; real MRI/TCK/TRK/atlas/filtering and native Qt screenshots | Missing-tool and simulated-tool tests; generic local subprocess execution/cancellation tested; no full scientific pipeline executed on Linux | Experimental, with bounded VM evidence; not physical-GPU certification |
| Ubuntu/Linux x86-64 | Python 3.12/3.13 Ubuntu 24.04 CI prepared, not executed | Existing strict adapter gates and distribution-specific guidance retained | Experimental; no x86-64 Linux runtime tested |
| Windows 10/11 x86-64 | Python 3.12 wheel availability and simulated platform contracts; Windows Server 2022 CI prepared, not executed | Native .exe detection improved; FSL/WSL and all Windows scientific adapters remain unavailable | Experimental viewer only; no Windows runtime/GPU verification |
| Windows-to-WSL | No execution, path translation, environment, cancellation or output bridge | A WSL/Linux executable is never reported as a usable native Windows FSL tool | Unsupported |
| Other OS/architecture | No application validation | Official guides accessible, no readiness claim | Unsupported/unverified |

The terms **locally verified**, **VM-tested**, **CI prepared**, **experimental**
and **unsupported** are deliberately distinct. No new platform has completed a
physical-hardware or independent-user acceptance study. Source compatibility and
upstream binary availability do not establish application or scientific validity.

## Desktop requirements and rendering

Use a native Python architecture; do not mix Intel and ARM Python packages. The
application wheel is platform-independent, but Qt/VTK/NumPy/SciPy wheels are not.
Python 3.12 was actually exercised. The Python 3.13 CI target is prepared, not a
completed verification of every Python version allowed by package metadata.

[Qt 6.12's upstream support table](https://doc.qt.io/qt-6/supported-platforms.html)
lists macOS 14.4+, Windows 10 1809+/11 and Ubuntu 24.04 ARM64/x86-64. These are
upstream criteria, not evidence that every listed TractoSphere configuration has
been tested. Windows 10/11 client desktops were not tested by this phase's CI
configuration (its planned Windows runner is Server 2022).

Linux verification used X11 through Xvfb and Mesa llvmpipe, not Wayland or a
physical GPU. Native Wayland remains unverified; on a Wayland desktop with
XWayland available, `QT_QPA_PLATFORM=xcb dmri-studio gui` requests the X11 path.
Do not force this setting on Windows/macOS or on systems without X11 support.
See the Ubuntu-specific library installation example in [installation](installation.md).

The macOS Qt/VTK presentation workaround is retained. Workspace screenshots now
composite the actual VTK framebuffer into Qt's captured controls: native X11
OpenGL children can display correctly while QWidget.grab returns black or
uninitialized pixels. The fix changes screenshot capture, not scientific data,
geometry, rendering algorithms or camera settings. Direct 3D export remains VTK.

## Scientific software and Windows/WSL boundary

Detection and scientific acceptance remain separate. Matching a version is not
permission to execute an unsupported adapter. Previews still bind executable
paths/hashes, tool ownership records and required sources; versions are unchanged.

- MRtrix Windows guidance uses upstream MSYS2 instructions. A shell-dependent
  MSYS script is not a native executable. Windows .exe discovery supports explicit
  bin/root directories and ANTSPATH; batch launchers are not queried as native tools.
- ANTs publishes Windows binaries; runtime dependencies and this application's
  adapter integration still require Windows testing. Availability is not support.
- FSL's supported Windows route uses WSL. `wsl --list --verbose` can identify
  distributions and `wslpath` can translate paths, but a reliable adapter must also
  bind the distribution, Linux environment, filesystem access, working directory,
  cancellation, provenance and outputs. This release implements no such bridge.
  Do not launch Linux commands directly through a Windows PATH or use PA/readout
  parameters from another acquisition to compensate for installation issues.
- Linux distribution and architecture must match the selected tool binary. Do
  not substitute x86-64 ANTs archives on ARM64 or assume every distro uses apt.
- Existing macOS/Unix path discovery covers Finder's limited PATH. Explicit
  verified installation directories are saved only in application settings; no
  global PATH or scientific installation is changed.

Generic argv execution is shell-free on every OS. POSIX cancellation targets the
owned process group. Windows cancellation currently targets the direct executable;
no Windows child-pipeline/WSL cancellation guarantee is claimed. Resource logs use
POSIX ps semantics on Unix and read-only psutil lifetime-average CPU/RSS on Windows.
Both exclude descendants and can miss brief memory peaks. Neither CPU utilization
nor dependency readiness establishes scientific QC acceptance.

Use local APFS/ext4/NTFS-style storage with hard-link support for atomic exclusive
exports. FAT/exFAT or restricted/network filesystems may reject publication;
outputs are not overwritten as a fallback. Streaming/cloud datasets must be
restored locally before processing. For Windows mapped streaming drives, configure
`DMRI_STREAMING_ROOTS` explicitly; do not rely on a provider-specific drive letter.

## CI status

`.github/workflows/tests.yml` builds/installs the wheel and source archive, checks
packaged assets and dependency resolution, runs core/CLI/geometry/tract/atlas/QC/
setup/filesystem tests, Qt behavior tests and actual VTK-pixel smoke tests. It
includes macOS ARM, macOS Intel, Ubuntu x86-64 (Python 3.12/3.13) and Windows x64.
Fixtures are small generated geometry, explicitly not biological validation data.

Jobs are gated to public repositories to avoid paid private CI usage. The workflow
has been locally linted, but **no GitHub runner results are claimed in this phase**.
The repository remains private. Xvfb/CI screenshots are not physical-GPU tests.

See [Phase 3 validation](cross_platform_validation_phase3.md) for results and
[dependency setup](dependency_setup.md) for guided installation boundaries.

Sources: [Qt](https://doc.qt.io/qt-6/supported-platforms.html),
[MRtrix Windows installation](https://mrtrix.readthedocs.io/en/latest/installation/windows_install.html),
[ANTs binary installation](https://github.com/ANTsX/ANTs/wiki/Installing-ANTs-release-binaries),
[WSL commands](https://learn.microsoft.com/windows/wsl/reference),
[WSL interoperability](https://learn.microsoft.com/en-us/windows/dev-environment/wsl-interop),
[GitHub runner architecture and pricing scope](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

## Windows gzip safety

CI reproduced a native indexed_gzip 1.10.3 process exit for a Unicode NIfTI
filename. TractoSphere selects NiBabel's standard gzip backend process-locally
on Windows before core image reads. Source arrays and installed packages are
unchanged; compressed random reads can be slower. macOS/Linux keep acceleration.
This fix is on main after v0.1.0-alpha; the published alpha artifacts are unchanged.

Windows hosted CI needs real OpenGL rendering support. Its Hyper-V display did
not provide OpenGL 3.2, and VTK's OSMesa fallback lacked its DLL. CI now verifies
a pinned Mesa archive against its publisher SHA-256 and deploys only its WGL
loader/driver into the disposable runner's Python directory. No system driver
or user workstation is modified, and no Mesa binaries are shipped in the app.
These are software-rendered tests, not physical-GPU or Windows 10/11 validation.
Reference: https://docs.vtk.org/en/v9.5.1/release_details/9.4/support-runtime-opengl-window-selection.html
