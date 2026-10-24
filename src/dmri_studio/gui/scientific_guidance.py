"""Presentation copy for the existing typed scientific contracts.

Only ``KINDS`` from the core can be offered for import. These descriptions do
not infer identity, compatibility, processing history or scientific approval.
"""

from dmri_studio.core.scientific.contracts import KINDS

INPUT_GUIDANCE = {
    "dwi": (
        "Diffusion MRI image",
        "A diffusion image with matching b-values and b-vectors. Confirm its acquisition and processing history.",
    ),
    "tensor": (
        "Diffusion tensor",
        "A six-component tensor image. Declare its component order, coordinate frame and units.",
    ),
    "fod": (
        "Fiber orientation distribution",
        "A spherical-harmonic image. Declare its basis, frame and tissue compartment.",
    ),
    "mask": (
        "Brain or tissue mask",
        "A mask whose subject and spatial association must be reviewed.",
    ),
    "anatomy": (
        "Anatomical MRI or scalar map",
        "A structural or derived image. Declare its contrast and spatial identity where required.",
    ),
    "atlas_labels": (
        "Registered atlas labels",
        "Categorical region IDs. Supply registration and QC evidence when required.",
    ),
    "atlas_probability": (
        "Probability atlas",
        "Continuous-valued regional probabilities; choose the threshold and spatial evidence explicitly.",
    ),
    "roi": ("Region of interest", "A region or mask for a verified spatial association."),
    "tractogram": (
        "Tractogram",
        "Existing streamlines with an explicit source and reference-space identity.",
    ),
    "spatial_transform": (
        "Spatial transformation",
        "A transform with declared source, target, direction and software convention.",
    ),
    "5tt": (
        "Five-tissue image",
        "Five tissue classes in the supported channel order, with anatomical review evidence.",
    ),
    "gmwmi": (
        "Gray–white interface",
        "A generated interface image associated with its original five-tissue source.",
    ),
    "response": (
        "CSD response function",
        "Tissue response coefficients with declared shell information.",
    ),
    "tissue_scalar": (
        "Tissue scalar image",
        "A tissue-specific scalar derivative with declared source association.",
    ),
    "density": (
        "Tract-density image",
        "A streamline-derived density image on a verified reference grid.",
    ),
    "connectome_matrix": (
        "Connectivity matrix",
        "A matrix with original atlas node identity and processing evidence.",
    ),
    "streamline_weights": (
        "SIFT2 streamline weights",
        "Weights bound to the exact source tractogram and reviewed FOD.",
    ),
}

assert set(INPUT_GUIDANCE) == set(KINDS)


def input_label(kind: str) -> str:
    return INPUT_GUIDANCE[kind][0]


def input_description(kind: str) -> str:
    return INPUT_GUIDANCE[kind][1]
