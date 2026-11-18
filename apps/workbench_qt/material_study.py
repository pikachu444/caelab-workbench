"""UI01 Independent Qt Material Calibration Study Window (C9)."""
from __future__ import annotations

import argparse
import ast
from collections.abc import Mapping, Sequence
from dataclasses import replace as replace_spec
import json
import math
import os
from pathlib import Path
import queue
import re
import sys
import threading
import traceback
from typing import Any

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QIcon, QKeySequence, QPainter, QPen, QPixmap
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

from caelab.artifact_refs import ArtifactRefV1, read_safe_npy_v2, validate_relative_artifact_path
from caelab.materials.contracts import (
    ComparisonSpec,
    ExperimentChannelSpec,
    ObservableSpec,
    ParameterSpec,
)
from caelab.materials.evaluator import build_variable_space, resolve_parameter_values
from caelab.materials.experiments import load_experiment_channel_arrays, preview_csv_rows
from caelab.materials.registry import CalibrationEvaluationArtifact, MaterialStudyError, default_registry
from caelab.materials.service import CalibrationStudyService
from caelab.materials.units import QuantityUnitSpec, convert_frequency_angular, convert_values
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
_WEIGHTS_KO = {
    "uniform": "균등",
    "trapezoidal": "사다리꼴",
    "log10_trapezoidal": "로그 사다리꼴",
}
_DMA_OBSERVABLES = ("E_storage", "E_loss", "E_star")
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
            "INVALID_CSV": "CSV 파일 형식이 올바르지 않거나 유한한 수가 필요합니다.",
            "INVALID_PARAMETER": "매개변수의 값, 범위, 역할 또는 변환을 확인하세요.",
            "INVALID_PARAMETER_UNIT_OR_VALUE": "매개변수의 값과 단위가 모델에 맞는지 확인하세요.",
            "INVALID_CONFIGURATION": "연구 설정을 확인하세요.",
            "DUPLICATE_EXPERIMENT_ID": "같은 이름의 실험이 이미 있습니다. 다른 이름을 입력하세요.",
            "OPTIMIZER_FAILED": "보정 계산에 실패했습니다.",
            "INVALID_EVALUATION_POLICY": "평가 방식과 캐시 설정을 확인하세요.",
            "INVALID_PATH": "연구나 자료의 경로가 올바르지 않습니다.",
            "STUDY_NOT_FOUND": "연구 파일을 찾을 수 없습니다.",
            "STUDY_ID_MISMATCH": "프로젝트 노드에 기록된 연구와 현재 파일의 연구가 일치하지 않습니다.",
            "STUDY_ALREADY_EXISTS": "이 위치에 연구가 이미 있습니다.",
            "STUDY_CLOSED": "이 연구는 이미 닫혔습니다.",
            "SOURCE_NOT_FOUND": "원자료 파일을 찾을 수 없습니다.",
            "SOURCE_CHANGED_DURING_COPY": "복사하는 동안 원본 연구가 바뀌었습니다.",
            "DESTINATION_ALREADY_EXISTS": "복사 대상 위치에 파일이 이미 있습니다.",
            "CORRUPT_MANIFEST": "연구 설정 파일이 손상되었습니다.",
            "CORRUPT_ARTIFACT": "저장된 평가, 노출 기록 또는 자료가 누락되었거나 손상되었습니다.",
            "EVALUATION_NOT_FOUND": "저장된 평가를 찾을 수 없습니다.",
            "SELECTION_MISMATCH": "고정한 선택과 현재 연구 내용 또는 매개변수가 일치하지 않습니다.",
            "HOLDOUT_REUSED_FOR_SELECTION": "이 보류 자료는 다른 선택의 확인에 이미 사용했습니다.",
            "INVALID_RECOVERY_ACTION": "지원하지 않는 복구 작업입니다.",
            "RECOVERY_TRANSACTION_NOT_FOUND": "복구할 저장 기록을 찾을 수 없습니다.",
            "RELINK_HASH_MISMATCH": "선택한 파일의 내용이 기존 원자료와 일치하지 않습니다.",
            "EXPERIMENT_METADATA_MISSING": "시험에 필요한 조건이나 메타데이터가 빠졌습니다.",
            "DATA_RECIPE_INCOMPATIBLE": "실험 자료와 지정한 열·분할·변환 조건이 일치하지 않습니다.",
            "MEASURE_MISMATCH": "관측량의 정의나 측정 방식이 일치하지 않습니다.",
            "PREDICTION_EXPERIMENT_MISMATCH": "모델이 반환한 예측의 실험 또는 개정이 실행 요청과 일치하지 않습니다.",
            "UNSUPPORTED_UNIT": "이 계산에서 지원하지 않는 단위입니다.",
            "UNSUPPORTED_OBSERVABLE": "이 모델에서 지원하지 않는 관측량입니다.",
            "UNSUPPORTED_MODEL": "이 재료 모델을 사용할 수 없습니다.",
            "UNSUPPORTED_ADAPTER_VERSION": "연구에 지정된 재료 모델 연결 버전을 사용할 수 없습니다.",
            "INTEGRATION_FAILED": "모델 계산 또는 결과 수집에 실패했습니다.",
            "NONFINITE_RESPONSE": "계산 결과에 유한하지 않은 수가 포함되었습니다.",
            "COVERAGE_INCOMPLETE": "예측 결과가 관측 구간 또는 필요한 표본을 모두 포함하지 않습니다.",
            "CONSTRAINT_RESPONSE_INVALID": "제약 판정에 필요한 값을 계산할 수 없습니다.",
            "REQUIRED_FIT_EXPERIMENT_MISSING": "적합에 필요한 실험 결과가 없습니다.",
            "NO_REQUIRED_FIT_COMPARISONS": "적합에 포함된 비교 설정이 없습니다.",
            "EXPERIMENT_NOT_EXECUTED": "이 실험은 평가에서 실행되지 않았습니다.",
            "CANCELLED": "평가를 취소했습니다.",
            "PYVISCO_VERSION_MISMATCH": "이 연구에는 pyvisco 2.1.3이 필요합니다.",
            "WLF_PROVENANCE_REQUIRED": "참조 온도와 다른 실측 시험에는 고정 WLF 계수의 출처가 필요합니다. 매개변수 탭에서 확인하세요.",
        }
        if isinstance(reason, str) and reason in code_map:
            return code_map[reason]
        base = code_map.get(exc.code, "요청을 처리하는 중 오류가 발생했습니다.")
        if reason:
            return f"{base} ({reason})"
        return base
    return str(exc)


def _experiment_label(exp: Any) -> str:
    if title := getattr(exp, "title", None):
        return title
    eid = exp.experiment_id
    if generated := re.fullmatch(r"exp-(\d+)-[0-9a-f]{6}", eid):
        return f"실험 {int(generated[1])}"
    uuid_suffix = r"(?:^|[_-])(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$"
    return re.sub(uuid_suffix, "", eid, flags=re.IGNORECASE) or "실험"


def _unit_label(label: str, unit: str) -> str:
    return f"{label} ({unit})" if unit else label


def _number_text(value: float | None) -> str:
    return str(value) if value is not None else ""


def _tinted_icon(icon: QIcon, size: QSize, color: str) -> QIcon:
    pixmap = icon.pixmap(size)
    if pixmap.isNull():
        return icon
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor(color))
    painter.end()
    return QIcon(pixmap)


