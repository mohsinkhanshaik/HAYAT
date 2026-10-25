"""Thirteen open camera networks: adapters, media allowlists, nearest search and event links."""
import pytest

from app.intelligence.feeds import BY_KEY, OPEN_CAMERA_FEEDS
from app.intelligence.manager import IntelligenceManager, xml_records
from app.intelligence.providers import camera_url
from app.intelligence.tools import direct_plan, register_intelligence_tools, report
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from tests.test_intelligence_resources import NYC_ID, ExtendedFetcher
from tests.test_public_intelligence import quake


@pytest.fixture
def cameras(tmp_path):
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    fetcher = ExtendedFetcher()
    # An earthquake beside the New York camera, to link events to nearby cameras.
    fetcher.payloads['earthquakes']['features'].append(quake('q5', 5.1, [-73.97, 40.78, 5]))
    manager = IntelligenceManager(tmp_path / 'intelligence.db', fetcher, registry, extended=True)
    register_intelligence_tools(registry, manager)
    manager.refresh()
    yield manager
    manager.close()


def test_every_open_network_normalizes_with_provenance_and_media(cameras):
    assert all(cameras.store.states()[feed.key]['status'] == 'ok' for feed in OPEN_CAMERA_FEEDS)
    rows = {row['feed']: row for row in cameras.cameras(limit=1000)['cameras']}
    expected = {'cameras_nyc': ('New York City DOT', 'image'), 'cameras_finland': ('Fintraffic', 'image'),
                'cameras_singapore': ('Land Transport Authority', 'image'), 'cameras_hongkong': ('Hong Kong Transport Department', 'image'),
                'cameras_nz': ('NZ Transport Agency', 'image'), 'cameras_delaware': ('Delaware DOT', 'video'),
                'cameras_maryland': ('Maryland CHART', 'page'), 'cameras_austin': ('City of Austin Transportation', 'image'),
                'cameras_seattle': ('Seattle DOT', 'image'), 'cameras_iowa': ('Iowa DOT', 'image'), 'cameras_quebec': ('Québec 511', 'page')}
    for feed, (provider, media) in expected.items():
        row = rows[feed]
        assert row['details']['provider'] == provider and row['details'][media + '_url'], feed
        assert row['kind'] == 'public_camera' and row['latitude'] is not None and row['observed_at'] is None
        assert row['category'] == 'cameras' and row['provider'] == provider and row['severity'] == 'info'
    assert rows['cameras_finland']['title'] == 'kt51 Inkoo'
    assert rows['cameras_finland']['details']['preset_images'] == ['https://weathercam.digitraffic.fi/C0150302.jpg']
    assert (rows['cameras_seattle']['latitude'], rows['cameras_seattle']['longitude']) == (47.526783, -122.392755)
    assert rows['cameras_nz']['details']['image_url'] == 'https://trafficnz.info/camera/714.jpg'
    assert rows['cameras_iowa']['details']['video_url'].startswith('https://video2.iowadot.gov:8888/')
    assert rows['cameras_hongkong']['details']['county'] == 'Southern'
    assert rows['cameras_washington']['details']['image_url'] == 'https://images.wsdot.wa.gov/nw/005vc16500.jpg'
    austin = cameras.store.states()['cameras_austin']
    assert austin['count'] == 1 and austin['rejected'] == 0  # A planned camera is skipped, not a failure.


