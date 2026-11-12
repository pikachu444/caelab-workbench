# 통합 서비스·데스크톱·저장·Prep 적용 구현 명세

개정 R4-recovered, 2026-11-12. 실행환경 단절 뒤 실제 작성·토론 문맥에서 새로 작성한 명세다. 회수하지 못한 로컬 커밋/원본 기록과 동일한 bytes라고 주장하지 않는다. 제품 구현 완료 보고가 아니다. 이 문서는 기존 통합 계약의 공통 DTO를 확장·정정하며 충돌하는 옛 문구보다 우선한다. A/B/C/D 부속 명세는 각 분야의 정확한 수식·소스·상태·출력·인수 조건을 소유한다.

## C0. 목표, 프로그램 소유와 완료 조건

DP/Xia는 예시다. 여러 재료의 물성 정의·다시험 동정·최적화·보류시험 평가·정직한 native export/Apply, 일반 CAE 이론과 실제 모델/해석 결과 자문, 독립 검토와 외부 프로그램 후처리를 제공한다. 목록 등록은 계산 지원이 아니다. J2/선형 모델만으로 범용 연구 기능 완료를 판정하지 않는다.

Workbench는 독립 프로그램을 연결하며 시험/평가·최적화·근거·대화의 수명을 소유한다. Prep은 실제 native 문서·카드·GUI 편집·Undo·export를, solver는 구성식/FE 계산을, RP/ParaView는 표시를 소유한다. 공통 FE 모델 DB, 새 queue/JobManager, 범용 앱 내부 전처리기, 웹 목업, 유료 API/플러그인/서버를 도입하지 않는다. Windows 로컬 PySide6==6.9.1 Core/Gui/Widgets/UiTools와 Matplotlib, 기존 FreeCAD/Prep/RP/ParaView를 쓴다.

최종 사용 시나리오: 실제 CSV 선택→열/단위/측도/조건·시험 분할 확인→모델과 고정/자유/연계 계수·범위 설정→실제 평가/최적화→시험별 원자료·예측·잔차·holdout·실패 확인→저장/재열기→지원 카드 export 또는 열린 Prep에 명시 Apply→동일 불변 근거로 전문가 질의·별도 검토→선택 결과를 독립 후처리기로 연다. 각 동작은 실제 파일/프로세스/수치와 연결돼야 한다.

## C1. 공용 계약의 단일 소유와 소스 기준

기준 WB104cdd6888a5554eecc8cf965c60ff1bd980b6da, Prep555af5b3ab2c6e7dc276111609302ef565bb4fc8, RPcec23cbdd4058237e04fdcc7ecb06a3834578cae. 정확한 신규 계약은 구현할 API이며 현존 API와 구별한다.

- 신규 caelab/artifact_refs.py: ArtifactRefV1, ResultRefV1, DatasetManifestV1, NumericResultV1, EvidenceEnvelopeV1의 frozen DTO/strict JSON. Qt/VTK/solver import 없음.
- 기존 mdo/variables.py VariableSpace/codec, mdo/optimize.py OptimizationProblem/EvaluationRequest/EvaluationObservation/optimize, study_manager.py StudyManager, objectives/constraints/doe/sensitivity를 소비한다.
- 신규 materials/service.py CalibrationStudyService는 해당 모듈과 기존 JobManager/RunStore/atomic_json/FileLock을 조합한다. CalculiX/INP에 묶인 MDOStudyService._submit나 surrogate 전용 흐름을 범용 direct evaluator로 호출하지 않는다. MDO 저장 v2는 이행하지 않는다.
- materials/contracts.py/experiments.py/residuals.py/evaluator.py/registry.py/units.py와 각 adapter가 물리/시험 영역을 소유한다. 모델명으로 generic service를 분기하지 않는다. JSON에서 module/shell/임의 Python을 실행하지 않는다.
- 자료 owner scope는 run|operation|consumer만 허용하며 host resolver가 등록 store context를 소유한다. 같은 study_id라도 SaveAs 전후 root가 다르므로 전역 ID→경로 cache를 쓰지 않는다.
- 원본 bytes·변환 recipe·수치 content hash·실제 파일 SHA·당시 감사 경로·현재 적용 가능성을 분리한다. 상대 POSIX 경로, containment/drive/UNC/../NUL/symlink/reparse 검증, SHA/size/등록 context 검증은 모든 진입점에서 동일하다.
- frozen evidence 해시는 전체 validated envelope에서 자기 content_sha256만 제외한 canonical JSON의 SHA256이다. UI/live stale/현재 문서 bytes를 불변 source 해시에 섞지 않는다.


