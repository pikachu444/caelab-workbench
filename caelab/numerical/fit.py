"""Fitting: least-squares parameter identification and RBF surrogates.

Ported from the original ``caelab/numerical.py`` ``fit_model`` /
``fit_surrogate`` / ``Surrogate``. This port keeps the contracts that
matter and drops the parts tied to unported subsystems (frozen data
inclusions, finite-measure prediction, fit-detail retention packets):

- experiments are ``{"id", "role": "fit"|"holdout", "response": name,
  "observations": {...}, "weight", "scale", "settings"?}``;
- observations are scalar or one-dimensional series responses with
  explicit unit/component/location/reduction; series predictions are
  linearly interpolated on the observation axis, and extrapolation
  is refused (original rule);
- residuals are ``sqrt(weight / retained_count) * (prediction -
  observation) / scale`` with the mask fixed from finite observations
  before fitting (original rule);
- holdout experiments never tune: they are evaluated once, at the
  fitted parameters only.
"""
from __future__ import annotations

import math
from copy import deepcopy
from time import perf_counter

import numpy as np
from scipy.interpolate import RBFInterpolator
from scipy.optimize import least_squares

from .core import EvaluationFailed, _Runner, _number, scalar_value
from .variables import free_variables, full_values, normalize_variables


def _experiment(experiment):
    result = deepcopy(experiment)
    if not isinstance(result.get("id"), str) or not result["id"]:
        raise ValueError("Every experiment needs an ID")
    if result.get("role", "fit") not in ("fit", "holdout"):
        raise ValueError("Experiment role must be fit or holdout")
    result.setdefault("role", "fit")
    obs = result.get("observations")
    if not isinstance(obs, dict) or obs.get("kind") not in ("scalar", "series"):
        raise ValueError("Observations declare scalar or series responses "
                         "(event observations need the unported event subsystem)")
    for key in ("unit", "component", "location", "reduction"):
        if not isinstance(obs.get(key), str):
            raise ValueError(f"Observation {key} must be declared explicitly")
    values = np.asarray(obs.get("value"), dtype=float)
    if obs["kind"] == "scalar":
        values = values.reshape(1) if values.size == 1 else values
    if values.ndim != 1 or not len(values):
        raise ValueError("Observations must be a nonempty scalar or one-dimensional curve")
    mask = np.asarray(result.get("mask", np.isfinite(values)), dtype=bool)
    if mask.shape != values.shape or not mask.any() or not np.all(np.isfinite(values[mask])):
        raise ValueError("Fixed observation mask must retain finite values")
    result["_mask"] = mask
    obs["value"] = values
    if obs["kind"] == "series":
        axes = obs.get("axes", [])
        if len(axes) != 1 or not all(isinstance(axes[0].get(k), str) for k in ("name", "unit")):
            raise ValueError("A curve requires one named axis and its unit")
        axis = np.asarray(axes[0].get("values"), dtype=float)
        if axis.shape != values.shape or not np.all(np.isfinite(axis)) \
                or np.any(np.diff(axis) <= 0):
            raise ValueError("Observation axis must be finite and strictly increasing")
        axes[0]["values"] = axis
    result["weight"] = _number(result.get("weight", 1), "curve weight")
    result["scale"] = _number(result.get("scale", 1), "curve scale")
    if result["weight"] <= 0 or result["scale"] <= 0:
        raise ValueError("Curve weight and scale must be positive")
    if not isinstance(result.get("response"), str) or not result["response"]:
        raise ValueError("Each experiment must select a model response")
    return result


