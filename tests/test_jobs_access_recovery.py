"""Verification classification and persistent, provider-wide cooldowns."""
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace

import httpx
import pytest

from app.jobs.access import failure_details, page_access
from app.jobs.center import JobCenter
from app.jobs.sources import collect
from tests.test_job_hunting_upgrade import hunting as hunting, jobs_window as jobs_window, pump


@pytest.mark.parametrize("page,expected", [
    ({"url": "https://example.org/jobs", "title": "Senior Security Verification Engineer", "text": "security verification duties. " * 100}, "accessible"),
    ({"url": "https://example.org/jobs", "title": "Security Engineer", "text": "Implement the verify you are human user experience. " * 100}, "accessible"),
    ({"url": "https://example.org/jobs", "text": "Find public jobs", "access_signals": {"visible_challenge": True}}, "human_verification"),
    ({"url": "https://example.org/login", "title": "Sign in"}, "sign_in_required"),
    ({"url": "https://example.org/cdn-cgi/challenge-platform/", "text": "Waiting"}, "human_verification"),
    ({"url": "https://example.org/jobs", "title": "Just a moment..."}, "human_verification"),
    ({"url": "https://example.org/jobs", "title": "Too many requests"}, "rate_limited"),
    ({"url": "https://example.org/jobs", "title": "Access denied"}, "access_denied"),
    ({"url": "https://example.org/jobs", "http_status": 429}, "rate_limited"),
])
def test_access_states_do_not_confuse_job_content_and_site_controls(page, expected):
    assert page_access(page) == expected


def limited(status=429, retry=None):
    request = httpx.Request("GET", "https://jobicy.com/api/jobs")
    response = httpx.Response(status, request=request, headers={"Retry-After": retry} if retry is not None else {})
    return httpx.HTTPStatusError("Provider unavailable", request=request, response=response)


@pytest.mark.parametrize("retry,expected", [("120", 120), ("0", 1), ("invalid", 60), ("inf", 60)])
def test_retry_after_seconds_and_invalid_values(retry, expected):
    now = datetime(2026, 10, 25, tzinfo=UTC)
    report = failure_details(limited(retry=retry), now)
    assert report["retry_after_seconds"] == expected
    assert datetime.fromisoformat(report["retry_at"]) == now + timedelta(seconds=expected)


def test_retry_after_http_date_and_robots_rules():
    now = datetime(2026, 10, 25, tzinfo=UTC)
    report = failure_details(limited(retry=format_datetime(now + timedelta(minutes=4), usegmt=True)), now)
    assert report["retry_after_seconds"] == 240
    assert failure_details(PermissionError("robots.txt disallows retrieval"), now)["access_status"] == "robots_disallowed"
    assert failure_details(TimeoutError("timed out"), now) == {"access_status": "unavailable"}


@pytest.mark.parametrize("depth", ["normal", "deep"])
def test_provider_cooldown_survives_restart_and_force_does_not_override_it(tmp_path, depth):
    calls = []
    def fetch(*args):
        calls.append(args[0])
        raise limited(retry="120")
    folder = tmp_path / "jobs"
    center = JobCenter(folder, SimpleNamespace(fetch=fetch))
    center.set_preferences({"sources": ["jobicy"], "search_terms": ["analyst"]})
    first = center.search(query="analyst", depth=depth, force=True)
    assert first["read"][0]["access_status"] == "rate_limited"
    assert first["read"][0]["retry_after_seconds"] == 120
    reopened = JobCenter(folder, SimpleNamespace(fetch=fetch))
    second = reopened.search(query="analyst", depth=depth, force=True)
    assert len(calls) == 1 and second["source_summary"]["attempted"] == 0
    assert second["source_summary"]["cached"] == 0 and second["skipped_recent"] == []
    assert second["deferred_sources"][0]["source"] == "jobicy"
    assert "cooling down" in second["empty_reason"]


@pytest.mark.parametrize("depth", ["normal", "deep"])
def test_same_ats_provider_is_not_hammered_via_another_board(depth):
    calls = []
    def fetch(*args):
        calls.append(args[0])
        raise limited(retry="90")
    found = collect(SimpleNamespace(fetch=fetch), ["lever:first", "lever:second"], ["analyst"], depth)
    assert len(calls) == 1 and found["deferred_sources"][0]["source"] == "lever:second"
    assert len(found["sources"]) == 1
    if depth == "deep":
        assert found["coverage"]["unvisited_sources"] == ["lever:second"]


def test_expired_cooldown_allows_retry_of_failed_source_before_hourly_cache(tmp_path, monkeypatch):
    center = JobCenter(tmp_path / "jobs", object())
    center.set_preferences({"sources": ["jobicy"], "search_terms": ["analyst"]})
    center.store.record_source("jobicy", 0, 0, "previous provider error")
    center.store.defer_source("jobicy", (datetime.now(UTC) - timedelta(seconds=2)).isoformat(), "rate_limited")
    calls = []
    monkeypatch.setattr("app.jobs.center.collect", lambda *args: calls.append(args) or {"jobs": [], "sources": []})
    assert center.search()["deferred_sources"] == []
    assert len(calls) == 1


def test_publisher_cooldown_cannot_be_shortened_by_a_later_failure(tmp_path):
    center = JobCenter(tmp_path / "jobs", object())
    later = (datetime.now(UTC) + timedelta(minutes=10)).isoformat(timespec="seconds")
    center.store.defer_source("lever:first", later, "rate_limited")
    center.store.defer_source("lever:second", (datetime.now(UTC) + timedelta(minutes=1)).isoformat(timespec="seconds"), "rate_limited")
    assert center.store.source_cooldowns()["lever"]["retry_at"] == later


def test_free_api_button_keeps_query_filters_and_pending_browser(jobs_window, monkeypatch):
    window = jobs_window
    calls = []
    pending = {"pending": True}
    window.center._job_browser_pending = pending
    window.query.set("designer")
    window.location.set("Japan")
    def search(**kwargs):
        calls.append(kwargs)
        return {"new": 0, "read": [], "matching_total": 0, "deferred_sources": [
            {"source": "jobicy", "reason": "rate_limited", "retry_at": "2026-10-25T13:00:00+00:00"}]}
    monkeypatch.setattr(window.center, "search", search)
    def children(root):
        for child in root.winfo_children():
            yield child
            yield from children(child)
    button = next(child for child in children(window) if "text" in child.keys() and child.cget("text") == "Search free APIs")
    button.invoke()
    pump(window, lambda: "Source cooldowns" in window.search_note.cget("text"))
    assert calls[0]["query"] == "designer" and calls[0]["location"] == "Japan"
    assert window.center._job_browser_pending is pending
    assert "choose sources" not in window.search_note.cget("text")
