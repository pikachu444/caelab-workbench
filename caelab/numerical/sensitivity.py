"""Global sensitivity: matched SALib Sobol sampling and analysis.

Ported from the original ``caelab/numerical.py`` ``sample_sensitivity``
/ ``analyze_sensitivity``. Rules kept: a Sobol analysis only accepts
its own matched sampling plan (never an ordinary LHS table), variable
order/units/bounds and candidate IDs/values must match the plan
exactly, and undefined indices are reported as ``None`` (JSON null),
never as zero. SALib is an optional dependency; without it both entry
points raise ``RuntimeError`` naming that dependency.
"""
from __future__ import annotations

from copy import deepcopy

import numpy as np

from .core import scalar_value
from .fit import _nullable
from .variables import normalize_variables


def _salib():
    try:
        from SALib.analyze.sobol import analyze
        from SALib.sample.sobol import sample
    except ImportError as error:
        raise RuntimeError("Global sensitivity requires the optional SALib "
                           "dependency") from error
    return sample, analyze


def sample_sensitivity(variables, *, count=128, seed=0, second_order=False):
    """A SALib Sobol plan, distinct from an ordinary LHS table."""
    sample, _ = _salib()
    variables = normalize_variables(variables)
    if any(v["transform"] != "linear" for v in variables):
        raise ValueError("This Sobol interface uses independent uniform "
                         "physical coordinates (linear transforms only)")
    if type(count) is not int or count < 2 or count & (count - 1):
        raise ValueError("Sobol base count must be a power of two >= 2")
    problem = {"num_vars": len(variables), "names": [v["id"] for v in variables],
               "bounds": [[v["lower"], v["upper"]] for v in variables]}
    matrix = sample(problem, count, calc_second_order=second_order, seed=seed)
    return {"variables": variables,
            "sampling": {"method": "SALib.sobol", "problem": problem,
                         "base_count": count, "second_order": second_order,
                         "seed": seed},
            "candidates": [{"candidate_id": f"S{i:06d}",
                            "values": dict(zip(problem["names"],
                                               (float(x) for x in row)))}
                           for i, row in enumerate(matrix)]}


def analyze_sensitivity(plan, table, response):
    """Sobol indices for a table evaluated from ``sample_sensitivity``."""
    _, analyze = _salib()
    sampling = plan.get("sampling", {})
    if sampling.get("method") != "SALib.sobol":
        raise ValueError("Sobol analysis requires its matched SALib sampling plan, "
                         "never ordinary LHS")
    planned = normalize_variables(plan["variables"])
    evaluated = normalize_variables(table["variables"])
    identity = lambda rows: [(v["id"], v["unit"], v["lower"], v["upper"], v["transform"])
                             for v in rows]
    if identity(planned) != identity(evaluated) \
            or sampling.get("problem", {}).get("names") != [v["id"] for v in planned]:
        raise ValueError("Sensitivity variable order, units and bounds differ "
                         "from the sampling plan")
    expected, rows = plan["candidates"], table["candidates"]
    if len(expected) != len(rows) or any(
            a["candidate_id"] != b["candidate_id"] or a["values"] != b["values"]
            for a, b in zip(expected, rows)):
        raise ValueError("Response identities/order/values differ from the "
                         "sensitivity sampling plan")
    y = np.asarray([scalar_value(row, response) for row in rows])
    if not np.all(np.isfinite(y)) or np.ptp(y) == 0:
        raise ValueError("Sobol analysis requires complete finite nonconstant "
                         "model outputs")
    result = analyze(sampling["problem"], y,
                     calc_second_order=sampling["second_order"],
                     seed=sampling["seed"])
    names = sampling["problem"]["names"]
    return {"method": "SALib.sobol", "sampling": deepcopy(sampling),
            "response": deepcopy(response), "variables": deepcopy(planned),
            "indices": {key: _nullable(np.asarray(value).tolist())
                        for key, value in result.items()},
            "undefined_indices": "null: undefined estimates or unused S2 "
                                 "diagonal/lower triangle; never zero",
            "second_order_pairs": [
                {"first": names[i], "second": names[j],
                 "value": _nullable(float(result["S2"][i, j])),
                 "confidence": _nullable(float(result["S2_conf"][i, j]))}
                for i in range(len(names)) for j in range(i + 1, len(names))]
            if sampling["second_order"] else [],
            "candidate_ids": [row["candidate_id"] for row in rows]}
