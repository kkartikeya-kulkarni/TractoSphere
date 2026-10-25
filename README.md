# TractoSphere

**A desktop research toolbox for diffusion MRI, tractography and connectomics.**

**Start with the [illustrated Read the Docs guide](https://tractosphere.readthedocs.io/en/latest/).** If diffusion MRI is new to you, read [Diffusion MRI basics](https://tractosphere.readthedocs.io/en/latest/user-guide/dmri-basics/), check [which files you need](https://tractosphere.readthedocs.io/en/latest/user-guide/files/), then follow [one participant from import to connectome](https://tractosphere.readthedocs.io/en/latest/user-guide/walkthrough/). The site also explains [ROI tract filtering](https://tractosphere.readthedocs.io/en/latest/user-guide/tract-filtering/), [connectome generation](https://tractosphere.readthedocs.io/en/latest/user-guide/connectivity/) and [QC review](https://tractosphere.readthedocs.io/en/latest/user-guide/qc-runs/) with native GUI screenshots.

**Download the current [v0.2.0-alpha prerelease](https://github.com/kkartikeya-kulkarni/TractoSphere/releases/tag/v0.2.0-alpha)** or follow the [installation guide](https://tractosphere.readthedocs.io/en/latest/installation/). The release package and `main` contain the same scientific toolbox. TractoSphere has a Python processing core, command-line interface and PySide6/VTK desktop application. It is for research use, not clinical decisions; successful processing does not establish anatomical or biological validity. The Mac download is a Python wheel and install helper, not a standalone `.app`.

## Scientific capabilities

| Area | Available workflows |
| --- | --- |
| Data and preprocessing | BIDS and custom input discovery; gradient and acquisition checks; MRtrix conversion, denoising, Gibbs correction and masking; metadata-gated FSL TOPUP/EDDY; bias correction and preprocessing QC. |
| Reconstruction | MRtrix tensor fitting and FA/MD/AD/RD; response estimation; single- and multi-tissue CSD; FOD normalization, QC and glyph visualization. |
| Tractography | MRtrix deterministic and probabilistic tracking, masks and ROI constraints, tract editing, density maps and statistics. ACT and GMWMI seeding require accepted exact-5TT tissue review. |
| Connectomics | Registered atlas import and review, streamline assignment, count/length/FA matrices, SIFT2 weights, weighted matrices, edge extraction and matrix inspection. |
| Registration | FSL/MRtrix image and atlas transforms, compatible nonlinear workflows, three-plane atlas-on-b0 QC, human review, and batch registration QC. Rejected or changed evidence blocks scientific reuse. |
| Studies | Multi-subject discovery, manifests, previewed plans, bounded batch execution and per-subject QC. Check the [batch guide](https://tractosphere.readthedocs.io/en/latest/user-guide/batch/) for recovery limits. |
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

Python **3.12 or newer** is required. Install the external scientific tools separately for the operations that use them: **MRtrix3**, **FSL** and **ANTs/N4**. FSL has separate licensing terms. The software setup checker reports detected executables and operation-specific version requirements. For the packaged prerelease, follow the [release installation guide](https://tractosphere.readthedocs.io/en/latest/installation/); for a source checkout, use the steps below and the [first-run walkthrough](https://tractosphere.readthedocs.io/en/latest/user-guide/first-run/).

In Settings, open **Software Dependencies** to locate an existing installation and choose **Verify and Save**. TractoSphere remembers compatible MRtrix3, FSL and ANTs folders across launches and supplies them to new applicable workflows; every scientific command still validates its exact inputs and binaries. See [dependency setup](docs/dependency_setup.md).

For macOS or Linux shells:

```sh
git clone https://github.com/kkartikeya-kulkarni/TractoSphere.git
cd TractoSphere
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install '.[gui]'
dmri-studio doctor
dmri-studio gui
```

On Windows PowerShell, create the environment with `py -3.12 -m venv .venv`, activate it with `.\.venv\Scripts\Activate.ps1`, and inspect [platform limits](docs/platforms.md) before scientific processing. `dmri-studio --help`, `dmri-studio scientific --help` and `dmri-studio batch --help` show the installed CLI. A first use can load existing NIfTI images, TCK/TRK tractograms or registered atlases without starting a reconstruction job. Review a selected input and the proposed command before approving new processing. Source data is never intentionally overwritten.

The [installation guide](docs/installation.md) covers the current `v0.2.0-alpha` package and scientific tool setup. Earlier alpha tags remain available for reproducibility.

The Batch Processing workspace has a [guided workflow](docs/batch_processing.md) for data discovery, acquisition selection, supported scientific presets, exact plan approval, monitoring and QC. Advanced Settings retains saved workflow definitions and technical execution controls.

## Quality control and scientific limits

The toolbox records technical validation, numerical checks, native image/atlas review, tracking and connectome QC, and batch findings. A registration decision applies only to its exact evidence. Missing or changed registration evidence requires review or an explicitly identified diagnostic operation. QC warnings remain visible after a command succeeds.

Small tractograms, a limited atlas, successful FSL/MRtrix execution or a visually plausible overlay do not establish research-grade connectomes. SIFT2 depends on adequate tractogram and FOD coverage; weights are not axon counts. Tensor and FOD outputs may contain nonphysical or uncertain estimates, and anatomical registration still needs expert inspection. See [how to check each result](https://tractosphere.readthedocs.io/en/latest/validation/), [testing phase and coverage](https://tractosphere.readthedocs.io/en/latest/user-guide/status/) and [known limitations](https://tractosphere.readthedocs.io/en/latest/limitations/).

macOS Apple Silicon is the primary scientific development and native GUI test environment. The prerelease has bounded Linux/Windows viewer and installation tests, but **the expanded scientific workflows have not been fully validated on Linux, Intel macOS or Windows/WSL2**. FSL is not claimed to run natively on Windows. Cross-platform work follows a separate scientific audit roadmap.

## Development, license and data safety

Run `python -m pytest tests` for the included tests; native scientific integration cases need their external tools and GUI cases need a display/Qt/VTK. The branch carries product source and tests without participant MRI arrays, tractograms, SIFT2 weights or private study records. Public GUI screenshots are documented in [third-party notices](THIRD_PARTY_NOTICES.md). Do not include private data in issues or logs. See [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md) and [LICENSE](LICENSE). The MIT license covers TractoSphere's own code; external scientific programs are installed and licensed separately.
