"""Numerical engine tests (port of the original caelab/numerical.py).

All expected numbers below are analytic or were measured from this
implementation on the seeded cases (see the assertions themselves:
analytic values are preferred; measured ones state their tolerance).
"""
import math

import pytest

from caelab.numerical import (EvaluationFailed, analyze_candidates,
                              compare_curves, detect_event, fit_model,
                              fit_surrogate, normalize_variables, optimize,
                              run_doe, run_uq, scalar_value)

VARS2 = [
    {"id": "x", "unit": "1", "lower": -2.0, "upper": 2.0, "value": 0.0},
    {"id": "y", "unit": "1", "lower": -2.0, "upper": 2.0, "value": 0.0},
]


def bowl(values):
    """f = (x-0.5)^2 + (y+1)^2, analytic minimum 0 at (0.5, -1)."""
    return {"responses": {
        "f": {"kind": "scalar",
              "value": (values["x"] - 0.5) ** 2 + (values["y"] + 1.0) ** 2,
              "unit": "1"},
        "xval": {"kind": "scalar", "value": values["x"], "unit": "1"}}}


def test_variable_declaration_rejects_bad_bounds_and_unknown_fields():
    with pytest.raises(ValueError):
        normalize_variables([{"id": "a", "unit": "1", "lower": 2, "upper": 1}])
    with pytest.raises(ValueError):
        normalize_variables([{"id": "a", "unit": "1", "lower": 0, "upper": 1,
                              "bogus": 1}])
    with pytest.raises(ValueError):  # discrete is not silently reinterpreted
        normalize_variables([{"id": "a", "unit": "1", "lower": 0, "upper": 1,
                              "kind": "discrete", "values": [0, 1]}])


def test_doe_lhs_is_seeded_in_bounds_and_reproducible():
    first = run_doe(bowl, VARS2, count=16, seed=42)
    second = run_doe(bowl, VARS2, count=16, seed=42)
    assert first["execution_status"] == "SUCCEEDED"
    assert [c["values"] for c in first["candidates"]] == \
        [c["values"] for c in second["candidates"]]
    assert first["sampling"]["method"] == "scipy.latin_hypercube"
    for row in first["candidates"]:
        assert -2.0 <= row["values"]["x"] <= 2.0
        assert -2.0 <= row["values"]["y"] <= 2.0
        expected = (row["values"]["x"] - 0.5) ** 2 + (row["values"]["y"] + 1) ** 2
        assert row["responses"]["f"]["value"] == pytest.approx(expected)
    # LHS property: one sample in each of the 16 x-strata.
    strata = {int((row["values"]["x"] + 2.0) / 4.0 * 16) for row in first["candidates"]}
    assert len(strata) == 16


def test_doe_retains_failures_with_reasons():
    def flaky(values):
        if values["x"] > 0:
            raise RuntimeError("boom")
        return bowl(values)

    table = run_doe(flaky, VARS2, count=8, seed=0)
    assert table["execution_status"] == "PARTIAL_FAILURE"
    failed = [r for r in table["candidates"] if r["execution_status"] == "FAILED"]
    assert failed and all("boom" in r["failure_reason"] for r in failed)
    assert table["timing"]["model_calls"] == 8


def test_scalar_reduction_and_unit_check():
    evaluation = {"execution_status": "SUCCEEDED", "responses": {
        "curve": {"kind": "series", "value": [1.0, 3.0, 2.0], "unit": "N",
                  "axes": [{"name": "t", "unit": "s", "values": [0.0, 1.0, 2.0]}]}}}
    assert scalar_value(evaluation, {"response": "curve", "unit": "N",
                                     "reduction": "max"}) == 3.0
    assert scalar_value(evaluation, {"response": "curve", "unit": "N",
                                     "reduction": "final"}) == 2.0
    with pytest.raises(ValueError):  # unit mismatch is an error, not a conversion
        scalar_value(evaluation, {"response": "curve", "unit": "Pa",
                                  "reduction": "max"})
    with pytest.raises(EvaluationFailed):
        scalar_value({"execution_status": "PREDICTED", "responses": {}},
                     {"response": "curve", "unit": "N"})


