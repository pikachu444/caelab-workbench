"""Job ownership: exactly one place runs solvers and records state."""

from __future__ import annotations

import itertools
import copy
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .model.schema import StructuralModel
from .connections import LaunchRequest
from .solvers.base import ResultSet, SolverConverter
from .validate.checker import validate_model
from .native.input_snapshot import snapshot_input
from .native.run_store import NativeRun, RunStore, TERMINAL_STATES, utc_now

JOB_STATES = ("queued", "preparing", "running", "succeeded", "failed", "cancelled")


@dataclass
class Job:
    id: int
    solver_id: str
    workdir: Path
    state: str = "queued"
    history: list = field(default_factory=lambda: ["queued"])
    result: ResultSet | None = None

    def _set(self, state: str) -> None:
        self.state = state
        self.history.append(state)


@dataclass
class LaunchRecord:
    id: int
    request: LaunchRequest
    state: str = "opened"
    process: subprocess.Popen | None = None
    error: str = ""

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process is not None else None


class _NativeCancelled(RuntimeError):
    pass


def _native_exited(process):
    if os.name == "posix" and hasattr(os, "waitid"):
        # WNOWAIT retains the leader PID until its owned group is cleaned up.
        return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    return process.poll() is not None


def _finish_owned(control, cancel=False):
    process, group = control["process"], control["group"]
    if group is not None:
        try:
            group.close()  # only this Windows Job handle owns these descendants
        finally:
            control["group"] = None
    elif os.name == "posix" and process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGTERM if cancel else signal.SIGKILL)
            if cancel:
                time.sleep(0.1)
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.terminate()  # live Popen handle only; never a restored PID
    return process.wait(timeout=3)