공용 EvidenceEnvelopeV1 wire는 {schema_version:1,id,kind,content_sha256,provenance,availability,payload}로 고정한다. id는 host발급 UUID,kind enum은 native_run|manual|calibration|document_chunk다. "result"는 별도 kind가 아니며 native 유래 결과는 native_run payload.derived_result_ref로 표현한다.

provenance={producer:{id,version},sources:[{source_kind,source_id,source_revision,url}],artifact_owners:{artifact_id:{scope,id,source_run_id?}}}. source_kind enum=native_run|manual|calibration|official_source|paper|solver_manual,source_revision/url은 실제알수없으면null이며추정값을넣지않는다. consumer/evidence/corpus는등록root에서resolve한다. availability={state:available|partial|source_unavailable|corrupt|unsupported,reason_codes:[],missing_artifact_ids:[]}. unavailable자료도knownhash/URL/identity를보존하며실물source가있는것처럼제공하지않는다.

payload는 kind별 strict union:
- native_run: {legacy_evidence_ref:ArtifactRefV1,source_run_id,source_node_id,derived_result_ref:ResultRefV1|null}. derived source_run_id와 payload가일치해야한다. legacy_evidence_ref는현재capture가만든기존evidence descriptor의frozenJSONbytes이며원metadata/text/files와그상대경로를보존한다.
- manual: {legacy_evidence_ref:ArtifactRefV1}. 기존수동evidence metadata/text/files descriptor의원bytes를참조한다. native결과를새로발명하지않는다.
- calibration: {evaluation_ref:ArtifactRefV1,study_id,content_revision,evaluation_id}. frozen CalibrationEvaluationArtifact를읽어실제시험/채널/잔차/split/constraint를전달한다.
- document_chunk: {corpus_id,corpus_revision,source_id,source_ref:ArtifactRefV1,chunk_id,chunk_ref:ArtifactRefV1,document_sha256,text_sha256,edition,span:{line_start,line_end,char_start,char_end},page_start,page_end,section_path:[]}. page값은null허용; source-line기준은1-basedinclusive,characteroffset은0-basedhalf-open Unicode codepoint다. chunk_ref는전달할exactUTF8textbytes,source_ref는실제원문이며둘의hash/span일치가필수다.

모든공용/unionrecord는additionalProperties=false,finite JSON,필수키(허용null포함)검증을사용한다. 기존discussion evidence는원metadata/text/files를자동rewrite/migrate하지않는다. readadapter가원descriptor exactbytes를consumer artifact로고정하거나기존frozen descriptorref를사용하고wrapper를메모리/새근거snapshot으로제공한다. 기존kind/source_kind가manual/native_run/calibration이면위union에명시mapping하고unknown은unsupported로보존한다. 원evidence.files stored_path는소유assetsroot기준그대로이며허용evidence subtree/context와files SHA를재검증한다. 새로운단일envelope hash는모든provenance/owners/availability/payload를포함하고content_sha256만뺀다. legacy descriptor원hash와newwrapper hash는서로다른identity다.

## C2. 문서 revision과 계산 revision

document_revision은 현재 manifest 실제 bytes의 SHA256이며 자기 자신을 manifest에 재귀 저장하지 않는다. expected_revision 인수는 항상 이 값을 뜻한다. 쓰기 직전 디스크 bytes와 대조하는 CAS다.

content_revision은 모델·계수·고정 시험/변환/비교/assessment policy·수치 설정·optimizer 설정·source identity의 canonical 해시다. 선택점/창 위치/현재 진행률/평가 index/live 절대경로는 제외한다. MaterialEvaluationRequest.study_revision과 OptimizationProblem.revision은 content_revision이다. 계산 중 자기 평가 index를 저장했다고 그 계산의 revision을 바꾸지 않는다. 구성 변경은 불변 새 content revision을 만든다.

StudySnapshot={study_id,document_revision,content_revision,path,data_root,read_only,busy_operation,active_campaign_id,experiment_ids,point_ids,diagnostics}. 모호한 revision 필드는 제거한다.
ImportReceipt={study_id,previous_document_revision,document_revision,previous_content_revision,content_revision,experiment_id,source_ref,converted_refs,warnings}.
EvaluationReceipt={campaign_id,evaluation_id,document_revision,content_revision,state,artifact_ref,observation}.
CampaignReceipt={campaign_id,content_revision,state,termination_reason,evaluation_ids,selected_ids,budget_usage}.
CancelReceipt={campaign_id,accepted,state}; requested는 worker/native 종료의 확인이 아니다.

worker는 시작 content snapshot과 자기 operation_id를 고정하고 자기 소유 최신 document_revision으로 index 저장 CAS를 갱신한다. UI callback token=(window generation,operation_id,content_revision); 닫힘/다른 study/이전 요청 결과는 표시하지 않되 이미 생성한 receipt는 보존한다. disk 외부 변경은 EXTERNAL_MANIFEST_CHANGED로 새 dispatch 중단·소유 job 취소 요청·결과 별도 보존하며 타인의 manifest를 덮어쓰지 않는다. 완료 UI는 낡은 시작 document snapshot을 덮어쓰지 않고 최신 snapshot을 읽는다.

