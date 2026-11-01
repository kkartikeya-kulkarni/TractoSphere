# Experimental FALCON H1 recording inspection

This is a **read-only, headless** research extension. It describes one modern
FALCON H1 NWB recording structure. It does not train a decoder, score the
benchmark, infer electrode anatomy, or connect these recordings to diffusion
MRI. No BCI GUI or live acquisition path is implemented.

## Source contract inspected before implementation

The upstream [`load_nwb` H1 branch](https://github.com/snel-repo/falcon-challenge/blob/770c9b9597b8f58bf67b5a9ec6401ef705a2cb0f/falcon_challenge/dataloaders.py)
reads `units`, `OpenLoopKinematicsVelocity`, `OpenLoopKinematics`, `TrialNum`
and `eval_mask`. It bins spike times at 20 ms using kinematics timing, marks
trial changes where `TrialNum` increases, and returns the raw evaluation mask.
An older `Blacklist` fallback exists upstream; this first inspector supports
**only the selected modern `eval_mask` structure** and reports an absent mask
as an unsupported/missing field. The inspector does not bin or return spikes.

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
| `units/id`, `spike_times`, `spike_times_index` | 176 unit rows; 71,718 source spike times, indexed as NWB ragged arrays. The inspector checks the indexes and times; it does not infer cell identity or compute bins. |
| `OpenLoopKinematics/data` and `OpenLoopKinematicsVelocity/data` | Both `1503 × 7`; the latter is the upstream decoding covariate. The seven labels come from the file description. |
| `starting_time` | Both kinematics, trial number and mask start at `0.0` seconds. Each has `rate=0.02` with attribute `unit=seconds`; the upstream loader uses this as a **time step**, so the inspector reports a 20 ms period, not 0.02 Hz. |
| `TrialNum/data` | 1,503 finite integer-valued samples, values 3 then 4. The increase starts the next trial at zero-based sample 699; the first sample begins the first segment. `intervals/epochs` has 29 rows and is not substituted for upstream `TrialNum` boundaries. |
| `eval_mask/data` | 1,503 booleans: 1,466 true and 37 false. Upstream documents true as an evaluation timestep. The NWB description says the mask identifies timesteps to ignore covariates for training/evaluation. The inspector reports both counts without changing, inverting or interpreting them as a training split. |
| Missing values | Both seven-column kinematic arrays have no nonfinite entries in this asset. The inspector counts nonfinite values without imputation; nonfinite timing/trial values or malformed masks block inspection. Empty spike lists for individual units are valid, but invalid ragged indexes or nonfinite spike times are not. |

## Supported inspection behavior

`inspect_falcon_h1(path)` in the Qt-free core validates the selected datasets,
their time axes, dimensions, trial changes, binary evaluation mask, and ragged
unit indexes. It returns only a bounded summary and a complete source hash,
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
1,503 samples at 0.02 s intervals, 176 unit rows (one with no spikes), 71,718
spike timestamps, trial segments `[0,699)` and `[699,1503)` with source trial
numbers 3 and 4, and raw mask counts of 1,466 true and 37 false. Both seven-
feature arrays had zero nonfinite values. Scientific acceptance remained
`unassigned`. The private local JSON report contains the absolute source path;
neither it nor the NWB file is committed.

Focused verification command:

```sh
PYTHONPATH=src python -m pytest -q tests/core/test_bci_h1.py tests/cli/test_load.py tests/test_scene.py
```

It passed **51 tests** in the development environment. The new tests use
synthetic HDF5 files and cover malformed/mismatched timing, dimensions,
missing fields, nonbinary masks, malformed trial numbers and ragged spikes,
source changes during inspection, nonfinite covariates and CLI/API parity.
The single public recording demonstrates structural inspection only; it does
not validate decoding or generalize this schema to every H1 asset.
