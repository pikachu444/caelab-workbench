# 병합 요청 — 전문가 토론 패널 (004 → 통합 채팅)

소유 범위 안에서 끝낸 것: 토론 모듈 `caelab/assist/`(experts·knowledge),
패널 `apps/workbench_qt/expert_panel.py`, 테스트
`tests/test_assist_experts.py`·`tests/test_assist_knowledge.py`·
`tests/test_workbench_expert_panel.py`. 공유·GUI 메인 파일은 직접
고치지 않았습니다.

## 이미 반영된 것 (통합 채팅 확인용)

작업 중 확인하니 아래가 이미 들어와 있었고, 이후 커밋 fcac805
(전문가 토론 패널 구현)와 f5b28ae(테스트 충돌 수정)로 반영됐습니다.
이 항목들은 제 요청이 아니라 현 상태 확인 결과입니다.

- `main_window.py`: `ExpertPanel` 오른쪽 도킹 장착, `get_model`/
  `get_result`/`_apply_proposal`/`run_solve` 연결, "전문가 패널 열기"
  메뉴, `discuss_selection`으로 선택 경로를 패널 논의 대상으로 전달,
  실행 완료 시 `expert_panel.refresh_evidence()` 호출.
- `project_window.py`: 체인 실행과 상자 상태 연결.
- `tests/test_gui_shell.py`: 패널 근거·제안 적용 테스트 포함.

검증: 전체 테스트 61개 통과·7개 건너뜀(skip은 설치가 필요한 솔버
연기 테스트)으로 위 통합이 제 모듈·패널 변경과 함께 도는 것을
확인했습니다. 커밋 분담은 통합 채팅이 정해 주세요 — 저는 아래
제 파일만 로컬 커밋합니다.

## RAG 전환 (2026-10-21 추가)

- 지식 층이 손글씨 노트 키워드 검색에서 RAG로 바뀌었습니다:
  `caelab/assist/rag.py`(조각 나누기·제목 경로·BM25+벡터 RRF·
  재정렬 후보 20개) + `knowledge.py`는 색인 통로로 교체. 노트 7개는
  색인의 초기 자료로 그대로 편입됐고 인용 id도 유지됩니다.
- 새 자료 추가는 코드 변경 없이 환경변수만으로 됩니다:
  `CAELAB_KNOWLEDGE_SOURCES="자료id:/경로/파일.pdf;..."` 또는
  `CAELAB_KNOWLEDGE_DIR`. 임베딩·재정렬은 로컬 엔드포인트
  (`CAELAB_EMBED_*`, `CAELAB_RERANK_*`)만 쓰고, 미리 만든 색인은
  `CAELAB_RAG_INDEX_DIR`로 읽습니다. 유료 API 경로는 없습니다.
- 이 컴퓨터의 실측 자산 (저장소 밖, ~/workspace/tools):
  매뉴얼 원문 `tools/manuals/ccx_2.23.pdf`(835쪽), 모델
  `tools/llm-local/models/`(EmbeddingGemma-300M Q8 — 서버 CPU
  색인용, Qwen3-Embedding-0.6B Q8 — 1순위 권장 모델도 받아 둠),
  색인 `tools/knowledge-index/rag-v1/`.
- CURRENT_STATE에 넣을 때 "전문가 지식 RAG(하이브리드+재정렬)"를
  된 것에 추가해 주세요. 전환 전/후 Hit@5 수치(시험 질문 13개,
  EmbeddingGemma 색인 + Qwen3-Reranker 재정렬, 이 서버 실측):
  전환 전 노트 키워드 9/13(69.2%) → 하이브리드(RRF) 13/13(100%),
  재정렬까지 11/13(84.6%). 재정렬은 실행마다 7~11/13으로 흔들려
  이 구성에서는 하이브리드가 더 안정적이었습니다. 재현은
  `tests/rag_eval_run.py` + `tests/rag_eval_questions.json`,
  원자료는 ~/workspace/tools/knowledge-index/eval_result.txt.

## 솔버별 색인 분리 + 메뉴얼 확충 (2026-10-22 추가)

- 조각마다 담당 솔버가 붙고, 한 솔버의 검색은 다른 솔버 조각을
  순위에서 제외합니다 (rag.Chunk.solver, HybridIndex.search의
  solver 인자). 토론은 결과 요약에 적힌 솔버명으로 자동 판정합니다
  (knowledge.infer_solver — 단어 경계 기준이라 "dynamic" 같은
  일반 단어는 LS-DYNA로 오인하지 않음). 자료의 솔버는 id·파일명에서
  자동 판정되고 `solver/자료id:/경로` 형식으로 강제할 수 있습니다.
- `CAELAB_RAG_INDEX_DIR`가 솔버별 하위 폴더들의 부모를 가리키면
  전부 읽어 합칩니다: `tools/knowledge-index/by-solver/{calculix,
  lsdyna, radioss, code_aster}`. 구형 색인(솔버 표시 없음)은 폴더
  이름으로 솔버를 채웁니다.
- 받은 메뉴얼 (저장소 밖 tools/manuals, 전부 공식 공개 PDF):
  LS-DYNA 이론 메뉴얼(Ansys LST 공식 페이지, 2025-03판),
  Radioss 2022 이론·사용자·레퍼런스 가이드(Altair 공식 도움말 PDF —
  OpenRadioss용으로 상용 Radioss 메뉴얼 사용), Code_Aster는 문서가
  수천 개로 쪼개져 있어 핵심 문서(U1·U2·U3·U4 일부 + R3.03.01 등
  13개 PDF, 공식 문서 미러)부터. Abaqus 문서는 라이선스 계정 뒤에
  있어 공개 입수가 안 돼 제외했습니다 (파일 확보 시 같은 방식으로
  편입 가능).
- 솔버별 색인 구축은 이어받기 가능한 스크립트로 돌립니다:
  tools/knowledge-index/build_solver_indexes.py (조각 80개 단위
  저장, 중단 후 재실행하면 이어서). 구축 완료 수치와 솔버별 검색
  확인 결과는 제 보고를 봐 주세요.

## 남은 요청 (문서만)

- CURRENT_STATE.md: "확인 안 된 것"에서 "LLM 전문가 토론 흐름"을 빼고,
  "된 것"에 전문가 토론 모듈(공급자 추상화 — OpenScience 구독 경로·
  OpenAI 호환(로컬 LLM 포함), 템플릿 4종, CalculiX 매뉴얼 지식 팩,
  인용 검증, 맥락 유지)과 토론 패널을 넣어 주세요.
- 단, "실제 구독 실행 1회"(Windows OpenScience 경유)는 미확인으로
  남겨 주세요. 리눅스에서 실제로 돌린 것은 로컬 LLM(llama.cpp 서버 +
  Qwen2.5, OpenAI 호환 경로)이며 구독 실행과 구분해 적어 주세요.
- ARCHITECTURE.md: "아직 없는 것"에서 "전문가(LLM) 토론 패널"을
  빼 주세요.
- connections.py: 변경 요청 없음. 전문가 공급자는 환경변수
  (`CAELAB_EXPERT_PROVIDER` = openscience | openai_compatible)로
  고르고, 기본값은 "없음"이라 유료 API가 임의로 붙지 않습니다.
