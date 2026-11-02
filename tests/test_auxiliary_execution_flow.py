"""Real owner/Qt/file flow with Python process probes and existing synthetic FRD.

No vendor solver, native-screen capture or physical benchmark runs here.
"""

import os
from pathlib import Path
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

from caelab.connections import LaunchRequest, PROGRAMS, ProgramStatus
from caelab.workbench import Workbench
from conftest import cantilever_model
from test_calculix_converter import _frd_block


@pytest.fixture
def probe(monkeypatch):
    import caelab.connections as connections
    from caelab.solvers.calculix import CalculiXConverter
    spec = next(item for item in PROGRAMS if item.id == 'calculix')
    monkeypatch.setattr(Workbench, 'programs', lambda self: [ProgramStatus(spec, sys.executable, source='Python probe')])
    monkeypatch.setattr(CalculiXConverter, 'run', lambda *a: pytest.fail('synchronous converter execution'))
    model = cantilever_model()
    displacement = {node: [0, -.0002, 0] for node in model.mesh.nodes}
    forces = {node: [0, 0, 0] for node in model.mesh.nodes}
    forces[model.sets['FIX'].members[0]] = [0, 1000, 0]
    frd = (_frd_block('DISP', displacement) + _frd_block('FORC', forces)
           + _frd_block('STRESS', {node: [0] * 6 for node in model.mesh.nodes}))
    settings = {'delays': {}, 'requests': [], 'mutate_input': False}
    def request(found, workdir):
        folder = Path(workdir).parent.parent.resolve()
        delay = settings['delays'].get(folder, .15)
        mutation = "Path('model.inp').write_bytes(Path('model.inp').read_bytes()+b'\\n'); " if settings['mutate_input'] else ''
        code = ("import time; from pathlib import Path; "
                "assert Path('model.inp').is_file(); "
                "Path('probe-started').write_text('started'); "
                f"time.sleep({delay!r}); {mutation}Path('model.frd').write_text({frd!r},encoding='ascii'); "
                "print('synthetic result written',flush=True)")
        settings['requests'].append(Path(workdir))
        return LaunchRequest([sys.executable, '-c', code], Path(workdir), dict(os.environ), 'calculix')
    monkeypatch.setattr(connections, 'calculix_run_request', request)
    return settings


