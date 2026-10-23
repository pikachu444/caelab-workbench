"""Project window — the parent screen, modelled on ANSYS Workbench.

The workbench is a launcher/integration shell: independent systems
(geometry in FreeCAD, meshing, a solver run, results in ParaView, an
expert discussion) appear as boxes on a project schematic. Boxes are
connected in data-flow order, each box carries a state (needs input,
ready, running, up-to-date, failed), and double-clicking a box opens
its own workspace — the structural-analysis box opens the CAE main
window. Nothing here computes by itself; execution goes through the
single Workbench command path like every other surface.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDockWidget,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from caelab.model.schema import (
    BoundaryCondition,
    Load,
    Material,
    MeshData,
    NamedSet,
    Section,
    Step,
    StructuralModel,
)
from caelab.connections import launch_command, program_statuses
from caelab.workbench import Workbench

from .icons import icon
from .theme import THEME

_KIND_ICONS = {"geometry": "geometry", "mesh": "mesh", "analysis": "solver",
               "results": "result", "expert": "expert"}

SYSTEM_KINDS = [
    ("geometry", "형상", "FreeCAD에서 열기"),
    ("mesh", "메시", "메시 보기"),
    ("analysis", "구조 해석", "해석 창 열기 (CalculiX)"),
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
}
STATE_LABELS = {
    "unavailable": "연결 미구현",
    "needs_input": "입력 필요",
    "ready": "준비됨",
    "running": "실행 중",
    "done": "최신",
    "failed": "실패",
}
BOX_W, BOX_H = 190, 74


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


def demo_model() -> StructuralModel:
    """Pilot scaffolding: the beam used to exercise the schematic."""
    nx, ny, nz = 10, 2, 2
    length, width, height = 1.0, 0.1, 0.1
    nodes: dict[int, list[float]] = {}
    index: dict[tuple[int, int, int], int] = {}
    nid = 0
    for i in range(nx + 1):
        for j in range(ny + 1):
            for k in range(nz + 1):
                nid += 1
                index[(i, j, k)] = nid
                nodes[nid] = [length * i / nx, width * j / ny, height * k / nz]
    elements: dict[int, dict] = {}
    eid = 0
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                eid += 1
                elements[eid] = {"type": "C3D8", "nodes": [
                    index[(i + di, j + dj, k + dk)] for di, dj, dk in
                    [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                     (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]]}
    fixed = [index[(0, j, k)] for j in range(ny + 1) for k in range(nz + 1)]
    tip = [index[(nx, j, k)] for j in range(ny + 1) for k in range(nz + 1)]
    return StructuralModel(
        name="cantilever",
        geometry_source="demo.FCStd",
        sets={
            "FIX": NamedSet("node", fixed),
            "TIP": NamedSet("node", tip),
            "ALL_ELEMS": NamedSet("element", list(elements)),
        },
        mesh=MeshData(nodes=nodes, elements=elements),
        materials=[Material("STEEL", "linear_elastic",
                            {"youngs_modulus": 210e9, "poissons_ratio": 0.3,
                             "density": 7850.0})],
        sections=[Section("SOLID", "solid", "ALL_ELEMS", "STEEL")],
        steps=[Step("STEP1", "linear_static", loads=["TIP_FORCE"], bcs=["FIX_BC"])],
        loads=[Load("TIP_FORCE", "force", "TIP", {"fy": -1000.0})],
        bcs=[BoundaryCondition("FIX_BC", "FIX", [1, 2, 3])],
    )


class SystemBox(QGraphicsItem):
    def __init__(self, kind: str, title: str, subtitle: str) -> None:
        super().__init__()
        self.kind = kind
        self.title = title
        self.subtitle = subtitle
        self.state = "needs_input"
        self.detail = ""
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
        painter.drawText(QRectF(10, 3, BOX_W - 40, 20), Qt.AlignLeft, self.title)
        # state dot
        painter.setBrush(QBrush(QColor(STATE_COLORS[self.state])))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(BOX_W - 24, 7, 11, 11))
        painter.setPen(QPen(QColor("#33414f")))
        painter.setFont(_box_font(9))
        painter.drawText(QRectF(10, 30, BOX_W - 20, 18), Qt.AlignLeft, self.subtitle)
        painter.setPen(QPen(QColor("#5a6672")))
        status = STATE_LABELS[self.state] + (f" · {self.detail}" if self.detail else "")
        painter.drawText(QRectF(10, 50, BOX_W - 20, 18), Qt.AlignLeft, status)

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


class ProjectWindow(QMainWindow):
    def __init__(self, workbench: Workbench | None = None) -> None:
        super().__init__()
        self.workbench = workbench or Workbench()
        self.model = demo_model()
        self.boxes: dict[str, SystemBox] = {}
        self.connectors: list[Connector] = []
        self._workspace = None
        self.setWindowTitle("CAELAB Workbench — 프로젝트")
        self.resize(1280, 820)
        self.setStyleSheet(THEME)
        self._build_ui()
        self.build_demo_chain()

    def _build_ui(self) -> None:
        toolbar = QToolBar("프로젝트")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        run_btn = QPushButton(icon("run"), "전체 실행")
        run_btn.setObjectName("primary")
        run_btn.clicked.connect(self.run_chain)
        toolbar.addWidget(run_btn)
        fit_btn = QPushButton("도식 맞춤")
        fit_btn.setToolTip("도식 전체가 창 안에 들어오도록 맞춥니다")
        fit_btn.clicked.connect(self.fit_schematic)
        toolbar.addWidget(fit_btn)
        toolbar.addWidget(QLabel("  상자를 더블클릭하면 그 프로그램의 작업 창이 열립니다."))

        toolbox = QListWidget()
        toolbox.setObjectName("toolbox")
        from PySide6.QtWidgets import QListWidgetItem

        for kind, title, sub in SYSTEM_KINDS:
            item = QListWidgetItem(icon(_KIND_ICONS[kind]), f"{title} — {sub}")
            toolbox.addItem(item)
        toolbox.itemDoubleClicked.connect(
            lambda item: self.add_box(SYSTEM_KINDS[toolbox.row(item)][0]))
        dock = QDockWidget("도구상자 (더블클릭으로 추가)")
        dock.setWidget(toolbox)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.toolbox = toolbox

        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.view.setBackgroundBrush(QBrush(QColor("#eef1f5")))
        self.setCentralWidget(self.view)
        self.scene.mouseDoubleClickEvent = self._scene_double_click

        bottom = QWidget()
        layout = QVBoxLayout(bottom)
        self.status = QLabel("프로젝트를 만들었습니다. 다음 행동: 전체 실행")
        layout.addWidget(self.status)
        self.log = QTextEdit()
        self.log.setObjectName("logView")
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(90)
        layout.addWidget(self.log)
        dock2 = QDockWidget("메시지")
        dock2.setWidget(bottom)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock2)
        self.statusBar().showMessage("상위 화면: 프로젝트 (런처) · 실행은 Workbench 단일 경로")

    # -- view fitting ---------------------------------------------------------
    def fit_schematic(self) -> None:
        """Fit the whole schematic in the view.

        The default chain is wider than a laptop window, so without a
        fit the right-hand boxes start out clipped behind a scrollbar.
        """
        rect = self.scene.itemsBoundingRect().adjusted(-30, -30, 30, 30)
        if not rect.isEmpty():
            self.view.fitInView(rect, Qt.KeepAspectRatio)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        if not getattr(self, "_fitted_once", False):
            self._fitted_once = True
            self.fit_schematic()

    # -- schematic ------------------------------------------------------------
    def add_box(self, kind: str) -> SystemBox:
        existing = self.boxes.get(kind)
        if existing is not None:
            # One box per system kind: adding the same kind twice used
            # to leave an orphaned box on the canvas that the chain
            # and the connectors no longer knew about.
            self.scene.clearSelection()
            existing.setSelected(True)
            self.view.centerOn(existing)
            self._log(f"{existing.title} 상자는 이미 도식에 있습니다.")
            return existing
        title, sub = next((t, s) for k, t, s in SYSTEM_KINDS if k == kind)
        box = SystemBox(kind, title, sub)
        box.setPos(60 + 230 * (len(self.boxes) % 4), 60 + 130 * (len(self.boxes) // 4))
        self.scene.addItem(box)
        self.boxes[kind] = box
        if kind == "expert":
            box.set_state("unavailable")
        return box

    def connect(self, src: str, dst: str) -> None:
        connector = Connector(self.boxes[src], self.boxes[dst])
        self.scene.addItem(connector)
        self.connectors.append(connector)

    def build_demo_chain(self) -> None:
        for kind, _t, _s in SYSTEM_KINDS:
            self.add_box(kind)
        positions = {"geometry": (40, 60), "mesh": (300, 60), "analysis": (560, 60),
                     "results": (820, 60), "expert": (820, 220)}
        for kind, (x, y) in positions.items():
            self.boxes[kind].setPos(x, y)
        self.connect("geometry", "mesh")
        self.connect("mesh", "analysis")
        self.connect("analysis", "results")
        self.boxes["geometry"].set_state("done", "demo.FCStd")
        self.boxes["mesh"].set_state("ready", "절점 99개 · 요소 40개")
        self.boxes["analysis"].set_state("ready", "CalculiX")
        self.boxes["results"].set_state("needs_input")
        self.boxes["expert"].set_state("unavailable")
        self._reflect_programs()
        for c in self.connectors:
            c.refresh()
        self._log("프로젝트 체인을 만들었습니다: 형상 → 메시 → 구조 해석 → 결과. 독립 전문가 연결은 미구현입니다.")

    # -- behaviour --------------------------------------------------------------
    def _log(self, text: str) -> None:
        self.log.append(text)

    def _reflect_programs(self) -> None:
        """Show, on the boxes themselves, whether the program behind
        each box is actually found on this machine. The workbench's
        main line is connecting real programs, so a box whose program
        is missing must say so instead of pretending — in its state
        colour as well as its text (a green "up-to-date" dot next to
        "program not found" contradicts itself)."""
        statuses = {s.spec.id: s for s in program_statuses()}

        def reflect(kind: str, program_id: str, name: str,
                    ok_state: str, ok_detail: str) -> None:
            status = statuses.get(program_id)
            if status is None or kind not in self.boxes:
                return
            if status.available:
                self.boxes[kind].set_state(ok_state, ok_detail)
            else:
                self.boxes[kind].set_state("needs_input", f"{name}를 찾지 못함")

        reflect("geometry", "freecad", "FreeCAD", "done", "demo.FCStd")
        reflect("mesh", "gmsh", "Gmsh", "ready", "절점 99개 · 요소 40개")
        reflect("analysis", "calculix", "CalculiX", "ready", "CalculiX")
        pv = statuses.get("paraview")
        if pv is not None and not pv.available and "results" in self.boxes:
            self.boxes["results"].set_state("needs_input", "ParaView를 찾지 못함")

    def _open_external(self, kind: str, program_id: str, file: str | None) -> None:
        """Open a box's own program when it is installed."""
        statuses = {s.spec.id: s for s in program_statuses()}
        status = statuses.get(program_id)
        if status is None:
            self._log(f"{program_id} 프로그램 정보를 찾지 못해 열 수 없습니다.")
            return
        command = launch_command(status, file)
        if command is None:
            self._log(
                f"{status.spec.name}가 이 컴퓨터에서 발견되지 않아 열 수 없습니다. "
                "설치 후 다시 시도해 주세요.")
            return
        import subprocess

        try:
            subprocess.Popen(command)
            self._log(f"{status.spec.name}를 실행했습니다.")
        except OSError as exc:
            self._log(f"{status.spec.name} 실행 실패 — {exc}")

    def _scene_double_click(self, event) -> None:
        item = self.scene.itemAt(event.scenePos(), self.view.transform())
        if isinstance(item, SystemBox):
            self.open_box(item)

    def open_box(self, box: SystemBox) -> None:
        if box.kind == "analysis":
            from .main_window import MainWindow

            self._workspace = MainWindow(self.workbench)
            self._workspace.set_model(self.model)
            self._workspace.show()
            self._log("구조 해석 작업 창을 열었습니다.")
        elif box.kind == "geometry":
            self._open_external(box.kind, "freecad", "demo.FCStd")
        elif box.kind == "results":
            self._open_external(box.kind, "paraview", None)
        elif box.kind == "mesh":
            self._open_external(box.kind, "gmsh", None)
        elif box.kind == "expert":
            box.set_state("unavailable")
            message = "독립 전문가 프로그램 연결은 아직 구현되지 않았습니다."
            self.status.setText(message)
            self._log(message)
        else:
            self._log(f"{box.title} 상자: 전용 화면은 다음 단계입니다.")

    def run_chain(self) -> None:
        from .main_window import fmt_length

        self.boxes["mesh"].set_state("done", "절점 99개 · 요소 40개")
        self.boxes["analysis"].set_state("running")
        workdir = Path(tempfile.mkdtemp(prefix="caelab-proj-"))
        job = self.workbench.run(self.model, "calculix", workdir)
        if job.result is not None and job.result.success:
            result = job.result
            self.boxes["analysis"].set_state("done", f"최대 변위 {fmt_length(result.max_displacement, result.units.get('length'))}")
            self.boxes["results"].set_state("done", "결과 준비됨")
            self.boxes["expert"].set_state("unavailable")
            self.status.setText("전체 실행 완료 — 결과 상자가 최신입니다. 다음 행동: 결과 확인")
            self._log(f"구조 해석 완료: 최대 변위 {fmt_length(result.max_displacement, result.units.get('length'))}")
        else:
            self.boxes["analysis"].set_state("failed")
            self.status.setText("실행 실패 — 메시지 로그를 확인하세요.")
            if job.result is not None and job.result.log_tail:
                # Say why it failed, not only that it failed.
                self._log(job.result.log_tail[-800:])
