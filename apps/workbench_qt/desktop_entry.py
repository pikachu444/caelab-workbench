"""Dispatch desktop tools and the Windows solver gate from one executable."""

from __future__ import annotations

from importlib import import_module
import sys


_MODULES = {
    "apps.workbench_qt": "apps.workbench_qt.__main__",
    "apps.workbench_qt.native_analysis": "apps.workbench_qt.native_analysis",
    "apps.workbench_qt.expert_window": "apps.workbench_qt.expert_window",
    "apps.workbench_qt.mdo_studio": "apps.workbench_qt.mdo_studio.__main__",
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
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
