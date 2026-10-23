# 지식 자산 출처 — 메뉴얼·모델 받는 곳과 색인 다시 만들기

전문가 지식 모듈(`caelab/assist`)의 매뉴얼 PDF와 임베딩 모델은 이 저장소에 넣지 않습니다. 아래는 개발자 환경의 자산 출처 기록입니다. 외부 구축 스크립트와 색인에는 환경 의존성이 남아 있어, 이 문서만으로 어느 환경에서든 재구축된다고 보장하지 않습니다.

작업 사본 위치: 서버 `~/workspace/tools/` (manuals/, llm-local/models/, knowledge-index/)
백업 사본: 사용자 Google Drive 폴더 "CAE Lab 메뉴얼·색인" (메뉴얼·색인·구축 스크립트·평가 기록)

## 메뉴얼 (전부 공식 공개 PDF)

주소와 크기는 2026-10-22 개발 기록의 값입니다. 2026-10-23 정합성 수정에서 이 파일들을 다시 다운로드·대조하지 않았습니다.

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
3. 외부 구축 스크립트 `build_solver_indexes.py`는 작업 서버 `~/workspace/tools/knowledge-index/`와 Drive 백업 폴더에 있습니다. 확인한 백업본은 개발자 절대 경로를 사용하며 `CAELAB_ASSETS_DIR`를 읽지 않습니다. CalculiX 색인은 기존 사본을 복사합니다. 따라서 경로 수정과 기존 CalculiX 색인 확보 또는 별도 재구축이 필요합니다. 이 스크립트를 저장소에 포함된 범용 복원 도구라고 보지 않습니다.
4. 만든 색인을 가리키면 패널이 씁니다: `CAELAB_RAG_INDEX_DIR=<자산>/index/by-solver`, `CAELAB_EMBED_ENDPOINT=http://127.0.0.1:18081/v1`, `CAELAB_EMBED_MODEL=embeddinggemma`.

기존 개발 기록의 색인 구성 (2026-10-22): calculix 1,365 + lsdyna 1,743 + radioss 4,224 + code_aster 356 = 7,688조각, 768차원. 총 조각 수는 검색 또는 답변 품질 지표가 아닙니다.

## 테스트와의 관계

단위 테스트는 작은 가짜 자료로 색인을 만들어 평가 계약을 검사합니다. 실제 매뉴얼 검색 품질이나 답변의 공학적 타당성을 입증하지 않습니다.

### 과거 수치의 해석

`tests/rag_eval_questions.json`의 13문항과 기존 69.2%→100%는 역사적 개발 평가 자료로 보존합니다. 앞 수치는 노트 7개, 뒤 수치는 CalculiX 매뉴얼 1,365조각을 사용한 비교입니다. 4문항은 `gold_note=null`이라 이전 기준에서 자동 실패했고, 제목·본문의 문자열 포함도 정답으로 셌습니다. 자료 확충 효과와 검색 방식 개선이 섞여 있으므로 알고리즘 우수성이나 솔버 4종 전체의 품질로 해석하지 않습니다. 이 질문을 과거 청킹·검색 조정에 사용했는지는 확인되지 않았으며, 독립 평가라고 부르지 않습니다.

### 별도 평가 절차

1. 매뉴얼·조각·벡터와 임베딩 모델 버전을 고정합니다. 조각에는 solver 태그와 고유한 `source_id:chunk_id`가 있어야 합니다. 태그 없는 기존 색인은 검토 후 메타데이터를 보완합니다.
2. `tests/rag_eval_holdout_questions.json`은 기존 13문항과 별도로 작성한 24문항(4종 × 6개)입니다. 2026-10-24 검색 순위를 보지 않은 별도 CAE 검토자가 실제 조각 내용을 읽고 `relevant_chunk_ids`와 이유·범위 한계를 주석했습니다. 질문 id·solver·본문은 초안 그대로이며 검색·청킹 조정에 사용하지 않았습니다. 근거 있는 19개와 근거 부족·손상 5개를 분리했으며 실제 검색 평가 완료 자료는 아닙니다.
3. 아래 도구는 같은 색인과 같은 질문으로 BM25·하이브리드(선택적으로 재정렬)를 비교합니다. `--index-dir`는 단일 색인이나 솔버별 하위 색인 폴더를 받습니다. 임베딩 서버는 색인 구축 당시 모델이어야 합니다. Hashing 대체, 혼합 모델, 미주석 질문은 점수를 내지 않고 실패합니다.

