"""Validated physical domains, codecs and dimension-aware restricted expressions."""
from __future__ import annotations
import ast
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from types import MappingProxyType
import numpy as np


def _id(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("IDs and names must be nonempty strings")


def _finite(value):
    try:
        if isinstance(value, (bool, str)) or not math.isfinite(value):
            raise ValueError("Expected a finite number")
        return float(value)
    except (TypeError, OverflowError) as exc:
        raise ValueError("Expected a finite number") from exc


def _readonly(value):
    a = np.array(value, dtype=float, copy=True)
    if not np.isfinite(a).all():
        raise ValueError("Nonfinite array")
    return np.frombuffer(a.tobytes(), dtype=float).reshape(a.shape)


_ZERO = (Fraction(0),) * 3
_UNITS = {
    "m": (1., (1,0,0)), "mm": (.001, (1,0,0)), "cm": (.01, (1,0,0)), "um": (1e-6, (1,0,0)),
    "kg": (1., (0,1,0)), "g": (.001, (0,1,0)), "s": (1., (0,0,1)), "ms": (.001, (0,0,1)),
    "N": (1., (1,1,-2)), "kN": (1e3, (1,1,-2)), "Pa": (1., (-1,1,-2)),
    "kPa": (1e3, (-1,1,-2)), "MPa": (1e6, (-1,1,-2)), "GPa": (1e9, (-1,1,-2)),
    "J": (1., (2,1,-2)), "W": (1., (2,1,-3)), "Hz": (1., (0,0,-1)), "rad": (1., (0,0,0)),
}


def _constant(n):
    if isinstance(n, ast.Constant) and type(n.value) in (int, float):
        return Fraction(str(_finite(n.value)))
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
        return _constant(n.operand) * (-1 if isinstance(n.op, ast.USub) else 1)
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
        return _constant(n.left) / _constant(n.right)
    raise ValueError("Powers require finite constant rational exponents")


def parse_unit(unit):
    """Known multiplicative SI units, *, / and **; unknown/offset units fail."""
    if not isinstance(unit, str):
        raise ValueError("Unit must be a string; '' means dimensionless")
    if unit in ("", "1"):
        return 1., _ZERO

    def visit(n):
        if isinstance(n, ast.Name) and n.id in _UNITS:
            s, d = _UNITS[n.id]
            return s, tuple(Fraction(x) for x in d)
        if isinstance(n, ast.Constant) and type(n.value) is int and n.value == 1:
            return 1., _ZERO
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Mult, ast.Div)):
            a, ad = visit(n.left); b, bd = visit(n.right)
            sign = -1 if isinstance(n.op, ast.Div) else 1
            return a * b**sign, tuple(x + sign*y for x,y in zip(ad,bd))
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow):
            a, ad = visit(n.left); p = _constant(n.right)
            if abs(p) > 100:
                raise ValueError("Unit exponent too large")
            return a**float(p), tuple(x*p for x in ad)
        raise ValueError(f"Unsupported unit {unit!r}; declare known multiplicative SI units")
    try:
        scale, dim = visit(ast.parse(unit, mode="eval").body)
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError("Invalid unit scale")
        return scale, dim
    except (SyntaxError, ArithmeticError) as exc:
        raise ValueError("Invalid unit") from exc


_FUNCTIONS = {"abs", "sqrt", "exp", "log", "sin", "cos", "min", "max"}


