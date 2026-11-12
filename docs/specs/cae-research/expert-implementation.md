# 전문가 자문, 근거 검색, 읽기 도구와 답변 검토 구현 명세 C — 재구성본 2026-11-12

이 문서는 작업 환경이 소실된 뒤, 현재 대화에 남아 있는 구현 계약을 바탕으로 다시 작성한 복구본이다. 과거 `parts/expert_implementation.md`의 원문이나 해시 `b06a6cdc5c7001617cd85f4d1b68e57de6dba63effb7b25d062146a7cca01c62`와 같다는 뜻이 아니다. 원문 자료 모음, 실행 로그 또는 시험용 고정 자료를 복구하지 못했다면, 과거에 관측한 결과와 현재 실제로 확보한 자료를 구분해서 기록해야 한다. 검토 담당 E/F는 새로 저장한 Git 파일 객체(blob)를 다시 읽고 승인해야 한다. 이 문서는 제품 실행 환경, 실제 답변 공급자 연결 또는 Windows 지원이 완료되었다는 보고서가 아니다.

여기서 근거 검색은 검색한 자료를 답변에 함께 제공하는 검색 증강 생성(RAG)을 뜻한다. 시험용 고정 자료는 이후에 같은 조건으로 검사를 반복하는 데 사용하는 입력과 기대 결과다. 아래의 참조 시험 결과는 구현할 제품의 완료 상태와 구분해서 읽어야 한다. 답변 공급자는 AI 모델을 호출하는 서비스나 실행 프로그램을 뜻한다. 호스트는 도구를 실행하고 결과를 검증하여 저장하는 Workbench 코드를 뜻한다.

## 1. 사용자가 하는 작업과 기존 코드 재사용

DP/Xia 재료 모델은 질문 유형의 예시다. 전문가는 일반 구조 이론과 연속체 이론, 유한요소(FE) 모델링, 요소 선택, 구속 조건, 접촉, 수렴, 실행 실패, 재료 선택, 시험 설계와 결과 해석에 관한 질문을 받아야 한다. 이론과 실행 사례가 섞인 질문도 받는다. 자료 모음에 모델명이 없다는 이유만으로 질문하거나 원래 입력 덱을 실행하는 일을 막지 않는다.

이론 질문은 해석을 실행하지 않고도 시작할 수 있다. 혼합 질문은 실행 사례를 분석하는 `case_analysis`로 처리하며, 실제 첨부 자료와 검색 자료를 함께 사용한다. Prep은 원래 파일 형식의 편집과 적용(`Apply`), 실행 취소(`Undo`)를 담당한다. Workbench는 파일, 연구, 해석 실행과 결과를 관리하고 독립 전문가 창을 연결한다. 창이나 파일을 여는 동작만으로 대규모 언어 모델(LLM), 검색 또는 솔버를 호출하거나 모델을 변경하지 않는다. 유료 API, 새 데이터베이스(DB), 중복 작업 관리자 `JobManager`, 웹 사용자 화면 또는 공통 FE 스키마를 추가하지 않는다.

기존 코드를 확인한 기준은 Workbench(WB) `104cdd6888a5554eecc8cf965c60ff1bd980b6da`, Prep `555af5b3ab2c6e7dc276111609302ef565bb4fc8`, OpenCourantRP(RP) `cec23cbdd4058237e04fdcc7ecb06a3834578cae`다. 다음 표는 환경 소실 전에 실제로 읽은 코드에 관한 기록이다. 기존 API와 앞으로 추가할 API를 구분한다.

| 실제 경로와 심볼 | 재사용할 기능과 변경할 내용 |
|---|---|
| `caelab/assist/experts.py`: `ExpertProvider.discuss`, `OpenAICompatibleProvider.discuss`, `OpenScienceProvider`, `Discussion.build_prompt/ask` | 문자열을 주고받는 기존 `discuss`는 유지한다. OpenAI 호환 공급자는 현재 `messages`를 한 번 보내는 방식이다. 새 `provider_steps` 연결 계층이 도구 요청과 결과를 주고받게 한다. 기존 `allowed_tools`는 자료 종류를 가리키므로 별칭 `allowed_evidence_sources`를 두고, 실행 가능한 조회 도구는 새 `allowed_query_tools`로 분리한다. |
| `assist/openscience.py`: `SubscriptionTransport._installation/discuss/_checkpoint/_stop` | Windows 실행 환경 `2.0.146`, 프로필 표식과 해시 검사를 유지한다. 권한은 거부하고 `tools=false`로 두며 MCP와 플러그인을 비활성화한다. 요청을 서로 격리하고 자신이 시작한 자식 프로세스만 정리한다. `structured_action`은 호스트가 JSON을 해석해 반복 처리하는 방식이며, 공급자 자체 도구 기능을 활성화하는 방식이 아니다. |
| `assist/discussions.py`: `create_discussion/load_discussion/save_discussion/attach_evidence/ask_discussion/review_proposal`, `_lock/_current/_interrupt/_write` | 파일 잠금 `FileLock`, 예상 버전이 일치할 때만 저장하는 비교 후 교환(CAS), `atomic_json`, 요청 점유권과 중단 보존을 재사용한다. 버전 2에는 이론 질문에서 비워 둘 수 있는 근거와 요청 맥락, 추적 기록, 답변 검토, 이벤트를 추가한다. 기존 `check/reject`는 파일 변경 제안을 검토하는 기능으로, 답변 검토와 구분한다. |
| `assist/rag.py`: `Chunk/parse_markdown/parse_pdf/tokenize/BM25/HashingEmbedder/HybridIndex/save_index/load_index`; `knowledge.py`: `search_knowledge_result/verify_citations` | 토큰 분리기, 검색 순위 계산 방식 BM25, 여러 순위를 합치는 RRF와 선택한 접속 주소를 재사용한다. 512차원 해시 벡터는 의미를 학습한 모델이 아니다. 파일명으로 솔버나 판본을 추측하기보다 명세 목록에 적힌 출처를 우선한다. 인용 ID가 존재하는지와 자료가 주장을 뒷받침하는지는 따로 검사한다. |
| `native/evidence.py`: `capture_run_evidence/verify_evidence`; `Workbench.read_native_frame`; `native.frame_view.read_native_frame` | 파일 바이트, 해시와 프레임 검증에 사용하는 공통 `ArtifactRefV1`, `ResultRefV1` 및 소유 모듈의 참조 해석기를 사용한다. FRD 전용 `_finish`에 모든 결과 판독기를 넣지 않는다. |
| `apps/workbench_qt/expert_window.py`: `ExpertWindow/capture_run/ask/_start/_poll/cancel_request/render`; `native_analysis.py`: `request_expert_debate` | 독립 창, 작업 스레드, 결과 전달함과 자료 검증을 재사용한다. 생성자에 사용자가 요청한 정확한 프레임을 전달하고, 초안 보존, 취소, 늦은 응답 처리, CAS와 검토 결과 표시를 추가한다. |

