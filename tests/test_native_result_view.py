"""The actual native window consumes one run/frame without auxiliary conversion."""
import copy
import os
import threading
import time
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from vtk.util.numpy_support import vtk_to_numpy

from apps.workbench_qt.native_analysis import NativeAnalysisWindow
from apps.workbench_qt.native_result_view import NativeResultView
from apps.workbench_qt.viewport import vtk
from caelab.native.results import collect_results
from caelab.native.run_store import RunStore
from caelab.project import file_revision
from caelab.workbench import Workbench
from test_native_evidence import make_run
from test_native_results import _field, _geometry


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv('CAELAB_UI_SETTINGS_PATH', str(tmp_path / 'ui.ini'))


def wait_for(app, condition):
    deadline = time.monotonic() + 8
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert condition()


def dispose(widget, app):
    if isinstance(widget, NativeResultView):
        widget.stop()
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def partial_stress_run(tmp_path):
    run = make_run(tmp_path)
    components = [('SXX', 4, 1, 1, 0), ('SYY', 4, 2, 2, 0)]
    text = _geometry() + _field([(40, [12, 2]), (10, [5, 3])], name='STRESS', components=components) + ' 9999\n'
    (run.workdir / 'model.frd').write_text(text, encoding='ascii')
    run.result = collect_results(run.workdir)
    run.parse_state = run.result['status']
    run.units = {}
    run.snapshot['artifacts'] = {name: file_revision(run.workdir / name)
                                 for name in ['model.frd', *run.result['exchange_files']]}
    return run


def test_partial_stress_without_displacement_keeps_native_mesh_ids_and_nan(app, tmp_path):
    run = partial_stress_run(tmp_path)
    view = NativeResultView(Workbench().read_native_frame)
    try:
        view.set_selection(run, 0)
        wait_for(app, lambda: view.frame_data is not None)
        assert view.component.itemText(0) == 'STRESS · SXX'
        assert view.component.itemText(1) == 'STRESS · SYY'
        grid = view.native_grid
        assert vtk_to_numpy(grid.GetPointData().GetArray('NativeNodeId')).tolist() == [40, 10, 90, 25]
        assert vtk_to_numpy(grid.GetCellData().GetArray('NativeElementId')).tolist() == [731]
        assert grid.GetNumberOfCells() == 1 and grid.GetPointData().GetArray('DISP') is None
        raw = vtk_to_numpy(grid.GetPointData().GetArray('STRESS'))
        assert raw[:2].tolist() == [[12, 2], [5, 3]]
        assert np.isnan(raw[2:]).all()
        assert vtk_to_numpy(grid.GetPointData().GetArray('Coverage_STRESS')).tolist() == [1, 1, 0, 0]
        assert '2 / 메시 절점 4' in view.notice.text()
        assert '단위 미선언' in view.source.text() and '위치 미확인' in view.notice.text()
        assert grid.GetPointData().GetArray('__CAELabNativeViewComponent') is None
        assert view.viewport.display_grid.GetPoint(0) == grid.GetPoint(0)
    finally:
        dispose(view, app)


def test_polling_same_selection_does_not_reread_and_failure_clears_previous(app, tmp_path):
    run = make_run(tmp_path)
    calls = []
    wb = Workbench()
    def load(run, index):
        calls.append((run.id, index))
        return wb.read_native_frame(run, index)
    view = NativeResultView(load)
    try:
        view.set_selection(run, 0)
        wait_for(app, lambda: view.frame_data is not None)
        for _ in range(4):
            view.set_selection(copy.deepcopy(run), 0)
            app.processEvents()
        assert calls == [(run.id, 0)]
        (run.workdir / run.result['frames'][1]['vtu_file']).write_bytes(b'tampered')
        view.set_selection(run, 1)
        assert view.native_grid is None and view.component.count() == 0
        wait_for(app, lambda: '해시' in view.source.text())
        assert view.viewport.display_grid is None
        view.set_selection(None, None)
        assert view.native_grid is None and view.frame_data is None
    finally:
        dispose(view, app)


