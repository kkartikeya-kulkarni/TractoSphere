"""Analytic, independent MRtrix and spatial tests of radial FOD visualization."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core._mif_checks import produced_mif, voxel_mapping
from dmri_studio.core.fod import (
    FODImage,
    glyphs,
    project_glyphs,
    region_positions,
    sampled_basis,
    sh_degree,
    sphere_mesh,
)
from dmri_studio.core.preview_geometry import plane_geometry
from dmri_studio.core.scene import ImageVolume
from dmri_studio.core.scientific.csd import sh_design


def save(path, values, affine=None):
    image = nib.Nifti1Image(values, np.eye(4) if affine is None else affine)
    image.header.set_xyzt_units("mm")
    image.set_sform(image.affine, 1)
    image.set_qform(image.affine, 1)
    nib.save(image, path)
    return path


@pytest.mark.parametrize("degree", [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30])
def test_order(degree):
    assert sh_degree((degree + 1) * (degree + 2) // 2) == degree


@pytest.mark.parametrize("count", [0, 2, 3, 5, 7, 9, 44, 46, 5000])
def test_bad_order(count):
    with pytest.raises(ValueError, match="coefficient count"):
        sh_degree(count)


@pytest.mark.parametrize("quality,n", [(0, 66), (1, 258), (2, 1026)])
def test_sampling(quality, n):
    d, f = sphere_mesh(quality)
    assert d.shape == (n, 3)
    np.testing.assert_allclose(np.linalg.norm(d, axis=1), 1)
    assert max(min(np.linalg.norm(d + v, axis=1)) for v in d) < 1e-10
    assert np.all(
        np.einsum(
            "ij,ij->i", np.cross(d[f[:, 1]] - d[f[:, 0]], d[f[:, 2]] - d[f[:, 0]]), d[f[:, 0]]
        )
        > 0
    )
    assert sampled_basis(8, quality) is sampled_basis(8, quality)


@pytest.mark.parametrize("degree", list(range(0, 31, 2)))
def test_mrtrix_basis_independent(tmp_path, degree):
    binary = Path("/usr/local/mrtrix3/bin/sh2amp")
    if not binary.is_file():
        pytest.skip("Independent installed MRtrix unavailable")
    n = (degree + 1) * (degree + 2) // 2
    source = save(tmp_path / "basis.nii", np.eye(n, dtype="f4")[:, None, None, :])
    directions = np.vstack([sphere_mesh(0)[0], [[0.173, 0.432, 0.885]]])
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    np.savetxt(tmp_path / "directions", directions)
    subprocess.run(
        [
            str(binary),
            str(source),
            str(tmp_path / "directions"),
            str(tmp_path / "amp.mif"),
            "-nthreads",
            "2",
        ],
        check=True,
        capture_output=True,
    )
    ref = ImageVolume(source)
    with produced_mif(tmp_path / "amp.mif") as (_, a, array):
        axes, flips = voxel_mapping(a, array.shape, ref.affine, ref.shape)
        values = array.transpose((*axes, 3))
        for axis in flips:
            values = np.flip(values, axis)
        np.testing.assert_allclose(
            values[:, 0, 0, :].T, sh_design(directions, degree), atol=3e-5, rtol=3e-5
        )


@pytest.mark.parametrize(
    "directions", [[[1, 0, 0]], [[1, 0, 0], [0, 1, 0]], [[1, 0, 0], [0, 1, 0], [0, 0, 1]]]
)
def test_controlled_crossings_and_antipodal(tmp_path, directions):
    d, _ = sphere_mesh(2)
    # Even polynomial (u dot a)^8 is exactly representable at lmax 8.
    signal = sum((d @ np.array(axis)) ** 8 for axis in directions)
    coef = np.linalg.lstsq(sh_design(d, 8), signal, rcond=None)[0]
    source = save(tmp_path / "fod.nii", coef.astype("f4")[None, None, None, :])
    g = glyphs(FODImage(source), [[0, 0, 0]], quality=2)
    np.testing.assert_allclose(g.amplitudes[0], signal, atol=1e-7)
    for axis in directions:
        idx = np.argmax(d @ axis)
        assert g.amplitudes[0, idx] > 0.99
        opposite = np.argmin(d @ axis)
        assert abs(g.amplitudes[0, idx] - g.amplitudes[0, opposite]) < 1e-12


def test_negative_and_scaling_preserve_sources(tmp_path):
    coef = np.zeros((2, 1, 1, 6), dtype="f4")
    coef[:, 0, 0, 0] = [1, 2]
    coef[:, 0, 0, 3] = [2, 4]
    image = FODImage(save(tmp_path / "fod.nii", coef))
    for scaling in ["Relative shape", "Shared region", "Amplitude (mm/unit)"]:
        g = glyphs(image, [[0, 0, 0], [1, 0, 0]], scaling=scaling, scale=0.4)
        assert g.amplitudes.min() < 0
        radii = np.linalg.norm(g.vertices - g.centers[:, None], axis=-1)
        assert np.all(radii[g.amplitudes < 0] < 1e-7)
        peaks = radii.max(axis=1)
        np.testing.assert_allclose(
            peaks[1] / peaks[0], 1 if scaling == "Relative shape" else 2, rtol=1e-6
        )
    np.testing.assert_array_equal(image.coefficients([[0, 0, 0], [1, 0, 0]]), coef[:, 0, 0])
    assert FODImage(image.path).sha256 == image.sha256
    neutral = glyphs(image, [[0, 0, 0]], color="Neutral")
    assert np.ptp(neutral.colors) == 0
    d, _ = sphere_mesh(1)
    g = glyphs(image, [[0, 0, 0]])
    np.testing.assert_array_equal(g.colors, np.rint(np.abs(d) * 255).astype("u1"))
    with pytest.raises(ValueError, match="512"):
        image.coefficients(np.zeros((513, 3), int))


@pytest.mark.parametrize(
    "affine",
    [
        np.diag([-2.0, 3.0, 1.5, 1]),
        np.array([[0, 0, -2, 12], [3, 0, 0, -8], [0, 1.5, 0, 4], [0, 0, 0, 1.0]]),
        np.array([[1.8, -1.2, 0, 10], [1.2, 1.8, 0, -5], [0, 0, 3, 9], [0, 0, 0, 1.0]]),
    ],
)
def test_world_placement_and_planes(tmp_path, affine):
    # qform may round obliquity but agrees within existing geometry policy.
    coef = np.zeros((7, 8, 9, 6), dtype="f4")
    coef[..., 0] = 1
    image = FODImage(save(tmp_path / "fod.nii", coef, affine))
    ref = ImageVolume(image.path)
    position = (3, 4, 5)
    g = glyphs(image, [position], scale=0.5)
    center = nib.affines.apply_affine(affine, position)
    np.testing.assert_allclose(g.centers[0], center)
    d, _ = sphere_mesh(1)
    # Directions remain world RAS even when the image grid is rotated/flipped.
    np.testing.assert_allclose(g.vertices[0] - center, 0.5 * d, atol=2e-6)
    for orientation in ["axial", "sagittal", "coronal"]:
        geometry = plane_geometry(ref, orientation)
        projection = project_glyphs(g, ref, geometry)[0]
        x, y = geometry.display_position(position)
        np.testing.assert_allclose(projection[:, :2].mean(axis=0), [x, y], atol=1e-6)
    positions = region_positions(
        ref,
        position,
        [plane_geometry(ref, o) for o in ["axial", "sagittal", "coronal"]],
        spacing=1,
        maximum=30,
    )
    assert len(positions) == 30 and tuple(positions[0]) == position
    with pytest.raises(ValueError):
        image.reference_indices(
            SimpleNamespace(shape=ref.shape, affine=affine + np.diag([0, 0, 0, 1])), [position]
        )


def test_invalid_sources_and_conventions(tmp_path):
    bad = save(tmp_path / "bad.nii", np.zeros((2, 2, 2, 7), dtype="f4"))
    with pytest.raises(ValueError, match="coefficient count"):
        FODImage(bad)
    with pytest.raises(ValueError, match="legacy/unknown"):
        FODImage(bad, basis="legacy")
    with pytest.raises(FileNotFoundError):
        FODImage(tmp_path / "missing.mif")
    data = np.ones((2, 2, 2, 6), dtype="f4")
    data[0, 0, 0, 0] = np.nan
    m = FODImage(save(tmp_path / "nan.nii", data))
    with pytest.raises(ValueError, match="nonfinite"):
        glyphs(m, [[0, 0, 0]])
    glyphs(m, [[1, 1, 1]])  # Scoped inspection does not invent whole-volume validation.


def test_non_axis_peak_and_source_change(tmp_path):
    d, _ = sphere_mesh(2)
    axis = np.array([0.3, 0.8, 0.5])
    axis /= np.linalg.norm(axis)
    signal = (d @ axis) ** 8
    coefficients = np.linalg.lstsq(sh_design(d, 8), signal, rcond=None)[0]
    affine = np.array([[0, 0, -2, 10], [-1.5, 0, 0, 20], [0, 3, 0, -7], [0, 0, 0, 1.0]])
    path = save(
        tmp_path / "rotated.nii",
        np.broadcast_to(coefficients.astype("f4"), (3, 4, 5, 45)).copy(),
        affine,
    )
    model = FODImage(path)
    mesh = glyphs(model, [[1, 2, 3]], quality=2)
    peak = mesh.vertices[0, np.argmax(mesh.amplitudes[0])] - mesh.centers[0]
    peak /= np.linalg.norm(peak)
    assert np.degrees(np.arccos(np.clip(abs(peak @ axis), 0, 1))) < 5
    with pytest.raises(ValueError, match="source hash"):
        FODImage(path, expected_hash="0" * 64)
    # In-place source changes invalidate later access even when data still parses.
    import os

    stamp = path.stat().st_mtime_ns
    os.utime(path, ns=(stamp, stamp + 1000000000))
    with pytest.raises(ValueError, match="changed since import"):
        model.coefficients([[1, 2, 3]])


def test_extreme_display_values_are_blocked_not_clipped(tmp_path):
    """Finite but enormous coefficients cannot create invalid VTK coordinates."""
    path = save(tmp_path / "extreme.nii", np.full((1, 1, 1, 1), 1e38, dtype="f4"))
    model = FODImage(path)
    with pytest.raises(ValueError, match="finite rendering precision"):
        glyphs(model, [[0, 0, 0]], scaling="Amplitude (mm/unit)", scale=100)
    # Shape mode remains bounded without altering the large source coefficient.
    mesh = glyphs(model, [[0, 0, 0]], scaling="Relative shape", scale=1)
    assert np.isfinite(mesh.vertices).all()
    assert model.coefficients([[0, 0, 0]])[0, 0] > 1e37
