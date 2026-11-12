# B. 유한변형·시간/온도 이력·순환소성·속도 의존 유동과 파손 개시의 구현 명세

이 문서는 **앞으로 구현할 재료 연구 기능의 계획 명세**다. 식과 API를 정의한 것, 과거 환경에서 실제 참조 계산을 실행한 것, 제품에 연결해 사용자가 끝까지 수행할 수 있는 것은 구분한다. 아래에는 실제 산술·pyvisco·기존 최적화기 참조 실행 기록이 있지만, 새 재료 서비스·화면·네이티브 보정·Windows 인수 검증은 완료하지 않았다. NEML 실제 텐서 라이브러리 실행도 설치 실패로 하지 못했다.

복구판은 B-RECOVERY-1, 2026-11-12다. 작업 환경을 잃은 뒤 저자가 직접 재구성했으며, 이전 part SHA256 `f3f1ce4d1d96fa60738268aa0d2dc685232a2e4a2ca6b849d48930f32924c399`와 동일한 파일이라고 주장하지 않는다. 원식·계약·기록된 수치 실행은 보존하지만 원래 CSV·원시 결과·실행 기록(receipt)의 바이트를 추측 복원하지 않는다. 새 실제 blob을 E/F가 재검토하기 전에는 최종 승인으로 표시하지 않는다.

## B0. 사용자가 얻게 될 결과와 공통 구현 계약

### 연구 범위와 파일 소유

사용자 U1–U9의 전체 목표는 Prep의 공용 CAE 자산, Workbench의 독립 프로그램·재료 연구·전문가, 검색 증강 생성(RAG)·도구·검토, 후처리 연결의 실제 상태를 확인하고 구현 가능한 인계를 완성하는 것이다. DP/Xia는 예시다. J2 단조곡선이나 재료명 등록부만으로 전체 목표를 대신하지 않는다. B는 물리·자료·계산 계약이 서로 다른 정확한 해석식/라이브러리 프로필을 정의한다. 원래 네이티브 덱의 실행은 계수 동정과 별도로 사용할 수 있는 기존 실행 경로이므로 보정 기능을 거부하더라도 차단하지 않는다.

사용자는 CAD/FE 메시 없이도 시험 원자료로 연구를 시작할 수 있어야 한다. Workbench는 실험 원자료·시험·변수·잔차·최적화·결과 출처를 소유한다. Prep은 솔버 고유 형식(네이티브)의 원본과 자료형을 검증한 문서 적용(Apply)과 실행 취소(Undo)를 소유한다. 현재 Prep의 LAW1/LAW36 기본 필드 편집을 아래 법칙 전체에 대한 자료형을 검증한 문서 적용으로 간주하지 않는다. 이 프로필들의 설정은 `supports_apply=false`다.

내보내기는 정확한 정식화·단위·파라미터·소스·시험 범위·한계를 담은 parameter JSON의 MaterialExportPlan이다. 네이티브 카드 내보내기는 지원하지 않는다. 이를 지원하려면 정확한 입력파일을 읽고 쓰는 형식 변환 코드(codec)·요소·출력·추출기·실제 실행을 별도로 검증해야 한다.

신규 예정 어댑터 파일은 caelab/materials/adapters/ogden.py, prony_trs.py, neml_chaboche.py, jc_state.py다. 공통 MaterialAdapter.capabilities/validate/plan/collect/export를 구현한다. 해석식/라이브러리 계산은 등록된 신뢰 콜백을 통해 `EvaluationPlan.kind=in_process`로 실행한다. JSON에 적힌 임의 Python 코드나 모듈 경로를 실행하지 않는다. 등록부와 설치된 entry point로 확장하고, 서비스에 모델명 if/elif를 넣지 않는다. 기존 MDO optimizer/VariableSpace/StudyManager/JobManager/RunStore/atomic_json/FileLock을 재사용한다. 두 번째 FE 스키마·실행 큐·DB·유료 API를 만들지 않는다.

### 자료형, 단위와 개정 관리

공통 ArtifactRefV1, MaterialStudy v1, ComparisonSpec, PredictionBundleV1, CalibrationEvaluationArtifact, AssessmentResultV1, ResultRefV1을 사용한다. 같은 뜻의 필드 이름을 따로 만들지 않는다.

아래 C1/C2/C6/C7/C8은 앞서 합의한 결정 ID다. 현재 재구성 공통 core `b3ec806dd6d5e52c5062c75fc98b1e829b2c08ac`의 절 번호는 비교·판정 C4, 단위 C6, scalar C5, revision C2, PredictionBundle C3다. 실제 전달 형식과 필드별 enum은 현재 core를 정본으로 따른다.

- **C1 — 비교의 목적:** ComparisonSpec.id/required_for_fit/required_for_validation은 필수다. fit=true는 train에만, validation=true는 validation/holdout에만 허용한다. 둘 다 false이면 진단 비교다. train 목적함수의 선택 채널 목록·분모·가중치를 고정하고 후보 실패나 자료 누락 때문에 시험을 빼지 않는다.
- **C2 — 단위 변환:** materials/units.py의 QuantityUnitSpec(unit,quantity_kind), convert_values가 단일 변환 경로다. 역학 단위는 기존 parse_unit에 위임한다. absolute_temperature는 K 또는 degC+273.15이며 변환 후 T>0이어야 한다. temperature_difference는 K/degC의 간격이 같고 음수·0도 허용한다. 활성화에너지는 J/mol 또는 kJ/mol×1000, 기체상수 R은 J/(mol*K)다. WLF 분모에는 온도차, Arrhenius 분모에는 절대온도 K를 사용한다.
- **C6 — 최적화 응답과 제약:** CapabilityReport.optimizer_responses/optimizer_constraints/differentiable을 등록한다. ScalarResponseSpecV1에는 schema_version=1,response_id,unit,source_kind,source_name을 저장한다. source_kind는 residual/parameter_expression/adapter_scalar다. 프로필의 필수 제약은 사용자가 임의 삭제할 수 없으며 response_units/constraints/content_revision에 고정한다.
- **C7 — 내용 개정과 화면 상태:** 변경 가능한 expected_revision은 document_revision이고 MaterialEvaluationRequest.study_revision/OptimizationProblem.revision은 변경 불가능한 content_revision이다. 모델·시험·비교·수치·제약·최적화 계약은 content에 포함한다. 진행률·색인·선택 상태를 저장해도 연구 내용은 바뀌지 않는다. 작업자는 token+content_revision을 대조하며 과거 산출물·캐시는 당시 content에 남긴다.
- **C8 — 계산 결과 전달:** PredictionBundle은 현재 core C3의 PredictionBundleV1 별칭이다. experiments[].channels[]에는 observable, independent_ref, sample_keys_ref, sample_key_columns, values_ref, mask_ref, component_order, value_kind, representation, coverage, source_refs, source_result_refs를 그대로 채운다. independent shape(N), values/mask(N,C), sample_keys(N,K), component_order 길이 C다. artifact_owners는 scope=consumer,id=material-study:<id>다. 재료점/라이브러리 계산은 run_ids=[],source_result_refs=[]이며 FE 결과로 꾸미지 않는다. optimizer_scalars에는 등록된 parameter_expression/adapter_scalar만 넣고 fit_loss는 넣지 않는다. 호스트 잔차 계산이 fit_loss를 만들어 기존 EvaluationObservation.responses로 전달한다.

