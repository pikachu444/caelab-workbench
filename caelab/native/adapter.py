"""Native solver adapters contract and registry.

Each supported solver (CalculiX, Code_Aster, OpenRadioss, LS-DYNA) implements
a SolverAdapter that owns its command construction, input file extension checks,
and native result collection without altering JobManager's process management
or RunStore's state ownership.
"""

from __future__ import annotations

from typing import Protocol, Callable
from pathlib import Path
import re

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

    def snapshot_input(self, input_path, workdir, check_cancelled):
        from .input_snapshot import snapshot_radioss_input
        return snapshot_radioss_input(input_path, workdir, check_cancelled)

    def build_run_stages(self, status, workdir, snapshot):
        from ..connections import radioss_run_requests
        return radioss_run_requests(status, workdir, Path(snapshot["execution_input"]))

    def validate_stage(self, stage, workdir, snapshot):
        sequence = "0000" if stage == "starter" else "0001"
        listing = workdir / f"{snapshot['run_name']}_{sequence}.out"
        if not listing.is_file():
            raise ValueError(f"{stage}: 솔버 종료 listing이 없습니다: {listing.name}")
        text = listing.read_text(encoding="utf-8", errors="replace")
        if "NORMAL TERMINATION" not in text.upper() or re.search(r"\b[1-9]\d*\s+ERROR\(S\)", text, re.I):
            raise ValueError(f"{stage}: 정상 종료를 확인하지 못했습니다. {listing.name}을 확인하세요.")
        if stage == "starter":
            restart = workdir / f"{snapshot['run_name']}_0000_0001.rst"
            if not restart.is_file() or not restart.stat().st_size:
                raise ValueError(f"Starter restart가 없습니다. Engine을 실행하지 않습니다: {restart.name}")

    def artifact_paths(self, workdir, snapshot):
        inputs = {item["execution_path"] for item in snapshot.get("files", [])}
        return [path for path in workdir.rglob("*") if path.is_file()
                and path.relative_to(workdir).as_posix() not in inputs
                and path.relative_to(workdir).parts[0] != ".source"
                and path.name not in {"job.json", ".start-solver"}]

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
