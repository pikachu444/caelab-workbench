# A. 압력 의존 소성·압축성 폼·방향성 종이 재료의 구현 명세

이 문서는 **앞으로 구현할 제품 기능의 명세**다. 압력 의존 소성의 Drucker–Prager(DP), Xia–Boyce–Parks(XBP) 계열 등을 다룬다. 일부 수식과 LAW33 계산은 실제 Linux 참조 실행으로 확인했지만, 새 재료 서비스, 일반 XBP 구성방정식 갱신, GEOS 실제 실행, Windows 제품 인수 검증은 완료되지 않았다. 각 계산법의 정의, 실제로 실행한 범위, 제품에 아직 연결하지 않은 범위를 구분해 읽어야 한다.

**복구 상태:** 실행 환경을 사용할 수 없게 된 뒤 새로 재구성한 명세다. 원래 최종본의 SHA256 `5d913c95bc5a0f8bfd9deeb6faf5dda690b8cd246e1d456f478ea4922b97f5ff`와 바이트가 같다고 주장하지 않는다. 작성 당시 R3에서 선택한 법칙·수식·코드 식별자·공개 인터페이스·수치 절차·실제 참조 결과·제한을 보존한다. 원래 솔버 고유 형식(네이티브)의 시간 이력(TH), CSV, 로그의 원시 바이트는 추측해 복원하지 않았다. 인계를 승인하려면 이 복구판 자체를 검토해야 한다.

## 1. 사용할 계산 프로필과 확인한 출처

프로필은 사용할 식과 지원 범위를 고정하는 식별자다. 아래에 등록했다고 해서 제품 구현이나 실물 재료 검증이 끝났다는 뜻은 아니다.

| 프로필 ID, 개정 1 | 계산 방법 | 구현할 모델의 범위 |
|---|---|---|
| geos_dp_axisym_linear_v1 | 기존 GEOS의 DruckerPrager와 TriaxialDriver 사용 | Linux 호스트의 직렬 실행. 소변형·압축 축대칭 경로의 매끄러운 원뿔 면만 지원하며, 마찰각·팽창각은 일정하고 점착력은 선형 경화함 |
| dp_axisym_closed_v1 | 같은 분기에 대한 정확한 해석식 계산 | Windows에서도 가능한 재료점 계산. 대각 변형률 제어와 정확한 혼합 삼축 제어를 지원하며 전체 유한요소(FE) 솔버는 제공하지 않음 |
| oc_foam33_ka0_v1 | 기존 OpenCourant LAW33의 Ka=0 사용 | 주응력별 압축 한계와 탄성 제하를 갖는 압축성 폼. 골격 응답은 속도 비의존 |
| oc_foam33_ka1_v1 | 기존 OpenCourant LAW33의 Ka=1 사용 | 소스에 정의된 속도 의존 탄성계수·점탄성 골격의 상미분방정식(ODE)과 주응력 제한을 사용 |
| ortho_plane_stress_elastic_v1 | 정확한 직교이방성 컴플라이언스 계산 | 기계 방향(MD)·그 직각 방향(CD)의 평면응력만 지원 |
| gmn12_xbp_power_plane_stress_v1 | 새 재료점 구성방정식 갱신 구현 예정 | GMN2012에서 유도한 축소 XBP의 거듭제곱 경화·텐서 노름 변형. 평면응력 단조 하중과 탄성 제하를 지원하도록 정의 |

**A-GEOS:** GEOS-DEV/GEOS 개정 `d81a4438f107446f17e33ab70c70358935e473ab`, 라이선스 LGPL-2.1-only. 아래 경로는 해당 저장소 기준이다.

- src/coreComponents/constitutive/solid/DruckerPrager.hpp, git blob 02e2a44fcf7ad2c53541533dcc126d9f53961cb6.
- src/coreComponents/constitutive/solid/DruckerPrager.cpp, blob a64ae00030cbd75a322b30ae66591d1c0521c9a9.
- src/coreComponents/constitutiveDrivers/solid/TriaxialDriver.cpp, blob d266ae01a44380501e6fedcb5dda2339d2f1d8fd.
- src/coreComponents/constitutive/docs/solid/DruckerPrager.rst, blob dcb4ccafe49e5165912f4558288e81ff31d55754.
- inputFiles/constitutiveDriver/triaxialDriver_DruckerPrager.xml, blob dc244cae36460669bf436982ed00083b637f1e76.
- inputFiles/constitutiveDriver/triaxialDriver_base.xml, blob f93a592f81d6cf5256b5aa6e96edaf1326efcb5c.
- LICENSE, blob 8c177f8bedc4a734d40312bb165035be1b22fad4.

**A-OC:** OpenCourant/OpenCourant 개정 `cb760c206e346eff12161a2268b134ef9d48a4ef`. 소스 헤더의 라이선스는 AGPL-3.0-or-later다. 외부 실행 파일을 등록하는 것만으로 그 소스가 제품에 링크되는 것은 아니다.

- engine/source/materials/mat/mat033/sigeps33.F, blob 62b137d2f62a5b101aced76bdf9d69c41cc33092.
- starter/source/materials/mat/mat033/hm_read_mat33.F, blob 84132a6b22bbd439ff0aec6e5bf427a22ea1f560.
- engine/source/input/freform.F:576-595, blob 79abd582b3bb4ad0ef363a2df68d129f0c4318ab. DTIX 읽기와 기본값을 확인했다.
- Radioss2022 형식 문서인 CFG matl33_foam_plas.cfg, blob 154a244ca95482ee14f3a30a856055207f350c52는 형식 확인에만 사용했다. 이 문서에 별도로 적용되는 제한은 제품으로 복사할 권한을 부여하지 않는다.
- LAW102의 Engine blob 85fc54763fb355614073ee9e45a6478dbe46ee66과 Starter blob d6f8541268c950a9c88d6b6bd851c7220547f66a도 읽었다. LAW102는 압력 의존 한계에서 편차응력을 제한하므로, 여기서 선택한 팽창·경화 GEOS 법칙을 대신하지 않는다.

**A-XBP02:** Xia Q.S., Boyce M.C., Parks D.M. (2002), International Journal of Solids and Structures 39,4053-4071, DOI 10.1016/S0020-7683(02)00238-X. 서지 정보는 확인했지만 원 논문 전문과 공개 라이선스로 재사용할 수 있는 원 사용자 재료 서브루틴(UMAT)은 확보하지 못했다. 원래의 27개 파라미터·tanh형 전체 XBP는 구현하지 않았고, 그 식을 검토했다고도 주장하지 않는다.

**A-GMN12:** Garbowski, Maier, Novati (2012), Structural and Multidisciplinary Optimization 46,111-128, DOI 10.1007/s00158-011-0747-3. 공개 논문 https://link.springer.com/article/10.1007/s00158-011-0747-3 의 3.1–3.3절과 식 1–9를 읽었다. 논문에는 축소한 거듭제곱 경화 XBP와 Abaqus6.9 서브루틴이 설명돼 있지만, 공개 라이선스 UMAT는 제공되지 않는다. 여기서는 독립 구현을 정의하고 소성 변형률 텐서의 노름 규약을 명시적으로 고정한다. 논문의 가상 실험은 독립적인 실물 재료 검증이 아니다. Hill LAW32/43이나 직물용 LAW19/58을 Xia 모델로 부르지 않는다.

