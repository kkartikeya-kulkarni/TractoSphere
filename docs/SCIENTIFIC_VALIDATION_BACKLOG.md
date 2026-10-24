# Outstanding Scientific Verification and Release Blockers

Created 2026-10-23 from local Phase 4 checkpoint `3faf38d`; targeted Phase 5 evidence was appended after native testing at `4e5e4c9`. This is a **planning and evidence register**, not a new broad scientific validation. No preprocessing policy, scientific algorithm, acceptance threshold, or release state changed in this update. The phase handoffs remain the source for exact commands, dates, environments, and results: [Phase 0](PHASE0_AUDIT_AND_HANDOFF.md), [Phase 1](PHASE1_IMPLEMENTATION_AND_HANDOFF.md), [Phase 2](PHASE2_IMPLEMENTATION_AND_HANDOFF.md), [Phase 3](PHASE3_IMPLEMENTATION_AND_HANDOFF.md), [Phase 4](PHASE4_IMPLEMENTATION_AND_HANDOFF.md), and [Phase 5](PHASE5_IMPLEMENTATION_AND_HANDOFF.md). See also [limitations](limitations.md), [architecture](architecture.md), and the older [release validation record](validation.md).

## How to use this register

**P0** means a question about scientific correctness, source/coordinate integrity, exact execution identity, or the validity of a scientific/release claim. **P1** means required verification of an implemented route, native integration, or a demonstrated usability/performance regression. **P2** means bounded capability expansion or lower-priority interoperability. A P0 task blocks the **affected scientific claim or release scope** until resolved; it does not automatically block unrelated Viewer use or Phase 5 Registration UX development. A feature that is outside a scoped release may remain explicitly unsupported, but it must not be advertised as validated. The final release-content decision is `SV-REL01`.

Statuses are **Not started**, **In progress**, **Implemented, unverified**, **Verified**, **Blocked**, and **Accepted limitation**. “Implemented, unverified” means code or a narrow synthetic test exists, while this task's acceptance criteria remain unmet. There are no newly verified tasks in this register. Each entry specifies the remaining validation and dependencies; when work completes, attach the tested commit, commands, data class, platform, result, reviewer, and decision before changing its status. Do not import historical completion percentages as a current count: the [Phase 0 audit](PHASE0_AUDIT_AND_HANDOFF.md) explains why the older Stage 9 and Stage A tallies are not a fresh assessment of this branch.

