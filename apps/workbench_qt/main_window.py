"""Workbench main window (PySide6), structured like a CAE desktop.

Layout and interaction follow the redesign GUI plan and the layout used
by mainstream CAE programs (Abaqus/CAE, ANSYS Mechanical, Salome):
menu + toolbar, a docked model tree on the left, a VTK viewport in the
centre, a docked property editor for the selected object on the right,
and a message/result dock at the bottom. One primary action per
surface; input errors are one line plus a jump to the object; internal
ids live only in Help > Diagnostics.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QStyle,
    QTextEdit,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from caelab.model.schema import StructuralModel
from caelab.workbench import Workbench

from .theme import THEME
from .viewport import Viewport

FLOW_STEPS = ["열기", "검사", "실행", "결과", "토론", "제안 적용", "재실행"]
TREE_GROUPS = ["재료·단면", "메시", "해석 단계", "하중·구속", "접촉·연결", "작업", "결과"]


def fmt_length(m: float) -> str:
    if m != 0 and abs(m) < 0.01:
        return f"{m * 1000:.3f} mm"
    return f"{m:.4f} m"


def fmt_force(n: float) -> str:
    return f"{n:,.1f} N"


class MainWindow(QMainWindow):
    def __init__(self, workbench: Workbench | None = None) -> None:
        super().__init__()
        self.workbench = workbench or Workbench()
        self.model: StructuralModel | None = None
        self.project_folder: Path | None = None
        self._issue_target: str | None = None
        self._path_items: dict[str, QTreeWidgetItem] = {}
        self._dirty = False
        self._last_result = None
        self.setWindowTitle("CAELAB Workbench")
        self.resize(1440, 900)
        self.setStyleSheet(THEME)
        self._build_ui()

    # -- construction ---------------------------------------------------------
    def _icon(self, name: str):
        return self.style().standardIcon(getattr(QStyle, name))

    def _build_ui(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("파일")
        file_menu.addAction("열기…", self.open_project_dialog)
        file_menu.addAction("저장", self.save_project)
        analysis_menu = menu.addMenu("해석")
        analysis_menu.addAction("입력 검사", self.run_check)
        analysis_menu.addAction("실행", self.run_solve)
        results_menu = menu.addMenu("결과")
        results_menu.addAction("ParaView에서 열기 (다음 단계)", lambda: self._log("ParaView 연결은 다음 단계에서 붙입니다."))
        expert_menu = menu.addMenu("전문가")
        expert_menu.addAction("선택 대상으로 논의 (다음 단계)", lambda: self._log("전문가 패널은 다음 단계에서 붙입니다."))
        help_menu = menu.addMenu("도움말")
        help_menu.addAction("진단 정보", self.show_diagnostics)

        toolbar = QToolBar("주 도구")
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)
        toolbar.addAction(self._icon("SP_DialogOpenButton"), "열기", self.open_project_dialog)
        toolbar.addAction(self._icon("SP_DialogSaveButton"), "저장", self.save_project)
        toolbar.addSeparator()
        toolbar.addAction(self._icon("SP_DialogApplyButton"), "검사", self.run_check)
        run_action = toolbar.addAction(self._icon("SP_MediaPlay"), "실행", self.run_solve)
        toolbar.widgetForAction(run_action).setObjectName("primary")

        # left dock: model tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("모델")
        self.tree.itemSelectionChanged.connect(self._on_select)
        tree_dock = QDockWidget("모델 탐색기")
        tree_dock.setWidget(self.tree)
        self.addDockWidget(Qt.LeftDockWidgetArea, tree_dock)

        # centre: viewport with a small view toolbar
        centre = QWidget()
        centre_layout = QVBoxLayout(centre)
        centre_layout.setContentsMargins(0, 0, 0, 0)
        view_bar = QHBoxLayout()
        for label, name in (("등각", "iso"), ("정면", "front"), ("위", "top")):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, n=name: self.viewport.set_view(n))
            view_bar.addWidget(btn)
        view_bar.addWidget(QLabel("변형 배율"))
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.1, 100.0)
        self.scale_spin.setValue(1.0)
        self.scale_spin.setDecimals(1)
        self.scale_spin.valueChanged.connect(self._rescale)
        view_bar.addWidget(self.scale_spin)
        view_bar.addStretch(1)
        self.legend_label = QLabel("")
        self.legend_label.setObjectName("panelHint")
        view_bar.addWidget(self.legend_label)
        centre_layout.addLayout(view_bar)
        self.viewport = Viewport()
        centre_layout.addWidget(self.viewport, 1)
        self.setCentralWidget(centre)

        # right dock: property editor
        self.props_panel = QWidget()
        self.props_panel.setObjectName("propsPanel")
        self.props_layout = QFormLayout(self.props_panel)
        self.props_hint = QLabel("트리에서 항목을 고르면 여기서 고칠 수 있습니다.")
        self.props_hint.setObjectName("panelHint")
        self.props_hint.setWordWrap(True)
        self.props_layout.addRow(self.props_hint)
        props_dock = QDockWidget("속성")
        props_dock.setWidget(self.props_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, props_dock)

        # bottom dock: messages + flow
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        status_row = QHBoxLayout()
        self.status = QLabel("준비됨 — 파일 ▸ 열기로 프로젝트를 여세요.")
        status_row.addWidget(self.status, 1)
        self.btn_goto = QPushButton("고칠 위치로 이동")
        self.btn_goto.setEnabled(False)
        self.btn_goto.clicked.connect(self._goto_issue)
        status_row.addWidget(self.btn_goto)
        bottom_layout.addLayout(status_row)
        self.log = QTextEdit()
        self.log.setObjectName("logView")
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        bottom_layout.addWidget(self.log)
        flow_row = QHBoxLayout()
        self.flow_labels = []
        for i, name in enumerate(FLOW_STEPS):
            chip = QLabel(f"{i + 1} {name}")
            chip.setProperty("class", "chip")
            chip.setProperty("active", i == 0)
            self.flow_labels.append(chip)
            flow_row.addWidget(chip)
        flow_row.addStretch(1)
        bottom_layout.addLayout(flow_row)
        bottom_dock = QDockWidget("메시지")
        bottom_dock.setWidget(bottom)
        self.addDockWidget(Qt.BottomDockWidgetArea, bottom_dock)

        self.statusBar().showMessage("솔버: CalculiX (변환기 1개 연결됨)")

    # -- tree ---------------------------------------------------------------------
    def set_model(self, model: StructuralModel, folder: Path | None = None) -> None:
        self.model = model
        self.project_folder = folder
        self._dirty = False
        self._path_items.clear()
        self.tree.clear()
        root = QTreeWidgetItem([model.name])
        root.setIcon(0, self._icon("SP_ComputerIcon"))
        root.setData(0, Qt.UserRole, "model")
        self.tree.addTopLevelItem(root)
        self._path_items["model"] = root
        groups: dict[str, QTreeWidgetItem] = {}
        for gname in TREE_GROUPS:
            item = QTreeWidgetItem([gname])
            item.setIcon(0, self._icon("SP_DirClosedIcon"))
            root.addChild(item)
            groups[gname] = item
        for i, m in enumerate(model.materials):
            self._add(groups["재료·단면"], f"{m.name} · {m.law}", f"materials[{i}]")
        for i, s in enumerate(model.sections):
            self._add(groups["재료·단면"], f"단면 {s.name} · {s.kind}", f"sections[{i}]")
        self._add(groups["메시"],
                  f"절점 {len(model.mesh.nodes):,}개 · 요소 {len(model.mesh.elements):,}개", "mesh")
        for i, st in enumerate(model.steps):
            self._add(groups["해석 단계"], f"{st.name} · {st.kind}", f"steps[{i}]")
        for i, ld in enumerate(model.loads):
            self._add(groups["하중·구속"], f"하중 {ld.name} · {ld.kind}", f"loads[{i}]")
        for i, bc in enumerate(model.bcs):
            self._add(groups["하중·구속"], f"구속 {bc.name}", f"bcs[{i}]")
        for i, inter in enumerate(model.interactions):
            self._add(groups["접촉·연결"], f"{inter.name} · {inter.kind}", f"interactions[{i}]")
        self._add(groups["작업"], "CalculiX", "job")
        self.results_item = groups["결과"]
        self.tree.expandAll()
        self.viewport.show_model(model)
        self.status.setText("모델을 열었습니다. 다음 행동: 검사")
        self._log(f"모델 '{model.name}'을 열었습니다.")
        self._set_flow(1)

    def _add(self, parent: QTreeWidgetItem, text: str, path: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        item.setIcon(0, self._icon("SP_FileIcon"))
        item.setData(0, Qt.UserRole, path)
        parent.addChild(item)
        self._path_items[path] = item
        return item

    def select_path(self, path: str) -> bool:
        item = self._path_items.get(path)
        if item is None:
            return False
        self.tree.setCurrentItem(item)
        return True

    # -- property editor ---------------------------------------------------------
    def _on_select(self) -> None:
        items = self.tree.selectedItems()
        if not items or self.model is None:
            return
        self._show_props(items[0].data(0, Qt.UserRole) or "model")

    def _clear_props(self) -> None:
        while self.props_layout.rowCount():
            self.props_layout.removeRow(0)

    def _mark_dirty(self) -> None:
        if not self._dirty:
            self._dirty = True
            self.status.setText("변경 사항이 있습니다. 저장하세요.")

    def _show_props(self, path: str) -> None:
        m = self.model
        assert m is not None
        self._clear_props()
        lay = self.props_layout
        if path == "model":
            name_edit = QLineEdit(m.name)
            name_edit.editingFinished.connect(
                lambda: (setattr(m, "name", name_edit.text()), self._mark_dirty()))
            lay.addRow("이름", name_edit)
            lay.addRow("단위계", QLabel("SI (m, kg, s, Pa)"))
            lay.addRow("형상 원본", QLabel(m.geometry_source or "-"))
            return
        if path == "mesh":
            types = sorted({e["type"] for e in m.mesh.elements.values()})
            lay.addRow("절점", QLabel(f"{len(m.mesh.nodes):,}개"))
            lay.addRow("요소", QLabel(f"{len(m.mesh.elements):,}개"))
            lay.addRow("요소 종류", QLabel(", ".join(types)))
            return
        if path == "job":
            lay.addRow("솔버", QLabel("CalculiX"))
            lay.addRow("주 행동", QLabel("상단 '실행' 버튼"))
            return
        if path == "result":
            if self._last_result is not None:
                lay.addRow("최대 변위", QLabel(fmt_length(self._last_result.max_displacement)))
                lay.addRow("반력 합 Fy", QLabel(fmt_force(self._last_result.reaction_totals.get("fy", 0.0))))
                lay.addRow("검증 상태", QLabel(self._last_result.verification))
            return
        kind, _, rest = path.partition("[")
        if not rest:
            return
        idx = int(rest.rstrip("]"))
        if kind == "materials":
            mat = m.materials[idx]
            lay.addRow("이름", QLabel(mat.name))
            lay.addRow("법칙", QLabel(mat.law))
            e_spin = QDoubleSpinBox()
            e_spin.setRange(0.001, 5000.0)
            e_spin.setDecimals(2)
            e_spin.setSuffix(" GPa")
            e_spin.setValue(mat.params.get("youngs_modulus", 0.0) / 1e9)
            e_spin.valueChanged.connect(
                lambda v: (mat.params.__setitem__("youngs_modulus", v * 1e9), self._mark_dirty()))
            lay.addRow("탄성계수", e_spin)
            nu_spin = QDoubleSpinBox()
            nu_spin.setRange(0.0, 0.49)
            nu_spin.setSingleStep(0.01)
            nu_spin.setDecimals(3)
            nu_spin.setValue(mat.params.get("poissons_ratio", 0.0))
            nu_spin.valueChanged.connect(
                lambda v: (mat.params.__setitem__("poissons_ratio", v), self._mark_dirty()))
            lay.addRow("포아송비", nu_spin)
            if "density" in mat.params:
                lay.addRow("밀도", QLabel(f"{mat.params['density']:,.0f} kg/m³"))
            return
        if kind == "loads":
            ld = m.loads[idx]
            lay.addRow("이름", QLabel(ld.name))
            lay.addRow("종류", QLabel(ld.kind))
            lay.addRow("대상 집합", QLabel(ld.target_set or "-"))
            for comp in ("fx", "fy", "fz"):
                spin = QDoubleSpinBox()
                spin.setRange(-1e12, 1e12)
                spin.setDecimals(1)
                spin.setSuffix(" N")
                spin.setValue(ld.values.get(comp, 0.0))
                spin.valueChanged.connect(
                    lambda v, c=comp: (ld.values.__setitem__(c, v), self._mark_dirty()))
                lay.addRow(f"힘 {comp.upper()}", spin)
            return
        if kind == "bcs":
            bc = m.bcs[idx]
            lay.addRow("이름", QLabel(bc.name))
            lay.addRow("대상 집합", QLabel(bc.target_set))
            for dof, label in ((1, "UX"), (2, "UY"), (3, "UZ")):
                chk = QCheckBox(label)
                chk.setChecked(dof in bc.fixed_dofs)
                chk.toggled.connect(lambda on, d=dof: self._toggle_dof(bc, d, on))
                lay.addRow("고정", chk)
            return
        if kind == "steps":
            st = m.steps[idx]
            lay.addRow("이름", QLabel(st.name))
            lay.addRow("종류", QLabel(st.kind))
            lay.addRow("하중", QLabel(", ".join(st.loads) or "-"))
            lay.addRow("구속", QLabel(", ".join(st.bcs) or "-"))
            return
        obj = {"sections": m.sections, "interactions": m.interactions}[kind][idx]
        for key, value in vars(obj).items():
            lay.addRow(str(key), QLabel(str(value)))

    def _toggle_dof(self, bc, dof: int, on: bool) -> None:
        if on and dof not in bc.fixed_dofs:
            bc.fixed_dofs.append(dof)
        elif not on and dof in bc.fixed_dofs:
            bc.fixed_dofs.remove(dof)
        self._mark_dirty()

    # -- actions ------------------------------------------------------------------
    def _log(self, text: str) -> None:
        self.log.append(text)

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
        self._dirty = False
        self.status.setText(f"저장했습니다: {folder}")
        self._log(f"프로젝트를 저장했습니다: {folder}")

    def run_check(self) -> None:
        if self.model is None:
            self.status.setText("먼저 모델을 여세요.")
            return
        issues = self.workbench.check(self.model)
        if not issues:
            self.status.setText("검사 통과: 입력 오류가 없습니다. 다음 행동: 실행")
            self._log("입력 검사 통과.")
            self.btn_goto.setEnabled(False)
            self._issue_target = None
            self._set_flow(2)
            return
        first = issues[0]
        self.status.setText(f"검사 오류 {len(issues)}건: {first.message}")
        self._log(f"검사 오류 {len(issues)}건. 첫 오류: {first.message} ({first.target_path})")
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
        if job.result is None:
            self.status.setText(f"작업 상태: {job.state}")
            return
        result = job.result
        self._last_result = result
        if result.success:
            summary = (
                f"계산 완료 — 최대 변위 {fmt_length(result.max_displacement)}, "
                f"반력 합 Fy {fmt_force(result.reaction_totals.get('fy', 0.0))} "
                f"({result.verification})"
            )
            self._add(self.results_item, f"CalculiX · 최대 변위 {fmt_length(result.max_displacement)}", "result")
            self.viewport.show_results(self.model, result)
            self.legend_label.setText("색상 범례: 변위 (mm)")
            self._set_flow(3)
        else:
            summary = "계산 실패 — 아래 로그를 확인하세요."
            self._log(result.log_tail[-1500:])
        self.status.setText(summary)
        self._log(summary)

    def _rescale(self) -> None:
        self.viewport.rescale(self.scale_spin.value())

    def show_diagnostics(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("진단 정보")
        text = QTextEdit()
        text.setReadOnly(True)
        info = [
            f"모델: {self.model.name if self.model else '-'}",
            f"프로젝트 폴더: {self.project_folder or '-'}",
            f"작업 수: {len(self.workbench.jobs.jobs)}",
            f"3D 화면: {'켜짐' if self.viewport.available else '꺼짐(헤드리스)'}",
        ]
        text.setPlainText("\n".join(info))
        layout = QVBoxLayout(dlg)
        layout.addWidget(text)
        dlg.exec()

    def _set_flow(self, upto: int) -> None:
        for i, chip in enumerate(self.flow_labels):
            chip.setProperty("active", i <= upto)
            chip.style().unpolish(chip)
            chip.style().polish(chip)
