"""UI01 Independent Qt Material Calibration Study Window (C9)."""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
import math
import os
from pathlib import Path
import queue
import sys
import threading
import traceback
from typing import Any

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
import matplotlib

from caelab.materials.contracts import (
    ComparisonSpec,
    ExperimentChannelSpec,
    ObservableSpec,
    ParameterSpec,
)
from caelab.materials.experiments import load_experiment_channel_arrays, preview_csv_rows
from caelab.materials.registry import CalibrationEvaluationArtifact, MaterialStudyError, default_registry
from caelab.materials.service import CalibrationStudyService
from caelab.materials.units import QuantityUnitSpec
from caelab.mdo.optimize import OptimizationConfig
from apps.workbench_qt.icons import icon as load_ui_icon
from apps.workbench_qt.theme import (
    build_stylesheet,
    current_theme_mode,
    get_palette,
    register_theme_listener,
    status_badge_style,
    theme_toggle_label,
    toggle_theme_mode,
)

__all__ = ["ExperimentImportDialog", "MaterialStudyWindow", "load_korean_font", "main"]

_MALGUN_FONT_PATH = Path("C:/Windows/Fonts/malgun.ttf")

_SPLIT_KO = {
    "train": "학습",
    "validation": "검증",
    "holdout": "보류",
}
_ROLE_KO = {
    "free": "자유",
    "fixed": "고정",
    "linked": "연계",
}
_TRANSFORM_KO = {
    "linear": "선형",
    "log": "로그",
}
_STATUS_KO = {
    "pass": "적합",
    "fail": "부적합",
    "not_assessed": "미확인",
    "not_computed": "미확인",
    "not_identifiable": "비식별",
    "FEASIBLE": "적합",
    "MARGINAL": "주의",
    "INFEASIBLE": "부적합",
    "NOT_ASSESSED": "미확인",
    "completed": "완료",
    "failed": "실패",
    "stopped": "중지됨",
    "cancelled": "취소됨",
    "interrupted": "중단됨",
    "running": "실행 중",
    "exploration": "탐색",
    "confirmation": "최종 확인",
}
_OBSERVABLE_KO = {
    "E_storage": "저장탄성계수 (E')",
    "E_loss": "손실탄성계수 (E'')",
    "E_star": "복소탄성계수 (E*)",
    "E_relax": "완화탄성계수 E(t)",
    "stress_xx": "축 응력",
    "axial_stress": "축 응력",
}


def load_korean_font(app: QApplication | None = None, point_size: int = 10) -> QFont:
    """Load Malgun Gothic font when available and configure Matplotlib Korean font rendering."""
    family = "Malgun Gothic"
    if _MALGUN_FONT_PATH.is_file():
        font_id = QFontDatabase.addApplicationFont(str(_MALGUN_FONT_PATH))
        if font_id >= 0:
            loaded = QFontDatabase.applicationFontFamilies(font_id)
            if loaded:
                family = loaded[0]
    font = QFont(family, point_size)
    if app is not None:
        app.setFont(font)
    matplotlib.rcParams["font.family"] = [family, "Malgun Gothic", "NanumGothic", "DejaVu Sans"]
    matplotlib.rcParams["mathtext.fontset"] = "stixsans"
    matplotlib.rcParams["axes.unicode_minus"] = False
    return font


def _ko_status(raw: str | None) -> str:
    if raw is None:
        return "미확인"
    return _STATUS_KO.get(str(raw), str(raw))


def _ko_split(raw: str | None) -> str:
    return _SPLIT_KO.get(str(raw), str(raw or "미확인"))


def _ko_observable(obs_id: str) -> str:
    return _OBSERVABLE_KO.get(obs_id, obs_id)


def _human_error(exc: Exception) -> str:
    if isinstance(exc, MaterialStudyError):
        reason = exc.details.get("reason") or exc.details.get("message") or ""
        code_map = {
            "STUDY_LOCKED": "다른 창에서 이 연구를 편집 중입니다.",
            "READ_ONLY_STUDY": "읽기 전용 모드에서는 연구를 변경할 수 없습니다.",
            "STUDY_BUSY": "현재 다른 작업이 실행 중입니다. 완료 후 다시 시도하세요.",
            "REVISION_MISMATCH": "문서가 변경되어 작업을 다시 시도해야 합니다.",
            "EXTERNAL_MANIFEST_CHANGED": "외부에서 연구 파일이 수정되었습니다. 다시 열어 확인하세요.",
            "BACKEND_UNAVAILABLE": "필수 재료 계산 라이브러리를 사용할 수 없습니다.",
            "PYVISCO_SOURCE_HASH_MISMATCH": "계산 라이브러리 소스 무결성 확인에 실패했습니다.",
            "CONFIRMATION_REQUIRES_REFRESH": "보류 시험 최종 확인은 새로 계산 모드만 허용됩니다.",
            "HOLDOUT_EXPOSURE_RECORD_FAILED": "보류 시험 노출 기록 저장에 실패하여 결과를 표시하지 않습니다.",
            "UNSUPPORTED_ATOMIC_APPLY": "현재 모델은 열린 전처리 문서 직접 적용을 지원하지 않습니다.",
            "INVALID_CSV": "CSV 파일 형식이 올바르지 않거나 비유한 값이 포함되어 있습니다.",
        }
        base = code_map.get(exc.code, "요청을 처리하는 중 오류가 발생했습니다.")
        if reason:
            return f"{base} ({reason})"
        return base
    return str(exc)


def _format_log_tick(val: float, _pos: Any) -> str:
    """Format positive log-axis ticks with pure ASCII minus (no Unicode U+2212 missing glyphs)."""
    if val <= 0.0 or not math.isfinite(val):
        return ""
    if 0.001 <= val <= 10000.0:
        return f"{val:g}"
    exp = int(round(math.log10(val)))
    if math.isclose(val, 10.0 ** exp, rel_tol=1e-6):
        return f"1e{exp}"
    return f"{val:.1e}"


def _configure_combo(combo: QComboBox) -> QComboBox:
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    return combo


