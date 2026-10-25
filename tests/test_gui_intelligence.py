from types import SimpleNamespace
import time

import pytest

from app.gui_intelligence import IntelligenceWindow
from app.gui_theme import Palette
from app.intelligence.manager import IntelligenceManager
from app.intelligence.tools import register_intelligence_tools
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from tests.conftest import open_window, close_window
from tests.test_public_intelligence import Fetcher


@pytest.fixture
def explorer(shared_tk, tmp_path):
    parent = open_window(shared_tk, '1100x850-4000-4000')
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    manager = IntelligenceManager(tmp_path / 'intelligence.db', Fetcher(), registry)
    register_intelligence_tools(registry, manager)
    manager.refresh()
    class GUI(Palette):
        def _open_intelligence(self):
            self._intelligence_window = window
    gui = GUI()
    gui.root, gui.runtime = parent, SimpleNamespace(intelligence=manager, registry=registry)
    window = IntelligenceWindow(gui)
    gui._intelligence_window = window
    try:
        yield window, gui
    finally:
        if window.winfo_exists():
            window.close()
        manager.close()
        close_window(parent)


def without_tallinn(manager):
    """Keep the dynamic fixture cameras, including Norway, Estonia, Luxembourg, València, NSW and Lyon;
    the 255 bundled Tallinn cameras would push them past the first 200-row page."""
    from datetime import UTC, datetime
    manager.store.save('cameras_tallinn', datetime.now(UTC).isoformat(), {})


def pump_until(window, condition):
    deadline = time.monotonic() + 4
    while not condition() and time.monotonic() < deadline:
        window.update()
        time.sleep(.01)
    assert condition()


def test_camera_preview_resources_weather_and_atlas_in_native_workspace(explorer):
    from tests.test_intelligence_resources import ExtendedFetcher
    window, gui = explorer
    window.manager.fetcher = ExtendedFetcher()
    window.manager.refresh(list(window.manager.fetcher.payloads))
    without_tallinn(window.manager)
    opened = []
    gui._open_source = opened.append
    window.notebook.select(window.tabs['cameras'])
    expected = window.manager.cameras(limit=200)['total_matching']
    pump_until(window, lambda: not window._busy and len(window.camera_rows) == expected)
    assert {'cameras_nsw', 'cameras_lyon'} <= {row['feed'] for row in window.camera_rows.values()}
    assert str(window.camera_buttons['Next']['state']) == 'disabled'
    window.camera_tree.selection_set('cameras_london:JamCams_test')
    window.update()
    window.load_camera_frame()
    pump_until(window, lambda: not window._busy and window._camera_photo is not None)
    assert 'Capture time unverified' in window.camera_preview_info.get()
    window.camera_atlas()
    pump_until(window, lambda: not window._busy and bool(opened))
    assert '#cameras_london' in opened[0]
    window.query.set('California')
    assert window.camera_selection is None and window._camera_photo is None
    window.load_cameras()
    pump_until(window, lambda: not window._busy and len(window.camera_rows) == 13)
    window.layer.set('cameras_caltrans_4')
    window.load_cameras()
    pump_until(window, lambda: not window._busy and len(window.camera_rows) == 1)
    assert next(iter(window.camera_rows.values()))['feed'] == 'cameras_caltrans_4'
    window.notebook.select(window.tabs['resources'])
    window.resource_tree.selection_set('windy')
    window.update()
    assert 'key_required' in window.resource_text.get('1.0', 'end')
    window.open_resource()
    assert opened[-1] == 'https://api.windy.com/webcams/docs'
    window.notebook.select(window.tabs['context'])
    window.context_lat.set('51.5')
    window.context_lon.set('-.1')
    window.context_weather()
    pump_until(window, lambda: not window._busy)
    assert 'model output' in window.context_text.get('1.0', 'end')
    window.context_recall()
    pump_until(window, lambda: not window._busy)
    assert 'California test road' in window.context_text.get('1.0', 'end')
    window.geometry('760x580-4000-4000')
    for key in ('cameras', 'resources', 'context'):
        window.notebook.select(window.tabs[key])
        window.update()
        assert window.tabs[key].winfo_height() >= 200


