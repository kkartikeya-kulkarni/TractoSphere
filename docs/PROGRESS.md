# Outstanding Scientific Verification and Release Blockers

## 2026-10-24 — bounded real-data import and selection audit

The [real-data functional audit](REAL_DATA_FUNCTIONAL_AUDIT_2026-10-24.md)
records read-only TractoSphere imports from bounded local research-data roots,
offscreen propagation across scientific workspaces, a real DWI Project →
Preprocessing → DTI/CSD path, non-BIDS custom discovery, and verified historical
Results/QC reopening. Local repairs add declaration review for imported draft
inputs, supported FA/MD/mean-b0 import choices, accurate Registration spatial
eligibility, and a T1-only 5TT generation source selector. Native Cocoa
interaction and broader scientific plan acceptance remain unverified at this
checkpoint. The permanent 38-task backlog and Phase 7 EDDY work are unchanged.

## 2026-10-24 — Registration anatomical declaration review

The [post-audit handoff](USABILITY_AUDIT_FIXES_2026-10-24.md#registration-anatomical-import-eligibility-follow-up) records a fix for imported T1 anatomy that was hidden from the EPI-to-T1 Reference selector. Imported images are now visible apart from role eligibility, with specific missing-declaration reasons and a path-preserving review/reinspection action. HCP ACPC-aligned subject T1 structural space is explained without filename inference or automatic MNI/raw classification. The synthetic Registration import-to-selection flow passed **2 focused offscreen tests**; the selected Registration/import/core run passed **57 tests, 10 deselected**. Native Cocoa picker/layout checks and real HCP association/registration remain open. Scientific planning, approval, source hashing and the 38-task validation backlog are unchanged.

## 2026-10-24 — Cross-workspace scientific input recovery

The [post-audit usability handoff](USABILITY_AUDIT_FIXES_2026-10-24.md#cross-workspace-input-recovery-follow-up) records contextual import actions and missing-input explanations across Connectivity, Anatomy/5TT, SIFT2, DTI, CSD/FOD, Tractography, Preprocessing and the advanced task editor; Registration and Batch keep their existing guided routes. **50 selected offscreen GUI tests and 122 selected core/CLI tests passed**. Two real-process cancellation tests remain unverified in the restricted command environment, and current-code native Cocoa pickers, layout and Viewer behavior await the user's normal-Mac check. Scientific validation, exact plan approval and the separate 38-task backlog are unchanged. Continued Phase 7 work must include the reviewed post-audit fixes rather than start again from `8102df1`.

## 2026-10-24 — Post-audit interaction and layout verification

The [usability audit follow-up](USABILITY_AUDIT_FIXES_2026-10-24.md) records click-driven synthetic imports into Registration, DTI and CSD, explicit non-BIDS Batch file-role mapping through plan review, and a 960×640 offscreen layout capture that exposed compressed sidebar headings. The sidebar now scrolls; DTI again selects an imported DWI by default. **129 selected offscreen GUI/core/CLI tests passed** after these changes. The prior **46 Cocoa passes at `62883c0` predate this follow-up**; actual native pickers, visual layout/focus, and scientific execution remain unverified. A fresh synthetic fixture generator and exact normal-Mac walkthrough are in the follow-up. The 38 scientific-validation tasks and processing approval gates are unchanged. Continued Phase 7 must start from a reviewed integration checkpoint that includes these usability fixes, rather than the older Phase 6 commit `8102df1`.

A direct Cocoa launch check from this Codex command environment at `0b7b1ac` found **zero available Qt screens**. TractoSphere and a minimal Qt widget both failed before a window appeared; VTK was not involved in the minimal failure. Computer Use could not select TractoSphere and barred Terminal; unsandboxed execution approval was automatically unavailable under the active policy. Details and macOS crash-report frames are in the follow-up handoff. This is an execution-environment limit, not a new native GUI pass or a demonstrated TractoSphere defect.

## 2026-10-24 — Screenshot-led usability audit corrections

The local [usability audit correction record](USABILITY_AUDIT_FIXES_2026-10-24.md)
tracks U1–U9: fresh Preprocessing access, scoped scientific imports and
candidate explanations, duplicate identity-field removal, guided Batch source
instructions/errors, Registration layout inset and Reconstruction guidance.
Synthetic offscreen GUI/core/CLI results and the restricted Cocoa/VTK failures
are recorded there. At `62883c0`, the user's normal Mac Terminal ran the
selected native Cocoa audit/Registration/Batch suite: **46 passed in 57.28 s,
exit 0**. Visual layout, chooser and researcher-led review remain pending; the
permanent 38-task scientific-validation backlog and all approval gates remain
unchanged.

## 2026-10-24 — Phase 7 EDDY evidence audit and experiment design

On `development/phase7-eddy-validation` from Phase 6 commit `8102df1`,
[the Phase 7 scientific record](PHASE7_EDDY_SCIENTIFIC_VALIDATION.md) reconciles
the original b≈1000 EDDY anomaly with a previously completed, restricted local
research investigation. It records the historical exact-command provenance,
earlier no-denoising and other failed controls, the confirmed residual-denominator
explanation for nonfinite CNR, gaps in currently available large output arrays,
primary literature, and a controlled next-experiment protocol. No Phase 7 MRI
experiment, correction-code change, QC-policy change or preprocessing-order
change occurred. Existing focused synthetic core tests passed (7/7). The
[38-task backlog](SCIENTIFIC_VALIDATION_BACKLOG.md) remains open; `SV-EDDY01`
and `SV-EDDY02` are In progress only for audit/protocol, pending authenticated
array restoration or an authorized exact replay and matched scientific review.

## 2026-10-23 — Phase 6 scientific workspace UX in verification

The task-focused scientific input inventory, discoverable preprocessing entry,
reconstruction/anatomy/SIFT2/connectivity guidance and explicit recent
project/run navigation are implemented on
`development/phase6-scientific-workspaces-ux-2026-10-23` from `38f2f78`.
Exact planners, approval, scientific gates, source identities and workers remain
the execution path. [The Phase 6 handoff](PHASE6_IMPLEMENTATION_AND_HANDOFF.md)
records local tests and measured offscreen performance. At application/test code
`03928a2`, the user's normal Mac Terminal returned **6 targeted Cocoa/VTK tests
passed in 24.41 s, exit 0** for synthetic inputs, five-tissue QC, DTI, CSD,
SIFT2 and Connectivity. At the same application code commit, two normal-Terminal
Cocoa benchmarks exited 0: script-to-interactive **909.261 ms empty** and
**904.160 ms with a small generated NIfTI**; the handoff gives navigation,
Batch and measurement limits. A seventh targeted native Cocoa test at later
documentation HEAD `c22697c` passed in **2.09 s** for actual synthetic MRtrix
conversion, correct project acquisition binding and verified Results reopening;
the application code was unchanged. True cold start, real-data Viewer performance and
researcher usability review remain open; the interface work is
**targeted-native-verified, not scientifically accepted**. The permanent
38-task scientific backlog remains open, and no EDDY preprocessing order or scientific acceptance
policy changed.

The permanent, prioritized task register and EDDY experimental protocol are in
[SCIENTIFIC_VALIDATION_BACKLOG.md](SCIENTIFIC_VALIDATION_BACKLOG.md). It links the
[Phase 0](PHASE0_AUDIT_AND_HANDOFF.md), [Phase 1](PHASE1_IMPLEMENTATION_AND_HANDOFF.md),
[Phase 2](PHASE2_IMPLEMENTATION_AND_HANDOFF.md), [Phase 3](PHASE3_IMPLEMENTATION_AND_HANDOFF.md),
[Phase 4](PHASE4_IMPLEMENTATION_AND_HANDOFF.md) and
[Phase 5](PHASE5_IMPLEMENTATION_AND_HANDOFF.md) evidence. This register is the
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

# 2026-10-23 — Phase 5 guided Registration checkpoint

The task-first Registration interface, exact typed planning/approval, verified
Viewer inspection, categorical atlas display and advanced transform controls are
recorded in [the Phase 5 handoff](PHASE5_IMPLEMENTATION_AND_HANDOFF.md). At code
commit `5c30ded`, a normal Mac Terminal returned **13 native Cocoa/BatchRunner
tests passed** and measured Registration first/return navigation at **51.971 /
34.553 ms** without loaded data. At `4e5e4c9`, an expanded native test passed
for an actual two-subject BatchRunner QC result opened through guided results,
including acquisition identity and changed-report rejection. The comparable
offscreen navigation median increased from 32.145/33.367 to 58.175/50.819 ms;
that tradeoff is tracked under `SV-PERF05`. Real anatomical validation,
researcher usability review, wider Batch product handoff and true cold start
remain open. No scientific backlog task was closed by this UX checkpoint.

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
