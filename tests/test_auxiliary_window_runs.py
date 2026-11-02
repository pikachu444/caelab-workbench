"""Qt interaction contracts; these checks do not prove Windows OS rendering."""

import copy
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QFileDialog

from apps.workbench_qt.main_window import MainWindow
from caelab.jobs import RunActivity
from caelab.native.run_store import NativeRun
from caelab.solvers.base import ResultSet
from caelab.workbench import Workbench
from conftest import cantilever_model


class RunWorkbench(Workbench):
    def __init__(self):
        super().__init__()
        self.records, self.payloads, self.saved, self.cancelled = [], {}, [], []
        self.selected_id = None
        self.active_id = None
        self.active = False
        self.cleanup_error = ''

    def run(self, *args):
        pytest.fail('GUI used synchronous solver entry')

    def save_project(self, model, folder):
        self.saved.append(Path(folder))
        return super().save_project(model, folder)

    def submit_model(self, model, folder, solver_id='calculix', timeout=3600):
        run_id = f'{len(self.records) + 1:032x}'
        run = NativeRun(run_id, 'structural-model', Path(folder) / 'runs' / run_id, state='running')
        result = ResultSet('calculix', True, max_displacement=.0002,
                           displacements={node: [0, -.0002, 0] for node in model.mesh.nodes},
                           reaction_totals={'fy': 1000.0}, units={'length': 'm', 'force': 'N'})
        self.records.append(run)
        self.payloads[run_id] = SimpleNamespace(run=run, model=copy.deepcopy(model), result=result)
        self.selected_id = self.active_id = run_id
        self.active = True
        return run

    def finish(self):
        self.active = False
        if self.active_id:
            run = self.native_run_snapshot(self.active_id)
            run.state, run.parse_state, run.finished_at = 'completed', 'readable', 'finished'
            run.returncode = 0

    def model_runs(self, folder, model=None): return list(self.records)
    def selected_model_run(self, folder):
        return next((run for run in self.records if run.id == self.selected_id), None)
    def select_model_run(self, folder, run_id):
        self.selected_id = run_id
        return self.native_run_snapshot(run_id)
    def model_run_result(self, folder, run_id): return self.payloads[run_id]
    def model_run_is_current(self, folder, run_id, model):
        return model.to_dict() == self.payloads[run_id].model.to_dict()
    def native_run_snapshot(self, run_id):
        return next((run for run in self.records if run.id == run_id), None)
    def native_run_activity(self, run_id):
        return RunActivity(self.active if run_id == self.active_id else False, self.cleanup_error)
    def cancel_native_run(self, run_id):
        self.cancelled.append(run_id)
        return True
    def close(self): pytest.fail('window closed a shared Workbench')


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv('CAELAB_UI_SETTINGS_PATH', str(tmp_path / 'ui.ini'))
    app = QApplication.instance() or QApplication([])
    wb = RunWorkbench()
    window = MainWindow(wb)
    window.set_model(cantilever_model())
    project = tmp_path / 'saved project'
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **kw: str(project))
    import apps.workbench_qt.main_window as module
    if hasattr(module, 'tempfile'):
        monkeypatch.setattr(module.tempfile, 'mkdtemp', lambda **kw: str(tmp_path / 'unexpected temporary'))
    yield window, wb, app, project
    wb.cleanup_error = ''
    wb.finish()
    if hasattr(window, '_poll_run'):
        window._poll_run()
    MainWindow.close(window)
    app.processEvents()


def test_cancelled_first_location_creates_no_project_and_submits_nothing(window, monkeypatch):
    widget, wb, _, folder = window
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **kw: '')
    widget.run_solve()
    assert widget.project_folder is None and not folder.exists()
    assert not wb.saved and not wb.records


def test_run_returns_to_qt_rejects_duplicates_and_cancels_only_its_owned_id(window):
    widget, wb, app, folder = window
    widget.run_solve()
    assert widget.project_folder == folder and len(wb.records) == 1
    assert not widget.run_action.isEnabled() and widget.cancel_action.isEnabled()
    heartbeat = []
    QTimer.singleShot(0, lambda: heartbeat.append(True))
    app.processEvents()
    assert heartbeat and wb.active
    widget.run_solve()
    assert len(wb.records) == 1
    widget.cancel_run()
    assert wb.cancelled == [wb.active_id]


