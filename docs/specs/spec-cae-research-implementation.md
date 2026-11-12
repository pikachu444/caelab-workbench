# 범용 CAE 재료 연구·전문가 자문·프로그램 연동 구현 계약

개정: 2026-11-12. 구현할 명세이며 제품 기능 완료 보고가 아닙니다. 기준 Workbench `104cdd6888a5554eecc8cf965c60ff1bd980b6da`, Prep `555af5b3ab2c6e7dc276111609302ef565bb4fc8`입니다. 실행환경 단절 뒤 재구성한 최종 계약은 다음 부속 명세와 함께 읽습니다. **공통 서비스·평가·revision·저장·GUI·Prep 적용은 core, 각 물리/자문/후처리 영역은 담당 부속 명세가 이 문서의 이전 문구보다 우선합니다.** 미회수 raw evidence를 확보했다고 주장하지 않습니다.

| 구현 영역 | 정본 |
|---|---|
| 공통 서비스·목적함수/제약·CAS·SaveAs·Windows Qt·Prep IPC | [core-implementation.md](cae-research/core-implementation.md) |
| DP·LAW33·직교이방·정확히 이름 붙인 derived XBP | [material-pressure-orthotropic.md](cae-research/material-pressure-orthotropic.md) |
| 두 Ogden·Prony/TRS·NEML 반복소성·JC state | [material-finite-history.md](cae-research/material-finite-history.md) |
| 일반 CAE 이론/실행 근거·RAG·도구 왕복·독립 답변 검토 | [expert-implementation.md](cae-research/expert-implementation.md) |
| 실제 native converter/TH·수치 판독·RP 별도 프로세스 | [post-implementation.md](cae-research/post-implementation.md) |
| 실제 토의와 독립 검토·회수/검증 범위 | [인계 색인](cae-research/README.md) |


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

No new constitutive library, package download, paid API, database, JobManager or copy of the existing numerical optimizer is required for the first implementation. OpenCourant's real constitutive source can be consumed by its native solver adapter; do not rewrite it in Python just to satisfy an artificial material-point prerequisite. MFront/MGIS or other external material libraries are possible later adapters, with installation/license/API checks still unverified. The exact first nonlinear profiles and libraries are specified in the A/B annexes; their per-profile unimplemented/platform/physics gates remain explicit.

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

Descriptor/experiment schemas express path dependence, multiaxial controlled and observed channels, ordered loading segments and complex frequency responses from the first version. Backend capability may return UNSUPPORTED_DOMAIN/UNSUPPORTED_CHANNEL/UNSUPPORTED_VALUE_KIND; this is an explicit initial implementation limit, not a schema restriction to a monotonic strain curve. Real/imaginary or storage/loss pairing has explicit units and phase convention; no complex numbers are encoded as JSON NaN/strings. The first adapter only computes real scalar small-strain elasticity. A viscoelastic or multiaxial native/library adapter may be added without changing the generic service or requiring an in-house material integrator.

v1 parameters optimize finite scalar/choice values. A vector/tensor model registers explicit component IDs/groups; table-valued laws use immutable curve references unless a declared parameterization maps named free scalars to the table. Never silently flatten an arbitrary table or change Prony term count. Shared constraints use existing `ConstraintSpec`/safe expressions where representable; otherwise an explicitly versioned adapter validator returns a typed invalid configuration before running. Fixed parameters are absent from the independent `VariableSpace` and merged into each physical candidate; linked values use the existing codec. If all parameters are fixed, run verification without calling `OptimizationProblem` (it rejects zero dimensions). Categorical choices select declared alternatives; no input string becomes executable code.

Units are normalized through one shared conversion contract using current `parse_unit` where supported. Core C6 fixes quantity-aware absolute/delta temperature, Mg and frequency conversions. Unsupported units are rejected rather than guessed. Dimensional input uses physical units; `EvaluationRequest.encoded` is optimizer space. Both are saved separately with codec version. Log transforms require positive physical bounds; no exponentiation of values already decoded by `VariableSpace`.

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

