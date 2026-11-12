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

요청 맥락 `RequestContext`는 원문 질문, 질문 범위, 제약, 초안의 기준 자료와 실제 첨부의 구별, 고정된 근거 해시, 자료 모음 버전, `QueryPlan`, 요청을 시작할 때 이미 확정한 초기 자료 조각과 텍스트 해시, 도구 스키마와 별칭 및 정책 SHA, 솔버, 판본, 모델과 물리량 정의의 한계를 담는다. 이를 정규화한 JSON의 SHA로 요청 시작 시 고정한다. 이후 도구가 새 자료 조각이나 수치 인용을 반환해도 `request_context_hash`를 다시 계산하지 않는다. 실제로 보여준 모든 자료 조각, 표시 바이트와 해시, 도구 결과 및 `CitationRecordV1`은 최종 근거 묶음 `EvidencePack`에 누적하고 답변 작성자가 종료할 때 고정한다. `RequestContext`에 최종 근거 묶음 해시를 소급 삽입하지 않으므로, 도구 결과와 인용은 같은 시작 맥락 해시를 안정적으로 참조한다.

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

어휘 일치 범위와 검색 순위는 진단용이다. 이것만으로 후보를 삭제하거나 `out_of_scope`로 분류하거나 주장이 뒷받침되었다고 결정하지 않는다. `RetrievalDecision.status=supported`는 출처 검토와 9.3절의 질문별 관련성 검토를 통과한 관련 후보를 제공했다는 뜻이며 주장 검토 통과를 뜻하지 않는다. 필요한 분류 조건이나 실제 추출 자료가 부족하면 `insufficient`와 `missing_evidence`로 기록한다. 솔버 또는 판본 불일치, 원문 누락, 후보 없음과 요구사항 미검증을 구분한다. 알 수 없는 모델은 범위 밖으로 분류하지 않는다. 명백히 CAE와 무관한 질문에만 `out_of_scope`를 사용한다. 질문은 계속 허용하되, 모델의 일반 지식에만 의존하는 `model_general_unverified` 설명을 인용 또는 검토 통과로 승격하지 않는다.

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

호스트는 각 구간이 정확히 한 번 나오는지, 알 수 없는 구간이 없는지, 부분 구간이 해당 범위 안에 있고 서로 겹치지 않는지, 모든 비공백 문자가 검토되었는지 확인한다. 표시한 인용인지와 정확한 인용 구간인지도 검사한다. 문헌은 실제 원문 구간을, 수치는 9.2절의 호스트 고정 표시 구간과 원수치 위치·해시를 함께 검사한다. 모델링 검사에는 중복 없이 정확히 8개 범주가 있어야 한다. 이 범주는 가정(`assumptions`), 단위(`units`), 물리량 정의(`measures`), 경계조건(`BC`), 격자(`mesh`), 접촉(`contact`), 수렴(`convergence`), 해석(`interpretation`)이다. `supported` 주장에 실제 인용과 인용 구간이 없으면 `not_assessed`로 처리한다.

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

사용자가 Prep 또는 전용 편집기에서 `Apply`를 누르면 현재 문서 버전을 검사하고 변경을 적용한다. 이후 사용자가 별도 동작으로 새 해석이나 평가를 시작한 경우에만 기존 실행 관리 경로로 실행하고 새 근거 캡처, 새 답변 차례와 새 검토를 진행한다. 해석 실행은 기존 `JobManager`가 담당하며 적용만으로 실행하지 않는다. 과거 실행의 `pass`를 새 실행으로 복사하지 않는다. 지원하지 않는 변경은 전용 앱을 안내하고 미적용 상태로 둔다.

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

## 8. 기존 계획에서 이어받는 작업과 질문별 선행조건

이 절은 [기존 개선 계획](../../review/2026-10-23-codex-improvement-plan.md)의 P4, P5, P6에 이 명세를 연결한다. 기존 P5/WBS-4/이슈 #45의 첨부 검증 잔여는 `E0-2`가 이어서 마무리한다. `E3-5`는 같은 실행 틀 위에 실제 AI의 읽기 도구 사용과 독립 답변 검토를 추가한다. 수치 판독을 추가하는 `POST01-04`와 외부 RP 표시를 추가하는 `POST05-06`은 같은 작업의 반복 완료로 세지 않는다. 통합 작업 ID와 의존 관계의 정본은 [구현 작업 연결표](implementation-map.json)다.

| 기존 항목 | 재사용할 부분 | 이 명세에서 추가할 부분과 완료 책임 |
|---|---|---|
| P4의 실행 기록과 결과 판독 | 기존 `JobManager/RunStore`, 실행별 입력 사본, 로그, 알려진 결과 판독 | `E0-2`는 선택한 실행과 프레임을 정확히 전달한다. `E3-5`의 수치 조회는 그 결과 형식을 실제로 지원하는 판독 프로필을 사용한다. P4 실행 성공만으로 도구 조회가 완료되었다고 하지 않는다. |
| P5의 독립 전문가 연결, WBS-4와 #45 | 기존 독립 `ExpertWindow`, 첨부 검증 작업 스레드, 자산 변경 시 재검증과 초안 저장 | `E0-2`가 검증 중 표시, 검증 완료 및 실패 표시, 닫힌 창의 늦은 결과 격리, 실행·프레임 변경 시 초안과 실제 첨부의 구분을 맡는다. #45를 별도 중복 작업으로 다시 배정하지 않는다. |
| P5의 지식 검색과 역할별 자료 제한 | 기존 검색기와 공급자 인터페이스, 역할별 자료 종류 제한 | `E0-2`는 원문과 판본, 한영 질의, 근거 부족 판단과 평가를 구현한다. `E3-5`는 실제 공급자의 도구 요청·반환과 별도 검토 요청을 확인한다. 역할 이름이나 검색 적중으로 독립 검토 완료를 대신하지 않는다. |
| P6의 제안 적용과 재실행 | 기존 전용 프로그램 수정 경로, 명시적 사용자 적용과 실행 | `E3-5`는 `E5/E6` 검사에서 현재 문서에 대한 적용, 새 실행, 새 근거와 후속 검토가 이어지는지 확인한다. Prep 적용의 정본은 `PREP01`과 핵심 계약 C10이며, 전문가 도구에 쓰기 권한을 추가하지 않는다. |
| P6의 저장과 재개 | 기존 대화 파일, 자산 저장, CAS와 원자적 저장 | `E0-2`가 질문·첨부·구형 문서 읽기를, `E3-5`가 도구 추적·검토·반론·세 필드 재검토 연결의 저장과 재개를 확인한다. 프로젝트 연결은 `STORE01/LINK01`과 같은 저장 계약을 사용한다. |

