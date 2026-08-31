from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("EVIDENCEDESK_RUN_BROWSER_TESTS") != "1",
    reason="Set EVIDENCEDESK_RUN_BROWSER_TESTS=1 after installing Chromium",
)


def _free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def _wait_for_server(url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"EvidenceDesk exited with code {process.returncode}")
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("EvidenceDesk did not become healthy within 30 seconds")


def test_full_citation_journey_and_mobile_layout(tmp_path: Path) -> None:
    from playwright.sync_api import expect, sync_playwright

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    environment = os.environ.copy()
    repository_root = Path(__file__).resolve().parents[1]
    environment.update(
        {
            "EVIDENCEDESK_ENV": "test",
            "EVIDENCEDESK_DB_PATH": str(tmp_path / "browser.db"),
            "EVIDENCEDESK_DOCUMENT_STORE_PATH": str(tmp_path / "originals"),
            "EVIDENCEDESK_RATE_LIMIT_PER_MINUTE": "10000",
            "EVIDENCEDESK_INGESTION_PROCESS_ISOLATION_ENABLED": "false",
            "EVIDENCEDESK_ACCOUNTS_ENABLED": "false",
            "EVIDENCEDESK_ACCOUNT_REGISTRATION_ENABLED": "false",
            "EVIDENCEDESK_GOOGLE_OAUTH_ENABLED": "false",
            "EVIDENCEDESK_GOOGLE_OAUTH_REGISTRATION_ENABLED": "false",
            "EVIDENCEDESK_EPHEMERAL_ACCOUNT_TESTING_ENABLED": "false",
            "PYTHONPATH": os.pathsep.join(
                [str(repository_root), environment.get("PYTHONPATH", "")]
            ).rstrip(os.pathsep),
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=environment,
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_server(base_url, process)
        handbook = tmp_path / "Synthetic-Leave-Policy.md"
        handbook.write_text(
            "# Annual leave policy\n"
            "Employees receive twenty annual leave days in each calendar year. "
            "Requests should be submitted to the manager at least five working days in advance.",
            encoding="utf-8",
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            page.goto(base_url)
            if page.locator("#skipIntro").is_visible():
                page.locator("#skipIntro").click()
            page.locator("#key").fill("dev-admin-key")
            page.locator("#connect").click()
            expect(page.locator("#connectStatus")).to_contain_text("Connected")

            page.locator('[data-view="library"]').click()
            page.locator("#file").set_input_files(handbook)
            page.locator("#upload button").click()
            expect(page.locator("#docs")).to_contain_text(
                "Synthetic-Leave-Policy.md", timeout=30_000
            )

            page.locator('[data-view="workspace"]').click()
            page.locator("#question").fill("How many annual leave days do employees receive?")
            page.locator("#ask").click()
            expect(page.locator(".bubble.assistant").last).to_contain_text(
                "twenty annual leave days", timeout=20_000
            )
            expect(page.locator(".bubble.assistant").last.locator(".citation-btn")).to_have_count(1)
            page.locator(".bubble.assistant").last.locator(".citation-btn").click()
            expect(page.locator("#viewerBody")).to_contain_text("twenty annual leave days")

            page.locator('[data-view="library"]').click()
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator("[data-delete]").click()
            expect(page.locator("#docs")).not_to_contain_text("Synthetic-Leave-Policy.md")

            mobile = browser.new_page(viewport={"width": 390, "height": 844})
            mobile.goto(f"{base_url}/about")
            overflow = mobile.evaluate("document.documentElement.scrollWidth > innerWidth")
            assert overflow is False
            expect(mobile.locator("h1")).to_contain_text("Evidence before confidence")
            browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
