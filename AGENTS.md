# TractoSphere contributor instructions

Read README.md, docs/architecture.md, docs/features.md and docs/limitations.md before changes.
TractoSphere is a public alpha for research only. Scientific validation is ongoing.

Keep core/ Qt-free, use validated application services in GUI/ and expose CLI/API parity.
Preserve source images, geometry, gradients, volume associations and processing provenance.
Never infer missing acquisition metadata or scientific acceptance from execution success.
Long operations run asynchronously. Do not introduce simulated progress or placeholder controls.
Use the existing design tokens, SVG icons and viewer architecture.
Run relevant numerical, GUI and security tests. Use public permitted data and synthetic fixtures;
never commit real MRI arrays, credentials, private study records or local user paths.
Review documentation claims against implemented behavior. Document limitations and tests honestly.
