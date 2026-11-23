"""Knowledge for the experts — a RAG index over solver manuals.

The pack used to be seven hand-written notes searched by keyword.
It is now an index (see `rag.py`): manual originals are split into
section chunks carrying their heading path, searched by BM25 +
vectors fused with RRF, reranked over the top 20 candidates. The
seven notes were not thrown away — they are the index's seed source,
under the same citation ids they always had.

Contracts that did not change:
- A reply may cite only material actually shown to the expert;
  `verify_citations` enforces that against the shown ids.
- Numbers from a user's run never live here; this layer is manual
  guidance only.

Adding a manual is data, not code: set CAELAB_KNOWLEDGE_SOURCES to
``source-id:/path/to/manual.pdf;other-id:/path/to/other.md`` (or drop
files into CAELAB_KNOWLEDGE_DIR, where the source id is the file
stem), and each file is parsed and embedded independently.
Embeddings/reranking use local endpoints when configured
(CAELAB_EMBED_*, CAELAB_RERANK_*); otherwise deterministic offline
fallbacks keep the flow working.

Indexes are separated per solver: every chunk carries the solver it
serves, and a search for one solver never ranks another solver's
chunks. The solver of a source is inferred from its id or file name
(``lsdyna-theory.pdf`` -> ``lsdyna``) and can be forced with the
``solver/source-id:/path`` form. CAELAB_RAG_INDEX_DIR may point at
one index folder or at a parent whose subfolders are per-solver
indexes (``by-solver/lsdyna``, ``by-solver/radioss``, ...); all of
them are loaded and merged, keeping their solver tags.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from . import rag
from .contracts import CorpusChunkSpec, QueryPlan

SOURCE_ID = "calculix-manual"
SOURCE_LABEL = "CalculiX CrunchiX User's Manual v2.23 (Dhondt)"


@dataclass(frozen=True)
class KnowledgeEntry:
    id: str  # citation id within its source, cited as [근거: <source>:<id>]
    topic: str
    keywords: tuple[str, ...]
    text: str
    source_id: str = SOURCE_ID
    solver: str = ""
    corpus_chunk: CorpusChunkSpec | None = None
    source_kind: str = "legacy_unknown"
    review_status: str = "unreviewed"

    @property
    def solver_scope(self) -> tuple[str, ...]:
        if self.corpus_chunk is not None:
            return self.corpus_chunk.coverage_facets.get("solver", ())
        return (self.solver,) if self.solver else ()


@dataclass(frozen=True)
class KnowledgeSearchResult:
    status: str  # ok | no_material | error
    entries: list[KnowledgeEntry]
    solver: str = ""
    error: str | None = None
    query_plan: QueryPlan | None = field(default=None, compare=False)
    corpus_revision: str | None = field(default=None, compare=False)
    mismatched_candidates: tuple[CorpusChunkSpec, ...] = field(default=(), compare=False)
    index_digest: str | None = field(default=None, compare=False)


# Canonical solver names and the spellings that identify them in
# source ids, file names and discussion text. Inference for search
# uses word boundaries, so "dynamic" never resolves to LS-DYNA.
SOLVER_ALIASES: dict[str, tuple[str, ...]] = {
    "calculix": ("calculix", "ccx", "crunchix"),
    "lsdyna": ("ls-dyna", "lsdyna", "dyna"),
    "radioss": ("openradioss", "radioss", "opencourant"),
    "code_aster": ("code_aster", "code-aster", "codeaster", "salome-meca", "salomemeca", "aster"),
    "fenicsx": ("fenicsx", "fenics", "dolfinx"),
    "abaqus": ("abaqus",),
}


def infer_solver(text: str) -> str:
    """Canonical solver named in free text (a question, a result
    summary), or "" when none is named."""
    low = (text or "").lower()
    for solver, aliases in SOLVER_ALIASES.items():
        for alias in aliases:
            if re.search(rf"(?<![0-9a-z]){re.escape(alias)}(?![0-9a-z])", low):
                return solver
    return ""


def solver_for_source(source_id: str, path: Path | None = None) -> str:
    """Solver of a knowledge source, from its id or file name tokens
    (``lsdyna-theory`` -> ``lsdyna``, ``radioss-referenceguide`` ->
    ``radioss``). "" when the name says nothing."""
    names = [source_id] + ([path.stem] if path is not None else [])
    for name in names:
        for token in re.split(r"[^0-9a-z]+", (name or "").lower()):
            if not token:
                continue
            for solver, aliases in SOLVER_ALIASES.items():
                if token in aliases or any(
                        token.startswith(a) for a in aliases if len(a) >= 4):
                    return solver
    return ""


KNOWLEDGE_PACK: tuple[KnowledgeEntry, ...] = (
    KnowledgeEntry(
        "reactions",
        "반력 읽기",
        ("반력", "reaction", "node print", "node file", "forc", "평형", "equilibrium"),
        "반력은 구속된 절점에 생기는 절점력이다. *NODE PRINT로 절점력을 .dat에 "
        "출력할 수 있고 TOTALS=ONLY를 주면 집합 합계만 나온다. *NODE FILE의 RF는 "
        ".frd에 반력을 기록한다. 하중을 준 절점의 절점력에는 적용 하중도 섞이므로, "
        "평형 대조는 구속 절점의 반력 합과 적용 하중 합을 비교해야 한다.",
    ),
    KnowledgeEntry(
        "static-step",
        "정적 해석 단계",
        ("static", "정적", "*static", "nlgeom", "증분", "increment"),
        "*STATIC은 정적 평형 문제를 푼다. 큰 변형·회전이 있으면 NLGEOM을 켠다. "
        "자동 증분을 쓰면 수렴이 어려울 때 증분이 줄고(cutback), 너무 많이 줄면 "
        "계산이 중단될 수 있으니 로그에서 증분 축소 경고를 먼저 확인한다.",
    ),
    KnowledgeEntry(
        "convergence",
        "수렴 문제 진단",
        ("수렴", "convergence", "발산", "divergence", "cutback", "잔차", "residual"),
        "수렴 실패는 대개 하중 증분이 너무 크거나, 접촉 상태가 급격히 바뀌거나, "
        "구속이 부족해 강체 운동이 남았을 때 생긴다. 로그의 마지막 수렴 반복에서 "
        "어느 자유도·절점에서 잔차가 큰지 확인하고, 증분을 줄이거나 구속을 "
        "점검하는 순서로 좁혀 나간다.",
    ),
    KnowledgeEntry(
        "contact",
        "접촉 정의",
        ("접촉", "contact", "마찰", "friction", "contact pair", "surface behavior"),
        "접촉은 *CONTACT PAIR로 접촉 쌍을 정하고 *SURFACE INTERACTION 아래 "
        "*SURFACE BEHAVIOR로 압력-침투 관계를, *FRICTION으로 마찰 계수를 준다. "
        "주면(master)·종속면(slave) 선택과 초기 틈·간섭이 수렴과 접촉압 분포를 "
        "좌우한다.",
    ),
    KnowledgeEntry(
        "materials",
        "재료 법칙",
        ("재료", "material", "elastic", "plastic", "소성", "탄성계수", "항복"),
        "선형 탄성은 *ELASTIC으로 탄성계수와 포아송비를 준다. 소성은 *PLASTIC에 "
        "응력-소성변형률 점들을 표로 주며, 첫 점이 항복점이다. 단위는 모델 단위계와 "
        "일관돼야 하고, 소성 표의 응력은 단조 증가해야 한다.",
    ),
    KnowledgeEntry(
        "elements",
        "요소 선택",
        ("요소", "element", "c3d8", "c3d10", "c3d4", "메시", "mesh", "사면체", "육면체"),
        "C3D8(1차 육면체)은 휨에서 잠금(locking)이 생길 수 있어 휨 지배 문제에는 "
        "C3D20이나 C3D10(2차 사면체)이 흔히 더 낫다. C3D4(1차 사면체)는 지나치게 "
        "뻣뻣하므로 정량 해석에는 피한다. 요소 왜곡이 크면 정확도와 수렴이 함께 "
        "나빠진다.",
    ),
    KnowledgeEntry(
        "verification",
        "검증 상태의 의미",
        ("검증", "verification", "벤치마크", "benchmark"),
        "결과에 표시되는 검증 상태는 그 모델·조건 조합이 검증 사례로 확인된 적이 "
        "있는지를 말한다. 검증 사례가 없다는 것은 실행이 잘못됐다는 뜻이 아니라, "
        "대조할 기준 해가 아직 없다는 뜻이며, 평형·단위·경계조건 같은 기본 대조를 "
        "스스로 해야 한다는 신호다.",
    ),
)


def get_entry(entry_id: str) -> KnowledgeEntry:
    for entry in KNOWLEDGE_PACK:
        if entry.id == entry_id:
            return entry
    raise KeyError(f"unknown knowledge entry: {entry_id}")


def notes_chunks() -> list[rag.Chunk]:
    """The seven hand notes as the index's seed source. Chunk ids are
    the old entry ids, so citations like calculix-manual:reactions
    keep meaning what they meant before the RAG switch."""
    return [
        rag.Chunk(
            chunk_id=entry.id,
            source_id=entry.source_id,
            heading_path=("CalculiX 매뉴얼 요약 노트", entry.topic),
            text=entry.text,
            solver="calculix",
        )
        for entry in KNOWLEDGE_PACK
    ]


def configured_sources() -> list[tuple[str, Path, str]]:
    """(source_id, file, solver) triples from the environment. Each
    file is parsed and embedded on its own — adding a source never
    touches the others. An entry may force its solver with the
    ``solver/source-id:/path`` form."""
    sources: list[tuple[str, Path, str]] = []
    raw = os.environ.get("CAELAB_KNOWLEDGE_SOURCES", "")
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        head, sep, path = part.partition(":")
        source_id = head.strip()
        solver = ""
        if "/" in source_id:  # explicit "solver/source-id"
            forced, _, source_id = source_id.partition("/")
            solver = infer_solver(forced) or forced.strip()
        if sep and source_id and path.strip():
            file = Path(path.strip())
            sources.append((source_id, file, solver or solver_for_source(source_id, file)))
    folder = os.environ.get("CAELAB_KNOWLEDGE_DIR", "")
    if folder and not Path(folder).is_dir():
        raise FileNotFoundError(f"Knowledge directory not found: {folder}")
    if folder:
        for file in sorted(Path(folder).iterdir()):
            if file.suffix.lower() in rag.PARSERS:
                source_id = rag._slug(file.stem) or file.stem
                sources.append((source_id, file, solver_for_source(source_id, file)))
    return sources


_index_cache: dict[tuple, rag.HybridIndex] = {}
_index_cache_lock = threading.Lock()


def _publish_index(key, index, check_cancelled):
    with _index_cache_lock:
        check_cancelled()
        previous = _index_cache.copy()
        _index_cache.clear()
        _index_cache[key] = index
        try:
            check_cancelled()
        except Exception:
            _index_cache.clear()
            _index_cache.update(previous)
            raise


def _cached_index(key):
    with _index_cache_lock:
        return _index_cache.get(key)


def _load_prebuilt(root: Path, embedder_id: str, *, check_cancelled=lambda: None) -> rag.HybridIndex | None:
    """Load a legacy prebuilt index from `root` or merge its per-solver subfolders. Chunks that carry no solver
    tag yet (older builds) take the solver of their subfolder name.
    None when any folder's embedder differs from the configured one."""
    if (root / "chunks.json").exists():
        folders = [root]
    elif root.is_dir():
        folders = sorted(p for p in root.iterdir()
                         if p.is_dir() and (p / "chunks.json").exists())
    else:
        return None
    if not folders:
        return None
    merged: rag.HybridIndex | None = None
    for folder in folders:
        check_cancelled()
        loaded = rag.load_index(folder)
        if loaded.embedder_id != embedder_id:
            return None
        if folder != root:
            fallback = infer_solver(folder.name) or folder.name
            loaded.chunks = [c if c.solver else replace(c, solver=fallback)
                             for c in loaded.chunks]
        if merged is None:
            merged = loaded
        else:
            vectors = None
            if merged.vectors is not None and loaded.vectors is not None:
                vectors = np.vstack([merged.vectors, loaded.vectors])
            merged = rag.HybridIndex(chunks=merged.chunks + loaded.chunks,
                                     embedder_id=embedder_id, vectors=vectors)
    return merged


