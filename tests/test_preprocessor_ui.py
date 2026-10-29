"""CAD exchange input and a preprocessor's editable project remain distinct."""
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from apps.workbench_qt.project_window import ProjectWindow


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv('CAELAB_UI_SETTINGS_PATH', str(tmp_path / 'ui.ini'))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Cancel)
    instance = ProjectWindow()
    yield instance
    instance._dirty = False
    instance.close()


def write(tmp_path, name):
    path = tmp_path / name
    path.write_text(name, encoding='utf-8')
    return path


def select(window, box):
    window.scene.clearSelection()
    box.setSelected(True)
    window._selection_changed()


def test_cad_step_pmx_and_solver_deck_are_separate(window, tmp_path):
    cad, pre, solver = (window.add_box(kind) for kind in ('geometry', 'preprocessor', 'analysis'))
    fcstd, step, pmx, deck = (write(tmp_path, name) for name in ('part.FCStd', 'part.step', 'study.pmx', 'model.inp'))
    assert window.register_file(cad.node_id, fcstd)
    assert window.register_file(cad.node_id, step, 'output')
    assert window.connect(cad.node_id, pre.node_id)
    assert window.register_file(pre.node_id, pmx, 'document')
    assert window.register_file(pre.node_id, deck, 'output')
    assert window.connect(pre.node_id, solver.node_id)
    assert Path(window.project.input_file(pre.node_id).path) == step
    assert Path(window.project.nodes[pre.node_id].files['document'].path) == pmx
    assert Path(window.project.input_file(solver.node_id).path) == deck
    select(window, pre)
    assert step.name in window.input_value.text()
    assert pmx.name in window.document_value.text()
    assert deck.name in window.output_value.text()
    assert window.open_button.text() == 'PrePoMax 편집 문서 열기'


def test_document_only_reopens_without_requiring_cad_input(window, tmp_path, monkeypatch):
    pre = window.add_box('preprocessor')
    pmx = write(tmp_path, 'saved-study.pmx')
    assert window.register_file(pre.node_id, pmx, 'document')
    select(window, pre)
    assert not window._needs_file(pre)
    assert not window._needs_upstream(pre)
    opened = []
    monkeypatch.setattr(window.workbench, 'open_program',
                        lambda project, node_id, **kwargs: opened.append((node_id, kwargs)) or SimpleNamespace(state='opened'))
    window.open_selected()
    assert opened == [(pre.node_id, {})]


def test_explicit_import_uses_step_while_default_open_uses_document(window, tmp_path, monkeypatch):
    pre = window.add_box('preprocessor')
    assert window.register_file(pre.node_id, write(tmp_path, 'part.step'))
    assert window.register_file(pre.node_id, write(tmp_path, 'study.pmx'), 'document')
    select(window, pre)
    opened = []
    monkeypatch.setattr(window.workbench, 'open_program',
                        lambda project, node_id, **kwargs: opened.append(kwargs) or SimpleNamespace(state='opened'))
    window.open_selected()
    window.import_selected_input()
    assert opened == [{}, {'use_input': True}]
    assert window.project.nodes[pre.node_id].files['document'].path.endswith('study.pmx')


def test_program_choice_changes_actual_node_and_keeps_documents(window, tmp_path):
    pre = window.add_box('preprocessor')
    assert window.register_file(pre.node_id, write(tmp_path, 'study.pmx'), 'document')
    select(window, pre)
    assert window.program_choice.findData('salome') >= 0
    assert window.program_choice.findData('gmsh') < 0  # meshing is a separate role
    window.program_choice.setCurrentIndex(window.program_choice.findData('salome'))
    node = window.project.nodes[pre.node_id]
    assert node.program_id == 'salome'
    assert node.files['document'].path.endswith('study.pmx')
    assert node.files['document'].stale
    assert '갱신 필요' in window.document_summary.text()
    analysis = window.add_box('analysis')
    select(window, analysis)
    assert window.program_choice.findData('codeaster') >= 0
    window.program_choice.setCurrentIndex(window.program_choice.findData('codeaster'))
    assert window.project.nodes[analysis.node_id].program_id == 'codeaster'


