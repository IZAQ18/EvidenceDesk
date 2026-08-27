from __future__ import annotations

from tests.conftest import auth, ingest


def test_original_page_archive_and_restore_workflow(client):
    created = ingest(
        client,
        "The annual leave policy provides fifteen paid days each calendar year.",
        name="leave-policy.txt",
    ).json()
    assert created["has_original"] is True

    original = client.get(f"/v1/documents/{created['id']}/content", headers=auth())
    assert original.status_code == 200
    assert "fifteen paid days" in original.text

    page = client.get(f"/v1/documents/{created['id']}/pages/1", headers=auth()).json()
    assert page["page_number"] == 1
    assert "annual leave" in page["text"]

    archived = client.patch(
        f"/v1/documents/{created['id']}/status",
        headers=auth(),
        json={"status": "archived"},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    refused = client.post(
        "/v1/answers",
        headers=auth(),
        json={"question": "How many annual leave days are provided?"},
    ).json()
    assert refused["grounded"] is False

    restored = client.patch(
        f"/v1/documents/{created['id']}/status",
        headers=auth(),
        json={"status": "active"},
    )
    assert restored.json()["status"] == "active"


def test_conversation_feedback_and_report_workflow(client):
    ingest(
        client,
        "Employees report suspected misconduct to People Operations or the ethics hotline.",
    )
    answered = client.post(
        "/v1/answers",
        headers=auth(),
        json={"question": "Where should employees report suspected misconduct?"},
    ).json()
    assert answered["conversation_id"]
    assert answered["message_id"]

    follow_up = client.post(
        "/v1/answers",
        headers=auth(),
        json={
            "question": "Can suspected misconduct be reported to People Operations?",
            "conversation_id": answered["conversation_id"],
        },
    ).json()
    assert follow_up["conversation_id"] == answered["conversation_id"]

    conversations = client.get("/v1/conversations", headers=auth()).json()
    assert conversations[0]["message_count"] == 4
    messages = client.get(
        f"/v1/conversations/{answered['conversation_id']}/messages", headers=auth()
    ).json()
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]

    feedback = client.post(
        "/v1/feedback",
        headers=auth(),
        json={"message_id": answered["message_id"], "category": "helpful"},
    )
    assert feedback.status_code == 201
    report = client.get(
        f"/v1/messages/{answered['message_id']}/report", headers=auth()
    )
    assert report.status_code == 200
    assert "# EvidenceDesk evidence report" in report.text
    assert "Where should employees" in report.text


def test_anonymous_pilot_scorecard_is_validated_and_aggregated(client):
    payload = {
        "reviewer_context": "people_ops",
        "ease_score": 4,
        "trust_score": 5,
        "usefulness_score": 4,
        "completed_tasks": 5,
        "would_return": "yes",
        "most_valuable": "Opening the exact source passage.",
        "improvement": "Make the first safe-refusal example more obvious.",
    }
    unauthorized = client.post("/v1/pilot-reviews", json=payload)
    assert unauthorized.status_code == 401

    created = client.post("/v1/pilot-reviews", headers=auth(), json=payload)
    assert created.status_code == 201
    assert created.json()["review_id"]

    invalid = client.post(
        "/v1/pilot-reviews",
        headers=auth(),
        json={**payload, "trust_score": 6},
    )
    assert invalid.status_code == 422

    metrics = client.get("/v1/admin/metrics", headers=auth()).json()["pilot_reviews"]
    assert metrics["review_count"] == 1
    assert metrics["avg_ease_score"] == 4
    assert metrics["avg_trust_score"] == 5
    assert metrics["avg_usefulness_score"] == 4
    assert metrics["avg_completed_tasks"] == 5
    assert metrics["would_return_yes"] == 1


