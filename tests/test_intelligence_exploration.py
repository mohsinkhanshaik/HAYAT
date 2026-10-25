"""Evidence, geographic navigation and persistent display choices work together."""
import pytest

from app.intelligence.manager import IntelligenceManager
from app.intelligence.tools import TOOL_NAMES, direct_plan, report
from app.intelligence.view import MapViewport, distance_km
from tests.test_public_intelligence import manager as manager_fixture, make_due, quake


@pytest.fixture
def manager(tmp_path):
    yield from manager_fixture.__wrapped__(tmp_path)


def test_paging_aggregates_cover_every_match_and_geographic_filters_skip_unknown(manager):
    manager.fetcher.payloads['earthquakes']['features'] = [quake(f'q{index}', index % 10) for index in range(451)]
    manager.refresh()
    first = manager.dashboard(limit=200)
    second = manager.dashboard(limit=200, offset=200)
    last = manager.dashboard(limit=200, offset=400)
    identifiers = [row['id'] for page in (first, second, last) for row in page['events']]
    assert len(identifiers) == len(set(identifiers)) == 454
    assert first['next_offset'] == 200 and last['next_offset'] is None
    assert first['aggregates'] == last['aggregates']
    assert first['aggregates']['largest_earthquake_magnitude'] == 9
    assert first['aggregates']['unknown_location'] == 2
    assert manager.dashboard(bbox=[170, -20, -170, 30])['total_matching'] == 451
    assert manager.dashboard(located_only=True, limit=1000)['total_matching'] == 452
    assessment = manager.assessment()
    assert assessment['total_matching'] == 454 and len(assessment['events']) == 20
    assert 'World coverage is partial' in assessment['coverage_gaps'][0]
    assert 'selected outlets' in assessment['coverage_gaps'][0]
    assert len(assessment['source_changes']) == 4
    assert assessment['source_changes'][0]['change']['baseline']


def test_deep_event_revision_history_nearby_and_archived_evidence(manager):
    manager.refresh()
    manager.fetcher.payloads['earthquakes']['features'] = [quake('q1', 6), quake('q2', 3, [179.1, 15, 8])]
    make_due(manager, 'earthquakes')
    manager.refresh(['earthquakes'])
    record = manager.event_record('earthquakes:q1')
    assert record['in_current_feed'] and record['event']['details']['magnitude'] == 6
    assert len(record['retained_revisions']) == 2
    assert record['matching_watches'][0]['key'] == 'earthquake-5plus'
    assert record['nearby_count'] == 1 and 0 < record['nearby_events'][0]['distance_km'] < 20
    assert 'causally' in record['basis']
    manager.fetcher.payloads['earthquakes']['features'] = [quake('q2', 3)]
    make_due(manager, 'earthquakes')
    manager.refresh(['earthquakes'])
    archived = manager.event_record('earthquakes:q1')
    assert not archived['in_current_feed']
    assert archived['event']['retrieved_at'] == archived['retained_revisions'][0]['captured_at']
    assert archived['event']['source_sha256'] is None
    cyber = manager.event_record('cyber:CVE-2026-12345')
    assert not cyber['nearby_events'] and 'not plotted' in cyber['event_summary']
    with pytest.raises(ValueError):
        manager.event_record('earthquakes:not-present')


def test_preferences_survive_restart_and_do_not_change_monitor_authority(manager):
    manager.preferences('save', {'home_alert_limit': 1, 'home_detail': 'detailed', 'show_home': False,
                                 'zoom': 8, 'center_lon': 179, 'region': 'Oceania'})
    before = manager.preferences()['preferences']
    manager.preferences('save', {'show_alerts': False})
    other = IntelligenceManager(manager.store.path, manager.fetcher)
    try:
        assert other.preferences()['preferences'] == {**before, 'show_alerts': False}
        assert not other.status()['running'] and other.enabled
    finally:
        other.close()


@pytest.mark.parametrize('value', [{'zoom': float('nan')}, {'zoom': True}, {'zoom': 33}, {'center_lat': 91},
                                    {'home_alert_limit': 6}, {'show_home': 'yes'}, {'region': []},
                                    {'query': 'x'*501}, {'execute': 'anything'}])
def test_invalid_display_settings_leave_saved_preferences_unchanged(manager, value):
    before = manager.preferences()
    with pytest.raises(ValueError):
        manager.preferences('save', value)
    assert manager.preferences() == before


def test_alert_filters_and_paging_and_tool_reporting(manager):
    manager.refresh()
    manager.fetcher.payloads['earthquakes']['features'] = [quake(f'alert{index}', 6) for index in range(35)]
    make_due(manager, 'earthquakes')
    manager.refresh(['earthquakes'])
    value = manager.alerts(limit=10, feed='earthquakes', bbox=[170, -20, -170, 30])
    assert len(value['alerts']) == 10 and value['total_matching'] == 35 and value['next_offset'] == 10
    assert manager.alerts(feed='cyber')['total_matching'] == 0
    assert manager.alerts(query='Test location')['total_matching'] == 35
    result = manager.registry.execute('intelligence_alerts', {'feed': 'earthquakes', 'limit': 1000})
    assert result.success and len(result.output['alerts']) == 35
    reply = report([{'tool': result.tool, 'result': {'success': True, 'output': result.output}, 'verification': {'passed': True}}])
    assert '35 recent local alerts' in reply


def test_pointer_zoom_roundtrip_pan_dateline_and_poles():
    view = MapViewport(3, 175, 10)
    before = view.unproject(420, 230, 800, 400)
    view.zoom_at(2, 420, 230, 800, 400)
    assert view.unproject(420, 230, 800, 400) == pytest.approx(before)
    assert view.unproject(*view.project(*before, 800, 400), 800, 400) == pytest.approx(before)
    view.pan(-60, 0, 800, 400)
    assert -180 <= view.longitude <= 180 and view.bounds()[0] > view.bounds()[2]
    view.pan(0, 100000, 800, 400)
    assert view.bounds()[3] <= 90
    view.fit([110, -55, -150, 15])
    assert view.bounds()[0] > view.bounds()[2]
    assert distance_km({'latitude': 0, 'longitude': 179.9}, {'latitude': 0, 'longitude': -179.9}) == pytest.approx(22.24, abs=.1)
    assert distance_km({'latitude': None}, {'latitude': 0}) is None


@pytest.mark.parametrize('text,tool,args', [
    ('What is happening around the world?', 'intelligence_assess', {}),
    ('Analyze intelligence in Europe', 'intelligence_assess', {'region': 'Europe'}),
    ('Analyze intelligence about Microsoft', 'intelligence_assess', {'query': 'Microsoft'}),
    ('Explain intelligence event earthquakes:q1', 'intelligence_event', {'event_id': 'earthquakes:q1'}),
])
def test_model_free_deep_intelligence_routes(text, tool, args):
    plan = direct_plan(text, TOOL_NAMES)
    assert plan['tool'] == tool and plan['arguments'] == args
