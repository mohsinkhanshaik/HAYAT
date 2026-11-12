"""Real routing, motion cancellation, axes and elapsed-time behavior."""
from types import SimpleNamespace

import pytest

from app.gui_motion import MOTION, SmoothScroller, attach, set_motion
from app.gui_navigation import NavigationHistory
from tests.test_gui import _window as _window, gui as gui


def test_history_back_forward_and_branch_are_bounded_and_deduplicated():
    history = NavigationHistory(limit=3)
    for key in ('chat', 'chat', 'models', 'invention'):
        history.push(key)
    assert history.entries == ['chat', 'models', 'invention']
    assert history.move(-1) == 'models' and history.can_forward
    history.push('memory')
    assert not history.can_forward and history.entries == ['chat', 'models', 'memory']
    history.push('health')
    assert history.entries == ['models', 'memory', 'health']
    assert history.move(1) is None


def test_workspace_history_keeps_draft_and_has_working_buttons(gui):
    original = gui.input.get('1.0', 'end-1c')
    gui.input.delete('1.0', 'end'); gui.input.insert('1.0', 'Keep this unsent draft')
    gui.show_view('chat')
    gui.show_view('invention')
    gui._navigate_back()
    assert gui.current_view == 'chat'
    gui.workspace_navigation.forward_button.invoke()
    assert gui.current_view == 'invention'
    assert gui.input.get('1.0', 'end-1c') == 'Keep this unsent draft'
    gui.input.delete('1.0', 'end'); gui.input.insert('1.0', original)


class View:
    def __init__(self): self.position = .25
    def yview(self): return self.position, self.position+.1
    def yview_moveto(self, value): self.position = value
    def winfo_height(self): return 100
    def after(self, *_args): return 'fake-timer'
    def after_cancel(self, *_args): pass


def test_reversal_starts_from_visible_position_and_reduced_motion_settles():
    previous = MOTION['enabled']
    set_motion(True)
    view = View()
    scroller = SmoothScroller(view)
    try:
        scroller.wheel(SimpleNamespace(delta=-1200))
        scroller.wheel(SimpleNamespace(delta=120))
        assert scroller.target < view.position
        target = scroller.target
        set_motion(False)
        assert view.position == target and scroller.job is None and scroller.target is None
    finally:
        scroller.cancel()
        set_motion(previous)


def test_smoothing_uses_elapsed_time_not_frame_count(monkeypatch):
    import app.gui_motion as motion
    previous = MOTION['enabled']
    set_motion(True)
    clock = [100.0]
    monkeypatch.setattr(motion.time, 'monotonic', lambda: clock[0])
    positions = []
    for frames, delay in ((6, .015), (3, .030)):
        clock[0] = 100
        view = View()
        scroller = SmoothScroller(view)
        scroller.wheel(SimpleNamespace(delta=-1200))
        for _ in range(frames):
            clock[0] += delay
            scroller._tick()
        positions.append(view.position)
        scroller.cancel()
    set_motion(previous)
    assert positions[0] == pytest.approx(positions[1], abs=1e-6)


def test_shift_wheel_scrolls_horizontally_and_nested_edge_hands_off(shared_tk):
    import tkinter as tk
    from app.gui_panels import ScrollableColumn
    from app.gui_theme import Palette
    from tests.conftest import open_window, close_window
    root = open_window(shared_tk, '440x320')
    previous = MOTION['enabled']
    set_motion(False)
    try:
        column = ScrollableColumn(root, Palette)
        column.pack(fill='both', expand=True)
        text = tk.Text(column.body, height=3, width=24, wrap='none')
        text.pack(fill='x')
        text.insert('1.0', ('very long line '*100+'\n')*100)
        attach(text)
        tk.Label(column.body, text='A long form\n'*80).pack()
        root.update()
        text.event_generate('<Shift-MouseWheel>', delta=-120, state=1)
        root.update()
        assert text.xview()[0] > 0 and text.yview()[0] == 0
        text.yview_moveto(1)
        root.update()
        text.event_generate('<MouseWheel>', delta=-120)
        root.update()
        assert column.canvas.yview()[0] > 0
        assert text.yview()[1] == pytest.approx(1, abs=.002)
    finally:
        close_window(root)
        set_motion(previous)


def test_navigation_marker_stops_when_motion_is_disabled_or_filtered(gui):
    navigation = gui.workspace_navigation
    previous = MOTION['enabled']
    try:
        set_motion(False)
        navigation.query.set('')
        gui.root.deiconify()
        gui.root.update()
        navigation.set_active('invention')
        gui.root.update_idletasks()
        assert navigation._marker_job is None
        assert navigation.marker.winfo_y() == navigation.items['invention'].winfo_y()+4
        navigation.query.set('lora')
        assert not navigation.marker.winfo_manager()
        navigation.query.set('')
    finally:
        set_motion(previous)