def evaluate_expression(expression, values, units, output_unit="", *, dependencies=None, validate_only=False):
    """Interpret an allowlisted AST in canonical units, never Python eval."""
    declared = tuple(values) if dependencies is None else tuple(dependencies)
    if len(set(declared)) != len(declared) or any(not isinstance(x,str) or not x.isidentifier() or x in _FUNCTIONS for x in declared):
        raise ValueError("Dependencies must be unique non-reserved identifiers")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, TypeError) as exc:
        raise ValueError("Invalid expression") from exc
    if sum(1 for _ in ast.walk(tree)) > 256:
        raise ValueError("Expression too complex")
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - _FUNCTIONS
    if names != set(declared):
        raise ValueError("Expression names must exactly match declared dependencies")

    def visit(n):
        if isinstance(n, ast.Constant) and type(n.value) in (int,float):
            return _finite(n.value), _ZERO
        if isinstance(n, ast.Name) and n.id in declared:
            if n.id not in values or n.id not in units:
                raise ValueError(f"Missing value or unit: {n.id}")
            factor, dim = parse_unit(units[n.id])
            return (1. if validate_only else _finite(values[n.id])*factor), dim
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd,ast.USub)):
            v,d = visit(n.operand)
            return (-v if isinstance(n.op, ast.USub) else v),d
        if isinstance(n, ast.BinOp):
            a,ad = visit(n.left); b,bd = visit(n.right)
            if isinstance(n.op, (ast.Add,ast.Sub)):
                if ad != bd:
                    raise ValueError("Addition requires compatible dimensions")
                return (a+b if isinstance(n.op,ast.Add) else a-b),ad
            if isinstance(n.op,(ast.Mult,ast.Div)):
                sign = -1 if isinstance(n.op,ast.Div) else 1
                return (1. if validate_only else a*b**sign), tuple(x+sign*y for x,y in zip(ad,bd))
            if isinstance(n.op,ast.Pow):
                if bd != _ZERO:
                    raise ValueError("Exponent must be dimensionless")
                p = _constant(n.right)
                if abs(p)>100:
                    raise ValueError("Power too large")
                return (1. if validate_only else a**float(p)), tuple(x*p for x in ad)
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in _FUNCTIONS and not n.keywords:
            name = n.func.id; args = [visit(a) for a in n.args]
            if not args or (name not in ("min","max") and len(args)!=1):
                raise ValueError("Invalid function arity")
            a,dim = args[0]
            if name in ("min","max"):
                if any(d != dim for _,d in args):
                    raise ValueError("min/max require compatible dimensions")
                return (min if name=="min" else max)(v for v,_ in args),dim
            if name=="abs":
                return abs(a),dim
            if name=="sqrt":
                return (1. if validate_only else math.sqrt(a)),tuple(d/2 for d in dim)
            if dim != _ZERO:
                raise ValueError(f"{name} requires dimensionless arguments")
            return (1. if validate_only else getattr(math,name)(a)),_ZERO
        raise ValueError("Unsupported expression syntax")
    try:
        value,dim = visit(tree.body); factor,expected = parse_unit(output_unit)
        if dim != expected:
            raise ValueError("Expression output unit has incompatible dimensions")
        return 0. if validate_only else _finite(value/factor)
    except (ArithmeticError, TypeError) as exc:
        raise ValueError("Expression produced an invalid numeric result") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class ContinuousVariable:
    id: str
    name: str
    unit: str
    lower: float
    upper: float
    initial: float
    transform: str = "linear"
    def __post_init__(self):
        _id(self.id); _id(self.name); parse_unit(self.unit)
        lo,hi,x = map(_finite,(self.lower,self.upper,self.initial))
        if lo>=hi or not lo<=x<=hi or self.transform not in ("linear","log"):
            raise ValueError("Invalid continuous domain")
        if self.transform=="log" and lo<=0:
            raise ValueError("Log bounds must be positive")


@dataclass(frozen=True, slots=True, kw_only=True)
class DiscreteVariable:
    id: str
    name: str
    unit: str
    values: tuple[float,...]
    initial: float
    def __post_init__(self):
        _id(self.id); _id(self.name); parse_unit(self.unit)
        levels = tuple(_finite(v) for v in self.values)
        if len(levels)<2 or tuple(sorted(set(levels)))!=levels or _finite(self.initial) not in levels:
            raise ValueError("Invalid discrete levels")
        object.__setattr__(self,"values",levels)
    @classmethod
    def integer_range(cls, *, lower, upper, step=1, **kwargs):
        if any(type(x) is not int for x in (lower,upper,step)) or step<=0 or upper<lower or (upper-lower)%step:
            raise ValueError("Invalid integer grid")
        return cls(values=tuple(range(lower,upper+1,step)),**kwargs)


@dataclass(frozen=True, slots=True, kw_only=True)
class CategoricalVariable:
    id: str
    name: str
    choices: tuple[str,...]
    initial: str
    def __post_init__(self):
        _id(self.id); _id(self.name); choices = tuple(self.choices)
        if len(choices)<2 or any(not isinstance(x,str) or not x for x in choices) or len(set(choices))!=len(choices) or self.initial not in choices:
            raise ValueError("Invalid category vocabulary")
        object.__setattr__(self,"choices",choices)


