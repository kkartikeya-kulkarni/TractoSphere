"""Headless anatomical display contract using all signed file-axis permutations.

The asymmetric voxel ramp exposes accidental transposes/flips independently of
crosshair round trips. No Qt, external data, or image resampling is involved.
"""

from itertools import permutations, product
from types import SimpleNamespace

import numpy as np
import pytest

from dmri_studio.core.preview_geometry import (
    ORIENTATIONS,
    native_plane,
    plane_geometry,
    world_position,
)


def metadata(affine: np.ndarray, source: str = "sform", units: str = "mm") -> object:
    """Supply just the immutable geometry fields used by the pure helpers."""
    return SimpleNamespace(
        affine=affine, affine_source=source, shape=(5, 7, 9, 4), spatial_units=units
    )


@pytest.mark.parametrize("permutation", list(permutations(range(3))))
@pytest.mark.parametrize("signs", list(product((-1, 1), repeat=3)))
@pytest.mark.parametrize("orientation", ORIENTATIONS)
@pytest.mark.parametrize("radiological", (False, True))
def test_signed_permutations(
    permutation: tuple, signs: tuple, orientation: str, radiological: bool
) -> None:
    """All 48 native axis layouts select/label/map actual anatomical directions."""
    affine = np.eye(4)
    affine[:3, :3] = np.eye(3)[:, permutation] * np.array(signs) * (1, 2, 3)
    dataset = metadata(affine)
    geometry = plane_geometry(dataset, orientation, radiological)
    normal_world = {"axial": 2, "coronal": 1, "sagittal": 0}[orientation]
    assert geometry.axis == permutation.index(normal_world)
    assert geometry.angle == 0
    expected_edges = {
        "axial": ("L", "R", "A", "P"),
        "coronal": ("L", "R", "S", "I"),
        "sagittal": ("A", "P", "S", "I"),
    }[orientation]
    if radiological and orientation != "sagittal":
        expected_edges = ("R", "L", *expected_edges[2:])
    assert geometry.edges == expected_edges
    volume = np.indices(dataset.shape[:3])
    ramp = volume[0] * 100 + volume[1] * 10 + volume[2]
    position = (1, 2, 3)
    native = np.take(ramp, position[geometry.axis], axis=geometry.axis)
    screen = geometry.display_plane(native)
    # Compute each displayed pixel's world direction independently from its
    # encoded native indices: increasing screen x must move toward R or P.
    for row, col in ((0, 0), (screen.shape[0] - 1, screen.shape[1] - 1)):
        value = screen[row, col]
        voxel = (int(value // 100), int(value % 100 // 10), int(value % 10))
        x, y = (col + 0.5) / screen.shape[1], (row + 0.5) / screen.shape[0]
        assert geometry.voxel_position(x, y, position) == voxel
        assert geometry.display_position(voxel) == pytest.approx((x, y))
    x, y = geometry.display_position(position)
    assert geometry.voxel_position(x, y, position) == position
    assert geometry.voxel_position(-0.01, y, position) is None
    assert geometry.voxel_position(x, 1, position) is None


@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_shear_uses_plane_normals(orientation: str) -> None:
    """Sheared native plane selection uses cross products, not the slice column."""
    affine = np.array([[1, 2, 0.2, 10], [0, 1, 0.3, -20], [0.8, 0, 1, 30], [0, 0, 0, 1]])
    target = {"axial": 2, "coronal": 1, "sagittal": 0}[orientation]
    # Inverse-transpose columns are proportional to file-plane normals.
    normals = np.linalg.inv(affine[:3, :3]).T
    scores = np.abs(normals[target, :]) / np.linalg.norm(normals, axis=0)
    axis, angle, title = native_plane(metadata(affine), orientation)
    assert axis == int(np.argmax(scores))
    assert angle == pytest.approx(np.degrees(np.arccos(scores[axis])))
    assert "no resampling" in title
    geometry = plane_geometry(metadata(affine), orientation)
    assert any(len(edge) > 1 for edge in geometry.edges)


@pytest.mark.parametrize("orientation,axis", [("axial", 2), ("coronal", 1), ("sagittal", 0)])
def test_fallback_never_claims_anatomy(orientation: str, axis: int) -> None:
    """Uncoded affines retain file planes and suppress anatomical labels/flips."""
    geometry = plane_geometry(metadata(np.eye(4), "fallback"), orientation, True)
    assert geometry.axis == axis
    assert np.isnan(geometry.angle)
    assert "unverified" in geometry.title
    assert geometry.edges == ("?",) * 4
    assert geometry.right_sign == geometry.up_sign == 1


@pytest.mark.parametrize(
    "units,factor,label",
    [
        ("mm", 1, "mm"),
        ("meter", 1000, "mm"),
        ("micron", 0.001, "mm"),
        ("unknown", 1, "units unknown"),
    ],
)
def test_world_units_and_translation(units: str, factor: float, label: str) -> None:
    """Apply the entire affine, then known units; unknown units remain unknown."""
    affine = np.array([[-2, 0, 0, 10], [0, 3, 0, -20], [0, 0, 4, 30], [0, 0, 0, 1]])
    values, actual_label = world_position(metadata(affine, units=units), (1, 2, 3))
    np.testing.assert_allclose(values, np.array([8, -14, 42]) * factor)
    assert actual_label == label
