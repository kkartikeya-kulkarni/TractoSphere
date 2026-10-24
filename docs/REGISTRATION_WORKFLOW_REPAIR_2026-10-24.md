# Registration workflow repair and real-data audit — 24 October 2026

## Scope and safety

This branch starts from the reviewed real-data audit checkpoint `372002d`, after
the Phase 1–6 and post-audit usability changes. Phase 7 remains on its separate
branch. Work used only the previously authorized, bounded reference and
validation directories. The detailed seven-input role matrix, exact private
paths and timing script are stored outside this repository in the existing
private audit folder. No research image, gradient, participant identifier or
private absolute path is committed. Original file identities were unchanged
after the timed DWI inspection and cancellation probe. No EDDY, TOPUP, ACT,
tractography or real-data registration command was run.

## Confirmed problems and corrections

| Priority | Confirmed problem | Correction |
|---|---|---|
| P1 | Selecting an HCP-style DWI in the scientific importer left matching `bvals`/`bvecs` to manual discovery. Changing images could retain an earlier gradient pair. | A Qt-free, read-only companion suggester handles exact-stem NIfTI sidecars, HCP's same-folder `data.nii.gz`/`bvals`/`bvecs`, and the existing bounded BIDS inheritance contract. Only unique associations populate fields; source changes clear old companions. Multiple inherited JSON files direct users to BIDS discovery or an explicit manifest because one-file import cannot preserve their merged provenance. The normal loader still validates gradients and volume count. |
| P1 | Imported DWI, mask, ROI, atlas and transform records could be hidden from Registration review when not eligible for a role. | The guided workspace retains all Registration input types in an inspected-input list. It shows concise per-input review prompts and separate per-role reasons; users can revise supported declarations through the existing source-bound reinspection route. The newly imported record is selected for review. Provisional selector eligibility remains separate from exact planning and anatomical approval. |
| P1 | Registration scientific import and the active Project/Preprocessing DWI were easy to confuse. | The status bar now names the active preprocessing DWI explicitly. Registration offers an explicit action to load the selected imported DWI and its exact gradients through the normal Project loader. This revalidates the data and never claims correction history or starts preprocessing. |
| P0 | EPI-to-T1 planning could accept the fixed T1 as its own brain-extracted T1 and an arbitrary binary mask as white-matter segmentation. | New plans require a distinct declared brain-extracted T1 and a mask explicitly declared for white-matter segmentation. The existing hash-bound same-space association, geometry, subject, direction, toolchain, plan digest and approval checks remain. Historical run records are unchanged. A declaration alone does not establish anatomical correctness. |
| P0 | An image could be redeclared as two different spaces and used as both sides of a new alignment or imported transform. | The guided form and Qt-free registration/transform contracts reject the same source image in both roles. Independently named synthetic images with the same voxel values remain valid test inputs. |
| P1 | Atlas/ROI import always opened the atlas-label type, even for a binary ROI or continuous image. | The guided move workflow asks which implemented derivative type to import and routes to the matching scientific importer. Planner interpolation and transform checks remain authoritative. |
| P1 | Previous Registration runs were reachable only by navigating away and finding Results. | Inspect Registration now opens the existing Results manifest picker and verifier. That route checks saved output hashes, provenance and QC separately from execution completion. |
| P1 | Repeated Cancel clicks and late worker progress could obscure the cancellation request. | Cancel disables after one request, retains a cancellation-requested message while a low-level read reaches its next cooperative checkpoint, and discards unpublished input results. No thread is terminated. |
| P2 | A long eligibility warning repeated the same filename and missing fields for several roles. | The overview now lists each imported file once and directs users to a wrapped, role-by-role evidence explanation. Advanced transform import disables undeclared image choices and shows their missing fields in a bounded scrollable detail panel. |

## Registration input matrix and current real-data boundary

