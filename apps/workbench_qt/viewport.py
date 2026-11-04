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

try:
    from .theme import current_theme_mode, get_palette, register_theme_listener
except (ImportError, ValueError):  # Fallback when loaded outside a package by tests
    from apps.workbench_qt.theme import (
        current_theme_mode,
        get_palette,
        register_theme_listener,
    )

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


import math

def compute_von_mises(stresses: dict[int, list[float]], node_ids: list[int]) -> np.ndarray:
    """Calculate Von Mises equivalent stress at given nodes from 6 components.

    Components: [Sxx, Syy, Szz, Sxy, Syz, Szx]
    vm = sqrt(0.5 * ((Sxx-Syy)^2 + (Syy-Szz)^2 + (Szz-Sxx)^2 + 6*(Sxy^2 + Syz^2 + Szx^2)))
    Nodes without valid 6-component stress are set to np.nan (masked per AC-3).
    """
    vm = np.full(len(node_ids), np.nan, dtype=float)
    for i, nid in enumerate(node_ids):
        vec = stresses.get(nid) if stresses else None
        if vec and len(vec) >= 6 and all(map(math.isfinite, vec[:6])):
            sxx, syy, szz, sxy, syz, szx = vec[:6]
            val_sq = 0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2 + 6.0 * (sxy ** 2 + syz ** 2 + szx ** 2))
            vm[i] = math.sqrt(max(0.0, val_sq))
    return vm


