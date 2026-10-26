"""Independent native CalculiX input, execution and recorded results."""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFrame, QHeaderView, QHBoxLayout, QLabel,
                              QMainWindow, QMessageBox, QPlainTextEdit, QSpinBox,
                              QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QToolBar,
                              QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt, QTimer

from caelab.workbench import Workbench
from .theme import (MONO_FONT_STACK, build_stylesheet, current_theme_mode, get_palette,
                    register_theme_listener, status_badge_style, theme_toggle_label, toggle_theme_mode)
from .expert_chat import source_name

_STATE_LABELS = {"needs_input": "입력 필요", "ready": "입력 준비",
                 "changed": "원본 변경됨", "missing": "파일 누락", "stale": "오래됨",
                 "opened": "프로그램 열림", "failed": "실패"}
_UNIT_LABELS = {"length": "길이", "force": "힘", "time": "시간", "temperature": "온도", "stress": "응력"}
_RUN_LABELS = {"queued": "대기", "preparing": "입력 보존 중", "running": "계산 중",
               "completed": "프로세스 정상 종료", "failed": "실패", "cancelled": "취소됨",
               "interrupted": "중단됨"}
_PARSE_LABELS = {"pending": "대기", "unparsed": "미판독", "reading": "판독 중",
                 "readable": "판독 완료", "partial": "일부 판독",
                 "unsupported": "미지원 출력 형식", "missing": "출력 없음", "failed": "판독 실패"}
