# DSI-style viewer compatibility matrix

Behavior was reviewed against DSI Studio's official Slices, Tracts, Regions and
Edit documentation: https://dsi-studio.labsolver.org/doc/. No DSI source code is
bundled. This is functional comparison, not an exact parity claim.

| Feature / expected behavior | TractoSphere | Input / test | Remaining limitation |
|---|---|---|---|
| Linked slices and 3D | Axial, sagittal, coronal and VTK scene share position | Real T1/65-volume DWI, affine/navigation tests | Oblique/fallback geometry labeled |
| Anatomical 3D | Mask/label surfaces, volume ray casting, orthogonal planes | Real subject masks/T1 | Not a reconstructed cortex |
| Camera | Rotate/pan/zoom, fit/reset, six directions/isometric, parallel/perspective | Native real scene interactions | Platform/GPU dependent |
| TCK/TRK | Indexed source fibers, multiple groups | Real AF_L, CC, PYT files and independent coordinate checks | TT.gz disabled |
| Fiber styles | Direction RGB/uniform/scalar, lines/tubes, width/diameter/alpha | Real data and rendering assertions | No TSF/SIFT2 or full profiles |
| Bounded rendering | Choose visible count without source changes | Real 239,937-fiber group; source hash checks | Not complete 10M-long-fiber validation |
| Selection/editing | Pick/rectangle/drag plane/angle, hide/delete/cut, undo/redo | Native ROI/Tracts commands, sparse geometry tests | Picks only visible fibers; bounded histories |
| Atlas regions | Grouped IDs/names, probabilities, lazy surfaces, controls | Public JHU local tests, native supplied tissue labels | No implicit registration/nonlinear ingestion |
| ROI tools | Load/create, draw/erase/3D brush/polygon, merge/morphology/stats | Native controls and mask tests | Voxel radii; explicit derivatives |
| ROI tract filtering | ALL/ANY include, exclusion, selected export | Real native AF_L and exact segment tests | Existing tracts only |
| Save/export | New TCK/TRK/ROI files, workspace/camera, PNG | Round-trip/header/coordinate/hash verification | Source references must remain available |
| Modeling/tracking/connectomes | Future work | No operational claim | Not implemented |
| Registration estimation | Future atlas capability | MNI/native refusal tested | Existing verified affine only |

See docs/features.md and docs/limitations.md for complete release scope.
