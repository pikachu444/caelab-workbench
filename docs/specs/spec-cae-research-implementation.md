# 범용 CAE 재료 연구·전문가 자문·프로그램 연동 구현 계약

작성: 2026-11-12 KST. 설계 기준은 [DESIGN](../../DESIGN.md), 우선순위는 [기존 개선 계획](../review/2026-10-23-codex-improvement-plan.md#material-expert-rp-followup)이다. 확인한 제품 소스는 Workbench `4953a1028582dbdd47ba056588f73f3f7b79d603`, Prep `629fb06cccbfc76efdedb8420236f189216b5257`, RP `cec23cbdd4058237e04fdcc7ecb06a3834578cae`이다. 이 문서는 **새로 구현할 API·데이터·저장·오류·수락 계약**이며 기능 구현/실환경 검증 완료 문서가 아니다.

## 범위와 구현 순서

DP와 Xia는 사용자가 제시한 예시다. 목표는 여러 재료 모델의 정의·시험자료 동정·최적화, 일반 CAE 모델링/구조해석 이론·결과 자문과 답변 검토, 여러 외부 CAE 프로그램·솔버·결과 포맷의 연결이다. 모델/솔버/프로그램별 adapter와 capability를 등록하여 확장한다. 제품 코어에 DP/Xia만의 분기를 넣지 않으며, 모든 구성식·매뉴얼·프로그램을 이미 지원한다고 표시하지 않는다.

- [재료 계약](#materials): 모델·계수·다채널 시험·잔차·evaluator·최적화·저장·내보내기/Apply.
- [자문 계약](#experts): 이론/사례 질문·문헌 색인·자료 충분성·읽기 도구 왕복·답변 검토.
- [프로그램/결과 계약](#program-results): converter·검증된 결과·별도 후처리기·원본 보존·수치 조회.

기존 API와 제안 API를 각 절에서 구분한다. 존재하지 않는 `caelab/mdo/contracts.py`, `study_store.py`, `study.py`를 현재 재사용 모듈로 취급하지 않는다. 실제 optimizer DTO는 `mdo/optimize.py`, 실제 point manager는 `mdo/study_manager.py`, 현재 MDO v2 저장은 `mdo/service.py`다. 기존 MDO/프로젝트/NativeRun 저장본을 새 재료 스터디로 묵시 이행하지 않는다.

첫 코드 작업은 독립적으로 착수 가능한 M1(재료 headless 공통 계약), E0/E1/E2(첨부 문맥·질문/저장·검색), POST01/02(결과 참조·파생작업 수명)이다. 공유 artifact 계약을 먼저 확정하고 E3의 저장 VTU 조회와 POST03의 native converter를 연결한다. M2/M3, E4/E5, POST04–06의 GUI/native/실공급자/Windows 조건은 해당 절의 명시된 의존과 인수 조건을 따른다. 프로그램·모델명을 나열한 것을 지원 완료로 세지 않는다.

## 공통 자료 참조와 소유 계약

신규 **`caelab/artifact_refs.py` 한 파일**이 다음 순수 DTO/JSON validator를 소유한다. stdlib 외 Qt·VTK·solver/GUI import를 금지한다. 실제 VTK 판독은 선택 의존성의 `caelab/result_readers/vtk_fields.py`가 지연 import한다. 자료 전체를 단일 FE 모델 스키마로 바꾸지 않는다.

`ArtifactRefV1`의 필수 필드는 `id, role, path, sha256, size, format_id, producer:{id,version}`이다. id/role/format_id/producer 문자열은 비어 있지 않아야 하고 size는 bool이 아닌 0 이상의 int, sha256은 소문자64hex다. path는 소유 저장소 안의 POSIX 상대 경로이며 절대/drive/UNC/..·NUL·symlink/reparse 이탈을 거부한다. 기존 ID는 그대로 읽고 신규 record ID는 UUID로 발급한다. 모든 JSON은 UTF-8·finite values·추가 미정 필드 거부 규칙을 사용한다. 저장 후 provenance를 나타내는 권한/eligibility를 호출자의 JSON 값만 믿고 승격하지 않는다.

Envelope의 `artifact_owners`는 각 artifact ID를 `{scope:'run'|'operation'|'consumer',id:owner_id}`에 연결한다. operation scope에는 source_run_id가 필수다. owner_id와 등록된 루트는 host resolver만 해석한다. 같은 path라도 owner가 다르면 다른 자료다. 각 소유자의 구체 루트와 ResultRef/DatasetManifest는 프로그램/결과 절에 정의한다. Material의 원자료·배열·보정 결과는 consumer owner `material-study:<study_id>`를 사용한다. discussion 자료와 corpus도 명시적으로 등록된 consumer root에서만 읽는다. LLM에는 artifact/evidence/selection ID만 노출하고 filesystem root를 선택하게 하지 않는다.

`ResultRefV1`은 아래 프로그램/결과 절의 **하나의 정본 정의**만 사용한다. 자문·보정은 그것을 읽고 각자 다른 Result DTO를 만들지 않는다. 실험 CSV·논문 조각·보정 보고서는 native FE result인 척 포장하지 않으며 각자 format과 evidence union을 유지한다. 기존 receipt/evidence는 읽기 adapter로 이 참조에 연결하고 자동 rewrite하지 않는다. source receipt의 frozen bytes와 현재 stale 표시/절대 감사 경로를 분리한다.

Content hash는 provenance·artifact_owners·availability·payload를 포함한 **전체 검증 envelope**에서 자기 `content_sha256` 필드만 제외하고 canonical JSON(키 정렬·공백 없음·UTF-8·NaN 금지)으로 해시한다. 숫자 배열은 재료 절의 dtype/shape/header+little-endian bytes digest와 실제 파일 bytes SHA를 구별한다. 배열 순서·하중 순서·component 순서가 의미 있는 목록은 정렬하지 않는다.

모든 변경/적용/실행의 소유자는 기존 앱과 서비스다. 연구/자문이 모델 수정을 제안하더라도 Prep Apply와 solver 실행은 별도 사용자 명령이다. 보정의 capability 부족이 기존 native 원덱 실행까지 막는 허용 목록이 되어서는 안 된다. GUI는 Windows Qt/PySide6 네이티브 창과 기존 위젯을 사용하며 새 웹 UI·DB·JobManager·유료 API를 선행 조건으로 만들지 않는다.

## 직접 토의에서 확정한 결정

아래는 실제 독립 서브에이전트의 제안→상호 비판→수정→원 비판자 재확인에서 도출한 계약이다. 외부 인간 전문가의 심의나 새 제품 기능 실행으로 표현하지 않는다.

| 제기자 → 검토 상대 | 지적 | 반영한 결정 |
|---|---|---|
| 재료 ↔ 자문 | 총 오차 하나로는 실패 시험·부적합 측도·holdout을 심의할 수 없음 | 시험/채널별 곡선·가중치·범위·실제/예측·실패를 보존하는 CalibrationEvaluationArtifact |
| 결과 ↔ 재료/자문 | 필드명만으로 축·측도·층·단위나 보정 적합성을 판단할 수 없음 | 공통 ResultRef와 required-observable completeness, 제한된 query와 fit/review 적격성 분리 |
| QA → 재료 | API 이름과 선형 예제만으로 범용 구현 명세라고 할 수 없음 | 실제 service 시그니처·저장 v1·잔차 수식·별도 사용자 adapter와 수치 oracle·첫 테스트 명령 |
| QA → 자문 | 질문 모드가 혼합 이론/사례를 막고 추가 확인 클릭을 만듦 | scope와 첨부 분리, 사용자 초안 보존, 실제 자료 선택+기존 OK, 문장 의미 판정으로 전송 차단하지 않음 |
| QA/통합 → 자문 | 판정 알고리즘·Windows 색인 교체·migration·native 함수명 호환 미정 | 고정 adequacy 절차/개발·평가 분리, immutable 색인+atomic pointer, 백업 후 CAS 저장, native alias mapping |
| 통합 → 결과 ↔ 재료/자문 | current job 전체 hash는 SaveAs·stale 변경에 깨짐 | frozen receipt artifact·불변 source fingerprint와 현재 문서 적용 가능성 분리 |
| QA → 결과 | RP CLI/operation 수명/owner/실제 native 수치 인수 미정 | project 소유 facade·operation sidecar·source bytecopy·별도 RP fork exact CLI·합성/제조해 oracle |

각 절의 시간·호출 횟수·수치 허용치는 버전 있는 **구현/수락 정책값**이다. 달성한 성능·범용 정답률로 발표하지 않는다. 작성자와 다른 QA가 동일 커밋의 사용자 진입점·실제 파일/상태 전달을 확인해야 구현된 작업을 닫을 수 있다.


<a id="materials"></a>

## 재료 모델링·동정·최적화


### 1. Existing code that can actually be reused

| Existing path/API | Reuse | Limit that must remain explicit |
|---|---|---|
| `caelab/mdo/optimize.py`: `EvaluationRequest`, `EvaluationObservation`, `BatchEvaluator.evaluate_batch`, `OptimizationProblem`, `optimize(..., cancel=...)` | Numerical optimizer and keyed actual/predicted observations | Responses are finite scalar values/None; curves and experimental semantics are not represented. No `mdo/contracts.py` exists. |
| `caelab/mdo/variables.py`: `ContinuousVariable`, `DiscreteVariable`, `CategoricalVariable`, `LinkedVariable`, `VariableSpace`, `parse_unit`, codec | Free-variable domains, linear/log transforms, linked dimensions and restricted expressions | Fixed parameters must be merged separately; equal lower/upper bounds are invalid. Units currently cover known multiplicative mechanical L/M/T units, not arbitrary units or offset temperature. |
| `caelab/mdo/study_manager.py`: `StudyManager`, `DesignPointRecord` | Point IDs, point table, statuses and actual/predicted separation | This is not a general persistent `StudyStore`. There is no `study_store.py` or `study.py` in the repository. |
| `caelab/mdo/doe.py`, `surrogate.py`, `sensitivity.py`, `objectives.py`, `constraints.py` | Existing DoE, optional surrogate/sensitivity, objective/constraint validation | Installation/capability must be checked; a fit score does not establish constitutive adequacy. |
| `caelab/mdo/service.py`: `_document`, `_persist`, `open`, `reconcile` | Existing persistence/reconciliation behavior is a reference; shared proven primitives can be extracted only where needed | Current format `caelab-mdo-study`, version 2 and `open_input(.inp)`/execution are tied to native CalculiX. Do not write calibration JSON into this format or claim current service accepts a material evaluator. |
| `caelab/native/run_store.py`: `NativeRun`, `RunStore`, `atomic_json`; `filelock.FileLock` | Existing native run receipts, atomic saves, writer lease | Calibration records reference run IDs and artifacts; do not add a second solver-run store or process queue. |
| `caelab/jobs.py`: `BatchItem`, `JobManager.submit_batch/submit_native`, `cancel_batch/cancel_native`, `stop_batch` | All external solver process control | Existing callbacks are threaded; caller must marshal UI notifications. Plugin code does not start untracked subprocesses. |
| `caelab/native/adapter.py`: `SolverAdapter` and registry; `input_snapshot.py` | Existing solver command/snapshot/termination/output collection | Solver registration is not proof of any material law/element/observable combination. Calibration capability checks gate calibration/evidence extraction only; they never introduce a new allowlist that blocks existing general native-deck solver execution. |
| `caelab/mdo/inp_binder.py`: `TemplateDeck`, `BindingSpec` | Supported INP scalar substitutions where their source spans are explicit | Variable-length INP tokens are not a RAD fixed-width renderer. |
| Prep `application/contracts.py`: `ObjectRef`, `PropertySpec`, `SetProperties`, `PropertyHost`; `RadProvider.submit`; `PrepSession.set_fields` | Typed native property edit, units, expected revision/resource validation, single undoable command | Current typed law support remains LAW1/LAW36 basic fields. No model gains support by registration alone. |
| Prep `application/card_text.py`: `prepare_card_text`, `CardTextDraft.changes`; `radioss/deck.py`; `application/curve_csv.py` | Supported fixed-width spans, preservation, CSV/table/curve widgets through public host contracts | Generic CSV curve storage is not experiment interpretation; a private editor schema is not an external law plugin API. |

No new constitutive library, package download, paid API, database, JobManager or copy of the existing numerical optimizer is required for the first implementation. OpenCourant's real constitutive source can be consumed by its native solver adapter; do not rewrite it in Python just to satisfy an artificial material-point prerequisite. MFront/MGIS or other external material libraries are possible later adapters, with installation/license/API checks still unverified. J2/Prony/hyperelastic/user-law are examples of future registrations, not current support commitments.

### 2. Ownership and proposed files

Workbench owns experimental evidence, parameter configurations, evaluations, optimization and fit artifacts. Prep owns its editable native cards/documents, Apply and Undo. Solvers own their constitutive evaluation. RP/ParaView are display consumers, not the source of numerical truth.

Proposed NEW modules (names are proposals, not claims of current APIs):

- `caelab/materials/contracts.py`: frozen v1 records and strict validation; no Qt/solver imports.
- `caelab/materials/registry.py`: explicitly trusted registered installed adapters or user-enabled installed entry points, stable ID/version/config validation; no model-name `if/elif` in the service, no Python module execution taken from JSON.
- `caelab/materials/experiments.py`: raw table acquisition and declared column/measure conversion; source bytes immutable.
- `caelab/materials/residuals.py`: deterministic comparison and per-test artifacts.
- `caelab/materials/evaluator.py`: `BatchEvaluator` bridge and proposed evaluation artifact index.
- `caelab/materials/service.py`: thin `CalibrationStudyService` composing existing `StudyManager`, optimizer/DoE, `RunStore`, `JobManager`, `atomic_json` and `FileLock`. It stores material-domain state, not a second general execution engine.
- `caelab/materials/adapters/linear_elastic_1d.py`: small reference adapter to prove the contract; not a limitation on the final material families.
- `caelab/materials/adapters/native.py`: reuse native snapshot/job infrastructure for externally authored solver one-element/coupon models. This backend is a peer of analytic/library material-point evaluation; neither requires the other first.
- `apps/workbench_qt/material_study.py`: proposed independent material-study user entry/host. Integrate through existing Workbench tool/project file-reference patterns; do not turn its common model viewer into a full constitutive editor.
- Prep proposed `application/material_binding.py`: translate an explicit supported export/Apply plan to existing typed provider commands. Model-specific formats live with native codec/schema owners, not in Workbench widget code.

No new generic `StudyStore` is proposed. The distinct material-study manifest below is necessary because existing MDO v2 persists INP/CalculiX assumptions. Extract shared persistence helpers only after concrete duplication is present; existing MDO v2 data and behavior remain readable without migration.

### 3. Proposed v1 records (required field names)

The proposed cross-domain pure DTO/validation module is the single `caelab/artifact_refs.py` (not a duplicated native-only contract). The cross-domain file reference is fixed as `ArtifactRefV1 = {id, role, path, sha256, size, format_id, producer:{id,version}}`. `path` is relative to its owning store; the app resolver supplies the store scope from envelope provenance and rejects escapes. Envelope `artifact_owners` maps each artifact ID to `{scope,id,source_run_id?}`. Material source/model/recipe/array/export artifacts use `scope="consumer", id="material-study:<study_id>"`, resolved by the host-registered `materials/<study_id>` root; existing native inputs/outputs keep run owners and derived files keep operation owners. Do not introduce a guessed base path or extra calibration scope. Raw experiment/model/recipe/array/export refs all use this DTO. Existing receipts are read through adapters, never automatically rewritten. Experts receive artifact IDs, not filesystem paths. The common evidence envelope may wrap this payload as `kind="calibration"`; a content digest excludes its own digest field and hashes canonical UTF-8 JSON. Experimental curves need not masquerade as native FE results.

All top-level records require `schema_version=1`. Dataclasses are frozen; public arrays are defensive/read-only copies. JSON rejects unknown fields, NaN/Inf, duplicate IDs and invalid references. UUIDs identify entities; SHA-256 identifies immutable content. Plugin config has its own declared JSON schema/hash and rejects unknown fields too. Unknown adapter versions open for evidence inspection only; evaluation/export/Apply is disabled with `UNSUPPORTED_ADAPTER_VERSION`.

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

Descriptor/experiment schemas express path dependence, multiaxial controlled and observed channels, ordered loading segments and complex frequency responses from the first version. Backend capability may return UNSUPPORTED_DOMAIN/UNSUPPORTED_CHANNEL/UNSUPPORTED_VALUE_KIND; this is an explicit initial implementation limit, not a schema restriction to a monotonic strain curve. Real/imaginary or storage/loss pairing has explicit units and phase convention; no complex numbers are encoded as JSON NaN/strings. The first adapter only computes real scalar small-strain elasticity. A viscoelastic or multiaxial native/library adapter may be added without changing the generic service or requiring an in-house material integrator.

v1 parameters optimize finite scalar/choice values. A vector/tensor model registers explicit component IDs/groups; table-valued laws use immutable curve references unless a declared parameterization maps named free scalars to the table. Never silently flatten an arbitrary table or change Prony term count. Shared constraints use existing `ConstraintSpec`/safe expressions where representable; otherwise an explicitly versioned adapter validator returns a typed invalid configuration before running. Fixed parameters are absent from the independent `VariableSpace` and merged into each physical candidate; linked values use the existing codec. If all parameters are fixed, run verification without calling `OptimizationProblem` (it rejects zero dimensions). Categorical choices select declared alternatives; no input string becomes executable code.

Units are normalized through one shared conversion contract using current `parse_unit` where supported. Unknown units/offset temperatures are rejected rather than guessed. Extending units is an explicit shared change with conversion tests. Dimensional input uses physical units; `EvaluationRequest.encoded` is optimizer space. Both are saved separately with codec version. Log transforms require positive physical bounds; no exponentiation of values already decoded by `VariableSpace`.

### 4. Adapter API and service wiring

Proposed protocol:

```python
class MaterialAdapter(Protocol):
    identity: AdapterIdentity
    def capabilities(self, model: ModelDescriptorV1, environment: dict) -> CapabilityReport: ...
    def validate(self, model: ModelDescriptorV1, parameters: dict, experiments: tuple[ExperimentSpec, ...]) -> tuple[dict, ...]: ...
    def plan(self, request: MaterialEvaluationRequest) -> EvaluationPlan: ...
    def collect(self, plan: EvaluationPlan, artifacts: tuple[ArtifactRef, ...]) -> PredictionBundle: ...
    def export(self, request: MaterialExportRequest) -> MaterialExportPlan: ...
```

`MaterialEvaluationRequest` carries `evaluation_id`, `request_id`, `point_id`, `study_revision`, `model_revision`, `adapter`, full physical parameters + units, original encoded vector + codec version, ordered experiment revisions, evaluation level, numerical settings/hash, run budget and cancellation context. `EvaluationPlan` has kind `in_process` (registered callback, never JSON code) or `native_jobs` (ordered explicit native input refs plus solver status/timeout and dependency map). Both return the same observable contract. `EvaluationPlan` also fixes `state_reset_policy="fresh_per_experiment"`, each candidate×experiment initial-state snapshot/hash, ordered increments/segments and temperature/rate/loading histories. No material/internal/plastic state may leak between candidates or independent experiments. Within one experiment, validated ordered segment continuity is preserved. Cross-experiment preload/restart continuation requires an explicit versioned state artifact and capability; M1 rejects it as UNSUPPORTED_STATE_CONTINUATION. Complex DMA responses use named real/imag or storage/loss real channels plus phase convention; no implicit complex→float conversion.

Capability is resolved before calibration jobs are enqueued; unsupported model/element/quantity has a reason, not a fake response. This restriction belongs to calibration and does not restrict the separate existing Workbench native solver run path; the native original and unknown metadata remain preservable/runnable independently.

`CalibrationBatchEvaluator.evaluate_batch(tuple[EvaluationRequest,...])` resolves the immutable study revision, merges fixed/linked parameters, validates capability, creates a plan, delegates external plans to existing JobManager, validates required observables, computes residuals and atomically writes the evaluation artifact. It returns exactly one keyed `EvaluationObservation` for each request; `responses` contains `fit_loss` and explicit per-train-test loss IDs in dimensionless units. Arrays live in artifacts, not `responses`. `source_id='material-evaluation:<id>:<sha>'`; native `run_ids`, `native_attempts`, `cache_hit` are populated honestly. Surrogate predictions keep `mode='SURROGATE'` and cannot count as actual material verification.

A calibration resource policy explicitly contains `max_candidate_evaluations`, `max_native_attempts`, `max_wall_seconds`, `per_job_timeout_seconds`, `max_retries_per_job`, and `max_concurrent_native_jobs`. Optimizer `max_evaluations` counts candidates, not number of specimen solver calls. Preflight expands candidate×experiment job count and refuses dispatch beyond remaining native-attempt budget; retries count against it and preserve their receipts. Completed cache hits consume candidate bookkeeping but zero new native attempts. M1 has no external native jobs; first native bridge uses the existing serial batch scheduler, so max_concurrent_native_jobs=1 until a separate supported concurrency implementation is verified.

The async JobManager bridge is a worker-side wait on completion events; it does not block Qt or start another subprocess manager. Cancellation is passed to `optimize(..., cancel=event)`, prevents further dispatch and calls `cancel_batch`/`cancel_native` for owned active jobs. Adapter computations poll cancellation between increments/tests; opaque external-library calls that cannot be interrupted declare that limitation and do not show cancellation completed before returning. Stop-scheduling and cancel-running remain distinct existing actions.

### 5. Deterministic residual definition

Compare predicted and observed quantities only after dimensions, measure, basis, component, location/gauge, shear/sign and independent coordinate match. A required unknown location/gauge is ineligible; declaring a quantity name alone cannot satisfy it. Cauchy↔PK2 or engineering↔log transformations require the recorded deformation/geometry information; no default volume preservation assumption. Cyclic tests use ordered time or explicit branches; never globally sort repeated strain values. Missing/duplicate abscissae must be resolved by an explicit import recipe, not `np.interp` silently.

v1 uses linear interpolation of prediction onto a **frozen observed grid** within the declared comparison domain. Both domains must cover every required sample; no extrapolation or candidate-dependent overlap crop. Predeclared masks/segments are stored in the experiment revision. A failed/missing point does not reduce the denominator.

For channel c of experiment e: `r[e,c,i] = (y_pred(x_i)-y_obs(x_i))/scale[e,c]`; `L[e,c]=sum(q_i*r_i**2)` where `q_i>=0`, `sum(q_i)=1`. Default q is normalized trapezoidal integration on the frozen x-grid; single-sample observables explicitly use q=1. Channel and training-experiment weights are normalized separately once, stored with the criteria revision. `fit_loss=sum(w_e * sum(w_ec*L[e,c]))` over train only. Per-test scores, raw residuals, normalized residuals, valid ranges and weights remain visible. Scales must be positive and fixed from declared engineering values or frozen training-only data, never a candidate prediction or holdout statistics. A constant signal cannot silently create a zero scale.

Holdout is excluded from optimization/surrogate training/normalization; split is by test/replicate group, not random points from the same curve. Validation may inform explicitly recorded model selection; holdout is evaluated after the chosen recipe is frozen. Tuning after holdout exposure creates a new revision and requires a new untouched holdout for a fresh final validation claim. R²/uncertainty/identifiability are optional reports with `not_computed/not_identifiable/undefined` reasons, never manufactured numbers. Solver termination, fit acceptance and model adequacy are separate assessments.

### 6. Evaluation artifact, states, failure and cache

Proposed `CalibrationEvaluationArtifact` v1 requires:

```
evaluation_id/request_id/point_id/study_id/study_revision
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
assessment: fit_status, holdout_status, adequacy_status, uncertainty_status,
            identifiability_status, warnings[]
provenance_refs[], content_sha256
```

States: pending→running→completed/failed/cancelled/timed_out/interrupted. Processing states and fit status are independent. Structured errors have `code`, stage, experiment/observable IDs, reason, retryable, artifact refs; codes include UNSUPPORTED_MODEL, UNSUPPORTED_UNIT, MEASURE_MISMATCH, COVERAGE_INCOMPLETE, SOLVER_FAILED, PARSE_FAILED, CANCELLED, TIMEOUT, STALE_INPUT and ADAPTER_VERSION_MISMATCH. Invalid required experiment means no aggregate fit objective (`valid=False`, reason, empty required responses); do not map failed runs to a float penalty or drop them from averaging. Retain completed prior evaluations and partial artifacts for inspection.

Whole-run `parse_state=partial` does not automatically forbid every field. Default deny for fit eligibility unless the registered extractor proves **each required observable** has complete expected IDs/samples, known time/measure/basis/location/unit and passes its validation policy; partial unrelated fields remain warnings. Required-field partial remains invalid. Numerical manifests, not RP display state, determine eligibility. Current generic post field-query work initially supports only the verified existing-writer VTU point scalar/component plus explicit frame. RP legacy VTK, cell/IP/layer and tensor-axis operations are later capabilities. A material adapter requiring them returns UNSUPPORTED_OBSERVABLE until its deterministic extractor is verified; listing an observable name is insufficient.

Success cache key includes adapter implementation/config+model revision, full physical parameter values with units, every experiment/data/conversion/loading/specimen hash, evaluation level, solver executable identity/version/options, input closure, converter/extractor identities, numerical tolerance/settings and comparison revision. Artifacts and receipts are hash-checked before reuse. Cancelled/failed/required-partial attempts are not success-cache entries; retry policy/budget is recorded. Material-point and native FE cache namespaces differ. Cache reuse records source evaluation/run refs. No source change is hidden by an earlier cached objective. Cache source identity uses immutable snapshots/artifact hashes, not a hash of the current mutable `job.json`/stale flags. If a native receipt is captured, preserve its exact bytes as a frozen source_receipt artifact and verify that artifact; reopen compares current run immutable identity and artifact closure without treating changed live `stale/stale_reason` context as corruption. Live-document applicability is separate from validity of the historical pinned calculation. Save-as relocates resolver-relative references and does not rewrite audit paths inside frozen receipt bytes.

### 7. Persistence and export/Apply

Proposed folder below the existing project data root (or user-selected standalone location):

```
materials/<study_id>/calibration.json     # format=caelab-material-study, version=1
materials/<study_id>/revisions/<sha>.json
materials/<study_id>/sources/<sha>       # immutable original files, incl. external CSV
materials/<study_id>/arrays/<sha>.npy    # canonical numeric arrays, allow_pickle=False
materials/<study_id>/evaluations/<id>.json
materials/<study_id>/exports/<id>/manifest.json
```

Array content identity is exactly SHA-256 of UTF-8 canonical JSON header `{"dtype":"<f8","order":"C","shape":[n,...]}` (sorted keys, separators `,`/`:`, no extra whitespace), followed by one LF byte, followed by C-contiguous little-endian numeric bytes. v1 permits finite `<f8`, `<i8` and boolean `|b1` only, never object dtype. Serialize `.npy` with explicit format version `(2,0)` and `allow_pickle=False`; ArtifactRef.sha256 separately verifies the actual file bytes. Identical numeric content must give the same numeric hash across platforms. Do not rely on default NPZ ZIP timestamps for determinism.

Native solver `runs/<id>/job.json` stays under the **existing RunStore**; calibration stores references. Top manifest includes study identity, immutable revision refs, point/attempt index, selection, optimizer configuration/history, resource budget, artifact inventory hashes and view state. Relative member refs only; reject traversal/symlink escapes and unknown schema fields. Use existing `atomic_json` plus FileLock. Save-as copies/verifies the full referenced closure and native run refs according to existing project relocation behavior. Reopen validates pinned artifact hashes; missing/tampered pinned evidence is stale/read-only and cannot be applied. A changed external live source does not corrupt a valid frozen snapshot; it disables applicability to that current source until explicitly reconciled, while preserving historical inspection/reproducibility. In-flight work after a crash becomes interrupted, not successful. v1 has no historical migration; future migration is an explicit versioned pure operation writing a new file, preserving original bytes. No blind MDO v2→material conversion.

`export()` is deterministic, writes a new native card/deck and manifest, and **does not modify the open Prep document**. Plan names supported solver/version/law, parameter IDs, unit conversion, actual emitted bytes/hash, required linked curves/includes and unsupported fields. Unsupported export is visible and disables the action. Reference 1D adapter may export a generic calibration parameter report only; that report is not a solver material card.

`prepare_apply()` is separate and requires a supported binding, target `ObjectRef`, document epoch+revision/resource ID, baseline card bytes/hash, exact change map and complete linked-card plan. The first supported LAW1 E edit calls existing `RadProvider.submit(SetProperties(...expected_revision...))`, which calls `PrepSession.set_fields` and commits one dispatcher command. One Undo reverses the entire supported change. For a multi-card material+curve Apply, prepare all typed resource candidate states, validate linked curve/material references and dependent uses without mutation, then call existing `cae_core/document.py:CommandDispatcher.execute_many(states, label, additions=..., removals=..., expected_revision=...)` once under the session lock. The binding/prepare method is new; `execute_many` already exists. One Undo must restore every touched resource/link atomically. Unsupported card fields or an unimplemented atomic prepare/binding path remain export-only with UNSUPPORTED_ATOMIC_APPLY; do not replace the whole document or invoke several separate `set_fields` commands. Revision/hash mismatch keeps the plan/draft, applies nothing and requires re-read/review. Never use Workbench INP file transaction Undo to impersonate Prep document Undo.

### 8. First executable implementation slices and acceptance

Each slice adds current-state documentation and tests; no model is claimed supported until its adapter-specific inputs/outputs are verified. These are implementation increments for the generic contract, not DP/Xia-only milestones.

**M1 — pure contract/residual/registry and first headless fit.** Add proposed contracts/registry/experiments/residuals/evaluator modules plus thin service persistence and `linear_elastic_1d` adapter; reuse existing VariableSpace/optimize. No Qt/native solver change is required to start. The exact proposed public service calls and return DTOs are specified immediately below; older shorthand names are not additional APIs. Configuration edits create a new immutable revision and invalidate old applicability, not old evidence.

M1 is deliberately a synchronous headless API executed by a caller worker; it does not promise an async run handle before an implementation exists. New service API signatures and minimum return DTOs are fixed below:

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
               comparisons: tuple[ComparisonSpec,...], expected_revision: str) -> StudySnapshot: ...
    def evaluate(self, values: Mapping[str,float|str], *, expected_revision: str,
               on_progress: Callable[[dict],None]|None=None) -> EvaluationReceipt: ...
    def optimize(self, config: OptimizationConfig, *, expected_revision: str,
               on_progress: Callable[[dict],None]|None=None) -> CampaignReceipt: ...
    def cancel(self, campaign_id: str) -> CancelReceipt: ... # thread-safe request, not terminal claim
    def evaluation(self, evaluation_id: str) -> CalibrationEvaluationArtifact: ...
    def save(self) -> StudySnapshot: ...
```

`StudySnapshot={study_id,revision,path,read_only,busy_operation,active_campaign_id,experiment_ids,point_ids,diagnostics}`. `ImportReceipt={study_id,previous_revision,revision,experiment_id,source_ref,converted_refs,warnings}`. `EvaluationReceipt={campaign_id,evaluation_id,state,artifact_ref,observation}`. `CampaignReceipt={campaign_id,revision,state,termination_reason,evaluation_ids,selected_ids,budget_usage}`. `CancelReceipt={campaign_id,accepted,state}` has state `requested` until the worker/native job actually acknowledges it; final receipt records `cancelled` or completed-before-cancel. Calls defensively copy DTOs. Headless CLI drives this same service; a UI queues progress to Qt. Create requires a nonexistent manifest target and valid initial model/config/parameters; import atomically copies raw bytes and writes a new revision only after full parsing/conversion succeeds. Configuration mutates no prior revision. Exceptions for invalid/stale configuration are `MaterialStudyError(code, stage, details)` before dispatch; runtime attempts become typed evaluation artifacts. A second writer/busy mutation is rejected, not silently queued. `save()` writes current state to the owned path only; save-as/relocation is a later explicit operation, not an undefined overload.

Exact numeric oracle: CSV strain `0,.001,.002`, stress MPa `0,2,4`; E free 100..10000 MPa, initial 1000. Use the existing SLSQP implementation: `OptimizationConfig(algorithm="slsqp", seed=17, max_evaluations=120, max_iterations=60, ftol=1e-12, finite_difference_step=1e-7)`, `OptimizationProblem(...differentiable=True)`, linear parameter transform, no constraints/surrogate. Accept termination CONVERGED, |E−2000|≤.01 MPa, loss≤1e-10, dispatched requests≤120; do not require an implementation-specific exact iteration count. This exact existing-core probe was run during design and returned E=1999.999614958788, loss=1.3899068902313467e-14 in five observations. No nu/rho is inferred. Independent holdout strain `0,.0015,.003`, stress `0,3,6` yields zero error at E=2000. For E=1800, scale=4 MPa and normalized trapezoidal q=(.25,.5,.25), expected training loss=.00375. Same data expressed in kPa yields identical physical E/loss after explicit conversion. This is a synthetic arithmetic oracle, not experimental validation of a real material.

Additional M1 acceptance: all-fixed verification avoids zero-dimension optimize; positive log bounds roundtrip once; unknown schema/unit/measure and missing comparison coverage reject; nonmonotonic cyclic strain must use explicit time/branches; masks cannot change per candidate; test groups cannot leak into holdout; failed/cancelled/required-partial attempts produce no fit objective; reopen reproduces parameter/curve/residual/split/hash state; tampered source is stale. A custom adapter registered from a separate installed/test module (y=a*x+b*x², synthetic x=(0,1,2), y=(0,5,16), physical bounds a,b=(0,10), initial a=b=1, scale=16, same SLSQP config and normalized trapezoid weights (.25,.5,.25)) fits using the same evaluator/residual/service without editing a model-name switch. Proposed `caelab/materials/__main__.py` supplies the CLI. Proposed registry API is `register_material_adapter(adapter, *, trusted_registration)`; optional installed entry-point group `caelab.material_adapters` resolves only after explicit user/installation enablement. Proposed headless CLI entry `python -m caelab.materials validate|evaluate|fit|inspect --study <manifest>` consumes data files, never module import names from a study. Acceptance registers the test adapter through the registry and CLI, then checks capability refusal for an unsupported multi-axial/complex request and no job dispatch. Unsupported export is explicit. Accept CONVERGED, |a−2|≤1e-3, |b−3|≤1e-3, loss≤1e-9 and ≤120 dispatches, with no generic-service model-name branch changed. Existing-core design probe returned a=2.0000045000541093, b=2.9999979999619013, loss=1.3183663827644835e-14 in 16 observations. This proves extensibility, not support for every material law.

First proposed test files: `tests/test_material_contracts.py`, `test_material_experiments.py`, `test_material_residuals.py`, `test_material_evaluator.py`, `test_material_service.py`, `test_material_registry.py`; custom adapter fixture `tests/material_adapter_fixture.py` has an explicitly registered trusted test object, not arbitrary study-import code. Reproduce with `python -m pytest -q tests/test_material_contracts.py tests/test_material_experiments.py tests/test_material_residuals.py tests/test_material_evaluator.py tests/test_material_service.py tests/test_material_registry.py`. These files are NEW implementation work and do not currently exist. Regression command also runs the existing `test_mdo_variables.py`, `test_mdo_objectives.py`, `test_mdo_optimize.py`. Tests independently recompute array content hashes from the specified canonical header+bytes and require equal hashes after save/reopen; assert tampering fails. State tests compare two candidates/experiments in opposite evaluation order and prove fresh initial state; an unsupported path-dependent/complex request dispatches no job. Actual native/UI tests belong to M2/M3 and cannot be counted as passed by M1.

**M2 — complete user entry for the same framework.** Connect actual CSV column/unit/observable selection→parameter fixed/free/bounds→run/stop/cancel→observed/predicted/residual/holdout plots→save/reopen→export status. Use Prep widgets via host contracts where appropriate; own study state stays in WB. Test with an independent outside-repository CSV, actual Windows UI and missing-library/error/cancel flows. Numerical reference tests cannot substitute for that user acceptance.

**M3 — first supported native law bridge.** Choose an available installed solver/native law and externally authored single-element or coupon deck; native FE adapter uses existing JobManager/RunStore and deterministic extractor. It may precede or replace an in-process constitutive adapter for that model. Prove parameter→native bytes→run→measured quantity→same residual contract→actual confirmation. First Prep Apply can use current LAW1 E; unsupported complex laws stay export-only. Regression test original unedited fields/INCLUDE/order/line endings and revision-conflict/one-Undo behavior. Fixed-width RAD and INP codecs have separate format tests.

**M4 — additional model adapters and research levels.** Add each material/library/solver model by schema/capability/validation tests, not core branches. Different measures/multiple paths/temperature-rate dependence/Prony terms or hyperelastic test modalities are accepted only when the registered adapter and unit/experiment contracts support them. Existing open solver laws and available libraries are preferred over reimplementation. A model may expose material-library and native-FE levels independently; cross-level agreement is an explicit study report, never assumed. Expert review reads immutable artifacts and proposes changes; it does not silently run or apply them.

Expert acceptance counterexamples required by direct review: (a) two fits have the same aggregate score but one required shear/holdout test fails; its per-test artifact must lead to revise/failed-fit, not blanket pass; (b) a Cauchy/PK2 or gauge/axis mismatch yields no fit objective and insufficient evidence; (c) a cancelled/unparsed required test is visible but not an actual fit. Read-only expert snapshots contain the detailed evaluation artifact and preserve these distinctions.


### 9. Expert evidence handoff (agreed direct boundary)

Calibration is a distinct discussion `source_kind="calibration"` in a proposed discussion v2 union, with `calibration_ref` fixing study revision/evaluation ID/content hash. It is not inserted as a manual/native-run fake. Existing discussion v1 bytes are not automatically rewritten; the explicit-save v2 migration defined in the expert section remains available; existing stored files keep their fields and canonical ArtifactRef is supplied by an adapter. The evidence snapshot uses actual descriptor/parameters for model_text, actual per-test observed/errors/coverage for summary_text, and actual log text only if provided. Missing log is empty plus unavailable status. Solver/run/frame is null where absent or multiple; top units is `{}` with unit_source=`per_artifact`, and observable-specific units remain in artifacts. Hash failures/unknown adapters/required partial/measure mismatch remain visible as insufficient or ineligible. A native-derived field keeps its native_run+derived_result_ref path instead. No experiment is converted to a dummy FE run and no current result is relabeled to bypass evidence checks.

<a id="experts"></a>

## 일반 CAE 자문·검색·도구·답변 검토


### 1. 기존 구현을 그대로 재사용할 경계

실제 기존 API는 `discussions.create_discussion(path, *, title=...)`, `load_discussion(path)`, `save_discussion(path, document, *, expected_revision)`, `attach_evidence(path, evidence, *, expected_revision)`, `ask_discussion(path, question, *, evidence_id:str, template_id:str, provider_id=None, expected_revision, check_cancelled=None, on_stage=None)`, `review_proposal(path, proposal_id, *, expected_revision, decision='check', reason='')`다.

`ExpertProvider.discuss(prompt)->str`와 availability/cancel, `Discussion.build_prompt(question)`, `knowledge.search_knowledge_result(query,limit=3,solver=None)`, `rag.HybridIndex`/`save_index`/`load_index`, `native.evidence.capture_run_evidence`/`verify_evidence`, 기존 Workbench façade와 `caelab.project.NativeProject`, `Workbench.read_native_frame(run,frame_index)`→`native.frame_view.read_native_frame`의 byte/hash 검증, FileLock/CAS/atomic_json, Qt worker/mailbox/late-result 배제, numpy/pypdf/BM25/RRF를 재사용한다. 기존 provider/discussion APIs를 무단 전면 교체하지 않고 어댑터를 추가한다. 별도 JobManager·task queue·agent framework·DB를 만들지 않는다.

현재 실제 `allowed_tools`는 자료 종류다. 새 호출 도구 이름과 혼용하지 않도록 기존 필드를 유지하되 새 계약은 `allowed_evidence_sources`(기존 allowed_tools alias)와 `allowed_query_tools`로 분리한다. 기존 OpenScience는 prompt-only·도구 비활성이고 호환 endpoint는 messages-only이다.

### 2. 문서 v2, 질문 범위, 마이그레이션

**제안 DTO Discussion v2**: 기존 v1 키를 보존하고 `question_scope: 'theory'|'case_analysis'`, `review_requests:[]`, `corpus_snapshot_ref:null`, `tool_policy_id:'cae-read-v1'`를 추가한다. 질문 범위와 첨부 유무는 독립이다. theory는 evidence 0개로 질문 가능하고 첨부가 있으면 참고할 수 있다. case_analysis는 최소 1개의 유효 첨부가 필수이며 이론·문헌 검색도 허용한다. 따라서 hybrid 질문은 case_analysis+run evidence+corpus로 표현한다.

**제안 ask 확장**:

```python
ask_discussion(path, question, *, evidence_id: str|None=None,
               template_id: str, provider_id=None, expected_revision:int,
               question_scope:str|None=None, review_policy_id='none',
               check_cancelled=None, on_stage=None) -> dict
```

기존 인자를 사용한 호출은 case_analysis로 해석한다. 새 GUI는 theory 선택 때 evidence_id=None를 허용한다. 문서의 question_scope 변경은 사용자의 다음 질문에만 적용하고 과거 turn의 scope/근거는 변경하지 않는다. `ExpertWindow._set_busy/ask`, `discussions._validate/create_discussion/save_discussion/ask_discussion`, `Discussion.build_prompt`, `Workbench.ask_expert_discussion`을 연결한다. theory 프롬프트에는 빈 가짜 native_run·수치·단위를 생성하지 않는다.

**제안 turn 추가키**: `schema_version:2`, `question_scope`, `request_context_ref`, `provider_capabilities`, `tool_trace_ref`, `retrieval_decisions:[]`, `claims:[]`, `answer_review_ids:[]`, `evidence_pack_hash`, `corpus_revision`, `budget_used`, `capability_limits:[]`. run/evidence_id는 nullable이며 null을 선택 실행으로 보충하지 않는다. 기존 turn의 `citations.verified`는 legacy ID 확인일 뿐이다. 새 화면 표시는 `citation_id_check`와 `claim_support`를 분리하며 old verified를 supported로 바꾸지 않는다.

**v2 evidence union(교차 담당 합의)**: 기존 `_EVIDENCE_KEYS`의 실제 metadata/text/files shape는 보존하고 v2에서 source_kind를 `native_run|manual|calibration`으로 검증한다. 실제 native run에서 파생된 dataset은 native_run+추가 `derived_result_ref:ResultRefV1`이며 source_run_id가 기존 run_id와 일치해야 한다. input/log 부재는 emptytext와 source availability 상태로 기록하고 원시 자료가 존재한 것처럼 채우지 않는다. `capture_result_evidence(project:NativeProject,ref:ResultRefV1,asset_root:Path)`는 이 호환 dict를 반환하되 FRD `_finish` 재판독을 하지 않는다. calibration은 `source_kind='calibration'`, `calibration_ref:{study_revision,evaluation_id,content_sha256,...CalibrationEvaluationArtifactV1}`의 명시 변형이다. summary_text는 실제 시험별 관측/오차/coverage, model_text는 실제 descriptor/parameters, log_text는 실제 있을 때만 제공한다. solver/run/frame이 복수/없으면 null, top units={}·unit_source='per_artifact'이고 각 observable 단위를 보존한다. manual의 '동일 실행 대응 미확인 사용자선택자료' 의미를 calibration에 강제로 적용하지 않는다. v1 validator/receipt를 자동 rewrite하지 않고 v2 `_validate_evidence`에서 추가 refs/schema/허용 root/identity/hash/eligibility를 검사한다. 실제 data 없는 theory 질문에는 evidence를 만들지 않는다.

v1 문서는 읽을 때 메모리 내 v2 view로 변환한다. 과거 turn은 question_scope='case_analysis', `review_status='not_requested'`, `claim_support='not_assessed'`이며 자동 LLM/색인 작업은 없다. 사용자의 첫 명시 저장에서만 v2 쓰기를 수행한다. 절차는 문서 FileLock 획득→기존 id/revision/bytes hash 확인→`assets/migrations/<v1sha>.json` exclusive create (`xb`) 및 flush/fsync(이미 있으면 bytes/hash 일치 확인)→문서 bytes/hash CAS 재확인→기존 atomic_json으로 v2 replace→원 v1 백업 유지다. 두 파일 전체가 하나의 원자 transaction인 것처럼 표현하지 않는다. 백업 완료 후 본문 쓰기 실패는 v1 문서와 백업을 모두 남기고, CAS 불일치면 새 v2 write를 하지 않는다. 미래 schema>2, 손상 JSON, 잘못된 상대 경로는 기존 DiscussionFormatError로 거부하고 덮어쓰지 않는다. migration 완료를 실제 답변/검토 완료로 표시하지 않는다.

### 3. Corpus manifest와 재현 가능한 색인

**제안 파일** `caelab/assist/corpus.py`, `scripts/build_knowledge_index.py`. 기존 pypdf와 parsers/embedding/index writer를 사용하며 외부 절대경로 build script 의존을 대체한다. 원문 PDF/모델 가중치는 Git에 넣지 않고 manifest만 추적한다. 무료 공개/사용자 소유 로컬 자료를 사용하고 다운로드는 별도 명령이며 질문 보내기로 자동 구매·대형 다운로드하지 않는다.

`CorpusManifest v1`:

```json
{"schema_version":1,"corpus_id":"cae-theory-and-solvers","revision":"sha256:...",
 "sources":[{"source_id":"solver-version-manual","source_kind":"official_manual|paper|reviewed_note|user_document",
 "title":"...","edition":"...","publisher":"...","uri":"https://...",
 "local_relative_path":"manuals/xxx.pdf","sha256":"...","byte_length":123,
 "license":{"id":"SPDX-or-unknown","source_uri":"...","redistribution":"allowed|restricted|unknown"},
 "solver_scope":["general","radioss"],"model_families":["plasticity"],
 "parser":{"id":"pypdf_outline","version":"..."},"extraction_quality":"reviewed|partial|unreviewed"}],
 "embedding":{"provider":"local_endpoint|hashing","model_id":"...","model_digest":"...","dimensions":768,"query_instruction_sha256":"..."},
 "chunking":{"version":"cae-chunks-v2","max_chars":1400,"overlap":150},
 "reranker":{"model_id":"...","digest":"..."},"files":{"chunks_sha256":"...","vectors_sha256":"..."}}
```

원문 정체성과 redistribution 판단은 서로 다르다. license unknown은 로컬 색인을 불법이라고 단정하지 않지만 배포할 자산 묶음에서 제외하고 사유를 보존한다. 논문/수기노트와 공식 solver 설명서를 같은 provenance로 표시하지 않는다.

`Chunk v2`는 기존 source_id/chunk_id/heading_path/text/solver에 `document_sha256, edition, page_start/end(null 허용), section_path, char_span, extraction_quality, source_kind, measure_tags`를 추가한다. PDF에서 실제 page별 추출 위치를 따라 chunk span을 만들고 수식/표 추출 손상은 partial로 표시한다. page를 모르면 null이며 추정 page를 발명하지 않는다. ID는 source_id+document hash+page/span+chunking version의 안정 digest로 만든다. citation은 `source_id:chunk_id` 호환을 유지하되 원문 위치 링크는 별도 메타데이터로 제공한다.

build command(제안): `python scripts/build_knowledge_index.py --manifest ... --output ... --verify-only`와 `--build`. manifest bytes와 source hashes를 먼저 검사하고 임베딩 모델/dim이 기존 index와 다르면 명시 실패한다. 같은 volume의 새 임시 디렉터리에 chunks/vectors/manifest를 쓰고 파일 flush/fsync·해시 검증 후 **존재하지 않는** `indexes/<revision_digest>/`로 rename한다. 이미 같은 digest dir이 있으면 전체 manifest/file hash가 일치해야 재사용한다. `current.json.tmp`에 완성 digest와 manifest SHA를 쓰고 flush/fsync 후 `os.replace`로 작은 pointer 파일만 교체한다. 기존 nonempty directory를 Windows에서 replace하려 하지 않는다. reader는 한 번 읽은 pointer의 immutable dir을 계속 사용한다. 실패/취소는 pointer를 변경하지 않고 자기 temp만 정리하며 이전 index·타인의 build를 삭제하지 않는다. 질문 도중 corpus revision 변경은 해당 요청에 적용하지 않고 다음 질문에만 사용한다. legacy index는 `provenance=legacy_unknown` 읽기는 허용하되 실품질/정밀출처 평가에서 제외하고 build 필요를 표시한다.

### 4. 관련도와 근거 충분성

기존 `search_knowledge_result`의 호환 status는 유지하고 **제안 내부 RetrievalDecision v1**을 추가한다:

```json
{"status":"supported|insufficient|out_of_scope|unavailable|error",
 "query_id":"...","corpus_revision":"...","scope":{"solver":"general|...","topic":"..."},
 "candidates":[{"citation_id":"...","bm25":0.0,"vector":0.0,"rerank":0.0}],
 "accepted_ids":[],"required_evidence":[],"missing_evidence":[],
 "reason_codes":["NO_SOLVER_CORPUS|TOPIC_UNCOVERED|EXTRACTION_INCOMPLETE|RELEVANCE_BELOW_PROFILE|MEASURE_UNSPECIFIED"],
 "adequacy_profile_id":"cae-relevance-v1","backend":"hashing|local-model"}
```

solver 범위와 general theory 범위를 명시해 선택한 solver 질문에 다른 solver의 카드 식을 섞지 않는다. general 자료는 구성식 이론에는 쓸 수 있지만 특정 solver default의 근거로 쓰지 않는다. BM25/vector/rerank는 후보검색이며 adequacy 판정과 분리한다. 완전한 수학적 근거 충분성을 검색 점수만으로 확정하지 않는다. 여기서 supported는 아래 결정 절차상 검색 근거 후보를 확보했다는 뜻이며 claim support/pass를 뜻하지 않는다.

**첫 adequacy 알고리즘(제안, 구현 선택 고정)**: manifest에 검토된 `domain_aliases, model_aliases, solver_aliases, topic_aliases`를 추가한다. 사용자 지정 solver/edition/model/measure facets를 우선하고, manifest chunk의 `coverage_facets:{solver_ids,editions,model_ids,topics,stress_measures,strain_measures,missing_sections}`를 검토해 채우며 unknown은 일치로 처리하지 않는다. 미지정 값은 NFKC·casefold 후 공백/기호 정규화한 질문에서 **longest exact alias match**로만 추출한다. 둘 이상 상충하면 `AMBIGUOUS_FACET` insufficient다. LLM으로 검색의 합격 여부를 판정하지 않는다. query와 문서 본문은 기존 rag.tokenize를 재사용한다. IDF의 없는 단어도 분모에 포함해 무관한 질의의 rare terms를 버리지 않는다.

```python
## N=corpus chunk수, Q=set(tokenize(query)), T(c)=set(tokenize(heading+text))
weight(t) = log(1 + (N + 0.5)/(df(t) + 0.5))
coverage(c) = sum(weight(t) for t in Q & T(c)) / sum(weight(t) for t in Q)
## Q가 비면 insufficient. score는 순위/코사인과 구별된 lexical coverage.
pool = [c for c in ranked_top20
        if solver_scope_matches(c, facets)
        and explicit_edition_model_measure_match(c, facets)
        and c.extraction_quality == 'reviewed']
if conflicting_facets: insufficient('AMBIGUOUS_FACET')
elif not exact_domain_alias(query) and max_coverage_all < tau:
    out_of_scope('NO_DOMAIN_OR_RELEVANCE')
elif not pool: insufficient('FACET_OR_SOURCE_MISSING')
elif max(coverage(c) for c in pool) < tau: insufficient('RELEVANCE_BELOW_PROFILE')
else: supported(top5_by_existing_rank(c for c in pool if coverage(c) >= tau))
```

자료가 general scope일 때 solver-specific facet 충족으로 계산하지 않는다. model·edition·measure가 명시 요구됐는데 metadata가 unknown이면 일치로 처리하지 않는다. 추출 손상/수식 누락은 reviewed가 아니므로 해당 항목의 근거로 승격하지 않는다. 미지정 facet은 사용자가 요구하지 않은 정보를 새 필수조건으로 강제하지 않는다. aliases는 고정 corpus manifest에서 검토하며 임의 새 모델 지원을 발명하지 않는다.

`tau`는 evaluation과 겹치지 않는 36개 개발질문(관련/부족/범위밖 각12)에서 grid `{0.10,0.15,...,0.90}`를 순서대로 평가한다. 손실은 `3*부족질문의_supported + 4*범위밖_supported + 관련질문의_기각`으로 고정한다. 최소 손실→관련질문 Hit@5 최대→tau가 큰 것 순으로 하나를 정한다. backend(model/index/query tokenizer version)가 바뀌면 새 profile/version을 만들며 evaluation 질문을 보고 조정하지 않는다. 이 알고리즘의 범위는 고정 corpus·alias·평가 질문이며 모든 언어의 recall이나 모든 구성식 충분성을 보장하지 않는다.

insufficient/out_of_scope도 질문 자체를 전부 차단하지 않는다. provider가 범위를 설명하거나 필요한 데이터/문헌을 제안할 수 있지만 출처 없는 구체적 수치·카드 변경은 검증된 제안으로 승격하지 않는다. 학습 지식만의 일반 설명은 `basis='model_general_unverified'`로 구분하고 citation을 만들지 않으며 answer_review는 supported/pass가 될 수 없다. 사용자에게 매번 장문의 경고를 붙이지 않고 자료 상태 한 줄과 해당 주장별 근거 상태로 표시한다.

**첫 품질 gate**: 별도 검토자가 원문을 읽고 고정한 60질문(관련 24, 자료부족 18, 비CAE/범위밖 18). 관련 24에는 일반 연속체/요소·접촉 8, 여러 재료군 8, solver 구현 8을 포함한다. DP/Xia 외 J2·점탄성·초탄성·직교이방·파손 등 해당 corpus로 답할 수 있는 사례를 섞고, 존재하지 않는 지원은 자료부족에 둔다. 같은 corpus로 BM25/hybrid를 각각 평가한다. Hit@5>=22/24, 자료부족 판정>=17/18, 범위밖 기각18/18, 관련질문 오기각<=2/24, 잘못된 solver 근거0을 최소 gate로 한다. development/evaluation 자료 SHA를 분리한다. 이 소규모 gate는 일반 CAE 답변 정답률이 아니다.

live provider 답변은 별도 12질문+근거부족 6질문의 원문 claim 검토를 수행한다. 허위 citation0, 기존 결과에서 발명한 수치0, 자료부족을 pass로 승인0, 독립 검토자 기록이 있어야 첫 slice 완료다. 공급자 미구성은 미실시로 남긴다.

### 5. 읽기 도구: 첫 slice의 정확한 schema와 adapter

첫 slice는 `knowledge.search`, `evidence.describe`, `evidence.query_field` 세 도구뿐이다. Prep/calibration은 아래 계약 준비까지 `capability='unavailable'`이며 도구 schema에 노출하지 않는다. arbitrary shell/Python/파일 경로/SQL/HTTP/자유 수식은 받지 않는다. 모든 args는 JSON Schema `additionalProperties:false`, finite number만 허용한다. 모델은 UUID/handle을 새로 만들지 않고 호스트가 제공한 ID 목록에서 선택한다.

**제안 공통 envelope** `ToolCall v1={call_id,tool_name,arguments}`와 `ToolResult v1={call_id,status:'ok|partial|error',data,error:{code,message,retryable}|null,evidence_refs:[],provenance:{request_context_hash,corpus_revision,source_hashes,run_id,frame_index,units,basis,measure},limits:{truncated,valid_count,total_count},elapsed_ms}`. provider-native call_id를 기록하되 호스트 trace ID와 구분한다.

| 이름 | args | 반환 data/필수 거부 |
|---|---|---|
| knowledge.search | `{query:str(1..2000),solver_scope:'general'|허용 solver ID,limit:int(1..5)}` | RetrievalDecision+허용 chunk의 text/원문page/section/hash. frozen corpus 밖·잘못된 solver·index mismatch는 거부 |
| evidence.describe | `{evidence_id:호스트가 제공한 ID}` | frozen evidence metadata, 원 run/frame, source hashes, 지원 field/component/association/layer/basis/measure 목록, available operators. 다른 discussion evidence·현재 외부 원본으로 대체는 거부 |
| evidence.query_field | `{evidence_id,field_id,component_id,selection_id:'all'|사전등록 selection ID,operator:'values'|'min'|'max'|'mean'|'norm',operator_version:'1',limit:1..1000}` | deterministic 값/집계와 valid/total/selection count, ids·unit·basis·measure·coverage. frame은 evidence에 고정되어 args로 바꿀 수 없음. 자유 수식·미지원 operator·미등록 selection·단위/축이 필요한 연산의 unknown은 거부 |

단위 미선언 raw values 조회는 `unit_status=unknown`을 보존하며 수치 환산/물리 비교/잔차 적합 대상으로 쓰지 않는다. '파일에 기록된 원시 수가 1이다/단위가 미선언이다'라는 파일 사실 claim은 검토할 수 있으나 그것을 mm·MPa 또는 물리적 충분성으로 해석한 claim은 pass 불가다. review eligibility의 숫자 의미 해석과 파일 사실 확인을 분리한다.

첫 slice의 operator 의미를 고정한다. values/min/max/mean은 **선택된 저장 point scalar 또는 한 component**에만 적용하며 mean은 유한 절점값의 산술평균으로 체적/면적 적분이나 절점 가중 평균이 아니다. norm은 공간집계가 아니라 각 절점에서 선언된 **동일 단위 3성분 벡터의 Euclidean magnitude**다. norm은 `component_id='vector'`, adapter가 선언한 exactly3 component labels·동일 unit·known orthonormal basis에서만 허용한다. unknown/mixed unit, unknown/nonorthonormal basis, tensor, 다른 component_id이면 unsupported를 반환한다. norm 결과를 다시 mean/min/max하는 중첩 연산은 첫 slice에 없다. overflow/nonfinite는 missing으로 두고 count를 보존한다. missing을 0으로 채우지 않는다. `evidence.query_field`는 원 결과 배열에서 추출하되 `geometry_state`와 `Displacement` 관계를 명시해 reference/deformed를 중복 변형하지 않는다.

**공통 참조(제안, root 단일 소유 `caelab/artifact_refs.py`)**: ArtifactRefV1은 `{id,role,path(relative),sha256,size,format_id,producer:{id,version}}`다. path는 호스트 소유 store root 기준 상대값이고 resolver가 root를 소유한다. LLM에는 artifact/evidence IDs만 노출한다. provenance의 `artifact_owners:{artifact_id:{scope:'run|operation|consumer',id,source_run_id}}`를 host ProjectContext resolver가 해석한다. evidence/selection/corpus는 scope를 추가하지 않고 아래 consumer id/root registry에 등록한다. ArtifactRef.path는 등록 root 아래 상대경로다. operation scope에는 source_run_id가 필수이며 scope/id/path는 tool args로 받지 않는다. frozen context에 등록된 artifact_id만 허용하고 상충 owner의 동일 ID는 conflict다. resolve/symlink root escape와 read중변경을 거부한다. existing evidence.files `{stored_path,sha256,size,role}`는 adapter로 참조를 제공하며 과거 receipt는 자동 rewrite하지 않는다. 최종 scope enum은 오직 run/operation/consumer이고 여러 enum안을 동시에 허용하지 않는다. EvidenceEnvelopeV1 `{id,kind,schema_version,content_sha256,provenance,availability,payload}`의 digest는 `content_sha256` 자신을 제외한 canonical UTF-8 JSON bytes of the entire validated envelope (including provenance, artifact_owners, availability and payload, omitting only content_sha256)의 SHA256이다.

최종 owner registry(제안 공통 정본, 모델/툴이 root를 지정하지 않음):

| scope | owner id | 호스트가 등록하는 정확 root |
|---|---|---|
| run | source_run_id | 해당 RunStore의 run.workdir |
| operation | operation_id (source_run_id 필수) | RunStore.root/.derived/<source_run_id>/<operation_id> |
| consumer | evidence:<discussion_id> | 해당 discussion 파일 parent/assets |
| consumer | selection:<selection_id> | RunStore.root/selections/<selection_id> |
| consumer | corpus:<corpus_revision> | corpus registry의 immutable indexes/<revision_digest> |
| consumer | material-study:<study_id> | material service가 등록한 해당 study의 artifact store root |
| consumer | post:<launch_id> | RunStore.root/.post/<launch_id> |

공통 owner validator는 scope별 id 문법을 구분한다. run/operation은 기존 저장소 ID 검증을 사용하고 consumer는 UUID 강제가 아니라 허용 namespace prefix+stable ID의 등록키 문자열이다(예: evidence:<discussion_id>, corpus:sha256:<digest>). consumer id는 경로로 해석하지 않고 정확 registry lookup만 수행한다. 등록되지 않은 prefix/key, NUL·경로구분자·상충 owner는 거부한다. registry는 ProjectContext가 소유하며 같은 consumer id를 다른 root로 재정의하면 conflict다. SaveAs는 등록 resolver root만 옮기고 frozen artifact bytes/receipt를 rewrite하지 않는다. 추가 기능도 새 scope를 임의로 만들지 않고 정본 변경 없이 consumer namespace를 등록한다.

result kind payload는 post 담당과 합의한 **단일 ResultRefV1**을 사용하고 별도 numerical DTO를 만들지 않는다. 필수 의미는 source_run_id, frozen source receipt ArtifactRef hash, `source_artifacts:[ArtifactRefV1]`, dataset_manifest_sha256, operation_id/operation_manifest_sha256, dataset_id/frame_id/field_id, selection artifact/revision, component/location(association/IP/layer), basis(kind/reference/orientation_sha256), stress/strain measure·shear_convention, units(symbol/dimension/scale_to_si/출처·unknown), geometry_state/displacement 관계, operator(id/version/params), coverage(expected/valid/missing), eligibility(view/query/fit/review/reason_codes)다. source receipt는 operation 준비 때 원 job.json exact bytes를 `inputs/source_receipt.json`으로 복사한 immutable ArtifactRef다. source_receipt_sha256는 그 frozen bytes hash이며 현재 job.json 전체 hash를 비교하지 않는다. reopen은 frozen receipt/artifacts와 현재 receipt의 immutable identity(id,node,solver,input_revision,input/execution/native-artifact hash set)만 대조한다. current stale/stale_reason은 별도 flag로 보존해 historical read와 구분한다. SaveAs는 frozen bytes를 바꾸지 않고 root resolver만 이동한다. multi-frame/PVD는 artifact목록과 dataset manifest 전체를 고정하고 latest operation 자동대체를 금지한다. eligibility.review는 리뷰 입력 적격성이며 최종 답변 승인과 다르다.

**첫 query 구현 범위**는 기존 writer로 만든 저장 VTU의 point(node) association·실제 scalar 또는 명시 component·고정 frame뿐이다. cell/IP/shell-layer/basis transform·임의 compressed VTU 전체는 capability에 없으면 unsupported다. 현 core에 공개 read_vtu가 없으므로 `native_result_view._read_grid`라는 Qt GUI private 함수를 import하지 않고 post 담당의 검증된 dataset adapter를 소비한다. 첫 read는 기존 `Workbench.read_native_frame`의 run/frame/hash 검증 bytes를 재사용하고 optional dataset reader만 값을 추출한다. core는 Qt/PySide6를 import하지 않으며 optional reader가 없으면 query_field unavailable이다. 새 result capture는 CalculiX FRD 재판독 전용인 `_finish`에 모든 solver 분기를 넣지 않고 제안 `capture_result_evidence(project:NativeProject,ref:ResultRefV1,asset_root:Path)`에서 검증된 dataset을 snapshot한다.

RP/ParaView는 표시 consumer이고 추출 소유는 WB deterministic native result adapter다. 'Strain' 이름만으로 strain measure/shear를 정하지 않는다. material basis를 RP가 이미 지원한다고 가정하지 않는다. basis 변환은 검증된 orientation/basis/operator가 있는 경우만 허용한다. partial도 필드·selection coverage가 검증되면 제한 query를 허용하지만 curve_residual/claim_approval은 기본 false이며 필요한 measure/time/entity 범위가 완전하고 검증된 capability일 때만 별도로 true가 된다. native parser가 지원하지 않은 binary field는 unavailable이다.

**후속 Prep adapter**: 기존 `LiveClient.request('document.describe'|'document.cards'|'document.properties',...)`만 허용한다. live 읽기 시작/끝 document_id/epoch/revision이 같아야 snapshot을 채택하고 이후 전문가는 snapshot만 읽는다. 원본 조회와 validation을 구분하고 typed card validator가 아직 없으면 검사를 만들어 성공이라 하지 않는다. `prep.edit/save/mesh_start/undo` 등 변경 도구는 노출하지 않는다.

**후속 calibration adapter**: material 담당의 CalibrationEvaluationArtifact v1만 읽는다. experiment revision/split, observed/predicted/residual curve refs+SHA, 시험별 loss·가중치/normalization/interpolation/valid range, quantity·measure/frame/axes/IP/layer/unit, physical/encoded parameter, actual/predicted 상태, solver/parse/convergence, identifiability/uncertainty를 읽는다. 동일 총점이나 holdout/전단시험 실패는 revise, measure mismatch는 insufficient이며 objective 미생성, cancelled/partial invalid는 NOT_ASSESSED다. 검증된 선택범위 partial query가 곧 보정 적합 승인은 아니다.

### 6. Provider adapter, bounded loop, 취소와 기록

**제안 `ProviderCapabilities v1`**: `{provider_id,model_id,transport_mode:'native_tools|structured_action|prompt_only',supported_tool_schema_version,max_context,configured,probe_status,last_probe_at}`. 설정 확인과 실제 probe 성공을 분리하며 probe도 사용자 명시 동작이다. OpenAICompatibleProvider native chat/completions는 `tools` JSON schema와 assistant.tool_calls/tool role 결과를 처리한다. endpoint가 tools를 거부하면 자동으로 조용히 다른 경로로 바꾸지 않고 `unsupported_capability`를 표시한다. 함수 없는 모델을 native tools 지원으로 발표하지 않는다.

**OpenAI-compatible native name mapping**은 canonical ID와 별도다. 공식 Chat Completions function.name은 `[A-Za-z0-9_-]{1,64}`로 제한되므로 dotted canonical 이름을 그대로 보내지 않는다. 고정 bijection은 `knowledge.search↔knowledge_search`, `evidence.describe↔evidence_describe`, `evidence.query_field↔evidence_query_field`다. schema 등록/응답 alias 역매핑/trace가 같은 versioned map SHA를 사용한다. 미등록 alias, 충돌 alias, 길이초과는 provider 요청 전에 거부한다. 다른 endpoint도 native 이름제약을 검사하며 dotted 이름을 지원한다고 가정하지 않는다. 근거: https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create (FunctionDefinition.name, 2026-11-12 확인).

기존 OpenScience의 보안 설정은 그대로 도구 비활성이다. 첫 연결은 prompt_only 유지. 선택적인 structured_action adapter는 제안 JSON `{type:'tool_request',tool:...,arguments:...}` 또는 `{type:'final',answer:...}`를 검증해 호스트 allowlist dispatcher가 실행하고 결과를 다음 격리 discuss 요청에 넣는 방식이다. 이는 provider-native function call과 구별해 표시/기록한다. 이 adapter가 구현·실구독 검증되기 전에는 unavailable이다. OpenScience plugin/MCP/shell 권한을 전면 허용해 구현하지 않는다.

**제안 루프** `run_grounded_request(context, provider, dispatcher, budget, cancelled)->GroundedReply`는 단계별 provider→validated action→dispatcher→tool result→provider만 수행한다. max_provider_rounds=4, max_tool_calls=6(한 round 최대3, 첫 slice는 순차), max_prompt_chars=60000, max_tool_text_chars=20000, max_result_values=1000, provider_seconds=60(합계), preparation_seconds=20, overall_seconds=85를 기본으로 한다. tool별 파일 읽기/검사도 남은 전체 기한 내에서 checkpoint하며 임베딩/rerank HTTP timeout을 남은 예산 이하로 전달한다. 기존 600초 default를 그대로 이 루프에 쓰지 않는다. 검토 요청은 별도 명시 요청으로 동일 예산을 가지며 질문 하나를 몰래 두 배 청구/요청하지 않는다. 자동 review_policy 선택 시 UI가 proposer+reviewer 두 요청임을 보여주고 합계 budget은 명시 설정한다.

도구 오류는 `invalid_arguments|out_of_scope|stale_context|integrity_error|unsupported_field|unknown_measure|unknown_unit|incomplete_coverage|source_unavailable|provider_unavailable|unsupported_capability|timeout|cancelled|budget_exhausted`로 구조화한다. retryable일 때도 모델의 무한 재시도를 허용하지 않는다. 취소/timeout 후 새로운 provider/tool을 시작하지 않고 늦은 응답은 기존 request/document ID/CAS 검사를 통해 폐기한다. blocking urllib 자체가 완전히 종료됐다고 단정하지 않고 관리 상한·transport 정리 상태를 별도로 기록한다.

trace는 append-only `assets/requests/<request_id>/trace.json`과 sha를 대화 turn에 연결한다. 원 데이터는 기존 assets 보존을 재사용하고 tool result의 잘린 표시와 전체 bytes/hash를 구분한다. 토큰/API key/private MCP descriptor/cancel capability는 저장하지 않는다. provider/model/version, exact tool schema/policy SHA, 요청·응답 해시, 단계·기한·오류, 실제 호출 횟수·사용 가능한 usage만 기록하며 없는 usage를 추정 비용으로 채우지 않는다. UI는 사람이 읽는 자료/행동/오류 요약을 보여주고 내부 ID·dump를 기본 화면에 노출하지 않는다.

### 7. 제안자와 답변 검토자

기존 `review_proposal(check/reject)`은 그대로 사용자 제안 파일/출처 검토로 유지한다. **제안 새 API** `request_answer_review(path, *, turn_id, reviewer_provider_id, expected_revision, check_cancelled=None)->dict`와 `AnswerReview v1`을 추가하며 구조안전/재료 유일성 승인을 의미하지 않는다.

`RequestContext v1`은 question text/scope/constraints, frozen evidence IDs+hashes, corpus manifest revision, 실제 제공 chunks+hashes, tool schema/policy, solver/model/measure limits를 canonical JSON hash로 고정한다. proposer tool 조회 종료 때 final EvidencePack을 freeze한다. reviewer는 같은 frozen evidence/chunks/이미 가능한 deterministic field 범위만 읽는다. 새 문헌/다른 frame이 필요하면 `insufficient:additional_evidence_required`로 요청하며 몰래 근거를 바꾸지 않는다. 새 근거는 다음 proposer turn과 새 review 요청으로 연결한다.

`AnswerReview v1={review_id,source_turn_id,request_context_hash,proposer:{provider,model,request_id},reviewer:{provider,model,request_id},independence:'separate_request_same_model|separate_model',verdict:'pass|revise|insufficient',status:'pending|completed|failed|timed_out|cancelled|interrupted',claim_checks:[{claim_id,text_span,citation_ids,id_valid,quoted_span_valid,support:'supported|contradicted|not_assessed',reason}],modeling_checks:[{category:'assumptions|units|measures|BC|mesh|contact|convergence|interpretation',result,reason,evidence_refs}],disagreements:[],required_evidence:[],limitations:[],created/finished_at}`.

리뷰어는 제안자의 결론을 지시로 따르지 않고 claim과 frozen evidence를 대조한다. 허용 citation ID와 quote 위치 존재는 코드가 검사하지만 실제 support는 별도 reviewer 판단이고 인간 검토 없음이면 그 사실도 명시한다. 같은 모델을 별도 요청한 것은 실제 다른 인간 전문가의 독립 심의가 아니다. pass는 현재 답변을 제공 근거 안에서 통과시킨 answer_review이며 안전성·정확도·모든 하중조건 승인으로 승격하지 않는다. 실패/timeout/cancelled는 verdict=insufficient(진행중은 null), 기록 삭제/이견 덮어쓰기 없이 보존한다.

사용자가 모델 변경을 수용하면 기존 전용 프로그램/Prep Apply 명령에서 revision을 확인해 수행한다. LLM tool loop에는 모델 편집·solver submit을 넣지 않는다. 재해석은 기존 JobManager·사용자 명령이며 새 run evidence는 새 turn으로 첨부하고 old verdict는 새 run에 적용하지 않는다.

### 8. 파일 단일 편집 소유와 구현 단위

| 단위/소유 | 허용 파일(제안 새 파일은 명시) | 첫 입력→출력 / 종료 검사 |
|---|---|---|
| E0 기존 frame/첨부 결함, WB GUI 담당 | 기존 apps/workbench_qt/expert_window.py, apps/workbench_qt/native_analysis.py, tests/test_expert_window.py/tests/test_native_analysis_expert_bridge.py | frame0 초안→frame1 첨부·frame 전체 소실·사용자 초안 변경. 아래 고정동작표로 전달값/기본선택/전송차단 확인. #45 표시·닫힌창 늦은 결과 회귀 유지 |
| E1 v2 scope/저장, WB assist 담당(공유문서 façade root 통합) | 기존 discussions.py/experts.py, 제안 contracts.py, 기존 Workbench façade, 위 GUI scope 부분은 GUI 담당 순차 반영 | v1 읽기·무호출→theory 질문(0run)→저장/재열기→case+corpus hybrid→취소·재열기. 원 v1 hash 보존/CAS·미래schema 거부 |
| E2 corpus/adequacy, WB knowledge 담당 | 기존 knowledge.py/rag.py/KNOWLEDGE_SOURCES.md, 제안 corpus.py/scripts/build_knowledge_index.py, tests/test_assist_rag.py/test_rag_evaluation.py | 로컬 manifest+PDF/text→versioned chunks/vectors→동일 source/corpus hash 재현·검증. 위 60문항 gate와 DP/Xia/비CAE 반례. partial 수식·모델 mismatch 거부 |
| E3 tools/providers, WB assist 담당 | 제안 query_tools.py/provider_steps.py, 기존 experts.py/openscience.py/discussions.py, 기존 native/evidence.py의 어댑터 진입만 post 담당과 계약 후 root 단일 편집 | 3도구 exact schema→native-compatible tool 왕복→최종 답변/trace. malformed/unknown tool·path·stale·budget·late response. OpenScience prompt-only와 structured 경로 구분 |
| E4 answer review, WB assist 담당 | 제안 answer_review.py, 기존 discussions.py/expert_chat.py/expert_window.py의 순차 표시 연결 | 동일 pack proposer/reviewer→supported/contradicted/근거부족·same-model 표시→이견·실패 저장/재열기. 사용자 check/reject 기존 회귀 보존 |
| E5 Prep/calibration 연결, 각 소유자 | Prep 기존 live read API/typed validator는 Prep 담당, CalibrationEvaluationArtifact는 material 담당, WB bridge만 assist 담당 | 실제 immutable snapshot→도구 capability 등록. 아직 미구현인 adapter는 목록에서 unavailable. 원본 편집·새 job 생성0 |

E0가 선행이며 E1/E2의 DTO를 확정한 뒤 E3/E4를 잇는다. discussions.py·experts.py 같은 파일을 담당이 동시에 편집하지 않는다. 첫 slice는 E0→E1/E2→세 read 도구 E3→review E4이며 Prep/보정 read 도구는 해당 artifact가 실제 준비된 후 추가한다. 각 slice는 작성자와 별도 QA가 같은 commit에서 사용자 진입점+파일/상태 전달을 확인한다. 모의 provider 계약 검사, 실제 local/native HTTP tool 왕복, 기존 Windows 구독 호출, Qt offscreen/OS 실화면을 별도 결과로 기록한다.

#### E0 함수·상태 전환 고정

현재 실제 constructor는 `ExpertWindow(discussion_path=None, *, project_path=None,node_id=None,run_id=None,workbench=None,view_settings=None)`이고 native_analysis.py `request_expert_debate()`는 frame을 window key/초안에는 넣지만 constructor에 전달하지 않는다. expert_window.py `capture_run()`은 `summary.frame_index`를 기본 선택해 불일치할 수 있다. **제안 constructor 확장**은 기존 kwargs 뒤 `requested_frame_index:int|None=None, frame_context_state:'explicit'|'no_results'|'unspecified'='unspecified'`다. request_expert_debate는 현재 run/result selector의 실제 int0도 `is not None`으로 전달하며 frame이 없으면 no_results로 전달한다. 저장한 run/frame·초안의 origin과 scope는 `draft_context`에 기록하고 사용자가 수정하면 `draft_origin='user'`를 보존한다.

capture는 기존 `Workbench.capture_expert_run(run, assets, *, frame_index=None)`→`native.evidence.capture_run_evidence(run,asset_root,frame_index=...)`를 그대로 사용한다. 실제 frame/입력로그 option 선택과 기존 가져오기 OK가 사용자 의사다. 추가 checkbox·문맥확인 승인·초안본문의 자동 일치판정은 만들지 않는다. 취소/실패는 현재 첨부/초안을 바꾸지 않는다.

| 상태 | capture dialog 기본값·문맥 라벨 | OK/보내기 조건 |
|---|---|---|
| 전달 run/frame 존재(0포함) | 그 정확 frame 선택. '전달 기준: 실행 이름/단계/증분/시간' | terminal run+exact frame이면 기존OK. 검증/첨부완료 후 유효첨부면 보내기 |
| 전달 frame 사라짐, 다른 frame 존재 | '전달 프레임 기록 없음 — 직접 선택 필요' placeholder. summary/마지막 frame 자동선택 금지 | placeholder일때 OK비활성. 사용자가 실제 frame을 선택하면 기존OK활성. 별도확인없음 |
| 전달 frame을 포함해 모든 결과 frame 없음 | placeholder와 실제 '입력·로그만 첨부' option을 제공(초기 placeholder) | 사용자가 입력·로그 option 선택 후 기존OK. capture frame_index=None, 생성요약은 결과수치없음, '입력·로그만' 라벨. 추가checkbox없음 |
| 다른 run/frame을 사용자가 선택 | '초안 작성 기준: … / 현재 첨부 기준: …' 한줄과 별도 현재자료 요약을 갱신 | 유효첨부면 Ask허용. 사용자 초안은 항상 보존하고 혼합 theory/비교질문을 차단하지 않음. prompt에는 초안기준과 실제 첨부근거를 분리전달 |
| 자동 생성 초안이 아직 사용자 편집 전 | generated context가 바뀌면 새 frame의 deterministic 요약으로 대체하고 이전 generated draft는 draft_history에 보존 | 초안의 사용자편집이 있으면 자동대체0. 별도 '현재자료요약 삽입'은 선택행동으로 제공가능 |
| 사용자가 이미 편집한 초안 | draft_origin=user, 질문원문 유지. 생성요약은 질문과 별도표시·갱신 | 사용자본문 의미의 자동판정이나 전송차단0. 자료손상/미선택/공급자미구성/검증중 같은 기존 실행조건만 적용 |
| standalone/legacy unspecified | 전달 frame 없음 라벨, 사용자가 실제 frame/입력로그option을 고르는 기존dialog | 자동 run대체0. 실제 선택 후 해당context를 기록. 사용자초안보존 동일규칙 |

draft_history는 v2의 append-only 원문 보존이며 old turn/evidence를 수정하지 않는다. no run 일반 질문은 theory scope로 시작하고 placeholder run을 만들지 않는다. 새 기준을 보여주는 표시가 사용자의 초안을 자동수정/삭제하거나 승인동선을 추가하는 핑계가 되지 않아야 한다.

### 9. 교차 담당 비판 수용

- material 담당의 scalar-only 계약에 대해 시험별 잔차/measure/보류시험이 심의에 필요하다고 비판했다. 상대는 CalibrationEvaluationArtifact v1을 추가하고 총점 동일/전단 실패, measure mismatch, cancelled·partial 3반례를 반영하기로 수용했다.
- RP 담당 비판 5개(unknown basis/strain measure/shear, geometry 중복warp, versioned deterministic 집계와 coverage, partial query와 objective eligibility 분리)를 5절에 반영했다. RP/ParaView 소비자, WB 추출 소유를 고정했다.
- user_progress QA의 hybrid 질문·첫 도구 범위·review 표현·frame 선행결함·exact GUI 함수·numeric quality/migration/cancellation 6비판을 2/4/5/6/7/8절에 반영했다.

### 10. 유한 JSON fixture와 첫 도구 수치 oracle

아래는 **합성 계약 fixture**이며 실제 solver 실행 결과/성능 실측이 아니다. 내부 VTU test array의 IDs `[10,20,30]`, 값 `[1,NaN,3]`는 JSON snapshot에서 values `[1.0,null,3.0]`와 validity `[true,false,true]`로 표현하고 실제 nonfinite payload는 reader가 mask한다. 결측점을 빼고 분모를 바꾸었다는 사실을 반드시 count로 보고한다.

유효 tool request JSON:

```json
{"call_id":"call_mean_1","tool_name":"evidence.query_field","arguments":{"evidence_id":"01010101010101010101010101010101","field_id":"DISP","component_id":"D1","selection_id":"all","operator":"mean","operator_version":"1","limit":1000}}
```

유효 tool result JSON(정책상 nullable는 실제 null, NaN/Infinity 없음):

```json
{"call_id":"call_mean_1","status":"partial","data":{"value":2.0,"association":"node","component":"D1","statistic":"arithmetic_finite_node_mean","unit":{"symbol":"mm","dimension":"length","status":"declared"},"coverage":{"expected":3,"valid":2,"missing":1}},"error":null,"evidence_refs":["01010101010101010101010101010101"],"provenance":{"request_context_hash":"be7e62de03c318ae671d7a4ef2dfdda611c544f09aa51cddf0a825d750c33a77","corpus_revision":null,"source_hashes":["5e7290127c0a7fad2ee0f746bc09e74299b8e31e2bbd8f554eb4862e384247b5"],"run_id":"fixture-run-1","frame_index":0,"units":{"length":"mm"},"basis":{"kind":"global","reference":"fixture-global","orientation_sha256":null},"measure":"displacement"},"limits":{"truncated":false,"valid_count":2,"total_count":3},"elapsed_ms":1}
```

fixture의 context/source SHA는 각각 이 절에서 정의한 고정 context JSON bytes와 합성 배열 JSON bytes를 실제 SHA256 계산한 유효64hex다. 합성 source format은 test-fixture-json-v1이며 native solver artifact로 등록하지 않는다. 실제 native evidence는 source_artifacts+dataset manifest hash가 없으면 integrity_error다. 아래 실서비스형 golden fixture는 실제 생성 VTU bytes/manifest/context SHA로 값을 대입해 검증한다.

정확한 합성 bytes와 hash 재현 recipe(마지막 LF 없음):

```python
import hashlib
source_bytes = b'{"ids":[10,20,30],"values":[1.0,null,3.0],"validity":[true,false,true]}'
context_bytes = b'{"evidence_id":"01010101010101010101010101010101","frame_index":0,"scope":"case_analysis"}'
assert hashlib.sha256(source_bytes).hexdigest() == '5e7290127c0a7fad2ee0f746bc09e74299b8e31e2bbd8f554eb4862e384247b5'
assert hashlib.sha256(context_bytes).hexdigest() == 'be7e62de03c318ae671d7a4ef2dfdda611c544f09aa51cddf0a825d750c33a77'
```

| 같은 fixture의 요청 | 정확 oracle |
|---|---|
| values | ids=[10,20,30], values=[1.0,null,3.0], validity=[true,false,true], coverage2/3 |
| min/max/mean | 1.0 / 3.0 / 2.0, status=partial, expected3/valid2/missing1. mean은 공간 평균 아님 |
| selection_id가 사전등록되지 않은 'made-up' | out_of_scope, provider에 임의 entity selection을 수행하지 않음 |
| 고정 frame0 evidence에 frame_index=1을 추가 인자로 보냄 | invalid_arguments(additionalProperties:false) |
| norm+component_id=vector, 3vector/동일mm/known orthonormal global basis | 각절점 sqrt(x²+y²+z²), 유효3성분 필요; [3,4,0]→5.0 |
| norm+unknown unit 또는 unknown basis 또는 tensor field | unsupported_field, reason_codes에 UNKNOWN_UNIT/UNKNOWN_BASIS/NOT_VECTOR 기록, 값 반환0 |
| 리뷰 동안 원 artifact bytes/hash 변조 | integrity_error/stale_context, 자동 재수집0, 답변검토 insufficient; 새 capture/질문을 사용자에게 제시 |

OpenAI 호환 native tool 왕복 fixture(실제 model은 별도 live 검사):

```json
{"role":"assistant","content":null,"tool_calls":[{"id":"call_mean_1","type":"function","function":{"name":"evidence_query_field","arguments":"{\"evidence_id\":\"01010101010101010101010101010101\",\"field_id\":\"DISP\",\"component_id\":\"D1\",\"selection_id\":\"all\",\"operator\":\"mean\",\"operator_version\":\"1\",\"limit\":1000}"}}]}
```

adapter는 native alias evidence_query_field를 canonical evidence.query_field로 역매핑→위 args 검증→고정 context dispatcher1회→같은 tool_call_id의 tool-role message로 결과 JSON 전송→최종 assistant 답변 순서를 확인한다. 서버가 `content=null`과 tool_calls를 보낸 것을 빈 최종 답변으로 오분류하지 않는다. 최종 설명은 '유효2/전체3 절점의 Ux 산술평균 2mm, 1개 결측'이며 ‘전체 모델 평균’으로 확장하지 않는다. 같은 roundtrip의 prompt-only provider는 unsupported_capability이고 도구 실행0이다.

**실서비스 schema까지 유효한 golden fixture 생성 절차**: `tests/test_query_tools.py`는 tempfile에 기존 writer가 만든 scalar VTU를 저장하고 실제 bytes SHA256/size, canonical context SHA256, ArtifactRefV1/dataset manifest를 생성해 위 request의 ID를 그 실제 생성 ID로 대입한다. 생성 receipt를 resolver로 검증한 후 mean=2/coverage2/3을 확인한다. golden fixture의 hash를 0 문자열로 대체하거나 resolver를 mock하여 ‘실데이터 무결성 통과’로 세지 않는다. provider transport-only fixture와 실제 bytes fixture를 별도 테스트로 구분한다.

<a id="program-results"></a>

## 프로그램·변환·결과 조회와 RP 연결


### 1. 제품 소유와 첫 사용자 흐름

WB는 프로그램 연결, solver/converter의 소유 프로세스, run/파생 artifact, 수치 추출과 이력을 소유한다. Prep은 native 모델·재료·곡선·revision/export를 소유한다. RP/ParaView/PrePoMax는 각자 표시/편집 기능을 소유하며 WB의 원 결과를 재판정하지 않는다. 별도 JobManager/FE DB/solver queue를 만들지 않는다.

사용자 명령과 산출물을 구분한다.

1. 기존 **계산 실행**: 기존 JobManager→NativeRun/job.json, source/execution inputs, solver-native outputs. 자동 후처리기를 실행하지 않는다.
2. **결과 변환…**: 선택 run+converter+프레임을 명시하고 별도 operation을 시작한다. source bytes는 불변, 변환 산출물만 `.derived`에 저장한다. 취소는 converter만 종료한다.
3. **후처리 열기**: 사용자가 선택한 완료 operation/dataset/frame을 외부 consumer로 연다. solver/converter를 자동 시작하지 않는다. 변환 자료가 없으면 변환 명령으로 안내한다. 같은 run의 가장 최근 operation으로 조용히 대체하지 않는다.
4. **결과 조회/보정/전문가 근거 추가**: deterministic extractor가 동일한 ResultRefV1을 읽는다. RP 화면 픽셀이나 LLM 추정 수치를 계산 근거로 쓰지 않는다.

첫 인도는 WB generic converter/result/consumer core + ParaView 기존 경로 유지까지이다. RP 1.0.0의 수동 폴더 열기는 별도 안내이다. **RP 자동 동일-run 연결은 별도 AGPL fork의 새 CLI 작업과 실제 Windows 인수에 의존하는 후속 WP이며 현재 upstream 기능이 아니다.**

### 2. 기존 코드에서 반드시 재사용할 경계

| 기존 파일·함수 | 현 상태 | 확장 계약 |
|---|---|---|
| `caelab/connections.py:ProgramSpec/ProgramStatus/LaunchRequest/launch_request` | 프로그램 탐색·known GUI file-open form. solver GUI launch 거부. RP 미등록 | 기존 ID/인자 유지. 결과 consumer/converter capability profile을 별도 registry로 추가. 알 수 없는 exe에 임의 플래그 probe 금지 |
| `caelab/native/adapter.py:SolverAdapter/get_solver_adapter` | build request + collect_results. Radioss stage validate 포함 | solver 소유 유지. Converter를 SolverAdapter로 위장하지 않음 |
| `caelab/jobs.py:JobManager._run_native/_WindowsRunGroup/_finish_owned` | timeout/cancel, Windows Job/Posix process group, restored PID 비채택 | 동일 소유 프로세스 runner를 재사용하도록 최소 리팩터링. `submit_operation`은 새 operation worker이며 _native controls와 구분 |
| `caelab/native/run_store.py:NativeRun/RunStore/atomic_json/copy_to` | job.json 엄격 dataclass 읽기, manifest/hash, SaveAs 전체 inventory | NativeRun top-level·job.json 변경 없이 operation v1 sidecar를 같은 RunStore가 소유. operation write/read/lease/selection/recovery 확장 |
| `caelab/native/radioss.py:collect_radioss_results` | actual native A### 미해독이면 partial. ASCII NODE/TETRA는 fixture | 기존 partial 보존. 실제 official converter output을 별도 adapter로 읽고 원 run 성공/parse_state를 덮지 않음 |
| `caelab/native/evidence.py:native_results_file/capture_run_evidence/_finish` | 기록된 PVD/VTU hash 검증. _finish 재파싱은 CalculiX FRD 전용 | 기존 동작 유지. 새 `capture_result_evidence(ref)`는 verified Dataset adapter 경로. 단순 solver_id 분기 추가로 FRD 전용 _finish를 범용인 척하지 않음 |
| `caelab/native/frame_view.py:read_native_frame` | 선택 VTU payload 해시 재확인 | source run 또는 derived result ref를 명시하는 새 resolver를 추가; 새 `.vtk`를 기존 vtu_file key에 억지로 넣지 않음 |
| `caelab/project.py:SCHEMA_VERSION=3/save_native_project` | 프로젝트 v3, RunStore.copy_to SaveAs | 첫 단계 v3 유지, operation selection은 RunStore sidecar. SaveAs가 active operation 중 실패하고 완료 operation을 재검증·복사해야 함 |

### 3. Capability·API

`caelab/artifact_refs.py`(신규, 단일 공통 위치): 표준 dataclass/enum/JSON 검증. `caelab/native/operations.py`(신규): 서비스 orchestration. `caelab/native/result_adapters.py`(신규): 등록된 deterministic reader/extractor 인터페이스. `caelab/result_readers/vtk_fields.py`(신규): optional numerical extra의 lazy VTK reader 구현. stdlib core DTO/service는 Qt/VTK나 GUI private `_read_grid`를 import하지 않는다. `caelab/native/converters.py`(신규): converter argv·input/output validation. 모듈 이름 변경은 통합 전 한 소유자가 정하며 공통 schema를 중복 정의하지 않는다.

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

WB facade additions:

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

`project`는 기존 Workbench._run_store(project)로 해석하며 source_run_id/node_id/operation_id가 그 저장소와 선택 노드에 실제 속하는지 검사한다. caller supplied root/path로 조회하지 않는다. tool frontend는 host가 발급한 opaque ProjectContextRef를 NativeProject에 해석하며 임의 path를 받지 않는다. 기존 `open_native_results(project,node_id)`는 호환 유지하고 선택된 derived result가 있을 때 새 resolver에 위임한다.

`plan`은 실행하지 않음: 필요한 converter executable/version/hash·input roles/hash·selected frames·output types·expected storage를 보여준다. submit은 plan/hash가 바뀌었으면 `STALE_PLAN`, 새 plan을 요구한다. adapter registration은 코드 allowlist이며 임의 Python module/사용자 shell command 자동 로딩을 제공하지 않는다.

첫 구현과 후속 지원을 명시 구분한다.

| 단계 | 지원 가능한 동작 | 아직 미지원 |
|---|---|---|
| POST01/02+초기 reader | 기존 writer VTU point scalar/명시 component, explicit frame, identity/min/max/mean(유한값·counts), norm(동일 단위 exactly3 vector·known orthonormal basis만), evidence.describe, operation lifecycle의 합성 fixture 검증 | 임의 legacy VTK, cell/IP/layer, basis transform, von Mises/principal, native binary physics |
| POST03 검증 후 | 고정 OpenCourant anim_to_vtk output의 원 ID/TIME/지원 shell·solid fields와 별도 검증한 tensor 연산 | 지원 profile 밖 beam/solver/version, 미확정 material orientation/strain measure |
| POST05/06 검증 후 | RP fork의 post-only exact dataset/frame 표시·receipt handshake | upstream vanilla 자동 CLI, RP 자체 solver 성공 판정 |

범용 DTO에 나타난 연산/형식은 첫 단계 구현지원 목록이 아니다. 다음 reduction 목록의 von_mises/principal/curve_extract 및 basis 연산(위 제한된3vector norm 제외)은 각 numerical adapter의 후속 verification gate를 통과해야 한다.

지원은 boolean 하나가 아니다. `SupportProfileV1`은 exact `{solver_id,solver_version,format_revision,element_family,quantity,association,location,component,basis,measure,shear_convention}`별 supported/unsupported/unknown과 검증 수준을 저장한다. equivalent scalar 하나만 있어도 full tensor·모든 beam/shell layers가 지원된다고 확대하지 않는다. 탐색된 프로그램과 solver/modeling capability를 혼동하지 않는다.

프로그램 capabilities 자동 탐색을 일반화하지 않는다. built-in profile은 정확한 known 인자만 사용한다. RP vanilla1.0.0은 `view_project=manual`, `post_only=false`; fork contract profile이 명시된 등록만 `--capabilities-file`로 확인한다. unknown/missing/version mismatch면 기존 프로그램 단독 실행+수동 폴더 안내로 fallback하고 자동 열기 성공을 표시하지 않는다.

### 4. 저장·manifest·재열기·cache

프로젝트의 `<name>.runs` 소유 RunStore 아래:

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

operation id/launch id는 새 UUID hex32. 모든 payload 경로는 자기 소유 root 상대 path이며 absolute/..·NUL·symlink·reparse escape는 거부한다. hardlink도 사용하지 않고 bytes copy 후 before/after fingerprint+sha 검증. stderr는 텍스트 로그, stdout binary result를 절대 같은 log에 합치지 않는다. `.tmp/.part`만 작성 후 검증·atomic rename하며 완료 receipt는 마지막에 commit한다.

`OperationReceiptV1` 필수:

- `schema_version=1`, `id`, `operation_kind='convert'|'extract'`, `source_run_id`, `source_receipt_sha256`, `source_input_revision`, `source_artifacts[]`, `adapter_id/version/code_sha256`, `executable:{path_audit,version,sha256,host_os}`, `plan_sha256`, `cache_key`, `created_at/started_at/finished_at`.
- `state=queued|preparing|running|validating|completed|failed|cancelled|interrupted`; `parse_state=unparsed|reading|readable|partial|unsupported|failed`; `stage`, `progress:{completed,total,unit}`, `returncode`, `error:{code,message}`, `warnings[]`, `outputs[]`, `dataset_ref`, `reused_from_operation_id|null`.
- artifact item은 root 공용 **ArtifactRefV1 `{id,role,path,sha256,size,format_id,producer:{id,version}}`**를 그대로 사용한다. `path`는 envelope가 정한 store root의 상대 경로다. 별도 provenance의 `source_artifact_id`로 입력·출력의 계보를 연결한다. 기존 evidence.files/NativeRun artifacts는 read adapter로 매핑하며 자동 rewrite하지 않는다. roles include `source_native_animation/source_time_history/source_input/source_include/converter_input/converter_stdout/converted_field/converted_history/dataset_manifest/log/stderr`. `source_*` refs can reference original run with run-scoped path; copied converter_input always op-scoped.
- exact command audit `{argv,cwd_relative,stdin_role|null,stdout_target,stderr_target,env_names,tool_version}`; secrets/env values are not serialized.
- PID is diagnostic. Recovery cannot adopt/kill a recorded PID. active receipt after unlocked crash→interrupted; temp outputs are quarantined as incomplete, not registered as completed dataset.

`source_receipt_sha256`은 현재 job.json 전체 재해시가 아니라 operation 준비 당시 `inputs/source_receipt.json`에 정확 bytes로 복사한 frozen receipt ArtifactRef의 hash다. `source_receipt_ref: ArtifactRefV1`도 필수이며 둘이 같아야 한다. frozen bytes는 SaveAs에서도 rewrite하지 않는다. receipt 내부 옛 absolute path는 당시 audit 정보일 뿐 현재 파일 열기 경로가 아니다. reopen은 frozen receipt 자체 hash, 원 native/derived artifact bytes/hash를 확인하고 현재 run receipt의 immutable identity `(id,node_id,solver_id,input_revision,snapshot의 원/실행 입력 SHA 집합 및 native artifact SHA 집합)`만 대조한다. 원 run이 unverified legacy이면 missing을 unknown으로 보존하고 verified 등급으로 승격하지 않는다.

현재 `Workbench.native_runs`는 입력 변경/INCLUDE 누락·단위 변경 등을 보고 반환 run의 `stale/stale_reason`을 현재 문맥으로 계산한다(`caelab/workbench.py:269–291`). 이 live flag를 frozen receipt나 source fingerprint에 넣지 않는다. 원 source 경로 변경은 현재 문맥 stale, snapshot native bytes/hash mismatch는 corrupt이다. 과거 frozen 결과 read를 stale 때문에 HASH_MISMATCH로 바꾸지 않는다. 최신 모델로 Apply/새 fitting context를 사용할 때만 별도 live revision/units 검사를 한다.

`source_fingerprint_v1`은 source run/node/solver identity, input_revision, 원/실행 입력 및 native output의 role+상대경로+SHA 집합, 기록돼 있는 고정 solver/execution version/hash/argv를 canonical JSON으로 해시한다. unknown version은 명시 unknown이다. source의 absolute audit path·현재 stale flag·변경될 수 있는 UI 문맥은 cache identity가 아니다. Dataset/operation canonical hash는 자기 hash 필드만 제외한다.
ArtifactRef 자체에 scope 필드를 추가하지 않는다. 상위 provenance envelope에 `artifact_owners:{artifact_id:{scope,id,source_run_id?}}`를 반드시 둔다. resolve 규칙은 다음으로 고정한다. 모든 resolver는 host의 frozen ProjectContext/등록 store만 사용하며 tool은 owner/path를 입력받지 않는다.

| scope | owner id | ArtifactRef.path가 상대인 root |
|---|---|---|
| run | source run UUID | Project RunStore.root/source_run_id |
| operation | operation UUID, source_run_id 필수 | RunStore.root/.derived/source_run_id/operation_id |
| consumer | post:<launch_id> | RunStore.root/.post/launch_id |
| consumer | material-study:<study_id> | host 등록 materials/study_id |
| consumer | selection:<selection_id> | RunStore.root/selections/selection_id |
| consumer | evidence:<discussion_id> | 질문 snapshot의 discussion.parent/assets. path는 기존 evidenceid/data/... stored_path와 동일 |
| consumer | corpus:<corpus_revision> | 질문 snapshot에 등록한 immutable corpus/index root |

scope enum은 **run|operation|consumer 세 개만**이다. prefix는 consumer workspace 종류를 나타내고 새 scope를 추가하지 않는다.

evidence:<discussion_id> root는 정확히 host등록 ExpertWindow.path.parent/assets이며 `<evidence_id>/data/...` stored_path를 strip 없이 사용한다. 질문 snapshot은 허용 evidence IDs/subtree 및 artifact IDs를 함께 고정하므로 같은 assets root의 다른 evidence를 조회할 수 없다. 같은 artifact_id를 상충 owner로 재정의하면 ARTIFACT_OWNER_CONFLICT다. 같은 path라도 scope가 다르면 자동 병합하지 않는다. owner-id 형식·context membership·resolve containment·symlink/reparse escape·fingerprint/read/hash를 검사한다. persisted ID/path는 host resolver를 통하며 임의 root override는 없다.

Backward compatibility: missing `.derived` means no derived records. Unknown operation schema is visible unsupported/read-only with reason; do not silently discard or resubmit. NativeRun dataclass remains unchanged. Sidecar receipt support does not require project v3 bump. `RunStore.copy_to` must acquire operation/staging leases, fail while active, copy complete inventories, verify relative refs/frozen source receipt artifact hashes and immutable source identities in new root. Imported legacy unverified results can be viewed with explicit `unverified` but cannot be passed as verified fit/review inputs. Archived source root paths are audit strings, not live external dependencies.

Cache key is canonical hash of all source artifact SHA/roles, immutable source_fingerprint_v1, adapter/converter executable hashes+versions, options/frame selection, reader version, basis/measure/location mapping, unit declaration/conversion policy, derived operators. Display theme/camera is excluded. Cache hit revalidates all files and eligible status. cancelled/failed/interrupted or required partial fields are never successful numeric cache hits. Multiple operations remain separate and explicitly selected; no `latest` fallback.

### 5. DatasetManifestV1·공용 ResultRefV1

Dataset manifest includes immutable source/operation refs and artifacts, `frames[]`, topology/ID maps, field support/coverage. Frame has stable `frame_id` string, `source_step`, actual `{quantity,value,unit,source}` sample coordinate(time/load/frequency); ordinal index is separate and never passed as physical time. `TIME` absent→unknown, not index.

Field descriptor:

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

이 값들은 예시다. solver provenance로 확정하지 못하면 measure/unit/basis는 `unknown`, scale=null로 둔다. 이름 `Strain`만으로 engineering/log/Green strain이나 tensor/engineering shear를 추측하지 않는다. geometry는 `reference|deformed|unknown`와 변위 의미·좌표 단위 선언이 필요하다. deformed+displacement를 두 번 warp하지 않는다. 재료 직교축은 요소 local 축과 다르며 orientation payload/hash 없이는 material 변환을 제공하지 않는다. Shell max/min은 실제 제공한 위치만의 범위이므로 비교한 layer IDs를 결과에 기록한다. 중간값을 /3이라고 단정하지 않고 N/실제 배열 위치를 함께 보존한다.

`ResultRefV1`은 모든 numeric 도구·expert·material fit의 단일 공용 schema(통합 문서 단일 소유)이다:

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

support와 eligibility는 공학 승인이 아니다. `review=true`는 독립 검토의 근거 자료로 사용 가능함을 뜻하며 결론 통과를 뜻하지 않는다. global dataset partial이더라도 required field/IDs/time/basis/measure/unit이 완전하면 query/fit policy가 허용할 수 있다. 필수 observable 중 하나라도 incomplete이면 objective invalid; 부가 field partial만으로 모두 기각하지 않는다. stale는 원 실행이 새 모델과 다름을 뜻하고 source hash mismatch는 corrupt이다. 과거 run 수치 조회는 historical 명시 후 허용 가능하지만 최신 모델 적용/fit는 명시한 run revision과 대조한다.

선택 ID 목록은 root 소유 selection artifact에 저장하고 ResultRef에는 위 참조와 count만 담는다. UI가 선택을 snapshot한 뒤 immutable revision을 계산하며 도구/LLM은 host가 허용한 selection_id만 입력한다. 모든 ID를 prompt에 복사하거나 arbitrary ID list로 임의 모델 부분을 조회하게 하지 않는다.

`NumericResultV1`: `ref`, `values` 또는 bounded asset ref, `sample_coordinates`, `counts`, `status`, `reason_codes`, `warnings`, `operator_provenance`. NaN/Inf는 JSON null+mask, valid counts에서 제외. reduction의 전체 확장 공간 identity/min/max/mean/norm/von_mises/principal/curve_extract는 operator id/version으로 등록하며 초기 profile은 위 단계표의 identity/min/max/mean 및 제한된 norm만 제공한다. norm은 선언된 동일 단위 exactly3-component vector의 각 절점 Euclidean magnitude이며 공간집계·tensor norm·nested reduction이 아니다. unknown/nonorthonormal basis나 mixed unit은 unsupported다. 평균/노드 smoothing·IP extrapolation은 원 association과 구별하며 자동 실시하지 않는다. arbitrary LLM 수식 실행은 없음. 요청 field/location/basis가 없으면 available list와 `UNSUPPORTED_*`를 반환; RP `variable(key)`의 unknown→U fallback을 numeric adapter로 재사용하지 않는다.

기존 native-run 유래 근거의 source_kind=native_run은 유지하고 evidence schema2에 derived_result_ref=ResultRefV1를 추가한다. 기존 v1은 read adapter로 매핑하며 자동 rewrite하지 않는다. source_run_id 일치를 확인하고 없는 input/log는 빈텍스트+미제공 상태로 보존한다. calibration은 별도 typed calibration_ref로 취급한다.

공통 expert envelope의 `kind=result` payload로 ResultRef를 넣고 document/chunk와 content hash/provenance/availability의 바깥 형식을 공유한다. 상세 envelope 명명은 expert 담당과 통합자가 단일 결정한다. 전문가 read tools는 stable ref ID를 입력받아 실제 artifact hash를 검증한 뒤 조회하며 임의 filesystem path를 받지 않는다.

### 6. 프로세스·오류·취소와 이벤트

Conversion lifecycle: queued→preparing(verify+copy)→running(process group)→validating(read+field metadata+hash)→completed. 어떤 단계에서든 cancel→owned converter descendants 종료→temp close/quarantine→cancelled receipt 마지막 저장. timeout→failed/TIMEOUT, cleanup 미확인→failed/PROCESS_CLEANUP_FAILED. 변환이 일부 끝났어도 전체 request 취소면 completed dataset 등록 금지; 성공 frame을 다시 쓰려면 명시적 재시도 새 operation이 재검증한다.

하나의 JobManager만 Popen/Windows Job handle을 소유한다. conversion key별 RunStore operation lease를 사용하고 SaveAs는 root+operation leases와 충돌하게 한다. 같은 key가 이미 실행 중이면 OPERATION_IN_PROGRESS와 읽기용 opref 반환; 타 controller PID를 채택하지 않는다. GUI 창 닫기는 자신이 제출한 operation만 취소하고 cleanup 상태를 기다린다. RP 창 닫기는 solver/conversion을 취소하지 않는다. 사용자가 닫은 RP만 WB가 임의 재실행하지 않는다.

오류 codes: PROGRAM_UNAVAILABLE/UNSUPPORTED_CAPABILITY/STALE_PLAN/SOURCE_CHANGED/HASH_MISMATCH/PATH_ESCAPE/CONVERTER_EXIT/EMPTY_OUTPUT/INVALID_DATASET/MISSING_TIME/MISSING_ID/UNSUPPORTED_FIELD/UNSUPPORTED_BASIS/MEASURE_UNKNOWN/UNIT_UNKNOWN/PARTIAL_REQUIRED_FIELD/TIMEOUT/CANCELLED/PROCESS_CLEANUP_FAILED/STARTUP_FAILED/HANDOFF_MISMATCH. 불완전 자료는 원인+available fields를 보여준다. 실패→0 값·가짜 frame·다른 run·최신 cache로 대체하지 않는다.

### 7. OpenCourant 구체 converter mapping

- Profile `opencourant.anim_to_vtk.v1`: official OpenCourant `latest-20261011` 설치 `exec/anim_to_vtk_win64.exe` (Linux은 동일 release 실제 executable discovery 뒤 별도 검증). filename만 같다고 binary 지원을 확정하지 않고 sha/version/host 기록.
- native run의 snapshot/artifacts에 기록된 `<NAME>A001` 또는 `<NAME>A001.gz`를 source artifact role로 고정. 원 artifact를 op inputs에 bytes copy. gz일 때 stdlib gzip streaming으로 op tmp에 해제하며 매 chunk cancel 확인. 원 gz와 hash 유지.
- source 근거는 RP `src/opencourantrp/resources/run_deck.bat:82–86` 및 `src/opencourantrp/gzanim.py:67–75`이다. 전자는 `anim_to_vtk_win64.exe <native>` stdout을 `.vtk`에 redirect하고 후자는 `subprocess.run([str(exe),str(tmp)],stdout=fh,stderr=PIPE,env=_converter_env(solver))`를 사용한다. 동일 invocation을 WB owned runner로 옮기는 계약이며 RP batch를 실행하는 것이 아니다. 실제 argv `[absolute_converter_exe, absolute_converter_input]`, shell=False, cwd=operation staging. stdin DEVNULL. stdout binary→`outputs/<NAME>A001.vtk.part`; stderr→stderr.log. DLL loader env는 기존 external_environment/ProgramStatus.runtime_env와 OpenCourant hm_reader·Intel runtime 디렉터리를 이용하고 WB 번들 Qt/Python 경로를 자식에 누출하지 않는다. returncode0+nonempty만으로 성공하지 않고 VTK parser·point/cell count·ID uniqueness·TIME·array component count·field/location metadata를 validate한 다음 rename/manifest commit.
- exact input→output mapping item `{input_artifact_id,source_step:1,output_artifact_id,native_name:'NAMEA001',vtk_name:'NAMEA001.vtk',actual_time:{value:0.125,...}}`. filename ordinal is no time oracle.
- RP source `gzanim.convert`는 blocking subprocess + converter 자체 소유이며 중간 취소가 없으므로 WB에서 import하지 않는다. 기존 `run_deck.bat`도 solver 재실행 및 rp_run 삭제 때문에 호출하지 않는다.
- `/H3D`가 있는 기존 solver run을 변환하는 데 engine deck를 바꾸지 않는다. 이미 A###가 없으면 변환 불가라고 표시한다. 다음 solver run에 /ANIM을 요청하는 작업은 Prep 명시 편집/새 run이다. H3D-only를 A###처럼 변환하지 않는다.
- first slice `T01→CSV`는 별도 capability `unsupported`다. generic history converter는 공식 th_to_csv argv·CSV 열명/ID/time/unit 검증을 후속 adapter로 구현한다. RP batch에 명령이 있다는 사실만으로 curve fit 지원을 선언하지 않는다.
- Reader는 optional lazy `caelab/result_readers/vtk_fields.py`에서 기존 설치 VTK `vtkUnstructuredGridReader`/`vtkXMLUnstructuredGridReader`(또는 이미 의존성에 있으면 PyVista pv.read)를 호출한다. stdlib core는 Reader Protocol만 소비한다. 초기 `query_field` capability는 기존 solver writer가 만든 검증된 VTU의 point scalar/component+명시 frame에 한정한다. native legacy VTK/cell/IP/layer/basis operators는 POST03에서 별도 oracle를 통과하기 전 unsupported이고, dataset adapter가 설치/구현되지 않았으면 `evidence.describe`만 제공한다. 기존 GUI `_read_grid`를 core에서 import하지 않는다. RP AGPL `variables.py/frames.py` 소스를 WB에 복사하지 않는다. tensor invariants/basis transform은 독립 규정식과 수치 oracle로 WB versioned operator에 구현하거나 permissive 기존 WB 계산을 확장한다.

### 8. RP fork 후속: exact files·CLI·receipt

별도 repository/fork의 독립 WP이다. upstream1.0.0은 PR/Issues를 받지 않으며 Discussions 제안만 기다리지 않는다. fork remote/branch 생성은 통합자가 해당 WP에서 지시하고 이번 문서 작업은 원격 생성하지 않는다. 분리 배포·source/notice 보존, AGPL 소스 경계 확인 후 연결한다.

최소 변경파일:

- **신규 `src/opencourantrp/cli.py`**: stdlib argparse/JSON만 먼저 import. `--capabilities-file PATH`는 Qt를 import하지 않고 profile JSON 원자쓰기 후 exit0. GUI CLI parse 후 app lazy import.
- **신규 `src/opencourantrp/handoff.py`**: WB handoff/dataset/ref 검증, 상대 경로 containment/hash, receipt atomic JSON. WB/LLM code import 없음.
- **기존 `src/opencourantrp/app.py`**: MainWindow `__init__(post_only=False, startup_project=None)`; explicit startup이 있으면 QSettings 이전 프로젝트 복원을 건너뜀. post-only는 launch별 isolated settings를 사용하여 기존 수동 RP 프로젝트/단위 설정을 바꾸지 않음. `_set_project`/`_refresh_files`에 verified exact frame list를 전달해 last 자동선택을 startup reference로 대체. `_file_changed`가 선택 frame/field를 실제 read 후 확인. `_update_run_enabled`, `_run`, `_set_gz_pending`, `_maybe_ask_gzip`, `_ask_gzip` 모두 post_only guard: Run·Stop·GZIP conversion disabled 및 method 직접 호출도 거부. 원 setting unit을 startup run 선언으로 덮고 unknown은 빈 label. unknown var key는 default U로 바꾸지 않고 startup 오류. 외부 project chooser는 post-only handoff 동안 잠그고 다른 자료는 별도 수동 세션으로 열도록 한다.
- **기존 `src/opencourantrp/__main__.py`, `tools/launcher.py`, `pyproject.toml` entry points**: cli:main으로 라우트. 현재 app module이 Qt/VTK를 top-level import하므로 main 내부 argparse만 추가하면 headless capability probe가 실패할 수 있다.
- **기존 `tools/build_exe.py`**: fork post-only 배포 옵션은 solver fetch와 --add-data solver를 생략 가능하게 한다. 현재 파일은 solver 없으면 자동 다운로드하므로 그대로 재사용하면 별도설치 원칙 위반. full upstream Run 빌드는 기존 옵션으로 유지.
- **신규 `tests/test_handoff.py`, `tests/test_post_only.py`, 기존 project tests**와 README/manual 변경.

새 CLI 예:

```
OpenCourantRP.exe --capabilities-file C:\Temp\rp-capabilities.json
OpenCourantRP.exe --project C:\CAE\post\<launch_id>\data --post-only --handoff C:\CAE\post\<launch_id>\handoff.json --receipt C:\CAE\post\<launch_id>\receipt.json
```

실제 LaunchRequest.argv는 list이며 경로 공백/한글도 별도 인자로 전달한다. 문자열 shell 조합 없음. handoff에는 schema1/launch_id/nonce/selected ResultRef/expected file list and hashes/read-only/run alias mapping/units/field support가 있다. source run과 operation은 해시 ref로 구별하며 viewer staging deck/INCLUDE/VTK는 사본만 쓴다. Run+compressed 변환을 막으므로 원 A/TH를 RP staging에 넣을 필요가 없다. converter 입력 staging에는 필요한 A/TH 사본만 별도로 둔다.

Receipt lifecycle: WB prepares staging+nonce→launch→RP validates handoff/data→load exact dataset+frame+field→VTK first-render EndEvent 관측→atomic receipt `ready`(launch_id/nonce/ref hash/file hash/actual TIME/node-cell counts/selected field/layer/basis/available fields/coverage)→normal close `closed` with exit0. receipt에는 monotonic seq·ready_at·loaded_ref_hash를 유지하므로 ready 직후 즉시 닫혀도 closed receipt에서 확인 가능하며 loaded 전 close는 ready로 승격하지 않음. opened Popen과 ready를 구별한다. ready는 수치 oracle·OS 실화면 인수의 대체가 아니다.

invalid CLI/schema exit2; missing/hash mismatch file exit3; unsupported requested field/basis/layer exit4; Qt/VTK startup/load error exit5; normal viewer close exit0. 실패는 receipt `failed`+code 작성 후 비대화형 종료하며 startup QMessageBox만 띄운 채 WB가 대기하게 하지 않는다. post-only 데이터 밖 snapshot/video export는 사용자가 명시한 별도 경로만 허용하며 source/staging overwrite는 거부한다.

WB ready deadline(기본30초, 설정 가능) 동안 GUI event loop를 막지 않고 process poll+receipt hash/nonce 확인. ready/timeout/cancel 경합은 manager lock 안에서 첫 terminal transition 한 번만 수용. timeout 직후 도착한 ready는 성공으로 되돌리지 않는다. Ready 전 취소는 해당 launch만 종료하고 staging을 failed로 보존한다. ready 후 사용자가 RP를 닫으면 closed 기록, solver 상태 불변. 재열기 새 launch_id/staging/nonce. vanilla RP에는 이 flags를 보내지 않고 manual 모드로 명시 fallback한다.

### 9. 재사용·라이선스와 작업 패키지

재사용: 기존 JobManager/RunStore/input_snapshot/evidence hashing, filelock·atomic_json, VTK/PyVista readers, NumPy tensor algebra, 기존 Qt GUI/control. Prep 공용 unit/table/curve 위젯은 WB host binding으로 재사용하며 결과 reader를 Prep에 중복 구현하지 않는다. 현재 WB root에 LICENSE 파일은 확인되지 않았으므로 RP AGPL 코드 복사를 기본 경로로 선택하지 않는다. RP fork는 독립 AGPL repository로 소스·수정·고지 보존하고 WB는 installed executable 참조. VTK/PyVista/NumPy/PySide6의 정확 pinned package license/notice는 기존 dependency 고지에 추가 확인한다. 라이선스 검토 전 바이너리를 vendor/installer에 복사하지 않는다.

| WP | 단일 편집 소유·파일 | 선행/완료 |
|---|---|---|
| POST-01 공용 ref/capability | WB core 통합자: artifact_refs.py, schema validator, result_adapters.py | expert/material와 단일 ResultRef 계약 승인, schema1 golden JSON·unknown/partial 검사 |
| POST-02 operation lifecycle | WB jobs 담당: jobs.py/run_store.py/operations.py + 해당 tests | POST01, 원 solver 회귀 유지·hash/cancel/timeout/recovery/SaveAs |
| POST-03 OpenCourant converter/read | WB adapter 담당: converters.py/result_adapters.py/radioss.py(no success promotion) | POST01/02, binary stdout→VTK·real timestamps/IDs/field map |
| POST-04 user flow/evidence | WB integration 담당: connections.py/workbench.py/native evidence/frame_view + Qt result commands | exact operation selection·convert/open 분리·expert same ref |
| POST-05 RP fork CLI | 별도 RP fork 담당: 위 CLI/handoff/app/entry/build/tests/docs | POST01/04 handoff fixed, vanilla fallback·postonly no solver |
| POST-06 독립 Windows 인수 | 작성자와 다른 QA | fixed commits+official binary hashes, OS 화면/field numeric/close/error/cancel/input unchanged |

공통파일 jobs.py/run_store.py/connections.py/workbench.py/schema는 통합자가 한 편집자에게 배정하고 다른 담당은 계약 소비만 한다. 첫 기능 인도는 POST01–04, RP 자동연결은 POST05–06 이후다. 프로그램 목록만 늘리거나 테스트 fake converter만 통과한 것으로 native 결과 판독/실 solver 완료를 선언하지 않는다.

### 10. 수락 검사와 재현 경로

이미 이번 감사에서 수행한 근거: RP `pip install -e '.[dev]'` 후 upstream `pytest -q` 75passed. 합성 mixed triangle/tetra binary VTK를 RP reader로 재읽고 field 수치 oracle를 검증했다. 이는 Windows/native solver 변환 검증이 아니다. 감사 원 기록은 통합 검토 이력에 링크하고 scratch 경로를 재현 의존성으로 두지 않는다. 구현 WP에서 재현 fixture를 `tests/fixtures/result_adapters/mixed_shell_solid/`에 독립 생성 recipe+expected.json+provenance로 소유하며 대형 vendor testdata는 외부 pinned testdata 경로에 둔다.

개발자가 추가할 tests:

1. contracts golden refs/unknown schema/invalid path/type/hash; malformed tensor9·NODE_ID/ELEMENT_ID duplicate·TIME absent; property-based tensor orthogonal invariance는 실제 버그 축에 한정.
2. fake executable는 process lifecycle만 검증: stdout binary/stderr 분리, child process 소유 cancel·timeout, return0 empty/invalid output, output hash mutation, concurrent samekey, crash recovery/late ready. 성공 물리검사로 세지 않음.
3. synthetic file fixture: NODE_ID=[11..17], ELEMENT_ID=[101,202], PART_ID=[10,20], frame TIME=[0.125,0.250]. 90도 triangle local xx=5→global yy=5, solid xx12 불변; shell top/middle/bottom10/5/-4→해당3위치 maxVM10, solidVM12. exact ID/time, no double displacement warp; unknown strain measure/shear/unit이면 diagnostic query와 fit deny를 분리. source/staging SHA 전후 동일.
4. schema migration/reopen/SaveAs: 기존 project v1/2/3·legacy job.json 그대로, .derived없음·unknown future sidecar, multiple operations same run, explicit operation/frame 선택, 원 root 제거 후 SaveAs archive 내 refs만으로 재열기, active operation copy 거부. stale historical read vs corrupt mismatch 구분.
5. actual Windows POST03은 아래 고정 제조해 인수계약으로 구현한다. 기존 zero tetra는 format/ID smoke이며 nonzero stress oracle를 대신하지 않는다. actual run·native 변환·물리 판독은 아직 미실시다.

#### POST03 고정 fixture 인수계약

신규 `tests/fixtures/result_adapters/opencourant_affine_elastic/{fixture.json,expected.json,provenance.json}`와 `scripts/generate_opencourant_affine_fixture.py`를 POST03 담당이 작성한다. generated `affine_0000.rad/affine_0001.rad`는 독립 작성 raw deck이며 baseline bytes를 덮어쓰지 않는다. 기존 WB `tests/fixtures/radioss_runs/tetra/tetcase_0000.rad`의 LAW1/PROP SOLID 파싱 형식과 `shell_include/conditions_0000.rad`의 PROP SHELL 형식·상대 INCLUDE 보존을 재사용하되 물성/요소/BC/output은 아래 고정 값으로 생성한다. 기존 tetra starter SHA=`a1c1ba0a93532b0c2d9668f6b74f59875eb92cce8c9bf313384931a1779207b5`, engine SHA=`e4d3ebb0fc72fb49caa68981e600bceb839f3e67ff0db850e872445d17e32143`이다.

- 단위 mm–Mg(tonne)–s, stress MPa. LAW1 E=1000, nu=.25, rho=7.8e-9 Mg/mm3. Tf=.01s.
- SOLID HEX8 element ID101/part10, cube10mm, nodes11–18. 정규 +x,+y,+z node ordering. shell QUAD4 element202/part20, 별도 떨어진 XY plane10mm square, nodes21–24, local x=globalX/localy=globalY/normal+Z, thickness1mm,NIP5. cube nodes11..18=(0,0,0),(10,0,0),(10,10,0),(0,10,0),(0,0,10),(10,0,10),(10,10,10),(0,10,10). shell nodes21..24=(0,0,20),(10,0,20),(10,10,20),(0,10,20). 두 파트는 node 공유/접촉/하중 없음.
- 모든 node `vx=x_local*.1 /s`를 prescribed: leftx0 group vx0, rightx10 group vx1mm/s, y/z translation0. shell rotationxyz0. 초기 velocity도 동일 affine field로 맞춰 초기 half-step 불일치를 제거한다. 모든 translational DOF가 exact affine motion이므로 해석 중 자유 내부 node/파동 응답에 의존하지 않는다. force나 stress를 입력으로 지정하지 않는다.
- solid profile 기존 Isolid1,Ismstr4,Iale0; qa1.1,qb.05,h.1,Lambda0,Mu0의 기존 artificial viscosity/hourglass 설정을 그대로 명시. small strain oracle와 비교하되 finite-geometry error는 아래1% 선언범위다. shell Ishell24,Ismstr4,Ish3n2,Idrill2,Ipinch0, Hm/Hf/Hr/Dm0,Dn.015; NIP5,THICK1,Ashear5/6,Ithick2,Iplas0,Ipos0을 명시. 전체 모델 damping/mass scaling/중력 없음. affine mode에서 hourglass가 없어야 하며 material stress tensor만 비교하고 총 reaction을 섞지 않는다. 이 card 값·override/warning을 Starter listing에서 검증한다.
- `/IMPVEL`은 global X,skew/frame0,sensor0,Icoor0,Scale_x1,Scale_y0 또는1,Tstart0,Tstop=.01, time function [(0,1),(.01,1)]과 left IDs11,14,15,18,21,24 및 right IDs12,13,16,17,22,23 node groups로 생성한다. left vx0의 x경계와 중복 /BCS constraint를 겹치지 않는다. y/z 및 shell rotations는 /BCS의 별도 DOF로 정의한다.
- output `/ANIM/DT 0 .0025`, `/ANIM/VECT/DISP`, `/ANIM/VECT/VEL`, `/ANIM/BRICK/TENS/STRESS/000`, `/ANIM/BRICK/TENS/STRAIN/000`, `/ANIM/SHELL/TENS/STRESS/ALL`, `/ANIM/SHELL/TENS/STRAIN/ALL`, `/ANIM/SHELL/TENS/STRESS/UPPER`, `/LOWER`, strain UPPER/LOWER. /H3D 없음. requested/effective cards와 hash를 generated manifest에 저장한다.
- frame 실제 `TIME=t`를 읽고 `exx(t)=.1*t`; 마지막 frame을 Tf라 가정하지 않는다. displacement right `ux=t`mm, left0; velocitiesright1mm/s,left0. small strain 최종 solid diag(1.2,.4,.4)MPa,VM=.8; shell diag(1.0666666667,.2666666667,0)MPa,VM=.9614803401. frame마다 stress expected=최종값*t/.01, shear0, shell 모든IP/top/bottom 같은 membrane 응답. 적어도 t>=.005인 실제 frame 하나가 있어야 하며 결과 시간은 converted TIME/단조성·engine time range와 대조하고 file ordinal로 채우지 않는다.
- tolerance stress `abs(actual-expected)<=1e-5MPa+.01*abs(expected)`, displacement `1e-8mm+.001*abs(expected)`, velocity `1e-8mm/s+.001*abs(expected)`로 사전 고정한다. deformation reference/current mapping·nodeIDs/elementIDs/partIDs는 exact, rotations/material축 변환은 미지원. native Strain measure/shear convention은 source 확정 후 descriptor에 기록하며 모르면 fit ineligible이고 small-strain 수치비교 통과를 measure확정으로 바꾸지 않는다.
- 이 제조해는 구현할 numerical acceptance 계약이지 현재 field 검증 완료가 아니다. POST01/02+synthetic reader는 먼저 인도 가능하고, POST03은 rawcard 생성/Starter effective profile 확인/actual official binary+converter+수치 oracle 후 인도한다. actual OpenRadioss 다른 버전은 별도 검증 전 unsupported/declared 수준이다.

pinned OpenCourant source `cb760c206e346eff12161a2268b134ef9d48a4ef`의 `/IMPVEL` field semantics: `starter/source/constraints/general/impvel/{hm_read_impvel.F,read_impvel.F}`, engine `engine/source/constraints/general/impvel/fixvel.F`. format 참고 `hm_cfg_files/config/CFG/radioss120/LOADS/impvel.cfg` blob9465edaf는 global profile header+10-wide slots와 second data row20-wide scale/time을 정의한다. shell NIP는 `starter/source/properties/shell/hm_read_prop01.F`가 NIP를 읽고, radioss2024/PROP/prop_p1_shell.cfg blobbf414768의233/237행에서 첫 property data row Ishell/Ismstr/...와 세번째 data row 첫10자 NIP·20자THICK·20자Ashear·10자Ithick/Iplas/Ipos를 정의한다. solid는 properties/solid/hm_read_prop14.F와 radioss2023/PROP/prop_p14_solid.cfg blobc078d64f의378/395행을 참조한다. **CFG에는 별도 제한 조건이 있으므로 파일을 제품에 복사하지 않고 형식 문헌으로만 참조한다.** 독립 generator는 필요한 cards를 작성하고 라이선스/소스 고지를 기록한다.

이 설정을 변경해 시험을 통과시키면 같은 oracle를 통과한 것으로 보고하지 않고 fixture revision/설계검토를 다시 수행한다.
6. RP fork actual: selected run/frame/known scalar/IDs와 TIME를 앱 model+receipt+visible probe로 대조, shell layer/basis가 불가하면 available list/partial 표시, ready 후 close exit0, missing/hash mismatch/unsupported field/Qt startup fail이 각 nonzero, Run/GZIP guard 직접 호출도 거부, 원 solver 프로세스 추가 생성 없음. 공백/한글 path. offscreen fixture와 OS 실화면 구분.
7. expert tool roundtrip에서 동일 ResultRef를 실제 deterministic read하여 numeric result/coverage/unknown semantics와 original artifact hash를 그대로 보존. tool title에 RP/ParaView가 있다는 이유만으로 extraction 성공을 세지 않음.

개발자가 커밋할 재현 경로/명령(아직 미구현):

```
python scripts/generate_result_contract_fixture.py --output tests/fixtures/result_adapters/mixed_shell_solid
python -m pytest tests/test_result_contracts.py tests/test_derived_operations.py tests/test_opencourant_conversion.py tests/test_result_handoff.py -q
```

생성 recipe와 expected.json은 제품 라이선스로 독립 작성하며 vendor 원 결과와 섞지 않는다. RP upstream 재검사는 해당 별도 checkout에서 `python -m pip install -e ".[dev]"` 후 `python -m pytest -q`다. 실제 Windows 명령은 named fixture의 `provenance.json`에 converter/solver binary hash와 함께 보존한다. Windows executable/env가 없으면 명시 미실시이지 성공으로 skip 합산하지 않는다.