| Goal | Required roles | Actual bounded real-data result |
|---|---|---|
| Create Diffusion Reference | Identified preprocessed DWI, validated gradients, documented correction history, low-b selection | The 288-volume DWI and matching gradients imported. Its correction/processing history is not independently established for this new Registration attempt, so reference planning remains blocked. The synthetic approved reference workflow and generated-reference inventory handoff have test coverage. |
| Align Diffusion and T1 | Verified 3D corrected EPI/b0 reference, T1, distinct brain-extracted T1, white-matter segmentation, matching space evidence | The HCP-style T1 imported and stayed visible while undeclared. Explicit review using its documented subject ACPC structural context made it a provisional T1 selector candidate without reimport. No verified corrected b0, separate brain-extracted T1 or WM segmentation association was established; a real EPI/T1 plan was not approved. |
| Align Two Images | Two distinct identified 3D images with supported contrasts and spaces | Existing T1, b0 and T1-in-diffusion candidate images imported. Their scientific contrast/space relationships are not inferred from names or geometry; undeclared versions remain visible but blocked. Rigid/affine execution is covered with controlled synthetic inputs. |
| Move Atlas or ROI | Identified categorical/ROI/continuous source, target reference, verified source-to-target transform | Real atlas and ROI imported. Three existing matrix files passed finite FSL-matrix structure inspection, but no software/version/direction/image-bound transform record or native atlas QC was established. No real resampling was authorized. |
| Inspect Registration | Verified completed registration run or registered-atlas QC bundle | No real native registration QC bundle was found in the bounded source set. The existing saved-run verifier and synthetic native Viewer/QC routes remain the supported inspection path. |
| Advanced | Exact typed source/target images, operation-specific transform convention and executable evidence | Supported operations remain in the registry; undeclared images are now visibly blocked in transform import. Real matrices alone do not establish their direction or anatomical acceptance. |

Seven representative actual inputs were reimported read-only through the
application's scientific importer and offscreen shared inventory: DWI, HCP
T1, b0 image, brain mask, atlas, ROI and a T1-in-diffusion candidate. All
seven were technically imported; all role blocks reported missing declarations
or independent spatial/processing evidence rather than silently approving a
match. The actual HCP T1 re-review retained the same source and changed its
artifact ID only after reinspection; Reference T1 and Anatomy selectors then
contained it. These checks are **real-file service and offscreen GUI evidence**,
not native file-picker or anatomical validation.

## Performance and cancellation

The accessible local 1,307,052,917-byte HCP-style DWI was inspected once
through `import_artifact` in **3.507 s**: companion suggestions **0.001 s**,
loader/header/gradient/sample work before hashing **3.018 s**, then source
hashing and publication **0.489 s**. A separate cancellable loader request
made during centered-volume sampling returned in **2.889 s**, published no
candidate and left the source fingerprint unchanged. This is one warm/local
measurement, not cold cloud-storage performance. NiBabel's individual gzip
read cannot be interrupted safely mid-call; the GUI now explicitly reports
that cancellation waits for a safe read checkpoint. Slow hydrated/cloud-backed
files remain a native-environment performance investigation.

## Verification and limitations

- The previous baseline selected Registration tests passed **11/11** before
  edits. Initial post-edit focused GUI/core runs passed **34/34** before the
  stricter EPI structural-role contract. The subsequent focused Registration
  GUI run passed **19/19**, and `test_registration.py` plus sidecar tests
  passed **18/18**. After the same-image transform guard, the combined
  Registration, input-association and loader selection passed **87/87**:
  `PYTHONPATH=src OPENBLAS_NUM_THREADS=1 ../application/.venv/bin/python -m pytest -q tests/test_registration.py tests/core/test_input_associations.py tests/core/test_loading.py --tb=short`.
  The final extra linked-gradient test passed in **5/5** input-association
  checks after that addition.
