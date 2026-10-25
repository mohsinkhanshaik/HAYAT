"""Opt-in checks against public employer boards; no application is submitted."""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from app.jobs.sources import ashby, discover, lever
from app.research.fetcher import PublicFetcher


@pytest.mark.skipif(os.environ.get("HAYAT_LIVE_JOBS") != "1", reason="opt-in public employer board retrieval")
def test_actual_employer_boards_and_url_discovery(tmp_path):
    from app.jobs.understand import understand
    fetcher = PublicFetcher("HAYAT public jobs verification", timeout=20, delay=.35)
    report = {"application_submitted": False, "boards": []}
    try:
        for ats, slug, reader in (("ashby", "ashby", ashby), ("lever", "palantir", lever)):
            rows = reader(fetcher, slug)
            assert rows and all(row["title"] and row["url"].startswith("https://") for row in rows)
            pay = [understand(row)["salary"] for row in rows if row.get("salary_min") or row.get("salary_max")]
            assert all(item["currency"] and item["min"] <= item["max"] for item in pay)
            report["boards"].append({"ats": ats, "slug": slug, "public_roles": len(rows),
                                     "structured_pay_roles": len(pay), "sample_pay": pay[:2]})
        found = discover(fetcher, "https://jobs.ashbyhq.com/ashby")
        assert found and found[0]["follow"] == "ashby:ashby"
        report["url_discovery"] = found
        path = Path(".test-runs/live-job-source-verification.json")
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    finally:
        fetcher.close()


@pytest.mark.skipif(os.environ.get("HAYAT_LIVE_JOB_BROWSER") != "1", reason="opt-in visible public job browser check")
@pytest.mark.parametrize("engine", ["Google", "Bing", "DuckDuckGo"])
def test_actual_visible_browser_job_search(tmp_path, engine):
    from app.jobs.center import JobCenter
    from app.safety.permissions import PermissionManager
    from app.tools.browser import BrowserTools
    from app.tools.browser_host import PlaywrightHost
    host = PlaywrightHost("jobs-live-browser-check")
    browser = BrowserTools(PermissionManager({"default": "ALLOW"}, tmp_path), host=host)
    center = JobCenter(tmp_path / "jobs", object(), browser=browser)
    try:
        result = center.browser_search("Employer boards", "analyst", "Japan", True, engine=engine)
        page = result["page"]
        assert page["title"] and page["text"] and page["retrieved_at"]
        if result["manual_interaction_required"]:
            assert result["status"] == "manual_interaction_required" and "scroll" not in result
            resumed = center.browser_search(resume=True)
            assert resumed["resumed"] and resumed["manual_interaction_required"]
        else:
            assert result["scroll"]["scrolls"] > 0
        assert not result["application_submitted"] and result["imported_jobs"] == 0
        assert center.store.counts()["jobs"] == 0
        final = urlsplit(page["url"])
        sample = re.sub(r"(?im)^\s*IP address:.*$", "IP address: [redacted]", page["text"][:5000])
        report = {"requested_url": result["url"], "engine": engine, "status": result["status"],
                  "final_url": urlunsplit((final.scheme, final.netloc, final.path, "", "")),
                  "retrieved_at": page["retrieved_at"], "text_sample": sample,
                  "links_seen": len(page.get("links", [])), "scrolls": (result.get("scroll") or {}).get("scrolls", 0),
                  "application_submitted": False, "owner_profile_accessed": False, "paid_api_used": False}
        suffix = "" if engine == "Google" else "-" + engine.lower()
        Path(f".test-runs/live-job-browser-verification{suffix}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    finally:
        browser.close()
        host.shutdown()


@pytest.mark.skipif(os.environ.get("HAYAT_LIVE_JOB_AUTO") != "1", reason="opt-in public browser fallback verification")
def test_actual_automatic_public_provider_fallback(tmp_path, monkeypatch):
    from app.jobs.center import JobCenter
    from app.safety.permissions import PermissionManager
    from app.tools.browser import BrowserTools
    from app.tools.browser_host import PlaywrightHost
    from app.jobs import browser_fallback
    # Exercise the observed blocked route first. Only ranking is controlled;
    # the browser pages and access outcomes are genuine network responses.
    monkeypatch.setattr(browser_fallback, "PROVIDERS", ("Google", "DuckDuckGo", "Bing"))
    host = PlaywrightHost("jobs-live-automatic-fallback")
    browser = BrowserTools(PermissionManager({"default": "ALLOW"}, tmp_path), host=host)
    center = JobCenter(tmp_path / "jobs", object(), browser=browser)
    try:
        result = center.browser_search(query="analyst", location="Japan", engine="Auto", scan=True)
        assert result["automatic_fallback"] and 1 <= len(result["attempts"]) <= 3
        assert result["attempts"][0]["engine"] == "Google"
        if result["status"] == "page_read":
            assert result["page"]["title"] and result["page"]["text"]
        assert result["imported_jobs"] == 0 and not result["application_submitted"]
        assert center.store.counts()["jobs"] == 0
        report = {key: result.get(key) for key in ("status", "engine", "attempts", "automatic_fallback")}
        report.update(links_seen=len((result.get("page") or {}).get("links", [])),
                      provider_order_controlled=True, page_responses_simulated=False,
                      owner_profile_accessed=False, application_submitted=False, paid_api_used=False)
        Path(".test-runs/live-job-auto-fallback.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    finally:
        browser.close()
        host.shutdown()
