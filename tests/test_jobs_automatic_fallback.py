"""Automatic provider fallback keeps site challenges intact and visible."""
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest

from app.jobs.center import JobCenter
from tests.test_job_hunting_upgrade import hunting as hunting, jobs_window as jobs_window, pump


class Browser:
    def __init__(self, blocked=(), errors=()):
        self.blocked, self.errors = set(blocked), set(errors)
        self.urls, self.tabs, self.selected = [], [""], 0
        self.scrolls = 0

    def form_fields(self, url, visible):
        assert visible
        self.urls.append(url)
        if urlsplit(url).hostname in self.errors:
            raise TimeoutError("Provider timed out")
        self.tabs[self.selected] = url

    def session_state(self):
        return {"running": True, "session": 1, "tab": self.selected + 1, "url": self.tabs[self.selected]}

    def structured_page(self):
        url = self.tabs[self.selected]
        blocked = urlsplit(url).hostname in self.blocked
        return {"url": url, "title": "Jobs", "text": "Verify you are human" if blocked else "Public job leads"}

    def scroll_through(self, **kwargs):
        self.scrolls += 1
        return {"scrolls": 2}

    def new_tab(self):
        self.tabs.append("")
        self.selected = len(self.tabs) - 1
        return {"index": self.selected}

    def list_tabs(self):
        return {"tabs": [{"index": n, "active": n == self.selected} for n in range(len(self.tabs))]}

    def switch_tab(self, index):
        self.selected = index


def center(tmp_path, **kwargs):
    return JobCenter(tmp_path / "jobs", object(), browser=Browser(**kwargs))


def test_blocked_provider_is_preserved_and_another_public_source_is_tried(tmp_path):
    jobs = center(tmp_path, blocked=["duckduckgo.com"])
    report = jobs.browser_search(query="analyst", location="Japan", engine="Auto", scan=True)
    assert report["engine"] == "Bing" and report["status"] == "page_read"
    assert [item["status"] for item in report["attempts"]] == ["manual_interaction_required", "page_read"]
    assert len(jobs.browser.urls) == 2 and len(jobs.browser.tabs) == 2
    assert urlsplit(jobs.browser.tabs[0]).hostname == "duckduckgo.com"  # no reload or challenge replacement
    assert all("analyst" in url and "Japan" in url for url in jobs.browser.urls)
    assert jobs.browser.scrolls == 1 and not report["application_submitted"]


def test_first_accessible_provider_stops_the_chain(tmp_path):
    jobs = center(tmp_path)
    report = jobs.browser_search(query="analyst", engine="Auto", scan=True)
    assert report["engine"] == "DuckDuckGo" and len(jobs.browser.urls) == 1


def test_all_blocked_routes_stop_after_three_and_preserve_manual_continuation(tmp_path):
    jobs = center(tmp_path, blocked=["duckduckgo.com", "www.bing.com", "www.google.com"])
    report = jobs.browser_search(query="analyst", engine="Auto", scan=True)
    assert report["manual_interaction_required"] and len(report["attempts"]) == 3
    assert len(jobs.browser.urls) == 3 and jobs.browser.scrolls == 0
    assert jobs.browser_search(resume=True)["manual_interaction_required"]
    assert len(jobs.browser.urls) == 3


def test_provider_errors_do_not_erase_the_previous_challenge_tab(tmp_path):
    jobs = center(tmp_path, blocked=["duckduckgo.com"], errors=["www.bing.com", "www.google.com"])
    report = jobs.browser_search(query="analyst", engine="Auto", scan=True)
    assert report["engine"] == "DuckDuckGo" and jobs.browser.selected == 0
    assert jobs.browser_search(resume=True)["manual_interaction_required"]


