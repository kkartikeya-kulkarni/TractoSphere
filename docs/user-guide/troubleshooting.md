# Common blockers and what to check

The app often blocks a task because an input is **visible** but not **eligible**. Read the complete preview message and fix the missing association or evidence. The table gives first checks; it does not authorize relabeling a file with facts that are not known.

| Symptom | Check and next step |
| --- | --- |
| `data.nii.gz` opens, but DTI/CSD cannot start | Find its exact `.bval` and `.bvec` from the same acquisition and processing stage; import it as typed DWI. Check gradient count, shell and history. |
| Loaded DWI absent from a task dropdown | **Import scientific inputs…** or that task's **Import…** action. Some workspaces maintain an active DWI separately; use their **Use imported DWI** handoff where shown. |
| TOPUP/EDDY says metadata/calibration missing | Confirm actual PE direction, readout and slice/acquisition information and associated reverse-PE magnitude b0s. A JSON from another scan is not a valid substitute. |
| Gibbs or denoising is ineligible | Check interpolation, partial-Fourier and processing-order history; review the selected axes/command. Running `mrdegibbs` does not establish suitability for every acquisition. |
| N4 is blocked | Select the **direct** EDDY output with corrected gradients, fitting mask, validated correction record and passing gate. If EDDY diagnostics failed, investigate them first. |
| DTI fit fails with a mask | Inspect masked signal and fitting-domain QC. Do not expand an invalid mask or conceal nonpositive observations. |
| 5TT/ACT is blocked | Check the exact anatomy/reference alignment record, five-channel order and **accepted tissue QC review bound to the exact 5TT**. A T1 alignment review is not tissue acceptance. |
| Already registered atlas is rejected | Use **Review an Existing Registered Atlas** with the exact verified low-b and its producer registration manifest or genuine QC association. Matching shape/affine alone is insufficient. |
| Viewer shows only 10,000 fibers | Inspect **Tract groups** for total indexed source count and **Appearance → Display limit** for drawn count. The rendering cap does not truncate the TCK or the filter input. |
| Tracts appear without a brain in 3D | Open the intended NIfTI background, choose **Four views**, and enable suitable volume rendering or a compatible surface in Inspector. Adjust its opacity/lighting. Verify coordinate correspondence before interpreting the overlay. |
| Google Drive file will not process | Ensure the exact file and companions are fully downloaded to local/suitable storage, then use a new local derivative folder. The app has no remote-data streaming or automatic cloud migration. |
| Plan says insufficient disk | Choose a smaller bounded run or a suitable volume with more free space. A successful 10k run or 10M Viewer load does not prove a 10M-generation plan fits. |
| Resume refuses a batch | Inspect pinned tool and implementation identity in the saved journal. Start **New Batch With Current Software** if code/tools changed; do not rewrite the old plan. |
| Run completed but QC is pending | Open its exact QC catalog and record a separate evidence-backed scientific decision. Technical pass is not acceptance. |

## Before reporting a reproducible problem

Keep the source unchanged. Note the GUI workspace/task, exact app branch/commit, selected input kinds, non-sensitive metadata, plan preview and blocker text, tool versions, output attempt status and the saved QC/run records. Remove participant identifiers and private paths before sharing externally. A screenshot of the full panel plus its status line is usually more useful than a cropped brain image alone.

For known platform and scientific limits, read [Known limitations](../limitations.md). For a missing scientific tool, follow [Software setup](first-run.md#3-configure-scientific-software) and the [dependency guide](../dependency_setup.md).
