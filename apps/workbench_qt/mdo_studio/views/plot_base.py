"""Small shared Qt plot surface and optional native VTK scene."""
from __future__ import annotations
import os
from itertools import product
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
        self._initial_fit_pending=False
        self._initial_fit_timer=QTimer(self); self._initial_fit_timer.setSingleShot(True); self._initial_fit_timer.timeout.connect(self._finish_initial_fit)
        self._fit_timer_axes=None
        self.actors = {}
        self.axes=None; self.physical_ranges=None; self._press=None; self._dragged=False
        self.annotation_layout=None
        self._endpoint_labels=[]
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
                self.renderer.TwoSidedLightingOn()
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
                interactor.AddObserver('StartInteractionEvent',self._camera_interacted)
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

    def _camera_interacted(self,*args):
        self._initial_fit_pending=False; self._camera_initialized=True; self._initial_fit_timer.stop()

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
            self._initial_fit_timer.stop(); self._fit_timer_axes=None
            self.renderer.RemoveAllViewProps()
            self._endpoint_labels.clear()
            self.actors.clear()
            self.picker.InitializePickList(); self.axes=None; self.physical_ranges=None; self.annotation_layout=None; self._press=None; self._dragged=False

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
        from vtkmodules.vtkFiltersCore import vtkPolyDataNormals
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
        normals=vtkPolyDataNormals(); normals.SetInputData(data); normals.SplittingOff(); normals.ConsistencyOn()
        mapper=vtkPolyDataMapper(); mapper.SetInputConnection(normals.GetOutputPort())
        actor=vtkActor(); actor.SetMapper(mapper)
        actor.PickableOff()
        actor.GetProperty().SetColor(*color); actor.GetProperty().SetOpacity(opacity)
        actor.GetProperty().SetAmbient(.65); actor.GetProperty().SetDiffuse(.35)
        actor.GetProperty().BackfaceCullingOff(); actor.GetProperty().FrontfaceCullingOff()
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
            actor.AdjustLabelsOff(); actor.SnapLabelsToGridOff(); actor.SkipFirstTickOff(); actor.SetNumberOfMinorTicks(0)
        self._update_annotation_layout(render=False)
        self.renderer.AddActor(axes)
        if not self._camera_initialized or (self._initial_fit_pending and self._render_ready()):
            self._fit_camera(defer_until_visible=not self._render_ready())
        else: self._update_annotation_layout()
        self.instructions.setText('Drag: rotate   Middle / Shift-drag: pan   Wheel: zoom'+('   Click point: select' if self.actors else ''))

    def eventFilter(self,watched,event):
        if self.available and watched is self.widget and event.type() in (QEvent.Type.Resize,QEvent.Type.DevicePixelRatioChange):
            if self._initial_fit_pending and self.isVisible():
                self._fit_timer_axes=self.axes; self._initial_fit_timer.start(0)
            else: QTimer.singleShot(0,self._update_annotation_layout)
        return super().eventFilter(watched,event)

    def showEvent(self,event):
        super().showEvent(event)
        if self._initial_fit_pending:
            self._fit_timer_axes=self.axes; self._initial_fit_timer.start(0)

    def _finish_initial_fit(self):
        if self.available and self.axes is not None and self.axes is self._fit_timer_axes and self._render_ready() and self._initial_fit_pending:
            self._fit_camera()

    def _render_ready(self):
        return self.isVisible() and min(*self.renderer.GetSize(),self.widget.width(),self.widget.height())>0

    def _update_annotation_layout(self,render=True):
        if not self.available or self.axes is None: return
        for prop in (self.axes.GetAxisTitleTextProperty(),self.axes.GetAxisLabelTextProperty()): prop.SetFontSize(round(12*self.widget.devicePixelRatioF()))
        for actor,offset in zip((self.axes.GetXAxisActor2D(),self.axes.GetYAxisActor2D(),self.axes.GetZAxisActor2D()),(12,12,32)):
            actor.SetTickOffset(round(offset*self.widget.devicePixelRatioF()))
        origin=self._project((0,0,0)); lengths=[np.linalg.norm(np.array(self._project(point))-origin)/self.widget.devicePixelRatioF() for point in ((1,0,0),(0,1,0),(0,0,1))]
        count,precision=axis_label_layout(self.axes.GetRanges(),self.widget.width(),self.widget.height(),min(lengths))
        self.axes.SetNumberOfLabels(count); self.axes.SetLabelFormat(f'%.{precision}g')
        if render:
            self._render_annotations(); self._annotation_warning()

    def _render_annotations(self):
        for actor in self._endpoint_labels: self.renderer.RemoveActor2D(actor)
        self._endpoint_labels.clear()
        self.widget.GetRenderWindow().Render()
        if self.axes is None: return
        if not self.axes.GetXAxisActor2D().GetTickPositions().GetNumberOfPoints(): return
        axes=(self.axes.GetXAxisActor2D(),self.axes.GetYAxisActor2D(),self.axes.GetZAxisActor2D())
        ratio=self.widget.devicePixelRatioF(); size=self.renderer.GetSize(); gap=4*ratio; pad=8*ratio
        # VTK has already selected its fly-mode axes and built its actual tick positions.
        offsets=(8,16,24,32)
        options=[[axis_text_boxes(axis,self.renderer,round(offset*ratio)) for offset in offsets] for axis in axes]
        def cost(boxes):
            overlap=sum(box_overlap(a['box'],b['box'],gap) for i,a in enumerate(boxes) for b in boxes[i+1:])
            clipped=sum(box_clip(item['box'],size,pad) for item in boxes)
            return overlap*1e6+clipped*1e3
        choice=min(product(range(len(offsets)),repeat=3),key=lambda indices:cost([item for i,index in enumerate(indices) for item in options[i][index]])+sum(indices))
        boxes=[item for i,index in enumerate(choice) for item in options[i][index]]
        for axis,index in zip(axes,choice): axis.SetTickOffset(round(offsets[index]*ratio))
        for axis in axes:
            candidates=[axis_title_box(axis,self.renderer,position) for position in (.5,.25,.75,0.,1.,.125,.375,.625,.875)]
            title=min(candidates,key=lambda item:sum(box_overlap(item['box'],other['box'],gap) for other in boxes)*1e6+box_clip(item['box'],size,pad)*1e3+abs(item['position']-.5))
            axis.SetTitlePosition(title['position']); boxes.append(title)
        # VTK can round its last tiny-range tick outside Range and omit it. Add only
        # that exact physical endpoint, without changing Range, geometry or picking.
        from vtkmodules.vtkRenderingCore import vtkTextMapper,vtkActor2D
        properties={axis.GetTitle():axis.GetLabelTextProperty() for axis in axes}
        for item in boxes:
            if not item.get('supplemental'): continue
            mapper=vtkTextMapper(); mapper.SetInput(item['text']); mapper.SetTextProperty(properties[item['axis']])
            actor=vtkActor2D(); actor.SetMapper(mapper); actor.GetPositionCoordinate().SetCoordinateSystemToViewport()
            actor.SetPosition(*item['box'][:2]); actor.PickableOff(); actor.UseBoundsOff()
            self.renderer.AddActor2D(actor); self._endpoint_labels.append(actor)
        self.widget.GetRenderWindow().Render()
        overlaps=[(a['text'],b['text']) for i,a in enumerate(boxes) for b in boxes[i+1:] if box_overlap(a['box'],b['box'],gap)>0]
        clipped=[item['text'] for item in boxes if box_clip(item['box'],size,pad)>0]
        self.annotation_layout=dict(boxes=boxes,overlaps=overlaps,clipped=clipped,size=tuple(size),gap=gap,padding=pad,valid=not overlaps and not clipped,supplemental_count=len(self._endpoint_labels))

    def _annotation_warning(self):
        if self.annotation_layout and not self.annotation_layout['valid']:
            self.message.setText('3D axis labels need more space. Enlarge this plot and use Fit 3D view.'); self.message.show()
        else: self.message.hide()

    def _project(self,point):
        self.renderer.SetWorldPoint(*point,1.); self.renderer.WorldToDisplay()
        return self.renderer.GetDisplayPoint()[:2]

    def _fit_camera(self,*,defer_until_visible=False):
        camera=self.renderer.GetActiveCamera()
        camera.SetFocalPoint(.5,.5,.5); camera.SetPosition(2.5,2.5,2.2); camera.SetViewUp(0,0,1)
        camera.SetWindowCenter(0,0)
        camera.ParallelProjectionOn(); self.renderer.ResetCamera(0,1,0,1,0,1)
        self.renderer.ResetCameraClippingRange(); self._camera_initialized=True
        self._initial_fit_pending=defer_until_visible
        for attempt in range(3):
            self._update_annotation_layout(render=False)
            self._render_annotations()
            if not self.annotation_layout: break
            self.annotation_layout['fit_passes']=attempt+1
            if attempt==0:
                frame=annotation_camera_frame(self.annotation_layout)
                if frame:
                    scale,center=frame; camera.SetParallelScale(scale); camera.SetWindowCenter(*center)
                    continue
            if self.annotation_layout['valid']: break
            factor=annotation_fit_factor(self.annotation_layout)
            if attempt<2 and factor>1: camera.SetParallelScale(camera.GetParallelScale()*factor*1.03)
        self._annotation_warning()

    def reset_camera(self):
        if self.available and self.axes is not None:
            self._fit_camera()

    def closeEvent(self, event):
        self._initial_fit_timer.stop()
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


