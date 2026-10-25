"""Discovery controls review source evidence before saving real local records."""
from copy import deepcopy
import hashlib
import threading
import time
from types import SimpleNamespace
from tkinter import ttk

import pytest

from app.gui_contact_discovery import discovery_contact_payload
from app.gui_theme import style_controls
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from tests.test_gui_networking import studio as studio, finish


@pytest.fixture
def report():
    sources, candidates = [], []
    for index in range(2):
        url = f'https://institute.example/people/ada-{index}'
        snippet = f'Ada Example — Example Institute — public professional correspondence: ada{index}@example.org'
        digest = hashlib.sha256(snippet.encode()).hexdigest()
        channel = {'kind': 'email', 'value': f'ada{index}@example.org', 'status': 'public_professional',
                   'source_url': url, 'source_hash': digest, 'recorded_at': '2026-10-24T12:00:00Z', 'snippet': snippet}
        sources.append({'url': url, 'final_url': url, 'title': 'Official directory', 'retrieved_at': channel['recorded_at'],
                        'sha256': digest, 'status': 'retrieved', 'accepted': True, 'reason': 'Name and organization match'})
        candidates.append({'id': f'candidate-{index}', 'name': 'Ada Example', 'organization': 'Example Institute',
                           'source_url': url, 'source_hash': digest, 'retrieved_at': channel['recorded_at'],
                           'snippet': snippet, 'channels': [channel]})
    team_url = 'https://institute.example/contact'
    team_snippet = 'Example Institute public research office: research@example.org'
    team_hash = hashlib.sha256(team_snippet.encode()).hexdigest()
    team = {'id': 'team-1', 'team_name': 'Example Institute research office', 'organization': 'Example Institute',
            'scope': 'team', 'kind': 'email', 'value': 'research@example.org', 'status': 'public_professional',
            'source_url': team_url, 'source_hash': team_hash, 'recorded_at': '2026-10-24T12:00:00Z', 'snippet': team_snippet}
    sources.append({'url': team_url, 'final_url': team_url, 'title': 'Institute contact', 'retrieved_at': team['recorded_at'],
                    'sha256': team_hash, 'status': 'retrieved', 'accepted': True, 'reason': 'Public team route'})
    return {'name': 'Ada Example', 'organization': 'Example Institute', 'official_url': '', 'candidates': candidates,
            'team_routes': [team], 'sources': sources, 'errors': [], 'limits': ['At most four public pages checked.'],
            'identity_verified': False, 'completed': True, 'cancelled': False}


@pytest.fixture
def discovery(studio, report):
    window, book = studio
    style = ttk.Style(window)
    previous_theme = style.theme_use()
    style.theme_use('clam')
    style_controls(window, window.palette)
    registry = ToolRegistry(PermissionManager({'default': 'DENY', 'permissions': {'network': 'ALLOW'}}))
    calls = []
    def handler(**arguments):
        calls.append((arguments, threading.get_ident()))
        return deepcopy(report)
    registry.register('public_contact_search', 'Explicit source-backed professional discovery',
                      {'type': 'object', 'properties': {'name': {'type': 'string'}, 'organization': {'type': 'string'},
                       'official_url': {'type': 'string'}, 'max_pages': {'type': 'integer'},
                       'depth': {'enum': ['normal', 'deep']}, 'tor_only': {'type': 'boolean'}},
                       'required': ['name', 'organization'], 'additionalProperties': False},
                      'low', 'network', handler)
    window.gui.runtime.registry = registry
    assert window.find_professional_contacts(name='Ada Example', organization='Example Institute')
    panel = window._contact_discovery
    yield panel, window, book, calls
    if panel.winfo_exists():
        panel.close()
    style.theme_use(previous_theme)


def finish_discovery(panel, predicate=None, seconds=4):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        panel.update()
        if predicate() if predicate is not None else not panel.busy:
            return
        time.sleep(.01)
    assert predicate() if predicate is not None else not panel.busy, panel.status.get()


def select(panel, scope, index=0):
    panel.tabs.select(('person', 'team', 'source').index(scope))
    panel.tables[scope].selection_set(f'{scope}:{index}')
    panel.show_selected()
    panel.update()


