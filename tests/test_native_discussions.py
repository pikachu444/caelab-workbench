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


@pytest.mark.parametrize("field,value", [
    ("evidence_id", "B"), ("run_id", "run-B"), ("input_revision", "revision-B"),
    ("frame_index", 999), ("reply", "rewritten reply"), ("question", "rewritten question"),
    ("status", "failed"), ("evidence_sources", []), ("provided_materials", []),
    ("citations", {"shown_ids": ["invented:source"], "unverified": [], "verified": []}),
])
def test_save_cannot_reassign_or_rewrite_existing_turn_after_new_evidence(tmp_path, monkeypatch, field, value):
    path, doc, _ = prepared(tmp_path, monkeypatch)
    doc = ask(path, doc, "A의 원래 질문")
    doc = service.attach_evidence(path, evidence("B"), expected_revision=doc["revision"])
    original = path.read_bytes()
    original_turn = copy.deepcopy(doc["turns"][0])
    candidate = copy.deepcopy(doc)
    candidate["turns"][0][field] = value
    with pytest.raises(service.DiscussionConflict):
        service.save_discussion(path, candidate, expected_revision=doc["revision"])
    assert path.read_bytes() == original
    assert service.load_discussion(path)["turns"][0] == original_turn


@pytest.mark.parametrize("change", ["remove", "append", "origin", "values", "applied"])
def test_save_cannot_replace_existing_proposal_record(tmp_path, monkeypatch, change):
    reply = '```proposals\n' + json.dumps([
        {"target_file": "/original/A.inp", "location": "*STATIC", "before": "0.1", "after": "0.01", "reason": "원래 제안"}
    ]) + '\n```'
    path, doc, _ = prepared(tmp_path, monkeypatch, Provider(reply))
    doc = ask(path, doc, template_id="convergence")
    doc = service.attach_evidence(path, evidence("B"), expected_revision=doc["revision"])
    assert len(doc["proposals"]) == 1
    original = path.read_bytes()
    candidate = copy.deepcopy(doc)
    if change == "remove":
        candidate["proposals"].clear()
    elif change == "append":
        candidate["proposals"].append(copy.deepcopy(candidate["proposals"][0]))
    elif change == "origin":
        candidate["proposals"][0].update(evidence_id="B", run_id="run-B", frame_index=999)
    elif change == "values":
        candidate["proposals"][0].update(before="fabricated", after="replacement")
    else:
        candidate["proposals"][0]["status"] = "applied"
    with pytest.raises(service.DiscussionConflict):
        service.save_discussion(path, candidate, expected_revision=doc["revision"])
    assert path.read_bytes() == original


def test_save_allows_user_fields_and_append_only_failure_notes(tmp_path, monkeypatch):
    path, doc, _ = prepared(tmp_path, monkeypatch)
    doc = ask(path, doc)
    doc = service.attach_evidence(path, evidence("B"), expected_revision=doc["revision"])
    saved_turns = copy.deepcopy(doc["turns"])
    doc.update(title="사용자 제목", constraints=["형상 변경 금지"], draft_question="새 질문 초안",
               provider_id="none", selected_evidence_id="A", failures=["사용자가 추가한 실패 이유"])
    doc = service.save_discussion(path, doc, expected_revision=doc["revision"])
    assert doc["turns"] == saved_turns and doc["draft_question"] == "새 질문 초안"
    assert doc["selected_evidence_id"] == "A" and doc["provider_id"] == "none"
    original = path.read_bytes()
    doc["failures"][0] = "과거 실패 이유 변조"
    with pytest.raises(service.DiscussionConflict):
        service.save_discussion(path, doc, expected_revision=doc["revision"])
    assert path.read_bytes() == original


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


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_discussion_requests_provider_cancel_and_keeps_model_origin(tmp_path, monkeypatch, stop):
    provider = Provider()
    provider.model = "openai-codex/mock-only"
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    def blocked(prompt):
        entered.set()
        release.wait(2)
        return "late reply must not be saved"
    provider.discuss = blocked
    def cancel_provider():
        cancelled.set()
        release.set()
    provider.cancel = cancel_provider
    path, doc, _ = prepared(tmp_path, monkeypatch, provider)
    monkeypatch.setattr(service, "REQUEST_TIMEOUT_SECONDS", .06 if stop == "timeout" else 2)
    def check():
        if stop == "cancel" and entered.is_set():
            raise RuntimeError("user stopped waiting")
    try:
        doc = ask(path, doc, check_cancelled=check)
        turn = doc["turns"][-1]
        assert cancelled.is_set() and turn["status"] == ("cancelled" if stop == "cancel" else "timed_out")
        assert turn["provider_model"] == "openai-codex/mock-only" and turn["reply"] == ""
        assert doc["draft_question"] == "질문" and doc["pending_request"] is None
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
    restored["draft_question"] = "중단 후 편집한 질문"
    saved = service.save_discussion(path, restored, expected_revision=doc["revision"])
    assert saved["pending_request"] is None and len(saved["failures"]) == 1
    assert saved["draft_question"] == "중단 후 편집한 질문"
    assert saved["turns"][0]["finished_at"] is None