### 원자료, 이력과 평가 실패 처리

CSV는 변경 불가능한 UTF-8 RFC4180 형식이며 헤더와 유한한 셀 값을 가져야 한다. 원본 바이트·해시, column_mapping, 단위·측도·기준(basis)·부호·제어/관측 구분·시험ID·split·replicate_group·변환 절차를 보존한다. `source_kind=synthetic_arithmetic`인 검증 자료는 물리 실험이 아니다. `sample_keys=[source_row]`는 원 CSV 행의 정수이며, 실제 시간·주파수·신장비를 순번으로 대체하지 않는다. 초탄성(HYP)·순환소성(CYC)·Johnson–Cook(JC) 채널과 점탄성(VIS)의 storage/loss 채널은 각각 real/scalar,C=1이고 component_order를 명시한다. VIS의 두 채널은 같은 위상 변환 절차와 실제 주파수 격자를 공유한다. 필수 mask/coverage를 검사하며 source_refs에는 원 CSV·단위/변환·계산 절차와 소스/라이브러리 출처를 넣는다.

한 시험의 예하중·반전·온도 경로에서는 상태를 유지한다. 매 후보×독립 시험은 새 초기 상태로 시작한다. 별도 시험 간 restart/continuation은 UNSUPPORTED_STATE_CONTINUATION이다. 라이브러리 호출을 즉시 중단할 수 없는 한계는 기능 보고에 표시하며, 취소 뒤 반환한 값을 성공 결과로 저장하지 않는다. 캐시에는 어댑터 구현·설정·모델, 파라미터·단위, 모든 원자료·변환 절차·수치 설정·비교·소스/라이브러리 식별 해시가 들어간다.

정본 잔차는 `r=(pred−obs)/고정 scale`, 손실은 `L=sum(q*r²)`다. train 목록과 정규화 가중치를 고정해 합산한다. 관측 격자 안에서만 선형 보간하며 외삽·후보별 겹침 범위 축소는 금지한다. 순환 경로에서 같은 변형률이 반복되면 시간 또는 명시한 분기로 비교하고 전체 자료를 변형률로 정렬하지 않는다. 계산이 유효하되 오차가 크면 유한한 fit_loss와 fit 불합격으로 남긴다. 필수 train의 실행·측도·무결성·범위 또는 필수 제약 스칼라가 실패하면 objective.eligible=false,fit_loss=null이다. holdout 실패가 유효한 train 목적함수를 지우지는 않는다. 판정 결과의 정본은 fit_status/validation_status/holdout_status/adequacy_status/uncertainty_status/identifiability_status/constraint_status 등이며 train_fit 별칭을 중복 직렬화하지 않는다.

공통 오류와 details.reason의 구체적 사유를 구분한다. 공통 오류는 INVALID_PARAMETER, UNSUPPORTED_DOMAIN/OBSERVABLE/UNIT/ADAPTER_VERSION, MEASURE_MISMATCH, COVERAGE_INCOMPLETE, NONFINITE_RESPONSE, INTEGRATION_FAILED, BACKEND_UNAVAILABLE, CONSTRAINT_RESPONSE_INVALID, CANCELLED다. 정상적인 큰 오차나 물리 제약 위반을 가짜 벌점 또는 실행 실패로 바꾸지 않는다.

구조적으로 식별할 수 없는 경우에도 유한한 평가를 보존하고 not_identifiable, 영공간, 자유변수 목록을 보고한다. Jacobian/특이값 분해(SVD) 계산 자체가 실패했거나 자료가 부족하면 identifiability_status=not_computed다. 행렬의 계수(rank)는 서로 독립인 정보의 수다. 수치 계산 불능을 구조적인 계수 부족으로 바꾸지 않으며, 필수 적합성 정책에서만 not_assessed로 연결한다.

## B1. 확인한 소스와 라이선스 경계

| ID | 저장소/고정 개정 | 실제로 읽은 경로·API | 재사용 및 검증 범위 |
|---|---|---|---|
| B-SRC-HYP | adtzlr/hyperelastic 58b036acc389585110f5d74e0457c36fae0cc4f2 | src/hyperelastic/models/stretches/_ogden.py Ogden.gradient/hessian | GPL-3.0 소스와 비교했다. 커널을 복사하지 않고 아래 독립적인 정확 해석식을 사용한다. |
| B-SRC-VIS/WLF | NatLabRockies/pyvisco b61b5a0c6a2939927c87189972ea5619503bd813 | pyvisco/prony.py E_relax_norm/E_freq_norm, pyvisco/shift.py WLF | BSD-3-Clause 고지를 보존한다. 실제 로드된 2.1.3 파일이 소스 SHA256과 같았다. |
| B-SRC-CYC | Argonne-National-Laboratory/neml 65ab2a648a975e2e9de8e1371be270567f9683ac | src/hardening.cxx Chaboche::h/q; neml/drivers.py; src/models.cxx/models_wrap.cxx; test/test_hardening.py; setup.py | MIT와 pybind11/RapidXML 고지가 필요하다. 컴파일하는 선택 백엔드다. |
| B-SRC-JCF/JCD | OpenCourant/OpenCourant d791761d9636abaa27e41ebbe271fa9df519287e | engine/source/materials/mat/mat002/m2cplr.F, engine/source/materials/fail/johnson_cook/fail_johnson.F | AGPL-3.0-or-later 소스의 의미를 근거로 삼는다. 커널 복사나 네이티브 바이너리 동등성을 주장하지 않는다. |

정확한 URL 형식은 https://github.com/<repository>/blob/<revision>/<path>다. B의 OpenCourant 소스와 D의 cb760c 실행 파일·소스를 자동으로 동일시하지 않는다. 소스 개정만으로 배포 바이너리의 대응 관계를 증명할 수 없다. 소스/API를 바꾸면 새 어댑터 버전·소스 diff·검증 자료 재검사가 필요하다.

## B2. Ogden 초탄성: 비압축성과 압축성은 별도 정식화

### B-HYP-INC-1 — 정확한 비압축성 Ogden 식

이 프로필은 비압축성(INC) Ogden N=2이며 2μ/α² 규약을 사용한다.

W=Σj 2μj/αj²*(λ1^αj+λ2^αj+λ3^αj−3), J=1.

μj의 단위는 MPa, αj는 무차원, W는 기준체적당 변형에너지 밀도이며 단위는 MPa다.

βi=Σj (2μj/αj) λi^αj; Cauchy σi=βi−p; nominal PK1 Pi=σi/λi.

p는 Lagrange 승수이며 두께 방향이 무응력이면 β3다. 초기 전단계수는 μ0=Σμj다. PK1은 제1 Piola–Kirchhoff 응력, 즉 이 표의 공칭응력이다.

