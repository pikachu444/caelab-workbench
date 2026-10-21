"""Optimization panel: HyperStudy-style 5-step flow on the own engine.

Steps: 1) setup (variables, single objective) -> 2) DOE (actual
evaluations, plus a separate test DOE) -> 3) approximation model
(RBF surrogate; its accuracy is measured against the test DOE, never
against its own training rows) -> 4) optimization (own differential
evolution; on the surrogate when one exists, on the actual evaluator
otherwise) -> 5) verification (the best row is re-run on the actual
evaluator and its predicted and actual values are compared side by
side).

The centre history table keeps every row's origin distinct: DOE and
test-DOE rows and the verification row are actual evaluations;
optimization rows evaluated on the surrogate are labelled 예측
(prediction) and are never shown as actual results. The current best
row is highlighted.

Single objective only (first scope). The panel is a standalone
widget: the host supplies the evaluator — directly, or via
``configure_with_model`` with common-model variable/response specs
and a run function. Main-window docking is a merge-request item for
the integration chat; this file does not touch the main window.

GUI completion is judged on a real Windows screen only; on Linux this
panel is verified offscreen by tests/test_optimization_panel.py.
"""

from __future__ import annotations

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from caelab.numerical import (ModelEvaluator, fit_surrogate, normalize_variables,
                              optimize, run_doe, scalar_value)

