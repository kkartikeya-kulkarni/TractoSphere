"""Read-only NIfTI structure and geometry checks for the core loader.

The fixed header is inspected before NiBabel's normal loading path can repair
fields. Extensions are checked within their declared envelope without reading
voxel payloads. This module never materializes an image array or writes a file.
"""

from __future__ import annotations

import gzip
import itertools
import math
import struct
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import nibabel as nib
import numpy as np
from nibabel.spatialimages import HeaderDataError, HeaderTypeError

from .validation import CheckResult


@dataclass
class NiftiInspection:
    """Carry successfully decoded information even when another check fails."""

    checks: list[CheckResult] = field(default_factory=list)
    image: Any | None = None
    shape: tuple[int, ...] | None = None
    voxel_sizes_native: tuple[float, float, float] | None = None
    spatial_units: str = "unknown"
    affine: np.ndarray | None = None
    affine_source: str = "unavailable"
    axis_codes: tuple[str | None, ...] = (None, None, None)
    metadata: dict[str, Any] = field(default_factory=dict)


def _extension_envelope(
    stream: BinaryIO, header: Any, endian: str, checkpoint: Callable[[], None]
) -> None:
    """Verify extension record sizes cannot overlap the voxel payload.

    NiBabel's extension reader is deliberately permissive about record sizes.
    We establish boundaries before calling that reader, and seek over content
    here rather than retaining a second copy of potentially large metadata.
    """
    offset = int(header["vox_offset"])
    flag = stream.read(4)
    if len(flag) != 4:
        raise ValueError("The four-byte extension flag is truncated.")
    if flag[0] == 0:
        return
    cursor = stream.tell()
    if offset - cursor < 16:
        raise ValueError("Extensions are declared but there is no room before the data offset.")
    while cursor < offset:
        checkpoint()
        if offset - cursor < 16:
            raise ValueError("An incomplete extension record precedes the data offset.")
        block = stream.read(8)
        if len(block) != 8:
            raise ValueError("An extension header is truncated.")
        size, _code = struct.unpack(endian + "ii", block)
        if size < 16 or size % 16 or cursor + size > offset:
            raise ValueError(
                f"Extension size {size} at offset {cursor} is invalid or overlaps voxels."
            )
        # Reading its final byte catches a truncated metadata record even for
        # uncompressed streams, whose seek operation alone can pass EOF.
        stream.seek(cursor + size - 1)
        if len(stream.read(1)) != 1:
            raise ValueError("An extension payload is truncated.")
        cursor += size


def _valid_affine(matrix: np.ndarray) -> bool:
    """Check finite homogeneous geometry, using NumPy's numerical rank test."""
    return bool(
        matrix.shape == (4, 4)
        and np.isfinite(matrix).all()
        and np.allclose(matrix[3], [0, 0, 0, 1], rtol=0, atol=1e-6)
        and np.linalg.matrix_rank(matrix[:3, :3]) == 3
    )


