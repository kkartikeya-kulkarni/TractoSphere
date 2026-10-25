"""Protect acquisition-specific choices without borrowing protocol defaults."""

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.acquisition import gibbs_plane, phase_encoding_row, validate_epi_reference


def test_explicit_signed_phase_and_seconds():
    """AP/PA filenames are irrelevant to the voxel-coordinate contract."""
    assert phase_encoding_row({"PhaseEncodingDirection": "j-", "TotalReadoutTime": 0.04914}) == (
        0,
        -1,
        0,
        0.04914,
    )
    assert phase_encoding_row({"PhaseEncodingDirection": "j", "TotalReadoutTime": 0.04914}) == (
        0,
        1,
        0,
        0.04914,
    )


@pytest.mark.parametrize("value", [None, True, "0.04914", 0, -1, float("nan"), float("inf")])
def test_no_readout_fallback(value):
    """Echo spacing alone never silently substitutes for TotalReadoutTime."""
    with pytest.raises(ValueError):
        phase_encoding_row(
            {
                "PhaseEncodingDirection": "j-",
                "TotalReadoutTime": value,
                "EffectiveEchoSpacing": 0.000390001,
            }
        )


def test_partial_fourier_unknown_filtering_is_eligible():
    """7/8 and filtering uncertainty qualify performance, not command eligibility."""
    image = nib.Nifti1Image(np.zeros((4, 6, 8)), np.eye(4))
    image.header.set_dim_info(freq=0, phase=1, slice=2)
    plane = gibbs_plane(image, {"PartialFourier": "7/8"})
    assert plane["axes"] == [0, 1]
    assert plane["partial_fourier_fraction"] == 0.875
    assert plane["eligible_despite_undocumented_filtering"]
    with pytest.raises(ValueError, match="conflict"):
        gibbs_plane(image, {"SliceEncodingDirection": "i"})


def test_missing_axes_requires_evidence():
    """Anatomical appearance and array dimensions cannot supply acquisition axes."""
    with pytest.raises(ValueError, match="undocumented"):
        gibbs_plane(nib.Nifti1Image(np.zeros((4, 6, 8)), np.eye(4)), {})


def test_unknown_fourier_coverage_does_not_block_verified_plane():
    """Record missing coverage/filtering without inventing full Fourier."""
    image = nib.Nifti1Image(np.zeros((4, 6, 8)), np.eye(4))
    image.header.set_dim_info(freq=0, phase=1, slice=2)
    result = gibbs_plane(image, {})
    assert result["partial_fourier_fraction"] is None
    assert result["axes"] == [0, 1]
    assert result["eligible_despite_undocumented_filtering"]


def test_reference_association_and_grid(tmp_path):
    """Do not select a same-grid BOLD reference or a reference of another subject."""
    subject = tmp_path / "sub-01"
    dwi = subject / "ses-01/dwi/sub-01_ses-01_dwi.nii.gz"
    reference = subject / "ses-01/fmap/sub-01_ses-01_epi.nii.gz"
    for path in (dwi, reference):
        path.parent.mkdir(parents=True, exist_ok=True)
        nib.save(nib.Nifti1Image(np.zeros((4, 6, 8, 2)), np.eye(4)), path)
    meta = {
        "PhaseEncodingDirection": "j-",
        "TotalReadoutTime": 0.04914,
        "EchoTime": 0.08,
        "RepetitionTime": 7,
        "FlipAngle": 90,
    }
    cal = {**meta, "PhaseEncodingDirection": "j", "IntendedFor": str(dwi.relative_to(subject))}
    assert validate_epi_reference(dwi, reference, meta, cal)["intended_for_verified"]
    windows_relative = str(dwi.relative_to(subject)).replace("/", "\\")
    assert validate_epi_reference(
        dwi, reference, meta, {**cal, "IntendedFor": windows_relative}
    )["intended_for_verified"]
    with pytest.raises(ValueError, match="IntendedFor"):
        validate_epi_reference(
            dwi, reference, meta, {**cal, "IntendedFor": "ses-01/func/bold.nii.gz"}
        )
    nib.save(nib.Nifti1Image(np.zeros((4, 6, 8, 2)), np.diag([2, 1, 1, 1])), reference)
    with pytest.raises(ValueError, match="grids differ"):
        validate_epi_reference(dwi, reference, meta, cal)
