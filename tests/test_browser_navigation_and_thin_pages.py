"""The browser can navigate, and research re-reads pages it only half read.

Two gaps that both look like "it cannot scrape". The browser had no way back,
no way to reload, and no way to wait for content a click had not produced yet,
so exploring past the first link meant re-searching from scratch. And research
treated a JavaScript-only page as read: it fetched a 200, extracted the shell,
and synthesised an answer from the lesser sources that happened to be
server-rendered, reporting no error at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.research.manager import ResearchManager
from app.research.models import ResearchBudget, ResearchMetrics, SourceDocument
from app.safety.permissions import PermissionManager
from app.tools.browser import BrowserTools, register_browser_tools
from app.tools.registry import ToolRegistry


# --------------------------------------------------------------------------
# Navigation tools
# --------------------------------------------------------------------------

def _registry(tmp_path: Path) -> ToolRegistry:
    return ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))


def test_navigation_and_waiting_tools_are_registered_with_schemas(tmp_path):
    registry = _registry(tmp_path)
    register_browser_tools(registry)
    for name in ("browser_back", "browser_forward", "browser_reload",
                 "browser_wait_for", "browser_hover", "browser_select_option",
                 "browser_press_key"):
        assert name in registry.names(), name
        spec = registry.get(name)
        assert spec.description.strip(), name
        assert spec.input_schema["type"] == "object", name
        # Navigating and clicking are never silently free actions.
        assert spec.permission in {"browser_read", "browser_click"}, name


def test_every_navigation_tool_is_thread_confined_like_the_rest():
    """A method that touches Playwright and is not confined crashes off-thread.

    BrowserTools derives the confined set from the class rather than a list, so
    this holds automatically -- which is the point, and worth a test because a
    hand-maintained list is exactly what it replaced.
    """
    confinable = set(BrowserTools._confinable())
    for name in ("go_back", "go_forward", "reload", "wait_for", "hover",
                 "select_option", "press_key"):
        assert name in confinable, name


def test_select_option_refuses_a_choice_that_names_nothing(tmp_path):
    registry = _registry(tmp_path)
    browser = register_browser_tools(registry)
    with pytest.raises(ValueError):
        browser.select_option("select#country")


# --------------------------------------------------------------------------
# Thin-document rendering
# --------------------------------------------------------------------------

class _RecordingBrowser:
    """Stands in for Playwright, returning rendered text for requested URLs."""

    def __init__(self, rendered: dict[str, str]) -> None:
        self.rendered = rendered
        self.navigated: list[str] = []
        self.scrolled = 0

    def navigate(self, url, wait_until="domcontentloaded"):
        self.navigated.append(url)
        return {"url": url, "title": "rendered", "status": 200}

    def scroll_through(self, max_scrolls=20, pause_ms=250, return_to_top=True):
        self.scrolled += 1
        return {"scrolls": 2, "reached_end": True}

    def structured_page(self):
        url = self.navigated[-1]
        return {
            "url": url, "title": "rendered", "text": self.rendered.get(url, ""),
            "headings": ["Rendered heading"], "links": [], "tables": [],
            "metadata": {"description": "rendered page"}, "canonical_url": url,
            "retrieved_at": "2026-09-27T00:00:00+00:00",
        }


def _manager(tmp_path: Path, browser) -> ResearchManager:
    manager = ResearchManager({
        "enabled": True, "database": str(tmp_path / "knowledge.db"),
        "user_agent": "test/1.0", "respect_robots_txt": False,
        "model_synthesis": False,
    })
    manager.attach_browser(browser)
    return manager


def _document(url: str, text: str, *, quality: float = 0.8,
              content_type: str = "text/html; charset=utf-8") -> SourceDocument:
    return SourceDocument(
        url, url, "Title", "example.com", text, "2026-09-27T00:00:00+00:00",
        200, content_type, "hash", len(text.encode()), quality=quality,
    )


def _budget() -> ResearchBudget:
    return ResearchBudget(searches=5, pages=15, max_seconds=90,
                          max_bytes=20 * 1024 * 1024, max_workers=3)


def test_a_javascript_only_page_is_re_read_in_the_real_browser(tmp_path):
    """The shell was accepted as the article. Now the article replaces it."""
    shell = "Loading... You need to enable JavaScript to run this app."
    article = "The real article body. " * 200
    browser = _RecordingBrowser({"https://example.com/app": article})
    manager = _manager(tmp_path, browser)
    metrics, errors = ResearchMetrics(), []
    try:
        result = manager._render_thin_documents(
            [_document("https://example.com/app", shell)],
            "what does the app say", _budget(), __import__("time").perf_counter(),
            metrics, errors,
        )
        assert browser.navigated == ["https://example.com/app"]
        assert len(result) == 1
        assert result[0].text.startswith("The real article body.")
        assert "rendered=playwright" in result[0].content_type
        assert "rendered_after_thin_fetch" in result[0].quality_reasons
        assert result[0].metadata["replaced_thin_fetch"]["fetched_characters"] == len(shell)
        assert not errors
    finally:
        manager.close()


def test_a_page_that_is_simply_short_is_left_alone(tmp_path):
    """Rendering that reveals nothing must not discard what the fetch got."""
    short = "A short but complete note about the subject."
    browser = _RecordingBrowser({"https://example.com/note": short})
    manager = _manager(tmp_path, browser)
    try:
        result = manager._render_thin_documents(
            [_document("https://example.com/note", short)],
            "the note", _budget(), __import__("time").perf_counter(),
            ResearchMetrics(), [],
        )
        assert result[0].text == short
        assert "rendered=playwright" not in result[0].content_type
    finally:
        manager.close()


def test_pages_that_fetched_properly_are_never_re_rendered(tmp_path):
    browser = _RecordingBrowser({})
    manager = _manager(tmp_path, browser)
    try:
        full = "Plenty of real body text here. " * 100
        result = manager._render_thin_documents(
            [_document("https://example.com/article", full)],
            "the article", _budget(), __import__("time").perf_counter(),
            ResearchMetrics(), [],
        )
        assert browser.navigated == []
        assert result[0].text == full
    finally:
        manager.close()


def test_a_pdf_with_little_text_is_not_treated_as_a_javascript_shell(tmp_path):
    """Only HTML hides behind JavaScript. A short PDF is short."""
    browser = _RecordingBrowser({})
    manager = _manager(tmp_path, browser)
    try:
        manager._render_thin_documents(
            [_document("https://example.com/a.pdf", "Two lines.",
                       content_type="application/pdf")],
            "the pdf", _budget(), __import__("time").perf_counter(),
            ResearchMetrics(), [],
        )
        assert browser.navigated == []
    finally:
        manager.close()


def test_the_number_of_renders_is_bounded(tmp_path):
    article = "Rendered body text. " * 200
    urls = {f"https://example.com/p{index}": article for index in range(8)}
    browser = _RecordingBrowser(urls)
    manager = _manager(tmp_path, browser)
    try:
        manager._render_thin_documents(
            [_document(url, "shell", quality=0.5) for url in urls],
            "anything", _budget(), __import__("time").perf_counter(),
            ResearchMetrics(), [],
        )
        assert len(browser.navigated) == ResearchManager.MAX_THIN_RENDERS
    finally:
        manager.close()


def test_the_most_relevant_thin_page_is_rendered_first(tmp_path):
    """One render's worth of budget should be spent on the best page."""
    article = "Rendered body text. " * 200
    browser = _RecordingBrowser({
        "https://example.com/low": article, "https://example.com/high": article,
    })
    manager = _manager(tmp_path, browser)
    original = ResearchManager.MAX_THIN_RENDERS
    ResearchManager.MAX_THIN_RENDERS = 1
    try:
        manager._render_thin_documents(
            [_document("https://example.com/low", "shell", quality=0.2),
             _document("https://example.com/high", "shell", quality=0.9)],
            "anything", _budget(), __import__("time").perf_counter(),
            ResearchMetrics(), [],
        )
        assert browser.navigated == ["https://example.com/high"]
    finally:
        ResearchManager.MAX_THIN_RENDERS = original
        manager.close()


def test_a_render_that_fails_keeps_the_document_and_records_the_failure(tmp_path):
    class _BrokenBrowser(_RecordingBrowser):
        def navigate(self, url, wait_until="domcontentloaded"):
            raise RuntimeError("renderer crashed")

    manager = _manager(tmp_path, _BrokenBrowser({}))
    metrics, errors = ResearchMetrics(), []
    try:
        result = manager._render_thin_documents(
            [_document("https://example.com/app", "shell")],
            "anything", _budget(), __import__("time").perf_counter(), metrics, errors,
        )
        assert result[0].text == "shell"
        assert metrics.failures == 1
        assert any("renderer crashed" in item for item in errors)
    finally:
        manager.close()


def test_without_a_browser_research_changes_nothing(tmp_path):
    manager = _manager(tmp_path, None)
    manager.browser = None
    try:
        documents = [_document("https://example.com/app", "shell")]
        assert manager._render_thin_documents(
            documents, "anything", _budget(),
            __import__("time").perf_counter(), ResearchMetrics(), [],
        ) == documents
    finally:
        manager.close()