def test_stale_pending_recovery_cannot_hide_a_reply_or_origin_rewrite(tmp_path, monkeypatch):
    path, doc, _ = prepared(tmp_path, monkeypatch)
    doc["pending_request"] = {"id": "crashed", "question": "질문", "evidence_id": "A"}
    doc["turns"] = [{"id": "crashed", "question": "질문", "status": "pending", "reply": "", "evidence_id": "A"}]
    atomic_json(path, doc)
    original = path.read_bytes()
    candidate = service.load_discussion(path)
    candidate["turns"][0].update(reply="invented completion", evidence_id="B")
    with pytest.raises(service.DiscussionConflict):
        service.save_discussion(path, candidate, expected_revision=doc["revision"])
    assert path.read_bytes() == original


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


def reviewed_document(tmp_path, monkeypatch, *, include_target=False):
    from caelab.native.evidence import import_file_evidence, verify_evidence
    source = tmp_path / "source"
    source.mkdir()
    model = source / "model.inp"
    include = source / "material.inc"
    model.write_bytes(b"*HEADING\nreview input\n*INCLUDE,INPUT=material.inc\n")
    include.write_bytes(b"*MATERIAL,NAME=M\n*ELASTIC\n1000,0.25\n")
    path = tmp_path / "conversation.json"
    doc = service.create_discussion(path)
    value = import_file_evidence(path.parent / "assets", files={"input": model}, solver_id="calculix")
    monkeypatch.setattr(service, "verify_evidence", verify_evidence)
    target = include if include_target else model
    reply = '```proposals\n' + json.dumps([{
        "target_file": str(target), "location": "*ELASTIC" if include_target else "*HEADING",
        "before": "1000" if include_target else "review input", "after": "900" if include_target else "reviewed input",
        "reason": "사용자 검토를 위한 제안", "target_sha256": "0" * 64, "status": "applied", "run_id": "invented",
    }]) + '\n```'
    provider = Provider(reply)
    monkeypatch.setattr(experts, "get_provider", lambda *args: provider)
    doc = service.attach_evidence(path, value, expected_revision=doc["revision"])
    doc = service.ask_discussion(path, "입력 수정안을 제안해 주세요", evidence_id=value["id"],
                                 template_id="convergence", expected_revision=doc["revision"])
    assert doc["turns"][-1]["status"] == "completed", doc["turns"][-1]
    assert len(doc["proposals"]) == 1
    return path, doc, model, include, provider


def test_review_records_manual_required_from_captured_target_hash_without_writes(tmp_path, monkeypatch):
    path, doc, model, include, provider = reviewed_document(tmp_path, monkeypatch, include_target=True)
    proposal = copy.deepcopy(doc["proposals"][0])
    include_hash = hashlib.sha256(include.read_bytes()).hexdigest()
    assert proposal["status"] == "unapplied" and proposal["target_sha256"] == include_hash
    assert proposal["target_sha256"] != proposal["input_revision"]  # target and full input origin are separate
    assert proposal["run_id"] is None  # provider-supplied provenance was discarded
    originals = {file: file.read_bytes() for file in (model, include)}
    provider_calls = len(provider.prompts)
    doc = service.review_proposal(path, proposal["id"], expected_revision=doc["revision"])
    checked = doc["proposals"][0]
    assert checked["status"] == "manual_required" and "전용 프로그램" in checked["review_reason"]
    assert checked["review_history"][-1]["status"] == "manual_required"
    assert checked["review_history"][-1]["issues"] == [] and checked["reviewed_at"]
    for key in ("target_file", "target_sha256", "location", "before", "after", "reason", "evidence_id", "input_revision"):
        assert checked[key] == proposal[key]
    assert originals == {file: file.read_bytes() for file in originals}
    assert len(provider.prompts) == provider_calls
    assert service.load_discussion(path)["proposals"] == doc["proposals"]
    assert service.discussion_issues(path) == []


