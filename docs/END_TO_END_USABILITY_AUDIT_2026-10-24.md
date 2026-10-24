# End-to-end scientific usability audit — active checkpoint, 24 October 2026

This is an **in-progress audit**, not product or scientific acceptance. Baseline:
`ec2c53d`, clean, on `development/tractography-filter-viewer-repair-2026-10-24`.
Work is isolated on `audit/end-to-end-usability-2026-10-24`. The prior
Registration, Tractography, Phase 7 EDDY, public-development and release branches
remain in place. No research source was changed; no EDDY, TOPUP, 10M-streamline,
ACT, real registration or real connectome processing was started.

The local Phase 1–7, real-data, Registration and Viewer-filter branch tips are
all ancestors of this audit branch; no older checkout replaced their work.
Handoffs consulted: [Phase 0](PHASE0_AUDIT_AND_HANDOFF.md),
[Phase 1](PHASE1_IMPLEMENTATION_AND_HANDOFF.md),
[Phase 2](PHASE2_IMPLEMENTATION_AND_HANDOFF.md),
[Phase 3](PHASE3_IMPLEMENTATION_AND_HANDOFF.md),
[Phase 4](PHASE4_IMPLEMENTATION_AND_HANDOFF.md),
[Phase 5](PHASE5_IMPLEMENTATION_AND_HANDOFF.md),
[Phase 6](PHASE6_IMPLEMENTATION_AND_HANDOFF.md),
[Phase 7 EDDY](PHASE7_EDDY_SCIENTIFIC_VALIDATION.md),
[usability](USABILITY_AUDIT_FIXES_2026-10-24.md),
[real data](REAL_DATA_FUNCTIONAL_AUDIT_2026-10-24.md),
[Registration repair](REGISTRATION_WORKFLOW_REPAIR_2026-10-24.md), and
[Viewer filtering repair](TRACTOGRAPHY_FILTER_VIEWER_REPAIR_2026-10-24.md).

## Evidence and status vocabulary

### Why viewing works while processing stays blocked

- Opening a file in Viewer proves it can be displayed. Scientific pages need a
  typed import, an explicit source identity and the relevant companion files.
- The HCP-style DWI is now recognized with same-folder gradients, but a new
  reference/registration plan still needs documented correction history and
  appropriate structural inputs.
- The old atlas review and node display are real evidence. A later Stage 8a
  run also produced an exact-reference, image-only native QC bundle for that
  atlas. The saved Connectivity selection did not link to this bundle, and
  its human anatomical decision is still pending. The guided route can open
  the existing bundle or prepare QC-only evidence without rerunning registration.
- Processing tools may be installed while an operation remains blocked by
  evidence; cloud-backed inputs also require verified local staging. The UI
  should state each missing item at the action, not leave a disabled button
  without a recovery path.

**PASS** means the named technical check passed on this branch. **BLOCKED**
means the application correctly refused a missing or conflicting scientific
precondition, or an identified product defect prevents progress; the reason is
given. **UNTESTED** means no current evidence for that step. **PRIOR** is dated
evidence from the linked handoffs, not a new pass. Execution success does not
mean anatomical or scientific acceptance. Every operation has these checkpoints:
source import → shared inventory → eligibility → exact plan → approval → run →
immutable output → Viewer/QC → provenance/reopen. For cloud input, local staging
and hash verification precede planning. For interrupted processing, cancellation
and verified resume/retry are separate checks.

The current real-data scope is the previously authorized HCP-style DWI/T1 and
existing derivatives described in [the real-data audit](REAL_DATA_FUNCTIONAL_AUDIT_2026-10-24.md).
Private paths, participant records and arrays remain outside Git. A saved
T1/DWI/FOD selection was reimported read-only with matching content IDs. The
current companion suggester selected the same-folder `bvals` and `bvecs` for
the real `data.nii.gz`, without claiming acquisition or scientific acceptance. The
old 23-node atlas review is present and its ten evidence-file hashes currently
match. A later Stage 8a output contains a generated mean low-b reference record,
a hash-bound atlas/reference association and a native atlas QC bundle. On this
checkpoint, `verify_bundle` validated all 46 evidence files, and `review_status`
returned `NEEDS_REVIEW`. The bundle has zero registration commands. Neither
the legacy review nor the native bundle is automatic anatomical approval. The
[38-item scientific backlog](SCIENTIFIC_VALIDATION_BACKLOG.md)
remains open (15 P0, 20 P1, 3 P2); Phase 7 EDDY experiments are separate.

