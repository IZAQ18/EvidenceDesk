from __future__ import annotations

from tests.conftest import auth, ingest


def test_health_and_security_headers(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert len(response.headers["x-request-id"]) == 32
    assert response.headers["x-frame-options"] == "DENY"
    readiness = client.get("/ready")
    assert readiness.status_code == 200
    assert readiness.json() == {
        "status": "ready",
        "checks": {"database": True, "original_storage": True},
    }


def test_authentication_is_required(client):
    response = client.get("/v1/documents")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API key"


def test_ingest_list_and_delete(client):
    created = ingest(
        client,
        "Parental leave provides twelve paid weeks after a birth or adoption. Employees notify People Operations.",
    )
    assert created.status_code == 201, created.text
    document = created.json()
    assert document["classification"] == "internal"
    assert document["sha256"]
    assert document["chunk_count"] == 1
    assert document["page_count"] == 1
    assert document["word_count"] > 5
    assert document["extraction_method"] == "plain-text"

    listed = client.get("/v1/documents", headers=auth()).json()
    assert [item["id"] for item in listed] == [document["id"]]

    deleted = client.delete(f"/v1/documents/{document['id']}", headers=auth())
    assert deleted.status_code == 204
    assert client.get("/v1/documents", headers=auth()).json() == []


def test_duplicate_upload_is_rejected(client):
    text = "This policy text is long enough to ingest and deliberately identical on both attempts."
    assert ingest(client, text).status_code == 201
    response = ingest(client, text)
    assert response.status_code == 409


def test_reader_cannot_ingest_or_delete(client):
    response = ingest(client, "A sufficiently long public handbook paragraph for testing.", key="reader-a")
    assert response.status_code == 403


def test_upload_allowlist_and_pdf_signature(client):
    executable = client.post(
        "/v1/documents",
        headers=auth(),
        files={"file": ("payload.exe", b"not actually executable", "application/octet-stream")},
        data={"classification": "internal"},
    )
    assert executable.status_code == 415

    fake_pdf = client.post(
        "/v1/documents",
        headers=auth(),
        files={"file": ("fake.pdf", b"this is not a PDF but it is long enough", "application/pdf")},
        data={"classification": "internal"},
    )
    assert fake_pdf.status_code == 415


def test_source_control_characters_are_removed(client):
    response = client.post(
        "/v1/documents",
        headers=auth(),
        files={"file": ("policy.txt", b"A valid policy sentence with enough content for extraction.", "text/plain")},
        data={"classification": "internal", "source": "People\x00Ops\n"},
    )
    assert response.status_code == 201
    assert response.json()["source"] == "PeopleOps"


def test_capabilities_are_exposed_to_authenticated_users(client):
    response = client.get("/v1/capabilities", headers=auth())
    assert response.status_code == 200
    result = response.json()
    assert result["answer_mode"] == "hybrid-semantic-extractive-citation-verified"
    assert result["retrieval"]["neural_embeddings"] is False
    assert result["ocr"]["engine"] == "PyMuPDF + Tesseract"
    assert ".pdf" in result["supported_files"]
    assert "conversations" in result["features"]
