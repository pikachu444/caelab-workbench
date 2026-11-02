import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QAction

from apps.workbench_qt.main_window import MainWindow, TREE_GROUPS
from conftest import cantilever_model


@pytest.fixture(scope="module")
def app():
    # Reuse an existing instance: creating a second QApplication in
    # the same pytest process (another GUI test module may have made
    # one already) raises "destroy the QGuiApplication singleton".
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_ui_theme_settings(tmp_path, monkeypatch):
    from apps.workbench_qt.theme import set_theme_mode
    monkeypatch.setenv("CAELAB_UI_SETTINGS_PATH", str(tmp_path / "ui-settings.ini"))
    set_theme_mode("dark")
    yield
    set_theme_mode("dark")


def _completed_model_runs(workbench, model, folder, results, monkeypatch):
    from types import SimpleNamespace
    from caelab.jobs import RunActivity
    from caelab.native.run_store import NativeRun
    records, payloads, calls = [], {}, []
    selection = {'id': None}
    outcomes = iter(results)
    def submit(actual_model, project_folder, solver='calculix', timeout=3600):
        result = next(outcomes)
        run_id = f'{len(records) + 1:032x}'
        success = result is not None and result.success
        run = NativeRun(run_id, 'structural-model', folder / 'runs' / run_id,
                        state='completed' if success else 'failed',
                        parse_state='readable' if success else 'failed', finished_at='finished',
                        returncode=0 if success else 1)
        calls.append((actual_model, solver))
        records.append(run)
        selection['id'] = run_id
        payloads[run_id] = SimpleNamespace(run=run, model=model, result=result)
        return run
    def selected(*args):
        return next((run for run in records if run.id == selection['id']), None)
    def select(folder, run_id):
        selection['id'] = run_id
        return selected()
    monkeypatch.setattr(workbench, 'run', lambda *a: pytest.fail('synchronous GUI solver call'))
    monkeypatch.setattr(workbench, 'submit_model', submit)
    monkeypatch.setattr(workbench, 'model_runs', lambda *a: list(records))
    monkeypatch.setattr(workbench, 'selected_model_run', selected)
    monkeypatch.setattr(workbench, 'select_model_run', select)
    monkeypatch.setattr(workbench, 'native_run_snapshot', lambda run_id: next(r for r in records if r.id == run_id))
    monkeypatch.setattr(workbench, 'native_run_activity', lambda _: RunActivity(False))
    monkeypatch.setattr(workbench, 'model_run_is_current', lambda *a: True)
    monkeypatch.setattr(workbench, 'model_run_result', lambda folder, run_id: payloads[run_id])
    return records, calls


def test_window_tree_and_check(app):
    window = MainWindow()
    window.set_model(cantilever_model())
    root = window.tree.topLevelItem(0)
    assert root.childCount() == len(TREE_GROUPS)
    window.run_check()
    assert "검사 통과" in window.status.text()
    assert not window.btn_goto.isEnabled()