## C3. PredictionBundleV1와 결과 왕복

MaterialAdapter.collect(plan,artifacts)->PredictionBundle는 다음 PredictionBundleV1의 별칭이다. 프로세스 exit0/파일 생성/화면 표시는 계산 수집 성공의 대용물이 아니다.

PredictionBundleV1={schema_version:1,evaluation_id,request_id,study_revision,model_revision,adapter:AdapterIdentity,experiments:[ExperimentPredictionV1],optimizer_scalars:{registered_response_id:finite_number|null},artifacts:[ArtifactRefV1],artifact_owners:{artifact_id:{scope,id,source_run_id?}},run_ids:[],diagnostics:[]}.
ExperimentPredictionV1={schema_version:1,experiment_id,experiment_revision,state,initial_state_ref,channels:[PredictedChannelV1],execution_refs:[],diagnostics:[]}.
PredictedChannelV1={schema_version:1,observable_id,observable:ObservableSpec,independent_ref,sample_keys_ref,sample_key_columns:[],values_ref,mask_ref,component_order:[],value_kind,representation,coverage,source_refs:[],source_result_refs:[]}.

independent_ref는 실제 time/frequency/load N행, values/mask는 N×C, sample_keys는 N×K다. 행/절점/요소/IP/layer/segment/branch identity를 sample_key_columns로 선언한다. ordinal을 물리시간으로 만들지 않는다. 선언한 observable contract와 representation/shape/unit/frame/measure/shear/sign/location/coverage가 정확히 일치해야 한다. material-point/library는 source_result_refs=[]이며 FE ResultRef를 발명하지 않는다. source_refs에는 실제 배열/라이브러리/상태/실행 provenance를 둔다. 무효값 저장은 canonical finite0+mask=false로 명시하고 그0을 유효 응답으로 쓰지 않는다. 원 NaN 포함 native bytes는 별도 raw artifact로 보존한다. fit_loss는 host만 계산하며 adapter optimizer_scalars에 넣을 수 없다.

DatasetManifestV1.dataset_kind=field|history, frames:[],series:[]를 필수화한다. field는 실제 frames와 빈 series, history는 빈 frames와 series[{series_id,coordinate_ref:ArtifactRefV1,row_count,source_row_index_ref:ArtifactRefV1,field_ids:[]}]. history ResultRefV1.frame_id는 등록된 series_id(예 history:all)이며 물리적인 all frame은 없다. NumericResult.sample_coordinates에는 실제 time[]를 반환한다. history.geometry_state=unknown, 최초 operator=curve_extract만 지원한다. row snapshot/min/max 등 series reduction은 별도 미지원이다. RP field consumer는 history를 거부하고 curve consumer는 series를 받는다. ResultRef의 기존 top-level 필드를 복제/분기하지 않는다.

Source ResultRef를 operator 요청에 쓸 때 base ref는 불변 유지하고 operator만 바꾼 request ref와 둘의 해시를 trace에 남긴다. unknown field→U, layer→membrane, tensor shear→engineering, stress→force 등의 추정 fallback을 금지한다.

## C4. 비교·목적함수·평가 정책의 하나의 판정

ComparisonSpec에 id:str,required_for_fit:bool,required_for_validation:bool를 추가한다. required_for_fit=true는 train에서만, required_for_validation=true는 validation/holdout에서만 허용하며 두 값 동시 true는 오류다. 둘 다 false는 diagnostic이다. 최적화에는 fit 필수 비교가 적어도1개 있어야 한다. 평가만이면 diagnostic-only 가능하다. required_for_fit 비교만 고정 목적함수에 참여한다. 후보마다 비교/샘플/가중치 분모를 바꾸거나 실패 시험을 빼지 않는다.

잔차는 frozen observed grid에서 동일 물리량·측도·축·위치·성분·단위로 linear interpolation, bracket 필수/외삽 금지다. 반복 strain의 cyclic path는 실제 time/branch를 유지한다. predeclared mask만 사용한다. r=(pred-obs)/scale, channel_loss=sum(q*r²), q>=0,sumq=1. 실험/채널 가중치는 fit 비교 집합에 대해 한 번 정규화한다. scale은 양의 고정 engineering/training-only recipe, holdout/후보로 재산정하지 않는다. 진단 실패는 목적함수 집합/분모에 영향을 주지 않는다.

