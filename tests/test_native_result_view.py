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
        assert view.component.itemText(0) == '응력 (S) · Sxx'
        assert view.component.itemText(1) == '응력 (S) · Syy'
        grid = view.native_grid
        assert vtk_to_numpy(grid.GetPointData().GetArray('NativeNodeId')).tolist() == [40, 10, 90, 25]
        assert vtk_to_numpy(grid.GetCellData().GetArray('NativeElementId')).tolist() == [731]
        assert grid.GetNumberOfCells() == 1 and grid.GetPointData().GetArray('DISP') is None
        raw = vtk_to_numpy(grid.GetPointData().GetArray('STRESS'))
        assert raw[:2].tolist() == [[12, 2], [5, 3]]
        assert np.isnan(raw[2:]).all()
        assert vtk_to_numpy(grid.GetPointData().GetArray('Coverage_STRESS')).tolist() == [1, 1, 0, 0]
        assert view.notice.text() == '유효 절점 2 / 4'
        assert '단위 미선언' in view.source.text()
        assert ('STRESS', 'vm') not in [view.component.itemData(i) for i in range(view.component.count())]
        assert not view.scale_combo.isEnabled()
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
        view.scale_combo.setCurrentIndex(view.scale_combo.findData('auto'))
        (run.workdir / run.result['frames'][1]['vtu_file']).write_bytes(b'tampered')
        view.set_selection(run, 1)
        assert view.native_grid is None and view.component.count() == 0
        assert view.scale_combo.currentData() == 0.0 and not view.scale_combo.isEnabled()
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


def test_all_uncovered_values_have_no_visible_numeric_legend(app, tmp_path):
    run = make_run(tmp_path)
    text = _geometry() + _field([(999, [7])], name='CUSTOM', components=[('V1', 1, 0, 0, 0)]) + ' 9999\n'
    (run.workdir / 'model.frd').write_text(text, encoding='ascii')
    run.result = collect_results(run.workdir)
    run.parse_state = run.result['status']
    run.snapshot['artifacts'] = {name: file_revision(run.workdir / name) for name in run.result['exchange_files']}
    view = NativeResultView(Workbench().read_native_frame)
    view.viewport.available = True
    view.viewport._renderer = vtk.vtkRenderer()
    view.viewport._render = lambda: None
    try:
        view.set_selection(run, 0)
        wait_for(app, lambda: view.frame_data is not None)
        assert np.isnan(vtk_to_numpy(view.native_grid.GetPointData().GetArray('CUSTOM'))).all()
        assert view.viewport._scalar_bar is None
        assert view.viewport._renderer.GetActors2D().GetNumberOfItems() == 0
        assert '유한값 없음' in view.notice.text()
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


@pytest.mark.parametrize('frame_count', [1, 2])
def test_explicit_project_reload_retries_the_same_recovered_frame(app, tmp_path, monkeypatch, frame_count):
    run = make_run(tmp_path)
    run.result['frames'] = run.result['frames'][:frame_count]
    run.result['summary']['frame_index'] = frame_count - 1
    wb = Workbench()
    project = wb.new_native_project('read recovery')
    node = wb.add_program_node(project, 'analysis', 'calculix')
    wb.set_node_file(project, node.id, run.input_path, units=run.units, unit_source=run.unit_source)
    wb.save_native_project(project, tmp_path / 'project.json')
    run.node_id = node.id
    store = RunStore(project.run_store)
    store.write(run)
    store.select(node.id, run.id)
    real_read = wb.read_native_frame
    calls = []
    blocked = [frame_count == 1]
    def read(run, frame):
        calls.append((run.id, frame))
        if blocked[0]:
            raise PermissionError('transient read denial')
        return real_read(run, frame)
    monkeypatch.setattr(wb, 'read_native_frame', read)
    window = NativeAnalysisWindow(project.path, node.id, wb)
    try:
        if frame_count == 2:
            wait_for(app, lambda: window.native_view.frame_data is not None)
            blocked[0] = True
            window.frame_selector.setCurrentIndex(0)
        wait_for(app, lambda: 'transient read denial' in window.native_view.source.text())
        previous_calls = len(calls)
        blocked[0] = False
        window.refresh_runs()
        assert len(calls) == previous_calls
        window.reload_project()
        wait_for(app, lambda: window.native_view.frame_data is not None)
        assert calls[-2:] == [(run.id, 0), (run.id, 0)]
        assert len(calls) == previous_calls + 1
        assert window.native_view.frame_data.run_id == run.id
        assert window.frame_selector.currentData() == window.native_view.frame_data.frame['index'] == 0
        window.refresh_runs()
        assert len(calls) == previous_calls + 1
    finally:
        dispose(window, app)


