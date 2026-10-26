"""Independent discussion UI with real saved assets and mock model responses."""
import copy
import json
import os
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import Qt, QSettings, QPoint
from PySide6.QtWidgets import QApplication, QMessageBox

from apps.workbench_qt.expert_window import ExpertWindow
from apps.workbench_qt.project_window import ProjectWindow
from caelab.assist import experts
from caelab.workbench import Workbench


@pytest.fixture(autouse=True)
def isolated_view_preferences(tmp_path, monkeypatch):
    from apps.workbench_qt.theme import set_theme_mode
    monkeypatch.setenv('CAELAB_UI_SETTINGS_PATH', str(tmp_path / 'ui.ini'))
    set_theme_mode('dark')
    previous = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / 'view-preferences'))
    try:
        yield
    finally:
        set_theme_mode('dark')
        QSettings.setDefaultFormat(previous)


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


class Provider(experts.ExpertProvider):
    id, name = 'mock', '모의 공급자'

    def __init__(self):
        self.prompts = []

    def availability(self):
        return True, '모의 설정'

    def discuss(self, prompt):
        self.prompts.append(prompt)
        return '원문에 없는 수치는 제시하지 않습니다.'


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Discard)
    provider = Provider()
    monkeypatch.setattr(experts, 'get_provider', lambda *args: provider)
    instance = ExpertWindow(tmp_path / 'discussion.json')
    instance.role.setCurrentIndex(instance.role.findData('interpretation'))
    yield instance, provider
    instance.close()


def attach(window, tmp_path, text, name='input.inp'):
    source = tmp_path / name
    source.write_text(text, encoding='utf-8')
    evidence = window.workbench.import_expert_files(window.assets, files={'input': source}, solver_id='calculix')
    window.document = window.workbench.attach_expert_evidence(
        window.path, evidence, expected_revision=window.document['revision'])
    window.render()
    return evidence


def finish(window, app):
    deadline = time.monotonic() + 5
    while window._operation and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert window._operation is None


def test_preview_is_not_question_basis_and_reopen_never_calls(window, app, tmp_path):
    ui, provider = window
    first = attach(ui, tmp_path, '** condition A')
    second = attach(ui, tmp_path, '** condition B')
    ui.sources.setCurrentRow(0)
    assert ui.document['selected_evidence_id'] == second['id']
    assert ui.sources.currentItem().data(Qt.UserRole) == first['id']
    assert 'input.inp' in ui.attachment_preview.toPlainText()
    assert 'condition A' in ui.document['evidences'][first['id']]['model_text']
    ui.select_evidence()
    assert ui.document['selected_evidence_id'] == first['id']
    ui.question.setPlainText('첫 번째 조건을 확인해 주세요.')
    ui.ask()
    assert ui.role.isEnabled() is False and ui.sources.isEnabled()
    finish(ui, app)
    assert len(provider.prompts) == 1
    assert ui.document['turns'][0]['evidence_id'] == first['id']
    assert ui.document['turns'][0]['status'] == 'completed'
    assert ui.question.toPlainText() == ''
    original_turn = copy.deepcopy(ui.document['turns'][0])
    ui.sources.setCurrentRow(1)
    ui.select_evidence()
    assert ui.document['turns'][0] == original_turn
    assert ui.document['selected_evidence_id'] == second['id']
    reopened = ExpertWindow(ui.path)
    assert len(provider.prompts) == 1
    assert reopened.document['turns'][0] == original_turn
    assert reopened.document['selected_evidence_id'] == second['id']
    assert reopened.document['turns'][0]['evidence_id'] == first['id']
    assert first['id'] not in reopened.transcript.toPlainText()
    assert not hasattr(reopened, 'request_details')
    reopened.close()
    ui.reload_document()


def test_saved_asset_tampering_fails_before_model_and_keeps_draft(window, app, tmp_path):
    ui, provider = window
    evidence = attach(ui, tmp_path, '** condition A')
    (ui.assets / evidence['files'][0]['stored_path']).write_text('changed', encoding='utf-8')
    ui.question.setPlainText('자료를 확인해 주세요.')
    ui.ask()
    finish(ui, app)
    assert provider.prompts == []
    assert ui.document['turns'][-1]['status'] == 'failed'
    assert '무결성' in ui.transcript.toPlainText()
    assert ui.question.toPlainText() == '자료를 확인해 주세요.'
    assert ui.role.isEnabled() and ui.ask_button.isEnabled()


