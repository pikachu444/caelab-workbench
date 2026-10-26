"""Core numerical engine: DOE sampling and differential-evolution
optimization over a plain callable evaluator.

Ported from the original repository's ``caelab/numerical.py``
(``run_doe`` / ``optimize`` / ``_scalar``) and
``caelab/optimizers/scipy_de.py``. The original evaluator went through
the old ``caelab.evaluation.prepare`` backend machinery, which the
redesign does not have; here the evaluator contract is direct:

    evaluator(values: dict) -> evaluation dict

where an evaluation dict is::

    {"execution_status": "SUCCEEDED",
     "responses": {"disp": {"kind": "scalar", "value": 1.2, "unit": "m"},
                   "curve": {"kind": "series", "value": [...],
                             "unit": "N", "axes": [{"name": "t",
                             "unit": "s", "values": [...]}]}},
     "checks": [...], "diagnostics": {...}}

For convenience an evaluator may also return a plain
``{response_name: float}`` mapping (treated as unit-less scalars only
when the caller declares no unit), or expose ``.evaluate(values)``.
Failed evaluations are retained as rows with their failure reason;
they are never replaced by invented numbers. Execution is serial;
the original's process/batch modes are not ported (see the merge
notes in the module package docstring).
"""
from __future__ import annotations

import math
from copy import deepcopy
from time import perf_counter

import numpy as np
import scipy
from scipy.optimize import NonlinearConstraint, differential_evolution
from scipy.stats import qmc

from .variables import (check_values, decode, encode, encoded_bounds,
                        free_variables, full_values, normalize_variables)


class EvaluationFailed(RuntimeError):
    """An actual evaluation cannot supply the declared numerical response."""


class EvaluationBudgetExceeded(RuntimeError):
    """A study would exceed its declared evaluation budget."""
    budget_exhausted = True


class EvaluationCancelled(RuntimeError):
    """Cooperative cancellation between evaluations; completed rows survive."""


