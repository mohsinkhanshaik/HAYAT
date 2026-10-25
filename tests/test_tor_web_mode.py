from types import SimpleNamespace

import pytest

from app.research.jobs import ResearchJobs
from app.research.models import SearchResult
from app.research.onion import parse_tor_web_request, research_tor_web
from tests.test_research import resource
from tests.test_networking_actions import runtime as runtime


def test_tor_web_never_searches_directly_when_tor_is_off(monkeypatch):
    from app.privacy_masks import ROUTE
    ROUTE.set(None)
    monkeypatch.setattr('app.research.search.DuckDuckGoHtmlSearch.search', lambda *a, **k: pytest.fail('leaked request'))
    with pytest.raises(PermissionError, match='requires Tor'):
        research_tor_web(SimpleNamespace(settings={}, model=None), 'artificial intelligence')


def _provider(self, *args, **kwargs):
    assert self.require_tor is True
    return [SearchResult('Artificial intelligence definition', 'https://docs.example.com/ai', rank=1)]


def test_public_web_sources_are_tor_only_and_distinct_from_onion_discovery(monkeypatch):
    from app.privacy_masks import ROUTE
    ROUTE.set('socks5://127.0.0.1:19999')
    monkeypatch.setattr('app.research.search.DuckDuckGoHtmlSearch.search', _provider)
    monkeypatch.setattr('app.research.search.BingRssSearch.search', _provider)
    calls = []
    def fetch(self, url, *args, **kwargs):
        calls.append(kwargs.get('require_tor'))
        return resource(url, b'<html><title>Artificial intelligence definition</title><main><p>Artificial intelligence uses computing systems to perform tasks that usually require human intelligence.</p></main></html>')
    monkeypatch.setattr('app.research.fetcher.PublicFetcher.fetch', fetch)
    result = research_tor_web(SimpleNamespace(settings={'model_synthesis': False}, model=None), 'artificial intelligence', 'quick')
    assert result.sources and calls and all(calls)
    assert result.trace['tor_web_scope']['onion_only'] is False
    assert 'ordinary websites, not onion' in result.answer


def test_stop_resume_between_fetch_and_accept_rejects_old_tor_results(monkeypatch):
    from app.emergency import ExecutionInterlock
    from app.privacy_masks import ROUTE
    ROUTE.set('socks5://127.0.0.1:19999')
    parent = SimpleNamespace(settings={'model_synthesis': False}, model=None, interlock=ExecutionInterlock())
    monkeypatch.setattr('app.research.search.DuckDuckGoHtmlSearch.search', _provider)
    monkeypatch.setattr('app.research.search.BingRssSearch.search', _provider)
    def fetch(self, url, *args, **kwargs):
        parent.interlock.halt()
        parent.interlock.resume()
        return resource(url, b'<html><p>Artificial intelligence is a computing field.</p></html>')
    monkeypatch.setattr('app.research.fetcher.PublicFetcher.fetch', fetch)
    with pytest.raises(PermissionError):
        research_tor_web(parent, 'artificial intelligence', 'quick')


def test_jobs_keep_tor_public_web_scope_and_never_promote_failed_evidence():
    calls = []
    result = SimpleNamespace(answer='No source could be verified.', complete=False, confidence=0,
                             sources=[], claims=[], errors=['Provider unavailable'], trace={'tor_web_scope': {}})
    manager = SimpleNamespace(research_tor_web=lambda *a, **k: calls.append(a) or result)
    jobs = ResearchJobs(lambda: manager)
    job = jobs.start('AI', tor_web=True)
    assert job._done.wait(3)
    assert calls and job.status == 'failed' and job.to_dict()['tor_web']
    assert 'No direct-network fallback' in jobs.result_text(job)
    with pytest.raises(ValueError):
        jobs.start('AI', tor_web=True, onion=True)


def test_explicit_tor_chat_and_browser_handoff_preserve_routing_requirement():
    from app.agent.planner import Planner
    from app.intent.router import IntentRouter
    from app.privacy_masks import ROUTE
    from app.research.handoff import browser_handoff
    ROUTE.set(None)
    text = 'Search public web through Tor for artificial intelligence'
    assert parse_tor_web_request(text) == 'artificial intelligence'
    assert parse_tor_web_request('Search the dark web for AI') is None
    intent = IntentRouter().route(text)
    assert intent.entities['modality'] == 'tor_web_research'
    steps = Planner(SimpleNamespace(names=lambda: ['tor_web_research']))._deterministic_plan(text, intent)
    assert steps[0].arguments['query'] == 'artificial intelligence'
    launched = []
    manager = SimpleNamespace(search_browser=lambda url, required: launched.append(required) or {'opened': True})
    output = browser_handoff(manager, 'AI', open_browser=True, tor_only=True)
    assert output['tor_required'] and launched == [True]


def test_chat_search_again_cannot_change_tor_web_job_to_direct_web(runtime, monkeypatch):
    calls = []
    output = SimpleNamespace(answer='Public sources read through Tor.', complete=True, confidence=.5,
                             sources=[], claims=[], errors=[], trace={'tor_web_scope': {'tor_required': True}})
    monkeypatch.setattr(runtime.research, 'research_tor_web', lambda query, **kwargs: calls.append(query) or output)
    monkeypatch.setattr(runtime.research, 'research', lambda *a, **k: pytest.fail('retry changed to ordinary/direct research'))
    runtime.research_jobs.inline_seconds = 2
    runtime.assistant.handle('Search public web through Tor for AI')
    assert runtime.research_jobs.recent()[0].tor_web
    runtime.assistant.handle('search again')
    latest = runtime.research_jobs.recent()[0]
    assert latest._done.wait(3) and latest.tor_web and calls == ['AI', 'AI']
