"""Before/after retrieval comparison for the RAG switch (not a test).

Run with the real local models up (llama.cpp servers):

    CAELAB_EMBED_ENDPOINT=http://127.0.0.1:18081/v1 CAELAB_EMBED_MODEL=qwen3-emb \
    CAELAB_RERANK_ENDPOINT=http://127.0.0.1:18082 CAELAB_RERANK_MODEL=qwen3-rerank \
    CAELAB_RAG_INDEX_DIR=~/workspace/tools/knowledge-index/rag-v1 \
    python tests/rag_eval_run.py

"Before" is the pre-RAG keyword search over the seven notes.
"After" loads the saved hybrid index and searches BM25+vector (RRF),
with and without the reranker. A hit means a top-5 chunk whose
heading path or text carries the question's gold marker; for the
notes, the gold note id in the top 5.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from caelab.assist import rag
from caelab.assist.knowledge import search_notes_keyword

QUESTIONS = json.loads((Path(__file__).parent / "rag_eval_questions.json").read_text(encoding="utf-8"))


def _hit(chunks, question) -> bool:
    heading = question["gold_heading"].lower()
    marker = question["gold_text"].lower()
    for chunk in chunks:
        if heading in chunk.topic.lower() or marker in chunk.text.lower():
            return True
    return False


def main() -> None:
    index_dir = os.environ["CAELAB_RAG_INDEX_DIR"]
    index = rag.load_index(index_dir)
    embedder = rag.get_embedder()
    reranker = rag.get_reranker()
    print(f"index: {len(index.chunks)} chunks, embedder={index.embedder_id}, reranker={reranker.id}")

    rows = []
    for q in QUESTIONS:
        before = search_notes_keyword(q["question"], limit=5)
        before_hit = q["gold_note"] is not None and any(e.id == q["gold_note"] for e in before)
        hybrid = index.search(q["question"], top_k=5, embedder=embedder, reranker=None)
        reranked = index.search(q["question"], top_k=5, embedder=embedder, reranker=reranker)
        rows.append((q, before_hit, _hit(hybrid, q), _hit(reranked, q)))
        # flush per row: a killed run must keep the rows it finished
        print(f"{q['id']} [{q['kind']}] before={'O' if before_hit else 'X'} "
              f"hybrid={'O' if rows[-1][2] else 'X'} reranked={'O' if rows[-1][3] else 'X'} "
              f"top={reranked[0].topic if reranked else '-'}", flush=True)

    n = len(rows)
    for label, col in (("before (notes keyword)", 1), ("after hybrid (RRF)", 2), ("after hybrid+rerank", 3)):
        hits = sum(1 for r in rows if r[col])
        print(f"{label}: Hit@5 {hits}/{n} = {hits / n:.1%}")


if __name__ == "__main__":
    main()
