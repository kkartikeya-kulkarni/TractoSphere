# Experimental FALCON H1 multi-recording inventory

This is a **read-only, headless, descriptive** comparison of explicitly listed
modern H1 NWB recordings. It reuses the [single-recording inspector and binning
contract](BCI_FALCON_H1_INSPECTOR.md). It does not concatenate recordings,
match neurons across sessions, train decoders, assign a quality score, or make
a scientific acceptance decision.

## Versioned private manifest

The API is `inspect_h1_inventory(manifest_path)` in the Qt-free core. The CLI is:

```sh
dmri-studio bci inventory-h1 --manifest PRIVATE_MANIFEST.json \
  --output PRIVATE_NEW_REPORT.json
```

`--output` is optional; without it, JSON goes to stdout. With it, the command
creates a **new** file exclusively and refuses an existing destination or one
that resolves to the manifest or any listed recording. Shell redirection is
outside this protection. Keep the manifest, NWB files and path-bearing report
outside the source repository.

Manifest schema `falcon-h1-inventory-manifest-v1` has this shape (replace the
illustrative path and digest with exact local values):

```json
{
  "schema": "falcon-h1-inventory-manifest-v1",
  "binning": {
    "period_s": 0.02,
    "timestamp_role": "bin_end",
    "analysis_windows": "all_samples",
    "eval_mask": "preserve_raw"
  },
  "measurement": {
    "unit_rate_denominator": "all_analyzed_exposure_s",
    "rate_quantiles": [0.0, 0.25, 0.5, 0.75, 1.0],
    "nonfinite_covariates": "count_by_feature_and_raw_mask_status",
    "trial_boundaries": "TrialNum_increase"
  },
  "recordings": [
    {
      "entry_id": "recording-one",
      "path": "./recording-one.nwb",
      "sha256": "replace-with-64-lowercase-hex-characters",
      "identity": {
        "participant": null,
        "session_day": null,
        "recording_set": null,
        "benchmark_partition": {
          "value": "held-in-minival",
          "provenance": "documented_upstream_mapping",
          "evidence": "https://api.dandiarchive.org/api/assets/ASSET-ID/"
        }
      },
      "dataset": {"dandiset": "000954", "version": "draft", "asset_id": "ASSET-ID"}
    }
  ]
}
```

Paths are explicit and resolve relative to the manifest directory. `entry_id`
is a unique report key, **not** a scientific file identity. The manifest pins
each input by full SHA-256; the inspector verifies it and records the source
size and current inspector/upstream-loader versions. There is no filename-based
identity inference. `dataset` is optional documented context, not a validation
substitute; its version and asset identifier should be recorded for public
comparisons.

Ambiguous duplicate JSON keys and nonfinite JSON constants in the manifest are
rejected before any recording is inspected.

The binning and measurement declarations are validated exactly against the
implemented 20 ms, all-window descriptive contract. An unsupported per-entry
override is reported as that entry's failure; it is never silently applied.

Each identity field is separately `null` (unknown) or an object with `value`,
`provenance`, and `evidence`. Allowed provenance labels are `nwb_metadata`,
`documented_upstream_mapping`, and `explicit_user_declaration`. Available NWB
`general/subject/subject_id`, `session_start_time`, and `identifier` populate
unknown participant, session-day, and recording-set fields respectively.
The session-day value is the **deidentified date label** from the NWB timestamp,
not an actual participant calendar date. `recording_set` retains the complete
NWB identifier; it is a label, not the unique file identity. This source
subject ID is a dataset pseudonym, not independent evidence of a biological
identity across recordings. The partition is not established by these NWB
fields; provide a documented mapping or explicit declaration, or leave it
unknown. A conflicting declaration is reported beside the observed NWB value
and is never silently preferred. Citation strings are retained as provenance;
the tool does not independently verify the contents of an external citation.

## Collisions, partial results and comparability

Manifest order is retained in the report. Each recording is inspected
sequentially; the manifest is capped at 4 MB and 512 recordings. Spike data
are processed per source in chunks, without a combined time-by-unit matrix.

- Repeated resolved paths and equal source hashes under different paths are
  separate errors on **every** affected entry. They are not silently dropped
  or counted twice.
- Different content with the same known participant, session-day,
  recording-set and partition identity is a collision. When participant or
  partition is unknown but known session-day and recording-set agree, it is a
  **possible** collision. Both cases mark the affected entries as errors.
- Different files within one session remain distinct when their recording
  identifiers differ. Sharing a session does not imply duplicate content.
- A failed file remains in the report with its error. Other files can still
  succeed, but `complete=false`, descriptive comparability is false, and the
  CLI returns status **1**. A complete run returns **0**. Global malformed
  manifests return an error without a partial report.
  Exit status 0 means all inspections completed; it does not imply
  `descriptive_eligible=true` or scientific acceptance.

