# 재구성 B 명세: 유한변형·시간/온도·순환소성·속도/손상

복구판 B-RECOVERY-1, 2026-11-12. 작업환경 소실 뒤 저자가 직접 재구성했다. 이전 part SHA256 f3f1ce4d1d96fa60738268aa0d2dc685232a2e4a2ca6b849d48930f32924c399와 동일한 파일이라고 주장하지 않는다. 아래 원식·계약·기록된 수치 실행을 보존하지만 원래 CSV/raw 결과/receipt bytes를 추측 복원하지 않는다. 새 실제 blob의 E/F 재검토 전 최종 승인으로 표시하지 않는다.

## B0. 범위, 구현 위치와 공통 정본

사용자 U1–U9 전체 목표는 Prep의 공용 CAE 자산과 Workbench의 독립 프로그램·재료 연구·전문가/RAG/tools/review·후처리 연결의 실제 상태와 구현 가능한 인계를 완성하는 것이다. DP/Xia는 예시이며 J2 단조곡선이나 재료명 registry만으로 이 목표를 대체하지 않는다. 본 B 절은 서로 다른 물리·데이터·계산 계약을 갖춘 정확한 analytic/library 프로필을 제공한다. 원 덱 실행은 독립 본선이며 calibration capability 거부로 차단하지 않는다.

신규 예정 adapter 파일은 caelab/materials/adapters/ogden.py, prony_trs.py, neml_chaboche.py, jc_state.py다. 공통 MaterialAdapter.capabilities/validate/plan/collect/export를 구현한다. analytic/library는 등록된 신뢰 callback의 EvaluationPlan.kind=in_process다. JSON의 arbitrary Python/module path를 실행하지 않는다. registry/installed entry point 등록으로 확장하고 서비스에 모델명 if/elif를 넣지 않는다. 기존 MDO optimizer/VariableSpace/StudyManager/JobManager/RunStore/atomic_json/FileLock을 재사용한다. 두 번째 FE schema, 실행 큐, DB, 유료 API를 만들지 않는다.

Workbench가 실험 원자료·시험·변수·잔차·최적화·결과 출처를, Prep이 native 원본·typed Apply/Undo를 소유한다. 이 연구는 CAD/FE 메시 없이 시작할 수 있다. 현재 Prep LAW1/LAW36 기본필드 편집이 아래 법칙 전체의 typed Apply라는 주장은 금지한다. 이 프로필은 supports_apply=false다. export는 정확 formulation/단위/파라미터/source/시험범위/한계를 담은 parameter JSON의 MaterialExportPlan이며 native 카드 export는 지원하지 않는다. native 지원은 별도 정확 codec/element/output/extractor/실행 검증을 통과해야 한다.

공통 ArtifactRefV1, MaterialStudy v1, ComparisonSpec, PredictionBundleV1, CalibrationEvaluationArtifact, AssessmentResultV1, ResultRefV1을 사용하고 필드 이름을 복제하지 않는다.

아래 C1/C2/C6/C7/C8 표기는 앞서 합의한 decision ID다. 현재 재구성 core b3ec806dd6d5e52c5062c75fc98b1e829b2c08ac의 절 번호는 비교·판정 C4, 단위 C6, scalar C5, revision C2, PredictionBundle C3다. 현재 core의 정확 wire/필드별 enum을 정본으로 따른다.

- C1: ComparisonSpec.id/required_for_fit/required_for_validation 필수. fit=true는 train, validation=true는 validation/holdout에서만 허용하며 둘 다 false는 진단이다. train 목적함수에 선택한 모든 채널의 목록/분모/가중치는 고정한다. candidate 실패나 missing으로 시험을 빼지 않는다.
- C2: materials/units.py QuantityUnitSpec(unit,quantity_kind), convert_values가 단일 단위 경로다. mechanical은 기존 parse_unit에 위임한다. absolute_temperature는 K 또는 degC+273.15, 변환 후 T>0. temperature_difference는 K/degC 동일간격이고 음수/0 허용. activation energy J/mol, kJ/mol×1000; R의 단위 J/(mol*K). WLF denominator는 온도차, Arrhenius 분모는 절대 K다.
- C6: CapabilityReport.optimizer_responses/optimizer_constraints/differentiable를 등록하고 ScalarResponseSpecV1의 schema_version=1,response_id,unit,source_kind,source_name을 저장한다. source_kind는 residual/parameter_expression/adapter_scalar다. mandatory profile constraints는 사용자 임의 삭제 불가이며 response_units/constraints/content_revision에 고정한다.
- C7: mutable expected_revision=document_revision이고 MaterialEvaluationRequest.study_revision/OptimizationProblem.revision은 immutable content_revision이다. 모델/시험/비교/수치/제약/optimizer 계약이 content에 들어간다. progress/index/selection 저장은 campaign content를 바꾸지 않는다. worker는 token+content_revision을 대조하며 과거 artifact/cache는 당시 content에 남는다.
- C8: PredictionBundle은 current core C3의 PredictionBundleV1 별칭이다. experiments[].channels[]는 observable, independent_ref, sample_keys_ref, sample_key_columns, values_ref, mask_ref, component_order, value_kind, representation, coverage, source_refs, source_result_refs를 그대로 채운다. independent shape(N), values/mask(N,C), sample_keys(N,K), component_order 길이 C다. artifact_owners는 scope=consumer,id=material-study:<id>를 쓴다. point/library에는 run_ids=[],source_result_refs=[]이며 FE 결과로 꾸미지 않는다. optimizer_scalars에는 등록된 parameter_expression/adapter_scalar만 넣고 fit_loss는 넣지 않는다. host residuals가 fit_loss를 계산하여 기존 EvaluationObservation.responses로 전달한다.