OpenSees DP도 조사했지만 COPYRIGHT가 사용·배포를 제한하므로 기본 재배포 백엔드로 선택하지 않았다. GEOS/OpenCourant를 외부 배포할 때는 각각의 고지와 제3자 라이브러리(TPL) 조건을 지켜야 한다. 제품 설치 프로그램에 두 프로그램을 반드시 동봉할 필요는 없다.

## 2. 연구 결과를 주고받는 공통 계약과 판정

사용자는 시험자료를 불러와 계산·보정하고, 잔차와 별도 검증시험을 확인한 뒤 연구를 저장하거나 내보낼 수 있어야 한다. 이 흐름에는 통합 명세의 단일 자료형인 ArtifactRefV1, ResultRefV1, MaterialStudyV1, ModelDescriptorV1, ExperimentSpec, ComparisonSpec, EvaluationPlan, PredictionBundle, CalibrationEvaluationArtifact, AssessmentResultV1을 사용한다. 별도의 범용 FE 스키마, 결과 전달 자료형(DTO), 데이터베이스, 솔버 큐, 최적화기를 만들지 않는다.

구현 예정인 MaterialAdapter의 메서드는 `capabilities(model,environment)`, `validate(model,parameters,experiments)`, `plan(request)`, `collect(plan,artifacts)`, `export(request)`다. A의 예정 모듈은 materials/adapters/geos_dp.py, dp_axisym_closed.py, opencourant_foam33.py, orthotropic_plane.py, gmn12_xbp.py다. 같은 입력에서 같은 결과를 내는 이력 추출은 materials/native_history.py에 등록한다. 프로필별 식과 제약은 어댑터가 소유하며 서비스·화면 코드에 모델명 분기를 넣지 않는다.

재료 CSV, 드라이버 출력, 파라미터 설정, 보고서의 소유자는 `material-study:<study_id>`인 소비자다. 실제 네이티브 실행 출력은 기존 run 소유를 유지한다. GEOS 드라이버의 텍스트 출력은 재료시험 산출물로 보존하며 FE 필드 ResultRef로 꾸미지 않는다. 필수 TH 관측량은 D가 등록한 단일 이력 추출기와 DatasetManifest의 `dataset_kind=history/series` 계약을 따른다. RP의 필드 표시만으로 보정 근거가 확보되지는 않는다.

외부 실행은 기존 JobManager/RunStore가 담당한다. 후보 파라미터와 독립 시험의 조합마다 초기 상태를 새로 만들고, 같은 시험의 순서 있는 하중·제하 구간에서는 상태를 유지한다. 지원하지 않는 시험 간 재시작은 추정하지 말고 거부한다. 변형률이 반전해도 시간은 엄격히 증가해야 한다. 변형률 기준 재정렬이나 가상의 t=0 행 삽입은 금지한다. 원시 CSV 바이트와 변환 절차를 변경 불가능한 형태로 보존한다.

CSV에는 time_s, 모든 제어량·관측량, 명시 단위와 측도, 부호, 재료/전역 좌표계, 게이지·선택 영역, 예하중, 시편 두께·단면, 시험 속도, 온도·상대습도(RH)·함수율 기준, 반복 시편 그룹이 필요하다. 메타데이터가 없으면 EXPERIMENT_METADATA_MISSING, 측도가 틀리거나 불명확하면 MEASURE_MISMATCH다. 단위를 추측하지 않는다. 역학 단위는 공통 단위 계층을 사용하고 온도 변환은 중앙 materials/units.py에서 처리한다. 프로필마다 온도 오프셋을 따로 두지 않는다.

아래 C7/C1/C6은 과거에 합의한 결정 ID다. 현재 core에서는 각각 C2(개정), C4(비교·판정), C5(스칼라·제약)에 해당한다. 실제 필드·enum은 현재 core를 정본으로 따른다.

개정 관리는 **결정 C7**을 따른다. expected_revision은 변경 가능한 document_revision을 사용한다. MaterialEvaluationRequest.study_revision, 캐시 키, OptimizationProblem.revision은 변경 불가능한 content_revision을 사용한다. 진행률·색인·선택 상태를 저장해도 고정된 연구 내용은 바뀌지 않으며, 어댑터 출력은 그 내용 개정에 계속 귀속된다.

비교·판정은 **결정 C1**을 따른다. `required_for_fit=true`인 train 비교만 고정 목적함수에 넣는다. validation/holdout에서는 `required_for_validation=true`인 비교로 해당 자료 분할의 통과 여부를 정한다. 두 플래그가 모두 false인 진단 비교는 실패하더라도 분모를 바꾸지 않는다. 관측 격자의 보간 방법, 정규화한 사다리꼴 가중치, 양의 스케일, 채널별·시험별 가중치를 미리 고정한다. 외삽과 후보별 표본 범위 축소는 금지한다. holdout은 학습이나 스케일 결정에 쓰지 않는다. 목적함수와 판정 결과는 정본 필드에만 직렬화한다.

계산이 유효하면 오차가 커도 유한한 손실과 평가 자격을 유지한다. 필수 train의 실행·무결성·측도·자료 범위 확인이 실패하면 `objective.eligible=false`, `fit_loss=null`, `EvaluationObservation(valid=false,reason,responses={})`로 기록한다. 물리적 벌점을 꾸며 넣지 않는다. 유효한 holdout의 불합격은 train 손실을 지우지 않는다. 물리적 수용 한계가 없으면 not_assessed다. 최적화 수렴만으로 파라미터 식별성이나 모델 적합성을 인증하지 않는다.

기능 보고는 **결정 C6**을 따른다. 모든 ScalarResponseSpecV1에는 `schema_version=1`이 필요하다. 호스트가 소유하는 예약 응답 fit_loss는 `unit=""`인 무차원 값, `source_kind=residual`로 등록한다. 어댑터는 fit_loss를 PredictionBundle.optimizer_scalars에 넣지 않으며, 호스트가 잔차 산출물에서 계산한다. A 프로필의 지원 영역은 이미 인코딩돼 있으므로 `optimizer_constraints=()`를 사용한다. `differentiable=true`는 탄성 직교이방성에만 적용하며 DP/폼/XBP는 false다. 배열은 산출물로 보존한다. 별도의 스칼라 제약 필드를 만들지 않는다.

최적화 설정은 다음과 같이 고정한다.

| 대상 | 기존 최적화기에 전달할 설정 |
|---|---|
| 탄성 직교이방성 | 순차 최소제곱 계획법(SLSQP), seed=17, max_evaluations=400, max_iterations=120, ftol=1e-10, 스케일한 자유 좌표의 finite_difference_step=1e-5 |
| 비선형 DP/Ka0/Ka1/XBP | 차분진화(DE), seed=17, max_evaluations=1000, max_iterations=100, population_size=20, max_failures=10, wall_seconds=3600, ftol=1e-8, best1bin, mutation(.5,1), recombination=.7, polish=false, 대리모델 없음 |

기존 DE가 임시로 사용하는 무한대는 무효 탐색 후보의 순위에만 쓰고 측정 손실로 직렬화하지 않는다. SLSQP는 첫 무효 점에서 멈춘다. 실패로 종료하더라도 유효 후보 기록은 보존한다.

