from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi import HTTPException

from app.config import Settings
from app.ingestion import extract_bytes


def _limits(memory_mb: int, cpu_seconds: int) -> None:
    if os.name == "nt":
        return
    try:
        import resource

        memory_bytes = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    except (ImportError, OSError, ValueError):
        pass


def _write(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main() -> int:
    if len(sys.argv) != 7:
        return 2
    input_path, request_path, response_path = map(Path, sys.argv[1:4])
    memory_mb, cpu_seconds, max_chars = map(int, sys.argv[4:7])
    _limits(memory_mb, cpu_seconds)
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        content = input_path.read_bytes()
        settings = Settings(_env_file=None, env="test", **request["settings"])
        extracted = extract_bytes(
            request["filename"], request["content_type"], content, settings
        )
        if sum(len(page.text) for page in extracted.pages) > max_chars:
            raise HTTPException(status_code=413, detail="Extracted text exceeds the safety limit")
        _write(
            response_path,
            {
                "ok": True,
                "filename": extracted.filename,
                "content_type": extracted.content_type,
                "sha256": extracted.sha256,
                "extraction_method": extracted.extraction_method,
                "extraction_warning": extracted.extraction_warning,
                "pages": [
                    {
                        "page_number": page.page_number,
                        "text": page.text,
                        "ocr_used": page.ocr_used,
                    }
                    for page in extracted.pages
                ],
            },
        )
        return 0
    except HTTPException as exc:
        _write(
            response_path,
            {"ok": False, "status_code": exc.status_code, "detail": exc.detail},
        )
        return 3
    except Exception:
        _write(
            response_path,
            {
                "ok": False,
                "status_code": 422,
                "detail": "The isolated extraction worker rejected the document.",
            },
        )
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