CSV는 immutable UTF-8 RFC4180 header+finite cells이며 원본 bytes/hash, column_mapping, 단위/측도/basis/sign/control/observed/시험ID/split/replicate_group/변환 recipe를 보존한다. source_kind=synthetic_arithmetic인 fixture는 물리 실험이 아니다. sample_keys=[source_row]는 원 CSV행 정수이며 실제 time/frequency/stretch를 ordinal로 대체하지 않는다. HYP/CYC/JC와 VIS storage/loss 각각은 real/scalar,C=1, 명시 component_order다. VIS 두 채널은 같은 phase recipe와 실제 frequency grid를 공유한다. 필수 mask/coverage를 검사하며 source_refs에 원CSV·단위/변환·계산 recipe 및 source/library provenance를 넣는다.

시험 내 preload/반전/온도경로 state는 유지한다. 매 후보×독립 시험은 fresh initial state다. 별도 시험 간 restart/continuation은 UNSUPPORTED_STATE_CONTINUATION이다. library call interrupt 한계는 capability에 표시하고 취소 후 반환값을 성공으로 저장하지 않는다. 캐시는 adapter implementation/config/model, 파라미터/units, 모든 원자료·recipe·수치·비교·source/library identity hash를 포함한다.

정본 잔차 r=(pred−obs)/고정 scale, L=sum(q*r²), 고정 train 목록/정규화 가중치의 합을 사용한다. 관측 grid 내 linear interpolation만, 외삽/후보별 overlap crop 금지다. cyclic repeated strain은 time/명시 branch로 비교하고 strain 전역 sort를 금지한다. high valid error는 finite fit_loss+fit fail이다. required train 실행/측도/무결성/coverage/required constraint scalar 실패는 objective.eligible=false,fit_loss=null이다. holdout 실패는 유효 train objective를 지우지 않는다. assessment는 fit_status/validation_status/holdout_status/adequacy_status/uncertainty_status/identifiability_status/constraint_status 등의 단일 정본이며 train_fit 별칭을 중복 직렬화하지 않는다.

공통 오류와 details.reason을 구분한다: INVALID_PARAMETER, UNSUPPORTED_DOMAIN/OBSERVABLE/UNIT/ADAPTER_VERSION, MEASURE_MISMATCH, COVERAGE_INCOMPLETE, NONFINITE_RESPONSE, INTEGRATION_FAILED, BACKEND_UNAVAILABLE, CONSTRAINT_RESPONSE_INVALID, CANCELLED. 정상 고오차/물리 제약 위반을 가짜 penalty나 execution failure로 바꾸지 않는다. 구조적 비식별은 finite 평가를 보존하고 not_identifiable/영공간/자유변수 목록을 보고한다. Jacobian/SVD 계산 자체 실패나 자료부족이면 identifiability_status=not_computed다. 수치 계산불능을 구조적 rank deficiency로 바꾸지 않으며 필수 adequacy policy에서만 not_assessed로 매핑한다.

## B1. 고정 source와 license 경계

| ID | repository/revision | 실제 읽은 path/API | 경계 |
|---|---|---|---|
| B-SRC-HYP | adtzlr/hyperelastic 58b036acc389585110f5d74e0457c36fae0cc4f2 | src/hyperelastic/models/stretches/_ogden.py Ogden.gradient/hessian | GPL-3.0 source 비교. 커널 복사 대신 아래 독립 정확 analytic peer |
| B-SRC-VIS/WLF | NatLabRockies/pyvisco b61b5a0c6a2939927c87189972ea5619503bd813 | pyvisco/prony.py E_relax_norm/E_freq_norm, pyvisco/shift.py WLF | BSD-3-Clause 고지 보존. 실제2.1.3 loaded files가 source SHA256과 같았음 |
| B-SRC-CYC | Argonne-National-Laboratory/neml 65ab2a648a975e2e9de8e1371be270567f9683ac | src/hardening.cxx Chaboche::h/q; neml/drivers.py; src/models.cxx/models_wrap.cxx; test/test_hardening.py; setup.py | MIT 및 pybind11/RapidXML notice; compiled optional backend |
| B-SRC-JCF/JCD | OpenCourant/OpenCourant d791761d9636abaa27e41ebbe271fa9df519287e | engine/source/materials/mat/mat002/m2cplr.F, engine/source/materials/fail/johnson_cook/fail_johnson.F | AGPL-3.0-or-later source 의미 근거. 커널 복사/native binary 동등성 주장 없음 |

정확 URL은 https://github.com/<repository>/blob/<revision>/<path>다. B의 OpenCourant source와 D의 cb760c runtime/source를 자동 동일시하지 않는다. source revision은 release binary 대응 증명이 아니다. source/API 변경은 새 adapter version/source diff/fixture 재검증이 필요하다.

## B2. Ogden 프로필: INC와 COMP는 별도 formulation

### B-HYP-INC-1

