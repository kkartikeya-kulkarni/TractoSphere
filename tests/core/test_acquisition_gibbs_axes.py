"""Acquisition-independent Gibbs slice-axis metadata contract."""

import numpy as np


def test_gibbs_plane_uses_header_without_assuming_PF():
    """Missing PF is distinct from full Fourier; NIfTI slice axis is usable."""
    import nibabel as nib

    from dmri_studio.core.acquisition import gibbs_plane

    image = nib.Nifti1Image(np.zeros((7, 9, 11), np.float32), np.eye(4))
    image.header.set_dim_info(freq=0, phase=1, slice=2)
    result = gibbs_plane(image, {})
    assert result["axes"] == [0, 1]
    assert result["partial_fourier_fraction"] is None
    assert result["slice_axis_source"] == "NIfTI dim_info.slice"
