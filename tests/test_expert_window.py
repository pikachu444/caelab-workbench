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
    monkeypatch.setenv('CAELAB_EXPERT_SETTINGS_PATH', str(tmp_path / 'expert-view.ini'))
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
    if hasattr(window, 'wait_for_asset_verification'):
        window.wait_for_asset_verification(timeout=2.0)
    assert window._operation is None


def wait_for_stage(window, app, label):
    deadline = time.monotonic() + 2
    while label not in window.wait_label.text() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert label in window.wait_label.text()


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
    ui.wait_for_asset_verification()
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
    ui.wait_for_asset_verification()
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


def test_request_stage_is_visible_through_real_service_callbacks(window, app, tmp_path, monkeypatch):
    from caelab.assist.knowledge import KnowledgeSearchResult
    ui, provider = window
    attach(ui, tmp_path, '** stage source')
    ui.role.setCurrentIndex(ui.role.findData('material'))
    preparing, prep_release = threading.Event(), threading.Event()
    responding, response_release = threading.Event(), threading.Event()
    def search(*args, **kwargs):
        preparing.set()
        assert prep_release.wait(3)
        return KnowledgeSearchResult('no_material', [], 'calculix')
    def discuss(prompt):
        responding.set()
        assert response_release.wait(3)
        return '모의 단계 응답'
    monkeypatch.setattr(experts, 'search_knowledge_result', search)
    provider.discuss = discuss
    try:
        ui.question.setPlainText('준비와 응답을 확인합니다.')
        ui.ask()
        assert preparing.wait(2)
        wait_for_stage(ui, app, '자료 준비')
        assert ui._operation == 'ask'
        prep_release.set()
        assert responding.wait(2)
        wait_for_stage(ui, app, '모델 응답')
        response_release.set()
        finish(ui, app)
        assert ui.document['turns'][-1]['status'] == 'completed'
        assert ui.document['turns'][-1]['stage'] == 'provider'
        assert ui.wait_label.text() == ''
    finally:
        prep_release.set()
        response_release.set()
        if ui._operation:
            ui.cancel_request()
            finish(ui, app)


def test_cancelled_and_previous_request_stage_cannot_replace_active_label(window, app, tmp_path, monkeypatch):
    ui, _ = window
    attach(ui, tmp_path, '** stage token source')
    callbacks = []
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    def ask(*args, on_stage=None, **kwargs):
        index = len(callbacks)
        callbacks.append(on_stage)
        assert on_stage is not None
        on_stage('preparation')
        entered[index].set()
        assert release[index].wait(3)
        return copy.deepcopy(ui.document)
    monkeypatch.setattr(ui.workbench, 'ask_expert_discussion', ask)
    try:
        ui.question.setPlainText('첫 요청')
        ui.ask()
        assert entered[0].wait(2)
        ui.cancel_request()
        callbacks[0]('provider')
        ui._poll()
        assert '중단 요청' in ui.wait_label.text()
        release[0].set()
        finish(ui, app)
        callbacks[0]('provider')
        ui._poll()
        assert ui.wait_label.text() == ''
        ui.question.setPlainText('다음 요청')
        ui.ask()
        assert entered[1].wait(2)
        callbacks[0]('provider')
        ui._poll()
        assert '자료 준비' in ui.wait_label.text()
        callbacks[1]('provider')
        ui._poll()
        assert '모델 응답' in ui.wait_label.text()
        callbacks[1]('unrecognized stage')
        ui._poll()
        assert '모델 응답' in ui.wait_label.text()
    finally:
        for event in release:
            event.set()
        if ui._operation:
            ui.cancel_request()
            finish(ui, app)


