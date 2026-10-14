"""VTK rendering adapter. All actor coordinates are RAS+ millimetres."""

import sys
from types import SimpleNamespace

import numpy as np
import vtkmodules.vtkRenderingOpenGL2  # noqa: F401 - register OpenGL factories
from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QRubberBand, QWidget
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray, vtk_to_numpy
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkImageData, vtkPlane, vtkPolyData
from vtkmodules.vtkCommonMath import vtkMatrix4x4
from vtkmodules.vtkFiltersCore import vtkFlyingEdges3D, vtkTubeFilter
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.vtkIOImage import vtkPNGWriter
from vtkmodules.vtkRenderingCore import (
    vtkActor,
    vtkCellPicker,
    vtkImageActor,
    vtkPolyDataMapper,
    vtkRenderer,
    vtkWindowToImageFilter,
)

vtk = SimpleNamespace(
    vtkMatrix4x4=vtkMatrix4x4,
    vtkImageData=vtkImageData,
    vtkCellArray=vtkCellArray,
    vtkPolyData=vtkPolyData,
    vtkPlane=vtkPlane,
    vtkPoints=vtkPoints,
    vtkFlyingEdges3D=vtkFlyingEdges3D,
    vtkTubeFilter=vtkTubeFilter,
    vtkRenderer=vtkRenderer,
    vtkCellPicker=vtkCellPicker,
    vtkImageActor=vtkImageActor,
    vtkPolyDataMapper=vtkPolyDataMapper,
    vtkActor=vtkActor,
    vtkWindowToImageFilter=vtkWindowToImageFilter,
    vtkInteractorStyleTrackballCamera=vtkInteractorStyleTrackballCamera,
    vtkPNGWriter=vtkPNGWriter,
)


def matrix(affine):
    result = vtk.vtkMatrix4x4()
    for i in range(4):
        for j in range(4):
            result.SetElement(i, j, float(affine[i, j]))
    return result


def vtk_image(data):
    image = vtk.vtkImageData()
    image.SetDimensions(*data.shape)
    image.GetPointData().SetScalars(
        numpy_to_vtk(np.asarray(data, dtype=np.float32).ravel(order="F"), deep=True)
    )
    return image


