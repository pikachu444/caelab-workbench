# 공통 서비스와 데스크톱 창, 저장, Prep 문서 적용 명세

개정 표시는 `R4-recovered`이며 개정일은 2026-11-12다. 같은 날의 계획 통합에서는 Workbench `2217429b6ccd8bce3063fdf60887ef2bcac97ae6`와 Prep `67ce4028c0a87745689ce97fbf0081b4a575054e`의 실제 코드·기존 계획을 다시 대조했다. 아래 최초 소스 기준과 과거 실행의 정체성은 유지한다. 실행 환경과의 연결이 끊긴 뒤, 실제 작성 내용과 토론 맥락을 바탕으로 다시 작성한 명세다. 회수하지 못한 로컬 커밋이나 원본 기록과 바이트까지 같다고 주장하지 않는다. 제품 구현 완료 보고도 아니다. 이 문서는 기존 통합 계약에서 데이터를 주고받는 공통 형식(DTO)을 확장하고 정정하며, 충돌하는 이전 문구보다 우선한다. 부속 명세 A, B, C, D는 각 분야의 정확한 수식, 출처, 상태, 출력과 인수 조건을 정한다.

## C0. 목표, 프로그램별 책임과 완료 조건

`DP`와 `Xia`는 재료 모델의 예시다. 목표는 여러 재료의 물성을 정의하고 여러 시험으로 계수를 동정한 뒤 최적화하며, 최적화에 쓰지 않은 보류 시험으로 평가하는 것이다. 지원하는 솔버의 입력 형식으로 정확히 내보내고, 사용자가 명시적으로 지시하면 열린 Prep 문서에 적용한다. 일반 CAE 이론, 실제 모델과 해석 결과에 대한 자문, 독립적인 답변 검토, 외부 프로그램을 통한 후처리도 제공한다. 목록에 모델 이름을 등록했다고 계산을 지원하는 것은 아니다. `J2` 모델이나 선형 모델만으로 범용 연구 기능이 완성됐다고 판정하지 않는다.

Workbench는 독립 프로그램을 연결하고 시험, 평가, 최적화, 근거와 대화가 생성되고 보존되는 과정을 관리한다. Prep은 솔버 원본 형식의 문서와 카드, 화면 편집, 실행 취소와 내보내기를 담당한다. 솔버는 구성식과 유한요소 계산을 담당하고 RP와 ParaView는 결과 표시를 담당한다. 공통 유한요소 모델 데이터베이스, 새로운 실행 대기열이나 `JobManager`, 범용 앱 내부 전처리기, 웹 모형 화면, 유료 API나 플러그인 또는 서버를 도입하지 않는다. Windows 로컬 환경의 `PySide6==6.9.1`에서 `Core`, `Gui`, `Widgets`, `UiTools`를 사용하고 Matplotlib과 기존 FreeCAD, Prep, RP, ParaView를 활용한다.

최종적으로 사용자는 다음 흐름을 실제로 수행할 수 있어야 한다.

1. 실제 CSV 파일을 선택하고 열, 단위, 물리량의 측도, 시험 조건과 시험 분할을 확인한다.
2. 모델을 선택하고 계수를 고정값, 최적화할 자유값, 다른 계수와 연계한 값으로 구분한 뒤 범위를 설정한다.
3. 실제 평가와 최적화를 실행하고 시험별 원자료, 예측, 잔차, 보류 시험 결과와 실패를 확인한다.
4. 저장한 연구를 다시 열고, 지원하는 카드로 내보내거나 열린 Prep 문서에 적용 버튼으로 반영한다.
5. 내용이 바뀌지 않게 고정한 동일한 근거로 전문가에게 질문하고 별도 검토를 받는다.
6. 선택한 결과를 독립 후처리기로 연다.

각 동작은 실제 파일, 프로세스와 수치에 연결돼야 한다.

## C1. 공통 계약의 담당 모듈과 소스 기준

기준 커밋은 Workbench `104cdd6888a5554eecc8cf965c60ff1bd980b6da`, Prep `555af5b3ab2c6e7dc276111609302ef565bb4fc8`, RP `cec23cbdd4058237e04fdcc7ecb06a3834578cae`다. 아래 신규 계약은 앞으로 구현할 API이며 이미 존재하는 API와 구별한다.

- 새 파일 `caelab/artifact_refs.py`는 자료 참조 `ArtifactRefV1`, 결과 참조 `ResultRefV1`, 데이터셋 목록 `DatasetManifestV1`, 수치 결과 `NumericResultV1`, 근거 묶음 `EvidenceEnvelopeV1`을 담당한다. 이 데이터 객체는 생성 후 바꾸지 않으며 JSON을 엄격하게 검사한다. Qt, VTK, 솔버를 가져오지 않는다.
- 기존 `mdo/variables.py`의 `VariableSpace`와 인코딩 및 디코딩 기능, `mdo/optimize.py`의 `OptimizationProblem`, `EvaluationRequest`, `EvaluationObservation`, `optimize`, `study_manager.py`의 `StudyManager`, 기존 목적함수, 제약, 실험계획과 민감도 기능을 사용한다.
- 새 `materials/service.py`의 `CalibrationStudyService`는 이 모듈들과 기존 `JobManager`, 실행 저장소 `RunStore`, 원자적 JSON 저장 함수 `atomic_json`, 파일 잠금 `FileLock`을 조합한다. CalculiX와 INP에 종속된 `MDOStudyService._submit`이나 대리모델 전용 흐름을 범용 직접 평가기로 호출하지 않는다. 기존 MDO 저장 형식 v2를 새 연구 형식으로 이행하지 않는다.
- `materials/contracts.py`, `experiments.py`, `residuals.py`, `evaluator.py`, `registry.py`, `units.py`와 각 어댑터가 물리 모델과 시험 영역을 담당한다. 공통 서비스가 모델명으로 분기하지 않는다. JSON에서 모듈, 셸 명령이나 임의의 Python 코드를 실행하지 않는다.
- 자료 소유 범위 `scope`는 `run`, `operation`, `consumer`만 허용한다. 호스트의 참조 해석기가 등록된 저장소 문맥을 관리한다. 다른 이름으로 저장한 뒤에는 같은 `study_id`여도 루트 경로가 다르므로, 전역 ID만으로 경로를 찾는 캐시를 사용하지 않는다.
- 원본 바이트, 변환 절차, 수치 내용의 해시, 실제 파일의 SHA, 당시 감사용 경로와 현재 적용 가능성을 구별한다. 모든 진입점에서 상대 POSIX 경로인지, 허용 루트 안에 있는지, 드라이브나 UNC 경로인지, `..` 또는 NUL을 포함하는지, 심볼릭 링크나 재분석 지점을 통하는지 검사한다. SHA, 크기와 등록된 문맥도 같은 규칙으로 검사한다.
- 고정한 근거의 해시는 검증된 전체 근거 묶음을 정규 JSON으로 직렬화한 뒤 SHA256으로 계산하되 자기 필드 `content_sha256`만 제외한다. 현재 화면 상태, 현재 자료가 낡았는지 여부, 현재 문서 바이트를 고정된 원자료 해시에 섞지 않는다.

공통 `EvidenceEnvelopeV1`의 전송 형식은 다음으로 고정한다.

```text
{schema_version:1,id,kind,content_sha256,provenance,availability,payload}
```

`id`는 호스트가 발급한 UUID다. `kind`는 `native_run`, `manual`, `calibration`, `document_chunk` 중 하나다. `result`라는 별도 종류는 없다. 솔버 실행에서 유래한 결과는 `native_run`의 `payload.derived_result_ref`로 표현한다.

출처 정보 `provenance`와 사용 가능 상태 `availability`는 다음 형식이다.

```text
provenance={producer:{id,version},sources:[{source_kind,source_id,source_revision,url}],artifact_owners:{artifact_id:{scope,id,source_run_id?}}}
availability={state:available|partial|source_unavailable|corrupt|unsupported,reason_codes:[],missing_artifact_ids:[]}
```

`source_kind`는 `native_run`, `manual`, `calibration`, `official_source`, `paper`, `solver_manual`만 허용한다. 실제로 모르는 `source_revision`과 `url`에는 추정값 대신 `null`을 넣는다. 소비자 자료, 근거와 문헌 집합은 등록된 루트에서 경로를 해석한다. 원자료를 사용할 수 없어도 알려진 해시, URL과 정체성은 보존하며, 실물이 있는 것처럼 제공하지 않는다.

`payload`는 `kind`별로 다음 필드를 엄격하게 구별하는 형식이다.

```text
native_run: {legacy_evidence_ref:ArtifactRefV1,source_run_id,source_node_id,derived_result_ref:ResultRefV1|null}
manual: {legacy_evidence_ref:ArtifactRefV1}
calibration: {evaluation_ref:ArtifactRefV1,study_id,content_revision,evaluation_id}
document_chunk: {corpus_id,corpus_revision,source_id,source_ref:ArtifactRefV1,chunk_id,chunk_ref:ArtifactRefV1,document_sha256,text_sha256,edition,span:{line_start,line_end,char_start,char_end},page_start,page_end,section_path:[]}
```

`native_run`에서는 파생 결과의 `source_run_id`가 본문과 일치해야 한다. `legacy_evidence_ref`는 현재 캡처 기능이 만든 기존 근거 설명 파일의 고정된 JSON 바이트를 참조한다. 원래 메타데이터, 본문, 파일과 상대 경로를 보존한다. `manual`도 기존 수동 근거의 설명 파일 원본 바이트를 참조하며 솔버 결과를 만들어 넣지 않는다. `calibration`은 고정된 평가 자료 `CalibrationEvaluationArtifact`를 읽어 실제 시험, 채널, 잔차, 시험 분할과 제약을 전달한다.

