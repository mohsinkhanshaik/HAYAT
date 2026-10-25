"""A movable, screen-fitting workspace must preserve map and event interaction."""
import os
from types import SimpleNamespace
from tkinter import ttk

import pytest

from app.gui_theme import Palette, style_controls, style_hayat_buttons
from tests.test_gui_intelligence import explorer, pump_until  # noqa: F401 -- fixture


def test_header_moves_the_window_and_map_drag_still_pans_only_the_map(explorer):  # noqa: F811
    window, _gui = explorer
    window.geometry('1000x700+120+100')
    window.update()
    before = (window.winfo_x(), window.winfo_y())
    title = window.title_label
    x, y = title.winfo_rootx() + 20, title.winfo_rooty() + 10
    title.event_generate('<ButtonPress-1>', x=20, y=10, rootx=x, rooty=y)
    title.event_generate('<B1-Motion>', x=70, y=45, rootx=x + 50, rooty=y + 35)
    title.event_generate('<ButtonRelease-1>', x=70, y=45, rootx=x + 50, rooty=y + 35)
    window.update()
    assert (window.winfo_x(), window.winfo_y()) == (before[0] + 50, before[1] + 35)
    assert window._window_drag is None
    position = (window.winfo_x(), window.winfo_y())
    center = (window.viewport.longitude, window.viewport.latitude)
    window.map.event_generate('<ButtonPress-1>', x=100, y=80)
    window.map.event_generate('<B1-Motion>', x=160, y=100)
    window.map.event_generate('<ButtonRelease-1>', x=160, y=100)
    window.update()
    assert (window.winfo_x(), window.winfo_y()) == position
    assert (window.viewport.longitude, window.viewport.latitude) != center


def test_header_can_move_onto_a_monitor_left_of_the_primary(explorer):  # noqa: F811
    window, _gui = explorer
    window.geometry('1000x700+100+100')
    window.update()
    window._start_window_drag(SimpleNamespace(x_root=120, y_root=115))
    window._move_window(SimpleNamespace(x_root=-180, y_root=115))
    window._end_window_drag()
    window.update()
    assert window.winfo_x() == -200 and window.winfo_y() == 100


def test_window_controls_allow_maximize_restore_resize_and_work_in_hayat(explorer):  # noqa: F811
    window, gui = explorer
    assert not str(window.transient())
    assert all(window.resizable())
    window.geometry('1000x700+60+60')
    window.update()
    window.toggle_window_size()
    pump_until(window, lambda: window.state() == 'zoomed')
    window.toggle_window_size()
    pump_until(window, lambda: window.state() == 'normal')
    window.toggle_window_size()
    pump_until(window, lambda: window.state() == 'zoomed')
    pointer = SimpleNamespace(x_root=window.winfo_rootx() + 200, y_root=window.winfo_rooty() + 20)
    window._start_window_drag(pointer)
    window._move_window(SimpleNamespace(x_root=pointer.x_root + 100, y_root=pointer.y_root + 40))
    window._end_window_drag()
    pump_until(window, lambda: window.state() == 'normal')
    assert window.title_label.bind('<Double-Button-1>')
    window.query.set('earthquake')
    window.select_event('earthquakes:q1')
    pump_until(window, lambda: window.selected_record is not None)
    window.geometry('900x650+80+80')
    window.update()
    assert window.query.get() == 'earthquake'
    assert window.selected_record['event']['id'] == 'earthquakes:q1'
    assert window.resize_grip.winfo_ismapped()
    # This is an independent modeless window; parent controls keep receiving input.
    called = []
    button = ttk.Button(gui.root, command=lambda: called.append(True))
    button.invoke()
    assert called == [True] and window.grab_current() is None
    button.destroy()


@pytest.mark.parametrize('area', [(0, 30, 1536, 864), (0, 0, 960, 500), (-1280, 40, 0, 780)])
def test_fit_recovers_an_oversized_offscreen_window_and_keeps_controls_reachable(explorer, monkeypatch, area):  # noqa: F811
    window, _gui = explorer
    monkeypatch.setattr('app.gui_intelligence.work_area', lambda _widget: area)
    window.geometry('1800x1100+2200+1400')
    window.update()
    window.fit_window()
    window.update()
    x, y, w, h = window.winfo_x(), window.winfo_y(), window.winfo_width(), window.winfo_height()
    assert area[0] <= x and x + w <= area[2]
    assert area[1] <= y and y + h + 30 <= area[3]
    assert h < 1100 and window.map.winfo_height() > 80
    for control in (window.window_button, window.resize_grip):
        assert control.winfo_ismapped()
        assert control.winfo_rootx() + control.winfo_width() <= window.winfo_rootx() + w
        assert control.winfo_rooty() + control.winfo_height() <= window.winfo_rooty() + h
    if h < 560:
        assert not window.filter_row.winfo_ismapped()
        window.focus_map_search()
        window.update()
        assert window.search_entry.winfo_ismapped()
        window.show_filter_bar(False)
        window.update()
    else:
        assert window.search_entry.winfo_ismapped()


def test_global_intelligence_window_visual_review(explorer):  # noqa: F811
    window, gui = explorer
    if os.environ.get('HAYAT_SHOTS') != '1':
        assert window.window_button.winfo_exists()
        return
    from tests.test_gui_screenshots import _grab, _pump
    style = ttk.Style(window)
    previous = style.theme_use()
    style.theme_use('clam')
    style_controls(window, Palette)
    style_hayat_buttons(window, Palette)
    try:
        window.geometry('1100x740+30+30')
        window.attributes('-topmost', True)
        window.deiconify()
        window.lift()
        window.select_event('earthquakes:q1')
        pump_until(window, lambda: window.selected_record is not None)
        _pump(gui, .5)
        _grab(gui, '35-global-intelligence-movable', widget=window)
        window._window_size((0, 0, 960, 500))
        window.geometry('820x436+30+30')
        _pump(gui, .5)
        _grab(gui, '36-global-intelligence-short-screen', widget=window)
    finally:
        style.theme_use(previous)