```bash
python tests/rag_eval_run.py --index-dir /path/to/index/by-solver \
    --questions /path/to/reviewed_questions.json
```

4. 결과의 전체·솔버별 적중 개수와 질문 수, 검색 출처 목록을 함께 보관합니다. 질문 수가 적은 점을 밝히고, 자료 확충 전후 효과는 검색 방식 비교와 분리합니다. 이 결과는 검색 관련도 Hit@5이며 최종 답변 정답률이 아닙니다.
5. 실제 구독 연구팀 흐름, 잘못된 공학 제안, 인용 불일치, 다른 솔버 자료·자료 없음·서비스 오류 처리는 별도 끝단 검사로 확인합니다. 주석 24문항의 실모델 검색·답변 검사는 아직 실행하지 않았으므로 새 품질 수치를 보고하지 않습니다.

### P5에서 고정한 자료와 주석

사용자가 안내한 [Drive 백업 폴더](https://drive.google.com/drive/folders/1_1SKeN_PBUtQKD5YjgQA46NB_aqR2u_H)에서 솔버 4종의 `chunks.json`/`vectors.npy` 원본 8개를 확보했습니다. 파일은 저장소 밖에 두며 원본 바이트를 변경하지 않았습니다. 같은 `endpoint:embeddinggemma` 모델의 7,688조각·768차원 벡터입니다. 별도 사본에서 태그가 없던 CalculiX 1,365조각에 solver를 추가하고, 그중 한국어 요약 노트 7개의 source_id를 `calculix-notes`로 구분했습니다. 조각 본문·순서·벡터는 원본과 같습니다. 원본 CalculiX chunks SHA256은 `7ee439a8dd13a2b6b91d3a756a191226f6e48c8e83b040ad49c7ab6f63aa4426`, 메타데이터 보완 사본은 `c8b7b8f6cfb101301c7aa44603f21d1db014ab054e2baee6fb0c23904cb5705e`입니다. 나머지 파일 해시와 주석 방법은 질문 JSON에 기록했습니다. 요약 노트를 공식 매뉴얼 발췌로 표시하지 않습니다.

| 솔버 | 전체 질문 | 검색 관련도 평가 대상 | 자료 부족·손상 |
|---|---:|---:|---:|
| CalculiX | 6 | 6 | 0 |
| Code_Aster | 6 | 2 | 4 |
| LS-DYNA | 6 | 5 | 1 |
| OpenRadioss | 6 | 6 | 0 |
| 합계 | 24 | 19 | 5 |

Code_Aster 01·03·04·05는 현재 선택 문서의 범위가 질문에 부족하고, LS-DYNA 02는 필요한 수식의 추출 손상 때문에 근거 없음으로 분류했습니다. 19개만 `annotation_status == "answerable"`로 별도 JSON에 추출해 위 평가 도구에 전달합니다. 나머지 5개는 답변 보류 검사 대상이며 검색 실패나 성공 분모에 섞지 않습니다. 빈 gold를 자동 통과시키지 않도록 평가 도구의 거부 동작은 유지했습니다. 실제 평가에는 색인을 만들 때 쓴 임베딩 모델 서버가 필요하며, 이번 환경에는 설정된 서버가 없어 BM25/하이브리드 비교와 최종 답변 검토를 수행하지 않았습니다.