식별성은 필수 train 채널의 백색화 잔차 Jacobian으로 확인한다. 스케일한 유한차분과 특이값 분해(SVD)를 사용하며 행렬의 계수(rank), 즉 서로 독립인 정보의 수를 판정하는 기준은 `s_i/s_max>=1e-6`, 완전 계수(full rank)의 조건수 한계는 `<=1e6`이다. 미분 간격을 .5배/2배로 바꿔 다시 확인하고, 행렬의 계수가 불안정하면 `identifiability_status=not_computed`, NUMERICAL_SENSITIVITY_UNSTABLE로 기록한다. 식별성 보고가 필수 정책이면 이를 적합성 not_assessed로 연결한다. 상관계수>.995와 경계 접촉은 경고한다. 이 값은 해당 시험의 진단이며 인증된 불확도가 아니다.

내보내기는 새 바이트와 manifest를 만들며 열린 Prep 문서를 바꾸지 않는다. 해석식 DP와 파생 XBP의 파라미터·수식 보고서는 네이티브 솔버 카드가 아니다. 실제 공급자의 카드·곡선·방향을 한 트랜잭션으로 처리하는 입력파일 읽기·쓰기 형식 변환 코드(codec)가 자체 검사를 통과하기 전까지 A 프로필은 Prep Apply를 지원하지 않는다.

## 3. Windows의 해석식 DP와 Linux GEOS 실행 범위

Windows의 DP 재료 연구에는 GEOS/HPC/Linux 없이 `dp_axisym_closed_v1`을 사용한다. 선택한 네이티브 GEOS는 **Linux 호스트 x86_64 직렬 실행만** 지원한다. Windows 네이티브와 WSL 호출은 지원하지 않으며, 이때 `host can_run=false`, `CapabilityReport.supported=false`, `reason_codes=(UNSUPPORTED_HOST_TRANSPORT,)`이고 작업을 하나도 배정하지 않는다. WSL 후보를 자동으로 사용 가능하다고 표시하지 않는다.

Linux 설정에는 실제 로컬 GEOS 실행 파일의 식별 정보가 필요하다. 인자는 정확히 `[absolute_geos,"-i","case.xml"]`, 작업 디렉터리는 새 run 디렉터리, `OMP_NUM_THREADS=1`이다. 주 입력과 그 입력이 참조하는 모든 파일을 빠짐없이 모은 집합의 사본·해시는 기존 네이티브 작업 소유 규칙에 따라 보존한다. 기존 Linux jobs.py의 _native_exited는 waitid/WNOWAIT로 선두 PID를 보존하고, _finish_owned는 자신이 기록한 살아 있는 프로세스 그룹만 제어한다. 복구한 PID를 인수하거나 프로젝트를 다시 열 때 자동 실행하지 않는다.

E-05에서는 wsl.exe의 Windows Job Object를 닫아도 Linux 자손 프로세스 정리를 입증할 수 없음을 확인했다. 따라서 R2의 직접 WSL 제안은 철회했다. 감독 프로토콜을 검토했지만 구현하지 않았고, 새 원격 전송 계층을 선행 조건으로 삼는 대신 현재 지원을 Linux로 한정했다. 유효한 WSL 인자·감독 계약은 남아 있지 않다. 향후 WSL 백엔드는 별도 전송 계층을 구현·검토한 뒤 등록해야 한다. 현재 지원 범위에 관한 결정은 확정됐다.

향후 Linux 네이티브 검사에는 실행 파일·소스 식별, 설치 누락, 새 상태·후보 순서 반전, 공백·한글 경로, 사본·출력 변조, 계산·수집 중 취소와 프로세스 그룹 정리, 재열기 시 미실행을 포함한다. GEOS 실제 실행은 아직 하지 않았다. Windows의 미지원 기능 검사에서는 GEOS 작업을 전혀 배정하지 않으면서 해석식 DP 연구는 실행 가능해야 한다.

## 4. Drucker–Prager 식과 계산 절차

### 4.1 부호, 상태와 지원 영역

응력·변형률은 인장 양수다. `p=tr(sigma)/3`은 압축에서 음수이며 표시 압력은 `P=-p`다. `s=sigma-pI`, `q=sqrt(3/2*s:s)>=0`를 사용한다. SI 단위는 Pa,s,kg/m3다. Mohr–Coulomb(MC)의 압축 모서리 변환은 다음과 같다.

a0=6*c*cos(phi)/(3-sin(phi));
F=6*sin(phi)/(3-sin(phi));
D=6*sin(psi)/(3-sin(psi)).
항복함수 f=q+F*p-a, 소성 포텐셜 g=q+D*p, dep=dlambda*dg/dsigma, da=h*dlambda.

확정·저장한 상태는 응력 6성분(stress6)과 점착력 a다. 소성 증분 진단값은 초기 응력을 기준으로 전체 변형률·응력 이력에서 재구성한다. 조건은 K,G,c,density>0, h>=0, 0<=psi<=phi<60 deg, a>=0, dlambda>=0이다. 마찰각과 팽창각은 일정하며 이동경화는 없다. 인장 절단, 원뿔 꼭짓점(apex) 복귀, cap 압밀, 손상, 파괴에너지, 연화 파라미터는 이 프로필에 없다. 유효응력/전응력 기준은 시험과 일치해야 하며 간극수압을 조용히 빼지 않는다.

### 4.2 대각 변형률 제어와 혼합 제어의 해석식

대각 변형률 제어에서는 다음 정확한 해석식을 사용한다.

p_t=p_n+K*tr(de);
s_t=s_n+2G*dev(de);
q_t=sqrt(3/2*s_t:s_t);
f_t=q_t+F*p_t-a_n.

`f_t<=max(1e-8 Pa,1e-12*stress_scale)`이면 탄성이다. 그 밖에는 다음과 같이 갱신한다.

dlambda=f_t/(3G+K*F*D+h);
p=p_t-K*D*dlambda;
q=q_t-3G*dlambda;
a=a_n+h*dlambda;
s=(q/q_t)*s_t.

분모는 양수여야 하며 q_t>1e-12*stress_scale, q>=1e-10*stress_scale, p<0을 요구한다. 꼭짓점이나 인장 영역을 지나면 UNSUPPORTED_STRESS_PATH다. 음수 q를 잘라 가상의 cap을 만들지 않는다. 순수 정수압 압축은 탄성으로 처리하고 q로 나누는 계산을 건너뛴다.

혼합 제어의 압축 분기는 정확한 2×2 연립식으로 푼다. 정의는 `dr=target_radial-old_radial`, `A=F/3-1`, `B=1+2F/3`, `C=D/3-1`, `T=E*de_ax+2*nu*dr`다. 시험값은 `dsa=T`, `ftrial=A*(old_axial+T)+B*target_radial-aold`다. 탄성이면 dlambda=0, 소성이면 `dlambda=ftrial/(h+E*A*C)`, `dsa=T-E*C*dlambda`다. 이후 `de_rad=(-nu*dsa+(1-nu)*dr)/E+dlambda*(.5+D/3)`, `sa_new=sa_old+dsa`, `sr_new=target`, `a_new=aold+h*dlambda`로 계산한다. 마지막 상태의 p<0, q=sr-sa>=0, multiplier>=0을 검사한다.

수치 탐색 중 시험한 상태와 실제 요청 경로의 지원 여부를 혼동하지 않는다. 이전의 고정 횡변형률 구간 [-.1,.1]은 위쪽 끝점에서 p가 인장이 되어 첫 탄성 압축 증분조차 실패했다. 이 반례를 재현한 뒤 해당 구간 탐색을 제거했다.

### 4.3 GEOS 입력·출력과 유효성 검사