`CalibrationBatchEvaluator.evaluate_batch(tuple[EvaluationRequest,...])` resolves the immutable study revision, merges fixed/linked parameters, validates capability, creates a plan, delegates external plans to existing JobManager, validates required observables, computes residuals and atomically writes the evaluation artifact. It returns exactly one keyed `EvaluationObservation` for each request; `responses` contains host-owned `fit_loss` (dimensionless, serialized unit="") and predeclared finite constraint scalars with their registered units; see core C5. Every ScalarResponseSpecV1 includes schema_version=1. Per-test losses remain in the artifact, with no undeclared dynamic response IDs. Arrays live in artifacts, not `responses`. `source_id='material-evaluation:<id>:<sha>'`; native `run_ids`, `native_attempts`, `cache_hit` are populated honestly. Surrogate predictions keep `mode='SURROGATE'` and cannot count as actual material verification.

A calibration resource policy explicitly contains `max_candidate_evaluations`, `max_native_attempts`, `max_wall_seconds`, `per_job_timeout_seconds`, `max_retries_per_job`, and `max_concurrent_native_jobs`. Optimizer `max_evaluations` counts candidates, not number of specimen solver calls. Preflight expands candidate×experiment job count and refuses dispatch beyond remaining native-attempt budget; retries count against it and preserve their receipts. Completed cache hits consume candidate bookkeeping but zero new native attempts. M1 has no external native jobs; first native bridge uses the existing serial batch scheduler, so max_concurrent_native_jobs=1 until a separate supported concurrency implementation is verified.

The async JobManager bridge is a worker-side wait on completion events; it does not block Qt or start another subprocess manager. Cancellation is passed to `optimize(..., cancel=event)`, prevents further dispatch and calls `cancel_batch`/`cancel_native` for owned active jobs. Adapter computations poll cancellation between increments/tests; opaque external-library calls that cannot be interrupted declare that limitation and do not show cancellation completed before returning. Stop-scheduling and cancel-running remain distinct existing actions.

### 5. Deterministic residual definition

Core C4 fixes required_for_fit/required_for_validation membership and the single AssessmentResultV1; it overrides any broader meaning of required below.

Compare predicted and observed quantities only after dimensions, measure, basis, component, location/gauge, shear/sign and independent coordinate match. A required unknown location/gauge is ineligible; declaring a quantity name alone cannot satisfy it. Cauchy↔PK2 or engineering↔log transformations require the recorded deformation/geometry information; no default volume preservation assumption. Cyclic tests use ordered time or explicit branches; never globally sort repeated strain values. Missing/duplicate abscissae must be resolved by an explicit import recipe, not `np.interp` silently.

v1 uses linear interpolation of prediction onto a **frozen observed grid** within the declared comparison domain. Both domains must cover every required sample; no extrapolation or candidate-dependent overlap crop. Predeclared masks/segments are stored in the experiment revision. A failed/missing point does not reduce the denominator.

For channel c of experiment e: `r[e,c,i] = (y_pred(x_i)-y_obs(x_i))/scale[e,c]`; `L[e,c]=sum(q_i*r_i**2)` where `q_i>=0`, `sum(q_i)=1`. Default q is normalized trapezoidal integration on the frozen x-grid; single-sample observables explicitly use q=1. Channel and training-experiment weights are normalized separately once, stored with the criteria revision. `fit_loss=sum(w_e * sum(w_ec*L[e,c]))` over train only. Per-test scores, raw residuals, normalized residuals, valid ranges and weights remain visible. Scales must be positive and fixed from declared engineering values or frozen training-only data, never a candidate prediction or holdout statistics. A constant signal cannot silently create a zero scale.

