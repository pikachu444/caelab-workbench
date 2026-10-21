"""Candidate-table analysis: statistics, regression sensitivity, Pareto.

Ported from the original ``caelab/numerical.py`` ``analyze_candidates``
and ``caelab/optimizers/campaign_analysis.py`` (``_statistics`` /
``_sensitivity`` / ``_pareto`` / limitations). One common eligible
cohort is used by every output: rows that failed, are predictions, or
lack a valid requested response are excluded *as whole rows*, with
their IDs retained in ``exclusions`` — missing responses are never
replaced with zero (original rule).

The "sensitivity" here is the original's normalized multivariate
least-squares association, *not* a Sobol index; Sobol lives in
``sensitivity.py`` and needs its own matched sampling plan.
"""
from __future__ import annotations

import math
from copy import deepcopy

import numpy as np

from .core import EvaluationFailed, scalar_value
from .variables import normalize_variables

LIMITATIONS = [
    "Statistics describe the eligible empirical DOE samples only; no population "
    "probability, confidence interval, failure probability or input origin is inferred.",
    "Normalized multivariate least-squares coefficients describe conditional "
    "sample associations, not causal effects or Sobol sensitivity indices.",
    "Pareto membership compares only eligible saved sample responses with their "
    "declared directions and exact units; it is not global optimality or physical feasibility.",
    "Failed or invalid rows are excluded as a common cohort without zero substitution. "
    "Material, model and physical qualification remain outside this analysis.",
]

_MAX_CONDITION = 1e12


def _statistics(values, definition):
    row = {"metric": definition["metric"], "unit": definition["unit"],
           "count": len(values), "mean": None, "std": None, "min": None,
           "max": None, "quantiles": {"q05": None, "q50": None, "q95": None}}
    if not len(values):
        return row
    row.update(mean=float(np.mean(values)),
               std=float(np.std(values, ddof=1)) if len(values) > 1 else None,
               min=float(np.min(values)), max=float(np.max(values)))
    row["quantiles"] = {key: float(value) for key, value in
                        zip(("q05", "q50", "q95"), np.quantile(values, [.05, .5, .95]))}
    return row


def _regression(design, values):
    try:
        coefficients, _, rank, singular = np.linalg.lstsq(design, values, rcond=None)
    except np.linalg.LinAlgError:
        return None, 0, None, "LINALG_FAILURE"
    rank = int(rank)
    if design.shape[0] < design.shape[1]:
        return None, rank, None, "INSUFFICIENT_SAMPLES"
    if rank != design.shape[1]:
        return None, rank, None, "RANK_DEFICIENT"
    condition = float(singular[0] / singular[-1])
    if not math.isfinite(condition):
        return None, rank, None, "NUMERIC_UNREPRESENTABLE"
    if condition > _MAX_CONDITION:
        return None, rank, condition, "ILL_CONDITIONED"
    if not np.all(np.isfinite(coefficients)):
        return None, rank, None, "NUMERIC_UNREPRESENTABLE"
    return coefficients, rank, condition, None


def _sensitivity(x, y, variables, definition, ids):
    row = {"metric": definition["metric"], "unit": definition["unit"],
           "method": "NORMALIZED_MULTIVARIATE_LEAST_SQUARES", "valid": False,
           "reason": "INSUFFICIENT_SAMPLES", "rank": 0, "condition_number": None,
           "coefficients": [], "sample_ids": list(ids), "intercept": None,
           "response_mean": None, "response_std": None}
    if not len(y):
        return row
    row["response_mean"] = float(np.mean(y))
    row["response_std"] = float(np.std(y, ddof=1)) if len(y) > 1 else None
    if len(y) < 2:
        return row
    columns = []
    for column in x.T:
        std = float(np.std(column, ddof=1))
        if std == 0:
            row["reason"] = "CONSTANT_INPUT"
            return row
        columns.append((column - float(np.mean(column))) / std)
    if row["response_std"] == 0:
        row["reason"] = "CONSTANT_RESPONSE"
        return row
    design = np.column_stack([np.ones(len(y)), *columns])
    normalized_y = (y - row["response_mean"]) / row["response_std"]
    coefficients, rank, condition, reason = _regression(design, normalized_y)
    row.update(rank=rank, condition_number=condition, reason=reason)
    if coefficients is None:
        return row
    row.update(valid=True, intercept=float(coefficients[0]), coefficients=[
        {"parameter_id": v["id"], "unit": "1", "parameter_unit": v["unit"],
         "coefficient": float(value)}
        for v, value in zip(variables, coefficients[1:])])
    return row


