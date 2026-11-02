"""Opt-in MDO layout settings without visible windows or native registry writes."""

import importlib
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QByteArray, QCoreApplication, QSettings


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setenv('MPLCONFIGDIR', str(tmp_path / 'matplotlib-cache'))
    monkeypatch.setenv('CAELAB_UI_SETTINGS_PATH', str(tmp_path / 'theme.ini'))
    application_before = QCoreApplication.instance()
    module = importlib.import_module('apps.workbench_qt.mdo_studio.window')
    assert QCoreApplication.instance() is application_before
    return module


def test_explicit_ini_preserves_unicode_values(studio, tmp_path, monkeypatch):
    path = tmp_path / '한글 설정 공백.ini'
    monkeypatch.setenv('CAELAB_MDO_SETTINGS_PATH', str(path))
    settings = studio._layout_settings()
    assert settings.format() == QSettings.IniFormat
    assert Path(settings.fileName()).resolve() == path.resolve()
    settings.setValue('문서/위치', '보존한 값')
    settings.sync()
    assert settings.status() == QSettings.NoError
    assert path.is_file()
    assert studio._layout_settings().value('문서/위치') == '보존한 값'


def test_missing_or_blank_override_preserves_default(studio, monkeypatch):
    calls = []
    sentinel = object()
    monkeypatch.setattr(studio, 'QSettings', lambda *args: calls.append(args) or sentinel)
    for value in (None, '   '):
        if value is None:
            monkeypatch.delenv('CAELAB_MDO_SETTINGS_PATH', raising=False)
        else:
            monkeypatch.setenv('CAELAB_MDO_SETTINGS_PATH', value)
        assert studio._layout_settings() is sentinel
    assert calls == [('CAELab', 'MDO Studio')] * 2


def test_layout_save_and_restore_share_the_ini(studio, tmp_path, monkeypatch):
    path = tmp_path / '한글 레이아웃 공백.ini'
    monkeypatch.setenv('CAELAB_MDO_SETTINGS_PATH', str(path))
    layout, splitter = QByteArray(b'layout-state'), QByteArray(b'splitter-state')
    source = SimpleNamespace(saveState=lambda: layout,
                             center_splitter=SimpleNamespace(saveState=lambda: splitter))
    studio.MDOStudioWindow._save_layout(source)
    restored = []
    target = SimpleNamespace(
        _cancel_default_splitter=lambda: None,
        restoreState=lambda value: restored.append(('layout', value)) or True,
        center_splitter=SimpleNamespace(restoreState=lambda value: restored.append(('splitter', value)) or True),
        _restore_study_view=lambda: False,
        _request_default_splitter=lambda: pytest.fail('Persisted splitter was not restored'),
    )
    studio.MDOStudioWindow._restore_layout(target)
    assert restored == [('layout', layout), ('splitter', splitter)]
    assert path.is_file()


def test_import_preserves_existing_application(request):
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    request.getfixturevalue('studio')
    assert QCoreApplication.instance() is application