def _aligned(evaluation, experiment):
    """(aligned prediction, residual) for one experiment — original rules."""
    if evaluation.get("execution_status") != "SUCCEEDED":
        raise EvaluationFailed(str(evaluation.get("diagnostics", {}).get(
            "failure_reason", evaluation.get("execution_status"))))
    pred = evaluation.get("responses", {}).get(experiment["response"])
    obs = experiment["observations"]
    if pred is None:
        raise EvaluationFailed(f"Missing response {experiment['response']}")
    for key in ("kind", "unit", "component", "location", "reduction"):
        if pred.get(key) != obs[key]:
            raise ValueError(f"Experiment {experiment['id']} response {key} mismatch")
    values = np.asarray(pred["value"], dtype=float)
    if obs["kind"] == "scalar":
        if values.size != 1:
            raise ValueError("Scalar response must have exactly one value")
        aligned = values.reshape(1)
    else:
        axes = pred.get("axes", [])
        if len(axes) != 1 or any(axes[0].get(key) != obs["axes"][0][key]
                                 for key in ("name", "unit")):
            raise ValueError("Prediction and observation axis name/unit must match")
        axis = np.asarray(axes[0].get("values"), dtype=float)
        if values.ndim != 1 or axis.shape != values.shape or not len(axis) \
                or not np.all(np.isfinite(axis)) or np.any(np.diff(axis) <= 0):
            raise ValueError("Prediction axis must be finite and strictly increasing")
        target = obs["axes"][0]["values"]
        used = target[experiment["_mask"]]
        if used[0] < axis[0] or used[-1] > axis[-1]:
            raise ValueError("Observation axis lies outside prediction support; "
                             "extrapolation is forbidden")
        aligned = np.full(target.shape, np.nan)
        aligned[experiment["_mask"]] = np.interp(used, axis, values)
    used_values = aligned[experiment["_mask"]]
    if not np.all(np.isfinite(used_values)):
        raise EvaluationFailed("Predictions are nonfinite at retained observations")
    residual = (used_values - obs["value"][experiment["_mask"]]) \
        * math.sqrt(experiment["weight"] / len(used_values)) / experiment["scale"]
    return aligned, residual


def _nullable(value):
    if isinstance(value, np.ndarray):
        return _nullable(value.tolist())
    if isinstance(value, dict):
        return {k: _nullable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_nullable(v) for v in value]
    if isinstance(value, (float, np.floating)) and not math.isfinite(value):
        return None
    return value


def compare_curves(prediction, observation, *, weight=1, scale=1, mask=None):
    """Compare an in-memory prediction/observation pair (original contract)."""
    experiment = {"id": "comparison", "response": "response",
                  "observations": observation, "weight": weight, "scale": scale}
    if mask is not None:
        experiment["mask"] = mask
    experiment = _experiment(experiment)
    aligned, residual = _aligned(
        {"execution_status": "SUCCEEDED", "responses": {"response": prediction}},
        experiment)
    retained = experiment["_mask"]
    difference = aligned[retained] - experiment["observations"]["value"][retained]
    return {"method": "linear_axis_alignment", "unit": observation["unit"],
            "weight": weight, "scale": scale,
            "prediction": _nullable(aligned),
            "observation": _nullable(experiment["observations"]),
            "mask": retained.tolist(), "residual": residual,
            "rmse": float(np.sqrt(np.mean(difference ** 2))),
            "mae": float(np.mean(abs(difference))),
            "count": int(retained.sum()), "objective": float(residual @ residual)}


