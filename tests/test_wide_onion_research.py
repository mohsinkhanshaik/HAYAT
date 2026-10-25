from types import SimpleNamespace
from urllib.parse import quote

import pytest

from app.research.models import ResearchBudget, SearchResult
from app.research.onion import INDEX, PUBLISHER_DIRECTORY, parse_index, parse_onion_request, research_onion
from app.research.query_planner import ResearchQueryPlanner
from app.research.search import SearchProviderChain
from tests.test_research import resource, settings
from tests.test_tor_routing import onion


def test_wide_search_combines_indexes_deduplicates_and_balances_hosts():
    first = [SearchResult('Shared', 'https://same.example/shared?utm_source=one'),
             *[SearchResult(str(i), f'https://same.example/{i}') for i in range(6)]]
    second = [SearchResult('Shared too', 'https://same.example/shared'), SearchResult('Other evidence', 'https://other.example/proof')]
    calls = []
    def provider(name, rows):
        def search(*args):
            calls.append(name)
            return rows
        return SimpleNamespace(name=name, search=search)
    chain = SearchProviderChain([provider('one', first), provider('two', second)])
    hits = chain.search_wide('shared topic', 5)
    assert set(calls) == {'one', 'two'}
    assert sum('/shared' in row.url for row in hits) == 1
    assert any(row.domain == 'other.example' for row in hits)
    assert [row.rank for row in hits] == list(range(1, len(hits) + 1))
    assert first[0].rank == 0


def test_wide_search_keeps_provider_errors_and_rejects_private_results():
    def fail(*args):
        raise TimeoutError('offline')
    chain = SearchProviderChain([SimpleNamespace(name='failed', search=fail),
                                SimpleNamespace(name='good', search=lambda *args: [SearchResult('private', 'http://127.0.0.1'),
                                                                                 SearchResult('Public', 'https://public.example/proof')])])
    assert len(chain.search_wide('topic')) == 1
    assert 'failed: TimeoutError' in chain.last_errors[0]


def test_deep_query_budget_keeps_different_evidence_intents():
    plan = ResearchQueryPlanner().plan('Compare current HTTPX and CPython API behavior', 'DEEP', ResearchBudget(searches=4))
    assert len(plan) == 4
    assert len({item.intent for item in plan}) == 4
    assert plan[0].intent == 'primary'


def test_deep_research_follows_a_relevant_reference_within_page_budget(tmp_path):
    from app.research.manager import ResearchManager
    class Search:
        def search(self, *args, **kwargs):
            return [SearchResult('Widget official documentation', 'https://docs.example.com/widget', rank=1)]
    class Fetch:
        def __init__(self):
            self.urls = []
        def fetch(self, url, *args, **kwargs):
            self.urls.append(url)
            return resource(url, b'<html><title>Widget specification</title><main><p>Widget supports safe mode for every API request.</p>'
                                 b'<a href="/widget-reference">Widget reference specification</a>'
                                 b'<a href="/unrelated">Contact us</a></main></html>')
        def close(self):
            pass
    manager = ResearchManager(settings(tmp_path), search_provider=Search())
    manager.fetcher.close()
    manager.fetcher = fetcher = Fetch()
    result = manager.research('Widget API', 'deep', remember=False,
                              _budget=ResearchBudget(searches=1, pages=4, max_seconds=10, max_bytes=100000, max_workers=1))
    assert fetcher.urls == ['https://docs.example.com/widget', 'https://docs.example.com/widget-reference']
    assert result.trace['followed_links'][0]['provider'] == 'source_link'
    assert result.metrics.pages_attempted <= 4
    manager.close()


def test_failed_search_is_not_cached_and_wide_cache_is_separate(tmp_path):
    from app.research.manager import ResearchManager
    class Search:
        name = 'test'
        def __init__(self):
            self.calls = []
        def search(self, *args, **kwargs):
            self.calls.append('normal')
            return []
        def search_wide(self, *args, **kwargs):
            self.calls.append('wide')
            return [SearchResult('Evidence', 'https://example.org/evidence')]
    search = Search()
    manager = ResearchManager(settings(tmp_path), search_provider=search)
    budget = ResearchBudget()
    manager._search_cached('topic', budget, 'primary')
    manager._search_cached('topic', budget, 'primary')
    manager._search_cached('topic', budget, 'primary', wide=True)
    rows, hit = manager._search_cached('topic', budget, 'counter_evidence', wide=True)
    assert search.calls == ['normal', 'normal', 'wide']
    assert hit and rows[0].query_intent == 'counter_evidence'
    manager.close()


