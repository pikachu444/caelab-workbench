# caelab-workbench

FreeCAD의 CAD 형상을 솔버별 전처리기에 전달하고, 작성된 네이티브 해석 입력을 솔버·ParaView·독립 전문가 프로그램에 연결하는 Windows용 Qt 워크벤치입니다. 제품 기준은 [DESIGN.md](DESIGN.md), 확인된 구현과 남은 작업은 [CURRENT_STATE.md](CURRENT_STATE.md)에 기록합니다.

## 5대 핵심 기능

| 기능 | 사용자가 하는 일 | 파일과 결과 |
|---|---|---|
| 프로젝트 런처 | 독립 프로그램을 등록·연결하고 입력과 실행 상태를 관리 | 프로젝트 JSON, 원본 파일 참조 |
| CAD와 전문 전처리 | FreeCAD에서 CAD 작성 후 STEP를 PrePoMax 등 전처리기로 전달해 메시·재료·접촉·단계 작성 | `.FCStd`, STEP, `.pmx`, CalculiX `.inp`를 각각 보존 |
| 솔버 실행 | 지원 어댑터로 입력을 실행하고 상태·로그 확인 | 실행별 입력 사본, 원로그, 결과 파일 |
| 후처리 | 같은 실행의 결과를 ParaView에 전달해 필드 확인 | FRD에서 변환한 VTU/PVD 등 |
| 독립 엔지니어링 도구 | 전문가 창에서 선택한 자료로 토론하거나 MDO Studio에서 파라미터 연구 수행 | 대화 근거, 설계점별 계산 결과와 연구 기록 |

각 프로그램의 설치 여부와 연결 지원 범위는 다릅니다. 네이티브 모델 작성·정밀 편집은 전용 프로그램이 맡습니다. MDO의 `Set as Current Design Point`는 선택한 INP의 토큰을 치환하며 FreeCAD 원본 형상을 갱신하거나 다시 메시하지 않습니다.

