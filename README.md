# caelab-workbench

**외부 CAE 프로그램의 입력·실행·결과와 전문가 대화를 프로젝트로 관리하는 Windows용 Qt 워크벤치입니다.** CAD·메시·해석 조건의 정밀 편집은 각 전용 프로그램에서 수행합니다. Workbench는 파일 연결, 실행별 입력 사본·로그·결과의 출처, 저장과 재개를 관리합니다.

## 재료 연구·전문가 자문·후처리 개발 현황

새 기능의 진행상태는 [사용자용 설명](docs/specs/cae-research/USER_PROGRESS_ko.md)에서 확인할 수 있습니다. 어떤 기능은 기존 코드에 있고, 어떤 계산은 별도로 시험했으며, 무엇을 앞으로 구현해야 하는지 구분했습니다.

개발을 맡길 때는 [작업표](docs/specs/cae-research/IMPLEMENTATION_TASKS_ko.md)와 [상세 명세서 안내](docs/specs/cae-research/README.md)를 이용하세요. 이 문서들은 개발 방법을 설명하며 새 기능 전체의 구현 완료를 뜻하지 않습니다.

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

## 내장 SciPy 2D PDE API

FEniCSx 설치 없이 직사각형 영역의 정상 열전도/Poisson 문제
`-div(k grad(u)) = f`와 일반화 고유값 `K phi = lambda M phi`를 계산할 수 있습니다.
설치는 `python -m pip install -e ".[numerical]"`입니다. `nx`, `ny`는 방향별 셀 수이며
각 셀을 선형 삼각형(T3) 두 개로 나눕니다. 확산계수와 질량계수는 양의 상수입니다.

```python
import math
from caelab.pde.scipy_solver import solve_poisson_2d, solve_eigenmodes_2d
from caelab.solvers.pde_solver import SciPyPDESolver

rhs = lambda x, y: 2 * math.pi**2 * math.sin(math.pi*x) * math.sin(math.pi*y)
solution = solve_poisson_2d(nx=32, ny=32, rhs_func=rhs)
print(solution.values, solution.flux)  # q = -k grad(u), 면적 가중 절점 평균
result = SciPyPDESolver().solve_poisson_2d(rhs_func=rhs)
print(result.summary())
modes = solve_eigenmodes_2d(num_modes=3)  # M-정규화 모드, 일반적인 lambda
```

소스 기본값은 0, 경계 기본값은 전체 `u=0`입니다. `dirichlet_bc`에는 상수,
`f(x, y)` 함수 또는 `{"left": 1, "right": 0}`처럼 면별 값을 지정할 수 있습니다.
`neumann_bc={"top": g}`는 **`g=k du/dn`**이며 바깥 방향 열유속은 `-g`입니다.
미지정 면은 자연경계 `g=0`이고 순수 Neumann·상충하는 모서리 값은 거부합니다.
고유모드는 동차 Dirichlet 조건만 받으며 모드 수는 자유 절점 수보다 작아야 합니다.
고유값의 물리 의미는 확산·질량계수에 따르며 Hz로 자동 해석하지 않습니다.

`ResultSet` 호환 어댑터는 1부터 시작하는 절점 ID로 `u→displacements[0]`,
`qx,qy→stresses[0:2]`, `max(abs(u))→max_displacement`를 담고 PDE 전용 요약을 제공합니다.
`reaction_totals.fx/fy`는 **절점 flux 성분의 단순 합**으로 메시 의존 진단값이며
반력이나 경계 열수지가 아닙니다. 실제 스칼라 FEM 경계 반응은 `solution.reactions`입니다.
단위는 호출자가 일관되게 정하고 어댑터의 `units`에 선언하며 기본값은 미선언입니다.
이 슬롯을 구조 변형·Von Mises 계산에 사용하면 안 됩니다.

현재 범위는 2D 정상 계산 Python API입니다. 3D·시간적분·Workbench 작업 등록·VTU 생성과
`NativeResultView` 화면 연결은 포함하지 않습니다. 기존 FEniCSx 경로는 유지합니다.
`python -m pytest tests/test_pde_scipy_solver.py tests/test_pde_line.py -v`로 검증합니다.

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
