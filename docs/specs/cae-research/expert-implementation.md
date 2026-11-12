# 전문가 자문·RAG·읽기 도구·답변 심의 구현 명세 C — 재구성본 2026-11-12

이 문서는 환경 소실 뒤 현재 대화에 남은 구현 계약을 새로 작성한 복구본이다. 과거 parts/expert_implementation.md의 b06a6cdc5c7001617cd85f4d1b68e57de6dba63effb7b25d062146a7cca01c62와 같은 원문/해시라고 주장하지 않는다. 원문 corpus/실행 로그/fixture가 현재 복구되지 않았다면 과거 관측 이력과 현재 확보 자산을 구분한다. 새 blob을 E/F가 다시 읽고 승인해야 한다. 제품 runtime·실공급자·Windows 완료 문서가 아니다.

## 1. 사용자 작업과 source 재사용

DP/Xia는 예시다. 일반 구조/연속체 이론·FE 모델링·요소/구속/접촉/수렴·실행 실패·재료 선택/시험 설계·결과 해석·혼합 질문을 받는다. corpus에 모델명이 없다는 이유로 질문/원덱 실행을 차단하지 않는다. 이론은 run 없이 시작하고 mixed는 case_analysis+실제 첨부+corpus다. Prep은 native 편집/Apply/Undo, Workbench는 파일/연구/실행/결과와 독립 전문가 연결을 소유한다. 열기로 LLM/검색/솔버 호출/모델 변경을 시작하지 않는다. 유료 API/새 DB/중복 JobManager/웹 UI/공통 FE 스키마를 추가하지 않는다.

기준 WB104cdd6888a5554eecc8cf965c60ff1bd980b6da, Prep555af5b3ab2c6e7dc276111609302ef565bb4fc8, RPcec23cbdd4058237e04fdcc7ecb06a3834578cae. 아래 source 관측은 환경 소실 전 실제 읽은 코드에 근거한다. 새 구현 API와 기존 API를 구분한다.

| 실제 경로/심볼 | 재사용/변경 |
|---|---|
| caelab/assist/experts.py: ExpertProvider.discuss, OpenAICompatibleProvider.discuss, OpenScienceProvider, Discussion.build_prompt/ask | 기존 문자열 discuss 유지. compatible은 현재 messages 단발이고 새 provider_steps adapter가 tool 왕복을 구현. allowed_tools는 자료 종류이므로 allowed_evidence_sources alias와 새 allowed_query_tools 분리 |
| assist/openscience.py: SubscriptionTransport._installation/discuss/_checkpoint/_stop | Windows runtime2.0.146/profile marker/hash, deny permission/tools=false/mcp/plugin 비활성·isolated request/소유 child cleanup을 유지. structured_action은 host JSON loop이며 provider native tools 활성화가 아님 |
| assist/discussions.py: create_discussion/load_discussion/save_discussion/attach_evidence/ask_discussion/review_proposal, _lock/_current/_interrupt/_write | FileLock/CAS/atomic_json/request lease·중단 보존 재사용. v2 nullable theory evidence·context/trace/review/events 추가. 기존 check/reject는 파일제안 검토이며 답변 심의와 다름 |
| assist/rag.py: Chunk/parse_markdown/parse_pdf/tokenize/BM25/HashingEmbedder/HybridIndex/save_index/load_index; knowledge.py: search_knowledge_result/verify_citations | tokenizer/BM25/RRF/선택 endpoint 재사용. hashing512D는 semantic model이 아님. manifest solver/edition provenance를 filename 추론보다 우선. citation ID 존재와 claim support 분리 |
| native/evidence.py: capture_run_evidence/verify_evidence; Workbench.read_native_frame; native.frame_view.read_native_frame | bytes/hash/frame 검증과 같은 공통 ArtifactRefV1/ResultRefV1/owner resolver 소비. FRD 전용 _finish에 모든 결과 reader를 삽입하지 않음 |
| apps/workbench_qt/expert_window.py: ExpertWindow/capture_run/ask/_start/_poll/cancel_request/render; native_analysis.py: request_expert_debate | 독립 창/worker/mailbox/asset 검증 재사용. constructor exact requested frame 전달·초안 보존·취소/late/CAS·review 표시 추가 |