7절의 `E0/E1/E2a/E2b`는 통합 작업표 `E0-2`의 세부 검사다. `E3/E4/E5`는 `E3-5`의 세부 검사이고 `E6`는 실제 공급자와 Windows 및 전체 사용자 흐름을 확인하는 최종 검사다. `E6`라는 이름으로 같은 기능을 한 번 더 개발하거나 별도 완료 수를 늘리지 않는다. #45의 Windows 화면·입력 검사는 이 최종 검사에서 수행하며, `E0-2`가 만든 검증 상태와 늦은 결과 격리를 그대로 대상으로 삼는다. 기존 PR82/87에서 반영한 작업 스레드와 자산 동등성 검사는 재사용하며 같은 기능을 다시 개발하지 않는다. 남은 수정은 `E0-2`와 `E3-5`에 한 번 배정하고 같은 검사 근거를 연결한다. 새 작업의 검사와 원 이슈의 남은 조건을 모두 확인한 뒤 #45 종료를 별도로 판정한다.

### 8.1 이론, 실제 해석 결과와 재료 평가 질문은 선행조건이 다르다

설계 계약 합의, 해당 코드를 구현하는 데 필요한 자료, 최종 사용자 인수에 필요한 연계를 구분한다. 공용 근거 교환 형식은 모두 `CORE01`을 따르지만 모든 질문이 솔버 실행이나 RP 표시를 기다릴 필요는 없다. `E3-5` 전체에 `POST01-04`를 일괄 선행시키지 않는다.

| 질문과 첨부 종류 | 기능을 구현하고 시험할 때 필요한 것 | 해당 분기의 최종 완료 검사 |
|---|---|---|
| 결과 없이 하는 일반 CAE 이론 또는 문헌 질문(`theory`) | `CORE01`, `E0-2`의 질문·원문·검색. 결과 실행, 재료 동정, 메셔와 RP는 선행조건이 아니다. | 실제 공급자에게 원문 질문과 실제 표시한 원문 구간을 전달한다. 실제 `knowledge.search` 요청과 반환, 답변, 독립 검토를 확인한다. 관련 자료가 없거나 판본이 다르면 부족 근거를 유지한다. 가짜 `run/frame`을 만들지 않는다. |
| 실제 실행의 입력·로그·결과를 묻는 질문(`case_analysis`, `kind=native_run`) | 유효한 고정 첨부와 그 결과 형식을 지원하는 판독기. OpenCourant A/T01의 새 수치 조회에는 `POST01-04` 중 해당 `OC-ANIM-1/OC-TH4-1` 프로필이 필요하다. 이미 검증한 VTU 절점 조회에 모든 새 TH·층 프로필을 요구하지 않는다. | 실제 선택 실행·프레임·필드·단위에 대해 `evidence.describe`와 지원되는 `evidence.query_field`의 값 및 추적 해시를 확인한다. 필요한 결과가 없으면 입력·실패 로그로 논의할 수 있지만 없는 수치를 채우거나 수치 검토를 통과시키지 않는다. |
| 재료 평가·동정 결과를 묻는 질문(`case_analysis`, `kind=calibration`) | `CORE02`가 실제로 생성한 고정 `CalibrationEvaluationArtifact`와 소유 모듈의 판독 기능. native 해석을 사용한 평가는 그 평가를 만들 때 해당 POST 판독기가 필요하지만, 이미 다른 계산기로 평가한 자료를 읽기 위해 POST 전체나 RP를 요구하지 않는다. | `evaluation_ref`, `study_id`, `content_revision`, `evaluation_id`로 고정한 실제 산출물을 읽는다. 시험별 실제값과 예측값, 비교 설정, 잔차, 학습·제외 검증 분할, 제약, 식별성, 부분 실패 및 계산 출처까지 따라갈 수 있어야 한다. 모델이 만든 추정 수치를 평가 결과로 표시하지 않는다. |

재료 근거는 공통 C1의 `payload={evaluation_ref,study_id,content_revision,evaluation_id}` 형식을 사용한다. 이 명세가 별도 평가 DTO를 만들지 않는다. 원자료, 변환 절차, 모델과 매개변수, 실제 계산기 및 소스·라이브러리 식별값, 운영체제와 비교 정책은 평가 참조를 따라 실제 파일까지 검증할 수 있어야 한다. 과거 평가의 `content_revision`, 시험, 단위 또는 하중이 현재 연구와 다르면 과거 근거로 보존하되 현재 평가로 재사용하지 않는다.

혼합 질문에는 실제 사용한 분기의 조건을 각각 적용한다. 자료가 없는 분기가 있다고 다른 이론·문헌 질문까지 막지 않는다. 사용자가 사례 분석을 요청했는데 첨부가 없으면 첨부를 선택하거나 결과가 필요 없는 이론 질문으로 명시적으로 바꿀 수 있게 한다. 자료 부족을 충분한 근거로 바꾸지 않으며 기존 `case_analysis`의 유효 첨부 조건도 없애지 않는다.