`document_chunk`의 페이지 값은 `null`일 수 있다. 원문 행 번호는 1부터 시작하며 끝 행을 포함한다. 문자 범위는 유니코드 코드포인트 기준으로 0부터 시작하며 끝 위치는 포함하지 않는다. `chunk_ref`는 실제 전달할 정확한 UTF-8 본문 바이트이고 `source_ref`는 실제 원문이다. 양쪽의 해시와 원문 범위가 일치해야 한다.

모든 공통 레코드와 종류별 레코드는 `additionalProperties=false`를 적용한다. JSON 수치는 유한해야 하며, `null`을 허용하는 키까지 포함해 모든 필수 키를 검사한다. 기존 토론 근거의 메타데이터, 본문과 파일을 자동으로 다시 쓰거나 이행하지 않는다. 읽기 어댑터는 원래 설명 파일의 정확한 바이트를 소비자 자료로 고정하거나 기존의 고정된 설명 파일 참조를 사용한다. 새 근거 묶음은 메모리 또는 새로 고정한 근거 상태로 제공한다. 기존 `kind`나 `source_kind`가 `manual`, `native_run`, `calibration`이면 위 형식으로 명시적으로 대응시키고, 알 수 없는 종류는 `unsupported`로 보존한다.

기존 `evidence.files`의 `stored_path`는 소유 자산 루트 기준의 경로를 그대로 유지한다. 허용된 근거 하위 경로와 저장소 문맥, 각 파일의 SHA를 다시 검사한다. 새 근거 묶음 해시는 모든 `provenance`, `owners`, `availability`, `payload`를 포함하고 `content_sha256`만 제외한다. 기존 설명 파일의 해시와 새 묶음의 해시는 서로 다른 자료를 식별한다.

## C2. 문서 변경 확인과 계산 내용 식별

`document_revision`은 현재 목록 파일인 매니페스트의 실제 바이트를 SHA256으로 계산한 값이다. 이 값을 매니페스트 안에 재귀적으로 저장하지 않는다. `expected_revision` 인수는 항상 이 값을 뜻한다. 저장 직전에 디스크 바이트와 대조하여 다른 작업이 문서를 바꾸었는지 검사하는 CAS 방식을 쓴다.

`content_revision`은 계산 내용을 식별하는 정규 해시다. 모델, 계수, 고정한 시험과 변환 및 비교, 평가 정책, 수치 설정, 최적화 설정과 원자료 정체성을 포함한다. 선택한 점, 창 위치, 현재 진행률, 평가 색인과 현재 절대 경로는 제외한다. `MaterialEvaluationRequest.study_revision`과 `OptimizationProblem.revision`에는 `content_revision`을 넣는다. 계산 중 자신의 평가 색인을 저장해도 해당 계산의 내용 식별값은 바뀌지 않는다. 구성을 바꾸면 이전 내용을 수정하지 않고 새 `content_revision`을 만든다.

현재 상태를 읽어 고정한 `StudySnapshot`과 작업 결과를 돌려주는 응답 형식은 다음과 같다. 뜻이 모호한 `revision` 필드는 제거한다.

```text
StudySnapshot={study_id,document_revision,content_revision,path,data_root,read_only,busy_operation,active_campaign_id,active_operation_id,experiment_ids,point_ids,diagnostics}
ImportReceipt={study_id,previous_document_revision,document_revision,previous_content_revision,content_revision,experiment_id,source_ref,converted_refs,warnings}
EvaluationReceipt={campaign_id,evaluation_id,document_revision,content_revision,state,artifact_ref,observation}
CampaignReceipt={campaign_id,content_revision,state,termination_reason,evaluation_ids,selected_ids,budget_usage}
CancelReceipt={campaign_id,accepted,state}
```

취소 응답에서 `requested`는 요청을 받았다는 뜻이며 작업자나 솔버 프로세스의 종료를 확인했다는 뜻이 아니다.

작업자는 시작할 때 계산 내용과 자신의 `operation_id`를 고정한다. 평가 색인을 저장할 때마다 자신이 소유한 최신 `document_revision`으로 변경 검사를 갱신한다. 화면 콜백 토큰은 `(window generation,operation_id,content_revision)`이다. 창이 닫혔거나 다른 연구로 바뀌었거나 이전 요청에 해당하면 결과를 화면에 표시하지 않는다. 이미 생성한 작업 응답은 보존한다.

`active_campaign_id`와 `active_operation_id`는 각각 UUID 또는 `null`이다. 유휴 상태에서는 둘 다 `null`이다. 계산은 짧은 상태 잠금 안에서 두 ID와 고정 내용을 등록한 뒤 시작한다. 첫 진행 알림은 `{stage:"started",campaign_id,operation_id,content_revision}`이며 실제 계산·프로세스 제출 전에 보낸다. 그 알림을 놓쳐도 `snapshot()`에서 활성 ID를 읽을 수 있다. 알림 함수는 상태 잠금 밖에서 호출하며 화면을 직접 바꾸지 않는다. 다른 작업의 ID나 종료한 ID로 현재 작업을 제어할 수 없다.

외부에서 디스크 문서를 바꾸면 `EXTERNAL_MANIFEST_CHANGED`로 새 실행 요청을 중단하고 자신이 소유한 작업의 취소를 요청한다. 결과는 별도로 보존하며 타인의 매니페스트를 덮어쓰지 않는다. 완료 화면은 시작 당시의 낡은 상태로 덮어쓰지 않고 최신 상태를 읽는다.

## C3. 예측 묶음과 결과 전달

어댑터가 계산 결과를 수집하는 `MaterialAdapter.collect(plan,artifacts)->PredictionBundle`에서 `PredictionBundle`은 다음 `PredictionBundleV1`의 별칭이다. 프로세스 종료 코드가 0이거나 파일이 생성됐거나 화면에 표시됐다는 사실로 결과 수집의 성공을 대신하지 않는다.

```text
PredictionBundleV1={schema_version:1,evaluation_id,request_id,study_revision,model_revision,adapter:AdapterIdentity,experiments:[ExperimentPredictionV1],optimizer_scalars:{registered_response_id:finite_number|null},artifacts:[ArtifactRefV1],artifact_owners:{artifact_id:{scope,id,source_run_id?}},run_ids:[],diagnostics:[]}
ExperimentPredictionV1={schema_version:1,experiment_id,experiment_revision,state,initial_state_ref,channels:[PredictedChannelV1],execution_refs:[],diagnostics:[]}
PredictedChannelV1={schema_version:1,observable_id,observable:ObservableSpec,independent_ref,sample_keys_ref,sample_key_columns:[],values_ref,mask_ref,component_order:[],value_kind,representation,coverage,source_refs:[],source_result_refs:[]}
```

독립축 참조 `independent_ref`에는 실제 시간, 주파수 또는 하중의 N행이 들어간다. 값과 유효성 마스크는 N×C이고 표본 식별 키는 N×K다. `sample_key_columns`에는 행, 절점, 요소, 적분점, 층, 구간과 경로 분기를 구별할 열을 선언한다. 단순 순번을 물리적 시간으로 바꾸지 않는다. 선언한 관측량 계약과 표현 방식, 배열 크기, 단위, 좌표계, 측도, 전단 정의, 부호, 위치와 `coverage`가 정확히 일치해야 한다. 여기서 `coverage`는 필요한 표본과 성분 중 실제 결과가 확보된 범위를 뜻한다.

재료점 계산이나 라이브러리 계산은 `source_result_refs=[]`로 두며 유한요소 결과 참조를 만들어 넣지 않는다. `source_refs`에는 실제 배열, 라이브러리, 상태와 실행 출처를 기록한다. 무효값은 정규화한 유한값 0과 `mask=false`로 저장하며, 그 0을 유효 응답으로 사용하지 않는다. NaN을 포함한 솔버 원본 바이트는 별도 원자료로 보존한다. 적합 오차 `fit_loss`는 호스트만 계산하며 어댑터의 `optimizer_scalars`에 넣을 수 없다.

`DatasetManifestV1`은 `dataset_kind=field|history`, `frames:[]`, `series:[]`를 필수로 가진다. 공간장인 `field`는 실제 프레임과 빈 `series`를 가진다. 이력인 `history`는 빈 `frames`와 다음 시계열 목록을 가진다.

```text
series[{series_id,coordinate_ref:ArtifactRefV1,row_count,source_row_index_ref:ArtifactRefV1,field_ids:[]}]
```

이력 결과의 `ResultRefV1.frame_id`에는 등록된 `series_id`, 예를 들어 `history:all`을 넣는다. 물리적 프레임 `all`이 있다는 뜻은 아니다. `NumericResult.sample_coordinates`는 실제 `time[]`를 반환한다. 이력의 `geometry_state=unknown`이며 처음에는 곡선 추출 연산 `curve_extract`만 지원한다. 행 하나의 상태나 최솟값 및 최댓값처럼 시계열을 축약하는 연산은 별도의 미지원 기능이다. RP의 공간장 소비자는 이력을 거부하고 곡선 소비자는 시계열을 받는다. 기존 `ResultRef`의 최상위 필드를 복제하거나 다른 형식으로 분기하지 않는다.

원본 `ResultRef`를 연산 요청에 사용할 때는 원본 참조를 바꾸지 않는다. `operator`만 바꾼 요청 참조를 만들고 두 참조의 해시를 추적 기록에 남긴다. 알 수 없는 필드를 `U`로, 층을 `membrane`으로, 텐서 전단을 공학 전단으로, 응력을 힘으로 추정해 대신 사용하는 처리는 금지한다.

