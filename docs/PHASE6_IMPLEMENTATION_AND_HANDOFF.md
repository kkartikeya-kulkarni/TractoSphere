# Phase 6 — remaining scientific workspaces

Branch: `development/phase6-scientific-workspaces-ux-2026-10-23`, from Phase 5 checkpoint `38f2f78`. This is a live implementation record. The scientific backlog and prior handoffs remain authoritative for unverified scientific claims.

## 6.0 audit and baseline

The Phase 1–5 worktrees were left untouched. The Phase 6 branch began clean. The 38-task [scientific validation backlog](SCIENTIFIC_VALIDATION_BACKLOG.md) remains open; a guided interface is not scientific acceptance.

| Workspace | Existing backend and interface | Main remaining usability gap |
| --- | --- | --- |
| Scientific input inventory | Typed `ImportRequest` and read-only `import_artifact`; shared by DTI/CSD, tractography, anatomy, SIFT2 and connectivity | Import begins with backend kind names and a long convention form. Inventory emphasizes content IDs over research identity. |
| Preprocessing | `WorkflowPage` already has history, operation, proposal and execution stages using `assess_operations`/`build_plan` | Operation prerequisites and scientific status are buried within detailed cards. Execution parameters crowd the default form. |
| DTI | Guided task creation, `prepare_dti`, exact approval, maps and QC | The form exposes fit iterations, thread count and other specialist choices before the basic task is clear. |
| CSD/FOD | `prepare_csd`, response estimation/import, exact approval, products and QC | Shell, response and method requirements need a clearer task path and missing-input state. |
| Anatomy/5TT | `prepare_anatomy`, four implemented operations, explicit anatomical review and reference association | The four operations and dense requirements appear in one form. Real ACT/5TT validation remains open. |
| SIFT2 | `prepare_sift2`, strict FOD/tractogram binding, approved weights and QC | The mask policy and advanced optimizer are more prominent than the scientific goal. |
| Connectivity | `prepare_connectome`, registered atlas, node table, matrix/QC and exact approval | Assignment and metrics appear before a clear statement of source and atlas eligibility. |
| QC/Results/Projects | Existing QC and run inspection workers, project acquisition tree and explicit Viewer handoffs | Users must navigate several pages to locate the result and understand execution versus review status. |

The scientific core remains Qt-free. Existing GUI pages own worker lifecycles. No preprocessing order, scientific numerical method or approval gate is changed by this audit.

**Fresh baseline, offscreen Qt, macOS ARM64, Python 3.12.15, empty settings/data:** `scripts/benchmark_gui_performance.py` reported script-to-interactive 958.444 ms, MainWindow 583.586 ms, first/return Reconstruction 375.395/23.713 ms, Connectivity 22.943/24.224 ms, Registration 58.106/50.644 ms, and 1,000-row Batch initial/unchanged journal 34.192/0.027 ms (latter after first read). This is one warm process, not cold-start or native Cocoa evidence. Seven selected offscreen GUI baseline tests passed in 14.33 s. A broader offscreen selection crashed in `QVTKRenderWindowInteractor` when it reached Viewer construction, matching the known Codex session restriction; no application regression is inferred. Native tests must be run from normal Mac Terminal after a committed code checkpoint.

## Implementation checkpoints

- [x] 6.1 Shared input guidance and direct DTI/FOD task entry implemented; existing approval unchanged. Focused offscreen tests passed.
- [x] 6.2 Preprocessing entry available before import; task explanations follow selection; specialist execution settings are expandable. Existing assessment and QC route retained. Focused offscreen tests passed.
- [x] 6.3 DTI/CSD default forms emphasize source, method and output. Specialist fitting/reconstruction controls remain available under Advanced. Focused offscreen tests passed.
- [x] 6.4 Anatomy/5TT, SIFT2 and Connectivity show task-dependent prerequisites and only applicable policy controls. Focused offscreen tests passed; biological validation remains open.
- [x] 6.5 Saved Runs and Projects expose explicit recent-record shortcuts and recheck evidence on opening. QC retains separate execution/technical/scientific states. Focused offscreen tests passed.
- [ ] 6.6 Cross-workspace state, accessibility, native light/dark layouts and performance review: offscreen state/layout/timing checks completed; researcher review and native layout checks pending.
- [ ] 6.7 GUI/core/CLI integration: focused local suites completed; native Cocoa/VTK and full beginner walkthroughs pending.

## Implemented interaction model