def test_background_asset_verification_lifecycle_and_stale_token_isolation(window, app, tmp_path, monkeypatch):
    ui, provider = window
    evidence = attach(ui, tmp_path, '** background test input')

    started = threading.Event()
    release = threading.Event()
    original_issues = ui.workbench.expert_discussion_issues

    def slow_issues(path, document):
        started.set()
        release.wait(5.0)
        return original_issues(path, document)

    monkeypatch.setattr(ui.workbench, 'expert_discussion_issues', slow_issues)

    ui.reload_document()
    assert started.wait(2.0)
    assert ui._asset_verifying
    assert not ui.ask_button.isEnabled()

    ui.question.setPlainText('검증 중에는 질문이 확정되지 않아야 합니다.')
    ui.ask()
    assert ui._operation is None
    assert provider.prompts == []

    other_path = tmp_path / 'other_discussion.json'
    ui.workbench.open_expert_discussion(other_path, create=True)
    old_token = ui._asset_token

    monkeypatch.setattr(ui.workbench, 'expert_discussion_issues', original_issues)
    ui._load(other_path)
    new_token = ui._asset_token
    assert new_token is not old_token
    assert not ui._asset_verifying

    release.set()
    time.sleep(0.05)
    ui._poll()

    assert ui.path == other_path.resolve()
    assert ui._asset_token is new_token
    assert ui._asset_issues == []


def test_background_asset_verification_completes_when_draft_saved_during_verification(
        window, app, tmp_path, monkeypatch):
    ui, provider = window
    evidence = attach(ui, tmp_path, '** background test input')
    assert ui.document.get('selected_evidence_id')

    started = threading.Event()
    release = threading.Event()
    original_issues = ui.workbench.expert_discussion_issues

    def slow_issues(path, document):
        started.set()
        release.wait(5.0)
        return original_issues(path, document)

    monkeypatch.setattr(ui.workbench, 'expert_discussion_issues', slow_issues)

    ui.reload_document()
    assert started.wait(2.0)
    assert ui._asset_verifying
    initial_rev = ui.document.get('revision')

    # While worker is delayed, user types draft question and saves
    ui.question.setPlainText('수정된 질문 초안')
    assert ui.save_document()
    assert ui.document.get('revision') > initial_rev

    # Now release worker and wait for verification to complete
    release.set()
    ui.wait_for_asset_verification(timeout=3.0)

    assert not ui._asset_verifying
    assert ui.ask_button.isEnabled()
    assert ui._asset_mailbox.empty()
    assert ui._asset_issues == []
    assert not ui.asset_warning.isVisible()


@pytest.mark.parametrize('bad_frame', [-1, -5, True, False, '0', 1.5])
def test_requested_frame_index_validation_rejects_invalid_values(app, tmp_path, bad_frame):
    wb = Workbench()
    try:
        with pytest.raises(ValueError):
            ExpertWindow(requested_frame_index=bad_frame, workbench=wb)
    finally:
        wb.jobs.close()


def test_cli_frame_index_argument_and_zero_capture_on_open(app, tmp_path, monkeypatch):
    from apps.workbench_qt import expert_window as entry

    calls = []
    created = []

    class App:
        def __init__(self, args):
            pass

        def setStyle(self, style):
            pass

        def exec(self):
            return 0

    monkeypatch.setattr(entry, 'QApplication', App)
    monkeypatch.setattr(Workbench, 'capture_expert_run',
                        lambda *a, **kw: pytest.fail('Opening ExpertWindow must not capture'))
    monkeypatch.setattr(experts.ExpertProvider, 'discuss',
                        lambda *a, **kw: pytest.fail('Opening ExpertWindow must not call model'))

    discussion_path = tmp_path / 'cli_discussion.json'
    original_init = ExpertWindow.__init__

    def spy_init(self, *args, **kwargs):
        calls.append(kwargs.get('requested_frame_index'))
        original_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(ExpertWindow, '__init__', spy_init)
    try:
        assert entry.main(['--discussion', str(discussion_path), '--frame-index', '0']) == 0
        assert calls == [0]

        seen_errors = []
        monkeypatch.setattr(QMessageBox, 'critical', lambda parent, title, text: seen_errors.append(text))
        assert entry.main(['--discussion', str(discussion_path), '--frame-index', '-1']) == 1
        assert len(seen_errors) == 1
    finally:
        for win in created:
            win.close()