@pytest.mark.parametrize("changed", ["input", "include", "missing_input", "missing_include", "moved"])
def test_review_blocks_when_any_original_input_changed_or_is_missing(tmp_path, monkeypatch, changed):
    path, doc, model, include, _ = reviewed_document(tmp_path, monkeypatch)
    proposal = copy.deepcopy(doc["proposals"][0])
    target = include if "include" in changed else model
    if changed.startswith("missing"):
        target.unlink()
    elif changed == "moved":
        destination = tmp_path / "moved"
        destination.mkdir()
        model.rename(destination / model.name)  # same basename must not be guessed
    else:
        target.write_bytes(target.read_bytes() + b"** user change\n")
    existing = {file: file.read_bytes() for file in (model, include) if file.exists()}
    doc = service.review_proposal(path, proposal["id"], expected_revision=doc["revision"])
    checked = doc["proposals"][0]
    assert checked["status"] == "blocked" and str(target) in checked["review_reason"]
    assert checked["before"] == proposal["before"] and checked["after"] == proposal["after"] and checked["reason"] == proposal["reason"]
    assert checked["review_history"][-1]["issues"]
    assert existing == {file: file.read_bytes() for file in existing}


def test_review_reject_is_persistent_context_and_check_cannot_revive_it(tmp_path, monkeypatch):
    path, doc, _, _, provider = reviewed_document(tmp_path, monkeypatch)
    identifier = doc["proposals"][0]["id"]
    doc = service.review_proposal(path, identifier, expected_revision=doc["revision"], decision="reject", reason="규격 시험 때문에 재료 계수를 바꿀 수 없음")
    assert doc["proposals"][0]["status"] == "rejected"
    doc = service.review_proposal(path, identifier, expected_revision=doc["revision"])
    assert doc["proposals"][0]["status"] == "rejected"
    assert len(doc["proposals"][0]["review_history"]) == 2
    assert len(doc["failures"]) == 1 and "규격 시험" in doc["failures"][0]["reason"]
    provider.reply = "사용자 거절 이유를 유지합니다."
    value = next(iter(doc["evidences"]))
    doc = service.ask_discussion(path, "다른 방안을 검토하세요", evidence_id=value,
                                 template_id="interpretation", expected_revision=doc["revision"])
    assert "규격 시험 때문에" in provider.prompts[-1]
    assert doc["proposals"][0]["status"] == "rejected"
    assert all(item["status"] != "applied" for item in doc["proposals"])


def test_review_detects_input_change_while_another_include_is_being_checked(tmp_path, monkeypatch):
    from contextlib import contextmanager
    path, doc, model, include, _ = reviewed_document(tmp_path, monkeypatch)
    original_open = Path.open
    @contextmanager
    def changing_open(file, *args, **kwargs):
        with original_open(file, *args, **kwargs) as stream:
            yield stream
        if file == include and args == ("rb",):
            model.write_bytes(model.read_bytes() + b"** concurrent user change\n")
    monkeypatch.setattr(Path, "open", changing_open)
    doc = service.review_proposal(path, doc["proposals"][0]["id"], expected_revision=doc["revision"])
    assert doc["proposals"][0]["status"] == "blocked"
    assert str(model) in doc["proposals"][0]["review_reason"]


@pytest.mark.parametrize("malformed", ["no_hash", "wrong_hash", "execution_target", "missing_manifest", "missing_origin",
                                      "duplicate_include", "unknown_status", "unread_model"])
