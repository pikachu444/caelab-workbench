"""Expert view settings without provider requests or visible windows."""

import importlib
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QCoreApplication, QSettings


@pytest.fixture
def expert(tmp_path, monkeypatch):
    monkeypatch.setenv('CAELAB_UI_SETTINGS_PATH', str(tmp_path / 'theme.ini'))
    application_before = QCoreApplication.instance()
    module = importlib.import_module('apps.workbench_qt.expert_window')
    assert QCoreApplication.instance() is application_before
    return module


def test_explicit_ini_preserves_unicode_values(expert, tmp_path, monkeypatch):
    path = tmp_path / '한글 읽기 위치.ini'
    monkeypatch.setenv('CAELAB_EXPERT_SETTINGS_PATH', str(path))
    settings = expert._view_settings()
    assert settings.format() == QSettings.IniFormat
    assert Path(settings.fileName()).resolve() == path.resolve()
    settings.setValue('문서/위치', '보존한 값')
    settings.sync()
    assert settings.status() == QSettings.NoError
    assert expert._view_settings().value('문서/위치') == '보존한 값'


def test_missing_or_blank_override_preserves_default(expert, monkeypatch):
    calls = []
    sentinel = object()

    class SettingsFactory:
        UserScope = QSettings.UserScope
        defaultFormat = staticmethod(QSettings.defaultFormat)

        def __new__(cls, *args):
            calls.append(args)
            return sentinel

    monkeypatch.setattr(expert, 'QSettings', SettingsFactory)
    for value in (None, '   '):
        if value is None:
            monkeypatch.delenv('CAELAB_EXPERT_SETTINGS_PATH', raising=False)
        else:
            monkeypatch.setenv('CAELAB_EXPERT_SETTINGS_PATH', value)
        assert expert._view_settings() is sentinel
    expected = (QSettings.defaultFormat(), QSettings.UserScope, 'CAELAB', 'ExpertChat')
    assert calls == [expected, expected]


def test_injected_settings_take_priority(expert, tmp_path, monkeypatch):
    ignored, path = tmp_path / '환경.ini', tmp_path / '주입 설정.ini'
    monkeypatch.setenv('CAELAB_EXPERT_SETTINGS_PATH', str(ignored))
    injected = QSettings(str(path), QSettings.IniFormat)
    assert expert._view_settings(injected) is injected
    injected.setValue('anchor', 17)
    injected.sync()
    assert QSettings(str(path), QSettings.IniFormat).value('anchor', type=int) == 17
    assert not ignored.exists()


def test_view_state_roundtrips_through_the_selected_ini(expert, tmp_path, monkeypatch):
    path = tmp_path / '한글 보기 상태.ini'
    monkeypatch.setenv('CAELAB_EXPERT_SETTINGS_PATH', str(path))
    document = tmp_path / '한글 대화.json'
    cursor = SimpleNamespace(position=lambda: 17)
    source = SimpleNamespace(
        path=document, view_settings=expert._view_settings(), isVisible=lambda: True,
        transcript=SimpleNamespace(
            cursorForPosition=lambda point: cursor,
            cursorRect=lambda value: SimpleNamespace(top=lambda: -3),
            verticalScrollBar=lambda: SimpleNamespace(maximum=lambda: 120, value=lambda: 30),
        ),
    )
    source._view_key = lambda: expert.ExpertWindow._view_key(source)
    expert.ExpertWindow._save_view_state(source)
    assert path.is_file()
    anchors = []
    restored = SimpleNamespace(
        path=document, view_settings=expert._view_settings(), isVisible=lambda: False,
        transcript=SimpleNamespace(restore_anchor=lambda *values: anchors.append(values)),
    )
    restored._view_key = lambda: expert.ExpertWindow._view_key(restored)
    expert.ExpertWindow._read_view_state(restored)
    assert restored._pending_view == (17, -3, False)
    assert restored.transcript.follow_tail is False
    expert.ExpertWindow._restore_view_state(restored)
    assert anchors == [(17, -3, False)]
    assert restored._pending_view is None
    assert not document.exists()


def test_import_preserves_existing_application(request):
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    request.getfixturevalue('expert')
    assert QCoreApplication.instance() is application