독립 전문가 프로그램의 직접 파일 불러오기도 P5의 별도 진입점으로 확인한다. 해석 창이나 내부 FE 모델 없이 입력·로그·결과 파일을 선택할 수 있어야 한다. 실행 기록을 확인할 수 없는 외부 파일에는 가짜 `source_run_id`를 붙이지 않는다. 원문과 가용성을 보존하는 수동 근거로 다루며, 정밀 수치 조회는 공용 소유 관계에 등록되고 해당 판독 프로필을 통과한 결과 참조에만 허용한다. 이 경로의 인수는 수동 근거(`manual evidence`)로 파일 가져오기, 원바이트와 출처·선언 단위 확인, 누락·판독 실패 표시, 관련 문헌 질의와 독립 검토, 저장·종료·새 인스턴스 재열기까지 직접 이어 확인한다. #45의 검증 중 표시와 닫힌 창의 늦은 결과 차단도 이 직접 불러오기 경로에 적용한다. Workbench가 선택 실행을 전달한 검사로 이 별도 인수를 대체하지 않는다.

처음 지원하는 `evidence.query_field`는 4절의 검증된 VTU 절점 스칼라·벡터 성분이다. 셸, 적분점, 층과 이력은 해당 프로필과 도구 계약을 검증하기 전에는 `describe`만 허용하거나 미지원으로 반환한다. POST에서 층 또는 시간 이력을 읽을 수 있다는 사실만으로 초기 점 조회 도구가 그것을 조회할 수 있다고 표시하지 않는다. 지원 확대는 기존 공용 스키마와 연산자 계약을 정한 뒤 별도 검사로 추가한다.

### 8.2 실제 AI와 독립 검토의 완료 증거

다음 네 종류의 증거는 서로 대체할 수 없다.

| 증거 | 확인할 내용과 한계 |
|---|---|
| 형식 및 모사 시험 | 엄격한 JSON, 허용 도구, 예산, 취소, 늦은 응답, CAS, 답변 전체 범위와 세 필드 재검토 연결을 검사한다. 고정 응답이나 로컬호스트 POST 2회는 이 단계의 증거다. 실제 AI의 자료 이해나 공학 품질을 입증하지 않는다. |
| 실제 공급자의 자료 읽기 | 구성한 실제 공급자·모델·통신 방식으로 도구 요청, 호스트의 실제 판독, 같은 요청 ID의 반환, 최종 응답을 관측한다. 4절의 연결 시험과 실제 작업의 추적 기록을 구분한다. 설정 파일이나 `probe_status=passed`만으로 실제 질문 완료를 주장하지 않는다. |
| 실제 답변의 독립 검토 | 고정한 같은 근거 묶음으로 별도 검토 요청을 실제 실행한다. 원문 인용, 수치, 단위, 부호, 물리량 정의와 가정을 독립적으로 확인하고 이견·부족·수정·재검토를 저장한다. 3절의 독립 질문 평가와 5절의 전체 답변 검토 규칙을 모두 적용한다. 같은 모델의 별도 요청과 다른 모델 요청의 독립성 표시는 그대로 유지한다. |
| Windows 전체 사용자 인수 | 사용자가 질문을 쓰고 첨부를 선택한 뒤 실제 답변, 도구 추적 요약, 별도 검토와 반론을 보고 저장한다. 새 프로그램 인스턴스로 다시 열어 질문과 결과를 이어 가며 실제 화면·입력·취소를 확인한다. Qt offscreen 통과나 Linux 공급자 실행으로 대체하지 않는다. |

실제 공급자 검사는 DESIGN §10이 요구하는 실제 구독 LLM 연구팀 흐름을 포함한다. OpenScience 구독과 OpenAI 호환 접속은 각각 실제 확인한 범위를 표시한다. 로컬 호환 모델을 확인했다고 OpenScience 구독의 구현 또는 검증까지 통과시키지 않는다. 연결 환경이 없거나 지원 기능이 부족하면 해당 공급자를 미실시 또는 미지원으로 남기되 일반 프로젝트 열기와 다른 질문 분기를 막지 않는다. 기존 실제 A/B 답변 이력은 재사용 가능한 관측 기록이며 이번 새 도구·독립 검토 검사의 실행 결과로 합산하지 않는다. 기본 자동 이중 호출 금지와 명시적 요청별 예산은 6절을 유지한다.

### 8.3 저장 후 이어 가는 전체 사용자 흐름

최종 인수는 질문 분기별로 다음을 실제로 이어 확인한다. 사용자는 이론 질문을 시작하거나 실제 결과·고정 재료 평가를 선택한다. 원문 또는 선택 자료의 출처와 부족한 부분을 확인한 뒤 질문을 전송하고, 실제 읽기 결과와 답변 및 별도 검토를 받는다. 반론에 답하면 기존 반론을 보존한 새 차례와 재검토가 만들어져야 한다.

설계 변경이 필요한 경우에만 사용자가 Prep 또는 전용 편집기에서 적용을 실행하고, 별도 동작으로 새 해석이나 평가를 시작한다. 새 결과는 새 근거로 붙이고 이전 검토 통과를 복사하지 않는다. 적용을 지원하지 않는 제안은 미적용과 전용 프로그램 안내로 남긴다. 사용자 적용 또는 실행 전에는 원본 변경과 새 작업이 0개여야 한다.

프로젝트와 대화를 저장하고 프로그램을 종료한 뒤 새 인스턴스로 다시 연다. 선택 실행과 프레임, 원문 구간, 고정 평가, 질문 초안, 답변, 도구 추적, 검토, 반론과 해결 이벤트가 같은 해시와 관계로 복원되어야 한다. `SaveAs`로 실제 보관한 스냅숏 자료는 원래 루트를 삭제한 뒤에도 보관본만으로 읽을 수 있어야 한다. 외부 문헌이나 결과를 동봉하지 않은 관계는 원래 URL·해시·출처를 보존하고 `source_unavailable/partial/corrupt` 등 실제 상태로 구분한다. 없는 외부 자료까지 재열기 성공으로 세지 않으며 다시 내려받거나 솔버·AI·동정을 자동 실행하지 않는다.

이 절은 계획과 구현할 완료 기준을 보완한다. 문서 연결, 기존 시험 통과 또는 작업표의 상태 변경만으로 실제 공급자, 독립 검토나 Windows 인수가 끝났다고 보고하지 않는다.

