"""One conservative registration reservation model, in bytes (GiB = 2**30).

This is admission budgeting, NOT measured peak RSS. FNIRT reserves 1500 bytes
per voxel of the largest source/reference/intermediate grid, plus decoded image
buffers and 256 MiB process overhead. The 1500 coefficient retains the existing
nonlinear contract and includes multiresolution images, warp/coefficient/Jacobian
fields and solver workspace; no reduction is assumed from compressed file size
or a coarse warp lattice. All image grids are included, even when they differ.
A 512 MiB floor covers process overhead for bounded tiny fixtures; unlike the
old fixed 4 GiB batch floor, realistic nonlinear grids dominate. Other operations reserve
32 bytes per decoded scalar and a 512 MiB floor. Low-b extraction additionally
reserves the selected volume buffer. Unmaterialized FNIRT dependencies reserve
8 GiB provisionally; execution MUST recompute and reject underestimated plans.
No estimate is capped downward. Overrides may increase but never reduce minima.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import nibabel as nib
import psutil

GIB = 1024**3
MODEL = "tractosphere.registration-resources/2"
RAM_HEADROOM_BYTES = 512 * 1024**2
DISK_HEADROOM_BYTES = 5 * GIB


def registration_resources(tasks, artifacts=()):
    """Compute identical minima for preparation, batch admission and recovery."""
    tasks = [t.to_dict() if hasattr(t, "to_dict") else t for t in tasks]
    nonlinear = any(t["algorithm"] == "registration.fnirt" for t in tasks)
    grids = []
    decoded = 0
    seen = set()

    def grid(shape, key, kind="", gradients=None):
        nonlocal decoded
        if not shape or key in seen:
            return
        if len(shape) < 3 or any(type(v) is not int or v < 1 for v in shape):
            raise ValueError("Registration resources require positive image dimensions")
        seen.add(key)
        voxels = math.prod(shape[:3])
        grids.append(dict(identity=key, shape=list(shape), voxels=voxels))
        if kind == "dwi":
            params = next(
                (
                    t.get("parameters", {})
                    for t in tasks
                    if t["algorithm"] == "registration.reference"
                ),
                {},
            )
            count = sum(
                b <= params.get("b0_threshold", 50) for b in (gradients or {}).get("bvals", [])
            )
            if params.get("selection") == "first_low_b":
                count = min(count, 1)
            decoded += voxels * (count * 4 + 64)
        else:
            decoded += math.prod(shape) * 32

    def frames(value):
        if isinstance(value, dict):
            if "image" in value and "sha256" in value and Path(value["image"]).is_file():
                image = nib.load(value["image"])
                grid(list(image.shape), str(Path(value["image"]).resolve()))
            for nested in value.values():
                frames(nested)
        elif isinstance(value, list):
            for nested in value:
                frames(nested)

    for index, artifact in enumerate(artifacts):
        a = artifact.to_dict() if hasattr(artifact, "to_dict") else artifact
        grid(
            (a.get("geometry") or {}).get("shape"),
            a.get("path", str(index)),
            a.get("kind"),
            a.get("gradients"),
        )
        if a.get("kind") == "spatial_transform":
            frames(a.get("metadata", {}))
    largest = max((g["voxels"] for g in grids), default=0)
    memory = (
        max(512 * 1024**2, largest * 1500 + decoded + 256 * 1024**2)
        if nonlinear
        else max(512 * 1024**2, decoded)
    )
    if nonlinear and not grids:
        memory = 8 * GIB
    return dict(
        memory_bytes=memory,
        disk_bytes=max(256 * 1024**2, memory if nonlinear else decoded),
        threads=1,
        model=MODEL,
        grids=grids,
        largest_grid_voxels=largest,
        decoded_buffer_bytes=decoded,
        estimate_not_measurement=True,
        unresolved_geometry=not bool(grids),
        ram_headroom_bytes=RAM_HEADROOM_BYTES,
        disk_headroom_bytes=DISK_HEADROOM_BYTES,
    )


def require_registration_resources(reservation, output):
    """Refuse execution if current available RAM/disk cannot meet the estimate."""
    if (
        psutil.virtual_memory().available
        < reservation["memory_bytes"] + reservation["ram_headroom_bytes"]
    ):
        raise ValueError(
            "Insufficient available RAM for registration resource reservation plus headroom"
        )
    if (
        shutil.disk_usage(Path(output).parent).free
        < reservation["disk_bytes"] + reservation["disk_headroom_bytes"]
    ):
        raise ValueError("Registration requires estimated scratch plus 5 GiB free headroom")
