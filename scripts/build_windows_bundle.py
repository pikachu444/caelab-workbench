"""Build a local Windows candidate into a new directory, without installing anything."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tomllib


SOURCE = Path(__file__).resolve().parents[1]
APP_NAME = "CAELabWorkbench"
DISTRIBUTIONS = (
    "PyInstaller", "pyinstaller-hooks-contrib", "numpy", "filelock", "PySide6",
    "shiboken6", "vtk", "scipy", "matplotlib", "pypdf", "scikit-learn", "pymoo", "SALib",
)


def _file_record(path: Path, root: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
            "sha256": digest.hexdigest()}


def _source_inventory() -> list[dict]:
    paths = {SOURCE / "pyproject.toml", SOURCE / "scripts/workbench_bundle.spec",
             Path(__file__).resolve(), SOURCE / "scripts/pyi_runtime_hook.py",
             SOURCE / "caelab/solvers/installer/manifest.json"}
    for package in ("apps", "caelab"):
        paths.update((SOURCE / package).rglob("*.py"))
    paths.update((SOURCE / "apps/workbench_qt/icons").glob("*.svg"))
    return [_file_record(path, SOURCE) for path in sorted(paths)]


def _static_checks(bundle: Path) -> dict[str, bool]:
    internal = bundle / "_internal"
    dlls = [path for path in bundle.rglob("*") if path.is_file() and path.suffix.lower() == ".dll"]
    return {
        "executable": (bundle / f"{APP_NAME}.exe").is_file(),
        "native_dlls": bool(dlls),
        "python_runtime": any(path.name.lower().startswith("python3") for path in dlls),
        "qt_core": any(path.name.lower() == "qt6core.dll" for path in dlls),
        "qt_windows_platform": (internal / "PySide6/plugins/platforms/qwindows.dll").is_file(),
        "qt_svg_library": (internal / "PySide6/Qt6Svg.dll").is_file(),
        "qt_svg_icon_plugin": (internal / "PySide6/plugins/iconengines/qsvgicon.dll").is_file(),
        "qt_svg_image_plugin": (internal / "PySide6/plugins/imageformats/qsvg.dll").is_file(),
        "vtk_core_extension": any(path.is_file() for path in (internal / "vtkmodules").glob("vtkCommonCore*.pyd")),
        "vtk_opengl_extension": any(path.is_file() for path in (internal / "vtkmodules").glob("vtkRenderingOpenGL2*.pyd")),
        "solver_manifest": (internal / "caelab/solvers/installer/manifest.json").is_file(),
        "svg_icons": all((internal / "apps/workbench_qt/icons" / path.name).is_file()
                         for path in (SOURCE / "apps/workbench_qt/icons").glob("*.svg")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path,
                        help="New output directory outside the source tree; it must not already exist")
    parser.add_argument("--base-revision", required=True,
                        help="Caller-supplied full 40-character baseline Git revision, not this candidate's SHA")
    parser.add_argument("--source-revision", required=True,
                        help="Full Git revision exported into this source directory before building")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("Windows bundles must be built on Windows.")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.base_revision):
        parser.error("--base-revision must be a full 40-character Git revision.")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.source_revision):
        parser.error("--source-revision must be a full 40-character Git revision.")
    output = args.output.expanduser().resolve()
    if output.is_relative_to(SOURCE) or output.exists():
        parser.error("--output must be a new directory outside the source tree; existing paths are never replaced.")

    required = [SOURCE / relative for relative in (
        "apps/workbench_qt/desktop_entry.py", "caelab/native/windows_gate.py",
        "caelab/process_entry.py", "scripts/pyi_runtime_hook.py", "scripts/workbench_bundle.spec",
        "caelab/solvers/installer/manifest.json", "apps/workbench_qt/icons/folder.svg",
    )]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        parser.error("Required source files are missing: " + ", ".join(missing))
    versions = {}
    for distribution in DISTRIBUTIONS:
        try:
            versions[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            parser.error(f"Missing build dependency: {distribution}. This script does not install packages.")
    source_files = _source_inventory()
    project = tomllib.loads((SOURCE / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    output.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, "-B", "-m", "PyInstaller", "--distpath", str(output / "dist"),
               "--workpath", str(output / "build"), str(SOURCE / "scripts/workbench_bundle.spec")]
    manifest = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "building",
        "source_kind": "local_candidate_copy",
        "source_root": str(SOURCE),
        "source_revision": args.source_revision.lower(),
        "source_revision_note": "Caller-supplied export revision; source hashes are checked again after the build.",
        "base_revision": args.base_revision.lower(),
        "base_revision_note": "Caller-supplied baseline; source file hashes identify the local candidate.",
        "source_files": source_files,
        "project": {"name": project["name"], "version": project["version"]},
        "builder": {"python": sys.version, "executable": sys.executable,
                    "platform": platform.platform(), "distributions": versions},
        "command": command,
        "standalone_runtime_validation": "not_performed",
    }
    manifest_path = output / "build-manifest.json"

    def save_manifest():
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    save_manifest()
    environment = dict(os.environ)
    environment.update(PYINSTALLER_CONFIG_DIR=str(output / "pyinstaller-cache"),
                       MPLCONFIGDIR=str(output / "matplotlib-cache"),
                       PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    print(f"Building local candidate. Log: {output / 'build.log'}", flush=True)
    try:
        with (output / "build.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=SOURCE, env=environment, stdout=log,
                                    stderr=subprocess.STDOUT, check=False)
        manifest["build_exit_code"] = result.returncode
        if result.returncode:
            manifest["status"] = "build_failed"
            save_manifest()
            return result.returncode
        source_after = _source_inventory()
        if source_after != source_files:
            manifest.update(status="source_changed_during_build", source_files_after=source_after)
            save_manifest()
            print("Source changed during the build. Use a new output directory after freezing the candidate.",
                  file=sys.stderr)
            return 1
        bundle = output / "dist" / APP_NAME
        inventory = [_file_record(path, bundle) for path in sorted(bundle.rglob("*")) if path.is_file()]
        checks = _static_checks(bundle)
        manifest.update(status="built_static_only" if all(checks.values()) else "static_checks_failed",
                        bundle_root=str(bundle), static_checks=checks, inventory=inventory,
                        bundle_file_count=len(inventory), bundle_bytes=sum(item["bytes"] for item in inventory))
        save_manifest()
        print(f"Static bundle manifest: {manifest_path}", flush=True)
        return 0 if all(checks.values()) else 1
    except (OSError, ValueError) as error:
        manifest.update(status="build_error", error=str(error))
        save_manifest()
        print(f"Bundle build failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
