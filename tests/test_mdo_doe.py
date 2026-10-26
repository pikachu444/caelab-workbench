import numpy as np
import pytest
from caelab.mdo.variables import ContinuousVariable, DiscreteVariable, CategoricalVariable, VariableSpace
from caelab.mdo.doe import DOEConfig, generate_design


def space(k=3):
    return VariableSpace(variables=tuple(ContinuousVariable(id=f"x{i}",name=f"x{i}",unit="",lower=-1,upper=1,initial=0) for i in range(k)))


@pytest.mark.parametrize("criterion",["maximin","correlation","hybrid"])
def test_optimized_lhs_strata_seed_and_achieved_metrics(criterion):
    cfg=DOEConfig(method="opt-lhs",n_samples=12,seed=19,criterion=criterion,restarts=2,iterations=35)
    a=generate_design(space(),cfg); b=generate_design(space(),cfg)
    np.testing.assert_array_equal(a.coded,b.coded)
    for col in a.coded.T:
        np.testing.assert_array_equal(np.sort((12*col).astype(int)),np.arange(12))
    assert a.diagnostics["coded_latin"] and a.diagnostics["decoded_latin"]
    brute=min(np.linalg.norm(a.coded[i]-a.coded[j]) for i in range(12) for j in range(i))
    assert a.diagnostics["minimum_distance"]==pytest.approx(brute)
    assert a.diagnostics["global_optimum_proven"] is False
    assert a.diagnostics["numpy_version"]==np.__version__
    assert a.diagnostics["scipy_version"]
    assert a.diagnostics["codec_version"]=="1"
    with pytest.raises(ValueError): a.coded.setflags(write=True)
    with pytest.raises(TypeError): a.diagnostics["x"]=1


def test_sobol_balanced_block_seed_and_no_padding():
    a=generate_design(space(2),DOEConfig(method="sobol",n_samples=8,seed=2))
    assert a.diagnostics["sequence_start"]==0 and a.diagnostics["sequence_end"]==8
    for col in a.coded.T:
        np.testing.assert_array_equal(np.sort((col*8).astype(int)),np.arange(8))
    with pytest.raises(ValueError,match="power-of-two"):
        generate_design(space(),DOEConfig(method="sobol",n_samples=7))
    with pytest.raises(ValueError,match="budget"):
        generate_design(space(),DOEConfig(method="sobol",n_samples=16,budget=8))


def test_mixed_full_factorial_and_early_budget():
    mixed=VariableSpace(variables=(
        ContinuousVariable(id="x",name="x",unit="",lower=0,upper=2,initial=1),
        DiscreteVariable(id="d",name="d",unit="",values=(1,3,9),initial=1),
        CategoricalVariable(id="c",name="c",choices=("steel","foam"),initial="steel")))
    cfg=DOEConfig(method="full-factorial",levels={"x":(0,2)},budget=12)
    a=generate_design(mixed,cfg)
    assert len(a.points)==12 and a.diagnostics["decoded_unique_count"]==12
    assert {p.values["c"] for p in a.points}=={"steel","foam"}
    assert {p.values["d"] for p in a.points}=={1,3,9}
    with pytest.raises(ValueError,match="budget"):
        generate_design(mixed,DOEConfig(method="full-factorial",levels={"x":(0,2)},budget=11))
    with pytest.raises(ValueError):
        generate_design(mixed,DOEConfig(method="full-factorial"))


def test_quantized_latin_reports_collisions_without_replenishing():
    mixed=VariableSpace(variables=(DiscreteVariable(id="d",name="d",unit="",values=(1,100),initial=1),))
    a=generate_design(mixed,DOEConfig(method="lhs",n_samples=8))
    assert len(a.points)==8
    assert a.diagnostics["coded_latin"] and not a.diagnostics["decoded_latin"]
    assert a.diagnostics["decoded_unique_count"]==2
    assert a.diagnostics["decoded_duplicate_count"]==6
    assert sorted(map(len,a.duplicate_groups))==[4,4]


def test_box_behnken_and_ccd_geometry_quadratic_rank():
    b=generate_design(space(),DOEConfig(method="box-behnken",centers=2))
    assert len(b.points)==14 and b.diagnostics["quadratic_rank"]==10
    z=2*b.coded-1
    assert all(np.count_nonzero(row)==2 for row in z[:-2])
    assert b.roles[-2:]==("center","center")
    assert len(b.diagnostics["replicate_groups"])==1
    c=generate_design(space(),DOEConfig(method="ccd",ccd_type="face-centered"))
    assert len(c.points)==15 and c.diagnostics["quadratic_rank"]==10
    axial=(2*c.coded-1)[8:14]
    assert all(np.count_nonzero(row)==1 and np.max(abs(row))==1 for row in axial)
    ins=generate_design(space(),DOEConfig(method="ccd",ccd_type="inscribed"))
    assert np.max(abs((2*ins.coded-1)[:8]))==pytest.approx(1/8**.25)
    with pytest.raises(ValueError,match="hard bounds"):
        generate_design(space(),DOEConfig(method="ccd",ccd_type="circumscribed"))
    with pytest.raises(ValueError):
        generate_design(space(2),DOEConfig(method="box-behnken"))


@pytest.mark.parametrize("kw",[{"n_samples":0},{"budget":0},{"seed":-1},{"iterations":-1},{"criterion":"random-cd"}])
def test_invalid_design_configuration(kw):
    with pytest.raises(ValueError): DOEConfig(method="lhs",**kw)



def test_lhs_swap_search_never_worsens_initial_candidate():
    a=generate_design(space(),DOEConfig(method="opt-lhs",n_samples=10,seed=9,restarts=1,iterations=0))
    b=generate_design(space(),DOEConfig(method="opt-lhs",n_samples=10,seed=9,restarts=1,iterations=100))
    assert b.diagnostics["minimum_distance"]>=a.diagnostics["minimum_distance"]
