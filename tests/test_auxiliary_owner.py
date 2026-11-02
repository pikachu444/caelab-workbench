"""The shared owner preserves adapter input and waits for real lease cleanup."""

import os
import sys
import threading
import time

import pytest

from caelab.connections import LaunchRequest, PROGRAMS, ProgramStatus
from caelab.native.input_snapshot import snapshot_input
from caelab.native.run_store import RunStore, atomic_json
from caelab.project import file_revision
from caelab.workbench import Workbench


class PersistingProbe:
    def __init__(self):
        self.entered = None
        self.release = None

    def persist_input(self, workdir):
        path = workdir / 'model-snapshot.json'
        atomic_json(path, {'name': 'frozen model'})
        if self.entered is not None:
            self.entered.set()
            assert self.release.wait(3)
        return {'auxiliary_model': {'path': path.name, 'sha256': file_revision(path)}}

    def snapshot_input(self, source, workdir, check):
        return snapshot_input(source, workdir, check)

    def build_run_request(self, status, workdir, input_path):
        script = "from pathlib import Path; Path('model.frd').write_text('probe, not solver data')"
        return LaunchRequest([sys.executable, '-c', script], workdir, dict(os.environ), 'calculix')

    def collect_results(self, workdir, *, check_cancelled):
        check_cancelled()
        return {'status': 'readable', 'frames': [], 'exchange_files': [], 'warnings': [], 'summary': {}}

    def artifact_paths(self, workdir, snapshot):
        return [workdir / name for name in ('model-snapshot.json', 'model.frd', 'job.log')]

    def expected_output_names(self):
        return ('model.frd',)


@pytest.fixture
def owned(tmp_path, monkeypatch):
    import caelab.native.adapter as registry
    probe = PersistingProbe()
    monkeypatch.setattr(registry, 'get_solver_adapter', lambda _: probe)
    wb = Workbench()
    project = wb.new_native_project()
    node = wb.add_program_node(project, 'analysis', 'calculix')
    source = tmp_path / 'input.inp'
    source.write_text('*HEADING\nowner probe\n')
    wb.set_node_file(project, node.id, source)
    wb.save_native_project(project, tmp_path / 'project.json')
    spec = next(item for item in PROGRAMS if item.id == 'calculix')
    monkeypatch.setattr(wb, 'programs', lambda: [ProgramStatus(spec, sys.executable, source='console probe')])
    yield wb, project, node, probe
    if probe.release is not None:
        probe.release.set()
    wb.close()


def settled(wb, run_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        activity = wb.native_run_activity(run_id)
        if activity is not None and not activity.active:
            return activity
        time.sleep(.01)
    pytest.fail('owned worker did not settle')


def test_adapter_model_metadata_survives_native_snapshot_and_output_collection(owned):
    wb, project, node, _ = owned
    submitted = wb.submit_native(project, node.id)
    assert settled(wb, submitted.id).cleanup_error == ''
    run = wb.jobs.native_snapshot(submitted.id)
    assert run.state == 'completed' and run.parse_state == 'readable'
    metadata = run.snapshot['auxiliary_model']
    assert metadata['sha256'] == file_revision(run.workdir / metadata['path'])
    assert run.snapshot['files'] and run.snapshot['execution']
    assert run.snapshot['artifacts']['model-snapshot.json'] == metadata['sha256']
    assert wb.native_run_activity('not-owned') is None
    assert wb.close()


def test_queued_cancel_preserves_model_and_finished_receipt_does_not_release_close_early(owned, monkeypatch):
    wb, project, node, probe = owned
    probe.entered, probe.release = threading.Event(), threading.Event()
    releasing, allow_release = threading.Event(), threading.Event()
    original_lease = RunStore.lease

    class DeferredLease:
        def __init__(self, inner): self.inner = inner
        @property
        def is_locked(self): return self.inner.is_locked
        def release(self):
            releasing.set()
            assert allow_release.wait(3)
            self.inner.release()

    monkeypatch.setattr(RunStore, 'lease', lambda store: DeferredLease(original_lease(store)))
    try:
        submitted = wb.submit_native(project, node.id)
        assert probe.entered.wait(2)
        assert wb.cancel_native_run(submitted.id)
        probe.release.set()
        assert releasing.wait(2)
        run = wb.jobs.native_snapshot(submitted.id)
        assert run.state == 'cancelled' and run.finished_at
        activity = wb.native_run_activity(submitted.id)
        assert activity.active and not activity.cleanup_error
        assert run.snapshot['artifacts']['model-snapshot.json'] == file_revision(run.workdir / 'model-snapshot.json')
        allow_release.set()
        activity = settled(wb, submitted.id)
        assert not activity.active and not activity.cleanup_error
    finally:
        probe.release.set()
        allow_release.set()


def test_failed_lease_cleanup_is_terminal_with_reason_not_a_settled_success(owned, monkeypatch):
    wb, project, node, _ = owned
    original_lease = RunStore.lease
    inner_leases = []

    class FailedLease:
        def __init__(self, inner):
            self.inner = inner
            inner_leases.append(inner)
        @property
        def is_locked(self): return self.inner.is_locked
        def release(self): raise OSError('injected lease release failure')

    monkeypatch.setattr(RunStore, 'lease', lambda store: FailedLease(original_lease(store)))
    try:
        submitted = wb.submit_native(project, node.id)
        activity = settled(wb, submitted.id)
        assert 'injected lease release failure' in activity.cleanup_error
        run = wb.jobs.native_snapshot(submitted.id)
        assert run.state == 'failed' and run.finished_at
    finally:
        for lease in inner_leases:
            lease.release()


def test_worker_start_and_release_failure_still_returns_owned_failed_receipt(owned, monkeypatch):
    wb, project, node, _ = owned
    original_lease = RunStore.lease
    inner_leases, releases = [], []

    class FailedLease:
        def __init__(self, inner):
            self.inner = inner
            inner_leases.append(inner)
        @property
        def is_locked(self): return self.inner.is_locked
        def release(self):
            releases.append(None)
            raise OSError('injected queue lease failure')

    def fail_start(self):
        raise RuntimeError('injected worker start failure')

    monkeypatch.setattr(RunStore, 'lease', lambda store: FailedLease(original_lease(store)))
    monkeypatch.setattr(threading.Thread, 'start', fail_start)
    try:
        submitted = wb.submit_native(project, node.id)
        assert submitted.state == 'failed' and submitted.finished_at
        assert len(releases) == 1
        activity = wb.native_run_activity(submitted.id)
        assert not activity.active and 'injected queue lease failure' in activity.cleanup_error
        stored = RunStore(submitted.workdir.parent).read(submitted.workdir / 'job.json')
        assert 'injected worker start failure' in stored.error and 'injected queue lease failure' in stored.error
        assert not wb.close()
    finally:
        for lease in inner_leases:
            lease.release()