네이티브 XML은 Events/SoloEvent의 target을 /Tasks/driver로 지정한다. Constitutive/DruckerPrager는 name dp와 defaultDensity, defaultBulkModulus, defaultShearModulus, defaultCohesion, defaultFrictionAngle, defaultDilationAngle, defaultHardeningRate를 사용한다. Tasks/TriaxialDriver는 name driver, material dp, mode mixedControl, axialControl axial, radialControl radial, initialStress, steps=100, precision=16, output dp_native.txt다. Functions/TableFunction에는 time 좌표와 값을 둔다. FE 메시 변환은 하지 않는다.

고정 검증 입력은 K=1e9 Pa, G=6e8 Pa, c=1e6 Pa, h=5e7 Pa, phi=30 deg, psi=10 deg, rho=2700 kg/m3, initialStress=-2e6 Pa다. 축변형률은 1 s 동안 0->-.01로 변하고 횡응력은 -2e6 Pa로 일정하다. GEOS 출력 변형률의 0은 이 초기 응력을 기준으로 하므로 예하중의 탄성 변형률을 암묵적으로 더하지 않는다.

출력은 공백 구분 파일이며 정확히 아홉 열의 헤더 의미를 읽는다. 열은 `time`, `strain,axial`, `strain,radial_1`, `strain,radial_2`, `stress,axial`, `stress,radial_1`, `stress,radial_2`, `newton_iter`, `residual_norm`이다. 유한한 값이 완전하게 채워진 steps+1행, 증가하는 실제 시간, 두 횡방향 채널을 요구한다. `eps_v=ea+er1+er2`, `p=mean normal stress`이며 횡응력의 동일성을 확인한 뒤에만 `q=abs(sa-sr)`를 쓴다. 삼축 q는 편차·전단 응답의 근거이지만 순수 단순전단 제어 지원을 뜻하지 않는다. gamma12 제어 요청은 작업 배정 없이 UNSUPPORTED_OBSERVABLE로 거부한다. StrainControl에서 세 변형률을 같게 하면 정수압 시험, 횡변형률을 0으로 두면 압밀계(oedometer) 시험이다.

GEOS 소스의 국소 갱신은 20회 반복 뒤 최종 미수렴 예외를 던지지 않으며, 견고한 꼭짓점 분기가 없고 newQ/trialQ로 나눈다. 따라서 exit0만으로 성공을 판정할 수 없다. 모든 네이티브 행에서 실제 자료 범위·유한성·횡응력 잔차·driver residual<=1e-8·q>=0과 재구성한 소성 승수/점착력/항복 조건 `|f|<=1e-7 Pa+1e-6*stress_scale`을 확인한다. 드라이버가 일찍 실패한 뒤 남긴 행은 자료 범위를 충족하지 않는다. 변형률·응력 증분에서 재구성한 점착력은 연산 진단값으로 표시하며, 솔버가 출력한 내부 필드로 표시하지 않는다.

### 4.4 시험 설계와 독립 기준값

train에는 구속압 P=2 MPa/P=5 MPa의 배수 삼축 시험의 0->-.01->-.008->-.012 경로를 사용하며 축응력, 두 횡게이지, 체적, q, P를 비교한다. K에는 별도의 동일 변형률 정수압 압축 0->-.003, G에는 작은 변형의 편차 탄성 자료를 사용한다. holdout은 독립 시편의 P=3.5 MPa와 2배 느린 속도다. 구속압이 하나뿐이면 c/phi가 서로 혼동되므로 하나를 고정하거나 비식별로 보고한다. 횡변형률이 없으면 psi를 고정하고 팽창각을 동정했다고 주장하지 않는다.

시연 범위는 K∈[.5,2]GPa, G∈[.3,1.2]GPa, c∈[.5,2]MPa, phi∈[15,45]deg, h∈[20,100]MPa다. 자유변수 dilation_fraction[0,1]로 psi=fraction*phi를 연결한다. 탄성·소성을 단계적으로 동정한 뒤 민감도 행렬이 필요한 독립 정보를 제공할 때만 공동 보정한다.

일정 구속압의 독립 검증값은 다음과 같다.

| 항목 | 값 |
|---|---|
| 탄성·항복 파라미터 | E=1.5e9 Pa, nu=.25, F=1.2, D=.3686338897262739, a0=2078460.9690826528 Pa |
| 항복점 | ea=-.0049760677434251685, q=7464101.615137753 Pa |
| 소성 기울기 | 89348488.7169089 Pa |
| 마지막 상태 | sa=-9912982.369678844 Pa, er=.004674005556472009, eps_v=-.0006519888870559817 |
| 해석식 허용오차 | 응력: 1e-7 Pa+1e-10\|expected\|, 변형률: 1e-11 |
| 네이티브 전 행 허용오차 | 응력: 10 Pa+1e-5\|expected\|, 변형률: 1e-7 |

과거 독립 구간별 계산과 수정한 2×2 계산의 비교는 응력<1e-6 Pa/변형률<1e-13을 통과했다.

검사에는 팽창각 0, phi=psi=0의 J2 한계, h=0, 추가 구속압, 정수압 압축, E/nu에 따른 탄성 제하, 상태 격리, 행 누락, 잘못된 헤더·단위, 취소와 미지원 꼭짓점·인장·단순전단 경로를 포함한다. GEOS 네이티브 인수 검증은 아직 실행하지 않았다.

## 5. LAW33 압축성 폼의 식과 네이티브 연결

### 5.1 공통 압축 한계와 갱신 순서

이 프로필은 주응력별 압축 한계 법칙이며 타원형 Deshpande–Fleck 모델이 아니다. 현재 체적 변수는 `gamma=rho0/rho-1+gamma0=J-1+gamma0`이고 압축에서 gamma<0이다. 공기압은 `p_air=max(0,-P0*gamma/(1+gamma-Phi))`다. 분모>=.05, J>Phi+.05를 요구하며 잠김 영역으로 외삽하지 않는다.

소스의 계산 순서를 그대로 지켜야 한다. 먼저 **현재 밀도**로 p_air를 계산하고, 그 현재 p_air를 이전 전응력의 수직 성분에 더해 골격 시험응력을 만든다. 갱신·주응력 제한을 적용한 뒤 **같은 현재 p_air**를 뺀다. 이전 공기압을 저장해 더하는 방식이 아니다. P0가 0이 아닌 단일 갱신의 독립 기준 계산으로 두 순서를 구분한다. 다른 분할이 직관적으로 자연스러워 보여도 확인한 소스 순서를 유지한다.

강도는 `Y(gamma)=Fscale*curve(gamma)`이며 curve_id=0일 때는 `abs(A+B*(1+C*gamma))`다. Starter의 curve2/속도 배율은 숨겨져 있거나 비활성화돼 있으므로 공개된 속도 곡선 기능으로 지원하지 않는다.

Ka0에서는 골격 수직 시험응력에 E*de_ii, 전단 시험응력에 E*dgamma_ij/2를 더한다. 포아송비 0의 골격으로 G=E/2다. 현재 골격 응력을 대각화하고 각 주응력을 [-Y,+Y]로 제한한 뒤 원래 축으로 회전하고 p_air*I를 뺀다. 확정·저장한 응력으로 비가역 제하·재하 이력을 유지한다. 표의 가로축은 총체적 변수 gamma이며 축방향 소성 변형률이 아니다. Ka0/Ka1은 인장 절단 필드를 읽지만 선택한 소스 분기에서 적용하지 않는다. 별도 인장 절단 기능으로 표시하지 않으며, Ka2를 지원하려면 새 프로필 검증이 필요하다.

Ka1의 유효 탄성계수와 방정식은 다음과 같다.

