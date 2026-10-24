# Phase 4 — Tractography UX implementation and handoff

Branch: `development/phase4-tractography-ux-2026-10-23`, created from clean Phase 3 `b2625d8`. Local development only; do not push, merge, change public `main`, or release. Earlier Phase 1–3 verification limits remain in their handoffs.

## Checkpoint 4.0: audit and baseline

The existing `TrackingExecutionPanel` presents generation, filtering and density through one setup form, with an operation combo and conditional rows. Source, mask, seed, tracts, reference, algorithm, ACT, output and requested count share that form. Advanced values, ROI lists, preview approval, results and logs are further disclosures. The form exposes scientific identifiers and separate ROI checklists before a researcher has chosen a task. The Viewer filter is a separate technical dialog with declarations, tool path, OR/AND/ORDERED identifiers and exact commands. Both routes use the shared scientific engine, but their ROI capabilities and entry points differ.

Scientific coverage found in the current code:

- Generation: MRtrix `iFOD2`, `SD_Stream`, `iFOD1`, `Tensor_Det`, `Tensor_Prob`; ACT variants of the three FOD methods. Exact source type, seed method, 5TT/reference, algorithm parameters, subject/space association, executable version and plan digest are validated by `prepare_tracking` and the artifact engine.
- Main-panel filtering: `tckedit` with separate binary ROI artifacts. Multiple `include_N` constraints mean **ALL**. Multiple `exclude_N` constraints reject **ANY** hit. `ordered_N` keeps explicit slot order. Endpoint-only and ordered waypoints are incompatible. The output is a new derivative.
- Viewer atlas filtering: `prepare_roi_filter` prepares exact original-atlas label snapshots and supports Include **ANY** via one union mask, Include **ALL** via separate masks, **ORDERED** via separate masks and Exclude **ANY** via a union. It validates original source hashes, exact atlas/reference voxel indexing, registration evidence, subject/space association and full-source TCK identity. In the validated MRtrix 3.0.5 contract, ordered filtering follows the saved streamline vertex direction; reversal can change results. This route does not silently resample or rewrite the source.
- Density: `tckmap` uses an existing compatible tractogram and explicit reference grid; directional channels are optional. The resulting image is streamline-derived density, not an axon count.
- Viewer ROI import: `MainWindow.import_tracking_rois` snapshots selected Viewer ROIs to new binary mask artifacts after an explicit source association and grid check. It does not automatically assign them to a tracking role. Viewer native atlas filtering currently starts from ROI roles already assigned in the Viewer.
- Result handoff: generated tractograms, filtered tracks and density images pass through saved product verification and Viewer/QC routes. Execution completion is `validated_pending_qc`, not scientific acceptance.

Baseline tests in the restricted local session with `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`, `OPENBLAS_NUM_THREADS=1`, Python 3.12.15, macOS 26.3 ARM64:

| Baseline command or measurement | Result |
|---|---|
| `pytest -q tests/gui/test_tracking_gui.py::test_tracking_controls_and_approval_invalidation tests/gui/test_tracking_gui.py::test_tracking_invalid_input_recovery tests/gui/test_tracking_gui.py::test_ordered_roi_choices_survive_inventory_refresh tests/core/test_roi_selection.py --tb=short` | **14 passed in 6.32 s.** Non-VTK controls and pure selection rules only. |
| Combined tracking GUI/ROI workflow/core tracking/ROI selection command | **Exit 139** on the first GUI execution case during offscreen `QVTKRenderWindowInteractor` construction. This is the previously documented restricted/offscreen native-rendering failure; the command did not establish results for subsequent tests. Do not rerun the same VTK case in this session. |
| Three fresh `scripts/benchmark_gui_performance.py` offscreen processes | Tractography plan first navigation **12.903–13.177 ms**, median **12.951 ms**; return **14.089–14.479 ms**, median **14.374 ms**. ScientificPlanningPage construction **184.150–188.669 ms**, median **185.640 ms**. Script-to-interactive **804.616–874.157 ms**, median **805.835 ms**. These are warm-cache offscreen results, not native cold start; the Tractography navigation measurement occurs after the scientific page has been constructed. |

### Implementation checklist

1. **4.1 Task-first screen:** Separate Generate, Filter and Density into clear task views while keeping the existing typed `Task` and one approval/worker path. Preserve existing public widget attributes for tests and integrations. Measure construction/navigation cost.
2. **4.2 ROI selection:** Add a searchable imported-ROI inventory with direct role assignment/removal and keyboard waypoint ordering. Keep role IDs, source hashes and explicit association. Expose Include ALL/ANY accurately, using the existing original-atlas service for ANY where eligible; never suggest arbitrary binary masks have been unioned when they have not.
3. **4.3 Generation:** Guide source/algorithm/seeding/mask/count and ACT prerequisites; move specialist values to Advanced. Keep exact parameter mapping and validation.
4. **4.4 Filtering/density:** Make full-path versus endpoint rules clear. Keep bounded preview honest; avoid simulated selected counts. Preserve reference-grid and immutable-source checks.
5. **4.5 Results/QC:** Show exact plan, outcome, provenance and separate scientific review. Use the shared Viewer loader.
6. **4.6 Integration:** Run relevant GUI/core/CLI and Phase 1–3 regressions, profile before/after, request native Cocoa/VTK tests only after a committed code checkpoint, and record actual native results.

