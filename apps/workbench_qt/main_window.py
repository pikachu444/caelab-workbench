"""Workbench main window (PySide6).

Layout follows the redesign GUI plan: model tree on the left, a light
viewport placeholder in the centre (meshes/selection display only —
precise editing stays in FreeCAD, post-processing in ParaView), the
selected object's properties on the right, and the work flow along the
bottom. One primary action per surface; errors are one line plus a
"go to the object" jump. Internal ids/hashes live only in the
diagnostics dialog (Help menu).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from caelab.model.schema import StructuralModel
from caelab.workbench import Workbench

FLOW_STEPS = ["열기", "검사", "실행", "결과", "토론", "제안 적용", "재실행"]
TREE_GROUPS = ["재료·단면", "메시", "해석 단계", "하중·구속", "접촉·연결", "작업", "결과"]


class MainWindow(QMainWindow):
    def __init__(self, workbench: Workbench | None = None) -> None:
        super().__init__()
        self.workbench = workbench or Workbench()
        self.model: StructuralModel | None = None
        self.project_folder: Path | None = None
        self._issue_target: str | None = None
        self._path_items: dict[str, QTreeWidgetItem] = {}
        self.setWindowTitle("CAELAB Workbench")
        self.resize(1280, 800)
        self._build_ui()

    # -- construction ------------------------------------------------------
    def _build_ui(self) -> None:
        toolbar = QToolBar("주 행동")
        self.addToolBar(toolbar)
        self.act_open = toolbar.addAction("열기", self.open_project_dialog)
        self.act_save = toolbar.addAction("저장", self.save_project)
        self.act_check = toolbar.addAction("검사", self.run_check)
        self.act_run = toolbar.addAction("실행", self.run_solve)
        help_menu = self.menuBar().addMenu("도움말")
        help_menu.addAction("진단 정보", self.show_diagnostics)

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        splitter = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("모델")
        self.tree.itemSelectionChanged.connect(self._on_select)
        splitter.addWidget(self.tree)

        self.viewport = QLabel("모델을 열면 여기에 형상·메시 개요가 표시됩니다.")
        self.viewport.setAlignment(Qt.AlignCenter)
        splitter.addWidget(self.viewport)

        self.props = QTableWidget(0, 2)
        self.props.setHorizontalHeaderLabels(["항목", "값"])
        splitter.addWidget(self.props)
        splitter.setSizes([300, 620, 360])
        root_layout.addWidget(splitter, 1)

        status_row = QHBoxLayout()
        self.status = QLabel("준비됨")
        status_row.addWidget(self.status, 1)
        self.btn_goto = QPushButton("고칠 위치로 이동")
        self.btn_goto.setEnabled(False)
        self.btn_goto.clicked.connect(self._goto_issue)
        status_row.addWidget(self.btn_goto)
        root_layout.addLayout(status_row)

        flow_row = QHBoxLayout()
        self.flow_labels = []
        for i, name in enumerate(FLOW_STEPS):
            label = QLabel(f"{i + 1}. {name}")
            label.setEnabled(i == 0)
            self.flow_labels.append(label)
            flow_row.addWidget(label)
        root_layout.addLayout(flow_row)

    # -- model / tree ---------------------------------------------------------
    def set_model(self, model: StructuralModel, folder: Path | None = None) -> None:
        self.model = model
        self.project_folder = folder
        self._path_items.clear()
        self.tree.clear()
        root = QTreeWidgetItem([model.name])
        root.setData(0, Qt.UserRole, "model")
        self.tree.addTopLevelItem(root)
        self._path_items["model"] = root

        groups: dict[str, QTreeWidgetItem] = {}
        for gname in TREE_GROUPS:
            item = QTreeWidgetItem([gname])
            root.addChild(item)
            groups[gname] = item

        for i, m in enumerate(model.materials):
            self._add(groups["재료·단면"], f"재료: {m.name} ({m.law})", f"materials[{i}]")
        for i, s in enumerate(model.sections):
            self._add(groups["재료·단면"], f"단면: {s.name} ({s.kind})", f"sections[{i}]")
        self._add(
            groups["메시"],
            f"절점 {len(model.mesh.nodes)}개 · 요소 {len(model.mesh.elements)}개",
            "mesh",
        )
        for i, st in enumerate(model.steps):
            self._add(groups["해석 단계"], f"{st.name} ({st.kind})", f"steps[{i}]")
        for i, ld in enumerate(model.loads):
            self._add(groups["하중·구속"], f"하중: {ld.name} ({ld.kind})", f"loads[{i}]")
        for i, bc in enumerate(model.bcs):
            self._add(groups["하중·구속"], f"구속: {bc.name}", f"bcs[{i}]")
        for i, inter in enumerate(model.interactions):
            self._add(groups["접촉·연결"], f"{inter.name} ({inter.kind})", f"interactions[{i}]")
        self._add(groups["작업"], "CalculiX", "job")
        self.results_item = groups["결과"]
        root.setExpanded(True)
        self.viewport.setText(
            f"{model.name}\n절점 {len(model.mesh.nodes)}개 · 요소 {len(model.mesh.elements)}개\n"
            "(정밀 편집은 FreeCAD, 정밀 후처리는 ParaView에서 엽니다)"
        )
        self.status.setText("모델을 열었습니다. 다음 행동: 검사")
        self._set_flow(1)

    def _add(self, parent: QTreeWidgetItem, text: str, path: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        item.setData(0, Qt.UserRole, path)
        parent.addChild(item)
        self._path_items[path] = item
        return item

    def select_path(self, path: str) -> bool:
        item = self._path_items.get(path)
        if item is None and "[" in path:  # fall back to the group object
            item = self._path_items.get(path.split("[")[0].rstrip("s") and path)
        if item is None:
            return False
        self.tree.setCurrentItem(item)
        return True

    # -- properties --------------------------------------------------------------
    def _on_select(self) -> None:
        items = self.tree.selectedItems()
        if not items or self.model is None:
            return
        path = items[0].data(0, Qt.UserRole) or "model"
        rows = self._describe(path)
        self.props.setRowCount(len(rows))
        for r, (k, v) in enumerate(rows):
            self.props.setItem(r, 0, QTableWidgetItem(str(k)))
            self.props.setItem(r, 1, QTableWidgetItem(str(v)))

    def _describe(self, path: str) -> list[tuple[str, str]]:
        m = self.model
        assert m is not None
        if path == "model":
            return [("이름", m.name), ("단위계", m.unit_system), ("형상 원본", m.geometry_source or "-")]
        if path == "mesh":
            types = sorted({e["type"] for e in m.mesh.elements.values()})
            return [("절점", len(m.mesh.nodes)), ("요소", len(m.mesh.elements)), ("요소 종류", ", ".join(types))]
        if path == "job":
            return [("솔버", "CalculiX"), ("주 행동", "실행")]
        kind, _, rest = path.partition("[")
        if rest:
            idx = int(rest.rstrip("]"))
            obj = {
                "materials": m.materials, "sections": m.sections, "steps": m.steps,
                "loads": m.loads, "bcs": m.bcs, "interactions": m.interactions,
            }[kind][idx]
            return [(k, v) for k, v in vars(obj).items()]
        return [("선택", path)]

    # -- actions ------------------------------------------------------------------
    def open_project_dialog(self) -> None:  # pragma: no cover - native dialog
        from PySide6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(self, "프로젝트 열기")
        if folder:
            self.set_model(self.workbench.open_project(folder), Path(folder))

    def save_project(self) -> None:
        if self.model is None:
            self.status.setText("저장할 모델이 없습니다.")
            return
        folder = self.project_folder or Path(tempfile.mkdtemp(prefix="caelab-project-"))
        self.workbench.save_project(self.model, folder)
        self.project_folder = folder
        self.status.setText(f"저장했습니다: {folder}")

    def run_check(self) -> None:
        if self.model is None:
            self.status.setText("먼저 모델을 여세요.")
            return
        issues = self.workbench.check(self.model)
        if not issues:
            self.status.setText("검사 통과: 입력 오류가 없습니다. 다음 행동: 실행")
            self.btn_goto.setEnabled(False)
            self._issue_target = None
            self._set_flow(2)
            return
        first = issues[0]
        self.status.setText(f"검사 오류 {len(issues)}건: {first.message}")
        self._issue_target = first.target_path
        self.btn_goto.setEnabled(True)

    def _goto_issue(self) -> None:
        if self._issue_target:
            self.select_path(self._issue_target)

    def run_solve(self) -> None:
        if self.model is None:
            self.status.setText("먼저 모델을 여세요.")
            return
        workdir = Path(tempfile.mkdtemp(prefix="caelab-run-"))
        job = self.workbench.run(self.model, "calculix", workdir)
        if job.result is not None:
            self._add(self.results_item, job.result.summary(), "result")
            self.status.setText(job.result.summary())
            if job.result.success:
                self._set_flow(3)
        else:
            self.status.setText(f"작업 상태: {job.state}")

    def show_diagnostics(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("진단 정보")
        text = QTextEdit()
        text.setReadOnly(True)
        info = [
            f"모델: {self.model.name if self.model else '-'}",
            f"프로젝트: {self.project_folder or '-'}",
            f"작업 수: {len(self.workbench.jobs.jobs)}",
        ]
        text.setPlainText("\n".join(info))
        layout = QVBoxLayout(dlg)
        layout.addWidget(text)
        dlg.exec()

    def _set_flow(self, upto: int) -> None:
        for i, label in enumerate(self.flow_labels):
            label.setEnabled(i <= upto)
