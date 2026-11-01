# Self-contained H1 chronological report v2

Schema `falcon-h1-ridge-chronological-report-v2` preserves every frozen v1 selection, model parameter, condition prediction, recorded metric and source/specification declaration. It adds `original_v1_report_sha256`, an `enrichment` provenance object and an `observations` object. `enrichment` records the explicitly supplied source path, matching source SHA-256 and read-only extraction method; the passive reader omits that path from its display view and never opens it. V1 files remain supported unchanged; opening one does not reconstruct targets or follow its embedded source path.

## Observation alignment

The existing `evaluation.conditions[condition].predictions` arrays retain **all evaluation candidate bins**, in original source-index order over the evaluation `[start, stop)` span. Each row has seven finite numeric outputs. This remains the v1 prediction storage contract.

`observations.storage` is `included_only_in_original_index_order`. Its `targets`, `original_indices`, and `trial_numbers` arrays have equal length: one row per **included** evaluation bin, ascending by original source index. Each target row has seven finite values. `original_indices` must equal the unchanged `evaluation.selected_original_indices`; trial ID must match the reported complete-trial interval containing that index. Prediction row for source index `i` is `predictions[i - evaluation.start]`. No target value is fabricated for an excluded bin.

`observations.candidate_raw_eval_mask` and `candidate_target_finite` are separate Boolean arrays of length `evaluation.samples`, one value per **candidate** bin. Inclusion is `raw_eval_mask AND all-seven-targets-finite`. A false raw mask is retained as a selection, not inferred acquisition failure. Additional nonfinite-target exclusion counts only bins with raw mask true and finite flag false. The reader checks included indices, total and per-trial counts, and all conditions against these declarations. Finiteness flags document excluded values without embedding nonfinite JSON numbers.

`observations.output_labels` is exactly `tx, ty, tz, rx, g1, g2, g3`, the established H1 output order. Each aligned `output_units` item has `label`, `unit`, and `provenance`. A unit found in the SHA-matched NWB `OpenLoopKinematicsVelocity/data` storage attribute is labeled `source_nwb_velocity_storage_unit`. Otherwise `unit` is JSON `null` and provenance is `unknown`. A feature name never supplies a unit. The permitted source records `arbitrary`; that is a storage-unit declaration, **not** an inferred physical unit.

`observations.metric_definitions` binds the v1 calculation: per-output R² is `1−SSE/SST`, undefined if fewer than two included observations or `SST≤0`; MAE is mean absolute error and RMSE is root mean square error, each undefined with zero included observations. Uniform-average R² is the mean of all seven defined per-output R² values. Variance-weighted R² uses each output's **population** evaluation-target variance as its weight. Both aggregates are undefined if any output R² is undefined. Pooled metrics use all included bins; per-trial metrics use only included bins within that trial.

## Explicit creation and validation

Create a new private v2 file with an **explicitly named** source:

```sh
dmri-studio bci enrich-h1-chronological-report \
  --report PRIVATE_FROZEN_V1.json \
  --source EXPLICIT_MATCHING_RECORDING.nwb \
  --output PRIVATE_NEW_V2.json
```

The command never opens the path embedded in v1. It read-only inspects the caller's source, checks its SHA-256 against v1, verifies labels, reconstructs the declared evaluation selection and trial IDs, and checks all recorded pooled and per-trial metrics against unchanged predictions and extracted targets **before** exclusive output creation. It refuses replacing the source, original report, specification or an existing output. It does not refit, retune, or alter the original experiment.

The Qt-free passive reader supports both versions. For v2, it checks dimensions, alignment, labels, unit declarations, exclusions and trials, and recomputes every supported metric. It rejects discrepancies without replacing recorded values. Relative tolerance is `1e-9`, absolute tolerance `1e-10` to accommodate NumPy's original float64 reductions versus independent `math.fsum` reductions. On the permitted report, 276 compared numeric metrics had maximum absolute difference `2.84e-14` and maximum relative difference (denominator floor `1e-12`) `2.65e-11`.

Passing this check establishes **internal report consistency with embedded evidence**, not independent source verification during passive reopening or scientific acceptance. The upgrade command verifies the explicitly supplied source at creation time; that one-time check does not change the passive GUI's `not_performed` source-verification state. The embedded original-v1 hash identifies the frozen report, but passive reading does not follow or compare a separate v1 file.

## Frozen real-data check and visual limits

The permitted public S1 held-in calibration recording supplied the explicit source. New v2 data and private screenshots remain outside the repository. The original v1 SHA-256 stayed `c947e4c8ca365542d091c2735955b1daeca9abf4e33790a9bec81c5f13aaf307`; the final v2 report SHA-256 is `2b447bde68f21e49f57cff17ba675439b04f84d62bd9f768c8f796297b7416d2`. Separate read-only hash checks confirmed that the source still matches the recorded SHA-256 in both reports. All three candidate prediction arrays, selected indices, condition metrics and trial intervals were unchanged in the new report. V2 embeds **2,569** observed included bins: trial 12 **884**, trial 13 **731**, trial 14 **954**. Recomputed pooled and per-trial metrics agree with frozen values; no fitting occurred. Pooled uniform-average R² remains approximately `-0.00375`, `-0.35098`, `-0.45903`; variance-weighted R² remains `-0.00107`, `-0.20014`, `-0.35439`.

The BCI Results page draws observed targets with all three condition predictions or the selected prediction condition, preserving original bin-index gaps and trial boundaries. Light and dark **offscreen** captures of the real v2 overlay were inspected privately at 1100×800 logical pixels. Native Cocoa desktop capture was unavailable here and remains unverified. This is still one recording and three later trials, not an official FALCON benchmark, general adaptation result, significance test, or scientific acceptance.

The relevant isolated-environment BCI numerical suite passed **87 tests**. The application environment passed **32 focused core/offscreen-GUI tests**. Synthetic tests exercise malformed alignment, altered target/prediction/metric values, exclusion flags, trial membership, unit provenance, undefined and constant-target calculations, v1 compatibility, CLI/API agreement, import isolation, explicit source mismatch, exclusive output creation and source preservation. Ruff lint and formatting checks passed.
