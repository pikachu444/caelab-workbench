"""Small shared Qt plot surface and optional native VTK scene."""
from __future__ import annotations
import os
from itertools import product,permutations
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
        self._custom_labels=[]; self._custom_titles=[]; self._label_leaders=[]
        self._rendering_layout=False; self._style_render_suppressed=False
        self._scene_epoch=0
        self._render_scene=None
        self._latest_layout_timer=QTimer(self); self._latest_layout_timer.setSingleShot(True); self._latest_layout_timer.timeout.connect(self._update_annotation_layout)
        self.annotation_message=QLabel(self); self.annotation_message.setTextFormat(Qt.TextFormat.PlainText)
        self.annotation_message.setAccessibleName('3D axis layout status'); self.annotation_message.hide()
        self.annotation_message.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.annotation_message.setStyleSheet('color: #fbbf24; background: #13171e; padding: 2px 4px;')
        self.annotation_message.setFixedHeight(self.annotation_message.fontMetrics().lineSpacing()+4)
        self.annotation_message.installEventFilter(self)
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
                self.interaction_style.AddObserver('MouseWheelForwardEvent',lambda style,event:self._style_camera_action(style,style.OnMouseWheelForward))
                self.interaction_style.AddObserver('MouseWheelBackwardEvent',lambda style,event:self._style_camera_action(style,style.OnMouseWheelBackward))
                self.interaction_style.AddObserver('CharEvent',lambda style,event:self._style_camera_action(style,style.OnChar))
                interactor.AddObserver('EndInteractionEvent',self._end_interaction)
                interactor.AddObserver('StartInteractionEvent',self._camera_interacted)
                interactor.AddObserver('ConfigureEvent',self._configure_scene)
                self.widget.Initialize()
                reset=QPushButton('Fit 3D view')
                reset.setToolTip('Restore the oblique z-up view and fit the data and physical axes.')
                reset.clicked.connect(self.reset_camera)
                controls=QHBoxLayout(); controls.addWidget(reset)
                self.instructions=QLabel('Drag: rotate   Middle / Shift-drag: pan   Wheel: zoom')
                self.instructions.setWordWrap(True); controls.addWidget(self.instructions,1)
                layout.addLayout(controls); layout.addWidget(self.legend); layout.addWidget(self.axis_key)
                layout.addWidget(self.widget)
                layout.addWidget(self.annotation_message); self.annotation_message.show()
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
            self.clear(); self.annotation_message.clear(); self.axis_key.clear(); self.axis_key.hide(); self.message.setText(text); self.message.show()
            self.widget.GetRenderWindow().Render()

    def _begin_pick(self,interactor,event):
        self._press=tuple(interactor.GetEventPosition()); self._dragged=False

    def _camera_interacted(self,*args):
        self._initial_fit_pending=False; self._camera_initialized=True; self._initial_fit_timer.stop()

    def _style_press(self,style,event):
        if not self.available: return
        self._begin_pick(style.GetInteractor(),event); style.OnLeftButtonDown()

    def _style_move(self,style,event):
        if not self.available: return
        self._track_pick(style.GetInteractor(),event)
        if style.GetState(): self._style_camera_action(style,style.OnMouseMove)
        else: style.OnMouseMove()

    def _style_camera_action(self,style,action):
        if not self.available: return
        interactor=style.GetInteractor(); enabled=interactor.GetEnableRender()
        suppressed=self._style_render_suppressed; scene=(self.axes,self._scene_epoch)
        self._camera_interacted(); self._style_render_suppressed=True; interactor.EnableRenderOff()
        try: action()
        except Exception:
            if self._same_scene(scene): self._invalidate_annotations()
            raise
        finally: interactor.SetEnableRender(enabled); self._style_render_suppressed=suppressed
        if enabled and self._same_scene(scene): self._update_annotation_layout()

    def _end_interaction(self,*args):
        if not self._style_render_suppressed: self._update_annotation_layout()

    def _configure_scene(self,*args):
        if self.available and not self._rendering_layout: self._update_annotation_layout()

    def _style_release(self,style,event):
        if not self.available: return
        style.OnLeftButtonUp(); self._pick(style.GetInteractor(),event)

    def _track_pick(self,interactor,event):
        if self._press is not None:
            x,y=interactor.GetEventPosition(); threshold=4*self.widget.devicePixelRatioF()
            if (x-self._press[0])**2+(y-self._press[1])**2>threshold**2: self._dragged=True

    def _pick(self, interactor, event):
        if not self.available: self._press=None; return
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
            self._scene_epoch+=1
            self._latest_layout_timer.stop()
            self._initial_fit_timer.stop(); self._fit_timer_axes=None
            self.renderer.RemoveAllViewProps()
            self._endpoint_labels.clear()
            self._custom_labels.clear(); self._custom_titles.clear(); self._label_leaders.clear()
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
        if not self._camera_initialized:
            self._fit_camera(defer_until_visible=True)
        else: self._update_annotation_layout()
        if self._initial_fit_pending and self.isVisible():
            self._fit_timer_axes=self.axes; self._initial_fit_timer.start(0)
        self.instructions.setText('Drag: rotate   Middle / Shift-drag: pan   Wheel: zoom'+('   Click point: select' if self.actors else ''))

    def eventFilter(self,watched,event):
        if watched is self.annotation_message and event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange):
            self.annotation_message.setFixedHeight(self.annotation_message.fontMetrics().lineSpacing()+4)
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
        dpi=max(1,self.renderer.GetRenderWindow().GetDPI())
        font_points=max(1,round(12*self.widget.devicePixelRatioF()*72/dpi))
        for prop in (self.axes.GetAxisTitleTextProperty(),self.axes.GetAxisLabelTextProperty()): prop.SetFontSize(font_points)
        for actor,offset in zip((self.axes.GetXAxisActor2D(),self.axes.GetYAxisActor2D(),self.axes.GetZAxisActor2D()),(12,12,32)):
            actor.SetTickOffset(round(offset*self.widget.devicePixelRatioF()))
        origin=self._project((0,0,0)); lengths=[np.linalg.norm(np.array(self._project(point))-origin)/self.widget.devicePixelRatioF() for point in ((1,0,0),(0,1,0),(0,0,1))]
        count,precision=axis_label_layout(self.axes.GetRanges(),self.widget.width(),self.widget.height(),min(lengths))
        self.axes.SetNumberOfLabels(count); self.axes.SetLabelFormat(f'%.{precision}g')
        if render:
            self._render_annotations(); self._annotation_warning()

    def _render_annotations(self):
        if not self.available or self.axes is None: return
        if self._rendering_layout:
            if self._render_scene is not None and not self._same_scene(self._render_scene): self._latest_layout_timer.start(0)
            return
        rendering=self._rendering_layout; previous_scene=self._render_scene; scene=(self.axes,self._scene_epoch)
        self._render_scene=scene
        self._rendering_layout=True
        try: self._render_annotations_impl(scene)
        except Exception:
            if self._same_scene(scene): self._invalidate_annotations()
            raise
        finally: self._rendering_layout=rendering; self._render_scene=previous_scene

    def _same_scene(self,scene):
        return self.available and self.axes is scene[0] and self._scene_epoch==scene[1]

    def _invalidate_annotations(self):
        self._scene_epoch+=1
        for group in (self._endpoint_labels,self._custom_labels,self._custom_titles,self._label_leaders):
            for actor in group: self.renderer.RemoveActor2D(actor)
            group.clear()
        self.annotation_layout=None
        if self.axes is not None:
            for axis in (self.axes.GetXAxisActor2D(),self.axes.GetYAxisActor2D(),self.axes.GetZAxisActor2D()): axis.LabelVisibilityOn(); axis.TitleVisibilityOn()
        self.annotation_message.setText('Axis layout unavailable. Use Fit 3D view.')

    def _render_annotations_impl(self,scene):
        for group in (self._endpoint_labels,self._custom_labels,self._custom_titles,self._label_leaders):
            for actor in group: self.renderer.RemoveActor2D(actor)
            group.clear()
        if self.axes is not None:
            for axis in (self.axes.GetXAxisActor2D(),self.axes.GetYAxisActor2D(),self.axes.GetZAxisActor2D()): axis.LabelVisibilityOn(); axis.TitleVisibilityOn()
        window=self.renderer.GetRenderWindow(); swap=window.GetSwapBuffers(); window.SwapBuffersOff()
        try: self.widget.GetRenderWindow().Render()
        finally: window.SetSwapBuffers(swap)
        if not self._same_scene(scene): return
        if not self.axes.GetXAxisActor2D().GetTickPositions().GetNumberOfPoints(): return
        axes=(self.axes.GetXAxisActor2D(),self.axes.GetYAxisActor2D(),self.axes.GetZAxisActor2D())
        ratio=self.widget.devicePixelRatioF(); size=self.renderer.GetSize(); gap=4*ratio; pad=8*ratio
        # VTK has already selected its fly-mode axes and built its actual tick positions.
        offsets=(8,16,24,32,0)
        def candidates(verticals):
            return [[dict(boxes=axis_text_boxes(axis,self.renderer,round(offset*ratio),vertical),offset=round(offset*ratio),vertical=vertical,preference=(100+vertical*10 if vertical else 0)+i)
                     for vertical in verticals for i,offset in enumerate(offsets)] for axis in axes]
        options=candidates((0,)); choice,error=choose_axis_text_layout(options,size,gap,pad)
        if any(error):
            options=candidates((0,1,2)); choice,error=choose_axis_text_layout(options,size,gap,pad)
        selected=[options[i][index] for i,index in enumerate(choice)]
        boxes=[item for option in selected for item in option['boxes']]
        for axis,option in zip(axes,selected):
            axis.SetTickOffset(option['offset']); axis.GetLabelTextProperty().SetVerticalJustification(option['vertical'])
            axis.GetLabelTextProperty().Modified()
        if not self._same_scene(scene): return
        outside=[axis.GetTitle() for axis in axes for coordinate in (axis.GetPositionCoordinate(),axis.GetPosition2Coordinate())
                 if any(value<0 or value>extent for value,extent in zip(coordinate.GetComputedViewportValue(self.renderer),size))]
        boxes,custom_axes,leaders=repair_axis_numeric_boxes(boxes,axes,self.renderer,size,pad,gap) if not outside else (boxes,set(),[])
        for axis in axes:
            if axis.GetTitle() in custom_axes: axis.LabelVisibilityOff()
        for axis in axes:
            candidates=[]; original_offset=axis.GetTickOffset()
            for offset in ((original_offset,0,round(8*ratio),round(16*ratio),round(24*ratio),round(32*ratio)) if axis.GetTitle() in custom_axes else (original_offset,)):
                axis.SetTickOffset(offset)
                for vertical in (0,1,2):
                    for position in (.5,.25,.75,0.,1.,.125,.375,.625,.875):
                        candidate=axis_title_box(axis,self.renderer,position,vertical); candidate['offset']=offset; candidates.append(candidate)
            title=min(candidates,key=lambda item:sum(segment_hits_box(leader['start'],leader['end'],item['box']) for leader in leaders)*1e9+sum(box_overlap(item['box'],other['box'],gap) for other in boxes)*1e6+box_clip(item['box'],size,pad)*1e3+abs(item['position']-.5))
            axis.SetTickOffset(title['offset']); axis.SetTitlePosition(title['position']); axis.GetTitleTextProperty().SetVerticalJustification(title['vertical']); boxes.append(title)
            axis.GetTitleTextProperty().Modified()
        boxes,custom_titles,leaders=repair_axis_titles(boxes,leaders,size,pad,gap) if not outside else (boxes,set(),leaders)
        for axis in axes:
            if axis.GetTitle() in custom_titles: axis.TitleVisibilityOff()
        if not self._same_scene(scene): return
        # VTK can round its last tiny-range tick outside Range and omit it. Add only
        # that exact physical endpoint, without changing Range, geometry or picking.
        from vtkmodules.vtkRenderingCore import vtkTextMapper,vtkActor2D
        properties={axis.GetTitle():axis.GetLabelTextProperty() for axis in axes}
        title_properties={axis.GetTitle():axis.GetTitleTextProperty() for axis in axes}
        for item in boxes:
            title_custom='axis' not in item and item['text'] in custom_titles
            if not title_custom and not item.get('supplemental') and item.get('axis') not in custom_axes: continue
            mapper=vtkTextMapper(); mapper.SetInput(item['text']); mapper.SetTextProperty(title_properties[item['text']] if title_custom else properties[item['axis']])
            actor=vtkActor2D(); actor.SetMapper(mapper); actor.GetPositionCoordinate().SetCoordinateSystemToViewport()
            actor.SetPosition(*item['raw_position']); actor.PickableOff(); actor.UseBoundsOff()
            self.renderer.AddActor2D(actor)
            if not self._same_scene(scene): return
            (self._custom_titles if title_custom else self._endpoint_labels if item.get('supplemental') else self._custom_labels).append(actor)
        for leader in leaders:
            actor=self._leader_actor(leader['start'],leader['end'])
            if not self._same_scene(scene): return
            self._label_leaders.append(actor)
        self.widget.GetRenderWindow().Render()
        if not self._same_scene(scene): return
        overlaps=[(a['text'],b['text']) for i,a in enumerate(boxes) for b in boxes[i+1:] if box_overlap(a['box'],b['box'],gap)>0]
        clipped=[item['text'] for item in boxes if box_clip(item['box'],size,pad)>0]
        conflicts=leader_conflicts(leaders,boxes)
        dpi=self.renderer.GetRenderWindow().GetDPI(); points=self.axes.GetAxisLabelTextProperty().GetFontSize()
        self.annotation_layout=dict(boxes=boxes,overlaps=overlaps,clipped=clipped,outside_anchors=outside,leader_conflicts=conflicts,size=tuple(size),gap=gap,padding=pad,valid=not overlaps and not clipped and not outside and not conflicts,supplemental_count=len(self._endpoint_labels),custom_axes=sorted(custom_axes),custom_titles=sorted(custom_titles),leader_lines=leaders,render_dpi=dpi,font_points=points,target_glyph_pixels=12*ratio,effective_glyph_pixels=points*dpi/72)

    def _leader_actor(self,start,end):
        from vtkmodules.vtkCommonCore import vtkPoints
        from vtkmodules.vtkCommonDataModel import vtkPolyData,vtkCellArray
        from vtkmodules.vtkRenderingCore import vtkPolyDataMapper2D,vtkActor2D
        points=vtkPoints(); points.SetDataTypeToDouble()
        points.InsertNextPoint(*start,0); points.InsertNextPoint(*end,0)
        cells=vtkCellArray(); cells.InsertNextCell(2); cells.InsertCellPoint(0); cells.InsertCellPoint(1)
        data=vtkPolyData(); data.SetPoints(points); data.SetLines(cells)
        mapper=vtkPolyDataMapper2D(); mapper.SetInputData(data)
        actor=vtkActor2D(); actor.SetMapper(mapper); actor.PickableOff(); actor.UseBoundsOff()
        actor.GetProperty().SetColor(.6,.7,.8); actor.GetProperty().SetLineWidth(self.widget.devicePixelRatioF())
        self.renderer.AddActor2D(actor); return actor

    def _annotation_warning(self):
        if self.annotation_layout and not self.annotation_layout['valid']:
            self.annotation_message.setText('Axis labels need space. Use Fit 3D view or enlarge the plot.')
        else: self.annotation_message.clear()

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
            self._initial_fit_timer.stop()
            self._fit_camera()

    def closeEvent(self, event):
        self._initial_fit_timer.stop()
        self._latest_layout_timer.stop()
        if self.available:
            self.clear(); self.available=False; self.widget.Finalize()
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


