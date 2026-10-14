# TractoSphere

**Diffusion MRI Research Studio · From Diffusion to Discovery**

An open-source diffusion MRI research workstation for preprocessing, quality
control, visualization, tractography, and analysis.

**PUBLIC ALPHA · RESEARCH USE ONLY · NOT FOR CLINICAL USE · SCIENTIFIC VALIDATION ONGOING**

TractoSphere brings a linked MRI and tractography workspace together with
metadata-aware preprocessing and saved QC. The alpha supports visualization and
editing of existing tractograms; diffusion reconstruction, new tractography
generation and connectome analysis are future capabilities.

![TractoSphere MRI and real tractography workspace](docs/screenshots/viewer-dark.png)

## Try the alpha

Python **3.12 or newer** is required. macOS Apple Silicon is the locally verified
platform. Ubuntu 24.04 ARM64 also has bounded VM/X11 software-rendering validation.
Intel macOS, Linux x86-64 and Windows remain experimental and have no native
runtime verification yet. Windows preprocessing/WSL bridging is unsupported.

```sh
git clone https://github.com/kkartikeya-kulkarni/TractoSphere.git
cd TractoSphere
git checkout v0.1.0-alpha
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install '.[gui]'
dmri-studio gui
```

On Windows activate with `.venv\Scripts\Activate.ps1`. See the
[installation guide](docs/installation.md) for wheel installation, external
scientific tools, troubleshooting and the macOS installation helper.
No AI account, API key, cloud service or MRI download is required to launch.
FSL, MRtrix3 and ANTs are installed separately for the operations that use them.
The first-run setup checker reports exact tool paths, versions and per-operation
requirements. Choose **Continue with viewer only** to begin without scientific
software. In Preferences → **Software Dependencies**, use **Install / Setup** for guided,
platform-specific official downloads, licensing/storage guidance and installation
testing. Completed compatible installations can be verified and configured without
global PATH edits. Downloads open only with explicit consent; installation remains
manual (FSL may require Terminal/IT help). No unattended installers are run.
Recheck through Preferences or run `dmri-studio doctor`. No external
tool is silently installed or changed. Missing acquisition metadata stays missing.

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
