"""The 3D Earth in Global intelligence (app/earth_globe.py, app/gui_globe.py), 2026-11-08.

The globe must be geographically right (points land where they are, the sun is where it is), honest
about what it shows (imagery and figures carry their source and year), and fully usable: the same
markers, selection and filters as the flat map, countries that open with their facts, a way back,
and the flat map one key away.
"""
from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest

from app.earth_globe import Earth, EarthRenderer, GlobeCamera, between, scales_for, subsolar_point
from app.gui_motion import MOTION
from app.intelligence.view import validate_view


@pytest.fixture(scope='module')
def earth():
    return Earth()


# ------------------------------------------------------------------------------- geometry and sun

def test_the_sun_is_overhead_where_the_almanac_says():
    lat, lon = subsolar_point(datetime(2026, 6, 21, 12, 0, tzinfo=UTC))
    assert abs(lat - 23.44) < .15 and abs(lon) < 1.5          # June solstice, noon at Greenwich
    lat, lon = subsolar_point(datetime(2026, 3, 20, 18, 0, tzinfo=UTC))
    assert abs(lat) < .5 and abs(lon - -90) < 3               # equinox, 18:00 UTC: over the Americas' meridian
    lat, _lon = subsolar_point(datetime(2026, 12, 21, 12, 0, tzinfo=UTC))
    assert abs(lat + 23.44) < .15


def test_points_project_and_come_back_and_the_far_side_is_hidden():
    camera = GlobeCamera(30, 20, 2.0)
    for lon, lat in ((30, 20), (45.5, 31.2), (10, -5), (60, 50)):
        x, y, depth = camera.project(lon, lat, 900, 600)
        assert depth > 0
        back = camera.unproject(x, y, 900, 600)
        assert back and abs(back[0] - lon) < 1e-6 and abs(back[1] - lat) < 1e-6
    assert camera.project(30, 20, 900, 600)[:2] == (450, 300)  # the facing point is the centre
    assert camera.project(-150, -20, 900, 600)[2] < 0           # the antipode is behind
    assert GlobeCamera(0, 0, 1).unproject(5, 5, 900, 600) is None  # a corner is space


def test_dragging_moves_the_surface_with_the_pointer():
    camera = GlobeCamera(0, 0, 1.0)
    width, height = 800, 600
    lon, lat = camera.unproject(400 + 40, 300, width, height)
    camera.drag(40, 0, width, height)
    x, y, _depth = camera.project(lon, lat, width, height)
    assert abs(x - 480) < 1.5 and abs(y - 300) < 1.5


def test_fitting_a_country_comes_close_and_flights_take_the_short_way(earth):
    camera = GlobeCamera()
    france = earth.find('France')[0]
    camera.fit(earth.country_bbox(france), 900, 600)
    # Metropolitan France fills the view (its outline also holds French Guiana, which must not count).
    assert 6 < camera.zoom < 20 and abs(camera.latitude - 46) < 3 and abs(camera.longitude - 2.5) < 4
    middle = between(GlobeCamera(170, 0, 1), GlobeCamera(-170, 0, 1), .5)
    assert abs(abs(middle.longitude) - 180) < 1  # across the date line, not round the world
    assert between(GlobeCamera(0, 0, 1), GlobeCamera(40, 10, 4), 1.0).zoom == pytest.approx(4)


def test_the_visible_area_and_the_flat_map_equivalent_agree():
    camera = GlobeCamera(10, 45, 6.0)
    west, south, east, north = camera.bounds(900, 600)
    assert west < 10 < east and south < 45 < north and north - south < 40
    assert GlobeCamera(0, 0, 1).bounds(900, 600)[0::2] == [-180.0, 180.0]
    flat_zoom = camera.flat_zoom(900, 600)
    again = GlobeCamera.from_flat(10, 45, flat_zoom, 900, 600)
    assert abs(again.zoom - camera.zoom) / camera.zoom < .25


# ----------------------------------------------------------------------------- countries and data

def test_countries_are_found_by_place_and_name(earth):
    name = lambda lon, lat: earth.countries[earth.country_at(lon, lat)]['name']
    assert name(2.35, 48.86) == 'France'
    assert name(77.2, 28.6) == 'India'
    assert name(39.8, 21.4) == 'Saudi Arabia'
    assert name(20, 10) == 'Chad'
    assert earth.country_at(-30, 30) is None  # the Atlantic
    assert earth.countries[earth.find('ind')[0]]['name'] in ('India', 'Indonesia')
    assert earth.find('saudi')[0] == earth.country_at(39.8, 21.4)


