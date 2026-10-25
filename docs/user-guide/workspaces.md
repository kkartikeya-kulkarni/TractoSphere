# Screen-by-screen map

![Current TractoSphere starting screen showing the left navigation and main workspace](../screenshots/user-guide/01-start.png)

*Every major workspace is on the left. Some scientific tasks are tabs inside Diffusion models or Tractography rather than separate sidebar entries.*

Use this map when you see a control in the sidebar or a task button and want to know where it fits. A visible or imported item is not automatically eligible for processing.

| Screen or control | First action | What to check before leaving | Detailed guide |
| --- | --- | --- | --- |
| **Import data** | Choose raw/preprocessed DWI, image, tractography, scientific input or existing result | File companions, gradient count/layout, validation findings, actual history | [Import](import-projects.md) |
| **Projects** | New/open project or import BIDS acquisitions | Subject/session/acquisition and attached attempts; no assumed AP/PA merge | [Projects](import-projects.md#projects-and-bids-discovery) |
| **Data validation** | Load DWI first, then open its existing input report | Integrity warnings are distinct from scientific output QC | [Import](import-projects.md#import-a-raw-or-already-processed-dwi) |
| **Image Viewer** | Open NIfTI, choose orthogonal/four views, inspect coordinates | Correct background image, orientation, volume and display range | [Viewer](viewer.md) |
| **Tractography Viewer** | Add TCK/TRK to a verified reference | Total indexed vs drawn, ROI/atlas provenance, 3D display | [Viewer](viewer.md#add-tractography-and-keep-the-brain-visible) |
| **Preprocessing** | Confirm history, select an operation | Eligibility, metadata, command, output folder, mask/correction QC | [Preprocessing](preprocessing.md) |
| **Diffusion models → DTI** | Select DWI or existing tensor | Shells, gradients, mask and numerical QC | [DTI](models.md#dti-fit-tensor-then-obtain-fa-and-other-maps) |
| **Diffusion models → CSD/FOD** | Select DWI, mask, model and responses | Shell/model suitability, response associations, FOD QC | [CSD](models.md#csd-estimate-response-and-wm-fod) |
| **Anatomy/5TT** | Generate, validate or inspect 5TT | Exact T1/reference alignment; separate tissue review | [5TT](anatomy-registration.md#five-tissue-anatomy-and-gmwm-seeds) |
| **Registration** | Choose one of six guided goals or Advanced | Moving/fixed direction, exact transform, interpolation, three-plane QC | [Registration](anatomy-registration.md) |
| **Tractography tasks** | Generate, filter or map tracks | Source association, seed/ROI policy, storage, command, QC | [Tracking](tractography.md) |
| **SIFT2** | Select exact tractogram/FOD and mask policy | ACT tissue review if selected; optimizer/weight QC | [SIFT2](connectivity.md#compute-sift2-weights) |
| **Connectivity** | Select tracts, registered atlas and metrics | Accepted atlas evidence or explicit diagnostic state, assignments and occupancy | [Connectome](connectivity.md#build-a-connectome) |
| **Batch Processing** | Discover study/acquisitions | Selected subset, blocked stages, pinned plan/tool identity, journal | [Batch](batch.md) |
| **Quality Control** | Open a producer QC catalog | Figures, measurements, actual reviewer name/reason/decision | [QC](qc-runs.md#open-a-qc-catalog) |
| **Runs & Reports** | Open a saved manifest | Source/product hashes, steps, errors and review state | [Runs](qc-runs.md#open-runs-reports) |
| **Software setup** | Locate, verify and save MRtrix/FSL/ANTs | Executables/versions needed for the chosen operation | [Setup](first-run.md#3-configure-scientific-software) |
| **Preferences** | Set system/light/dark appearance or open Software Dependencies | Appearance does not change scientific image values or analysis parameters; the motion checkbox is currently informational/disabled | [First run](first-run.md#4-learn-the-workspaces) |

**Keyboard and navigation:** Cmd/Ctrl+K opens the command palette; Escape restores an expanded viewport. Slice sliders and 4D volume controls change only the displayed position/volume. In 3D, drag rotates, middle-drag pans and the wheel zooms. Read the Viewer status bar's voxel and RAS+ mm coordinates when checking correspondence.
