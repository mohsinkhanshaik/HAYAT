"""Depth, coverage and privacy boundaries for professional-contact discovery."""
from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace

import pytest

from app.research.fetcher import FetchedResource
from app.research.models import SearchResult
from app.research import public_contacts as contacts


BASE = 'https://institute.example/'
PROFILE = BASE + 'people/ada'
PERSON = ('<h1>Ada Example</h1><p>Example Institute</p>'
          '<article class="staff-card"><h2>Ada Example</h2><p>Example Institute</p>'
          '<p>Ada Example professional email: <a href="mailto:ada@institute.example">Email</a></p></article>')


def resource(url, body, *, final=None):
    raw = body.encode()
    return FetchedResource(url, final or url, 200, {'content-type': 'text/html; charset=utf-8'}, raw, 1,
                           '2026-10-24T12:00:00Z', hashlib.sha256(raw).hexdigest())


def manager(pages=None, rows=None):
    events = []
    pages = pages or {}
    def fetch(url, **options):
        events.append(('fetch', url, options))
        value = pages[url]
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, FetchedResource) else resource(url, value)
    def search(query, **options):
        events.append(('search', query, options))
        return rows(query) if callable(rows) else list(rows or [])
    provider = SimpleNamespace(search=search, last_errors=[], last_coverage=[])
    return SimpleNamespace(fetcher=SimpleNamespace(fetch=fetch), search=provider, settings={}), events


@pytest.mark.parametrize('options', [{'depth': 'unlimited'}, {'depth': None}, {'depth': []}, {'tor_only': 1},
                                    {'tor_only': 'true'}, {'max_pages': 13}, {'max_pages': False}])
def test_new_controls_validate_before_requests(options):
    parent, events = manager()
    with pytest.raises(ValueError):
        contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', **options)
    assert events == []


def test_deep_uses_wide_search_and_reports_provider_coverage_without_search_snippet_facts():
    parent, events = manager({PROFILE: PERSON})
    def wide(query, **options):
        events.append(('wide', query, options))
        return [SearchResult('Lead only', PROFILE, snippet='Unverified secret: fake@elsewhere.example')]
    parent.search.search = lambda *a, **k: pytest.fail('The fallback-only search must not replace wide search')
    parent.search.search_wide = wide
    parent.search.last_coverage = [dict(provider='index_one', status='results', usable_results=1),
                                   dict(provider='index_two', status='no_usable_results', usable_results=0)]
    parent.search.last_errors = ['index_two: TimeoutError: password=hush fake@elsewhere.example']
    report = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', depth='deep', max_pages=12)
    queries = [row for row in events if row[0] == 'wide']
    assert len(queries) == report['searches_completed'] == report['coverage']['queries_planned'] == 5
    assert len({row[1] for row in queries}) == 5
    assert report['pages_checked'] == 1 and len(report['candidates']) == 1
    assert report['coverage']['queries'][0]['providers'][0]['provider'] == 'index_one'
    assert report['coverage']['duplicate_leads'] == 4
    assert 'hush' not in str(report) and 'fake@elsewhere' not in str(report)
    assert report['route'] == 'direct_public_web' and not report['scope']['exhaustive']
    assert report['coverage']['absence_of_data'] == 'unknown'


def test_selected_source_is_read_before_index_timeouts_and_queries_are_site_scoped_in_deep_mode():
    parent, events = manager({PROFILE: PERSON})
    def fail(query, **options):
        events.append(('search', query, options))
        raise TimeoutError('private diagnostics')
    parent.search.search = fail
    report = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', PROFILE, depth='deep')
    assert events[0][0] == 'fetch' and events[0][1] == PROFILE
    assert report['candidates'] and report['outcome'] == 'evidence_found'
    assert report['searches_attempted'] == 5 and report['searches_completed'] == 0
    assert any(row[1].startswith('site:institute.example ') for row in events if row[0] == 'search')
    assert 'private diagnostics' not in str(report)
    assert report['sources'][0]['discovered_via'] == 'selected_organization_url'