@dataclass(frozen=True, slots=True, kw_only=True)
class LinkedVariable:
    id: str
    name: str
    unit: str
    expression: str
    dependencies: tuple[str,...]
    lower: float|None = None
    upper: float|None = None
    def __post_init__(self):
        _id(self.id); _id(self.name); parse_unit(self.unit)
        object.__setattr__(self,"dependencies",tuple(self.dependencies))
        for v in (self.lower,self.upper):
            if v is not None:
                _finite(v)
        if self.lower is not None and self.upper is not None and self.lower>self.upper:
            raise ValueError("Invalid linked bounds")


@dataclass(frozen=True, slots=True, kw_only=True)
class EncodedPoint:
    vector: np.ndarray
    feature_order: tuple[str,...]
    codec_version: str = "1"
    id: str = "encoded"
    def __post_init__(self):
        _id(self.id); _id(self.codec_version)
        vector = _readonly(self.vector); order = tuple(self.feature_order)
        if vector.ndim!=1 or len(vector)!=len(order):
            raise ValueError("Encoded vector/feature mismatch")
        object.__setattr__(self,"vector",vector); object.__setattr__(self,"feature_order",order)


@dataclass(frozen=True, slots=True, kw_only=True)
class PhysicalPoint:
    values: Mapping[str,float|str]
    id: str = "point"
    original_suggestion: tuple[float,...]|None = None
    repair: str|None = None
    def __post_init__(self):
        _id(self.id); values = dict(self.values)
        for key,value in values.items():
            _id(key)
            if not isinstance(value,str):
                _finite(value)
        if self.original_suggestion is not None:
            object.__setattr__(self,"original_suggestion",tuple(_finite(x) for x in self.original_suggestion))
        object.__setattr__(self,"values",MappingProxyType(values))


