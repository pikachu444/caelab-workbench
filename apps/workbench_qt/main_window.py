"""Workbench main window (PySide6).

The layout follows the structure of mainstream CAE desktops:
Abaqus/CAE's module workflow (a module bar switches the tree, the
context toolbar and the property editor to the current task) combined
with a docked model tree, a VTK viewport, a message area, and a status
bar. One primary action per surface; input errors are one line plus a
jump to the object; internal ids live only in Help > Diagnostics.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
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
from caelab.model.store import model_revision, project_revision
from caelab.workbench import Workbench

from .icons import GROUP_ICONS, LEAF_ICONS, icon
from .theme import (
    build_stylesheet,
    current_theme_mode,
    register_theme_listener,
    theme_toggle_label,
    toggle_theme_mode,
)
from .viewport import Viewport

FLOW_STEPS = ["열기", "검사", "실행", "결과"]
TREE_GROUPS = ["재료·단면", "메시", "해석 단계", "하중·구속", "접촉·연결", "작업", "결과"]
MODULES = [
    ("모델", TREE_GROUPS),
    ("재료", ["재료·단면"]),
    ("메시", ["메시"]),
    ("해석 단계", ["해석 단계"]),
    ("하중·구속", ["하중·구속", "접촉·연결"]),
    ("작업", ["작업", "결과"]),
    ("결과", ["결과"]),
]


def fmt_length(m: float | None, unit: str | None = "m") -> str:
    if m is None:
        return "미확인"
    if unit != "m":
        return f"{m:.6g} {unit or '(단위 미확인 — 사용자 선언 필요)'}"
    if m != 0 and abs(m) < 0.01:
        return f"{m * 1000:.3f} mm"
    return f"{m:.4f} m"


def fmt_force(n: float | None, unit: str | None = "N") -> str:
    if n is None:
        return "미확인"
    return f"{n:,.1f} {unit or '(단위 미확인 — 사용자 선언 필요)'}"


class MainWindow(QMainWindow):
    def __init__(self, workbench: Workbench | None = None) -> None:
        super().__init__()
        self.workbench = workbench or Workbench()
        self.model: StructuralModel | None = None
        self.project_folder: Path | None = None
        self._saved_model_revision = ''
        self._issue_target: str | None = None
        self._path_items: dict[str, QTreeWidgetItem] = {}
        self._group_items: dict[str, QTreeWidgetItem] = {}
        self._last_result = None
        self._active_run_id = None
        self._active_folder = None
        self._model_generation = 0
        self._selection_generation = 0
        self._displayed_run_id = None
        self._displayed_run_label = ''
        self._displayed_model_name = ''
        self._displayed_state_text = ''
        self._run_labels = {}
        self._close_pending = False
        self._locked_actions = []
        self._context_locked_actions = []
        self._cancel_actions = []
        self.module = "모델"
        self._toolbar_icon_actions: list[tuple] = []
        self.setWindowTitle("CAELAB 구조 해석")
        self.resize(1440, 900)
        self._build_ui()
        self._run_timer = QTimer(self)
        self._run_timer.setInterval(100)
        self._run_timer.timeout.connect(self._poll_run)
        self.apply_theme(current_theme_mode())
        register_theme_listener(self.apply_theme)

    # -- construction ---------------------------------------------------------
    def _icon(self, name: str):
        return self.style().standardIcon(getattr(QStyle, name))

    def _unit_system_text(self, model: StructuralModel | None = None) -> str:
        model = model if model is not None else self.model
        if model is None:
            return "모델 없음"
        declaration = model.unit_system
        if declaration == "si":
            return "SI (m, kg, s, Pa)"
        if declaration is None or not str(declaration).strip():
            return "미선언 (단위 미확인)"
        return f"{declaration} (세부 단위 미확인)"

    def _build_ui(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("파일")
        self._locked_actions.append(file_menu.addAction("열기…", self.open_project_dialog))
        self._locked_actions.append(file_menu.addAction("저장", self.save_project))
        analysis_menu = menu.addMenu("해석")
        self._locked_actions.append(analysis_menu.addAction("입력 검사", self.run_check))
        self._locked_actions.append(analysis_menu.addAction("실행", self.run_solve))
        self._cancel_actions.append(analysis_menu.addAction("계산 취소", self.cancel_run))
        analysis_menu.addAction("MDO Studio 열기", self.open_optimization_panel)
        results_menu = menu.addMenu("결과")
        results_menu.addAction("ParaView에서 열기 (다음 단계)",
                               lambda: self._log("ParaView 연결은 다음 단계에서 붙입니다."))
        view_menu = menu.addMenu("보기")
        self.theme_action = view_menu.addAction(theme_toggle_label(), toggle_theme_mode)
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
        open_act = module_bar.addAction(icon("folder"), "열기", self.open_project_dialog)
        save_act = module_bar.addAction(icon("save"), "저장", self.save_project)
        module_bar.addSeparator()
        check_act = module_bar.addAction(icon("check"), "검사", self.run_check)
        run_action = module_bar.addAction(icon("run"), "실행", self.run_solve)
        self.run_action = run_action
        run_action.setToolTip('현재 프로젝트 모델을 CalculiX로 계산합니다.')
        self._locked_actions.extend((open_act, save_act, check_act, run_action))
        self.cancel_action = module_bar.addAction("계산 취소", self.cancel_run)
        self._cancel_actions.append(self.cancel_action)
        for action in self._cancel_actions:
            action.setEnabled(False)
        module_bar.widgetForAction(run_action).setObjectName("primary")
        self._toolbar_icon_actions = [
            (open_act, "folder"),
            (save_act, "save"),
            (check_act, "check"),
            (run_action, "run"),
        ]

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

        view_bar.addWidget(QLabel("표시 필드"))
        self.field_combo = QComboBox()
        self.field_combo.addItem("변위 (크기)", "U_mag")
        self.field_combo.addItem("변위 X (Ux)", "U_x")
        self.field_combo.addItem("변위 Y (Uy)", "U_y")
        self.field_combo.addItem("변위 Z (Uz)", "U_z")
        self.field_combo.addItem("Von Mises 응력", "von_mises")
        self.field_combo.currentIndexChanged.connect(self._on_display_setting_changed)
        view_bar.addWidget(self.field_combo)

        view_bar.addWidget(QLabel("배율 모드"))
        self.scale_mode_combo = QComboBox()
        self.scale_mode_combo.addItem("자동 배율", "auto")
        self.scale_mode_combo.addItem("실변형 (1x)", "real")
        self.scale_mode_combo.currentIndexChanged.connect(self._on_display_setting_changed)
        view_bar.addWidget(self.scale_mode_combo)

        view_bar.addWidget(QLabel("배율 계수"))
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.1, 100.0)
        self.scale_spin.setValue(1.0)
        self.scale_spin.setDecimals(1)
        self.scale_spin.valueChanged.connect(self._on_display_setting_changed)
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
        # edit in the native program.
        self.props_guide = QLabel(
            "값은 여기서 고치지 않습니다. 형상·재료·조건을 바꾸려면 "
            "FreeCAD·Salome 같은 전용 프로그램에서 수정한 뒤 다시 여세요.")
        self.props_guide.setObjectName("panelHint")
        self.props_guide.setWordWrap(True)
        self.props_layout.addWidget(self.props_guide)
        props_dock = QDockWidget("속성")
        props_dock.setWidget(self.props_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, props_dock)
        self.props_dock = props_dock

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
        self.unit_system_status = QLabel(f"솔버: CalculiX · 단위계: {self._unit_system_text()}")
        self.statusBar().addPermanentWidget(self.unit_system_status)

        # Give the docks usable starting widths. At the default split
        # the property dock came up ~200 px wide, which clipped the
        # spin boxes (value + unit + arrows did not fit).
        self.props_panel.setMinimumWidth(250)
        self.resizeDocks([tree_dock, props_dock], [280, 320], Qt.Horizontal)
        self.resizeDocks([bottom_dock], [190], Qt.Vertical)

    # -- modules ------------------------------------------------------------
    def _fill_context_bar(self) -> None:
        self._context_locked_actions = []
        self.context_bar.clear()
        actions = {
            "모델": [("열기", self.open_project_dialog), ("저장", self.save_project)],
            "재료": [("재료 편집 안내", lambda: self._log("재료는 여기서 고치지 않습니다. FreeCAD 같은 전용 프로그램에서 수정한 뒤 다시 여세요."))],
            "메시": [("메시 다시 표시", lambda: self._show_current_model(manual=True))],
            "해석 단계": [],
            "하중·구속": [],
            "작업": [("입력 검사", self.run_check), ("실행", self.run_solve)],
            "결과": [("ParaView에서 열기 (다음 단계)", lambda: self._log("ParaView 연결은 다음 단계에서 붙입니다."))],
        }.get(self.module, [])
        for label, fn in actions:
            action = self.context_bar.addAction(label, fn)
            if fn in (self.open_project_dialog, self.save_project, self.run_check, self.run_solve):
                self._context_locked_actions.append(action)
                action.setEnabled(self._active_run_id is None)
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
        if self._active_run_id is not None:
            self.status.setText("계산을 취소하고 정리를 마친 뒤 다른 모델을 여세요.")
            return
        self._model_generation += 1
        self._displayed_run_id = None
        self.model = model
        self.unit_system_status.setText(f"솔버: CalculiX · 단위계: {self._unit_system_text()}")
        self.project_folder = Path(folder).resolve() if folder is not None else None
        self._saved_model_revision = model_revision(model) if folder is not None else ''
        self._last_result = None
        self._issue_target = None
        self.btn_goto.setEnabled(False)
        self.legend_label.clear()
        self.statusBar().clearMessage()
        self.log.clear()
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
        if folder is not None:
            self._refresh_run_history()
            try:
                selected = self.workbench.selected_model_run(self.project_folder)
                if selected is not None and selected.finished_at:
                    self._load_run_result(selected)
            except Exception as error:
                self.status.setText(f'보존 실행을 열지 못했습니다: {error}')

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
        path = items[0].data(0, Qt.UserRole) or "model"
        if path.startswith('runs.'):
            self._choose_run(path.removeprefix('runs.'))
            return
        if path == 'model':
            self._show_current_model(manual=True)
            return
        self._show_props(path)

    def _clear_props(self) -> None:
        while self.props_form.rowCount():
            self.props_form.removeRow(0)

    def _show_props(self, path: str) -> None:
        m = self.model
        assert m is not None
        self._clear_props()
        lay = self.props_form
        self.props_hint.setText("")
        if path == "model":
            self.props_title.setText("모델")
            lay.addRow("이름", QLabel(m.name))
            lay.addRow("단위계", QLabel(self._unit_system_text()))
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
            if self._displayed_run_id is not None:
                lay.addRow("선택 실행", QLabel(self._displayed_run_label))
                lay.addRow("실행 상태", QLabel(self._displayed_state_text))
                lay.addRow("실행 모델", QLabel(self._displayed_model_name))
                lay.addRow("프로젝트 모델", QLabel(m.name))
            if self._last_result is not None:
                lay.addRow("최대 변위", QLabel(fmt_length(self._last_result.max_displacement, self._last_result.units.get("length"))))
                lay.addRow("반력 합 Fy", QLabel(fmt_force(self._last_result.reaction_totals.get("fy"), self._last_result.units.get("force"))))
                lay.addRow("검증 상태", QLabel(self._last_result.verification))
            return
        kind, _, rest = path.partition("[")
        if not rest:
            self.props_title.setText(path)
            return
        idx = int(rest.rstrip("]"))
        if kind == "materials":
            mat = m.materials[idx]
            si_units = m.unit_system == "si"
            self.props_title.setText(f"재료 — {mat.name}")
            lay.addRow("이름", QLabel(mat.name))
            lay.addRow("법칙", QLabel(mat.law))
            modulus = mat.params.get('youngs_modulus', 0.0)
            lay.addRow("탄성계수", QLabel(f"{modulus / 1e9:.2f} GPa" if si_units
                                       else f"{modulus} (단위 미확인)"))
            lay.addRow("포아송비",
                       QLabel(f"{mat.params.get('poissons_ratio', 0.0):.3f}"))
            if "density" in mat.params:
                density = mat.params['density']
                lay.addRow("밀도", QLabel(f"{density:,.0f} kg/m³" if si_units
                                        else f"{density} (단위 미확인)"))
            return
        if kind == "loads":
            ld = m.loads[idx]
            self.props_title.setText(f"하중 — {ld.name}")
            lay.addRow("이름", QLabel(ld.name))
            lay.addRow("종류", QLabel(ld.kind))
            lay.addRow("대상 집합", QLabel(ld.target_set or "-"))
            for comp in ("fx", "fy", "fz"):
                value = ld.values.get(comp, 0.0)
                text = fmt_force(value, "N") if m.unit_system == "si" else f"{value} (단위 미확인)"
                lay.addRow(f"힘 {comp.upper()}", QLabel(text))
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

        if self._active_run_id is not None:
            return
        folder = QFileDialog.getExistingDirectory(self, "프로젝트 열기")
        if folder:
            try:
                self.set_model(self.workbench.open_project(folder), Path(folder))
            except Exception as error:
                self.status.setText(f'프로젝트를 열지 못했습니다: {error}')

    def save_project(self) -> bool:
        from PySide6.QtWidgets import QFileDialog
        if self.model is None:
            self.status.setText("저장할 모델이 없습니다.")
            return False
        if self._active_run_id is not None:
            self.status.setText('계산 정리를 마친 뒤 모델을 저장하세요.')
            return False
        folder = self.project_folder
        expected_revision = self._saved_model_revision
        if folder is None:
            chosen = QFileDialog.getExistingDirectory(self, '프로젝트 저장 폴더 선택')
            if not chosen:
                self.status.setText('저장을 취소했습니다.')
                return False
            folder = Path(chosen).resolve()
            try:
                expected_revision = project_revision(folder)
            except Exception as error:
                self.status.setText(f'저장 위치의 모델을 확인하지 못했습니다: {error}')
                return False
            if expected_revision:
                answer = QMessageBox.question(self, '기존 모델 저장',
                    '이 폴더의 model.json을 현재 모델로 바꾸시겠습니까? 실행 이력은 보존합니다.',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return False
        try:
            self.workbench.save_project(self.model, folder, expected_revision=expected_revision)
        except Exception as error:
            self.status.setText(f'프로젝트를 저장하지 못했습니다: {error}')
            self._log(self.status.text())
            return False
        self.project_folder = folder
        self._saved_model_revision = model_revision(self.model)
        self.status.setText(f"저장했습니다: {folder}")
        self._log(f"프로젝트를 저장했습니다: {folder}")
        return True

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

    # -- independent MDO Studio ---------------------------------------------
    def open_optimization_panel(self) -> None:
        try:
            record = self.workbench.launch_mdo_studio()
            if record.state == "failed":
                raise ValueError(record.error)
        except Exception as error:
            self.statusBar().showMessage(f"MDO Studio를 열지 못했습니다: {error}")
            return
        self.statusBar().showMessage("독립 MDO Studio를 열었습니다.")

    def run_solve(self) -> None:
        if self.model is None:
            self.status.setText("먼저 모델을 여세요.")
            return
        if self._active_run_id is not None:
            self.status.setText('이미 계산 중입니다. 취소 또는 정리 완료 후 다시 실행하세요.')
            return
        if not self.save_project():
            return
        try:
            run = self.workbench.submit_model(self.model, self.project_folder, 'calculix')
        except Exception as error:
            self.status.setText(f'계산을 등록하지 못했습니다: {error}')
            self._log(self.status.text())
            return
        self._active_run_id = run.id
        self._active_folder = self.project_folder
        self._active_generation = self._model_generation
        self._active_selection_generation = self._selection_generation
        self._last_result = None
        self._show_current_model(manual=False)
        self._set_flow(2)
        self._set_running_controls(True)
        self._refresh_run_history()
        self.status.setText('현재 프로젝트 모델의 계산을 등록했습니다.')
        self._run_timer.start()

    def _set_running_controls(self, running: bool) -> None:
        for action in self._locked_actions + self._context_locked_actions:
            action.setEnabled(not running)
        for action in self._cancel_actions:
            action.setEnabled(running)

    def cancel_run(self) -> None:
        if self._active_run_id is not None:
            self.workbench.cancel_native_run(self._active_run_id)
            self.status.setText('계산 취소와 소유 작업 정리를 요청했습니다.')

    def _show_current_model(self, *, manual: bool) -> None:
        if self.model is None:
            return
        if manual:
            self._selection_generation += 1
        self._displayed_run_id = None
        self._last_result = None
        self._displayed_run_label = self._displayed_model_name = ''
        self._displayed_state_text = ''
        self.viewport.show_model(self.model)
        self.legend_label.clear()
        self.statusBar().clearMessage()
        self.unit_system_status.setText(f'솔버: CalculiX · 단위계: {self._unit_system_text()}')
        self.status.setText(f'현재 프로젝트 모델: {self.model.name}')
        self._show_props('model')
        blocked = self.tree.blockSignals(True)
        self.tree.setCurrentItem(self._path_items['model'])
        self.tree.blockSignals(blocked)

    @staticmethod
    def _run_state_text(run) -> str:
        text = {'queued': '대기', 'preparing': '준비 중', 'running': '실행 중',
                'completed': '프로세스 정상 종료', 'failed': '실패',
                'cancelled': '취소', 'interrupted': '중단'}.get(run.state, run.state)
        if run.state == 'completed':
            text += ' · ' + {'readable': '결과 판독', 'failed': '판독 실패',
                            'reading': '판독 중'}.get(run.parse_state, run.parse_state)
        return text

    def _refresh_run_history(self) -> None:
        if self.project_folder is None:
            return
        current = self.tree.currentItem()
        current_path = current.data(0, Qt.UserRole) if current is not None else None
        blocked = self.tree.blockSignals(True)
        try:
            self.results_item.takeChildren()
            for key in list(self._path_items):
                if key.startswith('runs.') or key == 'result':
                    self._path_items.pop(key)
            self._run_labels = {}
            records = self.workbench.model_runs(self.project_folder, self.model)
            selected = self.workbench.selected_model_run(self.project_folder)
            for index, run in enumerate(records, 1):
                label = f'실행 {index}'
                self._run_labels[run.id] = label
                timestamp = run.created_at.replace('T', ' ')[:19]
                text = f'{label} · {timestamp} UTC · {self._run_state_text(run)}'
                if run.stale:
                    text += ' · 현재 모델과 다름'
                item = self._add(self.results_item, text, f'runs.{run.id}')
                if selected is not None and selected.id == run.id:
                    self._path_items['result'] = item
            self.results_item.setExpanded(True)
            if current_path in self._path_items:
                self.tree.setCurrentItem(self._path_items[current_path])
        except Exception as error:
            self._add(self.results_item, '실행 이력을 읽을 수 없음', 'history_error')
            self.status.setText(f'실행 이력 확인 실패: {error}')
            self._log(self.status.text())
        finally:
            self.tree.blockSignals(blocked)

    def _choose_run(self, run_id: str) -> None:
        self._selection_generation += 1
        try:
            run = self.workbench.select_model_run(self.project_folder, run_id)
            self._path_items['result'] = self._path_items[f'runs.{run.id}']
            activity = (self.workbench.native_run_activity(run.id)
                        if run.id == self._active_run_id else None)
            own_unsettled = (run.id == self._active_run_id
                             and (activity is None or activity.active or activity.cleanup_error))
            if not run.finished_at or own_unsettled:
                self.status.setText('선택한 계산이 진행 중입니다. 과거 실행을 선택해 확인할 수 있습니다.')
                return
            self._load_run_result(run)
        except Exception as error:
            self.status.setText(f'보존 실행을 선택하지 못했습니다: {error}')

    def _load_run_result(self, run) -> None:
        try:
            payload = self.workbench.model_run_result(self.project_folder, run.id)
        except Exception as error:
            self.status.setText(f'선택 실행을 표시하지 못했습니다: {error}. 이전에 확인한 화면을 유지합니다.')
            return
        model, result = payload.model, payload.result
        item = self._path_items.get(f'runs.{run.id}')
        if item is not None:
            blocked = self.tree.blockSignals(True)
            self.tree.setCurrentItem(item)
            self.tree.blockSignals(blocked)
        self._displayed_run_id = run.id
        self._displayed_run_label = self._run_labels.get(run.id, '보존 실행')
        self._displayed_model_name = model.name
        self._displayed_state_text = self._run_state_text(run)
        self._last_result = result
        self.viewport.show_model(model)
        self.legend_label.clear()
        self.unit_system_status.setText(f'{self._displayed_run_label} · 단위계: {self._unit_system_text(model)}')
        if (result is not None and result.success and run.state == 'completed'
                and run.parse_state == 'readable' and run.returncode == 0):
            summary = (f'계산 완료 — 최대 변위 {fmt_length(result.max_displacement, result.units.get("length"))}, '
                       f'반력 합 Fy {fmt_force(result.reaction_totals.get("fy"), result.units.get("force"))} '
                       f'({result.verification})')
            self._displayed_model = model
            field = self.field_combo.currentData() or "U_mag"
            scale_mode = self.scale_mode_combo.currentData() or "auto"
            factor = self.scale_spin.value()
            try:
                self.viewport.show_results(model, result, field_name=field, scale_mode=scale_mode, scale_factor=factor)
            except TypeError:
                self.viewport.show_results(model, result)
            self._update_legend_label(model, result)
            self._set_flow(3)
        else:
            self._set_flow(2)
            summary = f'선택 실행: {self._run_state_text(run)}. 보존한 실행 모델을 표시합니다.'
            self._log(run.error or (result.log_tail[-1500:] if result is not None else ''))
        self.status.setText(summary)
        self._log(summary)
        self.statusBar().showMessage(f'{self._displayed_run_label} · 실행 모델: {model.name}')
        self._show_props('result')

    def _poll_run(self) -> None:
        run_id = self._active_run_id
        if run_id is None:
            self._run_timer.stop()
            return
        run = self.workbench.native_run_snapshot(run_id)
        activity = self.workbench.native_run_activity(run_id)
        if run is None or activity is None:
            self._run_timer.stop()
            self._close_pending = False
            self.status.setText('소유 작업의 상태를 확인하지 못했습니다. 자동 종료를 중단했습니다.')
            return
        if activity.active:
            self.status.setText('실행 기록을 보존했습니다. 소유 작업 정리 중입니다.' if run.finished_at
                                else f'현재 계산: {self._run_state_text(run)}')
            return
        self._run_timer.stop()
        if activity.cleanup_error:
            self._close_pending = False
            self.status.setText(f'소유 작업 정리를 확인하지 못했습니다: {activity.cleanup_error}')
            self._log(self.status.text())
            return
        self._active_run_id = None
        self._set_running_controls(False)
        if self._close_pending:
            self._close_pending = False
            self.close()
            return
        self._refresh_run_history()
        try:
            selected = self.workbench.selected_model_run(self.project_folder)
            may_display = (self.project_folder == self._active_folder
                           and self._model_generation == self._active_generation
                           and self._selection_generation == self._active_selection_generation
                           and selected is not None and selected.id == run_id
                           and self.workbench.model_run_is_current(self.project_folder, run_id, self.model))
            if may_display:
                self._load_run_result(run)
            else:
                self.status.setText(f'계산 종료: {self._run_state_text(run)}. 선택한 화면을 유지합니다.')
                if run.error:
                    self._log(run.error)
        except Exception as error:
            self.status.setText(f'완료 실행 확인 실패: {error}')

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._active_run_id is not None:
            activity = self.workbench.native_run_activity(self._active_run_id)
            if activity is None or activity.cleanup_error:
                reason = activity.cleanup_error if activity is not None else '소유 상태를 확인할 수 없습니다.'
                self._close_pending = False
                self.status.setText(f'정상 종료를 확인하지 못했습니다: {reason}')
                event.ignore()
                return
            if activity.active:
                self._close_pending = True
                self.cancel_run()
                self._run_timer.start()
                event.ignore()
                return
            self._active_run_id = None
        self._run_timer.stop()
        event.accept()

    def _rescale(self) -> None:
        self._on_display_setting_changed()

    def _on_display_setting_changed(self) -> None:
        if not hasattr(self, "field_combo") or not hasattr(self, "scale_mode_combo"):
            return
        field = self.field_combo.currentData() or "U_mag"
        mode = self.scale_mode_combo.currentData() or "auto"
        factor = self.scale_spin.value()
        self.viewport.update_display(field_name=field, scale_mode=mode, scale_factor=factor)
        model = getattr(self, "_displayed_model", self.model)
        result = getattr(self, "_last_result", None)
        if model is not None and result is not None:
            self._update_legend_label(model, result)

    def _update_legend_label(self, model: StructuralModel, result: ResultSet) -> None:
        field = self.field_combo.currentData() or "U_mag"
        if not model.mesh.nodes or not model.mesh.nodes.keys() <= result.displacements.keys():
            self.legend_label.setText("변위 필드 누락 — 변형 미표시")
            return
        unit = result.units.get("length")
        length_unit = "mm" if unit == "m" else (unit or "mm")

        if field == "von_mises":
            from apps.workbench_qt.viewport import deduce_stress_unit
            s_unit = deduce_stress_unit(result.units)
            if s_unit is None:
                self.legend_label.setText("색상 범례: Von Mises 응력 — [단위 미선언: 파생 응력 보류]")
            else:
                self.legend_label.setText(f"색상 범례: Von Mises 응력 ({s_unit}) [절점 외삽]")
        elif field == "U_x":
            self.legend_label.setText(f"색상 범례: 변위 X ({length_unit}) [절점]")
        elif field == "U_y":
            self.legend_label.setText(f"색상 범례: 변위 Y ({length_unit}) [절점]")
        elif field == "U_z":
            self.legend_label.setText(f"색상 범례: 변위 Z ({length_unit}) [절점]")
        else:
            self.legend_label.setText(f"색상 범례: 변위 크기 ({length_unit}) [절점]")

    def show_diagnostics(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("진단 정보")
        text = QTextEdit()
        text.setReadOnly(True)
        info = [
            f"모델: {self.model.name if self.model else '-'}",
            f"프로젝트 폴더: {self.project_folder or '-'}",
            f"진행 중 실행: {self._active_run_id or '-'}",
            f"표시한 실행: {self._displayed_run_id or '-'}",
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

    def apply_theme(self, mode: str | None = None) -> None:
        resolved = mode if mode in ("dark", "light") else current_theme_mode()
        self.setStyleSheet(build_stylesheet(resolved))
        if hasattr(self, "theme_action"):
            self.theme_action.setText(theme_toggle_label(resolved))
        for act, name in getattr(self, "_toolbar_icon_actions", []):
            act.setIcon(icon(name, resolved))
        if "model" in self._path_items:
            self._path_items["model"].setIcon(0, icon(LEAF_ICONS["model"], resolved))
        for gname, item in self._group_items.items():
            item.setIcon(0, icon(GROUP_ICONS.get(gname, "folder"), resolved))
        for path, item in self._path_items.items():
            if path == "model":
                continue
            kind = path.partition("[")[0]
            item.setIcon(0, icon(LEAF_ICONS.get(kind, "folder"), resolved))
        if hasattr(self, "viewport") and self.viewport is not None:
            self.viewport.apply_theme(resolved)
        for chip in getattr(self, "flow_labels", []):
            chip.style().unpolish(chip)
            chip.style().polish(chip)

