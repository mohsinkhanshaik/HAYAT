"""Public intelligence stays autonomous, bounded and faithful to source evidence."""
from datetime import UTC, datetime, timedelta
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest

from app.agent.planner import Planner
from app.intelligence.feeds import BY_KEY, coordinates, normalize
from app.intelligence.manager import IntelligenceManager, matches, validate_watch
from app.intelligence.tools import register_intelligence_tools, report
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry


def quake(identifier='q1', magnitude=4, point=None):
    return {'id': identifier, 'properties': {'place': 'Test location', 'type': 'earthquake', 'mag': magnitude,
            'time': 1791158400000, 'url': 'https://earthquake.usgs.gov/example'},
            'geometry': {'type': 'Point', 'coordinates': point if point is not None else [179, 15, 8]}}


class Fetcher:
    def __init__(self):
        self.calls = []
        self.failure = set()
        self.payloads = {
            'earthquakes': {'type': 'FeatureCollection', 'features': [quake()]},
            'natural_events': {'events': [{'id': 'n1', 'title': 'Test wildfire', 'categories': [{'title': 'Wildfires'}],
                'geometry': [{'type': 'Point', 'coordinates': [20, 10], 'date': '2026-10-01T00:00:00Z'}],
                'sources': [{'url': 'https://eonet.gsfc.nasa.gov/example'}]}]},
            'cyber': {'vulnerabilities': [{'cveID': 'CVE-2026-12345', 'vulnerabilityName': 'Test product entry',
                      'dateAdded': '2026-10-01', 'vendorProject': 'Example vendor', 'product': 'Example app'}]},
            'space_weather': [{'product_id': 'TEST', 'issue_datetime': '2026-10-01 00:00:00.000',
                              'message': 'Test space bulletin\nNo forecast supplied.'}],
        }

    def fetch(self, url, *args, **kwargs):
        key = next(key for key, feed in BY_KEY.items() if feed.url == url)
        self.calls.append(key)
        if key in self.failure:
            raise RuntimeError('Source unavailable')
        content = json.dumps(self.payloads[key]).encode()
        return SimpleNamespace(content=content, retrieved_at=datetime.now(UTC).isoformat(),
                               sha256=hashlib.sha256(content).hexdigest(), final_url=url)

    def close(self):
        self.closed = True


@pytest.fixture
def manager(tmp_path):
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    value = IntelligenceManager(tmp_path / 'intelligence.db', Fetcher(), registry)
    register_intelligence_tools(registry, value)
    yield value
    value.close()


def make_due(manager, key):
    state = manager.store.states().get(key, {})
    state['next_attempt_at'] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    manager.store.state(key, state)


def test_live_contracts_preserve_time_identity_and_unknown_geography(manager):
    result = manager.refresh()
    assert result['updated'] == 4 and result['failed'] == 0
    data = manager.dashboard(limit=100)
    assert data['total_matching'] == 4 and all(not source['stale'] for source in data['sources'])
    cyber = next(row for row in data['events'] if row['feed'] == 'cyber')
    assert cyber['latitude'] is None and cyber['longitude'] is None
    assert 'not established' in cyber['details']['applicability']
    assert all(row['source_sha256'] and row['observed_at'] and row['url'] for row in data['events'])
    assert not manager.store.alerts()  # Initial baseline is not a flood of old alerts.
    from pathlib import Path
    brief = Path(result['brief_path']).read_text(encoding='utf-8')
    assert 'SHA-256:' in brief and 'Test space bulletin' in brief and 'last successful fetch' in brief


def test_nasa_mislabeled_json_is_allowed_only_for_nasa(manager):
    original = manager.fetcher.fetch
    allowed = {}

    def fetch(url, max_bytes, content_types):
        allowed[url] = content_types
        return original(url)

    manager.fetcher.fetch = fetch
    assert manager.refresh()['updated'] == 4
    for feed in manager.feeds:
        key = feed.key
        assert ('application/rss+xml' in allowed[feed.url]) == (key == 'natural_events')
    make_due(manager, 'natural_events')
    manager.fetcher.fetch = lambda *args: SimpleNamespace(content=b'<rss><channel/></rss>')
    assert manager.refresh(['natural_events'])['failed'] == 1
    assert manager.dashboard(feed='natural_events')['total_matching'] == 1


