# Third-party dependencies, assets and scientific tools

TractoSphere's own source is MIT licensed. That license does not relicense its
libraries, fonts, icons, external scientific tools or datasets. No FSL, MRtrix3,
ANTs, DIPY, DSI Studio or HCP executables, models, atlases or MRI arrays are bundled
in the release wheel or source archive.

## Assets actually distributed

| Asset | License | Included notice |
|---|---|---|
| Lucide SVG icons, including Feather-derived portions | ISC; Feather portions MIT | src/dmri_studio/gui/assets/icons/LICENSE |
| Inter fonts | SIL Open Font License 1.1 | src/dmri_studio/gui/assets/fonts/Inter-LICENSE.txt |
| JetBrains Mono fonts | SIL Open Font License 1.1 | src/dmri_studio/gui/assets/fonts/JetBrainsMono-OFL.txt |
| Viridis 256-color lookup data (`core/_viridis.py`) | CC0-1.0 / public domain | Attribution/source retained in the module; no upstream executable code copied |
| TractoSphere logo and application SVG/code assets | MIT | LICENSE |

Fonts are distributed unmodified with their notices. Their reserved font names
are not transferred or relicensed. SVG icon attribution is retained.

## Python dependencies installed by the user

| Dependency | License family | Actual use |
|---|---|---|
| NumPy | BSD-3-Clause | Imported numerical arrays and checks |
| psutil | BSD-3-Clause | Imported read-only Windows process metrics and cross-platform peak-memory reporting |
| NiBabel | MIT | Imported image/tract headers, proxies and formats |
| Pillow | MIT-CMU | Core registration QC image generation and portable reports |
| SciPy | BSD-3-Clause | Imported sampling, morphology and numerical operations |
| PySide6 / Shiboken / Qt | LGPL-3.0 / GPL / commercial, depending on component | Imported Qt Widgets, SVG and desktop bindings |
| VTK | BSD-3-Clause; upstream third-party component notices also apply | Imported Qt-compatible scientific rendering |
| Matplotlib | Matplotlib license, PSF-compatible BSD style | Imported plots/QC figures |
| indexed_gzip | zlib | Imported acceleration of compressed NIfTI reads |

These dependencies are **not frozen into TractoSphere's wheel**. pip obtains
upstream distributions with their own notices. Source installation permits
replacement/reinstallation of libraries; it does not disable LGPL rights.
No proprietary Qt license is required for the documented open-source installation
when the applicable open-source obligations are met. Review upstream terms before
creating a frozen or otherwise redistributed binary distribution.

## External tools: separately installed local command-line software

| Tool | License / terms | Integration and distribution boundary |
|---|---|---|
| FSL | Most components: FSL noncommercial license; component exceptions exist | Local TOPUP, EDDY, BET/FAST/FLIRT/BBR and combination commands. Not bundled; commercial use needs appropriate permission. |
| MRtrix3 | MPL-2.0 | Local conversion, MP-PCA, mrdegibbs, dwi2mask and dwibiascorrect commands. Not bundled. |
| ANTs / N4 | Apache-2.0 | Locally installed N4 through the MRtrix wrapper. Not bundled. |
| HCP pipelines helper | BSD-style 3-clause at pinned faec8f3ec23697018a6cdc0824569d4082e6201e, with component exceptions | User-supplied verified average_bvecs helper for optional matched combination. Source is not bundled; retain its upstream copyright/license if redistributing it. |
| DIPY | BSD-3-Clause | Researched tool; no active DIPY reconstruction provider in this alpha. Not a required dependency or bundled implementation. |
| DSI Studio | Component-dependent upstream terms | Viewer behavior was researched. DSI Studio code/executables are not bundled; TinyTrack conversion is disabled after failed spatial verification. |

The application's independent checks of the documented HCP combination recipe
are in core/pairing.py and core/combination.py. The adapter calls the unmodified,
checksum-pinned helper supplied by the user; it does not claim to distribute or
license the entire HCP pipeline.

## License sources reviewed for this alpha

- FSL: https://fsl.fmrib.ox.ac.uk/fsl/docs/license.html
- MRtrix3: https://github.com/MRtrix3/mrtrix3/blob/master/LICENCE.txt
- ANTs: https://github.com/ANTsX/ANTs/blob/main/COPYING.txt
- HCP pinned license: https://github.com/Washington-University/HCPpipelines/blob/faec8f3ec23697018a6cdc0824569d4082e6201e/LICENSE.md
- Qt for Python: https://doc.qt.io/qtforpython-6/licenses.html
- VTK: https://github.com/Kitware/VTK/blob/master/Copyright.txt
- DIPY: https://github.com/dipy/dipy/blob/master/LICENSE
- NumPy: https://github.com/numpy/numpy/blob/main/LICENSE.txt
- NiBabel: https://github.com/nipy/nibabel/blob/master/COPYING
- Pillow: https://github.com/python-pillow/Pillow/blob/main/LICENSE
- SciPy: https://github.com/scipy/scipy/blob/main/LICENSE.txt
- Matplotlib: https://matplotlib.org/stable/project/license.html
- indexed_gzip: https://github.com/pauldmccarthy/indexed_gzip/blob/master/LICENSE

## Dataset and screenshot provenance

Public screenshots use TractoInferno/OpenNeuro ds003900 sub-1006, licensed CC0:
https://openneuro.org/datasets/ds003900. Tissue regions shown are derived from
that subject's supplied nonoverlapping masks and labeled as such, not a cortical
parcellation or registered MNI atlas. Real AF_L/other bundle files come from the
same subject. No MRI or tractogram arrays are distributed with the application.
FSL JHU atlases were used for local validation only; their arrays and figures are
excluded from the public release. Historical HCP/HCD images and private research
reports are excluded. See docs/validation.md for the tested scope.

## Phase 4 source and asset verification

The release content guard uses `release-assets.json` to bind reviewed font, icon
and screenshot bytes to their sources/licenses. Fonts were byte-compared with
Inter 4.1 / JetBrains Mono 2.304 publisher archives. Unmodified Lucide SVGs were
byte-compared with 0.468.0; two control chevrons only change stroke color, and
branding SVGs are original project artwork. Viridis lookup values exactly match
the attributed BIDS/colormap CC0 table. These exceptions retain their own terms;
the project MIT license does not relicense third-party assets.

Screenshot source: TractoInferno ds003900 v1.1.1 `dataset_description.json` declares
CC0 and requests citation of Poulin, Theaud, Jodoin and Descoteaux's TractoInferno
work: https://doi.org/10.1101/2021.11.29.470422. Screenshots show the permitted
public subject or procedural UI illustration. MRI/tract arrays are not included.
Public availability of any other dataset is not redistribution permission.

Qt bindings/modules used here are Core, Gui, Widgets, SVG/SVGWidgets; QtTest is
used in tests. GPL-only add-on modules are not used by application code. Upstream
Qt/PySide wheels and their license files remain separately installed and replaceable.
A future frozen app requires a new component-level redistribution audit. The
current installer fetches pip dependencies; it does not bundle their executables.