Eeff=max(E,C1*max(abs(normal_rates),abs(engineering_shear_rates))+C2).

수직 응력의 ODE:

dot(sig_s)=Eeff*dot(eps)-(Eeff+Et)/eta_n*sig_s+Eeff*Et/eta_n*eps.

텐서 전단변형률 eps12=gamma12/2에 대한 ODE:

dot(tau)=Eeff*dot(eps12)-(Eeff+Et)/eta_s*tau+2*Eeff*Et/eta_s*eps12.

그 뒤 주응력 제한과 p_air 차감을 적용한다. 소스는 실제 각 시간 간격에 명시적 Euler를 사용한다. 상태에는 골격 응력과 총변형률이 들어간다. E/C2/Et의 단위는 응력, C1과 eta_n/s의 단위는 응력×시간이다. Maxwell 스펙트럼을 임의로 추가하지 않는다. 속도·유지·소산의 유효성은 선언한 경로에서 검사하며 전체 영역에서 성립한다고 가정하지 않는다.

### 5.2 재료 카드와 고정 검증 입력

독립 작성한 재료 카드의 배치는 다음과 같다.

- /MAT/LAW33/1 다음에 제목을 둔다.
- rho의 폭은 20열이다. 기준 밀도를 생략하면 rho0와 같다.
- E는 1–20열, Ka는 21–30열, curve_id는 31–40열, Fscale은 41–60열이다.
- P0/Phi/gamma0는 각각 20열이다.
- A/B/C/sigma_tcut은 각각 20열이다.
- Ka1에만 C1/C2/Et/eta_n/eta_s를 각각 20열로 쓴다.
- 곡선 모드는 고정 FUNCT101에 gamma/응력 쌍을 각각 20열로 쓴다. 가져올 때 gamma 절점을 한 번 증가 순서로 정리하되 시험 경로 순서와 구분한다. 모든 후보의 gamma 범위를 곡선이 포함해야 하며 외삽하지 않는다.

검증 입력의 단위계는 mm-Mg-s다.

| 적용 범위 | 파라미터 값 |
|---|---|
| 공통 | rho=1e-10 Mg/mm3=100 kg/m3, E=10 MPa, P0=0, Phi=.1, gamma0=0, A=.1 MPa, B=C=0, Fscale=1 |
| 읽지만 적용하지 않는 값 | tcut=100 MPa |
| Ka1 추가값 | C1=.1 MPa*s, C2=10 MPa, Et=2 MPa, eta_n=eta_s=.5 MPa*s |

유효 조건은 rho/E>0, 0<Phi<1, Et>=0, eta>0, C1/C2>=0이다. 곡선 모드에서는 사용하지 않는 A/B/C를 고정한다. 절댓값 법칙의 내부 값이 0을 가로지르는 파라미터 범위는 거부한다. 밀도/Phi/P0는 독립 근거가 필요하며 응력 자료만으로 질량 밀도를 추정하지 않는다.

네이티브 모델은 1 mm 정육면체다. 절점 1..8의 표준 순서는 (0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1)이며 brick=1, part=1, material=1이다. 속성은 Isolid=1, Ismstr=4, Iale=0이고, 균일한 아핀 변형 참조 모델에서는 인공점성·아워글라스 값을 명시적으로 0으로 둔다. 실제 Starter 검사로 유효값을 확인했다. 접촉·중력·감쇠·질량 스케일링은 없다.

모든 y/z를 구속하고 왼쪽 x는 BCS=0으로 고정한다. 오른쪽 절점 2,3,6,7의 속도는 IMPVEL1에서 function=1, direction=X, skew/sensor=0, group=2, Icoor=0, scales=1/1, start=0/stop=.05로 속도를 지정한다. IMPVEL 진폭 0은 기본값 1로 처리되므로 정지 평면은 amplitude=0 대신 BCS로 고정한다.

속도 절점은 (s,mm/s)로 (0,0),(.001,-1),(.019,-1),(.020,0),(.021,1),(.029,1),(.030,0),(.031,-1),(.039,-1),(.040,0),(.05,0)이다. 램프와 속도 적분 변위도 고정 절차의 일부다. 실제 절점 변형·체적을 읽으며 정확한 지수 신장 제어로 간주하지 않는다. 정수압 변형에서는 좌표가 0이 아닌 각 평면을 같은 경로로 제어하고 0인 평면을 고정한다. 다른 요소 정식화는 새 프로필 개정으로 다룬다.

### 5.3 시간 이력 추출과 비교량

D와 합의한 실제 출력 카드 계약은 다음과 같다.

- TH/BRIC/1, title=BRIC. 폭 10열 토큰은 SX SY SZ SXY SYZ SXZ DENS VOL IE BULK다. 요소 행은 10열 ID=1+10공백+label foam이다.
- TH/NODE/2, title=NODE. 폭 10열 토큰은 DX DY DZ VX VY VZ다. 각 절점 행은 10열 ID+10열 frame=0+label n<ID>다.
- Engine은 TFILE/4의 interval=.0005와 TH/TITLE을 사용한다.

변환기는 stress/IE/DENS/BULK/VOL의 순서를 바꿀 수 있다. 요청한 순번으로 읽지 말고 원시·정규화 헤더, ID·제목, 소스 TITLES를 함께 맞춘다. 정규화 키의 예는 `BRIC 1 foam SX`다. TIME은 소스의 실제 시간이다. 제품에서는 D가 등록한 짧은 기본 이름의 3인자 호출 `[absolute_exe,"inT01","history"]`을 사용하고 history.csv를 받는다. 과거 upstream의 1인자 실행 관측은 과거 기록으로 남기며 별도 제품 실행 경로로 사용하지 않는다.

Tools의 실제 소스 빌드 식별자는 `1b6bb9200ee7dca899e61c18bd982c3fb1c72fd1`이다. 개별 파일의 마지막 변경 개정과는 다르다. 공식 배포본에 포함된 바이너리는 별도 SHA로 식별하며, 고정하지 않은 배포 CI의 Tools 브랜치로 추정하지 않는다. C 변환기의 허가 고지는 솔버/RP의 AGPL과 별개다.

보정 채널은 단일 솔리드 적분점(IP)의 전역 Cauchy 응력 6성분, rho/VOL, 절점 ID/U/V, 시간이다. `J=rho0/rho`에는 질량 보존·무스케일링이 필요하며 VOL/V0 및 절점 det(F)와 1e-5 이내로 일치해야 한다. thsol 코드는 과거의 오해를 부르는 주석과 달리 rho0/rho로 Lagrangian VOL을 보정하므로 코드와 실제 자료를 따른다. 적분점 평균을 자동으로 하지 않는다. 힘은 명시한 경계 합력 또는 별도 검증 연산자로만 구하며 응력의 합으로 만들지 않는다.

필수 관측량을 지원하지 않으면 배정 전에 보정을 거부한다. 실행 뒤 필수 출력이 빠졌으면 COVERAGE_INCOMPLETE다. RP 표시를 수치의 정답으로 쓰지 않는다. Leapfrog의 VX는 반 시간 간격 위치에 있으므로 실제 시간 오프셋 없이 현재 TIME의 명령 속도와 비교하지 않는다.

### 5.4 보정 자료, 기준 계산과 수치 통과 조건

