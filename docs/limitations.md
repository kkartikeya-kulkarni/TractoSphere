# Known limitations and scientific status

PUBLIC ALPHA · RESEARCH USE ONLY · NOT FOR CLINICAL USE · SCIENTIFIC VALIDATION ONGOING

- Native GUI verification is on macOS Apple Silicon. Linux/Windows are experimental;
  CI results, when available, are software checks rather than scientific validation.
- The v0.2.0-alpha package implements bounded reconstruction/model fitting, new
  tractography generation, connectome analysis and tensor/FOD glyph workflows.
  Their scientific validation gaps are listed below and in the 38-item backlog.
  Complete DSI Studio feature parity is not claimed.
- No automatic multi-run/joint multi-shell assembly or interrupted EDDY recovery.
  Multi-shell handling requires compatible, independently verified acquisition setup.
- TinyTrack (.tt.gz) conversion remains disabled after failed spatial round-trip checks.
  General MIF/surface/DSI format interoperability beyond documented adapters is future work.
- Atlas loading does not estimate registration. Verified RASmm affine contracts are
  supported; arbitrary FLIRT/ANTs matrices and nonlinear warps are not ingested.
- MRI/tract coordinate agreement, FOV overlap, plausible surfaces and nonzero ROI hits
  do not establish subject identity or anatomical correctness.
- A binary region or brain-mask surface is not a cortical reconstruction. Surface
  padding closes image-edge geometry without modifying the scientific mask.
- Standard mrdegibbs is not complete PF-specific correction. Filtering/reconstruction
  history can limit interpretation. MP-PCA must precede interpolation.
- Raw metadata omissions and contradictions can block correction. The app does not
  fill in readout time, phase encoding or slice groups from a similar dataset.
- EDDY successful exit alone does not establish valid CNR/predictions. Diagnostically
  invalid results must remain investigation outputs, even when images look plausible.
- N4 smoothness/convergence, mask volume counts and low motion do not establish
  scientific acceptance. Mask coverage and alignment require anatomical review.
- N4/registration/combination depend on bounded engine producer and review contracts;
  arbitrary historical run formats may need explicit adapters.
- ACT, GM-WM seed generation and ACT SIFT2 require an accepted 5TT tissue QC
  review bound to the exact five-tissue image. T1-to-DWI alignment approval and
  successful `5ttcheck` do not accept tissue boundaries. A reported GM-WM
  boundary problem must be resolved by qualified review before these methods run.
- The current 5TT GUI path requires T1 and diffusion reference on one physical
  voxel grid. Resampling a native high-resolution T1 to a coarser diffusion grid
  before segmentation can lose anatomical detail. A validated native-resolution
  segmentation and transform-preserving ACT path is still needed; the same-grid
  route should not be treated as publication-ready from technical checks alone.
- Large tractograms are indexed with bounded rendering, but complex editing/filtering
  can be slow. Ten-million-record indexing stress used repeated records, not evidence
  of rendering ten million distinct biological streamlines.
- Many simultaneously visible atlas surfaces or large probability atlases can use
  substantial RAM. No exhaustive workstation/GPU/platform benchmark is available.
- Workspace source references must remain accessible. No cloud-storage migration or
  verified remote-data browser is implemented. Intensive processing must use local
  or suitable high-performance storage rather than streaming Google Drive files.
- Undo histories are bounded and not preserved as full histories across restart.
- Original files remain unchanged; no automated volume removal, mask union/repair,
  gradient flipping or scientific acceptance is authorized by QC flags alone.

- Scientific software installation is guided manual/browser handoff, not a
  one-click installer. FSL can require Terminal/IT assistance. Unsigned macOS
  binaries can require upstream security approval; the app never bypasses it.
- Windows-to-WSL processing is unavailable. Dependency setup does not turn a WSL
  Linux installation into native Windows execution. Independent Mac installation
  and platform acceptance remain pending; successful installation is not scientific QC.

- Cross-platform evidence is bounded: macOS ARM64 is locally verified; Ubuntu
  ARM64 has VM/X11 software-rendering evidence. Hosted CI passes on macOS ARM64,
  macOS Intel, Ubuntu x86-64 (Python 3.12/3.13) and Windows Server 2022. Windows CI
  uses Mesa software rendering; physical Windows 10/11, native Wayland,
  independent clean Mac and physical Linux GPU testing remain pending.
  See [platform scope](platforms.md).
- Workspace/export publication requires hard-link-capable local storage; FAT/exFAT
  or restricted network filesystems may reject it. There is no overwrite fallback.
- Windows generic subprocess cancellation covers the direct executable only, not
  arbitrary child trees or WSL; Windows scientific processing remains unsupported.
