"""3D viewport built on VTK, the same stack ParaView/Salome use.

Shows the model mesh (surface + edges) and, after a run, the deformed
shape coloured by displacement magnitude with a legend. When no GL
display is available (headless test runs) it degrades to a labelled
placeholder instead of crashing; the rest of the window keeps working.
"""

from __future__ import annotations

import os

import numpy as np
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from caelab.model.schema import StructuralModel
from caelab.solvers.base import ResultSet

_CELL_TYPES = {}
_VTK_IMPORT_ERROR = ""
try:
    import vtk
    from vtk.util.numpy_support import numpy_to_vtk

    _CELL_TYPES = {
        "C3D4": vtk.VTK_TETRA,
        "C3D10": vtk.VTK_QUADRATIC_TETRA,
        "C3D8": vtk.VTK_HEXAHEDRON,
        "C3D8R": vtk.VTK_HEXAHEDRON,
        "C3D20": vtk.VTK_QUADRATIC_HEXAHEDRON,
    }
except Exception as exc:  # import failure must not masquerade as headless mode
    vtk = None
    if isinstance(exc, ModuleNotFoundError) and exc.name == "vtk":
        _VTK_IMPORT_ERROR = 'VTK가 설치되어 있지 않습니다.\npython -m pip install -e ".[gui]"로 GUI 의존성을 설치하세요.'
    else:
        _VTK_IMPORT_ERROR = f"VTK 모듈을 불러오지 못했습니다.\n{type(exc).__name__}: {exc}"


_FONT_CANDIDATES = (
    "C:/Windows/Fonts/malgun.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
)


def _apply_text_font(prop) -> None:
    """VTK's built-in font has no Hangul glyphs; use a system CJK font."""
    for candidate in _FONT_CANDIDATES:
        if os.path.exists(candidate):
            prop.SetFontFamily(3)  # VTK_FONT_FILE: use the given font file
            prop.SetFontFile(candidate)
            return


def build_grid(model: StructuralModel):
    """Convert the common-model mesh into a vtkUnstructuredGrid."""
    ids = sorted(model.mesh.nodes)
    index = {nid: i for i, nid in enumerate(ids)}
    points = vtk.vtkPoints()
    for nid in ids:
        points.InsertNextPoint(*model.mesh.nodes[nid])
    grid = vtk.vtkUnstructuredGrid()
    grid.SetPoints(points)
    for elem in model.mesh.elements.values():
        cell_type = _CELL_TYPES.get(elem["type"])
        if cell_type is None:
            continue
        id_list = vtk.vtkIdList()
        for nid in elem["nodes"]:
            id_list.InsertNextId(index[nid])
        grid.InsertNextCell(cell_type, id_list)
    return grid, ids


