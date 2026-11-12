"""Reconstructed existing-core + actual pyvisco nonlinear reference fit.
Not the proposed materials service/GUI. Run with explicit --workbench-root.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from pyvisco import prony
from workbench_source import load_core
ROOT=Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--workbench-root',required=True,type=Path)
args=parser.parse_args()
identity=load_core(args.workbench_root)
from caelab.mdo.variables import ContinuousVariable,VariableSpace
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.optimize import OptimizationProblem,OptimizationConfig,EvaluationObservation,optimize

sources=json.loads((ROOT/'source_manifest.json').read_text())['sources']
expected=next(x['source_bytes_sha256'] for x in sources if x['id']=='B-SRC-VIS')
loaded_hash=hashlib.sha256(Path(prony.__file__).read_bytes()).hexdigest()
assert loaded_hash==expected,'PYVISCO_SOURCE_HASH_MISMATCH'
raw=np.genfromtxt(ROOT/'relax.csv',delimiter=',',names=True,dtype=None,encoding='utf-8')
train=raw[(raw['split']=='train')&(raw['temperature_K']==293.15)]
t=train['time_s'];y=train['relaxation_modulus_MPa']
z=np.log10(t);q=np.zeros_like(z)
q[0]=(z[1]-z[0])/2;q[-1]=(z[-1]-z[-2])/2
q[1:-1]=(z[2:]-z[:-2])/2;q/=q.sum()
params=[('E0','MPa',10.,1000.,90.,'log'),
        ('a1','',1e-8,.475,.18,'linear'),('a2','',1e-8,.475,.27,'linear'),
        ('tau1','s',.001,.3,.2,'log'),('tau2','s',1.,1000.,8.,'log')]
space=VariableSpace(variables=tuple(ContinuousVariable(id=k,name=k,unit=u,
      lower=lo,upper=hi,initial=x,transform=tr) for k,u,lo,hi,x,tr in params))
class Evaluator:
    def __init__(self):self.count=0
    def evaluate_batch(self,requests):
        out={}
        for r in requests:
            v=r.values
            pred=v['E0']*prony.E_relax_norm(t,np.array([v['a1'],v['a2']]),
                                           np.array([v['tau1'],v['tau2']]))
            loss=float(np.dot(q,((pred-y)/100.)**2));self.count+=1
            out[r.id]=EvaluationObservation(request_id=r.id,point_id=r.point_id,
                responses={'fit_loss':loss},mode='ACTUAL',
                source_id='B-synthetic-pyvisco-eval:'+hashlib.sha256(pred.tobytes()).hexdigest())
        return out
ev=Evaluator()
cfg=OptimizationConfig(algorithm='slsqp',seed=17,max_evaluations=1500,
         max_iterations=200,ftol=1e-12,finite_difference_step=1e-7)
result=optimize(OptimizationProblem(space=space,
    objectives=(ObjectiveSpec(id='fit',response_id='fit_loss'),),
    differentiable=True,response_units={'fit_loss':''}),ev,cfg)
best=min(result.actual_archive,key=lambda x:x.responses['fit_loss'])
v=dict(best.values)
assert result.termination_reason=='CONVERGED'
assert best.responses['fit_loss']<1e-10
assert all(abs(v[k]-truth)<.003 for k,truth in
           [('E0',100.),('a1',.2),('a2',.3),('tau1',.1),('tau2',10.)])
hold=raw[(raw['split']=='holdout')&(raw['temperature_K']==323.15)]
aT=10**(-5*30/130)
hp=v['E0']*prony.E_relax_norm(hold['time_s']/aT,np.array([v['a1'],v['a2']]),
                             np.array([v['tau1'],v['tau2']]))
receipt={'status':'passed','recovery_version':'B-RECOVERY-1',
    'scope':'existing optimizer + actual pyvisco on synthetic spectrum; not materials product runtime',
    'termination':result.termination_reason,'fit_loss':best.responses['fit_loss'],
    'parameters':v,'observations':ev.count,
    'holdout_loss':float(np.mean(((hp-hold['relaxation_modulus_MPa'])/100)**2)),
    'holdout_used_in_fit':False,'WLF_coefficients':'fixed, not inferred',
    'data_sha256':hashlib.sha256((ROOT/'relax.csv').read_bytes()).hexdigest(),
    'core_identity':identity,
    'loaded_pyvisco':{'path_audit':prony.__file__,'sha256':loaded_hash}}
(ROOT/'optimizer_receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt,indent=2))
