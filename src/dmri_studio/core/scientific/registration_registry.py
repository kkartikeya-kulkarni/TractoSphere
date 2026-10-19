"""Native registration contracts shared by individual and batch processing."""


def registration_algorithms(algorithm, parameter):
    rows = []
    operations = [
        (
            "reference",
            "Derive preprocessed low-b reference",
            "mrconvert",
            {"dwi": "dwi"},
            {},
            {
                "b0_threshold": parameter("number", minimum=0, maximum=200),
                "selection": parameter("choice", choices=("mean_low_b", "first_low_b")),
            },
            {"reference": "anatomy"},
        ),
        (
            "qc",
            "Registered atlas QC on preprocessed diffusion",
            "internal_qc",
            {"atlas": "atlas_labels", "reference": "anatomy"},
            {},
            {},
            {},
        ),
        (
            "flirt",
            "Rigid / affine registration",
            "flirt",
            {"moving": "anatomy", "reference": "anatomy"},
            {"initial": "spatial_transform"},
            {
                "model": parameter("choice", choices=("rigid", "affine")),
                "cost": parameter(
                    "choice", choices=("mutualinfo", "normmi", "corratio", "normcorr", "leastsq")
                ),
            },
            {"registered": "anatomy", "transform": "spatial_transform"},
        ),
        (
            "epi_reg",
            "EPI to T1 boundary registration",
            "epi_reg",
            {"moving": "anatomy", "reference": "anatomy", "t1_brain": "anatomy", "wmseg": "mask"},
            {},
            {},
            {"registered": "anatomy", "transform": "spatial_transform"},
        ),
        (
            "fnirt",
            "Structural nonlinear registration",
            "fnirt",
            {"moving": "anatomy", "reference": "anatomy", "initial": "spatial_transform"},
            {},
            {
                "iterations": parameter("int", minimum=1, maximum=20),
                "warp_resolution": parameter("number", minimum=2, maximum=30),
            },
            {"registered": "anatomy", "transform": "spatial_transform"},
        ),
        (
            "invert",
            "Invert verified transform",
            "convert_xfm",
            {"transform": "spatial_transform"},
            {},
            {},
            {"transform": "spatial_transform"},
        ),
        (
            "compose",
            "Compose matched transform chain",
            "convertwarp",
            {"first": "spatial_transform", "second": "spatial_transform"},
            {},
            {},
            {"transform": "spatial_transform"},
        ),
        (
            "convert",
            "Convert linear transform to MRtrix",
            "transformconvert",
            {"transform": "spatial_transform"},
            {},
            {},
            {"transform": "spatial_transform"},
        ),
    ]
    for suffix, kind in (
        ("scalar", "anatomy"),
        ("labels", "atlas_labels"),
        ("roi", "roi"),
        ("probability", "atlas_probability"),
    ):
        operations.append(
            (
                "apply." + suffix,
                "Resample " + suffix,
                "flirt",
                {"moving": kind, "reference": "anatomy", "transform": "spatial_transform"},
                {},
                {},
                {"registered": kind},
            )
        )
    for key, name, command, required, optional, params, outputs in operations:
        rows.append(
            algorithm(
                "registration." + key,
                name,
                "registration",
                command,
                required,
                optional,
                ("existing_anatomy", "existing_transform", "existing_atlas"),
                params,
                outputs,
                (
                    "source/target image hashes and conventions",
                    "output grid and categorical label preservation",
                    "anatomical review required; no automatic acceptance",
                ),
                backend="FSL / MRtrix3 local CLI",
                implementation="implemented",
                validated_versions=("FSL 6.0.7.18", "MRtrix3 3.0.5"),
                adapter="dmri_studio.core.scientific.registration.RegistrationAdapter",
            )
        )
    return rows