## C4. 비교, 목적함수와 평가 정책의 일관된 판정

비교 정의 `ComparisonSpec`에 `id:str`, `required_for_fit:bool`, `required_for_validation:bool`을 추가한다. `required_for_fit=true`는 학습 시험인 `train`에서만 허용하고, `required_for_validation=true`는 검증 시험 `validation`이나 보류 시험 `holdout`에서만 허용한다. 두 값이 동시에 `true`이면 오류다. 둘 다 `false`이면 진단용 비교다. 최적화에는 적합에 필수인 비교가 적어도 1개 있어야 한다. 평가만 수행할 때는 진단용 비교만 있어도 된다. `required_for_fit`인 비교만 고정된 목적함수에 참여한다. 후보마다 비교, 표본이나 가중치 분모를 바꾸거나 실패한 시험을 제외하지 않는다.

잔차는 고정한 관측 격자에서 계산한다. 물리량, 측도, 축, 위치, 성분과 단위를 맞춘 뒤 선형 보간하며, 각 관측 좌표가 예측 좌표 구간 안에 있어야 한다. 같은 좌표의 표본이 있으면 그 값을 쓰고, 그렇지 않으면 양옆의 예측 좌표로 선형 보간한다. 외삽은 금지한다. 반복 변형률을 가진 순환 경로는 실제 시간과 분기를 유지한다. 미리 선언한 마스크만 사용한다. 수식은 `r=(pred-obs)/scale`, `channel_loss=sum(q*r²)`, `q>=0,sumq=1`이다. 실험과 채널의 가중치는 적합 비교 집합에 대해 한 번만 정규화한다. `scale`은 양의 고정값으로, 공학적으로 정하거나 학습 자료만 쓰는 절차로 정한다. 보류 시험이나 후보 결과로 다시 계산하지 않는다. 진단 실패는 목적함수의 집합과 분모에 영향을 주지 않는다.

평가 결과 `AssessmentResultV1`은 다음 한 가지 형식으로만 직렬화한다.

```text
objective={eligible,fit_loss:number|null,reason_codes:[]}
assessment={fit_status,validation_status,holdout_status,adequacy_status,uncertainty_status,identifiability_status,constraint_status,constraint_checks:[],policy_ref,channel_checks:[],split_checks:[],warnings:[]}
```

| 필드 | 허용값 |
|---|---|
| `fit_status`, `validation_status`, `holdout_status`, `adequacy_status` | `pass`, `fail`, `not_assessed` |
| `uncertainty_status` | `pass`, `fail`, `not_computed` |
| `identifiability_status` | `pass`, `fail`, `not_computed`, `not_identifiable` |
| `constraint_status` | 기존 `FEASIBLE`, `MARGINAL`, `INFEASIBLE`, `NOT_ASSESSED` |

각 보고서의 `not_computed`와 `not_identifiable`를 필수 타당성 요구에 반영할 때만 아래의 통과, 실패, 미평가 규칙으로 변환한다. `train_fit` 같은 별도 저장 별칭은 만들지 않는다. 평가 정책은 변경하지 않는 자료로 저장하며 필수 채널의 `acceptance_limit`, 시험 분할별 집계 방식, 타당성 및 계수 식별성과 불확실성 요구, 물리적 검증과 수치 검증 및 참조 검증의 범위를 고정한다.

판정 순서는 다음과 같다.

1. 필수 학습 시험의 실행, 변환, 전체 표본과 성분 확보, 정체성, 측도 또는 수치가 유효하지 않으면 `objective.eligible=false,fit_loss=null`이다. 실패, 취소 또는 필수 결과의 부분 누락을 큰 실수 벌점으로 바꾸지 않는다.
2. 결과는 유효하지만 오차가 크면 유한한 손실을 유지하고 `fit_status=fail`로 둔다. 최적화기는 이 값을 개선할 수 있다.
3. 채널의 `acceptance_limit=None`이면 `not_assessed`다. 필수 비교가 0개인 시험 분할도 `not_assessed`다. 필수 항목에 실패가 있으면 실패가 우선하고, 그다음 누락이나 미실행은 `not_assessed`로 판정한다. 모든 항목이 실제 평가를 받아 통과했을 때만 `pass`다.
4. 검증 시험이나 보류 시험의 실패 및 미실행으로 유효한 학습 손실을 삭제하지 않는다. 보류 시험은 최적화, 척도 계산과 모델 선택에 쓰지 않는다.
5. 타당성 정책이 없으면 `not_assessed`다. 필수 식별성 결과가 `not_identifiable`이면 실패이고 `not_computed`이면 미평가다. 계산 종료, 낮은 적합 손실이나 전문가의 말만으로 실제 재료의 타당성을 입증하지 않는다.
6. 전문가의 `pass`, `revise`, `insufficient`는 정확히 같은 고정 문맥일 때만 해당 요구의 `pass`, `fail`, `not_assessed`에 반영한다. 모델이나 데이터가 바뀌면 이전 검토를 재사용하지 않는다.
7. 수치 검증 자료인 `numerical_fixture`의 통과는 해당 수치 참조 범위만 검증한다. 독립 실험이 없으면 재료가 검증됐다는 `material validated` 표현을 만들지 않는다.

### C4 보완: 탐색과 보류 시험의 실행 경계

`MaterialEvaluationRequest`와 `CalibrationEvaluationArtifact`에 정식 필드 `execution_phase`와 `selection_ref`를 둔다. `execution_phase`는 `exploration` 또는 `confirmation`이고, `selection_ref`는 아래 선택 기록의 `ArtifactRefV1` 또는 `null`이다. 이 필드를 어댑터 임의 설정이나 숨은 추가 키로 넣지 않는다. 요청의 순서 있는 실험 목록은 실제 실행할 목록만 가진다.

`optimize` 내부의 탐색 요청은 `execution_phase=exploration,selection_ref=null`이다. 필수 학습 시험과 미리 고정한 선택 정책에 선언된 검증 시험만 계획·배정한다. 보류 시험은 계획, 실제 계산, 잔차, 후보 캐시, 대리모델 학습과 진행 알림의 후보별 결과에서 제외한다. 이 단계의 `holdout_status=not_assessed`다. 원시험 파일을 사용자가 알고 있다는 사실과 후보의 보류 오차를 계산해 선택에 사용하는 것은 구별한다.

공개 `evaluate`의 `execution_phase` 기본값은 `exploration`이다. 기본 평가와 최적화 탐색에서는 보류 시험을 실행하거나 결과를 노출하지 않는다. 사용자가 값 또는 저장된 후보를 최종 평가하면 `evaluate(...,execution_phase="confirmation",cache_policy="refresh")`를 호출한다. `confirmation`인데 `cache_policy`가 `refresh`가 아니면 `CONFIRMATION_REQUIRES_REFRESH`로 실행 전에 거부한다. 서비스는 실제 보류 결과를 읽기 **전에** 다음 선택 기록을 원자적으로 고정한다. 별도 확인 대화상자를 추가하지 않고 기존 최종 평가 동작에서 처리한다.

```text
SelectionRecordV1={schema_version:1,selection_id,study_id,content_revision,model_revision,adapter:AdapterIdentity,physical_parameters,parameter_values_sha256,numerical_settings_sha256,normalization_ref:ArtifactRefV1,assessment_policy_ref:ArtifactRefV1,selection_policy_ref:ArtifactRefV1,source_evaluation_ids:[],training_experiment_revisions:[],validation_experiment_revisions:[],holdout_experiment_revisions:[],created_at}
```

`selection_id`는 호스트 발급 UUID, `created_at`은 UTC 시각이다. `physical_parameters`는 평가 요청과 같은 값·단위의 전체 계수이고 해시는 그 정규 JSON이다. 실험 개정 목록의 각 항목은 `{experiment_id,experiment_revision}`이며 실제 실행 순서를 유지한다. `source_evaluation_ids`는 같은 고정 내용과 계수에 실제로 일치하는 기존 평가만 참조하고, 수동으로 새 값을 평가하면 빈 목록일 수 있다. 기록은 `revisions/<content_revision>/selections/<selection_id>.json`에 저장하고 같은 연구 소유자의 ArtifactRef로 참조한다.

`configure`의 `assessment_policy`에는 정식 항목 `selection_policy={mode:training_only|manual_with_validation,validation_comparison_ids:[]}`를 둔다. 생략한 호출은 `training_only`와 빈 목록을 명시적으로 고정하며 숨은 설정으로 취급하지 않는다. `training_only`는 검증 시험을 후보 선택에 사용하지 않는다. `manual_with_validation`은 선택 전에 선언한 validation 비교 ID만 후보 진단으로 계산·표시하고 사용자가 최종 후보를 고른다. 두 방식 모두 최적화 목적함수는 학습 비교만 사용한다. 존재하지 않는 ID, validation 이외의 ID나 holdout ID는 설정 시 거부한다.

호스트는 고정 ComparisonSpec의 비교 ID·척도·표본 격자/가중치·채널 가중치·단위·학습 원자료 식별값을 정규 JSON 자료로 저장하여 `normalization_ref`를 만든다. `assessment_policy_ref`는 실제 고정 정책 원문이다. `selection_policy_ref`는 `{schema_version:1,mode,validation_comparison_ids:[],optimization_config_sha256,comparisons_sha256}`의 정규 JSON 자료다. mode/ID는 위 설정, 비교 해시는 고정 비교 집합, 최적화 설정 해시는 실제 사용한 OptimizationConfig에서 얻으며 최적화 없이 직접 값을 정하면 `null`이다. 서비스가 미래 구현자가 임의 발명한 정책이나 보류 결과로 이 참조를 채우지 않는다.