def test_cancel_releases_ui_and_discards_late_model_result(window, app, tmp_path):
    ui, provider = window
    attach(ui, tmp_path, '** condition A')
    started, release = threading.Event(), threading.Event()
    def slow(prompt):
        started.set()
        release.wait(5)
        return '늦은 응답'
    provider.discuss = slow
    ui.question.setPlainText('질문 초안')
    ui.ask()
    assert started.wait(2)
    ui.cancel_request()
    ui.render()
    assert not ui.stop.isEnabled()
    assert '중단' in ui.transcript.toPlainText()
    finish(ui, app)
    assert ui.document['turns'][-1]['status'] == 'cancelled'
    assert ui.question.toPlainText() == '질문 초안'
    assert ui.role.isEnabled()
    saved = ui.path.read_bytes()
    release.set()
    time.sleep(.08)
    app.processEvents()
    assert ui.path.read_bytes() == saved


def test_external_revision_conflict_preserves_draft_and_can_reload(window):
    ui, provider = window
    other = ui.workbench.open_expert_discussion(ui.path)
    other['title'] = '다른 창에서 바뀐 제목'
    ui.workbench.save_expert_discussion(ui.path, other, expected_revision=other['revision'])
    ui.question.setPlainText('저장되지 않은 질문')
    assert not ui.save_document()
    assert '다른 창' in ui.status.text()
    ui.reload_document()
    assert ui.document['title'] == other['title']
    assert ui.question.toPlainText() == '저장되지 않은 질문'
    assert provider.prompts == []


def test_unconfigured_provider_keeps_files_readable_and_explains_setup(app, tmp_path, monkeypatch):
    monkeypatch.setenv('CAELAB_EXPERT_PROVIDER', 'none')
    ui = ExpertWindow(tmp_path / 'discussion.json')
    attach(ui, tmp_path, '** input only')
    assert not ui.ask_button.isEnabled()
    assert ui.settings.isEnabled() and ui.recheck.isEnabled()
    assert '공급자' in ui.provider.text()
    assert 'input.inp' in ui.attachment_preview.toPlainText()
    assert 'input only' in next(iter(ui.document['evidences'].values()))['model_text']
    assert not ui.provider.isVisible()
    ui.close()


def test_parent_opens_independent_expert_without_model_or_input(app, tmp_path, monkeypatch):
    workbench = Workbench()
    requests = []
    monkeypatch.setattr(workbench.jobs, 'launch', lambda request: requests.append(request) or
                        SimpleNamespace(state='opened', pid=4321, error=''))
    monkeypatch.setattr(experts.ExpertProvider, 'discuss', lambda *args: pytest.fail('model called'))
    project = workbench.new_native_project('사용자 연구')
    node = project.add_node('expert', 'expert')
    workbench.save_native_project(project, tmp_path / 'project.json')
    record = workbench.open_native_expert(project, node.id)
    request = requests[0]
    assert record.pid == 4321
    assert request.argv[1:3] == ['-m', 'apps.workbench_qt.expert_window']
    assert request.argv[request.argv.index('--node') + 1] == node.id
    path = Path(request.argv[request.argv.index('--discussion') + 1])
    assert path.is_relative_to(project.run_store)
    assert not path.exists()  # Launching does not fabricate a completed discussion.
    workbench.close()


def test_launch_pins_the_run_even_when_selection_changes(tmp_path, monkeypatch):
    from test_result_delivery import selected_project
    from caelab.native.run_store import RunStore
    workbench, project, analysis, _, expert, run = selected_project(tmp_path)
    expert.kind = 'expert'
    requests = []
    monkeypatch.setattr(workbench.jobs, 'launch', lambda request: requests.append(request))
    workbench.open_native_expert(project, expert.id)
    command = requests[0].argv
    pinned_id = command[command.index('--run-id') + 1]
    # A separate run becomes the current selection after the launcher has opened.
    later = copy.deepcopy(run)
    later.id = 'b' * 32
    later.workdir = project.run_store / later.id
    shutil.copytree(run.workdir, later.workdir)
    RunStore(project.run_store).write(later)
    workbench.select_native_run(project, analysis.id, later.id)
    assert workbench.expert_evidence_run(project, expert.id, pinned_id).id == run.id
    assert workbench.expert_evidence_run(project, expert.id).id == later.id
    unrelated = project.add_node('expert', 'expert')
    with pytest.raises(ValueError, match='연결된'):
        workbench.expert_evidence_run(project, unrelated.id, pinned_id)
    workbench.close()


