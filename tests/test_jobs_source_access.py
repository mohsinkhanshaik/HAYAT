"""Official manual routes expose restrictions without granting publisher permissions."""
from datetime import UTC, datetime, timedelta

import pytest

from app.jobs.center import JobCenter
from app.jobs.tools import register_job_tools, report_lines
from tests.test_job_hunting_upgrade import hunting as hunting, jobs_window as jobs_window, pump


@pytest.fixture
def center(tmp_path):
    opened = []
    result = JobCenter(tmp_path / "jobs", object(), opener=opened.append)
    result.opened = opened
    return result


@pytest.mark.parametrize("source,url", [
    ("jobicy", "https://jobicy.com"),
    ("remoteok", "https://remoteok.com"),
    ("lever:example", "https://jobs.lever.co/example"),
    ("greenhouse:example", "https://job-boards.greenhouse.io/example"),
    ("ashby:example", "https://jobs.ashbyhq.com/example"),
])
def test_manual_routes_are_known_provider_urls_and_import_nothing(center, source, url):
    report = center.open_source(source)
    assert report["source_opened"] and report["official_url"] == url
    assert center.opened == [url] and center.store.counts()["jobs"] == 0
    assert center.store.applications() == []


@pytest.mark.parametrize("source", ["https://evil.example/", "lever:../private", "lever:foo@evil.example", "greenhouse", "unknown", ""])
def test_unknown_urls_and_malformed_board_names_are_rejected(center, source):
    with pytest.raises(ValueError, match="known public feed"):
        center.open_source(source)
    assert center.opened == []


def test_source_settings_do_not_claim_publisher_permission(center):
    report = center.source_access("remoteok")
    assert report["enabled"] and report["access_status"] == "not_checked"
    assert "checked during retrieval" in report["automatic_instruction"]
    center.set_preferences({"sources": []})
    report = center.source_access("remoteok")
    assert not report["enabled"] and "paused" in report["automatic_instruction"]


def test_manual_site_open_does_not_clear_robots_refusal_or_cooldown(center):
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat(timespec="seconds")
    center.store.record_source("remoteok", 0, 0, "robots.txt disallows retrieval")
    center.store.defer_source("remoteok", until, "robots_disallowed")
    report = center.open_source("remoteok")
    assert report["access_status"] == "robots_disallowed" and report["retry_at"] == until
    assert center.store.source_cooldowns()["remoteok"]["retry_at"] == until
    assert "does not" in report["manual_instruction"]
    text = "\n".join(report_lines(report))
    assert "robots_disallowed" in text and until in text and "robots.txt" in text


def test_manual_open_failure_is_not_claimed_as_success(center, monkeypatch):
    monkeypatch.setattr(center, "opener", lambda url: False)
    with pytest.raises(RuntimeError, match="could not be opened"):
        center.open_source("remoteok")


def test_tool_registry_exposes_access_inspection_and_separate_manual_open(center, tmp_path):
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    registry = ToolRegistry(PermissionManager({"default": "ALLOW"}, tmp_path))
    register_job_tools(registry, center)
    inspected = registry.execute("job_sources", {"source": "jobicy"})
    assert inspected.success and inspected.output["source_access_view"]
    assert center.opened == []
    opened = registry.execute("job_source_open", {"source": "jobicy"})
    assert opened.success and opened.output["source_opened"]
    assert center.opened == ["https://jobicy.com"]


def test_source_selection_explains_refusal_and_opens_official_site(jobs_window, monkeypatch):
    window = jobs_window
    opened = []
    monkeypatch.setattr(window.center, "opener", opened.append)
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat(timespec="seconds")
    window.center.store.record_source("remoteok", 0, 0, "robots.txt disallows retrieval")
    window.center.store.defer_source("remoteok", until, "robots_disallowed")
    window.tabs.select(5)
    pump(window, lambda: bool(window.source_tree.get_children()))
    window.source_tree.selection_set("remoteok")
    pump(window, lambda: "robots_disallowed" in window.source_access_text.get("1.0", "end"))
    assert str(window.open_source_button.cget("state")) == "normal"
    assert until in str(window.source_tree.item("remoteok", "values"))
    window.open_source_button.invoke()
    pump(window, lambda: "Opened official site" in window.source_access_text.get("1.0", "end"))
    assert opened == ["https://remoteok.com"]
    assert window.center.store.source_cooldowns()["remoteok"]["retry_at"] == until


def test_stale_source_access_response_cannot_replace_selected_source(jobs_window):
    window = jobs_window
    window.tabs.select(5)
    pump(window, lambda: bool(window.source_tree.get_children()))
    window.source_tree.selection_set("jobicy")
    pump(window, lambda: "Source access: jobicy" in window.source_access_text.get("1.0", "end"))
    before = window.source_access_text.get("1.0", "end")
    window.show_source_access(window.center.source_access("remoteok"))
    assert window.source_access_text.get("1.0", "end") == before


def test_source_access_does_not_replace_employer_discovery_results(jobs_window):
    window = jobs_window
    window.tabs.select(5)
    pump(window, lambda: bool(window.source_tree.get_children()))
    window.say(window.discovery_text, "Employer discovery results to preserve")
    window.source_tree.selection_set("remoteok")
    pump(window, lambda: "Source access: remoteok" in window.source_access_text.get("1.0", "end"))
    assert window.discovery_text.get("1.0", "end-1c") == "Employer discovery results to preserve"


def test_wrapped_source_actions_stay_inside_outer_scroll_range_after_resize(jobs_window):
    window = jobs_window
    window.geometry("680x480")
    window.tabs.select(5)
    pump(window, lambda: bool(window.source_tree.get_children()))
    window.source_tree.selection_set("remoteok")
    pump(window, lambda: "Source access:" in window.source_access_text.get("1.0", "end"))
    window.update()
    page = window.pages[5]
    assert page.body.winfo_height() >= page.body.winfo_reqheight()
    page.canvas.yview_moveto(1)
    window.update()
    assert window.open_source_button.winfo_rooty() + window.open_source_button.winfo_height() <= page.canvas.winfo_rooty() + page.canvas.winfo_height()
    assert page.canvas.yview()[1] >= .99
