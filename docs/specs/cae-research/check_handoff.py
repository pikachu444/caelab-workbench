"""Validate delivered recovery assets and current core source bytes, not product functionality."""
from pathlib import Path
import ast, hashlib, json, sys
root=Path(__file__).resolve().parent
inventory=json.loads((root/"recovery-assets.json").read_text(encoding="utf-8"))
for entry in inventory["files"]:
    path=Path(entry["path"])
    if path.is_absolute() or ".." in path.parts: raise ValueError("bad inventory path")
    full=Path.cwd()/path
    data=full.read_bytes()
    gitsha=hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
    if gitsha!=entry["blob_sha"]: raise ValueError("GIT_BLOB_MISMATCH:"+str(path))
    if entry.get("sha256") and hashlib.sha256(data).hexdigest()!=entry["sha256"]:
        raise ValueError("SHA256_MISMATCH:"+str(path))
for p in root.rglob("*.json"):
    json.loads(p.read_text(encoding="utf-8"))
for p in root.rglob("*.py"):
    ast.parse(p.read_text(encoding="utf-8"),filename=str(p))
for p in root.glob("*.md"):
    text=p.read_text(encoding="utf-8")
    if "\x00" in text: raise ValueError("NUL in document")
print(json.dumps({"status":"pass","asset_files":len(inventory["files"]),
 "scope":"exact committed file identity, JSON readability, Python syntax; no product/native acceptance"}))