A fresh read-only replay of that saved real connectome selection returned
`blocked`, `execution_ready=false`, with the precise error
`Atlas alignment unresolved: registration review NEEDS_REVIEW/MISSING`.
It created a proposal only, in the private audit workspace. Compatible MRtrix3
and FSL tools were found by the local dependency doctor; ANTs/N4 was missing.
That software result does not resolve the atlas evidence block.
The saved historical Connectivity selection declares a legacy
`registration_record`, but neither its atlas nor its original b0 reference
points to the later Stage 8a `registration_qc`, `qc_association` or
`reference_record`. This is an inventory/evidence-linking gap. The separate
Stage 8a bundle binds the same atlas SHA-256 to a newer generated mean low-b
reference and preserves its relation to the original b0 with a reviewed,
hash-bound association. Its anatomical decision is still pending. The legacy
review says `spatial_review=reviewed`, notes no transform applied or header
rewrite in that inspection, and leaves scientific acceptance unassigned.

## Operation acceptance matrix

Each row names the acceptance path and present evidence/gap. Synthetic test
passes only cover the particular test, not all controls or real anatomy.

| Operation | Import → inventory → eligibility | Plan → approval → run → output | Viewer, QC, provenance, recovery | Current status and next check |
| --- | --- | --- | --- | --- |
| Individual DWI import, including HCP `data.nii.gz` | Unique same-folder `bvals`/`bvecs` suggested; gradient count and frame validated; active Project DWI and scientific inventory must both be explicit | No processing on import | Reopen source IDs and companion hashes | **PASS** read-only real saved selection; **PRIOR** real 288-volume load. Native picker and cross-workspace propagation remain **UNTESTED** on this checkpoint. |
| T1/scalar/mask/ROI/atlas import | Type and declarations visible even when a role is ineligible | No inferred contrast, transform, correction or acceptance | Review declarations reinspects unchanged source | **PRIOR** real T1/atlas and offscreen routes. Exact native T1 review, atlas/ROI spatial association **UNTESTED**. |
| Project load and save/restore | Source and gradients propagate to Project/Preprocessing; selection restores exact IDs | No plan until task chosen | Source hashes checked on restore | **PASS** T1/DWI/FOD real restore; Project DWI live handoff **PRIOR**. |
| Data Validation | Header, affine, dimensions, gradients, acquisition metadata, warnings | Failed checks prohibit dependent plans | QC findings retained without auto-repair | Core tests pass; complete real acquisition review and native warning recovery **UNTESTED**. |
| Viewer MRI/tract/atlas | Display selection stays distinct from scientific import; full tract source and reference retained | Viewer actions cannot approve a scientific plan | Scene preservation, edits/undo, source hashes, native rendering | Default 10,000-fiber drawing cap is separate from source indexing and full-source filtering. Targeted Cocoa Viewer filtering passed (26-test combined suite); actual 10M-file load/render performance and manual 2-group review **UNTESTED**. |
| Preprocessing conversion, denoise, Gibbs, mask | DWI and gradient/history order validated | Exact MRtrix plan and new local derivative; approve and run asynchronously | Output hashes, stage QC and cancellation | Synthetic/core coverage; real processing **UNTESTED**. No EDDY/TOPUP started. |
| TOPUP and EDDY | PE/readout, calibration and source history required | Bounded plan and explicit approval only; no expensive real run here | Residual/CNR and `eddy_quad` evidence; interrupted EDDY recovery | **BLOCKED** pending Phase 7 scientific controls, metadata and authorized bounded experiment. |
| N4/bias and correction QC | Correct pre-N4 image and mask/history required | Exact plan; reject repeat correction | Field, mask, numerical and anatomical review | Synthetic coverage; real scientific acceptance **UNTESTED**. |
| DTI fit and existing tensor metrics | DWI/gradients or six-component tensor conventions visible | Exact `dwi2tensor`/`tensor2metric` preview; separate approval | FA/MD/AD/RD images, numerical QC, provenance | Existing real tensor preview **PRIOR**; real metric execution/output **UNTESTED**. |
| Response estimation and CSD/FOD | Shells, WM/GM/CSF responses, SH convention and masks associated | Exact native commands and reviewed output directory | FOD QC, Viewer glyphs and provenance | Real selections technically eligible **PRIOR**; scientific output validation **UNTESTED**. |
| Anatomy/5TT/ACT | T1 contrast, segmentation, tissue channels and source correspondence | Exact plan; no FA-as-T1 substitution | Tissue QC and anatomical review before tracking | Eligibility repair **PRIOR**; no reviewed real 5TT/ACT in bounded set, therefore real workflow **BLOCKED**. |
| Registration: create diffusion reference | Preprocessed DWI, validated gradients and correction history | Low-b indices/threshold shown before approval; new reference only | Recorded indices, hashes, immutable result | Synthetic native regression **PRIOR**; real DWI correction history missing for this new attempt, so **BLOCKED**. |
| Registration: EPI to T1 | Distinct EPI, T1, brain-extracted T1, WM segmentation, identities | Exact direction/cost/software; approve | Three-plane alignment and human review | Real T1 selectable after documented declaration **PRIOR**; distinct structural evidence missing, **BLOCKED**. |
| Registration: two images | Distinct identified 3D moving/fixed images | Rigid/affine plan and approval | Transform provenance and native overlay review | Synthetic tests; real contrast/space relation **UNTESTED**. |
| Registration: move atlas/ROI | Source, target and transform convention/direction bound to images | Interpolation and command preview; approve | Label preservation, transform/output QC, human review | Existing standalone matrix lacks complete direction and source binding; real plan **BLOCKED**. |
| Registration: advanced nonlinear/BBR | Required fixed/moving anatomy, masks and tool-specific warp/segmentation contracts | FNIRT/BBR parameters, exact native commands and approval | Jacobian/coverage checks plus visual registration review | Controlled synthetic/core routes pass; real HCP prerequisites and native result inspection **UNTESTED**. |
| Registration: advanced transform import/invert/compose/convert | Exact source/target images, direction, convention, software/version and evidence | Typed plan; reject same-image or ambiguous transforms | Immutable transform record, reverse/chain direction and output checks | Core distinct-image guard retained; historical matrix alone remains **BLOCKED**. Native advanced-dialog and real interoperability **UNTESTED**. |
| Registration: existing registered atlas QC | Exact atlas plus verified low-b reference and hash-bound producer or association | QC-only plan has **zero registration commands**; approval yields new immutable bundle | Full-slice overlay, reviewer decision, current hashes; review can become stale | Guided route added on this branch. Existing Stage 8a native bundle and reference record verified (46 evidence files); anatomical decision **NEEDS_REVIEW**. Reuse the saved bundle. |
| Tractography generate | FOD, mask/ACT/GMWMI, geometry and source identity | Exact `tckgen` plan; bounded approval and async execution | Tract count, geometry, QC, Viewer | Synthetic and targeted native prior coverage; real generation and 10M scale **UNTESTED**. |
| Tractography filter and ROI constraints | Selected Viewer group maps to full original TCK/TRK source; compatible ROIs | TRK conversion explicit; exact `tckedit` preview/approval | Filtered/original Viewer, QC, hashes and cancellation | **PASS** current bounded real 1,000-source-track/5-output-track technical run and verified reopen; targeted Cocoa Viewer suite passed. Real anatomical ROI review remains **UNTESTED**. |
| Tract density/statistics | Compatible full tractogram/reference | Exact density/stat plan and approval | Density Viewer scene preservation, QC | Synthetic coverage; native scene preservation and real mapping **UNTESTED**. |
| SIFT2 | Exact tractogram and FOD, mask/ACT evidence | `tcksift2` preview, approval and new weights | Weight distribution, hash-bound source, QC | No reviewed real weights in bounded set; scientific test **BLOCKED**. |
| Connectivity count/length/FA/weighted metrics | Tracts, atlas, reference, optional FA and exact weights; nodes inspectable | Plan requires current atlas registration eligibility; separate approval and execution | Matrix, occupancy, edge extraction, QC, provenance | Real atlas's 23 nodes visible **PRIOR**; saved selection is **BLOCKED** because it does not link to the existing native QC bundle, whose human decision remains **NEEDS_REVIEW**. No real connectome run in this audit. |
| Batch BIDS/custom discovery and mapping | One HCP-style custom acquisition found **PRIOR**; incomplete roles surfaced | Per-subject plan, pinned tool paths and consent | Journal, pause/cancel/retry/resume, Viewer/QC handoff | Synthetic/core coverage; real discovery only, native seven-step walkthrough **UNTESTED**. |
| Runs, reports and QC reopen | Saved manifests/products verified by hashes | No new computation for inspection | Review status persists; report and export checked | Existing tract run and 11 hashes/QC **PRIOR**; current native end-to-end reopen **UNTESTED**. |
| Settings and scientific software | Executables and versions explicitly detected; saved paths do not confer eligibility | Each plan revalidates exact tools | Missing tool path stated at relevant operation | Existing profile had no saved paths **PRIOR**; current native settings workflow and real ANTs **UNTESTED**. |
| Google Drive/local staging | Streaming/File Provider paths detected; a local verified copy is required | Planning must reject streamed MRI and process only local output | Restore integrity, partial failure and no deletion authorization | Core storage checks exist; app has no general Drive staging wizard. Native File Provider/download/hydration timing **UNTESTED**. |

