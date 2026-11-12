# A. 압력 의존 소성·압축성 폼·방향성 종이 재료의 구현 명세

**현재 개발 순서:** A의 실제 솔버 납품은 D13의 LAW33 Ka0/Ka1이다. 계수→입력→실제 실행→TH→동정/새 확인평가를 POST 해당 판독과 병렬 준비한다. Ka0 curve_id=0 또는 LAW1 E 적용은 연결 곡선·방향 원자 적용의 증거가 아니다. 전체 A는 D13/D15/D16에서 여섯 프로필을 따로 인수한다. [전체 작업표](IMPLEMENTATION_TASKS_ko.md#whole-backlog)의 단계·파일·인수를 따른다.

이 문서는 **앞으로 구현할 제품 기능의 명세**다. 압력 의존 소성의 Drucker–Prager(DP), Xia–Boyce–Parks(XBP) 계열 등을 다룬다. 일부 수식과 LAW33 계산은 실제 Linux 참조 실행으로 확인했지만, 새 재료 서비스, 일반 XBP 구성방정식 갱신, GEOS 실제 실행, Windows 제품 인수 검증은 완료되지 않았다. 각 계산법의 정의, 실제로 실행한 범위, 제품에 아직 연결하지 않은 범위를 구분해 읽어야 한다.

**복구 상태:** 실행 환경을 사용할 수 없게 된 뒤 새로 재구성한 명세다. 원래 최종본의 SHA256 `5d913c95bc5a0f8bfd9deeb6faf5dda690b8cd246e1d456f478ea4922b97f5ff`와 바이트가 같다고 주장하지 않는다. 작성 당시 R3에서 선택한 법칙·수식·코드 식별자·공개 인터페이스·수치 절차·실제 참조 결과·제한을 보존한다. 원래 솔버 고유 형식(네이티브)의 시간 이력(TH), CSV, 로그의 원시 바이트는 추측해 복원하지 않았다. 인계를 승인하려면 이 복구판 자체를 검토해야 한다.

<a id="material-integration"></a>

## 0. 기존 전체 계획에 연결한 구현 순서와 인수 범위

이 절은 Workbench `2217429b6ccd8bce3063fdf60887ef2bcac97ae6`의 코드와 대조한 구현 계획이다. 아래의 물리식·소스 개정·과거 참조 계산 결과를 새 제품 구현으로 바꾸지 않는다. DP/Xia/J2는 범용 계약을 검증할 사례이며, 쉬운 모델 하나를 동작시킨 뒤 재료 연구 전체를 완료로 처리하지 않는다. A의 6개와 [B의 5개 프로필](material-finite-history.md)은 이번에 구현·검증할 서로 다른 계산 범위다. 이후 모델 추가도 같은 등록·자료·검증 경로를 사용해야 한다.

### 0.1 기존 코드에서 가져올 것과 새로 만들어야 할 것

기준 코드에는 `caelab/materials/`와 `MaterialStudyWindow`가 없다. 기존 모듈을 호출할 수 있다는 사실과 새 재료 연구가 연결됐다는 사실을 구분한다.

| 기존 코드·계획 | 그대로 사용할 기능 | 재료 연구에서 추가할 구현·확인 |
|---|---|---|
| `caelab/mdo/variables.py`, `objectives.py`, `constraints.py`, `optimize.py` | 자유·연계 변수와 좌표 변환, 기존 목적함수·제약 판정, `OptimizationProblem`, `BatchEvaluator.evaluate_batch`, `optimize` | 새 `materials/evaluator.py`가 고정 시험의 실제 `PredictionBundleV1`을 호스트 잔차·제약으로 바꾸고, 요청 ID·점 ID·내용 개정을 보존한 `EvaluationObservation`을 반환한다. 곡선 배열을 스칼라 응답으로 넣지 않는다. |
| `caelab/mdo/study_manager.py` | `StudyManager`와 `DesignPointRecord`의 후보 ID·상태·실제값/예측값 구분 | 시험별 곡선·잔차·단위·관측 범위와 `CalibrationEvaluationArtifact`는 새 재료 서비스가 저장한다. 현재 CalculiX/INP 종속 `MDOStudyService`에 재료 JSON을 넣거나 별도 최적화기를 복사하지 않는다. |
| 기존 계획 P4, `caelab/jobs.py`, `native/adapter.py`, `input_snapshot.py`, `native/run_store.py` | 외부 실행의 입력 사본·실제 프로세스·취소·로그·실행 소유권 | 재료 어댑터가 계수와 시험 경로를 정확한 입력 바이트로 만들고 필요한 관측량을 수집한다. 원래 네이티브 덱 실행은 재료 보정 기능의 미지원 때문에 차단하지 않는다. |
| POST01–04의 검증된 변환·이력 추출 | 원시 ID·실제 시간·단위·측도·축·위치를 보존한 수치 전달 | 실제 LAW33 보정에는 해당 시간 이력 추출기가 필요하다. 해석식·재료점 어댑터의 구현 착수와 검증에 RP 또는 POST 전체 완료를 요구하지 않는다. |
| 기존 계획 P6, `atomic_json`, `FileLock`, `RunStore.copy_to` | 원자적 저장, 쓰기 소유권, 실제 실행 참조 보존 | CORE02/STORE01이 재료 연구 v1과 프로젝트 v4의 저장·재열기·다른 이름으로 저장을 구현한다. MDO v2 저장·복구와 기존 사용자 연구는 유지하며 자동 이행하지 않는다. |
| 기존 MDO Phase 1–3과 독립 Studio | 수치 엔진과 화면·서비스 분리, 실제값/대리모델 구분 | 재료 연구 창은 UI01의 별도 진입점이다. MDO Studio를 재료 전처리기로 바꾸지 않는다. 기존 MDO의 회귀와 재료 연구 인수를 각각 확인한다. |

구체 구현의 공통 계약은 [core C1–C10](core-implementation.md), 기존 단계의 책임은 [전체 개선 계획](../../review/2026-10-23-codex-improvement-plan.md), MDO의 기존 범위는 [MDO 설계](../spec-clean-slate-mdo-design.md)를 따른다. 공통 모듈은 해당 단일 담당자가 구현하며 재료 담당은 아래 어댑터와 그 검사만 소유한다.

재료 평가기의 실제 응답은 기존 `mode="ACTUAL"`을 사용한다. 재료점·라이브러리는 `source_id='material-evaluation:<id>:<sha>'`와 실제 평가 자료를 제공하고 가짜 FE 실행 ID를 만들지 않는다. 네이티브 실행이 있으면 실제 `run_ids`, `native_attempts`, `cache_hit`을 기록한다. `SURROGATE`는 실제 계산으로 표시하지 않는다. 자유변수가 없으면 고정 파라미터를 평가하며 0차원 `OptimizationProblem`을 만들지 않는다.

### 0.2 여섯 프로필의 파일과 서로 다른 지원 판정

아래 파일은 모두 새로 만들 `caelab/materials/adapters/` 하위 경로다. 파일명과 프로필 ID는 공통 작업표에서도 같은 이름을 사용한다. 표의 내용은 인수할 목표이며 현재 제품 지원 선언이 아니다. `supports_export`가 참이어도 내보내는 산출물의 종류를 확인해야 하며, 파라미터 보고서를 네이티브 카드로 표시하지 않는다.

| 프로필 | 구현 파일 | 계산·동정 인수 | 실제 FE 재평가 | 파라미터·수식 보고서 | 네이티브 카드 내보내기 | 열린 Prep 문서 Apply | 플랫폼 조건 |
|---|---|---|---|---|---|---|---|
| geos_dp_axisym_linear_v1 | `geos_dp.py` | 4절의 드라이버 전체 행·잔차·상태 검사와 다중 시험 동정 | 이 드라이버는 FE 메시를 사용하지 않음 | 실제 드라이버·소스와 시험 범위를 명시 | GEOS 드라이버 XML과 범용 재료 카드를 구분; 카드 승격은 미지원 | 미지원 | Linux x86_64 직렬 실제 실행, Windows/WSL은 배정 0건 |
| dp_axisym_closed_v1 | `dp_axisym_closed.py` | 4절의 정확한 해석식·혼합 제어·다중 시험 동정 | 이 프로필은 재료점 계산 | 지원할 보고서의 정식화·제한을 고정 | 미지원 | 미지원 | Windows 실제 연구 흐름과 Linux의 수치 검사 |
| oc_foam33_ka0_v1 | `opencourant_foam33.py` | 5절 Ka0 식·이력·실제 TH 채널의 다중 시험 동정 | LAW33 요소·쿠폰 경로로 별도 인수 | Ka=0과 곡선·공기압 규약을 명시 | 정확 LAW33 codec과 솔버 판본 검증 전 미지원 | 원자적 다중 카드·연결 곡선 검증 전 미지원 | 실제 사용한다고 선언할 OS의 Starter/Engine/변환기 각각 확인 |
| oc_foam33_ka1_v1 | `opencourant_foam33.py` | 5절 Ka1의 속도·유지·전단·간격 세분화와 동정 | Ka0 성공으로 대체하지 않고 별도 인수 | Ka=1의 점성·속도·시간 단위를 명시 | Ka1 추가 필드·시간 설정의 codec 검증 전 미지원 | Ka1의 카드·곡선·한 번의 Undo/Redo 검증 전 미지원 | 짧은 Linux 참조 실행을 Windows 제품 검증으로 확대하지 않음 |
| ortho_plane_stress_elastic_v1 | `orthotropic_plane.py` | 6절의 MD/CD/전단·축 회전·식별성 및 동정 | 이 프로필은 평면응력 재료점 계산 | 재료축·공학 전단·평면응력 제한을 명시 | 정확 공급자 카드·방향 codec 검증 전 미지원 | 방향·연계 포아송비의 자료형 검사와 실제 적용을 확인하기 전에는 미지원 | Windows 실제 연구 흐름과 Linux의 수치 검사 |
| gmn12_xbp_power_plane_stress_v1 | `gmn12_xbp.py` | 6절의 일반 7미지수 갱신·혼합 제어·동정 전부 | 이 프로필은 재료점 계산; 시편 FE 역해석은 미지원 | 파생 모델명·현재 텐서 노름·지원 경로를 명시 | 미지원 | 미지원 | 일반 커널의 실제 실행과 Windows 연구 흐름을 각각 확인 |

GEOS의 `TriaxialDriver`는 외부 프로세스에서 실행하더라도 구성식 드라이버이므로 `CapabilityReport.level=material_library`로 고정한다. 실행 소유권은 기존 JobManager/RunStore에 남긴다. 실제 요소·시편 FE 계산만 `native_fe`다. 드라이버 텍스트를 FE 필드 `ResultRefV1`로 바꾸지 않는다. 해석식 DP·직교이방 탄성·파생 XBP는 `analytic` 수준이다. LAW33의 실제 요소·쿠폰 계산은 `native_fe`이며 실제 계수→입력→실행→추출의 근거가 필요하다.

지원 판정은 프로필/개정, 어댑터 정체성, 솔버/판본, 요소, 관측량, 호스트의 조합별로 한다. 계산·동정, 실제 FE 재평가, 파라미터 보고서, 네이티브 내보내기, 문서 Apply의 통과 근거를 기존 `CapabilityReport.evidence_refs`, 평가 자료와 내보내기/적용 응답에서 각각 따라갈 수 있어야 한다. 별도 지원 상태 enum이나 저장 형식을 새로 만들지 않는다. 설치 누락·미실행·수치 실패·물리 적합성 미판정은 서로 다른 이유로 남긴다.

### 0.3 구현 순서와 사용자가 확인할 종료 행동

| 단계 | 착수·의존 조건 | 이 단계에서 끝내야 할 동작과 검증 |
|---|---|---|
| 공통 평가 연결 | CORE01 계약을 고정한 뒤 CORE02와 어댑터 인터페이스 개발 가능 | 신뢰 등록한 어댑터가 엄격한 CSV→ExperimentSpec→plan→실제 PredictionBundle→호스트 잔차·제약→평가 자료를 통과한다. 등록부·서비스의 모델명 분기를 수정하지 않는다. |
| 프로필별 계산·동정 | 해당 프로필의 자료·단위·상태·오류 계약 | A의 여섯 행을 각각 검사한다. 각 절의 독립 수치 기준과 다중 시험 train/validation/holdout, 식별성, 경로 반전·후보/시험 순서 교환, 취소·누락·실패를 확인한다. 한 행 통과를 다른 행에 적용하지 않는다. |
| 실제 네이티브 관측 전달 | 기존 P4의 실행 소유권과 POST01–04 중 해당 판독기·추출기 검증 | 실제 솔버 입력·출력·TH를 읽고 원 ID·시간·단위·측도·축·위치·모든 필수 표본을 비교한다. RP 화면, 종료 코드 0, 파일 생성만으로 통과하지 않는다. GEOS는 FE 검사로 가장하지 않고 4절의 드라이버 검사로 닫는다. |
| 네이티브 FE 다중 시험 동정 | 검증한 LAW33 입력 codec·실제 실행·관측 추출과 CORE02 | 계수→각 시험의 입력 사본→실제 실행→관측량→고정 잔차→독립 holdout→저장/재열기를 같은 연구에서 수행한다. 원래 덱 실행이나 참조 법칙 한 번의 일치와 별도 인수다. 쿠폰에 필요한 에너지·힘 수지는 5절 그대로 적용한다. |
| UI·프로젝트 연결 | CORE02+STORE01과 수치 검증을 통과한 등록 어댑터 최소 1개 | UI01은 실제 CSV로 평가·동정·holdout·잔차·중지/취소·선택 평가 저장·다른 이름으로 저장·재열기를 Windows에서 수행한다. 첫 어댑터는 화면 경로의 첫 인수이며 MAT-A/B 전체 완료가 아니다. |
| 최종 계수 확인과 P6 재개 | 선택한 계수와 고정 내용 개정, 지원 플랫폼의 실제 백엔드 | 최종 선택 뒤 아래의 캐시를 쓰지 않는 재평가(`refresh`)를 수행하고 새 평가 ID·실제 응답·holdout·제약·식별성을 저장한다. 원자료·설정 변경 뒤에는 새 내용 개정으로 재계산하며 과거 결과는 과거 근거로 유지한다. |
| 정확한 내보내기·Apply | 해당 솔버/판본/필드·곡선·방향의 검증된 codec과 PREP01의 문서 소유 IPC | 먼저 보고서와 네이티브 카드를 구분한다. 지원하는 경우에만 새 카드 바이트와 미리보기를 만들고 문서 세대/개정·기준 해시·전체 연결 자원을 검사해 한 트랜잭션으로 적용한다. 한 번의 Undo/Redo, 재내보내기 의미 보존, 필요한 실제 FE 재평가를 확인한다. 미지원이면 원문 변경 0건과 이유를 남긴다. |

공통 서비스의 최종 확인 호출은 `evaluate(values, *, expected_revision, on_progress=None, cache_policy='reuse', execution_phase='exploration')`다. `cache_policy`는 `reuse` 또는 `refresh`만 허용한다. 탐색 중 성공 캐시는 `reuse`로 재사용할 수 있다. 최종 후보를 선택한 뒤의 확인 평가는 `execution_phase=confirmation,cache_policy=refresh`로 수행한다. 기존 성공 자료를 보존하면서 새 평가 ID를 만들고 해석식·라이브러리를 실제로 다시 계산하거나 새 네이티브 실행을 수행하며 `cache_hit=false`를 기록해야 한다. 같은 캐시 손실을 다시 읽은 것은 최종 실제 재평가가 아니다. confirmation에 reuse를 결합하면 `CONFIRMATION_REQUIRES_REFRESH`다. 실제 실험 목록·불변 선택 기록·선택 정책과 노출 후 재사용 경고는 공통 C4를 따른다. holdout으로 후보를 고르거나 다시 보정하지 않는다. holdout을 보고 절차를 바꾸면 새 개정과 사용하지 않은 보류 시험이 필요하다.

첫 UI 검증 모델과 첫 Prep 적용 모델은 같다고 가정하지 않는다. LAW1의 E 한 필드를 적용한 결과는 문서 IPC·초기 트랜잭션 검사일 뿐이며, A/B 모델의 카드·곡선·방향 적용을 검증하지 않는다. 내보내기 구현을 검증하지 않은 모델은 파라미터/수식 보고서만 내보내고 솔버 네이티브 덱 성공으로 표시하지 않는다. Apply 실패를 피하려고 입력파일을 직접 덮어쓰거나 여러 `set_fields`를 이어 호출하지 않는다.

### 0.4 독립 자료, Windows와 범용 확장의 완료 조건

각 프로필의 수치 인수에는 해당 절의 식·검증값·허용오차를 그대로 사용한다. 합성 자료는 잔차·최적화·상태·소스 연결의 수치 검증용이다. 실제 재료에 관한 적합성을 주장하려면 구현에 쓰지 않은 저장소 밖 측정자료, 필요한 게이지·단위·측도·조건·반복 시편, 고정한 자료 분할과 양의 스케일, 사용 전에 선언한 물리 수용 한계가 필요하다. 이 자료나 한계가 없으면 `adequacy_status=not_assessed`이며, 수치 수렴이나 낮은 합성 손실로 대신 통과시키지 않는다. 필요한 변수가 식별되지 않으면 고정하거나 비식별을 보고하고, 변경한 설정은 새 개정으로 보존한다.

Windows 인수는 [공통 C9](core-implementation.md#c9-실제-qt-재료-연구-창과-공통-위젯)의 실제 설치·한글/공백 경로·IME·화면 배율·닫힌 창의 지연 응답·취소·저장/재열기를 따른다. 해당 호스트에서 `can_run`을 선언한 프로필은 실제 백엔드로 확인하고, 부재·미지원 프로필은 0회 배정과 정직한 이유 표시를 확인한다. GEOS의 Windows 거부가 해석식 DP 연구를 막아서는 안 된다. Linux 참조 성공과 Windows 미지원 표시 검사는 Windows의 실제 계산 성공을 대신하지 않는다.

전체 재료 묶음의 종료에는 A의 여섯 프로필과 B의 다섯 프로필 각각의 구현·수치·실제 연구 흐름 결과가 필요하다. 부재인 선택 라이브러리나 미실행 솔버는 해당 행을 미충족으로 남기며 나머지 연구를 막지 않는다. 선택 기능이라는 이유로 그 행을 전체 완료에서 지우거나 skip을 통과로 세지 않는다. 계획상 미지원인 FE·내보내기·Apply는 지원 성공 대신 정확한 거부 동작으로 확인하고, 이후 지원을 선언하려면 별도 인수를 추가한다. 제품 기능, 실물 재료의 적합성, 공학적 안전 승인도 각각 구분한다.

새 모델의 확장성은 별도 신뢰 설치한 어댑터/descriptor 하나가 공통 서비스·GUI의 모델명 조건문 수정 없이 기존 CSV→실제 평가→잔차·제약→동정→독립 holdout→저장/재열기→지원 범위에 맞는 보고서/내보내기를 통과하는지로 확인한다. 초기 선형식이나 `y=a*x+b*x²`의 계약 검사는 이 구조의 검사이며 전체 재료법 지원을 뜻하지 않는다. 실제 법칙을 추가할 때는 8절의 출처·상태·측도·단위·지원 경로·실제 계산기·독립 수치/실험·플랫폼 조건을 빠짐없이 적용한다.

전문가에게는 기존 `EvidenceEnvelopeV1.kind=calibration`의 `evaluation_ref`, `study_id`, `content_revision`, `evaluation_id`로 같은 고정 평가를 전달한다. 실제 시험/예측/잔차/분할/holdout, 제약·식별성, 단위·측도, 백엔드·소스·플랫폼·부분 실패의 근거를 따라갈 수 있어야 한다. 해석식·라이브러리 평가를 자문에 첨부하는 데 POST/RP 전체 완료를 요구하지 않는다. 솔버에서 유래한 FE 결과는 별도의 실제 `native_run`/`derived_result_ref` 출처를 유지한다. 재열기 후 자료가 없거나 변조됐으면 현재 이용 가능성을 표시하고, 과거 평가를 현재 설정의 계산으로 재사용하거나 LLM이 계수를 적용·실행하게 하지 않는다.

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

모든 CSV에는 원자료 정체성, 시험·반복 시편·자료 분할, 실제 독립축, 제어량·관측량의 열 대응, 단위·측도·부호·좌표계·선택 위치와 적용한 변환 절차가 필요하다. 독립축은 프로필과 관측량에 따라 시간·주파수·신장비·하중 경로가 되며, 주파수 자료에 가상의 time_s를 만들지 않는다. 두께·단면·온도·상대습도(RH)·함수율 등은 아래의 적용 조건을 따르며 모든 재료에 일괄 필수로 요구하지 않는다.

| 프로필·관측 방식 | 반드시 필요한 축·조건 | 조건부 필수 또는 적용하지 않는 항목 |
|---|---|---|
| DP 해석식/GEOS 이력 | 증가하는 time_s, 축/횡 제어 경로, 초기 응력·예하중 기준, 유효/전응력 및 배수 조건 | 힘→응력 또는 변위→변형률 변환이면 실제 단면·게이지 길이가 필수다. 직접 응력·변형률 재료점 자료에는 시편 치수를 요구하지 않는다. RH·함수율은 이 일정 파라미터 DP 식에 적용하지 않는다. |
| LAW33 실제 요소·쿠폰 | time_s, 실제 제어·변형 이력, rho0·체적 기준, 요소/IP·절점 선택과 축, 초기 상태 | 실제 FE 형상·부피는 입력/실행 참조에서 확보한다. 힘·변위 환산을 쓰면 해당 단면·길이가 필수다. RH·함수율은 이 식에 적용하지 않는다. 온도 의존식을 지원한다고 추정하지 않는다. |
| 직교이방 탄성 | 선언한 단조 하중축 또는 시간, MD/CD 방향, 응력/공학 전단 규약 | 힘→공칭응력에는 두께·폭, 변위→변형률에는 게이지 길이가 필수다. 직접 텐서 자료에는 치수를 요구하지 않는다. 종이 시험은 조습 RH/T/함수율 기준을 모델 개정에 고정한다. 해당 조건이 없는 일반 직교이방 재료는 종이 조습 항목을 적용하지 않는다. |
| 파생 XBP 이력 | 6절 API의 time knots, 면내 세 성분의 제어 선택, 초기 소성 상태·재료축 | 기하 환산·종이 조습 항목은 위와 같은 조건부 규칙을 적용한다. 직접 재료점 응력 자료에 두께를 추정해 넣지 않는다. |
| B의 Ogden | 실제 신장비/F/J와 제어 모드 | 속도 비의존 정적 자료에 time_s를 요구하지 않는다. 힘·변위 환산에 필요한 기준 형상만 필수다. |
| B의 Prony/TRS | 완화·경로 시험은 time_s, DMA는 frequency 또는 angular_frequency와 위상 규약, 모두 실제 온도 | frequency-only DMA에 time_s는 적용하지 않는다. 이미 탄성계수로 측정한 자료에는 두께·단면을 요구하지 않는다. 응력→탄성계수에는 부과 변형률과 정규화가 필수다. |
| B의 NEML/JC | NEML은 time_s·온도·순환 제어, JC는 p/r/T/η와 상태 경로에 필요한 순서·시간 | 직접 국소 상태/응력에는 시편 치수를 요구하지 않는다. 힘·변위만으로 JC 국소 상태가 갖춰졌다고 보지 않는다. |

합성 산술 자료는 실물 조습·시편을 측정한 자료가 아니므로 그 실물 항목을 적용하지 않으며, 출처와 고정 법칙 조건을 명시한다. 등록한 `ModelDescriptorV1.conditions_schema_ref`와 관측량의 자료 변환 규칙이 위 필수 항목을 검사한다. 적용하지 않는 항목은 조건 스키마의 필수 목록에서 제외하고 판단 근거를 변환 설명·진단에 남긴다. 새로운 상태 enum을 만들거나 0·임의 상수로 채우지 않는다. **해당 조합에 필수인** 메타데이터가 없으면 EXPERIMENT_METADATA_MISSING, 측도가 틀리거나 불명확하면 MEASURE_MISMATCH다. 단위를 추측하지 않는다. 역학 단위는 공통 단위 계층을 사용하고 온도 변환은 중앙 materials/units.py에서 처리한다. 프로필마다 온도 오프셋을 따로 두지 않는다.

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

### 3.1 GEOS 외부 프로세스 어댑터의 구현 경로

기준 `JobManager.submit_native`는 `get_solver_adapter(status.spec.id)`로 실행 어댑터를 찾는다. 현재 등록부에는 GEOS가 없고 기본 `snapshot_input`은 INP를 `model.inp`로 복사한다. 따라서 프로그램 이름만 등록해서 위 GEOS 경로를 실행할 수 없다. 새 `caelab/native/geos_driver.py`의 `GeosDriverAdapter`를 아래와 같이 구현하고 기존 확장 훅을 사용한다. 이 파일과 공유 등록·설치 경로의 편집은 공통 통합 담당자가 소유하며, `materials/adapters/geos_dp.py` 담당자는 이를 호출·수집한다. 공유 파일의 동시 편집은 금지한다.

| 실제 기존 접점 | 구현할 정확한 계약 |
|---|---|
| `connections.py`의 `ProgramSpec`·설치 조회 | id=`geos_driver`, name=`GEOS constitutive driver`, role=`solver`, env_var=`CAELAB_GEOS`, executables=`('geos',)`, platforms=`('linux',)`. 기존 환경변수/설치 레지스트리/PATH 탐색으로 절대 실행 경로와 native Linux `ProgramStatus`를 얻는다. 실행 파일 존재·권한·버전/실제 SHA 및 고정 소스 대응 근거를 검사한다. 설치 발견만으로 재료 기능을 지원하지 않는다. |
| `native/adapter.py`의 `register_solver_adapter` | `solver_id='geos_driver'`, `input_extensions=('.xml',)`인 위 인스턴스를 신뢰 설치 단계에 한 번 등록한다. 연구 JSON에서 클래스를 import하거나 셸 명령을 받지 않는다. `submit_native(...status.spec.id='geos_driver')`가 같은 인스턴스를 찾는지 검사한다. |
| `adapter.snapshot_input(input_path,workdir,check_cancelled)` | 현재 프로필의 고정 입력 생성기가 만든 외부 참조가 없는 XML만 받는다. 허용 요소·속성은 4절의 Problem/Events/Constitutive/Tasks/Functions와 inline TableFunction에 한정한다. 외부 include/table/file/restart 참조와 알 수 없는 요소·속성은 실행 전에 거부한다. 현재 지원하는 참조 폐쇄 집합은 XML 한 파일이다. 원본 바이트를 `source/case.xml`, 실행 바이트를 `case.xml`로 보존하며 같은 바이트인지 확인한다. 읽기 전후 원본 파일의 식별정보(fingerprint)와 SHA를 대조하고 `input_revision`, `execution_input='case.xml'`, `files`의 원본/실행 상대경로·원본/실행 SHA를 기존 snapshot 형식에 채운다. 기본 INP 입력 사본 생성기로 우회하지 않는다. |
| `build_run_request(status,workdir,input_path)` | 원본 `input_path`를 실행하지 않고 검증한 `workdir/case.xml`을 사용한다. `LaunchRequest(argv=[absolute_geos,'-i','case.xml'],cwd=workdir,env={**external_environment(),**status.runtime_env,'OMP_NUM_THREADS':'1'},program_id='geos_driver')`를 만든다. 실행 직전 파일 SHA와 입력 사본 SHA를 다시 대조한다. 별도 subprocess·PID/큐 소유를 만들지 않는다. |
| `build_run_stages(status,workdir,snapshot)` | 위 `build_run_request`로 만든 단일 `('solver',LaunchRequest)` 목록을 반환한다. snapshot의 실행 XML 해시를 배정 직전에 대조한다. 기존 JobManager가 이 훅의 존재를 통해 실행 후 각 `files[].execution_path`의 SHA를 다시 검사하게 하며, 일치하지 않으면 수집 성공 전에 실패한다. 공유 어댑터 인스턴스에 run별 변경 상태를 저장하지 않는다. |
| `collect_results(workdir,check_cancelled=...)` | 먼저 `source/case.xml`과 `case.xml`의 바이트를 서로 및 고정 snapshot의 SHA와 다시 대조한다. snapshot은 해당 run의 저장 기록에서 읽고 원본 경로를 재탐색하지 않는다. 실제 `dp_native.txt`의 9열·전체 행·시간·유한성·잔차·항복 검사를 4절대로 수행하고, 같은 실제 표본을 `dp_history.csv`로 변환한다. 성공 반환은 기존 native 결과 사전의 `status='readable',frames=[],exchange_files=[],warnings=[],summary={}`다. 실패는 예외로 전달해 기존 `parse_state='failed'`가 되게 한다. FE 프레임·VTU·물리 요약을 꾸며 넣지 않는다. 재료 어댑터가 이 실제 CSV에서 `PredictionBundleV1`을 만든다. |
| `expected_output_names()`·`artifact_paths(workdir,snapshot)` | 예상 출력은 `('dp_native.txt','dp_history.csv')`다. `artifact_paths`는 실제 존재하는 위 두 출력과 `job.log`·`solver.log`를 반환한다. 기존 작업자의 출력/최종 artifact 해시 수집이 이 훅을 사용하게 하고 원본·실행 XML은 snapshot의 `files`로 보존한다. 기본 FRD/DAT/PVD/VTU 검색으로 대체하지 않는다. |

원시 드라이버·CSV·로그는 실제 run 소유를 유지하고 재료 연구는 그 고정 산출물을 참조한다. 계산 수준은 여전히 `material_library`이며 FE `ResultRefV1`은 없다. 성공 자료의 해시·행 검사와 실패·취소·수집 중 취소·공백/한글 경로·원본/출력 변조·미등록/미설치·재열기 시 미실행을 `test_geos_dp_history.py`에서 실제 native 어댑터 접점으로 검사한다. 모의 프로세스 검사는 계약 검사로 구분하며 실제 Linux GEOS 실행은 별도 인수다. 일반 GEOS XML이나 외부 참조 지원은 이 프로필로 추정하지 않는다.

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

GEOS 소스의 국소 갱신은 20회 반복 뒤 최종 미수렴 예외를 던지지 않으며, 견고한 꼭짓점 분기가 없고 newQ/trialQ로 나눈다. 따라서 exit0만으로 성공을 판정할 수 없다. 모든 네이티브 행에서 실제 자료 범위·유한성·횡응력 잔차·driver residual<=1e-8·q>=0과 재구성한 소성 승수·점착력·항복/상보조건을 확인한다. 응력 허용차는 `f_tol=1e-7 Pa+1e-6*stress_scale`, 승수 판정 허용차는 `lambda_tol=f_tol/(3G+K*F*D+h)`다. 초기 행과 `abs(dlambda)<=lambda_tol`인 탄성 증분은 `f<=f_tol`이어야 하며 항복면 위일 필요가 없다. `dlambda>lambda_tol`인 소성 증분만 `|f|<=1e-7 Pa+1e-6*stress_scale`을 요구한다. 첫 행은 이전 증분이 없으므로 초기 a0로 `f<=f_tol`을 검사한다. 이후 행의 진단 승수는 이전 응력과 실제 변형률 증분의 탄성 trial을 재구성한 `dlambda=(q_trial-q_new)/(3G)`로 구한다. 승수는 `dlambda>=-lambda_tol`, 점착력은 a>=0을 만족해야 하며, 상보성은 `abs(dlambda*f)<=max(lambda_tol,abs(dlambda))*f_tol`로 검사한다. 음의 승수가 이 범위에 있더라도 무조건 허용하지 않는다. 원시 수치 토큰의 마지막 십진 자리 반단위 반올림 구간과 float64 연산의 64ε 상대 여유를 위 trial/노름/승수 계산에 구간으로 전파했을 때0을 포함하는 경우만 반올림 진단으로 인정한다. 이 근거가 없거나 `dlambda<-lambda_tol`이면 실제 음의 소성 승수로 거부한다. 반올림 진단을 새 소성 상태로 확정하지 않으며, 어떤 분류에서도 `f>f_tol`인 행을 탄성 성공으로 통과시키지 않는다. 고정 초기 행의 q=0,p=-2 MPa에서는 f가 음수인 것이 정상이며 성공해야 한다. 드라이버가 일찍 실패한 뒤 남긴 행은 자료 범위를 충족하지 않는다. 변형률·응력 증분에서 재구성한 점착력은 연산 진단값으로 표시하며, 솔버가 출력한 내부 필드로 표시하지 않는다.

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

## 9. 동정 회귀 검사용 고정 합성 연구

이 절은 **새로 구현할 동정 검사의 입력과 합격 목표**다. 합성 관측값을 생성하는 데 사용한 기준 계수를 이하 참조계수라고 부른다. 새 실제 동정이 성공했다는 기록이 아니다. 각 절의 단일 평가 기준값, 실제 솔버의 법칙 재현, 저장소 밖 실측자료 인수를 대신하지 않는다. 원래 증거 CSV·코드·expected 파일은 바꾸지 않는다. 구현 시 별도 검사 자료 경로에 아래 독립식으로 관측값을 한 번 생성·검토해 고정하고, 검사 대상 어댑터의 예측으로 expected를 다시 만들지 않는다. `source_kind=synthetic_arithmetic`을 유지한다.

### 9.1 모든 연구에 적용할 실행·판정 규칙

- 참조계수, 자유/고정 파라미터, 제어·관측량, 비교 격자·자료 분할·스케일·가중치·수치 설정을 실행 전에 고정한다. 각 시험은 새 초기 상태로 시작한다. 한 시험의 제하·재하·유지는 상태를 유지한다. 이 절의 관측값에는 잡음이 없다.
- 표에 적은 초기 벡터는 최초 공개 `evaluate`와 상태/손실 확인에 사용한다. 기존 SLSQP는 `ContinuousVariable.initial`을 시작점으로 쓰지만 기존 DE는 그 값을 x0로 넘기지 않는다. DE의 모집단은 현재 구현의 seed = 17, population_size = 20, 나머지 2절 설정으로 고정한다. 초기 벡터를 DE의 첫 모집단이라고 표시하지 않는다. 각 표의 범위는 이 회귀 연구의 물리 범위이며 원 법칙의 지원 영역을 바꾸지 않는다.
- 목표 함수는 표에 적은 필수 채널만 사용한다. 시험별 동일 가중치, 채널별 동일 가중치, 각 시험의 실제 시간 또는 정적 하중축에 대한 정규화 사다리꼴 가중치다. 별도 기재한 진단량은 `required_for_fit=false,required_for_validation=false`다. 체적·q·P처럼 이미 선택한 채널에서 유도한 값을 중복 가중하지 않는다.
- 선택한 최종 계수에서 `execution_phase=confirmation,cache_policy=refresh`로 train·선언 validation·독립 holdout을 다시 계산한다. 탐색 중 holdout 계산·노출은 금지한다. 실행 단계와 불변 선택 기록은 [공통 C4](core-implementation.md#c4-비교-목적함수와-평가-정책의-일관된-판정)의 `execution_phase`, `selection_ref`, `SelectionRecordV1`을 따른다. 참조계수·초기 벡터 자체는 합성 검사 정의이며 실제 연구의 미지 물성을 제공하는 기능이 아니다.
- 각 표의 손실·최대 관측오차·자유 계수오차를 **모두** 만족하고 필수 자료·제약 검사를 통과해야 동정 회귀가 합격이다. 종료 이유와 후보 평가 횟수는 실제 값으로 남긴다. 예산 종료나 정체를 CONVERGED로 바꾸지 않는다. 최종 유효 후보가 기준을 만족했는지와 최적화 수렴 여부는 별도 보고한다. 실패 후 범위·가중치·허용오차·관측 표본을 바꿔 같은 검사를 통과시켜서는 안 된다.
- 아래 DE 연구의 예산은 각각 최대 평가 1000회/100세대/3600 s이며 별도 전단·축별 연구는 각각 독립 예산이다. 탄성 직교이방성은 2절 SLSQP의 최대 평가 400회/반복 120회이다. 실제 백엔드에서 이 예산이나 정확도를 충족하지 못하면 미충족으로 남기며 참조식 결과나 캐시로 교체하지 않는다. 이 예산은 성능 실측 결과가 아니다.

### 9.2 DP 해석식과 실제 GEOS 드라이버의 동정

두 DP 프로필에 같은 고정 합성 관측을 사용하되, 하나가 성공했다고 다른 실행기의 동정을 통과시키지 않는다. 참조계수는 K = 1e9 Pa, G = 6e8 Pa, c = 1e6 Pa, phi = 30 deg, h = 5e7 Pa, psi = 10 deg, density = 2700 kg/m3다. K/G/density는 고정하고 자유 좌표는 `(c,phi,h,dilation_fraction)`이다. `psi=dilation_fraction*phi`, 참조계수 fraction = 1/3, 초기값은 `(1.2e6 Pa,25 deg,4e7 Pa,.2)`다. 범위는 4절의 c/phi/h/fraction 범위 그대로다.

train은 구속압 P = 2 MPa와 5 MPa의 독립 두 시편이다. 시간 절점 `(0,1,2,3) s`에서 축변형률 `(0,-.01,-.008,-.012)`, 각 구간 선형, 출력 시간은 `t=i*.05 s, i=0..60`이다. 초기 정상응력은 각 시편에서 모두 -P이며 초기 출력 변형률은 0이다. validation은 P = 3 MPa의 같은 경로·격자다. holdout은 P = 3.5 MPa, 시간 절점 `(0,2,4,6) s`, 같은 변형률 경로, `t=i*.1 s, i=0..60`이다. 필요한 축응력과 두 횡변형률만 목적함수에 넣으며 스케일은 각각 10 MPa와 .01이다. q/P/체적변형률은 진단이다.

독립 관측 생성기는 4절의 일정 구속압 탄성/소성 구간별 해석과 탄성 제하·재항복점을 사용한다. 일반 복귀 사상이나 제품 어댑터를 호출하지 않는다. 구간 교차점은 정확한 항복점에서 나누며, 모든 비교 시간의 응력·횡변형률을 고정한다. GEOS는 같은 시간·경로의 값을 XML 내부의 TableFunction에 기록하고 `steps=60`으로 실제 61행을 읽는다. 원래 steps = 100의 단일 평가 검증 자료는 그대로 유지한다.

합격 목표는 train/validation/holdout 각각 `fit 형태의 L<=1e-6`, 모든 표본 축응력 오차<= 2e4 Pa, 횡변형률 오차<= 2e-5다. 자유 계수는 c/h의 상대오차 <= .02, phi 절대오차 <= .5 deg, fraction 절대오차 <= .02를 요구한다. 이는 4절의 훨씬 엄격한 단일 평가 정확도 검사와 별도인 동정 목표이며 그 정확도 검사를 완화하지 않는다. 순서 반전·구속압 하나만 남긴 비식별 반례·횡자료 누락의 psi 고정 규칙도 별도로 유지한다.

### 9.3 LAW33 Ka0의 탄성계수·압축 한계 동정

참조계수는 5절의 Ka0 고정 입력과 같다. 자유 계수는 `E=10 MPa,A=.1 MPa`이며 초기값은 `(8 MPa,.12 MPa)`, 범위는 E∈[5, 20] MPa, A∈[.05, .2] MPa다. curve_id = 0, B=C = 0, Fscale = 1, P0 = 0과 나머지는 고정한다. 단조 강도 곡선 전체를 이 두 계수 검사로 동정했다고 주장하지 않는다.

train은 5절의 **기존 11개 속도 절점**을 그대로 사용한 구속 단축과 정수압 아핀 시험 두 개다. validation은 속도 진폭을 .5배로 한 구속 단축, holdout은 25/19배로 한 독립 구속 단축이다. 첫 압축 정지점의 원래 변위는 -.019 mm이므로 holdout의 J는 .975다. 각 비교 시간은 `t=i*.0005 s,i=0..90`이며 기존 0.. .045 s 격자와 같다. 모든 시험의 관측량은 Cauchy 수직응력 세 성분, 스케일은 각각 .1 MPa다. J·밀도·변위·에너지는 필수 실행/물리 진단으로 별도 검사하며 목표함수에 중복 넣지 않는다.

독립 관측 생성기는 구간별 선형 속도를 해석 적분해 u(t)를 구한다. 단축은 λx = 1+u/1 mm, λy=λz = 1, 정수압은 세 λ가 모두 1+u/1 mm다. 실제 로그변형률 lnλ를 사용하고, 각 속도 구간에서 응력의 탄성 변화와 ±A 제한에 닿는 위치를 정확히 나눠 5절 Ka0의 누적·제하 응답을 구한다. 네이티브의 실제 절점/밀도/체적과 이 이상 제어의 차이는 기존 U/J 허용오차로 검사한다. 정확한 지수 신장 제어라고 바꿔 쓰지 않는다.

최종 합격 목표는 각 시험 분할의 L <= 1e-6, 모든 응력 성분의 최대오차 <= 5e-4 MPa, E/A의 상대오차 <= .02다. 실제 네이티브 Starter/Engine/TH 변환·추출을 통과해야 하며, 독립 Ka0 스칼라 계산만으로 네이티브 동정 검사를 대신하지 않는다. 5절의 원시 출력·에너지·시간 간격 기준은 그대로 적용한다.

### 9.4 LAW33 Ka1의 과도·유지 응답과 전단 점성 동정

이 짧은 합성 회귀는 기존 5절의 장시간·진속도 시험을 대체하지 않는다. 참조계수는 E = 10 MPa, C2 = 10 MPa, C1 = .1 MPa*s, Et = 2 MPa, eta_n=eta_s = .5 MPa*s와 5절의 나머지 고정값이다. 강도 한계는 A = .1 MPa, P0 = 0으로 고정하고 여기서는 응력 상·하한 제한이 비활성임을 검사한다.

정상응력 연구의 자유 벡터는 `(C1,Et,eta_n)`, 초기값은 `(.15 MPa*s,1.5 MPa,.7 MPa*s)`다. 범위는 C1∈[.01, 1] MPa*s, Et∈[.5, 5] MPa, eta_n∈[.1, 2] MPa*s다. eta_s는 참조계수로 고정한다. train의 명목 속도 r은 .1/1/10 s^-1인 구속 단축 세 시험, validation은 .3 s^-1, holdout은 3 s^-1의 독립 시험이다.

모든 시험에서 `Tload=.002/r`, δ=Tload/10, V = (1-exp(-.002))/(Tload-δ) mm/s로 둔다. 속도 절점은 `(0,0),(δ,-V),(Tload-δ,-V),(Tload,0),(Tload+.1,0),(Tload+.1+δ,V),(2*Tload+.1-δ,V),(2*Tload+.1,0)`이다. 유지는 .1 s이며 이 정의는 총 압축 신장 exp(-.002)와 원 길이까지 제하를 만든다. 실제 순간 진속도는 v/λ이고 명목 r과 구별한다. 각 램프·상수속도·유지 구간을 20등분한 시간과 모든 절점을 합쳐 비교 격자를 고정한다. 중복 절점은 한 번만 넣는다.

독립 관측 생성기는 속도 적분으로 λ·ε=lnλ·순간 변형률 속도를 구하고, 5절의 정상 ODE를 독립 DOP853으로 적분한다. rtol = 1e-11, atol = 1e-12 MPa, 각 제어 구간의 max_step=구간길이/20이며 경계마다 적분을 나눈다. 이 독립 기준 계산은 제품 Euler 갱신을 호출하지 않는다. 네이티브 입력은 같은 속도 절점과 DTIX 기준/절반 간격을 사용한다. 이 짧은 회귀에서는 TH 출력 간격을 각 시험의 최단 제어 구간/20 이하로 명시하고 실제 출력 시간에서 보간한다. Engine 종료는 마지막 비교 시간보다 기준 DTIX의 10배 뒤로 두고 그 사이 속도 0을 유지해 마지막 비교점까지 실제 시간 구간으로 둘러싸게 한다. 출력 시간을 추정해 넣거나 외삽하지 않는다. 정상 세 응력이 관측량이며 스케일은 각각 .1 MPa다.

전단 연구는 위 시간 경로의 스칼라 변위 u를 공학 전단변형률 gamma12로 치환한 균일 단순전단이다. 세로 길이 1 mm에서 x=u(t)*y/1 mm를 지정하고 나머지 아핀 성분은 고정한다. 단축과 같은 부호·속도 절점으로 train r = 1/10, validation r = .3, holdout r = 3을 사용한다. 관측량은 tau12, 스케일 .1 MPa다. 자유 계수는 eta_s뿐이며 초기값 .7 MPa*s, 범위 [.1, 2] MPa*s다. 나머지는 참조계수로 고정한다. 독립 기준 계산은 `eps12=gamma12/2`인 5절 전단 ODE다.

각 연구의 합격 목표는 시험 분할별 L <= 1e-5, 모든 응력의 최대오차 <= 5e-4 MPa다. 정상 연구는 C1 상대오차 <= .1, Et/eta_n 상대오차 <= .05, 전단 연구는 eta_s 상대오차 <= .05를 요구한다. 세분화·수동성·비유한/음의 체적·추가질량 검사는 계속 필수다. 실제 전단 입력·추출기가 없으면 전단 연구가 미충족이며 eta_s 동정 성공으로 표시하지 않는다. 일반 Ka1의 장시간 유지·속도 범위와 활성 응력 상·하한 제한 검사는 여전히 5절의 별도 인수다.

### 9.5 직교이방 탄성과 파생 XBP의 계수 동정

직교이방 탄성의 참조계수는 6절 `(E1,E2,G12,nu12)=(4000,1000,500 MPa,.25)`다. 자유 좌표는 `(E1,E2,G12,r)`, 참조계수 r = .125, 초기값 `(3600,1100,450 MPa,.1)`이다. 범위는 E1∈[2000, 8000], E2∈[500, 2000], G12∈[250, 1000] MPa, r∈[-.8, .8]이며 nu12=r*sqrt(E1/E2)로 연결한다. train은 MD 응력 0..4 MPa, CD 응력 0..1 MPa, 직접전단 0..1 MPa를 각각 21개 등간격으로 부과한 독립 시험이다. 종·횡 변형률과 공학 전단이 관측량, 스케일은 각각 .002다. 독립 정답은 6절 S의 직접 곱이다. validation은 45 deg, holdout은 22.5/67.5 deg의 전역 단축응력 0..4 MPa(각 21점)이며 전체 텐서 회전 후 S로 계산한다. 합격 목표는 시험 분할별 L <= 1e-10, 변형률 최대오차 <= 1e-7, E1/E2/G12 상대오차 <= 1e-3, r 절대오차 <= 1e-4다.

파생 XBP는 탄성값을 위 참조계수로 고정하고, k = 2, θ1 = 0, θ2 = 90 deg, q = (4, 2, 1, 4, 2, 1) MPa, nα = .2를 참조계수로 한다. 압축 계수·방향은 고정이며 압축/선택 방향까지 식별했다고 주장하지 않는다. q1/n1(MD), q2/n2(CD), q3/n3(전단)를 각각 자유로 둔 **별도 세 연구**를 수행한다. 초기값은 각각 `(3.6 MPa,.18),(2.2 MPa,.22),(.9 MPa,.18)`, 범위는 6절의 q1/q2/q3와 n∈[0, .4]다. 다른 q/n은 각 연구에서 참조계수로 고정한다.

MD/CD의 train은 축변형률 0.. .008을 .0002 간격으로, 전단 train은 공학 전단변형률 gamma12 = 0.. .02를 .0005 간격으로 준다. validation은 MD/CD의 축변형률 0.. .006을 .0003 간격, 전단 0.. .015를 .00075 간격으로 준다. holdout은 MD/CD의 축변형률 0.. .01을 .0005 간격, 전단 0.. .025를 .00125 간격으로 준다. 각 시험의 실제 시간은 표본 i에 대해 i/(N-1) s이고, 나머지 면내 응력 성분은 0으로 제어한다. 관측량은 부과축의 응력 또는 tau12이며 스케일은 MD = 4 MPa/CD = 2 MPa/전단=1 MPa다. 횡변형률·ep·kappa·일은 별도 진단으로 확인한다.

독립 관측은 6절의 MD 스칼라식(이때 CD도 해당 E/q/n으로 같은 식)과 전단 스칼라식의 유일 양의 근으로 만든다. 탄성 시험값이 초기 항복 안이면 그대로 쓰고, 소성이면 `[0,E*e]` 또는 `[0,G*gamma]`에서 Brent xtol = 1e-12 MPa, rtol = 1e-12, 최대 100회 반복하여 푼다. 비교 시 제품의 7미지수·응력제어 갱신을 실제 실행해야 하며 스칼라 독립 기준 계산으로 대체하지 않는다. 각 연구의 합격 목표는 시험 분할별 L <= 1e-8, 응력 최대오차 <= 1e-4 MPa, 해당 q 상대오차 <= .005, n 절대오차 <= .001이다. 세 연구를 합쳐 q1..3/n1..3 복원을 확인하되 공동 6변수 동정의 성공으로 부르지 않는다. 6절의 일반 갱신·반전 거부·45 deg·비례 이축·22.5/67.5 deg/이축 3:1 holdout 인수는 이 축별 동정 검사로 대체하지 않는다.
