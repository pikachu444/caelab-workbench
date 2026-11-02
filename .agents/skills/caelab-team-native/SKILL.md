---
name: caelab-team-native
description: >-
  Activate when running the Antigravity-native subagent team preset in caelab-workbench
  (Argon Team Lead + chief-reviewer + cae-user-expert + gui-launcher-dev + gui-apps-dev + gui-aux-runner).
---

# CAELAB 프로젝트 팀 구성안 1 — Antigravity 서브에이전트 전담형

공통 개발·검토·비판 해소 규칙은 루트 [AGENTS.md](../../../AGENTS.md)와 [DESIGN.md](../../../DESIGN.md)를 그대로 따릅니다.

## 영구 등록된 프로젝트 에이전트 (`.agents/agents/`)
- `chief-reviewer` (`Model: "inherit"`): 부팀장 겸 독립 아키텍처·회귀 감사관 (Gate 1 범위 감사, Gate 2 인도 감사)
- `cae-user-expert` (`Model: "pro"`): 실무 CAE/FEA 엔지니어 대표 심사관 (현재 프로젝트의 실제 입력·진단 및 실화면 기반 UX 감수)
- `gui-launcher-dev` (`Model: "pro"` / 고난도 `"inherit"`): `apps/workbench_qt/project_window.py` 및 `tests/test_native_project_gui.py` 전담
- `gui-apps-dev` (`Model: "pro"` / 고난도 `"inherit"`): `apps/workbench_qt/native_analysis.py`, `expert_window.py`, `expert_chat.py` 및 관련 테스트 전담
- `gui-aux-runner` (`Model: "flash"`): `main_window.py`, `viewport.py` 테마 연동 및 경량 테스트 보조

## 언제 선택하는가
- Antigravity 쿼터가 충분하고, 모든 서브에이전트 대화를 Antigravity 내부 메시지 버스로 일원화하고 싶을 때 선택합니다.

이 문서는 선택 가능한 도구별 프리셋이다. 실제 활성 팀·단일 편집자·모델·권한은 현재 사용자 지시와 루트 AGENTS에 따라 배정하며, 과거 역할명이나 사전 등록만으로 실행 중이라고 보고하지 않는다.
