import numpy as np
import pytest
from vtkmodules.util.numpy_support import vtk_to_numpy
from vtkmodules.vtkFiltersCore import vtkMassProperties, vtkPolyDataConnectivityFilter

from dmri_studio.gui.roi_surfaces import roi_mesh


@pytest.mark.parametrize("quality", ["Voxel boundary", "Smooth", "Detailed"])
def test_surface_physical_geometry_and_mask_preserved(quality):
    mask = np.zeros((15, 17, 19), "u1")
    mask[2:12, 3:14, 4:16] = 1
    original = mask.copy()
    angle = 0.3
    aff = np.array(
        [
            [-2 * np.cos(angle), -3 * np.sin(angle), 0, 41],
            [-2 * np.sin(angle), 3 * np.cos(angle), 0, -10],
            [0, 0, 1.2, 8],
            [0, 0, 0, 1],
        ]
    )
    mesh = roi_mesh(mask, aff, quality)
    points = vtk_to_numpy(mesh.GetPoints().GetData())
    expected = aff[:3, :3] @ np.array([6.5, 8, 9.5]) + aff[:3, 3]
    np.testing.assert_allclose(points.mean(axis=0), expected, atol=0.15)
    inverse = np.linalg.inv(aff)
    vox = points @ inverse[:3, :3].T + inverse[:3, 3]
    assert (vox.min(axis=0) >= np.array([1.4, 2.4, 3.4])).all()
    assert (vox.max(axis=0) <= np.array([11.6, 13.6, 15.6])).all()
    mass = vtkMassProperties()
    mass.SetInputData(mesh)
    mass.Update()
    scientific_volume = mask.sum() * abs(np.linalg.det(aff[:3, :3]))
    assert 0.8 < mass.GetVolume() / scientific_volume < 1.05
    np.testing.assert_array_equal(mask, original)


def test_small_components_and_edges_survive():
    mask = np.zeros((12, 12, 12), "u1")
    mask[0, 0, 0] = 1
    mask[8:11, 8:11, 8:11] = 1
    mesh = roi_mesh(mask, np.eye(4))
    c = vtkPolyDataConnectivityFilter()
    c.SetInputData(mesh)
    c.SetExtractionModeToAllRegions()
    c.Update()
    assert c.GetNumberOfExtractedRegions() == 2
    assert mesh.GetBounds()[0] < 0
