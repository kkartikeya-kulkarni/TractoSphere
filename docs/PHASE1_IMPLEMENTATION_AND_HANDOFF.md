# Phase 1 scientific software configuration — implementation and handoff

Date: 2026-10-23. Phase 0 baseline: [PHASE0_AUDIT_AND_HANDOFF.md](PHASE0_AUDIT_AND_HANDOFF.md). Scope is the saved MRtrix3/FSL/ANTs installation experience only. Do not merge PR #3 or start Phase 2 from this record.

## Repository and PR state

- Public scientific base: `development/scientific-toolbox-2026-10` at `9c7a3112ba13d5b1c608cbeb7866ef536de4e893`.
- Phase 0 audit branch: `audit/phase0-baseline-2026-10-23` at `d96ab4f`.
- Phase 1 working branch: `development/phase1-scientific-software-2026-10-23`, based on the Phase 0 audit commit. Commit `58f3a6b` carried forward only PR #3's dependency-path and busy-state changes. Commit `4577428` implemented the central configuration, workflow propagation, pinned batch defaults and regression tests.
- PR #3 remains draft at `1cf75fe8667d3164724bad12c48ef2a601d1fd9f`, base `9c7a311`. Its eight changed files were reproduced byte-for-byte from the GitHub patch in `/private/tmp/tractosphere-pr3-review`; `git apply --check` and `git diff --check` passed. The three dependency-path production files and corresponding test hunks were incorporated here. PR #3's tractography/registration wording changes were left on PR #3.
- PR #3 GitHub Actions run `40043535129` currently fails on all five matrix jobs at the core test step. The macOS arm64 log reports 31 failed, 801 passed, 126 skipped; several failures involve absent external FSL/MRtrix paths. This is not a passing CI baseline.

## Root causes addressed

1. QSettings already stored `dependencies/MRtrix3`, `/FSL`, `/ANTs`, but `DependencyController.apply()` emitted its change signal before checking the write and saved any scanned executable folder even if the complete family was incompatible or incomplete.
2. `MainWindow.apply_dependency_locations()` covered preprocessing and scientific MRtrix defaults but formerly used `Path("")` for missing MRtrix and did not propagate MRtrix to Batch. PR #3 supplied a first pass for these two paths and partial busy gates.
3. Scientific registration and native FSL 5TT anatomy derived FSL only from `FSLDIR`; deferred batch registration did the same. A saved application FSL path therefore did not reliably reach their actual command compilers.
4. Batch Processing had a separate MRtrix field. New plans lacked explicit FSL/ANTs defaults, and a reopened history's empty MRtrix field could fall back to a newer global value.
5. The full-source ROI filter dialog initialized MRtrix from a hard-coded `/usr/local/mrtrix3/bin`, bypassing the saved location.
6. The setup screen showed `Not checked` even with a saved path and separated scanning from applying, leaving a confusing save flow. Opening scientific workspaces must not rescan installations merely to show remembered paths.

## Implemented tasks

- [x] Central `ScientificSoftwareSettings` facade over the existing QSettings keys. Empty values are `None`, reads are immediate, and save/remove synchronizes settings and checks `QSettings.status()` with rollback on failure. Legacy keys need no migration.
- [x] Cheap display states distinguish no configuration, remembered configuration, missing directory, incomplete directory, and unsupported host without subprocesses. Fresh inventory remains the authority for ready, incompatible, incomplete, interrupted and verified versions.
- [x] Per-family **Verify and Save** uses the one existing asynchronous inventory worker and requires a complete compatible family and unchanged selected path. **Change Location** and **Remove Saved Location** are in the same Scientific Software section. A failed/interrupted check does not save.
- [x] Existing GUI workflow defaults now propagate MRtrix3 to preprocessing, DTI/CSD/tracking/SIFT2/connectivity/anatomy, batch and ROI filtering; FSL to preprocessing/registration/anatomy/batch; ANTs to preprocessing/batch. New scientific compilers still validate exact tools and source provenance.
- [x] Core FSL root resolver gives explicit configured root/bin precedence over `FSLDIR` and never treats empty as the working directory. Registration, native 5TT anatomy and deferred batch registration use it.
- [x] New batch plans record selected MRtrix3/FSL/ANTs directories; runtime recompilation reads those pinned plan values. Existing plan files lacking new optional fields keep their original behavior. Reopened history displays its prior MRtrix location, refuses to create a new plan implicitly, and offers a distinct **New batch with current software** action. Engine checks for exact tool identity and hashes remain in place.
- [x] Preference changes are blocked during preprocessing, scientific, batch or ROI-filter workers and while explicit scientific/batch approval controls are checked. Allowed changes invalidate unexecuted previews.
- [x] Dependency setup and README documentation updated for the implemented flow.