def text_size(renderer,prop,text):
    from vtkmodules.vtkRenderingCore import vtkTextMapper
    mapper=vtkTextMapper(); mapper.SetTextProperty(prop); mapper.SetInput(text)
    size=[0,0]; mapper.GetSize(renderer,size)
    return np.array(size,dtype=float)


def axis_text_boxes(axis,renderer,offset):
    """Mirror installed vtkAxisActor2D placement using its rendered, adjusted ticks."""
    ticks=axis.GetTickPositions(); adjusted=axis.GetAdjustedNumberOfLabels()
    step=axis.GetNumberOfMinorTicks()+1
    count=min(adjusted,(ticks.GetNumberOfPoints()+step-1)//step)
    if count<1: return []
    # CubeAxes uses exact physical endpoints with nice/grid adjustment disabled.
    if axis.GetAdjustLabels() or axis.GetSnapLabelsToGrid(): raise ValueError('3D axes require exact physical tick ranges')
    start=np.array(axis.GetPositionCoordinate().GetComputedViewportValue(renderer),dtype=float)
    end=np.array(axis.GetPosition2Coordinate().GetComputedViewportValue(renderer),dtype=float)
    theta=np.arctan2(*(end-start)[::-1]); normal=np.array([np.sin(theta),-np.cos(theta)])
    low,high=axis.GetRange(); delta=(high-low)/(adjusted-1) if adjusted>1 else 0.
    values=[low+i*delta for i in range(count)]
    labels=[axis.GetLabelFormat()%value for value in values]
    first=text_size(renderer,axis.GetLabelTextProperty(),labels[0]); boxes=[]
    entries=[(np.array(ticks.GetPoint(i*step)[:2]),label,values[i],False) for i,label in enumerate(labels)]
    for anchor,value in ((start,low),(end,high)):
        label=axis.GetLabelFormat()%value
        if not any(text==label and np.linalg.norm(point-anchor)<1.5 for point,text,_,_ in entries):
            entries.append((anchor,label,value,True))
    for anchor,label,value,supplemental in entries:
        center=anchor+axis.GetTickLength()*normal+(first/2+offset)*normal
        origin=np.trunc(center-first/2)
        width,height=text_size(renderer,axis.GetLabelTextProperty(),label)
        boxes.append(dict(text=label,box=[origin[0],origin[1],origin[0]+width,origin[1]+height],anchor=anchor.tolist(),value=value,axis=axis.GetTitle(),supplemental=supplemental))
    return boxes


def axis_title_box(axis,renderer,position):
    start=np.array(axis.GetPositionCoordinate().GetComputedViewportValue(renderer),dtype=float)
    end=np.array(axis.GetPosition2Coordinate().GetComputedViewportValue(renderer),dtype=float)
    anchor=start+(end-start)*position
    width,height=text_size(renderer,axis.GetTitleTextProperty(),axis.GetTitle())
    label=axis.GetLabelFormat()%axis.GetRange()[0]
    offset=axis.GetTickLength()+axis.GetTickOffset()+1.2*text_size(renderer,axis.GetLabelTextProperty(),label)[1]
    origin=np.trunc(anchor-np.array([width/2,height+offset]))
    return dict(text=axis.GetTitle(),box=[origin[0],origin[1],origin[0]+width,origin[1]+height],anchor=anchor.tolist(),position=position)


def box_overlap(a,b,gap=0):
    return max(0,min(a[2],b[2])+gap-max(a[0],b[0]))*max(0,min(a[3],b[3])+gap-max(a[1],b[1]))


def box_clip(box,size,padding):
    return sum((max(0,padding-box[0]),max(0,padding-box[1]),max(0,box[2]+padding-size[0]),max(0,box[3]+padding-size[1])))


def annotation_fit_factor(layout):
    """Fit projected anchors plus fixed pixel text extents, without shrinking fonts."""
    size=np.array(layout['size']); center=size/2; pad=layout['padding']; factor=1.
    for item in layout['boxes']:
        anchor=np.array(item['anchor']); box=item['box']
        for dimension in (0,1):
            distance=anchor[dimension]-center[dimension]
            edge=box[dimension+2] if distance>0 else box[dimension]
            offset=edge-anchor[dimension]
            available=(size[dimension]-pad-center[dimension]-offset) if distance>0 else (center[dimension]+offset-pad)
            if available>0: factor=max(factor,abs(distance)/available)
    return factor


def annotation_camera_frame(layout):
    """Reserve measured text margins around the normalized cube's rotation sphere."""
    size=np.array(layout['size'],dtype=float); pad=layout['padding']; margins=np.full((2,2),pad)
    for item in layout['boxes']:
        anchor=np.array(item['anchor']); box=item['box']
        for dimension in (0,1):
            margins[dimension,0]=max(margins[dimension,0],anchor[dimension]-box[dimension]+pad)
            margins[dimension,1]=max(margins[dimension,1],box[dimension+2]-anchor[dimension]+pad)
    available=size-margins.sum(axis=1)
    if np.any(available<=0): return None
    radius=np.sqrt(3)/2
    scale=radius*max(size[1]/available[1],size[1]/available[0])
    center=(margins[:,1]-margins[:,0])/size
    return scale,center.tolist()
