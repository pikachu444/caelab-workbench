"""Reconstructed finite-domain COMP root-coverage numerical reference."""
from dataclasses import dataclass
from pathlib import Path
import itertools,json,math
import numpy as np
from scipy.optimize import brentq
ROOT=Path(__file__).resolve().parent
LOW,HIGH=1e-4,1e4
@dataclass
class EquilibriumFailure(Exception):
    reason:str

def terms(mode,lam,mu,alpha,K):
    cs=[]
    for m,a in zip(mu,alpha):
        c=2*m/a
        if mode=='uniaxial':
            cs.extend([(c/3*lam**(-a/3),a/3),(-c/3*lam**(2*a/3),-2*a/3)])
        elif mode=='biaxial':
            cs.extend([(2*c/3*lam**(-2*a/3),2*a/3),(-2*c/3*lam**(a/3),-a/3)])
        elif mode=='planar':
            cs.extend([(2*c/3*lam**(-a/3),2*a/3),
                       (-c/3*(lam**(2*a/3)+lam**(-a/3)),-a/3)])
        else:raise ValueError(mode)
    if mode=='uniaxial':cs.extend([(K*lam**2,4.),(-K*lam,2.)])
    elif mode=='biaxial':cs.extend([(K*lam**4,2.),(-K*lam**2,1.)])
    else:cs.extend([(K*lam**2,2.),(-K*lam,1.)])
    return cs
def value(cs,z):return math.fsum(c*z**p for c,p in cs)
def derivative(cs):return [(c*p,p-1) for c,p in cs if p!=0 and c!=0]
def interval(cs,lo,hi):
    aa=[];bb=[]
    for c,p in cs:
        a,b=c*lo**p,c*hi**p
        aa.append(min(a,b));bb.append(max(a,b))
    low,high=math.fsum(aa),math.fsum(bb)
    pad=64*np.finfo(float).eps*max(1.,sum(abs(v) for v in aa+bb))
    return low-pad,high+pad

def all_roots(cs):
    d=derivative(cs);roots=[];examined=0;deepest=0
    def visit(lo,hi,depth):
        nonlocal examined,deepest
        examined+=1;deepest=max(deepest,depth)
        a,b=interval(cs,lo,hi)
        if a>0 or b<0:return
        dl,dh=interval(d,lo,hi)
        fl,fh=value(cs,lo),value(cs,hi)
        if dl>0 or dh<0:
            if fl==0:roots.append(lo)
            if fh==0:roots.append(hi)
            if fl*fh<0:
                roots.append(brentq(lambda z:value(cs,z),lo,hi,
                             xtol=1e-14,rtol=1e-14,maxiter=100))
            return
        if depth>=20:raise EquilibriumFailure('LATERAL_ROOT_UNCERTIFIED')
        mid=math.sqrt(lo*hi)
        visit(lo,mid,depth+1);visit(mid,hi,depth+1)
    grid=np.logspace(-4,4,129)
    for lo,hi in zip(grid[:-1],grid[1:]):visit(float(lo),float(hi),0)
    merged=[]
    for z in sorted(roots):
        if not merged or abs(math.log(z/merged[-1]))>1e-9:merged.append(z)
        elif abs(value(cs,z))<abs(value(cs,merged[-1])):merged[-1]=z
    return merged,{'examined_cells':examined,'max_subdivision':deepest,
                   'search_stretch_domain':[LOW,HIGH]}
def select_unique(cs):
    rs,meta=all_roots(cs)
    if not rs:raise EquilibriumFailure('LATERAL_EQUILIBRIUM_NOT_BRACKETED')
    if len(rs)>1:raise EquilibriumFailure('AMBIGUOUS_EQUILIBRIUM')
    return rs[0],meta

def main():
    configs=list(itertools.product(['uniaxial','biaxial','planar'],[.75,1.,2.],
                                  [.1,1.,10.],[1.2,2.,6.],[10.,100.,1000.]))
    cases=[];forward={}
    for config in configs:
        mode,lam,m,a,K=config;cs=terms(mode,lam,[m],[a],K)
        z,meta=select_unique(cs)
        J=lam*z*z if mode=='uniaxial' else (lam*lam*z if mode=='biaxial' else lam*z)
        residual=abs(value(cs,z)/J)
        assert residual<1e-9,(config,residual)
        forward[config]=z
        cases.append({'mode':mode,'lambda':lam,'mu':m,'alpha':a,'K':K,
                      'root':z,'lateral_residual_MPa':residual,**meta})
    for config in reversed(configs):
        mode,lam,m,a,K=config
        z,_=select_unique(terms(mode,lam,[m],[a],K))
        assert z==forward[config]
    failures=[]
    for name,cs,expected in [
       ('no_root',[(1.,0.)],'LATERAL_EQUILIBRIUM_NOT_BRACKETED'),
       ('outside_domain',[(1.,1.),(-20000.,0.)],'LATERAL_EQUILIBRIUM_NOT_BRACKETED'),
       ('multiple_roots',[(1.,2.),(-2.5,1.),(1.,0.)],'AMBIGUOUS_EQUILIBRIUM'),
       ('tangent_uncertified',[(1.,2.),(-2.,1.),(1.,0.)],'LATERAL_ROOT_UNCERTIFIED')]:
        try:select_unique(cs)
        except EquilibriumFailure as e:
            assert e.reason==expected,(name,e.reason)
            failures.append({'case':name,'reason':e.reason})
        else:raise AssertionError(name)
    out={'status':'passed','recovery_version':'B-RECOVERY-1',
         'scope':'numerical finite-domain root coverage, not formal global proof or product runtime',
         'cases':cases,'case_count':len(cases),'order_invariance':'exact','failure_cases':failures}
    (ROOT/'equilibrium_receipt.json').write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps({k:v for k,v in out.items() if k!='cases'},indent=2))
if __name__=='__main__':main()
