"""Navigation, coverage and accessible dense-map inspection in the desktop GUI."""
from types import SimpleNamespace

import pytest

from app.intelligence.view import MapViewport
from tests.test_gui_intelligence import explorer, pump_until  # noqa: F401 -- fixture


@pytest.mark.parametrize('points', [
    [(179, 15), (-179, 17)], [(-179, -20), (179, -22), (180, -21)],
    [(24, 5)], [(-30, -15), (60, 30), (45, 0)], [(0, 85), (12, 89)],
])
def test_fit_results_contains_valid_points_across_the_dateline(points):
    view = MapViewport()
    assert view.fit_points({'longitude': lon, 'latitude': lat} for lon, lat in points)
    for lon, lat in points:
        x, y = view.project(lon, lat, 1000, 600)
        assert 0 <= x <= 1000 and 0 <= y <= 600
    if all(abs(lon) >= 179 for lon, _lat in points):
        assert view.zoom > 8 and abs(view.longitude) > 175


def test_fit_results_ignores_unknown_and_invalid_coordinates():
    view = MapViewport(4, 20, 10)
    before = vars(view).copy()
    assert not view.fit_points([{'longitude': None, 'latitude': None}, {'longitude': 500, 'latitude': 10},
                                {'longitude': float('nan'), 'latitude': 0}])
    assert vars(view) == before


def test_coverage_counts_visible_markers_separately_from_the_loaded_subset(explorer):  # noqa: F811
    window, _gui = explorer
    window.update()
    base = window.map_rows['earthquakes:q1']
    window.map_rows = {
        'inside': {**base, 'id': 'inside', 'longitude': 0, 'latitude': 0},
        'outside': {**base, 'id': 'outside', 'longitude': 150, 'latitude': 45},
    }
    window.current_dashboard['mapped_total'] = 1200
    window.viewport = MapViewport(8, 0, 0)
    window.draw()
    assert window.map_metrics['visible']['text'] == '1'
    assert window.map_metrics['loaded']['text'].replace(' ', '') == '2/1,200'
    assert [row['id'] for row in window._visible_map_rows] == ['inside']
    window.viewport.longitude = -90
    window.draw()
    assert window.map_metrics['visible']['text'] == '0'
    assert window.map.find_withtag('empty-map')
    assert window.map_actions[4].instate(['disabled'])


def test_expand_restore_and_escape_keep_the_window_and_manual_split(explorer):  # noqa: F811
    window, _gui = explorer
    window.geometry('1450x900-4000-4000')
    window.update()
    window._sash_user = True
    window.map_split.sash_place(0, 0, 310)
    window.update()
    old_height = window.map.winfo_height()
    old_split = window.map_split.sash_coord(0)
    window.toggle_map_focus()
    window.update()
    assert window._map_focus and not window.cards_frame.winfo_ismapped() and not window.live.winfo_ismapped()
    assert window.map.winfo_height() > old_height + 100
    window.escape_map()
    window.update()
    assert window.winfo_exists() and not window._map_focus and window.cards_frame.winfo_ismapped()
    assert window.map_split.sash_coord(0) == old_split and window._sash_user


def test_expanded_map_survives_resize_and_selection_opens_the_inspector(explorer):  # noqa: F811
    window, _gui = explorer
    window.toggle_map_focus()
    window.geometry('760x580-4000-4000')
    window.update()
    assert window._compact and not window.cards_frame.winfo_ismapped()
    assert window.map.winfo_width() > 650
    window.select_event('earthquakes:q1')
    pump_until(window, lambda: window.selected_record and window.selected_record['event']['id'] == 'earthquakes:q1')
    window.update()
    assert not window._map_focus and window.cards_frame.winfo_ismapped()
    assert window.map.winfo_width() >= 300 and window.detail_text.winfo_height() >= 100