def test_capture_run_frame_handoff_zero_nonzero_and_missing_frame(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel
    from apps.workbench_qt.expert_window import _run_time_text
    from caelab.native.run_store import RunStore
    from test_result_delivery import selected_project

    wb, project, analysis, _, expert, run = selected_project(tmp_path)
    monkeypatch.setattr(experts.ExpertProvider, 'discuss',
                        lambda *a, **kw: pytest.fail('capture_run tests must not call model'))

    # 1) requested_frame_index == 0 selects frame 0 (even though summary default is 1)
    ui0 = ExpertWindow(tmp_path / 'd0.json', project_path=project.path,
                       node_id=expert.id, run_id=run.id, requested_frame_index=0, workbench=wb)
    assert ui0.requested_frame_index == 0
    assert ui0.frame_context_state == 'requested'
    assert '초안 기준: 전달받은 계산 · 프레임 1' in ui0.basis.text()

    def accept_initial(dialog):
        runs_box = dialog.findChild(QComboBox, 'capture_run_choice')
        frames_box = dialog.findChild(QComboBox, 'capture_frame_choice')
        hint = dialog.findChild(QLabel, 'capture_missing_hint')
        box = dialog.findChild(QDialogButtonBox)
        ok_btn = box.button(QDialogButtonBox.Ok)
        cancel_btn = box.button(QDialogButtonBox.Cancel)
        assert ok_btn.text() == '가져오기'
        assert cancel_btn.text() == '취소'
        assert _run_time_text(run.created_at) in runs_box.currentText()
        assert 'T' not in runs_box.currentText().split(' · ')[-1]
        assert frames_box.currentData() == 0
        assert frames_box.currentText().startswith('프레임 1 · 단계 ')
        assert frames_box.itemText(1).startswith('프레임 2 · 단계 ')
        assert hint.isHidden()
        assert ok_btn.isEnabled()
        assert ui0.frame_context_state == 'matched'
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, 'exec', accept_initial)
    ui0.capture_run()
    finish(ui0, app)
    ev0_id = ui0.document['selected_evidence_id']
    ev0 = ui0.document['evidences'][ev0_id]
    assert ev0['frame_index'] == 0
    assert '프레임 1' in ui0.basis.text()
    assert '단위: mm / N (출처 미확인)' in ui0.basis.text()
    assert '선택한 첨부와 다른' not in ui0.basis.text()
    assert run.id not in ui0.basis.text()
    # Transcript turn header shows frame tag exactly once without duplication
    ui0.document['turns'] = [dict(id='t0', question='결과를 확인해 주세요', reply='응답',
                                   role='해석', evidence_id=ev0_id, status='completed')]
    ui0.render()
    transcript_text = ui0.transcript.toPlainText()
    assert transcript_text.count('프레임 1') == 1
    assert '프레임 1 · 프레임 1' not in transcript_text
    assert '단위: mm / N (출처 미확인)' in transcript_text
    ui0.document['turns'] = []
    assert ui0.close()

    # 2) requested_frame_index == 1 selects frame 1
    ui1 = ExpertWindow(tmp_path / 'd1.json', project_path=project.path,
                       node_id=expert.id, run_id=run.id, requested_frame_index=1, workbench=wb)

    def accept_one(dialog):
        frames_box = dialog.findChild(QComboBox, 'capture_frame_choice')
        assert frames_box.currentData() == 1
        assert frames_box.currentText().startswith('프레임 2 · 단계 ')
        assert ui1.frame_context_state == 'matched'
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, 'exec', accept_one)
    ui1.capture_run()
    finish(ui1, app)
    ev1 = ui1.document['evidences'][ui1.document['selected_evidence_id']]
    assert ev1['frame_index'] == 1
    assert '프레임 2' in ui1.basis.text()
    ui1.close()

    # 3) Missing requested_frame_index == 5 inserts placeholder, disables OK until valid frame chosen, preserves draft
    ui_missing = ExpertWindow(tmp_path / 'd5.json', project_path=project.path,
                              node_id=expert.id, run_id=run.id, requested_frame_index=5, workbench=wb)
    ui_missing.question.setPlainText('사용자가 작성한 질문 초안')

    # Guard check: even if dialog somehow returns Accepted with missing-frame tuple, capture is NOT triggered
    captured_calls = []
    real_capture = wb.capture_expert_run
    monkeypatch.setattr(wb, 'capture_expert_run',
                        lambda *a, **kw: captured_calls.append((a, kw)) or real_capture(*a, **kw))

    def accept_missing_placeholder(dialog):
        frames_box = dialog.findChild(QComboBox, 'capture_frame_choice')
        hint = dialog.findChild(QLabel, 'capture_missing_hint')
        ok_btn = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)
        assert frames_box.currentIndex() == 0
        assert frames_box.itemText(0) == '전달받은 프레임 6 · 기록 없음'
        assert frames_box.currentData() == ('__missing_frame__', 5)
        assert ui_missing.frame_context_state == 'missing'
        assert not hint.isHidden()
        assert '요청한 프레임이 계산 결과에 없습니다' in hint.text()
        assert not ok_btn.isEnabled()
        # Toggling to valid frame enables OK and hides hint; toggling back restores both
        frames_box.setCurrentIndex(1)
        assert frames_box.currentData() == 0
        assert hint.isHidden()
        assert ok_btn.isEnabled()
        frames_box.setCurrentIndex(0)
        assert not hint.isHidden()
        assert not ok_btn.isEnabled()
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, 'exec', accept_missing_placeholder)
    ui_missing.capture_run()
    assert captured_calls == []
    assert ui_missing._operation is None
    assert ui_missing.question.toPlainText() == '사용자가 작성한 질문 초안'

    # Now user explicitly picks valid frame 0 and accepts
    def choose_valid_frame(dialog):
        frames_box = dialog.findChild(QComboBox, 'capture_frame_choice')
        ok_btn = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)
        assert not ok_btn.isEnabled()
        frames_box.setCurrentIndex(frames_box.findData(0))
        assert ok_btn.isEnabled()
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, 'exec', choose_valid_frame)
    ui_missing.capture_run()
    finish(ui_missing, app)
    assert len(captured_calls) == 1
    assert ui_missing.question.toPlainText() == '사용자가 작성한 질문 초안'
    basis = ui_missing.basis.text()
    assert '초안 기준: 전달받은 계산 · 프레임 6 (기록 없음) · 선택한 첨부와 다른 프레임' in basis
    assert '이번 질문의 첨부:' in basis and '프레임 1' in basis
    assert run.id not in basis
    ui_missing.close()

    # 4) Missing requested_frame_index == 5 when run has NO parsed frames
    empty_run = copy.deepcopy(run)
    empty_run.id = 'c' * 32
    empty_run.workdir = project.run_store / empty_run.id
    shutil.copytree(run.workdir, empty_run.workdir)
    empty_run.result = {'frames': [], 'summary': {}, 'warnings': []}
    RunStore(project.run_store).write(empty_run)
    wb.select_native_run(project, analysis.id, empty_run.id)

    ui_empty = ExpertWindow(tmp_path / 'd_empty.json', project_path=project.path,
                            node_id=expert.id, run_id=empty_run.id, requested_frame_index=5, workbench=wb)
    captured_calls.clear()

    def choose_log_only(dialog):
        frames_box = dialog.findChild(QComboBox, 'capture_frame_choice')
        hint = dialog.findChild(QLabel, 'capture_missing_hint')
        ok_btn = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)
        assert frames_box.count() == 2
        assert frames_box.itemText(0) == '전달받은 프레임 6 · 기록 없음'
        assert frames_box.itemData(0) == ('__missing_frame__', 5)
        assert frames_box.itemText(1) == '판독한 결과 프레임 없음 — 입력과 로그 가져오기'
        assert frames_box.itemData(1) is None
        assert ui_empty.frame_context_state == 'missing'
        assert not hint.isHidden()
        assert not ok_btn.isEnabled()
        frames_box.setCurrentIndex(1)
        assert hint.isHidden()
        assert ok_btn.isEnabled()
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, 'exec', choose_log_only)
    ui_empty.capture_run()
    finish(ui_empty, app)
    assert len(captured_calls) == 1
    assert captured_calls[0][1]['frame_index'] is None
    assert '초안 기준: 전달받은 계산 · 프레임 6 (기록 없음) · 선택한 첨부와 다른 프레임' in ui_empty.basis.text()
    ui_empty.close()
    wb.jobs.close()