def test_stream_filters_exclude_clips_and_stills_and_reach_chat_and_atlas(cameras):
    from app.intelligence.providers import camera_media_profile
    import httpx
    streams = cameras.cameras(media='stream')
    assert streams['total_matching'] > 0 and streams['with_video_clips'] == 0
    assert all(camera_media_profile(row['details'])['stream'] for row in streams['cameras'])
    london = cameras.cameras(feed='cameras_london')['cameras'][0]
    profile = camera_media_profile(london['details'])
    assert profile['video_clip'] and profile['image'] and not profile['stream']
    for request in ('Find live cameras', 'Show live CCTV', 'Find HLS streams'):
        assert direct_plan(request, ['intelligence_cameras'])['arguments'] == {'media': 'stream', 'available_only': True}
    plan = direct_plan('Find live cameras in Delaware', ['intelligence_cameras'])
    assert plan['arguments'] == {'query': 'Delaware', 'media': 'stream', 'available_only': True}
    url = cameras.open_map()['map_url']
    with httpx.Client() as client:
        payload = client.get(url + 'data?media=stream').json()
        # The atlas shows located streams; the ISS broadcast moves with the station, and live news
        # channels and EarthCam/Sen have no fixed place.
        from app.intelligence.live import LIVESTREAMS
        unplaced = sum(item.get('latitude') is None and not item.get('image_url') for item in LIVESTREAMS)
        assert payload['total_matching'] == cameras.cameras(media='stream', located_only=True)['total_matching']
        assert payload['total_matching'] == streams['total_matching'] - unplaced
        assert all(camera_media_profile(row['details'])['stream'] for row in payload['events'])
        assert client.get(url + 'data?media=invalid').status_code == 400
        from tests.test_public_intelligence import make_due
        catalog = cameras.fetcher.payloads['cameras_delaware']['videoCameras']
        catalog.append({**catalog[0], 'id': 'NO-LOCATION', 'lat': None, 'lon': None})
        make_due(cameras, 'cameras_delaware')
        cameras.refresh(['cameras_delaware'])
        assert cameras.cameras(media='stream')['total_matching'] == streams['total_matching'] + 1
        located = client.get(url + 'data?media=stream').json()
        assert located['total_matching'] == streams['total_matching'] - unplaced  # no fixed place
        assert all(row['latitude'] is not None for row in located['events'])


def test_chat_playback_prepares_only_registered_media_and_reports_its_real_kind(cameras):
    from app.intelligence.tools import TOOL_NAMES
    from app.intent.router import IntentRouter
    from app.session import SessionState
    phrases = ('Play live camera in Delaware in chat', 'Hayat can you play live CCTV in chat',
               'Play camera live in chat', 'Show live camera in chat', 'Play camera in London live in chat')
    for phrase in phrases:
        plan = direct_plan(phrase, TOOL_NAMES)
        assert plan['tool'] == 'intelligence_camera_play'
        assert plan['arguments']['live_only']
        assert plan['arguments'].get('query') != 'chat'
        assert IntentRouter().route(phrase).entities['modality'] == 'public_intelligence'
    prepared = cameras.registry.execute('intelligence_camera_play', {'query': 'Delaware'})
    assert prepared.success and prepared.output['live']
    assert camera_url(prepared.output['video_url'], 'cameras_delaware', video=True)
    assert 'Prepared' in report([{'tool': prepared.tool, 'result': prepared.to_dict(), 'verification': {'passed': True}}])
    london = cameras.cameras(feed='cameras_london')['cameras'][0]
    # A chosen clip camera plays its clip, labelled as not live, instead of refusing.
    asked_live = cameras.registry.execute('intelligence_camera_play', {'event_id': london['id']})
    assert asked_live.success and asked_live.output['mode'] == 'clip' and 'not a continuous live stream' in asked_live.output['notice']
    clip = cameras.registry.execute('intelligence_camera_play', {'event_id': london['id'], 'live_only': False})
    assert clip.success and not clip.output['live'] and not clip.output['notice']
    assert direct_plan('Play camera ' + london['id'], TOOL_NAMES)['arguments']['event_id'] == london['id']
    assert not cameras.registry.execute('intelligence_camera_play', {'event_id': 'earthquakes:q5'}).success
    assert not cameras.registry.execute('intelligence_camera_play', {'video_url': 'https://localhost/private.m3u8'}).success
    session = SessionState()
    session.remember_video_result('play_video', {'path': 'D:/AI/old.mp4'})
    assert IntentRouter().route('Pause camera video', session).entities['modality'] == 'public_intelligence'
    for phrase, action in [('Pause camera', 'pause'), ('Resume camera', 'play'), ('Stop camera', 'stop')]:
        assert direct_plan(phrase, TOOL_NAMES)['arguments'] == {'action': action}


