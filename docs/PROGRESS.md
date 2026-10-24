# Outstanding Scientific Verification and Release Blockers

The permanent, prioritized task register and EDDY experimental protocol are in
[SCIENTIFIC_VALIDATION_BACKLOG.md](SCIENTIFIC_VALIDATION_BACKLOG.md). It links the
[Phase 0](PHASE0_AUDIT_AND_HANDOFF.md), [Phase 1](PHASE1_IMPLEMENTATION_AND_HANDOFF.md),
[Phase 2](PHASE2_IMPLEMENTATION_AND_HANDOFF.md), [Phase 3](PHASE3_IMPLEMENTATION_AND_HANDOFF.md)
and [Phase 4](PHASE4_IMPLEMENTATION_AND_HANDOFF.md) evidence. This register is the
current list of open scientific verification and release decisions; historical
test counts in the handoffs remain dated evidence, not a current full-suite pass.
It currently records **38 open tasks: 15 P0, 20 P1 and 3 P2**. The six linked
EDDY tasks are a Stage B investigation; no preprocessing default has changed.
Phase 5 Registration UX development began from the consolidated Phase 4/backlog
checkpoint `cdc0c71` on `development/phase5-registration-ux-2026-10-23`; its
implementation and verification record is in
[PHASE5_IMPLEMENTATION_AND_HANDOFF.md](PHASE5_IMPLEMENTATION_AND_HANDOFF.md).
The open validation tasks do not become verified through a guided interface or
synthetic GUI walkthrough. The 38-task register and EDDY workstream remain intact.

# 2026-10-23 — Phase 1 scientific software configuration

The current Phase 1 implementation, tests, unresolved native/sandbox verification and next action are recorded in [PHASE1_IMPLEMENTATION_AND_HANDOFF.md](PHASE1_IMPLEMENTATION_AND_HANDOFF.md). The work is on `development/phase1-scientific-software-2026-10-23`; public `main`, releases and draft PR #3 remain untouched.

# 2026-10-14 — Phase 3 cross-platform compatibility completed locally

Preserved the existing dependency manager, scientific gates and all source data.
Added Windows native .exe root/ANTSPATH discovery, guarded non-native launchers,
read-only Windows process metrics, portable peak-RSS units and UTF-8 atlas/QC/
workspace text. Fixed Linux workspace screenshot capture of native VTK children;
actual framebuffer composition is regression-tested and preserves scene geometry.

Final: public candidate macOS 762 passed / 2 optional skips (174.16 s); original
checkout 803 passed / no skips (173.44 s); clean Ubuntu 24.04 ARM64 installed wheel
749 passed / 15 absent-scientific-tool skips (150.71 s). Source/wheel pip checks,
packaged assets, Ruff, actionlint and no-MRI-array distribution checks passed.
Real CC0 TractoInferno T1/65-volume DWI/TCK/TRK/tissue atlas/ROI filtering passed
on macOS and Ubuntu X11/Mesa VM; originals retained identical SHA-256. Generated
small geometry smoke separately covers navigation, workspace restore, captures,
setup guide and themes. Linux full filtering required a 300-second VM harness wait;
no science thresholds or algorithms changed. Failed harness/capture/timeout
attempts retained in phase evidence. Native Finder LaunchServices audit wrapper
completed with minimal PATH and exit 0; no standalone distribution claim.

CI prepared for macOS ARM/Intel, Ubuntu x86-64 Python 3.12/3.13 and Windows Server
2022; jobs deliberately gated off while private, so no GitHub CI execution claimed.
Windows/Intel Mac/Linux x86-64 runtime and physical Linux GPU/Wayland remain pending.
Windows scientific adapters/WSL bridge remain unsupported. Independent clean Mac
and prior historical-security publication gates unchanged. No history cleanup,
publication, costly MRI processing, tool-installation changes or next phase.

See cross_platform_validation_phase3.md, platforms.md and updated installation,
dependency setup, limitations/architecture/validation docs. Public source changes
backported to the original checkout without importing private history into release.
Evidence is retained outside the public source under the dated phase3-* artifacts.
The bounded Linux VM is stopped after all tests/evidence collection.

---

# Public alpha v0.1.0 preparation

The public tree begins from an audited application snapshot with private study
scripts/reports/screenshots and the experimental AI history excluded. Research
originals and full development history remain local. The app's core preprocessing,
QC, viewer and atlas implementations are preserved. Only version and alpha notices
changed in runtime source for release preparation.

Own-source license: MIT. Font/icon notices retained. External FSL/MRtrix/ANTs/HCP
software and atlas/MRI arrays are not bundled. No active AI services or credentials.

Prepared: installation/quick-start/features/atlas/architecture/limitations/validation
and viewer comparison docs; README, changelog, contribution/security/citation;
researcher issue forms; public CI configuration; wheel/source distribution and
macOS installation helper. Public screenshots use permitted CC0 TractoInferno data.

Verification: original checkout 751 passed; clean public source 710 passed, two
optional external integration skips; real native alpha and legacy ROI/Tracts audits;
full-history and curated-tree credential scanning. See validation.md.

Publication must remain gated until prior GitHub historical research content is
inaccessible and the distribution/install checks pass. A prepared tag, commit or
private draft does not establish a completed public release.