@pytest.mark.parametrize('point', [None, [], [1], [True, 10], [180.1, 10], [10, -91], ['NaN', 1], [1, 'Infinity']])
def test_bad_coordinates_never_become_a_fake_origin(point):
    assert coordinates(point) == (None, None)


def test_corrupt_source_preserves_snapshot_and_recovers_automatically(manager):
    manager.refresh(['earthquakes'])
    manager.fetcher.failure.add('earthquakes')
    make_due(manager, 'earthquakes')
    failed = manager.refresh(['earthquakes'])
    assert failed['failed'] == 1
    cached = manager.dashboard(feed='earthquakes')
    assert cached['total_matching'] == 1 and cached['events'][0]['stale']
    assert len(manager.store.history('earthquakes')) == 1
    assert manager.store.states()['earthquakes']['failures'] == 1
    before_calls = len(manager.fetcher.calls)
    assert manager.refresh(['earthquakes'])['sources'][0]['cached']
    assert len(manager.fetcher.calls) == before_calls
    manager.fetcher.failure.clear()
    make_due(manager, 'earthquakes')
    assert manager.refresh(['earthquakes'])['updated'] == 1
    assert not manager.dashboard(feed='earthquakes')['events'][0]['stale']


def test_new_and_revised_events_alert_once_and_old_feed_disappearance_is_not_resolution(manager):
    manager.refresh(['earthquakes'])
    manager.fetcher.payloads['earthquakes']['features'].append(quake('q2', 6))
    make_due(manager, 'earthquakes')
    manager.refresh(['earthquakes'])
    assert len(manager.store.alerts()) == 1
    make_due(manager, 'earthquakes')
    manager.refresh(['earthquakes'])
    assert len(manager.store.alerts()) == 1 and len(manager.store.history('earthquakes')) == 2
    manager.fetcher.payloads['earthquakes']['features'][1]['properties']['mag'] = 6.1
    make_due(manager, 'earthquakes')
    manager.refresh(['earthquakes'])
    assert len(manager.store.alerts()) == 2
    manager.fetcher.payloads['earthquakes']['features'] = []
    make_due(manager, 'earthquakes')
    output = manager.refresh(['earthquakes'])
    assert output['sources'][0]['change']['missing_count'] == 2
    assert 'does not establish resolution' in output['basis']


def test_partial_bad_rows_are_visible_and_total_schema_failure_is_not_empty_success(manager):
    manager.refresh(['earthquakes'])
    manager.fetcher.payloads['earthquakes']['features'].append({'id': 'bad'})
    make_due(manager, 'earthquakes')
    assert manager.refresh(['earthquakes'])['sources'][0]['rejected'] == 1
    manager.fetcher.payloads['earthquakes'] = {'error': 'Not a feed'}
    make_due(manager, 'earthquakes')
    assert manager.refresh(['earthquakes'])['failed'] == 1
    assert manager.dashboard(feed='earthquakes')['total_matching'] == 1


def test_watch_regions_wrap_dateline_without_matching_unknown_locations(manager):
    watch = validate_watch({'title': 'Dateline', 'bbox': [170, -20, -170, 20]})
    row = normalize(BY_KEY['earthquakes'], {'features': [quake()]})[0]['earthquakes:q1']
    assert matches(row, watch)
    assert matches({**row, 'longitude': -179}, watch)
    assert not matches({**row, 'longitude': 0}, watch)
    assert not matches({**row, 'longitude': None, 'latitude': None}, watch)
    manager.refresh()
    assert manager.dashboard(bbox=[170, -20, -170, 20])['total_matching'] == 1


@pytest.mark.parametrize('rule', [
    {}, {'title': 'Unbounded'}, {'title': 'Test', 'feeds': ['private']},
    {'title': 'Test', 'keywords': ['']}, {'title': 'Test', 'bbox': [0, 20, 10, -20]},
    {'title': 'Test', 'bbox': [float('nan'), 0, 20, 10]},
    {'title': 'Test', 'feeds': ['natural_events'], 'min_magnitude': 3},
    {'title': 'Test', 'feeds': ['earthquakes'], 'min_magnitude': True},
    {'title': 'Test', 'feeds': ['cyber'], 'command': 'execute'},
])
def test_invalid_watch_inputs_cannot_become_broad_automatic_actions(rule):
    with pytest.raises(ValueError):
        validate_watch(rule)


