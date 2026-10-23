"""Independent program entry points, exercised with Qt offscreen."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import subprocess
import sys
import urllib.request
from types import SimpleNamespace
import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QMessageBox
from apps.workbench_qt.project_window import ProjectWindow
from caelab.workbench import Workbench

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])

def test_empty_project_and_expert_never_start_analysis_solver_or_llm(app, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Opening a project or the unimplemented expert must not run another program")
    workbench = Workbench()
    monkeypatch.setattr(workbench, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setitem(sys.modules, "apps.workbench_qt.main_window", None)
    monkeypatch.setitem(sys.modules, "caelab.assist.experts", None)
    window = ProjectWindow(workbench)
    assert window.boxes == {}
    box = window.add_box("expert")
    windows = set(app.topLevelWidgets())
    event = SimpleNamespace(scenePos=lambda: box.pos() + QPointF(20, 20))
    window._scene_double_click(event)
    assert set(app.topLevelWidgets()) == windows
    assert box.state == "unavailable"
    assert "독립 전문가 프로그램 연결은 아직 구현되지 않았습니다" in window.status.text()
    assert not hasattr(window, "_workspace")
    window.run_chain()
    assert "CalculiX 상자를 하나 선택" in window.status.text()
    assert box.state == "unavailable"
    window._dirty = False
    window.close()

def test_analysis_opens_independent_process_with_project_and_node(app, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs:
                        calls.append((command, kwargs)) or SimpleNamespace(pid=731))
    monkeypatch.setitem(sys.modules, "apps.workbench_qt.main_window", None)
    monkeypatch.setitem(sys.modules, "caelab.assist.experts", None)
    window = ProjectWindow()
    box = window.add_box("analysis")
    deck = tmp_path / "사용자 덱.inp"
    deck.write_text("*HEADING\nuser input\n", encoding="utf-8")
    assert window.register_file(box.node_id, deck)
    project_path = tmp_path / "연구 프로젝트.json"
    assert window.save_project(project_path)
    window.open_box(box)
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:3] == [sys.executable, "-m", "apps.workbench_qt.native_analysis"]
    assert str(project_path) in command
    assert command[command.index("--node") + 1] == box.node_id
    assert "cwd" in kwargs and "env" in kwargs
    assert not hasattr(window, "_workspace")
    assert "열기 요청" in window.status.text()
    window.close()

def test_unsaved_analysis_open_can_be_cancelled(app, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Cancel)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("cancelled open launched"))
    window = ProjectWindow()
    box = window.add_box("analysis")
    project = window.project
    window.open_box(box)
    assert window.project is project
    assert window.project.path is None
    assert window._dirty
    window._dirty = False
    window.close()