def deduce_stress_unit(units: dict) -> str | None:
    """Deduce stress unit from result units.

    mm + N -> MPa
    m + N -> Pa
    Otherwise -> None (undeclared / [단위 미선언])
    """
    length = str(units.get("length", "")).lower()
    force = str(units.get("force", "")).lower()
    if length == "mm" and force == "n":
        return "MPa"
    elif length == "m" and force == "n":
        return "Pa"
    return None

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
        self._fallback_label = None
        self._axes_actor = None
        self._edge_actor = None
        self._scalar_bar = None
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
            label.setObjectName("panelHint")
            from PySide6.QtCore import Qt

            label.setAlignment(Qt.AlignCenter)
            self._layout.addWidget(label)
            self._fallback_label = label
        self.apply_theme(current_theme_mode())
        register_theme_listener(self.apply_theme)

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
        self._axes_actor = axes
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
        self._last_model, self._last_result = None, None
        self._base_scale = None
        if not self.available:
            return
        self._grid, self._node_ids = build_grid(model)
        self._renderer.RemoveAllViewProps()
        self._edge_actor = None
        self._scalar_bar = None
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
        eactor.GetProperty().SetColor(*get_palette()["vtk_edge_rgb"])
        self._edge_actor = eactor
        self._renderer.AddActor(eactor)
        self._renderer.ResetCamera()
        self._render()

    def show_results(
        self,
        model: StructuralModel,
        result: ResultSet,
        field_name: str = "U_mag",
        scale_mode: str = "auto",
        scale_factor: float = 1.0,
        scale: float | None = None,
    ) -> None:
        self._last_model = model
        self._last_result = result
        self._last_field = field_name
        self._last_scale_mode = scale_mode
        self._last_scale_factor = scale_factor
        if not self.available or self._grid is None:
            return
        if not self._node_ids or any(n not in result.displacements for n in self._node_ids):
            self._base_scale = None
            self._auto_scale = None
            self._last_model, self._last_result = None, None
            self.show_model(model)
            return

        disp = np.array([result.displacements[n] for n in self._node_ids], dtype=float)
        # AC-4: Keep displacements in the same length unit as mesh coordinates
        model_unit = getattr(model, "unit_system", "si")
        model_len = "mm" if model_unit in ("mm", "mm-t-s") else "m"
        result_len = result.units.get("length", model_len)
        if model_len == "mm" and result_len == "m":
            disp = disp * 1000.0
        elif model_len == "m" and result_len == "mm":
            disp = disp / 1000.0
        magnitude = np.linalg.norm(disp, axis=1)

        grid = vtk.vtkUnstructuredGrid()
        grid.DeepCopy(self._grid)
        vec = numpy_to_vtk(disp, deep=True)
        vec.SetName("U")
        grid.GetPointData().AddArray(vec)
        grid.GetPointData().SetActiveVectors("U")
        mag = numpy_to_vtk(magnitude, deep=True)
        mag.SetName("displacement_magnitude")
        grid.GetPointData().AddArray(mag)

        ux = numpy_to_vtk(disp[:, 0], deep=True)
        ux.SetName("U_x")
        grid.GetPointData().AddArray(ux)
        uy = numpy_to_vtk(disp[:, 1], deep=True)
        uy.SetName("U_y")
        grid.GetPointData().AddArray(uy)
        uz = numpy_to_vtk(disp[:, 2], deep=True)
        uz.SetName("U_z")
        grid.GetPointData().AddArray(uz)

        # Deformation scale computation (AC-4)
        bounds = grid.GetBounds()
        size = max(bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4])
        peak = float(np.max(magnitude)) or 1.0
        auto_scale = 0.25 * size / peak
        self._auto_scale = auto_scale

        if scale is not None:
            actual_scale = scale
        elif scale_mode == "real":
            actual_scale = 1.0 * scale_factor
        else:  # auto
            actual_scale = auto_scale * scale_factor
        self._base_scale = actual_scale

        warp = vtk.vtkWarpVector()
        warp.SetInputData(grid)
        warp.SetInputArrayToProcess(0, 0, 0, vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS, "U")
        warp.SetScaleFactor(actual_scale)
        self._last_warp = warp
        surface = vtk.vtkDataSetSurfaceFilter()
        surface.SetInputConnection(warp.GetOutputPort())

        # Determine active field, units, and data location
        active_scalar_name = "displacement_magnitude"
        legend_title = ""
        lut = vtk.vtkColorTransferFunction()
        lut.SetNanColor(0.55, 0.55, 0.55)  # AC-3: NaN masked rendered neutral gray

        length_unit = "mm" if result.units.get("length") == "m" else (result.units.get("length") or "mm")

        if field_name == "von_mises":
            stress_unit = deduce_stress_unit(result.units)
            if stress_unit is None:
                # AC-3: [단위 미선언] 경고 출력 및 파생 응력 표시 보류
                active_scalar_name = None
                legend_title = "[단위 미선언]\n파생 응력 표시 보류"
                lo, hi = 0.0, 1.0
            else:
                vm_values = compute_von_mises(getattr(result, "stresses", {}), self._node_ids)
                vm_arr = numpy_to_vtk(vm_values, deep=True)
                vm_arr.SetName("von_mises")
                grid.GetPointData().AddArray(vm_arr)
                active_scalar_name = "von_mises"
                legend_title = f"Von Mises [{stress_unit}]\n[절점 외삽]"
                valid = vm_values[np.isfinite(vm_values)]
                lo = float(np.min(valid)) if len(valid) else 0.0
                hi = float(np.max(valid)) if len(valid) else 1.0
                if lo == hi:
                    hi = lo + 1.0
        elif field_name == "U_x":
            active_scalar_name = "U_x"
            legend_title = f"Displacement X [{length_unit}]\n[절점]"
            lo, hi = float(disp[:, 0].min()), float(disp[:, 0].max())
            if lo == hi:
                hi = lo + 1e-6
        elif field_name == "U_y":
            active_scalar_name = "U_y"
            legend_title = f"Displacement Y [{length_unit}]\n[절점]"
            lo, hi = float(disp[:, 1].min()), float(disp[:, 1].max())
            if lo == hi:
                hi = lo + 1e-6
        elif field_name == "U_z":
            active_scalar_name = "U_z"
            legend_title = f"Displacement Z [{length_unit}]\n[절점]"
            lo, hi = float(disp[:, 2].min()), float(disp[:, 2].max())
            if lo == hi:
                hi = lo + 1e-6
        else:  # U_mag
            active_scalar_name = "displacement_magnitude"
            legend_title = f"Displacement [{length_unit}]\n[절점]"
            lo, hi = float(magnitude.min()), float(magnitude.max()) or 1.0
            if lo == hi:
                hi = lo + 1e-6

        # Build color lookup table (Blue -> Cyan -> Green -> Yellow -> Red)
        lut.AddRGBPoint(lo, 0.0, 0.0, 0.85)
        lut.AddRGBPoint(lo + 0.25 * (hi - lo), 0.0, 0.75, 0.75)
        lut.AddRGBPoint(lo + 0.5 * (hi - lo), 0.0, 0.8, 0.35)
        lut.AddRGBPoint(lo + 0.75 * (hi - lo), 0.9, 0.75, 0.0)
        lut.AddRGBPoint(hi, 0.9, 0.05, 0.0)

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(surface.GetOutputPort())

        if active_scalar_name is not None:
            grid.GetPointData().SetActiveScalars(active_scalar_name)
            mapper.SetScalarModeToUsePointData()
            mapper.SelectColorArray(active_scalar_name)
            mapper.SetLookupTable(lut)
            mapper.SetScalarRange(lo, hi)
        else:
            mapper.ScalarVisibilityOff()

        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        if active_scalar_name is None:
            actor.GetProperty().SetColor(0.62, 0.71, 0.81)

        self._renderer.RemoveAllViewProps()
        self._edge_actor = None
        self._scalar_bar = None

        # Ghost undeformed edges
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

        # Scalar bar
        if active_scalar_name is not None:
            bar = vtk.vtkScalarBarActor()
            bar.SetLookupTable(lut)
            bar.SetTitle(legend_title)
            bar.SetNumberOfLabels(5)
            bar.SetWidth(0.12)
            bar.SetPosition(0.85, 0.15)
            bar.SetHeight(0.7)
            rgb = get_palette()["vtk_text_rgb"]
            title_prop = bar.GetTitleTextProperty()
            title_prop.SetColor(*rgb)
            title_prop.SetFontSize(13)
            title_prop.BoldOff()
            _apply_text_font(title_prop)
            label_prop = bar.GetLabelTextProperty()
            label_prop.SetColor(*rgb)
            label_prop.SetFontSize(11)
            label_prop.BoldOff()
            _apply_text_font(label_prop)
            bar.SetLabelFormat("%.3g")
            self._scalar_bar = bar
            self._renderer.AddActor(bar)

        self._renderer.ResetCamera()
        self._render()

    def update_display(
        self,
        field_name: str | None = None,
        scale_mode: str | None = None,
        scale_factor: float | None = None,
    ) -> None:
        if not self.available or getattr(self, "_last_model", None) is None or getattr(self, "_last_result", None) is None:
            return
        field = field_name if field_name is not None else getattr(self, "_last_field", "U_mag")
        mode = scale_mode if scale_mode is not None else getattr(self, "_last_scale_mode", "auto")
        factor = scale_factor if scale_factor is not None else getattr(self, "_last_scale_factor", 1.0)
        self.show_results(self._last_model, self._last_result, field_name=field, scale_mode=mode, scale_factor=factor)

    def fit(self) -> None:
        if self.available:
            self._renderer.ResetCamera()
            self._render()

    def rescale(self, factor: float) -> None:
        self.update_display(scale_factor=factor)

    def set_field(self, field_name: str) -> None:
        self.update_display(field_name=field_name)

    def set_scale_mode(self, mode: str) -> None:
        self.update_display(scale_mode=mode)

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

    def apply_theme(self, mode: str | None = None) -> None:
        p = get_palette(mode)
        if self._fallback_label is not None:
            self._fallback_label.setStyleSheet(f"color: {p['text_secondary']};")
        if self.available and self._renderer is not None:
            self._renderer.SetBackground(*p["vtk_bg_top"])
            self._renderer.SetBackground2(*p["vtk_bg_bottom"])
            if self._axes_actor is not None:
                rgb = p["vtk_text_rgb"]
                for axis_actor in (
                    self._axes_actor.GetXAxisCaptionActor2D(),
                    self._axes_actor.GetYAxisCaptionActor2D(),
                    self._axes_actor.GetZAxisCaptionActor2D(),
                ):
                    if axis_actor is not None:
                        prop = axis_actor.GetCaptionTextProperty()
                        if prop is not None:
                            prop.SetColor(*rgb)
            if self._edge_actor is not None:
                self._edge_actor.GetProperty().SetColor(*p["vtk_edge_rgb"])
            if self._scalar_bar is not None:
                rgb = p["vtk_text_rgb"]
                tprop = self._scalar_bar.GetTitleTextProperty()
                if tprop is not None:
                    tprop.SetColor(*rgb)
                lprop = self._scalar_bar.GetLabelTextProperty()
                if lprop is not None:
                    lprop.SetColor(*rgb)
            self._render()

