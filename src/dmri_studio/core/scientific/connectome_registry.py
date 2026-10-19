"""Version-bound native connectome metrics, preserving each aggregation definition."""

METRICS = {
    "count": dict(
        name="Streamline count",
        units="streamlines",
        statistic="sum",
        flags=(),
        meaning="Number of assigned streamlines per undirected edge",
    ),
    "mean_length": dict(
        name="Mean streamline length",
        units="mm",
        statistic="mean",
        flags=("-scale_length",),
        meaning="Arithmetic mean of contributing complete streamline lengths",
    ),
    "mean_fa": dict(
        name="Mean FA",
        units="dimensionless FA",
        statistic="mean",
        flags=(),
        meaning="Arithmetic edge mean of contributing arc-length-weighted streamline mean FA from native trilinear vertex samples",
    ),
}

for key, base, name, units, meaning in (
    (
        "sift2_weighted",
        "count",
        "SIFT2-weighted streamline contribution",
        "sum of SIFT2 factors (no added mu scaling)",
        "Sum of native SIFT2 output weight factors assigned to an edge; not axon count",
    ),
    (
        "sift2_mean_length",
        "mean_length",
        "SIFT2-weighted mean length",
        "mm",
        "sum(weight * complete streamline length) / sum(weight); undefined when total weight is zero",
    ),
    (
        "sift2_mean_fa",
        "mean_fa",
        "SIFT2-weighted mean FA",
        "dimensionless FA",
        "sum(weight * arc-length-weighted streamline mean FA) / sum(weight); undefined when total weight is zero",
    ),
):
    METRICS[key] = dict(
        name=name,
        units=units,
        statistic=METRICS[base]["statistic"],
        flags=METRICS[base]["flags"],
        meaning=meaning,
        base_metric=base,
        weighted=True,
    )


def connectome_algorithms(algorithm, parameter):
    """Declare explicit policy choices and independently callable extraction."""
    return [
        algorithm(
            "tck2connectome",
            "Structural connectome",
            "connectivity",
            "tck2connectome",
            {"tracts": "tractogram", "nodes": "atlas_labels", "reference": "anatomy"},
            {"fa": "anatomy", "weights": "streamline_weights"},
            ("existing_tractogram",),
            {
                "metrics": parameter("choice_list", choices=tuple(METRICS)),
                "assignment": parameter(
                    "choice",
                    choices=("end_voxels", "radial_search", "reverse_search", "forward_search"),
                ),
                "radial_search_mm": parameter("number", minimum=0.01, maximum=100, required=False),
                "search_mm": parameter("number", minimum=0, maximum=200, required=False),
                "symmetric": parameter("choice", choices=("yes", "no")),
                "zero_diagonal": parameter("choice", choices=("yes", "no")),
            },
            {"connectome": "connectome_matrix"},
            (
                "explicit original node IDs including missing nodes",
                "both-endpoint assignments and independent edge aggregation",
                "finite matrices, occupancy, source/hash identity and pending scientific review",
            ),
            implementation="implemented",
            validated_versions=("3.0.5",),
            adapter="dmri_studio.core.scientific.connectome.ConnectomeAdapter",
        ),
        algorithm(
            "connectome2tck",
            "Extract selected node pair",
            "connectivity",
            "connectome2tck",
            {"connectome": "connectome_matrix"},
            {},
            ("existing_connectome",),
            {"node_a": parameter("int", minimum=1), "node_b": parameter("int", minimum=1)},
            {"tracts": "tractogram"},
            (
                "exact source tract/assignment identity",
                "edge count and original streamline coordinate identity",
            ),
            implementation="implemented",
            validated_versions=("3.0.5",),
            adapter="dmri_studio.core.scientific.connectome.ConnectomeAdapter",
        ),
    ]
