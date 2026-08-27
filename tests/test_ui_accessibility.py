from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser

from app.ui import UI_HTML


class _StructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.images: list[dict[str, str | None]] = []
        self.inputs: list[dict[str, str | None]] = []
        self.labels_for: set[str] = set()
        self.landmarks: Counter[str] = Counter()
        self.html_language: str | None = None
        self.viewport = False

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        if tag == "html":
            self.html_language = values.get("lang")
        if tag in {"header", "nav", "main"}:
            self.landmarks[tag] += 1
        if tag == "img":
            self.images.append(values)
        if tag in {"input", "select", "textarea"}:
            self.inputs.append(values)
        if tag == "label" and values.get("for"):
            self.labels_for.add(values["for"])
        if tag == "meta" and values.get("name") == "viewport":
            self.viewport = "width=device-width" in (values.get("content") or "")


def _page_structure() -> _StructureParser:
    parser = _StructureParser()
    parser.feed(UI_HTML)
    return parser


def test_workspace_has_stable_accessibility_landmarks_and_names():
    page = _page_structure()
    duplicates = [item for item, count in Counter(page.ids).items() if count > 1]

    assert page.html_language == "en"
    assert page.viewport is True
    assert page.landmarks["header"] == 1
    assert page.landmarks["nav"] >= 1
    assert page.landmarks["main"] == 1
    assert duplicates == []
    assert all("alt" in image for image in page.images)


def test_static_form_controls_have_programmatic_labels():
    page = _page_structure()
    unlabeled = []
    for control in page.inputs:
        if control.get("type") == "hidden":
            continue
        control_id = control.get("id")
        selector = (
            f"#{control_id}"
            if control_id
            else f'[name="{control.get("name")}"]'
            if control.get("name")
            else ""
        )
        statically_labeled = bool(
            control_id
            and (control_id in page.labels_for or control.get("aria-label"))
        )
        runtime_labeled = bool(selector and f"'{selector}':" in UI_HTML)
        if not statically_labeled and not runtime_labeled:
            unlabeled.append(selector or "unidentifiable-control")

    assert unlabeled == []


def test_motion_and_mobile_layout_have_regression_guards():
    assert "@media(prefers-reduced-motion:reduce)" in UI_HTML
    assert "@media(max-width:700px)" in UI_HTML
    assert "width=device-width,initial-scale=1" in UI_HTML
    assert 'aria-live="polite"' in UI_HTML
    assert 'aria-modal="true"' in UI_HTML


def test_active_ingestion_jobs_keep_refreshing_after_the_initial_upload_watch():
    assert "async function pollActiveJobs()" in UI_HTML
    assert "activeJobWatches.size" in UI_HTML
    assert "setInterval(pollActiveJobs,10000)" in UI_HTML
    assert "setTimeout(resolve,2500)" in UI_HTML
    assert "activeJobWatches.delete(id)" in UI_HTML
    assert "Scanned-page OCR can take about 75 seconds" in UI_HTML


def test_upload_limit_is_explained_before_sending_a_large_file():
    assert "uploadLimitBytes=0" in UI_HTML
    assert "caps.max_upload_bytes" in UI_HTML
    assert "Compress the PDF or split it into smaller parts" in UI_HTML
