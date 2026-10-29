# caelab-workbench

**네이티브 CAE 프로그램을 연결하고, 해석 결과를 근거로 전문가와 토론하는 Windows용 Qt 워크벤치입니다.** CAD 도구, 솔버별 전처리기, 해석 솔버, ParaView와 독립 전문가 프로그램의 파일·실행·결과를 하나의 프로젝트에서 관리합니다.

자신의 CAD·해석 입력을 전용 프로그램에서 작성하면서 계산 결과와 검토 기록을 함께 관리하려는 CAE 엔지니어·연구자를 위한 프로젝트입니다. 모델의 정밀 편집은 각 전용 프로그램이 맡고, 워크벤치는 프로그램 사이의 연결과 입력·결과의 출처를 관리합니다. 설계안 비교가 필요하면 선택적으로 MDO Studio를 사용할 수 있습니다.

## 주요 작업

| 작업 | 워크벤치에서 하는 일 |
|---|---|
| 프로젝트 구성 | CAD·전처리·솔버·후처리·전문가 프로그램을 상자로 등록하고 파일과 연결 상태 관리 |
| 네이티브 모델 준비 | FreeCAD의 STEP 형상을 전문 전처리기에 전달하고 CAD 원본·편집 문서·솔버 입력을 구분해 보존 |
| 해석 실행 | 지원하는 솔버 어댑터로 입력을 실행하고 실행별 입력 사본·로그·결과 확인 |
| 결과 확인 | 선택한 계산의 결과를 ParaView 또는 지원하는 전용 후처리기로 전달 |
| 전문가 검토 | 독립 전문가 창에서 입력·로그·결과 등 선택한 자료를 근거로 질문하고 대화 저장·재개 |
| 파라미터 연구 | 별도 MDO Studio에서 CalculiX 덱의 스칼라 변수로 실험계획·대리모델·최적화 연구 수행 |

일반적인 작업 흐름은 **프로젝트 구성 → 네이티브 입력 준비 → 해석 → 결과 확인 → 전문가 검토**입니다. 필요에 따라 전용 프로그램에서 모델을 수정해 다시 계산하거나 MDO 연구를 진행합니다.

## 현재 지원 범위

현재 개발·검증은 CalculiX 네이티브 입력 실행과 결과 전달을 중심으로 진행하고 있습니다. FreeCAD FEM 호환 경로를 유지하며, FreeCAD CAD → PrePoMax → CalculiX 연결을 추가했습니다. PrePoMax의 Windows 실제 GUI·솔버 끝단 실행은 아직 미검증입니다. 프로그램 목록에 등록돼 있다는 사실만으로 파일 교환·계산·결과 판독이 모두 지원되는 것은 아닙니다.

