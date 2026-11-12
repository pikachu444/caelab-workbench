"""Actual pyvisco evaluator source bytes and numerical API checks."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent
def main():
    from pyvisco import prony,shift
    byid={x['id']:x for x in json.loads((ROOT/'source_manifest.json').read_text())['sources']}
    checks=[]
    for mod,sid in [(prony,'B-SRC-VIS'),(shift,'B-SRC-WLF')]:
        got=hashlib.sha256(Path(mod.__file__).read_bytes()).hexdigest()
        assert got==byid[sid]['source_bytes_sha256'],(sid,got)
        checks.append({'source_id':sid,'loaded_file_sha256':got,'source_match':True})
    a=np.array([.2,.3]);tau=np.array([.1,10.])
    er=float(prony.E_relax_norm(np.array([1.]),a,tau)[0])*100
    freq=prony.E_freq_norm(np.array([1.]),a,tau)*100
    loga=float(shift.WLF(313.15,293.15,5.,100.))
    assert abs(er-77.14603053967404)<1e-10
    assert np.allclose(freq,[79.9009900990099,4.9504950495049505],atol=1e-10,rtol=0)
    assert abs(loga+5/6)<1e-13
    r={'status':'passed','actual_library_executed':True,
       'version':importlib.metadata.version('pyvisco'),'loaded_source_checks':checks,
       'relaxation_MPa':er,'storage_loss_MPa':freq.tolist(),'WLF_return_log10_aT':loga,
       'scope':'actual pinned evaluator source/API; not product/Windows/experimental acceptance'}
    (ROOT/'pyvisco_receipt.json').write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(r,indent=2))
if __name__=='__main__':main()
