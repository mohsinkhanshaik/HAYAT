"""Atlas workspace UI (2026-11-08), checked in real Chromium: view codes in the address and in a share desk,
layer chips that say why a layer shows nothing, slow-request activity with Cancel, location status, and one
predictable Escape. Ideas from gods-eye-view (MIT); HAYAT's own implementation in atlas-ui.js."""
from base64 import b64decode
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from app.intelligence.live import LiveData
from app.intelligence.map_server import IntelligenceMapServer
from tests.test_atlas_accessibility import AUDIT
from tests.test_atlas_workspace import browser, expanded  # noqa: F401

PNG = b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=')
SHOTS = Path('.test-runs/helper6')
TILE_HOSTS = {'tile.openstreetmap.org', 'mesonet.agron.iastate.edu', 'gibs.earthdata.nasa.gov'}
HANG = """prefix => { window.originalFetch = window.originalFetch || window.fetch;
  window.fetch = (url, options = {}) => String(url).startsWith(prefix)
    ? new Promise((resolve, reject) => options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))))
    : originalFetch(url, options); }"""


def open_page(browser, url, size=None):  # noqa: F811
    """A page that records every host it contacts, answers tile hosts with a blank tile, and fails on any script error."""
    context = browser.new_context(viewport=size or {'width': 1536, 'height': 900}, reduced_motion='reduce',
                                  permissions=['clipboard-read', 'clipboard-write'])
    page = context.new_page()
    page.problems, page.hosts, page.requests = [], set(), []
    page.on('pageerror', lambda error: page.problems.append(str(error)))
    page.on('console', lambda msg: msg.type == 'error' and not msg.text.startswith('Failed to load resource')
            and page.problems.append(msg.text))
    page.on('request', lambda request: (page.hosts.add(urlsplit(request.url).hostname), page.requests.append(request.url)))
    page.route(lambda address: urlsplit(address).hostname != '127.0.0.1',
               lambda route: route.fulfill(status=200, content_type='image/png', body=PNG))
    # Flags any use of the browser's own location API; the atlas must never ask for it.
    page.add_init_script("""window.__geolocationUsed = false;
      for (const name of ['getCurrentPosition', 'watchPosition']) navigator.geolocation[name] = () => { window.__geolocationUsed = true; };""")
    page.goto(url)
    expect(page.locator('#loaded-count')).to_contain_text('records loaded')
    return page


@pytest.fixture
def page(browser, expanded):  # noqa: F811
    page = open_page(browser, expanded.open_map()['map_url'])
    yield page
    page.context.close()
    assert not page.problems, page.problems


def centre(page):
    return page.evaluate('() => [map.getCenter().lat, map.getCenter().lng, map.getZoom()]')


def base(page):
    return page.url.split('#')[0]


def aircraft_reply(route):
    route.fulfill(json={'items': [{'id': 'fixture1', 'flight': 'FIX1', 'latitude': 51.5, 'longitude': -.1, 'altitude_ft': 30000,
                                   'speed_kt': 400, 'track': 90}], 'count': 1, 'radius_km': 50, 'source': 'fixture ADS-B'})


# ---------------------------------------------------------------- view codes

def test_the_address_follows_the_view_and_a_new_page_restores_all_of_it(page, browser, expanded):  # noqa: F811
    page.route('**/live?kind=aircraft**', aircraft_reply)
    page.get_by_role('button', name='India', exact=True).click()
    expect(page.locator('#visible-count')).to_have_text('4 records')
    card = page.locator('.observation-card').filter(has_text='Shirdi')
    record = card.get_attribute('data-record-id')
    card.click()
    expect(page.locator('#details h1')).to_contain_text('Shirdi')
    page.locator('#sort').select_option('title')
    page.locator('#hide-failed').check()
    page.locator('#open-layers').click()
    page.get_by_role('checkbox', name='Aircraft', exact=True).check()
    page.wait_for_function("() => location.hash.includes('l=') && location.hash.includes('aircraft') && location.hash.includes('sel=')")
    code = page.evaluate('location.hash')
    token = urlsplit(base(page)).path.strip('/')
    assert token not in code and 'q=India' in code and 'sort=title' in code and 'only=working' in code
    page.locator('#share-desk summary').click()
    expect(page.locator('#view-code')).to_have_value(code)
    lat, lon, zoom = centre(page)

    other = open_page(browser, base(page) + code)
    try:
        other.route('**/live?kind=aircraft**', aircraft_reply)
        expect(other.locator('#details h1')).to_contain_text('Shirdi')
        restored = centre(other)
        assert abs(restored[0] - lat) < 1e-3 and abs(restored[1] - lon) < 1e-3 and restored[2] == zoom
        expect(other.locator('#query')).to_have_value('India')
        expect(other.locator('#loaded-count')).to_have_text(f"{expanded.points(query='India')['total']:,} records loaded")
        expect(other.locator('#sort')).to_have_value('title')
        expect(other.locator('#hide-failed')).to_be_checked()
        expect(other.locator('#aircraft')).to_be_checked()
        assert other.evaluate('selected') == record
        expect(other.locator('#share-state')).to_contain_text('Opened the view in the address')
        assert other.hosts <= {'127.0.0.1'} | TILE_HOSTS
        assert not other.problems, other.problems
    finally:
        other.context.close()


