# 개발 담당자에게 전달하는 정밀 리뷰와 시정 요구

작성일: 2026-10-23 (한국시간)

- 검토 대상: `pikachu444/caelab-workbench`의 `f8b88c6ab1bf37f414657f0ba4d6eef04b4dcb0c`
- 비교 대상: `pikachu444/autonomous-cae-lab`의 `46a5cf57803b35549aab4261e309bb6ee0390e32`
- 판단 기준: 저장소 `DESIGN.md` 맨 앞 제품 정의와 기준 결정 5가지. 뒤쪽의 충돌하는 이전 설계를 우선하지 않는다.
- 성격: 사용자가 요청한 리뷰 전달 문서다. 제품 코드 수정·구현 착수를 승인하는 문서가 아니다.
- 후속 실행 계획: [Codex 개선 계획](2026-10-23-codex-improvement-plan.md)

## 1. 개발 담당 Codex에게

요구한 제품은 **각 프로그램을 연결하고, 그 결과를 근거로 토론하는 워크벤치**다. 이번 코드는 그 정의를 문서에는 반복해서 적었지만 실제 실행 경로에는 충분히 반영하지 않았다.

현재 부모 화면의 전체 실행은 사용자 FreeCAD FEM 입력을 받아 계산하지 않는다. 제품 코드에 내장한 외팔보를 자체 변환기로 계산한다. 결과 상자는 결과 파일 없이 ParaView를 실행하고, 전문가 상자는 해당 계산 결과를 전달받지 않는다. 구조 해석 창과 전문가 창은 필수 패널 파일이 저장소에 없어 생성 단계에서 실패한다.

이 상태에서 프로그램 설치 실험, 개별 수치 함수, 독립 패널 테스트를 제품의 연결 진척처럼 보고하면 사용자는 무엇을 실제로 쓸 수 있는지 판단할 수 없다. **함수가 존재하는 것, 개발자 환경에서 따로 실행한 것, 사용자 프로젝트에서 이어서 동작하는 것은 각각 구분해서 보고해야 한다.**

더 심각한 것은 입력 조건과 실패가 사라지는 경로다. 접촉을 추가해도 같은 덱을 만들고, 모드해석을 정적해석으로 쓰며, 빈 결과를 성공과 변위 0으로 반환한다. 공학 프로그램에서 이런 동작은 우선 차단해야 할 결함이다. 추가 기능이나 화면 정리로 덮을 문제가 아니다.

기존 autonomous-cae-lab에서 지적된 예제 중심 개발이 새 저장소에서도 반복되고 있다. 복잡한 상부 구조를 줄인 것은 맞지만, 기존에 있던 비동기 작업 관리·취소·저장까지 빠졌다. 코드가 짧아졌다는 사실만으로 재설계가 성공한 것은 아니다.

CURRENT_STATE가 전체를 완료라고 선언한 것은 아니다. 1단계 진행 중, 메인 최적화 연결과 설치 레지스트리 연결이 남았다고 적은 부분은 정확하다. 문제는 일부 세부 항목의 '구현', '연결', '저장/재개', '확인만 남음'이 실제 원격 코드보다 넓은 의미로 쓰였다는 점이다.

**다음 작업에서는 진단 문서와 기능 목록을 더 늘리지 말고, 아래 결함을 수정하고 사용자 입력 하나를 끝까지 연결하라. 보고는 바뀐 사용자 행동, 직접 실행한 근거, 아직 확인하지 못한 범위를 중심으로 하라.**

## 2. 설계 방향과 GUI에 대한 평가

| 대상 | 평가 | 유지하거나 바꿀 내용 |
|---|---|---|
| 프로그램 연결·결과 기반 토론이라는 제품 정의 | 적절함 | 그대로 유지 |
| 솔버별 네이티브 전처리기와 ParaView 사용 | 적절함 | 자체 공통 모델로 네이티브 입력을 다시 작성하지 말 것 |
| 부모 런처형 프로젝트 화면 | 적절함 | 실제 프로그램·입출력·상태를 소유하도록 구현 |
| 트리·도킹·아이콘·표시 전용 속성 패널 | 코드 구성 방향은 적절함 | 현재 자산을 재사용 |
| 현재 연결선과 상자 상태 | 기능적 구현 부족 | 파일 전달과 실제 실행 상태에 연결 |
| 화면의 시각적 완성도·조작성 | 판정 보류 | Windows 실화면을 확인하지 않았으므로 좋다고 단정하지 않음 |