## Tests actually executed

All local tests used `PYTHONPATH=src` and the existing application virtual environment at `../application/.venv/bin/python` (Python 3.12). GUI tests used `QT_QPA_PLATFORM=offscreen`; this is not native Cocoa/VTK verification.

| Command or selection | Observed result | Scope/limit |
| --- | --- | --- |
| PR #3 isolated worktree: `tests/gui/test_dependency_setup.py -k 'not first_run_viewer_only_during_scan_and_repeat_suppressed'` | 6 passed, 1 deselected | Preliminary dependency paths at PR head. |
| PR #3 isolated worktree: new registration/tracking wording tests | 2 passed | PR-scoped wording only. |
| Phase 1 dependency setup, guided installation and core dependency inventory | 23 passed, 1 deselected | Includes synthetic external tools and guided save/write-failure tests. |
| New Phase 1 GUI/core tests | 13 passed in 16.36 s | Saved values, restart reconstruction, actual new-batch resolver arguments, registration preview arguments, incompatible/moved/empty paths, historical separation, approved-plan reopening and active approval. |
| Focused core software setup, anatomy and batch planning tests | 17 passed | No long-running MRI processing. |
| CLI processing/plan/load tests | 34 passed | CLI parity unchanged. |
| Selected non-VTK execution, planning and tracking GUI tests | 8 passed | Preview invalidation and workflow controls. |
| Batch GUI control test | 1 failed | Journal remained READY instead of PAUSED; underlying BatchRunner encounters sandbox `psutil.process_iter()`/`sysctl` permission denial, matching Phase 0. |
| Three registration batch tests and two batch engine tests | 1 passed, 4 failed | Four fail inside `BatchRunner.orphan_check()` on sandbox `psutil.process_iter()`/`sysctl` permission denial before processing. |
| Offscreen registration GUI test | Segmentation fault | VTK `QVTKRenderWindowInteractor` cannot initialize in offscreen sandbox. Native Cocoa attempt in Phase 0 also exited 139. |

The combined post-change dependency/core/CLI suite in `/private/tmp/phase1-final-focused.log` reported **82 passed, 1 deselected** in 58.78 s. A final post-change run of all new tests reported **13 passed**. `ruff check src tests`, changed-file `ruff format --check`, and `git diff --check` passed after implementation edits. Repository-wide `ruff format --check src tests` still reports four pre-existing unformatted files outside this Phase 1 change (`core/execution.py`, `core/runs.py`, `core/scientific/adapters.py`, `core/scientific/tensor_qc.py`).

## Acceptance and remaining verification

- [x] Saved paths for all three families persist using legacy QSettings keys; synthetic restart and real temporary INI tests executed.
- [x] New preprocessing/scientific/batch defaults receive saved paths; focused GUI and core argument-path tests executed.
- [x] Clear/change, moved/incompatible/interrupted/empty path and write failure behavior are covered by focused tests.
- [x] Active approved GUI plan protection and historical batch default separation are covered by focused tests.
- [ ] Native GUI verification of the complete per-family setup and all workflow tabs on macOS Cocoa; this sandbox cannot initialize native VTK reliably.
- [ ] Full BatchRunner execution and historical resume/retry against pinned MRtrix/FSL/ANTs paths outside the `psutil` sandbox restriction.
- [ ] Physical Linux and Windows/WSL2 validation of the expanded scientific workflows. Native FSL execution on Windows remains unsupported.
- [ ] GitHub draft PR and CI results for this Phase 1 branch must be recorded after publication.

Do not call Phase 1 fully verified until the unchecked native and BatchRunner checks are run in a suitable environment. Do not weaken version or provenance gates to make a scan look successful. The specific next verification task is to run the full GUI and batch tests on a native macOS host with process enumeration and VTK access, then resolve only residual application failures. Phase 2 startup/navigation optimization follows only after this gate.