def test_reload_keeps_the_view_and_saved_views_still_open(page):
    page.evaluate('() => map.setView([51.5, -0.12], 11, {animate: false})')
    page.wait_for_function("() => location.hash.includes('c=51.5')")
    page.reload()
    expect(page.locator('#loaded-count')).to_contain_text('records loaded')
    lat, lon, zoom = centre(page)
    assert abs(lat - 51.5) < 1e-3 and abs(lon + .12) < 1e-3 and zoom == 11


def test_a_link_from_another_session_opens_only_its_view_code_and_bad_codes_are_refused(page):
    page.locator('#share-desk summary').click()
    old_link = ('http://127.0.0.1:9/0123456789abcdef0123456789abcdef0123456789abcdef/'
                '#v=1&c=48.85660,2.35220&z=12&sort=title&hide=events&l=streets,bogus')
    page.locator('#open-view-code').fill(old_link)
    page.get_by_role('button', name='Open', exact=True).click()
    expect(page.locator('#share-state')).to_contain_text('Opened the view code')
    expect(page.locator('#share-state')).to_contain_text('ignored unknown layer “bogus”')
    lat, lon, zoom = centre(page)
    assert abs(lat - 48.8566) < 1e-4 and abs(lon - 2.3522) < 1e-4 and zoom == 12
    expect(page.locator('#events')).not_to_be_checked()
    expect(page.locator('#sort')).to_have_value('title')
    expect(page.locator('#streets')).to_be_checked()
    assert not any(':9/' in url for url in page.requests)

    for bad, words in (('#v=1&c=999,0&z=3', 'View code not opened'), ('#v=1&c=10,10&z=40', 'View code not opened'),
                       ('#v=2&c=10,10', 'version 2'), ('hello there', 'not an atlas view code')):
        page.locator('#open-view-code').fill(bad)
        page.locator('#open-view-code').press('Enter')
        expect(page.locator('#share-state')).to_contain_text(words)
        assert centre(page)[2] == 12 and abs(centre(page)[0] - 48.8566) < 1e-4

    page.locator('#open-view-code').fill('#v=1&c=10,10&z=4&q=C:%5CUsers%5Cowner%5Cnotes.txt')
    page.get_by_role('button', name='Open', exact=True).click()
    expect(page.locator('#share-state')).to_contain_text('ignored the search text')
    expect(page.locator('#query')).to_have_value('')
    assert page.hosts <= {'127.0.0.1', 'tile.openstreetmap.org'}


def test_private_looking_search_text_never_enters_the_address(page):
    page.locator('#search-mode').select_option('records')
    page.locator('#query').fill('D:\\AI\\data\\secret.db')
    page.get_by_role('button', name='Apply search', exact=True).click()
    expect(page.locator('#loaded-count')).to_have_text('0 records loaded')
    page.locator('#share-desk summary').click()
    expect(page.locator('#view-code-note')).to_contain_text('Left out: the search text')
    code = page.evaluate('location.hash')
    assert code.startswith('#v=1&') and 'q=' not in code and 'secret' not in code


def test_copy_puts_only_the_view_code_on_the_clipboard(page):
    page.locator('#share-desk summary').click()
    page.get_by_role('button', name='Copy', exact=True).click()
    expect(page.locator('#share-state')).to_contain_text('View code copied')
    copied = page.evaluate('navigator.clipboard.readText()')
    assert copied.startswith('#v=1&c=') and urlsplit(base(page)).path.strip('/') not in copied