정확 incompressible Ogden N=2, 2μ/α² convention:
W=Σj 2μj/αj²*(λ1^αj+λ2^αj+λ3^αj−3), J=1.
μj 단위 MPa, αj dimensionless, W는 기준체적당 MPa다.
βi=Σj (2μj/αj) λi^αj; Cauchy σi=βi−p; nominal PK1 Pi=σi/λi.
p는 Lagrange multiplier이며 traction-free thickness에서 β3다. 초기 shear μ0=Σμj.

| 시험 | stretches/조건 | P1 |
|---|---|---|
| 단축 | (λ,λ^−1/2,λ^−1/2), σ2=σ3=0 | Σ2μ/α*(λ^(α−1)−λ^(−α/2−1)) |
| equibiaxial | (λ,λ,λ^−2), σ3=0 | Σ2μ/α*(λ^(α−1)−λ^(−2α−1)) |
| planar tension | (λ,1,λ^−1), σ3=0, 횡반력 허용 | Σ2μ/α*(λ^(α−1)−λ^(−α−1)) |

Reference μ=(.8,.2)MPa,α=(1.3,5). μj∈[.01,10] log,α1∈[1.2,3],α2∈[3.2,6] linear; ordering gap≥.2. Train 전체 단축/이축 λ=.75..2 step.05, planar 전체가 holdout이다. INC는 K 파라미터가 없다. nomstress=force/reference area와 λ=1+u/L0 변환은 측정 geometry를 기록한다. 실제 측정자료를 model incompressibility만으로 Cauchy로 바꾸지 않는다.

### B-HYP-COMP-1

정확 analytic COMP N=1은 별도 formulation이다:
barλ=J^(−1/3) λ;
W(F)=Σj2μj/αj²*(Σi barλi^αj−3)+K/2*(J−1)².
βi=Σj(2μj/αj)barλi^αj;
σi=(βi−meanβ)/J+K(J−1); P=JσF^−T.
F SVD로 principal 값을 계산하고 requested basis로 tensor를 돌려준다. 반복 고유값은 연속 isotropic limit이며 eigenvalue 차이0으로 나누지 않는다. stress API가 첫 지원이며 일반 FE tangent API는 아니다.

Reference μ1=1MPa,α1=2,K100MPa. μ[.1,10] log,α[1.2,6] linear,K[10,1000] log. Train finite-K uniax 및 hydro J={.97,.99,1,1.01,1.03}; validation은 biax λ=.85..1.85 step.05. Hydro F=J^(1/3) I는 mean Cauchy σ=K(J−1)이며 압축양수 pressure 관측은 명시적으로 부호 반전한다. INC force curve와 COMP hydro를 한 finite-K model data로 섞지 않는다. J=1만의 데이터는 K sensitivity=0이다. K를 고정하거나 비식별로 보고하며 목적함수를 꾸며내지 않는다.

### COMP lateral root: 정확 알고리즘

Uniax λ1=λ,λ2=λ3=z; biax λ1=λ2=λ,λ3=z; planar λ1=λ,λ2=1,λ3=z. R(z)=J*σ_thickness(z)=Σck z^pk로 쓴다. cj=2μj/αj이며 각 pair는 (coefficient,power)다.

| mode | per-j distortional terms | volume terms |
|---|---|---|
| uniax | (cj/3 λ^(−α/3),α/3), (−cj/3 λ^(2α/3),−2α/3) | (Kλ²,4),(−Kλ,2) |
| biax | (2cj/3 λ^(−2α/3),2α/3), (−2cj/3 λ^(α/3),−α/3) | (Kλ⁴,2),(−Kλ²,1) |
| planar | (2cj/3 λ^(−α/3),2α/3), (−cj/3[λ^(2α/3)+λ^(−α/3)],−α/3) | (Kλ²,2),(−Kλ,1) |

지원 z-domain=[1e−4,1e4], implicit expansion 없음. log10z=−4+i/16의129 nodes/128 cells 전체를 검사한다. 각 signed monomial의 양끝 min/max와 합으로 R/R′ interval bounds를 만들고 roundoff guard=64*eps_float64*max(1,sum_abs_endpoint_terms)를 양쪽에 더한다. R interval이0을 배제하면 해당 cell root 없음. R′ interval이0을 배제하면 numerically monotone cell: endpoint-zero 또는 sign-change root만 존재하며 sign change는 Brent xtol=rtol1e−14,max100으로 푼다. 둘 다 불확정이면 geometric midpoint로 bisect,max20 levels. 아직 uncertified/tangent면 INTEGRATION_FAILED/LATERAL_ROOT_UNCERTIFIED로 거부하고 다른 root를 골라 성공시키지 않는다.

검출 roots를 정렬, |ln(z1/z2)|≤1e−9면 낮은 |R| root로 merge한다. domain 전체에 exactly1 root 및 |R/J|≤1e−9MPa를 요구한다. 0개는 LATERAL_EQUILIBRIUM_NOT_BRACKETED, >1은 AMBIGUOUS_EQUILIBRIUM이다. previous/closest/stable root를 몰래 선택하지 않는다. 이 finite-domain numerical screening은 formal real-arithmetic/global uniqueness proof가 아니다. 유일 root에서 dimensionless lateral_log_slope_over_mu=zR′/(Jμ0)를 계산한다. 잘 계산된 음의 slope는 finite constraint violation이고, root coverage/숫자 계산불능은 invalid다.

