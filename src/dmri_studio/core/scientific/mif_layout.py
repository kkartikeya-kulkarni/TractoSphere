"""Lossless, bounded MIF storage-layout preparation for response scripts.

Upstream response estimation forces channel-fast storage. Converting a large
volume-major image voxel-by-voxel can thrash mapped pages on 16GB workstations.
This is only a storage permutation, never a scientific resampling/conversion:
copy one bounded z slab through RAM, preserve every Float32 value, affine,
gradient and metadata, and independently verify the written payload SHA256.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from .._mif_checks import produced_mif
from ..process import check_cancel


def prepare_response_layout(
    source: Path, destination: Path, *, cancelled=lambda: False, progress=lambda _: None
) -> dict:
    """Write a new component-fast derivative without touching its source.

    At most about 128MiB is allocated for the transposition buffer. File-order
    payload blocks have axes z,y,x,component; the MIF logical axes remain x,y,z,c.
    The upstream script still executes all scientific response computations.
    """
    offset = 1048576
    expected = hashlib.sha256()
    with produced_mif(source) as (fields, affine, values):
        if values.ndim != 4 or fields["datatype"] not in (["Float32LE"], ["Float32BE"]):
            raise ValueError("Response layout preparation requires a 4D Float32 MIF")
        nx, ny, nz, nc = values.shape
        limit = 128 * 1024**2
        if nx * nc * 4 > limit:
            raise ValueError("One response-layout row exceeds the bounded 128MiB buffer")
        height = min(ny, max(1, limit // (nx * nc * 4)))
        depth = max(1, min(nz, limit // (nx * ny * nc * 4)))
        header = ["mrtrix image"]
        for key, entries in fields.items():
            if key not in {"layout", "file", "datatype"}:
                header.extend(key + ": " + value for value in entries)
        header += [
            "layout: +1,+2,+3,+0",
            "datatype: Float32LE",
            f"file: . {offset}",
            "tractosphere_storage: lossless bounded response layout copy; no signal, gradient or geometry changes",
            "END",
            "",
        ]
        encoded = "\n".join(header).encode("utf-8")
        if len(encoded) >= offset:
            raise ValueError("Prepared MIF header exceeds reserved bounds")
        with destination.open("xb") as stream:
            stream.write(encoded)
            stream.seek(offset)
            for z in range(0, nz, depth):
                check_cancel(cancelled)
                stop = min(z + depth, nz)
                for y in range(0, ny, height):
                    top = min(y + height, ny)
                    block = np.empty((stop - z, top - y, nx, nc), dtype="<f4")
                    for volume in range(nc):
                        check_cancel(cancelled)
                        block[..., volume] = np.asarray(values[:, y:top, z:stop, volume]).transpose(
                            2, 1, 0
                        )
                    payload = memoryview(block).cast("B")
                    expected.update(payload)
                    stream.write(payload)
                progress(f"Preparing lossless response storage: {stop}/{nz} slices copied")
        # Read the actual file independently, rather than trust the write call
        # or a cached array view as complete payload verification.
        actual = hashlib.sha256()
        with destination.open("rb") as stream:
            stream.seek(offset)
            while chunk := stream.read(8 * 1024**2):
                check_cancel(cancelled)
                actual.update(chunk)
        if actual.digest() != expected.digest():
            raise ValueError("Prepared response payload integrity failed")
        with produced_mif(destination) as (output_fields, output_affine, output):
            if output.shape != values.shape or not np.array_equal(output_affine, affine):
                raise ValueError("Prepared response geometry changed")
            if output_fields.get("dw_scheme") != fields.get("dw_scheme"):
                raise ValueError("Prepared response gradient table changed")
    return dict(
        kind="lossless_MIF_storage_layout",
        payload_sha256=actual.hexdigest(),
        source=str(source),
        destination=str(destination),
        shape=list(output.shape),
        maximum_buffer_bytes=depth * nx * height * nc * 4,
        complete_payload_verified=True,
        signal_changes=False,
        gradient_changes=False,
        geometry_changes=False,
    )