The import dialog now presents all and only the core-supported `KINDS` with researcher-facing names and purpose text. The original core kind, explicit identity/convention declarations, read-only hash/format inspection and source path remain unchanged. The longer declaration form is expandable. After import, the shared inventory shows type and declared subject, can be searched without changing IDs, and removes references without deleting source bytes. The previously loaded DWI still has an explicit **Use loaded DWI** action; no large-image hash runs merely because a tab opens.

The scientific workspace offers direct **Fit diffusion tensor** and **Estimate FOD** actions. Existing DTI/CSD planners still create typed tasks, validate shells/response associations and scientific software, display exact commands, require approval, execute through the owned worker and expose verified results/QC. Fit reweightings, masks, lmax and thread controls are under Advanced without changing their values. DTI metric names remain measurements rather than diagnoses. CSD method text distinguishes single- and three-tissue requirements without declaring an acquisition eligible before planning.

Preprocessing is now accessible with no DWI loaded. Its empty state routes to data import and cannot create a plan or run. With DWI loaded, its existing four-step history → operations → proposal → execution flow and `assess_operations`/`build_plan` are unchanged. One reusable explanation follows the selected operation; this avoids constructing nine extra labels at startup. MRtrix path override and numerical tuning are in **Advanced processing settings**. The saved Phase 1 location still populates the field, and exact planning validates the executables. EDDY/denoising order is unchanged; the EDDY investigation remains a separate scientific backlog workstream.

Anatomy names the four real 5TT tasks and lists missing structural/5TT/reference/mask inputs. SIFT2 asks for tractogram, WM FOD, reviewed reference and only the ACT five-tissue or explicit processing mask required by the selected policy; optimizer controls remain in Advanced. Connectivity asks for tractogram, registered atlas and reference; the assignment selector shows only its applicable distance field. These messages establish what is **selected**, not scientific eligibility. The existing `prepare_anatomy`, `prepare_sift2` and `prepare_connectome` services remain the authority for subject, geometry, source hash, tool version, exact command approval and output verification.

Saved Runs now shows operation, recorded status, verified-hash/output counts and a separate scientific-review warning. **Open last inspected run** re-runs `inspect_run` only after an explicit click. Projects likewise offers **Open last project** without loading private data at startup; its `read_project` and hash comparison still run in the existing worker. Missing snapshots produce actionable messages. The QC workspace's independent execution, technical, automated-QC and human-review labels remain unchanged. The sidebar now describes implemented workspaces accurately instead of calling Reconstruction or Connectivity unavailable.

## Source/test changes and commits

| Commit | Change |
| --- | --- |
| `aedfc82` | Scientific input guidance, searchable shared inventory, direct model tasks; input tests. |
| `40c6846` | Preprocessing and DTI/CSD default/advanced grouping; focused tests. |
| `df30661` | Anatomy/SIFT2/connectivity prerequisite guidance and conditional controls; tests. |
| `b824a2d` | Explicit verified recent-run navigation and result state; tests. |
| `36fe6bb` | Accessible empty Preprocessing import route and accurate workspace descriptions; tests. |
| `47ed29f` | Explicit recent-project reopening with settings recovery; tests. |
| `0216df2` | Reuse one preprocessing operation explanation widget to reduce eager construction; test. |
| `03928a2` | Added a Cocoa-only actual 5TT visualization/QC/Viewer walkthrough; it passed in the six-test normal-Terminal suite below. |

Modified source: `gui/scientific_planning.py`, new `gui/scientific_guidance.py`, `gui/workflow.py`, `gui/execution.py`, `gui/dti.py`, `gui/csd.py`, `gui/five_tissue.py`, `gui/sift2.py`, `gui/connectome.py`, `gui/results.py`, `gui/projects.py`, `gui/main_window.py`. Added/updated tests: `test_phase6_scientific_inputs.py`, `test_phase6_preprocessing_reconstruction.py`, `test_phase6_task_panels.py`, `test_phase6_anatomy_native.py`, `test_results_page.py`, `test_projects_page.py`, `test_workflow_page.py`. No scientific core or CLI code changed.

## Verification executed locally

Environment: macOS 26.3 ARM64, Python 3.12.15, project `../application/.venv/bin/python`, `PYTHONPATH=src QT_QPA_PLATFORM=offscreen OPENBLAS_NUM_THREADS=1`. The final selected GUI suite (Phase 6 input, preprocessing, task panels, existing workflow/results/QC/project, selected DTI/scientific planning/connectivity/SIFT2 cases) passed **24 tests with 1 native-only skip in 53.49 s** at application/test code `03928a2`. The targeted recent-project/results test selection passed **5/5 in 7.85 s** and the reusable-description preprocessing selection **3/3 in 4.95 s**. Phase 1 scientific-software defaults/settings selection passed **14/14 in 19.52 s**. The relevant core plus CLI run returned **149 passed, 2 failed in 34.50 s**. Running the same seven core files with those two cancellation cases deselected returned **115 passed, 2 deselected in 28.99 s**; `tests/cli` alone passed **34/34 in 0.56 s**. Ruff checks passed on touched source/tests.

