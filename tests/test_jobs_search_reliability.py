"""Saved search filters and duplicate evidence must reflect real requisitions and empty results."""
from types import SimpleNamespace

import pytest

from app.jobs.center import JobCenter
from app.jobs.readiness import application_routes, plan_lines
from app.jobs.sources import matches_location, same_posting
from app.jobs.understand import understand
from tests.test_jobs import posting


@pytest.fixture
def jobs(tmp_path):
    center = JobCenter(tmp_path / "jobs", object())
    center.set_preferences({"sources": ["remoteok"], "search_terms": ["analyst"]})
    return center


def result(rows, *, error=""):
    return {"jobs": rows, "sources": [{"source": "remoteok", "read": len(rows), "kept": len(rows), "error": error}]}


def role(key, *, title="Data Analyst", location="Remote", remote=True, **extra):
    return posting(key=key, title=title, location=location, remote=remote,
                   url=f"https://employer.example/jobs/{key}", apply_url=f"https://employer.example/apply/{key}",
                   description="Requirements: SQL and Python.", **extra)


def test_location_views_preserve_collected_jobs_and_apply_to_hourly_cache(jobs, monkeypatch):
    rows = [role("remoteok:a", location="Austin, US", remote=False),
            role("remoteok:b", location="New York, US", remote=True),
            role("remoteok:c", location="Berlin, Germany", remote=False)]
    calls = []
    monkeypatch.setattr("app.jobs.center.collect", lambda *_: calls.append(True) or result(rows))
    first = jobs.search(location="Austin")
    assert first["new"] == 3 and first["matching_total"] == 1
    assert first["matches"][0]["location"] == "Austin, US"
    second = jobs.search(location="New York", remote=True)
    assert len(calls) == 1 and second["source_summary"]["cached"] == 1
    assert second["matching_total"] == 1 and second["matches"][0]["location"] == "New York, US"
    assert jobs.matches(location="Berlin")["matches"][0]["location"] == "Berlin, Germany"
    assert jobs.matches(location="Berlin", remote_only=True)["matches"] == []


def test_explicit_nonremote_filter_excludes_unknown_and_remote(jobs, monkeypatch):
    rows = [role("remoteok:site", remote=False, location="Boston"),
            role("remoteok:hybrid", remote=None, location="Boston hybrid"),
            role("remoteok:unknown", remote=None, location="Boston"),
            role("remoteok:remote", remote=True, location="Boston")]
    monkeypatch.setattr("app.jobs.center.collect", lambda *_: result(rows))
    found = jobs.search(remote=False)
    assert {row["mode"] for row in found["matches"]} == {"on-site", "hybrid"}
    assert found["matching_total"] == 2


def test_query_never_falls_back_to_unrelated_saved_jobs(jobs, monkeypatch):
    jobs.store.upsert([role("remoteok:old")])
    monkeypatch.setattr("app.jobs.center.collect", lambda *_: result([], error="TimeoutError: provider unavailable"))
    found = jobs.search(query="pediatric nurse", location="Toronto")
    assert found["matches"] == [] and found["matching_total"] == 0
    assert found["source_summary"] == {"attempted": 1, "succeeded": 0, "failed": 1, "cached": 0}
    assert "sources failed" in found["empty_reason"]
    assert found["criteria"]["location"] == "Toronto" and found["criteria"]["terms"] == ["pediatric nurse"]
    from app.jobs.tools import report_lines
    rendered = '\n'.join(report_lines(found))
    assert 'Source unavailable: remoteok' in rendered and 'Toronto' in rendered
    assert 'Showing 0 of 0' in rendered and found['empty_reason'] in rendered


def test_location_filter_is_available_through_registered_tool(jobs, tmp_path):
    from app.jobs.tools import register_job_tools
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    jobs.store.upsert([role('remoteok:ny', location='New York'), role('remoteok:berlin', location='Berlin')])
    jobs.rescore()
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    register_job_tools(registry, jobs)
    result = registry.execute('job_matches', {'location': 'Berlin'})
    assert result.success, result.error
    assert [row['location'] for row in result.output['matches']] == ['Berlin']


def test_query_scans_beyond_display_limit_and_uses_title_word_order_and_tags(jobs, monkeypatch):
    rows = [role(f"remoteok:{index}", title=f"Finance Manager {index}") for index in range(220)]
    rows += [role("remoteok:rare", title="Analyst, Regulatory Affairs"),
             role("remoteok:tag", title="Clinical Specialist", tags=["pharmacovigilance"])]
    jobs.store.upsert(rows)
    for number in jobs.store.numbers():
        row = jobs.store.job(number)
        jobs.store.set_match(number, understand(row), {"score": 10 if row["key"] in {"remoteok:rare", "remoteok:tag"} else 90,
                                                       "verdict": "stretch"})
    monkeypatch.setattr(jobs, "rescore", lambda: 0)
    monkeypatch.setattr("app.jobs.center.collect", lambda *_: result([]))
    found = jobs.search(query="regulatory analyst, pharmacovigilance")
    assert {row["title"] for row in found["matches"]} == {"Analyst, Regulatory Affairs", "Clinical Specialist"}
    assert found["matching_total"] == 2


