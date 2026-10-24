# Phase 2 — Startup and navigation performance handoff

Branch: `development/phase2-startup-navigation-2026-10-23`, created at the Phase 1 documentation checkpoint `e1377a2`. Keep Phase 1 native and CI limitations in `PHASE1_IMPLEMENTATION_AND_HANDOFF.md`. Do not merge PR #3/#4, change public `main`, or publish a release.

## Phase 2.1 baseline — 2026-10-23

Hardware observed by Python: Apple ARM64, macOS 26.3, Python 3.12.15. Five fresh Python processes used the existing application environment, `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`, `OPENBLAS_NUM_THREADS=1`, temporary empty INI settings and no MRI data. Run `scripts/benchmark_gui_performance.py` to repeat the same operations. Values below are median milliseconds (range). They are **warm-cache, offscreen process measurements starting at the script's first instruction**, excluding interpreter startup and disk cold-start. They are not native Cocoa/VTK benchmarks.

| Operation | Baseline median ms (range) |
|---|---:|
| Script start to first Qt event after showing Start | 1213.8 (1209.3–1366.6) |
| `MainWindow` construction | 938.3 (933.2–997.3) |
| PySide6 import | 39.9 (39.7–45.1) |
| Main window module import | 99.6 (98.8–173.2) |
| `QApplication` construction | 56.4 (56.0–64.2) |
| Show and event processing | 61.6 (61.6–62.0) |
| `ScientificPlanningPage` construction | 191.5 (190.6–243.1) |
| `WorkflowPage` construction | 98.5 (97.9–98.8) |
| First / repeated Reconstruction navigation | 20.6 / 22.9 |
| First / repeated Tractography planning navigation | 19.2 / 14.4 |
| First / repeated Registration navigation | 34.9 / 25.6 |
| First / repeated Batch navigation | 24.3 / 21.8 |
| First / repeated Settings navigation | 6.2 / 3.5 |
| 10 / 100 / 1,000 synthetic Batch row initial render | 0.94 / 3.71 / 32.56 |
| 1,000-row unchanged journal refresh | 35.9 (35.0–70.7) |
| Batch timer callbacks in 1.8 s / CPU time consumed | 3 (3–4) / 215.2 ms (207.0–252.3) |

The batch profiler deliberately opened Batch and installed an unchanged 1,000-row synthetic journal. Its idle CPU measurement includes that open page and timer, so it is not an estimate of the application's ordinary unopened Batch idle CPU. A separate small synthetic 8×10×12×4 NIfTI load through the actual GUI worker succeeded in 831.7 ms in one offscreen run; loaded navigation remained similar to the empty case (for example first Registration 34.9 ms, first Batch 24.3 ms). More repetitions and a native loaded-data benchmark remain due.

Measured causes: `MainWindow` constructs all scientific subpanels before Start is visible; `BatchPage` starts a 600 ms timer in its constructor and rereads/rebuilds an unchanged journal. The initial Start does not import VTK (`vtkmodules` absent), and Viewer itself is already lazy. At this baseline, navigation and 1,000-row initial rendering are already below the 200 ms repeated-navigation goal offscreen; do not rewrite them solely on suspicion. Native viewer/dialog costs remain unmeasured.

The first native Cocoa attempt with the correct application virtual environment exited 139 before producing metrics, with pasteboard and macOS service connection errors inside restricted execution. The user's normal Terminal passed a selected Cocoa test during Phase 1. Native performance is **pending**; do not classify this restricted-environment crash as an application defect. Earlier mistaken attempt with another Python environment lacked `psutil` and measured nothing.

## Work plan and verification checkpoints

- [x] 2.1 Empty offscreen startup, navigation, batch and idle baseline. Native Cocoa and loaded-data repetitions remain open.
- [ ] 2.2 Defer measured expensive scientific workspace construction, preserve settings, plans, workers and public attributes. Benchmark again and run GUI tests.
- [ ] 2.3 Remove measured redundant navigation work only where beneficial. Verify state and signal lifetime.
- [ ] 2.4 Stop unchanged journal rereads/table rebuilds and inactive idle polling; benchmark 10/100/1,000 rows, selection/filter, active status and shutdown.
- [ ] 2.5 Profile common dialogs and Viewer in permitted native Cocoa; change only proven slow paths.
- [ ] 2.6 Run GUI/core/CLI regressions, Phase 1 settings/provenance checks, native smoke checks, and document before/after median and range.

The Phase 1 handoff remains authoritative for native dependency verification limits and the pre-existing CI fixture failures. Do not turn those into scientific validation shortcuts.
