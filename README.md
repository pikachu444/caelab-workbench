# caelab-workbench

**외부 CAE 프로그램의 입력·실행·결과와 전문가 대화를 프로젝트로 관리하는 Windows용 Qt 워크벤치입니다.** CAD·메시·해석 조건의 정밀 편집은 각 전용 프로그램에서 수행합니다. Workbench는 파일 연결, 실행별 입력 사본·로그·결과의 출처, 저장과 재개를 관리합니다.

## 현재 지원 범위

- CalculiX 입력 실행·결과 전달, FreeCAD FEM 호환 경로와 FreeCAD CAD → PrePoMax → CalculiX 연결을 제공합니다. PrePoMax Windows GUI부터 솔버까지의 전체 흐름은 미검증입니다.
- 독립 전처리기 [CAELab Prep](https://github.com/pikachu444/caelab-prep)의 RAD·작업 문서 연결과 OpenRadioss/OpenCourant Starter→Engine 실행을 제공합니다. Windows 공식 패키지로 실제 서비스 실행을 확인했지만 native binary 결과의 물리 필드 판독은 아직 `partial`입니다. Windows 실화면·전체 후처리 검증은 남았습니다.
- 독립 전문가 창은 사용자가 선택한 입력·로그·결과로 대화합니다. 공급자 설정이 필요하며 제안만으로 원본 수정이나 계산을 실행하지 않습니다.
- 선택 도구인 MDO Studio는 CalculiX 덱의 스칼라 변수 연구를 지원합니다. 설계 적용은 INP 토큰 치환이며 CAD 재생성·재메시는 수행하지 않습니다.

네이티브 해석 창의 **선택 프레임 보기**에서 저장 VTU의 변위 크기 `|U|`·축 성분과 6성분 응력의 Von Mises를 선택할 수 있습니다. 범례에는 선언된 길이·응력 단위를 표시합니다. 원형(0x), 실변형(1x), 자동 배율과 등각·정면·평면·화면 맞춤을 한 줄에서 조작하며 원본 결과는 변경하지 않습니다. 변형에는 `mm` 또는 `m` 길이 선언과 원 좌표계에 맞는 D1/D2/D3 벡터가 필요합니다. 자동 배율은 최대 변위를 모델 최장축의 25%로 맞춥니다. 부모 **도구 → 보조 모델 / C3D10 결과 뷰어**는 별도 보조 창을 엽니다.

프로그램이 목록에 있다는 사실과 실제 파일 교환·계산·결과 판독 지원은 다릅니다. 확인한 환경과 남은 조건은 [CURRENT_STATE.md](CURRENT_STATE.md)를 확인하세요. 계산 완료나 최적화 결과가 구조 안전성을 보증하지는 않습니다.

네이티브 해석 창의 **도구 → AI 전문가와 결과 토론… (Ctrl+E)**, 툴바 **전문가 토론** 또는 로그·결과 아래 버튼으로 독립 전문가 창을 엽니다. 조회 중인 실행·프레임의 기존 판독 요약, 선언 단위, 실행·판독 상태와 최근 오류·경고를 질문 초안으로 전달합니다. 같은 실행·프레임의 열린 창은 작성 중인 질문을 보존하며, 다른 실행·프레임은 새 창으로 구분합니다. 원본 입력·로그·결과의 첨부는 전문가 창의 **계산 결과 가져오기…**에서 실행과 프레임을 확인해 선택하고, 질문 전송은 별도로 누릅니다.

## 빠른 시작

Python 3.11 이상과 Git이 필요합니다. PowerShell에서 가상환경의 Python을 직접 실행하므로 환경 활성화나 실행 정책 변경이 필요하지 않습니다.

```powershell
git clone https://github.com/pikachu444/caelab-workbench.git
cd caelab-workbench
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[gui,rag]"
.\.venv\Scripts\python.exe -m apps.workbench_qt
```

**프로그램 추가**로 상자를 추가하고 프로젝트를 저장한 뒤 입력 파일과 연결을 지정합니다. FreeCAD·PrePoMax·CalculiX·ParaView·Prep·OpenCourant는 필요한 것만 별도로 설치합니다. 위 명령은 이 외부 프로그램이나 LLM 공급자를 설치·설정하지 않습니다.

상세 설치, 외부 실행 파일 지정, RAD 실행, 저장·재개와 문제 해결은 [워크벤치 시작 가이드](docs/tutorials/workbench_quickstart.md)를 따르세요. 위 명령은 원격 저장소를 받습니다. 미게시 로컬 수정까지 설치하는 명령은 아닙니다.

## 문서

| 목적 | 문서 |
|---|---|
| 설치·프로그램 연결·해석·결과·전문가·저장 | [워크벤치 시작 가이드](docs/tutorials/workbench_quickstart.md) |
| 선택적 MDO 설치·연구·적용·재해석 | [MDO 실습 가이드](docs/tutorials/01_end_to_end_calculix_mdo_guide.md) |
| 실제 수행한 벤치마크와 입력·재현 조건 | [Sizing 벤치마크](docs/benchmarks/01_sizing_optimization_benchmarks.md) |
| 아직 실측하지 않은 산업 모델 목표와 후보 | [벤치마크 목표 사양](docs/benchmarks/commercial_mdo_benchmark_calculix.md) |
| 제품 정의·코드 구조·현재 상태 | [DESIGN](DESIGN.md), [ARCHITECTURE](ARCHITECTURE.md), [CURRENT_STATE](CURRENT_STATE.md) |
| 화면·차트의 의미 | [MDO 화면 참고자료](docs/tutorials/visual_mdo_guide.md) |
| 과거 구현·실패·정정의 원문 | [구현 이력](docs/history/implementation-history.md) |

## 개발과 문제 보고

[개발 규칙](AGENTS.md)을 먼저 확인하세요. GUI는 `apps/workbench_qt/`, 프로그램 연결은 `caelab/connections.py`, 실행 기록은 `caelab/native/`, 전문가 기능은 `caelab/assist/`, MDO는 `caelab/mdo/`에 있습니다.

MDO의 `MDOStudioWindow`는 Workbench를 통해 `caelab.mdo`의 `VariableSpace`(변수), `StudyManager`(연구·설계점), `TemplateDeck`(덱 치환)을 사용합니다. 독립 진입점은 `python -m apps.workbench_qt.mdo_studio`이며 위 가상환경에서는 다음처럼 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[mdo]"
.\.venv\Scripts\python.exe -m apps.workbench_qt.mdo_studio
```

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[gui,mdo,rag,dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts/check_doc_sync.py --mode pre-push
```

문서 동기화의 `pre-push` 검사는 커밋된 브랜치 차이를 대상으로 하므로 미커밋 diff도 확인해야 합니다. Qt offscreen 검사는 실제 화면 조작·외부 솔버 검증과 구분합니다. [Issues](https://github.com/pikachu444/caelab-workbench/issues)에 버전·환경·재현 순서와 비밀정보를 제거한 로그를 첨부해 문제를 보고할 수 있습니다. 현재 별도 라이선스 파일이나 패키지 라이선스 선언은 없습니다.
