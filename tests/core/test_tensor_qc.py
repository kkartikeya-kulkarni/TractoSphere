"""Validity counts distinguish components, voxels, nonfinite and roundoff."""

import numpy as np
import pytest

from dmri_studio.core.scientific.tensor_qc import eigenvalue_findings


def test_component_and_voxel_counts_do_not_hide_meaningful_violations():
    """Two negative components in one voxel are not two affected voxels."""
    values = np.array([[1e-3, 2e-4, 1e-4], [1e-3, -2e-4, -1e-4], [1e-3, 0, -1e-12]])
    original = values.copy()
    result = eigenvalue_findings(values, np.array([0.6, 1.2, 1 + 1e-7]))
    assert result["negative_eigenvalues_raw"] == 3
    assert result["negative_eigenvalue_voxels_raw"] == 2
    assert result["negative_eigenvalues_beyond_roundoff"] == 2
    assert result["negative_eigenvalue_voxels_beyond_roundoff"] == 1
    assert result["fa_above_one_raw"] == 2
    assert result["fa_above_one_beyond_roundoff"] == 1
    assert result["roundoff_policy"]["scientific_acceptance"] is False
    np.testing.assert_array_equal(values, original)


def test_nonfinite_findings_and_empty_domains_are_explicit():
    """Missing values are counted separately, never silently repaired as zero."""
    result = eigenvalue_findings(np.array([[np.nan, np.inf, -1e-3]]), np.array([np.nan]))
    assert result["nonfinite_eigenvalues"] == 2
    assert result["voxels_with_nonfinite_eigenvalues"] == 1
    assert result["negative_eigenvalues_beyond_roundoff"] == 1
    assert result["fa_nonfinite"] == 1
    assert eigenvalue_findings(np.empty((0, 3)))["evaluated_voxels"] == 0
    with pytest.raises(ValueError, match="three-eigenvalue"):
        eigenvalue_findings(np.ones((2, 6)))
    with pytest.raises(ValueError, match="one value"):
        eigenvalue_findings(np.ones((2, 3)), np.ones(3))