def test_parent_routes_expert_and_selected_result_to_new_services(app, tmp_path, monkeypatch):
    workbench = Workbench()
    monkeypatch.setattr(workbench, 'programs', lambda: [])
    ui = ProjectWindow(workbench)
    expert = ui.add_box('expert')
    results = ui.add_box('results')
    ui.save_project(tmp_path / 'parent.json')
    called = []
    opened = SimpleNamespace(state='opened', pid=43, error='')
    monkeypatch.setattr(workbench, 'open_native_expert', lambda project, node_id: called.append(('expert', node_id)) or opened)
    monkeypatch.setattr(workbench, 'resolve_evidence_run', lambda *args: SimpleNamespace(id='selected'))
    monkeypatch.setattr(workbench, 'open_native_results', lambda project, node_id: called.append(('results', node_id)) or opened)
    monkeypatch.setattr(workbench, 'open_program', lambda *args: pytest.fail('wrong launch route'))
    ui.open_box(expert)
    ui.open_box(results)
    assert called == [('expert', expert.node_id), ('results', results.node_id)]
    ui._dirty = False
    ui.close()


@pytest.mark.parametrize('after', [None, '** revised'])
def test_proposal_review_reject_and_reopen_preserve_original(window, app, tmp_path, monkeypatch, after):
    from PySide6.QtWidgets import QInputDialog
    from caelab.assist.knowledge import KnowledgeSearchResult
    from test_native_evidence import make_run
    ui, provider = window
    run = make_run(tmp_path)
    source = Path(run.input_path)
    original = source.read_bytes()
    evidence = ui.workbench.capture_expert_run(run, ui.assets)
    ui.document = ui.workbench.attach_expert_evidence(ui.path, evidence, expected_revision=ui.document['revision'])
    monkeypatch.setattr(experts, 'search_knowledge_result', lambda *a, **kw: KnowledgeSearchResult('no_material', [], 'calculix'))
    provider.discuss = lambda prompt: '```proposals\n' + json.dumps([{
        'target_file': str(source), 'location': 'comment', 'before': '** native original',
        'after': after, 'reason': '검토용 모의 제안'}]) + '\n```'
    ui.role.setCurrentIndex(ui.role.findData('convergence'))
    ui.render()
    ui.question.setPlainText('변경 제안을 검토합니다.')
    ui.ask()
    finish(ui, app)
    assert ui.proposal_choice.count() == 1
    ui.check_proposal.click()
    assert ui.document['proposals'][0]['status'] == 'manual_required'
    if after is None:
        assert ui.proposals.toPlainText().startswith('전용 프로그램에서 확인 필요 · 미적용')
        assert '전용 프로그램에서 설정을 확인하세요.' in ui.proposals.toPlainText()
    else:
        assert ui.proposals.toPlainText().startswith('전용 프로그램에서 수정 필요 · 미적용')
        assert '전용 프로그램에서 수정하세요.' in ui.proposals.toPlainText()
    source.write_bytes(original + b'** edited externally\n')
    ui.check_proposal.click()
    assert ui.document['proposals'][0]['status'] == 'blocked'
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *args: ('이 변경은 사용자 제약에 맞지 않습니다.', True))
    ui.reject_proposal.click()
    assert ui.document['proposals'][0]['status'] == 'rejected'
    assert source.read_bytes() == original + b'** edited externally\n'
    ui.reload_document()
    assert '사용자 제약' in ui.proposals.toPlainText()
    assert ui.document['proposals'][0]['status'] == 'rejected'


def test_missing_asset_warning_is_visible_on_reopen_without_model(window, tmp_path):
    ui, provider = window
    evidence = attach(ui, tmp_path, '** retained input')
    asset = ui.assets / evidence['files'][0]['stored_path']
    asset.unlink()
    ui.reload_document()
    assert not ui.asset_warning.isHidden()
    assert '누락' in ui.asset_warning.text()
    assert '누락' in ui.attachment_preview.toPlainText()
    assert evidence['files'][0]['stored_path'] not in ui.attachment_preview.toPlainText()
    assert not hasattr(ui, 'asset_details')
    assert provider.prompts == []


def test_malformed_legacy_proposal_warns_without_hiding_the_discussion(window):
    from caelab.native.run_store import atomic_json
    ui, provider = window
    document = copy.deepcopy(ui.document)
    document['proposals'] = [None, {'id': 'legacy', 'status': [], 'review_history': [1]}]
    atomic_json(ui.path, document)
    ui.reload_document()
    assert ui.proposal_choice.count() == 1
    assert '불완전' in ui.proposals.toPlainText()
    assert '출처' in ui.asset_warning.text()
    assert ui.document['proposals'] == document['proposals']
    assert 'None' not in ui.proposals.toPlainText()
    assert provider.prompts == []


