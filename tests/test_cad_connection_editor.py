"""Source/offscreen CAD intents; no installed GUI or vendor program execution."""

import os
from pathlib import Path
import subprocess
import urllib.request

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PySide6')
from PySide6.QtGui import QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QVBoxLayout
from shiboken6 import isValid

from apps.workbench_qt.cad_connection_editor import CadConnectionEditor
from apps.workbench_qt.theme import build_stylesheet
from caelab.connections import PROGRAMS, ProgramStatus
from caelab.project import FileReference, ProgramNode


@pytest.fixture(scope='module')
def app():
    instance = QApplication.instance() or QApplication([])
    assert instance.platformName() == 'offscreen'
    # Windows offscreen has no system font database; load an installed font in Qt only.
    if os.name == 'nt' and not QFontDatabase.families():
        font = Path(os.environ['WINDIR']) / 'Fonts/malgun.ttf'
        if font.is_file():
            assert QFontDatabase.addApplicationFont(str(font)) >= 0
    assert QFontDatabase.families(), 'Offscreen render needs an existing readable font'
    return instance


@pytest.fixture
def editor(app, tmp_path, monkeypatch):
    for key in ('CAELAB_UI_SETTINGS_PATH', 'CAELAB_EXPERT_SETTINGS_PATH', 'CAELAB_MDO_SETTINGS_PATH'):
        monkeypatch.setenv(key, str(tmp_path / (key + '.ini')))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('unexpected process'))
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **kw: pytest.fail('unexpected network'))
    monkeypatch.setattr('caelab.project.file_revision', lambda *a: pytest.fail('binding must not hash files'))
    widget = CadConnectionEditor()
    yield widget
    if isValid(widget):
        widget.close()
        widget.deleteLater()
    app.processEvents()


def node(program='freecad', node_id='selected-geometry'):
    return ProgramNode(node_id, 'geometry', program)


def statuses(available=False):
    return [ProgramStatus(spec, path='configured.exe' if available else None,
                          reason='' if available else '테스트 실행 파일 미설정')
            for spec in PROGRAMS if spec.role == 'cad']


def observe(editor):
    programs, documents = [], []
    editor.program_change_requested.connect(lambda *args: programs.append(args))
    editor.document_registration_requested.connect(lambda *args: documents.append(args))
    return programs, documents


def bind(editor, current, epoch=1, state='ready', available=False):
    editor.set_context(current, statuses(available), document_state=state, context_epoch=epoch)


def test_bind_and_clear_emit_no_intents_and_never_hash_documents(editor):
    current = node()
    current.files['document'] = FileReference('not-readable/한글 설계.FCStd', 'recorded revision')
    programs, documents = observe(editor)
    bind(editor, current, state='changed')
    assert editor.program_choice.currentData() == 'freecad'
    assert editor.document_path.text() == current.files['document'].path
    assert editor.document_path.isReadOnly()
    assert editor.document_status.text() == '원본 변경됨'
    assert '미설정' in editor.program_status.text()
    assert editor.document_button.isEnabled()  # availability does not block offline registration
    assert not programs and not documents
    editor.clear_context()
    assert not editor.isEnabled() and not editor.document_path.text()
    assert not programs and not documents


def test_program_choice_requests_change_without_mutating_or_optimistically_committing(editor):
    current = node()
    original = FileReference('original.FCStd', 'old')
    current.files['document'] = original
    programs, documents = observe(editor)
    bind(editor, current)
    editor.program_choice.setCurrentIndex(editor.program_choice.findData('solidcraft'))
    assert programs == [(current.id, 'solidcraft')]
    assert current.program_id == 'freecad' and current.files['document'] is original
    assert editor.program_choice.currentData() == 'freecad'
    assert not documents


