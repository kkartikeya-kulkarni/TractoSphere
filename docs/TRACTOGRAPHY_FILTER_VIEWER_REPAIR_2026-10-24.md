# Viewer-to-Tractography filtering repair — 24 October 2026

## Scope and starting point

This branch starts from `a4b3bd2`, the current Registration-repair checkpoint
that includes the native-verified low-b summary fix, post-audit usability work,
and guided Phase 4 Tractography. It does not merge or replace the separate
Phase 7 EDDY branch. The [Phase 4 handoff](PHASE4_IMPLEMENTATION_AND_HANDOFF.md),
[Registration repair handoff](REGISTRATION_WORKFLOW_REPAIR_2026-10-24.md),
[real-data audit](REAL_DATA_FUNCTIONAL_AUDIT_2026-10-24.md), and
[38-task scientific backlog](SCIENTIFIC_VALIDATION_BACKLOG.md) remain in force.

## Confirmed cause

The native Viewer indexed a TCK/TRK for display as a `TractGroup`. That group
was not a scientifically imported `tractogram` artifact. Guided filtering read
only the scientific artifact inventory, so a visible Viewer group left its
source selector empty. The Viewer could also show a capped subset or in-memory
edits, while the validated `tckedit` backend correctly requires the original
full-source file. A successful visual load did not establish subject, space,
reference, ROI registration, or scientific approval.

## Repair and scientific boundary

- **Filter These Tracks…** acts on the selected Viewer tract group. The
  selected original path, full-source count, display count, edited-state
  warning, and Viewer reference are carried into guided filtering. Multiple
  groups use the selected row; a former filter selection is cleared if it does
  not describe that row. Existing exact-path scientific records are reused only
  when unambiguous; a verified product also has to match its Viewer hash.
- If the source is display-only, the existing scientific importer opens with
  that exact path locked. A previously imported record opens its declaration
  review instead of creating an unnecessary duplicate. Imported-but-ineligible
  sources remain visible, with missing or conflicting subject/space evidence
  explained. The matching reference has its own review action. Imported ROIs
  remain searchable and assignable as required, excluded, or ordered masks;
  the new Viewer action returns to its existing ROI editor. The original-label
  atlas dialog continues to provide its separate Include ANY/ALL/ORDERED route.
- The Viewer checks that both the tractogram index and reference file remain
  unchanged before transfer. A changed Viewer source blocks preview; the
  existing planner rehashes all bound inputs, checks geometry and declared
  associations, compiles exact commands, and requires digest-bound approval.
  Selecting a region never executes a command.
- Native `tckedit` accepts TCK. TRK remains viewable and imported, but it
  cannot be passed to `tckedit` silently. A new explicit TRK-to-TCK action
  requires a matching declared subject/space/session and exact 3D NIfTI grid,
  refuses TRK point/streamline attributes that TCK would discard, exports every
  original streamline without Viewer edits, compares every converted point in
  RAS+ mm, and writes an exclusive hash-bound conversion record. It never
  infers a transform or certifies anatomical correspondence. The converted
  TCK is reimported, selected, and still requires a new plan and approval.
- After a verified filter run, **Open Result in Viewer**, **View Original
  Tracks**, **Review QC**, and **View Run Details** use the existing result
  record and hash checks. The original action rechecks source and generated
  reference hashes. Technical completion stays `validated_pending_qc`;
  scientific review remains required. Empty output is reported as a failed
  validation and no verified tractogram is published.

The guided TCK path keeps Include ALL, Exclude ANY, ordered-waypoint direction,
and endpoint-only behavior exactly as implemented by the existing backend.
Include ANY remains in the original-label Viewer atlas filter. The GUI does
not substitute a sampled preview count for full-source filtering.

## Bounded real-data check

The exact paths and hashes are in a **private local audit record outside Git**.
Four files from an existing local Stage 4 validation run were reimported through
TractoSphere's real `import_artifact` service: one TCK, its generated 3D NIfTI
reference, and two ROI masks. Import times were **0.015, 0.066, 0.017, and
0.017 seconds** respectively. The offscreen scientific inventory selected one
matching tractogram and one reference, displayed two ROIs, and kept Run
disabled without approval. A new, read-only `tckedit` plan compiled four exact
commands in **0.179 seconds**. A bounded technical run on this 1,000-track
source completed in **0.654 seconds**, retained **4 tracks**, and returned
`validated_pending_qc`; all four source hashes were unchanged. This is a
technical processing check with prior validation-derived masks, **not**
independent anatomical ROI registration or scientific approval. Its derivative
was written outside the research source directories. No large tractogram,
EDDY, TOPUP, ACT, or tract-generation run was started. The bounded real-data
roots contained TCK research results but no independent real research TRK;
TRK conversion is verified with synthetic geometry fixtures only.

## Performance and verification

One offscreen synthetic inventory sample measured first scientific-workspace
construction at **459.126 ms**. For 10/100/1,000 ROIs, refresh took
**1.213/3.625/22.049 ms** and exact Viewer-source selection took
**1.870/1.837/27.051 ms**. These are warm offscreen timings, not native
WindowServer/VTK rendering measurements.

