# Every scientific algorithm in the GUI

The guided pages are the easiest route for a first-time user. **Advanced algorithms** exposes the same registry in more detail; it does not bypass input checks or authorize a result. Each row below corresponds to an implemented algorithm in the GUI registry checked for this guide. Version and input gates are checked again when a plan is prepared.

## How to use Advanced algorithms and Workflow review

1. Open **Diffusion models**, **Tractography**, **Connectivity**, or **Registration**. The selected workspace determines which registry entries appear in **Advanced algorithms**.
2. Click an algorithm and expand **Technical adapter roles, versions and QC contract**. Read the required and optional input types, supported software version, parameters and expected QC.
3. Choose **Import required input…** for missing exact-source data, then **Configure task…**. The task form asks for typed inputs and explicit parameter values. A file shown in the inventory is not automatically eligible.
4. Open **Workflow review**. Select tasks to **Edit task…** or **Remove task**. Use **Validate proposal**, inspect compatibility and QC requirements, **Check command versions** for installed binaries, and **Export proposal…** if you need a review copy. **Open existing QC** opens saved evidence. A proposal is not a run.
5. Use the corresponding guided execution panel to choose a new output directory, preview the exact command, approve it and run. Keep the producer manifest with the output. The [common run sequence](walkthrough.md) explains this handoff.

The shared **Inputs** tab lets you search imported scientific items, **Inspect selected input**, **Review declarations…** when permitted, or **Remove inventory reference**. Removing a reference does not delete its source file. **Open selection…/Save selection…** restore or save the inventory/task selection; the referenced files must still exist. **Use loaded DWI** offers the currently loaded acquisition to these tasks, but does not invent missing processing history.

## Diffusion models: all eight entries