### Cross-cutting acceptance still required

| Concern | Exact acceptance | Present evidence |
| --- | --- | --- |
| Cancellation | Request once, show pending state through safe read checkpoints, leave no publishable partial result, preserve originals | Registration and tract conversion targeted tests **PRIOR**; current broad core passed. Real DWI/Viewer native cancellation **UNTESTED**. |
| Resume/retry | Reload journal/manifest, verify historical source/tool hashes, skip only verified products and use new immutable attempts for failures | Batch core tests passed; native multi-subject pause/resume/retry and EDDY interruption **UNTESTED**. No automatic EDDY resume is claimed. |
| Performance | Measure true cold start, HCP hydration/import, native file pickers, MRI/large tractogram Viewer and bounded memory; display actual progress | Warm prior samples exist; current real saved-selection restore and small filter succeeded. Cold/native scale numbers **UNTESTED**. |
| UX and accessibility | Every blocked action names the missing role/evidence and a direct recovery action; mouse, keyboard, focus, 960×640, light/dark, error retention and result return | Focused Cocoa automation passed; complete human navigation and screenshots **UNTESTED**. |
| Provenance | Source/sidecar hashes, tool versions, plan digest, exact decision, output hashes and QC survive reopen; stale evidence invalidates reuse | Core/CLI suite and bounded real filter reopen passed; real native atlas decision and downstream connectome provenance **BLOCKED**. |