def test_optimize_finds_analytic_minimum():
    result = optimize(bowl, VARS2, {"response": "f", "unit": "1"},
                      options={"max_generations": 15, "population_size": 10},
                      seed=1)
    assert result["execution_status"] == "SUCCEEDED"
    best = result["best"]
    assert best["objective"] < 1e-3
    assert best["values"]["x"] == pytest.approx(0.5, abs=0.05)
    assert best["values"]["y"] == pytest.approx(-1.0, abs=0.05)
    assert best["feasible"] is True


def test_optimize_respects_constraint_and_reports_infeasible_rows():
    result = optimize(
        bowl, VARS2, {"response": "f", "unit": "1"},
        constraints=[{"response": "xval", "unit": "1", "operator": "<=",
                      "limit": 0.0}],
        options={"max_generations": 15, "population_size": 10}, seed=1)
    best = result["best"]
    assert best is not None and best["values"]["x"] <= 0.0
    # Constrained analytic minimum: (0, -1) -> f = 0.25.
    assert best["objective"] == pytest.approx(0.25, abs=0.02)
    assert any(not r["feasible"] for r in result["candidates"])


def _fit_evaluator(values, settings):
    factor = settings.get("factor", 1.0)
    return {"responses": {"y": {"kind": "scalar", "value": values["k"] * factor,
                                "unit": "1", "component": "", "location": "",
                                "reduction": "none"}}}


def test_fit_model_recovers_known_coefficient_and_holdout_is_separate():
    experiments = [
        {"id": "e1", "response": "y", "settings": {"factor": 2.0},
         "observations": {"kind": "scalar", "value": 6.0, "unit": "1",
                          "component": "", "location": "", "reduction": "none"}},
        {"id": "e2", "response": "y", "settings": {"factor": 1.0},
         "observations": {"kind": "scalar", "value": 3.0, "unit": "1",
                          "component": "", "location": "", "reduction": "none"}},
        {"id": "e3", "role": "holdout", "response": "y",
         "settings": {"factor": 3.0},
         "observations": {"kind": "scalar", "value": 9.0, "unit": "1",
                          "component": "", "location": "", "reduction": "none"}},
    ]
    result = fit_model(_fit_evaluator,
                       [{"id": "k", "unit": "1", "lower": 0.0, "upper": 10.0,
                         "value": 1.0}], experiments)
    assert result["execution_status"] == "SUCCEEDED"
    assert result["parameters"]["k"] == pytest.approx(3.0, abs=1e-6)
    curves = {c["experiment_id"]: c for c in result["curves"]}
    assert curves["e3"]["role"] == "holdout"
    assert curves["e3"]["rmse"] == pytest.approx(0.0, abs=1e-6)


def test_fit_model_series_observation_interpolates():
    def ramp(values, settings):
        return {"responses": {"y": {
            "kind": "series", "value": [values["k"] * t for t in (0.0, 1.0, 2.0)],
            "unit": "1", "component": "", "location": "", "reduction": "none",
            "axes": [{"name": "t", "unit": "s", "values": [0.0, 1.0, 2.0]}]}}}

    experiments = [{"id": "s1", "response": "y", "observations": {
        "kind": "series", "value": [0.0, 2.5, 5.0], "unit": "1", "component": "",
        "location": "", "reduction": "none",
        "axes": [{"name": "t", "unit": "s", "values": [0.0, 1.0, 2.0]}]}}]
    result = fit_model(ramp, [{"id": "k", "unit": "1", "lower": 0.0,
                               "upper": 10.0, "value": 1.0}], experiments)
    assert result["parameters"]["k"] == pytest.approx(2.5, abs=1e-6)


def test_compare_curves_and_detect_event():
    prediction = {"kind": "series", "value": [0.0, 1.0, 2.0], "unit": "N",
                  "component": "Fz", "location": "tip", "reduction": "none",
                  "axes": [{"name": "t", "unit": "s", "values": [0.0, 1.0, 2.0]}]}
    observation = dict(prediction, value=[0.0, 1.5, 2.0])
    compared = compare_curves(prediction, observation)
    assert compared["count"] == 3
    assert compared["rmse"] == pytest.approx(math.sqrt(0.25 / 3))
    event = detect_event(prediction, 1.5)
    assert event["state"] == "OCCURRED"
    assert event["value"] == pytest.approx(1.5)  # linear crossing of 1.5 at t=1.5
    assert detect_event(prediction, 5.0)["state"] == "NOT_OBSERVED"


