"""Stage 4 contracts; all commands execute through the shared artifact engine."""


def tracking_algorithms(algorithm, parameter):
    """Declare five real tracking variants and independent filtering/density tasks.

    Repeated ROI slots have stable explicit order, not globbed filenames. Eight
    inclusion, exclusion and ordered-waypoint masks are currently supported.
    """
    rois = {f"{role}_{i}": "roi" for role in ("include", "exclude", "ordered") for i in range(1, 9)}
    qc = (
        "TCK full finite-coordinate/count/length verification",
        "physical RAS coordinates, mask and ROI compliance",
        "seeds/selected count and warnings; scientific acceptance remains pending",
    )
    params = {
        "select": parameter("int", minimum=1, maximum=10000000),
        "seeds": parameter("int", minimum=1, maximum=100000000),
        "seed_method": parameter(
            "choice",
            choices=("image", "dynamic", "sphere", "roi", "random_per_voxel", "grid_per_voxel"),
        ),
        "seed_sphere": parameter("vector", required=False),
        "per_voxel": parameter("int", minimum=1, maximum=100, required=False),
        "step_mm": parameter("number", minimum=0.01, maximum=10),
        "angle_deg": parameter("number", minimum=0.1, maximum=90),
        "cutoff": parameter("number", minimum=0.000001),
        "seed_cutoff": parameter("number", minimum=0.000001),
        "min_length_mm": parameter("number", minimum=0.01, maximum=1000),
        "max_length_mm": parameter("number", minimum=0.01, maximum=2000),
        "max_attempts_per_seed": parameter("int", minimum=1, maximum=10000),
        "seed_direction": parameter("vector", required=False),
        "unidirectional": parameter("choice", choices=("on", "off"), required=False),
        "stop_after_includes": parameter("choice", choices=("on", "off"), required=False),
    }
    rows = []
    for suffix, canonical, tensor in (
        ("ifod2", "iFOD2", False),
        ("sdstream", "SD_Stream", False),
        ("ifod1", "iFOD1", False),
        ("tensor_det", "Tensor_Det", True),
        ("tensor_prob", "Tensor_Prob", True),
    ):
        specific = dict(params)
        if tensor:
            specific["shells"] = parameter("number_list", minimum=0)
        if suffix in ("ifod1", "ifod2"):
            specific["trials"] = parameter("int", minimum=1, maximum=10000, required=False)
            specific["power"] = parameter("number", minimum=0.001, maximum=10, required=False)
        if suffix == "ifod2":
            specific["samples"] = parameter("int", minimum=2, maximum=100, required=False)
        if suffix in ("sdstream", "tensor_det"):
            specific["rk4"] = parameter("choice", choices=("on", "off"), required=False)
        rows.append(
            algorithm(
                "tckgen." + suffix,
                canonical + " · " + ("DWI tensor tracking" if tensor else "FOD tracking"),
                "tractography",
                "tckgen",
                {"dwi" if tensor else "fod": "dwi" if tensor else "fod", "mask": "mask"},
                {"seed_mask": "mask", "seed_roi": "roi", "reference": "anatomy", **rois},
                ("single_shell", "multi_shell") if tensor else ("existing_fod",),
                specific,
                {"tracts": "tractogram"},
                qc,
                implementation="implemented",
                validated_versions=("3.0.5",),
                adapter="dmri_studio.core.scientific.tracking.TrackingAdapter",
            )
        )
    # Qualified ACT variants share the same adapter and parameter contracts.
    # A separate ID makes mode explicit in saved workflows and older projects.
    from dataclasses import replace

    for standard in tuple(rows):
        if standard.id not in {"tckgen.ifod2", "tckgen.ifod1", "tckgen.sdstream"}:
            continue
        specific = dict(standard.parameters)
        specific["seed_method"] = parameter(
            "choice",
            choices=(
                "image",
                "dynamic",
                "sphere",
                "roi",
                "random_per_voxel",
                "grid_per_voxel",
                "gmwmi",
            ),
        )
        specific["backtrack"] = parameter("choice", choices=("on", "off"))
        specific["crop_at_gmwmi"] = parameter("choice", choices=("on", "off"))
        rows.append(
            replace(
                standard,
                id=standard.id.replace("tckgen.", "tckgen.act_"),
                name=standard.name + " · ACT",
                required_inputs={"fod": "fod", "five_tt": "5tt", "reference": "anatomy"},
                optional_inputs={
                    "mask": "mask",
                    "gmwmi": "gmwmi",
                    "seed_mask": "mask",
                    "seed_roi": "roi",
                    **rois,
                },
                parameters=specific,
                qc=(
                    *qc,
                    "native 5ttcheck; hash-bound anatomical review; endpoint tissue fractions",
                ),
            )
        )
    rows.append(
        algorithm(
            "tckedit",
            "Filter existing streamlines",
            "tractography",
            "tckedit",
            {"tracts": "tractogram", "reference": "anatomy"},
            {
                "original_atlas": "atlas_labels",  # provenance binding, never a binary constraint
                **{
                    f"{role}_{i}": "roi"
                    for role in ("include", "exclude", "ordered")
                    for i in range(1, 257)
                },
            },
            ("existing_tractogram",),
            {
                "min_length_mm": parameter("number", minimum=0),
                "max_length_mm": parameter("number", minimum=0.01),
                "number": parameter("int", minimum=1, maximum=10000000, required=False),
                "skip": parameter("int", minimum=0, required=False),
                "ends_only": parameter("choice", choices=("on", "off")),
            },
            {"tracts": "tractogram"},
            qc,
            implementation="implemented",
            validated_versions=("3.0.5",),
            adapter="dmri_studio.core.scientific.tracking.TrackingAdapter",
        )
    )
    rows.append(
        algorithm(
            "tckmap",
            "Tract-density image",
            "tractography",
            "tckmap",
            {"tracts": "tractogram", "reference": "anatomy"},
            {},
            ("existing_tractogram",),
            {"directional": parameter("choice", choices=("on", "off"))},
            {"density": "density"},
            (
                "finite nonnegative density, exact template geometry",
                "streamline contributions, not axon density",
            ),
            implementation="implemented",
            validated_versions=("3.0.5",),
            adapter="dmri_studio.core.scientific.tracking.TrackingAdapter",
        )
    )
    return rows
