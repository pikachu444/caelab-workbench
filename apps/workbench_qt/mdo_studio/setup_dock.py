"""Contextual setup and properties dock (Area 2 of MDO Studio).

Includes:
- Selected design point details (Parameters, Responses, Constraints).
- Recoverable feedback: "Set as Current Design Point" action.
- Parameter adjustment & re-evaluation controls.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFormLayout, QGroupBox, QTextEdit, QScrollArea
)

from caelab.mdo.study_manager import DesignPointRecord


class SetupDockWidget(QWidget):
    apply_as_current = Signal(str)  # point_id
    re_evaluate = Signal(str)      # point_id

    def __init__(self, is_dark: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.is_dark = is_dark
        self._current_record: DesignPointRecord | None = None
        self._build_ui()
        self.set_theme(is_dark)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.viewport().setStyleSheet("background: transparent;")
        container = QWidget()
        container.setObjectName("setupDockContainer")
        layout = QVBoxLayout(container)
        layout.setSpacing(10)

        # 1. Selected Point Header
        self.lbl_title = QLabel("Selected Design Point: None")
        self.lbl_title.setStyleSheet("font-weight: bold; font-size: 11pt;")
        layout.addWidget(self.lbl_title)

        # 2. Status & Feasibility Badge
        badge_box = QGroupBox("Status & Feasibility")
        b_layout = QFormLayout(badge_box)
        self.lbl_status = QLabel("—")
        self.lbl_feasibility = QLabel("—")
        self.lbl_feasibility.setWordWrap(True)
        self.lbl_violation = QLabel("0.0")
        b_layout.addRow("Execution:", self.lbl_status)
        b_layout.addRow("Feasibility:", self.lbl_feasibility)
        b_layout.addRow("Violation:", self.lbl_violation)
        layout.addWidget(badge_box)

        # 3. Parameters
        self.param_box = QGroupBox("Physical Parameters")
        self.param_layout = QFormLayout(self.param_box)
        layout.addWidget(self.param_box)

        # 4. Responses
        self.resp_box = QGroupBox("Observed Responses")
        self.resp_layout = QFormLayout(self.resp_box)
        layout.addWidget(self.resp_box)

        layout.addStretch(1)

        # 5. Engineering Action Buttons
        act_box = QGroupBox("Engineering Actions")
        act_layout = QVBoxLayout(act_box)

        self.btn_apply_current = QPushButton("★ Set as Current Design Point")
        self.btn_apply_current.setStyleSheet(
            "background-color: #2563eb; color: white; font-weight: bold; padding: 8px; border-radius: 4px;"
        )
        self.btn_apply_current.setEnabled(False)
        self.btn_apply_current.clicked.connect(self._on_apply_clicked)
        act_layout.addWidget(self.btn_apply_current)

        self.lbl_guide = QLabel("Safely substitutes parameters into active native deck with automatic .bak backup.")
        self.lbl_guide.setWordWrap(True)
        act_layout.addWidget(self.lbl_guide)

        layout.addWidget(act_box)

        scroll.setWidget(container)
        outer.addWidget(scroll)

    def set_theme(self, is_dark: bool) -> None:
        self.is_dark = is_dark
        bg_color = "#13171e" if is_dark else "#eef1f5"
        guide_color = "#94a3b8" if is_dark else "#475569"
        self.setStyleSheet(f"QWidget#setupDockContainer {{ background-color: {bg_color}; }} QScrollArea {{ background: {bg_color}; }}")
        self.lbl_guide.setStyleSheet(f"color: {guide_color}; font-size: 8.5pt;")

    def display_record(self, record: DesignPointRecord | None) -> None:
        self._current_record = record
        if record is None:
            self.lbl_title.setText("Selected Design Point: None")
            self.lbl_status.setText("—")
            self.lbl_feasibility.setText("—")
            self.lbl_violation.setText("0.0")
            self.btn_apply_current.setEnabled(False)
            return

        self.lbl_title.setText(f"{record.point_id}: {record.name}")
        self.lbl_status.setText(record.status)

        # Color feasibility
        if record.feasibility == "FEASIBLE":
            self.lbl_feasibility.setText("<span style='color:#22c55e; font-weight:bold;'>FEASIBLE (All criteria met)</span>")
        elif record.feasibility == "MARGINAL":
            self.lbl_feasibility.setText("<span style='color:#f59e0b; font-weight:bold;'>MARGINAL (Within tolerance band)</span>")
        elif record.feasibility == "INFEASIBLE":
            self.lbl_feasibility.setText("<span style='color:#ef4444; font-weight:bold;'>INFEASIBLE (Violated)</span>")
        else:
            self.lbl_feasibility.setText(record.feasibility or "NOT_ASSESSED")

        self.lbl_violation.setText(f"{record.total_violation:.4g}")

        # Update Parameters
        while self.param_layout.rowCount() > 0:
            self.param_layout.removeRow(0)
        for k, v in record.physical_values.items():
            val_str = f"{v:.4g}" if isinstance(v, (int, float)) else str(v)
            self.param_layout.addRow(f"{k}:", QLabel(val_str))

        # Update Responses
        while self.resp_layout.rowCount() > 0:
            self.resp_layout.removeRow(0)
        for k, v in record.observations.items():
            self.resp_layout.addRow(f"{k}:", QLabel(f"{v:.4g}"))

        self.btn_apply_current.setEnabled(record.status == "COMPLETED")

    def _on_apply_clicked(self) -> None:
        if self._current_record:
            self.apply_as_current.emit(self._current_record.point_id)
