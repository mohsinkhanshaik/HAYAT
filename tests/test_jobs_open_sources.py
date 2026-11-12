"""Job search beyond one board (2026-11-08): Hacker News hiring posts, Sweden's open public-service API,
Workable boards, and employers' own boards found by search or by companies seen hiring (app/jobs/discovery.py).

Live check on 2026-11-08 that motivated it: of 176 saved jobs, 171 came from Himalayas; Remote OK's tag
search returned no jobs, The Muse refused (403) and the other feeds returned their newest postings unsearched.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.jobs.discovery import BoardDirectory, boards_for_round, probe_companies, search_boards
from app.jobs.profile import DEFAULT_PREFERENCES, OLD_DEFAULT_SOURCES, Profile
from app.jobs.sources import FEEDS, board_from_url, hnhiring_rows, jobtech_rows, read_source

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)


class Fetcher:
    def __init__(self, boards=None):
        self.calls, self.boards = [], boards or {}

    def fetch(self, url, max_bytes, accept=None, request_headers=None):
        self.calls.append(url)
        for marker, body in self.boards.items():
            if marker in url:
                return SimpleNamespace(content=json.dumps(body).encode())
        if "remoteok.com/api" in url:
            body = [{"legal": "terms"}, {"id": "1", "position": "Python Developer", "company": "Acme", "url": "https://remoteok.com/1"}]
        else:
            raise LookupError("404 " + url)
        return SimpleNamespace(content=json.dumps(body).encode())


class Search:
    def __init__(self, urls):
        self.urls, self.queries = urls, []

    def search(self, query, limit=10, recency=None, domains=None):
        self.queries.append((query, tuple(domains or ())))
        return [SimpleNamespace(url=url, title="") for url in self.urls.get(domains[0], [])]


WORKABLE = {"name": "Hugging Face", "jobs": [
    {"title": "Senior Python Engineer", "shortcode": "F884", "url": "https://apply.workable.com/j/F884",
     "application_url": "https://apply.workable.com/j/F884/apply", "city": "Paris", "country": "France",
     "telecommuting": True, "department": "Open source", "published_on": "2026-11-01", "employment_type": "Full-time"},
    {"title": "Office Manager", "shortcode": "A1", "url": "https://apply.workable.com/j/A1", "country": "France"}]}


def test_hacker_news_hiring_posts_are_read_as_postings_and_replies_are_not():
    data = {"hits": [
        {"objectID": "11", "story_id": 9, "parent_id": 9, "created_at": "2026-11-02T17:00:00Z",
         "comment_text": "ICEYE | Platform Software Engineers (Senior/Staff) | Espoo, Finland / Berlin, Germany | ONSITE"
                         "<p>Python and Go on satellite data.</p>"},
        {"objectID": "12", "story_id": 9, "parent_id": 9, "created_at": "2026-11-02T17:00:00Z",
         "comment_text": "SadServers | Software Engineer | REMOTE (Canada)"},
        {"objectID": "13", "story_id": 9, "parent_id": 11, "comment_text": "Do you sponsor visas?"},       # a reply
        {"objectID": "14", "story_id": 9, "parent_id": 9, "comment_text": "We are a great team, ping me"},  # no first line
    ]}
    rows = {row["company"]: row for row in hnhiring_rows(data, "python")}
    assert set(rows) == {"ICEYE", "SadServers"}
    iceye = rows["ICEYE"]
    assert iceye["title"] == "Platform Software Engineers (Senior/Staff)" and "Espoo" in iceye["location"]
    assert iceye["url"] == "https://news.ycombinator.com/item?id=11" and iceye["remote"] is None
    assert "satellite data" in iceye["description"] and iceye["source_name"] == "Hacker News: Who is hiring?"
    assert rows["SadServers"]["remote"] is True


def test_swedens_public_employment_api_is_read_with_its_own_links():
    rows = jobtech_rows({"hits": [{"id": "301", "headline": "Python-utvecklare", "employer": {"name": "Spotify AB"},
                                   "workplace_address": {"municipality": "Stockholm", "country": "Sverige"},
                                   "description": {"text": "Vi söker en utvecklare."}, "publication_date": "2026-11-05T10:00:00",
                                   "application_deadline": "2026-12-01T23:59:59", "webpage_url": "https://arbetsformedlingen.se/platsbanken/annonser/301",
                                   "application_details": {"url": "https://spotify.example/apply"}}]})
    assert rows[0]["title"] == "Python-utvecklare" and rows[0]["company"] == "Spotify AB"
    assert rows[0]["apply_url"] == "https://spotify.example/apply" and rows[0]["expires_at"].startswith("2026-12-01")


def test_remote_ok_is_read_whole_because_its_tag_search_returns_no_jobs():
    fetcher = Fetcher()
    rows = read_source(fetcher, "remoteok", ["python developer"])
    assert fetcher.calls == ["https://remoteok.com/api"] and rows[0]["title"] == "Python Developer"


def test_workable_boards_are_recognised_and_read():
    assert board_from_url("https://apply.workable.com/huggingface/j/F884/") == ("workable", "huggingface")
    assert board_from_url("https://apply.workable.com/j/F884") is None  # a short job link names no company
    rows = read_source(Fetcher({"apply.workable.com/api/v1/widget/accounts/huggingface": WORKABLE}), "workable:huggingface", [])
    first = rows[0]
    assert first["company"] == "Hugging Face" and first["source"] == "workable@huggingface" and first["remote"] is True
    assert first["location"] == "Paris, France" and first["apply_url"].endswith("/apply")


def test_new_sources_are_on_by_default_and_the_old_default_list_is_upgraded(tmp_path):
    assert {"hnhiring", "company_boards"} <= set(DEFAULT_PREFERENCES["sources"]) <= set(FEEDS)
    assert "themuse" not in DEFAULT_PREFERENCES["sources"]
    profile = Profile(tmp_path / "profile.json")
    profile.save({"preferences": {"sources": list(OLD_DEFAULT_SOURCES)}})
    assert profile.load()["preferences"]["sources"] == DEFAULT_PREFERENCES["sources"]
    profile.save({"preferences": {"sources": ["himalayas", "themuse"]}})  # a real choice stays
    assert profile.load()["preferences"]["sources"] == ["himalayas", "themuse"]


def test_search_finds_boards_and_the_directory_remembers_and_ranks_them(tmp_path):
    search = Search({"job-boards.greenhouse.io": ["https://job-boards.greenhouse.io/canonical/jobs/1",
                                                  "https://example.com/not-a-board"],
                     "jobs.lever.co": ["https://jobs.lever.co/zimperium/62345dc6"]})
    found = search_boards(search, ["python developer"])
    assert set(found) == {"greenhouse:canonical", "lever:zimperium"}
    assert search.queries[0] == ('"python developer"', ("job-boards.greenhouse.io", "boards.greenhouse.io"))
    directory = BoardDirectory(tmp_path / "boards.json")
    plan = boards_for_round(directory, search, ["python developer"])
    assert set(plan["new"]) == set(found)
    assert set(plan["boards"]) == set(found) and plan["directory"] == 2
    # Next time, with a search engine that finds nothing, the known boards for similar terms are still read.
    again = boards_for_round(directory, Search({}), ["senior python engineer"])
    assert set(again["boards"]) == set(found) and again["new"] == []
    assert boards_for_round(directory, Search({}), ["nurse"])["boards"] == []


def test_boards_that_keep_giving_nothing_rest_for_a_week(tmp_path):
    directory = BoardDirectory(tmp_path / "boards.json")
    directory.add({"lever:quiet": {"terms": ["python"]}, "lever:busy": {"terms": ["python"]}})
    for _ in range(3):
        directory.record("lever:quiet", 40, 0)
    directory.record("lever:busy", 40, 5)
    assert directory.known_for(["python"]) == ["lever:busy"]
    later = datetime.now(UTC) + timedelta(days=8)
    assert set(directory.known_for(["python"], now=later)) == {"lever:busy", "lever:quiet"}


def test_companies_seen_hiring_are_checked_once_a_month_for_their_own_board(tmp_path):
    fetcher = Fetcher({"apply.workable.com/api/v1/widget/accounts/huggingface": WORKABLE})
    directory = BoardDirectory(tmp_path / "boards.json")
    new = probe_companies(fetcher, directory, ["Hugging Face", "Nobody Ltd", "Hugging Face"], ["python"])
    assert new == ["workable:huggingface"]
    probes = json.loads((tmp_path / "board-probes.json").read_text(encoding="utf-8"))
    assert probes["hugging face"]["board"] == "workable:huggingface" and probes["nobody ltd"]["board"] is None
    calls = len(fetcher.calls)
    assert probe_companies(fetcher, directory, ["Hugging Face", "Nobody Ltd"], ["python"]) == []
    assert len(fetcher.calls) == calls  # answers are kept for a month


def test_the_owners_own_searxng_comes_first_and_must_be_local_or_https(monkeypatch):
    import httpx
    import pytest

    from app.research.search import SearxngSearch, build_search_provider

    chain = build_search_provider({"searxng_url": "http://127.0.0.1:8888"})
    assert chain.providers[0].name == "searxng" and len(chain.providers) > 1  # the public engines stay as fallback
    assert build_search_provider({"searxng_url": "http://127.0.0.1:8888", "search_provider": "searxng"}).providers[0].name == "searxng"
    assert all(provider.name != "searxng" for provider in build_search_provider({"searxng_url": "http://8.8.8.8"}).providers)
    with pytest.raises(ValueError):
        SearxngSearch("http://search.example.com")
    seen = {}

    def fake_get(url, params=None, **kwargs):
        seen.update(url=url, params=params)
        return httpx.Response(200, json={"results": [{"url": "https://jobs.lever.co/acme/1", "title": "Python Developer",
                                                      "content": "Remote"}, {"url": "javascript:alert(1)", "title": "x"}]},
                              request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", fake_get)
    results = SearxngSearch("http://localhost:8888/").search('"python developer"', domains=["jobs.lever.co"])
    assert [result.url for result in results] == ["https://jobs.lever.co/acme/1"]
    assert seen["url"] == "http://localhost:8888/search" and seen["params"]["format"] == "json"
    assert "site:jobs.lever.co" in seen["params"]["q"]


def test_a_job_search_reads_boards_found_by_search_and_by_companies(tmp_path):
    from app.jobs.center import JobCenter

    boards = {"apply.workable.com/api/v1/widget/accounts/huggingface": WORKABLE,
              "hn.algolia.com/api/v1/search_by_date": {"hits": [{"objectID": "9", "title": "Ask HN: Who is hiring? (November 2026)"}]},
              "hn.algolia.com/api/v1/search?": {"hits": [{"objectID": "11", "story_id": 9, "parent_id": 9,
                                                         "comment_text": "Hugging Face | Python Engineer | Paris | REMOTE"}]},
              "boards-api.greenhouse.io/v1/boards/canonical": {"jobs": [
                  {"id": 5, "title": "Python Developer", "company_name": "Canonical", "absolute_url": "https://job-boards.greenhouse.io/canonical/jobs/5",
                   "location": {"name": "Remote"}, "content": "Python", "departments": [], "first_published": NOW.isoformat()}]}}
    center = JobCenter(tmp_path / "jobs", Fetcher(boards))
    center.search_provider = Search({"job-boards.greenhouse.io": ["https://job-boards.greenhouse.io/canonical/jobs/5"]})
    center.set_preferences({"sources": ["hnhiring", "company_boards"], "search_terms": ["python"]})
    result = center.search("python")
    by_source = {item["source"]: item for item in result["read"]}
    assert by_source["greenhouse:canonical"]["kept"] == 1                       # found by search
    assert by_source["workable:huggingface"]["kept"] == 1                      # found from the HN post's company
    assert result["company_boards"]["from_companies"] == ["workable:huggingface"]
    assert {row["source_name"] for row in result["matches"]} == {"Greenhouse", "Workable", "Hacker News: Who is hiring?"}
    assert center.boards.summary()["boards"] == 2
    # Within the hour nothing is searched or read again.
    queries = len(center.search_provider.queries)
    center.search()
    assert len(center.search_provider.queries) == queries
