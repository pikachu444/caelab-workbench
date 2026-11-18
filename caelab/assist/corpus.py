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
from .query_language import build_retrieval_decision, prepare_query

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
    text = raw.decode("utf-8")
    if "\x00" in text:
        raise ValueError(f"NUL byte rejected in JSON file: {path}")
    data = json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_non_finite,
    )
    return data, raw


def _write_bytes_fsync(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(payload).hexdigest()


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
        status = rev.get("status", "approved")
        if status not in {"approved", "partial", "rejected"}:
            raise ValueError(f"reviews[{idx}].status must be approved|partial|rejected, got {status!r}")
        validated_reviews.append(dict(rev))

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


def _verify_source_bytes(source: CorpusSourceSpec, source_root: Path) -> tuple[str, bytes | None]:
    if source.local_relative_path is None:
        if source.extraction_quality == "source_unavailable":
            return "source_unavailable", None
        raise ValueError(f"SOURCE_UNAVAILABLE: {source.source_id} has no local_relative_path")
    rel = validate_relative_artifact_path(source.local_relative_path, root=source_root)
    candidate = source_root.resolve() / PurePosixPath(rel)
    if not candidate.is_file():
        raise FileNotFoundError(f"SOURCE_UNAVAILABLE: {source.source_id} missing at {rel}")
    raw = candidate.read_bytes()
    if len(raw) != source.byte_length:
        raise ValueError(
            f"BYTE_LENGTH_MISMATCH: {source.source_id} expected {source.byte_length} bytes, got {len(raw)}"
        )
    actual_sha = _sha256_bytes(raw)
    if actual_sha != source.sha256:
        raise ValueError(
            f"SHA256_MISMATCH: {source.source_id} expected {source.sha256}, got {actual_sha}"
        )
    return "available", raw


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
    manifest, manifest_sha = load_corpus_manifest(m_path)
    source_root = m_path.parent
    out_root = Path(output_root).expanduser().resolve()

    overlay_data = load_review_overlay(review_overlay_path, manifest) if review_overlay_path is not None else None
    _check_cancel(check_cancelled)

    source_statuses: dict[str, str] = {}
    verified_bytes: dict[str, bytes] = {}
    warnings: list[str] = []

    for src in manifest.sources:
        _check_cancel(check_cancelled)
        status_label, raw = _verify_source_bytes(src, source_root)
        source_statuses[src.source_id] = status_label
        if raw is not None:
            verified_bytes[src.source_id] = raw
        else:
            warnings.append(f"source_unavailable:{src.source_id}")

    curated_by_source: dict[str, list[dict[str, Any]]] = {}
    if overlay_data is not None:
        for chk in overlay_data["chunks"]:
            curated_by_source.setdefault(chk["source_id"], []).append(chk)

    chunks: list[CorpusChunkSpec] = []
    for src in manifest.sources:
        _check_cancel(check_cancelled)
        raw = verified_bytes.get(src.source_id)
        if raw is None:
            continue
        chunks.extend(_chunk_text_with_spans(src, raw, curated_by_source.get(src.source_id, ())))

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
                "solver": (source_by_id[chunk.source_id].solver_scope[0]
                           if len(source_by_id[chunk.source_id].solver_scope) == 1
                           else ""),
                "solver_scope": list(source_by_id[chunk.source_id].solver_scope),
                "source_kind": source_by_id[chunk.source_id].source_kind,
            }
            for chunk in chunks
        ],
    }

    vec_buf = io.BytesIO()
    npy_format.write_array(vec_buf, vectors, version=(2, 0), allow_pickle=False)
    vectors_bytes = vec_buf.getvalue()
    manifest_bytes = canonical_json_bytes(manifest.to_dict())
    chunks_bytes = canonical_json_bytes(chunks_payload)
    reviews_bytes = canonical_json_bytes(overlay_data) if overlay_data is not None else None

    files_to_stage: dict[str, bytes] = {
        "manifest.json": manifest_bytes,
        "chunks.json": chunks_bytes,
        "vectors.npy": vectors_bytes,
    }
    if reviews_bytes is not None:
        files_to_stage["reviews.json"] = reviews_bytes
    for src in manifest.sources:
        raw = verified_bytes.get(src.source_id)
        if raw is None:
            continue
        if src.license["redistribution"] in _REDISTRIBUTABLE_VALUES and src.local_relative_path:
            files_to_stage[f"sources/{src.local_relative_path}"] = raw

    digest_input = {rel: _sha256_bytes(data) for rel, data in sorted(files_to_stage.items())}
    index_digest = canonical_sha256(
        {
            "corpus_id": manifest.corpus_id,
            "revision": manifest.revision,
            "profile_id": CORPUS_PROFILE_ID,
            "files": digest_input,
        }
    )

    indexes_dir = out_root / "indexes"
    indexes_dir.mkdir(parents=True, exist_ok=True)
    target_dir = indexes_dir / index_digest
    tmp_dir = Path(tempfile.mkdtemp(prefix=".tmp-corpus-", dir=indexes_dir))
    tmp_pointer: Path | None = None

    try:
        for rel, payload in files_to_stage.items():
            _check_cancel(check_cancelled)
            rel_ok = validate_relative_artifact_path(rel, root=tmp_dir)
            dest = tmp_dir / PurePosixPath(rel_ok)
            written_sha = _write_bytes_fsync(dest, payload)
            if written_sha != _sha256_bytes(payload):
                raise ValueError(f"STAGED_HASH_MISMATCH: {rel}")

        _check_cancel(check_cancelled)
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
                target_dir.rmdir()
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
    except BaseException:
        if tmp_pointer is not None:
            tmp_pointer.unlink(missing_ok=True)
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
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


