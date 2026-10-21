"""Retrieval over solver manuals — chunk, hybrid search, rerank.

The knowledge layer's engine (008 survey, approved): manuals are
split into section chunks that carry their heading path, searched by
BM25 and by vectors together (reciprocal rank fusion), and the fused
top 20 candidates are reranked. Embeddings and reranking come from
local models only — an OpenAI-compatible endpoint on this machine or
the user's (llama.cpp server qualifies); no paid API is ever called.

Extensibility is per source: a parser turns one file into chunks, the
index builder embeds just that file's chunks, and adding a manual
(e.g. Code_Aster) is "drop the file in", never a code change here.
Parsers are registered by file extension.

Without an embedding endpoint the index still works with a
deterministic hashing embedder and a lexical reranker. Those are
fallbacks for offline use and tests — quality claims are only made
for runs with the real local models (Qwen3-Embedding-0.6B /
Qwen3-Reranker-0.6B per the 008 recommendation).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

RRF_K = 60
RERANK_CANDIDATES = 20
CHUNK_CHARS = 1400
CHUNK_OVERLAP = 150
# Vectors are computed over the heading path plus the chunk's lead:
# the lead carries the section's semantics, BM25 covers the full
# text, and CPU indexing time stays proportional to ~900 chars/chunk.
EMBED_TEXT_CHARS = 900


# ---------------------------------------------------------------- chunks
@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    source_id: str
    heading_path: tuple[str, ...]  # e.g. ("Theory", "Convergence")
    text: str
    solver: str = ""  # canonical solver this chunk serves ("" = general)

    @property
    def topic(self) -> str:
        return " > ".join(self.heading_path)

    @property
    def citation(self) -> str:
        return f"{self.source_id}:{self.chunk_id}"


def _slug(text: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z]+", "-", text.lower()).strip("-")
    return slug[:40] or "section"


def _split_text(text: str, limit: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split section text into <=limit pieces on paragraph edges."""
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= limit:
        return [text] if text else []
    pieces: list[str] = []
    paragraphs = text.split("\n\n")
    current = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        while len(para) > limit:  # one huge paragraph: hard-split
            pieces.append(para[:limit])
            para = para[limit - overlap:]
        if current and len(current) + len(para) + 2 > limit:
            pieces.append(current)
            tail = current[-overlap:] if overlap else ""
            current = (tail + "\n\n" + para) if tail else para
        else:
            current = (current + "\n\n" + para) if current else para
    if current:
        pieces.append(current)
    return pieces


def _chunks_from_sections(source_id: str, sections: list[tuple[tuple[str, ...], str]],
                          solver: str = "") -> list[Chunk]:
    chunks: list[Chunk] = []
    for path, text in sections:
        for i, piece in enumerate(_split_text(text)):
            chunks.append(
                Chunk(
                    chunk_id=f"{_slug(path[-1])}-{i:02d}-{len(chunks):04d}",
                    source_id=source_id,
                    heading_path=path,
                    text=piece,
                    solver=solver,
                )
            )
    return chunks


# ---------------------------------------------------------------- parsers
def parse_markdown(path: Path, source_id: str, solver: str = "") -> list[Chunk]:
    """Markdown/text: '#' headings build the heading path."""
    sections: list[tuple[tuple[str, ...], str]] = []
    stack: list[str] = []
    current: list[str] = []
    title = path.stem

    def flush():
        if current and "".join(current).strip():
            sections.append((tuple(stack) or (title,), "\n".join(current)))

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            current = []
            level = len(m.group(1))
            stack = stack[: level - 1] + [m.group(2).strip()]
        else:
            current.append(line)
    flush()
    return _chunks_from_sections(source_id, sections, solver)


def parse_pdf(path: Path, source_id: str, solver: str = "") -> list[Chunk]:
    """PDF: the document outline gives heading paths; each page's text
    belongs to the deepest section starting at or before that page."""
    from pypdf import PdfReader  # lazy: only PDF sources need it

    reader = PdfReader(str(path))
    flat: list[tuple[str, int, int]] = []  # (title, page, depth)

    def walk(items, depth):
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
            else:
                try:
                    flat.append((item.title.strip(), reader.get_destination_page_number(item), depth))
                except Exception:
                    continue

    try:
        walk(reader.outline, 0)
    except Exception:
        flat = []
    if not flat:
        flat = [(path.stem, 0, 0)]
    flat.sort(key=lambda row: row[1])

    sections: list[tuple[tuple[str, ...], str]] = []
    stack: list[str] = []
    current_path: tuple[str, ...] = (flat[0][0],)
    current: list[str] = []
    section_idx = 0
    for page_no in range(len(reader.pages)):
        while section_idx + 1 < len(flat) and flat[section_idx + 1][1] <= page_no:
            if current and "".join(current).strip():
                sections.append((current_path, "\n".join(current)))
            section_idx += 1
            _title, _page, depth = flat[section_idx]
            stack = stack[:depth] + [_title]
            current_path = tuple(stack)
            current = []
        text = reader.pages[page_no].extract_text() or ""
        current.append(_clean_pdf_text(text))
    if current and "".join(current).strip():
        sections.append((current_path, "\n".join(current)))
    return _chunks_from_sections(source_id, sections, solver)


_LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl"}


def _clean_pdf_text(text: str) -> str:
    for lig, rep in _LIGATURES.items():
        text = text.replace(lig, rep)
    return re.sub(r"[ \t]+", " ", text)


PARSERS = {".md": parse_markdown, ".markdown": parse_markdown, ".txt": parse_markdown,
           ".pdf": parse_pdf}


def load_source_chunks(path: str | Path, source_id: str, solver: str = "") -> list[Chunk]:
    """Parse one source file into chunks. New formats register a
    parser in PARSERS; new manuals need no other code change."""
    path = Path(path)
    parser = PARSERS.get(path.suffix.lower())
    if parser is None:
        raise ValueError(f"지원하지 않는 지식 자료 형식입니다: {path.suffix} ({path.name})")
    return parser(path, source_id, solver)


# ---------------------------------------------------------------- tokenize / BM25
_TOKEN = re.compile(r"[0-9a-zA-Z*][0-9a-zA-Z*._-]*|[가-힣]+")


def tokenize(text: str) -> list[str]:
    """Mixed Korean/English tokenizer. Latin tokens keep solver
    symbols (*NODE, C3D10); Korean words also yield bigrams so a
    question word matches inside a longer manual phrase."""
    tokens: list[str] = []
    for raw in _TOKEN.findall((text or "").lower()):
        tokens.append(raw)
        if re.fullmatch(r"[가-힣]+", raw) and len(raw) > 2:
            tokens.extend(raw[i: i + 2] for i in range(len(raw) - 1))
    return tokens


class BM25:
    def __init__(self, documents: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs = documents
        self.doc_len = [len(d) for d in documents]
        self.avg_len = (sum(self.doc_len) / len(documents)) if documents else 0.0
        self.df: dict[str, int] = {}
        self.tf: list[dict[str, int]] = []
        for doc in documents:
            counts: dict[str, int] = {}
            for tok in doc:
                counts[tok] = counts.get(tok, 0) + 1
            self.tf.append(counts)
            for tok in counts:
                self.df[tok] = self.df.get(tok, 0) + 1
        n = len(documents)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in self.df.items()}

    def scores(self, query_tokens: list[str]) -> np.ndarray:
        out = np.zeros(len(self.docs))
        for tok in set(query_tokens):
            idf = self.idf.get(tok)
            if idf is None:
                continue
            for i, counts in enumerate(self.tf):
                f = counts.get(tok, 0)
                if f:
                    denom = f + self.k1 * (1 - self.b + self.b * self.doc_len[i] / max(self.avg_len, 1.0))
                    out[i] += idf * f * (self.k1 + 1) / denom
        return out


# ---------------------------------------------------------------- embedders
class Embedder:
    id = "base"

    def embed(self, texts: list[str]) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError

    def embed_query(self, text: str) -> np.ndarray:
        """Query-side embedding. Some models (Qwen3-Embedding) want an
        instruction prefix on queries only; documents stay raw."""
        return self.embed([text])[0]


class HashingEmbedder(Embedder):
    """Deterministic offline fallback (character n-gram hashing).
    Not a semantic model — quality claims never rest on this one."""

    id = "hashing-fallback"
    DIM = 512

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.DIM), dtype=np.float32)
        for row, text in enumerate(texts):
            norm = re.sub(r"\s+", " ", (text or "").lower())
            grams = [norm[i: i + 3] for i in range(max(0, len(norm) - 2))]
            grams += norm.split()
            for gram in grams:
                h = int(hashlib.md5(gram.encode("utf-8")).hexdigest()[:8], 16)
                out[row, h % self.DIM] += 1.0
        return _normalize(out)


