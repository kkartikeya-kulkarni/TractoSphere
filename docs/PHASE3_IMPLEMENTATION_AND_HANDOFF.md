# Phase 3 — Batch Processing usability handoff

Branch: `development/phase3-batch-usability-2026-10-23`, created locally from the Phase 2 checkpoint `0a21e53` on 2026-10-23. Do not merge PR #3/#4, change public `main`, push this branch, or publish a release without authorization. The latest application fix is `bc3723c`; its guided and existing Batch GUI tests passed in the user's normal Cocoa session. Phase 1 real ANTs verification, broader native GUI coverage and pre-existing CI fixtures remain open in the earlier handoffs.

## 3.0 audit and root causes

The prior `BatchPage` displayed dataset path, mapping/manifest path, output path, MRtrix location, adapter identifier, editable workflow JSON, resources, plan approval, execution controls, history, journal rows, QC and logs at once. It provided no route from discovered acquisitions to a selected subset. `build_batch_plan` always planned every acquisition in the Study. The GUI depended on users knowing adapter names and workflow serialization even for tensor metrics or DWI conversion. The back end already offered BIDS/custom/manifest discovery, typed scientific adapters, exact plan digests, approvals, journals, recovery and QC; replacing it would have weakened the scientific contract.

The Phase 2 Batch page is constructed on first navigation and reused. Its active/visible 600 ms journal timer, journal stat signature and selection-by-key rendering remain in place. The legacy controls remain under **Advanced Settings** for existing workflows and tests. The new guided controls write to the same `BatchPage.study`, workflow editor, output field, resource widgets, plan, approval checkbox, folder and journal.

## Implemented checkpoints

| Checkpoint | Change | Evidence and limit |
|---|---|---|
| 3.1 Data and subjects | Default seven-step Batch view: Select Data, Review Subjects, Choose Workflow, Configure, Review Plan, Run & Monitor, Results & QC. BIDS/custom mapping/CSV or TSV/saved study and saved batch entry points call existing discovery/load services. Recent saved-study metadata is kept in application settings; opening revalidates the study file. Acquisition checkboxes use stable `Acquisition.key` through sorting/filtering and navigation. A dataset change requires rediscovery; an in-flight stale discovery result is rejected. | Offscreen GUI tests cover all three importers, recent-study restart, sorting/filtering/selection, stale discovery and 960×640 light/dark layout. Discovery is distinct from scientific eligibility. |
| 3.1 core parity | `build_batch_plan(..., selected_keys=...)` validates nonempty, unique, known acquisition keys, plans only those rows and records sorted selected keys inside a new plan digest while retaining the complete Study provenance. The CLI accepts repeated `--acquisition-key`. Plans without a selection retain their old payload format. | Core/CLI subset tests passed. Historical plan files are not migrated or rewritten. Existing implementation-hash checks remain active. |
| 3.2 Workflow and configuration | Guided presets use the real preprocessing converter and tensor-metrics DTI adapter. They are shown only for selected acquisitions with the corresponding discovered input role; missing roles are explained and ultimately blocked by the planner. Conversion requires explicit history confirmation and asserts no prior correction claims. Saved/advanced workflows use the same JSON field; edits in Advanced become the guided saved choice. A saved workflow must be explicitly loaded or edited before use. The configure step uses the existing output, resource and centralized Phase 1 software defaults. | Offscreen GUI and actual MRtrix dry-run test passed. Other scientific adapters remain available in Advanced; no unvalidated preset was invented for them. |
| 3.3 Plan review | The existing asynchronous `build_batch_plan` supplies actual eligible/deferred/blocked counts, reasons, stages, selected count, output and exact commands. User-friendly next actions retain original error details. Guided approval mirrors the original digest-bound checkbox. Editing a relevant field invalidates approval; an in-flight plan result is discarded if configuration changed. Back/forward navigation with no edit retains approval. | Offscreen tests cover subset plan, blockers, no execution before approval, invalidation and stale plan discard. Scientific version/input/provenance checks were not relaxed. |
| 3.4 Monitoring | The guided journal table shows real subject/session/acquisition/stage/status/QC and selected-stage details. Existing pause scheduling, resume, selected/batch cancellation, retry and worker log actions are exposed. The Phase 2 stat-signature shortcut still avoids unchanged journal parsing and table rebuilds; inactive polling remains stopped. | Phase 2 refresh tests passed. At `bc3723c`, the normal Mac Terminal completed the guided execution and existing Batch GUI tests: 6 passed. |
| 3.5 Results/QC/history | Results lists journal stages, errors, manifest paths and QC states. Existing `inspect_run`, product-to-Viewer signal, study QC, registration QC, explicit scientific review and report export remain the sole back-end routes. Saved batch reopening pins its previous approved plan; a new batch is distinct and uses current software defaults. | The new native output-inspection assertion found a real `KeyError` at `a18cb1e`. `bc3723c` handles multi-file diffusion products in results and routes them to the existing hash-checked loader. All six native Batch tests passed after this fix. A full native Viewer load remains unverified. Execution completion is never labeled scientific approval. |
| 3.6 Layout and performance | Seven steps use the existing Qt theme and selectable labels. The Advanced container scrolls without forcing the main window wider than 960 px. The subject table is built on discovery, never on step return. No timer, scientific scan or executor was added to initial Batch construction. | Screenshots inspected in offscreen 960×640 dark theme; automated 960×640 light/dark size tests passed. Native Cocoa layout remains pending. |