이후 `execution_phase=confirmation` 요청은 해당 `selection_ref`와 정확히 일치하는 내용·계수·정책·실험 개정만 실행한다. 최종 확인의 `cache_policy=refresh`는 학습·선언 검증·보류 시험을 새 실제 계산으로 확인하며, 보류 결과로 계수·모델·선택 정책을 자동 변경하지 않는다. 다른 계수나 개정과 결합한 선택 참조는 실행 전 `SELECTION_MISMATCH`로 거부한다. 탐색·확인의 캐시 키는 단계와 실제 실험 목록을 포함하므로 서로 대체할 수 없다. 선택 ID·시각만으로 동등한 계산 캐시를 새로운 실제 계산이라고 표시하지 않는다.

보류 결과를 처음 열거나 계산해 제공한 사실은 연구의 변경하지 않는 `holdout-exposures/<event_id>.json`에 선택 참조·시험 원자료 해시·시각으로 남기고 SaveAs에도 복사한다. 같은 `content_revision`이어도 계수·모델/어댑터·수치 설정·정규화·평가/선택 정책 중 하나라도 바뀐 선택이면 기존 노출 원자료 해시와 대조한다. 선택 절차 식별값은 이 필드들의 정규 해시이며 UUID·시각·저장 경로는 제외한다. 이미 노출한 같은 보류 자료를 다른 선택에 쓰면 `split_checks`에 `HOLDOUT_REUSED_FOR_SELECTION`을 남기고 미사용 독립 검증이라고 표시하지 않는다. 독립 보류를 요구하는 정책의 타당성 판정은 미평가로 유지한다. 같은 동결 선택을 그대로 재현하는 재계산은 허용하지만 새로운 독립 시험이 추가된 것으로 세지 않는다. 다시 독립 검증을 주장하려면 새로운 미사용 보류 자료가 필요하다. 기존 기록이나 시험 원문을 지워 노출 이력을 없애지 않는다.

첫 보류 수치·곡선·점수를 UI, 진행 알림, 전문가 도구, 평가 조회 또는 내보내기에 제공하기 **전에** 노출 이벤트의 원자적 저장과 기록 확정을 확인한다. 저장 실패 시 `HOLDOUT_EXPOSURE_RECORD_FAILED`로 제공을 거부하고 해당 값·곡선·점수를 반환하지 않는다. 이미 계산한 내부 자료는 보존하되 재열기 후 조회에서도 같은 기록 확정 전에는 노출하지 않는다. 단순한 원 CSV 미리보기를 금지하는 규칙이 아니다. 실패 주입 검사에서는 이벤트 저장 실패·재열기·내보내기/도구 조회에서 보류 결과 전달이 0건인지 확인한다.

CORE02 검사는 탐색 시 보류 어댑터 호출·원결과 읽기·보류 잔차·후보 알림이 0건인지, 선택 기록 저장 전에는 보류 실행이 시작되지 않는지, 선택 변경·복사/재열기·최종 재계산 실패에서도 이 경계가 유지되는지 확인한다. 별도 독립 시험의 실제 적합성은 합성 자료 회귀와 구별한다.

## C5. 최적화에 전달할 스칼라 응답과 물리적 제약

지원 능력 보고서 `CapabilityReport`에 다음 필드를 추가한다. 스칼라 응답의 정의 형식도 아래와 같다.

```text
optimizer_responses:tuple[ScalarResponseSpecV1,...]
optimizer_constraints:tuple[ConstraintSpec,...]
differentiable:bool
ScalarResponseSpecV1={schema_version:1,response_id,unit,source_kind:residual|parameter_expression|adapter_scalar,source_name}
```

모든 응답 선언에는 `schema_version=1`이 필수다. `source_name`은 설치해 등록한 함수나 제한된 수식의 ID이며 JSON 실행 코드가 아니다. `fit_loss`는 호스트가 계산하는 무차원 스칼라다. 단위의 직렬화 문자열은 기존 계약에 따라 빈 문자열 `""`이며 물리적으로 1이다. 시험별 곡선과 손실은 평가 자료에 보존한다. 최적화 응답에는 `fit_loss`와 미리 등록한 제약 또는 진단 스칼라만 넣는다. 실행 중 미등록 응답 ID를 만들지 않는다.

모델 프로필에 필수인 `ConstraintSpec`과 사용자 제약을 고정한 계산 내용에 합쳐 기존 `OptimizationProblem.constraints`와 `response_units`로 전달한다. 사용자는 필수 제약을 제거하거나 완화할 수 없다. ID 중복, 미등록 응답, 단위나 경계값의 불일치는 실행 요청 전에 오류로 처리한다. `parameter_expression` 스칼라는 해석한 물리 파라미터와 선언 단위로 계산한다. `adapter_scalar`는 검증된 `PredictionBundle`에서 수집한다. 곡선 배열은 스칼라 응답이 아니다.

구성식 계산이 유한한 값으로 끝났지만 불안정성이나 유효 영역 제약 위반을 보이면, 유한한 `fit_loss`와 제약 스칼라를 보존하고 기존 최적화기에 `INFEASIBLE`로 전달한다. 이를 실행 실패로 처리하면 제약 최적화를 할 수 없다. 필수 스칼라를 계산하지 못했거나 NaN 또는 Inf이면 `CONSTRAINT_RESPONSE_INVALID`로 목적함수를 무효화한다. 기존 차분진화법 `DE`가 내부 순위를 매길 때 무한대를 쓰더라도 영구 저장하는 물리 응답 JSON에는 무한대나 임의 벌점을 넣지 않는다. `SLSQP`의 초기 평가가 무효이면 중단하고 이유를 표시한다. `DE`는 허용한 실패 횟수 안에서 탐색하고 유효한 평가 기록을 보존한다.

`B-HYP-COMP-1`의 예는 `bulk_ratio`, `stability_min_acoustic_over_mu`의 하한 `1e-6`, `stability_derivative_discrepancy_over_mu`의 상한 `1e-3`, `lateral_min_log_slope_over_mu`다. 정확한 정의, 단위와 `ConstraintSpec`은 부속 명세 B와 `constraint_contract.json`을 따른다. 계산된 유한한 음의 음향 안정성 값은 유효한 제약 위반 결과다. 횡방향 평형식의 근이 여러 개이거나 접선근이거나 해결되지 않은 경우는 별도로 구성식 수치 계산이 무효인 경우다.

권장 기본 설정은 `seed=17`, `max_candidate_evaluations=1000`, `max_iterations=100`, `population=20`, `max_failures=10`, `max_wall_seconds=3600`이다. 설정 절차에서 명시적으로 덮어쓸 수 있다. 매끄럽고 미분 가능하다고 검증한 프로필만 `SLSQP`를 쓰고 나머지는 기존 `DE`를 쓴다. 항의 수, 모델과 범주형 선택은 별도 검증 단계에서 결정하며 최종 보류 시험은 사용하지 않는다. 이 수치들은 달성 성능이 아니라 버전을 관리하는 설정값이다. 내부 동작을 제어할 수 없는 콜백을 강제로 취소할 수 없다는 제약을 명시한다.

## C6. 단위, 상태와 실험 초기화

새 `materials/units.py`는 물리량의 종류와 단위를 담는 `QuantityUnitSpec(unit,quantity_kind)`와 변환 함수 `convert_values(values,source,target,policy_revision)`를 담당한다. 기존 `parse_unit`이 지원하는 곱셈형 역학 단위를 재사용한다. 비공개 단위 등록부를 실행 중에 바꾸거나 `eval`을 쓰지 않는다. 기존 `parse_unit`에 없는 `Mg`는 `1000kg`인 고정 별칭으로 명시하고 `Mg/mm**3=1e12kg/m3`로 처리한다. `mm–Mg–s` 단위계에서는 응력 `MPa→1e6Pa`, 힘 `N→1`, 에너지 `→.001J`, 밀도 `→1e12`로 변환한다.

절대온도 `absolute_temperature`는 섭씨 값을 Kelvin으로 바꿀 때 `273.15`를 더한다. 최종 절대온도는 `T>0`이어야 한다. 온도 차이 `temperature_difference`는 `K`와 `degC` 사이에 상수 보정을 하지 않으며 음수도 허용한다. 활성화 에너지 `Q`는 `J/mol` 또는 `kJ/mol×1000`이고 `R`은 `J/(mol*K)`다. 주파수 `frequency`의 `Hz`와 각주파수 `angular_frequency`의 `rad/s`는 `quantity_kind`가 다르며 명시한 `2π` 변환 절차로만 바꾼다. WLF 식의 분모와 온도 유효 범위는 프로필 검증을 통과해야 한다. 지원하지 않는 단위는 추측하지 않는다.

각 후보와 각 실험의 조합은 새 초기 상태 자료와 그 해시에서 시작한다. 같은 실험 안의 시간, 온도, 속도, 변형률과 제어 구간만 순서대로 이어간다. 후보나 실험 실행 순서를 바꿔도 결과가 같아야 한다. 명시적 상태 형식과 지원 능력이 없는 M1 단계에서는 재시작이나 사전 하중을 이용해 다른 실험의 상태를 이어받는 기능을 거부한다. 라이브러리에 남은 내부 상태가 후보 사이에 유출되지 않아야 한다. 텐서, Mandel 표기와 전단 성분의 순서, 에너지 집계는 각 라이브러리의 정확한 API를 따른다. 이름만 보고 등가소성변형률을 추정하지 않는다.

## C7. CalibrationStudyService 공용 API

화면 없이 동작하는 동기 서비스를 작업자에서 호출하며 Qt 화면 스레드를 막지 않는다. 함수의 인수와 반환값은 다음 계약으로 확정한다.