이 알고리즘은 이전 R3 실행에서 mode3×λ{.75,1,2}×μ{.1,1,10}×α{1.2,2,6}×K{10,100,1000}=243 cases의 residual≤1e−9 및 reverse candidate order exact 동일성을 통과했다. Monomial no-root, outside-domain, two-root, tangent-uncertified 반례도 각 예상 오류를 냈다. 복구된 코드 자체는 환경 unavailable로 아직 재실행하지 않았다.

### HYP optimizer scalar/stability 정본

Physical validation은 μj>0,αj≥1.2,K>0이다. COMP bulk_ratio=K/Σμ의 [10,1000]은 execution error가 아니라 mandatory numerical optimization constraints다. positive-K ratio 위반 후보도 finite loss를 유지한다. HYP 양쪽 differentiable=false, 기존 DE(seed17,population15,max_evaluations3000,max_iterations200), no surrogate다. eigenvalue minimum/branch 거부를 smooth SLSQP로 가장하지 않는다.

고정 stability envelope는 모든 frozen experiment F samples, 각 mode λ={.75,1,1.25,1.5,1.75,2}, COMP hydro J={.97,.99,1,1.01,1.03}, F=I+γe1⊗e2의 γ={−.5,−.25,0,.25,.5}, 각각 I/Rz90/Rx90 premultiplication이다. INC는 J1 closed-form stretches, COMP는 unique lateral root다. n은 {−1,0,1}³\{0}의 정규화26 directions. A=d²W/dF²는 h=1e−5와h/2 중앙차분(대각 ±h second difference, 비대각 mixed4point/4h²)이다. Qij=AiAjB nA nB; INC는 m·F^−Tn=0의 orthonormal2D tangent basis에 투영한다. 고정 domain밖 전역 타원성은 주장하지 않는다.

모든 ScalarResponseSpecV1에 schema_version1이 필수이며 dimensionless unit=""이다.

| response/source | ConstraintSpec |
|---|---|
| bulk_ratio, parameter_expression K/mu1(COMP N1) | LOWER10/scale10/tol0와 UPPER1000/scale1000/tol0 |
| stability_min_acoustic_over_mu, adapter_scalar ogden.stability.min_acoustic_over_mu.v1=min eig(Q_h/2)/μ0 | LOWER1e−6/scale1/tol0 |
| stability_derivative_discrepancy_over_mu, adapter_scalar ogden.stability.max_derivative_discrepancy_over_mu.v1=max||Q_h−Q_h/2||2/μ0 | UPPER1e−3/scale1e−3/tol0 |
| lateral_min_log_slope_over_mu, adapter_scalar ogden.lateral.min_log_slope_over_mu.v1=min over frozen control roots | LOWER1e−6/scale1/tol0 |

INC는 bulk/lateral을 제외한 acoustic2개만 mandatory다. Equality는 inclusive이다. finite negative acoustic/slope 또는 finite discrepancy 위반은 finite fit_loss+INFEASIBLE, diagnostic STABILITY_ENVELOPE_FAILED다. required scalar missing/nonfinite/계산실패는 CONSTRAINT_RESPONSE_INVALID,objective.eligible=false,fit_loss=null,constraint_status=NOT_ASSESSED다. min-eigenvalue arrays는 artifact에만 둔다.

Fixture stress scale2MPa, normalized trapezoid in λ/J, equal experiment weights다. scaled residual Jacobian의 singular ratio cutoff1e−6/condition>1e6 경고를 사용한다. 여러 항수는 separate model revisions와 train/validation 선택 후 untouched holdout으로만 판단한다. μ1=1,α1=2,λ2에서 INC nominal P1=1.75(uniax),1.96875(biax),1.875(planar)MPa, uniax Cauchy3.5MPa; COMP K100/J1.03 hydroσ=(3,3,3)MPa. 산술 tolerance1e−11MPa, objectivity σ(RF)=Rσ(F)Rᵀ tolerance1e−9, source gradient/Hessian finite-difference abs1e−6+rel1e−6. 실제 재료 fit limit가 아니다.

## B3. B-VIS-TRS-1: generalized Maxwell + 온도이동

Scalar small-strain linear viscoelasticity다. finite-strain rubber-viscoelasticity로 확대하지 않는다.
Ei=E0 ai, E∞=E0(1−Σai);
E(t)=E∞+ΣEi exp(−t/τi).
E0 MPa는 순간 modulus, τi seconds, ai dimensionless. Fresh undeformed q_i=0. 이상 strain jump는 exact qi+=EiΔε, 실제 finite ramp는 실제duration이다. Raw stress relaxation→modulus 변환은 imposed small strain/정규화/불확도 recipe를 기록한다.

ε=Re(ε0 exp(iωt)),σ=Re(E*ε0 exp(iωt)),E*=E′+iE″. 양의 E″는 stress-leading-strain이다.
E′=E∞+ΣEi(ωτi)²/[1+(ωτi)²],
E″=ΣEi ωτi/[1+(ωτi)²].
δ=atan2(E″,E′); amplitude/phase는 E′=|E*|cosδ,E″=|E*|sinδ, degree→rad π/180. exp(−iωt) 자료는 imaginary sign 변환 recipe 필수. ω=2π f_Hz를 정확히한번 적용한다.

