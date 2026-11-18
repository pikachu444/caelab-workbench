"""Immutable v2 discussion, query planning, corpus manifest, and retrieval contracts."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import math
import re
from types import MappingProxyType
from typing import Any

from ..artifact_refs import (
    ArtifactRefV1,
    canonical_sha256,
    validate_relative_artifact_path,
)

__all__ = [
    "CORPUS_PROFILE_ID",
    "CORPUS_SOURCE_KINDS",
    "QUERY_LANGUAGES",
    "QUERY_POLICY_ID",
    "QUESTION_SCOPES",
    "RETRIEVAL_FACETS",
    "RETRIEVAL_STATUSES",
    "ROLE_POLICY_V1_ID",
    "ROLE_POLICY_V2_ID",
    "CorpusChunkSpec",
    "CorpusManifest",
    "CorpusReceipt",
    "CorpusSourceSpec",
    "QueryPlan",
    "QueryVariant",
    "ReassessmentLinkV1",
    "RetrievalCandidateCheckV1",
    "RetrievalDecisionV1",
    "RetrievalRequirementV1",
    "RoleReadPolicyV1",
    "build_role_read_policy",
    "map_corpus_source_kind_to_envelope",
]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

QUESTION_SCOPES: tuple[str, ...] = ("case_analysis", "theory")
ROLE_POLICY_V1_ID = "cae-role-read-v1"
ROLE_POLICY_V2_ID = "cae-role-read-v2"
QUERY_POLICY_ID = "cae-query-ko-en-v1"
CORPUS_PROFILE_ID = "cae-small-bm25-hash-v1"
CORPUS_LEXICAL_PROFILE_ID = "cae-small-bm25-v1"

QUERY_LANGUAGES = frozenset({"ko", "en", "mixed", "unsupported"})
CORPUS_SOURCE_KINDS = frozenset(
    {
        "official_source",
        "source_code",
        "verification_case",
        "theory_note",
        "paper",
        "solver_manual",
        "official_manual",
        "reviewed_note",
        "user_document",
    }
)
_ENVELOPE_SOURCE_KIND_MAP: dict[str, str] = {
    "official_source": "official_source",
    "source_code": "official_source",
    "verification_case": "manual",
    "theory_note": "manual",
    "paper": "paper",
    "solver_manual": "solver_manual",
    "official_manual": "solver_manual",
    "reviewed_note": "manual",
    "user_document": "manual",
}
_VALID_EVIDENCE_SOURCES = frozenset({"result_summary", "model_summary", "solver_log", "knowledge"})
_VALID_QUERY_TOOLS = frozenset({"knowledge.search", "evidence.describe", "evidence.query_field"})
_VALID_RESULT_PROJECTIONS = frozenset({"selected-node-scalar-v1", "selected-node-vector-v1"})
_EXTRACTION_QUALITIES = frozenset({"verified", "complete", "partial", "unverified", "failed", "source_unavailable"})
_LICENSE_REDISTRIBUTIONS = frozenset({"allowed", "notice_required", "local_only", "restricted", "prohibited"})

RETRIEVAL_STATUSES = frozenset({"supported", "insufficient", "out_of_scope"})
RETRIEVAL_FACETS = frozenset(
    {"topic", "solver", "edition", "model", "unit", "measure", "condition", "negation", "comparison"}
)
_REQUIREMENT_STATUSES = frozenset({"verified", "unverified"})
_CANDIDATE_RELATIONS = frozenset({"relevant", "irrelevant", "uncertain"})


def map_corpus_source_kind_to_envelope(source_kind: str) -> str:
    """Map CorpusManifest source_kind into EvidenceEnvelopeV1 provenance.sources[].source_kind."""
    if not isinstance(source_kind, str) or source_kind not in _ENVELOPE_SOURCE_KIND_MAP:
        raise ValueError(f"Unsupported corpus source_kind: {source_kind!r}")
    return _ENVELOPE_SOURCE_KIND_MAP[source_kind]


def _require_nonempty_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or "\x00" in value or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _require_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError(f"{name} must be a valid string without NUL bytes")
    return value


def _require_optional_nonempty_str(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _require_nonempty_str(value, name)


def _require_bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a bool")
    return value


def _require_int(value: Any, name: str, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an int (not bool or float)")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _require_sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a 64-char lowercase hex SHA256 digest")
    return value


def _require_exact_keys(
    data: Any,
    required: set[str] | frozenset[str],
    *,
    optional: set[str] | frozenset[str] = frozenset(),
    context: str,
) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{context} must be a mapping")
    keys = set(data.keys())
    for k in keys:
        if not isinstance(k, str) or "\x00" in k:
            raise ValueError(f"{context} mapping keys must be valid strings")
    extra = keys - (set(required) | set(optional))
    if extra:
        raise ValueError(f"{context} contains unexpected fields: {sorted(extra)}")
    missing = set(required) - keys
    if missing:
        raise ValueError(f"{context} is missing required fields: {sorted(missing)}")
    return data


def _freeze_value(value: Any, *, context: str = "value") -> Any:
    if value is None or type(value) is bool:
        return value
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError(f"{context} must not contain NUL bytes")
        return value
    if type(value) is int:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{context} must contain only finite numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str) or "\x00" in k:
                raise ValueError(f"{context} mapping keys must be valid strings")
            frozen[k] = _freeze_value(v, context=f"{context}.{k}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item, context=f"{context}[]") for item in value)
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value
    raise ValueError(f"{context} contains unsupported non-JSON value of type {type(value).__name__}")


def _to_plain(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _to_plain(value.to_dict())
    if value is None or type(value) is bool or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON output cannot contain non-finite floats")
        return value
    if isinstance(value, Mapping):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(item) for item in value]
    raise ValueError(f"Unsupported type for JSON export: {type(value).__name__}")


def _require_str_tuple(values: Any, name: str, *, allow_empty_item: bool = False) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Sequence):
        raise ValueError(f"{name} must be a sequence of strings")
    items: list[str] = []
    for idx, item in enumerate(values):
        if allow_empty_item:
            items.append(_require_str(item, f"{name}[{idx}]"))
        else:
            items.append(_require_nonempty_str(item, f"{name}[{idx}]"))
    return tuple(items)


def _require_span_pair(span: Any, name: str, *, allow_empty: bool = False) -> tuple[int, int]:
    if isinstance(span, (str, bytes, Mapping)) or not isinstance(span, Sequence) or len(span) != 2:
        raise ValueError(f"{name} must be a 2-element [start, end] sequence")
    start = _require_int(span[0], f"{name}[0]", minimum=0)
    minimum_end = start if allow_empty else start + 1
    end = _require_int(span[1], f"{name}[1]", minimum=minimum_end)
    return (start, end)


# ------------------------------------------------------------------ Reassessment & Role Policy
@dataclass(frozen=True)
class ReassessmentLinkV1:
    """Three-field mandatory reassessment link pointing to the original review context."""

    review_id: str
    item_id: str
    original_context_hash: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.review_id, "ReassessmentLinkV1.review_id")
        _require_nonempty_str(self.item_id, "ReassessmentLinkV1.item_id")
        _require_sha256(self.original_context_hash, "ReassessmentLinkV1.original_context_hash")

    def to_dict(self) -> dict[str, str]:
        return {
            "review_id": self.review_id,
            "item_id": self.item_id,
            "original_context_hash": self.original_context_hash,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ReassessmentLinkV1:
        if not isinstance(data, Mapping):
            raise ValueError("ReassessmentLinkV1 must be a mapping with review_id, item_id, original_context_hash (no tuple2 alias)")
        m = _require_exact_keys(
            data,
            {"review_id", "item_id", "original_context_hash"},
            context="ReassessmentLinkV1",
        )
        return cls(
            review_id=m["review_id"],
            item_id=m["item_id"],
            original_context_hash=m["original_context_hash"],
        )


@dataclass(frozen=True)
class RoleReadPolicyV1:
    """Role read permissions separating legacy evidence sources from executable query tools."""

    policy_id: str
    template_id: str
    template_revision: str
    allowed_evidence_sources: tuple[str, ...]
    allowed_query_tools: tuple[str, ...]
    allowed_result_projections: tuple[str, ...]
    policy_sha256: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.policy_id, "RoleReadPolicyV1.policy_id")
        _require_nonempty_str(self.template_id, "RoleReadPolicyV1.template_id")
        _require_nonempty_str(self.template_revision, "RoleReadPolicyV1.template_revision")
        sources = _require_str_tuple(self.allowed_evidence_sources, "RoleReadPolicyV1.allowed_evidence_sources")
        if not set(sources) <= _VALID_EVIDENCE_SOURCES:
            raise ValueError(f"Unsupported evidence sources: {sorted(set(sources) - _VALID_EVIDENCE_SOURCES)}")
        tools = _require_str_tuple(self.allowed_query_tools, "RoleReadPolicyV1.allowed_query_tools")
        if not set(tools) <= _VALID_QUERY_TOOLS:
            raise ValueError(f"Unsupported query tools: {sorted(set(tools) - _VALID_QUERY_TOOLS)}")
        projections = _require_str_tuple(self.allowed_result_projections, "RoleReadPolicyV1.allowed_result_projections")
        if not set(projections) <= _VALID_RESULT_PROJECTIONS:
            raise ValueError(f"Unsupported result projections: {sorted(set(projections) - _VALID_RESULT_PROJECTIONS)}")
        object.__setattr__(self, "allowed_evidence_sources", sources)
        object.__setattr__(self, "allowed_query_tools", tools)
        object.__setattr__(self, "allowed_result_projections", projections)
        _require_sha256(self.policy_sha256, "RoleReadPolicyV1.policy_sha256")
        expected = canonical_sha256(self.to_dict(), exclude_keys=("policy_sha256",))
        if self.policy_sha256 != expected:
            raise ValueError(f"RoleReadPolicyV1.policy_sha256 mismatch: expected {expected}, got {self.policy_sha256}")

    @classmethod
    def create(
        cls,
        *,
        policy_id: str,
        template_id: str,
        template_revision: str,
        allowed_evidence_sources: Sequence[str],
        allowed_query_tools: Sequence[str],
        allowed_result_projections: Sequence[str],
    ) -> RoleReadPolicyV1:
        preimage = {
            "policy_id": _require_nonempty_str(policy_id, "policy_id"),
            "template_id": _require_nonempty_str(template_id, "template_id"),
            "template_revision": _require_nonempty_str(template_revision, "template_revision"),
            "allowed_evidence_sources": list(allowed_evidence_sources),
            "allowed_query_tools": list(allowed_query_tools),
            "allowed_result_projections": list(allowed_result_projections),
        }
        digest = canonical_sha256(preimage, exclude_keys=("policy_sha256",))
        return cls(
            policy_id=preimage["policy_id"],
            template_id=preimage["template_id"],
            template_revision=preimage["template_revision"],
            allowed_evidence_sources=tuple(allowed_evidence_sources),
            allowed_query_tools=tuple(allowed_query_tools),
            allowed_result_projections=tuple(allowed_result_projections),
            policy_sha256=digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "template_id": self.template_id,
            "template_revision": self.template_revision,
            "allowed_evidence_sources": list(self.allowed_evidence_sources),
            "allowed_query_tools": list(self.allowed_query_tools),
            "allowed_result_projections": list(self.allowed_result_projections),
            "policy_sha256": self.policy_sha256,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RoleReadPolicyV1:
        m = _require_exact_keys(
            data,
            {
                "policy_id",
                "template_id",
                "template_revision",
                "allowed_evidence_sources",
                "allowed_query_tools",
                "allowed_result_projections",
                "policy_sha256",
            },
            context="RoleReadPolicyV1",
        )
        return cls(
            policy_id=m["policy_id"],
            template_id=m["template_id"],
            template_revision=m["template_revision"],
            allowed_evidence_sources=_require_str_tuple(m["allowed_evidence_sources"], "allowed_evidence_sources"),
            allowed_query_tools=_require_str_tuple(m["allowed_query_tools"], "allowed_query_tools"),
            allowed_result_projections=_require_str_tuple(m["allowed_result_projections"], "allowed_result_projections"),
            policy_sha256=m["policy_sha256"],
        )


def build_role_read_policy(
    template_id: str,
    allowed_evidence_sources: Sequence[str],
    *,
    policy_id: str = ROLE_POLICY_V2_ID,
    template_revision: str = "1",
) -> RoleReadPolicyV1:
    """Compute RoleReadPolicyV1 from role evidence permissions and policy version."""
    if policy_id not in (ROLE_POLICY_V1_ID, ROLE_POLICY_V2_ID):
        raise ValueError(f"Unsupported role read policy_id: {policy_id!r}")
    sources = tuple(allowed_evidence_sources)
    tools: list[str] = []
    if "knowledge" in sources:
        tools.append("knowledge.search")
    if template_id != "theory" and any(s in sources for s in ("result_summary", "model_summary", "solver_log")):
        tools.append("evidence.describe")
    projections: tuple[str, ...] = ()
    if (
        policy_id == ROLE_POLICY_V2_ID
        and template_id != "theory"
        and "result_summary" in sources
    ):
        projections = ("selected-node-scalar-v1", "selected-node-vector-v1")
        tools.append("evidence.query_field")
    return RoleReadPolicyV1.create(
        policy_id=policy_id,
        template_id=template_id,
        template_revision=template_revision,
        allowed_evidence_sources=sources,
        allowed_query_tools=tuple(tools),
        allowed_result_projections=projections,
    )


# ------------------------------------------------------------------ QueryPlan
@dataclass(frozen=True)
class QueryVariant:
    """One search query variant derived from an immutable QueryPlan."""

    text: str
    language: str
    method: str
    resource_revision: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.text, "QueryVariant.text")
        if self.language not in QUERY_LANGUAGES:
            raise ValueError(f"QueryVariant.language must be one of {sorted(QUERY_LANGUAGES)}, got {self.language!r}")
        _require_nonempty_str(self.method, "QueryVariant.method")
        _require_nonempty_str(self.resource_revision, "QueryVariant.resource_revision")

    def to_dict(self) -> dict[str, str]:
        return {
            "text": self.text,
            "language": self.language,
            "method": self.method,
            "resource_revision": self.resource_revision,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> QueryVariant:
        m = _require_exact_keys(
            data,
            {"text", "language", "method", "resource_revision"},
            context="QueryVariant",
        )
        return cls(
            text=m["text"],
            language=m["language"],
            method=m["method"],
            resource_revision=m["resource_revision"],
        )


@dataclass(frozen=True)
class QueryPlan:
    """Canonical bilingual query plan preserving original_text verbatim."""

    original_text: str
    original_language: str
    queries: tuple[QueryVariant, ...]
    policy_revision: str
    term_spans: tuple[Mapping[str, Any], ...] = ()
    unmatched_spans: tuple[Mapping[str, Any], ...] = ()
    removed_spans: tuple[Any, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty_str(self.original_text, "QueryPlan.original_text")
        if self.original_language not in QUERY_LANGUAGES:
            raise ValueError(
                f"QueryPlan.original_language must be one of {sorted(QUERY_LANGUAGES)}, got {self.original_language!r}"
            )
        _require_nonempty_str(self.policy_revision, "QueryPlan.policy_revision")
        if isinstance(self.queries, (str, bytes, Mapping)) or not isinstance(self.queries, Sequence) or not self.queries:
            raise ValueError("QueryPlan.queries must be a nonempty sequence of QueryVariant")
        variants = tuple(
            item if isinstance(item, QueryVariant) else QueryVariant.from_dict(item)
            for item in self.queries
        )
        object.__setattr__(self, "queries", variants)
        if not isinstance(self.removed_spans, Sequence) or isinstance(self.removed_spans, (str, bytes, Mapping)):
            raise ValueError("QueryPlan.removed_spans must be a sequence")
        if tuple(self.removed_spans) != ():
            raise ValueError("QueryPlan.removed_spans must always be empty; original query text cannot be deleted")
        object.__setattr__(self, "removed_spans", ())
        frozen_terms = tuple(_freeze_value(item, context="QueryPlan.term_spans[]") for item in self.term_spans)
        frozen_unmatched = tuple(_freeze_value(item, context="QueryPlan.unmatched_spans[]") for item in self.unmatched_spans)
        warnings = _require_str_tuple(self.warnings, "QueryPlan.warnings")
        object.__setattr__(self, "term_spans", frozen_terms)
        object.__setattr__(self, "unmatched_spans", frozen_unmatched)
        object.__setattr__(self, "warnings", warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_text": self.original_text,
            "original_language": self.original_language,
            "queries": [q.to_dict() for q in self.queries],
            "policy_revision": self.policy_revision,
            "term_spans": [_to_plain(item) for item in self.term_spans],
            "unmatched_spans": [_to_plain(item) for item in self.unmatched_spans],
            "removed_spans": [],
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> QueryPlan:
        m = _require_exact_keys(
            data,
            {
                "original_text",
                "original_language",
                "queries",
                "policy_revision",
                "term_spans",
                "unmatched_spans",
                "removed_spans",
                "warnings",
            },
            context="QueryPlan",
        )
        return cls(
            original_text=m["original_text"],
            original_language=m["original_language"],
            queries=tuple(QueryVariant.from_dict(item) for item in m["queries"]),
            policy_revision=m["policy_revision"],
            term_spans=tuple(m["term_spans"]),
            unmatched_spans=tuple(m["unmatched_spans"]),
            removed_spans=tuple(m["removed_spans"]),
            warnings=tuple(m["warnings"]),
        )


# ------------------------------------------------------------------ Corpus Manifest & Receipt
@dataclass(frozen=True)
class CorpusSourceSpec:
    """One verifiable source document entry in CorpusManifest."""

    source_id: str
    source_kind: str
    title: str
    edition: str
    commit: str | None
    uri: str | None
    local_relative_path: str | None
    sha256: str
    byte_length: int
    license: Mapping[str, Any]
    solver_scope: tuple[str, ...]
    parser: Mapping[str, Any]
    extraction_quality: str
    review_status: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.source_id, "CorpusSourceSpec.source_id")
        if self.source_kind not in CORPUS_SOURCE_KINDS:
            raise ValueError(
                f"CorpusSourceSpec.source_kind must be one of {sorted(CORPUS_SOURCE_KINDS)}, got {self.source_kind!r}"
            )
        _require_nonempty_str(self.title, "CorpusSourceSpec.title")
        _require_nonempty_str(self.edition, "CorpusSourceSpec.edition")
        _require_optional_nonempty_str(self.commit, "CorpusSourceSpec.commit")
        _require_optional_nonempty_str(self.uri, "CorpusSourceSpec.uri")
        if self.local_relative_path is not None:
            validate_relative_artifact_path(self.local_relative_path)
        _require_sha256(self.sha256, "CorpusSourceSpec.sha256")
        _require_int(self.byte_length, "CorpusSourceSpec.byte_length", minimum=0)

        lic = _require_exact_keys(
            self.license,
            {"id", "source_uri", "redistribution"},
            optional={"notice_required", "notice_files"},
            context=f"CorpusSourceSpec({self.source_id}).license",
        )
        _require_nonempty_str(lic["id"], "license.id")
        _require_optional_nonempty_str(lic["source_uri"], "license.source_uri")
        redist = lic["redistribution"]
        if type(redist) is not bool and (not isinstance(redist, str) or redist not in _LICENSE_REDISTRIBUTIONS):
            raise ValueError(f"license.redistribution must be bool or one of {sorted(_LICENSE_REDISTRIBUTIONS)}")
        if "notice_required" in lic:
            _require_bool(lic["notice_required"], "license.notice_required")
        notices = lic.get("notice_files", ())
        if not isinstance(notices, (tuple, list)):
            raise ValueError("license.notice_files must be a sequence")
        for notice in notices:
            if isinstance(notice, str):
                validate_relative_artifact_path(notice)
                continue
            _require_exact_keys(notice, {"path", "sha256", "byte_length"}, context="license.notice_files")
            validate_relative_artifact_path(notice["path"])
            _require_sha256(notice["sha256"], "notice.sha256")
            _require_int(notice["byte_length"], "notice.byte_length", minimum=1)
        object.__setattr__(self, "license", _freeze_value(lic, context="license"))

        if isinstance(self.solver_scope, str):
            scope = (_require_nonempty_str(self.solver_scope, "CorpusSourceSpec.solver_scope"),)
        else:
            scope = _require_str_tuple(self.solver_scope, "CorpusSourceSpec.solver_scope")
        object.__setattr__(self, "solver_scope", scope)

        parser = _require_exact_keys(
            self.parser,
            {"id", "version"},
            context=f"CorpusSourceSpec({self.source_id}).parser",
        )
        _require_nonempty_str(parser["id"], "parser.id")
        _require_nonempty_str(parser["version"], "parser.version")
        object.__setattr__(self, "parser", _freeze_value(parser, context="parser"))

        if self.extraction_quality not in _EXTRACTION_QUALITIES:
            raise ValueError(
                f"CorpusSourceSpec.extraction_quality must be one of {sorted(_EXTRACTION_QUALITIES)}, got {self.extraction_quality!r}"
            )

        if self.review_status not in (None, "approved", "partial", "rejected", "unreviewed", "review_required"):
            raise ValueError(f"Invalid source review status: {self.review_status!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "title": self.title,
            "edition": self.edition,
            "commit": self.commit,
            "uri": self.uri,
            "local_relative_path": self.local_relative_path,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
            "license": _to_plain(self.license),
            "solver_scope": list(self.solver_scope),
            "parser": _to_plain(self.parser),
            "extraction_quality": self.extraction_quality,
            **({"review_status": self.review_status} if self.review_status is not None else {}),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CorpusSourceSpec:
        if isinstance(data, Mapping) and "extraction_grade" in data:
            data = dict(data)
            grade = data.pop("extraction_grade")
            grade = "failed" if grade == "fail" else grade
            if "extraction_quality" in data and data["extraction_quality"] != grade:
                raise ValueError("Conflicting corpus extraction_grade and extraction_quality")
            data["extraction_quality"] = grade
        m = _require_exact_keys(
            data,
            {
                "source_id",
                "source_kind",
                "title",
                "edition",
                "commit",
                "uri",
                "local_relative_path",
                "sha256",
                "byte_length",
                "license",
                "solver_scope",
                "parser",
                "extraction_quality",
            },
            optional={"review_status"},
            context="CorpusSourceSpec",
        )
        return cls(**m)


@dataclass(frozen=True)
class CorpusManifest:
    """Verified offline corpus manifest."""

    corpus_id: str
    revision: str
    sources: tuple[CorpusSourceSpec, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if _require_int(self.schema_version, "CorpusManifest.schema_version") != 1:
            raise ValueError("CorpusManifest.schema_version must equal 1")
        _require_nonempty_str(self.corpus_id, "CorpusManifest.corpus_id")
        _require_nonempty_str(self.revision, "CorpusManifest.revision")
        if isinstance(self.sources, (str, bytes, Mapping)) or not isinstance(self.sources, Sequence):
            raise ValueError("CorpusManifest.sources must be a sequence")
        sources = tuple(
            item if isinstance(item, CorpusSourceSpec) else CorpusSourceSpec.from_dict(item)
            for item in self.sources
        )
        seen_ids: set[str] = set()
        for src in sources:
            if src.source_id in seen_ids:
                raise ValueError(f"Duplicate source_id in CorpusManifest: {src.source_id!r}")
            seen_ids.add(src.source_id)
        object.__setattr__(self, "sources", sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "corpus_id": self.corpus_id,
            "revision": self.revision,
            "sources": [src.to_dict() for src in self.sources],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CorpusManifest:
        if isinstance(data, Mapping) and "manifest" in data:
            _require_exact_keys(
                data, {"schema_version", "recovery_kind", "manifest", "chunks", "sources", "licenses"},
                optional={"verification"}, context="CorpusRecoveryBundle",
            )
            if _require_int(data["schema_version"], "CorpusRecoveryBundle.schema_version") != 1:
                raise ValueError("Unsupported recovery bundle schema")
            _require_nonempty_str(data["recovery_kind"], "CorpusRecoveryBundle.recovery_kind")
            scopes: dict[str, set[str]] = {}
            for chunk in data["chunks"]:
                source_id = _require_nonempty_str(chunk.get("source_id"), "chunk.source_id")
                solver_ids = _require_str_tuple(chunk["coverage_facets"]["solver_ids"], "chunk.solver_ids")
                scopes.setdefault(source_id, set()).update(solver_ids)
            return cls._from_recovery_manifest(data["manifest"], scopes)
        if isinstance(data, Mapping) and "recovery_notice" in data:
            return cls._from_recovery_manifest(data, {})
        m = _require_exact_keys(
            data,
            {"schema_version", "corpus_id", "revision", "sources"},
            context="CorpusManifest",
        )
        return cls(**m)

    @classmethod
    def _from_recovery_manifest(cls, data, scopes):
        m = _require_exact_keys(
            data, {"schema_version", "corpus_id", "revision", "sources", "recovery_notice", "license_files"},
            optional={"chunking", "embedding", "reranker", "limitations", "files"},
            context="RecoveryCorpusManifest",
        )
        notices = {}
        for item in m["license_files"]:
            notice = _require_exact_keys(
                item, {"id", "path", "sha256", "byte_length"},
                optional={"repository", "commit", "uri", "git_blob_sha"}, context="recovery license",
            )
            notice_id = _require_nonempty_str(notice["id"], "recovery license.id")
            if notice_id in notices:
                raise ValueError(f"Duplicate recovery license: {notice_id}")
            notices[notice_id] = {
                "path": f"licenses/{notice_id}/{notice['path']}",
                "sha256": notice["sha256"],
                "byte_length": notice["byte_length"],
            }
        sources = []
        for item in m["sources"]:
            source = dict(_require_exact_keys(
                item,
                {"source_id", "source_kind", "title", "edition", "commit", "uri", "local_relative_path",
                 "sha256", "byte_length", "license", "parser", "extraction_quality"},
                optional={"repository", "path", "git_blob_sha", "previous_observed_sha256_matches",
                          "solver_scope", "review_status"}, context="recovery source",
            ))
            for key in ("repository", "path", "git_blob_sha", "previous_observed_sha256_matches"):
                source.pop(key, None)
            license_data = dict(_require_exact_keys(
                source["license"], {"id", "redistribution"},
                optional={"source_uri", "notice_ids", "notice_required"}, context="recovery source license",
            ))
            notice_ids = _require_str_tuple(license_data.pop("notice_ids", ()), "license.notice_ids")
            if any(notice_id not in notices for notice_id in notice_ids):
                raise ValueError("Recovery source references a missing license notice")
            if license_data["redistribution"] == "allowed_with_original_license_and_notices":
                if not notice_ids:
                    raise ValueError("Recovery source requires license notices")
                license_data["redistribution"] = "notice_required"
            license_data.setdefault("source_uri", None)
            license_data["notice_files"] = [notices[notice_id] for notice_id in notice_ids]
            source["license"] = license_data
            source.setdefault("solver_scope", sorted(scopes.get(source["source_id"], ())))
            if source["extraction_quality"] == "independent_recovery_review_pending":
                source["extraction_quality"] = "unverified"
            sources.append(CorpusSourceSpec.from_dict(source))
        return cls(corpus_id=m["corpus_id"], revision=m["revision"], sources=sources,
                   schema_version=m["schema_version"])


@dataclass(frozen=True)
class CorpusChunkSpec:
    """One verified corpus chunk carrying exact 1-based inclusive lines and 0-based char spans."""

    source_id: str
    chunk_id: str
    document_sha256: str
    text: str
    text_sha256: str
    edition: str
    section_path: tuple[str, ...]
    line_start: int
    line_end: int
    char_start: int
    char_end: int
    page_start: int | None
    page_end: int | None
    coverage_facets: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        _require_nonempty_str(self.source_id, "CorpusChunkSpec.source_id")
        _require_nonempty_str(self.chunk_id, "CorpusChunkSpec.chunk_id")
        _require_sha256(self.document_sha256, "CorpusChunkSpec.document_sha256")
        _require_nonempty_str(self.text, "CorpusChunkSpec.text")
        _require_sha256(self.text_sha256, "CorpusChunkSpec.text_sha256")
        expected_text_sha = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.text_sha256 != expected_text_sha:
            raise ValueError(
                f"CorpusChunkSpec.text_sha256 mismatch: expected {expected_text_sha}, got {self.text_sha256}"
            )
        _require_nonempty_str(self.edition, "CorpusChunkSpec.edition")
        section_path = _require_str_tuple(self.section_path, "CorpusChunkSpec.section_path")
        if not section_path:
            raise ValueError("CorpusChunkSpec.section_path must be nonempty")
        object.__setattr__(self, "section_path", section_path)

        ls = _require_int(self.line_start, "CorpusChunkSpec.line_start", minimum=1)
        _require_int(self.line_end, "CorpusChunkSpec.line_end", minimum=ls)
        cs = _require_int(self.char_start, "CorpusChunkSpec.char_start", minimum=0)
        _require_int(self.char_end, "CorpusChunkSpec.char_end", minimum=cs + 1)

        if self.page_start is not None:
            ps = _require_int(self.page_start, "CorpusChunkSpec.page_start", minimum=1)
        else:
            ps = None
        if self.page_end is not None:
            pe = _require_int(self.page_end, "CorpusChunkSpec.page_end", minimum=1)
        else:
            pe = None
        if (ps is None) != (pe is None):
            raise ValueError("CorpusChunkSpec page_start and page_end must both be int or both be None")
        if ps is not None and pe is not None and pe < ps:
            raise ValueError("CorpusChunkSpec.page_end must be >= page_start")
        if not isinstance(self.coverage_facets, Mapping):
            raise ValueError("CorpusChunkSpec.coverage_facets must be a mapping")
        object.__setattr__(
            self,
            "coverage_facets",
            _freeze_value(self.coverage_facets, context="CorpusChunkSpec.coverage_facets"),
        )

    @property
    def citation(self) -> str:
        return f"{self.source_id}:{self.chunk_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "chunk_id": self.chunk_id,
            "document_sha256": self.document_sha256,
            "text": self.text,
            "text_sha256": self.text_sha256,
            "edition": self.edition,
            "section_path": list(self.section_path),
            "line_start": self.line_start,
            "line_end": self.line_end,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "coverage_facets": _to_plain(self.coverage_facets),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CorpusChunkSpec:
        m = _require_exact_keys(
            data,
            {
                "source_id",
                "chunk_id",
                "document_sha256",
                "text",
                "text_sha256",
                "edition",
                "section_path",
                "line_start",
                "line_end",
                "char_start",
                "char_end",
                "page_start",
                "page_end",
                "coverage_facets",
            },
            context="CorpusChunkSpec",
        )
        return cls(**m)


@dataclass(frozen=True)
class CorpusReceipt:
    """Receipt returned by build_corpus for verification or atomic index creation."""

    corpus_id: str
    revision: str
    manifest_sha256: str
    verify_only: bool
    status: str
    profile_id: str
    embedder_id: str
    reranker_id: str
    chunk_count: int
    source_count: int
    index_digest: str | None = None
    index_dir: str | None = None
    current_pointer_path: str | None = None
    source_statuses: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    warnings: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if _require_int(self.schema_version, "CorpusReceipt.schema_version") != 1:
            raise ValueError("CorpusReceipt.schema_version must equal 1")
        _require_nonempty_str(self.corpus_id, "CorpusReceipt.corpus_id")
        _require_nonempty_str(self.revision, "CorpusReceipt.revision")
        _require_sha256(self.manifest_sha256, "CorpusReceipt.manifest_sha256")
        _require_bool(self.verify_only, "CorpusReceipt.verify_only")
        if self.status not in {"verified", "verified_with_limitations", "built", "reused"}:
            raise ValueError(f"Unsupported CorpusReceipt.status: {self.status!r}")
        _require_nonempty_str(self.profile_id, "CorpusReceipt.profile_id")
        _require_nonempty_str(self.embedder_id, "CorpusReceipt.embedder_id")
        _require_nonempty_str(self.reranker_id, "CorpusReceipt.reranker_id")
        _require_int(self.chunk_count, "CorpusReceipt.chunk_count", minimum=0)
        _require_int(self.source_count, "CorpusReceipt.source_count", minimum=0)
        if self.index_digest is not None:
            _require_sha256(self.index_digest, "CorpusReceipt.index_digest")
        _require_optional_nonempty_str(self.index_dir, "CorpusReceipt.index_dir")
        _require_optional_nonempty_str(self.current_pointer_path, "CorpusReceipt.current_pointer_path")
        if not isinstance(self.source_statuses, Mapping):
            raise ValueError("CorpusReceipt.source_statuses must be a mapping")
        object.__setattr__(
            self,
            "source_statuses",
            _freeze_value(self.source_statuses, context="CorpusReceipt.source_statuses"),
        )
        object.__setattr__(self, "warnings", _require_str_tuple(self.warnings, "CorpusReceipt.warnings"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "corpus_id": self.corpus_id,
            "revision": self.revision,
            "manifest_sha256": self.manifest_sha256,
            "verify_only": self.verify_only,
            "status": self.status,
            "profile_id": self.profile_id,
            "embedder_id": self.embedder_id,
            "reranker_id": self.reranker_id,
            "chunk_count": self.chunk_count,
            "source_count": self.source_count,
            "index_digest": self.index_digest,
            "index_dir": self.index_dir,
            "current_pointer_path": self.current_pointer_path,
            "source_statuses": _to_plain(self.source_statuses),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CorpusReceipt:
        m = _require_exact_keys(
            data,
            {
                "schema_version",
                "corpus_id",
                "revision",
                "manifest_sha256",
                "verify_only",
                "status",
                "profile_id",
                "embedder_id",
                "reranker_id",
                "chunk_count",
                "source_count",
                "index_digest",
                "index_dir",
                "current_pointer_path",
                "source_statuses",
                "warnings",
            },
            context="CorpusReceipt",
        )
        return cls(**m)


# ------------------------------------------------------------------ RetrievalDecisionV1
@dataclass(frozen=True)
class RetrievalRequirementV1:
    """One host-derived requirement for query-relevance assessment."""

    requirement_id: str
    original_span: tuple[int, int]
    facet: str
    expected: str | None
    status: str
    reason: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.requirement_id, "RetrievalRequirementV1.requirement_id")
        span = _require_span_pair(self.original_span, "RetrievalRequirementV1.original_span")
        object.__setattr__(self, "original_span", span)
        if self.facet not in RETRIEVAL_FACETS:
            raise ValueError(f"RetrievalRequirementV1.facet must be one of {sorted(RETRIEVAL_FACETS)}, got {self.facet!r}")
        _require_optional_nonempty_str(self.expected, "RetrievalRequirementV1.expected")
        if self.status not in _REQUIREMENT_STATUSES:
            raise ValueError(
                f"RetrievalRequirementV1.status must be one of {sorted(_REQUIREMENT_STATUSES)}, got {self.status!r}"
            )
        _require_nonempty_str(self.reason, "RetrievalRequirementV1.reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "original_span": list(self.original_span),
            "facet": self.facet,
            "expected": self.expected,
            "status": self.status,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RetrievalRequirementV1:
        m = _require_exact_keys(
            data,
            {"requirement_id", "original_span", "facet", "expected", "status", "reason"},
            context="RetrievalRequirementV1",
        )
        return cls(**m)


@dataclass(frozen=True)
class RetrievalCandidateCheckV1:
    """Candidate relevance record bound to verified source SHA256 and quoted spans."""

    candidate_id: str
    source_sha256: str
    relation: str
    requirement_ids: tuple[str, ...]
    quoted_spans: tuple[Mapping[str, int], ...]
    reason: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.candidate_id, "RetrievalCandidateCheckV1.candidate_id")
        _require_sha256(self.source_sha256, "RetrievalCandidateCheckV1.source_sha256")
        if self.relation not in _CANDIDATE_RELATIONS:
            raise ValueError(
                f"RetrievalCandidateCheckV1.relation must be one of {sorted(_CANDIDATE_RELATIONS)}, got {self.relation!r}"
            )
        req_ids = _require_str_tuple(self.requirement_ids, "RetrievalCandidateCheckV1.requirement_ids")
        object.__setattr__(self, "requirement_ids", req_ids)

        if isinstance(self.quoted_spans, (str, bytes, Mapping)) or not isinstance(self.quoted_spans, Sequence):
            raise ValueError("RetrievalCandidateCheckV1.quoted_spans must be a sequence")
        frozen_spans: list[MappingProxyType[str, int]] = []
        for idx, sp in enumerate(self.quoted_spans):
            sm = _require_exact_keys(sp, {"start", "end"}, context=f"quoted_spans[{idx}]")
            s = _require_int(sm["start"], f"quoted_spans[{idx}].start", minimum=0)
            e = _require_int(sm["end"], f"quoted_spans[{idx}].end", minimum=s + 1)
            frozen_spans.append(MappingProxyType({"start": s, "end": e}))
        object.__setattr__(self, "quoted_spans", tuple(frozen_spans))
        _require_nonempty_str(self.reason, "RetrievalCandidateCheckV1.reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_sha256": self.source_sha256,
            "relation": self.relation,
            "requirement_ids": list(self.requirement_ids),
            "quoted_spans": [_to_plain(sp) for sp in self.quoted_spans],
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RetrievalCandidateCheckV1:
        m = _require_exact_keys(
            data,
            {"candidate_id", "source_sha256", "relation", "requirement_ids", "quoted_spans", "reason"},
            context="RetrievalCandidateCheckV1",
        )
        return cls(**m)


def _coerce_optional_artifact_ref(value: Any, name: str) -> ArtifactRefV1 | None:
    if value is None:
        return None
    if isinstance(value, ArtifactRefV1):
        return value
    if isinstance(value, Mapping):
        return ArtifactRefV1.from_dict(value)
    raise ValueError(f"{name} must be None, ArtifactRefV1, or mapping")


@dataclass(frozen=True)
class RetrievalDecisionV1:
    """Per-request retrieval relevance decision separate from static source reviews."""

    assessment_id: str
    query_plan_sha256: str
    candidate_set_sha256: str
    source_review_refs: tuple[ArtifactRefV1, ...]
    assessment_profile_id: str
    assessment_profile_sha256: str | None
    status: str
    requirements: tuple[RetrievalRequirementV1, ...]
    candidate_checks: tuple[RetrievalCandidateCheckV1, ...]
    missing_evidence: tuple[str, ...]
    limitations: tuple[str, ...]
    assessment_trace_ref: ArtifactRefV1 | None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if _require_int(self.schema_version, "RetrievalDecisionV1.schema_version") != 1:
            raise ValueError("RetrievalDecisionV1.schema_version must equal 1")
        _require_nonempty_str(self.assessment_id, "RetrievalDecisionV1.assessment_id")
        _require_sha256(self.query_plan_sha256, "RetrievalDecisionV1.query_plan_sha256")
        _require_sha256(self.candidate_set_sha256, "RetrievalDecisionV1.candidate_set_sha256")
        if isinstance(self.source_review_refs, (str, bytes, Mapping)) or not isinstance(self.source_review_refs, Sequence):
            raise ValueError("RetrievalDecisionV1.source_review_refs must be a sequence")
        refs = tuple(
            item if isinstance(item, ArtifactRefV1) else ArtifactRefV1.from_dict(item)
            for item in self.source_review_refs
        )
        object.__setattr__(self, "source_review_refs", refs)
        _require_nonempty_str(self.assessment_profile_id, "RetrievalDecisionV1.assessment_profile_id")
        if self.assessment_profile_sha256 is not None:
            _require_sha256(self.assessment_profile_sha256, "RetrievalDecisionV1.assessment_profile_sha256")
        if self.status not in RETRIEVAL_STATUSES:
            raise ValueError(
                f"RetrievalDecisionV1.status must be one of {sorted(RETRIEVAL_STATUSES)}, got {self.status!r}"
            )
        reqs = tuple(
            item if isinstance(item, RetrievalRequirementV1) else RetrievalRequirementV1.from_dict(item)
            for item in self.requirements
        )
        checks = tuple(
            item if isinstance(item, RetrievalCandidateCheckV1) else RetrievalCandidateCheckV1.from_dict(item)
            for item in self.candidate_checks
        )
        missing = _require_str_tuple(self.missing_evidence, "RetrievalDecisionV1.missing_evidence")
        limits = _require_str_tuple(self.limitations, "RetrievalDecisionV1.limitations")
        trace_ref = _coerce_optional_artifact_ref(self.assessment_trace_ref, "RetrievalDecisionV1.assessment_trace_ref")

        if self.status == "supported":
            if not reqs or any(r.status != "verified" for r in reqs):
                raise ValueError("RetrievalDecisionV1.status='supported' requires all requirements verified")
            if not any(c.relation == "relevant" and c.quoted_spans for c in checks):
                raise ValueError(
                    "RetrievalDecisionV1.status='supported' requires at least one relevant candidate with quoted_spans"
                )
            if not refs:
                raise ValueError("RetrievalDecisionV1.status='supported' requires source_review_refs")
            if self.assessment_profile_sha256 is None:
                raise ValueError("RetrievalDecisionV1.status='supported' requires assessment_profile_sha256")
            if trace_ref is None or trace_ref.size <= 0 or not trace_ref.role.endswith("_trace"):
                raise ValueError("RetrievalDecisionV1.status='supported' requires a valid assessment_trace_ref")

        object.__setattr__(self, "requirements", reqs)
        object.__setattr__(self, "candidate_checks", checks)
        object.__setattr__(self, "missing_evidence", missing)
        object.__setattr__(self, "limitations", limits)
        object.__setattr__(self, "assessment_trace_ref", trace_ref)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "assessment_id": self.assessment_id,
            "query_plan_sha256": self.query_plan_sha256,
            "candidate_set_sha256": self.candidate_set_sha256,
            "source_review_refs": [ref.to_dict() for ref in self.source_review_refs],
            "assessment_profile_id": self.assessment_profile_id,
            "assessment_profile_sha256": self.assessment_profile_sha256,
            "status": self.status,
            "requirements": [req.to_dict() for req in self.requirements],
            "candidate_checks": [chk.to_dict() for chk in self.candidate_checks],
            "missing_evidence": list(self.missing_evidence),
            "limitations": list(self.limitations),
            "assessment_trace_ref": None if self.assessment_trace_ref is None else self.assessment_trace_ref.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RetrievalDecisionV1:
        m = _require_exact_keys(
            data,
            {
                "schema_version",
                "assessment_id",
                "query_plan_sha256",
                "candidate_set_sha256",
                "source_review_refs",
                "assessment_profile_id",
                "assessment_profile_sha256",
                "status",
                "requirements",
                "candidate_checks",
                "missing_evidence",
                "limitations",
                "assessment_trace_ref",
            },
            context="RetrievalDecisionV1",
        )
        return cls(**m)
