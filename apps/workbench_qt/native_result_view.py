"""Native VTU contours and display-only deformation without auxiliary conversion."""
from __future__ import annotations

import copy
from dataclasses import replace
import queue
import threading

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .theme import get_palette
from .viewport import Viewport, _apply_text_font, compute_von_mises, deduce_length_unit, deduce_stress_unit, vtk


_DISPLACEMENT = {'D1': 'Ux', 'D2': 'Uy', 'D3': 'Uz'}
_STRESS = {'SXX': 'Sxx', 'SYY': 'Syy', 'SZZ': 'Szz', 'SXY': 'Sxy', 'SYZ': 'Syz', 'SZX': 'Szx'}


def _component_indices(array, names):
    if array is None:
        return None
    stored = [array.GetComponentName(i) for i in range(array.GetNumberOfComponents())]
    return [stored.index(name) for name in names] if all(stored.count(name) == 1 for name in names) else None


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

    def show_component(self, original, name=None, component=0, *, title='', scale=0.0):
        from vtk.util.numpy_support import numpy_to_vtk, vtk_to_numpy

        grid = vtk.vtkUnstructuredGrid()
        grid.ShallowCopy(original)
        finite_count = 0
        values = None
        if name is not None:
            source = grid.GetPointData().GetArray(name)
            if source is None:
                raise ValueError('선택한 원 저장 성분이 VTU에 없습니다.')
            raw = vtk_to_numpy(source)
            if component == 'mag':
                indices = _component_indices(source, _DISPLACEMENT)
                with np.errstate(over='ignore', invalid='ignore'):
                    values = np.hypot.reduce(raw[:, indices], axis=1)
            elif component == 'vm':
                indices = _component_indices(source, _STRESS)
                ordered = raw[:, indices]
                values = compute_von_mises(dict(enumerate(ordered.tolist())), list(range(len(ordered))))
            else:
                if not 0 <= component < source.GetNumberOfComponents():
                    raise ValueError('선택한 원 저장 성분이 VTU에 없습니다.')
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
        if scale:
            displacement = original.GetPointData().GetArray('DISP')
            indices = _component_indices(displacement, _DISPLACEMENT)
            vectors = vtk_to_numpy(displacement)[:, indices]
            valid = np.isfinite(vectors).all(axis=1)
            coordinates = np.array(vtk_to_numpy(original.GetPoints().GetData()), dtype=float, copy=True)
            with np.errstate(over='ignore', invalid='ignore'):
                warped = coordinates[valid] + float(scale) * vectors[valid]
            finite = np.isfinite(warped).all(axis=1)
            coordinates[np.flatnonzero(valid)[finite]] = warped[finite]
            points = vtk.vtkPoints()
            points.SetData(numpy_to_vtk(coordinates, deep=True))
            grid.SetPoints(points)
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
                # Bare bars are MathText column separators; \vert renders literal |U|.
                legend_title = title
                if name == 'DISP' and component == 'mag' and vtk.vtkTextRenderer.GetInstance().MathTextIsSupported():
                    legend_title = title.replace('|U|', r'$\vert U\vert$')
                legend.SetTitle(legend_title)
                legend.SetNumberOfLabels(2 if low == high else 5)
                legend.SetWidth(.12)
                legend.SetHeight(.7)
                legend.SetPosition(.85, .15)
                legend.SetUnconstrainedFontSize(True)
                for prop, size in ((legend.GetTitleTextProperty(), 13), (legend.GetLabelTextProperty(), 12)):
                    prop.SetFontSize(size)
                    prop.SetColor(*get_palette()['vtk_text_rgb'])
                    prop.BoldOff()
                    prop.ItalicOff()
                    prop.ShadowOff()
                    _apply_text_font(prop)
                self._renderer.AddActor(legend)
                self._scalar_bar = legend
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
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self.source = QLabel()
        self.source.setTextFormat(Qt.PlainText)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        self.component = QComboBox()
        self.component.setAccessibleName('네이티브 결과 성분')
        self.component.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.component.currentIndexChanged.connect(self._show_component)
        toolbar.addWidget(self.component)
        self.scale_combo = QComboBox()
        self.scale_combo.setAccessibleName('변형 배율')
        self.scale_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        for label, value in (('미변형 (0x)', 0.0), ('실변형 (1x)', 1.0), ('자동 배율', 'auto')):
            self.scale_combo.addItem(label, value)
        self.scale_combo.currentIndexChanged.connect(self._show_component)
        toolbar.addWidget(self.scale_combo)
        for label, preset in (('등각', 'iso'), ('정면', 'front'), ('평면', 'top')):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, value=preset: self.viewport.set_camera_preset(value))
            toolbar.addWidget(button)
        fit = QPushButton('화면 맞춤')
        fit.clicked.connect(lambda: self.viewport.reset_camera())
        toolbar.addWidget(fit)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        layout.addWidget(self.source)
        self.notice = QLabel()
        self.notice.setTextFormat(Qt.PlainText)
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
        self.scale_combo.blockSignals(True)
        self.scale_combo.setCurrentIndex(0)
        self.scale_combo.blockSignals(False)
        self.scale_combo.setEnabled(False)
        self.scale_combo.setToolTip('3성분 변위와 길이 단위가 필요합니다.')
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
            name = field['name']
            if name == 'DISP' and array.GetNumberOfComponents() == 3 and _component_indices(array, _DISPLACEMENT) is not None:
                self.component.addItem('변위 (U) · 크기 |U|', (name, 'mag'))
            if name == 'STRESS' and _component_indices(array, _STRESS) is not None:
                self.component.addItem('응력 (S) · Von Mises [절점 외삽]', (name, 'vm'))
            for index, item in enumerate(stored):
                label = f"{name} · {item['name']}"
                if name == 'DISP' and item['name'] in _DISPLACEMENT:
                    label = f"변위 (U) · {_DISPLACEMENT[item['name']]} ({item['name']})"
                elif name == 'STRESS' and item['name'] in _STRESS:
                    label = f"응력 (S) · {_STRESS[item['name']]}"
                self.component.addItem(label, (name, index))
        self.component.blockSignals(False)
        self.component.setEnabled(self.component.count() > 0)
        frame = data.frame
        units = ' · '.join(str(data.units[key]) for key in ('length', 'force') if data.units.get(key))
        units_text = f'단위: {units}' if units else '단위 미선언'
        old = ' · 오래된 실행' if data.stale else ''
        parse_label = {'readable': '판독 완료', 'partial': '부분 판독', 'failed': '판독 실패',
                       'missing': '결과 없음', 'reading': '판독 중'}.get(data.parse_state, '판독 미확인')
        metadata = ' / '.join(f'{label} {frame[key]}' for key, label in (('step', '단계'), ('increment', '증분'))
                              if frame.get(key) is not None)
        self.source.setText(f"프레임 {frame['index']}" + (f' ({metadata})' if metadata else '')
                            + f' · {parse_label}{old} · {units_text}')
        self.source.setToolTip(data.stale_reason if data.stale else '')
        displacement = grid.GetPointData().GetArray('DISP')
        indices = _component_indices(displacement, _DISPLACEMENT)
        self.scale_combo.setEnabled(displacement is not None and displacement.GetNumberOfComponents() == 3
                                    and indices is not None and deduce_length_unit(None, data)[1] is not None)
        self._show_component()
        self.viewport.reset_camera()

    def _show_component(self):
        if self.native_grid is None:
            return
        selected = self.component.currentData()
        title = ''
        if selected:
            name, component = selected
            array = self.native_grid.GetPointData().GetArray(name)
            comp_name = array.GetComponentName(component) if isinstance(component, int) else component
            if name == 'DISP' and (component == 'mag' or comp_name in _DISPLACEMENT):
                title = '|U|' if component == 'mag' else _DISPLACEMENT.get(comp_name, comp_name)
                unit = deduce_length_unit(None, self.frame_data)[1]
            elif name == 'STRESS' and (component == 'vm' or comp_name in _STRESS):
                title = 'Von Mises' if component == 'vm' else _STRESS.get(comp_name, comp_name)
                unit = deduce_stress_unit(self.frame_data.units)
            else:
                title, unit = f'{name} · {comp_name}', None
            if unit:
                title += f' ({unit})'
        scale = 0.0
        if self.scale_combo.isEnabled():
            from vtk.util.numpy_support import vtk_to_numpy
            displacement = self.native_grid.GetPointData().GetArray('DISP')
            vectors = vtk_to_numpy(displacement)[:, _component_indices(displacement, _DISPLACEMENT)]
            valid = np.isfinite(vectors).all(axis=1)
            self.scale_combo.setToolTip(f'유효 변위 {valid.sum()} / {len(vectors)} · 자동: 모델 크기의 25% · 원 저장 축 기준')
            scale = self.scale_combo.currentData()
            if scale == 'auto':
                bounds = self.native_grid.GetBounds()
                size = max(bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4])
                with np.errstate(over='ignore', invalid='ignore'):
                    peak = float(np.hypot.reduce(vectors[valid], axis=1).max()) if valid.any() else 0.0
                scale = .25 * size / peak if peak else 0.0
        count = self.viewport.show_component(self.native_grid, *(selected or (None, 0)), title=title, scale=scale)
        coverage = '표시할 원 저장 성분 없음'
        if selected:
            coverage = f'유효 절점 {count} / {self.native_grid.GetNumberOfPoints()}'
            if count == 0:
                coverage += ' · 유한값 없음'
        self.notice.setText(coverage)

    def stop(self):
        self._stopped = True
        self._token += 1
        self._pending = None
        self.timer.stop()
        self._clear('프레임 표시를 닫았습니다.')
