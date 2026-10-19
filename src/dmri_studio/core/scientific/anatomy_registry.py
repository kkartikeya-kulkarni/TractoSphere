"""Explicit anatomical operations using the shared artifact executor."""


def anatomy_algorithms(algorithm, parameter):
    """Declare native 5TT steps separately from streamline generation."""
    rows = []
    for key, name, command, required, optional, params, outputs in (
        (
            "5ttgen.fsl",
            "FSL five-tissue segmentation",
            "5ttgen",
            {"anatomy": "anatomy", "reference": "anatomy"},
            {"mask": "mask"},
            {
                "brain_extraction": parameter("choice", choices=("mask", "premasked", "automatic")),
                "amygdala_hippocampus_sgm": parameter("choice", choices=("on", "off")),
            },
            {"five_tt": "5tt"},
        ),
        (
            "5ttcheck",
            "Verify existing five-tissue image",
            "5ttcheck",
            {"five_tt": "5tt", "reference": "anatomy"},
            {},
            {},
            {},
        ),
        (
            "5tt2vis",
            "Inspect five tissue compartments",
            "5tt2vis",
            {"five_tt": "5tt", "reference": "anatomy"},
            {},
            {},
            {"visualization": "tissue_scalar"},
        ),
        (
            "5tt2gmwmi",
            "GM–WM interface seed image",
            "5tt2gmwmi",
            {"five_tt": "5tt", "reference": "anatomy"},
            {},
            {},
            {"gmwmi": "gmwmi"},
        ),
    ):
        rows.append(
            algorithm(
                key,
                name,
                "tractography",
                command,
                required,
                optional,
                ("existing_anatomy", "existing_5tt"),
                params,
                outputs,
                (
                    "exact five-channel partial-volume order and finite fractions",
                    "native 5ttcheck plus independent fraction and geometry checks",
                    "hash-bound anatomical alignment evidence; expert review remains separate",
                ),
                implementation="implemented",
                validated_versions=("3.0.5",),
                adapter="dmri_studio.core.scientific.anatomy.AnatomyAdapter",
            )
        )
    return rows
