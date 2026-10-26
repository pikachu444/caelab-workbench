import pytest
from caelab.mdo.inp_binder import TemplateDeck, BindingSpec
from caelab.mdo.responses import ResponseSpec, extract_all_responses
from caelab.mdo.study_manager import StudyManager
from caelab.mdo.variables import ContinuousVariable, VariableSpace
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.constraints import ConstraintSpec


def test_template_deck_substitution(tmp_path):
    raw_deck = """*HEADING
Cantilever Beam Model
** Comment line: <IGNORED_IN_COMMENT>
*MATERIAL, NAME=STEEL
*ELASTIC
 210000.0, 0.3
*BEAM SECTION, SECTION=RECT, ELSET=BEAM, MATERIAL=STEEL
 <WIDTH>, <HEIGHT>
*CLOAD
 100, 2, <LOAD>
*END STEP
"""
    deck = TemplateDeck.from_text(raw_deck)
    assert set(deck.token_names) == {"WIDTH", "HEIGHT", "LOAD"}

    rendered = deck.render({"WIDTH": 15.5, "HEIGHT": 30.0, "LOAD": -500.0})
    assert "15.5, 30" in rendered
    assert "-500" in rendered
    assert "*HEADING" in rendered
    assert "** Comment line: <IGNORED_IN_COMMENT>" in rendered


def test_response_extraction_regex(tmp_path):
    log_content = """
CalculiX Version 2.23
Step 1 completed.
Max Von Mises Stress = 142.85 MPa
Total Mass = 4.250 kg
Maximum Tip Displacement = 2.14 mm
"""
    log_file = tmp_path / "solver.log"
    log_file.write_text(log_content, encoding="utf-8")

    specs = {
        "stress": ResponseSpec(id="stress", name="Max Stress", pattern=r"Max Von Mises Stress\s*=\s*([0-9.]+)"),
        "mass": ResponseSpec(id="mass", name="Mass", pattern=r"Total Mass\s*=\s*([0-9.]+)"),
        "disp": ResponseSpec(id="disp", name="Tip Disp", pattern=r"Maximum Tip Displacement\s*=\s*([0-9.]+)"),
    }

    obs = extract_all_responses(specs, tmp_path)
    assert obs["stress"] == pytest.approx(142.85)
    assert obs["mass"] == pytest.approx(4.250)
    assert obs["disp"] == pytest.approx(2.14)


def test_study_manager_workflow():
    v1 = ContinuousVariable(id="x", name="Width", unit="mm", lower=10.0, upper=50.0, initial=20.0)
    v2 = ContinuousVariable(id="y", name="Height", unit="mm", lower=20.0, upper=100.0, initial=40.0)
    space = VariableSpace(variables=(v1, v2))

    # Objective: Min mass = x * y
    obj = ObjectiveSpec(id="mass", response_id="mass", sense="MIN", scale=100.0)
    # Constraint: Stress <= 200 MPa
    con = ConstraintSpec(id="c_stress", response_id="stress", kind="UPPER", limit=200.0, scale=100.0)

    mgr = StudyManager("Cantilever Study", space=space, objectives=[obj], constraints=[con])

    def mock_evaluator(params):
        w = params["x"]
        h = params["y"]
        # Analytical beam: mass ~ w*h, stress ~ 10000 / (w * h^2)
        mass = w * h * 0.001
        stress = 100000.0 / (w * (h ** 1.5))
        return {"mass": mass, "stress": stress}

    p1 = mgr.add_point({"x": 20.0, "y": 40.0})
    p2 = mgr.add_point({"x": 40.0, "y": 80.0})

    mgr.evaluate_point_with_evaluator(p1, mock_evaluator)
    mgr.evaluate_point_with_evaluator(p2, mock_evaluator)

    assert p1.status == "COMPLETED"
    assert p2.status == "COMPLETED"
    assert p1.feasibility in ("FEASIBLE", "MARGINAL", "INFEASIBLE")

    pareto_pts = mgr.update_pareto_front()
    assert isinstance(pareto_pts, list)
