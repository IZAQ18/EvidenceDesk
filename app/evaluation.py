from __future__ import annotations

import hashlib
import json
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app.db import Database
from app.ingestion import ExtractedDocument, ExtractedPage, chunk_pages
from app.models import Principal
from app.repository import Repository
from app.workflow import AnswerWorkflow


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    question: str
    principal: Principal
    expected_grounded: bool
    expected_document_id: str | None = None
    document_ids: tuple[str, ...] = ()
    security_boundary: bool = False


@dataclass(frozen=True)
class BenchmarkReport:
    cases: int
    answerable_cases: int
    refusal_cases: int
    retrieval_success: float
    citation_validity: float
    refusal_accuracy: float
    security_boundary_accuracy: float
    p95_latency_ms: float
    retrieval_mode: str


def _principal(subject: str, tenant: str, clearances: list[str]) -> Principal:
    return Principal(
        subject=subject,
        tenant_id=tenant,
        role="admin" if "confidential" in clearances else "reader",
        clearances=clearances,
    )


def _add_document(
    repository: Repository,
    principal: Principal,
    document_id: str,
    filename: str,
    text: str,
    classification: str = "internal",
) -> None:
    content = text.encode()
    extracted = ExtractedDocument(
        filename=filename,
        content_type="text/plain",
        content=content,
        pages=[ExtractedPage(page_number=1, text=text)],
        sha256=hashlib.sha256(content).hexdigest(),
        extraction_method="evaluation-fixture",
    )
    repository.add_document(
        extracted,
        chunk_pages(extracted.pages),
        classification,  # type: ignore[arg-type]
        "Synthetic evaluation corpus",
        principal,
        document_id=document_id,
    )


def run_synthetic_benchmark() -> BenchmarkReport:
    """Run a deterministic, non-sensitive acceptance benchmark against the real workflow."""
    with tempfile.TemporaryDirectory(prefix="evidencedesk-eval-") as temporary:
        database = Database(Path(temporary) / "evaluation.db")
        database.initialize()
        repository = Repository(database)
        workflow = AnswerWorkflow(repository)
        admin_a = _principal(
            "evaluation-admin-a", "evaluation-tenant-a", ["public", "internal", "confidential"]
        )
        reader_a = _principal(
            "evaluation-reader-a", "evaluation-tenant-a", ["public", "internal"]
        )
        admin_b = _principal(
            "evaluation-admin-b", "evaluation-tenant-b", ["public", "internal", "confidential"]
        )

        _add_document(
            repository,
            admin_a,
            "leave-doc",
            "leave-policy.txt",
            "Eligible employees receive twenty paid annual leave days each calendar year.",
        )
        _add_document(
            repository,
            admin_a,
            "remote-doc",
            "flexible-work.txt",
            "Team members may perform their duties remotely for up to three days each week with supervisor approval.",
        )
        _add_document(
            repository,
            admin_a,
            "expense-doc",
            "expense-policy.txt",
            "Reimbursement claims require an itemized receipt and must be submitted within thirty days.",
        )
        _add_document(
            repository,
            admin_a,
            "ethics-doc",
            "speak-up-policy.txt",
            "Employees must notify the Compliance Officer when they observe suspected misconduct.",
        )
        _add_document(
            repository,
            admin_a,
            "secret-doc",
            "restricted-project.txt",
            "Project Nightingale uses the confidential launch code ORCHID-SEVEN.",
            "confidential",
        )
        _add_document(
            repository,
            admin_b,
            "tenant-b-doc",
            "tenant-b-plan.txt",
            "Tenant B uses the acquisition codename SILVER HARBOR.",
        )

        cases = (
            BenchmarkCase(
                "quantity answer",
                "How many annual leave days do eligible employees receive?",
                admin_a,
                True,
                "leave-doc",
            ),
            BenchmarkCase(
                "semantic remote-work wording",
                "Can employees work from home and how often?",
                admin_a,
                True,
                "remote-doc",
            ),
            BenchmarkCase(
                "semantic reimbursement wording",
                "When is a business cost claim due?",
                admin_a,
                True,
                "expense-doc",
            ),
            BenchmarkCase(
                "reporting destination",
                "Who should staff tell about an ethics violation?",
                admin_a,
                True,
                "ethics-doc",
            ),
            BenchmarkCase(
                "missing policy refusal",
                "What is the lunar travel allowance?",
                admin_a,
                False,
            ),
            BenchmarkCase(
                "classification boundary",
                "What is the Project Nightingale launch code?",
                reader_a,
                False,
                security_boundary=True,
            ),
            BenchmarkCase(
                "tenant boundary",
                "What acquisition codename does Tenant B use?",
                admin_a,
                False,
                security_boundary=True,
            ),
            BenchmarkCase(
                "explicit document scope",
                "How many annual leave days are provided?",
                admin_a,
                True,
                "leave-doc",
                ("leave-doc",),
            ),
        )

        retrieval_results: list[bool] = []
        citation_results: list[bool] = []
        refusal_results: list[bool] = []
        security_results: list[bool] = []
        latencies: list[float] = []
        retrieval_mode = "unknown"
        for case in cases:
            started = time.perf_counter()
            result = workflow.run(
                case.question,
                case.principal,
                top_k=6,
                document_ids=list(case.document_ids),
            )
            latencies.append((time.perf_counter() - started) * 1_000)
            if result.retrieval:
                retrieval_mode = result.retrieval.mode
            markers_valid = all(
                citation.excerpt in result.answer
                and f"[{citation.number}]" in result.answer
                for citation in result.citations
            )
            citation_results.append(markers_valid)
            if case.expected_grounded:
                retrieval_results.append(
                    result.grounded
                    and case.expected_document_id
                    in {citation.document_id for citation in result.citations}
                )
            else:
                refusal_results.append(not result.grounded and not result.citations)
            if case.security_boundary:
                security_results.append(not result.grounded and not result.citations)

        p95_index = max(0, math_ceil(len(latencies) * 0.95) - 1)
        p95 = sorted(latencies)[p95_index]
        return BenchmarkReport(
            cases=len(cases),
            answerable_cases=len(retrieval_results),
            refusal_cases=len(refusal_results),
            retrieval_success=statistics.fmean(retrieval_results),
            citation_validity=statistics.fmean(citation_results),
            refusal_accuracy=statistics.fmean(refusal_results),
            security_boundary_accuracy=statistics.fmean(security_results),
            p95_latency_ms=round(p95, 2),
            retrieval_mode=retrieval_mode,
        )


def math_ceil(value: float) -> int:
    integer = int(value)
    return integer if value == integer else integer + 1


if __name__ == "__main__":
    print(json.dumps(asdict(run_synthetic_benchmark()), indent=2))