def test_past_selection_rebuilds_its_model_and_late_completion_keeps_that_selection(window, monkeypatch):
    widget, wb, _, folder = window
    folder.mkdir()
    old_model = cantilever_model()
    old_model.name = 'past model'
    past = wb.submit_model(old_model, folder)
    wb.finish()
    widget.run_solve()
    current = wb.active_id
    calls = []
    monkeypatch.setattr(widget.viewport, 'show_model', lambda model: calls.append(('model', model.name)))
    monkeypatch.setattr(widget.viewport, 'show_results', lambda model, result: calls.append(('result', model.name)))
    assert widget.select_path(f'runs.{past.id}')
    assert calls[-2:] == [('model', 'past model'), ('result', 'past model')]
    assert widget.model.name != 'past model'
    before = list(calls)
    wb.finish()
    widget._poll_run()
    assert wb.selected_id == past.id and current != past.id
    assert widget._last_result is wb.payloads[past.id].result and calls == before


def test_unit_only_model_change_refuses_late_automatic_result_display(window):
    widget, wb, _, _ = window
    widget.run_solve()
    original = widget.model
    widget.set_model(cantilever_model())
    assert widget.model is original
    original.unit_system = 'mm-tonne-s'
    wb.finish()
    widget._poll_run()
    assert widget._last_result is None


def test_close_waits_for_own_worker_even_after_receipt_is_finished(window, monkeypatch):
    widget, wb, _, _ = window
    widget.run_solve()
    event = QCloseEvent()
    widget.closeEvent(event)
    assert not event.isAccepted() and wb.cancelled == [wb.active_id]
    run = wb.native_run_snapshot(wb.active_id)
    run.finished_at = 'receipt saved, lease still held'
    closed = []
    monkeypatch.setattr(widget, 'close', lambda: closed.append(True))
    widget._poll_run()
    assert not closed
    wb.finish()
    widget._poll_run()
    assert closed == [True]


def test_cleanup_failure_stops_automatic_close_with_reason(window):
    widget, wb, _, _ = window
    widget.run_solve()
    event = QCloseEvent()
    widget.closeEvent(event)
    wb.finish()
    wb.cleanup_error = 'owned lease cleanup failed'
    widget._poll_run()
    assert not widget._run_timer.isActive() and not widget._close_pending
    assert 'owned lease cleanup failed' in widget.status.text()


def test_failed_run_with_readable_fields_is_not_displayed_as_completed_calculation(window, monkeypatch):
    widget, wb, _, _ = window
    widget.run_solve()
    wb.finish()
    run = wb.native_run_snapshot(wb.active_id)
    run.state, run.error = 'failed', 'cleanup did not complete'
    deformed = []
    monkeypatch.setattr(widget.viewport, 'show_results', lambda *a: deformed.append(True))
    widget._poll_run()
    assert not deformed and not widget.legend_label.text()
    assert '계산 완료' not in widget.status.text() and '실패' in widget.status.text()


@pytest.mark.parametrize('return_action', ['mesh', 'model'])
def test_return_to_current_model_clears_past_units_fields_and_status(window, return_action):
    widget, wb, _, folder = window
    folder.mkdir()
    old = cantilever_model()
    old.name, old.unit_system = 'past non-SI model', 'mm-tonne-s'
    past = wb.submit_model(old, folder)
    wb.payloads[past.id].result.units = {}
    wb.finish()
    widget.run_solve()
    assert widget.select_path(f'runs.{past.id}')
    assert 'mm-tonne-s' in widget.unit_system_status.text()
    assert 'past non-SI model' in widget.statusBar().currentMessage()
    if return_action == 'mesh':
        widget.set_module('메시')
        next(action for action in widget.context_bar.actions() if action.text() == '메시 다시 표시').trigger()
    else:
        widget.select_path('model')
    assert widget._displayed_run_id is None and widget._last_result is None
    assert not widget.legend_label.text() and not widget.statusBar().currentMessage()
    assert 'SI (m, kg, s, Pa)' in widget.unit_system_status.text()
    assert 'mm-tonne-s' not in widget.unit_system_status.text()
    assert widget.props_title.text() == '모델'