def test_chat_playback_can_fetch_missing_catalogs_and_does_not_substitute_another_location(tmp_path):
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    manager = IntelligenceManager(tmp_path / 'fresh.db', ExtendedFetcher(), registry, extended=True, enabled=False)
    register_intelligence_tools(registry, manager)
    try:
        result = registry.execute('intelligence_camera_play', {'query': 'Delaware'})
        assert result.success and result.output['feed'] == 'cameras_delaware'
        assert set(manager.store.states()) == {'cameras_delaware'}
        assert not registry.execute('intelligence_camera_play', {'query': 'a city with no cameras', 'refresh': False}).success
        assert not registry.execute('intelligence_camera_control', {'action': 'stop'}).success
    finally:
        manager.close()


def test_media_allowlist_is_per_network_and_port_bound():
    nyc = f'https://webcams.nyctmc.org/api/cameras/{NYC_ID}/image'
    assert camera_url(nyc, 'cameras_nyc') == nyc
    assert camera_url('https://webcams.nyctmc.org/api/cameras', 'cameras_nyc') == ''
    assert camera_url(nyc, 'cameras_finland') == ''
    iowa = 'https://video2.iowadot.gov:8888/a/playlist.m3u8'
    assert camera_url(iowa, 'cameras_iowa', video=True) == iowa
    assert camera_url('https://video2.iowadot.gov:9999/a/playlist.m3u8', 'cameras_iowa', video=True) == ''
    assert camera_url('https://video.deldot.gov:8888/a/playlist.m3u8', 'cameras_delaware', video=True) == ''
    page = 'https://chart.maryland.gov/Video/GetVideo/abc'
    assert camera_url(page, 'cameras_maryland', page=True) == page
    assert camera_url(page, 'cameras_quebec', page=True) == ''
    assert camera_url('http://chart.maryland.gov/Video/GetVideo/abc', 'cameras_maryland', page=True) == ''
    assert camera_url('https://s3-eu-west-1.amazonaws.com/other-bucket/x.jpg', 'cameras_london') == ''


