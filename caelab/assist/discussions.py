"""Saved, read-only expert discussions over immutable native evidence snapshots."""
from __future__ import annotations

import copy
import hashlib
import json
import queue
import threading
import time
import uuid
from pathlib import Path, PureWindowsPath

from filelock import FileLock, Timeout

from ..native.run_store import atomic_json, utc_now
from . import experts
from .knowledge import extract_citations, verify_citations

REQUEST_TIMEOUT_SECONDS = 60.0  # includes retrieval and provider work
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


def _validate(document):
    if not isinstance(document, dict) or document.get("schema_version") != 1:
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
    try:
        json.dumps(document, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise DiscussionFormatError("대화에는 JSON으로 저장할 수 있는 유한 값만 넣을 수 있습니다.") from error


def _read(path):
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise DiscussionFormatError("대화 파일을 읽지 못했습니다.") from error
    _validate(document)
    return document


def _interrupt(document):
    pending = document.get("pending_request")
    if pending is None:
        return
    reason = "이전 요청이 중단되었습니다. 자동으로 다시 호출하지 않습니다."
    for turn in document["turns"]:
        if turn.get("id") == pending["id"]:
            turn.update(status="interrupted", error=reason, finished_at=utc_now())
    document["draft_question"] = pending["question"]
    if not any(isinstance(f, dict) and f.get("request_id") == pending["id"] for f in document["failures"]):
        document["failures"].append({"request_id": pending["id"], "reason": reason,
                                      "evidence_sources": []})
    document["pending_request"] = None


def _current(path, expected_revision, *, idle=True):
    document = _read(path)
    if document["revision"] != expected_revision:
        raise DiscussionConflict("다른 창에서 대화가 바뀌었습니다. 다시 열어 확인하세요.")
    if idle:
        if _active(path):
            raise DiscussionBusy("이 대화에 진행 중인 질문이 있습니다.")
        _interrupt(document)
    return document


def _write(path, document):
    document = copy.deepcopy(document)
    document["revision"] += 1
    document["updated_at"] = utc_now()
    if document.get("pending_request"):
        document["pending_request"].pop("live", None)
    _validate(document)
    atomic_json(path, document)
    return copy.deepcopy(document)


def create_discussion(path, *, title="새 대화") -> dict:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _lock(path):
            if path.exists():
                raise DiscussionConflict("이미 있는 대화 문서를 덮어쓰지 않습니다.")
            now = utc_now()
            document = {"schema_version": 1, "id": str(uuid.uuid4()), "revision": 0, "title": title,
                        "created_at": now, "updated_at": now, "evidences": {}, "selected_evidence_id": None,
                        "turns": [], "constraints": [], "failures": [], "proposals": [],
                        "pending_request": None, "draft_question": "", "provider_id": None}
            _validate(document)
            atomic_json(path, document)
            return copy.deepcopy(document)
    except Timeout as error:
        raise DiscussionBusy("다른 창에서 대화를 저장하고 있습니다.") from error


def load_discussion(path) -> dict:
    path = Path(path).resolve()
    document = _read(path)
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
    _validate(candidate)
    try:
        with _lock(path):
            current = _current(path, expected_revision)
            if candidate["id"] != current["id"] or candidate["revision"] != expected_revision:
                raise DiscussionConflict("다른 대화 또는 개정의 내용으로 덮어쓸 수 없습니다.")
            if candidate["evidences"] != current["evidences"]:
                raise DiscussionConflict("저장으로 근거를 바꿀 수 없습니다. 근거 붙이기를 사용하세요.")
            if candidate.get("pending_request"):
                # Recovered pending requests are historical, never live jobs.
                _interrupt(candidate)
            return _write(path, candidate)
    except Timeout as error:
        raise DiscussionBusy("다른 창에서 대화를 저장하고 있습니다.") from error


def attach_evidence(path, evidence, *, expected_revision: int) -> dict:
    path = Path(path).resolve()
    evidence = copy.deepcopy(evidence)
    _validate_evidence(evidence)
    try:
        with _lock(path):
            document = _current(path, expected_revision)
            old = document["evidences"].get(evidence["id"])
            if old is not None and old != evidence:
                raise DiscussionConflict("같은 근거 ID의 원래 자료를 교체할 수 없습니다.")
            document["evidences"][evidence["id"]] = evidence
            document["selected_evidence_id"] = evidence["id"]
            return _write(path, document)
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
    targets = {item["source_path"] for item in evidence["files"] if "input" in item["role"] or "include" in item["role"]}
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
                          **{key: evidence.get(key) for key in ("run_id", "input_revision", "frame_index")},
                          "evidence_id": evidence["id"]})
    return proposals