```text
create(path,*,name,model,adapter,parameters,workbench=None)->service
open(path,*,read_only=False,workbench=None)->service
snapshot()->StudySnapshot
import_experiment(source,*,channels,column_mapping,sequence,conditions,split,replicate_group,expected_revision)->ImportReceipt
configure(*,parameters,comparisons,assessment_policy,user_constraints,expected_revision)->StudySnapshot
evaluate(values,*,expected_revision,on_progress=None,cache_policy="reuse",execution_phase="exploration")->EvaluationReceipt
optimize(config,*,expected_revision,on_progress=None)->CampaignReceipt
cancel(campaign_id)->CancelReceipt
stop(campaign_id)->CancelReceipt
evaluation(evaluation_id)->CalibrationEvaluationArtifact
save()->StudySnapshot
save_as(destination,*,expected_revision,on_progress=None)->SaveAsReceipt
reconcile(*,source_policy="inspect")->ReconcileReport
relink_source(source_id,new_path,*,expected_revision)->RelinkReceipt
inspect_recovery(*,destination=None)->RecoveryReport
recover(action,transaction_id,*,expected_revision,destination=None)->RecoveryReceipt
export(evaluation_id,destination,*,expected_revision)->ExportReceipt
prepare_apply(evaluation_id,target,*,expected_revision)->ApplyPlan
commit_apply(plan,*,expected_revision)->ApplyReceipt
close()->None
```

`create`는 아직 존재하지 않는 대상만 허용한다. `configure`는 전체 설정을 새 내용으로 고정한다. 다른 작업 중의 변경과 두 번째 쓰기 소유자는 거부한다. `relink`는 파일을 선택했다는 이유만으로 원자료를 교체하지 않는다. 기존 `source_ref`의 해시 및 크기와 새 바이트가 같을 때 경로만 복구한다. 내용이 바뀌었으면 새로 가져오고 새 내용 식별값을 만든다. `reconcile(inspect)`는 현재 적용 가능성을 읽기만 하며 원데이터를 자동 변경하지 않는다.

`evaluate`의 `cache_policy`는 `reuse` 또는 `refresh`, `execution_phase`는 `exploration` 또는 `confirmation`만 허용한다. 잘못된 값은 실행 전에 `INVALID_EVALUATION_POLICY`로 거부한다. `reuse`는 같은 고정 내용·계수·실행 설정과 유효한 전체 자료 해시를 가진 성공 평가만 재사용한다. `refresh`는 과거 자료를 보존하고 새 평가 ID와 실제 계산을 만든다. native FE는 새 소유 실행, 해석식·라이브러리는 새 실제 계산을 수행하고 해당 평가의 `cache_hit=false`를 남긴다. 탐색에서는 `reuse` 또는 `refresh`를 쓸 수 있지만 보류 시험은 둘 다 제외한다. 최종 확인은 `execution_phase="confirmation",cache_policy="refresh"`를 함께 지정한다. 보류 시험을 보고 점을 다시 선택하거나 재적합하지 않는다. 최종 재계산이 실패하면 이전 성공을 삭제하지 않고 새 실패와 최종 확인 미통과를 표시한다.

`stop(campaign_id)`는 이미 배정된 후보의 정해진 필수 시험을 끝낸 뒤 새 후보를 배정하지 않는 요청이다. 후보 하나의 여러 시험 중간에서 기존 `JobManager.stop_batch`를 호출해 남은 필수 시험을 성공 처리하지 않는다. 병렬로 이미 배정한 후보들도 같은 기준으로 마친다. 이미 등록했으나 배정하지 않은 재료 평가는 `pending→stopped`, `objective.eligible=false,fit_loss=null`로 남기며 실제 시도·솔버 실행 수는 0이다. 아직 생성하지 않은 미래 후보를 만들지 않는다. 완료한 실제 결과·마지막 유효 평가·선택 상태는 보존한다. 사용자 중지로 정상 종료한 `CampaignReceipt`는 `state=stopped,termination_reason=USER_STOP`이다. 진행 중 후보가 실제 실패하면 실패 자료와 무효 손실을 보존하며 중지 성공으로 바꾸지 않는다. 실제 오류로 종료했으면 실패 사유가 우선한다. 중지 뒤 새 최적점이나 성공한 최종 재계산을 만들어 넣지 않는다.

`cancel(campaign_id)`는 소유한 작업과 native 실행에 취소를 요청한다. 요청 수락 시 `CancelReceipt(accepted=true,state=requested)`를 즉시 반환하며 프로세스 종료를 기다리지 않는다. 후속 `snapshot`·진행 알림과 최종 응답에서 종료를 확인한다. 취소가 수락되면 중지 요청보다 우선하며, 소유 프로세스·수집 작업의 종료를 확인한 뒤에만 `state=cancelled,termination_reason=USER_CANCEL`로 확정한다. 이미 끝난 작업은 `accepted=false`와 그 최종 상태를, 현재 대상이 아닌 ID는 `accepted=false,state=not_active`를 반환하고 변경은 0건이다. 취소 전에 완료된 실행을 취소 성공으로 바꾸지 않는다. `stop`의 요청 응답도 같은 `CancelReceipt`를 사용하며 요청 수락은 종료 확인과 다르다.

새 재료 평가 상태와 기존 MDO의 상태는 서비스가 구분하여 연결한다. 재료 `pending/running/completed`는 `DesignPointRecord.status`의 `PENDING/RUNNING/COMPLETED`, 재료 `failed/cancelled/timed_out/interrupted`는 기존 `FAILED`에 대응한다. 실행 전 중지한 재료 `stopped`는 기존 점 기록에서는 `PENDING`을 유지하고, 재료 평가 기록의 `stopped`와 사유를 화면·재개 판정의 기준으로 사용한다. `stopped`를 기존 MDO 상태 열거에 직접 쓰거나 이를 미실행 신규 후보로 자동 재배정하지 않는다. 기존 `OptimizationResult.termination_reason`(예: `CANCELLED`)의 원결과는 자료로 보존한다. 새 `CampaignReceipt`의 `USER_STOP/USER_CANCEL`은 서비스의 사용자 제어 사유이며 기존 최적화기 종료값을 바꾸라는 지시가 아니다.

활성 `evaluate/optimize` 동안 `import_experiment`, `configure`, `save_as`, `relink_source`, 적용 확정 등 다른 쓰기 명령은 즉시 거부한다. 계산 중 일반 `save` 요청도 중복 저장하지 않고 거부하며 현재 작업이 소유한 결과·색인 저장만 허용한다. `save_as` 등 긴 쓰기 작업 역시 짧은 잠금 아래 `busy_operation`과 고유 `active_operation_id`를 먼저 예약하고 계산용 `active_campaign_id`는 `null`로 둔다. 복사 중 다른 쓰기나 평가 시작을 거부하고, 확정 시 같은 소유 연산·기대 개정인지 확인한다. `snapshot`·기존 평가 읽기·허용된 중지/취소 요청은 계속 가능하다. 이 규칙으로 계산 작업자가 복사 후 바뀐 저장 루트에 과거 결과를 확정하는 경쟁을 막는다.

CORE02/STORE01 검사는 의도적으로 오래 대기하는 평가 중에도 `snapshot`, `stop`, `cancel`이 계산 종료를 기다리지 않는지, 활성 ID가 먼저 공개되는지, 낡은 ID가 새 작업을 제어하지 않는지 확인한다. 다중 시험 후보 중지·병렬 후보 중지·중지 중 실패·중지 후 취소·종료 직전 취소의 순서와 실제 최종 상태를 대조한다. `refresh`는 동일 점의 두 번째 실제 호출과 새 평가/실행 식별값을 확인한다. 별도 수치값만 같다고 캐시 우회를 인정하지 않는다.

작업별 응답 형식은 다음과 같다.

```text
SaveAsReceipt={transaction_id,source_path,destination_path,document_revision,content_revision,state,copied_artifact_count,warnings}; state=committed|committed_cleanup_pending
ReconcileReport={document_revision,content_revision,source_states,artifact_states,applicability,diagnostics}
RecoveryReport={transactions,interrupted_campaigns,corrupt_artifacts,allowed_actions,diagnostics}
RecoveryReceipt={transaction_id,action,state,document_revision,diagnostics}
RelinkReceipt={source_id,old_path,new_path,sha256,document_revision,content_revision}
ExportReceipt={export_id,evaluation_id,manifest_ref,artifacts,supported_solver_identity,diagnostics}
```

`ApplyPlan`과 `ApplyReceipt`는 C10에서 정한, 문서를 소유한 호스트의 적용 계획과 응답이다. 모든 실패는 `MaterialStudyError(code,stage,details)`로 표현한다. 실행 요청 전의 설정 실패와 실제 실행 시도의 실패 자료를 구별한다.

## C8. 저장, 다른 이름으로 저장, 복구와 잠금 순서

독립 연구는 `<stem>.caemat.json`과 같은 폴더의 `<stem>.caemat-data/`를 사용한다. 전체 접미사 `.caemat.json`을 제거해 `stem`을 얻는다. `Path.with_suffix`만 사용해 `.caemat.caemat-data`를 만들지 않는다. 프로젝트 안에서는 `materials/<study_id>/calibration.json`을 쓰고 `data_root="."`로 둔다. 매니페스트는 `format=caelab-material-study`, 버전 1이다. 변경하지 않는 `revisions`, `sources`, `arrays`, `evaluations`, `exports`와 기존 `RunStore` 참조를 관리한다. 복사할 때는 이들이 참조하는 모든 자료를 빠짐없이 포함한다.

