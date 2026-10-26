"""Optimization panel (offscreen Qt): the 5-step HyperStudy-style flow.

The evaluator is an analytic bowl with known minimum 0 at (0.5, -1);
expected numbers are analytic or stated with their tolerance. This
proves the panel flow on Linux offscreen only — GUI completion is
judged on a real Windows screen (apps/workbench_qt rules).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from apps.workbench_qt.optimization_panel import OptimizationPanel
from caelab.numerical import ModelEvaluator
from caelab.solvers.calculix import find_ccx

VARS = [
    {"id": "x", "unit": "1", "lower": -2.0, "upper": 2.0, "value": 0.0},
    {"id": "y", "unit": "1", "lower": -2.0, "upper": 2.0, "value": 0.0},
]


def bowl(values):
    return {"responses": {"f": {"kind": "scalar",
                                "value": (values["x"] - 0.5) ** 2
                                + (values["y"] + 1.0) ** 2,
                                "unit": "1"}}}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_ui_theme_settings(tmp_path, monkeypatch):
    from apps.workbench_qt.theme import set_theme_mode
    monkeypatch.setenv("CAELAB_UI_SETTINGS_PATH", str(tmp_path / "ui-settings.ini"))
    set_theme_mode("dark")
    yield
    set_theme_mode("dark")


def _panel(app):
    panel = OptimizationPanel(
        evaluator=bowl, variables=VARS,
        objective={"response": "f", "unit": "1", "direction": "minimize"})
    panel.doe_count.setValue(16)
    panel.test_count.setValue(8)
    panel.opt_generations.setValue(12)
    panel.opt_population.setValue(10)
    assert panel.apply_setup()
    return panel


def test_setup_rejects_inverted_bounds_with_one_line(app):
    panel = _panel(app)
    panel.variable_table.item(0, 3).setText("5")   # lower (col 3)
    panel.variable_table.item(0, 4).setText("-5")  # upper (col 4) < lower
    assert not panel.apply_setup()
    assert "설정 오류" in panel.setup_status.text()


def test_full_five_step_flow(app):
    panel = _panel(app)

    doe = panel.run_doe_step()
    assert doe is not None and doe["execution_status"] == "SUCCEEDED", panel.doe_status.text()
    # History: 16 training DOE + 8 test DOE actual rows.
    assert panel.history_table.rowCount() == 24
    assert panel.best_history_index is not None

    surrogate = panel.run_surrogate_step()
    assert surrogate is not None
    # Accuracy is measured on the separate test DOE (8 rows).
    assert len(surrogate.validation["holdout_ids"]) == 8
    assert surrogate.validation["rmse"] < 0.6  # quadratic bowl, 16 training pts
    assert "테스트 DOE 8개 기준" in panel.surrogate_status.text()

    result = panel.run_optimization_step()
    assert result is not None and result["best"] is not None
    # Optimized on the surrogate; the best row is pinned and highlighted.
    assert panel.best_history_index is not None
    best_entry = panel._history[panel.best_history_index]
    assert best_entry["source"] == "opt_surrogate"
    item = panel.history_table.item(panel.best_history_index, 0)
    assert item.background().color().green() > 200  # highlight brush applied
    assert result["best"]["objective"] < 0.25

    verification = panel.run_verification_step()
    assert verification is not None
    values = verification["values"]
    analytic = (values["x"] - 0.5) ** 2 + (values["y"] + 1.0) ** 2
    # The re-run actual value is exactly the analytic bowl at the best point.
    assert verification["actual"] == pytest.approx(analytic, abs=1e-9)
    # Predicted (surrogate) vs actual are compared, with the error stated.
    assert verification["predicted"] is not None
    assert verification["abs_error"] == pytest.approx(
        abs(verification["predicted"] - verification["actual"]))
    assert verification["abs_error"] < 0.6
    # Error is also stated against the DOE objective range (a relative-to-
    # actual error alone explodes near the zero optimum).
    assert verification["range_fraction"] is not None
    assert verification["range_fraction"] < 0.01
    assert "예측값" in panel.verify_status.text()
    assert "실제값" in panel.verify_status.text()
    # Verification adds one actual re-run row at the end of the history.
    assert panel._history[-1]["source"] == "verify"


def test_optimization_without_surrogate_uses_actual_evaluator(app):
    panel = _panel(app)
    panel.run_doe_step()
    result = panel.run_optimization_step()  # no surrogate built
    assert result["best"]["objective"] < 0.05  # analytic minimum is 0
    verification = panel.run_verification_step()
    assert verification["predicted"] is None  # no surrogate -> no comparison
    assert verification["actual"] == pytest.approx(
        result["best"]["objective"], abs=1e-9)  # deterministic re-run


def test_panel_without_evaluator_says_so(app):
    panel = OptimizationPanel()
    assert "연결되지 않았습니다" in panel.setup_status.text()
    assert panel.run_doe_step() is None
    assert "먼저 1단계" in panel.doe_status.text()


# -- model connection (step-1 model paths -> ModelEvaluator) ---------------

E_SPEC = {"id": "E", "path": "materials[0].params.youngs_modulus", "unit": "Pa",
          "lower": 1.0e11, "upper": 3.0e11, "value": 2.1e11}
FY_SPEC = {"id": "fy", "path": "loads[TIP_FORCE].values.fy", "unit": "N",
           "lower": -2000.0, "upper": -500.0, "value": -1000.0}
DISP_SPEC = {"response": "disp", "path": "max_displacement", "unit": "m",
             "direction": "minimize"}


def _fake_run(model):
    """Cheap stand-in solver: Euler-Bernoulli tip deflection as the result."""
    from caelab.solvers.base import ResultSet

    e = model.materials[0].params["youngs_modulus"]
    fy = model.loads[0].values["fy"]
    inertia = 0.1 * 0.1 ** 3 / 12
    return ResultSet(solver_id="fake", success=True,
                     max_displacement=abs(fy) / (3 * e * inertia),
                     reaction_totals={"fy": -fy})


def _model_panel(app):
    from conftest import cantilever_model

    panel = OptimizationPanel()
    panel.configure_with_model(cantilever_model(), [E_SPEC, FY_SPEC],
                               _fake_run, [DISP_SPEC])
    return panel


def test_model_paths_shown_and_applied(app):
    panel = _model_panel(app)
    # The paths the user picked are visible in the step-1 table.
    assert panel.variable_table.item(0, 2).text() == E_SPEC["path"]
    assert panel.variable_table.item(1, 2).text() == FY_SPEC["path"]
    assert panel.response_path_edit.text() == "max_displacement"
    assert panel.apply_setup()
    assert "모델 경로 연결" in panel.setup_status.text()
    specs = panel.current_variable_specs()
    assert [s["path"] for s in specs] == [E_SPEC["path"], FY_SPEC["path"]]
    assert isinstance(panel.evaluator, ModelEvaluator)


def test_model_path_errors_are_one_line(app):
    panel = _model_panel(app)
    panel.variable_table.item(0, 2).setText("")  # mixed path / no-path
    assert not panel.apply_setup()
    assert "모든 변수" in panel.setup_status.text()
    panel = _model_panel(app)
    panel.variable_table.item(0, 2).setText("materials[0].params.nope")
    assert not panel.apply_setup()
    assert "설정 오류" in panel.setup_status.text()


def test_model_mode_full_flow_on_fake_solver(app):
    panel = _model_panel(app)
    panel.doe_count.setValue(8)
    panel.test_count.setValue(4)
    panel.opt_generations.setValue(8)
    panel.opt_population.setValue(6)
    assert panel.apply_setup()
    assert panel.run_doe_step() is not None
    assert panel.run_surrogate_step() is not None
    assert panel.run_optimization_step()["best"] is not None
    verification = panel.run_verification_step()
    values = verification["values"]
    inertia = 0.1 * 0.1 ** 3 / 12
    analytic = abs(values["fy"]) / (3 * values["E"] * inertia)
    assert verification["actual"] == pytest.approx(analytic, rel=1e-9)


@pytest.mark.skipif(find_ccx() is None, reason="ccx not available (CAELAB_CCX)")
def test_cantilever_end_to_end_with_real_solver(app, tmp_path):
    """The whole panel flow on the real CalculiX converter: DOE ->
    surrogate -> optimization -> re-run. The re-run value must match
    an independent direct solver run at the same values, and beat the
    nominal model's displacement (minimizing pushes E up, |F| down).
    """
    import copy
    import tempfile

    from caelab.numerical import set_path
    from caelab.solvers.calculix import CalculiXConverter
    from conftest import cantilever_model

    converter = CalculiXConverter()

    def run_fn(model):
        with tempfile.TemporaryDirectory() as td:
            deck = converter.write_input(model, td)
            return converter.run(model, deck, td)

    def direct(values):
        model = copy.deepcopy(cantilever_model())
        for spec in (E_SPEC, FY_SPEC):
            set_path(model, spec["path"], values[spec["id"]])
        return run_fn(model).max_displacement

    panel = OptimizationPanel()
    panel.configure_with_model(cantilever_model(), [E_SPEC, FY_SPEC],
                               run_fn, [DISP_SPEC])
    panel.doe_count.setValue(8)
    panel.test_count.setValue(4)
    panel.opt_generations.setValue(8)
    panel.opt_population.setValue(6)
    assert panel.apply_setup()

    assert panel.run_doe_step()["execution_status"] == "SUCCEEDED"
    assert panel.run_surrogate_step() is not None
    result = panel.run_optimization_step()
    assert result["best"] is not None
    verification = panel.run_verification_step()
    assert verification is not None

    # Re-run value vs an independent direct solver run at the best values.
    solver_value = direct(verification["values"])
    assert verification["actual"] == pytest.approx(solver_value, rel=0.005)
    # And it really improved on the nominal model (E=210 GPa, F=-1000 N).
    nominal = direct({"E": 2.1e11, "fy": -1000.0})
    assert verification["actual"] < 0.8 * nominal
    # Sanity band against Euler-Bernoulli at the best values.
    inertia = 0.1 * 0.1 ** 3 / 12
    values = verification["values"]
    analytic = abs(values["fy"]) / (3 * values["E"] * inertia)
    assert 0.4 * analytic < verification["actual"] < 1.2 * analytic


def test_optimization_panel_theme_toggle_updates_highlight(app, tmp_path, monkeypatch):
    monkeypatch.setenv("CAELAB_UI_SETTINGS_PATH", str(tmp_path / "ui.ini"))
    from apps.workbench_qt.theme import get_palette, set_theme_mode
    try:
        set_theme_mode("dark")
        panel = _panel(app)
        panel.run_doe_step()
        assert panel.run_surrogate_step() is not None
        assert panel.run_optimization_step()["best"] is not None
        assert panel.best_history_index is not None

        dark_p = get_palette("dark")
        item_dark = panel.history_table.item(panel.best_history_index, 0)
        assert item_dark.background().color().green() > 200
        assert item_dark.background().color().name().lower() == str(dark_p["best_row_bg"]).lower()
        assert item_dark.foreground().color().name().lower() == str(dark_p["best_row_fg"]).lower()

        set_theme_mode("light")
        light_p = get_palette("light")
        item_light = panel.history_table.item(panel.best_history_index, 0)
        assert item_light.background().color().green() > 200
        assert item_light.background().color().name().lower() == str(light_p["best_row_bg"]).lower()
        assert item_light.foreground().color().name().lower() == str(light_p["best_row_fg"]).lower()
    finally:
        set_theme_mode("dark")


# Dashboard data and interaction regressions use analytic evaluations, not FEA.
def test_splitter_tabs_and_headless_render_at_constrained_size(app):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QSplitter
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

    panel = _panel(app)
    panel.resize(960, 640)
    panel.show()
    app.processEvents()
    assert isinstance(panel.splitter, QSplitter)
    assert panel.splitter.orientation() == Qt.Horizontal
    assert panel.splitter.count() == 2
    assert [panel.visualization_tabs.tabText(i) for i in range(3)] == [
        '수렴 이력', '민감도 분석', '대리모델 검증']
    assert panel.splitter.sizes()[0] >= 360
    assert panel.splitter.sizes()[1] >= 300
    for i, canvas in enumerate(panel.canvases):
        assert isinstance(canvas, FigureCanvasQTAgg)
        panel.visualization_tabs.setCurrentIndex(i)
        app.processEvents()
        canvas.draw()
        assert not canvas.grab().isNull()
    panel.close()


def test_holdout_parity_and_separate_convergence_traces(app):
    import numpy as np

    panel = _panel(app)
    panel.run_doe_step()
    surrogate = panel.run_surrogate_step()
    validation = dict(surrogate.validation)
    panel.run_optimization_step()
    verification = panel.run_verification_step()
    parity = panel.axes[2]
    points = {c.get_label(): c.get_offsets() for c in parity.collections}
    holdout = points['Holdout (actual vs predicted)']
    assert holdout[:, 0].tolist() == pytest.approx(validation['holdout_observed'])
    assert holdout[:, 1].tolist() == pytest.approx(validation['holdout_prediction'])
    assert np.asarray(points['Verified optimum']) == pytest.approx(
        np.array([[verification['actual'], verification['predicted']]]))
    assert surrogate.validation == validation  # verification is not holdout data
    assert parity.get_xlim() == pytest.approx(parity.get_ylim())
    assert any('Holdout RMSE' in t.get_text() for t in parity.texts)
    traces = {line.get_label(): line for line in panel.axes[0].lines}
    for predicted, label in ((False, 'Best actual'), (True, 'Best prediction')):
        expected = [r['objective'] for r in panel._history
                    if (r['source'] == 'opt_surrogate') == predicted
                    and r['status'] == 'SUCCEEDED']
        assert traces[label].get_ydata() == pytest.approx(np.minimum.accumulate(expected))


@pytest.mark.parametrize('mode', ['dark', 'light'])
def test_chart_theme_covers_axes_text_grid_legend_and_badge(app, mode):
    from matplotlib.colors import to_rgba
    from apps.workbench_qt.theme import get_palette, set_theme_mode

    panel = _panel(app)
    panel.run_doe_step()
    panel.run_surrogate_step()
    set_theme_mode(mode)
    palette = get_palette(mode)
    for canvas, ax in zip(panel.canvases, panel.axes):
        canvas.draw()
        assert canvas.figure.get_facecolor() == to_rgba(palette['canvas_bg'])
        assert ax.get_facecolor() == to_rgba(palette['card_bg'])
        assert ax.spines['left'].get_edgecolor() == to_rgba(palette['border'])
        assert ax.title.get_color() == palette['text_primary']
        assert ax.xaxis.label.get_color() == palette['text_primary']
        assert ax.get_xticklabels()[0].get_color() == palette['text_muted']
        assert ax.get_xgridlines()[0].get_color() == palette['border']
        if ax.get_legend():
            assert all(to_rgba(t.get_color()) == to_rgba(palette['text_primary'])
                       for t in ax.get_legend().get_texts())
    badge = panel.axes[2].texts[0]
    assert badge.get_color() == palette['text_primary']
    assert badge.get_bbox_patch().get_facecolor() == to_rgba(palette['card_bg'])


@pytest.mark.parametrize('step,cancel_after', [('doe', 0), ('doe', 3), ('doe', 18), ('optimization', 0), ('optimization', 3)])
def test_cancellation_preserves_completed_rows_and_guards_reentry(app, step, cancel_after):
    from PySide6.QtCore import QTimer

    panel = _panel(app)
    calls = []

    def evaluator(values):
        calls.append(dict(values))
        assert not panel.controller.isEnabled()
        assert panel.run_doe_step() is None
        assert panel.run_optimization_step() is None
        assert not panel.apply_setup()
        if len(calls) == cancel_after:
            QTimer.singleShot(0, panel.cancel_button.click)
        return bowl(values)

    panel.evaluator = evaluator
    if cancel_after == 0:
        QTimer.singleShot(0, panel.cancel_button.click)
    result = panel.run_doe_step() if step == 'doe' else panel.run_optimization_step()
    cancelled = panel.test_table if cancel_after == 18 else result
    assert cancelled['execution_status'] == 'CANCELLED'
    assert len(calls) == cancel_after
    assert len(panel._history) == cancel_after
    assert all(r['status'] == 'SUCCEEDED' for r in panel._history)
    assert panel.controller.isEnabled()
    assert not panel.cancel_button.isEnabled()
    assert not panel._busy
    # Cancellation does not leak into the next run.
    panel.evaluator = bowl
    assert panel.run_doe_step()['execution_status'] == 'SUCCEEDED'
    assert len(panel._history) == 24


def test_failed_rows_keep_exact_reason_and_are_excluded_from_fitting(app):
    panel = _panel(app)
    calls = 0

    def partly_failing(values):
        nonlocal calls
        calls += 1
        if calls in (2, 18):
            raise RuntimeError('solver divergence at increment 7')
        return bowl(values)

    panel.evaluator = partly_failing
    panel.run_doe_step()
    failures = [r for r in panel._history if r['status'] == 'FAILED']
    assert len(failures) == 2
    assert all(r['objective'] is None for r in failures)
    assert all(r['failure_reason'] == 'RuntimeError: solver divergence at increment 7' for r in failures)
    assert panel.history_table.item(1, 3).text() == '—'
    assert panel.history_table.item(1, 5).text() == failures[0]['failure_reason']
    surrogate = panel.run_surrogate_step()
    assert surrogate is not None
    assert len(surrogate.validation['train_ids']) == 15
    assert len(surrogate.validation['holdout_ids']) == 7
    failed_markers = next(c for c in panel.axes[0].collections if c.get_label() == 'FAILED (no value)')
    assert failed_markers.get_offsets()[:, 0].tolist() == pytest.approx([2, 18])
    # Status markers use axes Y, not fabricated objective data.
    assert failed_markers.get_offset_transform() == panel.axes[0].get_xaxis_transform()


def test_failed_verification_retains_reason_and_clears_previous_point(app):
    panel = _panel(app)
    panel.run_doe_step()
    panel.run_surrogate_step()
    panel.run_optimization_step()
    assert panel.run_verification_step()
    panel.evaluator = lambda values: {'execution_status': 'FAILED', 'responses': {},
                                     'diagnostics': {'failure_reason': 'mesh inversion'}}
    assert panel.run_verification_step() is None
    assert panel.verification is None
    assert panel._history[-1]['failure_reason'] == 'mesh inversion'
    assert panel._history[-1]['status'] == 'FAILED'
    assert panel._history[-1]['objective'] is None
    assert not any(c.get_label() == 'Verified optimum' for c in panel.axes[2].collections)


def test_maximizing_trace_excludes_infeasible_and_predictions(app):
    panel = _panel(app)
    panel.objective['direction'] = 'maximize'
    rows = [{'candidate_id': str(i), 'values': {'x': 0, 'y': 0},
             'execution_status': 'SUCCEEDED', 'feasible': feasible,
             'responses': {'f': {'kind': 'scalar', 'value': value, 'unit': '1'}}}
            for i, (value, feasible) in enumerate([(2, True), (100, False), (1, True), (4, True)])]
    panel._append_history('opt_actual', rows)
    panel._append_history('opt_surrogate', [{**rows[0], 'responses': {'f': {'kind': 'scalar', 'value': 200, 'unit': '1'}}}])
    line = next(l for l in panel.axes[0].lines if l.get_label() == 'Best actual')
    assert line.get_ydata() == pytest.approx([2, 2, 4])
    assert panel.best_history_index == 3
    assert panel._history[1]['status'] == 'INFEASIBLE'
    marker = next(c for c in panel.axes[0].collections if c.get_label() == 'INFEASIBLE')
    assert marker.get_offsets()[0, 1] == 100


def test_tornado_is_ranked_signed_standardized_regression(app):
    import numpy as np

    panel = _panel(app)
    panel.evaluator = lambda v: {'f': 2 * v['x'] - 5 * v['y']}
    panel.run_doe_step()
    x = np.array([[r['values']['x'], r['values']['y']] for r in panel.doe_table['candidates']])
    y = 2 * x[:, 0] - 5 * x[:, 1]
    expected = [2, -5] * np.std(x, axis=0, ddof=1) / np.std(y, ddof=1)
    ax = panel.axes[1]
    labels = [t.get_text() for t in ax.get_yticklabels()]
    bars = ax.patches
    assert labels == ['y', 'x']
    assert [p.get_width() for p in bars] == pytest.approx(expected[::-1])
    assert bars[0].get_facecolor() != bars[1].get_facecolor()
    assert ax.yaxis_inverted()


def test_polynomial_option_and_reruns_clear_downstream_results(app):
    panel = _panel(app)
    panel.model_combo.setCurrentIndex(panel.model_combo.findData('polynomial'))
    panel.run_doe_step()
    surrogate = panel.run_surrogate_step()
    assert surrogate.validation['rmse'] < 1e-12  # exact quadratic bowl
    panel.run_optimization_step()
    panel.run_verification_step()
    assert panel.run_surrogate_step() is not None
    assert panel.optimization_result is None and panel.verification is None
    assert all(r['source'] in ('doe', 'test') for r in panel._history)
    panel.run_doe_step()
    assert panel.surrogate is None
    assert not panel.axes[2].collections


def test_tools_menu_opens_and_reuses_dashboard_with_native_binding_prompt(app):
    from apps.workbench_qt.project_window import ProjectWindow

    window = ProjectWindow()
    try:
        menu = next(a.menu() for a in window.menuBar().actions() if a.text() == '도구(&T)')
        action = next(a for a in menu.actions() if a.text() == '최적화 및 매개변수 연구...')
        action.trigger()
        panel = window._optimization_panel
        assert panel.isVisible()
        assert panel.evaluator is None
        assert '자동 연결되지 않습니다' in panel.setup_status.text()
        panel.close()
        action.trigger()
        assert window._optimization_panel is panel
        assert panel.isVisible()
    finally:
        window.close()


def test_missing_scalar_value_does_not_abort_doe_or_sensitivity(app):
    panel = _panel(app)
    calls = 0

    def evaluator(values):
        nonlocal calls
        calls += 1
        if calls == 2:
            return {'responses': {'f': {'kind': 'scalar', 'unit': '1'}}}
        return bowl(values)

    panel.evaluator = evaluator
    assert panel.run_doe_step() is not None
    assert calls == 24
    assert len(panel.test_table['candidates']) == 8
    row = panel._history[1]
    assert row['objective'] is None and row['status'] == 'FAILED'
    assert row['failure_reason'] == "'value'"
    assert panel.run_surrogate_step() is not None
    assert len(panel.surrogate.validation['train_ids']) == 15
