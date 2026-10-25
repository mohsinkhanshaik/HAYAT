"""Connection outages are reported as access failures, never search findings."""
from __future__ import annotations

import errno
from types import SimpleNamespace

import httpx
import pytest

from app.privacy_masks import ROUTE
from app.research.jobs import ResearchJob, ResearchJobs
from app.research.network_diagnostics import connection_diagnostic, failed_search_guidance
from app.research.search import BingRssSearch, DuckDuckGoHtmlSearch, SearchProviderChain, _search_get
from app.research.models import SearchResult


@pytest.mark.parametrize('provider', [DuckDuckGoHtmlSearch, BingRssSearch])
def test_direct_refusal_keeps_exception_class_and_observed_route(monkeypatch, provider):
    ROUTE.set(None)
    original = httpx.ConnectError('[WinError 10061] actively refused')
    def reject(*args, **kwargs):
        assert kwargs['trust_env'] is False
        raise original
    monkeypatch.setattr('app.research.search.httpx.get', reject)
    with pytest.raises(httpx.ConnectError, match=r'connection_refused; route=direct') as caught:
        provider().search('NIST AI risk management')
    assert caught.value.__cause__ is original
    assert 'Tor was not selected' in str(caught.value)


def test_tor_refusal_never_tries_direct_or_guesses_from_later_route(monkeypatch):
    ROUTE.set('socks5://127.0.0.1:19999')
    calls = []
    def reject(self, url, **kwargs):
        calls.append(kwargs)
        ROUTE.set(None)  # UI changed its selection while the masked request failed
        raise httpx.ConnectError('Connection refused')
    monkeypatch.setattr('app.research.fetcher.PublicFetcher.fetch', reject)
    monkeypatch.setattr('app.research.search.httpx.get', lambda *a, **k: pytest.fail('direct fallback'))
    with pytest.raises(httpx.ConnectError, match=r'connection_refused; route=tor') as caught:
        _search_get('https://www.bing.com/search')
    assert calls == [{'request_headers': {}, 'require_tor': True}]
    assert 'No direct-network fallback was used' in str(caught.value)


def test_explicit_tor_without_a_route_fails_closed_without_network(monkeypatch):
    ROUTE.set(None)
    monkeypatch.setattr('app.research.search.httpx.get', lambda *a, **k: pytest.fail('direct request'))
    with pytest.raises(PermissionError, match='requires Tor'):
        _search_get('https://www.bing.com/search', require_tor=True)


def test_provider_fallback_stops_when_tor_route_changes_after_a_failure(monkeypatch):
    ROUTE.set('socks5://127.0.0.1:19999')
    def reject(*args):
        ROUTE.set(None)
        raise httpx.ConnectError('Tor connection refused')
    first = SimpleNamespace(name='masked', search=reject)
    second = SimpleNamespace(name='fallback', search=lambda *a: pytest.fail('route changed before fallback'))
    chain = SearchProviderChain([first, second])
    assert chain.search('NIST AI') == []
    assert 'No provider fallback was used after the change' in chain.last_errors[-1]


def test_successful_provider_results_are_discarded_after_route_change():
    ROUTE.set('socks5://127.0.0.1:19999')
    def results(*args):
        ROUTE.set(None)
        return [SearchResult('NIST AI', 'https://www.nist.gov/ai', '', 'www.nist.gov')]
    chain = SearchProviderChain([SimpleNamespace(name='fake', search=results)])
    assert chain.search('NIST AI') == []
    assert 'retrieved results were discarded' in chain.last_errors[-1]


def test_direct_response_is_discarded_after_route_changes(monkeypatch):
    ROUTE.set(None)
    response = httpx.Response(200, text='result')
    def get(*args, **kwargs):
        ROUTE.set('socks5://127.0.0.1:19999')
        return response
    monkeypatch.setattr('app.research.search.httpx.get', get)
    with pytest.raises(PermissionError, match='Privacy route changed'):
        _search_get('https://www.bing.com/search')
    assert response.is_closed


