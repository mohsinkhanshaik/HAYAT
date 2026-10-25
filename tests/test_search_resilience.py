from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest

from app.research.models import SearchResult
from app.research.search import BraveSearch, SearchProviderChain, build_search_provider


def result(url):
    return SearchResult('Title', url, '', 'provider-supplied-wrong-domain')


def test_search_filters_scope_deduplicates_and_preserves_original_records():
    original = result('https://docs.example.com/page?utm_source=search')
    rows = [result('http://[malformed'), result('http://localhost/x'), result('http://10.1.2.3/x'), result('https://user:password@example.com'),
            result('https://example.com.evil.org/x'), original, result('https://docs.example.com/page'),
            result('https://example.com/other')]
    chain = SearchProviderChain([SimpleNamespace(name='fake', search=lambda *_: rows)])
    found = chain.search('topic', domains=['*.EXAMPLE.COM'])
    assert [row.domain for row in found] == ['docs.example.com', 'example.com']
    assert [row.rank for row in found] == [1, 2] and original.domain == 'provider-supplied-wrong-domain'
    assert chain.last_errors == []


def test_invalid_provider_output_does_not_prevent_fallback():
    chain = SearchProviderChain([SimpleNamespace(name='bad', search=lambda *_: [None, result('file:///secret')]),
                                SimpleNamespace(name='good', search=lambda *_: [result('https://example.com')])])
    assert chain.search('topic')[0].provider == 'good'
    assert chain.last_errors == ['bad: no usable results']


def test_concurrent_searches_keep_their_own_errors():
    barrier = Barrier(2)
    def search(query, *_):
        barrier.wait(timeout=5)
        raise RuntimeError(query)
    chain = SearchProviderChain([SimpleNamespace(name='fake', search=search)])
    def run(query):
        chain.search(query)
        barrier.wait(timeout=5)
        return chain.last_errors
    with ThreadPoolExecutor(2) as pool:
        a, b = list(pool.map(run, ['alpha', 'beta']))
    assert a == ['fake: RuntimeError: alpha'] and b == ['fake: RuntimeError: beta']
    assert not chain.last_errors


@pytest.mark.parametrize('period', ['day', 'week', 'month', 'year'])
def test_brave_is_blocked_before_any_request(monkeypatch, period):
    def get(_url, **kwargs):
        pytest.fail('A paid search request was attempted')
    monkeypatch.setattr('app.research.search.httpx.get', get)
    with pytest.raises(PermissionError, match='ZERO-COST'):
        BraveSearch('fake-key').search('topic', recency=period)


def test_explicit_free_provider_does_not_contact_other_providers(monkeypatch):
    monkeypatch.delenv('AI_SEARCH_PROVIDER', raising=False)
    monkeypatch.setenv('BRAVE_SEARCH_API_KEY', 'fake-key')
    chain = build_search_provider({'search_provider': 'bing', 'paid_search_allowed': True})
    assert [provider.name for provider in chain.providers] == ['bing_rss']