OpenRadioss/OpenCourant용 독립 전처리기는 별도 [CAELab Prep](https://github.com/pikachu444/caelab-prep)에서 개발하며, 현재 앱과 워크벤치 연결 어댑터는 미구현입니다. Salome 및 추가 솔버의 연결, 플랫폼별 지원과 전체 GUI 검증의 남은 범위는 [CURRENT_STATE.md](CURRENT_STATE.md)와 [ARCHITECTURE.md](ARCHITECTURE.md)에서 확인하세요. PrePoMax Python/MCP 자동화는 [후속 계획](docs/review/2026-10-23-codex-improvement-plan.md)입니다.

전문가의 제안은 사용자가 검토하고 전용 프로그램에서 수정합니다. MDO의 현재 설계 적용은 INP 토큰 치환이며 CAD 재생성·재메시를 수행하지 않습니다. 실제 관측과 대리모델 예측을 구분하며, 최적화 결과가 전역 최적해나 구조 안전성을 보증하지는 않습니다.

## 설치와 시작

Python 3.11 이상이 필요합니다. 저장소를 내려받은 뒤 PowerShell에서 실행합니다.

```powershell
git clone https://github.com/pikachu444/caelab-workbench.git
cd caelab-workbench
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[gui,rag]"
python -m apps.workbench_qt
```

기본 실행은 프로젝트 화면을 엽니다. **프로그램 추가**로 사용할 프로그램을 등록하고 프로젝트를 저장한 뒤 파일을 지정·연결합니다. 단계별 조작과 저장·재개 방법은 [워크벤치 시작 가이드](docs/tutorials/workbench_quickstart.md)를 따르세요.

FreeCAD·PrePoMax·CalculiX·ParaView는 별도로 설치해야 합니다. 필요한 실행 파일은 연결 설정 또는 `CAELAB_FREECAD`, `CAELAB_PREPOMAX`, `CAELAB_CCX`, `CAELAB_PARAVIEW` 환경변수로 지정합니다. 전문가 질문에는 별도의 공급자 연결 설정이 필요합니다. [외부 프로그램 설정](docs/tutorials/workbench_quickstart.md#1-외부-프로그램-준비)에서 실제 경로 지정 방법을 확인하세요.

MDO 연구를 사용하려면 추가 의존성을 설치합니다.

```powershell
python -m pip install -e ".[mdo]"
python -m apps.workbench_qt.mdo_studio
```

## 문서 길잡이

| 목적 | 문서 |
|---|---|
| 기본 프로그램 연결·해석·결과·전문가·저장 흐름 | [워크벤치 시작 가이드](docs/tutorials/workbench_quickstart.md) |
| CalculiX 파라미터 연구·차트·적용·재해석 | [MDO 실습 가이드](docs/tutorials/01_end_to_end_calculix_mdo_guide.md) |
| 치수 최적화 사례·입력·재현 절차·기존 측정 결과 | [Sizing 벤치마크 보고서](docs/benchmarks/01_sizing_optimization_benchmarks.md) |
| 아직 계산·비교하지 않은 산업 모델 목표 | [GE 브래킷·로어 암 목표 사양](docs/benchmarks/commercial_mdo_benchmark_calculix.md), [추가 후보와 준비 조건](docs/benchmarks/comprehensive_commercial_mdo_benchmarks.md) |
| 제품의 목적과 설계 결정 | [DESIGN.md](DESIGN.md) |
| 코드 구조와 프로그램 간 책임 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 확인된 구현·실행 증거·미구현·미검증 범위 | [CURRENT_STATE.md](CURRENT_STATE.md) |

벤치마크는 특정 입력과 환경의 결과입니다. GE 데모나 모의 GUI 검사는 대형 산업 모델을 실제 계산한 증거가 아니며, 과거 미실측 비교의 정정 근거는 [README·벤치마크 감사](docs/review/2026-10-27-readme-benchmark-audit.md)에 있습니다.

## 개발과 도움 요청

개발 전 [개발 규칙](AGENTS.md)과 [구조 문서](ARCHITECTURE.md)를 확인하세요. GUI는 `apps/workbench_qt/`, 프로그램 연결은 `caelab/connections.py`, 실행·결과 기록은 `caelab/native/`, 전문가 토론은 `caelab/assist/`, 선택적 수치 연구는 `caelab/mdo/`에 있습니다. 명령과 비동기 작업은 각각 `caelab/workbench.py`, `caelab/jobs.py`가 관리합니다.

MDO 개발의 시작점은 `caelab.mdo`의 `VariableSpace`(변수), `StudyManager`(연구·설계점), `TemplateDeck`(덱 토큰 치환)입니다. GUI의 `MDOStudioWindow`는 Workbench를 통해 연구 서비스에 사용자 동작을 전달합니다. 세부 책임과 저장 계약은 [구조 문서](ARCHITECTURE.md)에 설명합니다.

```powershell
python -m pip install -e ".[gui,mdo,rag,dev]"
python -m pytest
python scripts/check_doc_sync.py --mode pre-push
```

문서만 수정할 때는 링크·설명과 실제 코드의 일치·diff를 검사합니다. 문서 동기화 검사의 `pre-push` 모드는 커밋된 브랜치 차이를 대상으로 하며, 미커밋 편집은 별도로 확인해야 합니다. 모의·Qt offscreen 검사는 실제 솔버 실행이나 Windows 화면 조작 검증을 대신하지 않습니다.

질문과 문제 보고는 [저장소 Issues](https://github.com/pikachu444/caelab-workbench/issues)를 이용하세요. 사용한 버전, 운영체제, 외부 프로그램 버전, 재현 순서와 로그를 함께 제공하면 확인에 도움이 됩니다. 현재 저장소에는 별도 라이선스 파일이나 패키지 라이선스 선언이 없습니다.
