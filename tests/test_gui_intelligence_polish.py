"""The Global Intelligence window on real-world volumes (screenshots on a copy of the owner's feeds, 2026-10-10).

35,219 cameras counted as "events", 1,212 road works dated 2027 at the top of
every list, category chips that filtered only the first 60 rows, a snowflake
icon on twenty buttons, badges cut to "RESTRI", raw ISO times in Alerts.
"""
from datetime import UTC, datetime, timedelta
import tkinter as tk
from tkinter import ttk

from app.gui_intelligence import (IntelligenceWindow, alert_title, bounds_text, cluster_markers, readable_time,
                                  source_order, sources_summary, without_provider)
from app.gui_intelligence_parts import LiveFeed, fit
from app.gui_theme import Palette
from app.intelligence.classify import assess
from app.intelligence.manager import happening_first, live_selection, spotlight
from tests.conftest import close_window, open_window
from tests.test_gui_intelligence import explorer, pump_until  # noqa: F401 -- fixture


def at(hours):
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def row(identifier, category='earthquakes', hours=-1.0, severity='low'):
    return {'id': identifier, 'category': category, 'observed_at': at(hours) if hours is not None else None,
            'severity': severity, 'severity_rank': {'info': 0, 'low': 1, 'medium': 2, 'high': 3}[severity]}


def test_what_is_happening_comes_before_what_is_only_scheduled():
    rows = [row('road-2027', 'traffic', 24 * 200), row('quake', hours=-2), row('camera', 'cameras', None),
            row('road-next-week', 'traffic', 24 * 7), row('storm', 'severe_weather', -.5)]
    assert [item['id'] for item in happening_first(rows)] == ['storm', 'quake', 'road-next-week', 'road-2027', 'camera']


def test_every_category_chip_has_its_own_rows():
    live = [row(f'road{index}', 'traffic', -index / 100) for index in range(100)] + [row('quake', hours=-5)]
    chosen = live_selection(live, 60)
    assert 'quake' in {item['id'] for item in chosen}  # it was row 101 of 101
    assert sum(item['category'] == 'traffic' for item in chosen) == 60


def test_the_window_opens_on_the_days_most_severe_report():
    live = [row('road', 'traffic', -.1), row('hurricane', 'severe_weather', -3, 'high'), row('old', hours=-48, severity='high')]
    assert spotlight(live) == 'hurricane'
    assert spotlight([row('old', hours=-48, severity='high'), row('older', hours=-50)]) == 'old'
    assert spotlight([]) is None


def test_cameras_are_not_events_unless_asked_for(explorer):  # noqa: F811
    window, _gui = explorer
    manager = window.manager
    rows = [{**row('cam', 'cameras', None, 'info'), 'feed': 'cameras_london'},
            {**row('quake', hours=-1), 'feed': 'earthquakes', 'details': {}},
            {**row('works', 'traffic', 24 * 30), 'feed': 'roads_cars_co', 'details': {}}]
    manager._matching = lambda query='', feed='', bbox=None, located=False, category='', *rest: (
        manager._sources(), [dict(item) for item in rows if not category or item['category'] == category])
    manager._aggregates = lambda found: {'counts_by_category': {item['category']: 1 for item in found}}
    value = manager.view()
    assert [item['id'] for item in value['events']] == ['quake', 'works'] and value['cameras_total'] == 1
    assert value['spotlight'] == 'quake' and 'cameras' not in value['aggregates']['counts_by_category']
    assert [item['id'] for item in manager.view(category='cameras')['events']] == ['cam']


def test_dense_markers_cluster_but_the_selected_one_stays_single():
    visible = [(row(f'r{index}', 'traffic', severity='high' if index == 0 else 'low'), 100 + index, 100 + index)
               for index in range(6)] + [(row('far'), 400, 300)]
    singles, clusters = cluster_markers(visible, 1.0, selected_id='r1')
    assert {item[0]['id'] for item in singles} == {'r1', 'far'}
    assert len(clusters) == 1 and len(clusters[0][2]) == 5
    assert any(member['severity'] == 'high' for member in clusters[0][2])  # drawn with a red ring
    assert len(cluster_markers(visible, 9.0)[1]) == 1  # coincident markers stay inspectable


