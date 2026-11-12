"""Relocate reconstructed bundle/core and deny reads from both originals."""
import argparse,hashlib,json,os,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
p=argparse.ArgumentParser()
p.add_argument('--workbench-root',required=True,type=Path)
p.add_argument('--destination',required=True,type=Path)
a=p.parse_args();core=a.workbench_root.resolve(strict=True);dest=a.destination.resolve()
if dest.is_relative_to(ROOT) or dest.is_relative_to(core):
    raise ValueError('destination must be outside sources')
if dest.exists() and any(dest.iterdir()):raise ValueError('destination must be empty/new')
dest.mkdir(parents=True,exist_ok=True)
shutil.copytree(ROOT,dest/'B',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
shutil.copytree(core/'caelab',dest/'core'/'caelab',
                ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
guard="""import os,sys,runpy
from pathlib import Path
denied=tuple(os.path.realpath(x) for x in sys.argv[1:3])
target=sys.argv[3];args=sys.argv[4:]
def audit(event,args):
 if event=="open" and args and isinstance(args[0],(str,bytes,os.PathLike)):
  q=os.path.realpath(os.fsdecode(args[0]))
  if any(q==x or q.startswith(x+os.sep) for x in denied):
   raise PermissionError("ORIGINAL_SOURCE_READ_DENIED:"+q)
sys.addaudithook(audit)
sys.path.insert(0,str(Path(target).parent))
sys.argv=[target,*args]
runpy.run_path(target,run_name="__main__")
"""
commands=[('reference_probe.py',[],[0]),('equilibrium_probe.py',[],[0]),
          ('run_pyvisco.py',[],[0]),
          ('optimizer_probe.py',['--workbench-root',str(dest/'core')],[0]),
          ('constraint_probe.py',['--workbench-root',str(dest/'core')],[0]),
          ('check_assets.py',[],[0]),('run_neml.py',[],[20,21])]
results=[]
for name,args,allowed in commands:
    argv=[sys.executable,'-c',guard,str(ROOT),str(core),str(dest/'B'/name),*args]
    r=subprocess.run(argv,cwd=dest,env=os.environ.copy(),capture_output=True,text=True)
    (dest/(name+'.stdout')).write_text(r.stdout)
    (dest/(name+'.stderr')).write_text(r.stderr)
    if r.returncode not in allowed:
        print(r.stdout);print(r.stderr);raise RuntimeError((name,r.returncode,allowed))
    results.append({'script':name,'arguments':args,'exit_code':r.returncode,
         'stdout_sha256':hashlib.sha256(r.stdout.encode()).hexdigest(),
         'stderr_sha256':hashlib.sha256(r.stderr.encode()).hexdigest()})
for name in ['optimizer_receipt.json','constraint_receipt.json']:
    r=json.loads((dest/'B'/name).read_text())
    assert all(Path(x['path_audit']).is_relative_to(dest/'core')
               for x in r['core_identity']['loaded_core'])
r={'status':'reference_checks_passed_NEML_backend_or_source_gate_unmet',
   'recovery_version':'B-RECOVERY-1','original_bundle_and_core_read_denial':True,
   'destination_audit':str(dest),'explicit_dependency_python':sys.executable,
   'commands':results,'NEML_gate':'exit20 unavailable or exit21 numerical pass/source unverified',
   'scope':'fresh cold-copy reference/API execution, not product/Windows acceptance'}
(dest/'cold_reproduction_receipt.json').write_text(json.dumps(r,indent=2)+'\n')
(ROOT/'cold_reproduction_receipt.json').write_text(json.dumps(r,indent=2)+'\n')
print(json.dumps(r,indent=2))