@pytest.mark.parametrize("advertised,description,query,expected", [
    ("Austin", "Our global business works with customers in the US.", "US", False),
    ("Austin, USA", "", "United States", True),
    ("Remote", "Applicants must be based in the US.", "USA", True),
    ("Remote", "We have offices in London; you work anywhere.", "London", False),
    ("New York, US", "", "New York", True),
    ("Austin", "", "US", False),
])
def test_location_matching_uses_bounded_advertised_location_or_explicit_region(advertised, description, query, expected):
    row = posting(location=advertised, description=description)
    row["reading"] = understand(row)
    assert matches_location(row, query) is expected


def test_same_title_native_requisitions_and_distinct_locations_are_preserved(jobs, monkeypatch):
    rows = [role("remoteok:a", location="New York", remote=False), role("remoteok:b", location="New York", remote=False),
            {**role("jobicy:c", location="Boston", remote=False), "source": "jobicy", "source_name": "Jobicy"}]
    monkeypatch.setattr("app.jobs.center.collect", lambda *_: result(rows))
    assert jobs.search()["new"] == 3
    # A repeat must update native records rather than treating them as duplicates of their neighbours.
    rows[0]["description"] = "Updated requirements: SQL, Python and Excel."
    assert jobs.search(force=True)["new"] == 0
    assert jobs.store.job(1)["description"].startswith("Updated requirements")


def test_cross_board_duplicate_requires_strong_shared_evidence():
    left = role("remoteok:1")
    right = {**role("jobicy:1"), "source": "jobicy"}
    assert not same_posting(left, right)  # A short template plus title/company proves too little.
    right["apply_url"] = left["apply_url"] + "?utm_source=other"
    assert same_posting(left, right)
    right["apply_url"] = "https://employer.example/careers?job_id=2"
    left["apply_url"] = "https://employer.example/careers?job_id=1"
    assert not same_posting(left, right)
    right["apply_url"], left["apply_url"] = "https://employer.example/careers#job-2", "https://employer.example/careers#job-1"
    assert not same_posting(left, right)


def test_shared_company_homepage_does_not_prove_a_duplicate():
    left = role("remoteok:1")
    right = {**role("jobicy:1"), "source": "jobicy"}
    left.update(url="https://employer.example/", apply_url="https://employer.example/careers")
    right.update(url=left["url"], apply_url=left["apply_url"])
    assert not same_posting(left, right)


def test_identical_substantive_remote_posting_is_deduplicated_across_boards(jobs, monkeypatch):
    left = posting(key="remoteok:a", location="Remote")
    right = {**posting(key="jobicy:a", location="Anywhere"), "source": "jobicy", "url": "https://jobicy.com/jobs/a",
             "apply_url": "https://jobicy.com/apply/a"}
    assert same_posting(left, right)
    monkeypatch.setattr("app.jobs.center.collect", lambda *_: result([left, right]))
    assert jobs.search()["new"] == 1
    assert jobs.store.duplicate(right)


def test_malformed_source_timestamp_is_refetched_instead_of_breaking_search(jobs, monkeypatch):
    monkeypatch.setattr(jobs.store, "source_state", lambda: {"remoteok": {"fetched_at": "bad timestamp"}})
    monkeypatch.setattr("app.jobs.center.collect", lambda *_: result([]))
    assert jobs.search()["source_summary"]["attempted"] == 1


def test_application_routes_show_real_setup_requirements_without_sending(jobs):
    number = jobs.store.upsert([posting()])[0]
    jobs.rescore()
    routes = jobs.detail(number)["application_routes"]
    assert {route["channel"] for route in routes} == {"packet", "web", "browser prefill", "email"}
    assert next(route for route in routes if route["channel"] == "browser prefill")["available"] is False
    assert next(route for route in routes if route["channel"] == "email")["available"] is False
    jobs.browser, jobs.mailer, jobs.gate = SimpleNamespace(), SimpleNamespace(), SimpleNamespace()
    assert all(route["available"] for route in jobs.detail(number)["application_routes"])
    assert jobs.store.application(number) is None
    plan = jobs.insight(number)["application_plan"]
    rendered = "\n".join(plan_lines(plan))
    assert "Application routes:" in rendered and "check session setup" in rendered
    assert "engineering blog" not in rendered
    jobs.mailer = SimpleNamespace(setup_problem=lambda: "Email account has not been configured.")
    unavailable = next(route for route in jobs.detail(number)["application_routes"] if route["channel"] == "email")
    assert unavailable["available"] is False and "not been configured" in unavailable["setup_problem"]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://name:password@example.com/jobs/1", ""])
def test_invalid_or_credential_bearing_posting_urls_are_not_application_routes(url):
    routes = application_routes({"url": url, "apply_url": url, "reading": {}})
    assert [route["channel"] for route in routes] == ["packet"]
