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

import os
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
        ("freecad", "FreeCAD", "freecadcmd"),
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
        "paraview", "ParaView", "postprocessor", "CAELAB_PARAVIEW",
        ("paraview",),
        "결과 후처리의 본선 프로그램.",
    ),
)


@dataclass
class ProgramStatus:
    spec: ProgramSpec
    path: str | None = None

    @property
    def available(self) -> bool:
        return self.path is not None


def find_program(spec: ProgramSpec) -> str | None:
    """Locate a program: explicit env override first, then PATH."""
    override = os.environ.get(spec.env_var)
    if override:
        return override if os.path.exists(override) else None
    for name in spec.executables:
        found = shutil.which(name)
        if found:
            return found
    return None


def program_statuses() -> list[ProgramStatus]:
    return [ProgramStatus(spec, find_program(spec)) for spec in PROGRAMS]


def launch_command(status: ProgramStatus, file: str | None = None) -> list[str] | None:
    """Build the command that opens the program (with a file if given).

    Construction only — the caller decides whether to execute, so this
    stays testable and opening a project never launches anything by
    itself.
    """
    if not status.available:
        return None
    command = [status.path]  # type: ignore[list-item]
    if file:
        command.append(file)
    return command