_BEST_BG = QColor("#d9f2d9")
_SOURCES = {"doe": "DOE", "test": "테스트 DOE", "opt_surrogate": "최적화(근사 예측)",
            "opt_actual": "최적화(실제)", "verify": "재실행"}


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

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("최적화 (5단계: 설정 → DOE → 근사모델 → 최적화 → 재실행 검증)"))

        # -- step 1: setup ------------------------------------------------
        setup_box = QGroupBox("1단계 · 설정 (변수와 목적, 단일 목적만)")
        setup_layout = QVBoxLayout(setup_box)
        self.variable_table = QTableWidget(0, 5)
        self.variable_table.setHorizontalHeaderLabels(["변수", "단위", "하한", "상한", "시작값"])
        setup_layout.addWidget(self.variable_table)
        obj_row = QHBoxLayout()
        obj_row.addWidget(QLabel("목적 응답"))
        self.response_edit = QLineEdit()
        obj_row.addWidget(self.response_edit)
        obj_row.addWidget(QLabel("단위"))
        self.unit_edit = QLineEdit("1")
        self.unit_edit.setMaximumWidth(60)
        obj_row.addWidget(self.unit_edit)
        self.direction_combo = QComboBox()
        self.direction_combo.addItem("최소화", "minimize")
        self.direction_combo.addItem("최대화", "maximize")
        obj_row.addWidget(self.direction_combo)
        setup_layout.addLayout(obj_row)
        self.setup_status = QLabel("평가 함수가 연결되지 않았습니다.")
        setup_layout.addWidget(self.setup_status)
        apply_btn = QPushButton("설정 적용")
        apply_btn.clicked.connect(self.apply_setup)
        setup_layout.addWidget(apply_btn)
        layout.addWidget(setup_box)

        # -- centre: history table ----------------------------------------
        layout.addWidget(QLabel("이력 (출처를 구분해 표시합니다. 예측은 실제 결과가 아닙니다)"))
        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(["후보", "출처", "변수값", "목적값"])
        layout.addWidget(self.history_table, 1)

        # -- step 2: DOE ----------------------------------------------------
        doe_box = QGroupBox("2단계 · DOE (실제 평가)")
        doe_layout = QHBoxLayout(doe_box)
        doe_layout.addWidget(QLabel("개수"))
        self.doe_count = QSpinBox(minimum=4, maximum=512, value=16)
        doe_layout.addWidget(self.doe_count)
        doe_layout.addWidget(QLabel("시드"))
        self.doe_seed = QSpinBox(minimum=0, maximum=2**31 - 1, value=0)
        doe_layout.addWidget(self.doe_seed)
        doe_layout.addWidget(QLabel("테스트 DOE 개수"))
        self.test_count = QSpinBox(minimum=2, maximum=512, value=8)
        doe_layout.addWidget(self.test_count)
        doe_btn = QPushButton("DOE 실행")
        doe_btn.clicked.connect(self.run_doe_step)
        doe_layout.addWidget(doe_btn)
        self.doe_status = QLabel("")
        doe_layout.addWidget(self.doe_status)
        layout.addWidget(doe_box)

        # -- step 3: surrogate ----------------------------------------------
        sur_box = QGroupBox("3단계 · 근사모델 (정확도는 테스트 DOE로 잽니다)")
        sur_layout = QHBoxLayout(sur_box)
        sur_btn = QPushButton("근사모델 만들기")
        sur_btn.clicked.connect(self.run_surrogate_step)
        sur_layout.addWidget(sur_btn)
        self.surrogate_status = QLabel("아직 근사모델이 없습니다.")
        self.surrogate_status.setWordWrap(True)
        sur_layout.addWidget(self.surrogate_status)
        layout.addWidget(sur_box)

        # -- step 4: optimization --------------------------------------------
        opt_box = QGroupBox("4단계 · 최적화 (자체 차분진화 엔진)")
        opt_layout = QHBoxLayout(opt_box)
        opt_layout.addWidget(QLabel("세대"))
        self.opt_generations = QSpinBox(minimum=1, maximum=200, value=15)
        opt_layout.addWidget(self.opt_generations)
        opt_layout.addWidget(QLabel("개체수"))
        self.opt_population = QSpinBox(minimum=5, maximum=100, value=10)
        opt_layout.addWidget(self.opt_population)
        opt_btn = QPushButton("최적화 실행")
        opt_btn.clicked.connect(self.run_optimization_step)
        opt_layout.addWidget(opt_btn)
        self.opt_status = QLabel("")
        self.opt_status.setWordWrap(True)
        opt_layout.addWidget(self.opt_status)
        layout.addWidget(opt_box)

        # -- step 5: verification ---------------------------------------------
        ver_box = QGroupBox("5단계 · 재실행 검증 (최적 행을 실제 평가로 다시 실행)")
        ver_layout = QHBoxLayout(ver_box)
        ver_btn = QPushButton("최적 행 재실행")
        ver_btn.clicked.connect(self.run_verification_step)
        ver_layout.addWidget(ver_btn)
        self.verify_status = QLabel("아직 검증하지 않았습니다.")
        self.verify_status.setWordWrap(True)
        ver_layout.addWidget(self.verify_status)
        layout.addWidget(ver_box)

        if evaluator is not None and variables:
            self.set_study(evaluator, variables, objective)

    # -- study setup -------------------------------------------------------
    def set_study(self, evaluator, variables, objective=None) -> None:
        """Connect an evaluator and show its variables/objective in step 1."""
        self.evaluator = evaluator
        self.variables = normalize_variables(variables)
        if objective:
            self.objective = {"response": objective.get("response", ""),
                              "unit": objective.get("unit", "1"),
                              "direction": objective.get("direction", "minimize")}
        self._fill_setup_widgets()
        self.setup_status.setText(
            f"변수 {len(self.variables)}개가 연결됐습니다. 설정을 확인하고 적용하세요.")

    def configure_with_model(self, model, variable_specs, run_fn, response_specs) -> None:
        """Connect through the common model: variable paths -> variables,
        result paths -> the (single) objective response."""
        evaluator = ModelEvaluator(model, variable_specs, run_fn, response_specs)
        first = response_specs[0]
        self.set_study(evaluator, evaluator.variables,
                       {"response": first["response"], "unit": first.get("unit", "1"),
                        "direction": first.get("direction", "minimize")})

    def _fill_setup_widgets(self) -> None:
        self.variable_table.setRowCount(len(self.variables))
        for row, var in enumerate(self.variables):
            for col, key in enumerate(("id", "unit", "lower", "upper", "value")):
                self.variable_table.setItem(row, col, QTableWidgetItem(str(var[key])))
        self.response_edit.setText(self.objective.get("response", ""))
        self.unit_edit.setText(self.objective.get("unit", "1"))
        index = self.direction_combo.findData(self.objective.get("direction", "minimize"))
        if index >= 0:
            self.direction_combo.setCurrentIndex(index)

    def apply_setup(self) -> bool:
        """Parse the setup table into engine declarations (step 1 action)."""
        declarations = []
        try:
            for row in range(self.variable_table.rowCount()):
                cells = [(self.variable_table.item(row, col).text().strip()
                          if self.variable_table.item(row, col) else "")
                         for col in range(5)]
                declarations.append({"id": cells[0], "unit": cells[1],
                                     "lower": float(cells[2]), "upper": float(cells[3]),
                                     "value": float(cells[4])})
            variables = normalize_variables(declarations)
            response = self.response_edit.text().strip()
            if not response:
                raise ValueError("목적 응답 이름을 입력하세요")
            objective = {"response": response,
                         "unit": self.unit_edit.text().strip() or "1",
                         "direction": self.direction_combo.currentData()}
        except (ValueError, TypeError) as error:
            self.setup_status.setText(f"설정 오류: {error}")
            return False
        self.variables, self.objective = variables, objective
        # A new setup invalidates every downstream step.
        self.doe_table = self.test_table = None
        self.surrogate = None
        self.optimization_result = None
        self.verification = None
        self._opt_best_key = None
        self._history = []
        self._refresh_history()
        self.setup_status.setText(f"설정 적용됨: 변수 {len(variables)}개, 목적 {response} "
                                  f"({objective['direction']}).")
        return True

    # -- history -------------------------------------------------------------
    def _append_history(self, source, rows) -> None:
        for row in rows:
            objective = None
            try:
                objective = scalar_value(row, self.objective)
            except Exception:  # noqa: BLE001 - failed rows keep no objective
                objective = None
            self._history.append({"source": source, "candidate_id": row["candidate_id"],
                                  "values": dict(row["values"]), "objective": objective,
                                  "status": row.get("execution_status", "")})
        self._refresh_history()

    def _refresh_history(self) -> None:
        self.history_table.setRowCount(len(self._history))
        self.best_history_index = self._best_index()
        for i, entry in enumerate(self._history):
            values = ", ".join(f"{k}={v:.6g}" for k, v in entry["values"].items())
            objective = "—" if entry["objective"] is None else f"{entry['objective']:.6g}"
            cells = [entry["candidate_id"], _SOURCES.get(entry["source"], entry["source"]),
                     values, objective]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if i == self.best_history_index:
                    item.setBackground(QBrush(_BEST_BG))
                self.history_table.setItem(i, col, item)
        if self.best_history_index is not None:
            self.history_table.selectRow(self.best_history_index)

    def _best_index(self):
        """The step-4 best row when it is pinned; otherwise the best
        row by objective direction (verification rows excluded)."""
        if self._opt_best_key is not None:
            for i, entry in enumerate(self._history):
                if (entry["source"], entry["candidate_id"]) == self._opt_best_key:
                    return i
        best = None
        maximize = self.objective.get("direction") == "maximize"
        for i, entry in enumerate(self._history):
            if entry["objective"] is None or entry["source"] == "verify":
                continue
            if best is None:
                best = i
                continue
            current = self._history[best]["objective"]
            if (maximize and entry["objective"] > current) or \
                    (not maximize and entry["objective"] < current):
                best = i
        return best

    # -- steps ----------------------------------------------------------------
    def _require_study(self, status_label) -> bool:
        if self.evaluator is None or not self.variables:
            status_label.setText("먼저 1단계에서 평가 함수와 변수를 연결·적용하세요.")
            return False
        return True

    def run_doe_step(self) -> dict | None:
        """Step 2: training DOE and a disjoint test DOE, both actual."""
        if not self._require_study(self.doe_status):
            return None
        seed = self.doe_seed.value()
        self.doe_table = run_doe(self.evaluator, self.variables,
                                 count=self.doe_count.value(), seed=seed)
        self.test_table = run_doe(self.evaluator, self.variables,
                                  count=self.test_count.value(), seed=seed + 1)
        self._history = []
        self._opt_best_key = None
        self._append_history("doe", self.doe_table["candidates"])
        self._append_history("test", self.test_table["candidates"])
        failed = sum(1 for r in self._history if r["status"] != "SUCCEEDED")
        self.doe_status.setText(
            f"실제 평가 {len(self._history)}개 완료 (실패 {failed}개). "
            "테스트 DOE는 근사모델 정확도 측정 전용입니다.")
        return self.doe_table

    def run_surrogate_step(self):
        """Step 3: RBF surrogate trained on DOE, accuracy on the test DOE."""
        if self.doe_table is None or self.test_table is None:
            self.surrogate_status.setText("먼저 2단계 DOE를 실행하세요.")
            return None
        try:
            self.surrogate = fit_surrogate(
                self.doe_table, self.test_table,
                {"response": self.objective["response"], "unit": self.objective["unit"]},
                variables=self.variables)
        except Exception as error:  # noqa: BLE001 - shown as one line
            self.surrogate_status.setText(f"근사모델 실패: {error}")
            return None
        validation = self.surrogate.validation
        self.surrogate_status.setText(
            f"근사모델 정확도 (테스트 DOE {len(validation['holdout_ids'])}개 기준): "
            f"평균 오차 {validation['mae']:.6g}, RMSE {validation['rmse']:.6g}, "
            f"최대 오차 {validation['max_error']:.6g} (단위 {validation['unit']}). "
            "학습 데이터가 아닌 별도 평가로 잰 값입니다.")
        return self.surrogate

    def _surrogate_evaluator(self):
        surrogate, objective = self.surrogate, self.objective

        def evaluate(values):
            predicted = surrogate.predict([values])[0]
            return {"responses": predicted["responses"]}

        return evaluate

    def run_optimization_step(self) -> dict | None:
        """Step 4: differential evolution — on the surrogate if one
        exists (rows labelled 예측), else on the actual evaluator."""
        if not self._require_study(self.opt_status):
            return None
        on_surrogate = self.surrogate is not None
        evaluator = self._surrogate_evaluator() if on_surrogate else self.evaluator
        self.optimization_result = optimize(
            evaluator, self.variables, self.objective,
            options={"max_generations": self.opt_generations.value(),
                     "population_size": self.opt_population.value()},
            seed=self.doe_seed.value())
        source = "opt_surrogate" if on_surrogate else "opt_actual"
        self._append_history(source, self.optimization_result["candidates"])
        best = self.optimization_result.get("best")
        if best is not None:
            self._opt_best_key = (source, best["candidate_id"])
            self._refresh_history()
            self.opt_status.setText(
                f"최적값 {best['objective']:.6g} "
                f"({'근사모델 예측' if on_surrogate else '실제 평가'} 기준). "
                "5단계에서 실제 평가로 검증하세요.")
        else:
            self.opt_status.setText("실행 가능한 최적 행을 찾지 못했습니다.")
        return self.optimization_result

    def run_verification_step(self) -> dict | None:
        """Step 5: re-run the best row on the actual evaluator and
        compare the surrogate prediction with the actual value."""
        if not self._require_study(self.verify_status):
            return None
        if self.optimization_result is None or not self.optimization_result.get("best"):
            self.verify_status.setText("먼저 4단계 최적화를 실행하세요.")
            return None
        best = self.optimization_result["best"]
        values = {v["id"]: best["values"][v["id"]] for v in self.variables}
        from caelab.numerical import evaluate_once

        evaluation, _elapsed = evaluate_once(self.evaluator, values)
        try:
            actual = scalar_value(evaluation, self.objective)
        except Exception as error:  # noqa: BLE001
            self.verify_status.setText(f"재실행 실패: {error}")
            return None
        predicted = None
        if self.surrogate is not None:
            row = self.surrogate.predict([values])[0]
            predicted = row["responses"][self.objective["response"]]["value"]
        # A relative-to-actual error explodes near a zero optimum, so the
        # error is also stated against the DOE objective range.
        doe_objectives = [e["objective"] for e in self._history
                          if e["source"] in ("doe", "test") and e["objective"] is not None]
        doe_range = (max(doe_objectives) - min(doe_objectives)) if doe_objectives else None
        self.verification = {"values": values, "actual": actual,
                             "predicted": predicted,
                             "abs_error": abs(actual - predicted) if predicted is not None else None,
                             "rel_error": (abs(actual - predicted) / abs(actual)
                                           if predicted is not None and actual != 0 else None),
                             "range_fraction": (abs(actual - predicted) / doe_range
                                                if predicted is not None and doe_range else None)}
        self._append_history("verify", [{"candidate_id": "V000000", "values": values,
                                         "execution_status": "SUCCEEDED",
                                         "responses": evaluation.get("responses", {})}])
        if predicted is None:
            self.verify_status.setText(
                f"재실행 실제값 {actual:.6g}. 근사모델이 없어 예측 비교는 없습니다.")
        else:
            range_text = (f", DOE 범위 대비 {self.verification['range_fraction']:.2%}"
                          if self.verification["range_fraction"] is not None else "")
            self.verify_status.setText(
                f"예측값 {predicted:.6g} vs 재실행 실제값 {actual:.6g} — "
                f"절대 오차 {self.verification['abs_error']:.6g}{range_text}.")
        return self.verification
