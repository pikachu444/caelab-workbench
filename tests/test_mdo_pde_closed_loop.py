"""Real SciPy PDE solves through DoE, StudyManager and the batch optimizer."""

import math

import numpy as np
import pytest

from caelab.mdo import SciPyPDEEvaluator, StudyManager
from caelab.mdo.constraints import ConstraintSpec
from caelab.mdo.doe import DOEConfig, generate_design
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.optimize import (
    EvaluationRequest, OptimizationConfig, OptimizationProblem, optimize,
)
from caelab.mdo.variables import ContinuousVariable, VariableSpace


def _request(request_id, values, *, mode="ACTUAL"):
    return EvaluationRequest(
        id=request_id, point_id=f"point_{request_id}", values=values,
        encoded=np.zeros(len(values)), mode=mode, problem_revision="1",
    )


def _study_definition():
    space = VariableSpace(variables=(
        ContinuousVariable(id="k", name="Conductivity", unit="", lower=1, upper=5, initial=2),
        ContinuousVariable(id="q", name="HeatSource", unit="", lower=20, upper=100, initial=50),
    ))
    objectives = (
        ObjectiveSpec(id="min_max_temp", response_id="max_temp", sense="MIN"),
        ObjectiveSpec(id="min_max_flux", response_id="max_flux", sense="MIN"),
    )
    constraints = (ConstraintSpec(id="temp_cap", response_id="max_temp", kind="UPPER", limit=3),)
    return space, objectives, constraints


def test_pde_evaluator_unknown_variable_and_invalid_physics_rejected():
    evaluator = SciPyPDEEvaluator(variable_mapping={"k": "diffusion"}, nx=8, ny=8)
    with pytest.raises(ValueError, match="Unknown variable 'foo'"):
        evaluator({"foo": 1.0})
    with pytest.raises(ValueError, match="Missing required design variable 'k'"):
        evaluator({})
    for k in (-1.0, 0.0, math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="Diffusion k must be finite and positive"):
            evaluator({"k": k})
    obs = evaluator.evaluate_batch((_request("bad", {"k": -2.0}),))["bad"]
    assert obs.valid is False and obs.responses == {}
    assert "Diffusion k" in obs.reason

    with pytest.raises(ValueError, match="must not be empty"):
        SciPyPDEEvaluator(variable_mapping={})
    with pytest.raises(ValueError, match="Unsupported PDE target: imaginary"):
        SciPyPDEEvaluator(variable_mapping={"x": "imaginary"})
    for name in ("nx", "ny"):
        for invalid in (0, -1, 2.5, True, math.nan, math.inf):
            with pytest.raises(ValueError, match="positive integer"):
                SciPyPDEEvaluator(variable_mapping={"k": "k"}, **{name: invalid})
    for name in ("lx", "ly", "default_diffusion"):
        for invalid in (0, -1, math.nan, math.inf, None, object(), "invalid"):
            with pytest.raises(ValueError, match="finite and positive"):
                SciPyPDEEvaluator(variable_mapping={"k": "k"}, **{name: invalid})
    for target in ("lx", "ly", "nx", "ny"):
        mapped = SciPyPDEEvaluator(variable_mapping={"x": target}, nx=8, ny=8)
        for invalid in (0, -1):
            with pytest.raises(ValueError):
                mapped({"x": invalid})
    for target in ("lx", "ly", "rhs", "dirichlet", "neumann_right"):
        mapped = SciPyPDEEvaluator(
            variable_mapping={"x": target}, nx=8, ny=8, dirichlet_bc={"left": 0},
        )
        for invalid in (math.nan, math.inf):
            with pytest.raises(ValueError):
                mapped({"x": invalid})


