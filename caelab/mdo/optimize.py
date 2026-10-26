"""Library optimizers consuming keyed evaluation records; never launch processes."""
from __future__ import annotations
from dataclasses import dataclass, field
from importlib.metadata import version
from types import MappingProxyType
from typing import Mapping, Protocol
import time
import numbers
import numpy as np
from .variables import VariableSpace, ContinuousVariable, DiscreteVariable, CategoricalVariable, LinkedVariable
from .objectives import ObjectiveSpec, evaluate_criteria, scalarize
from .constraints import ConstraintSpec
from .pareto import pareto_front


def _ro(value):
    a = np.array(value, dtype=float, copy=True)
    if not np.isfinite(a).all():
        raise ValueError("finite numeric data required")
    return np.frombuffer(a.tobytes(), dtype=float).reshape(a.shape)


def _response_map(responses):
    validated = {}
    for key, value in responses.items():
        if not isinstance(key, str) or not key:
            raise ValueError("response IDs must be nonempty strings")
        if value is None:
            validated[key] = None
            continue
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            raise ValueError("responses require finite numeric scalars or explicit None")
        try:
            value = float(value)
        except (ValueError, OverflowError):
            raise ValueError("responses require finite numeric scalars or explicit None") from None
        if not np.isfinite(value):
            raise ValueError("responses require finite numeric scalars or explicit None")
        validated[key] = value
    return MappingProxyType(validated)


@dataclass(frozen=True, slots=True, kw_only=True)
class OptimizationProblem:
    space: VariableSpace
    objectives: tuple[ObjectiveSpec, ...]
    constraints: tuple[ConstraintSpec, ...] = ()
    mode: str = "ACTUAL"
    revision: str = "1"
    criteria_revision: str = "1"
    differentiable: bool = False
    response_units: Mapping[str, str] | None = None

    def __post_init__(self):
        object.__setattr__(self, "objectives", tuple(self.objectives))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        if not self.objectives or self.space.dimension == 0 or self.mode not in ("ACTUAL", "SURROGATE"):
            raise ValueError("objectives, independent variables and explicit evaluation mode required")
        if not self.revision or not self.criteria_revision:
            raise ValueError("frozen problem/criteria revisions required")
        if len({o.id for o in self.objectives}) != len(self.objectives) or len({c.id for c in self.constraints}) != len(self.constraints):
            raise ValueError("duplicate criterion IDs")
        if self.response_units is not None:
            object.__setattr__(self, "response_units", MappingProxyType(dict(self.response_units)))


