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
