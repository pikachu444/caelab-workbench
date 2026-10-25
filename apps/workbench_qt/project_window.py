"""Project window — the parent screen, modelled on ANSYS Workbench.

The workbench is a launcher/integration shell: independent systems
(geometry in FreeCAD, meshing, a solver run, results in ParaView, an
expert discussion) appear as boxes on a project schematic. Boxes are
connected through selected native files. Double-clicking a box opens
its independent program. The CalculiX box opens a separate input and
execution window; recorded runs can be inspected from this project.
"""

from __future__ import annotations

from html import escape
from os.path import relpath
from pathlib import Path

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QKeySequence, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDockWidget,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from caelab.workbench import Workbench

from .icons import icon
from .theme import THEME

_KIND_ICONS = {"geometry": "geometry", "mesh": "mesh", "analysis": "solver",
               "results": "result", "expert": "expert"}

SYSTEM_KINDS = [
    ("geometry", "형상", "FreeCAD에서 열기"),
    ("mesh", "메시", "메시 보기"),
    ("analysis", "CalculiX", "네이티브 계산"),
    ("results", "결과", "ParaView에서 열기"),
    ("expert", "전문가 논의", "별도 대화창에서 열기"),
]
STATE_COLORS = {
    "unavailable": "#c9ced4",
    "needs_input": "#c9ced4",
    "ready": "#7fb2e5",
    "running": "#f2b134",
    "done": "#3d9d63",
    "failed": "#d64545",
    "changed": "#c28422",
    "missing": "#d64545",
    "stale": "#c28422",
    "opened": "#7fb2e5",
    "queued": "#f2b134", "preparing": "#f2b134", "completed": "#7fb2e5",
    "cancelled": "#c28422", "interrupted": "#c28422",
}
STATE_LABELS = {
    "unavailable": "연결 미구현",
    "needs_input": "입력 필요",
    "ready": "준비됨",
    "running": "실행 중",
    "done": "최신",
    "failed": "실패",
    "changed": "원본 변경됨",
    "missing": "파일 누락",
    "stale": "오래됨",
    "opened": "열기 요청 전달",
    "queued": "대기", "preparing": "입력 보존 중", "completed": "프로세스 종료",
    "cancelled": "취소됨", "interrupted": "중단됨",
}
BOX_W, BOX_H = 210, 98


def _selection_changed_input(node, reference) -> bool:
    return (node.kind in {"results", "expert"} and node.state == "stale"
            and node.reason == "선택 실행 또는 결과 출처가 변경되었습니다."
            and reference is not None and not reference.stale)


def _box_font(size: int, bold: bool = False) -> QFont:
    """Pick a font whose metrics are reliable for mixed Korean/Latin
    text in graphics items (the empty default family garbles it)."""
    from PySide6.QtGui import QFontDatabase

    available = set(QFontDatabase.families())
    for family in ("Noto Sans CJK KR", "Malgun Gothic", "Noto Sans", "Sans Serif"):
        if family in available:
            font = QFont(family, size)
            font.setBold(bold)
            return font
    font = QFont()
    font.setPointSize(size)
    font.setBold(bold)
    return font


class SystemBox(QGraphicsItem):
    def __init__(self, kind: str, title: str, subtitle: str) -> None:
        super().__init__()
        self.kind = kind
        self.node_id = ""
        self.title = title
        self.subtitle = subtitle
        self.state = "needs_input"
        self.status_label = ""
        self.detail = ""
        self.file_label = "선택 파일 없음"
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self._on_move: list = []

    def boundingRect(self):
        from PySide6.QtCore import QRectF

        return QRectF(0, 0, BOX_W, BOX_H)

    def set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        self.status_label = ""
        self.detail = detail
        self.update()

    def paint(self, painter, option, widget=None) -> None:
        from PySide6.QtCore import QRectF

        rect = self.boundingRect()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        # Selection is shown by the border and a light tint only: a
        # dark fill would bury the dark title/status text drawn below.
        painter.setBrush(QBrush(QColor("#dcebf7") if self.isSelected() else QColor("#fdfdfe")))
        painter.setPen(QPen(QColor("#1668b4") if self.isSelected() else QColor("#8a97a5"),
                            2.2 if self.isSelected() else 1.6))
        painter.drawRoundedRect(rect, 8, 8)
        # header band
        painter.setBrush(QBrush(QColor("#e8eef4")))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(1, 1, BOX_W - 2, 24), 7, 7)
        painter.setPen(QPen(QColor("#10243a")))
        painter.setFont(_box_font(10, bold=True))
        painter.drawText(QRectF(10, 3, BOX_W - 40, 20), Qt.AlignLeft,
                         painter.fontMetrics().elidedText(self.title, Qt.ElideRight, BOX_W - 40))
        # state dot
        painter.setBrush(QBrush(QColor(STATE_COLORS.get(self.state, "#c9ced4"))))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(BOX_W - 24, 7, 11, 11))
        painter.setPen(QPen(QColor("#33414f")))
        painter.setFont(_box_font(9))
        painter.drawText(QRectF(10, 30, BOX_W - 20, 18), Qt.AlignLeft,
                         painter.fontMetrics().elidedText(self.subtitle, Qt.ElideRight, BOX_W - 20))
        painter.drawText(QRectF(10, 50, BOX_W - 20, 18), Qt.AlignLeft,
                         painter.fontMetrics().elidedText(self.file_label, Qt.ElideMiddle, BOX_W - 20))
        painter.setPen(QPen(QColor("#5a6672")))
        status = self.status_label or STATE_LABELS.get(self.state, "상태 미확인")
        painter.drawText(QRectF(10, 73, BOX_W - 20, 18), Qt.AlignLeft, status)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for callback in self._on_move:
                callback()
        return super().itemChange(change, value)

    def right_anchor(self) -> QPointF:
        return self.pos() + QPointF(BOX_W, BOX_H / 2)

    def left_anchor(self) -> QPointF:
        return self.pos() + QPointF(0, BOX_H / 2)

    def bottom_anchor(self) -> QPointF:
        return self.pos() + QPointF(BOX_W / 2, BOX_H)

    def top_anchor(self) -> QPointF:
        return self.pos() + QPointF(BOX_W / 2, 0)