def test_areas_and_layers_come_from_the_recorded_figures(earth):
    by_name = {country['name']: country for country in earth.countries}
    assert by_name['France']['area_km2'] == pytest.approx(550_000, rel=.25)
    assert by_name['Russia']['area_km2'] == pytest.approx(17_000_000, rel=.15)
    density = lambda name: earth.layer_value('density', by_name[name])
    assert density('Bangladesh') > density('India') > density('Russia')
    assert earth.layer_value('gdp_per_person', by_name['Norway']) > earth.layer_value('gdp_per_person', by_name['Chad'])
    edges, labels = earth.layer_classes('density')
    assert len(edges) == 4 and len(labels) == 5 and edges == sorted(edges)
    table, legend = earth.layer_table('income')
    assert legend['title'] == 'Income group' and 'Natural Earth' in legend['basis'] and legend['years']
    assert table[earth.country_at(10.7, 59.9) + 1][3] > 0  # Norway is coloured


# --------------------------------------------------------------------------------------- rendering

def test_day_night_city_lights_and_atmosphere_are_drawn(earth):
    renderer = EarthRenderer(earth)
    moment = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)  # noon at Greenwich: Africa lit, the Pacific dark
    lit = np.asarray(renderer.render(GlobeCamera(15, 0, 1), 400, 400, moment=moment), dtype=np.float32)
    dark = np.asarray(renderer.render(GlobeCamera(-165, 0, 1), 400, 400, moment=moment), dtype=np.float32)
    centre = (slice(150, 250), slice(150, 250))
    assert lit[centre].mean() > 2.5 * dark[centre].mean()
    night_asia = np.asarray(renderer.render(GlobeCamera(105, 30, 1.6), 400, 400,
                                            moment=datetime(2026, 11, 8, 18, 0, tzinfo=UTC)), dtype=np.float32)
    assert (night_asia[..., 0] > night_asia[..., 2] + 40).sum() > 50  # warm city lights on the night side
    day_edge, dusk_edge = lit[200, 200 - int(.42 * 400) - 4], lit[200, 200 + int(.42 * 400) + 4]
    space = lit[5, 5]
    assert day_edge[2] > space[2] + 60  # blue atmosphere just past the sunlit edge…
    assert dusk_edge[2] < day_edge[2]   # …fading where the sun is setting
    unlit = np.asarray(renderer.render(GlobeCamera(-165, 0, 1), 400, 400, lighting=False), dtype=np.float32)
    assert unlit[centre].mean() > 2 * dark[centre].mean()  # lighting off shows the whole face


def test_rendering_presets_and_the_automatic_one_follow_measured_cost(earth):
    renderer = EarthRenderer(earth)
    assert scales_for('performance', renderer, 900, 600)['still'] < scales_for('ultra', renderer, 900, 600)['still'] * \
        scales_for('ultra', renderer, 900, 600)['supersample']
    renderer.cost_per_megapixel = 400.0  # a slow computer
    slow = scales_for('auto', renderer, 1200, 800)
    renderer.cost_per_megapixel = 20.0
    fast = scales_for('auto', renderer, 1200, 800)
    assert slow['moving'] < fast['moving'] and slow['still'] <= fast['still']
    small = renderer.render(GlobeCamera(), 300, 200, scale=.5)
    assert small.size == (300, 200) and renderer.last_ms > 0


def test_without_the_imagery_the_globe_still_draws_land(tmp_path):
    fallback = Earth(tmp_path)
    assert not fallback.imagery and 'unavailable' in fallback.problem and not fallback.countries
    image = np.asarray(EarthRenderer(fallback).render(GlobeCamera(20, 10, 1), 300, 300, lighting=False))
    assert image[150, 150].sum() > 0


def test_globe_options_are_validated():
    assert validate_view({'map_mode': 'flat', 'globe': {'quality': 'ultra', 'layer': 'income', 'zoom': 3}})['globe']['zoom'] == 3
    for bad in ({'map_mode': 'sphere'}, {'globe': {'quality': 'max'}}, {'globe': {'spin': 'yes'}},
                {'globe': {'zoom': float('nan')}}, {'globe': {'colour': 'red'}}, {'globe': {'layer': 'crime'}}):
        with pytest.raises(ValueError):
            validate_view(bad)


