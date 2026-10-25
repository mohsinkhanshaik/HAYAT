"""Official public NSW/Lyon catalogs, guarded media and honest unknown freshness."""
import copy
from io import BytesIO

from PIL import Image
import pytest

from app.intelligence.feeds import BY_KEY, normalize
from app.intelligence.manager import IntelligenceManager
from app.intelligence.providers import camera_media_profile, camera_url
from app.intelligence.resources import resource_catalog
from tests.test_intelligence_resources import ExtendedFetcher, open_camera_payloads


@pytest.mark.parametrize('key,provider,country', [
    ('cameras_nsw', 'Transport for NSW', 'Australia'),
    ('cameras_lyon', 'Métropole de Lyon', 'France'),
])
def test_official_catalog_preserves_provenance_without_inventing_live_status(key, provider, country):
    feed = BY_KEY[key]
    assert not feed.key_env and feed.interval >= 3600
    rows, rejected = normalize(feed, open_camera_payloads()[key])
    row = next(iter(rows.values()))
    assert rejected == 0 and row['observed_at'] is None
    assert row['url'] == feed.documentation
    details = row['details']
    assert details['provider'] == provider and details['country'] == country
    assert details['catalog_credit'] and details['catalog_license']
    assert details['availability_verified'] is False
    assert details['availability_basis'] and 'capture' in details['availability_basis']
    assert camera_media_profile(details) == {
        'label': 'Refreshing still', 'stream': False, 'video_clip': False, 'image': True}
    assert not details['video_url'] and not details['page_url']
    if key == 'cameras_nsw':
        assert details['catalog_modified_at'] is None
        assert (row['latitude'], row['longitude']) == (-34.02977, 151.10533)
    else:
        assert details['catalog_modified_at'] == '2026-10-23T21:32:17+00:00'


@pytest.mark.parametrize('key,field', [('cameras_nsw', 'href'), ('cameras_lyon', 'url')])
def test_absent_or_unadmitted_snapshot_stays_metadata_only(key, field):
    payload = open_camera_payloads()[key]
    payload['features'][0]['properties'][field] = 'https://127.0.0.1/private.jpg'
    rows, rejected = normalize(BY_KEY[key], payload)
    details = next(iter(rows.values()))['details']
    assert rejected == 0 and details['available'] is False
    assert details['image_url'] == '' and not camera_media_profile(details)['image']


@pytest.mark.parametrize('key', ['cameras_nsw', 'cameras_lyon'])
@pytest.mark.parametrize('geometry', [
    {'type': 'LineString', 'coordinates': [[4, 45], [5, 45]]},
    {'type': 'Point', 'coordinates': [181, 45]},
    {'type': 'Point', 'coordinates': [4, float('nan')]},
    {'type': 'Point', 'coordinates': [True, 45]},
])
def test_invalid_point_never_becomes_a_fabricated_camera_position(key, geometry):
    payload = open_camera_payloads()[key]
    payload['features'][0]['geometry'] = geometry
    with pytest.raises(ValueError, match='All provider records failed'):
        normalize(BY_KEY[key], payload)


@pytest.mark.parametrize('key', ['cameras_nsw', 'cameras_lyon'])
def test_parser_rejects_error_payload_and_unbounded_catalog(key):
    with pytest.raises(ValueError, match='bounded catalog'):
        normalize(BY_KEY[key], {'error': 'rate limited'})
    record = open_camera_payloads()[key]['features'][0]
    with pytest.raises(ValueError, match='bounded catalog'):
        normalize(BY_KEY[key], {'features': [record] * 10001})


@pytest.mark.parametrize('key', ['cameras_nsw', 'cameras_lyon'])
def test_duplicate_same_identity_is_one_camera_and_malformed_identity_is_rejected(key):
    payload = open_camera_payloads()[key]
    record = payload['features'][0]
    payload['features'].append(copy.deepcopy(record))
    malformed = copy.deepcopy(record)
    if key == 'cameras_nsw':
        malformed['id'] = 'not-a-publisher-uuid'
    else:
        malformed['properties']['identifiant'] = True
    payload['features'].append(malformed)
    rows, rejected = normalize(BY_KEY[key], payload)
    assert len(rows) == 1 and rejected == 1


