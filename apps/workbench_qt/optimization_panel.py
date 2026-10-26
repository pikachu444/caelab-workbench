"""Five-step optimization dashboard with separate actual and predicted results."""

from __future__ import annotations

import numpy as np

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib import font_manager
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from caelab.numerical import (ModelEvaluator, analyze_candidates, fit_surrogate, get_path,
                              normalize_variables, optimize, run_doe, scalar_value)

from .theme import build_stylesheet, current_theme_mode, get_palette, register_theme_listener

_SOURCES = {"doe": "DOE (Actual)", "test": "Test (Actual)", "opt_surrogate": "Optimization (Pred)",
            "opt_actual": "Optimization (Actual)", "verify": "Verify (Actual)"}


class OptimizationPanel(QWidget):
    def __init__(self, evaluator=None, variables=None, objective=None) -> None:
        super().__init__()
        self.evaluator = evaluator
        self.variables: list[dict] = []
        self.objective: dict = {"response": "", "unit": "1", "direction": "minimize"}
        self.doe_table: dict | None = None
        self.test_table: dict | None = None
        self.surrogate = None
        self.optimization_result: dict | None = None
        self.verification: dict | None = None
        self._history: list[dict] = []  # {source, values, objective, status}
        self.best_history_index: int | None = None
        self._opt_best_key: tuple | None = None  # (source, candidate_id) of step-4 best
        # Model mode: the common model + run function behind ModelEvaluator,
        # and the paths the user picked in the step-1 table.
        self._model = None
        self._run_fn = None
        self._variable_paths: dict[str, str] = {}
        self._response_path: str = ""
        self._mode: str | None = None

        self._busy = False
        self._cancel_requested = False
        available_fonts = {font.name for font in font_manager.fontManager.ttflist}
        self._chart_fonts = [name for name in ("Malgun Gothic", "NanumGothic", "Noto Sans CJK KR")
                             if name in available_fonts] + ["DejaVu Sans"]
        self.setWindowTitle("최적화 및 매개변수 연구")
        self.resize(1280, 820)
        root = QVBoxLayout(self)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        root.addWidget(self.splitter)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("optimizationScroll")
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(360)
        self.controller = QWidget()
        self.controller.setObjectName("optimizationController")
        self.controller.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self.controller)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)
        scroll.setWidget(self.controller)
        left_layout.addWidget(scroll)
        self.cancel_button = QPushButton("취소")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setToolTip("현재 평가가 끝나면 중단하고 완료된 결과를 보존합니다.")
        self.cancel_button.clicked.connect(self.request_cancel)
        left_layout.addWidget(self.cancel_button)
        self.splitter.addWidget(left)

        setup_box = QGroupBox("1 · 설정")
        setup_layout = QVBoxLayout(setup_box)
        self.variable_table = QTableWidget(0, 6)
        self.variable_table.setHorizontalHeaderLabels(
            ["변수", "단위", "모델 경로", "하한", "상한", "시작값"])
        self.variable_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.variable_table.horizontalHeader().setDefaultSectionSize(78)
        self.variable_table.setAlternatingRowColors(True)
        self.variable_table.setMinimumHeight(105)
        self.variable_table.setMaximumHeight(150)
        setup_layout.addWidget(self.variable_table)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.response_edit = QLineEdit()
        self.response_path_edit = QLineEdit()
        self.response_path_edit.setPlaceholderText("예: max_displacement")
        self.unit_edit = QLineEdit("1")
        self.direction_combo = QComboBox()
        self.direction_combo.addItem("최소화", "minimize")
        self.direction_combo.addItem("최대화", "maximize")
        form.addRow("목적 응답", self.response_edit)
        form.addRow("응답 모델 경로", self.response_path_edit)
        form.addRow("단위", self.unit_edit)
        form.addRow("방향", self.direction_combo)
        setup_layout.addLayout(form)
        self.setup_status = QLabel("평가 함수가 연결되지 않았습니다.")
        self.setup_status.setWordWrap(True)
        setup_layout.addWidget(self.setup_status)
        apply_btn = QPushButton("설정 적용")
        apply_btn.clicked.connect(self.apply_setup)
        setup_layout.addWidget(apply_btn)
        layout.addWidget(setup_box)

        doe_box = QGroupBox("2 · DOE — 실제 평가")
        doe_layout = QVBoxLayout(doe_box)
        form = QFormLayout()
        self.doe_count = QSpinBox(minimum=4, maximum=512, value=16)
        self.doe_seed = QSpinBox(minimum=0, maximum=2**31 - 1, value=0)
        self.test_count = QSpinBox(minimum=2, maximum=512, value=8)
        form.addRow("학습 개수", self.doe_count)
        form.addRow("시드", self.doe_seed)
        form.addRow("테스트 개수", self.test_count)
        doe_layout.addLayout(form)
        doe_btn = QPushButton("DOE 실행")
        doe_btn.clicked.connect(self.run_doe_step)
        doe_layout.addWidget(doe_btn)
        self.doe_status = QLabel("")
        self.doe_status.setWordWrap(True)
        doe_layout.addWidget(self.doe_status)
        layout.addWidget(doe_box)

        sur_box = QGroupBox("3 · 대리모델")
        sur_layout = QVBoxLayout(sur_box)
        self.model_combo = QComboBox()
        self.model_combo.addItem("RBF", "rbf")
        self.model_combo.addItem("Polynomial (2차)", "polynomial")
        sur_layout.addWidget(self.model_combo)
        sur_btn = QPushButton("근사모델 만들기")
        sur_btn.clicked.connect(self.run_surrogate_step)
        sur_layout.addWidget(sur_btn)
        self.surrogate_status = QLabel("아직 근사모델이 없습니다.")
        self.surrogate_status.setWordWrap(True)
        sur_layout.addWidget(self.surrogate_status)
        layout.addWidget(sur_box)

        opt_box = QGroupBox("4 · 최적화")
        opt_layout = QVBoxLayout(opt_box)
        form = QFormLayout()
        self.algorithm_combo = QComboBox()
        self.algorithm_combo.addItem("차분진화 (Differential Evolution)", "differential_evolution")
        self.opt_generations = QSpinBox(minimum=1, maximum=200, value=15)
        self.opt_population = QSpinBox(minimum=5, maximum=100, value=10)
        form.addRow("알고리즘", self.algorithm_combo)
        form.addRow("세대", self.opt_generations)
        form.addRow("개체수", self.opt_population)
        opt_layout.addLayout(form)
        opt_btn = QPushButton("최적화 실행")
        opt_btn.clicked.connect(self.run_optimization_step)
        opt_layout.addWidget(opt_btn)
        self.opt_status = QLabel("")
        self.opt_status.setWordWrap(True)
        opt_layout.addWidget(self.opt_status)
        layout.addWidget(opt_box)

        ver_box = QGroupBox("5 · 재실행 검증")
        ver_layout = QVBoxLayout(ver_box)
        ver_btn = QPushButton("최적 행 재실행")
        ver_btn.clicked.connect(self.run_verification_step)
        ver_layout.addWidget(ver_btn)
        self.verify_status = QLabel("아직 검증하지 않았습니다.")
        self.verify_status.setWordWrap(True)
        ver_layout.addWidget(self.verify_status)
        layout.addWidget(ver_box)
        layout.addWidget(QLabel("평가 이력"))
        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(["후보", "출처", "변수값", "목적값", "상태", "실패 이유"])
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.history_table.horizontalHeader().setDefaultSectionSize(110)
        self.history_table.setColumnWidth(0, 48)
        self.history_table.setColumnWidth(1, 150)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setMinimumHeight(200)
        layout.addWidget(self.history_table)

        self.visualization_tabs = QTabWidget()
        self.visualization_tabs.setMinimumWidth(300)
        self.canvases = []
        self.axes = []
        for title in ("수렴 이력", "민감도 분석", "대리모델 검증"):
            canvas = FigureCanvasQTAgg(Figure(figsize=(6, 5), layout="constrained"))
            self.canvases.append(canvas)
            self.axes.append(canvas.figure.add_subplot(111))
            self.visualization_tabs.addTab(canvas, title)
        self.splitter.addWidget(self.visualization_tabs)
        self.visualization_tabs.currentChanged.connect(
            lambda index: self.canvases[index].draw())
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([460, 800])

        self.setAttribute(Qt.WA_StyledBackground, True)
        if evaluator is not None and variables:
            self.set_study(evaluator, variables, objective)
        self.apply_theme()
        register_theme_listener(self.apply_theme)

    # -- study setup -------------------------------------------------------
    def set_study(self, evaluator, variables, objective=None, *, _keep_model=False) -> None:
        """Connect an evaluator and show its variables/objective in step 1."""
        if self._busy:
            return
        self._reset_from(2)
        self.evaluator = evaluator
        self.variables = normalize_variables(variables)
        if not _keep_model:
            self._model = self._run_fn = None
            self._variable_paths = {}
            self._response_path = ""
        if objective:
            self.objective = {"response": objective.get("response", ""),
                              "unit": objective.get("unit", "1"),
                              "direction": objective.get("direction", "minimize")}
        self._fill_setup_widgets()
        self.setup_status.setText(
            f"변수 {len(self.variables)}개가 연결됐습니다. 설정을 확인하고 적용하세요.")
        self._redraw_plots()

    def configure_with_model(self, model, variable_specs, run_fn, response_specs) -> None:
        """Connect through the common model: variable paths -> variables,
        result paths -> the (single) objective response."""
        if self._busy:
            return
        evaluator = ModelEvaluator(model, variable_specs, run_fn, response_specs)
        self._model, self._run_fn = model, run_fn
        self._variable_paths = {
            spec.get("id", spec.get("parameter_id")): spec["path"]
            for spec in variable_specs}
        first = response_specs[0]
        self._response_path = first.get("path", "")
        self.set_study(evaluator, evaluator.variables,
                       {"response": first["response"], "unit": first.get("unit", "1"),
                        "direction": first.get("direction", "minimize")},
                       _keep_model=True)

    def current_variable_specs(self) -> list[dict]:
        """The applied variable declarations, with model paths if in
        model mode — exactly what a host needs to rebuild this study."""
        return [{**var, "path": self._variable_paths[var["id"]]}
                if var["id"] in self._variable_paths else dict(var)
                for var in self.variables]

    def _fill_setup_widgets(self) -> None:
        self.variable_table.setRowCount(len(self.variables))
        for row, var in enumerate(self.variables):
            cells = [var["id"], var["unit"], self._variable_paths.get(var["id"], ""),
                     var["lower"], var["upper"], var["value"]]
            for col, value in enumerate(cells):
                self.variable_table.setItem(row, col, QTableWidgetItem(str(value)))
        self.response_edit.setText(self.objective.get("response", ""))
        self.response_path_edit.setText(self._response_path)
        self.unit_edit.setText(self.objective.get("unit", "1"))
        index = self.direction_combo.findData(self.objective.get("direction", "minimize"))
        if index >= 0:
            self.direction_combo.setCurrentIndex(index)

    def apply_setup(self) -> bool:
        """Parse the setup table into engine declarations (step 1 action).

        When every variable row carries a model path, the evaluator is
        rebuilt through ModelEvaluator from the table's paths and the
        response model path — the paths shown are the paths that run.
        Mixing path and no-path variables is refused in one line.
        """
        if self._busy:
            return False
        declarations, paths = [], []
        try:
            for row in range(self.variable_table.rowCount()):
                cells = [(self.variable_table.item(row, col).text().strip()
                          if self.variable_table.item(row, col) else "")
                         for col in range(6)]
                declarations.append({"id": cells[0], "unit": cells[1],
                                     "lower": float(cells[3]), "upper": float(cells[4]),
                                     "value": float(cells[5])})
                paths.append(cells[2])
            variables = normalize_variables(declarations)
            response = self.response_edit.text().strip()
            if not response:
                raise ValueError("목적 응답 이름을 입력하세요")
            objective = {"response": response,
                         "unit": self.unit_edit.text().strip() or "1",
                         "direction": self.direction_combo.currentData()}
            response_path = self.response_path_edit.text().strip()
            if any(paths) and not all(paths):
                raise ValueError("모델 경로는 모든 변수에 입력하거나 모두 비우세요")
            if all(paths) and paths:
                if self._model is None or self._run_fn is None:
                    raise ValueError("모델이 연결되지 않았습니다. 모델 연결 후 경로를 쓸 수 있습니다")
                if not response_path:
                    raise ValueError("응답 모델 경로를 입력하세요 (예: max_displacement)")
                for declaration, path in zip(declarations, paths):
                    current = get_path(self._model, path)  # path must exist now
                    if isinstance(current, bool) or not isinstance(current, (int, float)):
                        raise ValueError(f"모델 경로 {path}에 숫자가 없습니다")
                specs = [{**decl, "path": path}
                         for decl, path in zip(declarations, paths)]
                evaluator = ModelEvaluator(
                    self._model, specs, self._run_fn,
                    [{"response": response, "unit": objective["unit"],
                      "path": response_path, "direction": objective["direction"]}])
                variables = evaluator.variables
                self.evaluator = evaluator
                self._variable_paths = {decl["id"]: path
                                        for decl, path in zip(declarations, paths)}
                self._response_path = response_path
        except (ValueError, TypeError, KeyError) as error:
            self.setup_status.setText(f"설정 오류: {error}")
            return False
        self.variables, self.objective = variables, objective
        self._reset_from(2)
        mode = "모델 경로 연결" if self._variable_paths else "직접 평가 함수"
        self.setup_status.setText(f"설정 적용됨: 변수 {len(variables)}개 ({mode}), "
                                  f"목적 {response} ({objective['direction']}).")
        return True

    def _reset_from(self, step):
        if step <= 2:
            self.doe_table = self.test_table = None
            self._history = []
            self.doe_status.clear()
        if step <= 3:
            self.surrogate = None
            self.surrogate_status.setText("아직 근사모델이 없습니다.")
        if step <= 4:
            self.optimization_result = None
            self._opt_best_key = None
            self._history = [r for r in self._history if r["source"] in ("doe", "test")]
            self.opt_status.clear()
        self.verification = None
        self._history = [r for r in self._history if r["source"] != "verify"]
        self.verify_status.setText("아직 검증하지 않았습니다.")
        self._refresh_history()

    def _append_history(self, source, rows):
        for row in rows:
            objective = None
            status = row.get("execution_status", "FAILED")
            reason = row.get("failure_reason") or row.get("diagnostics", {}).get("failure_reason", "")
            try:
                objective = scalar_value(row, self.objective)
            except (ValueError, RuntimeError, KeyError, TypeError) as error:
                reason = reason or str(error)
                if status == "SUCCEEDED":
                    status = "FAILED"
            if status == "SUCCEEDED" and row.get("feasible") is False:
                status = "INFEASIBLE"
                reason = reason or "Constraint limits exceeded"
            self._history.append({"source": source, "candidate_id": row["candidate_id"],
                                  "values": dict(row["values"]), "objective": objective,
                                  "status": status, "failure_reason": reason})
        self._refresh_history()

    def _best_index(self):
        if self._opt_best_key is not None:
            for i, entry in enumerate(self._history):
                if (entry["source"], entry["candidate_id"]) == self._opt_best_key:
                    return i
        eligible = [i for i, row in enumerate(self._history)
                    if row["objective"] is not None and row["status"] == "SUCCEEDED"
                    and row["source"] not in ("verify", "opt_surrogate")]
        sign = -1 if self.objective.get("direction") == "maximize" else 1
        return min(eligible, key=lambda i: sign * self._history[i]["objective"]) if eligible else None

    def _refresh_history(self):
        p = get_palette(self._mode)
        self.history_table.setUpdatesEnabled(False)
        self.history_table.setRowCount(len(self._history))
        self.best_history_index = self._best_index()
        for i, entry in enumerate(self._history):
            values = ", ".join(f"{k}={v:.6g}" for k, v in entry["values"].items())
            objective = "—" if entry["objective"] is None else f"{entry['objective']:.6g}"
            cells = [str(i + 1), _SOURCES[entry["source"]], values, objective,
                     entry["status"], entry["failure_reason"]]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setToolTip(text)
                if i == self.best_history_index:
                    item.setBackground(QBrush(QColor(str(p["best_row_bg"]))))
                    item.setForeground(QBrush(QColor(str(p["best_row_fg"]))))
                self.history_table.setItem(i, col, item)
        self.history_table.setUpdatesEnabled(True)
        self._redraw_plots()

    def apply_theme(self, mode=None):
        resolved = mode if mode in ("dark", "light") else current_theme_mode()
        if resolved == self._mode:
            return
        self._mode = resolved
        p = get_palette(self._mode)
        self.setStyleSheet(build_stylesheet(self._mode)
                          + f"\nOptimizationPanel, QWidget#optimizationController, "
                          f"QScrollArea#optimizationScroll {{ background: {p['window_bg']}; border: none; }}")
        self._refresh_history()

    def _redraw_plots(self):
        p = get_palette(self._mode)
        accent, predicted_color = p["accent"], p["status_warning_fg"]
        danger = p["status_error_fg"]
        for ax in self.axes:
            ax.clear()
        ax, sensitivity, parity = self.axes
        unit = self.objective.get("unit", "1")
        response = self.objective.get("response") or "Objective"
        ax.set(xlabel="Evaluation", ylabel=f"{response} [{unit}]", title="Convergence history")
        for predicted, color, marker, label in (
                (False, accent, "o", "Actual"), (True, predicted_color, "D", "Prediction")):
            points = [(i + 1, r["objective"]) for i, r in enumerate(self._history)
                      if (r["source"] == "opt_surrogate") == predicted
                      and r["status"] == "SUCCEEDED" and r["objective"] is not None]
            if points:
                x, y = np.asarray(points).T
                ax.plot(x, y, color=color, alpha=0.4, linewidth=0.8)
                ax.scatter(x, y, marker=marker, color=color, s=18, label=label)
                best = (np.maximum.accumulate(y) if self.objective.get("direction") == "maximize"
                        else np.minimum.accumulate(y))
                ax.step(x, best, where="post", color=color,
                        linestyle="--" if predicted else "-", label=f"Best {label.lower()}")
        for status, marker in (("FAILED", "x"), ("INFEASIBLE", "P")):
            points = [(i + 1, r["objective"]) for i, r in enumerate(self._history)
                      if r["status"] == status]
            numeric = [(i, y) for i, y in points if y is not None]
            missing = [i for i, y in points if y is None]
            if numeric:
                x, y = zip(*numeric)
                ax.scatter(x, y, color=danger, marker=marker, label=status)
            if missing:
                # Axes-relative positions are a status strip, never objective data.
                ax.scatter(missing, [0.03] * len(missing), transform=ax.get_xaxis_transform(),
                           color=danger, marker=marker, label=f"{status} (no value)")
        ax.margins(y=0.15)

        sensitivity.set(title="DOE sensitivity", xlabel="Standardized regression coefficient")
        if self.doe_table and self.doe_table["candidates"]:
            result = analyze_candidates(self._usable_table(self.doe_table), [self.objective])["sensitivity"][0]
            if result["valid"]:
                coefficients = sorted(result["coefficients"], key=lambda c: abs(c["coefficient"]), reverse=True)
                sensitivity.barh([c["parameter_id"] for c in coefficients],
                                 [c["coefficient"] for c in coefficients],
                                 color=[accent if c["coefficient"] >= 0 else predicted_color for c in coefficients])
                sensitivity.invert_yaxis()
                sensitivity.axvline(0, color=p["text_muted"], linewidth=0.8)
                sensitivity.set_title("DOE sensitivity — linear sample association")
            else:
                sensitivity.text(0.5, 0.5, f"Sensitivity unavailable\n{result['reason']}",
                                 ha="center", transform=sensitivity.transAxes)
        else:
            sensitivity.text(0.5, 0.5, "Run DOE to estimate sensitivity", ha="center",
                             transform=sensitivity.transAxes)

        parity.set(title="Surrogate validation", xlabel=f"Actual [{unit}]", ylabel=f"Predicted [{unit}]")
        values = []
        if self.surrogate is not None:
            v = self.surrogate.validation
            observed, predicted = v["holdout_observed"], v["holdout_prediction"]
            parity.scatter(observed, predicted, color=accent, marker="o", label="Holdout (actual vs predicted)")
            values.extend(observed + predicted)
            parity.text(0.03, 0.97, f"Holdout RMSE: {v['rmse']:.4g} {unit}\nMax error: {v['max_error']:.4g} {unit}",
                        va="top", transform=parity.transAxes,
                        bbox={"facecolor": p["card_bg"], "edgecolor": p["border"], "pad": 6})
        if self.verification and self.verification["predicted"] is not None:
            actual, predicted = self.verification["actual"], self.verification["predicted"]
            parity.scatter([actual], [predicted], color=predicted_color, marker="*", s=140,
                           label="Verified optimum")
            values.extend([actual, predicted])
        if values:
            low, high = min(values), max(values)
            margin = (high - low) * 0.1 or max(abs(low) * 0.1, 0.1)
            x = np.linspace(low - margin, high + margin, 101)
            if x[0] < 0 < x[-1]:
                x = np.sort(np.append(x, 0.0))
            parity.plot(x, x, color=p["text_secondary"], label="y = x")
            for fraction, style in ((0.05, "--"), (0.10, ":")):
                parity.plot(x, x + fraction * abs(x), color=p["text_muted"],
                            linestyle=style, label=f"±{fraction:.0%}")
                parity.plot(x, x - fraction * abs(x), color=p["text_muted"], linestyle=style)
            parity.set_aspect("equal", adjustable="box")
            limits = min(parity.get_xlim()[0], parity.get_ylim()[0]), max(parity.get_xlim()[1], parity.get_ylim()[1])
            parity.set_xlim(limits)
            parity.set_ylim(limits)
        else:
            parity.text(0.5, 0.5, "Fit a model using separate holdout evaluations", ha="center",
                        transform=parity.transAxes, wrap=True)
        for canvas, axes in zip(self.canvases, self.axes):
            canvas.figure.set_facecolor(p["canvas_bg"])
            axes.set_facecolor(p["card_bg"])
            axes.set_axisbelow(True)
            axes.grid(color=p["border"], alpha=0.65)
            axes.tick_params(colors=p["text_muted"])
            axes.xaxis.get_offset_text().set_color(p["text_muted"])
            axes.yaxis.get_offset_text().set_color(p["text_muted"])
            for spine in axes.spines.values():
                spine.set_color(p["border"])
            for text in [axes.title, axes.xaxis.label, axes.yaxis.label, *axes.texts]:
                text.set_color(p["text_primary"])
                text.set_fontfamily(self._chart_fonts)
            for text in [*axes.get_xticklabels(), *axes.get_yticklabels()]:
                text.set_fontfamily(self._chart_fonts)
            if axes.get_legend_handles_labels()[0]:
                legend = axes.legend(loc="best", fontsize=8, facecolor=p["card_bg"],
                                     edgecolor=p["border"], labelcolor=p["text_primary"])
                legend.get_frame().set_alpha(0.95)
            if canvas.isVisible():
                canvas.draw()

    def showEvent(self, event):
        super().showEvent(event)
        self.canvases[self.visualization_tabs.currentIndex()].draw()

    def request_cancel(self):
        if self._busy:
            self._cancel_requested = True
            self.cancel_button.setText("현재 평가 후 중단…")
            self.cancel_button.setEnabled(False)

    def _poll_cancel(self):
        QApplication.processEvents()
        return self._cancel_requested

    def _begin_run(self):
        self._busy = True
        self._cancel_requested = False
        self.controller.setEnabled(False)
        self.cancel_button.setEnabled(True)

    def _end_run(self):
        self._busy = False
        self.controller.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("취소")

    def closeEvent(self, event):
        if self._busy:
            self.request_cancel()
            event.ignore()
        else:
            super().closeEvent(event)

    def _require_study(self, status_label):
        if self._busy:
            return False
        if self.evaluator is None or not self.variables:
            status_label.setText("먼저 1단계에서 평가 함수와 변수를 연결·적용하세요.")
            return False
        return True

    def run_doe_step(self):
        if not self._require_study(self.doe_status):
            return None
        self._reset_from(2)
        self._begin_run()
        self.doe_status.setText("DOE 평가 중…")
        try:
            seed = self.doe_seed.value()
            execution = {"cancel_requested": self._poll_cancel}
            self.doe_table = run_doe(self.evaluator, self.variables,
                                     count=self.doe_count.value(), seed=seed, execution=execution)
            self._append_history("doe", self.doe_table["candidates"])
            if self.doe_table["execution_status"] != "CANCELLED":
                self.test_table = run_doe(self.evaluator, self.variables,
                                          count=self.test_count.value(), seed=seed + 1, execution=execution)
                self._append_history("test", self.test_table["candidates"])
            failed = sum(r["status"] != "SUCCEEDED" for r in self._history)
            cancelled = self.doe_table["execution_status"] == "CANCELLED" or (
                self.test_table is not None and self.test_table["execution_status"] == "CANCELLED")
            self.doe_status.setText(f"{'취소됨' if cancelled else '완료'} · 실제 평가 {len(self._history)}개 (실패 {failed}개)")
            return self.doe_table
        except Exception as error:
            self.doe_status.setText(f"DOE 실패: {error}")
            return None
        finally:
            self._end_run()

    def _usable_table(self, table):
        rows = []
        for row in table["candidates"]:
            try:
                scalar_value(row, self.objective)
            except (ValueError, RuntimeError, TypeError, KeyError):
                continue
            if row.get("feasible", True):
                rows.append(row)
        return {**table, "candidates": rows}

    def run_surrogate_step(self):
        if self._busy:
            return None
        self._reset_from(3)
        if self.doe_table is None or self.test_table is None:
            self.surrogate_status.setText("먼저 2단계 DOE를 실행하세요.")
            return None
        try:
            self.surrogate = fit_surrogate(
                self._usable_table(self.doe_table), self._usable_table(self.test_table),
                self.objective, variables=self.variables, method=self.model_combo.currentData())
        except Exception as error:
            self.surrogate_status.setText(f"근사모델 실패: {error}")
            return None
        v = self.surrogate.validation
        span = np.ptp(v["holdout_observed"])
        relative = f"{v['rmse'] / span:.2%}" if span else "정의되지 않음 (응답 범위 0)"
        self.surrogate_status.setText(
            f"테스트 DOE {len(v['holdout_ids'])}개 기준: RMSE {v['rmse']:.6g}, "
            f"최대 오차 {v['max_error']:.6g} {v['unit']}. 테스트 범위 대비 RMSE {relative}")
        self._redraw_plots()
        return self.surrogate

    def _surrogate_evaluator(self):
        surrogate = self.surrogate

        def evaluate(values):
            predicted = surrogate.predict([values])[0]
            return {"responses": predicted["responses"]}

        return evaluate

    def run_optimization_step(self):
        if not self._require_study(self.opt_status):
            return None
        self._reset_from(4)
        self._begin_run()
        self.opt_status.setText("최적화 평가 중…")
        try:
            on_surrogate = self.surrogate is not None
            evaluator = self._surrogate_evaluator() if on_surrogate else self.evaluator
            self.optimization_result = optimize(
                evaluator, self.variables, self.objective,
                options={"max_generations": self.opt_generations.value(),
                         "population_size": self.opt_population.value()},
                seed=self.doe_seed.value(), method=self.algorithm_combo.currentData(),
                execution={"cancel_requested": self._poll_cancel})
            source = "opt_surrogate" if on_surrogate else "opt_actual"
            best = self.optimization_result.get("best")
            if best is not None:
                self._opt_best_key = (source, best["candidate_id"])
            self._append_history(source, self.optimization_result["candidates"])
            prefix = "취소됨 · 완료된 후보 중 " if self.optimization_result["execution_status"] == "CANCELLED" else ""
            if best is not None:
                self.opt_status.setText(prefix + f"최적값 {best['objective']:.6g} "
                                        f"({'근사모델 예측' if on_surrogate else '실제 평가'}). 5단계에서 검증하세요.")
            else:
                self.opt_status.setText(prefix + "실행 가능한 최적 행을 찾지 못했습니다.")
            return self.optimization_result
        except Exception as error:
            self.opt_status.setText(f"최적화 실패: {error}")
            return None
        finally:
            self._end_run()

    def run_verification_step(self):
        if not self._require_study(self.verify_status):
            return None
        if self.optimization_result is None or not self.optimization_result.get("best"):
            self.verify_status.setText("먼저 4단계 최적화를 실행하세요.")
            return None
        self._reset_from(5)
        self._begin_run()
        self.verify_status.setText("최적 후보 재실행 중…")
        try:
            if self._poll_cancel():
                self.verify_status.setText("재실행 취소됨")
                return None
            best = self.optimization_result["best"]
            values = {v["id"]: best["values"][v["id"]] for v in self.variables}
            from caelab.numerical import evaluate_once
            evaluation, _elapsed = evaluate_once(self.evaluator, values)
            self._append_history("verify", [{**evaluation, "candidate_id": "V000000", "values": values}])
            actual = scalar_value(evaluation, self.objective)
            predicted = None
            if self.surrogate is not None:
                predicted = self.surrogate.predict([values])[0]["responses"][self.objective["response"]]["value"]
            doe_objectives = [e["objective"] for e in self._history
                              if e["source"] in ("doe", "test") and e["objective"] is not None]
            doe_range = np.ptp(doe_objectives) if doe_objectives else None
            error = abs(actual - predicted) if predicted is not None else None
            self.verification = {"values": values, "actual": actual, "predicted": predicted,
                                 "abs_error": error,
                                 "rel_error": error / abs(actual) if error is not None and actual != 0 else None,
                                 "range_fraction": error / doe_range if error is not None and doe_range else None}
            if predicted is None:
                self.verify_status.setText(f"재실행 실제값 {actual:.6g}. 근사모델이 없어 예측 비교는 없습니다.")
            else:
                fraction = self.verification["rel_error"]
                relative = f"{fraction:.2%}" if fraction is not None else "정의되지 않음 (실제값 0)"
                self.verify_status.setText(f"예측값 {predicted:.6g} | 실제값 {actual:.6g}\n"
                                           f"절대 오차 {error:.6g} · 실제값 대비 {relative}")
            self._redraw_plots()
            return self.verification
        except Exception as error:
            self.verify_status.setText(f"재실행 실패: {error}")
            return None
        finally:
            self._end_run()
