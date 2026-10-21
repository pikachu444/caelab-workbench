"""Workbench main window (PySide6).

The layout follows the structure of mainstream CAE desktops:
Abaqus/CAE's module workflow (a module bar switches the tree, the
context toolbar and the property editor to the current task) combined
with a docked model tree, a VTK viewport, a message area, and a status
bar. One primary action per surface; input errors are one line plus a
jump to the object; internal ids live only in Help > Diagnostics.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QStyle,
    QTextEdit,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from caelab.model.schema import StructuralModel
from caelab.workbench import Workbench

from .icons import GROUP_ICONS, LEAF_ICONS, icon
from .theme import THEME
from .viewport import Viewport

FLOW_STEPS = ["열기", "검사", "실행", "결과", "토론", "제안 적용", "재실행"]
TREE_GROUPS = ["재료·단면", "메시", "해석 단계", "하중·구속", "접촉·연결", "작업", "결과"]
MODULES = [
    ("모델", TREE_GROUPS),
    ("재료", ["재료·단면"]),
    ("메시", ["메시"]),
    ("해석 단계", ["해석 단계"]),
    ("하중·구속", ["하중·구속", "접촉·연결"]),
    ("작업", ["작업", "결과"]),
    ("결과", ["결과"]),
    ("전문가", []),
]


def fmt_length(m: float) -> str:
    if m != 0 and abs(m) < 0.01:
        return f"{m * 1000:.3f} mm"
    return f"{m:.4f} m"


def fmt_force(n: float) -> str:
    return f"{n:,.1f} N"


import re as _re

_PATH_SEG = _re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\[(\d+)\])?$")


def _path_step(obj, segment: str):
    match = _PATH_SEG.match(segment)
    if not match:
        raise ValueError(f"잘못된 경로 조각입니다: {segment}")
    value = obj[match.group(1)] if isinstance(obj, dict) else getattr(obj, match.group(1))
    if match.group(3) is not None:
        value = value[int(match.group(3))]
    return value


def _path_get(root, path: str):
    obj = root
    for segment in path.split("."):
        obj = _path_step(obj, segment)
    return obj


def _path_set(root, path: str, new_value) -> None:
    segments = path.split(".")
    parent = root
    for segment in segments[:-1]:
        parent = _path_step(parent, segment)
    last = _PATH_SEG.match(segments[-1])
    if not last:
        raise ValueError(f"잘못된 경로 조각입니다: {segments[-1]}")
    name, index = last.group(1), last.group(3)
    if index is None:
        if isinstance(parent, dict):
            parent[name] = new_value
        else:
            setattr(parent, name, new_value)
        return
    container = parent[name] if isinstance(parent, dict) else getattr(parent, name)
    container[int(index)] = new_value


class MainWindow(QMainWindow):
    def __init__(self, workbench: Workbench | None = None) -> None:
        super().__init__()
        self.workbench = workbench or Workbench()
        self.model: StructuralModel | None = None
        self.project_folder: Path | None = None
        self._issue_target: str | None = None
        self._path_items: dict[str, QTreeWidgetItem] = {}
        self._group_items: dict[str, QTreeWidgetItem] = {}
        self._dirty = False
        self._last_result = None
        self.module = "모델"
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
        analysis_menu.addAction("최적화 패널 열기", self.open_optimization_panel)
        results_menu = menu.addMenu("결과")
        results_menu.addAction("ParaView에서 열기 (다음 단계)",
                               lambda: self._log("ParaView 연결은 다음 단계에서 붙입니다."))
        expert_menu = menu.addMenu("전문가")
        expert_menu.addAction("전문가 패널 열기", self.open_expert_panel)
        expert_menu.addAction("선택 대상으로 논의", self.discuss_selection)
        help_menu = menu.addMenu("도움말")
        help_menu.addAction("진단 정보", self.show_diagnostics)

        # module bar (Abaqus/CAE style): switches tree + context tools
        module_bar = QToolBar("모듈")
        module_bar.setMovable(False)
        self.addToolBar(module_bar)
        module_bar.addWidget(QLabel(" 모듈: "))
        self.module_group = QButtonGroup(self)
        self.module_group.setExclusive(True)
        self._module_buttons: dict[str, QToolButton] = {}
        for name, _groups in MODULES:
            btn = QToolButton()
            btn.setText(name)
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
            btn.clicked.connect(lambda _=False, n=name: self.set_module(n))
            self.module_group.addButton(btn)
            module_bar.addWidget(btn)
            self._module_buttons[name] = btn
            if name == "모델":
                btn.setChecked(True)
        module_bar.addSeparator()
        module_bar.addAction(icon("folder"), "열기", self.open_project_dialog)
        module_bar.addAction(icon("save"), "저장", self.save_project)
        module_bar.addSeparator()
        module_bar.addAction(icon("check"), "검사", self.run_check)
        run_action = module_bar.addAction(icon("run"), "실행", self.run_solve)
        module_bar.widgetForAction(run_action).setObjectName("primary")

        # context toolbar: actions for the current module
        self.context_bar = QToolBar("모듈 도구")
        self.context_bar.setMovable(False)
        self.addToolBarBreak()
        self.addToolBar(self.context_bar)
        self._fill_context_bar()

        # left dock: model tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("모델")
        self.tree.itemSelectionChanged.connect(self._on_select)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        tree_dock = QDockWidget("모델 탐색기")
        tree_dock.setWidget(self.tree)
        self.addDockWidget(Qt.LeftDockWidgetArea, tree_dock)

        # centre: viewport with a view toolbar
        centre = QWidget()
        centre_layout = QVBoxLayout(centre)
        centre_layout.setContentsMargins(0, 0, 0, 0)
        view_bar = QHBoxLayout()
        for label, name in (("등각", "iso"), ("정면", "front"), ("위", "top")):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, n=name: self.viewport.set_view(n))
            view_bar.addWidget(btn)
        fit_btn = QPushButton("화면 맞춤")
        fit_btn.clicked.connect(lambda: self.viewport.fit())
        view_bar.addWidget(fit_btn)
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

        # right dock: property viewer (display only)
        self.props_panel = QWidget()
        self.props_panel.setObjectName("propsPanel")
        self.props_layout = QVBoxLayout(self.props_panel)
        self.props_title = QLabel("속성")
        self.props_title.setStyleSheet("font-weight: bold;")
        self.props_layout.addWidget(self.props_title)
        self.props_form_holder = QWidget()
        self.props_form = QFormLayout(self.props_form_holder)
        self.props_layout.addWidget(self.props_form_holder)
        self.props_hint = QLabel("트리에서 항목을 고르면 여기서 값을 확인할 수 있습니다.")
        self.props_hint.setObjectName("panelHint")
        self.props_hint.setWordWrap(True)
        self.props_layout.addWidget(self.props_hint)
        self.props_layout.addStretch(1)
        # This panel is a viewer, not an editor (DESIGN: the built-in
        # screens stay at check/run/summary level; preprocessing edits
        # belong to the native programs). The guide stays visible under
        # the values so the way to change a value is never a dead end:
        # edit in the native program, or apply an expert proposal.
        self.props_guide = QLabel(
            "값은 여기서 고치지 않습니다. 형상·재료·조건을 바꾸려면 "
            "FreeCAD·Salome 같은 전용 프로그램에서 여세요. 이 화면의 값은 "
            "전문가 토론에서 제안을 적용할 때만 바뀝니다.")
        self.props_guide.setObjectName("panelHint")
        self.props_guide.setWordWrap(True)
        self.props_layout.addWidget(self.props_guide)
        props_dock = QDockWidget("속성")
        props_dock.setWidget(self.props_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, props_dock)
        self.props_dock = props_dock

        # right dock, tabbed with properties: expert discussion panel
        from .expert_panel import ExpertPanel

        self.expert_panel = ExpertPanel(
            get_model=lambda: self.model,
            get_result=lambda: self._last_result,
            apply_proposal=self._apply_proposal,
            run_solve=self.run_solve,
            on_discussed=lambda: self._set_flow(4),
        )
        self.expert_dock = QDockWidget("전문가 토론")
        self.expert_dock.setWidget(self.expert_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, self.expert_dock)
        self.tabifyDockWidget(props_dock, self.expert_dock)
        self.expert_dock.hide()

        # right dock, tabbed with properties: optimization panel
        from .optimization_panel import OptimizationPanel

        self.optimization_panel = OptimizationPanel()
        self.optimization_dock = QDockWidget("최적화")
        self.optimization_dock.setWidget(self.optimization_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, self.optimization_dock)
        self.tabifyDockWidget(props_dock, self.optimization_dock)
        self.optimization_dock.hide()

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

        self.statusBar().showMessage("작업 없음")
        self.statusBar().addPermanentWidget(QLabel("솔버: CalculiX · 단위계: SI"))

        # Give the docks usable starting widths. At the default split
        # the property dock came up ~200 px wide, which clipped the
        # spin boxes (value + unit + arrows did not fit).
        self.props_panel.setMinimumWidth(250)
        self.resizeDocks([tree_dock, props_dock], [280, 320], Qt.Horizontal)
        self.resizeDocks([bottom_dock], [190], Qt.Vertical)

    # -- modules ------------------------------------------------------------
    def _fill_context_bar(self) -> None:
        self.context_bar.clear()
        actions = {
            "모델": [("열기", self.open_project_dialog), ("저장", self.save_project)],
            "재료": [("재료 편집 안내", lambda: self._log("재료는 여기서 고치지 않습니다. FreeCAD 같은 전용 프로그램에서 고치거나, 전문가 토론의 제안을 적용하세요."))],
            "메시": [("메시 다시 표시", lambda: self.model and self.viewport.show_model(self.model))],
            "해석 단계": [],
            "하중·구속": [],
            "작업": [("입력 검사", self.run_check), ("실행", self.run_solve)],
            "결과": [("ParaView에서 열기 (다음 단계)", lambda: self._log("ParaView 연결은 다음 단계에서 붙입니다."))],
            "전문가": [("전문가 패널 열기", self.open_expert_panel), ("선택 대상으로 논의", self.discuss_selection)],
        }.get(self.module, [])
        for label, fn in actions:
            self.context_bar.addAction(label, fn)
        if not actions:
            self.context_bar.addWidget(QLabel(f" {self.module} 모듈 — 값은 속성 패널에서 확인합니다."))

    def set_module(self, name: str) -> None:
        self.module = name
        btn = self._module_buttons.get(name)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        groups = dict(MODULES)[name]
        for gname, item in self._group_items.items():
            item.setHidden(gname not in groups)
        self._fill_context_bar()
        if name == "전문가":
            self.open_expert_panel()
            return
        # select the first visible leaf of the module for immediate context
        for gname in groups:
            item = self._group_items.get(gname)
            if item is not None and item.childCount():
                self.tree.setCurrentItem(item.child(0))
                return

    def _tree_menu(self, pos: QPoint) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("속성 보기", self._on_select)
        menu.addSeparator()
        menu.addAction("입력 검사", self.run_check)
        menu.addAction("실행", self.run_solve)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # -- tree ---------------------------------------------------------------------
    def set_model(self, model: StructuralModel, folder: Path | None = None) -> None:
        self.model = model
        self.project_folder = folder
        self._dirty = False
        self._path_items.clear()
        self._group_items.clear()
        self.tree.clear()
        root = QTreeWidgetItem([model.name])
        root.setIcon(0, icon(LEAF_ICONS["model"]))
        root.setData(0, Qt.UserRole, "model")
        self.tree.addTopLevelItem(root)
        self._path_items["model"] = root
        groups: dict[str, QTreeWidgetItem] = {}
        for gname in TREE_GROUPS:
            item = QTreeWidgetItem([gname])
            item.setIcon(0, icon(GROUP_ICONS.get(gname, "folder")))
            root.addChild(item)
            groups[gname] = item
            self._group_items[gname] = item
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
        self.set_module(self.module)
        self.viewport.show_model(model)
        self.status.setText("모델을 열었습니다. 다음 행동: 검사")
        self._log(f"모델 '{model.name}'을 열었습니다.")
        self._set_flow(1)

    def _add(self, parent: QTreeWidgetItem, text: str, path: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        kind = path.partition("[")[0]
        item.setIcon(0, icon(LEAF_ICONS.get(kind, "folder")))
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

    # -- property viewer (display only; edits live in the native programs) -------
    def _on_select(self) -> None:
        items = self.tree.selectedItems()
        if not items or self.model is None:
            return
        self._show_props(items[0].data(0, Qt.UserRole) or "model")

    def _clear_props(self) -> None:
        while self.props_form.rowCount():
            self.props_form.removeRow(0)

    def _mark_dirty(self) -> None:
        if not self._dirty:
            self._dirty = True
            self.status.setText("변경 사항이 있습니다. 저장하세요.")

    def _show_props(self, path: str) -> None:
        m = self.model
        assert m is not None
        self._clear_props()
        lay = self.props_form
        self.props_hint.setText("")
        if path == "model":
            self.props_title.setText("모델")
            lay.addRow("이름", QLabel(m.name))
            lay.addRow("단위계", QLabel("SI (m, kg, s, Pa)"))
            lay.addRow("형상 원본", QLabel(m.geometry_source or "-"))
            return
        if path == "mesh":
            self.props_title.setText("메시")
            types = sorted({e["type"] for e in m.mesh.elements.values()})
            lay.addRow("절점", QLabel(f"{len(m.mesh.nodes):,}개"))
            lay.addRow("요소", QLabel(f"{len(m.mesh.elements):,}개"))
            lay.addRow("요소 종류", QLabel(", ".join(types)))
            return
        if path == "job":
            self.props_title.setText("작업")
            lay.addRow("솔버", QLabel("CalculiX"))
            lay.addRow("주 행동", QLabel("상단 '실행' 버튼"))
            return
        if path == "result":
            self.props_title.setText("결과")
            if self._last_result is not None:
                lay.addRow("최대 변위", QLabel(fmt_length(self._last_result.max_displacement)))
                lay.addRow("반력 합 Fy", QLabel(fmt_force(self._last_result.reaction_totals.get("fy", 0.0))))
                lay.addRow("검증 상태", QLabel(self._last_result.verification))
            return
        kind, _, rest = path.partition("[")
        if not rest:
            self.props_title.setText(path)
            return
        idx = int(rest.rstrip("]"))
        if kind == "materials":
            mat = m.materials[idx]
            self.props_title.setText(f"재료 — {mat.name}")
            lay.addRow("이름", QLabel(mat.name))
            lay.addRow("법칙", QLabel(mat.law))
            lay.addRow("탄성계수",
                       QLabel(f"{mat.params.get('youngs_modulus', 0.0) / 1e9:.2f} GPa"))
            lay.addRow("포아송비",
                       QLabel(f"{mat.params.get('poissons_ratio', 0.0):.3f}"))
            if "density" in mat.params:
                lay.addRow("밀도", QLabel(f"{mat.params['density']:,.0f} kg/m³"))
            return
        if kind == "loads":
            ld = m.loads[idx]
            self.props_title.setText(f"하중 — {ld.name}")
            lay.addRow("이름", QLabel(ld.name))
            lay.addRow("종류", QLabel(ld.kind))
            lay.addRow("대상 집합", QLabel(ld.target_set or "-"))
            for comp in ("fx", "fy", "fz"):
                lay.addRow(f"힘 {comp.upper()}",
                           QLabel(fmt_force(ld.values.get(comp, 0.0))))
            return
        if kind == "bcs":
            bc = m.bcs[idx]
            self.props_title.setText(f"구속 — {bc.name}")
            lay.addRow("이름", QLabel(bc.name))
            lay.addRow("대상 집합", QLabel(bc.target_set))
            dof_names = {1: "UX", 2: "UY", 3: "UZ"}
            fixed = [dof_names[d] for d in sorted(bc.fixed_dofs) if d in dof_names]
            lay.addRow("고정", QLabel(", ".join(fixed) if fixed else "없음"))
            return
        if kind == "steps":
            st = m.steps[idx]
            self.props_title.setText(f"해석 단계 — {st.name}")
            lay.addRow("이름", QLabel(st.name))
            lay.addRow("종류", QLabel(st.kind))
            lay.addRow("하중", QLabel(", ".join(st.loads) or "-"))
            lay.addRow("구속", QLabel(", ".join(st.bcs) or "-"))
            return
        obj = {"sections": m.sections, "interactions": m.interactions}[kind][idx]
        self.props_title.setText(str(getattr(obj, "name", kind)))
        for key, value in vars(obj).items():
            lay.addRow(str(key), QLabel(str(value)))

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

    # -- optimization panel ---------------------------------------------------
    def open_optimization_panel(self) -> None:
        self.optimization_dock.show()
        self.optimization_dock.raise_()

    # -- expert panel ---------------------------------------------------------
    def open_expert_panel(self) -> None:
        self.expert_dock.show()
        self.expert_dock.raise_()
        self.expert_panel.refresh_evidence()

    def discuss_selection(self) -> None:
        items = self.tree.selectedItems()
        path = items[0].data(0, Qt.UserRole) if items else None
        self.expert_panel.set_target(path)
        self.open_expert_panel()

    def _apply_proposal(self, proposal) -> str:
        """Apply one expert proposal to the model. User-triggered only.

        The recorded "before" value must still match the model —
        otherwise the proposal is stale and is not applied.
        """
        if self.model is None:
            return "모델이 열려 있지 않습니다."
        try:
            current = _path_get(self.model, proposal.target_path)
        except (ValueError, KeyError, AttributeError, IndexError, TypeError) as exc:
            return f"경로를 찾을 수 없습니다: {exc}"
        if isinstance(current, (int, float)) and isinstance(proposal.before, (int, float)):
            matches = abs(float(current) - float(proposal.before)) <= 1e-9 * max(1.0, abs(float(current)))
        else:
            matches = current == proposal.before
        if not matches:
            return f"현재 값({current})이 제안의 이전 값({proposal.before})과 달라 적용하지 않았습니다."
        new_value = proposal.after
        if isinstance(current, float) and isinstance(new_value, (int, float)):
            new_value = float(new_value)
        elif isinstance(current, int) and isinstance(new_value, (int, float)):
            new_value = int(new_value)
        _path_set(self.model, proposal.target_path, new_value)
        self._mark_dirty()
        self._log(f"전문가 제안을 적용했습니다: {proposal.target_path} {current} → {new_value}")
        return "적용했습니다. 저장하고, 필요하면 실행 버튼으로 다시 계산하세요."

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
            self.expert_panel.refresh_evidence()
            self._set_flow(3)
        else:
            summary = "계산 실패 — 아래 로그를 확인하세요."
            self._log(result.log_tail[-1500:])
        self.status.setText(summary)
        self._log(summary)
        state_ko = {"succeeded": "완료", "failed": "실패", "running": "실행 중",
                    "queued": "대기", "preparing": "준비 중"}.get(job.state, job.state)
        self.statusBar().showMessage(f"최근 작업: {state_ko}")

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
