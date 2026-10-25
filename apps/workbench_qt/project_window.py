"""Project window — the parent screen, modelled on ANSYS Workbench.

The workbench is a launcher/integration shell: independent systems
(geometry in FreeCAD, meshing, a solver run, results in ParaView, an
expert discussion) appear as boxes on a project schematic. Boxes are
connected through selected native files. Double-clicking assigns missing
input or opens the independent program. The CalculiX box opens a separate input and
execution window; recorded runs can be inspected from this project.
"""

from __future__ import annotations

from html import escape
from os.path import normcase
from pathlib import Path
import math
import errno
import re

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
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
    QMenu,
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
    ("geometry", "FreeCAD", "형상·입력 작성"),
    ("mesh", "Gmsh", "메시 작성"),
    ("analysis", "CalculiX", "해석"),
    ("results", "ParaView", "결과 보기"),
    ("expert", "전문가", "논의"),
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
    "ready": "",
    "running": "실행 중",
    "done": "최신",
    "failed": "실패",
    "changed": "원본 변경됨",
    "missing": "파일 누락",
    "stale": "파일 갱신 필요",
    "opened": "열기 요청 전달",
    "queued": "대기", "preparing": "입력 보존 중", "completed": "프로세스 종료",
    "cancelled": "취소됨", "interrupted": "중단됨",
}
BOX_W, BOX_H = 210, 98


class SchematicView(QGraphicsView):
    """Notify the fixed view controls whenever the scene transform changes."""

    scaleChanged = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._layout_scroll = None
        self._window_size = None
        self._resizing = False
        for bar in (self.horizontalScrollBar(), self.verticalScrollBar()):
            bar.valueChanged.connect(self._remember_scroll)

    def _remember_scroll(self, *_):
        if not self._resizing:
            self._layout_scroll = (self.horizontalScrollBar().value(), self.verticalScrollBar().value())

    def scale(self, sx, sy):
        self._layout_scroll = None
        super().scale(sx, sy)
        self._remember_scroll()
        self.scaleChanged.emit()

    def resetTransform(self):
        self._layout_scroll = None
        super().resetTransform()
        self._remember_scroll()
        self.scaleChanged.emit()

    def setTransform(self, matrix, combine=False):
        self._layout_scroll = None
        super().setTransform(matrix, combine)
        self._remember_scroll()
        self.scaleChanged.emit()

    def fitInView(self, *args):
        self._layout_scroll = None
        super().fitInView(*args)
        self._remember_scroll()
        self.scaleChanged.emit()

    def place_controls(self):
        if hasattr(self, 'controls'):
            self.controls.adjustSize()
            self.controls.move(max(0, self.viewport().width() - self.controls.width() - 8),
                               max(0, self.viewport().height() - self.controls.height() - 8))

    def resizeEvent(self, event):
        was_resizing = self._resizing
        window_size = self.window().size()
        saved = self._layout_scroll if not was_resizing and window_size == self._window_size else None
        self._window_size = window_size
        self._resizing = True
        try:
            super().resizeEvent(event)
            if saved is not None:
                # A dock can make the scene fit and clamp its scrollbars to zero.
                # Keep the wanted position until the same layout can show it again.
                for bar, position in zip((self.horizontalScrollBar(), self.verticalScrollBar()), saved):
                    bar.setValue(position)
            elif not was_resizing:
                self._layout_scroll = (self.horizontalScrollBar().value(), self.verticalScrollBar().value())
        finally:
            self._resizing = was_resizing
        self.place_controls()


def _fit_icon():
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setPen(QPen(QColor('#33414f'), 1.6))
    for x, y, dx, dy in ((3, 3, 1, 1), (17, 3, -1, 1), (3, 17, 1, -1), (17, 17, -1, -1)):
        painter.drawLine(x, y, x + 5 * dx, y)
        painter.drawLine(x, y, x, y + 5 * dy)
    painter.end()
    return QIcon(pixmap)


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
        self.choice_title = None
        self.subtitle = subtitle
        self.state = "needs_input"
        self.status_label = ""
        self.status_warning = False
        self.status_error = False
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
        self.status_warning = False
        self.status_error = False
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
        painter.setFont(_box_font(11, bold=True))
        painter.drawText(QRectF(10, 3, BOX_W - 40, 20), Qt.AlignLeft,
                         painter.fontMetrics().elidedText(self.choice_title or self.title, Qt.ElideRight, BOX_W - 40))
        # state dot
        painter.setBrush(QBrush(QColor('#d64545' if self.status_error else '#c28422' if self.status_warning else STATE_COLORS.get(self.state, "#c9ced4"))))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(BOX_W - 24, 7, 11, 11))
        painter.setPen(QPen(QColor("#33414f")))
        painter.setFont(_box_font(10))
        painter.drawText(QRectF(10, 32, BOX_W - 20, 18), Qt.AlignLeft,
                         painter.fontMetrics().elidedText(self.file_label, Qt.ElideMiddle, BOX_W - 20))
        painter.drawText(QRectF(10, 51, BOX_W - 20, 18), Qt.AlignLeft,
                         painter.fontMetrics().elidedText(self.subtitle, Qt.ElideRight, BOX_W - 20))
        painter.setPen(QPen(QColor('#a12e2e' if self.status_error or self.state in {'failed', 'missing'} else
                                  '#875212' if self.status_warning or self.state in {'stale', 'changed', 'cancelled', 'interrupted'} else '#344d65')))
        painter.setFont(_box_font(9, bold=bool(self.status_label)))
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

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        path = self.path()
        end, prior = path.pointAtPercent(1), path.pointAtPercent(.96)
        angle = math.atan2(end.y() - prior.y(), end.x() - prior.x())
        back = QPointF(math.cos(angle) * 11, math.sin(angle) * 11)
        side = QPointF(-math.sin(angle) * 5, math.cos(angle) * 5)
        painter.setBrush(self.pen().color())
        painter.drawPolygon(QPolygonF([end, end - back + side, end - back - side]))

    def boundingRect(self):
        return super().boundingRect().adjusted(-6, -6, 6, 6)

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


def _problem_text(error) -> str:
    """Recognize OS failures and actual service messages, never filename keywords."""
    permission = '파일이나 프로그램에 접근할 권한이 없습니다. 접근 가능한 위치와 실행 권한을 확인하세요.'
    missing = '사용할 파일을 찾지 못했습니다. 파일을 다시 선택하거나 연결한 상위 파일을 확인하세요.'
    space = '저장 공간이 부족합니다. 공간을 확보하거나 다른 위치에 저장하세요.'
    # Preserve typed causes even when a project/service boundary wraps an OS error.
    cause, seen, causes = error, set(), []
    while isinstance(cause, BaseException) and id(cause) not in seen:
        seen.add(id(cause))
        causes.append(cause)
        if isinstance(cause, PermissionError) or getattr(cause, 'winerror', None) == 5:
            return permission
        if isinstance(cause, FileNotFoundError) or getattr(cause, 'winerror', None) in {2, 3}:
            return missing
        if isinstance(cause, OSError) and (cause.errno == errno.ENOSPC or getattr(cause, 'winerror', None) == 112):
            return space
        cause = cause.__cause__
    # A project-read wrapper must not replace a known version/configuration cause.
    # Unknown inner causes still fall back to the useful outer service context.
    for source in reversed(causes or [error]):
        message = _problem_message(str(source).strip())
        if message is not None:
            return message
    return '문제의 원인을 확인하지 못했습니다. 선택한 파일과 전용 프로그램의 작업 상태를 확인하세요.'


