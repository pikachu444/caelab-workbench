"""Project window — the parent screen, modelled on ANSYS Workbench.

The workbench is a launcher/integration shell: independent systems
(geometry in FreeCAD, meshing, a solver run, results in ParaView, an
expert discussion) appear as boxes on a project schematic. Boxes are
connected through selected native files. Double-clicking a box opens
its independent program. The CalculiX box opens a separate input and
execution window; recorded runs can be inspected from this project.
"""

from __future__ import annotations

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
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QToolBar,
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
    ("expert", "전문가 논의", "독립 프로그램 연결"),
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
    "opened": "프로그램 열림",
    "queued": "대기", "preparing": "입력 보존 중", "completed": "프로세스 종료",
    "cancelled": "취소됨", "interrupted": "중단됨",
}
BOX_W, BOX_H = 210, 98


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
        status = STATE_LABELS.get(self.state, self.state)
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
        self.setStyleSheet(THEME)
        self._build_ui()
        self._show_project()
        if project_path is not None:
            self.open_project(project_path, confirm=False)
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
        original = toolbar.addAction("원래 크기")
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
        self.properties = QTextEdit()
        self.properties.setReadOnly(True)
        self.properties.setMinimumWidth(220)
        props_layout.addWidget(self.properties)
        self.input_button = QPushButton("입력 파일 선택…")
        self.output_button = QPushButton("출력 파일 등록…")
        self.open_button = QPushButton("프로그램 열기")
        for button, callback in ((self.input_button, lambda: self.choose_file("input")),
                                 (self.output_button, lambda: self.choose_file("output")),
                                 (self.open_button, self.open_selected)):
            button.clicked.connect(callback)
            props_layout.addWidget(button)
        properties_dock = QDockWidget("선택한 프로그램", self)
        properties_dock.setWidget(props_widget)
        self.addDockWidget(Qt.RightDockWidgetArea, properties_dock)
        self.resizeDocks([tools, properties_dock], [240, 300], Qt.Horizontal)

        bottom = QWidget()
        layout = QVBoxLayout(bottom)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.log = QTextEdit()
        self.log.setObjectName("logView")
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(90)
        layout.addWidget(self.log)
        messages = QDockWidget("메시지", self)
        messages.setWidget(bottom)
        self.addDockWidget(Qt.BottomDockWidgetArea, messages)
        self._selection_changed()

    def _title(self) -> None:
        path = str(self.project.path) if self.project.path else "아직 저장하지 않음"
        self.setWindowTitle(f"{self.project.name}{' *' if self._dirty else ''} — CAELAB Workbench")
        self.statusBar().showMessage(path)

    def _mark_dirty(self) -> None:
        if not self._loading:
            self._dirty = True
            self._title()

    def _message(self, text: str) -> None:
        self.status.setText(text)
        self.log.append(text)

    def _error(self, text: str, error: Exception) -> None:
        message = f"{text}: {error}"
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
        self._message(f"프로젝트를 열었습니다: {self.project.path}")
        return True

    def save_project(self, path: str | Path | None = None) -> bool:
        if path is None and self.project.path is None:
            return self.save_project_as()
        try:
            self.workbench.save_native_project(self.project, path)
        except Exception as error:
            self._error("프로젝트를 저장하지 못했습니다", error)
            return False
        self._dirty = False
        self._title()
        self._message(f"프로젝트를 저장했습니다: {self.project.path}")
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
        editable = len(selected) == 1 and selected[0].kind != "expert"
        self.input_button.setEnabled(editable)
        self.output_button.setEnabled(editable)
        self.open_button.setEnabled(len(selected) == 1)
        if len(selected) != 1:
            self.properties.setPlainText("상자를 선택해 파일과 프로그램 상태를 확인하세요.\n\n"
                                         "상자 두 개를 Ctrl로 선택하면 연결할 수 있습니다.")
            return
        box = selected[0]
        node = self.project.nodes[box.node_id]
        statuses = {status.spec.id: status for status in self.workbench.programs()}
        program = statuses.get(node.program_id)
        lines = [box.title, f"입력 상태: {STATE_LABELS.get(node.state, node.state)}",
                 f"설명: {self._display_reason(node.reason)}"]
        incoming = [c for c in self.project.connections if c.target_id == node.id]
        for connection in incoming:
            role = "입력" if connection.source_role == "input" else "출력"
            lines += ["", f"연결 입력: {self.boxes[connection.source_id].title}의 {role} 파일"]
        reference = self.workbench.input_file(self.project, node.id)
        references = [("사용할 입력", reference)]
        references += [(f"등록한 {'입력' if role == 'input' else '출력'}", ref) for role, ref in node.files.items()
                       if ref is not reference]
        for role, ref in references:
            lines += ["", f"{role}:"]
            if ref is None:
                lines.append("파일 없음")
            else:
                source = {"user": "사용자 선언", "unknown": "미확인"}.get(ref.unit_source, ref.unit_source)
                lines += [ref.path, f"파일 상태: {STATE_LABELS.get(ref.status, ref.status)}",
                          f"단위: {_unit_text(ref.units)}", f"단위 출처: {source or '미확인'}"]
        if program:
            lines += ["", f"프로그램: {program.spec.name}",
                      f"설치: {'발견됨' if program.available else '찾지 못함'}",
                      f"실행 파일: {program.path or '없음'}",
                      f"발견 경로: {program.source}", f"설명: {program.reason}",
                      f"추가 환경 변수: {', '.join(program.runtime_env) or '없음'}"]
        else:
            lines += ["", "독립 전문가 프로그램 연결은 아직 구현되지 않았습니다."
                      if node.kind == "expert" else f"{node.program_id} 프로그램을 찾지 못했습니다."]
        if node.kind == "analysis" and self.project.path is not None:
            from .native_analysis import _PARSE_LABELS, _RUN_LABELS
            try:
                run = self.workbench.selected_native_run(self.project, node.id)
                if run is not None:
                    lines += ["", f"선택한 실행: {run.created_at}",
                              f"실행 상태: {_RUN_LABELS.get(run.state, run.state)}",
                              f"결과 판독: {_PARSE_LABELS.get(run.parse_state, run.parse_state)}",
                              f"실행 입력: {run.input_path}", f"보존 위치: {run.workdir}"]
                    if run.error:
                        lines.append(run.error)
                    if run.stale:
                        lines.append(f"오래된 입력의 결과: {run.stale_reason}")
            except (OSError, ValueError, RuntimeError) as error:
                lines.append(f"실행 기록을 읽지 못했습니다: {error}")
        self.properties.setPlainText("\n".join(lines))

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
            self._error("파일을 등록하지 못했습니다", error)
            return False
        self._mark_dirty()
        self._sync_connections()
        self._reflect_programs()
        self._selection_changed()
        self._message(f"{'입력' if role == 'input' else '출력'} 파일을 등록했습니다: {path}")
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
            file_path.setText(reference.path if reference else "파일 미등록 — 연결은 입력 필요 상태로 남습니다.")
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
        self.workbench.refresh_native_project(self.project)
        if before != [(node.state, node.reason) for node in self.project.nodes.values()]:
            self._mark_dirty()
        self._reflect_programs()
        self._selection_changed()
        self._message("파일 상태를 다시 확인했습니다. 변경된 원본과 오래된 연결을 확인하세요.")

    def _reflect_programs(self) -> None:
        statuses = {status.spec.id: status for status in self.workbench.programs()}
        for node_id, box in self.boxes.items():
            node = self.project.nodes[node_id]
            program = statuses.get(node.program_id)
            box.subtitle = (f"{program.spec.name} — {'설치됨' if program.available else '미설치'}"
                            if program else "독립 프로그램 연결 미구현")
            reference = self.workbench.input_file(self.project, node_id)
            title = next((title for kind, title, sub in SYSTEM_KINDS if kind == node.kind), node.kind)
            identifying_file = reference or node.files.get("output")
            box.title = f"{title} — {Path(identifying_file.path).name}" if identifying_file else title
            box.file_label = Path(reference.path).name if reference else "선택 파일 없음"
            box.set_state("unavailable" if node.kind == "expert" else node.state, node.reason)
            box.setToolTip(f"{box.title}\n{box.subtitle}\n{reference.path if reference else '입력 파일 없음'}\n"
                           f"{self._display_reason(node.reason)}")
        self._poll_runs()

    def _poll_runs(self) -> None:
        if self.project.path is None:
            return
        signature = []
        for node_id, box in self.boxes.items():
            if box.kind != "analysis":
                continue
            try:
                run = self.workbench.selected_native_run(self.project, node_id)
                if run is None:
                    continue
                state = "stale" if run.stale and run.state == "completed" else run.state
                box.set_state(state, run.error or run.stale_reason)
                signature.append((node_id, run.id, run.state, run.parse_state, run.stale, run.error))
            except (OSError, ValueError, RuntimeError) as error:
                box.set_state("failed", f"실행 기록을 읽지 못했습니다: {error}")
                signature.append((node_id, str(error)))
        signature = tuple(signature)
        if signature != getattr(self, "_run_signature", None):
            self._run_signature = signature
            self._selection_changed()

    def _display_reason(self, reason: str) -> str:
        for node_id, box in self.boxes.items():
            reason = reason.replace(node_id, box.title)
        for state in ("changed", "missing", "stale"):
            reason = reason.replace(f": {state} —", f": {STATE_LABELS[state]} —")
        return reason.replace(".files.input", " 입력 파일").replace(".files.output", " 출력 파일")

    def _scene_double_click(self, event) -> None:
        item = self.scene.itemAt(event.scenePos(), self.view.transform())
        if isinstance(item, SystemBox):
            self.open_box(item)

    def open_selected(self) -> None:
        selected = self._selected_boxes()
        if len(selected) == 1:
            self.open_box(selected[0])

    def open_box(self, box: SystemBox) -> None:
        if box.kind == "expert":
            box.set_state("unavailable")
            self._message("독립 전문가 프로그램 연결은 아직 구현되지 않았습니다.")
            return
        if box.kind == "analysis" and (self._dirty or self.project.path is None):
            answer = QMessageBox.question(
                self, "별도 해석창 열기", "프로젝트를 저장한 뒤 별도 해석창을 엽니다.",
                QMessageBox.Save | QMessageBox.Cancel, QMessageBox.Save)
            if answer != QMessageBox.Save or not self.save_project():
                return
        try:
            launch = (self.workbench.open_native_analysis(self.project, box.node_id)
                      if box.kind == "analysis" else self.workbench.open_program(self.project, box.node_id))
        except Exception as error:
            self._mark_dirty()
            self._reflect_programs()
            self._selection_changed()
            self._error("프로그램을 열지 못했습니다", error)
            return
        if box.kind != "analysis" or launch.state == "failed":
            self._mark_dirty()
        if launch.state == "failed":
            self._message(f"프로그램을 열지 못했습니다: {launch.error}")
        else:
            self._message(f"{box.title} 프로그램 열기 요청을 보냈습니다 (PID {launch.pid}).")
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
