import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

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


def test_window_tree_and_check(app):
    window = MainWindow()
    window.set_model(cantilever_model())
    root = window.tree.topLevelItem(0)
    assert root.childCount() == len(TREE_GROUPS)
    window.run_check()
    assert "검사 통과" in window.status.text()
    assert not window.btn_goto.isEnabled()


def test_analysis_runs_without_expert_provider_or_proposal_editor(app, monkeypatch):
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
    calls = []
    def solve(actual_model, solver, workdir):
        calls.append((actual_model, solver))
        return SimpleNamespace(state="succeeded", result=result)
    monkeypatch.setattr(workbench, "run", solve)
    window = MainWindow(workbench)
    window.set_model(model)
    assert calls == []
    window.run_solve()
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
    assert window.optimization_panel is not None


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



def test_actual_calculix_run_is_independent_of_expert_provider(app, monkeypatch):
    import sys
    from caelab.solvers.calculix import find_ccx

    if find_ccx() is None:
        pytest.skip("ccx is not available for the analysis-window run path")
    monkeypatch.setitem(sys.modules, "caelab.assist.experts", None)
    window = MainWindow()
    window.set_model(cantilever_model())
    window.run_solve()
    assert "계산 완료" in window.status.text(), window.log.toPlainText()
    assert window._last_result.success
    assert window._last_result.reaction_totals["fy"] == pytest.approx(1000, rel=0.005)


def test_new_model_clears_previous_result_and_issue_state(app, monkeypatch):
    from types import SimpleNamespace
    from caelab.solvers.base import ResultSet

    window = MainWindow()
    model_a = cantilever_model()
    window.set_model(model_a)
    result = ResultSet("calculix", True, max_displacement=0.25)
    monkeypatch.setattr(window.workbench, "run", lambda *args:
                        SimpleNamespace(state="succeeded", result=result))
    window.run_solve()
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
def test_failed_rerun_removes_previous_success_display(app, monkeypatch, failure_has_result):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QLabel
    from caelab.solvers.base import ResultSet

    window = MainWindow()
    window.set_model(cantilever_model())
    success = ResultSet("calculix", True, max_displacement=0.25, units={"length": "m"})
    failure = ResultSet("calculix", False, log_tail="failed input") if failure_has_result else None
    jobs = iter([SimpleNamespace(state="succeeded", result=success),
                 SimpleNamespace(state="failed", result=failure)])
    monkeypatch.setattr(window.workbench, "run", lambda *args: next(jobs))
    window.run_solve()
    assert window.results_item.childCount() == 1
    window.select_path("result")
    assert any(label.text() == "0.2500 m" for label in window.props_panel.findChildren(QLabel))
    window.run_solve()
    assert window.results_item.childCount() == 0
    assert "result" not in window._path_items
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

