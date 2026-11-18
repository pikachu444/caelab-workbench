"""Saved, read-only expert discussions over immutable native evidence snapshots."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path, PureWindowsPath

from filelock import FileLock, Timeout

from ..artifact_refs import (
    ArtifactRefV1, ProducerIdentity, canonical_json_bytes, canonical_sha256, validate_relative_artifact_path,
)
from ..native.run_store import atomic_json, utc_now
from ..native.input_snapshot import fingerprint
from . import experts
from .contracts import (
    QUESTION_SCOPES,
    ROLE_POLICY_V1_ID,
    ROLE_POLICY_V2_ID,
    build_role_read_policy,
)
from .knowledge import extract_citations, pin_knowledge_index, verify_citations
from .query_language import build_retrieval_decision, prepare_query

REQUEST_TIMEOUT_SECONDS = 85.0  # backward compatibility alias for administrative wait boundary
PREPARATION_TIMEOUT_SECONDS = 20.0  # policy default budget for verification, availability, and prompt building
PROVIDER_TIMEOUT_SECONDS = 60.0     # dedicated budget for provider.discuss
OVERALL_TIMEOUT_SECONDS = 85.0      # administrative wait and late response rejection boundary
_EVIDENCE_KEYS = {"id", "source_kind", "created_at", "solver_id", "run_id", "node_id",
                  "input_revision", "input_manifest", "units", "unit_source", "process_state",
                  "parse_state", "frame_index", "summary_text", "model_text", "log_text", "files", "warnings"}


class DiscussionConflict(ValueError):
    """The saved revision or document identity changed."""


class DiscussionBusy(RuntimeError):
    """Another writer or live request owns this document."""


class DiscussionFormatError(ValueError):
    """Unsupported document or evidence structure."""


def verify_evidence(evidence, asset_root):
    # Late import lets discussions load before any native evidence is attached.
    from ..native.evidence import verify_evidence as verify
    return verify(evidence, asset_root)


def _lock(path, *, request=False):
    return FileLock(str(path) + (".request.lock" if request else ".lock"), timeout=0, thread_local=False)


def _active(path):
    try:
        with _lock(path, request=True):
            return False
    except Timeout:
        return True


def _validate_evidence(evidence):
    if not isinstance(evidence, dict) or not _EVIDENCE_KEYS <= evidence.keys():
        raise DiscussionFormatError("근거의 필수 출처 정보가 없습니다.")
    if not isinstance(evidence["id"], str) or not evidence["id"] or evidence["source_kind"] not in ("native_run", "manual"):
        raise DiscussionFormatError("근거 ID 또는 종류가 잘못되었습니다.")
    if any(not isinstance(evidence[key], str) for key in ("summary_text", "model_text", "log_text")):
        raise DiscussionFormatError("근거 자료는 문자열이어야 합니다.")
    if not isinstance(evidence["files"], list):
        raise DiscussionFormatError("근거 파일 목록이 잘못되었습니다.")
    for item in evidence["files"]:
        if not isinstance(item, dict) or not {"role", "source_path", "stored_path", "sha256", "size"} <= item.keys():
            raise DiscussionFormatError("근거 파일의 경로·해시 정보가 없습니다.")
        stored = item["stored_path"]
        if any(not isinstance(item[key], str) for key in ("role", "source_path", "sha256")) or type(item["size"]) is not int or item["size"] < 0:
            raise DiscussionFormatError("근거 파일의 종류·경로·해시·크기가 잘못되었습니다.")
        if not isinstance(stored, str) or not stored or PureWindowsPath(stored).root or PureWindowsPath(stored).drive or ".." in stored.replace("\\", "/").split("/"):
            raise DiscussionFormatError("대화 자산 경로는 자산 폴더 안의 상대 경로여야 합니다.")


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n[ \t]*\n")
_DISCUSSIONS_PRODUCER = ProducerIdentity("caelab.assist.discussions", "2.0")


def _validate_profile_ref(ref):
    if ref is None:
        return
    if not isinstance(ref, dict):
        raise DiscussionFormatError("관련성 판정 프로필 참조는 객체 또는 null이어야 합니다.")
    if set(ref.keys()) != {"profile_id", "content_sha256"}:
        raise DiscussionFormatError("관련성 판정 프로필 참조는 profile_id와 content_sha256만 포함해야 합니다.")
    if not isinstance(ref["profile_id"], str) or not ref["profile_id"].strip():
        raise DiscussionFormatError("관련성 판정 프로필 ID가 잘못되었습니다.")
    sha = ref["content_sha256"]
    if sha is not None and (not isinstance(sha, str) or not _SHA256_RE.fullmatch(sha)):
        raise DiscussionFormatError("관련성 판정 프로필 SHA256이 잘못되었습니다.")


def _validate(document):
    if not isinstance(document, dict) or type(document.get("schema_version")) is not int or document.get("schema_version") not in (1, 2):
        raise DiscussionFormatError("지원하지 않는 대화 문서입니다.")
    if not isinstance(document.get("id"), str) or not document["id"] or type(document.get("revision")) is not int or document["revision"] < 0:
        raise DiscussionFormatError("대화 ID·개정 번호가 잘못되었습니다.")
    if not isinstance(document.get("title"), str) or not isinstance(document.get("evidences"), dict):
        raise DiscussionFormatError("대화 제목·근거 목록이 잘못되었습니다.")
    for key in ("turns", "constraints", "failures", "proposals"):
        if not isinstance(document.get(key), list):
            raise DiscussionFormatError(f"대화 {key} 목록이 잘못되었습니다.")
    if any(not isinstance(turn, dict) for turn in document["turns"]):
        raise DiscussionFormatError("대화 발언 형식이 잘못되었습니다.")
    pending = document.get("pending_request")
    if pending is not None and (not isinstance(pending, dict) or not isinstance(pending.get("id"), str) or not pending["id"] or not isinstance(pending.get("question"), str)):
        raise DiscussionFormatError("진행 중 요청 정보가 잘못되었습니다.")
    for key, evidence in document["evidences"].items():
        _validate_evidence(evidence)
        if key != evidence["id"]:
            raise DiscussionFormatError("근거 ID와 문서 목록이 다릅니다.")
    selected = document.get("selected_evidence_id")
    if selected is not None and selected not in document["evidences"]:
        raise DiscussionFormatError("선택한 근거가 문서에 없습니다.")
    if document["schema_version"] == 2:
        scope = document.get("question_scope")
        if scope not in QUESTION_SCOPES:
            raise DiscussionFormatError("대화 질문 범위(question_scope)가 잘못되었습니다.")
        for key in ("review_requests", "review_resolution_events", "draft_history"):
            if not isinstance(document.get(key), list) or any(not isinstance(item, dict) for item in document[key]):
                raise DiscussionFormatError(f"대화 {key} 형식이 잘못되었습니다.")
        corpus_ref = document.get("corpus_snapshot_ref")
        if corpus_ref is not None and not isinstance(corpus_ref, dict):
            raise DiscussionFormatError("corpus_snapshot_ref 형식이 잘못되었습니다.")
        policy_id = document.get("tool_policy_id")
        if not isinstance(policy_id, str) or not policy_id.strip():
            raise DiscussionFormatError("tool_policy_id가 잘못되었습니다.")
        draft_ctx = document.get("draft_context")
        if not isinstance(draft_ctx, dict):
            raise DiscussionFormatError("draft_context 형식이 잘못되었습니다.")
        if "retrieval_assessment_profile_ref" in draft_ctx:
            _validate_profile_ref(draft_ctx["retrieval_assessment_profile_ref"])
    try:
        json.dumps(document, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise DiscussionFormatError("대화에는 JSON으로 저장할 수 있는 유한 값만 넣을 수 있습니다.") from error


def _upgrade_v1_turn_in_memory(turn: dict) -> dict:
    upgraded = copy.deepcopy(turn)
    upgraded.setdefault("schema_version", 1)
    upgraded.setdefault("question_scope", "case_analysis")
    upgraded.setdefault("responds_to_review_ids", [])
    upgraded.setdefault("request_context_ref", None)
    upgraded.setdefault("provider_capabilities", None)
    upgraded.setdefault("tool_trace_ref", None)
    upgraded.setdefault("retrieval_decisions", [])
    upgraded.setdefault("claims", [])
    upgraded.setdefault("answer_review_ids", [])
    upgraded.setdefault("evidence_pack_hash", None)
    upgraded.setdefault("corpus_revision", None)
    upgraded.setdefault("budget_used", None)
    upgraded.setdefault(
        "capability_limits",
        {"citation_verification": "id_only", "claim_review_status": "not_assessed", "semantic_relevance_assessed": False},
    )
    return upgraded


def _upgrade_v1_to_v2(document: dict) -> dict:
    upgraded = copy.deepcopy(document)
    if upgraded.get("schema_version") == 2:
        return upgraded
    upgraded["schema_version"] = 2
    upgraded["question_scope"] = "case_analysis"
    upgraded["review_requests"] = []
    upgraded["review_resolution_events"] = []
    upgraded["corpus_snapshot_ref"] = None
    upgraded["tool_policy_id"] = ROLE_POLICY_V1_ID
    upgraded["draft_context"] = {
        "question_scope": "case_analysis",
        "template_id": "convergence",
        "retrieval_assessment_profile_ref": None,
    }
    upgraded["draft_history"] = []
    upgraded["turns"] = [_upgrade_v1_turn_in_memory(turn) for turn in upgraded["turns"]]
    return upgraded


def _read_raw(path):
    try:
        raw_bytes = path.read_bytes()
        document = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, ValueError, UnicodeError) as error:
        raise DiscussionFormatError("대화 파일을 읽지 못했습니다.") from error
    _validate(document)
    return document, raw_bytes


def _read(path):
    document, _raw = _read_raw(path)
    return document


def _interrupt(document):
    pending = document.get("pending_request")
    if pending is None:
        return
    reason = "이전 요청이 중단되었습니다. 자동으로 다시 호출하지 않습니다."
    for turn in document["turns"]:
        if turn.get("id") == pending["id"]:
            # A restored request has no observed completion time. Keep this
            # deterministic across read-only recovery views and their later save.
            turn.update(status="interrupted", error=reason, finished_at=turn.get("finished_at"))
    document["draft_question"] = pending["question"]
    if not any(isinstance(f, dict) and f.get("request_id") == pending["id"] for f in document["failures"]):
        document["failures"].append({"request_id": pending["id"], "reason": reason,
                                      "evidence_sources": []})
    document["pending_request"] = None


def _current_raw(path, expected_revision, *, idle=True):
    document, raw_bytes = _read_raw(path)
    if document["revision"] != expected_revision:
        raise DiscussionConflict("다른 창에서 대화가 바뀌었습니다. 다시 열어 확인하세요.")
    if idle:
        if _active(path):
            raise DiscussionBusy("이 대화에 진행 중인 질문이 있습니다.")
        _interrupt(document)
    raw_v1_bytes = raw_bytes if document["schema_version"] == 1 else None
    if raw_v1_bytes is not None:
        document = _upgrade_v1_to_v2(document)
        _interrupt(document) if idle else None
    return document, raw_v1_bytes


def _current(path, expected_revision, *, idle=True):
    document, _raw_v1 = _current_raw(path, expected_revision, idle=idle)
    return document


def _write_v1_migration_backup(path: Path, raw_v1_bytes: bytes) -> Path:
    v1_sha256 = hashlib.sha256(raw_v1_bytes).hexdigest()
    backup_path = path.parent / "assets" / "migrations" / f"{v1_sha256}.json"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with backup_path.open("xb") as stream:
            stream.write(raw_v1_bytes)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if backup_path.read_bytes() != raw_v1_bytes:
            raise DiscussionFormatError("기존 v1 마이그레이션 백업의 바이트가 원본과 일치하지 않습니다.")
    return backup_path


def _write(path, document, *, raw_v1_bytes: bytes | None = None):
    document = copy.deepcopy(document)
    document["schema_version"] = 2
    document["revision"] += 1
    document["updated_at"] = utc_now()
    if document.get("pending_request"):
        document["pending_request"].pop("live", None)
    _validate(document)
    if raw_v1_bytes is not None:
        _write_v1_migration_backup(path, raw_v1_bytes)
        if path.read_bytes() != raw_v1_bytes:
            raise DiscussionConflict("백업 저장 중 다른 창에서 대화 문서가 바뀌었습니다.")
    atomic_json(path, document)
    return copy.deepcopy(document)


def create_discussion(path, *, title="새 대화", question_scope: str = "case_analysis") -> dict:
    if question_scope not in QUESTION_SCOPES:
        raise ValueError(f"지원하지 않는 질문 범위입니다: {question_scope!r}")
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _lock(path):
            if path.exists():
                raise DiscussionConflict("이미 있는 대화 문서를 덮어쓰지 않습니다.")
            now = utc_now()
            document = {
                "schema_version": 2,
                "id": str(uuid.uuid4()),
                "revision": 0,
                "title": title,
                "created_at": now,
                "updated_at": now,
                "question_scope": question_scope,
                "evidences": {},
                "selected_evidence_id": None,
                "turns": [],
                "constraints": [],
                "failures": [],
                "proposals": [],
                "pending_request": None,
                "draft_question": "",
                "provider_id": None,
                "review_requests": [],
                "review_resolution_events": [],
                "corpus_snapshot_ref": None,
                "tool_policy_id": ROLE_POLICY_V2_ID,
                "draft_context": {
                    "question_scope": question_scope,
                    "template_id": "theory" if question_scope == "theory" else "convergence",
                    "retrieval_assessment_profile_ref": None,
                },
                "draft_history": [],
            }
            _validate(document)
            atomic_json(path, document)
            return copy.deepcopy(document)
    except Timeout as error:
        raise DiscussionBusy("다른 창에서 대화를 저장하고 있습니다.") from error


def load_discussion(path) -> dict:
    path = Path(path).resolve()
    document, raw = _read_raw(path)
    if document.get("schema_version") == 1:
        document = _upgrade_v1_to_v2(document)
        document["v1_disk_sha256"] = hashlib.sha256(raw).hexdigest()
    pending = document.get("pending_request")
    if pending:
        live = _active(path)
        if not live:
            # Return an interrupted view without rewriting on open. Preserve its
            # request metadata so the UI can distinguish recovery from live work.
            _interrupt(document)
            pending["status"] = "interrupted"
            document["pending_request"] = pending
        pending["live"] = live
    return document


def save_discussion(path, document, *, expected_revision: int) -> dict:
    path = Path(path).resolve()
    candidate = copy.deepcopy(document)
    v1_disk_sha256 = candidate.pop("v1_disk_sha256", None)
    _validate(candidate)
    try:
        with _lock(path):
            current, raw_v1_bytes = _current_raw(path, expected_revision, idle=False)
            if raw_v1_bytes is not None:
                if v1_disk_sha256 != hashlib.sha256(raw_v1_bytes).hexdigest():
                    raise DiscussionConflict("저장된 v1 원본이 바뀌었습니다. 다시 열어 확인하세요.")
            elif v1_disk_sha256 is not None:
                raise DiscussionConflict("저장된 v1 원본이 바뀌었습니다. 다시 열어 확인하세요.")
            if _active(path):
                raise DiscussionBusy("이 대화에 진행 중인 질문이 있습니다.")
            saved_pending = copy.deepcopy(current.get("pending_request"))
            _interrupt(current)
            if candidate["id"] != current["id"] or candidate["revision"] != expected_revision:
                raise DiscussionConflict("다른 대화 또는 개정의 내용으로 덮어쓸 수 없습니다.")
            if candidate.get("pending_request"):
                pending = {key: value for key, value in candidate["pending_request"].items() if key not in {"live", "status"}}
                if pending != saved_pending:
                    raise DiscussionConflict("저장으로 질문 요청 기록을 바꿀 수 없습니다.")
                draft = candidate.get("draft_question", "")
                _interrupt(candidate)
                candidate["draft_question"] = draft
            editable = {
                "title",
                "constraints",
                "draft_question",
                "provider_id",
                "selected_evidence_id",
                "question_scope",
                "draft_context",
            }
            for key in (current.keys() | candidate.keys()) - editable - {"pending_request", "failures", "draft_history"}:
                if key not in current or key not in candidate or candidate[key] != current[key]:
                    raise DiscussionConflict("저장으로 기존 발언·제안·근거·출처 기록을 바꿀 수 없습니다.")
            if any(not isinstance(value, str) for value in candidate["constraints"]):
                raise DiscussionFormatError("사용자 제약은 문자열 목록이어야 합니다.")
            old_failures = current["failures"]
            if candidate["failures"][:len(old_failures)] != old_failures or any(
                    not isinstance(value, str) for value in candidate["failures"][len(old_failures):]):
                raise DiscussionConflict("기존 실패 기록은 보존하고 사용자 실패 이유만 추가할 수 있습니다.")
            old_history = current.get("draft_history", [])
            cand_history = candidate.get("draft_history", [])
            if cand_history[:len(old_history)] != old_history or any(
                    not isinstance(value, dict) for value in cand_history[len(old_history):]):
                raise DiscussionConflict("기존 초안 이력은 보존하고 새 항목만 추가할 수 있습니다.")
            for key in editable:
                if key in candidate:
                    current[key] = candidate[key]
            current["failures"] = candidate["failures"]
            current["draft_history"] = cand_history
            return _write(path, current, raw_v1_bytes=raw_v1_bytes)
    except Timeout as error:
        raise DiscussionBusy("다른 창에서 대화를 저장하고 있습니다.") from error


def attach_evidence(path, evidence, *, expected_revision: int) -> dict:
    path = Path(path).resolve()
    evidence = copy.deepcopy(evidence)
    _validate_evidence(evidence)
    try:
        with _lock(path):
            document, raw_v1_bytes = _current_raw(path, expected_revision)
            old = document["evidences"].get(evidence["id"])
            if old is not None and old != evidence:
                raise DiscussionConflict("같은 근거 ID의 원래 자료를 교체할 수 없습니다.")
            document["evidences"][evidence["id"]] = evidence
            document["selected_evidence_id"] = evidence["id"]
            return _write(path, document, raw_v1_bytes=raw_v1_bytes)
    except Timeout as error:
        raise DiscussionBusy("다른 창에서 대화를 저장하고 있습니다.") from error


def _native_proposals(reply, evidence, request_id):
    block = experts._PROPOSAL_BLOCK.search(reply)
    if not block:
        return []
    try:
        items = json.loads(block.group(1))
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    targets = {item["source_path"]: item for item in evidence["files"] if item["role"] in {"input", "include"}}
    proposals = []
    for item in items:
        if not isinstance(item, dict) or not {"target_file", "location", "before", "after", "reason"} <= item.keys():
            continue
        if any(not isinstance(item[key], str) or not item[key].strip() for key in ("target_file", "location", "reason")) or item["target_file"] not in targets:
            continue
        try:
            json.dumps(item, allow_nan=False)
        except (TypeError, ValueError):
            continue
        proposals.append({**{key: item[key] for key in ("target_file", "location", "before", "after", "reason")},
                          "id": str(uuid.uuid4()), "request_id": request_id, "status": "unapplied",
                          "target_sha256": targets[item["target_file"]]["sha256"], "review_history": [],
                          **{key: evidence.get(key) for key in ("run_id", "input_revision", "frame_index")},
                          "evidence_id": evidence["id"]})
    return proposals


def _sha_valid(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _source_input_issues(evidence):
    """Check the complete captured source list without reparsing or guessing paths."""
    issues = []
    original_items = [item for item in evidence["files"] if item["role"] in {"input", "include"}]
    originals = {item["source_path"]: item for item in original_items}
    if len(originals) != len(original_items):
        issues.append("원본 입력/INCLUDE 보존 자산의 경로가 중복되어 출처가 모호합니다.")
    manifest = evidence.get("input_manifest")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list) or not manifest["files"] or not isinstance(manifest.get("includes"), list):
        return ["전체 입력/INCLUDE 캡처 목록이 없어 현재 원본과의 일치를 확인할 수 없습니다."]
    recorded = {}
    observed = {}
    for index, record in enumerate(manifest["files"]):
        if not isinstance(record, dict) or not isinstance(record.get("source_path"), str) or not _sha_valid(record.get("original_sha256")):
            issues.append("입력/INCLUDE 캡처 경로·SHA256 자료가 잘못되었습니다.")
            continue
        source = record["source_path"]
        if source in recorded:
            issues.append(f"입력/INCLUDE 캡처 경로가 중복됩니다: {source}")
        recorded[source] = record["original_sha256"]
        item = originals.get(source)
        if item is None or item["role"] != ("input" if index == 0 else "include") or item["sha256"] != recorded[source]:
            issues.append(f"원본 입력/INCLUDE 보존 자산과 캡처 목록이 다릅니다: {source}")
        if item and (item.get("verification") == "mismatch" or item.get("expected_sha256") not in (None, item["sha256"])):
            issues.append(f"실행 당시 입력 SHA256과 보존 자산이 다릅니다: {source}")
        path = Path(source)
        if not path.is_absolute():
            issues.append(f"원본 절대 경로를 현재 환경에서 확인할 수 없습니다: {source}")
            continue
        try:
            before = fingerprint(path)
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if before is None or before != fingerprint(path):
                issues.append(f"확인 중 입력 원본이 변경되었습니다: {source}")
            elif digest.hexdigest() != recorded[source]:
                issues.append(f"오래된 제안: 입력/INCLUDE 원본 SHA256이 달라졌습니다: {source}")
            else:
                observed[path] = before
        except OSError as error:
            issues.append(f"입력/INCLUDE 원본이 없거나 읽을 수 없습니다: {source} ({error})")
    if set(originals) != set(recorded):
        issues.append("원본 입력/INCLUDE 자산 목록과 캡처 목록의 파일 범위가 다릅니다.")
    for include in manifest["includes"]:
        if not isinstance(include, dict) or include.get("source_path") not in recorded or include.get("parent_path") not in recorded:
            issues.append("캡처 INCLUDE 참조에 보존되지 않은 원본 경로가 있습니다.")
    for path, before in observed.items():
        if fingerprint(path) != before:
            issues.append(f"전체 입력을 확인하는 동안 원본이 변경되었습니다: {path}")
    return issues


def _proposal_evidence(document, proposal):
    evidence = document["evidences"].get(proposal.get("evidence_id"))
    if evidence is None:
        raise ValueError("제안의 원래 근거가 없습니다.")
    required = {"id", "request_id", "target_file", "location", "before", "after", "reason", "evidence_id",
                "run_id", "input_revision", "frame_index"}
    if not required <= proposal.keys() or any(not isinstance(proposal[key], str) or not proposal[key].strip() for key in
                                            ("id", "request_id", "target_file", "location", "reason")):
        raise ValueError("옛 제안의 대상·전후값·이유·출처 정보가 부족하거나 잘못되었습니다.")
    if proposal.get("status") not in ("unapplied", "manual_required", "blocked", "rejected"):
        raise ValueError("저장된 제안 상태를 신뢰할 수 없습니다. 자동 적용은 지원하지 않습니다.")
    if any(proposal[key] != evidence.get(key) for key in ("run_id", "input_revision", "frame_index")):
        raise ValueError("제안과 원래 실행·입력 개정·프레임 출처가 다릅니다.")
    turn = next((turn for turn in document["turns"] if turn.get("id") == proposal["request_id"]), None)
    if turn is None or turn.get("status") != "completed" or any(
            turn.get(key) != proposal[key] for key in ("evidence_id", "run_id", "input_revision", "frame_index")):
        raise ValueError("제안의 원래 답변과 근거 출처를 확인할 수 없습니다.")
    if not isinstance(turn.get("evidence_sources"), list) or "model_summary" not in turn["evidence_sources"]:
        raise ValueError("제안한 답변에 실제 입력 자료가 제공됐는지 확인할 수 없습니다.")
    targets = [item for item in evidence["files"] if item["role"] in {"input", "include"} and item["source_path"] == proposal["target_file"]]
    if len(targets) != 1 or not _sha_valid(targets[0]["sha256"]):
        raise ValueError("제안 대상은 원래 근거의 input/include 파일 하나여야 합니다. 실행 스냅샷은 대상이 아닙니다.")
    if proposal.get("target_sha256", targets[0]["sha256"]) != targets[0]["sha256"]:
        raise ValueError("제안 대상의 저장 SHA256이 원래 캡처와 다릅니다.")
    return evidence, targets[0]


def review_proposal(path, proposal_id, *, expected_revision: int, decision="check", reason="") -> dict:
    """Record an explicit review only. Native files and solver state are untouched."""
    if decision not in ("check", "reject") or not isinstance(reason, str):
        raise ValueError("제안 검토는 check 또는 reject와 문자열 이유를 사용하세요.")
    path = Path(path).resolve()
    try:
        with _lock(path):
            document, raw_v1_bytes = _current_raw(path, expected_revision)
            matches = [item for item in document["proposals"] if isinstance(item, dict) and item.get("id") == proposal_id]
            if len(matches) != 1:
                raise ValueError("제안 ID가 없거나 중복되어 검토할 수 없습니다.")
            proposal = matches[0]
            issues = []
            try:
                evidence, target = _proposal_evidence(document, proposal)
                proposal["target_sha256"] = target["sha256"]  # derived only from captured original bytes
                issues.extend(verify_evidence(evidence, path.parent / "assets"))
                issues.extend(_source_input_issues(evidence))
            except (ValueError, TypeError, KeyError, OSError) as error:
                issues.append(str(error))
            if not isinstance(proposal.get("review_history", []), list):
                raise DiscussionFormatError("제안 검토 이력 형식이 잘못되었습니다.")
            if proposal.get("status") == "rejected":
                status, review_reason = "rejected", "이미 거절한 제안입니다. 새 제안 없이는 되살리지 않습니다."
            elif decision == "reject":
                status, review_reason = "rejected", reason.strip() or "사용자가 제안을 거절했습니다."
                document["failures"].append({"proposal_id": proposal_id, "reason": review_reason,
                                            "evidence_sources": [], "evidence_id": proposal.get("evidence_id"),
                                            "run_id": proposal.get("run_id"), "input_revision": proposal.get("input_revision")})
            elif issues:
                status, review_reason = "blocked", "; ".join(issues)
            else:
                status, review_reason = "manual_required", "미적용: 전용 프로그램에서 수정하세요. 수정 뒤 새 계산은 별도 명령입니다."
            now = utc_now()
            proposal.update(status=status, review_reason=review_reason, reviewed_at=now)
            proposal.setdefault("review_history", []).append({"decision": decision, "status": status,
                "reason": review_reason, "user_reason": reason, "reviewed_at": now, "issues": issues})
            return _write(path, document, raw_v1_bytes=raw_v1_bytes)
    except Timeout as error:
        raise DiscussionBusy("다른 창에서 대화를 저장하고 있습니다.") from error


_REQUEST_ARTIFACT_KEYS = ("request_context_ref", "request_artifact_ref", "request_artifact_sha256",
                          "tool_trace_ref", "retrieval_trace_ref")


def _read_request_json(asset_root, ref, turn_id, role):
    artifact = ArtifactRefV1.from_dict(ref)
    if artifact.role != role or not artifact.path.startswith(f"requests/{turn_id}/"):
        raise ValueError("자료 역할 또는 요청 경로 불일치")
    raw = (asset_root / validate_relative_artifact_path(artifact.path, root=asset_root)).read_bytes()
    if not raw or len(raw) != artifact.size or hashlib.sha256(raw).hexdigest() != artifact.sha256:
        raise ValueError("자료 바이트 또는 SHA256 불일치")
    payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_invalid_json_constant)
    if not isinstance(payload, dict) or not payload:
        raise ValueError("요청 자료는 비어 있지 않은 JSON 객체여야 합니다.")
    identifiers = [payload[key] for key in ("request_id", "turn_id") if key in payload]
    if not identifiers or any(identifier != turn_id for identifier in identifiers):
        raise ValueError("요청 ID 불일치")
    return payload


def _reject_invalid_json_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def _case_evidence_identity(evidence):
    summary = evidence.get("summary_text") or ""
    return {
        "evidence_id": evidence["id"],
        "evidence_name": summary.splitlines()[0][:120] if summary else evidence["id"],
        "evidence_summary": summary,
        "evidence_refs": evidence.get("files", []),
    }


def _turn_artifact_issues(document, turn, asset_root):
    refs_present = any(turn.get(key) is not None for key in _REQUEST_ARTIFACT_KEYS)
    completed = turn.get("status") == "completed"
    # V1 and legacy reference-free case turns predate immutable request artifacts.
    if not refs_present and not (completed and (
        turn.get("question_scope") == "theory" or turn.get("schema_version") == 2 or "request_artifact_ref" in turn
    )):
        return []
    turn_id = turn.get("id")
    issues = []
    context_ref = turn.get("request_context_ref")
    trace_ref = turn.get("tool_trace_ref")
    def report(key, error):
        detail = "파일 누락 또는 접근 실패" if isinstance(error, OSError) else str(error)
        issues.append(f"발언 {turn_id} {key} 무결성 확인 실패: {detail}")

    try:
        context = _read_request_json(asset_root, context_ref, turn_id, "request_context")
        modern = context.get("schema") == "caelab.expert_request.v1"
        if (("schema" in context and not modern)
                or type(context.get("schema_version")) is not int or context["schema_version"] != 1):
            raise ValueError("지원하지 않는 요청 문맥 스키마")
        if context.get("discussion_id") != document["id"]:
            raise ValueError("대화 ID 불일치")
        if any(context.get(key) != turn.get(key) for key in ("question", "question_scope", "template_id")):
            raise ValueError("요청 문맥과 발언 내용 또는 범위 불일치")
        if modern and (
            context.get("turn_id") != turn_id
            or not {"corpus_revision", "index_sha256"} <= context.keys()
            or context["corpus_revision"] != turn.get("corpus_revision")
            or context["index_sha256"] != turn.get("index_sha256")
            or (context["index_sha256"] is not None and not _sha_valid(context["index_sha256"]))
        ):
            raise ValueError("요청 문맥의 코퍼스 개정 또는 색인 SHA256 불일치")
        if turn.get("question_scope") == "case_analysis":
            evidence = document.get("evidences", {}).get(context.get("evidence_id"))
            if evidence is None or context.get("evidence_sha256") != canonical_sha256(evidence):
                raise ValueError("요청 문맥의 첨부 근거 또는 SHA256 불일치")
            identity = _case_evidence_identity(evidence)
            if turn.get("evidence_id") != identity["evidence_id"] or any(
                (key in turn and turn[key] != value)
                or (key in context and (context[key] != value or turn.get(key) != value))
                for key, value in identity.items()
            ):
                raise ValueError("발언과 요청 문맥의 첨부 근거 식별 정보 불일치")
            for key in ("run_id", "solver_id", "input_revision", "frame_index"):
                if turn.get(key) != evidence.get(key):
                    raise ValueError("발언과 요청 문맥의 실행 근거 불일치")
        if modern or any(key in turn for key in ("request_artifact_ref", "request_artifact_sha256")):
            if turn.get("request_artifact_ref") != context_ref or turn.get("request_artifact_sha256") != context_ref["sha256"]:
                report("request_artifact_ref", ValueError("요청 자료 별칭 또는 SHA256 불일치"))
    except (OSError, ValueError, TypeError) as error:
        report("request_context_ref", error)

    if trace_ref is None and not completed and turn.get("retrieval_trace_ref") is None:
        return issues
    try:
        trace = _read_request_json(asset_root, trace_ref, turn_id, "tool_trace")
        if type(trace.get("schema_version")) is not int or trace["schema_version"] != 1:
            raise ValueError("지원하지 않는 추적 스키마")
        if trace.get("request_context_ref") != context_ref or trace.get("status") != turn.get("status"):
            raise ValueError("추적의 요청 문맥 또는 상태 불일치")
        if ("chunks" in trace or "retrieval_trace_ref" in turn) and turn.get("retrieval_trace_ref") != trace_ref:
            report("retrieval_trace_ref", ValueError("추적 자료 별칭 불일치"))
        events = trace.get("events")
        if not isinstance(events, list) or not events:
            raise ValueError("추적 이벤트가 없습니다.")
        previous = None
        for seq, event_ref in enumerate(events, 1):
            event = _read_request_json(asset_root, event_ref, turn_id, "trace_event")
            if type(event.get("seq")) is not int or event["seq"] != seq or event.get("previous_event_sha256") != previous:
                raise ValueError("추적 이벤트 순서 또는 연결 해시 불일치")
            if seq == 1 and event.get("request_context_sha256") != (context_ref or {}).get("sha256"):
                raise ValueError("추적 이벤트의 요청 문맥 해시 불일치")
            previous = event_ref["sha256"]
        if completed and (len(events) < 2 or event.get("stage") == "preparation"
                          or event.get("status") not in {"completed", "ok"}
                          or not event.get("finished_at") or event["finished_at"] != turn.get("finished_at")):
            raise ValueError("완료된 요청의 종료 추적 이벤트가 없습니다.")
        chunks = trace.get("chunks", [])
        if not isinstance(chunks, list):
            raise ValueError("추적 자료 조각 목록이 잘못되었습니다.")
        stored_chunks = []
        for chunk_ref in chunks:
            chunk = _read_request_json(asset_root, chunk_ref, turn_id, "retrieval_chunks")
            if not isinstance(chunk.get("chunks"), list) or not chunk["chunks"] or any(
                not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip()
                for item in chunk["chunks"]
            ):
                raise ValueError("추적 자료 조각이 비어 있습니다.")
            stored_chunks.extend(chunk["chunks"])
        if "chunks" in trace and stored_chunks != [
            item for item in turn.get("provided_materials", []) if isinstance(item, dict) and item.get("source") == "knowledge"
        ]:
            raise ValueError("추적 자료 조각과 제공 근거 불일치")
    except (OSError, ValueError, TypeError, AttributeError) as error:
        report("tool_trace_ref", error)
    return issues


def request_artifact_issues(document, asset_root):
    """Check immutable request and trace references against their owning turns."""
    return [
        issue for turn in document.get("turns", ())
        for issue in _turn_artifact_issues(document, turn, asset_root)
    ]


def discussion_issues(path, document=None) -> list[str]:
    """Read-only reopening diagnostics; no provider calls or transient document keys."""
    path = Path(path).resolve()
    try:
        document = _read(path) if document is None else copy.deepcopy(document)
        _validate(document)
        issues = []
        for identifier, evidence in document["evidences"].items():
            issues.extend(f"근거 {identifier}: {issue}" for issue in verify_evidence(evidence, path.parent / "assets"))
            if any(item["role"] in {"input", "include"} for item in evidence["files"]):
                issues.extend(f"근거 {identifier}: {issue}" for issue in _source_input_issues(evidence))
        issues.extend(request_artifact_issues(document, path.parent / "assets"))
        for proposal in document["proposals"]:
            try:
                if not isinstance(proposal, dict):
                    raise ValueError("저장된 옛 제안의 자료 형식이 잘못되었습니다.")
                _proposal_evidence(document, proposal)
            except (ValueError, TypeError, KeyError) as error:
                issues.append(f"제안 출처 확인 제한: {error}")
        return list(dict.fromkeys(issues))
    except (OSError, ValueError, TypeError, KeyError) as error:
        return [f"대화/보존 자료 확인 실패: {error}"]


def _provided_materials(discussion, evidence, origin):
    materials = []
    for source in discussion.evidence_sources:
        if source == "knowledge":
            for entry in discussion.knowledge_result.entries:
                chunk = entry.corpus_chunk
                provenance = {
                    key: getattr(chunk, key)
                    for key in ("edition", "document_sha256", "line_start", "line_end", "char_start", "char_end",
                                "page_start", "page_end")
                } if chunk is not None else {}
                materials.append({"source": source, "citation_id": experts.citation_id(entry),
                                  "source_kind": entry.source_kind, "review_status": entry.review_status,
                                  "topic": entry.topic, "text": entry.text,
                                  "text_sha256": hashlib.sha256(entry.text.encode("utf-8")).hexdigest(),
                                  **provenance, **origin})
        elif evidence is not None:
            key = {"result_summary": "summary_text", "model_summary": "model_text", "solver_log": "log_text"}[source]
            text = evidence[key]
            materials.append({"source": source, "text_field": key, "text_length": len(text),
                              "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), **origin})
    return materials


def _write_immutable_request_json(asset_root: Path, rel_posix: str, artifact_id: str, role: str, format_id: str, payload: dict) -> dict:
    raw = canonical_json_bytes(payload)
    target = asset_root / rel_posix
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return ArtifactRefV1(
        id=artifact_id,
        role=role,
        path=rel_posix,
        sha256=hashlib.sha256(raw).hexdigest(),
        size=len(raw),
        format_id=format_id,
        producer=_DISCUSSIONS_PRODUCER,
    ).to_dict()


def _segment_paragraph(reply, raw_start, raw_end, request_id, reply_sha, verified_set):
    paragraph = reply[raw_start:raw_end]
    start = raw_start + len(paragraph) - len(paragraph.lstrip())
    end = raw_start + len(paragraph.rstrip())
    if start >= end:
        return None
    citation_ids = [cid for cid in extract_citations(reply[start:end]) if cid in verified_set]
    return {
        "claim_id": str(uuid.uuid5(uuid.UUID(request_id), f"{start}:{end}:{reply_sha}")),
        "text_span": [start, end],
        "citation_ids": citation_ids,
        "basis": "evidence" if citation_ids else "model_general_unverified",
        "support": "not_assessed",
    }


def _segment_reply_claims(reply: str, request_id: str, verified_citations: list[str]) -> list[dict]:
    reply_sha = hashlib.sha256(reply.encode("utf-8")).hexdigest()
    verified_set = set(verified_citations)
    matches = list(_PARAGRAPH_SPLIT_RE.finditer(reply))
    starts = [0, *(match.end() for match in matches)]
    ends = [*(match.start() for match in matches), len(reply)]
    claims = []
    for start, end in zip(starts, ends):
        claim = _segment_paragraph(reply, start, end, request_id, reply_sha, verified_set)
        if claim is not None:
            claims.append(claim)
    return claims


def _provider_capabilities_snapshot(provider, transport_mode: str) -> dict:
    configured = False
    if provider.id == "openai_compatible":
        configured = bool(getattr(provider, "endpoint", "") and getattr(provider, "model", ""))
    elif provider.id not in ("none", "base"):
        configured = True
    return {
        "provider_id": provider.id,
        "model_id": getattr(provider, "model", None),
        "transport_mode": transport_mode,
        "supported_tool_schema_version": None,
        "max_context": None,
        "configured": configured,
        "probe_status": "not_run",
        "last_probe_at": None,
    }


def ask_discussion(path, question, *, evidence_id: str | None = None, template_id: str,
                   provider_id: str | None = None, expected_revision: int,
                   question_scope: str | None = None, review_policy_id: str = "none",
                   responds_to_review_ids: tuple[str, ...] = (),
                   retrieval_assessment_profile_ref: dict | None = None,
                   check_cancelled=None, on_stage=None) -> dict:
    """Ask once with a frozen document/evidence context and bounded preparation/provider budgets.

    Only the caller writes. A late daemon worker can neither update a document
    nor start a provider after retrieval has been cancelled or timed out.
    """
    path = Path(path).resolve()
    if not isinstance(question, str) or not question.strip():
        raise ValueError("질문을 입력하세요.")
    if question_scope is not None and question_scope not in QUESTION_SCOPES:
        raise ValueError(f"지원하지 않는 질문 범위입니다: {question_scope!r}")
    effective_scope = question_scope or (
        "theory" if template_id == "theory" and evidence_id is None else "case_analysis"
    )
    if effective_scope == "theory" and evidence_id is not None:
        raise ValueError("이론·문헌 자문(theory) 범위에서는 실행·모델 근거를 첨부하지 않습니다.")
    if effective_scope == "case_analysis" and template_id == "theory":
        raise ValueError("계산 결과 분석(case_analysis)에서는 실행 근거를 읽는 역할을 선택하세요.")
    if not isinstance(review_policy_id, str) or not review_policy_id.strip():
        raise ValueError("review_policy_id는 비어 있지 않은 문자열이어야 합니다.")
    if isinstance(responds_to_review_ids, (str, bytes)) or not hasattr(responds_to_review_ids, "__iter__"):
        raise ValueError("responds_to_review_ids는 문자열 목록이어야 합니다.")
    responds_list = [str(rid) for rid in responds_to_review_ids]
    if any(not rid.strip() for rid in responds_list):
        raise ValueError("responds_to_review_ids 항목은 비어 있을 수 없습니다.")
    _validate_profile_ref(retrieval_assessment_profile_ref)

    template = experts.get_template(template_id)
    try:
        lease = _lock(path, request=True)
        lease.acquire()
    except Timeout as error:
        raise DiscussionBusy("이 대화에 진행 중인 질문이 있습니다.") from error
    try:
        try:
            with _lock(path):
                document, raw_v1_bytes = _current_raw(path, expected_revision, idle=False)
                _interrupt(document)
                prep_budget = PREPARATION_TIMEOUT_SECONDS
                provider_budget = PROVIDER_TIMEOUT_SECONDS
                overall_budget = OVERALL_TIMEOUT_SECONDS
                if REQUEST_TIMEOUT_SECONDS < overall_budget:
                    overall_budget = REQUEST_TIMEOUT_SECONDS
                    provider_budget = min(provider_budget, REQUEST_TIMEOUT_SECONDS)
                    prep_budget = min(prep_budget, REQUEST_TIMEOUT_SECONDS)

                if effective_scope == "theory":
                    evidence = None
                    identity = {"evidence_id": None}
                    origin = {
                        "evidence_id": None,
                        "evidence_name": "이론·문헌 자문 (첨부 없음)",
                        "solver_id": None,
                        "run_id": None,
                        "input_revision": None,
                        "frame_index": None,
                    }
                else:
                    if evidence_id is None or evidence_id not in document["evidences"]:
                        raise ValueError("선택한 근거가 대화에 없습니다.")
                    evidence = copy.deepcopy(document["evidences"][evidence_id])
                    identity = _case_evidence_identity(evidence)
                    origin = {
                        "evidence_id": evidence_id, "evidence_name": identity["evidence_name"],
                        **{key: evidence.get(key) for key in ("solver_id", "run_id", "input_revision", "frame_index")},
                    }

                provider = experts.get_provider(provider_id or document.get("provider_id"))
                if isinstance(provider, experts.OpenAICompatibleProvider):
                    provider.timeout_seconds = provider_budget
                request_id = str(uuid.uuid4())
                now = utc_now()
                policy_id = document.get("tool_policy_id") or ROLE_POLICY_V2_ID
                role_policy = build_role_read_policy(template.id, template.allowed_tools, policy_id=policy_id)
                transport_mode = "prompt_only"
                provider_caps = _provider_capabilities_snapshot(provider, transport_mode)
                profile_ref = (
                    copy.deepcopy(retrieval_assessment_profile_ref)
                    if retrieval_assessment_profile_ref is not None
                    else copy.deepcopy((document.get("draft_context") or {}).get("retrieval_assessment_profile_ref"))
                )
                query_plan = prepare_query(
                    question,
                    explicit_facets={"solver": evidence["solver_id"]} if evidence and evidence.get("solver_id") else None,
                )
                pinned = pin_knowledge_index() if "knowledge" in template.allowed_tools else None
                index = pinned.index if pinned is not None else None
                index_identity = {
                    "corpus_revision": index.corpus_revision if index is not None else None,
                    "index_sha256": index.index_digest if index is not None else None,
                }
                request_context_payload = {
                    "schema": "caelab.expert_request.v1",
                    "schema_version": 1,
                    "request_id": request_id,
                    "turn_id": request_id,
                    "discussion_id": document["id"],
                    "question": question,
                    "question_scope": effective_scope,
                    "template_id": template.id,
                    "role_policy": role_policy.to_dict(),
                    "constraints": copy.deepcopy(document["constraints"]),
                    **identity,
                    "evidence_sha256": canonical_sha256(evidence) if evidence is not None else None,
                    "query_plan": query_plan.to_dict(),
                    "retrieval_assessment_profile_ref": profile_ref,
                    "review_policy_id": review_policy_id,
                    "responds_to_review_ids": responds_list,
                    **index_identity,
                    "created_at": now,
                }
                asset_root = path.parent / "assets"
                request_context_ref = _write_immutable_request_json(
                    asset_root, f"requests/{request_id}/request_context.json", f"request-context:{request_id}",
                    "request_context", "cae-request-context-v1", request_context_payload,
                )
                capability_limits = {
                    "read_policy_id": "cae-read-v1",
                    "role_policy_id": role_policy.policy_id,
                    "role_policy_sha256": role_policy.policy_sha256,
                    "transport_mode": transport_mode,
                    "citation_verification": "id_only",
                    "claim_review_status": "not_assessed",
                    "semantic_relevance_assessed": False,
                }

                turn = {
                    "schema_version": 2,
                    "id": request_id,
                    "question": question,
                    "reply": "",
                    "status": "pending",
                    "error": None,
                    "question_scope": effective_scope,
                    "template_id": template.id,
                    "role": template.name,
                    "provider_id": provider.id,
                    "provider_name": provider.name,
                    "provider_model": getattr(provider, "model", None),
                    **identity, **origin,
                    "evidence_sources": [],
                    "provided_materials": [],
                    "constraints": copy.deepcopy(document["constraints"]),
                    "citations": {"shown_ids": [], "unverified": [], "verified": []},
                    "responds_to_review_ids": responds_list,
                    "review_policy_id": review_policy_id,
                    "request_context_ref": request_context_ref,
                    "request_artifact_ref": request_context_ref,
                    "request_artifact_sha256": request_context_ref["sha256"],
                    "provider_capabilities": provider_caps,
                    "tool_trace_ref": None,
                    "retrieval_trace_ref": None,
                    "retrieval_decisions": [],
                    "claims": [],
                    "answer_review_ids": [],
                    "evidence_pack_hash": None,
                    **index_identity,
                    "budget_used": {"preparation_seconds": 0.0, "provider_seconds": 0.0, "total_seconds": 0.0, "tool_calls": 0, "rounds": 0},
                    "capability_limits": capability_limits,
                    "created_at": now,
                    "finished_at": None,
                    "stage": "preparation",
                }
                history = copy.deepcopy(document["turns"])
                document["turns"].append(turn)
                document["question_scope"] = effective_scope
                document.setdefault("draft_context", {})["question_scope"] = effective_scope
                document["draft_context"]["template_id"] = template.id
                if retrieval_assessment_profile_ref is not None:
                    document["draft_context"]["retrieval_assessment_profile_ref"] = copy.deepcopy(retrieval_assessment_profile_ref)
                if effective_scope == "case_analysis":
                    document["selected_evidence_id"] = evidence_id
                document["pending_request"] = {
                    "id": request_id,
                    "question": question,
                    "question_scope": effective_scope,
                    **origin,
                    "template_id": template.id,
                    "provider_id": provider.id,
                    "started_at": now,
                    "constraints": copy.deepcopy(document["constraints"]),
                }
                document["draft_question"] = question
                pending_document = _write(path, document, raw_v1_bytes=raw_v1_bytes)
        except Timeout as error:
            raise DiscussionBusy("다른 창에서 대화를 저장하고 있습니다.") from error

        start_time = time.monotonic()
        prep_deadline = start_time + prep_budget
        overall_deadline = start_time + overall_budget
        if on_stage is not None:
            try:
                on_stage("preparation")
            except Exception:
                pass
        stopped = threading.Event()
        # A cancelled caller can leave the preparation message unread. The worker
        # can still post its one terminal result and exit.
        responses = queue.Queue()
        context_saved = threading.Event()
        context = None
        provider_started = threading.Event()
        provider_deadline_box = [overall_deadline]
        provider_started_at_box = [None]
        discussion = experts.Discussion(
            template=template,
            summary_text=evidence["summary_text"] if evidence is not None else "",
            model_text=evidence["model_text"] if evidence is not None else "",
            log_tail=evidence["log_text"] if evidence is not None else "",
            provider=provider,
            history=history,
            constraints=copy.deepcopy(document["constraints"]),
            failures=copy.deepcopy(document["failures"]),
            solver_id=evidence["solver_id"] if evidence is not None else "",
            native_evidence=evidence,
            question_scope=effective_scope,
        )

        class Cancelled(Exception):
            pass

        def checkpoint(stage="prep"):
            now_mono = time.monotonic()
            if stopped.is_set():
                raise Cancelled("전문가 요청 대기 시간이 지났습니다.")
            if stage == "prep":
                if now_mono >= prep_deadline:
                    raise TimeoutError(f"전문가 준비 대기 시간이 {prep_budget:g}초 제한을 넘었습니다.")
            else:
                if now_mono >= provider_deadline_box[0] or now_mono >= overall_deadline:
                    raise TimeoutError(f"전문가 요청이 {provider_budget:g}초 제한을 넘었습니다.")
            if check_cancelled is not None:
                try:
                    check_cancelled()
                except Exception as error:
                    raise Cancelled(str(error)) from error

        def request():
            try:
                checkpoint("prep")
                if evidence is not None:
                    problems = verify_evidence(evidence, path.parent / "assets")
                    if problems:
                        raise ValueError("근거 무결성 확인 실패: " + "; ".join(problems))
                available, reason = provider.availability()
                if not available:
                    responses.put(("unavailable", "", reason))
                    return
                discussion.history = []
                for prior_turn in history:
                    checkpoint("prep")
                    if prior_turn.get("status") == "completed" and not _turn_artifact_issues(
                        document, prior_turn, asset_root
                    ):
                        discussion.history.append(prior_turn)
                prompt = discussion.build_prompt(question, pinned_index=pinned)
                materials = _provided_materials(discussion, evidence, origin)
                k_res = discussion.knowledge_result
                retrieval_decisions = []
                if "knowledge" in template.allowed_tools and k_res is not None:
                    used_plan = getattr(k_res, "query_plan", None) or query_plan
                    candidates = [
                        entry.corpus_chunk or {
                            "citation": experts.citation_id(entry),
                            "text": entry.text,
                            "coverage_facets": {"solver": entry.solver_scope},
                        }
                        for entry in k_res.entries
                    ] or k_res.mismatched_candidates
                    decision = build_retrieval_decision(
                        used_plan,
                        candidates,
                        explicit_facets={"solver": k_res.solver} if k_res.solver else None,
                    )
                    retrieval_decisions.append(decision.to_dict())
                context = {
                    "evidence_sources": list(discussion.evidence_sources),
                    "provided_materials": materials,
                    "shown_ids": list(discussion.knowledge_ids),
                    "knowledge_status": k_res.status if k_res else "not_requested",
                    "knowledge_error": k_res.error if k_res else None,
                    "retrieval_decisions": retrieval_decisions,
                }
                checkpoint("prep")  # retrieval must not launch a provider after cancellation or prep timeout
                responses.put(context)
                while not context_saved.wait(0.02):
                    checkpoint("prep")
                checkpoint("prep")

                if on_stage is not None:
                    checkpoint("prep")
                    try:
                        on_stage("provider")
                    except Exception:
                        pass
                checkpoint("prep")

                provider_started_at = time.monotonic()
                provider_started_at_box[0] = provider_started_at
                provider_deadline_box[0] = min(provider_started_at + provider_budget, overall_deadline)
                provider_started.set()
                checkpoint("provider")

                reply = provider.discuss(prompt)
                if not isinstance(reply, str) or not reply.strip():
                    raise ValueError("공급자가 비어 있는 답변을 반환했습니다.")
                checkpoint("provider")
                responses.put(("completed", reply, None))
            except (Cancelled, TimeoutError) as error:
                responses.put(("cancelled" if isinstance(error, Cancelled) else "timed_out", "", str(error)))
            except Exception as error:
                responses.put(("failed", "", f"{type(error).__name__}: {error}"))

        threading.Thread(target=request, name=f"expert-{request_id}", daemon=True).start()
        while True:
            try:
                if check_cancelled is not None:
                    check_cancelled()
            except Exception as error:
                status, reply, error_text = "cancelled", "", f"{type(error).__name__}: {error}"
                break
            now_mono = time.monotonic()
            if not provider_started.is_set():
                if now_mono >= prep_deadline:
                    status, reply, error_text = "timed_out", "", f"전문가 준비 대기 시간이 {prep_budget:g}초 제한을 넘었습니다."
                    break
                remaining = min(prep_deadline - now_mono, overall_deadline - now_mono)
            else:
                target_deadline = min(provider_deadline_box[0], overall_deadline)
                if now_mono >= target_deadline:
                    status, reply, error_text = "timed_out", "", f"전문가 요청이 {provider_budget:g}초 제한을 넘었습니다."
                    break
                remaining = target_deadline - now_mono

            if remaining <= 0:
                status, reply, error_text = "timed_out", "", f"전문가 요청이 {provider_budget:g}초 제한을 넘었습니다."
                break
            try:
                message = responses.get(timeout=min(.05, max(0.001, remaining)))
                prepared = isinstance(message, dict)
                if not prepared:
                    status, reply, error_text = message
                now_mono = time.monotonic()
                if check_cancelled is not None:
                    try:
                        check_cancelled()
                    except Exception as error:
                        status, reply, error_text = "cancelled", "", f"{type(error).__name__}: {error}"
                        break
                if not provider_started.is_set():
                    if now_mono >= prep_deadline:
                        status, reply, error_text = "timed_out", "", f"전문가 준비 대기 시간이 {prep_budget:g}초 제한을 넘었습니다."
                        break
                else:
                    target_deadline = min(provider_deadline_box[0], overall_deadline)
                    if now_mono >= target_deadline:
                        status, reply, error_text = "timed_out", "", f"전문가 요청이 {provider_budget:g}초 제한을 넘었습니다."
                        break
                if now_mono >= overall_deadline:
                    status, reply, error_text = "timed_out", "", f"전문가 요청이 {overall_budget:g}초 제한을 넘었습니다."
                    break
                if prepared:
                    context = message
                    context_saved.set()
                    continue
                break
            except queue.Empty:
                pass
        stopped.set()
        if status in {"cancelled", "timed_out"}:
            try:
                provider.cancel()
            except Exception as error:
                error_text += f" 공급자 중단 요청 실패: {type(error).__name__}"
        end_time = time.monotonic()
        total_elapsed = max(0.0, end_time - start_time)
        p_start = provider_started_at_box[0]
        if p_start is not None:
            prep_elapsed = max(0.0, p_start - start_time)
            provider_elapsed = max(0.0, end_time - p_start)
        else:
            prep_elapsed = total_elapsed
            provider_elapsed = 0.0
        try:
            with _lock(path):
                current = _current(path, pending_document["revision"], idle=False)
                if current["id"] != pending_document["id"] or (current.get("pending_request") or {}).get("id") != request_id:
                    raise DiscussionConflict("질문을 시작한 대화가 바뀌어 늦은 응답을 저장하지 않았습니다.")
                final_turn = next(t for t in current["turns"] if t["id"] == request_id)
                final_stage = "provider" if provider_started.is_set() else "preparation"
                finished_at = utc_now()
                final_turn.update(status=status, reply=reply, error=error_text, finished_at=finished_at, stage=final_stage)
                if context is not None:
                    final_turn["evidence_pack_hash"] = canonical_sha256({
                        "request_context_sha256": request_context_ref["sha256"],
                        "evidence_sources": context["evidence_sources"],
                        "provided_materials": context["provided_materials"],
                        "shown_ids": context["shown_ids"],
                    })
                    final_turn.update({
                        key: context[key]
                        for key in ("evidence_sources", "provided_materials", "knowledge_status",
                                    "knowledge_error", "retrieval_decisions")
                    })
                    final_turn["citations"]["shown_ids"] = context["shown_ids"]
                if status == "completed":
                    shown = final_turn["citations"]["shown_ids"]
                    unverified = verify_citations(reply, shown)
                    verified = [cid for cid in extract_citations(reply) if cid in shown]
                    final_turn["citations"] = {"shown_ids": shown, "unverified": unverified, "verified": verified}
                    final_turn["claims"] = _segment_reply_claims(reply, request_id, verified)
                    if evidence is not None and "model_summary" in final_turn["evidence_sources"]:
                        current["proposals"].extend(_native_proposals(reply, evidence, request_id))
                    current["draft_question"] = ""
                    current["draft_context"].pop("native_analysis_framing", None)
                else:
                    current["failures"].append({"request_id": request_id, "reason": error_text,
                                                "evidence_sources": list(template.allowed_tools), **origin})
                final_turn["budget_used"] = {
                    "preparation_seconds": round(prep_elapsed, 6),
                    "provider_seconds": round(provider_elapsed, 6),
                    "total_seconds": round(total_elapsed, 6),
                    "tool_calls": 0,
                    "rounds": 1 if provider_started.is_set() else 0,
                }
                # Record chained immutable trace events + terminal trace.json
                asset_root = path.parent / "assets"
                ev1_ref = _write_immutable_request_json(
                    asset_root,
                    f"requests/{request_id}/events/0001.json",
                    f"trace-event:{request_id}:1",
                    "trace_event",
                    "cae-trace-event-v1",
                    {
                        "seq": 1,
                        "previous_event_sha256": None,
                        "request_id": request_id,
                        "stage": "preparation",
                        "request_context_sha256": request_context_ref["sha256"],
                    },
                )
                ev2_ref = _write_immutable_request_json(
                    asset_root,
                    f"requests/{request_id}/events/0002.json",
                    f"trace-event:{request_id}:2",
                    "trace_event",
                    "cae-trace-event-v1",
                    {
                        "seq": 2,
                        "previous_event_sha256": ev1_ref["sha256"],
                        "request_id": request_id,
                        "stage": final_stage,
                        "status": status,
                        "finished_at": finished_at,
                    },
                )
                chunk_refs = []
                retrieved = [item for item in final_turn["provided_materials"] if item["source"] == "knowledge"]
                if retrieved:
                    chunk_refs.append(_write_immutable_request_json(
                        asset_root, f"requests/{request_id}/chunks.json", f"retrieval-chunks:{request_id}",
                        "retrieval_chunks", "cae-retrieval-chunks-v1", {"request_id": request_id, "chunks": retrieved},
                    ))
                trace_ref = _write_immutable_request_json(
                    asset_root,
                    f"requests/{request_id}/trace.json",
                    f"trace:{request_id}",
                    "tool_trace",
                    "cae-tool-trace-v1",
                    {
                        "schema_version": 1,
                        "request_id": request_id,
                        "request_context_ref": request_context_ref,
                        "events": [ev1_ref, ev2_ref],
                        "chunks": chunk_refs,
                        "tool_call_count": 0,
                        "status": status,
                        "budget_used": final_turn["budget_used"],
                    },
                )
                final_turn.update(tool_trace_ref=trace_ref, retrieval_trace_ref=trace_ref)
                current["pending_request"] = None
                return _write(path, current)
        except Timeout as error:
            raise DiscussionBusy("응답을 저장하는 동안 다른 창에서 저장하고 있습니다.") from error
    finally:
        lease.release()