def _geometry(header: Any, result: NiftiInspection, source: str) -> None:
    """Validate every coded transform, then select and compare its geometry."""
    forms: dict[str, np.ndarray] = {}
    codes = {name: int(header[f"{name}_code"]) for name in ("qform", "sform")}
    result.metadata["form_codes"] = codes
    result.metadata.update(qform_code=codes["qform"], sform_code=codes["sform"])
    valid = True
    for name, code in codes.items():
        if code not in nib.nifti1.xform_codes.value_set():
            result.checks.append(
                CheckResult("A01", "fail", f"Unrecognized {name} code {code}.", source=source)
            )
            valid = False
            continue
        if code == 0:
            continue
        try:
            matrix, _ = getattr(header, f"get_{name}")(coded=True)
            if matrix is None or not _valid_affine(matrix):
                raise ValueError("matrix is nonfinite, singular, or nonhomogeneous")
            forms[name] = matrix
            result.checks.append(
                CheckResult("A01", "pass", f"Active {name} is valid.", source=source)
            )
        except (
            ValueError,
            TypeError,
            HeaderDataError,
            HeaderTypeError,
            np.linalg.LinAlgError,
        ) as exc:
            valid = False
            result.checks.append(
                CheckResult(
                    "A01",
                    "fail",
                    f"Active {name} is invalid: {exc}",
                    source=source,
                    remediation="Correct the transform in an external copy; this loader does not repair headers.",
                )
            )
    if not valid:
        return
    if not any(codes.values()):
        result.checks.append(
            CheckResult("A01", "pass", "No active transforms require validation.", source=source)
        )
    try:
        result.affine_source = (
            "sform" if codes["sform"] else "qform" if codes["qform"] else "fallback"
        )
        matrix = header.get_best_affine()
        if not _valid_affine(matrix):
            raise ValueError("selected/fallback geometry is not usable")
        result.affine = matrix
        result.checks.append(
            CheckResult(
                "A02",
                "warn" if result.affine_source == "fallback" else "pass",
                "No coded transform; fallback anatomical orientation is unverified."
                if result.affine_source == "fallback"
                else f"Selected coded {result.affine_source}.",
                source=source,
            )
        )
    except (ValueError, TypeError, HeaderDataError, HeaderTypeError, np.linalg.LinAlgError) as exc:
        result.checks.append(
            CheckResult("A02", "fail", f"Cannot select usable geometry: {exc}", source=source)
        )
        return
    if len(forms) == 2 and result.shape is not None:
        factor = {"mm": 1.0, "meter": 1000.0, "micron": 0.001}.get(result.spatial_units)
        if factor is not None:
            corners = np.array(
                list(itertools.product(*[(0, d - 1) for d in result.shape[:3]])), dtype=np.float64
            )
            points = np.column_stack((corners, np.ones(8)))
            displacement = (points @ forms["qform"].T - points @ forms["sform"].T)[:, :3]
            maximum = float(np.linalg.norm(displacement, axis=1).max() * factor)
            agrees = maximum <= 0.5
            observed: Any = {"maximum_corner_displacement_mm": maximum}
            expected: Any = "maximum corner displacement <= 0.5 mm"
        else:
            agrees = bool(np.allclose(forms["qform"], forms["sform"], rtol=1e-5, atol=1e-5))
            observed, expected = None, "matrix allclose rtol=1e-5, atol=1e-5 in native units"
        result.checks.append(
            CheckResult(
                "A03",
                "pass" if agrees else "warn",
                "qform and sform agree within the comparison tolerance."
                if agrees
                else "qform and sform disagree; they may intentionally describe different spaces. Selected form retained.",
                source=source,
                observed=observed,
                expected=expected,
            )
        )
    else:
        result.checks.append(
            CheckResult(
                "A03",
                "skipped",
                "Two valid coded transforms and valid dimensions are needed for comparison.",
                source=source,
            )
        )
    if result.voxel_sizes_native is not None:
        lengths = np.linalg.norm(result.affine[:3, :3], axis=0)
        agrees = bool(np.allclose(lengths, result.voxel_sizes_native, rtol=1e-3, atol=1e-6))
        result.checks.append(
            CheckResult(
                "A04",
                "pass" if agrees else "warn",
                "Affine column lengths agree with declared spacing."
                if agrees
                else "Affine column lengths differ from declared spacing; both are retained.",
                source=source,
                observed=lengths.tolist(),
                expected=list(result.voxel_sizes_native),
            )
        )
    if result.affine_source != "fallback":
        result.axis_codes = nib.aff2axcodes(result.affine)
    result.metadata.update(
        affine=result.affine.tolist(),
        affine_source=result.affine_source,
        axis_codes=result.axis_codes,
        orientation_from_coded_transform=result.affine_source != "fallback",
    )


