from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.config import Settings
from app.db import build_database
from app.ingestion import chunk_pages, validate_upload_bytes
from app.isolated_extraction import extract_in_isolated_process
from app.malware import MalwareScanner, build_malware_scanner
from app.models import Principal
from app.repository import DuplicateDocumentError, Repository
from app.storage import OriginalStorage, build_document_storage

logger = logging.getLogger("evidencedesk.ingestion")


def _principal(job: dict) -> Principal:
    return Principal(
        subject=job["owner_subject"],
        tenant_id=job["tenant_id"],
        role=job["owner_role"],
        clearances=json.loads(job["owner_clearances_json"]),
    )


def _error_detail(exc: HTTPException) -> tuple[str, str, str | None]:
    if isinstance(exc.detail, dict):
        return (
            str(exc.detail.get("code") or "extraction_rejected"),
            str(exc.detail.get("message") or "The document could not be extracted."),
            str(exc.detail["action"]) if exc.detail.get("action") else None,
        )
    return "extraction_rejected", str(exc.detail), None


def _read_staged(storage: OriginalStorage, reference: str, max_bytes: int) -> bytes:
    content = bytearray()
    for block in storage.stream(reference):
        content.extend(block)
        if len(content) > max_bytes:
            raise HTTPException(status_code=413, detail="The staged file exceeds the size limit")
    return bytes(content)


def process_ingestion_job(
    job: dict,
    *,
    worker_id: str,
    repository: Repository,
    storage: OriginalStorage,
    settings: Settings,
    malware_scanner: MalwareScanner,
) -> None:
    principal = _principal(job)
    promoted_reference: str | None = None
    try:
        existing = repository.get_document(job["document_id"], principal)
        if existing:
            existing_reference = existing.get("storage_path")
            repository.finish_ingestion_job(job["id"], worker_id, existing_reference)
            if job.get("storage_path") != existing_reference:
                storage.delete(job.get("storage_path"))
            return

        content = _read_staged(storage, job["storage_path"], settings.max_upload_bytes)
        validate_upload_bytes(job["filename"], job["content_type"], content, settings)
        if len(content) != job["size_bytes"] or hashlib.sha256(content).hexdigest() != job["sha256"]:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "staged_file_mismatch",
                    "message": "The staged file failed its integrity check.",
                    "action": "Delete this job and upload the original file again.",
                },
            )

        repository.update_ingestion_job(
            job["id"],
            worker_id,
            stage="scanning",
            progress=28,
            detail="Streaming the quarantined upload through the malware scanner.",
            lease_seconds=settings.ingestion_job_lease_seconds,
        )
        try:
            scan = malware_scanner.scan(content)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "malware_scanner_unavailable",
                    "message": "The private malware scanner is temporarily unavailable.",
                    "action": "Retry later. The upload remains isolated and is not searchable.",
                },
            ) from exc
        if scan.status == "infected":
            repository.fail_ingestion_job(
                job["id"],
                worker_id,
                code="malware_detected",
                message="The upload was blocked by the malware scanner.",
                action="An administrator should remove the quarantined upload.",
                retryable=False,
                quarantined=True,
            )
            repository.audit(
                principal,
                "ingestion_upload_quarantined",
                "ingestion_job",
                job["id"],
                {"engine": scan.engine},
            )
            return
        if scan.status in {"skipped", "unavailable"} and settings.malware_scan_required:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "malware_scan_required",
                    "message": "Required malware scanning did not complete.",
                    "action": "Retry after the private scanner is healthy.",
                },
            )

        repository.update_ingestion_job(
            job["id"],
            worker_id,
            stage="extracting",
            progress=38,
            detail=(
                "Opening the document in a resource-limited extraction process."
                if settings.ingestion_process_isolation_enabled
                else "Opening the document in the bounded extraction step."
            ),
            lease_seconds=settings.ingestion_job_lease_seconds,
        )
        if job["filename"].lower().endswith(".pdf"):
            repository.update_ingestion_job(
                job["id"],
                worker_id,
                stage="ocr",
                progress=58,
                detail="Inspecting PDF pages and OCRing weak pages when needed.",
                lease_seconds=settings.ingestion_job_lease_seconds,
            )
        extracted = extract_in_isolated_process(
            job["filename"], job["content_type"], content, settings
        )
        chunks = chunk_pages(extracted.pages)
        if len(chunks) > settings.max_chunks_per_document:
            raise HTTPException(status_code=413, detail="Document creates too many chunks")

        repository.update_ingestion_job(
            job["id"],
            worker_id,
            stage="indexing",
            progress=85,
            detail="Creating page-aware evidence and the searchable index.",
            lease_seconds=settings.ingestion_job_lease_seconds,
        )
        promoted_reference = storage.promote(
            job["storage_path"],
            principal.tenant_id,
            job["document_id"],
            Path(job["filename"]).suffix,
            extracted.content_type,
        )
        try:
            repository.add_document(
                extracted,
                chunks,
                job["classification"],
                job.get("source"),
                principal,
                document_id=job["document_id"],
                storage_path=promoted_reference,
                collection=job.get("collection"),
                version=job.get("version"),
                effective_date=job.get("effective_date"),
                expires_at=job.get("expires_at"),
                supersedes_document_id=job.get("supersedes_document_id"),
            )
        except Exception:
            storage.delete(promoted_reference)
            promoted_reference = None
            raise
        repository.finish_ingestion_job(job["id"], worker_id, promoted_reference)
        storage.delete(job["storage_path"])
        repository.audit(
            principal,
            "document_ingested_async",
            "document",
            job["document_id"],
            {
                "job_id": job["id"],
                "pages": extracted.page_count,
                "method": extracted.extraction_method,
                "attempt": job["attempts"],
            },
        )
    except DuplicateDocumentError:
        repository.fail_ingestion_job(
            job["id"],
            worker_id,
            code="duplicate_document",
            message="An identical document already exists at this classification.",
            action="Delete this job or upload a newer document version.",
            retryable=False,
        )
    except HTTPException as exc:
        code, message, action = _error_detail(exc)
        repository.fail_ingestion_job(
            job["id"], worker_id, code=code, message=message, action=action
        )
    except Exception:
        logger.exception("ingestion_job_failed job_id=%s", job["id"])
        repository.fail_ingestion_job(
            job["id"],
            worker_id,
            code="worker_error",
            message="The ingestion worker encountered an unexpected error.",
            action="Retry the job. If it fails again, delete it and inspect the service logs.",
        )