def test_legacy_proposals_load_and_review_safely(tmp_path, monkeypatch, malformed):
    path, doc, model, _, _ = reviewed_document(tmp_path, monkeypatch)
    value = next(iter(doc["evidences"].values()))
    proposal = doc["proposals"][0]
    if malformed == "no_hash":
        proposal.pop("target_sha256")  # valid P5 legacy: derive from immutable original capture
    elif malformed == "wrong_hash":
        proposal["target_sha256"] = "f" * 64
    elif malformed == "execution_target":
        # A P5 proposal might refer to a copied execution file. It is not an editable original.
        proposal["target_file"] = str(tmp_path / "run" / "model.inp")
        item = {**value["files"][0], "role": "execution_input", "source_path": proposal["target_file"]}
        value["files"].append(item)
    elif malformed == "missing_manifest":
        value["input_manifest"] = {}
    elif malformed == "missing_origin":
        proposal.pop("request_id")
    elif malformed == "duplicate_include":
        value["files"].append(copy.deepcopy(value["files"][1]))
    elif malformed == "unknown_status":
        proposal["status"] = "applied"
    else:
        doc["turns"][0]["evidence_sources"] = ["result_summary"]
    atomic_json(path, doc)
    assert service.load_discussion(path)["proposals"] == doc["proposals"]
    doc = service.review_proposal(path, proposal["id"], expected_revision=doc["revision"])
    assert doc["proposals"][0]["status"] == ("manual_required" if malformed == "no_hash" else "blocked")
    if malformed == "no_hash":
        assert doc["proposals"][0]["target_sha256"] == hashlib.sha256(model.read_bytes()).hexdigest()


def test_provider_cannot_propose_execution_snapshots_as_originals(tmp_path, monkeypatch):
    path, doc, provider = prepared(tmp_path, monkeypatch)
    value = evidence("B")
    value["files"].append({**value["files"][0], "role": "execution_input", "source_path": "/run/model.inp"})
    doc = service.attach_evidence(path, value, expected_revision=doc["revision"])
    provider.reply = '```proposals\n[{"target_file":"/run/model.inp","location":"line 1","before":1,"after":2,"reason":"bad target"}]\n```'
    doc = ask(path, doc, evidence_id="B", template_id="convergence")
    assert doc["proposals"] == []


def test_review_history_cannot_be_changed_by_general_save_and_revision_conflicts(tmp_path, monkeypatch):
    path, doc, _, _, _ = reviewed_document(tmp_path, monkeypatch)
    identifier = doc["proposals"][0]["id"]
    old_revision = doc["revision"]
    doc = service.review_proposal(path, identifier, expected_revision=old_revision)
    original = path.read_bytes()
    with pytest.raises(service.DiscussionConflict):
        service.review_proposal(path, identifier, expected_revision=old_revision)
    changed = copy.deepcopy(doc)
    changed["proposals"][0]["review_history"][0]["reason"] = "fabricated applied status"
    with pytest.raises(service.DiscussionConflict):
        service.save_discussion(path, changed, expected_revision=doc["revision"])
    with FileLock(str(path) + ".request.lock", timeout=0):
        with pytest.raises(service.DiscussionBusy):
            service.review_proposal(path, identifier, expected_revision=doc["revision"])
    assert path.read_bytes() == original


@pytest.mark.parametrize("failure", ["corrupt", "missing", "missing_original"])
def test_discussion_issues_is_read_only_and_reports_assets_and_originals(tmp_path, monkeypatch, failure):
    path, doc, model, _, provider = reviewed_document(tmp_path, monkeypatch)
    value = next(iter(doc["evidences"].values()))
    asset = path.parent / "assets" / value["files"][0]["stored_path"]
    if failure == "corrupt":
        asset.write_bytes(b"corrupted")
    elif failure == "missing":
        asset.unlink()
    else:
        model.unlink()
    original = path.read_bytes()
    original_doc = copy.deepcopy(doc)
    calls = len(provider.prompts)
    issues = service.discussion_issues(path, doc)
    assert issues and (str(model) if failure == "missing_original" else value["files"][0]["stored_path"]) in "\n".join(issues)
    assert service.discussion_issues(path) == issues
    assert doc == original_doc and path.read_bytes() == original
    assert len(provider.prompts) == calls
