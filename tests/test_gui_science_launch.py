"""Scientific entry points preserve navigation, responsive layout and unsent text."""
import os
import time

import pytest
from tkinter import ttk

from app.gui_guidance import TaskHome
from app.gui_theme import Palette, apply_text_scale, style_controls, style_hayat_buttons
from tests.conftest import close_window, open_window
from tests.test_gui import _window, gui  # noqa: F401


def settle(root, seconds=.15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(.005)


@pytest.mark.parametrize('geometry,scale', [('1120x680+20+20', 1.0), ('470x360+20+20', 1.5)])
def test_science_launcher_is_reachable_and_opens_without_running_tools(shared_tk, geometry, scale):
    root = open_window(shared_tk, geometry)
    ttk.Style(root).theme_use('clam')
    style_controls(root, Palette)
    style_hayat_buttons(root, Palette)
    called = []
    home = TaskHome(root, Palette, called.append)
    home.pack(fill='both', expand=True)
    try:
        apply_text_scale(root, scale)
        settle(root)
        launch = home.science_launch
        home._reveal(launch)
        settle(root)
        button = launch.open_button
        assert button.winfo_viewable()
        assert button.winfo_rootx() >= root.winfo_rootx()
        assert button.winfo_rootx() + button.winfo_width() <= root.winfo_rootx() + root.winfo_width()
        assert called == []
        button.invoke()
        assert called == ['science']
        home.query.set('security')
        settle(root)
        assert home.visible_keys == ('lab',)
        assert called == ['science']
        assert not launch.winfo_ismapped()
        home.query.set('csv')
        settle(root)
        assert not home.visible_keys and launch.winfo_ismapped() and not home.empty.winfo_ismapped()
        home._open_first()
        assert called == ['science', 'science']
    finally:
        apply_text_scale(root, 1.0)
        close_window(root)


def test_scientific_workbench_home_and_menu_routes_preserve_draft(gui, monkeypatch):  # noqa: F811
    called = []
    monkeypatch.setattr(gui, '_open_scientific_workbench', lambda: called.append('science'))
    gui.input.delete('1.0', 'end')
    gui.input.insert('1.0', 'Keep my unfinished experiment question.')
    gui._show_home()
    gui.task_home.science_launch.open_button.invoke()
    assert called == ['science']
    assert gui.input.get('1.0', 'end-1c') == 'Keep my unfinished experiment question.'
    for menu in (gui.workspace_overflow_menu, gui._studios_menu):
        labels = [menu.entrycget(index, 'label') for index in range(menu.index('end') + 1)
                  if menu.type(index) == 'command']
        assert any('Scientific Workbench' in label for label in labels)
    from app.gui_features import feature_inventory
    assert any(row.key == 'studio:_open_scientific_workbench' for row in feature_inventory(gui))


def test_short_home_keeps_the_featured_launch_and_a_task_row_visible(shared_tk):
    root = open_window(shared_tk, '1040x300+20+20')
    home = TaskHome(root, Palette, lambda _key: None)
    home.pack(fill='both', expand=True)
    try:
        settle(root)
        assert home.science_launch._compact
        first = home.cards[0]
        assert first.winfo_y() + first.winfo_height() <= home.canvas.winfo_height()
        assert home.science_launch.open_button.winfo_viewable()
    finally:
        close_window(root)


@pytest.mark.skipif(os.environ.get('HAYAT_SHOTS') != '1', reason='screenshots on request')
def test_capture_scientific_home(gui, monkeypatch, tmp_path):  # noqa: F811
    from tests.test_gui_screenshots import _grab, _pump
    from app.gui_theme import apply_theme
    monkeypatch.setattr(gui._folds, 'path', tmp_path / 'folds.json')
    original = gui._theme_name
    try:
        gui.root.deiconify()
        gui.root.attributes('-topmost', True)
        gui._show_home()
        gui._set_home_mode('tasks')
        apply_theme(gui.root, type(gui), 'Nebula')
        for geometry, name in [('1536x900+0+0', 'science-home-nebula'),
                               ('1040x700+20+20', 'science-home-compact')]:
            gui.root.geometry(geometry)
            gui.task_home.canvas.yview_moveto(0)
            _pump(gui, .5)
            _grab(gui, name)
        gui._open_scientific_workbench()
        studio = gui._scientific_workbench
        studio.geometry('1180x780+20+20')
        studio.attributes('-topmost', True)
        studio.load_demo()
        _pump(gui, 1)
        studio.columns_tree.selection_set('column-1')
        studio._select_column()
        _grab(gui, 'science-workbench-demo', studio)
        for page in ('Relationships', 'Preview'):
            studio.tabs.select(studio.pages[page])
            _pump(gui, .15)
            _grab(gui, 'science-workbench-' + page.lower(), studio)
        studio.tabs.select(studio.pages['Profile'])
        studio.geometry('760x520+20+20')
        apply_text_scale(studio, 1.5)
        _pump(gui, .3)
        _grab(gui, 'science-workbench-large-text', studio)
        studio.destroy()
    finally:
        apply_theme(gui.root, type(gui), original)
        gui.root.withdraw()
