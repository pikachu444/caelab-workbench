"""Evaluation validity checks; synthetic data provides no RAG quality claim."""

from unittest.mock import patch

import pytest

from caelab.assist import rag
from rag_eval_run import _hit, evaluate, load_evaluation_index, validate_questions


def _fixture():
    embedder = rag.HashingEmbedder()
    chunks = [rag.Chunk("reactions", "ccx", ("*NODE PRINT",),
                        "Read reactions at constrained nodes.", "calculix"),
              rag.Chunk("mention", "ccx", ("Unrelated",), "NLGEOM", "calculix"),
              rag.Chunk("reactions", "aster", ("Results",),
                        "Read reactions at constrained nodes.", "code_aster")]
    questions = [{"id": "q", "question": "Read reactions at constrained nodes",
                  "solver": "calculix", "relevant_chunk_ids": ["ccx:reactions"]}]
    return rag.HybridIndex.build(chunks, embedder), questions, embedder


def test_marker_or_same_chunk_name_in_another_source_is_not_relevance():
    index, questions, _ = _fixture()
    assert _hit([index.chunks[0]], questions[0])
    assert not _hit([index.chunks[1], index.chunks[2]], questions[0])
    with pytest.raises(ValueError, match="unjudged"):
        _hit([index.chunks[1]], {"id": "legacy", "gold_text": "NLGEOM"})


def test_unjudged_or_missing_gold_cannot_silently_change_denominator():
    index, questions, _ = _fixture()
    with pytest.raises(ValueError, match="unjudged"):
        validate_questions(index, questions + [{"id": "q2", "question": "x",
                                                "solver": "calculix"}])
    questions[0]["relevant_chunk_ids"] = ["ccx:missing"]
    with pytest.raises(ValueError, match="absent"):
        validate_questions(index, questions)
    questions[0]["relevant_chunk_ids"] = ["aster:reactions"]
    with pytest.raises(ValueError, match="wrong solver"):
        validate_questions(index, questions)


def test_methods_use_one_corpus_solver_filter_and_question_denominator():
    index, questions, embedder = _fixture()
    with patch.object(index, "search", wraps=index.search) as search:
        result = evaluate(index, questions, embedder)
    assert search.call_count == 2
    assert all(c.kwargs["solver"] == "calculix" for c in search.call_args_list)
    assert result["scores"]["all"]["bm25"] == {"hits": 1, "questions": 1}
    assert result["scores"]["all"]["hybrid"] == {"hits": 1, "questions": 1}
    assert result["scores"]["calculix"] == result["scores"]["all"]
    for retrieval in result["rows"][0]["retrieval"].values():
        assert "aster:reactions" not in retrieval["citations"]


def test_wrong_query_embedder_is_rejected_before_search():
    index, questions, embedder = _fixture()
    index.embedder_id = "endpoint:original-model"
    with patch.object(index, "search", side_effect=AssertionError("must not search")):
        with pytest.raises(ValueError, match="must match"):
            evaluate(index, questions, embedder)


def test_per_solver_indexes_are_compared_together_without_model_substitution(tmp_path):
    index, questions, embedder = _fixture()
    for solver in ("calculix", "code_aster"):
        saved = rag.HybridIndex.build([c for c in index.chunks if c.solver == solver], embedder)
        rag.save_index(saved, tmp_path / solver)
    merged = load_evaluation_index(tmp_path)
    questions.append({"id": "aster", "question": "Read reactions at constrained nodes",
                      "solver": "code_aster", "relevant_chunk_ids": ["aster:reactions"]})
    scores = evaluate(merged, questions, embedder)["scores"]
    assert scores["all"]["bm25"] == {"hits": 2, "questions": 2}
    assert scores["code_aster"]["hybrid"] == {"hits": 1, "questions": 1}
    saved.embedder_id = "different-model"
    rag.save_index(saved, tmp_path / "code_aster")
    with pytest.raises(ValueError, match="different embedding models"):
        load_evaluation_index(tmp_path)
