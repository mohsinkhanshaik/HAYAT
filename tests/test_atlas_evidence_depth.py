"""Actual atlas requests and deterministic boundaries on claimed evidence."""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect

from app.intelligence.deep import DeepLook
from tests.test_atlas_workspace import atlas, browser, expanded  # noqa: F401


@pytest.mark.parametrize('levels,result', [([], 'unknown'), (['gap'], 'unknown'),
    (['info', 'gap'], 'unknown'), (['info'], 'calm'), (['watch', 'gap'], 'watch'), (['alert', 'gap'], 'alert')])
def test_missing_coverage_never_implies_calm(levels, result):
    assert DeepLook.situation([{'level': level} for level in levels]) == result


def test_model_context_is_complete_json_and_keeps_gaps_before_detail():
    calls = []
    def generate(messages, **_kwargs):
        calls.append(messages)
        return SimpleNamespace(content='A reading of supplied findings.', finish_reason='stop', model='fixture')
    findings = [{'level': 'info', 'title': 'ordinary ' + str(i), 'detail': 'x' * 1600,
                 'evidence': ['e' * 900] * 8, 'confidence': 'reported'} for i in range(60)]
    findings.append({'level': 'gap', 'title': 'Missing weather', 'detail': 'Weather failed.', 'evidence': [], 'confidence': 'high'})
    result = DeepLook(SimpleNamespace(model=SimpleNamespace(generate=generate)), None).read({
        'place': {'name': 'Fixture'}, 'situation': 'unknown', 'findings': findings})
    context = calls[0][1].content.split('\n', 1)[1]
    decoded = json.loads(context)
    assert len(context) <= 12000 and decoded['omitted_findings'] > 0
    assert decoded['findings'][0]['title'] == 'Missing weather'
    assert result['text'] and result['verified'] is False


@pytest.mark.parametrize('content,finish', [('', 'stop'), ('Unfinished and misleading', 'length'), ('Partial', 'max_tokens')])
def test_empty_or_truncated_readings_are_not_published(content, finish):
    model = SimpleNamespace(generate=lambda *_args, **_kwargs: SimpleNamespace(content=content, finish_reason=finish))
    result = DeepLook(SimpleNamespace(model=model), None).read({'findings': [], 'place': {}})
    assert not result['text'] and result['verified'] is False and 'incomplete or empty' in result['note']


def test_source_desk_reads_all_local_sources_and_updates_staleness(atlas, expanded):  # noqa: F811
    atlas.locator('#feed').select_option('earthquakes')
    expect(atlas.locator('#refresh')).to_be_enabled()
    state = expanded.store.states()['earthquakes']
    state['last_success_at'] = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    expanded.store.state('earthquakes', state)
    atlas.get_by_role('button', name='Source health', exact=True).click()
    expect(atlas.locator('#source-health-list')).to_contain_text('stale or missing')
    # The address now carries a view code after '#' (2026-11-08 shareable views): join routes to the page address.
    health = atlas.request.get(atlas.url.split('#')[0] + 'health').json()
    assert len(health['sources']) > 1 and health['checked_at'] and 'not a new publisher request' in health['basis']
    atlas.locator('#source-search').fill('earthquakes')
    # Every earthquake publisher matches the word (USGS, EMSC, BMKG, JMA, GeoNet); the stale one is among them.
    articles = atlas.locator('#source-health-list article')
    expect(articles.first).to_be_visible()
    assert all('earthquake' in text.lower() for text in articles.all_inner_texts())
    usgs = atlas.locator('#source-health-list article[data-source-key="earthquakes"]')
    expect(usgs).to_have_count(1)
    expect(usgs).to_contain_text('stale or missing')
    expect(usgs).to_contain_text('Last successful fetch')
    atlas.locator('#source-state').select_option('current')
    expect(usgs).to_have_count(0)
    atlas.locator('#source-state').select_option('attention')
    usgs.get_by_role('button', name='Show retained observations', exact=True).click()
    expect(atlas.locator('#details-panel')).to_be_hidden()
    expect(atlas.locator('#feed')).to_have_value('earthquakes')


def test_source_desk_errors_are_recoverable_and_do_not_claim_success(atlas):  # noqa: F811
    atlas.route('**/health', lambda route: route.fulfill(status=503, json={'error': 'Fixture local status unavailable'}))
    atlas.get_by_role('button', name='Source health', exact=True).click()
    expect(atlas.locator('#details')).to_contain_text('Source health unavailable')
    expect(atlas.locator('#details')).to_contain_text('Fixture local status unavailable')
    atlas.unroute('**/health')
    atlas.get_by_role('button', name='Try local status again', exact=True).click()
    expect(atlas.locator('#source-search')).to_be_visible()


def test_deep_unknown_gauge_has_no_safe_needle_and_explains_model_failure(atlas):  # noqa: F811
    atlas.route('**/deep?**', lambda route: route.fulfill(json={'place': {'name': 'Fixture missing sources'},
        'radius_km': 25, 'observed_at': '2026-11-07T12:00:00+00:00', 'situation': 'unknown',
        'findings': [{'level': 'gap', 'title': 'Weather unavailable', 'detail': 'Source failed.', 'evidence': [], 'confidence': 'high'}],
        'reading': {'text': '', 'note': 'The local model could not answer.'}, 'basis': 'Incomplete source coverage.'}))
    atlas.locator('#deep-centre').click()
    expect(atlas.locator('.deep-level')).to_have_text('LIMITED COVERAGE')
    expect(atlas.locator('.deep-gauge line')).to_have_count(0)
    expect(atlas.locator('#details')).to_contain_text('The local model could not answer.')


def test_evidence_and_source_controls_fit_phone_and_do_not_start_media(atlas):  # noqa: F811
    requests = []
    atlas.on('request', lambda request: requests.append(request.url))
    atlas.get_by_role('button', name='Source health', exact=True).click()
    expect(atlas.locator('#source-search')).to_be_visible()
    atlas.set_viewport_size({'width': 390, 'height': 844})
    atlas.locator('#source-search').fill('earthquakes')
    expect(atlas.locator('#source-health-list article[data-source-key="earthquakes"]')).to_have_count(1)
    assert atlas.evaluate('document.documentElement.scrollWidth <= innerWidth')
    folder = Path('.test-runs/evidence-depth-shots')
    folder.mkdir(parents=True, exist_ok=True)
    atlas.screenshot(path=str(folder / 'atlas-phone-sources.png'))
    assert not any('/frame?' in url or '/camera/play?' in url for url in requests)
