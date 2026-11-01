# Chronological H1 constant, fixed-ridge, and normalization comparison

The versioned chronological specification rejects duplicate JSON keys and
nonfinite JSON constants before source inspection or fitting.

This is a **custom within-recording research comparison**, not a FALCON benchmark result, prospective clinical validation, or statistical significance test. It follows the [public-asset overlap audit](BCI_FALCON_H1_REAL_DATA_TRIAL_EXPERIMENT.md) and preserves the [pinned file-level reference](BCI_FALCON_H1_RIDGE_REFERENCE.md). The previous custom experiment calibrated on **later S1 trials 4–5** (`[2353, 3771)`) before predicting **earlier S1 minival trial 1** (`[0, 909)`). That was retrospective calibration, not a prospective validation. Its original results remain unchanged: alpha `29763.51441631313`, evaluation uniform-average R² `-0.17011`, variance-weighted R² `0.02175`, with per-output and independent-reference comparisons in the earlier audit.

## Frozen source and chronology

The sole source here is the public DANDI 000954 **held-in calibration S1 set1** NWB asset `e40a2eca-883d-4c80-81e3-4203a49506b5`, SHA-256 `8a09f6ead8a44776d2cee62850ff489bd16b1323799c986cad070dad784a2b83`, 12,233 represented 20 ms bins and 176 units. Its archive [asset metadata](https://api.dandiarchive.org/api/assets/e40a2eca-883d-4c80-81e3-4203a49506b5/) advertises the same content hash and `draft` Dandiset. The NWB's one regular `starting_time` clock, monotonically increasing `TrialNum`, and original source indices establish **within-file** ordering. The deidentified `1925-01-08` label is not treated as a participant calendar date or evidence of order against another file. No ordering across recordings is asserted.

The versioned `falcon-h1-ridge-chronological-spec-v1` was saved privately **before any evaluation score was calculated**. Its SHA-256 is `f801270f6c05ef46a138894f4aeaeb0d4f3ac5dd9ae66b14283de7e5d6196a86`. The rule uses the first eight complete trials for training, next two for calibration, one unused trial as a separation gap, and three later complete trials for evaluation. Trial 15 is unselected because the recording ends without a following TrialNum boundary to establish its completion. An earlier private draft had included trial 15; it was corrected on this boundary evidence before scoring. No split search or score-based selection occurred.

| Use | Original source indices `[start, stop)` | Trial IDs | Bins | Exposure | Raw `eval_mask` true/false |
| --- | ---: | --- | ---: | ---: | ---: |
| Training | `[0, 6274)` | 1–8 | 6,274 | 125.48 s | 6,115 / 159 |
| Calibration | `[6274, 7809)` | 9–10 | 1,535 | 30.70 s | 1,479 / 56 |
| Unused separation | `[7809, 8743)` | 11 | 934 | 18.68 s | 925 / 9 |
| Evaluation | `[8743, 11370)` | 12–14 | 2,627 | 52.54 s | 2,569 / 58 |
| Unselected file end | `[11370, 12233)` | 15 | 863 | 17.26 s | 838 / 25 |

The first calibration bin follows the last training bin on the same source clock; each spike is assigned to one full-source bin. Calibration ends at 156.16 s; evaluation's first bin window starts at 174.84 s. The 18.68 s intervening trial is unselected. The fixed 12-bin causal filter resets at each **role segment** start and carries state across trial changes *within* that segment. History length is zero. Calibration neural history is never carried into evaluation. This reset, rather than an arbitrary edge buffer, removes cross-role filter dependence. The regular NWB clock cannot reveal unrepresented acquisition gaps.

## Three conditions on identical observations

All conditions use the same safe 20 ms spike counts, seven ordered velocity features, raw evaluation mask, finite-target rule, causal filter, and 2,569 included evaluation bins. Model fitting uses 3,236 training bins after the raw mask, nonfinite-target and reference still-time rules; the union of exclusions is 3,038 (159 raw-mask false, 2,924 still-time before accounting for overlap, zero nonfinite). Calibration uses 1,479 raw-mask-true bins; **calibration target labels are never used**. Evaluation excludes 58 raw-mask-false bins and no additional nonfinite targets. Raw mask values are preserved and do not imply acquisition failure.

| Condition | Definition |
| --- | --- |
| Constant training mean | The mean of the seven training targets over the declared selected fitting bins, repeated at each evaluation bin. |
| Fixed ridge | Ridge with predeclared `alpha=1.0`, fitted once on selected training targets and causally filtered neural features normalized using selected **training** features. Coefficients, intercept and training normalization remain fixed during evaluation. |
| Normalization-recalibrated ridge | The **same fitted coefficients and intercept**. Only feature means and standard deviations are re-estimated from earlier, selected **calibration neural features**. No calibration targets, supervised retraining or model selection are used. |

Both Ridge conditions filter and predict on the same evaluation segment, with identical feature ordering. Zero feature standard deviations are replaced by one, as declared. The previous upstream reference used `GridSearchCV` with five folds; this comparison **does not retain that model-selection procedure**. Its alpha was explicitly fixed at `1.0` before scoring to prevent evaluation-based tuning. It does not claim upstream pipeline parity.

## Observed results

Metrics use only the identical included evaluation indices for all three conditions. R² is `1 − SSE/SST` for each output; MAE and RMSE are in the NWB's recorded **arbitrary velocity units**. Uniform-average R² is the mean of seven defined output R² values; variance-weighted R² weights them by each output's evaluation-target variance. A constant target or fewer than two included bins makes R² undefined rather than silently accepting a finite score. Pooled results are not the mean of per-trial R² values.

| Condition | Pooled uniform R² | Pooled variance-weighted R² |
| --- | ---: | ---: |
| Constant training mean | -0.00375 | -0.00107 |
| Fixed ridge | -0.35098 | -0.20014 |
| Normalization-recalibrated ridge | -0.45903 | -0.35439 |

| Condition | Output | R² | MAE | RMSE |
| --- | --- | ---: | ---: | ---: |
| Constant | tx | -0.00590 | 0.000287376 | 0.000677841 |
| Constant | ty | -0.01018 | 0.000300164 | 0.000762946 |
| Constant | tz | -0.00636 | 0.000472143 | 0.00133291 |
| Constant | rx | -0.00016 | 0.00226647 | 0.00473891 |
| Constant | g1 | -0.00029 | 0.00181876 | 0.00564091 |
| Constant | g2 | -0.00001 | 0.00176470 | 0.00577291 |
| Constant | g3 | -0.00333 | 0.00128502 | 0.00531821 |
| Fixed ridge | tx | -0.74483 | 0.000630702 | 0.000892742 |
| Fixed ridge | ty | -0.58221 | 0.000670308 | 0.000954834 |
| Fixed ridge | tz | -0.40343 | 0.00110796 | 0.00157406 |
| Fixed ridge | rx | -0.07659 | 0.00384196 | 0.00491664 |
| Fixed ridge | g1 | -0.31299 | 0.00496954 | 0.00646274 |
| Fixed ridge | g2 | -0.22850 | 0.00482003 | 0.00639854 |
| Fixed ridge | g3 | -0.10832 | 0.00418519 | 0.00558956 |
| Recalibrated ridge | tx | -0.73920 | 0.000643910 | 0.000891302 |
| Recalibrated ridge | ty | -0.68449 | 0.000703415 | 0.000985212 |
| Recalibrated ridge | tz | -0.45974 | 0.00114700 | 0.00160533 |
| Recalibrated ridge | rx | -0.19484 | 0.00413714 | 0.00517962 |
| Recalibrated ridge | g1 | -0.58356 | 0.00546484 | 0.00709746 |
| Recalibrated ridge | g2 | -0.38891 | 0.00517970 | 0.00680347 |
| Recalibrated ridge | g3 | -0.16246 | 0.00438026 | 0.00572444 |

| Condition | Trial 12 R² uniform / weighted (884 bins) | Trial 13 (731 bins) | Trial 14 (954 bins) |
| --- | ---: | ---: | ---: |
| Constant | -0.35019 / -0.00810 | -0.05471 / -0.02436 | -0.04474 / -0.01108 |
| Fixed ridge | -25.14397 / -0.00288 | -1.65080 / -0.41641 | -1.73199 / -0.34863 |
| Recalibrated ridge | -31.05025 / -0.09425 | -2.04117 / -0.62651 | -2.27410 / -0.55155 |

The large negative per-trial uniform scores reflect poor predictions relative to small within-trial target variance; they are retained, not clipped. On this predeclared split, neither Ridge condition outperformed the constant predictor on pooled R². This one file and three evaluation trials cannot establish a general adaptation effect or significance. Individual bins are serially dependent observations, not independent subjects.

## Reproducibility and boundaries

The Qt-free API is `run_h1_chronological_experiment(spec_path)`, and the CLI is `dmri-studio bci ridge-h1-chronological --spec PRIVATE_SPEC.json --output PRIVATE_NEW_REPORT.json`. The shared exclusive report writer refuses source, specification and existing-output collisions; path-bearing reports and all predictions remain outside the repository. The report schema `falcon-h1-ridge-chronological-report-v1` stores exact source/specification hashes, original selections, exclusions, software versions, normalization parameters, fitted coefficients, every condition's predictions and pooled/per-trial metrics. The original public NWB SHA-256 was unchanged after execution. The experiment ran once from the frozen final specification; no remote benchmark was contacted.

The focused BCI, CLI and scene suite passed **90 tests**; Ruff lint and formatting checks passed. Synthetic cases cover ordering and complete-trial rejection, shared evaluation indices, normalization scope, label isolation, future-input causality, constant-predictor metrics, source integrity, CLI/API agreement and lazy import behavior. This comparison does not assess cross-session stability, multiple tasks, optimal alpha, distribution shift causes, biological unit identity or official benchmark performance.

A later [self-contained v2 report](BCI_FALCON_H1_CHRONOLOGICAL_REPORT_V2.md) embeds the exact selected evaluation targets and candidate-mask evidence after explicit source-hash verification. The frozen v1 report, condition predictions and numerical results above remain unchanged; v2 does not rerun the experiment.