def default_index(*, cancel_event=None, deadline: float | None = None, _publish=None) -> rag.HybridIndex:
    """The shared index: seed notes + configured manual files,
    embedded with the configured embedder. Rebuilt only when the
    source set or embedder changes.

    Prebuilt indexes (CAELAB_CORPUS_INDEX_DIR or CAELAB_RAG_INDEX_DIR, made once — e.g. on the
    GPU laptop — and copied over) are loaded instead of rebuilt. A corpus bundle uses
    its fixed retrieval profile. Legacy indexes use the configured embedder."""
    def check_cancelled():
        rag.check_index_budget(cancel_event, deadline)

    def publish(key, index):
        check_cancelled()
        if _publish is None:
            _publish_index(key, index, check_cancelled)
        else:
            _publish(key, index)
        return index

    check_cancelled()
    corpus_dir = os.environ.get("CAELAB_CORPUS_INDEX_DIR", "")
    legacy_dir = os.environ.get("CAELAB_RAG_INDEX_DIR", "")
    if corpus_dir or (legacy_dir and ((Path(legacy_dir) / "current.json").is_file()
                                     or (Path(legacy_dir) / "manifest.json").is_file())):
        from .corpus import load_corpus_bundle

        root = Path(corpus_dir or legacy_dir).resolve()
        # Validate the current bundle even when its immutable index was seen before.
        loaded, _ = load_corpus_bundle(root, check_cancelled=check_cancelled)
        key = ("corpus", root, loaded.corpus_revision, loaded.index_digest)
        cached = _cached_index(key)
        if cached is not None:
            check_cancelled()
            return cached
        return publish(key, loaded)

    embedder = rag.get_embedder()
    prebuilt = legacy_dir
    if prebuilt:
        key = ("prebuilt", prebuilt, embedder.id)
        cached = _cached_index(key)
        if cached is not None:
            check_cancelled()
            return cached
        loaded = _load_prebuilt(Path(prebuilt), embedder.id, check_cancelled=check_cancelled)
        if loaded is not None:
            return publish(key, loaded)
        raise ValueError(f"Configured RAG index is missing or incompatible: {prebuilt}")
    sources = configured_sources()
    source_keys = []
    for source_id, path, _solver in sources:
        check_cancelled()
        source_keys.append((source_id, str(path), path.stat().st_mtime if path.exists() else 0))
    key = (embedder.id, tuple(source_keys))
    cached = _cached_index(key)
    if cached is not None:
        check_cancelled()
        return cached
    index = rag.HybridIndex.build(notes_chunks(), embedder, cancel_event=cancel_event, deadline=deadline)
    for source_id, path, solver in sources:
        check_cancelled()
        index.add_chunks(rag.load_source_chunks(path, source_id, solver), embedder,
                         cancel_event=cancel_event, deadline=deadline)
    return publish(key, index)