def index_page(url):
    return f'<ol class="searchResults"><li class="result"><h4><a href="/search/redirect?redirect_url={quote(url, safe="")}">Public evidence</a></h4><p>Index description</p></li></ol>'


def test_onion_index_decodes_redirect_without_visiting_it_or_using_dns(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo', lambda *args: pytest.fail('local DNS was used'))
    target = f'http://{onion()}/evidence'
    rows = parse_index(index_page(target), 'topic', 5)
    assert rows[0].url == target and rows[0].provider == 'ahmia_onion'


@pytest.mark.parametrize('url', ['http://127.0.0.1/', 'http://invalid.onion/', 'https://example.onion.evil.example',
                                 'file:///C:/secret', 'http://user:password@' + onion()])
def test_index_does_not_admit_private_invalid_or_gateway_links(url):
    with pytest.raises(ValueError, match='no usable v3'):
        parse_index(index_page(url), 'topic', 5)


def test_index_empty_result_is_distinct_from_a_challenge():
    assert parse_index('<p id="noResults">No matching public onion services</p>', 'topic', 5) == []
    with pytest.raises(ValueError, match='challenge'):
        parse_index('<html>Verify you are human</html>', 'topic', 5)


def test_onion_research_without_tor_never_contacts_an_index(monkeypatch):
    from app.privacy_masks import ROUTE
    previous = ROUTE.proxy
    try:
        ROUTE.set(None)
        monkeypatch.setattr('app.research.fetcher.PublicFetcher.fetch', lambda *args, **kwargs: pytest.fail('direct onion search'))
        with pytest.raises(PermissionError, match='requires Tor'):
            research_onion(SimpleNamespace(settings={}, model=None), 'Tor Project')
    finally:
        ROUTE.set(previous)


def test_onion_workflow_uses_index_and_publisher_then_retrieves_via_required_tor(tmp_path, monkeypatch):
    from app.privacy_masks import ROUTE
    from app.research.manager import ResearchManager
    from app.research.onion import TorRequiredFetcher
    previous = ROUTE.proxy
    calls = []
    target = f'http://{onion()}/'
    def fetch(self, url, *args, **kwargs):
        calls.append((url, kwargs.get('require_tor')))
        assert kwargs.get('require_tor') is True
        if url.startswith(INDEX):
            return resource(url, index_page(target).encode())
        if url == PUBLISHER_DIRECTORY:
            return resource(url, f'<a href="{target}">Tor Project</a>'.encode())
        assert url == target
        return resource(url, b'<html><title>Tor Project onion documentation</title><main><h1>Tor Project</h1>'
                             b'<p>The Tor Project provides privacy tools and documentation for onion services.</p></main></html>')
    # Inspect the mandatory flag at PublicFetcher, beneath the Tor-only wrapper.
    monkeypatch.setattr('app.research.fetcher.PublicFetcher.fetch', fetch)
    monkeypatch.setattr(TorRequiredFetcher, 'close', lambda self: None)
    try:
        ROUTE.set('socks5://127.0.0.1:19999')
        manager = ResearchManager(settings(tmp_path))
        result = manager.research_onion('Tor Project', 'quick')
        assert result.sources and result.trace['onion_scope']['tor_required']
        assert any(url.startswith(INDEX) for url, _ in calls)
        assert target in [url for url, _ in calls]
        with manager.knowledge.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 0
        manager.close()
    finally:
        ROUTE.set(previous)


@pytest.mark.parametrize('text', ['search the dark web for Tor Project', 'research onion services about censorship',
                                  'search for Tor Project on the darknet'])
def test_explicit_onion_requests_have_a_specific_query(text):
    assert parse_onion_request(text)
    assert parse_onion_request('what is the dark web?') is None


@pytest.mark.parametrize('text,query', [
    # Live 2026-10-24: these became business ideas, conversation, or nothing instead of a dark-web search.
    ('look all ways to make money on dark web and look what suspious going in there', 'all ways to make money'),
    ('look what is suspicious on the dark web', 'what is suspicious'),
    ('find ransomware groups on the dark web', 'ransomware groups'),
    ('monitor the darknet for mentions of my company', 'mentions of my company'),
    ('check the dark web for my leaked passwords', 'my leaked passwords'),
])
def test_more_dark_web_phrasings_reach_the_onion_searcher(text, query):
    assert parse_onion_request(text) == query


def test_a_bare_dark_web_search_runs_a_general_scan():
    assert parse_onion_request('search the dark web') == 'notable public onion services and recent activity'
    assert parse_onion_request('look on the darknet') == 'notable public onion services and recent activity'


@pytest.mark.parametrize('text', ['what is the dark web', 'how does tor work', 'search the web for ai news',
                                  'look at me again', 'is the dark web illegal'])
def test_definitions_and_non_onion_requests_are_left_alone(text):
    assert parse_onion_request(text) is None


def test_onion_job_reports_missing_evidence_as_failure():
    from app.research.jobs import ResearchJobs
    result = SimpleNamespace(answer='The indexes could not retrieve sources.', complete=False, confidence=0, sources=[],
                             claims=[], errors=['Index unavailable'], trace={'onion_scope': {'tor_required': True}})
    jobs = ResearchJobs(lambda: SimpleNamespace(research_onion=lambda *args, **kwargs: result))
    job = jobs.start('public onion topic', onion=True)
    assert job._done.wait(3)
    assert job.status == 'failed' and 'Index unavailable' in job.error


def test_chat_onion_request_runs_the_onion_worker_and_keeps_the_scope_on_retry(monkeypatch):
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=False, start_background_services=False)
    calls = []
    def onion_research(query, level='deep'):
        calls.append((query, level))
        return SimpleNamespace(answer='Public onion sources checked; evidence is limited.', complete=True, confidence=.5,
                               sources=[], claims=[], errors=[], trace={'onion_scope': {'tor_required': True}})
    monkeypatch.setattr(runtime.research, 'research_onion', onion_research)
    runtime.research_jobs.inline_seconds = 2
    try:
        runtime.assistant.handle('search the dark web for Tor Project')
        job = runtime.research_jobs.recent()[0]
        assert job._done.wait(3)
        assert job.onion and calls[0][0] == 'Tor Project'
        runtime.assistant.handle('search deeper')
        retried = runtime.research_jobs.recent()[0]
        assert retried._done.wait(3)
        assert retried.onion and len(calls) == 2
    finally:
        runtime.close()


