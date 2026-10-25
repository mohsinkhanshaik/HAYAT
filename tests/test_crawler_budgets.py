from types import SimpleNamespace

import pytest

from app.research.crawler import BoundedCrawler
from app.research.extractor import ContentExtractor
from tests.test_research import resource


def test_failed_requests_consume_crawl_budget_and_duplicates_are_not_queued():
    calls = []
    def fetch(url, _max_bytes):
        calls.append(url)
        if len(calls) > 1:
            raise RuntimeError('unavailable')
        links = ''.join(f'<a href="/p{i}">Page</a>' for i in range(10)) + '<a href="/p0?utm_source=x">Duplicate</a>'
        return resource('https://example.com/', ('<html><body>' + links + '</body></html>').encode())
    sources, errors = BoundedCrawler(SimpleNamespace(fetch=fetch), ContentExtractor()).crawl(
        'https://example.com/', 'page', max_pages=3)
    assert len(calls) == 3 and len(sources) == 1
    assert any('budget' in error for error in errors)


def test_seed_canonical_redirect_is_allowed_but_followup_external_redirect_is_not_retained():
    calls = []
    def fetch(url, _max_bytes):
        calls.append(url)
        if len(calls) == 1:
            return resource('https://www.example.com/', b'<html><body><a href="/next">Next</a></body></html>')
        return resource('https://outside.example.org/', b'<html><body>Outside</body></html>')
    sources, errors = BoundedCrawler(SimpleNamespace(fetch=fetch), ContentExtractor()).crawl('https://example.com/', 'next')
    assert calls == ['https://example.com/', 'https://www.example.com/next']
    assert len(sources) == 1 and any('outside the seed domain' in error for error in errors)


def test_oversize_response_is_not_retained_even_when_fetcher_ignores_limit():
    crawler = BoundedCrawler(SimpleNamespace(fetch=lambda *_: resource(content=b'x' * 50)), ContentExtractor())
    sources, errors = crawler.crawl('https://example.com', 'topic', max_bytes=10)
    assert not sources and any('byte budget' in error for error in errors)


@pytest.mark.parametrize('kwargs', [{'max_pages': 0}, {'max_depth': 4}, {'max_seconds': float('nan')}, {'max_bytes': True}])
def test_invalid_budgets_are_rejected_before_fetch(kwargs):
    with pytest.raises(ValueError):
        BoundedCrawler(None, None).crawl('https://example.com', 'topic', **kwargs)