@pytest.mark.parametrize('units,length_unit,stress_unit', [
    ({'length': 'mm', 'force': 'N'}, 'mm', 'MPa'),
    ({'length': 'm', 'force': 'N'}, 'm', 'Pa'),
    ({}, None, None),
])
def test_engineering_contours_warp_and_constant_legend_preserve_native_data(app, tmp_path, units, length_unit, stress_unit):
    from dataclasses import replace
    from vtk.util.numpy_support import numpy_to_vtk
    from apps.workbench_qt.native_result_view import _read_grid

    run = make_run(tmp_path)
    data = Workbench().read_native_frame(run, 0)
    grid = _read_grid(data)
    fields = []
    # Deliberately permute components: engineering axes must follow names, not indices.
    for name, names, values in (
        ('DISP', ['D3', 'D1', 'D2'], [[0, 3, 4], [0, 3, 4], [0, np.nan, 4], [0, 0, 0]]),
        ('STRESS', ['SXY', 'SZX', 'SZZ', 'SXX', 'SYZ', 'SYY'],
         [[0, 0, 0, 100, 0, 0], [50, 0, 0, 0, 0, 0], [0, 0, 12, 12, 0, 12], [0, 0, 0, np.nan, 0, 0]]),
    ):
        array = numpy_to_vtk(np.array(values, dtype=float), deep=True)
        array.SetName(name)
        for index, label in enumerate(names):
            array.SetComponentName(index, label)
        grid.GetPointData().RemoveArray(name)
        grid.GetPointData().AddArray(array)
        fields.append({'name': name, 'components': [{'name': label} for label in names]})
    frame = dict(data.frame, fields=fields, step=None, increment=None)
    data = replace(data, frame=frame, units=units, stale=True, stale_reason='입력 변경')
    original_points = vtk_to_numpy(grid.GetPoints().GetData()).copy()
    original_disp = vtk_to_numpy(grid.GetPointData().GetArray('DISP')).copy()
    view = NativeResultView(lambda *_: None)
    view.viewport.available = True
    view.viewport._renderer = vtk.vtkRenderer()
    view.viewport._render = lambda: None
    try:
        view._install(data, grid)
        assert view.component.currentData() == ('DISP', 'mag')
        np.testing.assert_allclose(vtk_to_numpy(view.viewport.display_grid.GetPointData().GetScalars()),
                                   [5, 5, np.nan, 0], equal_nan=True)
        suffix = f' ({length_unit})' if length_unit else ''
        title = r'$\vert U\vert$' if vtk.vtkTextRenderer.GetInstance().MathTextIsSupported() else '|U|'
        assert view.viewport._scalar_bar.GetTitle() == title + suffix
        assert '오래된 실행' in view.source.text() and 'None' not in view.source.text()
        assert all(token not in view.source.text() for token in (run.id, 'readable', 'declared by user', '\n'))
        assert view.scale_combo.isEnabled() == bool(length_unit)
        assert view.scale_combo.currentData() == 0.0
        camera = view.viewport._renderer.GetActiveCamera()
        camera.SetPosition(9, 8, 7)
        view.scale_combo.setCurrentIndex(view.scale_combo.findData(1.0))
        expected = original_points.copy()
        if length_unit:
            expected[[0, 1]] += [3, 4, 0]
        np.testing.assert_allclose(vtk_to_numpy(view.viewport.display_grid.GetPoints().GetData()), expected)
        assert camera.GetPosition() == (9, 8, 7)
        view.scale_combo.setCurrentIndex(view.scale_combo.findData('auto'))
        size = max(np.ptp(original_points, axis=0))
        expected = original_points.copy()
        if length_unit:
            expected[[0, 1]] += np.array([3, 4, 0]) * (.25 * size / 5)
        np.testing.assert_allclose(vtk_to_numpy(view.viewport.display_grid.GetPoints().GetData()), expected)
        view.component.setCurrentIndex(next(i for i in range(view.component.count())
                                            if view.component.itemData(i) == ('DISP', 0)))
        assert view.viewport._scalar_bar.GetTitle() == 'Uz' + suffix
        assert view.viewport._scalar_bar.GetNumberOfLabels() == 2
        view.component.setCurrentIndex(next(i for i in range(view.component.count())
                                            if view.component.itemData(i) == ('STRESS', 'vm')))
        np.testing.assert_allclose(vtk_to_numpy(view.viewport.display_grid.GetPointData().GetScalars()),
                                   [100, np.sqrt(3) * 50, 0, np.nan], equal_nan=True)
        assert view.viewport._scalar_bar.GetTitle() == 'Von Mises' + (f' ({stress_unit})' if stress_unit else '')
        assert view.notice.text() == '유효 절점 3 / 4'
        view.component.setCurrentIndex(next(i for i in range(view.component.count())
                                            if view.component.itemData(i) == ('STRESS', 3)))
        assert view.viewport._scalar_bar.GetTitle() == 'Sxx' + (f' ({stress_unit})' if stress_unit else '')
        np.testing.assert_array_equal(vtk_to_numpy(grid.GetPoints().GetData()), original_points)
        np.testing.assert_array_equal(vtk_to_numpy(grid.GetPointData().GetArray('DISP')), original_disp)
        assert grid.GetPointData().GetArray('__CAELabNativeViewComponent') is None
        view.stop()
        assert view.scale_combo.currentData() == 0.0 and not view.scale_combo.isEnabled()
    finally:
        dispose(view, app)


