"""Interaction and lifecycle checks for the local 3D workspace."""
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")
from app.gui_depth import DepthNavigator, DepthSurface
from app.gui_panels import ScrollableColumn
from app.gui_theme import Palette, apply_theme


@pytest.fixture(scope="module")
def root(shared_tk):
    from tests.conftest import close_window, open_window

    window = open_window(shared_tk, "440x360-4000-4000")
    yield window
    close_window(window)


@pytest.fixture
def scene(root):
    opened = []
    widget = DepthNavigator(root, Palette, opened.append)
    widget.pack(fill="both", expand=True)
    widget.set_motion(False)
    root.update()
    yield widget, opened
    widget.destroy()


def test_drag_does_not_open_a_view_and_reset_restores_camera(scene):
    widget, opened = scene
    widget._press(SimpleNamespace(x=40, y=40))
    widget._drag(SimpleNamespace(x=240, y=940))
    widget._release(SimpleNamespace(x=240, y=940))
    assert opened == []
    assert widget.pitch <= 1.1
    for _ in range(25):
        widget._zoom(1)
    assert widget.zoom <= 1.25
    widget.reset()
    assert (widget.angle, widget.pitch, widget.zoom) == (-.35, -.25, 1.0)


def test_overlapping_nodes_choose_frontmost_and_empty_space_is_not_a_shortcut(scene):
    widget, opened = scene
    widget._positions = {"chat": (100, 100, -.8), "memory": (100, 100, .8)}
    widget._press(SimpleNamespace(x=100, y=100))
    widget._release(SimpleNamespace(x=100, y=100))
    assert opened == ["memory"]
    assert widget._hit(130, 100) is None


def test_rail_scrolling_does_not_replace_the_scenes_zoom_binding(root):
    rail = ScrollableColumn(root, Palette, width=400)
    widget = DepthNavigator(rail.body, Palette, lambda _key: None)
    widget.set_motion(False)
    try:
        original = widget.bind("<MouseWheel>")
        rail.adopt(rail.body)
        assert widget.bind("<MouseWheel>") == original
    finally:
        rail.destroy()


def test_keyboard_navigation_and_motion_cleanup(scene, root):
    widget, opened = scene
    widget.focus_force()
    root.update()
    widget.event_generate("<Right>")
    widget.event_generate("<Return>")
    assert opened == ["operator"]
    widget.set_motion(True)
    assert widget._frame is not None
    widget.pack_forget()
    root.update()
    assert widget._frame is None
    widget.pack(fill="both", expand=True)
    root.update()
    assert widget._frame is not None
    widget.set_motion(False)
    assert widget._frame is None


def test_surface_content_survives_hide_restore_and_theme_change(root):
    class LocalPalette(Palette):
        pass
    surface = DepthSurface(root, LocalPalette)
    label = tk.Label(surface.body, text="A working control", bg=LocalPalette.PANEL)
    label.pack()
    surface.pack(fill="x")
    root.update()
    surface.pack_forget()
    root.update()
    surface.pack(fill="x")
    root.update()
    try:
        assert label.winfo_ismapped()
        apply_theme(root, LocalPalette, "Light")
        assert surface.body.cget("bg") == LocalPalette.PANEL
        assert label.cget("bg") == LocalPalette.PANEL
    finally:
        surface.destroy()


class Clock:
    """A hand-wound clock so frame timing is deterministic."""

    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def frames(widget, clock, count, step=.033):
    for _ in range(count):
        clock.now += step
        widget._animate()


def test_a_flick_coasts_and_settles_but_a_placed_drag_does_not(scene):
    widget, opened = scene
    clock = widget._clock = Clock()
    widget.set_motion(True)
    widget._press(SimpleNamespace(x=100, y=100))
    for x in (110, 125, 145):
        clock.now += .016
        widget._drag(SimpleNamespace(x=x, y=100))
    widget._release(SimpleNamespace(x=145, y=100))
    released = widget.angle
    assert widget._velocity[0] > 0
    frames(widget, clock, 3)
    assert widget.angle > released
    frames(widget, clock, 120)
    assert widget._velocity == (0.0, 0.0)
    assert opened == []

    # Holding still before letting go puts the globe down where it is.
    widget._press(SimpleNamespace(x=100, y=100))
    clock.now += .016
    widget._drag(SimpleNamespace(x=140, y=100))
    clock.now += .3
    widget._release(SimpleNamespace(x=140, y=100))
    assert widget._velocity == (0.0, 0.0)