# ---------------------------------------------------------------- layer chips

def test_layer_chips_name_the_reason_and_offer_the_remedy(page, expanded):  # noqa: F811
    calls = []

    def ships(url):
        calls.append(url)
        return {'features': [{'geometry': {'coordinates': [21.0, 59.5]}, 'properties': {'mmsi': 230000001, 'sog': 12.3, 'cog': 90}}]}
    expanded.live = LiveData(ships)
    page.route('**/live?kind=aircraft**', aircraft_reply)
    page.evaluate('() => map.setView([51.5, -0.1], 3, {animate: false})')
    page.locator('#open-layers').click()
    page.get_by_role('checkbox', name='Aircraft', exact=True).check()
    aircraft = page.locator('.layer-chip[data-layer=aircraft]')
    expect(aircraft).to_have_attribute('data-state', 'zoom')
    expect(aircraft.locator('.chip-main')).to_have_text('Aircraft · zoom in')
    assert 'Zoom in to level 6' in aircraft.locator('.chip-main').get_attribute('aria-label')
    aircraft.locator('.chip-main').click()
    assert centre(page)[2] == 6
    expect(aircraft).to_have_attribute('data-state', 'ready', timeout=10000)
    expect(aircraft.locator('.chip-main')).to_have_text('Aircraft · 1')

    page.get_by_role('checkbox', name='Ships', exact=True).check()
    ships_chip = page.locator('.layer-chip[data-layer=vessels]')
    expect(ships_chip).to_have_attribute('data-state', 'coverage')
    expect(ships_chip.locator('.chip-main')).to_have_text('Ships · no coverage here')
    expect(page.locator('#vessels-status')).to_contain_text('covers the Baltic only')
    assert calls == [], 'outside its coverage the ship source is not asked'
    ships_chip.locator('.chip-main').click()
    page.wait_for_function('() => Math.abs(map.getCenter().lat - 59.5) < .01')
    expect(ships_chip).to_have_attribute('data-state', 'ready', timeout=12000)
    expect(ships_chip.locator('.chip-main')).to_have_text('Ships · 1')
    assert len(calls) == 1

    page.get_by_role('checkbox', name='Fires · last 24 h', exact=True).check()
    fires = page.locator('.layer-chip[data-layer=fires]')
    expect(fires).to_have_attribute('data-state', 'error')
    expect(fires.locator('.chip-main')).to_have_text('Fires · unavailable')
    assert 'not available in this session' in fires.locator('.chip-main').get_attribute('aria-label')
    assert 'Check again now' in fires.locator('.chip-main').get_attribute('aria-label')

    page.get_by_role('checkbox', name='Weather radar · US', exact=True).check()
    radar = page.locator('.layer-chip[data-layer=radar]')
    expect(radar).to_have_attribute('data-state', 'coverage')
    radar.locator('.chip-main').click()
    expect(radar).to_have_attribute('data-state', 'enabled')
    expect(page.locator('#view-name')).to_have_text('United States')
    # Radar alone caps the map at zoom 12; street detail joins there and allows zooming further, as for a user.
    page.evaluate('() => map.setZoom(12, {animate: false})')
    expect(page.locator('#streets')).to_be_checked()
    page.evaluate('() => map.setZoom(14, {animate: false})')
    expect(radar.locator('.chip-main')).to_have_text('Radar · zoom out')
    radar.locator('.chip-main').click()
    assert centre(page)[2] == 12
    expect(radar).to_have_attribute('data-state', 'enabled')

    # × turns the layer off through its own checkbox, and focus stays among the chips.
    fires.get_by_role('button', name='Turn Fires off').click()
    expect(page.locator('#fires')).not_to_be_checked()
    expect(fires).to_have_count(0)
    expect(page.locator('#fires-status')).to_have_text('Off')
    assert page.evaluate("document.activeElement.classList.contains('chip-main')")
    assert page.hosts <= {'127.0.0.1'} | TILE_HOSTS


def test_a_layer_paused_while_hidden_checks_again_when_the_tab_returns(page):
    page.route('**/live?kind=aircraft**', aircraft_reply)
    page.evaluate("""() => { map.setView([51.5, -0.1], 8, {animate: false});
      Object.defineProperty(document, 'hidden', {value: true, configurable: true});
      $('aircraft').checked = true; $('aircraft').dispatchEvent(new Event('change', {bubbles: true})); }""")
    chip = page.locator('.layer-chip[data-layer=aircraft]')
    expect(chip).to_have_attribute('data-state', 'paused')
    expect(chip.locator('.chip-main')).to_have_text('Aircraft · paused')
    page.evaluate("""() => { Object.defineProperty(document, 'hidden', {value: false, configurable: true});
      document.dispatchEvent(new Event('visibilitychange')); }""")
    expect(chip).to_have_attribute('data-state', 'ready')


