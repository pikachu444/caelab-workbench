"""Studio interaction tests. Offscreen Qt and service spies are not native evidence."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import time
from types import SimpleNamespace
import numpy as np
import pytest
from PySide6.QtCore import Qt,QEvent,QPersistentModelIndex,QModelRoleData,QModelRoleDataSpan
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtWidgets import QApplication,QMessageBox,QDialogButtonBox,QPushButton,QDialog,QFormLayout,QLabel
from caelab.workbench import Workbench
from caelab.mdo.study_manager import StudyManager,DesignPointRecord
from caelab.mdo.variables import ContinuousVariable,VariableSpace
from caelab.mdo.objectives import ObjectiveSpec
from apps.workbench_qt.mdo_studio.window import MDOStudioWindow,startup_service
from apps.workbench_qt.mdo_studio.design_points_model import DesignPointsTableModel,DesignPointsProxyModel
from apps.workbench_qt.mdo_studio.study_config import StudyConfigEditor,ParameterizeDialog,SurrogateTrainingDialog
from test_mdo_phase3_service import service as native_service,measured


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
    mass_col=win.tdp_model.ParameterStart+len(win.tdp_model.parameter_keys)
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
    proxy=DesignPointsProxyModel(); proxy.setSourceModel(model); proxy.sort(model.ParameterStart+20,Qt.SortOrder.AscendingOrder)
    assert proxy.index(0,0).data(model.PointIdRole)=='point-9999'
    proxy.set_filters(text='Point 9999'); assert proxy.rowCount()==1
    # Opening a study with fewer columns must retire the prior response-column sort.
    model.set_study_data(records,[],[])
    assert proxy.rowCount()==1 and proxy.columnCount()==10


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


def test_point_numeric_sort_at_ten_thousand_and_insertion(qapp):
    rows=[DesignPointRecord(point_id=f'point-{i}',index=i,name=f'Point {i}',physical_values={}) for i in range(1,10001)]
    model=DesignPointsTableModel(rows); proxy=DesignPointsProxyModel(); proxy.setSourceModel(model)
    proxy.sort(0,Qt.SortOrder.AscendingOrder)
    assert proxy.index(9999,0).data(model.SortRole)==10000
    proxy.sort(0,Qt.SortOrder.DescendingOrder); assert proxy.index(0,0).data(model.SortRole)==10000
    model.update_record(DesignPointRecord(point_id='new',index=10001,name='new',physical_values={}))
    assert proxy.index(0,0).data(model.SortRole)==10001


def test_proxy_result_deltas_update_numeric_sort_and_filter_membership(qapp):
    from dataclasses import replace
    first=DesignPointRecord(point_id='a',index=1,name='kept A',physical_values={'x':2.})
    second=DesignPointRecord(point_id='b',index=2,name='kept B',physical_values={'x':1.})
    model=DesignPointsTableModel([first,second],['x']); proxy=DesignPointsProxyModel(); proxy.setSourceModel(model)
    proxy.sort(model.ParameterStart,Qt.SortOrder.AscendingOrder); proxy.set_filters(text='kept')
    selected=QPersistentModelIndex(proxy.index(0,0))
    model.update_record(replace(first,physical_values={'x':0.}))
    assert proxy.index(0,0).data(model.PointIdRole)=='a' and selected.data(model.PointIdRole)=='b'
    model.update_record(replace(first,name='hidden',physical_values={'x':0.}))
    assert proxy.rowCount()==1 and proxy.index(0,0).data(model.PointIdRole)=='b'
    model.update_record(replace(first,name='kept again',physical_values={'x':0.}))
    assert proxy.rowCount()==2 and proxy.index(0,0).data(model.PointIdRole)=='a'
    # A response-only change must retain the same sorted IDs and typed cell values.
    model.update_record(replace(second,name='kept B',physical_values={'x':1.},observations={'response':3.}))
    assert [proxy.index(row,0).data(model.PointIdRole) for row in range(2)]==['a','b']


def test_batched_roles_match_data_and_clear_reused_unsupported_values(win):
    for column in (0,4,win.tdp_model.ParameterStart):
        index=win.tdp_proxy.index(0,column)
        for role in (Qt.ItemDataRole.DisplayRole,Qt.ItemDataRole.TextAlignmentRole,Qt.ItemDataRole.ForegroundRole,
                     Qt.ItemDataRole.ToolTipRole,Qt.ItemDataRole.FontRole,win.tdp_model.PointIdRole,win.tdp_model.SortRole):
            data=QModelRoleData(int(role)); data.setData('stale reused role')
            win.tdp_proxy.multiData(index,QModelRoleDataSpan(data))
            assert data.data()==win.tdp_proxy.data(index,int(role))


def test_exact_scalar_preview_selection_crlf_and_unicode(qapp):
    source='** 한글 😀\r\n'*20+'*ELASTIC\r\n10,1,1\r\n'
    dialog=ParameterizeDialog({'C:/external/scalars.inp':source}); dialog.token.setText('A')
    first=dialog.source.document().find('1',dialog.source.document().find('10,'))
    second=dialog.source.document().find('1',first); dialog.source.setTextCursor(second)
    next(button for button in dialog.findChildren(QPushButton) if button.text()=='Use selected scalar literal').click()
    assert dialog.line.value()==22 and dialog.occurrence.value()==2
    assert dialog.binding['expected_line']=='10,1,1' and dialog.preview.text().endswith('10,1,<A>')
    dialog.close()


def test_filters_change_visible_plot_population(win):
    win.select_point(win.study.records[1].point_id); win.search.setText('한글 설계점 2'); win._update_visualizations()
    assert [r.point_id for r in win.pareto_view.records]==[win.study.records[1].point_id]
    assert '1 / 3' in win.plot_filter_label.text() and 'name:' in win.plot_filter_label.text()


def test_surface_single_inflight_and_latest_pending(qapp,monkeypatch):
    from apps.workbench_qt.mdo_studio.views import response_surface_view as module
    pool=SimpleNamespace(tasks=[],start=lambda task:pool.tasks.append(task))
    monkeypatch.setattr(module.QThreadPool,'globalInstance',lambda:pool)
    widget=module.ResponseSurfaceWidget(); space=ServiceSpy().study.space
    artifact=SimpleNamespace(dataset=SimpleNamespace(response_ids=('response',)))
    widget.set_study(artifact,space,[],{}); widget.timer.stop(); widget._dispatch()
    assert len(pool.tasks)==1
    for _ in range(5): widget._token+=1; widget._dispatch()
    assert len(pool.tasks)==1 and pool.tasks[0].cancel.is_set()
    latest=widget._token; widget._receive(pool.tasks[0].token,None,'cancelled')
    assert len(pool.tasks)==2 and pool.tasks[1].token==latest
    widget.close()


def test_train_editor_and_revision_resource_sensitivity_integration(native_service,qapp,monkeypatch):
    service=native_service; measured(service,np.linspace(1,3.2,12)); service.workbench.jobs.close=lambda:True
    win=MDOStudioWindow(service=service)
    def configure_dialog(dialog):
        dialog.method.setCurrentText('prs'); dialog.seed.setValue(17); dialog.validation_seed.setValue(9); dialog.fraction.setValue(.25)
        for row in range(dialog.responses.rowCount()): dialog.responses.item(row,3).setText('1e-5')
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(SurrogateTrainingDialog,'exec',configure_dialog)
    win._train(); deadline=time.monotonic()+10
    while (win._busy or not win.progress_bar.isHidden()) and time.monotonic()<deadline: qapp.processEvents(); time.sleep(.01)
    assert service.training_report['split_policy']['seed']==9 and service.training_report['qualification_limits']==[1e-5]*3
    win.viz_tabs.setCurrentIndex(3); win._update_visualizations()
    from caelab.mdo.sensitivity import SensitivityConfig
    win.tornado_view.config=SensitivityConfig(method='sobol',base_samples=8,resamples=20,max_evaluations=1000)
    win.search.setText('Point 1'); win.tornado_view.method_selector.setCurrentIndex(1)
    next(button for button in win.tornado_view.findChildren(QPushButton) if button.text()=='Analyze').click()
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        qapp.processEvents()
        if win.tornado_view.report and win.tornado_view.report['method']=='sobol': break
        time.sleep(.01)
    report=win.tornado_view.report
    assert report['source_mode']=='SURROGATE' and report['native_calls']==0 and report['sample_count']==24
    assert len(report['paired_rows'])==24 and report['point_ids']==[]
    assert 'visual filter is not applied' in win.tornado_view.note.text()
    win.tornado_view.experiment.setCurrentIndex(win.tornado_view.experiment.findData(report['experiment_id']))
    win.search.setText('Point 2'); win._update_visualizations()
    deadline=time.monotonic()+5
    while win._sensitivity_running and time.monotonic()<deadline: qapp.processEvents(); time.sleep(.01)
    assert win.tornado_view.report['experiment_id']==report['experiment_id']
    old_revision=service.revision
    from dataclasses import replace
    space=VariableSpace(variables=(replace(service.study.space.variables[0],unit='cm'),))
    win._configure_study(space,service.study.objectives,service.study.constraints,service.response_units)
    assert win.tdp_proxy.rowCount()==0
    win.revision_selector.setCurrentIndex(win.revision_selector.findData(old_revision)); win.search.clear()
    assert win.tdp_proxy.rowCount()==12 and win.tdp_model.units['width']=='mm'
    assert not win.act_run.isEnabled() and not win.act_confirm.isEnabled() and not win.setup_widget.btn_confirm.isEnabled()
    win.select_point(service.study.records[0].point_id)
    def accept_current_values(dialog):
        from PySide6.QtWidgets import QDoubleSpinBox
        values=dialog.findChildren(QDoubleSpinBox)
        assert values and 'Clone into current revision' in dialog.windowTitle()
        values[0].setValue(2.5)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog,'exec',accept_current_values)
    win._clone_selected(); deadline=time.monotonic()+5
    while win._busy and time.monotonic()<deadline: qapp.processEvents(); time.sleep(.01)
    assert win._display_revision==service.revision and service.study.records[-1].physical_values['width']==2.5
    assert service.study.records[-1].source_point_id==service.study.records[0].point_id
    win._busy=None; win.close(); qapp.processEvents()


def test_saved_selection_after_layout_and_narrow_preserves_manual_viewport(qapp):
    from pathlib import Path
    from PySide6.QtGui import QFontDatabase
    # Windows' offscreen plugin has an empty font database; load installed fonts for geometry.
    if not QFontDatabase.families():
        for name in ('segoeui.ttf','malgun.ttf'):
            path=Path('C:/Windows/Fonts')/name
            if path.is_file(): QFontDatabase.addApplicationFont(str(path))
    service=ServiceSpy()
    for i in range(300): service.study.add_point(dict(x=2.,y=2.),name=f'Candidate {i}')
    last=service.study.records[-1]; service.view_state=dict(selected_ids=[last.point_id],tab=0)
    window=MDOStudioWindow(service=service); window.resize(1440,960); window.show()
    def settle():
        end=time.monotonic()+.12
        while time.monotonic()<end: qapp.processEvents(); time.sleep(.005)
    settle(); window.resize(960,720); settle()
    selected=window.tdp_table.selectionModel().selectedRows()[0]
    assert window.width()==960 and window.tdp_table.viewport().rect().intersects(window.tdp_table.visualRect(selected))
    window.tdp_table.scrollToTop(); settle(); before=window.tdp_table.verticalScrollBar().value()
    window._pending_records[last.point_id]=last; window._flush_deltas(); settle()
    assert window.tdp_table.verticalScrollBar().value()==before
    assert not window.tdp_table.viewport().rect().intersects(window.tdp_table.visualRect(window.tdp_table.selectionModel().selectedRows()[0]))
    window.close(); qapp.processEvents()


def test_readonly_setup_commands_stay_disabled_after_restore(qapp):
    service=ServiceSpy(); service.read_only=True; service.view_state=dict(selected_ids=[service.study.records[0].point_id])
    window=MDOStudioWindow(service=service,read_only=True)
    assert not window.act_confirm.isEnabled() and not window.setup_widget.btn_confirm.isEnabled()
    assert not window.setup_widget.btn_apply_current.isEnabled() and not window.setup_widget.btn_result.isEnabled()
    window._input_context()
    for button in window.setup_widget.context_tabs.widget(1).findChildren(QPushButton):
        if button.text() in ('Open Native Input…','Create Scalar Token…'): assert not button.isEnabled()
    window.close(); qapp.processEvents()


def test_configure_clears_old_selection_and_hidden_history_keeps_frozen_units(native_service,qapp):
    from dataclasses import replace
    service=native_service; (record,)=measured(service,(2.,)); service.workbench.jobs.close=lambda:True
    window=MDOStudioWindow(service=service); window.select_point(record.point_id)
    assert window.setup_widget.param_layout.itemAt(0,QFormLayout.ItemRole.FieldRole).widget().text()=='2 mm'
    space=VariableSpace(variables=(replace(service.study.space.variables[0],unit='cm'),))
    window._configure_study(space,service.study.objectives,service.study.constraints,service.response_units)
    assert window._selected_ids==set() and window.setup_widget._current_record is None
    assert window.setup_widget.param_layout.rowCount()==0
    # Defensive inspection also keeps units correct if a hidden old ID is selected programmatically.
    window.select_point(record.point_id)
    assert window._hidden_selection and window.setup_widget._current_record.revision!=service.revision
    assert window.setup_widget.param_layout.itemAt(0,QFormLayout.ItemRole.FieldRole).widget().text()=='2 mm'
    assert not window.act_run.isEnabled() and not window.setup_widget.btn_confirm.isEnabled()
    window.close(); qapp.processEvents()


def test_sensitivity_writer_lifecycle_and_selector_generation(win,qapp,monkeypatch):
    from threading import Event
    started,finish=Event(),Event(); closed=[]
    monkeypatch.setattr(win.service,'close',lambda:closed.append(True) or True)
    win._on_configure(); editor=win._editor; editor.variables.item(0,5).setText('Preserved draft')
    def analyze():
        started.set()
        assert finish.wait(5)
        return dict(method='spearman',response_id='mass',unit='kg',variable_ids=[],estimates={},source_mode='ACTUAL',sample_count=0,revision='1',native_calls=0)
    win._sensitivity_job=1
    win._queue_sensitivity((win._generation,'Sensitivity 1',analyze))
    assert started.wait(1) and not win.act_new.isEnabled() and not win.act_open.isEnabled()
    old=win.service; win._on_new_study(); win._on_open_study('not-opened.json')
    assert win.service is old and not closed and win._editor is editor
    assert win.close() is False and not closed
    # Changing selectors invalidates the old result but keeps ownership until that task finishes.
    win.tornado_view.method_selector.setCurrentIndex(2)
    assert win._sensitivity_running and win.tornado_view.report is None
    finish.set(); deadline=time.monotonic()+5
    while win._sensitivity_running and time.monotonic()<deadline: qapp.processEvents(); time.sleep(.01)
    assert not win._sensitivity_running and win.tornado_view.method_selector.currentData()=='morris'
    assert win.tornado_view.report is None and win.act_new.isEnabled() and win.act_open.isEnabled()
    assert editor.variables.item(0,5).text()=='Preserved draft'


def test_open_candidate_is_closed_when_current_service_cannot_close(win,monkeypatch):
    closed=[]; original=win.service
    previous_close=original.close
    candidate=SimpleNamespace(close=lambda:closed.append(True) or True)
    monkeypatch.setattr(original,'close',lambda:False)
    win._task_finished(win._generation,'Open study',candidate,None)
    assert win.service is original and closed==[True]
    monkeypatch.setattr(original,'close',previous_close)


@pytest.mark.parametrize('floating,visible',[(False,False),(False,True),(True,False),(True,True)])
def test_async_open_study_restores_saved_view_after_editor_creation(win,qapp,monkeypatch,floating,visible):
    from pathlib import Path
    win.show(); win._default_layout(); win.setup_dock.setFloating(floating); win.setup_dock.setVisible(visible)
    win.center_splitter.setSizes([230,410]); qapp.processEvents()
    candidate=ServiceSpy(); candidate.workbench=win.workbench; candidate.input_path=Path('C:/new-study/model.inp')
    selected=candidate.study.records[1].point_id
    candidate.view_state=win._view_state(); candidate.view_state.update(selected_ids=[selected],tab=1)
    saved_splitter=win.center_splitter.sizes()
    win.setup_dock.setFloating(not floating); win.setup_dock.show(); win.center_splitter.setSizes([410,230])
    win.service.input_path=Path('C:/old-study/model.inp'); win._input_context(); win.select_point(win.study.records[0].point_id)
    monkeypatch.setattr(win.workbench,'open_mdo_study',lambda *args,**kwargs:candidate)
    win._on_open_study('new-study.json')
    deadline=time.monotonic()+5
    while win._busy and time.monotonic()<deadline: qapp.processEvents(); time.sleep(.01)
    for _ in range(5): qapp.processEvents(); time.sleep(.01)
    assert win.service is candidate and not win._busy
    assert win.setup_dock.isFloating()==floating and win.setup_dock.isHidden()==(not visible)
    assert win._selected_ids=={selected} and win.viz_tabs.currentIndex()==1
    assert win.tdp_table.selectionModel().selectedRows()[0].data(DesignPointsTableModel.PointIdRole)==selected
    assert win.center_splitter.sizes()==pytest.approx(saved_splitter,abs=2)
    editor_text=' '.join(label.text() for label in win.setup_widget.context_tabs.widget(1).findChildren(QLabel))
    assert 'new-study' in editor_text and 'old-study' not in editor_text
    assert win.status_bar.currentMessage().startswith('Open study completed.')


@pytest.mark.parametrize('operation',('Open input','Open study'))
def test_async_open_without_saved_view_keeps_layout_and_shows_input_guidance(win,qapp,monkeypatch,operation):
    candidate=ServiceSpy(); candidate.workbench=win.workbench
    win.show(); win.setup_dock.setFloating(True); win.setup_dock.hide(); win.tree_dock.hide()
    win.center_splitter.setSizes([240,400]); qapp.processEvents(); splitter=win.center_splitter.sizes(); position=win.setup_dock.pos()
    if operation=='Open study':
        monkeypatch.setattr(win.workbench,'open_mdo_study',lambda *args,**kwargs:candidate)
        win._on_open_study('no-saved-layout.json')
    else: win._background(operation,lambda:candidate)
    deadline=time.monotonic()+5
    while win._busy and time.monotonic()<deadline: qapp.processEvents(); time.sleep(.01)
    qapp.processEvents()
    assert win.service is candidate and not win.setup_dock.isHidden()
    assert win.setup_dock.isFloating() and win.tree_dock.isHidden()
    assert win.setup_dock.pos()==position
    assert win.center_splitter.sizes()==pytest.approx(splitter,abs=2)
    assert win.setup_widget.context_tabs.tabText(1)=='Input'
    assert win.status_bar.currentMessage().startswith(operation+' completed.')


@pytest.mark.parametrize('failure',('open_error','close_busy'))
def test_async_open_failure_retains_service_draft_layout_and_selection(win,qapp,monkeypatch,failure):
    win.show(); win._on_configure(); editor=win._editor; editor.variables.item(0,5).setText('Retained draft')
    win.setup_dock.setFloating(True); win.setup_dock.hide(); win.viz_tabs.setCurrentIndex(1)
    selected=win.study.records[1].point_id; win.select_point(selected)
    original=win.service; original_close=original.close; candidate=ServiceSpy(); closed=[]
    candidate.close=lambda:closed.append(True) or True
    if failure=='open_error':
        def fail(*args,**kwargs): raise ValueError('Invalid study document')
        monkeypatch.setattr(win.workbench,'open_mdo_study',fail)
    else:
        monkeypatch.setattr(win.workbench,'open_mdo_study',lambda *args,**kwargs:candidate)
        monkeypatch.setattr(original,'close',lambda:False)
    win._on_open_study('invalid-or-busy.json')
    deadline=time.monotonic()+5
    while win._busy and time.monotonic()<deadline: qapp.processEvents(); time.sleep(.01)
    for _ in range(3): qapp.processEvents()
    assert win.service is original and win._editor is editor
    assert editor.variables.item(0,5).text()=='Retained draft'
    assert win.setup_dock.isFloating() and win.setup_dock.isHidden()
    assert win._selected_ids=={selected} and win.viz_tabs.currentIndex()==1
    assert 'completed' not in win.status_bar.currentMessage()
    assert ('failed' if failure=='open_error' else 'Retry opening') in win.status_bar.currentMessage()
    assert closed==([True] if failure=='close_busy' else [])
    monkeypatch.setattr(original,'close',original_close)


def test_queued_ten_hz_results_keep_flush_cadence_and_stop_after_final_flush(win,qapp,monkeypatch):
    from copy import deepcopy
    from threading import Thread,Event,get_ident
    service=win.service; service.active_batch_id='cadence'
    for i in range(3,12):
        record=service.study.add_point(dict(x=i+1.,y=2.)); record.status='COMPLETED'; record.observations=dict(mass=0.,disp=0.)
    for record in service.study.records:
        record.study_id=service.study_id; record.revision=service.revision; record.batch_id='cadence'
    # Isolate the scheduling boundary. Native/10k probes retain the real plot work.
    monkeypatch.setattr(win,'_update_visualizations',lambda:None)
    win._viz_timer.timeout.disconnect(); win._viz_timer.timeout.connect(lambda:None)
    win._populate_study(); qapp.processEvents()
    selected=service.study.records[1].point_id; win.select_point(selected)
    original_item=win._on_item_finished; original_flush=win._flush_deltas; original_finish=win._on_batch_finished
    received=[]; timer_ids=[]; applied=[]; flushes=[]; errors=[]
    source_finished=Event(); terminal=Event(); worker_done=Event(); stop=Event(); owner=get_ident()
    def receive(point_id,record,run):
        original_item(point_id,record,run)
        accepted=win._pending_records.get(point_id) is record
        received.append((point_id,accepted,get_ident()))
        if accepted: timer_ids.append(win._delta_timer.timerId())
    def flush():
        pending=list(win._pending_records.values()); original_flush()
        if pending:
            applied.extend((record.point_id,record.observations['mass']) for record in pending)
            flushes.append((time.monotonic(),source_finished.is_set()))
    def finish(*args):
        original_finish(*args); service.active_batch_id=None; win._sync_actions(); terminal.set()
    win.bridge.itemFinished.disconnect(); win.bridge.itemFinished.connect(receive,Qt.ConnectionType.QueuedConnection)
    win._delta_timer.timeout.disconnect(); win._delta_timer.timeout.connect(flush)
    # The production final-flush method dispatches self._flush_deltas directly.
    monkeypatch.setattr(win,'_flush_deltas',flush)
    win.bridge.batchFinished.disconnect(); win.bridge.batchFinished.connect(finish,Qt.ConnectionType.QueuedConnection)
    callbacks=win._callbacks(); expected={}
    def source():
        try:
            for i,record in enumerate(service.study.records):
                if stop.is_set(): return
                record.observations['mass']=100.+i; snapshot=deepcopy(record); expected[record.point_id]=100.+i
                callbacks['on_item'](record.point_id,snapshot,None)
                if i<11 and stop.wait(.1): return
            for field,value in (('study_id','stale'),('revision','stale'),('batch_id','stale')):
                stale=deepcopy(service.study.records[0]); setattr(stale,field,value); stale.observations['mass']=999.
                callbacks['on_item'](stale.point_id,stale,None)
            source_finished.set(); callbacks['on_finished']('cadence','COMPLETED',12,0)
            if not terminal.wait(3): raise RuntimeError('Queued batch completion was not delivered')
            late=deepcopy(service.study.records[0]); late.observations['mass']=999.
            callbacks['on_item'](late.point_id,late,None)
        except Exception as error: errors.append(str(error))
        finally: worker_done.set()
    worker=Thread(target=source,daemon=True); worker.start()
    try:
        deadline=time.monotonic()+5
        while (not worker_done.is_set() or not terminal.is_set()) and time.monotonic()<deadline:
            qapp.processEvents(); time.sleep(.002)
        for _ in range(3): qapp.processEvents(); time.sleep(.01)
        assert worker_done.is_set() and terminal.is_set() and not errors
        assert win._delta_timer.interval()==100 and not win._delta_timer.isSingleShot()
        assert len(timer_ids)==12 and len(set(timer_ids))==1 and timer_ids[0]>=0
        assert sum(not after_source for _,after_source in flushes)>=3
        assert len(applied)==12 and len({point_id for point_id,_ in applied})==12
        assert dict(applied)==expected
        assert all(thread==owner for _,_,thread in received)
        assert sum(accepted for _,accepted,_ in received)==12 and len(received)==16
        assert not win._delta_timer.isActive() and not win._pending_records
        assert win._selected_ids=={selected}
        assert {record.point_id:record.observations['mass'] for record in win.tdp_model.records}==expected
        assert not service.calls
    finally:
        stop.set(); terminal.set(); worker.join(1); service.active_batch_id=None; win._delta_timer.stop()