AssessmentResultV1 단일 직렬화:
objective={eligible,fit_loss:number|null,reason_codes:[]}.
assessment={fit_status,validation_status,holdout_status,adequacy_status,uncertainty_status,identifiability_status,constraint_status,constraint_checks:[],policy_ref,channel_checks:[],split_checks:[],warnings:[]}.
필드별 enum: fit_status/validation_status/holdout_status/adequacy_status는 pass|fail|not_assessed; uncertainty_status는 pass|fail|not_computed; identifiability_status는 pass|fail|not_computed|not_identifiable; constraint_status는 기존 FEASIBLE|MARGINAL|INFEASIBLE|NOT_ASSESSED다. 각 보고서의 not_computed/not_identifiable를 필수 adequacy 요구에 매핑할 때만 아래 pass/fail/not_assessed 판정 규칙을 쓴다. train_fit 같은 별도 저장 alias를 만들지 않는다. assessment policy는 immutable artifact로 required channel acceptance_limit, split aggregation, adequacy/identifiability/uncertainty 요구, physical/numerical/reference scope를 고정한다.

판정 순서:
1 필수 train 실행/변환/전체 coverage/identity/측도/수치가 유효하지 않으면 objective.eligible=false,fit_loss=null. failed/cancelled/required-partial을 큰float penalty로 쓰지 않는다.
2 유효하지만 오차가 크면 finite loss를 유지하고 fit_status=fail. optimizer는 개선할 수 있다.
3 channel acceptance_limit=None은 not_assessed, 필수 비교0개인 split도 not_assessed. 필수 항목 중 fail 우선, 그 뒤 missing/notrun은 not_assessed, 모두 assessed pass일 때만 pass.
4 validation/holdout 실패나 미실행이 유효 train loss를 삭제하지 않는다. holdout은 optimizer/scaling/모델 선택에 쓰지 않는다.
5 adequacy policy 없음은 not_assessed. 필수 identifiability 결과 not_identifiable는 fail, not_computed는 not_assessed. 계산 종료/low fit loss/전문가 말 한마디를 실재료 타당성 증명으로 바꾸지 않는다.
6 전문가 pass/revise/insufficient는 정확히 같은 불변 context일 때 해당 요구의 pass/fail/not_assessed에만 반영한다. 바뀐 모델/데이터에 과거 review를 재사용하지 않는다.
7 numerical_fixture 검증은 그 수치 reference 범위의 통과다. 독립 실험이 없는 상태에서 material validated라는 문구를 만들지 않는다.

## C5. optimizer scalar와 물리적 제약

CapabilityReport 확장: optimizer_responses:tuple[ScalarResponseSpecV1,...],optimizer_constraints:tuple[ConstraintSpec,...],differentiable:bool.
ScalarResponseSpecV1={schema_version:1,response_id,unit,source_kind:residual|parameter_expression|adapter_scalar,source_name}.
모든 response 선언에 schema_version=1이 필수다. source_name은 설치/등록된 함수 또는 제한식 ID이며 JSON executable code가 아니다. fit_loss는 host-owned dimensionless scalar이며 unit의 직렬화 문자열은 기존 단위 계약대로 빈 문자열("")이다(물리적으로1). per-test curves/losses는 artifact에 보존한다. optimizer responses에는 fit_loss와 사전 등록된 제약/진단 scalar만 넣고 동적 미등록 response ID를 만들지 않는다.

profile mandatory ConstraintSpec와 사용자 constraints를 immutable content snapshot에 합쳐 기존 OptimizationProblem.constraints 및 response_units에 전달한다. 사용자가 mandatory constraint를 제거/완화할 수 없다. ID 중복/미등록 response/단위·bound 불일치는 dispatch 전 오류다. parameter_expression scalar는 해석된 physical parameters+declared units에서 계산하고 adapter_scalar는 검증한 PredictionBundle에서 수집한다. curve array는 responses가 아니다.

유한한 구성식 계산이 instability 또는 유효 영역 제약 위반을 보이면 finite fit_loss와 constraint scalar를 보존하고 기존 optimizer에 INFEASIBLE로 전달한다. 그 자체를 실행 실패로 처리하면 제약 최적화가 불가능하다. 필수 scalar를 계산하지 못했거나 NaN/Inf이면 CONSTRAINT_RESPONSE_INVALID로 objective invalid다. 기존 DE가 내부 순위용 inf를 사용해도 영구 물리 response JSON에는 inf/임의penalty를 저장하지 않는다. 초기 SLSQP invalid evaluation은 중단·이유표시; DE는 실패 budget 내 탐색하고 valid archive를 보존한다.

B-HYP-COMP-1 예: bulk_ratio,stability_min_acoustic_over_mu(lower1e-6),stability_derivative_discrepancy_over_mu(upper1e-3),lateral_min_log_slope_over_mu의 정확한 정의/단위/ConstraintSpec은 B부속과 constraint_contract.json을 따른다. 계산된 finite negative acoustic 값은 유효한 infeasible 결과다. lateral equilibrium 다중/접선/미해결 root는 별도 constitutive numerical invalid다.

