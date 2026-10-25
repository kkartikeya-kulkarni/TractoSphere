# Files and evidence checklist

The app does not infer scientific identity from a filename alone. Collect the **exact matching files** for the operation you intend to run. A BIDS folder can help discovery, but the selected acquisition and its metadata still need review.

For example, one BIDS participant may have a set resembling this (names vary by dataset):

```text
study/
  dataset_description.json
  sub-01/ses-01/dwi/
    sub-01_ses-01_dir-AP_dwi.nii.gz
    sub-01_ses-01_dir-AP_dwi.bval
    sub-01_ses-01_dir-AP_dwi.bvec
    sub-01_ses-01_dir-AP_dwi.json
    sub-01_ses-01_dir-PA_epi.nii.gz       # only if actually acquired
    sub-01_ses-01_dir-PA_epi.json         # matching reverse-PE metadata
  sub-01/ses-01/anat/
    sub-01_ses-01_T1w.nii.gz
    sub-01_ses-01_T1w.json
```

The `dir-PA_epi` example is calibration, **not** automatically a full PA diffusion series. Confirm its image contents and association with the AP DWI. A partly processed DWI may instead live under `derivatives/`; use that derivative's corrected gradients and producer record, not the raw filenames by habit.

| Data or task | Minimum files | Additional evidence when applicable |
| --- | --- | --- |
| View MRI | 3D or 4D `.nii`/`.nii.gz` | A valid spatial affine; matching reference for overlays |
| Import DWI | 4D DWI `.nii`/`.nii.gz`, `.bval`, `.bvec` | BIDS JSON for phase encoding, readout and other acquisition facts; documented processing history if already processed |
| TOPUP/EDDY | Eligible DWI with matching gradients; associated reverse phase-encoded **magnitude low-b** calibration NIfTI files | Actual phase-encoding directions, readout time, acquisition association and slice information from BIDS/producer evidence; FSL installation |
| N4 bias correction | Direct eligible EDDY derivative and its rotated gradients | Explicit fitting mask, validated EDDY record, passing correction-gate JSON and ANTs/N4 |
| Tensor/FA/MD/AD/RD | Eligible DWI with gradients and selected shells | Optional compatible fitting mask; MRtrix tensor if calculating maps without refitting |
| CSD/FOD | Eligible DWI with gradients, selected shells and compatible binary mask | WM response; GM and CSF responses for three-tissue MSMT-CSD, or a supported response-estimation choice |
| T1 preparation | Same-subject T1 image | Preparation history and optional associated MP2RAGE INV2/other input if the selected anatomy route requires it; FSL |
| Diffusion reference | Preprocessed DWI and matching gradients | Correction/processing history evidence; low-b threshold and selection policy |
| Registration | Identified moving and fixed images | Exact source/target space IDs; structural brain/WM evidence for EPI-to-T1; supported transform for resampling |
| Existing registered atlas QC | Discrete-label atlas `.nii.gz`, exact verified low-b reference | Saved registration producer manifest or hash-bound QC association; label table recommended; human review remains separate |
| 5TT and ACT | Reviewed aligned T1 or imported five-channel 5TT, exact diffusion reference | Hash-bound anatomical alignment review, exact 5TT tissue QC acceptance, supported masks/FOD; see [current grid limit](../limitations.md) |
| Tracking | WM FOD plus compatible mask/seed, or eligible DWI for tensor tracking | Accepted 5TT for ACT; source subject/space association; local free disk space |
| ROI filtering / density | Existing `.tck`/`.trk`, compatible reference | Binary ROI/atlas with verified same-space provenance; density requires an output reference grid |
| SIFT2 | Exact-source tractogram, compatible WM FOD, reviewed reference | Explicit processing mask or accepted ACT 5TT if selected; weight/source association is checked |
| Connectivity | Exact-source tractogram, registered parcellation and reference | Atlas QC review; FA image for FA metrics; exact-source SIFT2 weights for weighted metrics; node labels/table |
| Batch | BIDS root, custom mapping JSON, CSV/TSV acquisition table or saved study | Every subject/acquisition's needed files and a new batch output location |

## Important matching rules

1. **DWI volumes and gradients:** every DWI volume needs one b-value and one b-vector. If a correction rotates gradients, pair its output DWI with the **corrected** gradient files, not the raw companions. For an ambiguous 3-volume 3×3 table, select its actual layout in the import form.
2. **Subject and space:** a common shape or affine is not proof that two files belong to one subject or are anatomically registered. Record explicit subject/session, acquisition and coordinate-space identity. The app may reject an imported derivative until its producer evidence is attached.
3. **Atlas labels:** use an integer-valued 3D parcellation and its real label table (TSV/CSV/FSL XML/JSON as supported). Do not blur discrete labels with continuous interpolation. A probabilistic atlas is a different input type and uses thresholded probability channels.
4. **Five tissues:** the channel order is cortical GM, subcortical GM, WM, CSF, pathology. A valid channel sum or `5ttcheck` result does not by itself approve tissue boundaries. An ACT/GM–WM decision must identify the exact 5TT bytes.
5. **Provenance:** keep producer manifests, correction gates, registration records, anatomical reviews and QC catalogs next to the derivatives they describe. Replacing or editing a file changes its hash and can invalidate a prior review.

## If you have only `data.nii.gz`

That is not enough to fit DTI/CSD or run EDDY. Find the matching `.bval` and `.bvec` from the same acquisition and processing stage, plus its JSON/metadata when correction needs them. If they are missing, use the image in the Viewer while you retrieve the companions. Do not borrow gradient files from a similarly named scan. [Import instructions](import-projects.md) explain the form.