신규 assist/{contracts,query_language,corpus,query_tools,provider_steps,answer_review}.py와 scripts/build_knowledge_index.py를 한 assist 담당이 작성한다. experts/discussions는 단일 편집자. Qt 변경도 단일 GUI 편집자. workbench/artifact_refs/native 공통파일은 root가 소유자를 정한다. core는 Qt/apps import 금지. 기존 JobManager를 우회한 subprocess/solver submit tool 금지.

## 2. API와 문서 v2

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

discussion v2는 기존 v1 id/revision/title/evidences/turns/constraints/failures/proposals/selected_evidence_id/draft_question/pending_request를 보존하고 question_scope(theory|case_analysis),review_requests,review_resolution_events,corpus_snapshot_ref,tool_policy_id,draft_context,draft_history를 추가한다. theory는 evidence=None; case_analysis는 유효 첨부 필수. 구형 호출은 case_analysis. scope 변경은 다음 질문부터이며 과거 turn을 수정하지 않는다.

turn에는 schema_version2/question_scope/responds_to_review_ids/request_context_ref/provider_capabilities/tool_trace_ref/retrieval_decisions/claims/answer_review_ids/evidence_pack_hash/corpus_revision/budget_used/capability_limits를 추가. no-run theory에 가짜 run/frame/단위를 만들지 않는다.

evidence union은 기존 metadata/text/files를 보존한 native_run/manual/calibration. derived native는 derived_result_ref=공통 ResultRefV1과 source_run_id/run_id 일치. calibration은 실제 CalibrationEvaluationArtifact를 consumer owner에서 읽고 시험별 actual/predicted/loss/coverage/split/holdout·parameters/measure/unit을 전달한다. 원시자료 없는 항목을 빈 텍스트+availability로 표현하고 존재한 척 채우지 않는다.

공용 evidence wire는 최신 core C1(b3ec806dd6d5e52c5062c75fc98b1e829b2c08ac)의 EvidenceEnvelopeV1 한 정본만 따른다: {schema_version:1,id,kind,content_sha256,provenance,availability,payload}, kind=native_run|manual|calibration|document_chunk. native ResultRef는 native_run payload.derived_result_ref이며 kind=result를 만들지 않는다. provenance.artifact_owners는 core의 정확 위치/형식이며 source_kind는 native_run|manual|calibration|official_source|paper|solver_manual로 검증한다. CorpusManifest official_manual은 envelope provenance에서 solver_manual로, reviewed_note/user_document는 manual로 명시 mapping한다. document_chunk는 exact source_ref/chunk_ref·문서/텍스트 hash·실 line/codepoint span을 가진다. availability와 kind별 payload strict union/전체 envelope hash 규칙은 core C1을 복제하지 않고 그대로 소비한다. 앞 문단의 기존 metadata/text/files 보존은 legacy descriptor exactbytes read adapter를 뜻하며 새 envelope payload에 임의 필드를 삽입하는 별도 wire가 아니다. legacy descriptor hash와 wrapper hash는 별개 identity다.

root C6/C7를 적용한다. material document_revision은 mutable manifest/CAS, content_revision은 model/parameters/data/comparisons/policies/numerical recipes/optimizer contract의 불변 identity다. calibration.describe는 둘을 구분하고 등록 optimizer_responses/mandatory optimizer_constraints/differentiable/constraint_status(FEASIBLE|MARGINAL|INFEASIBLE|NOT_ASSESSED)/raw checks를 보존한다. 유효 고오차는 finite fit_loss와 fit_status=fail로 보존하며 constraint_status는 독립이다. 유한한 물리 제약 위반만 finite loss와 INFEASIBLE이다. required constraint scalar 계산실패/비유한은 objective={eligible:false,fit_loss:null,reason_codes:[CONSTRAINT_RESPONSE_INVALID]}, constraint_status=NOT_ASSESSED로 기록한다. holdout은 train objective를 지우지 않는다.

