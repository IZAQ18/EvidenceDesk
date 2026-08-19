from __future__ import annotations

import hashlib
import io
import os
import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile
from pypdf import PdfReader
from starlette.concurrency import run_in_threadpool

from app.config import Settings

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}
ALLOWED_CONTENT_TYPES = {
    "text/plain",
    "text/markdown",
    "application/pdf",
    "application/octet-stream",
}
INJECTION_PATTERNS = re.compile(
    r"(?i)(ignore\s+(all\s+)?previous|system\s+prompt|developer\s+message|"
    r"reveal\s+(your|the)\s+(instructions|prompt)|execute\s+(this|the)\s+(tool|command))"
)


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str
    ocr_used: bool = False


@dataclass(frozen=True)
class TextChunk:
    text: str
    page_number: int | None


@dataclass(frozen=True)
class ExtractedDocument:
    filename: str
    content_type: str
    content: bytes
    pages: list[ExtractedPage]
    sha256: str
    extraction_method: str
    extraction_warning: str | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text)

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def page_count(self) -> int:
        return len(self.pages)


def _safe_filename(raw: str | None) -> str:
    name = Path(raw or "document").name
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    return cleaned[:180] or "document"


def _clean_text(text: str) -> str:
    text = re.sub(r"\x00", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text, flags=re.UNICODE))


def _find_tessdata(settings: Settings) -> Path | None:
    candidates = [
        settings.tessdata_path,
        Path(value) if (value := os.getenv("TESSDATA_PREFIX")) else None,
        Path("/usr/share/tesseract-ocr/5/tessdata"),
        Path("/usr/share/tesseract-ocr/4.00/tessdata"),
        Path("C:/Program Files/Tesseract-OCR/tessdata"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tessdata"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_dir() and (candidate / "eng.traineddata").exists():
            return candidate
    return None


def ocr_capability(settings: Settings) -> dict[str, object]:
    try:
        import pymupdf  # noqa: F401

        pymupdf_available = True
    except ImportError:
        pymupdf_available = False
    tessdata = _find_tessdata(settings)
    available = settings.ocr_enabled and pymupdf_available and tessdata is not None
    if available:
        detail = f"OCR ready for {settings.ocr_languages} documents."
    elif not settings.ocr_enabled:
        detail = "OCR is disabled by configuration."
    elif not pymupdf_available:
        detail = "Install project dependencies to enable the PyMuPDF OCR adapter."
    else:
        detail = "Install Tesseract language data or configure EVIDENCEDESK_TESSDATA_PATH."
    return {
        "available": available,
        "enabled": settings.ocr_enabled,
        "engine": "PyMuPDF + Tesseract",
        "languages": settings.ocr_languages,
        "max_pages": settings.ocr_max_pages,
        "detail": detail,
    }


def _extract_with_pypdf(content: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted:
        try:
            if reader.decrypt("") == 0:
                raise HTTPException(status_code=422, detail="Password-protected PDFs are not supported")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Encrypted PDF could not be opened") from exc
    return [_clean_text(page.extract_text() or "") for page in reader.pages]


def _extract_pdf(content: bytes, settings: Settings) -> tuple[list[ExtractedPage], str, str | None]:
    try:
        import pymupdf
    except ImportError:
        embedded_pages = _extract_with_pypdf(content)
        if len(embedded_pages) > settings.max_pdf_pages:
            raise HTTPException(
                status_code=413, detail="PDF exceeds the configured page limit"
            ) from None
        total_words = sum(_word_count(text) for text in embedded_pages)
        if total_words < max(20, len(embedded_pages) * 5):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "ocr_unavailable",
                    "message": "This PDF appears scanned or has no usable text layer. OCR is unavailable.",
                    "action": "Install OCR dependencies or upload a searchable PDF, TXT, or Markdown file.",
                },
            ) from None
        pages = [ExtractedPage(index, text) for index, text in enumerate(embedded_pages, 1)]
        return pages, "pypdf-embedded", "The PyMuPDF OCR adapter was unavailable."

    try:
        document = pymupdf.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise HTTPException(status_code=422, detail="The PDF could not be safely opened") from exc
    if document.needs_pass:
        document.close()
        raise HTTPException(status_code=422, detail="Password-protected PDFs are not supported")
    if document.page_count > settings.max_pdf_pages:
        document.close()
        raise HTTPException(status_code=413, detail="PDF exceeds the configured page limit")

    try:
        embedded = [_clean_text(page.get_text("text", sort=True)) for page in document]
        weak_indexes = [
            index
            for index, text in enumerate(embedded)
            if _word_count(text) < settings.ocr_min_words_per_page
        ]
        ocr_indexes: set[int] = set()
        tessdata = _find_tessdata(settings)
        if weak_indexes and settings.ocr_enabled and tessdata:
            if len(weak_indexes) > settings.ocr_max_pages:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"PDF needs OCR on {len(weak_indexes)} pages; the safety limit is "
                        f"{settings.ocr_max_pages}. Split the document or raise the configured limit."
                    ),
                )
            for index in weak_indexes:
                page = document[index]
                try:
                    text_page = page.get_textpage_ocr(
                        language=settings.ocr_languages,
                        dpi=settings.ocr_dpi,
                        full=True,
                        tessdata=str(tessdata),
                    )
                    recognized = _clean_text(page.get_text("text", textpage=text_page, sort=True))
                except Exception as exc:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "ocr_failed",
                            "message": f"OCR failed on page {index + 1}.",
                            "action": "Check OCR languages or upload a searchable copy.",
                        },
                    ) from exc
                if _word_count(recognized) > _word_count(embedded[index]):
                    embedded[index] = recognized
                    ocr_indexes.add(index)

        total_words = sum(_word_count(text) for text in embedded)
        remaining_weak = [
            index
            for index, text in enumerate(embedded)
            if _word_count(text) < settings.ocr_min_words_per_page
        ]
        if total_words < max(20, document.page_count * 5):
            capability = ocr_capability(settings)
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "ocr_required" if not capability["available"] else "insufficient_text",
                    "message": "The PDF does not contain enough reliable searchable text.",
                    "action": capability["detail"],
                },
            )
        warning = None
        if remaining_weak:
            warning = f"{len(remaining_weak)} page(s) contained little or no extractable text."
        if ocr_indexes and len(ocr_indexes) == document.page_count:
            method = "ocr"
        elif ocr_indexes:
            method = "hybrid-embedded-ocr"
        else:
            method = "pymupdf-embedded"
        pages = [
            ExtractedPage(index + 1, text, index in ocr_indexes)
            for index, text in enumerate(embedded)
        ]
        return pages, method, warning
    finally:
        document.close()


