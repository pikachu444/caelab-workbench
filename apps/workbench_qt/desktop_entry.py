"""Dispatch desktop tools and the Windows solver gate from one executable."""

from __future__ import annotations

from importlib import import_module
import os
from pathlib import Path
import sys


_MODULES = {
    "apps.workbench_qt": "apps.workbench_qt.__main__",
    "apps.workbench_qt.native_analysis": "apps.workbench_qt.native_analysis",
    "apps.workbench_qt.expert_window": "apps.workbench_qt.expert_window",
    "apps.workbench_qt.material_study": "apps.workbench_qt.material_study",
    "apps.workbench_qt.mdo_studio": "apps.workbench_qt.mdo_studio.__main__",
}


def _registry_candidates() -> tuple[Path, Path]:
    name = "caelab-install-registry.json"
    return Path(sys.executable).resolve().parent / name, Path(__file__).resolve().parents[2] / name


def default_install_registry_path() -> Path:
    executable, repository = _registry_candidates()
    return executable if getattr(sys, "frozen", False) else repository


def configure_install_registry() -> None:
    if not os.environ.get("CAELAB_INSTALL_REGISTRY"):
        for candidate in _registry_candidates():
            if candidate.is_file():
                os.environ["CAELAB_INSTALL_REGISTRY"] = str(candidate)
                break


def main(argv: list[str] | None = None) -> int:
    configure_install_registry()
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--caelab-diagnostics":
        return import_module("apps.workbench_qt.bundle_diagnostics").main(args[1:])
    if args and args[0] == "--caelab-solver-gate":
        return import_module("caelab.native.windows_gate").main(args[1:])

    module = "apps.workbench_qt.__main__"
    if args and args[0] == "--caelab-module":
        if len(args) < 2:
            print("CAELab: --caelab-module requires a desktop module name.", file=sys.stderr)
            return 2
        module = _MODULES.get(args[1])
        if module is None:
            print(f"CAELab: unsupported desktop module: {args[1]}", file=sys.stderr)
            return 2
        args = args[2:]
    return import_module(module).main(args)


if __name__ == "__main__":
    raise SystemExit(main())