root C3: DatasetManifest.dataset_kind=field|history. history는 frames=[], series[{series_id,coordinate_ref,row_count,source_row_index_ref,field_ids}], 실제 source time 배열. ResultRef.frame_id는 history의 registered series_id. 첫 history capability는 curve_extract, geometry_state=unknown. 초기 point query와 RP field consumer는 history를 거부한다. animation ordinal을 time으로 채우지 않는다.

RequestContext는 원문 질문/scope/제약/초안근거와 실제 첨부의 구별, frozen evidence hashes, corpus revision, QueryPlan, 실제 shown chunks/text hashes, tool schema/alias/policy SHA, solver/edition/model/measure 한계를 canonical JSON SHA로 고정. proposer 종료에 EvidencePack freeze. request_context_hash는 질문/scope/제약/선택·정책·근거지문을 담은 RequestContext의 canonical hash이고 evidence_pack_hash는 frozen 근거 pack bytes의 canonical hash다. 서로 대체하지 않는다. 재심의 source turn에는 정확 reply/reply_sha256와 두 hash를 각각 보존하고 reviewer claim spans/coverage는 그 source turn의 실제 reply에서 다시 생성한다. 원 resolution event/original_context_hash link만 원 review context를 계속 가리킨다. 원자료 변조는 integrity_error; 현재 모델 revision이 달라 stale인 historical snapshot은 역사자료로 읽되 현재 설계 주장으로 승격하지 않는다. 단위/부호/측도 unknown은 추정하지 않는다. 동일 ArtifactRef/ResultRef/owner 정본을 사용하고 path/root를 LLM 인자로 받지 않는다.

## 3. 실물 corpus와 한영 검색

환경 소실 전 실제 준비한 corpus는 primary source8개와 source span16개였다. manifest/chunks/evaluation SHA256은 각각 0a978ff71489001727236ad06de0c31bd9a9a28c5b3b2e731a1ddb888317c2ac / ecbf2fbb02864ef3ef5fbdcbfb390a70436e84a9d1b83c44816db6fc0a25aea4 / 7e32083012e320b3872991068510eadf93bc824f2e82e22403059636197b35e9. F가 이 exact hashes의 raw/line/char span과60개 평가 label을 독립 읽은 관측 이력이 있다. 현재 원파일을 복구하지 못하면 그 receipt를 새 corpus의 승인으로 재사용하지 않는다. 새 blob/재획득 bytes는 새 manifest와 재검토가 필요하다.

선정 원문:
- FEniCS/dolfinx b8375fa9fb932cbfce43005eeb4a3d52293f428b(v0.10.0): python/demo/demo_poisson.py SHA25638d6c205871b94e6d1d91d66d8014357e7631065c082e054fabfba9f3984af96, demo_elasticity.py SHA256607789cc3b74d49d575c5a9266fae46a19c06fad6528e1ceeaeadc02e8677363, cpp/demo/hyperelasticity/hyperelasticity.py SHA256061f807c7cd1cde03f580645860649a6ea3025555946ec1faae1320c5302a104. LGPL-3.0-or-later; COPYING/COPYING.LESSER/README notice 보존.
- GEOS-DEV/GEOS d81a4438f107446f17e33ab70c70358935e473ab: src/coreComponents/constitutive/solid/DruckerPrager.hpp gitblob02e2a44fcf7ad2c53541533dcc126d9f53961cb6, DruckerPrager.cpp gitbloba64ae00030cbd75a322b30ae66591d1c0521c9a9. LGPL-2.1-only; LICENSE/COPYRIGHT/CONTRIBUTORS/NOTICE/ACKNOWLEDGEMENTS 보존. 여기서 git blob SHA1은 file SHA256이 아니다.
- NatLabRockies/pyvisco b61b5a0c6a2939927c87189972ea5619503bd813: pyvisco/prony.py SHA256641490da54f1d62d5f631c899f3f0ade44d1f9debc98a9e9068fe4f0577795bb; shift.py SHA25693f143f9789e31aaffebc229fe76b0c3b37ffa0b4937d5b2980b1d524e2503a2. BSD-3-Clause+LICENSE.
- Argonne-National-Laboratory/neml65ab2a648a975e2e9de8e1371be270567f9683ac: src/hardening.cxx SHA2561575188d63689f68a5b0ac0a28006b582e1a92e7b3d14c56108e73f40892318f. MIT+LICENSE.

