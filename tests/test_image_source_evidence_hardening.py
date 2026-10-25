"""Real source parsing/comparisons with bounded, explicitly replaced transport."""
from io import BytesIO
import hashlib
import json
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest

from app.privacy_masks import ROUTE
from app.research import image_sources
from app.research.fetcher import PublicFetcher
from app.research.image_sources import check_source_page, exact_sources
from app.research.safety import validate_public_url


@pytest.fixture
def source_file(tmp_path):
    stream = BytesIO()
    Image.new('RGB', (24, 16), 'blue').save(stream, format='PNG')
    path = tmp_path / 'synthetic-source.png'
    path.write_bytes(stream.getvalue())
    return path


class PageTransport:
    def __init__(self, html, images=None, headers=None):
        self.html = html.encode() if isinstance(html, str) else html
        self.images = images or {}
        self.headers = headers or {}
        self.calls = []

    def validate_url(self, url):
        return validate_public_url(url, via_tor=True)

    def fetch(self, url, **options):
        self.calls.append((url, options))
        value = self.html if len(self.calls) == 1 else self.images[url]
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(content=value, final_url=url, headers=self.headers,
                               retrieved_at='2026-10-24T23:30:00Z')


def test_safe_base_lazy_srcset_rel_tokens_and_declarations(source_file):
    html = ('<base href="https://cdn.publisher.example/images/">'
            '<base href="https://ignored.example/">'
            '<img data-lazy-src="a.png" data-srcset="b.png 400w, c.png 2x">'
            '<link rel="alternate preload" as="image" href="a.png#duplicate" '
            'imagesrcset="d.png 1x, e.png 2x">')
    urls = ['https://cdn.publisher.example/images/' + name + '.png' for name in 'abcde']
    fetcher = PageTransport(html, dict.fromkeys(urls, source_file.read_bytes()))
    result = check_source_page(source_file, 'https://publisher.example/article', fetcher)
    assert [url for url, _ in fetcher.calls[1:]] == urls
    assert len(result['matches']) == 5
    first = result['matches'][0]
    assert first['declarations'] == ['img:data-lazy-src', 'link:href']
    assert first['page_retrieved_at'] == result['page']['retrieved_at']
    assert first['page_sha256'] == hashlib.sha256(html.encode()).hexdigest()
    assert result['coverage']['candidates_verified'] == 5


@pytest.mark.parametrize('base', ['http://localhost/private/', 'http://127.0.0.1/',
                                 'https://user:secret@publisher.example/', 'javascript:alert(1)',
                                 'file:///C:/private/', 'https://[broken/'])
def test_invalid_declared_base_does_not_override_public_page(source_file, base):
    fetcher = PageTransport(f'<base href="{base}"><img src="/copy.png">', {
        'https://publisher.example/copy.png': source_file.read_bytes()})
    result = check_source_page(source_file, 'https://publisher.example/article', fetcher)
    assert len(result['matches']) == 1
    assert fetcher.calls[1][0] == 'https://publisher.example/copy.png'


def test_srcset_preserves_url_commas_and_rejects_invalid_descriptors(source_file):
    first = 'https://publisher.example/resize?crop=2,3&format=png'
    second = 'https://publisher.example/other.png'
    fetcher = PageTransport(
        '<img srcset="/resize?crop=2,3&amp;format=png 1x, /other.png 2x, '
        '/zero.png 0x, /negative.png -2x, /invalid.png 2x 3x, /inf.png 1e999x">',
        {first: source_file.read_bytes(), second: source_file.read_bytes()})
    result = check_source_page(source_file, 'https://publisher.example/article', fetcher)
    assert [url for url, _ in fetcher.calls[1:]] == [first, second]
    assert len(result['matches']) == 2


@pytest.mark.parametrize('headers,prefix,encoding', [
    ({'content-type': 'text/html; charset=windows-1252'}, '', 'cp1252'),
    ({'Content-Type': 'text/html; charset="ISO-8859-1"'}, '', 'cp1252'),
    ({}, '<meta charset="windows-1252">', 'cp1252'),
    ({}, '<meta http-equiv="content-type" content="text/html; charset=windows-1252">', 'cp1252'),
    ({'content-type': 'text/html; charset=windows-1252'}, '', 'utf-8-sig'),
    ({}, '', 'utf-16'),
])
def test_declared_charset_and_bom_preserve_unicode_source_url(source_file, headers, prefix, encoding):
    image_url = 'https://publisher.example/caf\u00e9.png'
    html = (prefix + '<img src="/caf\u00e9.png">').encode(encoding)
    fetcher = PageTransport(html, {image_url: source_file.read_bytes()}, headers)
    result = check_source_page(source_file, 'https://publisher.example/article', fetcher)
    assert len(result['matches']) == 1
    assert result['matches'][0]['image_url'] == image_url
    assert result['page']['sha256'] == hashlib.sha256(html).hexdigest()


