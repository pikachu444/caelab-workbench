"""Connections to external programs — the workbench's main line.

The workbench does not reimplement preprocessors or postprocessors.
Each solver uses the preprocessor that fits it (FreeCAD FEM for
CalculiX, Salome for Code_Aster, Gmsh for OpenRadioss), and results
are viewed in ParaView. This module is the registry of those programs:
how to find them on the machine, whether they are available, and how
to launch them with a file. The built-in solver converters in
`caelab/solvers` are auxiliary — for paths with no native
preprocessor — not the main road.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
import shutil
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProgramSpec:
    id: str
    name: str
    role: str  # "preprocessor" | "solver" | "postprocessor" | "mesh"
    env_var: str
    executables: tuple[str, ...]
    notes: str = ""


PROGRAMS: tuple[ProgramSpec, ...] = (
    ProgramSpec(
        "freecad", "FreeCAD", "preprocessor", "CAELAB_FREECAD",
        ("freecad", "FreeCAD"),
        "형상 편집과 CalculiX 전처리(FEM)의 본선 프로그램.",
    ),
    ProgramSpec(
        "salome", "Salome", "preprocessor", "CAELAB_SALOME",
        ("salome", "run_salome"),
        "Code_Aster의 표준 전처리기. MED 형식으로 내보낸다.",
    ),
    ProgramSpec(
        "gmsh", "Gmsh", "mesh", "CAELAB_GMSH",
        ("gmsh",),
        "메시 생성. Radioss 블록 형식과 LS-DYNA 키워드 내보내기를 제공한다.",
    ),
    ProgramSpec(
        "calculix", "CalculiX", "solver", "CAELAB_CCX",
        ("ccx",),
        "구조 솔버. 인풋 파일(.inp)은 FreeCAD FEM 작성기가 만드는 것이 본선이다.",
    ),
    ProgramSpec(
        "codeaster", "Code_Aster", "solver", "CAELAB_CODEASTER",
        ("run_aster", "codeaster"),
        "구조 솔버. Salome-Meca 배포에 포함되며, 입력은 MED 메시와 .comm 명령 파일이다.",
    ),
    ProgramSpec(
        "opencourant", "OpenCourant", "solver", "CAELAB_OPENCOURANT",
        ("starter_linux64_gf",),
        "명시적 동해석 솔버 (OpenRadioss의 후계). Radioss 블록 형식과 LS-DYNA 키워드 입력을 읽는다.",
    ),
    ProgramSpec(
        "fenicsx", "FEniCSx", "solver", "CAELAB_FENICSX_PYTHON",
        (),
        "PDE 라인 실행 환경. dolfinx가 든 파이썬을 환경변수로 지정한다. "
        "PDE는 공통 모델 스키마에 넣지 않고 자체 형식을 유지한다 (DESIGN §3.3).",
    ),
    ProgramSpec(
        "paraview", "ParaView", "postprocessor", "CAELAB_PARAVIEW",
        ("paraview",),
        "결과 후처리의 본선 프로그램.",
    ),
)


@dataclass
class ProgramStatus:
    spec: ProgramSpec
    path: str | None = None
    reason: str = ""
    source: str = ""
    runtime_env: dict[str, str] = field(default_factory=dict)
    host_os: str = ""
    execution_environment: str = "native"

    @property
    def available(self) -> bool:
        return self.path is not None and not self.reason


@dataclass(frozen=True)
class LaunchRequest:
    argv: list[str]
    cwd: Path
    env: dict[str, str]
    program_id: str = ""


def host_platform() -> str:
    return "windows" if sys.platform == "win32" else "linux" if sys.platform.startswith("linux") else sys.platform


def _registry(path=None):
    configured = path or os.environ.get("CAELAB_INSTALL_REGISTRY")
    if not configured:
        return {}, ""
    try:
        data = json.loads(Path(configured).expanduser().read_text(encoding="utf-8"))
        if not isinstance(data, dict) or any(not isinstance(data.get(key, {}), dict) for key in ("env", "runtime_env", "programs")):
            raise ValueError("env/runtime_env/programs 사전이 필요합니다.")
        data["_directory"] = Path(configured).expanduser().resolve().parent
        return data, ""
    except (OSError, ValueError) as exc:
        return {}, f"설치 레지스트리를 읽을 수 없습니다: {exc}"


def _registered_path(value: str, registry: dict) -> Path:
    if not value.strip():
        raise ValueError("설치 레지스트리에 빈 상대경로 항목이 있습니다.")
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    root = registry.get("root")
    if not isinstance(root, str) or not root.strip():
        raise ValueError(f"설치 root가 없어 레지스트리 상대경로를 해석할 수 없습니다: {value}")
    root_path = Path(root).expanduser()
    if root_path.is_absolute():
        return root_path / path
    # Installer stores root/executable, not an executable relative to registry.
    # Replace that recorded root prefix with the registry's actual directory.
    prefix = root_path.parts
    if path.parts[:len(prefix)] != prefix:
        raise ValueError(f"설치 root 접두부와 맞지 않는 레지스트리 상대경로입니다: {value}")
    return Path(registry["_directory"]).joinpath(*path.parts[len(prefix):])


_RUNTIME_PATH_LISTS = {"PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "PYTHONPATH", "QT_PLUGIN_PATH"}
_RUNTIME_PATHS = {"OPENRADIOSS_PATH", "RAD_CFG_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"}


def _runtime_environment(runtime: dict, registry: dict) -> dict[str, str]:
    resolved = {}
    for key, value in runtime.items():
        # A host override is authoritative, including an explicitly empty value.
        if key in os.environ or not value:
            resolved[key] = value
        elif key in _RUNTIME_PATH_LISTS:
            resolved[key] = os.pathsep.join(str(_registered_path(part, registry)) for part in value.split(os.pathsep))
        elif key in _RUNTIME_PATHS:
            resolved[key] = str(_registered_path(value, registry))
        else:
            resolved[key] = value
    return resolved


def _executable_reason(path: Path, host: str) -> str:
    if not path.is_file():
        return f"실행 파일이 없거나 일반 파일이 아닙니다: {path}"
    if host == "windows":
        if path.suffix.lower() not in {".exe", ".com"}:
            return f"Windows 네이티브 실행 파일이 아닙니다. WSL/스크립트 연결은 미구현입니다: {path}"
    elif not os.access(path, os.X_OK):
        return f"실행 권한이 없습니다: {path}"
    return ""


def _status(spec: ProgramSpec, registry: dict, registry_error: str, host: str) -> ProgramStatus:
    status = ProgramStatus(spec, host_os=host)
    manifest_path = Path(__file__).parent / "solvers" / "installer" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next((program for program in manifest["programs"] if program["id"] == spec.id), None)
    if entry is not None:
        platform = entry.get("platforms", {}).get(host)
        if platform is None:
            status.reason = f"{host} 실행 환경은 지원하지 않습니다."
            return status
        status.execution_environment = platform.get("execution_environment", "unresolved")
        if status.execution_environment not in {"native", "apptainer"}:
            status.reason = f"{status.execution_environment} 실행 연결은 구현되지 않았습니다 ({host})."
            return status
        if status.execution_environment == "apptainer" and shutil.which("apptainer") is None:
            status.reason = "Apptainer 실행 환경을 찾지 못했습니다."
            return status
    explicit = os.environ.get(spec.env_var)
    if explicit is not None:
        candidate, status.source = explicit, "environment"
    elif registry_error:
        status.reason = registry_error
        return status
    elif spec.env_var in registry.get("env", {}):
        candidate, status.source = registry["env"][spec.env_var], "registry"
    else:
        registered = registry.get("programs", {}).get(spec.id, {})
        candidate = registered.get("path") if isinstance(registered, dict) else None
        status.source = "registry" if candidate else "PATH"
        if not candidate:
            candidate = next((value for name in spec.executables if (value := shutil.which(name))), None)
    if not isinstance(candidate, str) or not candidate.strip():
        status.reason = f"{spec.name} 실행 파일을 찾지 못했습니다 ({status.source})."
        return status
    try:
        candidate_path = _registered_path(candidate, registry) if status.source == "registry" else Path(candidate).expanduser()
    except ValueError as exc:
        status.reason = str(exc)
        return status
    # Keep the invoked path: resolving a venv interpreter symlink loses its environment.
    path = Path(os.path.abspath(candidate_path))
    status.path = str(path)
    status.reason = _executable_reason(path, host)
    if spec.id == "freecad" and path.stem.lower() == "freecadcmd":
        status.reason = "FreeCAD GUI에는 freecadcmd를 사용할 수 없습니다. GUI 실행 파일을 지정하세요."
    if spec.id == "paraview" and path.stem.lower() in {"pvpython", "pvbatch"}:
        status.reason = "ParaView GUI에는 pvpython/pvbatch를 사용할 수 없습니다. paraview 실행 파일을 지정하세요."
    runtime = registry.get("runtime_env", {}).get(spec.id, {})
    if not isinstance(runtime, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in runtime.items()):
        status.reason = "설치 레지스트리의 runtime_env 형식이 잘못되었습니다."
    else:
        try:
            status.runtime_env = _runtime_environment(runtime, registry)
        except ValueError as exc:
            status.reason = str(exc)
    registered = registry.get("programs", {}).get(spec.id, {})
    if isinstance(registered, dict) and registered.get("host_os", host) != host:
        status.reason = f"다른 호스트의 설치 레지스트리입니다: {registered.get('host_os')} → {host}"
    return status


def program_statuses(registry_path=None) -> list[ProgramStatus]:
    registry, error = _registry(registry_path)
    host = host_platform()
    return [_status(spec, registry, error, host) for spec in PROGRAMS]


def find_program(spec: ProgramSpec, registry_path=None) -> str | None:
    registry, error = _registry(registry_path)
    status = _status(spec, registry, error, host_platform())
    return status.path if status.available else None


def launch_request(status: ProgramStatus, file: str | Path) -> LaunchRequest:
    """Known native GUI file-open forms; does not submit solver jobs."""
    if not status.available:
        raise ValueError(status.reason or f"{status.spec.name} 실행 파일이 없습니다.")
    if status.spec.role == "solver":
        raise ValueError("솔버 실행은 P4 네이티브 실행 연결에서 제공합니다. GUI 열기로 실행하지 않습니다.")
    reason = _executable_reason(Path(status.path), host_platform())
    if reason:
        raise ValueError(reason)
    document = Path(file).expanduser().resolve()
    if not document.is_file():
        raise ValueError(f"입력 파일이 없습니다: {document}")
    if status.spec.id == "freecad":
        if document.suffix.lower() != ".fcstd":
            raise ValueError("FreeCAD 문서(.FCStd)를 선택하세요.")
        argv = [status.path, str(document)]
    elif status.spec.id == "gmsh":
        argv = [status.path, str(document)]
    elif status.spec.id == "paraview":
        # ParaView Users Guide, commandLineArguments: --data loads data only.
        argv = [status.path, "--data=" + str(document)]
    else:
        raise ValueError(f"{status.spec.name}의 네이티브 파일 열기 인자는 아직 구현되지 않았습니다.")
    # Explicit host environment wins over registered runtime defaults.
    environment = {**status.runtime_env, **os.environ}
    return LaunchRequest(argv, document.parent, environment, status.spec.id)


def analysis_launch_request(project_file: str | Path, node_id: str) -> LaunchRequest:
    project_file = Path(project_file).resolve()
    if not project_file.is_file():
        raise ValueError("입력 확인 창을 열기 전에 프로젝트를 저장하세요.")
    return LaunchRequest(
        [sys.executable, "-m", "apps.workbench_qt.native_analysis", str(project_file), "--node", node_id],
        Path(__file__).resolve().parent.parent, dict(os.environ), "native_analysis",
    )


def calculix_run_request(status: ProgramStatus, workdir: Path) -> LaunchRequest:
    """Native CCX execution; the input is already snapshotted as model.inp."""
    if status.spec.id != "calculix" or not status.available:
        raise ValueError(status.reason or "CalculiX 실행 파일이 없습니다.")
    reason = _executable_reason(Path(status.path), host_platform())
    if reason:
        raise ValueError(reason)
    return LaunchRequest([status.path, "-i", "model"], Path(workdir),
                         {**status.runtime_env, **os.environ}, "calculix")


def codeaster_run_request(status: ProgramStatus, workdir: Path, input_path: Path) -> LaunchRequest:
    if status.spec.id != "codeaster" or not status.available:
        raise ValueError(status.reason or "Code_Aster 실행 파일이 없습니다.")
    reason = _executable_reason(Path(status.path), host_platform())
    if reason:
        raise ValueError(reason)
    target = Path(workdir) / Path(input_path).name
    return LaunchRequest([status.path, str(target)], Path(workdir),
                         {**status.runtime_env, **os.environ}, "codeaster")


def radioss_run_request(status: ProgramStatus, workdir: Path, input_path: Path) -> LaunchRequest:
    if status.spec.id not in {"radioss", "openradioss", "opencourant"} or not status.available:
        raise ValueError(status.reason or "OpenRadioss 실행 파일이 없습니다.")
    reason = _executable_reason(Path(status.path), host_platform())
    if reason:
        raise ValueError(reason)
    target = Path(workdir) / Path(input_path).name
    return LaunchRequest([status.path, "-i", str(target)], Path(workdir),
                         {**status.runtime_env, **os.environ}, status.spec.id)


def lsdyna_run_request(status: ProgramStatus, workdir: Path, input_path: Path) -> LaunchRequest:
    if status.spec.id != "lsdyna" or not status.available:
        raise ValueError(status.reason or "LS-DYNA 실행 파일이 없습니다.")
    reason = _executable_reason(Path(status.path), host_platform())
    if reason:
        raise ValueError(reason)
    target = Path(workdir) / Path(input_path).name
    return LaunchRequest([status.path, f"i={target}"], Path(workdir),
                         {**status.runtime_env, **os.environ}, "lsdyna")


def expert_launch_request(discussion_file, project_file=None, node_id=None, run_id=None) -> LaunchRequest:
    argv = [sys.executable, '-m', 'apps.workbench_qt.expert_window',
            '--discussion', str(Path(discussion_file).resolve())]
    if project_file:
        argv.extend(['--project', str(Path(project_file).resolve()), '--node', node_id])
    if run_id:
        argv.extend(['--run-id', run_id])
    return LaunchRequest(argv, Path(__file__).resolve().parent.parent,
                         dict(os.environ), 'native_expert')


def launch_command(status: ProgramStatus, file: str | None = None) -> list[str] | None:
    """Legacy command construction only; native GUI callers use launch_request."""
    if not status.available:
        return None
    return [status.path] + ([file] if file else [])
