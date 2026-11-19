"""Korean/English CAE query planning, terminology expansion, and RetrievalDecisionV1 helpers."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from typing import Any
import uuid

from ..artifact_refs import ArtifactRefV1, canonical_sha256, validate_relative_artifact_path
from .contracts import (
    QUERY_POLICY_ID,
    CorpusChunkSpec,
    QueryPlan,
    QueryVariant,
    RetrievalCandidateCheckV1,
    RetrievalDecisionV1,
    RetrievalRequirementV1,
    RetrievalTraceRecord,
    _RETRIEVAL_STAGES,
    _RETRIEVAL_TERMINAL_STAGES,
    _is_retrieval_event,
    _retrieval_stage,
    _validate_embedding_provider,
)

__all__ = [
    "GLOSSARY_REVISION",
    "classify_non_cae_intent",
    "extract_query_requirements",
    "prepare_query",
    "build_retrieval_decision",
]

GLOSSARY_REVISION = "cae-glossary-ko-en-v2"

# Curated bilingual Korean <-> English CAE terminology pairs (longest match applied at runtime).
_GLOSSARY_PAIRS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("푸아송 문제", "포아송 문제", "푸아송 방정식", "포아송 방정식"), ("Poisson equation",)),
    (("노이만 경계항", "자연 경계항", "경계 적분"), ("Neumann boundary term", "boundary integral")),
    (("법선 미분", "외향 법선 미분"), ("normal derivative",)),
    (("병진 강체 모드", "병진"), ("translation", "translational rigid mode")),
    (("회전 강체 모드", "회전"), ("rotation", "rotational rigid mode")),
    (("삼차원", "3차원"), ("three-dimensional",)),
    (("작은 변형", "미소 변형률"), ("small strain", "infinitesimal strain")),
    (("등방탄성", "등방 탄성"), ("isotropic elasticity",)),
    (("정수압 응력", "정수압응력", "평균 응력"), ("hydrostatic stress", "mean stress")),
    (("응력 편차", "편차 텐서"), ("stress deviator", "deviatoric tensor")),
    (("압축성 네오후크", "압축성 신후크"), ("compressible neo-Hookean",)),
    (("변형 에너지 밀도", "변형률 에너지 밀도"), ("strain energy density", "stored energy density")),
    (("변형 구배 행렬식", "체적비"), ("determinant of the deformation gradient", "Jacobian")),
    (("일차 피올라 응력", "제1 피올라 응력"), ("first Piola stress", "first Piola-Kirchhoff stress")),
    (("응력 밀어올리기",), ("stress push-forward", "push forward to Cauchy stress")),
    (("dpsi/dF", "에너지의 변형 구배 미분"), ("diff(psi, F)", "energy derivative with respect to F")),
    (("1-sum(alpha)", "프로니 평형 분율"), ("equilibrium fraction", "normalized equilibrium modulus")),
    (("PK1->Cauchy", "제1 피올라 응력에서 코시 응력으로"), ("first Piola to Cauchy stress", "stress push-forward")),
    (("시험응력", "시험 응력"), ("trial stress", "trial stress invariants")),
    (("시험 편차응력", "시험 편차 응력 불변량"), ("trial deviatoric stress", "trialQ")),
    (("시험 평균응력", "시험 압력"), ("trial pressure", "trialP")),
    (("이전 점착력", "기존 점착력", "이전 응집력"), ("old cohesion", "oldCohesion")),
    (("항복판정", "항복 판정"), ("yield test", "yield function")),
    (("비연관 유동", "비연관 흐름"), ("non-associated flow", "nonassociative flow")),
    (("정규화 이완계수", "정규화 이완 탄성계수"), ("normalized relaxation modulus",)),
    (("장시간 잔류값", "장시간 평형 탄성계수"), ("long-time relaxation modulus", "equilibrium modulus", "E_inf")),
    (("정규화 프로니 계수", "정규화 이완 강도"), ("normalized Prony coefficients", "alpha")),
    (("각진동수", "각 주파수", "각주파수"), ("angular frequency", "omega")),
    (("배응력", "역응력", "후방 응력"), ("backstress", "back stress")),
    (("샤보슈", "샤보쉬"), ("Chaboche",)),
    (("6성분 이력", "여섯 성분 이력"), ("six-component history", "six-component tensor")),
    (("내부 변수", "내부 이력 변수"), ("internal variable", "history array")),
    (("스칼라",), ("scalar",)),
    (("배응력 부호", "역응력 부호"), ("backstress sign convention",)),
    (("연화",), ("softening",)),
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

_DASHES = str.maketrans(dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"))
_POSSESSIVE = re.compile(r"(\w+)['’]s\b", re.I)
_CARD_MULTIWORD = (
    "SOLID SECTION", "SHELL SECTION", "BEAM SECTION", "CONTACT PAIR", "SURFACE INTERACTION",
    "SURFACE BEHAVIOR", "NODE PRINT", "NODE FILE", "EL PRINT", "EL FILE", "END STEP",
    "INITIAL CONDITIONS", "MASS FLOW", "GAP CONDUCTANCE", "COUPLED TEMPERATURE-DISPLACEMENT",
)
_CARD = r"\*(?:" + "|".join(word.replace(" ", r"[ \t]+") for word in _CARD_MULTIWORD) + r"|[A-Z][A-Z0-9_]*)(?![A-Za-z0-9_])"
_SOLVER_MENTION = re.compile(
    r"(?<![0-9a-z])(?:" + "|".join(re.escape(alias) for alias, _ in _SOLVER_PATTERNS) + r")(?![0-9a-z])",
    re.I,
)
_EDITION_RE = re.compile(r"(?<![0-9A-Za-z_.])v?\d+(?:\.\d+)*(?![0-9A-Za-z]|\.\d)", re.I)
_VERSION_LABEL = r"(?:\b(?:versions?|ver\.?|editions?|releases?)|버전|판본)"
_EDITION_BEFORE_SOLVER = (
    rf"(?:{_VERSION_LABEL}(?:\s*[:=]\s*|\s+)\d+|v\d+(?:\.\d+)*|\d+(?:\.\d+)+)"
)
_PRE_SOLVER_EDITIONS = re.compile(
    rf"(?<![\w.]){_EDITION_BEFORE_SOLVER}(?:\s+(?:vs\.?|versus|and|to)\s+{_EDITION_BEFORE_SOLVER})*"
    r"\s+(?:in|for|of|on|with)\s+$",
    re.I,
)
_PARENTHETIC_SOLVER_EDITION = re.compile(rf"(?<![\w.]){_EDITION_BEFORE_SOLVER}\s*\(\s*$", re.I)
_ENTITY_PREFIX = re.compile(
    r"\b(?:nodes?|elements?|parts?|steps?|modes?|frames?|pages?|lines?|cases?|runs?|ids?|laws?|types?|"
    r"columns?|rows?|sections?|sec|chapters?|chap|ch|clauses?|tables?|tab|figures?|fig|equations?|eq|"
    r"problems?|examples?|items?|articles?|appendix|appendices|절점|요소|파트|단계|페이지|절|장|항|표|그림|식|예제)"
    r"\s*(?:[.#:]\s*)?$", re.I,
)
_ENTITY_CONNECTOR = re.compile(r"\s*(?:(?:,\s*)?(?:and|or|to|through|vs\.?|versus|&|및|와|과)|,|/|-)\s*", re.I)
_UNIT_TOKEN = r"(?:MPa|GPa|kPa|Pa|mm|cm|km|mg|m|kg|kN|N|ms|s|Hz|rad/s|degC|K|J/mol|kJ/mol|bar|mbar|psi|ksi|atm|rpm|deg|rad|MN|mJ|kJ|W|kW|C)"
_EDITION_UNIT = re.compile(r"\s*(?:" + _UNIT_TOKEN + r"|%|°[CF])(?![0-9A-Za-z_])", re.I)
_VERSION_MARKER = re.compile(rf"{_VERSION_LABEL}\s*[:=]?\s*$", re.I)
_PROTECTED_TOKEN_RE = re.compile(
    _CARD
    + r"|(?<![A-Za-z0-9])(?i:3D|2D|1D)(?![A-Za-z0-9_])"
    r"|(?<![0-9A-Za-z])(?:[vV]\d+\.\d+(?:\.\d+)*|\d+(?:\.\d+){2,})(?!\.\d)"
    r"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
    r"|<=|>=|!=|==|<|>|="
    + r"|(?<![A-Za-z'’])(?i:" + _UNIT_TOKEN + r"|PK1|VM|WLF|RF|LAW\d+|C3D\d+)(?![A-Za-z0-9_])"
    r"|(?i:\b(?:not|no|without|never|except|unless|only|before|after|when|if)\b)"
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
        "compare",
        "comparing",
        "versus",
        "vs",
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
    ("cooking", re.compile(r"(?:요리|레시피|음식|김치찌개|파스타|recipe|조리법|(?<!johnson[- ])\bcook(?:ing)?\b|baking|ingredients)", re.I)),
    ("price_shopping", re.compile(r"(?:가격|할인|쇼핑|price|discount|buy\s+a|how\s+much\s+does)", re.I)),
    ("sports", re.compile(r"(?:축구|야구|농구|스포츠|경기\s*결과|soccer|football|baseball|basketball|sports\s+score)", re.I)),
    ("translation", re.compile(r"(?:번역해|영어로\s*번역|translate\b|translation\s+of)", re.I)),
    ("license_admin", re.compile(r"(?:운전면허|driver'?s\s+license)", re.I)),
    ("hospitality", re.compile(r"(?:식당|맛집|호텔|예약|restaurant|hotel|reservation|book\s+a\s+room)", re.I)),
    ("finance", re.compile(r"(?:은행|계좌|주식|주가|선거|투표|bank\s+account|stock\s+price|exchange rate|credit card|신용카드|election)", re.I)),
    ("personal_misc", re.compile(r"(?:축사|비밀번호|패스워드|이메일\s*작성|메일\s*써|체스|영화\s*추천|아기\s*이름|강아지\s*이름|이름\s*지어|speech|password|chess|movie\s+recommend|baby\s+name|write\s+an\s+email|이메일.{0,12}보내|graduation toast|wedding toast|haircut|movie reviews|(?:고양이)\s*이름|여권\s*갱신|sightseeing itinerary|\bcafe\b)", re.I)),
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
    search_text = text.translate(_DASHES)
    for idx, ch in enumerate(search_text):
        norm = unicodedata.normalize("NFKC", ch)
        for sub in norm:
            out_chars.append(sub)
            mapping.append((idx, idx + 1))
    normalized = unicodedata.normalize("NFKC", search_text)
    if normalized == "".join(out_chars):
        return normalized, mapping

    # Composition across original code points (for example decomposed Hangul)
    # maps the composed character to the whole original range.
    prefix = ""
    mapping = []
    for index, char in enumerate(search_text):
        updated = unicodedata.normalize("NFKC", prefix + char)
        common = len(prefix)
        while common and prefix[:common] != updated[:common]:
            common -= 1
        start = mapping[common][0] if common < len(mapping) else index
        mapping[common:] = [(start, index + 1)] * (len(updated) - common)
        prefix = updated
    return normalized, mapping


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
    # Match against a fixed-width view so possessive suffixes never move source spans.
    haystack_en = _POSSESSIVE.sub(lambda m: m[1] + " " * (len(m[0]) - len(m[1])), haystack_en)

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
            phrase = r"\s+".join(re.escape(word) for word in term.split(" "))
            pattern = re.compile(rf"(?<![0-9A-Za-z]){phrase}(?![0-9A-Za-z])", re.IGNORECASE)
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
        if _overlaps(s, e, covered) or (m[0].lower() == "s" and s and nfkc_text[s - 1] in "'’"):
            continue
        token = m.group(0)
        low = _fold_accents(token).lower()
        if low in _EN_STOPWORDS or low in solver_words:
            continue
        if _HANGUL_RE.search(token):
            stem = _strip_ko_particle(token)
            if stem in _KO_STOPWORDS or token in _KO_STOPWORDS or token in _KO_PARTICLES:
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
    nfkc_text, orig_map = _nfkc_with_orig_map(original_text)
    detected_lang = _detect_language(nfkc_text, query_language)
    protected = _protected_ranges(nfkc_text)
    term_spans, nfkc_matches = _match_glossary_spans(original_text, nfkc_text, orig_map, protected)
    for mention in _SOLVER_MENTION.finditer(nfkc_text):
        start, end = orig_map[mention.start()][0], orig_map[mention.end() - 1][1]
        if not any(start < span["end"] and span["start"] < end for span in term_spans):
            solver = normalize_solver(mention[0])
            term_spans.append({"start": start, "end": end, "source_text": original_text[start:end],
                               "matched_term": solver, "expanded_terms": [solver], "target_language": "en"})
    term_spans.sort(key=lambda span: span["start"])
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
        # Keep complete expressions intact, including unmatched operands and operators.
        cross_text = f"{nfkc_text} {' '.join(dict.fromkeys(expanded_bag))}"
        if cross_text not in {q.text for q in queries}:
            queries.append(
                QueryVariant(
                    text=cross_text,
                    language="mixed",
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
    return extract_query_scope(plan, explicit_facets)[0]


def extract_query_scope(plan, explicit_facets=None):
    """Keep edition-to-solver ownership alongside the frozen requirement records."""
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
    normalized, original_offsets = _nfkc_with_orig_map(text)
    mentions = list(_SOLVER_MENTION.finditer(normalized))
    solvers = {}
    for match in mentions:
        solver = normalize_solver(match[0])
        solvers.setdefault(solver, match.span())
    solver = normalize_solver(facets.get("solver"))
    if solver and solver != "general":
        solvers.setdefault(solver, None)

    def original_span(start, end):
        return original_offsets[start][0], original_offsets[end - 1][1]

    for index, (solver, span) in enumerate(solvers.items()):
        surface_span = original_span(*span) if span is not None else None
        reqs.append(RetrievalRequirementV1(
            requirement_id="req-solver" if index == 0 else f"req-solver-{index}",
            original_span=surface_span, facet="solver",
            expected=text[surface_span[0]:surface_span[1]] if surface_span is not None else solver,
            status="unverified", reason=f"명시된 솔버 '{solver}' 일치 확인 필요",
        ))

    editions = {}
    pre_solver_editions = {}

    def has_version_marker(number, prefix):
        return number.lower().startswith("v") or bool(_VERSION_MARKER.search(prefix))

    def is_entity_number(number, prefix):
        return bool(_ENTITY_PREFIX.search(prefix)) and not has_version_marker(number, prefix)

    edition_matches = list(_EDITION_RE.finditer(normalized))
    entity_spans: set[tuple[int, int]] = set()
    last_entity_end = None
    for match in edition_matches:
        prefix = normalized[:match.start()]
        continues_entity = (last_entity_end is not None
                            and _ENTITY_CONNECTOR.fullmatch(normalized[last_entity_end:match.start()])
                            and not has_version_marker(match[0], prefix))
        if is_entity_number(match[0], prefix) or continues_entity:
            entity_spans.add(match.span())
            last_entity_end = match.end()
        else:
            last_entity_end = None

    for mention in mentions:
        prefix = normalized[:mention.start()]
        comparison = _PRE_SOLVER_EDITIONS.search(prefix)
        if comparison is None and re.match(r"\s*\)", normalized[mention.end():]):
            comparison = _PARENTHETIC_SOLVER_EDITION.search(prefix)
        if comparison is None:
            continue
        solver = normalize_solver(mention[0])
        pre_solver_editions.update(
            (number.span(), solver)
            for number in _EDITION_RE.finditer(normalized, comparison.start(), comparison.end())
            if number.span() not in entity_spans
        )

    last_edition_end = None
    last_owner = None
    for match in edition_matches:
        if match.span() in entity_spans:
            last_edition_end = None
            continue
        if _EDITION_UNIT.match(normalized[match.end():]):
            continue
        prefix = normalized[:match.start()]
        explicit_version = has_version_marker(match[0], prefix)
        continued = last_edition_end is not None and re.fullmatch(
            r"\s*(?:(?:,\s*)?(?:and|or|versus|vs\.?|및|와|과)|,|/)\s*", prefix[last_edition_end:], re.I,
        )
        preceding = next((m for m in reversed(mentions) if m.end() <= match.start()), None)
        gap = prefix[preceding.end():].strip(" \t,(:") if preceding else None
        attached = preceding is not None and (not gap or _VERSION_MARKER.fullmatch(gap))
        if match.span() in pre_solver_editions or continued or explicit_version or attached:
            version = match[0]
            owner = pre_solver_editions.get(match.span())
            if owner is None:
                owner = normalize_solver(preceding[0]) if attached else last_owner if continued else None
            last_edition_end = match.end()
            last_owner = owner
            if not any(editions_match(version, current) and owner == bound for current, bound in editions):
                editions[version, owner] = original_span(*match.span())
    edition = facets.get("edition")
    if isinstance(edition, str) and edition.strip():
        edition = edition.strip()
        if not any(editions_match(edition, current) for current, _ in editions):
            editions[edition, None] = None
    edition_owners = {}
    for index, ((edition, owner), span) in enumerate(editions.items()):
        rid = "req-edition" if index == 0 else f"req-edition-{index}"
        if owner:
            edition_owners[rid] = owner
        reqs.append(RetrievalRequirementV1(
            requirement_id=rid,
            original_span=span, facet="edition", expected=edition,
            status="unverified", reason=f"명시된 판본 '{edition}' 일치 확인 필요" + (f" ({owner})" if owner else ""),
        ))

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
            span = (idx, idx + len(val.strip())) if idx >= 0 else None
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

    return tuple(reqs), edition_owners


def match_query_scope(requirements, edition_owners, scope, edition):
    mentioned = {normalize_solver(req.expected) for req in requirements
                 if req.facet == "solver" and req.original_span is not None}
    facets = {normalize_solver(req.expected) for req in requirements
              if req.facet == "solver" and req.original_span is None}
    solver_ok = facets <= scope and (not mentioned or bool(mentioned & scope))
    editions = [req for req in requirements if req.facet == "edition"]
    matched = {
        req.requirement_id for req in editions if editions_match(req.expected, edition)
        and (req.requirement_id not in edition_owners or edition_owners[req.requirement_id] in scope)
    }
    paired_solvers = set(edition_owners.values())
    matched_solvers = (scope - paired_solvers) | {
        edition_owners[rid] for rid in matched if rid in edition_owners
    }
    unpaired_solver = bool((mentioned | facets) & (scope - paired_solvers))
    edition_ok = not editions or bool(matched) or (
        len(edition_owners) == len(editions) and unpaired_solver
    )
    edition_ok = edition_ok and facets <= matched_solvers and (
        not mentioned or bool(mentioned & matched_solvers)
    )
    return solver_ok, edition_ok, matched_solvers, matched


def normalize_solver(solver: str | None) -> str | None:
    if not solver:
        return None
    normalized = unicodedata.normalize("NFKC", solver.translate(_DASHES)).strip().casefold()
    return dict(_SOLVER_PATTERNS).get(normalized, normalized)


def editions_match(left: str | None, right: str | None) -> bool:
    if any(not value or value.strip().casefold() in {"", "unknown", "legacy_unknown"} for value in (left, right)):
        return False

    def normalized(value):
        value = value.strip().casefold()
        return value.removeprefix("v") if _EDITION_RE.fullmatch(value) else value

    return normalized(left) == normalized(right)


def classify_non_cae_intent(plan: QueryPlan) -> tuple[bool, str]:
    """Return (is_non_cae, reason) ONLY for manifestly non-CAE questions without domain facets."""
    if plan.term_spans:
        return False, ""
    text = unicodedata.normalize("NFKC", plan.original_text.translate(_DASHES))
    low = text.lower()
    if any(
        re.search(rf"(?<![0-9a-z]){re.escape(alias)}(?![0-9a-z])", low)
        for alias, _ in _SOLVER_PATTERNS
    ):
        return False, ""
    if re.search(r"\*[A-Za-z]+|LAW\d+|C3D\d+|\b(?:FEM|FEA|CAE|PDE|beam|membrane)\b", text, re.I):
        return False, ""
    for category, pattern in _NON_CAE_PATTERNS:
        if pattern.search(text):
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
    documented = facets.get("solver")
    declared = candidate.get("solver_scope") or candidate.get("solver")
    if isinstance(documented, str):
        documented = (documented,)
    if isinstance(declared, str):
        declared = (declared,)
    scope = tuple(str(x) for x in (documented or declared or ()))
    if documented and declared:
        declared = {normalize_solver(value) for value in declared}
        scope = tuple(value for value in scope if normalize_solver(value) in declared)
    text = str(candidate.get("text") or "")
    return cid, sha, edition, scope, text


def _has_valid_source_span(candidate: CorpusChunkSpec | Mapping[str, Any]) -> bool:
    try:
        _candidate_source_span(candidate)
        if isinstance(candidate, CorpusChunkSpec):
            return (all(type(value) is int for value in (
                        candidate.line_start, candidate.line_end, candidate.char_start, candidate.char_end,
                    ))
                    and 1 <= candidate.line_start <= candidate.line_end
                    and 0 <= candidate.char_start < candidate.char_end
                    and candidate.char_end - candidate.char_start == len(candidate.text)
                    and ((candidate.page_start is None and candidate.page_end is None)
                         or (type(candidate.page_start) is int and type(candidate.page_end) is int
                             and 1 <= candidate.page_start <= candidate.page_end)))
        lines = (candidate.get("start_line", candidate.get("line_start")),
                 candidate.get("end_line", candidate.get("line_end")))
        chars = candidate["char_start"], candidate["char_end"]
        text = candidate["text"]
        return (
            all(type(value) is int for value in (*lines, *chars))
            and 1 <= lines[0] <= lines[1]
            and 0 <= chars[0] < chars[1]
            and isinstance(text, str) and chars[1] - chars[0] == len(text)
            and candidate["text_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
        )
    except (KeyError, ValueError):
        return False


_TOPIC_GAPS = {
    "radioss_cards": "missing_card_or_parameter",
    "calculix_cards": "missing_card_or_parameter",
    "contact_law": "missing_solver",
    "damage_evolution": "missing_model_variant_or_original_source",
    "material_identification": "missing_identification_data",
    "safety_certification": "missing_safety_evidence",
    "creep": "missing_material_test_data",
    "anisotropy": "missing_model_variant_or_original_source",
    "thermal": "missing_temperature_dependent_data",
    "history": "unsupported_coupling",
    "restart": "missing_restart_validation",
    "buckling": "missing_buckling_model_or_result",
    "measures": "missing_result_measure",
    "mesh_quality": "missing_mesher_coverage",
    "run_values": "missing_run_evidence",
}
_TOPIC_SIGNALS = {
    "contact_law": r"\bcontact\b.*\bpenalty\b|접촉.*페널티|접촉.*penalty",
    "damage_evolution": r"\bxia\b",
    "material_identification": r"\bidentifia\w+|유일성|계수.*유일",
    "safety_certification": r"\bcertif\w+|안전함.*승인|안전.*승인",
    "creep": r"\bnorton\b",
    "thermal": r"temperature[- ]dependen|온도\s*의존",
    "history": r"점성\s*(?:이력|내부상태)|viscous\s*(?:history|state)",
    "restart": r"\brestart\b|재시작",
    "buckling": r"\bbuckling\b|좌굴",
    "measures": r"engineering shear|tensor shear",
    "mesh_quality": r"all[- ]hex",
    "run_values": r"첨부하지 않은.*실행|\bunattached\b.*\brun\b",
}


def _coverage_gaps(plan, candidates, explicit_facets):
    requested = (explicit_facets or {}).get("topics") or ()
    if isinstance(requested, str):
        requested = (requested,)
    text = plan.original_text
    topics = set(requested)
    topics.update(topic for topic, pattern in _TOPIC_SIGNALS.items() if re.search(pattern, text, re.I))
    if re.search(r"(?<![A-Za-z0-9_])(?:LAW\d+|RF)(?![A-Za-z0-9_])|" + _CARD, text, re.I):
        solvers = {normalize_solver(match[0]) for match in _SOLVER_MENTION.finditer(text)}
        topics.update(f"{solver}_cards" for solver in solvers if solver in {"radioss", "calculix"})
    if re.search(r"\b(?:radioss|opencourant)\b.*(?:LAW\d+|TYPE\d+)", text, re.I):
        topics.add("radioss_cards")
    supplied = set()
    for candidate in candidates:
        facets = candidate.coverage_facets if isinstance(candidate, CorpusChunkSpec) else candidate.get("coverage_facets") or {}
        covered_topics = facets.get("topics") or ()
        supplied.update((covered_topics,) if isinstance(covered_topics, str) else covered_topics)
        if isinstance(candidate, Mapping):
            covered_topics = candidate.get("topics") or ()
            supplied.update((covered_topics,) if isinstance(covered_topics, str) else covered_topics)
    return [_TOPIC_GAPS[topic] for topic in sorted(topics - supplied) if topic in _TOPIC_GAPS]


def _candidate_source_span(candidate):
    if isinstance(candidate, CorpusChunkSpec):
        return {"source_id": candidate.source_id, "source_sha256": candidate.document_sha256,
                "start_line": candidate.line_start, "end_line": candidate.line_end,
                "char_start": candidate.char_start, "char_end": candidate.char_end,
                "page_start": candidate.page_start, "page_end": candidate.page_end,
                "page_breaks": candidate.page_breaks}
    nested = candidate.get("source_span")
    if nested is not None and not isinstance(nested, Mapping):
        raise ValueError("Candidate source_span must be an object")
    fields = ("source_id", "source_sha256", "document_sha256", "start_line", "end_line",
              "line_start", "line_end", "char_start", "char_end", "byte_start", "byte_end",
              "page", "page_start", "page_end", "start_page", "end_page", "page_breaks")
    top = {key: candidate[key] for key in fields if key in candidate}
    aliases = (("start_line", "line_start"), ("end_line", "line_end"),
               ("source_sha256", "document_sha256"),
               ("page_start", "start_page"), ("page_end", "end_page"),
               ("page", "page_start"), ("page", "page_end"), ("page", "start_page"), ("page", "end_page"))
    combined = {}
    positive = {"start_line", "end_line", "line_start", "line_end", "page",
                "page_start", "page_end", "start_page", "end_page"}
    nonnegative = {"char_start", "char_end", "byte_start", "byte_end"}
    for span in (top, nested or {}):
        for key in fields:
            if key in span:
                value = span[key]
                if ((key in positive and not ("page" in key and value is None)
                     and (type(value) is not int or value < 1))
                        or (key in nonnegative and (type(value) is not int or value < 0))):
                    raise ValueError(f"Invalid candidate source_span position: {key}")
                if key in combined and combined[key] != span[key]:
                    raise ValueError(f"Candidate source_span conflict: {key}")
                combined[key] = span[key]
    for first, second in aliases:
        if first in combined and second in combined and combined[first] != combined[second]:
            raise ValueError(f"Candidate source_span conflict: {first}/{second}")
    for start, end in (
        (combined.get("start_line", combined.get("line_start")), combined.get("end_line", combined.get("line_end"))),
        (combined.get("start_page", combined.get("page_start")), combined.get("end_page", combined.get("page_end"))),
        (combined.get("char_start"), combined.get("char_end")),
        (combined.get("byte_start"), combined.get("byte_end")),
    ):
        if start is not None and end is not None and start > end:
            raise ValueError("Reversed candidate source_span range")
    return {"top": top, "nested": nested}


def _quote_page_bounds(positions, text):
    first = positions.get("page_start", positions.get("start_page", positions.get("page")))
    last = positions.get("page_end", positions.get("end_page", positions.get("page")))
    breaks = positions.get("page_breaks")
    if breaks is None:
        return {first: (0, len(text))} if first is not None and first == last else {}
    if (first is None or last is None or not isinstance(breaks, (tuple, list))
            or len(breaks) != last - first
            or any(type(offset) is not int or not 0 < offset < len(text) for offset in breaks)
            or sorted(breaks) != list(breaks)):
        raise ValueError("Invalid candidate page_breaks")
    return dict(zip(range(first, last + 1), zip((0, *breaks), (*breaks, len(text)))))


def _trace_blocks_support(value):
    states = {"decision", "status", "outcome", "relation", "scope_status", "retrieval_decision", "kind", "stage"}
    denied = ("insufficient", "out_of_scope", "missing", "unsatisfied", "uncertain",
              "cancelled", "timed_out", "failed", "error", "no_candidates")
    return (value.get("is_non_cae") is True or value.get("satisfied") is False
            or value.get("selected_chunk_ids") == []
            or any(value.get(key) for key in ("insufficiency_reasons", "error", "errors"))
            or any(key in value and not value[key] for key in ("checks", "requirement_checks"))
            or any((key in states or key.startswith("req-")) and item in denied for key, item in value.items()))


_QUOTATION_PROVENANCE = {"page", "page_start", "page_end", "section_path"}


def _section_path(value):
    if isinstance(value, str):
        value = value.split(" > ")
    if not isinstance(value, (tuple, list)) or any(not isinstance(part, str) or not part.strip() for part in value):
        raise ValueError("Invalid retrieval assessment citation section_path")
    return tuple(part.strip() for part in value)


def _validate_quote_provenance(value, provenance, span=None, *, require_page=False):
    pages, is_pdf, section_path = provenance
    if "section_path" in value and _section_path(value["section_path"]) != section_path:
        raise ValueError("Invalid retrieval assessment citation section_path")
    page = value.get("page")
    has_page = "page" in value and (is_pdf or page is not None)
    has_range = "page_start" in value or "page_end" in value
    if not (has_page or has_range):
        if require_page and is_pdf:
            raise ValueError("PDF retrieval assessment citation requires page provenance")
        return

    first, last = (value.get("page_start"), value.get("page_end")) if has_range else (page, page)
    if (type(first) is not int or type(last) is not int or first > last or first not in pages or last not in pages
            or (has_page and (type(page) is not int or not page == first == last))):
        raise ValueError("Invalid retrieval assessment citation or quotation page")
    if span is not None and not (
        pages[first][0] <= span[0] < pages[first][1]
        and pages[last][0] < span[1] <= pages[last][1]
        and span[0] < span[1]
    ):
        raise ValueError("Invalid retrieval assessment citation or quotation page")


_SELECTION_STAGES = {"candidate_selection", "select_candidates", "candidates_selected"}
_REVIEW_STAGES = {"assessment", "retrieval_assessment", "decision", "retrieval_decision",
                  "response", "retrieval_response", "requirement_checks"}
_FINAL_REVIEW_STAGES = _REVIEW_STAGES | {
    "retrieval_completion", "completion", "complete", "completed", "finalize", "terminal",
}
_CANDIDATE_SUBSET_STAGES = _SELECTION_STAGES | _FINAL_REVIEW_STAGES
_PREPARATION_STAGES = {"prepare_query", "query_plan", "retrieval_start"}
_INITIAL_STAGES = _PREPARATION_STAGES | {"retrieval"}
_SEARCH_STAGES = _PREPARATION_STAGES | {"search", "embed_query", "rank_candidates", "rerank"}
_EVIDENCE_FIELDS = {"checks", "requirement_checks", "citations", "quoted_spans"}
_TRACE_HISTORY_FIELDS = ("stage_events", "events", "steps")
_TRACE_RESPONSE_FIELDS = ("result", "output", "assessment", "decision", "response")
_RESULT_STAGES = (_RETRIEVAL_TERMINAL_STAGES | _REVIEW_STAGES) - _SELECTION_STAGES - {"supported"}


def _trace_records(value, phase=None, ancestors=frozenset(), *, root=False):
    if isinstance(value, list):
        for item in value:
            yield from _trace_records(item, phase, ancestors)
    elif isinstance(value, Mapping):
        if not root and phase not in {"search", "detail", "selection"}:
            stage = _retrieval_stage(value)
            if isinstance(stage, str):
                if stage in _SEARCH_STAGES:
                    phase = "search"
                elif stage in _SELECTION_STAGES:
                    phase = "selection"
                elif stage in _FINAL_REVIEW_STAGES:
                    phase = "review"
        # A mapping's history precedes its reports regardless of JSON key order.
        fields = (
            *(key for key in _TRACE_HISTORY_FIELDS if key in value),
            *sorted(set(value).difference(_TRACE_HISTORY_FIELDS, _TRACE_RESPONSE_FIELDS)),
            *(key for key in _TRACE_RESPONSE_FIELDS if key in value),
        )
        child_ancestors = ancestors | {id(value)}
        for key in fields:
            child_phase = phase
            if key in _TRACE_HISTORY_FIELDS:
                child_phase = None
            elif key in _EVIDENCE_FIELDS:
                child_phase = "detail"
            elif key in _SEARCH_STAGES:
                child_phase = "search"
            elif phase is None and key in _FINAL_REVIEW_STAGES:
                child_phase = "review"
            yield from _trace_records(value[key], child_phase, child_ancestors)
        yield value, "review" if root else phase, ancestors


def _validate_trace_sequence(events, has_stage_events):
    stages = [_retrieval_stage(event) for event in events]
    if stages[-1] not in (_RESULT_STAGES | {"retrieval"}):
        raise ValueError("Retrieval assessment trace events must end with a result")
    first_result = next((i for i, stage in enumerate(stages) if stage in _RESULT_STAGES), len(stages))
    if _PREPARATION_STAGES.intersection(stages[first_result + 1:]):
        raise ValueError("Retrieval assessment trace events must finish preparation before the first result")
    if has_stage_events:
        return
    work_steps = [i for i, stage in enumerate(stages) if stage == "search" or stage in _SELECTION_STAGES]
    if work_steps and (not _INITIAL_STAGES.intersection(stages[:work_steps[0]])
                       or not _RESULT_STAGES.intersection(stages[work_steps[-1] + 1:])):
        raise ValueError("Retrieval assessment trace events must start before search and record a terminal result")


def _strict_assessment(payload, ref, root, identity, requirements, texts, provenance, question):
    if ref is None or root is None:
        raise ValueError("A retrieval assessment requires assessment_trace_ref and its artifact root")
    if not isinstance(ref, ArtifactRefV1):
        ref = ArtifactRefV1.from_dict(ref)
    if ref.size <= 0 or ref.role != "retrieval_trace":
        raise ValueError("Retrieval assessment requires a non-empty retrieval trace artifact")
    root = Path(root)
    raw = (root / validate_relative_artifact_path(ref.path, root=root)).read_bytes()
    if len(raw) != ref.size or hashlib.sha256(raw).hexdigest() != ref.sha256:
        raise ValueError("Retrieval assessment trace digest or size mismatch")
    trace = json.loads(raw)
    if (not isinstance(trace, dict) or trace.get("schema") != "caelab.retrieval_trace.v1"
            or ref.format_id != "cae-retrieval-trace-v1" or "schema_version" in trace
            and (type(trace["schema_version"]) is not int or trace["schema_version"] != 1)):
        raise ValueError("Invalid retrieval assessment trace format")
    def validate_events(events):
        if not isinstance(events, list) or not events or not all(_is_retrieval_event(event) for event in events):
            raise ValueError("Invalid retrieval assessment trace events")
        return events

    events = validate_events(trace.get("events"))
    _validate_trace_sequence(events, "stage_events" in trace)

    all_candidate_ids = set(identity["candidate_ids"])

    def declares_subset(record):
        ids = record.get("candidate_ids")
        stage = _retrieval_stage(record)
        return (record is not trace and isinstance(ids, list) and all(isinstance(cid, str) for cid in ids)
                and set(ids) <= all_candidate_ids and isinstance(stage, str) and stage in _CANDIDATE_SUBSET_STAGES)

    # The trace identifies the complete input set separately from any later selection.
    trace_identity = {}
    for record in (trace, *events):
        for key, expected in identity.items():
            if key not in record or (key == "candidate_ids" and declares_subset(record)):
                continue
            if record[key] != expected:
                raise ValueError("Retrieval assessment trace identity or candidate_ids mismatch")
            trace_identity[key] = record[key]
    if trace_identity != identity:
        raise ValueError("Retrieval assessment trace identity or candidate_ids mismatch")
    trace_records = list(_trace_records(trace, root=True))
    if any(record[key] != identity[key] for record, _, _ in trace_records
           for key in ("assessment_id", "query_plan_sha256", "candidate_set_sha256") if key in record):
        raise ValueError("Retrieval assessment trace identity mismatch in recorded response")
    trace_blocks_support = any(_trace_blocks_support(record) for record, _, _ in trace_records)
    if not isinstance(payload, Mapping):
        raise ValueError("Retrieval assessment fields are missing or unexpected")
    raw_keys = {"type", "checks", "out_of_scope", "limitations"}
    host_keys = (set(identity) - {"candidate_ids"}) | {"trace_ref", "decision"}
    supplied_keys = set(payload)
    full_keys = raw_keys | host_keys | {"candidate_ids", "citations", "scope_status", "insufficiency_reasons"}
    if supplied_keys == raw_keys:
        payload = {**payload, "decision": "supported", "scope_status": "in_scope", "insufficiency_reasons": []}
    elif supplied_keys != full_keys:
        raise ValueError("Retrieval assessment fields are missing or unexpected")
    elif payload["trace_ref"] != ref.to_dict() or any(payload.get(key) != value for key, value in identity.items()):
        raise ValueError("Retrieval assessment identity, trace_ref or candidate_ids mismatch")
    if payload["type"] != "retrieval_assessment":
        raise ValueError("Invalid retrieval assessment type")
    if payload["scope_status"] not in ("in_scope", "out_of_scope") or payload["decision"] not in (
        "supported", "insufficient", "out_of_scope"
    ):
        raise ValueError("Invalid retrieval assessment scope_status or decision")
    for key in ("limitations", "insufficiency_reasons"):
        if not isinstance(payload[key], list) or any(not isinstance(value, str) or not value.strip() for value in payload[key]):
            raise ValueError(f"Invalid retrieval assessment {key}")

    quote_fields = {"candidate_id", "start", "end"}
    allowed_quote_fields = quote_fields | _QUOTATION_PROVENANCE | {"text"}

    def quote(value):
        if (not isinstance(value, Mapping) or not quote_fields <= value.keys()
                or set(value) - allowed_quote_fields):
            raise ValueError("Invalid retrieval assessment citation")
        cid, start, end = value["candidate_id"], value["start"], value["end"]
        if (not isinstance(cid, str) or cid not in texts or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(texts[cid])
                or ("text" in value and value["text"] != texts[cid][start:end])):
            raise ValueError("Invalid retrieval assessment citation or quotation")
        _validate_quote_provenance(value, provenance[cid], (start, end), require_page=True)
        return cid, start, end

    def validate_candidate_ids(ids):
        if not isinstance(ids, list) or any(not isinstance(cid, str) or cid not in texts for cid in ids):
            raise ValueError("Retrieval assessment contains an unknown cited candidate or invalid candidate_ids")
        return set(ids)

    def validate_quotes(values):
        if not isinstance(values, list):
            raise ValueError("Retrieval assessment citations and quoted_spans must be lists")
        for value in values:
            cid = value.get("candidate_id") if isinstance(value, Mapping) else value
            if isinstance(cid, str) and cid not in texts:
                raise ValueError("Retrieval assessment contains an unknown cited candidate")
        return {quote(value) for value in values}

    required_ids = {req.requirement_id for req in requirements}

    def validate_checks(checks):
        if not isinstance(checks, list):
            raise ValueError("Retrieval assessment checks must be a list")
        by_req = {}
        checked_citations = set()
        for check in checks:
            if not isinstance(check, Mapping) or set(check) != {
                "requirement_id", "candidate_ids", "relation", "quoted_spans", "reason"
            }:
                raise ValueError("Invalid retrieval assessment check fields")
            rid = check["requirement_id"]
            if (not isinstance(rid, str) or rid in by_req
                    or check["relation"] not in ("satisfied", "missing", "uncertain")
                    or not isinstance(check["reason"], str) or not check["reason"].strip()):
                raise ValueError("Invalid retrieval assessment check")
            check_ids = validate_candidate_ids(check["candidate_ids"])
            quotes = validate_quotes(check["quoted_spans"])
            quoted_ids = {q[0] for q in quotes}
            if not quoted_ids <= check_ids or (check["relation"] == "satisfied" and (
                not quoted_ids or check_ids != quoted_ids
            )):
                raise ValueError("Retrieval assessment check candidate_ids do not match its quotations")
            checked_citations.update(quotes)
            by_req[rid] = (check, quotes)
        if set(by_req) != required_ids:
            raise ValueError("Retrieval assessment must check every requirement")
        return by_req, checked_citations

    by_req, checked_citations = validate_checks(payload["checks"])
    if "citations" in payload and validate_quotes(payload["citations"]) != checked_citations:
        raise ValueError("Retrieval assessment must link every citation")

    cited_ids = {cid for cid, _, _ in checked_citations}
    trace_quotes = set()
    has_recorded_quotes = False
    review_quotes = set()
    has_review_quotes = False
    review_superseded_quotes = False
    trace_checks = []
    review_checks = []
    review_checks_ancestors = frozenset()
    review_quotes_ancestors = frozenset()
    review_candidate_ids = None
    review_candidate_ancestors = frozenset()
    for record, phase, ancestors in trace_records:
        if record is not trace and "events" in record:
            nested = validate_events(record["events"])
            _validate_trace_sequence(nested, "stage_events" in record)
        if "steps" in record:
            validate_events(record["steps"])
        if "stage" in record and (not isinstance(record["stage"], str) or record["stage"] not in _RETRIEVAL_STAGES):
            raise ValueError("Invalid retrieval assessment trace events")
        if "embedding_provider" in record:
            _validate_embedding_provider(record["embedding_provider"])
        if "stage_events" in record:
            RetrievalTraceRecord(
                embedding_provider=record.get("embedding_provider"),
                stage_events=record["stage_events"],
                outcome=record.get("outcome", trace.get("outcome", "completed")),
                selected_chunk_ids=record.get(
                    "selected_chunk_ids", trace.get("selected_chunk_ids", identity["candidate_ids"]),
                ),
            )
            trace_blocks_support |= any(
                _trace_blocks_support({"stage": event})
                for event in record["stage_events"] if isinstance(event, str)
            )
        record_ids = validate_candidate_ids(record["candidate_ids"]) if "candidate_ids" in record else None
        if "candidate_id" in record:
            cid = record["candidate_id"]
            singular_id = validate_candidate_ids([cid])
            if record_ids is None:
                record_ids = singular_id
            if "start" in record or "end" in record:
                quote({key: record[key] for key in allowed_quote_fields if key in record})
            elif _QUOTATION_PROVENANCE.intersection(record):
                _validate_quote_provenance(record, provenance[cid])
        if phase == "selection" and record_ids is not None:
            if record_ids and not cited_ids <= record_ids:
                raise ValueError("Retrieval assessment contradicts trace selection or review")
            trace_blocks_support |= not record_ids
        if "selected_chunk_ids" in record:
            selected = validate_candidate_ids(record["selected_chunk_ids"])
            if selected and not cited_ids <= selected:
                raise ValueError("Retrieval assessment contradicts trace selection or review")
        recorded_citations = {key: validate_quotes(record[key]) for key in ("citations", "quoted_spans")
                              if key in record}
        record_quotes = set().union(*recorded_citations.values())
        record_checks = []
        for key in ("checks", "requirement_checks"):
            if key in record and record[key] != []:
                recorded_checks, recorded_spans = validate_checks(record[key])
                if "citations" in recorded_citations and recorded_citations["citations"] != recorded_spans:
                    raise ValueError("Retrieval assessment trace must link every citation")
                record_checks.extend(recorded_checks.items())
                record_quotes.update(recorded_spans)
        has_quotes = bool(recorded_citations or record_checks)
        if phase == "review":
            if record is trace and record_ids == all_candidate_ids:
                record_ids = None
            if has_quotes or record_ids is not None:
                if has_quotes:
                    has_review_quotes = True
                    if record_quotes or record_checks:
                        review_superseded_quotes = True
                    review_quotes = record_quotes
                    review_quotes_ancestors = ancestors
                elif id(record) not in review_quotes_ancestors:
                    has_review_quotes = False
                    review_quotes = set()
                    review_quotes_ancestors = frozenset()
                if record_checks:
                    review_checks = record_checks
                    review_checks_ancestors = ancestors
                elif id(record) not in review_checks_ancestors:
                    review_checks = []
                    review_checks_ancestors = frozenset()
                if record_ids is not None:
                    review_candidate_ids = (
                        review_candidate_ids & record_ids
                        if review_candidate_ids is not None and id(record) in review_candidate_ancestors
                        else record_ids
                    )
                    review_candidate_ancestors = ancestors
                elif id(record) not in review_candidate_ancestors:
                    review_candidate_ids = None
                    review_candidate_ancestors = frozenset()
        elif has_quotes and phase not in {"search", "detail"}:
            if (phase == "selection" and (record_quotes or record_checks)
                    and id(record) not in (review_quotes_ancestors | review_checks_ancestors | review_candidate_ancestors)
                    and (review_superseded_quotes or has_review_quotes or review_candidate_ids is not None)):
                trace_quotes.clear()
                trace_checks.clear()
                has_review_quotes = False
                review_superseded_quotes = False
                review_quotes = set()
                review_quotes_ancestors = frozenset()
                review_checks = []
                review_checks_ancestors = frozenset()
                review_candidate_ids = None
                review_candidate_ancestors = frozenset()
            has_recorded_quotes = True
            trace_quotes.update(record_quotes)
            trace_checks.extend(record_checks)

    if review_candidate_ids is not None and not cited_ids <= review_candidate_ids:
        raise ValueError("Retrieval assessment contradicts trace selection or review")
    if has_review_quotes:
        if not checked_citations <= review_quotes:
            raise ValueError("Retrieval assessment contradicts trace selection or review")
        active_checks = review_checks
    elif not review_superseded_quotes and has_recorded_quotes:
        if not checked_citations <= trace_quotes:
            raise ValueError("Retrieval assessment contradicts trace selection or review")
        active_checks = trace_checks
    else:
        active_checks = ()
    for rid, (check, quotes) in active_checks:
        supplied_check, supplied_quotes = by_req[rid]
        if (supplied_check["relation"] != check["relation"] or not supplied_quotes <= quotes
                or not set(supplied_check["candidate_ids"]) <= set(check["candidate_ids"])):
            raise ValueError("Retrieval assessment contradicts trace selection or review")

    scope = payload["out_of_scope"]
    if (not isinstance(scope, Mapping) or set(scope) != {"is_non_cae", "original_spans", "reason"}
            or type(scope["is_non_cae"]) is not bool or not isinstance(scope["reason"], str)
            or not isinstance(scope["original_spans"], list) or any(
                not isinstance(span, Mapping) or set(span) != {"start", "end"}
                or type(span["start"]) is not int or type(span["end"]) is not int
                or not 0 <= span["start"] < span["end"] <= len(question) for span in scope["original_spans"]
            ) or (scope["is_non_cae"] and not scope["original_spans"])):
        raise ValueError("Invalid retrieval assessment original span")
    if trace_blocks_support:
        payload = {**payload, "decision": "insufficient",
                   "insufficiency_reasons": [*payload["insufficiency_reasons"], "RETRIEVAL_TRACE_UNSUPPORTED"]}
    return ref, by_req, payload


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
    assessment_trace_root: Path | None = None,
    candidate_solver_scopes: Mapping[str, Sequence[str]] | None = None,
    approved_source_reviews: Mapping[str, ArtifactRefV1] | None = None,
) -> RetrievalDecisionV1:
    """Build a decision from assessment quotes and host-verified reviews keyed by document SHA256."""
    query_plan_sha = canonical_sha256(plan.to_dict())
    reqs, edition_owners = extract_query_scope(plan, explicit_facets)

    cand_summary = []
    candidate_provenance = {}
    for cand in candidates:
        cid, sha, edition, scope, text = _candidate_metadata(cand)
        override = (candidate_solver_scopes or {}).get(cid)
        meta = ({"coverage_facets": cand.coverage_facets, "source_kind": cand.source_kind,
                 "review_status": cand.review_status} if isinstance(cand, CorpusChunkSpec)
                else {key: cand.get(key) for key in ("coverage_facets", "topics", "solver", "source_kind", "review_status")})
        source_span = _candidate_source_span(cand)
        positions = ({**source_span["top"], **(source_span["nested"] or {})}
                     if "top" in source_span else source_span)
        source_format = cand.source_format if isinstance(cand, CorpusChunkSpec) else cand.get("source_format")
        if source_format is not None and not isinstance(source_format, str):
            raise ValueError("Invalid candidate source_format")
        section = cand.section_path if isinstance(cand, CorpusChunkSpec) else cand.get("section_path", ())
        section = _section_path(section)
        pages = _quote_page_bounds(positions, text)
        is_pdf = ((source_format or "").strip().lower() == "pdf" or bool(pages)
                  or any(positions.get(key) is not None
                         for key in ("page", "page_start", "page_end", "start_page", "end_page")))
        candidate_provenance[cid] = (pages, is_pdf, section)
        cand_summary.append({"candidate_id": cid, "source_sha256": sha, "edition": edition,
                             "solver_scope": list(scope), "scope_limit": list(override) if override is not None else None,
                             "source_span": source_span, "source_format": source_format, "section_path": section,
                             "chunk_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), **meta})
    cand_set_sha = canonical_sha256(cand_summary)

    assessment_id = str(uuid.uuid5(uuid.NAMESPACE_URL, canonical_sha256({
        "query_plan_sha256": query_plan_sha, "candidate_set_sha256": cand_set_sha,
        "required_facets": dict(explicit_facets or {}),
        "requirements": [req.to_dict() for req in reqs],
        "edition_owners": edition_owners,
    })))
    candidate_ids = [record["candidate_id"] for record in cand_summary]
    candidate_data = {_candidate_metadata(c)[0]: _candidate_metadata(c) for c in candidates}
    if assessment_result is not None:
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("Retrieval assessment candidate_ids must be unique")
        assessment_trace_ref, reviewed_checks, assessment_result = _strict_assessment(
            assessment_result, assessment_trace_ref, assessment_trace_root,
            {"assessment_id": assessment_id, "query_plan_sha256": query_plan_sha,
             "candidate_set_sha256": cand_set_sha, "candidate_ids": candidate_ids},
            reqs, {cid: row[4] for cid, row in candidate_data.items()}, candidate_provenance, plan.original_text,
        )

    req_solvers = {normalize_solver(r.expected) for r in reqs if r.facet == "solver"}
    req_editions = {r.requirement_id: r.expected for r in reqs if r.facet == "edition"}

    missing_evidence: list[str] = []
    limitations: list[str] = ["model_general_unverified"]
    checks: list[RetrievalCandidateCheckV1] = []

    if "QUERY_LANGUAGE_UNSUPPORTED" in plan.warnings:
        missing_evidence.append("QUERY_LANGUAGE_UNSUPPORTED")
    if "LANGUAGE_COVERAGE_UNVERIFIED" in plan.warnings:
        missing_evidence.append("LANGUAGE_COVERAGE_UNVERIFIED")

    # Non-CAE check only when no CAE terms/facets are present.
    is_non_cae, non_cae_reason = classify_non_cae_intent(plan)
    if is_non_cae:
        missing_evidence.append("NON_CAE_REQUEST")
        limitations.append(non_cae_reason)
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

    approved_refs = tuple(
        ref if isinstance(ref, ArtifactRefV1) else ArtifactRefV1.from_dict(ref)
        for ref in source_review_refs
    )
    candidate_counts = Counter(record["candidate_id"] for record in cand_summary)
    eligible: set[str] = set()
    solver_mismatch_count = 0
    scopes_by_candidate = {}
    editions_by_candidate = {}
    solvers_seen = set()
    editions_available = set()
    compatible_candidates = []
    for cand in candidates:
        cid, sha, edition, scope, _text = _candidate_metadata(cand)
        norm_scope = {normalize_solver(value) for value in scope}
        if candidate_solver_scopes and cid in candidate_solver_scopes:
            norm_scope &= {normalize_solver(value) for value in candidate_solver_scopes[cid]}
        solver_ok, edition_ok, covered_solvers, covered_editions = match_query_scope(
            reqs, edition_owners, norm_scope, edition,
        )
        scopes_by_candidate[cid] = covered_solvers
        editions_by_candidate[cid] = covered_editions
        if solver_ok:
            solvers_seen.update(norm_scope)
        if not solver_ok:
            solver_mismatch_count += 1
            checks.append(
                RetrievalCandidateCheckV1(
                    candidate_id=cid,
                    source_sha256=sha,
                    relation="irrelevant",
                    requirement_ids=(),
                    quoted_spans=(),
                    reason=f"요청 솔버({sorted(req_solvers)})와 후보 출처 범위({list(scope) or ['unknown']})가 다릅니다.",
                )
            )
        elif not edition_ok:
            checks.append(
                RetrievalCandidateCheckV1(
                    candidate_id=cid,
                    source_sha256=sha,
                    relation="irrelevant",
                    requirement_ids=(),
                    quoted_spans=(),
                    reason=f"요청 판본({list(req_editions.values())})과 후보 판본({edition})이 일치하지 않습니다.",
                )
            )
        else:
            compatible_candidates.append(cand)
            editions_available.update(covered_editions)
            review = (approved_source_reviews or {}).get(sha)
            if (isinstance(review, ArtifactRefV1) and review in approved_refs and review.role == "source_review"
                    and re.fullmatch(r"[0-9a-f]{64}", sha) and sha != "0" * 64
                    and candidate_counts[cid] == 1 and _has_valid_source_span(cand)):
                eligible.add(cid)
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

    if candidates and req_solvers - solvers_seen:
        if solver_mismatch_count == len(candidates):
            missing_evidence.append("SOLVER_MISMATCH")
        missing_evidence.extend(("SOLVER_COVERAGE_INSUFFICIENT", "missing_solver"))
    if req_editions and set(req_editions) - editions_available and (
        not req_solvers or solver_mismatch_count < len(candidates)
    ):
        missing_evidence.extend(("EDITION_COVERAGE_INSUFFICIENT", "edition_mismatch"))
    missing_evidence.extend(_coverage_gaps(plan, compatible_candidates, explicit_facets))

    status = "insufficient"
    if assessment_result is None or not source_review_refs or not assessment_profile_sha256:
        missing_evidence.append("RETRIEVAL_RELEVANCE_UNVERIFIED")
    else:
        missing_evidence.extend(assessment_result["insufficiency_reasons"])
        if assessment_result["out_of_scope"]["is_non_cae"] or assessment_result["scope_status"] != "in_scope":
            missing_evidence.append("NON_CAE_INTENT_UNVERIFIED")
        relevant: dict[str, list[tuple[str, int, int]]] = {}
        verified = []
        for req in reqs:
            check, quotes = reviewed_checks[req.requirement_id]
            matching_quotes = [
                (cid, start, end) for cid, start, end in quotes
                if (req.facet != "edition" or req.requirement_id in editions_by_candidate[cid])
                and (req.facet != "solver" or normalize_solver(req.expected) in scopes_by_candidate[cid])
            ]
            if (check["relation"] != "satisfied" or not matching_quotes
                    or len(matching_quotes) != len(quotes)
                    or any(cid not in eligible for cid, _, _ in matching_quotes)):
                continue
            verified.append(RetrievalRequirementV1(
                req.requirement_id, req.original_span, req.facet, req.expected, "verified",
                f"{req.reason} — {check['reason']}" if req.requirement_id in edition_owners else check["reason"],
            ))
            for cid, start, end in matching_quotes:
                relevant.setdefault(cid, []).append((req.requirement_id, start, end))
        checks = [
            RetrievalCandidateCheckV1(
                candidate_id=cid, source_sha256=candidate_data[cid][1], relation="relevant",
                requirement_ids=tuple(dict.fromkeys(rid for rid, _, _ in quotes)),
                quoted_spans=tuple({"start": start, "end": end} for _, start, end in quotes),
                reason="질문별 관련성 검토 및 출처 검증 통과",
            )
            for cid, quotes in relevant.items()
        ] + [check for check in checks if check.candidate_id not in relevant]
        if (not missing_evidence and assessment_result["decision"] == "supported"
                and len(verified) == len(reqs) and relevant):
            reqs = tuple(verified)
            status = "supported"
            limitations = assessment_result["limitations"]
        else:
            missing_evidence.append("RETRIEVAL_REQUIREMENTS_UNVERIFIED")

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
