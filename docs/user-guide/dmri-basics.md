# Diffusion MRI basics for your first session

You can use TractoSphere without knowing every MRI method in advance. First learn what each file represents, then follow the [one-participant walkthrough](walkthrough.md). A dataset must contain the actual acquisition information needed by the step you choose. The application cannot recover an unrecorded acquisition direction or declare an image anatomically correct for you.

## What is being measured?

An MRI image is a three-dimensional grid of tiny boxes called **voxels**. A normal T1 image describes anatomy. A diffusion MRI acquisition measures how the water signal changes when gradients are applied in different directions. The complete diffusion image is usually **4D**: many 3D volumes, one after another. Different volumes may use different diffusion weightings and directions.

| Term you will see | Plain-language meaning | Why you need it |
| --- | --- | --- |
| **DWI** | Diffusion-weighted image, usually a 4D `.nii.gz` file | Main input for preprocessing, tensor fitting and FOD estimation |
| **b0 / low-b** | A volume with no or very little diffusion weighting | Provides a useful anatomical reference and helps image alignment |
| **b-value** | Strength of diffusion weighting, in s/mm² | Defines shells and which volumes a model can use |
| **b-vector** | The applied gradient direction for a volume | Must match the volume and its processing stage |
| **Shell** | Group of volumes with similar b-values | Determines which tensor or FOD models are appropriate |
| **Phase encoding** | Acquisition direction sensitive to geometric distortion | TOPUP/EDDY needs the true direction, readout time and associated calibration images |
| **T1** | Structural image of the same participant | Supports anatomy, tissue segmentation and atlas placement |
| **Mask** | Image marking voxels to include or exclude | Restricts fitting, tracking or correction to a documented region |
| **ROI** | Region of interest: a mask or selected atlas parcel | Can seed, include, exclude or classify streamlines |
| **Atlas / parcellation** | Image whose integer labels name brain regions | Supplies nodes for filtering or a connectome after correct registration |
| **Registration** | Estimating how one image aligns with another | Links T1, DWI, atlas and tractogram coordinate spaces |
| **Transform** | Saved mapping from a moving image into a fixed image | Must be applied in its documented direction |

### A typical input bundle

For one raw DWI, look for a matching set such as `sub-01_dwi.nii.gz`, `sub-01_dwi.bval`, `sub-01_dwi.bvec` and `sub-01_dwi.json`. The `.bval` and `.bvec` files are small text companions, **not** extra brain images. Their volume count must match the last dimension of the DWI. The JSON may contain the acquisition facts needed for distortion correction. A reverse phase-encoded b0 is a separate calibration image when actually acquired. The T1 is usually in an `anat` folder. [See the complete file checklist](files.md).

If a DWI has already undergone motion correction, its matching rotated gradients and producer record may differ from the raw companions. Choose files from the **same processing stage**. A name that looks plausible does not prove they match.

## What will the outputs mean?

| Output | In everyday language | What it does **not** establish |
| --- | --- | --- |
| **Preprocessed DWI** | New diffusion image after selected corrections | That all artifacts were removed |
| **Tensor** | One simple directional diffusion model per voxel | That every voxel contains only one fiber direction |
| **FA** | Dimensionless summary of directional preference in a tensor | Fiber count, tract integrity or a diagnosis |
| **MD / AD / RD** | Different tensor diffusivity summaries | A unique tissue explanation without context |
| **FOD** | Model of possible fiber orientations within a voxel | A verified individual axon or pathway |
| **Streamline / `.tck`** | Computer-generated path following a local orientation model | An observed axon; counts depend on algorithm and settings |
| **Tract-density map** | Image of streamline contributions in each voxel | Axon density |
| **5TT** | Five tissue compartments derived from structural anatomy | Correct GM–WM boundaries without visual review |
| **ACT** | Tracking constrained by accepted tissue anatomy | Automatic anatomical correctness |
| **SIFT2 factors** | Weights assigned to source streamlines to better match a model | Literal axon counts |
| **Connectome matrix** | Numbers relating pairs of atlas regions under an assignment rule | Proven biological connections or clinical meaning |

**GM** means gray matter, **WM** means white matter and **CSF** means cerebrospinal fluid. A cortical GM–WM boundary is the interface between the outer gray matter and underlying white matter. A 5TT review should examine that boundary in axial, coronal and sagittal views before ACT is used.

## The route through the app

```text
Raw DWI + matching bval/bvec + acquisition JSON
                   │
             Import and validate
                   │
       Choose justified preprocessing steps
                   │
           Corrected DWI + gradients
             ┌─────┴─────┐
          DTI/maps      CSD/FOD
                         │
                  Generate streamlines
                         │
         Filter / density / SIFT2 / connectivity

Same-participant T1 → alignment → reviewed atlas and optional 5TT
                                   │
                      ROI filtering / ACT / connectivity
```

You can enter this route halfway through if you have a documented, compatible derivative. You do not need to rerun a completed correction simply to use a later task. After each operation, the common pattern is **choose the exact files, read eligibility, preview the commands, approve the current plan, run into a new folder, open outputs, inspect QC, then decide what is suitable to use next**.

## Three states that are easy to confuse

1. **Visible:** the Viewer can draw a file. This proves only that it can read/display it.
2. **Eligible:** a task has the file plus required companions, metadata and processing history.
3. **Scientifically accepted:** a qualified reviewer has inspected the exact result and recorded a decision with observations.

For a first session, open an image in [the Viewer](viewer.md), then import the matched DWI bundle using [the import screenshots](import-projects.md). Work with a small bounded output first and keep the original files untouched.
