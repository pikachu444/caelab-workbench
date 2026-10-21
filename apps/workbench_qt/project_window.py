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
from caelab.workbench import Workbench

from .theme import THEME

SYSTEM_KINDS = [
    ("geometry", "형상", "FreeCAD에서 열기"),
    ("mesh", "메시", "메시 보기"),
    ("analysis", "구조 해석", "해석 창 열기 (CalculiX)"),
    ("results", "결과", "ParaView에서 열기"),
    ("expert", "전문가 논의", "전문가 패널 열기"),
]
STATE_COLORS = {
    "needs_input": "#c9ced4",
    "ready": "#7fb2e5",
    "running": "#f2b134",
    "done": "#3d9d63",
    "failed": "#d64545",
}
STATE_LABELS = {
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
        body = QColor("#2f6db3") if self.isSelected() else QColor("#fdfdfe")
        painter.setBrush(QBrush(body if self.isSelected() else QColor("#fdfdfe")))
        painter.setPen(QPen(QColor("#1668b4") if self.isSelected() else QColor("#8a97a5"), 1.6))
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
        a, b = self.src.right_anchor(), self.dst.left_anchor()
        path = QPainterPath(a)
        dx = max(40.0, abs(b.x() - a.x()) / 2)
        path.cubicTo(a + QPointF(dx, 0), b - QPointF(dx, 0), b)
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
        run_btn = QPushButton("전체 실행")
        run_btn.setObjectName("primary")
        run_btn.clicked.connect(self.run_chain)
        toolbar.addWidget(run_btn)
        toolbar.addWidget(QLabel("  상자를 더블클릭하면 그 프로그램의 작업 창이 열립니다."))

        toolbox = QListWidget()
        toolbox.setObjectName("toolbox")
        for _kind, title, sub in SYSTEM_KINDS:
            toolbox.addItem(f"{title} — {sub}")
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

    # -- schematic ------------------------------------------------------------
    def add_box(self, kind: str) -> SystemBox:
        title, sub = next((t, s) for k, t, s in SYSTEM_KINDS if k == kind)
        box = SystemBox(kind, title, sub)
        box.setPos(60 + 230 * (len(self.boxes) % 4), 60 + 130 * (len(self.boxes) // 4))
        self.scene.addItem(box)
        self.boxes[kind] = box
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
        for c in self.connectors:
            c.refresh()
        self.connect("geometry", "mesh")
        self.connect("mesh", "analysis")
        self.connect("analysis", "results")
        self.connect("results", "expert")
        self.boxes["geometry"].set_state("done", "demo.FCStd")
        self.boxes["mesh"].set_state("ready", "절점 99개 · 요소 40개")
        self.boxes["analysis"].set_state("ready", "CalculiX")
        self.boxes["results"].set_state("needs_input")
        self.boxes["expert"].set_state("needs_input")
        for c in self.connectors:
            c.refresh()
        self._log("프로젝트 체인을 만들었습니다: 형상 → 메시 → 구조 해석 → 결과 → 전문가 논의")

    # -- behaviour --------------------------------------------------------------
    def _log(self, text: str) -> None:
        self.log.append(text)

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
            self._log("형상은 FreeCAD에서 엽니다 (연결은 다음 단계).")
        elif box.kind == "results":
            self._log("결과는 ParaView에서 엽니다 (연결은 다음 단계).")
        elif box.kind == "expert":
            self._log("전문가 패널은 다음 단계에서 붙입니다.")
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
            self.boxes["analysis"].set_state("done", f"최대 변위 {fmt_length(result.max_displacement)}")
            self.boxes["results"].set_state("done", "결과 준비됨")
            self.boxes["expert"].set_state("ready", "논의 가능")
            self.status.setText("전체 실행 완료 — 결과 상자가 최신입니다. 다음 행동: 결과 열기 또는 전문가 논의")
            self._log(f"구조 해석 완료: 최대 변위 {fmt_length(result.max_displacement)}")
        else:
            self.boxes["analysis"].set_state("failed")
            self.status.setText("실행 실패 — 메시지 로그를 확인하세요.")