def test_explicit_new_run_capture_keeps_same_discussion_and_old_basis(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QComboBox, QDialog
    from caelab.native.run_store import RunStore
    from test_result_delivery import selected_project
    wb, project, analysis, _, expert, first = selected_project(tmp_path)
    ui = ExpertWindow(project.run_store / 'discussions/topic/discussion.json',
                      project_path=project.path, node_id=expert.id, run_id=first.id, workbench=wb)
    first_evidence = wb.capture_expert_run(first, ui.assets, frame_index=0)
    ui.document = wb.attach_expert_evidence(ui.path, first_evidence, expected_revision=ui.document['revision'])
    later = copy.deepcopy(first)
    later.id = 'b' * 32
    later.workdir = project.run_store / later.id
    shutil.copytree(first.workdir, later.workdir)
    RunStore(project.run_store).write(later)
    wb.select_native_run(project, analysis.id, later.id)
    observed = []
    def choose_new(dialog):
        choice = dialog.findChild(QComboBox, 'capture_run_choice')
        observed.append(choice.currentData().id)
        assert choice.count() == 2
        choice.setCurrentIndex(1)
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, 'exec', choose_new)
    ui.capture_run()
    finish(ui, app)
    assert observed == [first.id]
    assert len(ui.document['evidences']) == 2
    assert ui.document['evidences'][first_evidence['id']]['run_id'] == first.id
    assert ui.document['evidences'][ui.document['selected_evidence_id']]['run_id'] == later.id
    assert ui.run_id == first.id
    ui.close()


def test_parent_reopen_missing_store_keeps_graph_and_reports_missing_records(app, tmp_path, monkeypatch):
    wb = Workbench()
    monkeypatch.setattr(wb, 'programs', lambda: [])
    project = wb.new_native_project('보존할 연구')
    node = project.add_node('analysis', 'calculix')
    inp = tmp_path / 'source.inp'
    inp.write_text('** source', encoding='utf-8')
    project.set_file(node.id, inp)
    wb.save_native_project(project, tmp_path / 'saved.json')
    project.run_store.rmdir()  # This test's saved store has no execution records.
    ui = ProjectWindow(wb)
    assert ui.open_project(project.path, confirm=False)
    assert node.id in ui.boxes and ui.project.name == '보존할 연구'
    assert ui.project.run_store == project.run_store and ui.project.run_store_expected
    for shown in (ui.status.text(), ui.status.toolTip(), ui.log.toPlainText()):
        assert '계산·대화 기록' in shown and '기록 폴더' in shown
        assert str(project.run_store) not in shown
    ui.refresh_files()
    for shown in (ui.status.text(), ui.status.toolTip(), ui.log.toPlainText()):
        assert '계산·대화 기록' in shown and '기록 폴더' in shown
        assert str(project.run_store) not in shown
    assert not project.run_store.exists()
    ui._dirty = False
    ui.close()


def test_chat_render_preserves_history_title_draft_and_reading_position(window, app, tmp_path):
    from PySide6.QtGui import QTextCursor
    ui, provider = window
    evidence = attach(ui, tmp_path, '** A condition')
    ui.show()
    ui.resize(850, 700)
    ui.document['turns'] = [dict(id=str(index), question=f'질문 {index}', role='재료 모델 선택',
        evidence_id=evidence['id'], status='completed', reply='읽을 수 있는 **답변**입니다.\n\n' * 15) for index in range(8)]
    ui.document['title'] = '사용자가 붙인 연구 이름'
    original = copy.deepcopy(ui.document)
    ui.question.setPlainText('작성 중인 다음 질문')
    ui.render()
    app.processEvents()
    bar = ui.transcript.verticalScrollBar()
    assert bar.maximum() > 500
    bar.setValue(250)
    ui.render()
    app.processEvents()
    assert bar.value() == 250
    assert ui.question.toPlainText() == '작성 중인 다음 질문'
    assert ui.heading.text() == '사용자가 붙인 연구 이름'
    assert ui.document == original
    bar.setValue(bar.maximum())
    ui.document['turns'].append(dict(id='last', question='후속 질문', role='수렴·접촉 진단',
        evidence_id=evidence['id'], status='completed', reply='새로 받은 답변\n\n' * 12))
    ui.render()
    app.processEvents()
    assert bar.value() == bar.maximum()
    ui.document['title'] = '새 대화'
    ui.render()
    assert ui.heading.text() == '질문 0'


