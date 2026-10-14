"""Explicit visualization derivatives; never register or modify input files."""

import nibabel as nib
import numpy as np


def resample_overlay(image, reference, order=0):
    """Sample an explicitly declared same-world-space image onto the base grid.

    This is resampling, not registration. Only documented physical coordinate
    systems are permitted across grids. Nearest neighbor preserves label IDs.
    """
    from scipy.ndimage import affine_transform

    if image.count != 1:
        raise ValueError("Choose a 3D overlay/ROI; 4D base images are supported separately")
    if image.shape[:3] == reference.shape[:3] and np.allclose(
        image.image.affine, reference.image.affine
    ):
        if (
            image.physical_units_known
            and reference.physical_units_known
            and not np.allclose(image.affine, reference.affine)
        ):
            raise ValueError("Conflicting spatial units")
        return image.volume()
    if (
        not image.physical_units_known
        or not reference.physical_units_known
        or image.affine_source == "fallback"
        or reference.affine_source == "fallback"
    ):
        raise ValueError("Cross-grid resampling requires documented units and coded affines")
    mapping = np.linalg.inv(image.affine) @ reference.affine
    return affine_transform(
        image.volume(),
        mapping[:3, :3],
        mapping[:3, 3],
        output_shape=reference.shape[:3],
        order=order,
        mode="constant",
        cval=0,
        prefilter=False,
    )


def paint_roi(data, position, axis, radius, value, three_dimensional=False):
    """Native voxel disk/sphere brush; returns a bounded edited view in place."""
    p = np.asarray(position, int)
    lo = np.maximum(p - radius, 0)
    hi = np.minimum(p + radius + 1, data.shape)
    if not three_dimensional:
        lo[axis], hi[axis] = p[axis], p[axis] + 1
    slices = tuple(slice(int(a), int(b)) for a, b in zip(lo, hi, strict=True))
    indices = np.indices(tuple(hi - lo)) + lo[:, None, None, None]
    disk = np.sum((indices - p[:, None, None, None]) ** 2, axis=0) <= radius**2
    data[slices][disk] = value


def polygon_roi(data, vertices, axis, index, value=True):
    """Fill polygon at voxel centers in one native plane using ray crossings."""
    if len(vertices) < 3:
        raise ValueError("Select at least three polygon vertices on the same slice")
    other = [a for a in range(3) if a != axis]
    xy = np.array(vertices)[:, other]
    lo = np.maximum(np.floor(xy.min(axis=0)).astype(int), 0)
    hi = np.minimum(np.ceil(xy.max(axis=0)).astype(int) + 1, np.array(data.shape)[other])
    x, y = np.meshgrid(np.arange(lo[0], hi[0]), np.arange(lo[1], hi[1]), indexing="ij")
    inside = np.zeros(x.shape, bool)
    for a, b in zip(xy, np.roll(xy, -1, axis=0), strict=True):
        if a[1] != b[1]:
            inside ^= ((a[1] > y) != (b[1] > y)) & (
                x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]
            )
    plane = np.take(data, index, axis=axis).copy()
    plane[lo[0] : hi[0], lo[1] : hi[1]][inside] = value
    sl = [slice(None)] * 3
    sl[axis] = index
    data[tuple(sl)] = plane


def read_mesh(path):
    """Read GIFTI physical coordinates or FreeSurfer geometry with volume metadata."""
    path = str(path)
    if path.endswith(".gii"):
        gii = nib.load(path)
        points = gii.get_arrays_from_intent("NIFTI_INTENT_POINTSET")
        triangles = gii.get_arrays_from_intent("NIFTI_INTENT_TRIANGLE")
        if len(points) != 1 or len(triangles) != 1:
            raise ValueError("GIFTI must contain one pointset and one triangle array")
        coords = points[0].coordsys
        if coords is None or coords.xformspace == 0:
            raise ValueError(
                "GIFTI coordinate system is unspecified; supply a mesh in documented scanner/world space"
            )
        vertices = nib.affines.apply_affine(coords.xform, points[0].data)
        faces = triangles[0].data
    else:
        vertices, faces, info = nib.freesurfer.read_geometry(path, read_metadata=True)
        required = {"volume", "voxelsize", "xras", "yras", "zras", "cras"}
        if not required.issubset(info):
            raise ValueError(
                "FreeSurfer surface lacks volume geometry required for scanner-space conversion"
            )
        affine = np.eye(4)
        affine[:3, :3] = (
            np.column_stack([info[k] for k in ["xras", "yras", "zras"]]) * info["voxelsize"]
        )
        affine[:3, 3] = info["cras"] - affine[:3, :3] @ (np.asarray(info["volume"]) / 2)
        hdr = nib.freesurfer.mghformat.MGHHeader()
        hdr.set_data_shape(tuple(info["volume"]))
        hdr.set_zooms(tuple(info["voxelsize"]))
        vertices = nib.affines.apply_affine(affine @ np.linalg.inv(hdr.get_vox2ras_tkr()), vertices)
    if (
        not np.isfinite(vertices).all()
        or faces.ndim != 2
        or faces.shape[1] != 3
        or np.any(faces < 0)
        or np.any(faces >= len(vertices))
    ):
        raise ValueError("Invalid mesh coordinates/triangles")
    return np.asarray(vertices, np.float32), np.asarray(faces, np.int64)


def projected_selection(group, projection, start, end, rectangle=False, angle=90, normal=None):
    """Select projected segment intersections in normalized display coordinates.

    Projection is a frozen camera matrix; this scans all indexed fibers in a
    worker. A drag plane is finite along the gesture and extends through depth.
    """
    start, end = np.asarray(start), np.asarray(end)
    direction = end - start
    selected = set()
    for i in range(len(group.streamlines)):
        if i in group.deleted or i in group.hidden:
            continue
        line = group.line(i)
        clip = np.column_stack((line, np.ones(len(line)))) @ projection.T
        valid = clip[:, 3] > 1e-9
        xy = clip[:, :2] / np.maximum(clip[:, 3, None], 1e-9)
        a, b = xy[:-1], xy[1:]
        if rectangle:
            # Segment/rectangle slab intersection, including crossing-only fibers.
            lo, hi = np.minimum(start, end), np.maximum(start, end)
            delta = b - a
            with np.errstate(divide="ignore", invalid="ignore"):
                t0 = (lo - a) / delta
                t1 = (hi - a) / delta
            parallel = np.abs(delta) < 1e-12
            outside = parallel & ((a < lo) | (a > hi))
            near = np.where(parallel, -np.inf, np.minimum(t0, t1)).max(axis=1)
            far = np.where(parallel, np.inf, np.maximum(t0, t1)).min(axis=1)
            hit = (np.maximum(near, 0) <= np.minimum(far, 1)) & ~outside.any(axis=1)
        else:
            delta = b - a

            def cross(u, v):
                return u[..., 0] * v[..., 1] - u[..., 1] * v[..., 0]

            den = cross(delta, direction)
            safe = np.where(np.abs(den) < 1e-12, np.nan, den)
            t = cross(start - a, direction) / safe
            u = cross(start - a, delta) / safe
            hit = (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1)
        hit &= valid[:-1] & valid[1:]
        if normal is not None and angle < 90:
            tangent = np.diff(line, axis=0)
            cosine = np.abs(tangent @ normal) / np.maximum(
                np.linalg.norm(tangent, axis=1) * np.linalg.norm(normal), 1e-12
            )
            hit &= cosine >= np.cos(np.deg2rad(angle))
        if hit.any():
            selected.add(i)
    return selected
