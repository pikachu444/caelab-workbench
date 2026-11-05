"""Read-only native VTU display; no auxiliary model or derived physical fields."""
from __future__ import annotations

import copy
from dataclasses import replace
import queue
import threading

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QLabel, QVBoxLayout, QWidget

from .viewport import Viewport, vtk


def _read_grid(data):
    if vtk is None:
        raise ValueError('VTK를 사용할 수 없어 네이티브 프레임을 표시할 수 없습니다.')
    reader = vtk.vtkXMLUnstructuredGridReader()
    errors = []
    reader.AddObserver('ErrorEvent', lambda *_: errors.append(True))
    reader.ReadFromInputStringOn()
    reader.SetInputString(data.payload)
    reader.Update()
    grid = vtk.vtkUnstructuredGrid()
    grid.ShallowCopy(reader.GetOutput())
    index = grid.GetFieldData().GetArray('FrameIndex')
    if (errors or reader.GetErrorCode() or grid.GetNumberOfPoints() == 0
            or grid.GetNumberOfCells() == 0 or index is None
            or index.GetNumberOfTuples() != 1 or index.GetTuple1(0) != data.frame['index']):
        raise ValueError('VTU 메시 또는 실제 프레임 번호가 선택한 실행 기록과 일치하지 않습니다.')
    for attributes, name, size in ((grid.GetPointData(), 'NativeNodeId', grid.GetNumberOfPoints()),
                                   (grid.GetCellData(), 'NativeElementId', grid.GetNumberOfCells())):
        ids = attributes.GetArray(name)
        if ids is None or ids.GetNumberOfTuples() != size:
            raise ValueError('VTU에 원 절점/요소 ID가 보존되어 있지 않습니다.')
    return grid


def _load_frame(load, run, frame_index, completed, token):
    try:
        data = load(run, frame_index)
        grid = _read_grid(data)
        answer = (replace(data, payload=b''), grid, None)
    except Exception as error:
        answer = (None, None, error)
    completed.put((token, answer))


class _NativeViewport(Viewport):
    def clear_native(self):
        self.display_grid = None
        self._scalar_bar = None
        self._edge_actor = None
        if self._renderer is not None:
            self._renderer.RemoveAllViewProps()
            self._render()

    def show_component(self, original, name=None, component=0):
        from vtk.util.numpy_support import numpy_to_vtk, vtk_to_numpy

        grid = vtk.vtkUnstructuredGrid()
        grid.ShallowCopy(original)
        finite_count = 0
        values = None
        if name is not None:
            source = grid.GetPointData().GetArray(name)
            if source is None or not 0 <= component < source.GetNumberOfComponents():
                raise ValueError('선택한 원 저장 성분이 VTU에 없습니다.')
            raw = vtk_to_numpy(source)
            values = raw if source.GetNumberOfComponents() == 1 else raw[:, component]
            values = np.array(values, dtype=float, copy=True)
            values[~np.isfinite(values)] = np.nan
            finite_count = int(np.isfinite(values).sum())
            scalar_name = '__CAELabNativeViewComponent'
            while grid.GetPointData().HasArray(scalar_name):
                scalar_name += '_'
            array = numpy_to_vtk(values, deep=True)
            array.SetName(scalar_name)
            grid.GetPointData().SetScalars(array)
        self.display_grid = grid
        self._scalar_bar = None
        if self.available:
            self._renderer.RemoveAllViewProps()
            mapper = vtk.vtkDataSetMapper()
            mapper.SetInputData(grid)
            if values is None:
                mapper.ScalarVisibilityOff()
            else:
                lut = vtk.vtkLookupTable()
                lut.SetNanColor(0.55, 0.55, 0.55, 1.0)
                valid = values[np.isfinite(values)]
                low, high = (float(valid.min()), float(valid.max())) if len(valid) else (0.0, 1.0)
                lut.SetRange(low, high)
                lut.Build()
                mapper.SetLookupTable(lut)
                mapper.SetScalarRange(low, high)
                mapper.SetScalarModeToUsePointData()
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().EdgeVisibilityOn()
            self._renderer.AddActor(actor)
            if values is not None and finite_count:
                legend = vtk.vtkScalarBarActor()
                legend.SetLookupTable(mapper.GetLookupTable())
                legend.SetTitle(f'{name} [{component}]')
                self._renderer.AddActor(legend)
                self._scalar_bar = legend
            self._renderer.ResetCamera()
            self._render()
        return finite_count