class SceneRenderer(QVTKRenderWindowInteractor):
    """Accelerated line/tube/surface rendering with ordinary trackball interaction."""

    def __init__(self, parent=None):
        self.frame = None
        self.capturing = False
        super().__init__(parent)
        if sys.platform == "darwin":
            self.setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen, False)
        self.renderer = vtk.vtkRenderer()
        self.renderer.SetBackground(0.035, 0.045, 0.06)
        self.renderer.SetUseDepthPeeling(True)
        self.renderer.SetMaximumNumberOfPeels(8)
        self.renderer.SetOcclusionRatio(0.1)
        self.GetRenderWindow().SetAlphaBitPlanes(1)
        self.GetRenderWindow().SetMultiSamples(0)
        self.GetRenderWindow().AddRenderer(self.renderer)
        self.GetRenderWindow().GetInteractor().SetInteractorStyle(
            vtk.vtkInteractorStyleTrackballCamera()
        )
        self.actors = {}
        self.planes = []
        self.image = None
        self.reference = None
        self.mapper_ids = {}
        self.selection_mode = "Camera"
        self.gesture_callback = None
        self.gesture_start = None
        self.rubber = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.pick_callback = None
        self.position_callback = None
        self.AddObserver("LeftButtonPressEvent", self.pick)
        self.setMinimumSize(200, 220)
        self.setToolTip(
            "Left: rotate · Middle/Shift-left: pan · Right/wheel: zoom · Cmd/Ctrl-click: select tract · Alt-click: navigate"
        )
        if sys.platform == "darwin":
            # Qt >=6.10 Cocoa enters a paint/render feedback loop with the
            # native QVTK widget (VTK issue #19915). Render in VTK's offscreen
            # framebuffer and present its actual pixels through Qt's painter.
            self.GetRenderWindow().SetOffScreenRendering(1)
            self.GetRenderWindow().AddObserver("EndEvent", self.capture_frame)
        self.Initialize()

    def mousePressEvent(self, event):
        self._handled_pick = False
        modifiers = event.modifiers()
        select = modifiers & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
        )
        navigate = modifiers & Qt.KeyboardModifier.AltModifier
        if (select or navigate) and event.button() in (
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.RightButton,
        ):
            scale = self.devicePixelRatioF()
            x = int(event.position().x() * scale)
            y = int((self.height() - event.position().y() - 1) * scale)
            self.pick_at(x, y, bool(navigate))
            self._handled_pick = True
            event.accept()
            return
        if self.selection_mode != "Camera" and event.button() == Qt.MouseButton.LeftButton:
            self.gesture_start = event.position().toPoint()
            self.rubber.setGeometry(QRect(self.gesture_start, self.gesture_start))
            self.rubber.show()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.gesture_start is not None:
            self.rubber.setGeometry(
                QRect(self.gesture_start, event.position().toPoint()).normalized()
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if getattr(self, "_handled_pick", False):
            self._handled_pick = False
            event.accept()
            return
        if self.gesture_start is not None:
            start = self.gesture_start
            self.gesture_start = None
            self.rubber.hide()
            if self.gesture_callback is not None:
                self.gesture_callback(start, event.position().toPoint(), self.selection_mode)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mesh(self, key, vertices, triangles, color=(0.72, 0.78, 0.85), opacity=0.4):
        self.remove(key)
        poly = vtk.vtkPolyData()
        points = vtk.vtkPoints()
        points.SetData(numpy_to_vtk(vertices, deep=True))
        poly.SetPoints(points)
        cells = vtk.vtkCellArray()
        cells.SetData(
            numpy_to_vtkIdTypeArray(np.arange(0, triangles.size + 1, 3, dtype=np.int64), deep=True),
            numpy_to_vtkIdTypeArray(triangles.ravel().astype(np.int64), deep=True),
        )
        poly.SetPolys(cells)
        from vtkmodules.vtkFiltersCore import vtkPolyDataNormals

        normals = vtkPolyDataNormals()
        normals.SetInputData(poly)
        normals.ConsistencyOn()
        normals.Update()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(normals.GetOutput())
        mapper.ScalarVisibilityOff()
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*color)
        actor.GetProperty().SetOpacity(opacity)
        self.renderer.AddActor(actor)
        self.actors[key] = actor
        self.draw()

    def volume_render(self, data, reference, low, high, opacity=0.15):
        """Actual GPU ray casting of the source volume, separate from surfaces."""
        import vtkmodules.vtkRenderingVolumeOpenGL2  # noqa: F401
        from vtkmodules.vtkCommonDataModel import vtkPiecewiseFunction
        from vtkmodules.vtkRenderingCore import (
            vtkColorTransferFunction,
            vtkVolume,
            vtkVolumeProperty,
        )
        from vtkmodules.vtkRenderingVolumeOpenGL2 import vtkSmartVolumeMapper

        self.remove(("volume", 0))
        mapper = vtkSmartVolumeMapper()
        mapper.SetInputData(vtk_image(data))
        colors = vtkColorTransferFunction()
        colors.AddRGBPoint(low, 0, 0, 0)
        colors.AddRGBPoint(high, 1, 1, 1)
        alpha = vtkPiecewiseFunction()
        alpha.AddPoint(low, 0)
        alpha.AddPoint(low + (high - low) * 0.15, 0)
        alpha.AddPoint(high, opacity)
        prop = vtkVolumeProperty()
        prop.SetColor(colors)
        prop.SetScalarOpacity(alpha)
        prop.SetInterpolationTypeToLinear()
        prop.ShadeOn()
        actor = vtkVolume()
        actor.SetMapper(mapper)
        actor.SetProperty(prop)
        actor.SetUserMatrix(matrix(reference.affine))
        self.renderer.AddVolume(actor)
        self.actors[("volume", 0)] = actor
        self.draw()

    def paintEngine(self):
        if sys.platform == "darwin":
            return QWidget.paintEngine(self)
        return super().paintEngine()

    def paintEvent(self, event):
        if sys.platform != "darwin":
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if self.frame is not None:
            painter.drawImage(self.rect(), self.frame)
        painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "renderer"):
            QTimer.singleShot(0, self.draw)

    def capture_frame(self, *_):
        if self.capturing:
            return
        self.capturing = True
        try:
            capture = vtk.vtkWindowToImageFilter()
            capture.SetInput(self.GetRenderWindow())
            capture.ReadFrontBufferOff()
            capture.ShouldRerenderOff()
            capture.Update()
            output = capture.GetOutput()
            w, h, _ = output.GetDimensions()
            if w and h:
                rgb = np.ascontiguousarray(
                    vtk_to_numpy(output.GetPointData().GetScalars()).reshape(h, w, -1)[::-1, :, :3]
                )
                self.frame = QImage(
                    rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888
                ).copy()
                self.update()
        finally:
            self.capturing = False

    def pick(self, *_):
        if not self.GetControlKey() and not self.GetAltKey():
            return
        self.pick_at(*self.GetEventPosition(), bool(self.GetAltKey()))

    def pick_at(self, x, y, navigate=False):
        picker = vtk.vtkCellPicker()
        picker.SetTolerance(0.005)
        if navigate:
            if self.position_callback and picker.Pick(x, y, 0, self.renderer):
                self.position_callback(picker.GetPickPosition())
            return
        if self.pick_callback is None:
            return
        # Pick only tract actors. Anatomical slices/surfaces must not consume
        # a tract-selection gesture before the intended fiber can be selected.
        picker.PickFromListOn()
        for actor in self.mapper_ids:
            if actor.GetVisibility():
                picker.AddPickList(actor)
        picker.Pick(x, y, 0, self.renderer)
        entry = self.mapper_ids.get(picker.GetActor())
        if entry and picker.GetCellId() >= 0:
            group, ids, poly = entry
            array = poly.GetCellData().GetArray("tract_id")
            index = int(array.GetTuple1(picker.GetCellId())) if array else ids[picker.GetCellId()]
            self.pick_callback(group, index)

    def draw(self):
        self.GetRenderWindow().Render()

    def remove(self, key):
        if key in self.actors:
            actor = self.actors.pop(key)
            self.renderer.RemoveViewProp(actor)
            self.mapper_ids.pop(actor, None)

    def set_volume(self, data, reference, position, low, high):
        self.reference = reference
        self.image = vtk_image(data)
        for actor in self.planes:
            self.renderer.RemoveActor(actor)
        self.planes = []
        for _axis in range(3):
            actor = vtk.vtkImageActor()
            actor.GetMapper().SetInputData(self.image)
            actor.SetUserMatrix(matrix(reference.affine))
            actor.GetProperty().SetColorWindow(max(high - low, 1e-9))
            actor.GetProperty().SetColorLevel((high + low) / 2)
            actor.GetProperty().SetInterpolationTypeToNearest()
            self.renderer.AddActor(actor)
            self.planes.append(actor)
        self.move_planes(position)

    def move_planes(self, position):
        if self.image is None:
            return
        shape = self.image.GetDimensions()
        for axis, actor in enumerate(self.planes):
            extent = [0, shape[0] - 1, 0, shape[1] - 1, 0, shape[2] - 1]
            extent[axis * 2 : axis * 2 + 2] = [position[axis]] * 2
            actor.SetDisplayExtent(*extent)
        self.draw()

    def surface(self, key, data, affine, threshold=0.5, color=(0.7, 0.75, 0.8), opacity=0.25):
        self.remove(key)
        contour = vtk.vtkFlyingEdges3D()
        contour.SetInputData(vtk_image(data))
        contour.SetValue(0, threshold)
        contour.ComputeNormalsOn()
        contour.Update()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(contour.GetOutput())
        mapper.ScalarVisibilityOff()
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.SetUserMatrix(matrix(affine))
        actor.GetProperty().SetColor(*color)
        actor.GetProperty().SetOpacity(opacity)
        actor.GetProperty().SetSpecular(0.15)
        actor.GetProperty().SetSpecularPower(20)
        self.renderer.AddActor(actor)
        self.actors[key] = actor
        self.draw()

    def tracts(
        self,
        key,
        group,
        limit,
        mode,
        coloring,
        width,
        opacity,
        scalar=None,
        reference=None,
        highlight=True,
    ):
        self.remove(key)
        ids = group.display_ids(limit)
        if not ids:
            self.draw()
            return
        lines = [group.line(i) for i in ids]
        points = np.concatenate(lines)
        lengths = np.array([len(line) for line in lines], dtype=np.int64)
        offsets = np.r_[0, np.cumsum(lengths)].astype(np.int64)
        cells = vtk.vtkCellArray()
        cells.SetData(
            numpy_to_vtkIdTypeArray(offsets, deep=True),
            numpy_to_vtkIdTypeArray(np.arange(len(points), dtype=np.int64), deep=True),
        )
        poly = vtk.vtkPolyData()
        vp = vtk.vtkPoints()
        vp.SetData(numpy_to_vtk(points, deep=True))
        poly.SetPoints(vp)
        poly.SetLines(cells)
        indices = numpy_to_vtk(np.asarray(ids, dtype=np.int32), deep=True)
        indices.SetName("tract_id")
        poly.GetCellData().AddArray(indices)
        rgb = []
        for i, line in zip(ids, lines, strict=False):
            tangent = np.gradient(line, axis=0)
            color = np.abs(tangent) / np.maximum(
                np.linalg.norm(tangent, axis=1, keepdims=True), 1e-12
            )
            if coloring == "Uniform":
                color[:] = group.color
            if coloring == "Length":
                value = min(np.linalg.norm(np.diff(line, axis=0), axis=1).sum() / 200, 1)
                color[:] = (value, 1 - value, 0.5)
            if highlight and i in group.selected:
                color[:] = (1, 0.85, 0.1)
            rgb.append(color)
        rgb = np.concatenate(rgb)
        if coloring == "Image scalar" and scalar is not None:
            vox = np.rint(reference.voxels(points)).astype(int)
            valid = np.all((vox >= 0) & (vox < scalar.shape), axis=1)
            values = np.full(len(points), np.nan)
            values[valid] = scalar[tuple(vox[valid].T)]
            finite = np.isfinite(values)
            if finite.any():
                lo, hi = np.percentile(values[finite], [2, 98])
                v = np.clip((values - lo) / max(hi - lo, 1e-9), 0, 1)
                rgb = np.stack((v, 1 - v, np.full(len(v), 0.5)), axis=1)
                rgb[~finite] = (0.3, 0.3, 0.3)
        for index, tract_id in enumerate(ids):
            if highlight and tract_id in group.selected:
                rgb[offsets[index] : offsets[index + 1]] = (1, 0.85, 0.1)
        colors = numpy_to_vtk(np.asarray(np.clip(rgb * 255, 0, 255), dtype=np.uint8), deep=True)
        colors.SetName("RGB")
        poly.GetPointData().SetScalars(colors)
        mapper = vtk.vtkPolyDataMapper()
        if mode == "Tubes":
            tube = vtk.vtkTubeFilter()
            tube.SetInputData(poly)
            tube.SetRadius(width / 2)
            tube.SetNumberOfSides(6)
            tube.CappingOn()
            tube.Update()
            output = tube.GetOutput()
            mapper.SetInputData(output)
        else:
            output = poly
            mapper.SetInputData(poly)
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.SetVisibility(group.visible)
        actor.GetProperty().SetLineWidth(width)
        actor.GetProperty().SetOpacity(opacity)
        self.renderer.AddActor(actor)
        self.actors[key] = actor
        self.mapper_ids[actor] = (key, ids, output)
        self.draw()

    def camera(self, preset):
        bounds = self.renderer.ComputeVisiblePropBounds()
        center = np.array([(bounds[i] + bounds[i + 1]) / 2 for i in (0, 2, 4)])
        distance = max(np.linalg.norm(np.array(bounds)[1::2] - np.array(bounds)[::2]), 100)
        direction = {
            "Anterior": (0, 1, 0),
            "Posterior": (0, -1, 0),
            "Left": (-1, 0, 0),
            "Right": (1, 0, 0),
            "Superior": (0, 0, 1),
            "Inferior": (0, 0, -1),
            "Isometric": (1, -1, 0.65),
        }[preset]
        camera = self.renderer.GetActiveCamera()
        camera.SetFocalPoint(*center)
        camera.SetPosition(*(center + np.array(direction) * distance))
        camera.SetViewUp(0, 1, 0) if preset in ("Superior", "Inferior") else camera.SetViewUp(
            0, 0, 1
        )
        self.renderer.ResetCamera()
        self.draw()

    def screenshot(self, path):
        from pathlib import Path

        if Path(path).exists():
            raise FileExistsError(path)
        capture = vtk.vtkWindowToImageFilter()
        capture.SetInput(self.GetRenderWindow())
        capture.ReadFrontBufferOff()
        capture.Update()
        writer = vtk.vtkPNGWriter()
        writer.SetFileName(str(path))
        writer.SetInputConnection(capture.GetOutputPort())
        writer.Write()
