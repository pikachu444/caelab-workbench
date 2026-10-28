"""Independent Studio launch contracts; native UI is checked separately."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
import sys
import pytest
from PySide6.QtWidgets import QApplication

from caelab.connections import mdo_launch_request
from caelab.workbench import Workbench


def test_studio_launch_arguments_keep_spaces_and_independent_entrypoint(tmp_path):
    study = tmp_path / "연구 one.json"
    project = tmp_path / "project two.json"
    study.write_text("{}")
    project.write_text("{}")
    request = mdo_launch_request(study, project, "node-17")
    assert request.argv == [sys.executable, "-m", "apps.workbench_qt.mdo_studio",
                            "--study", str(study), "--project", str(project), "--node", "node-17"]
    assert request.program_id == "mdo_studio"
    with pytest.raises(ValueError):
        mdo_launch_request(project_file=project)
    with pytest.raises(ValueError):
        mdo_launch_request(study_file=tmp_path / "missing.json")


def test_auxiliary_window_launches_studio_without_embedding(monkeypatch):
    from apps.workbench_qt.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    wb = Workbench()
    calls = []
    monkeypatch.setattr(wb, "launch_mdo_studio", lambda **kwargs: calls.append(kwargs) or SimpleNamespace(state="opened"))
    window = MainWindow(wb)
    window.open_optimization_panel()
    assert calls == [{}]
    assert not hasattr(window, "_mdo_studio_window")
    assert not hasattr(window, "optimization_panel")
    window.close()
    app.processEvents()


def test_parent_passes_selected_saved_project_without_owning_studio(tmp_path, monkeypatch):
    from apps.workbench_qt.project_window import ProjectWindow
    app = QApplication.instance() or QApplication([])
    window = ProjectWindow()
    node = window.project.add_node("analysis", "calculix")
    window.workbench.save_native_project(window.project, tmp_path / "project.json")
    window._show_project()
    window.boxes[node.id].setSelected(True)
    calls = []
    monkeypatch.setattr(window.workbench, "launch_mdo_studio", lambda **kwargs: calls.append(kwargs) or SimpleNamespace(state="opened"))
    window.open_optimization_panel()
    assert calls == [{"project": window.project, "node_id": node.id}]
    assert not hasattr(window, "_optimization_panel")
    window._dirty = False
    window.close()
    app.processEvents()


def test_project_writers_lock_and_reject_stale_revision(tmp_path):
    import threading
    from filelock import FileLock, Timeout
    from caelab.project import NativeProject, save_native_project, load_native_project, file_revision
    path = tmp_path / "shared.json"
    save_native_project(NativeProject("Original"), path)
    parent = load_native_project(path)
    child = load_native_project(path)
    original_revision = file_revision(path)
    parent.name, child.name = "Pending parent edits", "Saved child study"
    entered = threading.Event()
    release = threading.Event()

    def hold_project_lock():
        with FileLock(str(path) + ".lock"):
            entered.set()
            assert release.wait(5)
    thread = threading.Thread(target=hold_project_lock)
    thread.start()
    assert entered.wait(5)
    try:
        with pytest.raises(Timeout):
            save_native_project(parent, expected_revision=original_revision)
        assert file_revision(path) == original_revision
    finally:
        release.set()
        thread.join(5)
    save_native_project(child, expected_revision=original_revision)
    with pytest.raises(ValueError, match="다른 창"):
        save_native_project(parent, expected_revision=original_revision)
    assert parent.name == "Pending parent edits"
    assert load_native_project(path).name == "Saved child study"