def test_stream_actions_and_still_refresh_stop_on_selection_tab_and_close(explorer):
    from tests.test_intelligence_resources import ExtendedFetcher
    window, gui = explorer
    window.manager.fetcher = ExtendedFetcher()
    window.manager.refresh(list(window.manager.fetcher.payloads))
    without_tallinn(window.manager)
    opened = []
    gui._open_source = opened.append
    window.notebook.select(window.tabs['cameras'])
    expected = window.manager.cameras(limit=200)['total_matching']
    pump_until(window, lambda: not window._busy and len(window.camera_rows) == expected)
    window.camera_tree.selection_set('cameras_london:JamCams_test')
    window.update()
    window.camera_refresh.set(True)
    window.toggle_camera_refresh()
    pump_until(window, lambda: not window._busy and window._camera_photo is not None)
    assert window._camera_timer and window.camera_refresh.get()
    window.camera_tree.selection_set('cameras_nyc:8a6bc417-4877-4ebe-8052-88c1b261baf1')
    window.update()
    assert window._camera_timer is None and not window.camera_refresh.get()
    window.camera_refresh.set(True)
    window.toggle_camera_refresh()
    pump_until(window, lambda: not window._busy)
    window.notebook.select(window.tabs['resources'])
    window.update()
    assert window._camera_timer is None and not window.camera_refresh.get()
    window.notebook.select(window.tabs['cameras'])
    window.update()
    window.find_camera_streams()
    pump_until(window, lambda: not window._busy and 'cameras_delaware:KCAM001' in window.camera_rows)
    assert window.camera_media.get() == 'HLS streams'
    window.camera_tree.selection_set('cameras_delaware:KCAM001')
    window.update()
    window.watch_camera_video()
    # Played in HAYAT's own player window, not the web atlas or the browser.
    player = window.camera_player
    preview = next(child for child in player.winfo_children() if hasattr(child, 'camera'))
    assert preview.camera['event_id'] == 'cameras_delaware:KCAM001' and preview.max_width == 1280
    assert not opened and 'in HAYAT' in window.feedback.get()
    preview.close()
    player.destroy()
    window.close()
    assert window._camera_timer is None and window._signal_id is None and window._closed


def test_notebook_navigation_map_focus_and_deep_assessment(explorer):
    window, _gui = explorer
    window.update()
    assert window.map.winfo_ismapped() and not window.tree.winfo_ismapped()
    window.notebook.select(window.tabs['events'])
    window.update()
    assert window.tree.winfo_ismapped() and not window.map.winfo_ismapped()
    window.select_event('earthquakes:q1')
    pump_until(window, lambda: window.selected_record is not None and window.selected_record['event']['id'] == 'earthquakes:q1')
    assert 'Magnitude:\t4.0' in window.detail_text.get('1.0', 'end')
    assert window.detail_title.get() == 'M4.0 Earthquake' and window.detail_subtitle.get() == 'Test location'
    window.focus_selected()
    assert window.viewport.zoom >= 8 and window.viewport.longitude == 179
    window.reset_map()
    assert window.viewport.zoom == 1
    window.brief()
    pump_until(window, lambda: not window._busy)
    assert 'World coverage is partial' in window.assessment_text.get('1.0', 'end')
    assert 'intelligence_assess completed' in window.feedback.get()
    window.region.set('Europe')
    assert window.viewport.zoom > 1 and window.current_dashboard['total_matching'] == 0
    assert not window.selected_record
    assert str(window.focus_button['state']) == 'disabled'


