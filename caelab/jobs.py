"""Job ownership: exactly one place runs solvers and records state."""

from __future__ import annotations

import itertools
import os
import subprocess
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from .model.schema import StructuralModel
from .connections import LaunchRequest
from .solvers.base import ResultSet, SolverConverter
from .validate.checker import validate_model

JOB_STATES = ("queued", "preparing", "running", "succeeded", "failed", "cancelled")


@dataclass
class Job:
    id: int
    solver_id: str
    workdir: Path
    state: str = "queued"
    history: list = field(default_factory=lambda: ["queued"])
    result: ResultSet | None = None

    def _set(self, state: str) -> None:
        self.state = state
        self.history.append(state)


@dataclass
class LaunchRecord:
    id: int
    request: LaunchRequest
    state: str = "opened"
    process: subprocess.Popen | None = None
    error: str = ""

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process is not None else None


class JobManager:
    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.jobs: dict[int, Job] = {}
        self.launches: dict[int, LaunchRecord] = {}

    def launch(self, request: LaunchRequest) -> LaunchRecord:
        """Retain this GUI process; creation never means calculation success."""
        record = LaunchRecord(next(self._ids), request)
        self.launches[record.id] = record
        try:
            record.process = subprocess.Popen(
                request.argv, cwd=request.cwd, env=request.env, shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as exc:
            record.state, record.error = "failed", f"{type(exc).__name__}: {exc}"
        return record

    def submit(
        self,
        converter: SolverConverter,
        model: StructuralModel,
        workdir: str | Path,
    ) -> Job:
        job = Job(id=next(self._ids), solver_id=converter.id, workdir=Path(workdir))
        self.jobs[job.id] = job
        job._set("preparing")
        try:
            job.workdir.mkdir(parents=True, exist_ok=True)
            issues = validate_model(model) + converter.validate(model)
            errors = [str(issue) for issue in issues if issue.severity == "error"]
            if errors:
                job.result = ResultSet(
                    solver_id=converter.id, success=False,
                    log_tail="\n".join(errors)[-4000:], validation_issues=errors,
                )
                (job.workdir / "job.log").write_text("\n".join(errors), encoding="utf-8")
                job.result.log_path = str(job.workdir / "job.log")
                job._set("failed")
                return job
            deck = converter.write_input(model, job.workdir)
            job._set("running")
            job.result = converter.run(model, deck, job.workdir)
            if job.result.log_path is None:
                log_path = job.workdir / "job.log"
                log_path.write_text(job.result.log_tail, encoding="utf-8")
                job.result.log_path = str(log_path)
            job._set("succeeded" if job.result.success else "failed")
        except Exception as exc:  # preparation, execution and reading all terminate
            detail = traceback.format_exc()
            log_path = job.workdir / "job.log"
            try:
                with log_path.open("a", encoding="utf-8") as log:
                    log.write("\n" + detail)
                saved_path = str(log_path)
            except OSError:
                saved_path = None
            job.result = ResultSet(
                solver_id=converter.id, success=False,
                log_tail=f"{type(exc).__name__}: {exc}\n{detail}"[-4000:],
                log_path=saved_path,
            )
            job._set("failed")
        return job

    def get(self, job_id: int) -> Job:
        return self.jobs[job_id]
