"""Strict acquisition contracts for reference preprocessing, without GUI imports.

Read actual NIfTI file axes and explicit BIDS metadata. Direction labels in file
names are never interpreted as signed voxel axes. No readout fallback, slice
timing reconstruction, or full-Fourier assumption is permitted.
"""

from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path

import nibabel as nib
import numpy as np


def phase_encoding_row(metadata: dict) -> tuple[float, float, float, float]:
    """Return a FSL acqparams row from explicit signed PE and readout seconds."""
    direction = metadata.get("PhaseEncodingDirection")
    if not isinstance(direction, str) or direction not in {"i", "i-", "j", "j-", "k", "k-"}:
        raise ValueError("Explicit BIDS PhaseEncodingDirection is required.")
    readout = metadata.get("TotalReadoutTime")
    if isinstance(readout, bool) or not isinstance(readout, (int, float)):
        raise ValueError("Explicit numeric TotalReadoutTime in seconds is required.")
    if not math.isfinite(readout) or readout <= 0:
        raise ValueError("TotalReadoutTime must be positive and finite.")
    vector = [0.0, 0.0, 0.0]
    vector["ijk".index(direction[0])] = -1.0 if direction.endswith("-") else 1.0
    return (*vector, float(readout))


def gibbs_plane(image: nib.spatialimages.SpatialImage, metadata: dict) -> dict:
    """Verify the file slice axis; PF and unknown filtering remain limitations.

    BIDS SliceEncodingDirection and NIfTI dim_info must agree when both exist.
    Timing is not invented from axis, slice count or multiband factor. This
    contract selects the original 2D mrdegibbs algorithm, not the dev 3D option.
    """
    header_axis = image.header.get_dim_info()[2]
    direction = metadata.get("SliceEncodingDirection")
    if direction is not None and (
        not isinstance(direction, str) or direction not in {"i", "i-", "j", "j-", "k", "k-"}
    ):
        raise ValueError("Invalid BIDS SliceEncodingDirection.")
    json_axis = "ijk".index(direction[0]) if direction else None
    if header_axis is not None and json_axis is not None and header_axis != json_axis:
        raise ValueError("NIfTI and JSON slice axes conflict.")
    axis = json_axis if json_axis is not None else header_axis
    if axis is None:
        raise ValueError("Slice axis is undocumented; verify it before choosing Gibbs axes.")
    pf = metadata.get("PartialFourier")
    fraction = None
    if pf is not None:
        try:
            if isinstance(pf, bool):
                raise ValueError
            fraction = float(Fraction(str(pf)))
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError("Invalid PartialFourier value.") from exc
        if not 0 < fraction <= 1:
            raise ValueError("PartialFourier must be in (0, 1].")
    return {
        "slice_axis": axis,
        "slice_axis_source": "BIDS SliceEncodingDirection"
        if json_axis is not None
        else "NIfTI dim_info.slice",
        "axes": [a for a in range(3) if a != axis],
        "partial_fourier_original": pf,
        "partial_fourier_fraction": fraction,
        "scanner_filtering": "undocumented",
        "limitations": [
            "Partial Fourier can leave residual ringing; no promise of complete removal.",
            "Undocumented reconstruction filtering can alter ringing and noise statistics.",
        ],
        "eligible_despite_undocumented_filtering": True,
        "required_order": "MP-PCA, then Gibbs, before interpolation/motion correction",
    }


def validate_epi_reference(dwi: Path, reference: Path, dwi_metadata: dict, metadata: dict) -> dict:
    """Require explicit same-subject IntendedFor association, grid and contrast.

    IntendedFor paths are BIDS subject-relative. A shared reference with a
    different grid or contrast needs a separately reviewed alignment contract;
    this bounded validation does not silently resample or permit that case.
    """
    dwi, reference = dwi.expanduser().absolute(), reference.expanduser().absolute()
    row, dwi_row = phase_encoding_row(metadata), phase_encoding_row(dwi_metadata)
    target = next((p for p in dwi.parents if p.name.startswith("sub-")), None)
    reference_subject = next((p for p in reference.parents if p.name.startswith("sub-")), None)
    if target is None or reference_subject != target:
        raise ValueError("DWI and reference must share a BIDS subject.")
    intended = metadata.get("IntendedFor", [])
    intended = [intended] if isinstance(intended, str) else intended
    # Support the local-dataset BIDS URI as well as legacy subject-relative
    # IntendedFor. Named external datasets require a DatasetLinks resolver;
    # neither basename matching nor another subject's path is sufficient.
    accepted_targets = {
        dwi.relative_to(target).as_posix(),
        "bids::" + dwi.relative_to(target.parent).as_posix(),
    }
    if not isinstance(intended, list) or not any(
        isinstance(item, str) and item.replace("\\", "/") in accepted_targets
        for item in intended
    ):
        raise ValueError("Reference IntendedFor does not identify this DWI.")
    source, calibration = nib.load(dwi), nib.load(reference)
    if source.shape[:3] != calibration.shape[:3] or not np.allclose(
        source.affine, calibration.affine, atol=1e-5, rtol=0
    ):
        raise ValueError("Reference and DWI native grids differ; alignment review required.")
    if np.argmax(np.abs(row[:3])) != np.argmax(np.abs(dwi_row[:3])):
        raise ValueError("Reference PE axis differs from DWI.")
    for key in ("EchoTime", "RepetitionTime", "FlipAngle"):
        a, b = dwi_metadata.get(key), metadata.get(key)
        if (
            a is None
            or b is None
            or isinstance(a, bool)
            or isinstance(b, bool)
            or not isinstance(a, (int, float))
            or not isinstance(b, (int, float))
            or not math.isfinite(a)
            or not math.isfinite(b)
            or not math.isclose(a, b, abs_tol=1e-8)
        ):
            raise ValueError(f"Reference/DWI {key} is missing or differs.")
    return {
        "path": str(reference),
        "shape": list(calibration.shape),
        "acqparams": list(row),
        "same_native_grid": True,
        "contrast_matches": True,
        "intended_for_verified": True,
    }
