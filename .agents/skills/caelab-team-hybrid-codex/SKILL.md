---
name: caelab-team-hybrid-codex
description: >-
  Activate when running a hybrid team centered on Gemini 4 (Orchestrator Carbon/Neon + Gemini 4 Neon,
  Gemini 4 Carbon, and Gemini 4 Hydrogen) with optional local Codex CLI restricted to user-requested
  backend batch tasks (excluding Argon, Gemini 3.8 Flash, and GUI/copy delegation to Codex).
---

# CAELAB 개발 총괄 표준 운영 스킬 (Gemini 4 3원 체제 중심 × 선택적 로컬 Codex CLI 하이브리드)

공통 개발·검토·비판 해소 규칙은 루트 [AGENTS.md](../../../AGENTS.md), [DESIGN.md](../../../DESIGN.md), [GEMINI_LEAD_METHODOLOGY.md](../../../docs/GEMINI_LEAD_METHODOLOGY.md)를 그대로 따릅니다.

## 1. 핵심 설계 및 상용 CAE GUI 8대 철칙 (GUI Design Laws)

1. **추상화 및 상용 수준 구조화**:
   - 사용자의 예시(HyperMesh, Abaqus, ANSYS, PrePoMax, BeidPrep)에서 표면적 단어만 보지 말고, 전체 기능 카탈로그(`Module -> Sub-feature -> Supported Contract -> UX Flow -> Verification Gate`)로 구조화합니다.
2. **3D 뷰포트 최소 70% 보장**:
   - 전처리기 및 결과 뷰어에서 중앙 3D 뷰포트는 전체 창 폭의 **70% 이상**(`1440px` 기준 `>= 1000px`)을 유지합니다.
3. **관료적 면책 문구(Disclaimer) 영구 금지**:
   - `"공학 검증 미실시"`, `"원 좌표, 변형·파생 응력 계산 안 함"`, `"값은 여기서 고치지 않습니다..."` 등 개발자 방어용 정적 문구를 화면에 넣지 않습니다.
4. **물리량·단위계 직관 표기 및 파서 용어 차단 (0.5초 인지 원칙)**:
   - `'단위: mm · N (MPa)'`로 선언 단위와 유도 응력 단위를 함께 표기하고, `판독/메시 절점`, `집합 출력 상태: 일부 판독`, `DAT 마지막 판독 프레임의 집합 원값`, `원 합계 성분` 같은 백엔드 파서 내부 문구를 노출하지 않습니다.
   - **VTK MathText 주의**: `vtkScalarBarActor` 제목의 `|U|`는 반드시 `r'$\vert U\vert$'`로 이스케이프합니다.
5. **중복 표시 제거**: 동일 데이터를 상단 카드·중간 표·하단 텍스트 창에 2~3번 반복 출력하지 않으며, 결과 탭과 입력 원문 창을 불필요하게 50:50으로 가리지 않습니다.
6. **클릭 마찰(Friction) 제로화**: `Preview` 성공 시 `reviewed` 자동 체크로 `Apply`를 즉시 활성화합니다.
7. **콤보박스 잘림 및 유니코드 글리프 깨짐(`□`) 방지**: `AdjustToContents` 적용 및 `▸`/`−` 대신 ASCII `>`/`-` 사용.
8. **장난감 픽스처(Toy Fixture) 승인 영구 금지**: 단순 외팔보·4절점 사면체·소수 쉘 요소 몇 개만으로 화면·기능 완료를 보고하지 않고 **다중 파트 연결·체결·접촉·반력 평형·다중 스텝/프레임 어셈블리**로 실증합니다.

---

## 2. 역할 분담 및 Gemini 4 3원 체제 지휘 프로토콜

- **GUI 설계 · 사용자 문구 · UX 동선에는 로컬 Codex CLI를 절대 사용하지 않습니다.** GUI와 문구는 100% Gemini(`Orchestrator (Carbon/Neon)` + `Gemini 4 Carbon` + `Gemini 4 Neon`)가 작성·검수하며, Codex CLI는 사용자가 별도 구독 사용량 활용 또는 대규모 비-GUI 백엔드 작업을 명시적으로 요청한 경우에만 `Concrete Spec` 하에 제한적으로 투입합니다.

| 역할 | 주력 Gemini 4 모델 (`swarm` 핀) | 선택적 CLI 보조 (명시 요청 시 비-GUI 한정) | 핵심 책임 |
|---|---|---|---|
| **팀장 (메인)** | **`Orchestrator (Carbon)`** (`gemini-pro-orchestrator`) / **`Orchestrator (Neon)`** (`gemini-pro-orchestrator-neon`) | — | Plan Mode 아키텍처·`Concrete Spec`·UI 문구 확정, 게이트 판정, 통합·PR 관리 (**Argon 제외**) |
| **수석 코드 QA & UX 설계** | **`Gemini 4 Neon (XHigh)`** (`gemini-4-neon-xhigh`) | `gpt-6.2-astra` (`xhigh`) 보조 가능 | 작성자와 분리된 적대적 코드·계약 감사(Gate 1), 장난감 모델 승인 차단 |
| **실무 CAE 시각 UX 심사관** | **`Gemini 4 Neon (High)`** (`gemini-4-neon-high`, Vision) | **불가** | `1440×900` 전 화면·전 탭 캡처 PNG 직접 판독(Gate 2), 파서 용어·중복 위젯 감수 |
| **병렬 GUI·서비스 워커** | **`Gemini 4 Carbon (High / Medium)`** (`gemini-4-carbon-high`/`medium`) | **금지** | 격리 `git worktree` / `swarm share` 내 PySide6 UI, Workbench/Prep 연동 및 테스트 구현 |
| **수식·솔버·메셔 워커** | **`Gemini 4 Hydrogen (XHigh / High)`** (`gemini-4-hydrogen-xhigh`/`high`) | `codex exec` (`gpt-6.2-sol`, `high`) — 비-GUI 한정 | 재료 구성방정식, 다중 파트 솔버 덱, 메셔·MDO 폐루프 구현 및 수치 검산 |
| **경량 탐색·로그 보조** | **`Gemini 4 Hydrogen (Low / Medium)`** (`gemini-4-hydrogen-low`/`medium`) | — | 코드베이스 탐색, pytest/CI 로그 요약, 정적 점검 (**`Gemini 3.8 Flash` 대체**) |

- **3단계 무신뢰(Zero-Trust) 독립 검수**:
  - **Gate 1 (코드·계약·장난감 모델 배제 감사)**: `chief-reviewer` (`Gemini 4 Neon XHigh`)가 `git diff`, `check_doc_sync.py`, 다중 어셈블리 실증 포함 여부를 대조.
  - **Gate 2 (실화면 캡처 시각 UX 검수)**: 모든 주요 창과 하위 탭 PNG를 `cae-user-expert` (`Gemini 4 Neon High`)가 직접 판독하고 비판 해소 후 명시적 `APPROVED` 회수.
  - **Gate 3 (본선 클린 클론 & 패키징 실증)**: PR 병합 직후 임시 폴더에 `origin/main`을 새로 클론(`$env:PYTHONPATH` 명시)하여 검증하고 필요 시 포터블 번들/인스톨러 Tier 1/2 검사를 수행.
- **로컬 Codex CLI 실행 규칙 (백엔드 보조 요청 시)**:
  - 착수 전 `codex --version` (`v0.169.0+` 권장) 확인 후 JSONL 첫 줄 `thread_id`로만 `codex exec resume <THREAD_ID>` 호출. 제품 코드 변경 없이 문서/JSONL만 Git에 커밋하는 행위 엄격 금지.