한 `assist` 담당자가 새 `assist/{contracts,query_language,corpus,query_tools,provider_steps,answer_review}.py`와 `scripts/build_knowledge_index.py`를 작성한다. `experts/discussions`는 한 명이 편집하고 Qt 변경도 한 GUI 담당자가 편집한다. `workbench/artifact_refs/native` 공통 파일은 통합 책임자(root)가 편집자를 정한다. 핵심 계층은 Qt나 `apps`를 가져오지 않는다. 기존 `JobManager`를 우회해 하위 프로세스를 실행하거나 솔버 작업을 제출하는 도구를 만들지 않는다.

## 2. API와 문서 형식 버전 2

~~~python
@dataclass(frozen=True)
class ReassessmentLinkV1:
    review_id: str
    item_id: str
    original_context_hash: str   # mandatory lowercase64hex; no tuple2 alias

ask_discussion(path, question, *, evidence_id: str|None=None,
 template_id: str, provider_id=None, expected_revision:int,
 question_scope:str|None=None, review_policy_id='none',
 responds_to_review_ids:tuple[str,...]=(),
 check_cancelled=None, on_stage=None)->dict

prepare_query(question, *, query_language, explicit_facets, policy)->QueryPlan
build_corpus(manifest_path, *, output_root, review_overlay_path=None,
 verify_only=False, check_cancelled=None)->CorpusReceipt
run_grounded_request(context, provider, dispatcher, budget, cancelled)->GroundedReply
request_answer_review(path, *, turn_id, reviewer_provider_id, expected_revision,
 reassessment_of:tuple[ReassessmentLinkV1,...]=(),
 check_cancelled=None, on_stage=None)->dict
record_review_resolution(path, review_id, item_id, *, decision, reason,
 responding_turn_id, actor, expected_revision)->dict
~~~

`discussion` 버전 2는 기존 버전 1의 `id/revision/title/evidences/turns/constraints/failures/proposals/selected_evidence_id/draft_question/pending_request`를 보존한다. 여기에 `question_scope(theory|case_analysis)`, `review_requests`, `review_resolution_events`, `corpus_snapshot_ref`, `tool_policy_id`, `draft_context`, `draft_history`를 추가한다. 이론 질문인 `theory`에서는 `evidence=None`을 허용한다. 사례 분석인 `case_analysis`에는 유효한 첨부 자료가 있어야 한다. 구형 호출은 `case_analysis`로 해석한다. 질문 범위를 바꾸면 다음 질문부터 적용하며 과거 대화 차례(`turn`)는 수정하지 않는다.

각 `turn`은 `schema_version=2`로 저장하고 `question_scope/responds_to_review_ids/request_context_ref/provider_capabilities/tool_trace_ref/retrieval_decisions/claims/answer_review_ids/evidence_pack_hash/corpus_revision/budget_used/capability_limits` 필드를 추가한다. 해석 실행이 없는 이론 질문을 위해 가짜 실행, 프레임 또는 단위를 만들지 않는다.

근거 자료는 기존 `metadata/text/files`를 보존하며 `native_run/manual/calibration` 중 하나로 표현한다. 원래 해석 결과에서 파생된 자료는 `derived_result_ref`에 공통 `ResultRefV1`을 사용하고 `source_run_id/run_id`가 일치해야 한다. 재료 보정 근거인 `calibration`은 그 자료를 담당하는 모듈에서 실제 `CalibrationEvaluationArtifact`를 읽는다. 각 시험의 실제값(`actual`), 예측값(`predicted`), 오차(`loss`), 자료 충족 범위(`coverage`), 자료 분할(`split`), 학습에서 제외한 검증 자료(`holdout`), 매개변수(`parameters`), 물리량 정의(`measure`)와 단위(`unit`)를 전달한다. 원시자료가 없는 항목은 빈 텍스트와 `availability`로 표시하며 존재하는 것처럼 채우지 않는다.

공용 근거 교환 묶음(`EvidenceEnvelopeV1`)의 형식은 최신 핵심 계약 C1(`b3ec806dd6d5e52c5062c75fc98b1e829b2c08ac`)의 `EvidenceEnvelopeV1` 하나만 따른다. 형식은 `{schema_version:1,id,kind,content_sha256,provenance,availability,payload}`이고, `kind=native_run|manual|calibration|document_chunk`다. 원래 결과 참조 `ResultRef`는 `native_run payload.derived_result_ref`에 넣으며 별도의 `kind=result`를 만들지 않는다. `provenance.artifact_owners`는 핵심 계약이 정한 정확한 위치와 형식을 따른다. `source_kind`는 `native_run|manual|calibration|official_source|paper|solver_manual`로 검증한다.

`CorpusManifest`의 `official_manual`은 근거 교환 묶음의 `provenance`에서 `solver_manual`로, `reviewed_note/user_document`는 `manual`로 명시적으로 대응시킨다. `document_chunk`에는 정확한 `source_ref/chunk_ref`, 문서와 텍스트 해시, 실제 줄과 유니코드 코드 포인트 구간이 있어야 한다. `availability`, `kind`에 따라 정해진 한 가지 `payload` 형식만 허용하는 규칙, 근거 교환 묶음 전체의 해시 규칙은 C1을 그대로 사용하며 복제하지 않는다. 앞에서 말한 기존 `metadata/text/files` 보존은 구형 설명자를 정확한 원래 바이트로 읽는 변환 계층을 뜻한다. 새 근거 교환 묶음의 `payload`에 임의 필드를 추가하는 별도 교환 형식이 아니다. 구형 설명자의 해시와 그것을 감싸는 근거 교환 묶음의 해시는 서로 다른 식별값이다.

통합 책임자의 C6/C7를 적용한다. 재료 연구의 `document_revision`은 변경 가능한 명세 목록과 CAS 저장 버전이다. `content_revision`은 모델, 매개변수, 데이터, 비교, 정책, 수치 재현 절차와 최적화 계약으로 정한 변경 불가능한 내용 식별값이다. `calibration.describe`는 두 버전을 구분하고, 등록한 `optimizer_responses`, 필수 `optimizer_constraints`, `differentiable`, `constraint_status(FEASIBLE|MARGINAL|INFEASIBLE|NOT_ASSESSED)`와 원시 검사 결과를 보존한다.

