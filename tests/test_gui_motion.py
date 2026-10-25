"""Smooth scrolling: the wheel glides, keeps going with more notches, and yields to the scrollbar."""
import time
import tkinter as tk
from types import SimpleNamespace

import pytest

from app.gui_motion import MOTION, SmoothScroller, attach, near_bottom, reading_position, set_motion


@pytest.fixture
def text(shared_tk):
    from tests.conftest import open_window
    root = open_window(shared_tk, "400x300-4000-4000")
    root.geometry("400x300")
    widget = tk.Text(root, height=10, width=40)
    widget.pack(fill="both", expand=True)
    widget.insert("end", "\n".join(f"line {number}" for number in range(400)))
    root.update()
    previous = MOTION["enabled"]
    try:
        yield widget
    finally:
        set_motion(previous)
        root.destroy()


def pump(widget, seconds=1.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        widget.update()
        time.sleep(0.005)


def test_a_wheel_notch_glides_in_frames_to_its_target(text):
    set_motion(True)
    scroller = SmoothScroller(text)
    scroller.wheel(SimpleNamespace(delta=-120))
    target = scroller.target
    assert target and target > 0 and text.yview()[0] == 0.0  # nothing jumped yet
    text.update(); time.sleep(0.03); text.update()
    midway = text.yview()[0]
    assert 0 < midway < target  # part of the way: a glide, not a jump
    pump(text, 0.8)
    assert scroller.target is None and abs(text.yview()[0] - target) < 0.002


def test_more_notches_extend_the_glide_and_up_scrolls_back(text):
    set_motion(True)
    scroller = SmoothScroller(text)
    scroller.wheel(SimpleNamespace(delta=-120))
    one = scroller.target
    scroller.wheel(SimpleNamespace(delta=-240))
    assert scroller.target == pytest.approx(one * 3, rel=0.01)
    pump(text, 0.8)
    scroller.wheel(SimpleNamespace(delta=120))
    pump(text, 0.8)
    assert text.yview()[0] == pytest.approx(one * 2, abs=0.002)


def test_paused_motion_moves_instantly(text):
    set_motion(False)
    scroller = SmoothScroller(text)
    scroller.wheel(SimpleNamespace(delta=-120))
    assert scroller.target is None and text.yview()[0] > 0


def test_dragging_the_scrollbar_cancels_a_glide(text):
    set_motion(True)
    scroller = SmoothScroller(text)
    scroller.wheel(SimpleNamespace(delta=-1200))
    text.update(); time.sleep(0.03); text.update()
    text.yview_moveto(0.5)  # the owner drags the bar
    pump(text, 0.5)
    assert scroller.target is None and text.yview()[0] == pytest.approx(0.5, abs=0.002)


def test_the_themed_text_panels_scroll_smoothly(shared_tk):
    from tests.conftest import open_window
    root = open_window(shared_tk, hidden=True)
    try:
        from app.gui_theme import DarkScrolledText

        panel = DarkScrolledText(root)
        assert isinstance(panel._smooth_scroller, SmoothScroller)
        assert panel.bind("<MouseWheel>")
    finally:
        root.destroy()


def test_wheel_over_an_embedded_card_scrolls_its_chat(text):
    set_motion(False)
    attach(text)
    card = tk.Frame(text)
    label = tk.Label(card, text="A document preview")
    label.pack()
    text.window_create("1.0", window=card)
    text.update()
    label.event_generate("<MouseWheel>", delta=-120)
    text.update()
    assert text.yview()[0] > 0


def test_reading_position_keeps_the_visible_line_and_follows_only_at_bottom(text):
    text.yview_moveto(0.4)
    text.update()
    before = text.index("@0,0")
    with reading_position(text):
        text.insert("end", "\n" + "new reply\n" * 100)
    text.update()
    assert text.index("@0,0") == before
    text.see("end"); text.update()
    with reading_position(text):
        text.insert("end", "\nLatest answer")
    text.update()
    assert near_bottom(text)


def test_upward_glide_means_the_reader_has_left_the_bottom(text):
    text.see("end"); text.update()
    scroller = attach(text)
    set_motion(True)
    scroller.wheel(SimpleNamespace(delta=2400))
    assert not near_bottom(text)


def test_one_notch_up_in_a_long_chat_stops_following_new_messages(text):
    text.see("end"); text.update()
    scroller = attach(text)
    set_motion(True)
    scroller.wheel(SimpleNamespace(delta=120))
    assert not near_bottom(text)  # before the first animation frame
    pump(text, 0.8)
    assert text.yview()[1] > 0.98  # the old percentage rule would follow here
    assert not near_bottom(text)
    before = text.index("@0,0")
    with reading_position(text):
        text.insert("end", "\nNew background report\n" * 100)
    text.update()
    assert text.index("@0,0") == before


def test_empty_wheel_event_does_not_scroll(text):
    assert SmoothScroller(text).wheel(SimpleNamespace(delta=0, num=0)) == "break"
    assert text.yview()[0] == 0


@pytest.mark.parametrize('move', ['scrollbar', 'new_reply', 'cancel'])
def test_next_wheel_gesture_works_after_the_view_changes(text, move):
    set_motion(True)
    scroller = attach(text)
    scroller.wheel(SimpleNamespace(delta=-120))
    pump(text, .5)
    assert scroller.target is None
    if move == 'scrollbar':
        text.yview_moveto(.6)
    elif move == 'new_reply':
        text.insert('end', '\nA new answer\n' * 30)
        text.see('end')
    else:
        scroller.cancel()
        text.yview_moveto(.6)
    text.update()
    before = text.yview()[0]
    # This used to cancel every subsequent gesture against the previous
    # glide's stale position, leaving the wheel permanently stuck.
    scroller.wheel(SimpleNamespace(delta=120))
    pump(text, .6)
    assert text.yview()[0] < before - .001
    assert scroller.target is None and scroller.job is None