def test_dense_high_zoom_cluster_lists_each_report_and_selects_by_keyboard(explorer):  # noqa: F811
    window, _gui = explorer
    window.update()
    rows = [window.rows['earthquakes:q1'], window.rows['natural_events:n1']]
    window.viewport.zoom = 16
    window._clusters = [(100, 100, rows)]
    window.map.delete('all')
    window._draw_clusters()
    window.select_marker(SimpleNamespace(x=100, y=100))
    dialog = window._browse_dialog
    assert dialog is not None
    assert set(dialog.tree.get_children()) == {row['id'] for row in rows}
    dialog.geometry('480x280-4000-4000')
    window.update()
    for button in dialog.actions.winfo_children():
        assert button.winfo_ismapped()
        assert button.winfo_rooty() + button.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()
    dialog.tree.selection_set('earthquakes:q1')
    dialog.tree.event_generate('<Return>')
    window.update()
    # Direct invocation also covers platforms where off-screen synthetic keys lack focus.
    if dialog.winfo_exists():
        dialog.choose()
    pump_until(window, lambda: window.selected_record and window.selected_record['event']['id'] == 'earthquakes:q1')
    assert not dialog.winfo_exists()


def test_clear_filters_restores_map_and_invalidates_obsolete_assessment(explorer):  # noqa: F811
    window, _gui = explorer
    window.query.set('no matching observations anywhere')
    window.update()
    assert not window.selected_record and not window.map_rows
    assert all(tile['text'] == '—' for tile in window.tiles.values())
    assert 'Select a matching observation' in window.assessment_box.get('1.0', 'end')
    window.region.set('Europe')
    window.only_viewport.set(True)
    for variable in window.layer_vars.values():
        variable.set(False)
    window.clear_map_filters()
    window.update()
    assert window.query.get() == '' and window.region.get() == 'World' and not window.only_viewport.get()
    assert window.viewport.zoom == 1 and window.map_rows
    assert not window.layer_vars['cameras'].get()
    assert all(variable.get() for key, variable in window.layer_vars.items() if key != 'cameras')


def test_hidden_layers_remove_selected_marker_and_keep_a_useful_empty_state(explorer):  # noqa: F811
    window, _gui = explorer
    window.select_event('earthquakes:q1')
    pump_until(window, lambda: window.selected_record and window.selected_record['event']['id'] == 'earthquakes:q1')
    for variable in window.layer_vars.values():
        variable.set(False)
    window.layer_toggled()
    assert not window.map.find_withtag('focus') and not window.map.find_withtag('callout')
    assert window.map.find_withtag('empty-map')
    assert not window.map_rows


def test_hover_card_and_open_layers_stay_inside_the_map(explorer):  # noqa: F811
    window, _gui = explorer
    window.geometry('760x580-4000-4000')
    window.update()
    width, height = window.size()
    window.draw_map_hover(SimpleNamespace(x=width-3, y=height-3), identifier='earthquakes:q1')
    bounds = window.map.bbox('hover-card')
    assert bounds and bounds[0] >= 0 and bounds[1] >= 0 and bounds[2] <= width and bounds[3] <= height
    window.toggle_legend()
    window.update()
    assert window.legend.winfo_y() + window.legend.winfo_height() <= window.map.winfo_height()
    last = window.legend_layers.winfo_children()[-1].winfo_children()[-1]
    last.event_generate('<FocusIn>')
    window.update()
    assert window.legend_canvas.yview()[1] > .95


def test_narrow_coverage_values_fit_and_search_has_a_keyboard_route(explorer):  # noqa: F811
    window, _gui = explorer
    window.geometry('760x580-4000-4000')
    window.update()
    for key in ('visible', 'loaded'):
        label = window.map_metrics[key]
        assert label.winfo_ismapped()
        assert label.winfo_x() + label.winfo_width() <= label.master.winfo_width()
    assert not window.severity_badge.winfo_ismapped()
    assert window.bind('<Control-f>') and window.bind('<F11>') and window.map.bind('<Return>')
    window.focus_map_search()
    assert window.search_entry.selection_present() or window.query.get() == ''
