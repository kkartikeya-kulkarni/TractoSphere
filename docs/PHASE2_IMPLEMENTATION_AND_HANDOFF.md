# Phase 2 — Startup and navigation performance handoff

Branch: `development/phase2-startup-navigation-2026-10-23`, created at the Phase 1 documentation checkpoint `e1377a2`. Keep Phase 1 native and CI limitations in `PHASE1_IMPLEMENTATION_AND_HANDOFF.md`. Do not merge PR #3/#4, change public `main`, or publish a release. No Phase 2 code has been pushed.

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

The batch profiler had constructed Batch and installed an unchanged 1,000-row synthetic journal, but had navigated to Software Dependencies before the idle interval. Thus this measures the inactive Batch page's polling cost, not ordinary startup with no batch opened. A separate small synthetic 8×10×12×4 NIfTI load through the actual GUI worker succeeded in 831.7 ms in one offscreen run; loaded navigation remained similar to the empty case (for example first Registration 34.9 ms, first Batch 24.3 ms). Native loaded-data navigation remains unmeasured.

Measured causes: `MainWindow` constructs all scientific subpanels before Start is visible; `BatchPage` starts a 600 ms timer in its constructor and rereads/rebuilds an unchanged journal. The initial Start does not import VTK (`vtkmodules` absent), and Viewer itself is already lazy. At this baseline, navigation and 1,000-row initial rendering are already below the 200 ms repeated-navigation goal offscreen; do not rewrite them solely on suspicion. Native viewer/dialog costs remain unmeasured.

The first native Cocoa attempt with the correct application virtual environment exited 139 before producing metrics, with pasteboard and macOS service connection errors inside restricted execution. A later normal-Terminal native benchmark and two VTK smoke tests passed; see the native checkpoint below. The restricted-environment crash was not an application defect. An earlier mistaken attempt with another Python environment lacked `psutil` and measured nothing.

## Work plan and verification checkpoints

- [x] 2.1 Empty offscreen startup, navigation, batch and idle baseline. A post-change native Cocoa measurement is recorded below; a same-conditions pre-change native baseline and native loaded-data repetitions remain open.
- [x] 2.2 Defer measured expensive scientific and batch workspace construction. Offscreen tests cover state, restart, settings and close; selected native Cocoa/VTK startup and Viewer smoke tests passed.
- [x] 2.3 Recolor only the two affected sidebar entries on navigation. Offscreen tests cover route state, one cached instance and stable widget count over 20 cycles.
- [x] 2.4 Stop unchanged journal rereads/table rebuilds and inactive idle polling; preserve selection by acquisition key after changed journal renders. Offscreen 10/100/1,000-row measurements and tests are below.
- [ ] 2.5 Profile common dialogs and Viewer in permitted native Cocoa. Offscreen dialogs are fast; Viewer was already lazy and no VTK was imported by initial Start. Two native VTK smoke tests passed, but native picker/ROI dialog timing and broader Viewer behavior remain open.
- [ ] 2.6 Selected native smoke/performance verification passed; broader native GUI and full core regression completion remain open. Focused offscreen GUI and core/CLI runs are below.

The Phase 1 handoff remains authoritative for native dependency verification limits and the pre-existing CI fixture failures. Do not turn those into scientific validation shortcuts.

## Implementation and measured result

Commits on this branch:

- `c703165` — standalone synthetic GUI profiler and pre-change measurements.
- `14c80a1` — create `ScientificPlanningPage` and `BatchPage` only on first access through cached public properties. Factories connect signals once and apply the current Phase 1 dependency defaults. Batch navigation no longer forces scientific page construction. Active-worker and close guards inspect already-created pages without instantiating them. Sidebar navigation recolors the old and new selected buttons rather than all 14 buttons.
- `4af1bc9` — Batch timer runs only while a saved batch is visible or a worker is active. A journal file identity/mtime/size check skips parsing and rendering if unchanged. Changed renders clear stale row-index selection before restoring acquisition-key selection.
- `d16af42` — full settings/window restart and 20-cycle widget reuse tests for deferred workspaces.
- `8312865`, `bd1b933` — handoff results and final focused GUI regression count.
- `8ea26d1` — verify scientific signal connections fire once after repeated navigation.

Five fresh, warm-cache offscreen processes ran the same `scripts/benchmark_gui_performance.py` before and after code changes. The initial state was empty and temporary; each process used the same runtime variables and synthetic 1,000-row journal. Median milliseconds, with ranges:

