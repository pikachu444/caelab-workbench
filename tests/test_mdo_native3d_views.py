"""Installed VTK configuration/interaction tests; these do not certify native pixels."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from types import SimpleNamespace
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication,QWidget,QLabel,QTabWidget,QSizePolicy
from vtkmodules.vtkRenderingCore import vtkRenderWindow,vtkRenderWindowInteractor
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor as InstalledQVTK
from apps.workbench_qt.mdo_studio.views.plot_base import Native3D,axis_label_layout
from apps.workbench_qt.mdo_studio.views.pareto_view import ParetoFrontWidget
from apps.workbench_qt.mdo_studio.views.response_surface_view import ResponseSurfaceWidget
from apps.workbench_qt.mdo_studio.study_tree import StudyTreeWidget
from caelab.mdo.study_manager import StudyManager
from caelab.mdo.objectives import ObjectiveSpec


@pytest.fixture(scope='session')
def qapp(): return QApplication.instance() or QApplication([])


@pytest.fixture
def vtk_widget(qapp,monkeypatch):
    """Use real VTK cameras/styles/actors, with no window initialization or GPU draw."""
    import vtkmodules.qt.QVTKRenderWindowInteractor as module
    class TestInteractor(QWidget):
        def __init__(self,parent):
            super().__init__(parent); self.ratio=1.; self.renders=0
            self.window=vtkRenderWindow(); self.window.SetSize(800,320)
            self.interactor=vtkRenderWindowInteractor(); self.interactor.SetRenderWindow(self.window)
            self.proxy=SimpleNamespace(AddRenderer=self.window.AddRenderer,GetInteractor=lambda:self.interactor,Render=self.render)
            self.resize(800,320)
        def Initialize(self): pass
        def Finalize(self): pass
        def GetRenderWindow(self): return self.proxy
        def devicePixelRatioF(self): return self.ratio
        def render(self): self.renders+=1
    monkeypatch.setattr(module,'QVTKRenderWindowInteractor',TestInteractor)
    # This changes only Native3D's availability branch; the QApplication remains offscreen.
    monkeypatch.setenv('QT_QPA_PLATFORM','windows')
    return TestInteractor


def camera_state(camera):
    return camera.GetPosition(),camera.GetFocalPoint(),camera.GetViewUp(),camera.GetParallelProjection(),camera.GetParallelScale(),camera.GetWindowCenter(),camera.GetViewAngle()


@pytest.mark.parametrize('value,precision,expected',[(1.23456789e-12,6,'1.23457e-12'),(-297.1234,4,'-297.1'),(1234567890.,6,'1.23457e+09')])
def test_axis_numeric_format_printf_and_std_golden_strings(value,precision,expected):
    from apps.workbench_qt.mdo_studio.views.plot_base import axis_value_text
    assert axis_value_text(f'%.{precision}g',value)==expected
    assert axis_value_text(f'{{:.{precision}g}}',value)==expected


def test_installed_vtk_axis_label_and_title_paths_accept_both_formats(vtk_widget,monkeypatch):
    import re
    from vtkmodules.vtkRenderingAnnotation import vtkAxisActor2D
    from apps.workbench_qt.mdo_studio.views.plot_base import axis_value_text,axis_text_boxes,axis_title_box
    axis=vtkAxisActor2D(); axis.SetLabelFormat('%.6g')
    assert axis_value_text(axis.GetLabelFormat(),.05841234)=='0.0584123'
    view=Native3D(); widget=view.widget; widget.resize(620,236)
    widget.window.SetOffScreenRendering(1); widget.window.SetSize(620,236)
    widget.proxy.Render=widget.window.Render; widget.interactor.Initialize()
    view.points([[1e-4,.05,-200.],[2e-4,.09,-100.]],['a','b']); view.finish(['mass','disp','stress'])
    axis=view.axes.GetXAxisActor2D(); precision=int(re.search(r'\.(\d+)g',axis.GetLabelFormat()).group(1))
    snapshots=[]
    for spec in (f'%.{precision}g',f'{{:.{precision}g}}'):
        monkeypatch.setattr(axis,'GetLabelFormat',lambda spec=spec:spec)
        boxes=axis_text_boxes(axis,view.renderer,axis.GetTickOffset())
        assert boxes and all(item['text']==format(item['value'],f'.{precision}g') for item in boxes)
        snapshots.append((boxes,axis_title_box(axis,view.renderer,.5)))
    assert snapshots[0]==snapshots[1]
    view.close()


def test_native_style_camera_ranges_and_redraw_preserve_user_view(vtk_widget):
    view=Native3D(); assert view.available
    assert view.widget.interactor.GetInteractorStyle().IsA('vtkInteractorStyleTrackballCamera')
    values=np.array([[5e-5,.02,120.],[1.1e-4,.08,350.]])
    view.points(values,['actual','prediction']); actor=next(iter(view.actors)); view.finish(['mass [kg]','disp [mm]','stress [MPa]'])
    assert view.physical_ranges==pytest.approx((5e-5,1.1e-4,.02,.08,120.,350.))
    assert view.axes.GetBounds()==pytest.approx((0,1,0,1,0,1)) and view.axes.GetUseRanges()
    assert view.axes.GetRanges()==pytest.approx(view.physical_ranges)
    assert not view.axes.GetPickable() and view.picker.GetPickList().GetNumberOfItems()==1
    assert view.actors[actor]==('actual','prediction')
    camera=view.renderer.GetActiveCamera(); assert camera.GetViewUp()==(0.,0.,1.) and camera.GetParallelProjection()
    canonical=camera_state(camera)
    camera.SetPosition(3.,2.,1.); camera.SetFocalPoint(.2,.4,.6); camera.SetViewUp(0,1,0); camera.ParallelProjectionOff()
    changed=camera_state(camera)
    view.clear(); view.points(values,['actual','prediction']); view.finish(['mass [kg]','disp [mm]','stress [MPa]'])
    assert camera_state(camera)==changed
    view.reset_camera(); assert camera_state(camera)==canonical
    view.close()


def test_constant_axis_is_explicit_and_keeps_exact_original_range(vtk_widget):
    view=Native3D(); view.points([[1e-4,.05,200.]],['constant']); view.finish(['mass [kg]','disp [mm]','stress [MPa]'])
    assert view.physical_ranges==pytest.approx((1e-4,1e-4,.05,.05,200.,200.))
    assert view.axes.GetXLabel()=='X' and view.axes.GetZLabel()=='Z'
    assert 'X: mass [kg] (constant' in view.axis_key.text()
    assert 'Z: stress [MPa] (constant' in view.axis_key.text()
    ranges=view.axes.GetRanges()
    for i,value in enumerate((1e-4,.05,200.)): assert (ranges[2*i]+ranges[2*i+1])/2==pytest.approx(value)
    actor=next(iter(view.actors)); assert actor.GetBounds()==pytest.approx((.5,.5,.5,.5,.5,.5))
    view.close()


def test_surface_material_normals_preserve_finite_cell_mask_and_values(vtk_widget):
    view=Native3D(); x,y=np.meshgrid(np.arange(4.),np.arange(4.)); z=x+y; z[0,0]=np.nan
    view.surface(x,y,z); actor=view.renderer.GetActors().GetLastActor(); actor.GetMapper().Update(); data=actor.GetMapper().GetInput()
    assert data.GetNumberOfPoints()==16 and data.GetNumberOfCells()==8
    assert data.GetPointData().GetNormals() is not None
    for i in range(data.GetNumberOfCells()):
        assert all(np.isfinite(data.GetPoint(data.GetCell(i).GetPointId(j))).all() for j in range(data.GetCell(i).GetNumberOfPoints()))
    np.testing.assert_allclose(np.array([data.GetPoint(i) for i in range(16)]),np.column_stack((x.ravel(),y.ravel(),z.ravel())),equal_nan=True)
    prop=actor.GetProperty(); assert prop.GetAmbient()==.65 and prop.GetDiffuse()==.35
    assert not prop.GetBackfaceCulling() and not prop.GetFrontfaceCulling() and view.renderer.GetTwoSidedLighting()
    view.close()


def assert_annotation_pixels_match_boxes(view,tmp_path):
    """Separate real annotation pixels from geometry without masking white glyphs."""
    import json
    from PIL import Image,ImageDraw
    from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter
    from vtkmodules.vtkIOImage import vtkPNGWriter
    from vtkmodules.util.numpy_support import vtk_to_numpy
    tmp_path.mkdir(parents=True,exist_ok=True)
    window=view.renderer.GetRenderWindow(); before=camera_state(view.renderer.GetActiveCamera())
    def capture_frame(name):
        window.Render(); capture=vtkWindowToImageFilter(); capture.SetInput(window); capture.Update()
        writer=vtkPNGWriter(); writer.SetFileName(str(tmp_path/name)); writer.SetInputConnection(capture.GetOutputPort()); writer.Write()
        width,height=capture.GetOutput().GetDimensions()[:2]
        return capture,vtk_to_numpy(capture.GetOutput().GetPointData().GetScalars()).copy().reshape(height,width,-1)
    _,full=capture_frame('annotation-full-frame.png')
    collection=view.renderer.GetActors(); collection.InitTraversal()
    actors=[collection.GetNextActor() for _ in range(collection.GetNumberOfItems())]; visibility=[actor.GetVisibility() for actor in actors]
    try:
        for actor in actors: actor.SetVisibility(False)
        capture,pixels=capture_frame('annotation-only.png')
    finally:
        for actor,visible in zip(actors,visibility): actor.SetVisibility(visible)
        if view.available: window.Render()
    assert [actor.GetVisibility() for actor in actors]==visibility
    assert camera_state(view.renderer.GetActiveCamera())==before
    width,height=capture.GetOutput().GetDimensions()[:2]
    white=(pixels[:,:,:3]>180).all(axis=2)
    mask=Image.new('1',(width,height)); draw=ImageDraw.Draw(mask); ratio=view.widget.ratio
    for item in view.annotation_layout['boxes']:
        x0,y0,x1,y1=item['box']; padding=2*ratio
        draw.rectangle((x0-padding,y0-padding,x1+padding,y1+padding),fill=1)
    yy,xx=np.nonzero(white); points=np.column_stack((xx,yy)); font_covered=np.array(mask,dtype=bool)[yy,xx]; line_covered=np.zeros(len(points),dtype=bool)
    def cover_line(start,end,radius):
        delta=end-start; length=np.dot(delta,delta)
        fraction=np.clip((points-start)@delta/length,0,1) if length else np.zeros(len(points))
        line_covered[:]|=np.linalg.norm(points-(start+fraction[:,None]*delta),axis=1)<=radius
    for axis in (view.axes.GetXAxisActor2D(),view.axes.GetYAxisActor2D(),view.axes.GetZAxisActor2D()):
        p0=np.array(axis.GetPositionCoordinate().GetComputedViewportValue(view.renderer)); p1=np.array(axis.GetPosition2Coordinate().GetComputedViewportValue(view.renderer))
        # Exclude actual GL line strokes analytically; Pillow's even-width strokes
        # omit some diagonal caps and lower-edge pixels. Glyph boxes stay unchanged.
        radius=axis.GetProperty().GetLineWidth()/2+1
        cover_line(p0,p1,radius)
        theta=np.arctan2(*(p1-p0)[::-1]); normal=np.array([np.sin(theta),-np.cos(theta)])
        ticks=axis.GetTickPositions()
        for i in range(ticks.GetNumberOfPoints()):
            start=np.array(ticks.GetPoint(i)[:2]); end=start+axis.GetTickLength()*normal
            cover_line(start,end,radius)
    unexpected=points[~(font_covered|line_covered)]
    Image.fromarray(np.flipud(np.array(mask,dtype=np.uint8)*255)).save(tmp_path/'annotation-glyph-box-mask.png')
    line_mask=np.zeros((height,width),dtype=np.uint8); line_mask[yy[line_covered],xx[line_covered]]=255
    Image.fromarray(np.flipud(line_mask)).save(tmp_path/'annotation-axis-line-mask.png')
    Image.fromarray(np.flipud(np.any(full!=pixels,axis=2).astype(np.uint8)*255)).save(tmp_path/'annotation-geometry-difference-mask.png')
    (tmp_path/'annotation-layout.json').write_text(json.dumps(view.annotation_layout,indent=2),encoding='utf8')
    if len(unexpected):
        writer=vtkPNGWriter(); writer.SetFileName(str(tmp_path/'annotation-pixel-mismatch.png')); writer.SetInputConnection(capture.GetOutputPort()); writer.Write()
    assert not len(unexpected),unexpected[:20].tolist()


@pytest.mark.parametrize('width',(620,1038))
@pytest.mark.parametrize('ratio',(1.,1.5,2.))
@pytest.mark.parametrize('uncertainty',(False,True))
def test_real_surface_viewport_material_text_boxes_and_rotated_camera(vtk_widget,tmp_path,width,ratio,uncertainty):
    view=Native3D(); widget=view.widget; widget.ratio=ratio; widget.resize(width,236)
    widget.window.SetOffScreenRendering(1); widget.window.SetSize(round(width*ratio),round(236*ratio)); widget.proxy.Render=widget.window.Render
    widget.interactor.Initialize()
    x,y=np.meshgrid(np.linspace(7,13,32),np.linspace(3.5,6.5,32)); z=.9/(x+2*y); z[:2,:]=np.nan; z[-2:,:]=np.nan
    view.surface(x,y,z)
    if uncertainty:
        view.surface(x,y,z-.002,(.5,.4,.8),.15); view.surface(x,y,z+.002,(.5,.4,.8),.15)
    view.finish(['root width [mm]','tip width [mm]','displacement [mm]'])
    assert view.annotation_layout['valid'],view.annotation_layout
    assert view.annotation_layout['fit_passes']<=3
    assert_annotation_pixels_match_boxes(view,tmp_path)
    initial=camera_state(view.renderer.GetActiveCamera())
    view.renderer.GetActiveCamera().Azimuth(23); view.renderer.GetActiveCamera().Elevation(10)
    changed=camera_state(view.renderer.GetActiveCamera()); view._update_annotation_layout()
    assert camera_state(view.renderer.GetActiveCamera())==changed
    assert view.annotation_layout['valid'],view.annotation_layout
    assert_annotation_pixels_match_boxes(view,tmp_path)
    view.reset_camera(); assert camera_state(view.renderer.GetActiveCamera())==initial
    assert_annotation_pixels_match_boxes(view,tmp_path)
    widget.window.Finalize(); view.close()


def test_hidden_tab_first_fit_then_user_camera_survives_show_and_redraw(vtk_widget,qapp,monkeypatch):
    tabs=QTabWidget(); tabs.resize(960,720); tabs.addTab(QWidget(),'Other'); view=Native3D(); tabs.addTab(view,'Surface'); tabs.show(); qapp.processEvents()
    calls=[]; original=view._fit_camera
    def fit(**kwargs): calls.append(kwargs); return original(**kwargs)
    monkeypatch.setattr(view,'_fit_camera',fit)
    view.points([[1,2,3],[2,3,4]],['a','b']); view.finish(['x','y','z'])
    assert len(calls)==1 and view._initial_fit_pending and not view.isVisible()
    tabs.setCurrentIndex(1); qapp.processEvents(); qapp.processEvents()
    assert len(calls)==2 and not view._initial_fit_pending
    view.renderer.GetActiveCamera().Azimuth(30); view.renderer.GetActiveCamera().Zoom(1.3)
    changed=camera_state(view.renderer.GetActiveCamera())
    tabs.setCurrentIndex(0); tabs.setCurrentIndex(1); qapp.processEvents()
    view.clear(); view.points([[1,2,3],[2,3,4]],['a','b']); view.finish(['x','y','z'])
    view.widget.ratio=2.; view._update_annotation_layout()
    assert len(calls)==2 and camera_state(view.renderer.GetActiveCamera())==changed
    view.close(); tabs.close()


@pytest.mark.parametrize('action',('interaction','explicit_fit','close','replace'))
def test_pending_initial_fit_respects_user_and_scene_lifecycle(vtk_widget,qapp,monkeypatch,action):
    view=Native3D(); calls=[]; original=view._fit_camera
    def fit(**kwargs): calls.append(kwargs); return original(**kwargs)
    monkeypatch.setattr(view,'_fit_camera',fit)
    view.points([[1,2,3],[2,3,4]],['a','b']); view.finish(['x','y','z'])
    if action=='interaction':
        view.renderer.GetActiveCamera().Azimuth(19); view.widget.interactor.InvokeEvent('StartInteractionEvent')
    elif action=='explicit_fit': view.reset_camera()
    before=camera_state(view.renderer.GetActiveCamera()); expected=len(calls); view.show()
    if action=='close': view.close()
    elif action=='replace':
        view.clear(); view.points([[2,4,6],[3,5,7]],['new-a','new-b']); view.finish(['new-x','new-y','new-z']); expected=len(calls)
    qapp.processEvents(); qapp.processEvents()
    assert len(calls)==expected
    if action in ('interaction','explicit_fit'): assert camera_state(view.renderer.GetActiveCamera())==before
    view.close()


@pytest.mark.parametrize('width',(620,1038))
@pytest.mark.parametrize('ratio',(1.,1.5,2.))
@pytest.mark.parametrize('points',[[[1e-4,.05,-200.]],[[1e-12,-1e9,-.003],[5e-12,1e9,-.001]],[[-5e-12,-100,-.1],[5e-12,100,.1]]])
def test_real_annotation_constant_small_large_negative_ranges(vtk_widget,tmp_path,width,ratio,points):
    view=Native3D(); widget=view.widget; widget.ratio=ratio; widget.resize(width,236)
    widget.window.SetOffScreenRendering(1); widget.window.SetSize(round(width*ratio),round(236*ratio)); widget.proxy.Render=widget.window.Render; widget.interactor.Initialize()
    view.points(points,[str(i) for i in range(len(points))]); view.finish(['mass [tonne]','value [N]','stress [MPa]'])
    assert view.annotation_layout['valid'],view.annotation_layout
    for axis in (view.axes.GetXAxisActor2D(),view.axes.GetYAxisActor2D(),view.axes.GetZAxisActor2D()):
        numbers=[item for item in view.annotation_layout['boxes'] if item.get('axis')==axis.GetTitle()]
        assert len({item['text'] for item in numbers})>=2
        import re
        precision=int(re.search(r'\.(\d+)g',axis.GetLabelFormat()).group(1))
        for point,value in ((axis.GetPositionCoordinate().GetComputedViewportValue(view.renderer),axis.GetRange()[0]),(axis.GetPosition2Coordinate().GetComputedViewportValue(view.renderer),axis.GetRange()[1])):
            assert any(item['text']==format(value,f'.{precision}g') and np.linalg.norm(np.array(item['anchor'])-point)<1.5 for item in numbers)
    assert_annotation_pixels_match_boxes(view,tmp_path)
    widget.window.Finalize(); view.close()


def test_missing_endpoint_supplement_is_exact_not_pickable_and_never_duplicates(vtk_widget,tmp_path):
    view=Native3D(); widget=view.widget; widget.resize(620,236)
    widget.window.SetOffScreenRendering(1); widget.window.SetSize(620,236); widget.proxy.Render=widget.window.Render; widget.interactor.Initialize()
    view.points([[1e-12,-1e9,-.003],[5e-12,1e9,-.001]],['a','b']); view.finish(['x','y','z'])
    assert view.annotation_layout['supplemental_count']>0
    for actor in view._endpoint_labels:
        assert not actor.GetPickable() and not actor.GetUseBounds() and actor not in view.actors
    count=view.renderer.GetViewProps().GetNumberOfItems()
    for _ in range(3): view._update_annotation_layout()
    assert view.renderer.GetViewProps().GetNumberOfItems()==count
    view.renderer.GetActiveCamera().Azimuth(150); changed=camera_state(view.renderer.GetActiveCamera()); view._update_annotation_layout()
    assert camera_state(view.renderer.GetActiveCamera())==changed
    # Every supplementary value stays attached to the correct public endpoint after fly-mode reversal.
    axes={axis.GetTitle():axis for axis in (view.axes.GetXAxisActor2D(),view.axes.GetYAxisActor2D(),view.axes.GetZAxisActor2D())}
    for item in view.annotation_layout['boxes']:
        if not item.get('supplemental'): continue
        axis=axes[item['axis']]; endpoints=list(zip((axis.GetPositionCoordinate(),axis.GetPosition2Coordinate()),axis.GetRange()))
        assert any(item['value']==value and np.array_equal(item['anchor'],coordinate.GetComputedViewportValue(view.renderer)) for coordinate,value in endpoints)
    assert_annotation_pixels_match_boxes(view,tmp_path)
    view.reset_camera(); assert view.annotation_layout['valid']
    view.clear(); assert not view._endpoint_labels and not view.actors and view.renderer.GetViewProps().GetNumberOfItems()==0
    view.points([[1,2,3],[2,3,4]],['normal-a','normal-b']); view.finish(['x','y','z'])
    assert view.annotation_layout['supplemental_count']==0
    widget.window.Finalize(); view.close()


def test_surface_native_pixels_and_annotation_contrast_are_theme_independent(vtk_widget):
    from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter
    from vtkmodules.util.numpy_support import vtk_to_numpy
    images=[]
    for dark in (False,True):
        view=ResponseSurfaceWidget(is_dark=dark); widget=view.native.widget; widget.resize(620,236)
        view.xaxis.addItem('x [mm]','x'); view.yaxis.addItem('y [mm]','y'); view.response.addItem('disp [mm]','disp'); view.timer.stop()
        view.artifact=SimpleNamespace(config=SimpleNamespace(method='gp'),dataset=SimpleNamespace(training_revision='1'),qualified=True,metrics=[],id='model')
        widget.window.SetOffScreenRendering(1); widget.window.SetSize(620,236); widget.proxy.Render=widget.window.Render; widget.interactor.Initialize()
        x,y=np.meshgrid(np.linspace(7,13,16),np.linspace(3.5,6.5,16)); view._result=(x,y,.9/(x+2*y),None); view._draw_result()
        view.set_theme(dark)
        assert view.native.renderer.GetBackground()==(.075,.09,.12)
        assert '#e2e8f0' in view.native.axis_key.styleSheet() and '#13171e' in view.native.axis_key.styleSheet()
        capture=vtkWindowToImageFilter(); capture.SetInput(widget.window); capture.Update(); images.append(vtk_to_numpy(capture.GetOutput().GetPointData().GetScalars()).copy())
        widget.window.Finalize(); view.close()
    np.testing.assert_array_equal(images[0],images[1])


def test_first_fit_waits_for_nonzero_visible_render_size(vtk_widget,qapp):
    view=Native3D(); view.resize(960,720); view.widget.setFixedSize(0,0)
    view.points([[1,2,3],[2,3,4]],['a','b']); view.finish(['x','y','z']); view.show()
    qapp.processEvents(); qapp.processEvents()
    assert view._initial_fit_pending
    view.widget.setMaximumSize(16777215,16777215); view.widget.setMinimumSize(0,0); view.widget.resize(620,236)
    qapp.processEvents(); qapp.processEvents()
    assert not view._initial_fit_pending
    view.close()


def test_too_small_viewport_reports_residual_clip_after_bounded_fit(vtk_widget):
    view=Native3D(); widget=view.widget; widget.resize(100,60)
    widget.window.SetOffScreenRendering(1); widget.window.SetSize(100,60); widget.proxy.Render=widget.window.Render; widget.interactor.Initialize()
    view.points([[1e-6,-10,-100],[2e-6,-1,100]],['a','b']); view.finish(['x','y','z'])
    assert not view.annotation_layout['valid'] and view.annotation_layout['fit_passes']<=3
    assert 'enlarge' in view.annotation_message.text()
    widget.window.Finalize(); view.close()


def install_rendered_qt_widget(vtk_widget,monkeypatch,ratio):
    """Real Qt layout plus the installed QVTK resize/DPI method; offscreen GPU only."""
    import vtkmodules.qt.QVTKRenderWindowInteractor as module
    class RenderedInteractor(vtk_widget):
        def __init__(self,parent):
            super().__init__(parent); self.ratio=ratio; self.window.SetOffScreenRendering(1); self.proxy.Render=self.window.Render
            self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
            self.resizeEvent(None)
        def _getPixelRatio(self): return self.ratio
        def resizeEvent(self,event):
            if hasattr(self,'interactor'):
                self._RenderWindow=self.window; self._Iren=self.interactor
                InstalledQVTK.resizeEvent(self,event)
        def Initialize(self): self.interactor.Initialize()
        def Finalize(self): self.window.Finalize()
    monkeypatch.setattr(module,'QVTKRenderWindowInteractor',RenderedInteractor)


def settle_qt(qapp):
    import time
    for _ in range(5): qapp.processEvents(); time.sleep(.01)


@pytest.mark.parametrize('kind,target_height,drag', [('pareto',201,(100,38)),('surface',211,(60,26))])
@pytest.mark.parametrize('width,outer_height',((1380,880),(960,880),(960,720)))
@pytest.mark.parametrize('ratio',(1.,1.5,2.))
def test_full_qt_short_viewport_modest_drag_preserves_camera_data_and_labels(vtk_widget,qapp,monkeypatch,tmp_path,kind,target_height,drag,width,outer_height,ratio):
    from PySide6.QtCore import Qt
    from caelab.mdo.variables import ContinuousVariable,VariableSpace
    from apps.workbench_qt.mdo_studio.window import MDOStudioWindow
    from apps.workbench_qt.theme import build_stylesheet
    from test_mdo_phase3_gui import ServiceSpy
    install_rendered_qt_widget(vtk_widget,monkeypatch,ratio)
    service=ServiceSpy(); service.read_only=True
    service.study=StudyManager('Native viewport boundary',space=VariableSpace(variables=(ContinuousVariable(id='w_root',name='Root width',unit='mm',lower=7,upper=13,initial=10),ContinuousVariable(id='w_tip',name='Tip width',unit='mm',lower=3.5,upper=6.5,initial=5))),objectives=[ObjectiveSpec(id=key,response_id=key) for key in ('disp','stress','mass_elall')])
    for i,(disp,stress,mass) in enumerate(((.0584,297.,.000113),(.08,220.,.00009),(.0969,172.,.0000631))):
        record=service.study.add_point(dict(w_root=8.+i,w_tip=4.+i/2)); record.status='COMPLETED'; record.feasibility='FEASIBLE'; record.is_pareto=True; record.observations=dict(disp=disp,stress=stress,mass_elall=mass)
    service.response_units=dict(disp='mm',stress='MPa',mass_elall='N*s**2/mm'); service.current_point_id=service.study.records[-1].point_id
    win=MDOStudioWindow(service=service); win.resize(width,outer_height); win._default_layout(); win.setup_dock.hide()
    win.resizeDocks([win.tree_dock],[330],Qt.Orientation.Horizontal); win.show(); settle_qt(qapp)
    win.select_point(service.study.records[0].point_id); win._viz_timer.stop(); win.surface_view.timer.stop()
    plot=win.pareto_view if kind=='pareto' else win.surface_view; native=plot.native
    if kind=='pareto':
        plot.dimension.setCurrentText('3D')
    else:
        win.viz_tabs.setCurrentIndex(2); plot.timer.stop()
        plot.response.blockSignals(True); plot.response.addItem('disp [mm]','disp'); plot.response.blockSignals(False)
        plot.artifact=SimpleNamespace(config=SimpleNamespace(method='gp'),dataset=SimpleNamespace(training_revision='1'),qualified=True,metrics=[],id='viewport-fixture')
        x,y=np.meshgrid(np.linspace(7.193548,12.806452,32),np.linspace(3.693548,6.403226,32)); shape=1/(x+2*y)
        z=.058726741+(.101923859-.058726741)*(shape-shape.min())/np.ptp(shape)
        plot.uncertainty.setChecked(True); plot._result=(x,y,z,np.full_like(z,.0004)); plot._draw_result()
    win._viz_timer.stop(); plot.native.layout().activate(); plot.layout.activate()
    settle_qt(qapp)
    assert not native._initial_fit_pending
    if outer_height==880:
        assert native.annotation_layout['valid'],native.annotation_layout
        assert_annotation_pixels_match_boxes(native,tmp_path/'initial-no-fit')
    else:
        (tmp_path/'initial-no-fit-layout.json').write_text(__import__('json').dumps(native.annotation_layout,indent=2),encoding='utf8')
        assert native.annotation_layout['valid'] or native.annotation_message.text()
    # Reproduce the recorded 201/211 logical-pixel canvas through the full splitter,
    # controls, legend, physical key and reserved warning layout, not a detached VTK size.
    for _ in range(5):
        settle_qt(qapp)
        win.center_splitter.widget(1).layout().activate(); plot.layout.activate(); native.layout().activate()
        difference=target_height-native.widget.height(); sizes=win.center_splitter.sizes()
        if abs(difference)<=1: break
        win.center_splitter.setSizes([sizes[0]-difference,sizes[1]+difference])
    settle_qt(qapp); win._viz_timer.stop(); plot.timer.stop() if kind=='surface' else None
    import json
    (tmp_path/'full-qt-geometry.json').write_text(json.dumps(dict(requested_outer=[width,outer_height],observed_outer=[win.width(),win.height()],native_logical=[native.widget.width(),native.widget.height()],render_physical=list(native.widget.window.GetSize()),dpi=native.widget.window.GetDPI(),ratio=ratio,splitter=win.center_splitter.sizes()),indent=2),encoding='utf8')
    if outer_height==880: assert abs(native.widget.height()-target_height)<=2,(native.widget.size(),win.center_splitter.sizes())
    # The splitter adjustment above is an explicit post-show resize. Fit that
    # recorded allocation before reproducing the native modest camera drag;
    # resize itself deliberately preserves the already initialized camera.
    native.reset_camera()
    assert not native._initial_fit_pending and native.annotation_layout['valid'],native.annotation_layout
    assert native.widget.window.GetDPI()==round(72*ratio)
    assert native.annotation_layout['effective_glyph_pixels']==pytest.approx(12*ratio)
    initial=camera_state(native.renderer.GetActiveCamera()); ids={actor:values for actor,values in native.actors.items()}; physical=native.physical_ranges
    selected=set(win._selected_ids); current=service.current_point_id; height=native.widget.height()
    # Native67/71 drag coordinates expressed in logical screen pixels, with the
    # same QVTK top-to-bottom device-pixel conversion and real trackball events.
    interactor=native.widget.interactor; start=(round(native.widget.width()*.55*ratio),round(height*.52*ratio))
    def event(name,x,y): interactor.SetEventInformation(x,y,0,0,'0',0,None); interactor.InvokeEvent(name)
    event('LeftButtonPressEvent',*start)
    event('MouseMoveEvent',start[0]+round(drag[0]*ratio),start[1]-round(drag[1]*ratio))
    assert native.annotation_layout['valid'] and not native._rendering_layout and not native._style_render_suppressed
    assert_annotation_pixels_match_boxes(native,tmp_path/'move-before-release')
    moved=camera_state(native.renderer.GetActiveCamera()); event('LeftButtonReleaseEvent',start[0]+round(drag[0]*ratio),start[1]-round(drag[1]*ratio)); settle_qt(qapp)
    assert moved!=initial and camera_state(native.renderer.GetActiveCamera())==moved
    assert native.annotation_layout['valid'],native.annotation_layout
    assert native.widget.height()==height and native.actors==ids and native.physical_ranges==physical
    assert win._selected_ids==selected and service.current_point_id==current
    assert_annotation_pixels_match_boxes(native,tmp_path)
    # Theme changes and warning transitions never change the canvas allocation.
    win.setStyleSheet(build_stylesheet('dark')); settle_qt(qapp); native._update_annotation_layout()
    assert native.annotation_layout['valid'] and camera_state(native.renderer.GetActiveCamera())==moved
    assert_annotation_pixels_match_boxes(native,tmp_path)
    native.renderer.GetActiveCamera().Zoom(12); zoomed=camera_state(native.renderer.GetActiveCamera()); native._update_annotation_layout(); settle_qt(qapp)
    assert not native.annotation_layout['valid'] and native.annotation_layout['outside_anchors']
    assert native.annotation_message.text() and native.widget.height()==height and camera_state(native.renderer.GetActiveCamera())==zoomed
    assert native.annotation_message.fontMetrics().horizontalAdvance(native.annotation_message.text())<=native.annotation_message.width()-8
    native.reset_camera(); settle_qt(qapp)
    assert native.annotation_layout['valid'] and native.widget.height()==height
    win.close(); settle_qt(qapp)


def test_physical_axis_key_updates_wraps_and_dpi_layout_keeps_camera(vtk_widget,qapp):
    view=Native3D(); view.points([[1,2,3],[2,3,4]],['a','b'])
    labels=['long physical mass response [tonne]','root section width [mm]','von Mises stress [MPa]']
    view.finish(labels); view.resize(430,420); view.show(); qapp.processEvents()
    assert view.axis_key.wordWrap() and not view.axis_key.isHidden()
    assert all(f'{axis}: {label}' in view.axis_key.text() for axis,label in zip('XYZ',labels))
    assert view.axis_key.heightForWidth(400)>view.axis_key.fontMetrics().height()
    camera=view.renderer.GetActiveCamera(); camera.Azimuth(23); changed=camera_state(camera)
    view.widget.ratio=2.; view._update_annotation_layout()
    assert view.axes.GetAxisLabelTextProperty().GetFontSize()==24
    assert view.axes.GetZAxisActor2D().GetTickOffset()==64
    assert camera_state(camera)==changed
    view.clear(); view.points([[1,2,3],[2,3,4]],['a','b']); view.finish(labels[::-1])
    assert view.axis_key.text().startswith('X: von Mises stress [MPa]')
    assert camera_state(camera)==changed
    view.close()


def test_leaders_cannot_cross_unrelated_numbers_titles_or_each_other():
    from apps.workbench_qt.mdo_studio.views.plot_base import leader_conflicts,segments_cross
    boxes=[dict(box=(10,0,20,10)),dict(box=(3,2,7,8)),dict(box=(3,12,7,18))]
    leader=dict(start=(0,5),end=(10,5),label_index=0)
    assert ('glyph',0,1) in leader_conflicts([leader],boxes)
    assert ('glyph',0,2) in leader_conflicts([dict(leader,start=(0,15),end=(10,15))],boxes)
    assert segments_cross((0,0),(10,0),(3,0),(12,0))
    assert not segments_cross((0,0),(10,0),(0,0),(0,10))
    assert ('leader',0,1) in leader_conflicts([leader,dict(start=(5,0),end=(5,10),label_index=1)],boxes)


def test_coincident_tick_origins_keep_distinct_values_and_allow_fanout():
    from apps.workbench_qt.mdo_studio.views.plot_base import repair_axis_numeric_boxes,box_overlap,leader_conflicts
    raw=[('Y',172.,(275,35,296,47),(317,60)),('Y',297.,(312,32,333,44),(329,57)),
         ('Z',6.31e-5,(292,11,342,23),(317,60)),('Z',.000113,(292,11,344,23),(317,60)),
         ('X',.0969,(341,30,379,42),(329,57)),('X',.0584,(344,58,382,70),(332,69))]
    boxes=[dict(axis=axis,value=value,text=str(value),box=box,raw_position=box[:2],anchor=anchor) for axis,value,box,anchor in raw]
    def axis(name):
        coordinate=SimpleNamespace(GetComputedViewportValue=lambda renderer:(317,60))
        return SimpleNamespace(GetTitle=lambda:name,GetPositionCoordinate=lambda:coordinate,GetPosition2Coordinate=lambda:coordinate)
    repaired,custom,leaders=repair_axis_numeric_boxes(boxes,[axis(key) for key in 'XYZ'],None,(614,126),8,4)
    assert 'Z' in custom and leaders
    assert [(item['axis'],item['value'],item['anchor']) for item in repaired]==[(item['axis'],item['value'],item['anchor']) for item in boxes]
    assert not any(box_overlap(a['box'],b['box'],4)>0 for i,a in enumerate(repaired) for b in repaired[i+1:])
    assert not leader_conflicts(leaders,repaired)


def test_title_repair_frees_clipped_title_origin_without_moving_anchors():
    from apps.workbench_qt.mdo_studio.views.plot_base import repair_axis_titles,box_overlap,box_clip,leader_conflicts
    raw=[('172',(554,67,593,90),(600,122)),('297',(601,43,640,66),(647,98)),
         ('0.000113',(481,167,579,190),(600,179)),('6.31e-05',(481,110,577,133),(600,122)),
         ('0.0969',(652,29,724,52),(647,98)),('0.0584',(698,84,770,107),(693,122)),
         ('Y',(589,7,607,34),(600,122)),('Z',(590,102,606,129),(600,179)),('X',(659,76,678,103),(670,110))]
    boxes=[dict(text=text,box=box,raw_position=box[:2],anchor=anchor,**({'axis':'YZX'[i//2]} if i<6 else {})) for i,(text,box,anchor) in enumerate(raw)]
    leaders=[dict(axis='X',value=.0584,label_index=5,start=(693,122),end=(698,107))]
    repaired,custom,lines=repair_axis_titles(boxes,leaders,(1228,252),16,8)
    assert {'Y','Z'}<=custom
    assert [item['anchor'] for item in repaired]==[item['anchor'] for item in boxes]
    assert repaired[:6]==boxes[:6]
    assert not any(box_clip(item['box'],(1228,252),16) for item in repaired)
    assert not any(box_overlap(a['box'],b['box'],8)>0 for i,a in enumerate(repaired) for b in repaired[i+1:])
    assert not leader_conflicts(lines,repaired)


@pytest.mark.parametrize('enabled',(0,1))
@pytest.mark.parametrize('suppressed',(False,True))
def test_style_action_restores_original_render_flags_and_invalidates_failure(vtk_widget,enabled,suppressed):
    view=Native3D(); view.points([[1,2,3],[2,3,4]],['a','b']); view.finish(['x','y','z'])
    interactor=view.widget.interactor; interactor.SetEnableRender(enabled); view._style_render_suppressed=suppressed
    view.annotation_layout={'old':True}
    def fail(): raise RuntimeError('camera action fault')
    with pytest.raises(RuntimeError,match='camera action fault'): view._style_camera_action(view.interaction_style,fail)
    assert interactor.GetEnableRender()==enabled and view._style_render_suppressed is suppressed
    assert view.annotation_layout is None and not view._custom_labels and not view._label_leaders
    view.close()


@pytest.mark.parametrize('swap',(0,1))
@pytest.mark.parametrize('mutation',('exception','clear','replace','close'))
def test_annotation_render_fault_and_reentrant_scene_change(vtk_widget,qapp,swap,mutation):
    view=Native3D(); view.points([[1,2,3],[2,3,4]],['old-a','old-b']); view.finish(['x','y','z'])
    window=view.widget.window; window.SetSwapBuffers(swap); calls=[]; original=view.widget.proxy.Render
    def render():
        calls.append(view.available)
        if len(calls)==1:
            if mutation=='exception': raise RuntimeError('render fault')
            if mutation=='close': view.close()
            else:
                view.clear()
                if mutation=='replace':
                    view.points([[3,4,5],[4,5,6]],['new-a','new-b']); view.finish(['new-x','new-y','new-z'])
        else: original()
    view.widget.proxy.Render=render
    if mutation=='exception':
        with pytest.raises(RuntimeError,match='render fault'): view._render_annotations()
    else: view._render_annotations()
    assert window.GetSwapBuffers()==swap and not view._rendering_layout and view._render_scene is None
    assert all(calls)
    if mutation=='replace':
        qapp.processEvents(); assert view.axes is not None and all(ids[0].startswith('new-') for ids in view.actors.values())
    else:
        assert view.annotation_layout is None and not view._custom_labels and not view._label_leaders
        if mutation!='exception': assert not view.actors
        if mutation=='close':
            count=len(calls); qapp.processEvents(); view._update_annotation_layout(); assert len(calls)==count and not view.available
    view.close()


@pytest.mark.parametrize('ranges',[(1e-4,2e-4,.02,.08,120.,350.),(1.,1.000000000001,-1e-12,1e-12,200.,201.)])
def test_tick_density_and_precision_do_not_duplicate_adjacent_values(ranges):
    count,precision=axis_label_layout(ranges,480,220)
    assert 2<=count<=4 and 3<=precision<=17
    for i in (0,2,4):
        ticks=np.linspace(ranges[i],ranges[i+1],count)
        assert len({format(value,f'.{precision}g') for value in ticks})==count
    assert axis_label_layout(ranges,1200,800)[0]>=count


@pytest.mark.parametrize('ratio',(1.,1.5,2.))
def test_click_threshold_uses_device_pixels_and_drag_release_never_selects(vtk_widget,ratio):
    view=Native3D(); view.widget.ratio=ratio; view.points([[1,2,3]],['DP-known'])
    actor=next(iter(view.actors)); selected=[]; view.point_selected.connect(selected.append)
    picker=SimpleNamespace(Pick=lambda *args:None,GetActor=lambda:actor,GetPointId=lambda:0)
    original_picker=view.picker; view.picker=picker
    position=[(100,100)]; interactor=SimpleNamespace(GetEventPosition=lambda:position[0])
    view._begin_pick(interactor,None); position[0]=(100+round(3*ratio),100); view._pick(interactor,None)
    assert selected==['DP-known']
    view._begin_pick(interactor,None); position[0]=(position[0][0]+round(6*ratio),100); view._track_pick(interactor,None)
    position[0]=(100,100); view._pick(interactor,None)
    assert selected==['DP-known']
    view.picker=original_picker
    view.close()


@pytest.mark.parametrize('ratio',(1.,1.5,2.))
def test_real_vtk_interactor_trackball_focus_release_picks_and_drag_only_rotates(vtk_widget,ratio):
    view=Native3D(); widget=view.widget
    widget.ratio=ratio; widget.window.SetSize(round(800*ratio),round(320*ratio))
    widget.window.SetOffScreenRendering(1); widget.proxy.Render=widget.window.Render
    interactor=widget.interactor; interactor.Initialize()
    view.points([[1,2,3]],['known-point']); view.finish(['x [mm]','y [mm]','z [MPa]'])
    selected=[]; view.point_selected.connect(selected.append)
    x,y=map(round,view._project((.5,.5,.5)))
    def event(name,xx,yy):
        interactor.SetEventInformation(xx,yy,0,0,'0',0,None); interactor.InvokeEvent(name)
    before=camera_state(view.renderer.GetActiveCamera())
    event('LeftButtonPressEvent',x,y); event('LeftButtonReleaseEvent',x,y)
    assert selected==['known-point']
    assert camera_state(view.renderer.GetActiveCamera())==before
    event('LeftButtonPressEvent',x,y); event('MouseMoveEvent',x+45,y+25); event('LeftButtonReleaseEvent',x+45,y+25)
    assert selected==['known-point']
    assert camera_state(view.renderer.GetActiveCamera())!=before
    widget.window.Finalize(); view.close()


def test_native_controls_and_layer_key_toggle_both_directions(vtk_widget):
    manager=StudyManager(objectives=[ObjectiveSpec(id=key,response_id=key) for key in ('mass','disp','stress')])
    actual=manager.add_point({'x':1.}); actual.status='COMPLETED'; actual.feasibility='FEASIBLE'; actual.observations=dict(mass=1e-4,disp=.1,stress=200.); actual.is_pareto=True
    view=ParetoFrontWidget(); view.set_study(manager.records,manager.objectives,dict(mass='kg',disp='mm',stress='MPa'))
    assert not view.mpl_toolbar.isHidden() and view.native.isHidden()
    view.dimension.setCurrentText('3D')
    assert view.mpl_toolbar.isHidden() and not view.native.isHidden() and not view.native.legend.isHidden()
    for text in ('Actual feasible','Actual marginal','Actual infeasible','Surrogate prediction','Full-objective feasible front','Selected'):
        assert text in view.native.legend.text()
    assert 'Drag: rotate' in view.native.instructions.text() and 'Click point: select' in view.native.instructions.text()
    view.dimension.setCurrentText('2D'); assert not view.mpl_toolbar.isHidden() and view.native.isHidden()
    surface=ResponseSurfaceWidget(); assert surface.mpl_toolbar.isHidden() and not surface.native.isHidden()
    view.close(); surface.close()


@pytest.mark.parametrize('is_dark',(False,True))
def test_native_selected_color_matches_visible_key_in_both_themes(vtk_widget,is_dark):
    manager=StudyManager(objectives=[ObjectiveSpec(id=key,response_id=key) for key in ('mass','disp','stress')])
    record=manager.add_point({}); record.status='COMPLETED'; record.feasibility='FEASIBLE'; record.observations=dict(mass=1e-4,disp=.1,stress=200.)
    view=ParetoFrontWidget(is_dark=is_dark); view.set_study(manager.records,manager.objectives,{})
    view.set_selected([record.point_id]); view.dimension.setCurrentText('3D')
    actor=list(view.native.actors)[-1]
    assert actor.GetProperty().GetColor()==(1.,1.,1.)
    assert '<span style="color:#ffffff">●</span> Selected' in view.native.legend.text()
    # The hidden / restored Matplotlib selection keeps the original theme contrast.
    view.dimension.setCurrentText('2D')
    np.testing.assert_allclose(view.ax.collections[-1].get_edgecolors()[0,:3],(1,1,1) if is_dark else np.array([15,23,42])/255)
    view.close()


def test_native_empty_error_clears_picker_and_can_recover(vtk_widget):
    manager=StudyManager(objectives=[ObjectiveSpec(id=key,response_id=key) for key in ('mass','disp','stress')])
    record=manager.add_point({}); record.status='COMPLETED'; record.feasibility='FEASIBLE'; record.observations=dict(mass=1e-4,disp=.1,stress=200.)
    view=ParetoFrontWidget(); view.set_study(manager.records,manager.objectives,{}); view.dimension.setCurrentText('3D')
    assert view.native.actors and view.native.picker.GetPickList().GetNumberOfItems()
    selected=[]; view.point_selected.connect(selected.append)
    view.empty('Choose three different objective responses.')
    assert not view.native.actors and view.native.picker.GetPickList().GetNumberOfItems()==0
    assert view.native.axes is None and not view.native.message.isHidden()
    assert 'different objective' in view.native.message.text()
    view.native._pick(view.native.widget.interactor,None); assert selected==[]
    view.refresh(); assert view.native.actors and view.native.message.isHidden()
    view.native.clear(); view.native.finish(['x','y','z'])
    assert 'No finite 3D values' in view.native.message.text() and not view.native.message.isHidden()
    assert not view.native.actors and view.native.axes is None
    view.refresh(); assert view.native.actors and view.native.message.isHidden()
    view.close()


def test_stale_surface_error_cannot_clear_latest_scene(vtk_widget):
    view=ResponseSurfaceWidget(); view.native.points([[1,2,3]],['latest']); view.native.finish(['x [mm]','y [mm]','z [MPa]'])
    cached=(np.ones((2,2)),)*4; view._result=cached
    view._token=3; view._running=True; view._running_token=2
    before=view.native.widget.renders
    view._receive(2,None,'stale failure')
    assert view.native.actors and view.native.message.isHidden()
    assert view._result is cached
    assert view.native.widget.renders==before
    view._running=True; view._running_token=3
    view._receive(3,None,'current prediction failed')
    assert not view.native.actors and view.native.axes is None and not view.native.message.isHidden()
    assert view._result is None
    view.uncertainty.setChecked(True)
    assert not view.native.actors and not view.native.message.isHidden()
    assert view.native.widget.renders>before
    view.artifact=SimpleNamespace(config=SimpleNamespace(method='gp'),dataset=SimpleNamespace(training_revision='1'),qualified=True,metrics=[],id='new-model')
    view.xaxis.addItem('width [mm]','width'); view.yaxis.addItem('depth [mm]','depth'); view.response.addItem('disp [mm]','disp'); view.timer.stop()
    x,y=np.meshgrid([1.,2.],[3.,4.]); result=(x,y,x+y,np.full((2,2),.01))
    view._running=True; view._running_token=view._token; view._receive(view._token,result,None)
    assert view._result is result and view.native.message.isHidden()
    assert view.native.renderer.GetViewProps().GetNumberOfItems()>0
    view.close()


@pytest.mark.parametrize('invalid',('missing_model','same_axes'))
def test_invalid_surface_dispatch_invalidates_cached_scene(vtk_widget,invalid):
    view=ResponseSurfaceWidget(); view.native.points([[1,2,3]],['old']); view.native.finish(['x','y','z'])
    view._result=(np.ones((2,2)),)*4
    if invalid=='same_axes':
        view.artifact=object(); view.xaxis.addItem('x','x'); view.yaxis.addItem('x','x')
    view.timer.stop(); view._dispatch()
    assert view._result is None and not view.native.actors and not view.native.message.isHidden()
    view.uncertainty.setChecked(True)
    assert not view.native.actors and not view.native.message.isHidden()
    view.close()


@pytest.mark.parametrize('change',('response','fixed_value'))
def test_surface_request_change_clears_old_identity_until_current_result(vtk_widget,change):
    from caelab.mdo.variables import ContinuousVariable,VariableSpace
    space=VariableSpace(variables=tuple(ContinuousVariable(id=key,name=key,unit='mm',lower=1,upper=5,initial=2) for key in ('x','y','fixed')))
    artifact=SimpleNamespace(config=SimpleNamespace(method='gp'),dataset=SimpleNamespace(training_revision='1',response_ids=('disp','stress')),qualified=True,metrics=[],id='model')
    view=ResponseSurfaceWidget(); view.set_study(artifact,space,[],dict(disp='mm',stress='MPa')); view.timer.stop()
    x,y=np.meshgrid([1.,2.],[3.,4.]); old=(x,y,np.array([[.1,.2],[.3,.4]]),np.full((2,2),.01))
    view._result=old; view._draw_result(); camera=camera_state(view.native.renderer.GetActiveCamera())
    previous=view._token; view._running=True; view._running_token=previous
    if change=='response': view.response.setCurrentIndex(view.response.findData('stress'))
    else: view._controls['fixed'].setValue(3.)
    view.timer.stop()
    assert view._result is None and view.native.axes is None
    assert view.native.renderer.GetViewProps().GetNumberOfItems()==0
    assert 'Computing the current response' in view.native.message.text()
    view.uncertainty.setChecked(True)
    assert view.native.renderer.GetViewProps().GetNumberOfItems()==0 and not view.native.message.isHidden()
    assert camera_state(view.native.renderer.GetActiveCamera())==camera
    view._receive(previous,old,None)  # Completion from the replaced request remains inadmissible.
    assert view._result is None and not view.native.message.isHidden()
    current=(x,y,np.array([[100.,200.],[300.,400.]]),np.full((2,2),.1))
    view._running=True; view._running_token=view._token; view._receive(view._token,current,None)
    assert view._result is current and view.native.message.isHidden()
    assert 'Z: '+('stress [MPa]' if change=='response' else 'disp [mm]') in view.native.axis_key.text()
    assert view.native.physical_ranges[4]>99 and camera_state(view.native.renderer.GetActiveCamera())==camera
    view.close()


def test_study_tree_separates_sixteen_actual_and_two_hundred_predictions(qapp):
    manager=StudyManager()
    for i in range(216):
        record=manager.add_point({'x':i}); record.status='COMPLETED'; record.mode='ACTUAL' if i<16 else 'SURROGATE'
    tree=StudyTreeWidget(); tree.populate(manager)
    root=tree.tree.topLevelItem(0); labels=[root.child(i).text(0) for i in range(root.childCount())]
    assert 'Native evaluations (16 completed)' in labels
    assert 'Optimization runs (200 surrogate predictions)' in labels
    assert not any('216 completed' in label for label in labels)
    tree.close()