기본 추천은 seed17,max_candidate_evaluations1000,max_iterations100,population20,max_failures10,max_wall_seconds3600이며 recipe가 이를 명시 override한다. 검증된 smooth/differentiable profile만 SLSQP, 나머지는 기존 DE. term count/모델/categorical 선택은 별도 validation 단계이고 최종 holdout을 손대지 않는다. 모든값은 성능달성치가 아닌 버전 있는 설정이다. opaque callback의 강제취소 불가능은 선언한다.

## C6. 단위·상태·실험 초기화

신규 materials/units.py가 QuantityUnitSpec(unit,quantity_kind),convert_values(values,source,target,policy_revision)를 소유한다. parse_unit의 지원 multiplicative mechanical 단위를 재사용하며 private registry monkeypatch/eval은 금지한다. 기존 parse_unit에 없는 Mg는 명시 고정 alias1000kg, Mg/mm**3=1e12kg/m3로 처리한다. mm–Mg–s에서 stressMPa→1e6Pa,forceN→1,energy→.001J,density→1e12다.

absolute_temperature는 Kelvin/Celsius(+273.15),최종T>0. temperature_difference는 K/degC 차이에 offset없고 음수가능하다. Q는 J/mol 또는 kJ/mol×1000,R은 J/(mol*K). frequency Hz와 angular_frequency rad/s는 quantity_kind가 다르며 명시2π recipe로만 변환한다. WLF denominator/temperature validity는 profile 검증을 통과해야 한다. 미지원단위는 추측하지 않는다.

각 candidate×experiment는 fresh initial state의 artifact/hash에서 시작한다. 같은 실험의 시간/온도/rate/strain/control segments만 순서대로 이어간다. 후보/실험 실행 순서를 바꿔도 결과가 같아야 한다. restart/preload 다른 실험 continuation은 explicit state schema/capability가 없는 M1에서는 거부한다. library warm internal state가 후보 사이에 누출되지 않는다. tensor/Mandel/shear 순서와 energy bookkeeping은 각 exact library API를 따르며 이름만으로 등가소성변형률을 추정하지 않는다.

## C7. CalibrationStudyService 공용 API

headless 동기 service를 worker에서 호출하며 Qt thread는 block하지 않는다. 기존 signature는 다음 계약으로 확정한다.

create(path,*,name,model,adapter,parameters,workbench=None)->service
open(path,*,read_only=False,workbench=None)->service
snapshot()->StudySnapshot
import_experiment(source,*,channels,column_mapping,sequence,conditions,split,replicate_group,expected_revision)->ImportReceipt
configure(*,parameters,comparisons,assessment_policy,user_constraints,expected_revision)->StudySnapshot
evaluate(values,*,expected_revision,on_progress=None)->EvaluationReceipt
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

create는 미존재 target만 허용, configure는 전체 configuration의 불변 새 revision을 만든다. busy mutation/두번째 writer는 거부한다. stop은 추가 scheduling만 중지, cancel은 소유 실행 취소도 요청한다. completed-before-cancel은 취소성공으로 바꾸지 않는다. relink는 파일선택만으로 원자료를 교체하지 않고 old source_ref의 hash/size와 새bytes가 동일할 때 경로만 회복한다. 내용변경은 새 import/새revision이다. reconcile(inspect)는 live applicability를 읽으며 원데이터를 자동변경하지 않는다.

SaveAsReceipt={transaction_id,source_path,destination_path,document_revision,content_revision,state,copied_artifact_count,warnings}; state=committed|committed_cleanup_pending.
ReconcileReport={document_revision,content_revision,source_states,artifact_states,applicability,diagnostics}.
RecoveryReport={transactions,interrupted_campaigns,corrupt_artifacts,allowed_actions,diagnostics}.
RecoveryReceipt={transaction_id,action,state,document_revision,diagnostics}.
RelinkReceipt={source_id,old_path,new_path,sha256,document_revision,content_revision}.
ExportReceipt={export_id,evaluation_id,manifest_ref,artifacts,supported_solver_identity,diagnostics}.
ApplyPlan/Receipt는 C10의 host-owned 계획/응답이다. 모든 failure는 MaterialStudyError(code,stage,details)이며 before-dispatch 설정실패와 실제 attempt실패 artifact를 구별한다.

## C8. 저장·SaveAs·복구와 lease 순서

