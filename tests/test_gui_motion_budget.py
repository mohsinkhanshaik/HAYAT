"""Animation keeps to its share of the interface thread, and a drag draws the latest position, not every move."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.gui_depth import DepthNavigator


def test_a_cheap_scene_keeps_its_frame_rate_and_an_expensive_one_yields_the_thread():
    scene = SimpleNamespace(FRAME_MS=33, IDLE_MS=50, BUSY_SHARE=DepthNavigator.BUSY_SHARE,
                            IDLE_SHARE=DepthNavigator.IDLE_SHARE, MAX_FRAME_GAP_MS=DepthNavigator.MAX_FRAME_GAP_MS)
    delay = lambda cost, busy: DepthNavigator._next_frame_delay(scene, cost, busy)   # noqa: E731
    assert delay(0.004, True) == 33 and delay(0.004, False) == 50            # cheap: the normal rates
    # An 80 ms frame (the 3D brain with five million neurons): the rest is long enough that drawing takes at most
    # 30% of the thread while idling and 60% during a move the owner asked for.
    idle, busy = delay(0.080, False), delay(0.080, True)
    assert 80 / (80 + idle) <= DepthNavigator.IDLE_SHARE + 0.01 and idle == 186
    assert 80 / (80 + busy) <= DepthNavigator.BUSY_SHARE + 0.01 and busy == 53
    assert delay(5.0, False) == DepthNavigator.MAX_FRAME_GAP_MS              # never parked for seconds
    assert scene.frame_cost_ms == 5000.0                                     # the last cost is kept for the status line


class Scene:
    """Just the drag-drawing rule, with a scene that takes 30 ms to draw."""
    _drag_frame, _drag_draw = DepthNavigator._drag_frame, DepthNavigator._drag_draw

    def __init__(self):
        self.drawn, self.idle = 0, []

    def redraw(self):
        self.drawn += 1
        time.sleep(0.03)

    def after_idle(self, callback):
        self.idle.append(callback)
        return f"idle-{len(self.idle)}"

    def winfo_exists(self):
        return True


def test_a_burst_of_pointer_moves_is_drawn_once_now_and_once_at_the_end():
    scene = Scene()
    scene._drag_frame()
    assert scene.drawn == 1 and not scene.idle                               # the first move is drawn at once
    for _ in range(12):                                                      # twelve moves that queued up behind it
        scene._drag_frame()
    assert scene.drawn == 1 and len(scene.idle) == 1                         # none drawn; one frame asked for
    scene.idle.pop()()                                                       # the queue is empty: the latest position
    assert scene.drawn == 2 and scene._drag_pending is None
    time.sleep(0.05)                                                         # a slow, deliberate drag: every move drawn
    scene._drag_frame()
    assert scene.drawn == 3 and not scene.idle


def test_a_trailing_frame_for_a_closed_view_does_nothing():
    scene = Scene()
    scene.winfo_exists = lambda: False
    scene._drag_draw()
    assert scene.drawn == 0 and scene._drag_pending is None


def test_the_real_view_turns_with_the_drag_and_settles_on_the_last_position(shared_tk):
    from app.gui_brain import BrainGraph
    from app.gui_theme import Palette
    from tests.conftest import close_window, open_window

    root = open_window(shared_tk, '900x600-4000-4000')
    graph = BrainGraph(root, Palette, lambda *_: None)
    graph.pack(fill='both', expand=True)
    graph.set_motion(False)
    root.update()
    try:
        start = graph.angle
        graph._press(SimpleNamespace(x=300, y=300))
        for x in range(310, 420, 10):                                        # eleven quick moves, no event loop between
            graph._drag(SimpleNamespace(x=x, y=300))
        assert graph.angle == pytest.approx(start + 110 * .009)              # the camera followed every move
        root.update()                                                        # the trailing frame
        assert getattr(graph, '_drag_pending', None) is None
        graph._release(SimpleNamespace(x=410, y=300))
        root.update()
        assert graph.angle == pytest.approx(start + 110 * .009) and graph.find_withtag('scene')
    finally:
        close_window(root)