class Connector(QGraphicsPathItem):
    def __init__(self, src: SystemBox, dst: SystemBox) -> None:
        super().__init__()
        self.src, self.dst = src, dst
        self.setPen(QPen(QColor("#5b7a99"), 2))
        self.setZValue(-1)
        src._on_move.append(self.refresh)
        dst._on_move.append(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        # Route along the dominant direction between the two boxes.
        # A box placed below its source (e.g. the expert box under the
        # results box) must connect bottom -> top; forcing every link
        # through the right/left anchors drew a loop around the box.
        src_c = self.src.pos() + QPointF(BOX_W / 2, BOX_H / 2)
        dst_c = self.dst.pos() + QPointF(BOX_W / 2, BOX_H / 2)
        dx, dy = dst_c.x() - src_c.x(), dst_c.y() - src_c.y()
        if abs(dx) >= abs(dy):
            if dx >= 0:
                a, b = self.src.right_anchor(), self.dst.left_anchor()
                off = QPointF(max(40.0, abs(b.x() - a.x()) / 2), 0)
            else:
                a, b = self.src.left_anchor(), self.dst.right_anchor()
                off = QPointF(-max(40.0, abs(b.x() - a.x()) / 2), 0)
            path = QPainterPath(a)
            path.cubicTo(a + off, b - off, b)
        else:
            if dy >= 0:
                a, b = self.src.bottom_anchor(), self.dst.top_anchor()
                off = QPointF(0, max(40.0, abs(b.y() - a.y()) / 2))
            else:
                a, b = self.src.top_anchor(), self.dst.bottom_anchor()
                off = QPointF(0, -max(40.0, abs(b.y() - a.y()) / 2))
            path = QPainterPath(a)
            path.cubicTo(a + off, b - off, b)
        self.setPath(path)


_PROGRAM_IDS = {"geometry": "freecad", "mesh": "gmsh", "analysis": "calculix",
                "results": "paraview", "expert": "expert"}
_FILE_FILTERS = {
    "geometry": "FreeCAD (*.FCStd *.fcstd);;모든 파일 (*)",
    "mesh": "Gmsh (*.geo *.msh);;모든 파일 (*)",
    "analysis": "CalculiX 입력 (*.inp);;모든 파일 (*)",
    "results": "결과 파일 (*.vtu *.vtk *.pvd *.frd);;모든 파일 (*)",
}
PROJECT_FILTER = "CAELAB 프로젝트 (*.json);;모든 파일 (*)"
OUTPUT_FILTER = "작성한 출력 (*.inp *.msh *.med *.frd *.vtu *.vtk *.pvd);;모든 파일 (*)"
UNIT_LABELS = {"length": "길이", "force": "힘", "time": "시간", "temperature": "온도", "stress": "응력"}


def _unit_text(units: dict) -> str:
    return ", ".join(f"{UNIT_LABELS.get(name, name)}: {unit}" for name, unit in units.items()) \
        or "미확인 — 사용자 선언 필요"


def _problem_text(error) -> str:
    """Translate known failure categories; never echo arbitrary service diagnostics."""
    text = str(error)
    lowered = text.lower()
    if isinstance(error, PermissionError) or '권한' in text or 'permission denied' in lowered:
        return '파일이나 프로그램에 접근할 권한이 없습니다. 접근 가능한 위치와 실행 권한을 확인하세요.'
    if 'disk full' in lowered or 'no space left' in lowered or '공간' in text and '부족' in text:
        return '저장 공간이 부족합니다. 공간을 확보하거나 다른 위치에 저장하세요.'
    if '경로가 원본 파일과 같습니다' in text:
        return '프로젝트는 원본 입력 파일과 다른 이름으로 저장하세요.'
    if '이미 있습니다' in text or '이미 존재' in text:
        return '선택한 저장 위치에 기존 기록이 있습니다. 다른 이름이나 새 폴더를 선택하세요.'
    if '순환' in text:
        return '상자를 순환하게 연결할 수 없습니다. 입력이 한 방향으로 전달되도록 연결하세요.'
    if 'schema_version' in text and '지원하지' in text:
        return '이 프로그램에서 읽을 수 없는 프로젝트 버전입니다. 호환되는 버전에서 다시 여세요.'
    if any(part in text for part in ('프로젝트 읽기 실패', '중복 JSON', 'nodes.', 'connections:', '자료 형식')):
        return '프로젝트의 파일·상자·연결 정보를 읽지 못했습니다. 올바른 프로젝트 파일이나 보관한 사본을 선택하세요.'
    if '실행 저장소' in text or any(part in text for part in ('실행 기록', 'run_store', 'job.json', 'snapshot')):
        return '저장된 계산·대화 기록이 없거나 온전히 읽히지 않습니다. 프로젝트와 함께 보관한 기록 폴더를 확인하세요.'
    if '계산 중' in text or '질문' in text and ('진행 중' in text or '사용 중' in text):
        return '계산이나 대화 작업이 진행 중입니다. 작업을 마치거나 중단한 뒤 다시 저장하세요.'
    if 'FreeCAD 문서' in text:
        return 'FreeCAD 문서(.FCStd)를 입력 파일로 선택하세요.'
    if any(part in text for part in ('교환 파일', '무결성', '해시 불일치', '변조')):
        return '선택한 결과 파일의 보존 상태를 확인하지 못했습니다. 해석창에서 다른 보존 실행을 선택하거나 새로 계산하세요.'
    if '원본 변경' in text or '개정' in text and '변경' in text:
        return '원본 파일이 바뀌었습니다. 내용을 확인한 뒤 사용할 파일을 다시 등록하세요.'
    if '현재 선택 입력이 이 실행의 입력과 다릅니다' in text:
        return '선택한 계산은 현재 입력과 다른 입력으로 실행되었습니다. 해석창에서 사용할 계산을 확인하세요.'
    if '입력 단위 선언/출처가 변경' in text:
        return '입력의 단위 선언이 계산 당시와 다릅니다. 해석창에서 단위와 사용할 계산을 확인하세요.'
    if '실행 원본 또는 INCLUDE 파일이 변경/누락' in text:
        return '계산에 사용한 입력이나 포함 파일이 바뀌었거나 없습니다. 해석창에서 입력 파일을 확인하세요.'
    if '선택 실행에 사용할 수 있는 최신 결과가 없습니다' in text:
        return '선택한 계산에 사용할 수 있는 결과가 없습니다. 해석창에서 종료 상태와 결과 읽기 상태를 확인하세요.'
    if '상위 변경' in text or '오래' in text:
        return '상위 입력이 바뀌어 이전 출력이 오래되었습니다. 새로 작성한 출력을 등록하세요.'
    if '연결된 해석' in text or '선택 실행이 없습니다' in text:
        return '해석 상자를 연결하고 해석창에서 사용할 계산을 선택하세요.'
    if any(part in text for part in ('실행 환경', '네이티브 실행 파일이 아닙니다', '다른 호스트', '미구현', '구현되지')):
        return '현재 환경에서는 이 프로그램 연결을 사용할 수 없습니다. 지원되는 운영체제와 프로그램을 확인하세요.'
    if 'freecadcmd' in lowered or 'pvpython' in lowered or 'pvbatch' in lowered:
        return '화면이 있는 전용 프로그램을 연결하세요. 명령행 도구로는 이 파일을 표시할 수 없습니다.'
    if '실행 파일' in text:
        return '프로그램 실행 파일을 찾지 못했거나 사용할 수 없습니다. 설치와 프로그램 연결 설정을 확인하세요.'
    if any(part in text for part in ('레지스트리', 'runtime_env', '환경 변수', 'PATH')):
        return '프로그램 연결 설정을 읽지 못했습니다. 설치와 연결 설정을 확인하세요.'
    if isinstance(error, FileNotFoundError) or any(part in text for part in ('입력 파일이 없습니다', '입력 파일이 누락', 'No such file')):
        return '사용할 파일을 찾지 못했습니다. 파일을 다시 선택하거나 연결한 상위 파일을 확인하세요.'
    if '저장' in text and ('전에' in text or '먼저' in text):
        return '프로젝트를 먼저 저장한 뒤 프로그램을 여세요.'
    return '문제의 원인을 확인하지 못했습니다. 선택한 파일과 전용 프로그램의 작업 상태를 확인하세요.'


def _run_text(run, *, selected: bool = True) -> str:
    from .native_analysis import _RUN_LABELS, _PARSE_LABELS
    state = _RUN_LABELS.get(run.state, '미확인')
    parsed = _PARSE_LABELS.get(run.parse_state, '미확인')
    text = f"{'선택한 계산' if selected else '이 파일의 계산'}: {state}\n결과 읽기: {parsed}"
    if run.stale:
        text += '\n현재 입력과 다른 이전 결과입니다.'
    return text


def _request_time(value) -> str:
    from datetime import datetime
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone().strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError, AttributeError):
        return '미확인'