async def run_ingestion_worker(
    repository: Repository,
    storage: OriginalStorage,
    settings: Settings,
    stop: asyncio.Event,
    malware_scanner: MalwareScanner,
) -> None:
    worker_id = f"{os.getpid()}-{uuid4()}"
    logger.info("ingestion_worker_started worker_id=%s", worker_id)
    while not stop.is_set():
        job = await asyncio.to_thread(
            repository.claim_ingestion_job,
            worker_id,
            settings.ingestion_job_lease_seconds,
        )
        if job:
            await asyncio.to_thread(
                process_ingestion_job,
                job,
                worker_id=worker_id,
                repository=repository,
                storage=storage,
                settings=settings,
                malware_scanner=malware_scanner,
            )
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.ingestion_worker_poll_seconds)
        except TimeoutError:
            pass
    logger.info("ingestion_worker_stopped worker_id=%s", worker_id)


async def stop_ingestion_worker(stop: asyncio.Event, task: asyncio.Task[None]) -> None:
    stop.set()
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _run_standalone() -> None:
    settings = Settings()
    settings.validate_database()
    settings.validate_storage()
    settings.validate_ingestion_security()
    database = build_database(settings)
    storage = build_document_storage(settings)
    scanner = build_malware_scanner(settings)
    repository = Repository(
        database,
        semantic_candidate_limit=settings.semantic_candidate_limit,
        semantic_min_score=settings.semantic_min_score,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(signal_name, stop.set)
    try:
        database.initialize()
        if settings.malware_scan_required and not scanner.ping():
            raise RuntimeError("Required private malware scanner is unavailable")
        await run_ingestion_worker(repository, storage, settings, stop, scanner)
    finally:
        database.close()


def main() -> int:
    try:
        asyncio.run(_run_standalone())
    except KeyboardInterrupt:
        return 0
    except Exception:
        logger.exception("standalone_ingestion_worker_failed")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
