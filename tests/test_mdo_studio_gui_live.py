"""Async Studio/Workbench integration using a mocked subprocess, not native FEA."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import sys
import time
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication,QMessageBox
from apps.workbench_qt.mdo_studio.window import MDOStudioWindow,extract_calculix_responses
from apps.workbench_qt.mdo_studio.study_config import StudyConfigDialog
from caelab.connections import ProgramSpec,ProgramStatus,LaunchRequest
from caelab.mdo.variables import ContinuousVariable,VariableSpace
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.constraints import ConstraintSpec
from test_native_results import _geometry,_field

STRESS=[(name,4,row,col,0) for name,row,col in (('SXX',1,1),('SYY',2,2),('SZZ',3,3),('SXY',1,2),('SYZ',2,3),('SZX',3,1))]

def result_text(disp=.01,stress=25.,*,step=1,increment=1,value=1.):
    ids=(40,10,90,25)
    return (_field([(n,(disp,0.,0.)) for n in ids],step=step,increment=increment,value=value)
        +_field([(n,(stress,0.,0.,0.,0.,0.)) for n in ids],name='STRESS',components=STRESS,step=step,increment=increment,value=value))

@pytest.fixture(scope='session')
def qapp(): return QApplication.instance() or QApplication([])

@pytest.fixture(autouse=True)
def messages(monkeypatch,qapp):
    seen=[]
    for method in ('information','warning'): monkeypatch.setattr(QMessageBox,method,lambda parent,title,text,*args:seen.append((title,text)))
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Apply)
    yield seen
    qapp.processEvents()

def wait_idle(win,qapp):
    deadline=time.monotonic()+15
    while (win._busy or win._active_batch_id or not win.progress_bar.isHidden()) and time.monotonic()<deadline:
        qapp.processEvents(); time.sleep(.01)
    qapp.processEvents()
    assert win._busy is None and win._active_batch_id is None

wait_batch=wait_idle

@pytest.fixture
def mock_solver_probe(monkeypatch):
    import caelab.connections as connections
    from caelab.workbench import Workbench
    frd=_geometry()+result_text()+' 9999\n'
    dat=' total mass for set EALL and time 0.1000000E+01\n\n 3.140000E-05\n'
    script=[f"from pathlib import Path; Path('model.frd').write_text({frd!r}); Path('model.dat').write_text({dat!r}); Path('model.sta').write_text('1 1 1 1 1.0 1.0 1.0\\n'); print('MOCK subprocess fixture completed')"]
    spec=ProgramSpec('calculix','Mock CalculiX subprocess','solver','CAELAB_CCX',('ccx',),'test fixture')
    monkeypatch.setattr(Workbench,'programs',lambda self:[ProgramStatus(spec,sys.executable,source='test')])
    monkeypatch.setattr(connections,'calculix_run_request',lambda status,workdir:LaunchRequest([sys.executable,'-c',script[0]],workdir,dict(os.environ),'calculix'))
    return script

@pytest.fixture
def window(qapp,tmp_path):
    win=MDOStudioWindow(); deck=tmp_path/'plate.inp'
    deck.write_text('*HEADING\nExplicit mock-pipeline input\n*NODE\n1, 0, 0, 0\n*SOLID SECTION, ELSET=EALL, MATERIAL=STEEL\n<T>\n*STEP\n*STATIC\n*END STEP\n',encoding='utf-8')
    win._on_load_deck(deck); wait_idle(win,qapp)
    space=VariableSpace(variables=(ContinuousVariable(id='t',name='T',lower=1,upper=3,initial=2,unit='mm'),))
    win._configure_study(space,[ObjectiveSpec(id='mass',response_id='mass_eall'),ObjectiveSpec(id='displacement',response_id='disp')],
        [ConstraintSpec(id='stress_limit',response_id='stress',kind='UPPER',limit=40,unit='MPa')],{'mass_eall':'N*s**2/mm','disp':'mm','stress':'MPa'})
    win._on_generate_doe(3,42); wait_idle(win,qapp)
    yield win
    wait_idle(win,qapp); win.close(); qapp.processEvents()

def test_default_empty_and_no_implicit_execution(qapp,messages):
    win=MDOStudioWindow()
    try:
        assert not win.study.records and win.viz_tabs.count()==5
        win._run_all_points(); wait_idle(win,qapp)
        assert win._active_batch_id is None and not win.study.records
        assert messages
    finally: win.close()

def test_async_managed_dispatch_and_actual_responses(window,qapp,mock_solver_probe):
    window._run_all_points()
    assert window._busy and not window.act_run.isEnabled()
    wait_idle(window,qapp)
    assert window.act_run.isEnabled() and window.progress_bar.isHidden()
    assert len(window.study.records)==3
    for rec in window.study.records:
        assert rec.status=='COMPLETED'
        assert rec.observations==pytest.approx({'mass_eall':3.14e-5,'stress':25.,'disp':.01})
        assert rec.objectives['mass']==pytest.approx(3.14e-5) and rec.duration_sec>0
        assert window._point_is_verified(rec) and rec.attempts

def test_cancel_active_campaign_only(window,qapp,mock_solver_probe):
    mock_solver_probe[0]="import time; print('MOCK slow subprocess',flush=True); time.sleep(30)"
    window._run_all_points(); deadline=time.monotonic()+5
    while (not window._active_batch_id or window._busy) and time.monotonic()<deadline: qapp.processEvents(); time.sleep(.01)
    assert window.act_cancel.isEnabled()
    original=window.study; window._on_new_study(); assert window.study is original
    window._stop_evaluations(); wait_idle(window,qapp)
    assert all(r.status in ('CANCELLED','FAILED') for r in window.study.records)
    assert all(r.feasibility=='NOT_ASSESSED' and not r.observations for r in window.study.records)

def test_two_apply_backups_undo_and_readonly_reopen(window,qapp,mock_solver_probe,tmp_path):
    original=window.current_deck_path.read_bytes(); window._run_all_points(); wait_idle(window,qapp)
    first,second=window.study.records[:2]
    window._on_apply_as_current(first.point_id); wait_idle(window,qapp)
    assert window.current_deck_path.with_name('plate.inp.bak').read_bytes()==original
    first_bytes=window.current_deck_path.read_bytes(); assert b'<T>' not in first_bytes
    window._on_apply_as_current(second.point_id); wait_idle(window,qapp)
    assert window.current_deck_path.with_name('plate.inp.bak').read_bytes()==first_bytes
    assert any(p.read_bytes()==original for p in tmp_path.glob('plate.inp.*.previous.bak'))
    window._undo(); wait_idle(window,qapp); assert window.current_deck_path.read_bytes()==first_bytes
    saved=tmp_path/'study.json'; window.select_point(first.point_id); window._on_save_study(saved); wait_idle(window,qapp)
    other=MDOStudioWindow()
    try:
        other._on_open_study(saved,read_only=True); wait_idle(other,qapp)
        assert len(other.study.records)==3 and other._active_batch_id is None
        assert other._point_is_verified(other.study.records[0]) and other._selected_ids=={first.point_id}
    finally: other.close()

def test_changed_output_blocks_apply_without_backup(window,qapp,mock_solver_probe,messages):
    window._run_all_points(); wait_idle(window,qapp); rec=window.study.records[0]
    run=Path(window.service.point_evidence(rec.point_id)['workdir']); (run/'model.frd').write_text('changed output')
    original=window.current_deck_path.read_bytes(); window._on_apply_as_current(rec.point_id); wait_idle(window,qapp)
    assert window.current_deck_path.read_bytes()==original and not window.current_deck_path.with_name('plate.inp.bak').exists()
    assert 'failed' in window.status_bar.currentMessage().lower() and messages

def test_new_study_clears_native_binding(window):
    original=window.current_deck_path; before=original.read_bytes(); window._on_new_study()
    assert window.current_deck_path is None and window.template_deck is None and not window.study.records
    assert original.read_bytes()==before

def test_missing_responses_remain_failed(window,qapp,mock_solver_probe):
    mock_solver_probe[0]="print('MOCK completed without outputs')"; window._run_all_points(); wait_idle(window,qapp)
    assert all(r.status=='FAILED' and r.feasibility=='NOT_ASSESSED' and not r.is_pareto for r in window.study.records)
    assert not window.setup_widget.btn_apply_current.isEnabled()

def test_max_objective_uses_declared_response(window,qapp,mock_solver_probe):
    window._configure_study(window.study.space,[ObjectiveSpec(id='maximize_motion',response_id='disp',sense='MAX')],[],{'disp':'mm'})
    window._on_generate_doe(2,17); wait_idle(window,qapp); window._run_all_points(); wait_idle(window,qapp)
    current=[r for r in window.study.records if r.revision==window.service.revision]
    assert all(r.objectives=={'maximize_motion':-.01} and r.is_pareto for r in current)

def test_parser_final_frame_and_zero_are_retained(tmp_path):
    (tmp_path/'model.frd').write_text(_geometry()+result_text(10.,100.,increment=1,value=.5)+result_text(0.,0.,increment=2,value=1.)+' 9999\n')
    assert extract_calculix_responses(tmp_path,None)=={'disp':0.,'stress':0.}
    (tmp_path/'model.frd').write_text(_geometry()+result_text()+_field([(n,(0.,0.,0.)) for n in (40,10,90,25)],increment=2,value=2.)+' 9999\n')
    assert extract_calculix_responses(tmp_path,None)=={'disp':0.}

def test_configuration_requires_unit_inline(window):
    dialog=StudyConfigDialog(('T',),window.study,window.response_units,window); dialog.variables.item(0,4).setText('')
    with pytest.raises(ValueError,match='unit'): dialog.configuration()
    dialog.accept(); assert dialog.editor.error.text(); dialog.close()

def test_nested_include_snapshot_and_source_edit_invalidate(window,qapp,mock_solver_probe,tmp_path):
    include=tmp_path/'mesh.inc'; include.write_text('*NODE\n1,0,0,0\n'); deck=tmp_path/'nested.inp'
    deck.write_text('*INCLUDE,INPUT=mesh.inc\n*SOLID SECTION,ELSET=EALL,MATERIAL=M\n<T>\n*STEP\n*STATIC\n*END STEP\n')
    space,objectives,constraints,units=window.study.space,window.study.objectives,window.study.constraints,window.response_units
    window._on_load_deck(deck); wait_idle(window,qapp); window._configure_study(space,objectives,constraints,units)
    window._on_generate_doe(2,42); wait_idle(window,qapp); window._run_all_points(); wait_idle(window,qapp)
    rec=window.study.records[0]; assert window._point_is_verified(rec)
    run=Path(window.service.point_evidence(rec.point_id)['workdir']); execution_include=next((run/'includes').glob('*.inp'))
    original=execution_include.read_bytes(); execution_include.write_text('*NODE\n1,999,0,0\n'); assert not window._point_is_verified(rec)
    execution_include.write_bytes(original); assert window._point_is_verified(rec)
    include.write_text('*NODE\n1,1,0,0\n'); assert not window._point_is_verified(rec)

def test_apply_write_failure_does_not_report_success(window,qapp,mock_solver_probe,monkeypatch,messages):
    from caelab.mdo import apply_transaction
    window._run_all_points(); wait_idle(window,qapp); original=window.current_deck_path.read_bytes()
    monkeypatch.setattr(apply_transaction,'write_staged',lambda *args:(_ for _ in ()).throw(OSError('write denied')))
    window._on_apply_as_current(window.study.records[0].point_id); wait_idle(window,qapp)
    assert window.current_deck_path.read_bytes()==original and 'failed' in window.status_bar.currentMessage().lower()
    assert 'write denied' in messages[-1][1]

@pytest.mark.parametrize('invalid_field',['point_sources','record_error'])
def test_malformed_saved_open_preserves_live_state(window,qapp,tmp_path,invalid_field):
    path=tmp_path/'invalid.json'; window._on_save_study(path); wait_idle(window,qapp)
    old=window.service; before=(window.study,window.current_deck_path,window.template_deck,window._configured)
    data=json.loads(path.read_text(encoding='utf-8'))
    if invalid_field=='point_sources': data['point_sources']=1
    else: data['records'][0]['error_message']={'invalid':'object'}
    path.write_text(json.dumps(data),encoding='utf-8'); window._on_open_study(path,read_only=True); wait_idle(window,qapp)
    assert window.service is old and (window.study,window.current_deck_path,window.template_deck,window._configured)==before