def test_analysis_runs_without_expert_provider_or_proposal_editor(app, monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace

    from PySide6.QtWidgets import QDockWidget
    from caelab.solvers.base import ResultSet
    from caelab.workbench import Workbench

    # Analysis must work even if neither the old panel nor providers can import.
    monkeypatch.setitem(sys.modules, "apps.workbench_qt.expert_panel", None)
    monkeypatch.setitem(sys.modules, "caelab.assist.experts", None)
    workbench = Workbench()
    model = cantilever_model()
    before = model.to_dict()
    result = ResultSet(
        solver_id="calculix", success=True, max_displacement=0.0002,
        displacements={node: [0.0, -0.0002, 0.0] for node in model.mesh.nodes},
        reaction_totals={"fx": 0.0, "fy": 1000.0, "fz": 0.0},
        units={"length": "m", "force": "N"},
    )
    folder = tmp_path / 'project'
    records, calls = _completed_model_runs(workbench, model, folder, [result], monkeypatch)
    window = MainWindow(workbench)
    window.set_model(model, folder)
    assert calls == []
    window.run_solve()
    window._poll_run()
    assert calls == [(model, "calculix")]
    assert window._last_result is result
    assert "계산 완료" in window.status.text()
    assert "1,000" in window.status.text()
    assert window.results_item.childCount() == 1
    assert model.to_dict() == before
    assert not hasattr(window, "expert_panel")
    assert not hasattr(window, "_apply_proposal")
    assert all("전문가" not in dock.windowTitle() for dock in window.findChildren(QDockWidget))
    assert "전문가" not in window._module_buttons
    assert all("전문가" not in action.text() for action in window.menuBar().actions())
    assert all("토론" not in chip.text() and "제안" not in chip.text() for chip in window.flow_labels)
    assert not hasattr(window, "optimization_panel")
    assert any(action.text() == "MDO Studio 열기" for action in window.findChildren(QAction))


def test_props_panel_is_display_only(app):
    """The property panel shows values; it must not edit the model.

    Editing belongs to native programs (FreeCAD/Salome). No input
    widget may appear for a selectable object; values are labels and
    the native-program guide stays visible.
    """
    from PySide6.QtWidgets import (
        QCheckBox, QComboBox, QDoubleSpinBox, QLabel, QLineEdit, QSpinBox, QWidget,
    )

    window = MainWindow()
    model = cantilever_model()
    window.set_model(model)
    edit_widgets = (QLineEdit, QDoubleSpinBox, QSpinBox, QCheckBox, QComboBox)
    for path in ("model", "materials[0]", "loads[0]", "bcs[0]", "mesh", "steps[0]"):
        assert window.select_path(path), path
        found = [w for w in window.props_panel.findChildren(QWidget)
                 if isinstance(w, edit_widgets)]
        assert not found, f"{path}: editable widgets in props panel: {found}"
    # values are shown as text, with units
    assert window.select_path("materials[0]")
    texts = [w.text() for w in window.props_panel.findChildren(QLabel)]
    assert any("210.00 GPa" in t for t in texts), texts
    assert any("0.300" in t for t in texts), texts
    assert window.select_path("bcs[0]")
    texts = [w.text() for w in window.props_panel.findChildren(QLabel)]
    assert any("UX" in t and "UY" in t and "UZ" in t for t in texts), texts
    # the guide to the native programs is present in the panel
    assert "전용 프로그램" in window.props_guide.text()
    assert "제안" not in window.props_guide.text()
    # and the model is untouched by all this viewing
    assert model.materials[0].params["youngs_modulus"] == 210e9
    assert model.bcs[0].fixed_dofs == [1, 2, 3]


def test_window_issue_navigation(app):
    model = cantilever_model()
    model.sections[0].material = "NOPE"
    window = MainWindow()
    window.set_model(model)
    window.run_check()
    assert window.btn_goto.isEnabled()
    window._goto_issue()
    current = window.tree.currentItem()
    assert current is not None
    assert "단면" in current.text(0)



def test_actual_calculix_run_is_independent_of_expert_provider(app, monkeypatch, tmp_path):
    import sys
    import time
    from caelab.solvers.calculix import find_ccx

    if find_ccx() is None:
        pytest.skip("ccx is not available for the analysis-window run path")
    monkeypatch.setitem(sys.modules, "caelab.assist.experts", None)
    window = MainWindow()
    window.set_model(cantilever_model(), tmp_path / 'project')
    try:
        window.run_solve()
        deadline = time.monotonic() + 20
        while window._active_run_id is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert window._active_run_id is None
        assert "계산 완료" in window.status.text(), window.log.toPlainText()
        assert window._last_result.success
        assert window._last_result.reaction_totals["fy"] == pytest.approx(1000, rel=0.005)
    finally:
        window.workbench.close()
        window.close()


def test_new_model_clears_previous_result_and_issue_state(app, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from caelab.solvers.base import ResultSet

    window = MainWindow()
    model_a = cantilever_model()
    folder = tmp_path / 'project'
    result = ResultSet("calculix", True, max_displacement=0.25)
    _completed_model_runs(window.workbench, model_a, folder, [result], monkeypatch)
    window.set_model(model_a, folder)
    window.run_solve()
    window._poll_run()
    window._issue_target = "materials[0]"
    window.btn_goto.setEnabled(True)
    window.viewport._last_result = result
    window.viewport._last_model = model_a
    window.viewport._base_scale = 10.0
    model_b = cantilever_model()
    model_b.name = "model B"
    window.set_model(model_b)
    assert window._last_result is None
    assert window.results_item.childCount() == 0
    assert window._issue_target is None
    assert not window.btn_goto.isEnabled()
    assert not window.legend_label.text()
    assert not window.statusBar().currentMessage()
    assert "계산 완료" not in window.log.toPlainText()
    assert window.viewport._last_model is None
    assert window.viewport._last_result is None
    assert window.viewport._base_scale is None
    window.close()


@pytest.mark.parametrize("failure_has_result", [True, False])
def test_failed_rerun_keeps_history_without_showing_previous_success(app, monkeypatch, tmp_path, failure_has_result):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QLabel
    from PySide6.QtCore import Qt
    from caelab.solvers.base import ResultSet

    window = MainWindow()
    model = cantilever_model()
    folder = tmp_path / 'project'
    success = ResultSet("calculix", True, max_displacement=0.25, units={"length": "m"})
    failure = ResultSet("calculix", False, log_tail="failed input") if failure_has_result else None
    records, _ = _completed_model_runs(window.workbench, model, folder, [success, failure], monkeypatch)
    window.set_model(model, folder)
    window.run_solve()
    window._poll_run()
    assert window.results_item.childCount() == 1
    window.select_path("result")
    assert any(label.text() == "0.2500 m" for label in window.props_panel.findChildren(QLabel))
    window.run_solve()
    window._poll_run()
    assert window.results_item.childCount() == 2
    assert window._path_items['result'].data(0, Qt.UserRole) == f'runs.{records[-1].id}'
    assert window._last_result is failure
    assert not window.legend_label.text()
    assert "완료" not in window.statusBar().currentMessage()
    assert "완료" not in window.status.text()
    assert all(label.text() != "0.2500 m" for label in window.props_panel.findChildren(QLabel))
    window.close()


def test_main_window_theme_toggle_updates_ui(app, tmp_path, monkeypatch):
    monkeypatch.setenv("CAELAB_UI_SETTINGS_PATH", str(tmp_path / "ui.ini"))
    from apps.workbench_qt.icons import icon
    from apps.workbench_qt.theme import current_theme_mode, get_palette, set_theme_mode

    try:
        set_theme_mode("dark")
        window = MainWindow()
        window.set_model(cantilever_model())

        dark_p = get_palette("dark")
        assert current_theme_mode() == "dark"
        assert window.theme_action.text() == "라이트 모드"
        assert dark_p["window_bg"] in window.styleSheet()
        assert f"color: {dark_p['text_secondary']};" in window.viewport._fallback_label.styleSheet()
        assert window._toolbar_icon_actions[0][0].icon().cacheKey() == icon("folder", "dark").cacheKey()

        # Switch to light mode via menu action trigger
        window.theme_action.trigger()

        light_p = get_palette("light")
        assert current_theme_mode() == "light"
        assert window.theme_action.text() == "다크 모드"
        assert light_p["window_bg"] in window.styleSheet()
        assert f"color: {light_p['text_secondary']};" in window.viewport._fallback_label.styleSheet()
        assert window._toolbar_icon_actions[0][0].icon().cacheKey() == icon("folder", "light").cacheKey()

        # Switch back to dark mode
        window.theme_action.trigger()

        assert current_theme_mode() == "dark"
        assert window.theme_action.text() == "라이트 모드"
        assert dark_p["window_bg"] in window.styleSheet()
        assert f"color: {dark_p['text_secondary']};" in window.viewport._fallback_label.styleSheet()
        assert window._toolbar_icon_actions[0][0].icon().cacheKey() == icon("folder", "dark").cacheKey()
        window.close()
    finally:
        set_theme_mode("dark")


def _property_texts(window, path):
    from PySide6.QtWidgets import QLabel

    assert window.select_path(path)
    return [label.text() for label in window.props_panel.findChildren(QLabel)]


@pytest.mark.parametrize('omit_unit_key', [False, True])
def test_auxiliary_si_units_follow_the_model_default(app, omit_unit_key):
    from PySide6.QtWidgets import QLabel
    from caelab.model.schema import StructuralModel

    data = cantilever_model().to_dict()
    if omit_unit_key:
        data.pop('unit_system')
    model = StructuralModel.from_dict(data)
    before = model.to_dict()
    window = MainWindow()
    try:
        window.set_model(model)
        assert 'SI (m, kg, s, Pa)' in _property_texts(window, 'model')
        assert any('단위계: SI' in label.text() for label in window.statusBar().findChildren(QLabel))
        materials = _property_texts(window, 'materials[0]')
        assert '210.00 GPa' in materials
        assert '7,850 kg/m³' in materials
        assert '-1,000.0 N' in _property_texts(window, 'loads[0]')
        assert model.to_dict() == before
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize(('declaration', 'visible_declaration'), [
    ('SI', 'SI'), (' si ', ' si '), ('mm-ton-s', 'mm-ton-s'), ('', '미선언'), (None, '미선언'),
])
def test_auxiliary_unknown_units_preserve_declared_values(app, declaration, visible_declaration):
    from PySide6.QtWidgets import QLabel

    model = cantilever_model()
    model.unit_system = declaration
    model.materials[0].params['youngs_modulus'] = 210000
    model.materials[0].params['density'] = 7.8e-9
    model.loads[0].values['fy'] = 1e-6
    before = model.to_dict()
    window = MainWindow()
    try:
        window.set_model(model)
        materials = _property_texts(window, 'materials[0]')
        assert '210000 (단위 미확인)' in materials
        assert '7.8e-09 (단위 미확인)' in materials
        assert '0.300' in materials
        assert '1e-06 (단위 미확인)' in _property_texts(window, 'loads[0]')
        description = next(text for text in _property_texts(window, 'model') if '단위 미확인' in text)
        assert visible_declaration in description
        assert any(description in label.text() for label in window.statusBar().findChildren(QLabel))
        assert model.to_dict() == before
    finally:
        window.close()
        app.processEvents()


def test_auxiliary_units_refresh_when_the_model_changes(app):
    from PySide6.QtWidgets import QLabel

    window = MainWindow()
    try:
        assert any('모델 없음' in label.text() for label in window.statusBar().findChildren(QLabel))
        first, unknown, final = (cantilever_model() for _ in range(3))
        unknown.unit_system = ''
        for model in (first, unknown, final):
            window.set_model(model)
            description = _property_texts(window, 'model')
            assert any(text in label.text() for text in description if text.startswith(('SI (', '미선언'))
                       for label in window.statusBar().findChildren(QLabel))
        assert '210.00 GPa' in _property_texts(window, 'materials[0]')
    finally:
        window.close()
        app.processEvents()