정상적으로 계산했지만 오차가 큰 결과는 유한한 `fit_loss`와 `fit_status=fail`로 보존한다. `constraint_status`는 적합 오차와 독립적으로 판단한다. 유한한 물리 제약 위반은 유한한 손실과 `INFEASIBLE`로 기록한다. 필수 제약 스칼라 계산이 실패하거나 값이 비유한이면 `objective={eligible:false,fit_loss:null,reason_codes:[CONSTRAINT_RESPONSE_INVALID]}`, `constraint_status=NOT_ASSESSED`로 기록한다. `holdout` 평가가 학습 자료의 목적함수를 지우지 않는다.

통합 책임자의 C3에 따라 `DatasetManifest.dataset_kind=field|history`로 구분한다. 이력 자료인 `history`는 `frames=[]`, `series[{series_id,coordinate_ref,row_count,source_row_index_ref,field_ids}]`와 실제 원본 시간 배열을 가진다. 이 경우 `ResultRef.frame_id`에는 등록된 `series_id`를 사용한다. 첫 이력 조회 기능은 `curve_extract`이고 `geometry_state=unknown`이다. 초기 점 조회 도구와 RP 공간장 판독기는 `history`를 거부한다. 애니메이션 순번을 시간으로 대신 넣지 않는다.

요청 맥락 `RequestContext`는 원문 질문, 질문 범위, 제약, 초안의 기준 자료와 실제 첨부의 구별, 고정된 근거 해시, 자료 모음 버전, `QueryPlan`, 실제 표시한 자료 조각과 텍스트 해시, 도구 스키마와 별칭 및 정책 SHA, 솔버, 판본, 모델과 물리량 정의의 한계를 담는다. 이를 정규화한 JSON의 SHA로 고정한다. 답변 작성자가 종료하면 근거 묶음 `EvidencePack`도 고정한다.

`request_context_hash`는 질문, 범위, 제약, 선택 항목, 정책과 근거 지문을 담은 `RequestContext`의 정규화 해시다. `evidence_pack_hash`는 고정된 근거 묶음 바이트의 정규화 해시다. 두 값은 서로 대신할 수 없다. 재검토할 답변 차례에는 정확한 `reply/reply_sha256`와 두 해시를 각각 보존한다. 검토자의 주장 구간과 답변 전체 검토 범위는 그 차례의 실제 `reply`에서 다시 만든다. 원래 해결 이벤트와 `original_context_hash` 연결만 계속 원래 검토 맥락을 가리킨다.

원자료가 변조되면 `integrity_error`로 처리한다. 현재 모델 버전과 달라 오래된 이력 스냅숏은 과거 자료로 읽을 수 있지만 현재 설계에 관한 근거로 승격하지 않는다. 단위, 부호 또는 물리량 정의가 `unknown`이면 추측하지 않는다. 같은 `ArtifactRef/ResultRef`와 소유 모듈의 정본을 사용하며 LLM 인자로 경로나 루트 디렉터리를 받지 않는다.

## 3. 실제 자료 모음과 한국어·영어 검색

환경 소실 전에 실제 준비한 자료 모음은 1차 출처 8개와 원문 구간 16개였다. 명세 목록, 자료 조각, 평가 파일의 SHA256은 각각 `0a978ff71489001727236ad06de0c31bd9a9a28c5b3b2e731a1ddb888317c2ac`, `ecbf2fbb02864ef3ef5fbdcbfb390a70436e84a9d1b83c44816db6fc0a25aea4`, `7e32083012e320b3872991068510eadf93bc824f2e82e22403059636197b35e9`다. F는 정확히 이 해시를 가진 원시자료, 줄과 문자 구간, 평가 정답 표기 60개를 독립적으로 읽었다. 현재 원파일을 복구하지 못했다면 그 검토 기록을 새 자료 모음의 승인으로 재사용하지 않는다. 새 Git 파일 객체나 다시 확보한 바이트에는 새 명세 목록과 재검토가 필요하다.

선정한 원문은 다음과 같다.

- `FEniCS/dolfinx` 커밋 `b8375fa9fb932cbfce43005eeb4a3d52293f428b`(`v0.10.0`): `python/demo/demo_poisson.py`의 SHA256은 `38d6c205871b94e6d1d91d66d8014357e7631065c082e054fabfba9f3984af96`, `demo_elasticity.py`는 `607789cc3b74d49d575c5a9266fae46a19c06fad6528e1ceeaeadc02e8677363`, `cpp/demo/hyperelasticity/hyperelasticity.py`는 `061f807c7cd1cde03f580645860649a6ea3025555946ec1faae1320c5302a104`다. 라이선스는 `LGPL-3.0-or-later`이며 `COPYING/COPYING.LESSER/README` 고지를 보존한다.
- `GEOS-DEV/GEOS` 커밋 `d81a4438f107446f17e33ab70c70358935e473ab`: `src/coreComponents/constitutive/solid/DruckerPrager.hpp`의 Git blob은 `02e2a44fcf7ad2c53541533dcc126d9f53961cb6`, `DruckerPrager.cpp`는 `a64ae00030cbd75a322b30ae66591d1c0521c9a9`다. 라이선스는 `LGPL-2.1-only`이며 `LICENSE/COPYRIGHT/CONTRIBUTORS/NOTICE/ACKNOWLEDGEMENTS`를 보존한다. 여기서 Git blob SHA1은 파일 SHA256과 다르다.
- `NatLabRockies/pyvisco` 커밋 `b61b5a0c6a2939927c87189972ea5619503bd813`: `pyvisco/prony.py`의 SHA256은 `641490da54f1d62d5f631c899f3f0ade44d1f9debc98a9e9068fe4f0577795bb`, `shift.py`는 `93f143f9789e31aaffebc229fe76b0c3b37ffa0b4937d5b2980b1d524e2503a2`다. `BSD-3-Clause` 라이선스와 `LICENSE`를 보존한다.
- `Argonne-National-Laboratory/neml` 커밋 `65ab2a648a975e2e9de8e1371be270567f9683ac`: `src/hardening.cxx`의 SHA256은 `1575188d63689f68a5b0ac0a28006b582e1a92e7b3d14c56108e73f40892318f`다. `MIT` 라이선스와 `LICENSE`를 보존한다.

