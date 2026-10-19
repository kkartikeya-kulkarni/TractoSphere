"""Bounded integrity checks for files produced by our MRtrix adapters.

This is deliberately not a universal MIF importer/viewer. Only uncompressed,
single-file 3D/4D Float32LE/BE and binary outputs with identity scaling qualify.
Signed strides and physical voxel positions are checked, not just dimensions.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from itertools import product
from pathlib import Path

import numpy as np


@contextmanager
def produced_mif(path: Path, *, allow_integer=False):
    """Yield header, geometry and a read-only array; release its mapping on exit."""
    fields = {}
    with path.open("rb") as handle:
        if handle.readline() != b"mrtrix image\n":
            raise ValueError("Invalid MRtrix image magic.")
        while handle.tell() < 1_048_576:
            line = handle.readline(16384)
            if not line or len(line) == 16384:
                raise ValueError("Truncated or oversized MIF header.")
            text = line.decode("utf-8").strip()
            if text == "END":
                break
            key, value = text.split(":", 1)
            fields.setdefault(key, []).append(value.strip())
        else:
            raise ValueError("MIF header exceeds 1 MiB.")
        header_end = handle.tell()

    def one(key: str) -> str:
        """Required singular fields cannot be ambiguous or duplicated."""
        values = fields.get(key, [])
        if len(values) != 1:
            raise ValueError(f"Expected one MIF {key} field.")
        return values[0]

    shape = tuple(int(n) for n in one("dim").split(","))
    if len(shape) not in {3, 4} or min(shape) < 1:
        raise ValueError("Only positive 3D/4D produced MIF dimensions are supported.")
    vox = np.array([float(v) for v in one("vox").split(",")])
    if vox.size != len(shape) or not np.isfinite(vox[:3]).all() or min(vox[:3]) <= 0:
        raise ValueError("Invalid MIF spatial spacing.")
    layout = one("layout").split(",")
    if len(layout) != len(shape) or sorted(abs(int(v)) for v in layout) != list(range(len(shape))):
        raise ValueError("Invalid MIF stride layout.")
    spec = one("file").split()
    if len(spec) != 2 or spec[0] != ".":
        raise ValueError("External/compressed MIF payloads are outside this adapter contract.")
    offset = int(spec[1])
    if offset < header_end:
        raise ValueError("MIF payload overlaps its header.")
    if "scaling" in fields and one("scaling").replace(" ", "") not in {"0,1", "0.0,1.0"}:
        raise ValueError("Nonidentity MIF scaling is outside this output contract.")
    affine = np.eye(4)
    affine[:3] = [[float(v) for v in row.split(",")] for row in fields["transform"]]
    affine[:3, :3] *= vox[:3]
    if not np.isfinite(affine).all() or abs(np.linalg.det(affine[:3, :3])) < 1e-12:
        raise ValueError("Invalid MIF geometry.")
    datatype = one("datatype")
    count = math.prod(shape)
    if datatype == "Bit":
        size = (count + 7) // 8
        dtype = np.dtype("uint8")
    elif datatype in {"Float32LE", "Float32BE"}:
        size = count * 4
        dtype = np.dtype("<f4" if datatype.endswith("LE") else ">f4")
    elif allow_integer and datatype in {
        "Int32LE",
        "Int32BE",
        "UInt32LE",
        "UInt32BE",
        "Int16LE",
        "Int16BE",
        "UInt16LE",
        "UInt16BE",
        "UInt8",
        "Int8",
    }:
        codes = {
            "Int32": "i4",
            "UInt32": "u4",
            "Int16": "i2",
            "UInt16": "u2",
            "Int8": "i1",
            "UInt8": "u1",
        }
        stem = datatype[:-2] if datatype.endswith(("LE", "BE")) else datatype
        dtype = np.dtype((">" if datatype.endswith("BE") else "<") + codes[stem])
        size = count * dtype.itemsize
    else:
        raise ValueError(f"Unsupported produced MIF datatype: {datatype}")
    if path.stat().st_size != offset + size:
        raise ValueError("Truncated or unexpected MIF payload size.")
    mapping = np.memmap(path, mode="r", dtype=dtype, offset=offset, shape=(size // dtype.itemsize,))
    try:
        # MRtrix Bit stores the first voxel in the most significant bit. This is
        # independent of byte endianness for multibyte floating-point datatypes.
        data = np.unpackbits(mapping, bitorder="big")[:count] if datatype == "Bit" else mapping
        strides = [0] * len(shape)
        step = data.dtype.itemsize
        start = 0
        for axis in sorted(range(len(shape)), key=lambda i: abs(int(layout[i]))):
            negative = layout[axis].startswith("-")
            strides[axis] = -step if negative else step
            if negative:
                start += (shape[axis] - 1) * step
            step *= shape[axis]
        array = np.ndarray(shape, dtype=data.dtype, buffer=data, offset=start, strides=strides)
        array.setflags(write=False)
        yield fields, affine, array
    finally:
        mapping._mmap.close()


def voxel_mapping(
    source_affine: np.ndarray,
    source_shape: tuple,
    target_affine: np.ndarray,
    target_shape: tuple,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Permit only a geometry-preserving permutation/flip, never interpolation.

    Return input axes in output order and output axes requiring a flip. Checking
    the full homogeneous matrix also verifies the endpoint translations.
    """
    mapping = np.linalg.inv(source_affine) @ target_affine
    axes, signs = [], []
    expected = np.eye(4)
    expected[:3] = 0
    for output_axis in range(3):
        input_axis = int(np.argmax(np.abs(mapping[:3, output_axis])))
        sign = 1 if mapping[input_axis, output_axis] > 0 else -1
        axes.append(input_axis)
        signs.append(sign)
        expected[input_axis, output_axis] = sign
        expected[input_axis, 3] = source_shape[input_axis] - 1 if sign < 0 else 0
    if len(set(axes)) != 3 or not np.allclose(mapping, expected, atol=1e-4, rtol=0):
        raise ValueError("Output geometry requires interpolation or differs from the input grid.")
    if tuple(source_shape[i] for i in axes) != tuple(target_shape[:3]):
        raise ValueError("Output dimensions do not match physically corresponding voxels.")
    corners = np.array([(*point, 1) for point in product(*[(0, n - 1) for n in target_shape[:3]])])
    expected_world = corners @ (source_affine @ expected).T
    actual_world = corners @ target_affine.T
    if float(np.linalg.norm(expected_world[:, :3] - actual_world[:, :3], axis=1).max()) > 0.01:
        raise ValueError("Physically matched output corners differ by more than 0.01 mm.")
    return tuple(axes), tuple(i for i, sign in enumerate(signs) if sign < 0)


def rearrange(array: np.ndarray, axes: tuple, flips: tuple) -> np.ndarray:
    """Reindex an array without interpolation or numerical changes."""
    result = array.transpose(axes)
    for axis in flips:
        result = np.flip(result, axis)
    return result
