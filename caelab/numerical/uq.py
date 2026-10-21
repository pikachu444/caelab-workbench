"""Uncertainty quantification: declared input laws, propagated by LHS.

Ported from the original ``caelab/numerical.py`` ``run_uq`` (independent
and correlated-normal modes). The joint-distribution extension
(``numerical_extensions.run_joint_uq``) is not ported.

Rules kept from the original: one distribution per variable, the joint
assumption and the distribution *source* must be declared, samples are
a seeded Latin hypercube pushed through each law's inverse CDF, a
sampled law that extends outside the evaluation bounds is an error
(use ``truncnorm`` explicitly), and response statistics condition on
successful evaluations — the failure fraction is reported, never
hidden.
"""
from __future__ import annotations

import math
from copy import deepcopy

import numpy as np
from scipy.stats import lognorm, norm, qmc, truncnorm

from .core import EvaluationFailed, _number, run_doe, scalar_value
from .fit import _nullable
from .variables import normalize_variables

_SOURCES = ("ASSUMED", "MEASURED_REPORTED", "PUBLISHED_REFERENCE", "SYNTHETIC")


def run_uq(evaluator, variables, distributions, *, count=128, seed=0,
           independence="INDEPENDENT_USER_DECLARED", source=None,
           thresholds=None, correlation=None, execution=None):
    """Propagate declared distributions through the evaluator.

    ``distributions`` maps each variable ID to one law:
    ``{"distribution": "uniform", "lower": ..., "upper": ...}`` or
    ``{"distribution": "normal"|"truncnorm"|"lognormal", "mean": ...,
    "std": ...}`` (units must match the variable). ``source`` is
    ``{"origin": one of ASSUMED/MEASURED_REPORTED/PUBLISHED_REFERENCE/
    SYNTHETIC, "reference": str}``. Correlated mode
    (``independence="CORRELATED_NORMAL_USER_DECLARED"``) takes an exact
    ``{"variables": [...], "matrix": [[...]]}`` correlation and normal
    marginals only — no implicit copula (original rule).
    """
    variables = normalize_variables(variables)
    if independence not in ("INDEPENDENT_USER_DECLARED",
                            "CORRELATED_NORMAL_USER_DECLARED") \
            or not isinstance(source, dict) \
            or source.get("origin") not in _SOURCES or not source.get("reference"):
        raise ValueError("Declare the joint assumption and distribution source")
    if independence == "INDEPENDENT_USER_DECLARED" and correlation is not None:
        raise ValueError("Correlation cannot be ignored in independent UQ")
    correlated = independence == "CORRELATED_NORMAL_USER_DECLARED"
    if type(count) is not int or count < 2 or type(seed) is not int \
            or not 0 <= seed <= 2**32 - 1:
        raise ValueError("UQ requires count >= 2 and an unsigned seed")
    if set(distributions) != {v["id"] for v in variables}:
        raise ValueError("Declare one distribution for every variable")
    uniform = qmc.LatinHypercube(len(variables),
                                 rng=np.random.default_rng(seed)).random(count)
    points = np.empty_like(uniform)
    normal_scores = None
    if correlated:
        names = [v["id"] for v in variables]
        if not isinstance(correlation, dict) \
                or set(correlation) != {"variables", "matrix"} \
                or correlation["variables"] != names:
            raise ValueError("Correlation requires the exact ordered variable names and matrix")
        matrix = np.asarray(correlation["matrix"], dtype=float)
        if matrix.shape != (len(names), len(names)) \
                or not np.all(np.isfinite(matrix)) \
                or not np.array_equal(matrix, matrix.T) \
                or not np.all(np.diag(matrix) == 1) or np.any(abs(matrix) > 1):
            raise ValueError("Correlation must be finite, symmetric, unit diagonal "
                             "and within [-1, 1]")
        eig, axes = np.linalg.eigh(matrix)
        if np.min(eig) < -1e-12:
            raise ValueError("Correlation matrix is not positive semidefinite")
        if any(distributions[name].get("distribution") != "normal" for name in names):
            raise ValueError("Correlated mode supports physical normal marginals only; "
                             "no implicit copula")
        normal_scores = norm.ppf(uniform) @ (axes * np.sqrt(np.maximum(eig, 0))).T
    for index, v in enumerate(variables):
        law = distributions[v["id"]]
        if law.get("unit", v["unit"]) != v["unit"]:
            raise ValueError("Distribution unit differs from variable unit")
        kind = law.get("distribution")
        if kind == "uniform":
            low = _number(law.get("lower", v["lower"]), "uniform lower")
            high = _number(law.get("upper", v["upper"]), "uniform upper")
            if not v["lower"] <= low < high <= v["upper"]:
                raise ValueError("Uniform support must lie within evaluation bounds")
            points[:, index] = low + (high - low) * uniform[:, index]
        elif kind in ("normal", "truncnorm", "lognormal"):
            mean = _number(law.get("mean"), "distribution mean")
            std = _number(law.get("std"), "distribution std")
            if std <= 0:
                raise ValueError("Distribution std must be positive")
            if kind == "normal":
                points[:, index] = (mean + std * normal_scores[:, index] if correlated
                                    else norm.ppf(uniform[:, index], loc=mean, scale=std))
            elif kind == "truncnorm":
                points[:, index] = truncnorm.ppf(
                    uniform[:, index], (v["lower"] - mean) / std,
                    (v["upper"] - mean) / std, loc=mean, scale=std)
            else:
                if mean <= 0:
                    raise ValueError("Lognormal arithmetic mean must be positive")
                sigma = math.sqrt(math.log1p((std / mean) ** 2))
                points[:, index] = lognorm.ppf(
                    uniform[:, index], sigma, scale=mean / math.exp(sigma ** 2 / 2))
            if np.any((points[:, index] < v["lower"]) | (points[:, index] > v["upper"])):
                raise ValueError("Sampled distribution extends outside evaluation "
                                 "bounds; widen bounds or explicitly use truncnorm")
        else:
            raise ValueError("Supported distributions: uniform, normal, truncnorm, lognormal")
    candidates = [dict(zip([v["id"] for v in variables], (float(x) for x in point)))
                  for point in points]
    table = run_doe(evaluator, variables, candidates=candidates, execution=execution)
    table.update(kind="uq", sampling={
        "method": "correlated_normal_lhs" if correlated else "independent_lhs_inverse_cdf",
        "seed": seed, "count": count, "independence": independence,
        "source": deepcopy(source), "distributions": deepcopy(distributions),
        "correlation": deepcopy(correlation),
        "variable_order": [v["id"] for v in variables], "coordinates": "physical",
        "sample_correlation": _nullable(np.atleast_2d(
            np.corrcoef(points, rowvar=False)).tolist()) if correlated else None})
    if thresholds is None:
        inferred = {}
        for row in table["candidates"]:
            for name, response in row.get("responses", {}).items():
                if response.get("kind") == "scalar":
                    inferred[name] = {"response": name, "unit": response["unit"]}
        definitions = list(inferred.values())
    else:
        definitions = list(thresholds)
    statistics = []
    for definition in definitions:
        values, missing = [], 0
        for row in table["candidates"]:
            try:
                values.append(scalar_value(row, definition))
            except (EvaluationFailed, ValueError):
                missing += 1
        value = np.asarray(values)
        limit = _number(definition["limit"], "threshold limit") if "limit" in definition else None
        if definition.get("operator", ">") not in (">", ">=", "<", "<="):
            raise ValueError("Unknown threshold operator")
        operation = {">": np.greater, ">=": np.greater_equal,
                     "<": np.less, "<=": np.less_equal}[definition.get("operator", ">")]
        statistics.append({
            "response": definition.get("response", definition.get("metric")),
            "unit": definition["unit"], "valid_count": len(values),
            "failed_count": missing, "failure_fraction": missing / count,
            "mean": float(value.mean()) if len(value) else None,
            "std": float(value.std(ddof=1)) if len(value) >= 2 else None,
            "min": float(value.min()) if len(value) else None,
            "max": float(value.max()) if len(value) else None,
            "quantiles": dict(zip(["q05", "q50", "q95"],
                                   np.quantile(value, [.05, .5, .95]).tolist()))
            if len(value) else {},
            "threshold": deepcopy(definition),
            "exceedance_fraction_valid": float(operation(value, limit).mean())
            if len(value) and limit is not None else None,
            "limitations": ["Statistics condition on successful evaluations; "
                            "failures may bias the estimated distribution."]})
    table["probability_statistics"] = statistics
    return table
