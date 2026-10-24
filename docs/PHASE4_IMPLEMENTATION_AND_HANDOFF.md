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

## Checkpoint status

Only 4.0 is complete. No Phase 4 code change or native Phase 4 claim has yet been made. Do not mark Phase 4 or Phase 5 readiness complete from this audit.
