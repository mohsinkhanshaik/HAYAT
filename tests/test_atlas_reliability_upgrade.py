"""Real browser recovery, navigation and readable retained-result states."""
from playwright.sync_api import expect
import pytest

from tests.test_atlas_workspace import atlas, browser, expanded  # noqa: F401
from tests.test_gui_intelligence import explorer  # noqa: F401


@pytest.mark.parametrize('text', ['open browser atlas', 'bring back the browser atlas', 'open brwser atlas',
                                 'launch world atlas in the browser', 'show world map'])
def test_browser_atlas_words_select_the_existing_tool(text):
    from app.intelligence.tools import direct_plan
    assert direct_plan(text, ['intelligence_map'])['tool'] == 'intelligence_map'


def test_atlas_launcher_is_visible_and_uses_existing_browser_route(explorer, monkeypatch):  # noqa: F811
    window, _gui = explorer
    calls = []
    monkeypatch.setattr(window, 'work', lambda name, args, *rest: calls.append((name, args)) or True)
    window.geometry('760x580-4000-4000')
    window.update()
    assert window.atlas_button.winfo_ismapped()
    assert window.atlas_button.winfo_rootx() + window.atlas_button.winfo_width() <= window.winfo_rootx() + window.winfo_width()
    window.atlas_button.invoke()
    assert calls[-1] == ('intelligence_map', {})


def test_focus_map_restores_panels_with_escape_and_search(atlas):  # noqa: F811
    atlas.get_by_role('button', name='Focus map', exact=True).click()
    expect(atlas.locator('.explorer')).to_be_hidden()
    expect(atlas.get_by_role('button', name='Show panels', exact=True)).to_have_attribute('aria-pressed', 'true')
    atlas.keyboard.press('Escape')
    expect(atlas.locator('.explorer')).to_be_visible()
    atlas.get_by_role('button', name='Focus map', exact=True).click()
    atlas.keyboard.press('/')
    expect(atlas.locator('#query')).to_be_focused()
    expect(atlas.locator('.explorer')).to_be_visible()


def test_camera_health_changes_update_visible_cards_and_filter(atlas):  # noqa: F811
    atlas.evaluate("""() => {
      rows=[{id:'one',title:'One camera',latitude:25,longitude:0,kind:'public_camera',category:'cameras',media:'Refreshing still',flags:'i'}];
      map.setView([25,0],3); draw();
    }""")
    expect(atlas.locator('.observation-card')).to_have_count(1)
    atlas.evaluate("() => { rows[0].flags='ib'; draw(); }")
    expect(atlas.locator('.observation-card')).to_contain_text('last check failed')
    atlas.locator('#hide-failed').check()
    expect(atlas.locator('.observation-card')).to_have_count(0)
    atlas.get_by_role('button', name='Reset', exact=True).click()
    expect(atlas.locator('#hide-failed')).not_to_be_checked()


def test_failed_search_labels_retained_results_and_retry_recovers(atlas):  # noqa: F811
    before = atlas.locator('#loaded-count').inner_text().split()[0]
    atlas.route('**/points?**', lambda route: route.fulfill(status=503, json={'error': 'temporary source failure'}))
    atlas.locator('#query').fill('camera no-matching-records')
    atlas.get_by_role('button', name='Apply search', exact=True).click()
    expect(atlas.locator('#load-error')).to_be_visible()
    expect(atlas.locator('#load-error')).to_contain_text('Previous results remain visible')
    expect(atlas.locator('#loaded-count')).to_have_text(f'{before} previous records retained')
    atlas.unroute('**/points?**')
    atlas.get_by_role('button', name='Try loading again', exact=True).click()
    expect(atlas.locator('#loaded-count')).to_have_text('0 records loaded')
    expect(atlas.locator('#load-error')).to_be_hidden()


def test_missing_land_geometry_does_not_block_observations(browser, expanded):  # noqa: F811
    page = browser.new_page(reduced_motion='reduce')
    try:
        page.route('**/land', lambda route: route.fulfill(status=503, json={'error': 'geometry missing'}))
        page.goto(expanded.open_map()['map_url'])
        expect(page.locator('#loaded-count')).to_contain_text('records loaded')
        page.get_by_role('button', name='India', exact=True).click()
        expect(page.locator('.observation-card')).to_have_count(4)
    finally:
        page.close()


def test_back_forward_and_new_branch_do_not_lose_navigation(atlas):  # noqa: F811
    atlas.evaluate('() => map.setView([51.5,-.1],11,{animate:false})')
    atlas.evaluate('() => map.setView([35.7,139.7],12,{animate:false})')
    atlas.get_by_role('button', name='← Back', exact=True).click()
    assert abs(atlas.evaluate('map.getCenter().lat') - 51.5) < .1
    atlas.get_by_role('button', name='Forward →', exact=True).click()
    assert abs(atlas.evaluate('map.getCenter().lat') - 35.7) < .1
    atlas.get_by_role('button', name='← Back', exact=True).click()
    atlas.evaluate('() => map.setView([48.8,2.3],10,{animate:false})')
    expect(atlas.locator('#view-forward')).to_be_disabled()