def test_deep_follows_two_same_site_directory_levels_and_never_external_or_hidden_routes():
    staff = BASE + 'staff'
    pages = {BASE: '<h1>Example Institute</h1><a href="/staff">Staff directory</a>'
                   '<a href="https://elsewhere.example/">Ada Example</a>'
                   '<a hidden href="/private">Ada Example</a>',
             staff: '<h1>Example Institute</h1><a href="/people/ada">Ada Example</a>', PROFILE: PERSON}
    parent, events = manager(pages)
    report = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', BASE, depth='deep')
    assert [row[1] for row in events if row[0] == 'fetch'] == [BASE, staff, PROFILE]
    assert report['candidates'] and report['sources'][-1]['link_depth'] == 2
    assert report['sources'][-1]['parent_url'] == staff
    assert report['coverage']['pages_retrieved'] == 3


def test_normal_directory_depth_is_bounded_to_one_hop():
    staff = BASE + 'staff'
    pages = {BASE: '<h1>Example Institute</h1><a href="/staff">Our team</a>',
             staff: '<h1>Example Institute</h1><a href="/people/ada">Ada Example</a>', PROFILE: PERSON}
    parent, events = manager(pages)
    report = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', BASE)
    assert [row[1] for row in events if row[0] == 'fetch'] == [BASE, staff]
    assert not report['candidates']


def test_page_budget_is_exact_and_coverage_never_claims_to_have_searched_all_pages():
    pages = {f'{BASE}people/ada-{i}': PERSON for i in range(20)}
    parent, events = manager(pages, [SearchResult('Ada', url) for url in pages])
    report = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', depth='deep', max_pages=12)
    assert len([row for row in events if row[0] == 'fetch']) == report['pages_checked'] == 12
    assert report['coverage']['stop_reason'] == 'page_limit'
    assert report['coverage']['unvisited_leads'] == 8
    assert not report['scope']['exhaustive'] and report['completed'] and not report['partial']


def test_remaining_search_leads_are_balanced_across_source_hosts():
    urls = [f'{BASE}people/ada-{i}' for i in range(6)] + ['https://conference.example/ada']
    parent, events = manager({url: PERSON for url in urls}, [SearchResult('Ada', url) for url in urls])
    report = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', max_pages=3)
    fetched = [row[1] for row in events if row[0] == 'fetch']
    assert fetched[:2] == [urls[0], urls[-1]]
    assert report['coverage']['distinct_source_hosts'] == 2
    assert len(report['candidates']) == 3 and len({row['id'] for row in report['candidates']}) == 3


def test_budget_returns_existing_evidence_as_partial_not_false_completion(monkeypatch):
    tick = [0.0]
    monkeypatch.setattr(contacts, 'time', SimpleNamespace(monotonic=lambda: tick[0]))
    parent, events = manager({PROFILE: PERSON})
    def search(*a, **k):
        tick[0] = 181.0
        return []
    parent.search.search = search
    report = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', PROFILE, depth='deep')
    assert events[0][0] == 'fetch' and report['candidates']
    assert report['partial'] and not report['completed'] and report['outcome'] == 'partial_evidence'
    assert report['coverage']['stop_reason'] == 'run_budget'
    rendered = contacts.format_report(report)
    assert 'PARTIAL:' in rendered and 'Absence of data elsewhere: unknown' in rendered


def test_budget_cannot_hide_a_stop_or_route_change(monkeypatch):
    tick = [0.0]
    stopped = [False]
    monkeypatch.setattr(contacts, 'time', SimpleNamespace(monotonic=lambda: tick[0]))
    parent, _ = manager()
    def search(*a, **k):
        tick[0], stopped[0] = 181.0, True
        return []
    parent.search.search = search
    with pytest.raises(PermissionError, match='cancelled'):
        contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', depth='deep', check=lambda: not stopped[0])