실제 pyvisco.prony.E_relax_norm(time,ai,taui)*E0와 E_freq_norm(omega,ai,taui)*E0를 사용한다. 후자는 [all storage,all loss] concatenate이므로 sample count로 정확히 분리한다. pyvisco.fit_* 내부 optimizer는 사용하지 않고 기존 Workbench optimizer가 fit을 소유한다. Actual2.1.3 loaded prony.py/shift.py bytes는 pinned source hashes와 일치했으며 API oracle도 통과했다.

TRS horizontal-only:
τi(T)=aT τi(ref), ξ(t)=∫0^t ds/aT(T(s));
E(t,Tconst)=Eref(t/aT),E*(ω,T)=Eref*(aTω).
Vertical modulus shift는 없다.
WLF log10 aT=−C1(T−Tref)/(C2+T−Tref); pyvisco.shift.WLF는 aT가 아니라 log10 aT를 반환한다.
Arrhenius ln aT=(Q/R)(1/T−1/Tref),R=8.31446261815324 J/(mol*K).
Tref=293.15K fixed; Q>0이면 warmer→aT<1. WLF/Arrhenius는 서로 다른 model revision이며 도중 categorical switch하지 않는다.

Piecewise-linear strain/piecewise-constant T에서 r=Δt/(aTτi):
qi,new=exp(−r)qi,old+EiΔε*(1−exp(−r))/r;
stress=E∞ε+Σqi.
expm1와 r→0 limiting factor1을 쓴다. Temperature ramp는 midpoint constant-T substeps로 halving하여 모든 observed stress 차이≤1e−7MPa+1e−5|σ|,max16 subdivisions/interval을 요구한다. 실패는 INTEGRATION_FAILED/TEMPERATURE_QUADRATURE_FAILED. Accepted substep state를 유지한다. 중복시간은 명시 jump segment일 때만 허용하고 np.interp에 무방비로 넘기지 않는다. Rate/T jump는 forced segment boundary다.

Parameter/domain 선택을 고정한다:
E0[10,1000]MPa log; ai physical linear [1e−8,.95/n],Σai≤.95;
τ physical log bands n1:[.001,1000]s, n2:[.001,.3]/[1,1000], n3:[.001,.03]/[.1,3]/[10,1000].
이 conservative boxes는 positive equilibrium/order/ratio≥3을 보장하고 validator가 재확인한다. simplex/softmax/후보 clipping은 사용하지 않는다. Spectrum domain밖은 새 profile revision이다.
WLF C1[.1,20],C2[50,300]K, declared T∈[293.15,323.15]K, domain 전체 denominator≥20K. 범위밖 SHIFT_RANGE_EXCEEDED, pole WLF_SINGULAR_DENOMINATOR.
Arrhenius parallel recipe Q[1000,200000]J/mol, 같은 T domain/τwindow.
TRS가 Tg 부근에서 항상 유효하다는 주장은 없고 실제 shifted spectra/온도 holdout adequacy로 판정한다.

Reference E0=100,ai=(.2,.3),τ=(.1,10)s,C1=5,C2=100,Tref293.15K.
DMA f=.001..1000Hz log61samples at train293.15/303.15/313.15,validation318.15,holdout323.15K.
Relaxation t=.001..1000s log61samples at train293.15/313.15,holdout323.15K.
전체 T/domain test별 split이며 specimen replicate group을 섞지 않는다. Scale100MPa, DMA storage/loss equal .5 weight, equal experiment weights, normalized trapezoid in log10 coordinate를 frozen quadrature로 저장한다.
n1/2/3은 separate studies로 train, score=L_validation+1e−4*n, min→lower n exact tie로 선택한다. Holdout으로 n/shift type/범위/normalizer를 선택하지 않는다.
기존 SLSQP seed17,max1500eval,max200iter,ftol1e−10,FDstep1e−6. 초기값은 declared reference에서 E0×.9,ai×.9,τ1×2/τ2×.8,C1×.8/C2×1.1로 정하고 초기점만 bounds에 맞춘다. Fixed-parameter 검증은 zero-dimension optimize를 호출하지 않는다.

Tref-only는 C1/C2 sensitivity0다. 관측window밖 τ/미관측 short-time E0도 약식별이다. Scaled Jacobian SVD1e−6/condition1e6, τ/Tref gauge와 overlapping reduced time/frequency를 점검한다. 비식별 fixing은 새 사용자-visible revision에서만 한다.
Oracles: E0=100,E∞50,E(1)=77.14603053967404MPa;
ω1rad/s의 (E′,E″)=(79.9009900990099,4.9504950495049505)MPa;
aT313.15=.14677992676220694.
Tref에서 fresh .01 strain/1s ramp qi=(.01999909200140475,.2854877458921213),σ=.805486837893526MPa.
Tolerance1e−10MPa,aT1e−13. Endpoint limits loss→0,storage low50/high100,passivity를 검사한다.
Synthetic independent SciPy2term recovery target coeff abs1e−6/loss1e−18; 제품 SLSQP target loss1e−10/physicalcoeff1e−3은 다른 numerical acceptance다.

## B4. B-CYC-AF2-1: actual NEML Chaboche/Voce RI