def _problem_message(text: str) -> str | None:
    permission = '파일이나 프로그램에 접근할 권한이 없습니다. 접근 가능한 위치와 실행 권한을 확인하세요.'
    missing = '사용할 파일을 찾지 못했습니다. 파일을 다시 선택하거나 연결한 상위 파일을 확인하세요.'
    space = '저장 공간이 부족합니다. 공간을 확보하거나 다른 위치에 저장하세요.'
    # Launch records serialize exceptions. Match the error number at the start,
    # never a path or an incidental token later in the diagnostic.
    serialized = re.match(r'^(?:(?:PermissionError|FileNotFoundError|OSError): )?\[(WinError|Errno) (\d+)\](?: |$)', text)
    if serialized:
        family, code = serialized.group(1), int(serialized.group(2))
        if (family, code) in {('WinError', 5), ('Errno', errno.EACCES), ('Errno', errno.EPERM)}:
            return permission
        if (family, code) in {('WinError', 2), ('WinError', 3), ('Errno', errno.ENOENT)}:
            return missing
        if (family, code) in {('WinError', 112), ('Errno', errno.ENOSPC)}:
            return space
    if text.lower() in {'disk full', 'no space left on device'}:
        return space
    for prefix in ('ValueError: ', 'RuntimeError: '):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    exact = {
        '설치 레지스트리의 runtime_env 형식이 잘못되었습니다.': '프로그램 연결 설정의 형식이 올바르지 않습니다. 연결 설정을 확인하세요.',
        '프로젝트 저장 경로가 원본 파일과 같습니다.': '프로젝트는 원본 입력 파일과 다른 이름으로 저장하세요.',
        '다른 이름 저장에는 기존 실행 저장소와 분리된 경로가 필요합니다.': '기존 계산·대화 기록 폴더 안에는 다른 이름으로 저장할 수 없습니다. 그 폴더 밖의 새 프로젝트 위치를 선택하세요.',
        '실행 저장소 안으로 자기 복사를 할 수 없습니다.': '기존 계산·대화 기록 폴더 안에는 복사할 수 없습니다. 그 폴더 밖의 새 프로젝트 위치를 선택하세요.',
        '다른 이름 저장의 실행 저장소가 이미 있습니다. 기존 기록을 보호하기 위해 다른 이름을 선택하세요.': '선택한 저장 위치에 기존 기록이 있습니다. 다른 이름이나 새 폴더를 선택하세요.',
        '복사 준비 중 대화 목록이 변경되었습니다. 다시 저장하세요.': '저장 준비 중 대화 목록이 바뀌었습니다. 대화 작업을 마친 뒤 다시 저장하세요.',
        '복사 중 실행/대화/자산 파일이 변경되었습니다. 다시 저장하세요.': '저장 중 계산이나 대화 자료가 바뀌었습니다. 관련 작업을 마친 뒤 다시 저장하세요.',
        '대화 질문/저장이 진행 중입니다. 완료 후 다른 이름으로 저장하세요.': '대화 작업이 진행 중입니다. 작업을 마치거나 중단한 뒤 다시 저장하세요.',
        '이 프로젝트의 다른 창/세션에서 계산 중입니다. 해당 계산을 끝내거나 취소하세요.': '다른 해석창에서 계산 중입니다. 해당 계산을 끝내거나 취소하세요.',
        '계산 또는 결과 판독을 끝낸 뒤 프로그램을 제거하세요.': '계산이나 결과 읽기가 진행 중입니다. 해석창에서 완료하거나 중단한 뒤 제거하세요.',
        'connections: 순환 연결은 지원하지 않습니다.': '상자를 순환하게 연결할 수 없습니다. 입력이 한 방향으로 전달되도록 연결하세요.',
        '실행 근거 연결이 순환합니다.': '상자를 순환하게 연결할 수 없습니다. 입력이 한 방향으로 전달되도록 연결하세요.',
        'FreeCAD 문서(.FCStd)를 선택하세요.': 'FreeCAD 문서(.FCStd)를 입력 파일로 선택하세요.',
        '연결된 해석 노드에서 실행을 선택하세요.': '해석 상자를 연결하고 해석창에서 사용할 계산을 선택하세요.',
        '현재 선택 입력이 이 실행의 입력과 다릅니다.': '선택한 계산은 현재 입력과 다른 입력으로 실행되었습니다. 해석창에서 사용할 계산을 확인하세요.',
        '입력 단위 선언/출처가 변경되었습니다.': '입력의 단위 선언이 계산 당시와 다릅니다. 해석창에서 단위와 사용할 계산을 확인하세요.',
        '실행 원본 또는 INCLUDE 파일이 변경/누락되었습니다.': '계산에 사용한 입력이나 포함 파일이 바뀌었거나 없습니다. 해석창에서 입력 파일을 확인하세요.',
        '상위 변경으로 현재 입력 덱이 오래되었습니다.': '입력 파일 갱신이 필요합니다. 사용할 출력을 확인하고 다시 지정하세요.',
        '선택 실행에 사용할 수 있는 최신 결과가 없습니다.': '선택한 계산에 사용할 수 있는 결과가 없습니다. 해석창에서 종료 상태와 결과 읽기 상태를 확인하세요.',
        '선택하거나 연결한 입력 파일이 없습니다. 파일 경로를 확인하세요.': missing,
        '연결된 입력 파일이 없습니다.': missing,
        '선택하거나 연결한 INP 파일이 없습니다.': missing,
        '입력 확인 창을 열기 전에 프로젝트를 저장하세요.': '프로젝트를 먼저 저장한 뒤 프로그램을 여세요.',
        '프로젝트를 저장하고 전문가 상자를 선택하세요.': '프로젝트를 저장하고 전문가 상자를 선택하세요.',
        'FreeCAD GUI에는 freecadcmd를 사용할 수 없습니다. GUI 실행 파일을 지정하세요.': '화면이 있는 FreeCAD 프로그램을 연결하세요. 명령행 도구로는 파일을 표시할 수 없습니다.',
        'ParaView GUI에는 pvpython/pvbatch를 사용할 수 없습니다. paraview 실행 파일을 지정하세요.': '화면이 있는 ParaView 프로그램을 연결하세요. 명령행 도구로는 파일을 표시할 수 없습니다.',
        '선택 실행에 열 수 있는 PVD/VTU 결과 파일이 없습니다.': '선택한 계산에 ParaView에서 열 수 있는 결과 파일이 없습니다. 해석창에서 결과 읽기 상태를 확인하거나 다른 계산을 선택하세요.',
        '교환 파일 무결성을 확인할 수 없습니다. 새로 계산한 실행을 선택하세요.': '결과 파일의 보존 상태를 확인할 수 없습니다. 새로 계산한 실행을 선택하세요.',
    }
    if text in exact:
        return exact[text]
    groups = (
        (('검사 중 저장소 파일이 변경되었습니다: ',), '저장 자료를 확인하는 동안 파일이 바뀌었습니다. 관련 작업을 마친 뒤 다시 저장하세요.'),
        (('기대 실행 저장소가 없습니다: ', '실행 저장소가 폴더가 아닙니다: ', '실행 기록을 온전히 읽을 수 없습니다: ',
          '실행 기록 객체가 필요합니다: ', '잘못된 실행 기록 ID: ', '잘못된 실행 상태: ', '선택한 실행 기록이 없습니다: ',
          '실행 폴더의 기록이 없습니다: ', '실행 기록 읽기 실패: ', '대화 근거 자산이 온전하지 않습니다: '),
         '저장된 계산·대화 기록이 없거나 온전히 읽히지 않습니다. 프로젝트와 함께 보관한 기록 폴더를 확인하세요.'),
        (('지원하지 않는 프로젝트 schema_version: ',), '이 프로그램에서 읽을 수 없는 프로젝트 버전입니다. 호환되는 버전에서 다시 여세요.'),
        (('프로젝트 읽기 실패 (',), '프로젝트의 파일·상자·연결 정보를 읽지 못했습니다. 올바른 프로젝트 파일이나 보관한 사본을 선택하세요.'),
        (('입력 파일이 없습니다: ',), missing),
        (('실행 권한이 없습니다: ',), permission),
        (('실행 파일이 없거나 일반 파일이 아닙니다: ',), '프로그램 실행 파일을 찾지 못했거나 사용할 수 없습니다. 설치와 프로그램 연결 설정을 확인하세요.'),
        (('교환 파일이 없습니다: ', '교환 파일 해시가 일치하지 않습니다: ', 'PVD를 읽을 수 없습니다: '),
         '결과 파일이 없거나 온전히 읽히지 않습니다. 해석창에서 다른 보존 실행을 선택하거나 새로 계산하세요.'),
        (('Windows 네이티브 실행 파일이 아닙니다. WSL/스크립트 연결은 미구현입니다: ',), '현재 환경에서 사용할 수 있는 Windows 프로그램을 연결하세요.'),
        (('설치 레지스트리를 읽을 수 없습니다: ', '설치 root가 없어 레지스트리 상대경로를 해석할 수 없습니다: ',
          '설치 root 접두부와 맞지 않는 레지스트리 상대경로입니다: ', '다른 호스트의 설치 레지스트리입니다: '),
         '프로그램 연결 설정을 읽지 못했습니다. 설치와 연결 설정을 확인하세요.'),
    )
    for prefixes, message in groups:
        if text.startswith(prefixes):
            return message
    if re.fullmatch(r'(?:FreeCAD|Gmsh|CalculiX|ParaView|Code_Aster|OpenCourant|FEniCSx) 실행 파일(?:을 찾지 못했습니다 \([^\n]*\)|이 없습니다)\.', text):
        return '프로그램 실행 파일을 찾지 못했거나 사용할 수 없습니다. 설치와 프로그램 연결 설정을 확인하세요.'
    return None


