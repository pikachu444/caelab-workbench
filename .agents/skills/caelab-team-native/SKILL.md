---
name: caelab-team-native
description: >-
  Activate when running the Antigravity-native subagent team preset in caelab-workbench
  (Orchestrator Neon Team Lead + Gemini 4 Hydrogen & Gemini 4 Neon XHigh/High subagents/swarms + chief-reviewer + cae-user-expert + gui-launcher-dev + gui-apps-dev + gui-aux-runner).
---

# CAELAB 프로젝트 팀 구성안 1 — Antigravity 순정 팀 (Orchestrator Neon × Hydrogen & Neon)

공통 개발·검토·비판 해소 규칙은 루트 [AGENTS.md](../../../AGENTS.md), [DESIGN.md](../../../DESIGN.md), [GEMINI_LEAD_METHODOLOGY.md](../../../docs/GEMINI_LEAD_METHODOLOGY.md)를 그대로 따릅니다.

## 권장 모델 계층 (Antigravity 2-Phase + 5시간 롤링 쿼터 조합)
- **총괄 (`Orchestrator (Neon)` / `gemini-pro-orchestrator-neon`)**: Plan/Review 턴(Plan Mode)에서 Neon이 아키텍처·`Concrete Spec`·게이트 판정을 내리고, 실행 턴(`exit_plan_mode`)은 실행 모델로 전환해 효율적으로 조율합니다.
- **서브 리드 / 적대적 코드 QA (`Gemini 4 Hydrogen (XHigh)` 또는 `Gemini 4 Neon (XHigh)` / `swarm` PIN)**: 5시간 단기 롤링 쿼터를 활용해 부팀장급 명세 구체화, 수식·재료 구성방정식 검증, `chief-reviewer` 감사를 수행합니다.
- **병렬 구현 워커 / 실화면 UX QA (`Gemini 4 Hydrogen (High)` 또는 `Gemini 4 Neon (High)` / `invoke_subagent`)**: 격리 `git worktree` (또는 `swarm workspace:"share"`) 구현 및 `cae-user-expert` 1440×900 실캡처 시각 감수를 수행합니다 (`invoke_subagent` 슬롯 제약 시 `inherit`/`pro`, Swarm 사용 시 `gemini-4-hydrogen-high`/`xhigh` 및 `gemini-4-neon-high`/`xhigh` 직접 지정).
- **경량 탐색·로그 보조 (`Gemini 4 Hydrogen (Low/Medium)` 또는 `Gemini 3.8 Flash` / `Model: "flash"`)**: 파일 검색, CI 로그 요약, 반복 정적 검사 보조.
- **Argon 제외 정책**: `Orchestrator (Argon)` (`gemini-pro-orchestrator-g1-pro`) 및 `Gemini 4 Argon`은 현재 활성 팀 구성에서 제외합니다.

## 영구 등록된 프로젝트 에이전트 (`.agents/agents/`)
- `chief-reviewer` (XHigh / `"inherit"`): 부팀장 겸 독립 아키텍처·회귀 감사관 (Gate 1 범위 감사, Gate 2 인도 감사)
- `cae-user-expert` (Vision High / `"pro"`): 실무 CAE/FEA 엔지니어 대표 심사관 (현재 프로젝트의 실제 입력·진단 및 1440×900 실화면 캡처 UX 감수)
- `gui-launcher-dev` (High / `"pro"`): `apps/workbench_qt/project_window.py` 및 `tests/test_native_project_gui.py` 전담
- `gui-apps-dev` (High / `"pro"`): `apps/workbench_qt/native_analysis.py`, `expert_window.py`, `expert_chat.py` 및 관련 테스트 전담
- `gui-aux-runner` (`"flash"`): `main_window.py`, `viewport.py` 테마 연동 및 경량 테스트 보조

## 문서 과잉·토론 루프 방지 철칙 (Anti-Paperwork Drift Rule)
- 구현 배정 시 실제 제품 코드(`caelab/`, `apps/`, `src/`) 변경 없이 장문의 마크다운 명세나 토론 로그(`*.jsonl`), 영수증(`review-*.md`), 외부 소스를 Git 저장소에 쌓는 것을 금지합니다. 회의·비판 기록은 `scratch/`에 두고, PR은 검증된 코드·테스트·핵심 문서 동반 갱신(`CURRENT_STATE.md`/`README.md`)으로만 제출합니다.

이 문서는 선택 가능한 도구별 프리셋이다. 실제 활성 팀·단일 편집자·모델·권한은 현재 사용자 지시와 루트 AGENTS에 따라 배정하며, 과거 역할명이나 사전 등록만으로 실행 중이라고 보고하지 않는다.
