"""Explicit checked core dependency for standalone reconstructed probes."""
from pathlib import Path
import hashlib
import importlib
import json
import sys
ROOT=Path(__file__).resolve().parent

def load_core(workbench_root):
    root=Path(workbench_root).resolve(strict=True)
    manifest=json.loads((ROOT/'workbench_core_sources.json').read_text())
    if any(k=='caelab' or k.startswith('caelab.') for k in sys.modules):
        raise RuntimeError('CORE_ALREADY_IMPORTED')
    for item in manifest['files']:
        p=(root/item['path']).resolve(strict=True)
        if not p.is_relative_to(root):raise RuntimeError('CORE_PATH_ESCAPE')
        if hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:
            raise RuntimeError('CORE_SOURCE_HASH_MISMATCH:'+item['path'])
    sys.path.insert(0,str(root))
    checks=[]
    for item in manifest['files']:
        name=item['path'][:-3].replace('/','.')
        if name.endswith('.__init__'):name=name[:-9]
        mod=importlib.import_module(name)
        actual=Path(mod.__file__).resolve(strict=True)
        if actual!=(root/item['path']).resolve(strict=True):
            raise RuntimeError('CORE_LOADED_ORIGIN_MISMATCH:'+name)
        got=hashlib.sha256(actual.read_bytes()).hexdigest()
        if got!=item['sha256']:raise RuntimeError('CORE_LOADED_HASH_MISMATCH:'+name)
        checks.append({'module':name,'path_audit':str(actual),'sha256':got})
    return {'baseline_revision':manifest['baseline_revision'],'loaded_core':checks,
            'identity_scope':manifest['scope']}