쓰기 모드로 열면 서비스가 살아 있는 동안 `study.lock`의 독점 사용권을 유지한다. 이것이 잠금 소유권인 lease이며 대기 시간은 0이다. 같은 프로세스의 재진입 잠금 `RLock`은 활성 작업 등록·상태 사본·저장 직전 변경 검사 등 짧은 상태 갱신에만 사용한다. 솔버/라이브러리 계산, 큰 배열 읽기, 사용자 콜백이나 작업자 종료 대기 동안 상태 잠금을 잡고 있지 않는다. 저장은 고정 사본을 잠금 밖에서 준비하고 같은 활성 작업과 기대 변경 번호인지 다시 검사한 뒤 원자적으로 확정한다. 이미 소유한 서비스 수명용 `FileLock`을 다시 획득하지 않는다. `read_only`로 열 때는 원본 잠금 파일을 만들거나 바꾸지 않으므로 실제 읽기 전용 매체에서도 열 수 있다. `close`는 실행 중인 작업자를 취소하고 종료를 확인한 뒤 잠금을 해제한다. `close`의 종료 대기는 작업자에서 수행하고 Qt 화면 스레드를 막지 않는다. 창은 `closing` 상태에서 새 명령을 막고 늦은 콜백을 버린다. 사용 중인 창이 사라졌다는 이유만으로 종료 처리하지 않는다.

다른 이름으로 저장하는 절차는 다음과 같다.

1. 원본의 고정 상태, 문서 및 계산 내용 SHA, 참조되는 모든 자료와 필요한 실행 응답을 고정한다. 쓰기 가능한 원본은 기존 잠금 아래에서 복사한다. 읽기 전용 원본은 복사 전후 모든 해시를 검사하여 중간 변경이 없을 때만 복사를 인정한다.
2. 대상의 부모 폴더에 `.caemat-saveas-<transaction>.json`을 만들고 단계, 해당 트랜잭션이 만든 경로와 해시를 기록한다. 아직 존재하지 않는 임시 형제 폴더에 실제 바이트를 복사한다. 원본에는 복구 기록을 쓰지 않는다.
3. 고정된 작업 응답과 감사 문자열은 정확한 바이트를 보존한다. 잠금, PID, 임시 파일, 인증 정보, 실행 파일과 가상환경은 복사 대상 참조 집합이 아니다. 원본 자료의 완전성과 상대 소유 관계, 솔버 원본 및 파생 결과의 선택 상태, 필요한 모든 결과 자료를 검사한다.
4. 읽기 전용 원본은 먼저 매니페스트 상태 `M0`, 모든 참조 자료와 실행 응답의 해시를 검사한 뒤 복사한다. 복사 후 `M1==M0`인지 확인하고 모든 원본 바이트를 다시 검사한다. 바뀌었으면 `SOURCE_CHANGED_DURING_COPY`이며 성공 결과를 내지 않는다.
5. 대상 데이터의 임시 폴더를 최종 저장소 이름으로 바꾼 뒤 **대상의 `study.lock`을 획득한다.** 참조 자료 전체를 다시 검사하고 최종 매니페스트 저장을 확정한다. 그 뒤에만 서비스의 연결을 쓰기 가능한 대상으로 바꾸고 프로젝트 관계에서 분리하며 원본 잠금을 해제한다.
6. 저장 확정 전 실패하면 원본 연결과 잠금을 유지하고 해당 트랜잭션이 만든 임시 자료만 정리한다. 확정 후 정리에 실패하면 유효한 대상을 삭제하지 않고 `DESTINATION_COMMITTED_CLEANUP_PENDING`을 반환한다. 성공한 복사를 되돌렸다고 보고하지 않는다.

`study_id`를 유지하더라도 참조 해석기는 각 저장소의 독립 문맥을 사용한다. 부모에서 분리한 하위 연구를 다른 이름으로 저장해도 부모의 기존 관계나 경로를 자동 변경하지 않는다. 열린 하위 연구를 저장할 때는 하위 연구 잠금을 소유한 상태에서 `project.lock`을 기다리지 않고 획득 시도한다. 관련 문서 변경 여부를 검사한 뒤 관계를 저장한다. 실패해도 유효한 하위 연구 저장은 남기고 `ASSOCIATION_UPDATE_PENDING`을 표시한다.

부모 프로젝트를 다른 이름으로 저장할 때는 상태 고정, 정렬된 모든 하위 연구 잠금, 프로젝트 잠금, `RunStore` 및 작업 잠금 순서로 획득한 뒤 변경 여부를 검사한다. 모든 잠금의 대기 시간은 0이다. 하나라도 얻지 못하면 이미 얻은 잠금을 모두 해제하고 파일과 관계를 하나도 바꾸지 않는다. 쉬고 있는 상태라도 쓰기 가능한 하위 연구 서비스나 창이 잠금을 소유하면 `STUDY_OPEN_FOR_EDIT`로 즉시 거부한다. 숨은 잠금 양도, 역순으로 기다리는 잠금 획득이나 교착을 허용하지 않는다.

하위 연구 저장 후 부모 문서의 변경 검사에 실패하면, 하위 데이터의 `pending-association/<event_id>.json`에 변경하지 않을 보류 기록을 원자적으로 저장한 뒤 `ASSOCIATION_UPDATE_PENDING`을 반환한다. 이 기록에는 부모 정체성, 상대 연결 관계, 기대한 부모 문서 식별값, 저장 확정한 하위 문서 및 계산 내용 식별값, 역할 갱신 내용과 `created_at`을 담는다. 다시 열거나 `reconcile`을 호출하면 이 기록을 보여준다. 부모 정체성과 기대 문서 식별값이 같을 때만 기다리지 않는 잠금 획득과 변경 검사로 재시도한다. 부모가 바뀌면 충돌을 유지한다. 자동 병합하거나 하위 저장을 되돌리지 않는다. 성공하면 별도 해결 이벤트로 보류를 닫고 원 기록을 보존한다.

복구 함수 `recover`는 `mark_interrupted`, `finish_save_as`, `discard_incomplete_copy`만 허용한다. 해당 트랜잭션의 소유임을 검증한 대상만 다룬다. 과거 PID의 프로세스를 종료하거나 인계받지 않으며 솔버를 자동 재실행하지 않는다. `mark_interrupted`는 잠금이 없는 비정상 종료에서 남은 활성 상태만 `interrupted`로 기록한다. 자료가 없거나 해시가 다르면 `CORRUPT_ARTIFACT`로 판정하고 근거 검사만 허용하며 실행과 적용을 거부한다. 외부의 현재 파일이 바뀐 경우는 과거 자료의 손상과 구별하여 현재 적용 가능성이 낡았다고 표시한다.

프로젝트 저장에서 후처리 전용 파생 작업은 현행 v3과 호환되는 보조 파일로 저장한다. **`material_study` 역할을 처음 저장할 때는 v4를 쓴다.** 읽기와 다른 이름으로 저장은 v1–4를 지원한다. MDO v2는 유지한다. `material_study` 역할의 메타데이터는 `programid=caelab_material_study`, `kind=research`, `study_id`와 상대 매니페스트 경로다. 물리 입력과 출력 연결인 것처럼 표시하지 않는다. 같은 ID에 다른 루트 경로를 가진 연구를 한 프로젝트에 중복 연결하면 거부하고 명시적인 복제 정책을 요구한다.

## C9. 실제 Qt 재료 연구 창과 공통 위젯

이 절은 앞으로 구현할 화면의 동작과 검사 기준이다. `apps/workbench_qt/material_study.py`의 `MaterialStudyWindow`는 별도 도구 진입점이다. `--study <manifest>` 또는 `--project <project> --node <id>`와 `--read-only`를 지원한다. Qt 주 창에서 화면 없는 서비스 작업자를 실행하고 시그널로 진행률을 받는다. 웹서버, 브라우저나 모의 버튼으로 대신하지 않는다.

트리는 `Model`, `Experiments`, `Parameters`, `Comparisons`, `Campaigns`, `Evaluations`, `Exports`, `Reviews`로 구성한다. 메뉴는 `File` 아래의 `New`, `Open`, `Save`, `SaveAs`, `Close`와 `Study`, `Tools`, `View`를 둔다. 도킹 영역에는 설정, 표, 그래프, 로그와 상태를 표시한다. 단축키 `Ctrl+O`, `Ctrl+S`, `Ctrl+Shift+S`, 실제 중지 `Stop`, 취소 `Cancel`과 문맥 메뉴를 연결한다.

원자료를 가져올 때는 실제 파일과 최대 100행의 미리보기를 제공한다. 열 머리글 대응, 단위, 측도, 응력과 변형률 정의, 제어값과 관측값, 조건, 시편, 시험 분할과 반복 시험 묶음, 시간과 경로 분기, 변환 절차를 명시한다. 사용자가 확인하기 전에는 원자료나 근거로 저장 확정하지 않는다.

계수표에는 이름, 값, 단위, 고정 여부와 자유 또는 연계 여부, 경계값, 로그 변환, 식, 그룹과 식별 상태를 표시한다. 비교표에는 시험, 채널, 척도, 가중치, 격자, 적합 및 검증의 필수 여부와 허용 한계를 표시한다. 실제 평가와 적합 실행, 계산 예산과 취소 상태도 표시한다. 그래프에서는 관측, 실제 예측, 보류 시험과 잔차를 구별하며 대리모델을 실제 계산으로 그리지 않는다. 사용자가 점을 클릭하면 그 평가 자료를 읽고 실패한 실행, 로그와 필요한 결과의 확보 범위를 열 수 있어야 한다. 내보내기와 문서 적용은 정확한 지원 여부와 솔버 카드 연결 미리보기를 보여주고 명시적인 버튼으로 수행한다. 전문가 창은 같은 고정 평가와 문맥을 읽는다.

