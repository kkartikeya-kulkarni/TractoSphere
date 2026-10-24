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

IMPORT_EVIDENCE = {
    "dwi": "Select the image and its matching b-values/b-vectors; add the acquisition JSON when available. Declare processing state and subject/session only from records you trust.",
    "anatomy": "Select the structural or derived image. Declare contrast and spatial identity from acquisition or registration records; a matching grid alone is insufficient.",
    "tractogram": "Select the original .tck or .trk file. Supply source subject and reference-space evidence before spatially constrained use.",
    "response": "Select a response-function text file. Declare its tissue compartment and shell b-values from the generating record.",
    "streamline_weights": "Select the weight vector. A source tractogram ID and hash-bound weight record are needed before applying it to a connectome.",
    "fod": "Select the FOD image and declare its spherical-harmonic basis, frame and tissue compartment from the producing record.",
    "mask": "Select the mask and declare its subject and registered space from reviewed evidence.",
    "atlas_labels": "Select the categorical atlas image. Preserve original region IDs and provide registration/QC evidence before spatial use.",
    "atlas_probability": "Select the probability image and declare its threshold and registered space; it is continuous data, not categorical labels.",
    "roi": "Select the ROI mask and declare its subject and spatial association from reviewed evidence.",
    "spatial_transform": "Select the original transform and provide explicit source, target, direction and software convention evidence.",
    "tensor": "Select the six-component tensor image and declare its component order, coordinate frame and units.",
    "5tt": "Select the five-tissue image and provide its channel order and anatomical review evidence.",
    "gmwmi": "Select the gray–white interface image and identify its exact source five-tissue image.",
    "tissue_scalar": "Select the tissue scalar image and record its tissue and normalization history.",
    "density": "Select the tract-density image and its verified reference-space evidence.",
    "connectome_matrix": "Select the connectivity record with its original node and atlas identity.",
}

assert set(IMPORT_EVIDENCE) == set(KINDS)


def input_label(kind: str) -> str:
    return INPUT_GUIDANCE[kind][0]


def input_description(kind: str) -> str:
    return INPUT_GUIDANCE[kind][1]
