"""Compare BM25 and hybrid retrieval over the SAME saved manual index.

This is a manual quality evaluation, not an automatic solver/GUI test.
Questions need independently reviewed relevant_chunk_ids in
source_id:chunk_id form and an explicit solver. Missing judgments
are errors, never misses or successes. Legacy marker-based questions
remain in rag_eval_questions.json as a historical development set.

Example (with the original embedding model server running):
    python tests/rag_eval_run.py --index-dir /path/to/index \
        --questions /path/to/reviewed_questions.json

Use --rerank only when CAELAB_RERANK_ENDPOINT/MODEL are configured.
No service is launched and no model fallback is accepted for scoring.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from caelab.assist import rag


def load_evaluation_index(folder: Path):
    """Load one frozen index or all indexed children of a by-solver folder.

    Unlike product fallbacks, missing vectors or mixed embedding models
    abort evaluation. Solver tags must be present in the frozen metadata.
    """
    paths = ([folder] if (folder / "chunks.json").is_file()
             else sorted(p.parent for p in folder.glob("*/chunks.json")))
    if not paths:
        raise ValueError(f"no saved indexes found in {folder}")
    indexes = [rag.load_index(path) for path in paths]
    model_ids = {index.embedder_id for index in indexes}
    if len(model_ids) != 1:
        raise ValueError("saved indexes use different embedding models")
    for index in indexes:
        if (index.vectors is None or index.vectors.ndim != 2
                or len(index.vectors) != len(index.chunks)):
            raise ValueError("saved index needs one vector per chunk")
        if any(not chunk.solver for chunk in index.chunks):
            raise ValueError("annotate solver tags in the frozen index before evaluation")
    return rag.HybridIndex(
        chunks=[chunk for index in indexes for chunk in index.chunks],
        embedder_id=indexes[0].embedder_id,
        vectors=np.vstack([index.vectors for index in indexes]),
    )


def load_questions(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["questions"] if isinstance(payload, dict) else payload


def validate_questions(index, questions) -> None:
    if not isinstance(questions, list) or not questions:
        raise ValueError("evaluation requires a nonempty question list")
    chunks = {c.citation: c for c in index.chunks}
    if len(chunks) != len(index.chunks):
        raise ValueError("chunk citations must be unique across the evaluated indexes")
    seen = set()
    for q in questions:
        if not isinstance(q, dict) or not q.get("id") or q["id"] in seen:
            raise ValueError("each question needs a unique id")
        seen.add(q["id"])
        if not q.get("question") or not q.get("solver"):
            raise ValueError(f"{q['id']}: question and solver are required")
        gold = q.get("relevant_chunk_ids")
        if not isinstance(gold, list) or not gold or not all(isinstance(c, str) for c in gold):
            raise ValueError(
                f"{q['id']}: unjudged; annotate relevant_chunk_ids from the frozen index. "
                "Legacy heading/text markers cannot be scored as relevance.")
        for citation in gold:
            if citation not in chunks:
                raise ValueError(f"{q['id']}: gold chunk absent from this index: {citation}")
            if chunks[citation].solver != q["solver"]:
                raise ValueError(f"{q['id']}: gold chunk has the wrong solver: {citation}")


def _hit(chunks, question) -> bool:
    gold = question.get("relevant_chunk_ids")
    if not gold:
        raise ValueError(f"{question.get('id', '?')}: unjudged")
    return any(c.solver == question["solver"] and c.citation in gold for c in chunks)


def evaluate(index, questions, embedder, reranker=None) -> dict:
    validate_questions(index, questions)
    if index.vectors is None or index.embedder_id != embedder.id:
        raise ValueError("query embedder must match the saved index embedding model")
    methods = {"bm25": (None, None), "hybrid": (embedder, None)}
    if reranker is not None:
        methods["hybrid_rerank"] = (embedder, reranker)
    rows = []
    for q in questions:
        row = {"id": q["id"], "solver": q["solver"], "retrieval": {}}
        for name, (embedding, ranking) in methods.items():
            hits = index.search(q["question"], top_k=5, solver=q["solver"],
                                embedder=embedding, reranker=ranking)
            row["retrieval"][name] = {
                "hit": _hit(hits, q),
                "citations": [c.citation for c in hits],
            }
        rows.append(row)
    # This measures retrieval relevance, not engineering answer accuracy.
    groups = {"all": rows}
    groups.update({solver: [r for r in rows if r["solver"] == solver]
                   for solver in sorted({r["solver"] for r in rows})})
    scores = {group: {name: {"hits": sum(r["retrieval"][name]["hit"] for r in items),
                            "questions": len(items)}
                      for name in methods} for group, items in groups.items()}
    return {"metric": "judged-retrieval-Hit@5", "chunks": len(index.chunks),
            "sources": sorted({c.source_id for c in index.chunks}),
            "embedder": embedder.id, "reranker": reranker.id if reranker else None,
            "scores": scores, "rows": rows}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", type=Path, default=os.environ.get("CAELAB_RAG_INDEX_DIR"))
    parser.add_argument("--questions", type=Path, required=True,
                        help="reviewed question file; unjudged drafts cannot produce scores")
    parser.add_argument("--rerank", action="store_true")
    args = parser.parse_args(argv)
    if args.index_dir is None:
        parser.error("--index-dir or CAELAB_RAG_INDEX_DIR is required")
    try:
        index = load_evaluation_index(args.index_dir.expanduser())
        questions = load_questions(args.questions)
        validate_questions(index, questions)
        embedder = rag.get_embedder()
        if not isinstance(embedder, rag.EndpointEmbedder):
            raise ValueError("configure the original embedding model endpoint; hashing fallback is not a quality evaluation")
        reranker = rag.get_reranker() if args.rerank else None
        if args.rerank and not isinstance(reranker, rag.EndpointReranker):
            raise ValueError("configure the reranker endpoint before using --rerank")
        result = evaluate(index, questions, embedder, reranker)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(2, f"evaluation not completed: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
