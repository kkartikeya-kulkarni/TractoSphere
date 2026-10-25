# TractoSphere user guide

This guide follows the **v0.2.0-alpha scientific toolbox** in this repository. It explains the desktop GUI from the first data import through preprocessing, models, registration, tractography, connectivity and review. If diffusion MRI is new to you, begin with [Diffusion MRI basics](user-guide/dmri-basics.md), then use the [file checklist](user-guide/files.md) and [one-participant walkthrough](user-guide/walkthrough.md). The task pages include screenshots of the current native GUI and explain what to select. You can also use one operation on an already processed dataset if its history and source associations are documented.

> **Research use only.** A command finishing, an output passing numerical checks, and an expert accepting an anatomical result are three different events. The app records them separately. Do not interpret a technically completed result as a clinically or scientifically accepted finding.

## A complete route through the GUI

| Order | Open this workspace | What you supply | What you take forward |
| --- | --- | --- | --- |
| 1 | [Software setup](user-guide/first-run.md) | Installed MRtrix3, FSL and ANTs paths as needed | Verified local tool locations |
| 2 | [Import data / Projects](user-guide/import-projects.md) | DWI `.nii.gz`, matching `.bval` and `.bvec`, acquisition metadata; optional T1 | Checked acquisition and explicit processing history |
| 3 | [Preprocessing](user-guide/preprocessing.md) | Raw DWI; correction also needs reverse phase-encoded calibration and metadata | New DWI derivative, corrected gradients, masks, QC |
| 4 | [Diffusion models](user-guide/models.md) | Eligible DWI, matching gradients and mask | Tensor and FA/MD/AD/RD maps; WM FOD and responses |
| 5 | [Registration / Anatomy](user-guide/anatomy-registration.md) | T1, verified low-b reference, masks, transform and atlas as applicable | Aligned anatomy, reviewed atlas and optional 5TT |
| 6 | [Tractography](user-guide/tractography.md) | FOD or eligible DWI, mask/seed, optional accepted 5TT | New `.tck`, filtered `.tck`, density maps |
| 7 | [SIFT2 / Connectivity](user-guide/connectivity.md) | Exact-source tractogram and FOD; reviewed registered atlas; optional FA and SIFT2 weights | Weight file, matrices, assignments and selected connections |
| 8 | [Viewer, QC, Runs & Reports](user-guide/viewer.md) | The saved source and derivative files | Visual inspection, explicit review records, reproducible run history |

Every processing workspace follows the same safety pattern: **select exact files → inspect eligibility → choose parameters → preview the actual commands → approve that exact plan → run into a new output folder → open the result → inspect QC and provenance**. Changing an input or setting invalidates the prior approval. The source files are read in place and derivatives are written separately.

## Testing phase

TractoSphere is in an active testing phase with real public diffusion MRI data. Import, preprocessing, diffusion models, registration, tractography, filtering, SIFT2 and connectivity have all been exercised. Testing continues across datasets, platforms and larger runs. For the current scope of testing, see [Testing phase and coverage](user-guide/status.md); for operation-specific constraints, see [Known limitations](limitations.md).

## Using this documentation site

The source is in `docs/` and the site configuration is `mkdocs.yml`. To preview locally, install `docs/requirements.txt` in a documentation environment and run `mkdocs serve`. Read the Docs uses `.readthedocs.yaml`. The [published guide](https://tractosphere.readthedocs.io/en/latest/) builds from the repository; its version selector and build date identify the published snapshot.