Ka0의 train은 정수압 신장비 lambda=1->.98->.99->.98과 단축 변형률 lambda_x=1->exp(-.02)->exp(-.01)->exp(-.02), 횡방향 신장비=1이다. 응력·체적이 필수다. P0=0이면 단축 변형률에서 횡방향 골격 응력은 0이다. holdout은 독립 구속 압축 J=.975와 제하다. 회복·속도·이력 자료가 이 법칙과 맞지 않으면 모델 적합성을 불합격으로 판단하며 최적화 실행 실패로 바꾸지 않는다.

Ka1의 train은 진변형률 속도 .01,.1,1/s에서 로그 압축 -.02, 2 s 유지, 같은 속도 제하이며 정수압·단축을 모두 포함한다. eta_s가 자유변수이면 순수 전단 gamma=.02와 2 s 유지가 필수이고, 자료가 없으면 고정/미식별로 처리한다. holdout은 속도 .05/s와 독립 제하다. C1/eta/Et를 구별하려면 과도 응답과 유지 응답이 모두 필요하다. 시연 범위는 E∈[5,20] MPa, Yscale∈[.05,.2] MPa, Et∈[.5,5] MPa, C1∈[.01,1] MPa*s, eta_n/eta_s∈[.1,2] MPa*s이며 rho/Phi/P0는 고정한다. 고정 gamma 절점의 강도는 압축 순서대로 양의 단조 증분 `Y_j=Y0+sum(exp(z_j))`을 사용할 수 있다. 이름 있는 스칼라를 최적화하고 표를 임의 편집하지 않는다.

Ka0의 독립 한계 계산은 정확한 총 로그변형률 증분 0->-.02->-.01->-.02, E=10 MPa, Y=.1 MPa, P0=0에서 끝점 응력 -.1,0,-.1 MPa, J=exp(eps)를 준다. 이 증분 기준값을 유한 구간별 아핀 네이티브 제어와 구분한다. 공기압 순서의 단위 기준 계산은 P0=.1 MPa, Phi=.1, Jold=.9, Jnew=.91, old total=-Y-pair_old, 제하 증분=.002다. 현재 공기압을 더한 결과가 잘못된 이전 공기압 분할과 달라야 한다. 이 검사는 커널 단위 검사이며 일관된 전체 FE 증분으로 해석하지 않는다.

Ka1은 별도의 적분인자 기준 계산을 사용한다. 속도 -.02/s로 1 s, 2 s 유지, +.02/s로 1 s 제하하며 E0=C2=10 MPa, C1=.1 MPa*s, Et=2 MPa, eta=.5 MPa*s다. 강도 제한은 비활성이다. `sigma'+a*sigma=E*r+b*(eps0+r*t)`, `a=(E+Et)/eta`, `b=E*Et/eta`의 정확한 해는 다음과 같다.

sigma(t)=sigma0*exp(-a*t)+(E*r+b*eps0)/a*(1-exp(-a*t))+b*r*(t/a-(1-exp(-a*t))/a^2).

1e-5 s의 소스 Euler 결과를 상대오차 1e-3+절대오차 1e-6 MPa로 비교하고 활성 주응력 제한도 별도로 검사한다.

수치 인수 조건은 지정 U의 오차 1e-7 mm+1e-4|expected|, Ka0/Ka1의 각 표본 응력 absdiff<=1e-5 MPa+.005|oracle|, J의 상대오차 1e-5다. 질량 추가·요소 삭제·음의 체적·비유한 에너지가 없어야 한다.

에너지 진단에는 GLOBAL CSV에서 정확히 `INTERNAL ENERGY`, `KINETIC ENERGY`, `HOURGLASS ENERGY`라는 총에너지 열을 사용한다. 세 열의 선언 단위는 같아야 한다. 이 입력의 Mg*mm^2/s^2 = 1e-3 J다. `abs(global HOURGLASS ENERGY)<=1e-3*max(abs(global INTERNAL ENERGY),1e-12 in that total-energy unit)`을 요구한다. 자유 준정적 쿠폰에서는 미리 고정한 초기 램프 마스크 밖에서 `abs(global INTERNAL ENERGY)>1e-12 total-energy unit`인 표본에 `abs(global KINETIC ENERGY)/abs(global INTERNAL ENERGY)<=.01`을 추가로 요구한다. 마스크 밖 표본의 분모가 하한보다 작으면 진단 not_assessed이며 조용히 0으로 처리하지 않는다. 힘 불평형도 <=1%여야 한다. BRIC IE는 별도의 소스/D 프로필 에너지밀도 관측량이므로 전역 총에너지 비의 분모로 쓰지 않는다. 완전히 구속한 아핀 시험의 통과로 자유 쿠폰 기준을 인증하지 않는다.

Ka1 시간 간격은 소스에서 확인한 DTIX 다음에 초기/최대 초를 적는다. 기준은 2e-6/2e-6, 절반 간격은 1e-6/1e-6이며 질량 스케일링은 없다. freform은 DTIN/DTMX를 읽고 max<0 또는 initial>max를 거부한다. max=0은 큰 기본값이 되므로 0을 쓰지 않는다. 관측한 실제 dt는 `<=.01*min(eta_n/(Eeff+Et),eta_s/(Eeff+Et))`여야 한다.

비교 격자는 0..0.045s를 .0005 간격으로 나눈 고정 91점이다. 실제 네이티브 TIME에서 선형 보간하되 각 비교 시간이 실제 출력 시간 구간 안에 있어야 한다. 일치하는 실제 시간이 없으면 양옆의 실제 시간에서 보간하며 외삽·끝점 생성은 금지한다. 세분화 오차는 각 점에서 `abs(base-half)<=1e-5 MPa+.0025|half|`다. 선언한 실패가 있을 때만 한 번 더 절반 간격을 실행하고 이전 실패 바이트도 보존한다.

## 6. 직교이방성 탄성과 명시적으로 파생한 XBP

### 6.1 재료축, 탄성식과 시험 조건

재료축 1=MD, 2=CD, 3=MD cross CD다. 일에 켤레인 응력·변형률 벡터는 `s=(s11,s22,s12)`, `e=(e11,e22,gamma12=2eps12)`다.

S=[[1/E1,-nu12/E1,0],[-nu12/E1,1/E2,0],[0,0,1/G12]];
e=S*s;nu21=nu12*E2/E1로 연결한다.

E1,E2,G12>0, 1-nu12*nu21>1e-6을 요구한다. 평면 밖 응력은 0이며 두께 방향(ZD)·굽힘·박리·손상은 지원하지 않는다. 자유변수 r을 [-.95,.95]로 두고 `nu12=r*sqrt(E1/E2)`로 연결하면 탄성계수가 변해도 양의 정부호(SPD)를 유지한다. 두 포아송비를 동시에 자유변수로 두지 않는다.

방향 행렬 Q는 명시적으로 주어진 정규직교 행렬이며 det+1 허용오차는 1e-10이다. 전체 텐서에 `sigma_m=Q^T*sigma_g*Q`를 적용하고 변형률도 같은 방식으로 회전한 뒤 공학 전단변형률로 변환한다. 응력 벡터를 물리적 3차원 벡터처럼 회전하지 않는다. ±45 deg, 90 deg, 왕복 회전, 전단 변환 계수 2를 인수 검사에 포함한다.