## 9. 역할별 읽기 권한, 실제 수치 인용과 새 질문의 검색 판정

이 절은 1절의 기존 `ExpertTemplate.allowed_tools`를 새 조회 도구에 적용하는 계약을 구체화한다. 기존 코드에서 `allowed_tools`는 도구명이 아니라 자료 종류다. 기존 역할의 자료 권한은 유지하고, 도구 이름을 바꾸거나 `describe`로 같은 본문을 읽어 권한을 우회하지 못하게 한다. 다음 API와 레코드는 전문가 내부 계약이며 공통 `EvidenceEnvelopeV1`, `ArtifactRefV1`, `ResultRefV1`을 변경하지 않는다.

### 9.1 역할과 반환 자료를 함께 검사한다

자료 종류는 기존 `result_summary/model_summary/solver_log/knowledge`를 사용한다. 각각 검증된 결과 요약, 입력 모델과 계수 요약, 솔버 로그, 문헌 근거를 뜻한다. `kind=native_run/manual/calibration`만 보고 자료 전체를 허용하지 않는다. 한 파일이나 평가 자료에 여러 자료 종류가 섞여 있을 수 있으므로, 호스트가 등록된 판독 프로필에 따라 반환 필드와 인용마다 출처 분류를 붙인다. 모델이 분류를 지정하거나 바꿀 수 없다.

| 역할 ID와 이름 | 허용하는 기존 자료 종류 | `knowledge.search` | `evidence.describe` | 새 결과 수치 조회 권한 |
|---|---|---|---|---|
| `convergence`, 수렴·접촉 진단 | `result_summary,model_summary,solver_log,knowledge` | 허용 | 이 행의 자료만 골라 반환 | 아래 버전 2의 선택 수치 보기만 허용 |
| `material`, 재료 모델 선택 | `result_summary,model_summary,knowledge` | 허용 | 로그 본문을 제외하고 반환 | 아래 버전 2의 선택 수치 보기만 허용 |
| `interpretation`, 결과 해석 | `result_summary` | 거부 | 결과 요약만 반환 | 아래 버전 2의 선택 수치 보기만 허용 |
| `next_test`, 다음 시험 계획 | `result_summary,solver_log` | 거부 | 결과 요약과 로그만 반환 | 아래 버전 2의 선택 수치 보기만 허용 |
| `theory`, 일반 CAE 이론 자문(신규) | `knowledge` | 허용 | 실행·모델·보정 근거는 거부 | 거부 |

결과 없이 시작하는 `question_scope=theory`의 기본 역할은 새 `template_id='theory'`다. 화면에 선택 역할과 읽을 수 있는 자료를 표시한다. 기존 대화의 역할, 사용자 초안 또는 선택 첨부를 화면 이동만으로 바꾸지 않는다. 사용자가 `interpretation/next_test`를 선택하면 문헌 읽기 불허를 설명하되 질문 자체를 막거나 역할 권한을 몰래 늘리지 않는다. 혼합 질문도 문장 분류만으로 차단하지 않는다. 역할 선택에 별도 확인 클릭을 요구하지 않는다.

원래 `result_summary`를 임의 원배열 전체를 읽는 권한으로 해석하지 않는다. 새 `RoleReadPolicyV1`은 기존 자료 권한과 별도로 `allowed_query_tools`, `allowed_result_projections`, `template_revision`, `policy_id`, `policy_sha256`를 가진다. 기존 요청의 정책 `cae-role-read-v1`에는 새 수치 보기 권한이 없다. 새 요청의 등록 정책 `cae-role-read-v2`는 위 네 기존 역할에 `selected-node-scalar-v1/selected-node-vector-v1`을 명시적으로 부여한다. 이 두 프로필은 4절의 정확한 선택 절점, 필드·성분, 허용 연산, 단위·기저와 마스크를 검증한 값만 반환한다. 수치 결과에는 실제 `projection_id`와 기본 및 요청 `ResultRef` 해시를 표시한다. 이를 과거 `result_summary`로 위장하지 않는다. 이 별도 수치 보기 권한도 기존 자료 권한과 교집합으로 적용한다. 등록 프로필이 결과 값 영역으로 선언한 부분만 읽으며, 원파일에 함께 있는 모델·로그·문헌 영역을 수치 보기라는 이름으로 제공하지 않는다. 새 정책을 적용한 역할 설명과 정책 식별값은 요청 맥락에 고정하며, 과거 요청을 새 권한으로 재해석하지 않는다.

`allowed_query_tools`는 위 표와 결과 보기 권한에서 계산한다. 모든 역할에 도구 세 개를 일괄 제공하지 않는다. `describe`는 맥락에 등록된 근거만 받고, 허용 필드와 가용성만 반환한다. 예를 들어 보정 자료의 오차·잔차·평가 상태는 결과 요약, 모델·계수·시험 설정은 모델 요약, 실행 로그는 솔버 로그, 논문 본문은 문헌이다. `interpretation`에서 보정 자료를 설명해도 숨긴 계수나 로그 또는 참고 논문 본문을 함께 직렬화하지 않는다. 필드 목록도 요청에 등록된 허용 선택 범위만 보이며 임의 원배열 목록을 탐색하는 기능으로 쓰지 않는다.

권한 검사는 도구 목록 제공 전, 요청 맥락 생성, 도구 실행 전, 반환 자료 선택, 인용 생성, 과거 발언 삽입과 검토자 요청 각각에 적용한다. 권한을 벗어난 도구·근거·결과 보기 요청은 `ToolResultV1.status=error,error.code=POLICY_DENIED,retryable=false,data=null,evidence_refs=[]`다. 권한 검사와 거부 응답 생성 함수에서는 해당 자료 본문 읽기와 공급자 호출이 모두 0회다. 이미 도구 요청을 만들기 위해 수행한 공급자 호출을 0회라고 기록하지 않는다. 바깥의 정상 도구 반복은 거부 응답도 같은 `call_id`의 도구 결과로 공급자에게 돌려줄 수 있다. 이때 다음 POST는 기존 반복·시간 예산을 소비하며 실제 호출 수에 포함한다. 거부 처리 함수가 별도 공급자 요청을 시작하거나 예산 밖 재시도를 하지는 않는다. 거부를 감추기 위한 다른 자료·역할·모델로의 자동 재시도는 없다.

