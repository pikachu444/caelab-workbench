"""Desktop dispatch without constructing a GUI or starting a solver."""

import builtins
import importlib
import sys
from types import SimpleNamespace


def test_default_passes_project_arguments_and_return_code(monkeypatch):
    entry = importlib.import_module("apps.workbench_qt.desktop_entry")
    calls = []

    def load(name):
        return SimpleNamespace(main=lambda argv: calls.append((name, argv)) or 17)

    monkeypatch.setattr(entry, "import_module", load)
    args = ["C:/한글 프로젝트/project.json"]
    assert entry.main(args) == 17
    monkeypatch.setattr(sys, "argv", ["Workbench.exe", *args])
    assert entry.main() == 17
    assert calls == [("apps.workbench_qt.__main__", args)] * 2
    assert args == ["C:/한글 프로젝트/project.json"]


def test_mdo_dispatch_preserves_selected_study_and_node(monkeypatch):
    entry = importlib.import_module("apps.workbench_qt.desktop_entry")
    calls = []

    def load(name):
        return SimpleNamespace(main=lambda argv: calls.append((name, argv)) or 0)

    monkeypatch.setattr(entry, "import_module", load)
    args = ["--study", "C:/한글 연구/study.json", "--project", "C:/project.json",
            "--node", "mdo-node", "--read-only"]
    assert entry.main(["--caelab-module", "apps.workbench_qt.mdo_studio", *args]) == 0
    assert calls == [("apps.workbench_qt.mdo_studio.__main__", args)]


def test_missing_and_unapproved_modules_are_rejected_before_import(monkeypatch, capsys):
    entry = importlib.import_module("apps.workbench_qt.desktop_entry")

    def forbidden(name):
        raise AssertionError(f"An invalid request imported {name}")

    monkeypatch.setattr(entry, "import_module", forbidden)
    assert entry.main(["--caelab-module"]) == 2
    assert "requires a desktop module name" in capsys.readouterr().err
    assert entry.main(["--caelab-module", "os"]) == 2
    assert "unsupported desktop module: os" in capsys.readouterr().err


def test_solver_gate_import_and_dispatch_do_not_import_qt(monkeypatch):
    for name in list(sys.modules):
        if name == "apps.workbench_qt" or name.startswith("apps.workbench_qt."):
            monkeypatch.delitem(sys.modules, name)
    real_import = builtins.__import__

    def without_qt(name, *args, **kwargs):
        if name == "PySide6" or name.startswith("PySide6."):
            raise AssertionError(f"Solver gate imported Qt: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_qt)
    entry = importlib.import_module("apps.workbench_qt.desktop_entry")
    calls = []

    def load(name):
        assert name == "caelab.native.windows_gate"
        return SimpleNamespace(main=lambda argv: calls.append(argv) or 125)

    monkeypatch.setattr(entry, "import_module", load)
    args = ["C:/한글 실행/.start-solver", '["C:/solver path/ccx.exe", "-i", "model"]',
            "C:/한글 실행/solver.log"]
    assert entry.main(["--caelab-solver-gate", *args]) == 125
    assert calls == [args]


def test_diagnostics_dispatch_has_one_fixed_target_and_keeps_other_arguments(monkeypatch):
    entry = importlib.import_module('apps.workbench_qt.desktop_entry')
    calls = []
    monkeypatch.setattr(entry, 'import_module', lambda name: SimpleNamespace(
        main=lambda args: calls.append((name, args)) or 1))
    args = ['--output', 'C:/new-diagnostic']
    assert entry.main(['--caelab-diagnostics', *args]) == 1
    assert calls == [('apps.workbench_qt.bundle_diagnostics', args)]