### Why a 10M-track source shows 10,000 fibers

Viewer's **Display cap / group** defaults to 10,000 and has an upper control
limit of 1,000,000. It restricts the number of fibers drawn in the VTK scene,
not the number accepted from a TCK/TRK file. The full source is indexed with
read-only mapped access, and the Viewer-to-filter handoff uses the original
tractogram path rather than the displayed subset. Rendering millions of lines
or tubes at once can overwhelm graphics memory and interaction speed. The UI
now labels source, retained and drawn counts separately. Indexing a true 10M
file still scans its records and may be slow or resource-intensive; no 10M
load/performance acceptance was completed here. A Viewer that remains on
“Loading” or reports an error has a different defect from a successful load
that draws 10,000.

## Confirmed root causes and repair order

1. **P0, scientific continuity:** the prior registered atlas has a verified
   Stage 8a native QC bundle, but the saved Connectivity selection does not
   reference it. Open that bundle, review actual anatomy, record the human
   decision, and attach the reviewed bundle to an exact atlas/reference
   selection. The guided route is reachable from Connectivity; do not rerun
   registration or repeat QC merely because the older selection omitted a link.
2. **P0, workflow completion:** current full native Registration, Viewer and
   Batch handoffs plus real-file picker checks are needed. Automated Cocoa
   tests and a researcher-led visual/anatomical decision are distinct.
3. **P1, connected state:** recheck each import → shared inventory → eligible
   selector → exact plan. Preserve source IDs and explain missing declarations
   in place. HCP companion suggestion and Viewer filter handoff were repaired
   on predecessor branches and must remain in the regression set.
4. **P1, storage and dependencies:** choose verified local staging before
   processing cloud-backed files; test tool paths and operation-specific missing
   software messages. Do not copy or delete protected data automatically.
5. **P1, science and scale:** continue the 38-item register, especially EDDY
   diagnostics, real 5TT/ACT/SIFT2/connectome, native Viewer performance and
   cross-platform validation. No default science policy changes here.

## Native researcher acceptance checklist

Use a normal interactive Mac Terminal and this branch's final HEAD. Record
screenshots privately with source names and participant identifiers obscured.
For each screen, use both light/dark appearance and a 960×640 window; confirm
buttons, keyboard focus, cancellation feedback and result reopening.

1. Import the documented HCP-style `data.nii.gz`; confirm `bvals`/`bvecs`
   suggestions, correct volume/shell count, Project DWI, Preprocessing and
   DTI/CSD inventory. Preview only; do not start EDDY/TOPUP.
2. Import the T1 and atlas. Review only documented identity declarations.
   Confirm T1 eligibility messages, separate EPI/T1 prerequisites and atlas
   node inspection. For the historical atlas, open the saved Stage 8a native QC
   bundle, inspect the original registration reference/transform evidence and
   all three planes, then record an actual anatomical decision. Link the bundle
   through Data Import → Review declarations and select its exact generated
   low-b reference. Rerun Connectivity preview only after a current review.