@dataclass(frozen=True, slots=True, kw_only=True)
class OptimizationConfig:
    algorithm: str = "nsga2"
    seed: int = 0
    # Dispatched request budget; StudyManager owns native-attempt/retry/confirmation budgets.
    max_evaluations: int = 1000
    max_iterations: int = 100
    population_size: int = 40
    max_failures: int = 10
    wall_seconds: float | None = None
    weighted_sum: bool = False
    ftol: float = 1e-9
    finite_difference_step: float = 1e-6
    crossover_probability: float = 0.9
    crossover_eta: float = 15.0
    mutation_eta: float = 20.0
    de_strategy: str = "best1bin"
    de_mutation: tuple[float, float] = (0.5, 1.0)
    de_recombination: float = 0.7

    def __post_init__(self):
        if self.algorithm not in ("nsga2", "slsqp", "de"):
            raise ValueError("algorithm must be nsga2, slsqp or de")
        for name in ("max_evaluations", "max_iterations", "population_size"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.population_size < 4 or type(self.max_failures) is not int or self.max_failures < 0:
            raise ValueError("population>=4 and nonnegative failure limit required")
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        for name in ("ftol", "finite_difference_step", "crossover_eta", "mutation_eta"):
            if not np.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be finite positive")
        if self.wall_seconds is not None and (not np.isfinite(self.wall_seconds) or self.wall_seconds <= 0):
            raise ValueError("wall time must be finite positive")
        if not 0 < self.crossover_probability <= 1 or not 0 <= self.de_recombination <= 1:
            raise ValueError("invalid recombination probability")
        object.__setattr__(self, "de_mutation", tuple(self.de_mutation))
        if len(self.de_mutation) != 2 or not 0 <= self.de_mutation[0] <= self.de_mutation[1] < 2:
            raise ValueError("DE mutation range must lie in [0,2)")


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluationRequest:
    id: str
    point_id: str
    values: Mapping
    encoded: np.ndarray
    mode: str
    problem_revision: str

    def __post_init__(self):
        if not self.id or not self.point_id or self.mode not in ("ACTUAL", "SURROGATE"):
            raise ValueError("request IDs and mode required")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        object.__setattr__(self, "encoded", _ro(self.encoded))


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluationObservation:
    request_id: str
    responses: Mapping[str, float | None] = field(default_factory=dict)
    valid: bool = True
    reason: str | None = None
    mode: str = "ACTUAL"
    point_id: str | None = None
    run_ids: tuple[str, ...] = ()
    source_id: str | None = None
    native_attempts: int = 0
    cache_hit: bool = False

    def __post_init__(self):
        if not self.request_id or self.mode not in ("ACTUAL", "SURROGATE"):
            raise ValueError("observation request ID and mode required")
        if not self.valid and not self.reason:
            raise ValueError("invalid observation requires a reason")
        if type(self.native_attempts) is not int or self.native_attempts < 0:
            raise ValueError("native attempt count must be nonnegative")
        if self.valid and self.mode == "ACTUAL" and not self.run_ids and not self.source_id:
            raise ValueError("valid actual observation needs run IDs or explicit analytic source ID")
        if self.valid and self.mode == "SURROGATE" and not self.source_id:
            raise ValueError("prediction observation needs surrogate source ID")
        object.__setattr__(self, "responses", _response_map(self.responses))
        object.__setattr__(self, "run_ids", tuple(self.run_ids))


class BatchEvaluator(Protocol):
    def evaluate_batch(self, requests: tuple[EvaluationRequest, ...]) -> Mapping[str, EvaluationObservation]: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class ArchivePoint:
    point_id: str
    request_id: str
    values: Mapping
    responses: Mapping
    objectives: np.ndarray
    normalized_residuals: np.ndarray
    raw_residuals: np.ndarray
    total_violation: float
    feasible: bool
    feasibility: str
    mode: str
    problem_revision: str
    criteria_revision: str
    run_ids: tuple[str, ...] = ()
    source_id: str | None = None
    valid: bool = True

    def __post_init__(self):
        if self.valid is not True or self.mode not in ("ACTUAL", "SURROGATE") or not np.isfinite(self.total_violation) or self.total_violation < 0:
            raise ValueError("scientific archive contains only valid assessed records")
        for name in ("point_id", "request_id", "problem_revision", "criteria_revision"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError("archive provenance IDs and revisions must be nonempty strings")
        run_ids = tuple(self.run_ids)
        if any(not isinstance(value, str) or not value.strip() for value in run_ids):
            raise ValueError("archive run IDs must be nonempty strings")
        if self.source_id is not None and (not isinstance(self.source_id, str) or not self.source_id.strip()):
            raise ValueError("archive source identity must be a nonempty string")
        if self.mode == "ACTUAL" and not run_ids and self.source_id is None:
            raise ValueError("actual archive requires run IDs or explicit analytic source identity")
        if self.mode == "SURROGATE" and self.source_id is None:
            raise ValueError("predicted archive requires a model source identity")
        if self.feasibility not in ("FEASIBLE", "MARGINAL", "INFEASIBLE") or type(self.feasible) is not bool:
            raise ValueError("archive requires a valid feasibility state and boolean acceptance")
        if self.feasible != (self.feasibility == "FEASIBLE"):
            raise ValueError("only FEASIBLE is accepted")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        object.__setattr__(self, "responses", _response_map(self.responses))
        object.__setattr__(self, "run_ids", run_ids)
        for name in ("objectives", "normalized_residuals", "raw_residuals"):
            vector = _ro(getattr(self, name))
            if vector.ndim != 1:
                raise ValueError("archive objective and residual arrays must be one dimensional")
            object.__setattr__(self, name, vector)
        if self.objectives.size == 0:
            raise ValueError("archive requires at least one objective")
        if self.normalized_residuals.shape != self.raw_residuals.shape:
            raise ValueError("archive residual arrays must have identical shapes")
        positive_residual = bool(np.any(self.normalized_residuals > 0))
        if self.feasible:
            if positive_residual or self.total_violation != 0:
                raise ValueError("accepted archive points require nonpositive residuals and zero violation")
        elif not positive_residual or self.total_violation <= 0:
            raise ValueError("violated archive points require a positive residual and positive violation")


@dataclass(frozen=True, slots=True)
class EvaluationFailure:
    request_id: str
    point_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    dispatched_requests: int
    dispatcher_calls: int
    cache_hits: int
    duplicate_proposals: int
    predictions: int
    native_attempts: int
    failures: int
    elapsed_seconds: float


@dataclass(frozen=True, slots=True, kw_only=True)
class OptimizationResult:
    termination_reason: str
    actual_archive: tuple[ArchivePoint, ...]
    predicted_archive: tuple[ArchivePoint, ...]
    selected_ids: tuple[str, ...]
    failures: tuple[EvaluationFailure, ...]
    algorithm: str
    algorithm_version: str
    seed: int
    budget_usage: BudgetUsage
    message: str
    trace: tuple[tuple[int, int, int], ...]
    config: OptimizationConfig


class _Stop(Exception):
    def __init__(self, reason, message):
        self.reason, self.message = reason, message


class _Ledger:
    def __init__(self, problem, evaluator, config, cancel):
        self.problem, self.evaluator, self.config, self.cancel = problem, evaluator, config, cancel
        self.started = time.monotonic()
        self.cache, self.archive, self.failures, self.trace = {}, [], [], []
        self.dispatches = self.calls = self.hits = self.duplicates = self.predictions = self.native_attempts = 0

    def check(self):
        cancelled = self.cancel() if callable(self.cancel) else bool(self.cancel is not None and self.cancel.is_set())
        if cancelled:
            raise _Stop("CANCELLED", "cancelled at evaluation boundary")
        if self.config.wall_seconds is not None and time.monotonic() - self.started >= self.config.wall_seconds:
            raise _Stop("BUDGET_EXHAUSTED", "wall time budget exhausted")

    def batch(self, vectors):
        self.check()
        pending, keys, batch_results = {}, [], {}
        for vector in vectors:
            physical = self.problem.space.decode(vector, purpose="optimizer").values
            key = tuple((v.id, physical[v.id]) for v in self.problem.space.independent_variables)
            keys.append(key)
            if key in self.cache:
                self.hits += 1
                continue
            if key in pending:
                self.duplicates += 1
                continue
            if self.dispatches + len(pending) >= self.config.max_evaluations:
                raise _Stop("BUDGET_EXHAUSTED", "evaluation request budget exhausted before full batch")
            index = self.dispatches + len(pending)
            request_id = f"request:{index:09d}"
            pending[key] = EvaluationRequest(id=request_id, point_id=f"point:{index:09d}", values=physical,
                encoded=vector, mode=self.problem.mode, problem_revision=self.problem.revision)
        if pending:
            requests = tuple(pending.values())
            self.dispatches += len(requests)
            self.calls += 1
            observations = self.evaluator.evaluate_batch(requests)
            if not isinstance(observations, Mapping) or set(observations) != {r.id for r in requests}:
                raise ValueError("evaluator must return exactly the dispatched request IDs")
            for key, request in pending.items():
                obs = observations[request.id]
                if not isinstance(obs, EvaluationObservation) or obs.request_id != request.id:
                    raise ValueError("evaluator record/request identity mismatch")
                if obs.mode != self.problem.mode or (obs.point_id is not None and obs.point_id != request.point_id):
                    raise ValueError("evaluator provenance/mode mismatch")
                self.native_attempts += obs.native_attempts
                self.hits += int(obs.cache_hit)
                self.predictions += int(obs.mode == "SURROGATE")
                criteria = evaluate_criteria(obs.responses, self.problem.objectives, self.problem.constraints,
                                             valid=obs.valid, units=self.problem.response_units)
                if not criteria.assessed:
                    batch_results[key] = None
                    self.failures.append(EvaluationFailure(request.id, request.point_id, obs.reason or "required criteria responses missing/invalid"))
                    continue
                assessment = criteria.constraints
                point = ArchivePoint(point_id=request.point_id, request_id=request.id, values=request.values,
                    responses=obs.responses, objectives=criteria.objectives, normalized_residuals=assessment.normalized_residuals,
                    raw_residuals=assessment.raw_residuals, total_violation=assessment.total_violation,
                    feasible=assessment.feasible, feasibility=assessment.feasibility.value, mode=obs.mode,
                    problem_revision=self.problem.revision, criteria_revision=self.problem.criteria_revision,
                    run_ids=obs.run_ids, source_id=obs.source_id)
                self.archive.append(point)
                self.cache[key] = point
            self.trace.append((self.dispatches, len(self.archive), len(self.failures)))
            # Completed observations are retained before acknowledging cancellation or wall time.
            self.check()
            if self.failures and len(self.failures) >= self.config.max_failures:
                raise _Stop("FAILED", "invalid evaluation failure limit reached")
        return [self.cache[key] if key in self.cache else batch_results[key] for key in keys]

    def one(self, vector, *, stop_on_failure=False):
        point = self.batch([vector])[0]
        if point is None and stop_on_failure:
            raise _Stop("FAILED", "SLSQP stopped on invalid evaluation; no artificial failure surface")
        return point


def _scalar(point, problem):
    return float(point.objectives[0]) if len(problem.objectives) == 1 else scalarize(point.objectives, problem.objectives)


def _slsqp(problem, config, ledger):
    from scipy.optimize import minimize
    vector = problem.space.encode({v.id: v.initial for v in problem.space.independent_variables}, purpose="optimizer").vector
    def evaluate(x):
        return ledger.one(x, stop_on_failure=True)
    constraints = []
    for j, spec in enumerate(problem.constraints):
        if spec.kind == "EQUALITY" and spec.tolerance == 0:
            constraints.append({"type": "eq", "fun": lambda x, j=j, s=spec: evaluate(x).raw_residuals[j] / s.scale})
        elif spec.kind == "EQUALITY":
            constraints.extend({"type": "ineq", "fun": lambda x, j=j, s=spec, sign=sign:
                (s.tolerance + sign * evaluate(x).raw_residuals[j]) / s.scale} for sign in (-1, 1))
        else:
            constraints.append({"type": "ineq", "fun": lambda x, j=j: -evaluate(x).normalized_residuals[j]})
    result = minimize(lambda x: _scalar(evaluate(x), problem), np.asarray(vector), method="SLSQP",
                      bounds=problem.space.optimizer_bounds, constraints=constraints,
                      options={"maxiter": config.max_iterations, "ftol": config.ftol, "eps": config.finite_difference_step})
    return ("CONVERGED" if result.success else "STALLED"), str(result.message), version("scipy")


def _de(problem, config, ledger):
    from scipy.optimize import differential_evolution, NonlinearConstraint
    def residual(x):
        point = ledger.one(x)
        return np.full(len(problem.constraints), np.inf) if point is None else point.normalized_residuals
    constraints = () if not problem.constraints else (NonlinearConstraint(residual, -np.inf, 0),)
    def objective(x):
        point = ledger.one(x)
        return np.inf if point is None else _scalar(point, problem)
    result = differential_evolution(objective, bounds=problem.space.optimizer_bounds,
        integrality=[isinstance(v, DiscreteVariable) for v in problem.space.independent_variables],
        constraints=constraints, strategy=config.de_strategy, maxiter=config.max_iterations,
        popsize=max(1, int(np.ceil(config.population_size / problem.space.dimension))),
        mutation=config.de_mutation, recombination=config.de_recombination, tol=config.ftol,
        rng=np.random.default_rng(config.seed), workers=1, updating="immediate", polish=False,
        callback=lambda *args, **kwargs: ledger.check())
    return ("CONVERGED" if result.success else "STALLED"), str(result.message), version("scipy")



def _nsga_scores(points, objective_count):
    # Infinity is a transient adapter rejection, never a measured objective/constraint.
    f = np.array([p.objectives if p is not None else np.zeros(objective_count) for p in points])
    # Aggregate G reproduces declared weighted feasibility-first violation ordering.
    g = np.array([[p.total_violation if p is not None else np.inf] for p in points])
    return f, g


def _nsga2(problem, config, ledger):
    try:
        from pymoo.algorithms.moo.nsga2 import NSGA2, binary_tournament
        from pymoo.core.mixed import MixedVariableSampling, MixedVariableMating, MixedVariableDuplicateElimination
        from pymoo.core.variable import Real, Integer, Choice
        from pymoo.core.problem import Problem
        from pymoo.operators.selection.tournament import TournamentSelection
        from pymoo.operators.survival.rank_and_crowding import RankAndCrowding
        from pymoo.operators.crossover.sbx import SBX
        from pymoo.operators.crossover.ux import UX
        from pymoo.operators.mutation.pm import PM
        from pymoo.operators.mutation.rm import ChoiceRandomMutation
        from pymoo.operators.repair.rounding import RoundingRepair
    except ImportError as exc:
        raise ImportError("pymoo is required for NSGA-II; install caelab-workbench[mdo]") from exc
    variables = {}
    for v in problem.space.independent_variables:
        if isinstance(v, ContinuousVariable):
            variables[v.id] = Real(bounds=(0., 1.))
        elif isinstance(v, DiscreteVariable):
            variables[v.id] = Integer(bounds=(0, len(v.values) - 1))
        else:
            variables[v.id] = Choice(options=list(v.choices))
    duplicate = MixedVariableDuplicateElimination()
    mating = MixedVariableMating(selection=TournamentSelection(func_comp=binary_tournament),
        crossover={Real: SBX(prob=config.crossover_probability, eta=config.crossover_eta),
                   Integer: SBX(prob=config.crossover_probability, eta=config.crossover_eta, vtype=float, repair=RoundingRepair()),
                   Choice: UX(prob=config.crossover_probability)},
        mutation={Real: PM(eta=config.mutation_eta),
                  Integer: PM(eta=config.mutation_eta, vtype=float, repair=RoundingRepair()), Choice: ChoiceRandomMutation()},
        eliminate_duplicates=duplicate)
    algorithm = NSGA2(pop_size=config.population_size, sampling=MixedVariableSampling(), mating=mating,
                      eliminate_duplicates=duplicate, survival=RankAndCrowding())
    algorithm.tournament_type = "comp_by_rank_and_crowding"
    native_problem = Problem(vars=variables, n_obj=len(problem.objectives), n_ieq_constr=1)
    # pymoo 0.6.1.x uses global NumPy RNG; preserve caller state after this bounded adapter.
    rng_state = np.random.get_state()
    try:
        algorithm.setup(native_problem, termination=("n_gen", config.max_iterations), seed=config.seed, verbose=False)
        while algorithm.has_next():
            ledger.check()
            population = algorithm.ask()
            if population is None or len(population) == 0:
                return "STALLED", "no distinct offspring remain", version("pymoo")
            vectors = []
            for candidate in population.get("X"):
                vectors.append([v.choices.index(candidate[v.id]) if isinstance(v, CategoricalVariable) else candidate[v.id]
                                for v in problem.space.independent_variables])
            points = ledger.batch(vectors)
            f, g = _nsga_scores(points, len(problem.objectives))
            population.set("F", f)
            population.set("G", g)
            for individual in population:
                individual.evaluated.update(("F", "G", "H"))
            algorithm.tell(infills=population)
        return "BUDGET_EXHAUSTED", "configured generation limit reached; convergence not established", version("pymoo")
    finally:
        np.random.set_state(rng_state)


def optimize(problem, evaluator, config=None, cancel=None):
    """Execute single or multi-objective numerical optimization (SLSQP, DE, NSGA-II)."""
    config = config or OptimizationConfig()
    factors = problem.space.independent_variables
    if config.algorithm in ("slsqp", "de") and len(problem.objectives) > 1 and not config.weighted_sum:
        raise ValueError("scalar optimizers need explicit weighted_sum=True for multiple objectives")
    if config.algorithm == "slsqp":
        if not problem.differentiable or any(not isinstance(v, ContinuousVariable) for v in factors):
            raise ValueError("SLSQP requires declared differentiability and continuous independent factors")
        if any(o.sense == "TARGET" for o in problem.objectives):
            raise ValueError("SLSQP nonsmooth TARGET loss unsupported; choose a declared smooth objective")
    if config.algorithm == "de" and any(isinstance(v, CategoricalVariable) for v in factors):
        raise ValueError("DE does not assign numerical distance to categories; choose NSGA-II")
    ledger = _Ledger(problem, evaluator, config, cancel)
    library_version = "not_loaded"
    try:
        reason, message, library_version = {"slsqp": _slsqp, "de": _de, "nsga2": _nsga2}[config.algorithm](problem, config, ledger)
    except _Stop as stop:
        reason, message = stop.reason, stop.message
        package = "pymoo" if config.algorithm == "nsga2" else "scipy"
        try:
            library_version = version(package)
        except Exception:
            pass
    accepted = [p for p in ledger.archive if p.feasible]
    if reason not in ("CANCELLED", "FAILED"):
        if not ledger.archive:
            reason = "NO_VALID_EVALUATIONS"
        elif not accepted:
            reason = "NO_FEASIBLE_DESIGN"
    if accepted:
        if config.algorithm == "nsga2":
            indices = pareto_front([p.objectives for p in accepted], [p.point_id for p in accepted])
            selected = tuple(accepted[i].point_id for i in indices)
        else:
            selected = (min(accepted, key=lambda p: (_scalar(p, problem), p.point_id)).point_id,)
    else:
        selected = ()
    actual = tuple(p for p in ledger.archive if p.mode == "ACTUAL")
    predicted = tuple(p for p in ledger.archive if p.mode == "SURROGATE")
    usage = BudgetUsage(ledger.dispatches, ledger.calls, ledger.hits, ledger.duplicates, ledger.predictions, ledger.native_attempts,
                        len(ledger.failures), time.monotonic() - ledger.started)
    return OptimizationResult(termination_reason=reason, actual_archive=actual, predicted_archive=predicted,
        selected_ids=selected, failures=tuple(ledger.failures), algorithm=config.algorithm, algorithm_version=library_version,
        seed=config.seed, budget_usage=usage, message=message, trace=tuple(ledger.trace), config=config)
