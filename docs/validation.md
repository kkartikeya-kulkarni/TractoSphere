# Check each result before using it

Use this page while working through one participant. It explains what to inspect **after each GUI operation** and which file to carry to the next step. For the exact buttons and input fields, follow the linked task page. A saved output and a successful command are useful evidence, but the next operation may also need matching source files, acquisition metadata, spatial correspondence or a recorded scientific review.

## The same review sequence for every operation

1. **Confirm the input.** Check participant, session, acquisition, image space and processing history in **Import data** or **Projects**. For DWI, keep its matching `.bval`, `.bvec` and available acquisition metadata together. [Import guide](user-guide/import-projects.md)
2. **Read Data Validation.** Open the DWI input report. Expand warnings, failures and skipped checks; inspect volume and gradient counts. This report checks the imported input, not the result of a later command. [Data Validation guide](user-guide/data-validation.md)
3. **Preview the operation.** In the task screen, select the exact files and parameters, choose a new output folder, and click **Preview commands** or **Validate and Preview Plan**. Read the eligibility message, input/output paths, command, software choice and any scientific limitation. Change an incorrect selection before approving.
4. **Run and inspect the result.** After approving the exact plan, wait for completion. Open the saved image, tracks, atlas or matrix in its Viewer or result inspector. Check more than one slice or measurement where the task requires it.
5. **Open the saved QC.** In **Quality Control → Open Report…**, select that attempt's `qc-catalog.json` or task-specific QC bundle. Read **Findings**, **Figures**, **Measurements** and **Source details**. A green execution state does not erase an automated QC warning. [QC guide](user-guide/qc-runs.md#open-a-qc-catalog)
6. **Check the run record.** In **Runs & Reports**, open that attempt's `manifest.json`; verify the recorded inputs, output hashes, commands and processing step states. Load a verified derivative with its history if you need it for the next operation. [Saved runs guide](user-guide/qc-runs.md)
7. **Record the scientific decision when required.** A qualified reviewer should inspect the exact evidence and save an observation and decision of `accepted`, `needs_review` or `rejected`. Keep the review with the result it identifies. If the input or output changes, review the new derivative separately.

## What to check at each stage

| Stage | Open and inspect | Carry forward only after checking |
| --- | --- | --- |
| **Raw DWI import** | DWI volumes, matching gradients, orientation, acquisition metadata and every Data Validation warning or skipped check | The correct imported DWI and companions; do not infer missing phase-encoding or readout values. [How to import](user-guide/import-projects.md) |
| **Preprocessing** | Denoising residuals, Gibbs output, masks, distortion/motion diagnostics and any bias field for the operations actually run | The verified DWI derivative and its corrected gradients/history. A partially processed input must have its earlier steps declared. [How to preprocess](user-guide/preprocessing.md) |
| **DTI and scalar maps** | Tensor fit QC and the FA, MD, AD and RD maps in anatomical views; read nonfinite, negative or out-of-range findings | Only maps appropriate to the intended analysis. Do not treat an automatically generated FA map as accepted solely because a file exists. [Model guide](user-guide/models.md) |
| **CSD and FOD** | Shell/response selection, tissue responses, WM FOD coverage and reconstruction QC | The FOD associated with the exact DWI, gradients and mask used for tractography or SIFT2. [Model guide](user-guide/models.md) |
| **T1, registration and atlas** | Brain extraction, moving → fixed transform direction, registered image over the fixed reference in axial, coronal and sagittal views, atlas parcel IDs and boundaries | The reviewed T1/low-b association, registered atlas and its exact QC record. An already registered atlas can be reviewed without registering it again. [Anatomy and registration guide](user-guide/anatomy-registration.md) |
| **5TT and GM–WM interface** | Cortical GM, subcortical GM, WM and CSF compartments over the matching anatomy in all three planes, especially the cortical GM–WM border | An exact 5TT with a recorded tissue-boundary review before ACT-dependent operations. A colored overlay alone is not a review decision. [5TT guide](user-guide/anatomy-registration.md#five-tissue-anatomy-and-gmwm-seeds) |
| **Tractography and density** | Source FOD, seed/mask, ACT choice, generated track count, track locations, length distribution and density image | The full saved `.tck`, not merely the display subset in 3D. [Generate tracks](user-guide/tractography.md) |
| **ROI filtering** | Source tract count, each actual ROI mask or atlas ID, ALL/ANY/order/endpoints rule, retained count and tracks over the original reference image | The new filtered `.tck` plus its rule and QC; preserve the unfiltered source. [Filter two or three regions](user-guide/tract-filtering.md) |
| **SIFT2** | Exact tractogram–FOD binding, processing mask policy, weight count/distribution and warnings | The saved weights with their binding record; a bare vector is insufficient for a later weighted matrix. [SIFT2 guide](user-guide/connectivity.md#compute-sift2-weights) |
| **Connectome** | Registered atlas and node table, assignment rule, selected metrics, missing nodes, assigned/unassigned streamlines and matrix cells | The connectome bundle JSON, matrix CSVs, node mapping, assignment records and QC together. [Build and inspect a matrix](user-guide/connectivity.md#build-a-connectome) |
| **Batch processing** | Every participant's input pairing, per-item eligibility, generated plans, individual failures and saved attempt folders | Only verified outputs from each participant; a successful batch summary does not review every image. [Batch guide](user-guide/batch.md) |

## If a step is blocked or looks wrong

Read the exact message on that task's screen and its saved **Findings**. Common causes are a DWI separated from its gradients, missing correction metadata, a tool path that has not been verified, an atlas without exact registration evidence, or a derivative whose source history is unknown. Return to the relevant input or review screen and correct the evidence. Do not relabel a file, invent a processing step or approve an image simply to make the next button available. [Troubleshooting guide](user-guide/troubleshooting.md)

For a full example from first import to matrix inspection, follow [One participant, start to finish](user-guide/walkthrough.md). For the current scope of real-data testing, see [Testing phase and coverage](user-guide/status.md).
