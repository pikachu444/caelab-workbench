"""The single command path.

GUI, CLI and MCP front-ends all go through this facade: open/save a
project, validate it, and run it through one converter. Opening a
project has no solver or LLM side effects.
"""

from __future__ import annotations

from pathlib import Path

from .jobs import Job, JobManager
from .model.schema import StructuralModel
from .model.store import load_project, save_project
from .solvers import get_converter
from .validate.checker import Issue, validate_model


class Workbench:
    def __init__(self) -> None:
        self.jobs = JobManager()

    # -- project ---------------------------------------------------------
    def open_project(self, folder: str | Path) -> StructuralModel:
        return load_project(folder)

    def save_project(self, model: StructuralModel, folder: str | Path) -> Path:
        return save_project(model, folder)

    # -- checking ---------------------------------------------------------
    def programs(self):
        """Availability of the connected external programs."""
        from .connections import program_statuses

        return program_statuses()

    def check(self, model: StructuralModel, solver_id: str = "calculix") -> list[Issue]:
        issues = validate_model(model)
        issues.extend(get_converter(solver_id).validate(model))
        return issues

    # -- running ------------------------------------------------------------
    def run(
        self,
        model: StructuralModel,
        solver_id: str,
        workdir: str | Path,
    ) -> Job:
        converter = get_converter(solver_id)
        return self.jobs.submit(converter, model, workdir)