def test_tor_required_search_transport_never_uses_direct_http_when_route_is_off(monkeypatch):
    from app.privacy_masks import ROUTE
    from app.research.search import DuckDuckGoHtmlSearch
    previous = ROUTE.proxy
    try:
        ROUTE.set(None)
        monkeypatch.setattr('httpx.get', lambda *args, **kwargs: pytest.fail('Tor-only query leaked directly'))
        with pytest.raises(PermissionError, match='requires Tor'):
            DuckDuckGoHtmlSearch(require_tor=True).search('public onion references')
    finally:
        ROUTE.set(previous)


def test_directory_prioritizes_public_information_and_groups_same_publisher():
    from app.research.onion import PublisherOnionSearch, _DirectoryIndependence
    from app.research.extractor import ContentExtractor
    target = f'http://{onion()}'
    html = f'<ul><li><a href="https://accounts.torproject.org/">Accounts</a><ul><li><a href="{target}/admin">Admin</a></li></ul></li>'
    html += f'<li><a href="https://www.torproject.org/">Main</a><ul><li><a href="{target}/main">Main site</a></li></ul></li></ul>'
    class Fetcher:
        def fetch(self, url, *args, **kwargs):
            return resource(url, html.encode())
    provider = PublisherOnionSearch(Fetcher())
    rows = provider.search('Tor Project', limit=2)
    assert rows[0].url.endswith('/main')
    docs = [ContentExtractor().extract(resource(row.url, f'<main>Tor Project documentation for {index}</main>'.encode()))[0]
            for index, row in enumerate(rows)]
    independence = _DirectoryIndependence(provider)
    independence.annotate(docs)
    assert independence.independent_count(docs) == 1
    assert all(item.metadata['onion_publisher_directory']['publisher'] == 'torproject.org' for item in docs)