# -------------------------------------------------------------------------------------- the window

@pytest.fixture
def globe_window(shared_tk, tmp_path, monkeypatch):
    from app.gui_intelligence import IntelligenceWindow
    from app.gui_theme import Palette
    from app.intelligence.manager import IntelligenceManager
    from app.intelligence.tools import register_intelligence_tools
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    from tests.conftest import close_window, open_window
    from tests.test_public_intelligence import Fetcher

    monkeypatch.setitem(MOTION, 'enabled', False)  # flights land at once, so every check is deterministic
    parent = open_window(shared_tk, '1100x850-4000-4000')
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    manager = IntelligenceManager(tmp_path / 'intelligence.db', Fetcher(), registry)
    register_intelligence_tools(registry, manager)
    manager.refresh()

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = parent, SimpleNamespace(intelligence=manager, registry=registry)
    window = IntelligenceWindow(gui)
    window.geometry('1100x850-4000-4000')
    settle(window)
    try:
        yield window
    finally:
        if window.winfo_exists():
            window.close()
        manager.close()
        close_window(parent)


def settle(window):
    """Let the delayed redraw (16 ms after a change) happen before looking at the canvas."""
    deadline = time.monotonic() + 2
    window.update()
    while window._draw_handle is not None and time.monotonic() < deadline:
        time.sleep(.01)
        window.update()


def _click(window, x, y):
    event = SimpleNamespace(x=x, y=y, widget=window.map)
    window.start_drag(event)
    window.end_drag(event)
    settle(window)


def test_the_window_opens_on_the_globe_with_the_same_markers(globe_window):
    window = globe_window
    assert window.map_mode.get() == 'globe' and window.globe_on()
    assert window.map.find_withtag('globe-surface') and window.map.find_withtag('borders')
    assert window.map_hint.get().startswith('Drag to turn')
    # Face Chad: the wildfire there is drawn and clicking it selects it, as on the flat map.
    window.globe_focus(20, 10, 2.0, remember=False)
    settle(window)
    x, y, depth = window.globe_camera.project(20, 10, *window.size())
    assert depth > .9 and window.marker_at(x, y) == 'natural_events:n1'
    assert 'earthquakes:q1' not in {window.map.gettags(item)[1] for item in window.map.find_withtag('event')}  # far side
    _click(window, x, y)
    deadline = time.monotonic() + 4
    while not window.selected_record and time.monotonic() < deadline:  # the record loads on a worker thread
        time.sleep(.02)
        window.update()
    assert window.selected_record and window.selected_record['event']['id'] == 'natural_events:n1'


def test_a_country_opens_with_its_facts_observations_and_a_way_back(globe_window):
    window = globe_window
    window.globe_focus(20, 10, 2.0, remember=False)
    settle(window)
    before = window.globe_camera.copy()
    x, y, _depth = window.globe_camera.project(17, 15, *window.size())  # Chad, away from the marker
    _click(window, x, y)
    chad = window._globe_earth.find('Chad')[0]
    assert window.selected_country == chad
    card = ' '.join(window.map.itemcget(item, 'text') for item in window.map.find_withtag('country-card')
                    if window.map.type(item) == 'text')
    assert 'Chad' in card and 'people' in card and '1 loaded observation here' in card and 'Natural Earth' in card
    assert window.map.find_withtag('country-selected')
    dialog = window.country_action('list')
    assert dialog.tree.get_children() == ('natural_events:n1',)
    dialog.destroy()
    window.globe_back()
    settle(window)
    assert window.globe_camera.longitude == pytest.approx(before.longitude) and window.globe_camera.zoom == pytest.approx(before.zoom)
    window.country_action('close')
    assert window.selected_country is None


def test_hovering_a_country_outlines_it_and_names_it(globe_window):
    window = globe_window
    window.globe_focus(2.3, 46.5, 4.0, remember=False)
    settle(window)
    x, y, _depth = window.globe_camera.project(2.3, 46.5, *window.size())
    window.hover(SimpleNamespace(x=x, y=y, widget=window.map))
    assert window.map.find_withtag('country-hover') and 'France' in window.map_hint.get()
    assert 'click for details' in window.map_hint.get()


