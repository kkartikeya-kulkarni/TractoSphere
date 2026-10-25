# Testing phase and coverage

TractoSphere's scientific toolbox is being tested with real public diffusion MRI data. This page summarizes the routes exercised as of 24 October 2026 and highlights where testing or scientific review continues. The separate [public alpha validation record](../validation.md) describes an older release; this development branch has a wider feature set.

| Area | Evidence established in the local public-data audit | Still requiring care |
| --- | --- | --- |
| Import and native preprocessing | Raw/partly processed public DWI, companion gradients, MP-PCA, Gibbs and masks were exercised; a documented public acquisition reached bounded TOPUP/EDDY and N4 technical outputs | Acquisition-specific suitability, mask and correction diagnostics require scientific review; no universal preprocessing order is accepted |
| DTI and CSD | GUI produced tensor/FA/MD/AD/RD and WM FOD/response outputs on real public data | Real numerical QC recorded negative/nonphysical or uncertain findings; scientific model acceptance remains open |
| Anatomy, registration and atlas | GUI prepared T1, verified low-b, transformations, a Schaefer atlas, existing-atlas QC and 5TT/compartment outputs | Each anatomy/tissue/atlas decision belongs to exact evidence; high-resolution 5TT path and some advanced registration combinations remain unvalidated |
| Tracking, filtering and density | GUI generated bounded 2k/10k standard tractograms, filtered a real 10k source with length/ROI rules and made a density map | Expert pathway assessment, ACT native Viewer acceptance, large generation and performance remain open |
| SIFT2 and connectivity | GUI ran a bounded SIFT2 result and six diagnostic/reviewed connectivity metrics with a real registered atlas | Small coverage and flagged FA/assignment findings limit quantitative interpretation; not a research-grade connectome claim |
| Batch, QC and saved runs | A small guided batch and completed resume check, saved QC/run reopening and explicit review controls were exercised | Pause/cancel/retry, multi-subject and all advanced adapter combinations are not comprehensively validated |
| 10M tracts | The user reported that an existing 10M TCK loaded in Viewer | Drawing all 10M is not supported by the display cap; 10M generation was blocked by local storage and is untested |

**How to read a status:** `validated_pending_qc` describes technical completion with scientific review still outstanding. A user-reported successful action is useful evidence but is not the same as an instrumented, independently checked run. A result accepted by a qualified reviewer is scoped to its exact image/tractogram, hash, task and recorded observations. Do not transfer it to a new derivative.

For known input/platform limits see [Known limitations](../limitations.md). For a practical route through a real participant, use [One participant, start to finish](walkthrough.md) and record every blocker instead of filling gaps with assumptions.
