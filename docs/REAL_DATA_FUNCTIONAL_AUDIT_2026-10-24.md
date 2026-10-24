# Real-data functional audit — 24 October 2026

This audit began from clean post-audit commit `670755a` on the local branch
`development/real-data-functional-audit-2026-10-24`. It used bounded project
data roots, saved application folder settings and existing scientific run
records. A detailed, mode-restricted audit outside the repository records the
exact private paths, individual attempts, source sizes and outcomes. No MRI
image, gradient, participant record or private absolute path is tracked here.
No source data or scientific processing defaults changed.

## Scope and actual data

The available reference set contains an HCP-style preprocessed 288-volume DWI
with b-values and b-vectors, an ACPC-style T1 image, a brain mask, tensor and
FA/MD derivatives, a WM FOD, three tissue response files, a registered-atlas
candidate, and a tractogram with ROI masks in an existing validation run.
These are **existing research/validation files**, not generated fixtures for
this audit. The T1 file's bytes also appear in an existing local preservation
record; that record establishes byte preservation, not anatomical alignment.
No complete real 5TT, hash-bound SIFT2 weight record, or complete raw BIDS
cohort was established within the bounded roots. Test-generated examples of
those types do not substitute for real scientific validation.

Thirteen representative real files passed `import_artifact` inspection without
source writes: T1, FA, MD, b0 reference, binary mask, atlas labels, tensor,
WM FOD, WM/GM/CSF response files, tractogram and ROI. The DWI with its two
gradient files also passed the scientific importer. An offscreen `MainWindow`
imported 12 representative inputs and refreshed the live scientific inventory:

| Real input | Observed offscreen propagation | Boundary |
|---|---|---|
| DWI with gradients | DTI and CSD input selectors | Correction history and scientific approval still require review. |
| WM/GM/CSF responses | Corresponding CSD tissue selectors | Matching acquisition/shell provenance still requires validation. |
| WM FOD | Tractography and SIFT2 source selectors | FOD/tractogram association is not established by selection. |
| Tractogram | Tractography, SIFT2 and Connectivity selectors | Spatial compatibility remains a planner check. |
| ROI and mask | ROI inventory; mask choices in DTI, CSD, Anatomy, Tractography and SIFT2 | ROI/reference correspondence remains unverified. |
| Atlas and FA | Connectivity parcellation and FA selectors; the selected real atlas displayed 23 original node rows | A matching grid or readable node table alone never establishes atlas registration. |
| T1 | Imported Registration list | Initially ineligible for Reference T1 and 5TT generation pending explicit declarations. |

The Project/Load Data form also loaded the actual 288-volume DWI, populated
Preprocessing's active context, and passed that same loaded acquisition to
DTI/CSD through **Use loaded DWI**. No EDDY/TOPUP or other expensive MRI
processing was run. An explicit custom mapping of the non-BIDS HCP-style
folder discovered one acquisition with unique DWI/bval/bvec roles and no
discovery findings through both the core service and offscreen guided Batch
controls; the Review Subjects table showed one row. A bounded BIDS root with
no supported DWI returned zero
acquisitions; a derivative-style root was rejected as requiring an explicit
manifest and provenance. These are discovery observations, not plan approval.
Saving and restoring a private selection containing the real T1, DWI and FOD
reinspected all three originals and preserved their content IDs and source
fingerprints. The selection record stayed outside the repository.

An existing completed tractography manifest reopened through `ResultsPage`
with one output and 11 verified hashes. Its QC catalog reopened through
`QCPage`; both interfaces retained **scientific review required**. No Viewer
rendering, human review or output acceptance was inferred.

## Demonstrated defects and repairs

| Priority | Defect observed with real inputs | Repair and proof |
|---|---|---|
| P1 | Imported non-anatomical artifacts could not have missing declarations revised in the inventory without reimporting. | Inventory now offers **Review declarations…** for an unused draft artifact. The same original files and sidecars are reinspected, the old record is replaced only after success, and approvals are invalidated. Synthetic atlas/DWI regressions and the real T1 offscreen review passed. |
| P1 | A real FA map was importable, but the import dialog omitted FA, MD and mean-b0 contrast values already recognized by Registration. | Added those explicit choices; no contrast is inferred. A synthetic FA import became a supported Registration candidate only with declared source identity/history. |
| P1 | A contrast-only image could appear in generic Registration choices despite missing subject/acquisition/space IDs, then fail at planning. | Guided image-role selectors now require these core spatial fields and state the missing declarations while keeping the imported item visible. Exact core plan checks remain authoritative. |
| P1 | A real FA map appeared as a source for **Generate 5TT with FSL**, although `anatomical_input_qc` rejects FA. | That selector now requires explicit T1w contrast and excludes FA metric inputs. The page identifies imported, blocked filenames and directs users to review declarations or choose another image. Mask inputs remain optional according to the chosen extraction method. |
| P1 | A real atlas was selectable under Connectivity's **Registered parcellation** field although no native registration QC bundle was declared. | The atlas remains available for node inspection, but Connectivity now explicitly identifies missing declared QC evidence and directs the user to Registration. Exact atlas/reference review remains enforced by the core planner. The real atlas showed 23 node rows and the warning in offscreen Qt. |

