# Anatomical atlas and ROI guide

Open the matching anatomical reference, then **ROIs → Import anatomical atlas**.
Select .nii/.nii.gz or an FSL XML image entry. A discrete 3D atlas must contain
finite nonnegative integer labels; background 0 is excluded. Region IDs are
preserved. Names come from a matching FSL XML, TSV/CSV, text LUT or JSON Labels.
Without names, the UI displays Region <ID>. Empty regions are omitted with a warning.

For probabilistic atlases, each 4D volume represents one region; volume 0 is a
region, not background. A 3D continuous probability image requires explicit
probabilistic interpretation. Choose a fraction threshold and stored maximum
1 or 100; FSL probabilistic XML uses 100. Thresholded regions may overlap. The
application never splits individual probability intensities into separate labels.

The expandable tree shows names/IDs, color, visibility, mm³ and tracking role.
Hover for full names, voxel counts and source metadata. Selecting generates the
actual mask surface lazily; Cmd/Ctrl-select multiple regions. Use the existing
opacity/color controls for the current region. Opaque surfaces use opacity 1.
Save individual ROI derivatives plus metadata or the complete workspace.

## Alignment

Units, coded qform/sform, affine, dimensions and orientation are inspected.
Conflicting coded transforms are rejected. Matching geometry does not prove
anatomical alignment. Confirm same world space from provenance, or provide an
independently verified source-world → reference-world affine. Known MNI/native
conflicts cannot be bypassed with the same-space checkbox.

Discrete labels use nearest-neighbor sampling; probabilities use linear sampling
before thresholding. Originals stay unchanged. Only the following verified JSON
contract is currently accepted; an identity matrix is not a registration solution:

```json
{
  "schema": "tractosphere.verified-affine/1",
  "verified": true,
  "evidence": "Independent registration verification record",
  "source_sha256": "exact source NIfTI checksum",
  "reference_sha256": "exact reference NIfTI checksum",
  "matrix_rasmm": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
}
```

The flag documents a declaration; it does not independently validate registration.
No automatic registration, nonlinear warps or arbitrary FLIRT/ANTs matrices.

## Filtering existing tractography

In **Tractography → Select or Filter Existing Streamlines**, select a verified
tractogram and reference image. Imported binary ROI snapshots can be assigned to
**Must pass through** (every selected ROI), **Must avoid** (any hit excludes the
streamline), and **Pass through in this order**. Move waypoint rows up or down
to set their actual command order. **Endpoints only** tests the streamline
endpoints instead of full-path intersections. It cannot be combined with ordered
waypoints. Imported ROI snapshots retain their original subject and spatial
association; importing an edited Viewer ROI again creates a new snapshot.

For original discrete-label Viewer atlases, **Filter using original Viewer
atlas** opens the full-source atlas filter. Select named regions there without
first assigning Viewer roles. **Include ANY** creates one verified union mask;
**Include ALL** requires every selected region; **ORDERED** uses the saved
streamline vertex direction, so reversing point order can change the result.
Selecting a region can identify it in the existing Viewer. Edited masks and
unverified atlas/reference grids are rejected by this route. All routes require
exact plan preview and approval before running. Volumetric voxel-cell segment
intersections include crossings between sparse vertices and do not depend on the
screen camera or rendered subset. Boundaries use floor(voxel+0.5) half-open cells.

Export the selected TCK/TRK group to a new path; source fiber IDs/roles are recorded.
Filtering does not generate new tractography or alter original coordinates.

## API and CLI

```python
from dmri_studio.core.scene import ImageVolume
from dmri_studio.core.atlas import Atlas
atlas = Atlas('atlas.nii.gz', ImageVolume('reference.nii.gz'), same_space=True)
regions = list(atlas.regions())
```

```sh
dmri-studio atlas atlas.nii.gz --reference reference.nii.gz --same-space \
  --label-table atlas.tsv --report-json new-report.json --export-dir new-regions
```

See `dmri-studio atlas --help` for probabilities, verified transforms and role
filtering. All output paths must be new. SciPy is required (GUI extra includes it).
Lazy masks share atlas data; only edited regions get a private copy. Large visible
region sets can consume RAM. A region surface is not a cortical reconstruction.
