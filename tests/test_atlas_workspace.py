"""Real-browser checks for navigation, viewport counts, and media-free browsing."""
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.test_intelligence_resources import expanded as expanded_fixture
from tests.test_public_intelligence import make_due, quake


@pytest.fixture
def expanded(tmp_path):
    yield from expanded_fixture.__wrapped__(tmp_path)


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip('Chromium is not installed for real-browser atlas checks')
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def atlas(browser, expanded):
    page = browser.new_page(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(expanded.open_map()['map_url'])
    expect(page.locator('#loaded-count')).to_contain_text('records loaded')
    yield page
    page.close()
    assert not errors, errors


def test_regional_navigation_selection_and_reset_keep_media_explicit(atlas, expanded):
    requested = []
    atlas.on('request', lambda request: requested.append(request.url))
    atlas.get_by_role('button', name='India', exact=True).click()
    expect(atlas.locator('#loaded-count')).to_have_text(f"{expanded.points(query='India')['total']:,} records loaded")
    expect(atlas.locator('#visible-count')).to_have_text('4 records')
    expect(atlas.locator('.observation-card')).to_have_count(4)
    atlas.locator('.observation-card').filter(has_text='Shirdi').click()
    expect(atlas.locator('#details h1')).to_contain_text('Shirdi')
    expect(atlas.locator('#details-panel')).to_be_visible()
    expect(atlas.get_by_role('button', name='Play publisher video')).to_be_enabled()
    assert not any('/camera/play?' in url or '/frame?' in url for url in requested)
    atlas.get_by_role('button', name='Close observation details').click()
    expect(atlas.locator('#details-panel')).to_be_hidden()
    atlas.get_by_role('button', name='Makkah', exact=True).click()
    expect(atlas.locator('#view-name')).to_have_text('Makkah view')
    expect(atlas.locator('#record-count')).to_have_text('1')
    atlas.get_by_role('button', name='Reset', exact=True).click()
    expect(atlas.locator('#query')).to_have_value('')
    expect(atlas.locator('#loaded-count')).to_have_text(f"{expanded.points()['total']:,} records loaded")


def test_counts_and_browse_follow_visibility_filters(atlas):
    atlas.get_by_role('button', name='India', exact=True).click()
    expect(atlas.locator('#record-count')).to_have_text('4')
    expect(atlas.locator('#stream-count')).to_have_text('3')
    expect(atlas.locator('#still-count')).to_have_text('1')
    atlas.locator('#cameras').uncheck()
    expect(atlas.locator('#record-count')).to_have_text('0')
    expect(atlas.locator('#browse-list')).to_contain_text('No matching observations here')
    atlas.locator('#cameras').check()
    atlas.locator('#media').select_option('image')
    expect(atlas.locator('#record-count')).to_have_text('1')
    expect(atlas.locator('.observation-card')).to_have_count(1)


def test_sort_category_severity_and_dateline_distance(atlas):
    atlas.evaluate("""() => {
      rows = [
        {id:'far',title:'Alpha far',latitude:0,longitude:150,kind:'event',category:'earthquakes',severity:'high',flags:'!'},
        {id:'near',title:'Zulu near',latitude:0,longitude:-179.9,kind:'event',category:'floods',severity:'medium',flags:'+'}
      ];
      $('category').replaceChildren(new Option('All categories',''),new Option('Floods','floods'));
      map.setView([0,179.9],2); draw();
    }""")
    atlas.locator('#sort').select_option('nearest')
    expect(atlas.locator('.observation-card').first).to_have_attribute('data-record-id', 'near')
    from app.intelligence.live import distance_km
    centre = atlas.evaluate('() => ({lat:map.getCenter().lat,lon:map.getCenter().lng})')
    expected = distance_km(centre['lat'], centre['lon'], 0, -179.9)
    assert expected < 30
    expect(atlas.locator('.observation-card').first).to_contain_text(f'{expected:.1f} km')
    atlas.locator('#sort').select_option('title')
    expect(atlas.locator('.observation-card').first).to_have_attribute('data-record-id', 'far')
    atlas.locator('#category').select_option('floods')
    expect(atlas.locator('.observation-card')).to_have_count(1)
    atlas.locator('#severity').select_option('high')
    expect(atlas.locator('.observation-card')).to_have_count(0)


def test_more_results_are_accessible_beyond_the_first_forty(atlas):
    atlas.evaluate("""() => {
      rows=Array.from({length:85},(_,i)=>({id:String(i),title:'Camera '+String(i).padStart(3,'0'),latitude:25,
        longitude:0,kind:'public_camera',category:'cameras',media:'Refreshing still',flags:'i'}));
      map.setView([25,0],2);draw();
    }""")
    expect(atlas.locator('.observation-card')).to_have_count(40)
    atlas.get_by_role('button', name='Show 40 more').click()
    expect(atlas.locator('.observation-card')).to_have_count(80)
    atlas.get_by_role('button', name='Show 40 more').click()
    expect(atlas.locator('.observation-card')).to_have_count(85)


def test_new_filter_controls_remain_reachable_on_desktop_and_phone(atlas):
    directory = Path('.test-runs/upgrade-previews')
    directory.mkdir(parents=True, exist_ok=True)
    atlas.screenshot(path=str(directory / 'atlas-desktop.png'))
    atlas.set_viewport_size({'width':390, 'height':844})
    atlas.locator('#sort').scroll_into_view_if_needed()
    atlas.locator('#sort').select_option('title')
    expect(atlas.locator('#sort')).to_have_value('title')
    assert atlas.evaluate('document.body.scrollWidth <= window.innerWidth')
    atlas.screenshot(path=str(directory / 'atlas-phone.png'))


def test_fit_results_keeps_dateline_neighbors_together(atlas, expanded):
    expanded.fetcher.payloads['earthquakes']['features'] = [
        quake('east', 4, [179.5, 15, 2]), quake('west', 4, [-179.5, 15, 2])]
    make_due(expanded, 'earthquakes')
    expanded.refresh(['earthquakes'])
    atlas.locator('#feed').select_option('earthquakes')
    atlas.get_by_role('button', name='Apply search').click()
    expect(atlas.locator('#loaded-count')).to_have_text('2 records loaded')
    atlas.get_by_role('button', name='Fit results').click()
    expect(atlas.locator('#record-count')).to_have_text('2')
    bounds = atlas.evaluate('() => ({west: map.getBounds().getWest(), east: map.getBounds().getEast()})')
    assert bounds['east'] - bounds['west'] < 10


def test_street_switch_keeps_provider_credit_and_keyboard_search(atlas):
    # Tile delivery belongs to the publisher; navigation must work during tile failure.
    atlas.route('https://tile.openstreetmap.org/**', lambda route: route.abort())
    atlas.get_by_role('button', name='Street detail', exact=True).click()
    expect(atlas.locator('#street-toggle')).to_have_attribute('aria-pressed', 'true')
    expect(atlas.locator('.leaflet-control-attribution')).to_contain_text('OpenStreetMap contributors')
    assert atlas.evaluate('() => map.hasLayer(landLayer)'), 'overview remains available behind failed street tiles'
    atlas.get_by_role('button', name='Street detail', exact=True).click()
    expect(atlas.locator('.leaflet-control-attribution')).to_contain_text('Natural Earth')
    atlas.keyboard.press('/')
    expect(atlas.locator('#query')).to_be_focused()
    atlas.keyboard.type('Makkah')
    expect(atlas.locator('#query')).to_have_value('Makkah')
    atlas.get_by_role('button', name='Map information', exact=True).click()
    expect(atlas.get_by_role('dialog')).to_be_visible()
    atlas.keyboard.press('Escape')
    expect(atlas.get_by_role('dialog')).to_be_hidden()


@pytest.mark.parametrize('width', [390, 414, 768, 1024])
def test_mobile_has_no_horizontal_overflow_and_details_can_close(atlas, width):
    atlas.set_viewport_size({'width': width, 'height': 844})
    atlas.get_by_role('button', name='India', exact=True).click()
    expect(atlas.locator('#record-count')).to_have_text('4')
    assert atlas.evaluate('() => document.documentElement.scrollWidth <= window.innerWidth')
    atlas.locator('.observation-card').first.click()
    expect(atlas.locator('#details-panel')).to_be_visible()
    atlas.get_by_role('button', name='Close observation details').click()
    expect(atlas.locator('#details-panel')).to_be_hidden()
    assert atlas.evaluate('() => document.documentElement.scrollWidth <= window.innerWidth')


def test_pan_counts_only_the_visible_area_without_discarding_loaded_records(atlas):
    loaded = atlas.locator('#loaded-count').inner_text()
    atlas.evaluate('() => map.setView([-40, -150], 9, {animate: false})')
    expect(atlas.locator('#record-count')).to_have_text('0')
    expect(atlas.locator('.observation-card')).to_have_count(0)
    expect(atlas.locator('#loaded-count')).to_have_text(loaded)