The reported HCP-style T1 was reproduced: read/inspection and hash recording
succeeded, the imported list retained it, and the Reference T1 selector
correctly explained missing T1w contrast and subject T1 structural-space
evidence. In an **offscreen** Import dialog, explicit in-memory declarations
based on the dataset's documented HCP-style ACPC structural context caused
reinspection of the unchanged original and populated Reference T1. This is
provisional selector eligibility. It does **not** prove that this specific
image is anatomically aligned to any EPI, brain extraction, segmentation or
atlas, and no `epi_reg` plan was approved or executed.

## Verification and limits

- Offscreen Qt: 39 focused Registration/import/usability tests passed; 9
  input-recovery tests passed after the Anatomy change. The latter are separate
  runs and overlap prior suites. Three Connectivity recovery tests passed after
  its warning change.
- Core/offscreen: 40 input-recovery, Batch discovery and scientific-foundation
  tests passed before the Anatomy change; the affected GUI tests were rerun
  afterward.
- Final integrated offscreen gate at application code `d825d9c`:
  **87 passed, 1 deselected in 108.41 s** across the focused GUI, Batch
  discovery and scientific-foundation files. The deselected test constructs a
  native VTK Viewer. An initial broader run reached that test and exited 139
  in `QVTKRenderWindowInteractor` under offscreen Qt; the excluded native
  rendering behavior is unverified, not a demonstrated application regression.
- Real-data checks used the application's importer, Project loader, scientific
  inventory, guided selectors, custom Batch discovery, Results and QC services.
  The detailed private register has one record per attempted operation.
- The user's normal Mac Terminal ran 15 targeted native Cocoa GUI tests at
  committed HEAD `8da3091`: **15 passed in 41.27 s, exit 0**. These include
  synthetic click-driven import/review routes for Registration, Anatomy,
  DTI/CSD, SIFT2, Tractography and Connectivity, plus the existing scientific
  Viewer route/state test. This is native automated GUI evidence, not a
  researcher-led real-data visual inspection or scientific validation. The
  user also invoked `dmri-studio gui`, but supplied no visual observations or
  screenshot from that launch. The restricted Codex command environment
  itself still has zero available Qt screens. Native real-data file-picker,
  layout, registration inspection and output handoff checks remain open.
- The current local QSettings profile contained no saved MRtrix3/FSL/ANTs
  paths. Scientific software setup and exact command approval were therefore
  not exercised. No full real-data DTI/CSD/ACT/SIFT2/Connectivity plan or
  execution was claimed.
- A standalone linear-transform file was present, but no verified transform
  record establishing direction/software/reference identity was found in the
  bounded reference set. The strict import gate was left intact.
- Real raw DWI, complete 5TT, reviewed SIFT2 factors, and multi-subject BIDS
  cases still need suitable data/evidence; absence is only established for the
  bounded roots searched, not every volume on the Mac.

The [38-task scientific backlog](SCIENTIFIC_VALIDATION_BACKLOG.md) and
[Phase 7 EDDY protocol](PHASE7_EDDY_SCIENTIFIC_VALIDATION.md) are unchanged.
This usability audit does not close their scientific validation tasks. Prior
native Cocoa results in the [post-audit handoff](USABILITY_AUDIT_FIXES_2026-10-24.md)
apply to earlier application commits; the 15 tests above are targeted native
evidence for this audit branch.

## Remaining audit work

1. Complete researcher-led native Cocoa file-picker, real image/tractogram
   Viewer and layout checks on this committed branch; the 15 automated tests
   establish only their named synthetic interactions.
2. Establish documented subject/space/processing associations for the selected
   real reference files, then validate actual DTI/CSD/Registration/Connectivity
   plans without accepting scientific quality merely from successful commands.
3. Obtain suitable reviewed 5TT, ACT and SIFT2 records before testing their
   real-data downstream eligibility. Keep Phase 7 EDDY experiments separate.
4. Complete researcher-led navigation and recovery walkthroughs for every
   workspace, including Batch result to Viewer to QC.

This is a **bounded functional checkpoint**, not a claim that every scientific
workflow or native interaction has been verified.

## Follow-up: current real-data eligibility and propagation

A further read-only check used existing DTI and CSD selection records from the
bounded validation directories. TractoSphere **reimported the original
sources and matched the saved artifact IDs** for one DTI fit/metrics selection,
one response-estimation selection and one multi-tissue CSD selection. Their
current `build_workflow` proposals contained zero eligibility errors. Each
remains `review_required`: software identity, acquisition assumptions and
scientific quality were not established by a proposal. An offscreen scientific
page populated the DWI, mask and WM/GM/CSF response selectors from the CSD
selection. This confirms actual derivative-to-workspace propagation, without
rerunning reconstruction.

