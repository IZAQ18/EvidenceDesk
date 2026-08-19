from __future__ import annotations

import builtins
import io
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pypdf import PdfWriter

from app.config import Settings
from app.ingestion import _extract_pdf, ocr_capability


class FakePage:
    def get_text(self, *_args, textpage=None, **_kwargs):
        if textpage is not None:
            return (
                "Scanned policy evidence states that employees receive sixteen annual leave days. "
                "Requests must be submitted to People Operations before the planned absence and "
                "approved by the employee's manager whenever advance notice is practical."
            )
        return ""

    def get_textpage_ocr(self, **kwargs):
        assert kwargs["language"] == "eng"
        assert kwargs["dpi"] == 200
        assert kwargs["full"] is True
        return object()


class FakeDocument:
    needs_pass = False
    page_count = 1

    def __init__(self):
        self.pages = [FakePage()]
        self.closed = False

    def __iter__(self):
        return iter(self.pages)

    def __getitem__(self, index):
        return self.pages[index]

    def close(self):
        self.closed = True


def test_scanned_page_uses_bounded_ocr(monkeypatch, tmp_path):
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    (tessdata / "eng.traineddata").write_bytes(b"test-model")
    document = FakeDocument()
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=lambda **_kwargs: document))
    settings = Settings(
        env="test",
        db_path=tmp_path / "test.db",
        tessdata_path=tessdata,
        ocr_min_words_per_page=12,
    )

    pages, method, warning = _extract_pdf(b"%PDF-fake", settings)

    assert method == "ocr"
    assert warning is None
    assert pages[0].ocr_used is True
    assert "sixteen annual leave days" in pages[0].text
    assert document.closed is True
    assert ocr_capability(settings)["available"] is True


def test_scanned_pdf_has_actionable_error_when_ocr_adapter_is_missing(
    monkeypatch, tmp_path
):
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    stream = io.BytesIO()
    writer.write(stream)
    original_import = builtins.__import__

    def without_pymupdf(name, *args, **kwargs):
        if name == "pymupdf":
            raise ImportError("simulated missing adapter")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_pymupdf)
    settings = Settings(env="test", db_path=tmp_path / "test.db")

    with pytest.raises(HTTPException) as error:
        _extract_pdf(stream.getvalue(), settings)

    assert error.value.status_code == 422
    assert error.value.detail["code"] == "ocr_unavailable"
    assert "searchable PDF" in error.value.detail["action"]
