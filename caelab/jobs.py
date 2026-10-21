"""Job ownership: exactly one place runs solvers and records state."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path

from .model.schema import StructuralModel
from .solvers.base import ResultSet, SolverConverter

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


class JobManager:
    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.jobs: dict[int, Job] = {}

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
            deck = converter.write_input(model, job.workdir)
        except Exception as exc:  # deck errors fail the job, not the app
            job.result = ResultSet(
                solver_id=converter.id, success=False, log_tail=f"deck 작성 실패: {exc}"
            )
            job._set("failed")
            return job
        job._set("running")
        result = converter.run(model, deck, job.workdir)
        job.result = result
        job._set("succeeded" if result.success else "failed")
        return job

    def get(self, job_id: int) -> Job:
        return self.jobs[job_id]
