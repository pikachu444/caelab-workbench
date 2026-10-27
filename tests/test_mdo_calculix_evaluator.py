"""Tests for native CalculiXEvaluator integration with caelab.mdo.optimize."""

import os
import pytest
from pathlib import Path
import numpy as np

from caelab.mdo.calculix_evaluator import CalculiXEvaluator, DEFAULT_CCX
from caelab.mdo.inp_binder import TemplateDeck
from caelab.mdo.optimize import EvaluationRequest, OptimizationConfig, OptimizationProblem, optimize
from caelab.mdo.variables import ContinuousVariable, VariableSpace
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.constraints import ConstraintSpec


REPO_ROOT = Path(__file__).resolve().parent.parent
TENSION_PLATE_INP = REPO_ROOT / "docs" / "tutorials" / "assets" / "tension_plate_template.inp"


@pytest.mark.skipif(not os.path.isfile(DEFAULT_CCX), reason="CalculiX binary not found")
def test_calculix_evaluator_single_evaluation():
    template = TemplateDeck.from_file(TENSION_PLATE_INP)
    evaluator = CalculiXEvaluator(template)

    req = EvaluationRequest(
        id="req_001",
        point_id="point_001",
        values={"thickness": 2.0},
        encoded=np.array([2.0]),
        mode="ACTUAL",
        problem_revision="1",
    )

    results = evaluator.evaluate_batch((req,))
    assert "req_001" in results
    obs = results["req_001"]
    assert obs.mode == "ACTUAL"
    assert "calculix" in obs.source_id

    # Physics check for 2mm tension plate (210 GPa, 1000 N, 20x100 mm):
    # Stress = 50 / 2 = 25.0 MPa
    # Mass = 1.57e-5 * 2 = 3.14e-5 tonne
    assert "stress" in obs.responses
    assert "disp" in obs.responses
    assert "mass_eall" in obs.responses
    assert obs.responses["stress"] == pytest.approx(25.0, rel=1e-3)
    assert obs.responses["mass_eall"] == pytest.approx(3.14e-5, rel=1e-3)


@pytest.mark.skipif(not os.path.isfile(DEFAULT_CCX), reason="CalculiX binary not found")
def test_calculix_evaluator_general_optimization():
    template = TemplateDeck.from_file(TENSION_PLATE_INP)
    evaluator = CalculiXEvaluator(template)

    space = VariableSpace(
        variables=(ContinuousVariable(id="thickness", name="Thickness", unit="mm", lower=1.0, upper=5.0, initial=2.0),)
    )
    # Objective: Minimize mass subject to stress <= 20.0 MPa (requires thickness >= 2.5 mm)
    obj = ObjectiveSpec(id="min_mass", response_id="mass_eall", sense="MIN")
    con = ConstraintSpec(id="stress_limit", response_id="stress", kind="UPPER", limit=20.0)

    problem = OptimizationProblem(
        space=space,
        objectives=(obj,),
        constraints=(con,),
        differentiable=True,
    )

    config = OptimizationConfig(algorithm="slsqp", max_evaluations=15, ftol=1e-4, finite_difference_step=1e-2)
    res = optimize(problem, evaluator, config)

    assert res.termination_reason != "ERROR"
    assert len(res.actual_archive) > 0

    # Best point should have thickness >= 2.5 mm and stress <= 20.0 MPa
    best = [p for p in res.actual_archive if p.feasible]
    assert len(best) > 0
    # Minimum feasible thickness that satisfies 50 / t <= 20 is t = 2.5 mm
    min_feasible = min(best, key=lambda p: p.responses["mass_eall"])
    assert min_feasible.values["thickness"] >= 2.45
    assert min_feasible.responses["stress"] <= 20.5
