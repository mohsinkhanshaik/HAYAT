"""Human handoff preserves the session; it does not solve or bypass a challenge."""
from types import SimpleNamespace

import pytest

from app.jobs.center import JobCenter
from app.jobs.browser_search import search_url
from tests.test_job_hunting_upgrade import hunting as hunting, jobs_window as jobs_window, pump


class Browser:
    def __init__(self):
        self.url, self.original, self.session = "", "", 1
        self.tab = 1
        self.running, self.blocked = True, True
        self.navigations, self.reads, self.scrolls = 0, 0, 0

    def form_fields(self, url, visible):
        assert visible
        self.navigations += 1
        self.original = url
        self.url = "https://www.google.com/sorry/index" if self.blocked else url
        return []

    def session_state(self):
        return {"running": self.running, "session": self.session, "tab": self.tab, "url": self.url}

    def structured_page(self):
        self.reads += 1
        return {"url": self.url, "title": "Jobs", "text": "Verify you are human" if self.blocked else "Public job leads"}

    def scroll_through(self, **kwargs):
        self.scrolls += 1
        return {"scrolls": 2}


@pytest.fixture
def pending(tmp_path):
    browser = Browser()
    center = JobCenter(tmp_path / "jobs", object(), browser=browser)
    result = center.browser_search("Employer boards", "analyst", "Japan", True)
    assert result["manual_interaction_required"]
    return center, browser


def test_human_completes_check_then_resume_reads_same_tab_without_navigating(pending):
    center, browser = pending
    browser.blocked, browser.url = False, browser.original  # user completes the provider's check
    result = center.browser_search(resume=True)
    assert result["resumed"] and result["status"] == "page_read"
    assert browser.navigations == 1 and browser.scrolls == 1
    assert center._job_browser_pending is None
    assert not result["application_submitted"] and result["imported_jobs"] == 0


def test_still_blocked_can_be_resumed_repeatedly_without_reload_or_solver(pending):
    center, browser = pending
    for _ in range(3):
        assert center.browser_search(resume=True)["manual_interaction_required"]
    assert browser.navigations == 1 and browser.scrolls == 0


@pytest.mark.parametrize("change,message", [
    ({"running": False}, "session closed"),
    ({"session": 2}, "session closed"),
    ({"tab": 2}, "tab changed"),
    ({"url": "https://mail.google.com/mail/"}, "original public job search"),
    ({"url": "https://www.google.com/gmail"}, "original public job search"),
    ({"url": "https://www.google.com/search?q=different"}, "query changed"),
    ({"url": "http://www.google.com/search?q=analyst"}, "original public job search"),
])
def test_resume_rejects_unrelated_pages_before_reading_contents(pending, change, message):
    center, browser = pending
    for key, value in change.items():
        setattr(browser, key, value)
    before = browser.reads
    with pytest.raises(ValueError, match=message):
        center.browser_search(resume=True)
    assert browser.reads == before and browser.navigations == 1


def test_resume_rejects_privacy_route_change_and_changed_browser(pending, monkeypatch):
    from app.privacy_masks import ROUTE
    center, browser = pending
    with monkeypatch.context() as patch:
        patch.setattr(ROUTE, "snapshot", lambda: (None, 999999))
        with pytest.raises(ValueError, match="privacy route changed"):
            center.browser_search(resume=True)
    center.browser = Browser()
    with pytest.raises(ValueError, match="No pending"):
        center.browser_search(resume=True)
    assert browser.navigations == 1


def test_resume_rejects_navigation_race_without_returning_page_contents(pending, monkeypatch):
    center, browser = pending
    monkeypatch.setattr(browser, "structured_page", lambda: {"url": "https://private.example/account", "text": "private"})
    with pytest.raises(ValueError, match="original public job search"):
        center.browser_search(resume=True)


@pytest.mark.parametrize("engine,host", [("Google", "www.google.com"), ("Bing", "www.bing.com"), ("DuckDuckGo", "duckduckgo.com")])
def test_explicit_free_engine_choice_retains_original_query(engine, host):
    from urllib.parse import parse_qs, urlsplit
    url = urlsplit(search_url("Employer boards", "analyst", "Japan", engine))
    assert url.hostname == host
    assert "analyst jobs Japan" in parse_qs(url.query)["q"][0]
    assert "site:jobs.ashbyhq.com" in parse_qs(url.query)["q"][0]


def test_continue_control_stays_available_on_matches_until_human_check_passes(jobs_window, monkeypatch):
    window = jobs_window
    calls = []
    def search(*args, **kwargs):
        calls.append(kwargs)
        blocked = not kwargs.get("resume")
        window.center._job_browser_pending = {"pending": True} if blocked else None
        return {"url": "https://www.google.com/search?q=analyst", "next_step": "Complete verification" if blocked else "Page read",
                "manual_interaction_required": blocked, "page": {"title": "Jobs", "text": "Public leads"}}
    monkeypatch.setattr(window.center, "browser_search", search)
    window.query.set("analyst")
    window.browser_scan.set(True)
    window.browser_engine.set("Bing")
    window.browse_platform()
    pump(window, lambda: str(window.continue_browser_button.cget("state")) == "normal")
    assert window.tabs.index("current") == 0 and calls[0]["engine"] == "Bing"
    window.continue_browser_button.invoke()
    pump(window, lambda: window.tabs.index("current") == 5)
    assert calls[-1] == {"resume": True}
    assert str(window.continue_browser_button.cget("state")) == "disabled"


def test_inspecting_closed_browser_never_launches_it():
    from app.tools.browser import BrowserTools
    stub = SimpleNamespace(browser=None, page=None, _session_serial=3)
    assert BrowserTools.session_state(stub) == {"running": False, "session": 3, "tab": None, "url": ""}