class _WindowsRunGroup:
    """Kernel Job ownership, with solver release gated until assignment succeeds."""
    def __init__(self, process):
        import ctypes as c
        from ctypes import wintypes as w
        # Structure layouts follow the Windows SDK JOBOBJECT_* definitions.
        class Limits(c.Structure):
            _fields_ = [("process_time", c.c_longlong), ("job_time", c.c_longlong), ("flags", w.DWORD),
                        ("minimum", c.c_size_t), ("maximum", c.c_size_t), ("active", w.DWORD),
                        ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class Extended(c.Structure):
            _fields_ = [("limits", Limits), ("io", c.c_ulonglong * 6), ("memory", c.c_size_t * 4)]
        self.api = c.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes, self.api.CreateJobObjectW.restype = [w.LPVOID, w.LPCWSTR], w.HANDLE
        self.api.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, w.LPVOID, w.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        try:
            information = Extended()
            information.limits.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.api.SetInformationJobObject(self.handle, 9, c.byref(information), c.sizeof(information)):
                raise c.WinError(c.get_last_error())
            if not self.api.AssignProcessToJobObject(self.handle, w.HANDLE(int(process._handle))):
                raise c.WinError(c.get_last_error())
        except Exception:
            self.close()
            raise

    def close(self):
        if self.handle:
            if not self.api.CloseHandle(self.handle):
                raise OSError("Windows Job handle 종료를 확인하지 못했습니다.")
            self.handle = None


class JobManager:
    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.jobs: dict[int, Job] = {}
        self.launches: dict[int, LaunchRecord] = {}
        self._native_lock = threading.RLock()
        self._native: dict[str, NativeRun] = {}
        self._controls: dict[str, dict] = {}
        self._closed = False

    def native_snapshot(self, run_id: str) -> NativeRun | None:
        with self._native_lock:
            run = self._native.get(run_id)
            return copy.deepcopy(run) if run is not None else None

    def submit_native(self, store: RunStore, node_id: str, reference, status, timeout=3600, prerequisites=()) -> NativeRun:
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout은 양의 유한한 초 단위 값이어야 합니다.")
        with self._native_lock:
            if self._closed:
                raise ValueError("종료 중인 Workbench에는 계산을 제출할 수 없습니다.")
            lease = store.lease()
            try:
                store.recover_unlocked()
                run_id = uuid.uuid4().hex
                run = NativeRun(run_id, node_id, store.root / run_id, solver_id=status.spec.id, input_path=reference.path,
                                units=dict(reference.units), unit_source=reference.unit_source,
                                log_path=str(store.root / run_id / "job.log"))
                control = {"cancel": threading.Event(), "process": None, "group": None, "thread": None, "store": store,
                           "prerequisites": copy.deepcopy(prerequisites), "stale_input": reference.stale, "cleanup_failed": False}
                self._native[run_id], self._controls[run_id] = run, control
                try:
                    run.workdir.mkdir(parents=True)
                    store.write(run)
                    store.select(node_id, run.id)
                except Exception as exc:
                    run.state, run.error, run.finished_at = "failed", f"실행 기록 준비 실패: {exc}", utc_now()
                    try:
                        store.write(run)
                    except Exception:
                        pass
                    lease.release()
                    return copy.deepcopy(run)
                worker = threading.Thread(target=self._run_native, args=(run_id, status, timeout, lease), daemon=True,
                                          name=f"caelab-ccx-{run_id[:8]}")
                control["thread"] = worker
                try:
                    worker.start()
                except Exception as exc:
                    run.state, run.error, run.finished_at = "failed", f"worker 시작 실패: {exc}", utc_now()
                    control["thread"] = None
                    store.write(run)
                    lease.release()
                return copy.deepcopy(run)
            except Exception:
                lease.release()
                raise

    def _run_native(self, run_id, status, timeout, lease):
        from .native.adapter import get_solver_adapter
        adapter = get_solver_adapter(status.spec.id)

        with self._native_lock:
            run, control = self._native[run_id], self._controls[run_id]
        store = control["store"]
        deadline = time.monotonic() + timeout

        def check():
            if control["cancel"].is_set():
                raise _NativeCancelled("사용자가 계산을 취소했습니다.")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"계산 제한 시간 {timeout:g}초를 초과했습니다.")

        def update(**values):
            with self._native_lock:
                for key, value in values.items():
                    setattr(run, key, copy.deepcopy(value))
                store.write(run)

        try:
            check()
            update(state="preparing")
            from .project import file_revision
            from .native.input_snapshot import fingerprint
            lineage = []
            if control["stale_input"]:
                raise ValueError("상위 파일 변경으로 오래된 입력 덱입니다. 네이티브 도구에서 다시 작성하고 파일을 등록하세요.")
            for reference in control["prerequisites"]:
                check()
                before = fingerprint(reference.path)
                if file_revision(reference.path) != reference.revision or reference.stale or fingerprint(reference.path) != before:
                    raise ValueError(f"상위 파일이 변경/누락되어 입력 덱을 다시 작성·등록해야 합니다: {reference.path}")
                lineage.append({"source_path": reference.path, "revision": reference.revision, "stat": before})
            snapshot = snapshot_input(run.input_path, run.workdir, check)
            snapshot["prerequisites"] = lineage
            update(snapshot=snapshot, input_revision=snapshot["input_revision"])
            check()
            request = adapter.build_run_request(status, run.workdir, Path(run.input_path))
            snapshot["execution"] = {"argv": request.argv, "cwd": str(request.cwd), "executable": status.path,
                                     "discovery_source": status.source, "native_environment": status.execution_environment,
                                     "host_os": status.host_os,
                                     "runtime_env_names": sorted(status.runtime_env)}
            update(snapshot=snapshot)
            with Path(run.log_path).open("wb") as log:
                if os.name == "nt":
                    gate = run.workdir / ".start-solver"
                    script = (
                        "import json,subprocess,sys,time; from pathlib import Path; "
                        "gate=Path(sys.argv[1]); deadline=time.monotonic()+30; "
                        "exec('while not gate.exists():\\n if time.monotonic()>deadline: raise SystemExit(125)\\n time.sleep(.02)'); "
                        # pythonw's all-default Popen path does not forward the redirected log handles.
                        "raise SystemExit(subprocess.Popen(json.loads(sys.argv[2]),stdin=subprocess.DEVNULL,"
                        "stdout=sys.stdout,stderr=subprocess.STDOUT,shell=False).wait())"
                    )
                    process = subprocess.Popen([sys.executable, "-c", script, str(gate), json.dumps(request.argv)],
                                               cwd=request.cwd, env=request.env, stdout=log, stderr=subprocess.STDOUT,
                                               shell=False, creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
                    control["process"] = process
                    control["group"] = _WindowsRunGroup(process)
                    check()
                    gate.touch()
                else:
                    process = subprocess.Popen(request.argv, cwd=request.cwd, env=request.env, stdout=log,
                                               stderr=subprocess.STDOUT, shell=False, start_new_session=True)
                    control["process"] = process
                update(state="running", pid=process.pid)
                while not _native_exited(process):
                    check()
                    control["cancel"].wait(0.05)
                check()
                returncode = _finish_owned(control)
                update(returncode=returncode, state="completed" if returncode == 0 else "failed",
                       error="" if returncode == 0 else f"{status.spec.name} 종료 코드: {returncode}")
            if run.state == "completed":
                update(parse_state="reading")
                try:
                    result = adapter.collect_results(run.workdir, check_cancelled=check)
                    check()
                    snapshot["outputs"] = {name: file_revision(run.workdir / name) for name in adapter.expected_output_names()
                                           if (run.workdir / name).is_file()}
                    update(result=result, parse_state=result["status"], snapshot=snapshot)
                except _NativeCancelled:
                    raise
                except TimeoutError:
                    raise
                except Exception as exc:
                    update(parse_state="failed", result={"status": "failed", "frames": [], "exchange_files": [],
                                                          "warnings": [str(exc)], "summary": {}},
                           error=f"결과 판독 실패: {type(exc).__name__}: {exc}")
        except Exception as exc:
            cleanup_error = ""
            try:
                if control["process"] is not None:
                    run.returncode = _finish_owned(control, cancel=True)
            except Exception as cleanup:
                control["cleanup_failed"] = True
                cleanup_error = f" 소유 프로세스 종료를 확인하지 못했습니다: {cleanup}"
            with self._native_lock:
                run.state = "cancelled" if isinstance(exc, _NativeCancelled) and not cleanup_error else "failed"
                run.error = f"{type(exc).__name__}: {exc}{cleanup_error}"
                if run.parse_state == "reading":
                    run.parse_state = "failed"
            try:
                with Path(run.log_path).open("a", encoding="utf-8") as log:
                    log.write("\n" + traceback.format_exc() + cleanup_error)
            except OSError:
                pass
        finally:
            # Hash immutable, closed outputs on the worker, outside the UI snapshot
            # lock. Old receipts without these hashes remain explicitly unverified.
            try:
                from .project import file_revision
                artifacts = {}
                paths = [run.workdir / name for name in ('model.frd', 'model.dat', 'job.log')]
                paths.extend((run.workdir / 'results').glob('*.pvd'))
                paths.extend((run.workdir / 'results').glob('*.vtu'))
                for path in paths:
                    if path.is_file():
                        if not path.resolve().is_relative_to(run.workdir.resolve()):
                            raise ValueError(f'실행 출력 경로가 실행 폴더 밖을 가리킵니다: {path}')
                        artifacts[path.relative_to(run.workdir).as_posix()] = file_revision(path)
                with self._native_lock:
                    run.snapshot['artifacts'] = artifacts
            except Exception as exc:
                with self._native_lock:
                    run.state = 'failed'
                    run.error += f' 실행 출력 해시 기록 실패: {exc}'
            with self._native_lock:
                run.finished_at = utc_now()
                try:
                    store.write(run)
                except Exception as exc:
                    run.state = "failed"
                    run.error += f" 실행 기록 저장 실패: {exc}"
                    try:
                        store.write(run)
                    except Exception:
                        pass
            lease.release()

    def cancel_native(self, run_id: str) -> bool:
        with self._native_lock:
            control, run = self._controls.get(run_id), self._native.get(run_id)
            if control is None or run is None or (run.state in TERMINAL_STATES and run.parse_state != "reading"):
                return False
            control["cancel"].set()
            return True

    def close(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._native_lock:
            self._closed = True
            for run_id, control in self._controls.items():
                if self._native[run_id].state not in TERMINAL_STATES or self._native[run_id].parse_state == "reading":
                    control["cancel"].set()
            threads = [control["thread"] for control in self._controls.values() if control["thread"] is not None]
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        return not any(thread.is_alive() for thread in threads) and not any(c["cleanup_failed"] for c in self._controls.values())

    def launch(self, request: LaunchRequest) -> LaunchRecord:
        """Retain this GUI process; creation never means calculation success."""
        record = LaunchRecord(next(self._ids), request)
        self.launches[record.id] = record
        try:
            record.process = subprocess.Popen(
                request.argv, cwd=request.cwd, env=request.env, shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as exc:
            record.state, record.error = "failed", f"{type(exc).__name__}: {exc}"
        return record

    def submit(
        self,
        converter: SolverConverter,
        model: StructuralModel,
        workdir: str | Path,
    ) -> Job:
        job = Job(id=next(self._ids), solver_id=converter.id, workdir=Path(workdir))
        self.jobs[job.id] = job
        job._set("preparing")
        try:
            job.workdir.mkdir(parents=True, exist_ok=True)
            issues = validate_model(model) + converter.validate(model)
            errors = [str(issue) for issue in issues if issue.severity == "error"]
            if errors:
                job.result = ResultSet(
                    solver_id=converter.id, success=False,
                    log_tail="\n".join(errors)[-4000:], validation_issues=errors,
                )
                (job.workdir / "job.log").write_text("\n".join(errors), encoding="utf-8")
                job.result.log_path = str(job.workdir / "job.log")
                job._set("failed")
                return job
            deck = converter.write_input(model, job.workdir)
            job._set("running")
            job.result = converter.run(model, deck, job.workdir)
            if job.result.log_path is None:
                log_path = job.workdir / "job.log"
                log_path.write_text(job.result.log_tail, encoding="utf-8")
                job.result.log_path = str(log_path)
            job._set("succeeded" if job.result.success else "failed")
        except Exception as exc:  # preparation, execution and reading all terminate
            detail = traceback.format_exc()
            log_path = job.workdir / "job.log"
            try:
                with log_path.open("a", encoding="utf-8") as log:
                    log.write("\n" + detail)
                saved_path = str(log_path)
            except OSError:
                saved_path = None
            job.result = ResultSet(
                solver_id=converter.id, success=False,
                log_tail=f"{type(exc).__name__}: {exc}\n{detail}"[-4000:],
                log_path=saved_path,
            )
            job._set("failed")
        return job

    def get(self, job_id: int) -> Job:
        return self.jobs[job_id]