Holdout is excluded from optimization/surrogate training/normalization; split is by test/replicate group, not random points from the same curve. Validation may inform explicitly recorded model selection; holdout is evaluated after the chosen recipe is frozen. Tuning after holdout exposure creates a new revision and requires a new untouched holdout for a fresh final validation claim. R²/uncertainty/identifiability are optional reports with `not_computed/not_identifiable/undefined` reasons, never manufactured numbers. Solver termination, fit acceptance and model adequacy are separate assessments. Required comparison membership, acceptance aggregation and AssessmentResultV1 follow core C4; valid high loss remains a finite objective, and finite physical constraint violation remains INFEASIBLE (core C5).

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
assessment: fit_status, validation_status, holdout_status, adequacy_status,
            uncertainty_status, identifiability_status, constraint_status,
            constraint_checks[], policy_ref, channel_checks[], split_checks[], warnings[]
provenance_refs[], content_sha256
```

States: pending→running→completed/failed/cancelled/timed_out/interrupted. Processing states and fit status are independent. Structured errors have `code`, stage, experiment/observable IDs, reason, retryable, artifact refs; codes include UNSUPPORTED_MODEL, UNSUPPORTED_UNIT, MEASURE_MISMATCH, COVERAGE_INCOMPLETE, SOLVER_FAILED, PARSE_FAILED, CANCELLED, TIMEOUT, STALE_INPUT and ADAPTER_VERSION_MISMATCH. Invalid required-for-fit experiment means no aggregate fit objective (a diagnostic/holdout failure does not erase valid train loss; core C4) (`valid=False`, reason, empty required responses); do not map failed runs to a float penalty or drop them from averaging. Retain completed prior evaluations and partial artifacts for inspection.

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

Native solver `runs/<id>/job.json` stays under the **existing RunStore**; calibration stores references. Top manifest includes study identity, immutable revision refs, point/attempt index, selection, optimizer configuration/history, resource budget, artifact inventory hashes and view state. Relative member refs only; reject traversal/symlink escapes and unknown schema fields. Use existing `atomic_json` plus FileLock. Save-as copies/verifies the full referenced closure and native run refs according to existing project relocation behavior. Reopen validates pinned artifact hashes; missing/tampered pinned evidence is CORRUPT_ARTIFACT/read-only and cannot be applied. A changed external live source does not corrupt a valid frozen snapshot; it disables applicability to that current source until explicitly reconciled, while preserving historical inspection/reproducibility. In-flight work after a crash becomes interrupted, not successful. v1 has no historical migration; future migration is an explicit versioned pure operation writing a new file, preserving original bytes. No blind MDO v2→material conversion.

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
               comparisons: tuple[ComparisonSpec,...], assessment_policy, user_constraints,
               expected_revision: str) -> StudySnapshot: ...
    def evaluate(self, values: Mapping[str,float|str], *, expected_revision: str,
               on_progress: Callable[[dict],None]|None=None) -> EvaluationReceipt: ...
    def optimize(self, config: OptimizationConfig, *, expected_revision: str,
               on_progress: Callable[[dict],None]|None=None) -> CampaignReceipt: ...
    def cancel(self, campaign_id: str) -> CancelReceipt: ... # thread-safe request, not terminal claim
    def evaluation(self, evaluation_id: str) -> CalibrationEvaluationArtifact: ...
    def save(self) -> StudySnapshot: ...
```

The canonical service signatures, StudySnapshot/ImportReceipt/EvaluationReceipt/CampaignReceipt revisions, SaveAs/recovery/export/Apply DTOs and locking are **core C2/C7/C8/C10**. expected_revision is document bytes SHA; execution study_revision is immutable content_revision. SaveAs is required in the first user workflow. The synchronous service runs on a worker, callbacks marshal to Qt. Invalid/stale configuration raises MaterialStudyError before dispatch; runtime attempts persist typed artifacts. No second writable owner or busy mutation is silently queued.

