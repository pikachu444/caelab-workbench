"""Small shared Qt plot surface and optional native VTK scene."""
from __future__ import annotations
import os
import numpy as np
from PySide6.QtCore import Signal,Qt,QEvent,QTimer
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QSizePolicy
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
        self.mpl_toolbar=NavigationToolbar2QT(self.canvas,self)
        self.layout.addWidget(self.mpl_toolbar)
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
        if hasattr(self,'native') and self.native.available and not self.native.isHidden(): self.native.show_empty(text)
        self.ax.clear()
        self._picks.clear()
        self._apply_theme()
        self.ax.text(.5, .5, text, ha='center', va='center', color='#94a3b8', transform=self.ax.transAxes)
        self.canvas.draw_idle()

    def set_selected(self, point_ids):
        self._selected = set(point_ids)

    def set_native_mode(self,active):
        self.canvas.setVisible(not active); self.mpl_toolbar.setVisible(not active)
        self.native.setVisible(active)

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
        self.axes=None; self.physical_ranges=None; self._press=None; self._dragged=False
        self.legend=QLabel(self); self.legend.setWordWrap(True); self.legend.hide()
        self.axis_key=QLabel(self); self.axis_key.setWordWrap(True); self.axis_key.setTextFormat(Qt.TextFormat.PlainText)
        self.axis_key.setAccessibleName('3D physical axes'); self.axis_key.hide()
        for label in (self.legend,self.axis_key):
            label.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
            label.setStyleSheet('color: #e2e8f0; background: #13171e; padding: 2px 4px;')
        if os.environ.get('QT_QPA_PLATFORM') not in ('offscreen', 'minimal'):
            try:
                from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
                from vtkmodules.vtkRenderingCore import vtkRenderer, vtkPointPicker
                import vtkmodules.vtkRenderingOpenGL2
                from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
                self.widget = QVTKRenderWindowInteractor(self)
                self.widget.installEventFilter(self)
                self.renderer = vtkRenderer()
                self.renderer.SetBackground(.075, .09, .12)
                self.widget.GetRenderWindow().AddRenderer(self.renderer)
                self.picker = vtkPointPicker()
                self.picker.SetTolerance(.02)
                interactor=self.widget.GetRenderWindow().GetInteractor()
                self.interaction_style=vtkInteractorStyleTrackballCamera(); interactor.SetInteractorStyle(self.interaction_style)
                self.picker.PickFromListOn(); interactor.SetPicker(self.picker)
                # Trackball grabs interactor focus during a drag. Observe its forwarded
                # events so release is received, while retaining the native camera actions.
                self.interaction_style.AddObserver('LeftButtonPressEvent',self._style_press)
                self.interaction_style.AddObserver('MouseMoveEvent',self._style_move)
                self.interaction_style.AddObserver('LeftButtonReleaseEvent',self._style_release)
                interactor.AddObserver('EndInteractionEvent',lambda *_:self._update_annotation_layout())
                self.widget.Initialize()
                reset=QPushButton('Fit 3D view')
                reset.setToolTip('Restore the oblique z-up view and fit the data and physical axes.')
                reset.clicked.connect(self.reset_camera)
                controls=QHBoxLayout(); controls.addWidget(reset)
                self.instructions=QLabel('Drag: rotate   Middle / Shift-drag: pan   Wheel: zoom')
                self.instructions.setWordWrap(True); controls.addWidget(self.instructions,1)
                layout.addLayout(controls); layout.addWidget(self.legend); layout.addWidget(self.axis_key)
                layout.addWidget(self.widget)
                self.available = True
            except ImportError:
                pass
        self.message = QLabel('2D slice fallback — native VTK 3D is unavailable in this session.')
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.message.setVisible(not self.available)

    def set_legend(self,layers):
        self.legend.setText('   '.join(f'<span style="color:{color}">●</span> {name}' for name,color in layers))
        self.legend.setVisible(bool(layers) and self.available)

    def show_empty(self,text):
        if self.available:
            self.clear(); self.axis_key.clear(); self.axis_key.hide(); self.message.setText(text); self.message.show()
            self.widget.GetRenderWindow().Render()

    def _begin_pick(self,interactor,event):
        self._press=tuple(interactor.GetEventPosition()); self._dragged=False

    def _style_press(self,style,event):
        self._begin_pick(style.GetInteractor(),event); style.OnLeftButtonDown()

    def _style_move(self,style,event):
        self._track_pick(style.GetInteractor(),event); style.OnMouseMove()

    def _style_release(self,style,event):
        style.OnLeftButtonUp(); self._pick(style.GetInteractor(),event)

    def _track_pick(self,interactor,event):
        if self._press is not None:
            x,y=interactor.GetEventPosition(); threshold=4*self.widget.devicePixelRatioF()
            if (x-self._press[0])**2+(y-self._press[1])**2>threshold**2: self._dragged=True

    def _pick(self, interactor, event):
        if self._press is None: return
        self._track_pick(interactor,event)
        allowed=not self._dragged; self._press=None
        if not allowed: return
        x, y = interactor.GetEventPosition()
        self.picker.Pick(x, y, 0, self.renderer)
        ids = self.actors.get(self.picker.GetActor(), ())
        index = self.picker.GetPointId()
        if 0 <= index < len(ids): self.point_selected.emit(ids[index])

    def clear(self):
        if self.available:
            self.renderer.RemoveAllViewProps()
            self.actors.clear()
            self.picker.InitializePickList(); self.axes=None; self.physical_ranges=None; self._press=None; self._dragged=False

    def points(self, xyz, ids, color=(.2,.7,.9), size=7):
        if not self.available or not len(xyz): return
        from vtkmodules.vtkCommonCore import vtkPoints
        from vtkmodules.vtkCommonDataModel import vtkPolyData, vtkCellArray
        from vtkmodules.vtkRenderingCore import vtkPolyDataMapper, vtkActor
        points, cells = vtkPoints(), vtkCellArray(); points.SetDataTypeToDouble()
        for value in xyz:
            i = points.InsertNextPoint(*map(float, value))
            cells.InsertNextCell(1); cells.InsertCellPoint(i)
        data = vtkPolyData(); data.SetPoints(points); data.SetVerts(cells)
        mapper = vtkPolyDataMapper(); mapper.SetInputData(data)
        actor = vtkActor(); actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*color); actor.GetProperty().SetPointSize(size*self.widget.devicePixelRatioF())
        self.renderer.AddActor(actor)
        self.picker.AddPickList(actor)
        self.actors[actor] = tuple(ids)

    def surface(self, x, y, z, color=(.2,.65,.8), opacity=.8):
        if not self.available: return
        from vtkmodules.vtkCommonCore import vtkPoints
        from vtkmodules.vtkCommonDataModel import vtkPolyData, vtkCellArray
        from vtkmodules.vtkRenderingCore import vtkPolyDataMapper, vtkActor
        points, cells = vtkPoints(), vtkCellArray(); points.SetDataTypeToDouble()
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
        actor.PickableOff()
        actor.GetProperty().SetColor(*color); actor.GetProperty().SetOpacity(opacity)
        self.renderer.AddActor(actor)

    def finish(self, labels):
        if not self.available: return
        self.message.hide()
        from vtkmodules.vtkRenderingAnnotation import vtkCubeAxesActor2D
        bounds=self.renderer.ComputeVisiblePropBounds()
        if any(bounds[i]>bounds[i+1] for i in (0,2,4)) or not np.all(np.isfinite(bounds)):
            self.show_empty('No finite 3D values are available. Check the response selection and model domain.'); return
        self.physical_ranges=tuple(bounds)
        ranges=list(bounds); labels=list(labels)
        for i in range(3):
            if ranges[2*i]==ranges[2*i+1]:
                value=ranges[2*i]; pad=abs(value)*.05 if value else 1.
                ranges[2*i],ranges[2*i+1]=value-pad,value+pad
                labels[i]+=f' (constant {value:.6g})'
        spans=[ranges[i+1]-ranges[i] for i in (0,2,4)]
        # Each axis has different units; normalize scene geometry, retain physical tick ranges.
        actors=self.renderer.GetActors(); actors.InitTraversal()
        for _ in range(actors.GetNumberOfItems()):
            actor=actors.GetNextActor()
            actor.SetScale(*[1./span for span in spans])
            actor.SetPosition(*[-ranges[2*i]/span for i,span in enumerate(spans)])
        axes=vtkCubeAxesActor2D(); self.axes=axes
        axes.SetBounds(0,1,0,1,0,1)
        axes.SetRanges(ranges); axes.UseRangesOn(); axes.ScalingOff(); axes.PickableOff(); axes.UseBoundsOff()
        axes.SetCamera(self.renderer.GetActiveCamera())
        axes.SetXLabel('X'); axes.SetYLabel('Y'); axes.SetZLabel('Z'); axes.SetCornerOffset(0); axes.SetFlyModeToOuterEdges()
        self.axis_key.setText('   '.join(f'{axis}: {label}' for axis,label in zip('XYZ',labels))); self.axis_key.show()
        for prop in (axes.GetAxisTitleTextProperty(),axes.GetAxisLabelTextProperty()):
            prop.SetColor(.85,.9,.95); prop.SetBold(False); prop.SetItalic(False); prop.SetShadow(False)
        axes.GetAxisTitleTextProperty().SetBackgroundColor(.075,.09,.12)
        axes.GetAxisTitleTextProperty().SetBackgroundOpacity(1.)
        for actor,position in zip((axes.GetXAxisActor2D(),axes.GetYAxisActor2D(),axes.GetZAxisActor2D()),(0,.5,1)):
            actor.UseFontSizeFromPropertyOn(); actor.SetTitlePosition(position)
        self._update_annotation_layout(render=False)
        self.renderer.AddActor(axes)
        if not self._camera_initialized:
            self._fit_camera()
        self.instructions.setText('Drag: rotate   Middle / Shift-drag: pan   Wheel: zoom'+('   Click point: select' if self.actors else ''))
        self.widget.GetRenderWindow().Render()

    def eventFilter(self,watched,event):
        if self.available and watched is self.widget and event.type() in (QEvent.Type.Resize,QEvent.Type.DevicePixelRatioChange):
            QTimer.singleShot(0,self._update_annotation_layout)
        return super().eventFilter(watched,event)

    def _update_annotation_layout(self,render=True):
        if not self.available or self.axes is None: return
        for prop in (self.axes.GetAxisTitleTextProperty(),self.axes.GetAxisLabelTextProperty()): prop.SetFontSize(round(12*self.widget.devicePixelRatioF()))
        for actor,offset in zip((self.axes.GetXAxisActor2D(),self.axes.GetYAxisActor2D(),self.axes.GetZAxisActor2D()),(12,12,32)):
            actor.SetTickOffset(round(offset*self.widget.devicePixelRatioF()))
        origin=self._project((0,0,0)); lengths=[np.linalg.norm(np.array(self._project(point))-origin)/self.widget.devicePixelRatioF() for point in ((1,0,0),(0,1,0),(0,0,1))]
        count,precision=axis_label_layout(self.axes.GetRanges(),self.widget.width(),self.widget.height(),min(lengths))
        self.axes.SetNumberOfLabels(count); self.axes.SetLabelFormat(f'%.{precision}g')
        if render: self.widget.GetRenderWindow().Render()

    def _project(self,point):
        self.renderer.SetWorldPoint(*point,1.); self.renderer.WorldToDisplay()
        return self.renderer.GetDisplayPoint()[:2]

    def _fit_camera(self):
        camera=self.renderer.GetActiveCamera()
        camera.SetFocalPoint(.5,.5,.5); camera.SetPosition(2.5,-3.,2.2); camera.SetViewUp(0,0,1)
        camera.ParallelProjectionOn(); self.renderer.ResetCamera(0,1,0,1,0,1)
        camera.SetParallelScale(camera.GetParallelScale()*1.5)
        self.renderer.ResetCameraClippingRange(); self._camera_initialized=True
        self._update_annotation_layout(render=False)

    def reset_camera(self):
        if self.available:
            self._fit_camera()
            self.widget.GetRenderWindow().Render()

    def closeEvent(self, event):
        if self.available:
            self.available=False; self.widget.Finalize()
        super().closeEvent(event)


def axis_label_layout(ranges,width,height,axis_pixels=None):
    """Keep ticks distinct without shrinking text; reduce density in small viewports."""
    for count in range(4,1,-1):
        ticks=[np.linspace(ranges[i],ranges[i+1],count) for i in (0,2,4)]
        if any(len(set(values))<count for values in ticks): continue
        precision=next((p for p in range(3,18) if all(len({format(value,f'.{p}g') for value in values})==count for values in ticks)),17)
        longest=max(len(format(value,f'.{precision}g')) for values in ticks for value in values)
        available=max(40,min(width,height)*.35) if axis_pixels is None else axis_pixels
        if count==2 or (count-1)*(longest*7.2+16)<=available: return count,precision
    return 2,17
