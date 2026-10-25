"""Depth must retrieve real later pages, retain partial evidence and expose limits."""
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.jobs.center import JobCenter
from app.jobs.search_depth import DEPTHS
from app.jobs.sources import collect, matches_location
from app.jobs.tools import report_lines
from tests.test_jobs import posting


class Pages:
    def __init__(self, respond):
        self.respond, self.calls = respond, []

    def fetch(self, url, *args):
        self.calls.append(url)
        return SimpleNamespace(content=json.dumps(self.respond(url)).encode())


def himalaya(number, title="Data Analyst"):
    return {"guid": f"https://himalayas.app/companies/example/jobs/{number}", "title": title,
            "companyName": f"Employer {number}", "applicationLink": f"https://himalayas.app/jobs/{number}",
            "locationRestrictions": ["Japan"]}


def test_himalayas_deep_search_fetches_later_pages_and_keeps_apply_link():
    fetcher = Pages(lambda url: {"jobs": [himalaya(parse_qs(urlsplit(url).query)["page"][0])]})
    found = collect(fetcher, ["himalayas"], ["analyst"], "deep")
    assert len(found["jobs"]) == 5
    assert [parse_qs(urlsplit(url).query)["page"] for url in fetcher.calls] == [[str(i)] for i in range(1, 6)]
    assert found["jobs"][4]["apply_url"] == "https://himalayas.app/jobs/5"
    assert found["sources"][0]["limited"] and found["coverage"]["exhaustive"] is False


def test_himalayas_browse_echoes_opaque_cursor_and_stops_at_end():
    token = "opaque+/=?cursor"
    fetcher = Pages(lambda url: {"jobs": [himalaya(len(fetcher.calls))], "nextCursor": token if len(fetcher.calls) == 1 else None})
    found = collect(fetcher, ["himalayas"], [], "maximum")
    assert len(found["jobs"]) == 2 and len(fetcher.calls) == 2
    assert parse_qs(urlsplit(fetcher.calls[1]).query)["cursor"] == [token]
    assert not found["sources"][0]["limited"]


def test_jobicy_current_free_endpoint_pages_without_credentials():
    def respond(url):
        n = len(fetcher.calls)
        return {"jobs": [{"id": n, "jobTitle": "Data Analyst", "url": f"https://jobicy.com/jobs/{n}"}],
                "nextCursor": "next token?&" if n == 1 else None}
    fetcher = Pages(respond)
    found = collect(fetcher, ["jobicy"], ["analyst"], "deep")
    assert len(found["jobs"]) == 2
    assert all(url.startswith("https://jobicy.com/api/jobs?") for url in fetcher.calls)
    assert parse_qs(urlsplit(fetcher.calls[1]).query)["cursor"] == ["next token?&"]
    assert "key" not in fetcher.calls[0].lower()


def test_later_page_error_retains_first_page_and_continues_other_sources():
    def respond(url):
        if "himalayas" in url:
            if "page=2" in url:
                raise TimeoutError("later page timed out")
            return {"jobs": [himalaya(1)]}
        return {"jobs": [{"id": 2, "jobTitle": "Analyst", "url": "https://jobicy.com/jobs/2"}]}
    found = collect(Pages(respond), ["himalayas", "jobicy"], ["analyst"], "deep")
    assert len(found["jobs"]) == 2 and found["sources"][0]["kept"] == 1
    assert "TimeoutError" in found["sources"][0]["error"]
    assert found["sources"][1]["read"] == 1


@pytest.mark.parametrize("token", ["repeated", ["invalid"], "x" * 4097])
def test_bad_or_repeated_cursor_cannot_loop_or_redirect(token):
    fetcher = Pages(lambda url: {"jobs": [himalaya(len(fetcher.calls))], "nextCursor": token})
    found = collect(fetcher, ["himalayas"], [], "maximum")
    assert 1 <= len(fetcher.calls) <= 2
    assert found["jobs"] and found["sources"][0]["limited"]