def test_workspace_exposes_guided_pilot_without_requesting_contact_details(client):
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="pilotView"' in page.text
    assert '<button class="tab active" data-view="pilot">Pilot ' in page.text
    assert "(How to use EvidenceDesk)" in page.text
    assert 'id="workspaceView" class="view workspace hidden"' in page.text
    assert 'id="pilotReview"' in page.text
    assert "Five-minute evidence test" in page.text
    assert 'id="supportNudge"' in page.text
    assert "Swipe left or right to dismiss" in page.text
    assert "registerMeaningfulUse()" in page.text
    assert "body{zoom:1.25}" in page.text
    assert "||'cobalt'" in page.text
    assert "$9" in page.text
    assert "$149" in page.text
    assert "documents stored" in page.text
    assert "cited questions" in page.text
    assert "storage limit, not a monthly upload limit" in page.text
    assert 'name="email"' not in page.text

    public_page = client.get("/about")
    assert public_page.status_code == 200
    assert "5</b>documents stored at one time" in public_page.text
    assert "100</b>cited questions every month" in public_page.text
    assert "100</b>documents stored at one time" in public_page.text
    assert "2,000</b>cited questions every month" in public_page.text


def test_collections_versions_supersession_bulk_and_metrics(client):
    first = client.post(
        "/v1/documents",
        headers=auth(),
        files={
            "file": (
                "security-policy-v1.txt",
                b"Version one requires passwords with at least twelve characters for every account.",
                "text/plain",
            )
        },
        data={
            "classification": "internal",
            "source": "Security",
            "collection": "Security policies",
            "version": "1.0",
            "effective_date": "2026-01-01",
        },
    ).json()
    second = client.post(
        "/v1/documents",
        headers=auth(),
        files={
            "file": (
                "security-policy-v2.txt",
                b"Version two requires passwords with at least sixteen characters for every account.",
                "text/plain",
            )
        },
        data={
            "classification": "internal",
            "source": "Security",
            "collection": "Security policies",
            "version": "2.0",
            "effective_date": "2026-06-01",
            "supersedes_document_id": first["id"],
        },
    ).json()
    documents = client.get("/v1/documents", headers=auth()).json()
    prior = next(item for item in documents if item["id"] == first["id"])
    assert prior["status"] == "superseded"
    assert second["collection"] == "Security policies"

    bulk = client.post(
        "/v1/documents/bulk",
        headers=auth(),
        json={"document_ids": [second["id"]], "action": "archive"},
    )
    assert bulk.json()["affected"] == 1
    metrics = client.get("/v1/admin/metrics", headers=auth()).json()
    assert metrics["documents"]["total"] == 2
    assert metrics["documents"]["archived"] == 1
    assert metrics["recent_audit"]


def test_controlled_query_expansion_finds_common_synonym(client):
    ingest(
        client,
        "The annual leave entitlement is eighteen paid days during each calendar year.",
        name="annual-leave.txt",
    )
    result = client.post(
        "/v1/answers",
        headers=auth(),
        json={"question": "What is the vacation entitlement?"},
    ).json()
    assert result["grounded"] is True
    assert "eighteen paid days" in result["answer"].lower()
    assert result["evidence_quality"] in {"strong", "partial"}


def test_guided_pilot_unrelated_question_produces_safe_refusal(client):
    ingest(
        client,
        "Employees receive annual leave and managers approve requests based on team coverage.",
        name="fictional-handbook.txt",
    )
    result = client.post(
        "/v1/answers",
        headers=auth(),
        json={"question": "Which cryptocurrency tokens receive quarterly dividends?"},
    ).json()
    assert result["grounded"] is False
    assert result["citations"] == []
    assert result["evidence_quality"] == "insufficient"


def test_multi_part_question_selects_multiple_sentences_from_one_chunk(client):
    ingest(
        client,
        (
            "Full-time employees receive 20 days of paid annual leave. "
            "Up to five unused annual-leave days may be carried into the next year. "
            "Employees may work remotely up to two days per week."
        ),
        name="handbook.txt",
    )
    result = client.post(
        "/v1/answers",
        headers=auth(),
        json={
            "question": (
                "How many annual leave days do full-time employees receive, "
                "and how much can carry over?"
            )
        },
    ).json()
    assert result["grounded"] is True
    assert "20 days" in result["answer"]
    assert "five unused" in result["answer"]
    assert "remotely" not in result["answer"]
