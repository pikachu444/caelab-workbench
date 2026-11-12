"""Fetch the exact official Linux release for a new native reference run."""
from pathlib import Path
import hashlib,json,os,stat,subprocess,sys,urllib.request,zipfile
dest=Path(sys.argv[1]).resolve()
dest.mkdir(parents=True,exist_ok=True)
archive=dest/"OpenCourant_linux64.zip"
url="https://github.com/OpenCourant/OpenCourant/releases/download/latest-20261011/OpenCourant_linux64.zip"
with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"caelab-reference-ci"}),timeout=90) as response:
    with archive.open("xb") as output:
        while chunk:=response.read(1024*1024): output.write(chunk)
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
assert digest=="6cb419677ca36d73acb96218cc0cf5e4f7d50494d9c7812ba27935e3c0f8ec94",digest
unpack=dest/"unpacked"
with zipfile.ZipFile(archive) as z:
    names=z.namelist()
    for name in names:
        p=Path(name)
        assert not p.is_absolute() and ".." not in p.parts,name
    candidates=[n for n in names if n.endswith("/exec/starter_linux64_gf")]
    assert len(candidates)==1,candidates
    z.extractall(unpack)
root=(unpack/candidates[0]).parent.parent
for p in (root/"exec").iterdir():
    if p.is_file():p.chmod(p.stat().st_mode|stat.S_IXUSR|stat.S_IXGRP|stat.S_IXOTH)
(root/"exec"/"starter_linux64_gf").resolve(strict=True)
ldd=subprocess.run(["ldd",str(root/"exec"/"starter_linux64_gf")],capture_output=True,text=True)
(dest/"ldd.txt").write_text(ldd.stdout+ldd.stderr,encoding="utf-8")
assert ldd.returncode==0,ldd.stderr
with open(os.environ["GITHUB_ENV"],"a",encoding="utf-8") as env:env.write("OC_REFERENCE_ROOT="+str(root)+"\n")
(dest/"download_receipt.json").write_text(json.dumps({"url":url,"sha256":digest,"scope":"new exact release fetch; native execution is a separate step","root_audit":str(root)},indent=2)+"\n")
