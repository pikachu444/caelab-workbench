"""Installed VTK configuration/interaction tests; these do not certify native pixels."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from types import SimpleNamespace
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication,QWidget,QLabel,QTabWidget
from vtkmodules.vtkRenderingCore import vtkRenderWindow,vtkRenderWindowInteractor
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
    """Check real VTK glyph pixels, not only a mathematical overlap oracle."""
    from PIL import Image,ImageDraw
    from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter
    from vtkmodules.util.numpy_support import vtk_to_numpy
    capture=vtkWindowToImageFilter(); capture.SetInput(view.widget.window); capture.Update()
    width,height=capture.GetOutput().GetDimensions()[:2]
    pixels=vtk_to_numpy(capture.GetOutput().GetPointData().GetScalars()).reshape(height,width,-1)
    white=(pixels[:,:,:3]>180).all(axis=2)
    mask=Image.new('1',(width,height)); draw=ImageDraw.Draw(mask); ratio=view.widget.ratio
    for item in view.annotation_layout['boxes']:
        x0,y0,x1,y1=item['box']; padding=2*ratio
        draw.rectangle((x0-padding,y0-padding,x1+padding,y1+padding),fill=1)
    yy,xx=np.nonzero(white); points=np.column_stack((xx,yy)); covered=np.array(mask,dtype=bool)[yy,xx]
    def cover_line(start,end,radius):
        delta=end-start; length=np.dot(delta,delta)
        fraction=np.clip((points-start)@delta/length,0,1) if length else np.zeros(len(points))
        covered[:]|=np.linalg.norm(points-(start+fraction[:,None]*delta),axis=1)<=radius
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
    unexpected=points[~covered]
    if len(unexpected):
        from vtkmodules.vtkIOImage import vtkPNGWriter
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
        for point,value in ((axis.GetPositionCoordinate().GetComputedViewportValue(view.renderer),axis.GetRange()[0]),(axis.GetPosition2Coordinate().GetComputedViewportValue(view.renderer),axis.GetRange()[1])):
            assert any(item['text']==axis.GetLabelFormat()%value and np.linalg.norm(np.array(item['anchor'])-point)<1.5 for item in numbers)
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
    assert not view.message.isHidden() and 'Enlarge' in view.message.text()
    widget.window.Finalize(); view.close()


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
    view.picker=picker
    position=[(100,100)]; interactor=SimpleNamespace(GetEventPosition=lambda:position[0])
    view._begin_pick(interactor,None); position[0]=(100+round(3*ratio),100); view._pick(interactor,None)
    assert selected==['DP-known']
    view._begin_pick(interactor,None); position[0]=(position[0][0]+round(6*ratio),100); view._track_pick(interactor,None)
    position[0]=(100,100); view._pick(interactor,None)
    assert selected==['DP-known']
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
