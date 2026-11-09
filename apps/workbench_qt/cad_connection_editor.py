"""Contextual CAD choices; ProjectWindow owns model changes and execution."""

from collections.abc import Sequence

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from caelab.connections import PROGRAMS, ProgramStatus
from caelab.project import ProgramNode


_DOCUMENT_FILTERS = {
    'freecad': 'FreeCAD 편집 문서 (*.FCStd *.fcstd)',
    'solidcraft': 'SolidCraft 편집 문서 (*.sc *.solidcraft)',
    'cadcraft': 'CADCraft 도면 (*.dxf *.dwg *.dwt)',
}
_DOCUMENT_STATES = {
    'ready': '등록됨', 'missing': '파일 누락', 'changed': '원본 변경됨',
    'stale': '갱신 확인 필요', 'needs_input': '미등록',
}


class CadConnectionEditor(QWidget):
    """Emit intents synchronously in the GUI thread; never commit a selection."""

    program_change_requested = Signal(str, str)
    document_registration_requested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName('cad_connection_editor')
        self.setAccessibleName('선택한 형상의 CAD 연결')
        self._context: tuple[int, str, str] | None = None
        self._generation = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        program_label = QLabel('CAD 프로그램')
        self.program_choice = QComboBox()
        self.program_choice.setObjectName('cad_program_choice')
        self.program_choice.setAccessibleName('CAD 프로그램 선택')
        self.program_choice.setMinimumWidth(0)
        self.program_choice.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.program_choice.setMinimumContentsLength(12)
        program_label.setBuddy(self.program_choice)
        layout.addWidget(program_label)
        layout.addWidget(self.program_choice)
        self.program_status = QLabel()
        self.program_status.setWordWrap(True)
        self.program_status.setObjectName('cad_program_status')
        layout.addWidget(self.program_status)

        document_label = QLabel('편집 문서')
        self.document_path = QLineEdit()
        self.document_path.setObjectName('cad_document_path')
        self.document_path.setAccessibleName('CAD 편집 문서 경로')
        self.document_path.setReadOnly(True)
        self.document_path.setMinimumWidth(0)
        self.document_path.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        document_label.setBuddy(self.document_path)
        layout.addWidget(document_label)
        layout.addWidget(self.document_path)
        document_row = QHBoxLayout()
        self.document_status = QLabel()
        self.document_status.setObjectName('cad_document_status')
        self.document_status.setWordWrap(True)
        document_row.addWidget(self.document_status, 1)
        self.document_button = QPushButton('등록…')
        self.document_button.setObjectName('cad_document_register')
        self.document_button.setAccessibleName('CAD 편집 문서 등록')
        document_row.addWidget(self.document_button)
        layout.addLayout(document_row)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.feedback = QLabel()
        self.feedback.setObjectName('cad_connection_feedback')
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        layout.addStretch(1)

        self.program_choice.currentIndexChanged.connect(self._request_program)
        self.document_button.clicked.connect(self._choose_document)
        self.clear_context()

    def clear_context(self) -> None:
        """Invalidate a pending picker without emitting a model-change request."""
        if self._context is not None:
            self._generation += 1
        self._context = None
        with QSignalBlocker(self.program_choice):
            self.program_choice.clear()
        self.document_path.clear()
        self.document_path.setToolTip('')
        self.program_status.clear()
        self.document_status.setText('미등록')
        self.document_button.setText('등록…')
        self.hint.setText('형상 상자를 선택하면 CAD와 편집 문서를 지정할 수 있습니다.')
        self.feedback.clear()
        self.setEnabled(False)

    def set_context(self, node: ProgramNode, statuses: Sequence[ProgramStatus],
                    *, document_state: str, context_epoch: int) -> None:
        """Display supplied state. G advances the epoch on actual context changes."""
        if node.kind != 'geometry':
            self.clear_context()
            return
        context = (context_epoch, node.id, node.program_id)
        if context != self._context:
            self._generation += 1
        self._context = context
        self.setEnabled(True)
        cad_specs = [spec for spec in PROGRAMS if spec.role == 'cad']
        by_id = {status.spec.id: status for status in statuses}
        with QSignalBlocker(self.program_choice):
            self.program_choice.clear()
            for spec in cad_specs:
                self.program_choice.addItem(spec.name, spec.id)
            if self.program_choice.findData(node.program_id) < 0:
                self.program_choice.addItem(node.program_id + ' (현재 프로그램)', node.program_id)
            self.program_choice.setCurrentIndex(self.program_choice.findData(node.program_id))
        status = by_id.get(node.program_id)
        if status is None:
            self.program_status.setText('실행 파일 상태 미확인')
        elif status.available:
            self.program_status.setText('실행 파일 확인됨 (열기 가능)')
        else:
            self.program_status.setText('실행 불가: ' + (status.reason or '실행 파일을 지정하세요.'))
        reference = node.files.get('document')
        path = reference.path if reference else ''
        self.document_path.setText(path)
        self.document_path.setToolTip(path)
        self.document_path.setCursorPosition(len(path))
        self.document_path.setPlaceholderText('편집 문서 지정 안 됨')
        self.document_status.setText(_DOCUMENT_STATES.get(document_state, '상태 미확인') if reference else '미등록')
        self.document_button.setText('변경…' if reference else '등록…')
        self.document_button.setEnabled(node.program_id in _DOCUMENT_FILTERS)
        spec = next((spec for spec in cad_specs if spec.id == node.program_id), None)
        hint = '편집 원본 참조 · 교환 파일은 출력에 등록'
        if node.program_id == 'cadcraft':
            hint += ' · CADCraft 도면 (STEP 변환 없음)'
        self.hint.setText(hint if spec else 'CAD 프로그램을 선택하세요.')
        self.hint.setToolTip(spec.notes if spec else '')
        self.feedback.clear()

    def _request_program(self, _index: int) -> None:
        if self._context is None:
            return
        _epoch, node_id, current_program = self._context
        requested = self.program_choice.currentData()
        # Keep committed state visible until the consumer rebinds the real model.
        with QSignalBlocker(self.program_choice):
            self.program_choice.setCurrentIndex(self.program_choice.findData(current_program))
        if requested and requested != current_program:
            self.program_change_requested.emit(node_id, requested)

    def _choose_document(self) -> None:
        context, generation = self._context, self._generation
        if context is None:
            return
        _epoch, node_id, program_id = context
        file_filter = _DOCUMENT_FILTERS.get(program_id)
        if file_filter is None:
            return
        path, _selected_filter = QFileDialog.getOpenFileName(
            self, 'CAD 편집 문서 등록', self.document_path.text(), file_filter)
        if not isValid(self) or not path:
            return
        if self._context != context or self._generation != generation:
            self.feedback.setText('선택 대상이 바뀌어 문서를 등록하지 않았습니다.')
            return
        self.document_registration_requested.emit(node_id, path)