def _provided_materials(discussion, evidence, origin):
    materials = []
    for source in discussion.evidence_sources:
        if source == "knowledge":
            for entry in discussion.knowledge_result.entries:
                materials.append({"source": source, "citation_id": experts.citation_id(entry),
                                  "topic": entry.topic, "text": entry.text, **origin})
        else:
            key = {"result_summary": "summary_text", "model_summary": "model_text", "solver_log": "log_text"}[source]
            text = evidence[key]
            materials.append({"source": source, "text_field": key, "text_length": len(text),
                              "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), **origin})
    return materials


def ask_discussion(path, question, *, evidence_id: str, template_id: str,
                   provider_id: str | None = None, expected_revision: int, check_cancelled=None) -> dict:
    """Ask once with a frozen document/evidence context and a 60-second deadline.

    Only the caller writes. A late daemon worker can neither update a document
    nor start a provider after retrieval has been cancelled or timed out.
    """
    path = Path(path).resolve()
    if not isinstance(question, str) or not question.strip():
        raise ValueError("질문을 입력하세요.")
    template = experts.get_template(template_id)
    try:
        lease = _lock(path, request=True)
        lease.acquire()
    except Timeout as error:
        raise DiscussionBusy("이 대화에 진행 중인 질문이 있습니다.") from error
    try:
        try:
            with _lock(path):
                document = _current(path, expected_revision, idle=False)
                _interrupt(document)
                provider = experts.get_provider(provider_id or document.get("provider_id"))
                if isinstance(provider, experts.OpenAICompatibleProvider):
                    provider.timeout_seconds = REQUEST_TIMEOUT_SECONDS
                if evidence_id not in document["evidences"]:
                    raise ValueError("선택한 근거가 대화에 없습니다.")
                evidence = copy.deepcopy(document["evidences"][evidence_id])
                request_id = str(uuid.uuid4())
                origin = {"evidence_id": evidence_id, "evidence_name": evidence.get("summary_text", "").splitlines()[0][:120] if evidence.get("summary_text") else evidence_id,
                          **{key: evidence.get(key) for key in ("solver_id", "run_id", "input_revision", "frame_index")}}
                now = utc_now()
                turn = {"id": request_id, "question": question, "reply": "", "status": "pending", "error": None,
                        "template_id": template.id, "role": template.name, "provider_id": provider.id,
                        "provider_name": provider.name, **origin, "evidence_sources": [], "provided_materials": [],
                        "constraints": copy.deepcopy(document["constraints"]),
                        "citations": {"shown_ids": [], "unverified": [], "verified": []},
                        "created_at": now, "finished_at": None}
                history = copy.deepcopy(document["turns"])
                document["turns"].append(turn)
                document["selected_evidence_id"] = evidence_id
                document["pending_request"] = {"id": request_id, "question": question, **origin,
                                               "template_id": template.id, "provider_id": provider.id, "started_at": now,
                                               "constraints": copy.deepcopy(document["constraints"])}
                document["draft_question"] = question
                pending_document = _write(path, document)
        except Timeout as error:
            raise DiscussionBusy("다른 창에서 대화를 저장하고 있습니다.") from error

        deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
        stopped = threading.Event()
        responses = queue.Queue(maxsize=1)
        supplied = queue.Queue(maxsize=1)
        discussion = experts.Discussion(template=template, summary_text=evidence["summary_text"],
                    model_text=evidence["model_text"], log_tail=evidence["log_text"], provider=provider,
                    history=history, constraints=copy.deepcopy(document["constraints"]),
                    failures=copy.deepcopy(document["failures"]), solver_id=evidence["solver_id"], native_evidence=evidence)

        class Cancelled(Exception):
            pass

        def checkpoint():
            if stopped.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("전문가 요청 대기 시간이 지났습니다.")
            if check_cancelled is not None:
                try:
                    check_cancelled()
                except Exception as error:
                    raise Cancelled(str(error)) from error

        def request():
            try:
                checkpoint()
                problems = verify_evidence(evidence, path.parent / "assets")
                if problems:
                    raise ValueError("근거 무결성 확인 실패: " + "; ".join(problems))
                available, reason = provider.availability()
                if not available:
                    responses.put(("unavailable", "", reason))
                    return
                prompt = discussion.build_prompt(question)
                context = {"evidence_sources": list(discussion.evidence_sources),
                           "provided_materials": _provided_materials(discussion, evidence, origin),
                           "shown_ids": list(discussion.knowledge_ids),
                           "knowledge_status": discussion.knowledge_result.status if discussion.knowledge_result else "not_requested",
                           "knowledge_error": discussion.knowledge_result.error if discussion.knowledge_result else None}
                checkpoint()  # retrieval must not launch a provider after cancellation
                supplied.put(context)
                reply = provider.discuss(prompt)
                if not isinstance(reply, str) or not reply.strip():
                    raise ValueError("공급자가 비어 있는 답변을 반환했습니다.")
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
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                status, reply, error_text = "timed_out", "", f"전문가 요청이 {REQUEST_TIMEOUT_SECONDS:g}초 제한을 넘었습니다."
                break
            try:
                status, reply, error_text = responses.get(timeout=min(.05, remaining))
                break
            except queue.Empty:
                pass
        stopped.set()
        try:
            with _lock(path):
                current = _current(path, pending_document["revision"], idle=False)
                if current["id"] != pending_document["id"] or (current.get("pending_request") or {}).get("id") != request_id:
                    raise DiscussionConflict("질문을 시작한 대화가 바뀌어 늦은 응답을 저장하지 않았습니다.")
                final_turn = next(t for t in current["turns"] if t["id"] == request_id)
                final_turn.update(status=status, reply=reply, error=error_text, finished_at=utc_now())
                try:
                    context = supplied.get_nowait()
                except queue.Empty:
                    context = None
                if context is not None:
                    final_turn.update({key: context[key] for key in ("evidence_sources", "provided_materials", "knowledge_status", "knowledge_error")})
                    final_turn["citations"]["shown_ids"] = context["shown_ids"]
                if status == "completed":
                    shown = final_turn["citations"]["shown_ids"]
                    unverified = verify_citations(reply, shown)
                    final_turn["citations"] = {"shown_ids": shown, "unverified": unverified,
                                                "verified": [cid for cid in extract_citations(reply) if cid in shown]}
                    if "model_summary" in final_turn["evidence_sources"]:
                        current["proposals"].extend(_native_proposals(reply, evidence, request_id))
                    current["draft_question"] = ""
                else:
                    current["failures"].append({"request_id": request_id, "reason": error_text,
                                               "evidence_sources": list(template.allowed_tools), **origin})
                current["pending_request"] = None
                return _write(path, current)
        except Timeout as error:
            raise DiscussionBusy("응답을 저장하는 동안 다른 창에서 저장하고 있습니다.") from error
    finally:
        lease.release()
