from __future__ import annotations

import re

from app.models import AnswerResponse, Citation, Principal, WorkflowStep
from app.repository import QUERY_EXPANSIONS, Repository, RetrievedChunk

STOP_WORDS = {
    "about", "after", "again", "also", "and", "are", "because", "been", "before", "being",
    "can", "does", "for", "from", "have", "how", "into", "its", "not", "our", "that", "the",
    "their", "then", "there", "these", "this", "those", "was", "what", "when", "where", "which",
    "in", "is", "of", "to", "who", "will", "with", "would", "you", "your",
}


def _terms(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]{2,}", text.lower().replace("-", " "))
    normalized: set[str] = set()
    for word in words:
        if word in STOP_WORDS:
            continue
        if word.endswith("ied") and len(word) > 4:
            word = f"{word[:-3]}y"
        elif word.endswith("ed") and len(word) > 4:
            word = word[:-2]
        elif word.endswith("ies") and len(word) > 4:
            word = f"{word[:-3]}y"
        elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
            word = word[:-1]
        normalized.add(word)
    return normalized


def _query_terms(text: str) -> set[str]:
    exact = _terms(text)
    expanded = set(exact)
    for term in exact:
        expanded.update(QUERY_EXPANSIONS.get(term, set()))
    return expanded


def _ranked_sentences(question: str, text: str) -> list[tuple[str, int]]:
    query_terms = _query_terms(question)
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]
    if not sentences:
        return [(text[:500], 0)]
    ranked = sorted(
        ((len(query_terms & _terms(sentence)), -index, sentence) for index, sentence in enumerate(sentences)),
        reverse=True,
    )
    return [(sentence[:600], overlap) for overlap, _, sentence in ranked]


class AnswerWorkflow:
    """A fixed evidence workflow: retrieve, synthesize, verify, and safely fall back."""

    def __init__(self, repository: Repository):
        self.repository = repository

    def run(
        self,
        question: str,
        principal: Principal,
        top_k: int,
        document_ids: list[str] | None = None,
    ) -> AnswerResponse:
        trace: list[WorkflowStep] = []
        chunks = self.repository.search(question, principal, top_k, document_ids)
        trace.append(WorkflowStep(
            name="retrieve", status="complete", detail=f"Found {len(chunks)} authorized evidence chunks."
        ))
        if not chunks:
            trace.append(WorkflowStep(
                name="refuse", status="complete", detail="No authorized evidence matched the question."
            ))
            return AnswerResponse(
                answer="I couldn't find enough authorized evidence to answer that question.",
                grounded=False,
                citations=[],
                workflow=trace,
                evidence_quality="insufficient",
                quality_reason="No authorized evidence chunks matched the question.",
            )

        candidates: list[tuple[RetrievedChunk, str, int]] = []
        meaningful_terms = _terms(question)
        minimum_overlap = 1 if len(meaningful_terms) <= 2 else 2
        for chunk in chunks:
            for sentence, overlap in _ranked_sentences(question, chunk.text):
                if overlap >= minimum_overlap:
                    candidates.append((chunk, sentence, overlap))
        candidates.sort(key=lambda item: (item[2], item[0].relevance_score), reverse=True)
        deduplicated: list[tuple[RetrievedChunk, str, int]] = []
        seen_sentences: set[str] = set()
        covered_query_terms: set[str] = set()
        expanded_question_terms = _query_terms(question)
        for candidate in candidates:
            normalized = re.sub(r"\W+", " ", candidate[1].lower()).strip()
            matched_terms = _terms(candidate[1]) & expanded_question_terms
            adds_question_detail = bool(matched_terms - covered_query_terms)
            if normalized and normalized not in seen_sentences and adds_question_detail:
                deduplicated.append(candidate)
                seen_sentences.add(normalized)
                covered_query_terms.update(matched_terms)
        candidates = deduplicated[:3]
        if not candidates:
            trace.append(WorkflowStep(
                name="refuse", status="complete", detail="Retrieved text lacked meaningful term overlap."
            ))
            return AnswerResponse(
                answer="I couldn't find enough direct evidence to answer that question confidently.",
                grounded=False,
                citations=[],
                workflow=trace,
                evidence_quality="insufficient",
                quality_reason="Retrieved text did not overlap enough meaningful question terms.",
            )

        citations: list[Citation] = []
        statements: list[str] = []
        for number, (chunk, sentence, _) in enumerate(candidates, 1):
            citations.append(Citation(
                number=number,
                document_id=chunk.document_id,
                chunk_id=chunk.chunk_id,
                filename=chunk.filename,
                source=chunk.source,
                classification=chunk.classification,
                excerpt=sentence,
                page_number=chunk.page_number,
            ))
            statements.append(f"{sentence} [{number}]")
        answer = " ".join(statements)
        trace.append(WorkflowStep(
            name="synthesize", status="complete", detail="Selected source sentences and attached citations."
        ))

        markers = {int(value) for value in re.findall(r"\[(\d+)]", answer)}
        valid = markers == {citation.number for citation in citations}
        trace.append(WorkflowStep(
            name="verify_citations",
            status="passed" if valid else "failed",
            detail="All citation markers resolve to retrieved evidence." if valid else "Used safe evidence fallback.",
        ))
        if not valid:
            answer = "\n".join(f"- {citation.excerpt} [{citation.number}]" for citation in citations)

        flagged = sum(chunk.injection_flag for chunk, _, _ in candidates)
        warning = None
        if flagged:
            warning = (
                f"{flagged} cited chunk(s) contained instruction-like language and were treated as untrusted data."
            )
        best_overlap = max(overlap for _, _, overlap in candidates)
        covered_terms = set().union(*(_terms(sentence) for _, sentence, _ in candidates))
        coverage = len(covered_terms & meaningful_terms) / max(1, len(meaningful_terms))
        if best_overlap >= 3 and coverage >= 0.5:
            quality = "strong"
            quality_reason = "Direct source sentences cover most of the meaningful question terms."
        else:
            quality = "partial"
            quality_reason = "Relevant evidence was found, but it covers only part of the question."
        return AnswerResponse(
            answer=answer,
            grounded=True,
            citations=citations,
            workflow=trace,
            warning=warning,
            evidence_quality=quality,
            quality_reason=quality_reason,
        )