| 시험 | 신장비·경계조건 | P1 |
|---|---|---|
| 단축 | (λ,λ^−1/2,λ^−1/2), σ2=σ3=0 | Σ2μ/α*(λ^(α−1)−λ^(−α/2−1)) |
| 등이축 | (λ,λ,λ^−2), σ3=0 | Σ2μ/α*(λ^(α−1)−λ^(−2α−1)) |
| 평면 인장 | (λ,1,λ^−1), σ3=0, 횡반력 허용 | Σ2μ/α*(λ^(α−1)−λ^(−α−1)) |

참조 파라미터는 μ=(.8,.2) MPa, α=(1.3,5)다. μj∈[.01,10]은 로그, α1∈[1.2,3]과 α2∈[3.2,6]은 선형 좌표를 쓰며 순서 간격≥.2를 지킨다. train은 전체 단축·이축 λ=.75..2를 .05 간격으로 사용하고, 평면 인장 전체를 holdout으로 둔다. INC에는 K 파라미터가 없다. nomstress=force/reference area와 λ=1+u/L0로 변환할 때 측정 형상 정보를 기록한다. 실제 측정자료를 모델의 비압축성 가정만으로 Cauchy 응력으로 바꾸지 않는다.

### B-HYP-COMP-1 — 정확한 압축성 Ogden 식

압축성(COMP) N=1은 다음 별도 정식화다.

barλ=J^(−1/3) λ;
W(F)=Σj2μj/αj²*(Σi barλi^αj−3)+K/2*(J−1)².
βi=Σj(2μj/αj)barλi^αj;
σi=(βi−meanβ)/J+K(J−1); P=JσF^−T.

변형 기울기 F를 SVD로 분해해 주값을 계산한 뒤 요청 좌표계로 텐서를 회전해 반환한다. 중복 고유값에서는 연속인 등방 한계를 사용하며 0인 고유값 차이로 나누지 않는다. 첫 지원 대상은 응력 API이며 일반 FE 접선 API가 아니다.

참조 파라미터는 μ1=1 MPa, α1=2, K=100 MPa다. μ[.1,10]은 로그, α[1.2,6]은 선형, K∈[10,1000]은 로그 좌표를 사용한다. train은 유한 K의 단축 시험과 정수압 J={.97,.99,1,1.01,1.03}이고, validation은 이축 λ=.85..1.85를 .05 간격으로 사용한다. 정수압 F=J^(1/3) I에서 평균 Cauchy 응력은 K(J−1)이다. 압축 양수의 압력 관측량으로 비교하려면 부호 반전을 명시한다. INC 힘 곡선과 COMP 정수압 자료를 하나의 유한 K 모델 자료로 섞지 않는다. J=1 자료만 있으면 K 민감도가 0이므로 K를 고정하거나 비식별로 보고한다. 목적함수를 꾸며내지 않는다.

### COMP의 횡방향 평형근을 찾는 정확한 절차

횡방향 무응력을 만족하는 신장비 z를 계산해야 한다. 단축은 λ1=λ,λ2=λ3=z, 이축은 λ1=λ2=λ,λ3=z, 평면 인장은 λ1=λ,λ2=1,λ3=z다. `R(z)=J*σ_thickness(z)=Σck z^pk`로 쓰며 cj=2μj/αj다. 아래 각 쌍은 (계수,지수)다.

| 모드 | j번째 등체적 변형 항 | 체적 항 |
|---|---|---|
| 단축(uniax) | (cj/3 λ^(−α/3),α/3), (−cj/3 λ^(2α/3),−2α/3) | (Kλ²,4),(−Kλ,2) |
| 이축(biax) | (2cj/3 λ^(−2α/3),2α/3), (−2cj/3 λ^(α/3),−α/3) | (Kλ⁴,2),(−Kλ²,1) |
| 평면 인장(planar) | (2cj/3 λ^(−α/3),2α/3), (−cj/3[λ^(2α/3)+λ^(−α/3)],−α/3) | (Kλ²,2),(−Kλ,1) |

지원 z 영역은 [1e−4,1e4]이며 암묵적으로 넓히지 않는다. log10z=−4+i/16의 129개 절점·128개 셀을 전부 검사한다. 각 부호 있는 단항식의 양 끝 최소·최대와 그 합으로 R/R′의 구간 경계를 만든다. `roundoff guard=64*eps_float64*max(1,sum_abs_endpoint_terms)`를 구한 뒤 하한에서는 빼고 상한에는 더해 구간을 바깥쪽으로 넓힌다.

R 구간에 0이 없으면 그 셀에는 근이 없다. R′ 구간에 0이 없으면 수치적으로 단조인 셀이므로 끝점의 0 또는 부호 변화에만 근이 있다. 부호 변화는 Brent의 xtol=rtol=1e−14, 최대 반복 100회으로 푼다. 둘 다 확정할 수 없으면 기하평균 중점으로 최대 20단계 이분한다. 끝까지 미확정이거나 접하는 근이면 INTEGRATION_FAILED/LATERAL_ROOT_UNCERTIFIED로 거부하며 다른 근을 골라 성공시키지 않는다.

검출한 근을 정렬하고 |ln(z1/z2)|≤1e−9이면 |R|이 작은 근으로 합친다. 전체 영역에서 근이 정확히 하나이고 |R/J|≤1e−9 MPa여야 한다. 0개이면 LATERAL_EQUILIBRIUM_NOT_BRACKETED, 1개보다 많으면 AMBIGUOUS_EQUILIBRIUM이다. 이전 근·가까운 근·안정한 근을 몰래 선택하지 않는다. 이 유한 영역 수치 검사는 실수 연산에 대한 형식 증명이나 전역 유일성 증명은 아니다. 유일근에서 무차원 `lateral_log_slope_over_mu=zR′/(Jμ0)`를 구한다. 계산이 유효한 음의 기울기는 유한한 제약 위반이며, 근의 범위 확인이나 수치 계산을 못 한 경우는 무효 평가다.

이 절차는 이전 R3 실행에서 모드 3개×λ{.75,1,2}×μ{.1,1,10}×α{1.2,2,6}×K{10,100,1000}=243개 경우의 residual≤1e−9와 후보 순서를 뒤집었을 때의 정확한 동일성을 통과했다. 단항식의 무근·영역 밖 근·두 근·접하는 근을 확정하지 못한 경우(tangent-uncertified) 반례도 각각 예상 오류를 냈다. 복구한 코드 자체는 환경을 사용할 수 없어 아직 재실행하지 않았다.

### HYP의 필수 스칼라 제약과 안정성 검사

물리 파라미터 검사는 μj>0, αj≥1.2, K>0이다. COMP의 bulk_ratio=K/Σμ에 대한 [10,1000]은 실행 오류가 아니라 필수 수치 최적화 제약이다. K가 양수인 비율 위반 후보도 유한한 손실을 유지한다. HYP 두 프로필 모두 differentiable=false이며 기존 차분진화(DE; seed=17,population=15,max_evaluations=3000,max_iterations=200)를 사용한다. 대리모델은 없다. 최소 고유값이나 분기 거부를 매끄러운 순차 최소제곱 계획법(SLSQP) 문제로 가장하지 않는다.