def test_attachments_keep_past_basis_and_show_human_units_and_failures(window, app, tmp_path):
    ui, provider = window
    a_dir, b_dir = tmp_path / 'A', tmp_path / 'B'
    a_dir.mkdir(); b_dir.mkdir()
    first = attach(ui, a_dir, '** condition A', 'same.inp')
    second = attach(ui, b_dir, '** condition B', 'same.inp')
    second_saved = ui.document['evidences'][second['id']]
    second_saved.update(units={'length': 'mm', 'force': 'N'}, unit_source='user',
                        process_state='failed', parse_state='partial', warnings=['오래된 실행'])
    ui.document['turns'] = [dict(id='old', question='A 질문', reply='A 답변', role='재료 모델 선택',
                               evidence_id=first['id'], status='completed')]
    ui.render()
    assert 'B/same.inp' in ui.basis.text() and 'A/same.inp' in ui.transcript.toPlainText()
    assert '계산 실패' in ui.basis.text() and '오래된' in ui.basis.text()
    ui.sources.setCurrentRow(1)
    assert '길이 mm' in ui.attachment_preview.toPlainText()
    assert '응력 미확인' in ui.attachment_preview.toPlainText()
    assert '사용자 선언' in ui.attachment_preview.toPlainText()
    assert '보존 자료' not in ui.transcript.toPlainText()  # no substituted diagnostic IDs
    assert provider.prompts == []
    ui.sources.setCurrentRow(0)
    assert ui.document['selected_evidence_id'] == second['id']
    assert ui.document['turns'][0]['evidence_id'] == first['id']


def test_question_pending_and_failure_are_next_to_the_question(window, app, tmp_path):
    ui, provider = window
    attach(ui, tmp_path, '** condition')
    entered, release = threading.Event(), threading.Event()
    def slow_failure(prompt):
        entered.set()
        release.wait(3)
        raise RuntimeError('모의 연결 실패')
    provider.discuss = slow_failure
    ui.question.setPlainText('이 질문의 실패를 확인합니다.')
    ui.ask()
    assert entered.wait(2)
    pending = ui.transcript.toPlainText()
    assert '이 질문의 실패' in pending and '기다리고' in pending
    release.set()
    finish(ui, app)
    assert '이 질문의 실패' in ui.transcript.toPlainText()
    assert '답변을 받지 못했습니다' in ui.transcript.toPlainText()
    assert ui.question.toPlainText() == '이 질문의 실패를 확인합니다.'


def test_chat_flow_import_select_send_cancel_save_and_reopen(window, app, tmp_path, monkeypatch):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog, QDialog, QLineEdit
    ui, provider = window
    ui.show(); app.processEvents()
    a_dir, b_dir = tmp_path / 'A', tmp_path / 'B'
    a_dir.mkdir(); b_dir.mkdir()
    a, b = a_dir / 'same.inp', b_dir / 'same.inp'
    a.write_text('** original A', encoding='utf-8')
    b.write_text('** changed B', encoding='utf-8')
    selected = [a]
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames', lambda *args: ([str(selected[0])], ''))
    monkeypatch.setattr(QDialog, 'exec', lambda *args: QDialog.Accepted)
    ui.import_button.click()
    finish(ui, app)
    first = ui.document['selected_evidence_id']
    ui.question.setPlainText('A의 결과를 함께 읽어 주세요.')
    QTest.keyClick(ui.question, Qt.Key_Return, Qt.ControlModifier)
    finish(ui, app)
    assert len(provider.prompts) == 1
    old_turn = copy.deepcopy(ui.document['turns'][0])
    assert '원문에 없는 수치' in ui.transcript.toPlainText()
    selected[0] = b
    ui.import_button.click()
    finish(ui, app)
    second = ui.document['selected_evidence_id']
    assert second != first and 'B/same.inp' in ui.basis.text()
    ui.attachments_button.click()
    app.processEvents()
    assert ui.attachments_dialog.isVisible()
    ui.sources.setCurrentRow(0)
    assert ui.document['selected_evidence_id'] == second
    ui.use_source.click()
    assert ui.document['selected_evidence_id'] == first
    ui.sources.setCurrentRow(1)
    ui.use_source.click()
    ui.attachments_dialog.hide()
    assert ui.document['selected_evidence_id'] == second
    started, release = threading.Event(), threading.Event()
    def slow(prompt):
        provider.prompts.append(prompt)
        started.set()
        release.wait(4)
        return '저장되면 안 되는 늦은 답변'
    provider.discuss = slow
    ui.question.setPlainText('B와 비교해도 되는지 후속 질문합니다.')
    QTest.keyClick(ui.question, Qt.Key_Return)
    assert started.wait(2)
    assert '기다리고' in ui.transcript.toPlainText()
    ui.stop.click()
    ui.render()
    assert not ui.stop.isEnabled()
    finish(ui, app)
    assert ui.document['turns'][0] == old_turn
    assert ui.document['turns'][-1]['status'] == 'cancelled'
    assert '중단' in ui.transcript.toPlainText()
    draft = ui.question.toPlainText()
    assert draft.startswith('B와 비교')
    assert ui.save_document()
    saved = json.loads(ui.path.read_text(encoding='utf-8'))
    reopened = ExpertWindow(ui.path)
    assert reopened.document['turns'] == saved['turns']
    assert reopened.document['selected_evidence_id'] == second
    assert reopened.question.toPlainText() == draft
    assert len(provider.prompts) == 2
    release.set()
    time.sleep(.1); app.processEvents()
    assert '늦은 답변' not in reopened.transcript.toPlainText()
    assert a.read_text() == '** original A' and b.read_text() == '** changed B'
    reopened.close()
    ui.reload_document()