@pytest.mark.parametrize('error, code', [
    (httpx.ConnectTimeout('slow connection'), 'network_timeout'),
    (httpx.ReadTimeout('slow body'), 'network_timeout'),
    (httpx.ConnectError('[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed'), 'tls_verification_failed'),
    (httpx.ConnectError('[Errno 11001] getaddrinfo failed'), 'dns_failure'),
    (httpx.ProxyError('proxy handshake failed'), 'network_connection_failed'),
])
def test_network_failures_have_distinct_actionable_causes(error, code):
    text = connection_diagnostic(error, via_tor=False)
    assert f'[{code}; route=direct]' in text
    assert failed_search_guidance(text)
    if code == 'tls_verification_failed':
        assert 'verification remains enabled' in text


def test_nested_os_refusal_without_windows_message_is_recognized():
    error = httpx.ConnectError('transport failure')
    error.__cause__ = ConnectionRefusedError(errno.ECONNREFUSED, 'localized message')
    assert '[connection_refused;' in connection_diagnostic(error, via_tor=True)


def test_saved_legacy_10061_is_an_outage_not_a_bad_query_or_missing_result():
    job = ResearchJob('id', 'NIST AI risk management', '', 'normal', 'now', status='failed',
                      error='duckduckgo_html: ConnectError: [WinError 10061] No connection could be made because the target machine actively refused it')
    text = ResearchJobs(lambda: None).result_text(job)
    assert 'network connection was refused' in text
    assert 'retry the same search' in text
    assert 'Tell me more' not in text and 'Tor connection' not in text
    assert 'whether matching information exists' in text


def test_failed_search_preserves_provider_errors_and_does_not_claim_findings(monkeypatch):
    ROUTE.set(None)
    def reject(*args, **kwargs):
        raise httpx.ConnectError('Connection refused')
    monkeypatch.setattr('app.research.search.httpx.get', reject)
    chain = SearchProviderChain([DuckDuckGoHtmlSearch(), BingRssSearch()])
    assert chain.search('NIST AI risk management') == []
    assert len(chain.last_errors) == 2
    assert all('connection_refused; route=direct' in error for error in chain.last_errors)
    job = ResearchJob('id', 'NIST AI', '', 'normal', 'now', status='failed', error='; '.join(chain.last_errors))
    text = ResearchJobs(lambda: None).result_text(job)
    assert 'duckduckgo_html' in text and 'bing_rss' in text
    assert 'finished looking up' not in text


@pytest.mark.parametrize('mode', ['onion', 'tor_web'])
def test_protected_job_reports_no_direct_fallback_for_legacy_outage(mode):
    job = ResearchJob('id', 'NIST AI', '', 'normal', 'now', status='failed',
                      error='ConnectError: [WinError 10061] actively refused', **{mode: True})
    text = ResearchJobs(lambda: None).result_text(job)
    assert 'Tor connection' in text and 'No direct-network fallback' in text


def test_partial_evidence_remains_visible_despite_other_connection_failures():
    job = ResearchJob('id', 'NIST AI', '', 'normal', 'now', status='failed', sources=1,
                      error='ConnectError: [WinError 10061] actively refused', answer='Evidence insufficient.',
                      result=SimpleNamespace(sources=[SimpleNamespace(final_url='https://www.nist.gov/ai', retrieved_at='now')]))
    text = ResearchJobs(lambda: None).result_text(job)
    assert 'I read 1 source page(s)' in text and 'https://www.nist.gov/ai' in text


def test_genuine_no_usable_results_keeps_evidence_failure_not_network_guess():
    assert failed_search_guidance('duckduckgo_html: no usable results') == ''
    job = ResearchJob('id', 'NIST AI', '', 'normal', 'now', status='failed', error='no usable results')
    assert 'Tell me more' in ResearchJobs(lambda: None).result_text(job)