안정성을 확인할 영역은 미리 고정한다. 모든 시험의 고정 F 표본, 각 모드의 λ={.75,1,1.25,1.5,1.75,2}, COMP 정수압 J={.97,.99,1,1.01,1.03}, F=I+γe1⊗e2의 γ={−.5,−.25,0,.25,.5}를 사용하고 각각 I/Rz90/Rx90를 왼쪽에 곱한다. INC는 J=1의 닫힌식 신장비, COMP는 유일한 횡방향 근을 사용한다. n은 {−1,0,1}³\{0}을 정규화한 26방향이다.

A=d²W/dF²는 h=1e−5와 h/2의 중앙차분으로 구한다. 대각항은 ±h 2차 차분, 비대각항은 혼합 4점/4h²다. Qij=AiAjB nA nB이며 INC에서는 m·F^−Tn=0을 만족하는 정규직교 2차원 접선 기저로 투영한다. 이 고정 영역 밖의 전역 타원성은 주장하지 않는다.

모든 ScalarResponseSpecV1에는 schema_version=1과 무차원 unit=""가 필요하다.

| 응답/출처 | ConstraintSpec |
|---|---|
| bulk_ratio, parameter_expression K/mu1(COMP N1) | 하한(LOWER) 10, scale=10, tol=0 및 상한(UPPER) 1000, scale=1000, tol=0 |
| stability_min_acoustic_over_mu, adapter_scalar ogden.stability.min_acoustic_over_mu.v1=min eig(Q_h/2)/μ0 | 하한(LOWER) 1e−6, scale=1, tol=0 |
| stability_derivative_discrepancy_over_mu, adapter_scalar ogden.stability.max_derivative_discrepancy_over_mu.v1=max\|\|Q_h−Q_h/2\|\|2/μ0 | 상한(UPPER) 1e−3, scale=1e−3, tol=0 |
| lateral_min_log_slope_over_mu, adapter_scalar ogden.lateral.min_log_slope_over_mu.v1=고정 제어 경로의 근 전체에서 얻은 최솟값 | 하한(LOWER) 1e−6, scale=1, tol=0 |

INC의 필수 제약은 bulk/lateral을 제외한 음향 텐서 관련 두 개뿐이다. 등호는 허용한다. 유효하게 계산한 음의 음향값·횡기울기 또는 미분 차이 한계 위반은 유한한 fit_loss와 INFEASIBLE로 기록하고 진단 STABILITY_ENVELOPE_FAILED를 남긴다. 필수 스칼라의 누락·비유한값·계산 실패는 CONSTRAINT_RESPONSE_INVALID,objective.eligible=false,fit_loss=null,constraint_status=NOT_ASSESSED다. 최소 고유값 배열은 산출물에만 저장한다.

검증 입력의 응력 스케일은 2 MPa이며 λ/J에 대한 정규화 사다리꼴 가중치와 시험별 동일 가중치를 사용한다. 스케일한 잔차 Jacobian의 특이값 비 기준 1e−6/조건수>1e6 경고를 사용한다. 항 수가 다른 모델은 별도 모델 개정으로 만들고 train/validation으로 선택한 뒤 손대지 않은 holdout으로만 판단한다.

독립 기준값은 μ1=1 MPa, α1=2, λ=2에서 INC 공칭 P1=1.75(단축),1.96875(이축),1.875(평면 인장) MPa, 단축 Cauchy=3.5 MPa다. COMP K=100 MPa/J=1.03의 정수압 σ=(3,3,3) MPa다. 산술 허용오차는 1e−11 MPa, 객관성 σ(RF)=Rσ(F)Rᵀ의 허용오차는 1e−9, 소스 기울기/Hessian의 유한차분 허용오차는 절대오차 1e−6+상대오차 1e−6이다. 이 값은 실제 재료 보정의 통과 한계가 아니다.

## B3. B-VIS-TRS-1: 일반화 Maxwell 점탄성과 온도 이동

### 선형 점탄성식과 주파수 관측량

지원할 계산은 스칼라 소변형 선형 점탄성이다. 이를 유한변형 고무 점탄성으로 확대하지 않는다.

Ei=E0 ai, E∞=E0(1−Σai);
E(t)=E∞+ΣEi exp(−t/τi).

E0는 순간 탄성계수이며 단위는 MPa다. τi의 단위는 초이고 ai는 무차원이다. 새 무변형 상태는 q_i=0이다. 이상적인 변형률 점프는 정확히 qi+=EiΔε로 처리하고 실제 유한 램프에는 실제 지속시간을 사용한다. 원시 응력 완화 자료를 탄성계수로 바꿀 때는 부과한 작은 변형률·정규화·불확도 절차를 기록한다.

ε=Re(ε0 exp(iωt)),σ=Re(E*ε0 exp(iωt)),E*=E′+iE″.
E′=E∞+ΣEi(ωτi)²/[1+(ωτi)²],
E″=ΣEi ωτi/[1+(ωτi)²].

E′는 저장탄성계수(storage), E″는 손실탄성계수(loss)이며 양의 E″는 응력이 변형률보다 위상이 앞섬을 뜻한다. δ=atan2(E″,E′)이고 진폭·위상 자료는 E′=|E*|cosδ,E″=|E*|sinδ로 바꾼다. 각도를 degree에서 rad로 바꿀 때는 π/180을 쓴다. exp(−iωt) 규약 자료에는 허수부 부호 변환 절차가 필수다. ω=2π f_Hz는 정확히 한 번만 적용한다.

실제 pyvisco.prony.E_relax_norm(time,ai,taui)*E0와 E_freq_norm(omega,ai,taui)*E0를 사용한다. 후자의 반환값은 [all storage,all loss]를 이어 붙인 배열이므로 표본 수로 정확히 분리한다. pyvisco.fit_* 내부 최적화기는 쓰지 않고 기존 Workbench 최적화기가 보정을 담당한다. 과거 실제 2.1.3에서 로드된 prony.py/shift.py 바이트는 고정 소스 해시와 같았고 API 기준 검사도 통과했다.

### 시간–온도 중첩과 경로 적분

열유변학적 단순성(TRS)은 수평 이동만 사용한다.

τi(T)=aT τi(ref), ξ(t)=∫0^t ds/aT(T(s));
E(t,Tconst)=Eref(t/aT),E*(ω,T)=Eref*(aTω).

탄성계수의 수직 이동은 없다. Williams–Landel–Ferry(WLF)식은 `log10 aT=−C1(T−Tref)/(C2+T−Tref)`다. pyvisco.shift.WLF의 반환값은 aT가 아니라 log10 aT다. Arrhenius식은 `ln aT=(Q/R)(1/T−1/Tref)`, R=8.31446261815324 J/(mol*K)다. Tref=293.15 K로 고정하며 Q>0이면 온도가 높을수록 aT<1이다. WLF/Arrhenius는 서로 다른 모델 개정으로 만들고 보정 도중 범주를 바꾸지 않는다.