| Operation | Before | After | Change |
|---|---:|---:|---:|
| Script start to first Qt event after Start show | 1213.8 (1209.3–1366.6) | 829.5 (826.2–974.8) | 31.7% faster |
| `MainWindow` construction | 938.3 (933.2–997.3) | 557.3 (556.5–573.0) | 40.6% faster |
| First Reconstruction opening | 20.6 (20.3–21.2) | 245.7 (244.7–305.9) | Deferred construction paid here |
| Repeated Reconstruction opening | 22.9 (22.6–23.4) | 19.7 (19.6–19.8) | 14.0% faster |
| First / repeated Tractography planning | 19.2 / 14.4 | 13.5 / 13.2 | Page was already initialized by the preceding Reconstruction route |
| First / repeated Registration | 34.9 / 25.6 | 27.6 / 25.0 | Page was already initialized by the preceding Reconstruction route |
| First / repeated Batch Processing | 24.3 / 21.8 | 30.0 / 21.2 | First visit now creates the Batch page |
| First / repeated Settings | 6.2 / 3.5 | 5.9 / 1.9 | 46% faster repeated route |
| Unchanged 1,000-row journal refresh | 35.9 (35.0–70.7) | 0.04 (0.04–0.04) | About 99.9% less callback time |
| CPU in 1.8 s while Batch was **inactive** with journal | 215.2 (207.0–252.3) | 0.19 (0.19–0.21) | Timer now stopped (3–4 to 0 ticks) |

First Reconstruction is now visibly slower than before because that previously eager cost moved off startup. Repeated visits remain under the 200 ms target. The Batch first-visit regression is about 6 ms and remains about 30 ms. A changed single row in a 1,000-row journal still causes a full render: five changed-row trials measured 43.7 ms median (38.3–57.1), with selection preserved. This is below the interaction target; a model/diff-row rewrite was not justified by these measurements. A separate visible Batch window with an unchanged 1,000-row journal used 41.7 ms CPU in 1.8 s with three timer callbacks in one run; this result is not a repeated benchmark. Search/filter and initial table render stayed in the few-ms / ≈33-ms ranges observed before optimization.

The interpreter alone took 7.5 ms median (7.0–9.5) and PySide6 import took 60.9 ms (60.8–71.8) in five warm-cache subprocesses. Importing `vtkmodules.vtkRenderingOpenGL2` in a separate process took 126.7 ms median (125.4–282.8). Initial Start did not import VTK. These import timings are separate from the script-start benchmark and must not be added to it as if they were independent stages of one cold launch.

Three after-change offscreen runs loaded an 8×10×12×4 generated NIfTI with bvals/bvecs through the actual GUI worker. Load completion median was 692.7 ms (684.5–695.5); after load, first Registration navigation was 28.0 ms (27.9–28.1) and first Batch 30.7 ms (30.3–33.0). There was only one corresponding before-change loaded-data trial (831.7 ms load, 34.9 ms Registration, 24.3 ms Batch), so do not infer a reliable loaded-data speedup from that comparison. No real research data or expensive MRI processing was used.

### Dialogs and Viewer

In one clean offscreen process, five show-and-process-event trials measured scientific input dialog 7.1–30.1 ms, scientific task dialog 2.9–3.3 ms, and transform import dialog 3.0–3.2 ms. The Software Dependencies dialog first appeared in 25.4 ms and reopened in 0.26 ms with an already running, asynchronous inventory; the worker completed during orderly shutdown. These paths did not justify construction changes. Native file/folder pickers and ROI/VTK dialogs were not measurable in restricted Cocoa. Initial Start imported no VTK, so Viewer construction remains on its existing first-use path; no Viewer rendering or geometry code changed.

### Tests and limitations