@dataclass(frozen=True)
class PinnedKnowledgeIndex:
    index: rag.HybridIndex | None
    error: str | None = None


def pin_knowledge_index(*, cancel_event=None, deadline: float | None = None) -> PinnedKnowledgeIndex:
    try:
        if cancel_event is None and deadline is None:
            return PinnedKnowledgeIndex(default_index())
        pending = None

        def defer_publish(key, index):
            nonlocal pending
            pending = (key, index)

        def check_cancelled():
            rag.check_index_budget(cancel_event, deadline)

        pinned = PinnedKnowledgeIndex(default_index(
            cancel_event=cancel_event, deadline=deadline, _publish=defer_publish,
        ))
        if pending is None:
            check_cancelled()
        else:
            _publish_index(*pending, check_cancelled)
        return pinned
    except Exception as error:
        return PinnedKnowledgeIndex(None, f"{type(error).__name__}: {error}")


def _to_entry(chunk: rag.Chunk) -> KnowledgeEntry:
    return KnowledgeEntry(
        id=chunk.chunk_id,
        topic=chunk.topic,
        keywords=(),
        text=chunk.text,
        source_id=chunk.source_id,
        solver=chunk.solver,
        corpus_chunk=chunk.corpus_chunk,
        source_kind=chunk.source_kind,
        review_status=chunk.review_status,
    )