Prep의 숫자 편집기 `NumericEdit`, 곡선 표 `CurveTable`, 곡선 그래프 `CurvePlot`, 속성 편집기 `PropertyEditor`를 공통 `PropertyHost` API로 재사용한다. 화면의 `MaterialParameterHost`는 정수형 로컬 초안 변경 번호로 위젯 편집을 받으며 C2의 문서 SHA와 혼동하지 않는다. 위젯 편집은 초안만 바꾸고 `configure`의 확정 시점에 서비스가 저장 직전 변경 검사를 한다. Prep의 실제 원본 객체 참조 `ObjectRef`와 재료 초안은 분리한다. 원래 CSV를 자동 덮어쓰지 않는다.

화면 인수 검사는 실제 Windows 11 새 설치에서 수행한다. 한글과 공백을 포함한 경로, 한글 입력기 IME, FHD와 4K 화면, 125%, 150%, 200% 배율, 테마와 글꼴, 도킹 및 창 크기 복원, 키보드 초점, 닫힌 창의 지연 콜백, 대형 CSV 미리보기, 어댑터가 없을 때의 읽기 전용 열기를 확인한다. 실제 중지와 취소, 저장과 다시 열기, 선택한 평가 복원, 내보내기와 Prep 실행 취소까지 수행한다. 이 문서나 화면 없는 점검 코드만으로 Windows 사용자 흐름을 통과했다고 주장하지 않는다.

## C10. 열린 Prep 문서에 적용하는 경계

기존 클라이언트 `LiveClient(descriptor_path_or_dict,timeout=30).request(method,params=None,request_id=None)`로 **이미 해당 문서를 소유한 Prep 프로세스**에 연결한다. `hello`, `describe`, `cards`, `properties`에서 문서 핸들 `{document_id,document_epoch,revision}`과 현재 지원하는 자료형 정보를 읽는다. Workbench는 이 연결을 우회하려고 파일을 별도로 읽어 화면 없는 세션을 만들거나, 그 세션에서 원파일을 수정해서는 안 된다. 연결 설명에 있는 토큰과 비밀값은 LLM, 문맥, 로그와 매니페스트에 저장하지 않는다.

첫 단계에서는 LAW1 카드의 탄성계수 `E` 한 개를 연결한다. 기존 `document.edit`에서 자료형이 정해진 `SetProperties`를 통해 `set_fields`를 호출한다. 기대한 문서 세대와 변경 번호, 필드 자료형을 검사하고 한 번의 실행 취소를 보장한다. 범용 수치 최적화를 지원한다고 모든 솔버 원본 카드에 적용할 수 있다는 뜻은 아니다.

여러 카드를 한 번에 적용할 새 API는 다음과 같다.

```text
material.prepare_apply({handle,target,profile_id,profile_revision,parameters,curves,evaluation_digest})->{plan_id,handle,baseline_hashes,candidate_hashes,changes_preview,expires_at,supported,reason_codes}
material.commit_apply({plan_id,handle,candidate_hashes})->{status,handle_after,command_id,applied_hashes,reason_codes}
```

적용 준비인 `prepare`는 자료형이 정해진 자원의 후보 내용, 연결된 곡선과 의존 사용처를 모두 검사하며 문서를 바꾸지 않는다. `plan_id`는 서버가 발급한 UUID이고 60초 동안 유효하다. 대상 원문이나 전체 문서를 대체하는 명령이 아니다.

적용 확정인 `commit`은 기존 세션 보호 장치와 잠금 아래에서 같은 문서 세대, 변경 번호, 기준 해시, 후보 해시와 만료 여부를 다시 검사한다. 그 뒤 **`CommandDispatcher.execute_many(states,label,additions,removals,expected_revision)` 한 번**으로 확정한다. 한 번의 Undo와 한 번의 Redo가 각각 모든 자원과 연결을 원복하고 복구해야 한다. 지원하는 카드 연결이나 원자적 적용 준비가 없으면 `UNSUPPORTED_ATOMIC_APPLY`로 처리한다. 솔버 덱 내보내기는 해당 모델·솔버·판본의 별도 exporter가 구현·검증된 경우에만 허용한다. exporter도 없으면 계수·평가 보고서만 저장할 수 있으며 이를 솔버 카드/덱 내보내기 성공으로 표시하지 않는다.

기존 IPC 요청 캐시의 크기는 512다. 제한된 범위에서 중복을 방지할 뿐 영구적으로 정확히 한 번 실행을 보장하는 저장소가 아니다. 응답 확인이 유실되면 같은 `request_id`로만 재시도한다. 재시작, 만료나 캐시 누락 뒤에는 새 ID로 적용 확정을 자동 재시도하지 않는다. 핸들과 자료형별 해시를 다시 조회하여 적용 상태가 관측되면 `applied_state_observed`, 연결이 끊긴 뒤 상태를 알 수 없으면 `unknown_after_disconnect`를 표시한다. 사용자가 명시적으로 다시 검토하기 전에는 문서를 바꾸지 않는다. 문서가 낡았거나 지원하지 않으면 초안을 보존하고 변경은 0건이어야 한다.

## C11. 구현 작업 묶음과 착수 파일