def test_failed_fetch_and_empty_results_have_distinct_unknown_outcomes():
    parent, _ = manager({PROFILE: TimeoutError('internal data')}, [SearchResult('Ada', PROFILE)])
    failed = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute')
    assert failed['completed'] and not failed['partial'] and failed['outcome'] == 'sources_unavailable'
    assert failed['coverage']['pages_failed'] == 1 and failed['coverage']['pages_retrieved'] == 0
    parent, _ = manager()
    empty = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute')
    assert empty['outcome'] == 'no_eligible_evidence'
    assert empty['coverage']['absence_of_data'] == failed['coverage']['absence_of_data'] == 'unknown'


def test_index_failures_that_return_empty_are_reported_as_unavailable_and_do_not_echo_remote_data():
    parent, _ = manager()
    parent.search.last_errors = ['index_one: PermissionError: robots.txt password=SECRET; private@example.org']
    report = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute')
    assert report['outcome'] == 'sources_unavailable'
    assert all(row['status'] == 'provider_unavailable' for row in report['coverage']['queries'])
    assert 'SECRET' not in str(report) and 'private@example.org' not in str(report)
    assert 'publisher and authentication restrictions were respected' in str(report)


def onion():
    public_key = bytes(range(32))
    version = bytes([3])
    checksum = hashlib.sha3_256(b'.onion checksum' + public_key + version).digest()[:2]
    return 'http://' + base64.b32encode(public_key + checksum + version).decode().lower() + '.onion/'


def test_valid_onion_records_are_outside_this_professional_discovery_workflow():
    parent, events = manager()
    with pytest.raises(ValueError):
        contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', onion())
    assert not events
    parent, events = manager({}, [SearchResult('Onion', onion()), SearchResult('Broker', 'https://whitepages.com/ada')])
    report = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', depth='deep')
    assert not [row for row in events if row[0] == 'fetch']
    assert report['coverage']['rejected_leads'] == 10


def test_leaked_record_pages_never_create_public_contact_evidence():
    parent, _ = manager({PROFILE: '<title>Leaked database records</title>' + PERSON})
    report = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', PROFILE)
    assert not report['candidates'] and not report['team_routes']
    assert report['sources'] and not report['sources'][0]['accepted']
    assert 'leak' in report['sources'][0]['reason'].lower()
    assert 'ada@institute.example' not in str(report)


def test_source_audit_contains_retrieval_provenance_even_when_no_contact_is_found():
    parent, _ = manager({PROFILE: '<h1>Example Institute</h1><p>No public contact route.</p>'})
    report = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', PROFILE)
    rendered = contacts.format_report(report)
    assert 'Retrieved source audit:' in rendered and PROFILE in rendered
    assert report['sources'][0]['sha256'] in rendered and report['sources'][0]['retrieved_at'] in rendered
    assert 'Absence of data elsewhere: unknown' in rendered


@pytest.mark.parametrize('proxy', [None, 'http://127.0.0.1:8080', 'socks5://proxy.example:9050',
                                  'socks5://127.0.0.1', 'socks5://127.0.0.1:0'])
def test_required_tor_rejects_missing_or_nonlocal_routes_before_any_request(monkeypatch, proxy):
    from app.privacy_masks import ROUTE
    monkeypatch.setattr(ROUTE, 'snapshot', lambda: (proxy, 1234))
    parent, events = manager()
    with pytest.raises(PermissionError, match='no direct fallback'):
        contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', PROFILE, tor_only=True)
    assert events == []


def test_tor_adapters_require_tor_and_do_not_change_parent_services(monkeypatch):
    from app.privacy_masks import ROUTE
    monkeypatch.setattr(ROUTE, 'snapshot', lambda: ('socks5://127.0.0.1:19050', 1234))
    parent, _ = manager()
    parent.fetcher.fetch = lambda *a, **k: pytest.fail('Never use parent direct-capable fetcher while Tor is required')
    parent.search.search = lambda *a, **k: pytest.fail('Never use parent direct-capable search while Tor is required')
    fetched, searched, closed = [], [], []
    def adapters(manager_arg, guard):
        assert manager_arg is parent
        def fetch(url, **options):
            guard()
            fetched.append(url)
            return resource(url, PERSON)
        def search(query, **options):
            guard()
            searched.append(query)
            return []
        return SimpleNamespace(fetch=fetch, close=lambda: closed.append(True)), SimpleNamespace(search=search)
    monkeypatch.setattr(contacts, '_tor_resources', adapters)
    report = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', PROFILE, tor_only=True)
    assert fetched == [PROFILE] and len(searched) == 2 and closed == [True]
    assert report['route'] == 'public_web_via_tor' and report['tor_required'] and report['scope']['tor_required']
    assert not report['scope']['onion_sources']


