"""Seeded experimental designs with explicit budgets and decoded-domain diagnostics."""
from __future__ import annotations
import copy
import itertools
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
import numpy as np
from .variables import VariableSpace, PhysicalPoint, ContinuousVariable, DiscreteVariable, _id, _readonly


def _freeze(value):
    if isinstance(value,dict):
        return MappingProxyType({k:_freeze(v) for k,v in value.items()})
    if isinstance(value,(list,tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value,np.ndarray):
        return _readonly(value)
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class DOEConfig:
    method: str
    n_samples: int = 16
    seed: int = 0
    budget: int = 10000
    criterion: str = "maximin"
    restarts: int = 4
    iterations: int = 200
    levels: Mapping[str,tuple] = field(default_factory=dict)
    centers: int = 1
    ccd_type: str = "face-centered"
    id: str = "doe"
    def __post_init__(self):
        _id(self.id)
        if self.method not in ("lhs","opt-lhs","sobol","full-factorial","box-behnken","ccd"):
            raise ValueError("Unknown DOE method")
        if any(type(x) is not int for x in (self.n_samples,self.seed,self.budget,self.restarts,self.iterations,self.centers)):
            raise ValueError("Counts and seeds must be integers")
        if self.n_samples<1 or self.budget<1 or self.seed<0 or self.restarts<1 or self.iterations<0 or self.centers<1:
            raise ValueError("Invalid DOE counts or budget")
        if self.criterion not in ("maximin","correlation","hybrid"):
            raise ValueError("Unknown LHS criterion")
        if self.ccd_type not in ("face-centered","circumscribed","inscribed"):
            raise ValueError("Unknown CCD type")
        object.__setattr__(self,"levels",MappingProxyType({k:tuple(v) for k,v in self.levels.items()}))


@dataclass(frozen=True, slots=True, kw_only=True)
class DesignBatch:
    points: tuple[PhysicalPoint,...]
    coded: np.ndarray
    seed: int
    algorithm: str
    factor_order: tuple[str,...]
    roles: tuple[str,...]
    duplicate_groups: tuple[tuple[str,...],...]
    diagnostics: Mapping
    rng_state: Mapping
    version: str = "1"
    id: str = "design"
    def __post_init__(self):
        _id(self.id); _id(self.version)
        points=tuple(self.points); coded=_readonly(self.coded)
        if coded.shape!=(len(points),len(self.factor_order)) or len(self.roles)!=len(points):
            raise ValueError("Design shape mismatch")
        if len({p.id for p in points})!=len(points):
            raise ValueError("Duplicate point IDs")
        object.__setattr__(self,"points",points); object.__setattr__(self,"coded",coded)
        for name in ("factor_order","roles","duplicate_groups"):
            object.__setattr__(self,name,tuple(getattr(self,name)))
        object.__setattr__(self,"diagnostics",_freeze(dict(self.diagnostics)))
        object.__setattr__(self,"rng_state",_freeze(dict(self.rng_state)))


def _latin(x):
    n,d=x.shape
    return all(np.array_equal(np.sort(np.minimum((x[:,j]*n).astype(int),n-1)),np.arange(n)) for j in range(d))


def _quality(x):
    n,d=x.shape
    distance=min(float(np.min(np.linalg.norm(x[i+1:]-x[i],axis=1))) for i in range(n-1)) if n>1 else None
    if n>1 and d>1:
        centered=x-x.mean(axis=0)
        norms=np.linalg.norm(centered,axis=0)
        corr=np.divide(centered.T@centered,np.outer(norms,norms),out=np.zeros((d,d)),where=np.outer(norms,norms)>0)
        np.fill_diagonal(corr,0)
        correlation=float(np.sum(corr**2)/2)
    else:
        correlation=0.
    return distance,correlation


def _score(x,criterion):
    distance,corr=_quality(x); distance=0. if distance is None else distance
    if criterion=="maximin": return (-distance,)
    if criterion=="correlation": return (corr,)
    return (-distance,corr)


def _quadratic_rank(z):
    n,k=z.shape
    columns=[np.ones(n)]
    columns.extend(z[:,i] for i in range(k))
    columns.extend(z[:,i]*z[:,j] for i in range(k) for j in range(i,k))
    matrix=np.column_stack(columns)
    return int(np.linalg.matrix_rank(matrix)),matrix.shape[1]


def generate_design(space: VariableSpace, config: DOEConfig, rng=None):
    try:
        import scipy
        from scipy.stats import qmc
    except ImportError as exc:
        raise ImportError("DOE generation requires SciPy; install caelab-workbench[mdo]") from exc
    d=space.dimension
    if d<1:
        raise ValueError("DOE requires at least one independent factor")
    rng=np.random.default_rng(config.seed) if rng is None else rng
    if not isinstance(rng,np.random.Generator):
        raise ValueError("rng must be a NumPy Generator")
    state_before=copy.deepcopy(rng.bit_generator.state)
    method=config.method; roles=[]; extra={}
    physical=None
    if method=="full-factorial":
        factors=[]; count=1
        for v in space.independent_variables:
            levels=config.levels.get(v.id)
            if levels is None:
                if isinstance(v,ContinuousVariable):
                    raise ValueError("Continuous factorial factors require explicit physical levels")
                levels=v.values if isinstance(v,DiscreteVariable) else v.choices
            if not levels or len(set(levels))!=len(levels):
                raise ValueError("Factorial levels must be nonempty and unique")
            factors.append(levels); count*=len(levels)
        if count>config.budget:
            raise ValueError(f"Projected {count} attempts exceed budget {config.budget}")
        if set(config.levels)-{v.id for v in space.independent_variables}:
            raise ValueError("Unknown factorial factor")
        physical=[]
        for row in itertools.product(*factors):
            values=dict(zip((v.id for v in space.independent_variables),row))
            physical.append(PhysicalPoint(values=space._physical(values)))
        x=np.array([space.encode(p,purpose="optimizer").vector for p in physical])
        for j,v in enumerate(space.independent_variables):
            if not isinstance(v,ContinuousVariable):
                count_levels=len(v.values if isinstance(v,DiscreteVariable) else v.choices)
                x[:,j]=(x[:,j]+.5)/count_levels
        roles=["factorial"]*len(x)
    elif method in ("box-behnken","ccd"):
        if not all(isinstance(v,ContinuousVariable) and v.transform=="linear" for v in space.independent_variables):
            raise ValueError("Response-surface designs require linear continuous factors")
        count=(2*d*(d-1)+config.centers if method=="box-behnken" else 2**d+2*d+config.centers)
        if count>config.budget:
            raise ValueError(f"Projected {count} attempts exceed budget {config.budget}")
        if method=="box-behnken":
            if d<3: raise ValueError("Box-Behnken requires at least three factors")
            rows=[]
            for i,j in itertools.combinations(range(d),2):
                for a,b in itertools.product((-1.,1.),repeat=2):
                    row=np.zeros(d); row[i]=a; row[j]=b; rows.append(row)
            roles=["pair"]*len(rows)
        else:
            if config.ccd_type=="circumscribed" and d>0:
                raise ValueError("Circumscribed CCD axial points exceed hard bounds; choose inscribed or face-centered")
            alpha=(2**d)**.25 if config.ccd_type=="inscribed" else 1.
            rows=[np.array(row)/alpha for row in itertools.product((-1.,1.),repeat=d)]
            roles=["factorial"]*len(rows)
            for j in range(d):
                for a in (-1.,1.):
                    row=np.zeros(d); row[j]=a; rows.append(row); roles.append("axial")
            extra["alpha"]=alpha
        rows.extend(np.zeros(d) for _ in range(config.centers)); roles.extend(["center"]*config.centers)
        z=np.array(rows); rank,required=_quadratic_rank(z)
        if rank!=required: raise ValueError("Response-surface quadratic model is rank deficient")
        extra.update(quadratic_rank=rank,quadratic_terms=required)
        x=(z+1)/2
    else:
        n=config.n_samples
        if n>config.budget:
            raise ValueError(f"Projected {n} attempts exceed budget {config.budget}")
        if method=="sobol":
            if n&(n-1):
                raise ValueError(f"Sobol requires a balanced power-of-two count; requested {n}, next block {1<<(n-1).bit_length()}")
            scramble_seed=int(rng.integers(0,2**32))
            x=qmc.Sobol(d=d,scramble=True,seed=scramble_seed).random_base2(n.bit_length()-1)
            extra.update(scramble_seed=scramble_seed,sequence_start=0,sequence_end=n,requested_count=n,balanced_count=n)
        elif method=="lhs":
            x=qmc.LatinHypercube(d=d,seed=int(rng.integers(0,2**32))).random(n)
        else:
            best=None; best_score=None
            for _ in range(config.restarts):
                candidate=qmc.LatinHypercube(d=d,seed=int(rng.integers(0,2**32))).random(n)
                score=_score(candidate,config.criterion)
                if n>1:
                    for _ in range(config.iterations):
                        j=int(rng.integers(d)); a,b=rng.choice(n,2,replace=False)
                        candidate[a,j],candidate[b,j]=candidate[b,j],candidate[a,j]
                        proposed=_score(candidate,config.criterion)
                        if proposed<score:
                            score=proposed
                        else:
                            candidate[a,j],candidate[b,j]=candidate[b,j],candidate[a,j]
                if best is None or score<best_score:
                    best=candidate.copy(); best_score=score
            x=best
            extra.update(criterion=config.criterion,hybrid_order=("maximin","correlation"),
                         restarts=config.restarts,iterations=config.iterations,global_optimum_proven=False)
        roles=["sample"]*len(x)
    if physical is None:
        physical=[space.from_unit(row) for row in x]
    points=tuple(PhysicalPoint(id=f"{config.id}:{i}",values=p.values,
                             original_suggestion=tuple(x[i])) for i,p in enumerate(physical))
    groups={}
    for p in points:
        key=tuple(p.values[v.id] for v in space.independent_variables)
        groups.setdefault(key,[]).append(p.id)
    duplicate_groups=tuple(tuple(ids) for ids in groups.values() if len(ids)>1)
    distance,correlation=_quality(x)
    encoded=np.array([space.encode(p,purpose="optimizer").vector for p in points])
    decoded_latin=bool(_latin(encoded)) if all(isinstance(v,ContinuousVariable) for v in space.independent_variables) else False
    diagnostics=dict(adapter_version="1",codec_version=space.codec_version,
        numpy_version=np.__version__,scipy_version=scipy.__version__,
        projected_attempts=len(points),coded_latin=bool(_latin(x)),
        decoded_latin=decoded_latin,decoded_unique_count=len(groups),
        decoded_duplicate_count=len(points)-len(groups),minimum_distance=distance,
        squared_correlation=correlation,centered_discrepancy=float(qmc.discrepancy(x)),
        replicate_groups=tuple(tuple(ids) for ids in duplicate_groups if all(roles[int(i.rsplit(":",1)[1])]=="center" for i in ids)),**extra)
    return DesignBatch(id=config.id,points=points,coded=x,seed=config.seed,algorithm=method,
        factor_order=tuple(v.id for v in space.independent_variables),roles=tuple(roles),
        duplicate_groups=duplicate_groups,diagnostics=diagnostics,
        rng_state={"before":state_before,"after":copy.deepcopy(rng.bit_generator.state)})

