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