@pytest.mark.parametrize('button_scale', [1.0, 1.3, 1.5])
def test_small_window_keeps_map_actions_details_and_footer_accessible(explorer, button_scale):
    import tkinter as tk
    from tkinter import ttk
    from app.gui_theme import apply_text_scale, style_controls, style_hayat_buttons
    window, _gui = explorer
    # Match the actual application's ttk theme and inherited button fonts.
    style = ttk.Style(window)
    previous_theme = style.theme_use()
    font_styles = ('TLabel', 'TButton', 'TCheckbutton', 'TRadiobutton', 'Hayat.TButton',
                   'HayatAccent.TButton', 'HayatRoyal.TButton', 'Treeview', 'Workspace.Treeview',
                   'Treeview.Heading', 'Workspace.Treeview.Heading', 'TEntry', 'TCombobox')
    previous_fonts = {name: style.lookup(name, 'font') for name in font_styles}
    previous_padding = style.lookup('Compact.Hayat.TButton', 'padding')
    style.theme_use('clam')
    style_controls(window, Palette)
    style_hayat_buttons(window, Palette)
    style.configure('Compact.Hayat.TButton', padding=(8, 3))
    scale_owner = tk.Frame(window)
    apply_text_scale(scale_owner, button_scale)
    def restore_style(event):
        if event.widget is window:
            style.theme_use(previous_theme)
            for name, font in previous_fonts.items():
                style.configure(name, font=font)
            style.configure('Compact.Hayat.TButton', padding=previous_padding)
    window.bind('<Destroy>', restore_style, add='+')
    window.geometry('760x580-4000-4000')
    window.update()
    assert window.map.winfo_width() >= 300
    assert window.detail_text.winfo_height() >= 100
    for button in window.map_actions:
        assert button.winfo_x() + button.winfo_width() <= window.map_toolbar.winfo_width()
    # The toggle sits in the map toolbar; the hint is drawn on the map (a row of its own cost the map ~30 px).
    assert window.map_view_toggle.winfo_ismapped()
    assert window.map_view_toggle.winfo_x() + window.map_view_toggle.winfo_width() <= window.map_toolbar.winfo_width()
    assert window.map.find_withtag('hint') or window.map.winfo_width() < 420
    for button in (window.focus_button, window.ask_button, window.source_button, window.raw_button):
        assert button.winfo_rooty() + button.winfo_height() <= window.winfo_rooty() + window.winfo_height()
    for button in (*window.header_buttons, window.window_button):
        assert button.winfo_ismapped()
        assert button.winfo_rootx() + button.winfo_width() <= window.winfo_rootx() + window.winfo_width()


def test_customize_and_watch_forms_persist_without_repeating_operations(explorer):
    window, _gui = explorer
    form = window.customize()
    form.options['home_detail'].set('detailed')
    form.options['home_alert_limit'].set('1')
    form.save()
    pump_until(window, lambda: not window._busy)
    assert not form.winfo_exists()
    assert window.manager.preferences()['preferences']['home_alert_limit'] == 1
    watch = window.watch_view()
    watch.fields['Title'].insert(0, 'Visible map')
    watch.save()
    pump_until(window, lambda: not window._busy)
    assert not watch.winfo_exists()
    assert any(rule['title'] == 'Visible map' and rule['bbox'] for rule in window.manager.store.watches())
    key = next(rule['key'] for rule in window.manager.store.watches() if rule['title'] == 'Visible map')
    window.watch_tree.selection_set(key)
    window.render()
    assert window.watch_tree.selection() == (key,)
    editor = window.edit_watch()
    editor.save()
    pump_until(window, lambda: not window._busy)
    assert not editor.winfo_exists()
    window.delete_watch()
    pump_until(window, lambda: not window._busy)
    assert all(rule['key'] != key for rule in window.manager.store.watches())


def test_compact_home_card_alerts_visibility_and_navigation(explorer):
    import tkinter as tk
    from app.gui_intelligence_home import IntelligenceHomeCard
    from tests.test_public_intelligence import make_due, quake
    window, gui = explorer
    gui.depth_nav = tk.Frame(gui.root, height=80)
    gui.depth_nav.pack(fill='both', expand=True)
    card = IntelligenceHomeCard(gui.root, gui)
    gui._intelligence_home = card
    try:
        pump_until(gui.root, lambda: bool(card.summary.get()))
        assert '4 items' in card.headline.get() and not card.alerts
        window.manager.fetcher.payloads['earthquakes']['features'] = [quake('q2', 6), quake('q3', 7)]
        make_due(window.manager, 'earthquakes')
        window.manager.refresh(['earthquakes'])
        window.manager.preferences('save', {'home_alert_limit': 1, 'home_detail': 'detailed'})
        card.render(*card.read())
        assert len(card.alerts) == 1 and 'without coordinates' in card.summary.get()
        gui.root.geometry('480x620-4000-4000')
        gui.root.update()
        for control in (card.open_button, card.customize_button):
            assert control.winfo_ismapped()
            assert control.winfo_rootx() + control.winfo_width() <= card.winfo_rootx() + card.winfo_width()
        button = card.alert_frame.winfo_children()[0]
        button.invoke()
        pump_until(window, lambda: window.selected_record is not None and window.selected_record['event']['id'] == card.alerts[0]['event']['id'])
        assert window.notebook.select() == str(window.tabs['map'])
        window.manager.preferences('save', {'show_home': False})
        card.render(*card.read())
        assert not card.winfo_manager()
        window.manager.preferences('save', {'show_home': True, 'show_alerts': False})
        card.render(*card.read())
        assert card.winfo_manager() == 'pack' and not card.alerts
    finally:
        card.close()
        assert card._timer is None
        card.destroy()