고정 원문 URL recipe는 https://raw.githubusercontent.com/<repository>/<commit>/<path>. source code를 manual/paper로 위장하지 않고 root C5 enum official_source로 표시한다. 이 corpus는 OpenCourant 카드/접촉/foam/손상/Xia원형/안전인증 전체 coverage가 아니다. 공개 source 원문/고지의 검토용 동봉과 product kernel 편입을 구분한다. 제한/unknown 문헌은 사용자의 로컬 source root에 두고 URL/판본/hash/입수 recipe만 추적. Ask가 자동 구매/다운로드하지 않는다.

Manifest fields: schema_version/corpus_id/revision/sources; 각 source_id/source_kind/title/edition/commit/uri/local_relative_path/sha256/byte_length/license{id,source_uri,redistribution}/solver_scope/parser{id,version}/extraction_quality. Chunk는 source_id/chunk_id/document_sha256/text/text_sha256/edition/section_path/line_start/end/char_start/end/page_start/end/coverage_facets. source 파일의 page는 null이고 실제 1-based inclusive line을 사용한다. PDF는 pypdf의 실제 page extraction/outline과 원 page render를 대조. fraction/minus/exponent/table 손상은 partial+missing section; 조용히 식을 고치지 않고 별도 reviewed_note+원 span을 만든다.

source span recipe(재획득 후 재검사 필수):
poisson lines23..67, implementation98..164; elasticity modes54..97/law118..156/solve164..226/VM227..257; hyper kinematics28..58/energy60..85/stress84..92; GEOS hpp177..236/cpp77..122; prony relaxation317..344/frequency465..488; WLF13..40; NEML state657..670/evolution687..711.

고정 무료 backend cae-small-bm25-hash-v1: BM25 k1=1.5,b=.75, hashing512 float32, RRF k60, LexicalReranker. original/projected query 각 top20 union→RRF→top5. hashing은 semantic encoder가 아니며 이 작은 corpus의 Hit@5를 일반품질로 확대하지 않는다. solver/edition/measure가 explicit인데 source facet unknown이면 충분근거 아님. general 이론은 특정 solver default의 근거가 아니다. 벡터 dim/dtype/finite/embedder/manifest mismatch는 error; 몰래 다른 모델 fallback0. 선택 local embedding은 새profile/독립평가.

QueryPlan 정본: original_text/original_language,queries[{text,language,method,resource_revision}],policy_revision,term_spans,unmatched_spans,removed_spans,warnings. NFKC는 검색variant만, 원문보존. reviewed bilingual longest phrase를 원문span 옆에 병기하여 variant추가; 삭제0. 숫자/단위/solver/card/model/edition·not/no/without/않/아닌/없·조건/비교를잠근다. removed_spans=[]이고 unmatched도원문유지. 미검토언어/주요phrase는 LANGUAGE_COVERAGE_UNVERIFIED, 안전parse불가는 QUERY_LANGUAGE_UNSUPPORTED warning; 자료없음과 구분. 원문질문은provider에게항상전달.

lexical coverage/rank는진단이며 후보삭제/out_of_scope/claim지원 단독gate금지. RetrievalDecision.status supported는 reviewed 관련후보제공일뿐 claim pass가아님. 요구facet/실제추출이충족되지않으면 insufficient와missing_evidence. wrongsolver/edition·source missing·no candidates·requirements unverified를구분. 미지모델은out_of_scope가아님. 명시비CAE scope만 out_of_scope. 질문은계속허용하며 model_general_unverified 설명은인용/검토pass로승격하지않음.