구간별 선형 변형률·일정 온도에서 r=Δt/(aTτi)로 두면 다음과 같다.

qi,new=exp(−r)qi,old+EiΔε*(1−exp(−r))/r;
stress=E∞ε+Σqi.

expm1과 r→0의 한계 계수 1을 사용한다. 온도 램프는 중점의 일정 온도를 쓰는 하위 간격으로 나누고, 간격을 절반화해 모든 관측 응력의 차이≤1e−7 MPa+1e−5|σ|를 확인한다. 한 구간의 최대 세분화는 16이다. 실패하면 INTEGRATION_FAILED/TEMPERATURE_QUADRATURE_FAILED다. 수렴 조건을 통과한 하위 간격의 상태를 유지한다. 중복 시간은 명시한 jump segment에만 허용하며 np.interp에 그대로 넘기지 않는다. 속도·온도 점프 위치는 반드시 구간 경계로 둔다.

### 파라미터 범위와 시험 설계

다음 파라미터·영역을 고정한다.

- E0∈[10,1000] MPa는 로그 좌표다. ai의 물리 좌표는 선형 [1e−8,.95/n]이며 Σai≤.95다.
- τ의 물리 좌표는 로그 밴드다. n=1일 때 [.001,1000] s, n=2일 때 [.001,.3]/[1,1000] s, n=3일 때 [.001,.03]/[.1,3]/[10,1000] s다.
- 이 보수적인 범위는 양의 평형 탄성계수·순서·시간상수 비≥3을 보장하며 검증기가 다시 확인한다. simplex/softmax/후보 자르기는 사용하지 않는다. 스펙트럼 영역을 넓히려면 새 프로필 개정이 필요하다.
- WLF는 C1∈[.1,20], C2∈[50,300] K, 선언 온도 T∈[293.15,323.15] K이고 전체 영역의 분모≥20 K다. 범위 밖은 SHIFT_RANGE_EXCEEDED, 극점은 WLF_SINGULAR_DENOMINATOR다.
- 별도 Arrhenius 절차는 Q∈[1000,200000] J/mol이며 같은 온도 영역·τ 구간을 사용한다.

TRS가 유리전이온도(Tg) 부근에서 항상 성립한다고 주장하지 않는다. 실제 이동 스펙트럼과 온도 holdout의 적합성으로 판단한다.

참조값은 E0=100 MPa, ai=(.2,.3), τ=(.1,10) s, C1=5, C2=100 K, Tref=293.15 K다.

| 시험 | 표본 구간 | train 온도 | validation 온도 | holdout 온도 |
|---|---|---|---|---|
| 동적 기계 분석(DMA) | f=.001..1000 Hz, 로그 간격 61표본 | 293.15/303.15/313.15 K | 318.15 K | 323.15 K |
| 완화 | t=.001..1000 s, 로그 간격 61표본 | 293.15/313.15 K | 지정 없음 | 323.15 K |

시험별 전체 온도·영역을 분할하며 반복 시편 그룹을 섞지 않는다. 스케일 100 MPa, DMA 저장/손실 각각 .5, 시험별 동일 가중치를 사용하고 log10 좌표의 정규화 사다리꼴 적분 가중치를 고정해 저장한다.

n=1/2/3은 별도 연구로 train한다. score=L_validation+1e−4*n의 최솟값으로 선택하며 정확한 동점이면 낮은 n을 선택한다. holdout으로 n·이동식 종류·범위·정규화 값을 선택하지 않는다. 기존 SLSQP 설정은 seed=17, 최대 평가 1500회, 최대 반복 200회, ftol=1e−10, FDstep=1e−6이다. 초기값은 선언 참조값에서 E0×.9,ai×.9,τ1×2/τ2×.8,C1×.8/C2×1.1로 정하고 초기점에만 범위 보정을 한다. 파라미터를 모두 고정한 검증에서는 0차원 optimize를 호출하지 않는다.

Tref 자료만 있으면 C1/C2 민감도는 0이다. 관측 구간 밖 τ와 관측되지 않은 단시간 E0도 약하게 식별된다. 스케일한 Jacobian의 SVD 계수 판정 기준 1e−6/조건수 한계 1e6을 사용하고 τ/Tref 기준 자유도와 환산 시간·주파수의 겹침을 점검한다. 비식별 파라미터를 고정할 때는 사용자가 볼 수 있는 새 개정에서만 변경한다.

### 독립 기준값과 서로 다른 통과 기준

E0=100 MPa, E∞=50 MPa, E(1)=77.14603053967404 MPa다. ω=1 rad/s에서 (E′,E″)=(79.9009900990099,4.9504950495049505) MPa이고 313.15 K의 aT=.14677992676220694다. Tref의 새 상태에서 변형률 .01을 1 s 램프로 가하면 qi=(.01999909200140475,.2854877458921213) MPa, σ=.805486837893526 MPa다. 허용오차는 탄성계수·응력 1e−10 MPa, aT 1e−13이다. 끝점 한계 loss→0, storage의 저주파 50 MPa/고주파 100 MPa와 수동성도 검사한다.

합성 자료를 독립 SciPy 2항 계산으로 복원할 때 목표는 계수 절대오차 1e−6/손실 1e−18이다. 제품 SLSQP 목표인 손실 1e−10/물리 계수오차 1e−3은 별도의 수치 인수 기준이다.

## B4. B-CYC-AF2-1: 실제 NEML의 Chaboche/Voce 속도 비의존 순환소성

### 식과 NEML 상태의 부호

이 프로필은 소변형·등온 293.15 K·속도 비의존(RI)이다. 다른 vp_flow.ChabocheFlowRule과 혼용하지 않는다.

q=√(3/2)||devσ−ΣXi||,
f=q−[sy+Q(1−exp(−bp))]≤0;
dεp=(3/2)(devσ−ΣX)/q dp,dp≥0,dp*f=0;
dXi=(2/3)Ci dεp−γi Xi dp;
σ=Cel:(ε−εp).

상태는 소성변형률 텐서 εp, 누적 등가소성변형률 p, 각 편차 역응력 Xi와 라이브러리 내부 상태 배열·에너지 기록이다. 호출자는 내부 배열의 위치를 추측하지 않는다. 이 물리 상태 외에도 라이브러리 전용 기록을 유지한다. NEML 소스는 devσ+ΣXnative, 음의 등방경화 q, **음의 내부 역응력**을 사용한다. Xphysical=−Xnative로 정확히 한 번 변환한다. 단축 유효 스칼라는 Xaxial=3/2X11이며 dXaxial=Ci sign(dεp_axial)dp−γi Xaxial dp다.

### 정확한 생성 API와 단축 제어

라이브러리 연결은 다음과 같이 고정한다.

~~~python
elastic=elasticity.IsotropicLinearElasticModel(E,'youngs',nu,'poissons')
iso=hardening.VoceIsotropicHardeningRule(sy,Q,b)
hard=hardening.Chaboche(iso,[C1,C2],
    [hardening.ConstantGamma(gamma1),hardening.ConstantGamma(gamma2)],
    [0.,0.],[1.,1.],noniso=False)
