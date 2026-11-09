---
name: caelab-team-hybrid-codex
description: >-
  Activate when running the token-saving hybrid team preset or taking over as Team Lead in caelab-workbench / caelab-prep:
  Team Lead authors concrete specs and orchestrates 3-tier verification while parallel local Codex CLI workers execute isolated git worktrees and Gemini Pro subagents perform code and visual UX audits.
---

# CAELAB 개발 총괄 표준 운영 스킬 (Team Lead Playbook × 로컬 Codex CLI 병렬형)

공통 개발·검토·비판 해소 규칙은 루트 [AGENTS.md](../../AGENTS.md)와 [DESIGN.md](../../DESIGN.md)를 그대로 따릅니다. 다른 에이전트가 개발 총괄(Team Lead)을 인계받았을 때도 이 문서의 규칙을 100% 동일하게 수행합니다.

## 1. 핵심 설계 및 상용 CAE GUI 6대 철칙 (GUI Design Laws)

1. **추상화 및 상용 수준 구조화**:
   - 사용자의 예시(예: HyperMesh 매뉴얼, Abaqus, ANSYS, PrePoMax, BeidPrep)에서 표면적 단어만 보지 말고, 전체 기능 카탈로그(`Module -> Sub-feature -> Supported Contract -> UX Flow -> Verification Gate`)로 구조화합니다.
2. **3D 뷰포트 최소 70% 보장**:
   - `caelab-prep`(`beidprep`) 및 결과 뷰어에서 중앙 3D 뷰포트는 전체 창 폭의 **70% 이상**(`1440px` 기준 `>= 1000px`)을 유지합니다. 좌측 `MODEL`+`FORM` 인접 독은 합계 ~30%(`~430px`)로 상한을 두되, 사용자의 수동 리사이즈(`resizeDocks` 포함)는 보존합니다.
3. **관료적 면책 문구(Disclaimer) 영구 금지**:
   - `"공학 검증 미실시"`, `"수치 검증·공학적 적합성: 미실시"`, `"원 좌표, 변형·파생 응력 계산 안 함"`, `"입력 편집은 전용 프로그램에서 합니다..."`, `"값은 여기서 고치지 않습니다..."` 등 개발자 방어용 정적 문구를 화면에 넣지 않습니다. 필요한 제약 안내는 1줄 요약 라벨의 `setToolTip(...)`으로 넣습니다.
4. **물리량·단위계 직관 표기 (0.5초 인지 원칙)**:
   - `'단위 선언됨'` 대신 **`'단위: mm · N (MPa)'`** 처럼 선언된 단위와 유도 응력 단위를 명시합니다.
   - `'DISP · D1'`, `'DISP [0]'` 대신 **`'변위 (U) · 크기 |U|'`** (`|U| (mm)`), **`'변위 (U) · Ux (D1)'`**, **`'응력 (S) · Von Mises [절점 외삽]'`** (`Von Mises (MPa)`)을 표기합니다.
   - **VTK MathText 주의**: VTK `vtkScalarBarActor` 제목에 `|U|`를 그대로 넣으면 파이프 2개가 수식 구분자로 오인되어 컬러바가 사라지므로 반드시 `r'$\vert U\vert$'`로 이스케이프합니다.
5. **클릭 마찰(Friction) 제로화**:
   - `Preview` / `Review replacement` 성공(`admissible == True`) 시 `reviewed` 체크박스를 자동 체크(`setChecked(True)`)하여 `Apply` 버튼이 즉시 활성화되게 합니다.
   - 임시 출력 폴더(`Output directory`)는 `default_asset_directory()`로 기본값을 미리 채웁니다.
6. **콤보박스 잘림 및 유니코드 글리프 깨짐(`□`) 방지**:
   - 모든 툴바 `QComboBox`에 `AdjustToContents`를 적용하고, Windows 한글 폰트에서 `□`로 깨지는 `▸`(`\u25b8`)나 유니코드 마이너스(`−` `\u2212`) 대신 안전한 ASCII(`>`, `-`, `파일 > 열기(Ctrl+O)`)를 씁니다.

---

## 2. 역할 분담 및 토큰 절약형 지휘 프로토콜

1. **팀장 (Team Lead — 메인 세션)**:
   - 직접 대량 코드를 작성하거나 수만 줄 로그를 읽지 않습니다.
   - 파일 경로·줄 번호·문자열·불변조건까지 확정한 작업 명세서(`spec-*.md`)를 작성하여 로컬 Codex 워커에게 지시하고, 검수 서브에이전트(`chief-reviewer`, `cae-user-expert`)의 보고를 받아 비판을 해소한 뒤 본선(`main`) 병합 및 패키징을 수행합니다.
2. **병렬 개발 워커 A·B (로컬 Codex CLI — `gpt-6.1-sol`, `model_reasoning_effort="high"`)**:
   - 각각 독립된 `git worktree`에서 실행합니다:
     ```powershell
     $spec = Get-Content -Raw '<spec-path>.md'
     $spec | codex exec -C <worktree-path> -s danger-full-access --json -m gpt-6.1-sol -c 'model_reasoning_effort="high"' - > '<log-path>.jsonl' 2>&1
     ```
   - 후속 수정 지시 시 반드시 기존 `thread_id`를 `resume`합니다 (`-C`와 `-s`는 `resume` 앞에 위치):
     ```powershell
     $fixSpec | codex exec -C <worktree-path> -s danger-full-access resume <THREAD_ID> --json -m gpt-6.1-sol -c 'model_reasoning_effort="high"' - > '<resume-log>.jsonl' 2>&1
     ```
3. **3단계 무신뢰(Zero-Trust) 독립 검수**:
   - **Gate 1 (코드/계약 검수)**: `chief-reviewer` (`Model: "pro"`)가 대상 커밋 SHA와 `git diff`, `check_doc_sync.py`, 테스트 통과 여부를 대조.
   - **Gate 2 (실화면 캡처 & 시각 UX 검수)**: 모든 주요 창과 하위 탭(`output_tabs.setCurrentIndex(1)`, `(2)` 등)을 실제로 전환·렌더링하여 PNG로 캡처한 뒤, `cae-user-expert` (`Model: "pro"`)가 시각 감수. 비판이 나오면 수정 후 원 비판자에게 재검토받아 명시적 `APPROVED`를 획득.
   - **Gate 3 (본선 클린 클론 & 패키징 실증)**: PR 병합 직후 임시 폴더에 `origin/main`을 새로 클론(`$env:PYTHONPATH` 명시)하여 검증하고, `setup.exe` 및 포터블 번들을 리빌드하여 Tier 1/2 검사를 수행.
