"""Actual existing ConstraintSpec tests; planned assessment is not runtime."""
import argparse
import json
from pathlib import Path
from workbench_source import load_core
ROOT=Path(__file__).resolve().parent
p=argparse.ArgumentParser();p.add_argument('--workbench-root',required=True,type=Path)
a=p.parse_args();identity=load_core(a.workbench_root)
from caelab.mdo.constraints import ConstraintSpec,assess_constraints
contract=json.loads((ROOT/'constraint_contract.json').read_text())
assert all(type(x['schema_version']) is int and x['schema_version']==1
           for x in contract['optimizer_responses'])
specs=tuple(ConstraintSpec(**x) for x in contract['optimizer_constraints'])
base={'fit_loss':.02,'bulk_ratio':100.,'stability_min_acoustic_over_mu':.5,
      'stability_derivative_discrepancy_over_mu':1e-5,
      'lateral_min_log_slope_over_mu':10.}
records=[]
for name,override,expected in [
    ('high_error_stable',{},'FEASIBLE'),
    ('finite_instability',{'stability_min_acoustic_over_mu':-.02},'INFEASIBLE'),
    ('invalid_numerical_scalar',{'stability_min_acoustic_over_mu':None},None)]:
    values={**base,**override}
    r=assess_constraints(values,specs,units={k:'' for k in values})
    got=r.feasibility.value if r.feasibility is not None else None
    assert got==expected,(name,got)
    records.append({'case':name,'existing_constraint_feasibility':got,
        'raw_residuals':r.raw_residuals.tolist(),
        'planned_objective_eligible':got is not None,
        'planned_fit_loss':values['fit_loss'] if got is not None else None,
        'planned_constraint_error':None if got is not None else 'CONSTRAINT_RESPONSE_INVALID'})
assert records[0]['planned_fit_loss']==records[1]['planned_fit_loss']==.02
assert records[2]['planned_fit_loss'] is None
out={'status':'passed','recovery_version':'B-RECOVERY-1','core_identity':identity,
     'scope':'actual existing constraints API and explicit planned assessment mapping; not new materials runtime',
     'cases':records}
(ROOT/'constraint_receipt.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