@pytest.mark.parametrize('charset', ['invalid-codec', 'base64_codec', 'utf-32'])
def test_invalid_nontext_or_conflicting_charset_falls_back_without_fabricated_url(source_file, charset):
    fetcher = PageTransport('<img src="/copy.png">', {
        'https://publisher.example/copy.png': source_file.read_bytes()}, {'content-type': 'text/html; charset=' + charset})
    result = check_source_page(source_file, 'https://publisher.example/article', fetcher)
    assert len(result['matches']) == 1


def test_source_report_exposes_real_shared_byte_budget_and_stops(source_file, monkeypatch):
    html = '<img src="/one.png"><img src="/two.png">'
    monkeypatch.setattr(image_sources, 'MAX_SOURCE_BYTES', len(html.encode()) + len(source_file.read_bytes()))
    fetcher = PageTransport(html, {'https://publisher.example/one.png': source_file.read_bytes()})
    result = check_source_page(source_file, 'https://publisher.example/article', fetcher)
    assert len(result['matches']) == 1 and len(fetcher.calls) == 2
    assert result['coverage']['limited']
    assert result['coverage']['candidates_attempted'] == 1
    assert result['coverage']['bytes_received'] == result['coverage']['byte_limit']


def test_stop_during_failed_page_fetch_propagates_instead_of_provider_failure(source_file):
    stopped = [False]
    def fetch(*_args, **_options):
        stopped[0] = True
        raise TimeoutError('secret detail')
    def check():
        if stopped[0]:
            raise PermissionError('Emergency stop')
    fetcher = SimpleNamespace(validate_url=lambda url: url, fetch=fetch)
    with pytest.raises(PermissionError, match='Emergency stop'):
        check_source_page(source_file, 'https://publisher.example/article', fetcher, check=check)


def test_route_change_after_a_match_cannot_return_stale_success(source_file, monkeypatch):
    route = [None, 40]
    monkeypatch.setattr(ROUTE, 'snapshot', lambda: tuple(route))
    fetcher = PageTransport('<img src="/copy.png">', {
        'https://publisher.example/copy.png': source_file.read_bytes()})
    original = fetcher.fetch
    def fetch(*args, **options):
        value = original(*args, **options)
        if len(fetcher.calls) == 2:
            route[1] += 1
        return value
    fetcher.fetch = fetch
    with pytest.raises(PermissionError, match='Privacy route changed'):
        check_source_page(source_file, 'https://publisher.example/article', fetcher)


@pytest.mark.parametrize('payload', [{'query': {'allimages': []}}, {'error': {'code': 'ratelimited'}}, None])
def test_route_change_after_empty_or_failed_index_cannot_return_stale_result(source_file, monkeypatch, payload):
    route = ['socks5h://127.0.0.1:9999', 41]
    monkeypatch.setattr(ROUTE, 'snapshot', lambda: tuple(route))
    def listing(_url):
        route[1] += 1
        return payload
    with pytest.raises(PermissionError, match='Privacy route changed'):
        exact_sources(source_file, SimpleNamespace(get_json=listing))


@pytest.mark.parametrize('bad', ['https://[broken', 'https://upload.wikimedia.org:bad/a.png',
                                'https://user:secret@upload.wikimedia.org/a.png',
                                'https://upload.wikimedia.org:444/a.png',
                                'https://upload.wikimedia.org/a\n.png', None])
def test_bad_commons_candidate_does_not_abort_later_valid_evidence(source_file, bad):
    good = {'url': 'https://upload.wikimedia.org/good.png',
            'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Good.png'}
    rows = [{**good, 'url': bad}, good]
    fetcher = SimpleNamespace(get_json=lambda _url: {'query': {'allimages': rows}},
                              fetch=lambda url, **_options: SimpleNamespace(
                                  content=source_file.read_bytes(), final_url=url, retrieved_at='2026-10-24T23:30:00Z'))
    result = exact_sources(source_file, fetcher)
    assert result['outcome'] == 'matched'
    assert len(result['matches']) == 1 and result['errors'] == ['Invalid publisher candidate omitted.']
    assert result['matches'][0]['page_verified'] is False
    assert result['index'] is None  # injected decoded JSON has no claimed response hash


def test_commons_budget_and_continuation_report_partial_coverage(source_file, monkeypatch):
    monkeypatch.setattr(image_sources, 'MAX_SOURCE_BYTES', len(source_file.read_bytes()))
    rows = [{'url': 'https://upload.wikimedia.org/' + name + '.png',
             'descriptionurl': 'https://commons.wikimedia.org/wiki/File:' + name + '.png'} for name in ['One', 'Two']]
    calls = []
    def fetch(url, **options):
        calls.append((url, options))
        return SimpleNamespace(content=source_file.read_bytes(), final_url=url, retrieved_at='2026-10-24T23:30:00Z')
    result = exact_sources(source_file, SimpleNamespace(
        get_json=lambda _url: {'continue': {'aicontinue': 'next'}, 'query': {'allimages': rows}}, fetch=fetch))
    assert len(result['matches']) == 1 and len(calls) == 1
    assert result['coverage']['candidates_found'] == 2 and result['coverage']['candidates_attempted'] == 1
    assert result['coverage']['limited']
    assert calls[0][1]['max_bytes'] == len(source_file.read_bytes())


