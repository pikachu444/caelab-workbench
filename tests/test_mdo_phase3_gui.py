"""Studio interaction tests. Offscreen Qt and service spies are not native evidence."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import time
from types import SimpleNamespace
import numpy as np
import pytest
from PySide6.QtCore import Qt,QEvent,QPersistentModelIndex
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtWidgets import QApplication,QMessageBox,QDialogButtonBox
from caelab.workbench import Workbench
from caelab.mdo.study_manager import StudyManager,DesignPointRecord
from caelab.mdo.variables import ContinuousVariable,VariableSpace
from caelab.mdo.objectives import ObjectiveSpec
from apps.workbench_qt.mdo_studio.window import MDOStudioWindow,startup_service
from apps.workbench_qt.mdo_studio.design_points_model import DesignPointsTableModel,DesignPointsProxyModel
from apps.workbench_qt.mdo_studio.study_config import StudyConfigEditor,ParameterizeDialog


@pytest.fixture(scope='session')
def qapp(): return QApplication.instance() or QApplication([])


class ServiceSpy:
    def __init__(self):
        self.workbench=Workbench(); self.study=StudyManager('Independent interaction fixture',space=VariableSpace(variables=(
            ContinuousVariable(id='x',name='긴 한글 변수 이름',unit='mm',lower=1,upper=20,initial=2),
            ContinuousVariable(id='y',name='Width',unit='mm',lower=1,upper=5,initial=3))),
            objectives=[ObjectiveSpec(id='mass',response_id='mass'),ObjectiveSpec(id='disp',response_id='disp')])
        for i,(mass,disp) in enumerate(((10.,1.),(2.,5.),(3.,3.))):
            rec=self.study.add_point(dict(x=i+1.,y=2.),name=f'한글 설계점 {i+1}')
            rec.status='COMPLETED'; rec.feasibility='FEASIBLE'; rec.observations={'mass':mass,'disp':disp}; rec.is_pareto=True
        self.input_path=None; self.template=SimpleNamespace(token_names=('X','Y')); self.response_units={'mass':'kg','disp':'mm'}
        self.response_selectors={}; self.study_file=None; self.current_point_id=None; self.read_only=False
        self.active_batch_id=None; self.surrogate=None; self.optimization_result=None; self.revision='1'; self.study_id='fixture'; self.view_state={}; self.calls=[]
    def close(self): pass
    def configure(self,*args,**kwargs): self.calls.append(('configure',args,kwargs))
    def is_verified(self,*args,**kwargs): return True


@pytest.fixture
def win(qapp,monkeypatch):
    monkeypatch.setattr(QMessageBox,'warning',lambda *args:None)
    service=ServiceSpy(); window=MDOStudioWindow(service=service)
    yield window
    window._busy=None; window.close(); qapp.processEvents()


def test_five_views_and_numeric_sort_id_selection(win,qapp):
    assert win.viz_tabs.count()==5
    mass_col=8+len(win.tdp_model.parameter_keys)
    win.tdp_proxy.sort(mass_col,Qt.SortOrder.AscendingOrder)
    first=win.tdp_proxy.index(0,0).data(DesignPointsTableModel.PointIdRole)
    assert first==win.study.records[1].point_id
    win.tdp_table.selectRow(0); selected=set(win._selected_ids)
    persistent=QPersistentModelIndex(win.tdp_proxy.index(0,0))
    win.tdp_proxy.sort(mass_col,Qt.SortOrder.DescendingOrder)
    assert win._selected_ids==selected
    assert persistent.data(DesignPointsTableModel.PointIdRole)==first
    assert win.setup_widget._current_record.point_id==first
    win.search.setText('absent')
    assert win._selected_ids==selected and win.tdp_proxy.rowCount()==0
    assert 'hidden by filter' in win.tdp_lbl.text()
    win.search.clear(); assert win.tdp_proxy.rowCount()==3 and win._selected_ids==selected
    assert not win.service.calls


def test_pareto_pick_selects_matching_sorted_row_without_apply(win,qapp):
    win.pareto_view.canvas.draw()
    artist,ids=next(iter(win.pareto_view._picks.items()))
    win.pareto_view._pick(SimpleNamespace(artist=artist,ind=[1]))
    assert win._selected_ids=={ids[1]}
    assert win.tdp_table.selectionModel().selectedRows()[0].data(DesignPointsTableModel.PointIdRole)==ids[1]
    assert not win.service.calls
    # A normal table selection also reaches the plot's stable identity state.
    win.tdp_table.selectRow(0)
    assert win.pareto_view._selected==win._selected_ids
    assert not win.pareto_view.ax.lines  # No interpolated line between disconnected front points.


def test_draft_survives_tree_context_and_revert(win):
    win._on_configure(); editor=win._editor
    editor.variables.item(0,5).setText('한글 수정 초안')
    win._method_context('DOE'); win._on_configure()
    assert win._editor is editor and editor.variables.item(0,5).text()=='한글 수정 초안'
    editor.revert(); assert editor.variables.item(0,5).text()=='긴 한글 변수 이름'
    assert not win.service.calls


def test_parameterization_preview_and_korean_preedit(qapp):
    dialog=ParameterizeDialog({'C:/independent/model.inp':'*CLOAD\n1, 2, -100\n'})
    dialog.line.setValue(2); dialog.literal.setText('-100'); dialog.token.setText('LOAD'); dialog._preview()
    assert dialog.binding['expected_line']=='1, 2, -100' and '<LOAD>' in dialog.preview.text()
    before=dialog.token.text(); event=QInputMethodEvent('한글',[]); QApplication.sendEvent(dialog.token,event)
    assert dialog.token.text()==before  # Native preedit has not committed a token.
    commit=QInputMethodEvent(); commit.setCommitString('한글'); QApplication.sendEvent(dialog.token,commit)
    assert '한글' in dialog.token.text()
    dialog.close()


def test_response_surface_stale_worker_result_ignored(win):
    surface=win.surface_view; surface._token=5
    surface._receive(4,(None,None,None,None),None)
    assert surface._result is None


def test_configuration_omits_unspecified_node_filter_and_keeps_units(win):
    win._on_configure(); configuration=win._editor.configuration()
    assert configuration[3]=={'disp':'mm','mass':'kg'}
    assert all('node_ids' not in selector for selector in configuration[4].values())


def test_convergence_counts_attempts_and_fixed_actual_hypervolume(win,qapp):
    first=win.study.records[0]
    first.attempts=[dict(status='COMPLETED',feasibility='FEASIBLE',objectives={'mass':10.,'disp':1.},responses={'mass':10.,'disp':1.},total_violation=0.,duration_sec=.1,finished_at='2026-10-27T01:00:00Z'),
        dict(status='FAILED',feasibility='NOT_ASSESSED',objectives={},responses={},total_violation=None,duration_sec=.2,finished_at='2026-10-27T01:00:01Z')]
    win._populate_study()
    trace=win.convergence_view; trace.set_study(win.tdp_model.records,win.study.objectives,win.tdp_model.units,'1')
    assert list(trace.ax.lines[0].get_ydata())==[1,1]
    trace.reference.setText('20,20'); trace.metric_selector.setCurrentText('Measured hypervolume')
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        qapp.processEvents()
        if trace.ax.get_ylabel()=='Measured hypervolume': break
        time.sleep(.01)
    values=trace.ax.lines[0].get_ydata()
    assert values[0]==pytest.approx(190.) and np.isnan(values[1])


def test_table_snapshot_does_not_alias_mutable_service_records(win):
    rec=win.study.records[0]; snapshot=win.tdp_model.record(rec.point_id)
    rec.observations['mass']=999.
    assert snapshot.observations['mass']==10.
    win.tdp_model.update_record(rec)
    assert win.tdp_model.record(rec.point_id).observations['mass']==999.


def test_10k_scalar_snapshot_sort_filter(qapp):
    records=[DesignPointRecord(point_id=f'point-{i}',index=i+1,name=f'Point {i}',physical_values={f'x{j}':float(i+j) for j in range(20)},observations={'a':float(10000-i),'b':float(i)}) for i in range(10000)]
    model=DesignPointsTableModel(); start=time.perf_counter()
    model.set_study_data(records,[f'x{j}' for j in range(20)],['a','b'])
    assert model.columnCount()==32
    # A generous deterministic guard; native 10k×30 latency is measured separately on Windows.
    assert time.perf_counter()-start<5
    proxy=DesignPointsProxyModel(); proxy.setSourceModel(model); proxy.sort(28,Qt.SortOrder.AscendingOrder)
    assert proxy.index(0,0).data(model.PointIdRole)=='point-9999'
    proxy.set_filters(text='Point 9999'); assert proxy.rowCount()==1


def test_readonly_startup_never_creates_or_autoloads_wrong_relation():
    calls=[]
    wb=SimpleNamespace(open_mdo_study=lambda path,**kw:calls.append((path,kw)) or 'opened',
        create_mdo_study=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Writer startup called')))
    project=SimpleNamespace(nodes={'node':SimpleNamespace(files={'mdo_study':SimpleNamespace(path='relation.json')})})
    assert startup_service(wb,study_path='explicit.json',project=project,node_id='node',read_only=True)=='opened'
    assert calls==[('explicit.json',dict(read_only=True,project=project,node_id='node'))]
    calls.clear(); startup_service(wb,project=project,node_id='node',read_only=True)
    assert calls[0][0]=='relation.json' and calls[0][1]['read_only']
    with pytest.raises(ValueError,match='requires'): startup_service(wb,read_only=True)
