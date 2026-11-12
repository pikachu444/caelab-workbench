"""NEW RECONSTRUCTED runner; no execution claimed after environment loss.
Use recovered RAD inputs and an explicitly selected external official Linux package.
"""
from pathlib import Path
import argparse,os,subprocess,json,hashlib,platform,shutil
p=argparse.ArgumentParser()
p.add_argument("--opencourant-root",type=Path,required=True)
p.add_argument("--fixtures",type=Path,required=True)
p.add_argument("--output-root",type=Path,required=True)
a=p.parse_args();root=a.opencourant_root.resolve();fixtures=a.fixtures.resolve();out=a.output_root.resolve()
expected={"starter_linux64_gf":"0bbea37273656b270bea53cf756b115bcd5d9a8875a3c41283922a3b927719d1","engine_linux64_gf":"54cc9fc2a7cea6f1215afb6da9109d368650ff1fa7985625cb111f47d7e92be4","th_to_csv_linux64_gf":"704d647331ae4befd329d18b991d644f2af930abd117c5d204c23925f75fc8db"}
for tool,digest in expected.items():
 exe=root/"exec"/tool
 if not exe.is_file():raise SystemExit("MISSING_EXECUTABLE: "+tool)
 if hashlib.sha256(exe.read_bytes()).hexdigest()!=digest:raise SystemExit("BINARY_IDENTITY_MISMATCH: "+tool)
out.mkdir(parents=True,exist_ok=True)
env=dict(os.environ);env.update(LD_LIBRARY_PATH=str(root/"extlib/hm_reader/linux64")+":"+str(root/"extlib/h3d/lib/linux64"),RAD_CFG_PATH=str(root/"hm_cfg_files"),OMP_NUM_THREADS="1",KMP_STACKSIZE="400m")
for name,dt in [("ka1_dt2e6",2e-6),("ka1_dt1e6",1e-6)]:
 d=out/name;d.mkdir(exist_ok=False)
 shutil.copy2(fixtures/"foam_ka1_0000.rad",d/"foam_ka1_0000.rad")
 lines=(fixtures/"foam_ka1_0001.rad").read_text().splitlines()
 insert=next((k for k,line in enumerate(lines) if line.strip() in ("/END","/END/ENGINE")),len(lines))
 lines[insert:insert]=["/DTIX",f"{dt:.12g} {dt:.12g}"]
 (d/"foam_ka1_0001.rad").write_text("\n".join(lines)+"\n")
 cmds=[]
 for tool,arg,log in [("starter_linux64_gf","foam_ka1_0000.rad","starter.log"),("engine_linux64_gf","foam_ka1_0001.rad","engine.log"),("th_to_csv_linux64_gf","foam_ka1T01","converter.log")]:
  argv=[str(root/"exec"/tool)]+([arg,arg] if tool.startswith("th_") else ["-i",arg,"-nt","1"])
  with (d/log).open("w") as f:r=subprocess.run(argv,cwd=d,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=120)
  cmds.append({"argv":argv,"returncode":r.returncode,"log":log})
  if tool.startswith("th_") and r.returncode==0:
   if not (d/(arg+"_TITLES")).is_file():raise SystemExit("MISSING_SOURCE_TITLES")
   if not (d/(arg+".csv")).is_file():raise SystemExit("MISSING_CONVERTER_CSV")
  if r.returncode:raise SystemExit(f"{name} {tool} failed {r.returncode}; retain partial logs")
 receipt={"kind":"new reconstructed reference runner actual execution only when this program is run","platform":platform.platform(),"release_declared_source_revision":"cb760c206e346eff12161a2268b134ef9d48a4ef","release":"latest-20261011","commands":cmds,"binaries":expected,"files":{x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in d.iterdir() if x.is_file() and x.name!="probe_receipt.json"}}
 (d/"probe_receipt.json").write_text(json.dumps(receipt,indent=2)+"\n")
 print(name,"completed new reference execution; this is not a product workflow")
