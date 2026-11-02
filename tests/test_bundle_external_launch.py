"""Exercise the frozen launch wiring with a source gate and console probes."""

import json
import os
from pathlib import Path
import sys
import subprocess
import threading
import time

import pytest

import caelab.jobs as jobs
from caelab import process_entry
from caelab.connections import LaunchRequest


@pytest.fixture
def bundled_launch(monkeypatch):
    if os.name != 'nt':
        pytest.skip('Windows Job ownership')
    gate_script = Path(process_entry.__file__).parent / 'native/windows_gate.py'

    def source_gate(gate, argv, log, acknowledgement=None):
        args = [str(gate) if gate is not None else '-', json.dumps(argv),
                str(log) if log is not None else '-']
        if acknowledgement is not None:
            args.append(str(acknowledgement))
        return [sys.executable, str(gate_script), *args]

    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(process_entry, 'windows_gate_command', source_gate)
    monkeypatch.setattr(jobs, 'windows_gate_command', source_gate)


def test_bundled_external_start_failure_is_failed_with_original_reason(tmp_path, bundled_launch):
    manager = jobs.JobManager()
    record = manager.launch(LaunchRequest([str(tmp_path / 'missing.exe')], tmp_path,
                                        dict(os.environ), 'probe'))
    try:
        assert record.state == 'failed'
        assert 'FileNotFoundError' in record.error
        assert record.error.startswith('FileNotFoundError: ')
        assert record.process is None and record.pid is None
        assert manager.launches[record.id] is record
    finally:
        if record.process is not None:
            record.process.wait(timeout=5)


def test_successful_handoff_survives_launcher_parent_exit(tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows Job ownership')
    root = str(Path(jobs.__file__).resolve().parent.parent)
    release = tmp_path / 'finish'
    finished = tmp_path / 'finished'
    child = (
        "import time; from pathlib import Path; "
        f"release=Path({str(release)!r}); deadline=time.monotonic()+8; "
        "exec('while not release.exists() and time.monotonic()<deadline:\\n time.sleep(.01)'); "
        f"Path({str(finished)!r}).write_text('finished')"
    )
    driver = '''
import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import caelab.jobs as jobs
from caelab.connections import LaunchRequest
gate_script=Path(jobs.__file__).parent/'native/windows_gate.py'
def source_gate(gate,argv,log,acknowledgement=None):
    return [sys.executable,str(gate_script),str(gate),json.dumps(argv),'-',str(acknowledgement)]
jobs.windows_gate_command=source_gate
sys.frozen=True
manager=jobs.JobManager()
record=manager.launch(LaunchRequest([sys.executable,'-c',sys.argv[3]],Path(sys.argv[2]),dict(os.environ),'probe'))
assert record.state=='opened',record.error
assert manager.close()
'''
    try:
        parent = subprocess.run([sys.executable, '-c', driver, root, str(tmp_path), child],
                                capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        assert parent.returncode == 0, parent.stderr.decode(errors='replace')
        assert not finished.exists()
        release.touch()
        deadline = time.monotonic() + 3
        while not finished.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert finished.read_text() == 'finished'
    finally:
        release.touch()


def test_lost_acknowledgement_reaps_helper_and_already_started_child(tmp_path, bundled_launch, monkeypatch):
    import ctypes as c
    from ctypes import wintypes as w

    api = c.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes, api.OpenProcess.restype = [w.DWORD, w.BOOL, w.DWORD], w.HANDLE
    api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    api.CloseHandle.argtypes = [w.HANDLE]
    pid_file = tmp_path / 'child.pid'
    handles = []
    slow_gate = tmp_path / 'lost_ack.py'
    slow_gate.write_text('''
import os,subprocess,sys,time
from pathlib import Path
release=Path(sys.argv[1])
while not release.exists(): time.sleep(.01)
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
Path(sys.argv[2]).write_text(str(child.pid))
time.sleep(60)
''', encoding='utf-8')

    def command(release, argv, log, acknowledgement):
        return [sys.executable, str(slow_gate), str(release), str(pid_file)]

    original_group = jobs._WindowsRunGroup

    class CaptureGroup(original_group):
        def __init__(self, process):
            super().__init__(process)
            handles.append(api.OpenProcess(0x100000, False, process.pid))

    def watch_child():
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if pid_file.exists():
                text = pid_file.read_text()
                if text:
                    handles.append(api.OpenProcess(0x100000, False, int(text)))
                    return
            time.sleep(.005)

    monkeypatch.setattr(jobs, '_WindowsRunGroup', CaptureGroup)
    monkeypatch.setattr(jobs, 'windows_gate_command', command)
    watcher = threading.Thread(target=watch_child)
    watcher.start()
    try:
        request = LaunchRequest(['unused'], tmp_path, dict(os.environ), 'probe')
        with pytest.raises(TimeoutError, match='acknowledgement timed out'):
            jobs._launch_bundled_external(request, timeout=1)
        watcher.join(timeout=3)
        assert not watcher.is_alive() and len(handles) == 2 and all(handles)
        assert all(api.WaitForSingleObject(handle, 3000) == 0 for handle in handles)
    finally:
        watcher.join(timeout=3)
        for handle in handles:
            if handle:
                api.CloseHandle(handle)