역할을 바꿔도 원 대화와 반론은 로컬 기록에 보존한다. 새 공급자 요청에는 출처 분류가 새 역할의 권한 안에 모두 들어오는 과거 발언만 넣는다. 출처 분류가 없는 구형 발언, 실패 설명이나 자동 요약은 안전하다고 추정해 넣지 않는다. 허용된 발언을 요약해 우회 전달하지도 않는다. 사용자가 직접 쓴 질문과 제약은 원문 사용자 주장으로 구분하며 검증된 근거 인용으로 승격하지 않는다. 역할과 정책 변경은 새 요청의 맥락·시각·변경 전후 식별값에 기록한다.

독립 검토는 원답변의 고정 근거 묶음과 원역할의 상한을 유지한다. 검토자 역할이 더 넓어도 그 상한을 넓히지 않는다. 검토자의 권한이 더 좁으면 읽을 수 없는 부분을 부족 근거로 남긴다. 다른 역할의 추가 자료가 필요하면 사용자가 새 답변 차례와 새 검토를 요청해야 한다. 과거 검토를 덮어쓰거나 더 넓은 검토자 역할로 닫지 않는다.

필수 거부 시험은 다음과 같다. `interpretation`의 `knowledge_search`, `material`의 로그 요청, `theory`의 `evidence_query_field`를 도구 목록 제공과 직접 호출 양쪽에서 거부한다. 같은 내용을 `describe`, 보정 JSON 전체, 과거 발언, 자동 요약 또는 검토자 역할 변경으로 읽는 시도도 거부한다. 거부 처리 함수의 대상 본문 읽기 0회와 공급자 호출 0회를 확인한다. 실제 도구 왕복 시험에서는 첫 도구 요청과 거부 결과 뒤의 정상 후속 POST를 각각 계수하고, 두 호출을 거부 처리의 호출 0회와 혼동하지 않는다. 반대로 허용된 자료 요약과 버전 2의 등록 수치 보기는 실제 읽기로 반환하고, 원역할 자료 제한과 수치 보기 권한을 서로 독립적으로 검사한다.

### 9.2 문헌 구간과 실제 수치를 연결하는 인용 정본

5절의 `citation_ids/quoted_spans`는 모델이 만든 설명문을 다시 근거로 삼는 형식이 아니다. 실제 자료를 읽은 호스트가 다음 내부 API로 인용 기록과 표시 텍스트를 만든다.

~~~python
build_citation_view(source, *, request_context, role_policy, projection_profile,
                    resolver, check_cancelled=None) -> CitationRecordV1
resolve_citation(citation_id, *, frozen_pack, request_context_hash,
                 role_policy, resolver) -> ResolvedCitationV1
~~~

`CitationRecordV1`의 공통 필드는 `{schema_version:1,citation_id,citation_kind,request_context_hash,role_policy_sha256,source_labels,source,renderer_id,renderer_version,display_ref,text_sha256}`다. 모든 키는 필수이며 `additionalProperties=false`다. `citation_kind`는 전문가 내부의 `document_chunk|numeric_result|calibration_scalar|manual_text|manual_scalar`이고 공통 근거 교환 묶음의 `kind/source_kind`와 혼동하지 않는다. `display_ref`는 호스트가 만든 UTF-8 표시 텍스트의 공용 `ArtifactRefV1`이다. `text_sha256`은 그 정확한 바이트의 SHA256이다. 표시 텍스트에 모델이 만든 요약이나 문장은 넣지 않는다. 표시 생성기 식별값과 버전도 고정한다. `SaveAs`와 재열기에서는 원래 표시 바이트로 인용을 검사하며 새 언어·지역 표시 설정(로캘)이나 생성기로 다시 만들어 대체하지 않는다. 다른 언어의 화면 안내는 별도 표시이며 인용 본문을 바꾸지 않는다.

`source`는 종류별로 다음 한 가지 형식만 가진다. 아래 `locator`는 요청자가 입력한 자유 경로나 JSON 포인터가 아니다. 설치해 등록한 판독 프로필이 정하고 호스트가 검증한 원본 내 위치다.

| 인용 종류 | `source`의 필수 정보와 검증 |
|---|---|
| `document_chunk` | 기존 근거 교환 묶음 ID, `source_ref/chunk_ref`, 문서 및 텍스트 해시, 실제 `span`을 기록한다. 표시 텍스트는 해당 원문 구간의 정확한 텍스트다. 문서 판본, 페이지와 줄은 원문 연결 정보로 보존한다. |
| `numeric_result` | 고정 `ToolResultV1` JSON의 `tool_result_ref`, 실제 `NumericResultV1` JSON의 `numeric_result_ref`, 기본 `source_result_ref:ResultRefV1`, `request_ref_sha256`, `projection_id`, `locator`, 연산자와 버전, 단위·기저·물리량 정의, 유효·전체 개수와 잘림 여부를 기록한다. 두 JSON과 참조된 배열·선택·마스크의 해시를 검증하고 같은 결과인지 확인한다. |
| `calibration_scalar` | `evaluation_ref,study_id,content_revision,evaluation_id,locator`와 원평가의 유효성·평가 상태 위치를 기록한다. 예를 들어 적합 손실은 실제 `/objective/fit_loss`, 적격성은 `/objective/eligible`를 검증한다. 원본 평가 형식이 다르면 그 형식에 등록한 포인터를 사용하며 임의 문자열 검색으로 값을 찾지 않는다. |
| `manual_text` | `legacy_evidence_ref`, 원문 `ArtifactRefV1`, 텍스트 추출 프로필과 버전, 추출 기록 참조, 원문 바이트 및 코드 포인트 구간을 기록한다. 추출 텍스트와 원바이트 대응이 확인된 부분만 제공한다. |
| `manual_scalar` | 위 수동 근거의 원파일 및 추출 기록, 등록된 요약 스칼라의 `locator`, 실제값·단위·마스크를 기록한다. 검증한 원자료와 등록된 연산으로 재현되지 않는 수치는 만들지 않는다. 저장된 요약 문자열에 숫자가 있다는 사실만으로 인용을 만들지 않는다. 검증한 추출 프로필·버전, 원파일 SHA, 정확한 위치, 선언 단위·측도와 필요한 연산의 재현값을 모두 대조한다. 이 증명이 없으면 수치 주장은 `supported`가 될 수 없다. 원실행 계보를 확인할 수 없는 한 `ResultRefV1`이나 `source_run_id`는 넣지 않는다. |