- The selected Registration, cross-workspace input and usability GUI run was
  **53/53** offscreen. After the final eligibility wording/layout changes,
  focused current-code tests were **12/12**:
  `PYTHONPATH=src QT_QPA_PLATFORM=offscreen OPENBLAS_NUM_THREADS=1 ../application/.venv/bin/python -m pytest -q tests/gui/test_registration_role_recovery.py tests/gui/test_registration_import_review.py tests/gui/test_registration_guided.py::test_guided_registration_goals_are_separate_and_reused tests/gui/test_usability_audit_fixes.py::test_registration_explains_filtered_import_and_opens_scoped_dialog --tb=short`.
  Batch, Tractography and Phase 6 scientific-input GUI regressions were
  **33/33** offscreen. All CLI tests were **34/34**. Ruff lint and formatting
  checks passed on the changed Python files.
- One mixed test invocation included BatchRunner tests and encountered the
  known restricted-session `psutil.process_iter`/macOS `sysctl` permission
  error (2 tests); it does not establish an application BatchRunner defect.
  The same invocation also had a transient pytest fixture-discovery error for
  four GUI cases. Standalone GUI selection subsequently passed. A broad GUI
  invocation containing native VTK cases was interrupted under offscreen Qt;
  those cases must run under normal Cocoa.
- Offscreen 960×640 captures showed the Registration heading fully visible.
  A second capture exposed excessive repeated eligibility text; it was
  shortened to one item per imported file with detailed per-role findings
  below. These captures are not native macOS screenshots.
- Normal Cocoa picker operation, actual Viewer comparison/QC, real BBR
  anatomy, Atlas/ROI resampling with verified transforms, cold cloud-file
  performance and human review remain **unverified** on this branch. The
  permanent scientific-validation backlog retains its existing statuses.

Application and test code checkpoint: **`7161865`** on
`development/registration-workflow-repair-2026-10-24`. The native-only
Registration/Viewer test file was updated for the stricter structural-role
contract but was **not** executed in the restricted Codex desktop session.

## Modified files

The code checkpoint changed `core/input_associations.py`,
`core/scientific/registration.py`, `core/spatial.py`, `gui/main_window.py`,
`gui/registration.py`, `gui/registration_guided.py` and
`gui/scientific_planning.py`. It added or adjusted
`tests/core/test_input_associations.py`,
`tests/gui/test_registration_role_recovery.py`,
`tests/gui/test_registration_guided.py`,
`tests/gui/test_registration_guided_native.py`,
`tests/gui/test_usability_audit_fixes.py` and `tests/test_registration.py`.
This handoff and `docs/PROGRESS.md` record the boundary of verification.

The exact-path native acceptance guide is local only at
`../real-data-audit-private/REGISTRATION_NATIVE_ACCEPTANCE_2026-10-24.md`.
It has PASS/FAIL/BLOCKED rows for all six Registration goals, DWI/T1 evidence,
file pickers, cancellation, QC, resize, keyboard and theme inspection. Its
participant paths are not included in the repository.

For normal Mac Cocoa regression testing against the committed branch:

```sh
cd ~/Projects/dmri-studio2.0/TractoSphere-publication
git rev-parse --short HEAD
PYTHONPATH=src QT_QPA_PLATFORM=cocoa OPENBLAS_NUM_THREADS=1 FSLDIR="$HOME/fsl" \
  ../application/.venv/bin/python -m pytest -q \
  tests/gui/test_registration_guided_native.py \
  tests/gui/test_registration_role_recovery.py \
  tests/gui/test_registration_import_review.py \
  tests/gui/test_registration_gui.py --tb=short
echo "Native Registration exit code: $?"
```

This command is a proposed native check, not a recorded pass. Real-file
picker, visual Viewer and human anatomical review must be recorded separately
using the private guide.

## Branch integration and next verification

This focused branch includes the post-Phase-6 usability and real-data fixes
because it was created from `372002d`. Phase 7 must be reconciled with this
branch and the intervening post-audit commits before scientific experiments;
it must not restart from `8102df1`. Do not merge this branch into Phase 7 or
public `main` without a reviewed integration decision. Native Cocoa tests
should run only against the final committed application HEAD, followed by a
researcher walkthrough using the exact private guide outside the repository.
