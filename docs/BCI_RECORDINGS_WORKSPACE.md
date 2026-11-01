# Experimental BCI recording workspaces

**Inspect Recording** opens a user-selected modern FALCON H1 `.nwb` recording
through the same `inspect_falcon_h1` API used by `bci inspect-h1`. Install the
`bci` extra in the GUI environment (`pip install '.[gui,bci]'` for a checkout).
No decoder-reference dependencies are required for these pages.

The summary shows samples, analyzed exposure, unit/trial-segment counts, raw
mask counts with their sample denominator, and the source SHA-256 calculated
during inspection. Expand the read-only measurements tree for per-unit counts
and exposure-based rates, per-trial summaries, represented timing gaps,
nonfinite targets split by feature and raw mask, and storage-unit declarations.
Unknown units remain unknown. Long lists are grouped into blocks of 100; all
rows remain inspectable. This is a descriptive inspector, not a signal plotter.

**Compare Recordings** opens an existing versioned inventory manifest. The
selected manifest explicitly lists recording paths and expected hashes: this
page reads those listed sources, unlike the passive BCI Results page. The
manifest must already exist; no graphical manifest builder is included. The
comparison table shows each entry's status, first finding,
sample/exposure/unit/spike counts and raw-mask denominator, including entries with available measurements that
later fail identity or collision checks. Expand `recordings` for every error,
identity declaration and evidence, and `comparability` for compatibility reasons
and profiles. A partial run remains incomplete; descriptive compatibility does
not prove independent observations or cross-session neuron identity.

Both pages run reads in background threads, disable repeat opens during a read,
clear old results before a new attempt, and defer application close until reads
finish. There is no cancellation or progress percentage; a long read must finish.
They do not write source files, export reports, fit models, alter masks, repair
data or assign quality/acceptance scores. Incidental local paths are omitted
from the display, including diagnostic text; unknown metadata is not inferred.

**BCI Results** keeps its existing passive saved-report behavior. No MRI
interpretation, acquisition hardware integration, arbitrary NWB support, EEG/LFP
processing or anatomical inference is added. FALCON H1 schema restrictions and
constant-clock missing-acquisition limitations remain in force.

Validation at the GUI patch checkpoint used synthetic HDF5/NWB fixtures. The
BCI core, CLI, import-isolation and GUI checks passed (101 tests offscreen);
the focused GUI suite also passed with the native Cocoa Qt platform (9 tests).
In the native macOS application, the NWB picker opened and cancelled, the
JSON picker selected a manifest, and a synthetic recording and complete
two-recording manifest loaded.
A manifest with one missing recording remained explicitly incomplete with
the failed entry visible. The recording sources' SHA-256 values were unchanged
after these reads. The pages were inspected in light and dark themes and at a
smaller window size. These checks do not establish usability with long public
recordings or scientific acceptance. Active-worker close behavior has automated
coverage, but was not directly observed during the native manual smoke check.