@pytest.mark.parametrize('key', ['cameras_nsw', 'cameras_lyon'])
def test_media_allowlist_refuses_other_paths_credentials_ports_and_cross_network(key):
    item = open_camera_payloads()[key]['features'][0]['properties']
    url = item.get('href') or item['url']
    assert camera_url(url, key) == url
    for bad in (url.replace('https://', 'http://'), url.replace('https://', 'https://user:pass@'),
                url.replace('.gov.au/', '.gov.au:444/').replace('.com/', '.com:444/'),
                url + '?redirect=http://localhost/private', url + '#fragment',
                url.replace('/cameras/', '/private/').replace('/files/rdata/', '/private/')):
        assert camera_url(bad, key) == ''
    assert camera_url(url, 'cameras_lyon' if key == 'cameras_nsw' else 'cameras_nsw') == ''
    assert not camera_url(url, key, video=True) and not camera_url(url, key, page=True)
    if key == 'cameras_nsw':
        extra = 'https://data.livetraffic.com/cameras/victoriapass_7.jpg'
        assert camera_url(extra, key) == extra


def test_manual_catalog_to_filtered_camera_and_preview_keeps_freshness_unknown(tmp_path):
    fetcher = ExtendedFetcher()
    manager = IntelligenceManager(tmp_path / 'cameras.db', fetcher, None, extended=True, enabled=False)
    try:
        assert not manager.store.states()  # Registering sources performs no network read.
        result = manager.refresh(['cameras_nsw', 'cameras_lyon'])
        assert result['updated'] == 2 and result['failed'] == 0
        assert set(manager.store.states()) == {'cameras_nsw', 'cameras_lyon'}
        nsw = manager.cameras(query='New South Wales', media='image')['cameras']
        assert len(nsw) == 1
        row = nsw[0]
        frame = manager.camera_frame(row['id'])
        with Image.open(frame['path']) as picture:
            assert picture.size == (32, 24)
        assert frame['image_url'] == row['details']['image_url']
        assert 'not verified capture time' in frame['basis']
        assert len(manager.cameras(query='Lyon', media='image')['cameras']) == 1
        assert not manager.cameras(media='stream')['cameras']
        for key in ('nsw', 'lyon'):
            resource = next(row for row in resource_catalog(category='cameras')['resources'] if row['id'] == key)
            assert resource['integration'] == 'connected' and resource['trust'] == 'untrusted_external_data'
    finally:
        manager.close()


@pytest.mark.parametrize('size,succeeds', [((5388, 3592), True), ((5400, 4000), False)])
def test_nsw_large_official_camera_keeps_bounded_bytes_and_pixels(tmp_path, size, succeeds):
    class BoundedFetcher(ExtendedFetcher):
        def fetch(self, url, max_bytes, *args, **kwargs):
            if camera_url(url, 'cameras_nsw'):
                self.preview_limit = max_bytes
                if len(self.image) > max_bytes:
                    raise ValueError('Image exceeds transfer byte limit')
            return super().fetch(url, max_bytes, *args, **kwargs)
    fetcher = BoundedFetcher()
    output = BytesIO()
    with Image.new('RGB', size) as picture:
        picture.save(output, format='JPEG')
    # Real image decode plus a response exceeding the former 2 MiB byte ceiling.
    fetcher.image = output.getvalue().ljust(2653308, b'\0')
    manager = IntelligenceManager(tmp_path / 'large.db', fetcher, None, extended=True, enabled=False)
    try:
        manager.refresh(['cameras_nsw'])
        row = manager.cameras(feed='cameras_nsw')['cameras'][0]
        if succeeds:
            frame = manager.camera_frame(row['id'])
            with Image.open(frame['path']) as picture:
                assert picture.size == size
            assert manager.camera_frame(row['id'])['cached'] is True
        else:
            with pytest.raises(ValueError, match='dimensions exceed preview limits'):
                manager.camera_frame(row['id'])
        assert fetcher.preview_limit == 4 * 1024 * 1024
    finally:
        manager.close()


def test_publisher_html_unavailable_page_is_not_saved_as_a_frame(tmp_path):
    from PIL import UnidentifiedImageError
    fetcher = ExtendedFetcher()
    fetcher.image = b'<html>The camera image is temporarily unavailable.</html>'
    manager = IntelligenceManager(tmp_path / 'unavailable.db', fetcher, None, extended=True, enabled=False)
    try:
        manager.refresh(['cameras_nsw'])
        row = manager.cameras(feed='cameras_nsw')['cameras'][0]
        with pytest.raises(UnidentifiedImageError):
            manager.camera_frame(row['id'])
        assert not (tmp_path / 'intelligence' / 'camera-previews').exists()
    finally:
        manager.close()
