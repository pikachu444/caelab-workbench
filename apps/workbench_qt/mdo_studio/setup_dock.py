"""Contextual setup and properties dock (Area 2 of MDO Studio).

Includes:
- Selected design point details (Parameters, Responses, Constraints).
- Recoverable feedback: "Set as Current Design Point" action.
- Parameter adjustment & re-evaluation controls.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton,
    QFormLayout, QGroupBox, QTextEdit, QScrollArea, QSizePolicy, QTabWidget
)

from caelab.mdo.study_manager import DesignPointRecord

_STATUS_KO = {
    "PENDING": "대기",
    "RUNNING": "실행 중",
    "QUEUED": "대기열",
    "COMPLETED": "완료",
    "PREDICTED": "예측됨",
    "FAILED": "실패",
    "CANCELLED": "취소됨",
    "STALE": "갱신 필요",
}


class SetupDockWidget(QWidget):
    apply_as_current = Signal(str)  # point_id
    re_evaluate = Signal(str)      # point_id
    inspect_attempts = Signal(str)
    open_result = Signal(str)

    def __init__(self, is_dark: bool = True, parent=None, *, language: str = "en") -> None:
        super().__init__(parent)
        self.is_dark = is_dark
        self.language = "ko" if str(language).strip().lower() == "ko" else "en"
        self._current_record: DesignPointRecord | None = None
        self._last_units: dict[str, str] = {}
        self._build_ui()
        self._apply_language_labels()
        self.set_theme(is_dark)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        self.context_tabs = QTabWidget()
        outer.addWidget(self.context_tabs)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.viewport().setStyleSheet("background: transparent;")
        container = QWidget()
        container.setObjectName("setupDockContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # 1. Selected Point Header
        self.lbl_title = QLabel("Selected Design Point: None")
        self.lbl_title.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_title.setWordWrap(True)
        self.lbl_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_title.setStyleSheet("font-weight: bold; font-size: 11pt;")
        layout.addWidget(self.lbl_title)

        # 2. Status & Feasibility Badge
        self.badge_box = QGroupBox("Status && Feasibility")
        b_layout = QFormLayout(self.badge_box)
        b_layout.setVerticalSpacing(3)
        b_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.lbl_exec_key = QLabel("Execution:")
        self.lbl_feas_key = QLabel("Feasibility:")
        self.lbl_viol_key = QLabel("Violation:")
        self.lbl_status = QLabel("—")
        self.lbl_feasibility = QLabel("—")
        self.lbl_feasibility.setWordWrap(True)
        self.lbl_violation = QLabel("0.0")
        b_layout.addRow(self.lbl_exec_key, self.lbl_status)
        b_layout.addRow(self.lbl_feas_key, self.lbl_feasibility)
        b_layout.addRow(self.lbl_viol_key, self.lbl_violation)
        layout.addWidget(self.badge_box)

        # 3. Parameters
        self.param_box = QGroupBox("Physical Parameters")
        self.param_layout = QFormLayout(self.param_box)
        self.param_layout.setVerticalSpacing(3)
        self.param_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.addWidget(self.param_box)

        # 4. Responses
        self.resp_box = QGroupBox("Observed Responses")
        self.resp_layout = QFormLayout(self.resp_box)
        self.resp_layout.setVerticalSpacing(3)
        self.resp_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.addWidget(self.resp_box)

        layout.addStretch(1)

        # 5. Engineering Action Buttons
        self.act_box = QGroupBox("Engineering Actions")
        act_layout = QVBoxLayout(self.act_box)
        act_layout.setSpacing(4)

        self.btn_apply_current = QPushButton("Set as Current\nDesign Point")
        self.btn_apply_current.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.btn_apply_current.setEnabled(False)
        self.btn_apply_current.clicked.connect(self._on_apply_clicked)
        act_layout.addWidget(self.btn_apply_current)
        self.btn_confirm = QPushButton("Fresh Native\nConfirmation")
        self.btn_confirm.clicked.connect(lambda: self.re_evaluate.emit(self._current_record.point_id) if self._current_record else None)
        act_layout.addWidget(self.btn_confirm)
        self.btn_attempts = QPushButton("Attempts / Log")
        self.btn_attempts.clicked.connect(lambda: self.inspect_attempts.emit(self._current_record.point_id) if self._current_record else None)
        act_layout.addWidget(self.btn_attempts)
        self.btn_result = QPushButton("Open Result in ParaView")
        self.btn_result.clicked.connect(lambda: self.open_result.emit(self._current_record.point_id) if self._current_record else None)
        act_layout.addWidget(self.btn_result)

        self.lbl_guide = QTextEdit()
        self.lbl_guide.setReadOnly(True)
        self.lbl_guide.setPlainText("Select a point to inspect its values and available actions.")
        self.lbl_guide.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        self.lbl_guide.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lbl_guide.setMaximumHeight(84)
        self.lbl_guide.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        act_layout.addWidget(self.lbl_guide)

        layout.addWidget(self.act_box)

        for group in (self.badge_box, self.param_box, self.resp_box, self.act_box):
            group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        scroll.setWidget(container)
        self.context_tabs.addTab(scroll, "Point")

    def _empty_title_text(self) -> str:
        return "선택된 설계점: 없음" if self.language == "ko" else "Selected Design Point: None"

    def _empty_guide_text(self) -> str:
        if self.language == "ko":
            return "설계점을 선택하면 변수·응답 값과 수행 가능한 공학 작업을 확인할 수 있습니다."
        return "Select a point to inspect its values and available actions."

    def _apply_language_labels(self) -> None:
        ko = self.language == "ko"
        self.context_tabs.setTabText(0, "설계점 상세" if ko else "Point")
        self.badge_box.setTitle("실행 상태 · 적합성" if ko else "Status && Feasibility")
        self.lbl_exec_key.setText("실행 상태:" if ko else "Execution:")
        self.lbl_feas_key.setText("적합성:" if ko else "Feasibility:")
        self.lbl_viol_key.setText("제약 위반량:" if ko else "Violation:")
        self.param_box.setTitle("설계 변수" if ko else "Physical Parameters")
        self.resp_box.setTitle("해석 응답" if ko else "Observed Responses")
        self.act_box.setTitle("공학 작업" if ko else "Engineering Actions")
        self.btn_apply_current.setText("현재 설계점으로 적용" if ko else "Set as Current\nDesign Point")
        self.btn_confirm.setText("네이티브 재검증" if ko else "Fresh Native\nConfirmation")
        self.btn_attempts.setText("실행 이력 · 로그" if ko else "Attempts / Log")
        self.btn_result.setText("ParaView에서 결과 열기" if ko else "Open Result in ParaView")
        if self._current_record is None:
            self.lbl_title.setText(self._empty_title_text())
            self.lbl_guide.setPlainText(self._empty_guide_text())

    def set_language(self, language: str) -> None:
        self.language = "ko" if str(language).strip().lower() == "ko" else "en"
        self._apply_language_labels()
        self.display_record(self._current_record, self._last_units)

    def set_editor(self, editor, title="Setup"):
        if self.context_tabs.count() > 1:
            old = self.context_tabs.widget(1)
            self.context_tabs.removeTab(1)
            old.hide()  # Retain configuration drafts when navigating other contexts.
        self.context_tabs.addTab(editor, title)
        self.context_tabs.setCurrentIndex(1)

    def set_theme(self, is_dark: bool) -> None:
        self.is_dark = is_dark
        bg_color = "#13171e" if is_dark else "#eef1f5"
        guide_color = "#94a3b8" if is_dark else "#475569"
        self.setStyleSheet(f"QWidget#setupDockContainer {{ background-color: {bg_color}; }} QScrollArea {{ background: {bg_color}; }}")
        self.lbl_guide.setStyleSheet(f"color: {guide_color}; font-size: 8.5pt; border: none; background: transparent;")

    def display_record(self, record: DesignPointRecord | None, units=None) -> None:
        self._current_record = record
        units = dict(units or {})
        self._last_units = units
        ko = self.language == "ko"
        self.btn_attempts.setEnabled(record is not None)
        self.btn_confirm.setEnabled(False)
        self.btn_result.setEnabled(False)
        if record is None:
            self.lbl_title.setText(self._empty_title_text())
            self.lbl_title.setToolTip("")
            self.lbl_status.setText("—")
            self.lbl_feasibility.setText("—")
            self.lbl_violation.setText("0.0")
            self.resp_box.setTitle("해석 응답" if ko else "Observed Responses")
            self.btn_apply_current.setEnabled(False)
            self.lbl_guide.setPlainText(self._empty_guide_text())
            while self.param_layout.rowCount():
                self.param_layout.removeRow(0)
            while self.resp_layout.rowCount():
                self.resp_layout.removeRow(0)
            return

        mode = getattr(record, "mode", "ACTUAL")
        rev = getattr(record, "revision", "1")
        self.lbl_title.setText(f"DP_{record.index:04d}: {record.name}")
        if ko:
            mode_ko = "대리모델 예측" if mode == "SURROGATE" else "실제 해석"
            self.lbl_title.setToolTip(f"{mode_ko} · 리비전 {rev}")
            self.lbl_status.setText(_STATUS_KO.get(record.status, str(record.status)))
            self.resp_box.setTitle("예측 응답 (대리모델)" if mode == "SURROGATE" else "해석 응답")
        else:
            self.lbl_title.setToolTip(f"{mode} · revision {rev}")
            self.lbl_status.setText(record.status)
            self.resp_box.setTitle("Predicted Responses" if mode == "SURROGATE" else "Actual Responses")

        # Color feasibility
        if record.feasibility == "FEASIBLE":
            text = "적합 (모든 제약 충족)" if ko else "FEASIBLE (All criteria met)"
            self.lbl_feasibility.setText(f"<span style='color:#22c55e; font-weight:bold;'>{text}</span>")
        elif record.feasibility == "MARGINAL":
            text = "경계 적합 (허용 오차 내)" if ko else "MARGINAL (Within tolerance band)"
            self.lbl_feasibility.setText(f"<span style='color:#f59e0b; font-weight:bold;'>{text}</span>")
        elif record.feasibility == "INFEASIBLE":
            text = "부적합 (제약 위반)" if ko else "INFEASIBLE (Violated)"
            self.lbl_feasibility.setText(f"<span style='color:#ef4444; font-weight:bold;'>{text}</span>")
        else:
            if ko:
                self.lbl_feasibility.setText("미평가" if record.feasibility in (None, "", "NOT_ASSESSED") else str(record.feasibility))
            else:
                self.lbl_feasibility.setText(record.feasibility or "NOT_ASSESSED")

        self.lbl_violation.setText(f"{record.total_violation:.4g}")

        # Update Parameters
        while self.param_layout.rowCount() > 0:
            self.param_layout.removeRow(0)
        for k, v in record.physical_values.items():
            val_str = f"{v:.4g}" if isinstance(v, (int, float)) else str(v)
            self.param_layout.addRow(f"{k}:", QLabel(f"{val_str} {units.get(k, '')}".strip()))

        # Update Responses
        while self.resp_layout.rowCount() > 0:
            self.resp_layout.removeRow(0)
        for k, v in record.observations.items():
            self.resp_layout.addRow(f"{k}:", QLabel(f"{v:.6g} {units.get(k, '')}".strip()))

        self.btn_apply_current.setEnabled(False)  # Window enables only verified native results.

    def _on_apply_clicked(self) -> None:
        if self._current_record:
            self.apply_as_current.emit(self._current_record.point_id)