Exact numeric oracle: CSV strain `0,.001,.002`, stress MPa `0,2,4`; E free 100..10000 MPa, initial 1000. Use the existing SLSQP implementation: `OptimizationConfig(algorithm="slsqp", seed=17, max_evaluations=120, max_iterations=60, ftol=1e-12, finite_difference_step=1e-7)`, `OptimizationProblem(...differentiable=True)`, linear parameter transform, no constraints/surrogate. Accept termination CONVERGED, |E−2000|≤.01 MPa, loss≤1e-10, dispatched requests≤120; do not require an implementation-specific exact iteration count. This exact existing-core probe was run during design and returned E=1999.999614958788, loss=1.3899068902313467e-14 in five observations. No nu/rho is inferred. Independent holdout strain `0,.0015,.003`, stress `0,3,6` yields zero error at E=2000. For E=1800, scale=4 MPa and normalized trapezoidal q=(.25,.5,.25), expected training loss=.00375. Same data expressed in kPa yields identical physical E/loss after explicit conversion. This is a synthetic arithmetic oracle, not experimental validation of a real material.

Additional M1 acceptance: all-fixed verification avoids zero-dimension optimize; positive log bounds roundtrip once; unknown schema/unit/measure and missing comparison coverage reject; nonmonotonic cyclic strain must use explicit time/branches; masks cannot change per candidate; test groups cannot leak into holdout; failed/cancelled/required-partial attempts produce no fit objective; reopen reproduces parameter/curve/residual/split/hash state; tampered frozen source is corrupt; a changed external live source is applicability-stale. A custom adapter registered from a separate installed/test module (y=a*x+b*x², synthetic x=(0,1,2), y=(0,5,16), physical bounds a,b=(0,10), initial a=b=1, scale=16, same SLSQP config and normalized trapezoid weights (.25,.5,.25)) fits using the same evaluator/residual/service without editing a model-name switch. Proposed `caelab/materials/__main__.py` supplies the CLI. Proposed registry API is `register_material_adapter(adapter, *, trusted_registration)`; optional installed entry-point group `caelab.material_adapters` resolves only after explicit user/installation enablement. Proposed headless CLI entry `python -m caelab.materials validate|evaluate|fit|inspect --study <manifest>` consumes data files, never module import names from a study. Acceptance registers the test adapter through the registry and CLI, then checks capability refusal for an unsupported multi-axial/complex request and no job dispatch. Unsupported export is explicit. Accept CONVERGED, |a−2|≤1e-3, |b−3|≤1e-3, loss≤1e-9 and ≤120 dispatches, with no generic-service model-name branch changed. Existing-core design probe returned a=2.0000045000541093, b=2.9999979999619013, loss=1.3183663827644835e-14 in 16 observations. This proves extensibility, not support for every material law.

First proposed test files: `tests/test_material_contracts.py`, `test_material_experiments.py`, `test_material_residuals.py`, `test_material_evaluator.py`, `test_material_service.py`, `test_material_registry.py`; custom adapter fixture `tests/material_adapter_fixture.py` has an explicitly registered trusted test object, not arbitrary study-import code. Reproduce with `python -m pytest -q tests/test_material_contracts.py tests/test_material_experiments.py tests/test_material_residuals.py tests/test_material_evaluator.py tests/test_material_service.py tests/test_material_registry.py`. These files are NEW implementation work and do not currently exist. Regression command also runs the existing `test_mdo_variables.py`, `test_mdo_objectives.py`, `test_mdo_optimize.py`. Tests independently recompute array content hashes from the specified canonical header+bytes and require equal hashes after save/reopen; assert tampering fails. State tests compare two candidates/experiments in opposite evaluation order and prove fresh initial state; an unsupported path-dependent/complex request dispatches no job. Actual native/UI tests belong to M2/M3 and cannot be counted as passed by M1.

**M2 — complete user entry for the same framework.** Connect actual CSV column/unit/observable selection→parameter fixed/free/bounds→run/stop/cancel→observed/predicted/residual/holdout plots→save/reopen→export status. Use Prep widgets via host contracts where appropriate; own study state stays in WB. Test with an independent outside-repository CSV, actual Windows UI and missing-library/error/cancel flows. Numerical reference tests cannot substitute for that user acceptance.

