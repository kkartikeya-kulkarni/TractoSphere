# TractoSphere

TractoSphere is a diffusion MRI research workstation with a Python processing core, command-line interface and PySide6/VTK desktop application. This **development branch** contains the scientific modules developed after the public v0.1.1-alpha release. The published release and its tags remain separate. The software is for research use, not clinical decisions; successful processing does not establish anatomical or biological validity.

## Scientific capabilities

| Area | Available workflows |
| --- | --- |
| Data and preprocessing | BIDS and custom input discovery; gradient and acquisition checks; MRtrix conversion, denoising, Gibbs correction and masking; metadata-gated FSL TOPUP/EDDY; bias correction and preprocessing QC. |
| Reconstruction | MRtrix tensor fitting and FA/MD/AD/RD; response estimation; single- and multi-tissue CSD; FOD normalization, QC and glyph visualization. |
| Tractography | MRtrix deterministic and probabilistic tracking, masks and ROI constraints, tract editing, density maps, statistics, 5TT generation, ACT and GMWMI seeding. |
| Connectomics | Registered atlas import and review, streamline assignment, count/length/FA matrices, SIFT2 weights, weighted matrices, edge extraction and matrix inspection. |
| Registration | FSL/MRtrix image and atlas transforms, compatible nonlinear workflows, three-plane atlas-on-b0 QC, human review, and batch registration QC. Rejected or changed evidence blocks scientific reuse. |
| Studies | Multi-subject discovery, manifests, dry-run plans, bounded execution, pause/cancel/retry/recovery, per-subject and study QC. |
| Visualization | Linked MRI views, FOD glyphs, tractograms, atlas/ROI surfaces, label selection, ROI filtering and connectivity heatmaps. |

The GUI, CLI and Python APIs share the scientific processing engine. Commands are previewed before execution, results use new derivative directories, and provenance stores parameters, tool versions, hashes and QC. Scientific review is separate from command success.

## Installation and launch

Python **3.12 or newer** is required. Install the external scientific tools separately for the operations that use them: **MRtrix3**, **FSL** and **ANTs/N4**. FSL has separate licensing terms. The software setup checker reports detected executables and operation-specific version requirements. Earlier macOS validation used MRtrix3 3.0.5 and FSL 6.0.7.18; these version observations are not a guarantee for other installations.

For macOS or Linux shells:

```sh
git clone https://github.com/kkartikeya-kulkarni/TractoSphere.git
cd TractoSphere
git switch development/scientific-toolbox-2026-10
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install '.[gui]'
dmri-studio doctor
dmri-studio gui
```

On Windows PowerShell, create the environment with `py -3.12 -m venv .venv`, activate it with `.\.venv\Scripts\Activate.ps1`, and inspect [platform limits](docs/platforms.md) before scientific processing. `dmri-studio --help`, `dmri-studio scientific --help` and `dmri-studio batch --help` show the installed CLI. A first use can load existing NIfTI images, TCK/TRK tractograms or registered atlases without starting a reconstruction job. Review a selected input and the proposed command before approving new processing. Source data is never intentionally overwritten.

The existing [installation guide](docs/installation.md) describes the released alpha and tool setup. Its release-specific screenshots and package version are historical; this branch is the newer development snapshot.

## Quality control and scientific limits

The toolbox records technical validation, numerical checks, native image/atlas review, tracking and connectome QC, and batch findings. A registration decision applies only to its exact evidence. Missing or changed registration evidence requires review or an explicitly identified diagnostic operation. QC warnings remain visible after a command succeeds.

Small tractograms, a limited atlas, successful FSL/MRtrix execution or a visually plausible overlay do not establish research-grade connectomes. SIFT2 depends on adequate tractogram and FOD coverage; weights are not axon counts. Tensor and FOD outputs may contain nonphysical or uncertain estimates, and anatomical registration still needs expert inspection. See [scientific limitations](docs/limitations.md) and the stage reports retained in the private development workspace for detailed validation histories.

macOS Apple Silicon is the primary scientific development and native GUI test environment. The public alpha has bounded Linux/Windows viewer and installation tests, but **the expanded scientific workflows on this branch have not been fully validated on Linux, Intel macOS or Windows/WSL2**. FSL is not claimed to run natively on Windows. Cross-platform work follows a separate scientific audit roadmap.

## Development, license and data safety

Run `python -m pytest tests` for the included tests; native scientific integration cases need their external tools and GUI cases need a display/Qt/VTK. The branch carries product source and tests without participant MRI files, tractograms, SIFT2 weights or research-validation screenshots. Do not include private data in issues or logs. See [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md), [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md). The MIT license covers TractoSphere's own code; external scientific programs are installed and licensed separately.
