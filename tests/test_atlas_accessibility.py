"""The browser atlas is usable by keyboard, screen reader and touch, and readable (2026-11-07 audit).

Before: a keyboard user had to Tab through every observation to reach the map, one severe-event marker was
announced only as "button", the 14 layer/filter checkboxes were 14 px, and map labels were 8-9 px at 2.7-4.2:1
contrast. These checks run the same audit on the real page in Chromium.
"""
import pytest
from playwright.sync_api import expect

from tests.test_atlas_workspace import atlas, browser, expanded  # noqa: F401

AUDIT = r"""
() => {
  const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const name = el => (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') && document.getElementById(el.getAttribute('aria-labelledby'))?.textContent
    || (el.labels && [...el.labels].map(l => l.textContent).join(' ')) || el.getAttribute('title') || el.textContent || el.getAttribute('placeholder') || el.value || '').trim();
  const controls = [...document.querySelectorAll('button, a[href], input, select, textarea, [role=button], [tabindex]')].filter(visible);
  const unnamed = controls.filter(el => !name(el) && el.type !== 'hidden').map(el => el.outerHTML.slice(0, 140));
  const placeholderOnly = controls.filter(el => (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') && el.type !== 'checkbox'
     && !el.getAttribute('aria-label') && !(el.labels && el.labels.length)).map(el => el.id || el.outerHTML.slice(0, 100));
  const small = controls.filter(el => { const r = el.getBoundingClientRect(); return r.width < 24 || r.height < 24; })
     .map(el => `${el.id || el.textContent.trim().slice(0, 24) || el.tagName} ${Math.round(el.getBoundingClientRect().width)}x${Math.round(el.getBoundingClientRect().height)}`);
  const lum = c => { const m = c.match(/[\d.]+/g); if (!m) return null; const [r, g, b] = m.slice(0, 3).map(v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; });
    return { L: .2126 * r + .7152 * g + .0722 * b, a: m[3] === undefined ? 1 : +m[3] }; };
  const bg = el => { for (let e = el; e; e = e.parentElement) { const c = getComputedStyle(e).backgroundColor; const l = lum(c); if (l && l.a > .5) return l.L; } return lum('rgb(255,255,255)').L; };
  const lowContrast = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el) || ![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
    const fg = lum(getComputedStyle(el).color); if (!fg) continue; const b = bg(el);
    const ratio = (Math.max(fg.L, b) + .05) / (Math.min(fg.L, b) + .05);
    const size = parseFloat(getComputedStyle(el).fontSize), bold = +getComputedStyle(el).fontWeight >= 700;
    const need = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
    if (ratio < need) lowContrast.push(`${ratio.toFixed(2)}<${need} ${size}px "${el.textContent.trim().slice(0, 40)}" (${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}.${el.className})`);
  }
  const focusRules = [...document.styleSheets].flatMap(s => { try { return [...s.cssRules].map(r => r.cssText); } catch { return []; } })
     .filter(t => t.includes(':focus')).length;
  return { lang: document.documentElement.lang, title: document.title, controls: controls.length, unnamed, placeholderOnly,
           small: small.slice(0, 40), smallCount: small.length, lowContrast: lowContrast.slice(0, 40), lowContrastCount: lowContrast.length,
           focusRules, overflow: document.documentElement.scrollWidth > innerWidth,
           landmarks: [...document.querySelectorAll('main, nav, header, aside, [role=main], [role=navigation], [role=complementary], [role=region]')].length,
           liveRegions: [...document.querySelectorAll('[aria-live], [role=status], [role=alert]')].map(e => e.id || e.tagName) };
}
"""

@pytest.mark.parametrize('size', [{'width': 1536, 'height': 900}, {'width': 390, 'height': 844}])
def test_every_control_is_named_and_all_text_meets_contrast(atlas, size):  # noqa: F811
    atlas.set_viewport_size(size)
    report = atlas.evaluate(AUDIT)
    assert report['lang'] == 'en' and not report['overflow']
    assert report['unnamed'] == [], report['unnamed']
    assert report['lowContrast'] == [], report['lowContrast']
    assert report['liveRegions'], 'status changes must be announced'


def test_checkboxes_are_touch_sized(atlas):  # noqa: F811
    atlas.set_viewport_size({'width': 390, 'height': 844})
    for box in atlas.locator('input[type=checkbox]').all():
        if box.is_visible():
            size = box.bounding_box()
            assert size['width'] >= 20 and size['height'] >= 20
    for row in atlas.locator('label.check').all():
        if row.is_visible():
            assert row.bounding_box()['height'] >= 24


def test_tab_reaches_the_map_without_walking_every_observation(atlas):  # noqa: F811
    cards = atlas.locator('#browse-list .observation-card')
    expect(cards.first).to_be_visible()
    assert cards.count() >= 3
    assert atlas.locator('#browse-list .observation-card[tabindex="0"]').count() == 1
    atlas.locator('#query').focus()
    for _ in range(40):
        atlas.keyboard.press('Tab')
        if atlas.evaluate('document.activeElement.id') == 'map':
            break
    else:
        pytest.fail('Tab never reached the map')


def test_arrow_keys_move_through_the_observations_and_enter_opens_one(atlas):  # noqa: F811
    cards = atlas.locator('#browse-list .observation-card')
    expect(cards.first).to_be_visible()
    cards.first.focus()
    atlas.keyboard.press('ArrowDown')
    assert atlas.evaluate('document.activeElement === document.querySelectorAll("#browse-list .observation-card")[1]')
    atlas.keyboard.press('End')
    assert atlas.evaluate('document.activeElement === [...document.querySelectorAll("#browse-list .observation-card")].at(-1)')
    atlas.keyboard.press('Home')
    assert atlas.evaluate('document.activeElement === document.querySelector("#browse-list .observation-card")')
    assert atlas.locator('#browse-list .observation-card[tabindex="0"]').count() == 1
    assert 'arrow keys' in atlas.locator('#browse-list').get_attribute('aria-label')
    atlas.keyboard.press('Enter')
    expect(atlas.locator('#details')).not_to_be_empty()


def test_skip_links_reach_the_map_and_open_details(atlas):  # noqa: F811
    if atlas.locator('#details-panel').is_visible():
        atlas.locator('#toggle-details').click()  # start from closed details, as on a narrow screen
    atlas.locator('#skip-details').focus()
    atlas.keyboard.press('Enter')
    expect(atlas.locator('#details-panel')).to_be_visible()
    assert atlas.evaluate('document.activeElement.id') == 'details'
    atlas.locator('#skip-map').focus()
    atlas.keyboard.press('Enter')
    assert atlas.evaluate('document.activeElement.id') == 'map'


def test_event_and_cluster_markers_say_what_they_are(atlas):  # noqa: F811
    atlas.set_viewport_size({'width': 1536, 'height': 900})
    titled = atlas.locator('.leaflet-marker-icon.pulse[title], .leaflet-marker-icon.cluster[title]')
    expect(titled.first).to_be_attached()
    titles = [titled.nth(i).get_attribute('title') for i in range(titled.count())]
    assert all('press Enter' in title for title in titles)
    assert any('public cameras' in title or 'severity' in title for title in titles)
