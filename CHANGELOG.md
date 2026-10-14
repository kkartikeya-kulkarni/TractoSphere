# Changelog

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