def test_asset_verification_badge_lifecycle_and_theme(window, app, tmp_path, monkeypatch):
    from apps.workbench_qt.theme import status_badge_style

    ui, provider = window
    ui.show()
    app.processEvents()

    # Empty document without evidence has clear tooltip explaining attachment requirement
    assert not ui.ask_button.isEnabled()
    assert '첨부 자료' in ui.ask_button.toolTip()
    assert ui.reverify_assets_button.isHidden()
    assert ui.wait_label.text() == ''

    attach(ui, tmp_path, '** badge test input')
    assert ui.ask_button.isEnabled()
    assert 'Enter' in ui.ask_button.toolTip()

    started = threading.Event()
    release = threading.Event()
    original_issues = ui.workbench.expert_discussion_issues

    def delayed_issues(path, document):
        started.set()
        release.wait(5.0)
        return []

    monkeypatch.setattr(ui.workbench, 'expert_discussion_issues', delayed_issues)
    ui.reload_document()
    assert started.wait(2.0)
    assert ui._asset_verifying is True
    assert ui._asset_verify_failed is False
    assert ui._asset_warning_tone == 'running'
    assert not ui.asset_warning.isHidden()
    assert '확인하는 중' in ui.asset_warning.text()
    assert status_badge_style('running', ui._theme_mode) in ui.asset_warning.styleSheet()
    assert not ui.ask_button.isEnabled()
    assert '무결성을 확인하는 중' in ui.ask_button.toolTip()
    assert ui.wait_label.text() == '무결성 확인 중…'
    assert not ui.reverify_assets_button.isVisible()

    # Toggling theme while verifying preserves running tone
    ui.theme_action.trigger()
    assert ui._asset_warning_tone == 'running'
    assert status_badge_style('running', ui._theme_mode) in ui.asset_warning.styleSheet()

    # Pass -> badge hides, ask button enabled, wait_label cleared
    release.set()
    ui.wait_for_asset_verification(timeout=3.0)
    assert not ui._asset_verifying
    assert not ui._asset_verify_failed
    assert not ui.asset_warning.isVisible()
    assert ui.asset_warning.text() == ''
    assert ui.ask_button.isEnabled()
    assert not ui.reverify_assets_button.isVisible()
    assert ui.wait_label.text() == ''

    # Issues returned -> warning badge + reverify button visible; ask_button stays non-blocked
    monkeypatch.setattr(ui.workbench, 'expert_discussion_issues',
                        lambda path, doc: ['근거 missing: 보존 파일 누락'])
    ui.reload_document()
    ui.wait_for_asset_verification(timeout=3.0)
    assert not ui._asset_verifying
    assert not ui._asset_verify_failed
    assert ui._asset_warning_tone == 'warning'
    assert ui.asset_warning.isVisible()
    assert '누락·변경' in ui.asset_warning.text()
    assert status_badge_style('warning', ui._theme_mode) in ui.asset_warning.styleSheet()
    assert ui.reverify_assets_button.isVisible() and ui.reverify_assets_button.isEnabled()
    assert ui.ask_button.isEnabled()
    assert ui.wait_label.text() == ''

    # Exception raised -> error badge visible, ask_button & ask() blocked, reverify button recovers
    def broken_issues(path, doc):
        raise RuntimeError('INTERNAL_SECRET_TRACE')

    monkeypatch.setattr(ui.workbench, 'expert_discussion_issues', broken_issues)
    ui.reload_document()
    ui.wait_for_asset_verification(timeout=3.0)
    assert not ui._asset_verifying
    assert ui._asset_verify_failed is True
    assert ui._asset_warning_tone == 'error'
    assert ui.asset_warning.isVisible()
    assert '무결성 확인에 실패했습니다' in ui.asset_warning.text()
    assert 'INTERNAL_SECRET_TRACE' not in ui.asset_warning.text()
    assert status_badge_style('error', ui._theme_mode) in ui.asset_warning.styleSheet()
    assert not ui.ask_button.isEnabled()
    assert '무결성 확인에 실패했습니다' in ui.ask_button.toolTip()
    assert ui.wait_label.text() == '무결성 확인 실패'
    assert ui.reverify_assets_button.isVisible() and ui.reverify_assets_button.isEnabled()

    # Clicking ask directly is also guarded while verification failed
    ui.question.setPlainText('실패 상태에서 질문 시도')
    ui.ask()
    assert ui._operation is None
    assert provider.prompts == []

    # Reverify via button when error resolves restores ask_button even on unchanged document
    monkeypatch.setattr(ui.workbench, 'expert_discussion_issues', original_issues)
    ui.reverify_assets_button.click()
    ui.wait_for_asset_verification(timeout=3.0)
    assert not ui._asset_verifying
    assert not ui._asset_verify_failed
    assert not ui.asset_warning.isVisible()
    assert not ui.reverify_assets_button.isVisible()
    assert ui.ask_button.isEnabled()
    assert ui.wait_label.text() == ''
    assert provider.prompts == []

    # Provider unavailable updates tooltip specifically
    ui._provider_available = False
    ui._set_busy(False)
    assert not ui.ask_button.isEnabled()
    assert '공급자' in ui.ask_button.toolTip()
    ui._provider_available = True
    ui._set_busy(False)