고정 원문 URL은 `https://raw.githubusercontent.com/<repository>/<commit>/<path>`로 구성한다. 소스 코드를 매뉴얼이나 논문으로 표시하지 않고, 핵심 계약 C1의 `source_kind` 열거값 `official_source`로 표시한다. 이 자료 모음이 OpenCourant 카드, 접촉, 발포체, 손상, Xia 원형 모델 또는 안전 인증을 모두 다룬다는 뜻은 아니다. 공개 소스 원문과 고지를 검토용으로 동봉하는 일과 제품 계산 핵심에 편입하는 일은 구분한다. 이용 제한이 있거나 권한이 불명확한 문헌은 사용자의 로컬 원문 루트에 두고 URL, 판본, 해시와 입수 절차만 추적한다. 질문 버튼 `Ask`가 자료를 자동 구매하거나 다운로드하지 않는다.

명세 목록의 필드는 `schema_version/corpus_id/revision/sources`다. 각 출처에는 `source_id/source_kind/title/edition/commit/uri/local_relative_path/sha256/byte_length/license{id,source_uri,redistribution}/solver_scope/parser{id,version}/extraction_quality`가 있다. 자료 조각 `Chunk`에는 `source_id/chunk_id/document_sha256/text/text_sha256/edition/section_path/line_start/end/char_start/end/page_start/end/coverage_facets`가 있다. 소스 파일의 페이지는 `null`로 두고 실제 1부터 시작하는 양 끝 포함 줄 번호를 사용한다. PDF는 `pypdf`로 실제 페이지와 목차를 추출한 뒤 원래 페이지를 렌더링한 화면과 대조한다. 분수, 음수 부호, 지수 또는 표가 손상되면 일부만 추출되었다는 `partial` 상태와 누락 절을 기록한다. 식을 조용히 수정하지 않고, 검토한 별도 메모 `reviewed_note`와 원문 구간을 만든다.

다시 확보한 뒤 재검사할 원문 구간은 다음과 같다.

| 자료 | 구간 |
|---|---|
| `poisson` | 줄 `23..67`, 구현 `98..164` |
| `elasticity` | 모드 `54..97`, 구성식 `118..156`, 풀이 `164..226`, 등가응력 VM `227..257` |
| `hyper` | 운동학 `28..58`, 에너지 `60..85`, 응력 `84..92` |
| `GEOS` | `hpp177..236/cpp77..122` |
| `prony` | 이완 `317..344`, 주파수 `465..488` |
| `WLF` | `13..40` |
| `NEML` | 상태 `657..670`, 진화식 `687..711` |

고정 무료 검색 구성 `cae-small-bm25-hash-v1`은 `BM25 k1=1.5,b=.75`, `hashing512 float32`, `RRF k60`, `LexicalReranker`를 사용한다. 원문 질의와 검색용으로 변환한 질의에서 각각 상위 20개를 가져와 합친 뒤 RRF로 결합하고 상위 5개를 제공한다. 해시 벡터는 의미 인코더가 아니며, 이 작은 자료 모음의 `Hit@5`를 일반적인 검색 품질로 확대 해석하지 않는다. 솔버, 판본 또는 물리량 정의를 명시한 질문에서 출처의 해당 분류가 `unknown`이면 충분한 근거가 아니다. 일반 이론으로 특정 솔버의 기본값을 입증할 수 없다. 벡터 차원, 자료형, 유한성, 임베딩 생성기 또는 명세 목록이 불일치하면 오류다. 몰래 다른 모델로 대체하지 않는다. 선택적인 로컬 임베딩을 쓰려면 새 구성 프로필과 독립 평가가 필요하다.

`QueryPlan`의 정본 필드는 `original_text/original_language,queries[{text,language,method,resource_revision}],policy_revision,term_spans,unmatched_spans,removed_spans,warnings`다. 유니코드 NFKC 정규화는 검색용 변형에만 적용하고 원문은 보존한다. 검토된 한영 대응표에서 가장 긴 구를 찾아 원문 구간 옆에 병기하는 방식으로 검색 변형을 추가하며, 원문을 삭제하지 않는다. 숫자, 단위, 솔버, 카드, 모델, 판본, 부정 표현 `not/no/without/않/아닌/없`, 조건과 비교 표현은 변경하지 못하게 한다. `removed_spans=[]`이며 대응하지 못한 구간도 원문에 남긴다. 검토하지 않은 언어나 주요 구에는 `LANGUAGE_COVERAGE_UNVERIFIED`, 안전하게 해석할 수 없는 질의에는 `QUERY_LANGUAGE_UNSUPPORTED` 경고를 기록한다. 이 경고는 관련 자료가 없다는 판단과 구분한다. 공급자에게는 원문 질문을 항상 전달한다.

어휘 일치 범위와 검색 순위는 진단용이다. 이것만으로 후보를 삭제하거나 `out_of_scope`로 분류하거나 주장이 뒷받침되었다고 결정하지 않는다. `RetrievalDecision.status=supported`는 검토한 관련 후보를 제공했다는 뜻이며 주장 검토 통과를 뜻하지 않는다. 필요한 분류 조건이나 실제 추출 자료가 부족하면 `insufficient`와 `missing_evidence`로 기록한다. 솔버 또는 판본 불일치, 원문 누락, 후보 없음과 요구사항 미검증을 구분한다. 알 수 없는 모델은 범위 밖으로 분류하지 않는다. 명백히 CAE와 무관한 질문에만 `out_of_scope`를 사용한다. 질문은 계속 허용하되, 모델의 일반 지식에만 의존하는 `model_general_unverified` 설명을 인용 또는 검토 통과로 승격하지 않는다.

색인 생성 CLI는 `--manifest/--output/--review-overlay`와 `--verify-only|--build`를 제공한다. 원문, 고지, 해시와 승인된 검토 기록의 대상 해시를 검사한 뒤, 같은 볼륨의 임시 디렉터리에 허용된 원문 사본, 검토, 자료 조각, 벡터와 명세 목록을 쓴다. `fsync`와 해시 검사를 마치면 새 불변 디렉터리 `indexes/<digest>`로 이름을 바꾼다. `current.json`은 임시 파일에 쓰고 `fsync+os.replace`로 교체한다. 비어 있지 않은 디렉터리는 교체하지 않는다. 동일한 다이제스트는 바이트까지 일치할 때만 재사용한다. 실패하거나 취소되면 현재 색인 포인터를 유지하고 자신이 만든 임시 디렉터리만 정리한다. 요청을 시작할 때 색인 버전을 고정한다. 구형 색인은 `legacy_unknown`으로 표시하며 정밀 출처 검사 합격에서 제외한다.