def test_startup_error_is_readable_without_internal_payload(monkeypatch, capsys):
    from apps.workbench_qt import expert_window as entry
    raw = 'invalid JSON C:/private/project.runs/' + 'a' * 32 + '/discussion.json SHA256=' + 'b' * 64
    class App:
        def __init__(self, args): pass
        def setStyle(self, style): pass
    def broken(*args, **kwargs):
        raise ValueError(raw)
    seen = []
    monkeypatch.setattr(entry, 'QApplication', App)
    monkeypatch.setattr(entry, 'ExpertWindow', broken)
    monkeypatch.setattr(QMessageBox, 'critical', lambda parent, title, text: seen.append(text))
    assert entry.main(['--discussion', 'broken.json']) == 1
    assert len(seen) == 1
    assert all(token not in seen[0] for token in ('a' * 32, 'b' * 64, 'JSON', 'SHA256', 'C:/private'))
    assert '다른 대화 파일' in seen[0]
    assert raw in capsys.readouterr().err


def test_attachment_preview_names_original_retained_input_and_omits_derived_files(window, tmp_path):
    ui, provider = window
    evidence = attach(ui, tmp_path, '** original')
    stored = ui.document['evidences'][evidence['id']]
    stored['files'].extend([dict(role='execution_input', source_path='C:/project/project.runs/' + 'a'*32 + '/model.inp'),
                            dict(role='exchange', source_path='C:/project/project.runs/' + 'a'*32 + '/results/frame-000001.vtu')])
    ui.render()
    text = ui.attachment_preview.toPlainText()
    assert '원본 입력:' in text and '보존 실행 입력: model.inp' in text
    assert 'frame-000001.vtu' not in text and 'a'*32 not in text
    assert '다음 질문' in ui.role.toolTip() and '이전 답변' in ui.role.toolTip()