@pytest.mark.parametrize('field,component', [('DISP', 'D4'), ('STRESS', 'CUSTOM')])
def test_unknown_components_keep_raw_label_without_invented_units(app, tmp_path, field, component):
    from dataclasses import replace
    from vtk.util.numpy_support import numpy_to_vtk
    from apps.workbench_qt.native_result_view import _read_grid

    data = Workbench().read_native_frame(make_run(tmp_path), 0)
    grid = _read_grid(data)
    grid.GetPointData().RemoveArray('DISP')
    array = numpy_to_vtk(np.ones(4), deep=True)
    array.SetName(field)
    array.SetComponentName(0, component)
    grid.GetPointData().AddArray(array)
    data = replace(data, frame=dict(data.frame, fields=[{'name': field, 'components': [{'name': component}]}]))
    view = NativeResultView(lambda *_: None)
    view.viewport.available = True
    view.viewport._renderer = vtk.vtkRenderer()
    view.viewport._render = lambda: None
    try:
        view._install(data, grid)
        assert view.component.currentText() == f'{field} · {component}'
        assert view.viewport._scalar_bar.GetTitle() == f'{field} · {component}'
        assert not view.scale_combo.isEnabled()
    finally:
        dispose(view, app)


def test_large_finite_displacement_magnitude_and_auto_warp(app, tmp_path):
    from vtk.util.numpy_support import numpy_to_vtk
    from apps.workbench_qt.native_result_view import _read_grid

    data = Workbench().read_native_frame(make_run(tmp_path), 0)
    grid = _read_grid(data)
    coordinates = vtk_to_numpy(grid.GetPoints().GetData()).copy()
    displacement = numpy_to_vtk(np.tile([1e200, 0., 0.], (4, 1)), deep=True)
    displacement.SetName('DISP')
    for index, name in enumerate(('D1', 'D2', 'D3')):
        displacement.SetComponentName(index, name)
    grid.GetPointData().RemoveArray('DISP')
    grid.GetPointData().AddArray(displacement)
    view = NativeResultView(lambda *_: None)
    try:
        view._install(data, grid)
        values = vtk_to_numpy(view.viewport.display_grid.GetPointData().GetScalars())
        np.testing.assert_allclose(values, [1e200] * 4)
        view.scale_combo.setCurrentIndex(view.scale_combo.findData('auto'))
        size = max(np.ptp(coordinates, axis=0))
        np.testing.assert_allclose(vtk_to_numpy(view.viewport.display_grid.GetPoints().GetData()),
                                   coordinates + [.25 * size, 0, 0])
    finally:
        dispose(view, app)