def test_repeated_page_stops_even_if_provider_ignores_page_number():
    fetcher = Pages(lambda url: {"jobs": [himalaya(1)]})
    found = collect(fetcher, ["himalayas"], ["analyst"], "maximum")
    assert len(fetcher.calls) == 2 and len(found["jobs"]) == 1
    assert "repeated" in found["sources"][0]["stop_reason"]


def test_arbitrary_next_link_is_not_followed():
    fetcher = Pages(lambda url: {"data": [{"slug": str(len(fetcher.calls)), "title": "Analyst", "url": "https://www.arbeitnow.com/job/1"}],
                                "links": {"next": "http://127.0.0.1/private"}})
    found = collect(fetcher, ["arbeitnow"], ["analyst"], "deep")
    assert len(found["jobs"]) == 5
    assert all(url.startswith("https://www.arbeitnow.com/api/job-board-api?") for url in fetcher.calls)


def test_stop_retains_collected_pages_and_does_not_start_another_source():
    fetcher = Pages(lambda url: {"jobs": [himalaya(1)]})
    found = collect(fetcher, ["himalayas", "jobicy"], ["analyst"], "deep", lambda: len(fetcher.calls) < 1)
    assert len(fetcher.calls) == 1 and len(found["jobs"]) == 1
    assert found["coverage"]["stop_reason"] == "stopped by user"
    assert found["coverage"]["unvisited_sources"] == ["jobicy"]


def test_request_budget_is_shared_fairly_between_sources(monkeypatch):
    monkeypatch.setitem(DEPTHS, "deep", {"pages": 20, "terms": 6, "requests": 4, "seconds": 180})
    def respond(url):
        n = len(fetcher.calls)
        row = himalaya(n) if "himalayas" in url else {"id": n, "jobTitle": "Analyst", "url": f"https://jobicy.com/jobs/{n}"}
        return {"jobs": [row], "nextCursor": str(n)}
    fetcher = Pages(respond)
    found = collect(fetcher, ["himalayas", "jobicy"], ["analyst"], "deep")
    assert len(fetcher.calls) <= 4
    assert [item["requests"] for item in found["sources"]] == [2, 2]
    assert all(item["limited"] for item in found["sources"])


@pytest.mark.parametrize("where", ["", "Worldwide", "whole world", "all locations", "global", "anywhere", "Japan", "Tokyo; Japan"])
def test_worldwide_and_arbitrary_country_filters(where):
    assert matches_location(posting(location="Tokyo, Japan"), where)


def test_city_country_filter_does_not_expand_to_entire_country():
    assert not matches_location(posting(location="Boston, US"), "New York, US")


def test_worldwide_display_does_not_invent_worldwide_eligibility():
    from app.jobs.tools import _row_line
    row = {"number": 1, "title": "Analyst", "company": "Example", "score": 60, "verdict": "good",
           "location": "Japan", "source_name": "Public board"}
    text = _row_line(row, "Worldwide")
    assert "Japan" in text and "text names" not in text and "eligibility" not in text


def test_stop_before_start_does_not_mark_unread_sources_as_successful():
    fetcher = Pages(lambda url: pytest.fail("Cancelled search must not fetch"))
    found = collect(fetcher, ["himalayas", "jobicy"], ["analyst"], "deep", lambda: False)
    assert found["sources"] == [] and found["coverage"]["unvisited_sources"] == ["himalayas", "jobicy"]


def test_all_matching_saved_rows_are_accessible_and_chat_no_longer_cuts_at_ten(tmp_path):
    center = JobCenter(tmp_path / "jobs", object())
    center.store.upsert([posting(key=f"remoteok:{n}", company=f"Company {n}") for n in range(515)])
    for number in center.store.numbers():
        center.store.set_match(number, {"mode": "remote"}, {"score": 70, "verdict": "good"})
    first = center.matches(limit=500)
    second = center.matches(limit=500, offset=first["next_offset"])
    assert len(first["matches"]) == 500 and len(second["matches"]) == 15
    assert {row["number"] for row in first["matches"]}.isdisjoint(row["number"] for row in second["matches"])
    assert len([line for line in report_lines(second) if line.lstrip().startswith("#")]) == 15
    assert first["matching_total"] == 515 and second["next_offset"] is None