위 표의 `source`를 구현할 때는 다음 종류별 필드를 정확히 사용한다. 모든 필드가 필수이며 필요 없는 값만 등록된 자료형 계약에 따라 `null`을 허용한다. 포인터·참조·단위·상태는 호스트가 원본에서 만들고 모델이 제출하지 않는다.

~~~text
document_chunk: {envelope_id,source_ref,chunk_ref,document_sha256,text_sha256,span}
numeric_result: {tool_result_ref,numeric_result_ref,source_result_ref,request_ref_sha256,projection_id,locator,scalar_value,operator,operator_version,unit,dimension,unit_status,basis,measure,counts:{valid,total},truncated}
calibration_scalar: {evaluation_ref,study_id,content_revision,evaluation_id,locator,eligibility_pointer,assessment_pointer,scalar_value,unit,dimension,unit_status}
manual_text: {legacy_evidence_ref,source_ref,parser_id,parser_version,parse_receipt_ref,byte_span,codepoint_span}
manual_scalar: {legacy_evidence_ref,source_ref,parser_id,parser_version,parse_receipt_ref,locator,scalar_value,quantity,component,unit,dimension,unit_status,basis,measure,operator,operator_version,validity}
~~~

`scalar_value`는 실제 읽은 유한한 숫자이며 불리언을 거부한다. `dimension`은 등록한 물리 차원 또는 `null`, `unit_status`는 `declared|unknown`이다. `validity`는 불리언이다. 위 구조의 모든 파일 참조는 공용 `ArtifactRefV1`, 원실행 참조는 공용 `ResultRefV1`을 사용한다. `span`과 실제 평가·배열의 구조를 해당 소유 모듈의 정본과 대조하며 전문가가 별도 원결과 형식을 만들지 않는다.

수치 `locator`의 정본 형식은 `{artifact_id,json_pointer,index,shape,dtype,mask_ref}`다. JSON 스칼라는 `json_pointer`로 위치를 정하고 `index/shape/dtype/mask_ref`는 해당 자료형 계약이 허용한 `null`로 둔다. 배열은 배열을 보관한 공용 파일 참조와 실제 `index/shape/dtype/mask_ref`를 사용하고 등록 형식에 맞는 `json_pointer` 또는 `null`을 쓴다. JSON 포인터는 RFC6901의 이스케이프와 정확한 키·배열 인덱스를 검증한다. 값 찾기, 수식 평가, 경로 이동, 다른 파일 검색이나 마지막 값 대체는 금지한다. 큰 배열은 원배열 해시와 선택·마스크 및 연산자 입력 범위를 확인한 뒤 고정한 수치 결과의 선택값이나 집계값에 연결한다. 집계는 원래 등록된 연산자 의미와 동일해야 한다.

호스트는 인용할 스칼라를 실제 원값과 대조하고, 불리언·비유한 값·`mask=false`·선택 밖 값·숨겨진 잘린 값을 거부한다. 집계에는 유효·전체 개수, 실제 선택 범위, 연산자와 잘림 한계를 함께 표시한다. 선택 절점의 산술평균을 공간평균으로, 일부 위치의 최댓값을 전체 모델이나 전체 두께 최댓값으로 바꾸지 않는다. `unit=unknown`인 진단 원값으로 물리량 비교나 적합 평가 주장을 통과시키지 않는다. 정규화 손실 `fit_loss`는 공통 계약의 `unit=""`를 유지하면서 내부 인용 메타데이터에 `dimension=dimensionless,unit_status=declared`를 기록한다. 미선언 단위의 빈 표시와 구분한다.

수치 표시 텍스트는 등록된 고정 형식으로 실제 값, 단위, 선택, 연산자, 개수와 원자료 식별값을 손실 없이 적는다. 수치는 왕복 변환해 같은 유한 값을 얻는 JSON 표기를 사용한다. 인용 ID는 호스트가 요청 맥락 해시, 역할 정책 해시, 인용 종류, 원파일 해시와 위치, 표시 텍스트 해시로 UUID5를 만들어 발급한다. 같은 숫자라도 실행, 프레임, 작업, 단위, 선택, 연산자 또는 연구 내용 버전이 다르면 같은 인용으로 재사용할 수 없다. 인용 기록과 표시 텍스트는 근거 묶음에 포함하고 묶음 해시는 바깥에서 계산한다. 기록 안에 자기 자신을 포함한 묶음 해시를 넣는 순환 해시를 만들지 않는다.

답변 작성자와 검토자에게 실제 제공한 표시 텍스트만 `shown_chunks[citation_id]`에 넣는다. 이 이름은 구형 검증기와의 연결명이며 모든 항목이 문헌 조각이라는 뜻이 아니다. 화면과 프롬프트에는 문헌 원문, 실제 수치 조회, 보정 평가 또는 수동 자료의 종류를 표시한다. 5절 `quoted_spans`의 `[start,end)`는 해당 요청에 실제 보여준 이 텍스트의 유니코드 코드 포인트 범위다. 공급자에게 보이지 않은 원배열 값이나 같은 값이 적힌 다른 자료를 뒤늦게 인용 목록에 넣지 않는다.

