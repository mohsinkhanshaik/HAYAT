"""A page publishing image bytes is evidence; an image link alone is not."""
from io import BytesIO
import hashlib
from types import SimpleNamespace

from PIL import Image
import pytest

from app.research import image_sources
from app.research.image_sources import check_source_page, exact_sources, image_thumbnail, inspect_image


def picture_bytes(color='blue', *, format='PNG', **options):
    stream = BytesIO()
    Image.new('RGB', (24, 16), color).save(stream, format=format, **options)
    return stream.getvalue()


@pytest.fixture
def source_file(tmp_path):
    target = tmp_path / 'local-source.png'
    target.write_bytes(picture_bytes())
    return target


class PageFetcher:
    """Only the fetch transport is replaced; parsing and comparisons are real."""
    def __init__(self, html, images=None):
        self.html = html.encode() if isinstance(html, str) else html
        self.images = images or {}
        self.calls = []
        self.validated = []

    def validate_url(self, url):
        # Exercise the actual no-DNS public/Tor URL syntax policy. PublicFetcher
        # adds direct-route DNS and per-redirect checks in production.
        from app.research.safety import validate_public_url
        self.validated.append(url)
        return validate_public_url(url, via_tor=True)

    def fetch(self, url, **options):
        self.calls.append((url, options))
        value = self.html if len(self.calls) == 1 else self.images[url]
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(content=value, final_url=url, retrieved_at='2026-10-24T12:00:00Z')


def test_page_verifies_bytes_pixels_and_different_images_with_citations(source_file):
    # BMP is a different file encoding with exactly the same decoded pixels.
    fetcher = PageFetcher('<img src="/exact.png"><img src="/pixels.bmp"><img src="/different.png">', {
        'https://publisher.example/exact.png': source_file.read_bytes(),
        'https://publisher.example/pixels.bmp': picture_bytes(format='BMP'),
        'https://publisher.example/different.png': picture_bytes('red'),
    })
    report = check_source_page(source_file, 'https://publisher.example/source', fetcher)
    assert report['outcome'] == 'matched'
    assert [row['match'] for row in report['matches']] == ['verified_exact_bytes', 'verified_exact_pixels']
    assert [row['status'] for row in report['candidates']] == [
        'verified_exact_bytes', 'verified_exact_pixels', 'different']
    for row in report['matches']:
        assert row['page'] == report['page']['url']
        assert row['page_sha256'] == hashlib.sha256(fetcher.html).hexdigest()
        assert row['retrieved_at'] == '2026-10-24T12:00:00Z'
        assert row['sha256'] == hashlib.sha256(fetcher.images[row['image_url']]).hexdigest()
    assert all(set(options) == {'max_bytes', 'accept'} for _, options in fetcher.calls)
    assert 'not uploaded' in report['privacy']


def test_declared_sources_support_lazy_srcset_meta_and_deduplicate_fragments(source_file):
    html = ('<base href="http://127.0.0.1/">'
            '<meta property="og:image" content="/a.png#one">'
            '<meta name="twitter:image" content="/a.png#two">'
            '<img data-src="/b.png" data-original="/c.png" srcset="/d.png 1x, /e.png 2x">'
            '<source srcset="/f.png 800w"><link rel="preload" as="image" href="/g.png">'
            '<script>location="/not-followed"</script><a href="/another-page">next</a>')
    urls = ['https://publisher.example/' + letter + '.png' for letter in 'abcdefg']
    fetcher = PageFetcher(html, dict.fromkeys(urls, source_file.read_bytes()))
    report = check_source_page(source_file, 'https://publisher.example/source', fetcher)
    assert [url for url, _ in fetcher.calls[1:]] == urls
    assert len(report['matches']) == 7
    assert report['coverage']['candidates_found'] == 7
    assert not report['coverage']['limited']


@pytest.mark.parametrize('url', ['http://127.0.0.1/private', 'file:///private/file',
                                 'https://user:password@publisher.example/page', 'http://localhost/'])
def test_private_or_credential_source_page_is_rejected_before_retrieval(source_file, url):
    fetcher = PageFetcher('')
    with pytest.raises(ValueError):
        check_source_page(source_file, url, fetcher)
    assert not fetcher.calls


def test_private_image_links_are_not_fetched_and_do_not_become_matches(source_file):
    fetcher = PageFetcher('<img src="http://127.0.0.1/secret"><img src="file:///secret">'
                          '<img src="https://user:password@publisher.example/secret">'
                          '<img src="data:image/png;base64,abcd"><img src="/valid.png">', {
                              'https://publisher.example/valid.png': source_file.read_bytes()})
    report = check_source_page(source_file, 'https://publisher.example/source', fetcher)
    assert len(fetcher.calls) == 2
    assert report['candidates'][0]['status'] == 'failed'
    assert len(report['matches']) == 1