평가용 고정 자료는 개발용 36개(`covered12/insufficient12/nonCAE12`)와 평가용 60개(`covered24=한영12engineering intents/insufficient18/nonCAE18`)로 구성한다. 원문 질문, 기대 자료 조각(`expectedchunks`), 충족 의무, 부족 사유, 정답 표기 담당자와 원문 해시를 함께 저장한다. 다시 만든 시험 자료를 과거 평가 파일과 같다고 표현하지 않는다.

근거가 있는 12개 질문 의도는 다음을 포함한다. Poisson Neumann 약형의 부호, 3차원 강체 모드 6개, Lamé 상수 변환, 편차응력의 VM, 정확한 압축성 neoHookean 에너지, `PK1→Cauchy`, GEOS의 `trialQ+FtrialP-oldcohesion`, 서로 독립적인 마찰과 팽창, Prony 평형항 `1-sum(alpha)`, `omega rad/s`와 `Hz`의 구별, NEML의 6성분 이력, `dev(s)+X`의 부호 대응이다.

부족 사례 18개는 `RadiossLAW36 interpolation/CalculiXtemperature default/Xiaoriginal/contactpenalty/uniqueness/safety/LAW10 equivalence/ccxRFsign/Nortonexponent/anisotropyvariant/temperatureE/viscousstate/restart/buckling/strainmeasure/allhex/neweditionAPI/unattachedrunvalue`다. 각각 Radioss LAW36 보간, CalculiX 온도 기본값, Xia 원형, 접촉 페널티, 유일성, 안전성, LAW10 동등성, ccx 반력 부호, Norton 지수, 이방성 변형, 온도 의존 E, 점성 상태, 재시작, 좌굴, 변형률 정의, 모든 육면체 요소, 새 판본 API와 첨부되지 않은 실행값을 묻는 반례다. 비CAE 18개는 날씨, 요리, 가격, 스포츠, 번역, 운전면허, 식당, 호텔, 계좌, 선거, 주식, 축사, 메일, 비밀번호, 예약, 이름, 체스와 영화 요청이다. 별도 언어 반례에는 일본어, 프랑스어, 대응되지 않은 한국어, 부정 조건과 잘못된 판본을 포함한다.

새 시험 자료는 독립적인 출처 검토와 한영 정답 검토를 거쳐 고정한다. 다음은 목표이며 실측 결과가 아니다. `coveredHit@5>=22/24`, 정확한 부족 사유 `>=17/18`, 비CAE 질문에 잘못 승인한 출처 0개, 잘못된 솔버나 판본을 특정 근거로 제시한 사례 0개다. 한영 질문 쌍을 독립적인 60개 의도로 부풀려 세지 않는다. 평가 결과를 본 뒤 구성을 조정하면 새 프로필과 새 평가 세트를 사용한다. 실제 답변은 근거가 있는 의도 12개와 부족 의도 6개에 대해 주장, 인용문, 수치, 단위, 부호와 물리량 정의를 독립 검토한다. 허위 인용 0개, 첨부되지 않은 수치의 발명 0개, 근거 부족 답변의 통과 0개가 조건이다.

## 4. 읽기 도구와 공급자 통신

도구 호출 형식은 `ToolCallV1={call_id,tool_name,arguments}`다. 결과 형식은 다음과 같다.

`ToolResultV1={call_id,status:ok|partial|error,data,error:{code,message,retryable}|null,evidence_refs,provenance:{request_context_hash,corpus_revision,source_hashes,run_id,frame_index,units,basis,measure},limits:{truncated,valid_count,total_count},elapsed_ms}`.

모든 JSON 객체는 `additionalProperties:false`를 적용하고 필수 키를 빠짐없이 요구한다. 중복 키, `NaN/Inf`, 정수 대신 사용한 불리언을 거부한다. 동작 메시지 크기는 UTF-8 바이트 기준 32KiB이며 JavaScript 문자열 길이로 세지 않는다. JSON 공백은 RFC의 `space/tab/CR/LF`만 허용한다. 파서 객체는 `null prototype`을 사용한다. 허용 ID, 답변 구간, 인용 목록에 속하는지는 `Object.hasOwn`으로만 검사한다. `__proto__/constructor/toString` 같은 상속 키가 엄격한 스키마나 ID 목록 검사를 우회하지 못하게 한다. `call_id`는 1..128자이며 같은 요청 안에서 중복될 수 없다.

| 표준 도구명과 공급자 별칭 | 정확히 요구하는 인자 |
|---|---|
| `knowledge.search↔knowledge_search` | `query` 1..2000자, `solver_scope`는 맥락에 등록된 ID, `limit`은 정수 1..5 |
| `evidence.describe↔evidence_describe` | `evidence_id`는 맥락에 등록된 ID |
| `evidence.query_field↔evidence_query_field` | `evidence_id/field_id/component_id/selection_id`는 맥락에 등록된 ID, `operator values|min|max|mean|norm`, `operator_version='1'`, `limit`은 정수 1..1000 |

출력은 고정된 자료 조각과 명세 목록, 실제 근거의 메타데이터 또는 같은 `ResultRef`에 대한 조회 결과다. 임의 셸, Python, SQL, HTTP, 경로, 자유 수식, 솔버 제출 또는 모델 변경은 허용하지 않는다. 프레임은 근거 자료에 고정하며 인자를 추가해 바꾸려는 요청은 거부한다. `values`는 `ResultRef.operator identity v1`을 사용한다. `min/max/mean/norm`은 허용된 연산자만 교체한 요청 참조를 만들며 기본 참조와 원본, 데이터셋, 프레임, 선택 범위는 바꾸지 않는다. 추적 기록에는 기본 참조 해시와 요청 참조 해시를 모두 남긴다.

첫 지원 프로필은 검증된 VTU의 절점 스칼라 또는 성분 조회다. `mean`은 유한한 절점값의 산술평균이며 공간평균이 아니다. `values`는 ID, `null`, 유효성 및 개수를 반환한다. `norm`은 선언한 단위가 같고, 정규 직교 기저가 알려져 있으며, 정확히 3성분인 벡터에서 `component='vector'`일 때 절점별 유클리드 노름을 계산한다. 텐서, 미상 기저 또는 혼합 단위는 지원하지 않는다. 비유한 값을 0으로 채우지 않는다. 단위를 알 수 없는 원시값 조회는 진단용이며 적합 평가나 물리 주장 검토를 통과시키는 근거로 쓰지 않는다.

셀, 적분점(IP), 층과 이력은 검증 프로필을 등록하기 전까지 지원하지 않는다. D의 합성 시험 자료인 5MPa `ResultRef`는 `describe`에 그대로 표시하지만 초기 점 조회는 거부하고 `fit=false`를 유지한다. RP 또는 ParaView 창을 열었다는 사실만으로 수치 조회가 성공한 것은 아니다.