def test_pde_evaluator_physics_monotonicity_and_linearity():
    mapping = {"k": "diffusion", "q": "rhs"}
    evaluator = SciPyPDEEvaluator(variable_mapping=mapping, nx=16, ny=16)
    mapping["k"] = "rhs"  # The constructor owns a defensive copy.
    by_k = [evaluator({"k": k, "q": 100.0}) for k in (1.0, 2.0, 4.0)]
    assert by_k[0]["max_temp"] > by_k[1]["max_temp"] > by_k[2]["max_temp"]
    for i, ratio in ((1, 2.0), (2, 4.0)):
        assert by_k[0]["max_temp"] / by_k[i]["max_temp"] == pytest.approx(ratio, abs=1e-4)
    by_q = [evaluator({"k": 2.0, "q": q}) for q in (50.0, 100.0, 200.0)]
    assert by_q[1]["max_temp"] / by_q[0]["max_temp"] == pytest.approx(2, abs=1e-4)
    assert by_q[2]["max_temp"] / by_q[1]["max_temp"] == pytest.approx(2, abs=1e-4)
    assert by_k[0]["max_flux"] == pytest.approx(by_k[2]["max_flux"], abs=1e-4)
    for response in by_k + by_q:
        assert response["max_u"] == response["max_temp"]
        assert response["min_u"] == response["min_temp"]
        assert response["mean_u"] == response["mean_temp"]

    for k_target in ("diffusion", "k"):
        for q_target in ("rhs", "rhs_func", "source", "q"):
            alias = SciPyPDEEvaluator(
                variable_mapping={"k": k_target, "q": q_target}, nx=16, ny=16,
            )
            assert alias({"k": 2.0, "q": 100.0}) == by_k[1]
            assert alias({"k": 2.0, "q": lambda x, y: 100.0}) == by_k[1]
    for target in ("dirichlet", "dirichlet_bc", "T0"):
        uniform = SciPyPDEEvaluator(variable_mapping={"bc": target}, nx=8, ny=8)
        response = uniform({"bc": 7.0})
        for key in ("min_temp", "max_temp", "mean_temp"):
            assert response[key] == pytest.approx(7.0, abs=1e-12)
        assert response["max_flux"] == pytest.approx(0.0, abs=1e-12)

    # u=2+2x, k=3, Lx=2: right g=6 and flux=(-6,0).
    mixed = SciPyPDEEvaluator(
        variable_mapping={"t": "dirichlet_left", "g": "neumann_right",
                          "x": "lx", "y": "ly", "n": "nx", "m": "ny"},
        default_diffusion=3.0,
    )
    response = mixed({"t": 2, "g": 6, "x": 2, "y": 1.5, "n": 8, "m": 6})
    assert response["min_temp"] == pytest.approx(2, abs=1e-12)
    assert response["max_temp"] == pytest.approx(6, abs=1e-12)
    assert response["mean_temp"] == pytest.approx(4, abs=1e-12)
    assert response["max_flux"] == pytest.approx(6, abs=1e-12)

    for side in ("left", "right", "bottom", "top"):
        opposite = {"left": "right", "right": "left", "bottom": "top", "top": "bottom"}[side]
        prescribed, natural = {opposite: 2.0}, {side: 0.0}
        mapped = SciPyPDEEvaluator(
            variable_mapping={"t": f"dirichlet_{opposite}", "g": f"neumann_{side}"},
            nx=8, ny=8, default_diffusion=3, dirichlet_bc=prescribed, neumann_bc=natural,
        )
        prescribed[opposite], natural[side] = 100.0, -100.0
        response = mapped({"t": 2, "g": 6})
        assert response["min_temp"] == pytest.approx(2, abs=1e-12)
        assert response["max_temp"] == pytest.approx(4, abs=1e-12)
        assert response["max_flux"] == pytest.approx(6, abs=1e-12)
        assert mapped({"t": 2, "g": 0})["max_temp"] == pytest.approx(2, abs=1e-12)
        assert mapped({"t": 2, "g": 6}) == response

    # Promoting a uniform scalar/callable must preserve the other prescribed sides.
    for boundary in (2.0, lambda x, y: 2.0):
        mapped = SciPyPDEEvaluator(
            variable_mapping={"t": "dirichlet_left"}, nx=8, ny=8, dirichlet_bc=boundary,
        )
        with pytest.raises(ValueError, match="Conflicting Dirichlet"):
            mapped({"t": 3.0})