def test_non_auxiliary_triangle_and_unknown_component_reach_mapper_without_gl(app, tmp_path):
    run = make_run(tmp_path)
    text = (_geometry(ids=(40, 10, 90), topology=7, connectivity=(40, 10, 90))
            + _field([(40, [7]), (10, [9])], name='CUSTOM', components=[('V1', 1, 0, 0, 0)]) + ' 9999\n')
    (run.workdir / 'model.frd').write_text(text, encoding='ascii')
    run.result = collect_results(run.workdir)
    run.parse_state = run.result['status']
    run.snapshot['artifacts'] = {name: file_revision(run.workdir / name) for name in run.result['exchange_files']}
    view = NativeResultView(Workbench().read_native_frame)
    # Exercise the real mapper and lookup table without a window or OpenGL render.
    view.viewport.available = True
    view.viewport._renderer = vtk.vtkRenderer()
    view.viewport._render = lambda: None
    try:
        view.set_selection(run, 0)
        wait_for(app, lambda: view.frame_data is not None)
        assert view.native_grid.GetCellType(0) == vtk.VTK_TRIANGLE
        assert view.component.currentText() == 'CUSTOM · V1'
        assert view.viewport._renderer.GetActors().GetNumberOfItems() == 1
        actor = view.viewport._renderer.GetActors().GetItemAsObject(0)
        assert actor.GetMapper().GetLookupTable().GetNanColor() == (.55, .55, .55, 1)
        assert vtk_to_numpy(view.viewport.display_grid.GetPointData().GetScalars())[:2].tolist() == [7, 9]
        view.set_selection(None, None)
        assert view.viewport._renderer.GetActors().GetNumberOfItems() == 0
        assert view.viewport._scalar_bar is None
    finally:
        dispose(view, app)


def test_recorded_hash_does_not_make_a_different_actual_frame_acceptable(app, tmp_path):
    run = make_run(tmp_path)
    run.result['frames'][0]['vtu_file'] = run.result['frames'][1]['vtu_file']
    view = NativeResultView(Workbench().read_native_frame)
    try:
        view.set_selection(run, 0)
        wait_for(app, lambda: '실제 프레임 번호' in view.source.text())
        assert view.frame_data is None and view.viewport.display_grid is None
    finally:
        dispose(view, app)


def test_slow_old_read_never_replaces_new_selection_or_closed_view(app, tmp_path):
    run = make_run(tmp_path)
    other_root = tmp_path / 'other-run'
    other_root.mkdir()
    other = make_run(other_root)
    entered, release = threading.Event(), threading.Event()
    calls = []
    wb = Workbench()
    def load(run, index):
        calls.append((run.id, index))
        if run.id != other.id:
            entered.set()
            assert release.wait(5)
        return wb.read_native_frame(run, index)
    view = NativeResultView(load)
    try:
        view.set_selection(run, 0)
        wait_for(app, entered.is_set)
        view.set_selection(other, 0)
        assert view.native_grid is None
        release.set()
        wait_for(app, lambda: view.frame_data is not None)
        assert calls == [(run.id, 0), (other.id, 0)]
        assert view.frame_data.run_id == other.id and view.frame_data.frame['index'] == 0
        values = vtk_to_numpy(view.native_grid.GetPointData().GetArray('DISP'))
        assert values[0].tolist() == [1, 0, 0]
        view.stop()
        view._poll()
        assert view.native_grid is None
    finally:
        release.set()
        dispose(view, app)


def test_real_workbench_runstore_selection_reaches_native_window_and_clears(app, tmp_path):
    run = make_run(tmp_path)
    wb = Workbench()
    project = wb.new_native_project('native frame bridge')
    node = wb.add_program_node(project, 'analysis', 'calculix')
    wb.set_node_file(project, node.id, run.input_path, units=run.units, unit_source=run.unit_source)
    wb.save_native_project(project, tmp_path / 'project.json')
    run.node_id = node.id
    store = RunStore(project.run_store)
    store.write(run)
    store.select(node.id, run.id)
    window = NativeAnalysisWindow(project.path, node.id, wb)
    try:
        view = window.native_view
        wait_for(app, lambda: view.frame_data is not None)
        assert view.frame_data.run_id == run.id and view.frame_data.frame['index'] == 1
        window.frame_selector.setCurrentIndex(0)
        wait_for(app, lambda: view.frame_data is not None and view.frame_data.frame['index'] == 0)
        assert vtk_to_numpy(view.native_grid.GetPointData().GetArray('DISP'))[0].tolist() == [1, 0, 0]
        failed = copy.deepcopy(run)
        failed.id = uuid.uuid4().hex
        failed.workdir = project.run_store / failed.id
        failed.snapshot, failed.log_path = {}, ''
        failed.state, failed.parse_state, failed.result = 'failed', 'missing', {}
        store.write(failed)
        window.refresh_runs()
        window.run_selector.setCurrentIndex(window.run_selector.findData(failed.id))
        assert view.frame_data is None and view.native_grid is None
        assert not wb.jobs.jobs and not wb.jobs._native
    finally:
        dispose(window, app)


def test_close_during_read_discards_the_late_result(app, tmp_path):
    run = make_run(tmp_path)
    entered, release = threading.Event(), threading.Event()
    def load(run, index):
        entered.set()
        assert release.wait(5)
        return Workbench().read_native_frame(run, index)
    view = NativeResultView(load)
    try:
        view.set_selection(run, 0)
        wait_for(app, entered.is_set)
        view.stop()
        release.set()
        view._worker.join(timeout=5)
        assert not view._worker.is_alive()
        view._poll()
        assert view.frame_data is None and view.viewport.display_grid is None
        assert view.component.count() == 0
    finally:
        release.set()
        dispose(view, app)
