# TractoSphere

**Diffusion MRI Research Studio · From Diffusion to Discovery**

An open-source diffusion MRI research workstation for preprocessing, quality
control, visualization, tractography, and analysis.

**PUBLIC ALPHA**

TractoSphere brings a linked MRI and tractography workspace together with
metadata-aware preprocessing and saved QC. The alpha supports visualization and
editing of existing tractograms; diffusion reconstruction, new tractography
generation and connectome analysis are future capabilities.

![TractoSphere MRI and real tractography workspace](docs/screenshots/viewer-dark.png)

## Try the alpha

**Mac users: start with the [step-by-step installation guide](docs/installation.md#mac-install-and-open-the-application).**
It covers Python, the three exact release downloads, the installation helper,
first launch and the command to reopen the app. The helper route does not need
Git or a repository checkout. This alpha is a Python application, not a
standalone .app/.dmg.

macOS Apple Silicon with Python 3.12 is the locally verified platform. The pinned
Qt GUI libraries list macOS 14.4+ as their supported range. Intel macOS, Linux
x86-64 and Windows remain experimental; Ubuntu 24.04 ARM64 has bounded VM/X11
software-rendering validation. See [platform evidence](docs/platforms.md).

**Install the application first; add scientific tools when you need preprocessing.**
Choose **Continue with viewer only** to open images, existing tracts, atlases,
ROIs and saved QC without MRtrix3, FSL or ANTs. No AI account, API key, cloud
service or MRI download is required to launch.

For preprocessing, use the guide's
[exact folder examples for MRtrix3, FSL and ANTs](docs/installation.md#which-folders-to-select)
and [package installation steps](docs/installation.md#install-the-scientific-tools-on-a-mac).
Each tool needs its actual installation root or `bin` folder, followed by
**Test Installation** and saving the verified paths. A downloaded archive or
an empty folder is not an installed tool. macOS security prompts, unknown versions
and wrong paths have [separate troubleshooting steps](docs/installation.md#understand-setup-results).

The in-app **Install / Setup** opens official downloads and guidance; installation
of scientific packages remains manual. A “Missing” tool message does not mean the
GUI failed to install. Downloads require explicit consent, and no scientific
tool or system PATH is silently changed.

Developers can use the
[source installation instructions](docs/installation.md#source-installation-on-macos).
[Windows and Linux alternatives](docs/installation.md#other-installation-methods)
are documented separately. Windows preprocessing/WSL bridging is unsupported.
The [dependency setup guide](docs/dependency_setup.md) describes exact versions,
inventory details and operation requirements. Missing acquisition metadata stays missing.

## What works immediately after installing the GUI

| Feature | Additional scientific software |
| --- | --- |
| NIfTI/4D viewing, TCK/TRK rendering, atlas/ROI editing and tract filtering | None |
| Saved QC reports, image comparisons, metadata/history inspection | None |
| Conversion, MP-PCA denoising, Gibbs correction and dwi2mask | MRtrix3 3.0.5 |
| Metadata-supported TOPUP / CPU EDDY | MRtrix3 3.0.5 + FSL, eddy_cpu component 2401.2 |
| Standard MRtrix/ANTs N4 correction | MRtrix3 3.0.5 + ANTs N4 2.6.5 |
| Supported T1 preparation and rigid / BBR alignment | FSL with verified component records |
| Optional matched AP/PA combination | MRtrix3 + FSL + separately verified pinned helper and pairing |

Inputs and applicable metadata are required for each operation. These existing
version gates are intentionally retained; installing the latest upstream tool
does not guarantee adapter compatibility. The [setup guide](docs/dependency_setup.md)
explains detection, guided installation, version evidence and limits. See
[platform compatibility](docs/platforms.md) for macOS, Linux and Windows/WSL scope.

## What works today

- **MRI viewing:** NIfTI-1/2 `.nii` and `.nii.gz`; linked axial, sagittal, coronal
  and interactive 3D views; 4D volume navigation, coordinates, window/level,
  colormaps, overlays and anatomical slice planes.
- **Tractography viewing:** real MRtrix TCK and TrackVis TRK; direction/uniform/
  available-scalar coloring, lines/tubes, transparency, bounded display density,
  camera presets, selection, hide/delete/cut, undo/redo and derivative exports.
- **Regions and anatomy:** binary masks, surfaces, named discrete atlases and
  thresholded probability maps; grouped ROIs, colors/opacity, drawing/morphology,
  inclusion/exclusion filtering of existing streamlines and workspace persistence.
- **Reviewed preprocessing:** explicit input/history validation, native conversion,
  MP-PCA denoising, Gibbs correction, masking, metadata-gated TOPUP/EDDY,
  MRtrix/ANTs N4 and supported anatomical preparation/rigid/BBR alignment.
  Optional matched AP/PA combination has strict pairing and producer requirements.
- **QC and provenance:** separate execution, numerical verification and scientific
  review states; saved metrics/figures/logs, before/after comparisons, portable
  report access/export, input/output hashes and immutable attempt directories.
- **Projects:** acquisition identities, BIDS discovery, inherited metadata,
  saved processing histories and limited verified native-stage continuation.
- **Scripting:** core Python APIs and `dmri-studio` CLI. 

Read the [feature overview](docs/features.md), [quick start](docs/quickstart.md),
[atlas guide](docs/atlas.md), [scientific limitations](docs/limitations.md) and
[validation record](docs/validation.md). The
[DSI-style viewer compatibility matrix](docs/viewer-compatibility.md) describes
behavior and limits rather than claiming exact DSI Studio parity.

## Scientific boundaries

Original scientific files are preserved. Visualization settings do not change
source voxel values. Atlas registration is never inferred from matching array
sizes or a visually plausible overlap. A known MNI/native mismatch requires a
verified transform or a verified atlas already in the subject's space.

Missing acquisition parameters stay missing. MP-PCA must precede interpolation;
standard Gibbs correction does not establish complete partial-Fourier correction.
QC flags do not authorize volume deletion or mask repair. Successful execution
and visually smooth outputs do not establish scientific acceptance.

This alpha has bounded software tests and small-cohort research experience.

## Feedback and contributions

- [Bug reports and installation problems](https://github.com/kkartikeya-kulkarni/TractoSphere/issues)
- [Feature requests and scientific QC questions](https://github.com/kkartikeya-kulkarni/TractoSphere/issues/new/choose)
- [Researcher discussions](https://github.com/kkartikeya-kulkarni/TractoSphere/discussions)
- [Contribution guide](CONTRIBUTING.md) · [Security policy](SECURITY.md)

Share sanitized logs and reproducible public/synthetic examples. **Do not upload
identifiable patient data, restricted MRI files, private study records, credentials
or screenshots containing personal information.**

## License and citation

TractoSphere's own source is MIT licensed. Bundled fonts and icons retain their
original licenses; external scientific tools have separate terms and are not
bundled. See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md).
Use [CITATION.cff](CITATION.cff) when citing this software. This alpha has no
software-paper DOI; references are not fabricated.