def test_open_prefills_without_search_and_reuses_live_window(discovery):
    panel, window, book, calls = discovery
    assert panel.name.get() == 'Ada Example' and panel.organization.get() == 'Example Institute'
    assert not calls and not book.all()
    assert window.find_professional_contacts(name='Lee Example', organization='Another Institute', official_url='https://example.org/directory')
    assert window._contact_discovery is panel
    assert panel.name.get() == 'Lee Example'
    assert panel.official_url.get() == 'https://example.org/directory'
    assert not calls


def test_search_uses_registered_tool_in_worker_and_keeps_candidates_distinct(discovery):
    panel, _window, book, calls = discovery
    panel.search_button.invoke()
    finish_discovery(panel)
    assert calls[0][0] == {'name': 'Ada Example', 'organization': 'Example Institute', 'official_url': '', 'max_pages': 4}
    assert calls[0][1] != threading.get_ident()
    assert len(panel.tables['person'].get_children()) == 2
    assert len(panel.tables['team'].get_children()) == 1
    assert not book.all(), 'Searching must not automatically import contacts'
    select(panel, 'person')
    evidence = panel.evidence.get('1.0', 'end')
    assert 'identity unverified' in evidence and 'ada0@example.org' in evidence
    assert 'Source SHA-256:' in evidence and '2026-10-24T12:00:00Z' in evidence


def test_explicit_save_keeps_public_channel_provenance_and_parent_unsaved_edits(discovery):
    panel, window, book, _calls = discovery
    window.fields['role'].set('Owner draft must survive')
    assert panel.search()
    finish_discovery(panel)
    select(panel, 'person')
    assert panel.save_selected()
    finish_discovery(panel)
    finish(window)
    rows = book.all()
    assert len(rows) == 1 and rows[0]['name'] == 'Ada Example'
    assert rows[0]['channels'][0]['status'] == 'public_professional'
    assert rows[0]['channels'][0]['consent'] is False
    assert 'Source SHA-256:' in rows[0]['notes'] and '2026-10-24T12:00:00Z' in rows[0]['notes']
    assert window.fields['role'].get() == 'Owner draft must survive'
    assert not panel.save_selected()
    assert panel.search()
    finish_discovery(panel)
    select(panel, 'person')
    assert panel.save_selected()
    finish_discovery(panel)
    assert 'Already saved' in panel.status.get() and len(book.all()) == 1


def test_empty_tables_explain_themselves_before_and_after_a_search(discovery):
    panel, _window, _book, _calls = discovery
    panel.update()
    for scope, table in panel.tables.items():
        label = table._hayat_empty
        assert label.winfo_manager() == 'place' and label.cget('text'), scope
    assert 'press Find' in panel.tables['person']._hayat_empty.cget('text')
    panel.paint({'name': 'Ada Example', 'organization': 'Example Institute', 'candidates': [], 'team_routes': [],
                 'sources': [], 'errors': [], 'limits': []})
    assert 'Nothing was guessed' in panel.tables['person']._hayat_empty.cget('text')
    assert panel.search()
    finish_discovery(panel)
    for table in panel.tables.values():
        assert table.get_children() and table._hayat_empty.winfo_manager() == ''


def test_evidence_shows_the_publishing_page_for_each_channel(discovery):
    panel, _window, _book, _calls = discovery
    assert panel.search()
    finish_discovery(panel)
    select(panel, 'person')
    evidence = panel.evidence.get('1.0', 'end')
    assert 'Published on: https://institute.example/people/ada-0' in evidence
    select(panel, 'team')
    team_evidence = panel.evidence.get('1.0', 'end')
    assert 'Published on: https://institute.example/contact' in team_evidence


def test_team_route_is_saved_as_team_not_requested_person(discovery):
    panel, _window, book, _calls = discovery
    assert panel.search()
    finish_discovery(panel)
    select(panel, 'team')
    assert 'TEAM ROUTE · not the requested person' in panel.evidence.get('1.0', 'end')
    assert panel.save_selected()
    finish_discovery(panel)
    row = book.all()[0]
    assert row['name'] == 'Example Institute research office' and row['name'] != 'Ada Example'
    assert row['channels'][0]['value'] == 'research@example.org'
    assert 'organization/team route' in row['notes']


