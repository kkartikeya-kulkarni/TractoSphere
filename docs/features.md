# Alpha feature overview

| Area | Implemented | Inputs and limits |
|---|---|---|
| MRI loading | NIfTI-1/2, .nii/.nii.gz, 3D/4D | Native geometry; invalid affines fail explicitly |
| Viewer | Linked axial/sagittal/coronal/3D; volume/slice controls, overlays, W/L | Visualization does not edit source data |
| Tracts | TCK/TRK, multiple groups, RGB/uniform/scalar colors, lines/tubes | Verified anatomical correspondence required; no silent coordinate flips |
| Tract editing | Select, hide/delete/cut, undo/redo, derivative export | All original fibers preserved; picks operate on displayed subset |
| Anatomy | Mask/label isosurfaces, intensity volume rendering, GIFTI/FreeSurfer geometry | Mask surface is not a cortical reconstruction |
| Atlases | Discrete 3D labels, tables/FSL XML, thresholded 4D probabilities | Known space conflicts refused; verified RASmm affine only |
| ROIs | Grouped regions, colors/opacity, draw/erase/polygon/morphology, save/reload | Changes are explicit derivatives; original atlas remains immutable |
| ROI tract filtering | ALL/ANY inclusion, exclusion, selected-group export | Actual volumetric segment intersections, not screen overlap |
| Native preprocessing | Conversion, MP-PCA, Gibbs, dwi2mask | History/order and acquisition gates apply |
| Scientific input inventory | Typed import, explicit conventions, searchable shared references | Inspection does not establish scientific acceptance; removing a reference never deletes source data |
| DTI / CSD reconstruction | Guided tensor metrics and supported FOD methods with exact-plan approval | Shells, gradients, responses, software and output checks remain mandatory |
| Five-tissue / SIFT2 / Connectivity | Supported tissue operations, exact-source streamline weights and registered-atlas matrices | Real anatomy, ACT, SIFT2 and connectome scientific validation remain open |
| Correction | Supported acquisition-specific TOPUP/EDDY commands | PE/readout/calibration metadata required; failure blocks dependent work |
| Bias correction | MRtrix dwibiascorrect ants, explicit fitting mask and saved field | Applied once to verified pre-N4 input; division/gradient checks |
| Anatomy alignment | Supported extraction/segmentation and rigid/BBR adapters | Structural assumptions must be evidenced; registration separate from native DWI |
| AP/PA combination | Optional separate matched derivative | Full measured pairing, pinned helper and producer gates; PA b0 is not a full series |
| QC | Saved catalogs, figures, numerical results, logs, image comparison, reports/reviews | Producer-specific formats; no automatic scientific acceptance |
| Projects | BIDS discovery, inherited metadata, acquisition identity/history | No automatic joint-acquisition assembly |
| Recovery | Bounded verified native-stage handoff | No automatic interrupted EDDY recovery |
| AI | Absent | No authentication, API keys or inference services |

FSL/MRtrix/ANTs are separate local CLI dependencies. NumPy/NiBabel/SciPy/VTK/Qt
are imported local libraries. No remote inference API or container is needed for
the released workflows. Additional processing engines are future work.

In the current toolbox, open **Preprocessing** even before loading
data to reach its input action and staged history/operation review. **Diffusion
models** offers direct tensor and FOD tasks. The scientific input inventory is
shared with Anatomy, SIFT2, Connectivity and the other scientific workspaces;
explicit source identity and conventions remain required. Specialist controls
are available under Advanced. Saved Runs and Projects offer explicit recent
record actions; neither loads private data automatically at startup. Processing
completion, technical output checks and human scientific review are distinct.
See [how to check each result](validation.md), [testing phase and coverage](user-guide/status.md), and [known limitations](limitations.md)
for test coverage and native verification limits.