def search_knowledge_result(query: str, limit: int = 3,
                            solver: str | None = None, *,
                            pinned_index: PinnedKnowledgeIndex | None = None,
                            cancel_event=None, deadline: float | None = None) -> KnowledgeSearchResult:
    """Keep solver scope and distinguish absent material from retrieval failure."""
    from .corpus import mismatched_corpus_candidates, search_hybrid_with_plan
    from .query_language import normalize_solver, prepare_query

    requested = solver or os.environ.get("CAELAB_KNOWLEDGE_SOLVER", "") or infer_solver(query)
    resolved = infer_solver(requested) or normalize_solver(requested) or ""
    if not (query or "").strip():
        return KnowledgeSearchResult("no_material", [], resolved)
    try:
        plan = prepare_query(query, explicit_facets={"solver": resolved} if resolved else None)
        pinned = pinned_index if pinned_index is not None else pin_knowledge_index(cancel_event=cancel_event, deadline=deadline)
        index = pinned.index
        if index is None:
            return KnowledgeSearchResult("error", [], resolved, pinned.error)
        if index.corpus_revision is not None:
            embedder, reranker = rag.HashingEmbedder(), rag.LexicalReranker()
        else:
            embedder, reranker = rag.get_embedder(), rag.get_reranker()
        chunks = search_hybrid_with_plan(
            index,
            plan,
            embedder=embedder,
            reranker=reranker,
            limit=limit,
            solver=resolved or None,
            cancel_event=cancel_event,
            deadline=deadline,
        )
    except Exception as error:
        return KnowledgeSearchResult("error", [], resolved,
                                     f"{type(error).__name__}: {error}")
    return KnowledgeSearchResult(
        "ok" if chunks else "no_material", [_to_entry(c) for c in chunks], resolved,
        query_plan=plan, corpus_revision=index.corpus_revision,
        mismatched_candidates=tuple(mismatched_corpus_candidates(index, plan, resolved, None)) if not chunks else (),
        index_digest=index.index_digest,
    )


