"""Main-window space, keyboard navigation and draft continuity."""
import os

import pytest

from tests.test_gui import _window as _window, gui as gui


@pytest.fixture(autouse=True)
def restore_main_layout(gui):
    from app.gui_theme import apply_text_scale
    layout = gui._navigation_layout
    choices = dict(layout.choices)
    geometry = gui.root.geometry()
    scale = getattr(gui.root, '_hayat_text_scale', 1.0)
    try:
        yield
    finally:
        gui.workspace_navigation.query.set('')
        gui._focus_mode = False
        layout.choices = choices
        layout.return_focus = None
        apply_text_scale(gui.root, scale)
        gui.root.geometry(geometry)
        gui.root.update()
        gui._arrange_rails()
        gui.root.withdraw()


def show(gui, size):
    gui.root.geometry(size + '-4000-4000')
    gui.root.deiconify()
    gui.root.update()
    gui._jump_to_latest()
    gui.root.update()


def inside(widget, parent):
    left, top = parent.winfo_rootx(), parent.winfo_rooty()
    assert widget.winfo_ismapped()
    assert left <= widget.winfo_rootx()
    assert top <= widget.winfo_rooty()
    assert widget.winfo_rootx() + widget.winfo_width() <= left + parent.winfo_width()
    assert widget.winfo_rooty() + widget.winfo_height() <= top + parent.winfo_height()


def test_docking_and_drawer_keep_independent_choices_without_changing_draft(gui):
    show(gui, '1536x900')
    gui.input.insert('1.0', 'Keep this draft while I explore')
    before = gui.input.get('1.0', 'end-1c')
    width = gui._centre.winfo_width()
    gui._toggle_navigation()
    gui.root.update()
    assert not gui._sidebar.winfo_ismapped()
    assert gui._centre.winfo_width() > width
    gui._toggle_navigation()
    show(gui, '960x560')
    layout = gui._navigation_layout
    assert layout.compact and not layout.visible
    width = gui._centre.winfo_width()
    gui._toggle_navigation()
    gui.root.update()
    assert layout.drawer_open and gui._sidebar.winfo_manager() == 'place'
    assert gui._centre.winfo_width() == width
    assert layout.shade.winfo_ismapped()
    assert gui.workspace_navigation.column.canvas.winfo_height() >= 120
    inside(gui.workspace_navigation.entry, gui._sidebar)
    layout.shade.event_generate('<Button-1>')
    gui.root.update()
    assert not layout.drawer_open and not layout.shade.winfo_ismapped()
    show(gui, '1536x900')
    assert gui._sidebar.winfo_manager() == 'pack'
    assert gui.input.get('1.0', 'end-1c') == before


def test_escape_clears_search_then_closes_drawer_without_cancelling_work(gui, monkeypatch):
    show(gui, '960x560')
    cancelled = []
    monkeypatch.setattr(gui.runtime.agent, 'cancel_current', lambda: cancelled.append(True))
    gui.root.focus_force()
    gui.input.focus_set()
    gui._focus_workspace_search()
    navigation = gui.workspace_navigation
    navigation.query.set('invention')
    gui.root.update()
    navigation.entry.event_generate('<Escape>')
    gui.root.update()
    assert navigation.query.get() == '' and gui._navigation_layout.drawer_open
    navigation.entry.event_generate('<Escape>')
    gui.root.update()
    assert not gui._navigation_layout.drawer_open
    assert gui.root.focus_get() is gui.input
    assert cancelled == []


def test_selecting_a_workspace_dismisses_drawer_and_keeps_draft(gui):
    show(gui, '960x560')
    gui.input.insert('1.0', 'This is still unsent')
    gui._focus_workspace_search()
    gui.workspace_navigation.query.set('invention')
    gui.workspace_navigation.open_first()
    gui.root.update()
    assert gui.current_view == 'invention'
    assert not gui._navigation_layout.drawer_open
    assert gui.input.get('1.0', 'end-1c') == 'This is still unsent'
    gui.show_view('chat')


