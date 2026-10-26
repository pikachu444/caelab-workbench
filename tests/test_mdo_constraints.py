import numpy as np
import pytest
from caelab.mdo.constraints import ConstraintSpec, ConstraintAssessment, Feasibility, assess_constraints, constraint_penalty


def upper(**kw):
    return ConstraintSpec(id="u",response_id="y",kind="UPPER",limit=10,**kw)


@pytest.mark.parametrize("y,tag,e",[(10,Feasibility.FEASIBLE,-.5),(11,Feasibility.FEASIBLE,0),
    (11.5,Feasibility.MARGINAL,.25),(12,Feasibility.MARGINAL,.5),(12.01,Feasibility.INFEASIBLE,.505)])
def test_exact_tolerance_and_marginal_boundaries(y,tag,e):
    a=assess_constraints({"y":y},[upper(scale=2,tolerance=1,marginal_band=1,weight=3)])
    assert a.feasibility==tag
    assert a.normalized_residuals[0]==pytest.approx(e)
    assert a.raw_residuals[0]==pytest.approx(y-10)
    assert a.total_violation==pytest.approx(3*max(0,e))
    assert a.feasible==(tag==Feasibility.FEASIBLE)


def test_lower_equality_and_weighted_violation():
    specs=[ConstraintSpec(id="l",response_id="x",kind="LOWER",limit=4,scale=2,weight=2),
           ConstraintSpec(id="e",response_id="z",kind="EQUALITY",limit=10,tolerance=.5,scale=.5,weight=3)]
    a=assess_constraints({"x":2,"z":9},specs)
    np.testing.assert_allclose(a.raw_residuals,[2,-1])
    np.testing.assert_allclose(a.normalized_residuals,[1,1])
    assert a.total_violation==5
    assert constraint_penalty(a,multiplier=2,power=2)==10
    with pytest.raises(ValueError): constraint_penalty(a,weights=[1,1])
    assert constraint_penalty(a,multiplier=2,power=2,weights=[2,3])==10
    with pytest.raises(ValueError): a.violations.setflags(write=True)


def test_missing_partial_and_invalid_evaluation_never_feasible():
    specs=[upper(),ConstraintSpec(id="z",response_id="z",kind="LOWER",limit=0)]
    a=assess_constraints({"y":1},specs)
    assert a.feasibility is None and a.total_violation is None and a.reason=="NOT_ASSESSED"
    assert a.criterion_ids==("u",)
    assert assess_constraints({},[]).feasibility==Feasibility.FEASIBLE
    assert assess_constraints({},[],valid=False).feasibility is None
    for y in (None,float("nan"),float("inf")):
        assert assess_constraints({"y":y},[upper()]).feasibility is None
    with pytest.raises(ValueError): constraint_penalty(a)


def test_constraint_units_and_expression():
    spec=ConstraintSpec(id="e",response_id=None,kind="UPPER",limit=2,expression="x+y",
                        dependencies=("x","y"),unit="m")
    assert assess_constraints({"x":1000,"y":1},[spec],units={"x":"mm","y":"m"}).feasible
    assert assess_constraints({"x":1000,"y":1},[spec],units={"x":"mm","y":"s"}).feasibility is None
    s=ConstraintSpec(id="s",response_id="p",kind="UPPER",limit=1,unit="MPa")
    assert assess_constraints({"p":1e6},[s],units={"p":"Pa"}).feasible


@pytest.mark.parametrize("kw",[{"weight":0},{"weight":-1},{"scale":0},{"tolerance":-1},{"marginal_band":-1}])
def test_invalid_constraint_configuration(kw):
    with pytest.raises(ValueError): upper(**kw)


def test_equality_tolerance_required_and_numeric_overflow_unassessed():
    with pytest.raises(ValueError): ConstraintSpec(id="e",response_id="y",kind="EQUALITY",limit=0)
    assert assess_constraints({"y":1e308},[upper(scale=1e-308)]).feasibility is None



def test_aggregate_weight_overflow_is_unassessed():
    a=assess_constraints({"y":12},[upper(weight=1e308)])
    assert a.feasibility is None and a.total_violation is None


@pytest.mark.parametrize("changes",[
    {"total_violation":0,"feasibility":Feasibility.FEASIBLE},
    {"total_violation":None},
    {"total_violation":2},
    {"feasibility":"ACCEPTED"},
    {"violations":[0]},
    {"criterion_ids":()},
    {"criterion_ids":("",)},
])
def test_assessment_constructor_rejects_contradictory_scientific_acceptance(changes):
    fields=dict(raw_residuals=[1],normalized_residuals=[1],violations=[1],
                total_violation=1,feasibility=Feasibility.INFEASIBLE,weights=(1,),criterion_ids=("c",))
    fields.update(changes)
    with pytest.raises(ValueError):
        ConstraintAssessment(**fields)


def test_assessment_constructor_validates_duplicate_ids_and_acceptance_both_directions():
    with pytest.raises(ValueError):
        ConstraintAssessment(raw_residuals=[1,1],normalized_residuals=[1,1],violations=[1,1],
            total_violation=2,feasibility=Feasibility.INFEASIBLE,weights=(1,1),criterion_ids=("c","c"))
    for tag in (Feasibility.MARGINAL,Feasibility.INFEASIBLE):
        with pytest.raises(ValueError):
            ConstraintAssessment(raw_residuals=[-1],normalized_residuals=[-1],violations=[0],
                total_violation=0,feasibility=tag,weights=(1,),criterion_ids=("c",))
    partial=ConstraintAssessment(raw_residuals=[1],normalized_residuals=[1],violations=[1],
        total_violation=None,feasibility=None,reason="NOT_ASSESSED",weights=(1,),criterion_ids=("c",))
    assert partial.feasible is None