def wait_for(predicate, *, app=None, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    pytest.fail('probe flow did not finish')


def wait_run(wb, run_id, app=None):
    wait_for(lambda: not wb.native_run_activity(run_id).active, app=app)
    activity = wb.native_run_activity(run_id)
    assert not activity.cleanup_error
    return wb.native_run_snapshot(run_id)


def test_input_changed_inside_owned_process_cannot_publish_original_model_result(tmp_path, probe):
    probe['mutate_input'] = True
    wb = Workbench()
    try:
        model = cantilever_model()
        wb.save_project(model, tmp_path)
        run = wait_run(wb, wb.submit_model(model, tmp_path).id)
        assert run.returncode == 0 and run.parse_state == 'failed'
        assert 'model.inp' in run.error
        assert not (run.workdir / 'model-result.json').exists()
        with pytest.raises(ValueError, match=r'model\.inp'):
            wb.model_run_result(tmp_path, run.id)
    finally:
        assert wb.close()


def test_real_owner_keeps_frozen_units_failed_history_and_reopens_without_execution(tmp_path, probe):
    folder = tmp_path / '영구 모델'
    model = cantilever_model()
    wb = Workbench()
    try:
        wb.save_project(model, folder)
        submitted = wb.submit_model(model, folder)
        model.unit_system = 'mm-tonne-s'
        run = wait_run(wb, submitted.id)
        assert run.state == 'completed' and run.parse_state == 'readable' and run.returncode == 0
        payload = wb.model_run_result(folder, run.id)
        assert payload.model.unit_system == 'si' and payload.result.units == {'length': 'm', 'force': 'N'}
        assert payload.result.reaction_totals['fy'] == pytest.approx(1000)
        assert set(payload.result.displacements) == set(payload.model.mesh.nodes)
        assert wb.model_runs(folder, model)[0].stale
        model.sections[0].material = 'MISSING'
        wb.save_project(model, folder)
        failed = wb.submit_model(model, folder)
        failed = wait_run(wb, failed.id)
        assert failed.state == 'failed' and not (failed.workdir / 'probe-started').exists()
        failed_payload = wb.model_run_result(folder, failed.id)
        assert failed_payload.model.unit_system == 'mm-tonne-s' and failed_payload.result is None
        wb.select_model_run(folder, run.id)
        requests = len(probe['requests'])
        reopened = Workbench()
        assert reopened.open_project(folder).unit_system == 'mm-tonne-s'
        assert len(reopened.model_runs(folder)) == 2
        assert reopened.selected_model_run(folder).id == run.id
        assert reopened.model_run_result(folder, run.id).model.unit_system == 'si'
        assert len(probe['requests']) == requests == 1
    finally:
        assert wb.close()


@pytest.mark.parametrize('tampered', ['model-result.json', 'model.inp', 'model.frd', 'job.log'])
def test_actual_owner_qt_completion_reopen_and_tampered_result_refusal(tmp_path, monkeypatch, probe, tampered):
    pytest.importorskip('PySide6')
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QFileDialog
    from apps.workbench_qt.main_window import MainWindow
    monkeypatch.setenv('CAELAB_UI_SETTINGS_PATH', str(tmp_path / 'ui.ini'))
    app = QApplication.instance() or QApplication([])
    folder = tmp_path / 'Qt 프로젝트'
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **kw: str(folder))
    wb = Workbench()
    window = MainWindow(wb)
    window.set_model(cantilever_model())
    heartbeat = QTimer()
    heartbeat.setInterval(5)
    beats = []
    heartbeat.timeout.connect(lambda: beats.append(True))
    reopened = None
    try:
        heartbeat.start()
        window.run_solve()
        assert window._active_run_id is not None
        run_id = window._active_run_id
        wait_for(lambda: window._active_run_id is None, app=app)
        assert len(beats) >= 2 and '계산 완료' in window.status.text(), window.log.toPlainText()
        assert window._last_result.success and window._last_result.max_displacement == pytest.approx(.0002)
        assert window.project_folder == folder and (folder / 'model.json').is_file()
        window.close()
        fresh = Workbench()
        monkeypatch.setattr(fresh, 'submit_model', lambda *a, **kw: pytest.fail('reopen submitted a calculation'))
        reopened = MainWindow(fresh)
        reopened.set_model(fresh.open_project(folder), folder)
        assert reopened._last_result.success and reopened._displayed_run_id == run_id
        assert len(probe['requests']) == 1
        result_path = folder / 'runs' / run_id / tampered
        result_path.write_bytes(result_path.read_bytes() + b'\n')
        reopened._last_result = None
        reopened.select_path('model')
        reopened.select_path(f'runs.{run_id}')
        assert reopened._last_result is None and '해시' in reopened.status.text()
    finally:
        heartbeat.stop()
        wb.close()
        if window._active_run_id is not None:
            window._poll_run()
        window.close()
        if reopened is not None:
            reopened.close()
        app.processEvents()


def test_qt_close_cancels_only_its_model_run_and_preserves_other_store(tmp_path, monkeypatch, probe):
    pytest.importorskip('PySide6')
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QApplication, QFileDialog
    from apps.workbench_qt.main_window import MainWindow
    monkeypatch.setenv('CAELAB_UI_SETTINGS_PATH', str(tmp_path / 'ui.ini'))
    app = QApplication.instance() or QApplication([])
    own_folder, other_folder = tmp_path / 'own', tmp_path / 'other'
    probe['delays'][own_folder.resolve()] = 60
    probe['delays'][other_folder.resolve()] = 3
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **kw: str(own_folder))
    wb = Workbench()
    window = MainWindow(wb)
    window.set_model(cantilever_model())
    try:
        other_model = cantilever_model()
        other_model.name = 'other model'
        wb.save_project(other_model, other_folder)
        other = wb.submit_model(other_model, other_folder)
        window.run_solve()
        own_id = window._active_run_id
        own = wb.native_run_snapshot(own_id)
        wait_for(lambda: (own.workdir / 'probe-started').is_file(), app=app)
        before = (own_folder / 'model.json').read_bytes()
        with pytest.raises(ValueError):
            wb.save_project(cantilever_model(), own_folder)
        assert (own_folder / 'model.json').read_bytes() == before
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        wait_for(lambda: window._active_run_id is None, app=app)
        assert wb.native_run_activity(other.id).active
        cancelled = wb.native_run_snapshot(own_id)
        assert cancelled.state == 'cancelled'
        assert wb.model_run_result(own_folder, own_id).result is None
        assert wait_run(wb, other.id, app).state == 'completed'
        assert wb.model_run_result(other_folder, other.id).model.name == 'other model'
    finally:
        wb.close()
        if window._active_run_id is not None:
            window._poll_run()
        window.close()
        app.processEvents()
