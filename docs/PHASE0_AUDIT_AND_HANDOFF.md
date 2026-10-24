# Phase 0 repository audit and development handoff

Audited 2026-10-23 EDT. Scope: [issue #2](https://github.com/kkartikeya-kulkarni/TractoSphere/issues/2), confirmed by the project owner as the master specification. This is an audit and plan, not an implementation or scientific acceptance record.

## Repository and PR state at audit

- Public product repository: `kkartikeya-kulkarni/TractoSphere`. Audited checkout was clean at `9c7a3112ba13d5b1c608cbeb7866ef536de4e893`, branch `development/scientific-toolbox-2026-10`, matching its `origin` tracking ref. The local audit document is on a new branch from that exact commit, `audit/phase0-baseline-2026-10-23`.
- Cached `origin/main` was `014c2cad9cc948cd5192feac88a923492e9312b6`. Local `main` was `18a0c4a`; it was one commit behind cached `origin/main`. The development baseline and cached `origin/main` each had one unique commit. No main branch or release tag was changed.
- A separate local worktree had the README-image branch `docs/readme-current-toolbox-2026-10-21` at `014c2ca`. The original private development checkout was clean on `scientific/contracts-stage-a-2026-10-18` at `d6823d63f57edf525ff79ce9857409f5f6aa19d8`; its local `main` and other historical scientific/UI branches remain separate. The large parent data directory is not a Git repository.
- [Draft PR #3](https://github.com/kkartikeya-kulkarni/TractoSphere/pull/3) is open, unmerged, and targets `development/scientific-toolbox-2026-10`. Its head is `development/usability-improvements-2026-10` at `1cf75fe8667d3164724bad12c48ef2a601d1fd9f` (9 commits; 8 changed files; +191/-15). Its head object was not present locally. GitHub reported no commit statuses. The PR description explicitly says its tests had not run. No PR changes were applied to this audit branch.
- PR #3 edits Batch, main-window dependency propagation, Preferences availability, Registration wording, Tracking wording, and three GUI test files. It is a first pass on issue #2, not completion of the six acceptance areas. It does not introduce guided Batch stages, lazy page construction, a full Registration flow, or direct viewer ROI rule preview.

Read before this audit: public `AGENTS.md`, `README.md`, `docs/architecture.md`, `docs/features.md`, `docs/limitations.md`, `docs/PROGRESS.md`, current source and tests. Also read the original development `AGENTS.md`, `docs/SCIENTIFIC_COMPLETENESS_AUDIT.md`, `docs/SCIENTIFIC_FEATURE_GAP_MATRIX.md`, `docs/SCIENTIFIC_DEVELOPMENT_ROADMAP.md`, `docs/SCIENTIFIC_ARCHITECTURE.md`, `docs/SCIENTIFIC_CONTRACT_RESOURCE_REPAIR_REPORT.md`, and UI redesign progress/plan. Historical reports are evidence of earlier runs, not newly verified results.

## Root causes of the six issue #2 usability areas

| Area | Current cause at the audited baseline | PR #3 scope / remaining work |
|---|---|---|
| Configure tools once | `DependencyController` persists locations, but `MainWindow.apply_dependency_locations()` sends MRtrix3 to Scientific Planning and preprocessing, not Batch. `BatchPage` starts with an empty path. Empty saved values become `Path("")` in Scientific Planning. Preferences only checks the preprocessing worker when applying changes. | PR #3 initializes Batch from settings, propagates MRtrix changes, handles blank paths, and extends the Preferences busy gate. It still needs execution of its tests, restart/moved-path checks, FSL/ANTs parity, and native UX review. |
| Guided tractography | `TrackingExecutionPanel` has an operation combo and advanced disclosure, but its primary form presents algorithm, mode, 5TT, FOD/DWI, mask, seed, track, reference, and count together. ROI roles read `Include all`, `Exclude any`, `Ordered waypoints`; viewer ROI use triggers an export/import path rather than an immediate selection and membership preview. | PR #3 improves explanatory text and ROI labels. A task choice, conditional fields, direct ROI selection, and truthful full-source preview remain. |
| Task-first workspaces | `ScientificPlanningPage` is a shared registry/inventory/task graph with eight technical tabs built at once. Other processing screens mix goal, engine configuration, exact command preview, and result actions; help/preset/next-action patterns are inconsistent. | PR #3 changes wording in two panels only. Each workspace needs a narrow user task, requirements, preset, status, recovery and QC route while retaining Expert controls and the exact underlying task. |
| Startup/navigation | `MainWindow.__init__()` constructs `WorkflowPage`, `QCPage`, `ScientificPlanningPage` and all its scientific subpanels, `BatchPage`, Results and Preferences before first show. The viewer is already lazy. `BatchPage` starts a 600 ms refresh timer immediately; when a journal is open, refresh rereads it and rebuilds every table item. | PR #3 has no performance change. Profile first, then defer heavy pages and constrain journal refresh without breaking worker ownership or state handoffs. |
| Batch Processing | `BatchPage` begins with a multi-field Inputs tab, adapter identifier, editable JSON `QPlainTextEdit` workflow and resource matrix. It has actual plan/run/journal/QC services but no guided subject-review-to-run path. The whole table is rebuilt and columns resized each refresh. | PR #3 only reuses the MRtrix path. Build a typed beginner flow over the existing batch planner/engine; keep JSON and budgets in Advanced. |
| Registration | `RegistrationPanel` lists `ALGORITHMS` names and a generic “Select inputs and parameters” action, so users must map low-b, FLIRT, epi_reg, FNIRT, transform conversion and resampling themselves. Existing native atlas QC has overlays and review evidence, but the ordinary source→target workflow is not assembled around user goals. | PR #3 renames choices and adds concise help. It does not create the guided alignment sequence or integrated before/after review flow. |

These are source-supported causes, not claims from a formal user study. Issue #2 itself reports that the Viewer feels more approachable and cold launch/navigation feel slow.

## Scientific audit disposition

The private Stage 9 audit evaluated an older integration commit, `f18203c`, and counted 139 scoped requirements: 77 implemented/tested, 5 implemented but unvalidated for scope, 26 partial, 21 missing, and 10 blocked. Those counts must **not** be presented as a fresh tally for the public development branch. Its previous headless result was 994 passed/1 failed; 40 selected GUI tests passed. The later Stage A report says its contract/resource repair then passed 1,028 headless and 190 GUI tests in that original checkout. This session did not reproduce those totals.

| State on the public development baseline | Scope and qualification |
|---|---|
| Implemented | BIDS/custom discovery; acquisition/gradient checks; MRtrix conversion, denoise, Gibbs and masking; metadata-gated TOPUP/EDDY and bias correction; DTI, CSD/FOD; tracking/ACT; SIFT2; connectome matrices; registered atlas import, FSL/MRtrix transform tasks, native atlas QC; ROI filtering; batch planning/execution/recovery; shared GUI/CLI/Python scientific services. Source and bounded tests exist. This does not imply biological acceptance. |
| Stage A repair present | Shared FNIRT resource reservations, versioned registration review applicability across downstream consumers, Pillow core dependency, and tracking/SIFT2 task parity are in current source. Their historical validation is in the Stage A report; this sandbox's process restrictions prevented a clean full-suite repeat. |
| Incomplete | QUAD/SQUAD, broader eligible EDDY/N4 entry/settings, unified dependency inventory, ordinary DTI/Batch task construction, validated template-specific FNIRT policy, complete cortical/subcortical parcellation path, high-resolution 5TT transport, provenance-aware external derivatives, shareable sanitized metadata export, full tractometry, and publishable figure controls. Prioritized scientific specifications B/C remain separate from issue #2 UX implementation. |
| Deliberately blocked | Unsupported ANTs binary/HDF5/nonlinear transform stacks; some alternate-grid warp, nonlinear probability/5TT/FOD/tensor transport; unknown subject/space or missing acquisition metadata; invalid/stale registration evidence; TinyTrack conversion after failed spatial round-trip. Keep these gates. |
| Unverified | Human anatomical approval and biological validity; large/full 10M filtering and weight-vector support; independent participant cohort, large nonlinear resource peaks, physical Windows/Linux/Intel Mac scientific runtime, live network-storage durability, and cross-platform GUI behavior of the expanded toolbox. Historical 105014 experiments are not new validation here. |

Stage A changed the old Stage 9 P0 FNIRT under-reservation and contradictory registration review findings. Do not revive the older Stage 9 failure as a current confirmed defect. The current failures below primarily show limits of this sandbox and cancellation tests; they still require a trusted rerun before declaring the public branch green.

## Tests executed in Phase 0

Host: macOS 26.3 / Darwin arm64, Python 3.12.15, PySide6 6.12.0, VTK 9.7.1, psutil 7.2.2. Commands used `PYTHONPATH=src`, `OPENBLAS_NUM_THREADS=1`, and the existing development virtual environment. The meaningful native run also supplied the installed FSL directory and MRtrix/FSL binaries on `PATH`; no MRI participant processing was requested.

1. `pytest tests/core tests/cli tests/test_*.py -x -vv --tb=short`: **42 passed, 1 failed in 81.50 s**. `tests/core/test_act.py::test_real_act_child_cancellation_does_not_publish` expected `cancelled` but received `validated_pending_qc`. It was allowed to finish; no source changed.
2. The same collection with `-k 'not test_real_act_child_cancellation_does_not_publish' -q --tb=short`: **956 passed, 25 failed, 1 deselected in 179.26 s**. Together, the two runs executed all 982 collected cases: 956 unique passes, 26 unique failures. The first unconfigured attempt was interrupted and is not counted as a complete run.
3. Failure grouping: 18 Batch Engine cases and 2 Registration Batch cases fail or cascade from `psutil.process_iter()` raising macOS `PermissionError` from sandboxed `sysctl`. `test_real_process_resources_and_missing_process` also cannot observe process metrics. CSD, SIFT2, Tracking and ACT cancellation tests do not observe the expected running-child/resource sample; some finish as `validated_pending_qc`. The unicode subprocess test and interrupted/restart batch tests also fail in this process-restricted environment. Do not assert all 26 are product defects or all are harmless; rerun outside this restriction and inspect residuals.
4. `pytest tests/gui/test_dependency_setup.py -q` with `QT_QPA_PLATFORM=offscreen`: one test passed, then the process **segfaulted (exit 139)** in `QVTKRenderWindowInteractor` while opening the Viewer. The GUI test fixture explicitly says offscreen Qt is for non-VTK checks only.
5. Excluding that viewer case, the dependency setup module: **5 passed, 1 deselected in 8.79 s**. Focused Tracking controls/ROI ordering and Batch history tests: **4 passed in 8.13 s**. These are offscreen UI tests, not full native visual acceptance or PR #3 tests.
6. `ruff check src tests`: **passed**. A native Cocoa startup timing attempt exited **139**, with PasteBoard/XPC service errors. Full native GUI testing and viewer timing were not available in this sandbox.

The PR #3 head was not checked out or tested; its author also labels its verification as pending. A clean result for the baseline is **not** established here.

## Performance measured in this environment

An instrumented fresh Python process created `QApplication`, `Theme`, `MainWindow`, showed the Start workspace and processed Qt events. It used `QT_QPA_PLATFORM=offscreen`, no MRI data, and isolated empty settings. It timed navigation with `perf_counter()`. The first observed process took **1.486 s** from script start to shown; four subsequent fresh processes took **1.265–1.284 s**, of which `MainWindow` construction took **0.979–0.997 s**. These are warm filesystem-cache/offscreen measurements, not a true disk-cold or physically rendered launch. RSS after ordinary navigation was **236.6–240.1 MiB**.

| Interaction, four subsequent runs | Observed time |
|---|---:|
| Batch first / repeat | 27.8–28.4 / 19.8–20.3 ms |
| Models first / repeat | 27.3–27.8 / 24.2–25.0 ms |
| Registration first | 11.4–11.8 ms |
| Preferences first | 7.0–7.1 ms |

A separate synthetic 1,000-row Batch table benchmark (three fresh processes, no journal I/O) measured initial `render()` **41.5–42.4 ms**, row 500 selection **15.1–15.3 ms**, and complete rerender **43.9–44.2 ms**; RSS **242.9–243.9 MiB**. The 600 ms timer and journal reread under an actual active 1,000-subject study, native VTK first-open, dialogs, true cold startup and physical Linux/Windows timings remain unmeasured. Do not use these numbers as before/after acceptance targets without a representative UI harness.

## Subsequent phases: small, independently testable tasks

Each item should be one reviewable change with focused tests, exact source/derivative preservation, and an updated handoff. Begin from the scientific development branch in an isolated branch/worktree. Do not update `main`, a release, or PR #3 implicitly.

### Phase 1 — Close the draft first-pass patch safely

1.1 Fetch PR #3 into an isolated worktree; verify its exact head/base and existing patch against issue #2. Run its three changed GUI test modules, with native Cocoa/VTK outside this sandbox. Record CI and manual light/dark results. Exit: exact commands and pass/fail log tied to its head SHA.

1.2 Test saved MRtrix/FSL/ANTs path reuse across restart, blank/reset/moved installs, and every consuming workspace. Exercise configuration edits during preprocessing, scientific and batch workers. Exit: no accidental current-directory MRtrix path, no in-flight toolchain switch, and exact per-run binary/version/hash provenance remains unchanged.

1.3 Review labels on synthetic workflows with at least one first-time researcher; repair only PR-scoped wording/test defects on its own branch. Exit: clear wording without implying command success is scientific approval. Decide PR merge separately after checks; do not fold later redesign into it.

### Phase 2 — Reproducible performance and lifecycle baseline

2.1 Add a repo-owned benchmark harness for process startup, first/repeat workspace and dialog opens, VTK first/repeat viewer, and a 1,000+ subject table with a journal fixture. Capture wall time, RSS, host/Qt/VTK versions, and distribution across runs. Exit: reproducible JSON results on macOS ARM64, with unsupported environments marked.

2.2 Instrument constructor and UI-thread I/O spans. Exit: attribution to named pages, journal reads, VTK construction or dependency scans rather than an assumed bottleneck.

2.3 Lazily create Batch and Scientific Planning panels as independent steps, preserving signal handoffs, restored state, cancellation and QObject retirement. Gate the Batch timer to active visible/running work, update only changed rows, and avoid synchronous journal rereads on every tick. Exit: focused lifecycle/reopen tests, a full suitable-environment GUI run, and measured before/after results.

### Phase 3 — One scientific software setup experience

3.1 Make one shared saved dependency model and explicit precedence for default path versus run-pinned path. Exit: restart, change/reset/recheck and moved-install tests across preprocessing, reconstruction, tracking, registration and batch.

3.2 Show per-tool path/version/health, nonblocking repair route and visible reason for invalid configuration. Keep prepare/execute's stricter exact binary checks. Exit: a missing tool still permits Viewer use, while affected scientific execution blocks with actionable text.

### Phase 4 — Guided tractography

4.1 Add a three-goal entry flow: generate, filter, density. Show only applicable method/seed/ACT/filter fields, keeping validated advanced settings. Exit: UI value → typed task → command preview → argv/provenance parity tests for each route.

4.2 Select existing viewer ROIs directly into rule roles and preview actual membership with an explicit source/sample scope. Preserve source mask/tract hashes. Exit: known-geometry inclusion/exclusion/ordered/endpoint fixtures and unchanged originals; unsupported identity/space combinations block.

4.3 Provide a short review/QC/output path after run. Exit: cancellation/reopen/retry and native visual tests in both themes.

### Phase 5 — Task-first scientific workspaces

5.1 Inventory each non-Viewer page against goal, prerequisites, preset, one next action, status/error, QC/result handoff, and Advanced access. Choose one page per change, beginning with DTI/CSD and preprocessing. Exit: no hidden scientific parameter loss in GUI→typed task→CLI/batch parity tests.

5.2 Standardize plain-language help and error recovery without weakening acquisition, model, registration-review or provenance gates. Exit: synthetic walkthrough and keyboard/screen-reader checks for each page.

### Phase 6 — Guided Batch Processing

6.1 Build typed Select → Review subjects/acquisitions → Choose preset → Validate/plan screens over the existing discovery/planning service. Keep JSON recipe/adapter/resources under Advanced. Exit: missing metadata and blocked acquisitions shown before execution, with exact planner parity and no false eligibility.

6.2 Add Run/monitor → QC/results screens using the existing engine/journal; expose per-subject progress, pause/cancel/resume/retry, immutable outputs and provenance. Exit: multi-subject synthetic recovery and failure-isolation tests; no duplicate scientific execution on verified reuse.

6.3 Validate 1,000+ rows, journal update cost and navigation responsiveness with the Phase 2 harness. Exit: measured interaction distribution and no UI-thread stall from whole-table rebuilds.

### Phase 7 — Guided Registration and atlas review

7.1 Create beginner goals for low-b reference, EPI/T1 alignment, atlas-to-diffusion and inspection; put transform utilities in Advanced. Exit: each goal builds the existing typed task with correct moving/fixed identity, direction, interpolation, output space and exact command.

7.2 Connect native overlay, blink/outline/checkerboard, crosshair and slice QC to an explicit human decision. Exit: accepted/rejected/stale review tests across GUI/CLI/batch/ROI/connectome, no automatic anatomical approval and no rerun on reopen.

### Phase 8 — Acceptance and release decision

8.1 Execute complete headless and native GUI suites in an environment permitting process enumeration and Cocoa/VTK; resolve any remaining failures. Recheck source hashes, packaging and no private data. Exit: reproducible logs tied to commit SHA.

8.2 Run macOS ARM64 and representative Linux/Windows CI screen/interaction and timing checks, marking unsupported native scientific execution explicitly. Exit: platform-specific timings, screenshots in both themes, keyboard/accessibility smoke and a user walkthrough against every issue #2 criterion.

8.3 Review open scientific Stage B/C gaps before any wider scientific runtime/release claim. UX completion alone does not validate QUAD/SQUAD, nonlinear anatomical accuracy, new formats, cohorts or operating systems. Exit: a scoped feature/version/limitations matrix with evidence, then a separate merge/release decision.

## Exact next development task

**Phase 1.1:** check out PR #3 head in a new isolated worktree, run its added GUI tests plus native smoke on a host with Cocoa/VTK and process enumeration, and record failures against head SHA `1cf75fe`. Do not merge or expand the redesign during that task. The first potential code fix is the smallest failure revealed by those tests; do not assume the draft patch is verified.