def test_snapshots_watches_alerts_and_deleted_defaults_survive_restart(manager):
    manager.refresh(['earthquakes'])
    manager.watch('save', 'region', {'title': 'Test area', 'bbox': [170, -20, -170, 20]})
    manager.watch('delete', 'earthquake-5plus')
    again = IntelligenceManager(manager.store.path, Fetcher())
    try:
        assert again.dashboard(feed='earthquakes')['total_matching'] == 1
        assert 'region' in [rule['key'] for rule in again.store.watches()]
        assert 'earthquake-5plus' not in [rule['key'] for rule in again.store.watches()]
    finally:
        again.close()


def test_storage_retains_only_twelve_snapshots_and_one_thousand_alerts(manager):
    for index in range(15):
        manager.store.save('earthquakes', str(index), {'id': {'title': str(index)}})
    assert len(manager.store.history('earthquakes')) == 12
    for index in range(1005):
        manager.store.add_alert(str(index), f'{index:04}', {'title': 'Test alert'})
    assert len(manager.store.alerts(2000)) == 1000


@pytest.mark.parametrize('text,tool', [
    ('Show global intelligence', 'intelligence_dashboard'),
    ('Show Osiris status', 'intelligence_dashboard'),
    ('Show Osiris playbook', 'intelligence_brief'),
    ('Refresh intelligence feeds', 'intelligence_refresh'),
    ('Show intelligence alerts', 'intelligence_alerts'),
    ('Check intelligence monitor', 'intelligence_monitor'),
])
def test_chat_uses_independent_intelligence_and_delivers_evidence(manager, text, tool):
    intent = IntentRouter().route(text)
    assert intent.intent == IntentKind.ACTION
    plan = Planner(manager.registry).create_plan(text, intent=intent)
    assert plan[0].tool == tool and plan[0].max_retries == 0
    result = manager.registry.execute(tool, plan[0].arguments)
    assert result.success, result.error
    reply = report([{'tool': tool, 'result': result.to_dict(), 'verification': {'passed': True}}])
    assert 'Hayat intelligence' in reply
    assert report([{'tool': tool, 'result': result.to_dict(), 'verification': {'passed': False}}]) is None


def test_autonomous_monitor_needs_no_callback_and_does_not_reset_chat_budget(manager):
    approval_calls = []
    manager.registry.approval_callback = lambda *args: approval_calls.append(args) or False
    manager.registry.budget.consume('owner-work')
    manager.start()
    deadline = time.monotonic() + 3
    while len(manager.store.states()) < 4 and time.monotonic() < deadline:
        time.sleep(.01)
    assert manager.stop(timeout=2)
    assert len(manager.store.states()) == 4 and approval_calls == []
    assert manager.registry.budget.total == 1 and manager.registry.budget.counts == {'owner-work': 1}


def test_monitor_respects_deny_policy_instead_of_bypassing_registry(manager):
    manager.registry.permissions = PermissionManager({'default': 'ALLOW', 'permissions': {'public_web_read': 'DENY'}}, manager.store.path.parent)
    manager.start()
    deadline = time.monotonic() + 2
    while manager.last_error is None and time.monotonic() < deadline:
        time.sleep(.01)
    assert manager.stop(timeout=2)
    assert manager.last_error and manager.fetcher.calls == []


def test_overlapping_refreshes_do_not_duplicate_work(manager):
    entered, release = threading.Event(), threading.Event()
    original = manager.fetcher.fetch
    def blocked(*args, **kwargs):
        entered.set()
        release.wait(2)
        return original(*args, **kwargs)
    manager.fetcher.fetch = blocked
    thread = threading.Thread(target=lambda: manager.refresh(['earthquakes']))
    thread.start()
    try:
        assert entered.wait(1)
        assert manager.refresh(['earthquakes'])['refresh_in_progress']
    finally:
        release.set()
        thread.join(2)
    assert manager.fetcher.calls == ['earthquakes']


def test_expired_fetches_are_stale_even_without_a_recorded_failure(manager):
    manager.refresh(['earthquakes'])
    state = manager.store.states()['earthquakes']
    state['last_success_at'] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    manager.store.state('earthquakes', state)
    assert manager.dashboard(feed='earthquakes')['events'][0]['stale']
