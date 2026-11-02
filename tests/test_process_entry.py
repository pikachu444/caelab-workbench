"""Source/frozen command wiring and bundle environment boundaries, without GUI."""

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from caelab import process_entry


def test_frozen_analysis_request_reaches_the_same_frontend(tmp_path, monkeypatch):
    from caelab.connections import analysis_launch_request
    from apps.workbench_qt import desktop_entry

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("PYINSTALLER_RESET_ENVIRONMENT", raising=False)
    project = tmp_path / "한글 연구 file.json"
    project.write_text("{}", encoding="utf-8")
    received = []
    monkeypatch.setattr(desktop_entry, "import_module", lambda name: SimpleNamespace(
        main=lambda args: received.append((name, args)) or 17))
    request = analysis_launch_request(project, "node-1")
    assert desktop_entry.main(request.argv[1:]) == 17
    assert received == [("apps.workbench_qt.native_analysis", [str(project), "--node", "node-1"])]
    assert request.env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert "PYINSTALLER_RESET_ENVIRONMENT" not in os.environ


def test_external_environment_preserves_caller_solver_and_sibling_paths(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", r"C:\Bundle", raising=False)
    monkeypatch.setattr(process_entry, "os", SimpleNamespace(name="nt", pathsep=";", environ={}))
    environment = {
        "PATH": r"D:\Solver Bin;C:\BUNDLE;C:\bundle\bin;C:\BundleSibling;D:\User",
        "QT_PLUGIN_PATH": r"C:\Bundle\PySide6\plugins",
        "QT_QPA_PLATFORM_PLUGIN_PATH": r"C:\Bundle\PySide6\plugins\platforms",
        "QML2_IMPORT_PATH": r"C:\Bundle\qml",
        "CAELAB_BUNDLE_QT_ORIGINAL": json.dumps({"QT_PLUGIN_PATH": r"D:\User Qt",
            "QT_QPA_PLATFORM_PLUGIN_PATH": None, "QML2_IMPORT_PATH": ""}),
    }
    before = dict(environment)
    clean = process_entry.external_environment(environment)
    assert clean["PATH"] == r"D:\Solver Bin;C:\BundleSibling;D:\User"
    assert clean["QT_PLUGIN_PATH"] == r"D:\User Qt"
    assert "QT_QPA_PLATFORM_PLUGIN_PATH" not in clean and clean["QML2_IMPORT_PATH"] == ""
    assert "CAELAB_BUNDLE_QT_ORIGINAL" not in clean and environment == before


def test_pre_hook_snapshot_survives_internal_relaunch(monkeypatch):
    environment = {"QT_PLUGIN_PATH": "original plugins", "QML2_IMPORT_PATH": ""}
    monkeypatch.setattr(process_entry, "os", SimpleNamespace(environ=environment))
    process_entry.remember_caller_qt_environment()
    saved = environment["CAELAB_BUNDLE_QT_ORIGINAL"]
    assert json.loads(saved) == {"QT_PLUGIN_PATH": "original plugins",
                               "QT_QPA_PLATFORM_PLUGIN_PATH": None, "QML2_IMPORT_PATH": ""}
    environment["QT_PLUGIN_PATH"] = "bundle plugins"
    process_entry.remember_caller_qt_environment()
    assert environment["CAELAB_BUNDLE_QT_ORIGINAL"] == saved


def test_actual_gate_import_and_invalid_payload_need_no_qt():
    code = """
import sys
from caelab.native.windows_gate import main
assert main(['-', '{}', '-']) == 2
assert main([]) == 2
assert not any(name == 'PySide6' or name.startswith('PySide6.') for name in sys.modules)
"""
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                       PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-B", "-c", code], env=environment,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_gate_valid_command_preserves_log_environment_and_exit_code(tmp_path, monkeypatch):
    from caelab.native import windows_gate

    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("CAELAB_GATE_TEST_VALUE", "caller-value")
    before = dict(os.environ)
    log = tmp_path / "한글 공백 solver.log"
    log.write_bytes(b"existing log\n")
    command = ["solver executable", "-i", "model with spaces"]
    received = {}

    def start(argv, **kwargs):
        received.update(argv=argv, **kwargs)
        kwargs["stdout"].write(b"child output\n")
        return SimpleNamespace(wait=lambda: 7)

    monkeypatch.setattr(windows_gate.subprocess, "Popen", start)
    assert windows_gate.main(["-", json.dumps(command), str(log)]) == 7
    assert received["argv"] == command
    assert received["stdin"] is subprocess.DEVNULL
    assert received["stderr"] is subprocess.STDOUT
    assert received["shell"] is False
    assert received["env"]["CAELAB_GATE_TEST_VALUE"] == "caller-value"
    assert received["stdout"].closed
    assert log.read_bytes() == b"existing log\nchild output\n"
    assert dict(os.environ) == before


@pytest.mark.parametrize('frozen', [False, True])
def test_desktop_settings_keep_the_caller_path_when_child_cwd_changes(tmp_path, monkeypatch, frozen):
    from caelab.connections import mdo_launch_request
    from apps.workbench_qt.mdo_studio.window import _layout_settings

    caller = tmp_path / '한글 caller cwd'
    caller.mkdir()
    (caller / 'relative settings').mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    paths = {
        'CAELAB_UI_SETTINGS_PATH': 'relative settings/theme.ini',
        'CAELAB_MDO_SETTINGS_PATH': 'relative settings/한글 layout.ini',
        'CAELAB_EXPERT_SETTINGS_PATH': 'relative settings/expert.ini',
    }
    for key, value in paths.items():
        monkeypatch.setenv(key, value)
    before = dict(os.environ)
    request = mdo_launch_request()
    assert request.cwd != caller
    for key, value in paths.items():
        assert request.env[key] == str(caller / value)
    assert dict(os.environ) == before

    # Emulate the child's documented cwd/env boundary; no process or window.
    monkeypatch.chdir(request.cwd)
    monkeypatch.setenv('CAELAB_MDO_SETTINGS_PATH', request.env['CAELAB_MDO_SETTINGS_PATH'])
    settings = _layout_settings()
    assert Path(settings.fileName()).resolve() == caller / paths['CAELAB_MDO_SETTINGS_PATH']
    settings.setValue('layout-proof', 17)
    settings.sync()
    assert (caller / paths['CAELAB_MDO_SETTINGS_PATH']).is_file()
    assert _layout_settings().value('layout-proof', type=int) == 17


def test_desktop_environment_keeps_absolute_blank_and_missing_settings(tmp_path, monkeypatch):
    absolute = str(tmp_path / 'absolute settings.ini')
    monkeypatch.setenv('CAELAB_UI_SETTINGS_PATH', '')
    monkeypatch.setenv('CAELAB_MDO_SETTINGS_PATH', absolute)
    monkeypatch.setenv('CAELAB_EXPERT_SETTINGS_PATH', '   ')
    before = dict(os.environ)
    selected = process_entry.desktop_environment()
    assert selected['CAELAB_UI_SETTINGS_PATH'] == ''
    assert selected['CAELAB_MDO_SETTINGS_PATH'] == absolute
    assert selected['CAELAB_EXPERT_SETTINGS_PATH'] == '   '
    assert dict(os.environ) == before
    monkeypatch.delenv('CAELAB_MDO_SETTINGS_PATH')
    assert 'CAELAB_MDO_SETTINGS_PATH' not in process_entry.desktop_environment()