def test_reduced_motion_never_coasts(scene):
    widget, _opened = scene
    clock = widget._clock = Clock()
    widget._press(SimpleNamespace(x=100, y=100))
    clock.now += .016
    widget._drag(SimpleNamespace(x=150, y=100))
    widget._release(SimpleNamespace(x=150, y=100))
    assert widget._velocity == (0.0, 0.0)
    assert widget._frame is None


def test_opening_a_far_node_turns_it_to_the_front(scene):
    widget, opened = scene
    far = min(widget._positions, key=lambda key: widget._positions[key][2])
    assert widget._positions[far][2] < 0
    clock = widget._clock = Clock()
    widget.set_motion(True)
    x, y, _z = widget._positions[far]
    widget._press(SimpleNamespace(x=x, y=y))
    widget._release(SimpleNamespace(x=x, y=y))
    assert opened == [far]
    assert widget._pulse is not None and widget._target is not None
    frames(widget, clock, 60)
    assert widget._target is None and widget._pulse is None
    assert widget._positions[far][2] > .5
    assert widget.active == far


def test_reduced_motion_faces_and_resets_instantly(scene):
    widget, opened = scene
    widget._digit(SimpleNamespace(char="5", state=0))
    assert opened == ["gallery"]
    assert widget._target is None and widget._pulse is None
    assert widget._positions["gallery"][2] > .5
    widget.reset()
    assert (widget.angle, widget.pitch, widget.zoom) == pytest.approx(DepthNavigator.HOME)


def test_eased_reset_takes_the_short_way_home(scene):
    widget, _opened = scene
    clock = widget._clock = Clock()
    widget.set_motion(True)
    widget.angle, widget.pitch, widget.zoom = -.35 + 4 * 3.14159 + .5, .8, 1.2
    widget.reset()
    frames(widget, clock, 80)
    assert widget._target is None
    assert (widget.angle, widget.pitch, widget.zoom) == pytest.approx(DepthNavigator.HOME, abs=.01)


def test_keys_tilt_zoom_and_open_without_stealing_chords(scene):
    widget, opened = scene
    for _ in range(20):
        widget._tilt(1)
    assert widget.pitch == DepthNavigator.PITCH_LIMIT
    zoom = widget.zoom
    widget._zoom(-1)
    assert widget.zoom < zoom
    assert widget._digit(SimpleNamespace(char="3", state=0x0004)) is None
    assert widget._digit(SimpleNamespace(char="9", state=0)) is None
    # NumLock (0x0008 on Windows) must not disable the number keys.
    assert widget._digit(SimpleNamespace(char="3", state=0x0008)) == "break"
    assert opened == ["memory"]


def test_far_nodes_are_hidden_behind_the_core(scene):
    widget, _opened = scene
    order = widget.find_all()
    core = min(order.index(item) for item in widget.find_withtag("core"))
    for key, (_x, _y, z) in widget._positions.items():
        first = min(order.index(item) for item in widget.find_withtag(f"node-{key}"))
        assert (first < core) == (z < 0), key


def test_cursor_and_label_follow_what_is_under_the_pointer(scene):
    widget, _opened = scene
    widget._inside = True
    x, y, _z = widget._positions["chat"]
    widget._move(SimpleNamespace(x=x, y=y))
    assert widget.cget("cursor") == "hand2"
    assert widget._hover == "chat"
    labels = [widget.itemcget(item, "text") for item in widget.find_withtag("scene")
              if widget.type(item) == "text"]
    assert "Chat" in labels
    widget._move(SimpleNamespace(x=2, y=2))
    assert widget.cget("cursor") == "fleur"
    assert widget._hover is None


def test_every_destination_can_be_brought_to_the_front(scene):
    widget, _opened = scene
    for key, _title in DepthNavigator.VIEWS:
        widget.face(key)
        x, _y, z = widget._positions[key]
        assert z > .5, key
        # Beside the core, not covering it.
        assert abs(x - widget.winfo_width() / 2) > 8, key