Focused verification at application commit `4bcb813`:

| Check | Command / result |
|---|---|
| Offscreen guided GUI | `PYTHONPATH=src QT_QPA_PLATFORM=offscreen OPENBLAS_NUM_THREADS=1 ../application/.venv/bin/python -m pytest -q tests/gui/test_viewer_filter_handoff.py tests/gui/test_tracking_guided.py tests/gui/test_roi_filter_guided.py tests/gui/test_registration_guided.py tests/gui/test_batch_guided.py --tb=short` — **60 passed** in 94.50 s. |
| Focused handoff and conversion after final edits | `PYTHONPATH=src QT_QPA_PLATFORM=offscreen OPENBLAS_NUM_THREADS=1 ../application/.venv/bin/python -m pytest -q tests/gui/test_viewer_filter_handoff.py tests/core/test_tract_filter_conversion.py --tb=short` — **11 passed** in 13.89 s. |
| Cancellation follow-up | `PYTHONPATH=src OPENBLAS_NUM_THREADS=1 ../application/.venv/bin/python -m pytest -q tests/core/test_tract_filter_conversion.py --tb=short` — **4 passed** in 0.31 s at `ad09f14`, including cancellation after work has begun and absence of a partial derivative. |
| Core tracking/ROI/scene | `PYTHONPATH=src OPENBLAS_NUM_THREADS=1 ../application/.venv/bin/python -m pytest -q tests/core/test_tract_filter_conversion.py tests/core/test_tracking.py tests/core/test_roi_filtering.py tests/test_scene.py tests/test_scene_operations.py -k 'not cancellation_real_child' --tb=short` — **51 passed, 1 deselected** in 14.14 s. |
| CLI | `PYTHONPATH=src OPENBLAS_NUM_THREADS=1 ../application/.venv/bin/python -m pytest -q tests/cli --tb=short` — **34 passed**. |
| Native test collection under offscreen Qt | `... pytest -q tests/gui/test_tracking_guided_native.py --tb=short` — **4 skipped** because Cocoa/VTK is required. |
| Style | Ruff lint, formatting and `git diff --check` passed on changed files. |

An overly broad offscreen GUI invocation reached a pre-existing VTK Viewer
constructor and exited with signal 139. The native test is therefore pending,
not failed as an application interaction. A broad core invocation reproduced
the pre-edit `test_cancellation_real_child` race: processing finished before
its cancellation assertion. Three unrelated Registration/Batch tests also
failed there: one macOS `psutil` process-enumeration permission denial and two
fixture paths now rejected by the established distinct-image transform rule.
No scientific guard was relaxed to clear these environmental or historical
test failures. The complete Registration native suite remains independently
pending.

The added native Cocoa walkthrough test uses the actual Viewer button with two
loaded tract groups, assigns synthetic Include/Exclude ROIs, checks exact
command inputs and approval, executes a small `tckedit`, opens the verified
filtered result, and reopens the original. The user's normal-Mac run at
`4c8245c` returned **6 passed, 1 failed**. Source selection, filtering and
filtered-result Viewer opening passed; **View Original Tracks** timed out. A
targeted Cocoa diagnostic at `070e555` showed that the action passed
`reference.nii.gz` to the Viewer as a tractogram. The filter planner's primary
`source` is its reference image; the tractogram is the explicit
`input_bindings["tracts"]`. The action now selects that binding and verifies its
recorded hash. Synthetic regression fixtures now match the real plan shape and
test source/reference tampering. Offscreen handoff tests pass; the corrected
Viewer reopening still needs a native rerun. Native file-picker layout, atlas selection,
Viewer preservation, real TRK conversion, and real anatomical correspondence
remain explicit follow-ups.

## Native acceptance on the normal Mac

At the committed application HEAD, run the consolidated command in the final
report. Then use the private local MRI guide to open a documented TCK and
NIfTI reference in the native Viewer. Select the intended tract group, choose
**Filter These Tracks…**, verify the full-source warning and source/reference
selection, review or import only missing scientific declarations, choose
compatible ROIs, preview exact commands, approve a small new derivative, and
inspect filtered tracks, original tracks, QC, and run details. Repeat with two
groups to confirm that changing the Viewer row changes the intended source.
For a TRK, inspect its reference and conversion record before filtering; a
TRK with attributes or mismatched grid must remain blocked. Record screenshots
and any native failure outside the repository with private data.

## Commits and remaining work

Application/test baseline commit: `4bcb813`; focused cancellation-test commit:
`ad09f14`; initial documentation commit: `336b6f6`. The original-track
binding correction is recorded in the subsequent local commit.
The native Cocoa walkthrough and researcher-led anatomical review are pending;
the Registration full native suite remains independently pending. The 38-task
scientific-validation backlog is unchanged. Phase 7 must integrate this branch
with all intervening usability and Registration commits rather than restart at
the older Phase 6 checkpoint.
