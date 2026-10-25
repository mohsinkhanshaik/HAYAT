"""Capability routing, responsive geometry and honest samples in the neural workspace."""
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")
from app.gui_hologram import NeuralWorkspace, ResourceCharts
from app.gui_theme import Palette, apply_theme


@pytest.fixture(scope="module")
def root(shared_tk):
    from tests.conftest import close_window, open_window

    window = open_window(shared_tk, "890x400-4000-4000")
    yield window
    close_window(window)


@pytest.fixture
def scene(root):
    actions, views = [], []
    root.geometry("890x400-4000-4000")
    widget = NeuralWorkspace(root, Palette, views.append, actions.append)
    widget.pack(fill="both", expand=True)
    widget.set_motion(False)
    root.update()
    yield widget, actions, views
    widget.destroy()


@pytest.mark.parametrize("size", ["890x400", "930x185"])
def test_cards_fit_do_not_overlap_and_open_their_action(scene, root, size):
    widget, actions, views = scene
    root.geometry(size + "-4000-4000")
    root.update()
    boxes = list(widget._card_boxes.items())
    for key, (left, top, right, bottom) in boxes:
        assert 0 <= left < right <= widget.winfo_width()
        assert 28 <= top < bottom <= widget.winfo_height()
        for other, (x, y, xx, yy) in boxes:
            if other != key:
                assert right <= x or left >= xx or bottom <= y or top >= yy
        event = SimpleNamespace(x=(left+right)/2, y=(top+bottom)/2)
        widget._press(event)
        widget._release(event)
    assert actions == [row[0] for row in widget.CAPABILITIES]
    assert views == []


def test_drag_across_a_card_rotates_without_starting_a_capability(scene):
    widget, actions, views = scene
    left, top, _, _ = widget._card_boxes["listening"]
    angle = widget.angle
    widget._press(SimpleNamespace(x=left+20, y=top+20))
    widget._drag(SimpleNamespace(x=left+60, y=top+20))
    widget._release(SimpleNamespace(x=left+60, y=top+20))
    assert widget.angle != angle
    assert actions == views == []


def test_all_capabilities_can_be_opened_with_keyboard_and_chords_pass_through(scene, root):
    widget, actions, views = scene
    widget.focus_force()
    root.update()
    widget.event_generate("<Right>")
    widget.event_generate("<Return>")
    assert actions == ["vision"]
    assert widget._digit(SimpleNamespace(char="3", state=0x0004)) is None
    assert widget._digit(SimpleNamespace(char="", state=0)) is None
    for i in range(1, 7):
        widget._digit(SimpleNamespace(char=str(i), state=0))
    assert actions[1:] == [row[0] for row in widget.CAPABILITIES]
    assert views == []


def test_animation_stops_when_workspace_is_hidden_and_scene_items_stay_bounded(scene, root):
    widget, _, _ = scene
    widget.set_motion(True)
    initial = len(widget.find_all())
    for _ in range(15):
        widget.angle += .1
        widget.redraw()
    assert len(widget.find_all()) == initial
    assert widget._frame is not None
    widget.pack_forget()
    root.update()
    assert widget._frame is None
    widget.pack(fill="both", expand=True)
    root.update()
    assert widget._frame is not None


def test_runtime_status_respects_disabled_voice_and_highlights_actual_tool_work(scene):
    widget, _, _ = scene
    widget.set_status({"running": [{"kind": "tool"}]}, {"state": "idle", "enabled": False}, True)
    assert widget._status["listening"] == "Voice off"
    assert widget._status["vision"] == "Camera on"
    assert widget._busy_capability == "execute"
    widget.set_status({"running": []}, {"state": "listening", "enabled": True}, False)
    assert widget._busy_capability is None
    assert widget._status["listening"] == "Listening"


def test_charts_record_new_samples_once_preserve_missing_values_and_bound_history(root):
    charts = ResourceCharts(root, Palette)
    try:
        sample = {"cpu": 12., "ram": 38., "gpu": None, "disk": float("nan")}
        charts.set_metrics(sample)
        charts.set_metrics(sample)
        assert list(charts.samples["cpu"]) == [12.]
        assert list(charts.samples["gpu"]) == [None]
        assert list(charts.samples["disk"]) == [None]
        charts.set_metrics(dict(sample))
        assert list(charts.samples["cpu"]) == [12., 12.]
        for i in range(80):
            charts.set_metrics({"cpu": i})
        assert len(charts.samples["cpu"]) == 60
        assert charts.samples["cpu"][-1] == 79.
    finally:
        charts.destroy()


