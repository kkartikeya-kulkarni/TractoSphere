# Quick start

1. Follow the [installation guide](installation.md). Mac users can use the
   release helper without Git. Open the app using the launch command for your
   installation and choose **Continue with viewer only** if scientific tools
   are not configured. MRtrix3, FSL and ANTs are not needed for steps 2–6.
2. Use **Image viewer** or **Tractography** to open an anatomical NIfTI without
   creating a preprocessing project. The four-view workspace links the crosshair
   and preserves coded geometry. Open a 4D image to navigate volumes.
3. Add verified matching-space TCK/TRK tracts. Choose direction RGB, lines/tubes,
   opacity and visible fiber count. Rendering decimation does not delete fibers.
4. In **ROIs**, import a binary mask or anatomical atlas. Provide its matching
   table and confirm anatomical space from provenance. Select regions to generate
   their 3D surfaces; Cmd/Ctrl-select multiple regions. Adjust opacity/color.
5. In **Tractography**, choose **Select or Filter Existing Streamlines**. Import
   compatible Viewer ROIs and assign **Must pass through**, **Must avoid** or
   ordered waypoints. For original discrete-label atlases, open **Filter using
   original Viewer atlas** to choose Include ANY/ALL without preassigning roles.
   Confirm tract/reference correspondence, preview the exact plan and approve
   it before running. The source tractogram remains unchanged.
6. Save a workspace to preserve source references, ROI derivatives and display/
   camera state. Keep its source files and assets directory accessible.
7. For preprocessing, first install the needed scientific tools and
   [select/test/save their actual folders](installation.md#which-folders-to-select).
   Then use **Data & imports**, select DWI/bval/bvec/JSON and inspect
   validation. Record processing history only with evidence. In **Preprocessing**,
   select eligible operations, configure inputs and create a command preview.
   Approve the reviewed plan to run in a NEW local output directory.
8. Use **Runs & reports** and **Quality control** to inspect saved outputs, figures,
   metrics and logs. Scientific review decisions are recorded separately.

## Registration and atlas alignment

Open **Registration** and choose the result you need: create a diffusion low-b
reference, align diffusion and T1, align two images, move an atlas or ROI, or
inspect existing evidence. The moving image is transformed toward the fixed
reference image; the arrow in the form shows that direction. A low-b reference
is preparation, not anatomical alignment. Existing imported images can be
selected directly. For EPI-to-T1 alignment, provide the associated T1, its
brain-extracted image and white-matter segmentation.

To move an atlas or ROI, select its original image, a target reference and a
verified source-to-target transform. TractoSphere checks exact spatial
association and uses the supported interpolation for the source type. It does
not guess an inverse or transformation chain. A new output directory is
required. **Validate and Preview Plan** shows the command and approval digest;
execution requires explicit approval. Successful processing leaves anatomical
review pending. **Inspect Result in Viewer** provides overlay, outlines,
checkerboard, single-image and blink comparisons. Formal approval/rejection
requires a supported saved atlas QC bundle; a plausible overlay alone does not
establish scientific acceptance. Specialist transform and resampling operations
remain under **Advanced Registration Tools**.

## Public reference data

The repository contains no MRI arrays. An optional script downloads the CC0
TractoInferno/OpenNeuro ds003900 sub-1006 reference and verifies object checksums:

```sh
python scripts/fetch_viewer_reference.py /path/to/NEW-reference-folder
```

This downloads more than a minimal example: anatomy, DWI, metrics, masks and real
bundles. Check available disk space first. It produces TCK derivatives from three
actual TRK bundles and verifies coordinates; it does not make synthetic fibers.

Open the resulting `derivatives/testset/sub-1006/anat/sub-1006__T1w.nii.gz`, then
`tractography/sub-1006__AF_L.tck` or its original TRK from the same subject.
The supplied WM/GM/CSF masks are useful for ROI tests. Their masks are not an MNI
parcellation or a reconstructed cortical surface.

## Shortcuts and interactions

Cmd/Ctrl+K: command palette. Escape: restore expanded viewport. Slice sliders
and volume slider navigate independently; anatomical labels/coordinates remain
visible. Mouse rotation/pan/zoom in 3D follows the scientific viewer controls.
Cmd/Ctrl picking selects displayed fibers; ROI/rectangle operations can scan all
indexed fibers. The current GUI provides prerequisite/error guidance at each action.

Review docs/limitations.md before interpreting scientific results.