def fit_model(evaluator, variables, experiments, *, method="least_squares",
              options=None, execution=None):
    """Fit bounded coefficients to fit-role experiments by least squares.

    The evaluator is called as ``evaluator(values, settings)`` when it
    accepts two arguments, else ``evaluator(values)`` — per-experiment
    ``settings`` are the experiment's own dict (original: one prepared
    model per experiment). Returns fitted ``parameters``, per-experiment
    ``curves`` (predictions at the best point, incl. holdout), the full
    candidate ``candidates`` journal, and convergence state.
    """
    variables = normalize_variables(variables)
    if method not in ("least_squares",):
        raise ValueError("This port supports least_squares only; the original's "
                         "differential_evolution fit used the same journal and can "
                         "be layered on optimize()")
    experiments = [_experiment(e) for e in experiments]
    if len({e["id"] for e in experiments}) != len(experiments):
        raise ValueError("Experiment IDs must be distinct")
    fitted = [e for e in experiments if e["role"] == "fit"]
    holdout = [e for e in experiments if e["role"] == "holdout"]
    if not fitted:
        raise ValueError("At least one fit experiment is required")
    opts = dict(options or {})
    allowed = {"jac", "ftol", "xtol", "gtol", "x_scale", "loss", "f_scale",
               "diff_step", "max_nfev", "verbose"}
    if set(opts) - allowed:
        raise ValueError(f"Unknown least-squares options: {sorted(set(opts) - allowed)}")
    free = free_variables(variables)
    names = [v["id"] for v in free]
    x0 = np.asarray([v["value"] for v in free], dtype=float)
    lower = [v["lower"] for v in free]
    upper = [v["upper"] for v in free]

    import inspect

    fn = getattr(evaluator, "evaluate", evaluator)
    try:
        params = [p for p in inspect.signature(fn).parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        takes_settings = len(params) >= 2
    except (TypeError, ValueError):
        takes_settings = False

    def call_evaluator(values, settings):
        return fn(values, settings) if takes_settings else fn(values)

    runner = _Runner(None, execution)  # budget/timing only; calls are custom below
    rows = []

    def evaluate_all(values, exps):
        from .core import EvaluationBudgetExceeded, _failure, _normalize_evaluation
        outputs = []
        for exp in exps:
            if runner.limit is not None and runner.calls >= runner.limit:
                raise EvaluationBudgetExceeded("Evaluation budget exhausted")
            try:
                raw = call_evaluator(deepcopy(values), deepcopy(exp.get("settings", {})))
                outputs.append(_normalize_evaluation(raw))
            except Exception as error:  # noqa: BLE001 - retained as a failed row
                outputs.append(_failure(f"{type(error).__name__}: {error}"))
            runner.calls += 1
        return outputs

    def residual_at(x):
        values = full_values(variables, {name: float(value) for name, value
                                         in zip(names, np.asarray(x, dtype=float))})
        outputs = evaluate_all(values, fitted)
        residuals, reasons = [], []
        for exp, evaluation in zip(fitted, outputs):
            try:
                residuals.append(_aligned(evaluation, exp)[1])
            except EvaluationFailed as error:
                reasons.append(f"{exp['id']}: {error}")
        row = {"candidate_id": f"C{len(rows):06d}", "values": values,
               "responses": {}, "execution_status": "FAILED" if reasons else "SUCCEEDED",
               "failure_reason": "; ".join(reasons) if reasons else None,
               "objective": None}
        rows.append(row)
        if reasons:
            raise EvaluationFailed(row["failure_reason"])
        residual = np.concatenate(residuals)
        row["objective"] = float(residual @ residual)
        return residual

    started = perf_counter()
    termination, convergence, best_values, failure_reason = None, False, None, None
    if not free:
        termination = "NO_FREE_VARIABLES"
        best_values = full_values(variables, {})
        try:
            residual_at(np.asarray([], dtype=float))
        except EvaluationFailed as error:
            failure_reason = str(error)
    else:
        try:
            result = least_squares(residual_at, x0, bounds=(lower, upper), **opts)
            best_values = full_values(variables, {name: float(value) for name, value
                                                  in zip(names, result.x)})
            termination = str(result.message)
            convergence = bool(result.success)
        except EvaluationFailed as error:
            termination, failure_reason = "EVALUATION_FAILED", str(error)
    eligible = [r for r in rows if r["execution_status"] == "SUCCEEDED"
                and r["objective"] is not None]
    if best_values is None and eligible:
        best_values = min(eligible, key=lambda r: r["objective"])["values"]
    curves = []
    if best_values is not None:
        for exp, evaluation in zip(fitted + holdout,
                                   evaluate_all(best_values, fitted + holdout)):
            curve = {"experiment_id": exp["id"], "role": exp["role"],
                     "response": exp["response"],
                     "observations": _nullable(deepcopy(
                         {k: v for k, v in exp["observations"].items()})),
                     "mask": exp["_mask"].tolist(), "weight": exp["weight"],
                     "scale": exp["scale"],
                     "execution_status": evaluation.get("execution_status", "FAILED")}
            try:
                prediction, residual = _aligned(evaluation, exp)
                curve.update(prediction=_nullable(prediction), residual=residual,
                             rmse=float(np.sqrt(np.mean(
                                 (prediction[exp["_mask"]]
                                  - exp["observations"]["value"][exp["_mask"]]) ** 2))))
            except EvaluationFailed as error:
                curve.update(execution_status="FAILED", failure_reason=str(error))
            curves.append(curve)
    timing = runner.timing()
    timing["total_seconds"] = perf_counter() - started
    return {"kind": "fit", "method": method, "variables": variables,
            "parameters": best_values,
            "parameter_units": {v["id"]: v["unit"] for v in variables},
            "curves": curves, "candidates": rows,
            "execution_status": "FAILED" if failure_reason or best_values is None
            else "SUCCEEDED",
            "converged": convergence, "termination_reason": termination,
            "failure_reason": failure_reason, "timing": timing,
            "limitations": [
                "Small residuals do not establish parameter uniqueness or "
                "physical qualification.",
                "Fit and holdout are separated by experiment; no holdout data "
                "tunes the coefficients."]}


# ------------------------------------------------------------- surrogate

class Surrogate:
    """SciPy RBF interpolation with a disjoint confirmation table (original)."""

    def __init__(self, model, variables, response, validation, support):
        self.model, self.variables, self.response = model, variables, response
        self.validation, self.support = validation, support

    def predict(self, candidates):
        names = [v["id"] for v in self.variables]
        if not candidates:
            return []
        if any(set(row) != set(names) for row in candidates):
            raise ValueError("Prediction points require exactly the stored model variables")
        x = np.asarray([[_number(row[name], name) for name in names] for row in candidates])
        low = np.asarray([v["lower"] for v in self.variables])
        high = np.asarray([v["upper"] for v in self.variables])
        predictions = self.model((x - low) / (high - low))
        return [{"candidate_id": f"P{i:06d}",
                 "values": dict(zip(names, (float(v) for v in row))),
                 "execution_status": "PREDICTED",
                 "out_of_range": bool(np.any((row < self.support[0]) | (row > self.support[1]))),
                 "responses": {self.response["response"]: {
                     "kind": "scalar", "value": float(value),
                     "unit": self.response["unit"],
                     "component": self.response.get("component", ""),
                     "location": self.response.get("location", ""),
                     "reduction": "surrogate prediction"}}}
                for i, (row, value) in enumerate(zip(x, predictions))]


def fit_surrogate(training, holdout, response, *, variables=None, options=None):
    """Fit SciPy RBF on training rows only; report independent holdout errors."""
    variables = normalize_variables(variables or training["variables"])
    response = dict(response)
    response["response"] = response.get("response", response.get("metric"))
    train, test = training["candidates"], holdout["candidates"]
    if not train or not test:
        raise ValueError("Separate nonempty training and holdout tables are required")
    names = [v["id"] for v in variables]
    train_x = np.asarray([[row["values"][name] for name in names] for row in train],
                         dtype=float)
    test_x = np.asarray([[row["values"][name] for name in names] for row in test],
                        dtype=float)
    if len({tuple(r) for r in train_x}) != len(train_x):
        raise ValueError("Duplicate training parameter points are not independent observations")
    if {tuple(r) for r in train_x} & {tuple(r) for r in test_x}:
        raise ValueError("Training and holdout parameter points overlap")
    low = np.asarray([v["lower"] for v in variables])
    high = np.asarray([v["upper"] for v in variables])
    if not np.all(np.isfinite(train_x)) or not np.all(np.isfinite(test_x)) \
            or np.any((train_x < low) | (train_x > high)) \
            or np.any((test_x < low) | (test_x > high)):
        raise ValueError("Training/holdout coordinates must be finite and within bounds")
    support = train_x.min(axis=0), train_x.max(axis=0)
    train_y = np.asarray([scalar_value(row, response) for row in train])
    test_y = np.asarray([scalar_value(row, response) for row in test])
    opts = dict(options or {})
    if set(opts) - {"neighbors", "smoothing", "kernel", "epsilon", "degree"}:
        raise ValueError("Unknown SciPy RBF options")
    model = RBFInterpolator((train_x - low) / (high - low), train_y, **opts)
    predictions = model((test_x - low) / (high - low))
    error = predictions - test_y
    validation = {"method": "scipy.interpolate.RBFInterpolator", "options": opts,
                  "train_ids": [r["candidate_id"] for r in train],
                  "holdout_ids": [r["candidate_id"] for r in test],
                  "holdout_prediction": predictions.tolist(),
                  "holdout_observed": test_y.tolist(),
                  "holdout_residual": error.tolist(),
                  "mae": float(np.mean(abs(error))),
                  "rmse": float(np.sqrt(np.mean(error ** 2))),
                  "max_error": float(np.max(abs(error))),
                  "unit": response["unit"],
                  "training_support_bounds": [support[0].tolist(), support[1].tolist()],
                  "holdout_out_of_range": np.any(
                      (test_x < support[0]) | (test_x > support[1]), axis=1).tolist(),
                  "limitations": ["Hyperparameters are supplied before holdout "
                                  "evaluation; predictions are not solver evaluations."]}
    return Surrogate(model, variables, response, validation, support)
