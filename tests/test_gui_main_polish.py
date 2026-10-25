"""Main-window layout and direct navigation checks, with optional visual captures."""
import os
from types import SimpleNamespace

import pytest

from tests.test_gui import _window  # noqa: F401 -- shared runtime fixture
from tests.test_gui import gui as _base_gui

gui = _base_gui


@pytest.fixture(autouse=True)
def restore_layout(gui):
    old_width = gui._overview_layout.desired_width
    old_choices = dict(gui._overview_layout.choices)
    geometry = gui.root.winfo_geometry()
    try:
        yield
    finally:
        gui._overview_layout.desired_width = old_width
        gui._overview_layout.choices = old_choices
        gui._focus_mode = False
        gui.root.geometry(geometry)
        gui.root.update()
        gui._overview_open = old_choices.get(gui.root.winfo_width() < 1380, True)
        gui._arrange_rails()
        gui.root.withdraw()


def show(gui, size):
    gui.root.geometry(size + '-4000-4000')
    gui.root.deiconify()
    gui.root.update()


def test_chat_keeps_global_intelligence_visible_beside_it(gui):
    show(gui, '1536x900')
    gui.show_view('chat')
    gui._append('user', 'You\nKeep the intelligence panel beside my chat.')
    gui.root.update()
    gui.right_rail._smooth.cancel()
    gui.right_rail.canvas.yview_moveto(0)
    gui.root.update()
    assert not gui.chat_details.expanded and gui.chat_details.winfo_height() < 120
    title = gui._intelligence_home.title_label
    assert title.winfo_ismapped()
    assert title.winfo_rooty() < gui.right_rail.canvas.winfo_rooty() + gui.right_rail.canvas.winfo_height() - 30


def test_overview_resize_keeps_chat_usable_and_compact_drawer_does_not_squeeze_it(gui):
    show(gui, '1536x900')
    layout = gui._overview_layout
    before = gui.chat.winfo_width()
    layout._start(SimpleNamespace(x_root=1000))
    layout._move(SimpleNamespace(x_root=900))
    layout._stop()
    gui.root.update()
    assert gui.right_rail.winfo_width() == 504
    assert before - gui.chat.winfo_width() == 100
    layout.resize_by(5000)
    gui.root.update()
    assert gui.chat.winfo_width() >= 560 and gui.right_rail.winfo_width() <= 600
    show(gui, '960x560')
    assert gui.root.winfo_width() == 960 and gui.root.winfo_height() == 560
    assert not gui.right_rail.winfo_ismapped()
    chat_width = gui.chat.winfo_width()
    gui._toggle_overview()
    gui.root.update()
    assert gui.chat.winfo_width() == chat_width
    assert layout.grip.winfo_ismapped() and gui.right_rail.winfo_width() <= 576
    layout._start(SimpleNamespace(x_root=600))
    gui._toggle_overview()
    assert layout.grip.grab_current() is not layout.grip and layout.drag is None


def test_overview_remembers_explicit_visibility_separately_for_wide_and_compact_windows(gui):
    show(gui, '1536x900')
    gui._toggle_overview()
    assert not gui._overview_open
    show(gui, '1040x700')
    assert not gui._overview_open
    show(gui, '1536x900')
    assert not gui._overview_open
    gui._toggle_overview()
    show(gui, '1040x700')
    assert not gui._overview_open


def test_compact_workspace_menu_keeps_major_tools_reachable(gui):
    show(gui, '1040x700')
    assert gui.workspace_overflow.winfo_ismapped()
    menu = gui.workspace_overflow_menu
    labels = [menu.entrycget(index, 'label') for index in range(menu.index('end') + 1)]
    assert {'Searches', 'Trade Center', 'Reverse engineering', 'My cameras', 'Global intelligence'} <= set(labels)
    assert gui.chat.winfo_height() >= 120


def test_compact_saved_window_can_be_restored_without_changing_default_start_size():
    from app.gui import minimum_size, window_placement
    assert minimum_size((1536, 900)) == (960, 560)
    assert window_placement({'geometry': '1040x700+20+20'}, (1536, 900)) == ('1040x700+20+20', False)
    assert window_placement(None, (1280, 720)) == ('1180x660+50+10', False)


@pytest.mark.skipif(os.environ.get('HAYAT_SHOTS') != '1', reason='screenshots on request')
def test_capture_main_layout(gui, monkeypatch, tmp_path):
    from tests.test_gui_screenshots import _grab, _pump
    monkeypatch.setattr(gui._folds, 'path', tmp_path / 'folds.json')
    root = gui.root
    root.deiconify()
    root.attributes('-topmost', True)
    try:
        root.geometry('1536x900+0+0')
        gui.chat_details.reset()
        gui._show_home()
        _pump(gui, .7)
        _grab(gui, '52-main-home')
        for index in range(18):
            gui._append('user' if index % 2 == 0 else 'assistant',
                        f'{"You" if index % 2 == 0 else "HAYAT"}\n'
                        + ('Review this interface and keep the research beside my conversation.' if index % 2 == 0
                           else 'The research panel keeps source-linked results nearby while you continue the conversation.\n'
                                'Open a workspace or type your next request below.'))
        gui._jump_to_latest()
        for size, name in [('1536x900', '53-main-conversation'), ('1040x700', '54-main-compact'),
                           ('960x560', '55-main-small')]:
            root.geometry(size + '+0+0')
            _pump(gui, .7)
            _grab(gui, name)
    finally:
        root.withdraw()