def _pareto(y, definitions, ids):
    row = {"response_definitions": deepcopy(definitions), "nondominated_ids": [],
           "method": "EXACT_COMPONENTWISE_NONDOMINANCE", "valid": False,
           "reason": "REQUIRES_TWO_RESPONSES"}
    if len(definitions) < 2:
        return row
    if not ids:
        row["reason"] = "NO_ELIGIBLE_SAMPLES"
        return row
    oriented = y * np.array([1 if d["direction"] == "minimize" else -1
                              for d in definitions])
    row["nondominated_ids"] = [
        identifier for index, identifier in enumerate(ids)
        if not np.any(np.all(oriented <= oriented[index], axis=1)
                      & np.any(oriented < oriented[index], axis=1))]
    row.update(valid=True, reason=None)
    return row


def analyze_candidates(table, responses, *, seed=13):
    """Analyze a DOE/UQ candidate table over declared scalar responses.

    ``responses`` items are response definitions (``{"response": name,
    "unit": ..., "direction": "minimize"|"maximize", "reduction": ...}``)
    or bare names when every stored response is a scalar with one unit.
    Returns statistics / sensitivity / pareto / exclusions, in the
    original's shape.
    """
    variables = normalize_variables(table["variables"])
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("Analysis seed must be an unsigned 32-bit integer")
    declarations = []
    for requested in responses:
        declaration = {"response": requested} if isinstance(requested, str) else dict(requested)
        name = declaration.get("response", declaration.get("metric"))
        declaration["response"] = name
        if "unit" not in declaration:
            available = [row["responses"][name] for row in table["candidates"]
                         if name in row.get("responses", {})]
            units = {r.get("unit") for r in available}
            if len(units) != 1 or None in units:
                raise ValueError(f"{name}: an explicit unit is required for "
                                 "unavailable or inconsistent responses")
            declaration["unit"] = units.pop()
        declarations.append(declaration)
    definitions = [{"metric": d.get("id", d["response"]), "unit": d["unit"],
                    "direction": d.get("direction", "minimize")} for d in declarations]
    if not definitions or len({d["metric"] for d in definitions}) != len(definitions) \
            or any(d["direction"] not in ("minimize", "maximize") for d in definitions):
        raise ValueError("Declare distinct named scalar responses, their units and directions")

    samples = []
    for row in table["candidates"]:
        metrics = {}
        for definition, declaration in zip(definitions, declarations):
            # scalar_value keys on the response name, not the metric alias.
            probe = dict(declaration, response=declaration["response"])
            try:
                value = scalar_value(row, probe)
                metrics[definition["metric"]] = {"value": value,
                                                 "unit": definition["unit"],
                                                 "valid": True}
            except (EvaluationFailed, ValueError) as error:
                metrics[definition["metric"]] = {
                    "value": None, "unit": definition["unit"], "valid": False,
                    "reason": row.get("failure_reason") or str(error)}
        predicted = row.get("prediction_status") == "PREDICTED" or \
            row.get("execution_status") == "PREDICTED"
        samples.append({"id": row["candidate_id"], "values": row["values"],
                        "responses": metrics,
                        "usable": row.get("execution_status") == "SUCCEEDED"
                        and not predicted,
                        "exclusion_reason": "PREDICTED" if predicted else "ROW_UNUSABLE"})
    cohort = sorted([s for s in samples if s["usable"]
                     and all(r["valid"] for r in s["responses"].values())],
                    key=lambda s: s["id"])
    ids = [s["id"] for s in cohort]
    x = np.asarray([[s["values"][v["id"]] for v in variables] for s in cohort],
                   dtype=float).reshape(len(cohort), len(variables))
    y = np.asarray([[s["responses"][d["metric"]]["value"] for d in definitions]
                    for s in cohort], dtype=float).reshape(len(cohort), len(definitions))
    return {
        "statistics": [_statistics(y[:, i], d) for i, d in enumerate(definitions)],
        "sensitivity": [_sensitivity(x, y[:, i], variables, d, ids)
                        for i, d in enumerate(definitions)],
        "pareto": _pareto(y, definitions, ids),
        "exclusions": [{"id": s["id"],
                        "reason": s["exclusion_reason"] if not s["usable"]
                        else "INVALID_RESPONSE"}
                       for s in samples if s["id"] not in set(ids)],
        "candidate_count": len(samples),
        "failure_count": sum(not s["usable"] for s in samples),
        "sampling": deepcopy(table.get("sampling")),
        "variable_ranges": variables,
        "response_definitions": deepcopy(declarations),
        "qualification": "NUMERICAL_SAMPLE_ANALYSIS_ONLY",
        "decision": "NOT_RELEASED",
        "limitations": LIMITATIONS[:],
    }