class ProjectWindow(QMainWindow):
    def __init__(self, workbench: Workbench | None = None,
                 project_path: str | Path | None = None) -> None:
        super().__init__()
        self.workbench = workbench or Workbench()
        self.project = self.workbench.new_native_project()
        self.boxes: dict[str, SystemBox] = {}
        self.connectors: list[Connector] = []
        self._dirty = False
        self._loading = False
        self.resize(1280, 820)
        self.setStyleSheet(THEME + '''
            QPushButton#primary:disabled, QPushButton#primary:disabled:hover {
                background: #e1e6eb; border-color: #d5dce3; color: #5d6b78; }
        ''')
        self._build_ui()
        self._show_project()
        if project_path is not None:
            self.open_project(project_path, confirm=False)
        self.resizeDocks([self.history_dock], [90], Qt.Vertical)
        self.run_timer = QTimer(self)
        self.run_timer.setInterval(1500)
        self.run_timer.timeout.connect(self._poll_runs)
        self.run_timer.start()
        self._run_signature = None

    def _build_ui(self) -> None:
        file_menu = self.menuBar().addMenu("파일(&F)")
        self.file_actions = {}
        for key, text, shortcut, callback in (
            ("new", "새 프로젝트", QKeySequence.New, self.new_project),
            ("open", "프로젝트 열기…", QKeySequence.Open, self.open_project),
            ("save", "저장", QKeySequence.Save, self.save_project),
            ("save_as", "다른 이름으로 저장…", QKeySequence.SaveAs, self.save_project_as),
        ):
            action = QAction(text, self)
            action.setShortcut(shortcut)
            action.triggered.connect(lambda checked=False, fn=callback: fn())
            file_menu.addAction(action)
            self.file_actions[key] = action
        file_menu.addSeparator()
        close_action = file_menu.addAction("종료")
        close_action.triggered.connect(self.close)

        toolbar = QToolBar("프로젝트")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        toolbar.addAction(self.file_actions["save"])
        self.connect_action = toolbar.addAction("선택한 상자 연결…")
        self.connect_action.setToolTip("상자 두 개를 Ctrl로 선택하고 전달할 파일을 지정합니다")
        self.connect_action.triggered.connect(self.connect_selected)
        refresh = toolbar.addAction("파일 상태 새로고침")
        refresh.setShortcut(QKeySequence.Refresh)
        refresh.triggered.connect(self.refresh_files)
        fit = toolbar.addAction("도식 맞춤")
        fit.triggered.connect(self.fit_schematic)
        self.zoom_in_action = toolbar.addAction("도식 확대")
        self.zoom_in_action.setShortcut(QKeySequence.ZoomIn)
        self.zoom_in_action.triggered.connect(lambda: self.view.scale(1.2, 1.2))
        self.zoom_out_action = toolbar.addAction("도식 축소")
        self.zoom_out_action.setShortcut(QKeySequence.ZoomOut)
        self.zoom_out_action.triggered.connect(lambda: self.view.scale(1 / 1.2, 1 / 1.2))
        original = toolbar.addAction("도식 100%")
        original.triggered.connect(self.reset_view)
        run = toolbar.addAction("해석창 열기")
        run.triggered.connect(self.run_chain)

        self.toolbox = QListWidget()
        self.toolbox.setObjectName("toolbox")
        from PySide6.QtWidgets import QListWidgetItem
        for kind, title, sub in SYSTEM_KINDS:
            self.toolbox.addItem(QListWidgetItem(icon(_KIND_ICONS[kind]), f"{title} — {sub}"))
        self.toolbox.itemDoubleClicked.connect(
            lambda item: self.add_box(SYSTEM_KINDS[self.toolbox.row(item)][0]))
        tools = QDockWidget("프로그램 추가 (더블클릭)", self)
        tools.setWidget(self.toolbox)
        self.addDockWidget(Qt.LeftDockWidgetArea, tools)

        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.view.setBackgroundBrush(QBrush(QColor("#eef1f5")))
        self.setCentralWidget(self.view)
        self.scene.mouseDoubleClickEvent = self._scene_double_click
        self.scene.selectionChanged.connect(self._selection_changed)
        self.empty_hint = self.scene.addText(
            "빈 프로젝트\n\n왼쪽 도구상자에서 프로그램을 추가한 뒤\n실제 입력 파일을 선택하세요.",
            _box_font(12))
        self.empty_hint.setDefaultTextColor(QColor("#5a6672"))
        self.empty_hint.setPos(40, 40)

        props_widget = QWidget()
        props_layout = QVBoxLayout(props_widget)
        props_layout.setSpacing(12)
        self.selection_name = QLabel()
        title_font = self.selection_name.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 2)
        self.selection_name.setFont(title_font)
        self.program_summary = QLabel()
        self.input_summary = QLabel()
        self.output_summary = QLabel()
        self.run_summary = QLabel()
        self.next_action = QLabel()
        for label in (self.selection_name, self.program_summary, self.input_summary,
                      self.output_summary, self.run_summary, self.next_action):
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            props_layout.addWidget(label)
        self.input_button = QPushButton("입력 파일 선택…")
        self.output_button = QPushButton("작성한 출력 등록…")
        self.open_button = QPushButton("프로그램 열기")
        self.open_button.setObjectName("primary")
        for button, callback in ((self.open_button, self.open_selected),
                                 (self.output_button, lambda: self.choose_file("output")),
                                 (self.input_button, lambda: self.choose_file("input"))):
            button.clicked.connect(callback)
            props_layout.addWidget(button)
        props_layout.addStretch()
        props_scroll = QScrollArea()
        props_scroll.setWidgetResizable(True)
        props_scroll.setFrameShape(QScrollArea.NoFrame)
        props_scroll.setWidget(props_widget)
        props_scroll.setMinimumWidth(260)
        properties_dock = QDockWidget("선택한 프로그램", self)
        properties_dock.setWidget(props_scroll)
        self.addDockWidget(Qt.RightDockWidgetArea, properties_dock)
        self.resizeDocks([tools, properties_dock], [240, 300], Qt.Horizontal)

        bottom = QWidget()
        bottom.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout = QVBoxLayout(bottom)
        current_row = QHBoxLayout()
        self.status = QLabel()
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        current_row.addWidget(self.status, 1)
        self.history_toggle = QToolButton()
        self.history_toggle.setText("이력 보기")
        self.history_toggle.setCheckable(True)
        self.history_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.history_toggle.setArrowType(Qt.RightArrow)
        current_row.addWidget(self.history_toggle)
        layout.addLayout(current_row)
        self.log = QTextEdit()
        self.log.setObjectName("logView")
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(90)
        layout.addWidget(self.log)
        self.log.hide()
        self._collapsed_history_height = bottom.sizeHint().height()
        bottom.setMaximumHeight(self._collapsed_history_height)
        self.history_toggle.toggled.connect(self._show_history)
        messages = QDockWidget("작업 기록", self)
        messages.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.history_dock = messages
        messages.setWidget(bottom)
        self.addDockWidget(Qt.BottomDockWidgetArea, messages)
        self._selection_changed()

    def _show_history(self, shown: bool) -> None:
        self.log.setVisible(shown)
        self.history_toggle.setArrowType(Qt.DownArrow if shown else Qt.RightArrow)
        self.history_dock.widget().setMaximumHeight(16777215 if shown else self._collapsed_history_height)
        self.resizeDocks([self.history_dock],
                         [self.history_dock.widget().sizeHint().height() + 24], Qt.Vertical)

    def _title(self) -> None:
        path = str(self.project.path) if self.project.path else "아직 저장하지 않음"
        self.setWindowTitle(f"{self.project.name}{' *' if self._dirty else ''} — CAELAB Workbench")
        self.statusBar().showMessage(f"프로젝트: {self.project.path.parent.name}/{self.project.path.name}" if self.project.path else path)

    def _mark_dirty(self) -> None:
        if not self._loading:
            self._dirty = True
            self._title()

    def _message(self, text: str) -> None:
        # Only compose user-facing action text here; service diagnostics are translated at their boundary.
        first, *following = text.splitlines()
        self._current_message = first + (' — ' + following[0] if following else '')
        self._render_current_message()
        self.status.setToolTip(text)
        self.log.moveCursor(self.log.textCursor().MoveOperation.End)
        if self.log.toPlainText():
            self.log.insertPlainText('\n')
        self.log.insertPlainText(text)

    def _render_current_message(self) -> None:
        text = getattr(self, "_current_message", "")
        self.status.setText(self.status.fontMetrics().elidedText(
            text, Qt.ElideRight, max(50, self.status.width())) if self.isVisible() else text)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "status"):
            QTimer.singleShot(0, self._render_current_message)

    def _short_path(self, path: str) -> str:
        base = self.project.path.parent if self.project.path else Path.cwd()
        try:
            relative = Path(relpath(path, base))
            if '..' not in relative.parts:
                return relative.as_posix()
        except ValueError:
            pass
        selected = Path(path)
        return f'{selected.parent.name}/{selected.name}'

    def _summary_file_name(self, path: str) -> tuple[str, str]:
        root = self.project.run_store
        if root is None and self.project.path is not None:
            root = self.project.path.with_suffix(".runs")
        if root is None or not Path(path).resolve().is_relative_to(root.resolve()):
            return self._short_path(path), ""
        # Match the file's own receipt, never the currently selected execution.
        relative = Path(path).resolve().relative_to(root.resolve())
        try:
            from caelab.native.run_store import RunStore
            if len(relative.parts) < 2:
                raise ValueError("실행 파일 위치 미확인")
            cached = getattr(self, "_summary_run_store", None)
            if cached is None or cached.root != root.resolve():
                cached = self._summary_run_store = RunStore(root)
            run = cached.read(root / relative.parts[0] / "job.json")
            if not run.created_at or not run.input_path:
                raise ValueError("실행 출처 미확인")
            source = Path(run.input_path)
            source_name = source.name if source.resolve().is_relative_to(root.resolve()) else self._short_path(run.input_path)
            return Path(path).name, f"기준 입력: {source_name}\n계산 요청: {_request_time(run.created_at)}\n{_run_text(run, selected=False)}"
        except (OSError, ValueError, TypeError):
            return Path(path).name, "실행 출처 미확인"

    def _error(self, text: str, error: Exception) -> None:
        message = f"{text}. {_problem_text(error)}"
        self._message(message)
        QMessageBox.warning(self, text, message)

    def _confirm_transition(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self, "저장하지 않은 변경", "현재 프로젝트의 변경을 저장하시겠습니까?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if answer == QMessageBox.Cancel:
            return False
        return self.save_project() if answer == QMessageBox.Save else True

    def new_project(self) -> bool:
        if not self._confirm_transition():
            return False
        self.project = self.workbench.new_native_project()
        self._dirty = False
        self._show_project()
        return True

    def open_project(self, path: str | Path | None = None, *, confirm: bool = True) -> bool:
        if path is None:
            path, _filter = QFileDialog.getOpenFileName(self, "프로젝트 열기", "", PROJECT_FILTER)
        if not path or (confirm and not self._confirm_transition()):
            return False
        try:
            project = self.workbench.open_native_project(path)
        except Exception as error:
            self._error("프로젝트를 열지 못했습니다", error)
            return False
        self.project = project
        self._dirty = False
        self._show_project()
        self._message(f"프로젝트를 열었습니다: {self._short_path(self.project.path)}" +
                      ('\n' + '\n'.join(self._project_notes()) if project.issues else ''))
        return True

    def save_project(self, path: str | Path | None = None) -> bool:
        if path is None and self.project.path is None:
            return self.save_project_as()
        previous_path = self.project.path
        try:
            self.workbench.save_native_project(self.project, path)
        except Exception as error:
            self._error("프로젝트를 저장하지 못했습니다", error)
            return False
        self._dirty = False
        self._title()
        message = f"프로젝트를 저장했습니다: {self._short_path(self.project.path)}"
        if previous_path and previous_path != self.project.path:
            message += "\n실행 기록과 대화는 복사했습니다. CAD·INP 원본은 기존 파일을 계속 참조합니다."
        self._message(message)
        return True

    def save_project_as(self) -> bool:
        path, _filter = QFileDialog.getSaveFileName(
            self, "다른 이름으로 저장", str(self.project.path or "project.json"), PROJECT_FILTER)
        return self.save_project(path) if path else False

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._confirm_transition():
            self.run_timer.stop()
            event.accept()
        else:
            event.ignore()

    def _show_project(self) -> None:
        self._loading = True
        self.scene.clearSelection()
        self.connectors.clear()
        self.boxes.clear()
        # Keep the empty-state item alive when replacing only the schematic.
        for item in list(self.scene.items()):
            if item is not self.empty_hint:
                self.scene.removeItem(item)
        for node in self.project.nodes.values():
            self._add_node_box(node)
        for connection in self.project.connections:
            self._draw_connection(connection.source_id, connection.target_id, connection.source_role)
        self._loading = False
        self.log.clear()
        self.empty_hint.setVisible(not self.boxes)
        self._reflect_programs()
        self._selection_changed()
        self._title()
        self._message("프로그램을 추가하고 실제 파일을 선택하세요." if not self.boxes
                      else "상자를 선택하면 파일과 출처를 확인할 수 있습니다.")
        self.reset_view()

    def _add_node_box(self, node) -> SystemBox:
        title, sub = next(((title, sub) for kind, title, sub in SYSTEM_KINDS if kind == node.kind),
                          (node.kind, node.program_id))
        box = SystemBox(node.kind, title, sub)
        box.node_id = node.id
        box.setPos(*node.position)
        box._on_move.append(lambda: self._position_changed(box))
        self.boxes[node.id] = box
        self.scene.addItem(box)
        return box

    def add_box(self, kind: str) -> SystemBox:
        position = (40 + 250 * (len(self.boxes) % 3), 40 + 150 * (len(self.boxes) // 3))
        node = self.workbench.add_program_node(self.project, kind, _PROGRAM_IDS[kind], position)
        box = self._add_node_box(node)
        self.empty_hint.setVisible(False)
        self.scene.clearSelection()
        box.setSelected(True)
        self._mark_dirty()
        self._reflect_programs()
        self.view.centerOn(box)
        return box

    def _position_changed(self, box: SystemBox) -> None:
        self.project.nodes[box.node_id].position = (box.pos().x(), box.pos().y())
        self._mark_dirty()

    def _selected_boxes(self) -> list[SystemBox]:
        return [item for item in self.scene.selectedItems() if isinstance(item, SystemBox)]

    def _selection_changed(self) -> None:
        selected = self._selected_boxes()
        self.connect_action.setEnabled(len(selected) == 2)
        editable = len(selected) == 1 and selected[0].kind != 'expert'
        self.input_button.setVisible(editable)
        self.output_button.setVisible(editable)
        self.input_button.setEnabled(editable)
        self.output_button.setEnabled(editable)
        self.open_button.setEnabled(len(selected) == 1)
        self.run_summary.clear()
        self.input_summary.setToolTip('')
        self.output_summary.setToolTip('')
        if len(selected) != 1:
            self.selection_name.setText('프로그램을 선택하세요')
            self.program_summary.clear()
            self.input_summary.clear()
            self.output_summary.clear()
            self.next_action.setText('상자 하나를 선택해 파일 상태와 다음 행동을 확인하세요.')
            return
        box = selected[0]
        node = self.project.nodes[box.node_id]
        self.open_button.setText({'geometry': 'FreeCAD 열기', 'mesh': 'Gmsh 열기',
                                  'analysis': 'CalculiX 해석창 열기', 'results': 'ParaView 열기',
                                  'expert': '전문가 창 열기'}.get(node.kind, '프로그램 열기'))
        statuses = {status.spec.id: status for status in self.workbench.programs()}
        program = statuses.get(node.program_id)
        reference = self.workbench.input_file(self.project, node.id)
        if node.kind == 'analysis' and self.project.path is not None:
            try:
                run = self.workbench.selected_native_run(self.project, node.id)
                self.run_summary.setText(_run_text(run) if run else '선택한 계산 없음')
            except (OSError, ValueError, RuntimeError) as error:
                self.run_summary.setText(_problem_text(error))
        self._selection_summary(node, program, reference, self._node_note(node, reference))
        if node.kind not in {'analysis', 'expert'}:
            self.open_button.setEnabled(bool(program and program.available and reference and self._file_status(reference) != 'missing'))

    def _node_note(self, node, reference) -> str:
        if _selection_changed_input(node, reference) and self._file_status(reference) == 'ready':
            return '선택한 계산이 바뀌었습니다. 기존 표시·대화와 다를 수 있으니 전용 프로그램에서 확인하세요.'
        status = self._file_status(reference) if reference else 'needs_input'
        if status == 'missing':
            return '입력 파일이 없습니다. 파일을 다시 선택하거나 연결한 상위 파일을 확인하세요.'
        if status == 'changed':
            return '입력 파일이 외부에서 변경되었습니다. 내용을 확인한 뒤 사용할 파일을 다시 등록하세요.'
        if status == 'stale':
            return '현재 입력에 맞는 최신 파일인지 확인하세요. 새로 작성한 파일이 있다면 다시 등록하세요.'
        if node.state in {'failed', 'stale'} and node.reason:
            return _problem_text(node.reason)
        return ''

    def _selection_summary(self, node, program, reference, reason):
        name = program.spec.name if program else "전문가 논의" if node.kind == "expert" else "전용 프로그램"
        self.selection_name.setText(name)
        installation = ("프로그램 연결 준비" if program.available else _problem_text(program.reason)) if program else "별도 대화창에서 열기" if node.kind == "expert" else "프로그램 연결이 필요합니다."
        issue = reason
        self.program_summary.setText(installation + (f"\n{issue}" if issue else ""))
        self.output_summary.setVisible(node.kind != "expert" or "output" in node.files)
        incoming = next((edge for edge in self.project.connections if edge.target_id == node.id), None)
        input_title = '입력 파일'
        if incoming:
            source = self.project.nodes[incoming.source_id]
            source_label = next((title for kind, title, sub in SYSTEM_KINDS if kind == source.kind), '상위 프로그램')
            input_title = f"연결 입력 ({source_label}의 {'입력' if incoming.source_role == 'input' else '등록 출력'})"
        for label, title, ref in ((self.input_summary, input_title, reference),
                                  (self.output_summary, "등록한 출력", node.files.get("output"))):
            if ref is None:
                label.setTextFormat(Qt.RichText)
                label.setText(f"<b>{title}</b><br>선택 파일 없음")
            else:
                source = {"user": "사용자 선언", "unknown": "미확인", "": "미확인"}.get(ref.unit_source, "출처 미확인")
                status = self._file_status(ref)
                file_name, provenance = self._summary_file_name(ref.path)
                provenance_html = escape(provenance).replace("\n", "<br>") + "<br>" if provenance else ""
                label.setTextFormat(Qt.RichText)
                label.setText(f"<b>{title}</b> — {escape(STATE_LABELS.get(status, "상태 미확인"))}<br>"
                              f"<b>{escape(file_name)}</b><br>{provenance_html}"
                              f"단위: {escape(_unit_text(ref.units))} ({escape(source)})")
                label.setToolTip(file_name + ("\n" + provenance if provenance else ""))
        status = self._file_status(reference) if reference else "needs_input"
        if node.kind == "expert":
            action = "전문가 창에서 실행 출처를 확인하고 사용할 근거를 가져오세요."
        elif status == "missing":
            action = "누락된 입력 파일을 다시 선택하세요."
        elif status in {"stale", "changed"}:
            action = "입력의 변경 내용을 확인하세요. 상위 프로그램에서 새 파일을 작성한 뒤 다시 등록하세요."
        elif reference is None:
            action = "전달할 실행 자료를 연결하세요." if node.kind == "expert" else "사용할 입력 파일을 선택하세요."
        elif node.kind == "geometry":
            action = "FreeCAD에서 입력을 확인하세요. 이미 작성한 최신 덱은 출력 파일로 등록할 수 있습니다."
        elif node.kind == "analysis":
            action = "해석창에서 입력과 실행 기록을 확인하세요. 계산은 해석창에서 시작합니다."
        else:
            action = "전용 프로그램에서 선택한 파일을 열고 실제 표시를 확인하세요."
        if program and not program.available:
            action = f"{program.spec.name} 설치와 프로그램 연결 설정을 확인하세요.\n{action}"
        self.next_action.setText(f"다음 행동\n{action}")

    def choose_file(self, role: str = "input") -> bool:
        selected = self._selected_boxes()
        if len(selected) != 1:
            return False
        box = selected[0]
        path, _filter = QFileDialog.getOpenFileName(
            self, "입력 파일 선택" if role == "input" else "출력 파일 등록", "",
            OUTPUT_FILTER if role == "output" else _FILE_FILTERS.get(box.kind, "모든 파일 (*)"))
        if not path:
            return False
        units = self._ask_file_units(path)
        if units is None:
            return False
        return self.register_file(box.node_id, path, role, units)

    def _ask_file_units(self, path: str) -> dict | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("파일 단위 선언")
        layout = QFormLayout(dialog)
        selected_file = QLabel(path)
        selected_file.setWordWrap(True)
        layout.addRow("선택 파일", selected_file)
        layout.addRow(QLabel("사용한 단위를 입력하세요. 공란은 미확인으로 보존합니다."))
        fields = {}
        for name, label in UNIT_LABELS.items():
            field = QLineEdit()
            field.setObjectName(f"unit_{name}")
            field.setPlaceholderText("미확인")
            layout.addRow(label, field)
            fields[name] = field
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() != QDialog.Accepted:
            return None
        return {name: field.text().strip() for name, field in fields.items() if field.text().strip()}

    def register_file(self, node_id: str, path: str | Path, role: str = "input",
                      units: dict | None = None) -> bool:
        try:
            self.workbench.set_node_file(self.project, node_id, path, role=role,
                                         units=units, unit_source="user" if units else "unknown")
        except Exception as error:
            self._error(f"파일을 등록하지 못했습니다 ({self._summary_file_name(str(path))[0]})", error)
            return False
        self._mark_dirty()
        self._sync_connections()
        self._reflect_programs()
        self._selection_changed()
        self._message(f"{'입력' if role == 'input' else '출력'} 파일을 등록했습니다: {self._summary_file_name(str(path))[0]}")
        return True

    def _sync_connections(self) -> None:
        for connector in self.connectors:
            connector.src._on_move.remove(connector.refresh)
            connector.dst._on_move.remove(connector.refresh)
            self.scene.removeItem(connector)
        self.connectors.clear()
        for connection in self.project.connections:
            self._draw_connection(connection.source_id, connection.target_id, connection.source_role)

    def _draw_connection(self, source_id: str, target_id: str, role: str) -> None:
        connector = Connector(self.boxes[source_id], self.boxes[target_id])
        connector.setToolTip(f"{self.boxes[source_id].title}의 {'입력' if role == 'input' else '출력'} 파일"
                             f" → {self.boxes[target_id].title} 입력")
        self.scene.addItem(connector)
        self.connectors.append(connector)

    def connect(self, source_id: str, target_id: str, source_role: str = "output") -> bool:
        try:
            self.workbench.connect_nodes(self.project, source_id, target_id, source_role=source_role)
        except Exception as error:
            self._error("상자를 연결하지 못했습니다", error)
            return False
        self._sync_connections()
        self._mark_dirty()
        self._reflect_programs()
        self._selection_changed()
        self._message(f"{self.boxes[source_id].title}의 {'입력' if source_role == 'input' else '출력'} 파일을 "
                      f"{self.boxes[target_id].title} 입력에 연결했습니다.")
        return True

    def connect_selected(self) -> None:
        boxes = self._selected_boxes()
        if len(boxes) != 2:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("전달할 파일 선택")
        layout = QFormLayout(dialog)
        source = QComboBox()
        for box in boxes:
            source.addItem(box.title, box.node_id)
        role = QComboBox()
        role.addItem("등록한 출력 파일", "output")
        role.addItem("현재 사용하는 입력 파일", "input")
        target = QLabel()
        file_path = QLabel()
        file_path.setWordWrap(True)
        def describe():
            target_box = boxes[1 - source.currentIndex()]
            target.setText(f"{target_box.title}의 입력")
            reference = (self.workbench.input_file(self.project, source.currentData())
                         if role.currentData() == "input" else
                         self.project.nodes[source.currentData()].files.get("output"))
            file_path.setText("\n".join(part for part in self._summary_file_name(reference.path) if part) if reference else "파일 미등록 — 연결은 입력 필요 상태로 남습니다.")
        source.currentIndexChanged.connect(describe)
        role.currentIndexChanged.connect(describe)
        layout.addRow("보내는 상자", source)
        layout.addRow("전달할 파일", role)
        layout.addRow("받는 상자", target)
        layout.addRow("실제 파일", file_path)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        describe()
        if dialog.exec() == QDialog.Accepted:
            self.connect(source.currentData(), boxes[1 - source.currentIndex()].node_id,
                         role.currentData())

    def refresh_files(self) -> None:
        before = [(node.state, node.reason) for node in self.project.nodes.values()]
        issues = self.workbench.refresh_native_project(self.project)
        if before != [(node.state, node.reason) for node in self.project.nodes.values()]:
            self._mark_dirty()
        self._reflect_programs()
        self._selection_changed()
        self._message("파일 상태를 다시 확인했습니다. 변경된 원본과 오래된 연결을 확인하세요." +
                      ('\n' + '\n'.join(self._project_notes()) if issues else ''))

    def _reflect_programs(self) -> None:
        statuses = {status.spec.id: status for status in self.workbench.programs()}
        for node_id, box in self.boxes.items():
            node = self.project.nodes[node_id]
            program = statuses.get(node.program_id)
            box.subtitle = (f"{program.spec.name} — {'연결 준비' if program.available else '연결 확인 필요'}"
                            if program else "별도 대화창에서 열기" if node.kind == "expert" else "독립 프로그램 연결 미구현")
            self._reflect_node(node_id)
        self._poll_runs()

    def _reflect_node(self, node_id: str) -> None:
        node, box = self.project.nodes[node_id], self.boxes[node_id]
        reference = self.workbench.input_file(self.project, node_id)
        title = next((title for kind, title, sub in SYSTEM_KINDS if kind == node.kind), "프로그램")
        identifying_file = reference or node.files.get("output")
        box.title = f"{title} — {Path(identifying_file.path).name}" if identifying_file else title
        box.file_label = Path(reference.path).name if reference else "선택 파일 없음"
        box.set_state(node.state, self._node_note(node, reference))
        if _selection_changed_input(node, reference):
            # The connected file changed; this does not establish that a viewer
            # displayed it or that a discussion adopted it. Keep the stored state.
            status = self._file_status(reference)
            if status == "ready":
                box.status_label = "선택 실행 변경"
            else:
                box.set_state(status, self._node_note(node, reference))
        file_label = "\n".join(part for part in self._summary_file_name(reference.path) if part) if reference else "입력 파일 없음"
        box.setToolTip(f"{box.title}\n{box.subtitle}\n{file_label}\n{self._node_note(node, reference)}")

    def _file_status(self, reference) -> str:
        # Polling should not hash an unchanged large result every second.
        try:
            stat = Path(reference.path).stat()
            metadata = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
        except OSError:
            metadata = None
        key = (reference.path, reference.revision, reference.stale, metadata)
        cache = getattr(self, "_file_status_cache", {})
        if cache.get(reference.path, (None,))[0] != key:
            cache[reference.path] = (key, reference.status)
            self._file_status_cache = cache
        return cache[reference.path][1]

    def _poll_runs(self) -> None:
        if self.project.path is None:
            return
        signature = []
        run_states = {}
        for node_id, box in self.boxes.items():
            if box.kind != "analysis":
                continue
            try:
                run = self.workbench.selected_native_run(self.project, node_id)
                if run is None:
                    continue
                state = "stale" if run.stale and run.state == "completed" else run.state
                run_states[node_id] = (state, _run_text(run))
                signature.append((node_id, run.id, run.state, run.parse_state, run.stale, run.error))
            except (OSError, ValueError, RuntimeError) as error:
                run_states[node_id] = ("failed", _problem_text(error))
                signature.append((node_id, str(error)))
        # Selecting a run can replace/remove outputs and invalidate downstream nodes.
        for node_id in self.boxes:
            self._reflect_node(node_id)
        for node_id, (state, reason) in run_states.items():
            self.boxes[node_id].set_state(state, reason)
        signature = tuple(signature)
        if signature != getattr(self, "_run_signature", None):
            self._run_signature = signature
            self._selection_changed()

    def _project_notes(self) -> list[str]:
        notes = []
        for issue in self.project.issues:
            matched = False
            for node in self.project.nodes.values():
                for role, reference in node.files.items():
                    if issue.startswith(f'{node.id}.files.{role}:'):
                        label = self._summary_file_name(reference.path)[0]
                        state = STATE_LABELS.get(self._file_status(reference), '상태 미확인')
                        notes.append(f'{label}: {state}. 사용할 파일을 다시 확인하세요.')
                        matched = True
            if not matched:
                notes.append(_problem_text(issue))
        return list(dict.fromkeys(notes))

    def _scene_double_click(self, event) -> None:
        item = self.scene.itemAt(event.scenePos(), self.view.transform())
        if isinstance(item, SystemBox):
            self.open_box(item)

    def open_selected(self) -> None:
        selected = self._selected_boxes()
        if len(selected) == 1:
            self.open_box(selected[0])

    def open_box(self, box: SystemBox) -> None:
        if box.kind in {"analysis", "expert"} and (self._dirty or self.project.path is None):
            answer = QMessageBox.question(
                self, "별도 프로그램 열기", "프로젝트를 저장한 뒤 별도 프로그램을 엽니다.",
                QMessageBox.Save | QMessageBox.Cancel, QMessageBox.Save)
            if answer != QMessageBox.Save or not self.save_project():
                return
        try:
            if box.kind == "analysis":
                launch = self.workbench.open_native_analysis(self.project, box.node_id)
            elif box.kind == "expert":
                launch = self.workbench.open_native_expert(self.project, box.node_id)
            elif box.kind == "results" and self.workbench.resolve_evidence_run(self.project, box.node_id):
                launch = self.workbench.open_native_results(self.project, box.node_id)
            else:
                launch = self.workbench.open_program(self.project, box.node_id)
        except Exception as error:
            self._mark_dirty()
            self._reflect_programs()
            self._selection_changed()
            self._error("프로그램을 열지 못했습니다", error)
            return
        if box.kind != "analysis" or launch.state == "failed":
            self._mark_dirty()
        if launch.state == "failed":
            self._error("프로그램을 열지 못했습니다", launch.error)
        else:
            self._message(f"{box.title} 열기를 요청했습니다. 실제 파일 표시는 전용 프로그램에서 확인하세요.")
        self._reflect_programs()
        self._selection_changed()

    def run_chain(self) -> None:
        boxes = self._selected_boxes()
        if len(boxes) != 1 or boxes[0].kind != "analysis":
            self._message("CalculiX 상자를 하나 선택하세요. 별도 해석창에서 입력을 확인하고 계산합니다.")
            return
        self.open_box(boxes[0])

    def fit_schematic(self) -> None:
        rect = self.scene.itemsBoundingRect().adjusted(-30, -30, 30, 30)
        if not rect.isEmpty():
            self.view.fitInView(rect, Qt.KeepAspectRatio)
            if self.view.transform().m11() > 1.0:
                self.view.resetTransform()

    def reset_view(self) -> None:
        self.view.resetTransform()
        self.view.centerOn(self.scene.itemsBoundingRect().center())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.reset_view()