탄성 시험은 MD/CD 인장과 종·횡게이지, 그리고 직접 전단 또는 전체 변형률 텐서를 측정한 45 deg 인장이다. 횡자료가 없으면 nu12, 전단 민감 자료가 없으면 G12를 고정한다. A0=b*t의 측정 불확도가 필요하며, 없으면 두께와 탄성계수가 혼동된다. Force/A0는 공칭응력이다. 명시한 소변형<=.01/단면 변화 무시 정책에서만 Cauchy와 동일하게 취급하고, 그 외에는 실제 단면적이 필요하다. 고정 RH/T/함수율이 모델 개정을 정의하며 함수율 법칙을 추정하지 않는다. holdout은 독립 조습 시편의 22.5/67.5 deg이며 반복 시편 그룹의 자료 분할을 유지한다.

탄성 검증값은 E1=4000 MPa, E2=1000 MPa, G12=500 MPa, nu12=.25, nu21=.0625다.

| 하중 | 계산 결과 |
|---|---|
| MD의 s11=4 MPa | e11=.001, e22=-.00025 |
| CD의 s22=1 MPa | e22=.001, e11=-.0000625 |
| tau=1 MPa | gamma=.002 |

해석식 허용오차는 상대오차 1e-12/변형률 오차 1e-14다. 이는 방향별 탄성이며 종이 소성이나 3차원 9개 탄성상수 지원을 뜻하지 않는다.

### 6.2 파생 XBP의 정확한 이름과 식

사용자 표시명은 **GMN2012-derived power-hardening XBP, plane stress, tensor-norm variant**다. 이 모델이 사용자가 뜻한 Xia라고 추정하지 않는다. 원래 전체/tanh형 27개 파라미터 descriptor는 not_implemented이며 원문·소스 확보가 필요하다. 아래에는 이름의 모호함과 별개로 선택한 축소 후보의 식을 완전히 정의한다.

법선 N1=(cosθ1,sinθ1,0),N2=(cosθ2,sinθ2,0),N3=(0,0,1),N4=-N1,N5=-N2,N6=-N3.
f=sum_alpha((max(Nalpha dot s,0)/Yalpha(kappa))^(2k))-1;
Yalpha=qalpha*(1e-6+kappa)^nalpha,alpha1..5,Y6=Y3.
e=S*s+ep (=e_elastic+ep) and dep=dlambda*gradient_s(f).

이 구현에서 사용하는 경화 변수는 `kappa=sqrt(ep11^2+ep22^2+.5*gammap12^2)`다. **현재 소성 텐서의 노름**이며 누적 경로 길이가 아니다. 원 논문의 소성 벡터 전단 해석을 직접 검토했다고 주장하지 않는다. 초기 지원 영역은 단조 하중과 탄성 제하이며 역방향 소성은 검증하지 않았다.

### 6.3 국소 적분, 상태 관리와 제어

후진 Euler의 미지수는 (s11,s22,s12,ep11,ep22,gammap12,dlambda), 잔차는 일곱 개다.

Rstress=S*s+ep-e_target (3);
Rflow=ep-ep_n-dlambda*grad_s f(s,kappa(ep)) (3);
Rf=f (1).

탄성 시험값은 C*(e_target-ep_n)이며 f<=1e-10이면 ep를 유지한다. 제품용 해석 Jacobian에서 max(x,0)^m의 미분은 x<=0,m>=4일 때 0이고, 원점에서는 kappa의 기울기를 1e-14 기준으로 정칙화한다(수치적 특이점을 보정한다). 중앙 유한차분 7×7 검사는 무차원 간격 1e-6과 절반 간격의 미분 일치 1e-4를 사용한다. 응력은 max(q,1 MPa), 변형률은 .01, 승수는 stressscale*.01로 스케일한다.

Newton은 35회, Armijo는 축소 계수 .5/최대 12회/충분 감소 1e-4이며 dlambda>=0,Y>0을 지킨다. 적합·유동 잔차 무한 노름<=1e-10(변형률 단위), |f|<=1e-8을 요구한다. 실패하면 변형률 증분을 최대 10단계 이분하고 모든 하위 간격이 수렴한 뒤에만 상태를 확정·저장한다. 하위 간격 계획·해시도 보존한다. 실패는 CONSTITUTIVE_NONCONVERGENCE이며 시험응력을 성공 결과로 반환하지 않는다.

상태에는 ep의 3성분, stress의 3성분, 수렴 조건을 통과한 제어량·시간을 저장한다. 손상·함수율·유한회전 상태는 없다. 사다리꼴 일과 s:dep로 지원 영역 내 소성 일>=-1e-9 MPa를 검사한다. 탄성 제하는 ep를 유지한다. `dep dot ep_n<-1e-12`이면 역방향 소성으로 감지하고 UNSUPPORTED_STRESS_PATH를 반환한다. 경화 변수를 누적 변형률로 바꾸지 않는다. 응력 제어 경로는 같은 갱신식을 둘러싸서 필요한 자유 변형률을 풀며 일관된 음함수 접선을 사용한다. 외부 Newton은 30회, 응력 잔차는 1e-8 MPa+1e-8stressscale이고 매 시험 계산은 직전에 확정한 상태에서 시작한다.

조건은 qalpha>0, n∈[0,.6], 고정 정수 k=2(기본) 또는 3, 단위 법선, abs(sin(θ2-θ1))>=.2이며 Y3/Y6을 연결한다. 첫 방향별 절차에서는 θ1=0,θ2=90으로 고정한다. 전체 시연의 선택 범위 θ1∈[-30,0],θ2∈[90,120]도 법선 간격을 유지한다. 좌굴 없는 독립 압축 자료가 없으면 압축 q4/q5,n4/n5는 고정/미식별로 둔다. 인장 전용 십자형 시편 자료로 이 값을 동정할 수 없다.

### 6.4 독립 기준값, API와 동정 순서

완전소성 검증 입력은 k=2, 축/반대축 N, q1=q4=4 MPa, q2=q5=2 MPa, q3=q6=1 MPa, n=0이다. 항복응력은 MD=4 MPa, CD=2 MPa, 전단=1 MPa, 등이축=1.9699162420218093 MPa다. 횡방향 무응력 MD 끝점 e11=.002에서 s11=4 MPa, ep11=.001, e22=-nu12*s11/E1이다.

거듭제곱 경화 n=.2의 독립 스칼라 근은 다음과 같다.

- MD: E=4000 MPa, q=50 MPa, 끝점 e=.008일 때 sigma=16.473788317304816 MPa, ep=.003881552920673796.
- 전단: G=500 MPa, q=10 MPa, 끝점 gamma=.02일 때 tau=3.869162563705432 MPa, gammap=.012261674872589137, kappa=.008670313451112474.

MD 스칼라 식은 `sigma=q*(1e-6+e-sigma/E)^n`, 전단 식은 `tau=q*(1e-6+abs(gamma-tau/G)/sqrt2)^n`이다. 정확한 45 deg 텐서 회전은 소성 전단을 포함한 세 번째 독립 기준값을 제공한다.

일반 복귀 사상의 오차 한계는 응력 1e-6 MPa, kappa 1e-8이다. 완전소성의 한계는 응력 1e-8 MPa, ep 1e-10이다. MD/전단/비례 이축 경로에서 간격 절반화 차이는 상대<=1e-4여야 한다. 기울기 유한차분, 축 회전, 롤백, 상태 격리, 미지원 반전도 검사한다. **일곱 미지수 일반 커널은 이번 작업에서 실행하지 않았다.**