def test_unit_provenance_and_no_duplicate_unconfirmed_units_in_basis_and_transcript(window, tmp_path):
    ui, _ = window
    ev_none = attach(ui, tmp_path, '** no units', 'nounits.inp')
    ev_user = attach(ui, tmp_path, '** user units', 'userunits.inp')
    ev_unknown_src = attach(ui, tmp_path, '** unknown source units', 'unknownsrc.inp')
    ui.document['evidences'][ev_user['id']].update(
        units={'length': 'mm', 'force': 'N'},
        unit_source='user',
    )
    ui.document['evidences'][ev_unknown_src['id']].update(
        units={'length': 'm', 'force': 'N'},
        unit_source='declared by user',
    )
    ui.document['turns'] = [
        {'id': 't1', 'question': '단위 없음 확인', 'reply': '응답 1', 'role': '해석',
         'evidence_id': ev_none['id'], 'status': 'completed'},
        {'id': 't2', 'question': '사용자 선언 확인', 'reply': '응답 2', 'role': '해석',
         'evidence_id': ev_user['id'], 'status': 'completed'},
        {'id': 't3', 'question': '출처 미확인 확인', 'reply': '응답 3', 'role': '해석',
         'evidence_id': ev_unknown_src['id'], 'status': 'completed'},
    ]

    ui.document['selected_evidence_id'] = ev_none['id']
    ui.render()
    assert ui.basis.text().count('단위 미확인') == 1

    ui.document['selected_evidence_id'] = ev_user['id']
    ui.render()
    assert '단위: mm / N (사용자 선언)' in ui.basis.text()

    ui.document['selected_evidence_id'] = ev_unknown_src['id']
    ui.render()
    assert '단위: m / N (출처 미확인)' in ui.basis.text()

    lines = ui.transcript.toPlainText().splitlines()
    header_t1 = next(line for line in lines if 'nounits.inp' in line)
    header_t2 = next(line for line in lines if 'userunits.inp' in line)
    header_t3 = next(line for line in lines if 'unknownsrc.inp' in line)
    assert header_t1.count('단위 미확인') == 1
    assert '단위: mm / N (사용자 선언)' in header_t2
    assert '단위: m / N (출처 미확인)' in header_t3
    ui.document['turns'] = []


