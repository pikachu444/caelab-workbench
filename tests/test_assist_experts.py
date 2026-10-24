import pytest

from caelab.assist import knowledge, rag
from caelab.assist.experts import (
    TEMPLATES,
    Discussion,
    ExpertProvider,
    get_provider,
    get_template,
    parse_proposals,
    summarize_model,
)
from caelab.solvers.base import ResultSet
from caelab.result_summary import format_summary_ko, summarize_result
from conftest import cantilever_model


@pytest.fixture(autouse=True)
def offline_knowledge(monkeypatch):
    monkeypatch.setattr(rag, "get_embedder", rag.HashingEmbedder)
    monkeypatch.setattr(rag, "get_reranker", rag.LexicalReranker)
    monkeypatch.setattr(knowledge, "default_index",
                        lambda: rag.HybridIndex.build(knowledge.notes_chunks(), rag.HashingEmbedder()))


class FakeProvider(ExpertProvider):
    id = "fake"
    name = "시험용 공급자"

    def availability(self):
        return True, "시험용"

    def discuss(self, prompt):
        self.last_prompt = prompt
        return (
            "반력이 하중과 평형을 이룹니다.\n"
            "```proposals\n"
            '[{"target_path": "loads[0].values.fy", "before": -1000.0, '
            '"after": -500.0, "reason": "하중 절반으로 민감도 확인"}]\n'
            "```"
        )


def test_templates_are_four_and_tools_actually_differ():
    assert len(TEMPLATES) == 4
    tool_sets = {t.allowed_tools for t in TEMPLATES}
    assert len(tool_sets) == 4  # each role may read different evidence
    assert get_template("interpretation").allowed_tools == ("result_summary",)


def test_default_provider_is_honestly_unavailable(monkeypatch):
    monkeypatch.delenv("CAELAB_EXPERT_PROVIDER", raising=False)
    provider = get_provider()
    ok, reason = provider.availability()
    assert not ok
    assert "유료 API" in reason  # never silently spend on an API


def test_explicit_none_does_not_fall_back_to_environment_provider(monkeypatch):
    monkeypatch.setenv("CAELAB_EXPERT_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CAELAB_EXPERT_ENDPOINT", "http://localhost.invalid/v1")
    monkeypatch.setenv("CAELAB_EXPERT_MODEL", "configured")
    provider = get_provider("none")
    assert provider.id == "none"
    assert provider.availability() == (False, "선택한 전문가 공급자가 없습니다.")


def test_discussion_is_grounded_and_keeps_history():
    result = ResultSet(
        solver_id="calculix", success=True, max_displacement=0.0001905,
        reaction_totals={"fx": 0.0, "fy": 999.996, "fz": 0.0},
    )
    summary_text = format_summary_ko(summarize_result(result))
    provider = FakeProvider()
    discussion = Discussion(
        template=get_template("convergence"),
        summary_text=summary_text,
        model_text=summarize_model(cantilever_model()),
        log_tail="solver log tail",
        provider=provider,
    )
    outcome = discussion.ask("평형이 맞나요?")
    assert outcome["available"]
    # the prompt carries only evidence numbers, incl. the measured reaction
    assert "999" in provider.last_prompt or "1,000" in provider.last_prompt
    assert "solver log tail" in provider.last_prompt  # template may read logs
    assert len(outcome["proposals"]) == 1
    assert outcome["proposals"][0].after == -500.0
    # follow-up keeps the earlier exchange in context
    discussion.ask("이어서 묻습니다")
    assert "평형이 맞나요?" in provider.last_prompt


def test_interpretation_template_cannot_read_log():
    provider = FakeProvider()
    discussion = Discussion(
        template=get_template("interpretation"),
        summary_text="요약",
        log_tail="SECRET LOG",
        provider=provider,
    )
    discussion.ask("해석해 주세요")
    assert "SECRET LOG" not in provider.last_prompt


def test_parse_proposals_drops_malformed_entries():
    text = (
        "```proposals\n"
        '[{"target_path": "a.b", "before": 1, "after": 2},'
        ' {"target_path": 3, "before": 1, "after": 2},'
        ' {"before": 1, "after": 2}]\n```'
    )
    proposals = parse_proposals(text)
    assert len(proposals) == 1
    assert proposals[0].target_path == "a.b"
    assert parse_proposals("제안이 없는 답변") == []
    assert parse_proposals("```proposals\nnot json\n```") == []