- `pytest` on the new lazy and Batch refresh tests plus Phase 1 scientific settings: **20 passed**. The final combined Phase 1 dependency, software installation, execution-page and Phase 2 offscreen selection: **46 passed, 2 deselected** in 146.19 s (selected Viewer/VTK cases require native Cocoa). A subsequent lazy-only run including a one-emission signal-connection regression: **8 passed**.
- Focused core scientific software/dependency and all CLI tests: **61 passed**. Two non-VTK worker lifecycle tests: **2 passed, 1 VTK test deselected**. Two non-VTK desktop tests: **2 passed, 8 deselected**.
- Broad `tests/core tests/cli` run was interrupted after **62 passed, 12 failed** in 96.80 s; eleven Batch failures reached a macOS sandbox `psutil.process_iter`/`sysctl` permission denial, a restriction already demonstrated in Phase 1. The remaining ACT child-cancellation assertion returned `validated_pending_qc` instead of `cancelled`; its cause is unproven and the scientific code was not modified. This partial run is **not** a passing full core suite. The offscreen Batch GUI suite separately had **1 passed, 3 failed** from the same `sysctl` restriction. The user's normal Terminal passed the selected native batch execution/resume tests in Phase 1.2; those were not rerun natively for Phase 2.
- `ruff check .` passed. Repository-wide `ruff format --check src tests` reports four pre-existing core files as unformatted; Phase 2 files pass their focused format checks.
- One restricted native Cocoa profiler attempt exited 139 before timing. Do not repeat it in this sandbox or infer a product crash. The user's normal-Terminal selected native checks passed as recorded below. Cold-start behavior, native file-picker latency, broader VTK workflows and a same-conditions pre-change native benchmark remain unverified.

### Phase 2 native Cocoa checkpoint — normal Mac Terminal

The user ran from the repository on clean `development/phase2-startup-navigation-2026-10-23` at recorded commit `bd1b933`. With `PYTHONPATH=src`, `QT_QPA_PLATFORM=cocoa`, `OPENBLAS_NUM_THREADS=1` and the existing application virtual environment, `scripts/benchmark_gui_performance.py` exited **0**. It reported Apple ARM64, macOS 26.3, Python 3.12.15, and a native Start first-event time of **1155.719 ms** from the script's first instruction (still a warm-cache, in-process timing rather than cold OS launch). MainWindow construction was **604.548 ms**, show/event processing **193.901 ms**, first/repeated Reconstruction **363.498/21.772 ms**, first/repeated Batch **37.925/22.681 ms**, and the two unchanged 1,000-row journal callbacks after the initial read took **0.067/0.017 ms**. The inactive Batch timer fired **0** times and used **4.668 ms CPU** over 1.8 s. The benchmark did not construct a Viewer, as confirmed by `vtkmodules_loaded: false`.

The user then ran these two selected Cocoa/VTK tests in normal Terminal:

```sh
PYTHONPATH=src QT_QPA_PLATFORM=cocoa OPENBLAS_NUM_THREADS=1 ../application/.venv/bin/python -m pytest -q tests/gui/test_desktop_redesign.py::test_image_route_is_not_tractography_and_has_honest_empty_state tests/gui/test_worker_retirement.py::test_native_scene_finalizes_before_parent_destruction --tb=short
```

Result: **2 passed in 3.02 s, exit 0**. This verifies selected native VTK creation, image/tractography route switching, and safe Viewer finalization. It does not cover all Viewer interactions or scientific processing. The later `8ea26d1` commit changed only a test and this handoff; `git diff --quiet bd1b933 8ea26d1 -- src scripts/benchmark_gui_performance.py` returned 0, so the tested application and profiler code match the later local checkout. The user's captured HEAD is direct evidence of the exact tested commit. There is no pre-change native Cocoa run under identical conditions, so **no native percentage speedup is claimed**.

For follow-up native measurement or broader VTK testing, use the same environment and capture HEAD with the result:

```sh
cd ~/Projects/dmri-studio2.0/TractoSphere-publication
git rev-parse --short HEAD
PYTHONPATH=src QT_QPA_PLATFORM=cocoa OPENBLAS_NUM_THREADS=1 ../application/.venv/bin/python scripts/benchmark_gui_performance.py
PYTHONPATH=src QT_QPA_PLATFORM=cocoa OPENBLAS_NUM_THREADS=1 ../application/.venv/bin/python -m pytest -q tests/gui/test_desktop_redesign.py::test_image_route_is_not_tractography_and_has_honest_empty_state tests/gui/test_worker_retirement.py::test_native_scene_finalizes_before_parent_destruction --tb=short
```

Phase 2 is implemented and sufficiently verified on the measured bottlenecks, selected native shell/VTK behavior, and focused regressions to begin Phase 3 Batch UX development. This is a readiness decision, not a claim of complete native GUI, native cold-start, or full core-suite passage. Track the outstanding verification above alongside real ANTs installation verification, broader Phase 1 native GUI coverage, and pre-existing CI fixture failures in the Phase 1 handoff.