Small-strain/isothermal293.15K/rate-independent다. 다른 vp_flow.ChabocheFlowRule과 혼용하지 않는다.
q=√(3/2)||devσ−ΣXi||,
f=q−[sy+Q(1−exp(−bp))]≤0;
dεp=(3/2)(devσ−ΣX)/q dp,dp≥0,dp*f=0;
dXi=(2/3)Ci dεp−γi Xi dp;
σ=Cel:(ε−εp).
State는 εp tensor,p,각 deviatoric Xi와 opaque library bookkeeping/energy다. NEML source는 devσ+ΣXnative, negative isotropic q와 **negative internal backstress**를 사용한다. Xphysical=−Xnative를 한 번만 변환한다. Uniax effective scalar Xaxial=3/2X11이고 dXaxial=Ci sign(dεp_axial)dp−γi Xaxial dp다.

정확 binding:
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
model.update_sd(e_np1,e_n,T_np1,T_n,t_np1,t_n,s_n,h_n,u_n,p_n) returns(s_np1,h_np1,A_np1,u_np1,p_np1). Last p_n is dissipated energy bookkeeping, **not** accumulated equivalent plastic strain. Mandel order [xx,yy,zz,√2yz,√2xz,√2xy] 변환을 명시한다. 첫 capability는 uniax normal-only; general torsion/mixed driver는 conversion/driver검증 전 unsupported다.

Uniax strain control은 d.erate_einc_step([1,0,0,0,0,0],signed_erate,signed_einc,T)로 횡응력0을 푼다. strain_step([εx,0,0,...])는 uniax strain이라 대체 불가다. Ratchet은 d.stress_step([σx,0,0,0,0,0],t,T). Collector는 actual time,d.stress[:,0],d.strain[:,0],transverse-zero residual을 요구한다. ep_axial=εx−σx/E는 이 exact uniax/isothermal만의 derived observable이다. model.report_internal_variable_names()와 opaque states를 보존하고 검증 없는 positional p/X index를 비교채널로 쓰지 않는다. Reversal 내 reset금지, 독립test/candidate마다 새model+driver다.

E200000MPa/nu.3 fixed. Reference sy250,Q50MPa,b10,C=(20000,5000)MPa,γ=(200,20),A=(0,0),a=(1,1),nonisoFalse.
Bounds sy[150,400],Q[0,150]MPa linear,b[1,100]log,C1[1000,50000]/C2[100,20000]MPa log,γ1[100,500]/γ2[1,90]log. C/γ/b>0,Q≥0,ordering avoids exchange. Static/temperature recovery/viscoplastic extension은 별도 formulation과 rate/T시험을 요구한다.

Train low path0→.002→−.002→.002→−.002→.002 and high0→.006→−.006→.006→−.006→.006,|rate|.001/s,maxΔε1e−5,exactcorners.
Validation amplitude.004 같은cycle.
Holdout stress0→350→−100→350→−100→350→−100→350MPa,step≤.5MPa,|rate|100MPa/s; observed is strain/mean drift, endpoint prescribed stress만 비교하지 않는다.
Train stress scale400MPa,holdoutstrain scale.01,equaltestweights,time quadrature.
DE(seed17,pop15,max3000eval/200iter),differentiable=false,no surrogate.
Bauschinger onset/loop shape/cyclichardening/ratchet는 별개 observables이며 monotonic fit만으로 γ나ratchet적합을 승인하지 않는다.

Library solve100iterations,위 tolerances; deterministic subdivision halving,max16levels,originalcomparison grid를 유지한다. h1e−5→h/2의 allsample stress 차이≤.1MPa,loopwork≤.5%; ratchet step.5→.25MPa endpointstrain≤1e−5/driftsign일치. Nonfinite/equilibriumfailure는 INTEGRATION_FAILED,stress saturation밖 no equilibrium은 STRESS_CONTROL_NO_EQUILIBRIUM. 이전stress복제/실패sample누락금지.
Scaled Jacobian7freeparams SVD1e−6/condition1e6; Q0에서b, equalγ에서partition,smallp에서C/γ 비식별을 보고한다.

MIT NEML은 compiled C++/CMake/BLAS/LAPACK/ABI와 bundled notices가 필요하다. setup.py Windows branch 존재는 Windows 설치 검증이 아니다. Optional backend이며 부재는 이 profile만 BACKEND_UNAVAILABLE이다. Source-build recipe: pinned checkout검증→documented nativeprerequisites→pip wheel --no-deps .→compiler/BLAS/PythonABI/source-tree/wheelSHA buildreceipt→isolatedinstall. PyPIversion1.5.4나 NEML_SOURCE_SHA 환경문자열은 source correspondence 증명이 아니다. Runner는 numericalpass여도 actualloadedextensionhash와 source_correspondence=unverified/exit21로 남기며 production receipt/hash verifier가 별도 gate다. 이번 실제 install은 BLAS_LIBRARIES 부족으로 실패, actualtensorlibrary미실행/exit20이었다.

Independent scalar oracles: pureAF sy250,C10000,γ100,Q0 atp.001 continuousX=9.516258196404042MPa,reverseonset−240.48374180359596MPa.
Selected2term BE highcycle finalσ=355.32878792738677(h1e−5),355.3480599878022(h5e−6)MPa; stressholdout finalε=.007664366981880675(step.5MPa).
Actual NEML runner vs same BE grid target maxσerror.002MPa,maxεerror1e−7,transversestress1e−6MPa. Continuous AF exacttolerance1e−10은 finite BE discretization equality와 다르다. Synthetic same-law scalar reference는 실제물성/actualNEML 합격이 아니다.