def test_maximum_search_runs_full_pipeline_beyond_first_hundred_and_pages_saved_matches(tmp_path):
    def respond(url):
        page = int(parse_qs(urlsplit(url).query).get("page", ["1"])[0])
        return {"jobs": [himalaya(page * 100 + n) for n in range(20)]}
    fetcher = Pages(respond)
    center = JobCenter(tmp_path / "jobs", fetcher)
    center.set_preferences({"sources": ["himalayas"]})
    found = center.search(query="analyst", location="Worldwide", depth="maximum")
    assert len(fetcher.calls) == 20 and found["new"] == 400
    assert len(found["matches"]) == 100 and found["matching_total"] == 400 and found["next_offset"] == 100
    tail = center.matches(query="analyst", location="Japan", offset=300, limit=200)
    assert len(tail["matches"]) == 100 and tail["next_offset"] is None
    assert found["read"][0]["limited"] and found["coverage"]["exhaustive"] is False


def test_browser_search_uses_visible_browser_and_never_submits(tmp_path):
    calls = []
    browser = SimpleNamespace(form_fields=lambda url, visible: calls.append((url, visible)),
                              scroll_through=lambda **kw: calls.append(kw) or {"scrolls": 2},
                              structured_page=lambda: {"title": "Jobs", "text": "Public page", "trust": "untrusted_external_data"})
    center = JobCenter(tmp_path / "jobs", object(), browser=browser)
    report = center.browser_search("LinkedIn", "Data Analyst", "Japan", True)
    assert calls[0][1] is True and "location=Japan" in calls[0][0]
    assert calls[1]["max_scrolls"] == 8
    assert not report["application_submitted"] and report["imported_jobs"] == 0
    assert center.store.counts()["jobs"] == 0


def test_browser_handoff_errors_are_not_reported_as_success(tmp_path):
    center = JobCenter(tmp_path / "jobs", object(), opener=lambda url: False)
    with pytest.raises(RuntimeError, match="could not open"):
        center.browser_search("Web", "analyst")
    with pytest.raises(RuntimeError, match="Connect HAYAT"):
        center.browser_search("Web", "analyst", scan=True)
    with pytest.raises(ValueError, match="Enter a role"):
        center.browser_search()


def test_browser_challenge_is_reported_as_manual_work_instead_of_job_results(tmp_path):
    browser = SimpleNamespace(form_fields=lambda *a, **kw: [],
                              structured_page=lambda: {"url": "https://www.google.com/sorry/index", "text": "Human verification"},
                              scroll_through=lambda **kw: pytest.fail("Do not scroll through a challenge"))
    center = JobCenter(tmp_path / "jobs", object(), browser=browser)
    result = center.browser_search("Employer boards", "analyst", scan=True)
    assert result["status"] == "manual_interaction_required" and result["manual_interaction_required"]
    assert "No job results were verified" in result["next_step"]
    assert result["imported_jobs"] == 0 and not result["application_submitted"]


@pytest.mark.parametrize("text,expected", [
    ("deep search for remote python jobs worldwide", {"depth": "deep", "query": "python", "remote": True, "location": "Worldwide"}),
    ("maximum search for analyst jobs in Japan", {"depth": "maximum", "query": "analyst", "location": "Japan"}),
    ("find analyst jobs worldwide", {"query": "analyst", "location": "Worldwide"}),
])
def test_chat_depth_and_worldwide_routes(text, expected):
    from app.jobs.tools import job_request
    assert job_request(text) == {"tool": "job_search", "arguments": expected}