색인 build CLI는 --manifest/--output/--review-overlay와 --verify-only|--build. source/고지/hash·승인된review receipt/targethash검사→같은volume temp→허용source copies/review/chunks/vectors/manifest fsync/hash→새immutable indexes/<digest>rename→current.json tmp fsync+os.replace. nonempty dir replace금지. 동일digest는bytes일치만재사용. 실패/취소는pointer유지/자기temp만정리. 요청시revisionfreeze. legacy index는legacy_unknown,정밀출처합격제외.

평가 fixture recipe는 development36(covered12/insufficient12/nonCAE12), evaluation60(covered24=한영12engineering intents/insufficient18/nonCAE18). 원문질문/expectedchunks/의무/부족사유/주석자/sourcehash를함께저장. 새재구성 fixture는과거평가file과동일하다고말하지않는다. 12intent: Poisson Neumann weak-form sign; 3D six rigid modes; Lamé conversion; deviatoric VM; exact compressible neoHookean energy; PK1→Cauchy; GEOS trialQ+FtrialP-oldcohesion; independent friction/dilation; Prony equilibrium1-sum(alpha); omega rad/s vs Hz; NEML six-component histories; dev(s)+X sign mapping. 부족반례18: RadiossLAW36 interpolation/CalculiXtemperature default/Xiaoriginal/contactpenalty/uniqueness/safety/LAW10 equivalence/ccxRFsign/Nortonexponent/anisotropyvariant/temperatureE/viscousstate/restart/buckling/strainmeasure/allhex/neweditionAPI/unattachedrunvalue. 비CAE18은날씨·요리·가격·스포츠·번역·운전면허·식당·호텔·계좌·선거·주식·축사·메일·비밀번호·예약·이름·체스·영화요청. 별도언어반례는일본어/프랑스어/미대응한국어/부정조건/오판본을포함.

새fixture는독립source/한영label검토후동결한다. 목표(실측아님): coveredHit@5>=22/24, 부족정확사유>=17/18, 비CAE허위acceptedsource0, wrongsolver/판본 specific근거0. pair를독립60intent로과장하지않음. evaluation을보고조정하면새profile/새eval세트. live답변은12covered+6부족 uniqueintent의claim·quote/수치/단위/부호/측도를독립검토: 허위citation0/미첨부숫자발명0/부족pass0.

## 4. 읽기 도구/transport

ToolCallV1={call_id,tool_name,arguments}. ToolResultV1={call_id,status:ok|partial|error,data,error:{code,message,retryable}|null,evidence_refs,provenance:{request_context_hash,corpus_revision,source_hashes,run_id,frame_index,units,basis,measure},limits:{truncated,valid_count,total_count},elapsed_ms}. 모든JSON object additionalProperties:false, required키완전, duplicate키/NaN/Inf/bool-as-int거부, action32KiB(UTF-8 bytes, JS string length 아님). JSON whitespace는 RFC의 space/tab/CR/LF만 허용한다. parser object는 null prototype이며 허용 ID/segment/citation membership는 Object.hasOwn으로만 검사한다. __proto__/constructor/toString 같은 inherited key가 strict schema/ID 목록을 우회하지 못한다. callID1..128chars/request내unique.

| canonical↔nativealias | exact required args |
|---|---|
| knowledge.search↔knowledge_search | query1..2000chars,solver_scope=context등록ID,limit int1..5 |
| evidence.describe↔evidence_describe | evidence_id=context등록ID |
| evidence.query_field↔evidence_query_field | evidence_id/field_id/component_id/selection_id=context등록ID,operator values|min|max|mean|norm,operator_version='1',limit int1..1000 |