## Files and commits

- `7d45f8a` — `src/dmri_studio/core/batch/planning.py`, `src/dmri_studio/cli/batch.py`, `tests/core/test_batch_selection.py`: selected-acquisition planning and CLI parity.
- `2f4c97d` — `src/dmri_studio/gui/batch.py`, new `src/dmri_studio/gui/batch_guided.py`, new `tests/gui/test_batch_guided.py`, new `tests/gui/test_batch_guided_native.py`, new `scripts/benchmark_batch_guided.py`: guided view and tests.
- `c76aa32` — `README.md`, `docs/batch_processing.md`, this handoff: guided user documentation and initial handoff.
- `a18cb1e` — `tests/gui/test_batch_guided_native.py`: assert the guided **Inspect Verified Output** action exposes a product and that the Open action emits it.
- `bc3723c` — `src/dmri_studio/gui/batch.py`, `batch_guided.py`, `main_window.py`, and guided GUI/native tests: handle products with a `paths` map, retain verified handoff context and use the shared transactional loader for diffusion output.

No core scientific adapter, command compiler, registration transform, tensor algorithm, batch scheduler, journal/recovery semantics, or output validation was changed. The `selected_acquisition_keys` field is present only in newly requested subset plans; the full study, exact row recipes, dependency identities and implementation hashes are still part of approval. A historical batch remains inspectable. Resuming a plan produced under a different orchestration implementation hash can be rejected by the existing safety check; do not bypass that check to make a historical run appear compatible.

## Actual verification, 2026-10-23

Environment in restricted Codex execution: Apple ARM64, macOS 26.3, Python 3.12.15, existing `../application/.venv/bin/python`, `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`, `OPENBLAS_NUM_THREADS=1`. All tests below were executed locally; offscreen Qt is not native Cocoa/VTK verification.

| Command/selection | Result |
|---|---|
| `pytest -q tests/gui/test_batch_guided.py tests/gui/test_batch_guided_native.py::test_guided_bids_subset_reaches_exact_eligible_plan tests/gui/test_phase2_batch_refresh.py tests/gui/test_phase2_lazy_workspaces.py tests/gui/test_scientific_software_settings.py tests/core/test_batch_discovery.py tests/core/test_batch_selection.py tests/core/test_batch_software_defaults.py tests/cli --tb=short` | **86 passed in 73.32 s** at `bc3723c`, including the new output-routing regression. Earlier, the first combined run at the initial GUI checkpoint yielded 83 passed/1 failed from a new-batch placeholder-study defect that was fixed before the 85-pass rerun. Includes real MRtrix dry-run planning from a generated BIDS dataset with a selected subset; no scientific process was executed in this offscreen command. |
| `pytest -q tests/gui/test_batch_gui.py --tb=short` | **1 passed, 3 failed** in 5.76 s. The three runner paths stopped with `[Errno 1] Operation not permitted (originated from sysctl() malloc 1/3)` and their journals remained `READY`; this is the same restricted-process-enumeration failure documented in Phase 2, not evidence of a scientific failure or Phase 3 regression. The history/table test passed. The command was not repeatedly rerun. |
| Selected core Batch tests including `test_missing_inputs_isolated_dry_run`, `test_dependency_cycle_rejected`, `test_empty_batch_is_not_success`, and `test_cli_discovery_plan_execution_and_resume` | **3 passed, 1 failed**. The CLI execution case reached a valid eligible plan, then returned error 2 at the same `sysctl` process-enumeration denial. No scientific logic was changed to bypass it. |
| `ruff check` and focused `ruff format --check` on all changed Python files | Passed again at `bc3723c`. |
| `pytest -q tests/core/test_runs.py tests/core/test_projects.py --tb=short` | **9 passed in 0.20 s** at `bc3723c`; covers the existing verified-product loader and project binding contracts used by the new Batch handoff. |
| Normal Mac Terminal, Cocoa: `pytest -q tests/gui/test_batch_guided_native.py tests/gui/test_batch_gui.py --tb=short` at clean `c76aa32` | **6 passed in 8.36 s; exit 0.** This verified actual synthetic BIDS conversion, guided subset planning, journal/QC/history and existing Batch execution tests. The guided test at this commit did not click **Inspect Verified Output**. |
| Normal Mac Terminal, Cocoa: `pytest -q tests/gui/test_batch_guided_native.py::test_native_guided_execution_results_qc_and_history --tb=short` at `a18cb1e` | **1 failed, 1 Qt teardown error; exit 1.** The output inspection worker returned a verified DWI product with `paths`, and `BatchPage.result` eagerly evaluated `Path(p["path"])` while computing a default label, raising `KeyError: 'path'`. This was a genuine application defect; the repaired test passed in the six-test native rerun at `bc3723c`. |
| Normal Mac Terminal, Cocoa: `pytest -q tests/gui/test_batch_guided_native.py tests/gui/test_batch_gui.py --tb=short` at `bc3723c` | **6 passed in 8.57 s; exit 0.** User-supplied Terminal output includes the tested HEAD. The guided test now clicks **Inspect Verified Output**, observes the verified multi-file DWI product, exercises the Open product signal, checks QC and historical batch identity. Existing Batch GUI tests also pass in the permitted native session. |