A historical connectome plan supplied a tractogram, atlas, reference and FA
map. The current importer matched all four saved artifact IDs. Offscreen Qt
populated the tractogram, atlas and FA selectors and displayed the atlas's 23
node rows. The **current planner blocked the old plan** because the atlas lacks
a native, hash-bound registration review bundle. The interface displayed the
missing-QC explanation and directed the researcher to Registration. Prior
execution records and matching grids do not replace the current registration
review requirement. No atlas was silently approved.

The reported HCP-style T1 remains technically imported and visible in the
Registration inventory. The bounded anatomical folder contained the image and
a standalone matrix, but no sidecar that independently establishes T1 contrast,
processing history or its relationship to a selected diffusion reference. The
existing **Review declarations** route can make a documented ACPC structural
T1 selectable without reimporting its bytes; this is a researcher declaration,
not anatomical QC or permission to infer a transform. Its subsequent EPI/T1
plan, structural prerequisites and native visual alignment remain unverified.

### Demonstrated additional defect and correction

The existing six-component tensor derivative imported successfully, yet the
guided DTI panel only exposed a DWI-fit selector. Researchers had to enter the
technical Algorithms form to use the already implemented `tensor2metric`
operation. DTI now offers **Calculate maps from an existing tensor** as a
separate task. Its selector lists imported tensors; DWI-specific shell and fit
controls are hidden for this task. Incompatible FSL-order/frame tensors remain
visible with an explicit explanation and a declaration-review or validated
conversion path. Configuring a compatible tensor creates the same core
`tensor2metric` task and retains exact command preview and approval. No tensor
conversion or fit is inferred. The real derivative reached this selector and
produced a `review_required` proposal with zero errors; no metric command ran.
The correction also preserves the optional mask choice when the input
inventory refreshes. The application/test correction is local commit
`631bf2d`.
With the installed MRtrix tools, `prepare_dti` also compiled the real tensor's
exact metrics and eigenvalue commands under a new private proposed output
name. It created **no output directory** and ran no MRI command. The private
record retains the digest and command-step evidence. Exact command compilation
is not numerical output or scientific QC verification.

The focused offscreen Qt/core suite passed **59 tests, with two native
Viewer-dependent tests deselected**. After the final visibility adjustment,
the two directly affected DTI GUI tests passed. The synthetic tensor test
covers compatible/incompatible declarations and optional-mask preservation.
Ruff lint and format checks passed. These results supplement, rather
than replace, the earlier 15 native Cocoa synthetic passes at `8da3091`;
the new DTI route has not yet been visually checked in Cocoa.

The detailed private register now contains **27 attempted operations**, and
separate private JSON records hold source-specific current proposal findings.
These files remain outside the repository. No source or historical run was
modified.

### Native visual walkthrough still required

The Codex command environment has no Qt screens. Its actual-file checks above
used read-only services and offscreen Qt, not native desktop clicks. In the
normal Mac session, launch the committed app with the existing virtual
environment, `PYTHONPATH=src`, `QT_QPA_PLATFORM=cocoa` and
`OPENBLAS_NUM_THREADS=1`. Use the already available reference set and existing
validation derivatives; inspect the exact sources in the private audit. In one
session:

1. Import the known diffusion image with its b-values and b-vectors through
   **Import Data**. Confirm that Project/Preprocessing, DTI and CSD offer it;
   inspect shells and processing-history warnings. Do not start EDDY/TOPUP.
2. Import the HCP-style T1. Confirm that Registration lists it as imported,
   explains the missing contrast/space declarations, and offers **Review
   declarations**. Supply only declarations supported by source documentation;
   confirm Reference T1 and Anatomy eligibility update without changing the
   file. Check that EPI/T1 still requests its separate structural evidence.
3. In DTI, choose **Calculate maps from an existing tensor**. Import the known
   six-component tensor and confirm its selector, task summary, approval gate
   and hidden DWI-fit controls. Do not execute metrics during this audit.
4. Import the known FOD, three response files, tractogram, atlas, mask and FA
   map. Confirm CSD tissue selectors, Tractography/SIFT2 sources and
   Connectivity inputs update. In Connectivity inspect the atlas node table
   and its registration-QC warning; do not approve a blocked plan.
5. Open the existing verified tractography run through Results and its QC
   catalog. If the native Viewer opens, inspect image/tract loading and source
   scene preservation. Keep execution completion distinct from scientific
   review. Check each screen at a normal and minimum window size in light and
   dark appearance, including file pickers and keyboard focus.

Record screenshots and any exact rejection text privately. Native real-data
file-picker, Viewer and layout behavior remain **unverified** until those
observations are supplied. Missing complete real 5TT, reviewed SIFT2 weights,
native atlas QC and suitable source documentation still limit downstream
scientific eligibility. The 38-task scientific backlog and separate Phase 7
EDDY validation remain open.
