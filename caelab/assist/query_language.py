"""Korean/English CAE query planning, terminology expansion, and RetrievalDecisionV1 helpers."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
import unicodedata
from typing import Any
import uuid

from ..artifact_refs import ArtifactRefV1, canonical_sha256
from .contracts import (
    QUERY_POLICY_ID,
    CorpusChunkSpec,
    QueryPlan,
    QueryVariant,
    RetrievalCandidateCheckV1,
    RetrievalDecisionV1,
    RetrievalRequirementV1,
)

__all__ = [
    "GLOSSARY_REVISION",
    "classify_non_cae_intent",
    "extract_query_requirements",
    "prepare_query",
    "build_retrieval_decision",
]

GLOSSARY_REVISION = "cae-glossary-ko-en-v1"

# Curated bilingual Korean <-> English CAE terminology pairs (longest match applied at runtime).
_GLOSSARY_PAIRS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("포아송 방정식",), ("Poisson equation",)),
    (("포아송비", "포아송 비"), ("Poisson's ratio", "Poisson ratio")),
    (("포아송",), ("Poisson",)),
    (("노이만 경계조건", "노이만 경계 조건"), ("Neumann boundary condition",)),
    (("노이만",), ("Neumann",)),
    (("디리클레 경계조건", "디리클레 경계 조건"), ("Dirichlet boundary condition",)),
    (("디리클레",), ("Dirichlet",)),
    (("경계조건", "경계 조건"), ("boundary condition",)),
    (("약형",), ("weak form", "variational form")),
    (("강체 모드", "강체모드", "강체 운동", "강체운동"), ("rigid body mode", "rigid body")),
    (("라메 상수", "라메 계수", "라메 매개변수"), ("Lame parameters", "Lamé parameters", "Lame constants")),
    (("라메",), ("Lamé", "Lame")),
    (("편차응력", "편차 응력"), ("deviatoric stress",)),
    (("등가응력", "폰 미제스", "폰미제스"), ("von Mises", "equivalent stress")),
    (("초탄성",), ("hyperelastic", "hyperelasticity")),
    (("신후크", "네오후크", "네오 후크"), ("neo-Hookean", "neoHookean")),
    (("코시 응력", "코시응력"), ("Cauchy stress",)),
    (("제1 피올라-키르히호프", "제1피올라키르히호프"), ("first Piola-Kirchhoff", "PK1")),
    (("변형 구배", "변형구배"), ("deformation gradient",)),
    (("드러커-프라거", "드러커 프라거"), ("Drucker-Prager",)),
    (("팽창각",), ("dilation angle",)),
    (("마찰각",), ("friction angle",)),
    (("점착력", "응집력"), ("cohesion",)),
    (("시행 압력",), ("trial pressure",)),
    (("시행 응력",), ("trial stress",)),
    (("프로니 급수", "프로니급수"), ("Prony series",)),
    (("프로니",), ("Prony",)),
    (("평형 탄성계수", "평형항"), ("equilibrium modulus", "equilibrium term")),
    (("이완 탄성계수", "응력 이완"), ("relaxation modulus", "stress relaxation")),
    (("이완",), ("relaxation",)),
    (("저장 탄성계수", "저장탄성계수"), ("storage modulus",)),
    (("손실 탄성계수", "손실탄성계수"), ("loss modulus",)),
    (("시간-온도 중첩", "시간 온도 중첩"), ("time-temperature superposition", "WLF")),
    (("이동 인자", "이동 함수", "이동계수"), ("shift factor",)),
    (("각주파수",), ("angular frequency",)),
    (("주파수",), ("frequency",)),
    (("점탄성",), ("viscoelastic", "viscoelasticity")),
    (("점성",), ("viscous",)),
    (("등방 경화", "등방경화"), ("isotropic hardening",)),
    (("이동 경화", "이동경화"), ("kinematic hardening",)),
    (("경화",), ("hardening",)),
    (("이력 변수", "상태 변수", "상태변수"), ("history variable", "state variable")),
    (("이력",), ("history",)),
    (("소성",), ("plasticity", "plastic")),
    (("항복",), ("yield",)),
    (("반력",), ("reaction force", "reaction")),
    (("수렴",), ("convergence",)),
    (("발산",), ("divergence",)),
    (("접촉 쌍", "접촉쌍"), ("contact pair",)),
    (("접촉",), ("contact",)),
    (("마찰",), ("friction",)),
    (("전단 잠금", "전단잠금"), ("shear locking",)),
    (("체적 잠금", "체적잠금"), ("volumetric locking",)),
    (("잠금",), ("locking",)),
    (("변형률",), ("strain",)),
    (("체적 탄성계수", "체적탄성계수"), ("bulk modulus",)),
    (("전단 탄성계수", "전단탄성계수"), ("shear modulus",)),
    (("탄성계수", "영률"), ("Young's modulus", "elastic modulus")),
    (("탄성",), ("elastic", "elasticity")),
    (("압축성",), ("compressible", "compressibility")),
    (("비압축성",), ("incompressible", "incompressibility")),
    (("이방성",), ("anisotropy", "anisotropic")),
    (("응력",), ("stress",)),
    (("변위",), ("displacement",)),
    (("부호",), ("sign",)),
    (("단위",), ("unit",)),
    (("육면체",), ("hexahedral", "hex")),
    (("사면체",), ("tetrahedral", "tet")),
    (("요소",), ("element",)),
    (("절점",), ("node",)),
    (("좌굴",), ("buckling",)),
    (("증분",), ("increment",)),
    (("정적 해석", "정적해석"), ("static analysis",)),
    (("정적",), ("static",)),
    (("온도 의존",), ("temperature dependent",)),
    (("온도",), ("temperature",)),
    (("재시작",), ("restart",)),
    (("잔차",), ("residual",)),
    (("페널티",), ("penalty",)),
    (("모래시계",), ("hourglass",)),
    (("평형",), ("equilibrium",)),
    (("하중",), ("load",)),
    (("구속",), ("constraint",)),
    (("고정",), ("fixed",)),
    (("감쇠",), ("damping",)),
    (("에너지",), ("energy",)),
    (("재료 모델",), ("material model",)),
    (("상수",), ("constant", "parameter")),
    (("매개변수", "계수"), ("parameter",)),
    (("변환",), ("conversion",)),
    (("정의",), ("definition", "measure")),
    (("물리량",), ("measure", "quantity")),
    (("보간",), ("interpolation",)),
    (("기본값",), ("default",)),
    (("유일성",), ("uniqueness",)),
    (("안정성",), ("stability",)),
    (("안전성",), ("safety",)),
    (("비교",), ("comparison",)),
    (("자유도",), ("degree of freedom",)),
    (("지수",), ("exponent",)),
    (("동등성",), ("equivalence",)),
    (("원형",), ("original",)),
    (("해석",), ("analysis",)),
    (("판본", "버전"), ("edition", "version")),
)

_SOLVER_PATTERNS: tuple[tuple[str, str], ...] = (
    ("calculix", "calculix"),
    ("crunchix", "calculix"),
    ("ccx", "calculix"),
    ("fenicsx", "fenicsx"),
    ("dolfinx", "fenicsx"),
    ("fenics", "fenicsx"),
    ("geos", "geos"),
    ("pyvisco", "pyvisco"),
    ("neml", "neml"),
    ("openradioss", "radioss"),
    ("opencourant", "radioss"),
    ("radioss", "radioss"),
    ("code_aster", "code_aster"),
    ("code-aster", "code_aster"),
    ("codeaster", "code_aster"),
    ("ls-dyna", "lsdyna"),
    ("lsdyna", "lsdyna"),
    ("abaqus", "abaqus"),
)

_PROTECTED_TOKEN_RE = re.compile(
    r"(?:"
    r"\*[A-Za-z][A-Za-z0-9_ -]*"  # solver keyword cards like *NODE PRINT, *STATIC
    r"|v?\d+\.\d+(?:\.\d+)+"  # software editions like v0.10.0, 2.23.1
    r"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"  # numbers
    r"|<=|>=|!=|==|<|>|="  # comparisons
    r"|\b(?:MPa|GPa|kPa|Pa|mm|cm|km|m|kg|Mg|kN|N|ms|s|Hz|rad/s|degC|K|J/mol|kJ/mol|3D|2D|1D|PK1|VM|WLF|RF|LAW\d+|C3D\d+)\b"
    r"|\b(?:not|no|without|never|except|unless|only|before|after|when|if)\b"
    r")",
    re.IGNORECASE,
)

_KO_NEGATION_RE = re.compile(r"(?:않\w*|아닌|아니\w*|없\w*|제외\w*|없이|말고)")
_KO_PARTICLES = (
    "에서는",
    "으로",
    "에서",
    "에게",
    "부터",
    "까지",
    "처럼",
    "보다",
    "인가요",
    "입니까",
    "인지",
    "인가",
    "이란",
    "란",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "로",
    "와",
    "과",
    "도",
    "만",
)

_KO_STOPWORDS = frozenset(
    {
        "무엇",
        "뭐",
        "어떻게",
        "왜",
        "언제",
        "어디",
        "어떤",
        "몇",
        "개",
        "개의",
        "및",
        "또는",
        "혹은",
        "그리고",
        "관련",
        "대해",
        "대한",
        "관한",
        "설명",
        "알려",
        "알려주세요",
        "알려줘",
        "설명해",
        "설명해주세요",
        "확인",
        "구분",
        "차이",
        "경우",
        "조건",
        "값",
        "식",
        "수식",
        "모델",
        "이론",
        "문헌",
        "근거",
        "원리",
        "때",
        "있나요",
        "있는",
        "있다",
        "있습니까",
        "되나요",
        "되는",
        "한다",
        "합니다",
        "하나요",
        "주세요",
        "서로",
        "독립적",
        "독립적인",
        "독립",
        "표시",
        "적용",
        "사용",
        "계산",
        "정확한",
        "정확히",
        "관계",
        "항",
        "성분",
        "부호",
        "상태",
        "결과",
        "입력",
        "출력",
    }
)

_EN_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "what",
        "which",
        "how",
        "why",
        "where",
        "when",
        "who",
        "in",
        "on",
        "at",
        "of",
        "for",
        "to",
        "from",
        "by",
        "with",
        "between",
        "into",
        "and",
        "or",
        "as",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "can",
        "could",
        "should",
        "would",
        "may",
        "might",
        "must",
        "does",
        "explain",
        "describe",
        "show",
        "tell",
        "me",
        "about",
        "exact",
        "give",
        "find",
        "use",
        "used",
        "using",
        "model",
        "value",
        "values",
        "formula",
        "equation",
        "form",
        "terms",
        "term",
        "independent",
        "mutually",
        "each",
        "other",
    }
)

_NON_CAE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("weather", re.compile(r"(?:날씨|기온|강수|예보|weather|forecast|temperature\s+outside|rain\s+tomorrow)", re.I)),
    ("cooking", re.compile(r"(?:요리|레시피|음식|김치찌개|파스타|recipe|cook|cooking|baking|ingredients)", re.I)),
    ("price_shopping", re.compile(r"(?:가격|할인|쇼핑|price|discount|buy\s+a|how\s+much\s+does)", re.I)),
    ("sports", re.compile(r"(?:축구|야구|농구|스포츠|경기\s*결과|soccer|football|baseball|basketball|sports\s+score)", re.I)),
    ("translation", re.compile(r"(?:번역해|영어로\s*번역|translate\s+to|translation\s+of)", re.I)),
    ("license_admin", re.compile(r"(?:운전면허|driver'?s\s+license)", re.I)),
    ("hospitality", re.compile(r"(?:식당|맛집|호텔|예약|restaurant|hotel|reservation|book\s+a\s+room)", re.I)),
    ("finance", re.compile(r"(?:은행|계좌|주식|주가|선거|투표|bank\s+account|stock\s+price|election)", re.I)),
    ("personal_misc", re.compile(r"(?:축사|비밀번호|패스워드|이메일\s*작성|메일\s*써|체스|영화\s*추천|아기\s*이름|강아지\s*이름|이름\s*지어|speech|password|chess|movie\s+recommend|baby\s+name|write\s+an\s+email)", re.I)),
)

_UNSUPPORTED_SCRIPT_RE = re.compile(
    r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\u0400-\u04FF\u0600-\u06FF\u0900-\u097F\u0E00-\u0E7F]"
)
_FRENCH_OR_EURO_MARKERS_RE = re.compile(r"[àâçèêëîïôùûüÿœæäößñáíóú¿¡]", re.IGNORECASE)
_FRENCH_WORDS_RE = re.compile(
    r"\b(?:quel|quelle|quels|quelles|comment|pourquoi|est-ce|dans|avec|sans|pour|sur|les|des|une|aux|contrainte|équation|maillage|déformation|français)\b",
    re.IGNORECASE,
)
_HANGUL_RE = re.compile(r"[\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z\u00C0-\u024F]+")


def _build_glossary_lookup() -> tuple[list[tuple[str, tuple[str, ...], str]], dict[str, tuple[str, ...]]]:
    """Build sorted longest-match lookup entries for Korean and English CAE phrases."""
    ko_entries: dict[str, tuple[str, ...]] = {}
    en_entries: dict[str, tuple[str, ...]] = {}
    for ko_variants, en_variants in _GLOSSARY_PAIRS:
        for ko in ko_variants:
            n_ko = unicodedata.normalize("NFKC", ko)
            ko_entries[n_ko] = tuple(dict.fromkeys((*ko_entries.get(n_ko, ()), *en_variants)))
        for en in en_variants:
            n_en = _fold_accents(unicodedata.normalize("NFKC", en)).lower()
            en_entries[n_en] = tuple(dict.fromkeys((*en_entries.get(n_en, ()), *ko_variants)))
    entries: list[tuple[str, tuple[str, ...], str]] = []
    for k, targets in ko_entries.items():
        entries.append((k, targets, "en"))
    for k, targets in en_entries.items():
        entries.append((k, targets, "ko"))
    entries.sort(key=lambda row: (-len(row[0]), row[0]))
    return entries, ko_entries


def _fold_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


_SORTED_GLOSSARY, _KO_GLOSSARY = _build_glossary_lookup()


def _nfkc_with_orig_map(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Apply Unicode NFKC and track each NFKC codepoint back to [orig_start, orig_end)."""
    out_chars: list[str] = []
    mapping: list[tuple[int, int]] = []
    for idx, ch in enumerate(text):
        norm = unicodedata.normalize("NFKC", ch)
        for sub in norm:
            out_chars.append(sub)
            mapping.append((idx, idx + 1))
    return "".join(out_chars), mapping