따라서 '설계 방향이 좋다'는 평가는 가능하다. '현재 GUI가 완성도가 높고 사용자 작업을 제대로 처리한다'는 평가는 할 수 없다. 좋은 방향의 UI 자산을 유지하면서 그 아래의 프로젝트·실행·결과 연결을 바로잡아야 한다.

Windows 실화면 확인은 사용자 지시로 보류 중이다. 이를 결함으로 세지 않았다. 누락 모듈, 입력 파일 전달, 상태 전이, 저장·복원, 잘못된 성공 판정은 Windows 화면을 기다릴 이유가 없는 문제다.

## 3. 확인한 범위와 한계

README → DESIGN → ARCHITECTURE → CURRENT_STATE → 폴더별 AGENTS 순서로 기준을 확인하고 코드와 테스트를 대조했다. Linux에서 PySide6를 설치해 Qt offscreen 테스트를 실행했다.

```text
QT_QPA_PLATFORM=offscreen python -m pytest -q -ra
4 failed, 73 passed, 9 skipped
```

- 실패 4개: `tests/test_gui_shell.py`의 MainWindow 생성 경로. 공통 원인은 `apps.workbench_qt.expert_panel` 누락.
- 건너뜀 9개: CalculiX 관련 5개, FEniCSx 1개, OpenCourant·Code_Aster·Salome 각 1개. 필요한 실행 환경 또는 환경변수가 없음.
- 추가 확인: 빈 FRD, 잘못된 실행 파일 경로, 실패 딕셔너리의 수치 평가, 솔버 제한 RAG 검색, OpenScience 프로필 표식에 대한 재현.
- 프로세스 종료코드만 모의한 결과 판독 검사와 실제 솔버 계산을 구분했다. 이번 리뷰에서 실제 솔버 계산은 수행하지 않았다.
- 실제 LLM 호출, Windows GUI, 개발자 컴퓨터의 설치 결과와 외부 매뉴얼 색인은 재검증하지 못했다.
- 이전 저장소는 관련 소스 비교를 수행했다. 전체 테스트를 실행한 것은 아니다.
- 이 문서의 사실은 지정한 커밋에 대한 것이다. 후속 커밋에서는 해당 경로를 다시 확인해야 한다.

## 4. 심각도순 결함

### R1 — 치명적: 실행 시 입력 검사를 우회하여 물리 조건을 누락하거나 치환

