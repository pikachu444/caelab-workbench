"""Atomic run receipts and per-node selection, independent of project JSON writes."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import tempfile
import time
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock, Timeout

ACTIVE_STATES = {"queued", "preparing", "running"}
TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retry_missing(read):
    # DrvFS can briefly report ENOENT during replace or before the first receipt
    # commit. Only that condition gets four 10-ms waits; real loss still raises.
    for attempt in range(5):
        try:
            return read()
        except FileNotFoundError:
            if attempt == 4:
                raise
            time.sleep(.01)


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
    def __init__(self, root: str | Path, *, expected: bool = False):
        self.root = Path(root).resolve()
        self.expected = expected
        self._cache = {}
        self._known_selections = set()

    def lease(self):
        if self.expected and not self.root.is_dir():
            raise ValueError(f"기대 실행 저장소가 없습니다: {self.root}")
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
        def read_opened():
            cached = self._cache.get(path)
            if cached is not None:
                stat = path.stat()  # a failed lookup must never reuse the old receipt
                key = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
                if cached[0] == key:
                    return copy.deepcopy(cached[1])
            # Metadata and JSON belong to the same opened file even if another
            # process replaces its pathname. Never fall back to cache on ENOENT.
            with path.open(encoding="utf-8") as stream:
                stat = os.fstat(stream.fileno())
                key = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
                data = json.load(stream)
            if not isinstance(data, dict):
                raise ValueError(f'실행 기록 객체가 필요합니다: {path}')
            if data.get("id") != path.parent.name or len(path.parent.name) != 32 or any(c not in "0123456789abcdef" for c in path.parent.name):
                raise ValueError(f"잘못된 실행 기록 ID: {path}")
            if data.get("state") not in ACTIVE_STATES | TERMINAL_STATES:
                raise ValueError(f"잘못된 실행 상태: {path}")
            data["workdir"] = path.parent
            data["log_path"] = str(path.parent / "job.log") if data.get('log_path') else ''
            run = NativeRun(**data)
            self._cache[path] = key, run
            return copy.deepcopy(run)
        return _retry_missing(read_opened)

    def runs(self, node_id=None) -> list[NativeRun]:
        issues = self.integrity_issues()
        if issues:
            raise ValueError("실행 기록을 온전히 읽을 수 없습니다: " + "; ".join(issues))
        return self._records(node_id)

    def _records(self, node_id=None) -> list[NativeRun]:
        folders = self.root.iterdir() if self.root.is_dir() else ()
        runs = []
        for folder in folders:
            valid_id = len(folder.name) == 32 and all(c in '0123456789abcdef' for c in folder.name)
            if folder.is_dir() and (valid_id or (folder / 'job.json').is_file()):
                runs.append(self.read(folder / 'job.json'))
        return sorted((run for run in runs if node_id is None or run.node_id == node_id), key=lambda run: run.created_at)

    def integrity_issues(self) -> list[str]:
        """Cheap availability checks; missing history is not a new empty project."""
        if not self.root.is_dir():
            return [f"기대 실행 저장소가 없습니다: {self.root}"] if self.expected else []
        issues = []
        selections = set((self.root / 'selected').glob('*.json')) | self._known_selections
        self._known_selections.update(selections)
        for selection in sorted(selections):
            try:
                data = self._read_selection(selection)
                run_id = data['run_id']
                try:
                    run = self.read(self.root / run_id / 'job.json')
                except FileNotFoundError:
                    issues.append(f"선택한 실행 기록이 없습니다: {self.root / run_id / 'job.json'}")
                    continue
                if run.node_id != data.get('node_id'):
                    issues.append(f'선택 실행 기록의 노드가 다릅니다: {selection}')
            except (OSError, ValueError, KeyError, TypeError) as error:
                issues.append(f"선택 실행을 읽지 못했습니다 ({selection}): {error}")
        try:
            records = self._records()
        except FileNotFoundError as error:
            return [*issues, f"실행 폴더의 기록이 없습니다: {error}"]
        except (OSError, ValueError, TypeError) as error:
            return [*issues, f"실행 기록 읽기 실패: {error}"]
        for run in records:
            if run.state in ACTIVE_STATES or run.parse_state == 'reading':
                continue
            if not isinstance(run.snapshot, dict):
                issues.append(f'실행 manifest 자료 형식이 잘못되었습니다: {run.id}')
                continue
            expected = set()
            for key in ('outputs', 'artifacts'):
                entries = run.snapshot.get(key, {})
                if not isinstance(entries, dict):
                    issues.append(f'{run.id}.snapshot.{key}: 상대 경로와 해시의 객체가 필요합니다.')
                    continue
                for relative, digest in entries.items():
                    if not isinstance(relative, str) or not relative.strip() or '\x00' in relative:
                        issues.append(f'{run.id}.snapshot.{key}: 파일 경로 문자열이 잘못되었습니다.')
                        continue
                    if not isinstance(digest, str):
                        issues.append(f'{run.id}.snapshot.{key}.{relative}: 해시 문자열이 필요합니다.')
                    expected.add(relative)
            for list_key in ('files', 'prerequisites'):
                files = run.snapshot.get(list_key, [])
                if not isinstance(files, list):
                    issues.append(f'{run.id}.snapshot.{list_key}: 파일 기록 목록이 필요합니다.')
                    continue
                for index, item in enumerate(files):
                    location = f'{run.id}.snapshot.{list_key}[{index}]'
                    if not isinstance(item, dict):
                        issues.append(f'{location}: 파일 기록 객체가 필요합니다.')
                        continue
                    keys = ('source_path', 'source_copy', 'execution_path') if list_key == 'files' else ('source_path',)
                    for key in keys:
                        relative = item.get(key)
                        if not isinstance(relative, str) or not relative.strip() or '\x00' in relative:
                            issues.append(f'{location}.{key}: 파일 경로 문자열이 필요합니다.')
                        elif key != 'source_path':
                            expected.add(relative)
                    if not isinstance(item.get('stat'), dict):
                        issues.append(f'{location}.stat: 파일 상태 객체가 필요합니다.')
            if run.log_path:
                expected.add('job.log')
            for relative in expected:
                try:
                    path = Path(relative)
                    target = (run.workdir / path).resolve()
                    if path.is_absolute() or '..' in path.parts or not target.is_relative_to(run.workdir.resolve()):
                        issues.append(f"실행 파일 참조가 소유 폴더 밖입니다: {run.id}: {relative}")
                    elif not target.is_file():
                        issues.append(f"기록된 실행 파일이 없습니다: {target}")
                except (OSError, ValueError, RuntimeError) as error:
                    issues.append(f'{run.id}: 실행 파일 경로를 읽지 못했습니다 ({relative}): {error}')
        return issues

    def _selection_path(self, node_id):
        return self.root / "selected" / (hashlib.sha256(node_id.encode()).hexdigest() + ".json")

    def select(self, node_id, run_id):
        if not any(run.id == run_id and run.node_id == node_id for run in self.runs(node_id)):
            raise ValueError("선택할 실행이 이 프로젝트/노드에 없습니다.")
        path = self._selection_path(node_id)
        atomic_json(path, {"node_id": node_id, "run_id": run_id})
        self._known_selections.add(path)

    def _read_selection(self, path):
        def read_opened():
            with path.open(encoding="utf-8") as stream:
                self._known_selections.add(path)
                data = json.load(stream)
            if not isinstance(data, dict):
                raise ValueError(f"선택 실행 기록 객체가 필요합니다: {path}")
            run_id = data.get('run_id')
            if not isinstance(run_id, str) or len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
                raise ValueError(f"선택 실행 ID가 잘못되었습니다: {path}")
            return data
        return _retry_missing(read_opened)

    def selected_id(self, node_id):
        path = self._selection_path(node_id)
        try:
            data = self._read_selection(path)
        except FileNotFoundError:
            if path in self._known_selections:
                raise
            return None  # a node that has never had a selection remains unselected
        if data.get("node_id") != node_id:
            raise ValueError(f"선택 실행의 노드가 다릅니다: {path}")
        return data["run_id"]

    def recover_unlocked(self):
        for run in self._records():
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
        destination = Path(destination).resolve()
        if destination == self.root or self.root in destination.parents:
            raise ValueError('실행 저장소 안으로 자기 복사를 할 수 없습니다.')
        lease = self.lease()
        created = False
        try:
            self.recover_unlocked()
            issues = self.integrity_issues()
            if issues:
                raise ValueError('; '.join(issues))
            documents = self._discussion_paths()
            with ExitStack() as locks:
                try:
                    for path in documents:
                        locks.enter_context(FileLock(str(path) + '.request.lock', timeout=0))
                        locks.enter_context(FileLock(str(path) + '.lock', timeout=0))
                except Timeout as error:
                    raise ValueError('대화 질문/저장이 진행 중입니다. 완료 후 다른 이름으로 저장하세요.') from error
                before = self._inventory()
                if documents != self._discussion_paths():
                    raise ValueError('복사 준비 중 대화 목록이 변경되었습니다. 다시 저장하세요.')
                destination.mkdir(parents=True, exist_ok=False)
                created = True
                shutil.copytree(self.root, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns('*.lock'))
                copied = RunStore(destination, expected=True)
                if before != self._inventory() or before != copied._inventory() or documents != self._discussion_paths():
                    raise ValueError('복사 중 실행/대화/자산 파일이 변경되었습니다. 다시 저장하세요.')
                from .evidence import verify_evidence
                for path in documents:
                    document = json.loads(path.read_text(encoding='utf-8'))
                    if not isinstance(document, dict):
                        raise ValueError(f'대화 폴더의 JSON 자료 형식이 잘못되었습니다: {path}')
                    for evidence in document.get('evidences', {}).values():
                        problems = verify_evidence(evidence, destination / path.parent.relative_to(self.root) / 'assets')
                        if problems:
                            raise ValueError('대화 근거 자산이 온전하지 않습니다: ' + '; '.join(problems))
                for run in copied.runs():
                    copied.write(run)  # workdir/log paths belong to the new store
        except Exception:
            if created:
                shutil.rmtree(destination)
            raise
        finally:
            lease.release()

    def _discussion_paths(self):
        return sorted(path for path in (self.root / 'discussions').rglob('*.json')
                      if 'assets' not in path.relative_to(self.root / 'discussions').parts)

    def _inventory(self):
        inventory = {}
        for path in sorted(self.root.rglob('*')):
            if path.name.endswith('.lock'):
                continue
            if path.is_symlink():
                raise ValueError(f'저장소의 symbolic link는 복사하지 않습니다: {path}')
            relative = path.relative_to(self.root).as_posix()
            if path.is_dir():
                inventory[relative + '/'] = None
                continue
            from .input_snapshot import fingerprint
            before = fingerprint(path)
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            if fingerprint(path) != before:
                raise ValueError(f'검사 중 저장소 파일이 변경되었습니다: {path}')
            inventory[relative] = digest.hexdigest()
        return inventory