def test_clicking_a_cluster_zooms_in(explorer):  # noqa: F811
    window, _gui = explorer
    window.update()
    window._clusters = [(200.0, 150.0, [row('a'), row('b'), row('c')])]
    window.map.delete('all')
    window._draw_clusters()
    zoom = window.viewport.zoom
    window.select_marker(type('Click', (), {'x': 200, 'y': 150})())
    assert window.viewport.zoom > zoom * 2


def test_road_badges_are_whole_words():
    restricted = assess({'feed': 'roads_cars_co', 'title': 'I-70 lane restriction', 'kind': 'restriction', 'details': {}})
    works = assess({'feed': 'roads_cars_co', 'title': 'Bridge works', 'kind': 'construction', 'details': {}})
    assert (restricted['badge'], works['badge']) == ('RESTRICT', 'ROADWORK')


def test_feed_cards_measure_text_and_hold_one_category_to_fifteen(shared_tk):
    parent = open_window(shared_tk, '360x700-4000-4000')
    try:
        feed = LiveFeed(parent, Palette, lambda _id: None, lambda: None)
        feed.pack(fill='both', expand=True)
        rows = [{**row(f'road{index}', 'traffic', -index / 100), 'headline': 'I-465 eastbound: Roadway reduced to one lane',
                 'subtitle': 'Indiana DOT', 'provider': 'Indiana DOT', 'badge': 'LANE SHUT'} for index in range(40)]
        rows += [{**row(f'{key}{index}', key, -1 - index), 'headline': key, 'subtitle': '', 'provider': 'p', 'badge': 'LOW'}
                 for key in ('earthquakes', 'wildfires', 'cyber', 'floods', 'volcanoes', 'news', 'conflict', 'attack')
                 for index in range(2)]
        counts = {}
        for item in rows:
            counts[item['category']] = counts.get(item['category'], 0) + 1
        feed.set_rows(rows, len(rows), counts, True)
        parent.update()
        shown = feed.visible_rows()
        assert sum(item['category'] == 'traffic' for item in shown) == 15 and len(shown) == 15 + 16
        # "All" and the six largest of nine categories as chips, the remaining three in a menu.
        assert len(feed.chips.items) == LiveFeed.CHIPS + 1 and feed._more.cget('text') == 'More (3) ▾'
        feed.choice.set('attack')
        feed.set_rows(rows, len(rows), counts, True)
        assert feed._more.cget('text').startswith('Attacks') and [item['id'] for item in feed.visible_rows()] == ['attack0', 'attack1']
        badge = feed._badge_font.measure('LANE SHUT')
        assert fit('I-465 eastbound: Roadway reduced to one lane', feed._title_font, 120).endswith('…')
        assert feed._title_font.measure(fit('I-465 eastbound: Roadway reduced', feed._title_font, 120)) <= 120 and badge > 0
    finally:
        close_window(parent)


def test_chosen_icons_survive_hayats_app_wide_icon_pass(shared_tk):
    from app.gui_icons import apply_icons, icon_image, icon_kind

    parent = open_window(shared_tk, '200x100-4000-4000')
    try:
        button = ttk.Button(parent, text='Previous')
        button.pack()
        button._hayat_icon = 'history'
        apply_icons(parent, Palette)
        assert str(icon_image(button, 'history', Palette.ROYAL_SOFT)) in str(button.cget('image'))
        # Labels that fell back to the generic "tool" icon now have their own.
        assert [icon_kind(text) for text in ('Add watch', 'Open source', 'Copy location', 'Weather here', 'Generate brief',
                                             'Full raw evidence', 'Near selected event', 'Analyze with HAYAT', 'Load snapshot')] == [
            'eye', 'link', 'copy', 'cloud', 'file', 'code', 'pin', 'brain', 'image']
    finally:
        close_window(parent)