OpenRadioss/OpenCourant 전처리기는 별도 [CAELab Prep](https://github.com/pikachu444/caelab-prep)에서 개발합니다. 이 워크벤치는 해당 프로그램의 파일·실행 연결을 맡으며 정밀 전처리 GUI를 중복 구현하지 않습니다. CAELab Prep은 현재 개발 방향과 평가 자산을 정리한 초기 단계이며 실행 가능한 앱이나 연결 어댑터는 아직 없습니다. PrePoMax 3.0 Python/MCP 자동화는 기존 PMX/INP 연결 이후의 [후속 계획](docs/review/2026-10-23-codex-improvement-plan.md)에 반영했습니다.

## 설치와 실행

Python 3.11 이상과 필요한 외부 프로그램을 준비합니다. PowerShell에서:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[gui,mdo,rag,dev]"
python -m apps.workbench_qt
```

독립 도구는 다음과 같이 실행합니다.

```powershell
python -m apps.workbench_qt.mdo_studio
python -m apps.workbench_qt.mdo_studio --study "C:\연구\study.json" --read-only
python -m apps.workbench_qt.expert_window
```

PrePoMax는 Windows에서 `CAELAB_PREPOMAX`에 GUI 실행 파일을 지정합니다.

CalculiX 실행 파일은 연결 설정 또는 `CAELAB_CCX`, ParaView는 `CAELAB_PARAVIEW` 환경변수로 지정합니다. 외부 프로그램 설치는 Python 패키지 설치와 별개입니다.

## 실제 CalculiX MDO 따라 하기

[완전한 입력 덱으로 시작하는 MDO 튜토리얼](docs/tutorials/01_end_to_end_calculix_mdo_guide.md)은 덱 확인 → 변수·응답·목적/제약 설정 → DoE 생성 → 백그라운드 계산 → 차트 확인 → 백업 후 선택점 적용 → 저장·재열기를 설명합니다.

MDO Studio는 빈 연구로 시작하며, 부모 프로젝트의 메뉴에서도 독립 프로세스로 열립니다. Study Tree·Setup Dock·설계점 표·다섯 시각화 영역에서 실제 `.inp`의 스칼라 변수를 설정하고 계산합니다. Pareto 점 선택은 표의 같은 ID와 연결되며 선택만으로 덱을 변경하지 않습니다. 저장한 GP/RBF/PRS 모델로 제약이 있는 다목적 후보를 찾은 뒤 **Fresh Native Confirmation → Set as Current → Run Current Design → Open Current Rerun in ParaView**로 이어집니다.

실제 관측과 대리모델 예측은 표와 차트에서 구분합니다. 원본·INCLUDE 적용에는 백업·트랜잭션 기록·Undo가 있으며, 외부 편집과 충돌하면 거부합니다. Save As는 소유 결과와 모델을 복사하는 독립 스터디를 만들고 외부 INP 참조는 유지합니다. 수치·저장 검증과 Windows 실화면·IME·DPI 검증은 [현재 상태](CURRENT_STATE.md)에서 따로 보고합니다. 전역 최적해나 일반 구조 안전성을 보증하는 절차는 아닙니다.

## 실측 치수 최적화(Sizing Optimization) 벤치마크

- [CalculiX 네이티브 치수 최적화 벤치마크 보고서](docs/benchmarks/01_sizing_optimization_benchmarks.md):
  - **3D 캔틸레버 굽힘 수렴 검증**: 40×8×8 C3D8(2,560요소)로 전단 잠김 극복, 0.915초 만에 보 이론 대비 97.20% 정밀 일치(잔여 2.8%는 3D 전단 변형 및 횡구속 효과).
  - **10-Bar Truss 최적화**: 제품 라이브러리(`caelab.mdo.optimize` + `CalculiXEvaluator`) 연동, 10개 T3D2 부재 단면적 최적화, 501회 해석 43.94초 완료, 질량 57.60% 감량(5,710.48 kg → 2,421.46 kg), 처짐 허용치(50.80 mm / 2.0 in) 내 50.78 mm에 정확히 수렴.
  - **3단 단차 캔틸레버 셸 최적화**: 제품 라이브러리 연동, 3개 S4 두께 최적화, 92회 해석 13.53초 완료, 질량 21.04% 감량, 모멘트 구배에 따른 계단형 두께 테이퍼 형성.
  - 재현: `python benchmarks/run_sizing_benchmarks.py` (전체 594회 해석 58.78초 완주, 영수증 `benchmarks/runs/` 보존).

## 향후 3D 벤치마크 목표

GE 엔진 브래킷과 자동차 로어 컨트롤 암은 **향후 상용 툴 비교 검증을 위한 3D 산업 모델 목표 사양**입니다. 현재 저장소의 GE 데모나 모의 GUI 테스트는 해당 대형 모델을 계산한 증거가 아닙니다. 미실측 감량률·응력·시간 비교는 철회했습니다.

- [GE 브래킷·로어 암 Benchmark Target Specification](docs/benchmarks/commercial_mdo_benchmark_calculix.md)
- [추가 산업 모델 후보와 검증 준비 조건](docs/benchmarks/comprehensive_commercial_mdo_benchmarks.md)
- [정정 근거: README·벤치마크 감사](docs/review/2026-10-27-readme-benchmark-audit.md)

## 코드와 검사

`apps/workbench_qt/`는 GUI, `caelab/mdo/`는 Qt와 분리된 수치·연구 코어, `caelab/native/`는 입력 스냅샷·실행 기록·결과 전달을 담당합니다. 명령 연결은 `caelab/workbench.py`, 비동기 프로세스 관리는 `caelab/jobs.py`가 맡습니다. 기존 `caelab/numerical/`에는 안내 파일만 남아 있습니다. [구조 문서](ARCHITECTURE.md)와 [개발 규칙](AGENTS.md)을 함께 확인하세요.

`caelab.mdo`의 `VariableSpace`는 변수 영역, `StudyManager`는 연구와 설계점, `TemplateDeck`은 명시적 토큰 치환을 담당합니다. `MDOStudyService`가 실행·출처 검증·저장·현재 설계 적용을 소유하고 `MDOStudioWindow`는 Workbench를 통해 이 서비스를 사용자 조작에 연결합니다.

```powershell
python -m pytest tests/test_mdo_studio_gui_live.py tests/test_job_batch.py tests/test_mdo_deck_and_responses.py tests/test_doc_sync.py -q
python -m pytest
python scripts/check_doc_sync.py --mode pre-push
```

모의 프로세스·Qt offscreen 검사는 실제 CalculiX 실행과 Windows 화면 조작 검사를 대신하지 않습니다. 이번 확인 범위와 환경별 실패는 CURRENT_STATE에 구분합니다.

## FreeCAD → PrePoMax → CalculiX 사용 흐름

1. 프로그램 추가에서 FreeCAD CAD, 전처리기(PrePoMax), CalculiX, 후처리기를 추가합니다. 전처리·솔버 프로그램은 선택 설정에서 바꿀 수 있습니다.
2. FreeCAD 상자에 `.FCStd` 원본을 등록하고 FreeCAD에서 STEP를 내보낸 뒤 출력으로 등록합니다.
3. CAD 출력을 전처리 입력으로 연결해 **형상 가져오기**를 실행합니다. PrePoMax에서 메시·재료·접촉·하중·단계·출력을 설정합니다.
4. PrePoMax에서 `.pmx`를 저장하고 워크벤치의 **편집 문서**로 등록합니다. 다음 기본 열기는 PMX를 사용합니다. STEP는 형상 교환 파일이므로 해석 조건·CAD 설계 이력을 전달하지 않습니다.
5. PrePoMax가 내보낸 `.inp`를 전처리 출력으로 등록하고 CalculiX 입력으로 연결합니다. 기존 계산·로그·결과 전달 경로로 실행합니다. 계산 결과는 기존 ParaView 경로 또는 PrePoMax 결과 열기로 확인합니다.
6. CAD나 PMX를 바꿨다면 전처리기에서 조건을 확인하고 덱을 다시 내보내 등록합니다. 이 연결은 형상 변경 시 자동 재메시·조건 재연결을 보장하지 않습니다.

프로젝트 저장 형식 3은 이전 1/2를 읽습니다. 기존 FreeCAD FEM 입력·CalculiX 덱 경로도 유지합니다. PrePoMax의 Windows 실제 GUI/솔버 끝단 실행은 현재 개발 환경에서 미검증입니다. Salome 및 추가 솔버의 미완성 연결을 완료로 표시하지 않습니다.