3. Open two tract groups in Viewer. Filter the selected full source with
   documented ROIs, preview exact commands and a small derivative, then reopen
   filtered tracks, original tracks, QC and run details. Test cancellation once.
4. Walk each scientific page, Batch discovery/plan/history and saved Runs/QC.
   Record disabled controls and exact error text, then verify any repaired state
   updates after import/review without restarting the application.
5. Test an **authorized copy** of a cloud-backed file: verify local size/hash,
   stage to new local storage, preview there, and confirm the streamed path is
   refused. Do not alter or delete the cloud original.

Focused commands at final HEAD (test names imply only their explicit scope):

```sh
cd ~/Projects/dmri-studio2.0/TractoSphere-publication
git status --short --branch
PYTHONPATH=src QT_QPA_PLATFORM=cocoa OPENBLAS_NUM_THREADS=1 FSLDIR="$HOME/fsl" \
  ../application/.venv/bin/python -m dmri_studio gui
```

Run the automated Cocoa regressions from a separate Terminal after closing
the manual app window:

```sh
cd ~/Projects/dmri-studio2.0/TractoSphere-publication
PYTHONPATH=src QT_QPA_PLATFORM=cocoa OPENBLAS_NUM_THREADS=1 FSLDIR="$HOME/fsl" \
  ../application/.venv/bin/python -m pytest -q \
  tests/gui/test_registration_guided_native.py \
  tests/gui/test_registration_role_recovery.py \
  tests/gui/test_registration_import_review.py \
  tests/gui/test_registration_gui.py \
  tests/gui/test_tracking_guided_native.py \
  tests/gui/test_tracking_gui.py::test_tracking_gui_execution_and_result \
  tests/gui/test_roi_workflow.py::test_native_dialog_filter_full_source_and_return \
  --tb=short
```

## Checkpoint verification

Before changes, a broad core/CLI suite returned **995 passed, 2 failed**. The
two failures were old Batch/Registration fixtures that used one image as both
transform source and target, now forbidden by the spatial contract. The
fixtures were corrected to use a distinct synthetic reference; the two
specific tests then passed, and the same broad current-code core/CLI suite
returned **997 passed in 92.27 s**. The focused offscreen
Registration/Connectivity GUI set passed **20/20**. A targeted Cocoa suite
covering Registration, Tractography and Viewer filtering returned **26 passed
in 97.93 s**, and the two directly affected guided Registration Cocoa tests
passed again after the final UI wording change (**2 passed in 4.48 s**).
The full Cocoa GUI suite ran **351 tests in 24 min 11 s: 348 passed, 3 failed**.
All three failures were stale expectations in synthetic GUI tests: a tooltip
still expected unfinished Reconstruction/Connectivity, DWI fields were expected
to ignore the new companion suggester, and a T1 fixture omitted explicit
scientific identity. The assertions and fixture were updated; the four affected
GUI files then passed **44/44 on Cocoa in 88.46 s**. This is targeted regression
closure, not a second full 351-test pass. These are automated checks, not human
anatomical acceptance.

A subsequent read-only search located the separate Stage 8a atlas QC bundle.
The current core verified its 46 evidence files, confirmed an exact match to
the legacy 23-node atlas hash, and returned `NEEDS_REVIEW`. Its manifest is
`validated_pending_qc`, with `human_qc=pending` and zero registration commands.
The saved Connectivity selection lacks a link to this bundle. This corrects
the earlier checkpoint's claim that a native bundle/reference record was
missing altogether. No decision record was created or changed in this audit.

A read-only preview of a saved real tract-filter selection compiled four exact
MRtrix commands (`tckedit`, `mrconvert`, `tckinfo`, `tckstats`) for a new local
derivative. The approved bounded attempt read an existing 1,000-track TCK,
published five tracks and seven outputs, returned `validated_pending_qc`, and
recorded `inputs_unchanged=true`. `inspect_run` reopened the manifest with the
same status and output count. Its output is in the private audit workspace,
separate from the source directories. This is technical execution, not
anatomical ROI, tractography or scientific acceptance.

The approved real-data command preview, with private source/output paths
redacted, was:

```text
tckedit <existing-1000-track-source.tck> <new-filtered.tck> -minlength 10 -maxlength 200 -number 5 -nthreads 2
mrconvert <existing-fa.mif> <new-reference.nii.gz> -datatype float32 -nthreads 2
tckinfo <new-filtered.tck> -count -nthreads 2
tckstats <new-filtered.tck> -dump <new-lengths.txt> -nthreads 2
```
