"""Independent program boundaries, exercised with Qt offscreen."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from apps.workbench_qt.project_window import ProjectWindow
from caelab.solvers.base import ResultSet
from caelab.workbench import Workbench


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_expert_box_reports_unimplemented_without_starting_any_program(app, monkeypatch):
    import subprocess
    import urllib.request
    import apps.workbench_qt.main_window as analysis

    def forbidden(*args, **kwargs):
        pytest.fail("Expert box must not launch analysis, solver, external app or LLM")
    workbench = Workbench()
    monkeypatch.setattr(workbench, "run", forbidden)
    monkeypatch.setattr(analysis, "MainWindow", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    project = ProjectWindow(workbench)
    box = project.boxes["expert"]
    assert box.state == "unavailable"
    windows = set(app.topLevelWidgets())
    event = SimpleNamespace(scenePos=lambda: box.pos() + QPointF(20, 20))
    project._scene_double_click(event)
    assert project._workspace is None
    assert set(app.topLevelWidgets()) == windows
    assert box.state == "unavailable"
    assert "독립 전문가 프로그램 연결은 아직 구현되지 않았습니다" in project.status.text()
    assert "미구현" in project.log.toPlainText()
    assert not any(connector.dst is box for connector in project.connectors)


def test_analysis_completion_does_not_make_expert_program_ready(app, monkeypatch):
    workbench = Workbench()
    result = ResultSet("calculix", True, max_displacement=0.001, units={"length": "m"})
    monkeypatch.setattr(workbench, "run", lambda *args: SimpleNamespace(state="succeeded", result=result))
    project = ProjectWindow(workbench)
    project.run_chain()
    assert project.boxes["analysis"].state == "done"
    assert project.boxes["expert"].state == "unavailable"
    assert "전문가 논의" not in project.status.text()
    assert project._workspace is None


def test_analysis_box_opens_analysis_workspace_without_expert(app):
    project = ProjectWindow()
    project.open_box(project.boxes["analysis"])
    assert project._workspace is not None
    assert project._workspace.model is project.model
    assert not hasattr(project._workspace, "expert_panel")
    project._workspace.close()
