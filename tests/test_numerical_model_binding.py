"""Common-model binding tests: variables/responses on StructuralModel.

The run function here is an analytic stand-in (no solver): reaction is
exactly the applied force, and displacement is |fy| * 1e6 / E with the
numbers chosen so expected values are exact.
"""
import pytest

from caelab.numerical import (ModelEvaluator, apply_values, get_path,
                              optimize, run_doe, set_path,
                              variables_from_model)
from caelab.solvers.base import ResultSet
from conftest import cantilever_model

SPECS = [
    {"id": "E", "unit": "Pa", "path": "materials[0].params.youngs_modulus",
     "lower": 1.0e11, "upper": 3.0e11},
    {"id": "fy", "unit": "N", "path": "loads[TIP_FORCE].values.fy",
     "lower": -2000.0, "upper": -500.0},
]


def test_get_and_set_path_by_index_and_by_name():
    model = cantilever_model()
    assert get_path(model, "materials[0].params.youngs_modulus") == 210e9
    assert get_path(model, "materials[STEEL].params.youngs_modulus") == 210e9
    assert get_path(model, "loads[TIP_FORCE].values.fy") == -1000.0
    set_path(model, "loads[0].values.fy", -500.0)
    assert model.loads[0].values["fy"] == -500.0
    with pytest.raises(ValueError):
        get_path(model, "materials[NOPE].params.youngs_modulus")


def test_variables_come_from_model_and_apply_does_not_mutate():
    model = cantilever_model()
    variables = variables_from_model(model, SPECS)
    by_id = {v["id"]: v for v in variables}
    assert by_id["E"]["value"] == 210e9
    assert by_id["fy"]["value"] == -1000.0
    changed = apply_values(model, SPECS, {"E": 1.5e11, "fy": -1234.0})
    assert changed.materials[0].params["youngs_modulus"] == 1.5e11
    assert changed.loads[0].values["fy"] == -1234.0
    assert model.materials[0].params["youngs_modulus"] == 210e9  # original intact
    assert model.loads[0].values["fy"] == -1000.0


def _run(model):
    e = model.materials[0].params["youngs_modulus"]
    fy = model.loads[0].values["fy"]
    return ResultSet(solver_id="analytic", success=True,
                     max_displacement=abs(fy) * 1.0e6 / e,
                     reaction_totals={"fx": 0.0, "fy": -fy, "fz": 0.0})


def test_model_evaluator_extracts_resultset_responses_by_path():
    evaluator = ModelEvaluator(
        cantilever_model(), SPECS, _run,
        [{"response": "disp", "unit": "m", "path": "max_displacement"},
         {"response": "rfy", "unit": "N", "path": "reaction_totals.fy"}])
    evaluation = evaluator({"E": 2.0e11, "fy": -1000.0})
    assert evaluation["execution_status"] == "SUCCEEDED"
    # |−1000| * 1e6 / 2e11 = 0.005 exactly; reaction balances the load.
    assert evaluation["responses"]["disp"]["value"] == pytest.approx(0.005)
    assert evaluation["responses"]["rfy"]["value"] == pytest.approx(1000.0)


def test_doe_and_optimize_over_model_variables():
    evaluator = ModelEvaluator(
        cantilever_model(), SPECS, _run,
        [{"response": "disp", "unit": "m", "path": "max_displacement"},
         {"response": "rfy", "unit": "N", "path": "reaction_totals.fy"}])
    table = run_doe(evaluator, evaluator.variables, count=6, seed=2)
    assert table["execution_status"] == "SUCCEEDED"
    for row in table["candidates"]:
        # Reaction always balances the applied force of that candidate.
        assert row["responses"]["rfy"]["value"] == pytest.approx(
            -row["values"]["fy"])
    result = optimize(evaluator, evaluator.variables,
                      {"response": "disp", "unit": "m"},
                      options={"max_generations": 10, "population_size": 10},
                      seed=1)
    best = result["best"]
    # disp = |fy|*1e6/E is minimized at the corner E max, |fy| min.
    assert best["values"]["E"] == pytest.approx(3.0e11, rel=0.05)
    assert best["values"]["fy"] == pytest.approx(-500.0, abs=25.0)
    assert best["objective"] == pytest.approx(500.0 * 1.0e6 / 3.0e11, rel=0.05)