class Viewport(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._renderer = None
        self._vtk_widget = None
        self._grid = None
        self._node_ids: list[int] = []
        self.available = False
        self.unavailable_reason = None
        if vtk is None:
            self.unavailable_reason = _VTK_IMPORT_ERROR
        elif os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            self.unavailable_reason = "QT_QPA_PLATFORM=offscreen으로 실행 중이어서 3D 화면을 비활성화했습니다."
        else:
            try:
                self._init_vtk()
                self.available = True
            except Exception as exc:
                self.unavailable_reason = f"VTK 3D 화면을 초기화하지 못했습니다.\n{type(exc).__name__}: {exc}"
                if self._vtk_widget is not None:
                    self._layout.removeWidget(self._vtk_widget)
                    self._vtk_widget.hide()
                    self._vtk_widget.deleteLater()
                    self._vtk_widget = None
                self._renderer = None
        if not self.available:
            label = QLabel(self.unavailable_reason)
            label.setWordWrap(True)
            label.setStyleSheet("color:#666;")
            from PySide6.QtCore import Qt

            label.setAlignment(Qt.AlignCenter)
            self._layout.addWidget(label)

    # -- VTK setup ---------------------------------------------------------
    def _init_vtk(self) -> None:
        from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor

        self._vtk_widget = QVTKRenderWindowInteractor(self)
        self._layout.addWidget(self._vtk_widget)
        self._renderer = vtk.vtkRenderer()
        self._renderer.SetBackground(0.96, 0.97, 0.98)
        self._renderer.SetBackground2(0.85, 0.89, 0.94)
        self._renderer.GradientBackgroundOn()
        self._vtk_widget.GetRenderWindow().AddRenderer(self._renderer)
        self._vtk_widget.Initialize()
        axes = vtk.vtkAxesActor()
        axes.SetTotalLength(1.0, 1.0, 1.0)
        marker = vtk.vtkOrientationMarkerWidget()
        marker.SetOrientationMarker(axes)
        marker.SetInteractor(self._vtk_widget.GetRenderWindow().GetInteractor())
        marker.SetViewport(0.0, 0.0, 0.16, 0.22)
        marker.EnabledOn()
        marker.InteractiveOff()
        self._marker = marker

    def _render(self) -> None:
        if self._vtk_widget is not None:
            self._vtk_widget.GetRenderWindow().Render()

    # -- content -------------------------------------------------------------
    def show_model(self, model: StructuralModel) -> None:
        if not self.available:
            return
        self._grid, self._node_ids = build_grid(model)
        self._renderer.RemoveAllViewProps()
        surface = vtk.vtkDataSetSurfaceFilter()
        surface.SetInputData(self._grid)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(surface.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(0.62, 0.71, 0.81)
        actor.GetProperty().SetOpacity(0.55)
        self._renderer.AddActor(actor)
        edges = vtk.vtkExtractEdges()
        edges.SetInputData(self._grid)
        emapper = vtk.vtkPolyDataMapper()
        emapper.SetInputConnection(edges.GetOutputPort())
        eactor = vtk.vtkActor()
        eactor.SetMapper(emapper)
        eactor.GetProperty().SetColor(0.15, 0.2, 0.28)
        self._renderer.AddActor(eactor)
        self._renderer.ResetCamera()
        self._render()

    def show_results(self, model: StructuralModel, result: ResultSet,
                     scale: float | None = None) -> None:
        if not self.available or self._grid is None:
            return
        if not self._node_ids or any(n not in result.displacements for n in self._node_ids):
            self._base_scale = None
            self._last_model, self._last_result = None, None
            self.show_model(model)
            return
        disp = np.array([result.displacements[n] for n in self._node_ids], dtype=float)
        magnitude = np.linalg.norm(disp, axis=1)
        if result.units.get("length") == "m":
            magnitude *= 1000.0
        grid = vtk.vtkUnstructuredGrid()
        grid.DeepCopy(self._grid)
        vec = numpy_to_vtk(disp, deep=True)
        vec.SetName("U")
        grid.GetPointData().AddArray(vec)
        mag = numpy_to_vtk(magnitude, deep=True)
        mag.SetName("displacement_magnitude")
        grid.GetPointData().AddArray(mag)
        grid.GetPointData().SetActiveVectors("U")
        grid.GetPointData().SetActiveScalars("displacement_magnitude")
        if scale is None:
            bounds = grid.GetBounds()
            size = max(bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4])
            peak = float(np.max(np.linalg.norm(disp, axis=1))) or 1.0
            scale = 0.25 * size / peak
            self._base_scale = scale
            self._last_model, self._last_result = model, result
        warp = vtk.vtkWarpVector()
        warp.SetInputData(grid)
        warp.SetScaleFactor(scale)
        surface = vtk.vtkDataSetSurfaceFilter()
        surface.SetInputConnection(warp.GetOutputPort())
        lut = vtk.vtkColorTransferFunction()
        lo, hi = float(magnitude.min()), float(magnitude.max()) or 1.0
        lut.AddRGBPoint(lo, 0.0, 0.0, 0.85)
        lut.AddRGBPoint((lo + hi) / 2, 0.0, 0.8, 0.35)
        lut.AddRGBPoint(hi, 0.9, 0.05, 0.0)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(surface.GetOutputPort())
        mapper.SetScalarModeToUsePointData()
        mapper.SelectColorArray("displacement_magnitude")
        mapper.SetLookupTable(lut)
        mapper.SetScalarRange(lo, hi)
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        self._renderer.RemoveAllViewProps()
        ghost_edges = vtk.vtkExtractEdges()
        ghost_edges.SetInputData(self._grid)
        ghost_mapper = vtk.vtkPolyDataMapper()
        ghost_mapper.SetInputConnection(ghost_edges.GetOutputPort())
        ghost = vtk.vtkActor()
        ghost.SetMapper(ghost_mapper)
        ghost.GetProperty().SetColor(0.45, 0.45, 0.45)
        ghost.GetProperty().SetOpacity(0.35)
        self._renderer.AddActor(ghost)
        self._renderer.AddActor(actor)
        bar = vtk.vtkScalarBarActor()
        bar.SetLookupTable(lut)
        bar.SetTitle("")
        bar.SetNumberOfLabels(4)
        bar.SetWidth(0.09)
        bar.SetPosition(0.88, 0.15)
        bar.SetHeight(0.7)
        title_prop = bar.GetTitleTextProperty()
        title_prop.SetColor(0.1, 0.1, 0.1)
        title_prop.SetFontSize(15)
        title_prop.BoldOff()
        _apply_text_font(title_prop)
        label_prop = bar.GetLabelTextProperty()
        label_prop.SetColor(0.1, 0.1, 0.1)
        label_prop.SetFontSize(13)
        label_prop.BoldOff()
        _apply_text_font(label_prop)
        bar.SetLabelFormat("%.3g")
        self._renderer.AddActor2D(bar)
        self._renderer.ResetCamera()
        self._render()

    def fit(self) -> None:
        if self.available:
            self._renderer.ResetCamera()
            self._render()

    def rescale(self, factor: float) -> None:
        if self.available and getattr(self, "_base_scale", None) is not None:
            self.show_results(self._last_model, self._last_result,
                              scale=self._base_scale * factor)

    def set_view(self, name: str) -> None:
        if not self.available:
            return
        camera = self._renderer.GetActiveCamera()
        views = {
            "iso": ((1, 0.6, 0.8), (0, 0, 1)),
            "front": ((0, -1, 0.15), (0, 0, 1)),
            "top": ((0, 0, 1), (0, 1, 0)),
        }
        direction, up = views.get(name, views["iso"])
        focal = camera.GetFocalPoint()
        dist = camera.GetDistance()
        camera.SetPosition(
            focal[0] + direction[0] * dist,
            focal[1] + direction[1] * dist,
            focal[2] + direction[2] * dist,
        )
        camera.SetViewUp(*up)
        self._renderer.ResetCamera()
        self._render()