def search_knowledge(query: str, limit: int = 3, solver: str | None = None) -> list[KnowledgeEntry]:
    """List-returning compatibility API; retrieval errors are never silent."""
    result = search_knowledge_result(query, limit, solver)
    if result.status == "error":
        raise RuntimeError(result.error)
    return result.entries


def search_notes_keyword(query: str, limit: int = 3) -> list[KnowledgeEntry]:
    """The pre-RAG keyword search over the seven notes, kept for
    before/after comparison and as the offline fallback."""
    tokens = {t for t in re.split(r"[^0-9a-zA-Z가-힣*]+", (query or "").lower()) if t}
    scored: list[tuple[int, KnowledgeEntry]] = []
    for entry in KNOWLEDGE_PACK:
        score = 0
        haystacks = (entry.topic.lower(), " ".join(entry.keywords).lower(), entry.text.lower())
        for token in tokens:
            if any(token in h for h in haystacks):
                score += 2 if token in entry.keywords else 1
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [entry for _score, entry in scored[:limit]]


_CITATION = re.compile(r"\[근거:\s*([0-9a-zA-Z_-]+):([0-9a-zA-Z_.-]+)\]")


def citation_id(entry: KnowledgeEntry) -> str:
    return f"{entry.source_id}:{entry.id}"


def extract_citations(reply_text: str) -> list[str]:
    """Knowledge citations a reply makes, as full '<source>:<chunk>'."""
    return [f"{m.group(1)}:{m.group(2)}" for m in _CITATION.finditer(reply_text or "")]


def verify_citations(reply_text: str, allowed_ids: list[str] | tuple[str, ...]) -> list[str]:
    """Citations in the reply that were NOT among the entries actually
    shown to the expert. Design rule: only read material may be cited,
    so anything returned here is an unverified citation."""
    allowed = set(allowed_ids)
    return [c for c in extract_citations(reply_text) if c not in allowed]