## Checkpoint 4.1: task-first interface

The default Tractography view now asks for one of three tasks: **Generate New Streamlines**, **Select or Filter Existing Streamlines**, or **Create a Tract-Density Map**. Each task has a separate input form. The Generate view explains seeding versus endpoints and tracking masks versus pathway conditions. Filter presents a source tractogram, reference and endpoint-only choice without generation algorithms. Density presents only the source tractogram and reference grid, with directional channels left under Advanced. The existing controls, typed task builder, exact plan preview, digest approval, worker and result/QC routes remain authoritative. Guided selectors synchronize with those controls; Advanced still exposes all prior scientific parameters. Returning to the initialized Tractography page reuses its widget and draft state. A task change invalidates the unexecuted plan and approval.

Files in this checkpoint: new `src/dmri_studio/gui/tracking_guided.py`, updated `gui/tracking.py`, `gui/scientific_planning.py`, `gui/main_window.py`, new `tests/gui/test_tracking_guided.py`, and this handoff. No scientific compiler or executable behavior changed.

Offscreen verification after this checkpoint:

| Test or measurement | Result |
|---|---|
| `pytest -q tests/gui/test_tracking_guided.py` plus the three selected non-VTK legacy tracking GUI tests | **8 passed in 14.27 s.** Covers task separation, typed-task mapping, approval invalidation, page reuse and advanced/guided synchronization. |
| The same GUI selection plus `tests/gui/test_phase2_lazy_workspaces.py` and `tests/gui/test_scientific_software_settings.py` | **30 passed in 82.89 s** after formatting. Covers Phase 1 settings defaults and Phase 2 lazy workspace regressions relevant to this UI change. |
| `ruff check` on changed GUI and test files | Passed before formatting. |
| Three fresh `scripts/benchmark_gui_performance.py` offscreen processes | Tractography plan first navigation **28.242–29.022 ms**, median **28.901 ms**; return **22.779–23.243 ms**, median **22.840 ms**. ScientificPlanningPage construction **209.298–270.320 ms**, median **212.027 ms**. Script-to-interactive **802.185–842.685 ms**, median **806.372 ms**. Compared with the three pre-change runs, the guided controls add measurable first/repeated navigation and constructor cost, but no measured startup regression. These are warm-cache offscreen samples, not native Cocoa benchmarks. |

## Checkpoint status

4.0 and 4.1 are implemented and offscreen tested. Checkpoint 4.2 is in progress below. Guided generation details, filtering/density completion, native VTK verification and complete beginner walkthrough remain open. No native Phase 4 claim has yet been made. Do not mark Phase 4 or Phase 5 readiness complete from these checkpoints.

## Checkpoint 4.2: imported ROI roles, first increment

The guided Generate and Filter views now show a searchable inventory of imported binary ROI artifacts. A researcher can assign a region to **Must pass through (all)**, **Must avoid (any)**, or **Pass through in this order**, remove it, and move a waypoint with keyboard-accessible buttons. These actions update the existing `include_N`, `exclude_N` and `ordered_N` artifact slots; the typed task and its scientific planner remain unchanged. Empty density controls hide the ROI section without deleting the draft roles. Inventory rows are cached when artifact identity and display name are unchanged.

Viewer ROI import still creates immutable snapshots after its existing explicit subject/space and voxel-grid checks. Freshly imported names and colors are displayed in the guided list; the Viewer highlight action uses the current Viewer generation and captured object identity, with no retained MRI array reference. If the Viewer source was replaced or the ROI removed, the action explains the problem instead of selecting another region with the same name. Edits to the live Viewer ROI after import do not alter the imported snapshot; users must import a new snapshot to use those edits. Filtering imports use the selected reference image's association; the full planner still checks the tractogram, reference and ROI identities together. Saved scientific selections retain artifact IDs and hashes; display colors are session metadata and do not affect command semantics.

The ordinary artifact-based filtering planner implements Include **ALL**, Exclude **ANY**, and ordered waypoints. The original-atlas filter service separately implements Include **ANY** through an exact union snapshot. The guided artifact panel does not yet expose that service; do not claim Include ANY is available from this panel. The Viewer atlas filter remains available pending its UX integration. Spatially incompatible ROIs and changed original bytes remain rejected by the existing planner, but native Viewer import and actual filter execution have not yet been rerun for Phase 4.

Offscreen verification: `pytest -q tests/gui/test_tracking_guided.py` plus three selected legacy GUI tracking tests and pure `tests/core/test_roi_selection.py` passed **21 tests in 21.53 s**. The guided tests cover search, role assignment/removal, waypoint order in the actual typed task, cached inventory, density hiding, and still-open Viewer-object highlighting. `pytest -q tests/core/test_roi_filtering.py::test_spatial_unknown_and_bad_ids_block --tb=short` passed **1 test in 0.16 s**, retaining spatial/label rejection. An earlier focused run passed 8 tests, but a test-only fake-Viewer teardown error was fixed before the 21-pass run. These are not native VTK or actual full-source filtering verification.