def _command_icon(symbol: str, color: str) -> QIcon:
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    if symbol == "plus":
        painter.drawLine(3, 8, 13, 8)
        painter.drawLine(8, 3, 8, 13)
    else:
        painter.drawLine(4, 4, 12, 12)
        painter.drawLine(4, 12, 12, 4)
    painter.end()
    return QIcon(pixmap)


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
        count = f"{len(rows) + 1}행 이상" if total_rows > len(rows) else f"총 {total_rows}행"
        summary = QLabel(f"{csv_path.name} · {count} 중 상위 {len(rows)}행 미리보기")
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
        self.weight_policy_combo = _configure_combo(QComboBox())
        self.weight_policy_combo.addItem("자동", "")
        for policy, label in _WEIGHTS_KO.items():
            self.weight_policy_combo.addItem(label, policy)
        grid.addWidget(QLabel("표본 적분 정책"), 5, 2)
        grid.addWidget(self.weight_policy_combo, 5, 3)
        self.acceptance_limit_edit = QLineEdit()
        self.acceptance_limit_edit.setPlaceholderText("지정하지 않음")
        grid.addWidget(QLabel("허용 오차 한계"), 6, 0)
        grid.addWidget(self.acceptance_limit_edit, 6, 1)

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

        limit_text = self.acceptance_limit_edit.text().strip()
        try:
            limit = float(limit_text) if limit_text else None
            if limit is not None and (not math.isfinite(limit) or limit < 0):
                raise ValueError("invalid acceptance limit")
        except ValueError as exc:
            raise MaterialStudyError(
                "INVALID_CONFIGURATION", "import_csv",
                {"reason": "허용 오차 한계는 0 이상의 유한한 수이거나 빈칸이어야 합니다."},
            ) from exc
        sample_policy = self.weight_policy_combo.currentData() or (
            "trapezoidal" if ind_qkind == "time" else "log10_trapezoidal"
        )

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
            "sample_weights_policy": sample_policy,
            "acceptance_limit": limit,
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
        self._draft_dirty = False
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

        self._selected_eval_id = self._saved_selection()
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
        self._action_icons: list[tuple[QAction, str]] = []
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
                self._action_icons.append((act, icon_name))
            act.triggered.connect(slot)
            if shortcut:
                act.setShortcut(shortcut)
            menu.addAction(act)
            if toolbar:
                self.toolbar.addAction(act)
            return act

        self.act_new = _add(file_menu, "새 연구", self.on_new_study, QKeySequence.StandardKey.New, "plus", True)
        self.act_open = _add(file_menu, "열기…", self.on_open_study, QKeySequence.StandardKey.Open, "folder", True)
        self.act_save = _add(file_menu, "저장", self.on_save_study, QKeySequence.StandardKey.Save, "save", True)
        self.act_save_as = _add(file_menu, "다른 이름으로 저장…", self.on_save_as_study, "Ctrl+Shift+S", "save")
        file_menu.addSeparator()
        self.act_close = _add(file_menu, "닫기", self.close, QKeySequence.StandardKey.Close)

        self.act_import = _add(study_menu, "실험 CSV 불러오기…", self.on_import_csv, None, "material", True)
        self.act_evaluate = _add(study_menu, "단일 평가", self.on_evaluate, "F5", "check", True)
        self.act_optimize = _add(study_menu, "보정 실행", self.on_optimize, "F6", "run", True)
        self.act_stop = _add(study_menu, "최선값 유지 중지(Stop)", self.on_stop, None, "step", True)
        self.act_cancel = _add(study_menu, "취소(Cancel)", self.on_cancel, None, "close", True)
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
        self.badge_ident = QLabel("식별: 미확인")
        for b in (self.badge_fit, self.badge_val, self.badge_hold, self.badge_const, self.badge_ident):
            b.setWordWrap(True)
        badge_grid.addWidget(self.badge_fit, 0, 0)
        badge_grid.addWidget(self.badge_val, 0, 1)
        badge_grid.addWidget(self.badge_hold, 1, 0)
        badge_grid.addWidget(self.badge_const, 1, 1)
        badge_grid.addWidget(self.badge_ident, 2, 0, 1, 2)
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
        self.study_tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.study_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.study_tree.header().setStretchLastSection(False)
        self.study_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
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
        self.loss_summary_label.setWordWrap(True)
        plot_bar.addWidget(self.loss_summary_label)
        plot_layout.addLayout(plot_bar)

        self.fig = Figure(figsize=(10, 4.2), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.mpl_toolbar = NavigationToolbar2QT(self.canvas, self)
        self.mpl_toolbar.setObjectName("mplToolbar")
        self._mpl_icons = [(action, action.icon()) for action in self.mpl_toolbar.actions() if not action.icon().isNull()]
        self.ax_curve = self.fig.add_subplot(121)
        self.ax_resid = self.fig.add_subplot(122)
        self._plot_channels: dict[Any, tuple[str, str, str, np.ndarray]] = {}
        self.canvas.mpl_connect("button_press_event", self._on_plot_clicked)
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
        self.btn_wlf_provenance = QPushButton("고정 WLF 계수 출처…")
        self.btn_wlf_provenance.clicked.connect(self.on_set_wlf_provenance)
        param_bar.addWidget(self.btn_wlf_provenance)
        self.param_info_label = QLabel()
        param_bar.addWidget(self.param_info_label)
        param_layout.addLayout(param_bar)

        param_split = QSplitter(Qt.Orientation.Horizontal)
        self.param_table = QTableWidget(0, 11)
        self.param_table.setHorizontalHeaderLabels(
            ["매개변수", "초기/초안 값", "단위", "역할", "하한", "상한", "변환", "선택 평가 값", "식/제약", "그룹", "식별 상태"]
        )
        self.param_table.setAlternatingRowColors(True)
        self.param_table.verticalHeader().setVisible(False)
        p_hdr = self.param_table.horizontalHeader()
        p_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, self.param_table.columnCount()):
            p_hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        p_hdr.setMinimumSectionSize(34)
        self.param_table.itemChanged.connect(self._mark_draft_dirty)

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
        param_split.setSizes([840, 300])
        param_layout.addWidget(param_split, 1)
        self.detail_tabs.addTab(param_tab, "매개변수 · 제약")

        comparison_tab = QWidget()
        comparison_layout = QVBoxLayout(comparison_tab)
        comparison_apply = QPushButton("비교 초안 확정")
        comparison_apply.clicked.connect(self.on_apply_parameter_draft)
        comparison_layout.addWidget(comparison_apply, alignment=Qt.AlignmentFlag.AlignLeft)
        self.btn_apply_comparisons = comparison_apply
        self.comparison_table = QTableWidget(0, 8)
        self.comparison_table.setHorizontalHeaderLabels([
            "실험", "채널", "스케일", "가중치", "격자/적분 정책", "학습 포함", "검증 포함", "허용 오차 한계"
        ])
        self.comparison_table.verticalHeader().hide()
        self.comparison_table.setAlternatingRowColors(True)
        comparison_header = self.comparison_table.horizontalHeader()
        comparison_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        comparison_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        comparison_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.comparison_table.itemChanged.connect(self._mark_draft_dirty)
        comparison_layout.addWidget(self.comparison_table)
        self.detail_tabs.addTab(comparison_tab, "비교 설정")

        # 평가 이력 · 잔차 요약
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
        history_header = self.history_table.horizontalHeader()
        history_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        history_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.history_table.verticalHeader().hide()
        self.history_table.itemSelectionChanged.connect(self._on_history_row_selected)

        self.channel_summary_table = QTableWidget(0, 6)
        self.channel_summary_table.setHorizontalHeaderLabels(
            ["실험", "관측량", "분할", "손실", "최대 오차", "판정"]
        )
        self.channel_summary_table.setAlternatingRowColors(True)
        self.channel_summary_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.channel_summary_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.channel_summary_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        channel_header = self.channel_summary_table.horizontalHeader()
        channel_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for col in (0, 1):
            channel_header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.channel_summary_table.verticalHeader().hide()

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
        dark = resolved == "dark"
        toolbar_bg, toolbar_fg = ("#1e293b", "#e2e8f0") if dark else ("#f1f5f9", "#0f172a")
        for action, name in self._action_icons:
            if name in ("plus", "close"):
                action.setIcon(_command_icon(name, toolbar_fg))
            else:
                action.setIcon(load_ui_icon(name, resolved))
        for action, icon in self._mpl_icons:
            action.setIcon(_tinted_icon(icon, self.mpl_toolbar.iconSize(), toolbar_fg))
        extra_qss = (
            f"\nQScrollArea > QWidget > QWidget {{ background: {pal['surface_bg']}; }}"
            f"\nQWidget#propsPanel {{ background: {pal['surface_bg']}; }}"
            f"\nQTableCornerButton::section {{ background: {pal['header_bg']}; border: none; }}"
            f"\nQTableWidget, QTableView, QTreeWidget, QTreeView, QAbstractItemView {{ background-color: {pal['surface_bg']}; alternate-background-color: {pal['card_alt_bg']}; color: {pal['text_primary']}; gridline-color: {pal['border']}; }}"
            f"\nQToolBar#mplToolbar {{ background: {toolbar_bg}; border-radius: 5px; padding: 2px; border: 1px solid {pal['border']}; }}"
            f"\nQToolBar#mplToolbar QLabel {{ background: {toolbar_bg}; color: {toolbar_fg}; }}"
            f"\nQToolBar#mplToolbar QToolButton {{ color: {toolbar_fg}; background: transparent; border-radius: 3px; padding: 2px; }}"
            f"\nQToolBar#mplToolbar QToolButton:hover {{ background: {'#334155' if dark else '#cbd5e1'}; }}"
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
                (self.badge_ident, "식별"),
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
            (self.badge_ident, "식별", "identifiability_status"),
        ):
            val = str(assess.get(key, "not_assessed"))
            label = "식별" if key == "identifiability_status" and val == "pass" else _ko_status(val)
            badge.setText(f"{prefix}: {label}")
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
        self.btn_apply_params.setEnabled(not busy and not ro)
        self.btn_apply_comparisons.setEnabled(not busy and not ro)
        self.btn_wlf_provenance.setEnabled(not busy and not ro and self.service._descriptor().model_id == "B-VIS-TRS-1")
        self.btn_wlf_provenance.setVisible(self.service._descriptor().model_id == "B-VIS-TRS-1")
        self.param_table.setEnabled(not busy)
        self.comparison_table.setEnabled(not busy)

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
        if not self._draft_dirty:
            self._populate_parameter_draft()
            self._populate_comparison_table()
        self._populate_history_table()
        self._load_selected_evaluation()
        self._populate_preview_selector()
        self._sync_actions()

    def _populate_tree(self) -> None:
        self.study_tree.clear()
        desc = self.service._descriptor()
        exps = self.service.experiments()
        params = self.service.parameters()
        comps = self.service.comparisons()
        evals = self.service._manifest.get("evaluations", {})
        camps = self.service._manifest.get("campaigns", {})
        exports = self.service._manifest.get("exports", ())

        def _node(title: str, tooltip: str) -> QTreeWidgetItem:
            item = QTreeWidgetItem([title])
            item.setToolTip(0, tooltip)
            return item

        def _root(title: str, icon_name: str, key: str) -> QTreeWidgetItem:
            item = _node(title, title)
            item.setData(0, Qt.ItemDataRole.UserRole, (key, None))
            item.setIcon(0, load_ui_icon(icon_name, self._theme_mode))
            self.study_tree.addTopLevelItem(item)
            return item

        m_root = _root("모델", "material", "model")
        short_name = desc.name.split(" (", 1)[0]
        m_root.addChild(_node(short_name, desc.name))

        e_root = _root(f"실험 데이터 ({len(exps)})", "folder", "experiments")
        for exp in exps:
            temp = exp.conditions.get("temperature_K")
            t_txt = f" · {temp:.2f} K" if isinstance(temp, (int, float)) else ""
            ch_txt = ", ".join(_ko_observable(ch.observable_id) for ch in exp.channels)
            label = f"{_experiment_label(exp)} · {_ko_split(exp.split)}{t_txt}"
            child = _node(label, f"{label}\n관측량: {ch_txt}")
            child.setData(0, Qt.ItemDataRole.UserRole, ("experiment", exp.experiment_id))
            e_root.addChild(child)

        p_root = _root(f"매개변수 ({len(params)})", "section", "parameters")
        for p in params:
            value = " ".join(s for s in (f"{p.default:g}" if p.default is not None else "—", p.unit) if s)
            label = f"{p.id} = {value} ({_ROLE_KO[p.role]})"
            p_root.addChild(_node(label, f"{p.name}\n{label}"))

        c_root = _root(f"비교 설정 ({len(comps)})", "check", "comparisons")
        exp_labels = {exp.experiment_id: _experiment_label(exp) for exp in exps}
        for c in comps:
            label = exp_labels.get(c.experiment_id, c.experiment_id)
            observable = {"E_storage": "E'", "E_loss": "E''", "E_relax": "E(t)"}.get(c.observable_id, c.observable_id)
            c_root.addChild(_node(
                f"{label} · {observable}",
                f"{label} · {_ko_observable(c.observable_id)} · {_ko_split(c.split)}",
            ))

        camp_root = _root(f"최적화 실행 ({len(camps)})", "run", "campaigns")
        for idx, camp in enumerate(camps.values(), 1):
            label = f"실행 #{idx} · {_ko_status(camp.get('state'))}"
            camp_root.addChild(_node(label, label))

        ev_root = _root(f"평가 이력 ({len(evals)})", "result", "evaluations")
        for idx, ev in enumerate(evals.values(), 1):
            ev_id = ev["evaluation_id"]
            phase = _ko_status(ev.get("execution_phase"))
            label = f"평가 #{idx} · {phase}"
            child = _node(label, label)
            child.setData(0, Qt.ItemDataRole.UserRole, ("evaluation", ev_id))
            ev_root.addChild(child)

        exp_root = _root(f"내보내기 ({len(exports)})", "save", "exports")
        for idx, _ in enumerate(exports, 1):
            exp_root.addChild(_node(f"보고서 #{idx}", f"매개변수·정식화 보고서 #{idx}"))

        rev_root = _root("검토", "expert", "reviews")
        rev_root.addChild(_node("독립 평가 기록", "독립 평가 기록 준비 상태: " + ("가능" if evals else "미확인")))

        for root in (m_root, e_root, p_root):
            root.setExpanded(True)

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
        elif kind == "parameters":
            self.detail_tabs.setCurrentIndex(1)
        elif kind == "comparisons":
            self.detail_tabs.setCurrentIndex(2)
        elif kind == "evaluations" or kind == "evaluation":
            self.detail_tabs.setCurrentIndex(3)
            if val:
                for row in range(self.history_table.rowCount()):
                    if self.history_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == val:
                        self.history_table.selectRow(row)
                        break

    def _populate_parameter_draft(self) -> None:
        params = self.service.parameters()
        self.param_table.blockSignals(True)
        self.param_table.setRowCount(len(params))
        is_prony = self.service._descriptor().model_id == "B-VIS-TRS-1"
        prony_relations = {
            "E0": "E∞ = E0(1−a1−a2)",
            "a1": "a1+a2 ≤ 0.95",
            "a2": "a1+a2 ≤ 0.95",
            "tau1": "τ2/τ1 ≥ 2",
            "tau2": "τ2/τ1 ≥ 2",
        } if is_prony else {}
        prony_labels = {
            "E0": "순간 탄성률 E0",
            "Tref": "기준 온도 Tref",
            "a1": "1차 가중치 a1",
            "a2": "2차 가중치 a2",
            "tau1": "완화 시간 τ1",
            "tau2": "완화 시간 τ2",
        } if is_prony else {}
        for row, spec in enumerate(params):
            group = (
                "온도 이동" if spec.id in ("C1", "C2", "Tref")
                else "Prony" if is_prony else ""
            )
            items = (
                prony_labels.get(spec.id, spec.name), _number_text(spec.default), spec.unit, _ROLE_KO[spec.role],
                _number_text(spec.lower), _number_text(spec.upper), _TRANSFORM_KO[spec.transform],
                "", spec.expression or prony_relations.get(spec.id, ""), group, "",
            )
            for col, text in enumerate(items):
                cell = QTableWidgetItem(text)
                cell.setToolTip(text)
                if col == 0:
                    cell.setToolTip(spec.name)
                    cell.setData(Qt.ItemDataRole.UserRole, spec.id)
                if self.service.read_only or col not in (1, 3, 4, 5, 6, 8):
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                elif col == 8:
                    cell.setToolTip("연계 역할에서 계수 식을 입력하세요. 예: 0.5 * E0")
                self.param_table.setItem(row, col, cell)
        self.param_table.blockSignals(False)
        self.param_info_label.clear()

    def _populate_parameter_result(self) -> None:
        art = self._active_eval_artifact
        active_values = art.physical_parameters if art else {}
        ident = art.identifiability if art else {}
        assessed = set(ident.get("parameter_ids", ()))
        unidentifiable = set(ident.get("unidentifiable_parameters", ()))
        overall = art.assessment.assessment.get("identifiability_status") if art else None
        self.param_table.blockSignals(True)
        for row in range(self.param_table.rowCount()):
            parameter_id = self.param_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            value = active_values.get(parameter_id)
            self.param_table.item(row, 7).setText(f"{value:.6g}" if isinstance(value, (int, float)) else str(value or ""))
            status = (
                "비식별" if parameter_id in unidentifiable
                else "식별" if parameter_id in assessed and overall == "pass"
                else "미확인"
            )
            self.param_table.item(row, 10).setText(status)
        self.param_table.blockSignals(False)

        if art is not None:
            checks = art.assessment.assessment.get("constraint_checks", ())
        else:
            checks = [
                {"constraint_id": c.id, "limit": c.limit, "value": None, "status": "NOT_ASSESSED"}
                for c in (*self.service.mandatory_constraints(), *self.service.user_constraints())
            ]
        constraint_names = {
            "prony_fraction_sum_max": ("분율 합 a1+a2", "Prony 분율 합 (a1+a2)"),
            "prony_tau_order_ratio": ("시간 상수비 τ2/τ1", "완화 시간 상수 순서비 (τ2/τ1)"),
            "prony_e_inf_positive": ("평형 탄성률 E∞", "평형 탄성계수 E∞"),
        }
        self.constraint_table.setRowCount(len(checks))
        for row, check in enumerate(checks):
            constraint_id = str(check.get("constraint_id", ""))
            fallback = constraint_id.replace("_", " ")
            short_name, full_name = constraint_names.get(constraint_id, (fallback, fallback))
            vals = (
                short_name,
                _number_text(check.get("limit")),
                f"{check['value']:.6g}" if isinstance(check.get("value"), (int, float)) else "미확인",
                _ko_status(check.get("status")),
            )
            for col, value in enumerate(vals):
                cell = QTableWidgetItem(value)
                cell.setToolTip(full_name if col == 0 else value)
                self.constraint_table.setItem(row, col, cell)

    def _populate_comparison_table(self) -> None:
        comparisons = self.service.comparisons()
        labels = {exp.experiment_id: _experiment_label(exp) for exp in self.service.experiments()}
        self.comparison_table.blockSignals(True)
        self.comparison_table.setRowCount(len(comparisons))
        for row, spec in enumerate(comparisons):
            values = (
                labels.get(spec.experiment_id, spec.experiment_id),
                _ko_observable(spec.observable_id), f"{spec.scale:g}", f"{spec.weight:g}",
                _WEIGHTS_KO.get(spec.sample_weights_policy, spec.sample_weights_policy),
                "예" if spec.required_for_fit else "아니요",
                "예" if spec.required_for_validation else "아니요",
                _number_text(spec.acceptance_limit),
            )
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                if self.service.read_only or col < 2:
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                elif col == 4:
                    cell.setToolTip("균등, 사다리꼴, 로그 사다리꼴")
                elif col in (5, 6):
                    cell.setToolTip("예 또는 아니요")
                self.comparison_table.setItem(row, col, cell)
        self.comparison_table.blockSignals(False)

    def _mark_draft_dirty(self, item: QTableWidgetItem) -> None:
        if item.flags() & Qt.ItemFlag.ItemIsEditable:
            self._draft_dirty = True
            self.param_info_label.setText("저장하지 않은 초안")

    def _collect_draft_parameter_specs(self) -> list[ParameterSpec]:
        role_keys = {label: key for key, label in _ROLE_KO.items()} | {"연동": "linked"}
        transform_keys = {label: key for key, label in _TRANSFORM_KO.items()} | {"log10": "log"}
        updated: list[ParameterSpec] = []
        for row, spec in enumerate(self.service.parameters()):
            text = [self.param_table.item(row, col).text().strip() for col in range(7)]
            role = role_keys.get(text[3], text[3])
            transform = transform_keys.get(text[6], text[6])
            try:
                val, lo, hi = (float(text[col]) if text[col] else None for col in (1, 4, 5))
                if val is not None and ((lo is not None and val < lo) or (hi is not None and val > hi)):
                    raise ValueError("초기/초안 값은 하한과 상한 사이여야 합니다.")
                if transform == "log" and any(x is not None and x <= 0 for x in (val, lo, hi)):
                    raise ValueError("로그 변환의 값과 경계는 0보다 커야 합니다.")
                expression = None
                dependencies: tuple[str, ...] = ()
                if role == "linked":
                    expression = self.param_table.item(row, 8).text().strip()
                    tree = ast.parse(expression, mode="eval")
                    functions = {n.func for n in ast.walk(tree) if isinstance(n, ast.Call)}
                    dependencies = tuple(dict.fromkeys(
                        n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n not in functions
                    ))
                updated.append(replace_spec(
                    spec, default=val, lower=lo, upper=hi, role=role, transform=transform,
                    expression=expression, dependencies=dependencies,
                ))
            except (ValueError, SyntaxError) as exc:
                reason = str(exc) if str(exc).endswith("합니다.") else "값, 범위, 역할 및 연계 식을 확인하세요."
                raise MaterialStudyError("INVALID_PARAMETER", "parameter_table", {"reason": f"{spec.name}. {reason}"}) from exc
        return updated

    def _collect_draft_comparisons(self) -> list[ComparisonSpec]:
        policies = {label: key for key, label in _WEIGHTS_KO.items()}
        flags = {"예": True, "아니요": False, "true": True, "false": False}
        updated = []
        for row, spec in enumerate(self.service.comparisons()):
            scale, weight, policy, fit, validation, limit = (
                self.comparison_table.item(row, col).text().strip() for col in range(2, 8)
            )
            policy = policies.get(policy, policy)
            try:
                if policy not in _WEIGHTS_KO:
                    raise ValueError("unrecognized sample weights policy")
                updated.append(replace_spec(
                    spec, scale=float(scale), weight=float(weight),
                    required_for_fit=flags[fit.lower()], required_for_validation=flags[validation.lower()],
                    sample_weights_policy=policy, acceptance_limit=float(limit) if limit else None,
                ))
            except (KeyError, ValueError) as exc:
                raise MaterialStudyError(
                    "INVALID_CONFIGURATION", "comparison_table",
                    {"reason": "스케일, 가중치, 분할별 포함 여부, 적분 정책 및 허용 오차 한계를 확인하세요."},
                ) from exc
        return updated

    def _populate_preview_selector(self) -> None:
        current = self.preview_exp_combo.currentData()
        self.preview_exp_combo.blockSignals(True)
        self.preview_exp_combo.clear()
        for exp in self.service.experiments():
            temp = exp.conditions.get("temperature_K")
            t_txt = f" ({temp:.2f} K)" if isinstance(temp, (int, float)) else ""
            self.preview_exp_combo.addItem(f"{_experiment_label(exp)} · {_ko_split(exp.split)}{t_txt}", exp.experiment_id)
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
        channels = exp.channels
        if exp.split == "holdout":
            previous = self._active_eval_artifact
            verified = self._verify_holdout_display()
            allowed = self._holdout_channels(verified, exp_id)
            channels = tuple(ch for ch in exp.channels if ch.observable_id in allowed)
            if not channels:
                self.exp_preview_table.setRowCount(0)
                self.exp_preview_table.setColumnCount(0)
                if previous is not None and verified is None:
                    self._redraw_plots()
                else:
                    self.preview_meta_label.setText("보류 시험 최종 확인 후 표시됩니다.")
                return
        exp_data = self.service._manifest.get("experiment_data", {}).get(exp_id, {})
        if not channels or not exp_data:
            return
        ch0 = channels[0]
        try:
            x_arr, y0_arr, sk_arr, _ = load_experiment_channel_arrays(exp_data[ch0.observable_id], data_root=self.service.data_root)
            y_arrays = [y0_arr]
            for extra_ch in channels[1:]:
                _, values, _, _ = load_experiment_channel_arrays(exp_data[extra_ch.observable_id], data_root=self.service.data_root)
                y_arrays.append(values)
        except (MaterialStudyError, OSError, ValueError) as exc:
            self.exp_preview_table.setRowCount(0)
            self.preview_meta_label.setText(_human_error(exc))
            return
        max_n = min(100, x_arr.size)
        ind_unit = exp_data[ch0.observable_id]["independent_unit"]
        cols = ["원본 행", _unit_label(ch0.independent_column, ind_unit)]
        cols.extend(_unit_label(_ko_observable(ch.observable_id), ch.observable.unit) for ch in channels)
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

    def _saved_selection(self) -> str | None:
        manifest = self.service._manifest
        selected = manifest.get("view_state", {}).get("selected_eval_id")
        if selected in manifest.get("evaluations", {}):
            return selected
        return manifest.get("selected_evaluation_id")

    @staticmethod
    def _holdout_channels(art: CalibrationEvaluationArtifact | None, experiment_id: str) -> set[str]:
        if art is None or art.execution_phase != "confirmation" or art.prediction_bundle is None:
            return set()
        return {
            channel.observable_id
            for exp in art.prediction_bundle.experiments if exp.experiment_id == experiment_id
            for channel in exp.channels
        }

    def _verify_holdout_display(self) -> CalibrationEvaluationArtifact | None:
        art = self._active_eval_artifact
        if art is None or art.execution_phase != "confirmation":
            return art
        try:
            return self.service.evaluation(art.evaluation_id)
        except (MaterialStudyError, OSError, TypeError, ValueError, KeyError) as exc:
            problem = _human_error(exc)
            self._active_eval_artifact = None
            self.loss_summary_label.setText(problem)
            self.channel_summary_table.setRowCount(0)
            preview_id = self.preview_exp_combo.currentData()
            if any(exp.experiment_id == preview_id and exp.split == "holdout" for exp in self.service.experiments()):
                self.exp_preview_table.setRowCount(0)
                self.exp_preview_table.setColumnCount(0)
                self.preview_meta_label.setText(problem)
            self._populate_parameter_result()
            self._update_status_badges()
            self._populate_history_table()
            self._refresh_plot_selector()
            return None

    def _populate_history_table(self) -> None:
        evals = tuple(self.service._manifest.get("evaluations", {}).values())
        self.history_table.blockSignals(True)
        self.history_table.setRowCount(len(evals))
        if self._selected_eval_id is None and evals:
            self._selected_eval_id = self._saved_selection() or evals[-1]["evaluation_id"]

        for row, ev in enumerate(evals):
            eid = ev["evaluation_id"]
            loss = None
            fit = val = hold = constraint = ident = "미확인"
            state = _ko_status(ev.get("state"))
            problem = None
            confirmation = ev.get("execution_phase") == "confirmation"
            try:
                art = self.service.evaluation(eid)
                assess = art.assessment.assessment
                loss = art.assessment.objective.get("fit_loss")
                state = _ko_status(art.state)
                if confirmation:
                    hold = _ko_status(assess.get("holdout_status"))
                fit = _ko_status(assess.get("fit_status"))
                val = _ko_status(assess.get("validation_status"))
                constraint = _ko_status(assess.get("constraint_status"))
                ident = _ko_status(assess.get("identifiability_status"))
                if assess.get("identifiability_status") == "pass":
                    ident = "식별"
            except (MaterialStudyError, OSError, TypeError, ValueError, KeyError) as exc:
                problem = _human_error(exc)
                state = "기록 손상"
                if confirmation:
                    hold = state

            values = (
                f"#{row + 1}", _ko_status(ev.get("execution_phase")),
                state, f"{loss:.3e}" if isinstance(loss, (int, float)) else "미확인",
                fit, val, hold, f"{constraint} · {ident}",
            )
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(problem or value)
                if col == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, eid)
                self.history_table.setItem(row, col, cell)

            if eid == self._selected_eval_id:
                self.history_table.selectRow(row)
        self.history_table.blockSignals(False)

    def _on_history_row_selected(self) -> None:
        rows = self.history_table.selectionModel().selectedRows()
        if rows:
            item = self.history_table.item(rows[0].row(), 0)
            if item is not None:
                self._selected_eval_id = item.data(Qt.ItemDataRole.UserRole)
                self._load_selected_evaluation()
                self._populate_experiment_preview()

    def _load_selected_evaluation(self) -> None:
        eid = self._selected_eval_id
        self._active_eval_artifact = None
        self.channel_summary_table.setRowCount(0)
        if not eid:
            self.loss_summary_label.setText("적합 손실: 미확인")
        else:
            try:
                art = self.service.evaluation(eid)
            except (MaterialStudyError, OSError, TypeError, ValueError, KeyError) as exc:
                self.loss_summary_label.setText(_human_error(exc))
            else:
                self._active_eval_artifact = art
                self._populate_evaluation_summary(art)

        self._populate_parameter_result()
        self._update_status_badges()
        self._refresh_plot_selector()
        self._redraw_plots()

    def _populate_evaluation_summary(self, art: CalibrationEvaluationArtifact) -> None:
        assessment = art.assessment.assessment
        checks = [
            check for check in assessment.get("channel_checks", ())
            if art.execution_phase == "confirmation" or check.get("split") != "holdout"
        ]
        objective = art.assessment.objective
        fit_loss = objective.get("fit_loss")
        label = f"적합 손실: {fit_loss:.3e}" if isinstance(fit_loss, (int, float)) else "적합 손실: 무효"
        if art.state != "completed" or not objective.get("eligible"):
            reason_codes = objective.get("reason_codes", ())
            primary_reason = reason_codes[0] if reason_codes else (
                "CANCELLED" if art.state == "cancelled" else "INTEGRATION_FAILED"
            )
            label = _human_error(MaterialStudyError(primary_reason, "evaluation"))
            entries = self.service._manifest.get("experiment_data", {})
            expected = valid = 0
            prediction_coverage = {
                (experiment.experiment_id, channel.observable_id): channel.coverage
                for experiment in art.prediction_bundle.experiments
                for channel in experiment.channels
            } if art.prediction_bundle else {}
            for check in checks:
                exp_id, obs_id = check.get("experiment_id"), check.get("observable_id")
                sample_count = int(entries.get(exp_id, {}).get(obs_id, {}).get("row_count", 0))
                coverage = check.get("coverage") or prediction_coverage.get((exp_id, obs_id), {})
                expected += int(coverage.get("expected", sample_count))
                valid += int(coverage.get("valid", sample_count if check.get("loss") is not None else 0))
            label += f" · 표본 {expected}개 중 유효 {valid}개 · 누락 {expected - valid}개"
        self.loss_summary_label.setText(label)

        labels = {exp.experiment_id: _experiment_label(exp) for exp in self.service.experiments()}
        self.channel_summary_table.setRowCount(len(checks))
        for row, check in enumerate(checks):
            loss = check.get("loss")
            max_err = check.get("max_abs_error")
            exp_id = str(check.get("experiment_id", ""))
            values = (
                labels.get(exp_id, exp_id), _ko_observable(str(check.get("observable_id", ""))),
                _ko_split(check.get("split")), f"{loss:.3e}" if isinstance(loss, (int, float)) else "미확인",
                f"{max_err:.4g}" if isinstance(max_err, (int, float)) else "미확인", _ko_status(check.get("status")),
            )
            reasons = "\n".join(
                _human_error(MaterialStudyError(code, "residuals")) for code in check.get("reason_codes", ())
            )
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(reasons if col == 5 and reasons else value)
                if col == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, (exp_id, check.get("observable_id"), check.get("comparison_id")))
                self.channel_summary_table.setItem(row, col, cell)

    def _refresh_plot_selector(self) -> None:
        current = self.plot_selector.currentData()
        self.plot_selector.blockSignals(True)
        self.plot_selector.clear()
        experiments = [
            exp for exp in self.service.experiments()
            if exp.split != "holdout" or self._holdout_channels(self._active_eval_artifact, exp.experiment_id)
        ]
        if any(ch.observable_id in _DMA_OBSERVABLES for exp in experiments for ch in exp.channels):
            self.plot_selector.addItem("전체 DMA (저장·손실 곡선 / 잔차)", ("family", "dma"))
        if any(ch.observable_id == "E_relax" for exp in experiments for ch in exp.channels):
            self.plot_selector.addItem("전체 응력 완화 곡선 / 잔차", ("family", "relax"))
        for exp in experiments:
            temp = exp.conditions.get("temperature_K")
            t_txt = f" · {temp:.2f} K" if isinstance(temp, (int, float)) else ""
            self.plot_selector.addItem(
                f"{_experiment_label(exp)} · {_ko_split(exp.split)}{t_txt}", ("exp", exp.experiment_id)
            )
        for idx in range(self.plot_selector.count()):
            if self.plot_selector.itemData(idx) == current:
                self.plot_selector.setCurrentIndex(idx)
                break
        self.plot_selector.blockSignals(False)

    def _redraw_plots(self) -> None:
        if self._closed:
            return
        p = get_palette(self._theme_mode)
        bg, panel_bg, fg = (str(p[key]) for key in ("canvas_bg", "panel_bg", "text_primary"))
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

        self._plot_channels.clear()
        art = self._verify_holdout_display()
        experiment_data = self.service._manifest.get("experiment_data", {})
        data_root = self.service.data_root
        selected = self.plot_selector.currentData()
        records = {
            (entry["experiment_id"], entry["observable_id"]): entry
            for entry in art.channel_results
        } if art else {}
        dark = self._theme_mode == "dark"
        palettes = {
            "train": ("#38bdf8", "#2dd4bf", "#a78bfa", "#93c5fd") if dark else ("#0369a1", "#0f766e", "#6d28d9", "#1d4ed8"),
            "validation": ("#fbbf24", "#fdba74", "#fde047") if dark else ("#a16207", "#c2410c", "#854d0e"),
            "holdout": ("#f472b6", "#fda4af", "#e879f9") if dark else ("#be185d", "#be123c", "#a21caf"),
        }
        split_counts = dict.fromkeys(palettes, 0)
        axis_x: QuantityUnitSpec | None = None
        axis_y_unit: str | None = None
        axis_observables: set[str] = set()
        use_log_x = True
        plotted_residual = False

        for exp in self.service.experiments():
            eid, split = exp.experiment_id, exp.split
            if selected is None or (selected[0] == "exp" and selected[1] != eid):
                continue
            allowed = self._holdout_channels(art, eid) if split == "holdout" else None
            if allowed is not None and not allowed:
                continue
            palette = palettes[split]
            idx = split_counts[split]
            split_counts[split] += 1
            color = palette[idx % len(palette)]
            temp = exp.conditions.get("temperature_K")
            temp_label = f"{temp:.2f} K" if isinstance(temp, (int, float)) else _experiment_label(exp)
            for ch_idx, ch in enumerate(exp.channels):
                obs_id = ch.observable_id
                if allowed is not None and obs_id not in allowed:
                    continue
                if selected == ("family", "dma") and obs_id not in _DMA_OBSERVABLES:
                    continue
                if selected == ("family", "relax") and obs_id != "E_relax":
                    continue
                observed_entry = experiment_data.get(eid, {}).get(obs_id)
                saved = records.get((eid, obs_id))
                if not observed_entry or (art is not None and saved is None):
                    continue
                try:
                    if saved is None:
                        x, obs_y, _, _ = load_experiment_channel_arrays(observed_entry, data_root=data_root)
                    else:
                        x, obs_y, pred_y, residual = (
                            read_safe_npy_v2(ArtifactRefV1.from_dict(saved[key]).path, root=data_root)
                            for key in ("observed_coords_ref", "observed_values_ref", "predicted_on_obs_ref", "residuals_ref")
                        )
                    current_x = QuantityUnitSpec(observed_entry["independent_unit"], observed_entry["independent_quantity_kind"])
                    target_x = axis_x or current_x
                    if current_x.quantity_kind == target_x.quantity_kind:
                        x = np.asarray(convert_values(x, current_x, target_x))
                    else:
                        x = np.asarray(convert_frequency_angular(x, current_x, target_x))
                    current_y = QuantityUnitSpec(ch.observable.unit, ch.observable.quantity_kind)
                    target_y = QuantityUnitSpec(axis_y_unit if axis_y_unit is not None else current_y.unit, current_y.quantity_kind)
                    obs_y = np.asarray(convert_values(obs_y, current_y, target_y))
                    if saved is not None:
                        pred_y = np.asarray(convert_values(pred_y, current_y, target_y))
                except (MaterialStudyError, OSError, ValueError, KeyError) as exc:
                    self.status_bar.showMessage(f"그래프 자료를 표시할 수 없습니다. {_human_error(exc)}")
                    continue

                axis_x, axis_y_unit = target_x, target_y.unit
                axis_observables.add(obs_id)
                use_log_x &= bool(np.all(x > 0.0))
                short_obs = {"E_storage": "E'", "E_loss": "E''", "E_star": "E*", "E_relax": "E(t)"}.get(obs_id, _ko_observable(obs_id))
                caption = f"{_ko_split(split)} {temp_label} {short_obs}"
                marker = ("o", "s", "^", "D")[(idx + ch_idx) % 4]
                linestyle = ("-", "--", "-.")[(idx + ch_idx) % 3]
                observed, = self.ax_curve.plot(x, obs_y[:, 0], marker, markersize=3.0, alpha=0.8, color=color,
                                                label=caption if saved is None else "_nolegend_")
                if saved is not None:
                    predicted, = self.ax_curve.plot(x, pred_y[:, 0], linestyle, linewidth=1.55, color=color, label=caption)
                    resid, = self.ax_resid.plot(x, residual[:, 0], marker + linestyle, markersize=2.8, linewidth=1.15,
                                               color=color, label=caption)
                    for line in (observed, predicted, resid):
                        self._plot_channels[line] = (eid, obs_id, saved["comparison_id"], residual[:, 0])
                    plotted_residual = True

        if axis_x is not None:
            x_name = {"frequency": "주파수", "angular_frequency": "각주파수", "time": "시간", "strain": "변형률"}.get(
                axis_x.quantity_kind, "좌표"
            )
            curve_name = (
                "탄성계수" if axis_observables <= {*_DMA_OBSERVABLES, "E_relax"}
                else _ko_observable(next(iter(axis_observables))) if len(axis_observables) == 1 else "관측량"
            )
            self.ax_curve.set_title("재료 응답 · 점: 관측 / 선: 예측" if plotted_residual else "실험 관측값", fontsize=11)
            self.ax_curve.set_ylabel(_unit_label(curve_name, axis_y_unit or ""))
            for ax in (self.ax_curve, self.ax_resid):
                ax.set_xlabel(_unit_label(x_name, axis_x.unit))
                if use_log_x:
                    ax.set_xscale("log")
                    ax.xaxis.set_major_formatter(FuncFormatter(_format_log_tick))
            if plotted_residual:
                self.ax_resid.axhline(0.0, color=fg, linewidth=0.9, linestyle=":")
                self.ax_resid.set_ylabel("정규화 잔차 (예측 − 관측) / 척도")
                self.ax_resid.set_title("분할별 정규화 잔차", fontsize=11)
            for ax in (self.ax_curve, self.ax_resid) if plotted_residual else (self.ax_curve,):
                legend = ax.legend(
                    fontsize=8, ncols=2, loc="upper center", bbox_to_anchor=(0.5, -0.19),
                    facecolor=panel_bg, edgecolor=grid_color, labelcolor=fg, framealpha=1,
                    borderpad=0.35, handlelength=2, columnspacing=1.1, labelspacing=0.3,
                )
                legend.set_draggable(False)
        else:
            self.ax_curve.text(0.5, 0.5, "표시할 실험 곡선이 없습니다.", ha="center", va="center", color=fg, transform=self.ax_curve.transAxes)
        if not plotted_residual:
            self.ax_resid.text(0.5, 0.5, "표시할 잔차가 없습니다.", ha="center", va="center", color=fg, transform=self.ax_resid.transAxes)

        self.fig.tight_layout(pad=0.9, w_pad=1)
        self.canvas.draw_idle()

    def _on_plot_clicked(self, event: Any) -> None:
        if event.button != 1 or event.inaxes not in (self.ax_curve, self.ax_resid) or self.mpl_toolbar.mode:
            return
        hit = None
        best_distance = math.inf
        for line in event.inaxes.lines:
            if line not in self._plot_channels:
                continue
            contains, info = line.contains(event)
            if not contains:
                continue
            indices = info["ind"]
            points = line.get_transform().transform(line.get_xydata()[indices])
            distances = np.sum((points - [event.x, event.y]) ** 2, axis=1)
            nearest = int(np.argmin(distances))
            if distances[nearest] < best_distance:
                best_distance = distances[nearest]
                hit = (line, int(indices[nearest]))
        if hit is None:
            return

        line, index = hit
        eid, obs_id, comparison_id, residuals = self._plot_channels[line]
        is_holdout = any(exp.experiment_id == eid and exp.split == "holdout" for exp in self.service.experiments())
        if is_holdout and obs_id not in self._holdout_channels(self._verify_holdout_display(), eid):
            self._redraw_plots()
            return
        table = self.channel_summary_table
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item.data(Qt.ItemDataRole.UserRole) == (eid, obs_id, comparison_id):
                self.detail_tabs.setCurrentIndex(3)
                table.selectRow(row)
                table.scrollToItem(item)
                x = line.get_xdata()[index]
                self.loss_summary_label.setText(
                    f"{item.text()} · {table.item(row, 1).text()} · {line.axes.get_xlabel()} {x:.6g}"
                    f" · 정규화 잔차 {residuals[index]:.3e} · 채널 손실 {table.item(row, 3).text()}"
                )
                break

    def _start_worker(self, op_name: str, fn: Any) -> None:
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
            operation_id = ev.get("campaign_id") or ev.get("operation_id")
            if operation_id and ev.get("stage") == "started" and self._busy_op in (
                "단일 평가", "보정 실행", "보류 시험 최종 확인"
            ):
                self._active_campaign_id = str(operation_id)
                if self._close_after:
                    self.service.cancel(self._active_campaign_id)
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
            if self._close_after:
                self._close_after = False
                if not (op_name == "다른 이름으로 저장" and ok and val[2] is not None):
                    self.close()
                    return
            if not ok:
                msg = _human_error(val) if isinstance(val, Exception) else str(val)
                self.status_bar.showMessage(f"{op_name} 실패: {msg}")
                if not self._close_after:
                    QMessageBox.warning(self, op_name, msg)
            else:
                if op_name in ("단일 평가", "보류 시험 최종 확인"):
                    observation = val.observation or {}
                    if val.state == "completed" and observation.get("valid"):
                        self._selected_eval_id = val.evaluation_id
                        self.status_bar.showMessage(f"{op_name} 완료")
                    else:
                        status = _ko_status("failed" if val.state == "completed" else val.state)
                        reason = observation.get("reason")
                        details = f" · {_human_error(MaterialStudyError(reason, 'evaluation'))}" if reason else ""
                        self.status_bar.showMessage(f"{op_name} {status}{details}")
                elif op_name == "보정 실행":
                    self._last_campaign_receipt = val
                    if val.state == "stopped":
                        self._selected_eval_id = self.service._manifest.get("selected_evaluation_id") or self._selected_eval_id
                    elif val.state == "completed" and val.selected_ids:
                        self._selected_eval_id = val.selected_ids[0]
                    self.status_bar.showMessage(
                        f"보정 종료 ({_ko_status(val.state)}): 총 {len(val.evaluation_ids)}회 평가"
                    )
                elif op_name == "다른 이름으로 저장":
                    receipt, draft_saved, save_error = val
                    if draft_saved:
                        self._draft_dirty = False
                    if save_error is None:
                        self.status_bar.showMessage(f"연구 복사본을 저장했습니다: {Path(receipt.destination_path).name}")
                    else:
                        pending = "초안과 선택 평가" if self._draft_dirty else "선택 평가"
                        message = f"연구 복사본은 저장했습니다. {pending}를 복사본에 저장하지 못했습니다. {_human_error(save_error)}"
                        self.status_bar.showMessage(message)
                        QMessageBox.warning(self, "연구 복사본 설정 저장 실패", message)
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

    def _apply_draft_to_service_sync(self, *, numerical_settings: Mapping[str, Any] | None = None) -> None:
        if self.service.read_only or (not self._draft_dirty and numerical_settings is None):
            return
        self.service.configure(
            parameters=self._collect_draft_parameter_specs(),
            comparisons=self._collect_draft_comparisons(),
            assessment_policy=self.service.assessment_policy(),
            user_constraints=self.service.user_constraints(),
            numerical_settings=numerical_settings,
            expected_revision=self.service.snapshot().document_revision,
        )
        self._draft_dirty = False
        self.param_info_label.clear()

    def on_apply_parameter_draft(self) -> None:
        try:
            if self._draft_dirty:
                self._apply_draft_to_service_sync()
                self.status_bar.showMessage("연구 설정을 반영했습니다.", 3000)
                self._refresh_all()
        except Exception as exc:
            QMessageBox.warning(self, "연구 설정을 반영할 수 없습니다", _human_error(exc))

    def on_set_wlf_provenance(self) -> None:
        try:
            params = self._collect_draft_parameter_specs()
            fixed = {p.id: p.default for p in params if p.id in ("C1", "C2", "Tref") and p.role == "fixed"}
            settings = dict(self.service._manifest.get("numerical_settings", {}))
            old = settings.get("wlf_provenance")
            if not fixed or any(value is None for value in fixed.values()):
                raise MaterialStudyError("INVALID_PARAMETER", "wlf_provenance", {"reason": "먼저 고정 WLF 계수의 값을 입력하세요."})
            label = ", ".join(
                f"{p.id} = {_unit_label(f'{p.default:g}', p.unit)}" for p in params if p.id in fixed
            )
            source, accepted = QInputDialog.getText(
                self, "고정 WLF 계수 출처", f"다음 고정값의 시험 보고서, 문헌 또는 별도 측정 출처를 입력하세요.\n{label}",
                text=str(old.get("source", "")) if isinstance(old, Mapping) else "",
            )
            if accepted and source.strip():
                settings["wlf_provenance"] = {"source": source.strip(), **fixed}
                self._apply_draft_to_service_sync(numerical_settings=settings)
                self.status_bar.showMessage("고정 WLF 계수의 값과 출처를 저장했습니다.", 4000)
                self._refresh_all()
        except Exception as exc:
            QMessageBox.warning(self, "WLF 출처 저장 실패", _human_error(exc))

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
            self._draft_dirty = False
            self._selected_eval_id = None
            self._active_eval_artifact = None
            self._refresh_all()
            self.status_bar.showMessage(f"새 연구를 만들었습니다: {Path(dest).name}")
        except Exception as exc:
            QMessageBox.warning(self, "새 연구 생성 실패", _human_error(exc))

    def on_open_study(self) -> None:
        if self._busy_op is not None:
            return
        src, _ = QFileDialog.getOpenFileName(self, "재료 연구 열기", "", "재료 연구 (*.caemat.json calibration.json)")
        if not src:
            return
        try:
            new_svc = CalibrationStudyService.open(src, read_only=False)
            self.service.close()
            self.service = new_svc
            self._generation += 1
            self._draft_dirty = False
            self._selected_eval_id = self._saved_selection()
            self._refresh_all()
            self.status_bar.showMessage(f"연구를 열었습니다: {Path(src).name}")
        except Exception as exc:
            QMessageBox.warning(self, "연구 열기 실패", _human_error(exc))

    def on_save_study(self) -> None:
        if self._busy_op is not None or self.service.read_only:
            return
        try:
            if self._draft_dirty:
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
        draft = None
        if self._draft_dirty:
            try:
                parameters = self._collect_draft_parameter_specs()
                resolve_parameter_values(parameters)
                build_variable_space(parameters)
                draft = {
                    "parameters": parameters,
                    "comparisons": self._collect_draft_comparisons(),
                    "assessment_policy": svc.assessment_policy(),
                    "user_constraints": svc.user_constraints(),
                }
            except Exception as exc:
                QMessageBox.warning(self, "연구 초안 확인 실패", _human_error(exc))
                return
        expected_rev = svc.snapshot().document_revision
        view_state = {"selected_eval_id": self._selected_eval_id}

        def copy_study(on_progress: Any) -> tuple[Any, bool, Exception | None]:
            receipt = svc.save_as(dest, expected_revision=expected_rev, on_progress=on_progress)
            draft_saved = False
            try:
                if draft is not None:
                    svc.configure(**draft, expected_revision=receipt.document_revision)
                    draft_saved = True
                svc.save(view_state=view_state)
            except Exception as exc:
                return receipt, draft_saved, exc
            return receipt, draft_saved, None

        self._start_worker("다른 이름으로 저장", copy_study)

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
            if self._draft_dirty:
                self._apply_draft_to_service_sync()
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
                        scale=kw["scale"],
                        weight=ch_w,
                        required_for_fit=(kw["split"] == "train"),
                        required_for_validation=(kw["split"] in ("validation", "holdout")),
                        acceptance_limit=kw["acceptance_limit"],
                        sample_weights_policy=kw["sample_weights_policy"],
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
        )

    def on_stop(self) -> None:
        snap = self.service.snapshot()
        cid = self._active_campaign_id or snap.active_campaign_id or snap.active_operation_id
        if cid and self.service.stop(cid).accepted:
            self.status_bar.showMessage("최선값 유지 중지를 요청했습니다…")

    def on_cancel(self) -> None:
        snap = self.service.snapshot()
        cid = self._active_campaign_id or snap.active_campaign_id or snap.active_operation_id
        if cid and self.service.cancel(cid).accepted:
            self.status_bar.showMessage("작업 취소를 요청했습니다…")

    def on_confirm_holdout(self) -> None:
        if self._busy_op is not None or self.service.read_only:
            return
        try:
            if self._draft_dirty:
                self._apply_draft_to_service_sync()
        except Exception as exc:
            QMessageBox.warning(self, "매개변수 확인 실패", _human_error(exc))
            return
        svc = self.service
        snap = svc.snapshot()
        art = self._active_eval_artifact
        if art is not None and (art.content_revision != snap.content_revision or art.state != "completed"):
            QMessageBox.warning(self, "보류 시험 최종 확인", "현재 연구 설정에서 완료한 평가를 선택하세요.")
            self._refresh_all()
            return
        vals = dict(art.physical_parameters) if art is not None else None
        selection_ref = art.selection_ref if art is not None else None
        if art is not None and art.execution_phase == "exploration":
            try:
                selection_ref = svc.find_frozen_selection(art.evaluation_id)
            except MaterialStudyError as exc:
                QMessageBox.warning(self, "보류 시험 최종 확인", _human_error(exc))
                return
        self._start_worker(
            "보류 시험 최종 확인",
            lambda on_prog: svc.evaluate(
                vals,
                expected_revision=snap.document_revision,
                on_progress=on_prog,
                cache_policy="refresh",
                execution_phase="confirmation",
                selection_ref=selection_ref,
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
            snap = self.service.snapshot()
            cid = self._active_campaign_id or snap.active_campaign_id or snap.active_operation_id
            if cid:
                self.service.cancel(cid)
            event.ignore()
            return
        self._closed = True
        self._poll_timer.stop()
        # Matplotlib queues its idle callback without a Qt receiver.
        self.canvas._draw_pending = False
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


def _project_study_reference(project_file: str, node_id: str) -> tuple[Path, str | None]:
    from caelab.project import PROJECT_FILENAME, load_native_project

    project_path = Path(project_file).expanduser().resolve()
    if project_path.is_dir():
        project_path /= PROJECT_FILENAME
    raw = json.loads(project_path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and raw.get("schema_version") == 4:
        node = raw.get("nodes", {}).get(node_id)
        if isinstance(node, dict) and node.get("id") == node_id and node.get("kind") == "research" and (
            node.get("program_id") or node.get("programid")
        ) == "caelab_material_study":
            ref = node.get("files", {}).get("material_study")
            if isinstance(ref, dict) and isinstance(ref.get("path"), str):
                study_id = ref.get("study_id")
                if "study_id" in ref and (not isinstance(study_id, str) or not study_id.strip()):
                    raise MaterialStudyError(
                        "CORRUPT_MANIFEST", "project",
                        {"reason": "프로젝트 노드의 연구 식별자가 올바르지 않습니다."},
                    )
                relative_path = validate_relative_artifact_path(ref["path"], root=project_path.parent)
                return project_path.parent / relative_path, study_id
    else:
        project = load_native_project(project_path)
        node = project.nodes.get(node_id)
        if node is not None and node.kind == "research" and node.program_id == "caelab_material_study":
            ref = node.files.get("document")
            if ref is not None:
                return Path(ref.path), None
    raise MaterialStudyError(
        "STUDY_NOT_FOUND", "project",
        {"reason": "선택한 프로젝트 노드에 연결된 재료 연구 파일이 없습니다."},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CAELAB 재료 물성 보정 연구 창")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--study", help="열려는 재료 연구 매니페스트(.caemat.json)")
    mode.add_argument("--project", help="프로젝트 파일 경로")
    parser.add_argument("--read-only", action="store_true", help="읽기 전용으로 열기")
    parser.add_argument("--node", help="프로젝트 노드 ID")
    args = parser.parse_args(argv)
    if bool(args.project) != bool(args.node):
        parser.error("--project와 --node를 함께 지정하세요.")

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    load_korean_font(app, 10)
    project_service = None
    try:
        if args.project:
            study_path, expected_study_id = _project_study_reference(args.project, args.node)
            project_service = CalibrationStudyService.open(study_path, read_only=args.read_only)
            if expected_study_id is not None and project_service.study_id != expected_study_id:
                raise MaterialStudyError("STUDY_ID_MISMATCH", "project")
            win = MaterialStudyWindow(service=project_service)
        else:
            win = MaterialStudyWindow(study_path=args.study, read_only=args.read_only)
    except Exception as exc:
        if project_service is not None:
            project_service.close()
        traceback.print_exc()
        QMessageBox.critical(None, "재료 연구 창을 열 수 없습니다", _human_error(exc))
        return 1
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