def test_saved_browser_view_survives_reload_and_keeps_media_explicit(atlas, expanded):  # noqa: F811
    requested = []
    atlas.on('request', lambda request: requested.append(request.url))
    atlas.locator('.saved-views summary').click()
    atlas.evaluate('() => map.setView([51.5,-.1],13,{animate:false})')
    atlas.locator('#media').select_option('image')
    expect(atlas.locator('#refresh')).to_be_enabled()
    atlas.locator('#hide-failed').check()
    atlas.evaluate("() => { document.getElementById('aircraft').checked = true; }")  # layer state only
    atlas.locator('#view-title').fill('London still desk')
    atlas.locator('#save-atlas-view').click()
    expect(atlas.locator('#saved-view-state')).to_contain_text('Saved London still desk')
    expect(atlas.locator('#saved-view-state')).to_contain_text('map layers are saved too')
    saved = expanded.named_views(action='open', name='London still desk')['view']['options']
    assert saved['atlas']['zoom'] == 13 and saved['atlas']['media'] == 'image'
    assert 'aircraft' in saved['atlas']['layers']  # saved views keep layers (2026-11-08); streets may be on at zoom 13
    atlas.reload()
    expect(atlas.locator('#loaded-count')).to_contain_text('records loaded')
    atlas.locator('.saved-views summary').click()
    expect(atlas.locator('#saved-atlas-views option[value="London still desk"]')).to_have_count(1)
    atlas.evaluate("() => { document.getElementById('aircraft').checked = false; }")  # opening must restore it
    atlas.locator('#saved-atlas-views').select_option('London still desk')
    atlas.locator('#open-atlas-view').click()
    expect(atlas.locator('#saved-view-state')).to_contain_text('Sources re-checked now')
    expect(atlas.locator('#media')).to_have_value('image')
    expect(atlas.locator('#hide-failed')).to_be_checked()
    expect(atlas.locator('#aircraft')).to_be_checked()
    assert atlas.evaluate('map.getZoom()') == 13
    assert not any('/frame?' in url or '/camera/play?' in url for url in requested)
    atlas.locator('#delete-atlas-view').click()
    expect(atlas.locator('#saved-view-state')).to_contain_text('Deleted London still desk')
    assert expanded.named_views()['views'] == []


def test_saved_view_writes_reject_cross_origin_and_invalid_options(atlas, expanded):  # noqa: F811
    url = expanded.open_map()['map_url'] + 'views'
    refused = atlas.request.post(url, headers={'Origin': 'https://unrelated.example'}, data={'action': 'save', 'name': 'bad'})
    assert refused.status == 403 and expanded.named_views()['views'] == []
    origin = url.split('/', 3)[:3]
    invalid = atlas.request.post(url, headers={'Origin': '/'.join(origin)}, data={
        'action': 'save', 'name': 'bad', 'data': {'atlas': {'zoom': 99}}})
    assert invalid.status == 400 and expanded.named_views()['views'] == []


def test_phone_focus_and_saved_view_controls_fit_the_viewport(atlas):  # noqa: F811
    from pathlib import Path
    folder = Path('.test-runs/atlas-recovery-previews')
    folder.mkdir(parents=True, exist_ok=True)
    atlas.set_viewport_size({'width': 390, 'height': 844})
    atlas.get_by_role('button', name='Focus map', exact=True).click()
    expect(atlas.locator('.explorer')).to_be_hidden()
    bounds = atlas.locator('#map').bounding_box()
    assert bounds['height'] >= 350 and bounds['width'] <= 390
    assert atlas.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    for control in atlas.locator('.map-actions button:visible, .telemetry').all():
        box = control.bounding_box()
        assert box['x'] >= 0 and box['x'] + box['width'] <= 390, box
    atlas.screenshot(path=str(folder / 'phone-focused-map.png'))
    atlas.get_by_role('button', name='Show panels', exact=True).click()
    atlas.locator('.saved-views summary').click()
    atlas.locator('#view-title').scroll_into_view_if_needed()
    expect(atlas.locator('#save-atlas-view')).to_be_visible()
    assert atlas.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    atlas.screenshot(path=str(folder / 'phone-saved-views.png'))


def test_concurrent_browser_and_native_saves_keep_every_named_view(expanded, monkeypatch):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    import time
    evidence = expanded._view_evidence({})
    def slow_evidence(_options):
        time.sleep(.01)  # overlap the read/modify/write operations
        return evidence
    monkeypatch.setattr(expanded, '_view_evidence', slow_evidence)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda n: expanded.named_views('save', f'Desk {n}', {}), range(12)))
    assert len(expanded.named_views()['views']) == 12


def test_saved_view_layers_are_limited_to_registered_layer_names():
    from app.intelligence.view import validate_view
    assert validate_view({'atlas': {'layers': ['aircraft', 'fires']}})['atlas']['layers'] == ['aircraft', 'fires']
    for bad in (['aircraft', 'secret'], 'aircraft', ['x'] * 20):
        with pytest.raises(ValueError, match='registered map layer'):
            validate_view({'atlas': {'layers': bad}})
