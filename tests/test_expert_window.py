"""Independent discussion UI with real saved assets and mock model responses."""
import copy
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from apps.workbench_qt.expert_window import ExpertWindow
from apps.workbench_qt.project_window import ProjectWindow
from caelab.assist import experts
from caelab.workbench import Workbench


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
    assert 'condition A' in ui.details['model_text'].toPlainText()
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
    assert first['id'] in reopened.request_details.toPlainText()
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
    assert 'input only' in ui.details['model_text'].toPlainText()
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
    later.workdir.mkdir()
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
