# Experimental FALCON H1 recording inspection

This original milestone is a **read-only, headless** research extension. It describes one modern
FALCON H1 NWB recording structure. It does not train a decoder, score the
benchmark, infer electrode anatomy, or connect these recordings to diffusion
MRI. A later [read-only BCI Results workspace](BCI_FALCON_H1_RESULTS_WORKSPACE.md)
opens saved chronological research reports; this inspector remains headless.
No live acquisition path is implemented.

## Source contract inspected before implementation

The upstream [`load_nwb` H1 branch](https://github.com/snel-repo/falcon-challenge/blob/770c9b9597b8f58bf67b5a9ec6401ef705a2cb0f/falcon_challenge/dataloaders.py)
reads `units`, `OpenLoopKinematicsVelocity`, `OpenLoopKinematics`, `TrialNum`
and `eval_mask`. It bins spike times at 20 ms using kinematics timing, marks
trial changes where `TrialNum` increases, and returns the raw evaluation mask.
An older `Blacklist` fallback exists upstream; this first inspector supports
**only the selected modern `eval_mask` structure** and reports an absent mask
as an unsupported/missing field. The inspector computes descriptive binned
counts but does not return spike timestamps or a time-by-unit matrix.

The [FALCON benchmark paper](https://papers.neurips.cc/paper_files/paper/2024/file/8c2e6bb15be1894b8fb4e0f9bcad1739-Paper-Datasets_and_Benchmarks_Track.pdf)
describes H1 as open-loop attempted reach and grasp with seven robotic
kinematic dimensions: three translations, one roll rotation, and three grasp
shaping variables. The NWB `description` on both kinematic series gives the
ordered labels `tx,ty,tz,rx,g1,g2,g3`; both data arrays declare `unit=arbitrary`.
These are cue-related behavioral covariates, **not anatomical coordinates or
measured participant movement**, and this inspector does not assign physical
velocity units.
The paper describes 20 ms neural bins and 50 Hz kinematics. This inspector
does not assign brain regions to units or electrodes.

## Permitted example and observed structure

One open-access, CC-BY-4.0 **held-in minival** asset from
[DANDI 000954](https://dandiarchive.org/dandiset/000954) was downloaded to a
private local directory for read-only inspection. DANDI asset ID:
`6e45be2c-12b9-43ee-b945-9426f7349eb2`; complete source SHA-256:
`a95585515b1a12a61603fb3170843351d2fb6551ba6e0456c483570b085e68f3`.
The Dandiset is a draft, so the digest, not its mutable draft listing, pins
this demonstration. No NWB data are committed to this repository.

| Field | Observed value and interpretation |
| --- | --- |
| NWB/HDF5 | NWB `2.6.0`, HDF5 read-only source |
| `units/id`, `spike_times`, `spike_times_index` | 176 unit rows; 71,718 source spike times, indexed as NWB ragged arrays. The inspector checks indexes and times and counts spikes in analyzed windows; it does not infer cell identity. |
| `OpenLoopKinematics/data` and `OpenLoopKinematicsVelocity/data` | Both `1503 × 7`; the latter is the upstream decoding covariate. The seven labels come from the file description. |
| `starting_time` | Both kinematics, trial number and mask start at `0.0` seconds. Each has `rate=0.02` with attribute `unit=seconds`; the upstream loader uses this as a **time step**, so the inspector reports a 20 ms period, not 0.02 Hz. |
| `TrialNum/data` | 1,503 finite integer-valued samples, values 3 then 4. The increase starts the next trial at zero-based sample 699; the first sample begins the first segment. `intervals/epochs` has 29 rows and is not substituted for upstream `TrialNum` boundaries. |
| `eval_mask/data` | 1,503 booleans: 1,466 true and 37 false. Upstream documents true as an evaluation timestep. The NWB description says the mask identifies timesteps to ignore covariates for training/evaluation. The inspector reports both counts without changing, inverting or interpreting them as a training split. |
| Missing values | Both seven-column kinematic arrays have no nonfinite entries in this asset. The inspector counts nonfinite values without imputation; nonfinite timing/trial values or malformed masks block inspection. Empty spike lists for individual units are valid, but invalid ragged indexes or nonfinite spike times are not. |

## Spike binning and exposure contract

The selected NWB series store `starting_time` in **seconds** and a `rate`
attribute that the pinned upstream H1 loader treats as a **0.02 s step**, not
as 0.02 Hz. The loader constructs timestamps at `start + index × step` and
passes them to its [`bin_units`](https://github.com/snel-repo/falcon-challenge/blob/770c9b9597b8f58bf67b5a9ec6401ef705a2cb0f/falcon_challenge/dataloaders.py)
as **bin ends**. For end `e`, the analyzed window starts at `e − 0.02 s`, or
the prior end if closer. Windows are left-inclusive and right-exclusive;
only the last includes its right edge, matching NumPy histogram. With ends
`0, 0.02, 0.04`, a spike at `0` belongs to the second bin and a spike at
`0.04` to the final bin. Constructed lower edges allow `1e-12 s` floating
arithmetic tolerance.

For explicit irregular end timestamps supplied to the Qt-free
`h1_bin_windows`/`bin_h1_spikes` APIs, ends must be finite and strictly
increasing. A longer than 20 ms interval contributes only the final 20 ms
before its later end; spikes in the gap are outside analyzed windows. A
shorter interval contributes only its actual width. `TrialNum` increases
start new segments at the current sample; spikes in that bin count toward the
new segment under the upstream labeling. The selected NWB file provides only
`starting_time` and a constant step, so **actual missing acquisitions or
clock gaps cannot be discovered from that representation**. The inspector
reports zero *represented* gaps for its constructed regular clock. It does
not substitute `intervals/epochs` for `TrialNum` boundaries.

Exposure is the sum of analyzed window widths, independent of `eval_mask`.
A unit's descriptive rate is in-window spikes divided by this exposure.
Nominal duration is sample count × 20 ms and is reported separately. Spikes
before the first window, in represented gaps, or after the final window are
counted separately. Empty source units and units with no in-window spikes are
reported separately; neither automatically means a failed electrode.
Duplicate finite timestamps count separately. Unsorted or nonfinite spike
times are rejected rather than sorted or imputed. `bin_h1_spikes` returns
one unit's counts as `uint64`, avoiding the pinned upstream `uint8` wrap at
256. The inspector reads source spikes in chunks and never builds a full
time-by-unit matrix; an explicit API bin vector still uses memory
proportional to its number of bins.

The raw `eval_mask` is reported unchanged: its true/false counts do not
filter the descriptive rates and false does not mean acquisition failure.
Nonfinite position and velocity covariates are counted by feature and raw
mask status without repair or removal. Trial summaries show boundaries,
exposure, raw mask counts and observed spikes across units. These are
**descriptive findings**, not a quality score or acceptance decision.

Direct numerical comparison with the pinned upstream function gave equal
counts on ordinary and gap/short-interval fixtures. With 300 spikes in one
bin, upstream `uint8` output was `44` and the new API returned `300`. The
older-layout fallback, ambiguous timing and malformed unit times are not
silently accepted here.
On the permitted public asset, upstream's maximum bin count was 6; its 71,718
total counts and every per-unit count matched this inspector's observations.

## Supported inspection behavior

`inspect_falcon_h1(path)` in the Qt-free core validates the selected datasets,
their time axes, dimensions, trial changes, binary evaluation mask, and ragged
unit indexes. Its report schema is `falcon-h1-modern-nwb-v2`. It returns only a bounded summary and a complete source hash,
never the neural or behavioral arrays. It rejects changed source files before
returning. The CLI command is `dmri-studio bci inspect-h1 --nwb FILE` after
installing the optional `bci` extra. No output file is written by inspection;
the JSON printed to stdout contains the source identity and schema version.

This deliberately narrow implementation rejects other FALCON tasks, alternate
H1 NWB layouts, `Blacklist`-only older files, timestamps in place of the
selected `starting_time` representation, inconsistent time axes, and missing
required fields. NaN/Inf in kinematic values are reported as missing-value
counts rather than repaired or silently discarded. It makes no assertion
about benchmark eligibility, behavioral validity, human subject identity,
clinical use, or decoder performance.

## Demonstration and verification

The headless API and CLI inspected the single public asset above. The source
hash before and after was the pinned SHA-256 shown above. The CLI returned
1,503 samples at 0.02 s intervals and 30.06 s nominal/analyzed exposure;
176 unit rows (one with no source spikes and one with no in-window spikes);
71,718 spike timestamps, all within analyzed windows. Trial segments
`[0,699)` and `[699,1503)` held 33,059 and 38,659 observed spikes across
units. Source trial numbers were 3 and 4; raw mask counts were 1,466 true and
37 false. No represented clock gap, short interval or nonfinite covariate
was found. This cannot prove no acquisition samples were missed. Scientific acceptance remained
`unassigned`. The private local JSON report contains the absolute source path;
neither it nor the NWB file is committed.

Focused verification command:

```sh
PYTHONPATH=src python -m pytest -q tests/core/test_bci_h1.py tests/cli/test_load.py tests/test_scene.py
```

The current focused command passed **58 tests**. Synthetic HDF5 and numerical
fixtures cover malformed/mismatched timing, dimensions, missing fields,
nonbinary masks, malformed trial numbers, ragged spikes, boundary inclusion,
empty/duplicate/unsorted/nonfinite spike times, represented gaps and shorter
intervals, exposure denominators, counts above 255, raw-mask/nonfinite
separation, source changes and CLI/API parity. Ruff lint and format checks
passed; isolated core/CLI import loaded neither Qt/VTK nor eager `h5py`.
The single public recording demonstrates structural inspection only; it does
not validate decoding or generalize this schema to every H1 asset.
The separate [multi-recording inventory](BCI_FALCON_H1_INVENTORY.md) reuses
this exact inspector for sequential descriptive comparison.
The bounded [ridge reference experiment](BCI_FALCON_H1_RIDGE_REFERENCE.md)
is separate and requires explicit, nonoverlapping training, calibration and
evaluation roles. Inspection alone does not establish those roles.
