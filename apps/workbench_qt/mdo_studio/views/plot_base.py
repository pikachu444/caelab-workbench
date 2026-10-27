"""Small shared Qt plot surface and optional native VTK scene."""
from __future__ import annotations
import os
import numpy as np
from PySide6.QtCore import Signal,Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel,QPushButton
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure


class PlotWidget(QWidget):
    point_selected = Signal(str)
    filter_changed = Signal(object)

    def __init__(self, parent=None, is_dark=True):
        super().__init__(parent)
        self.is_dark = is_dark
        self.fig = Figure(figsize=(6, 4), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.ax = self.fig.add_subplot(111)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.controls = QVBoxLayout()
        self.layout.addLayout(self.controls)
        self.layout.addWidget(NavigationToolbar2QT(self.canvas, self))
        self.layout.addWidget(self.canvas, 1)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.layout.addWidget(self.note)
        self._picks = {}
        self._selected = set()
        self.canvas.mpl_connect('pick_event', self._pick)
        self._apply_theme()

    def _pick(self, event):
        ids = self._picks.get(event.artist, ())
        if len(event.ind) and event.ind[0] < len(ids):
            self.point_selected.emit(ids[event.ind[0]])

    def _apply_theme(self):
        bg, fg, grid = ("#13171e", "#e2e8f0", "#334155") if self.is_dark else ("#f8fafc", "#0f172a", "#cbd5e1")
        self.fig.patch.set_facecolor(bg)
        self.ax.set_facecolor(bg)
        self.ax.tick_params(colors=fg, labelsize=9)
        for axis in (self.ax.xaxis, self.ax.yaxis): axis.label.set_color(fg)
        self.ax.title.set_color(fg)
        for spine in self.ax.spines.values(): spine.set_color(grid)
        self.ax.grid(True, alpha=.3, color=grid)
        legend = self.ax.get_legend()
        if legend:
            legend.get_frame().set_facecolor(bg)
            for text in legend.get_texts(): text.set_color(fg)

    def set_theme(self, is_dark):
        self.is_dark = is_dark
        self._apply_theme()
        self.canvas.draw_idle()

    def empty(self, text):
        self.ax.clear()
        self._picks.clear()
        self._apply_theme()
        self.ax.text(.5, .5, text, ha='center', va='center', color='#94a3b8', transform=self.ax.transAxes)
        self.canvas.draw_idle()

    def set_selected(self, point_ids):
        self._selected = set(point_ids)

    def closeEvent(self,event):
        if hasattr(self,'native'): self.native.close()
        if hasattr(self.canvas,'_draw_pending'): self.canvas._draw_pending=False
        super().closeEvent(event)


class Native3D(QWidget):
    point_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.available = False
        self._camera_initialized = False
        self.actors = {}
        if os.environ.get('QT_QPA_PLATFORM') not in ('offscreen', 'minimal'):
            try:
                from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
                from vtkmodules.vtkRenderingCore import vtkRenderer, vtkPointPicker
                import vtkmodules.vtkRenderingOpenGL2
                import vtkmodules.vtkInteractionStyle
                self.widget = QVTKRenderWindowInteractor(self)
                self.renderer = vtkRenderer()
                self.renderer.SetBackground(.075, .09, .12)
                self.widget.GetRenderWindow().AddRenderer(self.renderer)
                self.picker = vtkPointPicker()
                self.picker.SetTolerance(.02)
                self.widget.GetRenderWindow().GetInteractor().SetPicker(self.picker)
                self.widget.GetRenderWindow().GetInteractor().AddObserver('LeftButtonReleaseEvent', self._pick)
                self.widget.Initialize()
                reset=QPushButton('Fit 3D view')
                reset.clicked.connect(self.reset_camera)
                layout.addWidget(reset)
                layout.addWidget(self.widget)
                self.available = True
            except ImportError:
                pass
        self.message = QLabel('2D slice fallback — native VTK 3D is unavailable in this session.')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.message.setVisible(not self.available)

    def _pick(self, interactor, event):
        x, y = interactor.GetEventPosition()
        self.picker.Pick(x, y, 0, self.renderer)
        ids = self.actors.get(self.picker.GetActor(), ())
        index = self.picker.GetPointId()
        if 0 <= index < len(ids): self.point_selected.emit(ids[index])

    def clear(self):
        if self.available:
            self.renderer.RemoveAllViewProps()
            self.actors.clear()

    def points(self, xyz, ids, color=(.2,.7,.9), size=7):
        if not self.available or not len(xyz): return
        from vtkmodules.vtkCommonCore import vtkPoints
        from vtkmodules.vtkCommonDataModel import vtkPolyData, vtkCellArray
        from vtkmodules.vtkRenderingCore import vtkPolyDataMapper, vtkActor
        points, cells = vtkPoints(), vtkCellArray()
        for value in xyz:
            i = points.InsertNextPoint(*map(float, value))
            cells.InsertNextCell(1); cells.InsertCellPoint(i)
        data = vtkPolyData(); data.SetPoints(points); data.SetVerts(cells)
        mapper = vtkPolyDataMapper(); mapper.SetInputData(data)
        actor = vtkActor(); actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*color); actor.GetProperty().SetPointSize(size)
        self.renderer.AddActor(actor)
        self.actors[actor] = tuple(ids)

    def surface(self, x, y, z, color=(.2,.65,.8), opacity=.8):
        if not self.available: return
        from vtkmodules.vtkCommonCore import vtkPoints
        from vtkmodules.vtkCommonDataModel import vtkPolyData, vtkCellArray
        from vtkmodules.vtkRenderingCore import vtkPolyDataMapper, vtkActor
        points, cells = vtkPoints(), vtkCellArray()
        for value in zip(x.ravel(), y.ravel(), z.ravel()): points.InsertNextPoint(*map(float,value))
        rows, cols = z.shape
        for i in range(rows-1):
            for j in range(cols-1):
                indices = (i*cols+j, i*cols+j+1, (i+1)*cols+j+1, (i+1)*cols+j)
                if all(np.isfinite(z.ravel()[k]) for k in indices):
                    cells.InsertNextCell(4)
                    for k in indices: cells.InsertCellPoint(k)
        data=vtkPolyData(); data.SetPoints(points); data.SetPolys(cells)
        mapper=vtkPolyDataMapper(); mapper.SetInputData(data)
        actor=vtkActor(); actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*color); actor.GetProperty().SetOpacity(opacity)
        self.renderer.AddActor(actor)

    def finish(self, labels):
        if not self.available: return
        from vtkmodules.vtkRenderingAnnotation import vtkCubeAxesActor
        bounds=self.renderer.ComputeVisiblePropBounds()
        if any(bounds[i]>bounds[i+1] for i in (0,2,4)): return
        spans=[bounds[i+1]-bounds[i] or 1. for i in (0,2,4)]
        # Each axis has different units; normalize scene geometry, retain physical tick ranges.
        actors=self.renderer.GetActors(); actors.InitTraversal()
        for _ in range(actors.GetNumberOfItems()):
            actor=actors.GetNextActor()
            actor.SetScale(*[1./span for span in spans])
            actor.SetPosition(*[-bounds[2*i]/span for i,span in enumerate(spans)])
        axes=vtkCubeAxesActor()
        axes.SetBounds(0,1,0,1,0,1)
        axes.SetXAxisRange(bounds[0],bounds[1]); axes.SetYAxisRange(bounds[2],bounds[3]); axes.SetZAxisRange(bounds[4],bounds[5])
        axes.SetCamera(self.renderer.GetActiveCamera())
        axes.SetXTitle(labels[0]); axes.SetYTitle(labels[1]); axes.SetZTitle(labels[2])
        for i in range(3):
            axes.GetTitleTextProperty(i).SetColor(.85,.9,.95)
            axes.GetLabelTextProperty(i).SetColor(.85,.9,.95)
        self.renderer.AddActor(axes)
        if not self._camera_initialized:
            self.renderer.ResetCamera()
            self._camera_initialized=True
        self.widget.GetRenderWindow().Render()

    def reset_camera(self):
        if self.available:
            self.renderer.ResetCamera()
            self.widget.GetRenderWindow().Render()

    def closeEvent(self, event):
        if self.available: self.widget.Finalize()
        super().closeEvent(event)
