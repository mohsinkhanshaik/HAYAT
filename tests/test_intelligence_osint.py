"""Exercise the recovered intelligence features through their actual dispatch and storage boundaries."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.intelligence.aviation import Aviation
from app.intelligence.feeds import BY_KEY, normalize
from app.intelligence.history import PositionHistory
from app.intelligence.live import LiveData
from app.intelligence.objects import Objects, build, correlate
from app.intelligence.osint import register_osint_tools, last_object_id
from app.intelligence.reports import news_item, parse_rss, faa_records
from app.intelligence.search import parse, search
from app.intelligence.tools import TOOL_NAMES, direct_plan, report
from tests.test_public_intelligence import manager, quake  # noqa: F401


@pytest.fixture
def connected(manager):  # noqa: F811 -- imported pytest fixture
    manager.history = PositionHistory(manager.store.path.parent / 'positions.db')
    manager.live = LiveData(lambda url: {'ac': [{'hex': 'a12345', 'flight': 'TEST123', 'lat': 42.33, 'lon': -83.05,
                                               'category': 'A3', 'alt_baro': 30000}]}, recorder=manager.history)
    manager.aviation = Aviation(manager.live)
    manager.objects = Objects(manager)
    manager.fusion = SimpleNamespace(resolve=lambda place: {'latitude': 42.33, 'longitude': -83.05, 'name': place})
    register_osint_tools(manager.registry, manager)
    return manager


def task(tool, value):
    return [{'tool': tool, 'result': {'success': True, 'output': value}, 'verification': {'passed': True}}]


def row(identifier, outlet='bbc.com', disputes=False):
    return {'id': identifier, 'feed': 'news_bbc_world', 'title': 'Explosion reported in Beirut port',
            'category': 'attack', 'latitude': 33.9, 'longitude': 35.5, 'url': f'https://{outlet}/{identifier}',
            'observed_at': datetime.now(UTC).isoformat(),
            'details': {'outlet': outlet, 'authority': 0.8, 'disputes': disputes,
                        'location_precision': 'city or region (approximate centre)'}}


def test_registration_preserves_feed_history_and_uses_recorded_positions(connected):
    registry = connected.registry
    original = registry.execute('intelligence_history', {'feed': 'earthquakes'})
    assert original.success and original.output['snapshots'] == []
    result = registry.execute('intelligence_aircraft', {'place': 'Detroit'})
    assert result.success and result.output['count'] == 1
    history = registry.execute('intelligence_positions', {'place': 'Detroit', 'at': 'now'})
    assert history.success and history.output['found'][0]['ident'] == 'a12345'
    assert 'TEST123' in report(task('intelligence_positions', history.output))


@pytest.mark.parametrize(('text', 'tool'), [
    ('What aircraft were near Detroit at 14:20?', 'intelligence_positions'),
    ('Please show military aircraft worldwide', 'intelligence_aircraft'),
    ('What conflicts are active worldwide?', 'intelligence_objects'),
    ('What changed in Detroit over the last six hours?', 'intelligence_region_changes'),
    ('Are there unusual air patterns near Detroit?', 'intelligence_anomalies'),
    ('Search intelligence for conflict near Beirut last 24 hours', 'intelligence_search'),
])
def test_natural_requests_reach_registered_tools(text, tool):
    assert direct_plan(text, TOOL_NAMES)['tool'] == tool


def test_general_research_does_not_become_intelligence_search():
    assert direct_plan('Search for SQLite documentation', TOOL_NAMES) is None


def test_duplicate_or_disputing_reports_do_not_confirm_attack():
    duplicates = build([row('one'), row('two'), row('three')])[0]
    assert duplicates['status'] == 'UNVERIFIED' and duplicates['independent_sources'] == 1
    disputed = build([row('one'), row('denial', 'apnews.com', True)])[0]
    assert disputed['status'] != 'CONFIRMED' and disputed['independent_sources'] == 1
    three = build([row('one'), row('two', 'apnews.com'), row('three', 'reuters.com')])[0]
    assert three['status'] == 'LIKELY'
    assert 'not a calibrated probability' in three['confidence_basis']
    assert 'fetch_reliability' in three['sources'][0] and 'historical_accuracy' not in three['sources'][0]


def test_repeated_denials_count_once_and_provider_name_cannot_impersonate_agency():
    primary = row('one')
    fake = row('two', 'unknown.example', True)
    fake['details'].update(authority=0.1, provider='NOAA news rumours')
    value = build([primary, fake, {**fake, 'id': 'three'}, {**fake, 'id': 'four'}])[0]
    assert value['status'] == 'UNVERIFIED'
    assert not any(item['official'] for item in value['sources'])


def test_official_quake_sources_merge_without_using_just_place(connected):
    stamp = datetime.now(UTC).isoformat()
    rows, _ = normalize(BY_KEY['earthquakes'], {'features': [quake('one', 6, [35.5, 33.9, 8])]})
    raw = rows['earthquakes:one']
    raw.update(observed_at=stamp, retrieved_at=stamp)
    connected.store.save('earthquakes', stamp, rows)
    value = connected.objects.query()
    assert value['total'] == 1 and value['objects'][0]['status'] == 'CONFIRMED'
    assert 'USGS' in report(task('intelligence_objects', value))
    connected.store.save('earthquakes', stamp, {})
    assert connected.objects.query(since_hours=24)['total'] == 1


def test_same_aircraft_is_preserved_before_and_after_event(connected):
    observed = row('event')
    moment = datetime.fromisoformat(observed['observed_at'])
    connected.objects = SimpleNamespace(get=lambda ident: build([observed])[0])
    connected.history.record('aircraft', [{'hex': 'ABCDEF', 'flight': 'EXAMPLE', 'latitude': 33.9, 'longitude': 35.5}],
                             'fixture', moment - timedelta(minutes=15))
    connected.history.record('aircraft', [{'hex': 'ABCDEF', 'flight': 'EXAMPLE', 'latitude': 33.9, 'longitude': 35.5}],
                             'fixture', moment + timedelta(minutes=15))
    connected.live = None
    value = correlate(connected, 'event')
    assert value['aircraft']['before'][0]['ident'] == value['aircraft']['after'][0]['ident'] == 'abcdef'
    assert 'Correlation is not causation' in report(task('intelligence_correlate', value))


def test_object_identity_survives_new_source_and_restart(connected):
    connected.feeds += (BY_KEY['news_bbc_world'], BY_KEY['news_aljazeera'])
    stamp = datetime.now(UTC).isoformat()
    original = row('news_bbc_world:z')
    original['details']['provider'] = 'BBC News'
    original['kind'] = 'explosion'
    original['details']['security_category'] = 'attack'
    connected.store.save('news_bbc_world', stamp, {original['id']: original})
    first = connected.objects.all()[0]
    added = {**original, 'id': 'news_aljazeera:a', 'feed': 'news_aljazeera',
             'details': {**original['details'], 'outlet': 'aljazeera.com'}}
    connected.store.save('news_aljazeera', stamp, {added['id']: added})
    second = Objects(connected).all()[0]
    assert second['id'] == first['id'] and second['status'] == 'LIKELY'
    assert len(connected.store.objects_since('')) == 1


def test_separate_quakes_from_same_agency_are_not_collapsed():
    first = {**row('quake1'), 'feed': 'earthquakes', 'category': 'earthquakes', 'details': {'magnitude': 5}}
    second = {**first, 'id': 'quake2', 'observed_at': (datetime.now(UTC) + timedelta(minutes=1)).isoformat()}
    assert len(build([first, second])) == 2
    third = {**first, 'id': 'emsc1', 'feed': 'earthquakes_emsc'}
    assert len(build([first, third])) == 1


def test_history_timezone_dateline_and_repeated_observations(tmp_path):
    history = PositionHistory(tmp_path / 'history.db')
    now = datetime.now(UTC)
    items = [{'hex': 'ABCDEF', 'flight': 'mixed123', 'latitude': 0, 'longitude': -179.95, 'operator': 'one|two'}]
    assert history.record('aircraft', items, 'fixture', now) == 1
    assert history.record('aircraft', items, 'fixture', now) == 0
    assert history.near(0, 179.95, 50, now)['found'][0]['ident'] == 'abcdef'
    assert history.track('ABCDEF')[0]['label'] == 'MIXED123'
    assert history.near(0, 179.95, 50, now.replace(tzinfo=None))['found']


def test_search_places_time_units_and_unresolved_locations(connected):
    value = parse('conflict and aircraft near Detroit last 24 hours within 20 miles')
    assert value['place'] == 'Detroit' and value['since_hours'] == 24 and value['radius_km'] == 32.2
    value = parse('earthquakes in South Sudan last two days')
    assert value['country'] == 'south sudan' and value['since_hours'] == 48
    connected.live = None
    value = search(connected, 'earthquakes near DoesNotExist last 24 hours')
    assert 'Could not resolve' in value['basis'] and 'events' not in value['sections']


def test_empty_geographic_search_never_substitutes_unrelated_worldwide_records(connected, monkeypatch):
    monkeypatch.setattr(connected.objects, 'all', lambda: [])
    monkeypatch.setattr(connected, 'dashboard', lambda **kwargs: pytest.fail('Geographic search lost its constraints'))
    result = search(connected, 'earthquakes near 0, 0')
    assert result['sections']['events'] == []
    assert result['sections']['place']['latitude'] == 0


@pytest.mark.parametrize('point', ['91, 0', '0, 181', '-91.5, 0'])
def test_invalid_search_coordinates_are_rejected(connected, point):
    with pytest.raises(ValueError, match='Coordinates'):
        search(connected, f'events near {point}')


def test_combined_categories_are_sorted_together_and_deduplicated(connected):
    first = {'id':'far','title':'Fire','severity':9,'status':'CONFIRMED','last_reported':None,'distance_km':50}
    second = {**first,'id':'close','title':'Quake','distance_km':2}
    connected.objects = SimpleNamespace(query=lambda **kwargs: {'objects': [first] if kwargs['category']=='wildfires' else [second, first]})
    result = search(connected, 'fires and earthquakes near Detroit', limit=3)
    assert [item['id'] for item in result['sections']['events']] == ['close', 'far']


def test_object_time_filter_respects_offsets_and_does_not_drop_the_equator(connected, monkeypatch):
    now = datetime.now(UTC)
    item = {'id':'one','title':'Quake','severity':1,'status':'CONFIRMED','category':'earthquakes',
            'last_reported':now.astimezone(__import__('datetime').timezone(timedelta(hours=-7))).isoformat(),
            'latitude':0,'longitude':-179.95}
    monkeypatch.setattr(connected.objects, 'all', lambda: [item, {**item,'id':'unlocated','latitude':None}])
    result = connected.objects.query(latitude=0, longitude=179.95, radius_km=20, since_hours=1, sort='newest')
    assert [row['id'] for row in result['objects']] == ['one']
    assert result['objects'][0]['distance_km'] < 12


def test_news_xml_faa_and_location_precision():
    raw = b'<rss><channel><item><title>Explosion reported in Beirut</title><link>https://bbc.com/news/example</link></item></channel></rss>'
    value = news_item(BY_KEY['news_bbc_world'], parse_rss(raw)[0])
    assert value['details']['location_precision'].startswith('city')
    assert value['details']['security_category'] == 'attack'
    assert news_item(BY_KEY['news_bbc_world'], {'title': 'Storm in Sudan', 'link': 'https://bbc.com/other'})['details']['location_precision'].startswith('country')
    with pytest.raises(ValueError):
        parse_rss(b'<!DOCTYPE rss><rss/>')
    with pytest.raises(ValueError):
        faa_records(b'<!DOCTYPE root><root/>', {})
    payload = b'<root><Delay_type><Name>Ground Stops</Name><Ground_Stop><ARPT>JFK</ARPT><Reason>Weather</Reason></Ground_Stop></Delay_type></root>'
    assert faa_records(payload, {'JFK': {'name': 'Kennedy'}})[0]['airport']['name'] == 'Kennedy'


def test_pronouns_require_one_verified_object_in_current_task():
    item = build([row('one')])[0]
    session = SimpleNamespace(last_task=SimpleNamespace(history=task('intelligence_objects', {'intelligence_objects': True, 'objects': [item]})))
    assert last_object_id(session) == item['id']
    assert direct_plan('What sources confirm this attack?', TOOL_NAMES, session)['arguments']['object_id'] == item['id']
    session.last_task.history[0]['verification']['passed'] = False
    assert last_object_id(session) is None
    assert direct_plan('What sources confirm this attack?', TOOL_NAMES, session) is None


def test_invalid_coordinates_rejected_at_dispatch(connected):
    result = connected.registry.execute('intelligence_aircraft', {'latitude': 999, 'longitude': 0})
    assert not result.success and 'ValidationError' in result.error
    result = connected.registry.execute('intelligence_aircraft', {'latitude': 1})
    assert not result.success and 'both latitude and longitude' in result.error


def test_atlas_endpoints_use_registry_validation_and_surface_errors(connected):
    import httpx
    from app.intelligence.map_server import IntelligenceMapServer

    server = IntelligenceMapServer(connected)
    try:
        assert httpx.get(server.url + 'objects').json()['objects'] == []
        response = httpx.get(server.url + 'search?query=earthquakes+near+Detroit+last+24+hours')
        assert response.status_code == 200 and response.json()['search']
        response = httpx.get(server.url + 'correlate?id=does-not-exist')
        assert response.status_code == 400 and 'No intelligence object' in response.json()['error']
        assert httpx.get(server.url.replace(server.token, 'invalid-token') + 'objects').status_code == 404
    finally:
        server.close()