def _detect_language(text: str, requested: str) -> str:
    if requested not in ("auto", "ko", "en", "mixed"):
        return "unsupported"
    nfkc = unicodedata.normalize("NFKC", text)
    if _UNSUPPORTED_SCRIPT_RE.search(nfkc):
        return "unsupported"
    # Allow Lamé / Poincaré / Lévy as CAE terms; reject French/German/Spanish prose.
    scrubbed = re.sub(r"\b(?:lam[ée]|poincar[ée]|l[ée]vy)\b", "", nfkc, flags=re.IGNORECASE)
    if _FRENCH_OR_EURO_MARKERS_RE.search(scrubbed) or _FRENCH_WORDS_RE.search(scrubbed):
        return "unsupported"

    has_ko = bool(_HANGUL_RE.search(nfkc))
    # Remove solvers, units, cards, editions, acronyms before counting English prose words.
    stripped = _PROTECTED_TOKEN_RE.sub(" ", nfkc)
    latin_words = [
        w for w in _LATIN_WORD_RE.findall(stripped)
        if not any(s_alias == w.lower() for s_alias, _ in _SOLVER_PATTERNS)
    ]
    if not has_ko and not latin_words:
        # Only punctuation or symbols with no readable language words
        if not re.search(r"[0-9A-Za-z\uAC00-\uD7AF]", nfkc):
            return "unsupported"
        return "en" if requested == "auto" else requested

    if has_ko and latin_words:
        if requested in ("ko", "mixed"):
            return requested
        return "mixed" if len(latin_words) >= 2 else "ko"
    if has_ko:
        return "ko" if requested in ("auto", "ko") else requested
    return "en" if requested in ("auto", "en") else requested


