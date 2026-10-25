"""Source-derived severity, filters, per-event assessment and local-model analysis."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.intelligence.classify import assess, category, recommended_actions, space_weather
from app.intelligence.feeds import BY_KEY, normalize
from app.intelligence.live import LIVESTREAMS
from app.intelligence.manager import IntelligenceManager
from app.intelligence.tools import direct_plan, register_intelligence_tools, report
from app.intelligence.view import format_point, relative_time, validate_view
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from tests.test_intelligence_resources import ADDED_CAMERAS, ExtendedFetcher
from tests.test_public_intelligence import Fetcher, make_due, quake


def row(feed, kind='', **details):
    return {'id': feed + ':x', 'feed': feed, 'title': 'Example place', 'kind': kind, 'details': details,
            'latitude': 1.0, 'longitude': 2.0, 'url': 'https://example.org', 'observed_at': None}


@pytest.fixture
def manager(tmp_path):
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    value = IntelligenceManager(tmp_path / 'intelligence.db', ExtendedFetcher(), registry, extended=True)
    register_intelligence_tools(registry, value)
    value.refresh()
    yield value
    value.close()


@pytest.mark.parametrize('details, level', [
    ({'magnitude': 6.2}, 'high'), ({'magnitude': 4.0, 'alert': 'orange'}, 'high'), ({'magnitude': 5.0}, 'medium'),
    ({'magnitude': 3.9, 'alert': 'yellow'}, 'medium'), ({'magnitude': 3.0}, 'low'), ({'magnitude': None}, 'low')])
def test_earthquake_severity_follows_magnitude_and_pager_alert(details, level):
    value = assess(row('earthquakes', 'earthquake', **details))
    assert value['severity'] == level and value['category'] == 'earthquakes'
    assert value['severity_basis'] and value['subtitle'] == 'Example place'
    if details['magnitude'] is not None:
        assert value['headline'] == f"M{details['magnitude']:.1f} Earthquake" and value['badge'].startswith('M')


def test_provider_rules_for_disasters_cyber_storms_fires_and_cameras():
    assert [assess(row('disasters', 'TC', alert_level=level))['severity'] for level in ('Red', 'Orange', 'Green', '')] == ['high', 'medium', 'low', 'low']
    assert assess(row('disasters', 'TC', alert_level='Red'))['category'] == 'severe_weather'
    assert category(row('disasters', 'VO')) == 'volcanoes' and category(row('disasters', 'XX')) == 'other'
    cyber = assess({**row('cyber', vendor='Acme', product='Router', ransomware_use='Known'), 'id': 'cyber:CVE-2026-1'})
    assert cyber['severity'] == 'high' and cyber['subtitle'] == 'CVE-2026-1 · Acme Router'
    assert assess(row('cyber', ransomware_use='Unknown'))['severity'] == 'medium'
    storm = assess(row('natural_events', 'Severe Storms', magnitude=130.0, magnitude_unit='kts'))
    assert storm['severity'] == 'high' and storm['badge'] == '130 kt' and storm['category'] == 'severe_weather'
    assert assess(row('natural_events', 'Severe Storms', magnitude=40.0, magnitude_unit='kts'))['severity'] == 'medium'
    assert assess(row('natural_events', 'Wildfires', magnitude=12000.0, magnitude_unit='acres'))['severity'] == 'medium'
    unrated = assess(row('natural_events', 'Wildfires'))
    assert unrated['severity'] == 'low' and 'no severity' in unrated['severity_basis']
    camera = assess(row('cameras_london', 'public_camera', provider='Transport for London'))
    assert camera['severity'] == 'info' and camera['category'] == 'cameras' and camera['badge'] == 'CAM'


def test_space_weather_bulletins_get_readable_headlines_scales_and_impacts():
    message = ('Space Weather Message Code: ALTK06\r\nSerial Number: 1\r\n\r\nALERT: Geomagnetic K-index of 6\nNoaa Scale: G2 - Moderate\r\n'
               '\r\nPotential Impacts: Area of impact poleward of 55 degrees.\r\nInduced Currents - Power grid fluctuations.\r\n\r\nEnd')
    value = space_weather(message)
    assert value['headline'] == 'Alert: Geomagnetic K-index of 6' and value['scale'] == 'G2' and value['severity'] == 'medium'
    assert value['impacts'] == ['Area of impact poleward of 55 degrees.', 'Induced Currents - Power grid fluctuations.']
    assert space_weather('WATCH: Geomagnetic Storm Category G4 Predicted')['severity'] == 'high'
    assert space_weather('ALERT: Geomagnetic K-index of 8')['scale'] == 'G4'
    cancelled = space_weather('CANCEL WATCH: Geomagnetic Storm Category G3 Predicted')
    assert cancelled['severity'] == 'low' and cancelled['cancelled']
    assert space_weather('Plain bulletin')['headline'] == '' and space_weather('Plain bulletin')['severity'] == 'low'
    rows, _rejected = normalize(BY_KEY['space_weather'], [{'product_id': 'K06A', 'issue_datetime': '2026-10-01 00:00:00.000', 'message': message}])
    assert next(iter(rows.values()))['title'] == 'Alert: Geomagnetic K-index of 6'
    actions = recommended_actions({**row('space_weather', message=message), 'category': 'space_weather'})
    assert actions[0]['text'].startswith('NOAA impact note: Area of impact')


def test_filters_by_category_severity_and_recent_hours(manager):
    everything = manager.dashboard(limit=1000)
    assert everything['aggregates']['counts_by_category']['cameras'] == 71 + len(LIVESTREAMS) + ADDED_CAMERAS
    assert {row['category'] for row in manager.dashboard(category='floods', limit=50)['events']} == {'floods'}
    medium = manager.dashboard(min_severity='medium', limit=50)['events']
    assert {row['id'] for row in medium} == {'cyber:CVE-2026-12345', 'disasters:FL-1',
                                             'wildfires_us:2026-CAFIX-000001', 'roads_cars_mn:mn-1'}
    assert manager.dashboard(since_hours=1)['total_matching'] == 0
    assert manager.dashboard(query='M4.0 earthquake')['events'][0]['id'] == 'earthquakes:q1'
    with pytest.raises(ValueError):
        manager.dashboard(category='aliens')
    with pytest.raises(ValueError):
        manager.dashboard(min_severity='extreme')
    with pytest.raises(ValueError):
        manager.dashboard(since_hours=0)
    registry_result = manager.registry.execute('intelligence_dashboard', {'category': 'cyber', 'min_severity': 'medium'})
    assert registry_result.success and registry_result.output['total_matching'] == 1


def test_view_prioritizes_severe_markers_hides_layers_and_keeps_cameras_out_of_live_feed(manager):
    value = manager.view(map_categories={'earthquakes', 'floods', 'wildfires'}, map_limit=2)
    assert value['mapped_total'] == 4 and [row['id'] for row in value['mapped']][0] == 'disasters:FL-1'
    assert all(row['category'] != 'cameras' and row['observed_at'] for row in value['live'])
    assert value['live_total'] == 11 and value['revision'] == manager.store.revision
    # Placed livestreams, plus the ISS broadcast at the station's position; news channels have no place.
    placed = sum(item.get('latitude') is not None for item in LIVESTREAMS) + 1
    assert manager.view(map_categories={'cameras'})['mapped_total'] == 70 + placed + ADDED_CAMERAS


def test_rows_are_cached_until_a_new_snapshot_and_writes_bump_the_revision(manager, monkeypatch):
    manager.view()
    reads = []
    original = manager.store.latest
    monkeypatch.setattr(manager.store, 'latest', lambda key: reads.append(key) or original(key))
    manager.view()
    manager.dashboard(query='flood')
    assert reads == []
    revision = manager.store.revision
    manager.fetcher.payloads['earthquakes']['features'] = [quake('q1', 4), quake('q9', 5.5)]
    make_due(manager, 'earthquakes')
    manager.refresh(['earthquakes'])
    assert manager.store.revision > revision
    # One read compares with the previous snapshot; one re-caches it for the saved brief.
    assert reads == ['earthquakes', 'earthquakes']
    reads.clear()
    assert manager.view()['aggregates']['counts_by_category']['earthquakes'] == 2
    assert reads == []


def test_event_assessment_reports_corroboration_relevance_and_staleness(tmp_path):
    fetcher = ExtendedFetcher()
    moment = datetime.now(UTC)
    fetcher.payloads['earthquakes']['features'] = [quake('q1', 6.1, [20.2, 10.1, 12])]
    fetcher.payloads['earthquakes']['features'][0]['properties']['time'] = int(moment.timestamp() * 1000)
    fetcher.payloads['disasters']['features'][0]['properties'].update(eventtype='EQ', datemodified=moment.isoformat())
    manager = IntelligenceManager(tmp_path / 'intelligence.db', fetcher, None, extended=True)
    try:
        manager.refresh()
        record = manager.event_record('earthquakes:q1')
        judged = record['assessment']
        assert record['corroborating'][0]['id'] == 'disasters:EQ-1'
        assert judged['confidence']['level'] == 'high' and 'corroborated by 1 other source: disasters' in judged['confidence']['basis']
        assert judged['risk']['level'] == 'high' and 'Magnitude 6.1' in judged['risk']['basis']
        assert judged['relevance']['level'] == 'high' and 'Earthquakes M5+' in judged['relevance']['basis']
        assert 'magnitude 6.1 earthquake' in judged['summary'] and 'no PAGER impact alert' in judged['summary']
        assert {action['action'] for action in judged['actions']} >= {'watch_area', 'weather', 'related'}
        fetcher.failure.add('earthquakes')
        make_due(manager, 'earthquakes')
        manager.refresh(['earthquakes'])
        state = manager.store.states()['earthquakes']
        state['last_success_at'] = (moment - timedelta(days=1)).isoformat()
        manager.store.state('earthquakes', state)
        stale = manager.event_record('earthquakes:q1')['assessment']
        assert stale['confidence']['level'] == 'low' and 'stale' in stale['confidence']['basis']
        text = report([{'tool': 'intelligence_event', 'result': {'success': True, 'output': record}, 'verification': {'passed': True}}])
        assert 'Confidence: High' in text and 'Suggested next steps:' in text
    finally:
        manager.close()


class Model:
    model = 'local-test'

    def __init__(self, content='<think>private reasoning</think>Summary: A strong earthquake was reported.'):
        self.content, self.calls = content, []

    def generate(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return SimpleNamespace(content=self.content, model=self.model, finish_reason='stop')


def test_local_model_analysis_is_grounded_labelled_and_routable(tmp_path):
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    manager = IntelligenceManager(tmp_path / 'intelligence.db', Fetcher(), registry)
    register_intelligence_tools(registry, manager)
    manager.refresh()
    try:
        with pytest.raises(RuntimeError, match='local model'):
            manager.analyze('earthquakes:q1')
        manager.model = Model()
        result = registry.execute('intelligence_analyze', {'event_id': 'earthquakes:q1'})
        assert result.success and result.output['analysis'] == 'Summary: A strong earthquake was reported.'
        assert 'Inference' in result.output['basis']
        messages, options = manager.model.calls[0]
        assert 'untrusted' in messages[0].content and '"earthquakes:q1"' in messages[1].content
        assert options['temperature'] == 0.2
        plan = direct_plan('Analyze intelligence event earthquakes:q1 with HAYAT', registry.names())
        assert plan['tool'] == 'intelligence_analyze' and plan['arguments'] == {'event_id': 'earthquakes:q1'}
        assert 'A strong earthquake' in report([{'tool': 'intelligence_analyze', 'result': {'success': True, 'output': result.output},
                                                 'verification': {'passed': True}}])
        manager.model = Model('<think>only thoughts</think>')
        assert not registry.execute('intelligence_analyze', {'event_id': 'earthquakes:q1'}).success
    finally:
        manager.close()


def test_view_preferences_and_display_helpers():
    assert validate_view({'category': 'cyber', 'min_severity': 'high', 'time_range': 'Last 7 days', 'hidden_layers': ['cameras']})
    for bad in ({'category': 'x'}, {'min_severity': 'x'}, {'time_range': 'Forever'}, {'hidden_layers': ['x']}, {'hidden_layers': 'cameras'}):
        with pytest.raises(ValueError):
            validate_view(bad)
    moment = datetime(2026, 10, 5, 12, tzinfo=UTC)
    assert relative_time('2026-10-05T11:58:00+00:00', moment) == '2 min ago'
    assert relative_time('2026-10-05T09:00:00Z', moment) == '3 h ago'
    assert relative_time('2026-10-01T12:00:00+00:00', moment) == '4 d ago'
    assert relative_time(None, moment) == '' and relative_time('2026-10-05T11:59:50+00:00', moment) == 'just now'
    assert format_point(-21.421, -174.268) == '21.421°S, 174.268°W'
