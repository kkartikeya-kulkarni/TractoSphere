# BCI alpha acceptance review — 2026-11-01

## Scope and decision

Reviewed `development/bci-falcon-h1-inspector-2026-11-01` at `0c843c9` against merge base `5f9a1512579e4e23d9335d54a0fa0f397fad7578` (`main`). The change set adds the H1 inspector, sequential inventory, bounded ridge/reference and chronological experiments, v1/v2 passive report reading, CLI commands, and a lazy BCI Results page. It changes 31 files at the reviewed revision; no MRI scientific algorithm was changed. This review is **conditional alpha acceptance of the bounded implementation**, not scientific acceptance of decoder results or release approval. The three confirmed defects below were corrected in this review.

## Findings by severity

| Severity | Finding and evidence | Disposition |
| --- | --- | --- |
| P1, confirmed privacy defect | `src/dmri_studio/core/bci_reports.py:86` previously replaced only the first whitespace-delimited token of an absolute path in freeform report text. `"/Users/Private Participant/recording.nwb"` left `Participant/recording.nwb` visible in the passive view and CLI summary. | Omit the whole freeform field containing an absolute local path. The synthetic regression covers a path with spaces. |
| P1, confirmed declaration-integrity defect | `src/dmri_studio/core/bci_inventory.py:205`, `src/dmri_studio/core/bci_ridge.py:474`, and `src/dmri_studio/core/bci_chronological.py:203` parsed versioned JSON declarations with the default last-duplicate-key-wins behavior. A file containing two `roles`, `selections`, or `schema` keys could silently select a different declaration from the one a reviewer read. | Shared strict parser rejects duplicate keys at every nesting level and nonfinite JSON constants before source inspection or fitting. Each entry path has a focused regression. |
| P2, confirmed malformed-report handling defect | `src/dmri_studio/core/bci_reports.py:52` and `:208` previously let extreme finite v2 values raise `OverflowError` (for example during `math.fsum`), rather than the documented report-validation error. | Reject unsupported numeric range with `ValueError`; regression exercises a v2 report and an oversized finite integer. No metric or tolerance was changed. |
| Open scientific limitation, not a code defect | The available public calibration/minival assets have demonstrated observation overlap. The pinned file-level reference therefore rejects that pairing. The frozen within-recording experiment is custom, uses one recording and three evaluation trials, and has negative pooled R² values. | Do not relabel as prospective benchmark validation or tune the frozen experiment. Find documented nonoverlapping assets for an unchanged benchmark comparison. |
| Open verification question | Native Cocoa file picker, appearance, plot readability, report switching, and long-report responsiveness could not be directly observed: the computer-control native pipe failed to start in this session. | Native desktop smoke remains blocked; offscreen Qt tests do not satisfy it. |

No additional numerical, role-separation, source-writing, or MRI regression defect was confirmed by the reviewed code and executed checks. The v2 reader recomputes embedded pooled/per-trial metrics but cannot independently verify the NWB on passive open. The v1 reader cannot recompute metrics because it lacks observed targets. These distinctions remain explicit in the GUI and CLI.

## Contract review

- The H1 spike inspector uses 20 ms bin-end windows, left-inclusive/right-exclusive bins except the final right edge, `uint64` counts, explicit timing/mask validation, chunked spike reads, and source hashes. Its tests include boundary, overflow, malformed input, and pinned-upstream ordinary-fixture comparisons.
- The reference and custom experiment paths bind source hashes and roles, block repeated or overlapping observations, and use the pinned filter/selection/model behavior where claimed. The chronological experiment requires same-file trial ordering, earlier training/calibration, a gap before evaluation, role-start filter resets, and one frozen evaluation selection across constant/fixed/recalibrated conditions. Calibration targets do not enter recalibration. Its fixed alpha and altered selection are documented as custom, not upstream parity.
- Reports are created exclusively outside the repository and refuse source/specification destinations. V2 enrichment requires an explicitly supplied matching source and leaves v1 untouched. The passive reader follows no embedded paths and unpickles no model. Report structure, internal metric consistency, source verification, and scientific acceptance remain distinct.
- Optional HDF5, SciPy, scikit-learn, Qt, and VTK dependencies are not eagerly imported by ordinary core/CLI imports. The Results page creates its worker only when opened; reads and trace preparation occur off the GUI thread. The passive report reader caps input at 32 MiB and plotting caps discontinuous segments. Existing MRI navigation and workflows were sampled separately below.
- The numerical tests contain independent pinned-upstream fixture values for alpha and R² and explicit boundary/overlap/causality cases. Other synthetic tests check internal consistency and invariants; they do not independently establish real-data decoder validity.

## Checks performed

| Check | Result |
| --- | --- |
| Isolated reference environment: all seven `tests/core/test_bci_*.py` modules plus `tests/cli/test_load.py` | **115 passed**, exit 0. |
| Application environment, Qt offscreen: H1 inspector/inventory/report core modules and `tests/gui/test_bci_results_gui.py` | **71 passed**, exit 0. |
| Application environment, Qt offscreen: BCI Results, guided Batch, guided Tractography, two Registration task/navigation checks, `tests/test_scene.py`, and CLI load tests | **69 passed**, exit 0. These sample shared navigation and MRI data behavior; they are not the entire MRI suite. |
| Private frozen v1 and v2 report, passive read only | Both opened; 2,569 included bins; v2 pooled/per-trial recomputation agreed. V1/v2 report SHA-256 remained `c947e4c8ca365542d091c2735955b1daeca9abf4e33790a9bec81c5f13aaf307` / `2b447bde68f21e49f57cff17ba675439b04f84d62bd9f768c8f796297b7416d2`. Reads took 23.5 / 58.9 ms in this environment. No source recording was opened during passive reading. |
| Ruff lint, Ruff formatting, `git diff --check` | Passed for changed code/tests. |
| Offscreen shared scientific scene navigation (`test_scientific_routes_independent_and_preserve_scene`) | **Blocked**: process exited 139 in VTK `QVTKRenderWindowInteractor.__init__` while constructing the MRI Viewer. The 69 BCI/MRI checks above were rerun separately and passed. This offscreen VTK crash alone is not evidence of an application defect. |
| Native desktop smoke and screenshots | **Blocked**: computer-control startup returned `Sky Computer Use native pipe startup failed`; no native screen, picker, captures, appearance, or window interaction was verified. |

## Remaining acceptance boundaries

Native Cocoa interaction remains required for both valid versions, malformed/unsupported reports, picker cancellation, condition/output switching, resizing, scrolling, light/dark appearance, a longer synthetic report, and reopening without stale state. Full MRI regression coverage and independent source verification are outside the completed checks. Benchmark-compatible nonoverlapping data, broader multi-session evaluation, and scientific interpretation of negative scores remain open. No model was refit, no frozen report was modified, and no public release is authorized by this record.

The isolated numerical command used `PYTHONPATH=src:tests OPENBLAS_NUM_THREADS=1` and the private pinned-upstream virtual environment's `python -m pytest -q` over `tests/core/test_bci_h1.py`, `test_bci_inventory.py`, `test_bci_ridge.py`, `test_bci_custom_trials.py`, `test_bci_chronological.py`, `test_bci_reports.py`, `test_bci_reports_v2.py`, and `tests/cli/test_load.py`. The application checks used the existing application virtual environment with `PYTHONPATH=src:tests QT_QPA_PLATFORM=offscreen OPENBLAS_NUM_THREADS=1`. The exact file selections for each application run are listed in the test table. No full native run occurred.
