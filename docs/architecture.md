# Architecture

TractoSphere is the desktop product name; dmri_studio and dmri-studio remain its
Python package and CLI names. The GUI uses PySide6 Widgets, VTK scientific rendering
and the existing linked slice-view architecture.

- src/dmri_studio/core: Qt-free loaders, acquisition/gradient validation, projects,
  workflow dependency rules, execution adapters, provenance/QC catalogs, scene
  data, atlas regions and volumetric filtering.
- src/dmri_studio/gui: desktop shell, data/project workspaces, reviewed execution,
  saved QC, comparison, preferences, four-view scientific viewer and atlas manager.
- src/dmri_studio/cli: shared headless loading/planning/prepare/execute/QC/project/
  atlas entry points and optional GUI dispatch.
- tests: synthetic input/security/geometry/gradient/execution regressions and GUI
  tests. External scientific commands are mocked where appropriate.
- scripts: optional public-data downloader and native real-data viewer audits.

Long reads/exports/filtering run through workers. VTK actors remain on the GUI
thread. Source images are proxies; no complete 4D MRI payload is loaded into an AI
service. There is no active AI integration.

Preprocessing uses separate local CLI installations, not bundled FSL/MRtrix/ANTs.
Plan approval binds actual commands/evidence; outputs have exclusive directories
and immutable processing histories. Execution success, numerical validity and
scientific review are separate states.

Future features must preserve this separation and the existing design system.

Dependency setup uses core/dependencies.py and shared tool_versions.py. It records
versions/provenance without MRI execution. GUI dependency_setup.py owns one shared
bounded worker for first run and Preferences; cli/dependencies.py exposes doctor.
Setup inventory never replaces the stricter prepare/execute evidence checks.
GUI `ScientificSoftwareSettings` is the single application-level reader/writer for
the legacy QSettings `dependencies/MRtrix3`, `dependencies/FSL` and
`dependencies/ANTs` keys. The shell distributes these defaults to new GUI
workflows without rescanning on navigation. Core compilers remain Qt-free and
receive explicit directories; batch plans pin the chosen directories so resume
uses historical tool identity. A remembered path is display configuration, while
inventory verification and exact scientific plan validation are separate steps.

Platform utilities keep core Qt-free: core/resources.py provides explicit resource
measurement semantics; GUI capture.py composites actual native VTK framebuffer
pixels into workspace exports without changing scene data. Platform-specific
discovery does not enable unsupported adapters. CI builds/tests installed wheels
on explicit runner architectures and never substitutes scientific acceptance.