@pytest.mark.parametrize('program,suffix', [('freecad', '.FCStd'), ('solidcraft', '.sc'), ('cadcraft', '.dwg')])
def test_document_registration_emits_exact_path_and_program_filter_only(editor, tmp_path, monkeypatch, program, suffix):
    current = node(program)
    original = tmp_path / ('한글 원본 공백' + suffix)
    original.write_bytes(b'owned fixture, not a vendor model')
    programs, documents = observe(editor)
    bind(editor, current)
    dialogs = []

    def choose(*args):
        dialogs.append(args)
        return str(original), ''

    monkeypatch.setattr(QFileDialog, 'getOpenFileName', choose)
    editor.document_button.click()
    assert documents == [(current.id, str(original))] and not programs
    assert current.files == {}  # G's consumer owns the actual registration
    assert suffix.lower() in dialogs[0][3].lower()
    if program == 'cadcraft':
        assert '*.step' not in dialogs[0][3] and '*.svg' not in dialogs[0][3]
    assert original.read_bytes() == b'owned fixture, not a vendor model'


def test_cancelled_picker_preserves_model_and_emits_nothing(editor, monkeypatch):
    current = node()
    current.files['document'] = FileReference('original.FCStd', 'unchanged')
    original = current.files['document']
    programs, documents = observe(editor)
    bind(editor, current)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: ('', ''))
    editor.document_button.click()
    assert current.files['document'] is original and not programs and not documents


@pytest.mark.parametrize('change', ['project-same-node-id', 'other-node', 'program', 'clear', 'leave-and-return'])
def test_picker_discards_stale_context(editor, monkeypatch, change):
    current = node()
    programs, documents = observe(editor)
    bind(editor, current)

    def choose(*args):
        if change == 'project-same-node-id':
            bind(editor, node(node_id=current.id), epoch=2)
        elif change == 'other-node':
            bind(editor, node(node_id='another-geometry'), epoch=2)
        elif change == 'program':
            current.program_id = 'cadcraft'
            bind(editor, current, epoch=2)
        elif change == 'clear':
            editor.clear_context()
        else:
            editor.clear_context()
            bind(editor, current, epoch=3)
        return 'old context.FCStd', ''

    monkeypatch.setattr(QFileDialog, 'getOpenFileName', choose)
    editor.document_button.click()
    assert not programs and not documents
    assert '선택 대상이 바뀌어' in editor.feedback.text()


def test_same_context_status_update_keeps_pending_choice_valid(editor, monkeypatch):
    current = node()
    _, documents = observe(editor)
    bind(editor, current)

    def choose(*args):
        bind(editor, current, state='stale', available=True)
        return '선택 원본.FCStd', ''

    monkeypatch.setattr(QFileDialog, 'getOpenFileName', choose)
    editor.document_button.click()
    assert documents == [(current.id, '선택 원본.FCStd')]
    assert '실행 파일 확인됨' in editor.program_status.text()


def test_consumer_rebind_displays_current_model_without_module_rollback(editor, monkeypatch):
    current = node()
    current.files['document'] = FileReference('original.FCStd', 'old')
    bind(editor, current)
    def consumer(_node_id, path):
        # Only exercise display rebind; G's real error/dirty contract needs its integration test.
        current.files['document'] = FileReference(path, 'new')
        bind(editor, current, state='missing')

    editor.document_registration_requested.connect(consumer)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: ('new.FCStd', ''))
    editor.document_button.click()
    assert editor.document_path.text() == current.files['document'].path == 'new.FCStd'
    assert editor.document_status.text() == '파일 누락'


@pytest.mark.parametrize('mode', ['dark', 'light'])
@pytest.mark.parametrize('width', [280, 720])
def test_inherited_theme_and_narrow_layout_keep_document_action_reachable(app, editor, tmp_path, mode, width):
    host = QDialog()
    layout = QVBoxLayout(host)
    layout.addWidget(editor)
    host.setStyleSheet(build_stylesheet(mode))
    current = node('solidcraft')
    current.files['document'] = FileReference('C:/' + '긴 한글 경로 공백/' * 12 + '최종 원본.sc', 'revision')
    bind(editor, current, state='stale')
    host.resize(width, 470)
    host.show()
    app.processEvents()
    try:
        assert host.width() == width
        assert editor.rect().contains(editor.program_choice.geometry())
        assert editor.rect().contains(editor.document_button.geometry())
        assert editor.document_path.toolTip() == current.files['document'].path
        colors = editor.document_path.palette()
        assert colors.color(QPalette.Text) != colors.color(QPalette.Base)
        assert host.grab().save(str(tmp_path / f'cad-editor-{mode}-{width}.png'))
    finally:
        host.close()
        layout.removeWidget(editor)
        editor.setParent(None)
        host.deleteLater()