**M3 — first supported native law bridge.** Choose an available installed solver/native law and externally authored single-element or coupon deck; native FE adapter uses existing JobManager/RunStore and deterministic extractor. It may precede or replace an in-process constitutive adapter for that model. Prove parameter→native bytes→run→measured quantity→same residual contract→actual confirmation. First Prep Apply can use current LAW1 E; unsupported complex laws stay export-only. Regression test original unedited fields/INCLUDE/order/line endings and revision-conflict/one-Undo behavior. Fixed-width RAD and INP codecs have separate format tests.

**M4 — additional model adapters and research levels.** Add each material/library/solver model by schema/capability/validation tests, not core branches. Different measures/multiple paths/temperature-rate dependence/Prony terms or hyperelastic test modalities are accepted only when the registered adapter and unit/experiment contracts support them. Existing open solver laws and available libraries are preferred over reimplementation. A model may expose material-library and native-FE levels independently; cross-level agreement is an explicit study report, never assumed. Expert review reads immutable artifacts and proposes changes; it does not silently run or apply them.

Expert acceptance counterexamples required by direct review: (a) two fits have the same aggregate score but one required shear/holdout test fails; its per-test artifact must lead to revise/failed-fit, not blanket pass; (b) a Cauchy/PK2 or gauge/axis mismatch yields no fit objective and insufficient evidence; (c) a cancelled/unparsed required test is visible but not an actual fit. Read-only expert snapshots contain the detailed evaluation artifact and preserve these distinctions.


### 9. Expert evidence handoff (agreed direct boundary)

Calibration is a distinct discussion `source_kind="calibration"` in a proposed discussion v2 union, with `calibration_ref` fixing study revision/evaluation ID/content hash. It is not inserted as a manual/native-run fake. Existing discussion v1 bytes are not automatically rewritten; the explicit-save v2 migration defined in the expert section remains available; existing stored files keep their fields and canonical ArtifactRef is supplied by an adapter. The evidence snapshot uses actual descriptor/parameters for model_text, actual per-test observed/errors/coverage for summary_text, and actual log text only if provided. Missing log is empty plus unavailable status. Solver/run/frame is null where absent or multiple; top units is `{}` with unit_source=`per_artifact`, and observable-specific units remain in artifacts. Hash failures/unknown adapters/required partial/measure mismatch remain visible as insufficient or ineligible. A native-derived field keeps its native_run+derived_result_ref path instead. No experiment is converted to a dummy FE run and no current result is relabeled to bypass evidence checks.

<a id="experts"></a>
## 일반 CAE 자문·검색·도구·답변 검토

[전문가 구현 명세](cae-research/expert-implementation.md)가 질문 준비·원문 corpus·검색/충분성·모든 답변 주장 검증·read tools·provider loop·독립 reviewer·재심 event의 정확한 schema/API/오류/fixture를 소유합니다. 이전 lexical coverage 단일 문턱/허용 질문 목록·빈 claims 통과·모호한 재심 연결 계약은 폐기했습니다. 8개 pinned 원문과 notice,16 source spans,36개 개발/60개 평가 문항 및 strict schema/golden은 인계 색인에서 실제 파일로 연결합니다. 이 fixture가 실 provider나 Windows 제품 흐름을 검증한 것은 아닙니다.

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
| `caelab/project.py:SCHEMA_VERSION=3/save_native_project` | 프로젝트 v3, RunStore.copy_to SaveAs | post-only sidecar는 v3 compatible; material_study role 최초 저장은 v4, reader/SaveAs는 v1–4 지원(core C8). operation selection은 RunStore sidecar. SaveAs가 active operation 중 실패하고 완료 operation을 재검증·복사해야 함 |

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