## B5. B-JC-STATE-1: flow와 local fracture initiation

선정 lane은 measured plastic-state→flow stress 및 local initiation의 정확 analytic peer다. Global force/displacement→FE stress update,elastic/plastic integration,coupled heating,continuous degradation,element deletion,mesh-objectivepostpeak를 제공한다고 주장하지 않는다. 그런 요청은 UNSUPPORTED_OBSERVABLE/EVALUATION_LEVEL 또는 UNSUPPORTED_REGULARIZATION이다.

p≥0 accumulated equivalent plastic strain,r=dp/dt≥0(1/s),η=mean(Cauchyσ)/q tensionpositive,q=vonMises>0,θ=(T−Tr)/(Tm−Tr).
Selected source branch IFORM0,FISOKIN0,uncappedYMAX,no ratefilter, prescribedT.
Tr293.15K,Tm1293.15K,r0=1/s fixed. θ∈[0,.9] 지원; fixtureθ≤.7.
σflow=(A+B p^n)*(1+C ln(max(1,r/r0)))*(1−θ^m).
εf=max(εmin,[D1+D2exp(D3η)]*[1+D4ln(max(1,r/r0))]*[1+D5θ]).
Dnew=min(1,Dold+Δp/εf); εmin1e−6. Constantη/r/T interval은 exact crossing으로 pcritical/time을 찾는다. Δp0이면 D불변; Δp>0 and r=0 inconsistency는 거부. q≈0의 positiveΔp는 TRIAXIALITY_UNDEFINED이며 η0으로 채우지 않는다.
Source failure의 EPSP는 rate,DPLA는 plasticincrement다. Source P=+meanσ/exponent+D3η이며 압축양수압력 문헌의D3를 그대로 쓰면 안 된다.

D는 initiation indicator다. Flow에 (1−D)를 곱하지 않는다. NativeISOLID1 threshold의 OFF=.8/후속decay,다른flag의deviatoric vanish/압축보존,XFEM/shellNIP/LAW2εmax는 별도 native동작이며 analyticD와 동일한 stressdegradation이 아니다.
Variableη/r/T는 source-style right-endpoint D accumulation에 midpoint/halving convergence≤1e−5D 또는1e−4pcritical,max16splits를 요구한다. NonconvergenceINTEGRATION_FAILED. εminfloor 활성은 ACTIVE_FAILURE_FLOOR와 sensitivityloss보고. Specimen마다fresh,preload는 같은specimen내유지.

Reference flow A300/B400MPa,n.5,C.02,m1;bounds A[100,600]linear,B[10,1000]log,n[.1,1],C[0,.1],m[.2,3].
Separatefailure D=(.1,.2,−1,.01,.2);bounds D1[.01,.5],D2[.01,1],D3[−5,−.01],D4[0,.1],D5[0,1],linear.
r0/Tr/Tm/εmin fixed. Declaredtestdomain p[0,.2],r[1,1000]/s,θ[0,.7],η[−1/3,2/3],positiveflow/εf.
Flow/failure는 separate campaigns이며 native inverseanalysis에서flow변경시 기존η를 불변 measured값처럼 재사용하지 않는다.

Flow p0.. .2step.01,train(r,T)=(1,293.15),(10,293.15),(1,793.15),validation(100,493.15),holdout(1000,993.15). ObservedflowCauchy; p/r/T는 measured covariates다. Failure는 localp_atfailure atη=−1/3,0,1/3,2/3와 같은T/r split이다. Variable-state시험은 D integral로 비교하고 constantηεf shortcut금지. Force/displacement/ruptureelongation뿐이면 DATA_RECIPE_INCOMPATIBLE/LOCAL_PLASTIC_STATE_REQUIRED. Necking후 volumeassumption으로 log(1+εeng)−σ/E를 자동 localplasticstrain으로 만들지 않는다. Local/current strain/stress/temperature/rate 또는 independentverifiedFE-inference가 필요하다.

Flow scale500MPa,fracturep scale.5,equalexperimentweight. SLSQP seed17,max1500eval/200iter,ftol1e−10,FD1e−6,startreference×.9 withinbounds,no surrogate.
At reference rate/T,C,m sensitivities0(flowrank≤3/5). Atoneη,D1/D2/D3의 singlecombinationrank≤1,D4/D5 reference sensitivity0. ScaledSVD1e−6/condition1e6,holdoutnottraining.
Oracles p.1/r1/θ0 flow426.4911064067352MPa;η0/r1/θ0 εf.3;freshΔp.15→D.5,p.3→initiation. θ.5,m1 halvesflow;r100 multiplier1+.02ln100. Tolerances1e−10MPa,1e−12strain/D;constantstateincrementsplitting invariant1e−12.

Localpointinitiation has **no** element length,localizationbandwidth or Gf. supports_fracture_energy=false. Never invent εf=Gf/(σh). Nativeinitiation-only promotion must freeze element/IP/meshcharacteristiclengthdefinition/specimenmesh and compareh,h/2,h/4 force/energy/initiation changes;≤5% is only an explicit declared adequacy target. Postpeakenergy needs a different exact traction/separation or crack-bandlaw and solverlengthdefinition, not registrymetadata. No unresolved regularizer choice is hidden within this capability.
Nativefamilyprovenance=/MAT/LAW2(PLAS_JOHNS)+/FAIL/JOHNSON; reader widths/defaults/element/thermal/deletion options/exactcodec/extractor remain unsupported by this analyticprofile. Promotion requires exactIP p/rate/Tstar/fullCauchy/D and realtimes/unit/sign/source correspondence, not averagedanimation/RPpixels. BsourceSHA is not Dreleasebinaryidentity.