def test_window_buttons_keep_their_icons_after_a_global_pass(explorer):  # noqa: F811
    from app.gui_icons import apply_icons

    window, gui = explorer
    apply_icons(gui.root, Palette)  # what HAYAT does on a theme change, across every window
    for button in window._buttons(window):
        if str(button.cget('text')) in IntelligenceWindow.ICONS:
            assert button._hayat_icon == IntelligenceWindow.ICONS[str(button.cget('text'))]


def test_alerts_sources_watches_and_events_read_as_words():
    assert alert_title({'title': 'southeast of Easter Island', 'details': {'magnitude': 5.3}}) == 'M5.3 · southeast of Easter Island'
    assert alert_title({'title': 'M 5.3 - Fiji', 'details': {'magnitude': 5.3}}) == 'M 5.3 - Fiji'
    assert readable_time('2026-10-10T22:04:41.340355+00:00').endswith('2026-10-10 22:04 UTC') and readable_time(None) == 'Unknown'
    assert bounds_text([26.38, 39.01, 31.58, 43.01]) == '39.0°N–43.0°N, 26.4°E–31.6°E' and bounds_text(None) == ''
    assert without_provider('Indiana DOT (TrafficWise) · Indiana · warning', 'Indiana DOT (TrafficWise)') == 'Indiana · warning'
    sources = [{'key': 'ok', 'title': 'USGS', 'status': 'ok', 'stale': False},
               {'key': 'gdelt', 'title': 'GDELT', 'status': 'failed', 'stale': True, 'error': 'ConnectTimeout'},
               {'key': 'keyed', 'title': '511 Ontario', 'status': 'needs_key', 'stale': True},
               {'key': 'old', 'title': 'NOAA', 'status': 'ok', 'stale': True}]
    assert [source['key'] for source in sorted(sources, key=source_order)] == ['gdelt', 'keyed', 'old', 'ok']
    summary = sources_summary(sources)
    assert summary.startswith('1 of 4 public feeds are current.')
    assert 'GDELT — ConnectTimeout' in summary and '511 Ontario' in summary and 'NOAA' in summary


def test_assessment_fills_itself_and_sources_explain_themselves(explorer):  # noqa: F811
    window, _gui = explorer
    window.notebook.select(window.tabs['assessment'])
    pump_until(window, lambda: 'event reports' in window.assessment_text.get('1.0', 'end'))
    window.notebook.select(window.tabs['sources'])
    window.update()
    assert 'public feeds are current' in window.source_text.get('1.0', 'end')
    first = window.source_tree.get_children()[0]
    window.source_tree.selection_set(first)
    window.update()
    assert window.source_text.get('1.0', 'end').startswith('Feed: ')


def test_the_map_split_settles_on_its_share_until_the_owner_drags_it(explorer):  # noqa: F811
    window, _gui = explorer
    window.geometry('1300x900-4000-4000')
    pump_until(window, lambda: window.map_split.winfo_height() > 456)
    window._place_map_sash()
    window.update()
    height = window.map_split.winfo_height()
    assert abs(window.map_split.sash_coord(0)[1] - (height - IntelligenceWindow._cards_height(height))) <= 8
    window._sash_user = True
    window.map_split.sash_place(0, 0, 300)
    window._place_map_sash()
    window.update()
    assert abs(window.map_split.sash_coord(0)[1] - 300) <= 8
    assert isinstance(window.map, tk.Canvas)


def test_the_legend_starts_folded_and_scrolls_inside_short_maps(explorer):  # noqa: F811
    window, _gui = explorer
    window.update()
    window.fit_legend(900, 1200)
    assert not window.legend_body.winfo_manager()
    window.fit_legend(900, 320)  # a compact map beside the inspector
    assert not window.legend_body.winfo_manager()
    window.fit_legend(150, 1200)
    assert not window.legend_body.winfo_manager()
    window.toggle_legend()  # the owner opens it: it stays open
    window.fit_legend(150, 320)
    assert window.legend_body.winfo_manager()
    assert int(window.legend_canvas['height']) <= 60
