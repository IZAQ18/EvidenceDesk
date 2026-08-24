from __future__ import annotations

from app.evaluation import run_synthetic_benchmark
from app.retrieval import active_concepts, passage_relevance, semantic_similarity


def test_local_semantic_features_are_auditable_and_domain_aware():
    question = "May I work from home?"
    relevant = "Staff can perform their duties remotely with manager approval."
    unrelated = "Receipts must accompany business expense claims."
    assert active_concepts(question) == {"remote_work"}
    assert active_concepts(relevant) == {"remote_work"}
    assert semantic_similarity(question, relevant) > semantic_similarity(question, unrelated)


def test_question_shape_reranking_rewards_direct_numeric_answers():
    question = "How many paid leave days are provided?"
    overview = "The leave program is administered by People Operations."
    entitlement = "Employees receive twenty paid leave days each year."
    assert passage_relevance(question, entitlement)["score"] > passage_relevance(
        question, overview
    )["score"]


def test_synthetic_intelligence_benchmark_meets_acceptance_floor():
    report = run_synthetic_benchmark()
    assert report.cases == 8
    assert report.retrieval_success >= 0.95
    assert report.citation_validity == 1.0
    assert report.refusal_accuracy == 1.0
    assert report.security_boundary_accuracy == 1.0
    assert report.retrieval_mode == "authorized-keyword-plus-local-semantic"
