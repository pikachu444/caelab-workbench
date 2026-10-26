"""Objective minimization coordinates and explicitly requested scalarization."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .variables import _id, _finite, _readonly


@dataclass(frozen=True, slots=True, kw_only=True)
class ObjectiveSpec:
    id: str
    response_id: str
    sense: str = "MIN"
    target: float|None = None
    target_band: float|None = None
    offset: float = 0.
    scale: float = 1.
    weight: float = 1.
    def __post_init__(self):
        _id(self.id); _id(self.response_id)
        if self.sense not in ("MIN","MAX","TARGET"):
            raise ValueError("Unknown objective sense")
        if _finite(self.scale)<=0 or _finite(self.weight)<0:
            raise ValueError("Scale must be positive and weight nonnegative")
        _finite(self.offset)
        if self.sense=="TARGET":
            if self.target is None or self.target_band is None:
                raise ValueError("Target objective requires target and target_band")
            _finite(self.target)
            if _finite(self.target_band)<0:
                raise ValueError("Target band must be nonnegative")
        elif self.target is not None or self.target_band is not None:
            raise ValueError("Target fields are only valid for TARGET objectives")


def transform_objectives(values, specs):
    specs = tuple(specs)
    if len({s.id for s in specs})!=len(specs):
        raise ValueError("Duplicate objective IDs")
    result = []
    for s in specs:
        y = values.get(s.response_id)
        if y is None:
            raise ValueError(f"Missing objective response {s.response_id}")
        y = _finite(y)
        if s.sense=="TARGET":
            value = max(0.,abs(y-s.target)-s.target_band)/s.scale
        else:
            value = ((y-s.offset)/s.scale) * (1 if s.sense=="MIN" else -1)
        result.append(_finite(value))
    return _readonly(result)


def scalarize(vector, specs):
    specs = tuple(specs); vector = _readonly(vector)
    if vector.shape!=(len(specs),):
        raise ValueError("Objective vector shape mismatch")
    weights = np.array([s.weight for s in specs],dtype=float)
    if not len(weights) or not np.any(weights>0):
        raise ValueError("Weighted sum requires at least one positive weight")
    # Divide first to avoid overflow for individually finite large weights.
    weights /= weights.max(); weights /= weights.sum()
    return _finite(float(np.dot(weights,vector)))


@dataclass(frozen=True, slots=True, kw_only=True)
class CriteriaEvaluation:
    objectives: np.ndarray|None
    constraints: object
    assessed: bool
    reason: str|None = None
    id: str = "criteria"
    def __post_init__(self):
        _id(self.id)
        if self.objectives is not None:
            object.__setattr__(self,"objectives",_readonly(self.objectives))
        if self.assessed != (self.objectives is not None and self.constraints.feasibility is not None):
            raise ValueError("Inconsistent criteria assessment")
        if not self.assessed and not self.reason:
            raise ValueError("Unassessed criteria need a reason")


def evaluate_criteria(observations, criteria, constraints=(), *, valid=True, units=None):
    """Compose all required objective/constraint validity before feasibility."""
    from .constraints import assess_constraints
    try:
        objectives = transform_objectives(observations,criteria) if valid else None
    except (ValueError,TypeError,OverflowError):
        objectives = None
    assessment = assess_constraints(observations,constraints,valid=valid and objectives is not None,units=units)
    assessed = objectives is not None and assessment.feasibility is not None
    return CriteriaEvaluation(objectives=objectives,constraints=assessment,assessed=assessed,
                              reason=None if assessed else "NOT_ASSESSED")

