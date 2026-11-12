"""Local task discovery and accessible launcher layout, using synthetic callbacks only."""
import os
import time
from pathlib import Path

import pytest
from tkinter import ttk

from app.gui_guidance import TASKS, TaskHome
from app.gui_theme import Palette, apply_text_scale, style_controls, style_hayat_buttons
from tests.conftest import close_window, open_window
from tests.test_gui import _window, gui  # noqa: F401


def settle(root):
    deadline = time.monotonic() + .15
    while time.monotonic() < deadline:
        root.update()
        time.sleep(.005)


@pytest.fixture
def launcher(shared_tk):
    window = open_window(shared_tk, '1120x680+20+20')
    ttk.Style(window).theme_use('clam')
    style_controls(window, Palette)
    style_hayat_buttons(window, Palette)
    called = []
    home = TaskHome(window, Palette, called.append)
    home.pack(fill='both', expand=True)
    settle(window)
    try:
        yield window, home, called
    finally:
        apply_text_scale(window, 1.0)
        close_window(window)


def test_search_categories_and_keyboard_open_real_callbacks(launcher):
    window, home, called = launcher
    assert len(home.visible_keys) == len(TASKS) == 9
    assert {'jobs', 'invention', 'chat', 'cameras', 'files', 'research', 'systems', 'lab', 'brain'} == set(home.buttons)
    home.query.set('security')
    settle(window)
    assert home.visible_keys == ('lab',)
    assert called == []  # filtering is navigation, never tool execution
    home._open_first()
    assert called == ['lab']
    home.clear_filters()
    home.category.set('System')
    home.category_picker.event_generate('<<ComboboxSelected>>')
    settle(window)
    assert home.visible_keys == ('systems', 'lab', 'brain')
    for key in home.visible_keys:
        home.buttons[key].invoke()
    assert called == ['lab', 'systems', 'lab', 'brain']
    home.query.set('nothing matches this')
    settle(window)
    assert not home.visible_keys and home.empty.winfo_ismapped()
    assert all(not card.winfo_ismapped() for card in home.cards)
    home._open_first()
    assert len(called) == 4
    home.clear_button.invoke()
    settle(window)
    assert len(home.visible_keys) == 9 and not home.empty.winfo_ismapped()


def test_new_home_destinations_dispatch_through_the_real_gui(gui, monkeypatch):  # noqa: F811
    called = []
    monkeypatch.setattr(gui, '_open_connections', lambda: called.append('systems'))
    monkeypatch.setattr(gui, '_open_research_lab', lambda: called.append('lab'))
    monkeypatch.setattr(gui, 'show_view', lambda key: called.append(key))
    gui._set_home_mode('tasks')
    for key in ('systems', 'lab', 'brain'):
        gui.task_home.buttons[key].invoke()
    assert called == ['systems', 'lab', 'operator']


@pytest.mark.parametrize('geometry,scale', [('1120x680+20+20', 1.0), ('800x520+20+20', 1.5),
                                           ('470x360+20+20', 1.5), ('800x250+20+20', 1.0)])
def test_layout_keeps_filter_and_every_destination_reachable(launcher, geometry, scale):
    window, home, _called = launcher
    apply_text_scale(window, scale)
    window.geometry(geometry)
    settle(window)
    right = window.winfo_rootx() + window.winfo_width()
    for control in (home.search_entry, home.category_picker, home.clear_button):
        assert control.winfo_viewable()
        assert control.winfo_rootx() >= window.winfo_rootx()
        assert control.winfo_rootx() + control.winfo_width() <= right
    for card in home.cards:
        assert card.winfo_x() >= 0
        assert card.winfo_x() + card.winfo_width() <= home.body.winfo_width() + 1
    home._reveal(home.cards[-1])
    settle(window)
    assert home.cards[-1].winfo_y() + home.cards[-1].winfo_height() <= home.canvas.canvasy(home.canvas.winfo_height()) + 3
    if os.environ.get('HAYAT_SHOTS') == '1':
        from PIL import ImageGrab
        folder = Path('.test-runs/gui-shots')
        folder.mkdir(parents=True, exist_ok=True)
        name = 'task-home-' + geometry.split('+')[0] + '-' + str(scale)
        home.canvas.yview_moveto(0)
        settle(window)
        ImageGrab.grab(window=int(window.wm_frame(), 16)).save(folder / (name + '.png'))