`ProviderCapabilities`의 필수 필드는 `provider_id/model_id/transport_mode(native_tools|structured_action|prompt_only)/supported_tool_schema_version/max_context/configured/probe_status(not_run|passed|failed|unsupported)/last_probe_at`다. `max_context`는 실제 확인한 토큰 수 또는 `null`이다. 접속 주소, 모델, 통신 방식 또는 스키마 해시가 바뀌면 연결 시험 결과는 무효다. 사용자가 명시적으로 연결 확인을 요청했을 때, 합성 ID에 대한 무해한 `evidence_describe` 호출, 그 결과, 엄격한 최종 응답을 주고받는 POST 2회를 실제 관측해야 공급자 자체 도구 시험을 통과시킨다. 설정이 있다는 사실만으로 지원 확인이 된 것은 아니다.

`ChatToolsAdapter.step(messages,*,tools,deadline,cancel)->ProviderStep`는 설정된 접속 주소의 `/chat/completions`에 `{model,messages,tools,tool_choice:'auto',stream:false}`를 보낸다. 인증은 명시한 것만 전송한다. `assistant.content=null+tool_calls`는 정상이다. 원래 assistant 메시지를 추가하고 별칭과 스키마를 검사한 다음, 호스트가 순서대로 도구를 실행한다. 같은 `tool_call_id`의 `role=tool/content=ToolResultJSON`을 추가하고 같은 `messages`로 다음 POST를 보낸다. 알 수 없는 별칭이나 잘못된 인자는 정해진 한도 내 오류로 반환하고, 서버 프로토콜 손상은 요청을 종료한다.

`400/404/422` 응답이 도구 미지원을 명시하면 `unsupported_capability`로 기록하고 실행하지 않는다. 묵시적으로 메시지만 보내는 방식으로 바꾸지 않는다. `401/403`은 `unavailable`, `429`는 `rate_limited`, `5xx`는 `provider_error`, 시간 초과는 `timedout`이다. 네트워크 재시도는 자동으로 하지 않는다.

OpenScience의 `structured_action`은 기존 권한 거부와 `tools=false`를 유지한다. 격리된 각 `discuss` 요청에 원문 질문, 고정된 맥락, 스키마와 이전 도구 결과를 구별된 데이터로 다시 전달한다. 전체 응답은 정확한 `{type:'tool_request',call_id,tool,arguments}` 또는 엄격한 최종 응답이어야 한다. 코드 울타리, 부연 문장, 추가 필드와 중복 필드는 거부한다. 호스트가 JSON을 해석하는 반복 처리이며 공급자 자체 함수 호출로 표시하지 않는다. 실제 구독 연결 시험 전에는 `unavailable`이다. `prompt_only` 일반 자문은 계속 가능하지만 도구 사용으로 승격하지 않는다. 권한이나 프로필을 자동 수정하거나 유료 API 사용을 선행 조건으로 두지 않는다.

## 5. 최종 응답 형식, 답변 전체 검토와 검토자 교환 형식

공급자 자체 도구 방식과 구조화 동작 방식은 같은 최종 JSON을 사용한다.

`{type:'final',answer:str1..20000,claims:[{text_span:[start,end],citation_ids:[shownIDs]}]}`

`response_format` 지원을 가정하지 않고 `content`의 JSON을 검사한다. 공급자가 보내는 주장 힌트에는 호스트 ID, 맥락 또는 행위자를 추가하지 못한다. 힌트 구간과 실제 표시한 인용 ID를 검사한다. 힌트가 없거나 일부만 있어도 허용하지만 `not_assessed`로 표시한다. 힌트는 답변 전체 검토 범위의 정본이 아니다. 공급자 자체 도구 방식의 일반 문장, 코드 울타리와 알 수 없는 필드는 `invalid_provider_action`이다. 구형 `prompt_only`의 문자열 답변은 `not_assessed`로 유지하며, 사용자가 명시적으로 검토를 요청하면 같은 전체 범위 검사를 적용한다.

호스트는 빈 줄 구분자 `\n[ \t]*\n`로 문단을 나누고 양 끝 공백을 제외한 유니코드 코드 포인트 범위 `[start,end)` 목록을 만든다. 답변의 모든 비공백 문자는 정확히 한 구간에 속해야 한다. 구간 ID는 `UUID5(requestID,start,end,canonicalanswerSHA)`다. 코드와 표도 포함하며 공급자의 힌트로 구간을 삭제할 수 없다. 초기 `ClaimV1={hostUUID,text_span,citation_ids,basis:evidence|model_general_unverified}`는 근거 지원 여부가 아직 미검토 상태다. 한 문단에 명제가 여러 개면 검토자가 겹치지 않는 부분으로 나누어 전부 평가한다.

검토자가 반환할 정확한 교환 형식은 다음과 같다.

~~~text
{type:'answer_review',
 segments:[{segment_id,parts:[{start,end,kind:claim|nonclaim,citation_ids,
 support:supported|contradicted|not_assessed|not_applicable,reason,
 quoted_spans:[{citation_id,start,end}]}]}],
 modeling_checks:[{category:assumptions|units|measures|BC|mesh|contact|convergence|interpretation,
 result:satisfied|issue|not_assessed|not_applicable,reason}],
 items:[{segment_id,part_index,category,severity:minor|major,reason,evidence_refs,required_action}],
 required_evidence:[str],limitations:[str]}.
~~~

모델은 검토 ID, 원답변 차례, 맥락, 행위자, 최종 판정, 상태 또는 시간을 보내지 못한다. 식별 필드를 추가하면 `invalid_provider_action`이다. 호스트가 실제 고정 요청의 답변 작성자, 검토자, 요청 ID, 원답변 차례, 맥락, 독립성, 재검토 연결, 시작 및 완료 시각과 결과를 합쳐 완전한 `AnswerReviewv1`을 저장한다. 주장과 지적 항목 ID도 호스트 UUID로 만든다. `claim_checks`에는 `text_span/citationIDs/id_valid/quoted_span_valid/support/reason`을 기록한다. `items`는 `ReviewItem.item_id/claim_id/category/severity/reason/evidence_refs/required_action`에 대응한다.

호스트는 각 구간이 정확히 한 번 나오는지, 알 수 없는 구간이 없는지, 부분 구간이 해당 범위 안에 있고 서로 겹치지 않는지, 모든 비공백 문자가 검토되었는지 확인한다. 표시한 인용인지, 실제 원문 인용 구간인지도 검사한다. 모델링 검사에는 중복 없이 정확히 8개 범주가 있어야 한다. 이 범주는 가정(`assumptions`), 단위(`units`), 물리량 정의(`measures`), 경계조건(`BC`), 격자(`mesh`), 접촉(`contact`), 수렴(`convergence`), 해석(`interpretation`)이다. `supported` 주장에 실제 인용과 인용 구간이 없으면 `not_assessed`로 처리한다.