class EndpointEmbedder(Embedder):
    """OpenAI-compatible /embeddings on a local endpoint
    (llama.cpp server with Qwen3-Embedding qualifies)."""

    def __init__(self, endpoint: str, model: str, api_key: str = "") -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.id = f"endpoint:{model}"
        self.query_instruction = os.environ.get("CAELAB_EMBED_QUERY_INSTRUCTION", "")

    def embed_query(self, text: str) -> np.ndarray:
        if self.query_instruction:
            text = f"{self.query_instruction}\n{text}"
        return self.embed([text])[0]

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 32):
            batch = texts[start: start + 32]
            body = json.dumps({"model": self.model, "input": batch}).encode("utf-8")
            request = urllib.request.Request(
                f"{self.endpoint}/embeddings", data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            if self.api_key:
                request.add_header("Authorization", f"Bearer {self.api_key}")
            with urllib.request.urlopen(request, timeout=600) as response:
                data = json.loads(response.read().decode("utf-8"))
            vectors.extend(item["embedding"] for item in sorted(data["data"], key=lambda d: d["index"]))
        return _normalize(np.array(vectors, dtype=np.float32))


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def get_embedder() -> Embedder:
    endpoint = os.environ.get("CAELAB_EMBED_ENDPOINT", "")
    model = os.environ.get("CAELAB_EMBED_MODEL", "")
    if endpoint and model:
        return EndpointEmbedder(endpoint, model, os.environ.get("CAELAB_EMBED_API_KEY", ""))
    return HashingEmbedder()


# ---------------------------------------------------------------- rerankers
class Reranker:
    id = "base"

    def rerank(self, query: str, chunks: list[Chunk]) -> list[float]:  # pragma: no cover
        raise NotImplementedError


class LexicalReranker(Reranker):
    """Offline fallback: exact symbol/phrase hits and token coverage
    over the fused candidates."""

    id = "lexical-fallback"

    def rerank(self, query: str, chunks: list[Chunk]) -> list[float]:
        q_tokens = set(tokenize(query))
        scores: list[float] = []
        for chunk in chunks:
            text = chunk.text.lower()
            heading = chunk.topic.lower()
            body = set(tokenize(chunk.text))
            coverage = len(q_tokens & body) / max(len(q_tokens), 1)
            symbol_hits = sum(1 for t in q_tokens if "*" in t and t in text)
            heading_hits = len(q_tokens & set(tokenize(heading)))
            scores.append(coverage + 0.5 * symbol_hits + 0.25 * heading_hits)
        return scores


class EndpointReranker(Reranker):
    """llama.cpp /reranking endpoint (Qwen3-Reranker qualifies)."""

    def __init__(self, endpoint: str, model: str, api_key: str = "") -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.id = f"endpoint:{model}"

    def rerank(self, query: str, chunks: list[Chunk]) -> list[float]:
        # Heading + lead is what relevance turns on; sending whole
        # chunks for 20 candidates would overflow the model context.
        body = json.dumps({
            "model": self.model, "query": query,
            "documents": [f"{c.topic}\n{c.text[:1200]}" for c in chunks],
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.endpoint}/reranking", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        if self.api_key:
            request.add_header("Authorization", f"Bearer {self.api_key}")
        with urllib.request.urlopen(request, timeout=600) as response:
            data = json.loads(response.read().decode("utf-8"))
        scores = [0.0] * len(chunks)
        for item in data["results"]:
            scores[item["index"]] = float(item["relevance_score"])
        return scores


def get_reranker() -> Reranker:
    endpoint = os.environ.get("CAELAB_RERANK_ENDPOINT", "")
    model = os.environ.get("CAELAB_RERANK_MODEL", "")
    if endpoint and model:
        return EndpointReranker(endpoint, model, os.environ.get("CAELAB_RERANK_API_KEY", ""))
    return LexicalReranker()


# ---------------------------------------------------------------- hybrid index
@dataclass
class HybridIndex:
    chunks: list[Chunk]
    embedder_id: str
    bm25: BM25 = field(init=False)
    vectors: np.ndarray | None = None

    def __post_init__(self) -> None:
        docs = [tokenize(f"{c.topic}\n{c.text}") for c in self.chunks]
        self.bm25 = BM25(docs)

    # -- construction ------------------------------------------------------
    @classmethod
    def build(cls, chunks: list[Chunk], embedder: Embedder) -> "HybridIndex":
        vectors = embedder.embed([_embed_text(c) for c in chunks]) if chunks else None
        return cls(chunks=list(chunks), embedder_id=embedder.id, vectors=vectors)

    def add_chunks(self, chunks: list[Chunk], embedder: Embedder) -> None:
        """Add one source's chunks independently of the others, so a
        new manual re-embeds only its own chunks."""
        if not chunks:
            return
        new_vectors = embedder.embed([_embed_text(c) for c in chunks])
        self.chunks.extend(chunks)
        self.vectors = new_vectors if self.vectors is None else np.vstack([self.vectors, new_vectors])
        self.embedder_id = embedder.id
        self.__post_init__()

    # -- search --------------------------------------------------------------
    def search(self, query: str, top_k: int = 5, embedder: Embedder | None = None,
               reranker: Reranker | None = None,
               candidate_count: int = RERANK_CANDIDATES,
               solver: str | None = None) -> list[Chunk]:
        """Hybrid search. With `solver` set, only that solver's chunks
        (plus untagged general ones) are ranked — a CalculiX question
        must not surface LS-DYNA material as its evidence."""
        if not self.chunks:
            return []
        allowed = None
        if solver:
            allowed = [i for i, c in enumerate(self.chunks)
                       if not c.solver or c.solver == solver]
            if not allowed:
                allowed = None  # unknown solver: hide nothing
        bm25_scores = self.bm25.scores(tokenize(query))
        query_vector = None
        if embedder is not None and self.vectors is not None:
            query_vector = embedder.embed_query(query)
        return self._search_with_scores(query, top_k, reranker, candidate_count,
                                        bm25_scores, query_vector=query_vector,
                                        allowed=allowed)

    def _search_with_scores(self, query, top_k, reranker, candidate_count, bm25_scores,
                            query_vector: np.ndarray | None = None,
                            allowed: list[int] | None = None) -> list[Chunk]:
        pool = allowed if allowed is not None else list(range(len(self.chunks)))

        def order_within(scores: np.ndarray) -> list[int]:
            return sorted(pool, key=lambda i: -scores[i])

        rankings = [order_within(bm25_scores)]
        if query_vector is not None and self.vectors is not None:
            rankings.append(order_within(self.vectors @ query_vector))
        fused_scores: dict[int, float] = {}
        for ranking in rankings:
            for rank, idx in enumerate(ranking):
                fused_scores[idx] = fused_scores.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)
        order = sorted(fused_scores, key=lambda i: -fused_scores[i])
        candidates = [self.chunks[i] for i in order[:candidate_count]]
        if reranker is not None and candidates:
            scores = reranker.rerank(query, candidates)
            candidates = [c for _s, c in sorted(zip(scores, candidates), key=lambda p: -p[0])]
        return candidates[:top_k]

    def search_embedded(self, query: str, query_vector: np.ndarray, top_k: int = 5,
                        reranker: Reranker | None = None,
                        candidate_count: int = RERANK_CANDIDATES,
                        solver: str | None = None) -> list[Chunk]:
        bm25_scores = self.bm25.scores(tokenize(query))
        allowed = None
        if solver:
            allowed = [i for i, c in enumerate(self.chunks)
                       if not c.solver or c.solver == solver] or None
        return self._search_with_scores(query, top_k, reranker, candidate_count,
                                        bm25_scores, query_vector=query_vector,
                                        allowed=allowed)


def _embed_text(chunk: Chunk) -> str:
    return f"{chunk.topic}\n{chunk.text[:EMBED_TEXT_CHARS]}"


def _rank_order(scores: np.ndarray) -> list[int]:
    return [int(i) for i in np.argsort(-scores, kind="stable")]


def _rrf(score_arrays: list[np.ndarray]) -> np.ndarray:  # kept for clarity/tests
    out = np.zeros_like(score_arrays[0], dtype=float)
    for scores in score_arrays:
        for rank, idx in enumerate(_rank_order(scores)):
            out[idx] += 1.0 / (RRF_K + rank + 1)
    return out


# ---------------------------------------------------------------- persistence
def save_index(index: HybridIndex, folder: str | Path) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    meta = {
        "embedder_id": index.embedder_id,
        "chunks": [
            {"chunk_id": c.chunk_id, "source_id": c.source_id,
             "heading_path": list(c.heading_path), "text": c.text,
             "solver": c.solver}
            for c in index.chunks
        ],
    }
    # Atomic writes: a killed build must never leave a half-written
    # index behind for a resume to trip over.
    tmp = folder / "chunks.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, folder / "chunks.json")
    if index.vectors is not None:
        tmp_v = folder / "vectors.npy.tmp"
        with open(tmp_v, "wb") as handle:  # a path would get .npy appended
            np.save(handle, index.vectors)
        os.replace(tmp_v, folder / "vectors.npy")
    return folder


def load_index(folder: str | Path) -> HybridIndex:
    folder = Path(folder)
    meta = json.loads((folder / "chunks.json").read_text(encoding="utf-8"))
    chunks = [
        Chunk(c["chunk_id"], c["source_id"], tuple(c["heading_path"]), c["text"],
              solver=c.get("solver", ""))
        for c in meta["chunks"]
    ]
    vectors_file = folder / "vectors.npy"
    vectors = np.load(vectors_file) if vectors_file.exists() else None
    return HybridIndex(chunks=chunks, embedder_id=meta["embedder_id"], vectors=vectors)