출력은frozen chunks/manifest 또는실제근거metadata/동일ResultRef. arbitraryshell/Python/SQL/HTTP/path/자유식/solver-submit/모델변경없음. frame은evidence고정,args추가거부. values→ResultRef.operator identity v1; min/max/mean/norm은허용operator만교체한requestref를만들되baseRef/source/dataset/frame/selection불변. base/requestrefhash둘다trace. 첫profile은verifiedVTU node scalar/component. mean은유한절점산술평균,공간평균아님. values는IDs/null/validity/count. norm은동일declaredunit/knownorthonormalbasis/exact3vector/component='vector'의절점별Euclidean;tensor/unknownbasis/mixedunit은unsupported. nonfinite를0으로채우지않음. unknownunitraw조회는진단뿐fit/물리주장pass불가. cell/IP/layer/history는검증profile등록전unsupported. Dsynthetic5MPaResultRef도describe에그대로보이나초기pointquery는거부,fit=false유지. RP/ParaView창열림은numericquery성공아님.

ProviderCapabilities required provider_id/model_id/transport_mode(native_tools|structured_action|prompt_only)/supported_tool_schema_version/max_context(실확인token또는null)/configured/probe_status(not_run|passed|failed|unsupported)/last_probe_at. endpoint/model/transport/schemahash변경시probe무효. 명시연결확인에서syntheticID의무해evidence_describe를tool로요청→결과→strictfinal의2POST를실관측해야nativeprobe pass. 설정존재≠지원확인.

ChatToolsAdapter.step(messages,*,tools,deadline,cancel)->ProviderStep는설정endpoint/chat/completions에{model,messages,tools,tool_choice:'auto',stream:false}. 명시auth만전송. assistant.content=null+tool_calls정상. 원assistantmessageappend→alias/schema검증→host순차dispatch→같은tool_call_id의roletool/contentToolResultJSONappend→같은messages다음POST. unknownalias/invalidargs는boundederror,서버protocol손상은terminal. 400/404/422가tools미지원명시하면unsupported_capability/실행0/묵시messages-onlyfallback0.401/403unavailable,429rate_limited,5xxprovider_error,timeouttimedout;자동networkretry0.

OpenScience structured_action은기존deny/toolsfalse를유지하고각isolateddiscuss에원질문/frozencontext/schema/이전toolresults를구분된data로재전달. 전체응답은exact{type:'tool_request',call_id,tool,arguments}또는strictfinal; fence/prose/extra/duplicate거부. JSONhostloop이며nativefunctioncall이라고표시하지않음. 실제구독probe전unavailable. prompt_only일반자문은계속가능,tools사용으로승격0. 권한/profile자동수정/유료API선행0.

## 5. strict final·전답변 coverage·reviewer wire

native와structured의final은동일{type:'final',answer:str1..20000,claims:[{text_span:[start,end],citation_ids:[shownIDs]}]} strictJSON. response_format지원은가정하지않고contentJSON을검사. claimhint에hostID/context/actor추가거부. hint범위/실shownID검사; 빈/부분hint는허용하되not_assessed이며coverage정본이아님. nativeplain/fence/unknownfield는invalid_provider_action;legacy prompt_only문자열답변은not_assessed로유지하고명시review에동일coverage적용.

host는blank-line delimiter \n[ \t]*\n로paragraph를나누고양끝whitespace제외[codepointstart,end)inventory를생성한다. answer모든비공백은정확1segment에속해야함. segmentUUID5(requestID,start,end,canonicalanswerSHA). code/table도포함. providerhint가segment를삭제하지못함. 초기ClaimV1={hostUUID,text_span,citation_ids,basis:evidence|model_general_unverified},지원미검토. paragraph내여러명제는reviewer가nonoverlap parts로전부평가한다.

reviewerexactwire:
{type:'answer_review',
 segments:[{segment_id,parts:[{start,end,kind:claim|nonclaim,citation_ids,
 support:supported|contradicted|not_assessed|not_applicable,reason,
 quoted_spans:[{citation_id,start,end}]}]}],
 modeling_checks:[{category:assumptions|units|measures|BC|mesh|contact|convergence|interpretation,
 result:satisfied|issue|not_assessed|not_applicable,reason}],
 items:[{segment_id,part_index,category,severity:minor|major,reason,evidence_refs,required_action}],
 required_evidence:[str],limitations:[str]}.