standalone <stem>.caemat.json과 sibling <stem>.caemat-data/를 사용한다. 전체 .caemat.json suffix를 제거해서 stem을 얻는다. Path.with_suffix만으로 .caemat.caemat-data를 만들지 않는다. project 내부는 materials/<study_id>/calibration.json,data_root="."다. manifest format=caelab-material-study,version1. 불변 revisions/sources/arrays/evaluations/exports와 기존 RunStore references를 복사 폐쇄집합으로 관리한다.

writable open은 service lifetime study.lock timeout0 lease를 소유하고 각 연산은 같은 process RLock을 쓴다. 자기 lifetime FileLock을 다시 acquire하지 않는다. read_only open은 source lock파일을 생성/수정하지 않아 실제 읽기전용 media에서도 열린다. close는 active worker를 취소/종료확인한 뒤 lease를 해제하며 사용중 창만 사라졌다고 완료 처리하지 않는다.

SaveAs:
1 source snapshot/document/content SHA·참조폐쇄·required run receipts를 고정한다. writable source는 기존 lease 아래, readonly source는 모든 hash를 before/after 검사하는 낙관copy를 쓴다.
2 destination parent의 .caemat-saveas-<transaction>.json에 단계/tx-owned paths/hash를 기록하고 미존재 sibling staging에 실제 bytes copy한다. source에 recovery journal을 쓰지 않는다.
3 frozen receipt/audit 문자열은 정확bytes를 보존한다. lock/PID/temp/credentials/executable/venv는 폐쇄집합이 아니다. source artifact 완전성/relative owner와 native/derived selection·결과 closure를 검증한다.
4 readonly는 M0 source manifest 및 closure/runreceipt hashes를 검사한 뒤 copy하고 M1==M0 및 모든 원bytes재검증을 요구한다. 바뀌면 SOURCE_CHANGED_DURING_COPY, 성공 출력 없음.
5 destination data staging→최종datastore rename 후 **destination study.lock 획득**, 다시closure검증, 최종manifest commit. 그 뒤에만 service를 destination writable로 전환하고 project relation에서 분리하며 source lease를 해제한다.
6 commit 전 실패는 source binding/lease를 유지하고 tx-owned staging만 정리한다. commit 후 cleanup실패는 유효destination을 삭제하지 않고 DESTINATION_COMMITTED_CLEANUP_PENDING으로 반환한다. 성공 copy를 rollback이라고 보고하지 않는다.

동일 study_id를 유지해도 resolver는 독립 store context다. detached child SaveAs가 parent의 기존 관계/경로를 자동수정하지 않는다. 열린 child save는 child lease 상태에서 project.lock을 nonblocking try하고 관련 revisionCAS한다. 실패해도 유효childsave는 남기고 ASSOCIATION_UPDATE_PENDING을 표시한다. parent project SaveAs는 snapshot→정렬된 모든 child lease→project lease→RunStore/operation leases 순서로 얻고 CAS한다. 모든 lease는 timeout0이며 하나라도 얻지 못하면 이미 얻은 lease를 모두 해제하고 파일/관계 변경0이다. idle 상태라도 writable child service/window가 lease를 소유하면 STUDY_OPEN_FOR_EDIT로 즉시거부한다. 숨은 lock양도/역순blocking/deadlock은 없다. childsave 후 parentCAS 실패는 child data의 pending-association/<event_id>.json에 불변 pending record(parent identity/relative association,expected parent document revision,committed child document/content revisions,role update payload,created_at)를 원자 저장한 뒤 ASSOCIATION_UPDATE_PENDING으로 반환한다. reopen/reconcile은 이 record를 보여주고 동일 parent identity/expected revision일 때만 nonblocking lease+CAS로 재시도한다. parent가 바뀌면 충돌을 유지하고 자동병합/childrollback하지 않으며 성공 후 별도 resolution event로 닫고 원 record를 보존한다.

recover의 허용 action은 mark_interrupted|finish_save_as|discard_incomplete_copy. tx-owned 검증destination만 건드리며 과거PID를 kill/adopt하거나 solver를 자동재실행하지 않는다. mark_interrupted는 unlockedcrash의 active 상태만 interrupted로 남긴다. artifact missing/hashmismatch=CORRUPT_ARTIFACT로 evidence검사전용·실행/적용거부. 외부 현재파일 변경은 historicalcorruption이 아니며 applicability stale로 구분한다.

프로젝트 저장: post-only derived operation은 현 v3 compatible sidecar다. **material_study role 최초 저장은 v4**, reader와 SaveAs는 v1–4를 지원한다. MDO v2는 그대로다. material_study role은 programid=caelab_material_study,kind=research,study_id/relative manifest metadata이며 물리 input/output 연결을 위장하지 않는다. 같은ID/다른root를 한project에 중복연결하면 거부하고 명시복제정책을 요구한다.

## C9. 실제 Qt Material Study 창과 공용 위젯