def test_existing_active_tor_also_uses_required_tor_adapters_and_rejects_route_changes(monkeypatch):
    from app.privacy_masks import ROUTE
    snapshot = [('socks5://127.0.0.1:19050', 1234)]
    monkeypatch.setattr(ROUTE, 'snapshot', lambda: snapshot[0])
    parent, _ = manager()
    closed = []
    def adapters(*args):
        def fetch(url, **options):
            snapshot[0] = (None, 1235)
            return resource(url, PERSON)
        return SimpleNamespace(fetch=fetch, close=lambda: closed.append(True)), SimpleNamespace(search=lambda *a, **k: [])
    monkeypatch.setattr(contacts, '_tor_resources', adapters)
    with pytest.raises(PermissionError, match='route changed'):
        contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', PROFILE)
    assert closed == [True]


def test_real_tor_factory_marks_both_indexes_and_fetcher_required(monkeypatch):
    import socket
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: pytest.fail('Factory must not resolve destination DNS locally'))
    parent, _ = manager()
    fetcher, search = contacts._tor_resources(parent, lambda: None)
    try:
        from app.research.onion import TorRequiredFetcher
        assert isinstance(fetcher, TorRequiredFetcher)
        assert len(search.providers) == 2
        assert all(provider.require_tor for provider in search.providers)
        assert all(provider.name in {'duckduckgo_html', 'bing_rss'} for provider in search.providers)
    finally:
        fetcher.close()



def test_private_phone_strings_are_redacted_from_titles_and_channel_labels():
    body = PERSON.replace('<h1>', '<title>Directory 555-123-4567</title><h1>', 1)
    body = body.replace('>Email</a>', '>Email 555-234-5678</a>')
    parent, _ = manager({PROFILE: body})
    report = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', PROFILE)
    assert '555-123-4567' not in str(report) and '555-234-5678' not in str(report)
    assert '[number omitted]' in report['sources'][0]['title']
    assert report['candidates'][0]['channels'][0]['label_redacted']


def test_privacy_mode_is_checked_before_requests_and_after_received_data():
    parent, events = manager({PROFILE: PERSON})
    parent.authority = SimpleNamespace(privacy_mode=True)
    with pytest.raises(PermissionError, match='Privacy mode'):
        contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', PROFILE)
    assert not events
    parent.authority.privacy_mode = False
    original = parent.fetcher.fetch
    def fetch(*args, **options):
        value = original(*args, **options)
        parent.authority.privacy_mode = True
        return value
    parent.fetcher.fetch = fetch
    with pytest.raises(PermissionError, match='Privacy mode'):
        contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', PROFILE)
    assert len(events) == 1


def test_unbounded_provider_iterators_are_capped_without_reading_arbitrary_leads():
    parent, _ = manager({PROFILE: PERSON})
    produced = []
    def search(*args, **options):
        while True:
            produced.append(True)
            yield SearchResult('Ada', PROFILE)
    parent.search.search = search
    report = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute', max_pages=1)
    assert len(produced) == 40 and len(report['candidates']) == 1
    assert report['pages_checked'] == 1


def test_final_url_deduplication_retains_one_source_without_merging_namesake_records():
    alias = BASE + 'profile-alias'
    parent, _ = manager({PROFILE: PERSON, alias: resource(alias, PERSON, final=PROFILE)},
                        [SearchResult('Ada', PROFILE), SearchResult('Alias', alias)])
    report = contacts.discover_contacts(parent, 'Ada Example', 'Example Institute')
    assert len(report['sources']) == len(report['candidates']) == 1
    assert report['coverage']['duplicate_pages'] == 1 and report['pages_checked'] == 2