async def extract_upload(upload: UploadFile, settings: Settings) -> ExtractedDocument:
    filename = _safe_filename(upload.filename)
    extension = Path(filename).suffix.lower()
    content_type = (upload.content_type or "application/octet-stream").lower()
    if extension not in ALLOWED_EXTENSIONS or content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="Only TXT, Markdown, and PDF files are supported")

    content = await upload.read(settings.max_upload_bytes + 1)
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="The uploaded file exceeds the size limit")

    try:
        if extension == ".pdf":
            if not content.startswith(b"%PDF-"):
                raise HTTPException(status_code=415, detail="The file does not have a valid PDF signature")
            pages, method, warning = await run_in_threadpool(_extract_pdf, content, settings)
        else:
            text = _clean_text(content.decode("utf-8-sig"))
            pages, method, warning = [ExtractedPage(1, text)], "plain-text", None
    except HTTPException:
        raise
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Text files must use UTF-8 encoding") from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail="The document could not be safely extracted") from exc

    if sum(_word_count(page.text) for page in pages) < 5:
        raise HTTPException(status_code=422, detail="The document contains too little extractable text")
    return ExtractedDocument(
        filename=filename,
        content_type="application/pdf" if extension == ".pdf" else "text/plain",
        content=content,
        pages=pages,
        sha256=hashlib.sha256(content).hexdigest(),
        extraction_method=method,
        extraction_warning=warning,
    )


def chunk_pages(
    pages: list[ExtractedPage], size_words: int = 180, overlap_words: int = 35
) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for page in pages:
        words = page.text.split()
        start = 0
        while start < len(words):
            end = min(start + size_words, len(words))
            chunks.append(TextChunk(" ".join(words[start:end]), page.page_number))
            if end == len(words):
                break
            start = end - overlap_words
    return chunks


def chunk_text(text: str, size_words: int = 180, overlap_words: int = 35) -> list[str]:
    """Compatibility helper retained for direct unit use."""
    return [
        chunk.text
        for chunk in chunk_pages([ExtractedPage(1, text)], size_words, overlap_words)
    ]


def has_injection_language(text: str) -> bool:
    return bool(INJECTION_PATTERNS.search(text))
