"""Commands and child environments for source and Windows onedir execution."""

from __future__ import annotations

import json
import ntpath
import os
from pathlib import Path
import sys

_QT_ORIGINAL = "CAELAB_BUNDLE_QT_ORIGINAL"
_QT_PATHS = ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH")


def module_command(module: str, *args: str) -> list[str]:
    flag = "--caelab-module" if getattr(sys, "frozen", False) else "-m"
    return [sys.executable, flag, module, *map(str, args)]


def desktop_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for key in ("CAELAB_UI_SETTINGS_PATH", "CAELAB_MDO_SETTINGS_PATH", "CAELAB_EXPERT_SETTINGS_PATH"):
        value = environment.get(key, "").strip()
        if value and not Path(value).is_absolute():
            environment[key] = str(Path(value).absolute())
    if getattr(sys, "frozen", False):
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment


def windows_gate_command(gate: Path | None, argv: list[str], log: Path | None) -> list[str]:
    args = [str(gate) if gate is not None else "-", json.dumps(argv, ensure_ascii=False),
            str(log) if log is not None else "-"]
    if getattr(sys, "frozen", False):
        return [sys.executable, "--caelab-solver-gate", *args]
    return [sys.executable, str(Path(__file__).resolve().parent / "native/windows_gate.py"), *args]


def external_command(argv: list[str]) -> list[str]:
    if not getattr(sys, "frozen", False) or os.name != "nt":
        return argv
    if argv[:2] == [sys.executable, "--caelab-module"]:
        return argv
    return windows_gate_command(None, argv, None)


def remember_caller_qt_environment() -> None:
    """Run before PyInstaller's Qt hook; preserve missing and empty values."""
    if _QT_ORIGINAL not in os.environ:
        os.environ[_QT_ORIGINAL] = json.dumps({key: os.environ.get(key) for key in _QT_PATHS})


def _inside_bundle(path: str, bundle: str) -> bool:
    path = ntpath.normcase(ntpath.normpath(path.strip('"')))
    bundle = ntpath.normcase(ntpath.normpath(bundle))
    if not ntpath.isabs(path):
        return False
    try:
        return ntpath.commonpath((path, bundle)) == bundle
    except ValueError:
        return False


def external_environment(environment: dict[str, str] | None = None) -> dict[str, str]:
    """Remove bundle-derived search paths without changing the parent process."""
    result = dict(os.environ if environment is None else environment)
    if not getattr(sys, "frozen", False) or os.name != "nt":
        return result
    bundle = str(sys._MEIPASS)
    saved = json.loads(result.pop(_QT_ORIGINAL, "{}"))
    if not isinstance(saved, dict) or any(key not in _QT_PATHS or (value is not None and not isinstance(value, str))
                                         for key, value in saved.items()):
        raise ValueError("Invalid bundled Qt environment snapshot")

    def without_bundle(value: str) -> str:
        return os.pathsep.join(part for part in value.split(os.pathsep)
                               if not _inside_bundle(part, bundle))

    if "PATH" in result:
        result["PATH"] = without_bundle(result["PATH"])
    for key in _QT_PATHS:
        current = result.get(key)
        if current is None or not any(_inside_bundle(part, bundle) for part in current.split(os.pathsep)):
            continue
        clean = without_bundle(current)
        original = saved.get(key)
        if clean:
            result[key] = clean
        elif isinstance(original, str):
            result[key] = original
        else:
            result.pop(key, None)
    return result