def test_intelligence_map_filters_missing_coordinates_and_shows_source_evidence(shared_tk, tmp_path):
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
    try:
        window.update()
        assert len(window.tree.get_children()) == 4
        assert len(window.map.find_withtag('event')) == 2
        assert window.polygons and '4/4 feeds current' in window.status.get()
        assert window.source_tree.set('cyber', 'status') == 'ok'
        window.layer.set('cyber')
        assert window.tree.get_children() == ('cyber:CVE-2026-12345',)
        assert len(window.map.find_withtag('event')) == 0
        window.layer.set('All layers')
        window.query.set('wildfire')
        assert window.tree.get_children() == ('natural_events:n1',)
        assert len(window.map.find_withtag('event')) == 1
        window.tree.selection_set('natural_events:n1')
        window.update()
        children = window.winfo_children()
        window.render()
        window.update()
        assert window.tree.selection() == ('natural_events:n1',)
        assert window.winfo_children() == children  # A refresh creates no evidence dialog.
        assert window.tree.bind('<<TreeviewSelect>>')
        assert window.tree.bind('<Double-1>') and window.tree.bind('<Return>')
        dialog = window.show_text('Evidence', 'Source evidence is selectable.')
        dialog.destroy()
        window.close()
        assert not window.winfo_exists()
    finally:
        if window.winfo_exists():
            window.close()
        manager.close()
        close_window(parent)


def test_severity_category_time_filters_layers_and_live_feed(explorer):
    window, _gui = explorer
    window.update()
    assert len(window.live.rows) == 4 and window.live.title.cget('text') == 'Live Global Feed (4)'
    assert window.live.canvas.find_withtag('card:earthquakes:q1')
    window.severity.set('Medium and above')
    assert window.tree.get_children() == ('cyber:CVE-2026-12345',)
    window.severity.set('All severities')
    window.category.set('Wildfires')
    assert window.tree.get_children() == ('natural_events:n1',)
    window.category.set('All events')
    window.time_range.set('Last hour')
    assert window.tree.get_children() == () and not window.live.rows
    window.time_range.set('Any time')
    assert len(window.map.find_withtag('event')) == 2
    window.layer_vars['earthquakes'].set(False)
    window.layer_toggled()
    assert len(window.map.find_withtag('event')) == 1 and len(window.tree.get_children()) == 4
    window.live.choice.set('cyber')
    window.live.draw()
    assert [row['id'] for row in window.live.visible_rows()] == ['cyber:CVE-2026-12345']
    window.live.on_select('cyber:CVE-2026-12345')
    pump_until(window, lambda: window.selected_record is not None and window.selected_record['event']['id'] == 'cyber:CVE-2026-12345')
    assert window.live.selected == 'cyber:CVE-2026-12345' and str(window.focus_button['state']) == 'disabled'
    window.severity.set('High only')
    window.save_view()
    pump_until(window, lambda: not window._busy)
    saved = window.manager.preferences()['preferences']
    assert saved['min_severity'] == 'high' and saved['hidden_layers'] == ['earthquakes', 'cameras']


def test_event_card_assessment_actions_navigation_and_local_analysis(explorer):
    window, gui = explorer
    opened = []
    gui._open_source = opened.append
    window.select_event('earthquakes:q1')
    pump_until(window, lambda: window.selected_record is not None and window.selected_record['event']['id'] == 'earthquakes:q1')
    assert [window.tiles[key].cget('text') for key in ('risk', 'relevance', 'confidence')] == ['Low', 'Medium', 'High']
    assert window.severity_badge.cget('text') == ('Low' if window._compact else 'Low priority')
    assert window.position.get().endswith('of 4')
    summary = window.assessment_box.get('1.0', 'end')
    assert 'magnitude 4.0 earthquake' in summary and 'Recommended actions' in summary
    assert 'watch_area' in window._action_targets.values()
    form = window.run_action('watch_area')
    assert form.fields['Title'].get() == 'Near Test location' and form.fields['Bounds (optional)'].get() == '177, 13, 180, 17'
    form.destroy()
    window.run_action('open_source')
    assert opened == ['https://earthquake.usgs.gov/example']
    window.assessment_view.set('related')
    window.render_assessment()
    assert 'No other retained observation' in window.assessment_box.get('1.0', 'end')
    for view in ('risk', 'context'):
        window.assessment_view.set(view)
        window.render_assessment()
        assert window.assessment_box.get('1.0', 'end').strip()
    first = window.selected_record['event']['id']
    window.step_event(1)
    pump_until(window, lambda: window.selected_record['event']['id'] != first)
    window.analyze_selected()
    pump_until(window, lambda: not window._busy)
    assert 'Analysis unavailable' in window.assessment_box.get('1.0', 'end')
    from tests.test_intelligence_insight import Model
    window.manager.model = Model()
    window.analyze_selected()
    pump_until(window, lambda: not window._busy)
    text = window.assessment_box.get('1.0', 'end')
    assert 'HAYAT analysis' in text and 'A strong earthquake' in text and 'private reasoning' not in text
    window.generate_report()
    pump_until(window, lambda: not window._busy)
    assert 'Hayat intelligence' in window.report_text.get('1.0', 'end')
    window.show_saved_brief()
    assert 'Hayat global intelligence' in window.report_text.get('1.0', 'end')