이 절은 [기존 전체 계획의 통합 절](../../review/2026-10-23-codex-improvement-plan.md#plan-integration)을 구현 파일에 연결한다. [작업표](IMPLEMENTATION_TASKS_ko.md)와 [JSON 목록 v2](implementation-map.json)는 같은 12개 항목이다. 기존 WB P/WBS/AC와 Prep G/P/N 대응, 실제 구현자 미배정, 달력 미정, 원래 잔여와 진도 보존은 그 기존 계획을 따른다.

편집 전 합의는 인터페이스의 합의, 코드 선행은 실제 소비 코드의 준비, 최종 확인은 사용자가 작업을 끝까지 수행할 조건이다. 최종 합동 인수 조건을 모든 개발의 강제 선행으로 해석하지 않는다. UI 일반 화면은 계약으로 병렬 작성할 수 있으나 실제 계산 버튼의 인수에는 검증한 실제 어댑터가 필요하다.

| 작업 | 단일 담당 파일 또는 접점 | 편집 전 합의 | 실제 코드 연결 선행 | 최종 확인 |
|---|---|---|---|---|
| `CORE01` | `caelab/artifact_refs.py`; `caelab/materials/contracts.py`; `caelab/materials/units.py` | 공통 참조·소유·단위 | 기존 기반 | C1/C3/C6의 엄격한 직렬화·단위·소유자·누락 거절 |
| `CORE02` | `caelab/materials/registry.py`; `caelab/materials/experiments.py`; `caelab/materials/residuals.py`; `caelab/materials/evaluator.py`; `caelab/materials/service.py` | CORE01 | CORE01 | 실제 등록 어댑터 최소 1개와 독립 기준으로 공통 기능 확인; MDOStudyService의 INP·SURROGATE 전용 경로를 직접 평가기로 사용하지 않음 |
| `STORE01` | `caelab/materials/service.py`; `caelab/project.py`; `caelab/native/run_store.py` | CORE01, CORE02 | CORE02 | v1–4 읽기·이동·SaveAs·잠금·실패 복구·선택 결과 보존 |
| `MAT-A` | `caelab/materials/adapters/dp_axisym_closed.py`; `caelab/materials/adapters/geos_dp.py`; `caelab/materials/adapters/opencourant_foam33.py`; `caelab/materials/adapters/orthotropic_plane.py`; `caelab/materials/adapters/gmn12_xbp.py`; `caelab/native/geos_driver.py`; `caelab/native/adapter.py`; `caelab/connections.py` | CORE01, CORE02 | CORE02 | A 6개 프로필 각각의 계산·최적화·플랫폼 조건; native FE 분기는 실제 원결과 판독과 별도 FE 재평가 |
| `MAT-B` | `caelab/materials/adapters/ogden.py`; `caelab/materials/adapters/prony_trs.py`; `caelab/materials/adapters/neml_chaboche.py`; `caelab/materials/adapters/jc_state.py` | CORE01, CORE02 | CORE02 | B 5개 프로필 각각의 실제 계산·동정·독립 기준; NEML·플랫폼 미확인을 성공으로 바꾸지 않음 |
| `UI01` | `apps/workbench_qt/material_study.py` | CORE01, CORE02, STORE01 | CORE02, STORE01 | 실제 계산 어댑터 최소 1개로 C9의 전체 사용자 작업; 표시하는 각 프로필의 실제 지원/미지원 상태; Windows C9; 공통 창 틀의 동작 확인과 모든 모델 지원을 구별 |
| `PREP01` | `src/caelab_prep/application/material_binding.py`; `기존 Prep의 실행 중인 문서 명령 처리와 입력파일 형식 처리 코드` | CORE01, CORE02, STORE01 | CORE01, CORE02 | LAW1 E 첫 연결과 여러 시험의 동정값 적용을 별도 판정; 지원 모델의 전체 계수·곡선·축·단위→prepare/commit→Undo/Redo→저장/재열기/native 의미 보존; STORE01·UI01·LINK01과 합동 확인; Apply 미지원과 exporter 미지원을 구별 |
| `LINK01` | `기존 Workbench program_catalog 및 프로젝트 실행 코드`; `기존 Prep 공용 위젯의 공개 연결 코드` | CORE01, STORE01, PREP01 | STORE01, UI01 | 독립 진입점과 v1–4 재열기·선택 평가 보존; Apply 연결은 PREP01과 함께 확인; 전체 연결은 자문·심의·결과·RP의 같은 자료로 합동 확인 |
| `E0-2` | `기존 assist의 질문·자료·대화 관리 코드와 ExpertWindow`; `전문가 명세의 새 문헌 준비·색인·검색 판단 코드` | CORE01 | CORE01 | 이론 질문·원문/판본·관련성 평가; 이슈 #45의 검증 중 표시·첨부/초안 변경·닫힌 창 지연 응답 잔여를 동일 산출물로 승계 |
| `E3-5` | `기존 assist의 AI 연결 및 대화 실행 관리 코드`; `전문가 명세의 읽기 도구 및 답변 검토 코드` | CORE01, E0-2 | E0-2 | 실제 공급자의 도구 요청/반환과 독립 요청의 답변 심의; #45 실제 공급자 취소·지연 응답과 Windows 흐름; 모의 형식 검사·실제 도구 왕복·공학 답변 품질·Windows를 각각 확인 |
| `POST01-04` | `caelab/native/operations.py`; `caelab/native/converters.py`; `caelab/native/result_adapters.py`; `caelab/result_readers/vtk_fields.py`; `caelab/result_readers/opencourant_history.py` | CORE01 | CORE01 | 실제 변환기와 솔버 원결과·독립 수치 기준; 취소·부분/낡음·이동/SaveAs; 기존 N21 실행 완료와 구별 |
| `POST05-06` | `caelab/native/rp_consumer.py`; `선택적으로 사용할 별도 OpenCourantRP 수정판` | CORE01, POST01-04 | POST01-04 | 원 RP 수동 실제 표시와 선택 fork 자동 전달/실제 표시를 별도 인수; 원본 불변·중복 solver Run 차단·Windows; 자동 연결을 지원한다고 표시할 때만 선택 fork 인수 필수 |

기존에 지정한 다음 필수 검사도 유지한다. 위 표의 전체 연결 조건이 이 검사를 대체하지 않는다.

| 작업 | 유지하는 필수 검사 |
|---|---|
| CORE01 | 유한 수치, 알 수 없는 필드 거부, 해시와 소유자, 온도, Mg, 복소수 표현과 행 크기를 검사한다. |
| CORE02 | 새 신뢰 어댑터의 별도 등록, 고정 목적함수 집합, 평가 정책 11사례, 기존 MDO 제약 4사례, 상태 순서, 취소, 캐시와 저장 직전 변경 검사를 확인한다. |
| STORE01 | 읽기 전용 원본의 쓰기 0건, 다른 이름으로 저장의 단계별 결함 주입, 두 잠금, 문서 변경 검사, 활성 하위 연구, v1–4 복구와 선택 결과의 전체 참조 복사를 확인한다. |
| MAT-A | A의 정확한 6개 프로필, 한계, 독립 수치 기준, 실제 LAW33 원자료 전달, Linux의 GEOS와 Windows의 해석식 계산을 확인한다. |
| MAT-B | B의 서로 다른 두 Ogden 모델, Prony 실제 API, NEML 라이브러리 사용 조건, JC 지정 상태, 제약과 유한 체적탄성계수 K에서의 모든 근을 확인한다. |
| UI01 | 실제 CSV에서 적합, 보류 시험, 저장과 다시 열기로 이어지는 흐름과 C9의 Windows 화면을 확인한다. |
| PREP01 | 문서 소유 프로세스 IPC, 낡은 문서 거부, 연결 곡선의 원자적 적용, Undo와 Redo, 응답 유실과 내보내기만 허용하는 경우를 확인한다. |
| LINK01 | 독립 프로그램 진입점, 도킹, 역할 연결과 프로젝트 v1–4의 다른 이름으로 저장을 확인한다. |
| E0/E1/E2 | C의 정확한 형식, 초안과 첨부 문맥, 원문 8개와 범위 16개, 개발 문항 36개와 평가 문항 60개, 언어, 부정과 미지원 처리를 확인한다. |
| E3/E4/E5 | C의 모의 도구 왕복과 별도의 실제 공급자 검증, 답변 전체 주장에 대한 검토, 재심, 실제로 구별되는 요청과 토큰, 실패를 확인한다. |
| POST01–04 | D의 실제 변환기 인수, 솔버 원본 ID, TIME과 TH 연결, 수치, 취소, 낡은 결과, 캐시와 다른 이름으로 저장을 확인한다. |
| POST05–06 | D의 정확한 후처리 전용 전달과 응답, 첫 화면 표시, Run과 Stop 보호, Windows 실행 파일을 확인한다. |

조건부 선행은 JSON의 `conditional_dependencies`를 따른다. MAT-A의 native FE는 해당 POST 판독기, E3-5의 native_run은 해당 결과 판독기, calibration은 CORE02의 고정 평가가 필요하다. 이론·문헌 질문은 POST가 필요 없다. UI01은 A/B 실제 프로필 1개 이상으로 연결을 인수하고 11개 프로필은 각각 별도로 판정한다. UI 인수 모델과 Prep Apply 인수 모델은 다를 수 있다. supports_apply=false인 UI 모델을 적용 가능으로 승격하지 않는다. LINK01의 Prep 적용 부분만 PREP01 코드가 필요하다.

PREP01은 CORE01/02·STORE01의 평가·내용 식별·저장·적용 결과 계약부터 합의한다. LAW1 E 첫 IPC 확인은 STORE01 전체 완료를 기다리지 않는다. 최종 동정값 적용은 STORE01/UI01/PREP01/LINK01 및 실제 카드 연결을 함께 확인한다. 계수·평가 보고서 저장, 솔버 native 덱 내보내기, 열린 문서 Apply의 지원 여부는 각각 판정한다.

공유 파일은 지정한 한 명이 편집한다. 특히 CORE02/STORE01의 `materials/service.py`, 프로젝트/RunStore 잠금, E0-2/E3-5/#45의 요청 수명과 `ExpertWindow`, Prep의 live 분배기·코덱을 중복 구현하지 않는다. 각 작업은 코드·관련 검사·현재 상태를 같은 PR에 담고 작성자와 다른 QA가 같은 커밋을 확인한다. 코드 구현, 실제 환경 인수, 모델별 지원과 참조 시험은 서로 다른 판정이다.

## C12. 모델군 확장과 검증 범위

GEOS의 `geos_dp_axisym_linear_v1`은 FE 메시가 없는 구성식 TriaxialDriver이므로 기존 `CapabilityReport.level=material_library`로 분류한다. 기존 JobManager/RunStore의 프로세스 소유는 재사용하되 출력을 재료 시험의 ArtifactRef/PredictionBundle로 전달하고 FE field ResultRef를 만들지 않는다. `native_fe`는 실제 요소·시험편 계산에만 사용한다.

A의 정확한 프로필은 6개다. `GEOS_DP_axisym`과 해석식 대응 모델, OpenCourant LAW33의 `Ka0` 및 `Ka1`, 직교이방 평면응력 탄성, `GMN2012-derived power XBP`가 해당한다. B의 정확한 프로필은 5개다. 비압축성 Ogden N2, 압축성 Ogden N1, Prony TRS, 배응력 항 두 개를 가진 NEML Chaboche, 지정 상태의 JC가 해당한다. 두 묶음의 모델군 중복 여부와 자체 계산, 솔버 계산, 라이브러리 계산 방식의 차이는 의도한 것이다. 원래 Xia2002 모델과 파생 XBP를 동일시하지 않는다. Ogden의 무한 K와 유한 K를 섞지 않으며, JC 유동 곡선의 지원을 유한요소 손상이나 요소 삭제 지원으로 확대하지 않는다.

새 모델을 추가할 때는 정확한 출처, 라이선스와 식, 상태, 파라미터, 측도와 단위, 하중, 유효 영역과 제약을 갖춰야 한다. 실제 계산기의 API와 카드, 출력 추출기, 독립 수치 기준, 실험에서 계수를 식별하는 방법, 내보내기와 문서 적용, 플랫폼별 인수까지 필요하다. 이름만 등록해서 `supported=true`를 부여하지 않는다. 다른 비선형 모델도 이 계약에 맞는 어댑터로 추가하되 개별 실행과 물리 검증을 수행한다.

기존 소스나 라이브러리가 실제로 있으면 재사용하고 독립 검증 기준과 입력 및 출력 연결을 개발한다. 모든 구성식에 대해 자체 적분기를 처음부터 다시 만들지 않는다. 솔버 원본 덱 실행은 별도 경로로 계속 허용한다. 해석식이나 라이브러리를 사용한 최적화와 실제 솔버의 유한요소 시험편 최적화는 둘 다 허용되는 계산 방식이며, 각 방식에서 실제로 확인한 능력만 주장한다.

과거 수행한 범위는 Linux LAW33의 `Ka0` 및 `Ka1`과 상한을 둔 시간 간격 세분화, 별도 솔버 원본 LAW1 N0 및 LAW2 N5와 변환 판독기, 실제 pyvisco와 기존 최적화기를 사용한 참조 적합, 독립적인 평가 정책과 제약 및 유한 K 근과 층 검사, 원문 문헌 집합과 엄격한 모의 호출이다.

아직 수행하지 않았거나 구현하지 않은 범위는 제품 서비스와 창, 실제 AI 공급자와의 연결, Prep의 새 다중 카드 적용, RP 포크, Windows 확인, GEOS 원본 실행, NEML 원본 빌드 성공, 일반 XBP 계산 핵심부와 실제 재료 실험의 타당성 검증이다. 실행 환경 소실로 회수하지 못한 원본 바이트는 해시 및 과거 관측 기록과 구별한다. 새로 재구성한 코드를 그 자리에서 다시 실행했다고 주장하지 않는다.
