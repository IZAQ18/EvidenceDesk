from __future__ import annotations

import math
import re
from collections import Counter

STOP_WORDS = {
    "a",
    "about",
    "after",
    "again",
    "also",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "by",
    "can",
    "do",
    "does",
    "for",
    "from",
    "have",
    "how",
    "in",
    "into",
    "is",
    "it",
    "its",
    "may",
    "not",
    "of",
    "on",
    "or",
    "our",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "this",
    "those",
    "to",
    "was",
    "what",
    "when",
    "where",
    "which",
    "who",
    "will",
    "with",
    "would",
    "you",
    "your",
}

# Compact, auditable domain concepts improve policy-language recall without pretending that the
# local MVP ships a neural embedding model. Each alias is normalized by the same tokenizer.
SEMANTIC_CONCEPTS: dict[str, tuple[str, ...]] = {
    "leave": (
        "leave",
        "vacation",
        "holiday",
        "pto",
        "absence",
        "time off",
        "annual break",
    ),
    "parental_leave": (
        "parental leave",
        "maternity",
        "paternity",
        "birth",
        "adoption",
        "new parent",
    ),
    "remote_work": (
        "remote",
        "remotely",
        "work from home",
        "home working",
        "telework",
        "hybrid",
        "offsite",
    ),
    "termination": (
        "termination",
        "terminate",
        "dismissal",
        "dismissed",
        "fired",
        "separation",
        "employment ends",
    ),
    "compensation": (
        "compensation",
        "salary",
        "wage",
        "pay",
        "remuneration",
        "payroll",
    ),
    "reporting": (
        "report",
        "complaint",
        "concern",
        "grievance",
        "speak up",
        "notify",
        "disclose",
        "escalate",
    ),
    "misconduct": (
        "misconduct",
        "violation",
        "harassment",
        "bullying",
        "discrimination",
        "retaliation",
        "ethics breach",
    ),
    "privacy": (
        "privacy",
        "personal data",
        "personal information",
        "pii",
        "confidential information",
        "data protection",
    ),
    "deadline": (
        "deadline",
        "due date",
        "time limit",
        "timeframe",
        "notice period",
        "days in advance",
        "submit by",
    ),
    "expense": (
        "expense",
        "reimbursement",
        "reimburse",
        "cost claim",
        "receipt",
        "business cost",
    ),
    "benefit": (
        "benefit",
        "insurance",
        "coverage",
        "health plan",
        "perk",
        "allowance",
    ),
    "access": (
        "access",
        "permission",
        "authorization",
        "clearance",
        "eligible",
        "entitled",
    ),
}


def _stem(word: str) -> str:
    if word.endswith("ied") and len(word) > 4:
        return f"{word[:-3]}y"
    if word.endswith("ies") and len(word) > 4:
        return f"{word[:-3]}y"
    if word.endswith("ing") and len(word) > 5:
        return word[:-3]
    if word.endswith("ed") and len(word) > 4:
        return word[:-2]
    if word.endswith("ly") and len(word) > 5:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def ordered_terms(text: str) -> list[str]:
    return [
        _stem(word)
        for word in re.findall(r"[a-z0-9][a-z0-9'-]+", text.lower().replace("-", " "))
        if word not in STOP_WORDS
    ]


def terms(text: str) -> set[str]:
    return set(ordered_terms(text))


def _concept_alias_terms() -> dict[str, tuple[frozenset[str], ...]]:
    return {
        concept: tuple(frozenset(ordered_terms(alias)) for alias in aliases)
        for concept, aliases in SEMANTIC_CONCEPTS.items()
    }


CONCEPT_ALIAS_TERMS = _concept_alias_terms()


def active_concepts(text: str) -> set[str]:
    text_terms = terms(text)
    return {
        concept
        for concept, aliases in CONCEPT_ALIAS_TERMS.items()
        if any(alias and alias <= text_terms for alias in aliases)
    }


def expanded_query_terms(text: str) -> tuple[set[str], set[str]]:
    exact = terms(text)
    expanded = set(exact)
    for concept in active_concepts(text):
        for alias in CONCEPT_ALIAS_TERMS[concept]:
            expanded.update(alias)
    return exact, expanded


def semantic_vector(text: str) -> dict[str, float]:
    tokens = ordered_terms(text)
    features: Counter[str] = Counter()
    for token in tokens:
        features[f"term:{token}"] += 1.0
    for left, right in zip(tokens, tokens[1:], strict=False):
        features[f"pair:{left}_{right}"] += 0.65
    for concept in active_concepts(text):
        features[f"concept:{concept}"] += 2.4
    return dict(features)


def cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    shared = left.keys() & right.keys()
    numerator = sum(left[key] * right[key] for key in shared)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


def semantic_similarity(question: str, text: str) -> float:
    return cosine_similarity(semantic_vector(question), semantic_vector(text))


NUMBER_WORDS = {
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "ninety",
    "hundred",
}


def answer_shape_bonus(question: str, passage: str) -> float:
    lowered_question = question.lower().strip()
    passage_terms = terms(passage)
    bonus = 0.0
    if re.search(r"\b(how many|how much|what amount|number of)\b", lowered_question):
        if re.search(r"\b\d+(?:\.\d+)?\b", passage) or NUMBER_WORDS & passage_terms:
            bonus += 0.45
    if re.search(r"\b(when|deadline|how long|notice|time limit)\b", lowered_question):
        if re.search(
            r"\b(day|week|month|year|hour|before|after|within|advance|deadline|due)\w*\b",
            passage.lower(),
        ):
            bonus += 0.35
    if re.search(r"\b(who|where|contact|report to)\b", lowered_question):
        if re.search(r"\b(team|manager|officer|department|operations|committee|email)\b", passage.lower()):
            bonus += 0.25
    return bonus


def passage_relevance(question: str, passage: str, metadata: str = "") -> dict[str, float]:
    exact, expanded = expanded_query_terms(question)
    passage_terms = terms(passage)
    exact_overlap = len(exact & passage_terms)
    expanded_overlap = len((expanded - exact) & passage_terms)
    exact_coverage = exact_overlap / max(1, len(exact))
    semantic = semantic_similarity(question, passage)
    metadata_overlap = len(exact & terms(metadata)) / max(1, len(exact))
    shape = answer_shape_bonus(question, passage)
    score = (
        exact_coverage * 3.0
        + min(expanded_overlap, 3) * 0.35
        + semantic * 3.5
        + metadata_overlap * 0.45
        + shape
    )
    return {
        "score": round(score, 6),
        "exact_overlap": float(exact_overlap),
        "expanded_overlap": float(expanded_overlap),
        "exact_coverage": round(exact_coverage, 6),
        "semantic": round(semantic, 6),
        "answer_shape": round(shape, 6),
    }
