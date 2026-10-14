"""Strict same-space, lazy NIfTI overlays, independent of Qt.

OverlayDataset.load(path, dwi) checks metadata and eight voxel-center corners.
No registration, resampling, reorientation, or source writes are performed.
"""

import logging
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from threading import RLock
from typing import Any

import nibabel as nib
import numpy as np

from .dataset import DwiDataset, SourceFingerprint

LOGGER = logging.getLogger(__name__)
MM_FACTORS = {"mm": 1.0, "meter": 1000.0, "micron": 0.001}


def validate_overlay_space(
    shape: tuple[int, ...], affine: np.ndarray, units: str, dataset: DwiDataset
) -> float:
    """Refuse shape/geometry mismatch; return maximum corner distance in mm."""
    if len(shape) not in (3, 4) or any(n < 1 for n in shape):
        raise ValueError(f"Overlay must be a nonempty 3D or 4D NIfTI; got shape {shape}.")
    if tuple(shape[:3]) != dataset.shape[:3]:
        raise ValueError(
            f"Overlay spatial shape {tuple(shape[:3])} differs from DWI {dataset.shape[:3]}. Choose an overlay on the exact DWI voxel grid; no resampling is performed."
        )
    if units not in MM_FACTORS or dataset.spatial_units not in MM_FACTORS:
        raise ValueError(
            "Overlay alignment cannot be verified to 0.01 mm: overlay or DWI spatial units are unknown. Supply images with explicit spatial units; no resampling is performed."
        )
    if dataset.affine_source == "fallback":
        raise ValueError(
            "DWI has no verified sform/qform affine; overlay alignment cannot be verified."
        )
    affine = np.asarray(affine, dtype=float)
    if (
        affine.shape != (4, 4)
        or not np.isfinite(affine).all()
        or np.linalg.matrix_rank(affine[:3, :3]) < 3
    ):
        raise ValueError("Overlay affine is nonfinite or singular; choose a valid NIfTI.")
    corners = np.array([(*p, 1) for p in product(*[(0, n - 1) for n in shape[:3]])])
    actual = (corners @ affine.T)[:, :3] * MM_FACTORS[units]
    expected = (corners @ dataset.affine.T)[:, :3] * MM_FACTORS[dataset.spatial_units]
    distance = float(np.linalg.norm(actual - expected, axis=1).max())
    if distance > 0.01:
        raise ValueError(
            f"Overlay affine differs from DWI: maximum corner displacement is {distance:.6g} mm (allowed 0.01 mm). Choose an overlay on the exact DWI voxel grid; no resampling is performed."
        )
    return distance


@dataclass
class OverlayDataset:
    """One read-only proxy; only requested planes are allocated and scaled."""

    path: Path
    image: Any = field(repr=False)
    fingerprint: SourceFingerprint
    corner_distance_mm: float
    _lock: Any = field(default_factory=RLock, repr=False)

    @classmethod
    def load(cls, path: Path | str, dataset: DwiDataset) -> "OverlayDataset":
        """Open and validate in the caller's worker; do not read a whole volume."""
        path = Path(path).expanduser().absolute()
        fingerprint = SourceFingerprint.capture(path)
        image = nib.load(path, mmap="r", keep_file_open=False)
        if not isinstance(image, (nib.Nifti1Image, nib.Nifti2Image)):
            raise ValueError("Overlay must be a NIfTI image.")
        if not (image.header["sform_code"] or image.header["qform_code"]):
            raise ValueError("Overlay has no sform/qform affine; alignment cannot be verified.")
        if np.dtype(image.get_data_dtype()).kind not in "iuf":
            raise ValueError("Overlay must contain real scalar voxel values.")
        distance = validate_overlay_space(
            image.shape, image.affine, image.header.get_xyzt_units()[0], dataset
        )
        if not fingerprint.unchanged(path):
            raise ValueError("Overlay source changed while loading; load it again.")
        LOGGER.info(
            "Overlay accepted: %s; maximum corner displacement %.6g mm; no resampling",
            path,
            distance,
        )
        return cls(path, image, fingerprint, distance)

    @property
    def n_volumes(self) -> int:
        """Expose one volume for 3D input and the fourth dimension for 4D."""
        return self.image.shape[3] if len(self.image.shape) == 4 else 1

    def read_slice(
        self, volume_index: int, *, axis: int = 2, index: int | None = None
    ) -> np.ndarray:
        """Read a detached scaled plane with strict indices and source checks."""
        axis = DwiDataset._index(axis, 3, "axis")
        volume_index = DwiDataset._index(volume_index, self.n_volumes, "volume_index")
        index = (
            self.image.shape[axis] // 2
            if index is None
            else DwiDataset._index(index, self.image.shape[axis], "index")
        )
        key = [slice(None)] * 3
        key[axis] = index
        if len(self.image.shape) == 4:
            key.append(volume_index)
        with self._lock:
            if not self.fingerprint.unchanged(self.path):
                raise ValueError("Overlay source changed or is unavailable; remove and reload it.")
            result = np.array(self.image.dataobj[tuple(key)], dtype=np.float64, copy=True)
            if not self.fingerprint.unchanged(self.path):
                raise ValueError("Overlay source changed during read; remove and reload it.")
        return result