def test_nearest_camera_action_media_filter_and_publisher_page(explorer):
    from tests.test_intelligence_resources import NYC_ID, ExtendedFetcher
    from tests.test_public_intelligence import make_due, quake
    window, gui = explorer
    opened = []
    gui._open_source = opened.append
    window.manager.fetcher = ExtendedFetcher()
    window.manager.fetcher.payloads['earthquakes']['features'].append(quake('q5', 5.1, [-73.97, 40.78, 5]))
    make_due(window.manager, 'earthquakes')
    window.manager.refresh(list(window.manager.fetcher.payloads))
    without_tallinn(window.manager)
    window.render(force=True)
    window.select_event('earthquakes:q5')
    pump_until(window, lambda: window.selected_record is not None and window.selected_record['event']['id'] == 'earthquakes:q5')
    assert 'View the nearest public camera (snapshot)' in window.assessment_box.get('1.0', 'end')
    window.run_action('camera:cameras_nyc:' + NYC_ID)
    pump_until(window, lambda: not window._busy and window._camera_photo is not None)
    assert window.notebook.select() == str(window.tabs['cameras'])
    window.assessment_view.set('related')
    window.render_assessment()
    assert 'Nearest public cameras' in window.assessment_box.get('1.0', 'end')
    window.cameras_near_selection()
    pump_until(window, lambda: not window._busy and window.camera_rows)
    first = window.camera_tree.get_children()[0]
    assert first == 'cameras_nyc:' + NYC_ID and window.camera_tree.set(first, 'distance').endswith('km')
    assert 'nearest the selected event' in window.camera_message.get()
    window.camera_media.set('Publisher player')
    window.all_cameras()
    pump_until(window, lambda: not window._busy and len(window.camera_rows) == 8)
    window.camera_tree.selection_set('cameras_quebec:4057')
    window.update()
    window.open_publisher_page()
    assert opened == ['https://www.quebec511.info/Carte/Fenetres/FenetreVideo.html?id=4057']
    assert window.camera_tree.set('cameras_quebec:4057', 'media') == 'Publisher player'


def test_home_card_mini_map_draws_located_events_and_keeps_its_place(explorer):
    import tkinter as tk
    from app.gui_intelligence_home import IntelligenceHomeCard
    window, gui = explorer
    holder = tk.Frame(gui.root)
    holder.pack(fill='both', expand=True)
    card = IntelligenceHomeCard(holder, gui, pack={'fill': 'x', 'pady': (0, 8)}, show_map=True)
    try:
        card.render(*card.read())
        gui.root.update()
        assert len(card._map_rows) == 2  # The located earthquake and wildfire; cyber and space have no coordinates.
        assert card.map.find_all() and '2 located events' in card.map.itemcget(card.map.find_all()[-1], 'text')
        window.manager.preferences('save', {'show_home': False})
        card.render(*card.read())
        window.manager.preferences('save', {'show_home': True})
        card.render(*card.read())
        assert card.winfo_manager() == 'pack' and card.master is holder
    finally:
        card.close()
        card.destroy()
        holder.destroy()


def test_the_map_keeps_most_of_the_height_on_a_laptop_screen():
    """A fixed 380 px of cards left a 130 px map strip at 1536x900; 40% still left about 250 px (2026-10-10)."""
    assert IntelligenceWindow._cards_height(680) == 258
    assert IntelligenceWindow._cards_height(400) == 230
    assert IntelligenceWindow._cards_height(1400) == 340