Normal-Terminal command executed successfully for the repaired application code at `bc3723c`:

```sh
cd ~/Projects/dmri-studio2.0/TractoSphere-publication
git status --short --branch
git rev-parse --short HEAD
PYTHONPATH=src QT_QPA_PLATFORM=cocoa OPENBLAS_NUM_THREADS=1 ../application/.venv/bin/python -m pytest -q tests/gui/test_batch_guided_native.py tests/gui/test_batch_gui.py --tb=short
```

The new native test executes a generated, non-sensitive two-subject BIDS conversion after choosing one acquisition, validates its exact plan, confirms explicit approval, inspects the hash-verified output, opens the guided product route, checks journal/QC and reopens the historical batch. The existing GUI suite exercises tensor execution, pause/cancel/retry and historical behavior. The MainWindow diffusion loader route was tested offscreen with a signal/loader spy; full native Viewer loading from this result remains unverified. This is a Batch page test, not a manual usability review of the entire application.

## Performance measurements

`scripts/benchmark_batch_guided.py` ran three synthetic offscreen repetitions per size after `2f4c97d`. Median milliseconds (range):

| Acquisition rows | Discovery presentation, including legacy and guided tables | Search | Deselect all | Step return |
|---:|---:|---:|---:|---:|
| 10 | 0.868 (0.795–1.067) | 0.025 (0.024–0.025) | 0.032 (0.030–0.033) | 0.009 (0.009–0.009) |
| 100 | 4.757 (4.712–10.864) | 0.147 (0.144–0.170) | 0.169 (0.165–0.188) | 0.010 (0.009–0.010) |
| 1,000 | 46.334 (45.978–55.668) | 1.687 (1.677–1.690) | 4.906 (4.892–4.937) | 0.013 (0.012–0.013) |

One final offscreen `scripts/benchmark_gui_performance.py` run measured Batch first/repeated navigation **94.363/20.594 ms**, 1,000-row legacy table initial render **34.171 ms**, unchanged journal callbacks **0.057/0.031 ms** after initial read, and **0** inactive Batch timer ticks with **0.264 ms CPU** over 1.8 s. The Phase 2 offscreen five-run median Batch first/repeated navigation was **30.0/21.2 ms**. Thus the new guided controls measurably increase first Batch construction cost; the single final sample remains below 100 ms and repeated navigation/idle behavior retained the Phase 2 range. These are not paired cold-start or native measurements, so no native speed claim is made. The generated study benchmark performs no MRI processing and does not measure real large-image import.

## Acceptance and remaining work

| Requirement | Status |
|---|---|
| Seven-step guided flow, BIDS/custom/manifest/saved study, multiple acquisition review, stable subset after sorting/filtering/back navigation, supported conversion/tensor presets, advanced workflow and resources | **Implemented and offscreen tested.** Other adapters require saved/advanced workflow; no unsupported preset is advertised. |
| Exact subset planner/CLI parity, genuine eligibility/blocker display, digest-bound approval, edit invalidation, historical plan identity | **Implemented and offscreen tested.** Prior plans keep their original payload; active runner retains its approved on-disk plan. |
| Actual pause/resume/cancel/retry, complete output inspection, registration QC, Viewer handoff, native layout and complete beginner walkthrough | **Partly native verified.** Six Cocoa Batch tests passed at `bc3723c`, including guided execution/output inspection/history and existing execution/recovery paths. The defect found at `a18cb1e` did not recur. MainWindow loader routing is offscreen tested, but a full native Viewer load, registration QC, native layout inspection and manual beginner usability review remain open. |
| Large-study responsiveness and Phase 2 idle/no-change behavior | **Offscreen measured/tested.** 1,000-row guided presentation median 46.334 ms; native large-study performance pending. |
| All Phase 3 GUI/core/CLI and scientific workflows | **Not claimed.** The 86-test focused run passed; three old GUI runner tests and one old CLI runner test hit the documented restricted-session process-enumeration failure. The existing Batch GUI tests subsequently passed in the normal Mac Terminal. Broader native and real ANTs verification are pending. |

**Phase 4 readiness:** The guided execution/output-inspection and existing Batch GUI tests passed in the normal Mac Terminal at `bc3723c`; the defect found by the expanded native test is fixed. The Batch implementation is stable enough to begin Phase 4 development, with a full native Viewer handoff, native layout/usability review and broader scientific verification still tracked. Do not mark the complete Phase 3 acceptance checklist as scientifically or manually verified on the strength of six tests alone. No code was pushed or merged.