근거: [caelab/workbench.py:43–50](https://github.com/pikachu444/caelab-workbench/blob/f8b88c6ab1bf37f414657f0ba4d6eef04b4dcb0c/caelab/workbench.py#L43), [caelab/solvers/calculix.py:108–139](https://github.com/pikachu444/caelab-workbench/blob/f8b88c6ab1bf37f414657f0ba4d6eef04b4dcb0c/caelab/solvers/calculix.py#L108).

```python
# Workbench.run
converter = get_converter(solver_id)
return self.jobs.submit(converter, model, workdir)

# CalculiXConverter._deck_text
nlgeom = step.kind == "nonlinear_static" or step.options.get("nlgeom")
lines.append("*STEP, NLGEOM" if nlgeom else "*STEP")
lines.append("*STATIC")
```

직접 재현: contact를 추가하면 check는 미지원 오류를 반환하지만, 작성된 덱은 접촉 없는 모델의 덱과 동일했다. modal도 검사에서는 거부되지만 run 경로는 실행 함수까지 호출하고 STATIC 덱을 만들었다. 후자의 솔버 호출은 모의했다.

수정: 보조 변환기의 공용 실행 진입점에서 유효성·지원 검사를 강제한다. 표현할 수 없는 조건을 생략하지 않는다. 네이티브 본선은 원 덱을 보존하고 자체 변환기의 제한을 적용하지 않는다.

### R2 — 높음: 빈 결과를 성공·변위 0으로 반환

근거: [caelab/solvers/calculix.py:162–168, 216–246](https://github.com/pikachu444/caelab-workbench/blob/f8b88c6ab1bf37f414657f0ba4d6eef04b4dcb0c/caelab/solvers/calculix.py#L162).

```python
except ValueError:
    continue

return ResultSet(
    solver_id="calculix",
    success=True,
    displacements=dict(disp),
    nodal_forces=dict(forc),
    max_displacement=max_disp,
)
```

직접 재현: 빈 FRD는 success=True, displacements={}, max_displacement=0.0이 됐다. 종료코드 0을 모의한 run에서도 같은 결과였다. 실제 CCX가 빈 FRD를 만드는 상황까지 재현한 것은 아니다.

수정: 프로세스 종료, 결과 판독, 필요한 결과의 존재 여부를 분리한다. 누락은 0이 아니며, 판독 실패는 성공이 아니다. 프레임·필드·단위·반력 집합의 출처를 보존한다.

### R3 — 높음: 필수 패널 파일 누락으로 작업 창 생성 실패

근거: [apps/workbench_qt/main_window.py:255](https://github.com/pikachu444/caelab-workbench/blob/f8b88c6ab1bf37f414657f0ba4d6eef04b4dcb0c/apps/workbench_qt/main_window.py#L255).

```python
from .expert_panel import ExpertPanel
```

해당 커밋에는 `expert_panel.py`가 없다. MainWindow 생성과 구조 해석·전문가 상자 열기가 ModuleNotFoundError로 실패했다. 관련 테스트 4개도 같은 이유로 실패했다. `caelab/assist/MERGE_REQUEST.md`의 파일 작성·통합 기록과 원격 파일 목록이 다르다. 로컬 작업의 원격 반영 누락 가능성은 추정이며 원인은 미확인이다.

수정: 실제 파일의 이력을 확인해 복구·통합하고 깨끗한 checkout에서 검사한다. 전문가 공급자 미설정이 일반 해석 창 사용까지 막지 않도록 한다.

### R4 — 높음: 네이티브 연결 본선 대신 고정 예제·자체 변환기 실행

근거: [apps/workbench_qt/project_window.py:251–263, 432–463](https://github.com/pikachu444/caelab-workbench/blob/f8b88c6ab1bf37f414657f0ba4d6eef04b4dcb0c/apps/workbench_qt/project_window.py#L251).

```python
self.model = demo_model()
self.build_demo_chain()
```

```python
self._open_external(box.kind, "freecad", "demo.FCStd")
job = self.workbench.run(self.model, "calculix", workdir)
```

직접 확인: 초기 모델은 `cantilever`, 형상 참조는 `demo.FCStd`, 부모 프로젝트 창의 메뉴 항목 수는 0개였다.

수정: 빈 프로젝트·사용자 파일에서 시작한다. 연결선이 나타내는 실제 입출력 관계를 프로젝트가 소유한다. FreeCAD FEM 작성 덱을 실행하는 경로를 본선으로 만들고 예제는 명시적 도움말 동작으로 옮긴다.

### R5 — 높음: 결과→후처리·토론 전달과 프로젝트 재개 단절

근거: [project_window.py:440–453](https://github.com/pikachu444/caelab-workbench/blob/f8b88c6ab1bf37f414657f0ba4d6eef04b4dcb0c/apps/workbench_qt/project_window.py#L440), [caelab/model/store.py:18–32](https://github.com/pikachu444/caelab-workbench/blob/f8b88c6ab1bf37f414657f0ba4d6eef04b4dcb0c/caelab/model/store.py#L18).

```python
self._open_external(box.kind, "paraview", None)
self._workspace.set_model(self.model)
```

```python
path = folder / MODEL_FILENAME
path.write_text(
    json.dumps(model.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
)
```

ParaView에는 결과 경로를 주지 않고, 새 전문가 작업 창에는 계산 결과를 주지 않는다. 저장은 모델 JSON만 다룬다. 실행 결과는 임시 폴더에 생기고 작업·토론·연결 관계는 복원되지 않는다. `MainWindow.set_model()`은 이전 `_last_result`도 초기화하지 않는다.

수정: 같은 실행의 입력·결과·로그·토론을 묶고 프로젝트 안에서 참조·복원한다. 모델 변경이나 재실행 후 이전 결과의 유효 상태를 명확히 갱신한다.

### R6 — 높음: 실행 예외에서 running 고착, GUI 동기 실행

근거: [caelab/jobs.py:43–54](https://github.com/pikachu444/caelab-workbench/blob/f8b88c6ab1bf37f414657f0ba4d6eef04b4dcb0c/caelab/jobs.py#L43).

```python
job._set("running")
result = converter.run(model, deck, job.workdir)
job.result = result
job._set("succeeded" if result.success else "failed")
```

덱 작성 이후는 예외 처리 밖이다. 존재하지 않는 CCX 경로를 지정하니 FileNotFoundError가 나고 작업은 running에 남았다. GUI 버튼부터 subprocess.run까지 동기 호출이다. 장시간 계산 중 실제 화면 멈춤은 측정하지 않았지만 호출 구조상 이벤트 루프를 차단한다.

수정: 실행·판독을 포함한 실패 상태 전이, 비동기 실행, 취소, 디스크 기록을 제공한다. 기존 저장소 JobManager의 해당 기능을 이식 대상으로 검토한다.

### R7 — 높음, 연결 시 위험: 실패 딕셔너리를 최적화 성공으로 변환

근거: [caelab/numerical/model_binding.py:214–219](https://github.com/pikachu444/caelab-workbench/blob/f8b88c6ab1bf37f414657f0ba4d6eef04b4dcb0c/caelab/numerical/model_binding.py#L214).

```python
success = bool(getattr(result, "success", True)) \
    if not isinstance(result, dict) else True
```

`{'success': False, 'execution_status': 'FAILED', 'score': 0.0}`를 반환시키니 SUCCEEDED 평가가 됐다. 메인 최적화 연결은 아직 없으므로 실제 사용자 흐름에서 발생했다고 주장하지 않는다. 연결 전에 막아야 하는 잠복 결함이다.

수정: 결과 타입별 실행 상태 계약을 명시하고 실패·취소·부분 실패를 성공 응답으로 정규화하지 않는다.

### R8 — 높음: 잘못된 솔버 출처와 다른 솔버 지식으로의 무음 대체

근거: [caelab/result_summary.py:15–20](https://github.com/pikachu444/caelab-workbench/blob/f8b88c6ab1bf37f414657f0ba4d6eef04b4dcb0c/caelab/result_summary.py#L15), [caelab/assist/knowledge.py:311–317](https://github.com/pikachu444/caelab-workbench/blob/f8b88c6ab1bf37f414657f0ba4d6eef04b4dcb0c/caelab/assist/knowledge.py#L311).

```python
def summarize_result(result: ResultSet, solver: str = "calculix") -> dict:
```

```python
if not chunks and resolved:
    chunks = index.search(query, top_k=limit, embedder=embedder,
                          reranker=reranker)
except Exception:
    return search_notes_keyword(query, limit)
```

Code_Aster 결과를 기본 인자로 요약하니 CalculiX로 표시됐다. Code_Aster를 명시한 검색에 해당 색인이 없자 CalculiX 자료가 반환됐다. 색인 오류도 사용자에게 원인을 전달하지 않고 내장 노트로 대체한다.

수정: 결과 자체의 solver_id·단위를 사용하고, 검색에는 실행의 솔버를 명시적으로 전달한다. 자료 없음·검색 실패·의도적 대체를 구분한다. 다른 솔버 자료는 비교 요청 때만 출처를 명시해 제공한다.

### R9 — 중간: OpenScience는 확인 보류가 아니라 실행 미구현

근거: [caelab/assist/experts.py:185–192](https://github.com/pikachu444/caelab-workbench/blob/f8b88c6ab1bf37f414657f0ba4d6eef04b4dcb0c/caelab/assist/experts.py#L185).

```python
ok, reason = self.availability()
if not ok:
    raise RuntimeError(reason)
raise RuntimeError(
    "OpenScience 호스트 실행은 Windows 작업 창에서 확인합니다. "
    "이 환경에서는 아직 실행 경로가 없습니다."
)
```

프로필이 있어도 호출은 무조건 실패한다. 프로필 표식으로 availability=True와 discuss 실패를 재현했다. Windows를 확인하면 작동할 구현이 현재 함수에 들어 있는 것이 아니다.

수정: 미구현 상태를 정직하게 표시하고, 실제 호출 경로를 구현한 후 환경 검증과 구분한다. 사용자 비용 제약을 유지한다.

### R10 — 중간: 설치 의존성과 실패 안내 불일치

근거: [pyproject.toml:10–15](https://github.com/pikachu444/caelab-workbench/blob/f8b88c6ab1bf37f414657f0ba4d6eef04b4dcb0c/pyproject.toml#L10), [run_workbench.bat:21](https://github.com/pikachu444/caelab-workbench/blob/f8b88c6ab1bf37f414657f0ba4d6eef04b4dcb0c/apps/workbench_qt/run_workbench.bat#L21), [viewport.py:81–89](https://github.com/pikachu444/caelab-workbench/blob/f8b88c6ab1bf37f414657f0ba4d6eef04b4dcb0c/apps/workbench_qt/viewport.py#L81).

```toml
dependencies = ["numpy>=1.26", "scipy>=1.11"]
gui = ["PySide6>=6.6"]
```

```bat
.venv\Scripts\python -m pip install PySide6 numpy vtk
```

GUI extras에 VTK가 없고 PDF 파서의 pypdf도 선언되지 않았다. Windows 실행기는 프로젝트·SciPy를 설치하지 않는다. VTK 부재·초기화 실패도 헤드리스 실행 안내로 표시한다. 선언 불일치는 확인했으며 Windows 새 환경 설치 실패는 직접 재현하지 않았다.

수정: 설치 경로를 프로젝트 extras로 통일하고 의존성 부재와 그래픽 초기화 실패를 구분한다.

## 5. 현황·테스트 해석을 바로잡을 것

| 주장 | 확인한 사실과 필요한 정정 |
|---|---|
| 프로그램 8종 연결 | 등록 목록·경로 탐색·명령 생성은 있음. 프로그램 간 파일·상태 전달 완료와 구분 |
| 저장/재개 | 모델 JSON 왕복은 있음. 작업·결과·토론 포함 프로젝트 재개는 없음 |
| 전문가 패널 반영 | 해당 커밋에는 필수 파일이 없음 |
| 최적화 모델 연결 완료 | 독립 패널 API는 있음. 메인 결합은 문서에도 남은 일로 적혀 있으며 실제로 없음 |
| 실제 솔버·LLM·설치 실측 | 개발자 환경 기록은 이번에 미확인. 허위라고 단정하지 않음 |
| RAG 7,688조각·Hit@5 100% | 외부 자산·모델을 재검증하지 못함. 솔버별 분리의 무조건적 보장은 코드와 다름 |
| OpenScience 확인만 남음 | 현재 호출 함수는 미구현 |

테스트는 전부 형식적이지 않다. 수치 엔진의 해석해·holdout 비교와 실제 솔버 테스트 코드는 의미가 있다. 그러나 다음 한계는 분명하다.

- solver smoke는 외부 프로그램을 직접 실행하며 Workbench 연결 경로를 검증하지 않는다.
- 일부 변환기 테스트는 키워드 존재만 확인하여 입력 조건의 보존을 보장하지 못한다.
- 최적화의 별도 재실행은 같은 변환기·판독기를 쓰므로 공통 결함을 독립적으로 검출하지 못한다.
- PySide6가 없으면 GUI 테스트가 건너뛰어질 수 있다. GUI 의존성을 설치한 필수 검사 경로가 필요하다.
- 해당 커밋에 GitHub Actions workflow는 없다. 외부 CI 존재 여부는 미확인이다.

## 6. 문서 충돌

| 문서 위치 | 충돌 |
|---|---|
| DESIGN 맨 앞 ↔ §1·§4·§5·§12 | 네이티브 연결·축소 공통 형식 ↔ 전체 공통 모델·자체 변환기 중심 |
| DESIGN 결정 4 ↔ §11.2 | 자체 화면의 확인·검사·실행·요약 제한 ↔ 자체 재료·단면·하중·접촉 편집 |
| README ↔ ARCHITECTURE | 공통 구조 모델 중심 ↔ 프로그램 연결 중심 |
| DESIGN §3.3 ↔ §12 | PDE를 구조 스키마에서 제외 ↔ PDE를 공통 모델 위로 일반화 |
| README 실행 안내 ↔ __main__.py | 프로젝트 인자 지원 안내 ↔ 실제 인자 처리 없음 |
| AGENTS·DESIGN 머리말 | 저장소 밖 문서를 기준 원본으로 지목하여 공유 기준이 분리됨 |

최신 사용자 결정에 맞게 기존 문서를 수정하라. 충돌 문서를 더 만들거나, 이미 정해진 제품 정의를 다시 사용자에게 선택시키지 말라.

## 7. 기존 실패와 비교한 판단

기존 저장소의 기능별 backend와 CATALOG 29개는 실제 소스에서 확인했다. 새 저장소에는 같은 분산 구조가 없고 코어→GUI 역방향 import도 발견하지 못했다. 이 부분은 개선이다.

반면 새 부모 화면이 내장 외팔보로 시작하고, 사용자 네이티브 입력의 본선이 연결되기 전에 수치·PDE·설치·RAG가 확장된 것은 같은 실패 패턴이다. 기존 `caelab/jobs.py`에 있는 비동기 실행·취소·디스크 기록·중단 복구는 새 JobManager에 이어지지 않았다.

교정 목표는 명확하다. **현재 GUI 자산을 유지하고, 네이티브 파일과 실제 실행 결과가 프로젝트·후처리·토론·저장 사이를 끊기지 않고 이동하게 만들 것.** 새 제품의 성공은 이 사용자 행동으로 판정해야 한다.
