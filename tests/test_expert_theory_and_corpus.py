"""Tests for D08 (evidence-free theory scope, Discussion v2 + v1 backup) and D09 (bilingual corpus/index)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
import pytest

from caelab.artifact_refs import ArtifactRefV1, ProducerIdentity, canonical_json_bytes, canonical_sha256
from caelab.assist import corpus, discussions, experts, knowledge, query_language, rag
from caelab.assist.contracts import (
    CORPUS_PROFILE_ID,
    ROLE_POLICY_V1_ID,
    ROLE_POLICY_V2_ID,
    CorpusManifest,
    QueryPlan,
    QueryVariant,
    ReassessmentLinkV1,
    RetrievalCandidateCheckV1,
    RetrievalDecisionV1,
    RetrievalRequirementV1,
    RetrievalTraceRecord,
    RoleReadPolicyV1,
    build_role_read_policy,
    map_corpus_source_kind_to_envelope,
)
from caelab.workbench import Workbench


# ------------------------------------------------------------------ Helpers
def _v1_sample_document() -> dict:
    return {
        "schema_version": 1,
        "id": "disc-v1-legacy-0001",
        "revision": 2,
        "title": "기존 v1 접촉 수렴 논의",
        "evidences": {},
        "selected_evidence_id": None,
        "turns": [
            {
                "id": "req-legacy-0001",
                "question": "이전 질문입니다.",
                "reply": "이전 답변입니다.",
                "status": "completed",
                "error": None,
                "template_id": "convergence",
                "role": "해석·수렴 전문가",
                "provider_id": "openai_compatible",
                "evidence_id": None,
                "evidence_sources": ["knowledge"],
                "provided_materials": [],
                "constraints": [],
                "citations": {"shown_ids": [], "unverified": [], "verified": []},
                "created_at": "2026-10-01T00:00:00Z",
                "finished_at": "2026-10-01T00:00:05Z",
                "stage": "completed",
            }
        ],
        "constraints": ["최대 요소 크기 2 mm 유지"],
        "failures": [],
        "proposals": [],
        "draft_question": "아직 보내지 않은 초안",
        "pending_request": None,
        "provider_id": None,
    }


def _make_sample_corpus(tmp_path: Path) -> tuple[Path, Path, dict]:
    sources_dir = tmp_path / "corpus_src"
    sources_dir.mkdir(parents=True, exist_ok=True)
    (sources_dir / "LICENSE").write_bytes(b"Synthetic test fixture license notice")

    ccx_manual = (
        "# Hyperelastic Constitutive Theory\n"
        "In compressible hyperelasticity, the strain energy density splits into deviatoric stress\n"
        "response parameterized by Lame parameters mu and bulk modulus kappa with Poisson's ratio.\n"
        "\n"
        "## Drucker-Prager Plasticity and Dilation Angle\n"
        "The Drucker-Prager yield surface uses friction angle, cohesion, trial pressure, and\n"
        "non-associated flow governed by the dilation angle to capture pressure-sensitive behavior.\n"
        "\n"
        "# Viscoelastic Prony Series and TTS\n"
        "Linear viscoelasticity models stress relaxation via a Prony series with equilibrium term\n"
        "and relaxation modulus. In frequency domain, storage modulus and loss modulus follow\n"
        "time-temperature superposition (WLF shift factor) without negative dissipation.\n"
    )
    ccx_file = sources_dir / "calculix_theory.md"
    ccx_bytes = ccx_manual.encode("utf-8")
    ccx_file.write_bytes(ccx_bytes)

    aster_manual = (
        "# Finite Element Weak Form and Boundary Conditions\n"
        "The Neumann boundary condition enters naturally into the weak form variational integral\n"
        "of Poisson equation. Missing Dirichlet constraints lead to zero-energy rigid body modes.\n"
        "First-order hexahedral elements may exhibit shear locking or volumetric locking under\n"
        "incompressible bending.\n"
    )
    aster_file = sources_dir / "code_aster_theory.md"
    aster_bytes = aster_manual.encode("utf-8")
    aster_file.write_bytes(aster_bytes)

    manifest_dict = {
        "schema_version": 1,
        "corpus_id": "cae-theory-test-corpus",
        "revision": "2026.11-r1",
        "sources": [
            {
                "source_id": "calculix-theory",
                "source_kind": "solver_manual",
                "title": "CalculiX Theory Excerpt",
                "edition": "2.22",
                "commit": None,
                "uri": "https://example.invalid/calculix/2.22",
                "local_relative_path": "calculix_theory.md",
                "sha256": hashlib.sha256(ccx_bytes).hexdigest(),
                "byte_length": len(ccx_bytes),
                "license": {
                    "id": "GPL-2.0-or-later",
                    "source_uri": "https://example.invalid/calculix/license",
                    "redistribution": "allowed",
                },
                "solver_scope": ["calculix"],
                "parser": {"id": "markdown-line-v1", "version": "1.0"},
                "extraction_quality": "verified",
            },
            {
                "source_id": "aster-theory",
                "source_kind": "official_manual",
                "title": "Code_Aster FEM Reference Excerpt",
                "edition": "16.5",
                "commit": "abc1234",
                "uri": "https://example.invalid/aster/16.5",
                "local_relative_path": "code_aster_theory.md",
                "sha256": hashlib.sha256(aster_bytes).hexdigest(),
                "byte_length": len(aster_bytes),
                "license": {
                    "id": "CC-BY-4.0",
                    "source_uri": "https://example.invalid/aster/license",
                    "redistribution": "notice_required",
                },
                "solver_scope": ["code_aster"],
                "parser": {"id": "markdown-line-v1", "version": "1.0"},
                "extraction_quality": "verified",
            },
        ],
    }
    manifest_path = sources_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_dict, ensure_ascii=False, indent=2), encoding="utf-8")
    return sources_dir, manifest_path, manifest_dict


class _StubTheoryProvider:
    id = "stub_theory"
    name = "스텁 이론 공급자"
    model = "stub-cae-v1"

    def __init__(self, reply: str | None = None):
        self.prompts: list[str] = []
        self.reply = reply or "핵심 이론 설명입니다. [근거: calculix-manual:convergence] 근거를 확인하세요."

    def availability(self):
        return True, ""

    def discuss(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply

    def cancel(self):
        return None


# ------------------------------------------------------------------ D08 Contracts
def test_d08_contracts_validation_and_role_policies():
    good_hash = "a" * 64
    link = ReassessmentLinkV1(review_id="rev-1", item_id="claim-1", original_context_hash=good_hash)
    assert ReassessmentLinkV1.from_dict(link.to_dict()) == link
    with pytest.raises(ValueError, match="tuple2|mapping"):
        ReassessmentLinkV1.from_dict(("rev-1", "claim-1"))
    with pytest.raises(ValueError):
        ReassessmentLinkV1(review_id="rev-1", item_id="claim-1", original_context_hash="not-sha")

    policy_v1 = build_role_read_policy("interpretation", ("result_summary", "knowledge"), policy_id=ROLE_POLICY_V1_ID)
    policy_v2 = build_role_read_policy("interpretation", ("result_summary", "knowledge"), policy_id=ROLE_POLICY_V2_ID)
    assert "evidence.query_field" not in policy_v1.allowed_query_tools
    assert policy_v1.allowed_result_projections == ()
    assert "evidence.query_field" in policy_v2.allowed_query_tools
    assert policy_v2.allowed_result_projections == ("selected-node-scalar-v1", "selected-node-vector-v1")
    assert policy_v1.policy_sha256 != policy_v2.policy_sha256
    assert RoleReadPolicyV1.from_dict(policy_v2.to_dict()) == policy_v2

    tampered = policy_v2.to_dict()
    tampered["policy_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="mismatch"):
        RoleReadPolicyV1.from_dict(tampered)

    theory_policy = build_role_read_policy("theory", ("knowledge",), policy_id=ROLE_POLICY_V2_ID)
    assert theory_policy.allowed_query_tools == ("knowledge.search",)
    assert theory_policy.allowed_result_projections == ()

    with pytest.raises(ValueError, match="removed_spans"):
        QueryPlan(
            original_text="포아송비 설명",
            original_language="ko",
            queries=(QueryVariant("포아송비 설명", "ko", "original", "cae-glossary-ko-en-v1"),),
            policy_revision="cae-query-ko-en-v1",
            removed_spans=("something",),
        )

    assert map_corpus_source_kind_to_envelope("solver_manual") == "solver_manual"
    assert map_corpus_source_kind_to_envelope("official_manual") == "solver_manual"
    assert map_corpus_source_kind_to_envelope("reviewed_note") == "manual"
    assert map_corpus_source_kind_to_envelope("paper") == "paper"

    # RetrievalDecisionV1 requires strict verification for status="supported"
    req = RetrievalRequirementV1(
        requirement_id="req-1",
        original_span=(0, 4),
        facet="topic",
        expected="Poisson",
        status="verified",
        reason="matched",
    )
    chk = RetrievalCandidateCheckV1(
        candidate_id="chunk-1",
        source_sha256="b" * 64,
        relation="relevant",
        requirement_ids=("req-1",),
        quoted_spans=({"start": 0, "end": 12},),
        reason="exact match",
    )
    ref = ArtifactRefV1(
        id="src-review-1",
        role="source_review",
        path="reviews.json",
        sha256="c" * 64,
        size=32,
        format_id="cae-source-review-v1",
        producer=ProducerIdentity("test", "1.0"),
    )
    with pytest.raises(ValueError, match="source_review_refs"):
        RetrievalDecisionV1(
            assessment_id="assess-1",
            query_plan_sha256="d" * 64,
            candidate_set_sha256="e" * 64,
            source_review_refs=(),
            assessment_profile_id="human-reviewed-v1",
            assessment_profile_sha256="f" * 64,
            status="supported",
            requirements=(req,),
            candidate_checks=(chk,),
            missing_evidence=(),
            limitations=(),
            assessment_trace_ref=None,
        )
    trace = ArtifactRefV1(
        id="assessment-1", role="retrieval_trace", path="trace.json",
        sha256="e" * 64, size=32, format_id="cae-retrieval-assessment-trace-v1",
        producer=ProducerIdentity("test", "1.0"),
    )
    supported = RetrievalDecisionV1(
        assessment_id="assess-1",
        query_plan_sha256="d" * 64,
        candidate_set_sha256="e" * 64,
        source_review_refs=(ref,),
        assessment_profile_id="human-reviewed-v1",
        assessment_profile_sha256="f" * 64,
        status="supported",
        requirements=(req,),
        candidate_checks=(chk,),
        missing_evidence=(),
        limitations=(),
        assessment_trace_ref=trace,
    )
    assert RetrievalDecisionV1.from_dict(supported.to_dict()) == supported


# ------------------------------------------------------------------ D08 Prompt & Discussion v2 + v1 Migration
def test_d08_theory_template_and_prompt_builder():
    theory_tpl = experts.get_template("theory")
    assert theory_tpl.id == "theory"
    assert theory_tpl.allowed_tools == ("knowledge",)
    assert theory_tpl.allowed_evidence_sources == ("knowledge",)

    provider = _StubTheoryProvider()
    disc = experts.Discussion(
        template=theory_tpl,
        summary_text="",
        model_text="",
        log_tail="",
        provider=provider,
        solver_id="",
        native_evidence=None,
        question_scope="theory",
    )
    prompt = disc.build_prompt("CalculiX에서 Prony 급수 점탄성 접촉 수렴 원리를 설명해 주세요.")
    assert "[질문 범위 — 실행 결과 첨부 없는 일반 CAE 이론·문헌 자문]" in prompt
    assert "첨부된 실행·모델·보정 근거가 없습니다" in prompt
    assert "run_id" not in prompt and "frame_index" not in prompt
    assert disc.knowledge_result.solver == "calculix"
    assert disc.evidence_sources == ["knowledge"]

    # case_analysis keeps inferring solver from summary_text rather than question
    model_tpl = experts.get_template("material")
    disc_case = experts.Discussion(
        template=model_tpl,
        summary_text="code_aster 계산 완료",
        model_text="DEFI_MATERIAU",
        log_tail="",
        provider=provider,
        solver_id="",
        question_scope="case_analysis",
    )
    disc_case.build_prompt("CalculiX 자료를 참고해 설명해 주세요.")
    assert disc_case.knowledge_result.solver == "code_aster"


def test_d08_v1_open_no_write_and_first_write_backup(tmp_path: Path, monkeypatch):
    v1_path = tmp_path / "legacy_discussion.json"
    raw_v1_bytes = (json.dumps(_v1_sample_document(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    v1_sha = hashlib.sha256(raw_v1_bytes).hexdigest()
    v1_path.write_bytes(raw_v1_bytes)

    # Opening v1 must NOT modify disk or write backup.
    loaded = discussions.load_discussion(v1_path)
    assert loaded["schema_version"] == 2
    assert loaded["question_scope"] == "case_analysis"
    assert loaded["tool_policy_id"] == ROLE_POLICY_V1_ID
    assert loaded["turns"][0]["schema_version"] == 1
    assert loaded["turns"][0]["capability_limits"]["claim_review_status"] == "not_assessed"
    assert v1_path.read_bytes() == raw_v1_bytes
    assert not (tmp_path / "assets" / "migrations").exists()

    # Corrupt version check
    bad_path = tmp_path / "bad_ver.json"
    bad_doc = _v1_sample_document()
    bad_doc["schema_version"] = 3
    bad_path.write_text(json.dumps(bad_doc), encoding="utf-8")
    with pytest.raises(discussions.DiscussionFormatError):
        discussions.load_discussion(bad_path)

    # Failed v2 write after backup must leave v1 intact on disk
    orig_atomic_json = discussions.atomic_json
    def failing_atomic_json(target, payload):
        if Path(target).resolve() == v1_path.resolve():
            raise OSError("simulated disk write failure")
        return orig_atomic_json(target, payload)

    monkeypatch.setattr(discussions, "atomic_json", failing_atomic_json)
    loaded["draft_question"] = "실패할 수정"
    with pytest.raises(OSError, match="simulated disk write failure"):
        discussions.save_discussion(v1_path, loaded, expected_revision=2)
    assert json.loads(v1_path.read_bytes().decode("utf-8"))["schema_version"] == 1
    assert v1_path.read_bytes() == raw_v1_bytes

    # If corrupted backup already exists at same sha path, migration refuses to overwrite v1
    monkeypatch.setattr(discussions, "atomic_json", orig_atomic_json)
    backup_path = tmp_path / "assets" / "migrations" / f"{v1_sha}.json"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.unlink(missing_ok=True)
    backup_path.write_bytes(b"corrupted backup bytes")
    with pytest.raises(discussions.DiscussionFormatError, match="원본과 일치"):
        discussions.save_discussion(v1_path, loaded, expected_revision=2)
    assert v1_path.read_bytes() == raw_v1_bytes

    # Remove tampered backup and perform legitimate first write -> exact backup + v2 file
    backup_path.unlink()
    loaded["draft_question"] = "정상 저장된 초안"
    saved = discussions.save_discussion(v1_path, loaded, expected_revision=2)
    assert saved["schema_version"] == 2
    assert saved["revision"] == 3
    assert backup_path.read_bytes() == raw_v1_bytes
    assert json.loads(v1_path.read_text(encoding="utf-8"))["schema_version"] == 2


def test_d08_ask_discussion_theory_scope_and_v2_artifacts(tmp_path: Path, monkeypatch):
    disc_path = tmp_path / "theory_chat.json"
    doc = discussions.create_discussion(disc_path, title="이론 자문", question_scope="case_analysis")
    assert doc["schema_version"] == 2
    assert doc["revision"] == 0

    provider = _StubTheoryProvider()
    monkeypatch.setattr(experts, "get_provider", lambda _pid=None: provider)

    # case_analysis requires evidence_id
    with pytest.raises(ValueError, match="근거"):
        discussions.ask_discussion(
            disc_path,
            "근거 없이 케이스 질문",
            evidence_id=None,
            template_id="convergence",
            expected_revision=0,
            question_scope="case_analysis",
        )

    # theory succeeds with evidence_id=None and writes immutable request artifacts + v2 turn
    updated = discussions.ask_discussion(
        disc_path,
        "CalculiX에서 접촉 수렴과 *STATIC 증분 축소 원리를 설명해 주세요.",
        evidence_id=None,
        template_id="theory",
        expected_revision=0,
        question_scope="theory",
    )
    assert updated["question_scope"] == "theory"
    assert updated["revision"] == 2
    assert len(updated["turns"]) == 1
    turn = updated["turns"][0]
    assert turn["schema_version"] == 2
    assert turn["question_scope"] == "theory"
    assert turn["evidence_id"] is None
    assert turn["run_id"] is None
    assert turn["frame_index"] is None
    assert turn["status"] == "completed"
    assert turn["request_context_ref"]["path"].endswith("request_context.json")
    assert turn["tool_trace_ref"]["path"].endswith("trace.json")
    assert turn["evidence_pack_hash"] and len(turn["evidence_pack_hash"]) == 64
    assert turn["retrieval_decisions"] and turn["retrieval_decisions"][0]["schema_version"] == 1
    assert turn["claims"] and any("calculix-manual:convergence" in c["citation_ids"] for c in turn["claims"])
    assert turn["capability_limits"]["claim_review_status"] == "not_assessed"
    assert turn["budget_used"]["total_seconds"] >= 0.0

    asset_root = tmp_path / "assets"
    req_ctx = json.loads((asset_root / turn["request_context_ref"]["path"]).read_text(encoding="utf-8"))
    assert req_ctx["evidence_id"] is None
    assert req_ctx["evidence_sha256"] is None
    assert req_ctx["question_scope"] == "theory"
    assert (asset_root / f"requests/{turn['id']}/events/0001.json").is_file()
    assert (asset_root / f"requests/{turn['id']}/events/0002.json").is_file()
    trace_doc = json.loads((asset_root / turn["tool_trace_ref"]["path"]).read_text(encoding="utf-8"))
    assert trace_doc["tool_call_count"] == 0
    assert trace_doc["status"] == "completed"
    assert trace_doc["request_context_ref"]["sha256"] == turn["request_context_ref"]["sha256"]


# ------------------------------------------------------------------ D08 ExpertWindow GUI
def test_d08_expert_window_theory_mode_gui(tmp_path: Path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QComboBox

    app = QApplication.instance() or QApplication([])
    settings_path = tmp_path / "expert_view.ini"
    view_settings = QSettings(str(settings_path), QSettings.IniFormat)

    provider = _StubTheoryProvider()
    monkeypatch.setattr(experts, "get_provider", lambda _pid=None: provider)

    disc_path = tmp_path / "gui_theory.json"
    wb = Workbench()
    try:
        win = None
        from apps.workbench_qt.expert_window import ExpertWindow

        win = ExpertWindow(disc_path, workbench=wb, view_settings=view_settings)
        win.resize(1100, 780)
        assert win.scope_combo.sizeAdjustPolicy() == QComboBox.SizeAdjustPolicy.AdjustToContents
        assert not win.ask_button.isEnabled()

        win.question.setPlainText("드러커-프라거 팽창각이 수렴에 미치는 영향은?")
        win.scope_combo.setCurrentIndex(win.scope_combo.findData("theory"))
        assert win.question.toPlainText() == "드러커-프라거 팽창각이 수렴에 미치는 영향은?"
        assert win.role.currentData() == "theory"
        assert win.ask_button.isEnabled()
        assert provider.prompts == []

        win.scope_combo.setCurrentIndex(win.scope_combo.findData("case_analysis"))
        assert win.question.toPlainText() == "드러커-프라거 팽창각이 수렴에 미치는 영향은?"
        assert not win.ask_button.isEnabled()

        win.scope_combo.setCurrentIndex(win.scope_combo.findData("theory"))
        win.ask()
        for _ in range(300):
            app.processEvents()
            win._poll()
            if not win._operation and win.document.get("turns") and win.document["turns"][0].get("status") == "completed":
                break
            time.sleep(0.01)
        assert win.document["turns"], f"Expected completed turn; status={win.status.text()}"
        assert win.document["turns"][0]["question_scope"] == "theory"
        html_out = win.transcript.toHtml()
        assert "이론·문헌 자문 (첨부 없음)" in html_out
        assert "입력 출처 미확인" not in html_out
        assert "단위 미확인" not in html_out
        win.close()

        # Reopening persisted theory discussion keeps theory scope without invoking provider
        calls_before = len(provider.prompts)
        reopened = ExpertWindow(disc_path, workbench=wb, view_settings=view_settings)
        assert reopened.scope_combo.currentData() == "theory"
        assert reopened.role.currentData() == "theory"
        assert reopened.ask_button.isEnabled()
        assert len(provider.prompts) == calls_before
        reopened.close()
    finally:
        wb.close()


def test_d08_theory_can_send_with_unrelated_missing_attachment_and_reports_request_tampering(
    tmp_path, monkeypatch,
):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from apps.workbench_qt.expert_window import ExpertWindow

    app = QApplication.instance() or QApplication([])
    provider = _StubTheoryProvider()
    monkeypatch.setattr(experts, "get_provider", lambda _pid=None: provider)
    path = tmp_path / "discussion.json"
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat)
    window = ExpertWindow(path, view_settings=settings)
    try:
        source = tmp_path / "input.inp"
        source.write_text("*HEADING\nPrivate attached case", encoding="utf-8")
        evidence = window.workbench.import_expert_files(window.assets, files={"input": source}, solver_id="calculix")
        window.document = discussions.attach_evidence(path, evidence, expected_revision=window.document["revision"])
        (window.assets / evidence["files"][0]["stored_path"]).unlink()
        window.role.setCurrentIndex(window.role.findData("theory"))
        assert "실행 근거를 읽는 역할" in window.basis.text()
        assert not window.ask_button.isEnabled()
        window.scope_combo.setCurrentIndex(window.scope_combo.findData("theory"))
        assert "첨부 자료를 모델에 전달하지 않습니다" in window.basis.text()
        window._start_asset_verification()
        assert window.ask_button.isEnabled()
        window.question.setPlainText("What is a weak form?")
        window.ask()
        deadline = time.monotonic() + 5
        while window._operation and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert window.document["turns"][-1]["status"] == "completed"
        assert "Private attached case" not in provider.prompts[-1]
        turn = window.document["turns"][-1]
        (window.assets / turn["request_context_ref"]["path"]).write_bytes(b"tampered")
        window.reload_document()
        window.wait_for_asset_verification()
        assert "보존된 요청 문맥" in window.asset_warning.text()
        assert "request_context_ref" in window.asset_warning.toolTip()
        assert window.ask_button.isEnabled()
    finally:
        window.close()


# ------------------------------------------------------------------ D09 QueryPlan
def test_d09_query_plan_ko_en_expansion_and_warnings():
    # NFKC applied only to search variants; original_text verbatim; removed_spans == ()
    original = "ＡＢＡＱＵＳ에서 포아송비 0.3과 노이만 경계조건, 강체 모드, 라메 상수, 편차응력이 아닌 조건은?"
    plan = query_language.prepare_query(original)
    assert plan.original_text == original
    assert plan.removed_spans == ()
    assert plan.original_language == "ko"
    assert plan.queries[0].text == original
    mixed_plan = query_language.prepare_query("ABAQUS에서 Poisson ratio와 rigid body mode 설명해 주세요.")
    assert mixed_plan.original_language == "mixed"
    nfkc_texts = [v.text for v in plan.queries if v.method == "nfkc"]
    assert any("ABAQUS" in t for t in nfkc_texts)
    expanded_text = " ".join(v.text for v in plan.queries)
    for expected in ("Poisson", "Neumann boundary condition", "rigid body", "Lame", "deviatoric stress", "0.3"):
        assert expected in expanded_text

    # Viscoelastic + Drucker-Prager + hardening + contact terms
    q2 = "초탄성과 드러커-프라거 팽창각, 프로니 급수 이완, 저장 탄성계수, 손실 탄성계수, 시간-온도 중첩, 경화, 반력, 수렴, 접촉, 잠금, 변형률 비교"
    plan2 = query_language.prepare_query(q2)
    text2 = " ".join(v.text for v in plan2.queries)
    for expected in (
        "hyperelastic",
        "Drucker-Prager",
        "dilation angle",
        "Prony series",
        "relaxation",
        "storage modulus",
        "loss modulus",
        "time-temperature superposition",
        "hardening",
        "reaction force",
        "convergence",
        "contact",
        "locking",
        "strain",
    ):
        assert expected in text2

    # Unsupported language detection (Japanese / French)
    jp_plan = query_language.prepare_query("ポアソン比とノイマン境界条件の説明")
    assert jp_plan.original_language == "unsupported"
    assert "QUERY_LANGUAGE_UNSUPPORTED" in jp_plan.warnings

    unmapped_ko = query_language.prepare_query("오늘 초전도 자기부상 열차 해석 방법")
    assert "LANGUAGE_COVERAGE_UNVERIFIED" in unmapped_ko.warnings


# ------------------------------------------------------------------ D09 Offline Corpus, CLI, Hybrid Retrieval & Decision
def test_d09_corpus_verify_build_reuse_cancel_and_cli(tmp_path: Path, monkeypatch):
    sources_dir, manifest_path, manifest_dict = _make_sample_corpus(tmp_path)
    out_root = tmp_path / "corpus_out"

    # --verify-only writes nothing
    receipt_verify = corpus.build_corpus(manifest_path, output_root=out_root, verify_only=True)
    assert receipt_verify.verify_only is True
    assert receipt_verify.status == "verified_with_limitations"
    assert receipt_verify.profile_id == CORPUS_PROFILE_ID
    assert not (out_root / "indexes").exists()
    assert not (out_root / "current.json").exists()

    # Build atomic index
    receipt_build = corpus.build_corpus(manifest_path, output_root=out_root, verify_only=False)
    assert receipt_build.status == "built"
    assert receipt_build.chunk_count >= 3
    idx_dir = Path(receipt_build.index_dir)
    assert idx_dir.is_dir()
    assert (out_root / "current.json").is_file()
    chunks_doc = json.loads((idx_dir / "chunks.json").read_text(encoding="utf-8"))
    assert chunks_doc["bm25"] == {"k1": 1.5, "b": 0.75}
    assert chunks_doc["rrf_k"] == 60
    assert chunks_doc["vector_dim"] == 512
    assert chunks_doc["vector_dtype"] == "float32"
    staged_manifest = (idx_dir / "manifest.json").read_bytes()
    assert receipt_build.manifest_sha256 == hashlib.sha256(staged_manifest).hexdigest()
    assert receipt_build.source_statuses["aster-theory"] == "unreviewed"
    assert (idx_dir / "licenses" / "LICENSE").read_bytes() == (sources_dir / "LICENSE").read_bytes()

    # Verify 1-based line and exact 0-based char spans reconstruct source slices identically
    src_texts = {
        "calculix-theory": (sources_dir / "calculix_theory.md").read_text(encoding="utf-8"),
        "aster-theory": (sources_dir / "code_aster_theory.md").read_text(encoding="utf-8"),
    }
    for ch in chunks_doc["chunks"]:
        full = src_texts[ch["source_id"]]
        assert ch["line_start"] >= 1
        assert ch["line_end"] >= ch["line_start"]
        assert full[ch["char_start"]:ch["char_end"]] == ch["text"]

    vecs = np.load(idx_dir / "vectors.npy", allow_pickle=False)
    assert vecs.dtype == np.float32
    assert vecs.shape == (len(chunks_doc["chunks"]), 512)

    # Second identical build reuses existing digest
    receipt_reused = corpus.build_corpus(manifest_path, output_root=out_root, verify_only=False)
    assert receipt_reused.status == "reused"
    assert receipt_reused.index_digest == receipt_build.index_digest

    # Cancellation during build cleans only its own tmpdir and keeps current.json untouched
    prior_pointer = (out_root / "current.json").read_bytes()
    sibling_staging = out_root / "indexes" / ".tmp-corpus-unrelated"
    sibling_staging.mkdir()
    sentinel = sibling_staging / "user_note.txt"
    sentinel.write_text("do not delete", encoding="utf-8")
    chunk_write = []
    write = corpus._write_bytes_fsync

    def capture_staged_write(path, data):
        write(path, data)
        if path.name == "chunks.json":
            chunk_write.append(path)
            assert path.is_file() and (path.parent / "manifest.json").is_file()

    with monkeypatch.context() as patch:
        patch.setattr(corpus, "_write_bytes_fsync", capture_staged_write)
        with pytest.raises(RuntimeError, match="cancelled|중단"):
            corpus.build_corpus(manifest_path, output_root=out_root, check_cancelled=lambda: bool(chunk_write))
    assert (out_root / "current.json").read_bytes() == prior_pointer
    assert sentinel.read_text(encoding="utf-8") == "do not delete"
    assert chunk_write and not chunk_write[0].parent.exists()
    assert idx_dir.is_dir()

    # CLI verification via subprocess (--verify-only and --build)
    cli_out = tmp_path / "cli_out"
    env = {key: value for key, value in os.environ.items() if key.upper() != "PYTHONPATH"}
    res_v = subprocess.run(
        [sys.executable, "scripts/build_knowledge_index.py", "--manifest", str(manifest_path), "--output", str(cli_out), "--verify-only"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(res_v.stdout)["status"] == "verified_with_limitations"
    assert not (cli_out / "current.json").exists()

    res_b = subprocess.run(
        [sys.executable, "scripts/build_knowledge_index.py", "--manifest", str(manifest_path), "--output", str(cli_out), "--build"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(res_b.stdout)["status"] == "built"
    assert (cli_out / "current.json").is_file()

    # Mismatched source SHA fails verification
    bad_manifest = dict(manifest_dict)
    bad_manifest["sources"] = [dict(manifest_dict["sources"][0], sha256="0" * 64)]
    bad_manifest_path = sources_dir / "bad_manifest.json"
    bad_manifest_path.write_text(json.dumps(bad_manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA256|sha256"):
        corpus.build_corpus(bad_manifest_path, output_root=out_root, verify_only=True)


def test_d09_cross_lingual_search_and_retrieval_decisions(tmp_path: Path, monkeypatch):
    _, manifest_path, _ = _make_sample_corpus(tmp_path)
    out_root = tmp_path / "corpus_idx"
    receipt = corpus.build_corpus(manifest_path, output_root=out_root)

    # Bilingual Korean queries retrieve corresponding English corpus chunks
    plan_hp, hits_hp, dec_hp = corpus.search_corpus(
        out_root,
        "초탄성 편차응력과 라메 상수, 포아송비 관계가 어떻게 되나요?",
        solver="calculix",
    )
    assert plan_hp.original_language == "mixed" or plan_hp.original_language == "ko"
    assert hits_hp and "deviatoric stress" in hits_hp[0].text
    assert dec_hp.status == "insufficient"  # unconfigured semantic assessment cannot claim supported

    _, hits_dp, _ = corpus.search_corpus(
        out_root,
        "드러커-프라거 모델에서 팽창각과 마찰각 차이를 설명해 주세요.",
        solver="calculix",
    )
    assert hits_dp and "Drucker-Prager" in hits_dp[0].text and "dilation angle" in hits_dp[0].text

    _, hits_prony, _ = corpus.search_corpus(
        out_root,
        "프로니 급수 이완 탄성계수 및 시간-온도 중첩 저장 탄성계수 손실 탄성계수",
        solver="calculix",
    )
    assert hits_prony and "Prony series" in hits_prony[0].text and "storage modulus" in hits_prony[0].text

    _, hits_aster, _ = corpus.search_corpus(
        out_root,
        "노이만 경계조건 약형과 강체 모드, 전단 잠금 현상",
        solver="code_aster",
    )
    assert hits_aster and "Neumann boundary condition" in hits_aster[0].text and "rigid body" in hits_aster[0].text

    # Non-CAE question explicitly marked out_of_scope (distinct from insufficient solver coverage)
    _, hits_oos, dec_oos = corpus.search_corpus(out_root, "오늘 저녁 김치찌개 레시피 알려줘")
    assert dec_oos.status == "out_of_scope"
    assert "NON_CAE_REQUEST" in dec_oos.missing_evidence

    # Unknown solver edition explicitly marked insufficient (not out_of_scope)
    _, _, dec_insuff = corpus.search_corpus(
        out_root,
        "LS-DYNA 2026에서 *MAT_999 카드의 포아송비를 설명해 주세요.",
        solver="lsdyna",
        edition="2026",
    )
    assert dec_insuff.status == "insufficient"
    assert "SOLVER_MISMATCH" in dec_insuff.missing_evidence
    assert "NO_CANDIDATES" not in dec_insuff.missing_evidence

    # Wire through knowledge.search_knowledge_result via CAELAB_CORPUS_INDEX_DIR
    monkeypatch.setenv("CAELAB_CORPUS_INDEX_DIR", str(out_root))
    knowledge._index_cache.clear()
    try:
        kres = knowledge.search_knowledge_result(
            "드러커-프라거 팽창각과 시행 압력",
            solver="calculix",
            limit=3,
        )
        assert kres.status == "ok"
        assert kres.query_plan is not None
        assert kres.corpus_revision == receipt.revision
        assert any("Drucker-Prager" in entry.text or "dilation angle" in entry.text for entry in kres.entries)
    finally:
        monkeypatch.delenv("CAELAB_CORPUS_INDEX_DIR", raising=False)
        knowledge._index_cache.clear()


def test_theory_prompt_has_only_eligible_history_and_never_reads_native_fields():
    prior = {"status": "completed", "question_scope": "theory", "evidence_sources": ["knowledge"]}
    conversation = experts.Discussion(
        template=experts.get_template("theory"),
        question_scope="theory",
        provider=_StubTheoryProvider(),
        summary_text="PRIVATE_RESULT",
        log_tail="PRIVATE_LOG",
        native_evidence={"run_id": "PRIVATE_RUN", "units": {"length": "PRIVATE_UNITS"}},
        history=[
            dict(prior, question="allowed history"),
            dict(prior, question="PRIVATE_EMPTY", evidence_sources=[]),
            dict(prior, question="PRIVATE_UNRECORDED", evidence_sources=None),
            dict(prior, question="PRIVATE_FAILED", status="failed"),
            dict(prior, question="PRIVATE_PENDING", status="pending"),
            dict(prior, question="PRIVATE_BAD_SOURCE", evidence_sources=["result_summary"]),
            dict(prior, question_scope="case_analysis", question="PRIVATE_CASE", evidence_name="PRIVATE_NAME"),
            dict(prior, question="safe metadata omitted", run_id="PRIVATE_META", frame_index=9876),
        ],
        failures=[dict(prior, reason="PRIVATE_FAILURE")],
    )
    prompt = conversation.build_prompt("What is the weak form?")
    assert "[근거 — 결과 요약]" not in prompt and "[현재 발언의 출처]" not in prompt
    assert "result_summary" not in conversation.evidence_sources
    assert "allowed history" in prompt and "safe metadata omitted" in prompt
    assert "PRIVATE_" not in prompt and "9876" not in prompt


def test_theory_scope_guards_and_prompt_only_transport(tmp_path, monkeypatch):
    path = tmp_path / "discussion.json"
    doc = discussions.create_discussion(path, question_scope="theory")
    provider = _StubTheoryProvider()
    provider.id = "openscience"
    monkeypatch.setattr(experts, "get_provider", lambda _pid=None: provider)
    ask = discussions.ask_discussion
    with pytest.raises(ValueError, match="case_analysis"):
        ask(path, "result", template_id="theory", question_scope="case_analysis", expected_revision=doc["revision"])
    with pytest.raises(ValueError, match="case_analysis"):
        ask(path, "result", template_id="theory", evidence_id="old", expected_revision=doc["revision"])
    with pytest.raises(ValueError, match="theory"):
        ask(path, "theory", template_id="material", evidence_id="old",
            question_scope="theory", expected_revision=doc["revision"])
    with pytest.raises(ValueError, match="근거"):
        ask(path, "result", template_id="material", expected_revision=doc["revision"])
    assert not provider.prompts
    updated = ask(path, "What is the weak form?", template_id="theory", expected_revision=doc["revision"])
    turn = updated["turns"][-1]
    assert turn["status"] == "completed" and turn["question_scope"] == "theory"
    assert turn["provider_capabilities"]["transport_mode"] == "prompt_only"
    assert not discussions.discussion_issues(path)
    (path.parent / "assets" / turn["request_context_ref"]["path"]).unlink()
    (path.parent / "assets" / turn["tool_trace_ref"]["path"]).write_bytes(b"tampered")
    issues = discussions.discussion_issues(path)
    assert any("request_context_ref" in issue for issue in issues)
    assert any("tool_trace_ref" in issue and "SHA256" in issue for issue in issues)


def test_legacy_draft_conflicts_on_external_v1_edit_at_same_revision(tmp_path):
    path = tmp_path / "discussion.json"
    original = _v1_sample_document()
    path.write_bytes(canonical_json_bytes(original))
    stale_draft = discussions.load_discussion(path)
    changed = canonical_json_bytes(dict(original, draft_question="External same-revision update"))
    path.write_bytes(changed)
    stale_draft["draft_question"] = "Local unsaved draft"
    with pytest.raises(discussions.DiscussionConflict, match="v1"):
        discussions.save_discussion(path, stale_draft, expected_revision=original["revision"])
    assert path.read_bytes() == changed
    assert not (tmp_path / "assets" / "migrations").exists()


@pytest.mark.parametrize("action", ["open_document", "new_document", "close"])
def test_leaving_untouched_v1_keeps_its_exact_bytes(tmp_path, monkeypatch, action):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QFileDialog
    from apps.workbench_qt.expert_window import ExpertWindow

    app = QApplication.instance() or QApplication([])
    old_path = tmp_path / "old.json"
    original = canonical_json_bytes(_v1_sample_document())
    old_path.write_bytes(original)
    next_path = tmp_path / "next.json"
    if action == "open_document":
        discussions.create_discussion(next_path)
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(next_path), ""))
    if action == "new_document":
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(next_path), ""))
    window = ExpertWindow(old_path, view_settings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    try:
        getattr(window, action)()
        if action != "close":
            assert window.path == next_path
        assert old_path.read_bytes() == original
        assert not (tmp_path / "assets" / "migrations").exists()
    finally:
        window.close()
        app.processEvents()


def test_d09_protected_operands_unicode_versions_and_non_cae():
    import unicodedata

    protect = query_language._protected_ranges
    text = "How does *FRICTION affect contact pair? CalculiX *NODE PRINT reaction force"
    assert [text[start:end] for start, end in protect(text)] == ["*FRICTION", "*NODE PRINT"]
    text = "CalculiX *NODE PRINT reaction force 출력"
    assert [text[start:end] for start, end in protect(text)] == ["*NODE PRINT"]
    text = "Poisson's ratio 0.3이 3D 2D 1D rad/s"
    assert [text[start:end] for start, end in protect(text)] == ["0.3", "3D", "2D", "1D", "rad/s"]
    assert not protect("Poisson’s ratio")
    assert not query_language.prepare_query("0.3이").warnings
    nfd = unicodedata.normalize("NFD", "응력")
    plan = query_language.prepare_query(nfd)
    assert plan.original_text == nfd
    assert plan.term_spans[0]["source_text"] == nfd
    assert plan.term_spans[0]["matched_term"] == "응력"
    condition = "Can this run without softening if J <= 0?"
    plan = query_language.prepare_query(condition)
    assert all("without softening" in entry.text and "if J <= 0" in entry.text for entry in plan.queries)
    for version in ("2.23", "v2.23", "0.10.0"):
        question = f"CalculiX {version}에서 약형은?"
        requirements = query_language.extract_query_requirements(query_language.prepare_query(question))
        assert next(req.expected for req in requirements if req.facet == "edition") == version
    assert query_language.editions_match("2.23", "v2.23")
    assert not query_language.editions_match("2.2", "2.23")
    weather = query_language.prepare_query("What's the weather tomorrow?")
    assert query_language.build_retrieval_decision(weather, [], explicit_facets={"solver": "general"}).status == "out_of_scope"
    assert not query_language.classify_non_cae_intent(query_language.prepare_query("Explain Johnson-Cook."))[0]
    special = "Explain the Johnson‑Cook model."
    normalized = query_language.prepare_query(special)
    assert normalized.original_text == special
    assert any("Johnson-Cook" in q.text for q in normalized.queries)
    assert not query_language.classify_non_cae_intent(normalized)[0]
    assert rag.tokenize("Poisson’s ratio and stress's value") == ["poisson", "ratio", "and", "stress", "value"]
    words = "*CARD NEXT *PLASTIC FOLLOWING *SOLID SECTION *CONTACT PAIR *NODE PRINT"
    assert [words[start:end] for start, end in protect(words)] == [
        "*CARD", "*PLASTIC", "*SOLID SECTION", "*CONTACT PAIR", "*NODE PRINT",
    ]
    for quantity in ("10.5 MPa", "200 GPa", "100 kPa", "50 Hz", "293.15 K", "10 mm", "0.5 s", "5 N"):
        plan = query_language.prepare_query(f"CalculiX {quantity} 하중의 의미는?")
        assert not any(req.facet == "edition" for req in query_language.extract_query_requirements(plan))
    for prompt, version in (("OpenRadioss 2024 매뉴얼", "2024"), ("ver. 2.20 field output", "2.20")):
        reqs = query_language.extract_query_requirements(query_language.prepare_query(prompt))
        assert next(req.expected for req in reqs if req.facet == "edition") == version


def test_d09_full_frozen_evaluation_against_bm25_baseline(tmp_path):
    evidence = Path(__file__).resolve().parents[1] / "docs" / "specs" / "cae-research" / "evidence" / "C"
    manifest, _ = corpus.load_corpus_manifest(evidence / "corpus-source-bundle.json")
    assert len(manifest.sources) == 8
    receipt = corpus.build_corpus(evidence / "corpus-source-bundle.json", output_root=tmp_path)
    assert receipt.chunk_count == 16
    assert set(receipt.source_statuses.values()) == {"unreviewed"}
    index_dir = Path(receipt.index_dir)
    assert (index_dir / "licenses" / "geos-LICENSE" / "LICENSE").is_file()
    assert (index_dir / "licenses" / "geos-NOTICE" / "NOTICE").is_file()
    index, _ = corpus.load_corpus_bundle(tmp_path)
    labels = json.loads((evidence / "evaluation-question-bundle.json").read_bytes())
    questions = labels["evaluation"]["questions"]
    expected_gaps = {
        25: "missing_card_or_parameter", 26: "missing_card_or_parameter",
        27: "missing_model_variant_or_original_source", 28: "missing_solver",
        29: "missing_identification_data", 30: "missing_safety_evidence",
        31: "missing_card_or_parameter", 32: "missing_card_or_parameter",
        33: "missing_material_test_data", 34: "missing_model_variant_or_original_source",
        35: "missing_temperature_dependent_data", 36: "unsupported_coupling",
        37: "missing_restart_validation", 38: "missing_buckling_model_or_result",
        39: "missing_result_measure", 40: "missing_mesher_coverage",
        41: "edition_mismatch", 42: "missing_run_evidence",
    }
    hybrid_hits = bm25_hits = 0
    counts = {"supported": 0, "insufficient": 0, "out_of_scope": 0}
    for row in questions:
        expected = row["expected_retrieval"]
        counts[expected] += 1
        facets = row["explicit_facets"]
        _, hits, decision = corpus.search_corpus(
            tmp_path, row["question"], explicit_facets=facets if expected == "supported" else None,
        )
        if expected == "supported":
            expected_ids = set(row["expected_chunk_ids"])
            hybrid_hits += bool(expected_ids & {hit.chunk_id for hit in hits})
            scores = index.bm25.scores(rag.tokenize(row["question"]))
            solver = facets["solver"]
            baseline = sorted(
                (i for i, chunk in enumerate(index.chunks)
                 if scores[i] > 0 and (solver == "general" or chunk.matches_solver(solver))),
                key=lambda i: (-scores[i], i),
            )[:5]
            bm25_hits += bool(expected_ids & {index.chunks[i].chunk_id for i in baseline})
        else:
            assert decision.status == expected, row["id"]
            if expected == "out_of_scope":
                assert not hits and not decision.candidate_checks, row["id"]
            else:
                number = int(row["id"].rsplit("-", 1)[1])
                assert expected_gaps[number] in decision.missing_evidence, (row["id"], decision.missing_evidence)
    assert counts == {"supported": 24, "insufficient": 18, "out_of_scope": 18}
    assert hybrid_hits >= 22 and hybrid_hits >= bm25_hits, (hybrid_hits, bm25_hits)


@pytest.fixture
def assessed_retrieval(tmp_path):
    text = "Drucker-Prager yield condition"
    digest = hashlib.sha256(text.encode()).hexdigest()
    reviewed_sha = "a" * 64
    producer = ProducerIdentity("pytest", "1")
    review = ArtifactRefV1(
        id="review-1", role="source_review", path="reviews/approved.json",
        sha256="e" * 64, size=12, format_id="cae-source-review-v1", producer=producer,
    )
    candidate = {
        "citation": "geos-manual:chapter-1", "source_id": "geos-manual",
        "document_sha256": reviewed_sha, "edition": "v2.23",
        "line_start": 12, "line_end": 12, "char_start": 80, "char_end": 80 + len(text),
        "text": text, "text_sha256": digest, "solver_scope": ["geos", "general"],
    }
    plan = query_language.prepare_query("GEOS v2.23 Drucker-Prager yield")

    def assess(target, *, approved=True, include_good=False, trace_role="retrieval_trace",
               candidate_scopes=None, facets=None, out_of_scope=None, drop=(), invalid_quote=None,
               trace_ids=None, assessment_ids=None, gaps=(), question_plan=plan,
               response_format=None, trace_outcome=None, trace_format="cae-retrieval-trace-v1", trace_schema="caelab.retrieval_trace.v1",
               trace_events=False, host_overrides=None, trace_overrides=None, companion=None, grouped=False,
               recorded_review=None, trace_serializer=canonical_json_bytes):
        candidates = [companion or candidate, target] if include_good else [target]
        probe = query_language.build_retrieval_decision(
            question_plan, candidates, explicit_facets=facets, candidate_solver_scopes=candidate_scopes,
        )
        identity = {key: getattr(probe, key) for key in ("assessment_id", "query_plan_sha256", "candidate_set_sha256")}
        ids = [item["citation"] for item in candidates]
        trace_payload = {"type": "retrieval_assessment", "schema_version": 1, **identity,
                         "candidate_ids": ids if trace_ids is None else trace_ids,
                         "events": [{"kind": "retrieval_completion", "status": "completed"}]}
        if trace_outcome is not None:
            trace_payload["decision"] = trace_outcome
        trace_payload["schema"] = trace_schema
        if trace_events:
            trace_payload = {"schema": trace_schema, "events": [
                {"kind": "retrieval", **{key: trace_payload[key] for key in (*identity, "candidate_ids")}},
                {"stage": "terminal", "status": trace_outcome},
            ]}
        trace_payload.update(trace_overrides or {})
        if recorded_review is not None:
            quote_span, relation = recorded_review
            trace_payload["response"] = {
                "checks": [{"requirement_id": req.requirement_id, "candidate_ids": [quote_span["candidate_id"]],
                            "relation": relation, "quoted_spans": [quote_span], "reason": "recorded review"}
                           for req in probe.requirements],
            }
        raw = trace_serializer(trace_payload)
        (tmp_path / "trace.json").write_bytes(raw)
        trace = ArtifactRefV1(
            id="trace", role=trace_role or "retrieval_trace", path="trace.json",
            sha256=hashlib.sha256(raw).hexdigest(), size=len(raw), format_id=trace_format,
            producer=producer,
        )
        quotes = [{"candidate_id": target["citation"], "start": 0, "end": len(text)}]
        result = {
            "type": "retrieval_assessment", **identity,
            "candidate_ids": ids if assessment_ids is None else assessment_ids, "trace_ref": trace.to_dict(),
            "scope_status": "in_scope", "decision": "supported", "insufficiency_reasons": list(gaps),
            "citations": quotes,
            "checks": [
                {"requirement_id": req.requirement_id, "candidate_ids": [target["citation"]],
                 "relation": "satisfied", "quoted_spans": quotes, "reason": "reviewed original text"}
                for req in probe.requirements
            ],
            "out_of_scope": out_of_scope or {"is_non_cae": False, "original_spans": [], "reason": ""},
            "limitations": [],
        }
        if invalid_quote is not None:
            result["checks"][0]["quoted_spans"] = [*quotes, invalid_quote]
        if grouped:
            flat = [{"candidate_id": item["citation"], "start": 0, "end": len(text)} for item in candidates]
            checks = []
            for req in probe.requirements:
                if req.facet == "solver":
                    checked = [q for q, item in zip(flat, candidates)
                               if query_language.normalize_solver(req.expected) in item["solver_scope"]]
                elif req.facet == "edition":
                    checked = [q for q, item in zip(flat, candidates)
                               if item["edition"].removeprefix("v") == req.expected.removeprefix("v")]
                else:
                    checked = flat
                checks.append({"requirement_id": req.requirement_id,
                               "candidate_ids": [q["candidate_id"] for q in checked],
                               "relation": "satisfied" if checked else "missing",
                               "quoted_spans": checked, "reason": "reviewed original text"})
            result.update(checks=checks, citations=flat)
        if response_format == "frozen":
            result = {key: result[key] for key in ("type", "checks", "out_of_scope", "limitations")}
        elif response_format == "hybrid":
            result = {"candidate_ids": ids, "citations": ids, "scope_status": "in_scope",
                      "quotes": quotes, "requirement_checks": result["checks"]}
        result.update(host_overrides or {})
        for field in drop:
            result.pop(field)
        return query_language.build_retrieval_decision(
            question_plan, candidates, source_review_refs=[review],
            approved_source_reviews={reviewed_sha: review} if approved else {},
            assessment_profile_sha256="f" * 64, assessment_result=result,
            assessment_trace_ref=trace if trace_role else None, assessment_trace_root=tmp_path,
            explicit_facets=facets, candidate_solver_scopes=candidate_scopes,
        )

    return assess, candidate


def test_d09_assessment_cannot_promote_wrong_source_edition_review_or_span(assessed_retrieval):
    assess, candidate = assessed_retrieval
    text = candidate["text"]
    plan = query_language.prepare_query("GEOS v2.23 Drucker-Prager yield")
    with pytest.raises(ValueError, match="assessment_trace_ref"):
        assess(candidate, trace_role=None)
    with pytest.raises(ValueError, match="retrieval trace artifact"):
        assess(candidate, trace_role="unrelated_trace")
    for key in ("assessment_id", "query_plan_sha256", "candidate_set_sha256", "trace_ref",
                "candidate_ids", "citations", "scope_status", "decision", "checks"):
        with pytest.raises(ValueError, match="fields"):
            assess(candidate, drop=(key,))
    with pytest.raises(ValueError, match="candidate_ids"):
        assess(candidate, trace_ids=["unrelated"])
    with pytest.raises(ValueError, match="candidate_ids"):
        assess(candidate, assessment_ids=[])
    for invalid in (
        {"candidate_id": candidate["citation"], "start": False, "end": 3},
        {"candidate_id": candidate["citation"], "start": 0, "end": len(text) + 1},
        {"candidate_id": candidate["citation"], "start": 0, "end": 3, "text": "not quoted"},
        {"candidate_id": candidate["citation"], "start": 0, "end": 3, "page": True},
    ):
        with pytest.raises(ValueError, match="citation or quotation"):
            assess(candidate, invalid_quote=invalid)
    bad_span = {"is_non_cae": True, "original_spans": [{"start": 0, "end": len(plan.original_text) + 1}], "reason": "invalid"}
    with pytest.raises(ValueError, match="original span"):
        assess(candidate, out_of_scope=bad_span)
    assert assess(candidate).status == "supported"
    assert assess(candidate, response_format="frozen").status == "supported"
    with pytest.raises(ValueError, match="fields"):
        assess(candidate, response_format="hybrid")
    with pytest.raises(ValueError, match="identity"):
        assess(candidate, host_overrides={"query_plan_sha256": "0" * 64})
    for schema, format_id in (("unrelated", "cae-retrieval-trace-v1"),
                              ("caelab.retrieval_trace.v1", "other-format")):
        with pytest.raises(ValueError, match="trace format"):
            assess(candidate, trace_schema=schema, trace_format=format_id, trace_events=True)
    with pytest.raises(ValueError, match="fields"):
        assess(candidate, response_format="frozen", drop=("checks",))
    for trace_outcome in ("insufficient", "out_of_scope", "cancelled", "timed_out", "failed"):
        assert assess(candidate, trace_outcome=trace_outcome).status == "insufficient"
        assert assess(candidate, trace_outcome=trace_outcome, trace_events=True).status == "insufficient"
    for response in ({"decision": {"status": "insufficient"}}, {"requirement_checks": []}):
        assert assess(candidate, trace_overrides={"response": response}).status == "insufficient"
    for response in ({"assessment": {"requirement_checks": [{"relation": "missing"}]}},
                     {"requirement_checks": [{"status": "uncertain"}]}):
        with pytest.raises(ValueError, match="check fields"):
            assess(candidate, trace_overrides={"response": response})
    for event in ({"kind": "solver_step"}, {"status": "completed"}):
        with pytest.raises(ValueError, match="trace events"):
            assess(candidate, trace_overrides={"events": [event]})
    for events in ([{"stage": "search"}], [{"kind": "query_plan"}],
                   [{"stage": "search"}, {"stage": "retrieval_completion"}],
                   [{"stage": "prepare_query"}, {"stage": "candidate_selection"}]):
        with pytest.raises(ValueError, match="trace events"):
            assess(candidate, trace_overrides={"events": events})
    assert assess(candidate, trace_overrides={"events": [
        {"kind": "prepare_query"}, {"stage": "search"}, {"stage": "assessment"},
    ]}).status == "supported"
    for failure in ({"status": "error"}, {"error": "backend failed"}, {"errors": ["backend failed"]},
                    {"insufficiency_reasons": ["review unavailable"]}, {"stage": "no_candidates"}):
        assert assess(candidate, trace_overrides={"events": [
            {"stage": "prepare_query"}, {"stage": "terminal", **failure},
        ]}).status == "insufficient"
    for citation in ("unknown:chunk", {"candidate_id": "unknown:chunk"}):
        with pytest.raises(ValueError, match="unknown cited candidate"):
            assess(candidate, trace_overrides={"response": {"citations": [citation]}})
    provider = {"provider_id": "endpoint", "model": "test", "dimensions": 384, "Status": "ready"}
    recorded_stages = RetrievalTraceRecord(
        embedding_provider=provider, stage_events=["prepare_query", {"stage": "retrieval_completion"}],
        outcome="candidates_selected", selected_chunk_ids=[candidate["citation"]],
    ).to_dict()
    assert assess(candidate, trace_overrides=recorded_stages).status == "supported"
    with pytest.raises(ValueError, match="outcome conflicts"):
        assess(candidate, trace_overrides={**recorded_stages, "selected_chunk_ids": []})
    for outcome, stage, ids in (
        ("candidates_selected", "no_candidates", [candidate["citation"]]),
        ("insufficient", "no_candidates", [candidate["citation"]]),
        ("out_of_scope", "candidates_selected", [candidate["citation"]]),
        ("supported", "error", [candidate["citation"]]),
    ):
        with pytest.raises(ValueError, match="outcome conflicts"):
            RetrievalTraceRecord(provider, ["prepare_query", stage], outcome, ids)
    RetrievalTraceRecord(provider, ["prepare_query", "no_candidates"], "insufficient", [])
    with pytest.raises(ValueError, match="stage_events"):
        assess(candidate, trace_overrides={**recorded_stages, "stage_events": ["prepare_query", "solver_step"]})
    assert assess(candidate, trace_overrides={
        **recorded_stages, "stage_events": ["prepare_query", "cancelled"], "outcome": "cancelled",
    }).status == "insufficient"
    for invalid in (dict(dimensions=True), dict(Status="unknown"), dict(extra=True)):
        with pytest.raises(ValueError, match="embedding.provider"):
            assess(candidate, trace_overrides={"response": {"embedding_provider": {**provider, **invalid}}})
    assert assess(candidate, trace_overrides={"selected_chunk_ids": []}).status == "insufficient"
    for field in ("events", "stage_events"):
        with pytest.raises(ValueError, match="events"):
            assess(candidate, trace_overrides={field: []})
    with pytest.raises(ValueError, match="events"):
        assess(candidate, trace_overrides={"response": {"events": [{"kind": "solver_step"}]}})
    citation = {"candidate_id": candidate["citation"], "start": 0, "end": 10_000}
    check = {"requirement_id": "req-topic", "relation": "satisfied", "reason": "recorded",
             "candidate_ids": [candidate["citation"]], "quoted_spans": [{**citation, "end": 2}]}
    for response_format in ("frozen", None):
        with pytest.raises(ValueError, match="citation or quotation"):
            assess(candidate, response_format=response_format, trace_overrides={"response": {"citations": [citation]}})
        with pytest.raises(ValueError, match="every requirement"):
            assess(candidate, response_format=response_format, trace_overrides={"response": {"checks": [check]}})
    other = dict(candidate, citation="geos-manual:chapter-2")
    recorded_quote = {"candidate_id": candidate["citation"], "start": 0, "end": len(text)}
    for trace_overrides in (
        {"selected_chunk_ids": [candidate["citation"]]},
        {"events": [{"stage": "prepare_query"},
                    {"stage": "candidate_selection", "candidate_ids": [candidate["citation"]]},
                    {"stage": "assessment"}]},
        {"events": [{"stage": "response", "candidate_ids": [candidate["citation"]]}]},
        {"response": {"citations": [recorded_quote]}},
        {"response": {"requirement_id": "req-topic", "quoted_spans": [recorded_quote]}},
    ):
        assert assess(candidate, include_good=True, companion=other, trace_overrides=trace_overrides).status == "supported"
        for response_format in ("frozen", None):
            with pytest.raises(ValueError, match="trace selection or review"):
                assess(other, include_good=True, trace_overrides=trace_overrides, response_format=response_format)
    assert assess(candidate, recorded_review=(recorded_quote, "satisfied")).status == "supported"
    for trace_quote, relation, target in ((recorded_quote, "satisfied", other),
                                         ({**recorded_quote, "end": 2}, "satisfied", candidate),
                                         (recorded_quote, "missing", candidate)):
        with pytest.raises(ValueError, match="trace selection or review"):
            assess(target, include_good=True, companion=other if target is candidate else candidate,
                   recorded_review=(trace_quote, relation))
    assert not query_language._has_valid_source_span(dict(candidate, page_start=5, page_end=2))
    assert not query_language._has_valid_source_span(dict(candidate, source_span={"char_start": True}))
    with pytest.raises(ValueError, match="trace identity"):
        assess(candidate, trace_overrides={"assessment_id": None})
    with pytest.raises(ValueError, match="retrieval trace artifact"):
        assess(candidate, trace_role="tool_trace", trace_format="cae-tool-trace-v1")
    assert assess(candidate, approved=False).status == "insufficient"
    assert assess(candidate, gaps=["missing_restart_validation"]).status == "insufficient"
    restart = query_language.prepare_query("GEOS v2.23 restart")
    assert "missing_restart_validation" in assess(candidate, question_plan=restart).missing_evidence
    wrong_solver = dict(candidate, solver_scope=["calculix"])
    comparison = query_language.prepare_query("Compare GEOS and CalculiX Drucker-Prager")
    ccx = dict(wrong_solver, citation="calculix-manual:chapter-1")
    both_solvers = assess(ccx, include_good=True, grouped=True, question_plan=comparison)
    assert both_solvers.status == "supported"
    assert {item.candidate_id for item in both_solvers.candidate_checks if item.relation == "relevant"} == {
        candidate["citation"], ccx["citation"]
    }
    one_solver = assess(ccx, grouped=True, question_plan=comparison)
    assert one_solver.status == "insufficient" and "missing_solver" in one_solver.missing_evidence
    assert one_solver.candidate_checks[0].relation == "relevant"
    paired = query_language.prepare_query("Compare GEOS 2.23 and CCX 2.24: Drucker-Prager")
    assert assess(dict(ccx, edition="2.24"), include_good=True, grouped=True,
                  question_plan=paired).status == "supported"
    swapped = assess(ccx, include_good=True, companion=dict(candidate, edition="2.24"),
                     grouped=True, question_plan=paired)
    assert swapped.status == "insufficient" and "edition_mismatch" in swapped.missing_evidence
    assert assess(wrong_solver, candidate_scopes={candidate["citation"]: ["geos"]}).status == "insufficient"
    assert assess(wrong_solver, facets={"solver": "calculix"}).status == "insufficient"
    assert assess(dict(wrong_solver, coverage_facets={"solver": ["geos"]})).status == "insufficient"
    for invalid in (dict(edition="2.230"), dict(char_end=80), dict(document_sha256="b" * 64)):
        assert assess(dict(candidate, citation="other:chunk", **invalid), include_good=True).status == "insufficient"
    first = query_language.build_retrieval_decision(plan, [candidate])
    matching = {"source_id": candidate["source_id"], "start_line": 12, "end_line": 12, "page": None}
    nested = query_language.build_retrieval_decision(plan, [dict(candidate, page=None, source_span=matching)])
    assert first.candidate_set_sha256 != nested.candidate_set_sha256
    for bad in (dict(start_line=13), dict(end_line=15), dict(source_id="other"), dict(page=1)):
        with pytest.raises(ValueError, match="source_span conflict"):
            query_language.build_retrieval_decision(plan, [dict(candidate, page=None, source_span={**matching, **bad})])
    for bad in (dict(start_line=True), dict(end_line=False), dict(page=True), dict(page=-1), dict(page=0),
                dict(char_start=True), dict(char_end=False), dict(char_start=-1), dict(byte_start=-1), dict(byte_end=True)):
        with pytest.raises(ValueError, match="source_span position"):
            query_language.build_retrieval_decision(plan, [dict(candidate, source_span={**matching, **bad})])
    for bad in (dict(start_page=5, end_page=2), dict(line_start=13), dict(char_start=200),
                dict(byte_start=10, byte_end=8)):
        with pytest.raises(ValueError, match="Reversed candidate source_span range"):
            query_language.build_retrieval_decision(plan, [dict(candidate, **bad)])
    for changed in (dict(text=text[:-1] + "X"), dict(line_start=13, line_end=13), dict(source_id="other"),
                    dict(coverage_facets={"topics": ["restart"]}), dict(topics=["restart"]),
                    dict(source_kind="solver_manual"), dict(review_status="approved"), dict(solver="geos")):
        altered = query_language.build_retrieval_decision(plan, [dict(candidate, **changed)])
        assert first.candidate_set_sha256 != altered.candidate_set_sha256
        assert first.assessment_id != altered.assessment_id
    norton = query_language.build_retrieval_decision(plan, [candidate], explicit_facets={"model": "Norton"})
    prony = query_language.build_retrieval_decision(plan, [candidate], explicit_facets={"model": "Prony"})
    assert norton.assessment_id != prony.assessment_id
    assert next(req for req in norton.requirements if req.facet == "model").original_span is None


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize("review_location", ["event", "nested-event", "root-response", "root-citations", "completion"])
def test_d09_search_quotes_cannot_substitute_for_review(assessed_retrieval, response_format, review_location):
    assess, candidate = assessed_retrieval
    other = dict(candidate, citation="geos-manual:chapter-2")
    reviewed, searched = (
        {"candidate_id": item["citation"], "start": 0, "end": len(item["text"])} for item in (candidate, other)
    )
    result = {"stage": "retrieval_completion"}
    search = {"stage": "search", "citations": [searched]}
    if review_location == "nested-event":
        search = {"stage": "search", "response": {"citations": [searched]}}
    trace = {"events": [
        {"stage": "prepare_query"},
        search,
        result,
    ]}
    if review_location == "event":
        result.update(stage="response", citations=[reviewed])
    elif review_location == "nested-event":
        result.update(assessment={"citations": [reviewed]})
    elif review_location == "root-response":
        trace["response"] = {"citations": [reviewed]}
    elif review_location == "root-citations":
        trace["citations"] = [reviewed]
    else:
        result["citations"] = [reviewed]

    assert assess(candidate, companion=other, include_good=True,
                  trace_overrides=trace, response_format=response_format).status == "supported"
    with pytest.raises(ValueError, match="trace selection or review"):
        assess(other, include_good=True, trace_overrides=trace, response_format=response_format)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize("final_location", [
    "response", "retrieval_completion", "complete", "completed", "finalize", "root-response", "root-citations",
])
def test_d09_final_review_supersedes_interim_citations(assessed_retrieval, response_format, final_location):
    assess, candidate = assessed_retrieval
    other = dict(candidate, citation="geos-manual:chapter-2")
    final_quote, earlier_quote = (
        {"candidate_id": item["citation"], "start": 0, "end": len(item["text"])} for item in (candidate, other)
    )
    interim_quotes = ([earlier_quote] if final_location in {"retrieval_completion", "complete", "completed", "finalize"}
                      else [final_quote, earlier_quote])
    trace = {"events": [
        {"stage": "prepare_query"},
        {"stage": "search", "citations": [earlier_quote]},
        {"stage": "assessment", "citations": interim_quotes},
    ]}
    if final_location == "root-response":
        trace["response"] = {"citations": [final_quote]}
    elif final_location == "root-citations":
        trace["response"] = {"citations": [earlier_quote]}
        trace["citations"] = [final_quote]
    else:
        trace["events"].append({"stage": final_location, "citations": [final_quote]})

    with pytest.raises(ValueError, match="trace selection or review"):
        assess(other, include_good=True, trace_overrides=trace, response_format=response_format)
    assert assess(candidate, companion=other, include_good=True,
                  trace_overrides=trace, response_format=response_format).status == "supported"


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize("layout", [
    "root", "wrapped", "expanded", "ids-only", "sibling", "retrieval-events", "retrieval-steps", "selection-steps",
    "retrieval-checks-events", "retrieval-checks-steps", "selection-result-events", "selection-result-steps",
    "completion-reports-events", "completion-reports-steps",
])
def test_d09_review_wrappers_preserve_requirement_bindings(
    assessed_retrieval, response_format, layout,
):
    assess, candidate = assessed_retrieval
    other = dict(candidate, citation="geos-manual:chapter-2")
    plan = query_language.prepare_query("Explain Drucker-Prager yield in version 2.23")
    requirements = query_language.extract_query_requirements(plan)
    assert [req.facet for req in requirements] == ["topic", "edition"]
    quotes = [{"candidate_id": item["citation"], "start": 0, "end": len(item["text"])}
              for item in (candidate, other)]
    selection_result = layout.startswith("selection-result-")
    if selection_result:
        other_quote = quotes[1]
        quotes[1] = {**quotes[0], "end": 8}

    def checks(spans):
        return [{"requirement_id": req.requirement_id, "candidate_ids": [span["candidate_id"]],
                 "relation": "satisfied", "quoted_spans": [span], "reason": "recorded review"}
                for req, span in zip(requirements, spans, strict=True)]

    response = {"checks": checks(quotes), "candidate_ids": list(dict.fromkeys(span["candidate_id"] for span in quotes))}
    summary = {"citations": list(reversed(quotes))}
    if layout == "root":
        trace = {"response": response, **summary}
    elif layout == "wrapped":
        trace = {"events": [{"stage": "retrieval_completion", "response": response, **summary}], **summary}
    elif layout in {"expanded", "ids-only"}:
        wrapper = {"stage": "retrieval_completion", "response": response}
        if layout == "expanded":
            wrapper["citations"] = [*quotes, {**quotes[0], "end": 8}]
        else:
            wrapper["candidate_ids"] = response["candidate_ids"]
        trace = {"events": [wrapper]}
    elif layout in {"retrieval-events", "retrieval-steps", "selection-steps"}:
        wrapper_kind, history_field = layout.split("-")
        history = [{"stage": "prepare_query"}, {"stage": "assessment", **response}]
        wrapper = {"stage": "retrieval" if wrapper_kind == "retrieval" else "candidate_selection",
                   history_field: history, **summary}
        events = [wrapper] if wrapper_kind == "retrieval" else [
            {"stage": "prepare_query"}, wrapper, {"stage": "retrieval_completion"},
        ]
        trace = {"events": events}
    elif layout.startswith("retrieval-checks-"):
        history_field = layout.removeprefix("retrieval-checks-")
        trace = {"events": [{
            "stage": "retrieval", "checks": response["checks"],
            history_field: [{"stage": "prepare_query"}, {"stage": "assessment", **summary}],
        }]}
    elif selection_result:
        history_field = layout.removeprefix("selection-result-")
        trace = {"events": [
            {"stage": "prepare_query"},
            {"stage": "candidate_selection", "candidate_ids": [candidate["citation"], other["citation"]],
             history_field: [{"stage": "prepare_query"}, {"stage": "assessment", **response}],
             "result": {"citations": [*quotes, other_quote]}},
            {"stage": "retrieval_completion"},
        ]}
    elif layout.startswith("completion-reports-"):
        history_field = layout.removeprefix("completion-reports-")
        trace = {"events": [{
            "stage": "retrieval_completion",
            history_field: [{"stage": "prepare_query"}, {"stage": "assessment", **response}],
            "assessment": {"checks": response["checks"]},
            "response": summary,
        }]}
    else:
        trace = {"events": [{"stage": "assessment", **response}, {"stage": "retrieval_completion", **summary}]}

    host_citations = {"citations": quotes} if response_format is None else {}

    def assess_assignments(spans):
        return assess(candidate, companion=other, include_good=True, question_plan=plan,
                      trace_overrides=trace, response_format=response_format,
                      host_overrides={**host_citations, "checks": checks(spans)})

    assert assess_assignments(quotes).status == "supported"
    if layout == "sibling":
        assert assess_assignments(reversed(quotes)).status == "supported"
    else:
        with pytest.raises(ValueError, match="trace selection or review"):
            assess_assignments(reversed(quotes))
    if selection_result:
        with pytest.raises(ValueError, match="trace selection or review"):
            assess(other, include_good=True, question_plan=plan, trace_overrides=trace, response_format=response_format)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize(("summary_kind", "with_checks"), [
    ("repeated", False), ("repeated", True), ("expanded", False), ("expanded", True), ("ids-only", False),
    ("wrapper-ids", True),
    ("retrieval-events", False), ("retrieval-steps", False),
    ("selection-result-events", False), ("selection-result-steps", False),
])
def test_d09_review_wrappers_preserve_nested_candidate_ids(
    assessed_retrieval, response_format, summary_kind, with_checks,
):
    assess, candidate = assessed_retrieval
    other = dict(candidate, citation="geos-manual:chapter-2")
    quotes = [{"candidate_id": item["citation"], "start": 0, "end": len(item["text"])}
              for item in (candidate, other)]
    response = {"candidate_ids": [candidate["citation"]]}
    selection_result = summary_kind.startswith("selection-result-")
    reviewed_quotes = quotes[:1] if summary_kind == "expanded" or selection_result else quotes
    if summary_kind != "ids-only":
        response["citations"] = reviewed_quotes
    if with_checks:
        plan = query_language.prepare_query("GEOS v2.23 Drucker-Prager yield")
        response["checks"] = [
            {"requirement_id": req.requirement_id, "candidate_ids": [span["candidate_id"] for span in reviewed_quotes],
             "relation": "satisfied", "quoted_spans": reviewed_quotes, "reason": "recorded review"}
            for req in query_language.extract_query_requirements(plan)
        ]
    trace = {
        "events": [{"stage": "retrieval_completion", "response": response, "citations": quotes}],
        "citations": quotes,
    }
    if summary_kind in {"retrieval-events", "retrieval-steps"}:
        history_field = summary_kind.removeprefix("retrieval-")
        trace = {"events": [{
            "stage": "retrieval", "citations": [*quotes, {**quotes[0], "end": 8}],
            history_field: [{"stage": "prepare_query"}, {"stage": "assessment", **response}],
        }]}
    elif selection_result:
        history_field = summary_kind.removeprefix("selection-result-")
        trace = {"events": [
            {"stage": "prepare_query"},
            {"stage": "candidate_selection", "candidate_ids": [span["candidate_id"] for span in quotes],
             history_field: [{"stage": "prepare_query"}, {"stage": "assessment", **response}],
             "result": {"citations": quotes}},
            {"stage": "retrieval_completion"},
        ]}
    elif summary_kind == "wrapper-ids":
        wrapper = trace["events"][0]
        del wrapper["citations"]
        wrapper["candidate_ids"] = [span["candidate_id"] for span in quotes]
        del trace["citations"]
    assert assess(candidate, companion=other, include_good=True,
                  trace_overrides=trace, response_format=response_format).status == "supported"
    with pytest.raises(ValueError, match="trace selection or review"):
        assess(other, include_good=True, trace_overrides=trace, response_format=response_format)

    later_review = {"stage": "retrieval_completion", "citations": quotes}
    trace = {"events": [*trace["events"], later_review]}
    assert assess(other, include_good=True, trace_overrides=trace, response_format=response_format).status == "supported"


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize("history_field", ["events", "steps"])
@pytest.mark.parametrize("wrapper_stage", ["retrieval", "candidate_selection"])
@pytest.mark.parametrize(("has_citation", "report_layout"), [
    pytest.param(True, None, id="narrowed"),
    pytest.param(False, None, id="empty"),
    pytest.param(False, "citations", id="empty-after-repeated-report"),
    pytest.param(False, "nested-history", id="empty-after-nested-history"),
    pytest.param(False, "nested-history-and-citations", id="empty-after-nested-history-and-citations"),
])
def test_d09_history_summary_can_narrow_review_citations(
    assessed_retrieval, response_format, history_field, wrapper_stage, has_citation, report_layout,
):
    assess, first = assessed_retrieval
    second = dict(first, citation="geos-manual:chapter-2")
    quotes = [{"candidate_id": item["citation"], "start": 0, "end": len(item["text"])}
              for item in (first, second)]
    ids = [span["candidate_id"] for span in quotes]
    history_quotes = quotes[:1] if report_layout else quotes
    assessment = {"stage": "assessment", "citations": history_quotes}
    if not report_layout:
        assessment["candidate_ids"] = ids
    history = [{"stage": "prepare_query"}, assessment]
    summary = {"citations": quotes[:1] if has_citation else []}
    wrapper = {"stage": wrapper_stage, history_field: history}
    if report_layout:
        result = {}
        if report_layout != "citations":
            result[history_field] = history
        if report_layout != "nested-history":
            result["citations"] = history_quotes
        wrapper.update(result=result, output=summary)
        if wrapper_stage == "candidate_selection":
            wrapper["candidate_ids"] = ids
    elif wrapper_stage == "retrieval":
        wrapper.update(summary)
    else:
        wrapper.update(candidate_ids=ids, result=summary)
    trace = {"events": [{"stage": "prepare_query"}, wrapper, {"stage": "retrieval_completion"}]}

    with pytest.raises(ValueError, match="trace selection or review"):
        assess(second, include_good=True, trace_overrides=trace, response_format=response_format)
    if has_citation:
        assert assess(first, companion=second, include_good=True,
                      trace_overrides=trace, response_format=response_format).status == "supported"
    else:
        with pytest.raises(ValueError, match="trace selection or review"):
            assess(first, companion=second, include_good=True, trace_overrides=trace, response_format=response_format)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize("history_field", ["events", "steps"])
@pytest.mark.parametrize("review_field", ["candidate_ids", "checks"])
def test_d09_sibling_history_reports_preserve_candidate_id_narrowing(
    assessed_retrieval, response_format, history_field, review_field,
):
    assess, first = assessed_retrieval
    second = dict(first, citation="geos-manual:chapter-2")
    quotes = [{"candidate_id": item["citation"], "start": 0, "end": len(item["text"])}
              for item in (first, second)]
    ids = [span["candidate_id"] for span in quotes]
    if review_field == "checks":
        plan = query_language.prepare_query("Explain Drucker-Prager yield")
        [topic] = query_language.extract_query_requirements(plan)
        history_review = {"checks": [
            {"requirement_id": topic.requirement_id, "candidate_ids": ids, "relation": "satisfied",
             "quoted_spans": quotes, "reason": "recorded review"},
        ]}
        output = {"citations": quotes}
    else:
        plan = query_language.prepare_query("GEOS v2.23 Drucker-Prager yield")
        history_review = {"candidate_ids": ids, "citations": quotes}
        output = {"candidate_ids": ids}
    trace = {"events": [{
        "stage": "retrieval",
        history_field: [
            {"stage": "prepare_query"},
            {"stage": "assessment", **history_review},
        ],
        "result": {"candidate_ids": ids[:1]},
        "output": output,
    }]}
    assert assess(first, companion=second, include_good=True, question_plan=plan,
                  trace_overrides=trace, response_format=response_format).status == "supported"
    with pytest.raises(ValueError, match="trace selection or review"):
        assess(second, include_good=True, question_plan=plan, trace_overrides=trace, response_format=response_format)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize("history_key", ["stage_events", "events", "steps"])
def test_d09_nested_final_response_is_independent_of_dict_order(assessed_retrieval, response_format, history_key):
    assess, candidate = assessed_retrieval
    other = dict(candidate, citation="geos-manual:chapter-2")
    final_quote, earlier_quote = (
        {"candidate_id": item["citation"], "start": 0, "end": len(item["text"])} for item in (candidate, other)
    )
    history = [{"stage": "prepare_query"}, {"stage": "assessment", "citations": [earlier_quote]},
               {"stage": "retrieval_completion"}]
    event = {"stage": "retrieval_completion", "response": {"citations": [final_quote]}, history_key: history}
    for completion, serializer in (
        (event, canonical_json_bytes),
        (event, lambda value: json.dumps(value).encode("utf-8")),
        (dict(reversed(event.items())), lambda value: json.dumps(value).encode("utf-8")),
    ):
        trace = {"events": [completion]}
        assert assess(candidate, companion=other, include_good=True, trace_overrides=trace,
                      response_format=response_format, trace_serializer=serializer).status == "supported"
        with pytest.raises(ValueError, match="trace selection or review"):
            assess(other, include_good=True, trace_overrides=trace,
                   response_format=response_format, trace_serializer=serializer)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize(("history_field", "nested_interim"), [
    pytest.param(None, False, id="no-history"),
    pytest.param("events", False, id="events"),
    pytest.param("steps", False, id="steps"),
    pytest.param("events", True, id="events-with-interim-history"),
    pytest.param("steps", True, id="steps-with-interim-history"),
])
@pytest.mark.parametrize("interim_evidence", ["checks-and-ids", "ids-only", "checks-only"])
def test_d09_completion_sibling_response_supersedes_interim_assessment(
    assessed_retrieval, response_format, history_field, interim_evidence, nested_interim,
):
    assess, first = assessed_retrieval
    second = dict(first, citation="geos-manual:chapter-2")
    plan = query_language.prepare_query("Explain Drucker-Prager yield")
    [topic] = query_language.extract_query_requirements(plan)
    first_quote, second_quote = (
        {"candidate_id": item["citation"], "start": 0, "end": len(item["text"])} for item in (first, second)
    )
    interim = {}
    if interim_evidence != "checks-only":
        interim["candidate_ids"] = [second["citation"]]
    if interim_evidence != "ids-only":
        interim["checks"] = [
            {"requirement_id": topic.requirement_id, "candidate_ids": [second["citation"]], "relation": "satisfied",
             "quoted_spans": [second_quote], "reason": "interim review"},
        ]
    if nested_interim:
        interim["events"] = [{"stage": "assessment", **interim}]
    completion = {
        "stage": "retrieval_completion",
        "assessment": interim,
        "response": {"candidate_ids": [first["citation"]], "citations": [first_quote]},
    }
    if history_field is not None:
        completion[history_field] = [{"stage": "prepare_query"}, {"stage": "retrieval_completion"}]
    trace = {"events": [completion]}

    assert assess(first, companion=second, include_good=True, question_plan=plan,
                  trace_overrides=trace, response_format=response_format).status == "supported"
    with pytest.raises(ValueError, match="trace selection or review"):
        assess(second, include_good=True, question_plan=plan, trace_overrides=trace, response_format=response_format)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize("id_field", ["candidate_ids", "candidate_id"])
def test_d09_final_review_supersedes_interim_candidate_ids(assessed_retrieval, response_format, id_field):
    assess, candidate = assessed_retrieval
    other = dict(candidate, citation="geos-manual:chapter-2")
    final_quote, earlier_quote = (
        {"candidate_id": item["citation"], "start": 0, "end": len(item["text"])} for item in (candidate, other)
    )
    earlier_id = [other["citation"]] if id_field == "candidate_ids" else other["citation"]
    interim = {"stage": "assessment", id_field: earlier_id}
    for earlier, final in (
        ({**interim, "citations": [earlier_quote]}, {"citations": [final_quote]}),
        (interim, {"candidate_ids": [candidate["citation"]]}),
    ):
        trace = {"events": [earlier, {"stage": "retrieval_completion", **final}]}
        assert assess(candidate, companion=other, include_good=True,
                      trace_overrides=trace, response_format=response_format).status == "supported"
        with pytest.raises(ValueError, match="trace selection or review"):
            assess(other, include_good=True, trace_overrides=trace, response_format=response_format)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
def test_d09_ids_only_final_sibling_replaces_interim_review_evidence(assessed_retrieval, response_format):
    assess, candidate = assessed_retrieval
    other = dict(candidate, citation="geos-manual:chapter-2")
    plan = query_language.prepare_query("Explain Drucker-Prager yield")
    [topic] = query_language.extract_query_requirements(plan)
    assert topic.facet == "topic"
    interim_quote = {"candidate_id": candidate["citation"], "start": 0, "end": len(candidate["text"])}
    trace = {"events": [
        {"stage": "assessment", "citations": [interim_quote], "checks": [
            {"requirement_id": topic.requirement_id, "candidate_ids": [candidate["citation"]],
             "relation": "satisfied", "quoted_spans": [interim_quote], "reason": "interim review"},
        ]},
        {"stage": "retrieval_completion", "candidate_ids": [other["citation"]]},
    ]}
    assert assess(other, include_good=True, question_plan=plan,
                  trace_overrides=trace, response_format=response_format).status == "supported"
    with pytest.raises(ValueError, match="trace selection or review"):
        assess(candidate, companion=other, include_good=True, question_plan=plan,
               trace_overrides=trace, response_format=response_format)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize(("interim_review", "final_candidate", "supported_candidate"), [
    ("B", "B", "B"),
    ("A", "B", "B"),
    (None, "B", None),
    (None, None, "A"),
], ids=["review-B", "review-A", "ids-only", "bare-completion"])
def test_d09_selection_evidence_stays_superseded_after_review(
    assessed_retrieval, response_format, interim_review, final_candidate, supported_candidate,
):
    assess, first = assessed_retrieval
    second = dict(first, citation="geos-manual:chapter-2")
    candidates = {"A": first, "B": second}
    candidate_ids = {name: item["citation"] for name, item in candidates.items()}
    quotes = {name: {"candidate_id": item["citation"], "start": 0, "end": len(item["text"])}
              for name, item in candidates.items()}
    plan = query_language.prepare_query("Explain Drucker-Prager yield")
    [topic] = query_language.extract_query_requirements(plan)
    events = [
        {"stage": "prepare_query"},
        {"stage": "candidate_selection", "candidate_ids": list(candidate_ids.values()), "citations": [quotes["A"]]},
    ]
    if interim_review is not None:
        events.append({
            "stage": "assessment",
            "candidate_ids": [candidate_ids[interim_review]],
            "citations": [quotes[interim_review]],
            "checks": [{"requirement_id": topic.requirement_id, "candidate_ids": [candidate_ids[interim_review]],
                        "relation": "satisfied", "quoted_spans": [quotes[interim_review]], "reason": "interim review"}],
        })
    completion = {"stage": "retrieval_completion"}
    if final_candidate is not None:
        completion["candidate_ids"] = [candidate_ids[final_candidate]]
    trace = {"events": [*events, completion]}

    def assess_candidate(target, companion):
        return assess(target, companion=companion, include_good=True, question_plan=plan,
                      trace_overrides=trace, response_format=response_format)

    for name, target, companion in (("A", first, second), ("B", second, first)):
        if name == supported_candidate:
            assert assess_candidate(target, companion).status == "supported"
        else:
            with pytest.raises(ValueError, match="trace selection or review"):
                assess_candidate(target, companion)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize(("empty_field", "ids_only_completion"), [
    ("citations", True), ("quoted_spans", True), ("citations", False),
])
def test_d09_empty_review_does_not_supersede_selection_evidence(
    assessed_retrieval, response_format, empty_field, ids_only_completion,
):
    assess, first = assessed_retrieval
    second = dict(first, citation="geos-manual:chapter-2")
    first_quote = {"candidate_id": first["citation"], "start": 0, "end": len(first["text"])}
    events = [
        {"stage": "prepare_query"},
        {"stage": "candidate_selection", "candidate_ids": [first["citation"], second["citation"]],
         "citations": [first_quote]},
        {"stage": "assessment", empty_field: []},
    ]
    if ids_only_completion:
        events.append({"stage": "retrieval_completion", "candidate_ids": [second["citation"]]})

    for candidate, companion in ((first, second), (second, first)):
        with pytest.raises(ValueError, match="trace selection or review"):
            assess(candidate, companion=companion, include_good=True, trace_overrides={"events": events},
                   response_format=response_format)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize("late_field", ["citations", "quoted_spans"])
@pytest.mark.parametrize("has_late_quote", [False, True], ids=["empty", "quoted"])
def test_d09_only_substantive_selection_evidence_replaces_empty_review(
    assessed_retrieval, response_format, late_field, has_late_quote,
):
    assess, first = assessed_retrieval
    second = dict(first, citation="geos-manual:chapter-2")
    first_quote, second_quote = (
        {"candidate_id": item["citation"], "start": 0, "end": len(item["text"])} for item in (first, second)
    )
    selection = {"stage": "candidate_selection", "candidate_ids": [first["citation"], second["citation"]]}
    trace = {"events": [
        {"stage": "prepare_query"},
        {**selection, "citations": [first_quote]},
        {"stage": "assessment", "citations": []},
        {"stage": "search"},
        {**selection, late_field: [second_quote] if has_late_quote else []},
        {"stage": "retrieval_completion"},
    ]}

    def assess_candidate(candidate, companion):
        return assess(candidate, companion=companion, include_good=True, trace_overrides=trace,
                      response_format=response_format)

    with pytest.raises(ValueError, match="trace selection or review"):
        assess_candidate(first, second)
    if has_late_quote:
        assert assess_candidate(second, first).status == "supported"
    else:
        with pytest.raises(ValueError, match="trace selection or review"):
            assess_candidate(second, first)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize(("initial_selection_quotes", "review_late_selection"), [
    (False, False), (True, False), (True, True),
], ids=["empty-selection", "quoted-selection", "quoted-review-after-selection"])
def test_d09_later_selection_evidence_applies_after_earlier_review(
    assessed_retrieval, response_format, initial_selection_quotes, review_late_selection,
):
    assess, first = assessed_retrieval
    second = dict(first, citation="geos-manual:chapter-2")
    first_id, second_id = first["citation"], second["citation"]
    first_quote, second_quote = (
        {"candidate_id": item["citation"], "start": 0, "end": len(item["text"])} for item in (first, second)
    )
    plan = query_language.prepare_query("Explain Drucker-Prager yield")
    [topic] = query_language.extract_query_requirements(plan)

    def evidence(quote):
        return {
            "citations": [quote],
            "checks": [{"requirement_id": topic.requirement_id, "candidate_ids": [quote["candidate_id"]],
                        "relation": "satisfied", "quoted_spans": [quote], "reason": "recorded evidence"}],
        }

    selection = {"stage": "candidate_selection", "candidate_ids": [first_id, second_id]}
    initial = {**selection, "citations": [first_quote]} if initial_selection_quotes else selection
    events = [{"stage": "prepare_query"}, initial, {"stage": "assessment", **evidence(first_quote)}]
    if initial_selection_quotes:
        events.append({"stage": "search"})
    events.append({**selection, **evidence(first_quote)})
    if review_late_selection:
        events.append({"stage": "assessment", **evidence(second_quote)})
    events.append({"stage": "retrieval_completion", "candidate_ids": [second_id]})

    def assess_candidate(candidate, companion):
        return assess(candidate, companion=companion, include_good=True, question_plan=plan,
                      trace_overrides={"events": events}, response_format=response_format)

    with pytest.raises(ValueError, match="trace selection or review"):
        assess_candidate(first, second)
    if review_late_selection:
        assert assess_candidate(second, first).status == "supported"
    else:
        with pytest.raises(ValueError, match="trace selection or review"):
            assess_candidate(second, first)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize(("earlier_review", "completion_kind", "supported_candidate"), [
    pytest.param("quoted", "bare", "A", id="bare"),
    pytest.param("quoted", "events", None, id="enclosing-events"),
    pytest.param("quoted", "steps", None, id="enclosing-steps"),
    pytest.param(None, "events", None, id="events-without-earlier-review"),
    pytest.param(None, "steps", None, id="steps-without-earlier-review"),
    pytest.param("quoted", "fresh-review", "B", id="fresh-review"),
    pytest.param("empty", "bare", "A", id="earlier-empty-review"),
    pytest.param("ids-only", "bare", "A", id="earlier-ids-only-review"),
])
def test_d09_new_selection_replaces_stale_review(
    assessed_retrieval, response_format, earlier_review, completion_kind, supported_candidate,
):
    assess, first = assessed_retrieval
    second = dict(first, citation="geos-manual:chapter-2")
    first_id, second_id = first["citation"], second["citation"]
    plan = query_language.prepare_query("Explain Drucker-Prager yield")
    [topic] = query_language.extract_query_requirements(plan)

    def evidence(candidate):
        cid = candidate["citation"]
        quote = {"candidate_id": cid, "start": 0, "end": len(candidate["text"])}
        return {
            "citations": [quote],
            "checks": [{"requirement_id": topic.requirement_id, "candidate_ids": [cid], "relation": "satisfied",
                        "quoted_spans": [quote], "reason": "recorded evidence"}],
        }

    review = {"stage": "assessment", "candidate_ids": [second_id], **evidence(second)}
    events = [{"stage": "prepare_query"}]
    if earlier_review == "quoted":
        events.append(review)
    elif earlier_review == "empty":
        events.append({"stage": "assessment", "citations": []})
    elif earlier_review == "ids-only":
        events.append({"stage": "assessment", "candidate_ids": [second_id]})
    events.extend([
        {"stage": "search"},
        {"stage": "candidate_selection", "candidate_ids": [first_id, second_id], **evidence(first)},
    ])
    if completion_kind == "fresh-review":
        events.append(review)
    events.append({"stage": "retrieval_completion"})
    if completion_kind in {"events", "steps"}:
        events = [{"stage": "retrieval_completion", "candidate_ids": [second_id], completion_kind: events}]
    trace = {"events": events}

    def assess_candidate(candidate, companion):
        return assess(candidate, companion=companion, include_good=True, question_plan=plan,
                      trace_overrides=trace, response_format=response_format)

    for name, candidate, companion in (("A", first, second), ("B", second, first)):
        if name == supported_candidate:
            assert assess_candidate(candidate, companion).status == "supported"
        else:
            with pytest.raises(ValueError, match="trace selection or review"):
                assess_candidate(candidate, companion)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize(("container", "id_field", "inner_stage"), [
    ("result", "candidate_ids", {}), ("output", "candidate_id", {}), ("response", "candidate_ids", {}),
    ("result", "candidate_ids", {"kind": "response"}),
])
def test_d09_trace_binds_nested_candidate_selection(assessed_retrieval, response_format, container, id_field, inner_stage):
    assess, candidate = assessed_retrieval
    other = dict(candidate, citation="geos-manual:chapter-2")
    selected = [candidate["citation"]] if id_field == "candidate_ids" else candidate["citation"]
    final_quote = {"candidate_id": other["citation"], "start": 0, "end": len(other["text"])}
    trace = {"events": [
        {"stage": "prepare_query"},
        {"kind": "candidate_selection", container: {**inner_stage, id_field: selected}},
        {"stage": "retrieval_completion", "citations": [final_quote]},
    ]}
    with pytest.raises(ValueError, match="trace selection or review"):
        assess(other, include_good=True, trace_overrides=trace, response_format=response_format)
    final_quote["candidate_id"] = candidate["citation"]
    assert assess(candidate, companion=other, include_good=True,
                  trace_overrides=trace, response_format=response_format).status == "supported"


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
def test_d09_explicit_trace_stage_takes_precedence_over_kind(assessed_retrieval, response_format):
    assess, candidate = assessed_retrieval
    other = dict(candidate, citation="geos-manual:chapter-2")
    selection = {
        "kind": "search",
        "stage": "candidate_selection",
        "result": {"kind": "response", "candidate_ids": [candidate["citation"]]},
    }
    trace = {"events": [{"stage": "prepare_query"}, selection, {"stage": "retrieval_completion"}]}
    assert assess(candidate, companion=other, include_good=True,
                  trace_overrides=trace, response_format=response_format).status == "supported"
    with pytest.raises(ValueError, match="trace selection or review"):
        assess(other, include_good=True, trace_overrides=trace, response_format=response_format)

    selection["stage"] = None
    with pytest.raises(ValueError, match="trace events"):
        assess(candidate, companion=other, include_good=True, trace_overrides=trace, response_format=response_format)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize("history_key", ["stage_events", "events", "steps"])
def test_d09_search_history_enforces_its_dedicated_candidate_selection(assessed_retrieval, response_format, history_key):
    assess, candidate = assessed_retrieval
    other = dict(candidate, citation="geos-manual:chapter-2")
    final_quote = {"candidate_id": other["citation"], "start": 0, "end": len(other["text"])}
    history = [
        {"stage": "prepare_query"},
        {"stage": "candidate_selection", "candidate_id": candidate["citation"]},
        {"stage": "retrieval_completion"},
    ]
    trace = {"events": [
        {"stage": "prepare_query"},
        {"stage": "search", history_key: history},
        {"stage": "retrieval_completion", "citations": [final_quote]},
    ]}
    with pytest.raises(ValueError, match="trace selection or review"):
        assess(other, include_good=True, trace_overrides=trace, response_format=response_format)
    final_quote["candidate_id"] = candidate["citation"]
    assert assess(candidate, companion=other, include_good=True,
                  trace_overrides=trace, response_format=response_format).status == "supported"


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize("invalid_location", ["step", "result"])
def test_d09_search_steps_validate_explicit_stages(assessed_retrieval, response_format, invalid_location):
    assess, candidate = assessed_retrieval
    other = dict(candidate, citation="geos-manual:chapter-2")
    selection = {"kind": "candidate_selection", "result": {"candidate_ids": [candidate["citation"]]}}
    trace = {"events": [
        {"stage": "prepare_query"},
        {"stage": "search", "steps": [selection]},
        {"stage": "retrieval_completion"},
    ]}
    assert assess(candidate, companion=other, include_good=True,
                  trace_overrides=trace, response_format=response_format).status == "supported"
    with pytest.raises(ValueError, match="trace selection or review"):
        assess(other, include_good=True, trace_overrides=trace, response_format=response_format)

    malformed = selection if invalid_location == "step" else selection["result"]
    for value in (None, "unknown_stage"):
        malformed["stage"] = value
        with pytest.raises(ValueError, match="Invalid retrieval assessment trace events"):
            assess(candidate, companion=other, include_good=True, trace_overrides=trace, response_format=response_format)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
def test_d09_nested_trace_result_ends_the_sequence(assessed_retrieval, response_format):
    assess, candidate = assessed_retrieval
    nested = [{"stage": stage} for stage in ("prepare_query", "search", "retrieval_completion")]
    trace = {"events": [{"kind": "retrieval", "events": nested}]}
    assert assess(candidate, trace_overrides=trace, response_format=response_format).status == "supported"
    nested.append({"stage": "prepare_query"})
    with pytest.raises(ValueError, match="trace events"):
        assess(candidate, trace_overrides=trace, response_format=response_format)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
def test_d09_trace_binds_singular_candidate_selection(assessed_retrieval, response_format):
    assess, candidate = assessed_retrieval
    other = dict(candidate, citation="geos-manual:chapter-2")
    selection = {"kind": "candidate_selection", "candidate_id": candidate["citation"]}
    trace = {"events": [{"stage": "prepare_query"}, selection, {"stage": "assessment"}]}
    assert assess(candidate, companion=other, include_good=True,
                  trace_overrides=trace, response_format=response_format).status == "supported"
    with pytest.raises(ValueError, match="trace selection or review"):
        assess(other, include_good=True, trace_overrides=trace, response_format=response_format)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
def test_d09_trace_accepts_full_reranked_candidate_selection(assessed_retrieval, response_format):
    assess, candidate = assessed_retrieval
    other = dict(candidate, citation="geos-manual:chapter-2")
    reranked = [other["citation"], candidate["citation"]]
    trace = {"events": [{"stage": "prepare_query"}, {"stage": "candidate_selection", "candidate_ids": reranked},
                        {"stage": "assessment"}]}
    assert assess(other, include_good=True, trace_overrides=trace, response_format=response_format).status == "supported"
    trace["events"][0]["candidate_ids"] = reranked
    with pytest.raises(ValueError, match="trace identity"):
        assess(other, include_good=True, trace_overrides=trace, response_format=response_format)


@pytest.mark.parametrize("response_format", ["frozen", None], ids=["live", "persisted"])
@pytest.mark.parametrize("stages", [
    ("retrieval_completion", "prepare_query"),
    ("prepare_query", "search", "retrieval_completion", "prepare_query"),
    ("retrieval_completion", "query_plan", "response"),
    ("retrieval_completion", "search"),
])
def test_d09_trace_result_ends_the_sequence(assessed_retrieval, response_format, stages):
    assess, candidate = assessed_retrieval
    compact = {"events": [{"kind": "retrieval"}]}
    assert assess(candidate, trace_overrides=compact, response_format=response_format).status == "supported"
    trace = {"events": [{"stage": stage} for stage in stages]}
    for trailer in ({}, {"stage_events": ["prepare_query", "retrieval_completion"]}):
        with pytest.raises(ValueError, match="trace events"):
            assess(candidate, trace_overrides={**trace, **trailer}, response_format=response_format)


def test_d09_rejects_corrupt_artifacts_and_refreshes_ask_cache(tmp_path, monkeypatch):
    root, manifest, data = _make_sample_corpus(tmp_path)
    out = tmp_path / "index"
    first = corpus.build_corpus(manifest, output_root=out)
    monkeypatch.setenv("CAELAB_CORPUS_INDEX_DIR", str(out))
    query = "Drucker-Prager 팽창각"
    result_a = knowledge.search_knowledge_result(query, solver="calculix")
    assert result_a.corpus_revision == first.revision and "trial pressure" in result_a.entries[0].text

    source = data["sources"][0]
    source.update(solver_scope=["calculix", "abaqus"], source_kind="source_code")
    path = root / source["local_relative_path"]
    replacement = path.read_bytes().replace(b"trial pressure", b"updated pressure")
    path.write_bytes(replacement)
    source.update(sha256=hashlib.sha256(replacement).hexdigest(), byte_length=len(replacement))
    data["revision"] = "r2"
    manifest.write_bytes(canonical_json_bytes(data))
    second = corpus.build_corpus(manifest, output_root=out)
    result_b = knowledge.search_knowledge_result(query, solver="calculix")
    assert result_b.corpus_revision == second.revision
    entry = result_b.entries[0]
    assert "updated pressure" in entry.text
    assert entry.corpus_chunk.document_sha256 == source["sha256"]
    assert entry.corpus_chunk.edition == source["edition"]
    assert entry.solver_scope == ("calculix", "abaqus")
    assert knowledge.search_knowledge_result(query, solver="abaqus").entries[0].solver_scope == entry.solver_scope
    assert replacement.decode()[entry.corpus_chunk.char_start:entry.corpus_chunk.char_end] == entry.text

    discussion_path = tmp_path / "discussion.json"
    document = discussions.create_discussion(discussion_path, question_scope="theory")
    provider = _StubTheoryProvider("[근거: " + knowledge.citation_id(entry) + "]")
    monkeypatch.setattr(experts, "get_provider", lambda _pid=None: provider)
    answered = discussions.ask_discussion(discussion_path, query, template_id="theory",
                                        expected_revision=document["revision"])
    turn = answered["turns"][-1]
    assert turn["status"] == "completed"
    citation = next(material for material in turn["provided_materials"]
                    if material["citation_id"] == knowledge.citation_id(entry))
    for key in ("edition", "document_sha256", "line_start", "line_end", "char_start", "char_end"):
        assert citation[key] == getattr(entry.corpus_chunk, key)
    stored_context = json.loads((tmp_path / "assets" / turn["request_context_ref"]["path"]).read_bytes())
    assert stored_context["query_plan"]["original_text"] == query
    assert turn["corpus_revision"] == stored_context["corpus_revision"] == second.revision
    assert turn["index_sha256"] == stored_context["index_sha256"] == second.index_digest
    assert entry.source_kind == "source_code" and entry.review_status == "unreviewed"
    assert "소스 코드" in provider.prompts[-1] and "[근거 — 솔버 매뉴얼 지식 팩]" not in provider.prompts[-1]
    assert citation["source_kind"] == entry.source_kind and citation["review_status"] == entry.review_status
    assert turn["request_artifact_ref"] == turn["request_context_ref"]
    assert turn["request_artifact_sha256"] == turn["request_context_ref"]["sha256"]
    assert turn["retrieval_trace_ref"] == turn["tool_trace_ref"]

    idx = Path(second.index_dir)
    for relative in ("manifest.json", "chunks.json", "licenses/LICENSE"):
        file = idx / relative
        original = file.read_bytes()
        file.write_bytes(original + b" ")
        with pytest.raises(ValueError, match="INDEX_DIGEST_MISMATCH"):
            corpus.load_corpus_bundle(out)
        assert knowledge.search_knowledge_result(query).status == "error"
        file.write_bytes(original)
    (idx / "vectors.npy").unlink()
    with pytest.raises(FileNotFoundError, match="vectors.npy"):
        corpus.load_corpus_bundle(out)


def test_d09_review_overlay_applies_per_source_and_image_only_fails(tmp_path):
    from pypdf import PdfWriter
    root, manifest, data = _make_sample_corpus(tmp_path)
    out = tmp_path / "out"
    review = tmp_path / "review.json"
    first, second = data["sources"]
    draft = {"schema_version": 1, "reviews": [
        dict(source_id=first["source_id"], document_sha256=first["sha256"]),
        dict(source_id=second["source_id"], document_sha256=second["sha256"], status="rejected"),
    ]}
    review.write_bytes(canonical_json_bytes(draft))
    receipt = corpus.build_corpus(manifest, output_root=out, review_overlay_path=review)
    assert receipt.source_statuses[first["source_id"]] == "unreviewed"
    assert receipt.source_statuses[second["source_id"]] == "rejected"
    index, _ = corpus.load_corpus_bundle(out)
    assert {chunk.source_id for chunk in index.chunks} == {first["source_id"]}
    draft["reviews"][0]["status"] = "approved"
    review.write_bytes(canonical_json_bytes(draft))
    approved = corpus.build_corpus(manifest, output_root=out, review_overlay_path=review)
    assert approved.source_statuses[first["source_id"]] == "approved"

    pdf = root / "image_only.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(pdf)
    raw = pdf.read_bytes()
    data["sources"] = [dict(first, local_relative_path=pdf.name,
                            byte_length=len(raw), sha256=hashlib.sha256(raw).hexdigest())]
    manifest.write_bytes(canonical_json_bytes(data))
    with pytest.raises(ValueError, match="NO_EXTRACTABLE_CHUNKS"):
        corpus.build_corpus(manifest, output_root=out, verify_only=True)


@pytest.mark.parametrize("false_page, diagnostic", [(999, "PAGE_OUT_OF_RANGE"), (2, "PDF_PAGE_SPAN_MISMATCH"), (None, "PAGE_OUT_OF_RANGE")])
def test_d09_pdf_chunk_page_is_bounded_at_build_and_load(tmp_path, monkeypatch, false_page, diagnostic):
    from pypdf import PdfWriter

    root, manifest, data = _make_sample_corpus(tmp_path)
    source = data["sources"][0]
    pdf = root / "manual.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    writer.write(pdf)
    raw = pdf.read_bytes()
    source.update(local_relative_path=pdf.name, sha256=hashlib.sha256(raw).hexdigest(), byte_length=len(raw))
    manifest.write_bytes(canonical_json_bytes(data))
    # The real PDF page tree supplies the count. Stub layout-dependent text extraction.
    monkeypatch.setattr(corpus, "_pdf_page_texts",
                        lambda reader: [f"{topic}\n" for topic, _ in zip(("Weak form", "Boundary"), reader.pages)])
    overlay = tmp_path / "overlay.json"
    span = {"source_id": source["source_id"], "chunk_id": "weak-form", "line_start": 1,
            "line_end": 1, "section_path": ["Weak form"], "page": false_page}
    overlay.write_bytes(canonical_json_bytes({"schema_version": 1, "chunks": [span]}))
    with pytest.raises(ValueError, match=diagnostic):
        corpus.build_corpus(manifest, output_root=tmp_path / "out", review_overlay_path=overlay)
    span["page"] = 1
    overlay.write_bytes(canonical_json_bytes({"schema_version": 1, "chunks": [span]}))
    receipt = corpus.build_corpus(manifest, output_root=tmp_path / "out", review_overlay_path=overlay)
    index_path = Path(receipt.index_dir)
    index, _ = corpus.load_corpus_bundle(index_path)
    assert index.chunks[0].corpus_chunk.page_start == 1
    chunks_path = index_path / "chunks.json"
    payload = json.loads(chunks_path.read_bytes())
    payload["chunks"][0].update(page_start=false_page, page_end=false_page)
    chunks_path.write_bytes(canonical_json_bytes(payload))
    published, _ = corpus.load_corpus_manifest(index_path / "manifest.json")
    digest = corpus._index_digest(published, corpus._dir_snapshot(index_path))
    altered = index_path.rename(tmp_path / digest)
    with pytest.raises(ValueError, match=diagnostic):
        corpus.load_corpus_bundle(altered)


@pytest.mark.parametrize("local_only", [False, True])
def test_assessment_quote_must_fit_the_claimed_page_of_a_pdf_chunk(tmp_path, monkeypatch, local_only):
    from pypdf import PdfWriter

    root, manifest, data = _make_sample_corpus(tmp_path)
    source = data["sources"][0]
    pdf = root / ("manual.data" if local_only else "manual.pdf")
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    writer.write(pdf)
    raw = pdf.read_bytes()
    source.update(local_relative_path=pdf.name, source_format="pdf", sha256=hashlib.sha256(raw).hexdigest(),
                  byte_length=len(raw))
    if local_only:
        source["license"]["redistribution"] = "local_only"
    manifest.write_bytes(canonical_json_bytes(data))
    monkeypatch.setattr(corpus, "_pdf_page_texts", lambda reader: ["Weak form\n", "Boundary\n"])
    overlay = tmp_path / "overlay.json"
    overlay.write_bytes(canonical_json_bytes({"schema_version": 1, "chunks": [{
        "source_id": source["source_id"], "chunk_id": "combined", "section_path": ["Weak form"],
        "line_start": 1, "line_end": 2, "page_start": 1, "page_end": 2,
    }]}))
    out = tmp_path / "out"
    corpus.build_corpus(manifest, output_root=out, review_overlay_path=overlay)
    index, _ = corpus.load_corpus_bundle(out)
    spec = next(chunk.corpus_chunk for chunk in index.chunks if chunk.chunk_id == "combined")
    assert spec.page_breaks == (len("Weak form\n"),)

    plan = query_language.prepare_query("Weak form?")
    probe = query_language.build_retrieval_decision(plan, [spec])
    producer = ProducerIdentity("pytest", "1")
    review = ArtifactRefV1(id="review", role="source_review", path="review.json", sha256="d" * 64,
                           size=1, format_id="cae-source-review-v1", producer=producer)

    def assess(provenance, trace_provenance=None, *, start=0, end=len("Weak form")):
        span = {"candidate_id": spec.citation, "start": start, "end": end}
        event = {"kind": "retrieval_completion", "status": "completed"}
        if trace_provenance is not None:
            event["response"] = {"citations": [{**span, **trace_provenance}]}
        trace_bytes = canonical_json_bytes({
            "schema": "caelab.retrieval_trace.v1",
            "assessment_id": probe.assessment_id, "query_plan_sha256": probe.query_plan_sha256,
            "candidate_set_sha256": probe.candidate_set_sha256, "candidate_ids": [spec.citation],
            "events": [event],
        })
        (tmp_path / "trace.json").write_bytes(trace_bytes)
        trace_ref = ArtifactRefV1(id="trace", role="retrieval_trace", path="trace.json",
                                  sha256=hashlib.sha256(trace_bytes).hexdigest(), size=len(trace_bytes),
                                  format_id="cae-retrieval-trace-v1", producer=producer)
        quotes = [{**span, **provenance}]
        checks = [{"requirement_id": req.requirement_id, "relation": "satisfied", "reason": "page evidence",
                   "candidate_ids": [spec.citation], "quoted_spans": quotes} for req in probe.requirements]
        return query_language.build_retrieval_decision(
            plan, [spec], assessment_trace_ref=trace_ref, assessment_trace_root=tmp_path,
            assessment_profile_sha256="f" * 64, source_review_refs=[review],
            approved_source_reviews={spec.document_sha256: review}, assessment_result={
                "type": "retrieval_assessment", "checks": checks, "limitations": [],
                "out_of_scope": {"is_non_cae": False, "original_spans": [], "reason": ""},
            },
        )

    assert assess({"page": 1}).status == "supported"
    page_range = {"page_start": 1, "page_end": 1, "section_path": "Weak form"}
    assert assess(page_range, trace_provenance=page_range).status == "supported"
    assert assess({"page_start": 1, "page_end": 2}, end=len(spec.text)).status == "supported"
    for true_page, start, end in ((1, 0, len("Weak form")), (2, spec.page_breaks[0], len(spec.text))):
        with pytest.raises(ValueError, match="citation"):
            assess({"page_start": 1, "page_end": 2}, start=start, end=end)
        with pytest.raises(ValueError, match="citation"):
            assess({"page": true_page}, trace_provenance={"page_start": 1, "page_end": 2}, start=start, end=end)
    for invalid in ({}, {"page": None}, {"page": 2}, {"page_start": 2, "page_end": 1},
                    {"page": 2, **page_range}, {"page": 1, "section_path": ["Forged clause"]}):
        with pytest.raises(ValueError, match="citation"):
            assess(invalid)
        with pytest.raises(ValueError, match="citation"):
            assess({"page": 1}, trace_provenance=invalid)


def test_local_only_declared_markdown_preserves_headings(tmp_path):
    root, manifest, data = _make_sample_corpus(tmp_path)
    source = data["sources"][0]
    (root / source["local_relative_path"]).rename(root / "manual.data")
    source.update(local_relative_path="manual.data", source_format="markdown")
    source["license"]["redistribution"] = "local_only"
    manifest.write_bytes(canonical_json_bytes(data))
    out = tmp_path / "out"
    corpus.build_corpus(manifest, output_root=out)
    index, _ = corpus.load_corpus_bundle(out)
    assert any(chunk.corpus_chunk.source_id == source["source_id"]
               and "Drucker-Prager Plasticity and Dilation Angle" in chunk.corpus_chunk.section_path
               for chunk in index.chunks)


@pytest.mark.parametrize("defect", ["wrong_schema", "wrong_version", "wrong_discussion", "wrong_turn", "alias", "event", "chunk", "no_terminal"])
def test_saved_request_artifact_issues_block_history_and_store_copy(tmp_path, monkeypatch, defect):
    from caelab.native.run_store import RunStore

    root = tmp_path / "original.runs"
    path = root / "discussions" / "test" / "discussion.json"
    provider = _StubTheoryProvider("private previous reply")
    monkeypatch.setattr(experts, "get_provider", lambda _pid=None: provider)
    doc = discussions.create_discussion(path, question_scope="theory")
    asked = discussions.ask_discussion(
        path, "CalculiX 접촉 수렴 원리에서 비공개 이전 발언", template_id="theory",
        expected_revision=doc["revision"],
    )
    turn = asked["turns"][-1]
    assert turn["status"] == "completed" and turn["evidence_sources"] == ["knowledge"]
    assert discussions.discussion_issues(path) == []
    intact = tmp_path / "intact.runs"
    RunStore(root).copy_to(intact)
    assert not discussions.discussion_issues(intact / path.relative_to(root))
    assets = path.parent / "assets"

    def rewrite(ref, **fields):
        artifact = assets / ref["path"]
        raw = canonical_json_bytes(dict(json.loads(artifact.read_bytes()), **fields))
        artifact.write_bytes(raw)
        return dict(ref, size=len(raw), sha256=hashlib.sha256(raw).hexdigest())

    if defect == "alias":
        turn["request_artifact_sha256"] = "0" * 64
        expected = "request_artifact_ref"
    elif defect == "no_terminal":
        trace_ref = turn["tool_trace_ref"]
        trace = json.loads((assets / trace_ref["path"]).read_bytes())
        truncated = rewrite(trace_ref, events=trace["events"][:1])
        turn.update(tool_trace_ref=truncated, retrieval_trace_ref=truncated)
        expected = "종료 추적 이벤트"
    elif defect in ("event", "chunk"):
        trace = json.loads((assets / turn["retrieval_trace_ref"]["path"]).read_bytes())
        references = trace["events"] if defect == "event" else trace["chunks"]
        (assets / references[0]["path"]).unlink()
        expected = "tool_trace_ref"
    else:
        fields = {
            "wrong_schema": {"schema": "unknown"},
            "wrong_version": {"schema_version": 999},
            "wrong_discussion": {"discussion_id": "other"},
            "wrong_turn": {"turn_id": "other"},
        }[defect]
        updated_ref = rewrite(turn["request_context_ref"], **fields)
        turn.update(request_context_ref=updated_ref, request_artifact_ref=updated_ref,
                    request_artifact_sha256=updated_ref["sha256"])
        expected = "request_context_ref"
    path.write_bytes(canonical_json_bytes(asked))
    assert any(expected in issue for issue in discussions.discussion_issues(path))
    copied = tmp_path / "broken.runs"
    with pytest.raises(ValueError, match="요청 자산"):
        RunStore(root).copy_to(copied)
    assert not copied.exists()

    followed = discussions.ask_discussion(
        path, "CalculiX 약형 후속 질문", template_id="theory", expected_revision=asked["revision"],
    )
    assert followed["turns"][-1]["status"] == "completed"
    assert "private previous reply" not in provider.prompts[-1]
    assert "비공개 이전 발언" not in provider.prompts[-1]


@pytest.mark.parametrize("action", ["empty_import", "cancel_import_options", "cancel_capture"])
def test_cancelling_attachment_dialog_keeps_v1_unchanged(tmp_path, monkeypatch, action):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QDialog, QFileDialog
    from apps.workbench_qt.expert_window import ExpertWindow
    from caelab.native.run_store import NativeRun

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "v1.json"
    original = canonical_json_bytes(_v1_sample_document())
    path.write_bytes(original)
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat)
    wb = Workbench()
    window = ExpertWindow(path, workbench=wb, view_settings=settings)
    try:
        monkeypatch.setattr(QDialog, "exec", lambda _dialog: QDialog.Rejected)
        files = [] if action == "empty_import" else [str(tmp_path / "deck.inp")]
        monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_args: (files, ""))
        if action == "cancel_capture":
            run = NativeRun("candidate", "node", tmp_path, state="completed", input_path="deck.inp")
            monkeypatch.setattr(wb, "open_native_project", lambda *_args: object())
            monkeypatch.setattr(wb, "expert_evidence_run", lambda *_args: run)
            monkeypatch.setattr(wb, "native_runs", lambda *_args: [run])
            window.capture_run()
        else:
            window.import_files()
        assert path.read_bytes() == original
        assert not (tmp_path / "assets" / "migrations").exists()
    finally:
        window.close()
        wb.close()
        app.processEvents()


@pytest.mark.parametrize("mode", ["source_missing", "source_tampered", "vectors_nonfinite", "vector_meta_missing",
                                  "forged_quotation", "notice_missing", "notice_tampered",
                                  "code_page", "local_quotation", "local_line", "local_heading"])
def test_d09_index_integrity_against_manifest_even_after_digest_recomputed(tmp_path, mode):
    import io
    import shutil

    _, manifest_path, manifest_data = _make_sample_corpus(tmp_path)
    if mode in ("local_quotation", "local_line", "local_heading"):
        for source in manifest_data["sources"]:
            source["review_status"] = "approved"
        manifest_data["sources"][0]["license"]["redistribution"] = "local_only"
        manifest_path.write_bytes(canonical_json_bytes(manifest_data))
    receipt = corpus.build_corpus(manifest_path, output_root=tmp_path / "out")
    original = Path(receipt.index_dir)
    staged = tmp_path / "staged"
    shutil.copytree(original, staged)
    if mode == "source_missing":
        (staged / "sources" / "calculix_theory.md").unlink()
        error = "PACKAGED_SOURCE_MISSING"
    elif mode == "source_tampered":
        path = staged / "sources" / "calculix_theory.md"
        raw = path.read_bytes()
        path.write_bytes(b"x" + raw[1:])
        error = "SHA256_MISMATCH"
    elif mode in ("forged_quotation", "local_quotation", "local_line", "local_heading", "code_page"):
        path = staged / "chunks.json"
        payload = json.loads(path.read_bytes())
        quoted = payload["chunks"][0]
        if mode == "code_page":
            quoted.update(page_start=1, page_end=1)
            error = "PAGE_NOT_APPLICABLE"
        elif mode == "local_heading":
            quoted.update(section_path=["Forged clause 9.9"], heading_path=["Forged clause 9.9"])
            error = "LOCAL_CHUNK_DIGEST_MISMATCH"
        elif mode == "local_line":
            quoted.update(line_start=999, line_end=999 + quoted["line_end"] - quoted["line_start"])
            error = "LOCAL_CHUNK_DIGEST_MISMATCH"
        else:
            quoted["text"] = "x" + quoted["text"][1:]
            quoted["text_sha256"] = hashlib.sha256(quoted["text"].encode()).hexdigest()
            error = "LOCAL_CHUNK_DIGEST_MISMATCH" if mode == "local_quotation" else "PACKAGED_SOURCE_SPAN_MISMATCH"
        if mode in ("local_quotation", "local_line", "local_heading"):
            assert not (staged / "sources" / "calculix_theory.md").exists()
            assert corpus.load_corpus_bundle(original)[1]["verification_status"] == "verified_with_limitations"
        path.write_bytes(canonical_json_bytes(payload))
    elif mode in ("notice_missing", "notice_tampered"):
        notice = staged / "licenses" / "LICENSE"
        if mode == "notice_missing":
            notice.unlink()
            error = "LICENSE_NOTICE_MISSING"
        else:
            raw = notice.read_bytes()
            notice.write_bytes(b"x" + raw[1:])
            error = "SHA256_MISMATCH"
    elif mode == "vectors_nonfinite":
        path = staged / "vectors.npy"
        original_vectors = np.load(path, allow_pickle=False)
        original_vectors[0, 0] = np.nan
        buffer = io.BytesIO()
        np.save(buffer, original_vectors, allow_pickle=False)
        raw = buffer.getvalue()
        path.write_bytes(raw)
        meta_path = staged / "vector_meta.json"
        metadata = dict(json.loads(meta_path.read_bytes()), sha256=hashlib.sha256(raw).hexdigest())
        meta_path.write_bytes(canonical_json_bytes(metadata))
        error = "non-finite"
    else:
        (staged / "vector_meta.json").unlink()
        error = "vector_meta.json"
    manifest, _ = corpus.load_corpus_manifest(staged / "manifest.json")
    forged_digest = corpus._index_digest(manifest, corpus._dir_snapshot(staged))
    renamed = staged.rename(tmp_path / forged_digest)
    with pytest.raises((ValueError, FileNotFoundError), match=error):
        corpus.load_corpus_bundle(renamed)


def test_d09_lexical_only_review_gate_and_unknown_edition_filter(tmp_path, monkeypatch):
    _, manifest, data = _make_sample_corpus(tmp_path)
    out = tmp_path / "out"
    data["sources"][0].update(edition="unknown", review_status="review_required")
    manifest.write_bytes(canonical_json_bytes(data))
    receipt = corpus.build_corpus(manifest, output_root=out, with_vectors=False)
    index, meta = corpus.load_corpus_bundle(out, with_vectors=False)
    assert index.vectors is None and not (Path(receipt.index_dir) / "vectors.npy").exists()
    assert meta["verification_status"] == "verified_with_limitations"
    assert any(chunk.review_status == "review_required" for chunk in index.chunks)
    with pytest.raises(ValueError, match="vector mode"):
        corpus.load_corpus_bundle(out, with_vectors=True)
    monkeypatch.setenv("CAELAB_CORPUS_INDEX_DIR", str(out))
    assert knowledge.search_knowledge_result("CalculiX 2.22 *PLASTIC").entries == []
    assert knowledge.search_knowledge_result("CalculiX Drucker-Prager").entries
    data["sources"][0].pop("extraction_quality")
    data["sources"][0].update(extraction_grade="fail", review_status="approved")
    manifest.write_bytes(canonical_json_bytes(data))
    with pytest.raises(ValueError, match="SOURCE_EXTRACTION_FAILED"):
        corpus.build_corpus(manifest, output_root=out, verify_only=True)



def test_switch_to_theory_discards_edited_saved_native_framing(tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from apps.workbench_qt.expert_window import ExpertWindow

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "discussion.json"
    doc = discussions.create_discussion(path)
    framing = "Run log\nWarning: solver stopped\nMaximum stress 848 MPa"
    doc["draft_context"]["native_analysis_framing"] = framing
    doc["draft_question"] = framing.replace("Warning", "Important warning")
    stored = discussions.save_discussion(path, doc, expected_revision=doc["revision"])
    assert "848 MPa" in stored["draft_question"]
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat)
    window = ExpertWindow(path, view_settings=settings)
    try:
        window.scope_combo.setCurrentIndex(window.scope_combo.findData("theory"))
        assert window.question.toPlainText() == window.document["draft_question"] == ""
        assert window.save_document()
        saved = discussions.load_discussion(path)
        assert not saved["draft_question"] and "native_analysis_framing" not in saved["draft_context"]
    finally:
        window.close()
        app.processEvents()



def test_d08_pin_index_and_save_request_before_provider_availability(tmp_path, monkeypatch):
    sources, manifest, data = _make_sample_corpus(tmp_path)
    out = tmp_path / "out"
    index_a = corpus.build_corpus(manifest, output_root=out)
    pointer = out / "current.json"
    pointer_a = pointer.read_bytes()

    src = data["sources"][0]
    source_path = sources / src["local_relative_path"]
    changed = source_path.read_bytes().replace(b"trial pressure", b"new index pressure")
    source_path.write_bytes(changed)
    src.update(sha256=hashlib.sha256(changed).hexdigest(), byte_length=len(changed))
    data["revision"] = "switched"
    manifest.write_bytes(canonical_json_bytes(data))
    index_b = corpus.build_corpus(manifest, output_root=out)
    pointer_b = pointer.read_bytes()
    pointer.write_bytes(pointer_a)
    monkeypatch.setenv("CAELAB_CORPUS_INDEX_DIR", str(out))

    path = tmp_path / "discussion.json"
    doc = discussions.create_discussion(path, question_scope="theory")

    class Provider(_StubTheoryProvider):
        def availability(self):
            pending = json.loads(path.read_bytes())
            turn = pending["turns"][-1]
            stored = json.loads((path.parent / "assets" / turn["request_context_ref"]["path"]).read_bytes())
            assert turn["status"] == "pending"
            assert turn["request_artifact_ref"] == turn["request_context_ref"]
            assert turn["request_artifact_sha256"] == turn["request_context_ref"]["sha256"]
            assert turn["index_sha256"] == stored["index_sha256"] == index_a.index_digest
            pointer.write_bytes(pointer_b)
            return True, ""

        def discuss(self, prompt):
            raw_pending = path.read_bytes()
            self.start_ref = json.loads(raw_pending)["turns"][-1]["request_context_ref"]
            self.start_bytes = (path.parent / "assets" / self.start_ref["path"]).read_bytes()
            interrupted_path = path.with_name("interrupt.json")
            interrupted_path.write_bytes(raw_pending)
            recovered = discussions.load_discussion(interrupted_path)
            turn = recovered["turns"][-1]
            assert turn["status"] == "interrupted"
            assert turn["request_context_ref"]["sha256"] == turn["request_artifact_sha256"]
            assert not discussions.discussion_issues(interrupted_path, recovered)
            assert "trial pressure" in prompt and "new index pressure" not in prompt
            return super().discuss(prompt)

    provider = Provider()
    monkeypatch.setattr(experts, "get_provider", lambda *_args: provider)
    saved = discussions.ask_discussion(
        path, "CalculiX Drucker-Prager trial pressure", template_id="theory", expected_revision=doc["revision"],
    )
    assert saved["turns"][-1]["status"] == "completed", saved["turns"][-1]["error"]
    assert saved["turns"][-1]["corpus_revision"] == index_a.revision
    turn = saved["turns"][-1]
    assert turn["request_context_ref"] == provider.start_ref
    assert turn["request_artifact_sha256"] == provider.start_ref["sha256"]
    assets = path.parent / "assets"
    assert (assets / provider.start_ref["path"]).read_bytes() == provider.start_bytes
    frozen = json.loads(provider.start_bytes)
    assert frozen["evidence_materials"] == turn["provided_materials"]
    assert frozen["evidence_pack_hash"] == turn["evidence_pack_hash"]
    trace = json.loads((assets / turn["tool_trace_ref"]["path"]).read_bytes())
    event = json.loads((assets / trace["events"][0]["path"]).read_bytes())
    assert event["request_context_sha256"] == provider.start_ref["sha256"]
    assert knowledge.search_knowledge_result("Drucker-Prager trial pressure").index_digest == index_b.index_digest


@pytest.mark.parametrize("key", ["evidence_summary", "evidence_name", "evidence_refs",
                                 "evidence_sources", "evidence_pack_sha256", "evidence_pack_hash", "legacy_coordinated"])
def test_d08_case_attachment_mismatch_excluded_from_history_and_save_as(tmp_path, monkeypatch, key):
    from caelab.native.run_store import RunStore

    root = tmp_path / "run-store"
    path = root / "discussions" / "case.json"
    doc = discussions.create_discussion(path)
    log = tmp_path / "solver.log"
    log.write_text("synthetic solver output", encoding="utf-8")
    wb = Workbench()
    try:
        evidence = wb.import_expert_files(path.parent / "assets", files={"log": log}, solver_id="calculix")
    finally:
        wb.close()
    doc = discussions.attach_evidence(path, evidence, expected_revision=doc["revision"])
    provider = _StubTheoryProvider("sensitive previous reply")
    monkeypatch.setattr(experts, "get_provider", lambda *_args: provider)
    options = {"evidence_id": evidence["id"], "template_id": "next_test"}
    updated = discussions.ask_discussion(path, "First case", expected_revision=doc["revision"], **options)
    assert updated["turns"][-1]["status"] == "completed"
    assert not discussions.discussion_issues(path)
    turn = updated["turns"][-1]
    if key == "legacy_coordinated":
        from caelab.artifact_refs import canonical_sha256

        # Convert the saved record to the 26d4d44 wire format, which did not
        # record its final pack in the immutable context or trace header.
        assets = path.parent / "assets"

        def rewrite(ref, update):
            file = assets / ref["path"]
            payload = json.loads(file.read_bytes())
            update(payload)
            raw = canonical_json_bytes(payload)
            file.write_bytes(raw)
            return dict(ref, sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))

        def remove_pack(payload):
            for name in ("evidence_sources", "evidence_materials", "shown_ids", "evidence_pack_hash"):
                del payload[name]

        def rewrite_trace(payload):
            payload["request_context_ref"] = context
            events = payload["events"]
            events[0] = rewrite(events[0], lambda event: event.update(request_context_sha256=context["sha256"]))
            events[1] = rewrite(events[1], lambda event: event.update(previous_event_sha256=events[0]["sha256"]))

        context = rewrite(turn["request_context_ref"], remove_pack)
        trace = rewrite(turn["tool_trace_ref"], rewrite_trace)
        turn.update(request_context_ref=context, request_artifact_ref=context, request_artifact_sha256=context["sha256"],
                    tool_trace_ref=trace, retrieval_trace_ref=trace)
        turn.pop("evidence_pack_sha256")

        def rehash():
            turn["evidence_pack_hash"] = canonical_sha256({
                "request_context_sha256": context["sha256"],
                "evidence_sources": turn["evidence_sources"],
                "provided_materials": turn["provided_materials"],
                "shown_ids": turn["citations"]["shown_ids"],
            })

        rehash()
        assert not discussions.discussion_issues(path, updated)
        turn["evidence_sources"].remove("solver_log")
        turn["provided_materials"] = [m for m in turn["provided_materials"] if m["source"] != "solver_log"]
        rehash()
        options["template_id"] = "interpretation"
        diagnostic = "evidence_pack"
    elif key.startswith("evidence_pack") or key == "evidence_sources":
        frozen = json.loads((path.parent / "assets" / turn["request_context_ref"]["path"]).read_bytes())
        assert turn["evidence_sources"] == frozen["evidence_sources"]
        assert turn["evidence_pack_sha256"] == frozen["evidence_pack_hash"]
        assert len(turn["evidence_sources"]) > 1
        turn[key] = turn[key][:-1] if key == "evidence_sources" else "0" * 64
        diagnostic = "evidence_pack"
    else:
        turn[key] = []
        diagnostic = "첨부 근거 식별 정보"
    path.write_bytes(canonical_json_bytes(updated))
    assert any(diagnostic in issue for issue in discussions.discussion_issues(path))
    target = tmp_path / "invalid-copy"
    with pytest.raises(ValueError, match="요청 자산"):
        RunStore(root).copy_to(target)
    assert not target.exists()
    followed = discussions.ask_discussion(path, "Second case", expected_revision=updated["revision"], **options)
    assert followed["turns"][-1]["status"] == "completed"
    assert "sensitive previous reply" not in provider.prompts[-1]


@pytest.mark.parametrize("editions", [("2.23", "2.24"), ("2.24", "2.23")])
@pytest.mark.parametrize("comparison", [
    "GEOS 2.23 and CCX 2.24",
    "2.23 (GEOS) vs 2.24 (CCX)",
    "v2.23 in GEOS and v2.24 in CCX",
])
def test_d09_corpus_search_matches_solver_and_edition_together(tmp_path, editions, comparison):
    _, manifest, data = _make_sample_corpus(tmp_path)
    for src, solver, edition in zip(data["sources"], ("geos", "calculix"), editions):
        src.update(solver_scope=[solver], edition=edition)
    manifest.write_bytes(canonical_json_bytes(data))
    out = tmp_path / "index"
    corpus.build_corpus(manifest, output_root=out)
    _, hits, decision = corpus.search_corpus(out, f"Compare {comparison}: weak form and Drucker-Prager")
    assert [req.expected for req in decision.requirements if req.facet == "solver"] == ["GEOS", "CCX"]
    assert len([req for req in decision.requirements if req.facet == "edition"]) == 2
    if editions == ("2.23", "2.24"):
        assert {(hit.coverage_facets["solver"][0], hit.edition) for hit in hits} == {
            ("geos", "2.23"), ("calculix", "2.24"),
        }
    else:
        assert not hits
        assert "edition_mismatch" in decision.missing_evidence


def test_d09_verify_only_writes_no_temporary_directories(tmp_path, monkeypatch):
    _, manifest, _ = _make_sample_corpus(tmp_path)
    out = tmp_path / "existing"
    corpus.build_corpus(manifest, output_root=out)
    def forbidden(*_args, **_kwargs):
        pytest.fail("verify-only attempted to create a temporary directory")
    monkeypatch.setattr(corpus.tempfile, "TemporaryDirectory", forbidden)
    monkeypatch.setattr(corpus.tempfile, "mkdtemp", forbidden)
    target = tmp_path / "read-only-target"
    verified = corpus.build_corpus(manifest, output_root=target, verify_only=True, with_vectors=True)
    assert verified.verify_only and not target.exists()
    assert corpus.load_corpus_bundle(out, with_vectors=True)[0].vectors is not None


@pytest.mark.parametrize("question", [
    "CalculiX versions 2.22 and 16.5: compare weak form and Drucker-Prager",
    "Compare 2.22 vs 16.5 in CalculiX: weak form and Drucker-Prager",
    "Compare 2.22 and 16.5 of CCX: weak form and Drucker-Prager",
])
def test_d09_comparison_editions_and_unmentioned_facet_spans(tmp_path, question):
    _, manifest, data = _make_sample_corpus(tmp_path)
    data["sources"][1]["solver_scope"] = ["calculix"]
    manifest.write_bytes(canonical_json_bytes(data))
    corpus.build_corpus(manifest, output_root=tmp_path / "out")
    plan, hits, decision = corpus.search_corpus(tmp_path / "out", question)
    editions = [req for req in decision.requirements if req.facet == "edition"]
    assert [req.expected for req in editions] == ["2.22", "16.5"]
    if "CCX" in question:
        alias = next(span for span in plan.term_spans if span["matched_term"] == "calculix")
        assert alias["source_text"] == question[alias["start"]:alias["end"]] == "CCX"
    assert {hit.edition for hit in hits} == {"2.22", "16.5"}
    assert "edition_mismatch" not in decision.missing_evidence
    one_edition = [hit for hit in hits if hit.edition == "2.22"]
    assert "edition_mismatch" in query_language.build_retrieval_decision(plan, one_edition).missing_evidence
    facets = {"solver": "abaqus", "model": "Norton", "edition": "2.24"}
    reqs = query_language.extract_query_requirements(query_language.prepare_query("Weak form?"), facets)
    assert all(req.original_span is None for req in reqs if req.facet in facets)


@pytest.mark.parametrize(("question", "solver"), [
    ("How do I extract displacement at node 42 in CalculiX?", "CalculiX"),
    ("Compare frames 42 and 43 in CalculiX", "CalculiX"),
    ("Do I need law 1 (CalculiX)?", "CalculiX"),
    ("절점 42 in CalculiX", "CalculiX"),
    ("Section 2.3 in CalculiX", "CalculiX"),
    ("Figure 1.2 in CalculiX", "CalculiX"),
    ("Equation 3.4 in Radioss", "Radioss"),
    ("See fig. 1.2 (CalculiX)", "CalculiX"),
    ("절 2.3 in CalculiX", "CalculiX"),
    ("Sections 2.3 and 2.4 in CalculiX", "CalculiX"),
    ("Sections 2.3 & 2.4 in CalculiX", "CalculiX"),
    ("Sections 2.3, 2.4 and 2.5 in CalculiX", "CalculiX"),
    ("Sections 2.3 to 2.5 in CalculiX", "CalculiX"),
    ("Sections 2.3-2.5 in CalculiX", "CalculiX"),
])
def test_d09_entity_numbers_are_not_solver_editions(question, solver):
    plan = query_language.prepare_query(question)
    requirements, owners = query_language.extract_query_scope(plan)
    assert any(req.facet == "solver" and req.expected == solver for req in requirements)
    assert not owners
    assert not any(req.facet == "edition" for req in requirements)


@pytest.mark.parametrize("connector", ["and", "&", "vs", "vs.", "versus"])
def test_d09_section_list_preserves_search_hits_for_both_editions(tmp_path, connector):
    _, manifest, data = _make_sample_corpus(tmp_path)
    for source, edition in zip(data["sources"], ("2.23", "2.24")):
        source.update(solver_scope=["calculix"], edition=edition)
    manifest.write_bytes(canonical_json_bytes(data))
    index_root = tmp_path / "index"
    corpus.build_corpus(manifest, output_root=index_root)
    _, hits, decision = corpus.search_corpus(
        index_root, f"Compare sections 2.3 {connector} 2.4 in CalculiX: weak form and Drucker-Prager",
    )
    assert {hit.edition for hit in hits} == {"2.23", "2.24"}
    assert not any(req.facet == "edition" for req in decision.requirements)


@pytest.mark.parametrize(("question", "editions"), [
    ("Compare 2.24 (GEOS) vs 2.23 (CCX) contact", {"geos": "2.24", "calculix": "2.23"}),
    ("Compare v2.23 in GEOS and v2.24 in CCX", {"geos": "2.23", "calculix": "2.24"}),
])
def test_d09_edition_first_comparisons_keep_explicit_solver_pairs(question, editions):
    requirements, owners = query_language.extract_query_scope(query_language.prepare_query(question))
    assert {owners[req.requirement_id]: req.expected.lower().removeprefix("v")
            for req in requirements if req.facet == "edition"} == editions


@pytest.mark.parametrize("phrase", ["release 42 in CalculiX", "V42 (CalculiX)"])
def test_d09_explicit_version_marker_allows_integer_edition(phrase):
    plan = query_language.prepare_query(f"Compare the contact behavior of {phrase}")
    requirements, owners = query_language.extract_query_scope(plan)
    [edition] = [req for req in requirements if req.facet == "edition"]
    assert query_language.editions_match(edition.expected, "42")
    assert owners == {edition.requirement_id: "calculix"}


def test_d09_units_scope_edges_and_legacy_labels():
    for unit in "bar mbar psi ksi atm rpm deg rad kN MN mJ kJ W kW C".split():
        plan = query_language.prepare_query(f"CalculiX 10 {unit} load")
        assert not any(req.facet == "edition" for req in query_language.extract_query_requirements(plan)), unit
    for question in ("Cook's membrane benchmark", "Why does a beam translate?"):
        assert not query_language.classify_non_cae_intent(query_language.prepare_query(question))[0]
    entry = knowledge._to_entry(rag.Chunk("chunk", "file", ("section",), "Some text"))
    assert entry.source_kind == "legacy_unknown"