def test_pde_evaluator_evaluate_batch_contract():
    evaluator = SciPyPDEEvaluator(
        variable_mapping={"k": "diffusion", "q": "rhs"}, nx=8, ny=8,
        source_id="scipy_pde:contract",
    )
    requests = (
        _request("req1", {"k": 1.0, "q": 40.0}),
        _request("req2", {"k": -0.5, "q": 40.0}),
        _request("req3", {"k": 2.0, "q": 80.0}),
    )
    results = evaluator.evaluate_batch(requests)
    assert set(results) == {"req1", "req2", "req3"}
    assert results["req1"].valid is True and "max_temp" in results["req1"].responses
    assert results["req2"].valid is False and results["req2"].responses == {}
    assert results["req2"].reason
    assert results["req3"].valid is True
    assert results["req1"].responses["max_temp"] == results["req3"].responses["max_temp"]
    for request in requests:
        observation = results[request.id]
        assert observation.request_id == request.id
        assert observation.point_id == request.point_id
        assert observation.mode == request.mode
        assert observation.source_id == evaluator.source_id
    assert evaluator.evaluate_batch(()) == {}

    def broken_source(x, y):
        raise RuntimeError("source evaluation failed")

    # A real solver field callback exception fails only that request.
    failures = evaluator.evaluate_batch((
        _request("solver_error", {"k": 1, "q": broken_source}),
        _request("nonfinite", {"k": 1, "q": math.nan}),
        _request("after", {"k": 1, "q": 40}),
    ))
    assert failures["solver_error"].reason == "source evaluation failed"
    for key in ("solver_error", "nonfinite"):
        assert failures[key].valid is False and failures[key].responses == {}
        assert failures[key].reason
    assert failures["after"].valid is True
    overlapping = SciPyPDEEvaluator(variable_mapping={"g": "neumann_right"}, nx=8, ny=8)
    observation = overlapping.evaluate_batch((_request("overlap", {"g": 1}),))["overlap"]
    assert observation.valid is False and observation.responses == {}
    assert "overlap" in observation.reason


def test_mdo_pde_closed_loop_doe_studymanager_pareto():
    space, objectives, constraints = _study_definition()
    evaluator = SciPyPDEEvaluator(variable_mapping={"k": "diffusion", "q": "rhs"}, nx=16, ny=16)

    def run_study():
        design = generate_design(space, DOEConfig(method="lhs", n_samples=12, seed=42))
        study = StudyManager(space=space, objectives=objectives, constraints=constraints)
        for point in design.points:
            record = study.add_point(point.values)
            study.evaluate_point_with_evaluator(record, evaluator)
            assert record.status == "COMPLETED"
            assert record.objectives and all(math.isfinite(v) for v in record.objectives.values())
        bad_rec = study.add_point({"k": -1.0, "q": 50.0})
        study.evaluate_point_with_evaluator(bad_rec, evaluator)
        assert bad_rec.status == "FAILED" and bad_rec.feasibility == "NOT_ASSESSED"
        assert bad_rec.observations == {} and bad_rec.objectives == {} and bad_rec.error_message
        pareto_ids = study.update_pareto_front()
        assert bad_rec.point_id not in pareto_ids and bad_rec.is_pareto is False
        assert pareto_ids
        for record in study.records:
            if record.point_id in pareto_ids:
                assert record.feasibility == "FEASIBLE" and record.status == "COMPLETED"
        return pareto_ids, [
            (r.point_id, r.physical_values, r.observations, r.objectives, r.status, r.feasibility, r.is_pareto)
            for r in study.records
        ]

    assert run_study() == run_study()


def test_mdo_pde_optimization_with_optimizer():
    space, objectives, constraints = _study_definition()
    problem = OptimizationProblem(
        space=space, objectives=objectives, constraints=constraints,
        mode="ACTUAL", revision="1", criteria_revision="1",
    )
    evaluator = SciPyPDEEvaluator(variable_mapping={"k": "diffusion", "q": "rhs"}, nx=16, ny=16)
    config = OptimizationConfig(algorithm="nsga2", seed=42, max_evaluations=20, population_size=10)
    result = optimize(problem, evaluator, config)
    assert result.termination_reason != "ERROR"
    assert result.actual_archive and not result.failures
    for point in result.actual_archive:
        assert point.valid is True
        assert point.source_id == evaluator.source_id
        assert all(math.isfinite(v) for v in point.responses.values())
    repeated = optimize(problem, evaluator, config)
    assert repeated.termination_reason == result.termination_reason
    assert repeated.selected_ids == result.selected_ids
    assert len(repeated.actual_archive) == len(result.actual_archive)
    for first, second in zip(result.actual_archive, repeated.actual_archive):
        assert first.point_id == second.point_id and first.values == second.values
        assert first.responses == second.responses
        np.testing.assert_array_equal(first.objectives, second.objectives)
        np.testing.assert_array_equal(first.normalized_residuals, second.normalized_residuals)
