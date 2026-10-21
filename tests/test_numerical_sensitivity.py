"""Sobol sensitivity tests (SALib). Skipped with a visible reason when
SALib is not installed, per the tests/ house rules."""
import pytest

pytest.importorskip("SALib", reason="SALib not installed: Sobol sensitivity "
                                    "requires the optional SALib dependency")

from caelab.numerical import (analyze_sensitivity, run_doe,  # noqa: E402
                              sample_sensitivity)

VARS = [{"id": "a", "unit": "1", "lower": 0.0, "upper": 1.0},
        {"id": "b", "unit": "1", "lower": 0.0, "upper": 1.0}]


def weighted(values):
    """y = 3a + b: analytic Sobol S1 shares are Var(3a)/Var(y)=0.9, 0.1."""
    return {"responses": {"y": {"kind": "scalar",
                                "value": 3.0 * values["a"] + values["b"],
                                "unit": "1"}}}


def test_sobol_plan_size_and_indices():
    plan = sample_sensitivity(VARS, count=256, seed=1)
    # SALib Sobol for D=2 without second order: N * (D + 2) = 256 * 4 rows
    # (measured with the SALib version in caelab-venv).
    assert len(plan["candidates"]) == 256 * 4
    table = run_doe(weighted, VARS, candidates=plan["candidates"])
    result = analyze_sensitivity(plan, table, {"response": "y", "unit": "1"})
    s1 = dict(zip(("a", "b"), result["indices"]["S1"]))
    st = dict(zip(("a", "b"), result["indices"]["ST"]))
    assert s1["a"] == pytest.approx(0.9, abs=0.1)
    assert s1["b"] == pytest.approx(0.1, abs=0.1)
    assert st["a"] > st["b"]


def test_sobol_rejects_unmatched_lhs_table():
    plan = sample_sensitivity(VARS, count=16, seed=1)
    lhs = run_doe(weighted, VARS, count=8, seed=1)
    with pytest.raises(ValueError):
        analyze_sensitivity(plan, lhs, {"response": "y", "unit": "1"})
    with pytest.raises(ValueError):  # an LHS table is not its own Sobol plan
        analyze_sensitivity(lhs, lhs, {"response": "y", "unit": "1"})
