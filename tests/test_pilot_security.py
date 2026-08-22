from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_protected_pilot_gate_and_seeded_content(tmp_path):
    settings = Settings(
        _env_file=None,
        env="production",
        db_path=tmp_path / "pilot.db",
        document_store_path=tmp_path / "originals",
        pilot_access_code="correct-horse-battery-staple",
        seed_demo_content=True,
        rate_limit_per_minute=10_000,
    )

    with TestClient(create_app(settings), base_url="https://testserver") as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
        assert "max-age=31536000" in health.headers["strict-transport-security"]

        robots = client.get("/robots.txt")
        assert robots.text == "User-agent: *\nDisallow: /\n"

        locked_page = client.get("/", follow_redirects=False)
        assert locked_page.status_code == 307
        assert locked_page.headers["location"] == "/access"

        locked_api = client.get("/v1/documents")
        assert locked_api.status_code == 401
        assert locked_api.json()["detail"] == "Pilot access required"

        rejected = client.post("/access", data={"access_code": "incorrect-access-code"})
        assert rejected.status_code == 401
        assert "not accepted" in rejected.text

        unlocked = client.post(
            "/access",
            data={"access_code": "correct-horse-battery-staple"},
            follow_redirects=False,
        )
        assert unlocked.status_code == 303
        assert "HttpOnly" in unlocked.headers["set-cookie"]
        assert "Secure" in unlocked.headers["set-cookie"]
        assert "SameSite=lax" in unlocked.headers["set-cookie"]

        workspace = client.get("/")
        assert workspace.status_code == 200
        assert "IZAQ" in workspace.text
        assert "const PILOT_MODE=true" in workspace.text
        assert "__SOCIAL_CARD_URL__" not in workspace.text

        documents = client.get("/v1/documents")
        assert documents.status_code == 200
        assert {item["filename"] for item in documents.json()} == {
            "Northstar-Code-of-Conduct.md",
            "Northstar-Employee-Handbook.md",
        }

        assert client.get("/docs").status_code == 404
        social_card = client.get("/social-card.png")
        assert social_card.status_code == 200
        assert social_card.headers["content-type"] == "image/png"


def test_production_without_authentication_fails_closed(tmp_path):
    settings = Settings(
        _env_file=None,
        env="production",
        db_path=tmp_path / "closed.db",
        document_store_path=tmp_path / "originals",
        api_keys={},
        pilot_access_code=None,
    )

    try:
        with TestClient(create_app(settings)):
            pass
    except RuntimeError as exc:
        assert "EVIDENCEDESK_API_KEYS" in str(exc)
    else:
        raise AssertionError("Production must not start without configured authentication")