# ---------------------------------------------------------------- activity and cancel

def test_fast_requests_do_not_flash_the_activity_notice(page):
    page.evaluate("""() => { window.__activityShown = false;
      new MutationObserver(() => { if (!$('activity').hidden) window.__activityShown = true; })
        .observe($('activity'), {attributes: true}); }""")
    page.get_by_role('button', name='India', exact=True).click()
    expect(page.locator('#visible-count')).to_have_text('4 records')
    page.locator('.observation-card').first.click()
    expect(page.locator('#details h1')).not_to_have_text('Loading evidence…')
    assert page.evaluate('window.__activityShown') is False


def test_a_slow_deep_look_is_announced_timed_and_cancellable(page):
    page.evaluate(HANG, 'deep?')
    page.locator('#deep-centre').click()
    expect(page.locator('#activity')).to_be_visible()
    expect(page.locator('#activity')).to_contain_text('Deep look')
    expect(page.locator('#activity .activity-time')).to_contain_text(' s')
    expect(page.locator('#details .cancel-request')).to_be_visible()
    page.evaluate('() => { for (const task of activity.values()) task.started -= 20000; renderActivity(); }')
    expect(page.locator('#activity')).to_contain_text('taking longer than usual')
    expect(page.locator('#activity-live')).to_contain_text('You can cancel it')
    page.get_by_role('button', name='Cancel: Deep look').click()
    expect(page.locator('#details h1')).to_have_text('Cancelled')
    expect(page.locator('#activity')).to_be_hidden()
    page.get_by_role('button', name='Try again', exact=True).click()
    expect(page.locator('#activity')).to_be_visible()
    # Closing the panel stops waiting too; reopening it says what happened.
    page.locator('#close-details').click()
    expect(page.locator('#activity')).to_be_hidden()
    page.locator('#toggle-details').click()
    expect(page.locator('#details h1')).to_have_text('Cancelled')


def test_slow_place_search_and_observation_load_can_be_cancelled(page):
    before = centre(page)
    page.evaluate(HANG, 'place?')
    page.locator('#search-mode').select_option('place')
    page.locator('#query').fill('Somewhere slow')
    page.get_by_role('button', name='Apply search', exact=True).click()
    page.get_by_role('button', name='Cancel: Finding the place').click()
    expect(page.locator('#search-feedback')).to_contain_text('Place search cancelled')
    assert centre(page) == before
    loaded = page.locator('#loaded-count').inner_text().split()[0]
    page.evaluate(HANG, 'points?')
    page.locator('#refresh').click()
    page.get_by_role('button', name='Cancel: Reading observations').click()
    expect(page.locator('#status')).to_contain_text('Stopped reading observations')
    expect(page.locator('#loaded-count')).to_have_text(f'{loaded} previous records retained')
    expect(page.locator('#refresh')).to_be_enabled()
    expect(page.locator('#activity')).to_be_hidden()


# ---------------------------------------------------------------- location status

FIX = {'method': 'windows_location_service', 'precise': True, 'latitude': 40.7128, 'longitude': -74.006, 'accuracy_m': 35,
       'source': 'Windows Location service on this computer (GPS, Wi-Fi or cell positioning)',
       'observed_at': '2026-11-08T10:00:00+00:00', 'windows_location': {'status': 'Ready', 'permission': 'Allowed'},
       'place': {'label': 'New York, United States'}, 'network': 'Example ISP', 'time_zone': 'EST'}