def test_unavailable_discussion_is_a_state_not_an_exception(monkeypatch):
    monkeypatch.delenv("CAELAB_EXPERT_PROVIDER", raising=False)
    discussion = Discussion(template=get_template("material"), summary_text="요약")
    outcome = discussion.ask("재료가 맞나요?")
    assert outcome["available"] is False
    assert outcome["proposals"] == []


def test_openscience_does_not_guess_a_profile_or_claim_authentication(tmp_path, monkeypatch):
    profile = tmp_path / "AutonomousCAELab" / "profiles" / "test"
    profile.mkdir(parents=True)
    (profile / "caelab-profile-owner.json").write_text("{}")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    provider = get_provider("openscience")
    discussion = Discussion(template=get_template("material"),
                            summary_text="요약", provider=provider)
    # An arbitrary marker is not the operator's configured login/runtime.
    outcome = discussion.ask("재료가 맞나요?")
    assert outcome["available"] is False
    assert discussion.history == []
    with pytest.raises(ValueError):
        provider.discuss("직접 호출")


def test_discussion_uses_result_solver_even_when_question_names_another():
    provider = FakeProvider()
    discussion = Discussion(template=get_template("material"),
                            summary_text="code_aster 계산 완료", provider=provider)
    outcome = discussion.ask("CalculiX 반력 자료로 설명해 주세요")
    assert outcome["knowledge_status"] == "no_material"
    assert outcome["knowledge_solver"] == "code_aster"
    assert outcome["knowledge_error"] is None
    assert "지식 자료 없음" in provider.last_prompt
    assert "calculix-manual" not in provider.last_prompt
    assert discussion.knowledge_ids == []


def test_discussion_passes_explicit_solver_and_keeps_retrieval_error(monkeypatch):
    def broken():
        raise OSError("index damaged")
    monkeypatch.setattr(knowledge, "default_index", broken)
    provider = FakeProvider()
    discussion = Discussion(template=get_template("material"),
                            summary_text="calculix 결과", provider=provider,
                            solver_id="fenicsx")
    outcome = discussion.ask("재료를 설명해 주세요")
    assert outcome["available"]
    assert outcome["knowledge_status"] == "error"
    assert outcome["knowledge_solver"] == "fenicsx"
    assert outcome["knowledge_error"] == "OSError: index damaged"
    assert "지식 검색 오류" in provider.last_prompt
    assert "index damaged" in provider.last_prompt
    assert "지식 자료 없음" not in provider.last_prompt
    assert discussion.knowledge_ids == []


def test_current_request_cannot_approve_a_citation_from_previous_retrieval(monkeypatch):
    from caelab.assist import experts
    entry = knowledge.KnowledgeEntry("old", "old topic", (), "old material", source_id="manual")
    outcomes = iter([knowledge.KnowledgeSearchResult("ok", [entry], "calculix"),
                     knowledge.KnowledgeSearchResult("no_material", [], "calculix")])
    monkeypatch.setattr(experts, "search_knowledge_result", lambda *args, **kwargs: next(outcomes))
    provider = FakeProvider()
    provider.discuss = lambda prompt: "[근거: manual:old]"
    discussion = Discussion(template=get_template("material"), summary_text="calculix", provider=provider)
    assert discussion.ask("first")["unverified_citations"] == []
    assert discussion.ask("second")["unverified_citations"] == ["manual:old"]
    assert discussion.knowledge_ids == []


def test_openai_compatible_provider_uses_configured_finite_timeout(monkeypatch):
    import io
    from caelab.assist import experts
    monkeypatch.setenv("CAELAB_EXPERT_ENDPOINT", "http://localhost.invalid/v1")
    monkeypatch.setenv("CAELAB_EXPERT_MODEL", "mock")
    calls = []
    def open_mock(request, *, timeout):
        calls.append(timeout)
        return io.BytesIO(b'{"choices":[{"message":{"content":"mock reply"}}]}')
    monkeypatch.setattr(experts.urllib.request, "urlopen", open_mock)
    provider = experts.OpenAICompatibleProvider()
    provider.timeout_seconds = 60
    assert provider.discuss("mock") == "mock reply"
    assert calls == [60]
