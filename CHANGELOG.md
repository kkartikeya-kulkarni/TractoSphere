# Changelog

## v0.2.1-alpha — 2026-10-25

Python package version: 0.2.1a0. Adds an in-toolbox dataset QC document built
from the processing attempts actually saved for one participant. The previous
v0.2.0-alpha release remains available for reproducibility.

### Added

- **Quality Control → Create dataset QC document…** combines verified producer
  catalogs into one offline HTML review document. It preserves execution,
  technical, automated QC and scientific review as separate states.
- Include recorded findings, representative figures and graphs, verified native
  before/after previews where available, measurements, histories, short logs,
  exact-bound local review declarations and source hashes. Pending or skipped
  plan stages are listed separately from performed steps. Conflicting explicit
  subject IDs and changed artifacts block export.
- Add the matching `dmri-studio combined-qc` command and an illustrated Read the
  Docs procedure. The exporter is general to saved toolbox attempts, with no
  study-specific script or bundled participant data.

### Scope

The combined document is a bounded overview. Producer reports remain the source
for complete slice coverage and large evidence. This feature does not grant
scientific acceptance or authenticate a reviewer.

## v0.2.0-alpha — 2026-10-25

Python package version: 0.2.0a0. Packages the scientific toolbox previously
available on `main` and its illustrated Read the Docs guide. The v0.1.1-alpha
downloads remain available for reproducibility.

### Added

- Shared scientific input inventory, eligible workflow planning and exact command
  approval across preprocessing, diffusion models, registration and connectivity.
- Guided tensor maps, CSD/FOD, anatomy and registration, streamline generation,
  filtering, density, SIFT2, connectome matrices, batch processing and saved QC.
- Review an existing registered atlas against its exact native reference without
  repeating registration, with provenance-bound QC and separate human review.
- Public real-data GUI guidance for import through connectome, including files,
  screenshots, operation-specific prerequisites and limitations.

### Fixed

- Connect imported files and verified derivatives to processing eligibility and
  actionable review evidence; preserve source data and exact association gates.
- Improve tractogram indexing, bounded display and ROI filtering feedback.
- Resolve GUI state, installer/version and cross-platform test issues found during
  the public scientific-toolbox audit.

### Limitations

Research alpha; successful execution is not scientific acceptance. ACT/GMWMI
requires a separately accepted exact-5TT tissue review. Scientific workflows are
primarily tested on Apple Silicon macOS; independent platform and large-run
validation remains open. The package does not bundle MRtrix3, FSL or ANTs and is
not a notarized standalone Mac application. See docs/limitations.md.

## v0.1.1-alpha — 2026-10-14

Python package version: 0.1.1a0. Updated downloads include the Windows fixes and
improved installation instructions. The original v0.1.0-alpha artifacts are retained.

### Fixed

- Keep BIDS calibration URI comparisons in portable slash form on Windows.
- Use NiBabel's standard gzip reader on Windows after isolating a native
  indexed_gzip Unicode-filename crash; preserve optional acceleration elsewhere.

- Preserve approved SVG/font/icon bytes during Windows Git checkout; exact-hash
  release auditing remains enabled and rejects modified assets.
- Verify linked-view expansion through workspace/layout geometry and persisted
  window sizes within native desktop bounds, including virtual CI displays.
- Add Windows checkout, SVG tampering and disguised-executable regression checks.
- Preserve original ZIP member names when auditing unsafe archive paths on Windows.
- Make source-freshness and simulated-platform tests deterministic across platforms.
- Exercise real Qt/VTK rendering on Windows hosted CI with checksum-pinned Mesa
  software OpenGL; Mesa is CI-only and is not bundled with the application.
- Clarify Mac installation and exact scientific-tool directory selection.

## v0.1.0-alpha — 2026-10-14

First public-alpha candidate under the TractoSphere product name. Python package
version: 0.1.0a0. Research use only; scientific validation ongoing.

### Added

- Guided platform-specific scientific software setup, consented pinned official
  browser downloads, read-only archive digest verification and tested family configuration.

- Optional first-run scientific dependency setup with exact paths/component versions,
  unchanged adapter gates, explicit app-owned defaults and read-only doctor CLI.

- Linked four-view NIfTI workspace with interactive VTK anatomy and real TCK/TRK.
- Tract coloring, rendering/camera controls, selection/editing and derivative exports.
- Binary ROI editing, surfaces, named discrete atlases and thresholded probability maps.
- Grouped atlas regions with colors, volume, visibility and Include/Exclude roles.
- Volumetric ROI filtering, selected-group export and metadata-preserving workspaces.
- Metadata/history-gated preprocessing, external-tool previews and saved QC/provenance.
- BIDS acquisition discovery, project snapshots and bounded verified native continuation.
- Public installation/quick-start guides, notices, citation and researcher feedback templates.

### Fixed before alpha preparation

- Atlas images now register individual regions instead of collapsing all nonzero labels.
- ROI/Tracts controls report prerequisites/errors and preserve source/edit correspondence.
- Known atlas/native-space mismatches are rejected rather than silently overlaid.
- Workspace restoration preserves saved hidden-region visibility.

### Removed

- Active AI assistant, ChatGPT login, OAuth controls and AI startup dependencies.

### Limitations

See docs/limitations.md. No new tractography generation, diffusion-model fitting,
nonlinear atlas registration, automatic joint assembly or interrupted EDDY recovery.
Native macOS source/wheel installation is the verification target; a notarized
standalone application is not included. This changelog describes the prepared
candidate and does not itself attest that GitHub publication has occurred.