def test_send_button_visibly_distinguishes_enabled_waiting_and_unconfigured(window, app, tmp_path):
    ui, provider = window
    attach(ui, tmp_path, '** input')
    ui.show(); app.processEvents()
    def background():
        button = ui.ask_button
        return button.grab().toImage().pixelColor(button.width() - 8, button.height() // 2)
    assert ui.ask_button.isEnabled()
    enabled = background()
    ui._set_busy(True)
    app.processEvents()
    assert not ui.ask_button.isEnabled()
    waiting = background()
    assert waiting != enabled and waiting.lightness() > enabled.lightness() + 40
    ui._set_busy(False)
    ui._provider_available = False
    ui._set_busy(False)
    app.processEvents()
    assert not ui.ask_button.isEnabled()
    assert background() == waiting


@pytest.mark.parametrize('mode', ['dark', 'light'])
def test_chat_theme_and_before_after_cards_keep_proposals_unapplied(window, app, tmp_path, mode):
    from apps.workbench_qt.theme import current_theme_mode, get_palette, set_theme_mode
    ui, provider = window
    evidence = attach(ui, tmp_path, '** input')
    ui.document['turns'] = [dict(id='turn', evidence_id=evidence['id'], question='설정을 확인해 주세요.',
                                 role='재료 전문가', reply='확인할 설정입니다.', status='completed')]
    ui.document['proposals'] = [dict(id='change', request_id='turn', target_file='A/model.inp',
                                    location='*PLASTIC', before=0, after='1.2E-05 K⁻¹', reason='재료값 확인', status='unapplied'),
                                dict(id='check', request_id='turn', target_file='A/model.inp',
                                     location='*BOUNDARY', before=0, after=None, reason='방향 확인', status='manual_required')]
    set_theme_mode(mode)
    ui.render()
    ui.show(); app.processEvents()
    text = ui.transcript.toPlainText()
    assert '변경 전 (Before)' in text and '변경 후 (After)' in text and '현재 확인 내용' in text
    assert '1.2E-05 K⁻¹' in text and 'None' not in text and '미적용' in text
    assert get_palette()['chat_user_bg'] in ui.transcript.toHtml()
    assert ui.ask_button.objectName() == 'primary' and not ui.ask_button.styleSheet()
    enabled = ui.ask_button.grab().toImage().pixelColor(ui.ask_button.width() - 8, ui.ask_button.height() // 2)
    ui._set_busy(True); app.processEvents()
    waiting = ui.ask_button.grab().toImage().pixelColor(ui.ask_button.width() - 8, ui.ask_button.height() // 2)
    assert waiting.lightness() > enabled.lightness() + 40
    ui._set_busy(False)
    ui.theme_action.trigger()
    assert current_theme_mode() != mode and get_palette()['text_primary'] in ui.styleSheet()
    assert provider.prompts == []


def test_log_only_attachments_and_ui_validation_messages_are_specific(window, app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QDialog, QInputDialog
    ui, provider = window
    for folder in ('A', 'B'):
        directory = tmp_path / folder
        directory.mkdir()
        source = directory / 'same.log'
        source.write_text('log only', encoding='utf-8')
        evidence = ui.workbench.import_expert_files(ui.assets, files={'log': source}, solver_id='calculix')
        ui.document = ui.workbench.attach_expert_evidence(ui.path, evidence, expected_revision=ui.document['revision'])
    ui.render()
    labels = [ui.sources.item(i).text() for i in range(ui.sources.count())]
    assert '로그 A/same.log' in labels[0] and '로그 B/same.log' in labels[1]
    assert not any('입력 출처 미확인' in label for label in labels)
    for name in ('first.inp', 'second.inp'):
        (tmp_path / name).write_text('** input')
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames', lambda *args: ([str(tmp_path/'first.inp'), str(tmp_path/'second.inp')], ''))
    ui.import_files()
    assert '같은 종류의 파일은 한 번에 하나씩' in ui.status.text()
    ui.proposal_choice.addItem('모의 제안', 'item')
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *args: ('', True))
    ui.review_proposal('reject')
    assert ui.status.text() == '제안을 거절하는 이유를 입력하세요.'
    assert provider.prompts == []


def test_reopen_restores_document_specific_reading_anchor_and_tail(window, app, tmp_path):
    from PySide6.QtCore import QSettings
    ui, provider = window
    attach(ui, tmp_path, '** input')
    provider.discuss = lambda prompt: '길게 읽는 답변입니다.\n\n' * 45
    ui.show(); ui.resize(850, 700); app.processEvents()
    for index in range(3):
        ui.question.setPlainText(f'실제 저장되는 모의 질문 {index}')
        ui.ask(); finish(ui, app)
    ui.transcript.follow_tail = False
    ui.transcript.verticalScrollBar().setValue(350)
    app.processEvents()
    ui.question.setPlainText('읽으면서 작성하던 후속 질문')
    assert ui.save_document()
    anchor = ui.transcript.cursorForPosition(QPoint(0,0)).position()
    settings_path = ui.view_settings.fileName()
    original_turns = copy.deepcopy(ui.document['turns'])
    ui.close()
    saved_bytes = ui.path.read_bytes()
    reopened = ExpertWindow(ui.path, view_settings=QSettings(settings_path, QSettings.IniFormat))
    reopened.resize(850,700); reopened.show(); app.processEvents(); app.processEvents()
    assert reopened.transcript.cursorForPosition(QPoint(0,0)).position() == anchor
    assert reopened.transcript.verticalScrollBar().value() < reopened.transcript.verticalScrollBar().maximum()
    assert reopened.question.toPlainText() == '읽으면서 작성하던 후속 질문'
    assert reopened.document['turns'] == original_turns
    assert reopened.path.read_bytes() == saved_bytes
    other = tmp_path / 'different' / 'discussion.json'
    other.parent.mkdir()
    shutil.copy2(ui.path, other)
    different = ExpertWindow(other, view_settings=QSettings(settings_path, QSettings.IniFormat))
    different.resize(850,700); different.show(); app.processEvents(); app.processEvents()
    assert different.transcript.verticalScrollBar().value() == different.transcript.verticalScrollBar().maximum()
    different.close()
    reopened.transcript.restored_anchor = None
    reopened.transcript.follow_tail = True
    reopened.transcript.verticalScrollBar().setValue(reopened.transcript.verticalScrollBar().maximum())
    reopened.close()
    following = ExpertWindow(ui.path, view_settings=QSettings(settings_path, QSettings.IniFormat))
    following.resize(850,700); following.show(); app.processEvents(); app.processEvents()
    assert following.transcript.verticalScrollBar().value() == following.transcript.verticalScrollBar().maximum()
    following.close()
    ui.reload_document()


@pytest.mark.parametrize('target', [None, '', '/not-a-captured-input.inp'])
def test_rejected_protocol_proposals_are_explained_next_to_real_mock_answer(window, app, tmp_path, monkeypatch, target):
    from caelab.assist.knowledge import KnowledgeSearchResult
    ui, provider = window
    evidence = attach(ui, tmp_path, '** input')
    ui.role.setCurrentIndex(ui.role.findData('convergence'))
    monkeypatch.setattr(experts, 'search_knowledge_result', lambda *a, **k: KnowledgeSearchResult('no_material', [], 'calculix'))
    provider.discuss = lambda prompt: '답변은 읽을 수 있습니다.\n```proposals\n' + json.dumps([dict(
        target_file=target, location='force', before=0, after=None, reason='대상을 확인하세요.')]) + '\n```'
    ui.question.setPlainText('제안이 거부될 경우도 알려 주세요.')
    ui.ask(); finish(ui, app)
    assert ui.document['turns'][-1]['status'] == 'completed'
    assert ui.document['proposals'] == []
    assert '답변은 읽을 수 있습니다.' in ui.transcript.toPlainText()
    assert '제안 형식을 읽지 못했습니다' in ui.transcript.toPlainText()
    assert 'target_file' not in ui.transcript.toPlainText()
    assert target is None or target not in ui.transcript.toPlainText() or target == ''
    assert '```proposals' in ui.document['turns'][-1]['reply']


def test_context_keeps_user_notes_but_hides_automatic_failure_diagnostics(window, monkeypatch):
    from PySide6.QtWidgets import QDialog, QPlainTextEdit
    ui, provider = window
    secret = 'request_id=' + 'a'*32 + ' SHA256=' + 'b'*64
    ui.document['constraints'] = ['사용자가 작성한 제약']
    ui.document['failures'] = [dict(request_id='automatic-request', reason=secret),
                               dict(proposal_id='user-rejection', reason='사용자가 작성한 거절 이유'),
                               '사용자가 직접 작성한 실패 메모']
    before = copy.deepcopy(ui.document)
    observed = []
    def inspect(dialog):
        observed.extend(edit.toPlainText() for edit in dialog.findChildren(QPlainTextEdit))
        return QDialog.Rejected
    monkeypatch.setattr(QDialog, 'exec', inspect)
    ui.edit_context()
    shown = '\n'.join(observed)
    assert '사용자가 작성한 제약' in shown
    assert '사용자가 작성한 거절 이유' in shown and '사용자가 직접 작성한 실패 메모' in shown
    assert 'a'*32 not in shown and 'b'*64 not in shown and 'request_id' not in shown
    assert ui.document == before


def test_opening_an_unvisited_document_resets_view_without_losing_previous_reading_position(
        window, app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    ui, provider = window
    attach(ui, tmp_path, '** input')
    provider.discuss = lambda prompt: '문답을 읽던 위치를 확인할 긴 답변입니다.\n\n' * 45
    ui.resize(850, 700)
    ui.show()
    app.processEvents()
    ui.question.setPlainText('A 문답을 읽습니다.')
    ui.ask()
    finish(ui, app)
    bar = ui.transcript.verticalScrollBar()
    assert bar.maximum() > 350
    bar.setValue(350)
    ui.transcript.follow_tail = False
    ui.question.setPlainText('읽으면서 적어 둔 후속 질문')
    assert ui.save_document()
    original = ui.path
    other = tmp_path / 'unvisited' / 'discussion.json'
    other.parent.mkdir()
    shutil.copy2(original, other)
    shutil.copytree(ui.assets, other.parent / 'assets')
    selected = [other]
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(selected[0]), ''))

    ui.open_document()
    app.processEvents()
    app.processEvents()
    assert ui.path == other.resolve()
    assert bar.value() == bar.maximum()
    assert ui.transcript.follow_tail
    assert ui.question.toPlainText() == '읽으면서 적어 둔 후속 질문'

    selected[0] = original
    ui.open_document()
    app.processEvents()
    app.processEvents()
    assert ui.path == original
    assert bar.value() == 350
    assert not ui.transcript.follow_tail
    assert ui.question.toPlainText() == '읽으면서 적어 둔 후속 질문'
