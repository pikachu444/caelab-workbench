"""Check reconstructed static bytes and freshly executed numeric references."""
from pathlib import Path
import hashlib,json
import numpy as np
ROOT=Path(__file__).resolve().parent
manifest=json.loads((ROOT/'asset_manifest.json').read_text())
for item in manifest['files']:
    p=ROOT/item['path'];assert p.is_file(),p
    assert hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256'],p
contract=json.loads((ROOT/'constraint_contract.json').read_text())
assert len(contract['optimizer_responses'])==5
assert all(type(x['schema_version']) is int and x['schema_version']==1
           for x in contract['optimizer_responses'])
receipt=json.loads((ROOT/'probe_receipt.json').read_text())
expected=json.loads((ROOT/'expected.json').read_text())
for key,spec in expected['checks'].items():
    assert np.allclose(receipt['checks'][key],spec['value'],atol=spec['atol'],rtol=0),key
assert receipt['checks']['VIS_synthetic_fit_loss']<1e-18
assert abs(receipt['checks']['CYC_final_coarse_MPa']-
           receipt['checks']['CYC_final_refined_MPa'])<.05
for name,count in [('ogden.csv',78),('volume.csv',5),('ogden_compressible.csv',26),
                   ('ogden_compressible_biax_validation.csv',21),
                   ('dma.csv',305),('relax.csv',183),('jc_flow.csv',105),('jc_failure.csv',20)]:
    with (ROOT/name).open() as f:rows=sum(1 for _ in f)-1
    assert rows==count,(name,rows,count)
print(json.dumps({'status':'passed','recovery_version':'B-RECOVERY-1',
    'frozen_static_files':len(manifest['files']),
    'numeric_expected_checks':len(expected['checks']),
    'scope':'reconstructed asset integrity and fresh numerical reference outputs, not native/product acceptance'}))
