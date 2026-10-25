# TractoSphere v0.2.0-alpha — Scientific toolbox

PUBLIC ALPHA · RESEARCH USE ONLY · NOT FOR CLINICAL USE · SCIENTIFIC VALIDATION ONGOING

This prerelease packages the scientific toolbox from `main` as Python package
`0.2.0a0`. It updates the earlier v0.1.1-alpha viewer-focused release; previous
tags and downloads remain available. [Read the illustrated user guide](index.md)
for every workspace and [install the package](installation.md) before using it.

## What is included

- Scientific file inventory, DWI gradient and acquisition validation, project
  discovery, metadata-bound preprocessing plans and output QC.
- Guided DTI metrics, CSD/FOD, 5TT anatomy, image/atlas registration, tractography
  generation and filtering, density maps, SIFT2 and connectome matrices.
- Existing registered-atlas reuse through exact-source provenance and a QC-only
  review route, without requiring another registration run.
- Bounded batch processing, saved runs/reports and image, tract, atlas and ROI
  viewing in the native PySide6/VTK GUI.

Each scientific operation still requires its exact input evidence and a reviewed
command plan. A successful command, technical QC and scientific acceptance are
separate results. See [files and evidence](user-guide/files.md),
[the complete walkthrough](user-guide/walkthrough.md),
[testing phase](user-guide/status.md) and [known limitations](limitations.md).

## Mac installation

Download **TractoSphere-v0.2.0-alpha-macos-source-install.zip** from the
[GitHub release](https://github.com/kkartikeya-kulkarni/TractoSphere/releases/tag/v0.2.0-alpha),
extract it, then follow the [Mac installation steps](installation.md#mac-install-and-open-the-application).
The bundle contains the versioned wheel, checksum file, install helper, license,
notices and guides. The helper verifies the wheel checksum before creating a new
`alpha-0.2.0` Python environment. It preserves earlier alpha installations.

This is a Python environment installation, not a notarized standalone `.app` or
`.dmg`. Python 3.12+ and internet access for GUI dependencies are needed. MRtrix3,
FSL and ANTs are separate external programs, with separate terms and exact
operation-specific version checks. They are not included in this release.

## Scientific and platform scope

The real-data GUI audit exercised public diffusion data through preprocessing,
tensor/FOD models, registration, a small whole-brain tractogram, ROI filtering,
SIFT2 and a diagnostic/reviewed connectome. Many outputs remained
`validated_pending_qc`, meaning technical checks passed while scientific
interpretation remained open. The user separately reported successful loading
of an existing 10M-track file; that is not evidence of 10M-track generation or
complete 10M rendering under the same conditions.

Native scientific GUI evidence is primarily Apple Silicon macOS. Hosted CI
checks package installation and GUI behavior on macOS Intel/ARM, Ubuntu and
Windows, but it does not validate scientific interpretation on every platform.
ACT and GM-WM interface seeding require an accepted exact-5TT tissue review.
Missing acquisition metadata, uncertain atlas correspondence or unreviewed
tissue boundaries remain blockers rather than being guessed or bypassed.

For bugs or questions, use [Issues](https://github.com/kkartikeya-kulkarni/TractoSphere/issues/new/choose)
or [Discussions](https://github.com/kkartikeya-kulkarni/TractoSphere/discussions).
Share sanitized logs and never upload restricted participant data or credentials.
