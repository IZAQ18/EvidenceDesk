from __future__ import annotations

import re
import time

from app.model_gateway import ModelGateway
from app.models import (
    AnswerResponse,
    Citation,
    GenerationSummary,
    Principal,
    RetrievalSummary,
    WorkflowStep,
)
from app.repository import Repository, RetrievedChunk
from app.retrieval import expanded_query_terms, passage_relevance, terms


def _ranked_sentences(question: str, text: str) -> list[tuple[str, dict[str, float]]]:
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]
    if not sentences:
        passage = text[:600]
        return [(passage, passage_relevance(question, passage))]
    ranked = sorted(
        (
            (passage_relevance(question, sentence)["score"], -index, sentence)
            for index, sentence in enumerate(sentences)
        ),
        reverse=True,
    )
    return [
        (sentence[:600], passage_relevance(question, sentence[:600]))
        for _, _, sentence in ranked
    ]


class AnswerWorkflow:
    """A fixed evidence workflow: retrieve, synthesize, verify, and safely fall back."""

    def __init__(self, repository: Repository, model_gateway: ModelGateway | None = None):
        self.repository = repository
        self.model_gateway = model_gateway

    def run(
        self,
        question: str,
        principal: Principal,
        top_k: int,
        document_ids: list[str] | None = None,
    ) -> AnswerResponse:
        trace: list[WorkflowStep] = []
        started = time.perf_counter()
        search = self.repository.hybrid_search(question, principal, top_k, document_ids)
        chunks = search.chunks
        duration_ms = round((time.perf_counter() - started) * 1_000, 2)
        diagnostics = search.diagnostics
        retrieval = RetrievalSummary(
            mode=diagnostics.mode,
            authorized_candidates=diagnostics.candidate_pool,
            lexical_hits=diagnostics.lexical_hits,
            semantic_hits=diagnostics.semantic_hits,
            returned_chunks=diagnostics.returned_chunks,
            best_semantic_score=diagnostics.best_semantic_score,
            best_rerank_score=diagnostics.best_rerank_score,
            duration_ms=duration_ms,
        )
        trace.append(WorkflowStep(
            name="hybrid_retrieve",
            status="complete",
            detail=(
                f"Reviewed {diagnostics.candidate_pool} authorized candidates; keyword and local "
                f"semantic scoring returned {len(chunks)} chunks."
            ),
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
                retrieval=retrieval,
            )

        candidates: list[tuple[RetrievedChunk, str, dict[str, float], float]] = []
        meaningful_terms, expanded_question_terms = expanded_query_terms(question)
        minimum_overlap = 1 if len(meaningful_terms) <= 2 else 2
        for chunk in chunks:
            for sentence, sentence_metrics in _ranked_sentences(question, chunk.text):
                exact_overlap = int(sentence_metrics["exact_overlap"])
                expanded_overlap = int(sentence_metrics["expanded_overlap"])
                semantic_support = sentence_metrics["semantic"]
                if exact_overlap >= minimum_overlap or (
                    expanded_overlap >= 1 and semantic_support >= 0.12
                ):
                    combined_score = sentence_metrics["score"] + chunk.relevance_score * 0.25
                    candidates.append((chunk, sentence, sentence_metrics, combined_score))
        candidates.sort(key=lambda item: item[3], reverse=True)
        trace.append(
            WorkflowStep(
                name="rerank",
                status="complete",
                detail=(
                    f"Rescored {len(candidates)} answer-bearing passages for direct coverage, "
                    "semantic similarity, metadata, and question shape."
                ),
            )
        )
        deduplicated: list[tuple[RetrievedChunk, str, dict[str, float], float]] = []
        seen_sentences: set[str] = set()
        covered_query_terms: set[str] = set()
        for candidate in candidates:
            normalized = re.sub(r"\W+", " ", candidate[1].lower()).strip()
            matched_terms = terms(candidate[1]) & expanded_question_terms
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
                retrieval=retrieval,
            )

        citations: list[Citation] = []
        statements: list[str] = []
        for number, (chunk, sentence, _, _) in enumerate(candidates, 1):
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
        answer = (
            f"The evidence states: {statements[0]}"
            if len(statements) == 1
            else "The strongest authorized evidence says:\n" + "\n".join(
                f"- {statement}" for statement in statements
            )
        )
        trace.append(WorkflowStep(
            name="synthesize", status="complete", detail="Selected source sentences and attached citations."
        ))

        extractive_answer = answer
        extractive_citations = list(citations)
        generation = GenerationSummary()
        if self.model_gateway and self.model_gateway.enabled:
            clean_citations = [
                citation
                for citation, (chunk, _, _, _) in zip(citations, candidates, strict=True)
                if not chunk.injection_flag
            ]
            if clean_citations:
                synthesis = self.model_gateway.synthesize(question, clean_citations, principal)
                generation = synthesis.summary
                if synthesis.answer:
                    answer = synthesis.answer
                    citations = synthesis.citations
                    trace.append(
                        WorkflowStep(
                            name="model_synthesize",
                            status="passed",
                            detail=(
                                "The server model organized authorized excerpts; each claim passed "
                                "citation, term, number, and negation checks."
                            ),
                        )
                    )
                else:
                    trace.append(
                        WorkflowStep(
                            name="model_synthesize",
                            status="fallback",
                            detail=(
                                "Used the local extractive answer because model synthesis was not "
                                f"accepted ({generation.fallback_reason or 'unavailable'})."
                            ),
                        )
                    )
            else:
                generation = GenerationSummary(
                    mode="extractive_fallback", fallback_reason="prompt_injection_risk"
                )
                trace.append(
                    WorkflowStep(
                        name="model_synthesize",
                        status="fallback",
                        detail="Instruction-like evidence was kept out of the external model request.",
                    )
                )

        markers = {int(value) for value in re.findall(r"\[(\d+)]", answer)}
        valid = markers == {citation.number for citation in citations}
        if generation.mode != "model":
            valid = valid and all(citation.excerpt in answer for citation in citations)
        trace.append(WorkflowStep(
            name="verify_citations",
            status="passed" if valid else "failed",
            detail="All citation markers resolve to retrieved evidence." if valid else "Used safe evidence fallback.",
        ))
        if not valid:
            answer = extractive_answer
            citations = extractive_citations
            generation.mode = "extractive_fallback"
            generation.fallback_reason = "post_generation_citation_check"

        flagged = sum(chunk.injection_flag for chunk, _, _, _ in candidates)
        warning = None
        if flagged:
            warning = (
                f"{flagged} retrieved chunk(s) contained instruction-like language and were treated as untrusted data."
            )
        best_exact_coverage = max(metrics["exact_coverage"] for _, _, metrics, _ in candidates)
        best_semantic = max(metrics["semantic"] for _, _, metrics, _ in candidates)
        covered_terms = set().union(*(terms(sentence) for _, sentence, _, _ in candidates))
        coverage = len(covered_terms & meaningful_terms) / max(1, len(meaningful_terms))
        if best_exact_coverage >= 0.5 and coverage >= 0.5:
            quality = "strong"
            quality_reason = "Direct source passages cover most meaningful question terms and passed reranking."
        else:
            quality = "partial"
            quality_reason = (
                "Semantically relevant evidence was found, but direct wording covers only part of the question."
                if best_semantic >= 0.12
                else "Relevant evidence was found, but it covers only part of the question."
            )
        return AnswerResponse(
            answer=answer,
            grounded=True,
            citations=citations,
            workflow=trace,
            warning=warning,
            evidence_quality=quality,
            quality_reason=quality_reason,
            retrieval=retrieval,
            generation=generation,
        )
