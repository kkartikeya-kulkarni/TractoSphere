# Bundled assets

- Inter 4.1: https://github.com/rsms/inter/releases/tag/v4.1 — static OpenType Regular, Medium, SemiBold; SIL Open Font License, see fonts/Inter-LICENSE.txt.
- JetBrains Mono 2.304: https://github.com/JetBrains/JetBrainsMono/releases/tag/v2.304 — static TrueType Regular, Medium; SIL Open Font License, see fonts/JetBrainsMono-OFL.txt.
- Lucide 0.468.0: https://github.com/lucide-icons/lucide/tree/0.468.0 — selected unmodified SVGs; upstream ISC/MIT notices retained in icons/LICENSE. Stroke colors are replaced in memory for display only.

No asset is downloaded at application startup.

## TractoSphere redesign

Additional Lucide icons use the same pinned 0.468.0 source and license;
`icons/tractosphere-icons.json` records source URLs and SHA-256 hashes.
`control-chevron-dark.svg` and `control-chevron-light.svg` are stroke-color-only
adaptations of Lucide's chevron-down for Qt stylesheet subcontrols.
`branding/tractosphere.svg` and `branding/tractosphere-app.svg` are original
project vector artwork. The startup scene is procedural project artwork; it is
illustrative and does not contain MRI data or reconstructed tractography.
