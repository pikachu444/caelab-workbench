"""Dependency diagnostics use Qt offscreen; launcher tests never install packages."""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
VIEWPORT = REPO / "apps" / "workbench_qt" / "viewport.py"
LAUNCHER = REPO / "apps" / "workbench_qt" / "run_workbench.bat"


def test_declared_extras_cover_gui_numerical_and_pdf_rag():
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["dependencies"] == ["numpy>=1.26"]
    extras = project["optional-dependencies"]
    assert set(extras["gui"]) == {"PySide6>=6.6", "vtk>=9.3", "scipy>=1.15"}
    assert extras["numerical"] == ["scipy>=1.15"]
    assert extras["rag"] == ["pypdf>=4"]
    assert extras["sensitivity"] == ["SALib>=1.4"]


@pytest.fixture
def qt_app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    return widgets.QApplication.instance() or widgets.QApplication([])


def load_viewport():
    # Exercise the owned widget independently of main-window/panel lifecycle.
    spec = importlib.util.spec_from_file_location("dependency_viewport", VIEWPORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def viewport_message(widget):
    from PySide6.QtWidgets import QLabel
    return "\n".join(label.text() for label in widget.findChildren(QLabel))


def test_missing_vtk_is_reported_before_offscreen(qt_app, monkeypatch):
    monkeypatch.setitem(sys.modules, "vtk", None)
    module = load_viewport()
    widget = module.Viewport()
    assert not widget.available
    assert "VTK가 설치되어 있지 않습니다" in viewport_message(widget)
    assert "헤드리스" not in viewport_message(widget)
    assert ".[gui]" in widget.unavailable_reason


def test_vtk_import_failure_is_reported_as_import_failure(qt_app, monkeypatch):
    import builtins
    original = builtins.__import__
    def broken_import(name, *args, **kwargs):
        if name == "vtk":
            raise ImportError("native dependency unavailable")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", broken_import)
    widget = load_viewport().Viewport()
    assert "VTK 모듈을 불러오지 못했습니다" in viewport_message(widget)
    assert "native dependency unavailable" in viewport_message(widget)


def test_offscreen_reason_does_not_claim_vtk_is_missing(qt_app, monkeypatch):
    module = load_viewport()
    monkeypatch.setattr(module, "vtk", SimpleNamespace())
    monkeypatch.setattr(module.Viewport, "_init_vtk", lambda self: pytest.fail("offscreen must not initialize VTK"))
    widget = module.Viewport()
    assert not widget.available
    assert "QT_QPA_PLATFORM=offscreen" in viewport_message(widget)
    assert "설치되어 있지 않습니다" not in viewport_message(widget)


def test_installed_vtk_uses_offscreen_reason(qt_app):
    pytest.importorskip("vtk", reason="Requires the declared gui VTK dependency")
    module = load_viewport()
    assert module.vtk is not None, module._VTK_IMPORT_ERROR
    widget = module.Viewport()
    assert not widget.available
    assert "QT_QPA_PLATFORM=offscreen" in viewport_message(widget)


def test_vtk_initialization_failure_removes_partial_widget(qt_app, monkeypatch):
    from PySide6.QtWidgets import QWidget
    module = load_viewport()
    monkeypatch.setattr(module, "vtk", SimpleNamespace())
    monkeypatch.setenv("QT_QPA_PLATFORM", "diagnostic-test")
    def fail_initialization(self):
        self._vtk_widget = QWidget(self)
        self._layout.addWidget(self._vtk_widget)
        raise RuntimeError("render context failed")
    monkeypatch.setattr(module.Viewport, "_init_vtk", fail_initialization)
    widget = module.Viewport()
    assert not widget.available
    assert "VTK 3D 화면을 초기화하지 못했습니다" in viewport_message(widget)
    assert "render context failed" in viewport_message(widget)
    assert widget._vtk_widget is None
    assert widget._layout.count() == 1


def prepare_launcher_project(tmp_path, existing_venv):
    app = tmp_path / "apps" / "workbench_qt"
    app.mkdir(parents=True)
    (tmp_path / "apps" / "__init__.py").write_text("")
    (app / "__init__.py").write_text("")
    (app / "__main__.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "Path('app-called').write_text('yes')\n"
        "raise SystemExit(int(os.environ.get('TEST_APP_EXIT', '0')))\n"
    )
    (tmp_path / "pip.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "with Path('pip-calls').open('a') as log: log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "raise SystemExit(int(os.environ.get('TEST_PIP_EXIT', '0')))\n"
    )
    launcher = app / "run_workbench.bat"
    shutil.copyfile(LAUNCHER, launcher)
    if existing_venv:
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(tmp_path / ".venv")], check=True, capture_output=True)
    return launcher


@pytest.mark.skipif(sys.platform != "win32", reason="Exercises the Windows batch launcher with cmd.exe")
@pytest.mark.parametrize("existing_venv", [False, True])
def test_launcher_syncs_declared_extras_on_every_run(tmp_path, existing_venv):
    import json
    launcher = prepare_launcher_project(tmp_path, existing_venv)
    for _ in range(2):
        process = subprocess.run(["cmd.exe", "/d", "/c", str(launcher)], cwd=tmp_path,
                                 input="\n", text=True, capture_output=True, timeout=90)
        assert process.returncode == 0, process.stdout + process.stderr
    assert (tmp_path / "app-called").exists()
    calls = [json.loads(line) for line in (tmp_path / "pip-calls").read_text().splitlines()]
    assert calls == [["install", "-e", ".[gui,rag]"]] * 2


@pytest.mark.skipif(sys.platform != "win32", reason="Exercises the Windows batch launcher with cmd.exe")
@pytest.mark.parametrize("failure", ["PIP", "APP"])
def test_launcher_stops_and_returns_failure(tmp_path, failure):
    launcher = prepare_launcher_project(tmp_path, existing_venv=True)
    environment = dict(os.environ, **{f"TEST_{failure}_EXIT": "7"})
    process = subprocess.run(["cmd.exe", "/d", "/c", str(launcher)], cwd=tmp_path, env=environment,
                             input="\n", text=True, capture_output=True, timeout=90)
    assert process.returncode == 1, process.stdout + process.stderr
    assert (tmp_path / "app-called").exists() is (failure == "APP")