`resolve_citation`은 요청 맥락과 정책, ID 소속, 표시 바이트·해시, 원파일과 위치, 원값·단위·마스크·선택 및 근거 가용성을 다시 확인한다. 해시·위조 인용 오류는 기존 무결성 또는 `invalid_provider_action` 처리로 남긴다. 실제 자료가 없거나 단위·범위가 부족하면 그 주장은 `not_assessed`이고 검토는 `insufficient`다. 원값이 주장과 다르면 `contradicted`다. 원문 구간과 실제 수치의 기계적 일치는 의미상 주장 전체의 옳음을 보장하지 않으므로 독립 검토자의 `support/reason/modeling_checks`도 필요하다.

다음 세 가지 성공·실패 쌍을 구현 전에 고정하고 실제 호스트 코드에서 검사한다. 현재 문서가 그 실행을 완료했다는 뜻은 아니다.

1. **실제 실행의 스칼라 1개:** 등록된 절점 선택의 실제 값이 2mm인 고정 `NumericResultV1`과 그 `ToolResultV1`을 읽어 인용을 만든다. 2mm라는 한정된 주장은 같은 실행·프레임·선택의 원값, 단위, 마스크와 표시 구간이 모두 일치할 때만 검토할 수 있다. 3mm 주장, 다른 실행·프레임·연산자·선택·단위의 같은 2, 해시 변경, `mask=false`, 잘림으로 감춘 값, 미상 단위와 일부 범위의 전역 최댓값 주장은 거부하거나 반박·미검토로 남긴다.
2. **고정 평가의 손실 1개:** 실제 평가의 `objective.eligible=true,objective.fit_loss=.25`를 호스트가 읽어 무차원 손실로 표시한다. 문헌 인용으로 위장하지 않는다. 다른 `content_revision/evaluation_id`, `eligible=false`, `fit_loss=null` 또는 수정된 원평가로 같은 인용을 재사용하지 못한다. 낮은 손실이라는 사실만으로 `adequacy_status`나 공학적 안전을 통과시키지 않는다.
3. **직접 불러온 수동 결과 요약 1개:** 등록 판독 프로필이 원파일에서 실제 추출한 스칼라와 원문 위치를 읽고 수동 근거로 인용한다. 가짜 실행을 만들지 않는다. 원문 숫자 변경, 대응하지 않는 추출 기록, 허용 밖 로그·모델 필드와 프로필이 없는 파일은 수치 인용을 만들지 못한다. 원문 첨부와 자료 한계를 설명하는 질문은 계속 허용한다.

### 9.3 출처 검토와 새 질문의 관련성 검토를 분리한다

문헌 원문·라이선스·해시·추출 품질을 검토한 기록은 출처 검토다. 그 문헌이 지금 질문의 조건을 만족하는지 판단한 기록은 질문별 관련성 검토다. 출처 검토 통과만으로 새 질문을 `supported`로 만들지 않는다. 3절의 개발 36개와 평가 60개 질문 및 정답 표기는 시험 전용이며 제품 실행 때 정답 사전이나 질문 허용 목록으로 읽지 않는다.

~~~python
assess_retrieval(query_plan, candidates, *, source_review_refs,
                 assessment_profile, budget, check_cancelled=None,
                 on_stage=None) -> RetrievalDecisionV1
~~~

`RetrievalDecisionV1`은 `{schema_version:1,assessment_id,query_plan_sha256,candidate_set_sha256,source_review_refs,assessment_profile_id,assessment_profile_sha256,status,requirements,candidate_checks,missing_evidence,limitations,assessment_trace_ref}`다. 모든 키는 필수이며 임의 키를 허용하지 않는다. `status=supported|insufficient|out_of_scope`다. 하위 정본은 `requirements[{requirement_id,original_span:[start,end],facet,expected,status:verified|unverified,reason}]`, `candidate_checks[{candidate_id,source_sha256,relation:relevant|irrelevant|uncertain,requirement_ids,quoted_spans:[{start,end}],reason}]`다. `facet`은 `topic|solver|edition|model|unit|measure|condition|negation|comparison` 중 하나이고 `expected`는 정확한 원문 문자열 또는 `null`이다. 원문 구간과 후보 구간은 각각 실제 원문 및 실제 후보 텍스트의 코드 포인트 범위다. 요구 ID는 호스트가 `QueryPlan`의 명시 분류와 원문 구간에서 발급한다. 추출하지 못한 의미를 빠뜨리지 않도록 원질문 전체 구간의 `topic` 요구도 반드시 포함한다. 모델이 최종 `status`, 호스트 ID, 원본 해시 또는 출처 승인 기록을 만들어 넣지 못하게 한다.

관련성 판정자가 반환하는 엄격한 형식은 `{type:'retrieval_assessment',checks:[{requirement_id,candidate_ids,relation:satisfied|missing|uncertain,quoted_spans:[{candidate_id,start,end}],reason}],out_of_scope:{is_non_cae,original_spans:[{start,end}],reason},limitations:[]}`다. 모든 키는 필수이며 추가 키를 거부한다. 각 호스트 요구를 정확히 한 번 검사하고 알려지지 않은 ID와 원문 밖 구간을 거부한다. `satisfied`에는 실제 후보와 유효한 원문 구간이 필요하다. `is_non_cae=true`는 원질문의 명백한 비CAE 의도와 원문 구간으로 검토하며 모델명 미지정·검색 실패로 대체하지 않는다. 후보별 판단과 요구 충족 결과를 호스트가 합쳐 위 정본에 저장한다. 구문 해석이나 의미 판단을 확인할 수 없으면 완료된 관련성 검토로 승격하지 않는다.

실행 흐름은 다음과 같다.

