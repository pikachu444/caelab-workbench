"""Atomic run receipts and per-node selection, independent of project JSON writes."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock, Timeout

ACTIVE_STATES = {"queued", "preparing", "running"}
TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        deadline = time.monotonic() + .5
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows readers/virus scanners may briefly hold a delete-denying
                # handle. Keep the old receipt intact and bound the retry.
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.01)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass
class NativeRun:
    id: str
    node_id: str
    workdir: Path
    solver_id: str = "calculix"
    state: str = "queued"
    parse_state: str = "unparsed"
    input_path: str = ""
    input_revision: str | None = None
    units: dict = field(default_factory=dict)
    unit_source: str = ""
    created_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    returncode: int | None = None
    pid: int | None = None  # diagnostic only; never used for process adoption/kill
    error: str = ""
    log_path: str = ""
    result: dict = field(default_factory=dict)
    snapshot: dict = field(default_factory=dict)
    stale: bool = False
    stale_reason: str = ""

    @property
    def run_id(self) -> str:
        return self.id


class RunStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self._cache = {}

    def lease(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lease = FileLock(self.root / "controller.lock", timeout=0, thread_local=False)
        try:
            lease.acquire()
        except Timeout as exc:
            raise ValueError("이 프로젝트의 다른 창/세션에서 계산 중입니다. 해당 계산을 끝내거나 취소하세요.") from exc
        return lease

    def write(self, run: NativeRun):
        data = asdict(run)
        data["workdir"] = str(run.workdir)
        atomic_json(run.workdir / "job.json", data)

    def read(self, path: Path) -> NativeRun:
        stat = path.stat()
        key = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        cached = self._cache.get(path)
        if cached is None or cached[0] != key:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("id") != path.parent.name or len(path.parent.name) != 32 or any(c not in "0123456789abcdef" for c in path.parent.name):
                raise ValueError(f"잘못된 실행 기록 ID: {path}")
            if data.get("state") not in ACTIVE_STATES | TERMINAL_STATES:
                raise ValueError(f"잘못된 실행 상태: {path}")
            data["workdir"] = path.parent
            data["log_path"] = str(path.parent / "job.log")
            run = NativeRun(**data)
            self._cache[path] = key, run
        return copy.deepcopy(self._cache[path][1])

    def runs(self, node_id=None) -> list[NativeRun]:
        runs = [self.read(path) for path in self.root.glob("*/job.json")]
        return sorted((run for run in runs if node_id is None or run.node_id == node_id), key=lambda run: run.created_at)

    def _selection_path(self, node_id):
        return self.root / "selected" / (hashlib.sha256(node_id.encode()).hexdigest() + ".json")

    def select(self, node_id, run_id):
        if not any(run.id == run_id and run.node_id == node_id for run in self.runs(node_id)):
            raise ValueError("선택할 실행이 이 프로젝트/노드에 없습니다.")
        atomic_json(self._selection_path(node_id), {"node_id": node_id, "run_id": run_id})

    def selected_id(self, node_id):
        path = self._selection_path(node_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("node_id") != node_id:
            raise ValueError(f"선택 실행의 노드가 다릅니다: {path}")
        return data["run_id"]

    def recover_unlocked(self):
        for run in self.runs():
            if run.state in ACTIVE_STATES:
                run.state = "interrupted"
                run.finished_at = utc_now()
                run.error = "이전 실행 제어가 중단되었습니다. 프로세스가 남아 있을 수 있습니다. 저장된 PID로 종료하거나 자동 재실행하지 않습니다."
                self.write(run)
            elif run.parse_state == "reading":
                run.parse_state = "failed"
                run.error = "이전 결과 판독이 중단되었습니다. 솔버 종료와 판독 상태를 별도로 확인하세요."
                self.write(run)

    def recover(self):
        if not self.root.exists():
            return
        try:
            lease = self.lease()
        except ValueError:
            return  # a live controller owns the store; readers do not interrupt it
        try:
            self.recover_unlocked()
        finally:
            lease.release()

    def copy_to(self, destination: Path) -> None:
        lease = self.lease()
        created = False
        try:
            self.recover_unlocked()
            destination.mkdir(parents=True, exist_ok=False)
            created = True
            shutil.copytree(self.root, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("controller.lock", "*.tmp"))
            copied = RunStore(destination)
            for run in copied.runs():
                copied.write(run)  # workdir/log paths belong to the new store
        except Exception:
            if created:
                shutil.rmtree(destination)
            raise
        finally:
            lease.release()
