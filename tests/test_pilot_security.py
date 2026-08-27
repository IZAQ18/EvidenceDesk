import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

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
        assert robots.text == "User-agent: *\nAllow: /about\nDisallow: /\n"

        public_site = client.get("/about")
        assert public_site.status_code == 200
        assert "Support IZAQ" in public_site.text
        assert "40%" in public_site.text
        assert "registered charity" in public_site.text
        assert "Ahsan Maqbool Ahmad" in public_site.text
        assert "PK58 MUCB 1160 9811 3100 4752" in public_site.text
        assert "Muhammad Qasim Ayub" in public_site.text
        assert "/logo.svg" in public_site.text
        assert "PK83 NAYA 1234 5030 6196 1996" in public_site.text
        assert "passed the standard IBAN checksum" in public_site.text
        assert "$9" in public_site.text
        assert "$149" in public_site.text
        assert "Validation pricing only" in public_site.text
        assert public_site.headers["x-robots-tag"] == "index, follow"
        assert "public" in public_site.headers["cache-control"]

        locked_page = client.get("/", follow_redirects=False)
        assert locked_page.status_code == 307
        assert locked_page.headers["location"] == "/access"

        locked_api = client.get("/v1/documents")
        assert locked_api.status_code == 401
        assert locked_api.json()["detail"] == "Pilot access required"

        rejected = client.post("/access", data={"access_code": "incorrect-access-code"})
        assert rejected.status_code == 401
        assert "not accepted" in rejected.text
        assert "Support IZAQ" in rejected.text
        assert "PK58 MUCB 1160 9811 3100 4752" in rejected.text
        assert "PK83 NAYA 1234 5030 6196 1996" in rejected.text

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
        assert "plansView" in workspace.text
        assert "supportDock" in workspace.text
        assert "40% pledged to charitable giving" in workspace.text
        assert "/developer-signature.png" in workspace.text
        assert "/logo.svg" in workspace.text
        assert "Maximum file size" in workspace.text
        assert "Ahsan Maqbool Ahmad" in workspace.text
        assert "Muhammad Qasim Ayub" in workspace.text
        assert "const PILOT_MODE=true" in workspace.text
        assert 'const SUPPORT_URL=""' in workspace.text
        assert "__SOCIAL_CARD_URL__" not in workspace.text
        assert "__SUPPORT_URL__" not in workspace.text

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
        developer_signature = client.get("/developer-signature.png")
        assert developer_signature.status_code == 200
        assert developer_signature.headers["content-type"] == "image/png"
        logo = client.get("/logo.svg")
        assert logo.status_code == 200
        assert logo.headers["content-type"] == "image/svg+xml"


def test_support_link_is_https_and_injected_as_data(tmp_path):
    settings = Settings(
        _env_file=None,
        db_path=tmp_path / "support.db",
        document_store_path=tmp_path / "originals",
        support_url="https://checkout.example.com/evidencedesk",
    )

    with TestClient(create_app(settings)) as client:
        workspace = client.get("/")
        assert 'const SUPPORT_URL="https://checkout.example.com/evidencedesk"' in workspace.text

    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(_env_file=None, support_url="javascript:alert(1)")


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
