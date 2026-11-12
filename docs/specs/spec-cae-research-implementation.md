# 범용 CAE 재료 연구, 전문가 자문과 프로그램 연결 구현 계약

## 기존 개발 계획 안에서 읽는 순서 — 2026-11-12 통합

제품 목표는 DESIGN, 구현 순서·담당 역할·완료 조건은 [기존 전체 계획](../review/2026-10-23-codex-improvement-plan.md#plan-integration), 현재 코드/검증/잔여는 각 저장소 CURRENT_STATE를 따른다. [12개 작업표](cae-research/IMPLEMENTATION_TASKS_ko.md)와 [자동 목록 v3](cae-research/implementation-map.json)는 그 전체 계획 아래의 세부 작업이다. 별도 전체 계획이나 별도 진행률이 아니다. Prep 단계·N표와의 연결도 기존 계획에 함께 적었다.

수식·데이터·API는 이 통합 명세와 [공통 C0–C12](cae-research/core-implementation.md), 각 분야 A/B/C/D를 함께 구현한다. 공통 C11과 작업표는 A/B의 구체 어댑터 파일명을 사용한다. 선행 조건은 편집 전 합의·실제 코드 사용·최종 인수로 나눴다. 기존 수식·과거 실행·원자료의 판정 범위는 보존한다.

공통 서비스는 작업자에서 실행하는 동기 API다. 활성 ID를 계산 전에 공개하고 긴 계산 동안 상태 잠금을 잡지 않는다. 중지는 이미 배정한 후보의 필수 시험을 마치는 경계, 취소는 요청 즉시 응답과 후속 종료 확인, 최종 확인은 `evaluate(...,execution_phase="confirmation",cache_policy="refresh")`의 새 실제 계산으로 정했다. 정확한 응답과 오류는 공통 C2/C7/C8을 따른다.


개정일은 2026-11-12다. 이 문서는 앞으로 구현할 명세이며 제품 기능 완료 보고가 아니다. 기준 커밋은 Workbench `104cdd6888a5554eecc8cf965c60ff1bd980b6da`와 Prep `555af5b3ab2c6e7dc276111609302ef565bb4fc8`이다. 실행 환경과의 연결이 끊긴 뒤 재구성한 최종 계약은 다음 부속 명세와 함께 읽는다. **공통 서비스, 평가, 문서 변경 확인, 저장, 화면과 Prep 문서 적용은 공통 명세를 따른다. 각 물리 모델, 자문과 후처리 영역은 해당 부속 명세가 이 문서의 이전 문구보다 우선한다.** 회수하지 못한 원본 근거를 확보했다고 주장하지 않는다.

| 구현 영역 | 기준이 되는 명세 |
|---|---|
| 공통 서비스와 목적함수 및 제약, 저장 직전 변경 검사, 다른 이름으로 저장, Windows Qt 화면, Prep 프로세스 통신 | [공통 구현 명세](cae-research/core-implementation.md) |
| Drucker–Prager 모델 DP, LAW33, 직교이방성과 정확한 이름으로 구별한 파생 XBP 모델 | [압력 의존 재료와 직교이방성 명세](cae-research/material-pressure-orthotropic.md) |
| 서로 다른 두 Ogden 모델, Prony 모델과 온도 환산, NEML 반복소성, JC 상태 | [유한변형과 이력 의존 재료 명세](cae-research/material-finite-history.md) |
| 일반 CAE 이론과 실행 근거, 검색 증강 생성 RAG, 도구 호출과 반환, 독립적인 답변 검토 | [전문가 구현 명세](cae-research/expert-implementation.md) |
| 실제 솔버 원본 결과 변환기와 시간 이력 TH, 수치 판독, 별도 RP 프로세스 | [후처리 구현 명세](cae-research/post-implementation.md) |
| 실제 토의와 독립 검토, 회수한 자료와 검증한 범위 | [인계 색인](cae-research/README.md) |

## 범위와 구현 순서

`DP`와 `Xia`는 사용자가 제시한 재료 모델의 예시다. 목표는 여러 재료 모델을 정의하고 시험자료로 계수를 동정하며 최적화하는 것이다. 일반 CAE 모델링과 구조해석 이론 및 결과에 대한 자문, 답변 검토, 여러 외부 CAE 프로그램과 솔버 및 결과 형식의 연결도 포함한다. 모델, 솔버와 프로그램마다 연결 구현인 어댑터와 지원 능력을 등록하여 확장한다. 제품 공통부에 DP나 Xia만을 위한 분기를 넣지 않는다. 모든 구성식, 매뉴얼과 프로그램을 이미 지원한다고 표시하지 않는다.

- [재료 계약](#materials)은 모델, 계수, 다채널 시험, 잔차, 평가기, 최적화와 저장을 정한다. 파일 내보내기와 열린 Prep 문서에 적용하는 동작도 포함한다.
- [자문 계약](#experts)은 이론 및 사례 질문, 문헌 색인, 자료 충분성 판정, 읽기 도구의 호출과 반환, 답변 검토를 정한다.
- [프로그램과 결과 계약](#program-results)은 변환기, 검증된 결과, 별도 후처리기, 원본 보존과 수치 조회를 정한다.

각 절은 기존 API와 제안 API를 구별한다. 존재하지 않는 `caelab/mdo/contracts.py`, `study_store.py`, `study.py`를 현재 재사용할 수 있는 모듈로 취급하지 않는다. 실제 최적화 데이터 전달 형식은 `mdo/optimize.py`에 있고 설계점 관리자는 `mdo/study_manager.py`에 있다. 현재 MDO v2 저장은 `mdo/service.py`가 담당한다. 기존 MDO, 프로젝트나 `NativeRun` 저장본을 새 재료 연구로 묵시적으로 이행하지 않는다.

M1/M2/M3, E0–E5와 POST01–06은 이 명세의 분야별 단계명이며 별도 잔여 작업 수가 아니다. 새 연구의 상세 계약은 CORE01/02·STORE01·MAT-A/B·UI01·PREP01·LINK01·E0-2·E3-5·POST01-04·POST05-06의 12개 분야로 묶는다. 실제 배정과 순서는 기존 제품 잔여까지 포함한 [전체 실행 목록](cae-research/IMPLEMENTATION_TASKS_ko.md#whole-backlog)을 따른다. 편집 전 계약을 합의하면 M1, E0/E1/E2와 POST01/02의 독립 부분부터 병렬 작성할 수 있다. M1은 화면 없는 재료 공통 계약을 구현한다. E0/E1/E2는 첨부 문맥, 질문과 저장, 검색을 구현한다. POST01/02는 결과 참조와 파생 작업의 생성부터 종료까지를 구현한다. 공유 자료 계약을 먼저 확정하고 E3의 저장 VTU 조회와 POST03의 솔버 원본 결과 변환기를 연결한다. M2/M3, E4/E5와 POST04–06에 필요한 화면, 솔버 원본 실행, 실제 공급자와 Windows 조건은 각 절의 의존 관계와 인수 조건을 따른다. 프로그램명이나 모델명을 나열했다고 지원 완료로 세지 않는다.

## 공통 자료 참조와 소유 계약

새 **`caelab/artifact_refs.py` 한 파일**이 데이터를 주고받는 순수 객체인 DTO와 JSON 검증을 담당한다. 표준 라이브러리 이외의 Qt, VTK, 솔버나 화면 모듈을 가져오지 않는다. 실제 VTK 판독은 선택 의존성을 사용하는 `caelab/result_readers/vtk_fields.py`가 필요할 때 지연해서 가져온다. 모든 자료를 하나의 유한요소 모델 형식으로 바꾸지 않는다.

자료 참조 `ArtifactRefV1`의 필수 필드는 `id, role, path, sha256, size, format_id, producer:{id,version}`이다. `id`, `role`, `format_id`와 `producer`의 문자열은 비어 있지 않아야 한다. `size`는 불리언이 아닌 0 이상의 정수이고 `sha256`은 소문자 16진수 64자리다. `path`는 소유 저장소 안의 POSIX 상대 경로다. 절대 경로, 드라이브 경로, UNC 경로, `..`, NUL, 심볼릭 링크나 재분석 지점으로 허용 범위를 벗어나는 경로를 거부한다. 기존 ID는 그대로 읽고 새 레코드 ID는 UUID로 발급한다. 모든 JSON은 UTF-8을 사용하며 유한값만 허용하고 정의하지 않은 추가 필드를 거부한다. 저장 후 출처에 따른 권한이나 사용 자격을 호출자의 JSON 값만 믿고 높이지 않는다.

근거 묶음의 `artifact_owners`는 각 자료 ID를 `{scope:'run'|'operation'|'consumer',id:owner_id}`에 연결한다. `operation` 범위에는 `source_run_id`가 필수다. 소유자 ID와 등록된 루트 경로는 호스트의 참조 해석기만 해석한다. 경로가 같아도 소유자가 다르면 다른 자료다. 구체적인 소유 루트와 `ResultRef`, `DatasetManifest`는 프로그램과 결과 절에서 정의한다. 재료 원자료, 배열과 보정 결과는 소비자 소유자 `material-study:<study_id>`를 쓴다. 토론 자료와 문헌 집합도 명시적으로 등록된 소비자 루트에서만 읽는다. LLM에는 자료, 근거와 선택 영역의 ID만 노출하며 파일시스템 루트를 고르게 하지 않는다.

결과 참조 `ResultRefV1`은 아래 프로그램과 결과 절의 **단일 기준 정의**만 사용한다. 자문과 보정은 이를 읽으며 각자 다른 결과 전달 형식을 만들지 않는다. 실험 CSV, 논문 조각과 보정 보고서를 솔버의 유한요소 결과처럼 포장하지 않는다. 각 자료의 형식과 종류별 근거 구조를 유지한다. 기존 작업 응답과 근거는 읽기 어댑터로 이 참조에 연결하며 자동으로 다시 쓰지 않는다. 당시 작업 응답의 고정된 바이트와 현재의 낡음 표시 및 절대 감사 경로는 구별한다.

내용 해시는 출처, 자료 소유자, 사용 가능 상태와 본문을 포함한 **검증된 전체 근거 묶음**에서 자기 `content_sha256` 필드만 제외하여 계산한다. 정규 JSON은 키를 정렬하고 공백 없이 UTF-8로 직렬화하며 NaN을 금지한다. 숫자 배열은 재료 절에서 정한 자료형과 크기 및 헤더와 리틀엔디언 바이트의 내용 해시를 실제 파일 바이트의 SHA와 구별한다. 배열, 하중과 성분처럼 순서가 의미 있는 목록은 정렬하지 않는다.

모든 변경, 적용과 실행은 기존 앱과 서비스가 소유한다. 연구나 자문이 모델 수정을 제안해도 Prep 문서 적용과 솔버 실행은 별개의 사용자 명령이다. 보정에 필요한 지원 능력이 부족하다는 이유로 기존 솔버 원본 덱 실행까지 차단하는 허용 목록을 만들지 않는다. 화면은 Windows의 Qt와 PySide6 창 및 기존 위젯을 사용한다. 새 웹 화면, 데이터베이스, `JobManager`나 유료 API를 선행 조건으로 만들지 않는다.

## 직접 토의에서 확정한 결정

다음 계약은 실제로 독립된 서브에이전트들이 제안하고 상호 비판한 뒤 수정하고, 원 비판자가 다시 확인하는 과정에서 도출했다. 외부 인간 전문가가 심의했다거나 새 제품 기능을 실행했다는 뜻으로 표현하지 않는다.

| 제기자와 검토 상대 | 지적 | 반영한 결정 |
|---|---|---|
| 재료 담당과 자문 담당 | 총 오차 하나로는 실패한 시험, 맞지 않는 측도나 보류 시험을 검토할 수 없다. | 평가 자료 `CalibrationEvaluationArtifact`에 시험별 및 채널별 곡선, 가중치, 범위, 관측과 예측, 실패를 보존한다. |
| 결과 담당과 재료 및 자문 담당 | 필드명만으로 좌표축, 측도, 층, 단위나 보정 적합성을 판단할 수 없다. | 공통 `ResultRef`와 필수 관측량의 완전성 검사를 사용한다. 제한된 조회와 적합 및 검토 자격을 구별한다. |
| QA와 재료 담당 | API 이름과 선형 예제만으로 범용 구현 명세라고 할 수 없다. | 실제 서비스의 함수 계약, 저장 v1, 잔차 수식, 별도 사용자 어댑터와 독립 수치 기준, 첫 시험 명령을 정한다. |
| QA와 자문 담당 | 질문 모드가 이론과 사례가 섞인 질문을 막고 추가 확인 클릭을 요구한다. | 질문 범위와 첨부를 분리하고 초안을 보존한다. 실제 자료 선택과 기존 확인 버튼을 사용하며 문장 의미 판정으로 전송을 막지 않는다. |
| QA 및 통합 담당과 자문 담당 | 판정 알고리즘, Windows의 색인 교체, 저장 형식 이행과 솔버 함수명 호환이 정해지지 않았다. | 자료 충분성 절차와 개발 및 평가 분리를 고정한다. 변경하지 않는 색인과 원자적 포인터 교체, 백업 후 저장 직전 변경 검사, 솔버 함수 별칭 대응을 사용한다. |
| 통합 담당, 결과 담당, 재료 및 자문 담당 | 현재 작업 전체의 해시는 다른 이름으로 저장하거나 현재 낡음 상태가 바뀔 때 깨진다. | 작업 응답 자료와 원본 식별값은 고정하고 현재 문서에 적용할 수 있는지는 별도로 판정한다. |
| QA와 결과 담당 | RP 명령행, 작업 수명, 소유자와 실제 솔버 수치의 인수 조건이 정해지지 않았다. | 프로젝트 소유의 공개 진입점, 작업 보조 파일, 원본 바이트 복사, 별도 RP 포크의 정확한 명령행과 합성 검증 기준을 정한다. 정답을 알고 있도록 인위적으로 구성한 해인 제조해도 검증에 사용한다. |

각 절의 시간, 호출 횟수와 수치 허용치는 버전을 관리하는 **구현 및 인수 정책값**이다. 달성 성능이나 범용 정답률로 발표하지 않는다. 작성자와 다른 QA가 동일 커밋의 사용자 진입점과 실제 파일 및 상태 전달을 확인해야 구현 작업을 닫을 수 있다.

<a id="materials"></a>

## 재료 모델링, 계수 동정과 최적화

### 1. 실제 재사용할 수 있는 기존 코드

| 기존 경로와 API | 재사용할 기능 | 명시적으로 유지할 한계 |
|---|---|---|
| `caelab/mdo/optimize.py`: `EvaluationRequest`, `EvaluationObservation`, `BatchEvaluator.evaluate_batch`, `OptimizationProblem`, `optimize(..., cancel=...)` | 수치 최적화와 키가 붙은 실제 관측 및 예측을 재사용한다. | 응답은 유한한 스칼라나 `None`이다. 곡선과 실험 의미는 표현하지 않는다. `mdo/contracts.py`는 존재하지 않는다. |
| `caelab/mdo/variables.py`: `ContinuousVariable`, `DiscreteVariable`, `CategoricalVariable`, `LinkedVariable`, `VariableSpace`, `parse_unit`과 코덱 | 자유 변수의 영역, 선형 및 로그 변환, 연계 변수와 제한식을 재사용한다. | 고정 계수는 별도로 합쳐야 한다. 하한과 상한이 같으면 무효다. 현재 단위는 알려진 길이, 질량과 시간의 곱셈형 역학 단위이며 임의 단위나 상수 보정이 필요한 온도를 지원하지 않는다. |
| `caelab/mdo/study_manager.py`: `StudyManager`, `DesignPointRecord` | 점 ID, 점 표, 상태와 실제값 및 예측값의 구별을 재사용한다. | 범용 영속 저장소 `StudyStore`가 아니다. 저장소에 `study_store.py`나 `study.py`는 없다. |
| `caelab/mdo/doe.py`, `surrogate.py`, `sensitivity.py`, `objectives.py`, `constraints.py` | 실험계획, 선택적인 대리모델과 민감도, 목적함수 및 제약 검증을 재사용한다. | 설치와 지원 능력을 확인해야 한다. 적합 점수만으로 구성식의 타당성을 입증하지 않는다. |
| `caelab/mdo/service.py`: `_document`, `_persist`, `open`, `reconcile` | 저장과 현재 참조 재확인 동작을 참고한다. 검증된 공통 저장 기능은 필요한 곳에서만 추출한다. | 현재 형식은 `caelab-mdo-study` 버전 2다. `open_input(.inp)`와 실행은 CalculiX 원본 형식에 종속된다. 이 형식에 보정 JSON을 넣거나 현재 서비스가 재료 평가기를 받는다고 주장하지 않는다. |
| `caelab/native/run_store.py`: `NativeRun`, `RunStore`, `atomic_json`; `filelock.FileLock` | 기존 솔버 실행 응답, 원자적 저장과 쓰기 잠금 소유권을 재사용한다. | 보정 기록은 실행 ID와 자료를 참조한다. 두 번째 솔버 실행 저장소나 프로세스 대기열을 만들지 않는다. |
| `caelab/jobs.py`: `BatchItem`, `JobManager.submit_batch/submit_native`, `cancel_batch/cancel_native`, `stop_batch` | 모든 외부 솔버 프로세스 제어를 재사용한다. | 기존 콜백은 별도 스레드에서 실행되므로 호출자가 화면 알림을 화면 스레드로 전달해야 한다. 플러그인은 추적하지 않는 하위 프로세스를 시작하지 않는다. |
| `caelab/native/adapter.py`: `SolverAdapter`와 등록부; `input_snapshot.py` | 기존 솔버 명령, 입력 고정 사본, 종료와 출력 수집을 재사용한다. | 솔버 등록은 재료법, 요소와 관측량의 특정 조합을 검증했다는 뜻이 아니다. 보정 지원 검사는 보정과 근거 추출에만 적용하며 기존 일반 솔버 원본 덱 실행을 막지 않는다. |
| `caelab/mdo/inp_binder.py`: `TemplateDeck`, `BindingSpec` | 원문의 위치 범위가 명시된, 지원하는 INP 스칼라 치환에 사용한다. | 가변 길이 INP 토큰 치환은 고정폭 RAD 렌더러가 아니다. |
| Prep `application/contracts.py`: `ObjectRef`, `PropertySpec`, `SetProperties`, `PropertyHost`; `RadProvider.submit`; `PrepSession.set_fields` | 자료형이 정해진 솔버 속성 편집, 단위, 기대 변경 번호와 자원 검증, 한 번에 취소할 수 있는 명령을 재사용한다. | 현재 자료형 지원은 LAW1과 LAW36의 기본 필드다. 등록만으로 모델 지원이 추가되지 않는다. |
| Prep `application/card_text.py`: `prepare_card_text`, `CardTextDraft.changes`; `radioss/deck.py`; `application/curve_csv.py` | 지원하는 고정폭 위치 범위와 원문 보존을 재사용한다. 공개 호스트 계약으로 CSV, 표와 곡선 위젯을 사용한다. | 범용 CSV 곡선 저장은 실험 해석이 아니다. 비공개 편집기 형식은 외부 재료법 플러그인 API가 아니다. |

첫 구현에 새 구성식 라이브러리, 패키지 다운로드, 유료 API, 데이터베이스, `JobManager`나 기존 수치 최적화기의 복사본은 필요하지 않다. OpenCourant의 실제 구성식 소스는 솔버 원본 실행 어댑터를 통해 사용할 수 있다. 인위적으로 재료점 계산을 선행시키려고 Python으로 다시 쓰지 않는다. MFront/MGIS 같은 외부 재료 라이브러리는 향후 어댑터 후보이며 설치, 라이선스와 API 검사는 아직 확인하지 않았다. 첫 비선형 프로필과 라이브러리의 정확한 범위는 부속 명세 A와 B를 따른다. 프로필별 미구현 기능, 플랫폼 조건과 물리 검증 조건을 명시적으로 유지한다.

### 2. 프로그램별 책임과 제안 파일

Workbench는 실험 근거, 계수 설정, 평가, 최적화와 적합 자료를 소유한다. Prep은 편집할 수 있는 솔버 원본 카드와 문서, 적용과 실행 취소를 소유한다. 솔버는 구성식 계산을 소유한다. RP와 ParaView는 표시 소비자이며 수치의 정본을 결정하지 않는다.

다음은 새로 만들 모듈의 제안이다. 현재 API가 존재한다는 주장이 아니다.

- `caelab/materials/contracts.py`는 변경하지 않는 v1 레코드와 엄격한 검증을 담당한다. Qt나 솔버를 가져오지 않는다.
- `caelab/materials/registry.py`는 명시적으로 신뢰하여 등록한 설치 어댑터와 사용자가 활성화한 설치 진입점을 관리한다. 안정된 ID, 버전과 설정을 검사한다. 서비스에 모델명 `if/elif`를 넣지 않으며 JSON에 적힌 Python 모듈을 실행하지 않는다.
- `caelab/materials/experiments.py`는 원본 표를 읽고 선언한 열과 측도를 변환한다. 원본 바이트는 바꾸지 않는다.
- `caelab/materials/residuals.py`는 잔차를 비교하고 시험별 자료를 만든다. 같은 입력에서는 같은 결과를 내야 한다.
- `caelab/materials/evaluator.py`는 기존 `BatchEvaluator`와 연결하며 새 평가 자료 색인을 관리한다.
- `caelab/materials/service.py`는 얇은 `CalibrationStudyService`를 제공한다. 기존 `StudyManager`, 최적화와 실험계획, `RunStore`, `JobManager`, `atomic_json`, `FileLock`을 조합한다. 재료 영역 상태를 저장하며 두 번째 범용 실행 엔진을 만들지 않는다.
- `caelab/materials/adapters/linear_elastic_1d.py`는 계약을 증명할 작은 참조 어댑터다. 최종 재료군을 이것으로 제한하지 않는다.
- `caelab/materials/adapters/native.py`는 외부에서 작성한 솔버의 단일 요소나 시험편 모델에 기존 입력 고정과 작업 기반 기능을 재사용한다. 이 방식과 해석식 또는 라이브러리의 재료점 계산은 둘 다 허용되는 계산 방식이며, 어느 쪽도 다른 쪽을 선행 조건으로 요구하지 않는다.
- `apps/workbench_qt/material_study.py`는 독립적인 재료 연구 진입점과 호스트를 제공한다. 기존 Workbench 도구와 프로젝트 파일 참조 방식으로 연결한다. 공통 모델 뷰어를 완전한 구성식 편집기로 바꾸지 않는다.
- Prep의 `application/material_binding.py`는 명시적으로 지원하는 내보내기 및 적용 계획을 기존의 자료형이 정해진 명령으로 바꾼다. 모델별 형식은 솔버 코덱과 스키마 담당에 두며 Workbench 위젯 코드에 넣지 않는다.

새 범용 `StudyStore`는 제안하지 않는다. 기존 MDO v2가 INP와 CalculiX 가정을 저장하므로 아래의 별도 재료 연구 매니페스트가 필요하다. 실제 중복이 생긴 뒤에만 공통 저장 보조 기능을 추출한다. 기존 MDO v2 데이터와 동작은 이행 없이 읽을 수 있어야 한다.

### 3. 제안하는 v1 레코드와 필수 필드

영역 간 순수 데이터 객체와 검증 모듈은 `caelab/artifact_refs.py` 한 곳이다. 솔버 전용 계약을 중복하지 않는다. 공통 파일 참조는 `ArtifactRefV1 = {id, role, path, sha256, size, format_id, producer:{id,version}}`로 고정한다. `path`는 소유 저장소 기준 상대 경로다. 앱의 참조 해석기는 근거 묶음의 출처에서 저장소 범위를 얻고 허용 범위 이탈을 거부한다. `artifact_owners`는 자료 ID를 `{scope,id,source_run_id?}`에 연결한다.

재료 원자료, 모델, 변환 절차, 배열과 내보내기 자료는 `scope="consumer", id="material-study:<study_id>"`를 사용한다. 호스트가 등록한 `materials/<study_id>` 루트에서 해석한다. 기존 솔버 입력과 출력은 실행 소유자를 유지하고 파생 파일은 작업 소유자를 유지한다. 기준 경로를 추측하거나 새 보정 전용 범위를 만들지 않는다. 모든 관련 참조는 같은 데이터 형식을 사용한다. 기존 작업 응답은 어댑터로 읽으며 자동으로 다시 쓰지 않는다. 전문가에게는 파일시스템 경로 대신 자료 ID를 제공한다.

공통 근거 묶음은 이 본문을 `kind="calibration"`으로 감쌀 수 있다. 내용 해시는 자기 해시 필드만 빼고 정규 UTF-8 JSON으로 계산한다. 실험 곡선을 솔버 유한요소 결과처럼 꾸밀 필요는 없다.

모든 최상위 레코드는 `schema_version=1`이 필수다. 데이터 클래스는 생성 후 바꿀 수 없게 하고 공개 배열은 방어적 복사본 또는 읽기 전용 복사본으로 제공한다. JSON은 알 수 없는 필드, NaN과 Inf, 중복 ID와 잘못된 참조를 거부한다. UUID는 개체를 식별하고 SHA-256은 고정된 내용을 식별한다. 플러그인 설정도 선언한 JSON 스키마와 해시를 가지며 알 수 없는 필드를 거부한다. 알 수 없는 어댑터 버전은 근거 열람만 허용하고 `UNSUPPORTED_ADAPTER_VERSION`으로 평가, 내보내기와 문서 적용을 막는다.

```python
@dataclass(frozen=True)
class AdapterIdentity:
    adapter_id: str             # stable namespaced installed implementation ID
    version: str
    implementation_sha256: str # package/source provenance, not an arbitrary code path
    config_schema_sha256: str

@dataclass(frozen=True)
class ModelDescriptorV1:
    id: str
    revision: str
    family: str                # informational namespaced ID; not a service switch
    formulation: str           # exact equations/reference/version identifier
    reference_refs: tuple[str, ...]
    state_kind: str            # memoryless | path_dependent
    domain: str                # time | frequency | static_path | declared adapter namespace
    input_channels: tuple[str, ...]
    output_channels: tuple[str, ...]
    value_kinds: tuple[str, ...] # real | complex (requires supported channel representation)
    parameter_schema_ref: str
    initial_state_ref: str | None
    conditions_schema_ref: str
    config: dict               # declared installed adapter config schema, never executable code

@dataclass(frozen=True)
class ObservableCapability:
    id: str
    quantity: str
    measures: tuple[str, ...]
    components: tuple[str, ...]
    bases: tuple[str, ...]
    location_kinds: tuple[str, ...]
    value_representations: tuple[str, ...]
    domain_kinds: tuple[str, ...]
    verification: str
    evidence_refs: tuple[str, ...]

@dataclass(frozen=True)
class CapabilityReport:
    identity: AdapterIdentity
    model_id: str
    model_revision: str
    level: str                 # analytic | material_library | native_fe
    supported: bool
    verification: str          # unverified | contract_tested | native_verified
    solver_id: str | None
    solver_version: str | None
    element_families: tuple[str, ...]
    supported_observables: tuple[ObservableCapability, ...]
    parameter_schema: dict     # registered model schema, immutable JSON-compatible data
    required_experiment_roles: tuple[str, ...]
    optimizer_responses: tuple[ScalarResponseSpecV1, ...]
    optimizer_constraints: tuple[ConstraintSpec, ...]
    differentiable: bool
    supports_export: bool
    supports_apply: bool
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]

@dataclass(frozen=True)
class ParameterSpec:
    id: str
    label: str
    unit: str
    role: str                  # fixed | free | linked
    value: float | str
    domain_kind: str            # continuous | discrete | categorical
    lower: float | None
    upper: float | None
    levels: tuple[float | str, ...]
    transform: str              # linear | log; physical bounds always stored
    expression: str | None
    dependencies: tuple[str, ...]
    group_id: str | None

@dataclass(frozen=True)
class ObservableSpec:
    id: str
    quantity: str              # stress, strain, force, displacement, storage_modulus, ...
    component: str             # scalar/explicit tensor component/defined invariant
    measure: str               # engineering/cauchy/PK2, total-small/log/plastic..., or not_applicable
    independent_quantity: str  # time, strain, frequency, explicit load parameter
    x_unit: str
    y_unit: str
    basis: str                 # global | material | explicit frame ref
    frame_ref: str | None
    location: dict             # kind + selection artifact/revision/count + IP + shell layer/section point
    gauge_or_reduction: dict   # extensometer/DIC gauge, weighted region, reaction sum, no implicit averaging
    shear_convention: str      # engineering | tensor | not_applicable
    sign_convention: str

@dataclass(frozen=True)
class ExperimentChannelSpec:
    id: str
    role: str                  # control | observed
    observable: ObservableSpec
    value_kind: str            # real | complex
    representation: str        # scalar | components | real_imag | storage_loss
    data_ref: str              # ArtifactRefV1 ID; shape/component order declared
    control_mode: str | None   # strain/stress/displacement/force/history/declared adapter mode

@dataclass(frozen=True)
class ExperimentSpec:
    id: str
    revision: str
    source_ref: str            # immutable raw bytes + SHA stored separately
    column_mapping: dict
    observables: tuple[ObservableSpec, ...]
    channels: tuple[ExperimentChannelSpec, ...]
    sequence_ref: str          # ordered segments, initial state and restart continuity rules
    loading_program_ref: str   # time/load/strain history/FE BC recipe, with hash
    specimen_ref: str | None   # geometry, area/gauge length, axes, test setup revision
    conditions: dict           # explicitly declared temp/rate/humidity metadata; units/capability checked
    conversion_recipe_ref: str
    split: str                 # train | validation | holdout
    replicate_group: str
    segments_ref: str          # monotonic comparison coordinates or explicit cyclic time segments

@dataclass(frozen=True)
class ComparisonSpec:
    id: str
    required_for_fit: bool       # train only; fixed objective membership
    required_for_validation: bool # validation/holdout only
    experiment_id: str
    observable_id: str
    grid_ref: str              # frozen measured comparison coordinates + hash
    interpolation: str        # linear v1; model/time/branch semantics already fixed
    comparison_domain: tuple[float, float]
    scale: float              # positive fixed value in comparison y_unit
    scale_source_ref: str     # declared engineering scale or training-only frozen recipe
    experiment_weight: float
    channel_weight: float
    quadrature_ref: str        # normalized nonnegative weights, not arbitrary candidate-dependent samples
    acceptance_limit: float | None
```

모델 설명과 실험 형식은 첫 버전부터 이력 의존성, 다축 제어 및 관측 채널, 순서가 있는 하중 구간과 복소 주파수 응답을 표현한다. 계산기의 지원 능력은 `UNSUPPORTED_DOMAIN`, `UNSUPPORTED_CHANNEL`, `UNSUPPORTED_VALUE_KIND`를 반환할 수 있다. 이는 초기 구현의 명시적 한계이며 형식 자체를 단조 변형률 곡선으로 제한하는 것은 아니다. 실수부와 허수부 또는 저장 성분과 손실 성분의 쌍에는 단위와 위상 규약을 명시한다. 복소수를 JSON의 NaN이나 문자열로 인코딩하지 않는다. 첫 어댑터는 실수 스칼라의 소변형 탄성만 계산한다. 이후 점탄성이나 다축 솔버 및 라이브러리 어댑터를 추가할 때 공통 서비스를 바꾸거나 자체 재료 적분기를 요구하지 않아야 한다.

v1 계수는 유한한 스칼라와 선택값을 최적화한다. 벡터나 텐서 모델은 성분별 ID와 그룹을 명시적으로 등록한다. 표로 정의하는 재료법은 고정 곡선 참조를 사용한다. 다만 선언한 계수화 절차가 이름 붙은 자유 스칼라를 표로 바꾸는 경우는 허용한다. 임의의 표를 묵시적으로 펼치거나 Prony 항의 수를 바꾸지 않는다. 표현 가능한 공통 제약은 기존 `ConstraintSpec`과 안전한 제한식을 사용한다. 표현할 수 없으면 버전을 명시한 어댑터 검증기가 실행 전에 정해진 오류 형식으로 잘못된 설정을 알린다.

고정 계수는 독립 `VariableSpace`에서 제외하고 각 물리 후보에 합친다. 연계값은 기존 코덱을 사용한다. 모든 계수가 고정되어 있으면 0차원을 거부하는 `OptimizationProblem`을 호출하지 않고 검증만 실행한다. 범주형 선택은 선언한 대안만 고르며 입력 문자열을 실행 코드로 쓰지 않는다.

단위는 공통 변환 계약 한 가지로 정규화하며 지원 범위에서는 기존 `parse_unit`을 쓴다. 공통 명세 C6은 물리량 종류를 구별한 절대온도와 온도 차이, Mg와 주파수 변환을 정한다. 미지원 단위는 추측하지 않고 거부한다. 차원이 있는 입력은 물리 단위를 사용하며 `EvaluationRequest.encoded`는 최적화 공간의 값이다. 두 값을 코덱 버전과 함께 별도로 저장한다. 로그 변환에는 양의 물리 경계값이 필요하다. `VariableSpace`가 이미 해독한 값에 지수 변환을 다시 적용하지 않는다.

### 4. 어댑터 API와 서비스 연결

제안하는 프로토콜은 다음과 같다.

```python
class MaterialAdapter(Protocol):
    identity: AdapterIdentity
    def capabilities(self, model: ModelDescriptorV1, environment: dict) -> CapabilityReport: ...
    def validate(self, model: ModelDescriptorV1, parameters: dict, experiments: tuple[ExperimentSpec, ...]) -> tuple[dict, ...]: ...
    def plan(self, request: MaterialEvaluationRequest) -> EvaluationPlan: ...
    def collect(self, plan: EvaluationPlan, artifacts: tuple[ArtifactRef, ...]) -> PredictionBundle: ...
    def export(self, request: MaterialExportRequest) -> MaterialExportPlan: ...
```

재료 평가 요청 `MaterialEvaluationRequest`는 `evaluation_id`, `request_id`, `point_id`, `study_revision`, `model_revision`, `adapter`를 담는다. 전체 물리 계수와 단위, 원래 인코딩 벡터와 코덱 버전, 순서가 있는 실제 실행 실험의 변경 식별값, 평가 수준, 수치 설정과 해시, 실행 예산과 취소 문맥도 담는다. 정식 필드 `execution_phase=exploration|confirmation`, `selection_ref:ArtifactRefV1|null`은 공통 C4 보완의 선택 고정·보류 시험 실행 경계를 따른다. 탐색 요청에는 보류 시험을 넣지 않는다.

실행 계획 `EvaluationPlan`의 종류는 `in_process` 또는 `native_jobs`다. 전자는 등록된 콜백을 호출하며 JSON 코드를 실행하지 않는다. 후자는 순서가 명시된 솔버 원본 입력 참조, 솔버 상태와 제한 시간, 의존 관계를 담는다. 두 방식 모두 같은 관측량 계약을 반환한다. 계획에는 `state_reset_policy="fresh_per_experiment"`를 고정한다. 각 후보와 실험 조합의 초기 상태 고정 사본과 해시, 순서가 있는 증분과 구간, 온도와 속도 및 하중 이력도 고정한다. 재료 내부 상태나 소성 상태가 후보 또는 독립 실험 사이에 유출되면 안 된다. 한 실험 안에서는 검증된 구간의 순서와 연속성을 유지한다.

다른 실험의 사전 하중이나 재시작 상태를 이어받으려면 버전을 명시한 상태 자료와 지원 능력이 있어야 한다. M1은 이를 `UNSUPPORTED_STATE_CONTINUATION`으로 거부한다. 복소 동적 기계 분석 DMA 응답은 이름 붙은 실수부 및 허수부 채널이나 저장 및 손실 채널과 위상 규약으로 표현한다. 복소수를 실수로 묵시적으로 바꾸지 않는다.

보정 작업을 대기열에 넣기 전에 지원 능력을 판정한다. 미지원 모델, 요소나 물리량은 이유를 반환하며 가짜 응답을 만들지 않는다. 이 제한은 보정에만 적용한다. 별도로 존재하는 Workbench의 솔버 원본 실행 경로는 제한하지 않는다. 솔버 원문과 알 수 없는 메타데이터도 독립적으로 보존하고 실행할 수 있다.

평가 연결 함수 `CalibrationBatchEvaluator.evaluate_batch(tuple[EvaluationRequest,...])`는 고정된 연구 내용을 찾고, 고정 및 연계 계수를 합치고, 지원 능력을 검사하고 계획을 만든다. 외부 실행 계획은 기존 `JobManager`에 맡긴다. 필수 관측량을 검사한 뒤 잔차를 계산하고 평가 자료를 원자적으로 저장한다. 요청마다 정확히 하나의 키가 붙은 `EvaluationObservation`을 반환한다. `responses`에는 호스트가 계산한 무차원 `fit_loss`와 미리 선언한 유한 제약 스칼라만 넣는다. `fit_loss`의 직렬화 단위는 `unit=""`이고 제약에는 등록 단위를 쓴다. 상세는 공통 명세 C5를 따른다. 모든 `ScalarResponseSpecV1`에 `schema_version=1`이 들어간다.

시험별 손실은 평가 자료에 보존하며 미선언 응답 ID를 실행 중 만들지 않는다. 배열은 `responses`가 아닌 자료 파일에 저장한다. `source_id='material-evaluation:<id>:<sha>'`를 사용한다. 솔버 `run_ids`, 실제 실행 시도 `native_attempts`, 캐시 재사용 `cache_hit`은 실제 사실대로 채운다. 대리모델 예측은 `mode='SURROGATE'`를 유지하며 실제 재료 검증으로 세지 않는다.

보정 자원 정책에는 `max_candidate_evaluations`, `max_native_attempts`, `max_wall_seconds`, `per_job_timeout_seconds`, `max_retries_per_job`, `max_concurrent_native_jobs`를 명시한다. 최적화기의 `max_evaluations`는 시험편 솔버 호출 수가 아니라 후보 수를 센다. 사전 검사에서 후보와 실험 조합의 작업 수를 계산하고 남은 솔버 실행 시도 예산을 넘으면 실행 요청을 거부한다. 재시도도 예산에 포함하고 응답을 보존한다. 완료 결과의 캐시 재사용은 후보 집계에는 포함하지만 새 솔버 실행 시도는 0회다. M1에는 외부 솔버 작업이 없다. 첫 솔버 연결은 기존 직렬 일괄 스케줄러를 사용하므로 별도 동시 실행 구현을 지원하고 검증하기 전까지 `max_concurrent_native_jobs=1`이다.

비동기 `JobManager` 연결은 작업자에서 완료 이벤트를 기다린다. Qt를 막거나 다른 하위 프로세스 관리자를 시작하지 않는다. 취소는 `optimize(..., cancel=event)`에 전달하고 추가 실행 요청을 막으며, 소유한 활성 작업에 `cancel_batch` 또는 `cancel_native`를 호출한다. 어댑터 계산은 증분과 시험 사이에서 취소를 확인한다. 중단할 수 없는 외부 라이브러리 호출은 그 한계를 명시하며 반환 전에 취소 완료로 표시하지 않는다. 추가 배정 중지와 실행 중인 작업 취소는 기존의 서로 다른 행동으로 유지한다.

### 5. 결과가 항상 같은 잔차 정의

공통 명세 C4는 `required_for_fit`, `required_for_validation`의 참여 조건과 단일 `AssessmentResultV1` 형식을 정한다. 아래의 필수라는 표현을 더 넓게 해석하는 문구보다 우선한다.

예측과 관측의 차원, 측도, 좌표계, 성분, 위치와 게이지, 전단과 부호, 독립 좌표가 맞을 때만 비교한다. 필수 위치나 게이지가 불명확하면 적합 자격이 없다. 물리량 이름만 선언해서는 충족할 수 없다. Cauchy 응력과 PK2 응력 사이, 공학 변형률과 로그 변형률 사이의 변환에는 기록된 변형과 형상 정보가 필요하다. 체적이 보존된다고 기본 가정하지 않는다. 순환 시험은 순서가 있는 시간이나 명시적 분기를 사용하고 반복된 변형률 값을 전체 정렬하지 않는다. 누락되거나 중복된 독립축 값은 명시적 가져오기 절차로 해결하며 `np.interp`가 조용히 처리하게 하지 않는다.

v1은 선언한 비교 영역 안의 **고정된 관측 격자**에 예측을 선형 보간한다. 두 자료의 영역이 모든 필수 표본을 포함해야 한다. 외삽하거나 후보에 따라 겹치는 범위를 잘라내지 않는다. 미리 선언한 마스크와 구간은 실험 변경본에 저장한다. 실패하거나 누락된 점 때문에 분모를 줄이지 않는다.

실험 e의 채널 c에 대해 `r[e,c,i] = (y_pred(x_i)-y_obs(x_i))/scale[e,c]`이고 `L[e,c]=sum(q_i*r_i**2)`다. 가중치는 `q_i>=0`, `sum(q_i)=1`을 만족한다. 기본 q는 고정 x 격자에서 정규화한 사다리꼴 적분 가중치다. 단일 표본 관측량은 명시적으로 `q=1`을 쓴다. 채널 가중치와 학습 실험 가중치는 각각 한 번 정규화하고 기준 변경본과 함께 저장한다. 학습 시험만 사용한 `fit_loss=sum(w_e * sum(w_ec*L[e,c]))`를 계산한다. 시험별 점수, 원래 잔차, 정규화 잔차, 유효 범위와 가중치를 계속 볼 수 있어야 한다. 척도는 양수이며 선언한 공학값 또는 고정한 학습 자료만으로 정한다. 후보 예측이나 보류 시험 통계로 정하지 않는다. 상수 신호에서 묵시적으로 0 척도를 만들지 않는다.

보류 시험은 최적화, 대리모델 학습과 정규화에서 제외한다. 같은 곡선의 무작위 점이 아니라 시험과 반복 시험 그룹 단위로 분할한다. 검증 시험은 명시적으로 기록한 모델 선택에 사용할 수 있다. 보류 시험은 선택한 절차를 고정한 뒤 평가한다. 보류 결과를 본 뒤 조정하면 새 변경본을 만들고, 새로운 최종 검증을 주장하려면 아직 사용하지 않은 새 보류 시험이 필요하다.

결정계수 R², 불확실성과 식별성은 선택 보고서다. 계산하지 못했거나 식별할 수 없거나 정의되지 않으면 `not_computed`, `not_identifiable`, `undefined`와 이유를 기록하며 숫자를 만들어 넣지 않는다. 솔버 종료, 적합 허용과 모델 타당성은 별도 판정이다. 필수 비교 참여, 허용 판정 집계와 `AssessmentResultV1`은 공통 명세 C4를 따른다. 유효한 큰 손실은 유한 목적함수로 남고 유한한 물리 제약 위반은 `INFEASIBLE`로 남는다. 후자는 공통 명세 C5를 따른다.

### 6. 평가 자료, 상태, 실패와 캐시

제안하는 `CalibrationEvaluationArtifact` v1의 필수 내용은 다음과 같다.

```
evaluation_id/request_id/point_id/study_id/study_revision
execution_phase/selection_ref
model_id/model_revision/adapter_identity/evaluation_level
physical_parameters+units/encoded_values+feature_order+codec_version
experiment_results[]:
  experiment_id/revision/split/observable_contract_hash
  observed_ref/predicted_ref/residual_vector_ref/normalized_residual_ref
  comparison_spec_ref, covered_domain, required_field_validation[]
  per_channel_loss/per_experiment_loss/acceptance/diagnostics
execution: state, run_ids[], attempts[], solver_identity, numerical_settings_ref
processing: parse_state, required_observables_state, converter/extractor identities
objective: eligible, fit_loss|null, reason_codes[]
assessment: fit_status, validation_status, holdout_status, adequacy_status,
            uncertainty_status, identifiability_status, constraint_status,
            constraint_checks[], policy_ref, channel_checks[], split_checks[], warnings[]
provenance_refs[], content_sha256
```

재료 평가 상태는 `pending→running→completed/failed/cancelled/timed_out/interrupted`로 전이한다. 이미 등록했으나 실행 배정 전 중지한 평가는 `pending→stopped`도 허용한다. 이 경우 실제 실행·시도 수는 0이고 `objective.eligible=false,fit_loss=null`이다. 아직 생성하지 않은 미래 후보를 만들어 저장하지 않는다. 캠페인 중지/취소, 기존 MDO 대문자 점 상태와 최적화기 종료 사유의 대응은 공통 C7을 따른다. 자료 처리 상태와 적합 판정은 독립적이다. 구조화된 오류에는 `code`, 단계, 실험과 관측량 ID, 이유, 재시도 가능 여부와 자료 참조를 담는다. 오류 코드는 `UNSUPPORTED_MODEL`, `UNSUPPORTED_UNIT`, `MEASURE_MISMATCH`, `COVERAGE_INCOMPLETE`, `SOLVER_FAILED`, `PARSE_FAILED`, `CANCELLED`, `TIMEOUT`, `STALE_INPUT`, `ADAPTER_VERSION_MISMATCH`를 포함한다.

적합에 필수인 실험이 무효이면 집계 적합 목적함수를 만들지 않는다. `valid=False`, 이유와 빈 필수 응답을 반환한다. 진단이나 보류 시험의 실패로 유효한 학습 손실을 없애지는 않는다. 공통 명세 C4를 따른다. 실패한 실행을 실수 벌점으로 바꾸거나 평균에서 제외하지 않는다. 이전에 완료한 평가와 부분 자료는 열람을 위해 보존한다.

전체 실행의 `parse_state=partial`이라고 모든 필드를 자동 금지하지 않는다. 기본적으로 적합은 거부하되 등록된 추출기가 **각 필수 관측량**의 예상 ID와 표본이 모두 있고 시간, 측도, 좌표계, 위치와 단위를 알 수 있으며 검증 정책을 통과함을 증명하면 허용할 수 있다. 관계없는 필드의 부분 누락은 경고로 남긴다. 필수 필드의 부분 누락은 무효다. 자격 판정은 RP 표시 상태가 아니라 수치 매니페스트로 한다.

현재 범용 후처리 필드 조회의 초기 지원은 검증된 기존 작성기의 VTU 절점 스칼라 또는 명시적 성분과 명시적 프레임뿐이다. RP의 기존 VTK, 요소 및 적분점과 층, 텐서 축 연산은 후속 지원 능력이다. 이를 요구하는 재료 어댑터는 같은 입력에서 같은 결과를 내는 추출기가 검증될 때까지 `UNSUPPORTED_OBSERVABLE`을 반환한다. 관측량 이름을 목록에 적는 것만으로 충분하지 않다.

성공 캐시 키에는 어댑터 구현과 설정 및 모델 변경 식별값, 전체 물리 계수와 단위, 모든 실험과 데이터 및 변환과 하중 및 시편 해시를 포함한다. 평가 수준, 솔버 실행 파일의 정체성과 버전 및 옵션, 입력 전체 참조 집합, 변환기와 추출기 정체성, 수치 허용치와 설정, 비교 변경 식별값도 포함한다. 재사용 전에 자료와 작업 응답의 해시를 확인한다. 취소, 실패나 필수 결과의 부분 누락은 성공 캐시가 아니며 재시도 정책과 예산을 기록한다. 재료점 계산과 유한요소 계산의 캐시 이름 공간은 구별한다. 재사용할 때 원 평가와 실행 참조를 기록한다. 이전 캐시 목적함수로 원자료 변경을 숨기지 않는다.

캐시의 원자료 정체성은 고정 사본과 자료 해시를 사용하며 변경 가능한 현재 `job.json` 전체나 낡음 표시를 해시하지 않는다. 솔버 실행 응답을 캡처하면 정확한 바이트를 고정된 `source_receipt` 자료로 보존하고 그 자료를 검증한다. 다시 열 때는 현재 실행의 고정된 정체성과 전체 참조 자료를 대조한다. 현재의 `stale` 또는 `stale_reason`이 바뀌었다고 과거 자료 손상으로 보지 않는다. 현재 문서에 적용할 수 있는지와 과거 고정 계산의 유효성은 구별한다. 다른 이름으로 저장할 때 참조 해석기의 상대 기준은 이동하지만 고정된 작업 응답 안의 감사 경로는 다시 쓰지 않는다.

### 7. 저장, 내보내기와 Prep 문서 적용

기존 프로젝트 데이터 루트 아래 또는 사용자가 선택한 독립 위치에 다음 폴더를 제안한다.

```
materials/<study_id>/calibration.json     # format=caelab-material-study, version=1
materials/<study_id>/revisions/<sha>.json
materials/<study_id>/sources/<sha>       # immutable original files, incl. external CSV
materials/<study_id>/arrays/<sha>.npy    # canonical numeric arrays, allow_pickle=False
materials/<study_id>/evaluations/<id>.json
materials/<study_id>/exports/<id>/manifest.json
```

배열 내용의 정체성은 정확히 다음 바이트열의 SHA-256이다. 먼저 UTF-8 정규 JSON 헤더 `{"dtype":"<f8","order":"C","shape":[n,...]}`를 쓴다. 키는 정렬하고 구분자는 `,`와 `:`이며 추가 공백은 없다. 다음에 LF 바이트 하나를 쓰고, 다음에 C 순서로 연속 배치한 리틀엔디언 수치 바이트를 쓴다. v1은 유한한 `<f8`, `<i8`와 불리언 `|b1`만 허용하며 객체 자료형은 금지한다. `.npy`는 명시한 형식 버전 `(2,0)`과 `allow_pickle=False`로 직렬화한다. `ArtifactRef.sha256`은 별도로 실제 파일 바이트를 검사한다. 같은 수치 내용은 플랫폼이 달라도 같은 수치 해시가 나와야 한다. 결정성을 위해 기본 NPZ ZIP 타임스탬프에 의존하지 않는다.

솔버 실행 `runs/<id>/job.json`은 **기존 `RunStore`** 안에 두고 보정은 참조를 저장한다. 최상위 매니페스트에는 연구 정체성, 고정 변경본 참조, 점과 시도 색인, 선택 상태, 최적화 설정과 이력, 자원 예산, 자료 목록 해시와 화면 상태를 넣는다. 내부 항목 참조는 상대 경로만 허용하며 경로 이탈, 심볼릭 링크 이탈과 알 수 없는 형식 필드를 거부한다. 기존 `atomic_json`과 `FileLock`을 사용한다. 다른 이름으로 저장은 기존 프로젝트 재배치 방식에 따라 참조된 자료 전체와 솔버 실행 참조를 복사하고 검사한다.

다시 열 때는 고정 자료 해시를 검사한다. 고정 근거가 없거나 변조됐으면 `CORRUPT_ARTIFACT`로 읽기 전용이 되며 적용할 수 없다. 현재 외부 원자료가 바뀌었다고 유효한 고정 사본이 손상되는 것은 아니다. 명시적으로 재확인하기 전까지 현재 원자료에 적용할 수 없게 하되 과거 열람과 재현성은 보존한다. 비정상 종료 중이던 작업은 성공이 아니라 `interrupted`가 된다. v1에는 과거 형식 이행이 없다. 향후 이행은 버전을 명시한 순수 연산으로 새 파일을 쓰고 원본 바이트를 보존한다. MDO v2를 맹목적으로 재료 형식으로 바꾸지 않는다.

내보내기 함수 `export()`는 같은 입력에서 같은 출력을 만들고 새 솔버 카드나 덱과 매니페스트를 쓴다. **열린 Prep 문서는 수정하지 않는다.** 계획에는 지원 솔버와 버전 및 재료법, 계수 ID, 단위 변환, 실제 출력 바이트와 해시, 필요한 연결 곡선과 INCLUDE, 미지원 필드를 명시한다. 지원하지 않는 내보내기는 화면에 알리고 행동을 비활성화한다. 참조 1차원 어댑터는 범용 보정 계수 보고서만 내보낼 수 있으며 그 보고서는 솔버 재료 카드가 아니다.

적용 준비 함수 `prepare_apply()`는 별도다. 지원하는 카드 연결, 대상 `ObjectRef`, 문서 세대와 변경 번호 및 자원 ID, 기준 카드 바이트와 해시, 정확한 변경 대응표와 완전한 연결 카드 계획이 필요하다. 첫 지원인 LAW1의 `E` 편집은 기존 `RadProvider.submit(SetProperties(...expected_revision...))`을 호출한다. 이는 `PrepSession.set_fields`를 통해 명령 전달기에 명령 하나를 확정한다. 한 번의 Undo가 지원한 변경 전체를 되돌린다.

재료와 곡선을 여러 카드에 적용할 때는 자료형이 정해진 자원 후보 상태를 모두 준비하고 연결된 곡선과 재료 참조 및 의존 사용처를 변경 없이 검사한다. 이어 기존 `cae_core/document.py:CommandDispatcher.execute_many(states, label, additions=..., removals=..., expected_revision=...)`를 세션 잠금 아래에서 한 번 호출한다. 카드 연결과 적용 준비는 새 기능이지만 `execute_many`는 이미 있다. 한 번의 Undo가 건드린 모든 자원과 연결을 원자적으로 복구해야 한다.

지원하지 않는 카드 필드나 구현되지 않은 원자적 적용 준비 및 연결 경로는 `UNSUPPORTED_ATOMIC_APPLY`로 적용을 거부한다. 해당 모델·솔버·판본의 내보내기 구현을 별도로 검증한 경우에만 솔버 덱 내보내기를 허용한다. 그 구현도 없으면 계수·평가 보고서만 저장할 수 있으며 솔버 카드/덱 출력 성공으로 표시하지 않는다. 전체 문서를 교체하거나 별개의 `set_fields` 명령을 여러 번 호출하지 않는다. 변경 번호나 해시가 맞지 않으면 계획과 초안을 보존하고 아무것도 적용하지 않은 채 다시 읽고 검토하도록 한다. Workbench의 INP 파일 트랜잭션 실행 취소를 Prep 문서의 실행 취소인 것처럼 쓰지 않는다.

### 8. 첫 실행 구현 단계와 인수 조건

각 단계에는 현재 상태 문서와 시험을 추가한다. 어댑터별 입력과 출력을 검증하기 전에는 모델 지원을 주장하지 않는다. 다음은 범용 계약의 구현 단계이며 DP와 Xia만의 이정표가 아니다.

**M1: 순수 계약, 잔차, 등록부와 첫 화면 없는 적합.** 제안한 계약, 등록부, 실험, 잔차, 평가기 모듈과 얇은 서비스 저장 기능, `linear_elastic_1d` 어댑터를 추가한다. 기존 `VariableSpace`와 `optimize`를 재사용한다. 착수에 Qt나 솔버 변경은 필요하지 않다. 정확한 공개 서비스 호출과 반환 데이터 형식은 아래와 같다. 과거의 줄인 이름은 추가 API가 아니다. 설정을 편집하면 새 내용을 고정하고 이전 내용의 현재 적용 가능성을 무효화한다. 과거 근거 자체는 무효화하지 않는다.

M1은 호출자 작업자가 실행하는 화면 없는 동기 API다. 구현도 없는 비동기 실행 핸들을 먼저 약속하지 않는다. 새 서비스 함수와 최소 반환 형식은 다음으로 고정한다.

```python
class CalibrationStudyService:
    @classmethod
    def create(cls, path: Path, *, name: str, model: ModelDescriptorV1,
               adapter: AdapterIdentity, parameters: tuple[ParameterSpec,...],
               workbench=None) -> "CalibrationStudyService": ...
    @classmethod
    def open(cls, path: Path, *, read_only: bool=False,
             workbench=None) -> "CalibrationStudyService": ...
    def snapshot(self) -> StudySnapshot: ...
    def import_experiment(self, source: Path, *, channels: tuple[ExperimentChannelSpec,...],
               column_mapping: dict, sequence: dict, conditions: dict, split: str,
               replicate_group: str, expected_revision: str) -> ImportReceipt: ...
    def configure(self, *, parameters: tuple[ParameterSpec,...],
               comparisons: tuple[ComparisonSpec,...], assessment_policy, user_constraints,
               expected_revision: str) -> StudySnapshot: ...
    def evaluate(self, values: Mapping[str,float|str], *, expected_revision: str,
               on_progress: Callable[[dict],None]|None=None,
               cache_policy: str="reuse", execution_phase: str="exploration") -> EvaluationReceipt: ...
    def optimize(self, config: OptimizationConfig, *, expected_revision: str,
               on_progress: Callable[[dict],None]|None=None) -> CampaignReceipt: ...
    def cancel(self, campaign_id: str) -> CancelReceipt: ... # thread-safe request, not terminal claim
    def evaluation(self, evaluation_id: str) -> CalibrationEvaluationArtifact: ...
    def save(self) -> StudySnapshot: ...
```

서비스의 최종 함수 계약, `StudySnapshot`, `ImportReceipt`, `EvaluationReceipt`, `CampaignReceipt`의 문서 및 계산 내용 식별값, 다른 이름으로 저장과 복구 및 내보내기와 적용의 응답 형식, 잠금은 **공통 명세 C2, C7, C8, C10**을 따른다. `expected_revision`은 문서 바이트의 SHA이고 실행의 `study_revision`은 고정 계산 내용의 `content_revision`이다. 첫 사용자 흐름부터 다른 이름으로 저장이 필요하다. 동기 서비스는 작업자에서 실행하고 콜백을 Qt로 전달한다. 잘못되거나 낡은 설정은 실행 요청 전에 `MaterialStudyError`를 발생시킨다. 실행 중 발생한 시도는 자료형이 정해진 자료로 저장한다. 두 번째 쓰기 소유자나 작업 중 변경을 조용히 대기열에 넣지 않는다.

정확한 독립 수치 기준은 다음과 같다. CSV의 변형률은 `0,.001,.002`, 응력은 MPa 단위로 `0,2,4`다. 자유 계수 E의 범위는 100..10000 MPa이고 초기값은 1000이다. 기존 SLSQP 구현에서 `OptimizationConfig(algorithm="slsqp", seed=17, max_evaluations=120, max_iterations=60, ftol=1e-12, finite_difference_step=1e-7)`와 `OptimizationProblem(...differentiable=True)`를 쓴다. 계수 변환은 선형이고 제약과 대리모델은 없다. 종료 이유 `CONVERGED`, `|E−2000|≤.01 MPa`, `loss≤1e-10`, 실행 요청 수 `≤120`을 수락한다. 구현별로 달라지는 정확한 반복 횟수는 요구하지 않는다.

이 정확한 기존 공통부 점검은 설계 중 실제 실행했고, 후보 계수를 5회 평가해 `E=1999.999614958788`, `loss=1.3899068902313467e-14`를 얻었다. 포아송비 nu나 밀도 rho는 추론하지 않는다. 독립 보류 시험의 변형률 `0,.0015,.003`, 응력 `0,3,6`은 `E=2000`에서 오차가 0이다. `E=1800`, `scale=4 MPa`, 정규화 사다리꼴 가중치 `q=(.25,.5,.25)`이면 기대 학습 손실은 `.00375`다. 같은 자료를 kPa로 표현해도 명시적 변환 후 물리적 E와 손실은 같아야 한다. 이는 합성 산술 검증 기준이며 실제 재료의 실험 검증이 아니다.

M1의 추가 인수 조건은 다음과 같다.

- 모든 계수가 고정된 검증은 0차원 최적화를 호출하지 않는다. 양의 로그 경계값은 한 번의 변환 왕복으로 보존한다.
- 알 수 없는 형식, 단위나 측도, 비교 범위 누락은 거부한다. 비단조 순환 변형률은 명시적 시간이나 분기가 있어야 한다. 마스크는 후보마다 바뀌지 않는다. 시험 그룹이 보류 시험으로 유출되지 않는다.
- 실패, 취소나 필수 결과의 부분 누락은 적합 목적함수를 만들지 않는다. 다시 열면 계수, 곡선, 잔차, 시험 분할과 해시가 재현된다. 고정 원자료의 변조는 손상이고 외부 현재 원자료의 변경은 현재 적용 가능성이 낡은 상태다.
- 별도 설치 모듈이나 시험 모듈에서 사용자 어댑터를 등록해 같은 평가기, 잔차와 서비스로 적합한다. 공통 서비스의 모델명 분기를 고치지 않는다.

사용자 어댑터의 정확한 시험식은 `y=a*x+b*x²`다. 합성 입력은 `x=(0,1,2)`, `y=(0,5,16)`이고 물리 경계값은 `a,b=(0,10)`, 초기값은 `a=b=1`, 척도는 `scale=16`이다. 같은 SLSQP 설정과 정규화 사다리꼴 가중치 `(.25,.5,.25)`를 사용한다. 제안한 `caelab/materials/__main__.py`가 명령행을 제공한다. 제안 등록 API는 `register_material_adapter(adapter, *, trusted_registration)`이다. 선택적인 설치 진입점 그룹 `caelab.material_adapters`는 사용자나 설치 단계에서 명시적으로 활성화한 뒤에만 해석한다.

제안하는 화면 없는 명령행은 `python -m caelab.materials validate|evaluate|fit|inspect --study <manifest>`다. 연구 파일에서 모듈 가져오기 이름을 받지 않고 데이터 파일을 읽는다. 인수 시험은 등록부와 명령행으로 시험 어댑터를 등록한 다음 미지원 다축 및 복소수 요청이 지원 거부되고 작업을 요청하지 않는지 확인한다. 미지원 내보내기도 명시적으로 드러나야 한다. `CONVERGED`, `|a−2|≤1e-3`, `|b−3|≤1e-3`, `loss≤1e-9`, 실행 요청 `≤120`을 수락하며 공통 서비스의 모델명 분기 변경은 없어야 한다. 기존 공통부 설계 점검은 후보 계수 조합을 16회 평가해 `a=2.0000045000541093`, `b=2.9999979999619013`, `loss=1.3183663827644835e-14`를 반환했다. 이는 확장성을 증명하며 모든 재료법 지원을 증명하지 않는다.

첫 제안 시험 파일은 `tests/test_material_contracts.py`, `test_material_experiments.py`, `test_material_residuals.py`, `test_material_evaluator.py`, `test_material_service.py`, `test_material_registry.py`다. 사용자 어댑터 시험 자료 `tests/material_adapter_fixture.py`는 명시적으로 신뢰하여 등록한 시험 객체를 가지며 연구 파일이 임의 코드를 가져오게 하지 않는다. 재현 명령은 `python -m pytest -q tests/test_material_contracts.py tests/test_material_experiments.py tests/test_material_residuals.py tests/test_material_evaluator.py tests/test_material_service.py tests/test_material_registry.py`다. 이 파일들은 새 구현 작업이며 현재 존재하지 않는다. 회귀 명령은 기존 `test_mdo_variables.py`, `test_mdo_objectives.py`, `test_mdo_optimize.py`도 실행한다.

시험은 명시한 정규 헤더와 바이트에서 배열 내용 해시를 독립적으로 다시 계산한다. 저장하고 다시 열어도 같아야 하고 변조는 실패해야 한다. 상태 시험은 두 후보와 두 실험의 평가 순서를 뒤집어 비교하여 매번 초기 상태가 새로 시작됨을 증명한다. 미지원 이력 의존 요청이나 복소수 요청은 작업을 요청하지 않는다. 실제 솔버와 화면 시험은 M2와 M3에 속하며 M1 통과로 대신하지 않는다.

**M2: 같은 공통 구조의 완전한 사용자 진입점.** 실제 CSV의 열, 단위와 관측량 선택에서 시작해 계수의 고정 여부, 자유값과 경계값 설정, 실행과 중지 및 취소, 관측과 예측 및 잔차와 보류 시험 그래프, 저장과 다시 열기, 내보내기 상태까지 연결한다. 필요한 곳에서는 호스트 계약으로 Prep 위젯을 사용하며 연구 상태는 Workbench가 소유한다. 저장소 밖의 독립 CSV, 실제 Windows 화면, 라이브러리 누락, 오류와 취소 흐름을 시험한다. 수치 참조 시험으로 이 사용자 인수를 대신하지 않는다.

**M3: 처음 지원할 솔버 재료법 연결.** 설치해 사용할 수 있는 솔버와 재료법, 외부에서 작성한 단일 요소나 시험편 덱을 선택한다. 솔버 유한요소 어댑터는 기존 `JobManager`, `RunStore`와 같은 입력에서 같은 결과를 내는 추출기를 쓴다. 해당 모델에서는 프로세스 내부 구성식 어댑터보다 먼저 구현하거나 이를 대체할 수 있다. 계수에서 솔버 입력 바이트, 실행, 측정 물리량, 같은 잔차 계약과 실제 확인으로 이어지는 흐름을 증명한다. 첫 Prep 적용은 현재 LAW1의 E를 쓸 수 있다. Apply 미지원 모델의 솔버 덱 출력은 해당 모델·솔버·판본의 내보내기 구현을 별도로 검증한 경우에만 허용한다. 그 구현도 없으면 계수·평가 보고서만 제공하며 솔버 덱 성공으로 표시하지 않는다. 수정하지 않은 원래 필드, INCLUDE, 순서와 줄바꿈을 보존하는지, 변경 번호 충돌과 한 번의 Undo를 검사한다. 고정폭 RAD와 INP 코덱은 별도 형식 시험을 둔다.

**M4: 추가 모델 어댑터와 연구 수준.** 각 재료, 라이브러리나 솔버 모델은 공통부 분기 대신 형식, 지원 능력과 검증 시험으로 추가한다. 다른 측도, 여러 하중 경로, 온도 및 속도 의존성, Prony 항이나 초탄성 시험 방식은 등록 어댑터와 단위 및 실험 계약이 지원할 때만 허용한다. 재구현보다 기존 공개 솔버 재료법과 사용 가능한 라이브러리를 우선한다. 모델은 재료 라이브러리 수준과 솔버 유한요소 수준을 독립적으로 제공할 수 있다. 수준 간 일치는 명시적 연구 보고서로 검증하며 가정하지 않는다. 전문가 검토는 고정 자료를 읽고 변경을 제안하지만 조용히 실행하거나 적용하지 않는다.

직접 검토에서 요구한 전문가 인수 반례도 검사한다. 첫째, 총점이 같은 두 적합 중 하나에서 필수 전단 시험이나 보류 시험이 실패하면 시험별 자료를 근거로 수정 요구 또는 적합 실패를 내야 하며 일괄 통과시키지 않는다. 둘째, Cauchy와 PK2의 측도 또는 게이지와 축이 맞지 않으면 적합 목적함수가 없고 근거 부족으로 판정한다. 셋째, 취소되거나 판독하지 못한 필수 시험은 보이게 남기되 실제 적합으로 세지 않는다. 읽기 전용 전문가 문맥은 상세 평가 자료를 포함하고 이 구별을 보존한다.

### 9. 전문가에게 근거를 전달하는 경계

보정은 제안하는 토론 v2의 종류별 구조에서 별도의 `source_kind="calibration"`이다. `calibration_ref`가 연구 내용 식별값, 평가 ID와 내용 해시를 고정한다. 수동 근거나 솔버 실행처럼 꾸며 넣지 않는다. 기존 토론 v1 바이트를 자동으로 다시 쓰지 않는다. 자문 절에서 정한 사용자의 명시적 저장 시 v2 이행은 유지한다. 기존 저장 파일은 필드를 유지하고 어댑터가 공통 `ArtifactRef`를 제공한다.

고정 근거 문맥은 실제 모델 설명과 계수를 `model_text`로, 실제 시험별 관측, 오차와 확보 범위를 `summary_text`로 사용한다. 로그는 실제 제공된 본문만 쓴다. 로그가 없으면 빈 문자열과 미제공 상태를 기록한다. 솔버, 실행이나 프레임이 없거나 여럿이면 해당 값은 `null`이다. 최상위 단위는 `{}`이고 `unit_source=per_artifact`를 사용하며 관측량별 단위는 자료 안에 유지한다. 해시 실패, 알 수 없는 어댑터, 필수 결과 부분 누락과 측도 불일치는 근거 부족 또는 사용 자격 없음으로 드러나야 한다. 솔버에서 유래한 필드는 `native_run+derived_result_ref` 경로를 유지한다. 실험을 가짜 유한요소 실행으로 바꾸거나 근거 검사를 우회하려고 현재 결과의 이름표를 바꾸지 않는다.

<a id="experts"></a>

## 일반 CAE 자문, 검색, 도구와 답변 검토

[전문가 구현 명세](cae-research/expert-implementation.md)는 질문 준비, 원문 문헌 집합, 검색과 자료 충분성, 모든 답변 주장 검증, 읽기 도구, 공급자 호출 반복, 독립 검토기와 재심 이벤트의 정확한 형식, API, 오류와 시험 자료를 정한다. 이전의 단어 일치 범위만 보는 단일 문턱과 허용 질문 목록, 주장이 비어 있어도 통과시키는 규칙, 모호한 재심 연결 계약은 폐기했다. 고정 원문 8개와 고지문, 원문 범위 16개, 개발 문항 36개와 평가 문항 60개, 엄격한 형식과 기준 정답 자료는 인계 색인에서 실제 파일로 연결한다. 이 시험 자료가 실제 공급자나 Windows 제품 흐름을 검증한 것은 아니다.

<a id="program-results"></a>

## 프로그램 연결, 변환, 결과 조회와 RP 연결

### 1. 프로그램별 책임과 첫 사용자 흐름

Workbench는 프로그램 연결, 솔버와 변환기의 소유 프로세스, 실행과 파생 자료, 수치 추출과 이력을 관리한다. Prep은 솔버 원본 모델, 재료, 곡선, 문서 변경과 내보내기를 담당한다. RP, ParaView와 PrePoMax는 각자의 표시와 편집 기능을 담당하며 Workbench 원 결과의 판정을 바꾸지 않는다. 별도 `JobManager`, 유한요소 데이터베이스나 솔버 대기열을 만들지 않는다.

사용자 명령과 그 산출물은 다음처럼 구별한다.

1. 기존 **계산 실행**은 기존 `JobManager`에서 `NativeRun/job.json`, 원본 및 실행 입력, 솔버 원본 출력을 만든다. 후처리기를 자동 실행하지 않는다.
2. **결과 변환…**은 선택한 실행, 변환기와 프레임을 명시하여 별도 작업을 시작한다. 원본 바이트는 고정하고 변환 산출물만 `.derived`에 저장한다. 취소는 변환기만 종료한다.
3. **후처리 열기**는 사용자가 고른 완료 작업, 데이터셋과 프레임을 외부 소비자에서 연다. 솔버나 변환기를 자동 시작하지 않는다. 변환 자료가 없으면 변환 명령을 안내한다. 같은 실행의 가장 최근 작업으로 조용히 바꾸지 않는다.
4. **결과 조회, 보정 또는 전문가 근거 추가**는 같은 입력에서 같은 결과를 내는 추출기로 동일한 `ResultRefV1`을 읽는다. RP 화면 픽셀이나 LLM이 추정한 수치를 계산 근거로 쓰지 않는다.

첫 인도 범위는 Workbench의 범용 변환기, 결과와 소비자 공통부를 구현하고 기존 ParaView 경로를 유지하는 데까지다. RP 1.0.0의 수동 폴더 열기는 별도 안내다. **RP에서 같은 실행을 자동으로 여는 기능은 별도 AGPL 포크의 새 명령행 작업과 실제 Windows 인수에 의존하는 후속 작업이다. 현재 원본 배포본의 기능이 아니다.**

### 2. 기존 코드에서 반드시 재사용할 경계

| 기존 파일과 함수 | 현재 상태 | 확장 계약 |
|---|---|---|
| `caelab/connections.py:ProgramSpec/ProgramStatus/LaunchRequest/launch_request` | 프로그램을 탐색하고 알려진 화면 파일 열기 방식을 제공한다. 솔버의 화면 실행은 거부하며 RP는 미등록이다. | 기존 ID와 인수를 유지한다. 결과 소비자와 변환기의 지원 프로필을 별도 등록부에 추가한다. 알 수 없는 실행 파일에 임의 플래그를 보내 시험하지 않는다. |
| `caelab/native/adapter.py:SolverAdapter/get_solver_adapter` | 실행 요청을 만들고 결과를 수집하며 Radioss 단계 검증을 포함한다. | 솔버의 소유를 유지한다. 변환기를 `SolverAdapter`로 위장하지 않는다. |
| `caelab/jobs.py:JobManager._run_native/_WindowsRunGroup/_finish_owned` | 제한 시간과 취소, Windows Job 및 POSIX 프로세스 그룹을 관리한다. 복원한 PID를 인계받지 않는다. | 같은 소유 프로세스 실행기를 재사용하도록 최소한만 정리한다. `submit_operation`은 새 작업자이며 기존 솔버 제어와 구별한다. |
| `caelab/native/run_store.py:NativeRun/RunStore/atomic_json/copy_to` | 엄격한 데이터 클래스로 `job.json`을 읽고 목록과 해시, 다른 이름으로 저장 시 전체 자료를 관리한다. | `NativeRun` 최상위나 `job.json`을 바꾸지 않고 같은 `RunStore`가 작업 v1 보조 파일을 소유한다. 작업 쓰기와 읽기, 잠금, 선택과 복구를 확장한다. |
| `caelab/native/radioss.py:collect_radioss_results` | 실제 원본 A###를 해독하지 못하면 부분 판독이다. ASCII NODE/TETRA는 시험 자료다. | 기존 부분 판독을 유지한다. 실제 공식 변환기 출력은 별도 어댑터로 읽고 원 실행의 성공이나 `parse_state`를 덮어쓰지 않는다. |
| `caelab/native/evidence.py:native_results_file/capture_run_evidence/_finish` | 기록된 PVD와 VTU 해시를 검사한다. `_finish` 재판독은 CalculiX FRD 전용이다. | 기존 동작을 유지한다. 새 `capture_result_evidence(ref)`는 검증된 데이터셋 어댑터를 사용한다. `solver_id` 분기만 추가해 FRD 전용 함수를 범용인 것처럼 쓰지 않는다. |
| `caelab/native/frame_view.py:read_native_frame` | 선택한 VTU 본문 해시를 다시 확인한다. | 원 실행이나 파생 결과 참조를 명시하는 새 참조 해석기를 추가한다. 새 `.vtk`를 기존 `vtu_file` 키에 억지로 넣지 않는다. |
| `caelab/project.py:SCHEMA_VERSION=3/save_native_project` | 프로젝트 v3과 `RunStore.copy_to`의 다른 이름으로 저장을 제공한다. | 후처리 전용 보조 파일은 v3과 호환된다. 재료 연구 역할의 첫 저장은 v4이며 읽기와 다른 이름으로 저장은 v1–4를 지원한다. 공통 명세 C8을 따른다. 작업 선택은 `RunStore` 보조 파일에 둔다. 작업 중에는 다른 이름으로 저장이 실패해야 하며 완료 작업은 다시 검사하고 복사해야 한다. |

### 3. 지원 능력과 API

새 `caelab/artifact_refs.py`는 표준 데이터 클래스, 열거형과 JSON 검증을 한 곳에서 정의한다. 새 `caelab/native/operations.py`는 서비스 작업 조정을 담당한다. 새 `caelab/native/result_adapters.py`는 등록된 같은 입력에서 같은 결과를 내는 판독기와 추출기의 인터페이스를 정한다. 새 `caelab/result_readers/vtk_fields.py`는 선택적 수치 의존성 묶음의 VTK 판독기를 필요할 때 가져온다. 표준 라이브러리만 쓰는 공통 데이터 객체와 서비스는 Qt, VTK나 화면의 비공개 `_read_grid`를 가져오지 않는다. 새 `caelab/native/converters.py`는 변환기 인수와 입력 및 출력을 검사한다. 모듈 이름을 바꾸려면 통합 전에 단일 담당자가 정하며 공통 형식을 중복 정의하지 않는다.

```python
@dataclass(frozen=True)
class AdapterCapabilityV1:
    adapter_id: str               # opencourant.anim_to_vtk.v1, vtk.legacy.radioss.v1
    adapter_version: str          # 코드 package/version + source digest
    operations: tuple[str, ...]   # solve | convert | read | extract | view | edit
    source_formats: tuple[str, ...]
    target_formats: tuple[str, ...]
    supported_host_os: tuple[str, ...]
    support_profile_id: str       # exact solver/version/element/quantity matrix
    evidence_level: str           # declared | code_verified | fixture_verified | binary_verified

class ConverterAdapter(Protocol):
    capability: AdapterCapabilityV1
    def plan(self, request: ConversionRequestV1, verified_inputs) -> ConversionPlanV1: ...
    def validate(self, plan, outputs, check_cancelled) -> DatasetManifestV1: ...

class ResultAdapter(Protocol):
    capability: AdapterCapabilityV1
    def inspect(self, verified_dataset, check_cancelled) -> DatasetManifestV1: ...
    def read(self, ref: ResultRefV1, check_cancelled) -> NumericResultV1: ...

class ResultConsumerAdapter(Protocol):
    capability: AdapterCapabilityV1
    def prepare(self, ref, verified_dataset, staging_root) -> HandoffV1: ...
    def launch_request(self, program_status, handoff) -> LaunchRequest: ...
```

Workbench 공개 진입점에 다음 함수를 추가한다.

```python
list_result_capabilities(project: NativeProject, run_id) -> list[AdapterCapabilityV1]
plan_result_conversion(project: NativeProject, run_id, converter_id, frame_ids, options) -> ConversionPlanV1
submit_result_conversion(project: NativeProject, accepted_plan_hash) -> OperationRefV1
operation_snapshot(project: NativeProject, operation_id) -> OperationReceiptV1
cancel_operation(project: NativeProject, operation_id) -> bool
select_result_operation(project: NativeProject, node_id, source_run_id, operation_id, dataset_id) -> None
list_result_datasets(project: NativeProject, source_run_id) -> list[DatasetManifestV1]
query_result(project: NativeProject, ref: ResultRefV1) -> NumericResultV1
open_result(project: NativeProject, ref: ResultRefV1, consumer_id) -> ConsumerLaunchReceiptV1
capture_result_evidence(project: NativeProject, ref, asset_root) -> existing_evidence_dict
```

`project`는 기존 `Workbench._run_store(project)`로 해석한다. `source_run_id`, `node_id`, `operation_id`가 실제로 해당 저장소와 선택 노드에 속하는지 검사한다. 호출자가 준 루트나 경로로 조회하지 않는다. 도구 진입점은 호스트가 발급한 불투명 참조 `ProjectContextRef`를 `NativeProject`로 해석하며 임의 경로를 받지 않는다. 기존 `open_native_results(project,node_id)`는 호환성을 유지하고 선택한 파생 결과가 있으면 새 참조 해석기에 맡긴다.

계획 수립 `plan`은 실행하지 않는다. 필요한 변환기 실행 파일과 버전 및 해시, 입력 역할과 해시, 선택 프레임, 출력 형식과 예상 저장 공간을 보여준다. 실행 요청 때 계획이나 해시가 바뀌었으면 `STALE_PLAN`으로 새 계획을 요구한다. 어댑터 등록은 코드 허용 목록이며 임의 Python 모듈이나 사용자 셸 명령을 자동으로 불러오지 않는다.

첫 구현과 후속 지원을 명시적으로 구별한다.

| 단계 | 지원 가능한 동작 | 아직 지원하지 않는 동작 |
|---|---|---|
| POST01/02와 초기 판독기 | 기존 작성기의 VTU 절점 스칼라와 명시적 성분, 명시적 프레임, 원값과 최솟값 및 최댓값과 평균, 유한값 및 개수 집계를 제공한다. 같은 단위의 정확히 3성분인 벡터이며 알려진 정규직교 좌표계일 때만 크기를 계산한다. `evidence.describe`와 작업 수명은 합성 시험 자료로 검증한다. | 임의의 기존 VTK, 요소와 적분점 및 층, 좌표계 변환, von Mises와 주응력, 솔버 바이너리의 물리 검증은 미지원이다. |
| POST03 검증 후 | 고정한 OpenCourant `anim_to_vtk` 출력의 원 ID와 TIME, 지원하는 셸과 솔리드 필드, 별도로 검증한 텐서 연산을 제공한다. | 지원 프로필 밖의 보, 솔버와 버전, 확정하지 않은 재료 방향과 변형률 측도는 미지원이다. |
| POST05/06 검증 후 | RP 포크에서 후처리 전용으로 정확한 데이터셋과 프레임을 표시하고 응답 확인 절차를 제공한다. | 원본 RP의 자동 명령행과 RP 자체의 솔버 성공 판정은 지원하지 않는다. |

범용 데이터 형식에 연산이나 파일 형식이 나타난다고 첫 단계에서 지원하는 것은 아니다. 아래의 `von_mises`, `principal`, `curve_extract`와 좌표계 연산은 각 수치 어댑터의 후속 검증 조건을 통과해야 한다. 예외는 위에서 제한한 3성분 벡터 크기뿐이다.

지원은 불리언 하나로 표현하지 않는다. `SupportProfileV1`은 정확한 `{solver_id,solver_version,format_revision,element_family,quantity,association,location,component,basis,measure,shear_convention}` 조합별로 `supported`, `unsupported`, `unknown`과 검증 수준을 저장한다. 등가 스칼라 하나를 제공한다고 전체 텐서나 모든 보 및 셸 층을 지원한다고 확대하지 않는다. 프로그램을 찾았다는 사실과 솔버 및 모델링 지원 능력을 혼동하지 않는다.

프로그램의 지원 능력을 자동 탐색하는 기능을 일반화하지 않는다. 내장 프로필은 정확히 알려진 인수만 사용한다. 원본 RP 1.0.0은 `view_project=manual`, `post_only=false`다. 포크 계약 프로필을 명시해 등록한 경우에만 `--capabilities-file`로 확인한다. 알 수 없거나 누락됐거나 버전이 맞지 않으면 기존 프로그램의 단독 실행과 수동 폴더 안내로 전환하며 자동 열기 성공으로 표시하지 않는다.

### 4. 저장, 매니페스트, 다시 열기와 캐시

프로젝트의 `<name>.runs`가 소유한 `RunStore` 아래에 다음을 둔다.

```
<source_run_id>/job.json                         # 기존 solver 기록, 변경하지 않음
.derived/<source_run_id>/<operation_id>/operation.json
.derived/<source_run_id>/<operation_id>/inputs/<original_relative_path>
.derived/<source_run_id>/<operation_id>/outputs/<output_relative_path>
.derived/<source_run_id>/<operation_id>/dataset.json
.derived/<source_run_id>/<operation_id>/operation.log
.derived/<source_run_id>/<operation_id>/stderr.log
.post/<launch_id>/handoff.json                   # 사용자 선택 consumer staging
.post/<launch_id>/data/<original_relative_path>
.post/<launch_id>/receipt.json
selected-derived/<sha256(node_id)>.json
```

작업 ID와 실행 요청 ID는 새 UUID의 32자리 16진수다. 모든 본문 경로는 자신이 소유한 루트 기준 상대 경로다. 절대 경로, `..`, NUL, 심볼릭 링크와 재분석 지점으로 벗어나는 경로를 거부한다. 하드 링크도 쓰지 않는다. 바이트를 복사하고 전후 식별값과 SHA를 검사한다. 표준 오류는 텍스트 로그로 저장하고 표준 출력의 바이너리 결과와 같은 로그에 절대 합치지 않는다. `.tmp` 또는 `.part`만 작성한 뒤 검사하고 원자적으로 이름을 바꾼다. 완료 작업 응답은 마지막에 저장 확정한다.

작업 응답 `OperationReceiptV1`의 필수 내용은 다음과 같다.

- `schema_version=1`, `id`, `operation_kind='convert'|'extract'`, `source_run_id`, `source_receipt_sha256`, `source_input_revision`, `source_artifacts[]`, `adapter_id/version/code_sha256`, `executable:{path_audit,version,sha256,host_os}`, `plan_sha256`, `cache_key`, `created_at/started_at/finished_at`를 기록한다.
- `state=queued|preparing|running|validating|completed|failed|cancelled|interrupted`, `parse_state=unparsed|reading|readable|partial|unsupported|failed`, `stage`, `progress:{completed,total,unit}`, `returncode`, `error:{code,message}`, `warnings[]`, `outputs[]`, `dataset_ref`, `reused_from_operation_id|null`을 기록한다.
- 자료 항목은 공통 **`ArtifactRefV1 {id,role,path,sha256,size,format_id,producer:{id,version}}`**를 그대로 사용한다. `path`는 근거 묶음이 정한 저장소 루트의 상대 경로다. 별도 출처 정보의 `source_artifact_id`로 입력과 출력의 계보를 연결한다. 기존 `evidence.files`와 `NativeRun` 자료는 읽기 어댑터로 대응시키며 자동으로 다시 쓰지 않는다. 역할은 `source_native_animation`, `source_time_history`, `source_input`, `source_include`, `converter_input`, `converter_stdout`, `converted_field`, `converted_history`, `dataset_manifest`, `log`, `stderr`를 포함한다. `source_*` 참조는 실행 범위 경로로 원 실행을 참조할 수 있다. 복사한 `converter_input`은 항상 작업 범위다.
- 명령 감사 기록은 정확히 `{argv,cwd_relative,stdin_role|null,stdout_target,stderr_target,env_names,tool_version}`이다. 비밀값과 환경변수 값은 직렬화하지 않는다.
- PID는 진단용이다. 복구 시 기록된 PID를 인계받거나 종료하지 않는다. 잠금이 없는 비정상 종료 뒤 남은 활성 응답은 `interrupted`로 바꾼다. 임시 출력은 미완성으로 격리하고 완료 데이터셋으로 등록하지 않는다.

`source_receipt_sha256`은 현재 `job.json` 전체를 다시 계산한 해시가 아니다. 작업 준비 당시 정확한 바이트로 `inputs/source_receipt.json`에 복사한 고정 작업 응답 자료의 해시다. `source_receipt_ref: ArtifactRefV1`도 필수이며 두 해시가 같아야 한다. 다른 이름으로 저장해도 고정 바이트를 다시 쓰지 않는다. 응답 내부의 옛 절대 경로는 당시 감사 정보일 뿐 현재 파일을 열 경로가 아니다.

다시 열 때는 고정 작업 응답 자체의 해시와 원본 및 파생 자료의 바이트 및 해시를 확인한다. 현재 실행 응답과는 고정된 정체성 `(id,node_id,solver_id,input_revision,snapshot의 원/실행 입력 SHA 집합 및 native artifact SHA 집합)`만 대조한다. 원 실행이 검증되지 않은 과거 형식이면 없는 값을 `unknown`으로 유지하고 검증 등급으로 높이지 않는다.

현재 `Workbench.native_runs`는 입력 변경, INCLUDE 누락이나 단위 변경을 보고 반환 실행의 `stale/stale_reason`을 현재 문맥에서 계산한다. 위치는 `caelab/workbench.py:269–291`이다. 이 현재 상태를 고정 작업 응답이나 원본 식별값에 넣지 않는다. 현재 원자료 경로가 바뀌면 현재 문맥이 낡은 것이고 고정 사본의 솔버 원본 바이트나 해시가 다르면 손상이다. 과거 고정 결과를 읽을 때 현재 자료가 낡았다는 이유로 `HASH_MISMATCH`를 발생시키지 않는다. 최신 모델에 적용하거나 새 적합 문맥에서 쓸 때만 현재 변경 번호와 단위를 별도로 검사한다.

원본 식별값 `source_fingerprint_v1`은 원 실행, 노드와 솔버 정체성, `input_revision`, 원본 및 실행 입력과 솔버 출력의 역할, 상대 경로, SHA 집합을 정규 JSON으로 해시한다. 기록된 고정 솔버와 실행의 버전, 해시와 인수도 포함한다. 알 수 없는 버전은 명시적으로 `unknown`이다. 원본의 절대 감사 경로, 현재 낡음 표시나 바뀔 수 있는 화면 문맥은 캐시 정체성에 넣지 않는다. 데이터셋과 작업의 정규 해시는 자기 해시 필드만 제외한다.

`ArtifactRef` 자체에는 `scope`를 추가하지 않는다. 상위 출처 근거 묶음에 `artifact_owners:{artifact_id:{scope,id,source_run_id?}}`를 반드시 둔다. 모든 참조 해석기는 호스트가 고정한 `ProjectContext`와 등록 저장소만 사용하며 도구는 소유자나 경로를 입력받지 않는다. 경로 해석 규칙은 다음과 같다.

| `scope` | 소유자 ID | `ArtifactRef.path`의 기준 루트 |
|---|---|---|
| `run` | 원 실행 UUID | `Project RunStore.root/source_run_id` |
| `operation` | 작업 UUID이며 `source_run_id`가 필수다. | `RunStore.root/.derived/source_run_id/operation_id` |
| `consumer` | `post:<launch_id>` | `RunStore.root/.post/launch_id` |
| `consumer` | `material-study:<study_id>` | 호스트가 등록한 `materials/study_id` |
| `consumer` | `selection:<selection_id>` | `RunStore.root/selections/selection_id` |
| `consumer` | `evidence:<discussion_id>` | 질문 시 고정한 `discussion.parent/assets`다. 경로는 기존 `evidenceid/data/...`의 `stored_path`와 같다. |
| `consumer` | `corpus:<corpus_revision>` | 질문 시 고정하여 등록한, 변경하지 않는 문헌 집합과 색인 루트 |

범위는 **`run`, `operation`, `consumer` 세 개뿐**이다. 접두사는 소비자 작업 공간의 종류를 나타내며 새 범위를 추가하지 않는다.

`evidence:<discussion_id>`의 루트는 정확히 호스트가 등록한 `ExpertWindow.path.parent/assets`다. `<evidence_id>/data/...`의 `stored_path`를 앞부분 제거 없이 사용한다. 질문 시 허용 근거 ID, 하위 경로와 자료 ID를 함께 고정하므로 같은 자산 루트의 다른 근거를 조회할 수 없다. 같은 `artifact_id`를 상충하는 소유자로 다시 정의하면 `ARTIFACT_OWNER_CONFLICT`다. 같은 경로여도 범위가 다르면 자동 병합하지 않는다. 소유자 ID 형식, 문맥 소속, 허용 경로 안에 있는지, 심볼릭 링크나 재분석 지점으로 벗어나는지, 식별값, 읽기와 해시를 검사한다. 저장된 ID와 경로는 호스트 해석기를 통하며 임의 루트 재지정은 없다.

하위 호환 규칙은 다음과 같다. `.derived`가 없으면 파생 기록이 없는 것이다. 알 수 없는 작업 형식은 이유와 함께 미지원 읽기 전용으로 보이며 조용히 버리거나 다시 요청하지 않는다. `NativeRun` 데이터 클래스는 바꾸지 않는다. 작업 응답 보조 파일만으로 프로젝트 v3 버전을 높일 필요는 없다. `RunStore.copy_to`는 작업과 임시 저장의 잠금 소유권을 얻고 활성 작업 중에는 실패해야 한다. 전체 목록을 복사하고 새 루트에서 상대 참조, 고정 원본 작업 응답의 자료 해시와 고정 원본 정체성을 검사해야 한다. 가져온 과거 미검증 결과는 `unverified`를 명시하여 볼 수 있지만 검증된 적합이나 검토 입력으로 쓰지 않는다. 보관된 원본 루트 경로는 감사 문자열이며 현재 외부 의존 경로가 아니다.

캐시 키는 모든 원자료의 SHA와 역할, 고정 `source_fingerprint_v1`, 어댑터와 변환기 실행 파일의 해시 및 버전, 옵션과 프레임 선택, 판독기 버전, 좌표계와 측도 및 위치 대응, 단위 선언과 변환 정책, 파생 연산을 정규 해시한 값이다. 표시 테마와 카메라는 제외한다. 캐시를 쓰기 전에 모든 파일과 사용 자격을 다시 검사한다. 취소, 실패, 중단이나 필수 필드의 부분 누락은 성공 수치 캐시가 아니다. 여러 작업은 별도로 남기고 명시적으로 선택한다. 가장 최근 것인 `latest`로 대신하지 않는다.

### 5. 데이터셋 목록과 공통 결과 참조

데이터셋 매니페스트는 고정 원본과 작업 참조 및 자료, `dataset_kind=field|history`, `frames[]`, `series[]`, 위상과 ID 대응, 필드 지원과 확보 범위를 담는다. 공통 명세 C3이 필수 시계열 레코드를 정한다. 공간장 `field`는 프레임을 사용하고 이력 `history`는 실제 좌표와 원 행 자료를 가진 시계열을 사용한다. 이력의 프레임 목록은 비어 있으며 `frame_id`는 등록된 `series_id`다. 초기 이력 연산은 `curve_extract`뿐이다.

프레임은 안정된 문자열 `frame_id`, `source_step`과 실제 표본 좌표 `{quantity,value,unit,source}`를 가진다. 좌표는 시간, 하중이나 주파수다. 순번은 별도 값이며 물리 시간으로 전달하지 않는다. `TIME`이 없으면 `unknown`이며 순번을 대신 쓰지 않는다.

필드 설명의 예시는 다음과 같다.

```json
{
  "field_id":"stress.shell.layer3", "native_name":"2DELEM_Stress_(layer__3)_",
  "quantity":"stress", "association":"element", "components":["xx","xy","xz","yx","yy","yz","zx","zy","zz"],
  "location":{"ip":3,"layer":"integration_point","through_thickness_n":5},
  "basis":{"kind":"element_local","orientation_sha256":"..."},
  "measure":"cauchy", "shear_convention":"tensor",
  "units":{"symbol":"MPa","dimension":"pressure","scale_to_si":1000000,"source":"run_declared"},
  "geometry_state":"deformed", "coverage":{"expected":120,"valid":120,"missing":0},
  "status":"readable", "evidence_level":"binary_verified"
}
```

이 값들은 예시다. 솔버 출처로 확정하지 못하면 측도, 단위와 좌표계는 `unknown`, 환산계수는 `null`로 둔다. `Strain`이라는 이름만 보고 공학, 로그 또는 Green 변형률인지, 텐서 전단인지 공학 전단인지 추측하지 않는다. 형상은 `reference|deformed|unknown` 상태와 변위 의미 및 좌표 단위 선언이 필요하다. 이미 변형된 형상에 변위를 다시 적용해 두 번 변형하지 않는다.

재료의 직교축은 요소 로컬 축과 다르다. 방향 자료와 해시가 없으면 재료축 변환을 제공하지 않는다. 셸의 최댓값과 최솟값은 실제 제공된 위치만의 범위이므로 비교한 층 ID를 결과에 기록한다. 가운데 층을 `/3`이라고 단정하지 않고 N과 실제 배열 위치를 함께 보존한다.

`ResultRefV1`은 모든 수치 도구, 전문가와 재료 적합이 공유하는 단일 형식이다. 이 통합 문서 한 곳에서 정의한다.

```json
{
 "schema_version":1,"source_run_id":"...","source_node_id":"...","source_receipt_sha256":"...",
 "source_receipt_ref":{"id":"...","role":"source_receipt","path":"inputs/source_receipt.json","sha256":"...","size":1234,"format_id":"caelab.native-run.receipt","producer":{"id":"wb.runstore","version":"legacy"}},
 "operation_id":"...","operation_manifest_sha256":"...","dataset_id":"...","dataset_manifest_sha256":"...",
 "source_artifacts":[{"id":"...","role":"converted_field","path":"outputs/auditA001.vtk","sha256":"...","size":1234,"format_id":"vtk.legacy","producer":{"id":"opencourant.anim_to_vtk","version":"latest-20261011"}}],
 "frame_id":"anim:001","field_id":"stress.shell.layer3","component":"xx",
 "selection":{"selection_id":"...","kind":"element_ids","artifact_ref":{"id":"...","role":"selection","path":"payload.json","sha256":"...","size":20,"format_id":"caelab.selection.v1","producer":{"id":"wb.selection","version":"1"}},"revision_sha256":"...","count":1},
 "location":{"association":"element","ip":3,"layer":"middle"},
 "basis":{"kind":"element_local","orientation_sha256":"..."},"measure":"cauchy","shear_convention":"tensor",
 "units":{"symbol":"MPa","dimension":"pressure","scale_to_si":1000000,"source":"run_declared"},
 "geometry_state":"deformed","operator":{"id":"identity","version":"1","params":{}},
 "coverage":{"expected":1,"valid":1,"missing":0},
 "eligibility":{"view":true,"query":true,"fit":true,"review":true,"reason_codes":[]}
}
```

지원 여부와 사용 자격은 공학적 승인이 아니다. `review=true`는 독립 검토의 근거로 쓸 수 있다는 뜻이며 결론 통과를 뜻하지 않는다. 데이터셋 전체가 부분 판독이어도 필수 필드, ID, 시간, 좌표계, 측도와 단위가 완전하면 조회나 적합 정책이 허용할 수 있다. 필수 관측량 하나라도 불완전하면 목적함수는 무효다. 부가 필드가 부분 누락됐다는 이유만으로 모두 기각하지 않는다.

`stale`는 원 실행이 새 모델과 다르다는 뜻이고 원자료 해시 불일치는 손상이다. 과거 실행의 수치 조회는 과거 결과임을 명시하면 허용할 수 있다. 최신 모델 적용이나 적합은 명시한 실행 변경 식별값과 대조한다.

선택 ID 목록은 루트가 소유한 선택 영역 자료에 저장한다. `ResultRef`에는 위 참조와 개수만 넣는다. 화면이 선택 상태를 고정한 뒤 변경 불가능한 내용 식별값을 계산하며 도구와 LLM은 호스트가 허용한 `selection_id`만 입력한다. 모든 ID를 프롬프트에 복사하거나 임의 ID 목록으로 모델의 원하는 부분을 조회하게 하지 않는다.

수치 결과 `NumericResultV1`은 `ref`, `values` 또는 크기를 제한한 자산 참조, `sample_coordinates`, `counts`, `status`, `reason_codes`, `warnings`, `operator_provenance`를 담는다. NaN과 Inf는 JSON의 `null`과 마스크로 표현하고 유효 개수에서 제외한다. 축약 연산의 전체 확장 범위는 `identity`, `min`, `max`, `mean`, `norm`, `von_mises`, `principal`, `curve_extract`이며 연산 ID와 버전으로 등록한다. 초기 프로필은 앞 단계표의 원값, 최솟값, 최댓값, 평균과 제한된 크기 계산만 제공한다.

`norm`은 같은 단위로 선언된 정확히 3성분 벡터의 절점별 유클리드 크기다. 공간 집계, 텐서 노름이나 중첩 축약이 아니다. 알 수 없거나 정규직교가 아닌 좌표계, 혼합 단위는 미지원이다. 평균, 절점 평활화와 적분점 외삽은 원래 자료 연결 위치와 구별하며 자동 수행하지 않는다. LLM의 임의 수식을 실행하지 않는다. 요청한 필드, 위치나 좌표계가 없으면 사용 가능 목록과 `UNSUPPORTED_*`를 반환한다. RP의 `variable(key)`가 알 수 없는 값을 `U`로 대신하는 동작을 수치 어댑터에서 재사용하지 않는다.

기존 솔버 실행 유래 근거는 `source_kind=native_run`을 유지하고 근거 schema2에 `derived_result_ref=ResultRefV1`를 추가한다. 기존 v1은 읽기 어댑터로 대응시키며 자동으로 다시 쓰지 않는다. `source_run_id` 일치를 검사하고 입력이나 로그가 없으면 빈 텍스트와 미제공 상태로 보존한다. 보정은 자료형이 구별된 별도 `calibration_ref`로 다룬다.

공통 전문가 근거 묶음은 `kind=native_run`의 `payload.derived_result_ref`에 `ResultRef`를 넣는다. 문서와 조각도 같은 내용 해시, 출처와 사용 가능 상태의 바깥 형식을 공유한다. `EvidenceEnvelopeV1`의 정확한 종류, 바깥 필드, 종류별 본문과 기존 형식 읽기 어댑터는 공통 명세 C1에 고정한다. 전문가 읽기 도구는 안정된 참조 ID를 받고 실제 자료 해시를 검사한 뒤 조회하며 임의 파일시스템 경로를 받지 않는다.

### 6. 프로세스, 오류, 취소와 이벤트

변환 수명은 `queued→preparing→running→validating→completed`다. 준비 단계에서는 검사와 복사를 하고 실행 단계에서는 프로세스 그룹을 사용한다. 검증 단계에서는 판독, 필드 메타데이터와 해시를 확인한다. 어느 단계에서든 취소하면 소유한 변환기와 하위 프로세스를 종료하고 임시 출력을 닫거나 격리한 뒤 마지막에 취소 응답을 저장한다. 제한 시간을 넘으면 `failed/TIMEOUT`이고 정리를 확인할 수 없으면 `failed/PROCESS_CLEANUP_FAILED`다. 일부 변환이 끝났어도 전체 요청이 취소됐으면 완료 데이터셋을 등록하지 않는다. 성공한 프레임을 다시 쓰려면 명시적 재시도로 만든 새 작업에서 다시 검사한다.

하나의 `JobManager`만 `Popen`과 Windows Job 핸들을 소유한다. 변환 키마다 `RunStore` 작업 잠금을 사용하며 다른 이름으로 저장은 루트와 작업 잠금에 충돌하도록 한다. 같은 키가 이미 실행 중이면 `OPERATION_IN_PROGRESS`와 읽기용 작업 참조를 반환한다. 다른 제어기의 PID를 인계받지 않는다. 화면 창을 닫을 때는 자신이 요청한 작업만 취소하고 정리 상태를 기다린다. RP 창을 닫아도 솔버나 변환을 취소하지 않는다. 사용자가 닫은 RP를 Workbench가 임의로 다시 실행하지 않는다.

오류 코드는 `PROGRAM_UNAVAILABLE`, `UNSUPPORTED_CAPABILITY`, `STALE_PLAN`, `SOURCE_CHANGED`, `HASH_MISMATCH`, `PATH_ESCAPE`, `CONVERTER_EXIT`, `EMPTY_OUTPUT`, `INVALID_DATASET`, `MISSING_TIME`, `MISSING_ID`, `UNSUPPORTED_FIELD`, `UNSUPPORTED_BASIS`, `MEASURE_UNKNOWN`, `UNIT_UNKNOWN`, `PARTIAL_REQUIRED_FIELD`, `TIMEOUT`, `CANCELLED`, `PROCESS_CLEANUP_FAILED`, `STARTUP_FAILED`, `HANDOFF_MISMATCH`다. 불완전 자료는 원인과 사용 가능한 필드를 보여준다. 실패를 0값, 가짜 프레임, 다른 실행이나 최신 캐시로 대신하지 않는다.

### 7. 실제 변환기, 시간 이력과 RP의 상세 구현

[후처리 구현 명세](cae-research/post-implementation.md)는 고정 소스와 라이선스, 정확한 명령행 인수, 입력과 출력, 실제 ID와 TIME, 텐서와 층 및 축, 시간 이력 연결, 솔버 원본 수치 기준, Windows 조건, 실패와 취소, RP 명령행과 응답을 정한다. 이전 LAW1 NIP5, 순번을 시간으로 쓰는 방식, 표준 출력 CSV와 VOL을 초기 체적으로 가정한 문구는 폐기한다. 첫 TH4의 1점 적분 HEX8 프로필은 선택한 LAW33 재료 평가에 필수인 전이 단계다. 원본 RP의 수동 보기, Workbench가 소유한 수치 후처리와 선택적 포크의 정확한 자료 전달은 각각 독립적으로 인수한다.

선택한 포크의 명령행은 `--post-only --handoff <json> --receipt <json>`이다. 원본 배포본에 존재하는 기능으로 표시하지 않는다. 프로그램이 실행됐다는 사실을 후처리 수치와 화면이 일치했다는 성공으로 세지 않는다.