def _run_text(run) -> str:
    if run.state == 'completed':
        if run.parse_state == 'failed':
            return '계산 종료 · 결과 읽기 실패'
        if run.parse_state in {'missing', 'unsupported'}:
            return '계산 종료 · 결과 없음' if run.parse_state == 'missing' else '계산 종료 · 읽을 수 없는 결과'
        if run.parse_state == 'reading':
            return '결과 읽는 중'
        if run.stale:
            return '이전 입력의 결과'
        return {'readable': '결과 있음', 'partial': '일부 결과'}.get(run.parse_state, '계산 종료 · 결과 미확인')
    return {'queued': '대기', 'preparing': '입력 준비 중', 'running': '계산 중',
            'failed': '계산 실패', 'cancelled': '취소됨', 'interrupted': '중단됨'}.get(run.state, '상태 미확인')




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
        self.add_program_menu = QMenu('프로그램 추가', self)
        self.add_program_actions = {}
        for kind, title, subtitle in SYSTEM_KINDS:
            action = self.add_program_menu.addAction(icon(_KIND_ICONS[kind]), title)
            action.triggered.connect(lambda checked=False, kind=kind: self.add_box(kind))
            self.add_program_actions[kind] = action
        self.add_program_button = QToolButton()
        self.add_program_button.setText('프로그램 추가')
        self.add_program_button.setIcon(icon('geometry'))
        self.add_program_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.add_program_button.setPopupMode(QToolButton.InstantPopup)
        self.add_program_button.setMenu(self.add_program_menu)
        toolbar.addWidget(self.add_program_button)
        self.edit_menu = self.menuBar().addMenu('편집(&E)')
        self.edit_menu.addMenu(self.add_program_menu)
        self.connect_action = toolbar.addAction("연결…")
        self.connect_action.setToolTip("프로그램을 선택하고 보내는 파일과 받는 프로그램을 지정합니다")
        self.connect_action.triggered.connect(self.connect_selected)
        self.edit_menu.addAction(self.connect_action)
        self.disconnect_action = self.edit_menu.addAction('입력 연결 해제')
        self.disconnect_action.triggered.connect(self.disconnect_selected)
        self.remove_action = self.edit_menu.addAction('프로젝트에서 제거')
        self.remove_action.setShortcut(QKeySequence.Delete)
        self.remove_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.remove_action.triggered.connect(self.remove_selected)
        self.restore_action = self.edit_menu.addAction('제거한 프로그램 복원…')
        self.restore_action.triggered.connect(self.restore_program)
        refresh = toolbar.addAction("파일 상태 새로고침")
        refresh.setShortcut(QKeySequence.Refresh)
        refresh.triggered.connect(self.refresh_files)

        view_menu = self.view_menu = self.menuBar().addMenu("보기(&V)")
        self.toolbox = QListWidget()
        self.toolbox.setObjectName("toolbox")
        from PySide6.QtWidgets import QListWidgetItem
        for kind, title, sub in SYSTEM_KINDS:
            self.toolbox.addItem(QListWidgetItem(icon(_KIND_ICONS[kind]), f"{title}"))
        self.toolbox.itemClicked.connect(
            lambda item: self.add_box(SYSTEM_KINDS[self.toolbox.row(item)][0]))
        tools = self.toolbox_dock = QDockWidget("프로그램", self)
        tools.setWidget(self.toolbox)
        self.addDockWidget(Qt.LeftDockWidgetArea, tools)
        view_menu.addAction(tools.toggleViewAction())
        tools.hide()

        self.scene = QGraphicsScene(self)
        self.view = SchematicView(self.scene)
        self.view.addAction(self.remove_action)
        self.view.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.view.setViewportMargins(24, 24, 8, 8)
        self.view.setResizeAnchor(QGraphicsView.NoAnchor)
        self.view.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.view.setBackgroundBrush(QBrush(QColor("#eef1f5")))
        self.setCentralWidget(self.view)
        self.zoom_in_action = view_menu.addAction('확대')
        self.zoom_in_action.setShortcut(QKeySequence.ZoomIn)
        self.zoom_in_action.triggered.connect(lambda: self.view.scale(1.2, 1.2))
        self.zoom_out_action = view_menu.addAction('축소')
        self.zoom_out_action.setShortcut(QKeySequence.ZoomOut)
        self.zoom_out_action.triggered.connect(lambda: self.view.scale(1 / 1.2, 1 / 1.2))
        self.fit_action = view_menu.addAction('화면에 맞추기')
        self.fit_action.triggered.connect(self.fit_schematic)
        self.reset_view_action = view_menu.addAction('100%로 보기')
        self.reset_view_action.triggered.connect(self.reset_view)
        view_menu.addSeparator()
        controls = self.view.controls = QWidget(self.view.viewport())
        controls.setObjectName('canvasControls')
        controls.setStyleSheet('''
            QWidget#canvasControls { background: #f8fafc; border: 1px solid #bdc7d1; border-radius: 4px; }
            QToolButton { min-width: 24px; min-height: 24px; padding: 2px 4px; }
            QToolButton#zoomPercentage { min-width: 50px; max-width: 50px; }
        ''')
        control_row = QHBoxLayout(controls)
        control_row.setContentsMargins(3, 3, 3, 3)
        control_row.setSpacing(2)
        self.zoom_out_button = QToolButton()
        self.zoom_out_button.setDefaultAction(self.zoom_out_action)
        self.zoom_out_button.setText('−')
        self.zoom_percent = QToolButton()
        self.zoom_percent.setObjectName('zoomPercentage')
        self.zoom_percent.setToolTip('100%로 보기')
        self.zoom_percent.setAccessibleName('현재 배율, 누르면 100%로 보기')
        self.zoom_percent.clicked.connect(self.reset_view)
        self.zoom_in_button = QToolButton()
        self.zoom_in_button.setDefaultAction(self.zoom_in_action)
        self.zoom_in_button.setText('+')
        self.fit_button = QToolButton()
        self.fit_button.setDefaultAction(self.fit_action)
        self.fit_button.setIcon(_fit_icon())
        self.fit_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        for button, label in ((self.zoom_out_button, '축소'), (self.zoom_in_button, '확대'),
                              (self.fit_button, '화면에 맞추기')):
            button.setToolTip(label)
            button.setAccessibleName(label)
        for button in (self.zoom_out_button, self.zoom_percent, self.zoom_in_button, self.fit_button):
            button.setFocusPolicy(Qt.StrongFocus)
            control_row.addWidget(button)
        self.view.scaleChanged.connect(self._update_zoom_display)
        self._update_zoom_display()
        self.scene.mouseDoubleClickEvent = self._scene_double_click
        self.scene.selectionChanged.connect(self._selection_changed)
        self.empty_hint = self.scene.addText(
            "프로그램 추가로 작업을 시작하세요",
            _box_font(12))
        self.empty_hint.setDefaultTextColor(QColor("#5a6672"))
        self.empty_hint.setPos(40, 40)

        props_widget = QWidget()
        props_widget.setObjectName('selectionSettings')
        props_widget.setStyleSheet('''
            QWidget#selectionSettings { background: #fafbfd; }
            QWidget#selectionSettings QPushButton { padding: 3px 7px; }
            QWidget#selectionSettings QLineEdit { border: none; background: #edf1f5; padding: 5px; }
            QWidget#selectionSettings QToolButton { padding: 3px 6px; }
        ''')
        props_layout = QVBoxLayout(props_widget)
        props_layout.setSpacing(12)
        props_layout.setContentsMargins(10, 10, 10, 10)
        self.selection_name = QLabel()
        title_font = self.selection_name.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 1)
        self.selection_name.setFont(title_font)
        self.selection_name.setWordWrap(True)
        header = QHBoxLayout()
        header.addWidget(self.selection_name, 1)
        self.open_button = QPushButton('열기')
        self.open_button.clicked.connect(self.open_selected)
        header.addWidget(self.open_button)
        props_layout.addLayout(header)
        self.program_summary = QLabel()
        self.program_summary.setWordWrap(True)
        self.program_summary.setTextFormat(Qt.PlainText)
        props_layout.addWidget(self.program_summary)
        self.selection_connect_button = QPushButton('연결…')
        self.selection_connect_button.clicked.connect(self.connect_selected)
        props_layout.addWidget(self.selection_connect_button)
        self.settings_body = QWidget()
        fields = QVBoxLayout(self.settings_body)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(8)
        self.input_summary = QLabel()
        self.input_value = QLineEdit()
        self.input_value.setReadOnly(True)
        self.input_button = QPushButton('지정…')
        self.input_button.setAccessibleName('입력 파일 지정')
        self.input_button.clicked.connect(lambda: self.choose_file('input'))
        self.input_units_button = QPushButton('단위…')
        self.input_units_button.clicked.connect(lambda: self.edit_units('input'))
        self.owner_button = QPushButton()
        self.owner_button.clicked.connect(self.select_input_owner)
        self.output_summary = QLabel()
        self.output_value = QLineEdit()
        self.output_value.setReadOnly(True)
        self.output_button = QPushButton('지정…')
        self.output_button.setAccessibleName('작성 출력 지정')
        self.output_button.clicked.connect(lambda: self.choose_file('output'))
        self.output_units_button = QPushButton('단위…')
        self.output_units_button.clicked.connect(lambda: self.edit_units('output'))
        self.input_units_value = QLabel()
        self.output_units_value = QLabel()
        self.input_more_button = QToolButton()
        self.input_more_button.setText('연결')
        self.input_more_button.setAccessibleName('입력 파일과 연결 명령')
        self.input_more_button.setToolTip('입력 파일과 연결 명령')
        self.input_more_button.setPopupMode(QToolButton.InstantPopup)
        input_menu = QMenu(self)
        self.replace_input_action = input_menu.addAction('직접 파일로 바꾸기…')
        self.replace_input_action.triggered.connect(lambda: self.choose_file('input'))
        input_menu.addAction(self.disconnect_action)
        self.input_more_button.setMenu(input_menu)
        for summary, value, button, units in (
                (self.input_summary, self.input_value, self.input_button, self.input_units_button),
                (self.output_summary, self.output_value, self.output_button, self.output_units_button)):
            summary.setTextFormat(Qt.PlainText)
            summary.setWordWrap(True)
            section_widget = QWidget()
            section = QVBoxLayout(section_widget)
            section.setContentsMargins(0, 0, 0, 0)
            section.setSpacing(4)
            if summary is self.input_summary:
                self.input_section = section_widget
            else:
                self.output_section = section_widget
            heading = QHBoxLayout()
            heading.addWidget(summary, 1)
            heading.addWidget(button)
            if summary is self.input_summary:
                heading.addWidget(self.input_more_button)
            section.addLayout(heading)
            section.addWidget(value)
            units_widget = QWidget()
            row = QHBoxLayout(units_widget)
            row.setContentsMargins(0, 0, 0, 0)
            if summary is self.input_summary:
                self.input_units_row = units_widget
            else:
                self.output_units_row = units_widget
            row.addWidget(QLabel('단위'))
            unit_value = self.input_units_value if summary is self.input_summary else self.output_units_value
            unit_value.setWordWrap(True)
            row.addWidget(unit_value, 1)
            row.addWidget(units)
            section.addWidget(units_widget)
            if summary is self.input_summary:
                section.addWidget(self.owner_button, 0, Qt.AlignLeft)
            fields.addWidget(section_widget)
        self.run_summary = QLabel()
        self.run_summary.setWordWrap(True)
        self.run_summary.setTextFormat(Qt.PlainText)
        fields.addWidget(self.run_summary)
        props_layout.addWidget(self.settings_body)
        props_layout.addStretch()
        props_scroll = QScrollArea()
        props_scroll.setWidgetResizable(True)
        props_scroll.setFrameShape(QScrollArea.NoFrame)
        props_scroll.setWidget(props_widget)
        props_scroll.setMinimumWidth(250)
        self.settings_dock = QDockWidget('선택 설정', self)
        self.settings_dock.setWidget(props_scroll)
        self.addDockWidget(Qt.RightDockWidgetArea, self.settings_dock)
        view_menu.addAction(self.settings_dock.toggleViewAction())
        self.resizeDocks([tools, self.settings_dock], [150, 280], Qt.Horizontal)
        self._first_settings = True
        self.settings_dock.hide()
        self.settings_dock.toggleViewAction().triggered.connect(self._settings_view_chosen)

        bottom = QWidget()
        layout = QVBoxLayout(bottom)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel()
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.statusBar().addWidget(self.status, 1)
        self.history_toggle = QToolButton()
        self.history_toggle.setText("작업 기록")
        self.history_toggle.setCheckable(True)
        self.statusBar().addPermanentWidget(self.history_toggle)
        self.log = QTextEdit()
        self.log.setObjectName("logView")
        self.log.setReadOnly(True)
        layout.addWidget(self.log)
        self.history_toggle.toggled.connect(self._show_history)
        messages = QDockWidget("작업 기록", self)
        self.history_dock = messages
        messages.setWidget(bottom)
        self.addDockWidget(Qt.BottomDockWidgetArea, messages)
        view_menu.addAction(messages.toggleViewAction())
        messages.visibilityChanged.connect(self.history_toggle.setChecked)
        messages.hide()
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(self._clear_message)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self._selection_changed()

    def _show_history(self, shown: bool) -> None:
        self.history_dock.setVisible(shown)
        if shown:
            self.resizeDocks([self.history_dock], [130], Qt.Vertical)

    def _settings_view_chosen(self, *_):
        self._first_settings = False

    def _show_first_settings(self):
        if self._first_settings:
            self.settings_dock.show()
            self._first_settings = False

    def _title(self) -> None:
        self.setWindowTitle(f"{self.project.name}{' *' if self._dirty else ''} — CAELAB Workbench")

    def _mark_dirty(self) -> None:
        if not self._loading:
            self._dirty = True
            self._title()

    def _message(self, text: str, *, persistent: bool = False) -> None:
        # Only compose user-facing action text here; service diagnostics are translated at their boundary.
        first, *following = text.splitlines()
        self._current_message = first + (' — ' + following[0] if following else '')
        self._render_current_message()
        self._message_timer.stop()
        if not persistent:
            self._message_timer.start(7000)
        self.status.setToolTip(text)
        self.log.moveCursor(self.log.textCursor().MoveOperation.End)
        if self.log.toPlainText():
            self.log.insertPlainText('\n')
        self.log.insertPlainText(text)

    def _render_current_message(self) -> None:
        text = getattr(self, "_current_message", "")
        self.status.setText(self.status.fontMetrics().elidedText(
            text, Qt.ElideRight, max(50, self.status.width())) if self.isVisible() else text)

    def _clear_message(self):
        self._current_message = ''
        self.status.clear()
        self.status.setToolTip('')

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "status"):
            QTimer.singleShot(0, self._render_current_message)

    def _short_path(self, path: str) -> str:
        base = (self.project.path.parent if self.project.path else Path.cwd()).resolve()
        selected = Path(path).resolve()
        if selected.is_relative_to(base):
            return selected.relative_to(base).as_posix()
        root = self.project.run_store
        references = {Path(ref.path).resolve() for node in self.project.nodes.values()
                      for ref in node.files.values()
                      if root is None or not Path(ref.path).resolve().is_relative_to(root.resolve())}
        # Past receipt inputs may no longer be registered. Do not invent a short
        # context for them; the exact source remains more useful than ambiguity.
        if selected not in references:
            return str(selected)
        internal_names = {normcase(other.relative_to(base).as_posix())
                          for other in references if other.is_relative_to(base)}
        for count in range(2, len(selected.parts) + 1):
            candidate = Path(*selected.parts[-count:]).as_posix()
            other_names = {normcase(Path(*other.parts[-count:]).as_posix())
                           for other in references if other != selected and not other.is_relative_to(base)}
            if normcase(candidate) not in internal_names | other_names:
                return candidate
        return str(selected)

    def _is_run_file(self, path) -> bool:
        root = self.project.run_store
        return root is not None and Path(path).resolve().is_relative_to(root.resolve())

    def _summary_file_name(self, path: str) -> tuple[str, str]:
        return (Path(path).name if self._is_run_file(path) else self._short_path(path)), ''

    def _file_tooltip(self, path: str) -> str:
        return Path(path).name if self._is_run_file(path) else str(Path(path).resolve())

    def _error(self, text: str, error: Exception) -> None:
        message = f"{text}. {_problem_text(error)}"
        self._message(message, persistent=True)
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
        if self.boxes:
            self._show_first_settings()
        self._message("프로그램을 추가하고 입력 파일을 지정하세요." if not self.boxes
                      else "프로젝트를 열었습니다.")
        self.reset_view()
        self.view.centerOn(self.scene.itemsBoundingRect().center())
        self._pending_initial_project = self.project if self.boxes else None
        if self.boxes:
            project = self.project
            QTimer.singleShot(0, lambda: self._initial_view(project))

    def _initial_view(self, project):
        if self.project is not project or self._pending_initial_project is not project or not self.isVisible():
            return
        self._pending_initial_project = None
        # Only opening a project establishes a new view. Later resize/selection
        # and dock changes retain the user's view, including clamped scroll.
        rect = self.scene.itemsBoundingRect().adjusted(-12, -12, 12, 12)
        viewport = self.view.viewport()
        if rect.isEmpty():
            return
        scale = min(1.0, viewport.width() / rect.width(), viewport.height() / rect.height())
        if scale < 1.0:
            self.view.scale(max(.85, scale), max(.85, scale))
        for bar in (self.view.horizontalScrollBar(), self.view.verticalScrollBar()):
            bar.setValue(bar.minimum())

    def showEvent(self, event):
        super().showEvent(event)
        project = getattr(self, '_pending_initial_project', None)
        if project is not None:
            QTimer.singleShot(0, lambda: self._initial_view(project))

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
        self._show_first_settings()
        self._mark_dirty()
        self._reflect_programs()
        self.view.centerOn(box)
        return box

    def _position_changed(self, box: SystemBox) -> None:
        self.project.nodes[box.node_id].position = (box.pos().x(), box.pos().y())
        self._mark_dirty()

    def _selected_boxes(self) -> list[SystemBox]:
        return [item for item in self.scene.selectedItems() if isinstance(item, SystemBox)]

    def _file_owner(self, node_id, role='input'):
        seen = set()
        while role == 'input' and node_id not in seen:
            seen.add(node_id)
            edge = next((edge for edge in self.project.connections if edge.target_id == node_id), None)
            if edge is None:
                break
            node_id, role = edge.source_id, edge.source_role
        return node_id, role

    def _box_name(self, box):
        reference = self.workbench.input_file(self.project, box.node_id) or self.project.nodes[box.node_id].files.get('output')
        return f'{box.title} · {self._summary_file_name(reference.path)[0]}' if reference else box.title

    @staticmethod
    def _distinct_choices(pairs):
        counts = {}
        for _, label in pairs:
            counts[label] = counts.get(label, 0) + 1
        seen, result = {}, {}
        for node_id, label in pairs:
            seen[label] = seen.get(label, 0) + 1
            result[node_id] = label if counts[label] == 1 else f'{label} ({seen[label]})'
        return result

    def _calculation_result(self, node_id, role, reference):
        owner_id, owner_role = self._file_owner(node_id, role)
        return bool(reference and owner_role == 'output'
                    and self.project.nodes[owner_id].kind == 'analysis'
                    and self._is_run_file(reference.path))

    def _selection_changed(self) -> None:
        selected = self._selected_boxes()
        self.connect_action.setEnabled(bool(selected) and len(self.boxes) > 1)
        self.selection_connect_button.setVisible(len(selected) == 2)
        self.remove_action.setEnabled(len(selected) == 1)
        self.disconnect_action.setEnabled(len(selected) == 1 and any(
            edge.target_id == selected[0].node_id for edge in self.project.connections))
        self.restore_action.setEnabled(bool(self.project.removed_nodes))
        self.settings_body.setVisible(len(selected) == 1)
        self.open_button.setVisible(len(selected) == 1)
        self.program_summary.clear()
        self.program_summary.hide()
        self.run_summary.clear()
        self.run_summary.hide()
        if len(selected) != 1:
            for value in (self.input_value, self.output_value):
                value.clear()
                value.setToolTip('')
            self.selection_name.setText('상자 연결' if len(selected) == 2 else '프로그램 선택')
            if len(selected) == 2:
                self.program_summary.setText(' ↔ '.join(self._box_name(box) for box in selected))
                self.program_summary.show()
            return
        box = selected[0]
        node = self.project.nodes[box.node_id]
        statuses = {status.spec.id: status for status in self.workbench.programs()}
        program = statuses.get(node.program_id)
        self.selection_name.setText(program.spec.name if program else '전문가 논의' if node.kind == 'expert' else '프로그램')
        reference = self.workbench.input_file(self.project, node.id)
        direct = self._file_owner(node.id) == (node.id, 'input')
        editable = node.kind != 'expert'
        self.output_section.setVisible(editable)
        self.input_button.setText('변경…' if reference else '선택…')
        self.input_button.setVisible(editable and direct)
        self.input_more_button.setVisible(editable and not direct)
        self.owner_button.setVisible(not direct and editable and not self._needs_upstream(box))
        if not direct:
            owner_id, owner_role = self._file_owner(node.id)
            owner = self.boxes[owner_id]
            role_name = '입력' if owner_role == 'input' else '출력'
            self.owner_button.setText(f'{owner.title} {role_name}으로 이동')
        for summary, value, button, unit_button, role, ref in (
                (self.input_summary, self.input_value, self.input_button, self.input_units_button, 'input', reference),
                (self.output_summary, self.output_value, self.output_button, self.output_units_button, 'output', node.files.get('output'))):
            title = '입력 파일' if role == 'input' else '출력 파일'
            saved_result = bool(role == 'output' and node.kind == 'analysis' and ref and self._is_run_file(ref.path))
            if saved_result:
                title = '선택 계산 결과'
            status = self._file_status(ref) if ref else 'needs_input'
            if status == 'stale':
                note = '이전 입력의 결과' if self._calculation_result(node.id, role, ref) else '갱신 필요'
            else:
                note = {'ready': '', 'needs_input': '미지정', 'changed': '변경됨', 'missing': '누락'}.get(status, '상태 미확인')
            summary.setText(f'{title} {note}'.rstrip())
            summary.setStyleSheet('color: #9d2929;' if status == 'missing' else
                                 'color: #875212;' if status in {'stale', 'changed'} else '')
            value.setText(self._summary_file_name(ref.path)[0] if ref else '')
            value.setPlaceholderText('파일 지정 안 됨')
            value.setToolTip(self._file_tooltip(ref.path) if ref else '')
            summary.setToolTip('')
            unit_button.setText('단위…' if ref and ref.units else '단위 지정…')
            unit_button.setEnabled(bool(ref and not self._is_run_file(ref.path)))
            unit_button.setToolTip('')
            unit_value = self.input_units_value if role == 'input' else self.output_units_value
            unit_row = self.input_units_row if role == 'input' else self.output_units_row
            unit_row.setVisible(ref is not None)
            unit_value.setText(', '.join(ref.units.values()) if ref and ref.units else '미확인')
            unit_value.setToolTip(', '.join(f'{UNIT_LABELS.get(key, key)}: {value}' for key, value in ref.units.items())
                                  if ref and ref.units else '')
            unit_button.setText('변경…' if ref and ref.units else '지정…')
            unit_button.setAccessibleName(f'{title} 단위 ' + ('변경' if ref and ref.units else '지정'))
            unit_button.setVisible(bool(editable and ref and not self._is_run_file(ref.path)))
            if role == 'output':
                button.setText('등록…' if ref is None else '변경…')
                button.setToolTip('전용 프로그램에서 작성한 출력 파일 등록' if ref is None else '등록한 출력 파일 변경')
                button.setEnabled(not saved_result)
            if role == 'output':
                for widget in (summary, value, unit_value):
                    widget.setVisible(editable)
                button.setVisible(editable and not saved_result)
                unit_button.setVisible(editable and bool(ref) and not saved_result)
        if program and not program.available:
            self.program_summary.setText(_problem_text(program.reason))
        if node.kind == 'analysis' and self.project.path is not None:
            try:
                run = self.workbench.selected_native_run(self.project, node.id)
                if run:
                    self.output_summary.setText('선택 계산 결과 — ' + _run_text(run))
                    self.output_value.setPlaceholderText('결과 파일 없음')
                    self.output_button.setEnabled(False)
                    self.output_units_button.setEnabled(False)
                    self.output_button.hide()
                    self.output_units_button.hide()
            except (OSError, ValueError, RuntimeError) as error:
                self.run_summary.setText(_problem_text(error))
        elif node.state == 'failed' and node.reason:
            problems = [self.program_summary.text(), _problem_text(node.reason)]
            self.program_summary.setText('\n'.join(dict.fromkeys(filter(None, problems))))
        self.open_button.setText(self._default_label(box))
        self.open_button.setEnabled(not (program and not program.available and not self._needs_file(box))
                                    or node.kind in {'analysis', 'expert'} or self._needs_upstream(box))
        self.program_summary.setVisible(bool(self.program_summary.text()))
        self.run_summary.setVisible(bool(self.run_summary.text()))

    def _needs_file(self, box):
        reference = self.workbench.input_file(self.project, box.node_id)
        direct = self._file_owner(box.node_id) == (box.node_id, 'input')
        return direct and box.kind in {'geometry', 'mesh', 'results'} and (reference is None or self._file_status(reference) == 'missing')

    def _needs_upstream(self, box):
        reference = self.workbench.input_file(self.project, box.node_id)
        return (box.kind in {'geometry', 'mesh', 'results'}
                and self._file_owner(box.node_id) != (box.node_id, 'input')
                and (reference is None or self._file_status(reference) == 'missing'))

    def _default_label(self, box):
        if self._needs_upstream(box):
            return self._owner_label(box.node_id)
        if self._needs_file(box):
            return '입력 파일 지정…' if box.kind != 'results' else '결과 파일 지정…'
        return {'geometry': 'FreeCAD 열기', 'mesh': 'Gmsh 열기', 'analysis': '해석창 열기',
                'results': 'ParaView 열기', 'expert': '전문가 대화 열기'}.get(box.kind, '프로그램 열기')

    def _owner_label(self, node_id):
        owner_id, role = self._file_owner(node_id)
        return f"{self.boxes[owner_id].title} {'입력' if role == 'input' else '출력'}으로 이동"

    def _node_note(self, node, reference) -> str:
        return _problem_text(node.reason) if node.state == 'failed' and node.reason else ''

    def select_input_owner(self):
        selected = self._selected_boxes()
        if len(selected) != 1:
            return
        node_id, role = self._file_owner(selected[0].node_id)
        self.scene.clearSelection()
        self.boxes[node_id].setSelected(True)
        self.settings_dock.show()
        (self.input_button if role == 'input' else self.output_button).setFocus()

    def edit_units(self, role='input'):
        selected = self._selected_boxes()
        if len(selected) != 1:
            return False
        node_id, role = self._file_owner(selected[0].node_id, role)
        reference = self.project.nodes[node_id].files.get(role)
        if reference is None or self._is_run_file(reference.path):
            return False
        units = self._ask_file_units(reference.path, reference.units)
        if units is None:
            return False
        if units == reference.units:
            return True
        try:
            self.workbench.set_node_file_units(self.project, node_id, units, role=role)
        except Exception as error:
            self._error('단위를 변경하지 못했습니다', error)
            return False
        self._mark_dirty()
        self._reflect_programs()
        self._selection_changed()
        self._message(f'파일 단위를 변경했습니다: {self._summary_file_name(reference.path)[0]}')
        return True

    def choose_file(self, role: str = "input") -> bool:
        selected = self._selected_boxes()
        if len(selected) != 1:
            return False
        box = selected[0]
        if role == 'output' and not self.output_button.isEnabled():
            return False
        path, _filter = QFileDialog.getOpenFileName(
            self, ("상위 연결 대신 사용할 입력 파일" if role == "input" and self._file_owner(box.node_id) != (box.node_id, "input") else "입력 파일 선택") if role == "input" else "작성한 출력 지정", "",
            OUTPUT_FILTER if role == "output" else _FILE_FILTERS.get(box.kind, "모든 파일 (*)"))
        if not path:
            return False
        units = self._ask_file_units(path)
        if units is None:
            return False
        return self.register_file(box.node_id, path, role, units)

    def _ask_file_units(self, path: str, current: dict | None = None) -> dict | None:
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
            field.setText((current or {}).get(name, ""))
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
        if not boxes or len(self.boxes) < 2:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("프로그램 연결")
        layout = QFormLayout(dialog)
        base_names = [(box.node_id, self._box_name(box)) for box in self.boxes.values()]
        names = self._distinct_choices(base_names)
        source = QComboBox()
        source.setObjectName('connection_source')
        for box in self.boxes.values():
            source.addItem(names[box.node_id], box.node_id)
        source.setCurrentIndex(source.findData(boxes[0].node_id))
        role = QComboBox()
        role.setObjectName('connection_role')
        role.addItem("등록한 출력 파일", "output")
        role.addItem("현재 사용하는 입력 파일", "input")
        target = QComboBox()
        target.setObjectName('connection_target')
        for box in self.boxes.values():
            target.addItem(names[box.node_id], box.node_id)
        target_id = boxes[1].node_id if len(boxes) == 2 else next(box.node_id for box in self.boxes.values() if box is not boxes[0])
        target.setCurrentIndex(target.findData(target_id))
        file_path = QLabel()
        file_path.setWordWrap(True)
        replacing = QLabel()
        replacing.setWordWrap(True)
        def describe():
            reference = (self.workbench.input_file(self.project, source.currentData())
                         if role.currentData() == "input" else
                         self.project.nodes[source.currentData()].files.get("output"))
            file_path.setText(self._summary_file_name(reference.path)[0] if reference else "파일 미지정")
            file_path.setToolTip(self._file_tooltip(reference.path) if reference else '')
            current = self.workbench.input_file(self.project, target.currentData())
            replacing.setText('기존 입력 대신 이 파일을 사용합니다.' if current or any(
                edge.target_id == target.currentData() for edge in self.project.connections) else '')
            buttons.button(QDialogButtonBox.Ok).setEnabled(source.currentData() != target.currentData())
        source.currentIndexChanged.connect(describe)
        role.currentIndexChanged.connect(describe)
        target.currentIndexChanged.connect(describe)
        layout.addRow("보내는 상자", source)
        layout.addRow("전달할 파일", role)
        layout.addRow("받는 상자", target)
        layout.addRow("실제 파일", file_path)
        layout.addRow(replacing)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        describe()
        for node_id, label in base_names:
            if names[node_id] != label:
                self.boxes[node_id].choice_title = self.boxes[node_id].title + names[node_id][len(label):]
                self.boxes[node_id].update()
        try:
            accepted = dialog.exec() == QDialog.Accepted
        finally:
            for box in self.boxes.values():
                box.choice_title = None
                box.update()
        if accepted:
            self.connect(source.currentData(), target.currentData(), role.currentData())

    def disconnect_selected(self):
        selected = self._selected_boxes()
        if len(selected) != 1:
            return False
        node_id = selected[0].node_id
        if not any(edge.target_id == node_id for edge in self.project.connections):
            return False
        try:
            self.workbench.disconnect_node_input(self.project, node_id)
        except Exception as error:
            self._error('연결을 해제하지 못했습니다', error)
            return False
        self._sync_connections()
        self._mark_dirty()
        self._reflect_programs()
        self._selection_changed()
        self._message('입력 연결을 해제했습니다. 사용할 입력을 지정하세요.')
        return True

    def remove_selected(self):
        selected = self._selected_boxes()
        if len(selected) != 1:
            return False
        box = selected[0]
        node = self.project.nodes[box.node_id]
        if node.files or any(edge.source_id == node.id or edge.target_id == node.id for edge in self.project.connections):
            answer = QMessageBox.question(self, '프로젝트에서 제거',
                f'{self._box_name(box)}을 프로젝트에서 제거하시겠습니까?\n파일과 계산·대화 기록은 지우지 않으며 편집 메뉴에서 복원할 수 있습니다.',
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Yes:
                return False
        try:
            self.workbench.remove_program_node(self.project, node.id)
        except Exception as error:
            self._error('프로그램을 제거하지 못했습니다', error)
            return False
        self._sync_connections()
        self.boxes.pop(node.id)
        self.scene.removeItem(box)
        self.empty_hint.setVisible(not self.boxes)
        self._mark_dirty()
        self._reflect_programs()
        self._selection_changed()
        self._message(f'{box.title}을 프로젝트에서 제거했습니다.')
        return True

    def restore_program(self):
        if not self.project.removed_nodes:
            return False
        dialog = QDialog(self)
        dialog.setWindowTitle('제거한 프로그램 복원')
        layout = QFormLayout(dialog)
        choices = QComboBox()
        pairs = []
        for node in self.project.removed_nodes.values():
            title = next(title for kind, title, _ in SYSTEM_KINDS if kind == node.kind)
            reference = node.files.get('input') or node.files.get('output')
            label = f'{title} — {self._summary_file_name(reference.path)[0]}' if reference else title
            if node.kind == 'analysis' and self.project.path:
                try:
                    runs = self.workbench.native_runs(self.project, node.id)
                    matched = next((run for run in runs if reference and
                                    Path(reference.path).is_relative_to(run.workdir)), None)
                    run = matched or (runs[-1] if runs else None)
                    if run and run.input_path:
                        label = f'{title} — {self._summary_file_name(run.input_path)[0]}'
                except (OSError, ValueError, RuntimeError):
                    pass  # Existing file name/number remains usable when records cannot be read.
            pairs.append((node.id, label))
        for node_id, label in self._distinct_choices(pairs).items():
            choices.addItem(label, node_id)
        layout.addRow('프로그램', choices)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() != QDialog.Accepted:
            return False
        try:
            node = self.workbench.restore_program_node(self.project, choices.currentData())
        except Exception as error:
            self._error('프로그램을 복원하지 못했습니다', error)
            return False
        box = self._add_node_box(node)
        self.empty_hint.hide()
        self.scene.clearSelection()
        box.setSelected(True)
        self._show_first_settings()
        self._mark_dirty()
        self._reflect_programs()
        self._selection_changed()
        self._message(f'{box.title}을 복원했습니다. 연결은 복원되지 않습니다.')
        return True

    def _context_menu(self, position):
        item = self.view.itemAt(position)
        if isinstance(item, SystemBox) and not item.isSelected():
            self.scene.clearSelection()
            item.setSelected(True)
        menu = QMenu(self)
        if self._selected_boxes():
            action = menu.addAction(self.open_button.text())
            action.setEnabled(len(self._selected_boxes()) == 1 and self.open_button.isEnabled())
            action.triggered.connect(self.open_selected)
            menu.addAction(self.connect_action)
            menu.addAction(self.disconnect_action)
            menu.addSeparator()
            menu.addAction(self.remove_action)
        else:
            menu.addMenu(self.add_program_menu)
        menu.exec(self.view.viewport().mapToGlobal(position))

    def refresh_files(self) -> None:
        before = [(node.state, node.reason) for node in self.project.nodes.values()]
        issues = self.workbench.refresh_native_project(self.project)
        if before != [(node.state, node.reason) for node in self.project.nodes.values()]:
            self._mark_dirty()
        self._reflect_programs()
        self._selection_changed()
        self._message("파일 상태를 확인했습니다." +
                      ('\n' + '\n'.join(self._project_notes()) if issues else ''))

    def _reflect_programs(self) -> None:
        statuses = {status.spec.id: status for status in self.workbench.programs()}
        for node_id, box in self.boxes.items():
            node = self.project.nodes[node_id]
            program = statuses.get(node.program_id)
            box.subtitle = "프로그램 연결 필요" if program and not program.available else ""
            self._reflect_node(node_id)
        self._poll_runs()

    def _reflect_node(self, node_id: str) -> None:
        node, box = self.project.nodes[node_id], self.boxes[node_id]
        reference = self.workbench.input_file(self.project, node_id)
        title = next((title for kind, title, sub in SYSTEM_KINDS if kind == node.kind), "프로그램")
        box.title = {"geometry": "FreeCAD", "mesh": "Gmsh", "analysis": "CalculiX", "results": "ParaView", "expert": "전문가 논의"}.get(node.kind, title)
        box.file_label = self._summary_file_name(reference.path)[0] if reference else "선택 파일 없음"
        box.set_state(node.state, self._node_note(node, reference))
        execution_state = node.state in {'queued', 'preparing', 'running', 'failed', 'cancelled', 'interrupted'}
        if reference is None and not execution_state:
            box.status_label = '결과 없음' if node.kind in {'results', 'expert'} else '입력 파일 미지정'
        if reference and not execution_state:
            status = self._file_status(reference)
            if status in {'stale', 'changed', 'missing'}:
                box.status_error = status == 'missing'
                box.status_warning = status in {'stale', 'changed'}
                box.status_label = {'stale': '입력 파일 갱신 필요', 'changed': '입력 파일 변경됨',
                                    'missing': '입력 파일 누락'}[status]
                if status == 'stale' and self._calculation_result(node_id, 'input', reference):
                    box.status_label = '이전 입력의 결과'
        if _selection_changed_input(node, reference):
            # The connected file changed; this does not establish that a viewer
            # displayed it or that a discussion adopted it. Keep the stored state.
            status = self._file_status(reference)
            if status == "ready":
                box.status_label = "선택 자료 변경"
            else:
                box.set_state(status, self._node_note(node, reference))
                box.status_label = {'stale': '입력 파일 갱신 필요', 'changed': '입력 파일 변경됨',
                                    'missing': '입력 파일 누락'}.get(status, '')
                if status == 'stale' and self._calculation_result(node_id, 'input', reference):
                    box.status_label = '이전 입력의 결과'
        if not execution_state and reference and self._file_status(reference) == 'ready' and node.kind in {'geometry', 'mesh'}:
            output = node.files.get('output')
            if output:
                output_status = self._file_status(output)
                box.status_label = {'stale': '출력 파일 갱신 필요', 'changed': '출력 파일 변경됨',
                                    'missing': '출력 파일 누락'}.get(output_status, '')
                box.status_warning = output_status in {'stale', 'changed', 'missing'}
                box.status_error = output_status == 'missing'
        file_label = self._file_tooltip(reference.path) if reference else "입력 파일 없음"
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
        run_objects = {}
        for node_id, box in self.boxes.items():
            if box.kind != "analysis":
                continue
            try:
                run = self.workbench.selected_native_run(self.project, node_id)
                if run is None:
                    continue
                run_objects[node_id] = run
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
            box = self.boxes[node_id]
            box.set_state(state, reason)
            run = run_objects.get(node_id)
            if run is not None:
                box.status_label = _run_text(run)
                box.status_error = run.state == 'completed' and run.parse_state == 'failed'
                box.status_warning = bool(run.stale or run.parse_state in {'missing', 'partial', 'unsupported'})
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
            self.scene.clearSelection()
            item.setSelected(True)
            self.open_selected()

    def open_selected(self) -> None:
        selected = self._selected_boxes()
        if len(selected) == 1:
            if self._needs_upstream(selected[0]):
                self.select_input_owner()
            elif self._needs_file(selected[0]):
                self.choose_file("input")
            else:
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
            self._message(f"{box.title} 열기 요청을 보냈습니다. 실제 파일 표시는 전용 프로그램에서 확인하세요.")
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
        viewport = self.view.viewport()
        center = self.view.viewportTransform().inverted()[0].map(QPointF(viewport.width() / 2, viewport.height() / 2))
        self.view.resetTransform()
        self.view.centerOn(center)

    def _update_zoom_display(self) -> None:
        percent = f'{self.view.transform().m11() * 100:.1f}'.rstrip('0').rstrip('.')
        self.zoom_percent.setText(percent + '%')
        self.zoom_percent.setAccessibleName(f'현재 배율 {percent}%, 100%로 보기')
        self.view.place_controls()
