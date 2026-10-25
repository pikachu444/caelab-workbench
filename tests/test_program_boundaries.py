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

def test_empty_project_and_expert_never_start_analysis_solver_or_llm(app, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Opening a project or expert must not run analysis, solver or LLM")
    workbench = Workbench()
    monkeypatch.setattr(workbench, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setitem(sys.modules, "apps.workbench_qt.main_window", None)
    monkeypatch.setitem(sys.modules, "caelab.assist.experts", None)
    window = ProjectWindow(workbench)
    assert window.boxes == {}
    box = window.add_box("expert")
    assert window.save_project(tmp_path / "연구.json")
    launches = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs:
                        launches.append((command, kwargs)) or SimpleNamespace(pid=732))
    windows = set(app.topLevelWidgets())
    event = SimpleNamespace(scenePos=lambda: box.pos() + QPointF(20, 20))
    window._scene_double_click(event)
    assert set(app.topLevelWidgets()) == windows
    assert len(launches) == 1
    command, options = launches[0]
    assert command[:3] == [sys.executable, "-m", "apps.workbench_qt.expert_window"]
    assert command[command.index("--node") + 1] == box.node_id
    assert "--discussion" in command and "--project" in command
    assert "cwd" in options and "env" in options
    assert box.state != "done"
    assert "열기 요청" in window.status.text()
    assert not hasattr(window, "_workspace")
    window.run_chain()
    assert "CalculiX 상자를 하나 선택" in window.status.text()
    assert len(launches) == 1
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
    assert window.open_button.text() == '해석창 열기'
    window.open_button.click()
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:3] == [sys.executable, "-m", "apps.workbench_qt.native_analysis"]
    assert str(project_path) in command
    assert command[command.index("--node") + 1] == box.node_id
    assert "cwd" in kwargs and "env" in kwargs
    assert not hasattr(window, "_workspace")
    assert "열기 요청" in window.status.text()
    window.close()


def test_context_open_command_matches_selected_object_without_duplicate_analysis_toolbar(app):
    window = ProjectWindow()
    try:
        geometry = window.add_box('geometry')
        assert geometry.isSelected() and window.open_button.text() == '입력 파일 지정…'
        analysis = window.add_box('analysis')
        assert analysis.isSelected() and window.open_button.text() == '해석창 열기'
        expert = window.add_box('expert')
        assert expert.isSelected() and window.open_button.text() == '전문가 대화 열기'
        analysis.setSelected(True)
        assert window.open_button.isHidden()
        window.scene.clearSelection()
        assert window.open_button.isHidden()
        assert not hasattr(window, 'analysis_action')
    finally:
        window._dirty = False
        window.close()

@pytest.mark.parametrize("kind", ["analysis", "expert"])
def test_unsaved_independent_window_open_can_be_cancelled(app, monkeypatch, kind):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Cancel)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("cancelled open launched"))
    window = ProjectWindow()
    box = window.add_box(kind)
    project = window.project
    window.open_box(box)
    assert window.project is project
    assert window.project.path is None
    assert window._dirty
    window._dirty = False
    window.close()