def test_my_location_says_how_it_was_read_and_never_uses_the_browser_or_the_last_place(page, expanded):  # noqa: F811
    expanded.fusion = SimpleNamespace(my_location=lambda: dict(FIX), last_place={'name': 'Explored', 'latitude': 1, 'longitude': 1})
    reply = page.request.get(base(page) + 'location').json()
    assert reply['found'] and reply['accuracy_m'] == 35 and reply['windows'] == {'status': 'Ready', 'permission': 'Allowed'}
    assert 'network' not in reply and 'time_zone' not in reply, 'nothing beyond how the position was read reaches the page'
    page.locator('#locate').click()
    status = page.locator('#location-status')
    expect(status).to_have_text('◎ Your location · ±35 m')
    expect(status).to_have_attribute('data-state', 'precise')
    lat, lon, zoom = centre(page)
    assert abs(lat - 40.7128) < 1e-3 and abs(lon + 74.006) < 1e-3 and zoom == 14
    status.click()
    details = page.locator('#details')
    for text in ('LOCATION STATUS', 'Method: Windows Location service', 'Accuracy: within about 35 m',
                 'Windows location permission: Allowed', 'New York, United States'):
        expect(details).to_contain_text(text)
    expect(details).to_contain_text('Browser location permission: not requested by the atlas')
    page.locator('#share-desk summary').click()
    expect(page.locator('#view-code-note')).to_contain_text('near this computer’s location')
    page.get_by_role('button', name='Clear from map', exact=True).click()
    expect(status).to_be_hidden()
    assert page.evaluate('locationLayer.getLayers().length') == 0
    assert page.evaluate('window.__geolocationUsed') is False
    assert page.hosts <= {'127.0.0.1', 'tile.openstreetmap.org'}


def test_approximate_and_unavailable_locations_are_labelled_honestly(page, expanded):  # noqa: F811
    expanded.fusion = SimpleNamespace(my_location=lambda: {
        'method': 'public_ip_lookup', 'precise': False, 'latitude': 48.85, 'longitude': 2.35,
        'accuracy': 'city level at best', 'source': 'public IP address lookup (fixture)',
        'why_not_precise': 'Windows location is turned off or desktop apps are not allowed to use it.',
        'windows_location': {'permission': 'Denied'}})
    page.locator('#locate').click()
    status = page.locator('#location-status')
    expect(status).to_have_text('◎ Your location · approximate')
    status.click()
    expect(page.locator('#details')).to_contain_text('Accuracy: approximate · city level at best')
    expect(page.locator('#details')).to_contain_text('Why not precise: Windows location is turned off')

    def unavailable():
        raise ValueError('Device location is unavailable.')
    expanded.fusion = SimpleNamespace(my_location=unavailable)
    page.get_by_role('button', name='Read location again', exact=True).click()
    expect(status).to_have_text('◎ Location unavailable')
    expect(page.locator('#status')).to_contain_text('Device location is unavailable')
    status.click()
    expect(page.locator('#details h1')).to_have_text('Location unavailable')
    expect(page.locator('#details')).to_contain_text('Device location is unavailable')


# ---------------------------------------------------------------- Escape

def test_escape_closes_one_thing_at_a_time_and_returns_focus(page):
    page.get_by_role('button', name='India', exact=True).click()
    expect(page.locator('#visible-count')).to_have_text('4 records')
    card = page.locator('.observation-card').first
    card.click()
    expect(page.locator('#details-panel')).to_be_visible()
    page.locator('#details button').first.focus()
    page.keyboard.press('Escape')
    expect(page.locator('#details-panel')).to_be_hidden()
    assert page.evaluate('document.activeElement.classList.contains("observation-card")')

    card.click()
    expect(page.locator('#details-panel')).to_be_visible()
    page.get_by_role('button', name='Map information', exact=True).click()
    page.keyboard.press('Escape')
    expect(page.get_by_role('dialog')).to_be_hidden()
    expect(page.locator('#details-panel')).to_be_visible()

    page.locator('#open-layers').click()
    page.locator('#streets').focus()
    page.keyboard.press('Escape')
    expect(page.locator('#layer-desk')).not_to_have_attribute('open', '')
    expect(page.locator('#layer-summary')).to_be_focused()
    expect(page.locator('#details-panel')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.locator('#details-panel')).to_be_hidden()
    expect(page.locator('#layer-summary')).to_be_focused()

    page.evaluate('() => map.setView([-40, -150], 9, {animate: false})')
    page.locator('#video-wall').click()
    expect(page.locator('#wall')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.locator('#wall')).to_be_hidden()
    expect(page.locator('#video-wall')).to_have_attribute('aria-pressed', 'false')


# ---------------------------------------------------------------- layout and accessibility

