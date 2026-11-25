"""Independent native CalculiX input, execution and recorded results."""
from __future__ import annotations

import argparse
import math
import re
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFrame, QHeaderView, QHBoxLayout, QLabel,
                              QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
                              QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QToolBar,
                              QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from PySide6.QtCore import QRect, Qt, QTimer

from caelab.workbench import Workbench
from .theme import (MONO_FONT_STACK, build_stylesheet, current_theme_mode, get_palette,
                    register_theme_listener, status_badge_style, theme_toggle_label, toggle_theme_mode)
from .expert_chat import source_name

_STATE_LABELS = {"needs_input": "입력 필요", "ready": "입력 준비",
                 "changed": "원본 변경됨", "missing": "파일 누락", "stale": "오래됨",
                 "opened": "프로그램 열림", "failed": "실패"}
_UNIT_LABELS = {"mass": "질량", "length": "길이", "force": "힘", "time": "시간", "temperature": "온도", "stress": "응력"}
_UNIT_SOURCE_LABELS = {
    "user": "사용자 선언",
    "declared": "사용자 선언",
    "explicit deck": "입력 덱 명시",
    "explicit input": "입력 명시",
    "unknown": "미확인",
}
_RUN_LABELS = {"queued": "대기", "preparing": "입력 보존 중", "running": "계산 중",
               "completed": "프로세스 정상 종료", "failed": "실패", "cancelled": "취소됨",
               "interrupted": "중단됨"}
_PARSE_LABELS = {"pending": "대기", "unparsed": "미판독", "reading": "판독 중",
                 "readable": "판독 완료", "partial": "일부 판독",
                 "unsupported": "미지원 출력 형식", "missing": "출력 없음", "failed": "판독 실패"}
_ACTIVE_STATES = {"queued", "preparing", "running"}
_FIELD_LABELS = {
    'DISP': '변위 (U)',
    'STRESS': '응력 (S)',
    'FORC': '절점 하중 (F)',
    'TOSTRAIN': '총 변형률 (E)',
    'PE': '등가 소성변형률 (PEEQ)',
    'TEMP': '온도 (T)',
}
_COMPONENT_LABELS = {
    'DISP': {'D1': 'Ux (D1)', 'D2': 'Uy (D2)', 'D3': 'Uz (D3)'},
    'STRESS': {'SXX': 'Sxx', 'SYY': 'Syy', 'SZZ': 'Szz', 'SXY': 'Sxy', 'SYZ': 'Syz', 'SZX': 'Szx'},
}


class _RunStatusLabel(QLabel):
    """A splitter must not clip the stage or failure text as it changes."""
    def _fit_text(self):
        if self.isVisible():
            content = self.contentsRect()
            height = self.fontMetrics().boundingRect(QRect(0, 0, max(1, content.width()), 100000),
                                                    Qt.TextWordWrap, self.text()).height()
            self.setMinimumHeight(height + self.height() - content.height())

    def setText(self, text):
        super().setText(text)
        self._fit_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_text()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_text()


def _busy(run) -> bool:
    return run.state in _ACTIVE_STATES or run.parse_state == "reading"


def _signature(path: Path):
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def _unit_text(units: dict) -> str:
    return ", ".join(f"{_UNIT_LABELS.get(key, key)}: {value}" for key, value in units.items()) or "미확인"


def _effective_stress_unit(units: dict) -> str | None:
    from .viewport import deduce_stress_unit

    if not isinstance(units, dict):
        return None
    stress = deduce_stress_unit(units)
    if stress:
        return stress
    mass = str(units.get("mass", "")).strip()
    length = str(units.get("length", "")).strip().lower()
    time = str(units.get("time", "")).strip().lower()
    if mass in {"Mg", "t", "ton", "tonne"} and length == "mm" and time == "s":
        return "MPa"
    if mass.lower() == "g" and length == "mm" and time == "ms":
        return "MPa"
    if mass.lower() == "kg" and length == "mm" and time == "ms":
        return "GPa"
    if mass.lower() == "kg" and length == "m" and time == "s":
        return "Pa"
    return str(units["stress"]).strip() if units.get("stress") else None


def _time_text(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    except (TypeError, ValueError):
        return str(value)


def _display_file(project, raw_path: str | Path | None) -> str:
    if not raw_path:
        return ""
    p = Path(raw_path)
    if project.run_store and p.resolve().is_relative_to(project.run_store.resolve()):
        return p.name
    if project.path and p.resolve().is_relative_to(project.path.parent.resolve()):
        return p.resolve().relative_to(project.path.parent.resolve()).as_posix()
    return source_name(str(p))


def _display_reason(reason, fallback: str) -> str:
    if isinstance(reason, OSError):
        return fallback
    text = str(reason or '')
    if re.search(r'CalculiX (?:실행 파일|연결|솔버)', text, re.I):
        return "CalculiX 실행 파일 또는 연결 설정을 확인하세요."
    if re.search(
        r'\[(?:Errno|WinError)\b|\b(?:PID|SHA256|node_id|request_id|CAELAB_[A-Z0-9_]+)\b'
        r'|program\.(?:source|runtime_env|path|reason)|\.runs\b'
        r'|설치 레지스트리|실행 파일이 없거나|실행 파일을 찾지 못했습니다.*?\('
        r'|\b[0-9a-f]{32,64}\b|\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b',
        text,
        re.I,
    ):
        return fallback
    return text


class CalculixDeckHighlighter(QSyntaxHighlighter):
    theme_mode: str | None = None

    def highlightBlock(self, text):  # noqa: N802
        p = get_palette(self.theme_mode)
        if re.match(r'^\*\*', text) or (re.match(r'^\s*#', text) and not re.match(r'^\s*#include\b', text, re.I)):
            self._format(0, len(text), p['syn_comment'])
            return
        keyword = re.match(r'^\*[A-Za-z][A-Za-z0-9 _-]*', text)
        if keyword:
            section = re.match(r'^\*(?:STEP|END\s+STEP|STATIC|DYNAMIC|FREQUENCY|BUCKLE|HEAT\s+TRANSFER|COUPLED\s+TEMPERATURE-DISPLACEMENT|VISCO|MODAL\s+DYNAMIC)\b', text, re.I)
            self._format(0, keyword.end(), p['syn_section'] if section else p['syn_keyword'], True)
            for match in re.finditer(r',?\s*([A-Za-z_][A-Za-z0-9_]*)\s*=', text):
                self._format(match.start(1), len(match[1]), p['syn_param'])
            return
        rad_kw = re.match(r'^\s*(?:/[A-Za-z0-9_/-]+|#include\b)', text, re.I)
        if rad_kw:
            section = re.match(r'^\s*/(?:BEGIN|RUN|STOP|END)\b', text, re.I)
            self._format(rad_kw.start(), rad_kw.end() - rad_kw.start(),
                         p['syn_section'] if section else p['syn_keyword'], True)

    def _format(self, start, length, color, bold=False):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(str(color)))
        if bold:
            fmt.setFontWeight(QFont.Bold)
        self.setFormat(start, length, fmt)


class SolverLogHighlighter(CalculixDeckHighlighter):
    def highlightBlock(self, text):  # noqa: N802
        p = get_palette(self.theme_mode)
        for pattern, key, bold in (
                (r'\bSTEP\s+\d+|\bINCREMENT\s+\d+|iteration\s+\d+|Job finished|\bCYCLE\s+\d+|\bNORMAL TERMINATION\b', 'syn_keyword', False),
                (r'\*WARNING.*|\bWARNING\s+ID\s*:|\*\*\s*WARNING\b|cutback', 'syn_warning', True),
                (r'\*ERROR.*|\bERROR\s+ID\s*:|\*\*\s*ERROR\b|\bERROR\s+TERMINATION\b|divergence|not convergent', 'syn_error', True)):
            for match in re.finditer(pattern, text, re.I):
                self._format(match.start(), len(match[0]), p[key], bold)


class _LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def paintEvent(self, event):  # noqa: N802
        self.editor.paint_line_numbers(event)


class DeckCodeEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        font = QFont()
        font.setFamilies(['Cascadia Mono', 'Consolas', 'Malgun Gothic'])
        font.setPointSize(10)
        self.setFont(font)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.gutter = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._update_gutter)
        self.highlighter = CalculixDeckHighlighter(self.document())
        self._update_margin()

    def gutter_width(self):
        return 14 + self.fontMetrics().horizontalAdvance('9') * len(str(self.blockCount()))

    def _update_margin(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margin()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        rect = self.contentsRect()
        self.gutter.setGeometry(rect.left(), rect.top(), self.gutter_width(), rect.height())

    def paint_line_numbers(self, event):
        p = get_palette()
        painter = QPainter(self.gutter)
        painter.fillRect(event.rect(), QColor(str(p['gutter_bg'])))
        painter.setPen(QColor(str(p['gutter_fg'])))
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        while block.isValid() and top <= event.rect().bottom():
            height = self.blockBoundingRect(block).height()
            if block.isVisible() and top + height >= event.rect().top():
                painter.drawText(0, int(top), self.gutter.width() - 6, self.fontMetrics().height(),
                                 Qt.AlignRight, str(block.blockNumber() + 1))
            top += height
            block = block.next()


class NativeAnalysisWindow(QMainWindow):
    def __init__(self, project_path: str | Path, node_id: str,
                 workbench: Workbench | None = None) -> None:
        super().__init__()
        self.workbench = workbench or Workbench()
        self.project = self.workbench.open_native_project(project_path)
        self.node_id = node_id
        self._project_signature = _signature(self.project.path)
        self._runs = {}
        self._owned_run_ids = set()
        self._displayed_run_id = None
        self._displayed_run_state = None
        self._displayed_result = None
        self._had_runs = False
        self._empty_initialized = False
        self._expert_windows = {}
        self._expert_window = None
        node = self.project.nodes[node_id]
        from caelab.native.adapter import supported_solver_ids
        if node.kind != "analysis" and node.program_id not in supported_solver_ids():
            raise ValueError("해석 입력 확인 대상이 아닙니다.")
        program_name = {"calculix": "CalculiX", "codeaster": "Code_Aster", "radioss": "OpenRadioss",
                        "openradioss": "OpenRadioss", "opencourant": "OpenCourant", "lsdyna": "LS-DYNA"}.get(node.program_id, node.program_id.capitalize())
        self.setWindowTitle(f"{self.project.name} — {program_name} 해석")
        self.resize(1360, 860)
        self.view_menu = self.menuBar().addMenu("보기(&V)")
        self.theme_action = self.view_menu.addAction(theme_toggle_label(), toggle_theme_mode)
        self.tools_menu = self.menuBar().addMenu("도구(&T)")
        self.expert_action = self.tools_menu.addAction("AI 전문가 자문 및 토론 요청…")
        self.expert_action.setShortcut(QKeySequence("Ctrl+E"))
        self.expert_action.triggered.connect(self.request_expert_debate)
        toolbar = QToolBar("해석", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        refresh = toolbar.addAction("파일 상태 새로고침")
        refresh.setShortcut(QKeySequence.Refresh)
        refresh.triggered.connect(self.refresh_input)
        self.reload_action = toolbar.addAction("프로젝트 다시 읽기")
        self.reload_action.triggered.connect(self.reload_project)
        toolbar.addSeparator()
        self.run_action = toolbar.addAction("현재 입력 계산")
        self.run_action.triggered.connect(self.start_run)
        run_btn = toolbar.widgetForAction(self.run_action)
        if run_btn is not None:
            run_btn.setObjectName("primary")
        self.cancel_action = toolbar.addAction("진행 중 계산 취소")
        self.cancel_action.triggered.connect(self.cancel_run)
        self.cancel_action.setEnabled(False)
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 604800)
        self.timeout.setValue(3600)
        self.timeout.setSuffix(" 초")
        self.timeout.setToolTip("최대 실행 시간. 이 시간이 지나면 이 창에서 시작한 계산을 종료합니다.")
        toolbar.addWidget(QLabel("  제한 시간 "))
        toolbar.addWidget(self.timeout)
        toolbar.addSeparator()
        self.expert_toolbar_action = toolbar.addAction("AI 전문가 자문 (Ctrl+E)")
        self.expert_toolbar_action.setToolTip("현재 계산 결과 및 로그를 바탕으로 AI 전문가 패널 토론을 시작합니다 (Ctrl+E)")
        self.expert_toolbar_action.triggered.connect(self.request_expert_debate)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        header_card = QFrame()
        header_card.setObjectName("sectionCard")
        header = QVBoxLayout(header_card)
        header.setContentsMargins(8, 3, 8, 3)
        header.setSpacing(2)
        status_line = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        status_line.addWidget(self.status, 1)
        self.unit_badge = QLabel()
        status_line.addWidget(self.unit_badge)
        header.addLayout(status_line)
        self.input_heading = QLabel()
        self.input_heading.setWordWrap(True)
        self.input_heading.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.input_heading.setTextInteractionFlags(Qt.TextSelectableByMouse)
        header.addWidget(self.input_heading)
        self.active_status = QLabel()
        self.active_status.setWordWrap(True)
        self.active_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        header.addWidget(self.active_status)
        layout.addWidget(header_card)

        run_header_card = QFrame()
        run_header_card.setObjectName("sectionCard")
        run_header = QVBoxLayout(run_header_card)
        run_header.setContentsMargins(8, 3, 8, 3)
        run_header.setSpacing(2)
        selection = QHBoxLayout()
        selection.addWidget(QLabel("조회할 실행"))
        self.run_selector = QComboBox()
        self.run_selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.run_selector.setMinimumContentsLength(20)
        self.run_selector.setMinimumWidth(160)
        self.run_selector.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.run_selector.currentIndexChanged.connect(self.select_run)
        selection.addWidget(self.run_selector, 1)
        run_header.addLayout(selection)
        self.run_status = _RunStatusLabel("아직 실행 기록이 없습니다.")
        self.run_status.setWordWrap(True)
        self.run_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        run_header.addWidget(self.run_status)
        self.run_source = QLabel()
        self.run_source.setWordWrap(True)
        self.run_source.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.run_source.setTextInteractionFlags(Qt.TextSelectableByMouse)
        run_header.addWidget(self.run_source)
        layout.addWidget(run_header_card)

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.source = DeckCodeEditor()
        self.source.setPlaceholderText("선택한 네이티브 입력 파일의 원문을 표시합니다.")
        input_tabs = QTabWidget()
        self.input_tabs = input_tabs
        deck_split = QSplitter(Qt.Horizontal)
        outline_panel = QWidget()
        outline_layout = QVBoxLayout(outline_panel)
        outline_layout.setContentsMargins(0, 0, 0, 0)
        self.section_jump = QComboBox()
        for title, category in (("전체 키워드", "all"), ("재료", "material"), ("경계조건 / 하중", "bc_load"),
                                ("접촉", "contact"), ("해석 단계", "step"), ("메시 / 집합", "mesh")):
            self.section_jump.addItem(title, category)
        self.section_jump.setAccessibleName("입력 키워드 필터")
        self.section_jump.currentIndexChanged.connect(self._filter_deck_outline)
        outline_layout.addWidget(self.section_jump)
        self.deck_outline = QTreeWidget()
        self.deck_outline.setHeaderLabels(["입력 키워드", "줄"])
        self.deck_outline.header().setStretchLastSection(False)
        self.deck_outline.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.deck_outline.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.deck_outline.itemClicked.connect(self._jump_to_deck_line)
        outline_layout.addWidget(self.deck_outline)
        deck_split.addWidget(outline_panel)
        deck_split.addWidget(self.source)
        deck_split.setSizes([300, 1020])
        input_tabs.addTab(deck_split, "입력 원문")
        input_tabs.addTab(self.details, "파일 정보")

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("monoView")
        self.log_highlighter = SolverLogHighlighter(self.log.document())
        self.log.setPlaceholderText("선택한 실행의 원본 로그를 표시합니다.")
        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(6, 6, 6, 6)
        log_layout.setSpacing(5)
        log_header = QHBoxLayout()
        self.log_summary_badge = QLabel()
        log_header.addWidget(self.log_summary_badge, 1)
        self.btn_log_expert = QPushButton("AI 원인 분석 및 자문 요청 (Ctrl+E)")
        self.btn_log_expert.setObjectName("expert_log_btn")
        self.btn_log_expert.setToolTip("이 로그와 실행 결과를 바탕으로 AI 전문가 패널 토론을 시작합니다 (Ctrl+E)")
        self.btn_log_expert.clicked.connect(self.request_expert_debate)
        log_header.addWidget(self.btn_log_expert)
        log_layout.addLayout(log_header)
        log_layout.addWidget(self.log, 1)

        from .native_result_view import NativeResultView
        self.native_view = NativeResultView(self.workbench.read_native_frame)
        self.native_view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.native_view.setMinimumWidth(300)

        result_panel = QWidget()
        result_layout = QVBoxLayout(result_panel)
        result_layout.setContentsMargins(4, 4, 4, 4)
        result_layout.setSpacing(0)
        result_main_split = QSplitter(Qt.Horizontal)
        result_main_split.addWidget(self.native_view)

        summary_panel = QWidget()
        summary_panel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        summary_panel.setMinimumWidth(340)
        summary_layout = QVBoxLayout(summary_panel)
        summary_layout.setContentsMargins(4, 0, 0, 0)
        summary_layout.setSpacing(3)

        summary_top_bar = QHBoxLayout()
        summary_top_bar.setSpacing(6)
        self.result_unit_badge = QLabel()
        summary_top_bar.addWidget(self.result_unit_badge)
        self.result_verify_badge = QLabel()
        self.result_verify_badge.hide()
        summary_top_bar.addWidget(self.result_verify_badge)
        summary_top_bar.addStretch(1)
        self.btn_result_expert = QPushButton("AI 전문가 결과 검토 (Ctrl+E)")
        self.btn_result_expert.setObjectName("expert_result_btn")
        self.btn_result_expert.setToolTip("선택한 결과와 요약을 바탕으로 AI 전문가 패널 토론을 시작합니다 (Ctrl+E)")
        self.btn_result_expert.clicked.connect(self.request_expert_debate)
        self.btn_expert_debate = self.btn_result_expert
        summary_top_bar.addWidget(self.btn_result_expert)
        summary_layout.addLayout(summary_top_bar)

        self.frame_selector = QComboBox()
        self.frame_selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.frame_selector.setMinimumContentsLength(18)
        self.frame_selector.setMinimumWidth(140)
        self.frame_selector.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.frame_selector.currentIndexChanged.connect(self.show_result)
        frame_line = QHBoxLayout()
        frame_line.setSpacing(6)
        frame_line.addWidget(QLabel("프레임"))
        frame_line.addWidget(self.frame_selector, 1)
        summary_layout.addLayout(frame_line)

        kpis = QHBoxLayout()
        kpis.setSpacing(6)
        for title, attr in (
            ("최대 변위 |U|max", "kpi_disp_value"),
            ("최대 응력 · 절점 규모", "kpi_nodes_value"),
            ("합력 평형 · 에너지 검산", "kpi_dat_value"),
        ):
            card = QFrame()
            card.setObjectName("kpiCard")
            card.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            card.setMinimumWidth(88)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 4, 10, 4)
            card_layout.setSpacing(2)
            title_lbl = QLabel(title)
            title_lbl.setObjectName("mutedLabel")
            title_lbl.setWordWrap(True)
            title_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            card_layout.addWidget(title_lbl)
            value = QLabel("미확인")
            value.setObjectName("cardTitle")
            value.setWordWrap(True)
            value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            setattr(self, attr, value)
            card_layout.addWidget(value)
            kpis.addWidget(card, 1)
        summary_layout.addLayout(kpis)

        field_header = QLabel("물리량 성분별 분포 범위")
        field_header.setObjectName("cardTitle")
        summary_layout.addWidget(field_header)
        self.field_table = self._result_table(["물리량", "성분", "최솟값", "최댓값", "단위"])
        self.field_table.setMinimumHeight(64)
        self.field_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        summary_layout.addWidget(self.field_table, 2)

        self.dat_container = QWidget()
        dat_layout = QVBoxLayout(self.dat_container)
        dat_layout.setContentsMargins(0, 0, 0, 0)
        dat_layout.setSpacing(2)
        dat_header = QLabel("경계 · 하중 집합 합력 및 변위 요약 (.dat)")
        dat_header.setObjectName("cardTitle")
        dat_layout.addWidget(dat_header)
        self.dat_frame_label = QLabel()
        self.dat_frame_label.setObjectName("mutedLabel")
        self.dat_frame_label.setWordWrap(True)
        self.dat_frame_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        dat_layout.addWidget(self.dat_frame_label)
        self.dat_table = self._result_table(["절점 집합", "항목", "절점 수", "X, Y, Z 성분", "단위"])
        self.dat_table.setMinimumHeight(62)
        self.dat_table.setMaximumHeight(220)
        self.dat_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.dat_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.dat_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.dat_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.dat_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        dat_layout.addWidget(self.dat_table)
        summary_layout.addWidget(self.dat_container, 1)

        self.energy_container = QWidget()
        energy_layout = QVBoxLayout(self.energy_container)
        energy_layout.setContentsMargins(0, 0, 0, 0)
        energy_layout.setSpacing(2)
        energy_header = QLabel("동해석 에너지 수지 · 사이클 검산")
        energy_header.setObjectName("cardTitle")
        energy_layout.addWidget(energy_header)
        self.energy_table = self._result_table(["검산 항목", "측정값", "판정 / 비고"])
        self.energy_table.setMinimumHeight(60)
        self.energy_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.energy_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.energy_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        energy_layout.addWidget(self.energy_table)
        self.energy_container.hide()
        summary_layout.addWidget(self.energy_container, 2)

        self.warnings_label = QLabel()
        self.warnings_label.setWordWrap(True)
        self.warnings_label.hide()
        summary_layout.addWidget(self.warnings_label)

        raw_bar = QHBoxLayout()
        raw_bar.setContentsMargins(0, 0, 0, 0)
        raw_bar.addStretch(1)
        self.btn_toggle_raw_summary = QPushButton("텍스트 요약 보기")
        self.btn_toggle_raw_summary.setCheckable(True)
        self.btn_toggle_raw_summary.setChecked(False)
        self.btn_toggle_raw_summary.toggled.connect(self._on_toggle_raw_summary)
        raw_bar.addWidget(self.btn_toggle_raw_summary)
        summary_layout.addLayout(raw_bar)

        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMinimumHeight(80)
        self.result_text.setMaximumHeight(150)
        self.result_text.hide()
        summary_layout.addWidget(self.result_text)

        summary_scroll = QScrollArea()
        summary_scroll.setWidgetResizable(True)
        summary_scroll.setFrameShape(QFrame.NoFrame)
        summary_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        summary_scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        summary_scroll.setMinimumWidth(340)
        summary_scroll.setMinimumHeight(180)
        summary_scroll.viewport().setStyleSheet("background: transparent;")
        summary_scroll.setWidget(summary_panel)

        result_main_split.addWidget(summary_scroll)
        result_main_split.setSizes([820, 520])
        result_main_split.setStretchFactor(0, 3)
        result_main_split.setStretchFactor(1, 2)
        result_layout.addWidget(result_main_split, 1)

        output_tabs = QTabWidget()
        self.output_tabs = output_tabs
        output_tabs.addTab(input_tabs, "입력 덱")
        output_tabs.addTab(log_panel, "로그")
        output_tabs.addTab(result_panel, "결과")
        layout.addWidget(output_tabs, 1)
        self.setCentralWidget(container)
        self._theme_mode = current_theme_mode()
        self.apply_theme()
        register_theme_listener(self.apply_theme)
        self._clear_result()
        self.refresh_input()
        self.refresh_runs()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh_runs)
        self.timer.start()

    def request_expert_debate(self) -> None:
        """Open a separate discussion with a draft for the displayed run/frame."""
        run = self._runs.get(self._displayed_run_id)
        run_id = run.id if run else None
        frame_index = self.frame_selector.currentData() if run else None
        context = (self.project.path, self.node_id, run_id, frame_index)
        # Keep edited drafts and attachments tied to their original run/frame.
        if context not in self._expert_windows:
            from .expert_window import ExpertWindow
            self._expert_windows[context] = ExpertWindow(
                project_path=self.project.path, node_id=self.node_id,
                run_id=run_id, requested_frame_index=frame_index,
                workbench=self.workbench,
            )
            self._expert_windows[context].setAttribute(Qt.WA_DeleteOnClose)
            self._expert_windows[context].destroyed.connect(
                lambda _object=None, key=context: self._forget_expert_window(key))
        self._expert_window = window = self._expert_windows[context]
        if not window.question.toPlainText().strip():
            lines = [f"[{self.project.name}] 해석 결과를 검토해 주세요."]
            if run:
                lines.extend([f"솔버: {run.solver_id}", self.run_source.text(),
                              f"실행 시각: {_time_text(run.created_at)}", self.run_status.text()])
                if run.returncode is not None:
                    lines.append(f"프로세스 종료 코드: {run.returncode}")
                if run.finished_at:
                    try:
                        elapsed = (datetime.fromisoformat(run.finished_at)
                                   - datetime.fromisoformat(run.created_at)).total_seconds()
                    except (ValueError, TypeError):
                        pass
                    else:
                        lines.append(f"실행 경과 시간: {elapsed:.2f}초")
                lines.append(self.result_unit_badge.text())
                if frame_index is not None:
                    lines.append("선택 프레임: " + self.frame_selector.currentText())
                lines.extend(["", "[현재 결과 요약]", self.result_text.toPlainText()])
                log_lines = [line.strip() for line in self.log.toPlainText().splitlines() if line.strip()]
                diagnostics = [line for line in log_lines if re.search(
                    r"ERROR|WARNING|FAIL|DIVERG|STOP|NOT CONVERG|오류|경고", line, re.I)]
                if log_lines:
                    lines.extend(["", "[최근 로그 오류·경고]" if diagnostics else "[최근 로그]"])
                    lines.extend(f"  * {line}" for line in (diagnostics or log_lines)[-5:])
            else:
                lines.append("아직 선택한 실행이 없습니다. 해석 입력과 확인할 사항을 논의하고 싶습니다.")
            window.set_native_analysis_draft("\n".join(lines))
        window.show()
        window.raise_()
        window.activateWindow()

    def _forget_expert_window(self, context) -> None:
        window = self._expert_windows.pop(context, None)
        if self._expert_window is window:
            self._expert_window = None

    def apply_theme(self, mode: str | None = None) -> None:
        resolved = mode if mode in ("dark", "light") else current_theme_mode()
        self._theme_mode = resolved
        self.setStyleSheet(build_stylesheet(resolved))
        self.theme_action.setText(theme_toggle_label(resolved))
        self.active_status.setStyleSheet(status_badge_style("running", resolved))
        self.result_verify_badge.setStyleSheet(status_badge_style("neutral", resolved))
        self.warnings_label.setStyleSheet(status_badge_style("warning", resolved))
        self.source.highlighter.theme_mode = resolved
        self.source.highlighter.rehighlight()
        self.source.gutter.update()
        self.log_highlighter.theme_mode = resolved
        self.log_highlighter.rehighlight()
        self._update_unit_badges()
        self._update_log_summary()

    def _on_toggle_raw_summary(self, checked: bool) -> None:
        self.result_text.setVisible(checked)
        self.btn_toggle_raw_summary.setText("텍스트 요약 숨기기" if checked else "텍스트 요약 보기")

    def _update_unit_badges(self):
        mode = getattr(self, '_theme_mode', None)
        reference = self.workbench.input_file(self.project, self.node_id)
        for badge, units, exists in ((self.unit_badge, reference.units if reference else {}, reference is not None),
                                     (self.result_unit_badge, getattr(self._runs.get(self._displayed_run_id), 'units', {}),
                                      self._displayed_run_id in self._runs)):
            tone = 'ready' if units else 'warning' if exists else 'neutral'
            if units.get('mass'):
                keys = ('mass', 'length', 'time', 'force')
            elif units.get('length') or units.get('force'):
                keys = ('length', 'force')
            else:
                keys = ('time', 'temperature', 'stress')
            text = ' · '.join(str(units[key]) for key in keys if units.get(key))
            stress_unit = _effective_stress_unit(units)
            if stress_unit and not (keys == ('time', 'temperature', 'stress') and text == stress_unit):
                text = f'{text} ({stress_unit})' if text else stress_unit
            badge.setText(f'단위: {text}' if text else '단위 미확인' if exists else '자료 없음')
            badge.setStyleSheet(status_badge_style(tone, mode))

    @staticmethod
    def _result_table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.horizontalHeader().setFixedHeight(26)
        table.verticalHeader().setDefaultSectionSize(24)
        table.verticalHeader().setVisible(False)
        table.setMinimumHeight(60)
        return table

    @staticmethod
    def _append_row(table, values):
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(str(value)))

    def _clear_result(self):
        self.native_view.set_selection(None, None)
        self.frame_selector.setToolTip("")
        self.result_text.clear()
        self.field_table.setRowCount(0)
        self.dat_table.setRowCount(0)
        self.dat_container.hide()
        self.dat_frame_label.clear()
        self.energy_table.setRowCount(0)
        self.energy_container.hide()
        self.warnings_label.clear()
        self.warnings_label.hide()
        for value in (self.kpi_disp_value, self.kpi_nodes_value, self.kpi_dat_value):
            value.setText('미확인')
        self.result_unit_badge.setText('자료 없음')
        self.result_unit_badge.setStyleSheet(status_badge_style('neutral', getattr(self, '_theme_mode', None)))

    def _update_log_summary(self):
        text = self.log.toPlainText()
        errors = len(re.findall(r'(?:\*ERROR\b|\bERROR\s+ID\s*:|\*\*\s*ERROR\b|\bERROR\s+TERMINATION\b)', text, re.I))
        warnings = len(re.findall(r'(?:\*WARNING\b|\bWARNING\s+ID\s*:|\*\*\s*WARNING\b)', text, re.I))
        self.log_summary_badge.setText(f'최근 로그: 오류 {errors} / 경고 {warnings}')
        self.log_summary_badge.setStyleSheet(
            status_badge_style('error' if errors else 'warning' if warnings else 'neutral', getattr(self, '_theme_mode', None))
        )

    @staticmethod
    def _deck_category(keyword):
        kw = keyword.upper().lstrip('/*')
        if kw in {'STEP', 'END STEP', 'STATIC', 'DYNAMIC', 'FREQUENCY', 'BUCKLE', 'HEAT TRANSFER',
                  'COUPLED TEMPERATURE-DISPLACEMENT', 'VISCO', 'MODAL DYNAMIC'} or kw.startswith(('RUN', 'ANIM', 'STOP')):
            return 'step'
        if kw in {'MATERIAL', 'ELASTIC', 'PLASTIC', 'DENSITY', 'EXPANSION', 'CONDUCTIVITY', 'SPECIFIC HEAT'} or kw.startswith(('MAT', 'EOS', 'FAIL')):
            return 'material'
        if kw in {'BOUNDARY', 'CLOAD', 'DLOAD', 'DSLOAD', 'TEMPERATURE', 'CFLUX', 'DFLUX', 'FILM', 'RADIATE',
                  'PRE-TENSION SECTION'} or kw.startswith(('BCS', 'CLOAD', 'GRAV', 'INIVEL', 'IMPDISP', 'IMPVEL', 'MONVOL')):
            return 'bc_load'
        if any(word in kw for word in ('CONTACT', 'FRICTION', 'SURFACE', 'TIE')) or kw.startswith(('INTER', 'SURF', 'TIE')):
            return 'contact'
        if kw in {'NODE', 'ELEMENT', 'NSET', 'ELSET', 'SOLID SECTION', 'SHELL SECTION', 'BEAM SECTION'} or kw.startswith(('NODE', 'PART', 'PROP', 'SHELL', 'SH3N', 'BRICK', 'TETRA', 'TRUSS', 'BEAM', 'SPRING', 'GRNOD', 'GRPART', 'GRSH3N', 'GRBRIC', 'GRTETRA', 'SKEW', 'FRAME')):
            return 'mesh'
        return 'other'

    def _rebuild_deck_outline(self):
        self.deck_outline.clear()
        step = None
        for line_no, line in enumerate(self.source.toPlainText().splitlines(), 1):
            text = line.lstrip()
            if not text:
                continue
            if text.startswith('**'):
                continue
            if text.startswith('#') and not text.lower().startswith('#include'):
                continue

            if text.startswith('*'):
                keyword = text.split(',', 1)[0].strip().upper().lstrip('*')
                name = re.search(r'\b(?:NAME|ELSET|NSET|MATERIAL|INPUT|SURFACE|INTERACTION)\s*=\s*([^,]+)', text, re.I)
                title = f'*{keyword}' + (f' ({name[1].strip()})' if name else '')
                cat = self._deck_category(keyword)
                parent = step if step is not None and keyword != 'STEP' else self.deck_outline
                item = QTreeWidgetItem(parent, [title, str(line_no)])
                item.setData(0, Qt.UserRole, line_no)
                item.setData(0, Qt.UserRole + 1, cat)
                if keyword == 'STEP':
                    step = item
                elif keyword == 'END STEP':
                    step = None
                continue

            if text.startswith('/') or text.lower().startswith('#include'):
                keyword_part = text.split()[0].rstrip('/')
                title = keyword_part
                cat = self._deck_category(keyword_part.lstrip('/'))
                parent = step if step is not None and not keyword_part.upper().startswith('/RUN') else self.deck_outline
                item = QTreeWidgetItem(parent, [title, str(line_no)])
                item.setData(0, Qt.UserRole, line_no)
                item.setData(0, Qt.UserRole + 1, cat)
                if keyword_part.upper().startswith('/RUN'):
                    step = item
                elif keyword_part.upper() in {'/STOP', '/END'}:
                    step = None
        self.deck_outline.expandAll()
        self._filter_deck_outline()

    def _filter_deck_outline(self, *_):
        category = self.section_jump.currentData()
        def apply(item):
            children = [apply(item.child(i)) for i in range(item.childCount())]
            visible = category == 'all' or item.data(0, Qt.UserRole + 1) == category or any(children)
            item.setHidden(not visible)
            return visible
        for index in range(self.deck_outline.topLevelItemCount()):
            apply(self.deck_outline.topLevelItem(index))

    def _jump_to_deck_line(self, item, *_):
        block = self.source.document().findBlockByNumber(item.data(0, Qt.UserRole) - 1)
        self.source.setTextCursor(QTextCursor(block))
        self.source.centerCursor()
        self.source.setFocus()

    def _render_input_status(self) -> None:
        node = self.project.nodes[self.node_id]
        state = _STATE_LABELS.get(node.state, node.state)
        reason = node.reason
        for node_id, owner in self.project.nodes.items():
            source = self.workbench.input_file(self.project, node_id) or owner.files.get("output")
            reason = reason.replace(node_id, Path(source.path).name if source else "프로그램")
        for source_state in ("changed", "missing", "stale"):
            reason = reason.replace(f": {source_state} —", f": {_STATE_LABELS[source_state]} —")
        reason = reason.replace(".files.input", " 입력 파일").replace(".files.output", " 출력 파일")
        reason = _display_reason(reason, "입력 파일과 선택한 실행의 상태를 확인하세요.")
        self.status.setText(f"입력 상태: {state}" + (f" — {reason}" if reason else ""))

    def refresh_input(self) -> None:
        self.workbench.refresh_native_project(self.project)
        node = self.project.nodes[self.node_id]
        reference = self.workbench.input_file(self.project, self.node_id)
        program = next((p for p in self.workbench.programs() if p.spec.id == node.program_id), None)
        self._render_input_status()
        lines = [f"프로젝트: {self.project.path}"]
        self.source.clear()
        if reference is None:
            lines.append("선택한 입력 파일이 없습니다.")
            self.input_heading.setText("계산할 입력: 파일 없음")
        else:
            units = ", ".join(f"{_UNIT_LABELS.get(name, name)}: {unit}" for name, unit in reference.units.items())
            raw_source = str(reference.unit_source or "").strip()
            unit_source = _UNIT_SOURCE_LABELS.get(raw_source.lower(), raw_source)
            p_ref = Path(reference.path)
            shown_input_path = (
                p_ref.name
                if (self.project.run_store and p_ref.resolve().is_relative_to(self.project.run_store.resolve()))
                else reference.path
            )
            lines += [f"입력 파일: {shown_input_path}",
                      f"파일 상태: {_STATE_LABELS.get(reference.status, reference.status)}",
                      f"단위: {units or '미확인 — 사용자 선언 필요'}",
                      f"단위 출처: {unit_source or '미확인'}"]
            self.input_heading.setText(f"계산할 입력: {_display_file(self.project, reference.path)}  |  단위: {_unit_text(reference.units)} ({unit_source or '미확인'})")
            try:
                # Keep very large decks from exhausting the inspection window.
                with Path(reference.path).open("rb") as stream:
                    preview = stream.read(2 * 1024 * 1024 + 1)
                self.source.setPlainText(preview[:2 * 1024 * 1024].decode("utf-8", errors="replace"))
                if len(preview) > 2 * 1024 * 1024:
                    lines.append("원문 표시는 처음 2 MiB로 제한합니다. 전체 파일은 전용 프로그램에서 확인하세요.")
            except OSError:
                lines.append("입력 파일을 읽지 못했습니다. 파일 위치와 접근 권한을 확인하세요.")
        if program:
            lines.append(f"CalculiX 연결 상태: {'사용 가능' if program.available else '설정 필요'}")
        self.details.setPlainText("\n".join(lines))
        self._rebuild_deck_outline()
        self._update_unit_badges()
        self._update_actions()

    def _update_actions(self) -> None:
        changed = _signature(self.project.path) != self._project_signature
        active = [run for run in self._runs.values() if _busy(run)]
        self.run_action.setEnabled(not changed and not active)
        self.cancel_action.setEnabled(any(run.id in self._owned_run_ids for run in active))
        self.reload_action.setEnabled(not active)
        self.active_status.setText("\n".join(
            f"{'이 창에서 진행 중' if run.id in self._owned_run_ids else '다른 창에서 진행 중'}: "
            f"{Path(run.input_path).name} — {'결과 판독 중' if run.parse_state == 'reading' else _RUN_LABELS.get(run.state, run.state)}"
            for run in active))
        self.active_status.setVisible(bool(active))
        if changed:
            self.status.setText("저장된 프로젝트가 변경되었습니다. ‘프로젝트 다시 읽기’로 계산할 입력을 확인하세요.")

    def reload_project(self) -> None:
        previous_selection = (self._displayed_run_id, self.frame_selector.currentData())
        try:
            project = self.workbench.open_native_project(self.project.path)
            node = project.nodes.get(self.node_id)
            from caelab.native.adapter import supported_solver_ids
            if node is None or (node.kind != "analysis" and node.program_id not in supported_solver_ids()):
                raise ValueError("저장된 프로젝트에 이 해석 상자가 없습니다.")
            self.project = project
            self._project_signature = _signature(project.path)
            self._runs.clear()
            self._displayed_run_id = None
            self._displayed_run_state = None
            self.native_view.set_selection(None, None)
            self.refresh_input()
            self.refresh_runs(preferred_selection=previous_selection)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "프로젝트를 다시 읽지 못했습니다", _display_reason(error, "프로젝트 파일 위치와 접근 권한을 확인하세요."))

    def start_run(self) -> None:
        if _signature(self.project.path) != self._project_signature:
            self._update_actions()
            return
        try:
            # Refresh the visible input before the explicit run command. The service
            # owns snapshotting, process creation and all validation of its lineage.
            self.refresh_input()
            run = self.workbench.submit_native(self.project, self.node_id, timeout=self.timeout.value())
            self._owned_run_ids.add(run.id)
            self.refresh_runs()
        except (OSError, ValueError, RuntimeError) as error:
            node = self.project.nodes.get(self.node_id)
            solver_name = node.program_id.capitalize() if node else "솔버"
            QMessageBox.warning(self, "계산을 시작하지 못했습니다", _display_reason(error, f"입력 파일과 {solver_name} 연결 설정을 확인하세요."))

    def cancel_run(self) -> None:
        for run in self._runs.values():
            if run.id in self._owned_run_ids and _busy(run):
                self.workbench.cancel_native_run(run.id)
        self.refresh_runs()

    def refresh_runs(self, *, preferred_selection=None) -> None:
        try:
            runs = self.workbench.native_runs(self.project, self.node_id)
            selected = self.workbench.selected_native_run(self.project, self.node_id)
        except (OSError, ValueError, RuntimeError):
            self.run_status.setText("실행 기록을 읽지 못했습니다. 프로젝트 파일과 접근 권한을 확인하세요.")
            self.log.clear()
            self.run_source.clear()
            self.frame_selector.clear()
            self._displayed_run_id = None
            self._displayed_run_state = None
            self._displayed_result = None
            self._had_runs = False
            self._clear_result()
            self._update_log_summary()
            self._update_actions()
            return
        self._runs = {run.id: run for run in runs}
        selected_id = selected.id if selected is not None else None
        self.run_selector.blockSignals(True)
        ids = [self.run_selector.itemData(index) for index in range(self.run_selector.count())]
        rebuild = ids != [run.id for run in runs]
        if rebuild:
            self.run_selector.clear()
        for index, run in enumerate(runs):
            label = f"{_time_text(run.created_at)} — {Path(run.input_path).name} — {_RUN_LABELS.get(run.state, run.state)}"
            if rebuild:
                self.run_selector.addItem(label, run.id)
            elif self.run_selector.itemText(index) != label:
                self.run_selector.setItemText(index, label)
        self.run_selector.setCurrentIndex(self.run_selector.findData(selected_id))
        self.run_selector.blockSignals(False)
        self._render_input_status()
        self._update_actions()
        if selected is None:
            if (self._displayed_run_id is None and self._displayed_run_state is None and not self._empty_initialized) or self._had_runs:
                self.output_tabs.setCurrentIndex(0)
                self._empty_initialized = True
                self._had_runs = False
            self.run_status.setText("아직 선택한 실행이 없습니다.")
            self.run_source.clear()
            self.log.clear()
            self.frame_selector.clear()
            self._displayed_run_id = None
            self._displayed_run_state = None
            self._displayed_result = None
            self._clear_result()
            self._update_log_summary()
            return
        self._show_run(selected, preferred_selection=preferred_selection)

    def select_run(self) -> None:
        run_id = self.run_selector.currentData()
        if run_id is None:
            return
        try:
            self.workbench.select_native_run(self.project, self.node_id, run_id)
            self.refresh_runs()
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "실행을 선택하지 못했습니다", _display_reason(error, "프로젝트를 다시 읽은 뒤 실행 기록을 선택하세요."))

    def _show_run(self, run, *, preferred_selection=None) -> None:
        changed = run.id != self._displayed_run_id
        now_busy = _busy(run)
        was_busy = self._displayed_run_state in _ACTIVE_STATES or self._displayed_run_state == "reading"
        self._displayed_run_id = run.id
        self._displayed_run_state = "reading" if now_busy and run.parse_state == "reading" else run.state
        self._had_runs = True
        if changed or (was_busy and not now_busy):
            result_payload = run.result or {}
            if now_busy:
                self.output_tabs.setCurrentIndex(1)
            elif (
                run.state in {"failed", "cancelled"}
                and not result_payload.get("frames")
                and not (result_payload.get("dat") or {}).get("sets")
            ):
                self.output_tabs.setCurrentIndex(1)
            else:
                self.output_tabs.setCurrentIndex(2)
        state = _RUN_LABELS.get(run.state, run.state)
        parsed = _PARSE_LABELS.get(run.parse_state, run.parse_state)
        stale_reason = _display_reason(run.stale_reason, "현재 입력과 보존 입력을 확인하세요.")
        stale = f" — 오래된 입력의 결과: {stale_reason}" if run.stale else ""
        error = _display_reason(run.error, "계산 기록을 확인하지 못했습니다. 입력 파일과 원본 로그를 확인하세요.")
        stages = run.snapshot.get("stages", [])
        stage_labels = {**_RUN_LABELS, 'pending': '대기', 'not_started': '미실행'}
        stage_text = " · ".join(f"{item['name'].title()}: {stage_labels.get(item['state'], item['state'])}"
                                for item in stages if item.get("name") in {"starter", "engine"})
        self.run_status.setText(f"실행: {state} / 결과: {parsed}{stale}" + (f"\n{stage_text}" if stage_text else "")
                                + (f"\n{error}" if error else ""))
        self.run_source.setText(f"이 실행의 입력: {_display_file(self.project, run.input_path)}")
        self.run_source.setToolTip("선택한 실행에 보존된 입력 파일을 기준으로 표시합니다.")
        self.log.setToolTip("선택한 실행의 원본 로그 일부 (최근 32 KiB)")
        text = ""
        if run.log_path:
            try:
                with Path(run.log_path).open("rb") as stream:
                    stream.seek(0, 2)
                    size = stream.tell()
                    stream.seek(max(0, size - 32768))
                    text = stream.read().decode("utf-8", errors="replace")
                    if size > 32768:
                        text = "[최근 로그 32 KiB — 전체 로그는 실행 폴더에 보존됨]\n" + text
            except OSError:
                text = "로그를 읽지 못했습니다. 프로젝트 파일 위치와 접근 권한을 확인하세요."
        if changed or text != self.log.toPlainText():
            scroll = self.log.verticalScrollBar()
            position, following = scroll.value(), scroll.value() == scroll.maximum()
            self.log.setPlainText(text)
            scroll.setValue(scroll.maximum() if changed or following else position)
        self._update_log_summary()
        result = run.result or {}
        if changed or result != self._displayed_result:
            previous = self.frame_selector.currentData() if not changed else None
            if preferred_selection is not None and preferred_selection[0] == run.id:
                previous = preferred_selection[1]
            self._displayed_result = result
            self.frame_selector.blockSignals(True)
            self.frame_selector.clear()
            frames = result.get("frames", [])
            is_radioss = result.get("summary", {}).get("solver_id") == "radioss"
            for idx, frame in enumerate(frames):
                raw_idx = frame.get("index")
                num = (raw_idx + 1) if isinstance(raw_idx, int) and not isinstance(raw_idx, bool) else idx + 1
                label = f"프레임 {num}"
                last = idx == len(frames) - 1
                if is_radioss:
                    if (time := frame.get("time")) is not None:
                        time_unit = run.units.get("time", "단위 미확인")
                        label += f" · 시간 {time:.6g} {time_unit}"
                    last = frame.get("is_final", last and result.get("status") == "readable")
                else:
                    label += f" · 단계 {frame.get('step', '?')} / 증분 {frame.get('increment', '?')} / 값 {frame.get('value', '?')}"
                if last:
                    label += " [최종]"
                self.frame_selector.addItem(label, raw_idx)
            default = result.get("summary", {}).get("frame_index")
            index = self.frame_selector.findData(previous if previous is not None else default)
            if index < 0:
                index = self.frame_selector.findData(default)
            self.frame_selector.setCurrentIndex(index if index >= 0 else self.frame_selector.count() - 1)
            self.frame_selector.blockSignals(False)
        self.show_result()

    def show_result(self) -> None:
        run = self._runs.get(self._displayed_run_id)
        if run is None:
            self._clear_result()
            return
        self.frame_selector.setToolTip(self.frame_selector.currentText())
        self.native_view.set_selection(run, self.frame_selector.currentData())
        self.field_table.setRowCount(0)
        self.dat_table.setRowCount(0)
        self.energy_table.setRowCount(0)
        self._update_unit_badges()
        self.kpi_disp_value.setText('미확인')
        result = run.result or {}
        run_summary = result.get("summary", {})
        stress_unit = _effective_stress_unit(run.units)
        frame = next((frame for frame in result.get("frames", [])
                      if frame.get("index") == self.frame_selector.currentData()), None)
        lines = [f"선언 단위: {_unit_text(run.units)}"]
        if result.get("native_files"):
            lines.append("원본 결과: " + ", ".join(result["native_files"]))
        raw_cyc = run_summary.get("total_cycles") if run_summary.get("total_cycles") is not None else run_summary.get("cycle")
        valid_cyc = int(raw_cyc) if isinstance(raw_cyc, (int, float)) and not isinstance(raw_cyc, bool) and math.isfinite(raw_cyc) else None
        if frame:
            summary = frame.get("summary", {})
            value = summary.get("max_displacement")
            self.kpi_disp_value.setText('미확인' if value is None else f"{value:.4g} {run.units.get('length', '(단위 미확인)')}")
            mesh_nodes = summary.get('mesh_node_count')
            peak_stress: float | None = None
            for item in frame.get("fields", []):
                if item.get('name') == 'STRESS':
                    for comp in item.get('summary', {}).get('components', []):
                        for bound in (comp.get('minimum'), comp.get('maximum')):
                            if isinstance(bound, (int, float)) and not isinstance(bound, bool) and math.isfinite(bound):
                                mag = abs(float(bound))
                                peak_stress = mag if peak_stress is None or mag > peak_stress else peak_stress
            if peak_stress is not None and mesh_nodes is not None:
                self.kpi_nodes_value.setText(
                    f"최대 {peak_stress:.4g} {stress_unit or ''} · {mesh_nodes:,} 절점".replace("  ", " ")
                )
            elif mesh_nodes is not None:
                self.kpi_nodes_value.setText(f"{mesh_nodes:,} 절점")
            elif valid_cyc is not None:
                self.kpi_nodes_value.setText(f"사이클 {valid_cyc:,}")
            else:
                self.kpi_nodes_value.setText('미확인')
            label = "판독된 절점 중 최대 변위" if summary.get("displacement_node_count", 0) < summary.get("mesh_node_count", 0) else "최대 변위"
            lines.append("최대 변위: 미확인" if value is None else
                         f"{label}: {value:.8g} {run.units.get('length', '(단위 미확인)')}")
            for item in frame.get("fields", []):
                field_name = item.get('name', '')
                field_label = _FIELD_LABELS.get(field_name, field_name)
                component_labels = _COMPONENT_LABELS.get(field_name, {})
                components = ", ".join(component_labels.get(component['name'], component['name']) for component in item.get("components", [])
                                       if component.get("exist", 0) in (0, 2))
                coverage = item.get("coverage", {})
                if field_name == 'DISP':
                    field_unit = run.units.get('length') or '—'
                elif field_name == 'STRESS':
                    field_unit = stress_unit or '—'
                elif field_name == 'FORC':
                    field_unit = run.units.get('force') or '—'
                elif field_name in {'TOSTRAIN', 'PE'}:
                    field_unit = '-'
                elif field_name == 'TEMP':
                    field_unit = run.units.get('temperature') or '—'
                else:
                    field_unit = '—'
                component_rows = item.get('summary', {}).get('components', [])
                for component in component_rows or [{}]:
                    low, high = component.get('minimum'), component.get('maximum')
                    component_name = component.get('name', components)
                    self._append_row(self.field_table, [field_label, component_labels.get(component_name, component_name),
                                     '?' if low is None else f'{low:.4g}', '?' if high is None else f'{high:.4g}', field_unit])
                lines.append(f"{field_label} ({components}) — 판독 절점 {coverage.get('record_count', '?')} / 메시 절점 {coverage.get('mesh_node_count', '?')}")
                for component in item.get("summary", {}).get("components", []):
                    low, high = component.get("minimum"), component.get("maximum")
                    if low is not None and high is not None:
                        lines.append(f"  {component_labels.get(component['name'], component['name'])}: {low:.8g} ~ {high:.8g}")
            if frame.get("vtu_file"):
                lines.append(f"교환 파일: {Path(frame['vtu_file']).name}")
        else:
            lines.append("판독한 결과 프레임이 없습니다. 원본 출력과 로그를 확인하세요.")
            if valid_cyc is not None:
                self.kpi_nodes_value.setText(f"사이클 {valid_cyc:,}")
            else:
                self.kpi_nodes_value.setText('미확인')

        has_energy = bool(
            run_summary.get("solver_id") == "radioss"
            or "internal_energy" in run_summary
            or "total_cycles" in run_summary
        )
        self.energy_container.setVisible(has_energy)
        if has_energy:
            lines += ["", "OpenRadioss 에너지·사이클 요약:"]
            tot_c = run_summary.get("total_cycles")
            prt_c = run_summary.get("printed_cycle", run_summary.get("cycle"))
            prt_t = run_summary.get("printed_time", run_summary.get("time"))
            time_unit = run.units.get("time", "s")

            if tot_c is not None:
                if prt_c is not None and prt_c != tot_c:
                    lines.append(f"  총 사이클 수: {int(tot_c)} (마지막 출력 사이클: {int(prt_c)})")
                    self._append_row(self.energy_table, ["총 사이클 수", f"{int(tot_c):,}", f"출력 사이클 {int(prt_c):,}"])
                else:
                    lines.append(f"  총 사이클 수: {int(tot_c)}")
                    self._append_row(self.energy_table, ["총 사이클 수", f"{int(tot_c):,}", "정상"])
            elif prt_c is not None:
                lines.append(f"  마지막 출력 사이클: {int(prt_c)}")
                self._append_row(self.energy_table, ["마지막 출력 사이클", f"{int(prt_c):,}", "종료 푸터 미확인"])

            if prt_t is not None:
                if tot_c is not None and prt_c is not None and tot_c == prt_c:
                    lines.append(f"  최종 시간: {prt_t:.6g} {time_unit}")
                    self._append_row(self.energy_table, ["최종 시간", f"{prt_t:.6g} {time_unit}", "해석 종료 시점"])
                else:
                    lines.append(f"  마지막 출력 시간: {prt_t:.6g} {time_unit}")
                    self._append_row(self.energy_table, ["마지막 출력 시간", f"{prt_t:.6g} {time_unit}", "중간 출력 시점"])
            elif "time" in run_summary and run_summary["time"] is not None:
                lines.append(f"  마지막 출력 시간: {run_summary['time']:.6g} {time_unit}")
                self._append_row(self.energy_table, ["마지막 출력 시간", f"{run_summary['time']:.6g} {time_unit}", "중간 출력 시점"])

            if "internal_energy" in run_summary and run_summary["internal_energy"] is not None:
                lines.append(f"  내부 에너지 (I-ENERGY): {run_summary['internal_energy']:.6g}")
                self._append_row(self.energy_table, ["내부 에너지 (IE)", f"{run_summary['internal_energy']:.6g}", "변형 에너지"])
            if "kinetic_energy" in run_summary and run_summary["kinetic_energy"] is not None:
                lines.append(f"  운동 에너지 (K-ENERGY): {run_summary['kinetic_energy']:.6g}")
                self._append_row(self.energy_table, ["운동 에너지 (KE)", f"{run_summary['kinetic_energy']:.6g}", "관성 에너지"])
            if "external_work" in run_summary and run_summary["external_work"] is not None:
                self._append_row(self.energy_table, ["외부 일 (Ext. Work)", f"{run_summary['external_work']:.6g}", "외력 투입 일"])
            if "hourglass_ratio" in run_summary and run_summary["hourglass_ratio"] is not None:
                hg = run_summary["hourglass_ratio"]
                lines.append(f"  아워글래스 비: {hg:.4f}")
                self._append_row(
                    self.energy_table,
                    ["아워글래스 비 (HG / IE)", f"{hg * 100:.2f}% ({hg:.4f})", "양호 (< 5%)" if hg < 0.05 else "주의 (≥ 5%)"],
                )
            if "energy_error" in run_summary and run_summary["energy_error"] is not None:
                err = run_summary["energy_error"]
                lines.append(f"  에너지 오차율: {err:.2f}%")
                self._append_row(
                    self.energy_table,
                    ["에너지 오차율", f"{err:+.2f}%", "양호 (±5% 이내)" if abs(err) <= 5.0 else "주의 (오차 확대)"],
                )
            if "mass_error" in run_summary and run_summary["mass_error"] is not None:
                m_err = run_summary["mass_error"]
                lines.append(f"  질량 오차율: {m_err:.6g}")
                self._append_row(
                    self.energy_table,
                    ["질량 오차율", f"{m_err:.6g}", "양호" if abs(m_err) <= 0.02 else "질량 스케일링 확인"],
                )

        dat = result.get("dat") or {}
        dat_status = dat.get('status')
        dat_sets = dat.get('sets') or {}
        force_unit = run.units.get('force', '(단위 미확인)')
        length_unit = run.units.get('length', '(단위 미확인)')
        force_vectors: list[tuple[float, ...]] = []
        for name, values in dat_sets.items():
            if 'total_force' in values and values['total_force'] is not None:
                vec = tuple(float(x) for x in values['total_force'])
                force_vectors.append(vec)
                count_val = len(values['nodal_forces']) if 'nodal_forces' in values else '—'
                self._append_row(
                    self.dat_table,
                    [name, '합력 (Fx, Fy, Fz)', count_val, ', '.join(f'{x:.4g}' for x in vec), force_unit],
                )
            elif 'nodal_forces' in values and values['nodal_forces']:
                vec = tuple(float(sum(col)) for col in zip(*values['nodal_forces'].values()))
                force_vectors.append(vec)
                self._append_row(
                    self.dat_table,
                    [name, '합력 (Fx, Fy, Fz)', len(values['nodal_forces']), ', '.join(f'{x:.4g}' for x in vec), force_unit],
                )

            if 'total_displacement' in values and values['total_displacement'] is not None:
                uvec = tuple(float(x) for x in values['total_displacement'])
                count_val = len(values['displacements']) if 'displacements' in values else '—'
                self._append_row(
                    self.dat_table,
                    [name, '변위 합계 (Ux, Uy, Uz)', count_val, ', '.join(f'{x:.4g}' for x in uvec), length_unit],
                )
            elif 'displacements' in values and values['displacements']:
                max_u = max(math.hypot(*(float(c) for c in row)) for row in values['displacements'].values())
                self._append_row(
                    self.dat_table,
                    [name, '최대 변위 |U|max', len(values['displacements']), f'{max_u:.4g}', length_unit],
                )

        same_dim = len(force_vectors) >= 2 and len({len(v) for v in force_vectors}) == 1 and len(force_vectors[0]) > 0
        net: tuple[float, ...] = ()
        if same_dim:
            net = tuple(sum(col) for col in zip(*force_vectors))
            self._append_row(
                self.dat_table,
                ['전체 합계 (∑F)', '평형 검산', '—', ', '.join(f'{x:.4g}' for x in net), force_unit],
            )

        self.dat_container.setVisible(bool(dat_sets))
        metadata = dat.get('frame') or {}
        frame_text = ' / '.join(f'{label} {metadata[key]}' for key, label in
                                (('step', '단계'), ('increment', '증분'), ('value', '값'))
                                if metadata.get(key) is not None)
        self.dat_frame_label.setText('최종 프레임' + (f' · {frame_text}' if frame_text else ''))
        dat_text_header = 'DAT 최종 프레임' + (f' · {frame_text}' if frame_text else '')

        if len(force_vectors) >= 2 and same_dim:
            ref_mag = max((math.hypot(*v) for v in force_vectors), default=0.0)
            net_mag = math.hypot(*net)
            if math.isfinite(ref_mag) and math.isfinite(net_mag) and ref_mag > 0 and net_mag <= 0.02 * ref_mag:
                self.kpi_dat_value.setText('평형 확인 (∑F ≈ 0)')
            else:
                self.kpi_dat_value.setText(f'{len(force_vectors)}개 집합 합력')
        elif len(force_vectors) >= 2:
            self.kpi_dat_value.setText(f'{len(force_vectors)}개 집합 합력')
        elif dat_sets:
            self.kpi_dat_value.setText(f'{len(dat_sets)}개 집합 확인')
        elif run_summary.get("energy_error") is not None:
            hg = run_summary.get("hourglass_ratio")
            hg_txt = f" · HG {hg * 100:.1f}%" if isinstance(hg, (int, float)) and not isinstance(hg, bool) and math.isfinite(hg) else ""
            self.kpi_dat_value.setText(f"오차 {run_summary['energy_error']:.1f}%{hg_txt}")
        else:
            self.kpi_dat_value.setText(_PARSE_LABELS.get(dat_status, dat_status or '출력 없음'))

        if dat and dat.get("status") != "missing":
            lines += ["", f"DAT 집합 결과: {_PARSE_LABELS.get(dat.get('status'), dat.get('status'))}"]
            if frame_text:
                lines.append(dat_text_header)
            value = dat.get("max_displacement")
            if value is not None:
                lines.append(f"DAT 판독 절점 중 최대 변위: {value:.8g} {run.units.get('length', '(단위 미확인)')}")
            for name, values in dat_sets.items():
                lines.append(f"집합 {name}")
                for key, label in (("displacements", "변위"), ("nodal_forces", "절점력 원값")):
                    if key in values:
                        lines.append(f"  {label}: {len(values[key])}개 절점")
                for key, label, unit_key in (("total_force", "절점력 합계 원값", "force"),
                                             ("total_displacement", "변위 합계 원값", "length")):
                    if key in values:
                        numbers = ", ".join(f"{item:.8g}" for item in values[key])
                        lines.append(f"  {label}: ({numbers}) {run.units.get(unit_key, '(단위 미확인)')}")
            if same_dim:
                net_numbers = ", ".join(f"{item:.8g}" for item in net)
                lines.append(f"전체 합계 (∑F): ({net_numbers}) {force_unit}")
            lines.extend(_display_reason(message, "DAT 원본 출력과 판독 상태를 확인하세요.") for message in dat.get("warnings", []))
        lines.extend(_display_reason(message, "원본 출력과 판독 상태를 확인하세요.") for message in result.get("warnings", []))

        all_warnings = [
            w for w in (
                *(_display_reason(m, "DAT 원본 출력과 판독 상태를 확인하세요.") for m in dat.get("warnings", [])),
                *(_display_reason(m, "원본 출력과 판독 상태를 확인하세요.") for m in result.get("warnings", [])),
            )
            if w
        ]
        self.warnings_label.setText("\n".join(all_warnings))
        self.warnings_label.setVisible(bool(all_warnings))

        text = "\n".join(lines)
        if self.result_text.toPlainText() != text:
            self.result_text.setPlainText(text)

    def closeEvent(self, event) -> None:  # noqa: N802
        for window in list(self._expert_windows.values()):
            if not window.close():
                event.ignore()
                return
        if self.workbench.close() is False:
            self.run_status.setText("소유한 계산을 종료하고 있습니다. 종료 후 다시 닫으세요.")
            event.ignore()
            return
        self.timer.stop()
        self.native_view.stop()
        event.accept()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="선택한 네이티브 CalculiX 입력 확인")
    parser.add_argument("project", help="프로젝트 JSON 파일")
    parser.add_argument("--node", required=True, help="프로젝트의 해석 상자 식별자")
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    try:
        window = NativeAnalysisWindow(args.project, args.node)
    except (OSError, ValueError, KeyError) as error:
        QMessageBox.critical(None, "입력 확인창을 열지 못했습니다", _display_reason(error, "프로젝트 파일과 해석 입력을 확인한 뒤 다시 여세요."))
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