The report gives sample/trial counts, exposure seconds, recorded-unit counts,
in-window spikes, zero-observed-spike units, and min/25th/median/75th/max
**per-unit rates in Hz**. Rates divide by each recording's analyzed exposure
across all raw-mask statuses. Represented timing gaps, raw mask counts and
fractions (denominator: all samples), and nonfinite covariate counts/fractions
by feature are separate. Nonfinite fractions use all samples or the indicated
raw-mask-status sample count; a zero denominator gives `null`. Raw masks are
never modified or interpreted as acquisition failures. A zero-activity unit
is not labeled a failed electrode.

Descriptive comparability checks feature labels/order and storage units,
20 ms bin-end timing and binning configuration. Different exposure and raw-mask
availability are reported, not normalized away. Wrong feature order is rejected
by the single-recording contract; different accepted storage units make a
comparison ineligible. Missing task, session or partition evidence remains
visible. `descriptive_eligible=true` means only that these measurements can be
placed side by side. `future_decoder_training_suitability` remains `not
assessed`; matching dimensions or unit IDs do not establish cross-session
neuron identity. This workflow does not calculate an aggregate score or infer
causes of differences.

The separate [ridge reference experiment](BCI_FALCON_H1_RIDGE_REFERENCE.md)
requires additional role and observation-separation evidence. Descriptive
inventory comparability does not imply decoder experiment eligibility.

## Two permitted public recordings

Two open-access CC-BY-4.0 **held-in minival** assets from [DANDI
000954](https://dandiarchive.org/dandiset/000954) were inspected locally in a
private directory. The Dandiset version is **draft** (not a fixed published
release), so both asset IDs and source hashes pin this observation. Distinct
NWB deidentified session-day labels were `1925-01-01` and `1925-01-08`;
these are not participant calendar dates. The NWB subject ID is present but
serves as a source pseudonym. The partition was explicitly linked to each
asset's documented DANDI location, rather than inferred from the NWB mask.

| Asset ID | SHA-256 | Samples / trial segments | Exposure | Units / zero observed | In-window spikes | Median per-unit rate | Raw mask true / false |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| [`6e45be2c-12b9-43ee-b945-9426f7349eb2`](https://api.dandiarchive.org/api/assets/6e45be2c-12b9-43ee-b945-9426f7349eb2/) | `a95585515b1a12a61603fb3170843351d2fb6551ba6e0456c483570b085e68f3` | 1,503 / 2 | 30.06 s | 176 / 1 | 71,718 | 9.631 Hz | 1,466 / 37 |
| [`1f52eef5-de31-444c-bd30-10ce9b1143dd`](https://api.dandiarchive.org/api/assets/1f52eef5-de31-444c-bd30-10ce9b1143dd/) | `99fb1997035c4f9b0dd0a97be093042f9b7cf10af55cb2a3dc83eea7e35b4d0e` | 1,691 / 2 | 33.82 s | 176 / 1 | 81,852 | 7.806 Hz | 1,629 / 62 |

Both imports completed with `descriptive_eligible=true`. The CLI report was
written only to the private data area. The source hashes matched the manifest
and were unchanged after inspection. Both represented clocks had zero gaps,
and all seven position and velocity features had zero nonfinite samples in
both recordings. These observations do not prove there were no unrepresented
missing acquisitions, and the differing rates do not establish a cause or
decoder suitability.

## Verification limits

Synthetic tests cover same-session distinct files, duplicate content and
paths, identity collisions and contradictions, unknown fields, reordered unit
IDs, feature/unit/binning mismatches, partial failure, zero-exposure rejection,
per-recording numerical parity, deterministic CLI/API output, exclusive
report creation, import isolation and source preservation. No decoder,
normalization, smoothing, anatomical inference, connectivity, GUI or live
acquisition path is implemented.

Focused verification on this branch:

```sh
PYTHONPATH=src python -m pytest -q tests/core/test_bci_inventory.py \
  tests/core/test_bci_h1.py tests/cli/test_load.py tests/test_scene.py --tb=short
python -m ruff check src/dmri_studio/core/bci_inventory.py \
  src/dmri_studio/core/bci.py src/dmri_studio/cli/bci.py tests/core/test_bci_inventory.py
python -m ruff format --check src/dmri_studio/core/bci_inventory.py \
  src/dmri_studio/core/bci.py src/dmri_studio/cli/bci.py tests/core/test_bci_inventory.py
```

The focused suite passed **68 tests**. Ruff checks passed. A separate-process
import-isolation test found neither Qt/VTK nor eager `h5py` after importing the
core inventory and CLI modules. The two public files were inspected read-only;
the versioned private manifest and JSON report remain outside the repository.