@dataclass(frozen=True, slots=True, kw_only=True)
class VariableSpace:
    variables: tuple[ContinuousVariable|DiscreteVariable|CategoricalVariable|LinkedVariable,...]
    id: str = "space"
    codec_version: str = "1"
    _linked: tuple[LinkedVariable,...] = field(init=False, repr=False)
    def __post_init__(self):
        _id(self.id); _id(self.codec_version); variables = tuple(self.variables)
        if any(not isinstance(v,(ContinuousVariable,DiscreteVariable,CategoricalVariable,LinkedVariable)) for v in variables):
            raise ValueError("Unsupported variable record")
        if len({v.id for v in variables})!=len(variables):
            raise ValueError("Duplicate variable IDs")
        object.__setattr__(self,"variables",variables)
        known = {v.id for v in variables if not isinstance(v,LinkedVariable)}
        pending = [v for v in variables if isinstance(v,LinkedVariable)]; linked = []
        units = {v.id:getattr(v,"unit","") for v in variables}
        categories = {v.id for v in variables if isinstance(v,CategoricalVariable)}
        while pending:
            ready = [v for v in pending if set(v.dependencies)<=known]
            if not ready:
                raise ValueError("Linked variable cycle or missing dependency")
            for v in ready:
                if set(v.dependencies)&categories:
                    raise ValueError("Categories cannot enter numeric expressions")
                evaluate_expression(v.expression,{d:1. for d in v.dependencies},units,v.unit,dependencies=v.dependencies,validate_only=True)
                linked.append(v); known.add(v.id); pending.remove(v)
        object.__setattr__(self,"_linked",tuple(linked))
    @property
    def independent_variables(self):
        return tuple(v for v in self.variables if not isinstance(v,LinkedVariable))
    @property
    def dimension(self):
        return len(self.independent_variables)
    @property
    def optimizer_bounds(self):
        return _readonly([[0.,1. if isinstance(v,ContinuousVariable) else len(v.values if isinstance(v,DiscreteVariable) else v.choices)-1] for v in self.independent_variables]).reshape(self.dimension,2)
    def _physical(self,values):
        data = dict(values.values if isinstance(values,PhysicalPoint) else values)
        if set(data)-{v.id for v in self.variables}:
            raise ValueError("Unknown variable")
        for v in self.independent_variables:
            if v.id not in data:
                raise ValueError(f"Missing variable {v.id}")
            x = data[v.id]
            if isinstance(v,ContinuousVariable):
                if not v.lower<=_finite(x)<=v.upper:
                    raise ValueError("Outside physical bounds")
            elif isinstance(v,DiscreteVariable):
                if _finite(x) not in v.values:
                    raise ValueError("Not a discrete level")
            elif x not in v.choices:
                raise ValueError("Unknown category")
        units = {v.id:getattr(v,"unit","") for v in self.variables}
        for v in self._linked:
            x = evaluate_expression(v.expression,data,units,v.unit,dependencies=v.dependencies)
            if (v.lower is not None and x<v.lower) or (v.upper is not None and x>v.upper):
                raise ValueError("Linked value outside bounds")
            if v.id in data and not math.isclose(_finite(data[v.id]),x,rel_tol=1e-12,abs_tol=1e-12):
                raise ValueError("Linked value disagrees with expression")
            data[v.id] = x
        return data
    def encode(self,values,purpose="surrogate"):
        if purpose not in ("surrogate","optimizer"):
            raise ValueError("Unknown codec purpose")
        data = self._physical(values); vector = []; order = []
        for v in self.independent_variables:
            x = data[v.id]
            if isinstance(v,ContinuousVariable):
                lo,hi,x = (math.log(v.lower),math.log(v.upper),math.log(x)) if v.transform=="log" else (v.lower,v.upper,x)
                span=hi-lo
                vector.append((x-lo)/span if math.isfinite(span) else (x/2-lo/2)/(hi/2-lo/2)); order.append(v.id)
            elif isinstance(v,DiscreteVariable):
                vector.append(v.values.index(x) if purpose=="optimizer" else ((x-v.values[0])/(v.values[-1]-v.values[0]) if len(v.values)>1 else 0.)); order.append(v.id)
            elif purpose=="optimizer":
                vector.append(v.choices.index(x)); order.append(v.id)
            else:
                vector.extend(float(x==c) for c in v.choices); order.extend(f"{v.id}:{c}" for c in v.choices)
        return EncodedPoint(vector=vector,feature_order=tuple(order),codec_version=self.codec_version)
    def decode(self,vector,purpose="optimizer",repair=None):
        if purpose not in ("surrogate","optimizer") or repair not in (None,"nearest","argmax"):
            raise ValueError("Unknown purpose or repair policy")
        raw = _readonly(vector)
        expected = sum(len(v.choices) if purpose=="surrogate" and isinstance(v,CategoricalVariable) else 1 for v in self.independent_variables)
        if raw.ndim!=1 or len(raw)!=expected:
            raise ValueError("Wrong encoded dimension")
        data = {}; index = 0
        for v in self.independent_variables:
            x = float(raw[index]); index += 1
            if isinstance(v,ContinuousVariable):
                if not 0<=x<=1:
                    if repair!="nearest":
                        raise ValueError("Continuous coordinate outside [0,1]")
                    x = float(np.clip(x,0,1))
                data[v.id] = (math.exp(math.log(v.lower)+x*(math.log(v.upper)-math.log(v.lower))) if v.transform=="log" else (1-x)*v.lower+x*v.upper)
                if x==0:
                    data[v.id] = v.lower
                if x==1:
                    data[v.id] = v.upper
            elif isinstance(v,CategoricalVariable) and purpose=="surrogate":
                scores = raw[index-1:index-1+len(v.choices)]; index += len(v.choices)-1
                exact = np.all((scores==0)|(scores==1)) and np.count_nonzero(scores==1)==1
                if not exact and repair!="argmax":
                    raise ValueError("Expected exact one-hot category")
                data[v.id] = v.choices[int(np.argmax(scores))]
            else:
                levels = v.values if isinstance(v,DiscreteVariable) else v.choices
                coded = (np.array([(a-levels[0])/(levels[-1]-levels[0]) for a in levels]) if len(levels)>1 else np.array([0.])) if purpose=="surrogate" else np.arange(len(levels))
                matches = np.flatnonzero(np.isclose(coded,x,rtol=1e-12,atol=1e-12))
                if len(matches):
                    chosen = int(matches[0])
                elif repair=="nearest":
                    chosen = int(np.argmin(abs(coded-x)))
                else:
                    raise ValueError("Coordinate is not an exact level identity")
                data[v.id] = levels[chosen]
        return PhysicalPoint(values=self._physical(data),original_suggestion=tuple(raw),repair=repair)
    def from_unit(self,vector):
        unit = _readonly(vector)
        if unit.shape!=(self.dimension,) or np.any((unit<0)|(unit>1)):
            raise ValueError("DOE coordinates must lie in [0,1]")
        optimizer = unit.copy()
        for i,v in enumerate(self.independent_variables):
            if not isinstance(v,ContinuousVariable):
                count = len(v.values if isinstance(v,DiscreteVariable) else v.choices)
                optimizer[i] = min(int(unit[i]*count),count-1)
        return self.decode(optimizer)

