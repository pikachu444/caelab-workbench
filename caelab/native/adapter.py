"""Native solver adapters contract and registry.

Each supported solver (CalculiX, Code_Aster, OpenRadioss, LS-DYNA) implements
a SolverAdapter that owns its command construction, input file extension checks,
and native result collection without altering JobManager's process management
or RunStore's state ownership.
"""

from __future__ import annotations

from typing import Protocol, Callable
from pathlib import Path

from ..connections import ProgramStatus, LaunchRequest


class SolverAdapter(Protocol):
    solver_id: str
    name: str
    input_extensions: tuple[str, ...]

    def build_run_request(self, status: ProgramStatus, workdir: Path, input_path: Path) -> LaunchRequest:
        """Create the solver process launch request."""
        ...

    def collect_results(self, workdir: Path, *, check_cancelled: Callable[[], None] | None = None) -> dict:
        """Parse native solver results into exchange files and summary."""
        ...

    def expected_output_names(self) -> tuple[str, ...]:
        """Candidate result output files in the run directory."""
        ...


class CalculiXAdapter:
    solver_id = "calculix"
    name = "CalculiX"
    input_extensions = (".inp",)

    def build_run_request(self, status: ProgramStatus, workdir: Path, input_path: Path) -> LaunchRequest:
        from ..connections import calculix_run_request
        return calculix_run_request(status, workdir)

    def collect_results(self, workdir: Path, *, check_cancelled: Callable[[], None] | None = None) -> dict:
        from .results import collect_results
        return collect_results(workdir, check_cancelled=check_cancelled)

    def expected_output_names(self) -> tuple[str, ...]:
        return ("model.frd", "model.dat")


class CodeAsterAdapter:
    solver_id = "codeaster"
    name = "Code_Aster"
    input_extensions = (".comm", ".export")

    def build_run_request(self, status: ProgramStatus, workdir: Path, input_path: Path) -> LaunchRequest:
        from ..connections import codeaster_run_request
        return codeaster_run_request(status, workdir, input_path)

    def collect_results(self, workdir: Path, *, check_cancelled: Callable[[], None] | None = None) -> dict:
        from .code_aster import collect_codeaster_results
        return collect_codeaster_results(workdir, check_cancelled=check_cancelled)

    def expected_output_names(self) -> tuple[str, ...]:
        return ("model.rmed", "model.resu", "model.med")


class OpenRadiossAdapter:
    solver_id = "radioss"
    name = "OpenRadioss"
    input_extensions = (".rad",)

    def build_run_request(self, status: ProgramStatus, workdir: Path, input_path: Path) -> LaunchRequest:
        from ..connections import radioss_run_request
        return radioss_run_request(status, workdir, input_path)

    def collect_results(self, workdir: Path, *, check_cancelled: Callable[[], None] | None = None) -> dict:
        from .radioss import collect_radioss_results
        return collect_radioss_results(workdir, check_cancelled=check_cancelled)

    def expected_output_names(self) -> tuple[str, ...]:
        return ("A001", "model.h3d", "model.out")


class LSDynaAdapter:
    solver_id = "lsdyna"
    name = "LS-DYNA"
    input_extensions = (".k", ".key")

    def build_run_request(self, status: ProgramStatus, workdir: Path, input_path: Path) -> LaunchRequest:
        from ..connections import lsdyna_run_request
        return lsdyna_run_request(status, workdir, input_path)

    def collect_results(self, workdir: Path, *, check_cancelled: Callable[[], None] | None = None) -> dict:
        from .lsdyna import collect_lsdyna_results
        return collect_lsdyna_results(workdir, check_cancelled=check_cancelled)

    def expected_output_names(self) -> tuple[str, ...]:
        return ("d3plot", "glstat", "nodout")


_ADAPTERS: dict[str, SolverAdapter] = {
    "calculix": CalculiXAdapter(),
    "codeaster": CodeAsterAdapter(),
    "radioss": OpenRadiossAdapter(),
    "openradioss": OpenRadiossAdapter(),
    "opencourant": OpenRadiossAdapter(),
    "lsdyna": LSDynaAdapter(),
}


def register_solver_adapter(adapter: SolverAdapter) -> None:
    _ADAPTERS[adapter.solver_id] = adapter


def get_solver_adapter(solver_id: str) -> SolverAdapter:
    adapter = _ADAPTERS.get(solver_id)
    if adapter is None:
        raise ValueError(f"지원되지 않거나 등록되지 않은 솔버입니다: {solver_id}")
    return adapter


def supported_solver_ids() -> tuple[str, ...]:
    return tuple(_ADAPTERS.keys())