def test_search_empty_or_permission_denied_is_honest(discovery):
    panel, _window, book, calls = discovery
    panel.name.set('')
    assert not panel.search() and not calls
    panel.name.set('Ada Example')
    panel.gui.runtime.registry.permissions.policies['network'] = 'DENY'
    assert panel.search()
    finish_discovery(panel)
    assert not calls and not book.all()
    assert not panel.report and 'DENY' in panel.status.get()


@pytest.mark.parametrize('mode', ['privacy', 'PRIVATE', 'LOCAL', 'LOCAL_ONLY'])
def test_privacy_blocks_search_and_source_open_before_dispatch(discovery, mode):
    panel, _window, _book, calls = discovery
    panel.paint({'name': 'Ada Example', 'organization': 'Example Institute', 'candidates': [], 'team_routes': [],
                 'sources': [{'url': 'https://institute.example/source', 'accepted': False}], 'errors': [], 'limits': []})
    select(panel, 'source')
    panel.gui.runtime.authority = SimpleNamespace(privacy_mode=mode == 'privacy')
    panel.gui.runtime.session = SimpleNamespace(effective_modes=[] if mode == 'privacy' else [mode])
    assert not panel.search()
    assert not panel.open_source()
    assert not calls and 'local-only mode' in panel.status.get()


def test_emergency_stop_blocks_discovery_and_local_import(discovery):
    panel, window, book, _calls = discovery
    assert panel.search()
    finish_discovery(panel)
    select(panel, 'person')
    panel.gui.runtime.registry.interlock.halt()
    assert not panel.save_selected() and not panel.search()
    assert not window.find_professional_contacts()
    assert not book.all() and 'stop' in window.status.get().lower()


def test_cancel_discards_late_report_and_restores_controls(discovery):
    panel, _window, _book, _calls = discovery
    started, release = threading.Event(), threading.Event()
    spec = panel.gui.runtime.registry._tools['public_contact_search']
    original = spec.handler
    def slow(**arguments):
        started.set()
        release.wait(3)
        return original(**arguments)
    spec.handler = slow
    try:
        assert panel.search() and started.wait(1)
        panel.update()
        panel.cancel_button.invoke()
        assert not panel.busy and not panel.search_button.instate(['disabled'])
        assert not panel.search(), 'A cancelled in-flight call must not permit another concurrent worker'
        assert 'still finishing' in panel.status.get()
        release.set()
        panel.after(200, lambda: setattr(panel, '_test_pumped', True))
        finish_discovery(panel, lambda: getattr(panel, '_test_pumped', False))
        assert not panel.report
    finally:
        release.set()


def test_open_source_uses_guarded_existing_browser_and_does_not_contact_recipient(discovery):
    panel, _window, _book, _calls = discovery
    assert panel.search()
    finish_discovery(panel)
    select(panel, 'person')
    opened = []
    from app.research.safety import validate_public_url
    panel.gui.runtime.research = SimpleNamespace(
        fetcher=SimpleNamespace(validate_url=lambda url: validate_public_url(url, via_tor=True)),
        search_browser=lambda url, required: (opened.append((url, required)) or {'opened': True}))
    assert panel.open_source()
    finish_discovery(panel)
    assert opened == [('https://institute.example/people/ada-0', False)]
    assert 'does not verify identity' in panel.status.get()


def test_compact_discovery_keeps_tables_evidence_and_actions_reachable(discovery):
    panel, _window, _book, _calls = discovery
    panel.geometry('700x640-4000-4000')
    panel.update()
    assert panel.evidence.winfo_height() >= 90
    assert panel.tables['person'].winfo_height() >= 75
    for widget in (panel.search_button, panel.cancel_button, panel.save_button, panel.entries['official_url']):
        assert widget.winfo_ismapped()
        assert widget.winfo_rootx() + widget.winfo_width() <= panel.winfo_rootx() + panel.winfo_width()
        assert widget.winfo_rooty() + widget.winfo_height() <= panel.winfo_rooty() + panel.winfo_height()