Dataset manifest includes immutable source/operation refs and artifacts, `dataset_kind=field|history`, `frames[]`, `series[]`, topology/ID maps, field support/coverage. Core C3 defines mandatory series records: field uses frames, history uses series with actual coordinate/source-row artifacts, empty frames and frame_id=registered series_id. Initial history operator is curve_extract only. Frame has stable `frame_id` string, `source_step`, actual `{quantity,value,unit,source}` sample coordinate(time/load/frequency); ordinal index is separate and never passed as physical time. `TIME` absent→unknown, not index.

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

공통 expert envelope의 `kind=native_run` payload.derived_result_ref로 ResultRef를 넣고 document/chunk와 content hash/provenance/availability의 바깥 형식을 공유한다. EvidenceEnvelopeV1의 exact kind/outer fields/payload union/legacy read adapter는 core C1에 고정한다. 전문가 read tools는 stable ref ID를 입력받아 실제 artifact hash를 검증한 뒤 조회하며 임의 filesystem path를 받지 않는다.

### 6. 프로세스·오류·취소와 이벤트

Conversion lifecycle: queued→preparing(verify+copy)→running(process group)→validating(read+field metadata+hash)→completed. 어떤 단계에서든 cancel→owned converter descendants 종료→temp close/quarantine→cancelled receipt 마지막 저장. timeout→failed/TIMEOUT, cleanup 미확인→failed/PROCESS_CLEANUP_FAILED. 변환이 일부 끝났어도 전체 request 취소면 completed dataset 등록 금지; 성공 frame을 다시 쓰려면 명시적 재시도 새 operation이 재검증한다.

하나의 JobManager만 Popen/Windows Job handle을 소유한다. conversion key별 RunStore operation lease를 사용하고 SaveAs는 root+operation leases와 충돌하게 한다. 같은 key가 이미 실행 중이면 OPERATION_IN_PROGRESS와 읽기용 opref 반환; 타 controller PID를 채택하지 않는다. GUI 창 닫기는 자신이 제출한 operation만 취소하고 cleanup 상태를 기다린다. RP 창 닫기는 solver/conversion을 취소하지 않는다. 사용자가 닫은 RP만 WB가 임의 재실행하지 않는다.

오류 codes: PROGRAM_UNAVAILABLE/UNSUPPORTED_CAPABILITY/STALE_PLAN/SOURCE_CHANGED/HASH_MISMATCH/PATH_ESCAPE/CONVERTER_EXIT/EMPTY_OUTPUT/INVALID_DATASET/MISSING_TIME/MISSING_ID/UNSUPPORTED_FIELD/UNSUPPORTED_BASIS/MEASURE_UNKNOWN/UNIT_UNKNOWN/PARTIAL_REQUIRED_FIELD/TIMEOUT/CANCELLED/PROCESS_CLEANUP_FAILED/STARTUP_FAILED/HANDOFF_MISMATCH. 불완전 자료는 원인+available fields를 보여준다. 실패→0 값·가짜 frame·다른 run·최신 cache로 대체하지 않는다.

### 7. 실제 converter·TH·RP의 상세 구현

[후처리 구현 명세](cae-research/post-implementation.md)가 source pin·라이선스·정확한 argv·input/output·실제 ID/TIME·tensor/층/축·TH binding·native oracle·Windows gate·실패/취소·RP CLI/receipt를 소유합니다. 이전 LAW1 NIP5·ordinal time·stdout CSV·VOL 초기부피 가정은 폐기합니다. 최초 TH4 one-point HEX8 profile은 선택 LAW33 재료 평가의 필수 전이 단계입니다. vanilla RP 수동 보기,WB 소유 수치 후처리,optional fork exact handoff는 각각 독립 인수합니다.

선택한 fork CLI는 `--post-only --handoff <json> --receipt <json>`이며 upstream에 존재하는 기능으로 표시하지 않습니다. 프로그램 실행을 후처리 수치/화면 일치의 성공으로 세지 않습니다.
