"""The brain's fourth dimension: history rebuilt only from recorded events."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.brain_history import Mark, load, reconstruct

T0 = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


def row(seconds, kind, source="gui", payload=None, turn=None):
    return ((T0 + timedelta(seconds=seconds)).isoformat(), kind, source, json.dumps(payload or {}), turn)


def at(seconds):
    return (T0 + timedelta(seconds=seconds)).timestamp()


def test_turns_tools_research_and_voice_become_spans():
    rows = [
        row(0, "TURN_RECEIVED", "voice", {"source": "voice"}, "t1"),
        row(2, "TURN_ROUTED", "voice", {"intent": "ACTION"}, "t1"),
        row(3, "TOOL_STARTED", "tools", {"tool": "list_folder"}, "t1"),
        row(4, "TOOL_COMPLETED", "tools", {"tool": "list_folder"}, "t1"),
        row(6, "TURN_COMPLETED", "voice", {}, "t1"),
        row(10, "TURN_RECEIVED", "gui", {"source": "gui"}, "t2"),
        row(10.5, "TURN_ROUTED", "gui", {"intent": "RESEARCH"}, "t2"),
        row(11, "TOOL_STARTED", "tools", {"tool": "research_topic"}, "t2"),
        row(20, "TOOL_FAILED", "tools", {"tool": "research_topic", "error": "timed out"}, "t2"),
        row(21, "TURN_COMPLETED", "gui", {}, "t2"),
        row(30, "TOOL_STARTED", "tools", {"tool": "research_topic"}),        # no turn: the learner
        row(40, "TOOL_COMPLETED", "tools", {"tool": "research_topic"}),
        row(50, "AUTHORITY_DENIED", "authority", {"tool": "install_system_software"}),
    ]
    history = reconstruct(rows, at(0), at(60))
    flows = {(span.flow, span.label) for span in history.spans}
    assert ("hearing", "hearing you") in flows
    assert ("tool", "list folder") in flows
    assert ("research", "research topic") in flows
    assert ("learning", "research topic") in flows
    assert history.flows_at(at(3.5)) == ["model", "tool"]
    assert history.flows_at(at(15)) == ["research"]
    assert history.flows_at(at(45)) == []
    kinds = {(mark.node, mark.kind) for mark in history.marks}
    assert kinds == {("tools", "failure"), ("authority", "denied")}
    assert [mark.label for mark in history.marks_near(at(20), 1)] == ["research topic: timed out"]


def test_an_end_is_never_invented_only_found():
    """No cap: a missing end is taken from later evidence, or shown as open."""
    rows = [
        # A tool in a turn, its own end never logged: over when the turn was.
        row(0, "TURN_RECEIVED", "gui", {"source": "gui"}, "t1"),
        row(1, "TURN_ROUTED", "gui", {"intent": "ACTION"}, "t1"),
        row(2, "TOOL_STARTED", "tools", {"tool": "image_preview"}, "t1"),
        row(30, "TURN_COMPLETED", "gui", {}, "t1"),
        # A later run of the same tool in another turn is not its end.
        row(3600, "TURN_RECEIVED", "gui", {"source": "gui"}, "t2"),
        row(3601, "TURN_ROUTED", "gui", {"intent": "ACTION"}, "t2"),
        row(3602, "TOOL_STARTED", "tools", {"tool": "image_preview"}, "t2"),
        row(3603, "TOOL_COMPLETED", "tools", {"tool": "image_preview"}, "t2"),
        row(3604, "TURN_COMPLETED", "gui", {}, "t2"),
        # A failure before the call began is a marker, not an end.
        row(4000, "TOOL_FAILED", "tools", {"tool": "browser_navigate", "error": "denied", "started": False}),
        # Nothing ever ends this one: open, to the end of the window.
        row(5000, "TOOL_STARTED", "tools", {"tool": "slow_tool"}, "t3"),
    ]
    history = reconstruct(rows, at(0), at(7200))
    tools = [span for span in history.spans if span.flow == "tool"]
    first = next(span for span in tools if span.start == at(2))
    assert first.end == at(30) and first.open and "end not logged" in first.label
    second = next(span for span in tools if span.start == at(3602))
    assert second.end == at(3603) and not second.open
    last = next(span for span in tools if span.start == at(5000))
    assert last.end == at(7200) and last.open and "no end recorded" in last.label
    assert [mark.kind for mark in history.marks] == ["failure"]
    # A recorded end stays solid.
    assert not any(span.open for span in history.spans if span.flow == "model")


def test_an_unfinished_turn_ends_when_the_next_one_begins():
    rows = [row(0, "TURN_RECEIVED", "gui", {}, "t1"), row(1, "TURN_ROUTED", "gui", {"intent": "QUESTION"}, "t1"),
            row(900, "TURN_RECEIVED", "gui", {}, "t2")]
    history = reconstruct(rows, at(0), at(1000))
    first = next(span for span in history.spans if span.start == at(1))
    assert first.end == at(900) and first.open


def test_spans_are_clipped_to_the_window_and_bad_rows_are_skipped():
    rows = [row(0, "TOOL_STARTED", "tools", {"tool": "x"}), row(100, "TOOL_COMPLETED", "tools", {"tool": "x"}),
            ("not a time", "TOOL_STARTED", "tools", "{}", None),
            ((T0 + timedelta(seconds=200)).isoformat(), "TOOL_STARTED", "tools", "{broken json", None)]
    history = reconstruct(rows, at(50), at(80))
    assert [(span.start, span.end) for span in history.spans] == [(at(50), at(80))]


def test_load_reads_the_event_log_read_only(tmp_path):
    path = tmp_path / "events.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, timestamp TEXT, event_type TEXT, source TEXT, "
                   "payload_json TEXT, turn_id TEXT)")
        for index, (stamp, kind, source, payload, turn) in enumerate([
                row(0, "TOOL_STARTED", "tools", {"tool": "read_file"}, "t"),
                row(2, "TOOL_COMPLETED", "tools", {"tool": "read_file"}, "t"),
                row(3, "PRIVACY_MODE_CHANGED", "authority", {})]):
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (index, stamp, kind, source, payload, turn))
    history = load(path, at(-10), at(10))
    assert [(span.flow, span.label) for span in history.spans] == [("tool", "read file")]
    assert load(tmp_path / "missing.db", at(0), at(1)).spans == ()


tk = pytest.importorskip("tkinter")


@pytest.fixture(scope="module")
def root(shared_tk):
    from tests.conftest import close_window, open_window

    window = open_window(shared_tk, "700x420-4000-4000")
    yield window
    close_window(window)


def test_the_timeline_scrubs_to_a_moment_and_snaps_back_to_live(root):
    from app.gui_brain import BrainTimeline
    from app.gui_theme import Palette

    moments = []
    timeline = BrainTimeline(root, Palette, moments.append)
    timeline.pack(fill="x")
    root.update()
    try:
        history = reconstruct([row(0, "TOOL_STARTED", "tools", {"tool": "x"}, "t"),
                               row(30, "TOOL_COMPLETED", "tools", {"tool": "x"}, "t")], at(0), at(60))
        timeline.set_history(history)
        assert timeline.find_withtag("span")
        middle = timeline.LEFT + timeline._usable() / 2
        timeline._scrub(SimpleNamespace(x=middle))
        assert moments[-1] == pytest.approx(at(30), abs=1)
        timeline._scrub(SimpleNamespace(x=timeline.winfo_width() - 2))
        assert moments[-1] is None                                  # the right edge is "now"
        timeline._step(-1)
        assert moments[-1] == pytest.approx(at(59), abs=.1)
        timeline._scrub(SimpleNamespace(x=0))
        assert moments[-1] == at(0)                                 # clamped to the window
    finally:
        timeline.destroy()


def test_the_brain_shows_a_past_moment_then_returns_to_live(root):
    from app.gui_brain import BrainGraph
    from app.gui_theme import Palette

    graph = BrainGraph(root, Palette, lambda _key: None)
    graph.pack(fill="both", expand=True)
    graph.set_motion(False)
    root.update()
    try:
        graph.set_activity({"running": [{"kind": "model"}]}, {})
        assert graph.flows == ["model"]
        graph.show_moment(at(0), ["tool", "not-a-flow"], [Mark(at(0), "tools", "failure", "x"),
                                                          Mark(at(0), "tools", "approval", "y")])
        assert graph.flows == ["tool"] and graph._moment_marks == {"tools": "failure"}
        texts = [graph.itemcget(item, "text") for item in graph.find_withtag("scene") if graph.type(item) == "text"]
        assert any(text.startswith("At ") and "using a tool" in text for text in texts)
        # Live activity while looking at the past is kept, not shown.
        graph.set_activity({"running": [{"kind": "research"}]}, {})
        assert graph.flows == ["tool"]
        graph.show_moment(None)
        assert graph.flows == ["research"] and graph._moment_marks == {}
    finally:
        graph.destroy()


def test_the_registry_says_whether_a_failed_call_had_started():
    """The log held 149 browser_navigate ends for 12 starts: failures before a
    call began were published exactly like the end of a call."""
    from app.tools.registry import ToolRegistry

    published = []
    registry = ToolRegistry()
    registry.events = SimpleNamespace(publish=lambda kind, source, payload, **_ids: published.append((kind, payload)))

    def boom():
        raise RuntimeError("mid-run failure")

    registry.register("explode", "fails while running", {"type": "object", "properties": {}, "additionalProperties": False}, "low", "read", boom)
    registry.execute("explode", {})
    registry.execute("explode", {"unexpected": 1})     # rejected by its schema, before starting
    registry.execute("not_a_tool", {})
    kinds = [(kind, payload.get("started")) for kind, payload in published]
    assert kinds == [("TOOL_STARTED", None), ("TOOL_FAILED", True),
                     ("TOOL_FAILED", False), ("TOOL_FAILED", False)]


def _lane_y(widget, flow):
    """The middle of a lane, found by name: adding a lane must not break this."""
    index = [key for key, _title in widget.LANES].index(flow)
    return widget.TOP + index * (widget.LANE + widget.GAP) + 5


@pytest.fixture
def timeline(root):
    from app.gui_brain import BrainTimeline
    from app.gui_theme import Palette

    moments, states = [], []
    widget = BrainTimeline(root, Palette, moments.append, on_replay=states.append)
    widget.pack(fill="x")
    root.update()
    history = reconstruct([
        row(100, "TOOL_STARTED", "tools", {"tool": "read_file"}, "t"),
        row(130, "TOOL_COMPLETED", "tools", {"tool": "read_file"}, "t"),
        row(1000, "TOOL_STARTED", "tools", {"tool": "slow_tool"}, "u"),
        row(2000, "TOOL_FAILED", "tools", {"tool": "browser_navigate", "error": "timed out", "started": False}),
        row(3000, "AUTHORITY_DENIED", "authority", {"tool": "install_system_software"}),
    ], at(0), at(3600))
    widget.set_history(history)
    yield widget, moments, states
    widget.destroy()


def test_the_wheel_zooms_around_the_pointer_and_never_below_twenty_seconds(timeline):
    widget, _moments, _states = timeline
    x = widget.LEFT + widget._usable() * .25             # a quarter of the way along
    anchor = widget._time(x)
    widget._wheel(SimpleNamespace(delta=120, x=x))
    assert widget.zoomed
    assert widget._time(x) == pytest.approx(anchor, abs=1)   # the point under the pointer stays put
    for _ in range(40):
        widget._wheel(SimpleNamespace(delta=120, x=x))
    begin, finish = widget._view()
    assert finish - begin == pytest.approx(widget.MIN_VIEW)
    for _ in range(40):
        widget._wheel(SimpleNamespace(delta=-120, x=x))
    assert not widget.zoomed                                  # zooming out far enough is the whole window


def test_a_zoomed_view_pans_and_fits_back(timeline):
    widget, _moments, _states = timeline
    widget.zoom_to(at(1000), at(1200))
    begin, _ = widget._view()
    widget._pan_start(SimpleNamespace(x=300))
    widget._pan(SimpleNamespace(x=200))                       # drag left: later times come into view
    widget._pan_end()
    assert widget._view()[0] > begin
    widget._pan_by(-1)
    widget.fit()
    assert widget._view() == (at(0), at(3600))
    widget._pan_by(1)                                         # an unzoomed view does not pan
    assert not widget.zoomed


def test_hovering_names_a_span_or_a_marker(timeline):
    widget, _moments, _states = timeline
    widget.zoom_to(at(90), at(140))
    y = _lane_y(widget, "tool")       # the Tools lane
    span = widget.item_at(widget._x(at(115)), y)
    assert span.label == "read file"
    assert widget.tip_text(span).endswith("30.0 s")
    widget.fit()
    open_span = widget.item_at(widget._x(at(1500)), y)
    assert "end not recorded" in widget.tip_text(open_span)
    mark = widget.item_at(widget._x(at(2000)), widget._mark_row() + 4)
    assert "timed out" in widget.tip_text(mark)
    assert widget.item_at(widget._x(at(500)), y) is None      # empty lane, nothing to name
    widget._hover(SimpleNamespace(x=widget._x(at(2000)), y=widget._mark_row() + 4))
    assert widget.find_withtag("tip")
    widget._hide_tip()
    assert not widget.find_withtag("tip")


def test_double_click_zooms_to_a_span_and_markers_can_be_stepped_through(timeline):
    widget, moments, _states = timeline
    y = _lane_y(widget, "tool")
    widget.zoom_to(at(50), at(200))
    widget._double(SimpleNamespace(x=widget._x(at(115)), y=y))
    begin, finish = widget._view()
    assert begin <= at(100) and finish >= at(130) and finish - begin < 60
    assert moments[-1] == at(100)
    widget.fit()
    widget.jump_mark(1)
    assert moments[-1] == at(2000)
    widget.jump_mark(1)
    assert moments[-1] == at(3000)
    widget.jump_mark(-1)
    assert moments[-1] == at(2000)
    count = len(moments)
    widget.seek(at(100))
    widget.jump_mark(-1)                                       # nothing earlier: stays
    assert len(moments) == count + 1


def test_replay_pauses_resumes_and_can_change_speed(timeline):
    widget, moments, states = timeline
    widget.toggle_replay()
    assert widget.replaying and states[-1] is True
    first = moments[-1]
    widget.toggle_replay()                                     # pause
    assert not widget.replaying and states[-1] is False and widget._replay_at is not None
    paused_at = widget._replay_at
    widget.toggle_replay()                                     # resume from there, not the start
    assert widget.replaying and moments[-1] >= first and moments[-1] == pytest.approx(paused_at)
    assert widget.cycle_speed() == "Fast" and widget.replaying
    assert widget.cycle_speed() == "Slow"
    widget.stop_replay()
    assert not widget.replaying and widget._replay_at is None


def test_seeking_outside_a_zoomed_view_slides_the_view_and_reloads_keep_it(timeline):
    widget, _moments, _states = timeline
    widget.zoom_to(at(100), at(200))
    widget.seek(at(3000))
    begin, finish = widget._view()
    assert begin <= at(3000) <= finish
    history = widget.history
    widget.set_history(history)
    assert widget._view() == (begin, finish)


def test_the_brain_keeps_a_faint_trail_of_what_was_just_used(root):
    from app.gui_brain import BrainGraph
    from app.gui_theme import Palette

    graph = BrainGraph(root, Palette, lambda _key: None)
    graph.pack(fill="both", expand=True)
    graph.set_motion(False)
    root.update()
    try:
        graph.show_moment(at(10), ["tool"], [], echoes=["model", "tool"])
        assert graph._echo_edges and not set(graph._echo_edges) & set(graph._edges)
        assert graph.find_withtag("echo")
        graph.show_moment(None)
        assert graph._echo_edges == [] and not graph.find_withtag("echo")
    finally:
        graph.destroy()


def test_the_keys_reach_the_timeline(timeline, root):
    widget, moments, _states = timeline
    widget.focus_force()
    root.update()
    widget.event_generate("<bracketright>", when="now")     # from "now" there is nothing later
    assert moments == []
    widget.event_generate("<bracketleft>", when="now")
    assert moments[-1] == at(3000)
    widget.event_generate("<bracketleft>", when="now")
    assert moments[-1] == at(2000)
    widget.event_generate("<plus>", when="now")
    assert widget.zoomed
    widget.event_generate("<Key-0>", when="now")
    assert not widget.zoomed
    widget.event_generate("<Left>", when="now")
    assert moments[-1] < at(2000)
    widget.event_generate("<End>", when="now")
    assert moments[-1] is None
    widget.event_generate("<space>", when="now")
    assert widget.replaying
    widget.event_generate("<space>", when="now")
    assert not widget.replaying


def test_the_heading_and_key_hint_never_overlap(timeline):
    widget, _moments, _states = timeline
    for zoomed in (False, True):
        if zoomed:
            widget.zoom_to(at(100), at(130))
            widget.seek(at(110))
        widget.redraw()
        title = widget.bbox(widget.find_withtag("heading")[0])
        hints = widget.find_withtag("hint")
        if hints:
            assert widget.bbox(hints[0])[0] >= title[2]


# ------------------------------------------------------------------ seeing

def test_camera_time_looks_and_screen_reads_are_seeing():
    rows = [
        row(0, "CAMERA_CHANGED", "camera", {"state": "ACTIVE", "device_index": 0}),
        row(5, "TOOL_STARTED", "tools", {"tool": "camera_observe"}, "t"),
        row(8, "TOOL_COMPLETED", "tools", {"tool": "camera_observe"}, "t"),
        row(8, "CAMERA_OBSERVATION", "camera", {"source": "look", "visibility": "clear"}),
        row(30, "CAMERA_CHANGED", "camera", {"state": "OFF"}),
        row(31, "CAMERA_CHANGED", "camera", {"state": "OFF"}),                 # repeats are harmless
        row(40, "TOOL_STARTED", "tools", {"tool": "describe_screen"}, "u"),
        row(42, "TOOL_COMPLETED", "tools", {"tool": "describe_screen"}, "u"),
        row(50, "CAMERA_CHANGED", "camera", {"state": "ACTIVE"}),
        row(55, "CAMERA_OBSERVATION", "camera", {"source": "watch"}),
        row(58, "CAMERA_OBSERVATION", "camera", {"source": "look", "visibility": "dark"}),
    ]
    history = reconstruct(rows, at(0), at(60))
    seeing = [(span.label, round(span.end - span.start), span.open) for span in history.spans if span.flow == "seeing"]
    assert ("camera on", 30, False) in seeing
    assert ("camera observe", 3, False) in seeing and ("describe screen", 2, False) in seeing
    assert ("camera on (still on)", 10, True) in seeing                   # on until now, as far as known
    looks = [mark.label for mark in history.marks if mark.kind == "look"]
    assert looks == ["looked through the camera", "watch mode noticed movement",
                     "looked through the camera (view dark)"]
    assert all(mark.node == "perception" for mark in history.marks if mark.kind == "look")


def test_node_seconds_add_up_each_parts_busy_time():
    from app.gui_brain import FLOWS

    history = reconstruct([
        row(0, "TOOL_STARTED", "tools", {"tool": "read_file"}, "t"),
        row(20, "TOOL_COMPLETED", "tools", {"tool": "read_file"}, "t"),
        row(30, "CAMERA_OBSERVATION", "camera", {"source": "look"}),
    ], at(0), at(60))
    seconds = history.node_seconds(at(0), at(60), FLOWS)
    assert seconds["tools"] == pytest.approx(20) and seconds["authority"] == pytest.approx(20)
    assert seconds["perception"] == pytest.approx(1)                        # one look
    assert history.node_seconds(at(40), at(60), FLOWS) == {}
    # A call whose end was never recorded adds nothing: that time is a guess.
    unended = reconstruct([row(0, "TOOL_STARTED", "tools", {"tool": "image_preview"}, "t")], at(0), at(3600))
    assert unended.spans[0].open and unended.node_seconds(at(0), at(3600), FLOWS) == {}


def test_a_look_event_never_carries_what_was_seen(tmp_path):
    """The event log is durable; nothing the camera sees is kept."""
    from app.perception.camera.manager import CameraManager
    from tests.test_camera_seeing import Camera

    published = []
    manager = CameraManager({"retain_frames": False}, vision=SimpleNamespace(
        analyze=lambda path, prompt: {"interpretation": "A person in a red shirt at a desk."}))
    manager.temp_root = tmp_path
    manager.ocr = SimpleNamespace(status=lambda: {}, read=lambda path: {"available": True, "text": "Private note"})
    manager.capture_backend = Camera()
    manager.event_bus = SimpleNamespace(publish=lambda kind, source, payload, **_ids: published.append((kind, payload)))
    manager.observe(shots=2, interval_ms=0)
    looks = [payload for kind, payload in published if kind == "CAMERA_OBSERVATION"]
    assert looks == [{"source": "look", "visibility": "clear"}]
    assert "red shirt" not in repr(published) and "Private note" not in repr(published)


# ------------------------------------------------------------------ heat and focus

def test_the_brain_glows_with_its_own_history(root):
    from app.gui_brain import BrainGraph
    from app.gui_theme import Palette

    graph = BrainGraph(root, Palette, lambda _key: None)
    graph.pack(fill="both", expand=True)
    graph.set_motion(False)
    root.update()
    try:
        graph.set_heat({"tools": 120.0, "memory": 30.0, "not-a-part": 999.0, "dna": 0.0})
        assert graph.heat == {"tools": 1.0, "memory": .25}
        glowing = {tag for item in graph.find_withtag("heat") for tag in graph.gettags(item) if tag.startswith("node-")}
        assert glowing == {"node-tools", "node-memory"}
        graph.set_heat(None)
        assert graph.heat == {} and not graph.find_withtag("heat")
        graph.set_activity({}, {}, camera_on=True)
        assert "seeing" in graph.flows and ("perception", "verifier") in graph._edges
    finally:
        graph.destroy()


def test_focusing_a_part_steps_everything_else_back(timeline):
    widget, _moments, _states = timeline
    widget.set_focus("tools", "Tool Bus", {"tool"})
    assert widget.find_withtag("focused")
    focused = {widget.itemcget(item, "fill") for item in widget.find_withtag("focused")}
    others = [item for item in widget.find_withtag("span") if item not in widget.find_withtag("focused")]
    assert all(widget.itemcget(item, "fill") not in focused for item in others)
    heading = widget.itemcget(widget.find_withtag("heading")[0], "text")
    assert "Tool Bus only" in heading
    widget.set_focus(None)
    assert not widget.find_withtag("focused")
