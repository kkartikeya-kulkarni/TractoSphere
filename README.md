# TractoSphere

TractoSphere is a diffusion MRI research workstation with a Python processing core, command-line interface and PySide6/VTK desktop application. This **development branch** contains the scientific modules developed after the public v0.1.1-alpha release. The published release and its tags remain separate. The software is for research use, not clinical decisions; successful processing does not establish anatomical or biological validity.

**New to the GUI or diffusion MRI?** Start with the [publishable user guide](docs/index.md), its [beginner primer](docs/user-guide/dmri-basics.md), [required-files checklist](docs/user-guide/files.md), and [one-participant walkthrough](docs/user-guide/walkthrough.md). The guide uses screenshots from the current native GUI to show the main workspaces and explains exact plan approval, saved outputs and review steps. It describes the current development branch and labels incomplete scientific validation.

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

If an atlas was **already registered**, use Registration → **Review an Existing
Registered Atlas**. Import the exact atlas and its verified diffusion reference,
review the original registration manifest or a hash-bound QC association, then
preview the QC-only plan. This route does not rerun alignment. Inspect its
overlay in all three planes and record a separate anatomical review before a
new Connectivity plan can use it. An older review assertion, matching image
grids or a completed historical connectome cannot supply missing native QC
evidence by itself. Connectivity's **Review atlas registration…** action carries
its selected atlas and reference into that Registration goal.

## Installation and launch

Python **3.12 or newer** is required. Install the external scientific tools separately for the operations that use them: **MRtrix3**, **FSL** and **ANTs/N4**. FSL has separate licensing terms. The software setup checker reports detected executables and operation-specific version requirements. Earlier macOS validation used MRtrix3 3.0.5 and FSL 6.0.7.18; these version observations are not a guarantee for other installations.

In Settings, open **Software Dependencies** to locate an existing installation and choose **Verify and Save**. TractoSphere remembers compatible MRtrix3, FSL and ANTs folders across launches and supplies them to new applicable workflows; every scientific command still validates its exact inputs and binaries. See [dependency setup](docs/dependency_setup.md).

For macOS or Linux shells:

```sh
git clone https://github.com/kkartikeya-kulkarni/TractoSphere.git
cd TractoSphere
git switch audit/end-to-end-usability-2026-10-24
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install '.[gui]'
dmri-studio doctor
dmri-studio gui
```

On Windows PowerShell, create the environment with `py -3.12 -m venv .venv`, activate it with `.\.venv\Scripts\Activate.ps1`, and inspect [platform limits](docs/platforms.md) before scientific processing. `dmri-studio --help`, `dmri-studio scientific --help` and `dmri-studio batch --help` show the installed CLI. A first use can load existing NIfTI images, TCK/TRK tractograms or registered atlases without starting a reconstruction job. Review a selected input and the proposed command before approving new processing. Source data is never intentionally overwritten.

The existing [installation guide](docs/installation.md) describes the released alpha and tool setup. Its release-specific screenshots and package version are historical; this branch is the newer development snapshot.

The development Batch Processing workspace now has a [guided workflow](docs/batch_processing.md) for data discovery, acquisition selection, supported scientific presets, exact plan approval, monitoring and QC. Advanced Settings retains saved workflow definitions and technical execution controls.

## Quality control and scientific limits

The toolbox records technical validation, numerical checks, native image/atlas review, tracking and connectome QC, and batch findings. A registration decision applies only to its exact evidence. Missing or changed registration evidence requires review or an explicitly identified diagnostic operation. QC warnings remain visible after a command succeeds.

Small tractograms, a limited atlas, successful FSL/MRtrix execution or a visually plausible overlay do not establish research-grade connectomes. SIFT2 depends on adequate tractogram and FOD coverage; weights are not axon counts. Tensor and FOD outputs may contain nonphysical or uncertain estimates, and anatomical registration still needs expert inspection. See [scientific limitations](docs/limitations.md) and the stage reports retained in the private development workspace for detailed validation histories.

macOS Apple Silicon is the primary scientific development and native GUI test environment. The public alpha has bounded Linux/Windows viewer and installation tests, but **the expanded scientific workflows on this branch have not been fully validated on Linux, Intel macOS or Windows/WSL2**. FSL is not claimed to run natively on Windows. Cross-platform work follows a separate scientific audit roadmap.

## Development, license and data safety

Run `python -m pytest tests` for the included tests; native scientific integration cases need their external tools and GUI cases need a display/Qt/VTK. The branch carries product source and tests without participant MRI files, tractograms, SIFT2 weights or research-validation screenshots. Do not include private data in issues or logs. See [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md), [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md). The MIT license covers TractoSphere's own code; external scientific programs are installed and licensed separately.