_ACTIVE_STATES = {"queued", "preparing", "running"}


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
        if re.match(r'^\*\*', text):
            self._format(0, len(text), p['syn_comment'])
            return
        keyword = re.match(r'^\*[A-Za-z][A-Za-z0-9 _-]*', text)
        if keyword:
            section = re.match(r'^\*(?:STEP|END\s+STEP|STATIC|DYNAMIC|FREQUENCY|BUCKLE|HEAT\s+TRANSFER|COUPLED\s+TEMPERATURE-DISPLACEMENT|VISCO|MODAL\s+DYNAMIC)\b', text, re.I)
            self._format(0, keyword.end(), p['syn_section'] if section else p['syn_keyword'], True)
            for match in re.finditer(r',?\s*([A-Za-z_][A-Za-z0-9_]*)\s*=', text):
                self._format(match.start(1), len(match[1]), p['syn_param'])

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
                (r'\bSTEP\s+\d+|\bINCREMENT\s+\d+|iteration\s+\d+|Job finished', 'syn_keyword', False),
                (r'\*WARNING.*|\bWARNING\b|cutback', 'syn_warning', True),
                (r'\*ERROR.*|\bERROR\b|divergence|not convergent', 'syn_error', True)):
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
        self._displayed_result = None
        node = self.project.nodes[node_id]
        from caelab.native.adapter import supported_solver_ids
        if node.kind != "analysis" and node.program_id not in supported_solver_ids():
            raise ValueError("해석 입력 확인 대상이 아닙니다.")
        program_name = {"calculix": "CalculiX", "codeaster": "Code_Aster", "radioss": "OpenRadioss",
                        "openradioss": "OpenRadioss", "opencourant": "OpenCourant", "lsdyna": "LS-DYNA"}.get(node.program_id, node.program_id.capitalize())
        self.setWindowTitle(f"{self.project.name} — {program_name} 해석")
        self.resize(1050, 760)
        self.view_menu = self.menuBar().addMenu("보기(&V)")
        self.theme_action = self.view_menu.addAction(theme_toggle_label(), toggle_theme_mode)
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
        container = QWidget()
        layout = QVBoxLayout(container)
        header_card = QFrame()
        header_card.setObjectName("sectionCard")
        header = QVBoxLayout(header_card)
        header.setContentsMargins(12, 10, 12, 10)
        header.setSpacing(6)
        status_line = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        status_line.addWidget(self.status, 1)
        self.unit_badge = QLabel()
        status_line.addWidget(self.unit_badge)
        header.addLayout(status_line)
        self.input_heading = QLabel()
        self.input_heading.setWordWrap(True)
        self.input_heading.setTextInteractionFlags(Qt.TextSelectableByMouse)
        header.addWidget(self.input_heading)
        self.active_status = QLabel()
        self.active_status.setWordWrap(True)
        header.addWidget(self.active_status)
        layout.addWidget(header_card)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.source = DeckCodeEditor()
        self.source.setPlaceholderText("선택한 네이티브 입력 파일의 원문을 표시합니다.")
        input_tabs = QTabWidget()
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
        deck_split.setSizes([240, 760])
        input_tabs.addTab(deck_split, "입력 원문")
        input_tabs.addTab(self.details, "파일 정보")
        run_panel = QWidget()
        run_layout = QVBoxLayout(run_panel)
        run_layout.setContentsMargins(0, 0, 0, 0)
        selection = QHBoxLayout()
        selection.addWidget(QLabel("조회할 실행"))
        self.run_selector = QComboBox()
        self.run_selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.run_selector.setMinimumContentsLength(20)
        self.run_selector.currentIndexChanged.connect(self.select_run)
        selection.addWidget(self.run_selector, 1)
        run_layout.addLayout(selection)
        self.run_status = QLabel("아직 실행 기록이 없습니다.")
        self.run_status.setWordWrap(True)
        run_layout.addWidget(self.run_status)
        self.run_source = QLabel()
        self.run_source.setWordWrap(True)
        self.run_source.setTextInteractionFlags(Qt.TextSelectableByMouse)
        run_layout.addWidget(self.run_source)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("monoView")
        self.log_highlighter = SolverLogHighlighter(self.log.document())
        self.log.setPlaceholderText("선택한 실행의 원본 로그를 표시합니다.")
        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        self.log_summary_badge = QLabel()
        log_layout.addWidget(self.log_summary_badge)
        log_layout.addWidget(self.log)
        result_panel = QWidget()
        result_layout = QVBoxLayout(result_panel)
        result_layout.setContentsMargins(0, 0, 0, 0)
        self.frame_selector = QComboBox()
        self.frame_selector.currentIndexChanged.connect(self.show_result)
        frame_line = QHBoxLayout()
        frame_line.addWidget(QLabel("프레임"))
        frame_line.addWidget(self.frame_selector, 1)
        self.result_unit_badge = QLabel()
        frame_line.addWidget(self.result_unit_badge)
        self.result_verify_badge = QLabel("공학 검증 미실시")
        frame_line.addWidget(self.result_verify_badge)
        result_layout.addLayout(frame_line)
        kpis = QHBoxLayout()
        for title, attr in (("최대 변위 (판독 절점)", "kpi_disp_value"),
                            ("변위 절점 / 메시 절점", "kpi_nodes_value"), ("DAT 판독 상태", "kpi_dat_value")):
            card = QFrame()
            card.setObjectName("kpiCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 5, 10, 5)
            card_layout.setSpacing(2)
            card_layout.addWidget(QLabel(title))
            value = QLabel("미확인")
            setattr(self, attr, value)
            card_layout.addWidget(value)
            kpis.addWidget(card, 1)
        result_layout.addLayout(kpis)
        tables = QWidget()
        tables_layout = QVBoxLayout(tables)
        tables_layout.setContentsMargins(0, 0, 0, 0)
        tables_layout.setSpacing(4)
        self.field_table = self._result_table(["필드", "성분", "최솟값", "최댓값", "판독/메시 절점"])
        self.field_table.setMinimumHeight(115)
        tables_layout.addWidget(self.field_table, 1)
        self.dat_container = QWidget()
        dat_layout = QVBoxLayout(self.dat_container)
        dat_layout.setContentsMargins(0, 0, 0, 0)
        dat_layout.setSpacing(2)
        dat_layout.addWidget(QLabel("DAT 마지막 판독 프레임의 집합 원값 (별도 파일)"))
        self.dat_frame_label = QLabel()
        self.dat_frame_label.setWordWrap(True)
        dat_layout.addWidget(self.dat_frame_label)
        self.dat_table = self._result_table(["집합 (NSET)", "종류", "절점 수", "원 합계 성분 (1, 2, 3)", "단위"])
        self.dat_table.setMinimumHeight(54)
        self.dat_table.setMaximumHeight(62)
        dat_layout.addWidget(self.dat_table)
        tables_layout.addWidget(self.dat_container, 0)
        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMinimumHeight(96)
        result_split = QSplitter(Qt.Vertical)
        result_split.addWidget(tables)
        result_split.addWidget(self.result_text)
        result_split.setSizes([355, 110])
        result_layout.addWidget(result_split)
        output_tabs = QTabWidget()
        output_tabs.addTab(log_panel, "로그")
        output_tabs.addTab(result_panel, "결과")
        run_layout.addWidget(output_tabs)
        split = QSplitter(Qt.Vertical)
        split.addWidget(input_tabs)
        split.addWidget(run_panel)
        split.setSizes([155, 555])
        layout.addWidget(split)
        note = QLabel("입력 편집은 전용 프로그램에서 합니다. 실행 기록을 선택해도 새 계산은 시작되지 않습니다.")
        note.setWordWrap(True)
        layout.addWidget(note)
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

    def apply_theme(self, mode: str | None = None) -> None:
        resolved = mode if mode in ("dark", "light") else current_theme_mode()
        self._theme_mode = resolved
        self.setStyleSheet(build_stylesheet(resolved))
        self.theme_action.setText(theme_toggle_label(resolved))
        self.active_status.setStyleSheet(status_badge_style("running", resolved))
        self.result_verify_badge.setStyleSheet(status_badge_style("neutral", resolved))
        self.source.highlighter.theme_mode = resolved
        self.source.highlighter.rehighlight()
        self.source.gutter.update()
        self.log_highlighter.theme_mode = resolved
        self.log_highlighter.rehighlight()
        self._update_unit_badges()
        self._update_log_summary()

    def _update_unit_badges(self):
        mode = getattr(self, '_theme_mode', None)
        reference = self.workbench.input_file(self.project, self.node_id)
        for badge, units, exists in ((self.unit_badge, reference.units if reference else {}, reference is not None),
                                     (self.result_unit_badge, getattr(self._runs.get(self._displayed_run_id), 'units', {}),
                                      self._displayed_run_id in self._runs)):
            tone = 'ready' if units else 'warning' if exists else 'neutral'
            badge.setText('단위 선언됨' if units else '단위 미확인' if exists else '자료 없음')
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
        table.setMinimumHeight(80)
        return table

    @staticmethod
    def _append_row(table, values):
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(str(value)))

    def _clear_result(self):
        self.result_text.clear()
        self.field_table.setRowCount(0)
        self.dat_table.setRowCount(0)
        self.dat_container.hide()
        self.dat_frame_label.clear()
        for value in (self.kpi_disp_value, self.kpi_nodes_value, self.kpi_dat_value):
            value.setText('미확인')
        self.result_unit_badge.setText('자료 없음')
        self.result_unit_badge.setStyleSheet(status_badge_style('neutral', getattr(self, '_theme_mode', None)))

    def _update_log_summary(self):
        text = self.log.toPlainText()
        errors = len(re.findall(r'\*ERROR\b', text, re.I))
        warnings = len(re.findall(r'\*WARNING\b', text, re.I))
        self.log_summary_badge.setText(f'최근 로그: 오류 {errors} / 경고 {warnings}')
        self.log_summary_badge.setStyleSheet(
            status_badge_style('error' if errors else 'warning' if warnings else 'neutral', getattr(self, '_theme_mode', None))
        )

    @staticmethod
    def _deck_category(keyword):
        if keyword in {'STEP', 'END STEP', 'STATIC', 'DYNAMIC', 'FREQUENCY', 'BUCKLE', 'HEAT TRANSFER',
                       'COUPLED TEMPERATURE-DISPLACEMENT', 'VISCO', 'MODAL DYNAMIC'}:
            return 'step'
        if keyword in {'MATERIAL', 'ELASTIC', 'PLASTIC', 'DENSITY', 'EXPANSION', 'CONDUCTIVITY', 'SPECIFIC HEAT'}:
            return 'material'
        if keyword in {'BOUNDARY', 'CLOAD', 'DLOAD', 'DSLOAD', 'TEMPERATURE', 'CFLUX', 'DFLUX', 'FILM', 'RADIATE'}:
            return 'bc_load'
        if any(word in keyword for word in ('CONTACT', 'FRICTION', 'SURFACE', 'TIE')):
            return 'contact'
        if keyword in {'NODE', 'ELEMENT', 'NSET', 'ELSET', 'SOLID SECTION', 'SHELL SECTION', 'BEAM SECTION'}:
            return 'mesh'
        return 'other'

    def _rebuild_deck_outline(self):
        self.deck_outline.clear()
        step = None
        for line_no, line in enumerate(self.source.toPlainText().splitlines(), 1):
            text = line.lstrip()
            if not text.startswith('*') or text.startswith('**'):
                continue
            keyword = text.split(',', 1)[0].strip().upper().lstrip('*')
            name = re.search(r'\b(?:NAME|ELSET|NSET|MATERIAL)\s*=\s*([^,]+)', text, re.I)
            title = f'*{keyword}' + (f' ({name[1].strip()})' if name else '')
            parent = step if step is not None and keyword != 'STEP' else self.deck_outline
            item = QTreeWidgetItem(parent, [title, str(line_no)])
            item.setData(0, Qt.UserRole, line_no)
            item.setData(0, Qt.UserRole + 1, self._deck_category(keyword))
            if keyword == 'STEP':
                step = item
            elif keyword == 'END STEP':
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
            unit_source = {"user": "사용자 선언", "unknown": "미확인"}.get(reference.unit_source, reference.unit_source)
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
            self.refresh_input()
            self.refresh_runs()
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

    def refresh_runs(self) -> None:
        try:
            runs = self.workbench.native_runs(self.project, self.node_id)
            selected = self.workbench.selected_native_run(self.project, self.node_id)
        except (OSError, ValueError, RuntimeError):
            self.run_status.setText("실행 기록을 읽지 못했습니다. 프로젝트 파일과 접근 권한을 확인하세요.")
            self.log.clear()
            self.run_source.clear()
            self.frame_selector.clear()
            self._displayed_run_id = None
            self._displayed_result = None
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
            self.run_status.setText("아직 선택한 실행이 없습니다.")
            self.run_source.clear()
            self.log.clear()
            self.frame_selector.clear()
            self._displayed_run_id = None
            self._displayed_result = None
            self._clear_result()
            self._update_log_summary()
            return
        self._show_run(selected)

    def select_run(self) -> None:
        run_id = self.run_selector.currentData()
        if run_id is None:
            return
        try:
            self.workbench.select_native_run(self.project, self.node_id, run_id)
            self.refresh_runs()
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "실행을 선택하지 못했습니다", _display_reason(error, "프로젝트를 다시 읽은 뒤 실행 기록을 선택하세요."))

    def _show_run(self, run) -> None:
        changed = run.id != self._displayed_run_id
        self._displayed_run_id = run.id
        state = _RUN_LABELS.get(run.state, run.state)
        parsed = _PARSE_LABELS.get(run.parse_state, run.parse_state)
        stale_reason = _display_reason(run.stale_reason, "현재 입력과 보존 입력을 확인하세요.")
        stale = f" — 오래된 입력의 결과: {stale_reason}" if run.stale else ""
        error = _display_reason(run.error, "계산 기록을 확인하지 못했습니다. 입력 파일과 원본 로그를 확인하세요.")
        self.run_status.setText(f"실행: {state} / 결과: {parsed}{stale}" + (f"\n{error}" if error else ""))
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
            self._displayed_result = result
            self.frame_selector.blockSignals(True)
            self.frame_selector.clear()
            frames = result.get("frames", [])
            for idx, frame in enumerate(frames):
                label = f"단계 {frame.get('step', '?')} / 증분 {frame.get('increment', '?')} / 값 {frame.get('value', '?')}{' [최종]' if idx == len(frames) - 1 else ''}"
                self.frame_selector.addItem(label, frame.get("index"))
            default = result.get("summary", {}).get("frame_index")
            index = self.frame_selector.findData(previous if previous is not None else default)
            self.frame_selector.setCurrentIndex(index if index >= 0 else self.frame_selector.count() - 1)
            self.frame_selector.blockSignals(False)
        self.show_result()

    def show_result(self) -> None:
        run = self._runs.get(self._displayed_run_id)
        if run is None:
            self._clear_result()
            return
        self.field_table.setRowCount(0)
        self.dat_table.setRowCount(0)
        self._update_unit_badges()
        self.kpi_disp_value.setText('미확인')
        self.kpi_nodes_value.setText('? / ?')
        result = run.result or {}
        frame = next((frame for frame in result.get("frames", [])
                      if frame.get("index") == self.frame_selector.currentData()), None)
        lines = [f"선언 단위: {_unit_text(run.units)}", "수치 검증·공학적 적합성: 미실시"]
        if frame:
            summary = frame.get("summary", {})
            value = summary.get("max_displacement")
            self.kpi_disp_value.setText('미확인' if value is None else f"{value:.4g} {run.units.get('length', '(단위 미확인)')}")
            disp_nodes, mesh_nodes = summary.get('displacement_node_count'), summary.get('mesh_node_count')
            self.kpi_nodes_value.setText(f"{disp_nodes:,} / {mesh_nodes:,}" if disp_nodes is not None and mesh_nodes is not None else '? / ?')
            label = "판독된 절점 중 최대 변위" if summary.get("displacement_node_count", 0) < summary.get("mesh_node_count", 0) else "최대 변위"
            lines.append("최대 변위: 미확인" if value is None else
                         f"{label}: {value:.8g} {run.units.get('length', '(단위 미확인)')}")
            for item in frame.get("fields", []):
                components = ", ".join(component["name"] for component in item.get("components", [])
                                       if component.get("exist", 0) in (0, 2))
                coverage = item.get("coverage", {})
                coverage_text = f"{coverage.get('record_count', '?')} / {coverage.get('mesh_node_count', '?')}"
                component_rows = item.get('summary', {}).get('components', [])
                for component in component_rows or [{}]:
                    low, high = component.get('minimum'), component.get('maximum')
                    self._append_row(self.field_table, [item.get('name', ''), component.get('name', components),
                                     '?' if low is None else f'{low:.4g}', '?' if high is None else f'{high:.4g}', coverage_text])
                lines.append(f"{item['name']} ({components}) — 판독 절점 {coverage.get('record_count', '?')} / 메시 절점 {coverage.get('mesh_node_count', '?')}")
                for component in item.get("summary", {}).get("components", []):
                    low, high = component.get("minimum"), component.get("maximum")
                    if low is not None and high is not None:
                        lines.append(f"  {component['name']}: {low:.8g} ~ {high:.8g}")
            if frame.get("vtu_file"):
                lines.append(f"교환 파일: {Path(frame['vtu_file']).name}")
        else:
            lines.append("판독한 결과 프레임이 없습니다. 원본 출력과 로그를 확인하세요.")
        dat = result.get("dat", {})
        self.kpi_dat_value.setText(_PARSE_LABELS.get(dat.get('status'), dat.get('status') or '출력 없음'))
        self.dat_container.setVisible(bool(dat.get('sets')))
        metadata = dat.get('frame') or {}
        self.dat_frame_label.setText(f"DAT 최종 프레임 — 단계 {metadata.get('step', '?')} / 증분 {metadata.get('increment', '?')} / 값 {metadata.get('value', '?')}\n선택한 FRD 프레임과 대응 여부 미확인")
        for name, values in dat.get('sets', {}).items():
            for key, label, unit_key, records in (
                    ('total_force', '절점력 합계 원값', 'force', 'nodal_forces'),
                    ('total_displacement', '변위 합계 원값', 'length', 'displacements')):
                if key in values:
                    self._append_row(self.dat_table, [name, label, len(values[records]) if records in values else '—',
                                     ', '.join(f'{x:.4g}' for x in values[key]), run.units.get(unit_key, '(단위 미확인)')])
            for records, label, unit_key in (('displacements', '변위', 'length'), ('nodal_forces', '절점력 원값', 'force')):
                if records in values:
                    self._append_row(self.dat_table, [name, label, len(values[records]), '집합별 원 절점값', run.units.get(unit_key, '(단위 미확인)')])
        if dat and dat.get("status") != "missing":
            lines += ["", f"DAT 집합 결과: {_PARSE_LABELS.get(dat.get('status'), dat.get('status'))}"]
            metadata = dat.get("frame") or {}
            if metadata:
                lines.append(f"DAT 최종 프레임 — 단계 {metadata.get('step')} / 증분 {metadata.get('increment')} / 값 {metadata.get('value')}")
            value = dat.get("max_displacement")
            if value is not None:
                lines.append(f"DAT 판독 절점 중 최대 변위: {value:.8g} {run.units.get('length', '(단위 미확인)')}")
            for name, values in dat.get("sets", {}).items():
                lines.append(f"집합 {name}")
                for key, label in (("displacements", "변위"), ("nodal_forces", "절점력 원값")):
                    if key in values:
                        lines.append(f"  {label}: {len(values[key])}개 절점")
                for key, label, unit_key in (("total_force", "절점력 합계 원값", "force"),
                                             ("total_displacement", "변위 합계 원값", "length")):
                    if key in values:
                        numbers = ", ".join(f"{item:.8g}" for item in values[key])
                        lines.append(f"  {label}: ({numbers}) {run.units.get(unit_key, '(단위 미확인)')}")
            lines.extend(_display_reason(message, "DAT 원본 출력과 판독 상태를 확인하세요.") for message in dat.get("warnings", []))
        lines.extend(_display_reason(message, "원본 출력과 판독 상태를 확인하세요.") for message in result.get("warnings", []))
        text = "\n".join(lines)
        if self.result_text.toPlainText() != text:
            self.result_text.setPlainText(text)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.workbench.close() is False:
            self.run_status.setText("소유한 계산을 종료하고 있습니다. 종료 후 다시 닫으세요.")
            event.ignore()
            return
        self.timer.stop()
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
