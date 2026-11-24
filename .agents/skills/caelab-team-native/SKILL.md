---
name: caelab-team-native
description: >-
  Activate when running the Antigravity-native subagent/swarm team preset in caelab-workbench
  (Orchestrator Carbon/Neon Team Lead + Gemini 4 Neon, Gemini 4 Carbon, and Gemini 4 Hydrogen
  workers/reviewers, excluding Argon and Gemini 3.8 Flash).
---

# CAELAB 프로젝트 팀 구성안 1 — Antigravity 순정 Gemini 4 3원 체제 (`Neon · Carbon · Hydrogen`)

공통 개발·검토·비판 해소 규칙은 루트 [AGENTS.md](../../../AGENTS.md), [DESIGN.md](../../../DESIGN.md), [GEMINI_LEAD_METHODOLOGY.md](../../../docs/GEMINI_LEAD_METHODOLOGY.md)를 그대로 따릅니다.

## 권장 모델 계층 (Gemini 4 3개 모델군 전용)

| 역할 | 담당 대상 | 권장 모델 (`swarm` 핀) | 핵심 임무 |
|---|---|---|---|
| **총괄 (메인)** | 현재 메인 세션 | **`Orchestrator (Carbon)`** (`gemini-pro-orchestrator`) / **`Orchestrator (Neon)`** (`gemini-pro-orchestrator-neon`) | Plan Mode 아키텍처·`Concrete Spec`·UI 문구 확정, 공통 계약 소유, 통합·PR 관리 (**Argon 제외**) |
| **수석 코드·아키텍처 QA / UX 설계** | `chief-reviewer` · GUI 명세 설계 | **`Gemini 4 Neon (XHigh)`** (`gemini-4-neon-xhigh`) | 구현 전 범위 감사(Gate 1), 인도 전 적대적 코드·회귀 감사(Gate 2), 장난감 모델 승인 차단 |
| **실무 CAE 시각·UX 심사관** | `cae-user-expert` | **`Gemini 4 Neon (High)`** (`gemini-4-neon-high`, Vision) | 다중 파트 어셈블리 실제 실행 및 `1440×900` 전 화면·전 탭 캡처 PNG 직접 판독·감수 |
| **병렬 GUI·서비스 워커** | `gui-launcher-dev` · `gui-apps-dev` | **`Gemini 4 Carbon (High / Medium)`** (`gemini-4-carbon-high`, `gemini-4-carbon-medium`) | 격리 `git worktree` / `swarm share` 내 PySide6 UI, Workbench/Prep 연동 및 회귀 테스트 구현 |
| **수식·솔버·메셔 워커 & 검산** | 수치·재료·솔버·MDO 워커 | **`Gemini 4 Hydrogen (XHigh / High)`** (`gemini-4-hydrogen-xhigh`, `gemini-4-hydrogen-high`) | 재료 구성방정식, 다중 파트 솔버 덱(CalculiX·OpenCourant), 메셔(`D24~D30`) 구현·검산 |
| **경량 탐색·로그·실행 보조** | `gui-aux-runner` / 탐색 보조 | **`Gemini 4 Hydrogen (Low / Medium)`** (`gemini-4-hydrogen-low`, `gemini-4-hydrogen-medium`) | 파일/심볼 탐색, CI/pytest 대용량 로그 요약, 정적 점검 (`Gemini 3.8 Flash` 대체) |

- **운용 수단 우선순위**: 모델 직접 핀이 가능한 `swarm(action="add", model=...)`을 우선 사용하며, `invoke_subagent` 슬롯 사용 시 `Model: "inherit"` 또는 `"pro"`만 사용합니다 (`"flash"`/`Gemini 3.8 Flash` 및 `Argon` 모델군 제외).

## 영구 등록된 프로젝트 에이전트 (`.agents/agents/`)
- `chief-reviewer` (`gemini-4-neon-xhigh`): 부팀장 겸 독립 아키텍처·회귀 감사관 (장난감 모델·파서 용어·중복 위젯 차단 포함)
- `cae-user-expert` (`gemini-4-neon-high`, Vision): 실무 CAE/FEA 엔지니어 대표 심사관 (다중 파트 복잡 어셈블리 흐름 및 `1440×900` 실캡처 감수)
- `gui-launcher-dev` (`gemini-4-carbon-high`): `apps/workbench_qt/project_window.py` 및 `tests/test_native_project_gui.py` 전담
- `gui-apps-dev` (`gemini-4-carbon-high`): `apps/workbench_qt/native_analysis.py`, `expert_window.py`, `expert_chat.py` 및 관련 테스트 전담
- `gui-aux-runner` (`gemini-4-hydrogen-low`/`medium`): `main_window.py`, `viewport.py` 테마 연동 및 경량 탐색·테스트 보조

## 장난감 검증 금지 · 문서 과잉 방지 철칙
- **장난감 픽스처(Toy Fixture) 완료 승인 금지**: 단순 외팔보·4절점 사면체·쉘 요소 몇 개짜리 미니 모델로 화면·기능 완료를 주장할 수 없으며, 다중 파트 어셈블리(접촉·체결·다중 반력 평형·다중 스텝/프레임) 실증 통과가 필수입니다.
- **문서 과잉·토론 루프 방지**: 제품 코드(`caelab/`, `apps/`, `src/`) 변경 없이 장문의 마크다운 명세나 토론 로그(`*.jsonl`), 영수증(`review-*.md`)만 Git에 쌓는 것을 금지합니다. 회의·비판 기록은 `scratch/`에 둡니다.

이 문서는 선택 가능한 도구별 프리셋이다. 실제 활성 팀·단일 편집자·모델·권한은 현재 사용자 지시와 루트 AGENTS에 따라 배정하며, 과거 역할명이나 사전 등록만으로 실행 중이라고 보고하지 않는다.
