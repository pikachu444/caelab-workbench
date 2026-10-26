"""Study manager and orchestration for MDO campaigns.

Owns:
- Study definition (VariableSpace, ObjectiveSpecs, ConstraintSpecs, TemplateDeck, ResponseSpecs).
- In-memory and persistent Table of Design Points (TDP).
- Execution dispatch (mock/callable for analytical tests, Subprocess/JobManager for CAE).
- Status, Feasibility, Pareto and Sensitivity extraction.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence, Any

from .variables import VariableSpace, EncodedPoint
from .objectives import ObjectiveSpec, transform_objectives
from .constraints import ConstraintSpec, ConstraintAssessment, assess_constraints, Feasibility
from .pareto import pareto_front, crowding_distance, knee_point


@dataclass
class DesignPointRecord:
    point_id: str
    index: int
    name: str
    physical_values: dict[str, Any]
    status: str = "PENDING"  # PENDING, RUNNING, COMPLETED, FAILED
    observations: dict[str, float] = field(default_factory=dict)
    feasibility: str | None = None  # FEASIBLE, MARGINAL, INFEASIBLE, NOT_ASSESSED
    total_violation: float = 0.0
    objectives: dict[str, float] = field(default_factory=dict)
    is_pareto: bool = False
    duration_sec: float = 0.0
    error_message: str = ""


class StudyManager:
    """Manages an MDO study campaign, execution ledger, and results."""

    def __init__(
        self,
        name: str = "New MDO Study",
        *,
        space: VariableSpace | None = None,
        objectives: Sequence[ObjectiveSpec] = (),
        constraints: Sequence[ConstraintSpec] = (),
    ) -> None:
        self.name = name
        self.space = space or VariableSpace(variables=())
        self.objectives = list(objectives)
        self.constraints = list(constraints)
        self.records: list[DesignPointRecord] = []
        self._record_map: dict[str, DesignPointRecord] = {}

    def add_point(self, physical_values: Mapping[str, Any], name: str | None = None) -> DesignPointRecord:
        idx = len(self.records) + 1
        pt_id = f"DP_{idx:04d}"
        pt_name = name or f"Point {idx}"
        rec = DesignPointRecord(
            point_id=pt_id,
            index=idx,
            name=pt_name,
            physical_values=dict(physical_values),
        )
        self.records.append(rec)
        self._record_map[pt_id] = rec
        return rec

    def evaluate_point_with_evaluator(
        self,
        rec: DesignPointRecord,
        evaluator: Callable[[dict[str, Any]], dict[str, float]],
    ) -> None:
        rec.status = "RUNNING"
        t0 = time.monotonic()
        try:
            obs = evaluator(rec.physical_values)
            rec.observations = obs
            rec.status = "COMPLETED"

            # Assess constraints
            if self.constraints:
                assess = assess_constraints(obs, self.constraints)
                rec.feasibility = assess.feasibility.value if assess.feasibility else "NOT_ASSESSED"
                rec.total_violation = assess.total_violation or 0.0
            else:
                rec.feasibility = "FEASIBLE"
                rec.total_violation = 0.0

            # Assess objectives
            if self.objectives:
                transformed = transform_objectives(obs, self.objectives)
                rec.objectives = {obj.id: float(val) for obj, val in zip(self.objectives, transformed)}
        except Exception as ex:
            rec.status = "FAILED"
            rec.feasibility = "NOT_ASSESSED"
            rec.error_message = str(ex)
        finally:
            rec.duration_sec = time.monotonic() - t0

    def update_pareto_front(self) -> list[str]:
        """Compute Pareto front on current feasible points."""
        feasible = [r for r in self.records if r.status == "COMPLETED" and r.feasibility == "FEASIBLE"]
        for r in self.records:
            r.is_pareto = False

        if not feasible or not self.objectives:
            return []

        # Minimization vectors
        vecs = [[r.objectives.get(o.id, 0.0) for o in self.objectives] for r in feasible]
        p_ids = [r.point_id for r in feasible]
        indices = pareto_front(vecs, p_ids)

        pareto_ids = []
        for idx in indices:
            feasible[idx].is_pareto = True
            pareto_ids.append(feasible[idx].point_id)
        return pareto_ids
