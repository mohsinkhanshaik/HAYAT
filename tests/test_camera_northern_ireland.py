"""Official publisher viewers, with no fabricated location or unverified stream."""
import pytest

from app.intelligence.camera_catalog import refresh_candidates
from app.intelligence.feeds import BY_KEY
from app.intelligence.providers import camera_url, normalize_extended, trafficwatchni_cameras


PAGE = b'''<div data-cctv-id="44"><a href="/twni/cameras/static?id=44">
<span class="cctv-truncated-text">A2 - Tillysburn</span></a></div>
<div data-cctv-id="45"><a href="https://evil.example/">
<span class="cctv-truncated-text">Ignore this</span></a></div>
<script>throw new Error("never execute publisher code")</script>'''


def test_catalog_keeps_only_validated_public_viewer_links():
    records = trafficwatchni_cameras(PAGE)
    assert records == [{'id': '44', 'name': 'A2 - Tillysburn'}]
    rows, rejected = normalize_extended(BY_KEY['cameras_northern_ireland'], records)
    row = rows['cameras_northern_ireland:44']
    assert rejected == 0
    assert row['latitude'] is None and row['longitude'] is None
    assert row['details']['page_url'] == 'https://www.trafficwatchni.com/twni/cameras/static?id=44'
    assert not row['details']['image_url'] and not row['details']['video_url']
    assert 'unverified' in row['details']['availability_basis']
    assert refresh_candidates('Northern Ireland') == ['cameras_northern_ireland']
    assert refresh_candidates('Belfast') == ['cameras_northern_ireland']


def test_schema_changes_fail_instead_of_erasing_a_previous_catalog():
    with pytest.raises(ValueError, match='prior catalog retained'):
        trafficwatchni_cameras(b'<html>maintenance</html>')


@pytest.mark.parametrize('url', [
    'http://www.trafficwatchni.com/twni/cameras/static?id=44',
    'https://www.trafficwatchni.com/twni/cameras/static?id=44&next=http://localhost',
    'https://www.trafficwatchni.com/twni/cameras/static?id=44#anything',
    'https://www.trafficwatchni.com/other?id=44',
    'https://evil.example/twni/cameras/static?id=44',
])
def test_viewer_urls_are_strictly_scoped_to_official_camera_pages(url):
    assert not camera_url(url, 'cameras_northern_ireland', page=True)
