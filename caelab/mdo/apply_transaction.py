"""Hash-checked, recoverable native closure replacements; never starts a process."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from contextlib import ExitStack
from pathlib import Path

from filelock import FileLock
from caelab.native.run_store import atomic_json


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_staged(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=path.name+".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def recover(root):
    journal = Path(root) / "apply-pending.json"
    if not journal.is_file():
        return None
    data = json.loads(journal.read_text(encoding="utf-8"))
    # Validate the complete closure before restoring any file, preserving external edits.
    for item in data["files"]:
        path = Path(item["path"])
        actual = digest(path.read_bytes())
        if actual not in (item["before_sha256"], item["after_sha256"]):
            raise ValueError(f"Apply recovery conflict; preserve externally edited file: {path}")
        if digest(Path(item["backup"]).read_bytes()) != item["before_sha256"]:
            raise ValueError(f"Apply recovery backup is damaged: {item['backup']}")
    for item in reversed(data["files"]):
        path = Path(item["path"])
        if digest(path.read_bytes()) == item["after_sha256"]:
            write_staged(path, Path(item["backup"]).read_bytes())
    data["state"] = "rolled_back"
    atomic_json(Path(root) / "apply-history" / (data["id"]+".json"), data)
    journal.unlink()
    return data


def replace_closure(root, replacements, expected, metadata):
    root = Path(root)
    if (root / "apply-pending.json").exists():
        raise ValueError("An interrupted apply must be recovered before applying")
    tx_id = uuid.uuid4().hex
    files = []
    with ExitStack() as locks:
        for raw in sorted(expected):
            locks.enter_context(FileLock(raw+".caelab-apply.lock", timeout=0))
        for raw, before in expected.items():
            path = Path(raw)
            if digest(path.read_bytes()) != before:
                raise ValueError(f"Native input changed after apply preview: {path}")
            if not path.stat().st_mode & stat.S_IWRITE or not os.access(path, os.W_OK):
                raise PermissionError(f"Native target is read-only: {path}")
        for raw, content in replacements.items():
            path = Path(raw)
            original = path.read_bytes()
            if original == content:
                continue
            if shutil.disk_usage(path.parent).free < len(original)+len(content)+65536:
                raise OSError(f"Not enough space to stage and back up {path}")
            backup = path.with_name(path.name+".bak")
            if backup.exists():
                version = path.with_name(path.name+"."+tx_id+".previous.bak")
                with version.open("xb") as stream:
                    stream.write(backup.read_bytes()); stream.flush(); os.fsync(stream.fileno())
            write_staged(backup, original)
            immutable = path.with_name(path.name+"."+tx_id+".bak")
            with immutable.open("xb") as stream:
                stream.write(original); stream.flush(); os.fsync(stream.fileno())
            if digest(immutable.read_bytes()) != expected[raw]:
                raise OSError(f"Backup verification failed: {path}")
            files.append(dict(path=raw, before_sha256=expected[raw], after_sha256=digest(content), backup=str(immutable)))
        receipt = dict(id=tx_id, state="prepared", files=files, **metadata)
        atomic_json(root / "apply-pending.json", receipt)
        try:
            # Recheck every source even if no substitution modifies that source.
            for raw, before in expected.items():
                if digest(Path(raw).read_bytes()) != before:
                    raise ValueError(f"Native input changed during backup: {raw}")
            for item in files:
                path = Path(item["path"])
                if digest(path.read_bytes()) != item["before_sha256"]:
                    raise ValueError(f"Native input changed immediately before replacement: {path}")
                write_staged(path, replacements[str(path)])
                receipt["state"] = "replacing"
                atomic_json(root / "apply-pending.json", receipt)
            receipt["state"] = "committed"
            # The journal remains until service metadata commit, making that crash recoverable too.
            atomic_json(root / "apply-pending.json", receipt)
            return receipt
        except BaseException:
            recover(root)
            raise


def finish(root, receipt):
    atomic_json(Path(root) / "apply-history" / (receipt["id"]+".json"), receipt)
    (Path(root) / "apply-pending.json").unlink()