## B6. Recorded execution, reproduction and remaining acceptance

Before environment loss the author executed:
1. reference_probe.py: HYP3INCmodes+hydro+finiteKlateralroot+energygradient/objectivity; Prony time/DMA/WLF/ramp and2termSciPyrecovery; independentCYCscalarBE reversal/low/high/validation/ratchet/refinement; JCflow/failure; K/WLF/JC rankcounterexamples.
2. run_pyvisco.py: actual2.1.3 APIs and exactloadedsourcehash match. Prony sourceSHA256=641490da54f1d62d5f631c899f3f0ade44d1f9debc98a9e9068fe4f0577795bb;shift=93f143f9789e31aaffebc229fe76b0c3b37ffa0b4937d5b2980b1d524e2503a2.
3. Existing pinnedWorkbench optimize + actualpyvisco5free2term spectrum on syntheticrelaxation: CONVERGED146dispatchedobservations,fit_loss1.4321823375482084e−13; E0=99.99991157325094,a=(.19999927747118482,.3000001346797645),τ=(.10000070016113262,9.99998403748328)s. WLFfixed,untouched323.15Kholdoutloss3.2911807678520303e−14. Exactreferenceconfigftol1e−12/FD1e−7,notproductmaterialservice/GUI.
4. ActualexistingConstraintSpec/assess_constraints: higherrorstable fit.02/FEASIBLE; finiteinstability fit.02/INFEASIBLE; invalidrequiredscalar null/NOTASSESSED.
5. COMP243cases/allrootsfailurecases as above.
6. Coldcopy relocatedbundle/core with Pythonauditdenialofbothoriginaldirectories: reference/equilibrium/actualpyvisco/existingoptimizer/constraints/assetintegrityexit0; NEMLexit20 separatelyunmet. Loadedcriticalcorepaths pointedonlytocopiedcore. Beforeloss30frozenassets/19numericexpectedchecks passed. OriginalR2receipts were preserved. These historicalresults are not regenerated/rawreceiptbyte recovery.

Portability uses explicit --workbench-root, never parent/workbench inference. workbench_source.py prechecks baseline104cdd6888a5554eecc8cf965c60ff1bd980b6da critical7files, then checksactualimportorigin+SHA again. Criticalpaths are caelab/__init__.py,mdo/__init__.py,variables.py,optimize.py,objectives.py,constraints.py,pareto.py. Docs-onlyHEAD maydifferwhilethesecodebytesmatch. This doesnotauthenticatealloptionalpackages. Actualloadedpyviscohashes areseparate.

Use user-selected Python3.12 optionalvenv with NumPy2.3.5,SciPy1.17.0,pyvisco2.1.3,filelock4.2.10; installpyvisco'sdeclareddependenciesvia pip. Historical/tmpauditpaths are notinstallinstructions. Bbundleandcoremayliveanywhere. Example:
~~~text
<env-python> <B>/reference_probe.py
<env-python> <B>/equilibrium_probe.py
<env-python> <B>/run_pyvisco.py
<env-python> <B>/optimizer_probe.py --workbench-root <explicit checked core>
<env-python> <B>/constraint_probe.py --workbench-root <explicit checked core>
<env-python> <B>/check_assets.py
<env-python> <B>/run_neml.py
~~~
NEML20=BACKEND_UNAVAILABLE,21=numericpass/sourceunverified; neither is pinnedbackendpass. CMake/BLAS/PythonABI/sourcebuildreceipt/loadedextensions must be separatelyverified. Windowsinstall/UI/provider/nativecalibration/realmaterialadequacy remain unexecuted byB.

Implementation acceptance: implement fouradapterfiles and tests/test_material_ogden.py,prony_trs.py,neml_chaboche.py,jc_state.py with ownfixturepaths; strictCSV→ExperimentSpec→plan→actualresponse→PredictionBundleV1→hostresidual/constraints→assessment→save/reopen→export, plus unit/measure/coverage/cancel/reset/failurecases. Sharedregistry/units/service/GUI remaintheirassignedowners. Use proposedindependentMaterialStudywindow andexistingWorkbenchconnectionpatterns,notanexistingimplementedmaterialwindow. Outside-repositorymeasureddata andrealWindowsUI areindependentgates. ActualtensorNEML isrequiredtoverifythislibrarybackend; itsabsencecannotbeskippedaspass.

A futurelaw enterswithoutcoreswitches onlyafterexactformulation/state/params/units/domain,licensedpinnedimplementation/API/nativeoptions,finiteexperiment/datarecipe,observables/extractor,physicalconstraints/identifiability andindependentnumerical/failureoracles are supplied. Registration=invocationonly; losslesshistory/namedlawcomputation/realuserresearch areseparategates. Originalnativefileexecutionremainsindependent. Syntheticfits/sourceinspection/plancompletionmustnotbeinflatedintoproductruntimeorengineeringapproval.