def test_tor_search_does_not_decode_an_already_decoded_body_twice(monkeypatch):
    from app.research.search import _search_get
    from app.privacy_masks import ROUTE
    original = ROUTE.proxy
    def fetch(self, url, *args, **kwargs):
        assert kwargs['require_tor']
        data = resource(url, b'<html><body>Decoded search results</body></html>')
        data.headers.update({'Content-Encoding': 'br', 'Content-Length': '15'})
        return data
    monkeypatch.setattr('app.research.fetcher.PublicFetcher.fetch', fetch)
    try:
        ROUTE.set('socks5://127.0.0.1:19999')
        response = _search_get('https://example.org/search', require_tor=True)
        assert 'Decoded search results' in response.text
        assert 'content-encoding' not in response.headers
        assert response.headers['content-length'] != '15'
    finally:
        ROUTE.set(original)


def test_explicit_onion_workflow_checks_tor_bootstrap_before_any_network_read(monkeypatch):
    calls = []
    def ensure():
        calls.append('bootstrap')
        return {'masked': False, 'reason': 'Tor is not installed'}
    monkeypatch.setattr('app.research.fetcher.PublicFetcher.fetch', lambda *args, **kwargs: pytest.fail('read before Tor readiness'))
    with pytest.raises(PermissionError, match='not installed'):
        research_onion(SimpleNamespace(settings={}, model=None, ensure_tor=ensure), 'public onion documentation')
    assert calls == ['bootstrap']


@pytest.mark.parametrize('protocol', ['https', 'http'])
@pytest.mark.parametrize('advertisement', ['header', 'meta'])
def test_publisher_onion_location_discovery_requires_https(protocol, advertisement):
    from app.research.onion import OnionPublisherWebSearch
    target = f'http://{onion()}/public'
    publisher = f'{protocol}://publisher.example/documentation'
    class Fetch:
        def fetch(self, url, *args, **kwargs):
            assert kwargs['require_tor']
            body = f'<meta http-equiv="onion-location" content="{target}">' if advertisement == 'meta' else '<html>Public information</html>'
            data = resource(url, body.encode())
            if advertisement == 'header':
                data.headers['onion-location'] = target
            return data
    provider = OnionPublisherWebSearch(Fetch())
    provider.searcher = SimpleNamespace(search_wide=lambda *args, **kwargs: [SearchResult('Publisher', publisher)], last_errors=[])
    found = provider.search('publisher topic')
    assert [item.url for item in found] == ([target] if protocol == 'https' else [])


def test_a_failed_onion_search_explains_what_works_instead():
    # Live 2026-10-24: "search the dark web for ai" gave a generic dead-end; now it says why and what works.
    from app.research.jobs import ResearchJobs
    result = SimpleNamespace(answer='', complete=False, confidence=0, sources=[], claims=[],
                             errors=['ahmia_onion: robots.txt disallows retrieval', 'tor_project_directory: no usable results'],
                             trace={'onion_scope': {'tor_required': True}})
    jobs = ResearchJobs(lambda: SimpleNamespace(research_onion=lambda *a, **k: result))
    job = jobs.start('ai', onion=True)
    assert job._done.wait(3) and job.status == 'failed'
    text = jobs.result_text(job)
    assert 'No supported onion answer was retrieved for "ai"' in text and "robots.txt" in text
    assert 'Tor web (public internet)' in text and 'known public v3 URL' in text
    assert 'not proof that no matching service exists' in text
    assert 'No official .onion address is published' not in text
    assert "robots.txt disallows retrieval" in text  # the raw per-source reasons are still shown


def test_a_public_listing_that_prints_an_onion_address_is_admitted():
    from app.research.onion import OnionPublisherWebSearch
    address = f"http://{onion()}/"
    provider = OnionPublisherWebSearch(fetcher=SimpleNamespace())
    provider.searcher = SimpleNamespace(
        search_wide=lambda *a, **k: [SearchResult('A directory', 'https://listing.example/page',
                                                  snippet=f'Official address: {address} — verified')],
        last_errors=[])
    found = provider.search('example service')
    assert [item.url for item in found] == [address]
    assert found[0].provider == 'public_onion_references'
