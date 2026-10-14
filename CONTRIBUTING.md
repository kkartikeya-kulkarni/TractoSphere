# Contributing

TractoSphere welcomes reproducible bug reports, installation feedback, scientific
review and small focused contributions. This is research software in public alpha.

Before filing, check docs/limitations.md and existing issues. Use the relevant
bug/installation/scientific-QC/feature template. Include OS/CPU/RAM, application
version, dependency versions, sanitized error text and exact steps. A permitted
public dataset reference or a small synthetic reproducer is preferable to MRI uploads.
Never submit patient identifiers, restricted scans, private credentials or unpublished
study materials. Crop/redact identifying information before sharing screenshots.

## Development

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install '.[gui-dev]'
python -m pytest
python -m ruff check src tests
```

Native GUI tests require a real desktop session. External processing is mocked
in normal tests; do not launch hours-long EDDY jobs to test a GUI change. Optional
real-data audits require independently downloaded permitted data and a NEW output
directory. Set TRACTOSPHERE_REFERENCE_DIR to the subject directory for the scripts.

Keep changes in a branch and open a focused pull request. Describe the behavior,
why it changes, tests performed and remaining scientific limitations. Source files
remain immutable; never weaken verification to make a pipeline appear successful.
No arbitrary shell execution, implicit gradient/geometry changes or placeholder
scientific controls. Preserve core/GUI/CLI separation and API parity.

Contributions to TractoSphere's own source are provided under the MIT license.
Do not introduce third-party source/assets without provenance, compatible terms
and required notices. Run a secret scanner before pushing. Security findings use
SECURITY.md rather than a public issue containing exploit credentials or data.

Submit feedback at https://github.com/kkartikeya-kulkarni/TractoSphere/issues/new/choose
and contributions through https://github.com/kkartikeya-kulkarni/TractoSphere/pulls.