def test_edit_document_dialog_does_not_request_physical_units(window, tmp_path, monkeypatch):
    pre = window.add_box('preprocessor')
    pmx = write(tmp_path, 'study.pmx')
    select(window, pre)
    dialog = []
    monkeypatch.setattr(QFileDialog, 'getOpenFileName',
                        lambda parent, title, folder, filters: dialog.append((title, filters)) or (str(pmx), ''))
    monkeypatch.setattr(window, '_ask_file_units', lambda *args: pytest.fail('editable project metadata is not an exchanged physical input'))
    assert window.choose_file('document')
    assert '*.pmx' in dialog[0][1]
    assert window.project.nodes[pre.node_id].files['document'].path == str(pmx)


def test_pmx_reference_survives_project_save_and_reopen(window, tmp_path):
    pre = window.add_box('preprocessor')
    pmx = write(tmp_path, 'study.pmx')
    assert window.register_file(pre.node_id, pmx, 'document')
    project_file = tmp_path / 'project.json'
    window.workbench.save_native_project(window.project, project_file)
    assert window.open_project(project_file, confirm=False)
    restored = window.boxes[pre.node_id]
    select(window, restored)
    assert window.document_value.text().endswith(pmx.name)
    assert window.open_button.text() == 'PrePoMax 편집 문서 열기'


def test_result_viewer_choice_names_and_opens_selected_preprocessor_viewer(window, tmp_path, monkeypatch):
    result = window.add_box('results')
    assert window.register_file(result.node_id, write(tmp_path, 'model.frd'))
    select(window, result)
    assert window.program_choice.findData('prepomax') >= 0
    window.program_choice.setCurrentIndex(window.program_choice.findData('prepomax'))
    assert window.project.nodes[result.node_id].program_id == 'prepomax'
    assert window.boxes[result.node_id].title == 'PrePoMax'
    assert window.open_button.text() == 'PrePoMax 열기'
    opened = []
    monkeypatch.setattr(window.workbench, 'open_program',
                        lambda project, node_id: opened.append(project.nodes[node_id].program_id) or SimpleNamespace(state='opened'))
    window.open_selected()
    assert opened == ['prepomax']


def test_changed_cad_exchange_is_not_hidden_by_ready_pmx_and_deck(window, tmp_path):
    cad, pre = window.add_box('geometry'), window.add_box('preprocessor')
    step = write(tmp_path, 'part.step')
    assert window.register_file(cad.node_id, step, 'output')
    assert window.connect(cad.node_id, pre.node_id)
    assert window.register_file(pre.node_id, write(tmp_path, 'study.pmx'), 'document')
    assert window.register_file(pre.node_id, write(tmp_path, 'model.inp'), 'output')
    step.write_text('CAD revision changed externally', encoding='utf-8')
    window._reflect_programs()
    select(window, pre)
    assert '변경됨' in window.input_summary.text()
    assert window.boxes[pre.node_id].status_label == '입력 파일 변경됨'
    assert window.boxes[pre.node_id].status_warning


@pytest.mark.parametrize(('reason', 'expected'), [
    ('PrePoMax의 지원 실행 환경은 windows입니다. linux 연결은 검증되지 않았습니다.', ('Windows', 'Linux', '지원하지 않습니다')),
    ('PrePoMax 실행 파일을 찾지 못했습니다 (PATH).', ('PrePoMax 실행 파일', '연결 설정')),
])
def test_preprocessor_connection_problem_explains_platform_or_missing_install(window, tmp_path, monkeypatch, reason, expected):
    pre = window.add_box('preprocessor')
    assert window.register_file(pre.node_id, write(tmp_path, 'study.pmx'), 'document')
    monkeypatch.setattr(window.workbench, 'programs', lambda: [SimpleNamespace(
        spec=SimpleNamespace(id='prepomax', name='PrePoMax'), available=False, reason=reason)])
    window._reflect_programs()
    select(window, pre)
    assert all(part in window.program_summary.text() for part in expected)
    assert '원인을 확인하지 못했습니다' not in window.program_summary.text()
