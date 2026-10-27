"""Installed VTK configuration/interaction tests; these do not certify native pixels."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from types import SimpleNamespace
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication,QWidget,QLabel
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
    return camera.GetPosition(),camera.GetFocalPoint(),camera.GetViewUp(),camera.GetParallelProjection(),camera.GetParallelScale()


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
    view._token=3; view._running=True; view._running_token=2
    before=view.native.widget.renders
    view._receive(2,None,'stale failure')
    assert view.native.actors and view.native.message.isHidden()
    assert view.native.widget.renders==before
    view._running=True; view._running_token=3
    view._receive(3,None,'current prediction failed')
    assert not view.native.actors and view.native.axes is None and not view.native.message.isHidden()
    assert view.native.widget.renders>before
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
