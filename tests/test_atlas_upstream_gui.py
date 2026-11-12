"""GUI adaptations reviewed against gods-eye-view, exercised in real Chromium."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect

from app.intelligence.map_server import IntelligenceMapServer, coordinate_place
from tests.test_atlas_workspace import atlas, browser, expanded  # noqa: F401


@pytest.mark.parametrize('text,point', [
    ('40.7128, -74.0060', (40.7128, -74.006)), ('geo:40.7128,-74.0060', (40.7128, -74.006)),
    ('40.7128 N 74.0060 W', (40.7128, -74.006)), ('33.9S, 151.2E', (-33.9, 151.2)),
    ('+90, +180', (90, 180)), ('.5, -.5', (.5, -.5)),
])
def test_coordinates_resolve_without_any_geocoder(text, point):
    value = coordinate_place(text)
    assert value['found'] and (value['latitude'], value['longitude']) == point
    assert IntelligenceMapServer.place(SimpleNamespace(manager=SimpleNamespace()), text)['found']


@pytest.mark.parametrize('text', ['geo:91,0', 'geo:0,181', 'geo:1,2,3', 'geo:1,2;crs=other',
                                 '91,0', '-40 N 70 W', 'geo:nan,0', 'geo:1,2;u=10'])
def test_invalid_or_unsupported_coordinates_are_correctable_and_never_geocoded(text):
    value = IntelligenceMapServer.place(SimpleNamespace(manager=SimpleNamespace()), text)
    assert value['found'] is False and value['error']


def test_an_address_is_not_misread_as_invalid_coordinates():
    assert coordinate_place('10 Downing Street, London') is None


def test_explicit_place_failure_preserves_query_focus_and_previous_map(atlas):  # noqa: F811
    before = atlas.evaluate('() => [map.getCenter().lat,map.getCenter().lng,map.getZoom()]')
    atlas.route('**/place?**', lambda route: route.fulfill(json={'found': False}))
    atlas.locator('#search-mode').select_option('place')
    atlas.locator('#query').fill('Unfindable sample place')
    atlas.get_by_role('button', name='Apply search', exact=True).click()
    expect(atlas.locator('#search-feedback')).to_contain_text('No matching place')
    expect(atlas.locator('#query')).to_be_focused()
    expect(atlas.locator('#query')).to_have_value('Unfindable sample place')
    assert atlas.evaluate('() => [map.getCenter().lat,map.getCenter().lng,map.getZoom()]') == before
    assert atlas.evaluate("$('query').selectionEnd - $('query').selectionStart") == len('Unfindable sample place')


def test_coordinate_gui_search_and_skip_link_work_without_external_geocoding(atlas):  # noqa: F811
    atlas.locator('#search-mode').select_option('place')
    atlas.locator('#query').fill('geo:40.7128,-74.0060')
    atlas.get_by_role('button', name='Apply search', exact=True).click()
    expect(atlas.locator('#search-feedback')).to_contain_text('Went to 40.7128, -74.0060')
    assert abs(atlas.evaluate('map.getCenter().lat') - 40.7128) < .001
    requested = []
    atlas.on('request', lambda request: requested.append(request.url))
    atlas.evaluate("() => { location.hash = 'query'; }")
    expect(atlas.locator('#query')).to_be_focused()
    assert not any('/event?id=query' in url for url in requested)


def test_observation_text_search_does_not_request_a_place(atlas):  # noqa: F811
    requested = []
    atlas.on('request', lambda request: requested.append(request.url))
    atlas.locator('#search-mode').select_option('records')
    atlas.locator('#query').fill('India')
    atlas.get_by_role('button', name='Apply search', exact=True).click()
    expect(atlas.locator('#loaded-count')).to_have_text('5 records loaded')  # text also matches the Indiana fixture
    assert not any('/place?' in url for url in requested)


def test_layer_desk_reports_empty_and_error_states_in_place(atlas):  # noqa: F811
    atlas.route('**/live?kind=aircraft**', lambda route: route.fulfill(json={
        'items': [], 'count': 0, 'radius_km': 20, 'source': 'fixture public feed'}))
    atlas.evaluate('() => map.setView([51.5,-.1],8,{animate:false})')
    atlas.get_by_role('button', name='Layers', exact=False).click()
    expect(atlas.locator('#layer-desk')).to_have_attribute('open', '')
    atlas.get_by_role('checkbox', name='Aircraft', exact=True).check()
    expect(atlas.locator('#aircraft-status')).to_have_attribute('data-state', 'empty')
    expect(atlas.locator('#aircraft-status')).to_contain_text('0 in the returned area')
    atlas.locator('#aircraft').uncheck()
    atlas.unroute('**/live?kind=aircraft**')
    atlas.route('**/live?kind=aircraft**', lambda route: route.fulfill(status=503, json={'error': 'source offline'}))
    atlas.locator('#aircraft').check()
    expect(atlas.locator('#aircraft-status')).to_have_attribute('data-state', 'error')
    expect(atlas.locator('#aircraft-status')).to_contain_text('source offline')
    folder = Path('.test-runs/upstream-gui-previews'); folder.mkdir(parents=True, exist_ok=True)
    atlas.screenshot(path=str(folder / 'layer-desk-desktop.png'))
    atlas.set_viewport_size({'width': 390, 'height': 844})
    atlas.locator('#aircraft-status').scroll_into_view_if_needed()
    assert atlas.evaluate('document.documentElement.scrollWidth <= innerWidth')
    atlas.screenshot(path=str(folder / 'layer-desk-phone.png'))


def test_turning_overlays_off_cancels_inflight_layers_and_stays_off_on_zoom(atlas):  # noqa: F811
    # Make a source ignore abort to exercise the generation checks too.
    atlas.evaluate("""() => {
      window.originalFetch=window.fetch;
      window.layerReplies=[];
      window.fetch=(url,options)=>String(url).startsWith('live?') ? new Promise(resolve=>layerReplies.push(resolve)) : originalFetch(url,options);
      map.setView([51.5,-.1],10,{animate:false});
    }""")
    atlas.get_by_role('button', name='Layers', exact=False).click()
    atlas.locator('#aircraft').check()
    expect(atlas.locator('#aircraft-status')).to_have_attribute('data-state', 'loading')
    atlas.locator('#clear-overlays').click()
    atlas.evaluate("""() => {
      layerReplies.forEach(resolve=>resolve(new Response(JSON.stringify({items:[{id:'late',flight:'late',latitude:51.5,longitude:-.1,altitude_ft:1000}],count:1,radius_km:20,source:'fixture'}),{headers:{'Content-Type':'application/json'}})));
      window.fetch=originalFetch; map.setZoom(11,{animate:false});
    }""")
    expect(atlas.locator('#aircraft-status')).to_have_text('Off')
    expect(atlas.locator('#streets')).not_to_be_checked()
    assert atlas.evaluate("() => LIVE.aircraft.markers.size === 0 && !map.hasLayer(LIVE.aircraft.layer)")
    assert atlas.evaluate('() => OPTIONAL_LAYERS.every(id => !$(id).checked)')


def test_superseded_place_search_cannot_move_the_map_back(atlas):  # noqa: F811
    atlas.evaluate("""() => {
      window.originalFetch=window.fetch; window.placeReplies={};
      window.fetch=(url,options)=>String(url).startsWith('place?') ? new Promise(resolve=>placeReplies[new URL(url,location.href).searchParams.get('name')]=resolve) : originalFetch(url,options);
    }""")
    atlas.locator('#search-mode').select_option('place')
    atlas.locator('#query').fill('Earlier place')
    atlas.get_by_role('button', name='Apply search', exact=True).click()
    atlas.locator('#query').fill('Newer place')
    atlas.get_by_role('button', name='Apply search', exact=True).click()
    atlas.evaluate("() => placeReplies['Newer place'](new Response(JSON.stringify({found:true,name:'Newer place',latitude:41.9,longitude:12.5})))")
    expect(atlas.locator('#view-name')).to_have_text('Newer place')
    atlas.evaluate("() => { placeReplies['Earlier place'](new Response(JSON.stringify({found:true,name:'Earlier place',latitude:48.8,longitude:2.3}))); window.fetch=originalFetch; }")
    expect(atlas.locator('#view-name')).to_have_text('Newer place')
    assert abs(atlas.evaluate('map.getCenter().lat') - 41.9) < .001