def _protected_ranges(nfkc_text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for m in _PROTECTED_TOKEN_RE.finditer(nfkc_text):
        ranges.append((m.start(), m.end()))
    for m in _KO_NEGATION_RE.finditer(nfkc_text):
        ranges.append((m.start(), m.end()))
    return ranges


def _overlaps(start: int, end: int, taken: Sequence[tuple[int, int]]) -> bool:
    return any(not (end <= a or start >= b) for a, b in taken)


def _match_glossary_spans(
    original_text: str,
    nfkc_text: str,
    orig_map: list[tuple[int, int]],
    protected: list[tuple[int, int]],
) -> tuple[list[dict[str, Any]], list[tuple[int, int, str, tuple[str, ...]]]]:
    """Find longest-match non-overlapping CAE glossary terms without mutating protected tokens."""
    folded_lower = _fold_accents(nfkc_text).lower()
    # Build index map from folded_lower back to nfkc_text when accent stripping is 1:1
    use_folded = len(folded_lower) == len(nfkc_text)
    haystack_en = folded_lower if use_folded else nfkc_text.lower()

    taken = list(protected)
    matched: list[tuple[int, int, str, tuple[str, ...], str]] = []

    for term, targets, target_lang in _SORTED_GLOSSARY:
        is_ko = bool(_HANGUL_RE.search(term))
        if is_ko:
            start = 0
            while True:
                idx = nfkc_text.find(term, start)
                if idx < 0:
                    break
                end = idx + len(term)
                # Ensure we start at word/space boundary or Hangul start when multi-word
                left_ok = idx == 0 or not _HANGUL_RE.match(nfkc_text[idx - 1])
                if left_ok and not _overlaps(idx, end, taken):
                    taken.append((idx, end))
                    matched.append((idx, end, term, targets, target_lang))
                start = idx + 1
        else:
            pattern = re.compile(rf"(?<![0-9A-Za-z]){re.escape(term)}(?![0-9A-Za-z])", re.IGNORECASE)
            for m in pattern.finditer(haystack_en):
                idx, end = m.start(), m.end()
                if not _overlaps(idx, end, taken):
                    taken.append((idx, end))
                    matched.append((idx, end, term, targets, target_lang))

    matched.sort(key=lambda item: item[0])
    term_spans: list[dict[str, Any]] = []
    nfkc_spans: list[tuple[int, int, str, tuple[str, ...]]] = []
    for n_start, n_end, canonical, targets, target_lang in matched:
        orig_start = orig_map[n_start][0] if n_start < len(orig_map) else len(original_text)
        orig_end = orig_map[n_end - 1][1] if n_end - 1 < len(orig_map) else len(original_text)
        term_spans.append(
            {
                "start": orig_start,
                "end": orig_end,
                "source_text": original_text[orig_start:orig_end],
                "matched_term": canonical,
                "expanded_terms": list(targets),
                "target_language": target_lang,
            }
        )
        nfkc_spans.append((n_start, n_end, canonical, targets))
    return term_spans, nfkc_spans


def _strip_ko_particle(token: str) -> str:
    for suffix in _KO_PARTICLES:
        if len(token) > len(suffix) and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _find_unmatched_spans(
    original_text: str,
    nfkc_text: str,
    orig_map: list[tuple[int, int]],
    covered: list[tuple[int, int]],
) -> list[dict[str, Any]]:
    """Collect domain tokens not covered by glossary matches, protected tokens, or stopwords."""
    solver_words = {alias for alias, _ in _SOLVER_PATTERNS}
    unmatched: list[dict[str, Any]] = []
    for m in re.finditer(r"[A-Za-z\u00C0-\u024F\uAC00-\uD7AF][0-9A-Za-z\u00C0-\u024F\uAC00-\uD7AF_-]*", nfkc_text):
        s, e = m.start(), m.end()
        if _overlaps(s, e, covered):
            continue
        token = m.group(0)
        low = _fold_accents(token).lower()
        if low in _EN_STOPWORDS or low in solver_words:
            continue
        if _HANGUL_RE.search(token):
            stem = _strip_ko_particle(token)
            if stem in _KO_STOPWORDS or token in _KO_STOPWORDS:
                continue
            if len(stem) <= 1 and stem in {"수", "것", "값", "식", "점", "선", "면", "축", "항", "형"}:
                continue
        orig_s = orig_map[s][0] if s < len(orig_map) else len(original_text)
        orig_e = orig_map[e - 1][1] if e - 1 < len(orig_map) else len(original_text)
        unmatched.append({"start": orig_s, "end": orig_e, "text": original_text[orig_s:orig_e]})
    return unmatched


def prepare_query(
    question: str,
    *,
    query_language: str = "auto",
    explicit_facets: Mapping[str, Any] | None = None,
    policy: str = QUERY_POLICY_ID,
) -> QueryPlan:
    """Create a bilingual QueryPlan preserving original_text verbatim and never dropping spans."""
    if not isinstance(question, str) or "\x00" in question or not question.strip():
        raise ValueError("질문을 입력하세요.")
    if not isinstance(policy, str) or not policy.strip():
        raise ValueError("query policy must be a nonempty string")

    original_text = question
    detected_lang = _detect_language(original_text, query_language)
    nfkc_text, orig_map = _nfkc_with_orig_map(original_text)
    protected = _protected_ranges(nfkc_text)
    term_spans, nfkc_matches = _match_glossary_spans(original_text, nfkc_text, orig_map, protected)
    covered_ranges = [*protected, *((s, e) for s, e, _c, _t in nfkc_matches)]
    unmatched_spans = _find_unmatched_spans(original_text, nfkc_text, orig_map, covered_ranges)

    warnings: list[str] = []
    if detected_lang == "unsupported":
        warnings.append("QUERY_LANGUAGE_UNSUPPORTED")
        warnings.append("LANGUAGE_COVERAGE_UNVERIFIED")
    elif unmatched_spans:
        warnings.append("LANGUAGE_COVERAGE_UNVERIFIED")

    queries: list[QueryVariant] = [
        QueryVariant(
            text=original_text,
            language=detected_lang,
            method="original",
            resource_revision=policy,
        )
    ]
    if nfkc_text != original_text:
        queries.append(
            QueryVariant(
                text=nfkc_text,
                language=detected_lang,
                method="nfkc",
                resource_revision=policy,
            )
        )
    if nfkc_matches and detected_lang != "unsupported":
        # Inline annotation places bilingual terms beside matched spans without deleting anything.
        inline_parts: list[str] = []
        cursor = 0
        expanded_bag: list[str] = []
        for s, e, _canonical, targets in nfkc_matches:
            inline_parts.append(nfkc_text[cursor:e])
            joined = " / ".join(targets)
            inline_parts.append(f" ({joined})")
            expanded_bag.extend(targets)
            cursor = e
        inline_parts.append(nfkc_text[cursor:])
        inline_text = "".join(inline_parts)
        if inline_text != original_text:
            queries.append(
                QueryVariant(
                    text=inline_text,
                    language="mixed",
                    method="glossary_inline",
                    resource_revision=GLOSSARY_REVISION,
                )
            )
        # Cross-lingual variant keeps original protected tokens + expanded domain targets.
        dedup_targets = list(dict.fromkeys(expanded_bag))
        protected_texts = [nfkc_text[s:e] for s, e in sorted(set(protected))]
        cross_parts = list(dict.fromkeys([*protected_texts, *dedup_targets]))
        if explicit_facets:
            for key in ("solver", "edition", "model", "unit"):
                val = explicit_facets.get(key)
                if isinstance(val, str) and val.strip():
                    cross_parts.append(val.strip())
        cross_text = " ".join(cross_parts).strip()
        if cross_text and cross_text not in {q.text for q in queries}:
            target_lang = "en" if detected_lang == "ko" else ("ko" if detected_lang == "en" else "mixed")
            queries.append(
                QueryVariant(
                    text=cross_text,
                    language=target_lang,
                    method="glossary_cross_lingual",
                    resource_revision=GLOSSARY_REVISION,
                )
            )

    return QueryPlan(
        original_text=original_text,
        original_language=detected_lang,
        queries=tuple(queries),
        policy_revision=policy,
        term_spans=tuple(term_spans),
        unmatched_spans=tuple(unmatched_spans),
        removed_spans=(),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def extract_query_requirements(
    plan: QueryPlan,
    explicit_facets: Mapping[str, Any] | None = None,
) -> tuple[RetrievalRequirementV1, ...]:
    """Derive host RetrievalRequirementV1 records preserving topic, solver, edition, and negations."""
    text = plan.original_text
    reqs: list[RetrievalRequirementV1] = [
        RetrievalRequirementV1(
            requirement_id="req-topic",
            original_span=(0, len(text)),
            facet="topic",
            expected=text,
            status="unverified",
            reason="질문 전체 주제와 조건의 문헌 일치 여부 미검토",
        )
    ]

    facets = dict(explicit_facets or {})
    # Auto-detect solver if not explicitly passed
    if not facets.get("solver"):
        low = text.lower()
        for alias, canonical in _SOLVER_PATTERNS:
            m = re.search(rf"(?<![0-9a-z]){re.escape(alias)}(?![0-9a-z])", low)
            if m:
                facets["solver"] = canonical
                reqs.append(
                    RetrievalRequirementV1(
                        requirement_id="req-solver",
                        original_span=(m.start(), m.end()),
                        facet="solver",
                        expected=text[m.start():m.end()],
                        status="unverified",
                        reason=f"명시된 솔버 '{canonical}' 일치 확인 필요",
                    )
                )
                break
    else:
        solver_val = str(facets["solver"]).strip()
        idx = text.lower().find(solver_val.lower())
        span = (idx, idx + len(solver_val)) if idx >= 0 else (0, len(text))
        reqs.append(
            RetrievalRequirementV1(
                requirement_id="req-solver",
                original_span=span,
                facet="solver",
                expected=solver_val,
                status="unverified",
                reason=f"지정 솔버 '{solver_val}' 일치 확인 필요",
            )
        )

    if facets.get("edition"):
        ed = str(facets["edition"]).strip()
        idx = text.lower().find(ed.lower())
        span = (idx, idx + len(ed)) if idx >= 0 else (0, len(text))
        reqs.append(
            RetrievalRequirementV1(
                requirement_id="req-edition",
                original_span=span,
                facet="edition",
                expected=ed,
                status="unverified",
                reason=f"지정 판본 '{ed}' 일치 확인 필요",
            )
        )
    else:
        m_ed = re.search(r"\bv?\d+\.\d+(?:\.\d+)+\b", text)
        if m_ed:
            reqs.append(
                RetrievalRequirementV1(
                    requirement_id="req-edition",
                    original_span=(m_ed.start(), m_ed.end()),
                    facet="edition",
                    expected=m_ed.group(0),
                    status="unverified",
                    reason=f"명시된 판본 '{m_ed.group(0)}' 일치 확인 필요",
                )
            )

    for neg_idx, m in enumerate(
        re.finditer(r"\b(?:not|no|without|never|except|unless)\b|(?:" + _KO_NEGATION_RE.pattern + ")", text, re.I)
    ):
        reqs.append(
            RetrievalRequirementV1(
                requirement_id=f"req-negation-{neg_idx}",
                original_span=(m.start(), m.end()),
                facet="negation",
                expected=m.group(0),
                status="unverified",
                reason="부정·예외 조건 보존 및 반례 검토 필요",
            )
        )

    for key in ("model", "unit", "measure", "condition", "comparison"):
        val = facets.get(key)
        if isinstance(val, str) and val.strip():
            idx = text.lower().find(val.strip().lower())
            span = (idx, idx + len(val.strip())) if idx >= 0 else (0, len(text))
            reqs.append(
                RetrievalRequirementV1(
                    requirement_id=f"req-{key}",
                    original_span=span,
                    facet=key,
                    expected=val.strip(),
                    status="unverified",
                    reason=f"명시 조건 '{key}={val.strip()}' 확인 필요",
                )
            )

    return tuple(reqs)


def classify_non_cae_intent(plan: QueryPlan) -> tuple[bool, str]:
    """Return (is_non_cae, reason) ONLY for manifestly non-CAE questions without domain facets."""
    if plan.term_spans:
        return False, ""
    low = plan.original_text.lower()
    if any(
        re.search(rf"(?<![0-9a-z]){re.escape(alias)}(?![0-9a-z])", low)
        for alias, _ in _SOLVER_PATTERNS
    ):
        return False, ""
    if re.search(r"\*[A-Za-z]+|LAW\d+|C3D\d+|FEM|FEA|CAE|PDE", plan.original_text, re.I):
        return False, ""
    for category, pattern in _NON_CAE_PATTERNS:
        if pattern.search(plan.original_text):
            return True, f"CAE 영역과 무관한 일반 요청({category})으로 확인되었습니다."
    return False, ""


def _candidate_metadata(candidate: CorpusChunkSpec | Mapping[str, Any]) -> tuple[str, str, str, tuple[str, ...], str]:
    if isinstance(candidate, CorpusChunkSpec):
        solver_scope = tuple(str(x) for x in candidate.coverage_facets.get("solver", ()))
        return candidate.citation, candidate.document_sha256, candidate.edition, solver_scope, candidate.text
    cid = str(candidate.get("citation") or f"{candidate.get('source_id', 'unknown')}:{candidate.get('chunk_id', 'unknown')}")
    sha = str(candidate.get("document_sha256") or candidate.get("source_sha256") or ("0" * 64))
    edition = str(candidate.get("edition") or "unknown")
    facets = candidate.get("coverage_facets") or {}
    scope = tuple(str(x) for x in (facets.get("solver") or candidate.get("solver_scope") or ()))
    text = str(candidate.get("text") or "")
    return cid, sha, edition, scope, text


def build_retrieval_decision(
    plan: QueryPlan,
    candidates: Sequence[CorpusChunkSpec | Mapping[str, Any]],
    *,
    source_review_refs: Sequence[ArtifactRefV1 | Mapping[str, Any]] = (),
    assessment_profile_id: str = "unconfigured",
    assessment_profile_sha256: str | None = None,
    explicit_facets: Mapping[str, Any] | None = None,
    assessment_result: Mapping[str, Any] | None = None,
    assessment_trace_ref: ArtifactRefV1 | Mapping[str, Any] | None = None,
    candidate_solver_scopes: Mapping[str, Sequence[str]] | None = None,
) -> RetrievalDecisionV1:
    """Build RetrievalDecisionV1 enforcing source review + semantic verification before 'supported'."""
    query_plan_sha = canonical_sha256(plan.to_dict())
    reqs = extract_query_requirements(plan, explicit_facets)

    cand_summary = []
    for cand in candidates:
        cid, sha, edition, scope, _text = _candidate_metadata(cand)
        cand_summary.append({"candidate_id": cid, "source_sha256": sha, "edition": edition, "solver_scope": list(scope)})
    cand_set_sha = canonical_sha256(cand_summary)

    req_solver = next((r.expected for r in reqs if r.facet == "solver"), None)
    if req_solver:
        req_solver_canon = next(
            (canon for alias, canon in _SOLVER_PATTERNS if alias == req_solver.lower()),
            req_solver.lower(),
        )
    else:
        req_solver_canon = None
    req_edition = next((r.expected for r in reqs if r.facet == "edition"), None)

    missing_evidence: list[str] = []
    limitations: list[str] = ["model_general_unverified"]
    checks: list[RetrievalCandidateCheckV1] = []

    if "QUERY_LANGUAGE_UNSUPPORTED" in plan.warnings:
        missing_evidence.append("QUERY_LANGUAGE_UNSUPPORTED")
    if "LANGUAGE_COVERAGE_UNVERIFIED" in plan.warnings:
        missing_evidence.append("LANGUAGE_COVERAGE_UNVERIFIED")

    # Non-CAE check only when no CAE terms/facets are present.
    is_non_cae, non_cae_reason = classify_non_cae_intent(plan)
    if is_non_cae and not (explicit_facets and any(explicit_facets.get(k) for k in ("solver", "model", "edition"))):
        missing_evidence.append("NON_CAE_REQUEST")
        limitations.append(non_cae_reason)
        assessment_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"cae-retrieval:{query_plan_sha}:{cand_set_sha}:oos"))
        return RetrievalDecisionV1(
            assessment_id=assessment_id,
            query_plan_sha256=query_plan_sha,
            candidate_set_sha256=cand_set_sha,
            source_review_refs=tuple(source_review_refs),
            assessment_profile_id=assessment_profile_id,
            assessment_profile_sha256=assessment_profile_sha256,
            status="out_of_scope",
            requirements=reqs,
            candidate_checks=(),
            missing_evidence=tuple(dict.fromkeys(missing_evidence)),
            limitations=tuple(dict.fromkeys(limitations)),
            assessment_trace_ref=assessment_trace_ref,
        )

    if not candidates:
        missing_evidence.append("NO_CANDIDATES")

    solver_mismatch_count = 0
    edition_mismatch_count = 0
    for cand in candidates:
        cid, sha, edition, scope, _text = _candidate_metadata(cand)
        if candidate_solver_scopes and cid in candidate_solver_scopes:
            scope = tuple(str(x) for x in candidate_solver_scopes[cid])
        solver_ok = True
        if req_solver_canon:
            norm_scope = {s.lower() for s in scope}
            solver_ok = req_solver_canon in norm_scope
        edition_ok = True
        if req_edition:
            edition_ok = req_edition.lower() in edition.lower()
        if not solver_ok:
            solver_mismatch_count += 1
            checks.append(
                RetrievalCandidateCheckV1(
                    candidate_id=cid,
                    source_sha256=sha,
                    relation="irrelevant",
                    requirement_ids=(),
                    quoted_spans=(),
                    reason=f"요청 솔버({req_solver_canon})와 후보 출처 범위({list(scope) or ['unknown']})가 다릅니다.",
                )
            )
        elif not edition_ok:
            edition_mismatch_count += 1
            checks.append(
                RetrievalCandidateCheckV1(
                    candidate_id=cid,
                    source_sha256=sha,
                    relation="irrelevant",
                    requirement_ids=(),
                    quoted_spans=(),
                    reason=f"요청 판본({req_edition})과 후보 판본({edition})이 일치하지 않습니다.",
                )
            )
        else:
            checks.append(
                RetrievalCandidateCheckV1(
                    candidate_id=cid,
                    source_sha256=sha,
                    relation="uncertain",
                    requirement_ids=tuple(r.requirement_id for r in reqs),
                    quoted_spans=(),
                    reason="어휘·혼합 검색 후보이며 질문별 의미 관련성 검토가 아직 실행되지 않았습니다.",
                )
            )

    if candidates and solver_mismatch_count == len(candidates):
        missing_evidence.append("SOLVER_COVERAGE_INSUFFICIENT")
    if candidates and edition_mismatch_count == len(candidates):
        missing_evidence.append("EDITION_COVERAGE_INSUFFICIENT")

    # If a validated semantic assessment payload is provided, strictly verify before considering 'supported'.
    if assessment_result is not None and source_review_refs and assessment_profile_sha256:
        out_of_scope = assessment_result.get("out_of_scope") or {}
        if out_of_scope.get("is_non_cae") is True and out_of_scope.get("original_spans"):
            status = "out_of_scope"
            missing_evidence.append("NON_CAE_REQUEST")
            limitations.append(str(out_of_scope.get("reason") or "비CAE 질의로 판정됨"))
        else:
            raw_checks = assessment_result.get("checks") or []
            by_req = {c.get("requirement_id"): c for c in raw_checks if isinstance(c, dict)}
            verified_reqs: list[RetrievalRequirementV1] = []
            cand_texts = {_candidate_metadata(c)[0]: _candidate_metadata(c)[4] for c in candidates}
            cand_shas = {_candidate_metadata(c)[0]: _candidate_metadata(c)[1] for c in candidates}
            updated_checks: list[RetrievalCandidateCheckV1] = []
            relevant_cids: dict[str, list[tuple[str, int, int]]] = {}
            all_satisfied = len(raw_checks) == len(reqs) and set(by_req) == {r.requirement_id for r in reqs}
            for r in reqs:
                chk = by_req.get(r.requirement_id)
                if chk is None or chk.get("relation") != "satisfied" or not chk.get("quoted_spans"):
                    all_satisfied = False
                    verified_reqs.append(r)
                    continue
                valid_quotes = False
                for q in chk.get("quoted_spans", []):
                    cid = q.get("candidate_id")
                    qs, qe = q.get("start"), q.get("end")
                    ctext = cand_texts.get(cid, "")
                    if isinstance(qs, int) and isinstance(qe, int) and 0 <= qs < qe <= len(ctext):
                        valid_quotes = True
                        relevant_cids.setdefault(cid, []).append((r.requirement_id, qs, qe))
                if not valid_quotes:
                    all_satisfied = False
                    verified_reqs.append(r)
                else:
                    verified_reqs.append(
                        RetrievalRequirementV1(
                            requirement_id=r.requirement_id,
                            original_span=r.original_span,
                            facet=r.facet,
                            expected=r.expected,
                            status="verified",
                            reason=str(chk.get("reason") or "관련성 검토 통과"),
                        )
                    )
            if all_satisfied and relevant_cids and solver_mismatch_count < len(candidates) and edition_mismatch_count < len(candidates):
                for cid, entries in relevant_cids.items():
                    updated_checks.append(
                        RetrievalCandidateCheckV1(
                            candidate_id=cid,
                            source_sha256=cand_shas[cid],
                            relation="relevant",
                            requirement_ids=tuple(dict.fromkeys(e[0] for e in entries)),
                            quoted_spans=tuple({"start": e[1], "end": e[2]} for e in entries),
                            reason="질문별 관련성 검토 및 출처 검증 통과",
                        )
                    )
                reqs = tuple(verified_reqs)
                checks = updated_checks
                status = "supported"
                limitations = [str(x) for x in (assessment_result.get("limitations") or ())]
            else:
                missing_evidence.append("RETRIEVAL_REQUIREMENTS_UNVERIFIED")
                status = "insufficient"
    else:
        missing_evidence.append("RETRIEVAL_RELEVANCE_UNVERIFIED")
        status = "insufficient"

    assessment_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"cae-retrieval:{query_plan_sha}:{cand_set_sha}:{status}"))
    return RetrievalDecisionV1(
        assessment_id=assessment_id,
        query_plan_sha256=query_plan_sha,
        candidate_set_sha256=cand_set_sha,
        source_review_refs=tuple(source_review_refs),
        assessment_profile_id=assessment_profile_id,
        assessment_profile_sha256=assessment_profile_sha256,
        status=status,
        requirements=tuple(reqs),
        candidate_checks=tuple(checks),
        missing_evidence=tuple(dict.fromkeys(missing_evidence)),
        limitations=tuple(dict.fromkeys(limitations)),
        assessment_trace_ref=assessment_trace_ref,
    )