@pytest.mark.parametrize('size', [{'width': 1536, 'height': 900}, {'width': 390, 'height': 844}])
def test_new_controls_fit_pass_the_audit_and_are_screenshotted(browser, expanded, size):  # noqa: F811
    expanded.fusion = SimpleNamespace(my_location=lambda: dict(FIX))
    page = open_page(browser, expanded.open_map()['map_url'], size)
    try:
        page.evaluate("""() => { map.setView([51.5, -0.1], 4, {animate: false});
          for (const id of ['aircraft', 'fires']) { $(id).checked = true; $(id).dispatchEvent(new Event('change', {bubbles: true})); } }""")
        expect(page.locator('.layer-chip[data-layer=fires]')).to_have_attribute('data-state', 'error')
        expect(page.locator('.layer-chip[data-layer=aircraft]')).to_have_attribute('data-state', 'zoom')
        page.locator('#locate').click()
        expect(page.locator('#location-status')).to_be_visible()
        page.evaluate('() => map.setView([51.5, -0.1], 4, {animate: false})')
        page.evaluate(HANG, 'points?')
        page.locator('#refresh').click()
        expect(page.locator('#activity')).to_be_visible()
        width = size['width']
        for selector in ('#location-status', '.layer-chip', '#activity', '.view-label'):
            for box in [element.bounding_box() for element in page.locator(selector).all() if element.is_visible()]:
                assert box['x'] >= 0 and box['x'] + box['width'] <= width + .5, (selector, box)
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"atlas-chips-activity-{width}.png"))
        page.locator('#share-desk summary').click()
        page.locator('#share-desk').scroll_into_view_if_needed()
        page.screenshot(path=str(SHOTS / f"atlas-share-desk-{width}.png"), full_page=width < 700)
        report = page.evaluate(AUDIT)
        assert not report['overflow'] and page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert report['unnamed'] == [], report['unnamed']
        assert report['lowContrast'] == [], report['lowContrast']
        page.get_by_role('button', name='Cancel: Reading observations').click()
        page.locator('#location-status').click()
        expect(page.locator('#details')).to_contain_text('LOCATION STATUS')
        page.screenshot(path=str(SHOTS / f"atlas-location-desk-{width}.png"))
        report = page.evaluate(AUDIT)
        assert report['unnamed'] == [] and report['lowContrast'] == [], report
        assert page.hosts <= {'127.0.0.1'} | TILE_HOSTS
        assert not page.problems, page.problems
    finally:
        page.context.close()


# ---------------------------------------------------------------- server

def test_location_route_reports_reasons_and_only_reading_details():
    server = IntelligenceMapServer(SimpleNamespace())
    try:
        assert server.my_location() == {'found': False, 'reason': "This computer's location is not available in this session."}

        def unavailable():
            raise ValueError('Device location is unavailable.')
        server.manager = SimpleNamespace(fusion=SimpleNamespace(my_location=unavailable))
        assert server.my_location() == {'found': False, 'reason': 'Device location is unavailable.'}
        server.manager = SimpleNamespace(fusion=SimpleNamespace(my_location=lambda: dict(FIX)))
        value = server.my_location()
        assert value['found'] and value['label'] == 'New York, United States' and value['precise'] is True
        assert set(value) <= {'found', 'latitude', 'longitude', 'label', 'windows', *IntelligenceMapServer.LOCATION_FIELDS}
        server.manager = SimpleNamespace(fusion=SimpleNamespace(my_location=lambda: {'method': 'none', 'precise': False,
                                                                                     'note': 'No location could be read.'}))
        assert server.my_location()['found'] is False and server.my_location()['reason'] == 'No location could be read.'
    finally:
        server.close()


def test_ship_coverage_hint_matches_where_the_ship_source_answers():
    calls = []
    live = LiveData(lambda url: calls.append(url) or {'features': []})
    server = IntelligenceMapServer(SimpleNamespace(live=live))
    try:
        south, north, west, east = IntelligenceMapServer.VESSEL_COVERAGE
        for lat, lon in ((south - .1, 20), (north + .1, 20), (60, west - .1), (60, east + .1)):
            assert live.vessels(lat, lon, 100) == [] and calls == []
            value = server.live_layer({'kind': 'vessels', 'lat': str(lat), 'lon': str(lon), 'radius_km': '100'})
            assert 'covers the Baltic only' in value['outside_coverage'] and calls == []
        value = server.live_layer({'kind': 'vessels', 'lat': str(south + .1), 'lon': str(west + .1), 'radius_km': '100'})
        assert 'outside_coverage' not in value and len(calls) == 1
    finally:
        server.close()