class ExperimentImportDialog(QDialog):
    """Real CSV preview (<=100 rows) + explicit column/unit/split/condition dialog (C9)."""

    def __init__(self, csv_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.csv_path = csv_path
        self.setWindowTitle("실험 CSV 불러오기")
        self.resize(840, 620)
        headers, rows, total_rows = preview_csv_rows(csv_path, max_rows=100)
        self._headers = headers

        layout = QVBoxLayout(self)
        summary = QLabel(
            f"파일: {csv_path.name} · 총 {total_rows}행 중 상위 {len(rows)}행 미리보기"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        # Up to 100-row real CSV preview table
        self.preview_table = QTableWidget(len(rows), len(headers))
        self.preview_table.setHorizontalHeaderLabels(headers)
        self.preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview_table.setAlternatingRowColors(True)
        for r_idx, row in enumerate(rows):
            for c_idx, cell in enumerate(row):
                self.preview_table.setItem(r_idx, c_idx, QTableWidgetItem(cell))
        self.preview_table.resizeColumnsToContents()
        layout.addWidget(self.preview_table, 1)

        form_box = QGroupBox("시험 조건 · 열 대응 · 단위 · 분할")
        grid = QGridLayout(form_box)

        self.kind_combo = _configure_combo(QComboBox())
        self.kind_combo.addItem("동적 기계 분석 (DMA: 저장/손실 탄성계수)", "dma")
        self.kind_combo.addItem("응력 완화 시험 (Relaxation)", "relax")
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)

        self.split_combo = _configure_combo(QComboBox())
        for key, label in (("train", "학습 (Train)"), ("validation", "검증 (Validation)"), ("holdout", "보류 (Holdout)")):
            self.split_combo.addItem(label, key)

        self.exp_name_edit = QLineEdit(csv_path.stem)
        self.rep_group_edit = QLineEdit("시편-1")

        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(-200.0, 2000.0)
        self.temp_spin.setDecimals(2)
        self.temp_spin.setValue(293.15)
        self.temp_unit_combo = _configure_combo(QComboBox())
        self.temp_unit_combo.addItem("K", "K")
        self.temp_unit_combo.addItem("degC", "degC")

        self.ind_col_combo = _configure_combo(QComboBox())
        self.ind_col_combo.addItems(headers)
        self.ind_unit_combo = _configure_combo(QComboBox())

        self.val1_col_combo = _configure_combo(QComboBox())
        self.val1_col_combo.addItems(headers)
        self.val2_col_combo = _configure_combo(QComboBox())
        self.val2_col_combo.addItem("(사용 안 함)", "")
        self.val2_col_combo.addItems(headers)
        self.dep_unit_combo = _configure_combo(QComboBox())
        for u in ("MPa", "Pa", "kPa", "GPa"):
            self.dep_unit_combo.addItem(u, u)

        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(1e-6, 1e9)
        self.scale_spin.setDecimals(3)
        self.scale_spin.setValue(100.0)

        grid.addWidget(QLabel("시험 유형"), 0, 0)
        grid.addWidget(self.kind_combo, 0, 1)
        grid.addWidget(QLabel("데이터 분할"), 0, 2)
        grid.addWidget(self.split_combo, 0, 3)

        grid.addWidget(QLabel("시험 식별자"), 1, 0)
        grid.addWidget(self.exp_name_edit, 1, 1)
        grid.addWidget(QLabel("반복 시편 그룹"), 1, 2)
        grid.addWidget(self.rep_group_edit, 1, 3)

        grid.addWidget(QLabel("시험 온도"), 2, 0)
        temp_row = QHBoxLayout()
        temp_row.addWidget(self.temp_spin, 1)
        temp_row.addWidget(self.temp_unit_combo)
        grid.addLayout(temp_row, 2, 1)
        grid.addWidget(QLabel("잔차 척도 (Scale)"), 2, 2)
        grid.addWidget(self.scale_spin, 2, 3)

        grid.addWidget(QLabel("독립축 열"), 3, 0)
        grid.addWidget(self.ind_col_combo, 3, 1)
        grid.addWidget(QLabel("독립축 단위"), 3, 2)
        grid.addWidget(self.ind_unit_combo, 3, 3)

        self.val1_label = QLabel("저장탄성계수 열")
        grid.addWidget(self.val1_label, 4, 0)
        grid.addWidget(self.val1_col_combo, 4, 1)
        self.val2_label = QLabel("손실탄성계수 열")
        grid.addWidget(self.val2_label, 4, 2)
        grid.addWidget(self.val2_col_combo, 4, 3)

        grid.addWidget(QLabel("탄성계수 단위"), 5, 0)
        grid.addWidget(self.dep_unit_combo, 5, 1)

        layout.addWidget(form_box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("가져오기 확인")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._guess_defaults()

    def _guess_defaults(self) -> None:
        lower_map = {h.lower(): h for h in self._headers}
        if any("time" in h.lower() or "relax" in h.lower() for h in self._headers):
            self.kind_combo.setCurrentIndex(1)
        else:
            self.kind_combo.setCurrentIndex(0)
        self._on_kind_changed()

        for cand in ("frequency_hz", "freq_hz", "frequency", "time_s", "time"):
            if cand in lower_map:
                self.ind_col_combo.setCurrentText(lower_map[cand])
                break
        for cand in ("storage_mpa", "e_storage", "storage", "relaxation_modulus_mpa", "e_relax_mpa", "e_relax"):
            if cand in lower_map:
                self.val1_col_combo.setCurrentText(lower_map[cand])
                break
        for cand in ("loss_mpa", "e_loss", "loss"):
            if cand in lower_map:
                self.val2_col_combo.setCurrentText(lower_map[cand])
                break

    def _on_kind_changed(self) -> None:
        kind = self.kind_combo.currentData()
        self.ind_unit_combo.clear()
        if kind == "dma":
            self.ind_unit_combo.addItem("Hz (주파수)", ("Hz", "frequency"))
            self.ind_unit_combo.addItem("rad/s (각주파수)", ("rad/s", "angular_frequency"))
            self.val1_label.setText("저장탄성계수 열")
            self.val2_label.setText("손실탄성계수 열")
            self.val2_col_combo.setEnabled(True)
        else:
            self.ind_unit_combo.addItem("s (초)", ("s", "time"))
            self.ind_unit_combo.addItem("ms (밀리초)", ("ms", "time"))
            self.val1_label.setText("완화탄성계수 열")
            self.val2_label.setText("(단일 채널)")
            self.val2_col_combo.setCurrentIndex(0)
            self.val2_col_combo.setEnabled(False)

    def build_import_kwargs(self) -> dict[str, Any]:
        kind = self.kind_combo.currentData()
        split = str(self.split_combo.currentData())
        ind_col = self.ind_col_combo.currentText()
        ind_unit, ind_qkind = self.ind_unit_combo.currentData()
        dep_unit = str(self.dep_unit_combo.currentData())
        temp_val = float(self.temp_spin.value())
        temp_unit = str(self.temp_unit_combo.currentData())
        temp_k = temp_val if temp_unit == "K" else temp_val + 273.15

        channels: list[ExperimentChannelSpec] = []
        unit_specs: dict[str, QuantityUnitSpec] = {ind_col: QuantityUnitSpec(ind_unit, ind_qkind)}

        if kind == "dma":
            col1 = self.val1_col_combo.currentText()
            obs1 = ObservableSpec(
                id="E_storage",
                quantity_kind="storage_modulus",
                unit=dep_unit,
                measure="viscoelastic",
                component_order=("E_storage",),
            )
            channels.append(
                ExperimentChannelSpec(
                    observable_id="E_storage",
                    observable=obs1,
                    column_name=col1,
                    source_unit=dep_unit,
                    independent_column=ind_col,
                    independent_unit=ind_unit,
                )
            )
            unit_specs[col1] = QuantityUnitSpec(dep_unit, "storage_modulus")
            col2 = str(self.val2_col_combo.currentText())
            if col2 and col2 != "(사용 안 함)":
                obs2 = ObservableSpec(
                    id="E_loss",
                    quantity_kind="loss_modulus",
                    unit=dep_unit,
                    measure="viscoelastic",
                    component_order=("E_loss",),
                )
                channels.append(
                    ExperimentChannelSpec(
                        observable_id="E_loss",
                        observable=obs2,
                        column_name=col2,
                        source_unit=dep_unit,
                        independent_column=ind_col,
                        independent_unit=ind_unit,
                    )
                )
                unit_specs[col2] = QuantityUnitSpec(dep_unit, "loss_modulus")
        else:
            col1 = self.val1_col_combo.currentText()
            obs_rel = ObservableSpec(
                id="E_relax",
                quantity_kind="relaxation_modulus",
                unit=dep_unit,
                measure="viscoelastic",
                component_order=("E_relax",),
            )
            channels.append(
                ExperimentChannelSpec(
                    observable_id="E_relax",
                    observable=obs_rel,
                    column_name=col1,
                    source_unit=dep_unit,
                    independent_column=ind_col,
                    independent_unit=ind_unit,
                )
            )
            unit_specs[col1] = QuantityUnitSpec(dep_unit, "relaxation_modulus")

        return {
            "source": self.csv_path,
            "experiment_id": self.exp_name_edit.text().strip() or self.csv_path.stem,
            "channels": channels,
            "column_mapping": {"independent_quantity_kind": ind_qkind},
            "unit_specs": unit_specs,
            "sequence": (),
            "conditions": {"temperature_K": temp_k},
            "split": split,
            "replicate_group": self.rep_group_edit.text().strip(),
            "scale": float(self.scale_spin.value()),
        }


class MaterialStudyWindow(QMainWindow):
    """Independent Qt Material Calibration Study Window (C9 / UI01)."""

    def __init__(
        self,
        *,
        study_path: str | Path | None = None,
        service: CalibrationStudyService | None = None,
        read_only: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._closed = False
        self._close_after = False
        self._generation = 0
        self._draft_revision = 0
        self._busy_op: str | None = None
        self._active_campaign_id: str | None = None
        self._op_token: object | None = None
        self._mailbox: queue.Queue[tuple[int, object, str, bool, Any]] = queue.Queue()
        self._progress_mailbox: queue.Queue[tuple[int, object, dict[str, Any]]] = queue.Queue()
        self._selected_eval_id: str | None = None
        self._active_eval_artifact: CalibrationEvaluationArtifact | None = None
        self._last_campaign_receipt: Any = None
        self._temp_dir: Any = None
        self._theme_mode = current_theme_mode()

        if service is not None:
            self.service = service
        elif study_path is not None:
            self.service = CalibrationStudyService.open(study_path, read_only=read_only)
        else:
            import tempfile

            self._temp_dir = tempfile.TemporaryDirectory(prefix="caemat-ui-")
            default_path = Path(self._temp_dir.name) / "untitled.caemat.json"
            self.service = CalibrationStudyService.create(
                default_path,
                name="새 재료 보정 연구",
                model="B-VIS-TRS-1",
                adapter="1.0.0",
            )

        self.setWindowTitle("CAELAB 재료 물성 보정 연구")
        self.resize(1440, 900)
        self.setMinimumSize(1024, 680)

        self._build_menus_and_toolbar()
        self._build_layout()
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(50)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

        self.apply_theme()
        register_theme_listener(self.apply_theme)
        self._refresh_all()

    def _build_menus_and_toolbar(self) -> None:
        self.toolbar = QToolBar("연구 도구 모음", self)
        self.toolbar.setObjectName("material.toolbar")
        self.toolbar.setMovable(False)
        self.toolbar.setIconSize(QSize(16, 16))
        self.addToolBar(self.toolbar)

        file_menu = self.menuBar().addMenu("파일(&F)")
        study_menu = self.menuBar().addMenu("연구(&S)")
        tools_menu = self.menuBar().addMenu("도구(&T)")
        view_menu = self.menuBar().addMenu("보기(&V)")

        def _add(menu, text, slot, shortcut=None, icon_name=None, toolbar=False):
            act = QAction(text, self)
            if icon_name:
                act.setIcon(load_ui_icon(icon_name, self._theme_mode))
            act.triggered.connect(slot)
            if shortcut:
                act.setShortcut(shortcut)
            menu.addAction(act)
            if toolbar:
                self.toolbar.addAction(act)
            return act

        self.act_new = _add(file_menu, "새 연구", self.on_new_study, QKeySequence.StandardKey.New, "folder", True)
        self.act_open = _add(file_menu, "열기…", self.on_open_study, QKeySequence.StandardKey.Open, "folder", True)
        self.act_save = _add(file_menu, "저장", self.on_save_study, QKeySequence.StandardKey.Save, "save", True)
        self.act_save_as = _add(file_menu, "다른 이름으로 저장…", self.on_save_as_study, "Ctrl+Shift+S", "save")
        file_menu.addSeparator()
        self.act_close = _add(file_menu, "닫기", self.close, QKeySequence.StandardKey.Close)

        self.act_import = _add(study_menu, "실험 CSV 불러오기…", self.on_import_csv, None, "material", True)
        self.act_evaluate = _add(study_menu, "단일 평가", self.on_evaluate, "F5", "check", True)
        self.act_optimize = _add(study_menu, "보정 실행", self.on_optimize, "F6", "run", True)
        self.act_stop = _add(study_menu, "최선값 유지 중지(Stop)", self.on_stop, None, "step", True)
        self.act_cancel = _add(study_menu, "취소(Cancel)", self.on_cancel, None, "solver", True)
        study_menu.addSeparator()
        self.act_confirm_holdout = _add(study_menu, "보류 시험 최종 확인(Confirm Holdout)", self.on_confirm_holdout, None, "result", True)
        self.act_export = _add(study_menu, "보고서 내보내기…", self.on_export_report, None, "result")

        self.act_provenance = _add(tools_menu, "상세 출처 · 라이브러리 정보…", self.on_show_provenance)
        self.act_theme = _add(view_menu, theme_toggle_label(self._theme_mode), toggle_theme_mode)

        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(160)
        self.progress_bar.hide()
        self.status_bar.addPermanentWidget(self.progress_bar)

    def _build_layout(self) -> None:
        # Left control dock (<= 30% width, around 295px so central workspace >= 70% of window area at 1440x900)
        self.control_dock = QDockWidget("연구 탐색 · 실행 제어", self)
        self.control_dock.setObjectName("material.control_dock")
        self.control_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.control_dock.setMinimumWidth(240)
        self.control_dock.setMaximumWidth(280)

        dock_scroll = QScrollArea()
        dock_scroll.setWidgetResizable(True)
        dock_scroll.setFrameShape(QFrame.Shape.NoFrame)
        dock_scroll.viewport().setAutoFillBackground(False)
        dock_body = QWidget()
        dock_body.setObjectName("propsPanel")
        dock_layout = QVBoxLayout(dock_body)
        dock_layout.setContentsMargins(8, 8, 8, 8)
        dock_layout.setSpacing(8)

        # Study overview card
        summary_card = QFrame()
        summary_card.setObjectName("sectionCard")
        sum_layout = QVBoxLayout(summary_card)
        sum_layout.setContentsMargins(10, 8, 10, 8)
        sum_layout.setSpacing(4)
        self.study_title_label = QLabel("새 재료 보정 연구")
        self.study_title_label.setObjectName("cardTitle")
        self.study_title_label.setWordWrap(True)
        self.model_label = QLabel("모델: 점탄성 2항 Prony + WLF (시간-온도 중첩)")
        self.model_label.setWordWrap(True)
        sum_layout.addWidget(self.study_title_label)
        sum_layout.addWidget(self.model_label)

        badge_grid = QGridLayout()
        badge_grid.setSpacing(4)
        self.badge_fit = QLabel("학습: 미확인")
        self.badge_val = QLabel("검증: 미확인")
        self.badge_hold = QLabel("보류: 미확인")
        self.badge_const = QLabel("제약: 미확인")
        for b in (self.badge_fit, self.badge_val, self.badge_hold, self.badge_const):
            b.setWordWrap(True)
        badge_grid.addWidget(self.badge_fit, 0, 0)
        badge_grid.addWidget(self.badge_val, 0, 1)
        badge_grid.addWidget(self.badge_hold, 1, 0)
        badge_grid.addWidget(self.badge_const, 1, 1)
        sum_layout.addLayout(badge_grid)
        dock_layout.addWidget(summary_card)

        # Action control panel
        ctrl_group = QGroupBox("보정 작업 실행")
        ctrl_layout = QVBoxLayout(ctrl_group)
        ctrl_layout.setSpacing(5)

        self.btn_import = QPushButton("실험 CSV 불러오기…")
        self.btn_import.clicked.connect(self.on_import_csv)
        self.btn_eval = QPushButton("단일 평가")
        self.btn_eval.clicked.connect(self.on_evaluate)
        self.btn_opt = QPushButton("보정 실행")
        self.btn_opt.setObjectName("primary")
        self.btn_opt.clicked.connect(self.on_optimize)

        stop_row = QHBoxLayout()
        self.btn_stop = QPushButton("최선값 유지 중지")
        self.btn_stop.clicked.connect(self.on_stop)
        self.btn_cancel = QPushButton("취소")
        self.btn_cancel.clicked.connect(self.on_cancel)
        stop_row.addWidget(self.btn_stop)
        stop_row.addWidget(self.btn_cancel)

        self.btn_confirm = QPushButton("보류 시험 최종 확인")
        self.btn_confirm.clicked.connect(self.on_confirm_holdout)
        self.btn_export = QPushButton("보고서 내보내기…")
        self.btn_export.clicked.connect(self.on_export_report)

        for btn in (self.btn_import, self.btn_eval, self.btn_opt):
            ctrl_layout.addWidget(btn)
        ctrl_layout.addLayout(stop_row)
        ctrl_layout.addWidget(self.btn_confirm)
        ctrl_layout.addWidget(self.btn_export)
        dock_layout.addWidget(ctrl_group)

        # C9 study hierarchy tree
        tree_lbl = QLabel("연구 구성")
        tree_lbl.setObjectName("cardTitle")
        dock_layout.addWidget(tree_lbl)
        self.study_tree = QTreeWidget()
        self.study_tree.setHeaderHidden(True)
        self.study_tree.itemClicked.connect(self._on_tree_clicked)
        dock_layout.addWidget(self.study_tree, 1)

        dock_scroll.setWidget(dock_body)
        self.control_dock.setWidget(dock_scroll)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.control_dock)
        self.resizeDocks([self.control_dock], [270], Qt.Orientation.Horizontal)

        # Central workspace (>= 70% window area): Plot top + Tabs bottom
        self.central_workspace = QWidget()
        central_layout = QVBoxLayout(self.central_workspace)
        central_layout.setContentsMargins(6, 4, 6, 4)
        central_layout.setSpacing(4)

        self.main_splitter = QSplitter(Qt.Orientation.Vertical)

        # Top plot card
        plot_panel = QWidget()
        plot_layout = QVBoxLayout(plot_panel)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.setSpacing(4)

        plot_bar = QHBoxLayout()
        plot_bar.addWidget(QLabel("그래프 표시 대상"))
        self.plot_selector = _configure_combo(QComboBox())
        self.plot_selector.currentIndexChanged.connect(self._redraw_plots)
        plot_bar.addWidget(self.plot_selector)
        plot_bar.addStretch(1)
        self.loss_summary_label = QLabel("적합 손실: 미확인")
        self.loss_summary_label.setWordWrap(False)
        plot_bar.addWidget(self.loss_summary_label)
        plot_layout.addLayout(plot_bar)

        self.fig = Figure(figsize=(10, 4.2), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.mpl_toolbar = NavigationToolbar2QT(self.canvas, self)
        self.mpl_toolbar.setObjectName("mplToolbar")
        self.ax_curve = self.fig.add_subplot(121)
        self.ax_resid = self.fig.add_subplot(122)
        plot_layout.addWidget(self.mpl_toolbar)
        plot_layout.addWidget(self.canvas, 1)
        self.main_splitter.addWidget(plot_panel)

        # Bottom detail tabs
        self.detail_tabs = QTabWidget()

        # Tab 1: 실험 데이터 미리보기 (up to 100 rows + unit/split columns)
        preview_tab = QWidget()
        prev_layout = QVBoxLayout(preview_tab)
        prev_layout.setContentsMargins(6, 6, 6, 6)
        prev_top = QHBoxLayout()
        prev_top.addWidget(QLabel("실험 선택"))
        self.preview_exp_combo = _configure_combo(QComboBox())
        self.preview_exp_combo.currentIndexChanged.connect(self._populate_experiment_preview)
        prev_top.addWidget(self.preview_exp_combo)
        prev_top.addStretch(1)
        self.preview_meta_label = QLabel("가져온 실험 데이터가 없습니다.")
        prev_top.addWidget(self.preview_meta_label)
        prev_layout.addLayout(prev_top)
        self.exp_preview_table = QTableWidget(0, 6)
        self.exp_preview_table.setAlternatingRowColors(True)
        self.exp_preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        prev_layout.addWidget(self.exp_preview_table, 1)
        self.detail_tabs.addTab(preview_tab, "실험 데이터 미리보기")

        # Tab 2: 매개변수 · 제약
        param_tab = QWidget()
        param_layout = QVBoxLayout(param_tab)
        param_layout.setContentsMargins(6, 6, 6, 6)
        param_bar = QHBoxLayout()
        self.btn_apply_params = QPushButton("매개변수 초안 확정")
        self.btn_apply_params.clicked.connect(self.on_apply_parameter_draft)
        param_bar.addWidget(self.btn_apply_params)
        param_bar.addStretch(1)
        self.param_info_label = QLabel("자유 계수를 편집한 뒤 확정을 누르면 다음 평가에 반영됩니다.")
        param_bar.addWidget(self.param_info_label)
        param_layout.addLayout(param_bar)

        param_split = QSplitter(Qt.Orientation.Horizontal)
        self.param_table = QTableWidget(0, 7)
        self.param_table.setHorizontalHeaderLabels(
            ["매개변수", "현재값", "단위", "역할", "최솟값", "최댓값", "변환"]
        )
        self.param_table.setAlternatingRowColors(True)
        self.param_table.verticalHeader().setVisible(False)
        p_hdr = self.param_table.horizontalHeader()
        p_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 7):
            p_hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        p_hdr.setMinimumSectionSize(52)
        self.param_table.itemChanged.connect(self._on_param_cell_changed)

        self.constraint_table = QTableWidget(0, 4)
        self.constraint_table.setHorizontalHeaderLabels(["물리 제약 조건", "기준값", "현재값", "판정"])
        self.constraint_table.setAlternatingRowColors(True)
        self.constraint_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.constraint_table.verticalHeader().setVisible(False)
        c_hdr = self.constraint_table.horizontalHeader()
        c_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 4):
            c_hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        c_hdr.setMinimumSectionSize(52)

        param_split.addWidget(self.param_table)
        param_split.addWidget(self.constraint_table)
        param_split.setSizes([640, 420])
        param_layout.addWidget(param_split, 1)
        self.detail_tabs.addTab(param_tab, "매개변수 · 제약")

        # Tab 3: 평가 이력 · 잔차 요약
        history_tab = QWidget()
        hist_layout = QVBoxLayout(history_tab)
        hist_layout.setContentsMargins(6, 6, 6, 6)
        hist_split = QSplitter(Qt.Orientation.Horizontal)

        self.history_table = QTableWidget(0, 8)
        self.history_table.setHorizontalHeaderLabels(
            ["번호", "단계", "상태", "적합 손실", "학습", "검증", "보류", "제약 · 식별"]
        )
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history_table.itemSelectionChanged.connect(self._on_history_row_selected)

        self.channel_summary_table = QTableWidget(0, 6)
        self.channel_summary_table.setHorizontalHeaderLabels(
            ["실험", "관측량", "분할", "손실", "최대 오차", "판정"]
        )
        self.channel_summary_table.setAlternatingRowColors(True)
        self.channel_summary_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.channel_summary_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        hist_split.addWidget(self.history_table)
        hist_split.addWidget(self.channel_summary_table)
        hist_split.setSizes([540, 520])
        hist_layout.addWidget(hist_split, 1)
        self.detail_tabs.addTab(history_tab, "평가 이력 · 잔차 요약")

        self.main_splitter.addWidget(self.detail_tabs)
        self.main_splitter.setSizes([470, 340])
        central_layout.addWidget(self.main_splitter, 1)
        self.setCentralWidget(self.central_workspace)

    def apply_theme(self, mode: str | None = None) -> None:
        if getattr(self, "_closed", False):
            return
        resolved = mode if mode in ("dark", "light") else current_theme_mode()
        self._theme_mode = resolved
        pal = get_palette(resolved)
        toolbar_bg = "#cbd5e1" if resolved == "dark" else str(pal["header_bg"])
        extra_qss = (
            f"\nQScrollArea > QWidget > QWidget {{ background: {pal['surface_bg']}; }}"
            f"\nQWidget#propsPanel {{ background: {pal['surface_bg']}; }}"
            f"\nQTableCornerButton::section {{ background: {pal['header_bg']}; border: none; }}"
            f"\nQTableWidget, QTableView, QTreeWidget, QTreeView, QAbstractItemView {{ background-color: {pal['surface_bg']}; alternate-background-color: {pal['card_alt_bg']}; color: {pal['text_primary']}; gridline-color: {pal['border']}; }}"
            f"\nQToolBar#mplToolbar {{ background: {toolbar_bg}; border-radius: 5px; padding: 2px; border: 1px solid {pal['border']}; }}"
            f"\nQToolBar#mplToolbar QToolButton {{ color: #0f172a; background: transparent; border-radius: 3px; padding: 2px; }}"
            f"\nQToolBar#mplToolbar QToolButton:hover {{ background: #94a3b8; }}"
        )
        self.setStyleSheet(build_stylesheet(resolved) + extra_qss)
        self.act_theme.setText(theme_toggle_label(resolved))
        self._update_status_badges()
        self._redraw_plots()

    def _update_status_badges(self) -> None:
        if getattr(self, "_closed", False):
            return
        art = self._active_eval_artifact
        if art is None:
            for badge, prefix in (
                (self.badge_fit, "학습"),
                (self.badge_val, "검증"),
                (self.badge_hold, "보류"),
                (self.badge_const, "제약"),
            ):
                badge.setText(f"{prefix}: 미확인")
                badge.setStyleSheet(status_badge_style("neutral", self._theme_mode))
            return

        assess = art.assessment.assessment
        def _tone(st: str) -> str:
            if st in ("pass", "FEASIBLE"):
                return "ready"
            if st in ("fail", "INFEASIBLE", "not_identifiable"):
                return "error"
            if st == "MARGINAL":
                return "warning"
            return "neutral"

        for badge, prefix, key in (
            (self.badge_fit, "학습", "fit_status"),
            (self.badge_val, "검증", "validation_status"),
            (self.badge_hold, "보류", "holdout_status"),
            (self.badge_const, "제약", "constraint_status"),
        ):
            val = str(assess.get(key, "not_assessed"))
            badge.setText(f"{prefix}: {_ko_status(val)}")
            badge.setStyleSheet(status_badge_style(_tone(val), self._theme_mode))

    def _sync_actions(self) -> None:
        if getattr(self, "_closed", False):
            return
        busy = self._busy_op is not None
        running_camp = self._active_campaign_id is not None
        ro = self.service.read_only
        has_exps = len(self.service.experiments()) > 0
        has_fit = any(c.required_for_fit for c in self.service.comparisons())
        has_free = any(p.role == "free" for p in self.service.parameters())
        has_eval = self._active_eval_artifact is not None

        for act, btn in (
            (self.act_import, self.btn_import),
            (self.act_evaluate, self.btn_eval),
        ):
            enabled = (not busy) and (not ro) and (act is self.act_import or has_exps)
            act.setEnabled(enabled)
            btn.setEnabled(enabled)

        opt_ok = (not busy) and (not ro) and has_fit and has_free
        self.act_optimize.setEnabled(opt_ok)
        self.btn_opt.setEnabled(opt_ok)

        self.act_stop.setEnabled(running_camp)
        self.btn_stop.setEnabled(running_camp)
        self.act_cancel.setEnabled(running_camp)
        self.btn_cancel.setEnabled(running_camp)

        conf_ok = (not busy) and (not ro) and has_exps
        self.act_confirm_holdout.setEnabled(conf_ok)
        self.btn_confirm.setEnabled(conf_ok)

        exp_ok = (not busy) and (not ro) and has_eval
        self.act_export.setEnabled(exp_ok)
        self.btn_export.setEnabled(exp_ok)

        self.act_save.setEnabled((not busy) and (not ro))
        self.act_save_as.setEnabled(not busy)
        self.act_new.setEnabled(not busy)
        self.act_open.setEnabled(not busy)
        self.btn_apply_params.setEnabled((not busy) and (not ro))

        self.progress_bar.setVisible(busy)
        if busy:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)

    def _refresh_all(self) -> None:
        if getattr(self, "_closed", False):
            return
        snap = self.service.snapshot()
        ro_tag = " (읽기 전용)" if snap.read_only else ""
        self.study_title_label.setText(f"{self.service.name}{ro_tag}")
        desc = self.service._descriptor()
        self.model_label.setText(f"모델: {desc.name}")

        self._populate_tree()
        self._populate_preview_selector()
        self._populate_history_table()
        self._load_selected_evaluation()
        self._sync_actions()

    def _populate_tree(self) -> None:
        self.study_tree.clear()
        desc = self.service._descriptor()
        exps = self.service.experiments()
        params = self.service.parameters()
        comps = self.service.comparisons()
        evals = self.service.list_evaluations()
        camps = self.service._manifest.get("campaigns", {})
        exports = self.service._manifest.get("exports", ())

        def _root(title: str, icon_name: str, key: str) -> QTreeWidgetItem:
            item = QTreeWidgetItem([title])
            item.setData(0, Qt.ItemDataRole.UserRole, (key, None))
            item.setIcon(0, load_ui_icon(icon_name, self._theme_mode))
            self.study_tree.addTopLevelItem(item)
            return item

        m_root = _root("모델", "material", "model")
        m_root.addChild(QTreeWidgetItem([desc.name]))

        e_root = _root(f"실험 데이터 ({len(exps)})", "folder", "experiments")
        for exp in exps:
            temp = exp.conditions.get("temperature_K")
            t_txt = f" · {temp:.2f} K" if isinstance(temp, (int, float)) else ""
            ch_txt = ", ".join(_ko_observable(ch.observable_id) for ch in exp.channels)
            child = QTreeWidgetItem([f"{exp.experiment_id} ({_ko_split(exp.split)}{t_txt}) · {ch_txt}"])
            child.setData(0, Qt.ItemDataRole.UserRole, ("experiment", exp.experiment_id))
            e_root.addChild(child)

        p_root = _root(f"매개변수 ({len(params)})", "section", "parameters")
        for p in params:
            p_root.addChild(QTreeWidgetItem([f"{p.name}: {p.default} {p.unit} ({_ROLE_KO.get(p.role, p.role)})"]))

        c_root = _root(f"비교 설정 ({len(comps)})", "check", "comparisons")
        for c in comps:
            c_root.addChild(QTreeWidgetItem([f"{c.experiment_id} · {_ko_observable(c.observable_id)} ({_ko_split(c.split)})"]))

        camp_root = _root(f"최적화 실행 ({len(camps)})", "run", "campaigns")
        for idx, camp in enumerate(camps.values(), 1):
            camp_root.addChild(QTreeWidgetItem([f"실행 #{idx} · {_ko_status(camp.get('state'))}"]))

        ev_root = _root(f"평가 이력 ({len(evals)})", "result", "evaluations")
        for idx, ev in enumerate(evals, 1):
            ev_id = ev["evaluation_id"]
            phase = _ko_status(ev.get("execution_phase"))
            loss = ev.get("fit_loss")
            loss_txt = f"{loss:.3e}" if isinstance(loss, (int, float)) else "미확인"
            child = QTreeWidgetItem([f"평가 #{idx} ({phase}) · 손실 {loss_txt}"])
            child.setData(0, Qt.ItemDataRole.UserRole, ("evaluation", ev_id))
            ev_root.addChild(child)

        exp_root = _root(f"내보내기 ({len(exports)})", "save", "exports")
        for idx, _ in enumerate(exports, 1):
            exp_root.addChild(QTreeWidgetItem([f"매개변수·정식화 보고서 #{idx}"]))

        rev_root = _root("검토", "expert", "reviews")
        rev_root.addChild(QTreeWidgetItem(["독립 평가 기록 준비 상태: " + ("가능" if evals else "미확인")]))

        self.study_tree.expandAll()

    def _on_tree_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple):
            return
        kind, val = data
        if kind == "experiments" or kind == "experiment":
            self.detail_tabs.setCurrentIndex(0)
            if val:
                idx = self.preview_exp_combo.findData(val)
                if idx >= 0:
                    self.preview_exp_combo.setCurrentIndex(idx)
        elif kind == "parameters" or kind == "comparisons":
            self.detail_tabs.setCurrentIndex(1)
        elif kind == "evaluations" or kind == "evaluation":
            self.detail_tabs.setCurrentIndex(2)
            if val:
                self._selected_eval_id = val
                self._load_selected_evaluation()

    def _populate_parameter_table(self) -> None:
        params = self.service.parameters()
        art = self._active_eval_artifact
        active_vals = dict(art.physical_parameters) if art is not None else {}

        self.param_table.blockSignals(True)
        self.param_table.setRowCount(len(params))
        ro = self.service.read_only
        for row, p in enumerate(params):
            val = active_vals.get(p.id, p.default)
            items = [
                (p.name, False, p.id),
                (f"{val:.6g}" if isinstance(val, (int, float)) else str(val or ""), (not ro) and p.role != "linked", None),
                (p.unit or "-", False, None),
                (_ROLE_KO.get(p.role, p.role), False, None),
                (f"{p.lower:.6g}" if p.lower is not None else "-", False, None),
                (f"{p.upper:.6g}" if p.upper is not None else "-", False, None),
                (_TRANSFORM_KO.get(p.transform, p.transform), False, None),
            ]
            for col, (text, editable, user_id) in enumerate(items):
                cell = QTableWidgetItem(text)
                if user_id is not None:
                    cell.setData(Qt.ItemDataRole.UserRole, user_id)
                if not editable:
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.param_table.setItem(row, col, cell)
        self.param_table.blockSignals(False)

        # Populate constraints summary
        if art is not None:
            checks = art.assessment.assessment.get("constraint_checks", ())
        else:
            checks = [
                {"constraint_id": c.id, "limit": c.limit, "value": None, "status": "NOT_ASSESSED"}
                for c in (*self.service.mandatory_constraints(), *self.service.user_constraints())
            ]
        const_names = {
            "prony_fraction_sum_max": "Prony 분율 합 (a1+a2 <= 0.95)",
            "prony_tau_order_ratio": "시간상수 순서비 (τ2/τ1 >= 2.0)",
            "prony_e_inf_positive": "평형 탄성계수 E∞ > 0",
        }
        self.constraint_table.setRowCount(len(checks))
        for row, chk in enumerate(checks):
            cid = str(chk.get("constraint_id", ""))
            val = chk.get("value")
            lim = chk.get("limit")
            st = str(chk.get("status", "NOT_ASSESSED"))
            self.constraint_table.setItem(row, 0, QTableWidgetItem(const_names.get(cid, cid)))
            self.constraint_table.setItem(row, 1, QTableWidgetItem(f"{lim:.6g}" if isinstance(lim, (int, float)) else "-"))
            self.constraint_table.setItem(row, 2, QTableWidgetItem(f"{val:.6g}" if isinstance(val, (int, float)) else "미확인"))
            self.constraint_table.setItem(row, 3, QTableWidgetItem(_ko_status(st)))

    def _on_param_cell_changed(self, _item: QTableWidgetItem) -> None:
        self._draft_revision += 1

    def _collect_draft_parameter_specs(self) -> list[ParameterSpec]:
        specs = list(self.service.parameters())
        updated: list[ParameterSpec] = []
        for row, p in enumerate(specs):
            item = self.param_table.item(row, 1)
            if item is None or p.role == "linked":
                updated.append(p)
                continue
            try:
                val = float(item.text().strip())
            except ValueError as exc:
                raise MaterialStudyError("INVALID_PARAMETER", "parameter_table", {"reason": f"'{p.name}' 값이 올바른 숫자가 아닙니다."}) from exc
            lo = p.lower
            hi = p.upper
            if lo is not None and val < lo:
                lo = val
            if hi is not None and val > hi:
                hi = val
            updated.append(
                ParameterSpec(
                    id=p.id,
                    name=p.name,
                    unit=p.unit,
                    lower=lo,
                    upper=hi,
                    default=val,
                    transform=p.transform,
                    role=p.role,
                    expression=p.expression,
                    dependencies=p.dependencies,
                )
            )
        return updated

    def _populate_preview_selector(self) -> None:
        current = self.preview_exp_combo.currentData()
        self.preview_exp_combo.blockSignals(True)
        self.preview_exp_combo.clear()
        for exp in self.service.experiments():
            temp = exp.conditions.get("temperature_K")
            t_txt = f" ({temp:.2f} K)" if isinstance(temp, (int, float)) else ""
            self.preview_exp_combo.addItem(f"{exp.experiment_id} · {_ko_split(exp.split)}{t_txt}", exp.experiment_id)
        if current:
            idx = self.preview_exp_combo.findData(current)
            if idx >= 0:
                self.preview_exp_combo.setCurrentIndex(idx)
        self.preview_exp_combo.blockSignals(False)
        self._populate_experiment_preview()

    def _populate_experiment_preview(self) -> None:
        exp_id = self.preview_exp_combo.currentData()
        exps = {e.experiment_id: e for e in self.service.experiments()}
        if not exp_id or exp_id not in exps:
            self.exp_preview_table.setRowCount(0)
            self.exp_preview_table.setColumnCount(0)
            self.preview_meta_label.setText("가져온 실험 데이터가 없습니다.")
            return
        exp = exps[exp_id]
        exp_data = self.service._manifest.get("experiment_data", {}).get(exp_id, {})
        if not exp.channels or not exp_data:
            return
        ch0 = exp.channels[0]
        x_arr, y0_arr, sk_arr, _ = load_experiment_channel_arrays(exp_data[ch0.observable_id], data_root=self.service.data_root)
        max_n = min(100, x_arr.size)

        cols = ["원본 행", f"{ch0.independent_column} ({ch0.independent_unit})"]
        y_arrays = [y0_arr]
        cols.append(f"{_ko_observable(ch0.observable_id)} ({ch0.observable.unit})")
        for extra_ch in exp.channels[1:]:
            _, yk_arr, _, _ = load_experiment_channel_arrays(exp_data[extra_ch.observable_id], data_root=self.service.data_root)
            y_arrays.append(yk_arr)
            cols.append(f"{_ko_observable(extra_ch.observable_id)} ({extra_ch.observable.unit})")
        cols.extend(["분할", "조건(온도)"])

        temp = exp.conditions.get("temperature_K")
        temp_str = f"{temp:.2f} K" if isinstance(temp, (int, float)) else "-"
        split_str = _ko_split(exp.split)

        self.exp_preview_table.setColumnCount(len(cols))
        self.exp_preview_table.setHorizontalHeaderLabels(cols)
        self.exp_preview_table.setRowCount(max_n)
        for r in range(max_n):
            self.exp_preview_table.setItem(r, 0, QTableWidgetItem(str(int(sk_arr[r, 0]))))
            self.exp_preview_table.setItem(r, 1, QTableWidgetItem(f"{float(x_arr[r]):.6g}"))
            for c_off, y_arr in enumerate(y_arrays):
                self.exp_preview_table.setItem(r, 2 + c_off, QTableWidgetItem(f"{float(y_arr[r, 0]):.6g}"))
            self.exp_preview_table.setItem(r, 2 + len(y_arrays), QTableWidgetItem(split_str))
            self.exp_preview_table.setItem(r, 3 + len(y_arrays), QTableWidgetItem(temp_str))
        self.exp_preview_table.resizeColumnsToContents()
        self.preview_meta_label.setText(f"전체 {x_arr.size}행 중 {max_n}행 표시 · 분할: {split_str} · 온도: {temp_str}")

    def _populate_history_table(self) -> None:
        evals = self.service.list_evaluations()
        self.history_table.blockSignals(True)
        self.history_table.setRowCount(len(evals))
        sel_id = self._selected_eval_id or self.service._manifest.get("selected_evaluation_id")
        row_to_select: int | None = None

        for row, ev in enumerate(evals):
            eid = ev["evaluation_id"]
            if eid == sel_id:
                row_to_select = row
            loss = ev.get("fit_loss")
            loss_txt = f"{loss:.3e}" if isinstance(loss, (int, float)) else "미확인"

            fit_s = val_s = hold_s = const_ident_s = "미확인"
            # Read lightweight assessment summary if present
            try:
                art_path = self.service.data_root / ev["artifact_ref"]["path"]
                if art_path.is_file():
                    raw_art = json.loads(art_path.read_text(encoding="utf-8"))
                    assess = raw_art.get("assessment", {}).get("assessment", {})
                    fit_s = _ko_status(assess.get("fit_status"))
                    val_s = _ko_status(assess.get("validation_status"))
                    # Respect Holdout Seal in history table!
                    if ev.get("execution_phase") == "confirmation" and ev.get("holdout_exposed", False):
                        hold_s = _ko_status(assess.get("holdout_status"))
                    else:
                        hold_s = "미확인"
                    const_ident_s = f"{_ko_status(assess.get('constraint_status'))} · {_ko_status(assess.get('identifiability_status'))}"
            except Exception:
                pass

            vals = [
                f"#{row + 1}",
                _ko_status(ev.get("execution_phase")),
                _ko_status(ev.get("state")),
                loss_txt,
                fit_s,
                val_s,
                hold_s,
                const_ident_s,
            ]
            for col, text in enumerate(vals):
                cell = QTableWidgetItem(text)
                if col == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, eid)
                self.history_table.setItem(row, col, cell)

        if row_to_select is None and evals:
            row_to_select = len(evals) - 1
            self._selected_eval_id = evals[-1]["evaluation_id"]
        if row_to_select is not None:
            self.history_table.selectRow(row_to_select)
        self.history_table.blockSignals(False)

    def _on_history_row_selected(self) -> None:
        rows = self.history_table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.history_table.item(rows[0].row(), 0)
        if item is None:
            return
        eid = item.data(Qt.ItemDataRole.UserRole)
        if eid:
            self._selected_eval_id = str(eid)
            self._load_selected_evaluation()

    def _load_selected_evaluation(self) -> None:
        eid = self._selected_eval_id or self.service._manifest.get("selected_evaluation_id")
        if not eid:
            self._active_eval_artifact = None
            self.loss_summary_label.setText("적합 손실: 미확인")
            self.channel_summary_table.setRowCount(0)
            self._populate_parameter_table()
            self._update_status_badges()
            self._refresh_plot_selector()
            self._redraw_plots()
            return

        # Never trigger holdout exposure automatically on passive UI refresh for unexposed confirmation
        ev_meta = self.service._manifest.get("evaluations", {}).get(eid, {})
        if ev_meta.get("execution_phase") == "confirmation" and not ev_meta.get("holdout_exposed", False):
            self._active_eval_artifact = None
            self.loss_summary_label.setText("보류 시험 노출 기록 미완료")
            self.channel_summary_table.setRowCount(0)
            self._populate_parameter_table()
            self._update_status_badges()
            self._refresh_plot_selector()
            self._redraw_plots()
            return

        try:
            art = self.service.evaluation(eid)
        except Exception:
            self._active_eval_artifact = None
            self._populate_parameter_table()
            self._update_status_badges()
            self._refresh_plot_selector()
            self._redraw_plots()
            return

        self._active_eval_artifact = art
        self._populate_parameter_table()
        self._update_status_badges()
        fit_loss = art.assessment.objective.get("fit_loss")
        if isinstance(fit_loss, (int, float)):
            self.loss_summary_label.setText(f"적합 손실: {fit_loss:.3e}")
        else:
            self.loss_summary_label.setText("적합 손실: 무효")

        # Populate channel summary table (filtering out sealed holdout during exploration)
        visible_checks = [
            chk
            for chk in art.assessment.assessment.get("channel_checks", ())
            if not (art.execution_phase == "exploration" and chk.get("split") == "holdout")
        ]
        self.channel_summary_table.setRowCount(len(visible_checks))
        for row, chk in enumerate(visible_checks):
            loss = chk.get("loss")
            max_err = chk.get("max_abs_error")
            self.channel_summary_table.setItem(row, 0, QTableWidgetItem(str(chk.get("experiment_id", ""))))
            self.channel_summary_table.setItem(row, 1, QTableWidgetItem(_ko_observable(str(chk.get("observable_id", "")))))
            self.channel_summary_table.setItem(row, 2, QTableWidgetItem(_ko_split(str(chk.get("split", "")))))
            self.channel_summary_table.setItem(row, 3, QTableWidgetItem(f"{loss:.3e}" if isinstance(loss, (int, float)) else "미확인"))
            self.channel_summary_table.setItem(row, 4, QTableWidgetItem(f"{max_err:.4g}" if isinstance(max_err, (int, float)) else "미확인"))
            self.channel_summary_table.setItem(row, 5, QTableWidgetItem(_ko_status(str(chk.get("status", "not_assessed")))))

        self._populate_parameter_table()
        self._update_status_badges()
        self._refresh_plot_selector()
        self._redraw_plots()

    def _refresh_plot_selector(self) -> None:
        current = self.plot_selector.currentData()
        self.plot_selector.blockSignals(True)
        self.plot_selector.clear()
        self.plot_selector.addItem("전체 DMA (저장·손실 곡선 / 잔차)", ("family", "dma"))
        self.plot_selector.addItem("전체 응력 완화 곡선 / 잔차", ("family", "relax"))

        art = self._active_eval_artifact
        exps = {e.experiment_id: e for e in self.service.experiments()}
        for eid, exp in exps.items():
            # Never show holdout selector entries during exploration unless exposed in confirmation
            if exp.split == "holdout" and (art is None or art.execution_phase != "confirmation"):
                continue
            temp = exp.conditions.get("temperature_K")
            t_txt = f" {temp:.1f} K" if isinstance(temp, (int, float)) else ""
            self.plot_selector.addItem(f"{eid} ({_ko_split(exp.split)}{t_txt})", ("exp", eid))

        if current is not None:
            idx = self.plot_selector.findData(current)
            if idx >= 0:
                self.plot_selector.setCurrentIndex(idx)
        elif art is not None and not any(c.get("observable_id") in ("E_storage", "E_loss") for c in art.channel_results):
            self.plot_selector.setCurrentIndex(1)
        self.plot_selector.blockSignals(False)

    def _redraw_plots(self) -> None:
        if getattr(self, "_closed", False):
            return
        p = get_palette(self._theme_mode)
        bg = str(p["canvas_bg"])
        panel_bg = str(p["panel_bg"])
        fg = str(p["text_primary"])
        grid_color = str(p["canvas_grid_major"])

        self.fig.patch.set_facecolor(panel_bg)
        for ax in (self.ax_curve, self.ax_resid):
            ax.clear()
            ax.set_facecolor(bg)
            ax.tick_params(colors=fg, labelsize=9)
            ax.xaxis.label.set_color(fg)
            ax.yaxis.label.set_color(fg)
            ax.title.set_color(fg)
            for spine in ax.spines.values():
                spine.set_color(grid_color)
            ax.grid(True, alpha=0.35, color=grid_color)

        art = self._active_eval_artifact
        exps = {e.experiment_id: e for e in self.service.experiments()}
        exp_data = self.service._manifest.get("experiment_data", {})
        data_root = self.service.data_root
        sel = self.plot_selector.currentData() or ("family", "dma")
        sel_kind, sel_val = sel

        split_colors = {
            "train": "#38bdf8" if self._theme_mode == "dark" else "#0284c7",
            "validation": "#fbbf24" if self._theme_mode == "dark" else "#d97706",
            "holdout": "#f472b6" if self._theme_mode == "dark" else "#db2777",
        }

        if art is not None and art.channel_results:
            # Plot evaluated predicted vs observed curves & residuals
            from caelab.artifact_refs import ArtifactRefV1, read_safe_npy_v2

            channels = list(art.channel_results)
            if sel_kind == "exp":
                plot_channels = [c for c in channels if c.get("experiment_id") == sel_val]
            elif sel_val == "dma":
                plot_channels = [c for c in channels if c.get("observable_id") in ("E_storage", "E_loss", "E_star")]
                if not plot_channels:
                    plot_channels = channels
            else:
                plot_channels = [c for c in channels if c.get("observable_id") == "E_relax"]
                if not plot_channels:
                    plot_channels = channels

            use_log_x = True
            for c_entry in plot_channels:
                eid = str(c_entry["experiment_id"])
                obs_id = str(c_entry["observable_id"])
                sp = str(c_entry["split"])
                if art.execution_phase == "exploration" and sp == "holdout":
                    continue
                coords = read_safe_npy_v2(ArtifactRefV1.from_dict(c_entry["observed_coords_ref"]).path, root=data_root)
                obs_y = read_safe_npy_v2(ArtifactRefV1.from_dict(c_entry["observed_values_ref"]).path, root=data_root)[:, 0]
                pred_y = read_safe_npy_v2(ArtifactRefV1.from_dict(c_entry["predicted_on_obs_ref"]).path, root=data_root)[:, 0]
                resid = read_safe_npy_v2(ArtifactRefV1.from_dict(c_entry["residuals_ref"]).path, root=data_root)[:, 0]
                if np.any(coords <= 0.0):
                    use_log_x = False

                color = split_colors.get(sp, "#60a5fa")
                temp = exps[eid].conditions.get("temperature_K") if eid in exps else None
                t_tag = f" {temp:.1f}K" if isinstance(temp, (int, float)) else ""
                short_obs = "E'" if obs_id == "E_storage" else ("E''" if obs_id == "E_loss" else "E(t)")
                lbl = f"{_ko_split(sp)}{t_tag} {short_obs}"
                marker = "o" if obs_id != "E_loss" else "^"
                ls = "-" if obs_id != "E_loss" else "--"

                self.ax_curve.plot(coords, obs_y, marker, markersize=3.5, alpha=0.75, color=color)
                self.ax_curve.plot(coords, pred_y, ls, linewidth=1.6, color=color, label=lbl)
                self.ax_resid.plot(coords, resid, marker + ls, markersize=3.0, linewidth=1.1, alpha=0.85, color=color, label=lbl)

            if use_log_x:
                self.ax_curve.set_xscale("log")
                self.ax_resid.set_xscale("log")
                fmt = FuncFormatter(_format_log_tick)
                self.ax_curve.xaxis.set_major_formatter(fmt)
                self.ax_resid.xaxis.set_major_formatter(fmt)
            self.ax_resid.axhline(0.0, color=fg, linewidth=0.9, linestyle=":")
            x_label = "주파수 (Hz)" if any(c.get("observable_id") in ("E_storage", "E_loss") for c in plot_channels) else "시간 (s)"
            self.ax_curve.set_xlabel(x_label)
            self.ax_curve.set_ylabel("탄성계수 (MPa)")
            self.ax_curve.set_title("실험 관측값(점) vs 모델 예측 곡선(선)")
            self.ax_resid.set_xlabel(x_label)
            self.ax_resid.set_ylabel("정규화 잔차 (예측 − 관측) / 척도")
            self.ax_resid.set_title("시리즈 · 분할별 정규화 잔차")
            if len(plot_channels) <= 10:
                leg1 = self.ax_curve.legend(fontsize=8, loc="best")
                if leg1:
                    leg1.get_frame().set_facecolor(panel_bg)
                    for txt in leg1.get_texts():
                        txt.set_color(fg)
            if len(plot_channels) <= 8:
                leg2 = self.ax_resid.legend(fontsize=8, loc="best")
                if leg2:
                    leg2.get_frame().set_facecolor(panel_bg)
                    for txt in leg2.get_texts():
                        txt.set_color(fg)
        elif exps:
            # Show imported non-holdout observed curves before first evaluation
            plotted = False
            for eid, exp in exps.items():
                if exp.split == "holdout":
                    continue
                for ch in exp.channels:
                    ch_arr = exp_data.get(eid, {}).get(ch.observable_id)
                    if not ch_arr:
                        continue
                    x_obs, y_obs, _, _ = load_experiment_channel_arrays(ch_arr, data_root=data_root)
                    color = split_colors.get(exp.split, "#38bdf8")
                    self.ax_curve.plot(x_obs, y_obs[:, 0], "o", markersize=3.5, color=color, label=f"{eid} ({_ko_split(exp.split)})")
                    if np.all(x_obs > 0.0):
                        self.ax_curve.set_xscale("log")
                        self.ax_curve.xaxis.set_major_formatter(FuncFormatter(_format_log_tick))
                    plotted = True
            if plotted:
                self.ax_curve.set_title("가져온 실험 데이터 (단일 평가 또는 보정 실행 대기)")
                self.ax_curve.set_ylabel("탄성계수 (MPa)")
                leg = self.ax_curve.legend(fontsize=8, loc="best")
                if leg:
                    leg.get_frame().set_facecolor(panel_bg)
                    for txt in leg.get_texts():
                        txt.set_color(fg)
            else:
                self.ax_curve.text(0.5, 0.5, "실험 CSV를 불러온 뒤 평가 또는 보정을 실행하세요.", ha="center", va="center", color=fg, transform=self.ax_curve.transAxes)
            self.ax_resid.text(0.5, 0.5, "평가 실행 후 정규화 잔차가 표시됩니다.", ha="center", va="center", color=fg, transform=self.ax_resid.transAxes)
        else:
            self.ax_curve.text(0.5, 0.5, "실험 CSV를 불러와 재료 보정을 시작하세요.", ha="center", va="center", color=fg, transform=self.ax_curve.transAxes)
            self.ax_resid.text(0.5, 0.5, "평가 이력이 없습니다.", ha="center", va="center", color=fg, transform=self.ax_resid.transAxes)

        self.fig.tight_layout()
        self.canvas.draw_idle()

    def _start_worker(self, op_name: str, fn: Any, *, campaign_mode: bool = False) -> None:
        if self._busy_op is not None or self._closed:
            self.status_bar.showMessage("이전 작업이 완료될 때까지 기다려 주세요.")
            return
        self._busy_op = op_name
        op_token = object()
        self._op_token = op_token
        gen = self._generation
        mailbox = self._mailbox
        prog_mailbox = self._progress_mailbox
        self._sync_actions()
        self.status_bar.showMessage(f"{op_name} 실행 중…")

        def _on_progress(event: Mapping[str, Any]) -> None:
            prog_mailbox.put((gen, op_token, dict(event)))

        def _work() -> None:
            try:
                res = fn(_on_progress)
                mailbox.put((gen, op_token, op_name, True, res))
            except Exception as exc:
                mailbox.put((gen, op_token, op_name, False, exc))

        threading.Thread(target=_work, daemon=True).start()

    def _poll(self) -> None:
        if getattr(self, "_closed", False):
            return
        while True:
            try:
                gen, tok, ev = self._progress_mailbox.get_nowait()
            except queue.Empty:
                break
            if gen != self._generation or tok is not self._op_token:
                continue
            cid = ev.get("campaign_id")
            if cid:
                self._active_campaign_id = str(cid)
                self._sync_actions()
            if ev.get("stage") == "candidate_evaluated":
                comp = ev.get("completed", 0)
                loss = ev.get("fit_loss")
                loss_txt = f" · 손실 {loss:.3e}" if isinstance(loss, (int, float)) else ""
                self.status_bar.showMessage(f"보정 진행 중: 후보 {comp}회 평가{loss_txt}")

        while True:
            try:
                gen, tok, op_name, ok, val = self._mailbox.get_nowait()
            except queue.Empty:
                break
            if gen != self._generation or tok is not self._op_token:
                continue
            self._busy_op = None
            self._active_campaign_id = None
            self._op_token = None
            if not ok:
                msg = _human_error(val) if isinstance(val, Exception) else str(val)
                self.status_bar.showMessage(f"{op_name} 실패: {msg}")
                if not self._close_after:
                    QMessageBox.warning(self, op_name, msg)
            else:
                if op_name in ("단일 평가", "보류 시험 최종 확인"):
                    self._selected_eval_id = val.evaluation_id
                    self.status_bar.showMessage(f"{op_name} 완료")
                elif op_name == "보정 실행":
                    self._last_campaign_receipt = val
                    if val.selected_ids:
                        self._selected_eval_id = val.selected_ids[0]
                    self.status_bar.showMessage(
                        f"보정 종료 ({_ko_status(val.state)}): 총 {len(val.evaluation_ids)}회 평가"
                    )
                elif op_name == "보고서 내보내기":
                    self.status_bar.showMessage(f"보고서를 저장했습니다: {val.manifest_ref.path}")
                    if not self._close_after:
                        QMessageBox.information(
                            self,
                            "보고서 내보내기 완료",
                            "매개변수 · 정식화 · 평가 요약 보고서(JSON)를 저장했습니다.\n"
                            "(현재 모델은 솔버 고유 카드 내보내기 및 Prep 문서 직접 적용을 지원하지 않습니다.)",
                        )
                else:
                    self.status_bar.showMessage(f"{op_name} 완료")
            self._refresh_all()
            if self._close_after:
                self._close_after = False
                self.close()

    def wait_for_idle(self, timeout: float = 10.0) -> bool:
        import time

        deadline = time.monotonic() + timeout
        while self._busy_op is not None:
            self._poll()
            if self._busy_op is None:
                break
            if time.monotonic() >= deadline:
                return False
            QApplication.processEvents()
            time.sleep(0.01)
        self._poll()
        return self._busy_op is None

    def _apply_draft_to_service_sync(self) -> None:
        if self.service.read_only:
            return
        updated_params = self._collect_draft_parameter_specs()
        snap = self.service.snapshot()
        self.service.configure(
            parameters=updated_params,
            comparisons=self.service.comparisons(),
            assessment_policy=self.service.assessment_policy(),
            user_constraints=self.service.user_constraints(),
            expected_revision=snap.document_revision,
        )

    def on_apply_parameter_draft(self) -> None:
        try:
            self._apply_draft_to_service_sync()
            self.status_bar.showMessage("매개변수 초안을 연구에 반영했습니다.", 3000)
            self._refresh_all()
        except Exception as exc:
            QMessageBox.warning(self, "매개변수 확정 실패", _human_error(exc))

    def on_new_study(self) -> None:
        if self._busy_op is not None:
            return
        name, ok = QInputDialog.getText(self, "새 재료 연구", "연구 이름:", text="새 점탄성 재료 보정")
        if not ok or not name.strip():
            return
        dest, _ = QFileDialog.getSaveFileName(self, "새 연구 파일 위치", "material_study.caemat.json", "재료 연구 (*.caemat.json)")
        if not dest:
            return
        if not dest.endswith(".caemat.json"):
            dest = dest.removesuffix(".json") + ".caemat.json"
        try:
            new_svc = CalibrationStudyService.create(dest, name=name.strip(), model="B-VIS-TRS-1", adapter="1.0.0")
            self.service.close()
            self.service = new_svc
            self._generation += 1
            self._selected_eval_id = None
            self._active_eval_artifact = None
            self._refresh_all()
            self.status_bar.showMessage(f"새 연구를 만들었습니다: {Path(dest).name}")
        except Exception as exc:
            QMessageBox.warning(self, "새 연구 생성 실패", _human_error(exc))

    def on_open_study(self) -> None:
        if self._busy_op is not None:
            return
        src, _ = QFileDialog.getOpenFileName(self, "재료 연구 열기", "", "재료 연구 (*.caemat.json)")
        if not src:
            return
        try:
            new_svc = CalibrationStudyService.open(src, read_only=False)
            self.service.close()
            self.service = new_svc
            self._generation += 1
            self._selected_eval_id = self.service._manifest.get("selected_evaluation_id")
            self._refresh_all()
            self.status_bar.showMessage(f"연구를 열었습니다: {Path(src).name}")
        except Exception as exc:
            QMessageBox.warning(self, "연구 열기 실패", _human_error(exc))

    def on_save_study(self) -> None:
        if self._busy_op is not None or self.service.read_only:
            return
        try:
            self._apply_draft_to_service_sync()
            self.service.save(view_state={"selected_eval_id": self._selected_eval_id})
            self._refresh_all()
            self.status_bar.showMessage("연구를 저장했습니다.", 3000)
        except Exception as exc:
            QMessageBox.warning(self, "저장 실패", _human_error(exc))

    def on_save_as_study(self) -> None:
        if self._busy_op is not None:
            return
        dest, _ = QFileDialog.getSaveFileName(self, "다른 이름으로 저장", "copy.caemat.json", "재료 연구 (*.caemat.json)")
        if not dest:
            return
        if not dest.endswith(".caemat.json"):
            dest = dest.removesuffix(".json") + ".caemat.json"
        svc = self.service
        expected_rev = svc.snapshot().document_revision
        self._start_worker(
            "다른 이름으로 저장",
            lambda on_prog: svc.save_as(dest, expected_revision=expected_rev, on_progress=on_prog),
        )

    def on_import_csv(self) -> None:
        if self._busy_op is not None or self.service.read_only:
            return
        csv_file, _ = QFileDialog.getOpenFileName(self, "실험 CSV 선택", "", "CSV 파일 (*.csv)")
        if not csv_file:
            return
        try:
            dlg = ExperimentImportDialog(Path(csv_file), self)
        except Exception as exc:
            QMessageBox.warning(self, "CSV 읽기 실패", _human_error(exc))
            return
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            kw = dlg.build_import_kwargs()
            scale = kw.pop("scale")
            snap = self.service.snapshot()
            rec = self.service.import_experiment(
                kw["source"],
                experiment_id=kw["experiment_id"],
                channels=kw["channels"],
                column_mapping=kw["column_mapping"],
                unit_specs=kw["unit_specs"],
                sequence=kw["sequence"],
                conditions=kw["conditions"],
                split=kw["split"],
                replicate_group=kw["replicate_group"],
                expected_revision=snap.document_revision,
            )
            # Automatically register comparison specs for imported channels
            comps = list(self.service.comparisons())
            ch_count = len(kw["channels"])
            ch_w = 1.0 / ch_count if ch_count > 0 else 1.0
            for ch in kw["channels"]:
                comps.append(
                    ComparisonSpec(
                        id=f"cmp_{rec.experiment_id}_{ch.observable_id}",
                        experiment_id=rec.experiment_id,
                        observable_id=ch.observable_id,
                        split=kw["split"],
                        scale=scale,
                        weight=ch_w,
                        required_for_fit=(kw["split"] == "train"),
                        required_for_validation=(kw["split"] in ("validation", "holdout")),
                        acceptance_limit=0.05,
                        sample_weights_policy="log10_trapezoidal",
                    )
                )
            self.service.configure(
                parameters=self.service.parameters(),
                comparisons=comps,
                assessment_policy=self.service.assessment_policy(),
                user_constraints=self.service.user_constraints(),
                expected_revision=rec.document_revision,
            )
            self._refresh_all()
            self.status_bar.showMessage(f"실험 데이터를 불러왔습니다: {rec.experiment_id}")
        except Exception as exc:
            QMessageBox.warning(self, "실험 가져오기 실패", _human_error(exc))

    def on_evaluate(self) -> None:
        if self._busy_op is not None or self.service.read_only:
            return
        try:
            self._apply_draft_to_service_sync()
        except Exception as exc:
            QMessageBox.warning(self, "매개변수 확인 실패", _human_error(exc))
            return
        svc = self.service
        expected_rev = svc.snapshot().document_revision
        self._start_worker(
            "단일 평가",
            lambda on_prog: svc.evaluate(
                None,
                expected_revision=expected_rev,
                on_progress=on_prog,
                cache_policy="reuse",
                execution_phase="exploration",
            ),
        )

    def on_optimize(self) -> None:
        if self._busy_op is not None or self.service.read_only:
            return
        try:
            self._apply_draft_to_service_sync()
        except Exception as exc:
            QMessageBox.warning(self, "매개변수 확인 실패", _human_error(exc))
            return
        svc = self.service
        expected_rev = svc.snapshot().document_revision
        cfg = OptimizationConfig(
            algorithm="slsqp",
            seed=17,
            max_evaluations=1500,
            max_iterations=200,
            ftol=1e-10,
            finite_difference_step=1e-6,
        )
        self._start_worker(
            "보정 실행",
            lambda on_prog: svc.optimize(cfg, expected_revision=expected_rev, on_progress=on_prog),
            campaign_mode=True,
        )

    def on_stop(self) -> None:
        cid = self._active_campaign_id or self.service.snapshot().active_campaign_id
        if cid:
            self.service.stop(cid)
            self.status_bar.showMessage("최선값 유지 중지(Stop)를 요청했습니다…")

    def on_cancel(self) -> None:
        cid = self._active_campaign_id or self.service.snapshot().active_campaign_id
        if cid:
            self.service.cancel(cid)
            self.status_bar.showMessage("작업 취소를 요청했습니다…")

    def on_confirm_holdout(self) -> None:
        if self._busy_op is not None or self.service.read_only:
            return
        try:
            self._apply_draft_to_service_sync()
        except Exception as exc:
            QMessageBox.warning(self, "매개변수 확인 실패", _human_error(exc))
            return
        svc = self.service
        art = self._active_eval_artifact
        vals = dict(art.physical_parameters) if art is not None else None
        expected_rev = svc.snapshot().document_revision
        self._start_worker(
            "보류 시험 최종 확인",
            lambda on_prog: svc.evaluate(
                vals,
                expected_revision=expected_rev,
                on_progress=on_prog,
                cache_policy="refresh",
                execution_phase="confirmation",
            ),
        )

    def on_export_report(self) -> None:
        if self._busy_op is not None or self._active_eval_artifact is None:
            return
        dest, _ = QFileDialog.getSaveFileName(self, "보고서 내보내기", "material_report.json", "JSON 보고서 (*.json)")
        if not dest:
            return
        svc = self.service
        eval_id = self._active_eval_artifact.evaluation_id
        expected_rev = svc.snapshot().document_revision
        self._start_worker(
            "보고서 내보내기",
            lambda _on_prog: svc.export(eval_id, dest, expected_revision=expected_rev),
        )

    def on_show_provenance(self) -> None:
        """Progressive-disclosure dialog showing library/source provenance without cluttering main labels."""
        dlg = QDialog(self)
        dlg.setWindowTitle("상세 출처 · 라이브러리 검증 정보")
        dlg.resize(620, 420)
        layout = QVBoxLayout(dlg)
        snap = self.service.snapshot()
        adapter = self.service._adapter_or_none()
        cap = adapter.capabilities(self.service._descriptor(), None) if adapter else None
        info = {
            "연구 경로": snap.path,
            "내용 식별": snap.content_revision,
            "문서 식별": snap.document_revision,
            "모델": self.service._descriptor().model_id,
            "어댑터": f"{cap.adapter.id} v{cap.adapter.version}" if cap else "미등록",
            "솔버 카드 내보내기 지원": bool(cap and cap.supports_export),
            "열린 문서 적용 지원": bool(cap and cap.supports_apply),
        }
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(json.dumps(info, ensure_ascii=False, indent=2))
        layout.addWidget(view)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        dlg.exec()

    def resizeEvent(self, event: Any) -> None:
        if getattr(self, "_closed", False):
            return
        super().resizeEvent(event)

    def closeEvent(self, event: Any) -> None:
        if self._busy_op is not None:
            self._close_after = True
            cid = self._active_campaign_id or self.service.snapshot().active_campaign_id
            if cid:
                self.service.cancel(cid)
            event.ignore()
            return
        self._closed = True
        self._poll_timer.stop()
        try:
            self.service.close()
        finally:
            if self._temp_dir is not None:
                try:
                    self._temp_dir.cleanup()
                except Exception:
                    pass
                self._temp_dir = None
        event.accept()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CAELAB 재료 물성 보정 연구 창")
    parser.add_argument("--study", help="열려는 재료 연구 매니페스트(.caemat.json)")
    parser.add_argument("--read-only", action="store_true", help="읽기 전용으로 열기")
    parser.add_argument("--project", help="프로젝트 파일 경로")
    parser.add_argument("--node", help="프로젝트 노드 ID")
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    load_korean_font(app, 10)
    try:
        win = MaterialStudyWindow(study_path=args.study, read_only=args.read_only)
    except Exception as exc:
        traceback.print_exc()
        QMessageBox.critical(None, "재료 연구 창을 열 수 없습니다", _human_error(exc))
        return 1
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
