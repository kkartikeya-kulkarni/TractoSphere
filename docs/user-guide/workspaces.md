# Screen-by-screen map

![Current TractoSphere starting screen showing the left navigation and main workspace](../screenshots/user-guide/01-start.png)

*Every major workspace is on the left. Some scientific tasks are tabs inside Diffusion models or Tractography rather than separate sidebar entries.*

Use this map when you see a control in the sidebar or a task button and want to know where it fits. A visible or imported item is not automatically eligible for processing.

This index accounts for all **14 sidebar destinations** in the current desktop app. It also links the three Tractography tasks, two model panels, six guided Registration goals, seven Batch pages, five Viewer Inspector groups, QC and saved-run actions, and the **37 implemented Advanced algorithms**. Follow the linked page for the file checklist, action sequence, resulting file and review step; use the [testing-phase record](status.md) when deciding what has actually been exercised on real data.

| Screen or control | First action | What to check before leaving | Detailed guide |
| --- | --- | --- | --- |
| **Import data** | Choose raw/preprocessed DWI, image, tractography, scientific input or existing result | File companions, gradient count/layout, validation findings, actual history | [Import](import-projects.md) |
| **Projects** | New/open project or import BIDS acquisitions | Subject/session/acquisition and attached attempts; no assumed AP/PA merge | [Projects](import-projects.md#projects-and-bids-discovery) |
| **Data validation** | Load DWI first, then open its existing input report | Integrity warnings are distinct from scientific output QC | [Read every finding](data-validation.md) |
| **Image Viewer** | Open NIfTI, choose orthogonal/four views, inspect coordinates | Correct background image, orientation, volume and display range | [Viewer](viewer.md) |
| **Tractography Viewer** | Add TCK/TRK to a verified reference | Total indexed vs drawn, ROI/atlas provenance, 3D display | [Viewer](viewer.md#add-tractography-and-keep-the-brain-visible) |
| **Preprocessing** | Confirm history, select an operation | Eligibility, metadata, command, output folder, mask/correction QC | [Preprocessing](preprocessing.md) |
| **Diffusion models → DTI** | Select DWI or existing tensor | Shells, gradients, mask and numerical QC | [DTI](models.md#dti-fit-tensor-then-obtain-fa-and-other-maps) |
| **Diffusion models → CSD/FOD** | Select DWI, mask, model and responses | Shell/model suitability, response associations, FOD QC | [CSD](models.md#csd-estimate-response-and-wm-fod) |
| **Anatomy/5TT** | Generate, validate or inspect 5TT | Exact T1/reference alignment; separate tissue review | [5TT](anatomy-registration.md#five-tissue-anatomy-and-gmwm-seeds) |
| **Registration** | Choose one of six guided goals or Advanced | Moving/fixed direction, exact transform, interpolation, three-plane QC | [Registration](anatomy-registration.md) |
| **Tractography tasks** | Generate, filter or map tracks | Source association, seed/ROI policy, storage, command, QC | [Tracking](tractography.md); [detailed filtering](tract-filtering.md) |
| **SIFT2** | Select exact tractogram/FOD and mask policy | ACT tissue review if selected; optimizer/weight QC | [SIFT2](connectivity.md#compute-sift2-weights) |
| **Connectivity** | Select tracts, registered atlas and metrics | Accepted atlas evidence or explicit diagnostic state, assignments and occupancy | [Connectome](connectivity.md#build-a-connectome) |
| **Batch Processing** | Discover study/acquisitions | Selected subset, blocked stages, pinned plan/tool identity, journal | [Batch](batch.md) |
| **Quality Control** | Open a producer QC catalog | Figures, measurements, actual reviewer name/reason/decision | [QC](qc-runs.md#open-a-qc-catalog) |
| **Runs & Reports** | Open a saved manifest | Source/product hashes, steps, errors and review state | [Runs](qc-runs.md#open-runs-reports) |
| **Software setup** | Locate, verify and save MRtrix/FSL/ANTs | Executables/versions needed for the chosen operation | [Setup](first-run.md#3-configure-scientific-software) |
| **Preferences** | Set system/light/dark appearance or open Software Dependencies | Appearance does not change scientific image values or analysis parameters; the motion checkbox is currently informational/disabled | [First run](first-run.md#4-learn-the-workspaces) |

**Keyboard and navigation:** Cmd/Ctrl+K opens the command palette; Escape restores an expanded viewport. Slice sliders and 4D volume controls change only the displayed position/volume. In 3D, drag rotates, middle-drag pans and the wheel zooms. Read the Viewer status bar's voxel and RAS+ mm coordinates when checking correspondence.

## Scientific workspace tabs

The four sidebar entries **Diffusion models**, **Tractography**, **Registration** and **Connectivity** share one scientific input inventory. Their tab bar exposes **Inputs**, **Advanced algorithms**, **Workflow review**, and the task panels relevant to that workspace. **Inputs** lets you search, inspect, review declarations and remove an inventory reference; removing one never deletes its original file. **Advanced algorithms** lists the exact implemented variants and contracts. **Workflow review** lets you edit or remove tasks, validate a proposal, check command versions, export the proposal and open saved QC. The execution tabs are **DTI execution**, **CSD / FOD**, **Tractography tasks**, **Anatomy / 5TT**, **SIFT2 weighting**, **Connectivity** and **Registration / atlas QC**. Some tabs are hidden when they do not apply to the selected sidebar workspace. [Every registry entry and required file](advanced-algorithms.md) is listed separately.

## Desktop menus and shortcuts

| Menu | Controls | What they do |
| --- | --- | --- |
| **File** | Open Image, Open Project, Import Diffusion MRI, Open Tractography, Open FOD Coefficients | Load or inspect the corresponding file; use [Import](import-projects.md) for scientific eligibility and [Viewer](viewer.md) for display. |
| **File** | Open Viewer Workspace, Save Viewer Workspace, Export View | Restore/save display state or export the current view. Keep linked source files reachable; a screenshot is not a QC decision. |
| **Edit** | Undo, Redo, Preferences | Reverse supported Viewer edits or change display/privacy preferences. Undo history is bounded. |
| **View** | Start, Image Viewer, Tractography, Toggle Sidebar, Toggle Inspector, Processing Logs | Navigate or reveal panes. These do not process MRI data. |
| **Tools** | Commands, Software Dependencies, Quality Control, Diffusion Models, Tractography tasks, Connectivity | Shortcuts to the matching workspace. |
| **Window / Help** | Full Screen; TractoSphere Documentation; Report an Issue | Window display and external documentation/issue links. Check for private paths before sharing an issue. |

Common shortcuts are **Cmd/Ctrl+O** for an image, **Cmd/Ctrl+S** for the Viewer workspace, **Cmd/Ctrl+K** for Commands, **Cmd/Ctrl+B** for the sidebar, **Cmd/Ctrl+I** for the Inspector, and **Cmd/Ctrl+J** for logs. On macOS, standard menu shortcuts may display with Command rather than Control.

## Viewer Inspector contents

| Inspector choice | Functions to look for | Detailed steps |
| --- | --- | --- |
| **Image display** | Contrast minimum/maximum and Auto contrast; gray/hot/viridis map; display sampling; camera orientation and projection; anatomical planes; 3D transparency, lighting, clipping, background; volume rendering; image information | [Viewer](viewer.md#start-with-a-reference-image) |
| **Tract groups** | Multiple loaded groups; visibility, line/tube geometry, direction/uniform/length/scalar/SIFT2 colors, opacity, width and display cap; select/hide/delete/cut, undo/redo, full-source filter and export | [Viewer](viewer.md#tract-group-controls), [full filtering walkthrough](tract-filtering.md) |
| **Atlas & ROIs** | Import original atlas or binary ROI, show named labels, create/draw/erase ROIs, brush radius, opacity/color, morphology, undo/redo, save a new ROI and show its 3D surface | [Viewer](viewer.md#atlas-and-roi-display) |
| **Anatomical surfaces** | Build a surface from an image/mask/label threshold, choose label ID, color, opacity and visibility | [Viewer](viewer.md#add-tractography-and-keep-the-brain-visible) |
| **Image overlays** | Add scalar/label overlays; visibility, opacity, color map, range, order and geometric correspondence; registration QC display modes where a QC bundle is open | [Viewer](viewer.md#start-with-a-reference-image) |

The [batch wizard](batch.md) has seven pages plus Advanced Settings. [Quality Control](qc-runs.md) and [Runs & Reports](qc-runs.md#open-runs-reports) reopen saved evidence. [Software setup](first-run.md#3-configure-scientific-software) and [Preferences](first-run.md#4-learn-the-workspaces) configure the application, not an MRI analysis.
