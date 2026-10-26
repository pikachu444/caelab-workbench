import numpy as np
import pytest
from caelab.mdo.objectives import ObjectiveSpec, transform_objectives, scalarize, evaluate_criteria
from caelab.mdo.constraints import ConstraintSpec


def test_analytic_transform_target_band_and_weight_normalization():
    specs=(
        ObjectiveSpec(id="a",response_id="a",sense="MIN",offset=10,scale=2,weight=1),
        ObjectiveSpec(id="b",response_id="b",sense="MAX",offset=1,scale=4,weight=3),
        ObjectiveSpec(id="c",response_id="c",sense="TARGET",target=10,target_band=2,scale=2,weight=0))
    f=transform_objectives({"a":14,"b":9,"c":15},specs)
    np.testing.assert_allclose(f,[2,-2,1.5])
    assert scalarize(f,specs)==pytest.approx(-1)
    assert transform_objectives({"a":14,"b":9,"c":11},specs)[2]==0
    with pytest.raises(ValueError): f.setflags(write=True)


@pytest.mark.parametrize("kw",[
    {"sense":"bad"},{"scale":0},{"scale":float("inf")},{"weight":-1},
    {"sense":"TARGET","target":1},{"sense":"TARGET","target":1,"target_band":-1},
    {"target":2},{"offset":float("nan")}])
def test_invalid_objectives(kw):
    with pytest.raises(ValueError): ObjectiveSpec(id="a",response_id="a",**kw)


def test_zero_weights_missing_and_overflow():
    spec=ObjectiveSpec(id="a",response_id="a",weight=0)
    with pytest.raises(ValueError): scalarize([1],[spec])
    with pytest.raises(ValueError): transform_objectives({},[spec])
    with pytest.raises(ValueError): transform_objectives({"a":float("nan")},[spec])
    spec=ObjectiveSpec(id="a",response_id="a",weight=1e308)
    assert scalarize([2],[spec])==2
    huge=ObjectiveSpec(id="a",response_id="a",scale=1e-300)
    with pytest.raises(ValueError): transform_objectives({"a":1e300},[huge])


def test_missing_objective_prevents_constraint_feasibility():
    objectives=[ObjectiveSpec(id="o",response_id="obj")]
    constraints=[ConstraintSpec(id="c",response_id="stress",kind="UPPER",limit=10)]
    result=evaluate_criteria({"stress":1},objectives,constraints)
    assert result.objectives is None
    assert result.constraints.feasibility is None and result.reason=="NOT_ASSESSED"
    assert evaluate_criteria({"stress":1,"obj":2},objectives,constraints).assessed