def _number(value, label):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.number)):
        raise ValueError(f"{label} must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return value


def _failure(reason, status="FAILED"):
    return {"execution_status": status, "responses": {}, "checks": [],
            "diagnostics": {"failure_reason": str(reason)}}


def _normalize_evaluation(raw):
    """Bring evaluator returns to the evaluation-dict contract."""
    if raw is None:
        return _failure("evaluator returned None")
    if isinstance(raw, dict) and "execution_status" in raw:
        out = deepcopy(raw)
        out.setdefault("responses", {})
        out.setdefault("checks", [])
        out.setdefault("diagnostics", {})
        return out
    if isinstance(raw, dict) and "responses" in raw:
        out = {"execution_status": "SUCCEEDED", "checks": [], "diagnostics": {}}
        out["responses"] = deepcopy(raw["responses"])
        return out
    if isinstance(raw, dict):  # plain {name: number} mapping
        responses = {}
        for name, value in raw.items():
            responses[name] = {"kind": "scalar", "value": _number(value, name),
                               "unit": "1"}
        return {"execution_status": "SUCCEEDED", "responses": responses,
                "checks": [], "diagnostics": {}}
    return _failure(f"unsupported evaluator return type: {type(raw).__name__}")


def evaluate_once(evaluator, values):
    """One evaluation; exceptions become FAILED rows, never raised numbers."""
    started = perf_counter()
    try:
        fn = getattr(evaluator, "evaluate", evaluator)
        raw = fn(deepcopy(values))
        result = _normalize_evaluation(raw)
    except EvaluationBudgetExceeded:
        raise
    except Exception as error:  # noqa: BLE001 - retained as a failed row, as original
        result = _failure(f"{type(error).__name__}: {error}")
    return result, perf_counter() - started


class _Runner:
    """Serial evaluation with an optional total-evaluation budget."""

    def __init__(self, evaluator, execution=None):
        opts = dict(execution or {})
        unknown = set(opts) - {"max_evaluations", "on_failure", "cancel_requested"}
        if unknown:
            raise ValueError(f"Unknown execution options: {sorted(unknown)} "
                             "(this port is serial-only)")
        if opts.get("on_failure", "continue") not in ("continue", "stop"):
            raise ValueError("on_failure must be continue or stop")
        limit = opts.get("max_evaluations")
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("max_evaluations must be a positive integer")
        self.evaluator = evaluator
        self.on_failure = opts.get("on_failure", "continue")
        self.limit = limit
        self.calls = 0
        self.seconds = 0.0
        self.cancel_requested = opts.get("cancel_requested")
        if self.cancel_requested is not None and not callable(self.cancel_requested):
            raise ValueError("cancel_requested must be callable")

    def check_cancel(self):
        if self.cancel_requested is not None and self.cancel_requested():
            raise EvaluationCancelled("CANCELLED")

    def one(self, values):
        self.check_cancel()
        if self.limit is not None and self.calls >= self.limit:
            raise EvaluationBudgetExceeded(
                f"Evaluation budget {self.limit} exhausted after {self.calls} evaluations")
        result, elapsed = evaluate_once(self.evaluator, values)
        self.calls += 1
        self.seconds += elapsed
        return result

    def timing(self):
        return {"model_calls": self.calls, "model_seconds_sum": self.seconds,
                "mode": "serial", "workers": 1, "max_evaluations": self.limit,
                "dispatched_evaluations_total": self.calls}


def _reason(evaluation):
    if evaluation.get("execution_status") == "SUCCEEDED":
        return None
    diagnostics = evaluation.get("diagnostics", {})
    return diagnostics.get("failure_reason", diagnostics.get(
        "reason", evaluation.get("execution_status", "Missing status")))


def _row(identifier, values, evaluation):
    return {"candidate_id": identifier, "values": deepcopy(values),
            "execution_status": evaluation.get("execution_status", "FAILED"),
            "failure_reason": _reason(evaluation),
            "responses": deepcopy(evaluation.get("responses", {})),
            "checks": deepcopy(evaluation.get("checks", [])),
            "diagnostics": deepcopy(evaluation.get("diagnostics", {}))}


def scalar_value(evaluation, definition):
    """The original ``_scalar``: extract one declared scalar response.

    ``definition`` is ``{"response": name, "unit": unit, "reduction": ...}``.
    Series/field responses need an explicit max/min/final/mean/abs_max
    reduction. A unit mismatch is an error, never a silent conversion.
    Predicted rows are refused: a prediction is not an evaluation.
    """
    if evaluation.get("prediction_status") == "PREDICTED" or \
            evaluation.get("execution_status") == "PREDICTED":
        raise EvaluationFailed("PREDICTED: this response is not an actual evaluation")
    if evaluation.get("execution_status") != "SUCCEEDED":
        raise EvaluationFailed(str(_reason(evaluation)))
    name = definition.get("response", definition.get("metric"))
    response = evaluation.get("responses", {}).get(name)
    if response is None:
        response = next((entry for entry in evaluation.get("response_reductions", [])
                         if entry.get("response") == name
                         and entry.get("reduction") == definition.get("reduction")), None)
    if response is None:
        raise EvaluationFailed(f"{name} is not an available scalar response")
    if definition.get("unit") is not None and response.get("unit") != definition.get("unit"):
        raise ValueError(f"{name} response unit differs from the declared unit")
    for key in ("component", "location", "measure", "frame",
                "coordinate_system", "coordinate_frame"):
        if key in definition and response.get(key) != definition[key]:
            raise ValueError(f"{name} response {key} differs from declaration")
    if response.get("kind") == "scalar":
        if "reduction" in definition and \
                definition["reduction"] != response.get("reduction", "none"):
            raise ValueError(f"{name} scalar reduction differs from declaration")
        return _number(response["value"], name)
    reduction = definition.get("reduction")
    if response.get("kind") not in ("series", "field") or \
            reduction not in ("max", "min", "final", "mean", "abs_max"):
        raise ValueError(f"{name}: a non-scalar response requires an explicit "
                         "max/min/final/mean/abs_max reduction")
    data = np.asarray(response.get("value"), dtype=float)
    if not data.size or not np.all(np.isfinite(data)):
        raise EvaluationFailed(f"{name} cannot be reduced from missing/nonfinite data")
    if reduction == "final":
        axes = response.get("axes", [])
        if response["kind"] != "series" or data.ndim != 1 or len(axes) != 1:
            raise ValueError("final reduction requires a one-dimensional series and its axis")
        coordinates = np.asarray(axes[0].get("values"), dtype=float)
        if coordinates.shape != data.shape or not np.all(np.isfinite(coordinates)) \
                or np.any(np.diff(coordinates) <= 0):
            raise ValueError("final reduction requires strictly increasing finite coordinates")
        value = data[-1]
    else:
        value = {"max": np.max, "min": np.min, "mean": np.mean,
                 "abs_max": lambda x: np.max(abs(x))}[reduction](data)
    return _number(value, name)


# ---------------------------------------------------------------- DOE

def _lhs_points(variables, count, seed):
    """SciPy Latin-hypercube sample in physical coordinates (original rule):
    continuous axes are linear/log mapped, integer axes use uniform bins."""
    free = free_variables(variables)
    if not free:
        return [{} for _ in range(count)]
    cube = qmc.LatinHypercube(len(free), rng=np.random.default_rng(seed)).random(count)
    points = []
    for row in cube:
        values = {}
        for v, u in zip(free, row):
            u = float(u)
            if v["kind"] == "integer":
                low, high = math.ceil(v["lower"]), math.floor(v["upper"])
                value = low + min(high - low, math.floor(u * (high - low + 1)))
            elif v["transform"] == "log":
                value = math.exp(math.log(v["lower"]) + u * (math.log(v["upper"]) - math.log(v["lower"])))
            else:
                value = v["lower"] + u * (v["upper"] - v["lower"])
            values[v["id"]] = value
        points.append(check_values(variables, values))
    return points


def run_doe(evaluator, variables, *, count=16, seed=0, candidates=None, execution=None):
    """Evaluate specified candidates or a seeded SciPy LHS design.

    Returns the original table shape: ``{"kind": "doe", "variables": ...,
    "sampling": ..., "candidates": [rows], "execution_status": ...,
    "timing": ...}``. Failures keep their candidate IDs and reasons.
    ``execution['cancel_requested']`` may be a callable; a true result stops
    before the next evaluation and returns CANCELLED with completed rows only.
    """
    variables = normalize_variables(variables)
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be an unsigned 32-bit integer")
    if candidates is None:
        if type(count) is not int or count < 1:
            raise ValueError("count must be a positive integer")
        free_points = _lhs_points(variables, count, seed)
        sampling = {"method": "scipy.latin_hypercube", "seed": seed, "count": count,
                    "scipy_version": scipy.__version__,
                    "coordinates": [v["transform"] for v in free_variables(variables)]}
        pending = [{"candidate_id": f"C{i:06d}", "values": full_values(variables, p)}
                   for i, p in enumerate(free_points)]
    else:
        pending = []
        seen = set()
        for i, candidate in enumerate(candidates):
            values = candidate.get("values", candidate) if isinstance(candidate, dict) else candidate
            identifier = candidate.get("candidate_id", f"C{i:06d}") \
                if isinstance(candidate, dict) and "values" in candidate else f"C{i:06d}"
            if identifier in seen:
                raise ValueError("Candidate IDs must be distinct")
            seen.add(identifier)
            free = check_values(variables, values)
            pending.append({"candidate_id": identifier,
                            "values": full_values(variables, free)})
        sampling = {"method": "specified", "count": len(pending)}
        if not pending:
            raise ValueError("At least one candidate is required")
    rows, stop_reason = [], None
    started = perf_counter()
    runner = _Runner(evaluator, execution)
    for item in pending:
        if stop_reason:
            status = "REJECTED" if stop_reason == "EVALUATION_BUDGET_EXHAUSTED" else "FAILED"
            rows.append(_row(item["candidate_id"], item["values"],
                             _failure(stop_reason, status)))
            continue
        try:
            evaluation = runner.one(item["values"])
        except EvaluationCancelled:
            stop_reason = "CANCELLED"
            break
        except EvaluationBudgetExceeded:
            stop_reason = "EVALUATION_BUDGET_EXHAUSTED"
            rows.append(_row(item["candidate_id"], item["values"],
                             _failure(stop_reason, "REJECTED")))
            continue
        rows.append(_row(item["candidate_id"], item["values"], evaluation))
        if rows[-1]["execution_status"] != "SUCCEEDED" and runner.on_failure == "stop":
            stop_reason = "STOPPED_AFTER_FAILURE"
    timing = runner.timing()
    timing["total_seconds"] = perf_counter() - started
    status = ("CANCELLED" if stop_reason == "CANCELLED" else
              "REJECTED" if stop_reason == "EVALUATION_BUDGET_EXHAUSTED" else
              "PARTIAL_FAILURE" if stop_reason or
              any(r["execution_status"] != "SUCCEEDED" for r in rows) else "SUCCEEDED")
    return {"kind": "doe", "variables": variables, "sampling": sampling,
            "candidates": rows, "execution_status": status,
            "termination_reason": stop_reason or "ALL_CANDIDATES_EVALUATED",
            "timing": timing}


# ---------------------------------------------------------- optimize

def optimize(evaluator, variables, objective, *, constraints=None, seed=0,
             options=None, execution=None, method="differential_evolution"):
    """Differential evolution over actual responses (original contract).

    ``objective`` is a scalar-response definition plus ``direction``
    (minimize/maximize) and optional positive ``scale``. Constraints are
    response definitions plus ``operator`` (``<=``/``>=``), ``limit`` and
    optional ``scale``; residuals are stored dimensionless, ``<= 0`` is
    feasible. Failed evaluations are journalled as rows and never rank
    as feasible. Termination does not prove global optimality.
    """
    if method != "differential_evolution":
        raise ValueError("This port supports differential_evolution only; the original's "
                         "slsqp_adjoint needs the unported adjoint subsystem")
    variables = normalize_variables(variables)
    constraints = list(constraints or [])
    direction = objective.get("direction", "minimize")
    if direction not in ("minimize", "maximize"):
        raise ValueError("Objective direction must be minimize or maximize")
    scale = _number(objective.get("scale", 1), "objective scale")
    if scale <= 0:
        raise ValueError("Objective scale must be positive")
    for constraint in constraints:
        if constraint.get("operator") not in ("<=", ">="):
            raise ValueError("Constraint operator must be <= or >=")
        _number(constraint.get("limit"), "constraint limit")
        if _number(constraint.get("scale", 1), "constraint scale") <= 0:
            raise ValueError("Constraint scale must be positive")
    opts = dict(options or {})
    if set(opts) - {"max_generations", "population_size", "initial_values"}:
        raise ValueError(f"Unknown DE options: {sorted(set(opts) - {'max_generations', 'population_size', 'initial_values'})}")
    max_generations = opts.get("max_generations", 40)
    population_size = opts.get("population_size", 12)
    if type(max_generations) is not int or max_generations < 1:
        raise ValueError("max_generations must be a positive integer")
    if type(population_size) is not int or population_size < 5:
        raise ValueError("population_size must be an integer of at least 5")
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be an unsigned 32-bit integer")

    runner = _Runner(evaluator, execution)
    rows, cache = [], {}
    started = perf_counter()
    termination = None

    def feedback(full_free_values):
        """Evaluate (or reuse) one point; returns SciPy-facing feedback."""
        runner.check_cancel()
        values = full_values(variables, full_free_values)
        key = tuple(float(values[v["id"]]).hex() if isinstance(values[v["id"]], float)
                    else values[v["id"]] for v in free_variables(variables))
        if key in cache:
            return cache[key]
        evaluation = runner.one(values)
        row = _row(f"C{len(rows):06d}", values, evaluation)
        row.update(objective=None, constraint_residuals=[None] * len(constraints),
                   feasible=False)
        rows.append(row)
        numeric, residuals = None, [None] * len(constraints)
        try:
            value = scalar_value(evaluation, objective)
            residuals = [(scalar_value(evaluation, c) - c["limit"]) / c.get("scale", 1)
                         * (1 if c["operator"] == "<=" else -1) for c in constraints]
            row.update(objective=value, constraint_residuals=residuals,
                       feasible=all(r <= 0 for r in residuals))
            numeric = value / scale * (1 if direction == "minimize" else -1)
        except EvaluationFailed as error:
            row["failure_reason"] = str(error)
        cache[key] = {"objective": numeric, "constraint_residuals": residuals}
        return cache[key]

    free = free_variables(variables)
    if not free:
        try:
            feedback({})
        except EvaluationCancelled:
            termination = "CANCELLED"
        except EvaluationBudgetExceeded:
            termination = "EVALUATION_BUDGET_EXHAUSTED"
        algorithm = {"engine": "scipy.differential_evolution", "solver_called": False,
                     "reason": "No free variables; one fixed assignment evaluated"}
    else:
        names, bounds, integrality = encoded_bounds(variables)
        initial = opts.get("initial_values")
        x0 = None
        init_population = "sobol"
        if initial is not None:
            encoded_initial = encode(variables, initial)
            x0 = np.asarray([encoded_initial[n] for n in names], dtype=float)
            # SciPy: x0 replaces the first member of the initial population.
            pop_points = _lhs_points(variables, population_size, seed)
            pop_encoded = [encode(variables, p) for p in pop_points]
            init_population = np.asarray([[e[n] for n in names] for e in pop_encoded],
                                         dtype=float)
            init_population[0] = x0

        def scipy_objective(x):
            values = decode(variables, dict(zip(names, np.asarray(x, dtype=float))))
            return np.inf if feedback(values)["objective"] is None \
                else feedback(values)["objective"]

        def scipy_constraints(x):
            values = decode(variables, dict(zip(names, np.asarray(x, dtype=float))))
            fb = feedback(values)
            available = fb["objective"] is not None and \
                all(r is not None for r in fb["constraint_residuals"])
            return np.asarray([np.inf if r is None else r
                               for r in fb["constraint_residuals"]]
                              + [0.0 if available else np.inf], dtype=float)

        try:
            result = differential_evolution(
                scipy_objective, bounds, strategy="best1bin", maxiter=max_generations,
                tol=1e-8, atol=1e-12, mutation=(0.5, 1.0), recombination=0.7,
                rng=np.random.default_rng(seed), polish=False, init=init_population,
                x0=x0, updating="deferred", workers=1, integrality=integrality,
                constraints=NonlinearConstraint(scipy_constraints, -np.inf, 0.0))
            termination = "CONVERGED" if result.success else \
                ("MAX_GENERATIONS" if int(result.nit) >= max_generations else "NOT_CONVERGED")
            algorithm = {"engine": "scipy.differential_evolution",
                         "scipy_version": scipy.__version__, "seed": seed,
                         "generations": int(result.nit),
                         "strategy": "best1bin", "mutation": [0.5, 1.0],
                         "recombination": 0.7, "polish": False}
        except EvaluationCancelled:
            termination = "CANCELLED"
            algorithm = {"engine": "scipy.differential_evolution", "seed": seed}
        except EvaluationBudgetExceeded:
            termination = "EVALUATION_BUDGET_EXHAUSTED"
            algorithm = {"engine": "scipy.differential_evolution", "seed": seed}

    eligible = [r for r in rows if r["feasible"] and r["objective"] is not None]
    best = deepcopy(min(eligible, key=lambda r: r["objective"]
                        * (1 if direction == "minimize" else -1))) if eligible else None
    if termination is None:
        termination = "NO_FREE_VARIABLES"
    timing = runner.timing()
    timing["total_seconds"] = perf_counter() - started
    status = ("CANCELLED" if termination == "CANCELLED" else
              "REJECTED" if termination == "EVALUATION_BUDGET_EXHAUSTED" else
              "PARTIAL_FAILURE" if termination == "STOPPED_AFTER_FAILURE" else
              "SUCCEEDED" if best else "FAILED")
    return {"kind": "optimization", "variables": variables,
            "objective_definition": deepcopy(objective),
            "constraint_definitions": constraints, "algorithm": algorithm,
            "candidates": rows, "best": best, "execution_status": status,
            "termination_reason": termination, "timing": timing,
            "limitations": ["Termination does not prove global optimality; "
                            "a warm start is a new optimization run."]}
