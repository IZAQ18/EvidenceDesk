from __future__ import annotations

import json
import sys

from app import extraction_runner


def _run(tmp_path, monkeypatch, *, content: bytes, request: object, max_chars: int = 10_000):
    input_path = tmp_path / "input"
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    input_path.write_bytes(content)
    request_path.write_text(json.dumps(request), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "extraction_runner",
            str(input_path),
            str(request_path),
            str(response_path),
            "256",
            "30",
            str(max_chars),
        ],
    )
    code = extraction_runner.main()
    return code, json.loads(response_path.read_text(encoding="utf-8"))


def test_runner_rejects_invalid_process_contract(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["extraction_runner"])
    assert extraction_runner.main() == 2


def test_runner_extracts_plain_text_and_writes_private_result(tmp_path, monkeypatch):
    code, response = _run(
        tmp_path,
        monkeypatch,
        content=b"Synthetic policy evidence contains five trustworthy words.",
        request={
            "filename": "policy.txt",
            "content_type": "text/plain",
            "settings": {"ocr_enabled": False},
        },
    )
    assert code == 0
    assert response["ok"] is True
    assert (
        response["pages"][0]["text"]
        == "Synthetic policy evidence contains five trustworthy words."
    )
    assert response["extraction_method"] == "plain-text"


def test_runner_enforces_extracted_text_limit(tmp_path, monkeypatch):
    code, response = _run(
        tmp_path,
        monkeypatch,
        content=b"synthetic policy text definitely exceeds this limit",
        request={
            "filename": "policy.txt",
            "content_type": "text/plain",
            "settings": {"ocr_enabled": False},
        },
        max_chars=5,
    )
    assert code == 3
    assert response == {
        "ok": False,
        "status_code": 413,
        "detail": "Extracted text exceeds the safety limit",
    }


def test_runner_returns_generic_error_for_invalid_request(tmp_path, monkeypatch):
    code, response = _run(
        tmp_path,
        monkeypatch,
        content=b"synthetic",
        request={"unexpected": "shape"},
    )
    assert code == 4
    assert response == {
        "ok": False,
        "status_code": 422,
        "detail": "The isolated extraction worker rejected the document.",
    }