The two core failures were `test_response_script_cancellation_retires_helper_process_group` (no resource-sample callback before completion) and `test_cancel_actual_sift2_process_and_keep_diagnostics` (process completed rather than cancelling). Both failed with the **same assertions** in an isolated, untouched `38f2f78` worktree under this Codex execution environment: **2 failed in 3.53 s**. A selected cross-phase GUI run returned **9 passed, 1 failed**: `test_batch_controls_and_configuration_invalidation` saw `READY` instead of `PAUSED`. That single test also failed identically at `38f2f78` (**1 failed in 1.12 s**). These are pre-existing in this restricted execution setting; scientific/process logic was not changed to force a pass. A broad offscreen GUI run crashed in VTK's `QVTKRenderWindowInteractor` when a Viewer test was reached, as recorded in the baseline. It is not classified as an application defect without native evidence. The new Cocoa-only anatomy test collected and skipped under offscreen Qt (**1 skip**).

At committed application/test HEAD `03928a2`, the user ran the following targeted suite in a normal Mac Terminal with `PYTHONPATH=src QT_QPA_PLATFORM=cocoa OPENBLAS_NUM_THREADS=1 FSLDIR="$HOME/fsl"`: `pytest -q tests/gui/test_phase6_anatomy_native.py tests/gui/test_scientific_planning.py::test_scientific_routes_independent_and_preserve_scene tests/gui/test_dti_gui.py::test_actual_guided_dti_approval_maps_vector_qc_history tests/gui/test_csd_gui.py::test_csd_gui_real_execution_import_preview_products tests/gui/test_sift2_gui.py::test_native_gui_sift2_approval_histogram_reopen_and_viewer tests/gui/test_connectome_gui.py::test_actual_gui_matrix_metrics_extraction_and_reopen --tb=short`. Result: **6 passed in 24.41 s, exit 0**. The Connectome test emitted 40 Matplotlib warnings about deprecated `AA_UseHighDpiPixmaps`; no test failed. This supplies native Cocoa/VTK evidence for synthetic input-to-Viewer, tensor/FOD execution, five-tissue visualization and QC, SIFT2, and matrix inspection. It is not real anatomical, ACT, DTI, CSD, SIFT2 or connectomics scientific acceptance, nor a full native GUI suite or researcher-led usability study. The previous Phase 1–5 native evidence remains historical, not a new Phase 6 pass.

## Performance and layout

`scripts/benchmark_gui_performance.py` ran three times in an isolated `38f2f78` baseline worktree and three times after code commit `0216df2`, on the same machine, offscreen Qt, empty data/settings, warm filesystem cache. Numbers are medians in milliseconds; ranges are below. The final code after `0216df2` adds only the Cocoa test; documentation does not change runtime behavior.

| Operation | `38f2f78` median | Phase 6 code median | Delta |
| --- | ---: | ---: | ---: |
| Script start to interactive | 864.846 | 869.319 | +4.473 |
| First Reconstruction | 360.863 | 375.649 | +14.786 |
| Return to Reconstruction | 23.833 | 25.927 | +2.094 |
| First Connectivity | 23.009 | 24.531 | +1.522 |
| Return to Connectivity | 24.345 | 25.843 | +1.498 |
| First Results | 19.592 | 15.603 | −3.989 |
| Return to Results | 18.054 | 17.784 | −0.270 |

Baseline startup range was 850.397–936.670 ms; final 840.658–877.557 ms. Reconstruction first-open range was 359.823–467.033 ms baseline and 367.674–432.482 ms final; return 23.646–23.917 versus 25.890–26.155 ms. All measured repeated navigation is below 200 ms. Scientific import dialog warm median was 6.792 ms (6.623–67.816) baseline versus 8.041 ms (7.828–65.606) final; first construction includes Qt/font setup. Final 1,000-row Batch table initial median was 34.186 ms, unchanged journal refresh median 0.028 ms and inactive timer ticks zero over 1,800 ms. One offscreen synthetic-loaded run measured loaded GUI work at 702.332 ms and Reconstruction 419.920/25.938 ms first/return; it preceded the final project shortcut and reusable operation label, so it is not a final loaded-data distribution. No native cold-start, actual MRI/tractography loaded Viewer or file-picker timing is claimed.

