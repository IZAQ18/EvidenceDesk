from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from contextlib import suppress
from uuid import uuid4

from fastapi import HTTPException

from app.config import Settings
from app.ingestion import chunk_pages, extract_bytes, validate_upload_bytes
from app.models import Principal
from app.repository import DuplicateDocumentError, Repository
from app.storage import OriginalStorage

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
) -> None:
    principal = _principal(job)
    try:
        existing = repository.get_document(job["document_id"], principal)
        if existing:
            repository.finish_ingestion_job(job["id"], worker_id)
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
            stage="extracting",
            progress=38,
            detail="Opening the document in the isolated extraction step.",
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
        extracted = extract_bytes(job["filename"], job["content_type"], content, settings)
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
        repository.add_document(
            extracted,
            chunks,
            job["classification"],
            job.get("source"),
            principal,
            document_id=job["document_id"],
            storage_path=job["storage_path"],
            collection=job.get("collection"),
            version=job.get("version"),
            effective_date=job.get("effective_date"),
            expires_at=job.get("expires_at"),
            supersedes_document_id=job.get("supersedes_document_id"),
        )
        repository.finish_ingestion_job(job["id"], worker_id)
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
