from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

from fastapi import HTTPException

from app.config import Settings
from app.ingestion import ExtractedDocument, ExtractedPage, extract_bytes

_MAX_ERROR_BYTES = 64_000


def _run_bounded_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: float,
    nice_level: int,
) -> int:
    """Run extraction below the web process priority and always reap it."""
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and internal arguments
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    if os.name != "nt" and nice_level and hasattr(os, "setpriority"):
        with suppress(OSError, PermissionError):
            os.setpriority(os.PRIO_PROCESS, process.pid, nice_level)
    try:
        return process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        with suppress(Exception):
            process.wait(timeout=5)
        raise


def _runner_environment() -> dict[str, str]:
    allowed = {
        "LANG",
        "LC_ALL",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TESSDATA_PREFIX",
        "WINDIR",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def _runner_settings(settings: Settings) -> dict[str, object]:
    return {
        "max_upload_bytes": settings.max_upload_bytes,
        "max_pdf_pages": settings.max_pdf_pages,
        "ocr_enabled": settings.ocr_enabled,
        "ocr_languages": settings.ocr_languages,
        "ocr_dpi": settings.ocr_dpi,
        "ocr_max_pages": settings.ocr_max_pages,
        "ocr_min_words_per_page": settings.ocr_min_words_per_page,
        "tessdata_path": str(settings.tessdata_path) if settings.tessdata_path else None,
    }


def extract_in_isolated_process(
    filename: str,
    content_type: str,
    content: bytes,
    settings: Settings,
) -> ExtractedDocument:
    if not settings.ingestion_process_isolation_enabled:
        return extract_bytes(filename, content_type, content, settings)

    with tempfile.TemporaryDirectory(prefix="evidencedesk-extract-") as directory:
        work = Path(directory)
        try:
            os.chmod(work, 0o700)
        except OSError:
            pass
        input_path = work / "input.bin"
        request_path = work / "request.json"
        response_path = work / "response.json"
        input_path.write_bytes(content)
        request_path.write_text(
            json.dumps(
                {
                    "filename": filename,
                    "content_type": content_type,
                    "settings": _runner_settings(settings),
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        for path in (input_path, request_path):
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        command = [
            sys.executable,
            "-m",
            "app.extraction_runner",
            str(input_path),
            str(request_path),
            str(response_path),
            str(settings.ingestion_worker_memory_mb),
            str(settings.ingestion_worker_cpu_seconds),
            str(settings.ingestion_max_extracted_chars),
        ]
        try:
            returncode = _run_bounded_process(
                command,
                cwd=Path(__file__).resolve().parent.parent,
                env=_runner_environment(),
                timeout_seconds=settings.ingestion_extraction_timeout_seconds,
                nice_level=settings.ingestion_worker_nice_level,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "extraction_timeout",
                    "message": "Document extraction exceeded the safety time limit.",
                    "action": "Split the document into smaller files or upload a searchable copy.",
                },
            ) from exc
        if not response_path.is_file() or response_path.stat().st_size > max(
            _MAX_ERROR_BYTES, settings.ingestion_max_extracted_chars * 4
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "isolated_worker_failed",
                    "message": "The isolated extraction worker did not return a safe result.",
                    "action": "Retry once, then inspect the worker logs if the problem continues.",
                },
            )
        try:
            response = json.loads(response_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail="Isolated extraction response was invalid") from exc
        if returncode != 0 or not response.get("ok"):
            raise HTTPException(
                status_code=int(response.get("status_code") or 422),
                detail=response.get("detail") or "The document could not be safely extracted",
            )
        pages = [ExtractedPage(**page) for page in response["pages"]]
        return ExtractedDocument(
            filename=response["filename"],
            content_type=response["content_type"],
            content=content,
            pages=pages,
            sha256=response["sha256"],
            extraction_method=response["extraction_method"],
            extraction_warning=response.get("extraction_warning"),
        )