모델은reviewID/sourceTurn/context/actor/verdict/status/시간을보내지못함;extraidentitykey는invalid_provider_action. host가실제frozenrequest의proposer/reviewer/requestIDs/sourceTurn/context/independence/reassessment/시작·완료시각과결과를합성하여완전한AnswerReviewv1저장. claim/itemIDs도hostUUID. claim_checks는text_span/citationIDs/id_valid/quoted_span_valid/support/reason. items→ReviewItem.item_id/claim_id/category/severity/reason/evidence_refs/required_action.

검사: segmentexactonce/unknown0/part해당범위/overlap0/모든비공백coverage,showncitation/실sourcequote범위,중복없는정확8category. supportedclaim에실citation/quote없으면not_assessed. nonclaim은not_applicable+이유이며순수인사/정형heading exact whitelist만허용(한영Summary/Sources/Limitations/Assumptions/Review/Hello/Thanks와요약/참고자료/한계/가정/검토결과/감사합니다/안녕하세요). 수치/기술명제를nonclaim으로숨기면insufficient.

verdict정본: coverage누락/빈review/claim0/미검토/필수category누락/required_evidence/불허nonclaim이면insufficient;그외contradicted/modelingissue/majoritem이면revise;나머지모든claimsupported와필수check충족또는이유있는not_applicable일때만pass. parse/unknowncitation/forgedidentity/overlap은failed+insufficient/invalid_provider_action,원반론보존. coverage는{answer_sha256,nonwhitespace_count,covered_nonwhitespace_count,segment_count,checked_segment_count,claim_count,complete,nonclaim_policy_id:'cae-nonclaim-v1'}를immutable review에저장한다. 기계coverage는의미정답/인간인증이아님.

AnswerReviewv1필수schema_version/review_id/source_turn_id/request_context_hash/proposer/reviewer/independence/verdict/status/claim_checks/modeling_checks/disagreements/required_evidence/limitations/reassessment_of/coverage/created_at/finished_at. 별도multi-role요청은자동다수결pass없음. 같은모델별도요청은separate_request_same_model,다른모델은separate_model,인간승인과구분. reviewer는동일frozenpack·기존허용deterministicread만;새문헌/frame필요시insufficient/newturn/newreview.

## 6. 반론·resolution·저장

이견답하기→사용자draft보존+responds_to_review_ids→새turn→재검토는ReassessmentLinkV1세필드배열. newpackreview가olditem을해소하려면exactoldreview/item/originalhashlink+실완료reviewer가필수. tuple2alias0. oldverdict/pack/event불변.

record_review_resolution의event정본:
{schema_version:1,event_id,review_id,item_id,responding_turn_id,decision:keep|resolve|withdraw,
 reason,actor:{kind:user|reviewer,reference_id},request_context_hash,created_at}.
eventcontext는원reviewhash. newreviewcontext는actor.reference_id의실completedrequest와reassessmentlink로검증. proposer답변만self-close불가. actor는실제UIuseraction또는완료reviewerrequest. kindreviewer로proposerrequest를가리켜도거부. status는이벤트계산view(unresolved/resolved/withdrawn),별도mutable정본0. 후속keep는unresolved로재개.

FileLock→expected_revisionCAS→같은discussionreview/item/turn→responds_to_review_ids→원claim/evidence/contexthash→실actor→newreviewsourcecontext/reassessment→append→atomic_json. missing/wrongoriginallink는REVIEW_CONTEXT_MISMATCH/details.reason=EVIDENCE_SCOPE_MISMATCH/event0. 공개reason정본UNKNOWN_REVIEW/UNKNOWN_ITEM/UNKNOWN_TURN/CROSS_DISCUSSION/EVIDENCE_SCOPE_MISMATCH/REFERENCE_HASH_MISMATCH. sourcebyte손상integrity_error,CASDiscussionConflict,leaseDiscussionBusy와구분. check_id/response_turn_id/새reasonenum0.

