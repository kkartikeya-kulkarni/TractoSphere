# One participant, start to finish

This is a **decision checklist**, not a claim that all scientific outputs from one participant are valid. Use one authorized participant with raw DWI, matched gradients and acquisition metadata, a corresponding T1, and enough local storage. For a partially processed dataset, start at the first operation justified by its producer evidence; do not replay completed stages to fill a checklist.

## Prepare the folder and source inventory

1. Make a read-only/source folder and a separate local derivative parent. Keep the participant's raw DWI `.nii.gz`, `.bval`, `.bvec`, BIDS JSON, reverse-PE b0 calibration files when correction is intended, T1 and any structural companions together. Write down subject/session/acquisition and their actual source.
2. Open **Software setup**. Verify MRtrix3, FSL and ANTs only for the steps you intend to use. Inspect the reported versions. Viewer-only work needs none of them.
3. **Import data → Import raw DWI**. Choose the exact DWI/bval/bvec/JSON, **Load dataset**, then read **Data validation**. If gradients or metadata do not match, stop and recover the correct companions.
4. **Projects → New project** or **Import BIDS acquisitions**, then activate the exact subject/session/acquisition. This is organization, not preprocessing.

## Produce a diffusion derivative

5. **Preprocessing → Confirm processing history**. Mark only observed prior stages. Choose MP-PCA if appropriate, preview, approve and run to a new folder. Open output/QC. Repeat separately for Gibbs if eligible; inspect the `mrdegibbs` axes.
6. If your acquisition has complete opposite-PE calibration and required metadata, configure TOPUP/EDDY. Review exact commands and run to a separate folder. Inspect diagnostic gate, outliers, masks and rotated gradients. If the plan blocks, retain that result and do not claim a corrected derivative.
7. If the direct EDDY output and passing gate exist, run N4 with its explicit fitting mask and records. Otherwise skip N4 and document why. Import the final chosen DWI **with its matching final gradients** into the scientific inventory.

## Fit models and prepare spatial evidence

8. **Diffusion models → DTI**: select DWI, shells (including b0), fit and mask policy. Preview and run. Open FA, MD, AD, RD and tensor QC; do not interpret invalid flagged estimates as accepted.
9. **Diffusion models → CSD/FOD**: choose the model supported by the actual shells, matching mask and response source/estimator. Preview and run; inspect WM FOD and response/QC. Retain its producer manifest for later tracking/SIFT2.
10. Prepare the participant T1 and candidate brain/WM masks. Review structural coverage. **Registration → Create a Diffusion Reference** from the final DWI; keep its exact low-b reference record.
11. Choose **Align Two Images** or **Align Diffusion and T1** as appropriate, checking moving → fixed direction. Preview/run into a new folder; inspect the registered anatomy in all three planes and record a separate alignment decision. Do not swap an inverse implicitly.
12. Obtain a public atlas with a label table and known template space. Register/move it through supported transforms to participant T1 and then the verified low-b reference as needed. Or use **Review an Existing Registered Atlas** if the atlas is already there and its producer evidence exists. Inspect three-plane QC and save the actual reviewer decision.
13. If ACT is in scope, generate or import a 5TT with the exact alignment evidence. Run `5ttcheck`/compartment views, inspect cortical GM/WM and CSF boundaries in three planes, and save a tissue QC decision bound to the exact 5TT. Only an accepted exact-5TT review enables GM–WM seeds, ACT tracking and ACT SIFT2. The current [same-grid limitation](../limitations.md) remains relevant even for a reviewed individual output.

## Generate tracks, analyze and review

14. **Tractography → Generate New Streamlines**: start with a small bounded count and an explicit mask/seed. Choose standard or ACT only when its evidence is ready. Preview exact `tckgen`, approve and run. Open the TCK over the verified data image in Four views; inspect count, path location, lengths and QC.
15. For an existing atlas or two/three ROIs, choose **Select or Filter Existing Streamlines**. Set ANY/ALL/ORDERED or pass/avoid/waypoint roles explicitly. Preview `tckedit`, run and inspect the new TCK, original count and retained count. Optionally create a tract-density map on the verified reference grid.
16. If desired, run **SIFT2** with the exact source tractogram/FOD and chosen mask policy. Inspect factor histogram and optimizer QC; keep the weight record.
17. **Connectivity**: select the registered atlas, exact low-b/reference, tractogram, optional FA and exact-source weights. Choose metric/assignment/diagonal policies, preview and run. Inspect node table, matrix occupancy, edge values and any unassigned tracks. Extract and view a selected connection if needed.
18. **Quality Control** and **Runs & Reports**: reopen every attempt, check input/product hashes and recorded commands, inspect figures/measurements, and save the responsible reviewer's real decision and limitations. Export only appropriately labeled reports. A successfully built matrix does not establish biological interpretation.

## Repeat one step or scale up

For **isolated preprocessing**, import a separate real derivative with its own gradients and documented history, then select only the eligible operation. For **Batch Processing**, begin with a small acquisition subset and inspect each stage's journal/QC. For a **10M tractogram**, first distinguish Viewer loading, drawing and generating: they have different memory/disk demands. Inspect the generation plan's storage estimate before approval. The [troubleshooting table](troubleshooting.md) lists common blockers.