| Algorithm shown in Advanced algorithms | Required scientific files | What it creates or checks | Guided explanation |
| --- | --- | --- | --- |
| **Diffusion tensor estimation** (`dwi2tensor`) | Eligible DWI with matching bval/bvec; optional binary mask | Six-component tensor and fit QC | [DTI](models.md#dti-fit-tensor-then-obtain-fa-and-other-maps) |
| **Tensor metrics from existing tensor** (`tensor2metric`) | Verified tensor with declared component convention | FA, MD, AD, RD and other tensor maps | [DTI](models.md#dti-fit-tensor-then-obtain-fa-and-other-maps) |
| **WM response · Tournier** | Eligible DWI and mask | WM response for a suitable single-tissue model | [CSD](models.md#csd-estimate-response-and-wm-fod) |
| **WM/GM/CSF responses · Dhollander** | Eligible DWI and mask | Separate WM, GM and CSF responses | [CSD](models.md#csd-estimate-response-and-wm-fod) |
| **WM response · Tax** | Eligible DWI and mask | WM response through the Tax estimator | [CSD](models.md#csd-estimate-response-and-wm-fod) |
| **Single-shell WM CSD** (`dwi2fod.csd`) | DWI, matching WM response and mask | WM FOD coefficients | [CSD](models.md#csd-estimate-response-and-wm-fod) |
| **Three-tissue MSMT-CSD** (`dwi2fod.msmt_csd`) | DWI, WM/GM/CSF responses and mask | WM FOD plus GM/CSF tissue outputs | [CSD](models.md#csd-estimate-response-and-wm-fod) |
| **Three-tissue intensity normalization** (`mtnormalise`) | Associated WM FOD, GM, CSF and mask | Normalized tissue outputs | [CSD](models.md#csd-estimate-response-and-wm-fod) |

Choose the response method and model that the actual shells support. The displayed estimator is an option, not a claim that it suits every acquisition. Never mix response files from a different participant or processing stage without a supported, reviewed association.

## Tractography and anatomy: all fifteen entries

| Algorithm | Required scientific files | What it creates or checks | Guide |
| --- | --- | --- | --- |
| **iFOD2 · FOD tracking** | WM FOD and tracking mask | Probabilistic tractogram | [Generate tracks](tractography.md#generate-a-bounded-tractogram) |
| **iFOD1 · FOD tracking** | WM FOD and tracking mask | Probabilistic tractogram using a different sampler | [Generate tracks](tractography.md#generate-a-bounded-tractogram) |
| **SD_Stream · FOD tracking** | WM FOD and tracking mask | Deterministic tractogram | [Generate tracks](tractography.md#generate-a-bounded-tractogram) |
| **Tensor_Det · DWI tensor tracking** | Eligible DWI with gradients and mask | Deterministic tensor-based tractogram | [Generate tracks](tractography.md#generate-a-bounded-tractogram) |
| **Tensor_Prob · DWI tensor tracking** | Eligible DWI with gradients and mask | Probabilistic tensor-based tractogram | [Generate tracks](tractography.md#generate-a-bounded-tractogram) |
| **iFOD2 · FOD tracking · ACT** | WM FOD, exact accepted 5TT and reference | Anatomically constrained iFOD2 tractogram | [5TT and ACT](anatomy-registration.md#five-tissue-anatomy-and-gmwm-seeds) |
| **iFOD1 · FOD tracking · ACT** | WM FOD, exact accepted 5TT and reference | Anatomically constrained iFOD1 tractogram | [5TT and ACT](anatomy-registration.md#five-tissue-anatomy-and-gmwm-seeds) |
| **SD_Stream · FOD tracking · ACT** | WM FOD, exact accepted 5TT and reference | Anatomically constrained deterministic tractogram | [5TT and ACT](anatomy-registration.md#five-tissue-anatomy-and-gmwm-seeds) |
| **Filter existing streamlines** (`tckedit`) | Source TCK, compatible reference and reviewed ROI rules where used | New filtered TCK; original unchanged | [Full filtering walkthrough](tract-filtering.md) |
| **Tract-density image** (`tckmap`) | Source tractogram and compatible reference grid | Density image | [Density](tractography.md#create-a-tract-density-map) |
| **SIFT2 streamline weighting** (`tcksift2`) | Exact-source tractogram, compatible WM FOD and reviewed reference | Streamline weight factors | [SIFT2](connectivity.md#compute-sift2-weights) |
| **FSL five-tissue segmentation** (`5ttgen.fsl`) | Same-participant structural anatomy and reviewed diffusion reference | Candidate five-tissue image | [Anatomy/5TT](anatomy-registration.md#five-tissue-anatomy-and-gmwm-seeds) |
| **Verify existing five-tissue image** (`5ttcheck`) | Existing 5TT and matching reference | Technical tissue checks; no new anatomical approval | [Anatomy/5TT](anatomy-registration.md#five-tissue-anatomy-and-gmwm-seeds) |
| **Inspect five tissue compartments** (`5tt2vis`) | Existing 5TT and matching reference | Visualization derivative for compartment review | [Anatomy/5TT](anatomy-registration.md#five-tissue-anatomy-and-gmwm-seeds) |
| **GM–WM interface seed image** (`5tt2gmwmi`) | Exact reviewed 5TT and reference | Seed image for eligible ACT tracking | [Anatomy/5TT](anatomy-registration.md#five-tissue-anatomy-and-gmwm-seeds) |

For tracking, choose an explicit seeding method (dynamic FOD, image/mask, selected ROI, world-space sphere, random/grid per voxel, or accepted GM–WM interface), count, mask and optional pathway regions. **ACT is a distinct mode** requiring the exact accepted 5TT tissue review. A tractogram may be technically produced while anatomical pathway review is still pending.

## Connectivity: both entries

| Algorithm | Required scientific files | What it creates or checks | Guide |
| --- | --- | --- | --- |
| **Structural connectome** (`tck2connectome`) | Tractogram, reviewed registered atlas/parcellation and reference; optional FA and exact-source weights for selected metrics | Matrix, node/edge assignments and QC | [Connectivity](connectivity.md#build-a-connectome) |
| **Extract selected node pair** (`connectome2tck`) | Saved connectome with its source association | New TCK for the selected matrix edge | [Connectivity](connectivity.md#build-a-connectome) |

The metric, assignment method, radius/search limit, diagonal and symmetry policy are part of the result's meaning. See the [metric table](connectivity.md#build-a-connectome) before comparing matrices.

## Registration: all twelve entries

| Algorithm | Required scientific files | What it creates or checks | Guided goal |
| --- | --- | --- | --- |
| **Derive preprocessed low-b reference** | Eligible preprocessed DWI and gradients | Verified diffusion reference plus selection record | Create a Diffusion Reference |
| **Registered atlas QC on preprocessed diffusion** | Existing registered atlas and exact low-b reference | QC-only bundle; no re-registration | Review an Existing Registered Atlas |
| **Rigid / affine registration** (`flirt`) | Moving and fixed images | Transform and registered image | Align Two Images |
| **EPI to T1 boundary registration** (`epi_reg`) | EPI/b0, T1, brain-extracted T1 and WM segmentation | EPI→T1 transform and result | Align Diffusion and T1 |
| **Structural nonlinear registration** (`fnirt`) | Moving/fixed structural images and verified initial transform | Nonlinear transform and result | Advanced registration |
| **Invert verified transform** | Existing transform | Opposite-direction transform with recorded source/destination | Advanced registration |
| **Compose matched transform chain** | First and second compatible transforms | One composed mapping | Advanced registration |
| **Convert linear transform to MRtrix** | Verified transform | MRtrix-compatible linear transform | Advanced registration |
| **Resample scalar** | Continuous image, target reference and correct-direction transform | Registered scalar image using appropriate interpolation | Move an Atlas or ROI / Advanced |
| **Resample labels** | Discrete atlas labels, target and transform | Registered labels with nearest-neighbor treatment | Move an Atlas or ROI / Advanced |
| **Resample ROI** | Binary ROI, target and transform | Registered ROI mask | Move an Atlas or ROI / Advanced |
| **Resample probability** | Probability image, target and transform | Registered probability image | Move an Atlas or ROI / Advanced |

**Inspect a Registration** is the sixth guided goal: it opens an existing result/QC for comparison and review, so it is not a thirteenth processing algorithm. The five other goals map onto one or more rows above. For every transform, read **moving → fixed** direction and apply only to compatible source/target spaces. A plausible image overlay is not itself a saved registration decision; see [Registration and atlas review](anatomy-registration.md).
