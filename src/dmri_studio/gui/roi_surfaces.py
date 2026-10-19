"""Display-only VTK meshes. Scientific masks and their affine are never modified."""

from __future__ import annotations

import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk
from vtkmodules.vtkCommonDataModel import vtkImageData, vtkPolyData
from vtkmodules.vtkCommonMath import vtkMatrix4x4
from vtkmodules.vtkCommonTransforms import vtkTransform
from vtkmodules.vtkFiltersCore import (
    vtkDecimatePro,
    vtkPolyDataNormals,
    vtkWindowedSincPolyDataFilter,
)
from vtkmodules.vtkFiltersGeneral import vtkDiscreteFlyingEdges3D, vtkTransformPolyDataFilter


def roi_mesh(mask, affine, quality="Smooth"):
    """Extract a padded binary label boundary, transform to physical mm, then smooth.

    Cropping is an exact index-space optimization with a compensating translation.
    Windowed-sinc smoothing/decimation affects only the returned display mesh.
    Small components are retained; no connectivity component is discarded.
    """
    if quality not in ("Voxel boundary", "Smooth", "Detailed"):
        raise ValueError("Unknown ROI surface quality")
    data = np.asarray(mask)
    if data.ndim != 3 or not np.isfinite(data).all() or not np.isin(data, [0, 1]).all():
        raise ValueError("ROI surface requires a finite binary 3D mask")
    affine = np.asarray(affine, float)
    if (
        affine.shape != (4, 4)
        or not np.isfinite(affine).all()
        or abs(np.linalg.det(affine[:3, :3])) < 1e-10
    ):
        raise ValueError("Invalid ROI voxel-to-world affine")
    occupied = np.where(data)
    if not len(occupied[0]):
        return vtkPolyData()
    low = np.array([v.min() for v in occupied])
    high = np.array([v.max() + 1 for v in occupied])
    crop = np.pad(data[tuple(slice(a, b) for a, b in zip(low, high, strict=True))].astype("u1"), 1)
    image = vtkImageData()
    image.SetDimensions(*crop.shape)
    image.GetPointData().SetScalars(numpy_to_vtk(crop.ravel(order="F"), deep=True))
    contour = vtkDiscreteFlyingEdges3D()
    contour.SetInputData(image)
    contour.SetValue(0, 1)
    contour.Update()
    shift = np.eye(4)
    shift[:3, 3] = low - 1
    world = affine @ shift
    matrix = vtkMatrix4x4()
    for i in range(4):
        for j in range(4):
            matrix.SetElement(i, j, float(world[i, j]))
    transform = vtkTransform()
    transform.SetMatrix(matrix)
    physical = vtkTransformPolyDataFilter()
    physical.SetInputConnection(contour.GetOutputPort())
    physical.SetTransform(transform)
    physical.Update()
    output = physical.GetOutput()
    if quality != "Voxel boundary":
        smooth = vtkWindowedSincPolyDataFilter()
        smooth.SetInputData(output)
        smooth.SetNumberOfIterations(12)
        smooth.SetPassBand(0.12)
        smooth.BoundarySmoothingOff()
        smooth.FeatureEdgeSmoothingOff()
        smooth.NonManifoldSmoothingOn()
        smooth.NormalizeCoordinatesOn()
        smooth.Update()
        output = smooth.GetOutput()
        if quality == "Smooth" and output.GetNumberOfPolys() > 1000:
            decimate = vtkDecimatePro()
            decimate.SetInputData(output)
            decimate.SetTargetReduction(0.15)
            decimate.PreserveTopologyOn()
            decimate.SplittingOff()
            decimate.BoundaryVertexDeletionOff()
            decimate.Update()
            output = decimate.GetOutput()
    normals = vtkPolyDataNormals()
    normals.SetInputData(output)
    normals.SplittingOff()
    normals.ConsistencyOn()
    normals.AutoOrientNormalsOn()
    normals.Update()
    result = vtkPolyData()
    result.DeepCopy(normals.GetOutput())
    return result