1. 원문을 보존한 `QueryPlan`으로 BM25·해시 혼합 검색 후보를 얻는다. 출처 검토, 가용성, 추출 품질과 명시한 솔버·판본 조건을 검사한다. 어휘 점수는 후보 정렬과 진단에만 쓴다.
2. 등록된 질문 관련성 검토 프로필이 있으면 실제 질문과 후보 원문을 읽는 별도 엄격한 판정 요청을 실행한다. 판정자는 조건과 부정·비교를 보존한 요구 및 후보별 이유·원문 구간만 반환한다. 호스트가 이를 원문과 대조하고 최종 `status`를 계산한다. 이 판정은 답변 주장을 검토하는 `AnswerReviewv1`과 구분하며 새 주장 검토 통과로 세지 않는다.
3. 출처가 검토되었고 요청별 검토가 실제 완료되었으며, 필수 요구가 모두 확인된 관련 후보가 있을 때만 `supported`다. 명시 조건 불일치, 확인 못 한 요구, 불확실한 관련성, 출처나 구간 누락, 오류·취소·시간 초과는 `insufficient`와 구체적 `missing_evidence`로 남긴다. 명백히 CAE와 무관한 원문 의도가 확인된 경우만 `out_of_scope`다. 알 수 없는 모델, 언어 또는 자료 없음은 범위 밖의 근거가 아니다.
4. 관련성 검토 프로필이 없거나 추가 요청을 설정하지 않은 기본 경로는 `insufficient`로 두고 후보와 미검토 상태를 구분해 제공한다. 질문은 계속할 수 있지만 문헌으로 충분히 뒷받침되었다고 표시하지 않는다. 원문 읽기와 부족 사유를 바탕으로 한 일반 자문은 `model_general_unverified` 한계를 유지한다.

질문별 관련성 검토는 추가 공급자 호출이다. 등록 프로필을 명시적으로 선택·설정한 경우에만 실행하고 화면의 단계 및 실제 요청 수, 공급자 사용량과 추적 기록에 포함한다. 별도 확인 클릭은 추가하지 않는다. 기존 `cae-read-v1`의 4회 반복, 공급자 합계 60초, 전체 85초 안에 이 호출도 포함하며 별도 숨은 시간이나 재시도 예산을 만들지 않는다. 예산이 부족하면 미검토 `insufficient`로 끝내고 호출 수를 꾸미지 않는다. 기본 자동 답변 작성+독립 검토 이중 호출은 계속 금지한다. 사용자가 그 두 요청을 명시한 경우의 170초·120초·12회 한도도 관련성 검토를 바깥에서 더할 수 있는 예산이 아니다.

프로필은 판정 공급자·모델, 프롬프트·스키마·정책 해시와 실제 요청·응답을 고정한다. 설정만 있고 실제 판정이 없으면 완료로 기록하지 않는다. 역할상 문헌을 읽을 수 없는 요청에는 검색이나 이 판정을 자동 실행하지 않는다. 평가에는 기존 질문의 단순 재조회뿐 아니라 처음 보는 표현, 명시 부정, 다른 판본, 겉보기 어휘만 같은 무관 후보, 일부 요구만 만족하는 후보와 미지원 언어를 포함한다. 평가 결과를 본 뒤 프로필을 바꾸면 새 프로필과 독립 평가 세트를 사용한다. 실제 의미 판정의 오류 가능성은 별도 독립 평가와 답변 검토로 확인하며 기계적 구간 일치만으로 정답을 선언하지 않는다.


## 10. 확정한 실행 순서와 관련성 판정 설정 전달

D01의 프레임·첨부 검증 상태를 고친 뒤 D08의 v2 이론 질문/저장, D09의 오프라인 원문·한영 질의·색인을 연결한다. D09는 공급자 없이 수행할 수 있다. 실제 질문 관련성 판정은 D20의 공급자·도구 요청/반환·공유 예산·trace가 준비된 뒤 연결한다. D21에서 문헌 citation/resolver·사용자에게 실제 제시한 내용(exact-shown EvidencePack)·독립 reviewer/event 저장을 먼저 구현해 이론 답변을 완전히 인수한다. 수치 인용은 해당 D11 판독기, calibration은 고정 평가와 보류 노출 규칙을 조건부 선행으로 사용한다. 전체 합동 인수 D36은 선택 RP 자동판의 완료를 기다리지 않는다.

관련성 판정 설정은 다음 경로를 하나의 계약으로 구현한다. 아래 필드·인자는 신규이며 현재 구현됐다는 뜻이 아니다.

- Qt 전문가 연결 설정 옆에서 등록된 관련성 판정 프로필을 선택한다. 비밀/endpoint 자체가 아니라 `retrieval_assessment_profile_ref={profile_id,content_sha256}|null`만 저장한다.
- v2 대화의 `draft_context.retrieval_assessment_profile_ref`에 기록하고 기존 CAS/원자 저장을 재사용한다. v1 읽기·미설정은 null이다.
- `ask_expert_discussion(...,retrieval_assessment_profile_ref=...)`와 하위 `ask_discussion`에 같은 새 키워드 인자를 두고, 요청 시작의 `RequestContext`에 고정한다. 프로필 변경은 다음 요청부터 반영하며 진행 중·과거 turn의 ref/trace를 바꾸지 않는다.
- 호스트가 등록 descriptor와 내용 해시·허용 역할을 검사한 뒤 `assess_retrieval(...,assessment_profile=...)`에 전달한다. 인증정보·endpoint·실행환경의 비밀은 대화문서에 저장하지 않는다.
- 재열기 때 descriptor가 없거나 해시가 다르면 자동 대체·자동 호출하지 않는다. 해당 판정은 `insufficient`와 구체적 이유로 남기되 일반 질문은 가능하다. 등록·설정만으로 supported라 하지 않는다.
- 관련성 판정은 별도 요청·trace이지만 기존 `cae-read-v1` 4round·공급자 합계60초·전체85초 안의 공유 예산과 실제 요청수에 포함한다. 추가 예산을 만들지 않는다. 기본 자동 답변+자동 독립심의 이중 호출은 기존 금지를 유지하며 사용자의 명시적 검토 요청을 사용한다.

인수는 미설정·선택·프로필 변경/해시 불일치·재열기·공급자 실패/취소·실제 문헌 질문을 포함한다. 실제 관련성 판단·문헌 답변·독립 심의·반론/수정/재심·저장재열기를 각각 남긴다. 수치 질문은 원hash/locator/정확 표시값을 별도 확인하며 과거 일반 답변을 새 도구 인수로 쓰지 않는다.
