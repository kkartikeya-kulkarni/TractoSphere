# View images, tractograms, atlases and ROIs

![Current Image Viewer empty screen with Open Image button](../screenshots/user-guide/18-image-viewer.png)

*A new user sees this empty Viewer. Select Open Image and choose a NIfTI first. The Inspector controls and slice views appear after the image loads.*

The Viewer is for inspection and display. An image opening successfully does not give it a processing history or make an atlas registered. For scientific tasks, import the corresponding typed input and evidence separately.

## Start with a reference image

![Current Image Viewer showing three orthogonal views of a public T1 image and the Image display inspector](../screenshots/user-guide/19-viewer-public-t1.png)

*After opening a NIfTI, inspect axial, sagittal and coronal views. This example uses the public CC0 TractoInferno/OpenNeuro ds003900 sub-1006 T1; it is a display example, not a registration or tissue-QC decision. The panel on the right changes contrast and other display settings without changing the source image.*

1. Choose **Image Viewer → Open Image** (or **Import data → Open image…**) and select a valid 3D/4D `.nii`/`.nii.gz`. The title/status shows image name, shape and spatial transform source. A 4D image has a volume control.
2. Choose **Three orthogonal** or **Four views**. Axial, sagittal and coronal slice sliders move through the source image; click/drag the crosshair to relate a location across planes. Check left/right orientation and the displayed RAS+ millimeter coordinates before judging alignment.
3. In **Inspector → Image display**, set minimum/maximum intensity, gray or another color map and **Auto contrast**. These change display only. **Display sampling** changes display interpolation; it does not resample the source file.
4. In **Image overlays**, add a compatible scalar, label or segmentation image. Adjust the selected overlay's visibility, opacity, color map and range. If its grid differs, establish real world-space correspondence; a plausible screen overlap alone is insufficient.

### Read the image display controls

| Control | What to do | Effect |
| --- | --- | --- |
| **Minimum / Maximum** | Enter an intensity window, or choose **Auto contrast** | Changes brightness/contrast on screen; saved voxels are unchanged. |
| **Color map** | Choose Gray for anatomy or another map when a scalar is easier to distinguish | Changes display colors only. Do not infer tissue type from a color map. |
| **Display sampling** | Review the selected display interpolation | Changes how enlarged or resliced pixels look; it does not create a registered/resampled derivative. |
| **Axial / sagittal / coronal sliders and crosshair** | Move to the same location in three planes; read orientation letters, voxel index and RAS+ mm coordinates | Helps inspect extent and correspondence. A single attractive slice is insufficient for registration QC. |
| **4D volume slider** | Pick a volume of a loaded 4D DWI | Shows one measured volume. It does not remove or select volumes for a model fit. |
| **Camera / anatomical planes** | Choose view direction and visible file-axis planes | Controls the 3D presentation; the camera does not rotate source coordinates. |
| **3D rendering / volume rendering** | Adjust plane opacity, background, lighting, transparency, clipping, threshold and rendering quality | Makes the reference visible around tracks or surfaces. It can be slow on large images; none of these controls validates anatomy. |
| **Image information** | Inspect shape, voxel dimensions, orientation and affine source | Use these facts when checking an overlay; equal dimensions alone do not establish registration. |

To inspect a saved **FOD coefficient image**, choose **File → Open FOD Coefficients…** or the Viewer FOD action, select the exact WM FOD, and check its association with the displayed anatomical reference. Glyph size/density and slice location are display choices. Inspect response/FOD QC in [Diffusion models](models.md), not solely the glyph appearance.

## Add tractography and keep the brain visible

Open **Tractography** in the sidebar, keep the chosen reference image loaded and use **Add Tracts** to select `.tck` or `.trk`. Choose **Four views** to see the background image in three slices plus 3D tracks. The background image is the currently loaded NIfTI, which can be a mean b0, FA or registered T1. To return to an earlier data image, choose **Open Image** and select that exact NIfTI again; do not change the tract source. Check that both files share verified anatomical coordinates before interpreting overlay.

For a visible 3D brain outline, open **Inspector → Volume rendering** or a compatible surface/ROI overlay and adjust its visibility, threshold/opacity and camera. A low-contrast transparent isosurface may look nearly black; increasing opacity/lighting affects display, not data. A T1 intensity isosurface is an anatomical outline, not a reconstructed cortical surface. **Camera** controls view angle and zoom; **Expand** gives the 3D pane more room.

## Tract group controls

Select a group under **Tract groups**. **Selected group visible** toggles drawing. **Appearance** offers lines/tubes, direction RGB or uniform/scalar coloring, opacity, width/diameter and **Display limit**. The status reports total indexed source fibers and how many are drawn in 3D. Lowering the cap does not delete fibers; raising it can slow rendering or exhaust graphics memory. **SIFT2 weight factors** and scalar coloring require matching source associations.

