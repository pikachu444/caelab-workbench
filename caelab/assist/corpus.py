"""Offline verifiable CAE corpus manifest builder, atomic indexer, and cae-small-bm25-hash-v1 search."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Any

import numpy as np
import numpy.lib.format as npy_format

from ..artifact_refs import (
    ArtifactRefV1,
    ProducerIdentity,
    canonical_json_bytes,
    canonical_sha256,
    validate_relative_artifact_path,
)
from . import rag
from .contracts import (
    CORPUS_PROFILE_ID,
    CorpusChunkSpec,
    CorpusManifest,
    CorpusReceipt,
    CorpusSourceSpec,
    QueryPlan,
    RetrievalDecisionV1,
)
from .query_language import build_retrieval_decision, classify_non_cae_intent, editions_match, extract_query_requirements, normalize_solver, prepare_query

__all__ = [
    "build_corpus",
    "load_corpus_bundle",
    "load_corpus_manifest",
    "load_review_overlay",
    "search_corpus",
    "search_hybrid_with_plan",
]

_PRODUCER = ProducerIdentity("caelab.assist.corpus", "1.0")
_REDISTRIBUTABLE_VALUES = {True, "allowed", "notice_required"}


def _reject_non_finite(constant: str) -> Any:
    raise ValueError(f"Non-finite JSON constant rejected: {constant!r}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"Duplicate JSON key rejected: {key!r}")
        obj[key] = value
    return obj


def _load_strict_json(path: Path) -> tuple[Any, bytes]:
    raw = path.read_bytes()
    return _strict_json(raw, path), raw


def _strict_json(raw: bytes, path: Path) -> Any:
    text = raw.decode("utf-8")
    if "\x00" in text:
        raise ValueError(f"NUL byte rejected in JSON file: {path}")
    return json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_non_finite,
    )


def _write_bytes_fsync(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_json_fsync(path: Path, data: Mapping[str, Any]) -> Path:
    payload = canonical_json_bytes(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as stream:
            tmp = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        return path
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_cancel(check_cancelled: Callable[[], Any] | None) -> None:
    if check_cancelled is not None and check_cancelled() is True:
        raise RuntimeError("Corpus build cancelled.")


def load_corpus_manifest(manifest_path: str | Path) -> tuple[CorpusManifest, str]:
    """Load and strictly validate CorpusManifest JSON, returning (manifest, file_sha256)."""
    path = Path(manifest_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Corpus manifest not found: {path}")
    data, raw = _load_strict_json(path)
    manifest = CorpusManifest.from_dict(data)
    return manifest, _sha256_bytes(raw)


def load_review_overlay(overlay_path: str | Path, manifest: CorpusManifest) -> dict[str, Any]:
    """Load and validate review overlay JSON against manifest source SHA256s."""
    path = Path(overlay_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Review overlay not found: {path}")
    data, raw = _load_strict_json(path)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Review overlay must be a JSON object with schema_version == 1")
    allowed_top = {"schema_version", "reviews", "chunks", "notes"}
    extra_top = set(data) - allowed_top
    if extra_top:
        raise ValueError(f"Review overlay contains unexpected fields: {sorted(extra_top)}")

    by_id = {src.source_id: src for src in manifest.sources}
    reviews = data.get("reviews", [])
    if not isinstance(reviews, list):
        raise ValueError("Review overlay 'reviews' must be a list")
    validated_reviews: list[dict[str, Any]] = []
    seen_reviews = set()
    for idx, rev in enumerate(reviews):
        if not isinstance(rev, dict):
            raise ValueError(f"reviews[{idx}] must be an object")
        sid = rev.get("source_id")
        if sid not in by_id:
            raise ValueError(f"REVIEW_OVERLAY_UNKNOWN_SOURCE: reviews[{idx}].source_id={sid!r}")
        expected_sha = by_id[sid].sha256
        if rev.get("document_sha256") != expected_sha:
            raise ValueError(
                f"REVIEW_OVERLAY_HASH_MISMATCH: source {sid!r} expected {expected_sha}, got {rev.get('document_sha256')!r}"
            )
        if sid in seen_reviews:
            raise ValueError(f"Duplicate review overlay source: {sid!r}")
        seen_reviews.add(sid)
        status = rev.get("status", "unreviewed")
        if status not in ("approved", "partial", "rejected", "unreviewed"):
            raise ValueError(f"reviews[{idx}].status is invalid: {status!r}")
        validated_reviews.append(dict(rev, status=status))

    curated_chunks = data.get("chunks", [])
    if not isinstance(curated_chunks, list):
        raise ValueError("Review overlay 'chunks' must be a list")
    for idx, chk in enumerate(curated_chunks):
        if not isinstance(chk, dict):
            raise ValueError(f"chunks[{idx}] must be an object")
        sid = chk.get("source_id")
        if sid not in by_id:
            raise ValueError(f"REVIEW_OVERLAY_UNKNOWN_SOURCE: chunks[{idx}].source_id={sid!r}")
        if "document_sha256" in chk and chk["document_sha256"] != by_id[sid].sha256:
            raise ValueError(
                f"REVIEW_OVERLAY_HASH_MISMATCH: chunks[{idx}] source {sid!r} hash mismatch"
            )
        ls = chk.get("line_start")
        le = chk.get("line_end")
        if type(ls) is not int or type(le) is not int or ls < 1 or le < ls:
            raise ValueError(f"chunks[{idx}] requires valid 1-based inclusive line_start <= line_end")
        if not isinstance(chk.get("chunk_id"), str) or not chk["chunk_id"].strip():
            raise ValueError(f"chunks[{idx}].chunk_id must be a nonempty string")
        sp = chk.get("section_path")
        if not isinstance(sp, list) or not sp or any(not isinstance(x, str) or not x.strip() for x in sp):
            raise ValueError(f"chunks[{idx}].section_path must be a nonempty list of strings")

    return {
        "schema_version": 1,
        "overlay_sha256": _sha256_bytes(raw),
        "reviews": validated_reviews,
        "chunks": curated_chunks,
        "notes": data.get("notes", ""),
    }


def _verify_bytes(raw: bytes, identifier: str, sha256: str, byte_length: int) -> None:
    if len(raw) != byte_length:
        raise ValueError(f"BYTE_LENGTH_MISMATCH: {identifier} expected {byte_length} bytes, got {len(raw)}")
    actual_sha = _sha256_bytes(raw)
    if actual_sha != sha256:
        raise ValueError(f"SHA256_MISMATCH: {identifier} expected {sha256}, got {actual_sha}")


def _read_asset(root: Path, relative: str) -> bytes:
    return (root / validate_relative_artifact_path(relative, root=root)).read_bytes()


def _verify_source_bytes(source: CorpusSourceSpec, source_root: Path, embedded: bytes | None) -> bytes | None:
    if source.local_relative_path is None:
        if source.extraction_quality == "source_unavailable":
            return None
        raise ValueError(f"SOURCE_UNAVAILABLE: {source.source_id} has no local_relative_path")
    raw = embedded if embedded is not None else _read_asset(source_root, source.local_relative_path)
    _verify_bytes(raw, source.source_id, source.sha256, source.byte_length)
    return raw


def _source_notices(source: CorpusSourceSpec, root: Path, embedded: Mapping[str, bytes]) -> dict[str, bytes]:
    license_data = source.license
    required = license_data["redistribution"] == "notice_required" or license_data.get("notice_required")
    notices = license_data.get("notice_files", ())
    if required and not notices:
        parent = PurePosixPath(source.local_relative_path).parent
        notices = tuple(
            (parent / name).as_posix()
            for name in ("LICENSE", "LICENSE.txt", "COPYING", "NOTICE", "NOTICE.txt")
            if (root / parent / name).is_file()
        )
    if required and not notices:
        raise ValueError(f"LICENSE_NOTICE_MISSING: {source.source_id}")
    verified = {}
    for item in notices:
        rel = item if isinstance(item, str) else item["path"]
        raw = embedded[rel] if rel in embedded else _read_asset(root, rel)
        if not raw.strip():
            raise ValueError(f"LICENSE_NOTICE_EMPTY: {rel}")
        if not isinstance(item, str):
            _verify_bytes(raw, rel, item["sha256"], item["byte_length"])
        # Recovery bundle paths already include the licenses directory.
        staged = rel if rel.startswith("licenses/") else f"licenses/{rel}"
        verified[staged] = raw
    return verified


def _recovery_materials(data, manifest):
    if "manifest" not in data:
        return {}, {}, {}
    embedded_sources = {}
    embedded_notices = {}
    chunks_by_source = {}
    sources = {source.source_id: source for source in manifest.sources}
    for item in data["sources"]:
        source = sources.get(item["source_id"])
        if source is None or source.source_id in embedded_sources:
            raise ValueError("Missing or duplicate recovery source manifest entry")
        raw = item["text"].encode("utf-8")
        if (item["sha256"], item["byte_length"], item["commit"], item["source_kind"]) != (
            source.sha256, source.byte_length, source.commit, source.source_kind
        ):
            raise ValueError(f"Recovery source metadata mismatch: {source.source_id}")
        _verify_bytes(raw, source.source_id, source.sha256, source.byte_length)
        embedded_sources[source.source_id] = raw
    if set(embedded_sources) != set(sources):
        raise ValueError("Recovery source bytes are incomplete")
    for item in data["licenses"]:
        rel = f"licenses/{item['id']}/{item['path']}"
        validate_relative_artifact_path(rel)
        if rel in embedded_notices:
            raise ValueError(f"Duplicate recovery notice: {rel}")
        raw = item["text"].encode("utf-8")
        _verify_bytes(raw, rel, item["sha256"], item["byte_length"])
        embedded_notices[rel] = raw
    expected_notices = {notice["path"] for source in manifest.sources for notice in source.license["notice_files"]}
    if not expected_notices <= embedded_notices.keys():
        raise ValueError("Recovery license notice bytes are incomplete")
    for item in data["chunks"]:
        chunk = dict(item)
        chunk.pop("extraction_quality", None)
        span = chunk.pop("span")
        if set(span) != {"line_start", "line_end", "char_start", "char_end"}:
            raise ValueError("Invalid recovery chunk span")
        chunk.update(span)
        chunk["coverage_facets"] = {**chunk["coverage_facets"], "solver": chunk["coverage_facets"]["solver_ids"]}
        spec = CorpusChunkSpec.from_dict(chunk)
        source = sources.get(spec.source_id)
        if source is None or spec.document_sha256 != source.sha256 or spec.edition != source.edition:
            raise ValueError(f"Recovery chunk provenance mismatch: {spec.citation}")
        chunks_by_source.setdefault(spec.source_id, []).append(chunk)
    return embedded_sources, embedded_notices, chunks_by_source


def _line_spans(text: str) -> tuple[list[str], list[int], list[int]]:
    lines = text.splitlines(keepends=True)
    starts: list[int] = []
    ends: list[int] = []
    pos = 0
    for line in lines:
        starts.append(pos)
        pos += len(line)
        ends.append(pos)
    return lines, starts, ends


def _extract_curated_chunk(
    source: CorpusSourceSpec,
    text: str,
    lines: list[str],
    line_starts: list[int],
    line_ends: list[int],
    spec: Mapping[str, Any],
) -> CorpusChunkSpec:
    ls: int = spec["line_start"]
    le: int = spec["line_end"]
    if le > len(lines):
        raise ValueError(
            f"SPAN_OUT_OF_RANGE: {source.source_id}:{spec['chunk_id']} line_end {le} > {len(lines)}"
        )
    cs = line_starts[ls - 1]
    ce = line_ends[le - 1]
    chunk_text = text[cs:ce]
    if not chunk_text.strip():
        raise ValueError(f"EMPTY_CHUNK: {source.source_id}:{spec['chunk_id']} has empty text")
    if any(key in spec and spec[key] != value for key, value in (
        ("char_start", cs), ("char_end", ce), ("text", chunk_text),
        ("text_sha256", _sha256_bytes(chunk_text.encode("utf-8"))),
    )):
        raise ValueError(f"CURATED_SPAN_MISMATCH: {source.source_id}:{spec['chunk_id']}")
    facets = dict(spec.get("coverage_facets") or {})
    facets.setdefault("solver", list(source.solver_scope))
    return CorpusChunkSpec(
        source_id=source.source_id,
        chunk_id=spec["chunk_id"],
        document_sha256=source.sha256,
        text=chunk_text,
        text_sha256=_sha256_bytes(chunk_text.encode("utf-8")),
        edition=source.edition,
        section_path=tuple(spec["section_path"]),
        line_start=ls,
        line_end=le,
        char_start=cs,
        char_end=ce,
        page_start=spec.get("page_start"),
        page_end=spec.get("page_end"),
        coverage_facets=facets,
    )


def _chunk_text_with_spans(
    source: CorpusSourceSpec,
    raw_bytes: bytes,
    curated: Sequence[Mapping[str, Any]],
) -> list[CorpusChunkSpec]:
    ext = Path(source.local_relative_path or "").suffix.lower()
    if ext == ".pdf":
        return _chunk_pdf_with_spans(source, raw_bytes, curated)

    text = raw_bytes.decode("utf-8")
    if "\x00" in text:
        raise ValueError(f"NUL byte rejected in source file: {source.source_id}")
    lines, line_starts, line_ends = _line_spans(text)
    if not lines:
        return []

    if curated:
        return [
            _extract_curated_chunk(source, text, lines, line_starts, line_ends, item)
            for item in curated
        ]

    # Automatic line-exact section chunking preserving 1-based inclusive line and 0-based char spans.
    sections: list[tuple[tuple[str, ...], int, int]] = []
    stack: list[str] = []
    current_start = 1
    current_path: tuple[str, ...] = (source.title,)
    is_markdown = ext in {".md", ".markdown", ".txt"}

    for idx, line in enumerate(lines, 1):
        stripped = line.rstrip("\r\n")
        if is_markdown:
            m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            if m:
                if idx > current_start and "".join(lines[current_start - 1 : idx - 1]).strip():
                    sections.append((current_path, current_start, idx - 1))
                level = len(m.group(1))
                stack = stack[: level - 1] + [m.group(2).strip()]
                current_path = tuple(stack) or (source.title,)
                current_start = idx + 1
    if current_start <= len(lines) and "".join(lines[current_start - 1 :]).strip():
        sections.append((current_path, current_start, len(lines)))
    if not sections and text.strip():
        sections.append(((source.title,), 1, len(lines)))

    chunks: list[CorpusChunkSpec] = []
    for sec_path, s_line, e_line in sections:
        window_start = s_line
        while window_start <= e_line:
            char_acc = 0
            window_end = window_start
            while window_end <= e_line:
                char_acc += len(lines[window_end - 1])
                if char_acc >= rag.CHUNK_CHARS and window_end > window_start:
                    break
                window_end += 1
            window_end = min(window_end, e_line)
            cs = line_starts[window_start - 1]
            ce = line_ends[window_end - 1]
            piece = text[cs:ce]
            if piece.strip():
                slug = rag._slug(sec_path[-1])
                chunks.append(
                    CorpusChunkSpec(
                        source_id=source.source_id,
                        chunk_id=f"{slug}-{len(chunks):04d}",
                        document_sha256=source.sha256,
                        text=piece,
                        text_sha256=_sha256_bytes(piece.encode("utf-8")),
                        edition=source.edition,
                        section_path=sec_path,
                        line_start=window_start,
                        line_end=window_end,
                        char_start=cs,
                        char_end=ce,
                        page_start=None,
                        page_end=None,
                        coverage_facets={"solver": list(source.solver_scope)},
                    )
                )
            window_start = window_end + 1
    return chunks


def _chunk_pdf_with_spans(
    source: CorpusSourceSpec,
    raw_bytes: bytes,
    curated: Sequence[Mapping[str, Any]],
) -> list[CorpusChunkSpec]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw_bytes))
    page_texts: list[str] = []
    for page in reader.pages:
        txt = rag._clean_pdf_text(page.extract_text() or "")
        if txt and not txt.endswith("\n"):
            txt += "\n"
        page_texts.append(txt)

    flat: list[tuple[str, int, int]] = []

    def walk(items: Any, depth: int) -> None:
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
        flat = [(source.title, 0, 0)]
    flat.sort(key=lambda row: row[1])

    full_text = "".join(page_texts)
    if not full_text.strip():
        return []
    lines, line_starts, line_ends = _line_spans(full_text)

    if curated:
        return [
            _extract_curated_chunk(source, full_text, lines, line_starts, line_ends, item)
            for item in curated
        ]

    # Map each page to 1-based line ranges and heading path
    chunks: list[CorpusChunkSpec] = []
    line_cursor = 1
    stack: list[str] = []
    sec_idx = 0
    current_path: tuple[str, ...] = (flat[0][0],)
    for page_idx, ptext in enumerate(page_texts):
        while sec_idx + 1 < len(flat) and flat[sec_idx + 1][1] <= page_idx:
            sec_idx += 1
            title, _p, depth = flat[sec_idx]
            stack = stack[:depth] + [title]
            current_path = tuple(stack) or (source.title,)
        plines = ptext.splitlines(keepends=True)
        if not plines:
            continue
        ls = line_cursor
        le = line_cursor + len(plines) - 1
        line_cursor = le + 1
        cs = line_starts[ls - 1]
        ce = line_ends[le - 1]
        piece = full_text[cs:ce]
        if piece.strip():
            slug = rag._slug(current_path[-1])
            chunks.append(
                CorpusChunkSpec(
                    source_id=source.source_id,
                    chunk_id=f"{slug}-p{page_idx + 1:03d}-{len(chunks):04d}",
                    document_sha256=source.sha256,
                    text=piece,
                    text_sha256=_sha256_bytes(piece.encode("utf-8")),
                    edition=source.edition,
                    section_path=current_path,
                    line_start=ls,
                    line_end=le,
                    char_start=cs,
                    char_end=ce,
                    page_start=page_idx + 1,
                    page_end=page_idx + 1,
                    coverage_facets={"solver": list(source.solver_scope)},
                )
            )
    return chunks


def _validate_vectors(vectors: np.ndarray, count: int) -> np.ndarray:
    if not isinstance(vectors, np.ndarray):
        raise ValueError("Vectors must be a numpy ndarray")
    if vectors.dtype != np.float32:
        raise ValueError(f"Vector dtype must be float32, got {vectors.dtype}")
    if vectors.shape != (count, rag.HashingEmbedder.DIM):
        raise ValueError(f"Vector shape mismatch: expected ({count}, {rag.HashingEmbedder.DIM}), got {vectors.shape}")
    if not np.isfinite(vectors).all():
        raise ValueError("Vectors contain non-finite entries (NaN/Inf)")
    return np.ascontiguousarray(vectors, dtype=np.float32)


def _dir_snapshot(directory: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            snapshot[p.relative_to(directory).as_posix()] = p.read_bytes()
    return snapshot


def build_corpus(
    manifest_path: str | Path,
    *,
    output_root: str | Path,
    review_overlay_path: str | Path | None = None,
    verify_only: bool = False,
    check_cancelled: Callable[[], None] | None = None,
) -> CorpusReceipt:
    """Validate sources & reviews and atomically build/verify a cae-small-bm25-hash-v1 corpus index."""
    _check_cancel(check_cancelled)
    m_path = Path(manifest_path).expanduser().resolve()
    data, _ = _load_strict_json(m_path)
    manifest = CorpusManifest.from_dict(data)
    manifest_bytes = canonical_json_bytes(manifest.to_dict())
    manifest_sha = _sha256_bytes(manifest_bytes)
    embedded_sources, embedded_notices, curated_by_source = _recovery_materials(data, manifest)
    source_root = m_path.parent
    out_root = Path(output_root).expanduser().resolve()

    overlay_data = load_review_overlay(review_overlay_path, manifest) if review_overlay_path is not None else None
    _check_cancel(check_cancelled)

    source_statuses: dict[str, str] = {}
    verified_bytes: dict[str, bytes] = {}
    notices_to_stage: dict[str, bytes] = {}
    warnings: list[str] = []
    reviews = {item["source_id"]: item["status"] for item in (overlay_data or {}).get("reviews", ())}
    for chunk in (overlay_data or {}).get("chunks", ()):
        if chunk["source_id"] in curated_by_source and embedded_sources:
            raise ValueError("Review overlays cannot replace frozen recovery spans")
        curated_by_source.setdefault(chunk["source_id"], []).append(chunk)

    chunks: list[CorpusChunkSpec] = []
    seen_citations = set()
    for src in manifest.sources:
        _check_cancel(check_cancelled)
        review_status = reviews.get(src.source_id, src.review_status or "unreviewed")
        if "rejected" in (review_status, src.review_status):
            source_statuses[src.source_id] = "rejected"
            warnings.append(f"source_rejected:{src.source_id}")
            continue
        raw = _verify_source_bytes(src, source_root, embedded_sources.get(src.source_id))
        if raw is None:
            source_statuses[src.source_id] = "source_unavailable"
            warnings.append(f"source_unavailable:{src.source_id}")
            continue
        verified_bytes[src.source_id] = raw
        notices_to_stage.update(_source_notices(src, source_root, embedded_notices))
        extracted = _chunk_text_with_spans(src, raw, curated_by_source.get(src.source_id, ()))
        if not extracted:
            raise ValueError(f"NO_EXTRACTABLE_CHUNKS: {src.source_id}")
        for chunk in extracted:
            if chunk.citation in seen_citations:
                raise ValueError(f"Duplicate corpus chunk: {chunk.citation}")
            seen_citations.add(chunk.citation)
            scope = _chunk_solver_scope(chunk)
            if not set(scope) <= set(src.solver_scope):
                raise ValueError(f"Chunk solver scope exceeds source scope: {chunk.citation}")
        chunks.extend(extracted)
        source_statuses[src.source_id] = review_status
        if review_status != "approved":
            warnings.append(f"source_review_{review_status}:{src.source_id}")

    embedder = rag.HashingEmbedder()
    reranker = rag.LexicalReranker()

    if verify_only:
        return CorpusReceipt(
            corpus_id=manifest.corpus_id,
            revision=manifest.revision,
            manifest_sha256=manifest_sha,
            verify_only=True,
            status="verified",
            profile_id=CORPUS_PROFILE_ID,
            embedder_id=embedder.id,
            reranker_id=reranker.id,
            chunk_count=len(chunks),
            source_count=len(manifest.sources),
            index_digest=None,
            index_dir=None,
            current_pointer_path=None,
            source_statuses=source_statuses,
            warnings=tuple(warnings),
        )

    _check_cancel(check_cancelled)
    # Build embeddings with cae-small-bm25-hash-v1 (hashing512 float32).
    if chunks:
        embed_inputs = [f"{' > '.join(c.section_path)}\n{c.text[:rag.EMBED_TEXT_CHARS]}" for c in chunks]
        vectors = _validate_vectors(embedder.embed(embed_inputs), len(chunks))
    else:
        vectors = np.zeros((0, rag.HashingEmbedder.DIM), dtype=np.float32)

    source_by_id = {src.source_id: src for src in manifest.sources}
    chunks_payload = {
        "schema_version": 1,
        "profile_id": CORPUS_PROFILE_ID,
        "corpus_id": manifest.corpus_id,
        "corpus_revision": manifest.revision,
        "manifest_sha256": manifest_sha,
        "embedder_id": embedder.id,
        "reranker_id": reranker.id,
        "bm25": {"k1": 1.5, "b": 0.75},
        "rrf_k": rag.RRF_K,
        "vector_dim": rag.HashingEmbedder.DIM,
        "vector_dtype": "float32",
        "chunks": [
            {
                **chunk.to_dict(),
                "heading_path": list(chunk.section_path),
                "solver_scope": list(_chunk_solver_scope(chunk)),
                "review_status": source_statuses[chunk.source_id],
                "source_kind": source_by_id[chunk.source_id].source_kind,
            }
            for chunk in chunks
        ],
    }

    vec_buf = io.BytesIO()
    npy_format.write_array(vec_buf, vectors, version=(2, 0), allow_pickle=False)
    vectors_bytes = vec_buf.getvalue()
    chunks_bytes = canonical_json_bytes(chunks_payload)
    reviews_bytes = canonical_json_bytes(overlay_data) if overlay_data is not None else None

    files_to_stage: dict[str, bytes] = {
        "manifest.json": manifest_bytes,
        "chunks.json": chunks_bytes,
        "vectors.npy": vectors_bytes,
        **notices_to_stage,
    }
    if reviews_bytes is not None:
        files_to_stage["reviews.json"] = reviews_bytes
    for src in manifest.sources:
        raw = verified_bytes.get(src.source_id)
        if raw is None:
            continue
        if src.license["redistribution"] in _REDISTRIBUTABLE_VALUES and src.local_relative_path:
            files_to_stage[f"sources/{src.local_relative_path}"] = raw

    index_digest = _index_digest(manifest, files_to_stage)

    indexes_dir = out_root / "indexes"
    indexes_dir.mkdir(parents=True, exist_ok=True)
    target_dir = indexes_dir / index_digest
    tmp_dir = Path(tempfile.mkdtemp(prefix=".tmp-corpus-", dir=indexes_dir))
    try:
        for rel, payload in files_to_stage.items():
            _check_cancel(check_cancelled)
            rel_ok = validate_relative_artifact_path(rel, root=tmp_dir)
            dest = tmp_dir / PurePosixPath(rel_ok)
            _write_bytes_fsync(dest, payload)

        _check_cancel(check_cancelled)
        if _index_digest(manifest, _dir_snapshot(tmp_dir)) != index_digest:
            raise ValueError("STAGED_HASH_MISMATCH: corpus index")
        status_out = "built"
        if target_dir.exists():
            if not target_dir.is_dir():
                raise ValueError(f"Index destination is not a directory: {target_dir}")
            existing_entries = list(target_dir.iterdir())
            if existing_entries:
                existing_snap = _dir_snapshot(target_dir)
                staged_snap = _dir_snapshot(tmp_dir)
                if existing_snap != staged_snap:
                    raise ValueError(
                        f"IMMUTABLE_INDEX_MISMATCH: existing non-empty index {target_dir} differs byte-for-byte"
                    )
                status_out = "reused"
            else:
                os.replace(tmp_dir, target_dir)
        else:
            os.replace(tmp_dir, target_dir)

        _check_cancel(check_cancelled)
        pointer_payload = {
            "schema_version": 1,
            "corpus_id": manifest.corpus_id,
            "revision": manifest.revision,
            "index_digest": index_digest,
            "index_relative_path": f"indexes/{index_digest}",
            "manifest_sha256": manifest_sha,
            "profile_id": CORPUS_PROFILE_ID,
            "embedder_id": embedder.id,
            "reranker_id": reranker.id,
            "chunk_count": len(chunks),
        }
        pointer_path = out_root / "current.json"
        _atomic_json_fsync(pointer_path, pointer_payload)
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return CorpusReceipt(
        corpus_id=manifest.corpus_id,
        revision=manifest.revision,
        manifest_sha256=manifest_sha,
        verify_only=False,
        status=status_out,
        profile_id=CORPUS_PROFILE_ID,
        embedder_id=embedder.id,
        reranker_id=reranker.id,
        chunk_count=len(chunks),
        source_count=len(manifest.sources),
        index_digest=index_digest,
        index_dir=str(target_dir),
        current_pointer_path=str(out_root / "current.json"),
        source_statuses=source_statuses,
        warnings=tuple(warnings),
    )


def _index_digest(manifest, files):
    return canonical_sha256({
        "corpus_id": manifest.corpus_id,
        "revision": manifest.revision,
        "profile_id": CORPUS_PROFILE_ID,
        "files": {rel: _sha256_bytes(raw) for rel, raw in files.items()},
    })


def _chunk_solver_scope(chunk: CorpusChunkSpec) -> tuple[str, ...]:
    scope = chunk.coverage_facets.get("solver", ())
    if not isinstance(scope, tuple) or any(not isinstance(solver, str) or not solver for solver in scope):
        raise ValueError(f"Invalid chunk solver scope: {chunk.citation}")
    return scope


def load_corpus_bundle(root_or_index_dir: str | Path) -> tuple[rag.HybridIndex, dict[str, Any]]:
    """Verify every file from a single published index and load that same snapshot."""
    base = Path(root_or_index_dir).expanduser().resolve()
    index_dir = base
    pointer_file = base / "current.json"
    pointer = None
    if pointer_file.is_file():
        pointer, _ = _load_strict_json(pointer_file)
        if not isinstance(pointer, dict) or pointer.get("schema_version") != 1:
            raise ValueError(f"Invalid corpus current.json pointer: {pointer_file}")
        digest = pointer.get("index_digest")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid corpus index_digest in current.json")
        if pointer.get("index_relative_path") != f"indexes/{digest}":
            raise ValueError("Corpus pointer path does not match index_digest")
        index_dir = base / validate_relative_artifact_path(pointer["index_relative_path"], root=base)

    files = _dir_snapshot(index_dir)
    for filename in ("chunks.json", "manifest.json", "vectors.npy"):
        if filename not in files:
            raise FileNotFoundError(f"Corpus {filename} missing: {index_dir}")
    manifest = CorpusManifest.from_dict(_strict_json(files["manifest.json"], index_dir / "manifest.json"))
    digest = _index_digest(manifest, files)
    if index_dir.name != digest or (pointer is not None and pointer["index_digest"] != digest):
        raise ValueError("INDEX_DIGEST_MISMATCH: corpus index files differ from their published digest")

    meta = _strict_json(files["chunks.json"], index_dir / "chunks.json")
    if not isinstance(meta, dict) or type(meta.get("schema_version")) is not int or meta["schema_version"] != 1:
        raise ValueError("Invalid corpus chunks.json schema")
    embedder_id = rag.HashingEmbedder().id
    reranker_id = rag.LexicalReranker().id
    manifest_sha = _sha256_bytes(files["manifest.json"])
    profile = {
        "corpus_id": manifest.corpus_id, "profile_id": CORPUS_PROFILE_ID, "embedder_id": embedder_id,
        "reranker_id": reranker_id, "manifest_sha256": manifest_sha,
    }
    expected_meta = dict(profile, corpus_revision=manifest.revision, rrf_k=rag.RRF_K,
                         bm25={"k1": 1.5, "b": 0.75}, vector_dim=rag.HashingEmbedder.DIM, vector_dtype="float32")
    if any(meta.get(key) != value for key, value in expected_meta.items()) or not isinstance(meta.get("chunks"), list):
        raise ValueError("Corpus chunks.json metadata does not match manifest or retrieval profile")
    if pointer is not None:
        expected_pointer = dict(profile, revision=manifest.revision, chunk_count=len(meta["chunks"]))
        if any(pointer.get(key) != value for key, value in expected_pointer.items()):
            raise ValueError("Corpus pointer metadata does not match the verified index")

    vectors = _validate_vectors(np.load(io.BytesIO(files["vectors.npy"]), allow_pickle=False), len(meta["chunks"]))
    sources = {source.source_id: source for source in manifest.sources}
    chunks = []
    citations = set()
    for raw in meta["chunks"]:
        spec = CorpusChunkSpec.from_dict({
            key: value for key, value in raw.items()
            if key not in {"heading_path", "solver", "solver_scope", "source_kind", "review_status"}
        })
        scope = _chunk_solver_scope(spec)
        source = sources.get(spec.source_id)
        if (source is None or spec.document_sha256 != source.sha256 or spec.edition != source.edition
                or tuple(raw["solver_scope"]) != scope or not set(scope) <= set(source.solver_scope)
                or spec.char_end - spec.char_start != len(spec.text) or spec.citation in citations):
            raise ValueError(f"Invalid corpus chunk provenance: {spec.citation}")
        citations.add(spec.citation)
        chunks.append(rag.Chunk(
            chunk_id=spec.chunk_id, source_id=spec.source_id, heading_path=spec.section_path, text=spec.text,
            solver=scope[0] if len(scope) == 1 else "", solver_scope=scope, corpus_chunk=spec,
        ))
    index = rag.HybridIndex(chunks=chunks, embedder_id=embedder_id, vectors=vectors,
                            corpus_revision=manifest.revision, index_digest=digest)
    return index, dict(meta, index_digest=digest)


def search_hybrid_with_plan(
    index: rag.HybridIndex,
    plan: QueryPlan,
    *,
    embedder: rag.Embedder,
    reranker: rag.Reranker,
    limit: int = 5,
    candidate_count: int = rag.RERANK_CANDIDATES,
    solver: str | None = None,
    edition: str | None = None,
) -> list[rag.Chunk]:
    """Run cae-small-bm25-hash-v1 multi-query top-20 BM25 + vector RRF(k=60) + lexical rerank."""
    if not index.chunks or classify_non_cae_intent(plan)[0]:
        return []
    if index.embedder_id and embedder.id != index.embedder_id:
        raise ValueError(
            f"Query embedder {embedder.id!r} does not match index embedder {index.embedder_id!r}"
        )
    if index.vectors is not None:
        if index.vectors.ndim != 2 or index.vectors.shape[0] != len(index.chunks):
            raise ValueError("Index vector matrix shape mismatch")
        if not np.isfinite(index.vectors).all():
            raise ValueError("Index vectors contain non-finite entries")
        if embedder.id == "hashing-fallback" and (
            index.vectors.dtype != np.float32 or index.vectors.shape[1] != rag.HashingEmbedder.DIM
        ):
            raise ValueError("Hashing fallback index requires float32 512-dim vectors")

    pool = [
        i for i, chunk in enumerate(index.chunks)
        if _matches_scope(chunk, solver, edition)
    ]
    if not pool:
        return []

    query_texts = list(dict.fromkeys([plan.original_text, *(q.text for q in plan.queries)]))
    fused_scores: dict[int, float] = {}

    for qtext in query_texts:
        bm25_scores = index.bm25.scores(rag.tokenize(qtext))
        bm25_order = [i for i in sorted(pool, key=lambda i: (-bm25_scores[i], i)) if bm25_scores[i] > 0][:candidate_count]
        for rank, idx in enumerate(bm25_order):
            fused_scores[idx] = fused_scores.get(idx, 0.0) + 1.0 / (rag.RRF_K + rank + 1)

        if index.vectors is not None:
            qvec = np.asarray(embedder.embed_query(qtext), dtype=np.float32)
            if qvec.ndim != 1 or qvec.shape[0] != index.vectors.shape[1]:
                raise ValueError(
                    f"Query embedding dimension {qvec.shape} mismatches index dimension {index.vectors.shape[1]}"
                )
            if not np.isfinite(qvec).all():
                raise ValueError("Query embedding contains non-finite entries")
            vec_scores = index.vectors @ qvec
            vec_order = [i for i in sorted(pool, key=lambda i: (-vec_scores[i], i)) if vec_scores[i] > 0][:candidate_count]
            for rank, idx in enumerate(vec_order):
                fused_scores[idx] = fused_scores.get(idx, 0.0) + 1.0 / (rag.RRF_K + rank + 1)

    if fused_scores:
        top_indices = sorted(fused_scores, key=lambda i: (-fused_scores[i], i))[:candidate_count]
    else:
        top_indices = pool[:candidate_count]
    candidates = [index.chunks[i] for i in top_indices]
    if reranker is not None and candidates:
        combined_query = " ".join(query_texts)
        scores = reranker.rerank(combined_query, candidates)
        candidates = [c for _s, c in sorted(zip(scores, candidates), key=lambda p: -p[0])]
    return candidates[:limit]


def search_corpus(
    root_or_index_dir: str | Path,
    question: str,
    *,
    limit: int = 5,
    solver: str | None = None,
    edition: str | None = None,
    query_language: str = "auto",
    explicit_facets: Mapping[str, Any] | None = None,
    source_review_refs: Sequence[ArtifactRefV1 | Mapping[str, Any]] = (),
) -> tuple[QueryPlan, list[CorpusChunkSpec], RetrievalDecisionV1]:
    """Search a built corpus index using QueryPlan + cae-small-bm25-hash-v1 and return RetrievalDecisionV1."""
    facets = dict(explicit_facets or {})
    if solver and "solver" not in facets:
        facets["solver"] = solver
    if edition and "edition" not in facets:
        facets["edition"] = edition

    plan = prepare_query(question, query_language=query_language, explicit_facets=facets or None)
    index, _ = load_corpus_bundle(root_or_index_dir)
    requirements = {req.facet: req.expected for req in extract_query_requirements(plan, facets)}
    target_solver = normalize_solver(requirements.get("solver"))
    target_edition = requirements.get("edition")
    hits = search_hybrid_with_plan(
        index, plan, embedder=rag.HashingEmbedder(), reranker=rag.LexicalReranker(),
        limit=limit, solver=target_solver, edition=target_edition,
    )
    matched_specs = [chunk.corpus_chunk for chunk in hits]
    # Keep mismatches in the decision only, where their source tags explain the empty results.
    decision_candidates = matched_specs or mismatched_corpus_candidates(index, target_solver, target_edition)
    decision = build_retrieval_decision(
        plan, decision_candidates, source_review_refs=source_review_refs, explicit_facets=facets or None,
    )
    return plan, matched_specs, decision


def _matches_scope(chunk: rag.Chunk, solver: str | None, edition: str | None) -> bool:
    return (
        (solver in (None, "", "general") or chunk.matches_solver(solver))
        and (not edition or chunk.corpus_chunk is None or editions_match(edition, chunk.corpus_chunk.edition))
    )


def mismatched_corpus_candidates(
    index: rag.HybridIndex, solver: str | None, edition: str | None,
) -> list[CorpusChunkSpec]:
    if any(_matches_scope(chunk, solver, edition) for chunk in index.chunks):
        return []
    return [chunk.corpus_chunk for chunk in index.chunks if chunk.corpus_chunk is not None]
