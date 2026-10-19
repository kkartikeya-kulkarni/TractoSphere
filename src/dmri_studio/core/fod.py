"""Read-only MRtrix3 real-even SH visualization, independent of Qt and VTK.

SH directions are scanner/world RAS, not voxel axes. Radial display truncation
never changes coefficients or the untruncated amplitude statistics. All extraction
and mesh allocations have explicit limits; no full-brain glyph volume is built.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import nibabel as nib
import numpy as np

from ._mif_checks import produced_mif, voxel_mapping
from .dataset import SourceFingerprint
from .execution import hash_file
from .scientific.csd import sh_design

BASIS = "mrtrix3_real_symmetric"
FRAME = "world_ras"


def sh_degree(count: int) -> int:
    """Invert N=(l+1)(l+2)/2, accepting even l=0..30 only."""
    for degree in range(0, 31, 2):
        if (degree + 1) * (degree + 2) // 2 == count:
            return degree
    raise ValueError(f"Unsupported real-even SH coefficient count: {count}")


@lru_cache(maxsize=3)
def sphere_mesh(quality: int = 1):
    """Antipodal octahedron subdivisions: 66/258/1026 vertices, 128/512/2048 faces.

    These finite angular samples approximate the continuous expansion; high SH
    degrees and narrow lobes require higher quality and still warrant review.
    """
    if quality not in (0, 1, 2):
        raise ValueError("Angular quality must be 0, 1 or 2")
    vertices = [
        np.array(v, float)
        for v in [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    ]
    faces = [(0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4), (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5)]
    for _ in range(quality + 2):
        edges, refined = {}, []

        def midpoint(a, b, edges=edges):
            key = tuple(sorted((a, b)))
            if key not in edges:
                v = vertices[a] + vertices[b]
                edges[key] = len(vertices)
                vertices.append(v / np.linalg.norm(v))
            return edges[key]

        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            refined.extend([(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)])
        faces = refined
    v, f = np.asarray(vertices), np.asarray(faces, dtype=np.int32)
    v.setflags(write=False)
    f.setflags(write=False)
    return v, f


@lru_cache(maxsize=48)
def sampled_basis(degree: int, quality: int):
    """Cache one exact basis evaluation per SH order and angular mesh."""
    sh_degree((degree + 1) * (degree + 2) // 2)
    result = sh_design(sphere_mesh(quality)[0], degree)
    result.setflags(write=False)
    return result


class FODImage:
    """Lazy immutable file reference; each operation owns its short-lived mapping.

    Unknown/legacy basis and frame are blocked, never guessed or converted.
    Constructor verifies structure and hashes; only requested coefficients are
    numerically inspected, so whole-volume finiteness is not claimed.
    """

    def __init__(
        self, path, *, basis=BASIS, frame=FRAME, expected_hash=None, cancelled=lambda: False
    ):
        if basis != BASIS or frame != FRAME:
            raise ValueError(
                "Only declared MRtrix3 orthonormal real-even SH in world RAS is supported; legacy/unknown conventions need verified conversion"
            )
        self.path = Path(path).resolve()
        self.basis, self.frame = basis, frame
        self.is_mif = self.path.suffix == ".mif"
        if self.is_mif:
            with produced_mif(self.path) as (fields, affine, array):
                self.shape, self.affine, self.header = array.shape, affine.copy(), fields
                if array.dtype.kind != "f":
                    raise ValueError("FOD coefficients must be floating point")
        elif self.path.name.endswith((".nii", ".nii.gz")):
            from .atlas import geometry_record
            from .scene import ImageVolume

            image = ImageVolume(self.path)
            # Reuse the scientific importer's coded physical-geometry checks.
            geometry = geometry_record(image)
            self.shape, self.affine, self.header = image.shape, np.array(geometry["affine_mm"]), {}
        else:
            raise ValueError("FOD requires an uncompressed Float32 MIF or NIfTI coefficient image")
        if len(self.shape) != 4:
            raise ValueError("FOD requires 4D coefficients, not scalar/tensor/vector intensities")
        self.degree = sh_degree(self.shape[3])
        self.sha256 = hash_file(self.path, cancelled)
        self.fingerprint = SourceFingerprint.capture(self.path)
        if expected_hash and expected_hash != self.sha256:
            raise ValueError("FOD source hash differs from verified result/workspace")

    def coefficients(self, positions):
        """Copy <=512 selected coefficient vectors; never retain a closed memmap."""
        if not self.fingerprint.unchanged(self.path):
            raise ValueError("FOD source changed since import; reopen and verify before rendering")
        p = np.asarray(positions, dtype=int).reshape(-1, 3)
        if len(p) > 512:
            raise ValueError("At most 512 glyphs per request")
        if np.any(p < 0) or np.any(p >= np.array(self.shape[:3])):
            raise ValueError("FOD voxel is outside the image")
        if self.is_mif:
            with produced_mif(self.path) as (_, _, array):
                value = np.array(array[tuple(p.T)], dtype=float, copy=True)
        else:
            image = nib.load(self.path, mmap=True)
            if np.prod(self.shape[:2]) * self.shape[3] * 8 > 64 * 1024**2:
                raise ValueError(
                    "NIfTI coefficient slab exceeds 64 MiB display limit; use a verified Float32 MIF for bounded access"
                )
            # Slice groups bound decompression and avoid repeated full-volume reads.
            value = np.empty((len(p), self.shape[3]), float)
            for z in np.unique(p[:, 2]):
                ids = np.flatnonzero(p[:, 2] == z)
                plane = np.asarray(image.dataobj[:, :, int(z), :])
                value[ids] = plane[p[ids, 0], p[ids, 1]]
        if not np.isfinite(value).all():
            raise ValueError(
                "Selected FOD coefficients contain nonfinite values; rendering blocked"
            )
        return value

    def c00(self):
        """Bounded 3D background proxy, explicitly coefficient C00, not FA/anatomy."""
        if self.is_mif:
            with produced_mif(self.path) as (_, _, array):
                return np.array(array[..., 0], dtype=np.float32, copy=True)
        return np.asarray(nib.load(self.path).dataobj[..., 0], dtype=np.float32)

    def reference_indices(self, reference, positions):
        """Validate exact grid equivalence, including axis permutation and flips."""
        voxel_mapping(self.affine, self.shape, reference.affine, reference.shape)
        p = nib.affines.apply_affine(np.linalg.inv(self.affine) @ reference.affine, positions)
        return np.rint(p).astype(int)


@dataclass(frozen=True)
class Glyphs:
    """World-space radial meshes and original, untruncated sampled statistics."""

    vertices: np.ndarray  # glyph, direction, world xyz
    faces: np.ndarray
    colors: np.ndarray  # direction, RGB uint8
    amplitudes: np.ndarray
    centers: np.ndarray
    peak: float


def glyphs(
    image,
    positions,
    *,
    quality=1,
    scale=0.5,
    scaling="Relative shape",
    color="Direction RGB",
    shared_peak=None,
):
    """Evaluate real SH, retain raw samples, apply an explicit display-only radius."""
    if not np.isfinite(scale) or not 0.001 <= scale <= 100:
        raise ValueError("Glyph scale must be finite and between 0.001 and 100")
    directions, faces = sphere_mesh(quality)
    coefficients = image.coefficients(positions)
    amplitudes = coefficients @ sampled_basis(image.degree, quality).T
    if not np.isfinite(amplitudes).all():
        raise ValueError("SH expansion produced nonfinite amplitudes; rendering blocked")
    radii = np.maximum(amplitudes, 0)
    peak = float(radii.max(initial=0))
    if scaling == "Relative shape":
        denominator = radii.max(axis=1, keepdims=True)
    elif scaling == "Shared region":
        denominator = np.array(shared_peak if shared_peak is not None else peak)
    elif scaling == "Amplitude (mm/unit)":
        denominator = np.array(1.0)
    else:
        raise ValueError("Unknown glyph scaling mode")
    radius = np.divide(radii, denominator, out=np.zeros_like(radii), where=denominator > 0) * scale
    centers = nib.affines.apply_affine(image.affine, positions)
    vertices = centers[:, None, :] + radius[:, :, None] * directions
    if (
        not np.isfinite(vertices).all()
        or np.max(np.abs(vertices), initial=0) > np.finfo(np.float32).max
    ):
        raise ValueError(
            "Glyph coordinates exceed finite rendering precision; decrease amplitude scale"
        )
    rgb = np.abs(directions) if color == "Direction RGB" else np.full_like(directions, 0.82)
    if color not in ("Direction RGB", "Neutral"):
        raise ValueError("Unknown glyph color mode")
    return Glyphs(
        vertices.astype(np.float32),
        faces,
        np.rint(rgb * 255).astype(np.uint8),
        amplitudes,
        centers,
        peak,
    )


def region_positions(
    reference, position, geometries, *, spacing=8, maximum=120, radius=24, roi=None
):
    """Deterministic bounded crosshair-centred slice region, selected voxel first.

    ROI must already be on the verified reference grid. Sampling is stratified
    across planes, rather than filling the cap with just the first orientation.
    """
    if not 1 <= maximum <= 512 or not 1 <= spacing <= 32 or not 1 <= radius <= 100:
        raise ValueError("Invalid regional density/extent/cap")
    if roi is not None and roi.shape != tuple(reference.shape[:3]):
        raise ValueError("ROI geometry does not match the reference grid")
    candidates = [tuple(position)]
    planes = []
    for g in geometries:
        points = []
        for a in range(
            max(0, position[g.horizontal] - radius),
            min(reference.shape[g.horizontal], position[g.horizontal] + radius + 1),
            spacing,
        ):
            for b in range(
                max(0, position[g.vertical] - radius),
                min(reference.shape[g.vertical], position[g.vertical] + radius + 1),
                spacing,
            ):
                p = list(position)
                p[g.horizontal], p[g.vertical] = a, b
                points.append(tuple(p))
        points.sort(key=lambda p: np.linalg.norm(np.array(p) - position))
        planes.append(points)
    for i in range(max(map(len, planes), default=0)):
        candidates.extend(p[i] for p in planes if i < len(p))
    unique = list(dict.fromkeys(p for p in candidates if roi is None or roi[p] > 0))
    return np.asarray(unique[:maximum], dtype=int).reshape(-1, 3)


def project_glyphs(mesh, reference, geometry):
    """Project actual world surfaces into existing native slice display fractions.

    Inverse affine accounts for permutations, flips, anisotropy and obliquity;
    SH directions themselves remain world RAS, with no second image-axis rotation.
    """
    points = nib.affines.apply_affine(np.linalg.inv(reference.affine), mesh.vertices)
    h, v = geometry.horizontal, geometry.vertical
    x = (points[..., h] + 0.5) / geometry.shape[h]
    y = (points[..., v] + 0.5) / geometry.shape[v]
    if geometry.right_sign < 0:
        x = 1 - x
    if geometry.up_sign > 0:
        y = 1 - y
    # Native slice positive-facing depth used only for painter ordering.
    depth = points[..., geometry.axis]
    return np.stack((x, y, depth), axis=-1)