def load_corpus_bundle(root_or_index_dir: str | Path) -> tuple[rag.HybridIndex, dict[str, Any]]:
    """Load and verify a corpus index from output_root (current.json) or an index folder."""
    base = Path(root_or_index_dir).expanduser().resolve()
    pointer_file = base / "current.json"
    pointer, _ = (_load_strict_json(pointer_file) if pointer_file.is_file() else (None, b""))
    if pointer is not None:
        if not isinstance(pointer, dict) or not isinstance(pointer.get("index_relative_path"), str):
            raise ValueError(f"Invalid corpus current.json pointer: {pointer_file}")
        rel = validate_relative_artifact_path(pointer["index_relative_path"], root=base)
        index_dir = base / PurePosixPath(rel)
    else:
        index_dir = base

    chunks_file = index_dir / "chunks.json"
    if not chunks_file.is_file():
        raise FileNotFoundError(f"Corpus chunks.json missing: {chunks_file}")
    meta, _ = _load_strict_json(chunks_file)
    if not isinstance(meta, dict) or not isinstance(meta.get("chunks"), list):
        raise ValueError(f"Invalid chunks.json structure in {index_dir}")
    if pointer is not None:
        if pointer.get("chunk_count") is not None and pointer["chunk_count"] != len(meta["chunks"]):
            raise ValueError("Pointer chunk_count mismatches chunks.json count")
        if pointer.get("manifest_sha256") and meta.get("manifest_sha256") != pointer["manifest_sha256"]:
            raise ValueError("Pointer manifest_sha256 mismatches chunks.json manifest_sha256")

    embedder_id = meta.get("embedder_id")
    if not isinstance(embedder_id, str) or not embedder_id:
        raise ValueError(f"Missing embedder_id in {chunks_file}")
    if pointer and pointer.get("embedder_id") and pointer["embedder_id"] != embedder_id:
        raise ValueError("Pointer embedder_id mismatches chunks.json embedder_id")

    vectors_file = index_dir / "vectors.npy"
    vectors: np.ndarray | None = None
    if vectors_file.is_file():
        vectors = np.load(vectors_file, allow_pickle=False)
        if not isinstance(vectors, np.ndarray) or vectors.ndim != 2:
            raise ValueError(f"Invalid vectors.npy shape in {index_dir}")
        if vectors.shape[0] != len(meta["chunks"]):
            raise ValueError("vectors.npy row count mismatches chunks count")
        if embedder_id == "hashing-fallback":
            _validate_vectors(vectors, len(meta["chunks"]))
        elif not np.isfinite(vectors).all():
            raise ValueError("vectors.npy contains non-finite values")

    chunks = [
        rag.Chunk(
            chunk_id=c["chunk_id"],
            source_id=c["source_id"],
            heading_path=tuple(c.get("section_path") or c.get("heading_path") or ("section",)),
            text=c["text"],
            solver=c.get("solver", ""),
        )
        for c in meta["chunks"]
    ]
    index = rag.HybridIndex(chunks=chunks, embedder_id=embedder_id, vectors=vectors)
    return index, meta


def search_hybrid_with_plan(
    index: rag.HybridIndex,
    plan: QueryPlan,
    *,
    embedder: rag.Embedder,
    reranker: rag.Reranker,
    limit: int = 5,
    candidate_count: int = rag.RERANK_CANDIDATES,
    solver: str | None = None,
) -> list[rag.Chunk]:
    """Run cae-small-bm25-hash-v1 multi-query top-20 BM25 + vector RRF(k=60) + lexical rerank."""
    if not index.chunks:
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

    allowed: list[int] | None = None
    if solver:
        allowed = [i for i, c in enumerate(index.chunks) if not c.solver or c.solver == solver]
        if not allowed:
            return []
    pool = allowed if allowed is not None else list(range(len(index.chunks)))

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
    index, meta = load_corpus_bundle(root_or_index_dir)
    embedder = rag.HashingEmbedder()
    reranker = rag.LexicalReranker()

    hits = search_hybrid_with_plan(
        index,
        plan,
        embedder=embedder,
        reranker=reranker,
        limit=limit,
        solver=solver,
    )
    chunk_meta_map = {
        (c["source_id"], c["chunk_id"]): c
        for c in meta.get("chunks", [])
        if isinstance(c, dict)
    }
    matched_specs: list[CorpusChunkSpec] = []
    solver_scopes: dict[str, Sequence[str]] = {}
    for ch in hits:
        raw_c = chunk_meta_map.get((ch.source_id, ch.chunk_id))
        if raw_c is not None and {"document_sha256", "text_sha256", "edition", "line_start", "line_end", "char_start", "char_end"} <= raw_c.keys():
            spec = CorpusChunkSpec(
                source_id=raw_c["source_id"],
                chunk_id=raw_c["chunk_id"],
                document_sha256=raw_c["document_sha256"],
                text=raw_c["text"],
                text_sha256=raw_c["text_sha256"],
                edition=raw_c["edition"],
                section_path=tuple(raw_c.get("section_path") or raw_c.get("heading_path") or ("section",)),
                line_start=raw_c["line_start"],
                line_end=raw_c["line_end"],
                char_start=raw_c["char_start"],
                char_end=raw_c["char_end"],
                page_start=raw_c.get("page_start"),
                page_end=raw_c.get("page_end"),
                coverage_facets=raw_c.get("coverage_facets") or {"solver": raw_c.get("solver_scope", [])},
            )
            matched_specs.append(spec)
            solver_scopes[spec.citation] = raw_c.get("solver_scope") or ([ch.solver] if ch.solver else [])

    decision = build_retrieval_decision(
        plan,
        matched_specs,
        source_review_refs=source_review_refs,
        explicit_facets=facets or None,
        candidate_solver_scopes=solver_scopes,
    )
    return plan, matched_specs, decision