The owner-reported 13 October 2026 correspondence from Professor Jesper Andersson is treated as **expert feedback to investigate**, not an independently reproduced result or proof of a defect. The cited [Andersson et al. 2016 outlier detection/replacement paper](https://pubmed.ncbi.nlm.nih.gov/27393418/) describes the prediction and replacement framework; the denoising/order/mask observations below come from the project owner's account of the correspondence, not from an inference that the paper proves a universal processing order.

**Phase 7 evidence reconciliation (24 October 2026):** The [Phase 7 audit and controlled protocol](PHASE7_EDDY_SCIENTIFIC_VALIDATION.md) located an older, restricted local EDDY investigation with exact historical commands, numerical anomaly records, several already executed controls, and a hash-indexed input bundle. This corrects the earlier “no controlled result” wording below: **no new Phase 7 MRI experiment was run**, and no old control established an acceptable preprocessing policy. Some original large output arrays are no longer at their recorded paths, so the old numerical findings are historical evidence pending restored-array recheck or an authorized exact replay. The register remains **38 outstanding tasks**; only the evidence-audit/design activity on `SV-EDDY01`/`02` has advanced to In progress. Never commit the restricted participant records or private paths.

## P0 — scientific correctness and data integrity

### SV-A01 — Reverify Stage A critical scientific contracts

- **Origin / priority / status / subsystem:** Stage A and Phase 0; P0; Implemented, unverified on this checkpoint; core resource reservations, registration review, planning, provenance.
- **Acceptance:** Rerun critical contract, source-preservation, implementation-hash, resource and review-applicability regressions against a recorded current commit in a permitted environment; investigate every residual failure before accepting the contract. Retain the existing unsupported-transform gates.
- **Environment:** Native Mac with process enumeration and scientific tools for external cases, plus suitable CI runners; synthetic fixtures and approved non-sensitive data.
- **Evidence:** Phase 0 reports the Stage A FNIRT reservation and registration-review repairs and historical 1,028 headless/190 GUI passes in another checkout; its later sandbox run had process-related failures. No current full-suite pass is established.
- **Remaining / dependencies and blockers:** Define the current critical-contract selection, rerun it, compare implementation hashes and resolve failures. Depends on usable process permissions and `SV-DEP03`; do not revive the older Stage 9 FNIRT finding as a current confirmed defect.

### SV-BAT01 — Verify pinned historical BatchRunner toolchains on resume and retry

- **Origin / priority / status / subsystem:** Phase 1; P0; Implemented, unverified; BatchRunner, scientific software resolution and provenance.
- **Acceptance:** Resume/retry representative historical MRtrix3, FSL and ANTs plans with the original executable paths, versions and SHA-256 identities; changed global defaults must not substitute binaries. Missing or incompatible pinned binaries must stop with a specific diagnostic. Approval, journal and source/output integrity remain unchanged.
- **Environment:** Permitted native Mac with real compatible tools and controlled old/new installations; synthetic batch fixtures; cross-platform cases where supported.
- **Evidence:** Phase 1 synthetic approved-plan path/hash checks passed; three selected native BatchRunner tests passed, but did not cover every MRtrix/FSL/ANTs history or real ANTs.
- **Remaining / dependencies and blockers:** Build actual pinned-toolchain matrix, including unavailable binaries. Real ANTs verification `SV-DEP01` is a dependency; historical orchestration implementation-hash mismatch must not be bypassed.

### SV-EDDY01 — Reproduce the b1000 prediction and CNR anomaly

- **Origin / priority / status / subsystem:** Stage B EDDY scientific validation; P0; In progress (evidence audit only; Phase 7 reproduction unrun); preprocessing output diagnostics and QC.
- **Acceptance:** Reproduce or refute the reported infinite weighted CNR and zero inclusive prediction residuals for the previously investigated b1000 acquisition using frozen inputs and exact commands. Record finite/zero/nonfinite counts, denominators, affected voxels/slices/volumes, b-values, motion/outlier locations, masks and software versions; preserve the original data.
- **Environment:** Authorized access to the original acquisition or a permitted equivalent, compatible FSL EDDY and MRtrix, controlled storage and sufficient compute. No participant data enters this repository.
- **Evidence:** The Phase 7 audit found a restricted prior comparison and read-only denominator-mechanism record: earlier b≈1000 EDDY output had near-global positive-infinite weighted CNR where inclusive weighted residuals were exactly zero. The prior run recorded FSL EDDY 2401.2 and an exact executable hash/argv. This is **historical research evidence**, not an independently rerun Phase 7 finding. [Limitations](limitations.md) already rejects EDDY exit success as scientific acceptance.
- **Remaining / dependencies and blockers:** Original run folders lack some large raw/intermediate/CNR/residual NIfTI arrays; a restricted local bundle retains hash-indexed input copies and records. Establish access/authorization, restore and rehash original output arrays or approve one exclusive unchanged replay, then independently recompute voxel and shell correspondence. See the Phase 7 protocol; do not infer the upstream cause from the confirmed denominator symptom.

### SV-EDDY02 — Compare no-denoising and MP-PCA-before-EDDY controls

- **Origin / priority / status / subsystem:** Stage B and Andersson feedback; P0; In progress (historical controls inventoried; decisive matched comparison unrun); preprocessing order, EDDY Gaussian-process prediction and QC.
- **Acceptance:** Run matched no-denoising and current MP-PCA-before-EDDY branches on the same eligible acquisitions. Hold EDDY version/settings, acquisition metadata, gradient orientation, mask definition and analysis regions constant where scientifically possible. Compare residual and CNR distributions/maps, prediction failures, outlier patterns and downstream QC with reproducible commands; determine whether denoising is associated with zero residuals without claiming causality from one case.
- **Environment:** Authorized original and comparison acquisitions, real compatible MRtrix/FSL, sufficient compute/storage, scientific reviewer.
- **Evidence:** Historical restricted controls omitted MP-PCA while retaining Gibbs, and separately omitted both MP-PCA and Gibbs; neither passed strict CNR/QC. Another control combined no MP-PCA with a changed GP objective and is **not** a one-factor baseline contrast. Denoising may matter, but neither causality nor an approved alternative was established. [Cieslak et al. 2024](https://pmc.ncbi.nlm.nih.gov/articles/PMC10826632/) found improved EDDY motion estimation with prior MP-PCA in its simulated settings, so a universal conclusion is unsupported.
- **Remaining / dependencies and blockers:** Complete `SV-EDDY01`; use authenticated existing runs before considering repeats. Explicitly separate natural-pipeline differences in TOPUP/BET from a fixed-field/fixed-mask analysis, and compare prediction fidelity, motion, replacement and downstream outputs. Version, acquisition, partial Fourier and mask differences must not be silently pooled.

### SV-EDDY03 — Assess EDDY-before-denoising feasibility and assumptions

- **Origin / priority / status / subsystem:** Stage B and Andersson feedback; P0; Not started; preprocessing order and MP-PCA noise assumptions.
- **Acceptance:** First document whether post-EDDY interpolation/resampling changes the noise structure enough to invalidate the intended MP-PCA method or require a different validated method. If a scientifically defensible branch exists, compare it with `SV-EDDY02` under matched conditions and record interpolation, gradients, masks and QC. Do not replace the default order on plausibility alone.
- **Environment:** Method review plus controlled real-data processing with compatible EDDY and denoising tools; expert review.
- **Evidence:** Owner reports a suggestion to try EDDY before denoising and acknowledges conflict with denoising-first guidance. No experiment or validated post-EDDY denoising contract exists here.
- **Remaining / dependencies and blockers:** Establish method validity before execution, then test if justified. Depends on `SV-EDDY01`/`02`; interpolation may be a scientific blocker or accepted limitation.

### SV-EDDY04 — Isolate brain-mask sensitivity from denoising effects

- **Origin / priority / status / subsystem:** Stage B and Andersson feedback; P0; Not started; EDDY mask and Gaussian-process estimation.
- **Acceptance:** Compare a reviewed baseline brain mask with controlled, documented mask changes, including a deliberately poor mask as a sensitivity bound where justified. Use a factorial or otherwise matched design that separates mask from denoising effects. Report hyperparameter-related diagnostics, residuals, CNR, voxel coverage and failure locations; do not assign modest mask differences causal responsibility without evidence.
- **Environment:** Same authorized acquisitions/tool versions as `SV-EDDY02`, reproducible mask derivatives and expert anatomical review.
- **Evidence:** Owner reports Andersson's point that EDDY mainly uses the mask to select voxels for Gaussian-process hyperparameter estimation and that substantial mask errors may be needed for large effects. This has not been tested in TractoSphere.
- **Remaining / dependencies and blockers:** Define controlled perturbations and preregister analysis after `SV-EDDY01`; avoid comparing branches with uncontrolled masks or acquisition changes.

### SV-EDDY05 — Validate outlier replacement against matched low-motion data

- **Origin / priority / status / subsystem:** Stage B and Andersson feedback; P0; Not started; EDDY outlier replacement and QC.
- **Acceptance:** For appropriately matched movement-corrupted and low-motion measurements, identify dropout slices/voxels, establish valid correspondence in diffusion direction, b-value and anatomy, and compare replaced predictions to the low-motion reference with uncertainty and registration limits documented. Assess reliability against the reference, not merely the magnitude of the changed intensity. Report false/missed replacements and impact on downstream measurements where feasible.
- **Environment:** Authorized matched motion/low-motion acquisitions or a scientifically defensible controlled dataset, real EDDY, spatial review and adequate compute.
- **Evidence:** Owner-reported Andersson feedback says large replacement changes are not inherently incorrect; [Andersson et al. 2016](https://pubmed.ncbi.nlm.nih.gov/27393418/) evaluates movement-related dropout detection/replacement. No matched-pair validation is recorded for this branch.
- **Remaining / dependencies and blockers:** Locate suitable matched data, establish correspondence and reference quality, then execute blinded comparison. A visually similar image alone is insufficient.

### SV-EDDY06 — Decide QC warnings and preprocessing policy from evidence

- **Origin / priority / status / subsystem:** Stage B and Andersson feedback; P0; Not started; preprocessing policy, EDDY QC and scientific acceptance.
- **Acceptance:** Define and test explicit warnings or investigation-only states for zero inclusive residuals, nonfinite CNR and questionable prediction behavior; maintain existing acceptance gates. An expert review of `SV-EDDY01`–`05` must document whether to retain or revise the current order, limitations and validation tests before any default changes.
- **Environment:** Synthetic edge-case fixtures plus reviewed real diagnostic results, GUI/CLI/batch QC tests and scientific review.
- **Evidence:** Existing limitations already reject EDDY exit status as scientific acceptance; no reviewed comparative data or policy decision is recorded.
- **Remaining / dependencies and blockers:** Requires `SV-EDDY01`–`05` and a scoped QC specification. Do not disable MP-PCA, change EDDY parameters, suppress CNR warnings or relax checks as a tracking shortcut.

### SV-BAT02 — Prove native Batch output-to-Viewer scientific handoff

- **Origin / priority / status / subsystem:** Phase 3; P0; Implemented, unverified; Batch results, product loader and Viewer.
- **Acceptance:** From a verified Batch output, complete native Cocoa/VTK Viewer loading and assert exact product hash, input/source and acquisition identity, NIfTI geometry/affine or tract coordinates, registered-space evidence, provenance and failure behavior for tampered/stale outputs. Opening a product must not imply scientific QC approval.
- **Environment:** Normal Mac Terminal with Cocoa/VTK and real compatible MRtrix/FSL as needed; synthetic multi-file DWI, image and registration outputs.
- **Evidence:** Phase 3 had six native Batch GUI passes at `bc3723c`, including verified output inspection and Open signal; MainWindow loader routing was offscreen tested. Phase 5 normal-Terminal Cocoa at `4e5e4c9` followed an actual two-subject BatchRunner registration QC output from the guided results page into the Viewer, checked selected acquisition, product hash, reference/atlas paths, pending review, and rejection of a changed derived QC report. The broader Phase 5 native selection was 13 passed at `5c30ded`.
- **Remaining / dependencies and blockers:** Extend native integrity/identity and geometry checks to other supported Batch image and tractogram products, including tampered inputs and stale spatial associations. The targeted registration QC route does not satisfy the entire cross-product acceptance matrix. Depends on supported product routes and valid spatial declarations.

### SV-C01 — Validate currently supported atlas/transform spatial contracts

- **Origin / priority / status / subsystem:** Stage C and Phase 0; P0; Implemented, unverified for broad scientific scope; registration, atlas, transforms, ROI/connectome downstream consumers.
- **Acceptance:** With ground-truth or expert-reviewed data, verify transform direction, reference/moving identity, affine/warp composition, interpolation for labels versus intensities, round trips, subject/space metadata, stale review invalidation and downstream atlas/ROI/connectome use. Reject unsupported transforms rather than guessing a conversion.
- **Environment:** Native scientific tools, synthetic phantoms with known mappings and permitted real anatomical/diffusion data; native Viewer QC and CLI/core tests.
- **Evidence:** Phase 0 records implemented FSL/MRtrix registration and native atlas QC, Stage A review repair, and deliberate blocks on unsupported ANTs/HDF5/nonlinear paths. Phase 5 ran 13 core registration tests and 13 targeted native Cocoa/BatchRunner cases at `5c30ded`; the latter used synthetic images and approved fixture declarations. This is not broad anatomical validation.
- **Remaining / dependencies and blockers:** Build a supported-format matrix and independent reference comparisons. Stage C expansion is separately `SV-C02`; Phase 5 UX must preserve these gates.

### SV-MOD01 — Numerically validate DTI and CSD/FOD outputs

- **Origin / priority / status / subsystem:** Scientific roadmap and Phase 0; P0; Implemented, unverified for broad numerical acceptance; reconstruction and QC.
- **Acceptance:** Compare tensor metrics, response estimates, FOD coefficients/normalization, gradients and geometry to independent known-answer or reference outputs across eligible acquisition classes; define tolerances and inspect failures/uncertainty. Confirm GUI, CLI and Batch commands select the same validated inputs.
- **Environment:** Real compatible MRtrix, synthetic phantoms and permitted reference datasets; scientific review.
- **Evidence:** Phase 0 lists DTI/CSD/FOD implementation and bounded tests, not a complete numerical validation campaign.
- **Remaining / dependencies and blockers:** Define representative acquisition matrix, reference method and reproducible tolerances; exclude unsupported histories rather than fabricating metadata.

### SV-ACT01 — Validate real ACT and five-tissue scientific behavior

- **Origin / priority / status / subsystem:** Phase 4 and scientific roadmap; P0; Implemented, unverified for real-data scientific acceptance; 5TT, tracking, backtracking and termination.
- **Acceptance:** On suitable independently reviewed anatomy/FOD data, verify 5TT tissue identity and alignment, ACT prerequisites, GMWMI seeding where supported, backtracking, termination and endpoint cropping against documented MRtrix behavior; inspect anatomical plausibility, exact commands, QC and unchanged source hashes. Incompatible data must block.
- **Environment:** Native Mac with compatible MRtrix, approved real anatomy/diffusion data, Viewer and expert anatomical review.
- **Evidence:** Phase 4 targeted synthetic native Generate/Filter/Density tests passed at `f9fc8ba`; they did not perform real ACT/5TT validation. Phase 0 lists implemented ACT routes.
- **Remaining / dependencies and blockers:** Acquire suitable data and ground-truth/review protocol. Depends on `SV-C01` for spatial association and `SV-MOD01` for FOD validity.

### SV-CON01 — Validate SIFT2 and connectomics scientifically

- **Origin / priority / status / subsystem:** Scientific roadmap and Phase 0; P0; Implemented, unverified for broad scientific acceptance; SIFT2, atlas assignment and connectivity matrices.
- **Acceptance:** Validate tract/FOD/atlas associations, SIFT2 weight identity and stability, matrix assignment/count/length/FA calculations, expected symmetry/edge cases, QC/review status and provenance against independent references. State clearly that weights and streamline counts are not axon counts.
- **Environment:** Compatible MRtrix, synthetic known-answer graph/tract fixtures and permitted real datasets with expert review.
- **Evidence:** Phase 0 lists implementation and bounded tests; README/limitations describe scientific limits. No current full biological validation is claimed.
- **Remaining / dependencies and blockers:** Requires validated FOD, ACT/tractogram and atlas routes (`SV-MOD01`, `SV-ACT01`, `SV-C01`).

### SV-ROI01 — Verify real anatomical ROI/tractogram correspondence

- **Origin / priority / status / subsystem:** Phase 4 and Stage C; P0; Implemented, unverified for real-data spatial acceptance; Viewer ROI import, filtering and registration evidence.
- **Acceptance:** On suitable real matched data, independently confirm ROI subject/session/source identity, reference grid, transform evidence, orientation, anatomical location and tractogram coordinates before and after filtering. Verify changed original ROI/tract files are refused and output tracks match the declared rule without modifying originals.
- **Environment:** Native Cocoa/VTK, real compatible MRtrix, permitted data with expert spatial review.
- **Evidence:** Phase 4 synthetic native full-path and endpoint filtering passed at `f9fc8ba` with original TCK hash unchanged; this does not establish real anatomical correspondence.
- **Remaining / dependencies and blockers:** Requires approved matched images/ROIs/tracts and `SV-C01`; visual overlap alone is not proof of registration.

### SV-REL01 — Complete scoped release-content and provenance review

- **Origin / priority / status / subsystem:** Phase 0 release gate and scientific roadmap; P0; Not started for the current development snapshot; packaging, claims, provenance, privacy and licensing.
- **Acceptance:** Freeze a candidate SHA; compare every advertised workflow to verified evidence and explicit limitations; check source/derivative immutability, executable hashes, data/credential exclusion, external licenses, build contents, documentation, platform scope, test logs and reviewer sign-off. Make a separate documented decision on what can ship and what stays unsupported.
- **Environment:** Clean build/install environments, security/package checks, permitted native and CI platforms, scientific and release review.
- **Evidence:** Older public-alpha validation is in [validation](validation.md); Phase 0–4 are development handoffs, not a release approval. No current candidate was reviewed in this task.
- **Remaining / dependencies and blockers:** Depends on the release's claimed features and their P0/P1 evidence. Do not infer a public release from a development-branch test or PR.

## P1 — required verification and demonstrated regressions

### SV-DEP01 — Verify a real compatible ANTs installation

- **Origin / priority / status / subsystem:** Phase 1; P1; Implemented, unverified; dependency settings and supported ANTs scientific adapter.
- **Acceptance:** Detect actual supported version/components, Verify and Save, restart and restore, then plan/execute an eligible ANTs operation with exact binary/version/hash provenance; reject moved/incompatible paths and verify clearing/changing defaults.
- **Environment:** Normal native Mac with a compatible licensed/available ANTs installation and synthetic supported input; no automatic install or security bypass.
- **Evidence:** Phase 1 synthetic ANTs persistence/restart tests passed. Its normal Mac lacked a real ANTs installation; older release inventory evidence is a different snapshot and not this execution proof.
- **Remaining / dependencies and blockers:** Obtain compatible installation and permitted native test environment; keep scientific operation-specific checks intact.

### SV-DEP02 — Broaden native dependency-settings GUI coverage

- **Origin / priority / status / subsystem:** Phase 1; P1; Implemented, unverified; Settings, async dependency worker and new-workflow defaults.
- **Acceptance:** Native Cocoa tests cover first setup, restart, missing/moved/incompatible tool, interrupted scan, save failure, active plan isolation, change/remove and cross-workspace reuse for MRtrix3/FSL/ANTs without repeated prompts or GUI freezes.
- **Environment:** Normal Mac Cocoa session, temporary settings, synthetic executables plus real tools for compatibility cases.
- **Evidence:** One targeted native dependency-path test passed in Phase 1.2; broader GUI/VTK tests remain open.
- **Remaining / dependencies and blockers:** Build and run the matrix; real ANTs operation depends on `SV-DEP01`.

### SV-DEP03 — Classify and resolve inherited GitHub CI fixture failures

- **Origin / priority / status / subsystem:** Phase 1 and Phase 0; P1; In progress; test fixtures, platform configuration and scientific dependency discovery.
- **Acceptance:** For each PR #4 failing node ID, compare PR #3 baseline, identify missing external tools versus fixture assumptions versus sandbox/platform restriction versus application defect, fix the responsible fixture/product code without weakening validation, and obtain a recorded appropriate CI outcome or accepted documented limitation.
- **Environment:** GitHub Actions macOS ARM/Intel, Ubuntu and Windows runners plus local reproduction where possible.
- **Evidence:** Phase 1 found the same 31 failing IDs on macOS/Ubuntu and 36 on Windows in PR #3 and #4; final PR #4 release audit passed but core failures blocked downstream GUI jobs. No new failing node IDs were attributed to Phase 1.
- **Remaining / dependencies and blockers:** Inspect exact logs/fixtures for every group; unavailable scientific tools must lead to honest skips or provided fixtures, never relaxed production gates.

### SV-PERF01 — Measure genuine native cold start

- **Origin / priority / status / subsystem:** Phase 2; P1; Not started; process launch, Qt and Viewer initialization.
- **Acceptance:** Repeat comparable cold OS/process launches on specified Mac hardware with wall time to visible and interactive Start, load settings, resource use and variability; separate interpreter/import, warm cache and native rendering costs. Retain a reproducible baseline for future changes.
- **Environment:** Normal Mac Cocoa desktop with reproducible cache/startup conditions and instrumented local build.
- **Evidence:** Phase 2 warm-cache native script-start first event was 1,155.719 ms at `bd1b933`; no true cold-start measurement or matched pre-change native baseline exists.
- **Remaining / dependencies and blockers:** Define cold-cache protocol and collect repeated trials without private auto-loaded data.

### SV-PERF02 — Profile Viewer with loaded MRI and tractography

- **Origin / priority / status / subsystem:** Phase 2; P1; Not started; VTK image/tract rendering and GUI navigation.
- **Acceptance:** Measure first and repeat Viewer opening, image/tract load, navigation, memory and idle CPU with bounded synthetic plus permitted representative real data; identify UI-thread stalls and verify geometry/display unchanged after any later optimization.
- **Environment:** Normal Mac Cocoa/VTK with available non-sensitive or authorized data; repeatable hardware/OS record.
- **Evidence:** Phase 2 selected native VTK smoke tests passed; offscreen small NIfTI load and Phase 4 synthetic native Viewer routes do not constitute representative loaded-data profiling.
- **Remaining / dependencies and blockers:** Acquire appropriate datasets, define size classes and compare identical conditions.

### SV-PERF03 — Measure native file pickers and common dialogs

- **Origin / priority / status / subsystem:** Phase 2; P1; Not started; native dialogs and UI thread.
- **Acceptance:** Repeated first/open timings for file/folder pickers, scientific software, ROI, registration and workflow dialogs with and without loaded data; eliminate proven synchronous bottlenecks without changing validation order.
- **Environment:** Normal Mac Cocoa desktop with scripted interaction and representative settings.
- **Evidence:** Phase 2 offscreen software dialog first open 25.4 ms and other basic dialogs were fast; native pickers/VTK dialogs were not measured.
- **Remaining / dependencies and blockers:** Build native measurement harness and inspect only demonstrated slow paths.

### SV-PERF04 — Resolve the ACT real-child cancellation discrepancy

- **Origin / priority / status / subsystem:** Phase 0 and Phase 2; P1; In progress; ACT worker/process retirement and output publication.
- **Acceptance:** Reproduce `test_real_act_child_cancellation_does_not_publish` in a process-permitted environment with timing/resource evidence, classify fixture race versus actual cancellation defect, and ensure cancelled work never publishes a validated output. Add deterministic regression coverage for any defect.
- **Environment:** Normal Mac Terminal or equivalent allowed process enumeration with real small ACT child; synthetic data and valid compatible tools.
- **Evidence:** Phase 0/2 restricted runs returned `validated_pending_qc` instead of `cancelled` when resource sampling could not observe the child. Phase 4 native **tracking** real-child cancellation passed at `7053b2a`, but that is a different test and does not classify ACT.
- **Remaining / dependencies and blockers:** Run the exact ACT test natively with captured HEAD and logs; do not mark it an app defect or a sandbox-only issue without this comparison.

### SV-PERF05 — Preserve startup/navigation gains across phases

- **Origin / priority / status / subsystem:** Phase 2 onward; P1; In progress; lazy workspaces, Batch polling, worker lifecycle.
- **Acceptance:** Record repeated startup/navigation/idle benchmarks at each major UI checkpoint and guard page reuse, signal counts, zero inactive Batch polling, unchanged journal fast path and source state. Investigate meaningful regressions with identical conditions.
- **Environment:** Offscreen synthetic benchmark plus periodic normal Mac Cocoa check.
- **Evidence:** Phase 2 reduced comparable offscreen script-to-interactive median from 1,213.8 to 829.5 ms and unchanged 1,000-row journal refresh to ~0.04 ms; Phase 4 measured a Tractography navigation regression tracked by `SV-TRK03`. Phase 5 comparable offscreen Registration first/return medians increased from 32.145/33.367 to 58.175/50.819 ms with the guided page/header, while remaining below the 200 ms interaction target. Normal-Terminal Cocoa samples at `5c30ded` were 51.971/34.553 ms empty and 51.371/34.762 ms with a small synthetic NIfTI loaded; no native pre-change Phase 5 baseline exists. Inactive Batch polling remained zero in the benchmark.
- **Remaining / dependencies and blockers:** Keep comparisons paired; profile the added Registration layout/navigation cost if it grows or becomes noticeable with real data. Do not claim native cold-start speedup from warm-cache script samples.

### SV-BAT03 — Conduct manual seven-step Batch usability review

- **Origin / priority / status / subsystem:** Phase 3; P1; Implemented, unverified for manual usability; guided Batch page.
- **Acceptance:** Researchers unfamiliar with the internal JSON/adapters complete data selection, subset, supported workflow, output, exact review/approval, run, results/QC and history; record confusion, errors, recovery and accessibility findings without bypassing scientific gates.
- **Environment:** Normal Mac GUI with synthetic BIDS dataset and consenting research users/testers.
- **Evidence:** Phase 3 six targeted native Batch GUI tests passed at `bc3723c`, including synthetic conversion and history; no manual researcher study was done.
- **Remaining / dependencies and blockers:** Recruit reviewers, run a documented walkthrough and fix observed problems in an authorized later UX checkpoint.

### SV-BAT04 — Inspect Batch layouts in native light/dark and realistic sizes

- **Origin / priority / status / subsystem:** Phase 3; P1; Implemented, unverified natively; guided Batch layouts.
- **Acceptance:** On native Cocoa at 960×640 and representative larger/narrower windows in both themes, verify scroll access, long names, tables, focus order, controls, dialogs and no clipping; record screenshots and fixes.
- **Environment:** Normal Mac desktop with Cocoa/VTK and both themes.
- **Evidence:** Phase 3 offscreen 960×640 dark screenshots and automated light/dark size tests passed; native layout review remains open.
- **Remaining / dependencies and blockers:** Execute native visual/keyboard audit after a committed GUI checkpoint.

### SV-BAT05 — Broaden Batch registration QC coverage

- **Origin / priority / status / subsystem:** Phase 3 and Stage C; P1; Implemented, unverified broadly; Batch registration QC and scientific review.
- **Acceptance:** Test eligible, rejected, stale and missing registration evidence in guided Batch results/history; confirm no automatic approval, exact transform/source identity and downstream gating across GUI/core/CLI.
- **Environment:** Synthetic geometry/transform fixtures, compatible FSL/MRtrix, normal Mac Cocoa for Viewer review.
- **Evidence:** Existing registration QC routes were preserved; Phase 3 targeted synthetic Batch walkthrough did not cover a broad registration QC matrix. Phase 5 normal-Terminal Cocoa at `4e5e4c9` reopened an actual two-subject BatchRunner, inspected one selected QC bundle in the Viewer, verified exact subject/session/acquisition identity and pending human review, and refused a changed derived report. The selected native Registration/BatchRunner suite was 13 passed at `5c30ded`.
- **Remaining / dependencies and blockers:** Complete the guided Batch matrix for missing/rejected/stale evidence, downstream eligibility and historical transform/source identity across acquisitions. Depends on `SV-C01`; the targeted successful handoff does not close broad QC coverage.

### SV-BAT06 — Test additional supported Batch scientific workflows

- **Origin / priority / status / subsystem:** Phase 3; P1; Implemented, unverified beyond targeted cases; Batch presets, advanced workflow, execution/QC.
- **Acceptance:** Exercise supported preprocessing, tensor and applicable saved/advanced adapters with multiple acquisitions, eligible/blocked/deferred rows, partial failure, recovery, exact commands, QC and unchanged originals. Do not advertise adapters lacking successful validation.
- **Environment:** Real compatible tools, synthetic and permitted representative data, normal Mac GUI plus core/CLI tests.
- **Evidence:** Phase 3 targeted native BIDS conversion and existing Batch GUI tests passed; selected offscreen regression had 86 passes. These are not all supported workflows.
- **Remaining / dependencies and blockers:** Create coverage matrix; scientific model outputs also depend on `SV-MOD01`, registration on `SV-C01`.

### SV-TRK01 — Broaden native Generate/Filter/Density GUI coverage

- **Origin / priority / status / subsystem:** Phase 4; P1; Implemented, unverified broadly; guided tracking, Viewer and worker lifecycle.
- **Acceptance:** Native Cocoa/VTK matrix covers algorithm and seed eligibility, ROI edits/refresh, Include ALL/ANY, Exclude ANY, ordered/direction, endpoint-only, density options, interrupted jobs, source changes, QC, output opening and safe shutdown without duplicate signals.
- **Environment:** Normal Mac with compatible MRtrix, synthetic inputs and approved data for applicable cases.
- **Evidence:** At `f9fc8ba`, three targeted Cocoa tests passed in 9.32 s: guided Generate/Density Viewer handoff and original-atlas full-path/endpoint filtering. They do not verify all methods/large inputs.
- **Remaining / dependencies and blockers:** Extend native suite without reinterpreting geometry or scientific semantics.

### SV-TRK02 — Researcher review of tractography ROI controls

- **Origin / priority / status / subsystem:** Phase 4; P1; Implemented, unverified for human usability; ROI assignment, ordered waypoints and Viewer handoff.
- **Acceptance:** Researchers can distinguish seeding, propagation mask, inclusion, exclusion, waypoint order and endpoint conditions; complete Generate/Filter/Density tasks without command syntax; record interpretation errors, accessibility and recovery issues.
- **Environment:** Normal Mac GUI, synthetic or permitted research data, diverse domain users and both themes/sizes.
- **Evidence:** Phase 4 synthetic GUI tests and guided native walkthroughs passed; no researcher-led usability review was reported.
- **Remaining / dependencies and blockers:** Schedule observed sessions and independently evaluate comprehension.

### SV-TRK03 — Investigate repeated Tractography navigation regression

- **Origin / priority / status / subsystem:** Phase 4; P1; In progress; guided scientific page and navigation.
- **Acceptance:** Profile identical route sequences and loaded/empty states, identify time spent in new controls/ROI inventory/layout, and reduce unnecessary work where possible without losing state or scientific checks. Rebenchmark on offscreen and native Cocoa; retain the result even if a cost is justified.
- **Environment:** Phase 4 performance scripts, identical Mac/runtime conditions, native Cocoa follow-up.
- **Evidence:** Comparable offscreen return-navigation median increased from **14.374 ms** before Phase 4 to **33.009 ms** after; direct native return was 23.087 ms in one run, which is a different route and not a paired baseline.
- **Remaining / dependencies and blockers:** Instrument causes and test any focused fix. No native before/after percentage is established.

### SV-TRK04 — Verify density Viewer scene preservation

- **Origin / priority / status / subsystem:** Phase 4; P1; Implemented, unverified for mixed-scene retention; scalar output Viewer handoff.
- **Acceptance:** Verify whether opening the NIfTI density image preserves or intentionally replaces existing tract/image scene state, labels this behavior accurately, checks geometry and allows safe return without mutating source or execution manifest.
- **Environment:** Normal Mac Cocoa/VTK with synthetic mixed scene and verified density result.
- **Evidence:** The repaired NIfTI density handoff passed natively at `f9fc8ba`; prior mixed-scene retention was not asserted.
- **Remaining / dependencies and blockers:** Add targeted native scene-state test; use existing Viewer loading contract.

### SV-QC01 — Complete Stage B `eddy_quad`/`eddy_squad` integration

- **Origin / priority / status / subsystem:** Stage B and Phase 0; P1; Not started for completion; preprocessing QC/report ingestion.
- **Acceptance:** Version-check supported QUAD/SQUAD tools, parse and preserve their products with exact run/acquisition provenance, verify GUI/CLI/Batch summaries and missing-tool behavior, and prevent technical report generation from implying scientific acceptance.
- **Environment:** Compatible real FSL installation, synthetic/authorized eligible EDDY outputs and reproducible tests.
- **Evidence:** Phase 0 lists QUAD/SQUAD as incomplete; no later handoff claims completion.
- **Remaining / dependencies and blockers:** Define supported FSL output contract; coordinate with `SV-EDDY06` so invalid CNR/predictions stay visible.

### SV-SCALE01 — Test large tractograms and large studies

- **Origin / priority / status / subsystem:** Scientific roadmap and Phases 2–4; P1; In progress; Viewer, tract editing, Batch tables and journals.
- **Acceptance:** Benchmark memory, first/repeated interaction, filtering, selection, journal refresh and failure recovery at representative scale; verify original hashes and no unbounded growth or UI freeze. State the tested size and whether tracts are distinct.
- **Environment:** Native workstation with sufficient RAM/GPU plus synthetic 10/100/1,000-acquisition studies and permitted large tractograms.
- **Evidence:** Phase 2 measured 1,000-row unchanged refresh; Phase 3 measured 1,000-row guided presentation; Phase 4 measured 1,000-ROI inventory. Limitations warn that ten-million-record indexing used repeated records, not distinct biological streamlines.
- **Remaining / dependencies and blockers:** Run representative native loaded-data trials; do not infer large-tract rendering from table or repeated-record benchmarks.

### SV-PLAT01 — Test claimed workflows across platforms

- **Origin / priority / status / subsystem:** Scientific roadmap, Phase 0 and Phase 1 CI; P1; Implemented, unverified for expanded scientific scope; packaging, native GUI and scientific adapters.
- **Acceptance:** For each claimed platform, run installation, dependency detection, GUI/VTK, core/CLI and supported scientific workflows with exact versions; clearly mark native Windows FSL/WSL unsupported until a validated bridge exists.
- **Environment:** Physical/representative macOS ARM/Intel, Linux x86-64/ARM64 and Windows where supported; CI plus permitted local machines.
- **Evidence:** Older alpha cross-platform records and hosted checks exist, but expanded development-branch scientific workflows were not fully validated; Phase 1 CI still had inherited fixture failures.
- **Remaining / dependencies and blockers:** Resolve `SV-DEP03`, obtain platform hosts and separate software smoke from scientific acceptance.

### SV-PREP01 — Complete broader Stage B preprocessing/N4 QC coverage

- **Origin / priority / status / subsystem:** Stage B and Phase 0; P1; Implemented, unverified for broad scope; preprocessing entry, N4, EDDY eligibility and QC.
- **Acceptance:** Verify eligible acquisition metadata, correction history/order, N4 field/mask review, missing/contradictory metadata, command/version provenance and GUI/CLI/Batch parity without filling missing acquisition facts. Keep EDDY findings from `SV-EDDY01`–`06` visible in QC.
- **Environment:** Real compatible MRtrix/FSL/ANTs where applicable, synthetic known-answer and authorized representative acquisitions.
- **Evidence:** Phase 0 describes implemented metadata-gated TOPUP/EDDY and bias correction but incomplete broader EDDY/N4 entry/settings and QC; limitations retain these restrictions.
- **Remaining / dependencies and blockers:** Requires real ANTs `SV-DEP01` for N4 and EDDY experimental/policy results for EDDY claims.

## P2 — capability expansion and optional interoperability

### SV-C02 — Expand Stage C atlas/transform interoperability only with contracts

- **Origin / priority / status / subsystem:** Stage C and Phase 0; P2; Not started; unsupported ANTs/HDF5/nonlinear transform, probability-atlas and tissue-map transport.
- **Acceptance:** For each newly proposed format/transform, implement a versioned explicit parser/adapter, direction and coordinate contract, round-trip/known-answer geometry, source/provenance hashes and downstream QC before enabling it. Existing unsupported routes must continue to fail safely until then.
- **Environment:** Scientific tool installations, synthetic phantoms, permitted registered real datasets and expert review.
- **Evidence:** Phase 0 records deliberate blocks on unsupported stacks and incomplete high-resolution 5TT/probability/FOD/tensor transport; `SV-C01` covers currently supported paths.
- **Remaining / dependencies and blockers:** Define one format/task at a time, following `SV-C01`; no generic transformation assumption is authorized.

### SV-EXT01 — Validate external derivative compatibility

- **Origin / priority / status / subsystem:** Scientific roadmap and Phase 0; P2; Not started; importers, scientific artifact declarations and provenance.
- **Acceptance:** Specify supported third-party derivative formats/sidecars, validate identity, gradients/geometry/history and tool provenance, distinguish trusted from unverified products, and reject ambiguous associations without modifying sources.
- **Environment:** Synthetic derivatives plus permitted outputs from named external tools/versions on supported platforms.
- **Evidence:** Phase 0 lists provenance-aware external derivatives as incomplete; basic image/tract import does not establish scientific history.
- **Remaining / dependencies and blockers:** Requires format-by-format contracts and `SV-C01` for spatial products.

### SV-REP01 — Add bounded scientific reporting/interoperability refinements

- **Origin / priority / status / subsystem:** Scientific roadmap and Phase 0; P2; Not started; sanitized metadata export, tractometry and publication-oriented figures.
- **Acceptance:** Define each feature's scientific meaning, privacy-safe export schema and reproducibility checks before implementation; retain exact input/provenance links and never imply an unvalidated measurement is accepted.
- **Environment:** Synthetic and permitted datasets, privacy review, CLI/GUI tests and scientific reviewer.
- **Evidence:** Phase 0 lists shareable sanitized metadata, full tractometry and publishable figure controls as incomplete; no Phase 1–4 handoff claims them.
- **Remaining / dependencies and blockers:** Prioritize only after correctness gates and explicit product scope; this entry is not permission to add new algorithms during Phase 5.

## Stage B EDDY experimental protocol

The following plan operationalizes `SV-EDDY01`–`06`. It is **unexecuted**. It is designed to distinguish data, mask, denoising, interpolation and EDDY prediction effects before any change to defaults or acceptance criteria.

1. **Freeze inputs and hypotheses.** Obtain authorized access to the previously investigated b1000 acquisition and suitable comparison acquisitions. Record acquisition/gradient/phase-encoding/readout/slice metadata, original checksums, prior preprocessing history and the exact EDDY/FSL/MRtrix versions and command lines. Define inclusive-residual and weighted-CNR file/field semantics from the specific installed version. Predeclare how zero, negative, missing and nonfinite denominators will be counted. Keep all derivatives separate and source data read-only.
2. **Reproduce the anomaly.** Replay the existing MP-PCA-before-EDDY route without changing parameters. Save raw diagnostic outputs and code used to derive summary statistics. Map finite/zero/infinite/NaN CNR and zero residuals by voxel, slice, volume, b-value/shell and anatomical region; compare with outlier and motion reports. Determine whether infinities arise from zero denominators, missing values, parsing or the model. Do not suppress these values to produce a clean aggregate.
3. **Controlled processing matrix.** On the same eligible inputs, compare (A) no prior denoising with EDDY and (B) existing MP-PCA-before-EDDY with EDDY, using matched EDDY settings and scientifically comparable masks. Analyze residual distributions, CNR histograms/quantiles and spatial maps, GP prediction diagnostics, outlier locations and downstream QC with paired statistics and uncertainty. Treat denoising association as a hypothesis, not a universal causal conclusion. Consider (C) EDDY-before-denoising **only after** reviewing the resampling/interpolation-induced noise correlations and whether the proposed post-EDDY method's assumptions hold; document exclusions and altered image/gradient contracts.
4. **Mask sensitivity without confounding.** Hold the processing branch fixed while comparing an anatomically reviewed baseline mask, controlled modest perturbations and a deliberately poor mask if ethical/scientifically useful. Separately cross selected mask conditions with denoising conditions. Record included GP-estimation voxels and changes in residual/CNR maps. Do not attribute every prediction failure to a modest mask difference.
5. **Outlier replacement reference experiment.** Use appropriately paired movement-corrupted and low-motion acquisitions. Verify correspondence of subject, diffusion direction, b-value, anatomy, acquisition conditions and processing space; mark genuine motion dropout. Compare EDDY predictions/replacements with valid low-motion reference measurements and uncertainty, including spatial residuals and detection errors. Large intensity changes alone are not a failure criterion. The 2016 paper motivates this framework; it does not substitute for a TractoSphere-specific comparison.
6. **Review and decision gate.** Have a scientific reviewer inspect all branches, mismatches, invalid-stat maps, mask analyses and replacement-reference comparisons. Define specific QC warnings and investigation-only states for zero inclusive residuals, nonfinite weighted CNR and questionable predictions, with regression fixtures and GUI/CLI/Batch propagation. Record a scoped decision to retain or revise the existing MP-PCA/EDDY order, with limitations and tool versions. Only after this review may a separate approved code task change preprocessing defaults; no experiment result alone silently changes historical plans.

The protocol must record missing controls, failed runs and assumptions as results. It cannot treat EDDY exit code, a plausible corrected image, a small brain-mask difference, or the magnitude of a replacement as sufficient proof of scientific validity. Stage B `eddy_quad`/`eddy_squad` work (`SV-QC01`) consumes these diagnostics but does not replace the controlled investigation.

## Preserved Phase 4 verification checkpoint

At application commit `f9fc8ba`, the user ran `tests/gui/test_tracking_guided_native.py` in normal macOS Cocoa: **3 passed in 9.32 s, exit 0**. The repaired density Viewer handoff presents the hash-verified NIfTI output; the native MIF remains in the execution manifest. The focused offscreen guided selection was **29 passed, 3 Cocoa-only skipped**; a selected cross-phase GUI regression run was **59 passed**; all CLI tests were **34 passed**. Phase 4 also recorded a restricted-session core cancellation failure and a distinct native tracking-cancellation pass. These are targeted software checks, **not** complete ACT/5TT, anatomical ROI, large-data or whole-application scientific validation. See [Phase 4](PHASE4_IMPLEMENTATION_AND_HANDOFF.md) for commands and measured navigation timings.
