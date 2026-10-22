# 지식 자산 출처 — 메뉴얼·모델 받는 곳과 색인 다시 만들기

전문가 토론 패널(`caelab/assist`)이 쓰는 메뉴얼 PDF와 임베딩 모델은 바이너리라 이 저장소에 넣지 않습니다 (원격 반영 수단이 바이너리를 올릴 수 없음). 대신 이 문서에 **받은 곳을 정확히 적어 두니**, 어느 환경에서든 아래 주소로 다시 받아 색인까지 복원할 수 있습니다.

작업 사본 위치: 서버 `~/workspace/tools/` (manuals/, llm-local/models/, knowledge-index/)
백업 사본: 사용자 Google Drive 폴더 "CAE Lab 메뉴얼·색인" (메뉴얼·색인·구축 스크립트·평가 기록)

## 메뉴얼 (전부 공식 공개 PDF)

주소와 크기는 2026-10-22에 다시 확인했고, 크기가 작업 사본과 바이트 단위까지 일치합니다.

| 파일 | 크기(바이트) | 받은 곳 |
|---|---|---|
| ccx_2.23.pdf | 6,281,257 | http://www.dhondt.de/ccx_2.23.pdf (CalculiX 공식 배포처) |
| lsdyna-theory.pdf | 13,894,836 | https://lsdyna.ansys.com/wp-content/uploads/2026/08/DRAFT_Theory.pdf (Ansys LST, 2025-03판) |
| radioss-TheoryManual.pdf | 16,422,767 | https://2022.help.altair.com/2022/simulation/pdfs/radopen/AltairRadioss_2022_TheoryManual.pdf |
| radioss-UserGuide.pdf | 26,740,464 | https://2022.help.altair.com/2022/simulation/pdfs/radopen/AltairRadioss_2022_UserGuide.pdf |
| radioss-ReferenceGuide.pdf | 48,136,101 | https://2022.help.altair.com/2022/simulation/pdfs/radopen/AltairRadioss_2022_ReferenceGuide.pdf |

Code_Aster는 문서가 수천 개로 쪼개져 있어 공식 문서 미러 저장소를 복제해 핵심 13개만 씁니다.

- 저장소: https://gitlab.com/biba1632/code-aster-manuals.git (복제본 기준 커밋 `9ba747082747e8bb8a356bab616718350db2fbea`)
- 경로: `source/docs/PDF/user/` 의 u1.03.00·u1.03.01·u1.04.00·u1.05.00·u2.00.01·u3.01.00·u3.02.01·u3.11.01·u4.01.00·u4.91.01·u7.01.31 (11개), `source/docs/PDF/reference/` 의 r0.00.01·r3.03.01 (2개), 합계 3,950,165바이트

Abaqus 메뉴얼은 라이선스 계정 뒤에 있어 공개 입수가 안 됩니다. 파일을 확보하면 같은 표에 주소 대신 입수 경로를 적고 편입하면 됩니다.

## 임베딩·재정렬 모델 (GGUF, HuggingFace)

| 파일 | 크기(바이트) | 모델 |
|---|---|---|
| embeddinggemma.gguf | 333,590,944 | EmbeddingGemma-300M Q8_0 — 색인 구축·질의 임베딩에 사용 중 |
| qwen3-emb.gguf | 639,150,592 | Qwen3-Embedding-0.6B Q8_0 — 노트북 GPU 구축용 1순위 |
| qwen3-rerank.gguf | 639,150,752 | Qwen3-Reranker-0.6B Q8_0 (커뮤니티 변환본) |

## 색인 다시 만들기

색인(솔버별 `chunks.json`+`vectors.npy`)은 내려받는 게 아니라 메뉴얼로 다시 만듭니다.

1. 위 메뉴얼을 받아 한 폴더에 둡니다 (Code_Aster는 저장소 복제 후 위 13개).
2. 임베딩 서버를 띄웁니다: `llama-server -m embeddinggemma.gguf --embedding --host 127.0.0.1 --port 18081 --alias embeddinggemma -c 16384 --parallel 4 -b 8192 -ub 8192` (llama.cpp).
3. 구축 스크립트 `build_solver_indexes.py`를 실행합니다. 스크립트 원본은 작업 서버 `~/workspace/tools/knowledge-index/`와 Drive 백업 폴더에 있습니다. 환경변수 `CAELAB_ASSETS_DIR`로 자산 폴더를 지정할 수 있고, 중간에 끊겨도 이어받기가 됩니다.
4. 만든 색인을 가리키면 패널이 씁니다: `CAELAB_RAG_INDEX_DIR=<자산>/index/by-solver`, `CAELAB_EMBED_ENDPOINT=http://127.0.0.1:18081/v1`, `CAELAB_EMBED_MODEL=embeddinggemma`.

현재 색인 구성 (2026-10-22 실측): calculix 1,365 + lsdyna 1,743 + radioss 4,224 + code_aster 356 = 7,688조각, 768차원.

## 테스트와의 관계

단위 테스트는 이 자산 없이 돕니다. 테스트가 작은 가짜 자료로 색인을 즉석에서 만들기 때문입니다. 실제 메뉴얼·색인이 필요한 건 품질 평가뿐입니다: `tests/rag_eval_questions.json` + `tests/rag_eval_run.py` (실모델 서버를 띄우고 실행하는 수동 평가 도구이며 자동 테스트가 아닙니다).