def inspect_nifti(path: Path, *, checkpoint: Callable[[], None]) -> NiftiInspection:
    """Run F04, N01–N07 and A01–A04, returning a lazy image only on success."""
    result = NiftiInspection()
    source = str(path)
    lower = path.name.lower()
    if not (lower.endswith(".nii") or lower.endswith(".nii.gz")):
        result.checks.append(
            CheckResult("F04", "fail", "DWI filename must end in .nii or .nii.gz.", source=source)
        )
        return result
    compressed = lower.endswith(".gz")
    opener = gzip.open if compressed else open
    try:
        with opener(path, "rb") as stream:
            prefix = stream.read(4)
            if len(prefix) != 4:
                raise ValueError("The fixed NIfTI header is truncated.")
            candidates = [(endian, struct.unpack(endian + "i", prefix)[0]) for endian in ("<", ">")]
            candidate = next(((e, n) for e, n in candidates if n in (348, 540)), None)
            if candidate is None:
                raise ValueError("Content does not identify a NIfTI-1 or NIfTI-2 header.")
            endian, length = candidate
            raw_bytes = prefix + stream.read(length - 4)
            if len(raw_bytes) != length:
                raise ValueError("The fixed NIfTI header is truncated.")
            header_class = nib.Nifti1Header if length == 348 else nib.Nifti2Header
            header = header_class(raw_bytes, endianness=endian, check=False)
            if header["magic"].item() != header.single_magic:
                raise ValueError(
                    "Only single-file NIfTI images are supported; invalid/paired magic found."
                )
            # CIFTI carries domain-specific axes, even though its container is NIfTI-2.
            if int(header["intent_code"]) >= 3000:
                raise ValueError(
                    "CIFTI/connectivity image intents are unsupported for DWI loading."
                )
            if length == 540 and not np.array_equal(header["eol_check"], [13, 10, 26, 10]):
                raise ValueError("The NIfTI-2 end-of-line marker is invalid.")
            result.checks.append(
                CheckResult(
                    "F04",
                    "pass",
                    f"Single-file NIfTI-{1 if length == 348 else 2} header recognized.",
                    source=source,
                )
            )
            result.metadata["nifti_version"] = 1 if length == 348 else 2

            raw_dim = np.asarray(header["dim"])
            if int(raw_dim[0]) == 4 and np.all(raw_dim[1:5] > 0):
                result.shape = tuple(int(v) for v in raw_dim[1:5])
                if tuple(header.get_data_shape()) != result.shape:
                    result.checks.append(
                        CheckResult(
                            "N01", "fail", "Raw and interpreted dimensions disagree.", source=source
                        )
                    )
                else:
                    result.checks.append(
                        CheckResult(
                            "N01",
                            "pass",
                            "Exactly four positive dimensions declared.",
                            source=source,
                            observed=list(result.shape),
                        )
                    )
            else:
                result.checks.append(
                    CheckResult(
                        "N01",
                        "fail",
                        "DWI must declare exactly four positive dimensions.",
                        source=source,
                        observed=raw_dim.tolist(),
                        expected="X × Y × Z × N, all > 0",
                    )
                )
            dtype = None
            try:
                dtype = header.get_data_dtype()
                incompatible_intents = set(range(1004, 1011)) | {2002, 2003, 2004, 2005}
                if (
                    dtype.kind not in "iuf"
                    or dtype.fields
                    or int(header["bitpix"]) != dtype.itemsize * 8
                ):
                    raise ValueError("datatype is not a real scalar or bitpix contradicts its size")
                if int(header["intent_code"]) in incompatible_intents:
                    raise ValueError("intent explicitly describes an incompatible non-DWI object")
                result.checks.append(
                    CheckResult(
                        "N02",
                        "pass",
                        "Real scalar datatype and bit width agree.",
                        source=source,
                        observed=str(dtype),
                    )
                )
            except (ValueError, KeyError, TypeError, HeaderDataError, HeaderTypeError) as exc:
                result.checks.append(
                    CheckResult(
                        "N02", "fail", f"Unsupported datatype/header intent: {exc}", source=source
                    )
                )
            sizes = tuple(float(v) for v in header["pixdim"][1:4])
            if all(math.isfinite(v) and v > 0 for v in sizes):
                result.voxel_sizes_native = sizes
                result.checks.append(
                    CheckResult(
                        "N03",
                        "pass",
                        "Raw spatial voxel sizes are positive and finite.",
                        source=source,
                        observed=list(sizes),
                    )
                )
            else:
                result.checks.append(
                    CheckResult(
                        "N03",
                        "fail",
                        "Raw spatial voxel sizes must be positive and finite; no repair applied.",
                        source=source,
                        observed=list(sizes),
                    )
                )
            unit_code = int(header["xyzt_units"]) & 7
            result.spatial_units = {1: "meter", 2: "mm", 3: "micron"}.get(unit_code, "unknown")
            factor = {"mm": 1.0, "meter": 1000.0, "micron": 0.001}.get(result.spatial_units)
            result.checks.append(
                CheckResult(
                    "N04",
                    "warn" if factor is None else "pass" if factor == 1 else "info",
                    "Spatial units unknown; no millimetre assumption made."
                    if factor is None
                    else f"Native spatial units: {result.spatial_units}; metadata conversion factor to mm: {factor:g}.",
                    source=source,
                    observed=unit_code,
                )
            )
            result.metadata.update(
                shape=result.shape,
                storage_dtype=str(dtype) if dtype is not None else None,
                byte_order=header.endianness,
                voxel_sizes_native=sizes,
                voxel_sizes_mm=[v * factor for v in sizes]
                if factor is not None and result.voxel_sizes_native is not None
                else None,
                spatial_units=result.spatial_units,
                spatial_units_to_mm=factor,
            )
            raw_slope, raw_intercept = float(header["scl_slope"]), float(header["scl_inter"])
            inactive = raw_slope == 0 or math.isnan(raw_slope)
            if math.isinf(raw_slope) or (not inactive and not math.isfinite(raw_intercept)):
                result.checks.append(
                    CheckResult(
                        "N06",
                        "fail",
                        "Raw intensity scaling is invalid.",
                        source=source,
                        observed={"slope": raw_slope, "intercept": raw_intercept},
                    )
                )
            else:
                slope, intercept = (1.0, 0.0) if inactive else (raw_slope, raw_intercept)
                result.metadata["scaling"] = {
                    "slope": slope,
                    "intercept": intercept,
                    "raw_scaling_undefined": inactive,
                }
                result.checks.append(
                    CheckResult(
                        "N06",
                        "info" if inactive or (slope, intercept) != (1, 0) else "pass",
                        "Undefined source scaling is interpreted as identity; intercept ignored."
                        if inactive
                        else f"Effective intensity = stored value × {slope:g} + {intercept:g}; source is unchanged.",
                        source=source,
                    )
                )
            try:
                # NIfTI-1 stores an offset as float32: inspect it BEFORE int().
                offset_value = header["vox_offset"].item()
                if (
                    not math.isfinite(offset_value)
                    or int(offset_value) != offset_value
                    or offset_value < header.single_vox_offset
                ):
                    raise ValueError(
                        "data offset must be an integer after the fixed header and extension flag"
                    )
                offset = int(offset_value)
                _extension_envelope(stream, header, endian, checkpoint)
                result.metadata["data_offset"] = offset
                result.checks.append(
                    CheckResult(
                        "N05",
                        "pass",
                        "Data offset and extension boundaries are valid.",
                        source=source,
                        observed=offset,
                    )
                )
                if compressed:
                    result.checks.append(
                        CheckResult(
                            "N05",
                            "skipped",
                            "Compressed file size cannot establish uncompressed payload length.",
                            source=source,
                        )
                    )
                elif result.shape is not None and dtype is not None:
                    minimum = offset + math.prod(result.shape) * dtype.itemsize
                    size = path.stat().st_size
                    result.checks.append(
                        CheckResult(
                            "N05",
                            "pass" if size >= minimum else "fail",
                            "Uncompressed payload length is sufficient."
                            if size >= minimum
                            else "Uncompressed voxel payload is truncated.",
                            source=source,
                            observed=size,
                            expected={"minimum_bytes": minimum},
                        )
                    )
                else:
                    result.checks.append(
                        CheckResult(
                            "N05",
                            "skipped",
                            "Payload length requires valid shape and datatype.",
                            source=source,
                        )
                    )
            except (OSError, EOFError, ValueError, OverflowError, struct.error) as exc:
                result.checks.append(
                    CheckResult(
                        "N05",
                        "fail",
                        f"Invalid data offset/extension envelope: {exc}",
                        source=source,
                    )
                )
            _geometry(header, result, source)
            try:
                diagnostic = header_class.diagnose_binaryblock(raw_bytes, endianness=endian)
            except (
                ValueError,
                KeyError,
                TypeError,
                OverflowError,
                HeaderDataError,
                HeaderTypeError,
            ) as exc:
                diagnostic = f"Header diagnostics could not complete: {exc}"
            if diagnostic:
                result.checks.append(
                    CheckResult(
                        "N07",
                        "warn",
                        "NiBabel raw-header diagnostics: " + diagnostic,
                        source=source,
                    )
                )
            else:
                result.checks.append(
                    CheckResult(
                        "N07",
                        "pass",
                        "No additional NiBabel fixed-header diagnostics.",
                        source=source,
                    )
                )
    except (
        OSError,
        EOFError,
        ValueError,
        KeyError,
        TypeError,
        struct.error,
        HeaderDataError,
        HeaderTypeError,
    ) as exc:
        result.checks.append(
            CheckResult(
                "F04",
                "fail",
                f"Cannot read supported NIfTI metadata: {exc}",
                source=source,
                remediation="Select a readable single-file 4D NIfTI DWI image.",
            )
        )
        return result
    if any(check.status == "fail" for check in result.checks):
        return result
    checkpoint()
    try:
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always")
            image = nib.load(path, mmap="r", keep_file_open=False)
        if type(image) not in (nib.Nifti1Image, nib.Nifti2Image):
            raise ValueError(f"Unsupported image class: {type(image).__name__}.")
        expected_scaling = result.metadata["scaling"]
        critical_matches = (
            tuple(image.shape) == result.shape
            and image.get_data_dtype() == dtype
            and image.dataobj.offset == result.metadata["data_offset"]
            and tuple(float(v) for v in image.header["pixdim"][1:4]) == result.voxel_sizes_native
            and image.dataobj.slope == expected_scaling["slope"]
            and image.dataobj.inter == expected_scaling["intercept"]
            and np.allclose(image.affine, result.affine, rtol=0, atol=0)
        )
        if not critical_matches:
            raise ValueError(
                "NiBabel's interpreted shape, spacing, dtype, offset, affine or scaling differs from the validated raw header."
            )
        changed = {
            key: {
                "raw": np.asarray(header[key]).tolist(),
                "interpreted": np.asarray(image.header[key]).tolist(),
            }
            for key in ("pixdim", "qform_code", "sform_code")
            if not np.array_equal(header[key], image.header[key], equal_nan=True)
        }
        if emitted or changed:
            result.checks.append(
                CheckResult(
                    "N07",
                    "warn",
                    "Additional NiBabel interpretations/diagnostics were recorded; source header is unchanged.",
                    source=source,
                    observed={"fields": changed, "warnings": [str(w.message) for w in emitted]},
                )
            )
        result.image = image
    except (
        OSError,
        EOFError,
        ValueError,
        KeyError,
        TypeError,
        HeaderDataError,
        HeaderTypeError,
        nib.filebasedimages.ImageFileError,
    ) as exc:
        result.checks.append(
            CheckResult(
                "N07", "fail", f"Cannot construct a consistent lazy image: {exc}", source=source
            )
        )
    return result
