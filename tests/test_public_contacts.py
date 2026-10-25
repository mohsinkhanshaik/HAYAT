import hashlib
from types import SimpleNamespace

import pytest

from app.research.fetcher import FetchedResource
from app.research.models import SearchResult
from app.research.public_contacts import contact_controls, discover_contacts


URL = 'https://institute.example/people/ada'
BODY = '''<html><title>Example Institute directory</title><h1>Example Institute</h1>
<article class="staff-card"><h2>Ada Example</h2><p>Example Institute researcher</p>
<p>Ada Example professional email: <a href="mailto:ada@institute.example">Email</a></p></article>
<p>Example Institute press office professional email: <a href="mailto:press@institute.example">Email</a></p></html>'''


def manager(pages=None, leads=None):
    pages = pages or {URL: BODY}
    calls = []
    def fetch(url, **options):
        calls.append(url)
        body = pages[url].encode()
        assert len(body) <= options['max_bytes']
        return FetchedResource(url, url, 200, {'content-type': 'text/html'}, body, 1,
                               '2026-10-24T12:00:00Z', hashlib.sha256(body).hexdigest())
    search = SimpleNamespace(search=lambda *a, **k: leads if leads is not None else [SearchResult('Ada', URL)], last_errors=[])
    return SimpleNamespace(fetcher=SimpleNamespace(fetch=fetch), search=search), calls


def test_routes_are_fetched_attributed_and_person_and_team_remain_separate():
    parent, calls = manager()
    result = discover_contacts(parent, 'Ada Example', 'Example Institute', URL)
    assert result['completed'] and not result['identity_verified']
    assert calls == [URL]
    candidate = result['candidates'][0]
    assert [c['value'] for c in candidate['channels']] == ['ada@institute.example']
    assert [c['value'] for c in result['team_routes']] == ['press@institute.example']
    assert candidate['source_hash'] == hashlib.sha256(BODY.encode()).hexdigest()
    assert candidate['retrieved_at'] and candidate['source_url'] == URL
    assert result['sources'][0]['accepted'] and not result['errors']


def test_visible_plain_text_work_and_representative_routes_keep_their_owners():
    body = '''<h1>Ada Example</h1><p>Example Institute researcher</p>
    <p>Ada Example professional email: ada@institute.example</p>
    <dl><dt>Correspondence email</dt><dd>Chief of staff: chief@institute.example</dd></dl>
    <article class="staff-card"><h2>Unrelated Person</h2><p>Example Institute</p>
    <p>Unrelated Person professional email: unrelated@institute.example</p></article>'''
    parent, _ = manager({URL: body})
    result = discover_contacts(parent, 'Ada Example', 'Example Institute', URL)
    assert [row['value'] for row in result['candidates'][0]['channels']] == ['ada@institute.example']
    assert [row['value'] for row in result['team_routes']] == ['chief@institute.example']
    assert 'unrelated@' not in str(result)


def test_inconsistent_source_hash_cannot_create_contact_evidence():
    parent, _ = manager()
    fetch = parent.fetcher.fetch
    from dataclasses import replace
    parent.fetcher.fetch = lambda *a, **k: replace(fetch(*a, **k), sha256='fabricated')
    result = discover_contacts(parent, 'Ada Example', 'Example Institute', URL)
    assert not result['sources'] and not result['candidates'] and not result['team_routes']
    assert any(row['stage'] == 'evidence' for row in result['errors'])


def test_same_name_on_two_sources_never_merges_contact_routes():
    other = URL + '-other'
    parent, _ = manager({URL: BODY, other: BODY.replace('ada@', 'other@')},
                        [SearchResult('Ada', URL), SearchResult('Ada', other)])
    result = discover_contacts(parent, 'Ada Example', 'Example Institute')
    assert len(result['candidates']) == 2
    assert len({r['id'] for r in result['candidates']}) == 2
    assert all(len(r['channels']) == 1 for r in result['candidates'])


@pytest.mark.parametrize('bad', ['personal email', 'private contact', 'home email', 'mobile'])
def test_personal_labels_are_not_recast_as_professional_contacts(bad):
    parent, _ = manager({URL: BODY.replace('professional email', bad)})
    result = discover_contacts(parent, 'Ada Example', 'Example Institute', URL)
    assert all(not r['channels'] for r in result['candidates'])
    assert not result['team_routes']


def test_search_snippets_and_hidden_html_cannot_create_a_contact():
    parent, _ = manager({URL: '<h1>Example Institute</h1><div hidden>Ada Example work email ada@institute.example</div>'},
                        [SearchResult('Ada', URL, snippet='Ada Example email ada@institute.example')])
    result = discover_contacts(parent, 'Ada Example', 'Example Institute', URL)
    assert not result['candidates'] and not result['team_routes']


@pytest.mark.parametrize('url', ['http://127.0.0.1/private', 'http://localhost/profile',
                               'https://spokeo.com/Ada', 'https://instagram.com/ada',
                               'https://user:secret@institute.example/', 'https://institute.example/?token=secret'])
def test_restricted_seeds_are_rejected_before_search_or_fetch(url):
    parent, calls = manager()
    with pytest.raises(ValueError):
        discover_contacts(parent, 'Ada Example', 'Example Institute', url)
    assert calls == []


def test_provider_failure_still_reads_selected_page_and_returns_actual_error():
    parent, calls = manager()
    parent.search.search = lambda *a, **k: (_ for _ in ()).throw(TimeoutError('private provider detail'))
    result = discover_contacts(parent, 'Ada Example', 'Example Institute', URL)
    assert calls == [URL] and result['candidates']
    assert len(result['errors']) == 2 and 'private provider detail' not in str(result['errors'])


def test_cancellation_context_stops_before_any_request_and_does_not_leak():
    parent, calls = manager()
    with contact_controls(check=lambda: False):
        with pytest.raises(PermissionError, match='cancelled'):
            discover_contacts(parent, 'Ada Example', 'Example Institute', URL)
    assert not calls
    assert discover_contacts(parent, 'Ada Example', 'Example Institute', URL)['completed']


def test_scope_bounds_and_input_rejection():
    parent, calls = manager()
    for kwargs in ({'max_pages': 0}, {'max_pages': 13}, {'max_pages': True}):
        with pytest.raises(ValueError):
            discover_contacts(parent, 'Ada Example', 'Example Institute', **kwargs)
    with pytest.raises(ValueError):
        discover_contacts(parent, 'Ada Example', '')
    assert not calls


def test_route_change_discards_even_if_proxy_returns_to_same_value():
    from app.privacy_masks import ROUTE
    parent, _ = manager()
    original = parent.fetcher.fetch
    def fetch(*args, **kwargs):
        result = original(*args, **kwargs)
        old = ROUTE.proxy
        ROUTE.set('socks5://127.0.0.1:19999')
        ROUTE.set(old)
        return result
    parent.fetcher.fetch = fetch
    with pytest.raises(PermissionError, match='route changed'):
        discover_contacts(parent, 'Ada Example', 'Example Institute', URL)
