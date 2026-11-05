"""Bounded diagnostics: source smoke and failure recording, never frozen proof."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from apps.workbench_qt import bundle_diagnostics as diagnostics


def test_existing_output_is_rejected_without_loading_qt_or_overwriting(tmp_path, monkeypatch):
    marker = tmp_path / 'existing.txt'
    marker.write_text('preserve me')
    monkeypatch.setattr(diagnostics, '_run', lambda *_: pytest.fail('initialized Qt'))
    assert diagnostics.main(['--output', str(tmp_path)]) == 2
    assert marker.read_text() == 'preserve me'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['existing.txt']


@pytest.mark.parametrize('args', [[], ['--output', 'relative'],
                                  ['--out', '{output}'],
                                  ['--output', '{output}', '--caelab-module', 'os'],
                                  ['--output', '{output}', '--script', 'arbitrary.py']])
def test_invalid_or_mixed_modes_reject_before_runtime(tmp_path, monkeypatch, args):
    output = tmp_path / 'new'
    args = [str(output) if a == '{output}' else a for a in args]
    monkeypatch.setattr(diagnostics, '_run', lambda *_: pytest.fail('initialized Qt'))
    try:
        result = diagnostics.main(args)
    except SystemExit as error:
        result = error.code
    assert result == 2 and not output.exists()


def test_success_record_is_not_committed_before_runtime_returns(tmp_path, monkeypatch):
    output = tmp_path / 'diagnostic'
    def runtime(path, report):
        assert json.loads((path / 'diagnostics.json').read_text())['status'] == 'starting'
        report['errors'].append('PNG verification failed')
        return 1
    monkeypatch.setattr(diagnostics, '_run', runtime)
    assert diagnostics.main(['--output', str(output)]) == 1
    saved = json.loads((output / 'diagnostics.json').read_text())
    assert saved['status'] == 'failed' and saved['errors'] == ['PNG verification failed']


def test_runtime_exception_preserves_failure_record(tmp_path, monkeypatch):
    def fail(*_):
        raise RuntimeError('renderer failed')
    monkeypatch.setattr(diagnostics, '_run', fail)
    output = tmp_path / 'failure'
    assert diagnostics.main(['--output', str(output)]) == 1
    result = json.loads((output / 'diagnostics.json').read_text())
    assert result['status'] == 'failed' and result['errors'] == ['RuntimeError: renderer failed']


def test_vtk_cleanup_failure_still_closes_owned_qt_widget():
    calls = []
    def failed_finalize():
        raise RuntimeError('finalize failed')
    checks, errors = {}, []
    diagnostics._close_owned(SimpleNamespace(Finalize=failed_finalize),
                             SimpleNamespace(close=lambda: calls.append('closed'), isVisible=lambda: False),
                             checks, errors)
    assert calls == ['closed'] and checks == {'widget_closed': True}
    assert errors == ['VTK cleanup failed: RuntimeError: finalize failed']


@pytest.mark.parametrize('kind', ['invalid', 'wrong_size', 'uniform', 'other_format'])
def test_png_validation_reads_real_saved_file(tmp_path, kind):
    from PySide6.QtGui import QImage
    path = tmp_path / 'image.png'
    if kind == 'invalid':
        path.write_bytes(b'not a PNG')
    else:
        image = QImage(64 if kind == 'wrong_size' else 128, 96, QImage.Format_RGB32)
        image.fill(0)
        if kind == 'other_format':
            image.setPixel(1, 1, 0xffffffff)
        assert image.save(str(path), 'BMP' if kind == 'other_format' else 'PNG')
    with pytest.raises(ValueError):
        diagnostics._verify_png(path, (128, 96))


def test_source_subprocess_paints_renders_and_exits_normally(tmp_path):
    output = tmp_path / 'source-smoke'
    environment = dict(os.environ)
    environment.pop('PYTHONPATH', None)
    environment.pop('PYTHONHOME', None)
    environment['QT_QPA_PLATFORM'] = 'minimal'
    result = subprocess.run([sys.executable, '-B', '-m', 'apps.workbench_qt.desktop_entry',
                             '--caelab-diagnostics', '--output', str(output)],
                            cwd=Path(__file__).resolve().parents[1], env=environment,
                            capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    report = json.loads((output / 'diagnostics.json').read_text('utf-8'))
    assert report['status'] == 'passed' and report['event_loop_exit_code'] == 0
    assert report['runtime']['frozen'] is False
    assert report['runtime']['qt_platform'] == 'offscreen'
    assert all(report['checks'].values()) and len(report['checks']) == 5
    assert report['qt_image']['decoded'] and report['vtk_image']['nonuniform']
    assert report['runtime']['vtk_native_module']
    assert environment['QT_QPA_PLATFORM'] == 'minimal'