주장이 아닌 `nonclaim`은 `not_applicable`과 이유를 요구한다. 순수 인사나 정형 제목만 정확한 허용 목록으로 인정한다. 한영 허용 목록은 `Summary/Sources/Limitations/Assumptions/Review/Hello/Thanks`와 `요약/참고자료/한계/가정/검토결과/감사합니다/안녕하세요`다. 수치 또는 기술 명제를 `nonclaim`으로 숨기면 `insufficient`다.

최종 판정 `verdict`는 다음 규칙으로 계산한다. 답변 범위 누락, 빈 검토, 주장 0개, 미검토 부분, 필수 범주 누락, `required_evidence` 또는 허용되지 않은 `nonclaim`이 있으면 `insufficient`다. 그 밖에 반박된 주장(`contradicted`), 모델링 문제 또는 중대 지적(`major`)이 있으면 `revise`다. 모든 주장이 뒷받침되고 필수 검사를 충족했거나 이유가 있는 `not_applicable`일 때만 `pass`다. 구문 해석 오류, 알 수 없는 인용, 식별값 위조 또는 구간 겹침은 `failed+insufficient/invalid_provider_action`으로 처리하고 원래 반론을 보존한다.

검토 범위는 `{answer_sha256,nonwhitespace_count,covered_nonwhitespace_count,segment_count,checked_segment_count,claim_count,complete,nonclaim_policy_id:'cae-nonclaim-v1'}`로 변경 불가능한 검토에 저장한다. 기계적으로 모든 문자를 검토했다는 사실은 의미가 옳다는 보장이나 사람의 인증이 아니다.

`AnswerReviewv1`의 필수 필드는 `schema_version/review_id/source_turn_id/request_context_hash/proposer/reviewer/independence/verdict/status/claim_checks/modeling_checks/disagreements/required_evidence/limitations/reassessment_of/coverage/created_at/finished_at`다. 역할을 여러 개 요청해도 다수결로 자동 통과시키지 않는다. 같은 모델의 별도 요청은 `separate_request_same_model`, 다른 모델은 `separate_model`로 표시하며 사람의 승인과 구분한다. 검토자는 같은 고정 근거 묶음과 기존에 허용한 결정적 읽기 도구만 사용한다. 새 문헌이나 프레임이 필요하면 `insufficient`로 기록하고 새 답변 차례와 새 검토를 진행한다.

## 6. 반론 처리, 해결 이벤트와 저장

사용자가 ‘이견에 답하기’를 선택하면 기존 초안을 보존하고 `responds_to_review_ids`를 설정한 새 답변 차례를 만든다. 재검토에는 `ReassessmentLinkV1`의 세 필드 배열을 사용한다. 새 근거 묶음으로 진행한 검토가 과거 지적을 해소하려면 정확한 과거 `review_id/item_id/original_context_hash` 연결과 실제 완료된 검토자 요청이 있어야 한다. 두 필드 튜플 별칭은 허용하지 않는다. 과거 판정, 근거 묶음과 이벤트는 변경하지 않는다.

`record_review_resolution`의 정본 이벤트 형식은 다음과 같다.

~~~text
{schema_version:1,event_id,review_id,item_id,responding_turn_id,decision:keep|resolve|withdraw,
 reason,actor:{kind:user|reviewer,reference_id},request_context_hash,created_at}.
~~~

이벤트의 맥락은 원래 검토 해시다. 새 검토의 맥락은 `actor.reference_id`가 가리키는 실제 완료 요청과 재검토 연결로 검증한다. 답변 작성자가 답변만 추가해 스스로 지적을 닫을 수 없다. 행위자는 실제 사용자 화면 동작 또는 완료된 검토자 요청이어야 한다. `kind=reviewer`로 지정해도 답변 작성자의 요청을 가리키면 거부한다. 상태는 이벤트에서 계산한 조회값 `unresolved/resolved/withdrawn`이며 별도의 변경 가능한 정본을 두지 않는다. 뒤이어 `keep` 이벤트가 오면 미해결 `unresolved`로 다시 연다.

저장은 `FileLock→expected_revision CAS→같은 discussion의 review/item/turn 확인→responds_to_review_ids 확인→원 claim/evidence/context hash 확인→실제 actor 확인→새 review의 source context/reassessment 확인→append→atomic_json` 순서로 진행한다. 원래 연결이 없거나 틀리면 `REVIEW_CONTEXT_MISMATCH/details.reason=EVIDENCE_SCOPE_MISMATCH`이고 이벤트를 만들지 않는다. 공개 사유의 정본은 `UNKNOWN_REVIEW/UNKNOWN_ITEM/UNKNOWN_TURN/CROSS_DISCUSSION/EVIDENCE_SCOPE_MISMATCH/REFERENCE_HASH_MISMATCH`다. 원본 바이트 손상의 `integrity_error`, CAS 충돌의 `DiscussionConflict`, 점유권 충돌의 `DiscussionBusy`와 구분한다. `check_id/response_turn_id`나 새 사유 열거값을 만들지 않는다.

읽기 예산 `cae-read-v1`은 준비 20초, 공급자 호출 합계 60초, 전체 85초다. 반복은 4회, 도구 호출은 총 6회, 한 반복에서는 최대 3회를 순서대로 실행한다. 프롬프트는 60000자, 결과 텍스트는 20000자, 값은 1000개, 동작 메시지는 32KiB다. 한 반복에서 요청한 호출 수가 남은 예산보다 많으면 그 반복 전체를 `budget_exhausted`로 처리하고 일부만 실행하지 않는다. 단조 시계로 계산한 남은 시간을 HTTP, CLI, 임베딩과 읽기 중단 확인 지점에 전달한다. 답변 작성과 검토를 자동으로 함께 실행하도록 명시한 경우 사용자 화면에는 요청 2개를 표시한다. 이때 전체 170초, 공급자 120초, 도구 호출 12회다. 기본값으로 자동 이중 호출하지 않는다.

취소하면 `flag/provider.cancel`을 설정하고 새 반복과 도구 실행을 중단한 뒤, 가능한 범위에서 자신이 소유한 자원을 정리하고 `cancelled`로 끝낸다. 차단된 호출의 응답이 나중에 올 수 있으므로 정리 상태와 원격 중단 여부에 대한 추정을 구분한다. 요청 ID, 문서 ID, 버전과 취소 세대가 일치할 때만 결과를 저장한다. 늦은 결과는 `ignored_late`로 감사 기록만 남기고 현재 결과로 승격하지 않는다.