flow=ri_flow.RateIndependentNonAssociativeHardening(surfaces.IsoKinJ2(),hard)
model=models.SmallStrainRateIndependentPlasticity(elastic,flow,
    rtol=1e-10,atol=1e-12,miter=100)
d=drivers.Driver_sd(model,T_init=293.15,no_thermal_strain=True,
    rtol=1e-9,atol=1e-11,miter=100)
~~~

`model.update_sd(e_np1,e_n,T_np1,T_n,t_np1,t_n,s_n,h_n,u_n,p_n)`은 `(s_np1,h_np1,A_np1,u_np1,p_np1)`를 반환한다. 마지막 p_n은 **소산 에너지 기록이며 누적 등가소성변형률이 아니다**. Mandel 순서 [xx,yy,zz,√2yz,√2xz,√2xy]의 변환을 명시한다. 첫 지원 범위는 단축 수직 성분뿐이다. 일반 비틀림·혼합 드라이버는 변환·드라이버 검증 전까지 지원하지 않는다.

단축 변형률 제어에는 `d.erate_einc_step([1,0,0,0,0,0],signed_erate,signed_einc,T)`를 사용해 횡응력 0을 푼다. `strain_step([εx,0,0,...])`는 횡변형률을 구속한 단축 변형 상태이므로 대신 사용할 수 없다. 래칫은 `d.stress_step([σx,0,0,0,0,0],t,T)`로 계산한다. 수집기는 실제 시간, d.stress[:,0], d.strain[:,0], 횡응력 0 잔차를 요구한다. ep_axial=εx−σx/E는 이 정확한 단축·등온 조건에만 쓰는 파생 관측량이다. model.report_internal_variable_names()와 라이브러리 내부 상태 배열을 보존하고 검증하지 않은 p/X의 위치 색인을 비교 채널로 쓰지 않는다. 반전 구간에서 초기화하지 않으며 독립 시험·후보마다 새 model+driver를 만든다.

### 파라미터, 하중 이력과 수렴 검사

E=200000 MPa, nu=.3을 고정한다. 참조값은 sy=250 MPa, Q=50 MPa, b=10, C=(20000,5000) MPa, γ=(200,20), A=(0,0), a=(1,1), noniso=False다. 범위는 sy∈[150,400] MPa, Q∈[0,150] MPa를 선형, b∈[1,100], C1∈[1000,50000] MPa/C2∈[100,20000] MPa, γ1∈[100,500]/γ2∈[1,90]을 로그 좌표로 사용한다. C/γ/b>0,Q≥0이며 순서 조건으로 두 항의 교환을 막는다. 정적 회복·온도 회복·점소성 확장은 별도 정식화와 속도·온도 시험이 필요하다.

train 저진폭 경로는 0→.002→−.002→.002→−.002→.002, 고진폭은 0→.006→−.006→.006→−.006→.006이다. |rate|=.001/s, maxΔε=1e−5를 사용하고 모서리점을 정확히 포함한다. validation은 같은 순환의 진폭 .004다. holdout 응력은 0→350→−100→350→−100→350→−100→350 MPa, step≤.5 MPa, |rate|=100 MPa/s다. 여기서는 변형률·평균 누적 이동을 관측하며 이미 지정한 끝점 응력만 비교하지 않는다.

train 응력 스케일 400 MPa, holdout 변형률 스케일 .01, 시험별 동일 가중치와 시간 적분 가중치를 사용한다. 최적화는 DE(seed=17,pop=15,최대 평가 3000회/반복 200회), differentiable=false이며 대리모델은 없다. Bauschinger 항복 개시, 이력고리 모양, 순환경화, 래칫은 각각 별도 관측량이다. 단조 보정만으로 γ 동정이나 래칫 적합성을 승인하지 않는다.

라이브러리 반복 한계는 100회이며 위 허용오차를 쓴다. 같은 입력에서 같은 순서로 수행하는 구간 절반화로 최대 16단계 세분화하되 원래 비교 격자는 유지한다. h=1e−5→h/2에서 모든 표본의 응력 차이≤.1 MPa, 이력고리 일 차이≤.5%여야 한다. 래칫 step=.5→.25 MPa에서는 끝점 변형률 차이≤1e−5와 누적 이동 방향 일치를 요구한다. 비유한값·평형 실패는 INTEGRATION_FAILED, 응력 포화 한계 밖에서 평형이 없으면 STRESS_CONTROL_NO_EQUILIBRIUM이다. 이전 응력을 복제하거나 실패 표본을 누락하지 않는다.

일곱 자유변수의 스케일한 Jacobian에 SVD 계수 판정 기준 1e−6/조건수 한계 1e6을 적용한다. Q=0에서 b, 같은 γ에서 두 항의 분배, 작은 p에서 C/γ가 비식별임을 보고한다.

### 설치 실패, 출처 확인과 독립 기준값

MIT 라이선스의 NEML은 컴파일한 C++와 CMake/BLAS/LAPACK, 바이너리 인터페이스(ABI), 동봉 고지가 필요하다. setup.py에 Windows 분기가 있다고 해서 Windows 설치가 검증된 것은 아니다. 선택 백엔드가 없으면 이 프로필만 BACKEND_UNAVAILABLE다.

소스 빌드 절차는 고정 checkout 확인→문서화한 네이티브 선행 의존성 준비→`pip wheel --no-deps .`→compiler/BLAS/PythonABI/source-tree/wheelSHA를 담은 빌드 기록→격리 설치다. PyPI 버전 1.5.4나 NEML_SOURCE_SHA 환경문자열만으로 소스 대응 관계를 증명하지 않는다. 실행기는 수치 비교를 통과해도 실제 로드한 확장 모듈 해시와 source_correspondence=unverified/exit21을 남긴다. 제품용 빌드 기록·해시 검증기는 별도 통과 조건이다. 이번 실제 설치는 BLAS_LIBRARIES 부족으로 실패했으며 실제 텐서 라이브러리는 미실행/exit20이었다.

독립 스칼라 기준은 순수 Armstrong–Frederick(AF)의 sy=250 MPa, C=10000 MPa, γ=100, Q=0, p=.001에서 연속해 X=9.516258196404042 MPa, 역방향 항복 개시 응력=-240.48374180359596 MPa다. 선택한 2항 후진 Euler(BE)의 고진폭 순환 마지막 σ는 355.32878792738677 MPa(h=1e−5), 355.3480599878022 MPa(h=5e−6)다. 응력 holdout의 마지막 ε=.007664366981880675(step=.5 MPa)다.

실제 NEML 실행기와 같은 BE 격자를 비교할 목표는 최대 σ 오차 .002 MPa, 최대 ε 오차 1e−7, 횡응력 1e−6 MPa다. 연속 AF 정확해의 허용오차 1e−10과 유한 간격 BE 해의 동일성은 서로 다른 기준이다. 같은 법칙으로 만든 합성 스칼라 기준값은 실제 물성 검증이나 실제 NEML 합격을 뜻하지 않는다.

## B5. B-JC-STATE-1: 측정 소성 상태의 유동응력과 국소 파손 개시

