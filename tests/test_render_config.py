from __future__ import annotations

from pathlib import Path


def test_render_blueprint_matches_private_google_acceptance_mode():
    blueprint = (Path(__file__).parents[1] / "render.yaml").read_text(encoding="utf-8")

    assert "EVIDENCEDESK_PILOT_ACCESS_CODE" not in blueprint
    assert "key: EVIDENCEDESK_ACCOUNTS_ENABLED\n        value: \"true\"" in blueprint
    assert (
        "key: EVIDENCEDESK_EPHEMERAL_ACCOUNT_TESTING_ENABLED\n"
        in blueprint
    )
    assert "key: EVIDENCEDESK_GOOGLE_OAUTH_ENABLED\n        value: \"true\"" in blueprint
    assert (
        "key: EVIDENCEDESK_GOOGLE_OAUTH_REGISTRATION_ENABLED\n"
        "        value: \"true\""
    ) in blueprint
    assert (
        "key: EVIDENCEDESK_PUBLIC_CUSTOMER_LAUNCH_ENABLED\n"
        "        value: \"false\""
    ) in blueprint
    assert (
        "key: EVIDENCEDESK_GOOGLE_OAUTH_CLIENT_SECRET\n"
        "        sync: false"
    ) in blueprint


def test_production_container_uses_path_only_application_request_logs():
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")

    assert "--no-access-log" in dockerfile
    assert "prevents OAuth codes" in dockerfile