def test_drawer_tab_traversal_skips_covered_controls_and_reaches_emergency_stop(gui):
    show(gui, '960x560')
    gui.root.focus_force()
    gui._focus_workspace_search()
    gui.root.update()
    visited = []
    for _ in range(80):
        widget = gui.root.focus_get()
        assert widget is not None
        visited.append(widget)
        ancestor = widget
        while ancestor not in (None, gui._sidebar, gui._body):
            ancestor = getattr(ancestor, 'master', None)
        assert ancestor is not gui._body
        if widget in gui.workspace_navigation.items.values():
            inside(widget, gui.workspace_navigation.column.canvas)
        widget.event_generate('<Tab>')
        gui.root.update()
    assert gui.emergency_button in visited
    assert gui.navigation_button in visited
    assert gui.workspace_navigation.entry in visited
    assert gui.input not in visited


def test_header_search_opens_real_command_palette_and_runs_navigation_action(gui):
    show(gui, '1180x760')
    inside(gui.navigation_button, gui.root)
    inside(gui.command_search_button, gui.root)
    gui.command_search_button.invoke()
    palette = gui._command_palette
    palette.query.set('Toggle navigation')
    palette.refresh()
    assert palette.visible[0].label == 'Toggle navigation'
    previous = gui._navigation_layout.visible
    palette.execute()
    gui.root.update()
    assert gui._navigation_layout.visible != previous
    assert ('<Control-Shift-B>', '_toggle_navigation') in gui.SHORTCUTS


def test_live_text_size_changes_navigation_without_a_window_resize(gui):
    show(gui, '1180x760')
    geometry = gui.root.geometry()
    assert not gui._navigation_layout.compact
    try:
        gui._set_text_scale(1.5)
        gui.root.update()
        assert gui._navigation_layout.compact
        assert gui.root.geometry() == geometry
        assert not gui._sidebar.winfo_ismapped()
        gui._set_text_scale(1.0)
        gui.root.update()
        assert not gui._navigation_layout.compact
        assert gui._sidebar.winfo_manager() == 'pack'
    finally:
        gui._set_text_scale(1.0)


@pytest.mark.parametrize('size,scale', [('960x560', 1.0), ('1180x760', 1.5), ('1536x900', 1.0)])
def test_main_controls_and_long_activity_stay_inside_window(gui, size, scale):
    from app.gui_theme import apply_text_scale
    show(gui, size)
    apply_text_scale(gui.root, scale)
    gui.root.update()
    gui.activity_var.set('Researching: ' + 'a long operation description ' * 30)
    gui.activity_progress_var.set(40)
    # Paint geometry without the regular live-state tick replacing this
    # deliberately long status with the real idle state.
    gui.root.update_idletasks()
    for widget in (gui.input, gui.send_button, gui.command_search_button,
                   gui.navigation_button, gui.reasoning_button, gui._activity_progress):
        inside(widget, gui.root)
    assert gui.chat.winfo_height() >= 120
    assert gui._activity_label.winfo_width() > 50
    assert gui._activity_label.winfo_rootx() + gui._activity_label.winfo_width() <= gui._activity_progress.winfo_rootx()
    assert not gui.reply_stop_button.winfo_manager()
    if gui._very_short:
        assert int(gui._transparency_state_label.cget('wraplength')) == 0
    gui._focus_workspace_search()
    gui.root.update()
    assert gui.workspace_navigation.column.canvas.winfo_height() >= 120
    inside(gui.workspace_navigation.entry, gui._sidebar)
    assert gui.emergency_button.winfo_ismapped()
    gui.activity_progress_var.set(0)


@pytest.mark.skipif(os.environ.get('HAYAT_SHOTS') != '1', reason='native capture on request')
def test_capture_main_upgrade(gui):
    from app.gui_theme import apply_text_scale
    from tests.test_gui_screenshots import _grab, _pump
    gui.root.deiconify()
    try:
        for size, name in [('1536x900', '141-main-upgraded-wide'), ('960x560', '142-main-upgraded-compact')]:
            gui.root.geometry(size + '+0+0')
            gui._jump_to_latest()
            gui._append('user', 'You\nHelp me turn an idea into a tested invention.')
            gui._append('assistant', 'HAYAT\nStart with a measurable goal and a fixed set of cases. '
                        'The Invention workspace keeps your experiments, evidence and unit checks together.')
            _pump(gui, .5)
            _grab(gui, name)
        gui._focus_workspace_search()
        _pump(gui, .3)
        _grab(gui, '143-main-navigation-drawer')
        gui._navigation_layout.dismiss()
        gui.root.geometry('1180x760+0+0')
        apply_text_scale(gui.root, 1.5)
        _pump(gui, .4)
        _grab(gui, '144-main-enlarged-text')
    finally:
        gui.root.withdraw()
