---
name: caelab-team-hybrid-codex
description: >-
  Activate when running the token-saving hybrid team preset in caelab-workbench:
  Argon Team Lead authors concrete specs and reviews outputs while parallel local Codex CLI workers execute isolated git worktrees.
---

# CAELAB 프로젝트 팀 구성안 2 — Antigravity 총괄 × 로컬 Codex CLI 병렬형

공통 개발·검토·비판 해소 규칙은 루트 [AGENTS.md](../../../AGENTS.md)와 [DESIGN.md](../../../DESIGN.md)를 그대로 따릅니다.

## 역할 분담 및 모델 구성
1. **팀장 (Antigravity 메인 — `Argon`)**:
   - 공유 계약(`theme.py`, `icons.py`, 루트 문서) 직접 소유.
   - **Codex에게 계획이나 디자인 재량을 맡기지 않고**, 픽셀·팔레트 키·위젯 `objectName`·정규식·테스트 불변조건까지 확정한 작업 명세서(`spec-*.md`)를 작성.
   - 워커 결과물 `git diff` 직접 검수, 통합 회귀 테스트, Windows 실화면(Dark/Light·한글 IME·하중 수정 작업 재열람) 최종 마감.
2. **병렬 개발 워커 A·B (로컬 Codex CLI — `gpt-6.1-sol`, `model_reasoning_effort="high"`)**:
   - 최신 바이너리: `(Get-AppxPackage OpenAI.Codex).InstallLocation + "\app\resources\codex.exe"` (`codex-cli 0.164.0+`)
   - 각각 독립된 `git worktree`에서 `-s workspace-write -c 'mcp_servers={}'`로 실행하며, 테스트 실행 시 `CAELAB_UI_SETTINGS_PATH`로 테마 설정을 격리함. 독립 MDO/전문가 창의 별도 QSettings까지 격리하는 환경변수는 아니므로 해당 창의 설정 경로를 별도로 확인함.
   - `codex exec --json`의 `thread_id`를 수집하고 재지시는 `codex exec resume <THREAD_ID>`만 사용.
3. **경량 보조 워커 C (Antigravity `gui-aux-runner` — `Model: "flash"`, 또는 제3 Codex worktree)**:
   - 보조 창(`main_window.py`, `viewport.py`) 팔레트 연동 및 관련 테스트 점검.
4. **독립 감사 및 도메인 시각 감수**:
   - 사전/인도 코드 감사: `chief-reviewer` 또는 로컬 Codex `gpt-6-astra` (`model_reasoning_effort="xhigh"`, `-s read-only`)
   - 실무 엔지니어 시각·사용성 감수: `.agents/agents/cae-user-expert` (`Model: "pro"`)

이 문서는 선택 가능한 도구별 프리셋이다. 실제 활성 팀·단일 편집자·모델·권한은 현재 사용자 지시와 루트 AGENTS에 따라 배정하며, 과거 역할명이나 사전 등록만으로 실행 중이라고 보고하지 않는다.