def test_missing_or_javascript_images_remain_unknown(source_file):
    report = check_source_page(source_file, 'https://publisher.example/source', PageFetcher(
        '<script>renderAnImage()</script><img src="data:image/png;base64,abcd">'))
    assert report['outcome'] == 'unknown'
    assert not report['matches'] and report['coverage']['candidates_found'] == 0
    assert report['page']['sha256']


def test_page_failure_keeps_local_hash_and_does_not_invent_source(source_file):
    fetcher = PageFetcher('')
    fetcher.html = TimeoutError('private exception detail must not be echoed')
    report = check_source_page(source_file, 'https://publisher.example/source', fetcher)
    assert report['outcome'] == 'failed'
    assert report['page'] is None and report['matches'] == []
    assert report['metadata']['sha256'] == hashlib.sha256(source_file.read_bytes()).hexdigest()
    assert report['errors'] == ['TimeoutError: source page could not be verified']


def test_failed_and_invalid_image_downloads_are_unverified_not_different(source_file):
    fetcher = PageFetcher('<img src="/bad.png"><img src="/invalid.png">', {
        'https://publisher.example/bad.png': TimeoutError('provider failed'),
        'https://publisher.example/invalid.png': b'This is not an image',
    })
    report = check_source_page(source_file, 'https://publisher.example/source', fetcher)
    assert not report['matches'] and report['outcome'] == 'unknown'
    assert all(row['status'] == 'failed' for row in report['candidates'])
    assert len(report['errors']) == 2


def test_candidate_and_byte_budgets_are_real(source_file, monkeypatch):
    fetcher = PageFetcher(''.join(f'<img src="/{index}.png">' for index in range(20)), {
        'https://publisher.example/0.png': source_file.read_bytes(),
        'https://publisher.example/1.png': source_file.read_bytes(),
    })
    report = check_source_page(source_file, 'https://publisher.example/source', fetcher, max_candidates=2)
    assert report['coverage']['limited'] and report['coverage']['candidates_found'] == 20
    assert report['coverage']['candidates_attempted'] == 2 and len(fetcher.calls) == 3
    monkeypatch.setattr(image_sources, 'MAX_PAGE_BYTES', 2)
    report = check_source_page(source_file, 'https://publisher.example/source', PageFetcher('<img>'))
    assert report['outcome'] == 'failed' and not report['matches']


@pytest.mark.parametrize('count', [0, 13, -1, True, 1.5, '2'])
def test_invalid_candidate_budget_is_rejected_before_network(source_file, count):
    fetcher = PageFetcher('')
    with pytest.raises(ValueError):
        check_source_page(source_file, 'https://publisher.example/source', fetcher, max_candidates=count)
    assert not fetcher.calls


def test_interlock_checked_before_network_and_between_images(source_file):
    fetcher = PageFetcher('<img src="/one.png"><img src="/two.png">', {
        'https://publisher.example/one.png': source_file.read_bytes(),
        'https://publisher.example/two.png': source_file.read_bytes(),
    })
    def check():
        if len(fetcher.calls) >= 2:
            raise PermissionError('Emergency stop')
    with pytest.raises(PermissionError, match='Emergency stop'):
        check_source_page(source_file, 'https://publisher.example/source', fetcher, check=check)
    assert len(fetcher.calls) == 2


def test_pixel_and_dimension_limits_reject_before_decode_or_thumbnail(source_file, monkeypatch):
    monkeypatch.setattr(image_sources, 'MAX_IMAGE_PIXELS', 24 * 16 - 1)
    monkeypatch.setattr(Image.Image, 'load', lambda _self, *a, **k: pytest.fail('Decoded oversized image'))
    for action in (inspect_image, image_thumbnail):
        with pytest.raises(ValueError, match='20 million pixels'):
            action(source_file)


def test_byte_limit_checked_before_open(source_file, monkeypatch):
    monkeypatch.setattr(image_sources, 'MAX_IMAGE_BYTES', 5)
    monkeypatch.setattr(Image, 'open', lambda *_a, **_k: pytest.fail('Opened oversized image'))
    with pytest.raises(ValueError, match='20 MiB'):
        inspect_image(source_file)