def test_basis_distinguishes_run_frame_and_manual_mismatches(app, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Discard)
    wb = Workbench()
    try:
        run_a = 'a' * 32
        run_b = 'b' * 32
        ui = ExpertWindow(run_id=run_a, requested_frame_index=0, workbench=wb)

        ev_diff_frame = {'id': 'e1', 'source_kind': 'native_run', 'run_id': run_a, 'frame_index': 1,
                         'created_at': '2026-11-18T00:00:00Z', 'files': [], 'units': {}}
        ev_diff_run = {'id': 'e2', 'source_kind': 'native_run', 'run_id': run_b, 'frame_index': 0,
                       'created_at': '2026-11-18T00:00:00Z', 'files': [], 'units': {}}
        ev_diff_both = {'id': 'e3', 'source_kind': 'native_run', 'run_id': run_b, 'frame_index': 2,
                        'created_at': '2026-11-18T00:00:00Z', 'files': [], 'units': {}}
        ev_manual = {'id': 'e4', 'source_kind': 'manual', 'run_id': None, 'frame_index': None,
                     'created_at': '2026-11-18T00:00:00Z', 'files': [], 'units': {}}
        ui.document['evidences'] = {e['id']: e for e in (ev_diff_frame, ev_diff_run, ev_diff_both, ev_manual)}

        for ev, expected in (
            (ev_diff_frame, '초안 기준: 전달받은 계산 · 프레임 1 · 선택한 첨부와 다른 프레임'),
            (ev_diff_run, '초안 기준: 전달받은 계산 · 프레임 1 · 선택한 첨부와 다른 실행'),
            (ev_diff_both, '초안 기준: 전달받은 계산 · 프레임 1 · 선택한 첨부와 다른 실행·프레임'),
            (ev_manual, '초안 기준: 전달받은 계산 · 프레임 1 · 선택한 첨부는 수동 파일·다른 프레임'),
        ):
            ui.document['selected_evidence_id'] = ev['id']
            ui.render()
            first_line = ui.basis.text().splitlines()[0]
            assert first_line == expected
            assert run_a not in ui.basis.text() and run_b not in ui.basis.text()

        # When requested_frame_index is None, still distinguish other run vs manual file
        ui_no_frame = ExpertWindow(run_id=run_a, workbench=wb)
        ui_no_frame.document['evidences'] = {'e2': ev_diff_run, 'e4': ev_manual}
        ui_no_frame.document['selected_evidence_id'] = 'e2'
        ui_no_frame.render()
        assert ui_no_frame.basis.text().splitlines()[0] == '초안 기준: 전달받은 계산 · 선택한 첨부와 다른 실행'
        ui_no_frame.document['selected_evidence_id'] = 'e4'
        ui_no_frame.render()
        assert ui_no_frame.basis.text().splitlines()[0] == '초안 기준: 전달받은 계산 · 선택한 첨부는 수동 파일'
        ui.close()
        ui_no_frame.close()
    finally:
        wb.jobs.close()