def test_repeat_auto_search_defers_blocked_engines_without_network_calls(tmp_path):
    jobs = center(tmp_path, blocked=["duckduckgo.com", "www.bing.com", "www.google.com"])
    jobs.browser_search(query="analyst", engine="Auto", scan=True)
    report = jobs.browser_search(query="analyst", engine="Auto", scan=True)
    assert report["status"] == "no_provider_available"
    assert len(jobs.browser.urls) == 3 and all(item["status"] == "cooling_down" for item in report["attempts"])


def test_cancel_before_start_contacts_no_provider(tmp_path):
    jobs = center(tmp_path)
    report = jobs.browser_search(query="analyst", engine="Auto", scan=True, check=lambda: False)
    assert report["status"] == "stopped" and jobs.browser.urls == []


def test_expired_automatic_cooldown_permits_a_new_attempt(tmp_path):
    jobs = center(tmp_path)
    jobs._job_browser_cooldowns = {"DuckDuckGo": datetime.now(UTC) - timedelta(seconds=1)}
    assert jobs.browser_search(query="analyst", engine="Auto", scan=True)["engine"] == "DuckDuckGo"


def test_route_change_cancels_fallback_instead_of_contacting_another_provider(tmp_path, monkeypatch):
    from app.privacy_masks import ROUTE
    jobs = center(tmp_path, blocked=["duckduckgo.com"])
    version = [0]
    monkeypatch.setattr(ROUTE, "snapshot", lambda: (None, version[0]))
    original = jobs.browser.structured_page
    def changed():
        page = original()
        version[0] = 1
        return page
    monkeypatch.setattr(jobs.browser, "structured_page", changed)
    with pytest.raises(ValueError, match="privacy route changed"):
        jobs.browser_search(query="analyst", engine="Auto", scan=True)
    assert len(jobs.browser.urls) == 1


def test_active_privacy_proxy_cannot_silently_fall_back_to_direct_browser(tmp_path, monkeypatch):
    from app.privacy_masks import ROUTE
    jobs = center(tmp_path)
    monkeypatch.setattr(ROUTE, "snapshot", lambda: ("socks5h://127.0.0.1:9050", 3))
    with pytest.raises(PermissionError, match="active privacy proxy"):
        jobs.browser_search(query="analyst", engine="Auto", scan=True)
    assert jobs.browser.urls == []


def test_new_auto_start_cannot_clear_an_existing_search_stop_signal(jobs_window):
    window = jobs_window
    window._busy_actions.add("search")
    window._search_stop.set()
    window.browser_engine.set("Auto")
    window.browse_platform()
    assert window._search_stop.is_set() and "browser_search" not in window._busy_actions
    window._busy_actions.discard("search")


@pytest.mark.parametrize("kwargs", [{"scan": False}, {"scan": True, "platform": "LinkedIn"}, {"scan": True, "query": ""}])
def test_invalid_auto_requests_are_rejected_before_browser_navigation(tmp_path, kwargs):
    jobs = center(tmp_path)
    with pytest.raises(ValueError):
        jobs.browser_search(**{"query": "analyst", "engine": "Auto", **kwargs})
    assert jobs.browser.urls == []


def test_auto_gui_selects_visible_reading_and_offers_stop(jobs_window, monkeypatch):
    window = jobs_window
    import threading
    started = threading.Event()
    def search(*args, **kwargs):
        assert args[3] is True and kwargs["engine"] == "Auto"
        started.set()
        assert window._search_stop.wait(5) and not kwargs["check"]()
        return {"url": "https://duckduckgo.com/", "next_step": "Automatic search stopped", "status": "stopped", "attempts": []}
    monkeypatch.setattr(window.center, "browser_search", search)
    window.query.set("analyst")
    window.browser_engine.set("Auto")
    window.browse_platform()
    pump(window, started.is_set)
    assert window.browser_scan.get() and str(window.stop_search_button.cget("state")) == "normal"
    window.stop_search_button.invoke()
    pump(window, lambda: "Automatic search stopped" in window.search_note.cget("text"))
    assert str(window.stop_search_button.cget("state")) == "disabled"