def test_switching_to_the_flat_map_keeps_the_place_and_is_remembered(globe_window):
    window = globe_window
    window.globe_focus(77, 22, 4.0, remember=False)
    settle(window)
    window.toggle_map_mode()
    settle(window)
    assert window.map_mode.get() == 'flat' and not window.map.find_withtag('globe-surface')
    # Same place; the flat zoom shows about the width the globe showed (it depends on the canvas shape).
    assert abs(window.viewport.longitude - 77) < 1 and window.viewport.zoom > 1.5
    assert window.manager.preferences()['preferences']['map_mode'] == 'flat'
    window.toggle_map_mode()
    settle(window)
    assert window.map.find_withtag('globe-surface') and abs(window.globe_camera.longitude - 77) < 1


def test_filter_to_visible_map_uses_what_the_globe_shows(globe_window):
    window = globe_window
    window.globe_focus(20, 10, 3.0, remember=False)
    window.only_viewport.set(True)
    window.filter_changed()
    settle(window)
    assert set(window.map_rows) == {'natural_events:n1'}
    west, south, east, north = window.bounds()
    assert west < 20 < east and south < 10 < north


def test_layers_lighting_and_quality_are_menu_choices_that_persist(globe_window):
    window = globe_window
    window.globe_layer.set('density')
    window.globe_quality.set('performance')
    window.globe_lighting.set(False)
    window.globe_option_changed()
    settle(window)
    texts = ' '.join(window.map.itemcget(item, 'text') for item in window.map.find_withtag('layer-legend')
                     if window.map.type(item) == 'text')
    assert 'People per km²' in texts and 'Historical, not live' in texts
    saved = window.manager.preferences()['preferences']['globe']
    assert saved['layer'] == 'density' and saved['quality'] == 'performance' and saved['lighting'] is False
    assert 'ms' in window.globe_details()


def test_go_to_place_finds_countries_and_local_towns(globe_window, monkeypatch):
    import app.gui_globe as gui_globe

    monkeypatch.setattr(gui_globe, '_places', [('makkah', 'Makkah', 'Makkah Region', 'Saudi Arabia', 21.4225, 39.8262),
                                                ('mumbai', 'Mumbai', 'Maharashtra', 'India', 19.0728, 72.8826)])
    window = globe_window
    dialog = window.go_to_place()
    dialog.query.set('mak')
    settle(window)
    assert dialog.listbox.get(0).startswith('Makkah, Makkah Region, Saudi Arabia')
    dialog.choose()
    settle(window)
    assert window._globe_pin[2] == 'Makkah' and abs(window.globe_camera.latitude - 21.42) < .01
    assert 'GeoNames list on this computer' in window.feedback.get()
    dialog = window.go_to_place()
    dialog.query.set('fran')
    settle(window)
    assert dialog.results[0][0] == 'country'
    dialog.choose()
    settle(window)
    assert window._globe_earth.countries[window.selected_country]['name'] == 'France'


def test_saved_views_carry_the_globe(globe_window):
    window = globe_window
    window.globe_focus(-74, 40.7, 6.0, remember=False)
    window.globe_borders.set(False)
    options = window.view_options()
    assert options['map_mode'] == 'globe' and options['globe']['zoom'] == pytest.approx(6.0)
    validate_view(options)
    window.globe_focus(0, 0, 1.0, remember=False)
    window.globe_borders.set(True)
    window.apply_view_options(options)
    settle(window)
    assert abs(window.globe_camera.longitude - -74) < 1e-3 and window.globe_borders.get() is False


def test_keyboard_and_buttons_turn_zoom_and_reset_the_globe(globe_window):
    window = globe_window
    start = window.globe_camera.copy()
    window.move_map(70, 0)
    assert window.globe_camera.longitude < start.longitude
    window.zoom(1.4)
    assert window.globe_camera.zoom == pytest.approx(start.zoom * 1.4)
    window.wheel(SimpleNamespace(x=550, y=300, delta=120))
    assert window.globe_camera.zoom > start.zoom * 1.4
    window.reset_map()
    assert window.globe_camera == GlobeCamera()
    assert math.isclose(window.globe_camera.zoom, 1.0)


def test_with_motion_off_nothing_turns_by_itself(globe_window):
    window = globe_window
    window.globe_spin.set(True)
    window.globe_option_changed()
    settle(window)
    assert window._globe_motion is None  # motion is off in this fixture: no spin loop