def _sum_evaluator(values):
    return {"responses": {"s": {"kind": "scalar",
                                "value": values["a"] + values["b"], "unit": "1"}}}


SUM_VARS = [{"id": "a", "unit": "1", "lower": 0.0, "upper": 10.0},
            {"id": "b", "unit": "1", "lower": 0.0, "upper": 10.0}]


def test_run_uq_uniform_sum_statistics_near_analytic():
    result = run_uq(_sum_evaluator, SUM_VARS,
                    {"a": {"distribution": "uniform", "lower": 0.0, "upper": 10.0},
                     "b": {"distribution": "uniform", "lower": 0.0, "upper": 10.0}},
                    count=256, seed=3,
                    source={"origin": "ASSUMED", "reference": "test"},
                    thresholds=[{"response": "s", "unit": "1", "limit": 10.0,
                                 "operator": ">"}])
    stats = result["probability_statistics"][0]
    assert stats["valid_count"] == 256 and stats["failed_count"] == 0
    # Sum of two U(0,10): analytic mean 10, std sqrt(2*100/12)=4.082, P(>10)=0.5.
    assert stats["mean"] == pytest.approx(10.0, abs=0.5)
    assert stats["std"] == pytest.approx(4.082, abs=0.4)
    assert stats["exceedance_fraction_valid"] == pytest.approx(0.5, abs=0.08)


def test_run_uq_requires_source_and_rejects_out_of_bounds_normal():
    with pytest.raises(ValueError):
        run_uq(_sum_evaluator, SUM_VARS,
               {"a": {"distribution": "uniform"}, "b": {"distribution": "uniform"}},
               count=8, seed=0, source=None)
    with pytest.raises(ValueError):  # normal tails leave [0, 10]; must use truncnorm
        run_uq(_sum_evaluator, SUM_VARS,
               {"a": {"distribution": "normal", "mean": 5.0, "std": 4.0},
                "b": {"distribution": "uniform"}},
               count=64, seed=0,
               source={"origin": "ASSUMED", "reference": "test"})


def test_analyze_candidates_statistics_sensitivity_pareto():
    table = run_doe(_sum_evaluator, SUM_VARS, count=32, seed=5)
    result = analyze_candidates(table, [
        {"response": "s", "unit": "1", "direction": "minimize"},
        {"response": "s", "unit": "1", "direction": "maximize", "id": "s_max"}])
    stats = result["statistics"][0]
    assert stats["count"] == 32
    assert stats["min"] >= 0.0 and stats["max"] <= 20.0
    coefficients = {c["parameter_id"]: c["coefficient"]
                    for c in result["sensitivity"][0]["coefficients"]}
    # s = a + b with equal ranges: both normalized coefficients are ~equal, > 0.
    assert coefficients["a"] == pytest.approx(coefficients["b"], abs=0.35)
    assert coefficients["a"] > 0.3
    assert result["pareto"]["valid"] is True
    assert result["exclusions"] == []


def test_fit_surrogate_rbf_holdout_error_small_for_smooth_sum():
    training = run_doe(_sum_evaluator, SUM_VARS, count=16, seed=1)
    holdout = run_doe(_sum_evaluator, SUM_VARS, count=6, seed=99)
    surrogate = fit_surrogate(training, holdout, {"response": "s", "unit": "1"},
                              variables=SUM_VARS)
    assert surrogate.validation["rmse"] < 0.5
    predicted = surrogate.predict([{"a": 1.0, "b": 2.0}])[0]
    assert predicted["execution_status"] == "PREDICTED"
    assert predicted["responses"]["s"]["value"] == pytest.approx(3.0, abs=0.5)
    with pytest.raises(ValueError):  # training/holdout overlap is refused
        fit_surrogate(training, training, {"response": "s", "unit": "1"},
                      variables=SUM_VARS)