def test_thumbnail_is_small_and_preserves_orientation(tmp_path):
    path = tmp_path / 'oriented.jpg'
    image = Image.new('RGB', (400, 200), 'green')
    exif = Image.Exif()
    exif[274] = 6
    image.save(path, exif=exif)
    result = image_thumbnail(path)
    assert result.width < result.height
    assert result.width <= 140 and result.height <= 80
    assert result.mode == 'RGB'


def test_animation_frame_is_not_reported_as_exact_pixel_match(source_file):
    stream = BytesIO()
    Image.new('RGB', (24, 16), 'blue').save(stream, format='GIF', save_all=True,
        append_images=[Image.new('RGB', (24, 16), 'red')], duration=100)
    fetcher = PageFetcher('<img src="/animated.gif">', {
        'https://publisher.example/animated.gif': stream.getvalue()})
    report = check_source_page(source_file, 'https://publisher.example/source', fetcher)
    assert report['candidates'][0]['status'] == 'different'
    assert not report['matches']


def test_missing_html_attribute_values_do_not_abort_valid_image_discovery(source_file):
    fetcher = PageFetcher('<meta property><link rel><img srcset><img src="/good.png">', {
        'https://publisher.example/good.png': source_file.read_bytes()})
    report = check_source_page(source_file, 'https://publisher.example/source', fetcher)
    assert len(report['matches']) == 1 and not report['errors']


def test_page_candidate_extraction_has_a_bound_and_reports_truncation(source_file):
    fetcher = PageFetcher(''.join(f'<img src="/{index}.png">' for index in range(1000)), {
        'https://publisher.example/0.png': source_file.read_bytes()})
    report = check_source_page(source_file, 'https://publisher.example/source', fetcher, max_candidates=1)
    assert report['coverage']['candidates_found'] == 32
    assert report['coverage']['limited'] and len(fetcher.calls) == 2


def test_a_changed_privacy_route_stops_before_any_image_request(source_file, monkeypatch):
    from app.privacy_masks import ROUTE
    route = [None, 1]
    monkeypatch.setattr(ROUTE, 'snapshot', lambda: tuple(route))
    fetcher = PageFetcher('<img src="/image.png">')
    original = fetcher.fetch
    def fetch(*args, **kwargs):
        result = original(*args, **kwargs)
        route[1] += 1
        return result
    fetcher.fetch = fetch
    report = check_source_page(source_file, 'https://publisher.example/source', fetcher)
    assert report['outcome'] == 'failed' and not report['matches']
    assert len(fetcher.calls) == 1


def test_remote_oversized_pixels_cannot_be_classified_as_different_or_match(source_file):
    stream = BytesIO()
    Image.new('RGB', (20_000, 1)).save(stream, format='PNG')
    fetcher = PageFetcher('<img src="/too-wide.png">', {
        'https://publisher.example/too-wide.png': stream.getvalue()})
    report = check_source_page(source_file, 'https://publisher.example/source', fetcher)
    assert report['outcome'] == 'unknown' and report['candidates'][0]['status'] == 'failed'
    assert not report['matches']


def test_exact_commons_lookup_checks_stop_before_api_request(source_file):
    def stopped():
        raise PermissionError('Emergency stop')
    fetcher = SimpleNamespace(get_json=lambda _url: pytest.fail('Network after stop'))
    with pytest.raises(PermissionError, match='Emergency stop'):
        exact_sources(source_file, fetcher, check=stopped)


def test_exact_commons_lookup_does_not_switch_routes_between_api_and_image(source_file, monkeypatch):
    from app.privacy_masks import ROUTE
    route = [None, 1]
    monkeypatch.setattr(ROUTE, 'snapshot', lambda: tuple(route))
    def listing(_url):
        route[1] += 1
        return {'query': {'allimages': [{'url': 'https://upload.wikimedia.org/image.png',
            'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Image.png'}]}}
    fetcher = SimpleNamespace(get_json=listing, fetch=lambda *_a, **_k: pytest.fail('Mixed privacy routes'))
    with pytest.raises(PermissionError, match='Privacy route changed'):
        exact_sources(source_file, fetcher)


def test_data_url_srcset_never_turns_embedded_bytes_into_a_relative_request(source_file):
    fetcher = PageFetcher('<img srcset="data:image/png;base64,abcd 1x, /not-checked.png 2x">'
                          '<img src="/valid.png" srcset="DATA:image/png;base64,efgh 1x">', {
        'https://publisher.example/valid.png': source_file.read_bytes()})
    report = check_source_page(source_file, 'https://publisher.example/source', fetcher)
    assert [url for url, _ in fetcher.calls] == [
        'https://publisher.example/source', 'https://publisher.example/valid.png']
    assert len(report['matches']) == 1 and report['coverage']['candidates_found'] == 1
