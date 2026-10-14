"""Read-only dataset metadata and bounded proxy access, with no GUI dependency.

Construct datasets through ``DatasetLoader.load``; see README.md for an example
using a DWI/bval/bvec set outside the repository. ``read_slice`` indexes the
requested 2D file-axis plane directly. It does not obtain a volume and discard
most of it, which matters for multi-gigabyte HCP data and the later preview.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import InitVar, asdict, dataclass, field
from numbers import Integral
from pathlib import Path
from threading import RLock
from types import MappingProxyType, TracebackType
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
from numpy.typing import NDArray

from dmri_studio.core.validation import ValidationReport, freeze_json, json_compatible

if TYPE_CHECKING:
    from dmri_studio.core.gradients import GradientTable

LOGGER = logging.getLogger(__name__)


class DatasetReadError(RuntimeError):
    """An explicitly requested lazy read failed, without changing source data."""


class SourceChangedError(DatasetReadError):
    """Source identity or metadata changed; a fresh validation/load is required."""


class DatasetClosedError(DatasetReadError):
    """A caller attempted image access after closing the dataset."""


@dataclass(frozen=True, slots=True)
class DatasetPaths:
    """Explicit source references; no BIDS discovery or image copying is implied.

    Missing/blank references are retained as None so the service can return a
    helpful F01 finding instead of accidentally interpreting an empty path as '.'.
    """

    dwi: Path | str | None
    bval: Path | str | None
    bvec: Path | str | None
    json: Path | str | None = None

    def __post_init__(self) -> None:
        """Convert supplied references to paths without requiring their existence."""
        for name in ("dwi", "bval", "bvec", "json"):
            value = getattr(self, name)
            object.__setattr__(self, name, None if value is None or value == "" else Path(value))

    def supplied(self) -> dict[str, Path]:
        """Return only supplied roles, retaining lexical paths for change checks."""
        return {
            name: value
            for name in ("dwi", "bval", "bvec", "json")
            if (value := getattr(self, name)) is not None
        }

    def resolved(self) -> DatasetPaths:
        """Expand home paths and resolve references without opening their data."""
        return DatasetPaths(
            **{
                name: value.expanduser().resolve()
                if (value := getattr(self, name)) is not None
                else None
                for name in ("dwi", "bval", "bvec", "json")
            }
        )


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    """Cheap source identity, not a content checksum or immutable file guarantee."""

    path: str
    resolved_path: str
    size: int
    mtime_ns: int
    device: int
    inode: int

    @classmethod
    def capture(cls, path: Path | str) -> SourceFingerprint:
        """Stat the referenced target and retain the original path for symlink checks."""
        requested = Path(path).expanduser().absolute()
        resolved = requested.resolve(strict=True)
        stat = resolved.stat()
        return cls(
            str(requested), str(resolved), stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino
        )

    def unchanged(self, path: Path | str) -> bool:
        """Check identity, size, and mtime; inaccessible/removed sources are changed."""
        try:
            return self == type(self).capture(path)
        except (OSError, ValueError, RuntimeError):
            return False

    def to_dict(self) -> dict[str, str | int]:
        """Expose only primitive provenance fields for report serialization."""
        return asdict(self)


@dataclass(frozen=True, slots=True, eq=False)
class DwiDataset:
    """A validated metadata snapshot with explicitly requested, serialized reads.

    The service owns creation. ``image`` is an InitVar retained privately as a
    NiBabel proxy image. Image/gradient axes are never canonicalized here. This
    dataclass freezes public metadata; close() only releases the private reader.
    """

    id: str
    paths: DatasetPaths
    image: InitVar[Any]
    gradients: GradientTable
    validation: ValidationReport
    voxel_sizes_native: tuple[float, float, float]
    spatial_units: str
    affine: NDArray[np.float64]
    affine_source: Literal["sform", "qform", "fallback"]
    axis_codes: tuple[str | None, str | None, str | None]
    fingerprints: Mapping[str, SourceFingerprint]
    metadata: Mapping[str, Any]
    sidecar: Mapping[str, Any] | None = None
    shape: tuple[int, int, int, int] = field(init=False)
    storage_dtype: str = field(init=False)
    _image: Any = field(init=False, repr=False, compare=False)
    _closed: bool = field(default=False, init=False, repr=False, compare=False)
    _lock: Any = field(default_factory=RLock, init=False, repr=False, compare=False)

    def __post_init__(self, image: Any) -> None:
        """Defensively freeze metadata; never request the proxy's voxel array."""
        # Preserve lexical symlink paths, but make them independent of a later
        # working-directory change by the script or future GUI.
        object.__setattr__(
            self,
            "paths",
            DatasetPaths(
                **{
                    role: path.expanduser().absolute()
                    for role, path in self.paths.supplied().items()
                }
            ),
        )
        object.__setattr__(self, "_image", image)
        object.__setattr__(self, "shape", tuple(int(n) for n in image.shape))
        object.__setattr__(self, "storage_dtype", str(image.get_data_dtype()))
        # bytes-backed metadata cannot be made writeable again with setflags().
        affine = np.frombuffer(
            np.asarray(self.affine, dtype=np.float64).tobytes(), dtype=np.float64
        ).reshape(4, 4)
        object.__setattr__(self, "affine", affine)
        object.__setattr__(self, "voxel_sizes_native", tuple(self.voxel_sizes_native))
        object.__setattr__(self, "axis_codes", tuple(self.axis_codes))
        object.__setattr__(self, "fingerprints", MappingProxyType(dict(self.fingerprints)))
        object.__setattr__(self, "metadata", freeze_json(self.metadata))
        object.__setattr__(self, "sidecar", freeze_json(self.sidecar))

    @property
    def n_volumes(self) -> int:
        """Number of ordered diffusion measurements (the fourth dimension)."""
        return self.shape[3]

    def voxel_sizes_mm(self) -> tuple[float, float, float] | None:
        """Convert known spatial units for presentation; never guess unknown units."""
        factor = {"mm": 1.0, "meter": 1000.0, "micron": 0.001}.get(self.spatial_units)
        return None if factor is None else tuple(float(v * factor) for v in self.voxel_sizes_native)

    def default_preview_volume(self) -> int:
        """Choose the first b0-like volume under the recorded policy, else volume 0."""
        indices = np.flatnonzero(self.gradients.b0_mask(b0_max=self.validation.policy.b0_max))
        return int(indices[0]) if indices.size else 0

    def summary(self) -> dict[str, Any]:
        """Return detached metadata for CLI/GUI without opening voxel data."""
        policy = self.validation.policy
        b0_count = int(np.count_nonzero(self.gradients.b0_mask(b0_max=policy.b0_max)))
        return {
            "id": self.id,
            "paths": {
                role: str(path.expanduser().absolute())
                for role, path in self.paths.supplied().items()
            },
            "shape": list(self.shape),
            "storage_dtype": self.storage_dtype,
            "voxel_sizes_native": list(self.voxel_sizes_native),
            "voxel_sizes_mm": json_compatible(self.voxel_sizes_mm()),
            "spatial_units": self.spatial_units,
            "affine": self.affine.tolist(),
            "affine_source": self.affine_source,
            "axis_codes": list(self.axis_codes),
            "n_volumes": self.n_volumes,
            "b0_count": b0_count,
            "weighted_count": self.n_volumes - b0_count,
            "default_preview_volume_index": self.default_preview_volume(),
            "gradient_layout": self.gradients.source_layout,
            "gradient_convention": self.gradients.convention,
            "shells": [
                shell.to_dict()
                for shell in self.gradients.shells(b0_max=policy.b0_max, max_span=policy.shell_span)
            ],
            "metadata": json_compatible(self.metadata),
        }

    @staticmethod
    def _index(value: int, size: int, name: str) -> int:
        """Validate indices strictly; Python negative-index wrapping is not a UI action."""
        if isinstance(value, bool) or not isinstance(value, Integral):
            raise TypeError(f"{name} must be an integer")
        if not 0 <= value < size:
            raise IndexError(f"{name} {value} is outside [0, {size - 1}]")
        return int(value)

    def _check_sources(self) -> None:
        """Invalidate stale reads rather than mixing an old report with new data."""
        for role, fingerprint in self.fingerprints.items():
            path = self.paths.supplied().get(role, Path(fingerprint.path))
            if not fingerprint.unchanged(path):
                raise SourceChangedError(
                    f"The {role} source changed or is unavailable: {path}. Reload the dataset."
                )

    def assert_current(self) -> None:
        """Check that this open snapshot still refers to the loaded source files.

        Workflow planning uses this metadata-only check without reading voxels.
        Fingerprints detect common changes; they are not content checksums.
        """
        # No image-reader lock: a planning screen must not wait for a blocking
        # preview read. This is a snapshot check, not an execution-time lease.
        if self._closed:
            raise DatasetClosedError("The dataset is closed. Reload it before planning.")
        self._check_sources()

    def _read(self, key: tuple[Any, ...], dtype: str) -> NDArray[np.floating]:
        """Read directly through the proxy, scale/cast only that selection, and detach."""
        if dtype not in ("float32", "float64"):
            raise ValueError("dtype must be float32 or float64")
        with self._lock:
            if self._closed:
                raise DatasetClosedError(
                    "Dataset is closed. Load it again before reading image data."
                )
            self._check_sources()
            try:
                # Indexing is intentionally before conversion: np.asarray(proxy)
                # would allocate the whole image. keep_file_open=False in loading
                # ensures the dataset owns no indefinitely open source descriptor.
                scaled = self._image.dataobj[key]
                with np.errstate(over="raise", invalid="raise"):
                    result = np.array(scaled, dtype=dtype, copy=True)
            except (
                OSError,
                EOFError,
                ValueError,
                IndexError,
                FloatingPointError,
                MemoryError,
            ) as exc:
                raise DatasetReadError(f"Could not read requested image selection: {exc}") from exc
            self._check_sources()
            LOGGER.debug(
                "Read selection %s as %s; NIfTI scaling applied; sources unchanged", key, dtype
            )
            return result

    def read_slice(
        self,
        volume_index: int,
        *,
        axis: int = 2,
        index: int | None = None,
        dtype: Literal["float32", "float64"] = "float32",
    ) -> NDArray[np.floating]:
        """Read one native 2D plane, without resampling or a full-volume allocation.

        Axis 2 is a file-axis default, not a claim about anatomical axial planes.
        The future preview uses geometry to select and honestly label its plane.
        """
        axis = self._index(axis, 3, "axis")
        volume_index = self._index(volume_index, self.n_volumes, "volume_index")
        index = (
            self.shape[axis] // 2
            if index is None
            else self._index(index, self.shape[axis], "index")
        )
        key: list[Any] = [slice(None), slice(None), slice(None), volume_index]
        key[axis] = index
        return self._read(tuple(key), dtype)

    def read_block(
        self,
        volume_index: int,
        region: tuple[slice, slice, slice],
        *,
        dtype: Literal["float32", "float64"] = "float32",
    ) -> NDArray[np.floating]:
        """Read a bounded rectangular region; strides/reversed slices are rejected."""
        volume_index = self._index(volume_index, self.n_volumes, "volume_index")
        if len(region) != 3 or not all(isinstance(item, slice) for item in region):
            raise ValueError("region must contain three slices")
        bounded = []
        for item, size in zip(region, self.shape[:3], strict=True):
            if item.step not in (None, 1):
                raise ValueError("region slices must have step 1")
            start, stop = (
                (0 if item.start is None else item.start),
                (size if item.stop is None else item.stop),
            )
            start = self._index(start, size, "region start")
            if isinstance(stop, bool) or not isinstance(stop, Integral) or not start < stop <= size:
                raise IndexError("region stop must be greater than start and within the image")
            bounded.append(slice(start, int(stop)))
        return self._read((*bounded, volume_index), dtype)

    def read_volume(
        self,
        volume_index: int,
        *,
        dtype: Literal["float32", "float64"] = "float32",
    ) -> NDArray[np.floating]:
        """Materialize one explicitly requested 3D volume; the loader does not call it."""
        volume_index = self._index(volume_index, self.n_volumes, "volume_index")
        return self._read((slice(None), slice(None), slice(None), volume_index), dtype)

    def close(self) -> None:
        """Idempotently release the private proxy; previously returned copies survive."""
        with self._lock:
            object.__setattr__(self, "_image", None)
            object.__setattr__(self, "_closed", True)

    def __enter__(self) -> DwiDataset:
        """Support deterministic lifecycle management in scripts."""
        if self._closed:
            raise DatasetClosedError("Dataset is closed")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release image resources even when a caller's analysis raises."""
        self.close()