def test_xml_catalogs_refuse_document_type_declarations():
    assert xml_records(b'<list><item><key>A</key></item></list>') == [{'key': 'A'}]
    with pytest.raises(ValueError):
        xml_records(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><list><item><key>&a;</key></item></list>')


def test_nearest_search_media_filter_and_network_summary(cameras):
    near = cameras.cameras(near=[40.7, -74.0], radius_km=50)
    assert [row['feed'] for row in near['cameras']] == ['cameras_nyc'] and near['cameras'][0]['distance_km'] < 15
    wide = cameras.cameras(near=[40.7, -74.0], radius_km=1000, limit=10)
    assert [row['distance_km'] for row in wide['cameras']] == sorted(row['distance_km'] for row in wide['cameras'])
    assert {row['feed'] for row in wide['cameras']} >= {'cameras_nyc', 'cameras_delaware', 'cameras_maryland'}
    assert {row['feed'] for row in cameras.cameras(media='video', limit=50)['cameras']} >= {'cameras_delaware', 'cameras_iowa', 'cameras_london'}
    assert {row['feed'] for row in cameras.cameras(media='page', limit=50)['cameras']} == {'cameras_maryland', 'cameras_quebec', 'cameras_alertca',
                                                                                   'cameras_illinois', 'cameras_michigan', 'cameras_alabama', 'cameras_luxembourg', 'cameras_valencia',
                                                                                   'livestreams'}
    summary = cameras.cameras(limit=1)
    from app.intelligence.live import LIVESTREAMS
    assert summary['networks']['Caltrans'] == 12 and summary['with_publisher_player'] == 8 + len(LIVESTREAMS)
    by_event = cameras.registry.execute('intelligence_cameras', {'near_event': 'earthquakes:q5', 'radius_km': 30})
    assert by_event.success and by_event.output['cameras'][0]['feed'] == 'cameras_nyc'
    assert 'Networks:' in report([{'tool': 'intelligence_cameras', 'result': {'success': True, 'output': by_event.output},
                                   'verification': {'passed': True}}])
    for bad in ({'near': [100, 0]}, {'near': [1, 2], 'radius_km': 0}, {'media': 'audio'}):
        with pytest.raises(ValueError):
            cameras.cameras(**bad)
    with pytest.raises(ValueError, match='no location'):
        cameras.cameras(near_event='cyber:CVE-2026-12345')


def test_frames_load_for_stills_and_explain_video_or_player_only_cameras(cameras):
    frame = cameras.camera_frame('cameras_nyc:' + NYC_ID)
    assert frame['image_url'].endswith('/image') and not frame['cached']
    with pytest.raises(ValueError, match='publishes video'):
        cameras.camera_frame('cameras_delaware:KCAM001')
    with pytest.raises(ValueError, match='publisher'):
        cameras.camera_frame('cameras_quebec:4057')


def test_events_link_to_the_nearest_camera_without_burying_nearby_events(cameras):
    record = cameras.event_record('earthquakes:q5')
    assert record['nearest_cameras'][0]['id'] == 'cameras_nyc:' + NYC_ID
    assert all(item['category'] != 'cameras' for item in record['nearby_events'])
    first = record['assessment']['actions'][0]
    assert first['action'] == 'camera:cameras_nyc:' + NYC_ID and 'snapshot' in first['text']
    camera_record = cameras.event_record('cameras_nyc:' + NYC_ID)
    assert not any(action['action'].startswith('camera:') for action in camera_record['assessment']['actions'])


def test_chat_routes_and_atlas_policy_cover_new_networks():
    names = ['intelligence_cameras']
    plan = direct_plan('Find cameras near 40.71, -74.0 within 20 km', names)
    assert plan['arguments'] == {'near': [40.71, -74.0], 'radius_km': 20.0, 'limit': 20}
    plan = direct_plan('Show the nearest public cameras near event earthquakes:q5', names)
    assert plan['arguments'] == {'near_event': 'earthquakes:q5', 'radius_km': 100.0, 'limit': 20}
    assert direct_plan('Find public cameras in Hong Kong', names)['arguments'] == {'query': 'Hong Kong'}
    from app.intelligence.map_server import VIDEO_SOURCES
    assert 'https://video.deldot.gov' in VIDEO_SOURCES and 'https://video2.iowadot.gov:8888' in VIDEO_SOURCES
    assert {'news_bbc_world', 'news_aljazeera', 'news_un', 'gdelt_security',
            'storms_nhc', 'airports_faa', 'earthquakes_emsc'} <= BY_KEY.keys()
    # camera/road networks plus seven report feeds; Norway, Estonia, Tallinn added 2026-10-11; nine official hazard feeds 2026-10-14; seven more 2026-10-15
    assert 'cameras_luxembourg' in BY_KEY
    assert {'cameras_nsw', 'cameras_lyon'} <= BY_KEY.keys()
    assert len(BY_KEY) == 131


def test_second_wave_networks_keep_identity_media_and_robots_choices(cameras):
    rows = {}
    for row in cameras.cameras(limit=1000)['cameras']:
        rows.setdefault(row['feed'], []).append(row)
    alert = rows['cameras_alertca'][0]
    assert alert['details']['image_url'].endswith('/latest-frame.jpg') and alert['details']['page_url']
    assert (round(alert['latitude'], 2), round(alert['longitude'], 2)) == (40.45, -120.57) and alert['details']['county'] == 'Lassen'
    assert rows['cameras_drivebc'][0]['details']['image_url'].startswith('https://www.drivebc.ca/images/1141.jpg')
    assert len(rows['cameras_iceland']) == 2  # One station number, two cameras.
    assert rows['cameras_toronto'][0]['title'] == 'York St / Bremner Blvd' and rows['cameras_toronto'][0]['longitude'] == -79.3826
    assert rows['cameras_calgary'][0]['details']['image_url'] == 'https://trafficcam.calgary.ca/loc86.jpg'
    illinois, michigan = rows['cameras_illinois'][0], rows['cameras_michigan'][0]
    # robots.txt forbids automated image retrieval here: viewer links only, never an image URL.
    assert not illinois['details']['image_url'] and illinois['id'] == 'cameras_illinois:IL-IDOTD1-IK14B-NONE'
    assert not michigan['details']['image_url'] and michigan['details']['page_url'].endswith('id=1129')
    assert (michigan['latitude'], michigan['longitude']) == (42.491304, -83.04479)
    assert 'micamerasimages' not in str(michigan) and 'cctv.travelmidwest' not in str(illinois)


def test_keyed_networks_stay_off_without_a_key_and_never_store_it(tmp_path, monkeypatch):
    from app.intelligence.feeds import BY_KEY, configured
    for name in ('HAYAT_KEY_511_ONTARIO', 'HAYAT_KEY_WSDOT'):
        monkeypatch.delenv(name, raising=False)
    fetcher = ExtendedFetcher()
    manager = IntelligenceManager(tmp_path / 'off.db', fetcher, None, extended=True)
    try:
        assert all(not feed.key_env for feed in manager.feeds)
        result = manager.refresh(['cameras_511_ontario'])
        assert result['sources'][0]['status'] == 'needs_key' and 'HAYAT_KEY_511_ONTARIO' in result['sources'][0]['error']
        assert 'cameras_511_ontario' not in manager.store.states()
    finally:
        manager.close()
    secret = 'S3CRET-key/value'
    monkeypatch.setenv('HAYAT_KEY_511_ONTARIO', secret)
    monkeypatch.setenv('HAYAT_KEY_WSDOT', secret)
    assert configured(BY_KEY['cameras_511_ontario'])
    fetcher = ExtendedFetcher()
    fetcher.payloads['cameras_511_ontario'] = [{'Id': 'ON-1', 'Location': 'Hwy 401 at Keele St', 'Roadway': 'Highway 401',
        'Latitude': 43.72, 'Longitude': -79.48, 'Views': [{'Url': 'https://511on.ca/map/Cctv/ON-1--1', 'Status': 'Enabled'},
                                                          {'Url': 'https://evil.example/x.jpg', 'Status': 'Enabled'}]}]
    fetcher.payloads['cameras_wsdot'] = [{'CameraID': 9001, 'Title': 'I-5 at Olive Way', 'ImageURL': 'https://images.wsdot.wa.gov/nw/005vc16500.jpg',
        'IsActive': True, 'CameraLocation': {'Latitude': 47.61, 'Longitude': -122.32, 'RoadName': 'I-5'}}]
    manager = IntelligenceManager(tmp_path / 'on.db', fetcher, None, extended=True)
    try:
        assert {'cameras_511_ontario', 'cameras_wsdot'} <= {feed.key for feed in manager.feeds}
        manager.refresh(['cameras_511_ontario', 'cameras_wsdot'])
        assert all('key=S3CRET' in url or 'AccessCode=S3CRET' in url for url in fetcher.requested_keyed_urls)
        ontario = manager.cameras(feed='cameras_511_ontario')['cameras'][0]
        assert ontario['details']['image_url'] == 'https://511on.ca/map/Cctv/ON-1--1' and 'other_views' not in ontario['details']
        assert ontario['details']['country'] == 'Canada' and ontario['details']['provider'] == 'Ontario 511'
        assert manager.cameras(feed='cameras_wsdot')['cameras'][0]['details']['provider'] == 'WSDOT'
        def failing(url, *args, **kwargs):
            # Real HTTP errors quote the full request URL, key included.
            raise RuntimeError(f"Client error '403 Forbidden' for url '{url}'")
        fetcher.fetch = failing
        from tests.test_public_intelligence import make_due
        make_due(manager, 'cameras_511_ontario')
        manager.refresh(['cameras_511_ontario'])
        assert '***' in manager.store.states()['cameras_511_ontario']['error']
        stored = manager.store.run(lambda db: ' '.join(' '.join(map(str, row)) for table in ('feeds', 'snapshots', 'settings')
                                                       for row in db.execute(f'SELECT * FROM {table}')))
        brief = (tmp_path / 'intelligence' / 'latest-brief.md').read_text(encoding='utf-8')
        assert secret not in stored and 'S3CRET' not in stored and 'S3CRET' not in brief
    finally:
        manager.close()