class NativeResultView(QWidget):
    """Only the latest selection may install background-read frame data."""
    def __init__(self, load, parent=None):
        super().__init__(parent)
        self._load = load
        self._key = None
        self._token = 0
        self._pending = None
        self._worker = None
        self._completed = queue.SimpleQueue()
        self._stopped = False
        self.frame_data = None
        self.native_grid = None
        layout = QVBoxLayout(self)
        self.source = QLabel()
        self.source.setTextFormat(Qt.PlainText)
        self.source.setWordWrap(True)
        layout.addWidget(self.source)
        self.component = QComboBox()
        self.component.setAccessibleName('네이티브 원 저장 성분')
        self.component.currentIndexChanged.connect(self._show_component)
        layout.addWidget(self.component)
        self.notice = QLabel()
        self.notice.setTextFormat(Qt.PlainText)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.viewport = _NativeViewport(self)
        layout.addWidget(self.viewport, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self._clear('표시할 네이티브 결과 프레임을 선택하세요.')

    def _clear(self, message):
        self.frame_data = self.native_grid = None
        self.component.blockSignals(True)
        self.component.clear()
        self.component.blockSignals(False)
        self.component.setEnabled(False)
        self.source.setText(message)
        self.notice.clear()
        self.viewport.clear_native()

    def set_selection(self, run, frame_index):
        if self._stopped:
            return
        frame = next((f for f in run.result.get('frames', []) if f.get('index') == frame_index), None) if run else None
        key = repr((run.id, frame_index, frame, run.snapshot.get('artifacts', {}), run.units,
                    run.unit_source, run.parse_state, run.stale, run.stale_reason)) if run and frame else None
        if key == self._key:
            return
        self._key = key
        self._token += 1
        self._pending = None
        self._clear('결과 프레임을 읽고 있습니다.' if key else '표시할 네이티브 VTU 프레임이 없습니다. 요약과 원본 로그를 확인하세요.')
        if key:
            self._pending = (self._token, copy.deepcopy(run), frame_index)
            self._start_pending()

    def _start_pending(self):
        if self._stopped or self._pending is None or (self._worker and self._worker.is_alive()):
            return
        token, run, frame_index = self._pending
        self._pending = None
        self._worker = threading.Thread(target=_load_frame,
                                        args=(self._load, run, frame_index, self._completed, token), daemon=True)
        self._worker.start()

    def _poll(self):
        while not self._completed.empty():
            token, (data, grid, error) = self._completed.get_nowait()
            if self._stopped or token != self._token:
                continue
            if error is not None:
                self._clear(f'결과 프레임을 표시하지 못했습니다: {error}')
                continue
            try:
                self._install(data, grid)
            except (ValueError, TypeError, KeyError) as error:
                self._clear(f'결과 프레임을 표시하지 못했습니다: {error}')
        self._start_pending()

    def _install(self, data, grid):
        self.frame_data, self.native_grid = data, grid
        self.component.blockSignals(True)
        self.component.clear()
        for field in data.frame.get('fields', []):
            array = grid.GetPointData().GetArray(field['name'])
            if array is None:
                continue
            stored = [c for c in field.get('components', []) if c.get('exist', 0) in (0, 2)]
            if len(stored) != array.GetNumberOfComponents():
                raise ValueError('원 저장 성분의 개수가 VTU와 실행 기록에서 다릅니다.')
            for index, item in enumerate(stored):
                if array.GetComponentName(index) != item['name']:
                    raise ValueError('원 저장 성분 이름/순서가 VTU와 실행 기록에서 다릅니다.')
                self.component.addItem(f"{field['name']} · {item['name']}", (field['name'], index))
        self.component.blockSignals(False)
        self.component.setEnabled(self.component.count() > 0)
        frame = data.frame
        units = ', '.join(f'{key}={value}' for key, value in data.units.items()) or '단위 미선언'
        old = f' · 오래된 실행: {data.stale_reason}' if data.stale else ''
        self.source.setText(f"실행 {data.run_id} · 프레임 {frame['index']} · 단계 {frame.get('step', '?')} / 증분 {frame.get('increment', '?')}\n"
                            f"판독 {data.parse_state}{old}\n선언 단위: {units} · 출처: {data.unit_source or '미선언'}")
        self._show_component()

    def _show_component(self):
        if self.native_grid is None:
            return
        selected = self.component.currentData()
        count = self.viewport.show_component(self.native_grid, *(selected or (None, 0)))
        coverage = '표시할 원 저장 성분 없음'
        if selected:
            coverage = f'선택 성분 유한값 {count} / 메시 절점 {self.native_grid.GetNumberOfPoints()}'
            if count == 0:
                coverage += ' · 유한값 없음'
        self.notice.setText(f'{coverage} · 결측/비유한 값은 회색\n필드 단위·위치 미확인 · 원 좌표, 변형·파생 응력 계산 안 함')

    def stop(self):
        self._stopped = True
        self._token += 1
        self._pending = None
        self.timer.stop()
        self._clear('프레임 표시를 닫았습니다.')
