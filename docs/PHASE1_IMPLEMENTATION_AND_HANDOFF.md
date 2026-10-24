# Phase 1 scientific software configuration — implementation and handoff

Date: 2026-10-23 EDT, with Phase 1.2 verification added later that day. Phase 0 baseline: [PHASE0_AUDIT_AND_HANDOFF.md](PHASE0_AUDIT_AND_HANDOFF.md). Scope is the saved MRtrix3/FSL/ANTs installation experience only. Do not merge PR #3. Phase 2 readiness is assessed below; performance code awaits separate Phase 2 instructions.

## Repository and PR state

- Public scientific base: `development/scientific-toolbox-2026-10` at `9c7a3112ba13d5b1c608cbeb7866ef536de4e893`.
- Phase 0 audit branch: `audit/phase0-baseline-2026-10-23` at `d96ab4f`.
- Phase 1 working branch: `development/phase1-scientific-software-2026-10-23`, based on the Phase 0 audit commit. Commit `58f3a6b` carried forward only PR #3's dependency-path and busy-state changes. Commit `4577428` implemented the central configuration, workflow propagation, pinned batch defaults and regression tests.
- Local documentation commit: `f411990`. Draft Phase 1 PR: [#4](https://github.com/kkartikeya-kulkarni/TractoSphere/pull/4), targeting `development/scientific-toolbox-2026-10`.
- Follow-up code commit `a9f120e` clarifies interrupted, failed and incompatible verification messages and adds active-worker/path-replacement regressions. The corresponding remote code commit is `db51c98512659ffc6de7dabb6009faf7eb61ff1e` with the same tree SHA `53baf548309bb03f86b7bcb3b7ff81e667c2104c`.
- The shell cannot resolve `github.com` for `git push`. The GitHub connector published four equivalent focused commits instead; the remote PR head before this final tracking update is `541bfeadfbe958e26a67252c03d83c4dbba4f6cc`. Its tree SHA `dbc1add5c12bb9486b52ea665634afd7230c291b` exactly matches local `f411990^{tree}`. Remote commit hashes differ because GitHub authored the equivalent commits. Preserve this equivalence when continuing work; do not assume a normal fast-forward push from the local branch.
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
| New Phase 1 GUI/core tests | 16 passed in the final GUI regression run | Saved values, restart reconstruction, actual new-batch resolver arguments, registration preview arguments, incompatible/moved/empty paths, replacement, historical separation, approved-plan reopening, active approvals and active workers. |
| Final Phase 1 GUI regression run | 29 passed, 1 deselected in 52.64 s | `test_scientific_software_settings.py`, `test_dependency_setup.py`, `test_software_installation.py`; offscreen VTK viewer case excluded. |
| Focused core software setup, anatomy and batch planning tests | 17 passed | No long-running MRI processing. |
| CLI processing/plan/load tests | 34 passed | CLI parity unchanged. |
| Selected non-VTK execution, planning and tracking GUI tests | 8 passed | Preview invalidation and workflow controls. |
| Batch GUI control test | 1 failed | Journal remained READY instead of PAUSED; underlying BatchRunner encounters sandbox `psutil.process_iter()`/`sysctl` permission denial, matching Phase 0. |
| Three registration batch tests and two batch engine tests | 1 passed, 4 failed | Four fail inside `BatchRunner.orphan_check()` on sandbox `psutil.process_iter()`/`sysctl` permission denial before processing. |
| Offscreen registration GUI test | Segmentation fault | VTK `QVTKRenderWindowInteractor` cannot initialize in offscreen sandbox. Native Cocoa attempt in Phase 0 also exited 139. |

The combined dependency/core/CLI suite in `/private/tmp/phase1-final-focused.log` reported **82 passed, 1 deselected** in 58.78 s before the final message/worker-guard patch. That patch was covered by the final **29 passed, 1 deselected** GUI regression run. `ruff check src tests`, changed-file `ruff format --check`, and full-branch `git diff --check` passed after implementation edits. Repository-wide `ruff format --check src tests` still reports four pre-existing unformatted files outside this Phase 1 change (`core/execution.py`, `core/runs.py`, `core/scientific/adapters.py`, `core/scientific/tensor_qc.py`).

## Acceptance and remaining verification

- [x] Saved paths for all three families persist using legacy QSettings keys; synthetic restart and real temporary INI tests executed.
- [x] New preprocessing/scientific/batch defaults receive saved paths; focused GUI and core argument-path tests executed.
- [x] Clear/change, moved/incompatible/interrupted/empty path and write failure behavior are covered by focused tests.
- [x] Active approved GUI plan protection and historical batch default separation are covered by focused tests.
- [ ] Native GUI verification of the complete per-family setup and all workflow tabs on macOS Cocoa; this sandbox cannot initialize native VTK reliably.
- [x] The selected native Cocoa dependency-path GUI test passed outside the sandbox (Phase 1.2 user report); broader GUI/VTK coverage is still open.
- [ ] Full BatchRunner execution and historical resume/retry against pinned MRtrix/FSL/ANTs paths outside the `psutil` sandbox restriction.
- [x] The three selected native batch execution/resume tests passed outside the sandbox (Phase 1.2 user report); this does not cover every historical toolchain.
- [ ] Physical Linux and Windows/WSL2 validation of the expanded scientific workflows. Native FSL execution on Windows remains unsupported.
- [x] Draft PR #4 opened against the scientific development branch. Its initial CI results are analyzed in the Phase 1.1 section below.

The selected native checks have since passed as recorded in Phase 1.2 below. Broader scientific and GUI verification remains open. Do not weaken version or provenance gates to make a scan look successful.

## Phase 1.1 native verification and CI triage — 2026-10-23/24

This follow-up was run locally on the same ARM64 Mac, on `development/phase1-scientific-software-2026-10-23`. PR #4 was draft and unmerged, targeting `development/scientific-toolbox-2026-10`; its pre-follow-up remote head was `ff8e639c9ed603a08220400d2410c698925cb252`, with the same tree as local `35552cf`. PR #3 remained separate and unmerged.

### CI comparison and cause

The PR #4 [Actions run 40046817126](https://github.com/kkartikeya-kulkarni/TractoSphere/actions/runs/40046817126) finished on all five platforms. Its core test step failed before GitHub Actions could run any GUI tests or viewer smoke tests. The preceding wheel build, archive audit, installation and dependency resolution steps passed on all five jobs. PR #3 [run 40043535129](https://github.com/kkartikeya-kulkarni/TractoSphere/actions/runs/40043535129) failed at the same step.

| Job | PR #3 | PR #4 | Failed-case comparison |
| --- | --- | --- | --- |
| macOS ARM64 3.12 | 31 failed, 801 passed, 126 skipped | 31 failed, 803 passed, 126 skipped | Exactly the same 31 node IDs |
| macOS Intel 3.12 | 31 failed, 801 passed, 126 skipped | 31 failed, 803 passed, 126 skipped | Exactly the same 31 node IDs |
| Ubuntu 3.12 | 31 failed, 801 passed, 126 skipped | 31 failed, 803 passed, 126 skipped | Exactly the same 31 node IDs |
| Ubuntu 3.13 | 31 failed, 801 passed, 126 skipped | 31 failed, 803 passed, 126 skipped | Exactly the same 31 node IDs |
| Windows 3.12 | 36 failed, 787 passed, 135 skipped | 36 failed, 789 passed, 135 skipped | Exactly the same 36 node IDs |

The shared 31 comprise 20 `tests/core/test_batch_engine.py`, three `tests/core/test_registration_batch.py`, and eight `tests/core/test_roi_filtering.py` cases. Batch and ROI fixtures hard-code `BIN = Path("/usr/local/mrtrix3/bin")`; hosted runners do not install MRtrix there. Their plans become `BLOCKED` or their resolver raises `MRtrix mrconvert was not found`, including cases whose assertions later expect execution/resume. Two registration cases set `FSLDIR = Path.home()/"fsl"`; hosted macOS traceback reports that this runner-home FSL folder does not exist. The remaining registration atlas case is also blocked before its expected native failure. The five extra Windows cases already failed on PR #3: one batch directory permission error, one NIfTI `OSError: Invalid argument`, and three existing acquisition/project/workflow assertion failures (`test_reference_association_and_grid`, `test_two_runs_one_subject_and_shared_calibration`, `test_changed_and_closed_inputs_require_reload`). Their exact causes beyond the reported path, filesystem and assertion evidence remain unproven. These failures are not demonstrated Phase 1 application regressions. There is no evidence that Phase 1 introduced a new failed CI test. Do not relax the scientific gates or substitute fake executables to turn these tests green. Portable fixture setup or an explicit external-software CI lane is separate work.

### Actual local commands and outcomes

All Python commands below used `PYTHONPATH=src` and `../application/.venv/bin/python` (Python 3.12), unless otherwise stated.

| Check | Result | Verification boundary |
| --- | --- | --- |
| Explicit native inventory of `/usr/local/mrtrix3/bin` and `~/fsl` via `inspect_dependencies(..., search_common=False)` | MRtrix3 **ready**, ten required executables at 3.0.5; FSL **ready**, 13 component records including `eddy_cpu` 2401.2. ANTs **missing**. | Actual installed tools and metadata were queried; no real ANTs binary was found. |
| Native QSettings/`DependencyController` round trip with a temporary INI: inventory, `apply_verified_family` for MRtrix3 and FSL, reconstruct controller and settings | Passed. Restored `/usr/local/mrtrix3/bin` and canonical `~/fsl/bin`; ANTs stayed unconfigured. | Exercises real filesystem writes and real tool verification without a GUI window. |
| Fresh offscreen `MainWindow` constructed from that same temporary INI after the real MRtrix3/FSL verification and save | Passed in 1.80 s. Restored paths reached preprocessing inputs, shared scientific/tracking defaults, registration FSL, and new batch defaults. | Full GUI/settings reconstruction, but offscreen Qt; ANTs remained unconfigured and native Cocoa/VTK remains untested. |
| `QT_QPA_PLATFORM=offscreen ... pytest -q tests/gui/test_scientific_software_settings.py tests/gui/test_dependency_setup.py -k 'not first_run_viewer_only_during_scan_and_repeat_suppressed' --tb=short` | **20 passed, 1 deselected** in 32.71 s. | Covers synthetic MRtrix/FSL/ANTs save/restart and actual new preprocessing, registration, scientific/tracking and batch argument propagation; offscreen is not native Cocoa/VTK. |
| `QT_QPA_PLATFORM=cocoa ... pytest -q tests/gui/test_scientific_software_settings.py::test_legacy_saved_paths_reach_new_workspaces --tb=short` | Exit **139**, segmentation fault constructing `gui/qc.py:189` VTK window. | Native GUI assertions never ran. An escalated retry was rejected by the current execution approval policy; no macOS protection was bypassed. |
| `pytest -q tests/core/test_batch_engine.py::test_actual_execution_and_resume_no_duplicate tests/core/test_batch_engine.py::test_tool_change_blocks_reuse_without_rerun tests/core/test_registration_batch.py::test_actual_batch_registration_qc_resume --tb=short` | **3 failed** in 0.59 s, all at `BatchRunner.orphan_check()` before a job runs. | `psutil.process_iter()` calls macOS `sysctl`, which returns `PermissionError: [Errno 1] Operation not permitted` in this sandbox. Resume/retry is unverified here. |
| Strengthened `test_reopened_approved_batch_keeps_recorded_toolchain` | **1 passed** in 1.33 s, offscreen. | Approved plan JSON, including nonempty executable paths and SHA-256 records, is unchanged after the global default changes. This does not execute a historical batch. |

The first round-trip assertion incorrectly expected the FSL root string to remain `~/fsl`; the controller intentionally saves the verified canonical executable directory `~/fsl/bin`. Re-running with that expected path passed. This was a verification-script expectation error, not an application failure. `ruff check` and `ruff format --check` on the strengthened test plus `git diff --check` passed after formatting. Phase 1.1 did not change scientific execution code.

### Phase 1.1 acceptance and next task

- [x] Real installed MRtrix3 and FSL verified and persisted across a fresh local QSettings/controller construction.
- [x] Synthetic ANTs save/restart and cross-workspace reuse covered by offscreen GUI tests; real ANTs native verification is unavailable because ANTs is not installed.
- [x] Historical approved-plan executable paths and SHA-256 records proved unchanged by a global default change, without running the batch.
- [x] PR #3 versus PR #4 failed test names and logs compared on all five CI jobs; no new failing node IDs.
- [x] The selected native Cocoa dependency-path GUI test subsequently passed outside the sandbox; broader Cocoa/VTK GUI coverage remains open.
- [x] The three selected BatchRunner execution/resume tests subsequently passed outside the sandbox; historical FSL/ANTs toolchain revalidation remains open.
- [ ] Native ANTs verification: no installed ANTs 2.6.5 binary on this Mac.
- [ ] Passing PR #4 CI: pre-existing external-tool fixtures block the core step and all downstream GUI steps.

**Phase 1.1 initially had unverified native checks; Phase 1.2 below supersedes those two selected checkpoints.** Complete native GUI, ANTs and historical FSL/ANTs execution coverage remains open.

The first Phase 1.1 follow-up [CI run 40047596347](https://github.com/kkartikeya-kulkarni/TractoSphere/actions/runs/40047596347) stopped earlier than the baseline core test failures: the release audit found a literal hosted-runner home path in this handoff. That path was replaced with a generic description. The repository's source release audit then **passed** locally with 372 files and zero findings. Final-head [CI run 40047818751](https://github.com/kkartikeya-kulkarni/TractoSphere/actions/runs/40047818751) passed the release audit on all five jobs and retained exactly the PR #3 failed test IDs: 31 on each macOS/Ubuntu job and 36 on Windows. The core failures prevented CI GUI tests and viewer smoke tests from running.

## Phase 1.2 normal-Terminal native checkpoint — 2026-10-23 EDT

The user reran the previously blocked tests directly in a normal macOS Terminal outside the Codex sandbox and reported **4 passed, 0 failed**:

| Selected test group | User-reported result | What this verifies |
| --- | --- | --- |
| One native Cocoa GUI dependency-path test previously attempted above | **1 passed in 1.41 s** | The selected window and dependency-path assertions completed under native Cocoa. It does not establish that the complete GUI/VTK suite passed. |
| Three BatchRunner execution/resume tests previously attempted above | **3 passed in 3.26 s** | The selected execution, tool-change/reuse, and registration resume cases completed with process enumeration available. It does not cover every historical MRtrix/FSL/ANTs batch configuration. |

The current local TractoSphere checkout is `development/phase1-scientific-software-2026-10-23` at `d9dca2f86f54a8eae033956956deba88273564d4`, with a clean working tree before this documentation update. Its reflog shows no checkout or commit after that SHA. The Terminal report did not include `git rev-parse HEAD` output, so the exact test-to-commit association is **inferred from the unchanged checkout**, not independently captured from the Terminal test process. These four results are user-reported and were not rerun by Codex inside its restricted sandbox.

### Phase 2 readiness and remaining limits

- [x] Selected native Cocoa dependency-path test passed in normal Terminal.
- [x] Selected native batch execution/resume tests passed in normal Terminal.
- [ ] Real ANTs 2.6.5 installation verification on this Mac; no real ANTs installation was found in Phase 1.1.
- [ ] Broader native GUI/VTK suite and all scientific workflows; no claim of full-suite passage.
- [ ] Full historical batch toolchain coverage, including unavailable/incompatible original binaries and FSL/ANTs provenance during resume or retry.
- [ ] Pre-existing CI fixture failures; final PR #4 run remains red at the core step with no new failing test IDs relative to PR #3.

**Decision:** The selected formerly blocked native checks now pass, so Phase 1 is sufficiently verified to begin Phase 2 development with these limits tracked. This is a readiness decision, not a claim that every Phase 1 acceptance checkbox or scientific workflow has passed. Keep PR #4 draft and unmerged; wait for the Phase 2 instructions before changing startup or navigation performance code.