### 계산 가능한 관측량과 정확한 식

이 프로필은 측정한 소성 상태에서 유동응력과 국소 파손 개시를 구하는 정확한 해석식 계산이다. 전역 힘·변위로부터 FE 응력을 갱신하거나 탄소성 적분·연성 발열·연속 응력 저하·요소 삭제·메시 객관적인 최대하중 이후 거동을 제공하지 않는다. 이런 요청은 UNSUPPORTED_OBSERVABLE/EVALUATION_LEVEL 또는 UNSUPPORTED_REGULARIZATION으로 거부한다.

p≥0은 누적 등가소성변형률, r=dp/dt≥0(1/s)은 그 속도다. 인장 양수에서 응력 삼축도 η=mean(Cauchyσ)/q, q=vonMises>0, 무차원 온도 θ=(T−Tr)/(Tm−Tr)다. 선택한 소스 분기는 IFORM=0, FISOKIN=0, YMAX 상한 미적용(uncappedYMAX), 속도 필터 없음, 지정 온도다. Tr=293.15 K, Tm=1293.15 K, r0=1/s를 고정한다. 지원 θ∈[0,.9]이며 검증 입력은 θ≤.7이다.

σflow=(A+B p^n)*(1+C ln(max(1,r/r0)))*(1−θ^m).
εf=max(εmin,[D1+D2exp(D3η)]*[1+D4ln(max(1,r/r0))]*[1+D5θ]).
Dnew=min(1,Dold+Δp/εf); εmin=1e−6.

η/r/T가 일정한 구간에서는 정확한 교차 계산으로 pcritical/time을 구한다. Δp=0이면 D는 변하지 않는다. Δp>0인데 r=0인 모순은 거부한다. q≈0에서 Δp가 양수이면 TRIAXIALITY_UNDEFINED이며 η=0으로 채우지 않는다. 소스 파손식의 EPSP는 속도, DPLA는 소성 증분이다. 소스는 P=+meanσ와 지수+D3η를 사용하므로 압축 양수 압력 문헌의 D3를 그대로 쓰면 안 된다.

### 파손 개시와 응력 저하의 구분

D는 파손 **개시 지표**다. 유동응력에 (1−D)를 곱하지 않는다. 네이티브 ISOLID=1의 임계값의 OFF=.8/후속 감소, 다른 플래그의 편차응력 소멸·압축 보존, XFEM/shellNIP/LAW2εmax는 별도 네이티브 동작이다. 이 해석식 D와 같은 응력 저하로 취급하지 않는다.

η/r/T가 변하면 소스 방식의 오른쪽 끝점 D 누적에 중점·절반화 수렴 검사를 적용한다. D의 차이≤1e−5 또는 pcritical의 차이≤1e−4, 최대 16분할을 요구한다. 미수렴은 INTEGRATION_FAILED다. εmin 하한이 활성화되면 ACTIVE_FAILURE_FLOOR와 민감도 손실을 보고한다. 시편마다 새 상태로 시작하며 같은 시편의 예하중 상태는 유지한다.

### 보정할 파라미터와 필요한 측정자료

유동 참조값은 A=300 MPa, B=400 MPa, n=.5, C=.02, m=1이다. 범위는 A∈[100,600] 선형, B∈[10,1000] 로그, n∈[.1,1],C∈[0,.1],m∈[.2,3]이다. 별도 파손 참조 파라미터는 D=(.1,.2,−1,.01,.2), 범위는 D1∈[.01,.5],D2∈[.01,1],D3∈[−5,−.01],D4∈[0,.1],D5∈[0,1]이며 모두 선형이다. r0/Tr/Tm/εmin은 고정한다. 선언 시험 영역은 p∈[0,.2],r∈[1,1000]/s,θ∈[0,.7],η∈[−1/3,2/3]이며 유동응력·εf는 양수다.

유동과 파손은 별도 보정 연구로 수행한다. 네이티브 역해석에서 유동 파라미터를 바꾼 뒤 기존 η를 변하지 않는 측정값처럼 재사용하지 않는다. 유동 시험의 p는 0.. .2를 .01 간격으로 사용한다. train(r,T)=(1,293.15),(10,293.15),(1,793.15), validation(100,493.15), holdout(1000,993.15)다. 관측 유동응력은 Cauchy이며 p/r/T는 측정 공변량이다.

파손은 η=−1/3,0,1/3,2/3에서 국소 p_atfailure를 관측하고 같은 T/r 분할을 사용한다. 상태가 변하는 시험은 D 적분으로 비교하며 일정 η의 εf 식으로 대체하지 않는다. 힘·변위·파단 연신율만 있으면 DATA_RECIPE_INCOMPATIBLE/LOCAL_PLASTIC_STATE_REQUIRED다. 네킹 뒤 체적 가정만으로 log(1+εeng)−σ/E를 자동 국소 소성변형률로 만들지 않는다. 국소·현재 상태의 변형률/응력/온도/속도 또는 독립 검증한 FE 추론이 필요하다.

유동 스케일 500 MPa, 파손 p 스케일 .5와 시험별 동일 가중치를 사용한다. SLSQP는 seed=17, 최대 평가 1500회/반복 200회, ftol=1e−10, FD=1e−6이며 초기점은 범위 안의 참조값×.9다. 대리모델은 없다. 기준 속도·온도에서는 C,m의 민감도가 0이므로 유동 계수(rank)≤3/5다. η 하나에서는 D1/D2/D3의 단일 조합의 계수(rank)≤1이고, 기준 상태에서 D4/D5 민감도는 0이다. 스케일한 SVD 계수 판정 기준 1e−6/조건수 한계 1e6을 적용하고 holdout은 학습하지 않는다.

독립 기준값은 다음과 같다.

| 상태 | 결과 |
|---|---|
| p=.1, r=1/s, θ=0 | 유동응력=426.4911064067352 MPa |
| η=0, r=1/s, θ=0 | εf=.3 |
| 새 상태에서 Δp=.15 | D=.5 |
| 새 상태에서 p=.3 | 파손 개시 |
| θ=.5, m=1 | 유동응력이 절반이 됨 |
| r=100/s | 속도 배율=1+.02ln100 |

허용오차는 응력 1e−10 MPa, 변형률·D 1e−12다. 일정 상태에서 증분을 나눠도 차이는 1e−12 이내여야 한다.

### 메시·파괴에너지·네이티브 연결의 미지원 범위

국소 재료점 개시에는 요소 길이·국소화 띠 폭·Gf가 **없다**. supports_fracture_energy=false다. εf=Gf/(σh)를 임의로 만들지 않는다. 네이티브 개시 전용 기능으로 확장하려면 요소·적분점(IP)·메시 특성길이 정의·시편 메시를 고정하고 h,h/2,h/4의 힘·에너지·개시 변화를 비교해야 한다. ≤5%는 명시적으로 선언한 적합성 목표일 뿐이다. 최대하중 이후 에너지를 다루려면 정확한 별도의 traction/separation 또는 crack-band 법칙과 솔버 길이 정의가 필요하다. 등록 메타데이터가 이를 대신하지 않는다. 이 기능 안에 미결정 정규화 선택을 숨기지 않는다.

