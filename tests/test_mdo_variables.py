import dataclasses
import numpy as np
import pytest
from caelab.mdo.variables import (
    ContinuousVariable, DiscreteVariable, CategoricalVariable, LinkedVariable,
    VariableSpace, PhysicalPoint, evaluate_expression, parse_unit,
)


def continuous(**kw):
    return ContinuousVariable(**dict(id="x",name="x",unit="",lower=1.,upper=100.,initial=10.,**kw))


def test_mixed_roundtrip_and_codec_meanings():
    space=VariableSpace(variables=(
        continuous(transform="log"),
        DiscreteVariable(id="d",name="d",unit="mm",values=(1,2,10),initial=2),
        CategoricalVariable(id="c",name="c",choices=("steel","foam"),initial="steel"),
        LinkedVariable(id="z",name="z",unit="",expression="x*2",dependencies=("x",)),
    ))
    data={"x":10.,"d":2.,"c":"foam"}
    surrogate=space.encode(data)
    np.testing.assert_allclose(surrogate.vector,[.5,1/9,0,1])
    np.testing.assert_allclose(space.encode(data,purpose="optimizer").vector,[.5,1,1])
    for purpose in ("surrogate","optimizer"):
        decoded=space.decode(space.encode(data,purpose=purpose).vector,purpose=purpose)
        assert decoded.values["x"]==pytest.approx(10)
        assert decoded.values["z"]==pytest.approx(20)
        assert decoded.values["d"]==2 and decoded.values["c"]=="foam"
    assert space.dimension==3
    with pytest.raises(ValueError): surrogate.vector.setflags(write=True)
    with pytest.raises(TypeError): decoded.values["x"]=2
    with pytest.raises(dataclasses.FrozenInstanceError): space.id="other"


def test_strict_decode_repairs_and_lower_ties():
    space=VariableSpace(variables=(
        DiscreteVariable(id="d",name="d",unit="",values=(1,2,10),initial=2),
        CategoricalVariable(id="c",name="c",choices=("a","b"),initial="a")))
    with pytest.raises(ValueError): space.decode([.5,.5])
    fixed=space.decode([.5,.5],repair="nearest")
    assert dict(fixed.values)=={"d":1.,"c":"a"}
    assert fixed.original_suggestion==(.5,.5)
    with pytest.raises(ValueError): space.decode([0,.5,.5],purpose="surrogate")
    assert space.decode([0,.5,.5],purpose="surrogate",repair="argmax").values["c"]=="a"
    with pytest.raises(ValueError): space.encode({"d":3,"c":"a"})
    with pytest.raises(ValueError): space.encode({"d":2,"c":"missing"})


def test_canonical_unit_scale_and_dimensions():
    assert evaluate_expression("x+y",{"x":1000,"y":1},{"x":"mm","y":"m"},"m")==pytest.approx(2)
    assert evaluate_expression("f/a",{"f":2,"a":4},{"f":"N","a":"mm**2"},"MPa")==pytest.approx(.5)
    assert evaluate_expression("sqrt(a)",{"a":9},{"a":"mm**2"},"mm")==pytest.approx(3)
    for expr,units,out in [
        ("x+y",{"x":"mm","y":"s"},"m"),
        ("log(x)",{"x":"mm"},""),
        ("x",{"x":"mm"},"s"),
        ("x+1",{"x":"mm"},"mm"),
        ("x**y",{"x":"m","y":""},"m")]:
        with pytest.raises(ValueError): evaluate_expression(expr,{k:1 for k in units},units,out)
    for unit in ("degC","K","unknown","m.foo","__import__('os')"):
        with pytest.raises(ValueError): parse_unit(unit)


@pytest.mark.parametrize("expr,deps",[
    ("__import__('os')",()),("x.real",("x",)),("x[0]",("x",)),
    ("[x for x in y]",("y",)),("x+z",("x",)),("x",("x","y"))])
def test_ast_rejects_executable_or_undeclared_syntax(expr,deps):
    with pytest.raises(ValueError):
        evaluate_expression(expr,{x:1 for x in deps},{x:"" for x in deps},dependencies=deps)


def test_linked_topology_static_units_and_runtime_errors():
    x=ContinuousVariable(id="x",name="x",unit="mm",lower=0,upper=2000,initial=1000)
    y=LinkedVariable(id="y",name="y",unit="m",expression="x*2",dependencies=("x",))
    z=LinkedVariable(id="z",name="z",unit="mm",expression="y/2",dependencies=("y",))
    space=VariableSpace(variables=(z,y,x))
    assert dict(space.decode([.5]).values)=={"x":1000.,"y":2.,"z":1000.}
    with pytest.raises(ValueError): VariableSpace(variables=(y,z))
    bad=LinkedVariable(id="y",name="y",unit="s",expression="x*2",dependencies=("x",))
    with pytest.raises(ValueError): VariableSpace(variables=(x,bad))
    with pytest.raises(ValueError): evaluate_expression("1/x",{"x":0},{"x":""})
    with pytest.raises(ValueError): evaluate_expression("exp(x)",{"x":1000},{"x":""})


def test_domain_boundaries_and_integer_range():
    for lo,hi,init,transform in [(0,0,0,"linear"),(0,1,2,"linear"),(-1,1,0,"log"),(0,float("inf"),0,"linear")]:
        with pytest.raises(ValueError): ContinuousVariable(id="x",name="x",unit="",lower=lo,upper=hi,initial=init,transform=transform)
    for values in ((),(2,1),(1,1),(1,float("nan"))):
        with pytest.raises(ValueError): DiscreteVariable(id="d",name="d",unit="",values=values,initial=1)
    with pytest.raises(ValueError):
        DiscreteVariable.integer_range(id="d",name="d",unit="",lower=2,upper=2,initial=2)
    with pytest.raises(ValueError):
        CategoricalVariable(id="c",name="c",choices=("one",),initial="one")
    for step,hi in [(0,3),(2,3)]:
        with pytest.raises(ValueError): DiscreteVariable.integer_range(id="d",name="d",unit="",lower=0,upper=hi,step=step,initial=0)
    x=continuous(transform="log"); space=VariableSpace(variables=(x,))
    assert space.decode([0]).values["x"]==1
    assert space.decode([1]).values["x"]==100
    with pytest.raises(ValueError): space.decode([1.01])
    assert space.decode([1.01],repair="nearest").values["x"]==100



def test_small_and_near_integer_exponents_are_never_rounded():
    assert evaluate_expression("x**0.0001",{"x":16},{"x":""})==pytest.approx(16**.0001)
    assert evaluate_expression("x**1.0001",{"x":16},{"x":""})==pytest.approx(16**1.0001)
    assert parse_unit("m**(1/10000)")[1] != parse_unit("")[1]
    with pytest.raises(ValueError):
        evaluate_expression("x**0.0001",{"x":16},{"x":"m"},"")


def test_extreme_finite_linear_domain_roundtrip():
    s=VariableSpace(variables=(ContinuousVariable(id="x",name="x",unit="",lower=-1e308,upper=1e308,initial=0),))
    assert s.encode({"x":0}).vector[0]==.5
    assert s.decode([.5]).values["x"]==0