def test_capture_reviewed_discovery_at_normal_and_compact_sizes(discovery):
    from tests.test_gui_screenshots import _grab
    panel, _window, _book, _calls = discovery
    assert panel.search()
    finish_discovery(panel)
    select(panel, 'person')
    for geometry, label in (('1000x820+20+20', '104-professional-contact-discovery'),
                            ('700x640+20+20', '105-professional-contact-discovery-compact')):
        panel.geometry(geometry)
        panel.update()
        _grab(panel.gui, label, panel)


def test_payload_rejects_fabricated_or_unaccepted_source_before_save(report):
    row = deepcopy(report['candidates'][0])
    row['source_hash'] = 'fabricated'
    with pytest.raises(ValueError, match='matching retrieved source'):
        discovery_contact_payload(row, 'person', report)
    report['sources'][0]['accepted'] = False
    with pytest.raises(ValueError, match='matching retrieved source'):
        discovery_contact_payload(report['candidates'][0], 'person', report)


def test_payload_rejects_source_mixed_channel_and_does_not_invent_permission(report):
    row = deepcopy(report['candidates'][0])
    row['channels'][0]['source_url'] = 'https://other.example/fake'
    with pytest.raises(ValueError, match='matching public professional source'):
        discovery_contact_payload(row, 'person', report)
    payload = discovery_contact_payload(report['candidates'][0], 'person', report)
    assert set(payload['channels'][0]) == {'kind', 'value', 'status', 'source_url'}
    assert 'identity is not verified' in payload['notes']


def test_deep_and_tor_controls_only_start_explicit_scoped_work(discovery):
    panel, _window, _book, calls = discovery
    panel.depth.set('deep')
    panel.depth_combo.event_generate('<<ComboboxSelected>>')
    panel.route.set('Public web through Tor')
    panel.update()
    assert panel.max_pages.get() == '12' and not calls
    assert panel.search()
    finish_discovery(panel)
    assert calls[0][0]['depth'] == 'deep' and calls[0][0]['tor_only'] is True
    assert calls[0][0]['max_pages'] == 12


def test_coverage_summary_distinguishes_unchecked_leads_from_absence(discovery, report):
    panel = discovery[0]
    report.update(route='public_web_via_tor', tor_required=True, searches_completed=3,
        coverage={'queries_planned': 5, 'pages_retrieved': 4, 'distinct_source_hosts': 2,
                  'unvisited_leads': 7, 'stop_reason': 'page_budget', 'queries': [{'query': 'official public profile', 'status': 'ok'}]})
    panel.paint(report)
    text = panel.evidence.get('1.0', 'end')
    assert 'public_web_via_tor' in text and 'Unchecked leads: 7' in text and 'Absent elsewhere: unknown' in text
    assert 'official public profile' in text


def test_tor_result_cannot_be_opened_directly_after_route_is_disabled(discovery, monkeypatch):
    panel = discovery[0]
    assert panel.search()
    finish_discovery(panel)
    select(panel, 'person')
    panel.report['tor_required'] = True
    from app.privacy_masks import ROUTE
    ROUTE.set(None)
    panel.gui.runtime.research = SimpleNamespace(fetcher=SimpleNamespace(validate_url=lambda url: pytest.fail('direct validation')))
    assert panel.open_source()
    finish_discovery(panel)
    assert 'requires Tor' in panel.status.get()


def test_organization_onion_handoff_copies_no_person_or_photo_and_does_not_search(discovery, monkeypatch):
    panel = discovery[0]
    draft = ttk.Entry(panel)
    fake = SimpleNamespace(search_kind=SimpleNamespace(set=lambda value: setattr(panel, '_test_mode', value)), query=draft,
                           _show_person_fields=lambda: None)
    monkeypatch.setattr(panel.gui, '_open_searches', lambda: None, raising=False)
    monkeypatch.setattr(panel.gui, '_research_studio', fake, raising=False)
    assert panel.research_handoff('onion')
    assert panel._test_mode == 'Onion (Tor)' and draft.get() == 'Example Institute'
    assert 'Ada' not in draft.get() and 'no search has started' in panel.status.get()