예산cae-read-v1: prep20s/provider합60s/overall85s,round4/calls6/round3순차,prompt60000chars/resulttext20000/values1000/action32KiB. 남은call보다round요청많으면round전체budget_exhausted/부분실행0. monotonicremaining을HTTP/CLI/embedding/readcheckpoint에전달. 명시automaticproposer+reviewer는UI2요청/총170s·120sprovider·12calls,기본자동이중호출0.

cancel→flag/provider.cancel→새round/tool0→가능한ownedcleanup→cancelled. 늦은blockingresponse가끝날수있으므로cleanup상태와remote중단추정구분. request/documentID/revision/cancelgeneration일치때만commit;late는ignored_late/audit,승격0. trace는assets/requests/<id>/events/<seq>.json exclusivecreate/fsync+previoushash,종료trace.json은immutable orderedrefs/counts/statusmanifest+ArtifactRef. auth/token/privateprofiledescriptor저장0;실usage만기록.

v1read는메모리v2view/oldscopecase/citationIDcheck만/claimnot_assessed,자동write/LLM0. 첫사용자save는FileLock→원id/revision/hash→assets/migrations/<v1sha>.json xb/fsync(기존exact확인)→CAS재확인→atomic_jsonv2. 백업+본문전체transaction인척금지;본문실패시v1/백업보존. future>2/손상/invalidpath/nonfinite거부·덮어쓰기0. pendinglease없으면in-memoryinterrupted/draft·partialtrace·반론보존/자동재호출0. SaveAsactive는busy;참조assets/허용raw/chunks/context/trace/reviewclosure를복사하고resolverroot만이동. 외부원문미동봉은availability/hash/URL보존,재review는source_unavailable.

## 7. Qt/수락과현재경계

ExpertWindow독립창/transcript중심/하단input. 내부ID/JSONdump기본화면0;문서명/section/page/line와사람이읽는자료한계표시. constructor기존kwargs뒤requested_frame_index:int|None/frame_context_state추가. nativebridge는0도isnotNone으로전달. capturedefault는exactframe;사라졌으면placeholder+직접선택,결과전무는입력·로그option을기존OK로선택/frameNone/가짜숫자0. 다른run/frame선택은초안기준/실첨부를구분표시,userdraft보존. generated만업데이트+history. 추가승인checkbox/문장판정차단0. Ask/Cancel/review/이견답변/재검토/keep·resolve·withdraw/저장재열기를같은서비스명령에연결. busyworker/새창/닫힌창late결과격리. GUIthread에서network/PDF/fieldread0.

제안수용→Prep/전용editor사용자Apply revision검사→기존JobManager새run→새capture/turn/review. oldpass를newrun에복사0. 미지원변경은전용앱안내+미적용.

실행/수락분리:
E0/E1 exactframe/theory/v2backup/CAS/open무호출;
E2a realraw/license/span/한영label독립검토;
E2b frozen36dev/60eval·samecorpusBM25/hybrid;
E3 strictschemas/같은ResultRef/native2POST/structuredaction·미지원/cancel/late/budget;
E4 fullanswerreview+반론/newpack/link/event/저장;
E5 Prepdocrevision일치/calibrationactual·predicted·split/constraints·원본변경0/새job0;
E6 실제공급자/Windows/사용자Apply새run후속심의.
환경소실전reference에서source8/span16/36dev60eval/hash검사,localhost2POSTtoolmock·strictfinal,15개claim/reviewer부정검사·partialhintcoverage,missing/wrongreassessmentevent0를PASS로관측했다. 이문장으로원로그를재생성하거나새blob의실행을주장하지않는다. 재구성본은새schema/golden/reference를실제만들고검토·실행가능환경에서재실행해야한다. 실제제품index/Qt/provideradapter/실LLM공학품질/실솔버끝단/Windows는미구현·미실시로남는다.
