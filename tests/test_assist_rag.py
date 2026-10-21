"""RAG engine unit tests — offline fallbacks only (hashing embedder,
lexical reranker). Quality numbers for the real local models are
measured separately (tests/rag_eval_run.py) and never claimed here."""

import numpy as np
import pytest

from caelab.assist import rag
from caelab.assist.knowledge import notes_chunks, search_notes_keyword


def _index(chunks):
    return rag.HybridIndex.build(chunks, rag.HashingEmbedder())


def test_markdown_chunks_carry_heading_path(tmp_path):
    doc = tmp_path / "manual.md"
    doc.write_text(
        "# Solver Guide\nintro\n## Contact\n"
        "*FRICTION defines the friction coefficient for a contact pair.\n"
        "## Output\n*NODE PRINT writes nodal results.\n",
        encoding="utf-8",
    )
    chunks = rag.load_source_chunks(doc, "test-manual")
    topics = [c.topic for c in chunks]
    assert any("Solver Guide > Contact" in t for t in topics)
    assert any("Solver Guide > Output" in t for t in topics)
    friction = [c for c in chunks if "FRICTION" in c.text][0]
    assert friction.citation.startswith("test-manual:")
    assert friction.source_id == "test-manual"


def test_new_source_needs_no_code_change(tmp_path):
    extra = tmp_path / "aster.md"
    extra.write_text("# Code_Aster\n## AFFE_CHAR_MECA\nDDL_IMPO fixes degrees of freedom.\n", encoding="utf-8")
    index = _index(notes_chunks())
    before = len(index.chunks)
    index.add_chunks(rag.load_source_chunks(extra, "codeaster-manual"), rag.HashingEmbedder())
    assert len(index.chunks) == before + 1
    hits = index.search("DDL_IMPO로 자유도를 고정", top_k=3, embedder=rag.HashingEmbedder())
    assert any(h.source_id == "codeaster-manual" for h in hits)


def test_hybrid_search_finds_keyword_chunk():
    index = _index(notes_chunks())
    hits = index.search("*NODE PRINT TOTALS=ONLY 반력", top_k=3, embedder=rag.HashingEmbedder())
    assert hits[0].chunk_id == "reactions"


def test_rerank_applies_to_candidates_only():
    index = _index(notes_chunks())
    seen_sizes = []

    class CountingReranker(rag.Reranker):
        id = "counting"

        def rerank(self, query, chunks):
            seen_sizes.append(len(chunks))
            return [float(len(chunks) - i) for i in range(len(chunks))]

    index.search("반력", top_k=2, embedder=rag.HashingEmbedder(),
                 reranker=CountingReranker(), candidate_count=4)
    assert seen_sizes == [4]  # reranker saw exactly the candidate cap
    assert rag.RERANK_CANDIDATES == 20  # design cap for real reranking


def test_index_save_load_roundtrip(tmp_path):
    index = _index(notes_chunks())
    rag.save_index(index, tmp_path / "idx")
    loaded = rag.load_index(tmp_path / "idx")
    assert [c.chunk_id for c in loaded.chunks] == [c.chunk_id for c in index.chunks]
    assert loaded.vectors is not None and loaded.vectors.shape == index.vectors.shape
    hits = loaded.search("접촉 마찰 계수", top_k=2, embedder=rag.HashingEmbedder())
    assert hits and hits[0].chunk_id == "contact"


def test_tokenize_keeps_solver_symbols_and_korean_bigrams():
    tokens = rag.tokenize("*NODE PRINT로 반력을 출력")
    assert "*node" in tokens
    assert "반력" in tokens
    assert "반 력".replace(" ", "") in tokens


def test_bm25_prefers_matching_document():
    bm25 = rag.BM25([rag.tokenize("사과 바나나 과일"), rag.tokenize("*NODE PRINT 절점 출력")])
    scores = bm25.scores(rag.tokenize("*NODE PRINT"))
    assert scores[1] > scores[0]


def test_keyword_fallback_still_available_for_comparison():
    hits = search_notes_keyword("반력 평형", limit=5)
    assert hits and hits[0].id == "reactions"


# ------------------------------------------------------- per-solver split
from caelab.assist import knowledge as knowledge_mod  # noqa: E402


def _solver_index(tmp_path):
    dyna = tmp_path / "dyna.md"
    dyna.write_text(
        "# LS-DYNA Theory\n## Hourglass\nHourglass energy must stay small "
        "compared to internal energy.\n", encoding="utf-8")
    chunks = notes_chunks() + rag.load_source_chunks(
        dyna, "lsdyna-theory", solver=knowledge_mod.solver_for_source("lsdyna-theory"))
    return _index(chunks)


def test_solver_inference_from_names_and_text(tmp_path):
    assert knowledge_mod.solver_for_source("lsdyna-theory") == "lsdyna"
    assert knowledge_mod.solver_for_source("radioss-referenceguide") == "radioss"
    assert knowledge_mod.solver_for_source("ccx_2.23") == "calculix"
    assert knowledge_mod.solver_for_source("aster-u2", tmp_path / "aster-u2.pdf") == "code_aster"
    assert knowledge_mod.infer_solver("calculix 계산 완료. 최대 변위 0.133 mm") == "calculix"
    assert knowledge_mod.infer_solver("LS-DYNA 결과 요약") == "lsdyna"
    # word-boundary discipline: "dynamic" is not LS-DYNA
    assert knowledge_mod.infer_solver("dynamic analysis of a structure") == ""


def test_search_restricted_to_one_solver(tmp_path):
    index = _solver_index(tmp_path)
    hits = index.search("반력 평형 node print", top_k=3,
                        embedder=rag.HashingEmbedder(), solver="lsdyna")
    assert hits and all(h.solver in ("lsdyna", "") for h in hits)
    assert not any(h.source_id == "calculix-manual" for h in hits)
    hits = index.search("hourglass energy internal energy", top_k=3,
                        embedder=rag.HashingEmbedder(), solver="calculix")
    assert not any(h.source_id == "lsdyna-theory" for h in hits)
    hits = index.search("hourglass energy", top_k=3,
                        embedder=rag.HashingEmbedder(), solver="lsdyna")
    assert hits[0].source_id == "lsdyna-theory"


def test_per_solver_prebuilt_folders_merge(tmp_path, monkeypatch):
    root = tmp_path / "by-solver"
    (root / "calculix").mkdir(parents=True)
    (root / "lsdyna").mkdir(parents=True)
    rag.save_index(_index(notes_chunks()), root / "calculix")
    dyna = tmp_path / "dyna.md"
    dyna.write_text("# T\n## H\nHourglass energy control in explicit dynamics.\n",
                    encoding="utf-8")
    # saved without solver tags: the folder name must supply the solver
    untagged = [rag.Chunk(c.chunk_id, c.source_id, c.heading_path, c.text)
                for c in rag.load_source_chunks(dyna, "lsdyna-theory")]
    rag.save_index(_index(untagged), root / "lsdyna")
    monkeypatch.setenv("CAELAB_RAG_INDEX_DIR", str(root))
    knowledge_mod._index_cache.clear()
    merged = knowledge_mod.default_index()
    solvers = {c.solver for c in merged.chunks}
    assert {"calculix", "lsdyna"} <= solvers
    hits = merged.search("반력", top_k=3, embedder=rag.get_embedder(), solver="lsdyna")
    assert all(h.solver != "calculix" for h in hits)
    knowledge_mod._index_cache.clear()
