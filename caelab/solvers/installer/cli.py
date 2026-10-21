"""Command line interface for the CAE Lab install helper.

Usage:
    python -m caelab.solvers.installer list [--platform linux|windows]
    python -m caelab.solvers.installer install --root DIR
        [--only freecad,gmsh] [--system-deps]
    python -m caelab.solvers.installer verify --root DIR
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .engine import Installer, load_manifest


def _cmd_list(args) -> int:
    manifest = load_manifest(args.manifest)
    for prog in manifest["programs"]:
        entry = prog.get("platforms", {}).get(args.platform) or {}
        method = entry.get("method", "-")
        size = entry.get("size")
        size_txt = f"{size:,} B" if size else "-"
        print(f"{prog['id']:<12} {prog['name']:<24} {prog.get('version', '-'):<16} "
              f"{method:<12} {size_txt}")
        if entry.get("status"):
            print(f"{'':<12} status: {entry['status']}")
    return 0


def _cmd_install(args) -> int:
    installer = Installer(Path(args.root), platform=args.platform)
    only = [s.strip() for s in args.only.split(",")] if args.only else None
    results = installer.install(only=only, system_deps=args.system_deps)
    failed = [r for r in results if r.status == "failed"]
    for r in results:
        print(f"{r.program_id:<12} {r.status:<20} {r.detail}")
    print(f"registry: {Path(args.root) / 'caelab-install-registry.json'}")
    return 1 if failed else 0


def _cmd_verify(args) -> int:
    installer = Installer(Path(args.root))
    rows = installer.verify()
    bad = 0
    for pid, ok, line in rows:
        print(f"{pid:<12} {'OK ' if ok else 'FAIL'} {line}")
        bad += 0 if ok else 1
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="caelab-installer",
                                     description="CAE Lab program install helper")
    parser.add_argument("--manifest", type=Path, default=None,
                        help="alternate manifest.json (default: bundled)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list manifest entries")
    p_list.add_argument("--platform", default="linux", choices=["linux", "windows"])
    p_list.set_defaults(func=_cmd_list)

    p_inst = sub.add_parser("install", help="install programs into --root")
    p_inst.add_argument("--root", required=True, type=Path)
    p_inst.add_argument("--platform", default="linux", choices=["linux", "windows"])
    p_inst.add_argument("--only", default=None, help="comma-separated program ids")
    p_inst.add_argument("--system-deps", action="store_true",
                        help="also install listed apt packages (Linux)")
    p_inst.set_defaults(func=_cmd_install)

    p_ver = sub.add_parser("verify", help="probe installed programs")
    p_ver.add_argument("--root", required=True, type=Path)
    p_ver.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
