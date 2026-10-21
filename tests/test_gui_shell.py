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


def test_expert_panel_evidence_and_proposal_apply(app):
    from caelab.assist.experts import Proposal
    from caelab.solvers.base import ResultSet

    window = MainWindow()
    model = cantilever_model()
    window.set_model(model)
    # no result yet: the panel must say so, not invent a discussion
    window.expert_panel.refresh_evidence()
    assert "아직 계산 결과가 없습니다" in window.expert_panel.evidence_label.text()

    window._last_result = ResultSet(
        solver_id="calculix", success=True, max_displacement=0.0002,
        reaction_totals={"fx": 0.0, "fy": 1000.0, "fz": 0.0},
    )
    window.expert_panel.refresh_evidence()
    assert window.expert_panel.discussion is not None
    assert "1,000" in window.expert_panel.evidence_label.text()

    # proposal application is a user action and checks the before value
    applied = window._apply_proposal(Proposal("loads[0].values.fy", -1000.0, -500.0))
    assert "적용했습니다" in applied
    assert model.loads[0].values["fy"] == -500.0
    stale = window._apply_proposal(Proposal("loads[0].values.fy", -1000.0, -250.0))
    assert "적용하지 않았습니다" in stale
    assert model.loads[0].values["fy"] == -500.0


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
