from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.walkthrough import EXAMPLES, walkthrough_page


def test_example_quotes_match_bundled_fictional_source():
    text = (Path(__file__).parents[1] / "app/demo_docs/Northstar-Employee-Handbook.md").read_text(encoding="utf-8")
    for example in EXAMPLES[:2]:
        assert example["source"] in text
    assert "Insufficient evidence" == EXAMPLES[2]["status"]
    assert "guaranteed salary increase" in EXAMPLES[2]["answer"]


def test_walkthrough_does_not_unlock_private_workspace(tmp_path):
    settings = Settings(_env_file=None, env="test", db_path=tmp_path / "demo.db",
                        document_store_path=tmp_path / "originals",
                        pilot_access_code="test-private-code-123456")
    with TestClient(create_app(settings)) as client:
        response = client.get("/demo")
        assert response.status_code == 200
        assert "prepared illustrations, not a live search or model response" in response.text
        assert "fictional documents" in response.text
        assert not response.cookies
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/v1/documents").status_code == 401
        assert client.get("/", follow_redirects=False).status_code == 307


def test_walkthrough_renders_only_fixed_examples_and_escapes_link():
    html = walkthrough_page('/signin?next="<script>')
    assert '&quot;&lt;script&gt;' in html
    assert html.count('class="question"') == 3
    assert 'id="source-0"' in html
    assert 'aria-pressed="true"' in html
    assert "fetch(" not in html