apps/workbench_qt/material_study.py MaterialStudyWindow는 별도 tool entry다. --study <manifest> 또는 --project <project> --node <id>,--read-only를 지원한다. Qt main창에서 headless serviceworker를 실행하고 signal로 progress를 받는다. 웹서버/브라우저/모의버튼은 없다.

트리 Model/Experiments/Parameters/Comparisons/Campaigns/Evaluations/Exports/Reviews, 메뉴 File(New/Open/Save/SaveAs/Close)/Study/Tools/View, dock설정/표/plot/log/status를 둔다. Ctrl+O/S/Shift+S,실제 Stop/Cancel과 context action을 연결한다. 원자료import는 실제file,최대100행preview,headermapping·단위·측도·stress/strain정의·control/observed·조건·시편·split/replicate·시간/분기와변환recipe를 명시한다. 확인전 source/evidence로 commit하지 않는다.

계수표는 이름/값/단위/고정·자유·연계/bounds/log/식/그룹·식별상태, comparison표는 test/channel/scale/weights/grid/필수fit·검증/limit를 보인다. 실제 evaluate/fit과 budget/cancel 상태를 표시한다. plot은 관측·실제예측·holdout/residual을 구분하고 surrogate를 실제로 그리지 않는다. 클릭한점의평가artifact를 읽고 실패run/log/coverage를 연다. export/Apply는 정확 지원여부·nativebinding preview와 explicit버튼을 쓴다. 전문가창은 같은immutable evaluation/context를 읽는다.

Prep NumericEdit/CurveTable/CurvePlot/PropertyEditor를 공용 PropertyHost API로 재사용한다. GUI MaterialParameterHost는 local integer draft revision을 써서 위젯편집을 수용하며 C2 document SHA와 섞지 않는다. 위젯 변경은 draft만 바꾸고 configure Commit에서service CAS한다. Native PrepObjectRef와materialdraft를 분리한다. 원 CSV를 자동overwrite하지 않는다.

UI 인수: 실제 Windows11 새설치·한글공백경로·IME·FHD/4K와125/150/200% scaling·테마/폰트·dock/창크기복원·keyboardfocus·닫힌창latecallback·대형CSVpreview·missingadapterread-only·실제StopCancel·저장재열기/선택평가·export·PrepUndo까지 수행한다. 이 문서와 headlessprobe가 Windows사용자flow를 통과했다는 뜻이 아니다.

## C10. 열린 Prep 문서에 적용할 정확한 경계

existing LiveClient(descriptor_path_or_dict,timeout=30).request(method,params=None,request_id=None)를 사용해 **이미 문서를 소유한 Prep 프로세스**에 연결한다. hello/describe/cards/properties에서 {document_id,document_epoch,revision} handle과 current typed support를 읽는다. Workbench가 파일을 따로 읽어 headlesssession을 만들고 열린 문서를 수정하지 않는다. descriptor token/secret은 LLM/context/log/manifest에 저장하지 않는다.

scalar LAW1 E 첫binding은 기존 document.edit typed SetProperties→set_fields를 호출하며 expectedepoch/revision과 typedfield검증·한번Undo를 지킨다. generic 수치최적화 지원이 모든nativecardApply를 의미하지 않는다.

새multi-card API:
material.prepare_apply({handle,target,profile_id,profile_revision,parameters,curves,evaluation_digest})->{plan_id,handle,baseline_hashes,candidate_hashes,changes_preview,expires_at,supported,reason_codes}.
plan_id는serverUUID,유효60초이며 rawtarget/전체문서대체명령이 아니다. prepare는 typedresourcecandidate/linkedcurves/dependentuses를 전부검증하고 mutation0.
material.commit_apply({plan_id,handle,candidate_hashes})->{status,handle_after,command_id,applied_hashes,reason_codes}.
같은epoch/revision/baseline/candidate와expiry를 기존session guard/lock아래재검사 후 **CommandDispatcher.execute_many(states,label,additions,removals,expected_revision)** 한번으로 commit한다. Undo/Redo각한번이모든resource/link를원복/복구해야 한다. 지원binding/atomicprepare가없으면UNSUPPORTED_ATOMIC_APPLY/export-only.

기존 IPC requestcache512는bounded중복방지이며 영구 exactly-once저장이아니다. acklost retry는 동일request_id만사용한다. restart/expiry/cachemiss 뒤 새ID로 commit자동재시도금지. handle+typedhash를재조회해서 applied_state_observed 또는 unknown_after_disconnect를 표시하고 사용자명시재검토전 mutation하지 않는다. stale/unsupported는 draft보존,변경0.

## C11. 구현 작업 묶음과 바로 착수할 파일

