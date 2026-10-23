"""Saved discussions use mock providers; these checks make no LLM/network calls."""
import copy
import hashlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from filelock import FileLock

from caelab.assist import discussions as service
from caelab.assist import experts
from caelab.assist.knowledge import KnowledgeEntry, KnowledgeSearchResult
from caelab.native.run_store import atomic_json


class Provider(experts.ExpertProvider):
    id = "mock"
    name = "Mock provider"

    def __init__(self, reply="확인할 값이 없으면 미확인으로 남깁니다."):
        self.reply = reply
        self.prompts = []

    def availability(self):
        return True, "mock"

    def discuss(self, prompt):
        self.prompts.append(prompt)
        return self.reply


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(service, "verify_evidence", lambda evidence, asset_root: [])
    monkeypatch.setattr(experts, "search_knowledge_result",
                        lambda *args, **kwargs: KnowledgeSearchResult("no_material", [], kwargs.get("solver", "")))


def evidence(identifier="A", **overrides):
    text = f"input-{identifier}"
    value = {"id": identifier, "source_kind": "native_run", "created_at": "2026-10-24T00:00:00Z",
             "solver_id": "calculix", "run_id": f"run-{identifier}", "node_id": "solver-node",
             "input_revision": f"revision-{identifier}", "input_manifest": [], "units": {}, "unit_source": "unknown",
             "process_state": "failed", "parse_state": "missing", "frame_index": None,
             "summary_text": f"근거 {identifier}: 출처 run-{identifier}, 단위 미확인, 결과 없음",
             "model_text": f"MODEL_SECRET_{identifier}", "log_text": f"LOG_SECRET_{identifier}: failed convergence",
             "files": [{"role": "input", "source_path": f"/original/{identifier}.inp", "stored_path": f"{identifier}/model.inp",
                        "sha256": hashlib.sha256(text.encode()).hexdigest(), "size": len(text)}], "warnings": []}
    value.update(overrides)
    return value


def prepared(tmp_path, monkeypatch, provider=None):
    path = tmp_path / "conversation.json"
    doc = service.create_discussion(path)
    doc = service.attach_evidence(path, evidence(), expected_revision=doc["revision"])
    provider = provider or Provider()
    monkeypatch.setattr(experts, "get_provider", lambda provider_id=None: provider)
    return path, doc, provider


def ask(path, doc, question="질문", **kwargs):
    return service.ask_discussion(path, question, evidence_id=kwargs.pop("evidence_id", "A"),
                                  template_id=kwargs.pop("template_id", "interpretation"),
                                  expected_revision=doc["revision"], **kwargs)


