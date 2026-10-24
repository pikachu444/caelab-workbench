"""Prompt-only transport using the operator's existing signed-in OpenScience.

The invocation/overlay follows the original Lab's openscience_host adapter.
Credentials stay in their existing profile; no model tools or CAE MCP are exposed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time

IS_WINDOWS = sys.platform == "win32"
VERSION = "2.0.146"
MODEL = re.compile(r"openai-codex/[A-Za-z0-9._-]+\Z")


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


class SubscriptionTransport:
    def __init__(self):
        self.cancelled = threading.Event()
        self.timeout_seconds = 55
        self.model = ""
        self.profile = self.runtime = None
        self.configuration_error = ""
        try:
            local = os.environ.get("LOCALAPPDATA")
            if not local:
                raise ValueError("기존 Windows OpenScience 설정을 찾을 수 없습니다.")
            settings = Path(local) / "AutonomousCAELab" / "workbench-config.json"
            configured = _json(settings).get("runtime", {}) if settings.is_file() else {}
            profile = os.environ.get("CAELAB_OPENSCIENCE_PROFILE") or configured.get("openscience_profile")
            runtime = os.environ.get("CAELAB_OPENSCIENCE_RUNTIME_PREFIX") or configured.get("openscience_runtime_prefix")
            self.model = os.environ.get("CAELAB_EXPERT_MODEL") or configured.get("model", "")
            if not profile or not runtime or not MODEL.fullmatch(self.model):
                raise ValueError("기존 OpenScience 로그인 프로필·설치 경로와 openai-codex 모델을 선택하세요.")
            self.profile, self.runtime = Path(profile).resolve(), Path(runtime).resolve()
        except (OSError, ValueError, TypeError) as error:
            self.configuration_error = str(error)

    def _installation(self, *, verify_binary=False):
        if not IS_WINDOWS:
            raise ValueError("기존 OpenScience 구독 연결은 Windows 호스트에서 사용하세요.")
        if self.configuration_error:
            raise ValueError(self.configuration_error)
        marker = _json(self.profile / "caelab-profile-owner.json")
        binary = self.runtime / "node_modules/@synsci/openscience-windows-x64/bin/openscience.exe"
        if (marker.get("kind") != "autonomous-cae-lab.chatgpt-auth-profile"
                or marker.get("provider") != "openai-codex"
                or marker.get("runtime_version") != VERSION
                or Path(marker.get("profile_root", "")).resolve() != self.profile):
            raise ValueError("기존 OpenScience 프로필의 소유·버전 정보가 일치하지 않습니다.")
        pins = [p for p in marker.get("runtime_pin", {}).get("NativeBinaries", [])
                if Path(p.get("Path", "")).resolve() == binary]
        if len(pins) != 1 or not binary.is_file():
            raise ValueError("기존 OpenScience 실행 파일의 고정 정보를 확인할 수 없습니다.")
        if verify_binary:
            with binary.open("rb") as stream:
                actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual_hash.lower() != str(pins[0].get("Sha256", "")).lower():
                raise ValueError("기존 OpenScience 실행 파일이 변경되어 전송하지 않았습니다.")
        config = _json(self.profile / "config/openscience.json")
        if config.get("enabled_providers") != ["openai-codex"] or config.get("permission", {}).get("*") != "deny":
            raise ValueError("기존 OpenScience 구독 공급자·도구 거부 설정을 확인하세요.")
        return binary, config

    def availability(self):
        try:
            self._installation()
            return True, f"기존 구독 설정: {self.model}. 로그인·답변 성공은 질문 시 확인합니다."
        except (OSError, ValueError, TypeError, KeyError) as error:
            return False, str(error)

    def cancel(self):
        # Nonblocking and idempotent; the request worker owns process cleanup.
        self.cancelled.set()

    def _checkpoint(self, deadline):
        if self.cancelled.is_set():
            raise RuntimeError("OpenScience 요청 대기를 중단했습니다.")
        if time.monotonic() >= deadline:
            raise TimeoutError("OpenScience 요청 제한 시간이 지났습니다.")

    def _stop(self, process):
        # This Popen retains the Windows process handle, so its PID cannot be
        # reused while terminating only this invocation's native child tree.
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            process.wait(timeout=5)
            process.communicate(timeout=2)  # Join Windows stdout/stderr readers.
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("OpenScience 소유 프로세스 정리를 확인하지 못했습니다.") from error

    def discuss(self, prompt):
        deadline = time.monotonic() + self.timeout_seconds
        self._checkpoint(deadline)
        binary, original = self._installation(verify_binary=True)
        temp_root = self.profile / "temp"
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="caelab-expert-", dir=temp_root) as temporary:
            config_dir = Path(temporary) / "config"
            config_dir.mkdir()
            config = copy.deepcopy(original)
            config.update(permission={"*": "deny"}, tools={"*": False}, mcp={}, plugin=[],
                          default_agent="research", sandbox={"enabled": False},
                          agent={"research": {"permission": {"*": "deny"}, "steps": 1}},
                          harness={key: False for key in ("deliverables", "acceptance", "unattended",
                                   "review", "budget", "redirect", "durable-jobs")})
            (config_dir / "openscience.json").write_text(json.dumps(config), encoding="utf-8")
            env = dict(os.environ)
            env.update(OPENSCIENCE_TEST_HOME=str(self.profile / "home"), OPENSCIENCE_CONFIG_DIR=str(config_dir),
                       OPENSCIENCE_DATA_DIR=str(self.profile / "data"),
                       XDG_CONFIG_HOME=str(self.profile / "xdg-config"), XDG_DATA_HOME=str(self.profile / "xdg-data"),
                       XDG_CACHE_HOME=str(self.profile / "xdg-cache"), XDG_STATE_HOME=str(self.profile / "xdg-state"),
                       TEMP=str(temp_root), TMP=str(temp_root), OPENSCIENCE_DISABLE_AUTOUPDATE="1",
                       OPENSCIENCE_DISABLE_MODELS_FETCH="1", OPENSCIENCE_DISABLE_PROJECT_CONFIG="1",
                       OPENSCIENCE_DISABLE_LSP_DOWNLOAD="1")
            command = [str(binary), "run", "--format", "json", "--workspace", "isolated", "--delegation", "off",
                       "--deny-prompts", "--model", self.model, "--variant", "low",
                       "--deadline", str(max(1, int(deadline - time.monotonic()))), "--"]
            # Windows communicate(input=...) writes to a pipe synchronously,
            # before its timeout polling. A child that never reads would block
            # cancellation. File stdin needs no input worker or pipe write.
            prompt_path = Path(temporary) / "prompt.txt"
            with prompt_path.open("wb") as stream:
                for offset in range(0, len(prompt), 65536):
                    self._checkpoint(deadline)
                    stream.write(prompt[offset:offset + 65536].encode("utf-8"))
            self._checkpoint(deadline)
            with prompt_path.open("rb") as standard_input:
                process = subprocess.Popen(command, cwd=self.profile, env=env, stdin=standard_input,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                while True:
                    self._checkpoint(deadline)
                    try:
                        stdout, _stderr = process.communicate(timeout=.05)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                self._checkpoint(deadline)
                events = []
                for line in stdout.splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict):
                        events.append(event)
                done = [e for e in events if e.get("type") == "done"]
                texts = [e.get("part", {}).get("text") for e in events if e.get("type") == "text"]
                if (process.returncode != 0 or not done or done[-1].get("status") != "completed"
                        or not texts or not isinstance(texts[-1], str) or not texts[-1].strip()):
                    # Raw host diagnostics may contain account details. Keep
                    # those in the host, never put stderr/auth in the document.
                    status = done[-1].get("status") if done else "완료 이벤트 없음"
                    raise RuntimeError(f"OpenScience 답변을 완료하지 못했습니다 ({status}, exit={process.returncode}).")
                return texts[-1]
            finally:
                self._stop(process)