@pytest.mark.parametrize('final_url', ['http://127.0.0.1/leak', 'https://attacker.example/copy.png',
                                      'https://upload.wikimedia.org:444/copy.png'])
def test_commons_match_cannot_claim_a_redirected_nonpublisher_file(source_file, final_url):
    row = {'url': 'https://upload.wikimedia.org/copy.png',
           'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Copy.png'}
    result = exact_sources(source_file, SimpleNamespace(
        get_json=lambda _url: {'query': {'allimages': [row]}},
        fetch=lambda *_args, **_options: SimpleNamespace(
            content=source_file.read_bytes(), final_url=final_url, retrieved_at='2026-10-24T23:30:00Z')))
    assert not result['matches'] and result['coverage']['candidates_failed'] == 1


class NoDnsFetcher(PublicFetcher):
    def validate_url(self, url):
        return validate_public_url(url, via_tor=True)


def test_actual_fetcher_keeps_index_and_file_citations_without_upload(source_file, monkeypatch):
    monkeypatch.setattr(ROUTE, 'snapshot', lambda: (None, 45))
    row = {'url': 'https://upload.wikimedia.org/copy.png',
           'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Copy.png'}
    payload = json.dumps({'query': {'allimages': [row]}}).encode()
    requests = []
    def respond(request):
        requests.append(request)
        index = request.url.host == 'commons.wikimedia.org'
        return httpx.Response(200, content=payload if index else source_file.read_bytes(),
                              headers={'content-type': 'application/json' if index else 'image/png'})
    fetcher = NoDnsFetcher('HAYAT-TestImageSources/1.0', delay=0, respect_robots=False)
    fetcher.client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
    try:
        result = exact_sources(source_file, fetcher)
    finally:
        fetcher.close()
    assert len(result['matches']) == 1
    assert all(request.method == 'GET' and not request.content for request in requests)
    assert source_file.read_bytes() not in b''.join(request.content for request in requests)
    assert result['index']['sha256'] == hashlib.sha256(payload).hexdigest()
    assert result['index']['retrieved_at'] and result['index']['url'].startswith('https://commons.wikimedia.org/w/api.php?')
    assert result['matches'][0]['index'] == result['index']
    assert result['route'] == {'mode': 'direct', 'epoch': 45}
    assert 'dark-web search' in result['scope']


@pytest.mark.parametrize('operation', ['page', 'index'])
def test_tor_disconnect_at_fetch_boundary_cannot_open_direct_request(source_file, monkeypatch, operation):
    route = ['socks5h://127.0.0.1:9999', 46]
    monkeypatch.setattr(ROUTE, 'snapshot', lambda: tuple(route))
    class DisconnectingFetcher(NoDnsFetcher):
        def fetch(self, *args, **options):
            assert options['require_tor'] is True
            route[:] = [None, 47]
            return super().fetch(*args, **options)
    fetcher = DisconnectingFetcher('HAYAT-TestImageSources/1.0', delay=0, respect_robots=False)
    fetcher.client = httpx.Client(transport=httpx.MockTransport(
        lambda _request: pytest.fail('Direct HTTP request after Tor disconnect')), trust_env=False)
    try:
        if operation == 'page':
            result = check_source_page(source_file, 'https://publisher.example/article', fetcher)
            assert result['outcome'] == 'failed' and not result['matches']
            assert result['route'] == {'mode': 'tor', 'epoch': 46}
        else:
            with pytest.raises(PermissionError, match='Privacy route changed'):
                exact_sources(source_file, fetcher)
    finally:
        fetcher.close()


@pytest.mark.parametrize('operation', ['page', 'index'])
def test_http_denial_status_is_visible_without_exception_secrets(source_file, operation):
    request = httpx.Request('GET', 'https://publisher.example/private?token=secret')
    error = httpx.HTTPStatusError('secret local C:/private/path', request=request,
                                 response=httpx.Response(403, request=request))
    def fail(*_args, **_options):
        raise error
    fetcher = SimpleNamespace(validate_url=lambda url: url, fetch=fail, get_json=fail)
    result = (check_source_page(source_file, 'https://publisher.example/article', fetcher) if operation == 'page'
              else exact_sources(source_file, fetcher))
    assert result['outcome'] == 'failed' and not result['matches']
    assert 'HTTP 403' in result['errors'][0]
    assert all('secret' not in detail and 'C:/private' not in detail for detail in result['errors'])
