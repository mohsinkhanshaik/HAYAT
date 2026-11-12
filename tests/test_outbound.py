"""Every reply says what left this computer (app/outbound.py), from the records the transfer log keeps."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.execution import execution_scope
from app.outbound import describe, model_location, summary
from app.transfers import transfers


def turn():
    return "turn-" + uuid.uuid4().hex


LOCAL = SimpleNamespace(base_url="http://localhost:11435")


def test_a_reply_that_sent_nothing_says_so():
    report = summary(turn_id=turn(), model=LOCAL)
    assert report["nothing_sent"] and describe(report) == ["Answered on this computer; nothing was sent out."]


def test_searches_and_pages_are_counted_for_their_own_turn_only():
    mine, other = turn(), turn()
    with execution_scope(turn_id=mine):
        transfers().search("weather in dubai", ["duckduckgo", "bing"], 8)
        transfers().web("https://en.wikipedia.org/wiki/Dubai?key=secret", purpose="research", size=10, ms=5)
        transfers().web("https://en.wikipedia.org/wiki/Climate", purpose="research", size=10, ms=5)
        transfers().web("https://www.weather.gov/x", purpose="research", size=10, ms=5)
    with execution_scope(turn_id=other):
        transfers().web("https://example.com/", purpose="research", size=1, ms=1)
    report = summary(turn_id=mine, model=LOCAL)
    assert report["searches"] == [{"query": "weather in dubai", "engines": ["duckduckgo", "bing"]}]
    assert report["sites"] == {"en.wikipedia.org": 2, "www.weather.gov": 1} and report["pages"] == 3
    [model_line, sent] = describe(report)
    assert model_line == "The reply model ran on this computer."
    assert sent == ("Sent out: 1 web search (“weather in dubai”); 3 pages from 2 sites "
                    "(en.wikipedia.org ×2, www.weather.gov).")
    assert all("secret" not in str(row) for row in transfers().web_recent(10))  # query strings never kept


def test_the_search_chain_records_the_words_it_sends(tmp_path):
    from app.research.models import SearchResult
    from app.research.search import SearchProviderChain

    class Engine:
        name = "fake-engine"

        def search(self, query, limit=10, recency=None, domains=None):
            return [SearchResult("A", "https://a.example.org/", "", "a.example.org", rank=1, provider="fake-engine")]

    mine = turn()
    with execution_scope(turn_id=mine):
        SearchProviderChain([Engine()]).search("tokio worker threads", limit=5)
    assert summary(turn_id=mine)["searches"] == [{"query": "tokio worker threads", "engines": ["fake-engine"]}]


def test_an_outward_action_is_named_by_where_it_went_never_by_what_it_said(tmp_path):
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    schema = {"type": "object", "properties": {"to": {"type": "array"}, "body": {"type": "string"}},
              "additionalProperties": False}
    registry.register("send_email", "Sends.", schema, "low", "send_message", lambda to, body: {"sent": True})
    registry.register("read_note", "Reads.", {"type": "object", "properties": {}}, "low", "filesystem_read",
                      lambda: {"ok": True})
    mine = turn()
    with execution_scope(turn_id=mine):
        assert registry.execute("send_email", {"to": ["a@b.c", "d@e.f"], "body": "my secret plan"}).success
        assert registry.execute("read_note", {}).success
    report = summary(turn_id=mine, model=LOCAL)
    assert report["actions"] == ["send_email: sent a message (2 recipients)"]
    assert "secret plan" not in str(report) and not report["nothing_sent"]
    assert describe(report)[-1] == "Sent out: send_email: sent a message (2 recipients)."


def test_where_the_model_runs_is_read_from_its_address():
    assert model_location(SimpleNamespace(base_url="http://127.0.0.1:11435")) == "this computer"
    assert model_location(SimpleNamespace(base_url="https://api.openai.com/v1")) == "api.openai.com"
    assert model_location(SimpleNamespace()) == ""
    remote = summary(turn_id=turn(), model=SimpleNamespace(base_url="https://api.openai.com/v1"))
    assert not remote["nothing_sent"]
    assert describe(remote) == ["The reply model runs at api.openai.com: what you wrote was sent there."]


def test_the_details_panel_shows_the_privacy_line():
    from app.answer_provenance import describe as provenance

    runtime = SimpleNamespace(session=SimpleNamespace(last_answer_source="exact",
                                                      last_outbound=summary(turn_id=turn(), model=LOCAL)))
    lines = provenance(runtime)
    assert lines[0].startswith("Worked out by code") and lines[-1] == "Answered on this computer; nothing was sent out."


def test_a_background_search_reports_what_it_sent():
    import time

    from app.research.jobs import ResearchJobs

    class Research:
        def close(self):
            pass

        def research(self, query, level="normal", remember=True):
            transfers().search(query, ["duckduckgo"], 3)
            transfers().web("https://docs.rs/tokio", purpose="research", size=10, ms=5)
            return SimpleNamespace(answer="Tokio uses one worker per core [1].", confidence=0.8, sources=[1],
                                   claims=[], errors=[], complete=True)

    delivered = []
    jobs = ResearchJobs(lambda: Research(), deliver=lambda job, text: delivered.append(text), inline_seconds=0)
    job = jobs.start("tokio worker threads")
    deadline = time.monotonic() + 5
    while job.status not in {"done", "failed"} and time.monotonic() < deadline:
        time.sleep(0.02)
    assert job.answer.rstrip().endswith("Sent out: 1 web search (“tokio worker threads”); 1 page from 1 site (docs.rs).")