def test_new_surfaces_follow_light_and_high_contrast_themes(root):
    class LocalPalette(Palette):
        pass
    widget = NeuralWorkspace(root, LocalPalette, lambda key: None, lambda key: None)
    charts = ResourceCharts(root, LocalPalette)
    try:
        for theme in ("Light", "High contrast"):
            apply_theme(root, LocalPalette, theme)
            assert widget.cget("bg") == LocalPalette.BLACK
            assert charts.canvases["cpu"].cget("bg") == LocalPalette.PANEL_2
    finally:
        widget.destroy()
        charts.destroy()


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now


@pytest.mark.parametrize("voice, form", [
    ({"state": "listening", "enabled": False}, "off"),
    ({"state": "idle", "enabled": True}, "idle"),
    ({"state": "listening", "enabled": True}, "listening"),
    ({"state": "approval_required", "enabled": True}, "approval_required"),
    ({"state": "something_new", "enabled": True}, "idle"),
    ({"state": "error", "enabled": False}, "error"),
    ({}, "off"),
])
def test_voice_status_maps_to_one_honest_form(voice, form):
    assert NeuralWorkspace.voice_form(voice) == form


def test_voice_shell_takes_its_size_from_real_loudness(scene):
    widget, _actions, _views = scene
    heard = {"input": 0.0, "output": 0.0}
    widget.voice_levels = lambda: dict(heard)
    widget.set_status({}, {"state": "listening", "enabled": True}, False)
    assert widget._status["listening"] == "Listening"
    quiet = widget._sample_voice()
    heard["input"] = .9
    for _ in range(8):
        loud = widget._sample_voice()
    assert quiet == 0.0 and loud > .8
    # Reduced motion still shows loudness, through the status poll.
    widget.set_status({}, {"state": "listening", "enabled": True}, False)
    ring = [item for item in widget.find_withtag("voice") if widget.type(item) == "oval"][-1]
    assert float(widget.itemcget(ring, "width")) > 2.5
    heard["input"] = 0.0
    for _ in range(40):
        widget._sample_voice()
    assert widget._voice_level == 0.0


def test_audio_that_is_playing_shows_as_speaking(scene):
    widget, _actions, _views = scene
    widget.voice_levels = lambda: {"input": 0.0, "output": .6}
    widget.set_status({}, {"state": "idle", "enabled": True}, False)
    widget._sample_voice()
    assert widget._voice_state == "speaking"
    widget.voice_levels = lambda: (_ for _ in ()).throw(RuntimeError("gone"))
    widget._sample_voice()  # a failing source reads as silence, never an exception


def test_voice_shell_is_the_voice_control(scene):
    widget, actions, _views = scene
    x, y, size = widget._voice_box
    assert size > 0
    widget._positions = {}
    widget._press(SimpleNamespace(x=x, y=y))
    widget._release(SimpleNamespace(x=x, y=y))
    assert actions == ["listening"]


def test_only_the_voice_layer_animates_while_the_camera_is_still(scene):
    widget, _actions, _views = scene
    widget._clock = Clock()
    widget.voice_levels = lambda: {"input": .5, "output": 0.0}
    widget.set_status({}, {"state": "listening", "enabled": True}, False)
    globe = [item for item in widget.find_withtag("scene") if "voice" not in widget.gettags(item)]
    voice = set(widget.find_withtag("voice"))
    assert widget._overlay(redrawn=False) is True
    assert set(widget.find_withtag("voice")).isdisjoint(voice)   # the shell was redrawn
    assert all(widget.type(item) for item in globe)              # the globe was not
    widget.set_status({}, {"state": "off", "enabled": False}, False)
    widget.voice_levels = lambda: {"input": 0.0, "output": 0.0}
    for _ in range(40):
        widget._sample_voice()
    assert widget._overlay(redrawn=False) is False               # a silent, off voice idles


@pytest.mark.parametrize("size", ["890x430", "1200x430", "890x600"])
def test_the_globe_stays_inside_the_canvas_and_its_caption_is_backed(scene, root, size):
    widget, _actions, _views = scene
    root.geometry(size + "-4000-4000")
    root.update()
    widget.redraw()
    cx, cy, radius, _compact = widget._geometry(widget.winfo_width(), widget.winfo_height())
    tops = [widget._point(point, widget.winfo_width(), widget.winfo_height())[1] for point in widget._particles]
    assert min(tops) >= 0, "the top of the globe was cut off by the canvas edge"
    caption = next(item for item in widget.find_withtag("voice") if widget.type(item) == "text")
    backing = [item for item in widget.find_withtag("voice") if widget.type(item) == "polygon"]
    assert backing, "the voice caption needs a backing so the mesh behind it does not hide it"
    # The caption is drawn above its backing.
    assert widget.find_withtag("voice").index(caption) > widget.find_withtag("voice").index(backing[-1])