def text_bounds(renderer,prop,text,origin):
    from vtkmodules.vtkRenderingCore import vtkTextRenderer
    bounds=[0,0,0,0]
    vtkTextRenderer.GetInstance().GetBoundingBox(prop,text,bounds,renderer.GetRenderWindow().GetDPI())
    return [origin[0]+bounds[0],origin[1]+bounds[2],origin[0]+bounds[1]+1,origin[1]+bounds[3]+1]


def axis_text_boxes(axis,renderer,offset,vertical=None):
    """Mirror installed vtkAxisActor2D placement using its rendered, adjusted ticks."""
    ticks=axis.GetTickPositions(); adjusted=axis.GetAdjustedNumberOfLabels()
    step=axis.GetNumberOfMinorTicks()+1
    count=min(adjusted,(ticks.GetNumberOfPoints()+step-1)//step)
    if count<1: return []
    # CubeAxes uses exact physical endpoints with nice/grid adjustment disabled.
    if axis.GetAdjustLabels() or axis.GetSnapLabelsToGrid(): raise ValueError('3D axes require exact physical tick ranges')
    from vtkmodules.vtkRenderingCore import vtkTextProperty
    prop=vtkTextProperty(); prop.ShallowCopy(axis.GetLabelTextProperty())
    if vertical is not None: prop.SetVerticalJustification(vertical)
    start=np.array(axis.GetPositionCoordinate().GetComputedViewportValue(renderer),dtype=float)
    end=np.array(axis.GetPosition2Coordinate().GetComputedViewportValue(renderer),dtype=float)
    theta=np.arctan2(*(end-start)[::-1]); normal=np.array([np.sin(theta),-np.cos(theta)])
    low,high=axis.GetRange(); delta=(high-low)/(adjusted-1) if adjusted>1 else 0.
    values=[low+i*delta for i in range(count)]
    labels=[axis.GetLabelFormat()%value for value in values]
    first=text_size(renderer,prop,labels[0]); boxes=[]
    entries=[(np.array(ticks.GetPoint(i*step)[:2]),label,values[i],False) for i,label in enumerate(labels)]
    for anchor,value in ((start,low),(end,high)):
        label=axis.GetLabelFormat()%value
        if not any(text==label and np.linalg.norm(point-anchor)<1.5 for point,text,_,_ in entries):
            entries.append((anchor,label,value,True))
    for anchor,label,value,supplemental in entries:
        center=anchor+axis.GetTickLength()*normal+(first/2+offset)*normal
        origin=np.trunc(center-first/2)
        boxes.append(dict(text=label,box=text_bounds(renderer,prop,label,origin),raw_position=origin.tolist(),anchor=anchor.tolist(),value=value,axis=axis.GetTitle(),supplemental=supplemental,vertical=prop.GetVerticalJustification()))
    return boxes


def axis_title_box(axis,renderer,position,vertical=None):
    start=np.array(axis.GetPositionCoordinate().GetComputedViewportValue(renderer),dtype=float)
    end=np.array(axis.GetPosition2Coordinate().GetComputedViewportValue(renderer),dtype=float)
    anchor=start+(end-start)*position
    from vtkmodules.vtkRenderingCore import vtkTextProperty
    prop=vtkTextProperty(); prop.ShallowCopy(axis.GetTitleTextProperty())
    if vertical is not None: prop.SetVerticalJustification(vertical)
    width,height=text_size(renderer,prop,axis.GetTitle())
    label=axis.GetLabelFormat()%axis.GetRange()[0]
    offset=axis.GetTickLength()+axis.GetTickOffset()+(1.2*text_size(renderer,axis.GetLabelTextProperty(),label)[1] if axis.GetLabelVisibility() else 0)
    # Match vtkAxisActor2D::ShiftPosition's integer width division and offset
    # conversion before each integer-coordinate assignment.
    origin=np.array([int(anchor[0]-int(width)//2),int(anchor[1])-int(height)-int(offset)],dtype=float)
    return dict(text=axis.GetTitle(),box=text_bounds(renderer,prop,axis.GetTitle(),origin),raw_position=origin.tolist(),anchor=anchor.tolist(),position=position,vertical=prop.GetVerticalJustification())


def choose_axis_text_layout(options,size,gap,padding):
    """Search three small axis option sets, caching pair costs instead of repainting."""
    def overlap(a,b): return sum(box_overlap(x['box'],y['box'],gap) for x in a for y in b)
    local=[]
    for group in options:
        local.append([(sum(box_overlap(a['box'],b['box'],gap) for i,a in enumerate(option['boxes']) for b in option['boxes'][i+1:]),
                       sum(box_clip(item['box'],size,padding) for item in option['boxes']),option['preference']) for option in group])
    cross={(i,j):[[overlap(a['boxes'],b['boxes']) for b in options[j]] for a in options[i]] for i,j in ((0,1),(0,2),(1,2))}
    def score(indices):
        a,b,c=indices
        return (local[0][a][0]+local[1][b][0]+local[2][c][0]+cross[0,1][a][b]+cross[0,2][a][c]+cross[1,2][b][c],
                local[0][a][1]+local[1][b][1]+local[2][c][1],local[0][a][2]+local[1][b][2]+local[2][c][2])
    choice=min(product(*(range(len(group)) for group in options)),key=score)
    return choice,score(choice)[:2]


def repair_axis_numeric_boxes(boxes,axes,renderer,size,padding,gap):
    """Keep residual annotations inside, linked to unchanged projected tick anchors."""
    result=[dict(item) for item in boxes]; custom=set(); leaders=[]
    for i,item in enumerate(result):
        if not box_clip(item['box'],size,padding) and not any(box_overlap(item['box'],other['box'],gap)>0 for j,other in enumerate(result) if j!=i): continue
        axis=next(axis for axis in axes if axis.GetTitle()==item['axis'])
        start=np.array(axis.GetPositionCoordinate().GetComputedViewportValue(renderer)); end=np.array(axis.GetPosition2Coordinate().GetComputedViewportValue(renderer))
        width=item['box'][2]-item['box'][0]; height=item['box'][3]-item['box'][1]
        if width>size[0]-2*padding or height>size[1]-2*padding: continue
        original=np.array(item['box']); anchor=np.array(item['anchor']); nearest=np.array([np.clip(original[0],padding,size[0]-padding-width),np.clip(original[1],padding,size[1]-padding-height)])
        candidates=[]
        for dx,dy in ((0,0),(0,height+gap),(0,-height-gap),(width+gap,0),(-width-gap,0)):
            origin=nearest+[dx,dy]; box=[*origin,origin[0]+width,origin[1]+height]
            if box_clip(box,size,padding) or any(box_overlap(box,other['box'],gap)>0 for j,other in enumerate(result) if j!=i): continue
            target=np.clip(anchor,box[:2],box[2:])
            if np.linalg.norm(target-anchor)<gap/2: continue
            if any(segments_cross(anchor,target,leader['start'],leader['end']) for leader in leaders): continue
            if any(segment_hits_box(anchor,target,other['box']) for j,other in enumerate(result) if j!=i): continue
            candidates.append((np.linalg.norm(origin-original[:2]),box,target))
        if not candidates: continue
        _,box,target=min(candidates,key=lambda entry:entry[0]); shift=np.array(box[:2])-original[:2]
        item['box']=box; item['raw_position']=(np.array(item['raw_position'])+shift).tolist(); item['clamped']=True
        custom.add(item['axis']); leaders.append(dict(axis=item['axis'],value=item['value'],label_index=i,start=item['anchor'],end=target.tolist()))
    return result,custom,leaders


def repair_axis_titles(boxes,leaders,size,padding,gap):
    """Move only residual XYZ titles; their native axis identities stay unchanged."""
    titles=[i for i,item in enumerate(boxes) if 'axis' not in item]
    candidates=[_repair_title_order(boxes,leaders,size,padding,gap,order) for order in permutations(titles)]
    def score(candidate):
        result,_,lines=candidate
        conflicts=sum(bool(box_clip(item['box'],size,padding)) for item in result)+sum(box_overlap(a['box'],b['box'],gap)>0 for i,a in enumerate(result) for b in result[i+1:])+len(leader_conflicts(lines,result))
        distance=sum(np.linalg.norm(np.array(item['box'][:2])-original['box'][:2]) for item,original in zip(result,boxes))
        return conflicts,distance
    return min(candidates,key=score)


def _repair_title_order(boxes,leaders,size,padding,gap,order):
    result=[dict(item) for item in boxes]; leaders=list(leaders); custom=set()
    def needs_move(index):
        item=result[index]
        return box_clip(item['box'],size,padding) or any(box_overlap(item['box'],other['box'],gap)>0 for j,other in enumerate(result) if j!=index) or any(segment_hits_box(leader['start'],leader['end'],item['box']) for leader in leaders)
    def contains(box,point): return box[0]<=point[0]<=box[2] and box[1]<=point[1]<=box[3]
    for i in order:
        item=result[i]
        # A valid title may block the unchanged leader origin of a clipped title.
        # Free that origin using the same candidates before routing the other title.
        blocked=[result[j]['anchor'] for j in order if j!=i and needs_move(j) and contains(item['box'],result[j]['anchor'])]
        if not needs_move(i) and not blocked: continue
        original=np.array(item['box']); width,height=original[2:]-original[:2]; anchor=np.array(item['anchor'])
        if width>size[0]-2*padding or height>size[1]-2*padding: continue
        nearest=np.array([np.clip(original[0],padding,size[0]-padding-width),np.clip(original[1],padding,size[1]-padding-height)])
        candidates=[]
        for dx,dy in product((0,1,-1,2,-2,3,-3,4,-4),repeat=2):
            origin=nearest+np.array([dx*(width+gap),dy*(height+gap)]); box=[*origin,origin[0]+width,origin[1]+height]
            if any(contains(box,anchor) for anchor in blocked): continue
            if box_clip(box,size,padding) or any(box_overlap(box,other['box'],gap)>0 for j,other in enumerate(result) if j!=i): continue
            if any(segment_hits_box(leader['start'],leader['end'],box) for leader in leaders): continue
            target=np.clip(anchor,box[:2],box[2:])
            if np.linalg.norm(target-anchor)<gap/2: continue
            if any(segments_cross(anchor,target,leader['start'],leader['end']) for leader in leaders): continue
            if any(segment_hits_box(anchor,target,other['box']) for j,other in enumerate(result) if j!=i): continue
            candidates.append((np.linalg.norm(origin-original[:2]),box,target))
        if not candidates: continue
        _,box,target=min(candidates,key=lambda entry:entry[0]); shift=np.array(box[:2])-original[:2]
        item['box']=box; item['raw_position']=(np.array(item['raw_position'])+shift).tolist(); item['clamped']=True
        custom.add(item['text']); leaders.append(dict(axis=item['text'],kind='title',label_index=i,start=item['anchor'],end=target.tolist()))
    return result,custom,leaders


def segments_cross(a,b,c,d):
    a,b,c,d=map(lambda point:np.array(point,dtype=float),(a,b,c,d))
    def side(p,q,r): return (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
    values=(side(a,b,c),side(a,b,d),side(c,d,a),side(c,d,b)); epsilon=1e-6
    if all(abs(value)<epsilon for value in values):
        dimension=int(np.argmax(np.abs(b-a)))
        return min(max(a[dimension],b[dimension]),max(c[dimension],d[dimension]))-max(min(a[dimension],b[dimension]),min(c[dimension],d[dimension]))>epsilon
    if any(np.linalg.norm(x-y)<epsilon for x in (a,b) for y in (c,d)): return False
    def on_segment(p,q,r): return abs(side(p,q,r))<epsilon and np.all(r>=np.minimum(p,q)-epsilon) and np.all(r<=np.maximum(p,q)+epsilon)
    return (values[0]*values[1]<0 and values[2]*values[3]<0) or any(on_segment(p,q,r) for p,q,r in ((a,b,c),(a,b,d),(c,d,a),(c,d,b)))


def segment_hits_box(start,end,box):
    if any(box[0]<=point[0]<=box[2] and box[1]<=point[1]<=box[3] for point in (start,end)): return True
    corners=((box[0],box[1]),(box[2],box[1]),(box[2],box[3]),(box[0],box[3]))
    return any(segments_cross(start,end,corners[i],corners[(i+1)%4]) for i in range(4))


def leader_conflicts(leaders,boxes):
    conflicts=[]
    for i,leader in enumerate(leaders):
        conflicts.extend(('glyph',i,j) for j,item in enumerate(boxes) if j!=leader['label_index'] and segment_hits_box(leader['start'],leader['end'],item['box']))
        conflicts.extend(('leader',i,j) for j,other in enumerate(leaders[i+1:],i+1) if segments_cross(leader['start'],leader['end'],other['start'],other['end']))
    return conflicts


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