def test_handoff_notice_persists_until_superseded_by_attach_or_verify_error(app, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Discard)
    wb = Workbench()
    try:
        # 1) Fresh handoff keeps notice across initial render until evidence is attached
        ui = ExpertWindow(tmp_path / 'handoff_attach.json', run_id='a' * 32, workbench=wb)
        assert ui._handoff_notice_active is True
        assert '전달되었습니다' in ui.status.text()
        assert not ui.status.isHidden()

        ui.render()
        assert ui._handoff_notice_active is True
        assert '전달되었습니다' in ui.status.text()

        inp = tmp_path / 'a.inp'
        inp.write_text('** input', encoding='utf-8')
        ev = wb.import_expert_files(ui.assets, files={'input': inp}, solver_id='calculix')
        ui._operation = 'attach'
        tok = object()
        ui._op_token = tok
        ui._mailbox.put((tok, ui.path.resolve(), True, ev))
        ui._poll()
        ui.wait_for_asset_verification(timeout=3.0)
        assert ui._handoff_notice_active is False
        assert ui.status.text() == '자료를 첨부했습니다.'
        ui.render()
        assert ui.status.text() == '자료를 첨부했습니다.'
        ui.close()

        # 2) Verification failure supersedes initial handoff notice
        ui_err = ExpertWindow(tmp_path / 'handoff_err.json', run_id='a' * 32, workbench=wb)
        assert ui_err._handoff_notice_active is True
        tok_asset = object()
        ui_err._asset_token = tok_asset
        ui_err._asset_mailbox.put((tok_asset, ui_err.path.resolve(), None, None, False, ['error']))
        ui_err._poll()
        assert ui_err._asset_verify_failed is True
        assert ui_err._handoff_notice_active is False
        assert ui_err.status.text() == ''
        assert ui_err.status.isHidden()
        ui_err.close()
    finally:
        wb.jobs.close()