재료점 API는 등록 어댑터 콜백 안의 `evaluate_path(parameters,control_history,initial_state)->PredictionBundle`이다. 세 면내 제어 성분마다 응력/변형률 중 정확히 하나를 선택하고 시간 절점을 반드시 지정한다. 출력은 실제 시간, 재료/전역 응력 3성분·변형률 3성분, ep 3성분, kappa, Yalpha, 일, 잔차/하위 간격 상태다. 균질 재료점 경로는 십자형 시편의 디지털 영상 상관(DIC) FE 필드가 아니다. 유료 Abaqus에 의존하지 않는다. 이 파생 후보의 네이티브 솔버 export/Apply는 false이며 수식·파라미터·방향 보고서를 LAW32/43으로 가장하지 않는다.

동정은 탄성 파라미터부터 하고, 인장 MD/CD/전단과 이축 1:1/2:1의 횡소성변형률을 사용해 선택한 q1/q2/q3,n1/n2/n3과 선택적 θ를 구한다. 시연 범위 q1∈[2,8] MPa, q2∈[1,4] MPa, q3∈[.5,2] MPa, n∈[0,.4]는 해당 연구의 범위이며 모든 종이에 적용할 기본값이 아니다. holdout은 22.5/67.5 deg와 이축 3:1이다. 원래 전체 XBP의 27개 파라미터 보정, 역방향 소성, 시편 FE 역해석 보정은 이 백엔드에서 지원하지 않는다.

## 7. 실제 참조 실행 기록과 아직 남은 검증

과거 D는 공식 Linux 배포본 latest-20261011(선언 솔버 개정 cb760c)로 A가 작성한 원래 Ka0/Ka1 덱을 실제 실행했다. Starter는 오류 0개/경고 0개, Engine은 NORMAL, TH 변환기는 0이었다. CSV는 각각 100행×82열이며 마지막 실제 TIME은 Ka0 .04950070, Ka1 .04950116이다. .05로 가정하지 않는다. 원래 원시 파일의 해시는 다음과 같다.

Ka0 CSV a86ddfe3c6af75de48b6359177e8ce08fb8d8b6ebc1cf2aa0291e249f9956012;
Ka1 CSV bd01598f59a3200d6eb3136045e2736e9fb6c73c326958c1d66c5ef27ed715b2.

A의 독립 미세 1차원 소스 법칙 적분에서 최대 응력 오차는 2.708214999e-7/3.123068068e-6 MPa였다. F는 DOP853으로 별도 계산해 2.70927677e-7/3.37898440e-6 MPa를 얻었다. F는 8개 절점의 U, J(rho/VOL/절점), 나머지 5개 응력, 추가 질량/아워글라스도 확인했다. maxU~1.01e-8 mm, maxJ~5.10e-7이며 추가 질량·아워글라스 값은 모두 0이었다. 고정 혼합 허용오차를 통과한 범위는 이 짧은 아핀 법칙 검사뿐이다.

A는 DTIX 상한/절반 상한 쌍도 별도로 실행했다. 상한 없는 원래 실행 기록을 소급 변경하지 않는다.

baseline CSV SHA256 92f76f9076b0a387a82deab460165b59efdaaa0ef368e483f2631e0c6bf8836f;
half CSV e04b55417e9372ba7b062fc529cbbeb48780fc511af68ef316ef5c44482bae4e.

두 실행의 실제 TIME STEP과 Engine 목록으로 2e-6/1e-6 상한을 확인했다. 실제 자료는 모두 0..0.0495를 포함하지만 고정 91점 격자는 0..0.045만 쓴다. 최대 baseline-half는 7.56674e-6 MPa, A의 baseline-oracle은 5.20662e-6, half-oracle은 7.56674e-6이었다. F의 독립 값은 baseline=5.21684e-6 MPa, half=7.56674e-6 MPa이었다. 모든 표본의 혼합 허용오차를 통과했다. 복구한 코드를 사용할 수 없는 환경에서 새로 실행했다고 주장하지 않는다.

실제 사용한 공식 바이너리의 SHA256은 다음과 같다.

starter_linux64_gf=0bbea37273656b270bea53cf756b115bcd5d9a8875a3c41283922a3b927719d1;
engine_linux64_gf=54cc9fc2a7cea6f1215afb6da9109d368650ff1fa7985625cb111f47d7e92be4;
th_to_csv_linux64_gf=704d647331ae4befd329d18b991d644f2af930abd117c5d204c23925f75fc8db.

Tools의 소스 빌드 식별과 배포 바이너리 식별은 서로 다른 주장이다.

원래 묶음에는 바이너리 해시를 검사하는 이식 가능한 실행기가 있었고, --opencourant-root/--output-root를 명시적으로 받았다. 검증기는 --native-root를 받았다. 실제로 새 출력 위치에서 재실행해 두 상한 CSV 해시를 재현했다. 표준 라이브러리만 쓰는 산술·보관 출력 검사 스크립트 8개도 다른 완전한 증거 디렉터리 구조로 복사해 원래 체크아웃·네이티브 바이너리를 가져오지 않고 통과했다. 과거 실행 기록의 절대 argv 경로는 관측값이며 이후 설치의 필수 경로가 아니다. 원 바이트가 없으므로 복구판에서 원 실행 기록의 바이트 해시를 다시 입증할 수는 없다.

새 구현 검사는 test_material_dp_axisym.py, test_geos_dp_history.py, test_material_foam33.py, test_material_orthotropic.py, test_material_xbp.py와 공통 재료 계약/평가기/서비스 검사다. 등록 CLI `python -m caelab.materials validate|evaluate|fit|inspect --study <manifest>`는 자료를 읽으며 임의 import 코드를 실행하지 않는다. 전체 사용자 인수는 독립 CSV→설정→평가/보정→holdout→잔차 확인→저장/재열기→정직한 export/Apply 상태 표시의 흐름이다. 취소·오류와 실제 Windows GUI도 포함한다.

확인된 근거는 정확한 소스 조사, 산술·한계 검증값, 짧은 Linux LAW33 실행과 상한 간격 세분화다. GEOS/Linux 네이티브 실행, 일반 XBP 커널, Ka1의 전체 장시간 유지·속도·전단 시험, 독립 실물 재료시험, 새 제품 서비스·공급자·Windows 화면은 아직 실행하지 않았거나 구현하지 않았다. 호출, 이력의 손실 없는 전달, 명시한 법칙의 계산, 사용자 작업 흐름이라는 네 보정 계층을 각각 보고한다.

## 8. 새 재료 모델을 추가할 때의 통과 조건

메타데이터 등록만으로 새 모델을 지원한다고 표시하지 않는다. 다음 열 가지가 필요하다.

1. 정확한 법칙·버전·소스·라이선스·식 위치.
2. 파라미터·상태·단위·측도·좌표계·전단 규약·부호·지원 영역.
3. 실제 설치된 해석식/라이브러리/네이티브 백엔드와 모든 API·카드 연결.
4. 제어량·필수 관측량·추출기·자료 범위 및 독립 확인한 원시 검증 자료.
5. 초기 상태·초기화·이력 유지·수치 수렴과 미지원 영역.
6. 실험 변환·자료 분할·가중치·스케일·식별성.
7. 독립 한계값·수치 기준값과 물리적 판정 정책.
8. 실제 export/Apply 경계.
9. 신뢰할 수 있는 설치 어댑터·스키마 등록. 연구 JSON에서 모듈을 실행하지 않음.
10. 주장하는 플랫폼·네이티브 검사와 공통 서비스의 처음부터 끝까지 인수 검증.

통과하지 못한 기능은 이유와 함께 선언만 됨/미검증/미지원으로 남긴다. 이 상태가 무관한 원래 네이티브 덱의 실행을 막지는 않는다.