One later offscreen synthetic-loaded run on the final application code measured data loading **674.604 ms** and Reconstruction **428.338/26.250 ms** first/return. It is a single generated NIfTI measurement, not realistic loaded-data/Viewer performance. At 960×640 offscreen Qt, Preprocessing, DTI, CSD, Anatomy and SIFT2 scroll areas each had horizontal scrollbar maximum **0**. This numeric check does not substitute for native visual inspection, keyboard review or light/dark screenshots.

The user also ran two `scripts/benchmark_gui_performance.py` samples in normal-Terminal **native Cocoa** at application/test HEAD `03928a2`; both exited **0** on macOS 26.3 ARM64, Python 3.12.15. Empty-state script-to-interactive was **909.261 ms**, MainWindow **575.201 ms**, first/return Reconstruction **428.224/26.063 ms**, Connectivity **32.972/33.300 ms**, Results **16.429/18.509 ms** and Batch **104.670/21.365 ms**. The generated-small-NIfTI sample recorded load work **413.774 ms**, script-to-interactive **904.160 ms**, Reconstruction **373.840/26.681 ms**, Connectivity **31.456/34.593 ms**, Results **16.502/18.275 ms** and Batch **104.119/21.781 ms**. The empty/loaded 1,000-row Batch initial table measurements were **35.108/46.614 ms**, unchanged journal refresh **0.015/0.016 ms**, and inactive timer ticks **0** in both samples. `vtkmodules_loaded` was false during both benchmarks. These are **one warm native sample per state**, not medians, true cold starts or actual MRI/tractogram Viewer rendering. Relative to the Phase 5 handoff's single empty native script-to-interactive observation of 952.131 ms, the new empty observation is 42.870 ms lower, but sample count and changing machine state do not support a robust speedup claim.

## Remaining work and acceptance boundary

The targeted committed Cocoa walkthrough suite has passed. Phase 6 is **implemented and targeted-native-verified**, while a complete researcher-led beginner walkthrough, native light/dark layout and accessibility inspection, true cold start, and broader GUI suite remain open. The new 5TT walkthrough uses synthetic reviewed tissue and cannot establish real anatomy or ACT validity. Actual preprocessing, DTI, FOD, SIFT2 and connectome scientific acceptance on appropriate research data remains in the 38-task backlog. Broader Viewer handoffs, EDDY residual/CNR experiments, external derivatives, cross-platform CI and release review are still open. Do not mark the backlog tasks Verified or proceed to release solely because these UI tests pass.

### Phase 6 acceptance status

| Area | Implementation and actual verification |
| --- | --- |
| Input inventory and reuse | Implemented; focused offscreen GUI and targeted native shared-input/Viewer test passed. Broader data types remain untested natively. |
| Preprocessing guidance | Implemented; selected offscreen history/planning tests passed. A complete native correction and QC walkthrough was not run. |
| DTI and CSD/FOD | Guided essential/advanced controls implemented; targeted native execution/result tests passed on synthetic data. Numerical scientific validation remains in backlog. |
| Anatomy/5TT | Contextual guided tasks implemented; native 5TT visualization/QC/Viewer test passed on analytic tissue. Real anatomy and ACT validity remain unverified. |
| SIFT2 and Connectivity | Conditional inputs implemented; targeted native execution/inspection tests passed. Real-data scientific validity remains unverified. |
| Results, QC and Projects | Explicit recent-record actions, existing independent QC states and exact evidence workers tested offscreen; targeted native result/QC routes passed. Wider historical-product coverage remains open. |
| Cross-phase science and approval | Core unchanged; 115 selected core tests passed with two environment-sensitive cases deselected. All 38 scientific validation tasks remain open. |
| Performance and usability | Empty offscreen distributions, one empty and one small-loaded Cocoa benchmark, and 960×640 numeric clipping check complete. True cold start, real-data Viewer and researcher-led usability/light-dark inspection pending. |

## Scientific boundaries and outstanding validation

The [Phase 0–5 handoffs](PHASE5_IMPLEMENTATION_AND_HANDOFF.md) retain the historical evidence. No prior verification count is renewed by this phase. EDDY residual/CNR experiments, real ACT/5TT, SIFT2/connectomics, real ANTs, native Viewer product handoffs, cross-platform CI fixtures and release acceptance remain in the permanent backlog. MP-PCA-before-EDDY is not changed on the basis of expert feedback alone.
