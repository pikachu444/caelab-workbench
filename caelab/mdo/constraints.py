"""Tolerance-aware residuals with nullable, three-state feasibility."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import math
import numpy as np
from .variables import _id, _finite, _readonly, evaluate_expression, parse_unit


class Feasibility(str, Enum):
    """Three-state constraint feasibility evaluation status."""
    FEASIBLE = "FEASIBLE"
    MARGINAL = "MARGINAL"
    INFEASIBLE = "INFEASIBLE"


@dataclass(frozen=True, slots=True, kw_only=True)
class ConstraintSpec:
    id: str
    response_id: str|None
    kind: str
    limit: float
    scale: float = 1.
    tolerance: float|None = None
    marginal_band: float = 0.
    weight: float = 1.
    expression: str|None = None
    dependencies: tuple[str,...] = ()
    unit: str = ""
    def __post_init__(self):
        _id(self.id); parse_unit(self.unit)
        if self.kind not in ("UPPER","LOWER","EQUALITY"):
            raise ValueError("Unknown constraint kind")
        _finite(self.limit)
        if _finite(self.scale)<=0 or _finite(self.weight)<=0 or _finite(self.marginal_band)<0:
            raise ValueError("Constraint scale/weight must be positive and band nonnegative")
        if self.kind=="EQUALITY" and self.tolerance is None:
            raise ValueError("Equality requires an explicit tolerance")
        tolerance = 0. if self.tolerance is None else _finite(self.tolerance)
        if tolerance<0:
            raise ValueError("Acceptance tolerance must be nonnegative")
        object.__setattr__(self,"tolerance",tolerance)
        object.__setattr__(self,"dependencies",tuple(self.dependencies))
        if self.expression is None:
            _id(self.response_id)
            if self.dependencies:
                raise ValueError("Dependencies need an expression")
        elif self.response_id is not None:
            raise ValueError("Choose response_id or expression")


@dataclass(frozen=True, slots=True, kw_only=True)
class ConstraintAssessment:
    raw_residuals: np.ndarray
    normalized_residuals: np.ndarray
    violations: np.ndarray
    total_violation: float|None
    feasibility: Feasibility|None
    reason: str|None = None
    criterion_ids: tuple[str,...] = ()
    weights: tuple[float,...] = ()
    adjusted_residuals: np.ndarray|None = None
    id: str = "constraints"
    def __post_init__(self):
        _id(self.id)
        for name in ("raw_residuals","normalized_residuals","violations"):
            object.__setattr__(self,name,_readonly(getattr(self,name)))
        sizes = {getattr(self,n).shape for n in ("raw_residuals","normalized_residuals","violations")}
        if len(sizes)!=1 or self.raw_residuals.ndim!=1:
            raise ValueError("Constraint residual shape mismatch")
        if self.adjusted_residuals is not None:
            adjusted = _readonly(self.adjusted_residuals)
            if adjusted.shape!=self.raw_residuals.shape:
                raise ValueError("Adjusted residual shape mismatch")
            object.__setattr__(self,"adjusted_residuals",adjusted)
        ids = tuple(self.criterion_ids)
        for criterion_id in ids:
            _id(criterion_id)
        if len(ids)!=len(self.violations) or len(set(ids))!=len(ids):
            raise ValueError("Unique criterion IDs must match residual rows")
        object.__setattr__(self,"criterion_ids",ids)
        if not np.array_equal(self.violations,np.maximum(0.,self.normalized_residuals)):
            raise ValueError("Violations must equal the positive normalized residuals")
        weights = tuple(_finite(x) for x in self.weights)
        if len(weights)!=len(self.violations) or any(x<=0 for x in weights):
            raise ValueError("Declared positive weights must match violations")
        object.__setattr__(self,"weights",weights)
        if self.feasibility is None:
            if not isinstance(self.reason,str) or not self.reason.strip() or self.total_violation is not None:
                raise ValueError("Unassessed feasibility needs reason and nullable total")
        else:
            try:
                feasibility = Feasibility(self.feasibility)
            except (ValueError,TypeError) as exc:
                raise ValueError("Invalid feasibility enum") from exc
            object.__setattr__(self,"feasibility",feasibility)
            if self.total_violation is None or _finite(self.total_violation)<0:
                raise ValueError("Assessed feasibility requires a finite nonnegative total")
            try:
                expected_total = _finite(math.fsum(w*v for w,v in zip(weights,self.violations)))
            except ArithmeticError as exc:
                raise ValueError("Weighted violation total overflows") from exc
            if not math.isclose(self.total_violation,expected_total,rel_tol=1e-12,abs_tol=0.):
                raise ValueError("Total violation disagrees with declared weights and violations")
            accepted = bool(np.all(self.normalized_residuals<=0))
            if (feasibility==Feasibility.FEASIBLE)!=accepted:
                raise ValueError("Feasibility contradicts normalized residual acceptance")
    @property
    def feasible(self):
        return None if self.feasibility is None else self.feasibility==Feasibility.FEASIBLE


def assess_constraints(values, specs, *, valid=True, units=None):
    specs = tuple(specs)
    if len({s.id for s in specs})!=len(specs):
        raise ValueError("Duplicate constraint IDs")
    raw=[]; adjusted=[]; excess=[]; violations=[]; weights=[]; bands=[]; ids=[]
    missing = not valid
    for s in specs:
        try:
            if s.expression is not None:
                y = evaluate_expression(s.expression,values,units or {},s.unit,dependencies=s.dependencies)
            else:
                y = _finite(values[s.response_id])
                if units is not None:
                    source_factor,source_dim = parse_unit(units[s.response_id])
                    output_factor,output_dim = parse_unit(s.unit)
                    if source_dim!=output_dim:
                        raise ValueError("Response/constraint dimensions differ")
                    y *= source_factor/output_factor
            r = y-s.limit if s.kind!="LOWER" else s.limit-y
            a = (abs(r) if s.kind=="EQUALITY" else r)-s.tolerance
            e = a/s.scale
            band = s.marginal_band/s.scale
            for x in (r,a,e,band):
                _finite(x)
            raw.append(r); adjusted.append(a); excess.append(e); violations.append(max(0.,e))
            weights.append(s.weight); bands.append(band); ids.append(s.id)
        except (KeyError,ValueError,TypeError,ArithmeticError):
            missing = True
    total = None
    feasibility = None
    if not missing:
        try:
            total = _finite(math.fsum(w*v for w,v in zip(weights,violations)))
        except (ValueError, ArithmeticError):
            missing = True
        if not missing:
            feasibility = (Feasibility.FEASIBLE if all(e<=0 for e in excess) else
                           Feasibility.MARGINAL if all(e<=b for e,b in zip(excess,bands)) else
                           Feasibility.INFEASIBLE)
    return ConstraintAssessment(raw_residuals=raw,adjusted_residuals=adjusted,
        normalized_residuals=excess,violations=violations,total_violation=total,
        feasibility=feasibility,reason="NOT_ASSESSED" if missing else None,criterion_ids=tuple(ids),weights=tuple(weights))


def constraint_penalty(assessment, *, multiplier=1., power=1, weights=None):
    if assessment.feasibility is None:
        raise ValueError("Invalid evaluations cannot become penalty recommendations")
    if _finite(multiplier)<=0 or power not in (1,2):
        raise ValueError("Invalid penalty configuration")
    w = _readonly(assessment.weights)
    if weights is not None and not np.array_equal(_readonly(weights),w):
        raise ValueError("Penalty weights must match the declared constraints")
    if w.shape!=assessment.violations.shape or np.any(w<=0):
        raise ValueError("Positive penalty weights required")
    return _finite(float(multiplier*np.dot(w,assessment.violations**power)))