def test_late_responses_ignored_on_switched_document_and_closed_window(window, app, tmp_path, monkeypatch):
    ui, provider = window
    attach(ui, tmp_path, '** doc A input')
    doc_a_path = ui.path
    doc_a_id = ui.document['id']

    # 1) Switched document ignores late _mailbox and _asset_mailbox messages
    ask_started = threading.Event()
    ask_release = threading.Event()
    fake_doc_a = copy.deepcopy(ui.document)
    fake_doc_a['title'] = '오염되면 안 되는 A 응답'
    fake_doc_a['turns'] = [{'id': 'late-a', 'question': 'A', 'reply': '늦은 A 응답',
                            'role': '재료', 'evidence_id': ui.document['selected_evidence_id'],
                            'status': 'completed'}]

    def slow_ask(*args, **kwargs):
        ask_started.set()
        ask_release.wait(5.0)
        return fake_doc_a

    monkeypatch.setattr(ui.workbench, 'ask_expert_discussion', slow_ask)
    ui.question.setPlainText('문서 A 질문')
    ui.ask()
    assert ask_started.wait(2.0)
    old_op_token = ui._op_token
    old_asset_token = ui._asset_token
    assert old_op_token is not None

    doc_b_path = tmp_path / 'doc_b.json'
    ui.workbench.open_expert_discussion(doc_b_path, create=True)
    ui._load(doc_b_path)
    assert ui._op_token is None
    assert ui.path == doc_b_path.resolve()
    doc_b_id = ui.document['id']
    assert doc_b_id != doc_a_id
    ui.question.setPlainText('문서 B 초안')

    # Finish slow ask for doc A AND inject stale asset message for doc A
    ask_release.set()
    time.sleep(0.05)
    ui._asset_mailbox.put((old_asset_token, doc_a_path, 0, ({}, []), False, ['stale error']))
    # Also simulate an active operation on doc B while stale doc A result arrives
    ui._operation = 'ask'
    new_token = object()
    ui._op_token = new_token
    ui._mailbox.put((old_op_token, doc_a_path, True, fake_doc_a))
    ui._poll()

    assert ui.document['id'] == doc_b_id
    assert ui.document['turns'] == []
    assert ui.question.toPlainText() == '문서 B 초안'
    assert ui.asset_warning.isHidden() and not ui.asset_warning.isVisible()
    assert ui._operation == 'ask' and ui._op_token is new_token
    ui._operation = None
    ui._op_token = None

    # 2) Closed window ignores late _asset_mailbox / _mailbox / _stage_mailbox responses
    closed_path = tmp_path / 'closed_doc.json'
    closed_win = ExpertWindow(closed_path)
    attach(closed_win, tmp_path, '** closed doc input')
    closed_win.question.setPlainText('닫기 전 초안')
    assert closed_win.save_document()
    assert closed_win.close()
    assert closed_win._closed is True
    assert closed_win._op_token is None
    assert closed_win._asset_token is None
    assert not closed_win._asset_verifying
    assert not closed_win.timer.isActive()

    doc_snapshot = copy.deepcopy(closed_win.document)
    basis_snapshot = closed_win.basis.text()
    warning_snapshot = closed_win.asset_warning.text()
    question_snapshot = closed_win.question.toPlainText()

    closed_win._stage_mailbox.put((object(), 'provider'))
    closed_win._asset_mailbox.put((object(), closed_path.resolve(), None, None, False, ['late error']))
    closed_win._mailbox.put((object(), closed_path.resolve(), True, {'id': 'mutated', 'turns': []}))
    closed_win._poll()

    assert closed_win.document == doc_snapshot
    assert closed_win.basis.text() == basis_snapshot
    assert closed_win.asset_warning.text() == warning_snapshot
    assert closed_win.question.toPlainText() == question_snapshot
    closed_win.workbench.jobs.close()


def test_close_while_operation_active_cancels_then_marks_closed(window, app, tmp_path, monkeypatch):
    ui, provider = window
    attach(ui, tmp_path, '** close after test')
    entered, release = threading.Event(), threading.Event()

    def slow_discuss(prompt):
        entered.set()
        release.wait(5.0)
        return '응답'

    provider.discuss = slow_discuss
    ui.question.setPlainText('닫기 테스트 질문')
    ui.ask()
    assert entered.wait(2.0)

    # First close request defers closing while operation cancels
    assert not ui.close()
    assert ui._close_after is True
    assert not ui._closed
    assert ui.timer.isActive()

    release.set()
    deadline = time.monotonic() + 5.0
    while not ui._closed and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert ui._closed is True
    assert ui._op_token is None
    assert ui._asset_token is None
    assert not ui.timer.isActive()
