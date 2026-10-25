# TractoSphere v0.1.0-alpha

**First Public Research Preview**

**PUBLIC ALPHA · RESEARCH USE ONLY · NOT FOR CLINICAL USE · SCIENTIFIC VALIDATION ONGOING**

TractoSphere is an open-source diffusion MRI research workstation for preprocessing,
quality control, visualization, tractography, and analysis. This alpha focuses on
reviewed preprocessing and interactive exploration/editing of existing MRI and
tractography; it does not yet generate new tractograms or fit diffusion models.

## Included

- Synchronized axial, sagittal, coronal and 3D NIfTI viewing with 4D navigation.
- Real TCK/TRK groups, RGB/uniform/scalar colors, lines/tubes and camera controls.
- ROI editing, mask/anatomical surfaces, named atlas labels and probability regions.
- Volumetric Include/Exclude filtering, selected-fiber exports and saved workspaces.
- Metadata/history-gated preprocessing using separately installed MRtrix/FSL/ANTs.
- Saved QC/provenance/logs, comparison views and independent scientific review states.
- First-run read-only MRtrix/FSL/ANTs setup, exact executable versions/paths,
  official installation links, viewer-only continuation and `dmri-studio doctor`.
- Guided official scientific software installation: platform/pinned-version
  instructions, consented browser handoff, archive integrity checks and verified
  configuration. No automatic scientific installer or Windows-to-WSL execution.
- No active AI assistant, ChatGPT login, API keys or inference services.

## Installation and downloads

Python 3.12+ is required. The release wheel installs the application; GUI libraries
are installed from upstream by pip. The macOS helper installs into a new local venv.
The source archive includes documentation/tests and an optional permitted-data
fetcher. No MRI arrays or scientific-tool executables are included.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install './dmri_studio-0.1.0a0-py3-none-any.whl[gui]'
dmri-studio gui
```

Use the companion installation guide and SHA256SUMS. This alpha has no notarized
standalone .app/.dmg. macOS Apple Silicon is the verified native installation target;
Linux/Windows source support is experimental.

## Validation and limits

Prior Phase 3 verification: public candidate **762 passed, 2 optional skips** on
macOS; **749 passed, 15 skips** on Ubuntu 24.04 ARM64 in a VM. Phase 4 added
**15 passing security tests**. These suites were reused, not repeated for release.
Phase 5 installed the audited wheel into a fresh isolated Python 3.12 environment
and verified actual native GUI startup/shutdown, real 65-volume DWI, TCK/TRK,
3D atlas regions, ROI filtering, dependency setup and settings persistence.

| Platform | Status |
|---|---|
| macOS Apple Silicon | Verified locally |
| Ubuntu 24.04 ARM64 | Tested in a VM with software rendering |
| Windows | Experimental; native runtime unverified |
| macOS Intel | Runtime unverified |
| Linux x86-64 | Runtime unverified |
| Independent physical Mac | Pending |

The final release rebuild changes repository links and documentation, with targeted
installation/source-integrity checks. No scientific algorithms changed and no
lengthy MRI processing was rerun. See the [historical alpha validation record](https://github.com/kkartikeya-kulkarni/TractoSphere/blob/2a0d339233eb256665ef86b4947d22ed9b44e74d/docs/validation.md).

This small validation scope does not establish clinical suitability or universal
scientific readiness. Known limits include nonlinear atlas registration, automatic
joint acquisition assembly, interrupted EDDY recovery, TinyTrack conversion, new
tractography generation, modeling/connectomes and exhaustive cross-platform/GPU
validation. Missing metadata blocks dependent processing. Successful execution does
not imply valid prediction/CNR diagnostics or accepted anatomical QC.

## Feedback

Use Issues for bugs, installation problems, feature requests and scientific QC;
Discussions for usage/research questions. Include versions and sanitized evidence.
Do not upload identifiable patient data, restricted scans or credentials.

- Repository: https://github.com/kkartikeya-kulkarni/TractoSphere
- Issues: https://github.com/kkartikeya-kulkarni/TractoSphere/issues/new/choose
- Discussions: https://github.com/kkartikeya-kulkarni/TractoSphere/discussions
- License: MIT for TractoSphere's own source; dependencies retain separate terms.