def test_open_save_attach_and_new_document_never_call_provider(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(experts, "get_provider", lambda *args: calls.append(args))
    path = tmp_path / "conversation.json"
    doc = service.create_discussion(path)
    doc["constraints"] = ["원본 덱 보존"]
    doc["provider_id"] = "openscience"
    doc = service.save_discussion(path, doc, expected_revision=0)
    doc = service.attach_evidence(path, evidence(), expected_revision=doc["revision"])
    assert service.load_discussion(path) == doc
    assert doc["evidences"]["A"]["files"][0]["stored_path"] == "A/model.inp"
    other = service.create_discussion(tmp_path / "other.json")
    assert other["id"] != doc["id"] and other["evidences"] == {} and other["turns"] == []
    assert calls == []


def test_save_conflict_identity_and_immutable_evidence_preserve_file(tmp_path, monkeypatch):
    path, doc, _ = prepared(tmp_path, monkeypatch)
    old = copy.deepcopy(doc)
    doc["title"] = "첫 창"
    doc = service.save_discussion(path, doc, expected_revision=doc["revision"])
    original = path.read_bytes()
    with pytest.raises(service.DiscussionConflict):
        service.save_discussion(path, old, expected_revision=old["revision"])
    other = {**doc, "id": "other"}
    with pytest.raises(service.DiscussionConflict):
        service.save_discussion(path, other, expected_revision=doc["revision"])
    changed = copy.deepcopy(doc)
    changed["evidences"]["A"]["summary_text"] = "replacement"
    with pytest.raises(service.DiscussionConflict):
        service.save_discussion(path, changed, expected_revision=doc["revision"])
    assert path.read_bytes() == original
    with pytest.raises(service.DiscussionConflict):
        service.create_discussion(path)


def test_failed_atomic_write_preserves_saved_document(tmp_path, monkeypatch):
    path, doc, _ = prepared(tmp_path, monkeypatch)
    original = path.read_bytes()
    def broken(*args):
        raise OSError("disk full")
    monkeypatch.setattr(service, "atomic_json", broken)
    with pytest.raises(OSError, match="disk full"):
        service.save_discussion(path, doc, expected_revision=doc["revision"])
    assert path.read_bytes() == original


@pytest.mark.parametrize("stored", ["../outside", "/absolute", "C:\\outside", "A/../../outside"])
def test_asset_paths_stay_relative(tmp_path, stored):
    path = tmp_path / "conversation.json"
    doc = service.create_discussion(path)
    value = evidence()
    value["files"][0]["stored_path"] = stored
    with pytest.raises(service.DiscussionFormatError):
        service.attach_evidence(path, value, expected_revision=doc["revision"])


def test_a_then_b_keeps_constraints_objections_and_source_identity(tmp_path, monkeypatch):
    path, doc, provider = prepared(tmp_path, monkeypatch, Provider("반론 A: 값이 없어 검증할 수 없습니다."))
    doc["constraints"] = ["시험편 형상 변경 금지"]
    doc["failures"] = ["이전 후보는 메모리 부족으로 실패"]
    doc = service.save_discussion(path, doc, expected_revision=doc["revision"])
    doc = ask(path, doc, "A 질문")
    provider.reply = "B도 현재 자료의 한계를 유지합니다."
    doc = service.attach_evidence(path, evidence("B"), expected_revision=doc["revision"])
    doc = ask(path, doc, "B 질문", evidence_id="B")
    assert [turn["evidence_id"] for turn in doc["turns"]] == ["A", "B"]
    assert [turn["run_id"] for turn in doc["turns"]] == ["run-A", "run-B"]
    assert "반론 A" in provider.prompts[-1] and "run-A" in provider.prompts[-1]
    assert "시험편 형상 변경 금지" in provider.prompts[-1] and "메모리 부족" in provider.prompts[-1]
    assert "MODEL_SECRET" not in provider.prompts[-1] and "LOG_SECRET" not in provider.prompts[-1]
    assert service.load_discussion(path) == doc
    material = doc["turns"][-1]["provided_materials"][0]
    assert material["text_field"] == "summary_text" and material["evidence_id"] == "B"


def test_manual_files_need_no_internal_model_or_run(tmp_path, monkeypatch):
    path = tmp_path / "manual-conversation.json"
    doc = service.create_discussion(path)
    value = evidence("manual", source_kind="manual", run_id=None, node_id=None,
                     input_revision=None, model_text="", log_text="", files=[],
                     summary_text="직접 가져온 파일: 단위 미확인, 판독 값 없음")
    doc = service.attach_evidence(path, value, expected_revision=doc["revision"])
    provider = Provider()
    monkeypatch.setattr(experts, "get_provider", lambda *args: provider)
    doc = ask(path, doc, evidence_id="manual")
    assert doc["turns"][0]["status"] == "completed" and doc["turns"][0]["run_id"] is None
    assert "직접 가져온 파일" in provider.prompts[0]


def test_history_cannot_leak_more_privileged_model_or_logs_into_restricted_role(tmp_path, monkeypatch):
    path, doc, provider = prepared(tmp_path, monkeypatch, Provider("SECRET_REPLY_FROM_LOG"))
    doc = ask(path, doc, "LOG_QUESTION", template_id="convergence")
    assert "LOG_SECRET_A" in provider.prompts[0] and "MODEL_SECRET_A" in provider.prompts[0]
    provider.reply = "제한된 요약만 읽었습니다."
    doc = ask(path, doc, "요약만", template_id="interpretation")
    assert "SECRET_REPLY_FROM_LOG" not in provider.prompts[-1]
    assert "LOG_QUESTION" not in provider.prompts[-1]
    assert "LOG_SECRET" not in provider.prompts[-1] and "MODEL_SECRET" not in provider.prompts[-1]
    assert doc["turns"][0]["reply"] == "SECRET_REPLY_FROM_LOG"  # preserved for display


def test_citations_use_only_this_request_knowledge_and_report_search_failure(tmp_path, monkeypatch):
    path, doc, provider = prepared(tmp_path, monkeypatch, Provider("[근거: manual:one]"))
    entry = KnowledgeEntry("one", "topic", (), "manual text", source_id="manual", solver="calculix")
    monkeypatch.setattr(experts, "search_knowledge_result", lambda *args, **kwargs: KnowledgeSearchResult("ok", [entry], "calculix"))
    doc = ask(path, doc, template_id="material")
    assert doc["turns"][-1]["citations"]["unverified"] == []
    monkeypatch.setattr(experts, "search_knowledge_result", lambda *args, **kwargs: KnowledgeSearchResult("error", [], "calculix", "index damaged"))
    doc = ask(path, doc, template_id="material")
    turn = doc["turns"][-1]
    assert turn["knowledge_status"] == "error" and turn["knowledge_error"] == "index damaged"
    assert turn["citations"]["shown_ids"] == [] and turn["citations"]["unverified"]


def test_native_proposals_are_file_based_unapplied_and_attached_to_origin(tmp_path, monkeypatch):
    reply = '```proposals\n' + json.dumps([
        {"target_file": "/original/A.inp", "location": "*STATIC, line 12", "before": "0.1", "after": "0.01", "reason": "증분 축소 검토"},
        {"target_file": "/other.inp", "location": "line 1", "before": 1, "after": 2, "reason": "unknown"},
        {"target_file": {}, "location": "line 1", "before": 1, "after": 2, "reason": "bad type"},
        {"target_path": "loads[0].values.fy", "before": 1, "after": 2},
    ]) + '\n```'
    path, doc, provider = prepared(tmp_path, monkeypatch, Provider(reply))
    doc = ask(path, doc, template_id="convergence")
    assert "loads[0]" not in provider.prompts[0]
    assert len(doc["proposals"]) == 1
    proposal = doc["proposals"][0]
    assert proposal["status"] == "unapplied" and proposal["target_file"] == "/original/A.inp"
    assert proposal["evidence_id"] == "A" and proposal["run_id"] == "run-A" and proposal["input_revision"] == "revision-A"
    assert proposal["request_id"] == doc["turns"][0]["id"]


@pytest.mark.parametrize("kind", ["unavailable", "exception", "empty", "invalid_evidence"])
def test_provider_or_evidence_failure_keeps_question_and_never_saves_success(tmp_path, monkeypatch, kind):
    provider = Provider("")
    if kind == "unavailable":
        provider = experts.UnavailableProvider("not configured")
    elif kind == "exception":
        def broken(prompt):
            raise OSError("connection failed")
        provider.discuss = broken
    path, doc, _ = prepared(tmp_path, monkeypatch, provider)
    if kind == "invalid_evidence":
        monkeypatch.setattr(service, "verify_evidence", lambda *args: ["hash changed"])
    doc = ask(path, doc, "질문 초안")
    turn = doc["turns"][-1]
    assert turn["status"] == ("unavailable" if kind == "unavailable" else "failed")
    assert turn["reply"] == "" and turn["error"] and doc["draft_question"] == "질문 초안"
    assert doc["pending_request"] is None and doc["failures"]


def test_live_request_blocks_changes_but_allows_read_and_cancellation(tmp_path, monkeypatch):
    entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
    provider = Provider()
    def blocked(prompt):
        entered.set()
        assert release.wait(3)
        return "LATE ANSWER"
    provider.discuss = blocked
    path, doc, _ = prepared(tmp_path, monkeypatch, provider)
    outcome = []
    def cancelled():
        if cancel.is_set():
            raise RuntimeError("window closed")
    thread = threading.Thread(target=lambda: outcome.append(ask(path, doc, check_cancelled=cancelled)))
    thread.start()
    try:
        assert entered.wait(2)
        pending = service.load_discussion(path)
        assert pending["pending_request"]["live"] is True
        for operation in (lambda: service.save_discussion(path, pending, expected_revision=pending["revision"]),
                          lambda: service.attach_evidence(path, evidence("B"), expected_revision=pending["revision"]),
                          lambda: ask(path, pending)):
            with pytest.raises(service.DiscussionBusy):
                operation()
        cancel.set()
        thread.join(2)
        assert not thread.is_alive()
        assert outcome[0]["turns"][-1]["status"] == "cancelled"
        other_path = tmp_path / "other.json"
        other = service.create_discussion(other_path)
        saved = path.read_bytes()
        release.set()
        time.sleep(.05)
        assert path.read_bytes() == saved and service.load_discussion(other_path) == other
    finally:
        release.set()
        thread.join(3)


def test_timeout_is_finite_and_late_reply_cannot_overwrite(tmp_path, monkeypatch):
    release = threading.Event()
    provider = Provider()
    def blocked(prompt):
        release.wait(3)
        return "LATE"
    provider.discuss = blocked
    path, doc, _ = prepared(tmp_path, monkeypatch, provider)
    monkeypatch.setattr(service, "REQUEST_TIMEOUT_SECONDS", .04)
    try:
        started = time.monotonic()
        doc = ask(path, doc)
        assert time.monotonic() - started < 1
        assert doc["turns"][-1]["status"] == "timed_out" and doc["draft_question"] == "질문"
        saved = path.read_bytes()
        release.set()
        time.sleep(.05)
        assert path.read_bytes() == saved
    finally:
        release.set()


@pytest.mark.parametrize("stop", ["timeout", "cancel"])
def test_slow_retrieval_cannot_start_provider_after_request_has_stopped(tmp_path, monkeypatch, stop):
    entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
    path, doc, provider = prepared(tmp_path, monkeypatch)
    def search(*args, **kwargs):
        entered.set()
        release.wait(3)
        return KnowledgeSearchResult("no_material", [], "calculix")
    monkeypatch.setattr(experts, "search_knowledge_result", search)
    monkeypatch.setattr(service, "REQUEST_TIMEOUT_SECONDS", .06 if stop == "timeout" else 2)
    outcome = []
    def cancelled():
        if cancel.is_set():
            raise RuntimeError("cancel")
    thread = threading.Thread(target=lambda: outcome.append(ask(path, doc, template_id="material", check_cancelled=cancelled)))
    thread.start()
    try:
        assert entered.wait(2)
        if stop == "cancel":
            cancel.set()
        thread.join(2)
        assert not thread.is_alive()
        assert outcome[0]["turns"][-1]["status"] == ("timed_out" if stop == "timeout" else "cancelled")
        release.set()
        time.sleep(.05)
        assert provider.prompts == []
    finally:
        release.set()
        thread.join(3)


def test_restored_pending_is_interrupted_without_provider_call(tmp_path, monkeypatch):
    path, doc, provider = prepared(tmp_path, monkeypatch)
    doc["pending_request"] = {"id": "crashed", "question": "미완료 질문", "evidence_id": "A"}
    doc["turns"] = [{"id": "crashed", "question": "미완료 질문", "status": "pending", "reply": "", "evidence_sources": []}]
    atomic_json(path, doc)
    original = path.read_bytes()
    restored = service.load_discussion(path)
    assert restored["pending_request"]["live"] is False
    assert restored["turns"][0]["status"] == "interrupted" and restored["draft_question"] == "미완료 질문"
    assert path.read_bytes() == original and provider.prompts == []
    saved = service.save_discussion(path, restored, expected_revision=doc["revision"])
    assert saved["pending_request"] is None and len(saved["failures"]) == 1


def test_replaced_document_drops_late_response_without_touching_replacement(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    provider = Provider()
    def blocked(prompt):
        entered.set()
        release.wait(3)
        return "OLD DOCUMENT REPLY"
    provider.discuss = blocked
    path, doc, _ = prepared(tmp_path, monkeypatch, provider)
    errors = []
    def run():
        try:
            ask(path, doc)
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(2)
        replacement = service.create_discussion(tmp_path / "replacement.json")
        # Simulate an external replacement that bypasses the service lock.
        replacement["revision"] = service.load_discussion(path)["revision"]
        atomic_json(path, replacement)
        release.set()
        thread.join(2)
        assert isinstance(errors[0], service.DiscussionConflict)
        assert service.load_discussion(path) == replacement
    finally:
        release.set()
        thread.join(3)


def test_os_write_lock_is_reported_as_busy(tmp_path, monkeypatch):
    path, doc, _ = prepared(tmp_path, monkeypatch)
    with FileLock(str(path) + ".lock", timeout=0):
        with pytest.raises(service.DiscussionBusy):
            service.save_discussion(path, doc, expected_revision=doc["revision"])
        script = """
import sys
from caelab.assist.discussions import load_discussion, save_discussion, DiscussionBusy
doc = load_discussion(sys.argv[1])
try:
    save_discussion(sys.argv[1], doc, expected_revision=doc['revision'])
except DiscussionBusy:
    print('busy')
else:
    raise AssertionError('OS lock did not protect another process')
"""
        result = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "busy"


def test_corrupt_document_fails_without_replacing_original(tmp_path):
    path = tmp_path / "conversation.json"
    path.write_text('{"schema_version":9}', encoding="ascii")
    original = path.read_bytes()
    with pytest.raises(service.DiscussionFormatError):
        service.load_discussion(path)
    assert path.read_bytes() == original
