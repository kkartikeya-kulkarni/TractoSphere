"""Version-verified native SIFT2 contracts, separate from tract generation."""


def sift2_algorithm(algorithm, parameter):
    """Declare explicit mask policy; absent numerical controls retain native defaults."""
    parameters = {
        "act": parameter("choice", choices=("off", "on")),
        "fd_scale_gm": parameter("choice", choices=("off", "on")),
    }
    for key, kind, minimum, maximum in (
        ("reg_tikhonov", "number", 0, None),
        ("reg_tv", "number", 0, None),
        ("min_td_frac", "number", 0, 1),
        ("fd_thresh", "number", 0, None),
        ("min_iters", "int", 0, 10000),
        ("max_iters", "int", 1, 10000),
        ("min_factor", "number", 0, 1),
        ("max_factor", "number", 1, None),
        ("min_coeff", "number", None, 0),
        ("max_coeff", "number", 1, None),
        ("max_coeff_step", "number", 0.000001, None),
        ("min_cf_decrease", "number", 0, 1),
    ):
        parameters[key] = parameter(kind, minimum=minimum, maximum=maximum, required=False)
    return algorithm(
        "tcksift2",
        "SIFT2 streamline weighting",
        "tractography",
        "tcksift2",
        {"tracts": "tractogram", "fod": "fod", "reference": "anatomy"},
        {"five_tt": "5tt", "proc_mask": "mask"},
        ("existing_tractogram", "existing_fod"),
        parameters,
        {"weights": "streamline_weights"},
        (
            "Exact source/order binding and one finite nonnegative factor per streamline",
            "mu and optimizer coefficients remain distinct from output weight factors",
            "Convergence and coverage findings never imply scientific acceptance",
        ),
        implementation="implemented",
        validated_versions=("3.0.5",),
        adapter="dmri_studio.core.scientific.sift2.SIFT2Adapter",
    )
