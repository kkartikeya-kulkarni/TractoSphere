"""Native-plane display geometry, usable from scripts without Qt.

``plane_geometry(dataset, 'coronal')`` describes a transpose and axis flips only.
Normals include shear; no interpolation, canonicalization or voxel edits occur.
Display fractions refer to pixel edges; crosshairs pass through voxel centers.
"""

from dataclasses import dataclass
from typing import Literal

import numpy as np

from .dataset import DwiDataset

Orientation = Literal["axial", "coronal", "sagittal"]
ORIENTATIONS: tuple[Orientation, ...] = ("axial", "coronal", "sagittal")


def native_plane(dataset: DwiDataset, orientation: Orientation = "axial") -> tuple[int, float, str]:
    """Choose the nearest normal independently; ties use the first file axis."""
    target = {"axial": 2, "coronal": 1, "sagittal": 0}[orientation]
    if dataset.affine_source == "fallback":
        return target, float("nan"), "Native file-axis preview — anatomical orientation unverified"
    columns = dataset.affine[:3, :3]
    normals = np.array(
        [np.cross(columns[:, (i + 1) % 3], columns[:, (i + 2) % 3]) for i in range(3)]
    )
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    axis = int(np.argmax(np.abs(normals[:, target])))
    angle = float(np.degrees(np.arccos(np.clip(abs(normals[axis, target]), 0, 1))))
    unit = columns / np.linalg.norm(columns, axis=0)
    aligned = np.allclose(np.max(np.abs(unit), axis=0), 1, atol=1e-6) and np.allclose(
        unit.T @ unit, np.eye(3), atol=1e-6
    )
    title = (
        f"{orientation.title()} preview"
        if aligned
        else f"Native plane closest to {orientation} — no resampling"
    )
    return axis, angle, title


def direction_label(vector: np.ndarray) -> str:
    """Report signed RAS components, largest first; include components >= 20%."""
    unit = vector / np.linalg.norm(vector)
    return "".join(
        ("RAS" if unit[i] > 0 else "LPI")[i]
        for i in sorted(range(3), key=lambda i: -abs(unit[i]))
        if abs(unit[i]) >= 0.2
    )


@dataclass(frozen=True)
class PlaneGeometry:
    """One invertible native-index/display mapping and its honest edge labels."""

    orientation: Orientation
    axis: int
    horizontal: int
    vertical: int
    right_sign: int
    up_sign: int
    shape: tuple[int, int, int]
    spacing: tuple[float, float]
    edges: tuple[str, str, str, str]  # left, right, top, bottom
    angle: float
    title: str

    @property
    def aspect(self) -> float:
        """Preserve affine column lengths (shear angle is not rectified)."""
        return (self.shape[self.horizontal] * self.spacing[0]) / (
            self.shape[self.vertical] * self.spacing[1]
        )

    def display_plane(self, plane: np.ndarray) -> np.ndarray:
        """Return screen rows/columns using only transpose and display flips."""
        remaining = [i for i in range(3) if i != self.axis]
        pixels = plane.transpose(remaining.index(self.vertical), remaining.index(self.horizontal))
        if self.right_sign < 0:
            pixels = np.fliplr(pixels)
        if self.up_sign > 0:
            pixels = np.flipud(pixels)
        return pixels

    def display_position(self, position: tuple[int, int, int]) -> tuple[float, float]:
        """Convert voxel centers to normalized left-to-right, top-to-bottom position."""
        x = (position[self.horizontal] + 0.5) / self.shape[self.horizontal]
        y = (position[self.vertical] + 0.5) / self.shape[self.vertical]
        return (x if self.right_sign > 0 else 1 - x, 1 - y if self.up_sign > 0 else y)

    def voxel_position(
        self, x: float, y: float, current: tuple[int, int, int]
    ) -> tuple[int, int, int] | None:
        """Map a hit to its voxel; reject padding and retain the slice coordinate."""
        if not (0 <= x < 1 and 0 <= y < 1):
            return None
        # A transformed pixel boundary can land a few ULPs to either side
        # after zoom/pan inversion. Snap only subnanovoxel roundoff so an exact
        # boundary consistently belongs to the voxel on its right/bottom.
        h = min(int(round(x * self.shape[self.horizontal], 10)), self.shape[self.horizontal] - 1)
        v = min(int(round(y * self.shape[self.vertical], 10)), self.shape[self.vertical] - 1)
        result = list(current)
        result[self.horizontal] = h if self.right_sign > 0 else self.shape[self.horizontal] - 1 - h
        result[self.vertical] = self.shape[self.vertical] - 1 - v if self.up_sign > 0 else v
        return tuple(result)


def plane_geometry(
    dataset: DwiDataset, orientation: Orientation, radiological: bool = False
) -> PlaneGeometry:
    """Place R right (L right in radiological), S/A up; sagittal A stays left.

    For oblique/sheared input, maximize the in-plane anatomical projections.
    Composite labels disclose obliquity, and fallback geometry has no anatomical
    claims. Each view independently selects its closest native plane, so very
    oblique/sheared input can select the same file axis for multiple views.
    """
    axis, angle, title = native_plane(dataset, orientation)
    remaining = [i for i in range(3) if i != axis]
    columns = dataset.affine[:3, :3]
    lengths = np.linalg.norm(columns, axis=0)
    unit = columns / lengths
    h_world, v_world = (
        (1, 2) if orientation == "sagittal" else (0, 1 if orientation == "axial" else 2)
    )
    h, v = max(
        (remaining, remaining[::-1]),
        key=lambda pair: abs(unit[h_world, pair[0]]) + abs(unit[v_world, pair[1]]),
    )
    right = (1 if unit[h_world, h] >= 0 else -1) * (
        -1 if orientation == "sagittal" or radiological else 1
    )
    up = 1 if unit[v_world, v] >= 0 else -1
    if dataset.affine_source == "fallback":
        h, v = remaining
        right, up = 1, 1
        edges = ("?", "?", "?", "?")
    else:
        edges = tuple(
            direction_label(vector)
            for vector in (
                -columns[:, h] * right,
                columns[:, h] * right,
                columns[:, v] * up,
                -columns[:, v] * up,
            )
        )
    return PlaneGeometry(
        orientation,
        axis,
        h,
        v,
        right,
        up,
        dataset.shape[:3],
        (float(lengths[h]), float(lengths[v])),
        edges,
        angle,
        title,
    )


def world_position(dataset: DwiDataset, position: tuple[int, int, int]) -> tuple[np.ndarray, str]:
    """Apply the chosen affine and explicit NIfTI units; never guess millimeters."""
    coordinates = (dataset.affine @ np.array([*position, 1]))[:3]
    factor = {"mm": 1.0, "meter": 1000.0, "micron": 0.001}.get(dataset.spatial_units)
    return (coordinates, "units unknown") if factor is None else (coordinates * factor, "mm")