추적 이벤트는 `assets/requests/<id>/events/<seq>.json`에 배타적 생성으로 쓰고 `fsync`하며 이전 해시를 연결한다. 종료 `trace.json`은 순서 있는 참조, 개수와 상태 명세 목록을 담은 불변 파일이며 `ArtifactRef`로 참조한다. 인증, 토큰 또는 비공개 프로필 설명자를 저장하지 않는다. 사용량은 실제 확인한 값만 기록한다.

버전 1을 읽을 때는 메모리에서만 버전 2 조회 형태로 변환한다. 과거 질문 범위는 사례 분석이며 인용 ID만 검사하고 주장 검토는 `not_assessed`다. 자동 저장이나 LLM 호출은 하지 않는다. 사용자가 처음 저장하면 `FileLock→원 id/revision/hash 확인→assets/migrations/<v1sha>.json`을 `xb/fsync`로 기록하고 기존 파일이 있으면 정확히 일치하는지 확인한 뒤, CAS를 재확인하고 `atomic_json`으로 버전 2 본문을 저장한다. 백업과 본문 전체가 하나의 트랜잭션인 것처럼 취급하지 않는다. 본문 저장이 실패하면 버전 1과 백업을 보존한다.

버전이 2보다 크거나 파일이 손상되었거나 경로가 잘못되었거나 비유한 값이 있으면 거부하고 덮어쓰지 않는다. 진행 중 요청의 점유권이 없으면 메모리에서 `interrupted`로 표시하고 초안, 일부 추적 기록과 반론을 보존한다. 자동으로 재호출하지 않는다. 활성 요청 중 다른 이름으로 저장(`SaveAs`)은 사용 중 `busy`로 거부한다. 저장할 때 참조하는 자료, 허용된 원시자료, 자료 조각, 맥락, 추적과 검토의 전체 참조 묶음을 복사하고 참조 해석기의 루트만 옮긴다. 외부 원문을 동봉하지 못하면 가용성, 해시와 URL을 보존하며 재검토 시 `source_unavailable`로 처리한다.

## 7. Qt 화면, 완료 판정 기준과 현재 구현 범위

다음은 구현할 화면 동작과 검사 기준이다. `ExpertWindow`는 독립 창이며 대화 기록을 중심에, 입력을 아래에 둔다. 내부 ID와 JSON 덤프를 기본 화면에 나열하지 않는다. 문서명, 절, 페이지, 줄과 자료의 한계를 사람이 읽을 수 있게 표시한다. 생성자의 기존 키워드 인자 뒤에 `requested_frame_index:int|None/frame_context_state`를 추가한다. 원래 결과 화면과 연결하는 코드도 프레임 0을 `is not None`으로 확인해 전달한다.

근거 캡처의 기본값은 사용자가 지정한 정확한 프레임이다. 해당 프레임이 사라졌으면 자리 표시자와 직접 선택 기능을 제공한다. 결과가 전혀 없으면 기존 확인 동작으로 입력과 로그 옵션을 선택하고 `frame=None`으로 두며 가짜 수치를 만들지 않는다. 다른 실행이나 프레임을 선택할 때 초안의 기준 자료와 실제 첨부를 구별해 표시하고 사용자 초안을 보존한다. 자동 생성한 부분만 갱신하고 이력을 남긴다. 추가 승인 체크박스를 요구하거나 문장 판정 때문에 질문을 막지 않는다.

`Ask/Cancel`, 검토, 이견 답변, 재검토, `keep/resolve/withdraw`, 저장과 재열기를 같은 서비스 명령에 연결한다. 작업 중인 스레드, 새 창과 닫힌 창의 늦은 결과가 서로 섞이지 않게 한다. GUI 스레드에서는 네트워크, PDF 또는 공간장 자료를 읽지 않는다.

사용자가 제안을 수용하면 Prep 또는 전용 편집기에서 `Apply`를 실행한다. 이때 버전을 검사하고 기존 `JobManager`로 새 해석을 실행한 뒤 새 근거 캡처, 새 답변 차례와 새 검토를 진행한다. 과거 실행의 `pass`를 새 실행으로 복사하지 않는다. 지원하지 않는 변경은 전용 앱을 안내하고 미적용 상태로 둔다.

실행 검증과 완료 판정 기준은 다음 단계로 구분한다.

| 단계 | 확인할 내용 |
|---|---|
| `E0/E1` | 정확한 프레임, 이론 질문, 버전 2 백업, CAS, 열기만으로 호출하지 않는 동작 |
| `E2a` | 실제 원시자료, 라이선스, 원문 구간과 한영 정답 표기의 독립 검토 |
| `E2b` | 고정한 개발 36개와 평가 60개, 같은 자료 모음에 대한 BM25 및 혼합 검색 |
| `E3` | 엄격한 스키마, 같은 `ResultRef`, 공급자 자체 도구의 POST 2회, 구조화 동작, 미지원 처리, 취소, 늦은 결과와 예산 |
| `E4` | 답변 전체 검토, 반론, 새 근거 묶음, 연결, 이벤트와 저장 |
| `E5` | Prep 문서 버전 일치, 보정의 실제값과 예측값 및 자료 분할, 제약, 원본 변경 없음과 새 작업 생성 없음 |
| `E6` | 실제 공급자, Windows, 사용자 `Apply`와 새 실행 뒤 후속 검토 |

환경 소실 전 참조 시험에서는 원문 8개, 원문 구간 16개, 개발 36개와 평가 60개, 해시 검사를 수행했다. 로컬호스트에서 POST 2회로 도구를 모사하고 엄격한 최종 응답을 검사했으며, 주장과 검토자에 대한 부정 검사 15개, 부분 힌트의 전체 검토 범위, 누락되거나 잘못된 재검토 연결에서 이벤트가 0개인 결과를 `PASS`로 관측했다. 이 기록은 원로그를 재생성하거나 새 Git 파일 객체로 시험을 실행했다는 주장이 아니다.

재구성본에서는 새 스키마, 기준 정답 자료와 참조 자료를 실제로 만들어 검토하고, 실행 가능한 환경에서 재실행해야 한다. 실제 제품 색인, Qt 화면, 공급자 연결 계층, 실제 LLM 답변의 공학적 품질, 실제 솔버까지 이어지는 전체 흐름과 Windows 검증은 미구현 또는 미실시 상태로 남아 있다.
