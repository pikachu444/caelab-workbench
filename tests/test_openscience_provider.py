"""Transport policy/process checks only: no model, network or real credentials."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from caelab.assist import openscience as transport
from caelab.assist.experts import OpenScienceProvider


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "IS_WINDOWS", True)
    for key in ("CAELAB_EXPERT_MODEL", "CAELAB_OPENSCIENCE_PROFILE", "CAELAB_OPENSCIENCE_RUNTIME_PREFIX"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    base = tmp_path / "AutonomousCAELab"
    profile, runtime = base / "existing-profile", base / "existing-runtime"
    binary = runtime / "node_modules/@synsci/openscience-windows-x64/bin/openscience.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fake native binary; never executed")
    (profile / "config").mkdir(parents=True)
    config = {"enabled_providers": ["openai-codex"], "permission": {"*": "deny"}}
    (profile / "config/openscience.json").write_text(json.dumps(config))
    marker = {"kind": "autonomous-cae-lab.chatgpt-auth-profile", "provider": "openai-codex",
              "profile_root": str(profile), "runtime_version": "2.0.146", "runtime_pin": {
                  "NativeBinaries": [{"Path": str(binary), "Sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}]}}
    (profile / "caelab-profile-owner.json").write_text(json.dumps(marker))
    (base / "workbench-config.json").write_text(json.dumps({"runtime": {
        "openscience_profile": str(profile), "openscience_runtime_prefix": str(runtime),
        "model": "openai-codex/existing-model"}}))
    return profile, binary


class NativeProcess:
    pid = 12345
    returncode = None

    def __init__(self, output, *, blocked=False):
        self.output = output
        self.blocked = blocked
        self.input = None
        self.entered = threading.Event()

    def communicate(self, *, input=None, timeout=None):
        self.input = input or self.input
        self.entered.set()
        if self.blocked and self.returncode is None:
            time.sleep(.002)
            raise subprocess.TimeoutExpired("owned fake", timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.output, "private account diagnostic must not escape"

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        assert self.returncode is not None
        return self.returncode


def test_subscription_prompt_only_preserves_profile_and_only_returns_completed_final_text(configured, monkeypatch):
    profile, binary = configured
    original = (profile / "config/openscience.json").read_bytes()
    events = [{"type": "text", "part": {"text": "intermediate"}},
              {"type": "text", "part": {"text": "실제 공급자 대신 모의 응답"}},
              {"type": "done", "status": "completed"}]
    child = NativeProcess("\n".join(json.dumps(e, ensure_ascii=False) for e in events))
    calls = []
    def launch(command, **kwargs):
        calls.append(command)
        assert command[0] == str(binary) and "--deny-prompts" in command
        assert command[command.index("--model") + 1] == "openai-codex/existing-model"
        assert "민감 질문" not in " ".join(command)
        config = json.loads((Path(kwargs["env"]["OPENSCIENCE_CONFIG_DIR"]) / "openscience.json").read_text())
        assert config["permission"] == {"*": "deny"} and config["tools"] == {"*": False}
        assert config["mcp"] == {} and config["plugin"] == []
        assert config["agent"]["research"]["steps"] == 1
        assert not any(config["harness"].values())
        assert kwargs["env"]["OPENSCIENCE_DATA_DIR"] == str(profile / "data")
        assert kwargs["cwd"] == profile
        assert kwargs["stdin"].read() == "민감 질문".encode("utf-8")
        return child
    monkeypatch.setattr(transport.subprocess, "Popen", launch)
    provider = OpenScienceProvider()
    assert provider.availability()[0]
    assert "질문 시" in provider.availability()[1] and calls == []
    assert provider.discuss("민감 질문") == "실제 공급자 대신 모의 응답"
    assert child.input is None and len(calls) == 1
    assert (profile / "config/openscience.json").read_bytes() == original
    assert list((profile / "temp").iterdir()) == []


@pytest.mark.parametrize("failure", ["binary", "api_model", "policy"])
def test_changed_binary_paid_route_or_tool_policy_never_launch(configured, monkeypatch, failure):
    profile, binary = configured
    if failure == "binary":
        binary.write_bytes(b"changed")
    elif failure == "api_model":
        monkeypatch.setenv("CAELAB_EXPERT_MODEL", "openai/paid-api-model")
    else:
        (profile / "config/openscience.json").write_text(json.dumps({"enabled_providers": ["openai-codex"], "permission": {"*": "allow"}}))
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(ValueError):
        OpenScienceProvider().discuss("question")


def test_cancel_before_start_never_launches(configured, monkeypatch):
    provider = OpenScienceProvider()
    provider.cancel()
    provider.cancel()
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *a, **k: pytest.fail("cancelled before start"))
    with pytest.raises(RuntimeError, match="중단"):
        provider.discuss("question")


def test_cancel_while_preparing_long_input_never_launches_and_removes_file(configured, monkeypatch):
    profile, _ = configured
    provider = OpenScienceProvider()
    original = provider.transport._checkpoint
    checks = []
    def checkpoint(deadline):
        checks.append(None)
        if len(checks) == 3:  # First input chunk written; next chunk not yet written.
            provider.cancel()
        original(deadline)
    monkeypatch.setattr(provider.transport, "_checkpoint", checkpoint)
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *a, **k: pytest.fail("input cancelled"))
    with pytest.raises(RuntimeError, match="중단"):
        provider.discuss("x" * (1024 * 1024))
    assert list((profile / "temp").iterdir()) == []


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_stop_cleans_only_owned_native_tree_and_never_returns_late_text(configured, monkeypatch, stop):
    child = NativeProcess("late private text", blocked=True)
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *a, **k: child)
    killed = []
    def kill(command, **kwargs):
        killed.append(command)
        child.returncode = -9
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(transport.subprocess, "run", kill)
    provider = OpenScienceProvider()
    provider.transport.timeout_seconds = .03 if stop == "timeout" else 2
    errors = []
    def request():
        try:
            provider.discuss("question")
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=request)
    thread.start()
    assert child.entered.wait(1)
    if stop == "cancel":
        provider.cancel()
        provider.cancel()
    thread.join(2)
    assert not thread.is_alive() and len(errors) == 1
    assert isinstance(errors[0], TimeoutError if stop == "timeout" else RuntimeError)
    assert killed == [["taskkill", "/PID", "12345", "/T", "/F"]]


def test_incomplete_events_never_become_an_answer_or_leak_stderr(configured, monkeypatch):
    child = NativeProcess('{"type":"text","part":{"text":"unfinished"}}')
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *a, **k: child)
    with pytest.raises(RuntimeError) as error:
        OpenScienceProvider().discuss("question")
    assert "private" not in str(error.value) and "완료 이벤트 없음" in str(error.value)


@pytest.mark.skipif(sys.platform != "win32", reason="Actual Windows owned process-tree termination")
def test_windows_stop_terminates_only_this_native_child_tree():
    import csv
    import io
    # Real stdlib subprocesses, not OpenScience/model/network calls.
    code = ("import subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']); "
            "print(child.pid,flush=True);time.sleep(30)")
    process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        child_pid = int(process.stdout.readline())
        transport.SubscriptionTransport()._stop(process)
        assert process.poll() is not None
        listing = subprocess.run(["tasklist", "/FI", f"PID eq {child_pid}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=5,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        assert not any(len(row) > 1 and row[1] == str(child_pid)
                       for row in csv.reader(io.StringIO(listing.stdout)))
    finally:
        if process.poll() is None:
            transport.SubscriptionTransport()._stop(process)


def test_real_child_receives_complete_utf8_file_stdin_and_eof(configured, monkeypatch):
    _, binary = configured
    actual_popen = subprocess.Popen
    code = ("import sys,json,hashlib;data=sys.stdin.buffer.read();"
            "print(json.dumps({'type':'text','part':{'text':hashlib.sha256(data).hexdigest()}}));"
            "print(json.dumps({'type':'done','status':'completed'}))")
    def launch(command, **kwargs):
        assert command[0] == str(binary)
        return actual_popen([sys.executable, "-c", code], **kwargs)
    monkeypatch.setattr(transport.subprocess, "Popen", launch)
    prompt = "긴 근거\r\n다음 줄\n" * 20000
    assert OpenScienceProvider().discuss(prompt) == hashlib.sha256(prompt.encode("utf-8")).hexdigest()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows communicate pipe-write cancellation regression")
@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_large_prompt_stops_real_windows_child_that_never_reads_stdin(configured, monkeypatch, stop):
    profile, binary = configured
    actual_popen = subprocess.Popen
    children, input_paths = [], []
    created = threading.Event()
    def launch(command, **kwargs):
        if command[0] == str(binary):
            input_paths.append(Path(kwargs["stdin"].name))
            command = [sys.executable, "-c", "import time;time.sleep(30)"]
            child = actual_popen(command, **kwargs)
            children.append(child)
            created.set()
            return child
        return actual_popen(command, **kwargs)  # Owned taskkill only.
    monkeypatch.setattr(transport.subprocess, "Popen", launch)
    provider = OpenScienceProvider()
    provider.transport.timeout_seconds = .2 if stop == "timeout" else 3
    errors = []
    def invoke():
        try:
            provider.discuss("x" * (8 * 1024 * 1024))
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=invoke)
    began = time.monotonic()
    worker.start()
    try:
        assert created.wait(2)
        if stop == "cancel":
            provider.cancel()
        worker.join(2)
        assert not worker.is_alive(), "Long stdin must not block the deadline/cancel poll"
        assert len(errors) == 1 and isinstance(errors[0], TimeoutError if stop == "timeout" else RuntimeError)
        assert children[0].poll() is not None
        assert not children[0].stdout_thread.is_alive() and not children[0].stderr_thread.is_alive()
        assert all(not p.exists() for p in input_paths)
        assert list((profile / "temp").iterdir()) == []
        assert time.monotonic() - began < 2.5
    finally:
        if children and children[0].poll() is None:
            provider.transport._stop(children[0])
        worker.join(3)