| 묶음 | 단일 소유 파일/접점 | 선행 | 완료를 판단할 실제 검사 |
|---|---|---|---|
| CORE01 | artifact_refs.py/result resolver,materials/contracts.py/units.py | 없음 | strictfinite/unknownfields/hash/owner/온도·Mg·complexrepresentation·행shape |
| CORE02 | materials/{experiments,residuals,evaluator,registry,service}.py | CORE01 | 새trusted별도adapter등록,고정목적집합,11assessment정책case,4기존MDO제약case,state순서·취소·cache·CAS |
| STORE01 | service persistence/project reader/RunStore.copy_to | CORE01/02 | readonlysource0write,SaveAs결함주입각단계/두lease/CAS/activechild/복구v1–4/selectionclosure |
| MAT-A | adapters/{dp_axisym,geos_dp,foam33,ortho_plane_stress,xbp_power}.py | CORE02+필요nativePOST | A의6exactprofile·한계/수치oracle/실제LAW33rawtransfer·GEOSLinux와analyticWindows |
| MAT-B | adapters/{ogden,pyvisco,neml_chaboche,jc_state}.py | CORE02 | B의두distinctOgden/PronyrealAPI/NEMLlibrarygate/JCprescribedstate·constraints·finiteK모든root |
| UI01 | apps/workbench_qt/material_study.py/host | CORE02/STORE01 | 실제CSV→fit→holdout→저장재열기·WindowsGUI C9 |
| PREP01 | Prep application/material_binding.py/live dispatcher/native codec | CORE01/02 | ownerIPCstale/linkedcurveatomic/UndoRedo/acklost/export-only |
| LINK01 | WB program_catalog/project4/tool launcher+Prep shared GUIwidgets | STORE01/UI01/PREP01 | independentprogramentry,도킹/role연결/프로젝트SaveAsv1–4 |
| E0/E1/E2 | assist context/discussion/corpus/index/querypolicy | CORE01 | C의정확schema·draft/attach문맥·8원문16span·dev36/eval60/언어·부정·미지원 |
| E3/E4/E5 | assist readtools/providerloop/reviewer+ExpertWindow | E0–2+POSTreader | C의mock왕복과별도실provider검증·全answercoverage·재심·실제서로다른요청/토큰·실패 |
| POST01–04 | native/{operations,converters,result_adapters},result_readers | CORE01 | D의진짜converterargv/nativeID/TIME/THbinding·수치·cancel/stale/cache/SaveAs |
| POST05–06 | rp_consumer+optional독립RPfork | POST01–04 | D의정확postonlyhandoff/receipt·first-render·RunStopguard·Windowsexe |

공용 파일은 지정소유자가 합치고각분야는extension함수만추가한다. 서로다른코어DTO/하위호환별도안/미정CLI를각자만들지않는다. 각작업은 구현파일/테스트/문서currentstate를같은PR에놓고 독립QA가동일commit을검사한다. 제품완료와referencepass를분리한다.

## C12. 모형군 확장과 검증 범위

A exact profiles6: GEOS_DP_axisym와analyticpeer,OpenCourantLAW33Ka0/Ka1,직교plane-stresselastic,GMN2012-derived powerXBP. B exactprofiles5: incompressibleOgdenN2,compressibleOgdenN1,PronyTRS,NEML2backstressChaboche,JCprescribedstate. 둘의모델family중복/자체/solver/library계산방식은의도적으로다르다. 원Xia2002와derivedXBP를동일시하지않는다. Ogden의무한K와유한K를섞거나JC흐름곡선에서FE손상/deletion지원으로확대하지않는다.

신규모델onboarding은exactsource/license/equations·상태·파라미터·측도/단위·하중·domain/제약·실제backendAPI/카드·출력extractor·독립oracle·실험식별·export/Apply·platformacceptance까지갖춰야한다. 모델이름등록만으로supported=true를부여하지않는다. 다른nonlinear모델도이계약에맞는adapter로추가하되개별실행/물리검증을수행한다.

기존source/library가실제로있으면재사용하고독립oracle/입력출력binding을개발한다. 모든구성식의자체적분기부터다시만들지않는다. 원native덱실행은별도통로로계속허용한다. analytic/library최적화와nativeFE쿠폰최적화는동등한backend종류이며각자의실제능력만주장한다.

과거실시: LinuxLAW33Ka0/Ka1·cappedtimesteprefinement,별도nativeLAW1N0/LAW2N5와converterreader,실pyvisco+기존optimizer참조fit,독립정책/제약/유한Kroot/층검사,sourcecorpus+strictmock. 미실시/미구현: 제품서비스/창/원provider/Prep새multiApply/RPfork/Windows,GEOSnative·NEMLnative빌드성공·일반XBPkernel과실재료실험타당성. 실행환경소실로회수불가한rawbytes는해시/과거관측기록과구분한다. 새로재구성한코드를그자리에서다시실행했다고주장하지않는다.