**Select streamlines**, **Edit selection** and **Manage group** operate on explicit selections. Hidden/deleted/cut display edits are not the same as a saved scientific derivative. Export or run the reviewed filter task when you need a new TCK. The current Viewer can index a large file while drawing a bounded subset; do not infer 10M processing throughput from a 10M load.

### Each Tract groups action

| Action | What it changes |
| --- | --- |
| **Lines / Tubes**, width and opacity | Geometry and visibility of drawn fibers only. Tubes cost more graphics memory. |
| **Direction RGB / Uniform / Length / Image scalar / SIFT2 weight** | Coloring rule. Scalar color requires a compatible image; SIFT2 color requires the weight record bound to this exact source and order. |
| **Display limit** | Maximum fibers drawn for this group. Read the status line for **source indexed** versus **drawn** counts. The cap cannot force all 10M to render and is not the filtering input size. |
| **Select tracts intersecting selected ROI**, **Select all loaded in group**, **Clear selection** | Viewer selection. Cmd/Ctrl-click can select a drawn fiber. A selection is not a scientific full-source filter. |
| **Hide selected**, **Show hidden** | Display visibility; source file remains intact. |
| **Delete selected**, **Cut selected at axial plane**, **Undo**, **Redo** | Editable Viewer derivative state. Undo history is bounded. Check which loaded/displayed fibers are affected before exporting. |
| **Export retained loaded tracts** | Saves an explicit Viewer derivative. For a reproducible ROI rule on every source fiber, use [Filter tracks with ROIs](tract-filtering.md). |
| **Filter These Tracks…** | Carries the selected group's **full source file** into the scientific filter task. Review its reference and ROI evidence there. |

## Atlas and ROI display

1. With a matching reference open, import an anatomical atlas from **Atlas & ROIs** or **ROIs → Import anatomical atlas**. Use its real label table where available. The tree shows parcel names/IDs, color, visibility and size. Select a region to build its surface; Cmd/Ctrl-select multiple regions.
2. For a binary ROI, choose **Load ROI NIfTI** or **New empty ROI in image grid**. Draw/erase or apply supported morphology only to an explicit editable derivative. **Save selected ROI as new NIfTI** creates a new file and metadata; it does not rewrite an atlas source.
3. Adjust ROI visibility, opacity/color and 3D surface. A binary mask surface marks a region boundary; it is not cortical reconstruction. For pathway filtering, choose the same-space verified ROIs or original atlas in [the tractography filter task](tractography.md#filter-existing-tracks-using-rois).
4. Use the Viewer registration modes—overlay, outlines, checkerboard, registered-only/reference-only and blink—when inspecting an actual saved registration QC bundle. A normal tissue or scalar overlay does not itself expose registration approval controls in current code.

### Create and inspect a binary ROI

Choose **New empty ROI in image grid** for an editable mask on the displayed image, or **Load ROI NIfTI** for an existing compatible mask. Select the ROI in the tree. **Navigate** moves through the image; **Draw** and **Erase** modify the active ROI, using **Brush radius (voxels)**. **Undo/Redo** reverse supported local edits. **Dilate** expands the region, **Erode** shrinks it, and **Merge visible into new ROI** creates a separate result from the visible ROIs. Inspect the mask in all three planes after any edit. Set **ROI opacity**, **ROI color** and **Selected ROI visible** to see where it lies; **Build / update ROI in 3D** creates a surface for display. Finally choose **Save selected ROI as new NIfTI** and keep the saved image and provenance for later filtering. An ROI edit does not silently change the original atlas.

### Add a surface or overlay

In **Anatomical surfaces**, choose a same-space mask, label image or scalar NIfTI using **Add surface from NIfTI**. A **Label ID** selects one discrete parcel; `0` uses the **Isovalue** threshold. Adjust **Selected surface visible**, color and opacity. A surface extracted from a mask is just that mask's boundary; it is not a measured cortical surface.

In **Image overlays**, add a compatible image and select its row. Set visibility, opacity, color map and intensity range; review layer order and geometry. For a registered result, open its saved QC bundle to use overlay/outlines/checkerboard/blink comparison with the exact fixed reference. If one image seems shifted, check affines and transform direction in [Registration](anatomy-registration.md) rather than dragging it into apparent alignment.

## Save and reopen display state

Save a Viewer workspace when you need camera, selected image, tract groups and ROI display choices later. Keep the referenced source files and workspace assets folder reachable. Loading the workspace restores a view; it does not rerun processing or confer scientific acceptance. A screenshot can document the view, but a QC decision must still be recorded in the relevant evidence workflow.

Use **File → Save Viewer Workspace…** to choose a new workspace file, **Open Viewer Workspace…** to restore it and **Export View…** for a PNG of the current display. A saved workspace may refer to external NIfTI/TCK/ROI files, so moving or deleting them can break restoration. Inspect a reloaded workspace before presenting it as evidence. **Processing Logs** shows application messages; a producer run's exact command and output hashes belong in [Runs & Reports](qc-runs.md#open-runs-reports).