네이티브 계열 출처는 /MAT/LAW2(PLAS_JOHNS)+/FAIL/JOHNSON이다. 읽기 필드 폭·기본값·요소·열·삭제 옵션·정확한 codec·추출기는 이 해석식 프로필에서 지원하지 않는다. 확장 시에는 정확한 IP의 p/rate/Tstar/전체 Cauchy/D와 실제 시간·단위·부호·소스 대응을 확인해야 한다. 평균 애니메이션이나 RP 픽셀로 대체하지 않는다. B 소스 SHA는 D 배포 바이너리 식별자가 아니다.

## B6. 과거 실제 실행, 재현 방법과 제품 인수 조건

### 환경 소실 전에 실제로 실행한 범위

1. reference_probe.py로 HYP의 세 INC 모드·정수압·유한 K의 횡평형근·에너지 기울기/객관성, Prony의 시간/DMA/WLF/램프 및 독립 SciPy 2항 복원, 독립 CYC 스칼라 BE의 반전·저/고진폭·validation·래칫·간격 세분화, JC 유동·파손, K/WLF/JC 행렬의 계수(rank) 반례를 실행했다.
2. run_pyvisco.py로 실제 2.1.3 API와 로드 소스 해시의 정확한 일치를 확인했다. Prony sourceSHA256=641490da54f1d62d5f631c899f3f0ade44d1f9debc98a9e9068fe4f0577795bb; shift=93f143f9789e31aaffebc229fe76b0c3b37ffa0b4937d5b2980b1d524e2503a2다.
3. 고정한 기존 Workbench optimize와 실제 pyvisco의 자유변수 5개·2항 스펙트럼으로 합성 완화 자료를 보정했다. 후보 파라미터 조합을 146회 평가해 CONVERGED, fit_loss=1.4321823375482084e−13이었다. E0=99.99991157325094,a=(.19999927747118482,.3000001346797645),τ=(.10000070016113262,9.99998403748328)s였다. WLF를 고정한 채 손대지 않은 323.15 K holdout 손실은 3.2911807678520303e−14였다. 이 참조 실행의 정확한 설정은 ftol=1e−12/FD=1e−7이며, 제품 재료 서비스·GUI를 확인한 것은 아니다.
4. 실제 기존 ConstraintSpec/assess_constraints에서 큰 오차·안정한 경우 fit_loss=.02/FEASIBLE, 유한한 불안정 경우 fit_loss=.02/INFEASIBLE, 필수 스칼라 무효 경우 fit_loss=null/NOT_ASSESSED를 확인했다.
5. 앞서 설명한 COMP 243개 경우와 모든 근 실패 반례를 실행했다.
6. 묶음·core를 다른 위치로 복사하고 Python 감사 기능으로 두 원본 디렉터리 읽기를 거부한 상태에서 reference/equilibrium/actualpyvisco/existingoptimizer/constraints/assetintegrity는 exit0이었다. NEML은 exit20으로 별도 미충족 상태였다. 실제 로드한 주요 core 경로는 복사한 core만 가리켰다. 소실 전에 고정 자산 30개/예상 수치 검사 19개가 통과했고 원래 R2 실행 기록을 보존했다. 이 과거 결과는 새 재실행이나 원시 실행 기록 바이트 복구를 뜻하지 않는다.

### 임의 위치에서 다시 실행하는 방법

실행 위치는 --workbench-root로 명시한다. 부모/workbench 디렉터리를 추정하지 않는다. workbench_source.py는 기준 104cdd6888a5554eecc8cf965c60ff1bd980b6da의 주요 7개 파일을 먼저 검사하고 실제 import 경로·SHA를 다시 확인한다. 경로는 caelab/__init__.py,mdo/__init__.py,variables.py,optimize.py,objectives.py,constraints.py,pareto.py다. 이 코드 바이트가 같으면 문서만 변경한 HEAD는 달라도 된다. 일곱 core 파일만으로 모든 선택 패키지를 인증할 수는 없다. 실제 로드한 pyvisco 해시는 별도로 확인한다.

사용자가 고른 선택 Python 3.12 가상환경에서 NumPy 2.3.5, SciPy 1.17.0, pyvisco 2.1.3, filelock 4.2.10을 사용한다. pyvisco가 선언한 의존성은 pip로 설치한다. 과거 /tmp 감사 경로는 설치 지침이 아니다. B 묶음과 core는 어디에 두어도 된다. 예시는 다음과 같다.

~~~text
<env-python> <B>/reference_probe.py
<env-python> <B>/equilibrium_probe.py
<env-python> <B>/run_pyvisco.py
<env-python> <B>/optimizer_probe.py --workbench-root <explicit checked core>
<env-python> <B>/constraint_probe.py --workbench-root <explicit checked core>
<env-python> <B>/check_assets.py
<env-python> <B>/run_neml.py
~~~

NEML의 20은 BACKEND_UNAVAILABLE,21은 수치 통과/소스 대응 미검증이다. 어느 쪽도 고정 백엔드의 합격이 아니다. CMake/BLAS/PythonABI/소스 빌드 기록/로드 확장 모듈을 별도 확인해야 한다. B에서는 Windows 설치·화면·공급자·네이티브 보정·실물 재료 적합성을 실행하지 않았다.

### 구현 후 사용자가 끝까지 확인할 조건

예정한 네 어댑터 파일과 tests/test_material_ogden.py,prony_trs.py,neml_chaboche.py,jc_state.py를 각각의 검증 자료 경로와 함께 구현한다. 엄격한 CSV→ExperimentSpec→plan→실제 응답→PredictionBundleV1→호스트 잔차/제약→판정→저장/재열기→내보내기를 확인한다. 단위·측도·자료 범위·취소·초기화·실패 반례도 포함한다. 공통 registry/units/service/GUI는 배정된 소유자가 계속 담당한다.

예정된 독립 MaterialStudy 창과 기존 Workbench 연결 방식을 사용한다. 이미 구현된 재료 창이 있다고 가정하지 않는다. 저장소 밖에서 얻은 실측자료와 실제 Windows 화면은 각각 별도 통과 조건이다. NEML 라이브러리 백엔드를 검증하려면 실제 텐서 NEML 실행이 필요하며, 없다고 skip한 뒤 합격으로 표시할 수 없다.

새 법칙은 정확한 정식화·상태·파라미터·단위·영역, 라이선스를 확인하고 고정한 구현/API/네이티브 옵션, 유한한 시험·자료 변환 절차, 관측량·추출기, 물리 제약·식별성, 독립 수치·실패 기준값을 제공한 뒤에만 core의 모델명 분기 없이 추가한다. 등록은 호출 가능성만 뜻한다. 손실 없는 이력 전달, 명시한 법칙의 계산, 실제 사용자 연구 흐름은 각각 별도 통과 조건이다. 원래 네이티브 파일 실행은 독립적으로 유지한다. 합성 자료 보정·소스 조사·계획 완성을 제품 실행이나 공학적 승인으로 확대하지 않는다.
