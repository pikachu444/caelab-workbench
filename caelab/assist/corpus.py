"""Offline verifiable CAE corpus manifest builder, atomic indexer, and cae-small-bm25-hash-v1 search."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import io
from itertools import accumulate
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from dataclasses import replace
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
    CORPUS_LEXICAL_PROFILE_ID,
    CORPUS_PROFILE_ID,
    CorpusChunkSpec,
    CorpusManifest,
    CorpusReceipt,
    CorpusSourceSpec,
    QueryPlan,
    RetrievalDecisionV1,
)
from .query_language import (
    build_retrieval_decision, classify_non_cae_intent, extract_query_scope, match_query_scope, normalize_solver,
    prepare_query,
)

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
        if status not in ("approved", "partial", "rejected", "unreviewed", "review_required"):
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


def _is_paginated(source):
    return Path(source.local_relative_path or "").suffix.lower() == ".pdf"


def _chunk_pages(source, fields, page_count):
    start, end = fields.get("page_start"), fields.get("page_end")
    if fields.get("page") is not None:
        page = fields["page"]
        if any(value is not None and (type(value) is not int or value != page) for value in (start, end)):
            raise ValueError(f"PAGE_SPAN_MISMATCH: {source.source_id}")
        start = end = page
    if start is None and end is None:
        return start, end
    if not _is_paginated(source):
        raise ValueError(f"PAGE_NOT_APPLICABLE: {source.source_id}")
    if (page_count is None or type(start) is not int or type(end) is not int
            or not 1 <= start <= end <= page_count):
        raise ValueError(f"PAGE_OUT_OF_RANGE: {source.source_id}")
    return start, end


def _verify_pdf_page_span(chunk, offsets):
    if chunk.page_start is None:
        return
    start, end = chunk.page_start, chunk.page_end
    if (not offsets[start - 1] <= chunk.char_start < offsets[start]
            or not offsets[end - 1] < chunk.char_end <= offsets[end]):
        raise ValueError(f"PDF_PAGE_SPAN_MISMATCH: {chunk.citation}")


def _local_chunk_digest(chunk):
    return canonical_sha256({
        key: getattr(chunk, key) for key in (
            "chunk_id", "source_id", "document_sha256", "line_start", "line_end",
            "char_start", "char_end", "page_start", "page_end", "text_sha256",
        )
    })


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
    page_start, page_end = _chunk_pages(source, spec, source.page_count)
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
        page_start=page_start,
        page_end=page_end,
        coverage_facets=facets,
    )


def _chunk_text_with_spans(
    source: CorpusSourceSpec,
    raw_bytes: bytes,
    curated: Sequence[Mapping[str, Any]],
) -> list[CorpusChunkSpec]:
    ext = Path(source.local_relative_path or "").suffix.lower()

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


def _pdf_page_texts(reader):
    pages = []
    for page in reader.pages:
        text = rag._clean_pdf_text(page.extract_text() or "")
        pages.append(text + "\n" if text and not text.endswith("\n") else text)
    return pages


def _chunk_pdf_with_spans(
    source: CorpusSourceSpec,
    reader,
    curated: Sequence[Mapping[str, Any]],
) -> list[CorpusChunkSpec]:
    page_texts = _pdf_page_texts(reader)

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
        chunks = [
            _extract_curated_chunk(source, full_text, lines, line_starts, line_ends, item)
            for item in curated
        ]
        offsets = (0, *accumulate(map(len, page_texts)))
        for chunk in chunks:
            _verify_pdf_page_span(chunk, offsets)
        return chunks

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
    with_vectors: bool = True,
    check_cancelled: Callable[[], None] | None = None,
) -> CorpusReceipt:
    """Validate sources & reviews and atomically build/verify a cae-small-bm25-hash-v1 corpus index."""
    _check_cancel(check_cancelled)
    m_path = Path(manifest_path).expanduser().resolve()
    data, _ = _load_strict_json(m_path)
    manifest = CorpusManifest.from_dict(data)
    embedded_sources, embedded_notices, curated_by_source = _recovery_materials(data, manifest)
    source_root = m_path.parent
    out_root = Path(output_root).expanduser().resolve()

    overlay_data = load_review_overlay(review_overlay_path, manifest) if review_overlay_path is not None else None
    _check_cancel(check_cancelled)

    source_statuses: dict[str, str] = {}
    verified_bytes: dict[str, bytes] = {}
    notices_to_stage: dict[str, bytes] = {}
    packaged_notices: dict[str, list[dict]] = {}
    warnings: list[str] = []
    reviews = {item["source_id"]: item["status"] for item in (overlay_data or {}).get("reviews", ())}
    for chunk in (overlay_data or {}).get("chunks", ()):
        if chunk["source_id"] in curated_by_source and embedded_sources:
            raise ValueError("Review overlays cannot replace frozen recovery spans")
        curated_by_source.setdefault(chunk["source_id"], []).append(chunk)

    chunks: list[CorpusChunkSpec] = []
    seen_citations = set()
    page_counts = {}
    local_chunk_sha256 = {}
    for src in manifest.sources:
        _check_cancel(check_cancelled)
        review_status = reviews.get(src.source_id, src.review_status or "unreviewed")
        if "rejected" in (review_status, src.review_status):
            source_statuses[src.source_id] = "rejected"
            warnings.append(f"source_rejected:{src.source_id}")
            continue
        if src.extraction_quality == "failed":
            raise ValueError(f"SOURCE_EXTRACTION_FAILED: {src.source_id}")
        raw = _verify_source_bytes(src, source_root, embedded_sources.get(src.source_id))
        if raw is None:
            source_statuses[src.source_id] = "source_unavailable"
            warnings.append(f"source_unavailable:{src.source_id}")
            continue
        verified_bytes[src.source_id] = raw
        source_notices = _source_notices(src, source_root, embedded_notices)
        for relative, payload in source_notices.items():
            if relative in notices_to_stage and notices_to_stage[relative] != payload:
                raise ValueError(f"Conflicting packaged license notice: {relative}")
        notices_to_stage.update(source_notices)
        if source_notices:
            packaged_notices[src.source_id] = [
                {"path": relative, "sha256": _sha256_bytes(payload), "byte_length": len(payload)}
                for relative, payload in source_notices.items()
            ]
        curated = curated_by_source.get(src.source_id, ())
        if _is_paginated(src):
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            page_count = len(reader.pages)
            if src.page_count is not None and src.page_count != page_count:
                raise ValueError(f"PAGE_COUNT_MISMATCH: {src.source_id}")
            if not page_count:
                raise ValueError(f"NO_EXTRACTABLE_CHUNKS: {src.source_id}")
            page_counts[src.source_id] = page_count
            extracted = _chunk_pdf_with_spans(replace(src, page_count=page_count), reader, curated)
        else:
            if src.page_count is not None:
                raise ValueError(f"PAGE_NOT_APPLICABLE: {src.source_id}")
            extracted = _chunk_text_with_spans(src, raw, curated)
        if not extracted:
            raise ValueError(f"NO_EXTRACTABLE_CHUNKS: {src.source_id}")
        for chunk in extracted:
            if chunk.citation in seen_citations:
                raise ValueError(f"Duplicate corpus chunk: {chunk.citation}")
            seen_citations.add(chunk.citation)
            scope = _chunk_solver_scope(chunk)
            if not set(scope) <= set(src.solver_scope):
                raise ValueError(f"Chunk solver scope exceeds source scope: {chunk.citation}")
            if src.license["redistribution"] not in _REDISTRIBUTABLE_VALUES:
                local_chunk_sha256[chunk.citation] = _local_chunk_digest(chunk)
        chunks.extend(extracted)
        source_statuses[src.source_id] = review_status
        if review_status != "approved":
            warnings.append(f"source_review_{review_status}:{src.source_id}")
        if src.extraction_quality not in {"verified", "complete"}:
            warnings.append(f"source_extraction_{src.extraction_quality}:{src.source_id}")
        if src.license["redistribution"] not in _REDISTRIBUTABLE_VALUES:
            warnings.append(f"source_original_not_packaged:{src.source_id}")

    if manifest.local_chunk_sha256 is not None and manifest.local_chunk_sha256 != local_chunk_sha256:
        raise ValueError("LOCAL_CHUNK_DIGEST_MISMATCH: source manifest")
    manifest = replace(
        manifest,
        local_chunk_sha256=local_chunk_sha256 or None,
        sources=tuple(replace(
            src, page_count=page_counts.get(src.source_id, src.page_count),
            license={**src.license, "notice_files": packaged_notices[src.source_id]}
            if src.source_id in packaged_notices else src.license,
        ) for src in manifest.sources),
    )
    manifest_bytes = canonical_json_bytes(manifest.to_dict())
    manifest_sha = _sha256_bytes(manifest_bytes)
    embedder = rag.HashingEmbedder()
    reranker = rag.LexicalReranker()

    profile_id = CORPUS_PROFILE_ID if with_vectors else CORPUS_LEXICAL_PROFILE_ID
    embedder_id = embedder.id if with_vectors else "none"
    vectors_bytes = None
    if with_vectors:
        _check_cancel(check_cancelled)
        if chunks:
            embed_inputs = [f"{' > '.join(c.section_path)}\n{c.text[:rag.EMBED_TEXT_CHARS]}" for c in chunks]
            vectors = _validate_vectors(embedder.embed(embed_inputs), len(chunks))
        else:
            vectors = np.zeros((0, rag.HashingEmbedder.DIM), dtype=np.float32)
        vec_buf = io.BytesIO()
        npy_format.write_array(vec_buf, vectors, version=(2, 0), allow_pickle=False)
        vectors_bytes = vec_buf.getvalue()

    source_by_id = {src.source_id: src for src in manifest.sources}
    chunks_payload = {
        "schema_version": 1,
        "profile_id": profile_id,
        "corpus_id": manifest.corpus_id,
        "corpus_revision": manifest.revision,
        "manifest_sha256": manifest_sha,
        "embedder_id": embedder_id,
        "reranker_id": reranker.id,
        "bm25": {"k1": 1.5, "b": 0.75},
        "rrf_k": rag.RRF_K,
        "vector_dim": rag.HashingEmbedder.DIM if with_vectors else None,
        "vector_dtype": "float32" if with_vectors else None,
        "source_statuses": source_statuses,
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

    chunks_bytes = canonical_json_bytes(chunks_payload)
    reviews_bytes = canonical_json_bytes(overlay_data) if overlay_data is not None else None

    files_to_stage: dict[str, bytes] = {
        "manifest.json": manifest_bytes,
        "chunks.json": chunks_bytes,
        **notices_to_stage,
    }
    if vectors_bytes is not None:
        files_to_stage["vectors.npy"] = vectors_bytes
        files_to_stage["vector_meta.json"] = canonical_json_bytes(_vector_metadata(vectors_bytes, len(chunks)))
    if reviews_bytes is not None:
        files_to_stage["reviews.json"] = reviews_bytes
    for src in manifest.sources:
        raw = verified_bytes.get(src.source_id)
        if raw is None:
            continue
        if src.license["redistribution"] in _REDISTRIBUTABLE_VALUES and src.local_relative_path:
            files_to_stage[f"sources/{src.local_relative_path}"] = raw

    index_digest = _index_digest(manifest, files_to_stage, profile_id)

    if verify_only:
        _check_cancel(check_cancelled)
        _load_vectors(files_to_stage, out_root, len(chunks), with_vectors)
        _verify_packaged_sources(manifest, files_to_stage, out_root)
        return CorpusReceipt(
            corpus_id=manifest.corpus_id, revision=manifest.revision, manifest_sha256=manifest_sha,
            verify_only=True, status="verified_with_limitations" if warnings else "verified",
            profile_id=profile_id, embedder_id=embedder_id, reranker_id=reranker.id,
            chunk_count=len(chunks), source_count=len(manifest.sources), source_statuses=source_statuses,
            warnings=tuple(warnings),
        )

    indexes_dir = out_root / "indexes"
    indexes_dir.mkdir(parents=True, exist_ok=True)
    target_dir = indexes_dir / index_digest
    tmp_dir = Path(tempfile.mkdtemp(prefix=".tmp-corpus-", dir=indexes_dir))
    try:
        _stage_files(tmp_dir, files_to_stage, check_cancelled)
        staged = _dir_snapshot(tmp_dir)
        if _index_digest(manifest, staged, profile_id) != index_digest:
            raise ValueError("STAGED_HASH_MISMATCH: corpus index")
        _load_vectors(staged, tmp_dir, len(chunks), with_vectors)
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
            "profile_id": profile_id,
            "embedder_id": embedder_id,
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
        profile_id=profile_id,
        embedder_id=embedder_id,
        reranker_id=reranker.id,
        chunk_count=len(chunks),
        source_count=len(manifest.sources),
        index_digest=index_digest,
        index_dir=str(target_dir),
        current_pointer_path=str(out_root / "current.json"),
        source_statuses=source_statuses,
        warnings=tuple(warnings),
    )


def _stage_files(directory, files, check_cancelled):
    for relative, payload in files.items():
        _check_cancel(check_cancelled)
        path = directory / validate_relative_artifact_path(relative, root=directory)
        _write_bytes_fsync(path, payload)
    _check_cancel(check_cancelled)


def _vector_metadata(raw, chunk_count):
    return {
        "schema_version": 1, "embedder_id": rag.HashingEmbedder.id,
        "shape": [chunk_count, rag.HashingEmbedder.DIM], "dtype": "float32",
        "sha256": _sha256_bytes(raw),
    }


def _load_vectors(files, index_dir, chunk_count, with_vectors):
    if not with_vectors:
        if "vectors.npy" in files or "vector_meta.json" in files:
            raise ValueError("Lexical-only index must not contain vector files")
        return None
    for name in ("vectors.npy", "vector_meta.json"):
        if name not in files:
            raise FileNotFoundError(f"Corpus {name} missing: {index_dir}")
    raw = files["vectors.npy"]
    vector_meta = _strict_json(files["vector_meta.json"], index_dir / "vector_meta.json")
    if vector_meta != _vector_metadata(raw, chunk_count):
        raise ValueError("VECTOR_METADATA_MISMATCH: vectors.npy")
    return _validate_vectors(np.load(io.BytesIO(raw), allow_pickle=False), chunk_count)


def _index_digest(manifest, files, profile_id=CORPUS_PROFILE_ID):
    return canonical_sha256({
        "corpus_id": manifest.corpus_id,
        "revision": manifest.revision,
        "profile_id": profile_id,
        "files": {rel: _sha256_bytes(raw) for rel, raw in files.items()},
    })


def _chunk_solver_scope(chunk: CorpusChunkSpec) -> tuple[str, ...]:
    scope = chunk.coverage_facets.get("solver", ())
    if not isinstance(scope, tuple) or any(not isinstance(solver, str) or not solver for solver in scope):
        raise ValueError(f"Invalid chunk solver scope: {chunk.citation}")
    return scope


def load_corpus_bundle(root_or_index_dir: str | Path, *, with_vectors: bool | None = None) -> tuple[rag.HybridIndex, dict[str, Any]]:
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
    for filename in ("chunks.json", "manifest.json"):
        if filename not in files:
            raise FileNotFoundError(f"Corpus {filename} missing: {index_dir}")
    manifest = CorpusManifest.from_dict(_strict_json(files["manifest.json"], index_dir / "manifest.json"))
    meta = _strict_json(files["chunks.json"], index_dir / "chunks.json")
    if not isinstance(meta, dict) or type(meta.get("schema_version")) is not int or meta["schema_version"] != 1:
        raise ValueError("Invalid corpus chunks.json schema")
    profile_id = meta.get("profile_id")
    if profile_id not in (CORPUS_PROFILE_ID, CORPUS_LEXICAL_PROFILE_ID):
        raise ValueError("Invalid corpus retrieval profile")
    has_vectors = profile_id == CORPUS_PROFILE_ID
    if with_vectors is not None and with_vectors != has_vectors:
        raise ValueError("Corpus vector mode does not match the requested verification mode")
    for filename in ("vectors.npy", "vector_meta.json") if has_vectors else ():
        if filename not in files:
            raise FileNotFoundError(f"Corpus {filename} missing: {index_dir}")
    digest = _index_digest(manifest, files, profile_id)
    if index_dir.name != digest or (pointer is not None and pointer["index_digest"] != digest):
        raise ValueError("INDEX_DIGEST_MISMATCH: corpus index files differ from their published digest")

    embedder_id = rag.HashingEmbedder.id if has_vectors else "none"
    reranker_id = rag.LexicalReranker().id
    manifest_sha = _sha256_bytes(files["manifest.json"])
    profile = {
        "corpus_id": manifest.corpus_id, "profile_id": profile_id, "embedder_id": embedder_id,
        "reranker_id": reranker_id, "manifest_sha256": manifest_sha,
    }
    expected_meta = dict(profile, corpus_revision=manifest.revision, rrf_k=rag.RRF_K,
                         bm25={"k1": 1.5, "b": 0.75},
                         vector_dim=rag.HashingEmbedder.DIM if has_vectors else None,
                         vector_dtype="float32" if has_vectors else None)
    if any(meta.get(key) != value for key, value in expected_meta.items()) or not isinstance(meta.get("chunks"), list):
        raise ValueError("Corpus chunks.json metadata does not match manifest or retrieval profile")
    if pointer is not None:
        expected_pointer = dict(profile, revision=manifest.revision, chunk_count=len(meta["chunks"]))
        if any(pointer.get(key) != value for key, value in expected_pointer.items()):
            raise ValueError("Corpus pointer metadata does not match the verified index")

    vectors = _load_vectors(files, index_dir, len(meta["chunks"]), has_vectors)
    review_statuses = _verify_packaged_sources(manifest, files, index_dir)
    if meta.get("source_statuses") != review_statuses:
        raise ValueError("Source review metadata does not match the manifest and review overlay")
    sources = {source.source_id: source for source in manifest.sources}
    chunks = []
    citations = set()
    source_texts = {}
    local_chunks = {}
    originals_missing = False
    for raw in meta["chunks"]:
        if not isinstance(raw, dict):
            raise ValueError("Corpus chunk must be an object")
        source = sources.get(raw.get("source_id"))
        if source is None:
            raise ValueError("Corpus chunk references an unknown source")
        packaged = files.get(f"sources/{source.local_relative_path}")
        if source.source_id not in source_texts:
            page_count = source.page_count
            if not _is_paginated(source) and page_count is not None:
                raise ValueError(f"PAGE_NOT_APPLICABLE: {source.source_id}")
            text = None
            page_offsets = None
            if packaged is not None:
                if _is_paginated(source):
                    from pypdf import PdfReader
                    pages = _pdf_page_texts(PdfReader(io.BytesIO(packaged)))
                    if page_count is not None and page_count != len(pages):
                        raise ValueError(f"PAGE_COUNT_MISMATCH: {source.source_id}")
                    page_count = len(pages)
                    text = "".join(pages)
                    page_offsets = (0, *accumulate(map(len, pages)))
                else:
                    text = packaged.decode("utf-8")
            source_texts[source.source_id] = (
                page_count, text, _line_spans(text)[1:] if text is not None else None, page_offsets,
            )
        page_count, text, line_spans, page_offsets = source_texts[source.source_id]
        page_start, page_end = _chunk_pages(source, raw, page_count)
        chunk_data = {key: value for key, value in raw.items()
                      if key not in {"heading_path", "solver", "solver_scope", "source_kind", "review_status", "page"}}
        if raw.get("page") is not None:
            chunk_data.update(page_start=page_start, page_end=page_end)
        spec = CorpusChunkSpec.from_dict(chunk_data)
        scope = _chunk_solver_scope(spec)
        if (spec.document_sha256 != source.sha256 or spec.edition != source.edition
                or tuple(raw["solver_scope"]) != scope or not set(scope) <= set(source.solver_scope)
                or raw.get("source_kind") != source.source_kind or raw.get("review_status") != review_statuses[spec.source_id]
                or review_statuses[spec.source_id] in {"rejected", "source_unavailable"}
                or spec.char_end - spec.char_start != len(spec.text) or spec.citation in citations):
            raise ValueError(f"Invalid corpus chunk provenance: {spec.citation}")
        if source.license["redistribution"] not in _REDISTRIBUTABLE_VALUES:
            local_chunks[spec.citation] = _local_chunk_digest(spec)
        if text is None:
            originals_missing = True
        else:
            starts, ends = line_spans
            if (spec.line_end > len(ends) or starts[spec.line_start - 1] != spec.char_start
                    or ends[spec.line_end - 1] != spec.char_end
                    or text[spec.char_start:spec.char_end] != spec.text):
                raise ValueError(f"PACKAGED_SOURCE_SPAN_MISMATCH: {spec.citation}")
        if page_offsets is not None:
            _verify_pdf_page_span(spec, page_offsets)
        citations.add(spec.citation)
        chunks.append(rag.Chunk(
            chunk_id=spec.chunk_id, source_id=spec.source_id, heading_path=spec.section_path, text=spec.text,
            solver=scope[0] if len(scope) == 1 else "", solver_scope=scope,
            corpus_chunk=replace(spec, source_kind=source.source_kind, review_status=review_statuses[spec.source_id]),
            source_kind=source.source_kind, review_status=review_statuses[spec.source_id],
        ))
    if local_chunks != (manifest.local_chunk_sha256 or {}):
        raise ValueError("LOCAL_CHUNK_DIGEST_MISMATCH: published corpus")
    index = rag.HybridIndex(chunks=chunks, embedder_id=embedder_id, vectors=vectors,
                            corpus_revision=manifest.revision, index_digest=digest)
    complete_reviews = all(
        review_statuses[source.source_id] == "approved" and source.extraction_quality in {"verified", "complete"}
        for source in manifest.sources
    )
    return index, dict(meta, index_digest=digest,
                       verification_status="verified" if complete_reviews and not originals_missing
                       else "verified_with_limitations")


def _verify_packaged_sources(manifest, files, index_dir):
    sources = {source.source_id: source for source in manifest.sources}
    reviews = {}
    if "reviews.json" in files:
        overlay = _strict_json(files["reviews.json"], index_dir / "reviews.json")
        for review in overlay["reviews"]:
            sid = review["source_id"]
            if (sid not in sources or sid in reviews or review["document_sha256"] != sources[sid].sha256
                    or review["status"] not in ("approved", "partial", "rejected", "unreviewed", "review_required")):
                raise ValueError(f"Invalid packaged source review: {sid}")
            reviews[sid] = review["status"]
    statuses = {}
    for sid, source in sources.items():
        review_status = reviews.get(sid, source.review_status or "unreviewed")
        if "rejected" in (review_status, source.review_status):
            statuses[sid] = "rejected"
            continue
        if source.local_relative_path is None and source.extraction_quality == "source_unavailable":
            statuses[sid] = "source_unavailable"
            continue
        if source.extraction_quality == "failed":
            raise ValueError(f"SOURCE_EXTRACTION_FAILED: {sid}")
        relative = f"sources/{source.local_relative_path}"
        if relative in files:
            _verify_bytes(files[relative], sid, source.sha256, source.byte_length)
        elif source.license["redistribution"] in _REDISTRIBUTABLE_VALUES:
            raise ValueError(f"PACKAGED_SOURCE_MISSING: {sid}")
        license_data = source.license
        notices = license_data.get("notice_files", ())
        required = license_data["redistribution"] == "notice_required" or license_data.get("notice_required")
        if required and not notices:
            raise ValueError(f"LICENSE_NOTICE_MISSING: {sid}")
        for notice in notices:
            if not isinstance(notice, Mapping):
                raise ValueError(f"LICENSE_NOTICE_DIGEST_MISSING: {sid}")
            relative = notice["path"]
            if relative not in files:
                raise ValueError(f"LICENSE_NOTICE_MISSING: {relative}")
            _verify_bytes(files[relative], relative, notice["sha256"], notice["byte_length"])
        statuses[sid] = review_status
    return statuses


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
    if index.vectors is not None and index.embedder_id and embedder.id != index.embedder_id:
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

    requirements = _search_scope(plan, solver, edition)
    pool = [
        i for i, chunk in enumerate(index.chunks)
        if _matches_scope(chunk, requirements)
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
    target_solver = normalize_solver(facets.get("solver"))
    target_edition = facets.get("edition")
    hits = search_hybrid_with_plan(
        index, plan, embedder=rag.HashingEmbedder(), reranker=rag.LexicalReranker(),
        limit=limit, solver=target_solver, edition=target_edition,
    )
    matched_specs = [chunk.corpus_chunk for chunk in hits]
    # Keep mismatches in the decision only, where their source tags explain the empty results.
    decision_candidates = matched_specs or mismatched_corpus_candidates(index, plan, target_solver, target_edition)
    decision = build_retrieval_decision(
        plan, decision_candidates, source_review_refs=source_review_refs, explicit_facets=facets or None,
    )
    return plan, matched_specs, decision


def _search_scope(plan, solver, edition):
    return extract_query_scope(plan, {"solver": solver, "edition": edition})


def _matches_scope(chunk: rag.Chunk, query_scope) -> bool:
    requirements, edition_owners = query_scope
    scope = {normalize_solver(value) for value in (chunk.solver_scope or ((chunk.solver,) if chunk.solver else ()))}
    if not scope and chunk.corpus_chunk is None:
        scope = {normalize_solver(req.expected) for req in requirements if req.facet == "solver"}
    edition = chunk.corpus_chunk.edition if chunk.corpus_chunk is not None else None
    solver_ok, edition_ok, _, _ = match_query_scope(requirements, edition_owners, scope, edition)
    return solver_ok and edition_ok


def mismatched_corpus_candidates(
    index: rag.HybridIndex, plan: QueryPlan, solver: str | None, edition: str | None,
) -> list[CorpusChunkSpec]:
    requirements = _search_scope(plan, solver, edition)
    if any(_matches_scope(chunk, requirements) for chunk in index.chunks):
        return []
    return [chunk.corpus_chunk for chunk in index.chunks if chunk.corpus_chunk is not None]
