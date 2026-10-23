"""Expert discussion — the workbench's second half.

The experts read evidence and propose; they never apply or run
anything (the user presses those buttons), and they never invent
numbers: the only figures in a prompt are the ones in the result
summary and model summary handed to them.

Providers sit behind one interface. Nothing is configured by default:
- ``openscience``: the ChatGPT-subscription route used on the user's
  Windows machine (a pinned local profile). It reports unavailable
  anywhere that profile does not exist.
- ``openai_compatible``: any OpenAI-compatible endpoint (a local LLM
  included), enabled only when the user sets the endpoint env vars.
  It is never enabled implicitly, so no paid API use can sneak in.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .knowledge import (KnowledgeSearchResult, citation_id, infer_solver,
                        search_knowledge_result, verify_citations)


# ---------------------------------------------------------------- templates
@dataclass(frozen=True)
class ExpertTemplate:
    id: str
    name: str
    instructions: str
    allowed_tools: tuple[str, ...]  # evidence sources this role may read


TEMPLATES: tuple[ExpertTemplate, ...] = (
    ExpertTemplate(
        "convergence",
        "수렴·접촉 진단",
        "계산 로그와 수렴 이력을 근거로 실패·경고 원인을 찾고, "
        "모델에서 고칠 항목을 제안합니다.",
        ("result_summary", "solver_log", "model_summary", "knowledge"),
    ),
    ExpertTemplate(
        "material",
        "재료 모델 선택",
        "현재 재료 법칙과 계수가 해석 목적에 맞는지 근거와 함께 논의하고, "
        "대안을 제안합니다.",
        ("result_summary", "model_summary", "knowledge"),
    ),
    ExpertTemplate(
        "interpretation",
        "결과 해석",
        "결과 요약의 수치만 근거로 물리적 의미를 해석하고, "
        "검증 상태의 한계를 분명히 밝힙니다.",
        ("result_summary",),
    ),
    ExpertTemplate(
        "next_test",
        "다음 시험 계획",
        "이번 결과에서 확인하지 못한 것을 다음 계산·시험으로 "
        "어떻게 확인할지 계획을 제안합니다.",
        ("result_summary", "solver_log"),
    ),
)


def get_template(template_id: str) -> ExpertTemplate:
    for template in TEMPLATES:
        if template.id == template_id:
            return template
    raise KeyError(f"unknown expert template: {template_id}")


# ---------------------------------------------------------------- proposals
@dataclass(frozen=True)
class Proposal:
    """One suggested change: target path plus before/after values.

    This is the only shape a suggestion may take. Applying it is a
    separate, user-triggered step in the GUI.
    """

    target_path: str
    before: object
    after: object
    reason: str = ""


_PROPOSAL_BLOCK = re.compile(r"```proposals\s*(.*?)```", re.DOTALL)


def parse_proposals(reply_text: str) -> list[Proposal]:
    """Extract validated proposals from a fenced ```proposals JSON block.

    Malformed entries are dropped, never guessed at.
    """
    match = _PROPOSAL_BLOCK.search(reply_text or "")
    if not match:
        return []
    try:
        raw = json.loads(match.group(1))
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []
    proposals: list[Proposal] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = item.get("target_path")
        if not isinstance(path, str) or not path or "before" not in item or "after" not in item:
            continue
        proposals.append(
            Proposal(
                target_path=path,
                before=item["before"],
                after=item["after"],
                reason=str(item.get("reason", "")),
            )
        )
    return proposals


# ---------------------------------------------------------------- providers
class ExpertProvider:
    """One discussion backend. ``discuss`` returns the raw reply text."""

    id = "base"
    name = "전문가 공급자"

    def availability(self) -> tuple[bool, str]:
        return False, "구성되지 않았습니다."

    def discuss(self, prompt: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class UnavailableProvider(ExpertProvider):
    id = "none"
    name = "연결된 전문가 없음"

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def availability(self) -> tuple[bool, str]:
        return False, self._reason

    def discuss(self, prompt: str) -> str:
        raise RuntimeError(self._reason)


class OpenScienceProvider(ExpertProvider):
    """Reserved subscription route; the host transport is not implemented.

    A profile is configuration evidence, not an executable provider.
    Keep this unavailable until a real transport can send the prompt.
    """

    id = "openscience"
    name = "OpenScience (ChatGPT 구독)"

    def _profile_root(self) -> Path | None:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            return None
        base = Path(local) / "AutonomousCAELab" / "profiles"
        if not base.is_dir():
            return None
        for child in sorted(base.iterdir()):
            if (child / "caelab-profile-owner.json").is_file():
                return child
        return None

    def availability(self) -> tuple[bool, str]:
        if self._profile_root() is not None:
            return False, "OpenScience 프로필은 있지만 구독 실행 연결은 아직 구현되지 않았습니다."
        return False, (
            "OpenScience 구독 실행 연결은 아직 구현되지 않았습니다. "
            "이 컴퓨터에서는 연결 프로필도 찾을 수 없습니다."
        )

    def discuss(self, prompt: str) -> str:
        _ok, reason = self.availability()
        raise RuntimeError(reason)


class OpenAICompatibleProvider(ExpertProvider):
    """OpenAI-compatible chat endpoint (local LLM included).

    Enabled only by explicit env configuration:
    CAELAB_EXPERT_ENDPOINT (e.g. http://localhost:11434/v1),
    CAELAB_EXPERT_MODEL, and CAELAB_EXPERT_API_KEY when the endpoint
    needs one.
    """

    id = "openai_compatible"
    name = "OpenAI 호환 엔드포인트"

    def __init__(self) -> None:
        self.endpoint = os.environ.get("CAELAB_EXPERT_ENDPOINT", "").rstrip("/")
        self.model = os.environ.get("CAELAB_EXPERT_MODEL", "")
        self.api_key = os.environ.get("CAELAB_EXPERT_API_KEY", "")

    def availability(self) -> tuple[bool, str]:
        if not self.endpoint or not self.model:
            return False, (
                "엔드포인트가 설정되지 않았습니다 "
                "(CAELAB_EXPERT_ENDPOINT, CAELAB_EXPERT_MODEL 환경변수)."
            )
        return True, f"엔드포인트 {self.endpoint}, 모델 {self.model}"

    def discuss(self, prompt: str) -> str:
        ok, reason = self.availability()
        if not ok:
            raise RuntimeError(reason)
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "당신은 CAE 전문가입니다. 주어진 근거 안의 수치만 인용하고, 계산값을 새로 만들지 마세요."},
                    {"role": "user", "content": prompt},
                ],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.endpoint}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self.api_key:
            request.add_header("Authorization", f"Bearer {self.api_key}")
        with urllib.request.urlopen(request, timeout=300) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


def get_provider(provider_id: str | None = None) -> ExpertProvider:
    """Resolve the configured provider; default is honestly unavailable."""
    chosen = provider_id or os.environ.get("CAELAB_EXPERT_PROVIDER", "")
    if chosen == "openscience":
        return OpenScienceProvider()
    if chosen == "openai_compatible":
        return OpenAICompatibleProvider()
    if chosen:
        return UnavailableProvider(f"알 수 없는 전문가 공급자입니다: {chosen}")
    return UnavailableProvider(
        "설정된 전문가 공급자가 없습니다. CAELAB_EXPERT_PROVIDER 환경변수로 "
        "openscience 또는 openai_compatible을 지정하세요. 임의로 유료 API를 "
        "사용하지 않습니다."
    )


def summarize_model(model) -> str:
    """Plain model facts for the templates allowed to read them.

    Counts and declared values only — nothing computed here.
    """
    lines = [f"모델 {model.name} (단위계 {model.unit_system})"]
    lines.append(
        f"절점 {len(model.mesh.nodes):,}개, 요소 {len(model.mesh.elements):,}개"
    )
    for m in model.materials:
        params = ", ".join(f"{k}={v}" for k, v in m.params.items())
        lines.append(f"재료 {m.name} ({m.law}): {params}")
    for step in model.steps:
        lines.append(f"해석 단계 {step.name}: {step.kind}")
    for ld in model.loads:
        lines.append(f"하중 {ld.name} ({ld.kind}, 대상 {ld.target_set}): {ld.values}")
    for bc in model.bcs:
        lines.append(f"구속 {bc.name} (대상 {bc.target_set}): 고정 방향 {bc.fixed_dofs}")
    return "\n".join(lines)


# ---------------------------------------------------------------- discussion
@dataclass
class Discussion:
    """A grounded discussion thread over one result summary.

    Follow-ups keep the earlier context: previous questions, replies
    and rejected/failed proposals stay in the prompt, so a new result
    never silently wipes what was already discussed.
    """

    template: ExpertTemplate
    summary_text: str  # formatted result summary — the evidence
    model_text: str = ""
    log_tail: str = ""
    provider: ExpertProvider = field(default_factory=get_provider)
    history: list = field(default_factory=list)  # [(question, reply)]
    proposals: list = field(default_factory=list)  # accumulated Proposal
    # Context that must survive follow-ups (design A2/A3): the user's
    # own constraints and why earlier candidates failed. A new result
    # attached later never clears these.
    constraints: list = field(default_factory=list)  # [str]
    failures: list = field(default_factory=list)  # [str]
    knowledge_ids: list = field(default_factory=list)  # citation ids actually shown
    solver_id: str = ""
    knowledge_result: KnowledgeSearchResult | None = field(default=None, init=False)

    def build_prompt(self, question: str) -> str:
        parts = [
            f"역할: {self.template.name}. {self.template.instructions}",
            "",
            "[근거 — 결과 요약]",
            self.summary_text,
        ]
        if "model_summary" in self.template.allowed_tools and self.model_text:
            parts += ["", "[근거 — 모델 요약]", self.model_text]
        if "solver_log" in self.template.allowed_tools and self.log_tail:
            parts += ["", "[근거 — 솔버 로그 끝부분]", self.log_tail[-1500:]]
        knowledge_shown = False
        if "knowledge" in self.template.allowed_tools:
            self.knowledge_result = search_knowledge_result(
                f"{question} {self.summary_text}",
                solver=self.solver_id or infer_solver(self.summary_text))
            entries = self.knowledge_result.entries
            if entries:
                knowledge_shown = True
                parts += ["", "[근거 — 솔버 매뉴얼 지식 팩]"]
                for entry in entries:
                    cid = citation_id(entry)
                    if cid not in self.knowledge_ids:
                        self.knowledge_ids.append(cid)
                    parts.append(f"[{cid}] {entry.topic}: {entry.text}")
            elif self.knowledge_result.status == "error":
                parts += ["", "[지식 검색 오류 — 매뉴얼 근거를 가져오지 못함]",
                          self.knowledge_result.error or "검색 원인 미확인"]
            else:
                parts += ["", f"[지식 자료 없음 — 솔버: {self.knowledge_result.solver or '미확인'}]"]
        if self.constraints:
            parts += ["", "[사용자 제약 — 이전 논의에서 유지]"]
            parts += [f"- {c}" for c in self.constraints]
        if self.failures:
            parts += ["", "[이전 후보가 실패한 이유 — 이전 논의에서 유지]"]
            parts += [f"- {f}" for f in self.failures]
        if self.history:
            parts += ["", "[이전 논의]"]
            for q, a in self.history:
                parts += [f"질문: {q}", f"답변: {a}"]
        parts += [
            "",
            f"질문: {question}",
            "",
            *(
                [
                    "매뉴얼 내용을 인용할 때는 위에 실제로 나온 "
                    "[근거: <자료>:<조각 id>] 형식으로만 인용하세요. "
                    "읽지 않은 자료는 인용하지 마세요."
                ]
                if knowledge_shown
                else []
            ),
            "변경 제안이 있으면 답변 끝에 ```proposals 코드 블록으로 "
            'JSON 목록 [{"target_path": "loads[0].values.fy", "before": -1000.0, '
            '"after": -500.0, "reason": "..."}] 형식으로만 적으세요. '
            "적용과 실행은 사용자가 직접 누릅니다.",
        ]
        return "\n".join(parts)

    def ask(self, question: str) -> dict:
        """Ask once. Returns {available, reply, proposals,
        unverified_citations} — an unavailable provider is a state,
        not an exception. Citations are checked against the knowledge
        entries this discussion actually showed the expert."""
        ok, reason = self.provider.availability()
        if not ok:
            return {"available": False, "reply": reason, "proposals": [],
                    "unverified_citations": []}
        reply = self.provider.discuss(self.build_prompt(question))
        proposals = parse_proposals(reply)
        unverified = verify_citations(reply, self.knowledge_ids)
        self.history.append((question, reply))
        self.proposals.extend(proposals)
        return {"available": True, "reply": reply, "proposals": proposals,
                "unverified_citations": unverified,
                "knowledge_status": self.knowledge_result.status if self.knowledge_result else "not_requested",
                "knowledge_error": self.knowledge_result.error if self.knowledge_result else None,
                "knowledge_solver": self.knowledge_result.solver if self.knowledge_result else ""}
